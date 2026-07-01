#!/usr/bin/env python3
"""Backward-compatible ingestion entrypoint."""

from __future__ import annotations

import argparse
from datetime import datetime
import os

from event_collector.news_pipeline import NewsPipelineRequest, run_news_pipeline


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Collect news, summarize it, index it, and optionally run a grounded RAG question.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument("--top-k", type=int, default=3, help="Number of reranked articles to use in the answer")
    parser.add_argument("--question", default=None, help="Optional grounded question to run after ingestion")
    parser.add_argument("--debug-rerank", action="store_true", help="Print rerank order and short reasons")
    parser.add_argument("--include-manual", action="store_true", help="Also prompt for manual event input")
    parser.add_argument("--news-endpoint", default="everything", choices=["everything", "top-headlines"], help="Which NewsAPI endpoint mode to use")
    parser.add_argument("--news-days-back", type=int, default=7, help="For everything mode, collect articles from the last N days")
    parser.add_argument("--news-page", type=int, default=1, help="NewsAPI page number to request")
    parser.add_argument("--news-sort-by", default="publishedAt", choices=["publishedAt", "popularity", "relevancy"], help="For everything mode, sort order for returned articles")
    parser.add_argument("--news-page-size", type=int, default=100, help="How many news articles to request from NewsAPI")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress bars during ingestion")
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
    try:
        print("Collecting events from all sources...")
        print("Ingesting, summarizing, and indexing...")
        result = run_news_pipeline(
            NewsPipelineRequest(
                db_path=args.db_path,
                persist_dir=args.persist_dir,
                collection_name=args.collection_name,
                top_k=args.top_k,
                question=args.question,
                debug_rerank=args.debug_rerank,
                include_manual=args.include_manual,
                news_endpoint=args.news_endpoint,
                news_days_back=args.news_days_back,
                news_page=args.news_page,
                news_sort_by=args.news_sort_by,
                news_page_size=args.news_page_size,
                show_progress=not args.no_progress,
            )
        )
        stats = result.stats
        print(f"Collected {result.collected_events} events")
        print()
        print(f"  Total events:  {stats['total_events']}")
        print(f"  Saved:         {stats['saved']}")
        print(f"  Summarized:    {stats['summarized']}")
        print(f"  Indexed:       {stats['indexed']}")
        print(f"  Skipped:       {stats['skipped']}")
        print()
        print(f"Total articles in database: {result.total_articles}")
        if args.question:
            print()
            print("Grounded RAG Answer:")
            print("-" * 50)
            print(result.answer_text)
        else:
            print("Ready for grounded RAG queries.")
        print()
        print("=" * 50)
        print("Pipeline complete.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
