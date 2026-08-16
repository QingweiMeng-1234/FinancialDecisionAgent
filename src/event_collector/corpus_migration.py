"""Read-only planning and isolated staging for legacy RAG corpus data.

The source SQLite database, legacy article directory, and optional live Chroma
directory are never modified by this module.  ``apply_corpus_migration`` only
creates a brand-new output directory after re-checking a deterministic source
fingerprint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any


MANIFEST_SCHEMA_VERSION = 2
STAGED_DATABASE_NAME = "news_articles.db"
STAGED_ARTICLES_DIR = "data/articles"

# These columns make the staged database self-describing to the canonical
# content reader.  They are added only to the SQLite backup in a staging
# directory; the source schema is never changed.
_STAGED_ARTICLE_CONTRACT_COLUMNS = {
    "active_content_sha256": "TEXT",
    "content_validation_status": "TEXT",
    "summary_content_sha256": "TEXT",
    "indexed_content_sha256": "TEXT",
    "quarantined_at": "TEXT",
    "quarantine_reason": "TEXT",
    "quarantine_source_relpath": "TEXT",
    "quarantine_expected_sha256": "TEXT",
    "quarantine_observed_sha256": "TEXT",
    "quarantine_plan_id": "TEXT",
}

READY_VALID = "ready_valid"
READY_MISSING_CONTENT = "ready_missing_content"
READY_MISSING_HASH = "ready_missing_hash"
READY_HASH_MISMATCH = "ready_hash_mismatch"
READY_UNREADABLE_CONTENT = "ready_unreadable_content"
NON_READY = "non_ready"
NON_READY_WITH_CONTENT = "non_ready_with_content"
NON_READY_UNREADABLE_CONTENT = "non_ready_unreadable_content"


class CorpusMigrationError(RuntimeError):
    """Base exception for a migration that must not alter its source."""


class MigrationSafetyError(CorpusMigrationError):
    """Raised when an output location or path is unsafe."""


class SourceDriftError(CorpusMigrationError):
    """Raised when source DB or canonical legacy content changed after planning."""


@dataclass(frozen=True)
class ChromaAudit:
    """Raw SQLite Chroma inspection, always performed from a disposable copy."""

    status: str
    collection_name: str
    record_count: int = 0
    article_ids: tuple[int, ...] = ()
    missing_article_id_records: int = 0
    content_hash_metadata: tuple[tuple[int, tuple[str | None, ...]], ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "collection_name": self.collection_name,
            "record_count": self.record_count,
            "article_ids": list(self.article_ids),
            "missing_article_id_records": self.missing_article_id_records,
            "content_hash_metadata": [
                {"article_id": article_id, "hashes": list(hashes)}
                for article_id, hashes in self.content_hash_metadata
            ],
            "error": self.error,
        }


@dataclass(frozen=True)
class MigrationEntry:
    """One source article's deterministic audit classification."""

    article_id: int
    source_content_status: str
    source_index_status: str
    source_content_path: str | None
    source_relpath: str | None
    expected_sha256: str | None
    observed_sha256: str | None
    classification: str
    detail: str | None = None

    @property
    def staged_relpath(self) -> str | None:
        if self.classification != READY_VALID or not self.expected_sha256:
            return None
        # Stored relative to the configured content root (the stage's data/articles directory).
        return f"{self.article_id}/{self.expected_sha256}.txt"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["staged_relpath"] = self.staged_relpath
        return payload


@dataclass(frozen=True)
class OrphanFile:
    """A safe source-root file not selected by any article entry."""

    relpath: str
    observed_sha256: str | None
    classification: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorpusMigrationPlan:
    """A reproducible plan.  It contains no timestamp so JSON is deterministic."""

    source_database: str
    source_content_root: str
    source_database_logical_sha256: str
    source_fingerprint: str
    snapshot_id: str
    entries: tuple[MigrationEntry, ...]
    orphan_files: tuple[OrphanFile, ...]
    plan_id: str
    chroma_audit: ChromaAudit | None = None

    @property
    def valid_ready_count(self) -> int:
        return sum(entry.classification == READY_VALID for entry in self.entries)

    @property
    def invalid_ready_count(self) -> int:
        return sum(
            entry.source_content_status == "ready" and entry.classification != READY_VALID
            for entry in self.entries
        )

    @property
    def chroma_reconciliation(self) -> dict[str, Any] | None:
        if self.chroma_audit is None:
            return None
        return _build_chroma_reconciliation(self.entries, self.chroma_audit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source_database": self.source_database,
            "source_content_root": self.source_content_root,
            "source_database_logical_sha256": self.source_database_logical_sha256,
            "source_fingerprint": self.source_fingerprint,
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "counts": {
                "articles": len(self.entries),
                "valid_ready": self.valid_ready_count,
                "invalid_ready": self.invalid_ready_count,
                "orphan_files": len(self.orphan_files),
            },
            "entries": [entry.to_dict() for entry in self.entries],
            "orphan_files": [orphan.to_dict() for orphan in self.orphan_files],
            "chroma_audit": self.chroma_audit.to_dict() if self.chroma_audit else None,
            "chroma_reconciliation": self.chroma_reconciliation,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"


@dataclass(frozen=True)
class StagedCorpusVerification:
    valid: bool
    checked_ready_articles: int
    audit_rows: int
    issues: tuple[str, ...]


def build_corpus_migration_plan(
    source_database: str | os.PathLike[str],
    source_content_root: str | os.PathLike[str] | None = None,
    *,
    chroma_dir: str | os.PathLike[str] | None = None,
    chroma_collection_name: str = "news_articles",
    snapshot_id: str | None = None,
) -> CorpusMigrationPlan:
    """Audit a legacy corpus without writing to any supplied source path."""

    database = Path(source_database).resolve(strict=True)
    content_root = Path(source_content_root or database.parent / "data" / "articles").resolve()
    if not database.is_file():
        raise MigrationSafetyError(f"Source database is not a file: {database}")
    if not content_root.is_dir():
        raise MigrationSafetyError(f"Source content root is not a directory: {content_root}")

    rows, database_logical_sha256 = _read_article_rows_and_logical_hash(database)
    entries = tuple(_audit_row(row, content_root) for row in rows)
    referenced_relpaths = {entry.source_relpath for entry in entries if entry.source_relpath}
    orphan_files = _scan_orphan_files(content_root, referenced_relpaths)
    fingerprint_payload = {
        "database_logical_sha256": database_logical_sha256,
        "entries": [
            {
                "article_id": entry.article_id,
                "source_content_status": entry.source_content_status,
                "source_index_status": entry.source_index_status,
                "expected_sha256": entry.expected_sha256,
                "observed_sha256": entry.observed_sha256,
                "classification": entry.classification,
                "source_relpath": entry.source_relpath,
            }
            for entry in entries
        ],
        "orphan_files": [orphan.to_dict() for orphan in orphan_files],
    }
    source_fingerprint = _hash_bytes(_canonical_json(fingerprint_payload).encode("utf-8"))
    normalized_snapshot_id = snapshot_id or source_fingerprint
    if not isinstance(normalized_snapshot_id, str) or not normalized_snapshot_id.strip():
        raise MigrationSafetyError("snapshot_id must be a non-empty string")
    plan_id = _compute_plan_id(
        str(database), str(content_root), source_fingerprint, normalized_snapshot_id, entries, orphan_files
    )
    return CorpusMigrationPlan(
        source_database=str(database),
        source_content_root=str(content_root),
        source_database_logical_sha256=database_logical_sha256,
        source_fingerprint=source_fingerprint,
        snapshot_id=normalized_snapshot_id,
        entries=entries,
        orphan_files=orphan_files,
        plan_id=plan_id,
        chroma_audit=(
            audit_chroma_snapshot(chroma_dir, collection_name=chroma_collection_name)
            if chroma_dir
            else None
        ),
    )


def write_plan_manifest(plan: CorpusMigrationPlan, path: str | os.PathLike[str]) -> Path:
    """Write a deterministic plan manifest without overwriting an existing file."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(plan.to_json())
    return destination


def load_plan_manifest(path: str | os.PathLike[str]) -> CorpusMigrationPlan:
    """Load a persisted plan with strict type/schema/integrity validation."""

    manifest_path = Path(path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationSafetyError(f"Could not load manifest: {type(exc).__name__}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise MigrationSafetyError("Unsupported or missing manifest schema_version")
    required_keys = {
        "schema_version", "source_database", "source_content_root", "source_database_logical_sha256",
        "source_fingerprint", "plan_id",
        "snapshot_id", "counts", "entries", "orphan_files", "chroma_audit", "chroma_reconciliation",
    }
    if set(raw) != required_keys:
        raise MigrationSafetyError("Manifest has an unsupported field set")
    source_database = _required_string(raw, "source_database")
    source_content_root = _required_string(raw, "source_content_root")
    source_database_logical_sha256 = _required_sha256(raw, "source_database_logical_sha256")
    source_fingerprint = _required_sha256(raw, "source_fingerprint")
    snapshot_id = _required_string(raw, "snapshot_id")
    supplied_plan_id = _required_sha256(raw, "plan_id")
    entries_raw = raw.get("entries")
    orphan_raw = raw.get("orphan_files")
    if not isinstance(entries_raw, list) or not isinstance(orphan_raw, list):
        raise MigrationSafetyError("Manifest entries and orphan_files must be lists")
    entries = tuple(_parse_entry(value) for value in entries_raw)
    orphan_files = tuple(_parse_orphan(value) for value in orphan_raw)
    if [entry.article_id for entry in entries] != sorted({entry.article_id for entry in entries}):
        raise MigrationSafetyError("Manifest article IDs must be unique and sorted")
    if [orphan.relpath for orphan in orphan_files] != sorted({orphan.relpath for orphan in orphan_files}):
        raise MigrationSafetyError("Manifest orphan paths must be unique and sorted")
    calculated_plan_id = _compute_plan_id(
        source_database, source_content_root, source_fingerprint, snapshot_id, entries, orphan_files
    )
    if calculated_plan_id != supplied_plan_id:
        raise MigrationSafetyError("Manifest plan_id does not match its contents")
    counts = raw.get("counts")
    expected_counts = {
        "articles": len(entries),
        "valid_ready": sum(entry.classification == READY_VALID for entry in entries),
        "invalid_ready": sum(
            entry.source_content_status == "ready" and entry.classification != READY_VALID
            for entry in entries
        ),
        "orphan_files": len(orphan_files),
    }
    if counts != expected_counts:
        raise MigrationSafetyError("Manifest counts do not match its entries")
    chroma_audit = _parse_chroma_audit(raw.get("chroma_audit"))
    expected_reconciliation = (
        _build_chroma_reconciliation(entries, chroma_audit) if chroma_audit is not None else None
    )
    if raw.get("chroma_reconciliation") != expected_reconciliation:
        raise MigrationSafetyError("Manifest Chroma reconciliation does not match its inputs")
    return CorpusMigrationPlan(
        source_database=source_database,
        source_content_root=source_content_root,
        source_database_logical_sha256=source_database_logical_sha256,
        source_fingerprint=source_fingerprint,
        snapshot_id=snapshot_id,
        entries=entries,
        orphan_files=orphan_files,
        plan_id=supplied_plan_id,
        chroma_audit=chroma_audit,
    )


def apply_corpus_migration(
    plan: CorpusMigrationPlan,
    output_dir: str | os.PathLike[str],
) -> Path:
    """Create a fully isolated staged corpus, never changing the source corpus.

    Every valid ready article is copied into immutable hash-addressed storage.
    The staged DB deliberately marks its index state pending because a legacy
    Chroma index is not imported into this new generation.
    """

    output = Path(output_dir).resolve()
    if output.exists():
        raise MigrationSafetyError(f"Refusing to use existing output directory: {output}")
    if _is_within(output, Path(plan.source_content_root)):
        raise MigrationSafetyError("Output directory must not be inside the source content root")
    output.parent.mkdir(parents=True, exist_ok=True)

    current = build_corpus_migration_plan(
        plan.source_database,
        plan.source_content_root,
        snapshot_id=plan.snapshot_id,
    )
    if (
        current.source_fingerprint != plan.source_fingerprint
        or current.source_database_logical_sha256 != plan.source_database_logical_sha256
        or current.plan_id != plan.plan_id
    ):
        raise SourceDriftError("Source corpus changed after planning; generate a new plan before apply")

    staging = Path(tempfile.mkdtemp(prefix=".corpus-stage-", dir=str(output.parent))).resolve()
    try:
        staged_database = staging / STAGED_DATABASE_NAME
        _sqlite_backup(Path(plan.source_database), staged_database)
        if _sqlite_logical_sha256(staged_database) != plan.source_database_logical_sha256:
            raise SourceDriftError(
                "Source corpus changed while creating the SQLite backup; generate a new plan before apply"
            )
        articles_root = staging / STAGED_ARTICLES_DIR
        articles_root.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(staged_database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_staged_articles_contract(connection)
            _create_staged_audit_table(connection)
            # A stage never imports live Chroma state, even for source rows that were pending.
            connection.execute(
                """
                UPDATE articles
                SET index_status = 'pending', content_path = NULL,
                    active_content_sha256 = NULL,
                    content_validation_status = 'pending',
                    indexed_content_sha256 = NULL,
                    summary_status = 'pending', summary = NULL,
                    summary_content_sha256 = NULL,
                    quarantined_at = NULL, quarantine_reason = NULL,
                    quarantine_source_relpath = NULL, quarantine_expected_sha256 = NULL,
                    quarantine_observed_sha256 = NULL, quarantine_plan_id = NULL
                """
            )
            for entry in plan.entries:
                if entry.classification == READY_VALID:
                    source = _source_path_from_entry(entry, Path(plan.source_content_root))
                    target = _safe_staged_target(articles_root, entry)
                    _copy_as_canonical_utf8(source, target)
                    connection.execute(
                        """
                        UPDATE articles
                        SET content_path = ?, content_sha256 = ?,
                            active_content_sha256 = ?, content_status = 'ready',
                            content_validation_status = 'verified'
                        WHERE id = ?
                        """,
                        (
                            entry.staged_relpath,
                            entry.expected_sha256,
                            entry.expected_sha256,
                            entry.article_id,
                        ),
                    )
                elif entry.source_content_status == "ready":
                    connection.execute(
                        """
                        UPDATE articles
                        SET content_path = NULL, content_sha256 = NULL,
                            active_content_sha256 = NULL, content_status = 'failed',
                            content_validation_status = 'quarantined',
                            summary_status = 'pending', summary = NULL,
                            quarantined_at = CURRENT_TIMESTAMP,
                            quarantine_reason = ?, quarantine_source_relpath = ?,
                            quarantine_expected_sha256 = ?, quarantine_observed_sha256 = ?,
                            quarantine_plan_id = ?
                        WHERE id = ?
                        """,
                        (
                            _quarantine_reason(entry),
                            entry.source_relpath,
                            entry.expected_sha256,
                            entry.observed_sha256,
                            plan.plan_id,
                            entry.article_id,
                        ),
                    )
                else:
                    # Legacy absolute locations are never allowed to leak into a portable stage.
                    connection.execute("UPDATE articles SET content_path = NULL WHERE id = ?", (entry.article_id,))
                connection.execute(
                    """
                    INSERT INTO corpus_migration_audit
                    (article_id, classification, expected_sha256, observed_sha256, source_relpath, detail, plan_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.article_id,
                        entry.classification,
                        entry.expected_sha256,
                        entry.observed_sha256,
                        entry.source_relpath,
                        entry.detail,
                        plan.plan_id,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        write_plan_manifest(plan, staging / "manifest.json")
        verification = verify_staged_corpus(staging)
        if not verification.valid:
            raise CorpusMigrationError("Staged corpus verification failed: " + "; ".join(verification.issues))
        os.replace(staging, output)
        return output
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_staged_corpus(output_dir: str | os.PathLike[str]) -> StagedCorpusVerification:
    """Verify staged DB paths and content hashes without modifying the stage."""

    output = Path(output_dir).resolve(strict=True)
    database = output / STAGED_DATABASE_NAME
    manifest = output / "manifest.json"
    issues: list[str] = []
    if not database.is_file():
        return StagedCorpusVerification(False, 0, 0, ("missing staged database",))
    if not manifest.is_file():
        return StagedCorpusVerification(False, 0, 0, ("missing manifest",))
    try:
        plan = load_plan_manifest(manifest)
    except CorpusMigrationError as exc:
        return StagedCorpusVerification(False, 0, 0, (f"invalid manifest: {exc}",))

    connection = _readonly_connection(database)
    try:
        _require_columns(
            connection,
            {
                "id", "content_path", "content_sha256", "active_content_sha256",
                "content_status", "content_validation_status", "index_status",
                "summary_status", "summary", "summary_content_sha256", "indexed_content_sha256",
                "quarantined_at", "quarantine_reason", "quarantine_source_relpath",
                "quarantine_expected_sha256", "quarantine_observed_sha256", "quarantine_plan_id",
            },
        )
        rows = connection.execute(
            """
            SELECT id, content_path, content_sha256, active_content_sha256,
                   content_status, content_validation_status, index_status,
                   summary_status, summary, summary_content_sha256, indexed_content_sha256,
                   quarantined_at, quarantine_reason, quarantine_source_relpath,
                   quarantine_expected_sha256, quarantine_observed_sha256, quarantine_plan_id
            FROM articles ORDER BY id
            """
        ).fetchall()
        audit_rows = connection.execute(
            """
            SELECT article_id, classification, expected_sha256, observed_sha256,
                   source_relpath, detail, plan_id
            FROM corpus_migration_audit ORDER BY article_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        return StagedCorpusVerification(False, 0, 0, (f"staged schema error: {exc}",))
    finally:
        connection.close()

    rows_by_id = {int(row["id"]): row for row in rows}
    expected_ids = {entry.article_id for entry in plan.entries}
    if set(rows_by_id) != expected_ids:
        issues.append("staged article IDs do not match manifest entries")
    if any(str(row["index_status"] or "").lower() != "pending" for row in rows):
        issues.append("all staged rows must have index_status=pending")
    if any(row["indexed_content_sha256"] is not None for row in rows):
        issues.append("all staged rows must have indexed_content_sha256=NULL")
    if any(str(row["summary_status"] or "").lower() != "pending" for row in rows):
        issues.append("all staged rows must have summary_status=pending")
    if any(row["summary"] is not None for row in rows):
        issues.append("all staged rows must have summary=NULL")
    if any(row["summary_content_sha256"] is not None for row in rows):
        issues.append("all staged rows must have summary_content_sha256=NULL")

    expected_audit = {
        entry.article_id: (
            entry.classification,
            entry.expected_sha256,
            entry.observed_sha256,
            entry.source_relpath,
            entry.detail,
            plan.plan_id,
        )
        for entry in plan.entries
    }
    actual_audit = {
        int(row["article_id"]): (
            row["classification"], row["expected_sha256"], row["observed_sha256"],
            row["source_relpath"], row["detail"], row["plan_id"],
        )
        for row in audit_rows
    }
    if actual_audit != expected_audit:
        issues.append("staged migration audit does not match manifest plan")

    valid_ready_ids = {entry.article_id for entry in plan.entries if entry.classification == READY_VALID}
    entries_by_id = {entry.article_id: entry for entry in plan.entries}
    staged_ready_ids = {article_id for article_id, row in rows_by_id.items() if row["content_status"] == "ready"}
    if staged_ready_ids != valid_ready_ids:
        issues.append("staged ready set does not match manifest valid-ready set")
    invalid_ready_ids = {
        entry.article_id
        for entry in plan.entries
        if entry.source_content_status == "ready" and entry.classification != READY_VALID
    }
    for article_id in invalid_ready_ids:
        row = rows_by_id.get(article_id)
        entry = entries_by_id[article_id]
        if (
            row is None
            or row["content_status"] != "failed"
            or row["content_path"] is not None
            or row["active_content_sha256"] is not None
            or row["content_validation_status"] != "quarantined"
        ):
            issues.append(f"article {article_id}: invalid-ready row was not quarantined")
            continue
        if not isinstance(row["quarantined_at"], str) or not row["quarantined_at"]:
            issues.append(f"article {article_id}: quarantine timestamp is missing")
        if row["quarantine_reason"] != _quarantine_reason(entry):
            issues.append(f"article {article_id}: quarantine reason differs from manifest")
        if row["quarantine_source_relpath"] != entry.source_relpath:
            issues.append(f"article {article_id}: quarantine source differs from manifest")
        if row["quarantine_expected_sha256"] != entry.expected_sha256:
            issues.append(f"article {article_id}: quarantine expected hash differs from manifest")
        if row["quarantine_observed_sha256"] != entry.observed_sha256:
            issues.append(f"article {article_id}: quarantine observed hash differs from manifest")
        if row["quarantine_plan_id"] != plan.plan_id:
            issues.append(f"article {article_id}: quarantine plan differs from manifest")

    expected_staged_paths: set[Path] = set()
    for article_id in valid_ready_ids:
        row = rows_by_id.get(article_id)
        if row is None:
            issues.append(f"article {article_id}: missing staged row")
            continue
        entry = entries_by_id[article_id]
        article_id = int(row["id"])
        relpath = row["content_path"]
        expected = row["content_sha256"]
        active = row["active_content_sha256"]
        if (
            relpath != entry.staged_relpath
            or expected != entry.expected_sha256
            or active != entry.expected_sha256
            or row["content_validation_status"] != "verified"
        ):
            issues.append(f"article {article_id}: staged metadata differs from manifest")
            continue
        if any(
            row[column] is not None
            for column in (
                "quarantined_at",
                "quarantine_reason",
                "quarantine_source_relpath",
                "quarantine_expected_sha256",
                "quarantine_observed_sha256",
                "quarantine_plan_id",
            )
        ):
            issues.append(f"article {article_id}: valid-ready row contains quarantine evidence")
            continue
        if not isinstance(relpath, str) or not relpath or Path(relpath).is_absolute() or ".." in Path(relpath).parts:
            issues.append(f"article {article_id}: invalid relative content path")
            continue
        candidate = (output / STAGED_ARTICLES_DIR / relpath).resolve()
        if not _is_within(candidate, output / STAGED_ARTICLES_DIR):
            issues.append(f"article {article_id}: content path escapes articles root")
            continue
        expected_staged_paths.add(candidate)
        if not candidate.is_file():
            issues.append(f"article {article_id}: missing staged content")
            continue
        try:
            observed = _content_sha256_from_file(candidate)
        except (OSError, UnicodeError) as exc:
            issues.append(f"article {article_id}: unreadable staged content ({type(exc).__name__})")
            continue
        if observed != expected or observed != active:
            issues.append(f"article {article_id}: staged hash mismatch")
    articles_root = output / STAGED_ARTICLES_DIR
    actual_staged_paths = {
        path.resolve()
        for path in articles_root.rglob("*")
        if path.is_file() and _is_within(path.resolve(), articles_root)
    } if articles_root.is_dir() else set()
    if actual_staged_paths != expected_staged_paths:
        issues.append("orphan or missing staged content files")
    return StagedCorpusVerification(not issues, len(valid_ready_ids), len(audit_rows), tuple(issues))


def audit_chroma_snapshot(
    chroma_dir: str | os.PathLike[str], *, collection_name: str = "news_articles"
) -> ChromaAudit:
    """Audit one Chroma collection via raw SQLite, never via an embedding client."""

    source = Path(chroma_dir).resolve(strict=True)
    if not source.is_dir():
        raise MigrationSafetyError(f"Chroma path is not a directory: {source}")
    with tempfile.TemporaryDirectory(prefix="corpus-chroma-audit-") as temporary:
        snapshot = Path(temporary) / "chroma_snapshot"
        shutil.copytree(source, snapshot)
        database = snapshot / "chroma.sqlite3"
        if not database.is_file():
            raise MigrationSafetyError("Unsupported Chroma snapshot: chroma.sqlite3 is missing")
        connection = _readonly_connection(database)
        try:
            connection.execute("PRAGMA query_only = ON")
            _require_chroma_schema(connection)
            collection = connection.execute(
                "SELECT id FROM collections WHERE name = ?", (collection_name,)
            ).fetchone()
            if collection is None:
                raise MigrationSafetyError(f"Chroma collection not found: {collection_name}")
            segment_ids = [
                row["id"]
                for row in connection.execute("SELECT id FROM segments WHERE collection = ?", (collection["id"],))
            ]
            if not segment_ids:
                return ChromaAudit(status="ok", collection_name=collection_name)
            placeholders = ",".join("?" for _ in segment_ids)
            metadata_rows = connection.execute(
                f"""
                SELECT e.id AS record_id,
                       MAX(CASE WHEN m.key = 'article_id' THEN COALESCE(CAST(m.int_value AS TEXT), m.string_value) END) AS article_id,
                       MAX(CASE WHEN m.key = 'content_sha256' THEN m.string_value END) AS content_sha256
                FROM embeddings e
                LEFT JOIN embedding_metadata m ON m.id = e.id
                WHERE e.segment_id IN ({placeholders})
                GROUP BY e.id
                ORDER BY e.id
                """,
                tuple(segment_ids),
            ).fetchall()
        finally:
            connection.close()
    article_hashes: dict[int, set[str | None]] = {}
    missing_article_id_records = 0
    for row in metadata_rows:
        raw_id = row["article_id"]
        try:
            article_id = int(str(raw_id)) if raw_id is not None else None
        except ValueError:
            article_id = None
        if article_id is None or article_id < 1:
            missing_article_id_records += 1
            continue
        article_hashes.setdefault(article_id, set()).add(row["content_sha256"])
    return ChromaAudit(
        status="ok",
        collection_name=collection_name,
        record_count=len(metadata_rows),
        article_ids=tuple(sorted(article_hashes)),
        missing_article_id_records=missing_article_id_records,
        content_hash_metadata=tuple(
            (article_id, tuple(sorted(hashes, key=lambda value: "" if value is None else value)))
            for article_id, hashes in sorted(article_hashes.items())
        ),
    )


def _read_article_rows_and_logical_hash(database: Path) -> tuple[list[sqlite3.Row], str]:
    """Read the audit rows and full SQLite logical state from one snapshot.

    A raw ``.db`` file hash is not sufficient in WAL mode: committed changes
    can exist only in ``-wal``.  Keeping the row audit and dump in the same
    read transaction binds a plan to one consistent logical database.
    """

    connection = _readonly_connection(database)
    try:
        connection.execute("BEGIN")
        _require_columns(
            connection,
            {"id", "content_path", "content_sha256", "content_status", "index_status"},
        )
        rows = connection.execute(
            """
            SELECT id, content_path, content_sha256, content_status, index_status
            FROM articles ORDER BY id
            """
        ).fetchall()
        return rows, _sqlite_logical_sha256_from_connection(connection)
    finally:
        connection.close()


def _sqlite_logical_sha256(database: Path) -> str:
    """Hash a read-only SQLite snapshot's schema and table contents.

    ``iterdump`` reads SQLite's logical view, including committed WAL pages,
    rather than only bytes in the main database file.
    """

    connection = _readonly_connection(database)
    try:
        connection.execute("BEGIN")
        return _sqlite_logical_sha256_from_connection(connection)
    finally:
        connection.close()


def _sqlite_logical_sha256_from_connection(connection: sqlite3.Connection) -> str:
    digest = sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _audit_row(row: sqlite3.Row, content_root: Path) -> MigrationEntry:
    article_id = int(row["id"])
    if article_id < 1:
        raise MigrationSafetyError(f"Unsafe article id: {article_id!r}")
    content_status = str(row["content_status"] or "").strip().lower()
    index_status = str(row["index_status"] or "").strip().lower()
    stored_path = row["content_path"]
    stored_path = str(stored_path) if stored_path is not None else None
    source = _resolve_legacy_content_path(content_root, article_id, stored_path)
    source_relpath = _relative_posix(source, content_root) if source is not None else None
    observed: str | None = None
    read_error: str | None = None
    if source is not None:
        try:
            observed = _content_sha256_from_file(source)
        except (OSError, UnicodeError) as exc:
            read_error = type(exc).__name__
    if content_status != "ready":
        classification = NON_READY
        if read_error:
            classification = NON_READY_UNREADABLE_CONTENT
        elif observed is not None:
            classification = NON_READY_WITH_CONTENT
        return MigrationEntry(
            article_id, content_status, index_status, stored_path, source_relpath,
            row["content_sha256"], observed, classification, read_error,
        )
    expected = str(row["content_sha256"] or "").strip() or None
    if expected is None:
        return MigrationEntry(
            article_id, content_status, index_status, stored_path, source_relpath,
            None, observed, READY_MISSING_HASH, read_error,
        )
    if source is None:
        return MigrationEntry(
            article_id, content_status, index_status, stored_path, None,
            expected, None, READY_MISSING_CONTENT,
        )
    if read_error:
        return MigrationEntry(
            article_id, content_status, index_status, stored_path, source_relpath,
            expected, None, READY_UNREADABLE_CONTENT, read_error,
        )
    classification = READY_VALID if observed == expected else READY_HASH_MISMATCH
    return MigrationEntry(
        article_id, content_status, index_status, stored_path, _relative_posix(source, content_root),
        expected, observed, classification,
    )


def _resolve_legacy_content_path(content_root: Path, article_id: int, stored_path: str | None) -> Path | None:
    """Resolve only paths contained in content_root; DB strings never grant access."""

    candidates: list[Path] = []
    if stored_path:
        supplied = Path(stored_path)
        candidate = supplied.resolve() if supplied.is_absolute() else (content_root / supplied).resolve()
        if _is_within(candidate, content_root):
            candidates.append(candidate)
    # A known-safe stored relative/version path wins over the old flat-file convention.
    candidates.append(content_root / f"{article_id}.txt")
    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_within(resolved, content_root) and resolved.is_file():
            return resolved
    return None


def _scan_orphan_files(content_root: Path, referenced_relpaths: set[str]) -> tuple[OrphanFile, ...]:
    orphans: list[OrphanFile] = []
    for candidate in sorted(content_root.rglob("*"), key=lambda path: path.as_posix()):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not _is_within(resolved, content_root):
            # Do not follow a source-root symlink outside the explicitly authorized root.
            continue
        relpath = _relative_posix(resolved, content_root)
        if relpath in referenced_relpaths:
            continue
        try:
            observed = _content_sha256_from_file(resolved)
            orphans.append(OrphanFile(relpath, observed, "orphan_file"))
        except (OSError, UnicodeError) as exc:
            orphans.append(OrphanFile(relpath, None, "orphan_unreadable_file", type(exc).__name__))
    return tuple(orphans)


def _source_path_from_entry(entry: MigrationEntry, content_root: Path) -> Path:
    if not entry.source_relpath:
        raise MigrationSafetyError(f"Valid entry {entry.article_id} has no safe source path")
    source = (content_root / entry.source_relpath).resolve()
    if not _is_within(source, content_root) or not source.is_file():
        raise SourceDriftError(f"Source content disappeared or escaped root for article {entry.article_id}")
    if _content_sha256_from_file(source) != entry.expected_sha256:
        raise SourceDriftError(f"Source content changed for article {entry.article_id}")
    return source


def _safe_staged_target(articles_root: Path, entry: MigrationEntry) -> Path:
    if not entry.expected_sha256 or len(entry.expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in entry.expected_sha256.lower()
    ):
        raise MigrationSafetyError(f"Unsafe SHA-256 for article {entry.article_id}")
    target = (articles_root / str(entry.article_id) / f"{entry.expected_sha256}.txt").resolve()
    if not _is_within(target, articles_root):
        raise MigrationSafetyError("Staged target escaped articles root")
    return target


def _copy_as_canonical_utf8(source: Path, target: Path) -> None:
    text = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection = _readonly_connection(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _create_staged_audit_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus_migration_audit (
            article_id INTEGER PRIMARY KEY,
            classification TEXT NOT NULL,
            expected_sha256 TEXT,
            observed_sha256 TEXT,
            source_relpath TEXT,
            detail TEXT,
            plan_id TEXT NOT NULL
        )
        """
    )


def _ensure_staged_articles_contract(connection: sqlite3.Connection) -> None:
    """Add canonical-content and quarantine fields to the staged DB only."""

    existing = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
    for column, definition in _STAGED_ARTICLE_CONTRACT_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")


def _quarantine_reason(entry: MigrationEntry) -> str:
    """Stable, manifest-derived reason persisted with an invalid ready row."""

    return f"source_ready_{entry.classification}" + (f":{entry.detail}" if entry.detail else "")


def _compute_plan_id(
    source_database: str,
    source_content_root: str,
    source_fingerprint: str,
    snapshot_id: str,
    entries: tuple[MigrationEntry, ...],
    orphan_files: tuple[OrphanFile, ...],
) -> str:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_database": source_database,
        "source_content_root": source_content_root,
        "source_fingerprint": source_fingerprint,
        "snapshot_id": snapshot_id,
        "entries": [entry.to_dict() for entry in entries],
        "orphan_files": [orphan.to_dict() for orphan in orphan_files],
    }
    return _hash_bytes(_canonical_json(payload).encode("utf-8"))


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MigrationSafetyError(f"Manifest {key} must be a non-empty string")
    return value


def _required_sha256(payload: dict[str, Any], key: str) -> str:
    value = _required_string(payload, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise MigrationSafetyError(f"Manifest {key} must be a SHA-256 hex string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MigrationSafetyError(f"Manifest {label} must be a string or null")
    return value


def _parse_entry(value: Any) -> MigrationEntry:
    if not isinstance(value, dict):
        raise MigrationSafetyError("Manifest entry must be an object")
    article_id = value.get("article_id")
    if isinstance(article_id, bool) or not isinstance(article_id, int) or article_id < 1:
        raise MigrationSafetyError("Manifest entry article_id must be a positive integer")
    classification = _required_string(value, "classification")
    allowed = {
        READY_VALID,
        READY_MISSING_CONTENT,
        READY_MISSING_HASH,
        READY_HASH_MISMATCH,
        READY_UNREADABLE_CONTENT,
        NON_READY,
        NON_READY_WITH_CONTENT,
        NON_READY_UNREADABLE_CONTENT,
    }
    if classification not in allowed:
        raise MigrationSafetyError(f"Unsupported manifest entry classification: {classification}")
    entry = MigrationEntry(
        article_id=article_id,
        source_content_status=_required_string(value, "source_content_status"),
        source_index_status=_required_string(value, "source_index_status"),
        source_content_path=_optional_string(value.get("source_content_path"), "source_content_path"),
        source_relpath=_optional_string(value.get("source_relpath"), "source_relpath"),
        expected_sha256=_optional_string(value.get("expected_sha256"), "expected_sha256"),
        observed_sha256=_optional_string(value.get("observed_sha256"), "observed_sha256"),
        classification=classification,
        detail=_optional_string(value.get("detail"), "detail"),
    )
    if entry.expected_sha256 is not None:
        _validate_sha256(entry.expected_sha256, "entry expected_sha256")
    if entry.observed_sha256 is not None:
        _validate_sha256(entry.observed_sha256, "entry observed_sha256")
    supplied_staged_relpath = value.get("staged_relpath")
    if supplied_staged_relpath != entry.staged_relpath:
        raise MigrationSafetyError("Manifest entry staged_relpath does not match its immutable identity")
    if entry.source_relpath and (Path(entry.source_relpath).is_absolute() or ".." in Path(entry.source_relpath).parts):
        raise MigrationSafetyError("Manifest source_relpath is unsafe")
    return entry


def _parse_orphan(value: Any) -> OrphanFile:
    if not isinstance(value, dict):
        raise MigrationSafetyError("Manifest orphan file must be an object")
    relpath = _required_string(value, "relpath")
    if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        raise MigrationSafetyError("Manifest orphan relpath is unsafe")
    classification = _required_string(value, "classification")
    if classification not in {"orphan_file", "orphan_unreadable_file"}:
        raise MigrationSafetyError("Unsupported manifest orphan classification")
    observed = _optional_string(value.get("observed_sha256"), "orphan observed_sha256")
    if observed is not None:
        _validate_sha256(observed, "orphan observed_sha256")
    return OrphanFile(relpath, observed, classification, _optional_string(value.get("detail"), "orphan detail"))


def _parse_chroma_audit(value: Any) -> ChromaAudit | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MigrationSafetyError("Manifest chroma_audit must be an object or null")
    status = _required_string(value, "status")
    if status != "ok":
        raise MigrationSafetyError("Manifest chroma_audit must be an auditable successful result")
    collection_name = _required_string(value, "collection_name")
    record_count = value.get("record_count")
    missing_count = value.get("missing_article_id_records")
    article_ids = value.get("article_ids")
    hashes = value.get("content_hash_metadata")
    if (
        isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0
        or isinstance(missing_count, bool) or not isinstance(missing_count, int) or missing_count < 0
        or not isinstance(article_ids, list) or not isinstance(hashes, list)
    ):
        raise MigrationSafetyError("Manifest chroma_audit has invalid count fields")
    parsed_ids: list[int] = []
    for item in article_ids:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise MigrationSafetyError("Manifest chroma_audit article_ids must be positive integers")
        parsed_ids.append(item)
    if parsed_ids != sorted(set(parsed_ids)):
        raise MigrationSafetyError("Manifest chroma_audit article_ids must be unique and sorted")
    parsed_hashes: list[tuple[int, tuple[str | None, ...]]] = []
    for item in hashes:
        if not isinstance(item, dict) or set(item) != {"article_id", "hashes"}:
            raise MigrationSafetyError("Manifest chroma hash metadata has invalid shape")
        article_id = item["article_id"]
        raw_hashes = item["hashes"]
        if isinstance(article_id, bool) or not isinstance(article_id, int) or article_id < 1 or not isinstance(raw_hashes, list):
            raise MigrationSafetyError("Manifest chroma hash metadata has invalid fields")
        parsed_values: list[str | None] = []
        for raw_hash in raw_hashes:
            if raw_hash is not None:
                if not isinstance(raw_hash, str):
                    raise MigrationSafetyError("Manifest chroma hash must be string or null")
                _validate_sha256(raw_hash, "chroma content hash")
            parsed_values.append(raw_hash)
        parsed_hashes.append((article_id, tuple(parsed_values)))
    if [item[0] for item in parsed_hashes] != parsed_ids:
        raise MigrationSafetyError("Manifest chroma hash metadata must match article_ids")
    error = _optional_string(value.get("error"), "chroma error")
    if error is not None:
        raise MigrationSafetyError("Successful manifest chroma audit cannot include an error")
    return ChromaAudit(
        status=status,
        collection_name=collection_name,
        record_count=record_count,
        article_ids=tuple(parsed_ids),
        missing_article_id_records=missing_count,
        content_hash_metadata=tuple(parsed_hashes),
    )


def _build_chroma_reconciliation(
    entries: tuple[MigrationEntry, ...],
    chroma_audit: ChromaAudit,
) -> dict[str, Any]:
    entries_by_id = {entry.article_id: entry for entry in entries}
    database_ids = set(entries_by_id)
    indexed_ids = set(chroma_audit.article_ids)
    hashes_by_id = dict(chroma_audit.content_hash_metadata)
    indexed_non_ready = sorted(
        article_id
        for article_id in indexed_ids & database_ids
        if entries_by_id[article_id].source_content_status != "ready"
    )
    indexed_unknown = sorted(indexed_ids - database_ids)
    db_index_ready_missing = sorted(
        entry.article_id
        for entry in entries
        if entry.source_index_status == "ready" and entry.article_id not in indexed_ids
    )
    hash_mismatch: list[int] = []
    missing_hash_metadata: list[int] = []
    for article_id in sorted(indexed_ids & database_ids):
        observed_hashes = set(hashes_by_id.get(article_id, ()))
        if not observed_hashes or None in observed_hashes:
            missing_hash_metadata.append(article_id)
        expected = entries_by_id[article_id].expected_sha256
        concrete_hashes = {value for value in observed_hashes if value is not None}
        if expected is not None and concrete_hashes and concrete_hashes != {expected}:
            hash_mismatch.append(article_id)
    return {
        "indexed_non_ready_article_ids": indexed_non_ready,
        "indexed_unknown_article_ids": indexed_unknown,
        "db_index_ready_missing_article_ids": db_index_ready_missing,
        "hash_mismatch_article_ids": hash_mismatch,
        "missing_hash_metadata_article_ids": missing_hash_metadata,
    }


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise MigrationSafetyError(f"{label} must be a SHA-256 hex string")


def _require_chroma_schema(connection: sqlite3.Connection) -> None:
    required = {
        "collections": {"id", "name"},
        "segments": {"id", "collection"},
        "embeddings": {"id", "segment_id"},
        "embedding_metadata": {"id", "key", "string_value", "int_value"},
    }
    existing_tables = {
        row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, columns in required.items():
        if table not in existing_tables:
            raise MigrationSafetyError(f"Unsupported Chroma schema: missing table {table}")
        actual_columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(columns - actual_columns)
        if missing:
            raise MigrationSafetyError(
                f"Unsupported Chroma schema: {table} missing columns {', '.join(missing)}"
            )


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _require_columns(connection: sqlite3.Connection, required: set[str]) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)").fetchall()}
    missing = sorted(required - columns)
    if missing:
        raise MigrationSafetyError("articles table missing required columns: " + ", ".join(missing))


def _content_sha256_from_file(path: Path) -> str:
    return _hash_bytes(path.read_text(encoding="utf-8").encode("utf-8"))


def _hash_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
