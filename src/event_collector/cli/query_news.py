#!/usr/bin/env python3
"""
News Retrieval Pipeline - query stored news and generate grounded answers.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from event_collector.news_storage import SQLiteNewsStore
from event_collector.query_workflow import render_rag_answer, run_question
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT
from event_collector.service_defaults import load_service_defaults
from event_collector.corpus_retrieval import create_corpus_vector_store


def parse_args(argv=None):
    defaults = load_service_defaults()
    parser = argparse.ArgumentParser(description="Query stored news with a minimal true-RAG answer step.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument("--top-k", type=int, default=defaults.query_top_k, help="Number of reranked articles to use in the answer")
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=defaults.retrieval_top_k,
        help="How many retrieved candidates to consider before reranking",
    )
    parser.add_argument(
        "--retrieval-intent",
        choices=("direct", "indirect"),
        default=DEFAULT_RETRIEVAL_INTENT,
        help="Whether retrieval should prefer direct company attribution or indirect theme exposure",
    )
    parser.add_argument("--question", default=None, help="Optional one-shot question instead of interactive mode")
    parser.add_argument("--debug-rerank", action="store_true", help="Print rerank order and short reasons")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Financial Agent - Grounded News Query")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    vector_store = create_corpus_vector_store(
        db_path=args.db_path,
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )

    total = storage.count_articles()
    print(f"Database has {total} articles")

    if total == 0:
        print("\nNo articles in database. Run ingest_news.py first.")
        storage.close()
        return 0

    print()

    if args.question:
        try:
            print(
                run_question(
                    args.question,
                    vector_store,
                    top_k=args.top_k,
                    retrieval_top_k=args.retrieval_top_k,
                    debug_rerank=args.debug_rerank,
                    retrieval_intent=args.retrieval_intent,
                )
            )
        except Exception as exc:
            print(f"Error: {exc}")
            storage.close()
            return 1
        storage.close()
        return 0

    print("Ask grounded questions about the stored news articles (type 'quit' to exit)")
    print("-" * 50)
    print()

    while True:
        question = input("Question: ").strip()

        if question.lower() in ["quit", "exit", "q"]:
            break

        if not question:
            print("Please enter a question.")
            print()
            continue

        print()
        try:
            print(
                run_question(
                    question,
                    vector_store,
                    top_k=args.top_k,
                    retrieval_top_k=args.retrieval_top_k,
                    debug_rerank=args.debug_rerank,
                    retrieval_intent=args.retrieval_intent,
                )
            )
        except Exception as exc:
            print(f"Error: {exc}")
            storage.close()
            return 1
        print()
        print("-" * 50)
        print()

    print("Retrieval session complete.")
    storage.close()
    return 0
