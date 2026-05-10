#!/usr/bin/env python3
"""
End-to-end news pipeline: collect, summarize, index, and optionally query.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

load_dotenv()

from event_collector import (
    ChromaVectorStore,
    ManualCollector,
    NewsCollector,
    SQLiteNewsStore,
    collect_from_all_sources,
    ingest_events_to_storage,
)
from query_news import run_question


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect news, summarize it, index it, and optionally run a grounded RAG question."
    )
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument("--top-k", type=int, default=3, help="Number of reranked articles to use in the answer")
    parser.add_argument("--question", default=None, help="Optional grounded question to run after ingestion")
    parser.add_argument("--debug-rerank", action="store_true", help="Print rerank order and short reasons")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Financial Agent - News Pipeline")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    news_key = os.getenv("NEWSAPI_API_KEY")
    print(f"NewsAPI key: {'Set' if news_key else 'Not set'}")
    print(f"SQLite DB: {args.db_path}")
    print(f"ChromaDB: {args.persist_dir}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )

    collectors = [
        ManualCollector(),
        NewsCollector(),
    ]

    try:
        print("Collecting events from all sources...")
        batch = collect_from_all_sources(collectors)
        print(f"Collected {len(batch.events)} events")
        print()

        print("Ingesting, summarizing, and indexing...")
        stats = ingest_events_to_storage(batch, storage, vector_store)

        print(f"  Total events:  {stats['total_events']}")
        print(f"  Saved:         {stats['saved']}")
        print(f"  Summarized:    {stats['summarized']}")
        print(f"  Indexed:       {stats['indexed']}")
        print(f"  Skipped:       {stats['skipped']}")
        print()

        total_articles = storage.count_articles()
        print(f"Total articles in database: {total_articles}")

        if args.question:
            print()
            print("Grounded RAG Answer:")
            print("-" * 50)
            print(
                run_question(
                    args.question,
                    vector_store,
                    top_k=args.top_k,
                    debug_rerank=args.debug_rerank,
                )
            )
        else:
            print("Ready for grounded RAG queries.")

        print()
        print("=" * 50)
        print("Pipeline complete.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
