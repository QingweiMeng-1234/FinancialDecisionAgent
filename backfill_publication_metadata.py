#!/usr/bin/env python3
"""Default-dry-run CLI for auditable publisher publication-date backfill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from event_collector.publication_metadata_backfill import PublicationMetadataBackfill


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--allow-host", action="append", required=True, help="Exact publisher hostname; repeat per host")
    parser.add_argument("--apply", action="store_true", help="Persist receipts and guarded publisher metadata upgrades")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--article-id",
        action="append",
        type=int,
        dest="article_ids",
        help="Restrict processing to one reviewed article ID; repeat per ID",
    )
    parser.add_argument("--ready-only", action="store_true", help="Only inspect legacy rows with ready canonical content")
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--max-body-bytes", type=int, default=1_000_000)
    parser.add_argument(
        "--report-json", "--report-path", dest="report_json", type=Path, required=True,
        help="Required JSON result report path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = PublicationMetadataBackfill(
        args.db_path,
        allow_hosts=args.allow_host,
        delay_seconds=args.delay_seconds,
        timeout_seconds=args.timeout_seconds,
        max_body_bytes=args.max_body_bytes,
    )
    report = runner.run(
        dry_run=not args.apply,
        limit=args.limit,
        ready_only=args.ready_only,
        article_ids=args.article_ids,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report_path": str(args.report_json), **report.to_dict()}, ensure_ascii=False))
    return 0 if not report.failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
