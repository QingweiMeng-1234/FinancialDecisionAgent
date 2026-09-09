from event_collector.news_pipeline import NewsPipelineRequest, run_news_pipeline


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

    def fake_run_ingestion(request, *, storage=None, vector_store=None, collectors=None):
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
    assert result.total_articles == 7
    assert result.answer_text == "grounded answer"


def test_run_news_pipeline_closes_owned_storage_on_failure(monkeypatch):
    monkeypatch.setattr(
        "event_collector.news_pipeline.run_news_ingestion",
        lambda request, *, storage=None, vector_store=None, collectors=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    try:
        run_news_pipeline(NewsPipelineRequest())
        assert False, "Expected pipeline failure"
    except RuntimeError as exc:
        assert "boom" in str(exc)


def test_run_news_pipeline_defaults_to_non_interactive_news_only(monkeypatch):
    calls = {}
    monkeypatch.setattr("event_collector.news_pipeline.create_corpus_vector_store", lambda **kwargs: FakeVectorStore())

    def fake_run_ingestion(request, *, storage=None, vector_store=None, collectors=None):
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
