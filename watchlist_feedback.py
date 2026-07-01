#!/usr/bin/env python3
"""Minimal CLI for inserting watchlist human-review and follow-up records."""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

load_dotenv()

from event_collector.news_storage import SQLiteNewsStore
from event_collector.watchlist_triage import FollowupInput, HumanReviewInput, normalize_ticker


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Write minimal watchlist human review or follow-up feedback.")
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--run-id", required=True, help="Watchlist run ID")
    parser.add_argument("--ticker", required=True, help="Ticker to annotate")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["review", "followup"],
        help="Whether to insert a human review row or a follow-up row",
    )
    parser.add_argument("--worth-reviewing", choices=["yes", "no"])
    parser.add_argument("--evidence-specific", choices=["yes", "no"])
    parser.add_argument("--reasoning-sound", choices=["yes", "no"])
    parser.add_argument("--missed-important-name", choices=["yes", "no"])
    parser.add_argument("--follow-through-status", default=None)
    parser.add_argument("--still-worth-tracking", choices=["yes", "no"])
    parser.add_argument("--notes", default="", help="Optional notes")
    return parser.parse_args(argv)


def parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "yes"


def main(argv=None):
    args = parse_args(argv)

    print("Financial Agent - Watchlist Feedback")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()

    try:
        ticker = normalize_ticker(args.ticker)
        if args.mode == "review":
            row_id = storage.save_watchlist_human_review(
                HumanReviewInput(
                    run_id=args.run_id,
                    ticker=ticker,
                    worth_reviewing=parse_optional_bool(args.worth_reviewing),
                    evidence_specific=parse_optional_bool(args.evidence_specific),
                    reasoning_sound=parse_optional_bool(args.reasoning_sound),
                    missed_important_name=parse_optional_bool(args.missed_important_name),
                    follow_through_status=args.follow_through_status,
                    notes=args.notes,
                )
            )
            print(f"Saved human review row: {row_id}")
        else:
            row_id = storage.save_watchlist_followup(
                FollowupInput(
                    run_id=args.run_id,
                    ticker=ticker,
                    still_worth_tracking=parse_optional_bool(args.still_worth_tracking),
                    outcome_notes=args.notes,
                )
            )
            print(f"Saved follow-up row: {row_id}")
        storage.close()
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        storage.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
