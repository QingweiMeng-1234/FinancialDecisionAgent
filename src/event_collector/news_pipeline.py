"""Application-layer orchestration for the news ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from event_collector.news_ingestion import NewsIngestionRequest, run_news_ingestion
from event_collector.query_workflow import run_question
from event_collector.news_storage import SQLiteNewsStore
from event_collector.vector_store import ChromaVectorStore


@dataclass
class NewsPipelineRequest(NewsIngestionRequest):
    top_k: int = 3
    question: str | None = None
    debug_rerank: bool = False


@dataclass
class NewsPipelineResult:
    collected_events: int
    stats: dict
    total_articles: int
    answer_text: str | None


def run_news_pipeline(
    request: NewsPipelineRequest,
    *,
    storage: SQLiteNewsStore | None = None,
    vector_store: ChromaVectorStore | None = None,
    collectors: list | None = None,
) -> NewsPipelineResult:
    """Run one end-to-end refresh + optional query workflow."""
    ingestion_result = run_news_ingestion(
        NewsIngestionRequest(
            db_path=request.db_path,
            persist_dir=request.persist_dir,
            collection_name=request.collection_name,
            include_manual=request.include_manual,
            news_endpoint=request.news_endpoint,
            news_days_back=request.news_days_back,
            news_page=request.news_page,
            news_sort_by=request.news_sort_by,
            news_page_size=request.news_page_size,
            show_progress=request.show_progress,
        ),
        storage=storage,
        vector_store=vector_store,
        collectors=collectors,
    )
    runtime_vector_store = vector_store or ChromaVectorStore(
        persist_dir=request.persist_dir,
        collection_name=request.collection_name,
    )
    answer_text = None
    if request.question:
        answer_text = run_question(
            request.question,
            runtime_vector_store,
            top_k=request.top_k,
            debug_rerank=request.debug_rerank,
        )

    return NewsPipelineResult(
        collected_events=ingestion_result.collected_events,
        stats=ingestion_result.stats,
        total_articles=ingestion_result.total_articles,
        answer_text=answer_text,
    )
