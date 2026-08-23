"""MVP MCP surface for the core financial-agent workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import importlib
import json
import inspect
import os
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from event_collector.entity_kb import SQLiteEntityStore
from event_collector.news_storage import SQLiteNewsStore
from event_collector.refresh_ledger import RetrySchedule, compute_scope_key
from event_collector.refresh_retry_scheduler import RefreshRetryScheduler
from event_collector.rag_answering import answer_query
from event_collector.recommendation import recommend_target, write_recommendation_report
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT
from event_collector.retrieval_orchestration import retrieve_evidence_bundle
from event_collector.service_defaults import (
    DEFAULT_SERVICE_DEFAULTS_PATH,
    ServiceDefaults,
    load_service_defaults,
    resolve_query_time_window,
)
from event_collector.serving_generation_factory import (
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)
from event_collector.successor_generation_coordinator import (
    SuccessorGenerationCoordinatorConfig,
    create_runtime_successor_generation_coordinator,
)
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
    REFRESH_CONTRACT_VERSION,
    SuccessorGenerationCoordinator,
    WatchlistWorkflowResult,
    read_watchlist_report_artifact,
    read_watchlist_timeline_artifact,
    refresh_news_corpus,
    run_refresh_then_watchlist_workflow,
    run_watchlist_triage_workflow,
)
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface

try:  # pragma: no cover - version-specific import
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.server.transport_security import TransportSecuritySettings as _TransportSecuritySettings
except Exception:  # pragma: no cover - MCP 2.0 moved the public server
    try:
        from mcp.server import MCPServer as _FastMCP
        from mcp.server.transport_security import (
            TransportSecuritySettings as _TransportSecuritySettings,
        )
    except Exception:  # pragma: no cover - environments without MCP
        _FastMCP = None
        _TransportSecuritySettings = None


DEFAULT_TOOL_NAMES = [
    "refresh_news",
    "run_watchlist_workflow",
    "read_watchlist_report",
    "read_watchlist_timeline",
]


def _refresh_ledger_path(refresh_state_path: str) -> str:
    path = Path(refresh_state_path)
    return str(path.with_name(f"{path.stem}.sqlite3"))


def _scheduled_text(snapshot: dict[str, Any], field: str) -> str:
    value = snapshot.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Scheduled {field} must be non-empty text")
    return value


def _scheduled_positive_int(snapshot: dict[str, Any], field: str) -> int:
    value = snapshot.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Scheduled {field} must be a positive integer")
    return value


def _reader_generation_id(reader: Any) -> str | None:
    active = getattr(reader, "active_generation", None)
    value = getattr(active, "generation_id", None)
    return value if isinstance(value, str) and value else None


def _reader_provenance(reader: Any) -> dict[str, str | None]:
    """Return the immutable serving identity attached to one reader."""

    active = getattr(reader, "active_generation", None)
    return {
        "generation_id": getattr(active, "generation_id", None),
        "corpus_id": getattr(active, "corpus_id", None),
        "collection_name": getattr(active, "collection_name", None),
        "corpus_snapshot_id": getattr(active, "corpus_snapshot_id", None),
        "embedding_artifact": getattr(active, "embedding_artifact", None),
        "index_config_fingerprint": getattr(active, "index_config_fingerprint", None),
    }


def _corpus_unavailable_payload(error: CorpusUnavailableError) -> dict[str, str]:
    """Expose expected serving absence as a stable MCP result, not a 5xx."""

    return {
        "status": "failed",
        "failure_code": error.code,
        "failure_stage": error.stage,
        "failure_reason_code": error.reason_code,
        "failure_message": str(error),
    }


class _SerializedVectorReader:
    """Serialize access when one request shares a reader across worker threads."""

    def __init__(self, reader: Any) -> None:
        self._reader = reader
        self._search_lock = Lock()

    def search(self, *args: Any, **kwargs: Any) -> Any:
        with self._search_lock:
            return self._reader.search(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reader, name)


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
    db_path: str = "data/rag_corpus_v2_20260815/news_articles.db"
    index_control_db_path: str = "data/runtime/index_generation_control.db"
    index_corpus_id: str = "news"
    canonical_db_path: str = "data/rag_corpus_v2_20260815/news_articles.db"
    canonical_content_root: str = "data/rag_corpus_v2_20260815/data/articles"
    chroma_persist_dir: str = "data/rag_index_v2_20260815"
    persist_dir: str = "data/rag_index_v2_20260815"
    collection_name: str = "news_articles_v2_20260815_v1"
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
    refresh_retry_scheduler_enabled: bool = True
    refresh_retry_poll_interval_seconds: float = 30.0
    theme_db_path: str = os.getenv(
        "FINANCIAL_AGENT_THEME_DB_PATH",
        os.path.join("data", "runtime", "theme_chokepoint.sqlite3"),
    )
    theme_runtime_factory: str | None = os.getenv(
        "FINANCIAL_AGENT_THEME_RUNTIME_FACTORY"
    )


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
        successor_generation_coordinator: SuccessorGenerationCoordinator | None = None,
        theme_interface: Any | None = None,
    ):
        self.runtime_config = runtime_config or FinancialAgentRuntimeConfig()
        self.capabilities = capabilities or FinancialAgentCapabilities()
        self.model_policy = model_policy or ModelManagementPolicy()
        self.security_config = security_config or OpenClawSecurityConfig.default_for_mvp()
        self.route_policy = route_policy or OpenClawRoutePolicy.default_for_mvp()
        self.theme_interface = theme_interface or _build_theme_interface(
            self.runtime_config
        )
        self.successor_generation_coordinator = (
            successor_generation_coordinator
            if successor_generation_coordinator is not None
            else create_runtime_successor_generation_coordinator(
                SuccessorGenerationCoordinatorConfig(
                    control_db_path=self.runtime_config.index_control_db_path,
                    canonical_db_path=self.runtime_config.canonical_db_path,
                    canonical_content_root=self.runtime_config.canonical_content_root,
                    chroma_persist_dir=self.runtime_config.chroma_persist_dir,
                    activate_verified_generation=True,
                )
            )
        )
        self._run_news_pipeline = run_news_pipeline
        self._mcp = self._build_mcp_runtime()
        self._tool_registry = self._build_registry()
        self._register_tools()
        self.retry_scheduler = (
            RefreshRetryScheduler(
                ledger_path=_refresh_ledger_path(self.runtime_config.refresh_state_path),
                retry_handler=self._run_scheduled_retry,
                poll_interval_seconds=self.runtime_config.refresh_retry_poll_interval_seconds,
            )
            if self.runtime_config.refresh_retry_scheduler_enabled
            else None
        )

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
        if self.retry_scheduler is not None:
            self.retry_scheduler.start()
        try:
            self._mcp.run(
                **_supported_kwargs(
                    self._mcp.run,
                    {
                        "transport": transport,
                        "host": self.runtime_config.http_host,
                        "port": self.runtime_config.http_port,
                    },
                    allow_var_keyword=True,
                )
            )
        finally:
            if self.retry_scheduler is not None:
                self.retry_scheduler.stop()

    def _run_scheduled_retry(self, schedule: RetrySchedule) -> dict[str, Any]:
        """Re-enter one frozen scope using runtime-owned provider adapters."""
        if not isinstance(schedule, RetrySchedule):
            raise TypeError("schedule must be a RetrySchedule")
        snapshot = schedule.config_snapshot
        if compute_scope_key(snapshot) != schedule.scope_key:
            raise ValueError("Scheduled refresh scope fingerprint does not match its snapshot")
        if snapshot.get("requested_date") != schedule.requested_date:
            raise ValueError("Scheduled refresh date does not match its snapshot")
        if snapshot.get("ingestion_contract_version") != REFRESH_CONTRACT_VERSION:
            raise ValueError("Scheduled refresh contract is not executable by this runtime")
        if snapshot.get("corpus_collection") != self.runtime_config.collection_name:
            raise ValueError("Scheduled refresh collection differs from the runtime collection")
        if snapshot.get("index_corpus_id") != self.runtime_config.index_corpus_id:
            raise ValueError("Scheduled refresh corpus differs from the runtime corpus")

        endpoint = _scheduled_text(snapshot, "news_endpoint")
        sort_by = _scheduled_text(snapshot, "news_sort_by")
        days_back = _scheduled_positive_int(snapshot, "news_days_back")
        page = _scheduled_positive_int(snapshot, "news_page")
        page_size = _scheduled_positive_int(snapshot, "news_page_size")
        include_manual = snapshot.get("include_manual")
        if not isinstance(include_manual, bool):
            raise ValueError("Scheduled include_manual must be boolean")
        return refresh_news_corpus(
            RefreshNewsRequest(
                refresh_state_path=self.runtime_config.refresh_state_path,
                service_defaults_path=self.runtime_config.service_defaults_path,
                db_path=self.runtime_config.db_path,
                persist_dir=self.runtime_config.persist_dir,
                collection_name=self.runtime_config.collection_name,
                index_corpus_id=self.runtime_config.index_corpus_id,
                today=schedule.requested_date,
                include_manual=include_manual,
                news_endpoint=endpoint,
                news_days_back=days_back,
                news_page=page,
                news_sort_by=sort_by,
                news_page_size=page_size,
            ),
            run_news_pipeline_fn=self._run_news_pipeline,
            successor_generation_coordinator=self.successor_generation_coordinator,
        )

    def streamable_http_app(self):
        if not hasattr(self._mcp, "streamable_http_app"):
            raise RuntimeError("HTTP MCP app is unavailable because the `mcp` package is missing.")
        return self._mcp.streamable_http_app(
            **_supported_kwargs(
                self._mcp.streamable_http_app,
                {
                    "host": self.runtime_config.http_host,
                    "streamable_http_path": self.runtime_config.streamable_http_path,
                    "transport_security": self._transport_security(),
                },
            )
        )

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
        transport_security = self._transport_security()
        if transport_security is not None:
            fastmcp_kwargs["transport_security"] = transport_security

        return _FastMCP(
            "financial-agent",
            **_supported_kwargs(
                _FastMCP,
                fastmcp_kwargs,
                allow_var_keyword=True,
            ),
        )

    def _transport_security(self):
        if _TransportSecuritySettings is None:
            return None
        return _TransportSecuritySettings(
            allowed_hosts=list(self.runtime_config.http_allowed_hosts),
        )

    def _build_registry(self) -> dict[str, ToolMetadata]:
        registry = {
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
        if self.theme_interface is not None:
            for name, handler in self.theme_interface.tool_handlers().items():
                registry[name] = ToolMetadata(
                    name=name,
                    handler=handler,
                    description="Theme Chokepoint lifecycle authority.",
                    mutates_state=name
                    in {
                        "theme_chokepoint_start",
                        "theme_chokepoint_confirm_anchors",
                        "theme_chokepoint_continue",
                    },
                )
        return registry

    def _register_tools(self) -> None:
        for metadata in self._tool_registry.values():
            if self._is_tool_exposed(metadata.name):
                self._mcp.tool(name=metadata.name)(metadata.handler)

    def _is_tool_exposed(self, tool_name: str) -> bool:
        return tool_name in self.security_config.allowed_mcp_tools or (
            self.theme_interface is not None
            and tool_name.startswith("theme_chokepoint_")
            and tool_name in self._tool_registry
        )

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
                index_corpus_id=self.runtime_config.index_corpus_id,
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
            successor_generation_coordinator=self.successor_generation_coordinator,
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
        start_at: str | None = None,
        end_at: str | None = None,
        latest_at: str | None = None,
        lookback_days: int | None = None,
    ) -> dict[str, Any]:
        defaults = self._service_defaults()
        try:
            vector_store = self._make_vector_store()
        except CorpusUnavailableError as error:
            return _corpus_unavailable_payload(error)
        vector_store = vector_store.with_time_window(
            **resolve_query_time_window(
                defaults,
                start_at=start_at,
                end_at=end_at,
                latest_at=latest_at,
                lookback_days=lookback_days,
            )
        )
        result = answer_query(
            question,
            vector_store,
            top_k=top_k or defaults.query_top_k,
            retrieval_top_k=retrieval_top_k or defaults.retrieval_top_k,
            retrieval_intent=retrieval_intent,
        )
        return {
            "question": question,
            "generation_id": _reader_generation_id(vector_store),
            "corpus_provenance": _reader_provenance(vector_store),
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
        try:
            vector_store = self._make_time_scoped_vector_store()
        except CorpusUnavailableError as error:
            return _corpus_unavailable_payload(error)
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
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
                retrieval_provenance=_reader_provenance(vector_store),
            )

        return {
            "target": target,
            "generation_id": _reader_generation_id(vector_store),
            "corpus_provenance": _reader_provenance(vector_store),
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
        provenance: dict[str, str | None] = {}
        try:
            workflow = self._run_watchlist_triage_workflow(
                tickers=tickers,
                top_n=top_n,
                retrieval_top_k=retrieval_top_k,
                retrieval_intent=retrieval_intent,
                progress_sink=progress_sink,
                timeline_recorder=timeline_recorder,
                provenance_sink=provenance,
            )
        except CorpusUnavailableError as error:
            return _corpus_unavailable_payload(error)
        result = workflow.result
        return {
            "run_id": result.run_id,
            "generation_id": provenance.get("generation_id"),
            "corpus_provenance": provenance,
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
        provenance: dict[str, str | None] = {}
        try:
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
                provenance_sink=provenance,
            )
        except CorpusUnavailableError as error:
            return _corpus_unavailable_payload(error)
        return {
            "workflow": "watchlist_refresh_then_triage",
            "generation_id": provenance.get("generation_id"),
            "corpus_provenance": provenance,
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
        start_at: str | None = None,
        end_at: str | None = None,
        latest_at: str | None = None,
        lookback_days: int | None = None,
    ) -> dict[str, Any]:
        defaults = self._service_defaults()
        try:
            vector_store = self._make_vector_store()
        except CorpusUnavailableError as error:
            return _corpus_unavailable_payload(error)
        company_kb = SQLiteEntityStore(db_path=self.runtime_config.entity_db_path)
        vector_store = vector_store.with_time_window(
            **resolve_query_time_window(
                defaults,
                start_at=start_at,
                end_at=end_at,
                latest_at=latest_at,
                lookback_days=lookback_days,
            )
        )
        bundle = retrieve_evidence_bundle(
            query,
            vector_store,
            top_k=top_k or defaults.query_top_k,
            retrieval_top_k=retrieval_top_k or defaults.retrieval_top_k,
            company_kb=company_kb,
            retrieval_intent=retrieval_intent,
        )
        company_kb.close()
        return {
            "query": query,
            "generation_id": _reader_generation_id(vector_store),
            "corpus_provenance": _reader_provenance(vector_store),
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

    def _make_vector_store(self) -> Any:
        pinned = create_active_generation_reader(
            ServingCorpusConfig(
                index_control_db_path=self.runtime_config.index_control_db_path,
                index_corpus_id=self.runtime_config.index_corpus_id,
                canonical_db_path=self.runtime_config.canonical_db_path,
                canonical_content_root=self.runtime_config.canonical_content_root,
                chroma_persist_dir=self.runtime_config.chroma_persist_dir,
            )
        )
        return pinned.reader

    def _make_time_scoped_vector_store(self) -> Any:
        """Pin one active generation and apply the service's finite news horizon."""

        reader = self._make_vector_store()
        return reader.with_time_window(
            **resolve_query_time_window(self._service_defaults())
        )

    def _request_pinned_vector_store_provider(
        self, provenance_sink: dict[str, str | None] | None = None
    ) -> Callable[[], Any]:
        lock = Lock()
        unset = object()
        reader: Any = unset

        def provide() -> Any:
            nonlocal reader
            if reader is unset:
                with lock:
                    if reader is unset:
                        reader = _SerializedVectorReader(
                            self._make_time_scoped_vector_store()
                        )
                        if provenance_sink is not None:
                            provenance_sink.update(_reader_provenance(reader))
            return reader

        return provide

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
        provenance_sink: dict[str, str | None] | None = None,
    ) -> WatchlistWorkflowResult:
        defaults = self._service_defaults()
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
        try:
            pinned_reader = _SerializedVectorReader(
                self._make_time_scoped_vector_store()
            )
            if provenance_sink is not None:
                provenance_sink.update(_reader_provenance(pinned_reader))
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
                lambda: pinned_reader,
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
                retrieval_provenance=_reader_provenance(pinned_reader),
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
        provenance_sink: dict[str, str | None] | None = None,
    ) -> WatchlistWorkflowResult:
        timeline_recorder = WatchlistTimelineRecorder(sink=self._watchlist_progress_printer)
        defaults = self._service_defaults()
        storage = SQLiteNewsStore(db_path=self.runtime_config.db_path)
        try:
            request_reader_provider = self._request_pinned_vector_store_provider(
                provenance_sink
            )
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
                request_reader_provider,
                refresh_request=RefreshNewsRequest(
                    refresh_state_path=self.runtime_config.refresh_state_path,
                    service_defaults_path=self.runtime_config.service_defaults_path,
                    db_path=self.runtime_config.db_path,
                    persist_dir=self.runtime_config.persist_dir,
                    collection_name=self.runtime_config.collection_name,
                    index_corpus_id=self.runtime_config.index_corpus_id,
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
                successor_generation_coordinator=self.successor_generation_coordinator,
                retrieval_provenance_provider=lambda: _reader_provenance(
                    request_reader_provider()
                ),
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
    successor_generation_coordinator: SuccessorGenerationCoordinator | None = None,
    theme_interface: Any | None = None,
) -> FinancialAgentMCPServer:
    return FinancialAgentMCPServer(
        runtime_config=runtime_config,
        capabilities=capabilities,
        successor_generation_coordinator=successor_generation_coordinator,
        theme_interface=theme_interface,
    )


class _LazyThemeRepository:
    """Delay creation of the Theme SQLite file until a lifecycle tool is used."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._repository = None

    def _get_repository(self):
        if self._repository is None:
            from event_collector.theme_chokepoint.repository import (
                ThemeChokepointRepository,
            )

            self._repository = ThemeChokepointRepository(self.db_path)
        return self._repository

    def __getattr__(self, name):
        return getattr(self._get_repository(), name)


def _build_theme_interface(
    runtime_config: FinancialAgentRuntimeConfig,
) -> ThemeChokepointInterface:
    """Compose the shipping Theme lifecycle boundary, optionally with full providers."""
    if runtime_config.theme_runtime_factory:
        module_name, separator, attribute = runtime_config.theme_runtime_factory.partition(
            ":"
        )
        if not separator or not module_name or not attribute:
            raise ValueError("theme runtime factory must use module:callable syntax")
        factory = getattr(importlib.import_module(module_name), attribute)
        runtime = factory(runtime_config)
        required = ("repository", "stage1", "orchestrator", "stage5")
        if any(getattr(runtime, name, None) is None for name in required):
            raise ValueError("theme runtime factory returned an incomplete runtime")
        return ThemeChokepointInterface(
            runtime.repository,
            product_service=runtime.stage5,
            stage1=runtime.stage1,
            orchestrator=runtime.orchestrator,
        )
    return ThemeChokepointInterface(
        _LazyThemeRepository(runtime_config.theme_db_path),
        product_service=None,
    )


def _supported_kwargs(callable_value, candidates, *, allow_var_keyword=False):
    parameters = inspect.signature(callable_value).parameters
    if allow_var_keyword and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return dict(candidates)
    return {
        name: value
        for name, value in candidates.items()
        if name in parameters
    }
