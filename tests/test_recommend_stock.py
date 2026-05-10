import os
import tempfile

import recommend_stock


class FakeStorage:
    def __init__(self, db_path):
        self.db_path = db_path

    def count_articles(self):
        return 2

    def close(self):
        return None


class EmptyStorage(FakeStorage):
    def count_articles(self):
        return 0


class FakeVectorStore:
    def __init__(self, persist_dir, collection_name):
        self.persist_dir = persist_dir
        self.collection_name = collection_name


def test_main_generates_recommendation_and_report(monkeypatch, capsys):
    calls = {}

    monkeypatch.setattr(recommend_stock, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(recommend_stock, "ChromaVectorStore", FakeVectorStore)

    response = type(
        "Recommendation",
        (),
        {
            "decision": type("Decision", (), {"value": "HOLD"})(),
            "confidence": type("Confidence", (), {"value": "low"})(),
            "time_horizon": type("Horizon", (), {"value": "Long-term"})(),
            "insufficient_evidence": True,
            "reasoning": "Evidence is limited.",
            "key_risks": ["Coverage is thin."],
            "aggregation": None,
            "sources": [],
            "rerank_metadata": None,
        },
    )()

    def fake_recommend_target(target, vector_store, storage, top_k=3):
        calls["recommend"] = (target, vector_store.persist_dir, storage.db_path, top_k)
        return response

    def fake_write_report(target, response_obj, output_dir, debug_rerank=False, debug_aggregation=False):
        calls["report"] = (target, output_dir, debug_rerank, debug_aggregation)
        return os.path.join(output_dir, "report.md")

    monkeypatch.setattr(recommend_stock, "recommend_target", fake_recommend_target)
    monkeypatch.setattr(recommend_stock, "write_recommendation_report", fake_write_report)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = recommend_stock.main(
            [
                "--target",
                "MSFT",
                "--output-dir",
                tmpdir,
                "--debug-rerank",
                "--debug-aggregation",
            ]
        )
        captured = capsys.readouterr()

        assert result == 0
        assert calls["recommend"][0] == "MSFT"
        assert calls["report"][0] == "MSFT"
        assert "Report saved to:" in captured.out
        assert "Decision: HOLD" in captured.out


def test_main_handles_empty_database(monkeypatch, capsys):
    monkeypatch.setattr(recommend_stock, "SQLiteNewsStore", EmptyStorage)
    monkeypatch.setattr(recommend_stock, "ChromaVectorStore", FakeVectorStore)

    result = recommend_stock.main(["--target", "MSFT"])
    captured = capsys.readouterr()

    assert result == 0
    assert "No articles in database. Run main.py first." in captured.out


def test_main_surfaces_recommendation_error(monkeypatch, capsys):
    monkeypatch.setattr(recommend_stock, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(recommend_stock, "ChromaVectorStore", FakeVectorStore)

    def fake_recommend_target(target, vector_store, storage, top_k=3):
        raise RuntimeError("recommendation unavailable")

    monkeypatch.setattr(recommend_stock, "recommend_target", fake_recommend_target)

    result = recommend_stock.main(["--target", "MSFT"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: recommendation unavailable" in captured.out
