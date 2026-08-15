import os
import tempfile

import run_watchlist


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


def test_parse_requested_tickers_supports_csv_and_repeat_flags():
    args = run_watchlist.parse_args(["--tickers", "nvda, msft", "--ticker", "tsla", "--ticker", "msft"])
    assert run_watchlist.parse_requested_tickers(args) == ["NVDA", "MSFT", "TSLA"]


def test_main_generates_watchlist_run_and_report(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.run_watchlist"

    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.ChromaVectorStore", FakeVectorStore)

    result = type("Result", (), {"run_id": "run-123", "ranked_items": [], "top_n": 3})()

    def fake_run_workflow(request, storage, vector_store, output_dir, persist_timeline, debug_review, debug_rerank):
        calls["run"] = (
            request.tickers,
            request.top_n,
            request.force_structure,
            vector_store.persist_dir,
            storage.db_path,
        )
        calls["workflow"] = (output_dir, persist_timeline, debug_review, debug_rerank)
        return type(
            "Workflow",
            (),
            {
                "result": result,
                "report_path": os.path.join(output_dir, "watchlist.md"),
                "timeline_path": os.path.join(output_dir, "watchlist.timeline.jsonl"),
            },
        )()

    def fake_render_summary(result_obj, debug_review=False, debug_rerank=False):
        calls["summary"] = (result_obj.run_id, debug_review, debug_rerank)
        return "Watchlist Triage:\n1. MSFT - High / High"

    monkeypatch.setattr(f"{cli_path}.run_watchlist_triage_workflow", fake_run_workflow)
    monkeypatch.setattr(f"{cli_path}.render_watchlist_summary", fake_render_summary)

    with tempfile.TemporaryDirectory() as tmpdir:
        exit_code = run_watchlist.main(
            [
                "--tickers",
                "msft,aapl",
                "--top-n",
                "1",
                "--output-dir",
                tmpdir,
                "--debug-review",
                "--debug-rerank",
                "--force-structure",
            ]
        )
        captured = capsys.readouterr()

        assert exit_code == 0
        assert calls["run"][0] == ["MSFT", "AAPL"]
        assert calls["run"][1] == 1
        assert calls["run"][2] is True
        assert calls["workflow"] == (tmpdir, True, True, True)
        assert calls["summary"] == ("run-123", True, True)
        assert "Report saved to:" in captured.out
        assert "Timeline saved to:" in captured.out
        assert "Watchlist Triage:" in captured.out


def test_main_handles_empty_database(monkeypatch, capsys):
    cli_path = "event_collector.cli.run_watchlist"
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", EmptyStorage)
    monkeypatch.setattr(f"{cli_path}.ChromaVectorStore", FakeVectorStore)

    exit_code = run_watchlist.main(["--tickers", "MSFT"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No articles in database. Run main.py first." in captured.out
