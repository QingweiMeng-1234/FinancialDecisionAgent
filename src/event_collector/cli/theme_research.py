#!/usr/bin/env python3
"""Run the theme research workflow from the command line."""

from __future__ import annotations

import argparse
from uuid import uuid4

from event_collector.news_storage import SQLiteNewsStore
from event_collector.service_defaults import load_service_defaults, resolve_query_time_window
from event_collector.serving_generation_factory import (
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)
from event_collector.successor_generation_coordinator import (
    SuccessorGenerationCoordinatorConfig,
    create_runtime_successor_generation_coordinator,
)
from event_collector.theme_research import ThemeResearchRequest, run_theme_research
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationBuildRequest,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run theme-first research and write persistent research assets.")
    parser.add_argument("--theme", required=True, help="Theme to research, for example AI infrastructure")
    parser.add_argument("--analysis-goal", required=True, help="What you want to understand about the theme")
    parser.add_argument("--seed-query", default=None, help="Optional search phrase to bias discovery")
    parser.add_argument("--tickers", default=None, help="Optional comma-separated ticker hints")
    parser.add_argument("--index-control-db-path", default="data/runtime/index_generation_control.db")
    parser.add_argument("--index-corpus-id", default="news")
    parser.add_argument(
        "--canonical-db-path",
        default="data/rag_corpus_v2_20260815/news_articles.db",
    )
    parser.add_argument(
        "--canonical-content-root",
        default="data/rag_corpus_v2_20260815/data/articles",
    )
    parser.add_argument("--chroma-persist-dir", default="data/rag_index_v2_20260815")
    parser.add_argument("--start-at", default=None, help="Inclusive UTC ISO-8601 start timestamp")
    parser.add_argument("--end-at", default=None, help="Inclusive UTC ISO-8601 end timestamp")
    parser.add_argument("--latest-at", default=None, help="UTC ISO-8601 end anchor for --lookback-days")
    parser.add_argument("--lookback-days", type=int, default=None, help="Positive rolling UTC lookback in days")
    return parser.parse_args(argv)


def parse_ticker_hints(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def render_theme_research_summary(result) -> str:
    return "\n".join(
        [
            "Theme Research:",
            f"- Theme: {result.metadata.theme}",
            f"- Goal: {result.metadata.analysis_goal}",
            f"- Evidence items: {len(result.evidence)}",
            f"- Structure sufficient: {result.sufficiency.structure_sufficient}",
            f"- Fresh monitoring sufficient: {result.sufficiency.fresh_monitoring_sufficient}",
            f"- Related companies: {', '.join(result.related_companies) if result.related_companies else 'None'}",
        ]
    )


def main(argv=None):
    args = parse_args(argv)
    defaults = load_service_defaults()
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

    try:
        local_vector_reader = pinned.reader.with_time_window(
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

    storage = SQLiteNewsStore(db_path=args.canonical_db_path)
    coordinator = create_runtime_successor_generation_coordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=args.index_control_db_path,
            canonical_db_path=args.canonical_db_path,
            canonical_content_root=args.canonical_content_root,
            chroma_persist_dir=args.chroma_persist_dir,
            activate_verified_generation=True,
        )
    )
    handoff_run_id = f"theme-{uuid4().hex}"
    discovered_proofs: dict[int, str] = {}

    def discovered_article_sink(article_id: int, content_sha256: str) -> None:
        previous_hash = discovered_proofs.get(article_id)
        if previous_hash is not None and previous_hash != content_sha256:
            raise ValueError("conflicting theme discovery proof for one article")
        discovered_proofs[article_id] = content_sha256

    try:
        result = run_theme_research(
            ThemeResearchRequest(
                theme=args.theme,
                analysis_goal=args.analysis_goal,
                seed_query=args.seed_query,
                tickers=parse_ticker_hints(args.tickers),
                db_path=args.canonical_db_path,
            ),
            storage=storage,
            local_vector_reader=local_vector_reader,
            retrieval_provenance=_retrieval_provenance(pinned),
            discovered_article_sink=discovered_article_sink,
        )
        if discovered_proofs:
            coordinator(
                SuccessorGenerationBuildRequest(
                    run_id=handoff_run_id,
                    scope_key=f"theme:{args.theme.strip().casefold()}",
                    corpus_id=args.index_corpus_id,
                    articles=tuple(
                        GenerationArticleProof(article_id, content_hash)
                        for article_id, content_hash in sorted(discovered_proofs.items())
                    ),
                )
            )
    except Exception as exc:
        storage.close()
        print(f"Error: {exc}")
        return 1

    print(render_theme_research_summary(result))
    print()
    print("Artifacts:")
    for path in result.artifact_paths.values():
        print(f"- {path}")
    storage.close()
    return 0


def _retrieval_provenance(pinned) -> dict[str, object]:
    active = pinned.active_generation
    eligibility = pinned.eligibility
    return {
        "generation_id": active.generation_id,
        "corpus_id": active.corpus_id,
        "collection_name": active.collection_name,
        "corpus_snapshot_id": active.corpus_snapshot_id,
        "embedding_artifact": active.embedding_artifact,
        "index_config_fingerprint": active.index_config_fingerprint,
        "eligibility_policy_version": eligibility.policy_version,
        "eligible_article_count": len(eligibility.articles),
        "excluded_article_count": len(eligibility.exclusions),
    }
