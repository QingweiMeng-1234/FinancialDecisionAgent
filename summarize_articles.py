#!/usr/bin/env python3
"""
Batch Article Summarization runner.

Processes canonical stored articles into factual bullet summaries.

This command deliberately does not mutate a serving Chroma collection. A
verified successor-generation refresh is required after canonical repair.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.errors import ArticleSummarizationError
from event_collector.news_storage import SQLiteNewsStore
from event_collector.rag_runtime_paths import DEFAULT_RAG_CANONICAL_DB_PATH
from event_collector.summarization import summarize_stored_articles


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize stored news articles for retrieval.")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_RAG_CANONICAL_DB_PATH,
        help="Canonical v2 SQLite article database path",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum articles to process")
    parser.add_argument("--source", default=None, help="Optional article source filter")
    parser.add_argument("--force", action="store_true", help="Replace existing summaries")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()

    print("Financial Agent - Article Summarization")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print(f"SQLite DB: {args.db_path}")
    print("Index mutation: disabled (run a verified successor refresh after repair)")
    print(f"Model: {os.getenv('OPENAI_MODEL') or 'deepseek-v4-flash'}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    try:
        stats = summarize_stored_articles(
            storage=storage,
            vector_store=None,
            source=args.source,
            limit=args.limit,
            force=args.force,
        )
    except ArticleSummarizationError as exc:
        print(f"Failed article {exc.article_id}: {exc}")
        storage.close()
        return 1

    print("Article summarization complete")
    print(f"Articles processed: {stats['processed']}")
    print(f"Indexed:            {stats['indexed']}")
    print(f"Skipped:            {stats['skipped']}")
    print(f"Candidates:         {stats['total_candidates']}")
    if stats["processed"]:
        print("Serving index unchanged; run a verified successor-generation refresh.")

    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
