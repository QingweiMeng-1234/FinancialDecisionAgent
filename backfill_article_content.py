#!/usr/bin/env python3
"""Second-stage backfill for articles that already have external URLs but incomplete raw content."""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.article_content import (
    ArticleContentFetcher,
    ArticleFetchError,
    FetchFailureReason,
)
from event_collector.news_storage import ArticleRecord, NewsArticle, SQLiteNewsStore
from event_collector.summarization import ArticleForSummarization, SummarizationAgent
from event_collector.vector_store import ChromaVectorStore, VectorStore


DEFAULT_REPORT_PATH = "content_backfill_report.csv"
DEFAULT_COLLECTION_NAME = "news_articles"
MIN_COMPLETE_CONTENT_LENGTH = 200
MAX_PLACEHOLDER_CONTENT_LENGTH = 600


@dataclass
class ContentBackfillReportRow:
    article_id: int
    current_url: str
    canonical_url: str
    status: str
    content_length_before: int
    content_length_after: int
    fetch_failure_reason: str
    summary_rebuilt: str
    index_rebuilt: str
    notes: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill raw article content for news rows that already have external URLs."
    )
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default=DEFAULT_COLLECTION_NAME, help="Chroma collection name")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of rows to process")
    parser.add_argument("--article-id", type=int, default=None, help="Only process one specific article")
    parser.add_argument("--force", action="store_true", help="Re-fetch even when content already looks complete")
    parser.add_argument("--report-path", default=DEFAULT_REPORT_PATH, help="CSV report output path")
    return parser.parse_args(argv)


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").split()).strip()


def normalize_for_compare(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in normalize_whitespace(value)).strip()


def is_placeholder_content(article: NewsArticle) -> bool:
    content = normalize_whitespace(article.content)
    if not content:
        return True
    if len(content) < MIN_COMPLETE_CONTENT_LENGTH:
        return True

    if len(content) > MAX_PLACEHOLDER_CONTENT_LENGTH:
        return False

    normalized_content = normalize_for_compare(content)
    normalized_title = normalize_for_compare(article.title)
    normalized_description = normalize_for_compare(article.description)
    combined = normalize_for_compare(f"{article.title} {article.description}")

    placeholder_candidates = {
        normalized_title,
        normalized_description,
        combined,
        normalize_for_compare(f"{article.title}. {article.description}"),
    }
    return normalized_content in {candidate for candidate in placeholder_candidates if candidate}


def should_backfill_content(record: ArticleRecord, force: bool = False) -> bool:
    article = record.article
    current_url = article.original_url or article.url or ""
    if article.source != "news":
        return False
    if not current_url.startswith(("http://", "https://")):
        return False
    if current_url.startswith("internal://"):
        return False
    if force:
        return True
    return is_placeholder_content(article)


def load_candidate_records(
    storage: SQLiteNewsStore,
    *,
    article_id: int | None,
    limit: int | None,
    force: bool,
) -> list[ArticleRecord]:
    if article_id is not None:
        record = storage.get_article_record(article_id)
        if record is None:
            return []
        return [record]

    records = storage.list_article_records(source="news")
    candidates = [record for record in records if should_backfill_content(record, force=force)]
    if limit is not None:
        return candidates[:limit]
    return candidates


def rebuild_summary_and_index(
    storage: SQLiteNewsStore,
    record: ArticleRecord,
    *,
    summarizer: SummarizationAgent | None,
    vector_store: VectorStore | None,
) -> tuple[bool, bool, str]:
    notes: list[str] = []
    summary_rebuilt = False
    index_rebuilt = False
    article = storage.get_article(record.id)
    if article is None:
        return False, False, "article disappeared before rebuild"

    agent = summarizer or SummarizationAgent()
    try:
        summary = agent.summarize_article(
            ArticleForSummarization(
                article_id=record.id,
                title=article.title,
                description=article.description,
                content=article.content,
                url=article.canonical_url or article.original_url or article.url,
            )
        )
        storage.update_article_summary(record.id, summary)
        article.summary = summary
        summary_rebuilt = True
    except Exception as exc:
        storage.mark_article_processing_status(record.id, summary_status="failed")
        notes.append(f"summary_failed: {exc}")

    if vector_store is not None:
        try:
            vector_store.add_article(record.id, article)
            storage.mark_article_processing_status(record.id, index_status="ready")
            index_rebuilt = True
        except Exception as exc:
            storage.mark_article_processing_status(record.id, index_status="failed")
            notes.append(f"index_failed: {exc}")

    return summary_rebuilt, index_rebuilt, "; ".join(notes)


def process_record(
    storage: SQLiteNewsStore,
    record: ArticleRecord,
    *,
    fetcher: ArticleContentFetcher,
    summarizer: SummarizationAgent | None,
    vector_store: VectorStore | None,
    force: bool,
) -> ContentBackfillReportRow:
    article = record.article
    current_url = article.original_url or article.url or ""
    content_before = len(normalize_whitespace(article.content))

    if not should_backfill_content(record, force=force):
        return ContentBackfillReportRow(
            article_id=record.id,
            current_url=current_url,
            canonical_url=article.canonical_url or "",
            status="content_skipped",
            content_length_before=content_before,
            content_length_after=content_before,
            fetch_failure_reason="",
            summary_rebuilt="no",
            index_rebuilt="no",
            notes="content already complete",
        )

    try:
        fetch_result = fetcher.fetch_with_classification(current_url)
        storage.update_article_content(
            record.id,
            content=fetch_result.content,
            title=article.title,
            description=article.description,
            original_url=fetch_result.original_url,
            canonical_url=fetch_result.canonical_url,
            published_at=article.published_at,
        )
    except ArticleFetchError as exc:
        storage.mark_article_processing_status(record.id, content_status="failed")
        return ContentBackfillReportRow(
            article_id=record.id,
            current_url=current_url,
            canonical_url=article.canonical_url or "",
            status="content_failed",
            content_length_before=content_before,
            content_length_after=content_before,
            fetch_failure_reason=exc.reason.value,
            summary_rebuilt="no",
            index_rebuilt="no",
            notes=str(exc),
        )
    except sqlite3.IntegrityError as exc:
        storage.mark_article_processing_status(record.id, content_status="failed")
        return ContentBackfillReportRow(
            article_id=record.id,
            current_url=current_url,
            canonical_url=article.canonical_url or "",
            status="content_failed",
            content_length_before=content_before,
            content_length_after=content_before,
            fetch_failure_reason=FetchFailureReason.DUPLICATE_URL_CONFLICT.value,
            summary_rebuilt="no",
            index_rebuilt="no",
            notes=str(exc),
        )

    updated = storage.get_article_record(record.id)
    assert updated is not None
    summary_rebuilt, index_rebuilt, notes = rebuild_summary_and_index(
        storage,
        updated,
        summarizer=summarizer,
        vector_store=vector_store,
    )
    content_after = len(normalize_whitespace(updated.article.content))
    return ContentBackfillReportRow(
        article_id=record.id,
        current_url=current_url,
        canonical_url=updated.article.canonical_url or "",
        status="content_backfilled",
        content_length_before=content_before,
        content_length_after=content_after,
        fetch_failure_reason="",
        summary_rebuilt="yes" if summary_rebuilt else "no",
        index_rebuilt="yes" if index_rebuilt else "no",
        notes=notes,
    )


def write_report(report_path: str, rows: list[ContentBackfillReportRow]) -> None:
    with open(report_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "article_id",
                "current_url",
                "canonical_url",
                "status",
                "content_length_before",
                "content_length_after",
                "fetch_failure_reason",
                "summary_rebuilt",
                "index_rebuilt",
                "notes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def run_content_backfill(
    *,
    storage: SQLiteNewsStore,
    fetcher: ArticleContentFetcher | None = None,
    summarizer: SummarizationAgent | None = None,
    vector_store: VectorStore | None = None,
    article_id: int | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    runtime_fetcher = fetcher or ArticleContentFetcher()
    rows: list[ContentBackfillReportRow] = []
    candidates = load_candidate_records(
        storage,
        article_id=article_id,
        limit=limit,
        force=force,
    )

    for record in candidates:
        rows.append(
            process_record(
                storage,
                record,
                fetcher=runtime_fetcher,
                summarizer=summarizer,
                vector_store=vector_store,
                force=force,
            )
        )

    return {
        "rows": rows,
        "processed": len(rows),
        "content_backfilled": sum(1 for row in rows if row.status == "content_backfilled"),
        "content_failed": sum(1 for row in rows if row.status == "content_failed"),
        "content_skipped": sum(1 for row in rows if row.status == "content_skipped"),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )
    try:
        result = run_content_backfill(
            storage=storage,
            vector_store=vector_store,
            article_id=args.article_id,
            limit=args.limit,
            force=args.force,
        )
        write_report(args.report_path, result["rows"])
        print(
            "Processed {processed} rows: {content_backfilled} backfilled, {content_failed} failed, {content_skipped} skipped".format(
                **result
            )
        )
        print(f"Report written to {args.report_path}")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
