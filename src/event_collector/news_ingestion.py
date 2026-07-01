"""Deeper collect-and-ingest workflow for article refresh runs."""

from __future__ import annotations

from dataclasses import dataclass

from event_collector.event_collection import (
    EventSourceCollector,
    ManualCollector,
    NewsCollector,
    collect_from_all_sources,
    ingest_events_to_storage,
)
from event_collector.news_storage import SQLiteNewsStore
from event_collector.vector_store import ChromaVectorStore


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


@dataclass
class NewsIngestionResult:
    collected_events: int
    stats: dict
    total_articles: int


def run_news_ingestion(
    request: NewsIngestionRequest,
    *,
    storage: SQLiteNewsStore | None = None,
    vector_store: ChromaVectorStore | None = None,
    collectors: list[EventSourceCollector] | None = None,
) -> NewsIngestionResult:
    """Run one collect -> ingest pass behind a single workflow seam."""
    owns_storage = storage is None
    runtime_storage = storage or SQLiteNewsStore(db_path=request.db_path)
    runtime_storage.init_db()
    runtime_vector_store = vector_store or ChromaVectorStore(
        persist_dir=request.persist_dir,
        collection_name=request.collection_name,
    )
    runtime_collectors = collectors or _build_default_collectors(request)

    try:
        batch = collect_from_all_sources(runtime_collectors)
        stats = ingest_events_to_storage(
            batch,
            runtime_storage,
            runtime_vector_store,
            show_progress=request.show_progress,
        )
        return NewsIngestionResult(
            collected_events=len(batch.events),
            stats=stats,
            total_articles=runtime_storage.count_articles(),
        )
    finally:
        if owns_storage:
            runtime_storage.close()


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
