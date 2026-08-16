#!/usr/bin/env python3
"""Safe CLI for RAG corpus audit and isolated staging migration."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
from typing import Any


EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_FINDINGS = 2
EXIT_REFUSED = 3
EXIT_INPUT_CHANGED = 4
EXIT_APPLY_FAILED = 5


def _load_core() -> ModuleType:
    """Load the stdlib-only core without executing event_collector.__init__."""

    module_path = Path(__file__).resolve().parent / "src" / "event_collector" / "corpus_migration.py"
    spec = importlib.util.spec_from_file_location("rag_corpus_migration_core", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load corpus migration core: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a legacy RAG corpus and create a verified, isolated staging copy."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser("dry-run", help="Read source data and write a deterministic plan manifest")
    dry_run.add_argument("--db-path", required=True, help="Existing source SQLite database")
    dry_run.add_argument("--content-root", required=True, help="Existing legacy article-content root")
    dry_run.add_argument("--chroma-dir", default=None, help="Optional existing Chroma persistence root")
    dry_run.add_argument("--collection-name", default="news_articles", help="Chroma collection to audit")
    dry_run.add_argument(
        "--snapshot-id",
        default=None,
        help="Frozen-corpus snapshot identifier; defaults to the deterministic source fingerprint",
    )
    dry_run.add_argument("--manifest-out", required=True, help="New JSON manifest path; must not exist")

    apply = subparsers.add_parser(
        "apply",
        help="Create a new staging corpus; never mutates the source corpus",
    )
    apply.add_argument("--plan", required=True, help="Manifest produced by dry-run")
    apply.add_argument("--output-dir", required=True, help="New staging directory; must not exist")
    apply.add_argument(
        "--confirm-plan-id",
        required=True,
        help="Exact plan_id from the reviewed manifest",
    )

    verify = subparsers.add_parser("verify", help="Read and verify an existing staged corpus")
    verify.add_argument("--output-dir", required=True, help="Existing staging directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        core = _load_core()
        if args.command == "dry-run":
            return _run_dry_run(core, args)
        if args.command == "apply":
            return _run_apply(core, args)
        if args.command == "verify":
            return _run_verify(core, args)
        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:  # CLI boundary maps failures to stable machine-readable outcomes.
        core = sys.modules.get("rag_corpus_migration_core")
        if core is not None and isinstance(exc, core.MigrationSafetyError):
            return _emit_error("refused", EXIT_REFUSED, exc)
        if core is not None and isinstance(exc, core.SourceDriftError):
            return _emit_error("source_changed", EXIT_INPUT_CHANGED, exc)
        if core is not None and isinstance(exc, core.CorpusMigrationError):
            code = EXIT_APPLY_FAILED if args.command == "apply" else EXIT_INPUT_CHANGED
            return _emit_error("migration_failed", code, exc)
        if isinstance(exc, (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError)):
            return _emit_error("input_error", EXIT_INPUT_CHANGED, exc)
        return _emit_error("unexpected_error", EXIT_UNEXPECTED, exc)


def _run_dry_run(core: ModuleType, args: argparse.Namespace) -> int:
    database = Path(args.db_path).resolve(strict=True)
    content_root = Path(args.content_root).resolve(strict=True)
    chroma_dir = Path(args.chroma_dir).resolve(strict=True) if args.chroma_dir else None
    manifest_out = Path(args.manifest_out).resolve()
    _guard_manifest_output(core, manifest_out, database, content_root, chroma_dir)
    plan = core.build_corpus_migration_plan(
        database,
        content_root,
        chroma_dir=chroma_dir,
        chroma_collection_name=args.collection_name,
        snapshot_id=args.snapshot_id,
    )
    core.write_plan_manifest(plan, manifest_out)
    chroma = plan.chroma_audit
    reconciliation = plan.chroma_reconciliation
    finding_count = plan.invalid_ready_count + len(plan.orphan_files)
    if chroma is not None:
        finding_count += chroma.missing_article_id_records
    if reconciliation is not None:
        finding_count += sum(len(values) for values in reconciliation.values())
    status = "clean" if finding_count == 0 else "findings"
    _emit(
        {
            "command": "dry-run",
            "status": status,
            "plan_id": plan.plan_id,
            "snapshot_id": plan.snapshot_id,
            "manifest_schema_version": core.MANIFEST_SCHEMA_VERSION,
            "manifest_path": str(manifest_out),
            "counts": plan.to_dict()["counts"],
            "chroma": (
                {
                    "status": chroma.status,
                    "collection_name": chroma.collection_name,
                    "record_count": chroma.record_count,
                    "distinct_article_ids": len(chroma.article_ids),
                    "missing_article_id_records": chroma.missing_article_id_records,
                }
                if chroma
                else None
            ),
            "chroma_reconciliation": reconciliation,
        }
    )
    return EXIT_OK if finding_count == 0 else EXIT_FINDINGS


def _run_apply(core: ModuleType, args: argparse.Namespace) -> int:
    plan = core.load_plan_manifest(args.plan)
    if args.confirm_plan_id != plan.plan_id:
        raise core.MigrationSafetyError("--confirm-plan-id does not match the reviewed manifest")
    output = core.apply_corpus_migration(plan, args.output_dir)
    verification = core.verify_staged_corpus(output)
    if not verification.valid:
        raise core.CorpusMigrationError("Post-apply verification failed")
    _emit(
        {
            "command": "apply",
            "status": "staged",
            "plan_id": plan.plan_id,
            "snapshot_id": plan.snapshot_id,
            "manifest_schema_version": core.MANIFEST_SCHEMA_VERSION,
            "output_dir": str(output),
            "verification": _verification_payload(verification),
        }
    )
    return EXIT_OK


def _run_verify(core: ModuleType, args: argparse.Namespace) -> int:
    output = Path(args.output_dir).resolve()
    plan = core.load_plan_manifest(output / "manifest.json")
    verification = core.verify_staged_corpus(output)
    _emit(
        {
            "command": "verify",
            "status": "verified" if verification.valid else "invalid",
            "output_dir": str(output),
            "plan_id": plan.plan_id,
            "snapshot_id": plan.snapshot_id,
            "manifest_schema_version": core.MANIFEST_SCHEMA_VERSION,
            "verification": _verification_payload(verification),
        }
    )
    return EXIT_OK if verification.valid else EXIT_FINDINGS


def _guard_manifest_output(
    core: ModuleType,
    output: Path,
    database: Path,
    content_root: Path,
    chroma_dir: Path | None,
) -> None:
    if output.exists():
        raise core.MigrationSafetyError(f"Manifest output already exists: {output}")
    if output == database:
        raise core.MigrationSafetyError("Manifest output must not replace the source database")
    protected_roots = [content_root]
    if chroma_dir is not None:
        protected_roots.append(chroma_dir)
    for root in protected_roots:
        try:
            output.relative_to(root)
        except ValueError:
            continue
        raise core.MigrationSafetyError(f"Manifest output must not be inside source data: {root}")


def _verification_payload(verification: Any) -> dict[str, Any]:
    return {
        "valid": verification.valid,
        "checked_ready_articles": verification.checked_ready_articles,
        "audit_rows": verification.audit_rows,
        "issues": list(verification.issues),
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _emit_error(status: str, code: int, exc: Exception) -> int:
    _emit(
        {
            "status": status,
            "exit_code": code,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
