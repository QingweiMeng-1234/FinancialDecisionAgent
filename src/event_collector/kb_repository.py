"""SQLite persistence boundary for immutable Entity KB snapshots and releases."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


class KBRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActiveState:
    namespace: str
    active_release_id: str | None
    lock_version: int
    updated_at: str | None
    updated_by: str | None


@dataclass(frozen=True)
class ReleaseManifest:
    namespace: str
    snapshot_id: str
    snapshot_checksum: str
    resolver_policy_version: str
    resolver_policy_checksum: str
    freshness_policy_version: str
    freshness_policy_checksum: str
    signal_policy_version: str
    signal_policy_checksum: str
    schema_version: str
    id_algorithm_version: str


@dataclass(frozen=True)
class FinalRelease:
    release_id: str
    manifest: ReleaseManifest
    evaluation_run_id: str
    evaluation_manifest_hash: str
    created_at: str
    created_by: str


@dataclass(frozen=True)
class AliasRecord:
    alias_id: str
    entity_id: str
    display_value: str
    normalized_value: str
    alias_type: str
    language: str


def compute_release_id(
    manifest: ReleaseManifest,
    evaluation_run_id: str,
    evaluation_manifest_hash: str,
) -> str:
    payload = {
        **asdict(manifest),
        "evaluation_run_id": evaluation_run_id,
        "evaluation_manifest_hash": evaluation_manifest_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "kbr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def initialize_kb_schema(
    database: str | Path,
    *,
    namespaces: Iterable[str] = (),
) -> None:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        for namespace in namespaces:
            connection.execute(
                """
                INSERT OR IGNORE INTO kb_active_state
                    (namespace, active_release_id, lock_version, updated_at, updated_by)
                VALUES (?, NULL, 0, NULL, NULL)
                """,
                (namespace,),
            )
        connection.commit()
    finally:
        connection.close()


class SQLiteKBRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def get_active_state(self, namespace: str) -> ActiveState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM kb_active_state WHERE namespace=?",
                (namespace,),
            ).fetchone()
        if row is None:
            raise KBRepositoryError("KB_NAMESPACE_NOT_FOUND", f"unknown namespace {namespace!r}")
        return ActiveState(
            namespace=row["namespace"],
            active_release_id=row["active_release_id"],
            lock_version=row["lock_version"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

    def get_snapshot_build_identity(self, snapshot_id: str) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_manifest_hash, content_checksum "
                "FROM kb_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return row["source_manifest_hash"], row["content_checksum"]

    def list_aliases(self, snapshot_id: str) -> tuple[AliasRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT alias_id, entity_id, alias, normalized_alias, alias_type, language
                FROM kb_aliases WHERE snapshot_id=? ORDER BY alias_id
                """,
                (snapshot_id,),
            ).fetchall()
        return tuple(
            AliasRecord(
                alias_id=row["alias_id"],
                entity_id=row["entity_id"],
                display_value=row["alias"],
                normalized_value=row["normalized_alias"],
                alias_type=row["alias_type"],
                language=row["language"],
            )
            for row in rows
        )

    def apply_draft_import(
        self,
        *,
        snapshot: dict[str, Any],
        entities: Iterable[dict[str, Any]],
        aliases: Iterable[dict[str, Any]],
        findings: Iterable[dict[str, Any]],
    ) -> str:
        with self._connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT source_manifest_hash, content_checksum "
                "FROM kb_snapshots WHERE snapshot_id=?",
                (snapshot["snapshot_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    existing["source_manifest_hash"] == snapshot["source_manifest_hash"]
                    and existing["content_checksum"] == snapshot["content_checksum"]
                ):
                    return "already_applied"
                raise KBRepositoryError(
                    "BUILD_ID_CONFLICT",
                    "snapshot ID already exists with different import content",
                )
            entity_rows = tuple(entities)
            alias_rows = tuple(aliases)
            finding_rows = tuple(findings)
            connection.execute(
                """
                INSERT INTO kb_snapshots (
                    snapshot_id, namespace, parent_snapshot_id, status,
                    source_manifest_hash, schema_version, entity_count, alias_count,
                    relationship_count, content_checksum, created_at
                ) VALUES (?, ?, NULL, 'draft', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    snapshot["snapshot_id"],
                    snapshot["namespace"],
                    snapshot["source_manifest_hash"],
                    snapshot["schema_version"],
                    len(entity_rows),
                    len(alias_rows),
                    snapshot["content_checksum"],
                    _now(),
                ),
            )
            connection.executemany(
                """
                INSERT INTO kb_entities (
                    snapshot_id, entity_id, entity_type, canonical_name,
                    canonical_evidence_id, status, jurisdiction
                ) VALUES (:snapshot_id,:entity_id,'company',:canonical_name,
                          :canonical_evidence_id,'active',NULL)
                """,
                entity_rows,
            )
            connection.executemany(
                """
                INSERT INTO kb_aliases (
                    snapshot_id, alias_id, entity_id, alias, normalized_alias,
                    alias_type, language, match_policy, strength_class,
                    ambiguity_class, evidence_id, review_status
                ) VALUES (
                    :snapshot_id,:alias_id,:entity_id,:alias,:normalized_alias,
                    :alias_type,:language,:match_policy,'strong','unique',
                    :evidence_id,'verified'
                )
                """,
                alias_rows,
            )
            connection.executemany(
                """
                INSERT INTO kb_validation_findings (
                    finding_id, snapshot_id, severity, code, object_type,
                    object_id, message, details_json, created_at
                ) VALUES (
                    :finding_id,:snapshot_id,'error',:code,'alias',NULL,
                    :message,:details_json,:created_at
                )
                """,
                finding_rows,
            )
            return "applied"

    def create_snapshot(
        self,
        *,
        snapshot_id: str,
        namespace: str,
        source_manifest_hash: str,
        schema_version: str,
        content_checksum: str,
        parent_snapshot_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kb_snapshots (
                    snapshot_id, namespace, parent_snapshot_id, status,
                    source_manifest_hash, schema_version, entity_count,
                    alias_count, relationship_count, content_checksum, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, 0, 0, 0, ?, ?)
                """,
                (
                    snapshot_id,
                    namespace,
                    parent_snapshot_id,
                    source_manifest_hash,
                    schema_version,
                    content_checksum,
                    _now(),
                ),
            )

    def add_entity(
        self,
        *,
        snapshot_id: str,
        entity_id: str,
        entity_type: str,
        canonical_name: str,
        canonical_evidence_id: str,
        status: str = "active",
        jurisdiction: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kb_entities (
                    snapshot_id, entity_id, entity_type, canonical_name,
                    canonical_evidence_id, status, jurisdiction
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    entity_id,
                    entity_type,
                    canonical_name,
                    canonical_evidence_id,
                    status,
                    jurisdiction,
                ),
            )
            connection.execute(
                "UPDATE kb_snapshots SET entity_count=entity_count+1 WHERE snapshot_id=?",
                (snapshot_id,),
            )

    def add_alias(self, **fields: Any) -> None:
        columns = (
            "snapshot_id",
            "alias_id",
            "entity_id",
            "alias",
            "normalized_alias",
            "alias_type",
            "language",
            "match_policy",
            "strength_class",
            "ambiguity_class",
            "evidence_id",
            "review_status",
        )
        values = tuple(fields[column] for column in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO kb_aliases ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                values,
            )
            connection.execute(
                "UPDATE kb_snapshots SET alias_count=alias_count+1 WHERE snapshot_id=?",
                (fields["snapshot_id"],),
            )

    def add_relationship(self, **fields: Any) -> None:
        columns = (
            "snapshot_id",
            "relationship_id",
            "subject_entity_id",
            "relation_type",
            "object_entity_id",
            "scope_product_id",
            "scope_region",
            "evidence_id",
            "observed_at",
            "valid_from",
            "valid_to",
            "verification_status",
        )
        values = tuple(fields.get(column) for column in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO kb_relationships ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                values,
            )
            connection.execute(
                "UPDATE kb_snapshots SET relationship_count=relationship_count+1 "
                "WHERE snapshot_id=?",
                (fields["snapshot_id"],),
            )

    def transition_snapshot(
        self,
        snapshot_id: str,
        target_status: str,
        *,
        actor: str | None = None,
    ) -> None:
        allowed = {
            "draft": {"validating", "rejected"},
            "validating": {"draft", "validated", "rejected"},
            "validated": {"approved", "rejected"},
        }
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM kb_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if row is None or target_status not in allowed.get(row["status"], set()):
                raise KBRepositoryError(
                    "KB_SNAPSHOT_TRANSITION_INVALID",
                    f"cannot transition snapshot from {None if row is None else row['status']} "
                    f"to {target_status}",
                )
            now = _now()
            fields = ["status=?"]
            values: list[Any] = [target_status]
            if target_status == "validated":
                fields.append("validated_at=?")
                values.append(now)
            elif target_status == "approved":
                if not actor:
                    raise KBRepositoryError("KB_REVIEWER_REQUIRED", "snapshot approval needs actor")
                fields.extend(("approved_at=?", "approved_by=?"))
                values.extend((now, actor))
            elif target_status == "rejected":
                fields.append("rejected_at=?")
                values.append(now)
            values.append(snapshot_id)
            connection.execute(
                f"UPDATE kb_snapshots SET {','.join(fields)} WHERE snapshot_id=?",
                values,
            )

    def create_release_candidate(
        self,
        candidate_id: str,
        manifest: ReleaseManifest,
        *,
        actor: str,
    ) -> None:
        with self._connect() as connection:
            snapshot = connection.execute(
                "SELECT status, content_checksum FROM kb_snapshots WHERE snapshot_id=?",
                (manifest.snapshot_id,),
            ).fetchone()
            if (
                snapshot is None
                or snapshot["status"] != "approved"
                or snapshot["content_checksum"] != manifest.snapshot_checksum
            ):
                raise KBRepositoryError(
                    "KB_SNAPSHOT_NOT_APPROVED",
                    "candidate requires matching approved snapshot",
                )
            connection.execute(
                """
                INSERT INTO kb_release_candidates (
                    candidate_id, namespace, snapshot_id, snapshot_checksum,
                    resolver_policy_version, resolver_policy_checksum,
                    freshness_policy_version, freshness_policy_checksum,
                    signal_policy_version, signal_policy_checksum, schema_version,
                    id_algorithm_version, evaluation_run_id, status, created_at,
                    created_by, evaluated_at, approved_at, approved_by, rejected_at,
                    rejection_reason
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,NULL,'draft',?,?,NULL,NULL,NULL,NULL,NULL
                )
                """,
                (
                    candidate_id,
                    manifest.namespace,
                    manifest.snapshot_id,
                    manifest.snapshot_checksum,
                    manifest.resolver_policy_version,
                    manifest.resolver_policy_checksum,
                    manifest.freshness_policy_version,
                    manifest.freshness_policy_checksum,
                    manifest.signal_policy_version,
                    manifest.signal_policy_checksum,
                    manifest.schema_version,
                    manifest.id_algorithm_version,
                    _now(),
                    actor,
                ),
            )

    def record_evaluation_run(
        self,
        *,
        candidate_id: str,
        evaluation_run_id: str,
        evaluation_manifest_hash: str,
        result: str,
        report_uri: str,
        report_checksum: str,
        exception_approved_by: str | None = None,
        exception_reason: str | None = None,
    ) -> None:
        if result not in {"passed", "failed", "approved_with_exception"}:
            raise KBRepositoryError("KB_EVALUATION_RESULT_INVALID", f"invalid result {result}")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kb_evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evaluation_run_id,
                    candidate_id,
                    evaluation_manifest_hash,
                    result,
                    report_uri,
                    report_checksum,
                    now,
                    now,
                    exception_approved_by,
                    exception_reason,
                ),
            )
            connection.execute(
                """
                UPDATE kb_release_candidates
                SET evaluation_run_id=?, status='evaluated', evaluated_at=?
                WHERE candidate_id=? AND status='draft'
                """,
                (evaluation_run_id, now, candidate_id),
            )
            if connection.total_changes != 2:
                raise KBRepositoryError(
                    "KB_RELEASE_CANDIDATE_INVALID",
                    "evaluation requires one draft candidate",
                )

    def approve_release_candidate(self, candidate_id: str, *, actor: str) -> FinalRelease:
        with self._connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT c.*, e.evaluation_manifest_hash, e.result
                FROM kb_release_candidates c
                JOIN kb_evaluation_runs e ON e.evaluation_run_id=c.evaluation_run_id
                WHERE c.candidate_id=?
                """,
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise KBRepositoryError("KB_RELEASE_CANDIDATE_INVALID", "candidate not evaluated")
            if row["status"] == "approved":
                return self._release_for_evaluation(connection, row["evaluation_run_id"])
            if row["status"] != "evaluated" or row["result"] not in {
                "passed",
                "approved_with_exception",
            }:
                raise KBRepositoryError(
                    "KB_EVALUATION_NOT_APPROVED",
                    "candidate evaluation does not permit approval",
                )
            manifest = _manifest_from_row(row)
            release_id = compute_release_id(
                manifest,
                row["evaluation_run_id"],
                row["evaluation_manifest_hash"],
            )
            now = _now()
            connection.execute(
                """
                INSERT INTO kb_releases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    release_id,
                    *asdict(manifest).values(),
                    row["evaluation_run_id"],
                    row["evaluation_manifest_hash"],
                    now,
                    actor,
                ),
            )
            connection.execute(
                """
                UPDATE kb_release_candidates
                SET status='approved', approved_at=?, approved_by=?
                WHERE candidate_id=? AND status='evaluated'
                """,
                (now, actor, candidate_id),
            )
            return FinalRelease(
                release_id=release_id,
                manifest=manifest,
                evaluation_run_id=row["evaluation_run_id"],
                evaluation_manifest_hash=row["evaluation_manifest_hash"],
                created_at=now,
                created_by=actor,
            )

    def get_release(self, release_id: str) -> FinalRelease:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM kb_releases WHERE release_id=?",
                (release_id,),
            ).fetchone()
        if row is None:
            raise KBRepositoryError("KB_RELEASE_NOT_FOUND", f"release {release_id!r} not found")
        return _release_from_row(row)

    def _release_for_evaluation(
        self,
        connection: sqlite3.Connection,
        evaluation_run_id: str,
    ) -> FinalRelease:
        row = connection.execute(
            "SELECT * FROM kb_releases WHERE evaluation_run_id=?",
            (evaluation_run_id,),
        ).fetchone()
        if row is None:
            raise KBRepositoryError("KB_RELEASE_INVALID", "approved candidate has no release")
        return _release_from_row(row)

    def _connect(self, *, immediate: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
        return connection


def _manifest_from_row(row: sqlite3.Row) -> ReleaseManifest:
    return ReleaseManifest(
        namespace=row["namespace"],
        snapshot_id=row["snapshot_id"],
        snapshot_checksum=row["snapshot_checksum"],
        resolver_policy_version=row["resolver_policy_version"],
        resolver_policy_checksum=row["resolver_policy_checksum"],
        freshness_policy_version=row["freshness_policy_version"],
        freshness_policy_checksum=row["freshness_policy_checksum"],
        signal_policy_version=row["signal_policy_version"],
        signal_policy_checksum=row["signal_policy_checksum"],
        schema_version=row["schema_version"],
        id_algorithm_version=row["id_algorithm_version"],
    )


def _release_from_row(row: sqlite3.Row) -> FinalRelease:
    return FinalRelease(
        release_id=row["release_id"],
        manifest=_manifest_from_row(row),
        evaluation_run_id=row["evaluation_run_id"],
        evaluation_manifest_hash=row["evaluation_manifest_hash"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_snapshots (
    snapshot_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, parent_snapshot_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('draft','validating','validated','approved','rejected')),
    source_manifest_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
    entity_count INTEGER NOT NULL, alias_count INTEGER NOT NULL,
    relationship_count INTEGER NOT NULL, content_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL, validated_at TEXT, approved_at TEXT, approved_by TEXT,
    rejected_at TEXT, rejection_reason TEXT
);
CREATE TABLE IF NOT EXISTS kb_entities (
    snapshot_id TEXT NOT NULL REFERENCES kb_snapshots(snapshot_id), entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL, canonical_name TEXT NOT NULL, canonical_evidence_id TEXT NOT NULL,
    status TEXT NOT NULL, jurisdiction TEXT, valid_from TEXT, valid_to TEXT,
    PRIMARY KEY(snapshot_id, entity_id)
);
CREATE TABLE IF NOT EXISTS kb_aliases (
    snapshot_id TEXT NOT NULL REFERENCES kb_snapshots(snapshot_id), alias_id TEXT NOT NULL,
    entity_id TEXT NOT NULL, alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL, language TEXT NOT NULL, match_policy TEXT NOT NULL,
    strength_class TEXT NOT NULL, ambiguity_class TEXT NOT NULL, evidence_id TEXT NOT NULL,
    review_status TEXT NOT NULL, valid_from TEXT, valid_to TEXT,
    PRIMARY KEY(snapshot_id, alias_id),
    FOREIGN KEY(snapshot_id, entity_id) REFERENCES kb_entities(snapshot_id, entity_id)
);
CREATE TABLE IF NOT EXISTS kb_relationships (
    snapshot_id TEXT NOT NULL REFERENCES kb_snapshots(snapshot_id), relationship_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL, relation_type TEXT NOT NULL, object_entity_id TEXT NOT NULL,
    scope_product_id TEXT, scope_region TEXT, evidence_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT, verification_status TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, relationship_id),
    FOREIGN KEY(snapshot_id, subject_entity_id) REFERENCES kb_entities(snapshot_id, entity_id),
    FOREIGN KEY(snapshot_id, object_entity_id) REFERENCES kb_entities(snapshot_id, entity_id)
);
CREATE TABLE IF NOT EXISTS kb_validation_findings (
    finding_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES kb_snapshots(snapshot_id),
    severity TEXT NOT NULL, code TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT,
    message TEXT NOT NULL, details_json TEXT, created_at TEXT NOT NULL,
    resolved_at TEXT, resolution_review_id TEXT
);
CREATE TABLE IF NOT EXISTS kb_release_candidates (
    candidate_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, snapshot_id TEXT NOT NULL,
    snapshot_checksum TEXT NOT NULL, resolver_policy_version TEXT NOT NULL,
    resolver_policy_checksum TEXT NOT NULL, freshness_policy_version TEXT NOT NULL,
    freshness_policy_checksum TEXT NOT NULL, signal_policy_version TEXT NOT NULL,
    signal_policy_checksum TEXT NOT NULL, schema_version TEXT NOT NULL,
    id_algorithm_version TEXT NOT NULL, evaluation_run_id TEXT UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('draft','evaluated','approved','rejected')),
    created_at TEXT NOT NULL, created_by TEXT NOT NULL, evaluated_at TEXT,
    approved_at TEXT, approved_by TEXT, rejected_at TEXT, rejection_reason TEXT,
    FOREIGN KEY(snapshot_id) REFERENCES kb_snapshots(snapshot_id)
);
CREATE TABLE IF NOT EXISTS kb_evaluation_runs (
    evaluation_run_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE,
    evaluation_manifest_hash TEXT NOT NULL, result TEXT NOT NULL,
    report_uri TEXT NOT NULL, report_checksum TEXT NOT NULL,
    started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
    exception_approved_by TEXT, exception_reason TEXT,
    FOREIGN KEY(candidate_id) REFERENCES kb_release_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS kb_releases (
    release_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, snapshot_id TEXT NOT NULL,
    snapshot_checksum TEXT NOT NULL, resolver_policy_version TEXT NOT NULL,
    resolver_policy_checksum TEXT NOT NULL, freshness_policy_version TEXT NOT NULL,
    freshness_policy_checksum TEXT NOT NULL, signal_policy_version TEXT NOT NULL,
    signal_policy_checksum TEXT NOT NULL, schema_version TEXT NOT NULL,
    id_algorithm_version TEXT NOT NULL, evaluation_run_id TEXT NOT NULL UNIQUE,
    evaluation_manifest_hash TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES kb_snapshots(snapshot_id),
    FOREIGN KEY(evaluation_run_id) REFERENCES kb_evaluation_runs(evaluation_run_id)
);
CREATE TABLE IF NOT EXISTS kb_active_state (
    namespace TEXT PRIMARY KEY, active_release_id TEXT REFERENCES kb_releases(release_id),
    lock_version INTEGER NOT NULL, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS kb_activation_events (
    event_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, from_release_id TEXT,
    to_release_id TEXT NOT NULL, operation TEXT NOT NULL, expected_lock_version INTEGER NOT NULL,
    resulting_lock_version INTEGER NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

for _table in ("kb_entities", "kb_aliases", "kb_relationships"):
    _SCHEMA += f"""
    CREATE TRIGGER IF NOT EXISTS {_table}_approved_insert BEFORE INSERT ON {_table}
    WHEN (SELECT status FROM kb_snapshots WHERE snapshot_id=NEW.snapshot_id)='approved'
    BEGIN SELECT RAISE(ABORT, 'approved snapshot content is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS {_table}_approved_update BEFORE UPDATE ON {_table}
    WHEN (SELECT status FROM kb_snapshots WHERE snapshot_id=OLD.snapshot_id)='approved'
    BEGIN SELECT RAISE(ABORT, 'approved snapshot content is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS {_table}_approved_delete BEFORE DELETE ON {_table}
    WHEN (SELECT status FROM kb_snapshots WHERE snapshot_id=OLD.snapshot_id)='approved'
    BEGIN SELECT RAISE(ABORT, 'approved snapshot content is immutable'); END;
    """

_SCHEMA += """
CREATE TRIGGER IF NOT EXISTS kb_snapshot_approved_update BEFORE UPDATE ON kb_snapshots
WHEN OLD.status='approved'
BEGIN SELECT RAISE(ABORT, 'approved snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kb_snapshot_approved_delete BEFORE DELETE ON kb_snapshots
WHEN OLD.status='approved'
BEGIN SELECT RAISE(ABORT, 'approved snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kb_evaluation_update BEFORE UPDATE ON kb_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation run is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kb_evaluation_delete BEFORE DELETE ON kb_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation run is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kb_release_update BEFORE UPDATE ON kb_releases
BEGIN SELECT RAISE(ABORT, 'final release is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kb_release_delete BEFORE DELETE ON kb_releases
BEGIN SELECT RAISE(ABORT, 'final release is immutable'); END;
"""


__all__ = [
    "ActiveState",
    "AliasRecord",
    "FinalRelease",
    "KBRepositoryError",
    "ReleaseManifest",
    "SQLiteKBRepository",
    "compute_release_id",
    "initialize_kb_schema",
]
