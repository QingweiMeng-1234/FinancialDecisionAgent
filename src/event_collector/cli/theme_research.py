#!/usr/bin/env python3
"""Run the theme research workflow from the command line."""

from __future__ import annotations

import argparse

from event_collector.news_storage import SQLiteNewsStore
from event_collector.theme_research import ThemeResearchRequest, run_theme_research
from event_collector.vector_store import ChromaVectorStore


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run theme-first research and write persistent research assets.")
    parser.add_argument("--theme", required=True, help="Theme to research, for example AI infrastructure")
    parser.add_argument("--analysis-goal", required=True, help="What you want to understand about the theme")
    parser.add_argument("--seed-query", default=None, help="Optional search phrase to bias discovery")
    parser.add_argument("--tickers", default=None, help="Optional comma-separated ticker hints")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
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
    storage = SQLiteNewsStore(db_path=args.db_path)
    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )
    try:
        result = run_theme_research(
            ThemeResearchRequest(
                theme=args.theme,
                analysis_goal=args.analysis_goal,
                seed_query=args.seed_query,
                tickers=parse_ticker_hints(args.tickers),
                db_path=args.db_path,
                persist_dir=args.persist_dir,
                collection_name=args.collection_name,
            ),
            storage=storage,
            vector_store=vector_store,
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

