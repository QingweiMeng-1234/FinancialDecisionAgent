#!/usr/bin/env python3
"""
News Retrieval Pipeline - query stored news and generate grounded answers.
"""

import argparse
import os
import sys
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector import (
    ChromaVectorStore,
    SQLiteNewsStore,
    answer_query,
    render_citation_list,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Query stored news with a minimal true-RAG answer step.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument("--top-k", type=int, default=3, help="Number of reranked articles to use in the answer")
    parser.add_argument("--question", default=None, help="Optional one-shot question instead of interactive mode")
    parser.add_argument("--debug-rerank", action="store_true", help="Print rerank order and short reasons")
    return parser.parse_args(argv)


def render_rag_answer(result, debug_rerank=False) -> str:
    lines = [
        "Answer:",
        result.answer,
        "",
        f"Confidence: {result.confidence.value}",
        f"Insufficient evidence: {'yes' if result.insufficient_evidence else 'no'}",
    ]

    if result.supporting_points:
        lines.extend(["", "Supporting Points:"])
        for point in result.supporting_points:
            lines.append(f"- {point.text} {render_citation_list(point.citations)}".rstrip())

    if result.counter_points:
        lines.extend(["", "Counter Points:"])
        for point in result.counter_points:
            lines.append(f"- {point.text} {render_citation_list(point.citations)}".rstrip())

    if result.sources:
        lines.extend(["", "Sources:"])
        for source in result.sources:
            lines.append(f"[{source.id}] {source.title}")
            lines.append(f"URL: {source.url}")
            lines.append(f"Snippet: {source.snippet}")
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()

    if debug_rerank and getattr(result, "rerank_metadata", None):
        lines.extend(["", "Rerank Debug:"])
        for index, item in enumerate(result.rerank_metadata.ranked_candidates, start=1):
            lines.append(f"{index}. Candidate {item.candidate_id}: {item.reason}")

    return "\n".join(lines)


def run_question(question, vector_store, top_k=3, answer_fn=answer_query, debug_rerank=False) -> str:
    result = answer_fn(question, vector_store, top_k=top_k)
    return render_rag_answer(result, debug_rerank=debug_rerank)


def main(argv=None):
    args = parse_args(argv)

    print("Financial Agent - Grounded News Query")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )

    total = storage.count_articles()
    print(f"Database has {total} articles")

    if total == 0:
        print("\nNo articles in database. Run main.py first.")
        storage.close()
        return 0

    print()

    if args.question:
        try:
            print(run_question(args.question, vector_store, top_k=args.top_k, debug_rerank=args.debug_rerank))
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
            print(run_question(question, vector_store, top_k=args.top_k, debug_rerank=args.debug_rerank))
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


if __name__ == "__main__":
    raise SystemExit(main())
