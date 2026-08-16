#!/usr/bin/env python3
"""Run the theme research workflow from the command line."""

from __future__ import annotations

import argparse

from event_collector.news_storage import SQLiteNewsStore
from event_collector.serving_generation_factory import (
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)
from event_collector.theme_research import ThemeResearchRequest, run_theme_research


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
            local_vector_reader=pinned.reader,
            retrieval_provenance=_retrieval_provenance(pinned),
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
