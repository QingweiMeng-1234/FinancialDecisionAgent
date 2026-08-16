"""Deeper ingestion seam for canonical article outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import sqlite3
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from event_collector.article_content import (
    CONTENT_VALIDATOR_VERSION,
    ArticleContentFetcher,
    ArticleFetchError,
    FetchFailureReason,
    validate_title_content_alignment,
)
from event_collector.event_collection import (
    CollectionResult,
    CollectionSourceOutcome,
    Event,
    EventSource,
    EventSourceCollector,
    ManualCollector,
    NewsCollector,
    RawEventInput,
    collect_collection_result,
    resolve_source_publication_metadata,
)
from event_collector.news_storage import ArticleRecord, SQLiteNewsStore, compute_content_sha256
from event_collector.refresh_lease_watchdog import LeaseHeartbeatFailed
from event_collector.summarization import ArticleForSummarization, SummarizationAgent
from event_collector.vector_store import ChromaVectorStore, VectorStore


@dataclass
class NewsIngestionRequest:
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"
    include_manual: bool = False
    news_endpoint: str = "everything"
    news_days_back: int = 7
    news_page: int = 1
    news_sort_by: str = "publishedAt"
    news_page_size: int = 100
    show_progress: bool = True


@dataclass(frozen=True)
class CanonicalArticleOutcome:
    input_index: int
    source: str
    title: str
    original_url: str | None
    article_id: int | None
    status: str
    created: bool | None
    content_status: str | None
    summary_status: str | None
    index_status: str | None
    canonical_url: str | None
    final_url: str | None
    failure_reason: str | None = None
    story_group_id: int | None = None
    unchanged: bool = False
    retryable: bool | None = None
    next_retry_at: datetime | None = None
    content_sha256: str | None = None


@dataclass(frozen=True)
class NewsIngestionResult:
    collected_events: int
    stats: dict[str, int]
    total_articles: int
    items: list[CanonicalArticleOutcome] = field(default_factory=list)
    total_inputs: int = 0
    accepted_inputs: int = 0
    rejected_inputs: int = 0
    collector_status: str = "succeeded"
    source_outcomes: list[CollectionSourceOutcome] = field(default_factory=list)
    source_errors: list[CollectionSourceOutcome] = field(default_factory=list)
    empty_reason: str | None = None

    @property
    def source_error_count(self) -> int:
        """Number of failed configured collectors, with their details in ``source_errors``."""
        return len(self.source_errors)


class ProcessingAttemptRecorder(Protocol):
    """Opaque append-only stage-attempt boundary for a refresh execution."""

    def begin_attempt(
        self,
        *,
        article_id: int,
        stage: str,
        input_content_sha256: str | None,
    ) -> object:
        """Start one stage attempt before its external operation."""

    def finish_attempt(
        self,
        attempt: object,
        *,
        status: str,
        failure_code: str | None = None,
        retryable: bool = False,
        next_retry_at: datetime | None = None,
    ) -> bool:
        """CAS an attempt to a terminal state and report whether it succeeded."""


RetrySchedulePolicy = Callable[[ArticleFetchError], datetime | None]


def run_news_ingestion(
    request: NewsIngestionRequest,
    *,
    storage: SQLiteNewsStore | None = None,
    vector_store: ChromaVectorStore | None = None,
    collectors: list[EventSourceCollector] | None = None,
    summarizer: SummarizationAgent | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
    attempt_recorder: ProcessingAttemptRecorder | None = None,
    retry_schedule_policy: RetrySchedulePolicy | None = None,
) -> NewsIngestionResult:
    """Run one collect -> ingest pass behind a workflow shell."""
    owns_storage = storage is None
    runtime_storage = storage or SQLiteNewsStore(db_path=request.db_path)
    runtime_storage.init_db()
    # Canonical ingestion and immutable generation construction are separate
    # side-effect boundaries.  A legacy mutable vector store must be injected
    # explicitly; the production default only updates canonical article state.
    runtime_vector_store = vector_store
    runtime_collectors = collectors or _build_default_collectors(request)

    try:
        collection_result = collect_collection_result(runtime_collectors)
        return ingest_raw_inputs(
            collection_result.raw_inputs,
            runtime_storage,
            runtime_vector_store,
            summarizer=summarizer,
            content_fetcher=content_fetcher,
            attempt_recorder=attempt_recorder,
            retry_schedule_policy=retry_schedule_policy,
            show_progress=request.show_progress,
            collection_result=collection_result,
        )
    finally:
        if owns_storage:
            runtime_storage.close()


def collect_raw_inputs_from_sources(collectors: list[EventSourceCollector]) -> list[RawEventInput]:
    raw_inputs: list[RawEventInput] = []
    for collector in collectors:
        raw_inputs.extend(collector.collect())
    return raw_inputs


def ingest_raw_inputs(
    raw_inputs: list[RawEventInput],
    storage: SQLiteNewsStore,
    vector_store: VectorStore | None = None,
    *,
    summarizer: SummarizationAgent | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
    attempt_recorder: ProcessingAttemptRecorder | None = None,
    retry_schedule_policy: RetrySchedulePolicy | None = None,
    show_progress: bool = False,
    collection_result: CollectionResult | None = None,
    lease_guard: Callable[[], None] | None = None,
) -> NewsIngestionResult:
    if not storage.conn:
        storage.init_db()
    summary_agent = summarizer
    fetcher = content_fetcher or ArticleContentFetcher()
    items: list[CanonicalArticleOutcome] = []

    progress = _build_progress(
        list(enumerate(raw_inputs)),
        enabled=show_progress,
        desc="Ingesting articles",
        unit="article",
    )

    for input_index, raw_input in progress:
        failure_reason = validate_raw_input(raw_input)
        if failure_reason is not None:
            items.append(
                CanonicalArticleOutcome(
                    input_index=input_index,
                    source=raw_input.source,
                    title=_resolve_title(raw_input),
                    original_url=raw_input.url or None,
                    article_id=None,
                    status="rejected",
                    created=None,
                    content_status=None,
                    summary_status=None,
                    index_status=None,
                    canonical_url=None,
                    final_url=None,
                    failure_reason=failure_reason,
                    retryable=False,
                )
            )
            _update_progress(progress, rejected=sum(1 for item in items if item.status == "rejected"))
            continue

        article_id, created = _create_or_reuse_reference(input_index, raw_input, storage)
        canonical_url: str | None = None
        final_url = raw_input.url or _build_internal_url(input_index, raw_input.source)
        content_status = "pending"
        summary_status = "pending"
        index_status = "pending"
        stage_failure = None
        unchanged = False
        story_group_id: int | None = None
        content_attempt: object | None = None

        try:
            if raw_input.source == "news" and raw_input.url:
                content_attempt = _begin_attempt(
                    attempt_recorder,
                    article_id=article_id,
                    stage="content_fetch",
                    input_content_sha256=None,
                )
                fetch_result = fetcher.fetch(raw_input.url)
                if lease_guard is not None:
                    lease_guard()
                canonical_url = fetch_result.canonical_url
                content = fetch_result.content
                final_url = fetch_result.original_url
                validate_title_content_alignment(
                    _resolve_title(raw_input),
                    _resolve_description(raw_input),
                    content,
                    url=final_url,
                )
                article_id, identity_reused = storage.reconcile_article_identity(article_id, canonical_url)
                if identity_reused:
                    created = False
                storage.register_url_alias(
                    article_id,
                    final_url,
                    alias_kind="redirect",
                    publisher_source_id=raw_input.publisher_source_id,
                    publisher_source_name=raw_input.publisher_source_name,
                )
            else:
                content = raw_input.raw_text

            content_sha256 = compute_content_sha256(content)
            source_published_at, published_at_provenance = resolve_source_publication_metadata(
                raw_input
            )
            content_changed = storage.update_article_content(
                article_id,
                content=content,
                title=_resolve_title(raw_input),
                description=_resolve_description(raw_input),
                original_url=None if raw_input.source == "news" else final_url,
                canonical_url=canonical_url,
                published_at=raw_input.published_at or datetime.now(),
                source_published_at=source_published_at,
                published_at_provenance=published_at_provenance,
                validation_status="verified" if raw_input.source == "news" else "not_applicable",
                validator_version=CONTENT_VALIDATOR_VERSION if raw_input.source == "news" else None,
                response_status_code=getattr(fetch_result, "status_code", None)
                if raw_input.source == "news" and raw_input.url
                else None,
                response_content_type=getattr(fetch_result, "content_type", None)
                if raw_input.source == "news" and raw_input.url
                else None,
                extractor_version=getattr(fetch_result, "extractor_version", None)
                if raw_input.source == "news" and raw_input.url
                else None,
                final_response_url=final_url if raw_input.source == "news" else None,
            )
            unchanged = not content_changed
            if raw_input.source == "news":
                story_group_id, _ = storage.assign_story_group(
                    article_id,
                    title=_resolve_title(raw_input),
                    published_at=raw_input.published_at or datetime.now(),
                    content_sha256=content_sha256,
                )
            content_status = "ready"
            article = storage.get_article(article_id)
            assert article is not None
            _finish_attempt(attempt_recorder, content_attempt, status="succeeded")
        except LeaseHeartbeatFailed:
            raise
        except ArticleFetchError as exc:
            retryable = bool(exc.retryable)
            next_retry_at = (
                _resolve_next_retry_at(exc, retry_schedule_policy) if retryable else None
            )
            storage.mark_article_content_failure(
                article_id,
                reason=exc.reason.value,
                validator_version=CONTENT_VALIDATOR_VERSION,
                final_response_url=exc.url,
                response_status_code=exc.status_code,
            )
            _finish_fetch_failure_attempt(
                attempt_recorder,
                content_attempt,
                error=exc,
                retryable=retryable,
                next_retry_at=next_retry_at,
            )
            record = storage.get_article_record(article_id)
            items.append(
                _build_outcome(
                    input_index,
                    raw_input,
                    record,
                    article_id=article_id,
                    status="accepted",
                    created=created,
                    failure_reason=exc.reason.value,
                    content_status_override="failed",
                    retryable=retryable,
                    next_retry_at=next_retry_at,
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue
        except sqlite3.IntegrityError:
            storage.mark_article_content_failure(
                article_id,
                reason=FetchFailureReason.DUPLICATE_URL_CONFLICT.value,
                validator_version=CONTENT_VALIDATOR_VERSION,
                final_response_url=final_url,
            )
            _finish_failed_attempt(
                attempt_recorder,
                content_attempt,
                failure_code=FetchFailureReason.DUPLICATE_URL_CONFLICT.value,
            )
            record = storage.get_article_record(article_id)
            items.append(
                _build_outcome(
                    input_index,
                    raw_input,
                    record,
                    article_id=article_id,
                    status="accepted",
                    created=created,
                    failure_reason=FetchFailureReason.DUPLICATE_URL_CONFLICT.value,
                    content_status_override="failed",
                    retryable=False,
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue
        except Exception:
            storage.mark_article_content_failure(
                article_id,
                reason="content_fetch_failed",
                validator_version=CONTENT_VALIDATOR_VERSION if raw_input.source == "news" else None,
                final_response_url=final_url,
            )
            _finish_failed_attempt(
                attempt_recorder,
                content_attempt,
                failure_code="content_fetch_failed",
                retryable=True,
            )
            record = storage.get_article_record(article_id)
            items.append(
                _build_outcome(
                    input_index,
                    raw_input,
                    record,
                    article_id=article_id,
                    status="accepted",
                    created=created,
                    failure_reason="content_fetch_failed",
                    content_status_override="failed",
                    retryable=True,
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue

        processing_hash = article.active_content_sha256 or article.content_sha256
        record = storage.get_article_record(article_id)
        summary_ready = _derivation_is_ready(record, "summary", processing_hash)
        index_ready = vector_store is None or _derivation_is_ready(record, "index", processing_hash)

        if unchanged and summary_ready and index_ready:
            record = storage.get_article_record(article_id)
            items.append(
                _build_outcome(
                    input_index,
                    raw_input,
                    record,
                    article_id=article_id,
                    status="accepted",
                    created=created,
                    failure_reason=None,
                    content_status_override="ready",
                    canonical_url_override=canonical_url,
                    final_url_override=final_url,
                    story_group_id_override=story_group_id,
                    unchanged=True,
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue

        summary_status = record.summary_status if record else "pending"
        index_status = record.index_status if record else "pending"

        if not summary_ready:
            if summary_agent is None:
                summary_agent = SummarizationAgent()
            summary_attempt: object | None = None
            try:
                summary_attempt = _begin_attempt(
                    attempt_recorder,
                    article_id=article_id,
                    stage="summary",
                    input_content_sha256=processing_hash,
                )
                summary = summary_agent.summarize_article(
                    ArticleForSummarization(
                        article_id=article_id,
                        title=article.title,
                        description=article.description,
                        content=article.content,
                        url=article.url,
                    )
                )
                if lease_guard is not None:
                    lease_guard()
                if not storage.update_article_summary(
                    article_id,
                    summary,
                    content_sha256=processing_hash,
                ):
                    raise RuntimeError("Active content changed before summary commit")
                _finish_attempt(attempt_recorder, summary_attempt, status="succeeded")
                summary_status = "ready"
                article.summary = summary
            except LeaseHeartbeatFailed:
                raise
            except Exception:
                if processing_hash:
                    storage.mark_article_summary_failure(article_id, processing_hash)
                _finish_failed_attempt(
                    attempt_recorder,
                    summary_attempt,
                    failure_code="summary_failed",
                    retryable=True,
                )
                summary_status = "failed"
                stage_failure = "summary_failed"

        if vector_store is not None and not index_ready:
            index_attempt: object | None = None
            try:
                index_attempt = _begin_attempt(
                    attempt_recorder,
                    article_id=article_id,
                    stage="index",
                    input_content_sha256=processing_hash,
                )
                vector_store.add_article(article_id, article)
                if not processing_hash or not storage.mark_article_index_ready(article_id, processing_hash):
                    raise RuntimeError("Active content changed before index commit")
                _finish_attempt(attempt_recorder, index_attempt, status="succeeded")
                index_status = "ready"
            except Exception:
                if processing_hash:
                    storage.mark_article_index_failure(article_id, processing_hash)
                _finish_failed_attempt(
                    attempt_recorder,
                    index_attempt,
                    failure_code="index_failed",
                    retryable=True,
                )
                index_status = "failed"
                if stage_failure is None:
                    stage_failure = "index_failed"

        record = storage.get_article_record(article_id)
        items.append(
            _build_outcome(
                input_index,
                raw_input,
                record,
                article_id=article_id,
                status="accepted",
                created=created,
                failure_reason=stage_failure,
                content_status_override=content_status,
                summary_status_override=summary_status,
                index_status_override=index_status,
                canonical_url_override=canonical_url,
                final_url_override=final_url,
                story_group_id_override=story_group_id,
                unchanged=unchanged,
                retryable=True if stage_failure is not None else None,
            )
        )
        _update_progress(progress, **_build_progress_stats(items))

    _close_progress(progress)

    accepted_inputs = sum(1 for item in items if item.status == "accepted")
    rejected_inputs = sum(1 for item in items if item.status == "rejected")
    collection_facts = collection_result or _caller_supplied_collection_result()
    return NewsIngestionResult(
        collected_events=accepted_inputs,
        stats=_build_stats(items),
        total_articles=storage.count_articles(),
        items=items,
        total_inputs=len(raw_inputs),
        accepted_inputs=accepted_inputs,
        rejected_inputs=rejected_inputs,
        collector_status=collection_facts.collector_status,
        source_outcomes=collection_facts.source_outcomes,
        source_errors=collection_facts.source_errors,
        empty_reason=collection_facts.empty_reason,
    )


def ingest_event_batch(
    events: list[Event],
    storage: SQLiteNewsStore,
    vector_store: VectorStore | None = None,
    *,
    summarizer: SummarizationAgent | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
    attempt_recorder: ProcessingAttemptRecorder | None = None,
    retry_schedule_policy: RetrySchedulePolicy | None = None,
    show_progress: bool = False,
) -> NewsIngestionResult:
    raw_inputs = [_raw_input_from_event(event) for event in events]
    return ingest_raw_inputs(
        raw_inputs,
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=content_fetcher,
        attempt_recorder=attempt_recorder,
        retry_schedule_policy=retry_schedule_policy,
        show_progress=show_progress,
    )


def _caller_supplied_collection_result() -> CollectionResult:
    """Describe legacy direct ingestion without fabricating provider collection proof."""
    return CollectionResult(
        collector_status="succeeded",
        raw_inputs=[],
        source_outcomes=[],
        source_errors=[],
        empty_reason=None,
    )


def _begin_attempt(
    recorder: ProcessingAttemptRecorder | None,
    *,
    article_id: int,
    stage: str,
    input_content_sha256: str | None,
) -> object | None:
    if recorder is None:
        return None
    return recorder.begin_attempt(
        article_id=article_id,
        stage=stage,
        input_content_sha256=input_content_sha256,
    )


def _finish_attempt(
    recorder: ProcessingAttemptRecorder | None,
    attempt: object | None,
    *,
    status: str,
    failure_code: str | None = None,
    retryable: bool = False,
    next_retry_at: datetime | None = None,
) -> None:
    if recorder is None or attempt is None:
        return
    if recorder.finish_attempt(
        attempt,
        status=status,
        failure_code=failure_code,
        retryable=retryable,
        next_retry_at=next_retry_at,
    ) is not True:
        raise RuntimeError("Attempt recorder did not confirm the terminal transition")


def _finish_failed_attempt(
    recorder: ProcessingAttemptRecorder | None,
    attempt: object | None,
    *,
    failure_code: str,
    retryable: bool = False,
) -> None:
    try:
        _finish_attempt(
            recorder,
            attempt,
            status="failed",
            failure_code=failure_code,
            retryable=retryable,
        )
    except Exception:
        # The caller has already persisted (or is persisting) its primary
        # stage failure. Never turn a recorder outage into a false success.
        return


def _finish_fetch_failure_attempt(
    recorder: ProcessingAttemptRecorder | None,
    attempt: object | None,
    *,
    error: ArticleFetchError,
    retryable: bool,
    next_retry_at: datetime | None,
) -> None:
    """Persist provider-directed retry intent without running a retry here."""
    status = "retry_scheduled" if retryable and next_retry_at is not None else "failed"
    try:
        _finish_attempt(
            recorder,
            attempt,
            status=status,
            failure_code=error.reason.value,
            retryable=retryable,
            next_retry_at=next_retry_at if status == "retry_scheduled" else None,
        )
    except Exception:
        # Durable primary content failure is already recorded by the caller.
        # A ledger outage cannot turn it into a false successful refresh item.
        return


def _resolve_next_retry_at(
    error: ArticleFetchError,
    retry_schedule_policy: RetrySchedulePolicy | None,
) -> datetime | None:
    """Use only a provider-supplied absolute time or an injected policy.

    This ingestion pass never reads its own wall clock, sleeps, or invokes the
    provider again.  A retryable error without either source remains a failed
    attempt marked retryable; another worker may make a later policy decision.
    """
    candidate = error.retry_after_at
    if candidate is None and retry_schedule_policy is not None:
        candidate = retry_schedule_policy(error)
    if candidate is None:
        return None
    if not isinstance(candidate, datetime):
        raise TypeError("retry scheduling must return a datetime or None")
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValueError("retry scheduling time must be timezone-aware")
    return candidate


def _derivation_is_ready(
    record: ArticleRecord | None,
    stage: str,
    content_sha256: str | None,
) -> bool:
    if record is None or not content_sha256:
        return False
    article = record.article
    if stage == "summary":
        return (
            record.summary_status == "ready"
            and article.summary_content_sha256 == content_sha256
            and bool((article.summary or "").strip())
        )
    if stage == "index":
        return (
            record.index_status == "ready"
            and article.indexed_content_sha256 == content_sha256
        )
    raise ValueError(f"Unsupported derivation stage: {stage!r}")


def validate_raw_input(raw_input: RawEventInput) -> str | None:
    source = (raw_input.source or "").strip()
    raw_text = raw_input.raw_text or ""
    if source not in {"manual", "news", "api"}:
        return "invalid_source"
    if not raw_text and not raw_input.url:
        return "missing_text"
    if source == "news":
        if raw_input.url:
            try:
                parsed = urlparse(raw_input.url.strip())
                hostname = parsed.hostname
                _ = parsed.port
            except ValueError:
                return "invalid_news_url"
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.netloc
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                return "invalid_news_url"
        if not raw_input.url and len(raw_text.strip()) < 50:
            return "short_news_text_without_url"
        return None
    if len(raw_text.strip()) < 50:
        return "short_text"
    return None


def _create_or_reuse_reference(
    input_index: int,
    raw_input: RawEventInput,
    storage: SQLiteNewsStore,
) -> tuple[int, bool]:
    source_published_at, published_at_provenance = resolve_source_publication_metadata(
        raw_input
    )
    return storage.create_or_get_article_reference(
        source=raw_input.source,
        title=_resolve_title(raw_input),
        description=_resolve_description(raw_input),
        original_url=raw_input.url or _build_internal_url(input_index, raw_input.source),
        published_at=raw_input.published_at or datetime.now(),
        source_published_at=source_published_at,
        published_at_provenance=published_at_provenance,
        publisher_source_id=raw_input.publisher_source_id,
        publisher_source_name=raw_input.publisher_source_name,
    )


def _resolve_title(raw_input: RawEventInput) -> str:
    return raw_input.title or (raw_input.raw_text[:100] if raw_input.raw_text else "Untitled")


def _resolve_description(raw_input: RawEventInput) -> str:
    return raw_input.description or raw_input.raw_text[:200]


def _build_internal_url(input_index: int, source: str) -> str:
    return f"internal://{source}/{input_index}"


def _raw_input_from_event(event: Event) -> RawEventInput:
    return RawEventInput(
        source=event.source.value,
        raw_text=event.raw_text,
        title=event.title,
        description=event.description,
        url=event.url,
        published_at=event.source_published_at,
        publisher_source_id=event.publisher_source_id,
        publisher_source_name=event.publisher_source_name,
    )


def _build_outcome(
    input_index: int,
    raw_input: RawEventInput,
    record: ArticleRecord | None,
    *,
    article_id: int,
    status: str,
    created: bool,
    failure_reason: str | None,
    content_status_override: str | None = None,
    summary_status_override: str | None = None,
    index_status_override: str | None = None,
    canonical_url_override: str | None = None,
    final_url_override: str | None = None,
    story_group_id_override: int | None = None,
    unchanged: bool = False,
    retryable: bool | None = None,
    next_retry_at: datetime | None = None,
) -> CanonicalArticleOutcome:
    article = record.article if record is not None else None
    return CanonicalArticleOutcome(
        input_index=input_index,
        source=raw_input.source,
        title=_resolve_title(raw_input),
        original_url=raw_input.url or None,
        article_id=article_id,
        status=status,
        created=created,
        content_status=content_status_override or (record.content_status if record is not None else None),
        summary_status=summary_status_override or (record.summary_status if record is not None else None),
        index_status=index_status_override or (record.index_status if record is not None else None),
        canonical_url=canonical_url_override or (article.canonical_url if article is not None else None),
        final_url=final_url_override or (article.url if article is not None else None),
        failure_reason=failure_reason,
        story_group_id=story_group_id_override or (article.story_group_id if article is not None else None),
        unchanged=unchanged,
        retryable=retryable,
        next_retry_at=next_retry_at,
        content_sha256=(
            article.active_content_sha256 or article.content_sha256
            if article is not None
            else None
        ),
    )


def _build_stats(items: list[CanonicalArticleOutcome]) -> dict[str, int]:
    accepted = [item for item in items if item.status == "accepted"]
    return {
        "total_events": len(accepted),
        "saved": sum(1 for item in accepted if item.created is True),
        "updated": sum(1 for item in accepted if item.created is False),
        "unchanged": sum(1 for item in accepted if item.unchanged),
        "summarized": sum(1 for item in accepted if item.summary_status == "ready"),
        "indexed": sum(1 for item in accepted if item.index_status == "ready"),
        "skipped": 0,
        "content_failed": sum(1 for item in accepted if item.content_status == "failed"),
        "summary_failed": sum(1 for item in accepted if item.summary_status == "failed"),
        "index_failed": sum(1 for item in accepted if item.index_status == "failed"),
        "rejected": sum(1 for item in items if item.status == "rejected"),
    }


def _build_progress_stats(items: list[CanonicalArticleOutcome]) -> dict[str, int]:
    stats = _build_stats(items)
    return {
        "saved": stats["saved"],
        "updated": stats["updated"],
        "fetch_fail": stats["content_failed"],
        "summary_fail": stats["summary_failed"],
        "index_fail": stats["index_failed"],
        "rejected": stats["rejected"],
    }


def _build_default_collectors(request: NewsIngestionRequest) -> list[EventSourceCollector]:
    collectors: list[EventSourceCollector] = [
        NewsCollector(
            page_size=request.news_page_size,
            endpoint=request.news_endpoint,
            days_back=request.news_days_back,
            page=request.news_page,
            sort_by=request.news_sort_by,
        )
    ]
    if request.include_manual:
        collectors.insert(0, ManualCollector())
    return collectors


def _build_progress(items: list[Any], *, enabled: bool, desc: str, unit: str):
    if not enabled:
        return items
    try:
        from tqdm import tqdm
    except Exception:  # pragma: no cover - tqdm is optional in tests
        return items
    return tqdm(items, total=len(items), desc=desc, unit=unit)


def _update_progress(progress, **postfix: int) -> None:
    if hasattr(progress, "set_postfix"):
        progress.set_postfix(postfix)


def _close_progress(progress) -> None:
    if hasattr(progress, "close"):
        progress.close()
