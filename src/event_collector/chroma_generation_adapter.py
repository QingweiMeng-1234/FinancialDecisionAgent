"""Generation-only Chroma adapter for :mod:`index_generation_builder`.

The adapter deliberately exposes only the builder's append-only surface: list
collection names, create one new collection, add immutable records, and read
all persisted metadata back.  It has no get-or-create, upsert, or delete path.

Optional Chroma and sentence-transformers imports are kept inside factories so
the safety contract remains importable and unit-testable without either
dependency (and without opening a live persistence directory).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Callable, NamedTuple, Protocol


_RESERVED_COLLECTION_METADATA = frozenset(
    {
        "hnsw:space",
        "generation_id",
        "corpus_snapshot_id",
        "embedding_artifact",
        "index_config_fingerprint",
        "chunk_config_json",
    }
)
_CHROMA_METADATA_SCALARS = (str, int, float, bool)


class ChromaCollectionProtocol(Protocol):
    """The narrow raw collection surface used by a generation build."""

    def add(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[Mapping[str, object]],
        embeddings: list[object],
    ) -> object: ...

    def count(self) -> int: ...

    def get(
        self, *, include: list[str], limit: int, offset: int
    ) -> Mapping[str, object]: ...


class ChromaClientProtocol(Protocol):
    """The narrow raw client surface; destructive methods are absent."""

    def list_collections(self) -> Sequence[object]: ...

    def create_collection(
        self, *, name: str, metadata: Mapping[str, object]
    ) -> ChromaCollectionProtocol: ...


class GenerationDependencies(NamedTuple):
    """Objects passed directly to ``index_generation_builder.build_generation``."""

    collection_client: "ChromaGenerationClientAdapter"
    embedder: Callable[[Sequence[str]], list[object]]


class ChromaGenerationClientAdapter:
    """Builder collection client permanently bound to one generation identity."""

    def __init__(
        self,
        *,
        client: ChromaClientProtocol,
        generation_id: str,
        corpus_snapshot_id: str,
        embedding_artifact: str,
        index_config_fingerprint: str,
        chunk_config: Mapping[str, object],
        read_page_size: int = 1000,
    ) -> None:
        self.raw_client = client
        self.generation_id = _required_text(generation_id, "generation_id")
        self.corpus_snapshot_id = _required_text(
            corpus_snapshot_id, "corpus_snapshot_id"
        )
        self.embedding_artifact = _required_text(
            embedding_artifact, "embedding_artifact"
        )
        self.index_config_fingerprint = _required_text(
            index_config_fingerprint, "index_config_fingerprint"
        )
        if not isinstance(chunk_config, Mapping):
            raise TypeError("chunk_config must be a mapping")
        self.chunk_config = dict(chunk_config)
        collisions = _RESERVED_COLLECTION_METADATA.intersection(self.chunk_config)
        if collisions:
            raise ValueError(
                "chunk_config uses reserved collection metadata keys: "
                + ", ".join(sorted(collisions))
            )
        self.read_page_size = _positive_integer(read_page_size, "read_page_size")
        # Fail before any external collection operation if the complete config
        # cannot be recorded deterministically.
        self._chunk_config_json = _canonical_json(self.chunk_config)

    def collection_exists(self, collection_name: str) -> bool:
        """Check exact names across Chroma versions returning strings or objects."""
        name = _required_text(collection_name, "collection_name")
        for listed in self.raw_client.list_collections():
            listed_name = listed if isinstance(listed, str) else getattr(listed, "name", None)
            if not isinstance(listed_name, str):
                raise TypeError("list_collections returned an entry without a text name")
            if listed_name == name:
                return True
        return False

    def create_collection(self, collection_name: str) -> "ChromaGenerationCollection":
        """Create a fresh collection; Chroma owns the exists/race failure."""
        name = _required_text(collection_name, "collection_name")
        metadata: dict[str, object] = {
            "hnsw:space": "cosine",
            "generation_id": self.generation_id,
            "corpus_snapshot_id": self.corpus_snapshot_id,
            "embedding_artifact": self.embedding_artifact,
            "index_config_fingerprint": self.index_config_fingerprint,
            "chunk_config_json": self._chunk_config_json,
        }
        # Scalar values are also exposed under their original names for
        # operator inspection.  The canonical JSON remains the lossless source
        # for nested or null-valued config.
        for key, value in self.chunk_config.items():
            if not isinstance(key, str) or not key:
                raise ValueError("chunk_config keys must be non-empty text")
            if isinstance(value, _CHROMA_METADATA_SCALARS):
                metadata[key] = value
        raw_collection = self.raw_client.create_collection(name=name, metadata=metadata)
        return ChromaGenerationCollection(
            raw_collection,
            generation_id=self.generation_id,
            read_page_size=self.read_page_size,
        )


class ChromaGenerationCollection:
    """Append-only candidate collection with strict persisted read-back checks."""

    def __init__(
        self,
        collection: ChromaCollectionProtocol,
        *,
        generation_id: str,
        read_page_size: int = 1000,
    ) -> None:
        self.raw_collection = collection
        self.generation_id = _required_text(generation_id, "generation_id")
        self.read_page_size = _positive_integer(read_page_size, "read_page_size")
        self._written_ids: set[str] = set()

    def add_chunks(self, chunks: Sequence[object]) -> None:
        """Add one builder batch and reject IDs already seen in this process."""
        if not isinstance(chunks, Sequence) or isinstance(chunks, (str, bytes)):
            raise TypeError("chunks must be a sequence")
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[Mapping[str, object]] = []
        embeddings: list[object] = []
        batch_ids: set[str] = set()

        for chunk in chunks:
            try:
                record_id = chunk.record_id
                document = chunk.document
                metadata = chunk.metadata
                embedding = chunk.embedding
            except AttributeError as error:
                raise TypeError("chunks must follow the CollectionChunk contract") from error
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("chunk record_id must be non-empty text")
            if record_id in batch_ids or record_id in self._written_ids:
                raise ValueError(f"duplicate record id: {record_id}")
            if not isinstance(document, str):
                raise ValueError("chunk document must be text")
            _validate_record_identity(record_id, metadata, self.generation_id)
            batch_ids.add(record_id)
            ids.append(record_id)
            documents.append(document)
            metadatas.append(metadata)
            embeddings.append(embedding)

        if not ids:
            return
        self.raw_collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        # A failed Chroma add must remain retryable by the controlling process.
        self._written_ids.update(batch_ids)

    def read_chunk_metadata(self) -> tuple[Mapping[str, object], ...]:
        """Read exactly one stable, complete metadata snapshot using pagination."""
        expected_count = _collection_count(self.raw_collection.count())
        observed: list[Mapping[str, object]] = []
        seen_ids: set[str] = set()
        offset = 0

        while offset < expected_count:
            limit = min(self.read_page_size, expected_count - offset)
            payload = self.raw_collection.get(
                include=["metadatas"], limit=limit, offset=offset
            )
            if not isinstance(payload, Mapping):
                raise ValueError("collection get must return a mapping")
            ids = payload.get("ids")
            metadatas = payload.get("metadatas")
            if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
                raise ValueError("collection read-back ids must be a sequence")
            if not isinstance(metadatas, Sequence) or isinstance(
                metadatas, (str, bytes)
            ):
                raise ValueError("collection read-back metadatas must be a sequence")
            if len(ids) != len(metadatas):
                raise ValueError("collection read-back ids/metadatas length mismatch")
            if not ids:
                break

            for record_id, metadata in zip(ids, metadatas):
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError("collection read-back id must be non-empty text")
                if record_id in seen_ids:
                    raise ValueError(f"collection read-back duplicate id: {record_id}")
                _validate_record_identity(record_id, metadata, self.generation_id)
                seen_ids.add(record_id)
                observed.append(metadata)
            offset += len(ids)

        final_count = _collection_count(self.raw_collection.count())
        if final_count != expected_count:
            raise RuntimeError(
                "collection count drift during read-back: "
                f"started at {expected_count}, ended at {final_count}"
            )
        if len(observed) != expected_count:
            raise ValueError(
                "collection read-back count mismatch: "
                f"expected {expected_count}, observed {len(observed)}"
            )
        return tuple(observed)


def create_sentence_transformer_embedder(
    embedding_artifact: str,
    *,
    allow_download: bool = False,
    model_factory: Callable[..., object] | None = None,
) -> Callable[[Sequence[str]], list[object]]:
    """Load an embedding model offline by default and return a builder callable."""
    model_name = _required_text(embedding_artifact, "embedding_artifact")
    if not isinstance(allow_download, bool):
        raise TypeError("allow_download must be a boolean")
    if model_factory is None:
        sentence_transformers = importlib.import_module("sentence_transformers")
        model_factory = sentence_transformers.SentenceTransformer
    model = model_factory(model_name, local_files_only=not allow_download)

    def embed(texts: Sequence[str]) -> list[object]:
        if not isinstance(texts, Sequence) or isinstance(texts, (str, bytes)):
            raise TypeError("embedder input must be a sequence of text")
        values = list(texts)
        if any(not isinstance(value, str) for value in values):
            raise TypeError("embedder input must contain only text")
        encoded = model.encode(values)
        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()
        return list(encoded)

    return embed


def create_chroma_generation_dependencies(
    *,
    persist_dir: str | Path,
    generation_id: str,
    corpus_snapshot_id: str,
    embedding_artifact: str,
    index_config_fingerprint: str,
    chunk_config: Mapping[str, object],
    read_page_size: int = 1000,
    allow_download: bool = False,
    client: ChromaClientProtocol | None = None,
    model_factory: Callable[..., object] | None = None,
) -> GenerationDependencies:
    """Create the two objects a root CLI wires into the generation builder."""
    if client is None:
        chromadb = importlib.import_module("chromadb")
        client = chromadb.PersistentClient(path=str(persist_dir))
    collection_client = ChromaGenerationClientAdapter(
        client=client,
        generation_id=generation_id,
        corpus_snapshot_id=corpus_snapshot_id,
        embedding_artifact=embedding_artifact,
        index_config_fingerprint=index_config_fingerprint,
        chunk_config=chunk_config,
        read_page_size=read_page_size,
    )
    embedder = create_sentence_transformer_embedder(
        embedding_artifact,
        allow_download=allow_download,
        model_factory=model_factory,
    )
    return GenerationDependencies(collection_client, embedder)


def _validate_record_identity(
    record_id: str, metadata: object, expected_generation_id: str
) -> None:
    if not isinstance(metadata, Mapping):
        raise ValueError("chunk metadata must be a mapping")
    generation_id = metadata.get("generation_id")
    article_id = metadata.get("article_id")
    chunk_index = metadata.get("chunk_index")
    if generation_id != expected_generation_id:
        raise ValueError("chunk metadata generation is inconsistent with the adapter")
    if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id <= 0:
        raise ValueError("chunk metadata article_id must be a positive integer")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool) or chunk_index < 0:
        raise ValueError("chunk metadata chunk_index must be a non-negative integer")
    prefix = f"{expected_generation_id}:"
    if not record_id.startswith(prefix):
        raise ValueError("chunk id is not generation-scoped to the adapter")
    expected_record_id = f"{expected_generation_id}:{article_id}:{chunk_index}"
    if record_id != expected_record_id:
        suffix = record_id[len(prefix) :]
        parts = suffix.split(":")
        if len(parts) == 2 and parts[0] != str(article_id):
            raise ValueError("chunk id article is inconsistent with metadata")
        if len(parts) == 2 and parts[1] != str(chunk_index):
            raise ValueError("chunk id chunk is inconsistent with metadata")
        raise ValueError("chunk id article/chunk identity is inconsistent with metadata")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _collection_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("collection count must be a non-negative integer")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError("chunk_config must be JSON-serializable") from error


__all__ = [
    "ChromaClientProtocol",
    "ChromaCollectionProtocol",
    "ChromaGenerationClientAdapter",
    "ChromaGenerationCollection",
    "GenerationDependencies",
    "create_chroma_generation_dependencies",
    "create_sentence_transformer_embedder",
]
