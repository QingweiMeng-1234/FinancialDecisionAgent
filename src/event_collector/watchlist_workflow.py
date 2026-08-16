"""Package-native watchlist workflow seam for artifacts, refresh, and reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Protocol
import uuid

from event_collector.article_content import ArticleContentFetcher, FetchFailureReason
from event_collector.event_collection import CollectionSourceOutcome, NewsCollector
from event_collector.news_ingestion import ingest_raw_inputs
from event_collector.news_storage import SQLiteNewsStore
from event_collector.news_pipeline import NewsPipelineRequest, run_news_pipeline
from event_collector.refresh_ledger import (
    LedgerProcessingAttemptRecorder,
    RefreshArticleObservation,
    RefreshCounts,
    RefreshOutcomeFacts,
    claim_due_retry_run,
    claim_refresh_run,
    classify_refresh_outcome,
    compute_scope_key,
    finalize_retry_run_from_chain,
    finalize_refresh_run,
    initialize_schema,
    list_retry_targets,
    record_collection_attempt,
    record_refresh_article_observations,
    record_refresh_generation_proof,
    read_cumulative_scope_state,
    renew_refresh_lease,
)
from event_collector.refresh_lease_watchdog import (
    LeaseHeartbeatFailed,
    LeaseHeartbeatWatchdog,
)
from event_collector.refresh_retry import (
    RetryExecutionResult,
    build_default_content_retry_executor,
    build_default_summary_retry_executor,
    run_retry_child,
)
from event_collector.service_defaults import DEFAULT_SERVICE_DEFAULTS_PATH, load_service_defaults
from event_collector.summarization import SummarizationAgent
from event_collector.vector_store import VectorStore
from event_collector.watchlist_presentation import render_watchlist_report
from event_collector.watchlist_progress import (
    TimelineProvenanceError,
    WatchlistProgressEvent,
    WatchlistProgressSink,
    WatchlistTimelineRecorder,
    build_watchlist_timing_summary,
    load_watchlist_timeline_document,
    render_watchlist_timing_text,
    utc_now,
)
from event_collector.watchlist_research import WatchlistResearchConfig, WatchlistResearchRun, run_watchlist_research

DEFAULT_REPORTS_DIR = os.path.join("reports", "watchlist_triage")
DEFAULT_TIMELINE_SUFFIX = ".timeline.jsonl"
REFRESH_CONTRACT_VERSION = "refresh-generation-proof-v5"
REFRESH_LEASE_SECONDS = 300
REFRESH_LEASE_HEARTBEAT_INTERVAL_SECONDS = 30.0
REFRESH_RETRY_MAX_ATTEMPTS = {
    "content_fetch": 3,
    "summary": 3,
    "index": 3,
    "reconcile": 1,
    "collection": 3,
}
_SAFE_ITEM_FAILURE_CODES = frozenset(
    {
        *(reason.value for reason in FetchFailureReason),
        "content_fetch_failed",
        "summary_failed",
        "index_failed",
        "missing_text",
        "invalid_source",
        "short_news_text_without_url",
        "invalid_news_url",
        "short_text",
    }
)


@dataclass(frozen=True)
class GenerationArticleProof:
    article_id: int
    indexed_content_sha256: str


@dataclass(frozen=True)
class SuccessorGenerationBuildRequest:
    run_id: str
    scope_key: str
    corpus_id: str
    articles: tuple[GenerationArticleProof, ...]
    lease_heartbeat: Callable[[], None] | None = None


@dataclass(frozen=True)
class SuccessorGenerationProof:
    corpus_id: str
    generation_id: str
    corpus_snapshot_id: str
    index_config_fingerprint: str
    status: str
    chunk_verification_valid: bool
    articles: tuple[GenerationArticleProof, ...]


class SuccessorGenerationCoordinator(Protocol):
    def __call__(self, request: SuccessorGenerationBuildRequest) -> SuccessorGenerationProof:
        """Build and verify an isolated successor generation without activating it."""


@dataclass(frozen=True)
class RefreshNewsRequest:
    refresh_state_path: str = os.path.join("data", "runtime", "refresh_news_state.json")
    refresh_ledger_path: str | None = None
    service_defaults_path: str = DEFAULT_SERVICE_DEFAULTS_PATH
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"
    index_corpus_id: str = "news"
    today: str | None = None
    top_k: int | None = None
    question: str | None = None
    debug_rerank: bool = False
    include_manual: bool = False
    news_endpoint: str = "everything"
    news_days_back: int = 7
    news_page: int = 1
    news_sort_by: str = "publishedAt"
    news_page_size: int = 100
    show_progress: bool = False


@dataclass(frozen=True)
class WatchlistWorkflowResult:
    result: Any
    batching: dict[str, Any]
    report_path: str
    timeline_path: str | None
    timing_summary: dict[str, Any]
    timing_text: str
    refresh: dict[str, Any] | None = None


class RefreshWorkflowBlockedError(RuntimeError):
    """A refresh disposition that does not permit downstream watchlist work."""

    def __init__(self, refresh: dict[str, Any]):
        self.refresh = refresh
        super().__init__(
            f"Watchlist triage blocked by refresh status={refresh.get('status')!r} "
            f"run_id={refresh.get('run_id')!r}"
        )


class _LazySummarizer:
    def __init__(self) -> None:
        self._delegate: SummarizationAgent | None = None

    def summarize_article(self, article: Any) -> str:
        if self._delegate is None:
            self._delegate = SummarizationAgent()
        return self._delegate.summarize_article(article)


def refresh_news_corpus(
    request: RefreshNewsRequest,
    *,
    progress_sink: WatchlistProgressSink | None = None,
    run_news_pipeline_fn: Callable[[NewsPipelineRequest], Any] = run_news_pipeline,
    successor_generation_coordinator: SuccessorGenerationCoordinator | None = None,
    retry_content_fetcher: ArticleContentFetcher | object | None = None,
    retry_summarizer: SummarizationAgent | object | None = None,
    retry_news_collector: NewsCollector | object | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="refresh_news",
            status="started",
            started_at=started_at,
        ),
    )
    refresh_date = request.today or date.today().isoformat()
    ledger_path = _resolve_refresh_ledger_path(request)
    scope_config = _refresh_scope_config(request, refresh_date)
    scope_key = compute_scope_key(scope_config)
    lease_owner = f"refresh-{uuid.uuid4().hex}"
    lease_expires_at = started_at + timedelta(seconds=REFRESH_LEASE_SECONDS)
    initialize_schema(ledger_path)
    retry_claim = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner=lease_owner,
        now=started_at,
        lease_expires_at=lease_expires_at,
        max_attempts_by_stage=REFRESH_RETRY_MAX_ATTEMPTS,
    )
    if retry_claim.disposition not in {"retry_unavailable", "claimed"}:
        public_status = (
            "failed" if retry_claim.disposition == "retry_exhausted" else retry_claim.disposition
        )
        payload = _refresh_payload(
            status=public_status,
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=retry_claim.run_id,
            attempt_no=retry_claim.attempt_no,
            counts=RefreshCounts(),
            retry_after=retry_claim.retry_after,
            failure_code=(
                "retry_exhausted" if retry_claim.disposition == "retry_exhausted" else None
            ),
            disposition=retry_claim.disposition,
            message=_retry_disposition_message(retry_claim.disposition),
        )
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=payload["message"],
                metrics={"refresh_status": payload["status"], "run_id": retry_claim.run_id},
            ),
        )
        return payload
    if retry_claim.disposition == "claimed":
        return _run_claimed_retry_child(
            request,
            claim=retry_claim,
            ledger_path=ledger_path,
            refresh_date=refresh_date,
            scope_key=scope_key,
            progress_sink=progress_sink,
            started_at=started_at,
            content_fetcher=retry_content_fetcher,
            summarizer=retry_summarizer,
            successor_generation_coordinator=successor_generation_coordinator,
            news_collector=retry_news_collector,
        )
    claim = claim_refresh_run(
        ledger_path,
        scope_key=scope_key,
        requested_date=refresh_date,
        lease_owner=lease_owner,
        now=started_at,
        lease_expires_at=lease_expires_at,
        config_snapshot=scope_config,
    )
    if claim.disposition != "claimed":
        payload = _refresh_payload(
            status=claim.disposition,
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=claim.run_id,
            attempt_no=claim.attempt_no,
            counts=RefreshCounts(),
            message=(
                "A completed refresh already owns this scope."
                if claim.disposition == "already_completed"
                else "A refresh worker currently owns this scope."
            ),
        )
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=payload["message"],
                metrics={"refresh_status": payload["status"], "run_id": claim.run_id},
            ),
        )
        return payload

    watchdog: LeaseHeartbeatWatchdog | None = None
    try:
        watchdog = LeaseHeartbeatWatchdog(
            lambda: _renew_refresh_lease_or_raise(
                ledger_path=ledger_path,
                run_id=claim.run_id,
                lease_owner=lease_owner,
            ),
            interval_seconds=REFRESH_LEASE_HEARTBEAT_INTERVAL_SECONDS,
        )
        watchdog.start()
        defaults = load_service_defaults(request.service_defaults_path)
        attempt_recorder = LedgerProcessingAttemptRecorder(
            ledger_path=ledger_path,
            run_id=claim.run_id,
            worker_id=lease_owner,
            clock=utc_now,
        )
        result = run_news_pipeline_fn(
            NewsPipelineRequest(
                db_path=request.db_path,
                persist_dir=request.persist_dir,
                collection_name=request.collection_name,
                top_k=request.top_k or defaults.query_top_k,
                question=request.question,
                debug_rerank=request.debug_rerank,
                include_manual=request.include_manual,
                news_endpoint=request.news_endpoint,
                news_days_back=request.news_days_back,
                news_page=request.news_page,
                news_sort_by=request.news_sort_by,
                news_page_size=request.news_page_size,
                show_progress=request.show_progress,
                attempt_recorder=attempt_recorder,
            )
        )
        watchdog.raise_if_failed()
        if not _record_collection_outcomes(
            result,
            ledger_path=ledger_path,
            run_id=claim.run_id,
            worker_id=lease_owner,
        ):
            raise RuntimeError("Refresh lease was lost while recording collection outcomes")
        watchdog.raise_if_failed()
        generation_proof, generation_failure_code = _build_successor_generation_proof(
            result,
            run_id=claim.run_id,
            scope_key=scope_key,
            corpus_id=request.index_corpus_id,
            coordinator=successor_generation_coordinator,
            lease_heartbeat=watchdog.pulse,
        )
        watchdog.raise_if_failed()
        verified_index_articles = _verified_generation_articles(generation_proof)
        counts, facts = _derive_refresh_counts(
            result,
            verified_index_articles=verified_index_articles,
        )
        status = classify_refresh_outcome(facts)
        failure_code = _derive_refresh_failure_code(
            status=status,
            generation_failure_code=generation_failure_code,
            result=result,
        )
        finished_at = utc_now()
        if not record_refresh_article_observations(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=lease_owner,
            observations=_refresh_article_observations(result),
            recorded_at=finished_at,
        ):
            return _refresh_payload(
                status="failed",
                refresh_date=refresh_date,
                scope_key=scope_key,
                run_id=claim.run_id,
                attempt_no=claim.attempt_no,
                counts=counts,
                failure_code="refresh_lease_lost",
                message="Refresh lease was lost before article observations could be committed.",
            )
        if generation_proof is not None and not record_refresh_generation_proof(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=lease_owner,
            corpus_id=generation_proof.corpus_id,
            generation_id=generation_proof.generation_id,
            corpus_snapshot_id=generation_proof.corpus_snapshot_id,
            index_config_fingerprint=generation_proof.index_config_fingerprint,
            manifest={
                item.article_id: item.indexed_content_sha256
                for item in generation_proof.articles
            },
            recorded_at=finished_at,
        ):
            return _refresh_payload(
                status="failed",
                refresh_date=refresh_date,
                scope_key=scope_key,
                run_id=claim.run_id,
                attempt_no=claim.attempt_no,
                counts=counts,
                failure_code="refresh_lease_lost",
                message="Refresh lease was lost before generation proof could be committed.",
            )
        watchdog.raise_if_failed()
        watchdog.stop()
        watchdog.raise_if_failed()
        if not finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=lease_owner,
            status=status,
            collector_status="succeeded",
            counts=counts,
            finished_at=finished_at,
            empty_reason=facts.empty_reason,
        ):
            return _refresh_payload(
                status="failed",
                refresh_date=refresh_date,
                scope_key=scope_key,
                run_id=claim.run_id,
                attempt_no=claim.attempt_no,
                counts=counts,
                failure_code="refresh_lease_lost",
                message="Refresh lease was lost before terminal state could be committed.",
            )
        payload = _refresh_payload(
            status=status,
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=claim.run_id,
            attempt_no=claim.attempt_no,
            counts=counts,
            collected_events=getattr(result, "collected_events", 0),
            total_articles=getattr(result, "total_articles", 0),
            answer_text=getattr(result, "answer_text", None),
            stats=dict(getattr(result, "stats", {})),
            failure_code=failure_code,
            generation_proof=generation_proof,
        )
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                metrics={
                    "refresh_status": payload["status"],
                    "collected_events": payload["collected_events"],
                    "total_articles": payload["total_articles"],
                    "usable_items": counts.usable_items,
                },
            ),
        )
        return payload
    except LeaseHeartbeatFailed as exc:
        finished_at = utc_now()
        counts = RefreshCounts()
        finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=lease_owner,
            status="failed",
            collector_status="failed",
            counts=counts,
            finished_at=finished_at,
        )
        payload = _refresh_payload(
            status="failed",
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=claim.run_id,
            attempt_no=claim.attempt_no,
            counts=counts,
            failure_code="refresh_lease_lost",
            message=str(exc),
        )
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=str(exc),
            ),
        )
        return payload
    except Exception as exc:
        finished_at = utc_now()
        counts = RefreshCounts()
        finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=lease_owner,
            status="failed",
            collector_status="failed",
            counts=counts,
            finished_at=finished_at,
        )
        payload = _refresh_payload(
            status="failed",
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=claim.run_id,
            attempt_no=claim.attempt_no,
            counts=counts,
            failure_code="pipeline_exception",
            message=str(exc),
        )
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=str(exc),
            ),
        )
        return payload
    finally:
        if watchdog is not None:
            watchdog.stop()


def _resolve_refresh_ledger_path(request: RefreshNewsRequest) -> str:
    if request.refresh_ledger_path:
        return request.refresh_ledger_path
    legacy_state_path = Path(request.refresh_state_path)
    return str(legacy_state_path.with_name(f"{legacy_state_path.stem}.sqlite3"))


def _retry_disposition_message(disposition: str) -> str:
    return {
        "already_completed": "A completed refresh already owns this scope.",
        "in_progress": "A refresh worker currently owns this scope.",
        "retry_scheduled": "Retry work exists but is not due yet.",
        "retry_exhausted": "Automatic retry attempts are exhausted; manual review is required.",
    }.get(disposition, "Refresh retry is unavailable.")


def _run_claimed_retry_child(
    request: RefreshNewsRequest,
    *,
    claim: Any,
    ledger_path: str,
    refresh_date: str,
    scope_key: str,
    progress_sink: WatchlistProgressSink | None,
    started_at: Any,
    content_fetcher: ArticleContentFetcher | object | None,
    summarizer: SummarizationAgent | object | None,
    successor_generation_coordinator: SuccessorGenerationCoordinator | None,
    news_collector: NewsCollector | object | None,
) -> dict[str, Any]:
    watchdog = LeaseHeartbeatWatchdog(
        lambda: _renew_refresh_lease_or_raise(
            ledger_path=ledger_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner,
        ),
        interval_seconds=REFRESH_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    )
    watchdog.start()
    storage = SQLiteNewsStore(db_path=request.db_path)
    try:
        storage.init_db()
        runtime_fetcher = content_fetcher or ArticleContentFetcher(clock=utc_now)
        runtime_summarizer = summarizer or _LazySummarizer()
        runtime_news_collector = news_collector or NewsCollector(
            page_size=request.news_page_size,
            endpoint=request.news_endpoint,
            days_back=request.news_days_back,
            page=request.news_page,
            sort_by=request.news_sort_by,
        )
        collection_ingestion_results: list[Any] = []
        collection_outcomes: list[CollectionSourceOutcome] = []
        child_attempt_recorder = LedgerProcessingAttemptRecorder(
            ledger_path=ledger_path,
            run_id=claim.run_id,
            worker_id=claim.lease_owner,
            clock=utc_now,
        )
        prior_state = read_cumulative_scope_state(ledger_path, scope_key)
        generation_proof: SuccessorGenerationProof | None = None
        generation_failure_code: str | None = None
        generation_attempted = False

        def execute_index_target(target: Any) -> RetryExecutionResult:
            nonlocal generation_proof, generation_failure_code, generation_attempted
            if not generation_attempted:
                generation_attempted = True
                generation_proof, generation_failure_code = _build_retry_generation_proof(
                    storage,
                    prior_state.articles,
                    extra_article_ids={
                        retry_target.article_id
                        for retry_target in list_retry_targets(ledger_path, claim.run_id)
                        if retry_target.stage == "index"
                    },
                    run_id=claim.run_id,
                    scope_key=scope_key,
                    corpus_id=request.index_corpus_id,
                    coordinator=successor_generation_coordinator,
                    lease_heartbeat=lambda: _renew_refresh_lease_or_raise(
                        ledger_path=ledger_path,
                        run_id=claim.run_id,
                        lease_owner=claim.lease_owner,
                    ),
                )
                if generation_proof is not None and not record_refresh_generation_proof(
                    ledger_path,
                    run_id=claim.run_id,
                    lease_owner=claim.lease_owner,
                    corpus_id=generation_proof.corpus_id,
                    generation_id=generation_proof.generation_id,
                    corpus_snapshot_id=generation_proof.corpus_snapshot_id,
                    index_config_fingerprint=generation_proof.index_config_fingerprint,
                    manifest={
                        article.article_id: article.indexed_content_sha256
                        for article in generation_proof.articles
                    },
                    recorded_at=utc_now(),
                ):
                    raise RuntimeError("Refresh retry lost its lease before generation proof")
            if generation_proof is not None and any(
                article.article_id == target.article_id
                and article.indexed_content_sha256 == target.input_content_sha256
                for article in generation_proof.articles
            ):
                return RetryExecutionResult(status="succeeded")
            return RetryExecutionResult(
                status="retry_scheduled",
                failure_code=generation_failure_code or "generation_rebuild_required",
                retryable=True,
                next_retry_at=utc_now() + timedelta(seconds=60),
            )

        def execute_collection_target(target: Any) -> CollectionSourceOutcome:
            source_name = target.source_name
            if source_name.startswith("news#batch:"):
                if target.provider_request is None:
                    raise ValueError("NewsAPI batch retry target is missing its frozen request")
                outcome = runtime_news_collector.retry_frozen_source_batch(
                    target.provider_request
                )
            elif source_name == "news":
                outcome = runtime_news_collector.collect_result()
            else:
                raise ValueError("Only NewsAPI collection targets are retryable")
            if not isinstance(outcome, CollectionSourceOutcome):
                raise TypeError("News collection retry must return CollectionSourceOutcome")
            watchdog.pulse()
            collection_outcomes.append(outcome)
            if outcome.collector_status == "succeeded" and outcome.raw_inputs:
                collection_ingestion_results.append(
                    ingest_raw_inputs(
                        list(outcome.raw_inputs),
                        storage,
                        None,
                        summarizer=runtime_summarizer,
                        content_fetcher=runtime_fetcher,
                        lease_guard=watchdog.pulse,
                        attempt_recorder=child_attempt_recorder,
                        show_progress=request.show_progress,
                    )
                )
            return replace(outcome, source_name=source_name)

        batch = run_retry_child(
            ledger_path,
            claim=claim,
            clock=utc_now,
            lease_seconds=REFRESH_LEASE_SECONDS,
            content_executor=build_default_content_retry_executor(
                storage=storage,
                fetcher=runtime_fetcher,
                clock=utc_now,
                lease_guard=watchdog.pulse,
            ),
            summary_executor=build_default_summary_retry_executor(
                storage=storage,
                summarizer=runtime_summarizer,
                clock=utc_now,
                lease_guard=watchdog.pulse,
            ),
            successor_generation_coordinator=execute_index_target,
            collection_executor=execute_collection_target,
        )
        if batch.lost_lease:
            return _refresh_payload(
                status="failed",
                refresh_date=refresh_date,
                scope_key=scope_key,
                run_id=claim.run_id,
                attempt_no=claim.attempt_no,
                counts=RefreshCounts(),
                failure_code="refresh_lease_lost",
                message="Refresh retry lost its lease during target execution.",
            )
        watchdog.raise_if_failed()

        recovered_content_ids = {
            outcome.article_id
            for outcome in batch.outcomes
            if outcome.stage == "content_fetch"
            and outcome.status == "succeeded"
            and isinstance(outcome.article_id, int)
        }
        recovered_content_ids.update(
            item.article_id
            for ingestion_result in collection_ingestion_results
            for item in ingestion_result.items
            if item.status == "accepted"
            and item.content_status == "ready"
            and isinstance(item.article_id, int)
        )
        collection_article_ids = {
            item.article_id
            for ingestion_result in collection_ingestion_results
            for item in ingestion_result.items
            if item.status == "accepted" and isinstance(item.article_id, int)
        }
        collection_permanent_article_ids = {
            item.article_id
            for ingestion_result in collection_ingestion_results
            for item in ingestion_result.items
            if item.status == "accepted"
            and item.retryable is False
            and isinstance(item.article_id, int)
        }
        if recovered_content_ids and generation_proof is None:
            generation_proof, generation_failure_code = _build_retry_generation_proof(
                storage,
                prior_state.articles,
                extra_article_ids=recovered_content_ids,
                run_id=claim.run_id,
                scope_key=scope_key,
                corpus_id=request.index_corpus_id,
                coordinator=successor_generation_coordinator,
                lease_heartbeat=lambda: _renew_refresh_lease_or_raise(
                    ledger_path=ledger_path,
                    run_id=claim.run_id,
                    lease_owner=claim.lease_owner,
                ),
            )
            if generation_proof is not None and not record_refresh_generation_proof(
                ledger_path,
                run_id=claim.run_id,
                lease_owner=claim.lease_owner,
                corpus_id=generation_proof.corpus_id,
                generation_id=generation_proof.generation_id,
                corpus_snapshot_id=generation_proof.corpus_snapshot_id,
                index_config_fingerprint=generation_proof.index_config_fingerprint,
                manifest={
                    article.article_id: article.indexed_content_sha256
                    for article in generation_proof.articles
                },
                recorded_at=utc_now(),
            ):
                return _refresh_payload(
                    status="failed",
                    refresh_date=refresh_date,
                    scope_key=scope_key,
                    run_id=claim.run_id,
                    attempt_no=claim.attempt_no,
                    counts=RefreshCounts(),
                    failure_code="refresh_lease_lost",
                    message="Refresh retry lost its lease before generation proof was committed.",
                )
        if recovered_content_ids:
            _schedule_recovered_article_obligations(
                ledger_path,
                run_id=claim.run_id,
                lease_owner=claim.lease_owner,
                storage=storage,
                article_ids=recovered_content_ids,
                generation_ready=generation_proof is not None,
                generation_failure_code=generation_failure_code,
            )

        if not record_refresh_article_observations(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner,
            observations=_observations_from_storage(
                storage,
                prior_state.articles,
                collection_article_ids | recovered_content_ids,
                extra_permanent_exclusions=collection_permanent_article_ids,
            ),
            recorded_at=utc_now(),
        ):
            return _refresh_payload(
                status="failed",
                refresh_date=refresh_date,
                scope_key=scope_key,
                run_id=claim.run_id,
                attempt_no=claim.attempt_no,
                counts=RefreshCounts(),
                failure_code="refresh_lease_lost",
                message="Refresh retry lost its lease before observations were committed.",
            )
        cumulative = read_cumulative_scope_state(ledger_path, scope_key)
        counts = _derive_cumulative_refresh_counts(
            cumulative,
            collection_ingestion_results=collection_ingestion_results,
            collection_outcomes=collection_outcomes,
        )
        finished_at = utc_now()
        status = finalize_retry_run_from_chain(
            ledger_path,
            run_id=claim.run_id,
            lease_owner=claim.lease_owner,
            cumulative_counts=counts,
            finished_at=finished_at,
        )
        payload = _refresh_payload(
            status=status,
            refresh_date=refresh_date,
            scope_key=scope_key,
            run_id=claim.run_id,
            attempt_no=claim.attempt_no,
            counts=counts,
            failure_code=generation_failure_code,
            message="Targeted refresh retry finished from cumulative scope evidence.",
        )
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                metrics={"refresh_status": status, "run_id": claim.run_id},
            ),
        )
        return payload
    finally:
        watchdog.stop()
        storage.close()


def _build_retry_generation_proof(
    storage: SQLiteNewsStore,
    prior_observations: tuple[RefreshArticleObservation, ...],
    *,
    extra_article_ids: set[int],
    run_id: str,
    scope_key: str,
    corpus_id: str,
    coordinator: SuccessorGenerationCoordinator | None,
    lease_heartbeat: Callable[[], None],
) -> tuple[SuccessorGenerationProof | None, str | None]:
    candidates: list[GenerationArticleProof] = []
    article_ids = {observation.article_id for observation in prior_observations} | extra_article_ids
    for article_id in sorted(article_ids):
        record = storage.get_article_record(article_id)
        if record is None or record.content_status != "ready":
            continue
        content_hash = record.article.active_content_sha256 or record.article.content_sha256
        if _is_sha256(content_hash):
            candidates.append(GenerationArticleProof(record.id, content_hash))
    frozen_candidates = tuple(sorted(candidates, key=lambda article: article.article_id))
    if not frozen_candidates:
        return None, "generation_rebuild_required"
    if coordinator is None:
        return None, "generation_rebuild_required"
    try:
        proof = coordinator(
            SuccessorGenerationBuildRequest(
                run_id=run_id,
                scope_key=scope_key,
                corpus_id=corpus_id,
                articles=frozen_candidates,
                lease_heartbeat=lease_heartbeat,
            )
        )
    except Exception:
        return None, "generation_rebuild_failed"
    if not _proof_matches_request(proof, corpus_id=corpus_id, candidates=frozen_candidates):
        return None, "generation_proof_invalid"
    return proof, None


def _schedule_recovered_article_obligations(
    ledger_path: str,
    *,
    run_id: str,
    lease_owner: str,
    storage: SQLiteNewsStore,
    article_ids: set[int],
    generation_ready: bool,
    generation_failure_code: str | None,
) -> None:
    recorder = LedgerProcessingAttemptRecorder(
        ledger_path=ledger_path,
        run_id=run_id,
        worker_id=lease_owner,
        clock=utc_now,
    )
    for article_id in sorted(article_ids):
        record = storage.get_article_record(article_id)
        if record is None:
            continue
        content_hash = record.article.active_content_sha256 or record.article.content_sha256
        if not _is_sha256(content_hash):
            continue
        if record.summary_status != "ready" and not _child_has_terminal_stage(
            ledger_path, run_id, article_id, "summary"
        ):
            attempt = recorder.begin_attempt(
                article_id=article_id,
                stage="summary",
                input_content_sha256=content_hash,
            )
            if not recorder.finish_attempt(
                attempt,
                status="failed",
                failure_code="summary_pending_after_content_retry",
                retryable=True,
            ):
                raise RuntimeError("Refresh retry lost its lease while scheduling summary work")
        if not generation_ready and not _child_has_terminal_stage(
            ledger_path, run_id, article_id, "index"
        ):
            attempt = recorder.begin_attempt(
                article_id=article_id,
                stage="index",
                input_content_sha256=content_hash,
            )
            if not recorder.finish_attempt(
                attempt,
                status="failed",
                failure_code=generation_failure_code or "generation_rebuild_required",
                retryable=True,
            ):
                raise RuntimeError("Refresh retry lost its lease while scheduling index work")


def _child_has_terminal_stage(
    ledger_path: str,
    run_id: str,
    article_id: int,
    stage: str,
) -> bool:
    with sqlite3.connect(ledger_path) as conn:
        return conn.execute(
            "SELECT 1 FROM article_processing_attempts "
            "WHERE run_id = ? AND article_id = ? AND stage = ? AND status != 'running' LIMIT 1",
            (run_id, article_id, stage),
        ).fetchone() is not None


def _observations_from_storage(
    storage: SQLiteNewsStore,
    prior_observations: tuple[RefreshArticleObservation, ...],
    extra_article_ids: set[int] | None = None,
    *,
    extra_permanent_exclusions: set[int] | None = None,
) -> tuple[RefreshArticleObservation, ...]:
    observations: list[RefreshArticleObservation] = []
    prior_by_id = {observation.article_id: observation for observation in prior_observations}
    article_ids = set(prior_by_id) | set(extra_article_ids or ())
    for article_id in sorted(article_ids):
        prior = prior_by_id.get(article_id)
        record = storage.get_article_record(article_id)
        if record is None:
            continue
        active_hash = record.article.active_content_sha256 or record.article.content_sha256
        observations.append(
            RefreshArticleObservation(
                article_id=article_id,
                content_sha256=active_hash if _is_sha256(active_hash) else None,
                content_status=(
                    record.content_status
                    if record.content_status in {"pending", "ready", "failed"}
                    else "failed"
                ),
                summary_status=(
                    record.summary_status
                    if record.summary_status in {"pending", "ready", "failed"}
                    else "failed"
                ),
                permanent_exclusion=(
                    prior.permanent_exclusion if prior is not None else False
                )
                or article_id in set(extra_permanent_exclusions or ()),
            )
        )
    return tuple(observations)


def _derive_cumulative_refresh_counts(
    state: Any,
    *,
    collection_ingestion_results: list[Any] | None = None,
    collection_outcomes: list[CollectionSourceOutcome] | None = None,
) -> RefreshCounts:
    root = state.root_counts
    ingestion_results = list(collection_ingestion_results or ())
    outcomes = [
        outcome
        for outcome in list(collection_outcomes or ())
        if outcome.collector_status == "succeeded"
    ]
    added_accepted = sum(result.accepted_inputs for result in ingestion_results)
    added_ingestion_rejected = sum(result.rejected_inputs for result in ingestion_results)
    added_provider_rejected = sum(outcome.rejected_source_row_count for outcome in outcomes)
    added_total = sum(outcome.source_row_count for outcome in outcomes)
    accepted_inputs = max(
        root.accepted_inputs + added_accepted,
        len(state.articles),
    )
    rejected_inputs = (
        root.rejected_inputs + added_ingestion_rejected + added_provider_rejected
    )
    total_inputs = max(
        root.total_inputs + added_total,
        accepted_inputs + rejected_inputs,
    )
    manifest = dict(state.verified_index_articles)
    content_ready = summary_ready = index_ready = usable_items = failed_items = 0
    permanent_exclusions = retryable_failures = 0
    for observation in state.articles:
        has_content = observation.content_status == "ready" and _is_sha256(
            observation.content_sha256
        )
        has_summary = observation.summary_status == "ready"
        has_index = has_content and manifest.get(observation.article_id) == observation.content_sha256
        content_ready += int(has_content)
        summary_ready += int(has_summary)
        index_ready += int(has_index)
        usable_items += int(has_content and has_index)
        if not has_content or not has_summary or not has_index:
            failed_items += 1
            if observation.permanent_exclusion:
                permanent_exclusions += 1
            else:
                retryable_failures += 1
    unrepresented = max(accepted_inputs - len(state.articles), 0)
    failed_items += unrepresented
    retryable_failures += unrepresented
    return RefreshCounts(
        total_inputs=total_inputs,
        accepted_inputs=accepted_inputs,
        rejected_inputs=rejected_inputs,
        rejected_permanent_exclusions=(
            root.rejected_permanent_exclusions
            + added_ingestion_rejected
            + added_provider_rejected
        ),
        unchanged_ready=min(root.unchanged_ready, usable_items),
        content_ready=content_ready,
        summary_ready=summary_ready,
        index_ready=index_ready,
        usable_items=usable_items,
        failed_items=failed_items,
        permanent_exclusions=permanent_exclusions,
        retryable_failures=retryable_failures,
        source_errors=0,
    )


def _refresh_scope_config(request: RefreshNewsRequest, refresh_date: str) -> dict[str, Any]:
    return {
        "requested_date": refresh_date,
        "news_endpoint": request.news_endpoint,
        "news_days_back": request.news_days_back,
        "news_page": request.news_page,
        "news_sort_by": request.news_sort_by,
        "news_page_size": request.news_page_size,
        "include_manual": request.include_manual,
        "corpus_collection": request.collection_name,
        "index_corpus_id": request.index_corpus_id,
        "ingestion_contract_version": REFRESH_CONTRACT_VERSION,
    }


def _build_successor_generation_proof(
    result: Any,
    *,
    run_id: str,
    scope_key: str,
    corpus_id: str,
    coordinator: SuccessorGenerationCoordinator | None,
    lease_heartbeat: Callable[[], None] | None = None,
) -> tuple[SuccessorGenerationProof | None, str | None]:
    candidates_by_id: dict[int, str] = {}
    for item in list(getattr(result, "items", ()) or ()):
        article_id = getattr(item, "article_id", None)
        content_hash = getattr(item, "content_sha256", None)
        if (
            getattr(item, "status", None) != "accepted"
            or getattr(item, "content_status", None) != "ready"
            or not isinstance(article_id, int)
            or isinstance(article_id, bool)
            or article_id <= 0
            or not _is_sha256(content_hash)
        ):
            continue
        observed_hash = candidates_by_id.get(article_id)
        if observed_hash is not None and observed_hash != content_hash:
            return None, "generation_article_conflict"
        candidates_by_id[article_id] = content_hash
    candidates = tuple(
        GenerationArticleProof(article_id=article_id, indexed_content_sha256=content_hash)
        for article_id, content_hash in sorted(candidates_by_id.items())
    )
    if not candidates:
        return None, None
    if coordinator is None:
        return None, "generation_rebuild_required"
    try:
        proof = coordinator(
            SuccessorGenerationBuildRequest(
                run_id=run_id,
                scope_key=scope_key,
                corpus_id=corpus_id,
                articles=candidates,
                lease_heartbeat=lease_heartbeat,
            )
        )
    except Exception:
        return None, "generation_rebuild_failed"
    if not _proof_matches_request(proof, corpus_id=corpus_id, candidates=candidates):
        return None, "generation_proof_invalid"
    return proof, None


def _renew_refresh_lease_or_raise(
    *,
    ledger_path: str,
    run_id: str,
    lease_owner: str,
) -> None:
    now = utc_now()
    if not renew_refresh_lease(
        ledger_path,
        run_id=run_id,
        lease_owner=lease_owner,
        now=now,
        lease_expires_at=now + timedelta(seconds=REFRESH_LEASE_SECONDS),
    ):
        raise RuntimeError("Refresh lease was lost during refresh processing")


def _proof_matches_request(
    proof: object,
    *,
    corpus_id: str,
    candidates: tuple[GenerationArticleProof, ...],
) -> bool:
    if not isinstance(proof, SuccessorGenerationProof):
        return False
    if (
        proof.status != "verified"
        or not proof.chunk_verification_valid
        or proof.corpus_id != corpus_id
        or not proof.generation_id.strip()
        or not proof.corpus_snapshot_id.strip()
        or not _is_sha256(proof.index_config_fingerprint)
    ):
        return False
    manifest: dict[int, str] = {}
    for article in proof.articles:
        if (
            not isinstance(article.article_id, int)
            or article.article_id <= 0
            or article.article_id in manifest
            or not _is_sha256(article.indexed_content_sha256)
        ):
            return False
        manifest[article.article_id] = article.indexed_content_sha256
    return all(manifest.get(item.article_id) == item.indexed_content_sha256 for item in candidates)


def _verified_generation_articles(proof: SuccessorGenerationProof | None) -> dict[int, str]:
    if proof is None:
        return {}
    return {item.article_id: item.indexed_content_sha256 for item in proof.articles}


def _refresh_article_observations(result: Any) -> tuple[RefreshArticleObservation, ...]:
    observations: list[RefreshArticleObservation] = []
    seen_article_ids: set[int] = set()
    for item in list(getattr(result, "items", ()) or ()):
        article_id = getattr(item, "article_id", None)
        if (
            getattr(item, "status", None) != "accepted"
            or not isinstance(article_id, int)
            or isinstance(article_id, bool)
            or article_id <= 0
            or article_id in seen_article_ids
        ):
            continue
        seen_article_ids.add(article_id)
        content_status = getattr(item, "content_status", None)
        summary_status = getattr(item, "summary_status", None)
        observations.append(
            RefreshArticleObservation(
                article_id=article_id,
                content_sha256=(
                    getattr(item, "content_sha256", None)
                    if _is_sha256(getattr(item, "content_sha256", None))
                    else None
                ),
                content_status=(
                    content_status if content_status in {"pending", "ready", "failed"} else "failed"
                ),
                summary_status=(
                    summary_status if summary_status in {"pending", "ready", "failed"} else "failed"
                ),
                permanent_exclusion=getattr(item, "retryable", None) is False,
            )
        )
    return tuple(observations)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _derive_refresh_counts(
    result: Any,
    *,
    verified_index_articles: dict[int, str],
) -> tuple[RefreshCounts, RefreshOutcomeFacts]:
    items = list(getattr(result, "items", ()) or ())
    total_inputs = _nonnegative_int(getattr(result, "total_inputs", len(items)), len(items))
    accepted_inputs = _nonnegative_int(
        getattr(result, "accepted_inputs", sum(getattr(item, "status", None) == "accepted" for item in items)),
        sum(getattr(item, "status", None) == "accepted" for item in items),
    )
    rejected_inputs = _nonnegative_int(
        getattr(result, "rejected_inputs", sum(getattr(item, "status", None) == "rejected" for item in items)),
        sum(getattr(item, "status", None) == "rejected" for item in items),
    )
    total_inputs = max(total_inputs, accepted_inputs + rejected_inputs)
    collector_status = getattr(result, "collector_status", "succeeded")
    if collector_status not in {"succeeded", "failed"}:
        collector_status = "failed"
    source_outcomes = list(getattr(result, "source_outcomes", ()) or ())
    source_error_details = list(getattr(result, "source_errors", ()) or ())
    declared_source_errors = _nonnegative_int(
        getattr(result, "source_error_count", len(source_error_details)),
        len(source_error_details),
    )
    source_errors = max(
        declared_source_errors,
        len(source_error_details),
        sum(
            int(getattr(outcome, "collector_status", None) == "failed")
            + len(list(getattr(outcome, "batch_errors", ()) or ()))
            for outcome in source_outcomes
        ),
    )
    empty_reason = getattr(result, "empty_reason", None)
    if empty_reason != "no_matching_articles":
        empty_reason = None

    unchanged_ready = content_ready = summary_ready = index_ready = usable_items = 0
    failed_items = retryable_failures = permanent_exclusions = 0
    retryable_content_failures = retryable_index_failures = retryable_summary_failures = 0
    observed_accepted = 0
    for item in items:
        if getattr(item, "status", None) != "accepted":
            continue
        observed_accepted += 1
        article_id = getattr(item, "article_id", None)
        has_ready_content = getattr(item, "content_status", None) == "ready"
        content_sha256 = getattr(item, "content_sha256", None)
        has_verified_index = (
            isinstance(article_id, int)
            and _is_sha256(content_sha256)
            and verified_index_articles.get(article_id) == content_sha256
        )
        has_ready_summary = getattr(item, "summary_status", None) == "ready"
        if has_ready_content:
            content_ready += 1
        if has_ready_summary:
            summary_ready += 1
        if has_verified_index:
            index_ready += 1
        is_usable = has_ready_content and has_verified_index
        if is_usable:
            usable_items += 1
            if bool(getattr(item, "unchanged", False)):
                unchanged_ready += 1

        required_failure = not has_ready_content or not has_verified_index
        summary_failure = not has_ready_summary
        if required_failure or summary_failure:
            failed_items += 1
            # Missing/unknown evidence is retryable fail-closed.  Only an
            # explicit False can establish a permanent accepted exclusion.
            if getattr(item, "retryable", None) is False:
                permanent_exclusions += 1
            else:
                retryable_failures += 1
                retryable_content_failures += int(not has_ready_content)
                retryable_index_failures += int(not has_verified_index)
                retryable_summary_failures += int(not has_ready_summary)

    # A malformed pipeline result is never silently treated as successful.
    unrepresented_accepted = max(accepted_inputs - observed_accepted, 0)
    failed_items += unrepresented_accepted
    retryable_failures += unrepresented_accepted
    retryable_content_failures += unrepresented_accepted
    retryable_index_failures += unrepresented_accepted
    retryable_summary_failures += unrepresented_accepted

    # Rejected invalid inputs are permanent exclusions, but are separately
    # counted from accepted-item exclusions to preserve the ledger invariant.
    counts = RefreshCounts(
        total_inputs=total_inputs,
        accepted_inputs=accepted_inputs,
        rejected_inputs=rejected_inputs,
        rejected_permanent_exclusions=rejected_inputs,
        unchanged_ready=unchanged_ready,
        content_ready=content_ready,
        summary_ready=summary_ready,
        index_ready=index_ready,
        usable_items=usable_items,
        failed_items=failed_items,
        permanent_exclusions=permanent_exclusions,
        retryable_failures=retryable_failures,
        source_errors=source_errors,
    )
    facts = RefreshOutcomeFacts(
        collector_status=collector_status,
        total_inputs=counts.total_inputs,
        accepted_inputs=counts.accepted_inputs,
        rejected_inputs=counts.rejected_inputs,
        rejected_permanent_exclusions=counts.rejected_permanent_exclusions,
        usable_items=counts.usable_items,
        content_failures=retryable_content_failures,
        index_failures=retryable_index_failures,
        summary_failures=retryable_summary_failures,
        permanent_exclusions=counts.permanent_exclusions,
        retryable_failures=counts.retryable_failures,
        source_errors=counts.source_errors,
        empty_reason=empty_reason,
    )
    return counts, facts


def _record_collection_outcomes(
    result: Any,
    *,
    ledger_path: str,
    run_id: str,
    worker_id: str,
) -> bool:
    for outcome in list(getattr(result, "source_outcomes", ()) or ()):
        raw_inputs = list(getattr(outcome, "raw_inputs", ()) or ())
        source_name = getattr(outcome, "source_name", "")
        recorded_at = utc_now()
        if not record_collection_attempt(
            ledger_path,
            run_id=run_id,
            source_name=source_name,
            collector_status=getattr(outcome, "collector_status", ""),
            source_row_count=getattr(outcome, "source_row_count", -1),
            accepted_input_count=len(raw_inputs),
            rejected_source_row_count=getattr(outcome, "rejected_source_row_count", -1),
            empty_reason=getattr(outcome, "empty_reason", None),
            failure_code=getattr(outcome, "failure_code", None),
            retryable=bool(getattr(outcome, "retryable", False)),
            recorded_at=recorded_at,
            worker_id=worker_id,
            next_retry_at=_collection_next_retry_at(outcome, recorded_at),
        ):
            return False
        for batch_error in list(getattr(outcome, "batch_errors", ()) or ()):
            batch_index = getattr(batch_error, "source_batch_index", None)
            if not isinstance(batch_index, int) or isinstance(batch_index, bool) or batch_index < 0:
                raise ValueError("Collection batch errors require a non-negative batch index")
            recorded_at = utc_now()
            provider_request = getattr(batch_error, "provider_request", None)
            provider_request_mapping = (
                provider_request.to_mapping()
                if callable(getattr(provider_request, "to_mapping", None))
                else None
            )
            if not record_collection_attempt(
                ledger_path,
                run_id=run_id,
                source_name=f"{source_name}#batch:{batch_index}",
                collector_status="failed",
                source_row_count=0,
                accepted_input_count=0,
                rejected_source_row_count=0,
                empty_reason=None,
                failure_code=getattr(batch_error, "failure_code", None),
                retryable=bool(getattr(batch_error, "retryable", False)),
                recorded_at=recorded_at,
                worker_id=worker_id,
                next_retry_at=_collection_next_retry_at(batch_error, recorded_at),
                provider_request=provider_request_mapping,
            ):
                return False
    return True


def _collection_next_retry_at(value: Any, recorded_at: Any) -> Any | None:
    retry_after_at = getattr(value, "retry_after_at", None)
    if (
        isinstance(retry_after_at, type(recorded_at))
        and retry_after_at.tzinfo is not None
        and retry_after_at.utcoffset() is not None
    ):
        return max(retry_after_at, recorded_at + timedelta(microseconds=1))
    retry_after_seconds = getattr(value, "retry_after_seconds", None)
    if (
        isinstance(retry_after_seconds, int)
        and not isinstance(retry_after_seconds, bool)
        and retry_after_seconds > 0
    ):
        return recorded_at + timedelta(seconds=retry_after_seconds)
    return None


def _nonnegative_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def _derive_refresh_failure_code(
    *,
    status: str,
    generation_failure_code: str | None,
    result: Any,
) -> str | None:
    """Return one stable public code for an unsuccessful root refresh."""
    if status not in {"failed", "partial"}:
        return None
    if generation_failure_code:
        return generation_failure_code
    collection_codes: set[str] = set()
    source_outcomes = list(getattr(result, "source_outcomes", ()) or ())
    source_outcomes.extend(list(getattr(result, "source_errors", ()) or ()))
    for outcome in source_outcomes:
        if getattr(outcome, "collector_status", None) == "failed":
            failure_code = getattr(outcome, "failure_code", None)
            if isinstance(failure_code, str) and failure_code:
                collection_codes.add(failure_code)
        for batch_error in list(getattr(outcome, "batch_errors", ()) or ()):
            failure_code = getattr(batch_error, "failure_code", None)
            if isinstance(failure_code, str) and failure_code:
                collection_codes.add(failure_code)
    if len(collection_codes) == 1:
        return next(iter(collection_codes))
    if collection_codes:
        return "collection_failed"
    item_codes, has_unsafe_item_failure = _item_failure_facts(result)
    if len(item_codes) == 1 and not has_unsafe_item_failure:
        return next(iter(item_codes))
    if item_codes or has_unsafe_item_failure:
        return "processing_failed"
    return "refresh_incomplete"


def _item_failure_facts(result: Any) -> tuple[set[str], bool]:
    """Classify per-item failure facts without exposing untrusted failure text."""
    safe_codes: set[str] = set()
    has_unsafe_failure = False
    for item in list(getattr(result, "items", ()) or ()):
        if getattr(item, "status", None) not in {"accepted", "rejected"}:
            continue
        failure_reason = getattr(item, "failure_reason", None)
        if not isinstance(failure_reason, str) or not failure_reason.strip():
            continue
        if failure_reason in _SAFE_ITEM_FAILURE_CODES:
            safe_codes.add(failure_reason)
        else:
            has_unsafe_failure = True
    return safe_codes, has_unsafe_failure


def _refresh_payload(
    *,
    status: str,
    refresh_date: str,
    scope_key: str,
    run_id: str,
    attempt_no: int,
    counts: RefreshCounts,
    message: str | None = None,
    failure_code: str | None = None,
    collected_events: int = 0,
    total_articles: int = 0,
    answer_text: str | None = None,
    stats: dict[str, Any] | None = None,
    generation_proof: SuccessorGenerationProof | None = None,
    retry_after: Any | None = None,
    disposition: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "refresh_date": refresh_date,
        "scope_key": scope_key,
        "run_id": run_id,
        "attempt_no": attempt_no,
        "counts": {
            "total_inputs": counts.total_inputs,
            "accepted_inputs": counts.accepted_inputs,
            "rejected_inputs": counts.rejected_inputs,
            "rejected_permanent_exclusions": counts.rejected_permanent_exclusions,
            "unchanged_ready": counts.unchanged_ready,
            "content_ready": counts.content_ready,
            "summary_ready": counts.summary_ready,
            "index_ready": counts.index_ready,
            "usable_items": counts.usable_items,
            "failed_items": counts.failed_items,
            "permanent_exclusions": counts.permanent_exclusions,
            "retryable_failures": counts.retryable_failures,
            "source_errors": counts.source_errors,
        },
        "message": message,
        "failure_code": failure_code,
        "disposition": disposition,
        "retry_after": (
            retry_after.isoformat() if hasattr(retry_after, "isoformat") else retry_after
        ),
        "collected_events": collected_events,
        "total_articles": total_articles,
        "answer_text": answer_text,
        "stats": dict(stats or {}),
        "generation_proof": (
            {
                "corpus_id": generation_proof.corpus_id,
                "generation_id": generation_proof.generation_id,
                "corpus_snapshot_id": generation_proof.corpus_snapshot_id,
                "index_config_fingerprint": generation_proof.index_config_fingerprint,
                "status": generation_proof.status,
            }
            if generation_proof is not None
            else None
        ),
    }


def run_watchlist_triage_workflow(
    request,
    storage: SQLiteNewsStore,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
    config: WatchlistResearchConfig | None = None,
    triage_agent=None,
    reviewer_agent=None,
    reranking_agent=None,
    structuring_agent=None,
    company_kb_provider=None,
    execute_batch_fn=None,
    progress_sink: WatchlistProgressSink | None = None,
    timeline_recorder: WatchlistTimelineRecorder | None = None,
    write_report_fn: Callable[..., str] | None = None,
    persist_timeline: bool | None = None,
    debug_review: bool = False,
    debug_rerank: bool = False,
    retrieval_provenance: dict[str, object] | None = None,
) -> WatchlistWorkflowResult:
    from event_collector.publication_provenance import require_publication_provenance

    proof = require_publication_provenance(retrieval_provenance)
    recorder = timeline_recorder or WatchlistTimelineRecorder(sink=progress_sink)
    effective_progress_sink = recorder.emit
    research_kwargs = {
        "config": config,
        "triage_agent": triage_agent,
        "reviewer_agent": reviewer_agent,
        "reranking_agent": reranking_agent,
        "structuring_agent": structuring_agent,
        "company_kb_provider": company_kb_provider,
        "progress_sink": effective_progress_sink,
    }
    if execute_batch_fn is not None:
        research_kwargs["execute_batch_fn"] = execute_batch_fn
    research_run = run_watchlist_research(
        request,
        storage,
        vector_store_provider,
        **research_kwargs,
    )
    result = research_run.result
    report_path = persist_watchlist_report(
        result,
        storage=storage,
        output_dir=output_dir,
        progress_sink=effective_progress_sink,
        write_report_fn=write_report_fn,
        debug_review=debug_review,
        debug_rerank=debug_rerank,
        retrieval_provenance=proof,
    )
    should_persist_timeline = bool(timeline_recorder is not None) if persist_timeline is None else persist_timeline
    timeline_path = None
    if should_persist_timeline:
        timeline_path = write_watchlist_timeline(
            result.run_id,
            recorder,
            output_dir=output_dir,
            timeline_suffix=timeline_suffix,
            retrieval_provenance=proof,
        )
    timing_summary = build_watchlist_timing_summary(recorder.events)
    return WatchlistWorkflowResult(
        result=result,
        batching=research_run.batching,
        report_path=report_path,
        timeline_path=timeline_path,
        timing_summary=timing_summary,
        timing_text=render_watchlist_timing_text(timing_summary),
    )


def run_refresh_then_watchlist_workflow(
    request,
    storage: SQLiteNewsStore,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    refresh_request: RefreshNewsRequest,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
    config: WatchlistResearchConfig | None = None,
    progress_sink: WatchlistProgressSink | None = None,
    timeline_recorder: WatchlistTimelineRecorder | None = None,
    successor_generation_coordinator: SuccessorGenerationCoordinator | None = None,
    retrieval_provenance: dict[str, object] | None = None,
    retrieval_provenance_provider: Callable[[], dict[str, object]] | None = None,
) -> WatchlistWorkflowResult:
    from event_collector.publication_provenance import require_publication_provenance

    if retrieval_provenance is None and retrieval_provenance_provider is None:
        require_publication_provenance(None)
    proof = (
        require_publication_provenance(retrieval_provenance)
        if retrieval_provenance is not None
        else None
    )
    recorder = timeline_recorder or WatchlistTimelineRecorder(sink=progress_sink)
    refresh_kwargs: dict[str, Any] = {"progress_sink": recorder.emit}
    if successor_generation_coordinator is not None:
        refresh_kwargs["successor_generation_coordinator"] = successor_generation_coordinator
    refresh = refresh_news_corpus(refresh_request, **refresh_kwargs)
    refresh_status = refresh.get("status")
    usable_items = int((refresh.get("counts") or {}).get("usable_items", 0))
    if refresh_status not in {"completed", "already_completed", "partial"} or (
        refresh_status == "partial" and usable_items <= 0
    ):
        raise RefreshWorkflowBlockedError(refresh)
    if retrieval_provenance_provider is not None:
        proof = require_publication_provenance(retrieval_provenance_provider())
    workflow = run_watchlist_triage_workflow(
        request,
        storage,
        vector_store_provider,
        output_dir=output_dir,
        timeline_suffix=timeline_suffix,
        config=config,
        progress_sink=recorder.emit,
        timeline_recorder=recorder,
        persist_timeline=True,
        retrieval_provenance=proof,
    )
    return WatchlistWorkflowResult(
        result=workflow.result,
        batching=workflow.batching,
        report_path=workflow.report_path,
        timeline_path=workflow.timeline_path,
        timing_summary=workflow.timing_summary,
        timing_text=workflow.timing_text,
        refresh=refresh,
    )


def persist_watchlist_report(
    result,
    *,
    storage: SQLiteNewsStore,
    output_dir: str = DEFAULT_REPORTS_DIR,
    progress_sink: WatchlistProgressSink | None = None,
    write_report_fn: Callable[..., str] | None = None,
    debug_review: bool = False,
    debug_rerank: bool = False,
    retrieval_provenance: dict[str, object] | None = None,
) -> str:
    from event_collector.publication_provenance import require_publication_provenance

    proof = require_publication_provenance(retrieval_provenance)
    started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="started",
            started_at=started_at,
            message="write_watchlist_report",
        ),
    )
    report_path = (write_report_fn or write_watchlist_report)(
        result,
        output_dir=output_dir,
        debug_review=debug_review,
        debug_rerank=debug_rerank,
        retrieval_provenance=proof,
    )
    storage.save_watchlist_report_path(result.run_id, report_path)
    finished_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="finished",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
            message="write_watchlist_report",
            metrics={"report_path": report_path},
        ),
    )
    return report_path


def write_watchlist_report(
    result,
    output_dir: str = DEFAULT_REPORTS_DIR,
    debug_review: bool = False,
    debug_rerank: bool = False,
    retrieval_provenance: dict[str, object] | None = None,
) -> str:
    from event_collector.publication_provenance import require_publication_provenance

    proof = require_publication_provenance(retrieval_provenance)
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{result.run_at.strftime('%Y-%m-%d_%H%M%S')}_watchlist.md"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            render_watchlist_report(
                result,
                debug_review=debug_review,
                debug_rerank=debug_rerank,
                retrieval_provenance=proof,
            )
        )
    return path


def write_watchlist_timeline(
    run_id: str,
    recorder: WatchlistTimelineRecorder,
    *,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
    retrieval_provenance: dict[str, object] | None = None,
) -> str:
    from event_collector.publication_provenance import require_publication_provenance

    proof = require_publication_provenance(retrieval_provenance)
    timeline_path = os.path.join(output_dir, f"{run_id}{timeline_suffix}")
    return recorder.write_jsonl(
        timeline_path,
        header={"record_type": "retrieval_provenance", **proof},
    )


def read_watchlist_report_artifact(
    run_id: str,
    *,
    storage: SQLiteNewsStore,
    reports_dir: str = DEFAULT_REPORTS_DIR,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "found": False,
        "report_path": None,
        "content": None,
        "error_code": None,
        "error_message": None,
    }
    report_path = storage.fetch_watchlist_report_path(run_id)
    if not report_path:
        payload["error_code"] = "report_not_found"
        payload["error_message"] = f"No watchlist report path was found for run_id={run_id}"
        return payload
    resolved_report_path = _resolve_path(report_path)
    allowed_reports_root = Path(reports_dir).resolve()
    if not _is_path_within_root(resolved_report_path, allowed_reports_root):
        payload["error_code"] = "report_not_found"
        payload["error_message"] = f"Stored report path is outside the watchlist reports directory for run_id={run_id}"
        return payload
    payload["report_path"] = report_path
    if not resolved_report_path.exists():
        payload["error_code"] = "report_file_missing"
        payload["error_message"] = f"Saved watchlist report file is missing for run_id={run_id}"
        return payload
    payload["found"] = True
    payload["content"] = resolved_report_path.read_text(encoding="utf-8")
    return payload


def read_watchlist_timeline_artifact(
    run_id: str,
    *,
    reports_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "found": False,
        "timeline_path": None,
        "retrieval_provenance": None,
        "events": [],
        "timing_summary": None,
        "timing_text": None,
        "error_code": None,
        "error_message": None,
    }
    resolved_timeline_path = _resolve_path(os.path.join(reports_dir, f"{run_id}{timeline_suffix}"))
    allowed_reports_root = Path(reports_dir).resolve()
    if not _is_path_within_root(resolved_timeline_path, allowed_reports_root):
        payload["error_code"] = "timeline_not_found"
        payload["error_message"] = f"Timeline path is outside the watchlist reports directory for run_id={run_id}"
        return payload
    if not resolved_timeline_path.exists():
        payload["error_code"] = "timeline_not_found"
        payload["error_message"] = f"No watchlist timeline file was found for run_id={run_id}"
        return payload
    try:
        provenance, events = load_watchlist_timeline_document(str(resolved_timeline_path))
    except TimelineProvenanceError:
        payload["error_code"] = "timeline_provenance_invalid"
        payload["error_message"] = (
            f"Watchlist timeline provenance is invalid for run_id={run_id}"
        )
        return payload
    timing_summary = build_watchlist_timing_summary(events)
    payload["found"] = True
    payload["timeline_path"] = str(resolved_timeline_path)
    payload["retrieval_provenance"] = provenance
    payload["events"] = [event.to_dict() for event in events]
    payload["timing_summary"] = timing_summary
    payload["timing_text"] = render_watchlist_timing_text(timing_summary)
    return payload


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (Path.cwd() / candidate).resolve()


def _is_path_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _emit_progress(progress_sink: WatchlistProgressSink | None, event: WatchlistProgressEvent) -> None:
    if progress_sink is not None:
        progress_sink(event)
