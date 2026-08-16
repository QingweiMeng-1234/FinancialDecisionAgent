#!/usr/bin/env python3
"""Generate a news-grounded Buffett-style recommendation for one target."""

from __future__ import annotations

import argparse
from datetime import datetime
import os

from event_collector.news_storage import SQLiteNewsStore
from event_collector.recommendation import recommend_target, write_recommendation_report
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT
from event_collector.service_defaults import load_service_defaults
from event_collector.serving_generation_factory import (
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)


def parse_args(argv=None):
    defaults = load_service_defaults()
    parser = argparse.ArgumentParser(
        description="Generate a news-grounded Buffett-style recommendation for a stock, ETF, or general market target."
    )
    parser.add_argument("--target", required=True, help="Ticker, ETF, or 'general market'")
    parser.add_argument("--index-control-db-path", default="data/runtime/index_generation_control.db")
    parser.add_argument("--index-corpus-id", default="news")
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
        help="Chroma root containing the active immutable generation",
    )
    parser.add_argument("--top-k", type=int, default=defaults.recommendation_top_k, help="Number of reranked articles to use in the recommendation")
    parser.add_argument("--retrieval-top-k", type=int, default=defaults.retrieval_top_k, help="How many retrieved candidates to consider before reranking")
    parser.add_argument(
        "--retrieval-intent",
        choices=("direct", "indirect"),
        default=DEFAULT_RETRIEVAL_INTENT,
        help="Whether retrieval should prefer direct company attribution or indirect theme exposure",
    )
    parser.add_argument("--output-dir", default=os.path.join("reports", "recommendations"), help="Directory to store generated recommendation reports")
    parser.add_argument("--debug-rerank", action="store_true", help="Include rerank details in output and report")
    parser.add_argument("--debug-aggregation", action="store_true", help="Include aggregated event details in output and report")
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
        lines.extend(["", "Aggregation:", f"- Summary: {response.aggregation.summary}", f"- Dominant driver: {response.aggregation.dominant_driver}", f"- Net score: {response.aggregation.net_score}"])
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
            lines.append(f"[{event.source_id}] {event.article_title}: {event.event_type.value} / {event.direction.value} / {event.importance.value} / {event.time_horizon.value} (score {event.score})")
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
    try:
        pinned = create_active_generation_reader(
            ServingCorpusConfig(
                index_control_db_path=args.index_control_db_path,
                index_corpus_id=args.index_corpus_id,
                canonical_db_path=args.canonical_db_path,
                canonical_content_root=args.canonical_content_root,
                chroma_persist_dir=args.chroma_persist_dir,
            )
        )
    except CorpusUnavailableError as exc:
        print(f"{exc.code}:{exc.reason_code} ({exc.stage}): {exc}")
        return 1

    storage = SQLiteNewsStore(db_path=args.canonical_db_path)
    vector_store = pinned.reader
    print(f"Active generation: {pinned.active_generation.generation_id}")
    total = storage.count_articles()
    print(f"Database has {total} articles")
    if total == 0:
        print("\nNo articles in database. Run main.py first.")
        storage.close()
        return 0
    print()
    try:
        response = recommend_target(args.target, vector_store, storage, top_k=args.top_k, retrieval_top_k=args.retrieval_top_k, retrieval_intent=args.retrieval_intent)
        report_path = write_recommendation_report(
            args.target,
            response,
            output_dir=args.output_dir,
            debug_rerank=args.debug_rerank,
            debug_aggregation=args.debug_aggregation,
            retrieval_provenance={
                "generation_id": pinned.active_generation.generation_id,
                "corpus_snapshot_id": pinned.active_generation.corpus_snapshot_id,
                "index_config_fingerprint": pinned.active_generation.index_config_fingerprint,
            },
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
