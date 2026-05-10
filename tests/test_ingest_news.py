import ingest_news


def test_main_runs_ingestion_only(monkeypatch, capsys):
    calls = {}

    class FakeStorage:
        def __init__(self, db_path):
            calls["db_path"] = db_path

        def init_db(self):
            calls["init_db"] = True

        def count_articles(self):
            return 4

        def close(self):
            calls["closed"] = True

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name):
            calls["persist_dir"] = persist_dir
            calls["collection_name"] = collection_name

    monkeypatch.setattr(ingest_news, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(ingest_news, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(ingest_news, "ManualCollector", lambda: "manual")
    monkeypatch.setattr(ingest_news, "NewsCollector", lambda: "news")
    monkeypatch.setattr(ingest_news, "collect_from_all_sources", lambda collectors: type("Batch", (), {"events": [1, 2]})())
    monkeypatch.setattr(
        ingest_news,
        "ingest_events_to_storage",
        lambda batch, storage, vector_store: {
            "total_events": 2,
            "saved": 2,
            "summarized": 2,
            "indexed": 2,
            "skipped": 0,
        },
    )

    result = ingest_news.main([])
    captured = capsys.readouterr()

    assert result == 0
    assert "Ready for grounded RAG queries." in captured.out
    assert "Summarized:    2" in captured.out
    assert calls["closed"] is True


def test_main_runs_question_after_ingestion(monkeypatch, capsys):
    calls = {}

    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def init_db(self):
            return None

        def count_articles(self):
            return 1

        def close(self):
            calls["closed"] = True

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name):
            self.persist_dir = persist_dir
            self.collection_name = collection_name

    monkeypatch.setattr(ingest_news, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(ingest_news, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(ingest_news, "ManualCollector", lambda: "manual")
    monkeypatch.setattr(ingest_news, "NewsCollector", lambda: "news")
    monkeypatch.setattr(ingest_news, "collect_from_all_sources", lambda collectors: type("Batch", (), {"events": [1]})())
    monkeypatch.setattr(
        ingest_news,
        "ingest_events_to_storage",
        lambda batch, storage, vector_store: {
            "total_events": 1,
            "saved": 1,
            "summarized": 1,
            "indexed": 1,
            "skipped": 0,
        },
    )

    def fake_run_question(question, vector_store, top_k=3, debug_rerank=False):
        calls["question"] = question
        calls["top_k"] = top_k
        calls["debug_rerank"] = debug_rerank
        return "formatted grounded answer"

    monkeypatch.setattr(ingest_news, "run_question", fake_run_question)

    result = ingest_news.main(["--question", "What changed?", "--top-k", "5", "--debug-rerank"])
    captured = capsys.readouterr()

    assert result == 0
    assert "Grounded RAG Answer:" in captured.out
    assert "formatted grounded answer" in captured.out
    assert calls["question"] == "What changed?"
    assert calls["top_k"] == 5
    assert calls["debug_rerank"] is True
    assert calls["closed"] is True


def test_main_surfaces_ingestion_or_query_errors(monkeypatch, capsys):
    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def init_db(self):
            return None

        def close(self):
            return None

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name):
            self.persist_dir = persist_dir
            self.collection_name = collection_name

    monkeypatch.setattr(ingest_news, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(ingest_news, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(ingest_news, "ManualCollector", lambda: "manual")
    monkeypatch.setattr(ingest_news, "NewsCollector", lambda: "news")
    monkeypatch.setattr(ingest_news, "collect_from_all_sources", lambda collectors: type("Batch", (), {"events": [1]})())
    monkeypatch.setattr(ingest_news, "ingest_events_to_storage", lambda batch, storage, vector_store: (_ for _ in ()).throw(RuntimeError("ingest failed")))

    result = ingest_news.main([])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: ingest failed" in captured.out
