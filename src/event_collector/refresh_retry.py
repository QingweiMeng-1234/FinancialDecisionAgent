"""Bounded refresh retry execution over immutable child-run targets.

The control plane claims and materializes targets in ``refresh_ledger``.  This
module renews the owner lease, appends one stage attempt, commits, and only then
invokes an injected external executor.  Consequently it never holds a ledger
transaction across HTTP, LLM, embedding, or generation-coordinator work.

There is intentionally no legacy vector-store path.  Index work either invokes
an explicit successor-generation coordinator or records
``generation_rebuild_required``.  Cumulative scope reconciliation and the
completed daily gate remain a separate proof boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import sqlite3
from typing import Callable

from event_collector.article_content import (
    CONTENT_VALIDATOR_VERSION,
    ArticleContentFetcher,
    ArticleFetchError,
    validate_title_content_alignment,
)
from event_collector.event_collection import CollectionSourceOutcome
from event_collector.news_storage import SQLiteNewsStore
from event_collector.refresh_ledger import (
    COLLECTION_FAILURE_CODES,
    CollectionRetryTarget,
    RetryClaim,
    RetryTarget,
    finish_processing_attempt,
    list_collection_retry_targets,
    list_retry_targets,
    read_retry_target_terminal_attempt,
    record_collection_attempt,
    renew_refresh_lease,
    start_processing_attempt,
)
from event_collector.refresh_lease_watchdog import (
    LeaseHeartbeatFailed,
    LeaseHeartbeatWatchdog,
)
from event_collector.summarization import ArticleForSummarization, SummarizationAgent


@dataclass(frozen=True)
class RetryExecutionResult:
    status: str
    failure_code: str | None = None
    retryable: bool = False
    next_retry_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "retry_scheduled"}:
            raise ValueError(f"Unsupported retry execution status: {self.status!r}")
        if self.status == "succeeded":
            if self.failure_code is not None or self.retryable or self.next_retry_at is not None:
                raise ValueError("succeeded retry results cannot include failure fields")
            return
        if not (self.failure_code or "").strip():
            raise ValueError(f"{self.status} retry results require a failure_code")
        if self.status == "retry_scheduled" and (
            not self.retryable or self.next_retry_at is None
        ):
            raise ValueError(
                "retry_scheduled retry results require retryable=True and next_retry_at"
            )


@dataclass(frozen=True)
class RetryTargetOutcome:
    target_id: str
    article_id: int | None
    stage: str
    status: str
    failure_code: str | None = None
    source_name: str | None = None


@dataclass(frozen=True)
class RetryBatchResult:
    run_id: str
    outcomes: tuple[RetryTargetOutcome, ...]
    lost_lease: bool = False


@dataclass(frozen=True)
class _CollectionAttemptExecution:
    collector_status: str
    source_row_count: int
    accepted_input_count: int
    rejected_source_row_count: int
    empty_reason: str | None
    failure_code: str | None
    retryable: bool
    next_retry_at: datetime | None


RetryExecutor = Callable[[RetryTarget], RetryExecutionResult]
CollectionRetryExecutor = Callable[[CollectionRetryTarget], CollectionSourceOutcome]


def build_default_content_retry_executor(
    *,
    storage: SQLiteNewsStore,
    fetcher: ArticleContentFetcher | object,
    clock: Callable[[], datetime],
    default_retry_delay_seconds: int = 60,
    lease_guard: Callable[[], None] | None = None,
) -> RetryExecutor:
    """Build the production publisher-fetch adapter for one exact article."""
    _validate_default_executor_inputs(storage, clock, default_retry_delay_seconds)
    if not callable(getattr(fetcher, "fetch", None)):
        raise TypeError("fetcher must expose fetch(url)")

    def execute(target: RetryTarget) -> RetryExecutionResult:
        if getattr(target, "stage", None) != "content_fetch":
            raise ValueError("content retry executor only accepts content_fetch targets")
        record = storage.get_article_record(getattr(target, "article_id", 0))
        if record is None or record.article.source != "news":
            return RetryExecutionResult(
                status="failed",
                failure_code="retry_article_missing_or_not_news",
            )
        article = record.article
        url = article.original_url or article.url
        if not url:
            return RetryExecutionResult(status="failed", failure_code="retry_article_url_missing")
        try:
            fetched = fetcher.fetch(url)
        except ArticleFetchError as exc:
            if not exc.retryable:
                return RetryExecutionResult(status="failed", failure_code=exc.reason.value)
            retry_at = exc.retry_after_at or (
                _aware_now(clock)
                + timedelta(
                    seconds=(
                        exc.retry_after_seconds
                        if exc.retry_after_seconds is not None
                        else default_retry_delay_seconds
                    )
                )
            )
            return RetryExecutionResult(
                status="retry_scheduled",
                failure_code=exc.reason.value,
                retryable=True,
                next_retry_at=retry_at,
            )
        validate_title_content_alignment(
            article.title,
            article.description,
            fetched.content,
            url=fetched.original_url,
        )
        conflicting_id = storage.find_article_id_by_url(fetched.canonical_url, None)
        if conflicting_id is not None and conflicting_id != record.id:
            return RetryExecutionResult(
                status="failed",
                failure_code="duplicate_url_conflict",
            )
        if lease_guard is not None:
            lease_guard()
        storage.update_article_content(
            record.id,
            content=fetched.content,
            title=article.title,
            description=article.description,
            original_url=fetched.original_url,
            canonical_url=fetched.canonical_url,
            published_at=article.published_at,
            validation_status="verified",
            validator_version=CONTENT_VALIDATOR_VERSION,
            response_status_code=fetched.status_code,
            response_content_type=fetched.content_type,
            extractor_version=fetched.extractor_version,
            final_response_url=fetched.original_url,
        )
        storage.conn.commit()
        return RetryExecutionResult(status="succeeded")

    return execute


def build_default_summary_retry_executor(
    *,
    storage: SQLiteNewsStore,
    summarizer: SummarizationAgent | object,
    clock: Callable[[], datetime],
    default_retry_delay_seconds: int = 60,
    lease_guard: Callable[[], None] | None = None,
) -> RetryExecutor:
    """Build the production summary adapter bound to the target content hash."""
    _validate_default_executor_inputs(storage, clock, default_retry_delay_seconds)
    if not callable(getattr(summarizer, "summarize_article", None)):
        raise TypeError("summarizer must expose summarize_article(article)")

    def execute(target: RetryTarget) -> RetryExecutionResult:
        if getattr(target, "stage", None) != "summary":
            raise ValueError("summary retry executor only accepts summary targets")
        record = storage.get_article_record(getattr(target, "article_id", 0))
        expected_hash = getattr(target, "input_content_sha256", None)
        if record is None or not expected_hash:
            return RetryExecutionResult(status="failed", failure_code="summary_input_missing")
        article = record.article
        active_hash = article.active_content_sha256 or article.content_sha256
        if record.content_status != "ready" or active_hash != expected_hash:
            return RetryExecutionResult(status="failed", failure_code="stale_content_version")
        try:
            summary = summarizer.summarize_article(
                ArticleForSummarization(
                    article_id=record.id,
                    title=article.title,
                    description=article.description,
                    content=article.content,
                    url=article.url,
                )
            )
        except Exception:
            return RetryExecutionResult(
                status="retry_scheduled",
                failure_code="summary_provider_error",
                retryable=True,
                next_retry_at=_aware_now(clock)
                + timedelta(seconds=default_retry_delay_seconds),
            )
        if lease_guard is not None:
            lease_guard()
        if not storage.update_article_summary(
            record.id,
            summary,
            content_sha256=expected_hash,
        ):
            return RetryExecutionResult(status="failed", failure_code="stale_content_version")
        return RetryExecutionResult(status="succeeded")

    return execute


def run_retry_child(
    db_path: str | os.PathLike[str],
    *,
    claim: RetryClaim,
    clock: Callable[[], datetime],
    lease_seconds: int,
    content_executor: RetryExecutor,
    summary_executor: RetryExecutor,
    successor_generation_coordinator: RetryExecutor | None = None,
    collection_executor: CollectionRetryExecutor | None = None,
    lease_heartbeat_interval_seconds: float = 30.0,
) -> RetryBatchResult:
    """Execute exactly the stages frozen into one claimed retry child.

    The function does not finalize the refresh run.  A completed gate requires
    cumulative reconciliation across the whole parent chain, not merely a
    successful retry sub-batch.
    """
    if not isinstance(claim, RetryClaim) or claim.disposition != "claimed":
        raise ValueError("claim must be a claimed RetryClaim")
    if not claim.lease_owner:
        raise ValueError("claimed retry run must have a lease owner")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    if not callable(content_executor) or not callable(summary_executor):
        raise TypeError("content_executor and summary_executor must be callable")
    if successor_generation_coordinator is not None and not callable(
        successor_generation_coordinator
    ):
        raise TypeError("successor_generation_coordinator must be callable")
    if collection_executor is not None and not callable(collection_executor):
        raise TypeError("collection_executor must be callable")

    return _run_retry_child_with_watchdog(
        db_path,
        claim=claim,
        clock=clock,
        lease_seconds=lease_seconds,
        content_executor=content_executor,
        summary_executor=summary_executor,
        successor_generation_coordinator=successor_generation_coordinator,
        collection_executor=collection_executor,
        lease_heartbeat_interval_seconds=lease_heartbeat_interval_seconds,
    )


def _run_retry_child_with_watchdog(
    db_path: str | os.PathLike[str],
    *,
    claim: RetryClaim,
    clock: Callable[[], datetime],
    lease_seconds: int,
    content_executor: RetryExecutor,
    summary_executor: RetryExecutor,
    successor_generation_coordinator: RetryExecutor | None,
    collection_executor: CollectionRetryExecutor | None,
    lease_heartbeat_interval_seconds: float,
) -> RetryBatchResult:
    def heartbeat() -> None:
        now = _aware_now(clock)
        if not renew_refresh_lease(
            db_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner or "",
            now=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        ):
            raise LeaseHeartbeatFailed("retry child lost its lease")

    watchdog = LeaseHeartbeatWatchdog(
        heartbeat, interval_seconds=lease_heartbeat_interval_seconds
    )
    try:
        return _run_retry_child_impl(
            db_path,
            claim=claim,
            clock=clock,
            lease_seconds=lease_seconds,
            content_executor=content_executor,
            summary_executor=summary_executor,
            successor_generation_coordinator=successor_generation_coordinator,
            collection_executor=collection_executor,
            watchdog=watchdog,
        )
    except LeaseHeartbeatFailed:
        return RetryBatchResult(claim.run_id, (), lost_lease=True)
    finally:
        watchdog.stop()


def _run_retry_child_impl(
    db_path: str | os.PathLike[str],
    *,
    claim: RetryClaim,
    clock: Callable[[], datetime],
    lease_seconds: int,
    content_executor: RetryExecutor,
    summary_executor: RetryExecutor,
    successor_generation_coordinator: RetryExecutor | None,
    collection_executor: CollectionRetryExecutor | None,
    watchdog: LeaseHeartbeatWatchdog,
) -> RetryBatchResult:

    outcomes: list[RetryTargetOutcome] = []
    watchdog_started = False

    def start_watchdog_before_blocking_work() -> None:
        nonlocal watchdog_started
        if not watchdog_started:
            watchdog.start()
            watchdog_started = True

    for target in list_collection_retry_targets(db_path, claim.run_id):
        terminal = _read_collection_target_terminal_attempt(db_path, target)
        if terminal is not None:
            outcomes.append(
                RetryTargetOutcome(
                    target.target_id,
                    None,
                    "collection",
                    terminal[0],
                    terminal[1],
                    target.source_name,
                )
            )
            continue

        lease_now = _aware_now(clock)
        if not renew_refresh_lease(
            db_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner,
            now=lease_now,
            lease_expires_at=lease_now + timedelta(seconds=lease_seconds),
        ):
            return RetryBatchResult(claim.run_id, tuple(outcomes), lost_lease=True)

        if collection_executor is None:
            execution = _collection_executor_failure(target.source_name)
        else:
            try:
                start_watchdog_before_blocking_work()
                execution = _normalize_collection_execution(
                    target,
                    collection_executor(target),
                    finished_at=_aware_now(clock),
                )
            except Exception:
                execution = _collection_executor_failure(target.source_name)

        watchdog.raise_if_failed()

        finished_at = _aware_now(clock)
        if not record_collection_attempt(
            db_path,
            run_id=claim.run_id,
            source_name=target.source_name,
            collector_status=execution.collector_status,
            source_row_count=execution.source_row_count,
            accepted_input_count=execution.accepted_input_count,
            rejected_source_row_count=execution.rejected_source_row_count,
            empty_reason=execution.empty_reason,
            failure_code=execution.failure_code,
            retryable=execution.retryable,
            next_retry_at=execution.next_retry_at,
            recorded_at=finished_at,
            worker_id=claim.lease_owner,
            provider_request=target.provider_request,
        ):
            return RetryBatchResult(claim.run_id, tuple(outcomes), lost_lease=True)
        outcomes.append(
            RetryTargetOutcome(
                target.target_id,
                None,
                "collection",
                execution.collector_status,
                execution.failure_code,
                target.source_name,
            )
        )

    # Index targets are one scope-level successor build, so all publisher and
    # summary mutations in this immutable child must finish before the first
    # index adapter snapshots canonical storage.
    retry_targets = list_retry_targets(db_path, claim.run_id)
    ordered_targets = tuple(target for target in retry_targets if target.stage != "index") + tuple(
        target for target in retry_targets if target.stage == "index"
    )
    for target in ordered_targets:
        terminal = read_retry_target_terminal_attempt(db_path, target)
        if terminal is not None:
            public_status = (
                "rebuild_required"
                if terminal.failure_code == "generation_rebuild_required"
                else terminal.status
            )
            outcomes.append(
                RetryTargetOutcome(
                    target.target_id,
                    target.article_id,
                    target.stage,
                    public_status,
                    terminal.failure_code,
                )
            )
            continue
        lease_now = _aware_now(clock)
        if not renew_refresh_lease(
            db_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner,
            now=lease_now,
            lease_expires_at=lease_now + timedelta(seconds=lease_seconds),
        ):
            return RetryBatchResult(claim.run_id, tuple(outcomes), lost_lease=True)

        try:
            attempt = start_processing_attempt(
                db_path,
                run_id=claim.run_id,
                article_id=target.article_id,
                stage=target.stage,
                worker_id=claim.lease_owner,
                started_at=lease_now,
                input_content_sha256=target.input_content_sha256,
            )
        except PermissionError:
            return RetryBatchResult(claim.run_id, tuple(outcomes), lost_lease=True)
        start_watchdog_before_blocking_work()

        public_status = "failed"
        if target.stage == "index" and successor_generation_coordinator is None:
            execution = RetryExecutionResult(
                status="failed",
                failure_code="generation_rebuild_required",
                retryable=True,
            )
            public_status = "rebuild_required"
        else:
            executor = _executor_for_target(
                target,
                content_executor=content_executor,
                summary_executor=summary_executor,
                successor_generation_coordinator=successor_generation_coordinator,
            )
            try:
                execution = executor(target)
                if not isinstance(execution, RetryExecutionResult):
                    raise TypeError("retry executor must return RetryExecutionResult")
                public_status = execution.status
            except Exception:
                # Unknown local/executor errors are not blindly scheduled.
                execution = RetryExecutionResult(
                    status="failed",
                    failure_code="retry_executor_error",
                    retryable=False,
                )
                public_status = "failed"

        watchdog.raise_if_failed()

        finished_at = _aware_now(clock)
        if not finish_processing_attempt(
            db_path,
            attempt_id=attempt.attempt_id,
            worker_id=claim.lease_owner,
            status=execution.status,
            finished_at=finished_at,
            failure_code=execution.failure_code,
            retryable=execution.retryable,
            next_retry_at=execution.next_retry_at,
        ):
            return RetryBatchResult(claim.run_id, tuple(outcomes), lost_lease=True)
        outcomes.append(
            RetryTargetOutcome(
                target.target_id,
                target.article_id,
                target.stage,
                public_status,
                execution.failure_code,
            )
        )
    return RetryBatchResult(claim.run_id, tuple(outcomes))


def _executor_for_target(
    target: RetryTarget,
    *,
    content_executor: RetryExecutor,
    summary_executor: RetryExecutor,
    successor_generation_coordinator: RetryExecutor | None,
) -> RetryExecutor:
    if target.stage == "content_fetch":
        return content_executor
    if target.stage == "summary":
        return summary_executor
    if target.stage == "index" and successor_generation_coordinator is not None:
        return successor_generation_coordinator
    raise ValueError(f"Retry runner does not execute stage {target.stage!r}")


def _read_collection_target_terminal_attempt(
    db_path: str | os.PathLike[str], target: CollectionRetryTarget
) -> tuple[str, str | None] | None:
    """Read a source terminal fact without extending a ledger write transaction."""
    with sqlite3.connect(os.fspath(db_path)) as conn:
        row = conn.execute(
            """
            SELECT collector_status, failure_code
            FROM collection_attempts
            WHERE run_id = ? AND source_name = ?
            ORDER BY recorded_at DESC, attempt_id DESC
            LIMIT 1
            """,
            (target.run_id, target.source_name),
        ).fetchone()
    return None if row is None else (row[0], row[1])


def _normalize_collection_execution(
    target: CollectionRetryTarget,
    outcome: CollectionSourceOutcome,
    *,
    finished_at: datetime,
) -> _CollectionAttemptExecution:
    """Reduce a callback outcome to the ledger's safe terminal source contract."""
    if not isinstance(outcome, CollectionSourceOutcome) or outcome.source_name != target.source_name:
        return _collection_executor_failure(target.source_name)
    raw_inputs = list(outcome.raw_inputs)
    if len(raw_inputs) + outcome.rejected_source_row_count != outcome.source_row_count:
        return _collection_executor_failure(target.source_name)
    if outcome.collector_status == "succeeded":
        return _CollectionAttemptExecution(
            "succeeded",
            outcome.source_row_count,
            len(raw_inputs),
            outcome.rejected_source_row_count,
            outcome.empty_reason,
            None,
            False,
            None,
        )
    if outcome.failure_code not in COLLECTION_FAILURE_CODES:
        return _collection_executor_failure(target.source_name)
    next_retry_at = outcome.retry_after_at
    if next_retry_at is None and outcome.retry_after_seconds and outcome.retry_after_seconds > 0:
        next_retry_at = finished_at + timedelta(seconds=outcome.retry_after_seconds)
    if next_retry_at is not None and next_retry_at <= finished_at:
        next_retry_at = None
    return _CollectionAttemptExecution(
        "failed",
        outcome.source_row_count,
        len(raw_inputs),
        outcome.rejected_source_row_count,
        None,
        outcome.failure_code,
        outcome.retryable,
        next_retry_at,
    )


def _collection_executor_failure(source_name: str) -> _CollectionAttemptExecution:
    """Fail closed when the injected source callback is absent or malformed."""
    return _CollectionAttemptExecution(
        "failed",
        0,
        0,
        0,
        None,
        "collector_error",
        False,
        None,
    )


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


def _validate_default_executor_inputs(
    storage: SQLiteNewsStore,
    clock: Callable[[], datetime],
    default_retry_delay_seconds: int,
) -> None:
    if not isinstance(storage, SQLiteNewsStore):
        raise TypeError("storage must be a SQLiteNewsStore")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if (
        isinstance(default_retry_delay_seconds, bool)
        or not isinstance(default_retry_delay_seconds, int)
        or default_retry_delay_seconds <= 0
    ):
        raise ValueError("default_retry_delay_seconds must be a positive integer")
