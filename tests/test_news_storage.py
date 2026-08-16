import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector.news_storage import (
    NewsArticle,
    SQLiteNewsStore,
    compute_content_sha256,
    normalize_url,
)


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "test_news.db")


@pytest.fixture
def storage(temp_db):
    store = SQLiteNewsStore(db_path=temp_db)
    store.init_db()
    yield store
    store.close()


def test_save_article_persists_content_to_file(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Bitcoin Surges",
            description="Bitcoin rises to new highs",
            content="Full article about bitcoin",
            url="https://example.com/bitcoin?utm_source=test",
            published_at=datetime.now(),
        )
    )

    record = storage.get_article_record(article_id)
    assert record is not None
    assert record.article.content == "Full article about bitcoin"
    assert record.article.content_status == "ready"
    assert os.path.exists(record.article.content_path)
    assert record.article.normalized_url == "https://example.com/bitcoin"


def test_create_or_get_article_reference_dedupes_by_normalized_url(storage):
    first_id, first_created = storage.create_or_get_article_reference(
        source="news",
        title="Market Update",
        description="Description",
        original_url="https://example.com/article?utm_source=a",
        published_at=datetime.now(),
    )
    second_id, second_created = storage.create_or_get_article_reference(
        source="news",
        title="Market Update Duplicate",
        description="Description duplicate",
        original_url="https://example.com/article?utm_source=b",
        published_at=datetime.now(),
    )

    assert first_created is True
    assert second_created is False
    assert first_id == second_id


def test_update_article_content_sets_hash_and_resets_processing_states(storage):
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Needs Content",
        description="Pending",
        original_url="https://example.com/pending",
        published_at=datetime.now(),
    )
    storage.update_article_content(article_id, content="Earlier canonical content.")
    previous = storage.get_article(article_id)
    storage.update_article_summary(
        article_id,
        "- Existing summary",
        content_sha256=previous.active_content_sha256,
    )
    storage.mark_article_index_ready(article_id, previous.active_content_sha256)

    storage.update_article_content(
        article_id,
        content="Canonical full text for the article.",
        canonical_url="https://example.com/canonical",
    )

    article = storage.get_article(article_id)
    assert article.content == "Canonical full text for the article."
    assert article.content_sha256 == compute_content_sha256("Canonical full text for the article.")
    assert article.summary is None
    assert article.summary_status == "pending"
    assert article.index_status == "pending"
    assert article.canonical_url == "https://example.com/canonical"


def test_update_article_summary_marks_summary_ready(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Rates Update",
            description="Rates moved higher",
            content="Treasury yields rose after stronger than expected payrolls data.",
            url="https://example.com/rates",
            published_at=datetime.now(),
        )
    )

    updated = storage.update_article_summary(article_id, "- Yields moved higher.")

    article = storage.get_article(article_id)
    assert updated is True
    assert article.summary == "- Yields moved higher."
    assert article.summary_status == "ready"


def test_update_article_source_metadata_preserves_summary_and_content(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Legacy row",
            description="Only had internal URL before",
            content="Stored content that should remain intact.",
            url="internal://news/legacy-id",
            published_at=datetime.now(),
            summary="- Existing summary",
        )
    )

    updated = storage.update_article_source_metadata(
        article_id,
        original_url="https://example.com/story?utm_source=test",
        canonical_url="https://example.com/story",
        title="Recovered headline",
    )

    article = storage.get_article(article_id)
    assert updated is True
    assert article.content == "Stored content that should remain intact."
    assert article.summary == "- Existing summary"
    assert article.original_url == "https://example.com/story?utm_source=test"
    assert article.canonical_url == "https://example.com/story"
    assert article.normalized_url == "https://example.com/story"
    assert article.title == "Recovered headline"


def test_list_article_records_missing_summary_only_returns_ready_content(storage):
    missing_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Needs Summary",
            description="Pending summary",
            content="An article without a generated summary yet.",
            url="https://example.com/missing-summary",
            published_at=datetime.now(),
        )
    )
    failed_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Failed Content",
        description="No full text",
        original_url="https://example.com/failed",
        published_at=datetime.now(),
    )
    storage.mark_article_processing_status(failed_id, content_status="failed")

    missing = storage.list_article_records_missing_summary()

    assert [record.id for record in missing] == [missing_id]


def test_normalize_url_strips_tracking_and_fragment():
    assert normalize_url("https://Example.com/story/?utm_source=x&id=1#top") == "https://example.com/story?id=1"


def test_watchlist_run_schema_adds_nullable_report_path_column(storage):
    columns = {
        row["name"]
        for row in storage.conn.execute("PRAGMA table_info(watchlist_runs)").fetchall()
    }

    assert "report_path" in columns


def test_legacy_schema_migration_marks_raw_json_rows_unverified_without_guessing_source_date(temp_db):
    connection = sqlite3.connect(temp_db)
    connection.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            content TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            published_at TEXT NOT NULL,
            summary TEXT,
            fetched_at TEXT NOT NULL,
            raw_json TEXT
        );
        INSERT INTO articles VALUES (
            1, 'theme_search', 'Legacy', '', '', 'https://example.com/legacy',
            '2026-08-15T12:00:00+00:00', NULL, '2026-08-15T12:00:00+00:00',
            '{"publishedAt":"2026-08-01T00:00:00Z"}'
        );
        """
    )
    connection.close()

    store = SQLiteNewsStore(db_path=temp_db)
    store.init_db()
    try:
        row = store.conn.execute(
            "SELECT source_published_at, published_at_provenance, raw_json FROM articles WHERE id = 1"
        ).fetchone()
        assert row["source_published_at"] is None
        assert row["published_at_provenance"] == "legacy_unverified"
        assert row["raw_json"] == '{"publishedAt":"2026-08-01T00:00:00Z"}'
    finally:
        store.close()


def test_save_and_fetch_watchlist_report_path(storage):
    result = SimpleNamespace(
        run_id="watch-1",
        run_at=datetime(2026, 6, 5, 1, 0, 0),
        tickers=["MSFT"],
        top_n=1,
        retrieval_top_k=5,
        triage_model="triage",
        reviewer_model="reviewer",
        triage_prompt_version="triage-v1",
        reviewer_prompt_version="reviewer-v1",
        status="completed",
        ranked_items=[
            SimpleNamespace(
                ticker="MSFT",
                rank=1,
            )
        ],
        items=[
            SimpleNamespace(
                ticker="MSFT",
                evidence=[],
                structured_signals=[],
                structuring_attempts=[],
                card=SimpleNamespace(
                    priority=SimpleNamespace(value="High"),
                    confidence=SimpleNamespace(value="High"),
                    why_now="why now",
                    next_action="next",
                    key_evidence=[],
                    counter_evidence=[],
                    missing_questions=[],
                ),
                reviewer_finding=SimpleNamespace(
                    should_flag_human_review=False,
                    evidence_too_generic=False,
                    missing_target_specific_signal=False,
                    reasoning_jump=False,
                    missing_counter_evidence=False,
                    next_action_too_vague=False,
                    summary="ok",
                ),
            )
        ],
    )

    storage.save_watchlist_run(result)

    assert storage.fetch_watchlist_report_path("watch-1") is None
    assert storage.save_watchlist_report_path("watch-1", "reports/watchlist_triage/watch-1.md") is True
    assert storage.fetch_watchlist_report_path("watch-1") == "reports/watchlist_triage/watch-1.md"
