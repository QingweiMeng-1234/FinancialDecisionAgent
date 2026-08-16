"""Dependency-free builder for one immutable RAG index generation.

This is deliberately an orchestration seam, not a Chroma implementation.  A
backend adapter supplies a *new* collection and exposes the chunk metadata it
actually persisted.  The builder then uses :mod:`index_generation` to prove
that that collection contains exactly the frozen canonical-content snapshot.

It never activates a generation, deletes a collection, imports an embedding
library, or reads the live corpus.  Production adapters may use Chroma and a
real embedder; tests may use deterministic in-memory implementations.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence, TypeVar

try:  # Normal package import, without importing event_collector.__init__ here.
    from . import index_generation
except ImportError:  # Direct-file use for dependency-independent operators/tests.
    _SPEC = importlib.util.spec_from_file_location(
        "_index_generation_builder_dependency", Path(__file__).with_name("index_generation.py")
    )
    assert _SPEC is not None and _SPEC.loader is not None
    index_generation = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = index_generation
    _SPEC.loader.exec_module(index_generation)


Embedding = TypeVar("Embedding")


class NewCollectionRequiredError(RuntimeError):
    """The requested collection already exists and therefore cannot be reused."""


@dataclass(frozen=True)
class CanonicalIndexCandidate:
    """One verified canonical content version from a frozen corpus snapshot."""

    article_id: int
    content: str
    content_sha256: str


@dataclass(frozen=True)
class CollectionChunk:
    """A backend-neutral chunk write request.

    ``embedding`` is opaque intentionally.  This module neither calculates
    embeddings nor makes any claim about the embedding model's quality.
    """

    record_id: str
    document: str
    metadata: Mapping[str, object]
    embedding: object


class CandidateCollection(Protocol):
    """Minimal adapter for a candidate collection, never an active collection."""

    def add_chunks(self, chunks: Sequence[CollectionChunk]) -> None:
        """Persist chunks for one article."""

    def read_chunk_metadata(self) -> Iterable[Mapping[str, object]]:
        """Return metadata read back from the persisted collection."""


class CollectionClient(Protocol):
    """Creates isolated collections and never exposes deletion in this builder."""

    def collection_exists(self, collection_name: str) -> bool:
        """Return whether a collection with this exact name already exists."""

    def create_collection(self, collection_name: str) -> CandidateCollection:
        """Create and return a new, empty collection or raise."""


@dataclass(frozen=True)
class GenerationBuildResult:
    """The builder outcome; successful means verified, never activated."""

    generation_id: str
    collection_name: str
    successful: bool
    chunk_report: index_generation.ChunkVerificationReport | None
    verification_report: index_generation.VerificationReport | None
    error: str | None = None


def build_generation(
    connection: sqlite3.Connection,
    collection_client: CollectionClient,
    *,
    generation_id: str,
    corpus_id: str,
    collection_name: str,
    embedding_artifact: str,
    chunk_config: Mapping[str, object],
    corpus_snapshot_id: str,
    candidates: Iterable[CanonicalIndexCandidate],
    splitter: Callable[[str], Sequence[str]],
    embedder: Callable[[Sequence[str]], Sequence[Embedding]],
    progress_callback: Callable[[], None] | None = None,
) -> GenerationBuildResult:
    """Build and verify a successor generation without activating it.

    The collection name is a write-once namespace: an existing collection is
    rejected, never cleared or reused.  Each article is split, embedded,
    written, and manifested before the next article begins.  Finally metadata
    is read back from the backend and reconciled bidirectionally before the
    manifest itself is verified against the frozen candidate set.

    Any failure after the generation row is created is recorded as ``failed``.
    The failed row and any partially written collection are retained for
    diagnosis; this function intentionally has no deletion or activation path.
    """
    generation = index_generation.create_generation(
        connection,
        generation_id=generation_id,
        corpus_id=corpus_id,
        collection_name=collection_name,
        embedding_artifact=embedding_artifact,
        chunk_config=chunk_config,
        corpus_snapshot_id=corpus_snapshot_id,
    )
    # Persist the attempt before any external collection operation.  Chroma
    # writes cannot participate in this SQLite transaction, so leaving the
    # INSERT pending would let a process crash orphan a collection with no
    # control-plane record and would hold the SQLite writer lock throughout
    # embedding.
    connection.commit()
    chunk_report: index_generation.ChunkVerificationReport | None = None
    verification_report: index_generation.VerificationReport | None = None

    try:
        ordered_candidates = _freeze_candidates(candidates)
        if not ordered_candidates:
            raise ValueError("at least one canonical candidate is required")
        bound_snapshot_id = index_generation.compute_eligible_snapshot_fingerprint(
            (candidate.article_id, candidate.content_sha256)
            for candidate in ordered_candidates
        )
        if corpus_snapshot_id != bound_snapshot_id:
            raise ValueError("corpus_snapshot_id is not bound to the complete candidate set")
        _report_progress(progress_callback)
        if collection_client.collection_exists(collection_name):
            raise NewCollectionRequiredError(
                f"candidate collection already exists: {collection_name!r}"
            )

        collection = collection_client.create_collection(collection_name)
        eligible: dict[int, str] = {}
        for candidate in ordered_candidates:
            _report_progress(progress_callback)
            chunks = _split_candidate(candidate, splitter)
            embeddings = list(embedder(chunks))
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"embedder returned {len(embeddings)} vectors for {len(chunks)} chunks "
                    f"of article {candidate.article_id}"
                )
            writes = tuple(
                CollectionChunk(
                    record_id=_build_chunk_record_id(generation_id, candidate.article_id, chunk_index),
                    document=chunk,
                    embedding=embeddings[chunk_index],
                    metadata={
                        "generation_id": generation_id,
                        "corpus_snapshot_id": corpus_snapshot_id,
                        "article_id": candidate.article_id,
                        "indexed_content_sha256": candidate.content_sha256,
                        "chunk_index": chunk_index,
                        "index_config_fingerprint": generation.index_config_fingerprint,
                        # Keep both a generic model field and the complete artifact identity.
                        "embedding_model": embedding_artifact,
                        "embedding_artifact": embedding_artifact,
                    },
                )
                for chunk_index, chunk in enumerate(chunks)
            )
            collection.add_chunks(writes)
            index_generation.record_article_manifest(
                connection,
                generation_id=generation_id,
                article_id=candidate.article_id,
                indexed_content_sha256=candidate.content_sha256,
                expected_chunk_count=len(writes),
                actual_chunk_count=len(writes),
            )
            # Make completed article progress observable before the next
            # external write.  A crash may still leave unmanifested chunks for
            # the in-flight article, but the generation row remains building
            # and therefore cannot verify or activate.
            connection.commit()
            eligible[candidate.article_id] = candidate.content_sha256

        _report_progress(progress_callback)
        observed = tuple(
            _indexed_chunk_record(
                metadata,
                corpus_snapshot_id=corpus_snapshot_id,
                embedding_artifact=embedding_artifact,
            )
            for metadata in collection.read_chunk_metadata()
        )
        chunk_report = index_generation.reconcile_generation_chunks(
            connection, generation_id, observed
        )
        if not chunk_report.valid:
            return GenerationBuildResult(
                generation_id, collection_name, False, chunk_report, None,
                "chunk reconciliation failed",
            )

        verification_report = index_generation.verify_generation(connection, generation_id, eligible)
        if not verification_report.valid:
            return GenerationBuildResult(
                generation_id, collection_name, False, chunk_report, verification_report,
                "manifest verification failed",
            )
        return GenerationBuildResult(
            generation_id, collection_name, True, chunk_report, verification_report
        )
    except Exception as error:
        _mark_failed(connection, generation_id, str(error))
        if isinstance(error, NewCollectionRequiredError):
            raise
        return GenerationBuildResult(
            generation_id, collection_name, False, chunk_report, verification_report, str(error)
        )


def _freeze_candidates(candidates: Iterable[CanonicalIndexCandidate]) -> tuple[CanonicalIndexCandidate, ...]:
    """Validate exact content identities before any collection is created."""
    by_id: dict[int, CanonicalIndexCandidate] = {}
    for candidate in candidates:
        if not isinstance(candidate, CanonicalIndexCandidate):
            raise TypeError("candidates must contain CanonicalIndexCandidate values")
        if not isinstance(candidate.article_id, int) or isinstance(candidate.article_id, bool) or candidate.article_id <= 0:
            raise ValueError("candidate article_id must be a positive integer")
        if not isinstance(candidate.content, str) or not candidate.content.strip():
            raise ValueError(f"candidate {candidate.article_id} has empty canonical content")
        if not isinstance(candidate.content_sha256, str) or not candidate.content_sha256:
            raise ValueError(f"candidate {candidate.article_id} has no canonical content hash")
        observed_hash = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
        if observed_hash != candidate.content_sha256:
            raise ValueError(f"candidate {candidate.article_id} content hash does not match its canonical hash")
        if candidate.article_id in by_id:
            raise ValueError(f"duplicate canonical candidate article_id: {candidate.article_id}")
        by_id[candidate.article_id] = candidate
    return tuple(by_id[article_id] for article_id in sorted(by_id))


def _report_progress(progress_callback: Callable[[], None] | None) -> None:
    if progress_callback is not None:
        progress_callback()


def _split_candidate(
    candidate: CanonicalIndexCandidate, splitter: Callable[[str], Sequence[str]]
) -> tuple[str, ...]:
    chunks = tuple(splitter(candidate.content))
    if not chunks:
        raise ValueError(f"splitter produced no chunks for article {candidate.article_id}")
    for chunk in chunks:
        if not isinstance(chunk, str) or not chunk.strip():
            raise ValueError(f"splitter produced an empty/non-text chunk for article {candidate.article_id}")
    return chunks


def _build_chunk_record_id(generation_id: str, article_id: int, chunk_index: int) -> str:
    return f"{generation_id}:{article_id}:{chunk_index}"


def _indexed_chunk_record(
    metadata: Mapping[str, object],
    *,
    corpus_snapshot_id: str,
    embedding_artifact: str,
) -> index_generation.IndexedChunkRecord:
    """Turn read-back metadata into strict reconciliation input."""
    required = (
        "generation_id", "article_id", "indexed_content_sha256",
        "chunk_index", "index_config_fingerprint", "corpus_snapshot_id",
        "embedding_model", "embedding_artifact",
    )
    if not isinstance(metadata, Mapping):
        raise ValueError("collection read-back must yield metadata mappings")
    missing = tuple(key for key in required if key not in metadata)
    if missing:
        raise ValueError(f"collection chunk metadata misses required keys: {', '.join(missing)}")
    generation_id = metadata["generation_id"]
    article_id = metadata["article_id"]
    content_hash = metadata["indexed_content_sha256"]
    chunk_index = metadata["chunk_index"]
    config_fingerprint = metadata["index_config_fingerprint"]
    observed_snapshot_id = metadata["corpus_snapshot_id"]
    observed_model = metadata["embedding_model"]
    observed_artifact = metadata["embedding_artifact"]
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("collection chunk generation_id must be non-empty text")
    if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id <= 0:
        raise ValueError("collection chunk article_id must be a positive integer")
    if not isinstance(content_hash, str) or not content_hash:
        raise ValueError("collection chunk indexed_content_sha256 must be non-empty text")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool):
        raise ValueError("collection chunk chunk_index must be an integer")
    if not isinstance(config_fingerprint, str) or not config_fingerprint:
        raise ValueError("collection chunk index_config_fingerprint must be non-empty text")
    if observed_snapshot_id != corpus_snapshot_id:
        raise ValueError("collection chunk corpus_snapshot_id does not match the generation")
    if observed_model != embedding_artifact or observed_artifact != embedding_artifact:
        raise ValueError("collection chunk embedding artifact does not match the generation")
    return index_generation.IndexedChunkRecord(
        generation_id, article_id, content_hash, chunk_index, config_fingerprint
    )


def _mark_failed(connection: sqlite3.Connection, generation_id: str, reason: str) -> None:
    """Persist failure without disturbing a generation that already became terminal."""
    with connection:
        connection.execute(
            "UPDATE index_generations SET status = ?, failure_reason = ? "
            "WHERE generation_id = ? AND status = ?",
            (index_generation.FAILED, reason[:1000], generation_id, index_generation.BUILDING),
        )
