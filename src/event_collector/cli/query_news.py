#!/usr/bin/env python3
"""
News Retrieval Pipeline - query stored news and generate grounded answers.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from event_collector.query_workflow import render_rag_answer, run_question
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT
from event_collector.service_defaults import load_service_defaults, resolve_query_time_window
from event_collector.serving_generation_factory import (
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)


def parse_args(argv=None):
    defaults = load_service_defaults()
    parser = argparse.ArgumentParser(description="Query stored news with a minimal true-RAG answer step.")
    parser.add_argument(
        "--index-control-db-path",
        default="data/runtime/index_generation_control.db",
        help="Read-only generation control database path",
    )
    parser.add_argument("--index-corpus-id", default="news", help="Corpus active-pointer identity")
    parser.add_argument(
        "--canonical-db-path",
        default="data/rag_corpus_v2_20260815/news_articles.db",
        help="Read-only canonical article database path",
    )
    parser.add_argument(
        "--canonical-content-root",
        default="data/rag_corpus_v2_20260815/data/articles",
        help="Canonical article content root",
    )
    parser.add_argument(
        "--chroma-persist-dir",
        default="data/rag_index_v2_20260815",
        help="Chroma persistence root containing the active generation collection",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=defaults.query_top_k,
        help="Number of reranked articles to use in the answer",
    )
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
    parser.add_argument("--start-at", default=None, help="Inclusive UTC ISO-8601 start timestamp")
    parser.add_argument("--end-at", default=None, help="Inclusive UTC ISO-8601 end timestamp")
    parser.add_argument("--latest-at", default=None, help="UTC ISO-8601 end anchor for --lookback-days")
    parser.add_argument("--lookback-days", type=int, default=None, help="Positive rolling UTC lookback in days")
    parser.add_argument("--question", default=None, help="Optional one-shot question instead of interactive mode")
    parser.add_argument("--debug-rerank", action="store_true", help="Print rerank order and short reasons")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    defaults = load_service_defaults()

    print("Financial Agent - Grounded News Query")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    config = ServingCorpusConfig(
        index_control_db_path=args.index_control_db_path,
        index_corpus_id=args.index_corpus_id,
        canonical_db_path=args.canonical_db_path,
        canonical_content_root=args.canonical_content_root,
        chroma_persist_dir=args.chroma_persist_dir,
    )
    try:
        pinned = create_active_generation_reader(config)
    except CorpusUnavailableError as exc:
        print(f"{exc.code}:{exc.reason_code} ({exc.stage}): {exc}")
        return 1

    try:
        vector_store = pinned.reader.with_time_window(
            **resolve_query_time_window(
                defaults,
                start_at=args.start_at,
                end_at=args.end_at,
                latest_at=args.latest_at,
                lookback_days=args.lookback_days,
            )
        )
    except ValueError as exc:
        print(f"Invalid time window: {exc}")
        return 1
    print(f"Active generation: {pinned.active_generation.generation_id}")
    print(f"Eligible articles: {len(pinned.eligibility.articles)}")
    print(f"Excluded articles: {len(pinned.eligibility.exclusions)}")

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
            return 1
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
            return 1
        print()
        print("-" * 50)
        print()

    print("Retrieval session complete.")
    return 0
