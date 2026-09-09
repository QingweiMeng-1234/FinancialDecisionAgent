"""MVP MCP surface for the core financial-agent workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
from typing import Any, Callable

from event_collector.entity_kb import SQLiteEntityStore
from event_collector.corpus_retrieval import create_corpus_vector_store
from event_collector.news_storage import SQLiteNewsStore
from event_collector.rag_answering import answer_query
from event_collector.recommendation import recommend_target, write_recommendation_report
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT
from event_collector.retrieval_orchestration import retrieve_evidence_bundle
from event_collector.service_defaults import (
    DEFAULT_SERVICE_DEFAULTS_PATH,
    ServiceDefaults,
    load_service_defaults,
)
from event_collector.vector_store import ChromaVectorStore
from event_collector.watchlist_domain import WatchlistRunRequest
from event_collector.watchlist_progress import (
    WatchlistProgressEvent,
    WatchlistProgressSink,
    WatchlistTimelineRecorder,
    render_watchlist_progress_line,
)
from event_collector.watchlist_research import WatchlistResearchConfig
from event_collector.watchlist_workflow import (
    DEFAULT_REPORTS_DIR as DEFAULT_WATCHLIST_REPORTS_DIR,
    DEFAULT_TIMELINE_SUFFIX,
    RefreshNewsRequest,
    WatchlistWorkflowResult,
    read_watchlist_report_artifact,
    read_watchlist_timeline_artifact,
    refresh_news_corpus,
    run_refresh_then_watchlist_workflow,
    run_watchlist_triage_workflow,
)

try:  # pragma: no cover - exercised only when FastMCP is installed locally
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.server.transport_security import TransportSecuritySettings as _TransportSecuritySettings
except Exception:  # pragma: no cover - the test environment does not ship MCP
    _FastMCP = None
    _TransportSecuritySettings = None


DEFAULT_TOOL_NAMES = [
    "refresh_news",
    "run_watchlist_workflow",
    "read_watchlist_report",
    "read_watchlist_timeline",
]


def run_news_pipeline(request):
    from event_collector.news_pipeline import run_news_pipeline as _run_news_pipeline

    return _run_news_pipeline(request)


def build_news_pipeline_request(**kwargs):
    from event_collector.news_pipeline import NewsPipelineRequest

    return NewsPipelineRequest(**kwargs)


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    handler: Callable[..., dict[str, Any]]
    description: str
    mutates_state: bool = False
    requires_quantgpt: bool = False
    critical_by_default: bool = False


@dataclass(frozen=True)
class FinancialAgentCapabilities:
    enable_quantgpt: bool = False
    enable_host_exec: bool = False
    enable_native_plugins: bool = False


@dataclass(frozen=True)
class FinancialAgentRuntimeConfig:
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"
    entity_db_path: str = "company_entities.db"
    reports_dir: str = "reports"
    recommendation_reports_subdir: str = "recommendations"
    watchlist_reports_subdir: str = "watchlist_triage"
    watchlist_timeline_suffix: str = ".timeline.jsonl"
    refresh_state_path: str = os.path.join("data", "runtime", "refresh_news_state.json")
    service_defaults_path: str = DEFAULT_SERVICE_DEFAULTS_PATH
    http_host: str = "127.0.0.1"
    http_port: int = 8877
    streamable_http_path: str = "/mcp"
    http_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "host.docker.internal:*",
    )
    watchlist_auto_batch_threshold: int = 12
    watchlist_auto_batch_size: int = 8
    watchlist_retrieval_max_concurrency: int = 5


@dataclass(frozen=True)
class ModelManagementPolicy:
    default_model: str = "deepseek-v4-flash"
    critical_model: str = "deepseek-v4-pro"

    def select_model(self, *, critical: bool = False, full_deepseek: bool = False) -> str:
        if critical or full_deepseek:
            return self.critical_model
        return self.default_model


@dataclass(frozen=True)
class OpenClawSecurityConfig:
    host_exec_enabled: bool
    native_plugins_enabled: bool
    allow_yolo_mode: bool
    allowed_mcp_tools: list[str]

    @classmethod
    def default_for_mvp(cls) -> "OpenClawSecurityConfig":
        return cls(
            host_exec_enabled=False,
            native_plugins_enabled=False,
            allow_yolo_mode=False,
            allowed_mcp_tools=list(DEFAULT_TOOL_NAMES),
        )


@dataclass(frozen=True)
class OpenClawRoutePolicy:
    stock_research_servers: list[str]
    watchlist_triage_servers: list[str]
    quant_validation_servers: list[str]

    @classmethod
    def default_for_mvp(cls) -> "OpenClawRoutePolicy":
        return cls(
            stock_research_servers=["financial-agent"],
            watchlist_triage_servers=["financial-agent"],
            quant_validation_servers=["financial-agent", "quantgpt"],
        )

    def required_servers_for_task(self, task_name: str) -> list[str]:
        mapping = {
            "stock_research": self.stock_research_servers,
            "watchlist_triage": self.watchlist_triage_servers,
            "quant_validation": self.quant_validation_servers,
        }
        return list(mapping.get(task_name, self.stock_research_servers))


class _FallbackMCPServer:
    """Tiny MCP-like container for environments without the `mcp` package."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, Callable[..., Any]] = {}

    def tool(self, name: str | None = None):
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name or func.__name__] = func
            return func

        return decorator

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def run(self, transport: str = "stdio", mount_path: str | None = None) -> None:
        raise RuntimeError(
            "The `mcp` package is not installed in this Python environment. "
            "Install it in the repo .venv to run the real MCP server."
        )


class FinancialAgentMCPServer:
    """Public wrapper around the new financial-agent MCP surface."""

    def __init__(
        self,
        *,
        runtime_config: FinancialAgentRuntimeConfig | None = None,
        capabilities: FinancialAgentCapabilities | None = None,
        model_policy: ModelManagementPolicy | None = None,
        security_config: OpenClawSecurityConfig | None = None,
        route_policy: OpenClawRoutePolicy | None = None,
    ):
        self.runtime_config = runtime_config or FinancialAgentRuntimeConfig()
        self.capabilities = capabilities or FinancialAgentCapabilities()
        self.model_policy = model_policy or ModelManagementPolicy()
        self.security_config = security_config or OpenClawSecurityConfig.default_for_mvp()
        self.route_policy = route_policy or OpenClawRoutePolicy.default_for_mvp()
        self._mcp = self._build_mcp_runtime()
        self._tool_registry = self._build_registry()
        self._register_tools()

    def list_tools(self) -> list[ToolMetadata]:
        return [metadata for metadata in self._tool_registry.values() if self._is_tool_exposed(metadata.name)]

    def list_tool_names(self) -> list[str]:
        return [metadata.name for metadata in self.list_tools()]

    def requires_quantgpt(self, tool_name: str) -> bool:
        return self._tool_registry[tool_name].requires_quantgpt

    def select_model(self, *, tool_name: str, critical: bool = False, full_deepseek: bool = False) -> str:
        metadata = self._tool_registry[tool_name]
        return self.model_policy.select_model(
            critical=critical or metadata.critical_by_default,
            full_deepseek=full_deepseek,
        )

    def call_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        return self._tool_registry[tool_name].handler(**kwargs)

    def run(self, transport: str = "stdio") -> None:
        self._mcp.run(transport=transport)

    def streamable_http_app(self):
        if not hasattr(self._mcp, "streamable_http_app"):
            raise RuntimeError("HTTP MCP app is unavailable because the `mcp` package is missing.")
        return self._mcp.streamable_http_app()

    def http_endpoint_url(self, *, hostname: str | None = None) -> str:
        host = hostname or self.runtime_config.http_host
        return f"http://{host}:{self.runtime_config.http_port}{self.runtime_config.streamable_http_path}"

    def _build_mcp_runtime(self):
        if _FastMCP is None:
            return _FallbackMCPServer("financial-agent")

        fastmcp_kwargs: dict[str, Any] = {
            "host": self.runtime_config.http_host,
            "port": self.runtime_config.http_port,
            "streamable_http_path": self.runtime_config.streamable_http_path,
        }
        if _TransportSecuritySettings is not None:
            fastmcp_kwargs["transport_security"] = _TransportSecuritySettings(
                allowed_hosts=list(self.runtime_config.http_allowed_hosts),
            )

        return _FastMCP("financial-agent", **fastmcp_kwargs)

    def _build_registry(self) -> dict[str, ToolMetadata]:
        return {
            "refresh_news": ToolMetadata(
                name="refresh_news",
                handler=self.refresh_news_tool,
                description="Refresh the news corpus once per day.",
                mutates_state=True,
            ),
            "query_news_research": ToolMetadata(
                name="query_news_research",
                handler=self.query_news_research,
                description="Run grounded news research over the indexed article corpus.",
            ),
            "recommend_stock": ToolMetadata(
                name="recommend_stock",
                handler=self.recommend_stock,
                description="Generate a news-grounded stock recommendation.",
                critical_by_default=True,
            ),
            "run_watchlist_triage": ToolMetadata(
                name="run_watchlist_triage",
                handler=self.run_watchlist_triage_tool,
                description="Rank a watchlist by research priority.",
                critical_by_default=True,
            ),
            "run_watchlist_workflow": ToolMetadata(
                name="run_watchlist_workflow",
                handler=self.run_watchlist_workflow,
                description="Refresh the local news corpus if needed, then research and rank a watchlist by priority.",
                mutates_state=True,
                critical_by_default=True,
            ),
            "read_watchlist_report": ToolMetadata(
                name="read_watchlist_report",
                handler=self.read_watchlist_report,
                description="Read the saved Markdown watchlist report for a specific run id.",
            ),
            "read_watchlist_timeline": ToolMetadata(
                name="read_watchlist_timeline",
                handler=self.read_watchlist_timeline,
                description="Read the saved watchlist timing timeline and latency summary for a specific run id.",
            ),
            "get_company_profile": ToolMetadata(
                name="get_company_profile",
                handler=self.get_company_profile,
                description="Return a stable company profile from the entity KB.",
            ),
            "retrieve_supporting_articles": ToolMetadata(
                name="retrieve_supporting_articles",
                handler=self.retrieve_supporting_articles,
                description="Return grounded supporting articles only.",
            ),
        }

    def _register_tools(self) -> None:
        for metadata in self._tool_registry.values():
            if self._is_tool_exposed(metadata.name):
                self._mcp.tool(name=metadata.name)(metadata.handler)

    def _is_tool_exposed(self, tool_name: str) -> bool:
        return tool_name in self.security_config.allowed_mcp_tools

    def refresh_news(
        self,
        today: str | None = None,
        top_k: int | None = None,
        question: str | None = None,
        debug_rerank: bool = False,
        include_manual: bool = False,
        news_endpoint: str = "everything",
        news_days_back: int = 7,
        news_page: int = 1,
        news_sort_by: str = "publishedAt",
        news_page_size: int = 100,
        show_progress: bool = False,
        progress_sink: WatchlistProgressSink | None = None,
    ) -> dict[str, Any]:
        return refresh_news_corpus(
            RefreshNewsRequest(
                refresh_state_path=self.runtime_config.refresh_state_path,
                service_defaults_path=self.runtime_config.service_defaults_path,
                db_path=self.runtime_config.db_path,
                persist_dir=self.runtime_config.persist_dir,
                collection_name=self.runtime_config.collection_name,
                today=today,
                top_k=top_k,
                question=question,
                debug_rerank=debug_rerank,
                include_manual=include_manual,
                news_endpoint=news_endpoint,
                news_days_back=news_days_back,
                news_page=news_page,
                news_sort_by=news_sort_by,
                news_page_size=news_page_size,
                show_progress=show_progress,
            ),
            progress_sink=progress_sink,
            run_news_pipeline_fn=run_news_pipeline,
        )

    def refresh_news_tool(
        self,
        today: str | None = None,
        top_k: int | None = None,
        question: str | None = None,
        debug_rerank: bool = False,
        include_manual: bool = False,
        news_endpoint: str = "everything",
        news_days_back: int = 7,
        news_page: int = 1,
        news_sort_by: str = "publishedAt",
        news_page_size: int = 100,
        show_progress: bool = False,
    ) -> dict[str, Any]:
        return self.refresh_news(
            today=today,
            top_k=top_k,
            question=question,
            debug_rerank=debug_rerank,
            include_manual=include_manual,
            news_endpoint=news_endpoint,
            news_days_back=news_days_back,
            news_page=news_page,
            news_sort_by=news_sort_by,
            news_page_size=news_page_size,
            show_progress=show_progress,
        )

    def query_news_research(
        self,
        question: str,
        top_k: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
    ) -> dict[str, Any]:
        defaults = self._service_defaults()
        vector_store = self._make_vector_store()
        result = answer_query(
            question,
            vector_store,
            top_k=top_k or defaults.query_top_k,
            retrieval_top_k=retrieval_top_k or defaults.retrieval_top_k,
            retrieval_intent=retrieval_intent,
        )
        return {
            "question": question,
            "answer": result.answer,
            "confidence": result.confidence.value,
            "insufficient_evidence": result.insufficient_evidence,
            "supporting_points": [
                {"text": point.text, "citations": list(point.citations)}
                for point in result.supporting_points
            ],
            "counter_points": [
                {"text": point.text, "citations": list(point.citations)}
                for point in result.counter_points
            ],
            "sources": [
                {
                    "id": source.id,
                    "title": source.title,
                    "url": source.url,
                    "snippet": source.snippet,
                }
                for source in result.sources
            ],
            "report_path": None,
        }

    def recommend_stock(
        self,
        target: str,
        top_k: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        include_report: bool = False,
    ) -> dict[str, Any]:
        defaults = self._service_defaults()
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
        vector_store = self._make_vector_store()
        try:
            response = recommend_target(
                target,
                vector_store,
                storage,
                top_k=top_k or defaults.recommendation_top_k,
                retrieval_top_k=retrieval_top_k or defaults.retrieval_top_k,
                retrieval_intent=retrieval_intent,
            )
        finally:
            storage.close()

        report_path = None
        if include_report:
            report_path = write_recommendation_report(
                target,
                response,
                output_dir=self._recommendation_reports_dir(),
            )

        return {
            "target": target,
            "decision": response.decision.value,
            "confidence": response.confidence.value,
            "time_horizon": response.time_horizon.value,
            "insufficient_evidence": response.insufficient_evidence,
            "reasoning": response.reasoning,
            "key_risks": list(response.key_risks),
            "report_path": report_path,
        }

    def run_watchlist_triage(
        self,
        tickers: list[str],
        top_n: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        include_report: bool = False,
        progress_sink: WatchlistProgressSink | None = None,
        timeline_recorder: WatchlistTimelineRecorder | None = None,
    ) -> dict[str, Any]:
        workflow = self._run_watchlist_triage_workflow(
            tickers=tickers,
            top_n=top_n,
            retrieval_top_k=retrieval_top_k,
            retrieval_intent=retrieval_intent,
            progress_sink=progress_sink,
            timeline_recorder=timeline_recorder,
        )
        result = workflow.result
        return {
            "run_id": result.run_id,
            "top_n": result.top_n,
            "ranked_items": [
                {
                    "ticker": item.ticker,
                    "priority": item.priority.value,
                    "confidence": item.confidence.value,
                    "rank": item.rank,
                    "should_flag_human_review": item.should_flag_human_review,
                }
                for item in result.ranked_items
            ],
            "report_path": workflow.report_path,
            "timeline_path": workflow.timeline_path,
            "timing_summary": workflow.timing_summary,
            "timing_text": workflow.timing_text,
            "batching": workflow.batching,
            "retrieval_failures": [
                {
                    "ticker": failure.ticker,
                    "status": failure.status,
                    "error_message": failure.error_message,
                }
                for failure in (result.retrieval_failures or [])
            ],
        }

    def run_watchlist_triage_tool(
        self,
        tickers: list[str],
        top_n: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        include_report: bool = False,
    ) -> dict[str, Any]:
        return self.run_watchlist_triage(
            tickers=tickers,
            top_n=top_n,
            retrieval_top_k=retrieval_top_k,
            retrieval_intent=retrieval_intent,
            include_report=include_report,
        )

    def run_watchlist_workflow(
        self,
        tickers: list[str],
        top_n: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        include_report: bool = False,
        today: str | None = None,
        news_endpoint: str = "everything",
        news_days_back: int = 7,
        news_page: int = 1,
        news_sort_by: str = "publishedAt",
        news_page_size: int = 100,
    ) -> dict[str, Any]:
        workflow = self._run_refresh_then_watchlist_workflow(
            tickers=tickers,
            top_n=top_n,
            retrieval_top_k=retrieval_top_k,
            retrieval_intent=retrieval_intent,
            today=today,
            news_endpoint=news_endpoint,
            news_days_back=news_days_back,
            news_page=news_page,
            news_sort_by=news_sort_by,
            news_page_size=news_page_size,
        )
        return {
            "workflow": "watchlist_refresh_then_triage",
            "refresh": workflow.refresh,
            "triage": {
                "run_id": workflow.result.run_id,
                "top_n": workflow.result.top_n,
                "ranked_items": [
                    {
                        "ticker": item.ticker,
                        "priority": item.priority.value,
                        "confidence": item.confidence.value,
                        "rank": item.rank,
                        "should_flag_human_review": item.should_flag_human_review,
                    }
                    for item in workflow.result.ranked_items
                ],
                "report_path": workflow.report_path,
                "timeline_path": workflow.timeline_path,
                "timing_summary": workflow.timing_summary,
                "timing_text": workflow.timing_text,
                "batching": workflow.batching,
                "retrieval_failures": [
                    {
                        "ticker": failure.ticker,
                        "status": failure.status,
                        "error_message": failure.error_message,
                    }
                    for failure in (workflow.result.retrieval_failures or [])
                ],
            },
            "timeline_path": workflow.timeline_path,
            "timing_summary": workflow.timing_summary,
            "timing_text": workflow.timing_text,
        }

    def read_watchlist_report(self, run_id: str) -> dict[str, Any]:
        storage = self._make_news_store()
        try:
            return read_watchlist_report_artifact(
                run_id,
                storage=storage,
                reports_dir=self._watchlist_reports_dir(),
            )
        finally:
            storage.close()

    def read_watchlist_timeline(self, run_id: str) -> dict[str, Any]:
        return read_watchlist_timeline_artifact(
            run_id,
            reports_dir=self._watchlist_reports_dir(),
            timeline_suffix=self.runtime_config.watchlist_timeline_suffix,
        )

    def get_company_profile(self, ticker: str) -> dict[str, Any]:
        store = SQLiteEntityStore(db_path=self.runtime_config.entity_db_path)
        try:
            company = store.load_company_by_ticker(ticker)
        finally:
            store.close()

        if company is None:
            return {"ticker": ticker.upper(), "found": False}

        return {
            "found": True,
            "ticker": company.primary_ticker,
            "company_id": company.company_id,
            "canonical_name": company.canonical_name,
            "website": company.website,
            "ir_url": company.ir_url,
            "asset_type": company.asset_type,
            "ticker_aliases": list(company.ticker_aliases),
            "company_aliases": list(company.company_aliases),
            "ceo_names": list(company.ceo_names),
            "products": list(company.product_names),
            "business_lines": list(company.business_lines),
            "themes": list(company.themes),
        }

    def retrieve_supporting_articles(
        self,
        query: str,
        top_k: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
    ) -> dict[str, Any]:
        defaults = self._service_defaults()
        company_kb = SQLiteEntityStore(db_path=self.runtime_config.entity_db_path)
        bundle = retrieve_evidence_bundle(
            query,
            self._make_vector_store(),
            top_k=top_k or defaults.query_top_k,
            retrieval_top_k=retrieval_top_k or defaults.retrieval_top_k,
            company_kb=company_kb,
            retrieval_intent=retrieval_intent,
        )
        company_kb.close()
        return {
            "query": query,
            "article_count": len(bundle.evidence),
            "articles": [
                {
                    "source_id": item.id,
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "summary": item.summary,
                    "snippet": item.snippet,
                    "published_at": item.published_at,
                    "rerank_position": item.rerank_position,
                    "retrieval_intent": item.retrieval_intent,
                    "company_id": item.company_id,
                    "expanded_query": item.expanded_query,
                    "kb_boost": item.kb_boost,
                    "final_score": item.final_score,
                    "kb_match_count": item.kb_match_count,
                    "matches_business_line": item.matches_business_line,
                    "matches_theme": item.matches_theme,
                    "indirect_kb_boost": item.indirect_kb_boost,
                    "indirect_match_types": list(item.indirect_match_types),
                    "attribution_match_types": list(item.attribution_match_types),
                    "attribution_matched_aliases": list(item.attribution_matched_aliases),
                }
                for item in bundle.evidence
            ],
        }

    def _make_vector_store(self) -> ChromaVectorStore:
        return create_corpus_vector_store(
            db_path=self.runtime_config.db_path,
            persist_dir=self.runtime_config.persist_dir,
            collection_name=self.runtime_config.collection_name,
        )

    def _make_news_store(self) -> SQLiteNewsStore:
        return SQLiteNewsStore(db_path=self.runtime_config.db_path)

    def _recommendation_reports_dir(self) -> str:
        return os.path.join(self.runtime_config.reports_dir, self.runtime_config.recommendation_reports_subdir)

    def _watchlist_reports_dir(self) -> str:
        return os.path.join(self.runtime_config.reports_dir, self.runtime_config.watchlist_reports_subdir)

    def _service_defaults(self) -> ServiceDefaults:
        return load_service_defaults(self.runtime_config.service_defaults_path)

    def _run_watchlist_triage_workflow(
        self,
        *,
        tickers: list[str],
        top_n: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        progress_sink: WatchlistProgressSink | None = None,
        timeline_recorder: WatchlistTimelineRecorder | None = None,
    ) -> WatchlistWorkflowResult:
        defaults = self._service_defaults()
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
        try:
            return run_watchlist_triage_workflow(
                WatchlistRunRequest(
                    tickers=tickers,
                    top_n=top_n or defaults.watchlist_top_n,
                    retrieval_top_k=retrieval_top_k or defaults.watchlist_retrieval_top_k,
                    retrieval_intent=retrieval_intent,
                    db_path=self.runtime_config.db_path,
                    persist_dir=self.runtime_config.persist_dir,
                    collection_name=self.runtime_config.collection_name,
                ),
                storage,
                self._make_vector_store,
                output_dir=self._watchlist_reports_dir(),
                timeline_suffix=self.runtime_config.watchlist_timeline_suffix,
                config=WatchlistResearchConfig(
                    auto_batch_threshold=self.runtime_config.watchlist_auto_batch_threshold,
                    auto_batch_size=self.runtime_config.watchlist_auto_batch_size,
                    max_concurrency=self.runtime_config.watchlist_retrieval_max_concurrency,
                ),
                progress_sink=progress_sink,
                timeline_recorder=timeline_recorder,
                persist_timeline=timeline_recorder is not None,
            )
        finally:
            storage.close()

    def _run_refresh_then_watchlist_workflow(
        self,
        *,
        tickers: list[str],
        top_n: int | None = None,
        retrieval_top_k: int | None = None,
        retrieval_intent: str = DEFAULT_RETRIEVAL_INTENT,
        today: str | None = None,
        news_endpoint: str = "everything",
        news_days_back: int = 7,
        news_page: int = 1,
        news_sort_by: str = "publishedAt",
        news_page_size: int = 100,
    ) -> WatchlistWorkflowResult:
        timeline_recorder = WatchlistTimelineRecorder(sink=self._watchlist_progress_printer)
        defaults = self._service_defaults()
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
        try:
            return run_refresh_then_watchlist_workflow(
                WatchlistRunRequest(
                    tickers=tickers,
                    top_n=top_n or defaults.watchlist_top_n,
                    retrieval_top_k=retrieval_top_k or defaults.watchlist_retrieval_top_k,
                    retrieval_intent=retrieval_intent,
                    db_path=self.runtime_config.db_path,
                    persist_dir=self.runtime_config.persist_dir,
                    collection_name=self.runtime_config.collection_name,
                ),
                storage,
                self._make_vector_store,
                refresh_request=RefreshNewsRequest(
                    refresh_state_path=self.runtime_config.refresh_state_path,
                    service_defaults_path=self.runtime_config.service_defaults_path,
                    db_path=self.runtime_config.db_path,
                    persist_dir=self.runtime_config.persist_dir,
                    collection_name=self.runtime_config.collection_name,
                    today=today,
                    news_endpoint=news_endpoint,
                    news_days_back=news_days_back,
                    news_page=news_page,
                    news_sort_by=news_sort_by,
                    news_page_size=news_page_size,
                ),
                output_dir=self._watchlist_reports_dir(),
                timeline_suffix=self.runtime_config.watchlist_timeline_suffix,
                config=WatchlistResearchConfig(
                    auto_batch_threshold=self.runtime_config.watchlist_auto_batch_threshold,
                    auto_batch_size=self.runtime_config.watchlist_auto_batch_size,
                    max_concurrency=self.runtime_config.watchlist_retrieval_max_concurrency,
                ),
                timeline_recorder=timeline_recorder,
            )
        finally:
            storage.close()

    def _empty_watchlist_report_payload(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "found": False,
            "report_path": None,
            "content": None,
            "error_code": None,
            "error_message": None,
        }

    def _empty_watchlist_timeline_payload(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "found": False,
            "timeline_path": None,
            "events": [],
            "timing_summary": None,
            "timing_text": None,
            "error_code": None,
            "error_message": None,
        }

    def _resolve_watchlist_report_path(self, report_path: str) -> Path:
        path = Path(report_path)
        if path.is_absolute():
            return path.resolve()
        return (Path.cwd() / path).resolve()

    def _resolve_watchlist_timeline_path(self, run_id: str) -> Path:
        timeline_path = os.path.join(
            self._watchlist_reports_dir(),
            f"{run_id}{self.runtime_config.watchlist_timeline_suffix}",
        )
        return self._resolve_watchlist_report_path(timeline_path)

    def _is_path_within_root(self, candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _watchlist_progress_printer(self, event: WatchlistProgressEvent) -> None:
        print(render_watchlist_progress_line(event), flush=True)

    def _emit_progress(
        self,
        progress_sink: WatchlistProgressSink | None,
        event: WatchlistProgressEvent,
    ) -> None:
        if progress_sink is not None:
            progress_sink(event)


def create_financial_agent_server(
    runtime_config: FinancialAgentRuntimeConfig | None = None,
    capabilities: FinancialAgentCapabilities | None = None,
) -> FinancialAgentMCPServer:
    return FinancialAgentMCPServer(
        runtime_config=runtime_config,
        capabilities=capabilities,
    )
