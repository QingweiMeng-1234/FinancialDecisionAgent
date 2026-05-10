#!/usr/bin/env python3
"""
Batch Article Summarization runner.

Processes stored articles into factual bullet summaries and reindexes them.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector import (
    ArticleSummarizationError,
    ChromaVectorStore,
    SQLiteNewsStore,
    summarize_stored_articles,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize stored news articles for retrieval.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
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
    print(f"Chroma DB: {args.persist_dir}")
    print(f"Model: {os.getenv('OPENAI_MODEL') or 'gpt-5.4-mini'}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )

    try:
        stats = summarize_stored_articles(
            storage=storage,
            vector_store=vector_store,
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

    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
