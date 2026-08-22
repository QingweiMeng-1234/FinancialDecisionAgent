"""CLI for persisted Theme Chokepoint research products."""

from __future__ import annotations

import argparse
import json

from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Theme Chokepoint research runs.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--artifacts", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("run_id")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("run_id")
    compare = commands.add_parser("compare")
    compare.add_argument("before_run_id")
    compare.add_argument("after_run_id")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("run_id")
    feedback.add_argument("object_type")
    feedback.add_argument("object_id")
    feedback.add_argument("field_name")
    feedback.add_argument("proposed_value")
    feedback.add_argument("rationale")
    feedback.add_argument("actor")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repository = ThemeChokepointRepository(args.db)
    service = PersistentResearchProductService(repository, args.artifacts)
    interface = ThemeChokepointInterface(repository, service)
    if args.command == "status":
        payload = interface.get_run_status(args.run_id)
    elif args.command == "finalize":
        payload = interface.finalize(args.run_id)
    elif args.command == "compare":
        payload = interface.compare_runs(args.before_run_id, args.after_run_id)
    else:
        payload = interface.record_feedback(
            run_id=args.run_id,
            object_type=args.object_type,
            object_id=args.object_id,
            field_name=args.field_name,
            proposed_value=args.proposed_value,
            rationale=args.rationale,
            actor=args.actor,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
