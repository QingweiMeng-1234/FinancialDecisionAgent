"""Read-only resolution of immutable RAG index generations for serving."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Union


MISSING_POINTER = "missing_pointer"
VERIFIED_NOT_ACTIVE = "verified_not_active"
POINTER_MISMATCH = "pointer_mismatch"
PROOF_INVALID = "proof_invalid"
MANIFEST_INVALID = "manifest_invalid"
CANDIDATE_NOT_FOUND = "candidate_not_found"
CANDIDATE_NOT_VERIFIED = "candidate_not_verified"


class ActiveGenerationResolutionError(RuntimeError):
    """A stable, fail-closed active-generation resolution failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActiveManifestEntry:
    generation_id: str
    article_id: int
    indexed_content_sha256: str
    expected_chunk_count: int
    actual_chunk_count: int
    index_config_fingerprint: str
    index_status: str
    indexed_at: str


@dataclass(frozen=True)
class ActiveGenerationSnapshot:
    """All immutable control-plane data needed to open one serving index."""

    generation_id: str
    corpus_id: str
    collection_name: str
    corpus_snapshot_id: str
    embedding_artifact: str
    index_config: Mapping[str, object]
    index_config_fingerprint: str
    observed_chunk_count: int
    manifest: tuple[ActiveManifestEntry, ...]
    status: str


def _deep_freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


class ActiveGenerationResolver:
    """Resolve serving state from the generation control DB without writes."""

    def __init__(self, control_db_path: Union[str, Path]) -> None:
        self._control_db_path = Path(control_db_path).resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self._control_db_path.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def resolve(self, corpus_id: str) -> ActiveGenerationSnapshot:
        """Resolve the active serving generation for ``corpus_id``."""
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            pointer = connection.execute(
                "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = ?",
                (corpus_id,),
            ).fetchone()
            if pointer is None:
                raise ActiveGenerationResolutionError(
                    MISSING_POINTER,
                    f"no active generation pointer for corpus: {corpus_id}",
                )
            generation = connection.execute(
                "SELECT * FROM index_generations WHERE generation_id = ?",
                (pointer["active_generation_id"],),
            ).fetchone()
            if generation is None or generation["corpus_id"] != corpus_id:
                raise ActiveGenerationResolutionError(
                    POINTER_MISMATCH,
                    f"active generation pointer is inconsistent for corpus: {corpus_id}",
                )
            if generation["status"] == "verified":
                raise ActiveGenerationResolutionError(
                    VERIFIED_NOT_ACTIVE,
                    f"pointed generation is verified but not active: {generation['generation_id']}",
                )
            if generation["status"] != "active":
                raise ActiveGenerationResolutionError(
                    POINTER_MISMATCH,
                    f"pointed generation is not active: {generation['generation_id']}",
                )
            return self._snapshot(connection, generation)
        finally:
            connection.close()

    def open_verified_candidate(self, generation_id: str) -> ActiveGenerationSnapshot:
        """Operator-only inspection of a verified or already-active candidate.

        Serving code must use :meth:`resolve`, which additionally requires the
        active corpus pointer. This method never changes generation state.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            generation = connection.execute(
                "SELECT * FROM index_generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise ActiveGenerationResolutionError(
                    CANDIDATE_NOT_FOUND,
                    f"unknown generation candidate: {generation_id}",
                )
            if generation["status"] not in ("verified", "active"):
                raise ActiveGenerationResolutionError(
                    CANDIDATE_NOT_VERIFIED,
                    f"generation candidate is not verified: {generation_id}",
                )
            return self._snapshot(connection, generation)
        finally:
            connection.close()

    @staticmethod
    def _snapshot(
        connection: sqlite3.Connection, generation: sqlite3.Row
    ) -> ActiveGenerationSnapshot:
        proof = connection.execute(
            "SELECT * FROM generation_chunk_verification WHERE generation_id = ?",
            (generation["generation_id"],),
        ).fetchone()
        if (
            proof is None
            or proof["status"] != "verified"
            or proof["issue_count"] != 0
            or not isinstance(proof["observed_chunk_count"], int)
            or isinstance(proof["observed_chunk_count"], bool)
            or proof["observed_chunk_count"] < 0
        ):
            raise ActiveGenerationResolutionError(
                PROOF_INVALID,
                f"generation chunk proof is missing or invalid: {generation['generation_id']}",
            )
        rows = connection.execute(
            "SELECT * FROM article_index_manifest WHERE generation_id = ? ORDER BY article_id",
            (generation["generation_id"],),
        ).fetchall()
        try:
            decoded_config = json.loads(generation["index_config_json"])
            canonical_config = json.dumps(
                decoded_config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ActiveGenerationResolutionError(
                MANIFEST_INVALID,
                f"generation config is not valid JSON: {generation['generation_id']}",
            ) from error
        if not isinstance(decoded_config, dict) or (
            hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()
            != generation["index_config_fingerprint"]
        ):
            raise ActiveGenerationResolutionError(
                MANIFEST_INVALID,
                f"generation config fingerprint is inconsistent: {generation['generation_id']}",
            )

        seen_article_ids: set[int] = set()
        observed_manifest_chunks = 0
        for row in rows:
            article_id = row["article_id"]
            expected_count = row["expected_chunk_count"]
            actual_count = row["actual_chunk_count"]
            if (
                not isinstance(article_id, int)
                or isinstance(article_id, bool)
                or article_id <= 0
                or article_id in seen_article_ids
                or row["generation_id"] != generation["generation_id"]
                or row["index_status"] != "indexed"
                or not isinstance(expected_count, int)
                or isinstance(expected_count, bool)
                or expected_count <= 0
                or not isinstance(actual_count, int)
                or isinstance(actual_count, bool)
                or actual_count <= 0
                or expected_count != actual_count
                or row["index_config_fingerprint"]
                != generation["index_config_fingerprint"]
            ):
                raise ActiveGenerationResolutionError(
                    MANIFEST_INVALID,
                    f"generation manifest is inconsistent: {generation['generation_id']}",
                )
            seen_article_ids.add(article_id)
            observed_manifest_chunks += actual_count
        if observed_manifest_chunks != proof["observed_chunk_count"]:
            raise ActiveGenerationResolutionError(
                MANIFEST_INVALID,
                f"manifest chunk total does not match proof: {generation['generation_id']}",
            )
        manifest = tuple(
            ActiveManifestEntry(
                row["generation_id"],
                row["article_id"],
                row["indexed_content_sha256"],
                row["expected_chunk_count"],
                row["actual_chunk_count"],
                row["index_config_fingerprint"],
                row["index_status"],
                row["indexed_at"],
            )
            for row in rows
        )
        config = _deep_freeze(decoded_config)
        return ActiveGenerationSnapshot(
            generation_id=generation["generation_id"],
            corpus_id=generation["corpus_id"],
            collection_name=generation["collection_name"],
            corpus_snapshot_id=generation["corpus_snapshot_id"],
            embedding_artifact=generation["embedding_artifact"],
            index_config=config,
            index_config_fingerprint=generation["index_config_fingerprint"],
            observed_chunk_count=proof["observed_chunk_count"],
            manifest=manifest,
            status=generation["status"],
        )
