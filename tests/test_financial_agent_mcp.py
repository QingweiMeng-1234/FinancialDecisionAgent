from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

from event_collector.entity_kb import CompanyProfile, SQLiteEntityStore
from event_collector.financial_agent_mcp import (
    FinancialAgentCapabilities,
    FinancialAgentMCPServer,
    FinancialAgentRuntimeConfig,
    OpenClawRoutePolicy,
    OpenClawSecurityConfig,
    ToolMetadata,
    create_financial_agent_server,
)
from event_collector.refresh_ledger import RetrySchedule, compute_scope_key


class _TimeWindowPassthroughReader:
    active_generation = SimpleNamespace(
        generation_id="generation-test-1",
        corpus_snapshot_id="snapshot-test-1",
        index_config_fingerprint="b" * 64,
    )

    def with_time_window(self, **kwargs):
        return self
from event_collector.watchlist_progress import WatchlistProgressEvent, utc_now
from event_collector.successor_generation_coordinator import (
    RuntimeSuccessorGenerationCoordinator,
)


def test_create_financial_agent_server_exposes_only_high_level_openclaw_tools_by_default():
    server = create_financial_agent_server()

    assert server.list_tool_names() == [
        "refresh_news",
        "run_watchlist_workflow",
        "read_watchlist_report",
        "read_watchlist_timeline",
    ]


def test_default_capabilities_do_not_require_quantgpt():
    server = create_financial_agent_server()

    assert server.capabilities.enable_quantgpt is False
    assert server.requires_quantgpt("query_news_research") is False
    assert server.requires_quantgpt("recommend_stock") is False
    assert server.requires_quantgpt("run_watchlist_triage") is False


def test_runtime_defaults_point_to_activated_v2_corpus():
    """SELECT INVARIANT: default serving config resolves the activated immutable corpus."""
    config = FinancialAgentRuntimeConfig()

    assert config.db_path == "data/rag_corpus_v2_20260815/news_articles.db"
    assert config.canonical_db_path == config.db_path
    assert config.canonical_content_root == "data/rag_corpus_v2_20260815/data/articles"
    assert config.chroma_persist_dir == "data/rag_index_v2_20260815"


def test_default_mcp_runtime_has_an_executable_successor_generation_coordinator():
    """SELECT INVARIANT: real MCP refresh promotes its fully verified successor by default."""
    server = create_financial_agent_server()

    assert isinstance(
        server.successor_generation_coordinator,
        RuntimeSuccessorGenerationCoordinator,
    )
    assert server.successor_generation_coordinator._config.activate_verified_generation is True


def test_only_refresh_news_is_marked_as_mutating_tool():
    server = create_financial_agent_server()

    mutating = [
        metadata.name
        for metadata in server.list_tools()
        if metadata.mutates_state
    ]

    assert mutating == ["refresh_news", "run_watchlist_workflow"]


def test_refresh_news_fails_closed_without_verified_generation_proof(tmp_path, monkeypatch):
    calls: list[str] = []
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            refresh_state_path=str(tmp_path / "refresh_state.json"),
            reports_dir=str(tmp_path / "reports"),
        )
    )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_news_pipeline",
        lambda request: calls.append(request.db_path) or SimpleNamespace(
            collected_events=4,
            stats={"saved": 2, "indexed": 2},
            total_articles=8,
            answer_text=None,
        ),
    )

    first = server.call_tool("refresh_news", today="2026-06-02")
    second = server.call_tool("refresh_news", today="2026-06-02")
    third = server.call_tool("refresh_news", today="2026-06-03")

    assert first["status"] == "failed"
    assert first["refresh_date"] == "2026-06-02"
    assert first["counts"]["usable_items"] == 0
    assert second["status"] == "failed"
    assert second["failure_code"] == "retry_exhausted"
    assert second["disposition"] == "retry_exhausted"
    assert third["status"] == "failed"
    assert calls == [
        "data/rag_corpus_v2_20260815/news_articles.db",
        "data/rag_corpus_v2_20260815/news_articles.db",
    ]
    assert not (tmp_path / "refresh_state.json").exists()
    assert (tmp_path / "refresh_state.sqlite3").exists()


def test_refresh_news_threads_runtime_corpus_and_successor_coordinator(tmp_path, monkeypatch):
    """SELECT INVARIANT: MCP exposes the verified-generation build seam explicitly."""
    captured = {}
    coordinator = object()
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            index_corpus_id="news-canonical",
            refresh_state_path=str(tmp_path / "refresh_state.json"),
        ),
        successor_generation_coordinator=coordinator,
    )

    def fake_refresh(request, **kwargs):
        captured["request"] = request
        captured["coordinator"] = kwargs.get("successor_generation_coordinator")
        return {"status": "failed", "failure_code": "generation_rebuild_required"}

    monkeypatch.setattr("event_collector.financial_agent_mcp.refresh_news_corpus", fake_refresh)

    payload = server.call_tool("refresh_news", today="2026-08-15")

    assert payload["failure_code"] == "generation_rebuild_required"
    assert captured["request"].index_corpus_id == "news-canonical"
    assert captured["coordinator"] is coordinator


def test_scheduled_retry_reconstructs_frozen_scope_and_uses_default_provider_adapters(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: automatic dispatch re-enters the exact child scope, never a new root."""
    captured = {}
    coordinator = object()
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            refresh_state_path=str(tmp_path / "refresh.json"),
            collection_name="news-v2",
            index_corpus_id="news",
        ),
        successor_generation_coordinator=coordinator,
    )

    def fake_refresh(request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return {"status": "completed", "run_id": "child-2"}

    monkeypatch.setattr("event_collector.financial_agent_mcp.refresh_news_corpus", fake_refresh)
    snapshot = {
        "requested_date": "2026-08-15",
        "news_endpoint": "everything",
        "news_days_back": 3,
        "news_page": 2,
        "news_sort_by": "publishedAt",
        "news_page_size": 25,
        "include_manual": False,
        "corpus_collection": "news-v2",
        "index_corpus_id": "news",
        "ingestion_contract_version": "refresh-generation-proof-v5",
    }
    schedule = RetrySchedule(
        scope_key=compute_scope_key(snapshot),
        requested_date="2026-08-15",
        due_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        config_snapshot=snapshot,
    )

    result = server._run_scheduled_retry(schedule)

    assert result == {"status": "completed", "run_id": "child-2"}
    assert captured["request"].today == "2026-08-15"
    assert captured["request"].news_days_back == 3
    assert captured["request"].news_page == 2
    assert captured["request"].news_page_size == 25
    assert captured["kwargs"] == {
        "run_news_pipeline_fn": server._run_news_pipeline,
        "successor_generation_coordinator": coordinator,
    }


def test_mcp_run_owns_one_automatic_retry_scheduler_lifecycle(tmp_path):
    """SELECT INVARIANT: production serving starts and stops its retry poller with MCP."""
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(refresh_state_path=str(tmp_path / "refresh.json"))
    )
    calls = []

    class Scheduler:
        def start(self):
            calls.append("scheduler-start")

        def stop(self):
            calls.append("scheduler-stop")

    class Runtime:
        def run(self, transport="stdio"):
            calls.append(("mcp-run", transport))

    server.retry_scheduler = Scheduler()
    server._mcp = Runtime()

    server.run(transport="streamable-http")

    assert calls == [
        "scheduler-start",
        ("mcp-run", "streamable-http"),
        "scheduler-stop",
    ]


def test_research_tools_return_structured_results_and_optional_report_paths(tmp_path, monkeypatch):
    captured = {}
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            reports_dir=str(tmp_path / "reports"),
            refresh_state_path=str(tmp_path / "refresh_state.json"),
        )
    )
    monkeypatch.setattr(server, "_make_vector_store", _TimeWindowPassthroughReader)

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.answer_query",
        lambda question, vector_store, top_k=3, retrieval_top_k=5, retrieval_intent="direct": SimpleNamespace(
            answer="Demand looks stronger than feared.",
            confidence=SimpleNamespace(value="medium"),
            insufficient_evidence=False,
            supporting_points=[SimpleNamespace(text="Cloud backlog improved.", citations=[1])],
            counter_points=[],
            sources=[SimpleNamespace(id=1, title="MSFT demand", url="https://example.com/msft", snippet="Azure demand improved.")],
            rerank_metadata=None,
        ),
    )
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.recommend_target",
        lambda target, vector_store, storage, top_k=3, retrieval_top_k=5, retrieval_intent="direct": SimpleNamespace(
            decision=SimpleNamespace(value="HOLD"),
            confidence=SimpleNamespace(value="low"),
            time_horizon=SimpleNamespace(value="Long-term"),
            insufficient_evidence=True,
            reasoning="News is mixed.",
            key_risks=["Signal quality is thin."],
            aggregation=None,
            sources=[],
            rerank_metadata=None,
        ),
    )
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.write_recommendation_report",
        lambda target, response, output_dir, debug_rerank=False, debug_aggregation=False, **kwargs: captured.setdefault(
            "recommendation_provenance", kwargs["retrieval_provenance"]
        ) and os.path.join(output_dir, "recommendation.md"),
    )
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_watchlist_triage_workflow",
        lambda request, storage, vector_store_provider, output_dir=None, **kwargs: SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch-1",
                ranked_items=[
                    SimpleNamespace(
                        ticker="MSFT",
                        priority=SimpleNamespace(value="High"),
                        confidence=SimpleNamespace(value="High"),
                        rank=1,
                        should_flag_human_review=False,
                    )
                ],
                retrieval_failures=[],
                top_n=request.top_n,
            ),
            batching={"enabled": False, "batch_count": 1, "batch_size": len(request.tickers), "input_ticker_count": len(request.tickers), "report_supported": True},
            report_path=os.path.join(output_dir, "watchlist.md"),
            timeline_path=None,
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nTotal tracked workflow time: 0ms",
        ),
    )

    research = server.call_tool("query_news_research", question="What changed for MSFT?")
    recommendation = server.call_tool("recommend_stock", target="MSFT", include_report=True)
    triage = server.call_tool("run_watchlist_triage", tickers=["MSFT", "NVDA"], include_report=True)

    assert research["answer"] == "Demand looks stronger than feared."
    assert research["sources"][0]["title"] == "MSFT demand"
    assert recommendation["decision"] == "HOLD"
    assert recommendation["report_path"].endswith("recommendation.md")
    assert captured["recommendation_provenance"] == {
        "generation_id": "generation-test-1",
        "corpus_snapshot_id": "snapshot-test-1",
        "index_config_fingerprint": "b" * 64,
        "corpus_id": None,
        "collection_name": None,
        "embedding_artifact": None,
    }
    assert triage["run_id"] == "watch-1"
    assert triage["ranked_items"][0]["ticker"] == "MSFT"
    assert triage["report_path"].endswith("watchlist.md")
    assert triage["timing_text"].startswith("Timing Summary:")


def test_read_watchlist_report_returns_saved_markdown_content(tmp_path):
    reports_dir = tmp_path / "reports"
    report_path = reports_dir / "watchlist_triage" / "watch-1.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Watchlist Triage Report\n", encoding="utf-8")

    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(reports_dir),
        )
    )
    storage = server._make_news_store()
    storage.init_db()
    storage.conn.execute(
        """
        INSERT INTO watchlist_runs
        (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
         reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "watch-1",
            "2026-06-05T00:00:00",
            2,
            1,
            5,
            "triage",
            "reviewer",
            "triage-v1",
            "reviewer-v1",
            str(report_path),
            "completed",
        ),
    )
    storage.conn.commit()
    storage.close()

    payload = server.call_tool("read_watchlist_report", run_id="watch-1")

    assert payload == {
        "run_id": "watch-1",
        "found": True,
        "report_path": str(report_path),
        "content": "# Watchlist Triage Report\n",
        "error_code": None,
        "error_message": None,
    }


def test_read_watchlist_timeline_returns_saved_latency_summary(tmp_path):
    reports_dir = tmp_path / "reports"
    timeline_path = reports_dir / "watchlist_triage" / "watch-1.timeline.jsonl"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(
        "\n".join(
            [
                '{"scope":"workflow","stage":"retrieval","status":"finished","ticker":null,"started_at":"2026-07-01T11:00:00Z","finished_at":"2026-07-01T11:00:10Z","duration_ms":10000,"message":null,"metrics":{}}',
                '{"scope":"ticker","stage":"triage","status":"finished","ticker":"MSFT","started_at":"2026-07-01T11:00:10Z","finished_at":"2026-07-01T11:00:12Z","duration_ms":2000,"message":null,"metrics":{}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(reports_dir),
        )
    )

    payload = server.call_tool("read_watchlist_timeline", run_id="watch-1")

    assert payload["found"] is True
    assert payload["timeline_path"] == str(timeline_path.resolve())
    assert payload["timing_summary"]["total_duration_ms"] == 10000
    assert payload["timing_summary"]["slowest_workflow_stage"]["stage"] == "retrieval"
    assert "Slowest workflow stage: retrieval (10.0s, status=finished)" in payload["timing_text"]
    assert payload["events"][1]["ticker"] == "MSFT"


def test_read_watchlist_report_returns_not_found_when_run_has_no_saved_report_path(tmp_path):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(tmp_path / "reports"),
        )
    )
    storage = server._make_news_store()
    storage.init_db()
    storage.conn.execute(
        """
        INSERT INTO watchlist_runs
        (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
         reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "watch-2",
            "2026-06-05T00:00:00",
            2,
            1,
            5,
            "triage",
            "reviewer",
            "triage-v1",
            "reviewer-v1",
            None,
            "completed",
        ),
    )
    storage.conn.commit()
    storage.close()

    payload = server.call_tool("read_watchlist_report", run_id="watch-2")

    assert payload["found"] is False
    assert payload["report_path"] is None
    assert payload["content"] is None
    assert payload["error_code"] == "report_not_found"


def test_read_watchlist_report_returns_file_missing_when_saved_path_is_gone(tmp_path):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(tmp_path / "reports"),
        )
    )
    missing_path = tmp_path / "reports" / "watchlist_triage" / "missing.md"
    storage = server._make_news_store()
    storage.init_db()
    storage.conn.execute(
        """
        INSERT INTO watchlist_runs
        (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
         reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "watch-3",
            "2026-06-05T00:00:00",
            2,
            1,
            5,
            "triage",
            "reviewer",
            "triage-v1",
            "reviewer-v1",
            str(missing_path),
            "completed",
        ),
    )
    storage.conn.commit()
    storage.close()

    payload = server.call_tool("read_watchlist_report", run_id="watch-3")

    assert payload["found"] is False
    assert payload["report_path"] == str(missing_path)
    assert payload["content"] is None
    assert payload["error_code"] == "report_file_missing"


def test_read_watchlist_report_returns_not_found_for_unknown_run_id(tmp_path):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(tmp_path / "reports"),
        )
    )

    payload = server.call_tool("read_watchlist_report", run_id="missing-run")

    assert payload["found"] is False
    assert payload["report_path"] is None
    assert payload["content"] is None
    assert payload["error_code"] == "report_not_found"


def test_read_watchlist_report_rejects_paths_outside_watchlist_reports_root(tmp_path):
    reports_dir = tmp_path / "reports"
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("secret", encoding="utf-8")
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(reports_dir),
        )
    )
    storage = server._make_news_store()
    storage.init_db()
    storage.conn.execute(
        """
        INSERT INTO watchlist_runs
        (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
         reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "watch-4",
            "2026-06-05T00:00:00",
            2,
            1,
            5,
            "triage",
            "reviewer",
            "triage-v1",
            "reviewer-v1",
            str(outside_path),
            "completed",
        ),
    )
    storage.conn.commit()
    storage.close()

    payload = server.call_tool("read_watchlist_report", run_id="watch-4")

    assert payload["found"] is False
    assert payload["content"] is None
    assert payload["error_code"] == "report_not_found"


def test_watchlist_workflow_refreshes_then_runs_triage(tmp_path, monkeypatch):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            reports_dir=str(tmp_path / "reports"),
            refresh_state_path=str(tmp_path / "refresh_state.json"),
        )
    )
    call_order: list[str] = []

    def fake_run_refresh_then_watchlist_workflow(
        request,
        storage,
        vector_store_provider,
        refresh_request,
        output_dir=None,
        timeline_recorder=None,
        **kwargs,
    ):
        call_order.extend(["refresh", "triage"])
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch-3",
                ranked_items=[],
                retrieval_failures=[],
                top_n=request.top_n,
            ),
            batching={"enabled": False, "batch_count": 1, "batch_size": 2, "input_ticker_count": 2, "report_supported": True},
            report_path=os.path.join(output_dir, "watch-3.md"),
            timeline_path=None,
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 5, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nWorkflow stages:",
            refresh={"status": "refreshed", "refresh_date": "2026-06-03"},
        )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_refresh_then_watchlist_workflow",
        fake_run_refresh_then_watchlist_workflow,
    )

    payload = server.call_tool("run_watchlist_workflow", tickers=["MSFT", "NVDA"], include_report=True)

    assert call_order == ["refresh", "triage"]
    assert payload["workflow"] == "watchlist_refresh_then_triage"
    assert payload["refresh"]["status"] == "refreshed"
    assert payload["triage"]["run_id"] == "watch-3"
    assert payload["timing_text"].startswith("Timing Summary:")


def test_watchlist_workflow_prints_progress_and_writes_timeline_artifact(tmp_path, monkeypatch, capsys):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            reports_dir=str(tmp_path / "reports"),
            refresh_state_path=str(tmp_path / "refresh_state.json"),
        )
    )

    def fake_run_refresh_then_watchlist_workflow(
        request,
        storage,
        vector_store_provider,
        refresh_request,
        output_dir=None,
        timeline_recorder=None,
        **kwargs,
    ):
        refresh_started = utc_now()
        ticker_started = utc_now()
        if timeline_recorder is not None:
            timeline_recorder.emit(
                WatchlistProgressEvent(
                    scope="workflow",
                    stage="refresh_news",
                    status="started",
                    started_at=refresh_started,
                )
            )
            timeline_recorder.emit(
                WatchlistProgressEvent(
                    scope="workflow",
                    stage="refresh_news",
                    status="finished",
                    started_at=refresh_started,
                    finished_at=utc_now(),
                    duration_ms=5,
                )
            )
            timeline_recorder.emit(
                WatchlistProgressEvent(
                    scope="ticker",
                    stage="triage",
                    status="started",
                    ticker="MSFT",
                    started_at=ticker_started,
                )
            )
            timeline_recorder.emit(
                WatchlistProgressEvent(
                    scope="ticker",
                    stage="triage",
                    status="finished",
                    ticker="MSFT",
                    started_at=ticker_started,
                    finished_at=utc_now(),
                    duration_ms=12,
                )
            )
        timeline_path = os.path.join(output_dir, "watch-progress.timeline.jsonl")
        timeline_recorder.write_jsonl(timeline_path)
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch-progress",
                ranked_items=[],
                retrieval_failures=[],
                top_n=request.top_n,
            ),
            batching={"enabled": False, "batch_count": 1, "batch_size": 1, "input_ticker_count": 1, "report_supported": True},
            report_path=os.path.join(output_dir, "watch-progress.md"),
            timeline_path=timeline_path,
            timing_summary={"workflow_stages": [], "ticker_stages": {"MSFT": []}, "total_duration_ms": 17, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nTicker stages:",
            refresh={"status": "refreshed", "refresh_date": "2026-06-03"},
        )

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_refresh_then_watchlist_workflow",
        fake_run_refresh_then_watchlist_workflow,
    )

    payload = server.call_tool("run_watchlist_workflow", tickers=["MSFT"])

    captured = capsys.readouterr()
    assert "[watchlist] stage=refresh_news status=started" in captured.out
    assert "[watchlist] ticker=MSFT stage=triage status=finished duration_ms=12" in captured.out
    assert payload["timeline_path"].endswith("watch-progress.timeline.jsonl")
    assert "Ticker stages:" in payload["timing_text"]
    assert os.path.exists(payload["timeline_path"])
    timeline_lines = open(payload["timeline_path"], encoding="utf-8").read().splitlines()
    assert any('"stage": "refresh_news"' in line for line in timeline_lines)
    assert any('"ticker": "MSFT"' in line for line in timeline_lines)


def test_watchlist_triage_always_writes_report_even_without_include_report(tmp_path, monkeypatch):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            db_path=str(tmp_path / "news.db"),
            reports_dir=str(tmp_path / "reports"),
        )
    )
    monkeypatch.setattr(server, "_make_vector_store", _TimeWindowPassthroughReader)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_watchlist_triage_workflow",
        lambda request, storage, vector_store_provider, output_dir=None, **kwargs: SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch-auto",
                ranked_items=[],
                retrieval_failures=[],
                top_n=request.top_n,
            ),
            batching={"enabled": False, "batch_count": 1, "batch_size": 1, "input_ticker_count": 1, "report_supported": True},
            report_path=os.path.join(output_dir, "watch-auto.md"),
            timeline_path=None,
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nTotal tracked workflow time: 0ms",
        ),
    )

    payload = server.call_tool("run_watchlist_triage", tickers=["MSFT"], include_report=False)

    assert payload["report_path"].endswith("watch-auto.md")


def test_get_company_profile_and_retrieve_supporting_articles_use_existing_data(tmp_path, monkeypatch):
    entity_db = tmp_path / "company_entities.db"
    store = SQLiteEntityStore(db_path=str(entity_db))
    store.init_db()
    store.upsert_company_profile(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            ceo_name="Satya Nadella",
            aliases=("Microsoft Corp",),
            products=("Azure", "GitHub Copilot"),
            business_lines=("cloud infrastructure",),
            themes=("enterprise software",),
        )
    )
    store.close()

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.retrieve_evidence_bundle",
        lambda query, vector_store, top_k, retrieval_top_k=5, company_kb=None, retrieval_intent="direct": SimpleNamespace(
            resolved_company=None,
            evidence=[
                SimpleNamespace(
                    id=1,
                    article_id=101,
                    title="Azure demand",
                    url="https://example.com/1",
                    summary="Azure demand accelerated.",
                    excerpt="Azure demand accelerated among enterprise customers.",
                    snippet="Azure demand accelerated.",
                    published_at="2026-06-02T08:00:00",
                    rerank_position=1,
                    retrieval_intent="direct",
                    company_id="company:MSFT",
                    expanded_query="MSFT Microsoft Azure",
                    normalized_vector_score=0.9,
                    kb_boost=0.2,
                    final_score=1.1,
                    kb_match_count=2,
                    matches_business_line=False,
                    matches_theme=False,
                    indirect_kb_boost=0.0,
                    indirect_match_types=(),
                    attribution_match_types=("product",),
                    attribution_matched_aliases=("Azure",),
                ),
                SimpleNamespace(
                    id=2,
                    article_id=102,
                    title="Copilot adoption",
                    url="https://example.com/2",
                    summary=None,
                    excerpt="GitHub Copilot seats grew.",
                    snippet="GitHub Copilot seats grew.",
                    published_at="2026-06-02T09:00:00",
                    rerank_position=2,
                    retrieval_intent="direct",
                    company_id="company:MSFT",
                    expanded_query="MSFT Microsoft Azure",
                    normalized_vector_score=0.8,
                    kb_boost=0.1,
                    final_score=0.9,
                    kb_match_count=1,
                    matches_business_line=False,
                    matches_theme=False,
                    indirect_kb_boost=0.0,
                    indirect_match_types=(),
                    attribution_match_types=("product",),
                    attribution_matched_aliases=("GitHub Copilot",),
                ),
            ],
            rerank_metadata=None,
        ),
    )

    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(entity_db_path=str(entity_db))
    )
    monkeypatch.setattr(server, "_make_vector_store", _TimeWindowPassthroughReader)
    profile = server.call_tool("get_company_profile", ticker="MSFT")
    articles = server.call_tool("retrieve_supporting_articles", query="MSFT", top_k=2)

    assert profile["ticker"] == "MSFT"
    assert profile["canonical_name"] == "Microsoft"
    assert profile["products"] == ["Azure", "GitHub Copilot"]
    assert articles["articles"][0]["article_id"] == 101
    assert articles["articles"][1]["article_id"] == 102


def test_security_defaults_only_allow_explicit_tool_allowlist():
    config = OpenClawSecurityConfig.default_for_mvp()

    assert config.host_exec_enabled is False
    assert config.native_plugins_enabled is False
    assert config.allow_yolo_mode is False
    assert config.allowed_mcp_tools == [
        "refresh_news",
        "run_watchlist_workflow",
        "read_watchlist_report",
        "read_watchlist_timeline",
    ]


def test_model_policy_prefers_flash_and_uses_pro_for_critical_nodes():
    server = create_financial_agent_server()

    assert server.select_model(tool_name="query_news_research") == "deepseek-v4-flash"
    assert server.select_model(tool_name="recommend_stock", critical=True) == "deepseek-v4-pro"
    assert server.select_model(tool_name="run_watchlist_triage", full_deepseek=True) == "deepseek-v4-pro"


def test_http_endpoint_url_uses_runtime_host_port_and_optional_override():
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(http_host="127.0.0.1", http_port=9900)
    )

    assert server.http_endpoint_url() == "http://127.0.0.1:9900/mcp"
    assert server.http_endpoint_url(hostname="host.docker.internal") == "http://host.docker.internal:9900/mcp"


def test_http_transport_security_allows_localhost_and_docker_host(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTransportSecuritySettings:
        def __init__(self, *, enable_dns_rebinding_protection=True, allowed_hosts=None, allowed_origins=None):
            captured["enable_dns_rebinding_protection"] = enable_dns_rebinding_protection
            captured["allowed_hosts"] = list(allowed_hosts or [])
            captured["allowed_origins"] = list(allowed_origins or [])

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs
            captured["registered_tools"] = []

        def tool(self, name=None):
            def decorator(func):
                captured["registered_tools"].append(name or func.__name__)
                return func

            return decorator

    monkeypatch.setattr("event_collector.financial_agent_mcp._FastMCP", FakeFastMCP)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp._TransportSecuritySettings",
        FakeTransportSecuritySettings,
    )

    create_financial_agent_server(
        FinancialAgentRuntimeConfig(http_host="127.0.0.1", http_port=8877)
    )

    assert captured["name"] == "financial-agent"
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8877
    assert captured["kwargs"]["streamable_http_path"] == "/mcp"
    assert captured["enable_dns_rebinding_protection"] is True
    assert captured["allowed_hosts"] == [
        "127.0.0.1:*",
        "localhost:*",
        "host.docker.internal:*",
    ]
    assert captured["registered_tools"] == [
        "refresh_news",
        "run_watchlist_workflow",
        "read_watchlist_report",
        "read_watchlist_timeline",
    ]


def test_injected_theme_lifecycle_tools_register_only_through_explicit_allowlist(
    monkeypatch,
):
    """SELECT INVARIANT: MCP exposes injected lifecycle handlers, never providers."""
    captured = []
    lifecycle_names = [
        "theme_chokepoint_start",
        "theme_chokepoint_get_run",
        "theme_chokepoint_get_pending_anchors",
        "theme_chokepoint_confirm_anchors",
        "theme_chokepoint_continue",
        "theme_chokepoint_get_artifacts",
    ]

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            pass

        def tool(self, name=None):
            def decorator(func):
                captured.append(name)
                return func
            return decorator

    class FakeInterface:
        def tool_handlers(self):
            return {name: (lambda **kwargs: kwargs) for name in lifecycle_names}

    monkeypatch.setattr("event_collector.financial_agent_mcp._FastMCP", FakeFastMCP)
    monkeypatch.setattr(
        "event_collector.financial_agent_mcp._TransportSecuritySettings", None
    )
    security = OpenClawSecurityConfig(
        host_exec_enabled=False,
        native_plugins_enabled=False,
        allow_yolo_mode=False,
        allowed_mcp_tools=lifecycle_names,
    )

    server = FinancialAgentMCPServer(
        runtime_config=FinancialAgentRuntimeConfig(
            refresh_retry_scheduler_enabled=False
        ),
        security_config=security,
        theme_chokepoint_interface=FakeInterface(),
    )

    assert server.list_tool_names() == lifecycle_names
    assert captured == lifecycle_names


def test_small_parameter_surface_uses_shared_defaults(tmp_path, monkeypatch):
    defaults_path = tmp_path / "service_defaults.json"
    defaults_path.write_text(
        '{"query_top_k": 4, "retrieval_top_k": 6, "recommendation_top_k": 2, "watchlist_top_n": 1, "watchlist_retrieval_top_k": 8}',
        encoding="utf-8",
    )
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            service_defaults_path=str(defaults_path),
            reports_dir=str(tmp_path / "reports"),
        )
    )
    monkeypatch.setattr(server, "_make_vector_store", _TimeWindowPassthroughReader)
    captured: dict[str, object] = {}

    def fake_answer_query(question, vector_store, top_k=3, retrieval_top_k=5, retrieval_intent="direct"):
        captured["query"] = (question, top_k, retrieval_top_k, retrieval_intent)
        return SimpleNamespace(
            answer="ok",
            confidence=SimpleNamespace(value="low"),
            insufficient_evidence=False,
            supporting_points=[],
            counter_points=[],
            sources=[],
            rerank_metadata=None,
        )

    def fake_recommend_target(target, vector_store, storage, top_k=3, retrieval_top_k=5, retrieval_intent="direct"):
        captured["recommend"] = (target, top_k, retrieval_top_k, retrieval_intent)
        return SimpleNamespace(
            decision=SimpleNamespace(value="HOLD"),
            confidence=SimpleNamespace(value="low"),
            time_horizon=SimpleNamespace(value="Long-term"),
            insufficient_evidence=True,
            reasoning="thin",
            key_risks=[],
            aggregation=None,
            sources=[],
            rerank_metadata=None,
        )

    def fake_run_watchlist_triage_workflow(request, storage, vector_store_provider, config=None, output_dir=None, **kwargs):
        captured["watchlist"] = (request.top_n, request.retrieval_top_k, config.max_concurrency)
        return SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch",
                ranked_items=[],
                retrieval_failures=[],
                top_n=request.top_n,
            ),
            batching={"enabled": False, "batch_count": 1, "batch_size": len(request.tickers), "input_ticker_count": len(request.tickers), "report_supported": True},
            report_path=os.path.join(output_dir, "watchlist.md"),
            timeline_path=None,
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nTotal tracked workflow time: 0ms",
        )

    monkeypatch.setattr("event_collector.financial_agent_mcp.answer_query", fake_answer_query)
    monkeypatch.setattr("event_collector.financial_agent_mcp.recommend_target", fake_recommend_target)
    monkeypatch.setattr("event_collector.financial_agent_mcp.run_watchlist_triage_workflow", fake_run_watchlist_triage_workflow)

    server.call_tool("query_news_research", question="MSFT?")
    server.call_tool("recommend_stock", target="MSFT")
    payload = server.call_tool("run_watchlist_triage", tickers=["MSFT"])

    assert captured["query"] == ("MSFT?", 4, 6, "direct")
    assert captured["recommend"] == ("MSFT", 2, 6, "direct")
    assert captured["watchlist"] == (1, 8, 5)
    assert payload["report_path"].endswith("watchlist.md")


def test_watchlist_triage_delegates_batching_policy_to_watchlist_research(tmp_path, monkeypatch):
    server = create_financial_agent_server(
        FinancialAgentRuntimeConfig(
            reports_dir=str(tmp_path / "reports"),
            watchlist_auto_batch_threshold=3,
            watchlist_auto_batch_size=2,
            watchlist_retrieval_max_concurrency=5,
        )
    )
    monkeypatch.setattr(server, "_make_vector_store", _TimeWindowPassthroughReader)

    monkeypatch.setattr(
        "event_collector.financial_agent_mcp.run_watchlist_triage_workflow",
        lambda request, storage, vector_store_provider, config=None, output_dir=None, **kwargs: SimpleNamespace(
            result=SimpleNamespace(
                run_id="watch-2",
                ranked_items=[],
                retrieval_failures=[SimpleNamespace(ticker="NVDA", status="timeout", error_message="slow")],
                top_n=request.top_n,
            ),
            batching={
                "enabled": True,
                "batch_count": 2,
                "batch_size": 2,
                "input_ticker_count": 4,
                "report_supported": True,
                "max_concurrency": config.max_concurrency,
            },
            report_path=os.path.join(output_dir, "watchlist.md"),
            timeline_path=None,
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:\nTotal tracked workflow time: 0ms",
        ),
    )

    payload = server.call_tool("run_watchlist_triage", tickers=["MSFT", "NVDA", "AAPL", "TSLA"])

    assert payload["batching"] == {
        "enabled": True,
        "batch_count": 2,
        "batch_size": 2,
        "input_ticker_count": 4,
        "report_supported": True,
        "max_concurrency": 5,
    }
    assert payload["retrieval_failures"] == [
        {"ticker": "NVDA", "status": "timeout", "error_message": "slow"}
    ]


def test_route_policy_keeps_default_research_on_financial_agent_only():
    policy = OpenClawRoutePolicy.default_for_mvp()

    assert policy.required_servers_for_task("stock_research") == ["financial-agent"]
    assert policy.required_servers_for_task("watchlist_triage") == ["financial-agent"]
    assert policy.required_servers_for_task("quant_validation") == ["financial-agent", "quantgpt"]
