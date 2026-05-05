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
    stats = ingest_events_to_storage(batch, storage)
    
    assert stats["total_events"] == 2
    assert stats["saved"] == 2
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
    stats = ingest_events_to_storage(batch, storage, vector_store)
    
    assert stats["total_events"] == 1
    assert stats["saved"] == 1
    assert stats["indexed"] == 1
    
    # Verify storage
    articles = storage.list_articles()
    assert len(articles) == 1
    
    # Verify vector search works
    results = vector_store.search("Ethereum DeFi", top_k=1)
    assert len(results) > 0
    assert "Ethereum" in results[0].get("title", "")
    
    storage.close()
    vector_store.client = None  # Cleanup
