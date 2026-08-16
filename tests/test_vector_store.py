import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector.document_pipeline import article_to_document, split_article_document
from event_collector.news_storage import NewsArticle, compute_content_sha256
from event_collector.vector_store import ChromaVectorStore, chunk_text, parse_article_id_from_chunk_id
from tests.deterministic_embedder import DeterministicEmbedder


@pytest.fixture
def temp_chroma_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def vector_store(temp_chroma_dir):
    store = ChromaVectorStore(
        persist_dir=temp_chroma_dir,
        chunk_size=40,
        chunk_overlap=10,
        embedder=DeterministicEmbedder(),
    )
    yield store
    if hasattr(store, "client") and store.client:
        store.client = None


def test_vector_store_uses_injected_embedder_without_loading_model(temp_chroma_dir, monkeypatch):
    injected_embedder = object()

    def fail_if_loaded(*args, **kwargs):
        del args, kwargs
        raise AssertionError("SentenceTransformer should not be loaded")

    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer", fail_if_loaded)

    store = ChromaVectorStore(persist_dir=temp_chroma_dir, embedder=injected_embedder)

    assert store.embedder is injected_embedder


def verified_news_article(**kwargs):
    article = NewsArticle(**kwargs)
    article.content_status = "ready"
    article.content_validation_status = "verified"
    article.content_sha256 = compute_content_sha256(article.content)
    return article


def test_chunk_text_uses_fixed_windows_with_overlap():
    chunks = chunk_text("A" * 95, chunk_size=40, chunk_overlap=10)

    assert len(chunks) == 3
    assert len(chunks[0]) == 40
    assert chunks[0][-10:] == chunks[1][:10]


def test_article_to_document_standardizes_content_and_metadata():
    article = verified_news_article(
        source="news",
        title="Bitcoin Rises",
        description="Bitcoin price increase",
        content=" Bitcoin has risen   by 10% today. ",
        url="https://example.com/bitcoin",
        original_url="https://example.com/bitcoin",
        canonical_url="https://example.com/bitcoin",
        published_at=datetime.now(),
        content_sha256="sha-1",
        summary="- Supply concerns pushed crude prices higher.",
    )

    document = article_to_document(7, article)

    assert document.page_content == "Bitcoin has risen by 10% today."
    assert document.metadata["article_id"] == 7
    assert document.metadata["title"] == "Bitcoin Rises"
    assert document.metadata["summary"] == "- Supply concerns pushed crude prices higher."


def test_split_article_document_preserves_article_metadata_and_chunk_indexes():
    article = verified_news_article(
        source="news",
        title="Bitcoin Rises",
        description="Bitcoin price increase",
        content="A" * 95,
        url="https://example.com/bitcoin",
        original_url="https://example.com/bitcoin",
        canonical_url="https://example.com/bitcoin",
        published_at=datetime.now(),
        content_sha256="sha-1",
    )

    chunks = split_article_document(5, article, chunk_size=40, chunk_overlap=10)

    assert len(chunks) == 3
    assert chunks[0].metadata["article_id"] == 5
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[1].metadata["chunk_index"] == 1
    assert chunks[0].metadata["title"] == "Bitcoin Rises"
    assert chunks[0].page_content[-10:] == chunks[1].page_content[:10]


def test_vector_store_add_article_creates_chunk_records(vector_store):
    article = verified_news_article(
        source="news",
        title="Bitcoin Rises",
        description="Bitcoin price increase",
        content="Bitcoin has risen by 10% today due to positive market sentiment. " * 4,
        url="https://example.com/bitcoin",
        published_at=datetime.now(),
        canonical_url="https://example.com/bitcoin",
        original_url="https://example.com/bitcoin",
        content_sha256="sha-1",
    )

    record_ids = vector_store.add_article(1, article)

    assert len(record_ids) > 1
    assert record_ids[0] == "1:0"


def test_vector_store_search_aggregates_chunks_to_articles(vector_store):
    articles = [
        verified_news_article(
            source="news",
            title="Bitcoin Market Analysis",
            description="BTC analysis",
            content="Bitcoin is a decentralized digital currency. Recent analysis shows bullish trends. " * 3,
            url="https://example.com/btc1",
            original_url="https://example.com/btc1",
            canonical_url="https://example.com/btc1",
            published_at=datetime.now(),
            content_sha256="btc-sha",
        ),
        verified_news_article(
            source="news",
            title="Stock Market Update",
            description="Stock update",
            content="The stock market closed up today with major indices gaining. " * 2,
            url="https://example.com/stock1",
            original_url="https://example.com/stock1",
            canonical_url="https://example.com/stock1",
            published_at=datetime.now(),
            content_sha256="stock-sha",
        ),
    ]

    for index, article in enumerate(articles, start=1):
        vector_store.add_article(index, article)

    results = vector_store.search("Bitcoin cryptocurrency", top_k=1)

    assert len(results) == 1
    assert results[0]["article_id"] == 1
    assert results[0]["title"] == "Bitcoin Market Analysis"
    assert len(results[0]["matched_chunks"]) <= 2


def test_vector_store_reindex_replaces_existing_chunks(vector_store):
    original = verified_news_article(
        source="news",
        title="Oil Rises",
        description="Energy markets move",
        content="Oil prices rose after supply concerns increased. " * 3,
        url="https://example.com/oil",
        original_url="https://example.com/oil",
        canonical_url="https://example.com/oil",
        published_at=datetime.now(),
        content_sha256="sha-1",
    )
    updated = verified_news_article(
        source="news",
        title="Oil Rises",
        description="Energy markets move",
        content="Tightening inventory expectations moved crude sharply higher. " * 3,
        url="https://example.com/oil",
        original_url="https://example.com/oil",
        canonical_url="https://example.com/oil",
        published_at=original.published_at,
        content_sha256="sha-2",
        summary="- Supply concerns pushed crude prices higher.",
    )

    original_ids = vector_store.add_article(9, original)
    updated_ids = vector_store.add_article(9, updated)
    results = vector_store.search("inventory expectations", top_k=1)

    assert vector_store.collection.count() == len(updated_ids)
    assert all(chunk_id.startswith("9:") for chunk_id in updated_ids)
    assert results[0]["article_id"] == 9
    assert results[0]["summary"] == updated.summary


def test_legacy_reindex_embedding_failure_does_not_delete_existing_chunks(temp_chroma_dir):
    """SELECT INVARIANT: prepare embeddings before the destructive legacy replacement step."""
    class FailingEmbedder:
        def encode(self, values):
            raise RuntimeError("embedding unavailable")

    class RecordingCollection:
        def __init__(self):
            self.delete_calls = []

        def delete(self, **kwargs):
            self.delete_calls.append(kwargs)

        def get(self, include=None):
            return {"ids": ["9:0"], "metadatas": [{"article_id": 9}]}

    class FakeClient:
        def __init__(self):
            self.collection = RecordingCollection()

        def get_or_create_collection(self, **kwargs):
            return self.collection

    client = FakeClient()
    store = ChromaVectorStore(
        persist_dir=temp_chroma_dir,
        embedder=FailingEmbedder(),
        client=client,
    )
    article = verified_news_article(
        source="news",
        title="Embedding fails",
        description="Existing article",
        content="Canonical content remains available. " * 4,
        url="https://example.com/failure",
        original_url="https://example.com/failure",
        canonical_url="https://example.com/failure",
        published_at=datetime.now(),
    )

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        store.add_article(9, article)

    assert client.collection.delete_calls == []


def test_vector_store_search_respects_allowed_article_ids(vector_store):
    first = verified_news_article(
        source="news",
        title="Apple launches service",
        description="Apple services launch",
        content="Apple launched a new consumer service with recurring revenue implications. " * 3,
        url="https://example.com/apple",
        original_url="https://example.com/apple",
        canonical_url="https://example.com/apple",
        published_at=datetime.now(),
        content_sha256="apple-sha",
    )
    second = verified_news_article(
        source="news",
        title="Google launches service",
        description="Google services launch",
        content="Google launched a new cloud service with recurring revenue implications. " * 3,
        url="https://example.com/google",
        original_url="https://example.com/google",
        canonical_url="https://example.com/google",
        published_at=datetime.now(),
        content_sha256="google-sha",
    )

    vector_store.add_article(1, first)
    vector_store.add_article(2, second)

    unrestricted = vector_store.search("consumer service launch", top_k=2)
    filtered = vector_store.search("consumer service launch", top_k=2, allowed_article_ids={2})

    assert {item["article_id"] for item in unrestricted}
    assert [item["article_id"] for item in filtered] == [2]


def test_parse_article_id_from_legacy_chunk_ids():
    assert parse_article_id_from_chunk_id("6:0") == 6
    assert parse_article_id_from_chunk_id("internal://news/f106d0be-7da9-4d56-baa6-fccac6f01089_6") == 6


def test_delete_article_sweeps_legacy_chunk_ids(temp_chroma_dir):
    class FakeCollection:
        def __init__(self):
            self.delete_calls = []

        def delete(self, **kwargs):
            self.delete_calls.append(kwargs)

        def get(self, include=None):
            return {
                "ids": ["legacy://item_103", "104:0"],
                "metadatas": [{}, {"article_id": 104}],
            }

    store = ChromaVectorStore(
        persist_dir=temp_chroma_dir,
        chunk_size=40,
        chunk_overlap=10,
        embedder=DeterministicEmbedder(),
    )
    store.collection = FakeCollection()

    store.delete_article(103)

    assert store.collection.delete_calls[0] == {"where": {"article_id": 103}}
    assert store.collection.delete_calls[1] == {"ids": ["legacy://item_103"]}


def test_vector_store_defaults_to_local_only_sentence_transformer_loading(temp_chroma_dir, monkeypatch):
    captured = {}

    class FakeClient:
        def get_or_create_collection(self, name, metadata=None):
            captured["collection_name"] = name
            captured["collection_metadata"] = metadata
            return object()

    class FakeEmbedder:
        def encode(self, value):
            return value

    def fake_sentence_transformer(model_name, local_files_only=False):
        captured["model_name"] = model_name
        captured["local_files_only"] = local_files_only
        return FakeEmbedder()

    monkeypatch.setattr("event_collector.vector_store.chromadb.PersistentClient", lambda path: FakeClient())
    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer", fake_sentence_transformer)
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_LOCAL_FILES_ONLY", raising=False)

    ChromaVectorStore(persist_dir=temp_chroma_dir)

    assert captured["model_name"] == "all-MiniLM-L6-v2"
    assert captured["local_files_only"] is True


def test_vector_store_allows_env_override_for_remote_model_checks(temp_chroma_dir, monkeypatch):
    captured = {}

    class FakeClient:
        def get_or_create_collection(self, name, metadata=None):
            return object()

    class FakeEmbedder:
        def encode(self, value):
            return value

    def fake_sentence_transformer(model_name, local_files_only=False):
        captured["local_files_only"] = local_files_only
        return FakeEmbedder()

    monkeypatch.setattr("event_collector.vector_store.chromadb.PersistentClient", lambda path: FakeClient())
    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer", fake_sentence_transformer)
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_LOCAL_FILES_ONLY", "false")

    ChromaVectorStore(persist_dir=temp_chroma_dir)

    assert captured["local_files_only"] is False
