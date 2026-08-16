"""Contract tests for the generation-only Chroma builder adapter.

No test imports or talks to real Chroma or sentence-transformers.  Small fakes
prove the adapter boundary and make accidental live-data operations visible.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "event_collector"
    / "chroma_generation_adapter.py"
)
SPEC = importlib.util.spec_from_file_location(
    "chroma_generation_adapter_under_test", MODULE_PATH
)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class FakeCollection:
    def __init__(self, rows=(), *, counts=None):
        self.rows = list(rows)
        self.counts = list(counts) if counts is not None else None
        self.add_calls = []
        self.get_calls = []
        self.upsert_calls = []
        self.delete_calls = []

    def add(self, **kwargs):
        self.add_calls.append(kwargs)
        self.rows.extend(zip(kwargs["ids"], kwargs["metadatas"]))

    def count(self):
        if self.counts is not None:
            return self.counts.pop(0)
        return len(self.rows)

    def get(self, *, include, limit, offset):
        self.get_calls.append({"include": include, "limit": limit, "offset": offset})
        page = self.rows[offset : offset + limit]
        return {
            "ids": [record_id for record_id, _metadata in page],
            "metadatas": [metadata for _record_id, metadata in page],
        }


class FakeClient:
    def __init__(self, collections=()):
        self.collections = {
            collection.name if hasattr(collection, "name") else str(collection): collection
            for collection in collections
        }
        self.list_calls = 0
        self.create_calls = []
        self.get_or_create_calls = []
        self.delete_calls = []

    def list_collections(self):
        self.list_calls += 1
        return list(self.collections.values())

    def create_collection(self, *, name, metadata):
        self.create_calls.append({"name": name, "metadata": metadata})
        collection = FakeCollection()
        collection.name = name
        self.collections[name] = collection
        return collection


class NamedCollection:
    def __init__(self, name):
        self.name = name


class Chunk:
    def __init__(self, record_id, article_id, chunk_index, *, generation_id="gen-2"):
        self.record_id = record_id
        self.document = f"document-{article_id}-{chunk_index}"
        self.embedding = [float(article_id), float(chunk_index)]
        self.metadata = {
            "generation_id": generation_id,
            "article_id": article_id,
            "indexed_content_sha256": f"hash-{article_id}",
            "chunk_index": chunk_index,
            "index_config_fingerprint": "fp-2",
            "embedding_artifact": "model@2",
        }


def _client_adapter(client=None, *, page_size=2):
    return adapter.ChromaGenerationClientAdapter(
        client=client or FakeClient(),
        generation_id="gen-2",
        corpus_snapshot_id="snapshot-2",
        embedding_artifact="model@2",
        index_config_fingerprint="fp-2",
        chunk_config={"chunk_size": 500, "overlap": 50, "splitter": "recursive-v2"},
        read_page_size=page_size,
    )


def _metadata(article_id, chunk_index, *, generation_id="gen-2"):
    return {
        "generation_id": generation_id,
        "article_id": article_id,
        "indexed_content_sha256": f"hash-{article_id}",
        "chunk_index": chunk_index,
        "index_config_fingerprint": "fp-2",
    }


def test_lists_exact_collection_names_and_creates_only_with_full_generation_metadata():
    client = FakeClient((NamedCollection("news-v1"), NamedCollection("news-v10")))
    factory = _client_adapter(client)

    assert factory.collection_exists("news-v1") is True
    assert factory.collection_exists("news-v2") is False
    collection = factory.create_collection("news-v2")

    assert client.get_or_create_calls == []
    assert client.delete_calls == []
    assert client.create_calls == [{
        "name": "news-v2",
        "metadata": {
            "hnsw:space": "cosine",
            "generation_id": "gen-2",
            "corpus_snapshot_id": "snapshot-2",
            "embedding_artifact": "model@2",
            "index_config_fingerprint": "fp-2",
            "chunk_config_json": '{"chunk_size":500,"overlap":50,"splitter":"recursive-v2"}',
            "chunk_size": 500,
            "overlap": 50,
            "splitter": "recursive-v2",
        },
    }]
    assert isinstance(collection, adapter.ChromaGenerationCollection)


def test_collection_listing_accepts_chroma_versions_that_return_names_as_strings():
    client = FakeClient()
    client.collections = {"news-v1": "news-v1"}

    assert _client_adapter(client).collection_exists("news-v1") is True


def test_add_chunks_uses_add_not_upsert_and_rejects_duplicate_ids_locally():
    raw = FakeCollection()
    collection = adapter.ChromaGenerationCollection(raw, generation_id="gen-2", read_page_size=10)
    chunks = (
        Chunk("gen-2:7:0", 7, 0),
        Chunk("gen-2:7:1", 7, 1),
    )

    collection.add_chunks(chunks)

    assert raw.add_calls == [{
        "ids": ["gen-2:7:0", "gen-2:7:1"],
        "documents": ["document-7-0", "document-7-1"],
        "metadatas": [chunks[0].metadata, chunks[1].metadata],
        "embeddings": [[7.0, 0.0], [7.0, 1.0]],
    }]
    assert raw.upsert_calls == []
    assert raw.delete_calls == []
    with pytest.raises(ValueError, match="duplicate record id"):
        collection.add_chunks((Chunk("gen-2:7:1", 7, 1),))
    assert len(raw.add_calls) == 1


@pytest.mark.parametrize(
    ("chunk", "message"),
    [
        (Chunk("other:7:0", 7, 0), "generation-scoped"),
        (Chunk("gen-2:8:0", 7, 0), "article"),
        (Chunk("gen-2:7:1", 7, 0), "chunk"),
        (Chunk("gen-2:7:0", 7, 0, generation_id="other"), "generation"),
    ],
)
def test_add_chunks_rejects_id_and_metadata_scope_inconsistency(chunk, message):
    raw = FakeCollection()
    collection = adapter.ChromaGenerationCollection(raw, generation_id="gen-2")

    with pytest.raises(ValueError, match=message):
        collection.add_chunks((chunk,))

    assert raw.add_calls == []


def test_readback_uses_count_and_paginates_every_metadata_row():
    rows = [
        (f"gen-2:{article_id}:{chunk_index}", _metadata(article_id, chunk_index))
        for article_id, chunk_index in ((7, 0), (7, 1), (8, 0), (8, 1), (8, 2))
    ]
    raw = FakeCollection(rows)
    collection = adapter.ChromaGenerationCollection(raw, generation_id="gen-2", read_page_size=2)

    observed = collection.read_chunk_metadata()

    assert observed == tuple(metadata for _record_id, metadata in rows)
    assert raw.get_calls == [
        {"include": ["metadatas"], "limit": 2, "offset": 0},
        {"include": ["metadatas"], "limit": 2, "offset": 2},
        {"include": ["metadatas"], "limit": 1, "offset": 4},
    ]


def test_readback_rejects_count_drift():
    raw = FakeCollection(
        [("gen-2:7:0", _metadata(7, 0)), ("gen-2:7:1", _metadata(7, 1))],
        counts=[2, 3],
    )

    with pytest.raises(RuntimeError, match="count drift"):
        adapter.ChromaGenerationCollection(raw, generation_id="gen-2").read_chunk_metadata()


def test_readback_rejects_ids_metadatas_length_mismatch():
    class MismatchedCollection(FakeCollection):
        def get(self, **kwargs):
            return {"ids": ["gen-2:7:0"], "metadatas": []}

    with pytest.raises(ValueError, match="ids/metadatas length mismatch"):
        adapter.ChromaGenerationCollection(
            MismatchedCollection(("unused",)), generation_id="gen-2"
        ).read_chunk_metadata()


def test_readback_rejects_duplicate_ids_across_pages():
    class DuplicatePageCollection(FakeCollection):
        def get(self, *, include, limit, offset):
            if offset == 0:
                return {"ids": ["gen-2:7:0"], "metadatas": [_metadata(7, 0)]}
            return {"ids": ["gen-2:7:0"], "metadatas": [_metadata(7, 0)]}

    raw = DuplicatePageCollection(
        [("gen-2:7:0", _metadata(7, 0)), ("gen-2:7:1", _metadata(7, 1))]
    )
    with pytest.raises(ValueError, match="duplicate id"):
        adapter.ChromaGenerationCollection(
            raw, generation_id="gen-2", read_page_size=1
        ).read_chunk_metadata()


@pytest.mark.parametrize(
    ("record_id", "metadata", "message"),
    [
        ("other:7:0", _metadata(7, 0), "generation"),
        ("gen-2:8:0", _metadata(7, 0), "article"),
        ("gen-2:7:1", _metadata(7, 0), "chunk"),
        ("gen-2:7:0", _metadata(7, 0, generation_id="other"), "generation"),
    ],
)
def test_readback_rejects_id_metadata_generation_article_chunk_inconsistency(
    record_id, metadata, message
):
    raw = FakeCollection([(record_id, metadata)])

    with pytest.raises(ValueError, match=message):
        adapter.ChromaGenerationCollection(raw, generation_id="gen-2").read_chunk_metadata()


def test_default_embedder_loader_is_offline_unless_download_is_explicitly_allowed():
    calls = []

    class FakeModel:
        def encode(self, texts):
            return [[float(len(text))] for text in texts]

    def model_factory(model_name, **kwargs):
        calls.append((model_name, kwargs))
        return FakeModel()

    offline = adapter.create_sentence_transformer_embedder(
        "model@2", model_factory=model_factory
    )
    online = adapter.create_sentence_transformer_embedder(
        "model@2", allow_download=True, model_factory=model_factory
    )

    assert offline(["a", "abcd"]) == [[1.0], [4.0]]
    assert online(["xy"]) == [[2.0]]
    assert calls == [
        ("model@2", {"local_files_only": True}),
        ("model@2", {"local_files_only": False}),
    ]


def test_factory_can_inject_client_and_exposes_builder_ready_objects():
    raw_client = FakeClient()
    model_calls = []

    class FakeModel:
        def encode(self, texts):
            return [[0.25] for _text in texts]

    dependencies = adapter.create_chroma_generation_dependencies(
        persist_dir="never-opened",
        generation_id="gen-2",
        corpus_snapshot_id="snapshot-2",
        embedding_artifact="model@2",
        index_config_fingerprint="fp-2",
        chunk_config={"chunk_size": 500, "overlap": 50},
        client=raw_client,
        model_factory=lambda name, **kwargs: model_calls.append((name, kwargs)) or FakeModel(),
    )

    assert isinstance(dependencies.collection_client, adapter.ChromaGenerationClientAdapter)
    assert dependencies.collection_client.raw_client is raw_client
    assert dependencies.embedder(["one"]) == [[0.25]]
    assert model_calls == [("model@2", {"local_files_only": True})]
