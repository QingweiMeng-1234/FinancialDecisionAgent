#!/usr/bin/env python3
"""Run the watchlist triage workflow over a CLI-provided ticker list."""

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
    WatchlistRunRequest,
    normalize_tickers,
    render_watchlist_summary,
    run_watchlist,
    write_watchlist_report,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the watchlist triage workflow for a list of tickers.")
    parser.add_argument("--tickers", default=None, help="Comma-separated ticker list, for example NVDA,MSFT,TSLA")
    parser.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="Repeatable single ticker argument. Can be used more than once.",
    )
    parser.add_argument("--top-n", type=int, default=3, help="How many ranked names to emphasize in output")
    parser.add_argument("--retrieval-top-k", type=int, default=5, help="How many retrieved items to rerank per ticker")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--persist-dir", default="./chroma_data", help="Chroma persistence directory")
    parser.add_argument("--collection-name", default="news_articles", help="Chroma collection name")
    parser.add_argument(
        "--output-dir",
        default=os.path.join("reports", "watchlist_triage"),
        help="Directory to store generated watchlist reports",
    )
    parser.add_argument("--debug-rerank", action="store_true", help="Include rerank details in the markdown report")
    parser.add_argument("--debug-review", action="store_true", help="Include reviewer flags in the markdown report")
    parser.add_argument(
        "--force-structure",
        action="store_true",
        help="Re-run structuring for retrieved articles even when cached structured events already exist",
    )
    return parser.parse_args(argv)


def parse_requested_tickers(args) -> list[str]:
    if args.tickers:
        tickers = [part.strip() for part in args.tickers.split(",")]
    else:
        tickers = []
    tickers.extend(args.ticker or [])
    return normalize_tickers(tickers)


def main(argv=None):
    args = parse_args(argv)
    tickers = parse_requested_tickers(args)

    print("Financial Agent - Watchlist Triage")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    if not tickers:
        print("No tickers provided. Use --tickers or --ticker.")
        return 1

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
        result = run_watchlist(
            WatchlistRunRequest(
                tickers=tickers,
                top_n=args.top_n,
                retrieval_top_k=args.retrieval_top_k,
                force_structure=args.force_structure,
                db_path=args.db_path,
                persist_dir=args.persist_dir,
                collection_name=args.collection_name,
            ),
            vector_store,
            storage,
        )
        report_path = write_watchlist_report(
            result,
            output_dir=args.output_dir,
            debug_review=args.debug_review,
            debug_rerank=args.debug_rerank,
        )
        print(render_watchlist_summary(result))
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
