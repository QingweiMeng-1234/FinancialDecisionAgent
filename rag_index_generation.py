#!/usr/bin/env python3
"""Build and verify one immutable RAG index generation without activation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
from typing import Any, Callable, Sequence


EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_REFUSED = 3
EXIT_BUILD_FAILED = 5

ROOT = Path(__file__).resolve().parent
LIVE_CHROMA_ROOT = ROOT / "chroma_data"
GENERATION_MANIFEST_NAME = "generation-input-manifest.json"
SPLITTER_ID = "fixed-character-v1"


class CliUsageError(ValueError):
    """The command line does not describe a safe build."""


class PreflightError(RuntimeError):
    """The build was refused before any output or model write."""


class BuildFailedError(RuntimeError):
    """A retained generation attempt did not verify."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


@dataclass(frozen=True)
class RuntimeModules:
    migration: Any
    generation_corpus: Any
    index_generation: Any
    builder: Any
    chroma_adapter: Any


@dataclass(frozen=True)
class BuildPreflight:
    staging_dir: Path
    source_database: Path
    content_root: Path
    generation_database: Path
    persist_root: Path
    stage_plan_id: str
    source_database_logical_sha256: str
    snapshot: Any
    candidates: tuple[Any, ...]
    chunk_config: dict[str, object]
    index_config_fingerprint: str


def _load_module(name: str, filename: str) -> ModuleType:
    module_path = ROOT / "src" / "event_collector" / filename
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load runtime module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime_modules() -> RuntimeModules:
    """Load generation modules without executing event_collector.__init__."""

    return RuntimeModules(
        migration=_load_module("_rag_build_corpus_migration", "corpus_migration.py"),
        generation_corpus=_load_module("_rag_build_generation_corpus", "generation_corpus.py"),
        index_generation=_load_module("_rag_build_index_generation", "index_generation.py"),
        builder=_load_module("_rag_build_generation_builder", "index_generation_builder.py"),
        chroma_adapter=_load_module("_rag_build_chroma_adapter", "chroma_generation_adapter.py"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = JsonArgumentParser(
        description="Build and verify a fresh RAG index generation from a verified staging corpus."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build a new verified generation without switching readers")
    build.add_argument("--staging-dir", required=True, help="Existing verified corpus staging directory")
    build.add_argument("--generation-db", required=True, help="New independent generation-control SQLite file")
    build.add_argument("--persist-root", required=True, help="New Chroma persistence directory")
    build.add_argument("--generation-id", required=True)
    build.add_argument("--collection-name", required=True)
    build.add_argument("--corpus-id", required=True)
    build.add_argument("--embedding-artifact", required=True)
    build.add_argument("--chunk-size", required=True, type=int)
    build.add_argument("--chunk-overlap", "--overlap", dest="chunk_overlap", required=True, type=int)
    build.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow the embedding loader to use the network; default is local-files-only",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runtime: RuntimeModules | None = None,
) -> int:
    try:
        args = parse_args(argv)
        modules = runtime if runtime is not None else _load_runtime_modules()
        return _run_build(modules, args)
    except (CliUsageError, PreflightError) as error:
        return _emit_error("refused", EXIT_REFUSED, error)
    except BuildFailedError as error:
        return _emit_error("build_failed", EXIT_BUILD_FAILED, error)
    except Exception as error:  # Stable machine-readable CLI boundary.
        return _emit_error("unexpected_error", EXIT_UNEXPECTED, error)


def _run_build(modules: RuntimeModules, args: argparse.Namespace) -> int:
    preflight = _preflight(modules, args)
    try:
        return _execute_build(modules, args, preflight)
    except (PreflightError, BuildFailedError):
        raise
    except Exception as error:
        raise BuildFailedError(str(error) or type(error).__name__) from error


def _execute_build(
    modules: RuntimeModules, args: argparse.Namespace, preflight: BuildPreflight
) -> int:
    manifest_path = preflight.persist_root / GENERATION_MANIFEST_NAME

    # Both output roots are created only after the complete read-only preflight.
    preflight.persist_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        preflight.persist_root.mkdir()
    except FileExistsError as error:
        raise PreflightError(
            f"Persist root appeared after preflight: {preflight.persist_root}"
        ) from error
    modules.generation_corpus.write_generation_manifest(
        preflight.snapshot, manifest_path
    )

    preflight.generation_database.parent.mkdir(parents=True, exist_ok=True)
    try:
        with preflight.generation_database.open("x+b"):
            pass
    except FileExistsError as error:
        raise PreflightError(
            "Generation database appeared after preflight: "
            f"{preflight.generation_database}"
        ) from error

    connection = sqlite3.connect(preflight.generation_database)
    try:
        modules.index_generation.initialize_schema(connection)
        dependencies = modules.chroma_adapter.create_chroma_generation_dependencies(
            persist_dir=preflight.persist_root,
            generation_id=args.generation_id,
            corpus_snapshot_id=preflight.snapshot.eligible_snapshot_fingerprint,
            embedding_artifact=args.embedding_artifact,
            index_config_fingerprint=preflight.index_config_fingerprint,
            chunk_config=preflight.chunk_config,
            allow_download=args.allow_model_download,
        )
        result = modules.builder.build_generation(
            connection,
            dependencies.collection_client,
            generation_id=args.generation_id,
            corpus_id=args.corpus_id,
            collection_name=args.collection_name,
            embedding_artifact=args.embedding_artifact,
            chunk_config=preflight.chunk_config,
            corpus_snapshot_id=preflight.snapshot.eligible_snapshot_fingerprint,
            candidates=preflight.candidates,
            splitter=_fixed_window_splitter(args.chunk_size, args.chunk_overlap),
            embedder=dependencies.embedder,
        )
        if not result.successful:
            raise BuildFailedError(result.error or "generation builder did not verify")
        if result.chunk_report is None or not result.chunk_report.valid:
            raise BuildFailedError("generation lacks a valid persisted chunk proof")
        if result.verification_report is None or not result.verification_report.valid:
            raise BuildFailedError("generation lacks a valid manifest proof")

        row = connection.execute(
            "SELECT status, corpus_snapshot_id, index_config_fingerprint "
            "FROM index_generations WHERE generation_id = ?",
            (args.generation_id,),
        ).fetchone()
        if row is None or row[0] != modules.index_generation.VERIFIED:
            raise BuildFailedError("generation control state is not verified")
        if row[1] != preflight.snapshot.eligible_snapshot_fingerprint:
            raise BuildFailedError("generation control state has the wrong corpus snapshot")
        if row[2] != preflight.index_config_fingerprint:
            raise BuildFailedError("generation control state has the wrong index configuration")
        active_pointer_count = connection.execute(
            "SELECT COUNT(*) FROM corpus_index_state"
        ).fetchone()[0]
        if active_pointer_count != 0:
            raise BuildFailedError("generation build unexpectedly changed a reader pointer")
    finally:
        connection.close()

    _emit(
        {
            "command": "build",
            "status": "verified",
            "generation_id": args.generation_id,
            "corpus_id": args.corpus_id,
            "collection_name": args.collection_name,
            "embedding_artifact": args.embedding_artifact,
            "corpus_snapshot_id": preflight.snapshot.eligible_snapshot_fingerprint,
            "stage_plan_id": preflight.stage_plan_id,
            "source_database_logical_sha256": preflight.source_database_logical_sha256,
            "index_config_fingerprint": preflight.index_config_fingerprint,
            "staging_dir": str(preflight.staging_dir),
            "source_database": str(preflight.source_database),
            "content_root": str(preflight.content_root),
            "generation_db": str(preflight.generation_database),
            "persist_root": str(preflight.persist_root),
            "generation_input_manifest": str(manifest_path),
            "counts": {
                "eligible_articles": len(preflight.candidates),
                "indexed_chunks": result.chunk_report.observed_chunk_count,
            },
            "active_generation_changed": False,
        }
    )
    return EXIT_OK


def _preflight(modules: RuntimeModules, args: argparse.Namespace) -> BuildPreflight:
    staging_dir = _existing_directory(args.staging_dir, "staging directory")
    verification = modules.migration.verify_staged_corpus(staging_dir)
    if not verification.valid:
        issues = "; ".join(str(issue) for issue in verification.issues)
        raise PreflightError(f"staged corpus verification failed: {issues or 'unknown issue'}")

    plan = modules.migration.load_plan_manifest(staging_dir / "manifest.json")
    logical_hash = _required_text(
        plan.source_database_logical_sha256, "stage source database logical hash"
    )
    plan_id = _required_text(plan.plan_id, "stage plan id")
    source_database = _existing_file(
        staging_dir / modules.migration.STAGED_DATABASE_NAME, "staged source database"
    )
    content_root = _existing_directory(
        staging_dir / modules.migration.STAGED_ARTICLES_DIR, "staged content root"
    )
    generation_database = Path(args.generation_db).resolve()
    persist_root = Path(args.persist_root).resolve()
    _guard_new_outputs(
        generation_database,
        persist_root,
        protected=(
            staging_dir,
            LIVE_CHROMA_ROOT.resolve(),
            source_database,
            content_root,
        ),
    )
    _validate_identity_arguments(args)
    chunk_config: dict[str, object] = {
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "splitter": SPLITTER_ID,
    }
    index_config_fingerprint = modules.index_generation.compute_index_config_fingerprint(
        chunk_config
    )
    snapshot = modules.generation_corpus.build_generation_corpus_snapshot(
        source_database,
        content_root,
        source_database_logical_sha256=logical_hash,
        snapshot_id=plan_id,
    )
    if snapshot.source_database_logical_sha256 != logical_hash:
        raise PreflightError("canonical snapshot lost the staged database identity")
    if snapshot.snapshot_id != plan_id:
        raise PreflightError("canonical snapshot lost the reviewed stage plan identity")
    if not snapshot.candidates:
        raise PreflightError("canonical staged corpus contains no eligible articles")
    expected_snapshot_id = modules.index_generation.compute_eligible_snapshot_fingerprint(
        (candidate.article_id, candidate.content_sha256)
        for candidate in snapshot.candidates
    )
    if snapshot.eligible_snapshot_fingerprint != expected_snapshot_id:
        raise PreflightError("canonical snapshot fingerprint is inconsistent")
    candidates = tuple(
        modules.builder.CanonicalIndexCandidate(
            candidate.article_id, candidate.content, candidate.content_sha256
        )
        for candidate in snapshot.candidates
    )
    return BuildPreflight(
        staging_dir=staging_dir,
        source_database=source_database,
        content_root=content_root,
        generation_database=generation_database,
        persist_root=persist_root,
        stage_plan_id=plan_id,
        source_database_logical_sha256=logical_hash,
        snapshot=snapshot,
        candidates=candidates,
        chunk_config=chunk_config,
        index_config_fingerprint=index_config_fingerprint,
    )


def _guard_new_outputs(
    generation_database: Path,
    persist_root: Path,
    *,
    protected: Sequence[Path],
) -> None:
    if generation_database.exists():
        raise PreflightError(f"Generation database already exists: {generation_database}")
    if persist_root.exists():
        raise PreflightError(f"Persist root already exists: {persist_root}")
    for output, label in (
        (generation_database, "generation database"),
        (persist_root, "persist root"),
    ):
        for root in protected:
            if _same_or_within(output, root):
                raise PreflightError(f"{label} must not be inside protected data: {root}")
    if _same_or_within(generation_database, persist_root) or _same_or_within(
        persist_root, generation_database
    ):
        raise PreflightError("generation database and persist root must not overlap")


def _validate_identity_arguments(args: argparse.Namespace) -> None:
    for value, label in (
        (args.generation_id, "generation id"),
        (args.collection_name, "collection name"),
        (args.corpus_id, "corpus id"),
        (args.embedding_artifact, "embedding artifact"),
    ):
        _required_text(value, label)
    if isinstance(args.chunk_size, bool) or args.chunk_size <= 0:
        raise PreflightError("chunk size must be a positive integer")
    if isinstance(args.chunk_overlap, bool) or args.chunk_overlap < 0:
        raise PreflightError("chunk overlap must be a non-negative integer")
    if args.chunk_overlap >= args.chunk_size:
        raise PreflightError("chunk overlap must be smaller than chunk size")


def _fixed_window_splitter(
    chunk_size: int, chunk_overlap: int
) -> Callable[[str], tuple[str, ...]]:
    step = chunk_size - chunk_overlap

    def split(content: str) -> tuple[str, ...]:
        if not isinstance(content, str):
            raise TypeError("canonical content must be text")
        chunks: list[str] = []
        start = 0
        while start < len(content):
            end = min(len(content), start + chunk_size)
            chunks.append(content[start:end])
            if end == len(content):
                break
            start += step
        return tuple(chunks)

    return split


def _existing_file(value: str | os.PathLike[str], label: str) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except OSError as error:
        raise PreflightError(f"{label} does not exist: {value}") from error
    if not path.is_file():
        raise PreflightError(f"{label} is not a file: {path}")
    return path


def _existing_directory(value: str | os.PathLike[str], label: str) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except OSError as error:
        raise PreflightError(f"{label} does not exist: {value}") from error
    if not path.is_dir():
        raise PreflightError(f"{label} is not a directory: {path}")
    return path


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreflightError(f"{label} must be non-empty text")
    return value


def _same_or_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _emit_error(status: str, code: int, error: Exception) -> int:
    _emit(
        {
            "status": status,
            "exit_code": code,
            "error_type": type(error).__name__,
            "error": str(error),
        }
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
