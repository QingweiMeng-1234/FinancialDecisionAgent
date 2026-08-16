from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from event_collector.recommendation import RecommendationResponse, write_recommendation_report
from event_collector.watchlist_progress import WatchlistTimelineRecorder, load_watchlist_timeline
from event_collector.watchlist_triage import WatchlistRunRequest
from event_collector.watchlist_workflow import (
    read_watchlist_timeline_artifact,
    run_watchlist_triage_workflow,
    write_watchlist_report,
    write_watchlist_timeline,
)


PROVENANCE = {
    "generation_id": "generation-test-1",
    "corpus_id": "news",
    "collection_name": "news_articles_generation_test_1",
    "corpus_snapshot_id": "snapshot-test-1",
    "embedding_artifact": "sentence-transformers/all-MiniLM-L6-v2",
    "index_config_fingerprint": "b" * 64,
}


def _recommendation_response() -> RecommendationResponse:
    return RecommendationResponse.model_validate(
        {
            "decision": "HOLD",
            "confidence": "low",
            "time_horizon": "Long-term",
            "reasoning": "Evidence is limited.",
            "key_risks": [],
            "insufficient_evidence": True,
            "sources": [],
        }
    )


def test_recommendation_report_requires_provenance_before_creating_output_dir(tmp_path):
    """SELECT INVARIANT: direct report persistence never creates an unbound asset."""
    output_dir = tmp_path / "recommendations"

    with pytest.raises(ValueError, match="publication provenance"):
        write_recommendation_report("MSFT", _recommendation_response(), output_dir=str(output_dir))

    assert not output_dir.exists()


def test_recommendation_report_persists_all_request_pinned_provenance(tmp_path):
    """SELECT INVARIANT: persisted proof matches the complete MCP serving identity."""
    report_path = write_recommendation_report(
        "MSFT",
        _recommendation_response(),
        output_dir=str(tmp_path),
        generated_at=datetime(2026, 8, 15, 12, 0, 0),
        retrieval_provenance=PROVENANCE,
    )

    report = open(report_path, encoding="utf-8").read()
    assert "## Retrieval Provenance" in report
    assert "generation-test-1" in report
    assert "news_articles_generation_test_1" in report
    assert "snapshot-test-1" in report
    assert "sentence-transformers/all-MiniLM-L6-v2" in report
    assert "b" * 64 in report


def test_watchlist_writers_require_provenance_before_creating_assets(tmp_path, monkeypatch):
    """SELECT INVARIANT: report and timeline writers fail closed independently."""
    output_dir = tmp_path / "watchlist"
    result = SimpleNamespace(run_id="watch-1", run_at=datetime(2026, 8, 15, 12, 0, 0))
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.render_watchlist_report",
        lambda *args, **kwargs: "# report\n" + repr(kwargs["retrieval_provenance"]) + "\n",
    )

    with pytest.raises(ValueError, match="publication provenance"):
        write_watchlist_report(result, output_dir=str(output_dir))
    with pytest.raises(ValueError, match="publication provenance"):
        write_watchlist_timeline("watch-1", WatchlistTimelineRecorder(), output_dir=str(output_dir))

    assert not output_dir.exists()


def test_watchlist_writers_persist_provenance_in_markdown_and_jsonl(tmp_path, monkeypatch):
    result = SimpleNamespace(run_id="watch-1", run_at=datetime(2026, 8, 15, 12, 0, 0))
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.render_watchlist_report",
        lambda *args, **kwargs: "# report\n" + repr(kwargs["retrieval_provenance"]) + "\n",
    )

    report_path = write_watchlist_report(
        result,
        output_dir=str(tmp_path),
        retrieval_provenance=PROVENANCE,
    )
    timeline_path = write_watchlist_timeline(
        "watch-1",
        WatchlistTimelineRecorder(),
        output_dir=str(tmp_path),
        retrieval_provenance=PROVENANCE,
    )

    report = open(report_path, encoding="utf-8").read()
    timeline = open(timeline_path, encoding="utf-8").read().splitlines()[0]
    for value in PROVENANCE.values():
        assert value in report
        assert value in timeline
    assert load_watchlist_timeline(timeline_path) == []


def test_watchlist_workflow_rejects_missing_provenance_before_research(monkeypatch, tmp_path):
    """SELECT INVARIANT: the public workflow boundary rejects before retrieval side effects."""
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_research",
        lambda *args, **kwargs: pytest.fail("research must not start"),
    )

    with pytest.raises(ValueError, match="publication provenance"):
        run_watchlist_triage_workflow(
            WatchlistRunRequest(tickers=["MSFT"]),
            SimpleNamespace(),
            object(),
            output_dir=str(tmp_path / "watchlist"),
        )


def test_watchlist_timeline_reader_returns_persisted_provenance(tmp_path):
    """SELECT INVARIANT: timeline readback exposes the exact persisted serving identity."""
    write_watchlist_timeline(
        "watch-1",
        WatchlistTimelineRecorder(),
        output_dir=str(tmp_path),
        retrieval_provenance=PROVENANCE,
    )

    payload = read_watchlist_timeline_artifact("watch-1", reports_dir=str(tmp_path))

    assert payload["found"] is True
    assert payload["retrieval_provenance"] == PROVENANCE


def test_watchlist_timeline_reader_keeps_legacy_files_compatible(tmp_path):
    """SELECT INVARIANT: pre-provenance event-only timelines remain readable."""
    timeline = tmp_path / "watch-legacy.timeline.jsonl"
    timeline.write_text(
        '{"scope":"workflow","stage":"retrieval","status":"finished",'
        '"ticker":null,"started_at":null,"finished_at":null,"duration_ms":1,'
        '"message":null,"metrics":{}}\n',
        encoding="utf-8",
    )

    payload = read_watchlist_timeline_artifact("watch-legacy", reports_dir=str(tmp_path))

    assert payload["found"] is True
    assert payload["retrieval_provenance"] is None
    assert payload["events"][0]["stage"] == "retrieval"


def test_watchlist_timeline_reader_fails_closed_on_invalid_provenance(tmp_path):
    """SELECT INVARIANT: a declared but incomplete proof cannot masquerade as legacy."""
    timeline = tmp_path / "watch-invalid.timeline.jsonl"
    timeline.write_text(
        '{"record_type":"retrieval_provenance","generation_id":"generation-test-1"}\n',
        encoding="utf-8",
    )

    payload = read_watchlist_timeline_artifact("watch-invalid", reports_dir=str(tmp_path))

    assert payload["found"] is False
    assert payload["retrieval_provenance"] is None
    assert payload["events"] == []
    assert payload["error_code"] == "timeline_provenance_invalid"
