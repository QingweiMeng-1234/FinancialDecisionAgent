from event_collector.news_pipeline import NewsPipelineRequest, run_news_pipeline
from event_collector.event_collection import CollectionSourceOutcome
import pytest


class FakeStorage:
    def __init__(self, db_path="ignored.db"):
        self.db_path = db_path
        self.closed = False
        self.init_calls = 0

    def init_db(self):
        self.init_calls += 1

    def count_articles(self):
        return 7

    def close(self):
        self.closed = True


class FakeVectorStore:
    pass


def test_run_news_pipeline_uses_injected_dependencies(monkeypatch):
    calls = {}
    storage = FakeStorage()
    vector_store = FakeVectorStore()
    collectors = ["manual", "news"]
    items = [object(), object(), object()]

    def fake_run_ingestion(
        request,
        *,
        storage=None,
        vector_store=None,
        collectors=None,
        attempt_recorder=None,
    ):
        calls["ingestion_args"] = (request, storage, vector_store, collectors)
        return type(
            "IngestionResult",
            (),
            {
                "collected_events": 3,
                "stats": {
                    "total_events": 3,
                    "saved": 3,
                    "summarized": 3,
                    "indexed": 3,
                    "skipped": 0,
                },
                "total_articles": 7,
                "items": items,
                "total_inputs": 5,
                "accepted_inputs": 3,
                "rejected_inputs": 2,
                "collector_status": "succeeded",
                "source_outcomes": [],
                "source_errors": [],
                "empty_reason": None,
            },
        )()

    def fake_question(question, runtime_vector_store, top_k=3, debug_rerank=False):
        calls["question_args"] = (question, runtime_vector_store, top_k, debug_rerank)
        return "grounded answer"

    monkeypatch.setattr("event_collector.news_pipeline.run_news_ingestion", fake_run_ingestion)
    monkeypatch.setattr("event_collector.news_pipeline.run_question", fake_question)

    result = run_news_pipeline(
        NewsPipelineRequest(question="What changed?", top_k=4, debug_rerank=True),
        storage=storage,
        vector_store=vector_store,
        collectors=collectors,
    )

    assert calls["ingestion_args"][1] is storage
    assert calls["ingestion_args"][2] is vector_store
    assert calls["ingestion_args"][3] == collectors
    assert calls["ingestion_args"][0].show_progress is True
    assert calls["question_args"] == ("What changed?", vector_store, 4, True)
    assert result.collected_events == 3
    assert result.stats == {
        "total_events": 3,
        "saved": 3,
        "summarized": 3,
        "indexed": 3,
        "skipped": 0,
    }
    assert result.total_articles == 7
    assert result.answer_text == "grounded answer"
    assert result.items is items
    assert result.total_inputs == 5
    assert result.accepted_inputs == 3
    assert result.rejected_inputs == 2


def test_run_news_pipeline_preserves_structured_collection_facts(monkeypatch):
    """SELECT INVARIANT: pipeline output retains collector facts needed to distinguish partial collection from empty."""
    successful = CollectionSourceOutcome(
        source_name="manual",
        collector_status="succeeded",
        source_row_count=1,
    )
    failed = CollectionSourceOutcome(
        source_name="news",
        collector_status="failed",
        failure_code="http_429",
        retryable=True,
    )

    def fake_run_ingestion(
        request,
        *,
        storage=None,
        vector_store=None,
        collectors=None,
        attempt_recorder=None,
    ):
        return type(
            "IngestionResult",
            (),
            {
                "collected_events": 1,
                "stats": {},
                "total_articles": 1,
                "items": [],
                "total_inputs": 1,
                "accepted_inputs": 1,
                "rejected_inputs": 0,
                "collector_status": "succeeded",
                "source_outcomes": [successful, failed],
                "source_errors": [failed],
                "source_error_count": 1,
                "empty_reason": None,
            },
        )()

    monkeypatch.setattr("event_collector.news_pipeline.run_news_ingestion", fake_run_ingestion)

    result = run_news_pipeline(NewsPipelineRequest())

    assert result.collector_status == "succeeded"
    assert result.source_outcomes == [successful, failed]
    assert result.source_errors == [failed]
    assert result.source_error_count == 1
    assert result.empty_reason is None


def test_run_news_pipeline_forwards_attempt_recorder_to_ingestion(monkeypatch):
    calls = {}
    attempt_recorder = object()

    def fake_run_ingestion(
        request,
        *,
        storage=None,
        vector_store=None,
        collectors=None,
        attempt_recorder=None,
    ):
        calls["attempt_recorder"] = attempt_recorder
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
                "collector_status": "succeeded",
                "source_outcomes": [],
                "source_errors": [],
                "empty_reason": None,
            },
        )()

    monkeypatch.setattr("event_collector.news_pipeline.run_news_ingestion", fake_run_ingestion)

    run_news_pipeline(NewsPipelineRequest(attempt_recorder=attempt_recorder))

    assert calls["attempt_recorder"] is attempt_recorder


def test_run_news_pipeline_closes_owned_storage_on_failure(monkeypatch):
    monkeypatch.setattr(
        "event_collector.news_pipeline.run_news_ingestion",
        lambda request, *, storage=None, vector_store=None, collectors=None, attempt_recorder=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    try:
        run_news_pipeline(NewsPipelineRequest())
        assert False, "Expected pipeline failure"
    except RuntimeError as exc:
        assert "boom" in str(exc)


def test_run_news_pipeline_defaults_to_non_interactive_news_only(monkeypatch):
    calls = {}

    def fake_run_ingestion(
        request,
        *,
        storage=None,
        vector_store=None,
        collectors=None,
        attempt_recorder=None,
    ):
        calls["request"] = request
        calls["collectors"] = collectors
        return type(
            "IngestionResult",
            (),
            {
                "collected_events": 0,
                "stats": {
                    "total_events": 0,
                    "saved": 0,
                    "summarized": 0,
                    "indexed": 0,
                    "skipped": 0,
                },
                "total_articles": 0,
                "items": [],
                "total_inputs": 0,
                "accepted_inputs": 0,
                "rejected_inputs": 0,
                "collector_status": "succeeded",
                "source_outcomes": [],
                "source_errors": [],
                "empty_reason": None,
            },
        )()

    monkeypatch.setattr(
        "event_collector.news_pipeline.run_news_ingestion",
        fake_run_ingestion,
    )

    run_news_pipeline(
        NewsPipelineRequest(
            news_page_size=25,
            news_endpoint="everything",
            news_days_back=7,
            news_page=1,
            news_sort_by="publishedAt",
        )
    )

    assert calls["collectors"] is None
    assert calls["request"].news_page_size == 25
    assert calls["request"].include_manual is False


def test_run_news_pipeline_never_constructs_legacy_chroma_implicitly(monkeypatch):
    """SELECT INVARIANT: pipeline cannot open legacy Chroma as a hidden read/write default."""
    result = type(
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
            "collector_status": "succeeded",
            "source_outcomes": [],
            "source_errors": [],
            "empty_reason": None,
        },
    )()
    monkeypatch.setattr("event_collector.news_pipeline.run_news_ingestion", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        "event_collector.news_pipeline.ChromaVectorStore",
        lambda *args, **kwargs: pytest.fail("legacy Chroma must not be constructed"),
    )

    assert run_news_pipeline(NewsPipelineRequest()).answer_text is None
    with pytest.raises(ValueError, match="explicit generation-pinned vector_store"):
        run_news_pipeline(NewsPipelineRequest(question="What changed?"))
