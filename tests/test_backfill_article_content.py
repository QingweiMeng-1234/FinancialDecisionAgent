import csv
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backfill_article_content import (
    ContentBackfillReportRow,
    is_placeholder_content,
    process_record,
    should_backfill_content,
    write_report,
)
from event_collector.article_content import (
    ArticleFetchError,
    FetchFailureReason,
    classify_empty_extraction,
    is_suspected_truncated_preview,
)
from event_collector.news_storage import NewsArticle, SQLiteNewsStore


class FakeFetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch_with_classification(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.result


class FakeFetchResult:
    def __init__(self, original_url, canonical_url, content):
        self.original_url = original_url
        self.canonical_url = canonical_url
        self.content = content


class FakeSummarizer:
    def __init__(self, summary=None, error=None):
        self.summary = summary or "- One\n- Two\n- Three"
        self.error = error
        self.calls = []

    def summarize_article(self, article):
        self.calls.append(article)
        if self.error:
            raise self.error
        return self.summary


class FakeVectorStore:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def add_article(self, article_id, article):
        self.calls.append((article_id, article))
        if self.error:
            raise self.error
        return [f"{article_id}:0"]


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        store.init_db()
        yield store
        store.close()


def save_article(storage, *, url, content, title="Legacy headline", description="Legacy description"):
    return storage.save_article(
        NewsArticle(
            source="news",
            title=title,
            description=description,
            content=content,
            url=url,
            published_at=datetime.now(),
        )
    )


def test_should_backfill_content_identifies_external_url_with_placeholder_content(storage):
    article_id = save_article(
        storage,
        url="https://example.com/story",
        content="Legacy headline Legacy description",
    )
    record = storage.get_article_record(article_id)

    assert record is not None
    assert is_placeholder_content(record.article) is True
    assert should_backfill_content(record) is True


def test_should_backfill_content_skips_internal_url_and_complete_content(storage):
    internal_id = save_article(
        storage,
        url="internal://news/1",
        content="Legacy headline Legacy description",
    )
    complete_id = save_article(
        storage,
        url="https://example.com/complete",
        content="Full content paragraph. " * 30,
    )

    internal_record = storage.get_article_record(internal_id)
    complete_record = storage.get_article_record(complete_id)

    assert should_backfill_content(internal_record) is False
    assert should_backfill_content(complete_record) is False


def test_classify_empty_extraction_covers_paywall_dynamic_and_empty():
    assert classify_empty_extraction("<html>Subscribe to continue reading this premium content</html>") == FetchFailureReason.PAYWALL_SUSPECTED
    assert classify_empty_extraction('<html><div id="__next"></div><div>Loading...</div><script></script>' * 4) == FetchFailureReason.DYNAMIC_PAGE_SUSPECTED
    assert classify_empty_extraction("<html><body><div>No text</div></body></html>") == FetchFailureReason.EXTRACT_EMPTY


def test_is_suspected_truncated_preview_flags_short_ellipsis_teasers():
    assert is_suspected_truncated_preview(
        "Kaspersky warns that passwords hashed with MD5 remain among the worst choices for storing passwords. In..."
    ) is True
    assert is_suspected_truncated_preview("Full article content. " * 30) is False


def test_process_record_marks_fetch_failures_in_report(storage):
    article_id = save_article(storage, url="https://example.com/forbidden", content="")
    record = storage.get_article_record(article_id)
    fetcher = FakeFetcher(
        error=ArticleFetchError(
            FetchFailureReason.HTTP_401_403,
            "HTTP 403 while fetching",
            status_code=403,
            url="https://example.com/forbidden",
        )
    )

    row = process_record(
        storage,
        record,
        fetcher=fetcher,
        summarizer=FakeSummarizer(),
        vector_store=FakeVectorStore(),
        force=False,
    )

    updated = storage.get_article(article_id)
    assert row.status == "content_failed"
    assert row.fetch_failure_reason == "http_401_403"
    assert row.summary_rebuilt == "no"
    assert updated.content_status == "failed"


def test_process_record_classifies_duplicate_url_conflict(storage, monkeypatch):
    article_id = save_article(storage, url="https://example.com/story", content="")
    record = storage.get_article_record(article_id)
    fetcher = FakeFetcher(
        result=FakeFetchResult(
            "https://example.com/other-story",
            "https://example.com/other-story",
            "Publisher content " * 20,
        )
    )

    def raise_integrity(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: articles.url")

    monkeypatch.setattr(storage, "update_article_content", raise_integrity)

    row = process_record(
        storage,
        record,
        fetcher=fetcher,
        summarizer=FakeSummarizer(),
        vector_store=FakeVectorStore(),
        force=False,
    )

    assert row.status == "content_failed"
    assert row.fetch_failure_reason == "duplicate_url_conflict"


def test_process_record_success_rebuilds_content_summary_and_index(storage):
    article_id = save_article(storage, url="https://example.com/story", content="")
    record = storage.get_article_record(article_id)
    fetcher = FakeFetcher(
        result=FakeFetchResult(
            "https://example.com/story?utm_source=test",
            "https://example.com/story",
            "Canonical publisher content. " * 25,
        )
    )
    summarizer = FakeSummarizer("- Summary A\n- Summary B\n- Summary C")
    vector_store = FakeVectorStore()

    row = process_record(
        storage,
        record,
        fetcher=fetcher,
        summarizer=summarizer,
        vector_store=vector_store,
        force=False,
    )

    updated = storage.get_article(article_id)
    assert row.status == "content_backfilled"
    assert row.summary_rebuilt == "yes"
    assert row.index_rebuilt == "yes"
    assert row.content_length_after > row.content_length_before
    assert updated.content.startswith("Canonical publisher content.")
    assert updated.content_status == "ready"
    assert updated.summary_status == "ready"
    assert updated.index_status == "ready"
    assert article_id in storage.list_retrieval_eligible_article_ids()
    assert updated.canonical_url == "https://example.com/story"
    assert len(vector_store.calls) == 1


def test_process_record_keeps_raw_content_when_summary_or_index_fail(storage):
    article_id = save_article(storage, url="https://example.com/story", content="")
    record = storage.get_article_record(article_id)
    fetcher = FakeFetcher(
        result=FakeFetchResult(
            "https://example.com/story",
            "https://example.com/story",
            "Canonical publisher content. " * 25,
        )
    )

    row = process_record(
        storage,
        record,
        fetcher=fetcher,
        summarizer=FakeSummarizer(error=RuntimeError("summary boom")),
        vector_store=FakeVectorStore(error=RuntimeError("index boom")),
        force=False,
    )

    updated = storage.get_article(article_id)
    assert row.status == "content_backfilled"
    assert row.summary_rebuilt == "no"
    assert row.index_rebuilt == "no"
    assert "summary_failed" in row.notes
    assert "index_failed" in row.notes
    assert updated.content.startswith("Canonical publisher content.")
    assert updated.content_status == "ready"
    assert updated.summary_status == "failed"
    assert updated.index_status == "failed"


def test_write_report_outputs_content_backfill_columns():
    with tempfile.TemporaryDirectory() as tmpdir:
        report_path = os.path.join(tmpdir, "content_backfill_report.csv")
        write_report(
            report_path,
            [
                ContentBackfillReportRow(
                    article_id=7,
                    current_url="https://example.com/story",
                    canonical_url="https://example.com/story",
                    status="content_failed",
                    content_length_before=0,
                    content_length_after=0,
                    fetch_failure_reason="extract_empty",
                    summary_rebuilt="no",
                    index_rebuilt="no",
                    notes="Could not extract article content",
                )
            ],
        )

        with open(report_path, "r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    assert rows[0]["status"] == "content_failed"
    assert rows[0]["fetch_failure_reason"] == "extract_empty"
