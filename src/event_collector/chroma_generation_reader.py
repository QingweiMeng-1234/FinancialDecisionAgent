"""Chroma search bound to one immutable eligibility and source snapshot."""

from __future__ import annotations

import atexit
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import tempfile
from threading import RLock
from typing import Any
import warnings
import weakref

from event_collector.generation_eligibility import EligibilitySnapshot


class GenerationReaderContractError(RuntimeError):
    """The persisted collection does not match its pinned control-plane proof."""


@dataclass
class _RuntimeClone:
    cache_key: str
    source_snapshot: Mapping[str, tuple[int, int, str]]
    runtime_directory: tempfile.TemporaryDirectory
    runtime_path: Path
    client: Any
    reader_count: int = 0
    retired: bool = False


_RUNTIME_CLONE_LOCK = RLock()
_RUNTIME_CLONES: dict[str, _RuntimeClone] = {}
_ALL_RUNTIME_CLONES: dict[str, _RuntimeClone] = {}
MAX_RETIRED_RUNTIME_CLONES = 2


def _cleanup_runtime_clones() -> None:
    """Release Chroma systems before deleting Windows runtime-clone files."""
    with _RUNTIME_CLONE_LOCK:
        for clone in list(_ALL_RUNTIME_CLONES.values()):
            clone.retired = True
            clone.reader_count = 0
            _cleanup_runtime_clone_locked(clone, warn_only=True)


atexit.register(_cleanup_runtime_clones)


class ChromaGenerationReader:
    """Query adapter for one request-pinned active generation.

    The production default snapshots the operator-owned Chroma source into a
    private writable process-lifetime clone and opens PersistentClient only on
    that clone. Chroma bookkeeping writes therefore never touch the immutable
    source generation. An injected client/factory is an explicit integration
    boundary and is responsible for equivalent isolation.
    """

    def __init__(
        self,
        eligibility: EligibilitySnapshot,
        *,
        persist_dir: str | Path | None = None,
        client: Any | None = None,
        client_factory: Callable[[str], Any] | None = None,
        embedder: Any | None = None,
        embedder_factory: Callable[[str], Any] | None = None,
        max_matched_chunks: int = 2,
    ) -> None:
        if not isinstance(eligibility, EligibilitySnapshot):
            raise TypeError("eligibility must be an EligibilitySnapshot")
        if client is not None and client_factory is not None:
            raise ValueError("provide either client or client_factory, not both")
        if embedder is not None and embedder_factory is not None:
            raise ValueError("provide either embedder or embedder_factory, not both")
        _validate_eligibility_binding(eligibility)
        self.source_persist_dir: str | None = None
        self.runtime_persist_dir: str | None = None
        self._runtime_clone_finalizer: weakref.finalize | None = None
        if client is None:
            if persist_dir is None:
                raise ValueError("persist_dir is required when client is not injected")
            self.source_persist_dir = str(Path(persist_dir).resolve())
            if client_factory is not None:
                client = client_factory(str(persist_dir))
                self.runtime_persist_dir = str(persist_dir)
            else:
                runtime_clone = _default_client(str(persist_dir))
                client = runtime_clone.client
                self.runtime_persist_dir = str(runtime_clone.runtime_path)
                self._runtime_clone_finalizer = weakref.finalize(
                    self, _release_runtime_clone, runtime_clone
                )
        self.eligibility = eligibility
        self.active_generation = eligibility.active_generation
        self.client = client
        try:
            self.collection = client.get_collection(name=eligibility.collection_name)
        except Exception as error:
            self.close()
            raise GenerationReaderContractError(
                f"active generation collection is unavailable: {eligibility.collection_name}"
            ) from error
        try:
            self._validate_collection()
        except Exception:
            self.close()
            raise
        self._embedder = embedder
        self._embedder_factory = embedder_factory or _default_embedder
        self.max_matched_chunks = max(1, int(max_matched_chunks))
        # Watchlist fan-out may share one request-pinned reader.  Neither
        # lazy model construction nor a Chroma PersistentClient query is
        # assumed thread-safe without this request-local serialization.
        self._search_lock = RLock()

    def close(self) -> None:
        """Release this pin; retired clones are deleted after the last reader."""
        finalizer = self._runtime_clone_finalizer
        if finalizer is not None and finalizer.alive:
            finalizer()

    def _validate_collection(self) -> None:
        metadata = getattr(self.collection, "metadata", None)
        if not isinstance(metadata, Mapping):
            raise GenerationReaderContractError("collection metadata must be a mapping")
        active = self.active_generation
        expected = {
            "hnsw:space": "cosine",
            "generation_id": active.generation_id,
            "corpus_snapshot_id": active.corpus_snapshot_id,
            "embedding_artifact": active.embedding_artifact,
            "index_config_fingerprint": active.index_config_fingerprint,
            "chunk_config_json": json.dumps(
                dict(active.index_config), sort_keys=True, separators=(",", ":")
            ),
        }
        for key, value in dict(active.index_config).items():
            if isinstance(value, (str, int, float, bool)):
                expected[key] = value
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise GenerationReaderContractError(
                    f"collection metadata mismatch for {key}"
                )
        try:
            count = self.collection.count()
        except Exception as error:
            raise GenerationReaderContractError("could not read collection count") from error
        if count != active.observed_chunk_count:
            raise GenerationReaderContractError(
                "collection count does not match observed generation chunk count"
            )

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        allowed_article_ids: set[int] | None = None,
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        latest_at: str | datetime | None = None,
        lookback_days: int | None = None,
    ) -> list[dict]:
        with self._search_lock:
            return self._search_unlocked(
                query,
                top_k=top_k,
                allowed_article_ids=allowed_article_ids,
                start_at=start_at,
                end_at=end_at,
                latest_at=latest_at,
                lookback_days=lookback_days,
            )

    def _search_unlocked(
        self,
        query: str,
        top_k: int = 5,
        *,
        allowed_article_ids: set[int] | None = None,
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        latest_at: str | datetime | None = None,
        lookback_days: int | None = None,
    ) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty text")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        eligible_ids = self._eligible_ids_for_time_window(
            start_at=start_at,
            end_at=end_at,
            latest_at=latest_at,
            lookback_days=lookback_days,
        )
        if allowed_article_ids is not None:
            eligible_ids.intersection_update(allowed_article_ids)
        if not eligible_ids:
            return []
        embedder = self._load_embedder()
        embedding = embedder.encode(query)
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        if not isinstance(embedding, Sequence) or isinstance(embedding, (str, bytes)):
            raise GenerationReaderContractError("embedder returned an invalid query embedding")
        payload = self.collection.query(
            query_embeddings=[list(embedding)],
            n_results=max(top_k * self.max_matched_chunks, top_k),
            where={"article_id": {"$in": sorted(eligible_ids)}},
            include=["metadatas", "documents", "distances"],
        )
        rows = self._validate_query_payload(payload, eligible_ids)
        return self._hydrate_results(rows, top_k)

    def with_time_window(
        self,
        *,
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        latest_at: str | datetime | None = None,
        lookback_days: int | None = None,
    ) -> "TimeScopedGenerationReader":
        """Bind one validated UTC time window before downstream retrieval starts."""

        return TimeScopedGenerationReader(
            self,
            allowed_article_ids=self._eligible_ids_for_time_window(
                start_at=start_at,
                end_at=end_at,
                latest_at=latest_at,
                lookback_days=lookback_days,
            ),
        )

    def _eligible_ids_for_time_window(
        self,
        *,
        start_at: str | datetime | None,
        end_at: str | datetime | None,
        latest_at: str | datetime | None,
        lookback_days: int | None,
    ) -> set[int]:
        window = _normalize_time_window(
            start_at=start_at,
            end_at=end_at,
            latest_at=latest_at,
            lookback_days=lookback_days,
        )
        if window is None:
            return set(self.eligibility.eligible_article_ids)
        start, end = window
        eligible_ids: set[int] = set()
        for article in self.eligibility.articles:
            if article.published_at_provenance in {"unknown", "legacy_unverified"}:
                continue
            published_at = _parse_published_at(article.source_published_at)
            if published_at is None:
                continue
            if start is not None and published_at < start:
                continue
            if end is not None and published_at > end:
                continue
            eligible_ids.add(article.article_id)
        return eligible_ids

    def _load_embedder(self) -> Any:
        with self._search_lock:
            if self._embedder is None:
                self._embedder = self._embedder_factory(
                    self.active_generation.embedding_artifact
                )
            return self._embedder

    def _validate_query_payload(
        self, payload: object, eligible_ids: set[int]
    ) -> list[tuple[str, Mapping[str, object], str, float]]:
        if not isinstance(payload, Mapping):
            raise GenerationReaderContractError("collection query payload must be a mapping")
        values: list[Sequence[object]] = []
        for key in ("ids", "metadatas", "documents", "distances"):
            outer = payload.get(key)
            if not _is_sequence(outer) or len(outer) != 1 or not _is_sequence(outer[0]):
                raise GenerationReaderContractError(f"collection query {key} shape is invalid")
            values.append(outer[0])
        ids, metadatas, documents, distances = values
        if len({len(ids), len(metadatas), len(documents), len(distances)}) != 1:
            raise GenerationReaderContractError("collection query result lengths are inconsistent")
        article_by_id = {article.article_id: article for article in self.eligibility.articles}
        manifest_by_id = {
            entry.article_id: entry for entry in self.active_generation.manifest
        }
        observed_ids: set[str] = set()
        validated = []
        for record_id, metadata, document, distance in zip(
            ids, metadatas, documents, distances, strict=True
        ):
            if not isinstance(record_id, str) or not record_id:
                raise GenerationReaderContractError("chunk record id must be non-empty text")
            if record_id in observed_ids:
                raise GenerationReaderContractError("collection query returned duplicate chunk record id")
            observed_ids.add(record_id)
            if not isinstance(metadata, Mapping):
                raise GenerationReaderContractError("chunk metadata must be a mapping")
            article_id = metadata.get("article_id")
            chunk_index = metadata.get("chunk_index")
            if metadata.get("generation_id") != self.active_generation.generation_id:
                raise GenerationReaderContractError("chunk generation_id mismatch")
            if metadata.get("corpus_snapshot_id") != self.active_generation.corpus_snapshot_id:
                raise GenerationReaderContractError("chunk corpus_snapshot_id mismatch")
            if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id <= 0:
                raise GenerationReaderContractError("chunk article_id is invalid")
            if article_id not in eligible_ids or article_id not in article_by_id:
                raise GenerationReaderContractError("chunk article_id is outside eligible query scope")
            if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
                raise GenerationReaderContractError("chunk chunk_index is invalid")
            manifest = manifest_by_id.get(article_id)
            article = article_by_id[article_id]
            if manifest is None:
                raise GenerationReaderContractError("chunk article_id is absent from active manifest")
            if metadata.get("indexed_content_sha256") != article.indexed_content_sha256:
                raise GenerationReaderContractError("chunk indexed_content_sha256 mismatch")
            if metadata.get("indexed_content_sha256") != manifest.indexed_content_sha256:
                raise GenerationReaderContractError("chunk hash disagrees with active manifest")
            if metadata.get("index_config_fingerprint") != self.active_generation.index_config_fingerprint:
                raise GenerationReaderContractError("chunk index_config_fingerprint mismatch")
            if metadata.get("embedding_model") != self.active_generation.embedding_artifact:
                raise GenerationReaderContractError("chunk embedding_model mismatch")
            if metadata.get("embedding_artifact") != self.active_generation.embedding_artifact:
                raise GenerationReaderContractError("chunk embedding_artifact mismatch")
            expected_record_id = f"{self.active_generation.generation_id}:{article_id}:{chunk_index}"
            if record_id != expected_record_id:
                raise GenerationReaderContractError(
                    "chunk record id is inconsistent with article_id/chunk_index"
                )
            if chunk_index >= manifest.actual_chunk_count:
                raise GenerationReaderContractError("chunk chunk_index exceeds active manifest")
            if not isinstance(document, str) or not document.strip():
                raise GenerationReaderContractError("chunk document must be non-empty text")
            if not isinstance(distance, (int, float)) or isinstance(distance, bool):
                raise GenerationReaderContractError("chunk distance must be numeric")
            validated.append((record_id, metadata, document, float(distance)))
        return validated

    def _hydrate_results(
        self,
        rows: list[tuple[str, Mapping[str, object], str, float]],
        top_k: int,
    ) -> list[dict]:
        article_by_id = {article.article_id: article for article in self.eligibility.articles}
        grouped: dict[int, dict] = {}
        for record_id, metadata, document, distance in rows:
            article = article_by_id[int(metadata["article_id"])]
            story_group_id = article.story_group_id or article.article_id
            item = grouped.get(story_group_id)
            if item is None:
                item = _hydrated_article_result(
                    article,
                    generation_id=self.active_generation.generation_id,
                    corpus_snapshot_id=self.active_generation.corpus_snapshot_id,
                    index_config_fingerprint=self.active_generation.index_config_fingerprint,
                    distance=distance,
                )
                grouped[story_group_id] = item
            item["distance"] = min(item["distance"], distance)
            item["duplicate_article_ids"].add(article.article_id)
            item["publisher_sources"][article.article_id] = {
                "article_id": article.article_id,
                "source_id": article.publisher_source_id,
                "source_name": article.publisher_source_name,
                "url": article.canonical_url or article.original_url or article.url or "N/A",
            }
            item["matched_chunks"].append(
                {
                    "chunk_id": record_id,
                    "chunk_index": int(metadata["chunk_index"]),
                    "content": document,
                    "distance": distance,
                }
            )
        results = []
        for item in grouped.values():
            chunks = sorted(item["matched_chunks"], key=lambda value: value["distance"])
            chunks = chunks[: self.max_matched_chunks]
            item["matched_chunks"] = chunks
            item["content"] = "\n".join(chunk["content"] for chunk in chunks).strip()
            item["duplicate_article_ids"] = sorted(item["duplicate_article_ids"])
            item["publisher_sources"] = list(item["publisher_sources"].values())
            results.append(item)
        results.sort(key=lambda value: value["distance"])
        return results[:top_k]


def _hydrated_article_result(
    article: Any,
    *,
    generation_id: str,
    corpus_snapshot_id: str,
    index_config_fingerprint: str,
    distance: float,
) -> dict:
    return {
        "id": str(article.article_id),
        "article_id": article.article_id,
        "story_group_id": article.story_group_id or article.article_id,
        "title": article.title,
        "url": article.canonical_url or article.original_url or article.url or "N/A",
        "original_url": article.original_url,
        "canonical_url": article.canonical_url,
        "normalized_url": article.normalized_url,
        "summary": article.summary,
        "source": article.source,
        "published_at": article.published_at,
        "source_published_at": article.source_published_at,
        "published_at_provenance": article.published_at_provenance,
        "content_sha256": article.indexed_content_sha256,
        "indexed_content_sha256": article.indexed_content_sha256,
        "publisher_source_id": article.publisher_source_id,
        "publisher_source_name": article.publisher_source_name,
        "story_dedupe_method": article.story_dedupe_method,
        "content_validation_status": article.content_validation_status,
        "content_validation_reason": article.content_validation_reason,
        "content_validator_version": article.content_validator_version,
        "final_response_url": article.final_response_url,
        "response_status_code": article.response_status_code,
        "response_content_type": article.response_content_type,
        "extractor_version": article.extractor_version,
        "extracted_char_count": article.extracted_char_count,
        "fetched_at": article.fetched_at,
        "raw_json": article.raw_json,
        "generation_id": generation_id,
        "corpus_snapshot_id": corpus_snapshot_id,
        "index_config_fingerprint": index_config_fingerprint,
        "distance": distance,
        "matched_chunks": [],
        "duplicate_article_ids": set(),
        "publisher_sources": {},
    }


def _validate_eligibility_binding(eligibility: EligibilitySnapshot) -> None:
    active = eligibility.active_generation
    bound_values = (
        (eligibility.generation_id, active.generation_id),
        (eligibility.corpus_id, active.corpus_id),
        (eligibility.collection_name, active.collection_name),
        (eligibility.corpus_snapshot_id, active.corpus_snapshot_id),
        (eligibility.embedding_artifact, active.embedding_artifact),
        (eligibility.index_config_fingerprint, active.index_config_fingerprint),
    )
    if any(observed != expected for observed, expected in bound_values):
        raise GenerationReaderContractError(
            "eligibility snapshot is not bound to its active generation"
        )


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


class TimeScopedGenerationReader:
    """A request-local reader whose eligible IDs were time-filtered up front."""

    def __init__(
        self,
        reader: ChromaGenerationReader,
        *,
        allowed_article_ids: set[int],
    ) -> None:
        self._reader = reader
        self._allowed_article_ids = frozenset(allowed_article_ids)
        self.active_generation = reader.active_generation
        self.eligibility = reader.eligibility

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        allowed_article_ids: set[int] | None = None,
        **kwargs: Any,
    ) -> list[dict]:
        if kwargs:
            raise ValueError("time scope is already bound to this generation reader")
        scoped_ids = set(self._allowed_article_ids)
        if allowed_article_ids is not None:
            scoped_ids.intersection_update(allowed_article_ids)
        return self._reader.search(query, top_k=top_k, allowed_article_ids=scoped_ids)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reader, name)


def _normalize_time_window(
    *,
    start_at: str | datetime | None,
    end_at: str | datetime | None,
    latest_at: str | datetime | None,
    lookback_days: int | None,
) -> tuple[datetime | None, datetime | None] | None:
    explicit_requested = start_at is not None or end_at is not None
    rolling_requested = latest_at is not None or lookback_days is not None
    if explicit_requested and rolling_requested:
        raise ValueError("start_at/end_at cannot be combined with latest_at/lookback_days")
    if not explicit_requested and not rolling_requested:
        return None
    if explicit_requested:
        start = _parse_utc_boundary(start_at, "start_at") if start_at is not None else None
        end = _parse_utc_boundary(end_at, "end_at") if end_at is not None else None
    else:
        if latest_at is None or lookback_days is None:
            raise ValueError("latest_at and lookback_days must be supplied together")
        if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days <= 0:
            raise ValueError("lookback_days must be a positive integer")
        end = _parse_utc_boundary(latest_at, "latest_at")
        start = end - timedelta(days=lookback_days)
    if start is not None and end is not None and start > end:
        raise ValueError("start_at must not be later than end_at")
    return start, end


def _parse_utc_boundary(value: str | datetime, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{name} must be an ISO-8601 UTC-aware timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be UTC-aware")
    return value.astimezone(timezone.utc)


def _parse_published_at(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        return _parse_utc_boundary(value, "published_at")
    except ValueError:
        return None


def _default_client(persist_dir: str) -> _RuntimeClone:
    persist_path = Path(persist_dir).resolve()
    if not persist_path.is_dir():
        raise GenerationReaderContractError(
            "Chroma source persist directory is unavailable for read-only serving"
        )
    if not (persist_path / "chroma.sqlite3").is_file():
        raise GenerationReaderContractError(
            "Chroma source persist directory is missing chroma.sqlite3 for read-only serving"
        )
    cache_key = str(persist_path)
    with _RUNTIME_CLONE_LOCK:
        source_snapshot = _snapshot_chroma_tree(persist_path)
        cached = _RUNTIME_CLONES.get(cache_key)
        if (
            cached is not None
            and not cached.retired
            and cached.source_snapshot == source_snapshot
        ):
            cached.reader_count += 1
            return cached
        if cached is not None:
            _RUNTIME_CLONES.pop(cache_key, None)
            cached.retired = True
            if cached.reader_count == 0 and not _cleanup_runtime_clone_locked(cached):
                raise GenerationReaderContractError(
                    "retired Chroma runtime clone could not be reclaimed"
                )
        retired_count = sum(
            1 for clone in _ALL_RUNTIME_CLONES.values() if clone.retired
        )
        if retired_count >= MAX_RETIRED_RUNTIME_CLONES:
            raise GenerationReaderContractError(
                "too many retired Chroma runtime clones remain pinned"
            )

        runtime_directory = tempfile.TemporaryDirectory(
            prefix=f"financial-agent-chroma-{hashlib.sha256(cache_key.encode()).hexdigest()[:12]}-",
        )
        runtime_path = Path(runtime_directory.name) / "runtime"
        try:
            shutil.copytree(persist_path, runtime_path, copy_function=shutil.copy2)
            runtime_snapshot = _snapshot_chroma_tree(runtime_path)
            if _tree_content_signature(runtime_snapshot) != _tree_content_signature(
                source_snapshot
            ):
                raise GenerationReaderContractError(
                    "Chroma runtime clone does not match its immutable source snapshot"
                )
            if _snapshot_chroma_tree(persist_path) != source_snapshot:
                raise GenerationReaderContractError(
                    "Chroma source generation changed while creating the runtime clone"
                )
            chromadb = importlib.import_module("chromadb")
            client = chromadb.PersistentClient(path=str(runtime_path))
            if _snapshot_chroma_tree(persist_path) != source_snapshot:
                raise GenerationReaderContractError(
                    "Chroma source generation changed while opening the runtime clone"
                )
        except Exception:
            runtime_directory.cleanup()
            raise

        clone = _RuntimeClone(
            cache_key=cache_key,
            source_snapshot=source_snapshot,
            runtime_directory=runtime_directory,
            runtime_path=runtime_path,
            client=client,
            reader_count=1,
        )
        _RUNTIME_CLONES[cache_key] = clone
        _ALL_RUNTIME_CLONES[str(runtime_path)] = clone
        return clone


def _release_runtime_clone(clone: _RuntimeClone) -> None:
    with _RUNTIME_CLONE_LOCK:
        if clone.reader_count > 0:
            clone.reader_count -= 1
        if clone.retired and clone.reader_count == 0:
            _cleanup_runtime_clone_locked(clone, warn_only=True)


def _cleanup_runtime_clone_locked(
    clone: _RuntimeClone,
    *,
    warn_only: bool = False,
) -> bool:
    try:
        clone.client.close()
        clone.runtime_directory.cleanup()
    except Exception as error:
        if warn_only:
            warnings.warn(
                f"Could not reclaim Chroma runtime clone {clone.runtime_path}: {error}",
                RuntimeWarning,
                stacklevel=2,
            )
        return False
    _ALL_RUNTIME_CLONES.pop(str(clone.runtime_path), None)
    if _RUNTIME_CLONES.get(clone.cache_key) is clone:
        _RUNTIME_CLONES.pop(clone.cache_key, None)
    return True


def _snapshot_chroma_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    try:
        paths = sorted(root.rglob("*"))
    except OSError as error:
        raise GenerationReaderContractError("could not inventory Chroma source tree") from error
    for path in paths:
        if path.is_symlink():
            raise GenerationReaderContractError("Chroma source tree must not contain symlinks")
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as error:
            raise GenerationReaderContractError("could not snapshot Chroma source tree") from error
        snapshot[path.relative_to(root).as_posix()] = (
            stat.st_mtime_ns,
            stat.st_size,
            digest.hexdigest(),
        )
    if "chroma.sqlite3" not in snapshot:
        raise GenerationReaderContractError(
            "Chroma source persist directory is missing chroma.sqlite3 for read-only serving"
        )
    return snapshot


def _tree_content_signature(
    snapshot: Mapping[str, tuple[int, int, str]],
) -> dict[str, tuple[int, str]]:
    return {
        relative_path: (properties[1], properties[2])
        for relative_path, properties in snapshot.items()
    }


def _default_embedder(artifact: str) -> Any:
    sentence_transformers = importlib.import_module("sentence_transformers")
    return sentence_transformers.SentenceTransformer(artifact, local_files_only=True)
