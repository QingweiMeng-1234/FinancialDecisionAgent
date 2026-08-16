import os
import tempfile
from types import SimpleNamespace

import recommend_stock
from event_collector.serving_generation_factory import CorpusUnavailableError


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


def _pinned(reader=None):
    reader = reader or FakeVectorStore("active-root", "active-collection")
    return SimpleNamespace(
        reader=reader,
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            collection_name="active-collection",
            corpus_snapshot_id="snapshot-active",
            index_config_fingerprint="fingerprint-active",
        ),
        eligibility=SimpleNamespace(articles=(), exclusions=()),
    )


def test_main_generates_recommendation_and_report(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.recommend_stock"

    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: _pinned())

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

    def fake_recommend_target(
        target,
        vector_store,
        storage,
        top_k=3,
        retrieval_top_k=5,
        retrieval_intent="direct",
    ):
        calls["recommend"] = (target, vector_store.persist_dir, storage.db_path, top_k, retrieval_top_k, retrieval_intent)
        return response

    def fake_write_report(target, response_obj, output_dir, debug_rerank=False, debug_aggregation=False, retrieval_provenance=None):
        calls["report"] = (target, output_dir, debug_rerank, debug_aggregation, retrieval_provenance)
        return os.path.join(output_dir, "report.md")

    monkeypatch.setattr(f"{cli_path}.recommend_target", fake_recommend_target)
    monkeypatch.setattr(f"{cli_path}.write_recommendation_report", fake_write_report)

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
        assert calls["recommend"][4] == 5
        assert calls["recommend"][5] == "direct"
        assert calls["report"][0] == "MSFT"
        assert calls["report"][4]["generation_id"] == "gen-active"
        assert "Report saved to:" in captured.out
        assert "Decision: HOLD" in captured.out


def test_main_handles_empty_database(monkeypatch, capsys):
    cli_path = "event_collector.cli.recommend_stock"
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", EmptyStorage)
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: _pinned())

    result = recommend_stock.main(["--target", "MSFT"])
    captured = capsys.readouterr()

    assert result == 0
    assert "No articles in database. Run main.py first." in captured.out


def test_main_surfaces_recommendation_error(monkeypatch, capsys):
    cli_path = "event_collector.cli.recommend_stock"
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: _pinned())

    def fake_recommend_target(target, vector_store, storage, top_k=3, retrieval_top_k=5, retrieval_intent="direct"):
        raise RuntimeError("recommendation unavailable")

    monkeypatch.setattr(f"{cli_path}.recommend_target", fake_recommend_target)

    result = recommend_stock.main(["--target", "MSFT"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: recommendation unavailable" in captured.out


def test_recommendation_cli_fails_closed_before_opening_legacy_storage_or_chroma(
    monkeypatch, capsys
):
    """SELECT INVARIANT: recommendation serving requires an active generation."""
    cli_path = "event_collector.cli.recommend_stock"
    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: (_ for _ in ()).throw(
            CorpusUnavailableError(
                stage="active_generation",
                reason_code="missing_pointer",
                message="no active generation",
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        f"{cli_path}.SQLiteNewsStore",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("storage opened")),
    )

    assert recommend_stock.main(["--target", "MSFT"]) == 1
    assert "CORPUS_UNAVAILABLE:missing_pointer" in capsys.readouterr().out


def test_recommendation_cli_uses_only_the_active_generation_reader(monkeypatch, tmp_path):
    """SELECT INVARIANT: no caller-controlled legacy collection reaches recommendation."""
    cli_path = "event_collector.cli.recommend_stock"
    reader = SimpleNamespace(active_generation=SimpleNamespace(generation_id="gen-active"))
    pinned = SimpleNamespace(
        reader=reader,
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            corpus_id="news-v2",
            collection_name="news-v2-gen-active",
            corpus_snapshot_id="snapshot-1",
            embedding_artifact="embedder-v1",
            index_config_fingerprint="fingerprint-1",
        ),
        eligibility=SimpleNamespace(articles=(object(),), exclusions=()),
    )
    captured = {}
    def open_reader(config):
        captured["config"] = config
        return pinned
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", open_reader)
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    def recommend(target, vector_store, storage, **kwargs):
        captured["reader"] = vector_store
        return type(
            "Recommendation",
            (),
            {
                "decision": type("Decision", (), {"value": "HOLD"})(),
                "confidence": type("Confidence", (), {"value": "low"})(),
                "time_horizon": type("Horizon", (), {"value": "Long-term"})(),
                "insufficient_evidence": True,
                "reasoning": "thin",
                "key_risks": [],
                "aggregation": None,
                "sources": [],
                "rerank_metadata": None,
            },
        )()
    monkeypatch.setattr(f"{cli_path}.recommend_target", recommend)
    monkeypatch.setattr(f"{cli_path}.write_recommendation_report", lambda *args, **kwargs: str(tmp_path / "report.md"))

    assert recommend_stock.main(["--target", "MSFT", "--canonical-db-path", "canonical.db"]) == 0
    assert captured["reader"] is reader
    assert captured["config"].canonical_db_path == "canonical.db"
