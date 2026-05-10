import pytest
import tempfile
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from event_collector import (
    collect_from_all_sources,
    ingest_events_to_storage,
    ManualCollector,
    NewsCollector,
    SQLiteNewsStore,
    ChromaVectorStore,
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


@pytest.fixture
def temp_storage_dir():
    """Create temporary directories for storage and vector store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        chroma_dir = os.path.join(tmpdir, "chroma")
        os.makedirs(chroma_dir, exist_ok=True)
        yield db_path, chroma_dir


def test_ingest_events_to_sqlite_only(temp_storage_dir):
    """Test ingesting events to SQLite storage only."""
    db_path, chroma_dir = temp_storage_dir
    
    # Create storage
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    
    # Simulate a mock collector
    from event_collector import RawEventInput, create_event, create_event_batch
    
    raw_inputs = [
        RawEventInput(
            source="news",
            raw_text="Bitcoin rises to all-time high amid growing institutional adoption."
        ),
        RawEventInput(
            source="api",
            raw_text="Market volatility index increased significantly following FOMC announcement."
        ),
    ]
    
    batch = create_event_batch(
        [create_event(ri) for ri in raw_inputs]
    )
    
    # Ingest to storage
    stats = ingest_events_to_storage(batch, storage, summarizer=FakeSummarizer())
    
    assert stats["total_events"] == 2
    assert stats["saved"] == 2
    assert stats["summarized"] == 2
    assert stats["skipped"] == 0
    
    # Verify storage
    articles = storage.list_articles()
    assert len(articles) == 2
    
    storage.close()


def test_ingest_events_to_sqlite_and_vector_store(temp_storage_dir):
    """Test ingesting events to both SQLite and vector store."""
    db_path, chroma_dir = temp_storage_dir
    
    # Create storage
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    
    # Create vector store
    vector_store = ChromaVectorStore(persist_dir=chroma_dir)
    
    # Simulate a mock collector
    from event_collector import RawEventInput, create_event, create_event_batch
    
    raw_inputs = [
        RawEventInput(
            source="news",
            raw_text="Ethereum smart contracts enable decentralized finance applications."
        ),
    ]
    
    batch = create_event_batch(
        [create_event(ri) for ri in raw_inputs]
    )
    
    # Ingest to both storage and vector store
    stats = ingest_events_to_storage(
        batch,
        storage,
        vector_store,
        summarizer=FakeSummarizer(["- Ethereum enables DeFi.\n- Smart contracts are central.\n- Adoption supports usage."]),
    )
    
    assert stats["total_events"] == 1
    assert stats["saved"] == 1
    assert stats["summarized"] == 1
    assert stats["indexed"] == 1
    
    # Verify storage
    articles = storage.list_articles()
    assert len(articles) == 1
    
    # Verify vector search works
    results = vector_store.search("Ethereum DeFi", top_k=1)
    assert len(results) > 0
    assert "Ethereum" in results[0].get("title", "")
    assert results[0]["summary"].startswith("- Ethereum")
    
    storage.close()
    vector_store.client = None  # Cleanup


def test_ingest_duplicate_article_skips_before_summarization(temp_storage_dir):
    """Test duplicate URLs are skipped before summarization runs."""
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()

    from event_collector import Event, EventBatch, EventSource

    shared_id = "duplicate-event"
    timestamp = datetime.now()
    batch = EventBatch(
        events=[
            Event(id=shared_id, source=EventSource.NEWS, raw_text="A long enough duplicate article body about market stress and rates.", timestamp=timestamp),
            Event(id=shared_id, source=EventSource.NEWS, raw_text="A long enough duplicate article body about market stress and rates.", timestamp=timestamp),
        ],
        batch_id="batch-1",
        created_at=timestamp,
    )
    summarizer = FakeSummarizer()

    stats = ingest_events_to_storage(batch, storage, summarizer=summarizer)

    assert stats["saved"] == 1
    assert stats["summarized"] == 1
    assert stats["skipped"] == 1
    assert len(summarizer.calls) == 1
    storage.close()


def test_ingest_failure_keeps_saved_row_unindexed(temp_storage_dir):
    """Test summarization failure leaves the row saved with no summary and raises."""
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(persist_dir=chroma_dir)

    from event_collector import RawEventInput, create_event, create_event_batch, ArticleSummarizationError

    batch = create_event_batch(
        [
            create_event(
                RawEventInput(
                    source="news",
                    raw_text="A sufficiently long article about inflation cooling and stocks rallying afterward.",
                )
            )
        ]
    )

    with pytest.raises(ArticleSummarizationError):
        ingest_events_to_storage(
            batch,
            storage,
            vector_store,
            summarizer=FakeSummarizer(error=RuntimeError("summary failed")),
        )

    articles = storage.list_article_records()
    assert len(articles) == 1
    assert articles[0].article.summary is None
    assert vector_store.collection.count() == 0

    storage.close()
    vector_store.client = None
