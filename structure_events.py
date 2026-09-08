#!/usr/bin/env python3
"""
Batch Event Structuring Agent runner.

Manual backfill/repair tool for stored articles into normalized, durable market signals.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.event_structuring import ArticleForStructuring, EventStructuringAgent
from event_collector.news_storage import SQLiteNewsStore


def parse_args():
    parser = argparse.ArgumentParser(
        description="Manually structure stored news articles into market events."
    )
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--limit", type=int, default=None, help="Maximum articles to process")
    parser.add_argument("--source", default=None, help="Optional article source filter")
    parser.add_argument(
        "--force-structure",
        "--force",
        dest="force_structure",
        action="store_true",
        help="Re-run structuring for the selected articles and replace their stored structured events",
    )
    return parser.parse_args()


def run_structuring(
    storage: SQLiteNewsStore,
    limit: int | None = None,
    source: str | None = None,
    force_structure: bool = False,
    agent: EventStructuringAgent | None = None,
) -> dict:
    """Run one manual structuring pass and return summary counts."""
    if force_structure:
        records = storage.list_article_records(source=source, limit=limit)
        skipped = 0
    else:
        candidate_records = storage.list_article_records(source=source, limit=limit)
        records = storage.list_unstructured_article_records(source=source, limit=limit)
        skipped = len(candidate_records) - len(records)

    processed = 0
    events_created = 0
    failures = 0
    runtime_agent = agent or (EventStructuringAgent() if records else None)

    for record in records:
        article = ArticleForStructuring(
            article_id=record.id,
            title=record.article.title,
            description=record.article.description,
            content=record.article.content,
            url=record.article.url,
        )

        try:
            events = runtime_agent.structure_article(article)
            events_created += storage.save_structured_events(
                record.id,
                events,
                replace=force_structure,
            )
            processed += 1
        except Exception as exc:
            failures += 1
            storage.mark_article_structuring_result(record.id, status="failed", error=str(exc))
            print(f"Failed article {record.id}: {exc}")

    return {
        "processed": processed,
        "events_created": events_created,
        "skipped": skipped,
        "failures": failures,
    }


def main():
    load_dotenv()
    args = parse_args()

    print("Financial Agent - Event Structuring Agent")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print(f"SQLite DB: {args.db_path}")
    provider = os.getenv("EVENT_STRUCTURING_PROVIDER", "deepseek").strip().lower()
    configured_model = (
        os.getenv("LOCAL_EVENT_MODEL", "<unset>") if provider.startswith("local")
        else os.getenv("DEEPSEEK_STRUCTURING_MODEL") or "deepseek-v4-pro"
    )
    print(f"Provider: {provider}")
    print(f"Model: {configured_model}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()

    summary = run_structuring(
        storage,
        limit=args.limit,
        source=args.source,
        force_structure=args.force_structure,
    )

    print()
    print("Event structuring complete")
    print(f"Articles processed: {summary['processed']}")
    print(f"Events created:     {summary['events_created']}")
    print(f"Skipped:            {summary['skipped']}")
    print(f"Failures:           {summary['failures']}")

    storage.close()


if __name__ == "__main__":
    main()
