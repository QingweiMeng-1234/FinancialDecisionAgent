import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_collector import ChromaVectorStore, SQLiteNewsStore
from event_collector.article_content import ArticleFetchError, FetchFailureReason
from event_collector.event_collection import (
    CollectionSourceOutcome,
    EventSourceCollector,
    RawEventInput,
)
from event_collector.news_ingestion import (
    NewsIngestionRequest,
    ingest_raw_inputs,
    run_news_ingestion,
    validate_raw_input,
)
from event_collector.refresh_lease_watchdog import LeaseHeartbeatFailed
from tests.deterministic_embedder import DeterministicEmbedder


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


class FailingVectorStore:
    def add_article(self, article_id, article):
        raise RuntimeError("index unavailable")


class RecordingAttemptRecorder:
    def __init__(self):
        self.events = []
        self.finish_metadata = []

    def begin_attempt(self, *, article_id, stage, input_content_sha256):
        attempt = {"article_id": article_id, "stage": stage, "input_content_sha256": input_content_sha256}
        self.events.append(("begin", attempt))
        return attempt

    def finish_attempt(
        self,
        attempt,
        *,
        status,
        failure_code=None,
        retryable=False,
        next_retry_at=None,
    ):
        self.events.append(("finish", attempt, status, failure_code))
        self.finish_metadata.append(
            {
                "status": status,
                "failure_code": failure_code,
                "retryable": retryable,
                "next_retry_at": next_retry_at,
            }
        )
        return True


class RejectingSummarySuccessRecorder(RecordingAttemptRecorder):
    def finish_attempt(self, attempt, **kwargs):
        super().finish_attempt(attempt, **kwargs)
        status = kwargs["status"]
        return not (attempt["stage"] == "summary" and status == "succeeded")


class StructuredCollector(EventSourceCollector):
    def __init__(self, outcome):
        self.outcome = outcome

    def collect(self):
        return self.outcome.raw_inputs

    def collect_result(self):
        return self.outcome


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
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
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
                raw_text="Headline. Short description.",
                title="Headline",
                description="Short description",
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


def test_run_news_ingestion_preserves_successful_inputs_and_structured_source_failures(monkeypatch):
    """SELECT INVARIANT: one failed collector cannot erase another collector's inputs or outcome facts."""
    captured = {}
    successful = CollectionSourceOutcome(
        source_name="manual",
        collector_status="succeeded",
        raw_inputs=[RawEventInput(source="manual", raw_text="caller supplied market context")],
        source_row_count=1,
    )
    failed = CollectionSourceOutcome(
        source_name="news",
        collector_status="failed",
        failure_code="http_429",
        retryable=True,
    )

    def fake_ingest(raw_inputs, storage, vector_store, **kwargs):
        captured["raw_inputs"] = raw_inputs
        captured["collection_result"] = kwargs["collection_result"]
        collection_result = kwargs["collection_result"]
        return type(
            "IngestionResult",
            (),
            {
                "collected_events": 1,
                "stats": {},
                "total_articles": 0,
                "items": [],
                "total_inputs": 1,
                "accepted_inputs": 1,
                "rejected_inputs": 0,
                "collector_status": collection_result.collector_status,
                "source_outcomes": collection_result.source_outcomes,
                "source_errors": collection_result.source_errors,
                "source_error_count": len(collection_result.source_errors),
                "empty_reason": collection_result.empty_reason,
            },
        )()

    monkeypatch.setattr("event_collector.news_ingestion.ingest_raw_inputs", fake_ingest)

    result = run_news_ingestion(
        NewsIngestionRequest(show_progress=False),
        storage=type("Storage", (), {"init_db": lambda self: None})(),
        vector_store=object(),
        collectors=[StructuredCollector(successful), StructuredCollector(failed)],
    )

    assert captured["raw_inputs"] == successful.raw_inputs
    assert result.collector_status == "succeeded"
    assert result.source_outcomes == [successful, failed]
    assert result.source_errors == [failed]
    assert result.source_error_count == 1
    assert result.empty_reason is None


def test_run_news_ingestion_does_not_create_or_mutate_legacy_chroma_by_default(monkeypatch):
    """SELECT INVARIANT: canonical ingestion does not default to the mutable legacy index."""
    captured = {}
    successful = CollectionSourceOutcome(
        source_name="manual",
        collector_status="succeeded",
        raw_inputs=[],
        source_row_count=0,
        empty_reason="no_matching_articles",
    )

    def forbidden_legacy_store(*args, **kwargs):
        pytest.fail("default ingestion must not construct ChromaVectorStore")

    def fake_ingest(raw_inputs, storage, vector_store, **kwargs):
        captured["vector_store"] = vector_store
        collection_result = kwargs["collection_result"]
        return type(
            "IngestionResult",
            (),
            {
                "collected_events": 0,
                "stats": {},
                "total_articles": 0,
                "items": [],
                "total_inputs": 0,
                "accepted_inputs": 0,
                "rejected_inputs": 0,
                "collector_status": collection_result.collector_status,
                "source_outcomes": collection_result.source_outcomes,
                "source_errors": collection_result.source_errors,
                "empty_reason": collection_result.empty_reason,
            },
        )()

    monkeypatch.setattr("event_collector.news_ingestion.ChromaVectorStore", forbidden_legacy_store)
    monkeypatch.setattr("event_collector.news_ingestion.ingest_raw_inputs", fake_ingest)

    run_news_ingestion(
        NewsIngestionRequest(show_progress=False),
        storage=type("Storage", (), {"init_db": lambda self: None})(),
        collectors=[StructuredCollector(successful)],
    )

    assert captured["vector_store"] is None


def test_direct_ingest_raw_inputs_is_not_a_verified_empty_collection(temp_storage_dir):
    """SELECT INVARIANT: an empty legacy caller list is not proof that providers returned no articles."""
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()

    result = ingest_raw_inputs([], storage)

    assert result.collector_status == "succeeded"
    assert result.source_outcomes == []
    assert result.source_errors == []
    assert result.source_error_count == 0
    assert result.empty_reason is None


def test_ingest_raw_inputs_reuses_article_id_and_rebuilds_chunks(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=50, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
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
                raw_text="Headline A",
                title="Headline",
                description="Description",
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
                raw_text="Headline B",
                title="Headline",
                description="Description",
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
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
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
    assert result.items[0].retryable is True
    assert record.content_status == "failed"
    assert record.article.content == ""
    assert vector_store.collection.count() == 0


def test_ingest_raw_inputs_records_retryable_fetch_failure_without_retrying_or_opening_a_gate(
    temp_storage_dir,
):
    """A provider-directed retry is durable intent, not an in-process retry."""
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    recorder = RecordingAttemptRecorder()
    retry_at = datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc)
    error = ArticleFetchError(
        FetchFailureReason.HTTP_429,
        "rate limited",
        status_code=429,
        url="https://example.com/article",
        retryable=True,
        retry_after_at=retry_at,
    )

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
        None,
        content_fetcher=FakeFetcher(error=error),
        attempt_recorder=recorder,
    )

    assert result.items[0].content_status == "failed"
    assert result.items[0].failure_reason == "http_429"
    assert result.items[0].retryable is True
    assert result.items[0].next_retry_at == retry_at
    assert [event[2:] for event in recorder.events if event[0] == "finish"] == [
        ("retry_scheduled", "http_429")
    ]
    assert recorder.finish_metadata == [
        {
            "status": "retry_scheduled",
            "failure_code": "http_429",
            "retryable": True,
            "next_retry_at": retry_at,
        }
    ]


def test_ingest_raw_inputs_uses_only_an_injected_policy_to_derive_retry_time(temp_storage_dir):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    recorder = RecordingAttemptRecorder()
    expected_retry_at = datetime(2026, 8, 15, 2, 15, tzinfo=timezone.utc)
    error = ArticleFetchError(
        FetchFailureReason.NETWORK_TIMEOUT,
        "timed out",
        url="https://example.com/article",
        retryable=True,
        retry_after_seconds=15,
    )

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
        None,
        content_fetcher=FakeFetcher(error=error),
        attempt_recorder=recorder,
        retry_schedule_policy=lambda received: expected_retry_at if received is error else None,
    )

    assert result.items[0].content_status == "failed"
    assert result.items[0].retryable is True
    assert result.items[0].next_retry_at == expected_retry_at
    assert recorder.finish_metadata[-1]["status"] == "retry_scheduled"
    assert recorder.finish_metadata[-1]["retryable"] is True
    assert recorder.finish_metadata[-1]["next_retry_at"] == expected_retry_at


@pytest.mark.parametrize(
    "reason",
    [FetchFailureReason.HTTP_401_403, FetchFailureReason.INVALID_OR_PRIVATE_URL],
)
def test_ingest_raw_inputs_leaves_non_retryable_fetch_failure_terminal(temp_storage_dir, reason):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    recorder = RecordingAttemptRecorder()

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
        None,
        content_fetcher=FakeFetcher(
            error=ArticleFetchError(
                reason,
                "permanent fetch exclusion",
                url="https://example.com/article",
                retryable=False,
            )
        ),
        attempt_recorder=recorder,
    )

    assert result.items[0].content_status == "failed"
    assert result.items[0].retryable is False
    assert result.items[0].next_retry_at is None
    assert recorder.finish_metadata[-1] == {
        "status": "failed",
        "failure_code": reason.value,
        "retryable": False,
        "next_retry_at": None,
    }


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://[2001:db8::1",
        "https://example.com:bad/article",
        "https://user:password@example.com/article",
    ],
)
def test_invalid_news_urls_are_rejected_without_aborting_following_input(temp_storage_dir, invalid_url):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    valid = RawEventInput(
        source="api",
        raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
        published_at=datetime.now(),
    )

    assert validate_raw_input(
        RawEventInput(
            source="news",
            raw_text="Headline",
            title="Headline",
            description="Description",
            url=invalid_url,
            published_at=datetime.now(),
        )
    ) == "invalid_news_url"
    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Headline",
                title="Headline",
                description="Description",
                url=invalid_url,
                published_at=datetime.now(),
            ),
            valid,
        ],
        storage,
        None,
        summarizer=FakeSummarizer(),
    )

    assert [item.status for item in result.items] == ["rejected", "accepted"]
    assert result.items[0].failure_reason == "invalid_news_url"
    assert result.items[1].content_status == "ready"


def test_ingest_raw_inputs_marks_summary_failure_and_keeps_canonical_content(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
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
                raw_text="Headline",
                title="Headline",
                description="Description",
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
    assert result.items[0].retryable is True
    assert record.article.content.startswith("Exact article content")
    assert record.summary_status == "failed"


def test_ingest_raw_inputs_marks_unknown_index_failure_retryable(temp_storage_dir):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    recorder = RecordingAttemptRecorder()

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="api",
                raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
                published_at=datetime.now(),
            )
        ],
        storage,
        FailingVectorStore(),
        summarizer=FakeSummarizer(),
        attempt_recorder=recorder,
    )

    assert result.items[0].failure_reason == "index_failed"
    assert result.items[0].retryable is True
    assert recorder.finish_metadata[-1]["failure_code"] == "index_failed"
    assert recorder.finish_metadata[-1]["retryable"] is True


def test_ingest_raw_inputs_aborts_before_content_commit_when_lease_guard_fails(
    temp_storage_dir,
):
    """A provider result returned after lease loss must not update canonical content."""
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    url = "https://example.com/lease-guard"
    fetcher = FakeFetcher(
        {url: FetchResult(url, url, "Central bank policy evidence. " * 40)}
    )

    with pytest.raises(LeaseHeartbeatFailed):
        ingest_raw_inputs(
            [
                RawEventInput(
                    source="news",
                    raw_text="Central bank policy evidence",
                    title="Central bank policy evidence",
                    description="Officials published central bank policy evidence.",
                    url=url,
                    published_at=datetime.now(timezone.utc),
                )
            ],
            storage,
            None,
            summarizer=FakeSummarizer(),
            content_fetcher=fetcher,
            lease_guard=lambda: (_ for _ in ()).throw(
                LeaseHeartbeatFailed("lease lost")
            ),
        )

    assert storage.list_article_records()[0].content_status == "pending"


def test_ingest_raw_inputs_uses_raw_text_for_non_news_inputs(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )

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


def test_ingest_raw_inputs_records_successful_external_stages_after_storage_commits(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
    recorder = RecordingAttemptRecorder()
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
        attempt_recorder=recorder,
    )

    assert result.items[0].failure_reason is None
    assert [event[1]["stage"] for event in recorder.events if event[0] == "begin"] == [
        "content_fetch",
        "summary",
        "index",
    ]
    assert [event[2:] for event in recorder.events if event[0] == "finish"] == [
        ("succeeded", None),
        ("succeeded", None),
        ("succeeded", None),
    ]
    assert recorder.events[0][1]["input_content_sha256"] is None
    assert recorder.events[2][1]["input_content_sha256"]
    assert recorder.events[4][1]["input_content_sha256"] == recorder.events[2][1]["input_content_sha256"]


def test_ingest_raw_inputs_retries_failed_derivations_for_unchanged_content_without_rewriting_it(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
    content = "Exact article content from publisher page. " * 3
    raw_input = RawEventInput(
        source="news",
        raw_text="Headline",
        title="Headline",
        description="Description",
        url="https://example.com/article",
        published_at=datetime.now(),
    )
    first_fetcher = FakeFetcher(
        {"https://example.com/article": FetchResult("https://example.com/article", "https://example.com/article", content)}
    )
    ingest_raw_inputs(
        [raw_input],
        storage,
        vector_store,
        summarizer=FakeSummarizer(error=RuntimeError("summary unavailable")),
        content_fetcher=first_fetcher,
    )
    first_record = storage.list_article_records()[0]
    original_path = first_record.article.content_path
    assert first_record.summary_status == "failed"
    assert first_record.index_status == "ready"
    assert storage.has_verified_content_hash(
        first_record.id, first_record.article.active_content_sha256
    )

    retry_summarizer = FakeSummarizer(["- Recovered summary."])
    retry_recorder = RecordingAttemptRecorder()
    second_result = ingest_raw_inputs(
        [raw_input],
        storage,
        vector_store,
        summarizer=retry_summarizer,
        content_fetcher=FakeFetcher(
            {"https://example.com/article": FetchResult("https://example.com/article", "https://example.com/article", content)}
        ),
        attempt_recorder=retry_recorder,
    )
    second_record = storage.list_article_records()[0]

    assert second_result.items[0].unchanged is True
    assert second_record.article.content_path == original_path
    assert second_record.summary_status == "ready"
    assert second_record.index_status == "ready"
    assert len(retry_summarizer.calls) == 1
    assert [event[1]["stage"] for event in retry_recorder.events if event[0] == "begin"] == [
        "content_fetch",
        "summary",
    ]


def test_ingest_raw_inputs_fails_closed_when_summary_attempt_completion_is_rejected(temp_storage_dir):
    db_path, chroma_dir = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    vector_store = ChromaVectorStore(
        persist_dir=chroma_dir, chunk_size=60, chunk_overlap=10, embedder=DeterministicEmbedder()
    )
    recorder = RejectingSummarySuccessRecorder()
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
        content_fetcher=FakeFetcher(
            {
                "https://example.com/article": FetchResult(
                    "https://example.com/article",
                    "https://example.com/article",
                    "Exact article content from publisher page. " * 3,
                )
            }
        ),
        attempt_recorder=recorder,
    )

    assert result.items[0].failure_reason == "summary_failed"
    assert result.items[0].summary_status == "failed"
    assert ("finish", recorder.events[3][1], "failed", "summary_failed") in recorder.events


def test_ingest_raw_inputs_retries_empty_ready_summary_without_an_index_attempt(temp_storage_dir):
    db_path, _ = temp_storage_dir
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()
    raw_input = RawEventInput(
        source="api",
        raw_text="Treasury yields spiked while the VIX moved sharply higher after the latest Fed minutes.",
        published_at=datetime.now(),
    )
    ingest_raw_inputs([raw_input], storage, None, summarizer=FakeSummarizer())
    first_record = storage.list_article_records()[0]
    content_hash = first_record.article.active_content_sha256
    assert storage.update_article_summary(first_record.id, "", content_sha256=content_hash)

    recorder = RecordingAttemptRecorder()
    retry_summarizer = FakeSummarizer(["- Non-empty recovered summary."])
    result = ingest_raw_inputs(
        [raw_input],
        storage,
        None,
        summarizer=retry_summarizer,
        attempt_recorder=recorder,
    )

    assert result.items[0].unchanged is True
    assert len(retry_summarizer.calls) == 1
    assert [event[1]["stage"] for event in recorder.events if event[0] == "begin"] == ["summary"]
