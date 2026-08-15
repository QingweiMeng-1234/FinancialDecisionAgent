from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from event_collector.news_storage import SQLiteNewsStore
from event_collector.watchlist_progress import WatchlistProgressEvent, WatchlistTimelineRecorder, utc_now
from event_collector.watchlist_research import WatchlistResearchConfig
from event_collector.watchlist_triage import WatchlistRunRequest
from event_collector.watchlist_workflow import (
    RefreshNewsRequest,
    read_watchlist_report_artifact,
    read_watchlist_timeline_artifact,
    run_refresh_then_watchlist_workflow,
    run_watchlist_triage_workflow,
)


def _make_storage(tmpdir: str) -> SQLiteNewsStore:
    storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
    storage.init_db()
    return storage


def _fake_research_run(run_id: str = "watch-1"):
    return SimpleNamespace(
        result=SimpleNamespace(run_id=run_id),
        batching={
            "enabled": False,
            "batch_count": 1,
            "batch_size": 1,
            "input_ticker_count": 1,
            "report_supported": True,
        },
    )


class FakeReportPathStorage:
    def __init__(self):
        self.report_paths: dict[str, str] = {}

    def save_watchlist_report_path(self, run_id: str, report_path: str) -> bool:
        self.report_paths[run_id] = report_path
        return True

    def fetch_watchlist_report_path(self, run_id: str) -> str | None:
        return self.report_paths.get(run_id)


def test_run_refresh_then_watchlist_workflow_refreshes_before_triage(monkeypatch):
    call_order: list[str] = []

    monkeypatch.setattr(
        "event_collector.watchlist_workflow.refresh_news_corpus",
        lambda request, progress_sink=None: call_order.append("refresh")
        or {"status": "refreshed", "refresh_date": "2026-07-01"},
    )
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_triage_workflow",
        lambda request, storage, vector_store_provider, **kwargs: call_order.append("triage")
        or SimpleNamespace(
            result=SimpleNamespace(run_id="watch-ordered", ranked_items=[], retrieval_failures=[], top_n=request.top_n),
            batching={"enabled": False, "batch_count": 1, "batch_size": 2, "input_ticker_count": 2, "report_supported": True},
            report_path="report.md",
            timeline_path="timeline.jsonl",
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:",
        ),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        workflow = run_refresh_then_watchlist_workflow(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"]),
            storage,
            object(),
            refresh_request=RefreshNewsRequest(today="2026-07-01", refresh_state_path=os.path.join(tmpdir, "refresh.json")),
        )

        assert call_order == ["refresh", "triage"]
        assert workflow.refresh == {"status": "refreshed", "refresh_date": "2026-07-01"}
        storage.close()


def test_run_watchlist_triage_workflow_persists_report_and_timeline(monkeypatch):
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_research",
        lambda request, storage, vector_store_provider, **kwargs: _fake_research_run("watch-workflow"),
    )

    def fake_write_report(result, output_dir, debug_review=False, debug_rerank=False):
        path = os.path.join(output_dir, f"{result.run_id}.md")
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# {result.run_id}\n")
        return path

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FakeReportPathStorage()
        recorder = WatchlistTimelineRecorder()
        recorder.emit(
            WatchlistProgressEvent(
                scope="workflow",
                stage="triage",
                status="finished",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_ms=7,
            )
        )

        workflow = run_watchlist_triage_workflow(
            WatchlistRunRequest(tickers=["MSFT"]),
            storage,
            object(),
            output_dir=os.path.join(tmpdir, "reports"),
            config=WatchlistResearchConfig(),
            timeline_recorder=recorder,
            persist_timeline=True,
            write_report_fn=fake_write_report,
        )

        assert workflow.report_path.endswith("watch-workflow.md")
        assert os.path.exists(workflow.report_path)
        assert workflow.timeline_path is not None
        assert os.path.exists(workflow.timeline_path)
        assert storage.report_paths["watch-workflow"] == workflow.report_path
        assert workflow.timing_summary["total_duration_ms"] >= 7
        assert any(stage["stage"] == "persist_report" for stage in workflow.timing_summary["workflow_stages"])
        assert workflow.timing_text.startswith("Timing Summary:")


def test_read_watchlist_report_artifact_handles_found_missing_and_unsafe_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FakeReportPathStorage()
        reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        found_path = os.path.join(reports_dir, "watch-found.md")
        with open(found_path, "w", encoding="utf-8") as handle:
            handle.write("report body")
        storage.save_watchlist_report_path("watch-found", found_path)

        missing_payload = read_watchlist_report_artifact("watch-missing", storage=storage, reports_dir=reports_dir)
        found_payload = read_watchlist_report_artifact("watch-found", storage=storage, reports_dir=reports_dir)

        outside_path = os.path.join(tmpdir, "outside.md")
        with open(outside_path, "w", encoding="utf-8") as handle:
            handle.write("outside")
        storage.save_watchlist_report_path("watch-unsafe", outside_path)
        unsafe_payload = read_watchlist_report_artifact("watch-unsafe", storage=storage, reports_dir=reports_dir)

        deleted_path = os.path.join(reports_dir, "watch-deleted.md")
        storage.save_watchlist_report_path("watch-deleted", deleted_path)
        missing_file_payload = read_watchlist_report_artifact("watch-deleted", storage=storage, reports_dir=reports_dir)

        assert missing_payload["error_code"] == "report_not_found"
        assert found_payload["found"] is True
        assert found_payload["content"] == "report body"
        assert unsafe_payload["error_code"] == "report_not_found"
        assert missing_file_payload["error_code"] == "report_file_missing"


def test_read_watchlist_timeline_artifact_handles_found_missing_and_unsafe_paths(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        timeline_path = os.path.join(reports_dir, "watch-found.timeline.jsonl")
        recorder = WatchlistTimelineRecorder()
        recorder.emit(
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_ms=11,
            )
        )
        recorder.write_jsonl(timeline_path)

        found_payload = read_watchlist_timeline_artifact("watch-found", reports_dir=reports_dir)
        missing_payload = read_watchlist_timeline_artifact("watch-missing", reports_dir=reports_dir)

        unsafe_root = os.path.join(tmpdir, "safe-root")
        os.makedirs(unsafe_root, exist_ok=True)
        monkeypatch.setattr(
            "event_collector.watchlist_workflow._resolve_path",
            lambda path: Path(tmpdir, "outside", os.path.basename(path)),
        )
        unsafe_payload = read_watchlist_timeline_artifact("watch-unsafe", reports_dir=unsafe_root)

        assert found_payload["found"] is True
        assert found_payload["events"][0]["stage"] == "refresh_news"
        assert found_payload["timing_summary"]["total_duration_ms"] == 11
        assert missing_payload["error_code"] == "timeline_not_found"
        assert unsafe_payload["error_code"] == "timeline_not_found"
