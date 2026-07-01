import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector import (
    ChromaVectorStore,
    Event,
    EventBatch,
    EventSource,
    SQLiteNewsStore,
    ingest_events_to_storage,
)


class FakeSummarizer:
    def __init__(self, summaries=None, error=None):
        self.summaries = summaries or []
        self.error = error
        self.calls = []

    def summarize_article(self, article):
        self.calls.append(article)
        if self.error:
            raise self.error
        if self.summaries:
            return self.summaries.pop(0)
        return "- Default summary bullet 1\n- Default summary bullet 2\n- Default summary bullet 3"


class FakeFetcher:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.responses[url]


class FetchResult:
    def __init__(self, original_url, canonical_url, content):
        self.original_url = original_url
        self.canonical_url = canonical_url
        self.content = content


@pytest.fixture
def temp_storage_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        chroma_dir = os.path.join(tmpdir, "chroma")
        os.makedirs(chroma_dir, exist_ok=True)
        yield db_path, chroma_dir


def test_ingest_news_fetches_exact_content_and_indexes_chunks(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)
    summarizer = FakeSummarizer(["- Exact content captured.\n- Summary generated.\n- Chunks indexed."])
    fetcher = FakeFetcher(
        {
            "https://example.com/article": FetchResult(
                "https://example.com/article",
                "https://example.com/article",
                "Exact article content from publisher page. " * 5,
            )
        }
    )

    batch = EventBatch(
        events=[
            Event(
                id="news-1",
                source=EventSource.NEWS,
                raw_text="Headline. Short description.",
                timestamp=datetime.now(),
                title="Headline",
                description="Short description",
                url="https://example.com/article",
            )
        ],
        batch_id="batch-1",
        created_at=datetime.now(),
    )

    stats = ingest_events_to_storage(
        batch,
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=fetcher,
    )

    record = storage.list_article_records()[0]
    assert stats["saved"] == 1
    assert stats["updated"] == 0
    assert stats["indexed"] == 1
    assert record.article.content.startswith("Exact article content")
    assert record.content_status == "ready"
    assert record.summary_status == "ready"
    assert record.index_status == "ready"
    assert vector_store.collection.count() > 1


def test_ingest_existing_article_reuses_article_id_and_rebuilds_chunks(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=50, chunk_overlap=10)
    first_fetcher = FakeFetcher(
        {
            "https://example.com/article?utm_source=a": FetchResult(
                "https://example.com/article?utm_source=a",
                "https://example.com/article",
                "First version of exact content. " * 4,
            )
        }
    )
    second_fetcher = FakeFetcher(
        {
            "https://example.com/article?utm_source=b": FetchResult(
                "https://example.com/article?utm_source=b",
                "https://example.com/article",
                "Updated version of exact content with more detail. " * 4,
            )
        }
    )

    first_batch = EventBatch(
        events=[
            Event(
                id="news-1",
                source=EventSource.NEWS,
                raw_text="Headline A",
                timestamp=datetime.now(),
                title="Headline",
                description="Description",
                url="https://example.com/article?utm_source=a",
            )
        ],
        batch_id="batch-a",
        created_at=datetime.now(),
    )
    second_batch = EventBatch(
        events=[
            Event(
                id="news-2",
                source=EventSource.NEWS,
                raw_text="Headline B",
                timestamp=datetime.now(),
                title="Headline",
                description="Description",
                url="https://example.com/article?utm_source=b",
            )
        ],
        batch_id="batch-b",
        created_at=datetime.now(),
    )

    ingest_events_to_storage(first_batch, storage, vector_store, summarizer=FakeSummarizer(), content_fetcher=first_fetcher)
    first_record = storage.list_article_records()[0]
    first_chunk_count = vector_store.collection.count()

    stats = ingest_events_to_storage(second_batch, storage, vector_store, summarizer=FakeSummarizer(), content_fetcher=second_fetcher)
    second_record = storage.list_article_records()[0]

    assert stats["saved"] == 0
    assert stats["updated"] == 1
    assert first_record.id == second_record.id
    assert "Updated version" in second_record.article.content
    assert vector_store.collection.count() > 0
    assert vector_store.collection.count() != first_chunk_count


def test_ingest_fetch_failure_keeps_reference_and_marks_content_failed(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)
    fetcher = FakeFetcher(error=RuntimeError("fetch failed"))

    batch = EventBatch(
        events=[
            Event(
                id="news-1",
                source=EventSource.NEWS,
                raw_text="Headline",
                timestamp=datetime.now(),
                title="Headline",
                description="Description",
                url="https://example.com/article",
            )
        ],
        batch_id="batch-1",
        created_at=datetime.now(),
    )

    stats = ingest_events_to_storage(batch, storage, vector_store, summarizer=FakeSummarizer(), content_fetcher=fetcher)
    record = storage.list_article_records()[0]

    assert stats["saved"] == 1
    assert stats["content_failed"] == 1
    assert record.content_status == "failed"
    assert record.article.content == ""
    assert vector_store.collection.count() == 0


def test_ingest_summary_failure_keeps_canonical_content(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)
    fetcher = FakeFetcher(
        {
            "https://example.com/article": FetchResult(
                "https://example.com/article",
                "https://example.com/article",
                "Exact article content from publisher page. " * 3,
            )
        }
    )

    batch = EventBatch(
        events=[
            Event(
                id="news-1",
                source=EventSource.NEWS,
                raw_text="Headline",
                timestamp=datetime.now(),
                title="Headline",
                description="Description",
                url="https://example.com/article",
            )
        ],
        batch_id="batch-1",
        created_at=datetime.now(),
    )

    stats = ingest_events_to_storage(
        batch,
        storage,
        vector_store,
        summarizer=FakeSummarizer(error=RuntimeError("summary failed")),
        content_fetcher=fetcher,
    )
    record = storage.list_article_records()[0]

    assert stats["summary_failed"] == 1
    assert record.article.content.startswith("Exact article content")
    assert record.summary_status == "failed"


def test_ingest_non_news_event_uses_raw_text_as_canonical_content(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)

    batch = EventBatch(
        events=[
            Event(
                id="api-1",
                source=EventSource.API,
                raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
                timestamp=datetime.now(),
            )
        ],
        batch_id="batch-api",
        created_at=datetime.now(),
    )

    stats = ingest_events_to_storage(batch, storage, vector_store, summarizer=FakeSummarizer())
    record = storage.list_article_records()[0]

    assert stats["saved"] == 1
    assert record.article.content.startswith("Treasury yields spiked")
    assert record.content_status == "ready"
