"""SQLite manifest and activation state for immutable RAG index generations.

This module deliberately does not import Chroma or the event_collector package.
An index builder records its observed per-article result here; this module only
proves that the result covers one canonical snapshot before switching readers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Union


BUILDING = "building"
VERIFIED = "verified"
FAILED = "failed"
ACTIVE = "active"
_GENERATION_STATES = (BUILDING, VERIFIED, FAILED, ACTIVE)


class GenerationStateError(RuntimeError):
    """Raised when a generation transition would violate its state contract."""


@dataclass(frozen=True)
class EligibleArticle:
    """The canonical content identity an index generation must cover."""

    article_id: int
    content_sha256: str


@dataclass(frozen=True)
class IndexGeneration:
    generation_id: str
    corpus_id: str
    collection_name: str
    embedding_artifact: str
    index_config: Mapping[str, object]
    index_config_fingerprint: str
    corpus_snapshot_id: str
    status: str
    created_at: str
    verified_at: Optional[str]
    activated_at: Optional[str]


@dataclass(frozen=True)
class ArticleIndexManifest:
    generation_id: str
    article_id: int
    indexed_content_sha256: str
    expected_chunk_count: int
    actual_chunk_count: int
    index_config_fingerprint: str
    index_status: str
    indexed_at: str


@dataclass(frozen=True)
class IndexedChunkRecord:
    """One chunk observed in the candidate index, supplied by its adapter."""

    generation_id: str
    article_id: int
    indexed_content_sha256: str
    chunk_index: int
    index_config_fingerprint: str


@dataclass(frozen=True)
class ChunkVerificationReport:
    """Persistable result of reconciling all candidate chunks to its manifest."""

    generation_id: str
    valid: bool
    observed_chunk_count: int
    wrong_generation_chunk_count: int
    orphan_article_ids: tuple[int, ...]
    hash_mismatch_article_ids: tuple[int, ...]
    config_mismatch_article_ids: tuple[int, ...]
    chunk_count_mismatch_article_ids: tuple[int, ...]
    duplicate_chunk_indices: tuple[tuple[int, int], ...]
    missing_chunk_indices: tuple[tuple[int, int], ...]
    invalid_chunk_indices: tuple[tuple[int, int], ...]

    @property
    def issue_count(self) -> int:
        return (
            self.wrong_generation_chunk_count
            + len(self.orphan_article_ids)
            + len(self.hash_mismatch_article_ids)
            + len(self.config_mismatch_article_ids)
            + len(self.chunk_count_mismatch_article_ids)
            + len(self.duplicate_chunk_indices)
            + len(self.missing_chunk_indices)
            + len(self.invalid_chunk_indices)
        )


@dataclass(frozen=True)
class VerificationReport:
    generation_id: str
    valid: bool
    missing_article_ids: tuple[int, ...]
    unexpected_article_ids: tuple[int, ...]
    hash_mismatch_article_ids: tuple[int, ...]
    chunk_count_mismatch_article_ids: tuple[int, ...]
    non_positive_chunk_article_ids: tuple[int, ...]
    config_mismatch_article_ids: tuple[int, ...]
    non_indexed_article_ids: tuple[int, ...]
    chunk_proof_missing: bool

    @property
    def issue_count(self) -> int:
        return sum(
            len(group)
            for group in (
                self.missing_article_ids,
                self.unexpected_article_ids,
                self.hash_mismatch_article_ids,
                self.chunk_count_mismatch_article_ids,
                self.non_positive_chunk_article_ids,
                self.config_mismatch_article_ids,
                self.non_indexed_article_ids,
            )
        ) + int(self.chunk_proof_missing)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_config(config: Mapping[str, object]) -> tuple[str, str]:
    try:
        encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("chunk_config must be JSON serializable") from error
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compute_index_config_fingerprint(config: Mapping[str, object]) -> str:
    """Return the canonical fingerprint persisted for generation configuration."""
    return _canonical_config(config)[1]


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the independent index-generation tables without touching articles."""
    # This module addresses columns by name. Set the standard sqlite mapping
    # factory at its public setup boundary so callers need no hidden setup.
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS index_generations (
            generation_id TEXT PRIMARY KEY,
            corpus_id TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            embedding_artifact TEXT NOT NULL,
            index_config_json TEXT NOT NULL,
            index_config_fingerprint TEXT NOT NULL,
            corpus_snapshot_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('building', 'verified', 'failed', 'active')),
            created_at TEXT NOT NULL,
            verified_at TEXT,
            activated_at TEXT,
            failure_reason TEXT,
            UNIQUE (corpus_id, collection_name)
        );

        CREATE TABLE IF NOT EXISTS article_index_manifest (
            generation_id TEXT NOT NULL,
            article_id INTEGER NOT NULL,
            indexed_content_sha256 TEXT NOT NULL,
            expected_chunk_count INTEGER NOT NULL CHECK (expected_chunk_count >= 0),
            actual_chunk_count INTEGER NOT NULL CHECK (actual_chunk_count >= 0),
            index_config_fingerprint TEXT NOT NULL,
            index_status TEXT NOT NULL CHECK (index_status IN ('indexed', 'failed')),
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (generation_id, article_id),
            FOREIGN KEY (generation_id) REFERENCES index_generations(generation_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS corpus_index_state (
            corpus_id TEXT PRIMARY KEY,
            active_generation_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (active_generation_id) REFERENCES index_generations(generation_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS generation_chunk_verification (
            generation_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('verified', 'failed')),
            observed_chunk_count INTEGER NOT NULL,
            issue_count INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            failure_reason TEXT,
            FOREIGN KEY (generation_id) REFERENCES index_generations(generation_id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_article_index_manifest_generation
            ON article_index_manifest(generation_id);
        """
    )
    connection.commit()


def create_generation(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    corpus_id: str,
    collection_name: str,
    embedding_artifact: str,
    chunk_config: Mapping[str, object],
    corpus_snapshot_id: str,
) -> IndexGeneration:
    """Start a successor generation. A builder may only append while building."""
    if not all((generation_id, corpus_id, collection_name, embedding_artifact, corpus_snapshot_id)):
        raise ValueError("generation identity, collection, artifact, and snapshot are required")
    config_json, config_fingerprint = _canonical_config(chunk_config)
    created_at = _utc_now()
    connection.execute(
        """
        INSERT INTO index_generations (
            generation_id, corpus_id, collection_name, embedding_artifact,
            index_config_json, index_config_fingerprint, corpus_snapshot_id,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id,
            corpus_id,
            collection_name,
            embedding_artifact,
            config_json,
            config_fingerprint,
            corpus_snapshot_id,
            BUILDING,
            created_at,
        ),
    )
    return IndexGeneration(
        generation_id, corpus_id, collection_name, embedding_artifact,
        json.loads(config_json), config_fingerprint, corpus_snapshot_id,
        BUILDING, created_at, None, None,
    )


def _generation_row(connection: sqlite3.Connection, generation_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM index_generations WHERE generation_id = ?", (generation_id,)
    ).fetchone()
    if row is None:
        raise GenerationStateError(f"unknown generation: {generation_id}")
    return row


def record_article_manifest(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    article_id: int,
    indexed_content_sha256: str,
    expected_chunk_count: int,
    actual_chunk_count: int,
    index_config_fingerprint: Optional[str] = None,
    index_status: str = "indexed",
) -> ArticleIndexManifest:
    """Record one builder observation; duplicate article IDs are never overwritten."""
    generation = _generation_row(connection, generation_id)
    if generation["status"] != BUILDING:
        raise GenerationStateError("manifest records may only be added to a building generation")
    if article_id <= 0:
        raise ValueError("article_id must be positive")
    if expected_chunk_count < 0 or actual_chunk_count < 0:
        raise ValueError("chunk counts cannot be negative")
    if index_status not in ("indexed", "failed"):
        raise ValueError("index_status must be indexed or failed")
    fingerprint = index_config_fingerprint or generation["index_config_fingerprint"]
    indexed_at = _utc_now()
    connection.execute(
        """
        INSERT INTO article_index_manifest (
            generation_id, article_id, indexed_content_sha256,
            expected_chunk_count, actual_chunk_count, index_config_fingerprint,
            index_status, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id, article_id, indexed_content_sha256,
            expected_chunk_count, actual_chunk_count, fingerprint,
            index_status, indexed_at,
        ),
    )
    return ArticleIndexManifest(
        generation_id, article_id, indexed_content_sha256, expected_chunk_count,
        actual_chunk_count, fingerprint, index_status, indexed_at,
    )


EligibleInput = Union[Mapping[int, str], Iterable[EligibleArticle], Iterable[tuple[int, str]]]


def _eligible_by_id(eligible_articles: EligibleInput) -> dict[int, str]:
    if isinstance(eligible_articles, Mapping):
        source = eligible_articles.items()
    else:
        source = eligible_articles
    result: dict[int, str] = {}
    for item in source:
        if isinstance(item, EligibleArticle):
            article_id, content_sha256 = item.article_id, item.content_sha256
        else:
            article_id, content_sha256 = item
        if not isinstance(article_id, int) or article_id <= 0:
            raise ValueError("eligible article IDs must be positive integers")
        if not isinstance(content_sha256, str) or not content_sha256:
            raise ValueError("eligible content hashes must be non-empty strings")
        if article_id in result:
            raise ValueError(f"duplicate eligible article ID: {article_id}")
        result[article_id] = content_sha256
    return result


def compute_eligible_snapshot_fingerprint(eligible_articles: EligibleInput) -> str:
    """Bind a snapshot identity to the complete ordered article/hash set."""
    eligible = _eligible_by_id(eligible_articles)
    encoded = json.dumps(
        [[article_id, eligible[article_id]] for article_id in sorted(eligible)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def reconcile_generation_chunks(
    connection: sqlite3.Connection,
    generation_id: str,
    indexed_chunks: Iterable[IndexedChunkRecord],
) -> ChunkVerificationReport:
    """Reconcile a candidate index's chunk metadata against its manifest.

    The caller obtains records from the vector backend, but no backend client is
    imported here. A failed reconciliation permanently fails this immutable
    generation and records a diagnostic proof; rebuild as a successor instead.
    """
    generation = _generation_row(connection, generation_id)
    if generation["status"] != BUILDING:
        raise GenerationStateError("only a building generation can reconcile chunks")
    if connection.execute(
        "SELECT 1 FROM generation_chunk_verification WHERE generation_id = ?", (generation_id,)
    ).fetchone() is not None:
        raise GenerationStateError("chunk reconciliation proof already exists for this generation")
    manifests = {
        row["article_id"]: row
        for row in connection.execute(
            "SELECT * FROM article_index_manifest WHERE generation_id = ? ORDER BY article_id",
            (generation_id,),
        ).fetchall()
    }
    observed_indices: dict[int, list[int]] = {article_id: [] for article_id in manifests}
    wrong_generation_count = 0
    orphan_ids: set[int] = set()
    hash_mismatches: set[int] = set()
    config_mismatches: set[int] = set()
    observed_count = 0

    for chunk in indexed_chunks:
        if not isinstance(chunk, IndexedChunkRecord):
            raise ValueError("indexed_chunks must contain IndexedChunkRecord values")
        observed_count += 1
        if chunk.generation_id != generation_id:
            wrong_generation_count += 1
            continue
        manifest = manifests.get(chunk.article_id)
        if manifest is None:
            orphan_ids.add(chunk.article_id)
            continue
        observed_indices[chunk.article_id].append(chunk.chunk_index)
        if chunk.indexed_content_sha256 != manifest["indexed_content_sha256"]:
            hash_mismatches.add(chunk.article_id)
        if (
            chunk.index_config_fingerprint != generation["index_config_fingerprint"]
            or chunk.index_config_fingerprint != manifest["index_config_fingerprint"]
        ):
            config_mismatches.add(chunk.article_id)

    count_mismatches: set[int] = set()
    duplicate_indices: set[tuple[int, int]] = set()
    missing_indices: set[tuple[int, int]] = set()
    invalid_indices: set[tuple[int, int]] = set()
    for article_id, manifest in manifests.items():
        indexes = observed_indices[article_id]
        expected_count = manifest["actual_chunk_count"]
        if len(indexes) != expected_count:
            count_mismatches.add(article_id)
        seen: set[int] = set()
        expected_indexes = set(range(expected_count))
        for index in indexes:
            if not isinstance(index, int) or isinstance(index, bool) or index not in expected_indexes:
                invalid_indices.add((article_id, index))
                continue
            if index in seen:
                duplicate_indices.add((article_id, index))
            seen.add(index)
        for index in expected_indexes - seen:
            missing_indices.add((article_id, index))

    report = ChunkVerificationReport(
        generation_id,
        not any((
            wrong_generation_count, orphan_ids, hash_mismatches, config_mismatches,
            count_mismatches, duplicate_indices, missing_indices, invalid_indices,
        )),
        observed_count,
        wrong_generation_count,
        tuple(sorted(orphan_ids)),
        tuple(sorted(hash_mismatches)),
        tuple(sorted(config_mismatches)),
        tuple(sorted(count_mismatches)),
        tuple(sorted(duplicate_indices)),
        tuple(sorted(missing_indices)),
        tuple(sorted(invalid_indices)),
    )
    now = _utc_now()
    with connection:
        connection.execute(
            """
            INSERT INTO generation_chunk_verification
                (generation_id, status, observed_chunk_count, issue_count, checked_at, failure_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                VERIFIED if report.valid else FAILED,
                observed_count,
                report.issue_count,
                now,
                None if report.valid else "chunk reconciliation failed",
            ),
        )
        if not report.valid:
            connection.execute(
                "UPDATE index_generations SET status = ?, failure_reason = ? "
                "WHERE generation_id = ? AND status = ?",
                (FAILED, "chunk reconciliation failed", generation_id, BUILDING),
            )
    return report


def verify_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    eligible_articles: EligibleInput,
) -> VerificationReport:
    """Prove manifest coverage against one frozen canonical eligible snapshot.

    A failed generation is retained as diagnostic evidence. Repair is a new
    generation, rather than mutating records that may have been partially built.
    """
    generation = _generation_row(connection, generation_id)
    if generation["status"] != BUILDING:
        raise GenerationStateError("only a building generation can be verified")
    expected = _eligible_by_id(eligible_articles)
    rows = connection.execute(
        "SELECT * FROM article_index_manifest WHERE generation_id = ? ORDER BY article_id",
        (generation_id,),
    ).fetchall()
    chunk_proof = connection.execute(
        "SELECT status FROM generation_chunk_verification WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    chunk_proof_missing = chunk_proof is None or chunk_proof["status"] != VERIFIED
    actual = {row["article_id"]: row for row in rows}
    expected_ids = set(expected)
    actual_ids = set(actual)
    shared_ids = expected_ids & actual_ids

    missing = tuple(sorted(expected_ids - actual_ids))
    unexpected = tuple(sorted(actual_ids - expected_ids))
    hash_mismatches = tuple(sorted(
        article_id for article_id in shared_ids
        if actual[article_id]["indexed_content_sha256"] != expected[article_id]
    ))
    chunk_count_mismatches = tuple(sorted(
        article_id for article_id in shared_ids
        if actual[article_id]["expected_chunk_count"] != actual[article_id]["actual_chunk_count"]
    ))
    non_positive = tuple(sorted(
        article_id for article_id in shared_ids
        if actual[article_id]["expected_chunk_count"] <= 0
        or actual[article_id]["actual_chunk_count"] <= 0
    ))
    config_mismatches = tuple(sorted(
        article_id for article_id in shared_ids
        if actual[article_id]["index_config_fingerprint"] != generation["index_config_fingerprint"]
    ))
    non_indexed = tuple(sorted(
        article_id for article_id in shared_ids
        if actual[article_id]["index_status"] != "indexed"
    ))
    report = VerificationReport(
        generation_id,
        not any((missing, unexpected, hash_mismatches, chunk_count_mismatches,
                 non_positive, config_mismatches, non_indexed, chunk_proof_missing)),
        missing, unexpected, hash_mismatches, chunk_count_mismatches,
        non_positive, config_mismatches, non_indexed, chunk_proof_missing,
    )
    now = _utc_now()
    with connection:
        if report.valid:
            transition = connection.execute(
                "UPDATE index_generations SET status = ?, verified_at = ?, failure_reason = NULL "
                "WHERE generation_id = ? AND status = ?",
                (VERIFIED, now, generation_id, BUILDING),
            )
        else:
            transition = connection.execute(
                "UPDATE index_generations SET status = ?, failure_reason = ? "
                "WHERE generation_id = ? AND status = ?",
                (
                    FAILED,
                    "missing verified chunk proof" if chunk_proof_missing else "manifest verification failed",
                    generation_id,
                    BUILDING,
                ),
            )
        if transition.rowcount != 1:
            raise GenerationStateError(
                "generation state changed concurrently during verification"
            )
    return report


def activate_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    expected_current_generation_id: Optional[str],
) -> IndexGeneration:
    """Compare-and-swap a corpus pointer to a verified successor generation."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        generation = _generation_row(connection, generation_id)
        if generation["status"] != VERIFIED:
            raise GenerationStateError("only a verified generation can be activated")
        current = connection.execute(
            "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = ?",
            (generation["corpus_id"],),
        ).fetchone()
        current_generation_id = (
            None if current is None else current["active_generation_id"]
        )
        if current_generation_id != expected_current_generation_id:
            raise GenerationStateError(
                "active generation changed concurrently during activation"
            )
        now = _utc_now()
        if current is not None:
            connection.execute(
                "UPDATE index_generations SET status = ? WHERE generation_id = ? AND status = ?",
                (VERIFIED, current_generation_id, ACTIVE),
            )
        connection.execute(
            """
            INSERT INTO corpus_index_state (corpus_id, active_generation_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(corpus_id) DO UPDATE SET
                active_generation_id = excluded.active_generation_id,
                updated_at = excluded.updated_at
            """,
            (generation["corpus_id"], generation_id, now),
        )
        activation = connection.execute(
            "UPDATE index_generations SET status = ?, activated_at = ? "
            "WHERE generation_id = ? AND status = ?",
            (ACTIVE, now, generation_id, VERIFIED),
        )
        if activation.rowcount != 1:
            raise GenerationStateError(
                "generation state changed concurrently during activation"
            )
        activated = _generation_row(connection, generation_id)
        active_pointer = connection.execute(
            "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = ?",
            (generation["corpus_id"],),
        ).fetchone()
        if (
            activated["status"] != ACTIVE
            or active_pointer is None
            or active_pointer["active_generation_id"] != generation_id
        ):
            raise GenerationStateError("generation activation readback is inconsistent")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return _as_generation(activated)


def _as_generation(row: sqlite3.Row) -> IndexGeneration:
    return IndexGeneration(
        row["generation_id"], row["corpus_id"], row["collection_name"],
        row["embedding_artifact"], json.loads(row["index_config_json"]),
        row["index_config_fingerprint"], row["corpus_snapshot_id"], row["status"],
        row["created_at"], row["verified_at"], row["activated_at"],
    )


def read_active_generation(
    connection: sqlite3.Connection, corpus_id: str
) -> Optional[IndexGeneration]:
    """Return the active generation, failing closed on an inconsistent pointer."""
    state = connection.execute(
        "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = ?", (corpus_id,)
    ).fetchone()
    if state is None:
        return None
    generation = _generation_row(connection, state["active_generation_id"])
    if generation["corpus_id"] != corpus_id or generation["status"] != ACTIVE:
        raise GenerationStateError("corpus active-generation pointer is inconsistent")
    return _as_generation(generation)


def read_active_manifest(
    connection: sqlite3.Connection, corpus_id: str
) -> tuple[ArticleIndexManifest, ...]:
    """Read only manifest entries bound to the active corpus generation."""
    generation = read_active_generation(connection, corpus_id)
    if generation is None:
        return ()
    rows = connection.execute(
        "SELECT * FROM article_index_manifest WHERE generation_id = ? ORDER BY article_id",
        (generation.generation_id,),
    ).fetchall()
    return tuple(
        ArticleIndexManifest(
            row["generation_id"], row["article_id"], row["indexed_content_sha256"],
            row["expected_chunk_count"], row["actual_chunk_count"],
            row["index_config_fingerprint"], row["index_status"], row["indexed_at"],
        )
        for row in rows
    )
