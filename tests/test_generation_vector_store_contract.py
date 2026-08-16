"""Dependency-independent contract tests for immutable Chroma generations.

The production vector adapter imports Chroma, sentence-transformers and
LangChain at module import time.  These tests deliberately replace those
adapters with small in-memory fakes so the generation safety contract remains
testable on a migration host that has none of those optional dependencies.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PIPELINE_PATH = ROOT / "src" / "event_collector" / "document_pipeline.py"
VECTOR_STORE_PATH = ROOT / "src" / "event_collector" / "vector_store.py"


class FakeDocument:
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class FakeSplitter:
    def __init__(self, *, chunk_size: int, chunk_overlap: int, **_kwargs):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_documents(self, documents):
        result = []
        for document in documents:
            start = 0
            while start < len(document.page_content):
                end = min(len(document.page_content), start + self.chunk_size)
                result.append(FakeDocument(document.page_content[start:end], dict(document.metadata)))
                if end == len(document.page_content):
                    break
                start = end - self.chunk_overlap
        return result


@dataclass
class FakeArticle:
    source: str = "news"
    title: str = "Generation-safe article"
    description: str = ""
    content: str = ""
    url: str = "https://example.test/article"
    published_at: datetime = datetime(2026, 8, 15)
    original_url: str | None = None
    canonical_url: str | None = None
    content_sha256: str | None = None
    active_content_sha256: str | None = None
    content_status: str = "ready"
    content_validation_status: str = "verified"
    summary: str | None = None
    story_group_id: int | None = None
    publisher_source_id: str | None = None
    publisher_source_name: str | None = None


class FakeEncoded(list):
    def tolist(self):
        return list(self)


class FakeEmbedder:
    def encode(self, values):
        if isinstance(values, str):
            return FakeEncoded([1.0, 0.0])
        return FakeEncoded([[float(index + 1), 0.0] for index, _value in enumerate(values)])


class FakeCollection:
    def __init__(self, name, metadata):
        self.name = name
        self.metadata = metadata
        self.upsert_calls = []
        self.add_calls = []
        self.delete_calls = []
        self._ids = set()

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)

    def add(self, **kwargs):
        duplicate_ids = self._ids.intersection(kwargs["ids"])
        if duplicate_ids:
            raise RuntimeError(f"duplicate ids: {sorted(duplicate_ids)}")
        self._ids.update(kwargs["ids"])
        self.add_calls.append(kwargs)

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class FakeClient:
    def __init__(self):
        self.collections = {}
        self.create_calls = []
        self.get_or_create_calls = []

    def create_collection(self, *, name, metadata=None):
        self.create_calls.append((name, metadata))
        if name in self.collections:
            raise RuntimeError(f"collection already exists: {name}")
        collection = FakeCollection(name, metadata)
        self.collections[name] = collection
        return collection

    def get_or_create_collection(self, *, name, metadata=None):
        self.get_or_create_calls.append((name, metadata))
        return self.collections.setdefault(name, FakeCollection(name, metadata))


def _load_vector_store_modules():
    """Load the two production files with only narrow, explicit dependency fakes."""
    preserved = {name: sys.modules.get(name) for name in (
        "event_collector", "event_collector.news_storage", "event_collector.document_pipeline",
        "event_collector.generation_vector_store_under_test", "chromadb", "sentence_transformers",
        "langchain_core", "langchain_core.documents", "langchain_text_splitters",
    )}
    package = types.ModuleType("event_collector")
    package.__path__ = []
    news_storage = types.ModuleType("event_collector.news_storage")
    news_storage.NewsArticle = FakeArticle
    news_storage.compute_content_sha256 = lambda content: hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    chromadb = types.ModuleType("chromadb")
    chromadb.PersistentClient = lambda path: (_ for _ in ()).throw(AssertionError("client injection was required"))
    sentence_transformers = types.ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = lambda *_args, **_kwargs: FakeEmbedder()
    langchain_core = types.ModuleType("langchain_core")
    langchain_documents = types.ModuleType("langchain_core.documents")
    langchain_documents.Document = FakeDocument
    langchain_splitters = types.ModuleType("langchain_text_splitters")
    langchain_splitters.RecursiveCharacterTextSplitter = FakeSplitter
    sys.modules.update({
        "event_collector": package,
        "event_collector.news_storage": news_storage,
        "chromadb": chromadb,
        "sentence_transformers": sentence_transformers,
        "langchain_core": langchain_core,
        "langchain_core.documents": langchain_documents,
        "langchain_text_splitters": langchain_splitters,
    })
    try:
        pipeline_spec = importlib.util.spec_from_file_location("event_collector.document_pipeline", PIPELINE_PATH)
        assert pipeline_spec and pipeline_spec.loader
        pipeline = importlib.util.module_from_spec(pipeline_spec)
        sys.modules[pipeline_spec.name] = pipeline
        pipeline_spec.loader.exec_module(pipeline)

        store_spec = importlib.util.spec_from_file_location(
            "event_collector.generation_vector_store_under_test", VECTOR_STORE_PATH
        )
        assert store_spec and store_spec.loader
        store = importlib.util.module_from_spec(store_spec)
        sys.modules[store_spec.name] = store
        store_spec.loader.exec_module(store)
        return store
    finally:
        for name, module in preserved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _article(content: str) -> FakeArticle:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return FakeArticle(
        content=content,
        content_sha256="obsolete-hash",
        active_content_sha256=digest,
        original_url="https://example.test/original",
        canonical_url="https://example.test/canonical",
    )


def test_generation_store_creates_new_collection_with_full_immutable_identity_metadata():
    vector_store = _load_vector_store_modules()
    client = FakeClient()

    store = vector_store.ChromaVectorStore(
        client=client,
        embedder=FakeEmbedder(),
        collection_name="news_articles_gen_20260815",
        generation_id="gen-20260815",
        corpus_snapshot_id="snapshot-20260815",
        index_config_fingerprint="config-fingerprint",
        model_name="deterministic-model@1",
        chunk_size=20,
        chunk_overlap=5,
    )

    assert client.get_or_create_calls == []
    assert client.create_calls == [
        ("news_articles_gen_20260815", {
            "hnsw:space": "cosine",
            "generation_id": "gen-20260815",
            "corpus_snapshot_id": "snapshot-20260815",
            "embedding_model": "deterministic-model@1",
            "chunk_size": 20,
            "chunk_overlap": 5,
            "index_config_fingerprint": "config-fingerprint",
        })
    ]
    assert store.generation_id == "gen-20260815"


def test_generation_add_binds_active_hash_and_never_deletes_before_upsert():
    vector_store = _load_vector_store_modules()
    client = FakeClient()
    store = vector_store.ChromaVectorStore(
        client=client,
        embedder=FakeEmbedder(),
        collection_name="news_articles_gen_20260815",
        generation_id="gen-20260815",
        corpus_snapshot_id="snapshot-20260815",
        index_config_fingerprint="config-fingerprint",
        model_name="deterministic-model@1",
        chunk_size=20,
        chunk_overlap=5,
    )

    record_ids = store.add_article(17, _article("generation content " * 4))
    write = store.collection.add_calls[0]

    assert record_ids == [
        "gen-20260815:17:0",
        "gen-20260815:17:1",
        "gen-20260815:17:2",
        "gen-20260815:17:3",
        "gen-20260815:17:4",
    ]
    assert store.collection.delete_calls == []
    assert store.collection.upsert_calls == []
    for metadata in write["metadatas"]:
        assert metadata["content_sha256"] == metadata["indexed_content_sha256"]
        assert metadata["indexed_content_sha256"] == hashlib.sha256(("generation content " * 4).encode("utf-8")).hexdigest()
        assert metadata["generation_id"] == "gen-20260815"
        assert metadata["corpus_snapshot_id"] == "snapshot-20260815"
        assert metadata["index_config_fingerprint"] == "config-fingerprint"
        assert metadata["embedding_model"] == "deterministic-model@1"
        assert metadata["chunk_size"] == 20
        assert metadata["chunk_overlap"] == 5


def test_generation_collection_rejects_an_in_place_rewrite_of_the_same_article():
    vector_store = _load_vector_store_modules()
    store = vector_store.ChromaVectorStore(
        client=FakeClient(),
        embedder=FakeEmbedder(),
        collection_name="news_articles_gen_immutable",
        generation_id="gen-immutable",
        corpus_snapshot_id="snapshot-immutable",
        index_config_fingerprint="config-immutable",
        chunk_size=20,
        chunk_overlap=5,
    )
    article = _article("immutable generation content")

    store.add_article(9, article)
    with pytest.raises(RuntimeError, match="duplicate ids"):
        store.add_article(9, article)

    assert len(store.collection.add_calls) == 1
    assert store.collection.upsert_calls == []


def test_generation_mode_rejects_legacy_collection_and_an_existing_generation_collection():
    vector_store = _load_vector_store_modules()
    client = FakeClient()

    with pytest.raises(ValueError, match="collection"):
        vector_store.ChromaVectorStore(
            client=client,
            embedder=FakeEmbedder(),
            generation_id="gen-unsafe",
            corpus_snapshot_id="snapshot-unsafe",
            index_config_fingerprint="config-unsafe",
        )
    with pytest.raises(ValueError, match="required together"):
        vector_store.ChromaVectorStore(
            client=client,
            embedder=FakeEmbedder(),
            collection_name="news_articles_gen_invalid",
            generation_id=" ",
            corpus_snapshot_id="snapshot-invalid",
            index_config_fingerprint="config-invalid",
        )

    kwargs = dict(
        client=client,
        embedder=FakeEmbedder(),
        collection_name="news_articles_gen_safe",
        generation_id="gen-safe",
        corpus_snapshot_id="snapshot-safe",
        index_config_fingerprint="config-safe",
    )
    vector_store.ChromaVectorStore(**kwargs)
    with pytest.raises(RuntimeError, match="already exists"):
        vector_store.ChromaVectorStore(**kwargs)
    assert client.get_or_create_calls == []
    assert client.collections["news_articles_gen_safe"].delete_calls == []


def test_legacy_store_keeps_get_or_create_and_old_chunk_metadata_contract():
    vector_store = _load_vector_store_modules()
    client = FakeClient()
    store = vector_store.ChromaVectorStore(client=client, embedder=FakeEmbedder(), chunk_size=20, chunk_overlap=5)

    store.add_article(3, _article("legacy content " * 3))

    assert client.create_calls == []
    assert client.get_or_create_calls[0][0] == "news_articles"
    metadata = store.collection.upsert_calls[0]["metadatas"][0]
    assert "generation_id" not in metadata
    assert "indexed_content_sha256" not in metadata
