#!/usr/bin/env python3
"""Generate retrieval-eval annotation skeletons and evaluate completed labels."""

import argparse
import os
import sys

from dotenv import load_dotenv
try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - fallback when tqdm is unavailable
    tqdm = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

load_dotenv()

from event_collector.entity_kb import SQLiteEntityStore
from event_collector.news_storage import SQLiteNewsStore
from event_collector.retrieval_eval import (
    DEFAULT_ANNOTATION_TARGET,
    DEFAULT_REPORTS_DIR,
    build_fresh_annotation_eligibility,
    build_annotation_file,
    evaluate_retrieval_comparisons,
    load_annotation_file,
    rebalance_annotation_directory,
    run_retrieval_comparison,
    save_annotation_file,
    split_master_annotations_by_ticker,
    suggest_annotation_directory,
    write_evaluation_outputs,
    load_tickers_from_annotation_dir,
)
from event_collector.reranking import (
    DeepSeekRAGRerankingClient,
    OpenAIRAGRerankingClient,
    RAGRerankingAgent,
)
from event_collector.ticker_kb import load_ticker_identities, load_ticker_list
from event_collector.vector_store import ChromaVectorStore


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run ticker KB retrieval evaluation workflows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_defaults = {
        "ticker_list_path": os.path.join("data", "retrieval_eval", "experiment_tickers.yaml"),
        "db_path": "news_articles.db",
        "entity_db_path": "company_entities.db",
        "persist_dir": "./chroma_data",
        "collection_name": "news_articles",
        "retrieval_top_k": 4,
    }

    generate_parser = subparsers.add_parser("generate-annotations", help="Generate annotation skeleton YAMLs")
    _add_common_arguments(generate_parser, common_defaults)
    generate_parser.add_argument(
        "--annotation-output-dir",
        default=os.path.join("annotations", "retrieval_eval"),
        help="Directory for generated annotation YAML files",
    )
    generate_parser.add_argument(
        "--annotation-target",
        type=int,
        default=DEFAULT_ANNOTATION_TARGET,
        help="How many candidate articles to include per ticker annotation file",
    )
    generate_parser.add_argument("--force", action="store_true", help="Overwrite existing annotation files")

    rebalance_parser = subparsers.add_parser(
        "rebalance-annotations",
        help="Copy related articles into the ticker YAMLs they appear to fit",
    )
    rebalance_parser.add_argument(
        "--annotation-dir",
        default=os.path.join("annotations", "retrieval_eval"),
        help="Directory containing annotation YAML files",
    )
    rebalance_parser.add_argument(
        "--kb-path",
        default=os.path.join("data", "retrieval_eval", "ticker_kb.yaml"),
        help="Ticker knowledge-base YAML",
    )

    suggest_parser = subparsers.add_parser(
        "suggest-related-tickers",
        help="Fill suggested_tickers and optionally promote clear single-match articles into related_tickers",
    )
    suggest_parser.add_argument(
        "--annotation-dir",
        default=os.path.join("annotations", "retrieval_eval"),
        help="Directory containing annotation YAML files",
    )
    suggest_parser.add_argument(
        "--kb-path",
        default=os.path.join("data", "retrieval_eval", "ticker_kb.yaml"),
        help="Ticker knowledge-base YAML",
    )
    suggest_parser.add_argument(
        "--no-promote-single-match",
        action="store_true",
        help="Do not auto-promote single unambiguous suggestions into related_tickers",
    )
    suggest_parser.add_argument(
        "--replace-existing-related",
        action="store_true",
        help="Replace existing related_tickers with only the newly promoted single-match suggestions",
    )

    split_parser = subparsers.add_parser(
        "split-master-by-ticker",
        help="Split retrieval_eval_master.yaml into one YAML per ticker with impact labels",
    )
    split_parser.add_argument(
        "--master-path",
        default=os.path.join("annotations", "retrieval_eval_master.yaml"),
        help="Path to the master per-article golden-set YAML",
    )
    split_parser.add_argument(
        "--output-dir",
        default=os.path.join("annotations", "retrieval_eval_by_ticker"),
        help="Directory for generated per-ticker YAML files",
    )
    split_parser.add_argument("--force", action="store_true", help="Overwrite existing ticker YAML files")

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate completed annotations")
    _add_common_arguments(evaluate_parser, common_defaults)
    evaluate_parser.add_argument(
        "--annotation-input-dir",
        default=os.path.join("annotations", "retrieval_eval_by_ticker"),
        help="Directory containing completed annotation YAML files",
    )
    evaluate_parser.add_argument(
        "--output-dir",
        default=DEFAULT_REPORTS_DIR,
        help="Directory for Markdown and JSON evaluation outputs",
    )
    evaluate_parser.add_argument(
        "--min-relevant",
        type=int,
        default=0,
        help="Only evaluate tickers whose annotation file contains at least this many relevant labels",
    )
    evaluate_parser.add_argument(
        "--company-only",
        action="store_true",
        help="Only evaluate tickers whose entity KB asset_type is 'company'",
    )
    evaluate_parser.add_argument(
        "--max-article-id",
        type=int,
        default=None,
        help="Only evaluate retrieval results whose article_id is less than or equal to this value",
    )
    evaluate_parser.add_argument(
        "--evaluation-top-k-mode",
        choices=("fixed", "relevant_count"),
        default="fixed",
        help="Use a fixed evaluation K, or set each ticker's evaluation K to its relevant article count",
    )
    evaluate_parser.add_argument(
        "--relevance-type-filter",
        choices=("all", "direct", "indirect"),
        default="all",
        help="Score all relevant evidence, or only direct / indirect relevant evidence",
    )
    return parser.parse_args(argv)


def _add_common_arguments(parser, defaults):
    parser.add_argument("--ticker-list-path", default=defaults["ticker_list_path"], help="Experiment ticker list YAML")
    parser.add_argument("--db-path", default=defaults["db_path"], help="SQLite article database path")
    parser.add_argument("--entity-db-path", default=defaults["entity_db_path"], help="SQLite company entity KB path")
    parser.add_argument("--persist-dir", default=defaults["persist_dir"], help="Chroma persistence directory")
    parser.add_argument("--collection-name", default=defaults["collection_name"], help="Chroma collection name")
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=defaults["retrieval_top_k"],
        help="How many retrieval candidates to consider per chain",
    )
    parser.add_argument(
        "--fresh-window-hours",
        type=int,
        default=24,
        help="Only use clean news articles fetched within this many hours of the latest successful ingest",
    )
    parser.add_argument(
        "--reranker-provider",
        choices=("openai", "deepseek", "both"),
        default="deepseek",
        help="Which reranker provider to use for entity-KB evaluation chains",
    )
    parser.add_argument(
        "--retrieval-intent",
        choices=("direct", "indirect", "both"),
        default="both",
        help="Which retrieval intent to evaluate or generate annotations with",
    )
    parser.add_argument(
        "--show-progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show progress bars while generating retrieval comparisons and outputs",
    )


def main(argv=None):
    args = parse_args(argv)
    if args.command == "rebalance-annotations":
        identities = load_ticker_identities(args.kb_path)
        counts = rebalance_annotation_directory(
            args.annotation_dir,
            identities=identities,
        )
        print("Rebalanced annotation files:")
        for ticker, count in sorted(counts.items()):
            print(f"- {ticker}: {count}")
        return 0

    if args.command == "suggest-related-tickers":
        identities = load_ticker_identities(args.kb_path)
        counts = suggest_annotation_directory(
            args.annotation_dir,
            identities=identities,
            promote_single_match=not args.no_promote_single_match,
            replace_existing_related=args.replace_existing_related,
        )
        print("Suggested related tickers:")
        for ticker, count in sorted(counts.items()):
            print(f"- {ticker}: promoted {count}")
        return 0

    if args.command == "split-master-by-ticker":
        counts = split_master_annotations_by_ticker(
            args.master_path,
            args.output_dir,
            force=args.force,
        )
        print("Generated per-ticker master splits:")
        for ticker, count in sorted(counts.items()):
            print(f"- {ticker}: {count}")
        return 0

    if args.command == "evaluate":
        tickers = load_tickers_from_annotation_dir(args.annotation_input_dir)
        if args.min_relevant > 0:
            tickers = _filter_tickers_by_min_relevant(
                tickers,
                annotation_dir=args.annotation_input_dir,
                min_relevant=args.min_relevant,
            )
    else:
        tickers = load_ticker_list(args.ticker_list_path)

    vector_store = ChromaVectorStore(
        persist_dir=args.persist_dir,
        collection_name=args.collection_name,
    )
    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    company_kb = SQLiteEntityStore(db_path=args.entity_db_path)
    company_kb.init_db()
    if args.command == "evaluate" and args.company_only:
        tickers = _filter_company_only_tickers(
            tickers,
            company_kb=company_kb,
        )
    reranking_agent, deepseek_reranking_agent, entity_chain_name = _build_reranking_agents(args.reranker_provider)
    eligibility = build_fresh_annotation_eligibility(
        storage,
        fresh_window_hours=args.fresh_window_hours,
    )
    allowed_article_ids = _build_allowed_article_ids(
        base_ids=eligibility.allowed_article_ids,
        max_article_id=args.max_article_id if args.command == "evaluate" else None,
    )

    comparison_retrieval_top_k = args.retrieval_top_k
    if args.command == "evaluate" and args.evaluation_top_k_mode == "relevant_count":
        comparison_retrieval_top_k = max(
            args.retrieval_top_k,
            _max_relevant_count_for_tickers(
                tickers,
                annotation_dir=args.annotation_input_dir,
            ),
        )
        if args.show_progress:
            print(
                "Adjusted retrieval_top_k for relevant_count evaluation mode:",
                comparison_retrieval_top_k,
            )

    comparison_progress = _build_progress(
        tickers,
        enabled=args.show_progress,
        desc=f"{args.command}: retrieval comparisons",
        unit="ticker",
    )
    comparisons = []
    for index, ticker in enumerate(comparison_progress, start=1):
        comparisons.append(
            run_retrieval_comparison(
                ticker,
                vector_store,
                company_kb=company_kb,
                retrieval_top_k=comparison_retrieval_top_k,
                reranking_agent=reranking_agent,
                deepseek_reranking_agent=deepseek_reranking_agent,
                entity_chain_name=entity_chain_name,
                allowed_article_ids=allowed_article_ids,
                retrieval_intent=args.retrieval_intent,
            )
        )
        _update_progress(comparison_progress, completed=index, total=len(tickers))
    _close_progress(comparison_progress)

    if args.command == "generate-annotations":
        output_paths = []
        output_progress = _build_progress(
            comparisons,
            enabled=args.show_progress,
            desc="generate-annotations: writing files",
            unit="ticker",
        )
        for index, comparison in enumerate(output_progress, start=1):
            annotation_file = build_annotation_file(
                comparison,
                storage=storage,
                annotation_target=args.annotation_target,
            )
            output_paths.append(
                save_annotation_file(
                    annotation_file,
                    args.annotation_output_dir,
                    force=args.force,
                )
            )
            _update_progress(output_progress, completed=index, total=len(comparisons))
        _close_progress(output_progress)
        print("Generated annotation skeletons:")
        for path in output_paths:
            print(f"- {path}")
        return 0

    if args.command == "evaluate":
        if args.show_progress:
            print("Evaluating labeled comparisons against annotations...")
        evaluation = evaluate_retrieval_comparisons(
            comparisons,
            args.annotation_input_dir,
            top_k=args.retrieval_top_k,
            top_k_mode=args.evaluation_top_k_mode,
            relevance_type_filter=args.relevance_type_filter,
        )
        markdown_path, json_path = write_evaluation_outputs(evaluation, args.output_dir)
        print(f"Markdown report: {markdown_path}")
        print(f"JSON results: {json_path}")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def _build_reranking_agents(provider: str) -> tuple[RAGRerankingAgent, RAGRerankingAgent | None, str]:
    if provider == "openai":
        return RAGRerankingAgent(OpenAIRAGRerankingClient()), None, "entity_kb_enhanced"
    if provider == "deepseek":
        return RAGRerankingAgent(DeepSeekRAGRerankingClient()), None, "deepseek_entity_kb_enhanced"
    if provider == "both":
        return (
            RAGRerankingAgent(OpenAIRAGRerankingClient()),
            RAGRerankingAgent(DeepSeekRAGRerankingClient()),
            "entity_kb_enhanced",
        )
    raise ValueError(f"Unsupported reranker provider: {provider}")


def _filter_tickers_by_min_relevant(
    tickers: list[str],
    *,
    annotation_dir: str,
    min_relevant: int,
) -> list[str]:
    filtered: list[str] = []
    for ticker in tickers:
        annotation = load_annotation_file(os.path.join(annotation_dir, f"{ticker}.yaml"))
        relevant_count = sum(1 for item in annotation.annotations if item.label == "relevant")
        if relevant_count >= min_relevant:
            filtered.append(ticker)
    return filtered


def _max_relevant_count_for_tickers(
    tickers: list[str],
    *,
    annotation_dir: str,
) -> int:
    max_count = 0
    for ticker in tickers:
        annotation = load_annotation_file(os.path.join(annotation_dir, f"{ticker}.yaml"))
        relevant_count = sum(1 for item in annotation.annotations if item.label == "relevant")
        max_count = max(max_count, relevant_count)
    return max_count


def _filter_company_only_tickers(
    tickers: list[str],
    *,
    company_kb: SQLiteEntityStore,
) -> list[str]:
    filtered: list[str] = []
    for ticker in tickers:
        company = company_kb.load_company_by_ticker(ticker)
        if company is None:
            continue
        if (company.asset_type or "").strip().lower() == "company":
            filtered.append(ticker)
    return filtered


def _build_allowed_article_ids(
    *,
    base_ids: set[int] | None,
    max_article_id: int | None,
) -> set[int] | None:
    allowed = set(base_ids) if base_ids is not None else None
    if max_article_id is None:
        return allowed

    bounded_ids = {article_id for article_id in range(1, max_article_id + 1)}
    if allowed is None:
        return bounded_ids
    return allowed & bounded_ids


def _build_progress(items, *, enabled: bool, desc: str, unit: str):
    if not enabled or tqdm is None:
        return items
    return tqdm(items, total=len(items), desc=desc, unit=unit)


def _update_progress(progress, **postfix: int) -> None:
    if tqdm is None:
        return
    if hasattr(progress, "set_postfix"):
        progress.set_postfix(postfix)


def _close_progress(progress) -> None:
    if tqdm is None:
        return
    if hasattr(progress, "close"):
        progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
