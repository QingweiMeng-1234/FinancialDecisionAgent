#!/usr/bin/env python3
"""Generate a news-grounded Buffett-style recommendation for one target."""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

load_dotenv()

from event_collector import (
    ChromaVectorStore,
    SQLiteNewsStore,
    recommend_target,
    write_recommendation_report,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a news-grounded Buffett-style recommendation for a stock, ETF, or general market target."
    )
    parser.add_argument("--target", required=True, help="Ticker, ETF, or 'general market'")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument("--top-k", type=int, default=3, help="Number of reranked articles to use in the recommendation")
    parser.add_argument(
        "--output-dir",
        default=os.path.join("reports", "recommendations"),
        help="Directory to store generated recommendation reports",
    )
    parser.add_argument("--debug-rerank", action="store_true", help="Include rerank details in output and report")
    parser.add_argument(
        "--debug-aggregation",
        action="store_true",
        help="Include aggregated event details in output and report",
    )
    return parser.parse_args(argv)


def render_recommendation(response, debug_rerank=False, debug_aggregation=False) -> str:
    lines = [
        "Recommendation:",
        f"Decision: {response.decision.value}",
        f"Confidence: {response.confidence.value}",
        f"Time horizon: {response.time_horizon.value}",
        f"Insufficient evidence: {'yes' if response.insufficient_evidence else 'no'}",
        "",
        "Reasoning:",
        response.reasoning,
    ]

    if response.key_risks:
        lines.extend(["", "Key Risks:"])
        for risk in response.key_risks:
            lines.append(f"- {risk}")

    if response.aggregation is not None:
        lines.extend(
            [
                "",
                "Aggregation:",
                f"- Summary: {response.aggregation.summary}",
                f"- Dominant driver: {response.aggregation.dominant_driver}",
                f"- Net score: {response.aggregation.net_score}",
            ]
        )
        if response.aggregation.conflicts:
            lines.append("- Conflicts:")
            for conflict in response.aggregation.conflicts:
                lines.append(f"  - {conflict}")

    if response.sources:
        lines.extend(["", "Sources:"])
        for source in response.sources:
            lines.append(f"[{source.id}] {source.title}")
            lines.append(f"URL: {source.url}")
            lines.append(f"Snippet: {source.snippet}")
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

    if debug_aggregation and response.aggregation is not None:
        lines.extend(["", "Aggregation Debug:"])
        for event in response.aggregation.target_events:
            lines.append(
                (
                    f"[{event.source_id}] {event.article_title}: {event.event_type.value} / "
                    f"{event.direction.value} / {event.importance.value} / {event.time_horizon.value} "
                    f"(score {event.score})"
                )
            )

    if debug_rerank and response.rerank_metadata is not None:
        lines.extend(["", "Rerank Debug:"])
        for index, item in enumerate(response.rerank_metadata.ranked_candidates, start=1):
            lines.append(f"{index}. Candidate {item.candidate_id}: {item.reason}")

    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)

    print("Financial Agent - Recommendation Flow")
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

    try:
        response = recommend_target(
            args.target,
            vector_store,
            storage,
            top_k=args.top_k,
        )
        report_path = write_recommendation_report(
            args.target,
            response,
            output_dir=args.output_dir,
            debug_rerank=args.debug_rerank,
            debug_aggregation=args.debug_aggregation,
        )
        print(render_recommendation(response, debug_rerank=args.debug_rerank, debug_aggregation=args.debug_aggregation))
        print()
        print(f"Report saved to: {report_path}")
        storage.close()
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        storage.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
