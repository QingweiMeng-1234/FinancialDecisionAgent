from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
from types import SimpleNamespace
from datetime import datetime

from event_collector.financial_agent_mcp import (
    FinancialAgentRuntimeConfig,
    create_financial_agent_server,
)


def _runtime(tmp_path) -> FinancialAgentRuntimeConfig:
    return FinancialAgentRuntimeConfig(
        db_path=str(tmp_path / "canonical.db"),
        index_control_db_path=str(tmp_path / "control.db"),
        index_corpus_id="news-v2",
        canonical_db_path=str(tmp_path / "canonical.db"),
        canonical_content_root=str(tmp_path / "content"),
        chroma_persist_dir=str(tmp_path / "chroma-v2"),
    )


def test_mcp_reader_factory_uses_control_plane_config_not_legacy_collection(
    tmp_path, monkeypatch
) -> None:
    captured = {}
    reader = SimpleNamespace(active_generation=SimpleNamespace(generation_id="gen-active"))
    reader.with_time_window = lambda **kwargs: reader

    def create_reader(config):
        captured["config"] = config
        return SimpleNamespace(reader=reader)

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.create_active_generation_reader",
        create_reader,
    )
    server = create_financial_agent_server(_runtime(tmp_path))

    assert server._make_vector_store() is reader
    config = captured["config"]
    assert config.index_control_db_path == str(tmp_path / "control.db")
    assert config.index_corpus_id == "news-v2"
    assert config.canonical_db_path == str(tmp_path / "canonical.db")
    assert config.canonical_content_root == str(tmp_path / "content")
    assert config.chroma_persist_dir == str(tmp_path / "chroma-v2")


def test_mcp_query_returns_active_generation_provenance(tmp_path, monkeypatch) -> None:
    reader = SimpleNamespace(active_generation=SimpleNamespace(generation_id="gen-active"))
    reader.with_time_window = lambda **kwargs: reader
    server = create_financial_agent_server(_runtime(tmp_path))
    monkeypatch.setattr(server, "_make_vector_store", lambda: reader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.answer_query",
        lambda *args, **kwargs: SimpleNamespace(
            answer="answer",
            confidence=SimpleNamespace(value="medium"),
            insufficient_evidence=False,
            supporting_points=[],
            counter_points=[],
            sources=[],
        ),
    )

    payload = server.query_news_research("What changed?")

    assert payload["generation_id"] == "gen-active"


def test_mcp_query_binds_time_window_to_request_pinned_reader(tmp_path, monkeypatch) -> None:
    captured = {}

    class Reader:
        active_generation = SimpleNamespace(generation_id="gen-active")

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return SimpleNamespace(active_generation=self.active_generation)

    server = create_financial_agent_server(_runtime(tmp_path))
    monkeypatch.setattr(server, "_make_vector_store", Reader)
    def answer_query(question, vector_store, **kwargs):
        captured["reader"] = vector_store
        return SimpleNamespace(
            answer="answer",
            confidence=SimpleNamespace(value="medium"),
            insufficient_evidence=False,
            supporting_points=[],
            counter_points=[],
            sources=[],
        )

    monkeypatch.setattr("event_collector.financial_agent_mcp.answer_query", answer_query)

    server.query_news_research(
        "What changed?",
        latest_at="2026-08-16T00:00:00+00:00",
        lookback_days=7,
    )

    assert captured["window"] == {
        "start_at": None,
        "end_at": None,
        "latest_at": "2026-08-16T00:00:00+00:00",
        "lookback_days": 7,
    }
    assert captured["reader"].active_generation.generation_id == "gen-active"


def test_mcp_supporting_article_query_binds_time_window(tmp_path, monkeypatch) -> None:
    captured = {}

    class Reader:
        active_generation = SimpleNamespace(generation_id="gen-active")

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return SimpleNamespace(active_generation=self.active_generation)

    server = create_financial_agent_server(_runtime(tmp_path))
    monkeypatch.setattr(server, "_make_vector_store", Reader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.SQLiteEntityStore",
        lambda **kwargs: SimpleNamespace(close=lambda: None),
    )

    def retrieve(query, vector_store, **kwargs):
        captured["reader"] = vector_store
        return SimpleNamespace(evidence=[])

    monkeypatch.setattr("event_collector.financial_agent_mcp.retrieve_evidence_bundle", retrieve)

    payload = server.retrieve_supporting_articles(
        "MSFT",
        start_at="2026-08-15T00:00:00+00:00",
        end_at="2026-08-16T00:00:00+00:00",
    )

    assert captured["reader"].active_generation.generation_id == "gen-active"
    assert payload["generation_id"] == "gen-active"


def test_mcp_query_uses_configured_finite_lookback_by_default(tmp_path, monkeypatch) -> None:
    captured = {}

    class Reader:
        active_generation = SimpleNamespace(generation_id="gen-active")

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return SimpleNamespace(active_generation=self.active_generation)

    server = create_financial_agent_server(_runtime(tmp_path))
    monkeypatch.setattr(server, "_make_vector_store", Reader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.resolve_query_time_window",
        lambda defaults, **kwargs: {
            "start_at": None,
            "end_at": None,
            "latest_at": "2026-08-15T12:00:00+00:00",
            "lookback_days": 30,
        },
    )
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.answer_query",
        lambda *args, **kwargs: SimpleNamespace(
            answer="answer",
            confidence=SimpleNamespace(value="medium"),
            insufficient_evidence=False,
            supporting_points=[],
            counter_points=[],
            sources=[],
        ),
    )

    server.query_news_research("What changed?")

    assert captured["window"]["lookback_days"] == 30


def test_mcp_recommendation_binds_configured_finite_window_and_preserves_generation(
    tmp_path, monkeypatch
) -> None:
    """SELECT INVARIANT: recommendation cannot bypass default recent-news eligibility."""
    defaults_path = tmp_path / "service-defaults.json"
    defaults_path.write_text('{"query_lookback_days": 17}', encoding="utf-8")
    runtime = _runtime(tmp_path)
    runtime = FinancialAgentRuntimeConfig(
        **{
            **runtime.__dict__,
            "service_defaults_path": str(defaults_path),
        }
    )
    captured = {}

    class ScopedReader:
        active_generation = SimpleNamespace(generation_id="gen-active")

    class Reader:
        active_generation = ScopedReader.active_generation

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return ScopedReader()

    server = create_financial_agent_server(runtime)
    monkeypatch.setattr(server, "_make_vector_store", Reader)

    def recommend(target, vector_store, storage, **kwargs):
        captured["reader"] = vector_store
        return SimpleNamespace(
            decision=SimpleNamespace(value="HOLD"),
            confidence=SimpleNamespace(value="low"),
            time_horizon=SimpleNamespace(value="Long-term"),
            insufficient_evidence=True,
            reasoning="thin",
            key_risks=[],
        )

    monkeypatch.setattr("event_collector.financial_agent_mcp.recommend_target", recommend)

    payload = server.recommend_stock("MSFT")

    assert captured["window"]["start_at"] is None
    assert captured["window"]["end_at"] is None
    assert captured["window"]["lookback_days"] == 17
    latest_at = datetime.fromisoformat(captured["window"]["latest_at"])
    assert latest_at.utcoffset() is not None
    assert captured["reader"].active_generation.generation_id == "gen-active"
    assert payload["generation_id"] == "gen-active"


def test_mcp_watchlist_binds_one_default_window_before_handing_reader_to_workers(
    tmp_path, monkeypatch
) -> None:
    """SELECT INVARIANT: every watchlist worker shares one recent, generation-pinned reader."""
    defaults_path = tmp_path / "service-defaults.json"
    defaults_path.write_text('{"query_lookback_days": 19}', encoding="utf-8")
    runtime = _runtime(tmp_path)
    runtime = FinancialAgentRuntimeConfig(
        **{
            **runtime.__dict__,
            "service_defaults_path": str(defaults_path),
        }
    )
    captured = {}

    class ScopedReader:
        active_generation = SimpleNamespace(generation_id="gen-active")

    class Reader:
        active_generation = ScopedReader.active_generation

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return ScopedReader()

    server = create_financial_agent_server(runtime)
    monkeypatch.setattr(server, "_make_vector_store", Reader)

    def run_workflow(request, storage, vector_store_provider, **kwargs):
        captured["first"] = vector_store_provider()
        captured["second"] = vector_store_provider()
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="run-scoped",
                top_n=request.top_n,
                ranked_items=[],
                retrieval_failures=[],
            ),
            report_path=None,
            timeline_path=None,
            timing_summary={},
            timing_text="",
            batching={},
        )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_watchlist_triage_workflow",
        run_workflow,
    )

    payload = server.run_watchlist_triage(["MSFT"])

    assert captured["window"]["lookback_days"] == 19
    assert datetime.fromisoformat(captured["window"]["latest_at"]).utcoffset() is not None
    assert captured["first"] is captured["second"]
    assert captured["first"].active_generation.generation_id == "gen-active"
    assert payload["generation_id"] == "gen-active"


def test_refresh_watchlist_binds_default_window_after_refresh_and_pins_successor(
    tmp_path, monkeypatch
) -> None:
    """SELECT INVARIANT: post-refresh triage scopes and pins the reader resolved after refresh."""
    defaults_path = tmp_path / "service-defaults.json"
    defaults_path.write_text('{"query_lookback_days": 23}', encoding="utf-8")
    runtime = _runtime(tmp_path)
    runtime = FinancialAgentRuntimeConfig(
        **{
            **runtime.__dict__,
            "service_defaults_path": str(defaults_path),
        }
    )
    captured = {}

    class ScopedReader:
        active_generation = SimpleNamespace(generation_id="gen-successor")

    class Reader:
        active_generation = ScopedReader.active_generation

        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return ScopedReader()

    server = create_financial_agent_server(runtime)
    monkeypatch.setattr(server, "_make_vector_store", Reader)

    def run_workflow(request, storage, vector_store_provider, **kwargs):
        captured["first"] = vector_store_provider()
        captured["second"] = vector_store_provider()
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="run-refreshed",
                top_n=request.top_n,
                ranked_items=[],
                retrieval_failures=[],
            ),
            refresh={"status": "completed"},
            report_path=None,
            timeline_path=None,
            timing_summary={},
            timing_text="",
            batching={},
        )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_refresh_then_watchlist_workflow",
        run_workflow,
    )

    payload = server.run_watchlist_workflow(["MSFT"])

    assert captured["window"]["lookback_days"] == 23
    assert datetime.fromisoformat(captured["window"]["latest_at"]).utcoffset() is not None
    assert captured["first"] is captured["second"]
    assert captured["first"].active_generation.generation_id == "gen-successor"
    assert payload["generation_id"] == "gen-successor"


def test_watchlist_request_pins_one_reader_before_worker_provider_calls(
    tmp_path, monkeypatch
) -> None:
    server = create_financial_agent_server(_runtime(tmp_path))
    reader = SimpleNamespace()
    reader.with_time_window = lambda **kwargs: reader
    make_calls = []
    observed = {}

    def make_reader():
        make_calls.append("make")
        return reader

    def run_workflow(request, storage, vector_store_provider, **kwargs):
        observed["first"] = vector_store_provider()
        observed["second"] = vector_store_provider()
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="run-1",
                top_n=request.top_n,
                ranked_items=[],
                retrieval_failures=[],
            ),
            report_path=None,
            timeline_path=None,
            timing_summary={},
            timing_text="",
            batching={},
        )

    monkeypatch.setattr(server, "_make_vector_store", make_reader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_watchlist_triage_workflow",
        run_workflow,
    )

    server.run_watchlist_triage(["MSFT"])

    assert make_calls == ["make"]
    assert observed["first"] is observed["second"]


def test_refresh_watchlist_provider_resolves_once_when_triage_starts(
    tmp_path, monkeypatch
) -> None:
    server = create_financial_agent_server(_runtime(tmp_path))
    reader = SimpleNamespace()
    reader.with_time_window = lambda **kwargs: reader
    make_calls = []
    observed = []

    monkeypatch.setattr(
        server,
        "_make_vector_store",
        lambda: make_calls.append("make") or reader,
    )

    def run_workflow(request, storage, vector_store_provider, **kwargs):
        observed.extend([vector_store_provider(), vector_store_provider()])
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="run-2", top_n=request.top_n, ranked_items=[], retrieval_failures=[]
            ),
            refresh={"status": "already_completed"},
            report_path=None,
            timeline_path=None,
            timing_summary={},
            timing_text="",
            batching={},
        )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_refresh_then_watchlist_workflow",
        run_workflow,
    )

    server.run_watchlist_workflow(["MSFT"])

    assert make_calls == ["make"]
    assert observed[0] is observed[1]


def test_request_pinned_provider_serializes_shared_reader_search(
    tmp_path, monkeypatch
) -> None:
    server = create_financial_agent_server(_runtime(tmp_path))
    state_lock = Lock()
    active_calls = 0
    max_active_calls = 0

    class ConcurrentProbeReader:
        def with_time_window(self, **kwargs):
            return self

        def search(self, query, top_k=5, **kwargs):
            nonlocal active_calls, max_active_calls
            with state_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            time.sleep(0.02)
            with state_lock:
                active_calls -= 1
            return []

    monkeypatch.setattr(server, "_make_vector_store", ConcurrentProbeReader)
    provider = server._request_pinned_vector_store_provider()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(provider().search, f"q-{index}") for index in range(5)]
        for future in futures:
            assert future.result() == []

    assert max_active_calls == 1
