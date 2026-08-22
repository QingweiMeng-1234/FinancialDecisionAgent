"""Thin JSON-friendly boundary shared by CLI and MCP tool registration."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
import math

from event_collector.theme_chokepoint.contracts import (
    FeedbackCorrectionDraft,
    ResearchRequest,
    RunStatus,
)


NEXT_STAGE = {
    RunStatus.READY_FOR_SUPPLY_CHAIN: 2,
    RunStatus.SUPPLY_CHAIN_GRAPH_READY: 3,
    RunStatus.CHOKEPOINT_ASSESSMENT_READY: 4,
    RunStatus.COMPANY_ASSESSMENT_INCOMPLETE: 4,
    RunStatus.COMPANY_ASSESSMENT_READY: 5,
    RunStatus.PERSISTENT_RESEARCH_READY: 6,
    RunStatus.MONITORING_READY: 7,
    RunStatus.SIGNAL_EXPORT_READY: None,
}


MCP_SCHEMA_VERSION = "theme-chokepoint-mcp.v1"


class _ToolError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


class ThemeChokepointInterface:
    def __init__(self, repository, product_service, *, stage1=None, orchestrator=None):
        self.repository = repository
        self.product_service = product_service
        self.stage1 = stage1
        self.orchestrator = orchestrator

    def get_run_status(self, run_id: str) -> dict:
        run = self.repository.get_run(run_id)
        return {
            "run_id": run_id,
            "status": run.status.value,
            "next_stage": NEXT_STAGE.get(run.status),
        }

    def start(
        self,
        run_id: str,
        theme: str,
        trigger: str,
        region: str,
        as_of_date: str,
        time_horizon_months: int,
        analysis_goal: str,
        seed_products: list[str],
        seed_companies: list[str],
        research_mode: str,
        max_depth: int,
        max_nodes: int,
        max_iterations: int,
        max_sources: int,
        max_time_seconds: int,
        max_cost_usd: float,
        max_product_anchors: int,
    ) -> dict:
        def execute():
            request = ResearchRequest(
                run_id=_text(run_id, "run_id", identifier=True),
                theme=_text(theme, "theme"),
                trigger=_text(trigger, "trigger"),
                region=_text(region, "region"),
                as_of_date=_date(as_of_date),
                time_horizon_months=_positive_int(
                    time_horizon_months, "time_horizon_months"
                ),
                analysis_goal=_text(analysis_goal, "analysis_goal"),
                seed_products=_text_tuple(seed_products, "seed_products"),
                seed_companies=_text_tuple(seed_companies, "seed_companies"),
                research_mode=_assisted_mode(research_mode),
                max_depth=_positive_int(max_depth, "max_depth"),
                max_nodes=_positive_int(max_nodes, "max_nodes"),
                max_iterations=_positive_int(max_iterations, "max_iterations"),
                max_sources=_positive_int(max_sources, "max_sources"),
                max_time_seconds=_positive_int(max_time_seconds, "max_time_seconds"),
                max_cost_usd=_finite_cost(max_cost_usd),
                max_product_anchors=_positive_int(
                    max_product_anchors, "max_product_anchors"
                ),
            )
            manifest = self.orchestrator.start(request)
            _correlated_run(manifest.run_id, request.run_id)
            return _run_data(
                request.run_id, manifest.final_status, stages=None
            )

        return _safe_call(execute)

    def get_run(self, run_id: str) -> dict:
        def execute():
            normalized = _text(run_id, "run_id", identifier=True)
            run = self.repository.get_run(normalized)
            return {
                "schema_version": MCP_SCHEMA_VERSION,
                "run_id": normalized,
                "status": run.status.value,
                "next_stage": NEXT_STAGE.get(run.status),
                "confirmed_by": run.confirmed_by,
                "confirmed_at": _jsonable(run.confirmed_at),
            }

        return _safe_call(execute)

    def get_pending_anchors(self, run_id: str) -> dict:
        def execute():
            normalized = _text(run_id, "run_id", identifier=True)
            run = self.repository.get_run(normalized)
            if run.status is not RunStatus.AWAITING_PRODUCT_CONFIRMATION:
                raise _ToolError(
                    "RUN_NOT_AWAITING_CONFIRMATION",
                    "run is not awaiting product confirmation",
                )
            if run.demand_frame is None:
                raise _ToolError(
                    "MCP_RESPONSE_SCHEMA_MISMATCH",
                    "pending-anchor data is incomplete",
                )
            return {
                "schema_version": MCP_SCHEMA_VERSION,
                "run_id": normalized,
                "status": run.status.value,
                "demand_frame": _jsonable(asdict(run.demand_frame)),
                "anchors": [_jsonable(asdict(anchor)) for anchor in run.product_anchors],
            }

        return _safe_call(execute)

    def confirm_anchors(
        self, run_id: str, anchor_ids: list[str], confirmed_by: str
    ) -> dict:
        def execute():
            normalized = _text(run_id, "run_id", identifier=True)
            actor = _text(confirmed_by, "confirmed_by")
            ids = _selected_ids(anchor_ids)
            receipt = self.stage1.confirm_product_anchors(
                normalized, anchor_ids=ids, confirmed_by=actor
            )
            _correlated_run(receipt.run_id, normalized)
            if receipt.status is not RunStatus.READY_FOR_SUPPLY_CHAIN:
                raise _ToolError(
                    "PYTHON_STATUS_MISMATCH",
                    "confirmation did not reach the required Python status",
                )
            return {
                "schema_version": MCP_SCHEMA_VERSION,
                "run_id": receipt.run_id,
                "status": receipt.status.value,
                "confirmed_anchor_ids": list(receipt.confirmed_anchor_ids),
                "confirmed_by": receipt.confirmed_by,
                "confirmed_at": _jsonable(receipt.confirmed_at),
            }

        return _safe_call(execute)

    def continue_run(self, run_id: str) -> dict:
        def execute():
            normalized = _text(run_id, "run_id", identifier=True)
            manifest = self.orchestrator.continue_run(normalized)
            _correlated_run(manifest.run_id, normalized)
            return _manifest_data(manifest)

        return _safe_call(execute)

    def get_artifacts(self, run_id: str) -> dict:
        def execute():
            normalized = _text(run_id, "run_id", identifier=True)
            manifest = self.orchestrator.get_manifest(normalized)
            _correlated_run(manifest.run_id, normalized)
            return _manifest_data(manifest)

        return _safe_call(execute)

    def finalize(self, run_id: str) -> dict:
        return _jsonable(asdict(self.product_service.finalize(run_id)))

    def compare_runs(self, before_run_id: str, after_run_id: str) -> dict:
        return _jsonable(
            asdict(self.product_service.compare_runs(before_run_id, after_run_id))
        )

    def record_feedback(self, **payload) -> dict:
        correction = self.product_service.record_feedback(
            FeedbackCorrectionDraft(**payload)
        )
        return _jsonable(asdict(correction))

    def tool_handlers(self) -> dict:
        """Handlers can be registered directly on a FastMCP-compatible server."""
        handlers = {
            "theme_chokepoint_get_run": self.get_run_status,
            "theme_chokepoint_finalize": self.finalize,
            "theme_chokepoint_compare_runs": self.compare_runs,
            "theme_chokepoint_record_feedback": self.record_feedback,
        }
        if self.stage1 is not None and self.orchestrator is not None:
            handlers.update(
                {
                    "theme_chokepoint_start": self.start,
                    "theme_chokepoint_get_run": self.get_run,
                    "theme_chokepoint_get_pending_anchors": self.get_pending_anchors,
                    "theme_chokepoint_confirm_anchors": self.confirm_anchors,
                    "theme_chokepoint_continue": self.continue_run,
                    "theme_chokepoint_get_artifacts": self.get_artifacts,
                }
            )
        return handlers


def register_mcp_tools(server, interface: ThemeChokepointInterface) -> None:
    for name, handler in interface.tool_handlers().items():
        server.tool(name=name)(handler)


def _safe_call(callback) -> dict:
    try:
        return {"ok": True, "data": callback()}
    except _ToolError as error:
        return _error(error.code, error.safe_message, error.retryable)
    except KeyError:
        return _error("RUN_NOT_FOUND", "theme chokepoint run was not found")
    except ValueError as error:
        message = str(error).casefold()
        if "already exists" in message:
            return _error("RUN_ALREADY_EXISTS", "theme chokepoint run already exists")
        if "not found" in message or "missing" in message:
            return _error("RUN_NOT_FOUND", "theme chokepoint run was not found")
        if "awaiting" in message:
            return _error(
                "RUN_NOT_AWAITING_CONFIRMATION",
                "run is not awaiting product confirmation",
            )
        if "anchor" in message:
            return _error(
                "ANCHOR_NOT_PROPOSED_FOR_RUN",
                "an anchor is not proposed for this run",
            )
        return _error("INVALID_ARGUMENT", "invalid theme chokepoint argument")
    except Exception:
        raise RuntimeError("theme chokepoint lifecycle tool failed") from None


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def _text(value, field: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ToolError("INVALID_ARGUMENT", f"{field} must be non-empty text")
    normalized = value.strip()
    if identifier and len(normalized) > 128:
        raise _ToolError("INVALID_ARGUMENT", f"{field} is too long")
    return normalized


def _text_tuple(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _ToolError("INVALID_ARGUMENT", f"{field} must be an array")
    return tuple(_text(item, field) for item in value)


def _positive_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _ToolError("INVALID_ARGUMENT", f"{field} must be a positive integer")
    return value


def _finite_cost(value) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise _ToolError("INVALID_ARGUMENT", "max_cost_usd must be finite and non-negative")
    return float(value)


def _date(value) -> date:
    if not isinstance(value, str):
        raise _ToolError("INVALID_ARGUMENT", "as_of_date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _ToolError("INVALID_ARGUMENT", "as_of_date must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise _ToolError("INVALID_ARGUMENT", "as_of_date must be YYYY-MM-DD")
    return parsed


def _assisted_mode(value) -> str:
    if value != "assisted":
        raise _ToolError("INVALID_ARGUMENT", "research_mode must be assisted")
    return value


def _selected_ids(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _ToolError("INVALID_ARGUMENT", "anchor_ids must be a non-empty array")
    normalized = tuple(_text(item, "anchor_id", identifier=True) for item in value)
    if len(set(normalized)) != len(normalized):
        raise _ToolError("DUPLICATE_ANCHOR_ID", "anchor_ids must be unique")
    return normalized


def _correlated_run(actual, expected) -> None:
    if actual != expected:
        raise _ToolError(
            "RUN_CORRELATION_MISMATCH", "Python run correlation did not match"
        )


def _run_data(run_id, status, *, stages) -> dict:
    data = {
        "schema_version": MCP_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status.value,
        "next_stage": NEXT_STAGE.get(status),
    }
    if stages is not None:
        data["stages"] = stages
    return data


def _manifest_data(manifest) -> dict:
    stages = [
        {
            "stage": receipt.stage,
            "input_status": _jsonable(receipt.input_status),
            "output_status": _jsonable(receipt.output_status),
            "outcome": receipt.outcome,
            "artifact_ids": list(receipt.artifact_ids),
            "completed_at": _jsonable(receipt.completed_at),
        }
        for receipt in manifest.stages
    ]
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "status": manifest.final_status.value,
        "stages": stages,
    }


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, RunStatus):
        return value.value
    return value
