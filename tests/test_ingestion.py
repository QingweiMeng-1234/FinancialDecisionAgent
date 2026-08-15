import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector import Event, EventBatch, EventSource, SQLiteNewsStore, ingest_events_to_storage


class FakeVectorStore:
    pass


class FakeSummarizer:
    def summarize_article(self, article):
        return "- Summary"


def test_ingest_events_to_storage_delegates_to_ingestion_seam(monkeypatch):
    calls = {}

    def fake_ingest_event_batch(events, storage, vector_store=None, **kwargs):
        calls["events"] = events
        calls["storage"] = storage
        calls["vector_store"] = vector_store
        calls["kwargs"] = kwargs
        return type("Result", (), {"stats": {"saved": 1, "updated": 0, "total_events": 1}})()

    monkeypatch.setattr("event_collector.news_ingestion.ingest_event_batch", fake_ingest_event_batch)

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
    storage = SQLiteNewsStore(db_path=":memory:")
    storage.init_db()

    stats = ingest_events_to_storage(batch, storage, FakeVectorStore(), show_progress=True)

    assert calls["events"] == batch.events
    assert calls["storage"] is storage
    assert isinstance(calls["vector_store"], FakeVectorStore)
    assert calls["kwargs"]["show_progress"] is True
    assert stats == {"saved": 1, "updated": 0, "total_events": 1}


def test_ingest_events_to_storage_keeps_stats_shape_for_callers():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
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

        stats = ingest_events_to_storage(batch, storage, None, summarizer=FakeSummarizer())

        assert stats["total_events"] == 1
        assert stats["saved"] == 1
        assert stats["updated"] == 0
        assert stats["content_failed"] == 0
        assert stats["summary_failed"] == 0
        assert stats["index_failed"] == 0
