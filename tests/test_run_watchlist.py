import os
import tempfile
from types import SimpleNamespace

import run_watchlist
from event_collector.service_defaults import ServiceDefaults
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

    def with_time_window(self, **kwargs):
        return self


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


def test_parse_requested_tickers_supports_csv_and_repeat_flags():
    args = run_watchlist.parse_args(["--tickers", "nvda, msft", "--ticker", "tsla", "--ticker", "msft"])
    assert run_watchlist.parse_requested_tickers(args) == ["NVDA", "MSFT", "TSLA"]


def test_main_generates_watchlist_run_and_report(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.run_watchlist"

    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: _pinned())

    result = type("Result", (), {"run_id": "run-123", "ranked_items": [], "top_n": 3})()

    def fake_run_workflow(request, storage, vector_store, output_dir, persist_timeline, debug_review, debug_rerank, retrieval_provenance):
        calls["run"] = (
            request.tickers,
            request.top_n,
            request.force_structure,
            vector_store.persist_dir,
            storage.db_path,
        )
        calls["workflow"] = (output_dir, persist_timeline, debug_review, debug_rerank)
        calls["provenance"] = retrieval_provenance
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
        assert calls["provenance"]["generation_id"] == "gen-active"
        assert calls["summary"] == ("run-123", True, True)
        assert "Report saved to:" in captured.out
        assert "Timeline saved to:" in captured.out
        assert "Watchlist Triage:" in captured.out


def test_main_handles_empty_database(monkeypatch, capsys):
    cli_path = "event_collector.cli.run_watchlist"
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", EmptyStorage)
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: _pinned())

    exit_code = run_watchlist.main(["--tickers", "MSFT"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No articles in database. Run main.py first." in captured.out


def test_watchlist_cli_fails_closed_before_opening_legacy_storage_or_chroma(
    monkeypatch, capsys
):
    """SELECT INVARIANT: watchlist serving requires an active generation."""
    cli_path = "event_collector.cli.run_watchlist"
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

    assert run_watchlist.main(["--tickers", "MSFT"]) == 1
    assert "CORPUS_UNAVAILABLE:missing_pointer" in capsys.readouterr().out


def test_watchlist_cli_scopes_each_reader_once_with_defaults_or_explicit_window(
    monkeypatch, tmp_path
):
    """SELECT INVARIANT: every watchlist CLI run has one finite time scope."""
    cli_path = "event_collector.cli.run_watchlist"

    class RecordingReader:
        def __init__(self):
            self.calls = []
            self.scoped = object()

        def with_time_window(self, **kwargs):
            self.calls.append(kwargs)
            return self.scoped

    default_reader = RecordingReader()
    explicit_reader = RecordingReader()
    readers = iter((default_reader, explicit_reader))
    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: _pinned(next(readers)),
    )
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(
        f"{cli_path}.load_service_defaults",
        lambda: ServiceDefaults(query_lookback_days=19),
    )
    downstream_readers = []

    def run_workflow(request, storage, vector_store, **kwargs):
        downstream_readers.append(vector_store)
        result = type("Result", (), {"run_id": "run-123", "ranked_items": [], "top_n": 3})()
        return type(
            "Workflow",
            (),
            {
                "result": result,
                "report_path": str(tmp_path / "watchlist.md"),
                "timeline_path": None,
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_watchlist_triage_workflow", run_workflow)
    monkeypatch.setattr(f"{cli_path}.render_watchlist_summary", lambda *args, **kwargs: "summary")

    assert run_watchlist.main(["--tickers", "MSFT"]) == 0
    assert run_watchlist.main(
        [
            "--tickers",
            "MSFT",
            "--start-at",
            "2026-08-01T00:00:00+00:00",
            "--end-at",
            "2026-08-10T00:00:00+00:00",
        ]
    ) == 0

    assert len(default_reader.calls) == 1
    assert default_reader.calls[0]["lookback_days"] == 19
    latest_at = __import__("datetime").datetime.fromisoformat(
        default_reader.calls[0]["latest_at"]
    )
    assert latest_at.utcoffset() is not None
    assert len(explicit_reader.calls) == 1
    assert explicit_reader.calls[0] == {
        "start_at": "2026-08-01T00:00:00+00:00",
        "end_at": "2026-08-10T00:00:00+00:00",
        "latest_at": None,
        "lookback_days": None,
    }
    assert downstream_readers == [default_reader.scoped, explicit_reader.scoped]
