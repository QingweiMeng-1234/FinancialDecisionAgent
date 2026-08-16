"""Runtime builder for one verified, non-active RAG successor generation.

The refresh ledger deliberately accepts a :class:`SuccessorGenerationProof`
only when this coordinator has rebuilt the *current canonical snapshot*.  It
does not inspect, reuse, or activate the serving generation: a new article can
therefore never be proven merely because an older active index exists.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4

from event_collector import index_generation
from event_collector.chroma_generation_adapter import (
    GenerationDependencies,
    create_chroma_generation_dependencies,
)
from event_collector.generation_corpus import build_generation_corpus_snapshot
from event_collector.index_generation_builder import (
    CanonicalIndexCandidate,
    GenerationBuildResult,
    build_generation,
)
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationBuildRequest,
    SuccessorGenerationProof,
)


DEFAULT_EMBEDDING_ARTIFACT = "all-MiniLM-L6-v2"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_SPLITTER_ID = "fixed-character-v1"
DEFAULT_LEASE_HEARTBEAT_INTERVAL_SECONDS = 30.0


class SuccessorGenerationCoordinatorError(RuntimeError):
    """A refresh candidate could not be proven as a new verified generation."""


class _LeaseHeartbeatFailed(RuntimeError):
    """The refresh owner lease could not be renewed by the watchdog."""


class _LeaseHeartbeatWatchdog:
    """Keep a refresh lease live while synchronous generation work is blocked."""

    def __init__(self, heartbeat: Callable[[], None], *, interval_seconds: float) -> None:
        self._heartbeat = heartbeat
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._lock = RLock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        self.pulse()
        self._thread = Thread(
            target=self._run,
            name="successor-lease-watchdog",
            daemon=True,
        )
        self._thread.start()

    def pulse(self) -> None:
        self.raise_if_failed()
        try:
            with self._lock:
                self.raise_if_failed()
                self._heartbeat()
        except Exception as error:
            self._record_failure(error)
            raise _LeaseHeartbeatFailed("lease heartbeat failed") from error
        self.raise_if_failed()

    def raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise _LeaseHeartbeatFailed("lease heartbeat failed") from failure

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.pulse()
            except _LeaseHeartbeatFailed:
                self._stop_event.set()
                return

    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = error


@dataclass(frozen=True)
class SuccessorGenerationCoordinatorConfig:
    """Operator-owned input/output locations and immutable build settings."""

    control_db_path: str | Path
    canonical_db_path: str | Path
    canonical_content_root: str | Path
    chroma_persist_dir: str | Path
    embedding_artifact: str = DEFAULT_EMBEDDING_ARTIFACT
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    collection_prefix: str = "news_articles_successor"
    activate_verified_generation: bool = False


class RuntimeSuccessorGenerationCoordinator:
    """Freeze, build, verify, and optionally activate an isolated successor."""

    def __init__(
        self,
        config: SuccessorGenerationCoordinatorConfig,
        *,
        dependencies_factory: Callable[..., GenerationDependencies] = create_chroma_generation_dependencies,
        generation_token_factory: Callable[[], str] | None = None,
        snapshot_builder: Callable[..., Any] = build_generation_corpus_snapshot,
        generation_builder: Callable[..., GenerationBuildResult] = build_generation,
        lease_heartbeat_interval_seconds: float = DEFAULT_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._config = _validate_config(config)
        self._dependencies_factory = dependencies_factory
        self._generation_token_factory = generation_token_factory or (lambda: uuid4().hex)
        self._snapshot_builder = snapshot_builder
        self._generation_builder = generation_builder
        if (
            not isinstance(lease_heartbeat_interval_seconds, (int, float))
            or isinstance(lease_heartbeat_interval_seconds, bool)
            or lease_heartbeat_interval_seconds <= 0
        ):
            raise ValueError("lease_heartbeat_interval_seconds must be positive")
        self._lease_heartbeat_interval_seconds = float(lease_heartbeat_interval_seconds)

    def __call__(self, request: SuccessorGenerationBuildRequest) -> SuccessorGenerationProof:
        """Build one fresh candidate and return proof only after full read-back.

        ``request.articles`` is evidence from the refresh attempt, not the
        candidate set.  The candidate set is frozen afresh from canonical
        storage, so historical ready rows remain covered and accepted refresh
        rows must agree with that snapshot before Chroma is opened.
        """
        _validate_request(request)
        watchdog = (
            _LeaseHeartbeatWatchdog(
                request.lease_heartbeat,
                interval_seconds=self._lease_heartbeat_interval_seconds,
            )
            if request.lease_heartbeat is not None
            else None
        )
        connection: sqlite3.Connection | None = None
        generation_id: str | None = None
        try:
            if watchdog is not None:
                watchdog.start()
            snapshot = self._snapshot_builder(
                self._config.canonical_db_path,
                self._config.canonical_content_root,
            )
            candidates = tuple(getattr(snapshot, "candidates", ()))
            if not candidates:
                raise SuccessorGenerationCoordinatorError(
                    "current canonical snapshot has no eligible articles"
                )
            _validate_refresh_articles_against_snapshot(request.articles, candidates)

            token = _identity_token(self._generation_token_factory())
            generation_id = f"{request.corpus_id}-successor-{token}"
            collection_name = f"{self._config.collection_prefix}_{token}"
            snapshot_id = getattr(snapshot, "eligible_snapshot_fingerprint", None)
            if not _is_sha256(snapshot_id):
                raise SuccessorGenerationCoordinatorError(
                    "frozen canonical snapshot has an invalid fingerprint"
                )
            canonical_candidates = tuple(
                CanonicalIndexCandidate(
                    candidate.article_id,
                    candidate.content,
                    candidate.content_sha256,
                )
                for candidate in candidates
            )
            chunk_config = {
                "chunk_size": self._config.chunk_size,
                "chunk_overlap": self._config.chunk_overlap,
                "splitter": DEFAULT_SPLITTER_ID,
            }
            expected_fingerprint = index_generation.compute_index_config_fingerprint(
                chunk_config
            )

            control_path = Path(self._config.control_db_path)
            control_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(control_path)
            index_generation.initialize_schema(connection)
            active_before = _active_pointer(connection, request.corpus_id)
            if watchdog is not None:
                watchdog.raise_if_failed()
            dependencies = self._dependencies_factory(
                persist_dir=self._config.chroma_persist_dir,
                generation_id=generation_id,
                corpus_snapshot_id=snapshot_id,
                embedding_artifact=self._config.embedding_artifact,
                index_config_fingerprint=expected_fingerprint,
                chunk_config=chunk_config,
                allow_download=False,
            )
            result = self._generation_builder(
                connection,
                dependencies.collection_client,
                generation_id=generation_id,
                corpus_id=request.corpus_id,
                collection_name=collection_name,
                embedding_artifact=self._config.embedding_artifact,
                chunk_config=chunk_config,
                corpus_snapshot_id=snapshot_id,
                candidates=canonical_candidates,
                splitter=_fixed_window_splitter(
                    self._config.chunk_size, self._config.chunk_overlap
                ),
                embedder=dependencies.embedder,
                progress_callback=watchdog.pulse if watchdog is not None else None,
            )
            if watchdog is not None:
                watchdog.raise_if_failed()
            if not result.successful:
                raise SuccessorGenerationCoordinatorError(
                    result.error or "successor generation did not verify"
                )
            _verify_persisted_candidate(
                connection,
                generation_id=generation_id,
                corpus_id=request.corpus_id,
                corpus_snapshot_id=snapshot_id,
                index_config_fingerprint=expected_fingerprint,
                candidates=canonical_candidates,
                active_before=active_before,
            )
            if watchdog is not None:
                watchdog.stop()
                watchdog.raise_if_failed()
            if self._config.activate_verified_generation:
                try:
                    index_generation.activate_generation(
                        connection,
                        generation_id,
                        expected_current_generation_id=active_before,
                    )
                except (index_generation.GenerationStateError, sqlite3.Error) as error:
                    raise SuccessorGenerationCoordinatorError(
                        "verified successor generation activation failed"
                    ) from error
        except _LeaseHeartbeatFailed as error:
            if connection is not None and generation_id is not None:
                _mark_generation_failed_after_lease_loss(connection, generation_id)
            raise SuccessorGenerationCoordinatorError("lease heartbeat failed") from error
        finally:
            if watchdog is not None:
                watchdog.stop()
            if connection is not None:
                connection.close()

        return SuccessorGenerationProof(
            corpus_id=request.corpus_id,
            generation_id=generation_id,
            corpus_snapshot_id=snapshot_id,
            index_config_fingerprint=expected_fingerprint,
            status=index_generation.VERIFIED,
            chunk_verification_valid=True,
            articles=tuple(
                GenerationArticleProof(candidate.article_id, candidate.content_sha256)
                for candidate in canonical_candidates
            ),
        )


def create_runtime_successor_generation_coordinator(
    config: SuccessorGenerationCoordinatorConfig,
) -> RuntimeSuccessorGenerationCoordinator:
    """Create the default MCP refresh coordinator without opening any backend."""
    return RuntimeSuccessorGenerationCoordinator(config)


def _mark_generation_failed_after_lease_loss(
    connection: sqlite3.Connection,
    generation_id: str,
) -> None:
    """Make a possibly verified candidate ineligible after its owner lease is lost."""

    try:
        connection.execute(
            """
            UPDATE index_generations
            SET status = 'failed', failure_reason = ?
            WHERE generation_id = ? AND status != 'active'
            """,
            ("refresh lease heartbeat failed", generation_id),
        )
        connection.commit()
    except sqlite3.Error as error:
        raise SuccessorGenerationCoordinatorError(
            "lease heartbeat failed and candidate invalidation failed"
        ) from error


def _validate_config(
    config: SuccessorGenerationCoordinatorConfig,
) -> SuccessorGenerationCoordinatorConfig:
    if not isinstance(config, SuccessorGenerationCoordinatorConfig):
        raise TypeError("config must be a SuccessorGenerationCoordinatorConfig")
    for value, label in (
        (config.control_db_path, "control_db_path"),
        (config.canonical_db_path, "canonical_db_path"),
        (config.canonical_content_root, "canonical_content_root"),
        (config.chroma_persist_dir, "chroma_persist_dir"),
        (config.embedding_artifact, "embedding_artifact"),
        (config.collection_prefix, "collection_prefix"),
    ):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError(f"{label} must be non-empty")
    if not isinstance(config.chunk_size, int) or isinstance(config.chunk_size, bool) or config.chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if not isinstance(config.chunk_overlap, int) or isinstance(config.chunk_overlap, bool) or config.chunk_overlap < 0:
        raise ValueError("chunk_overlap must be a non-negative integer")
    if config.chunk_overlap >= config.chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    if not isinstance(config.activate_verified_generation, bool):
        raise ValueError("activate_verified_generation must be boolean")
    return config


def _validate_request(request: SuccessorGenerationBuildRequest) -> None:
    if not isinstance(request, SuccessorGenerationBuildRequest):
        raise TypeError("request must be a SuccessorGenerationBuildRequest")
    for value, label in (
        (request.run_id, "run_id"),
        (request.scope_key, "scope_key"),
        (request.corpus_id, "corpus_id"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise SuccessorGenerationCoordinatorError(f"{label} must be non-empty")
    if not request.articles:
        raise SuccessorGenerationCoordinatorError(
            "a successor generation requires at least one accepted refresh article"
        )


def _validate_refresh_articles_against_snapshot(
    articles: tuple[GenerationArticleProof, ...], candidates: tuple[Any, ...]
) -> None:
    snapshot_hashes = {
        candidate.article_id: candidate.content_sha256 for candidate in candidates
    }
    observed_ids: set[int] = set()
    for article in articles:
        article_id = getattr(article, "article_id", None)
        content_hash = getattr(article, "indexed_content_sha256", None)
        if (
            not isinstance(article_id, int)
            or isinstance(article_id, bool)
            or article_id <= 0
            or article_id in observed_ids
            or not _is_sha256(content_hash)
        ):
            raise SuccessorGenerationCoordinatorError(
                "refresh generation article proof is invalid"
            )
        observed_ids.add(article_id)
        if snapshot_hashes.get(article_id) != content_hash:
            raise SuccessorGenerationCoordinatorError(
                f"refresh article {article_id} does not match frozen canonical snapshot"
            )


def _verify_persisted_candidate(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    corpus_id: str,
    corpus_snapshot_id: str,
    index_config_fingerprint: str,
    candidates: tuple[CanonicalIndexCandidate, ...],
    active_before: str | None,
) -> None:
    generation = connection.execute(
        """
        SELECT corpus_id, corpus_snapshot_id, index_config_fingerprint, status
        FROM index_generations WHERE generation_id = ?
        """,
        (generation_id,),
    ).fetchone()
    if generation is None or tuple(generation) != (
        corpus_id,
        corpus_snapshot_id,
        index_config_fingerprint,
        index_generation.VERIFIED,
    ):
        raise SuccessorGenerationCoordinatorError(
            "generation control record is not a verified successor"
        )
    chunk_proof = connection.execute(
        """
        SELECT status, issue_count, observed_chunk_count
        FROM generation_chunk_verification WHERE generation_id = ?
        """,
        (generation_id,),
    ).fetchone()
    if (
        chunk_proof is None
        or chunk_proof[0] != index_generation.VERIFIED
        or chunk_proof[1] != 0
        or not isinstance(chunk_proof[2], int)
        or chunk_proof[2] <= 0
    ):
        raise SuccessorGenerationCoordinatorError(
            "generation has no valid chunk verification proof"
        )
    manifest = connection.execute(
        """
        SELECT article_id, indexed_content_sha256, expected_chunk_count,
               actual_chunk_count, index_config_fingerprint, index_status
        FROM article_index_manifest WHERE generation_id = ? ORDER BY article_id
        """,
        (generation_id,),
    ).fetchall()
    expected = [
        (candidate.article_id, candidate.content_sha256) for candidate in candidates
    ]
    if len(manifest) != len(expected):
        raise SuccessorGenerationCoordinatorError(
            "generation manifest does not cover frozen canonical snapshot"
        )
    for record, (article_id, content_hash) in zip(manifest, expected, strict=True):
        if (
            record[0] != article_id
            or record[1] != content_hash
            or record[2] <= 0
            or record[3] != record[2]
            or record[4] != index_config_fingerprint
            or record[5] != "indexed"
        ):
            raise SuccessorGenerationCoordinatorError(
                "generation manifest is inconsistent with frozen canonical snapshot"
            )
    if _active_pointer(connection, corpus_id) != active_before:
        raise SuccessorGenerationCoordinatorError(
            "successor generation unexpectedly changed the active pointer"
        )


def _active_pointer(connection: sqlite3.Connection, corpus_id: str) -> str | None:
    row = connection.execute(
        "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = ?",
        (corpus_id,),
    ).fetchone()
    return None if row is None else row[0]


def _fixed_window_splitter(chunk_size: int, chunk_overlap: int) -> Callable[[str], tuple[str, ...]]:
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


def _identity_token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise SuccessorGenerationCoordinatorError(
            "generation token must contain only letters, digits, underscores, or hyphens"
        )
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_EMBEDDING_ARTIFACT",
    "RuntimeSuccessorGenerationCoordinator",
    "SuccessorGenerationCoordinatorConfig",
    "SuccessorGenerationCoordinatorError",
    "create_runtime_successor_generation_coordinator",
]
