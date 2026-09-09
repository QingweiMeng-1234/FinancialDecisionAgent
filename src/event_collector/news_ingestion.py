"""Deeper ingestion seam for canonical article outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from event_collector.article_processing import process_article
from event_collector.article_content import (
    CONTENT_VALIDATOR_VERSION,
    ArticleContentFetcher,
    ArticleFetchError,
    FetchFailureReason,
    validate_title_content_alignment,
)
from event_collector.event_collection import Event, EventSource, EventSourceCollector, ManualCollector, NewsCollector, RawEventInput
from event_collector.news_storage import ArticleRecord, SQLiteNewsStore, compute_content_sha256
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
    unchanged: bool = False


@dataclass(frozen=True)
class NewsIngestionResult:
    collected_events: int
    stats: dict[str, int]
    total_articles: int
    items: list[CanonicalArticleOutcome] = field(default_factory=list)
    total_inputs: int = 0
    accepted_inputs: int = 0
    rejected_inputs: int = 0


def run_news_ingestion(
    request: NewsIngestionRequest,
    *,
    storage: SQLiteNewsStore | None = None,
    vector_store: ChromaVectorStore | None = None,
    collectors: list[EventSourceCollector] | None = None,
    summarizer: SummarizationAgent | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
) -> NewsIngestionResult:
    """Run one collect -> ingest pass behind a workflow shell."""
    owns_storage = storage is None
    runtime_storage = storage or SQLiteNewsStore(db_path=request.db_path)
    runtime_storage.init_db()
    runtime_vector_store = vector_store or ChromaVectorStore(
        persist_dir=request.persist_dir,
        collection_name=request.collection_name,
    )
    runtime_collectors = collectors or _build_default_collectors(request)

    try:
        raw_inputs = collect_raw_inputs_from_sources(runtime_collectors)
        return ingest_raw_inputs(
            raw_inputs,
            runtime_storage,
            runtime_vector_store,
            summarizer=summarizer,
            content_fetcher=content_fetcher,
            show_progress=request.show_progress,
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
    show_progress: bool = False,
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

        try:
            if raw_input.source == "news" and raw_input.url:
                fetch_result = fetcher.fetch(raw_input.url)
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
            unchanged = storage.has_verified_content_hash(article_id, content_sha256)
            storage.update_article_content(
                article_id,
                content=content,
                title=_resolve_title(raw_input),
                description=_resolve_description(raw_input),
                original_url=None if raw_input.source == "news" else final_url,
                canonical_url=canonical_url,
                published_at=raw_input.published_at or datetime.now(),
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
            if raw_input.source == "news":
                storage.assign_story_group(
                    article_id,
                    title=_resolve_title(raw_input),
                    published_at=raw_input.published_at or datetime.now(),
                    content_sha256=content_sha256,
                )
            content_status = "ready"
            article = storage.get_article(article_id)
            assert article is not None
        except ArticleFetchError as exc:
            storage.mark_article_content_failure(
                article_id,
                reason=exc.reason.value,
                validator_version=CONTENT_VALIDATOR_VERSION,
                final_response_url=exc.url,
                response_status_code=exc.status_code,
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
                    content_status_override=record.content_status if record else "failed",
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue
        except Exception:
            storage.mark_article_content_failure(article_id, reason="content_fetch_failed")
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
                )
            )
            _update_progress(progress, **_build_progress_stats(items))
            continue

        def summarize(article):
            nonlocal summary_agent
            if summary_agent is None:
                summary_agent = SummarizationAgent()
            return summary_agent.summarize_article(
                ArticleForSummarization(
                    article_id=article_id, title=article.title,
                    description=article.description, content=article.content, url=article.url,
                )
            )

        processing = process_article(storage, article_id, vector_store, summarize=summarize)
        summary_status, index_status = processing.summary_status, processing.index_status
        stage_failure = processing.failure_reason
        unchanged = unchanged and not processing.summarized and not processing.indexed and stage_failure is None

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
                unchanged=unchanged,
            )
        )
        _update_progress(progress, **_build_progress_stats(items))

    _close_progress(progress)

    accepted_inputs = sum(1 for item in items if item.status == "accepted")
    rejected_inputs = sum(1 for item in items if item.status == "rejected")
    return NewsIngestionResult(
        collected_events=accepted_inputs,
        stats=_build_stats(items),
        total_articles=storage.count_articles(),
        items=items,
        total_inputs=len(raw_inputs),
        accepted_inputs=accepted_inputs,
        rejected_inputs=rejected_inputs,
    )


def ingest_event_batch(
    events: list[Event],
    storage: SQLiteNewsStore,
    vector_store: VectorStore | None = None,
    *,
    summarizer: SummarizationAgent | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
    show_progress: bool = False,
) -> NewsIngestionResult:
    raw_inputs = [_raw_input_from_event(event) for event in events]
    return ingest_raw_inputs(
        raw_inputs,
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=content_fetcher,
        show_progress=show_progress,
    )


def validate_raw_input(raw_input: RawEventInput) -> str | None:
    source = (raw_input.source or "").strip()
    raw_text = raw_input.raw_text or ""
    if source not in {"manual", "news", "api"}:
        return "invalid_source"
    if not raw_text and not raw_input.url:
        return "missing_text"
    if source == "news":
        if raw_input.url:
            parsed = urlparse(raw_input.url.strip())
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
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
    return storage.create_or_get_article_reference(
        source=raw_input.source,
        title=_resolve_title(raw_input),
        description=_resolve_description(raw_input),
        original_url=raw_input.url or _build_internal_url(input_index, raw_input.source),
        published_at=raw_input.published_at or datetime.now(),
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
        published_at=event.timestamp,
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
    unchanged: bool = False,
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
        unchanged=unchanged,
    )


def _build_stats(items: list[CanonicalArticleOutcome]) -> dict[str, int]:
    accepted = [item for item in items if item.status == "accepted"]
    return {
        "total_events": len(accepted),
        "saved": sum(1 for item in accepted if item.created is True),
        "updated": sum(1 for item in accepted if item.created is False),
        "summarized": sum(1 for item in accepted if item.summary_status == "ready"),
        "indexed": sum(1 for item in accepted if item.index_status == "ready"),
        "skipped": 0,
        "content_failed": sum(1 for item in accepted if item.failure_reason == "content_fetch_failed"),
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
