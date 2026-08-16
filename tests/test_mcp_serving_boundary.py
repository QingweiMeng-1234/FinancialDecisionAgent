from __future__ import annotations

from types import SimpleNamespace

import pytest

from event_collector.financial_agent_mcp import create_financial_agent_server
from event_collector.serving_generation_factory import CorpusUnavailableError


def _unavailable() -> CorpusUnavailableError:
    return CorpusUnavailableError(
        stage="active_generation",
        reason_code="missing_pointer",
        message="no active generation",
    )


def _assert_unavailable(payload: dict) -> None:
    assert payload["status"] == "failed"
    assert payload["failure_code"] == "CORPUS_UNAVAILABLE"
    assert payload["failure_stage"] == "active_generation"
    assert payload["failure_reason_code"] == "missing_pointer"
    assert payload["failure_message"] == "no active generation"


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("query_news_research", {"question": "What changed?"}),
        ("recommend_stock", {"target": "MSFT"}),
        ("retrieve_supporting_articles", {"query": "MSFT"}),
    ],
)
def test_mcp_research_tools_map_missing_active_corpus_to_stable_payload(
    monkeypatch, method, kwargs
) -> None:
    """SELECT INVARIANT: expected corpus absence never escapes as a generic MCP error."""
    server = create_financial_agent_server()
    monkeypatch.setattr(server, "_make_vector_store", lambda: (_ for _ in ()).throw(_unavailable()))
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.SQLiteNewsStore",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("storage opened")),
    )
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.SQLiteEntityStore",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entity DB opened")),
    )

    _assert_unavailable(getattr(server, method)(**kwargs))


@pytest.mark.parametrize(
    "method",
    ["run_watchlist_triage", "run_watchlist_workflow"],
)
def test_mcp_watchlist_tools_map_pinned_corpus_failure_to_stable_payload(
    monkeypatch, method
) -> None:
    server = create_financial_agent_server()
    private_method = (
        "_run_watchlist_triage_workflow"
        if method == "run_watchlist_triage"
        else "_run_refresh_then_watchlist_workflow"
    )
    monkeypatch.setattr(
        server,
        private_method,
        lambda **kwargs: (_ for _ in ()).throw(_unavailable()),
    )

    _assert_unavailable(getattr(server, method)(["MSFT"]))


def test_mcp_research_outputs_full_pinned_generation_provenance(monkeypatch) -> None:
    """SELECT INVARIANT: each returned decision identifies the exact serving corpus."""
    active = SimpleNamespace(
        generation_id="gen-active",
        corpus_id="news-v2",
        collection_name="news-v2-gen-active",
        corpus_snapshot_id="snapshot-1",
        embedding_artifact="embedder-v1",
        index_config_fingerprint="fingerprint-1",
    )
    reader = SimpleNamespace(active_generation=active)
    reader.with_time_window = lambda **kwargs: reader
    server = create_financial_agent_server()
    monkeypatch.setattr(server, "_make_vector_store", lambda: reader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.answer_query",
        lambda *args, **kwargs: SimpleNamespace(
            answer="answer",
            confidence=SimpleNamespace(value="low"),
            insufficient_evidence=True,
            supporting_points=[],
            counter_points=[],
            sources=[],
        ),
    )

    payload = server.query_news_research("What changed?")

    assert payload["generation_id"] == "gen-active"
    assert payload["corpus_provenance"] == {
        "generation_id": "gen-active",
        "corpus_id": "news-v2",
        "collection_name": "news-v2-gen-active",
        "corpus_snapshot_id": "snapshot-1",
        "embedding_artifact": "embedder-v1",
        "index_config_fingerprint": "fingerprint-1",
    }
