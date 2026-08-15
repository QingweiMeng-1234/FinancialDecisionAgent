import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector import ChromaVectorStore, SQLiteNewsStore
from event_collector.event_collection import RawEventInput
from event_collector.news_ingestion import ingest_raw_inputs


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


def test_ingest_raw_inputs_fetches_exact_content_and_indexes_chunks(temp_storage_dir):
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

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Exact publisher article. Exact article content.",
                title="Exact publisher article",
                description="Exact article content",
                url="https://example.com/article",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=fetcher,
    )

    record = storage.list_article_records()[0]
    assert result.accepted_inputs == 1
    assert result.rejected_inputs == 0
    assert result.stats["saved"] == 1
    assert result.stats["indexed"] == 1
    assert result.items[0].created is True
    assert result.items[0].canonical_url == "https://example.com/article"
    assert record.article.content.startswith("Exact article content")
    assert record.content_status == "ready"
    assert record.summary_status == "ready"
    assert record.index_status == "ready"
    assert vector_store.collection.count() > 1


def test_ingest_raw_inputs_reuses_article_id_and_rebuilds_chunks(temp_storage_dir):
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

    first_result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Exact content version A",
                title="Exact content version",
                description="First publisher version",
                url="https://example.com/article?utm_source=a",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=FakeSummarizer(),
        content_fetcher=first_fetcher,
    )
    first_record = storage.list_article_records()[0]
    first_chunk_count = vector_store.collection.count()

    second_result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Exact content version B",
                title="Exact content version",
                description="Updated publisher version",
                url="https://example.com/article?utm_source=b",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=FakeSummarizer(),
        content_fetcher=second_fetcher,
    )
    second_record = storage.list_article_records()[0]

    assert first_result.items[0].created is True
    assert second_result.stats["saved"] == 0
    assert second_result.stats["updated"] == 1
    assert second_result.items[0].created is False
    assert first_record.id == second_record.id
    assert "Updated version" in second_record.article.content
    assert vector_store.collection.count() > 0
    assert vector_store.collection.count() != first_chunk_count


def test_ingest_raw_inputs_rejects_invalid_inputs_with_normalized_reasons(temp_storage_dir):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()

    result = ingest_raw_inputs(
        [
            RawEventInput(source="manual", raw_text=""),
            RawEventInput(source="invalid", raw_text="This looks long enough to pass length validation."),
            RawEventInput(source="news", raw_text="Short"),
            RawEventInput(
                source="api",
                raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
            ),
        ],
        storage,
        None,
        summarizer=FakeSummarizer(),
    )

    assert result.total_inputs == 4
    assert result.accepted_inputs == 1
    assert result.rejected_inputs == 3
    assert [item.failure_reason for item in result.items[:3]] == [
        "missing_text",
        "invalid_source",
        "short_news_text_without_url",
    ]
    assert all(item.article_id is None for item in result.items[:3])
    assert result.items[3].article_id is not None
    assert result.stats["rejected"] == 3


def test_ingest_raw_inputs_marks_content_fetch_failure_and_keeps_reference(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)
    fetcher = FakeFetcher(error=RuntimeError("fetch failed"))

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Headline",
                title="Headline",
                description="Description",
                url="https://example.com/article",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=FakeSummarizer(),
        content_fetcher=fetcher,
    )
    record = storage.list_article_records()[0]

    assert result.stats["saved"] == 1
    assert result.stats["content_failed"] == 1
    assert result.items[0].failure_reason == "content_fetch_failed"
    assert record.content_status == "failed"
    assert record.article.content == ""
    assert vector_store.collection.count() == 0


def test_ingest_raw_inputs_marks_summary_failure_and_keeps_canonical_content(temp_storage_dir):
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

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Exact publisher article",
                title="Exact publisher article",
                description="Exact article content",
                url="https://example.com/article",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=FakeSummarizer(error=RuntimeError("summary failed")),
        content_fetcher=fetcher,
    )
    record = storage.list_article_records()[0]

    assert result.stats["summary_failed"] == 1
    assert result.items[0].failure_reason == "summary_failed"
    assert record.article.content.startswith("Exact article content")
    assert record.summary_status == "failed"


def test_ingest_raw_inputs_uses_raw_text_for_non_news_inputs(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10)

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="api",
                raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
                published_at=datetime.now(),
            )
        ],
        storage,
        vector_store,
        summarizer=FakeSummarizer(),
    )
    record = storage.list_article_records()[0]

    assert result.stats["saved"] == 1
    assert result.items[0].final_url.startswith("internal://api/")
    assert record.article.content.startswith("Treasury yields spiked")
    assert record.content_status == "ready"
