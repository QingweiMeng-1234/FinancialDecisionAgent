#!/usr/bin/env python3
"""
Batch Event Structuring Agent runner.

Processes stored articles into normalized, durable market signals.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector import ArticleForStructuring, EventStructuringAgent, SQLiteNewsStore


def parse_args():
    parser = argparse.ArgumentParser(description="Structure stored news articles into market events.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--limit", type=int, default=None, help="Maximum articles to process")
    parser.add_argument("--source", default=None, help="Optional article source filter")
    parser.add_argument("--force", action="store_true", help="Replace existing structured events")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()

    print("Financial Agent - Event Structuring Agent")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print(f"SQLite DB: {args.db_path}")
    print(f"Model: {os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()

    if args.force:
        records = storage.list_article_records(source=args.source, limit=args.limit)
        skipped = 0
    else:
        candidate_records = storage.list_article_records(source=args.source, limit=args.limit)
        records = [
            record for record in candidate_records
            if not storage.list_structured_events_for_article(record.id)
        ]
        skipped = len(candidate_records) - len(records)

    processed = 0
    events_created = 0
    failures = 0

    agent = EventStructuringAgent() if records else None

    for record in records:
        article = ArticleForStructuring(
            article_id=record.id,
            title=record.article.title,
            description=record.article.description,
            content=record.article.content,
            url=record.article.url,
        )

        try:
            events = agent.structure_article(article)
            events_created += storage.save_structured_events(
                record.id,
                events,
                replace=args.force,
            )
            processed += 1
        except Exception as exc:
            failures += 1
            print(f"Failed article {record.id}: {exc}")

    print()
    print("Event structuring complete")
    print(f"Articles processed: {processed}")
    print(f"Events created:     {events_created}")
    print(f"Skipped:            {skipped}")
    print(f"Failures:           {failures}")

    storage.close()


if __name__ == "__main__":
    main()
