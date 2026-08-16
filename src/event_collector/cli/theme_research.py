#!/usr/bin/env python3
"""Run the theme research workflow from the command line."""

from __future__ import annotations

import argparse
import sqlite3
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
from event_collector import index_generation
from event_collector.theme_research import (
    ThemeResearchRequest,
    run_theme_research,
    write_theme_research_assets,
)
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationBuildRequest,
    SuccessorGenerationProof,
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
    corpus_config = ServingCorpusConfig(
        index_control_db_path=args.index_control_db_path,
        index_corpus_id=args.index_corpus_id,
        canonical_db_path=args.canonical_db_path,
        canonical_content_root=args.canonical_content_root,
        chroma_persist_dir=args.chroma_persist_dir,
    )
    try:
        pinned = create_active_generation_reader(corpus_config)
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
            publish_assets=False,
        )
        generation_handoff_provenance = None
        if discovered_proofs:
            proof = coordinator(
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
            activation_receipt = _read_activation_receipt(
                args.index_control_db_path,
                proof,
            )
            generation_handoff_provenance = {
                "handoff_run_id": handoff_run_id,
                "corpus_id": proof.corpus_id,
                "generation_id": proof.generation_id,
                "corpus_snapshot_id": proof.corpus_snapshot_id,
                "index_config_fingerprint": proof.index_config_fingerprint,
                "generation_status": proof.status,
                "activation_receipt": activation_receipt,
            }
        artifact_paths = write_theme_research_assets(
            result.metadata,
            ThemeResearchRequest(
                theme=args.theme,
                analysis_goal=args.analysis_goal,
                seed_query=args.seed_query,
                tickers=parse_ticker_hints(args.tickers),
                db_path=args.canonical_db_path,
            ),
            result.evidence,
            result.sufficiency,
            result.theme_summary,
            result.related_companies,
            result.candidate_segments,
            retrieval_provenance=_retrieval_provenance(pinned),
            generation_handoff_provenance=generation_handoff_provenance,
        )
    except Exception as exc:
        storage.close()
        print(f"Error: {exc}")
        return 1

    print(render_theme_research_summary(result))
    print()
    print("Artifacts:")
    for path in artifact_paths.values():
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


def _read_activation_receipt(
    control_db_path: str,
    proof: SuccessorGenerationProof,
) -> dict[str, str]:
    """Read back the durable active pointer before publishing success assets."""
    if not isinstance(proof, SuccessorGenerationProof):
        raise RuntimeError("successor generation activation returned no proof")
    if proof.status != index_generation.VERIFIED or not proof.chunk_verification_valid:
        raise RuntimeError("successor generation proof is not verified")
    try:
        with sqlite3.connect(control_db_path) as connection:
            connection.row_factory = sqlite3.Row
            active = index_generation.read_active_generation(connection, proof.corpus_id)
    except (OSError, sqlite3.Error, index_generation.GenerationStateError) as error:
        raise RuntimeError("successor generation activation receipt is unavailable") from error
    if (
        active is None
        or active.generation_id != proof.generation_id
        or active.corpus_snapshot_id != proof.corpus_snapshot_id
        or active.index_config_fingerprint != proof.index_config_fingerprint
        or active.status != index_generation.ACTIVE
        or active.activated_at is None
    ):
        raise RuntimeError("successor generation activation receipt does not match proof")
    return {
        "corpus_id": active.corpus_id,
        "generation_id": active.generation_id,
        "corpus_snapshot_id": active.corpus_snapshot_id,
        "index_config_fingerprint": active.index_config_fingerprint,
        "status": active.status,
        "activated_at": active.activated_at,
    }
