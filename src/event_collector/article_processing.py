"""Completion and retry rules for an article's summary and vector index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from event_collector.news_storage import ArticleRecord, NewsArticle, SQLiteNewsStore

if TYPE_CHECKING:
    from event_collector.vector_store import VectorStore


@dataclass
class ArticleProcessingResult:
    summary_status: str
    index_status: str
    summarized: bool = False
    indexed: bool = False
    errors: dict[str, Exception] = field(default_factory=dict)

    @property
    def failure_reason(self) -> str | None:
        return next(iter(self.errors), None)

    @property
    def error(self) -> Exception | None:
        return next(iter(self.errors.values()), None)


def needs_article_processing(record: ArticleRecord, *, include_index: bool = False) -> bool:
    """Whether a ready article has an unfinished requested stage."""
    return record.content_status == "ready" and (
        _needs_summary(record.article)
        or (include_index and _needs_index(record.article))
    )


def _needs_index(article: NewsArticle) -> bool:
    return (
        article.index_status != "ready"
        or article.indexed_content_sha256 != (article.active_content_sha256 or article.content_sha256)
    )


def _needs_summary(article: NewsArticle) -> bool:
    return article.summary_status != "ready" or not (article.summary or "").strip()


def process_article(
    storage: SQLiteNewsStore,
    article_id: int,
    vector_store: VectorStore | None,
    *,
    summarize: Callable[[NewsArticle], str],
    force_summary: bool = False,
) -> ArticleProcessingResult:
    """Resume unfinished derived stages; refresh vector metadata after a new summary."""
    record = storage.get_article_record(article_id)
    if record is None:
        raise ValueError(f"Article {article_id} not found")
    article = record.article
    content_hash = article.active_content_sha256 or article.content_sha256
    result = ArticleProcessingResult(record.summary_status, record.index_status)
    if article.content_status != "ready" or not storage.has_verified_content_hash(article_id, content_hash):
        result.errors["content_unavailable"] = ValueError(f"Article {article_id} has no verified current content")
        return result
    needs_summary = force_summary or _needs_summary(article)
    needs_index = vector_store is not None and (_needs_index(article) or needs_summary)

    if needs_summary:
        try:
            summary = summarize(article)
            if not storage.update_article_summary(article_id, summary, expected_content_sha256=content_hash):
                raise RuntimeError("Article content changed during summary processing")
            article.summary = summary
            result.summary_status = "ready"
            result.summarized = True
        except Exception as exc:
            if not storage.mark_article_processing_status(
                article_id, summary_status="failed", expected_content_sha256=content_hash,
            ):
                needs_index = False
            result.summary_status = "failed"
            result.errors["summary_failed"] = exc

    if needs_index:
        try:
            vector_store.add_article(article_id, article)
            if not storage.mark_article_index_ready(article_id, content_hash):
                raise RuntimeError("Article content changed during index processing")
            result.index_status = "ready"
            result.indexed = True
        except Exception as exc:
            storage.mark_article_processing_status(
                article_id, index_status="failed", expected_content_sha256=content_hash,
            )
            result.index_status = "failed"
            result.errors["index_failed"] = exc
    current = storage.get_article_record(article_id)
    if current is not None:
        result.summary_status, result.index_status = current.summary_status, current.index_status
    return result
