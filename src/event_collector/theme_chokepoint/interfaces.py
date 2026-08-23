"""Thin JSON-friendly boundary shared by CLI and MCP tool registration."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from functools import wraps
import math

from event_collector.theme_chokepoint.contracts import (
    FeedbackCorrectionDraft,
    ResearchRequest,
    RunStatus,
)
from event_collector.theme_chokepoint.orchestrator import (
    ContinueInProgressError,
    ManifestCorruptionError,
    ManifestNotFoundError,
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

_UNCONFIRMED_STATUSES = {
    RunStatus.REQUEST_STORED,
    RunStatus.NEEDS_CLARIFICATION,
    RunStatus.AWAITING_PRODUCT_CONFIRMATION,
}


class _ResponseSchemaError(ValueError):
    pass


class _RuntimeNotConfigured(RuntimeError):
    pass


class _LifecycleError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_LIFECYCLE_ERROR_MESSAGES = {
    "RUN_NOT_AWAITING_CONFIRMATION": "Run is not awaiting confirmation",
    "ANCHOR_NOT_PROPOSED_FOR_RUN": "Anchor is not proposed for this run",
    "DUPLICATE_ANCHOR_ID": "Anchor IDs must be unique",
}


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

    def get_run(self, run_id: str) -> dict:
        run = self.repository.get_run(run_id)
        if run.run_id != run_id:
            raise _ResponseSchemaError("run correlation mismatch")
        confirmed_anchor_ids = [
            anchor.anchor_id
            for anchor in run.product_anchors
            if anchor.status == "confirmed"
        ]
        _validate_receipt_matrix(
            run.status,
            confirmed_anchor_ids,
            run.confirmed_by,
            run.confirmed_at,
        )
        return {
            "schema_version": "theme-chokepoint-mcp.v1",
            "run_id": run_id,
            "status": run.status.value,
            "next_stage": NEXT_STAGE.get(run.status),
            "confirmed_anchor_ids": confirmed_anchor_ids,
            "confirmed_by": run.confirmed_by,
            "confirmed_at": (
                run.confirmed_at.isoformat() if run.confirmed_at is not None else None
            ),
        }

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

    def start_run(
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
        if self.stage1 is None:
            raise _RuntimeNotConfigured
        payload = {
            "run_id": run_id,
            "theme": theme,
            "trigger": trigger,
            "region": region,
            "as_of_date": as_of_date,
            "time_horizon_months": time_horizon_months,
            "analysis_goal": analysis_goal,
            "seed_products": seed_products,
            "seed_companies": seed_companies,
            "research_mode": research_mode,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_iterations": max_iterations,
            "max_sources": max_sources,
            "max_time_seconds": max_time_seconds,
            "max_cost_usd": max_cost_usd,
            "max_product_anchors": max_product_anchors,
        }
        request = _materialize_research_request(payload)
        orchestrator_start = getattr(self.orchestrator, "start", None)
        result = (
            orchestrator_start(request)
            if callable(orchestrator_start)
            else self.stage1.start(request)
        )
        if result.run_id != request.run_id:
            raise _ResponseSchemaError("start response run mismatch")
        return self.get_run(request.run_id)

    def get_pending_anchors(self, run_id: str) -> dict:
        _validate_run_id(run_id)
        run = self.repository.get_run(run_id)
        if run.run_id != run_id:
            raise _ResponseSchemaError("pending-anchor run mismatch")
        if run.status is not RunStatus.AWAITING_PRODUCT_CONFIRMATION:
            raise _LifecycleError("RUN_NOT_AWAITING_CONFIRMATION")
        return {
            "schema_version": "theme-chokepoint-mcp.v1",
            "run_id": run_id,
            "status": run.status.value,
            "anchors": [_jsonable(asdict(anchor)) for anchor in run.product_anchors],
        }

    def confirm_anchors(
        self, run_id: str, anchor_ids: list[str], confirmed_by: str
    ) -> dict:
        if self.stage1 is None:
            raise _RuntimeNotConfigured
        normalized_run_id = _validate_run_id(run_id)
        normalized_actor = _validate_text(confirmed_by, "confirmed_by")
        normalized_ids = tuple(
            _validate_text(value, "anchor_id") for value in _validate_list(anchor_ids)
        )
        if not normalized_ids:
            raise ValueError("at least one anchor is required")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise _LifecycleError("DUPLICATE_ANCHOR_ID")
        before = self.repository.get_run(normalized_run_id)
        if before.run_id != normalized_run_id:
            raise _ResponseSchemaError("confirmation request run mismatch")
        if before.status is not RunStatus.AWAITING_PRODUCT_CONFIRMATION:
            raise _LifecycleError("RUN_NOT_AWAITING_CONFIRMATION")
        proposed = {
            anchor.anchor_id
            for anchor in before.product_anchors
            if anchor.status == "proposed"
        }
        if not set(normalized_ids) <= proposed:
            raise _LifecycleError("ANCHOR_NOT_PROPOSED_FOR_RUN")
        receipt = self.stage1.confirm_product_anchors(
            normalized_run_id,
            anchor_ids=normalized_ids,
            confirmed_by=normalized_actor,
        )
        receipt_ids = tuple(receipt.confirmed_anchor_ids)
        if (
            receipt.run_id != normalized_run_id
            or receipt.confirmed_by != normalized_actor
            or len(receipt_ids) != len(set(receipt_ids))
            or set(receipt_ids) != set(normalized_ids)
        ):
            raise _ResponseSchemaError("confirmation response correlation mismatch")
        durable = self.get_run(normalized_run_id)
        durable_ids = durable["confirmed_anchor_ids"]
        if (
            durable["status"] != RunStatus.READY_FOR_SUPPLY_CHAIN.value
            or durable["confirmed_by"] != normalized_actor
            or set(durable_ids) != set(normalized_ids)
            or durable["confirmed_at"] != receipt.confirmed_at.isoformat()
        ):
            raise _ResponseSchemaError("durable confirmation receipt mismatch")
        return {
            "schema_version": "theme-chokepoint-mcp.v1",
            "run_id": durable["run_id"],
            "status": durable["status"],
            "confirmed_anchor_ids": list(durable_ids),
            "confirmed_by": durable["confirmed_by"],
            "confirmed_at": durable["confirmed_at"],
        }

    def continue_run(self, run_id: str) -> dict:
        if self.orchestrator is None:
            raise _RuntimeNotConfigured
        normalized_run_id = _validate_run_id(run_id)
        run = self.repository.get_run(normalized_run_id)
        if run.run_id != normalized_run_id:
            raise _ResponseSchemaError("continue request run mismatch")
        if run.status in _UNCONFIRMED_STATUSES:
            raise _LifecycleError("RUN_NOT_AWAITING_CONFIRMATION")
        result = self.orchestrator.continue_run(normalized_run_id)
        return _jsonable(asdict(result) if is_dataclass(result) else vars(result))

    def advance_stage(
        self,
        run_id: str,
        expected_stage: int,
        expected_status: str,
        idempotency_key: str,
    ) -> dict:
        if self.orchestrator is None:
            raise _RuntimeNotConfigured
        normalized_run_id = _validate_run_id(run_id)
        normalized_key = _validate_text(idempotency_key, "idempotency_key")
        if (
            isinstance(expected_stage, bool)
            or not isinstance(expected_stage, int)
            or expected_stage not in range(2, 8)
            or not isinstance(expected_status, str)
        ):
            raise ValueError("invalid expected stage/status")
        normalized_status = RunStatus(expected_status)
        run = self.repository.get_run(normalized_run_id)
        if run.run_id != normalized_run_id:
            raise _ResponseSchemaError("advance request run mismatch")
        result = self.orchestrator.advance_one_stage(
            normalized_run_id,
            expected_stage=expected_stage,
            expected_status=normalized_status,
            idempotency_key=normalized_key,
        )
        return _jsonable(asdict(result) if is_dataclass(result) else vars(result))

    def get_artifacts(self, run_id: str) -> dict:
        if self.orchestrator is None:
            raise _RuntimeNotConfigured
        normalized_run_id = _validate_run_id(run_id)
        run = self.repository.get_run(normalized_run_id)
        if run.run_id != normalized_run_id:
            raise _ResponseSchemaError("artifact request run mismatch")
        result = self.orchestrator.get_manifest(normalized_run_id)
        return _jsonable(asdict(result) if is_dataclass(result) else vars(result))

    def list_runs(self) -> dict:
        run_ids = self.repository.list_run_ids()
        if not isinstance(run_ids, (tuple, list)) or not all(
            isinstance(run_id, str) and run_id.strip() == run_id and run_id
            for run_id in run_ids
        ):
            raise _ResponseSchemaError("run listing response mismatch")
        return {
            "schema_version": "theme-chokepoint-mcp.v1",
            "run_ids": list(run_ids),
        }

    def tool_handlers(self) -> dict:
        """Handlers can be registered directly on a FastMCP-compatible server."""
        handlers = {
            "theme_chokepoint_get_run": self._safe_tool(self.get_run),
            "theme_chokepoint_finalize": self._safe_tool(self.finalize),
            "theme_chokepoint_compare_runs": self._safe_tool(self.compare_runs),
            "theme_chokepoint_record_feedback": self._safe_tool(self.record_feedback),
        }
        handlers.update(
            {
                "theme_chokepoint_start": self._safe_tool(self.start_run),
                "theme_chokepoint_get_pending_anchors": self._safe_tool(
                    self.get_pending_anchors
                ),
                "theme_chokepoint_confirm_anchors": self._safe_tool(
                    self.confirm_anchors
                ),
                "theme_chokepoint_continue": self._safe_tool(self.continue_run),
                "theme_chokepoint_advance_stage": self._safe_tool(
                    self.advance_stage
                ),
                "theme_chokepoint_get_artifacts": self._safe_tool(
                    self.get_artifacts
                ),
                "theme_chokepoint_list_runs": self._safe_tool(self.list_runs),
            }
        )
        return handlers

    @staticmethod
    def _safe_tool(handler):
        @wraps(handler)
        def safe_handler(*args, **kwargs):
            try:
                return {"ok": True, "data": handler(*args, **kwargs)}
            except _ResponseSchemaError:
                return _error(
                    "MCP_RESPONSE_SCHEMA_MISMATCH",
                    "MCP response schema mismatch",
                )
            except ManifestCorruptionError:
                return _error(
                    "MCP_RESPONSE_SCHEMA_MISMATCH",
                    "MCP response schema mismatch",
                )
            except ManifestNotFoundError:
                return _error(
                    "MCP_RESPONSE_SCHEMA_MISMATCH",
                    "MCP response schema mismatch",
                )
            except ContinueInProgressError:
                return _error(
                    "CONCURRENT_OR_DUPLICATE_RESUME",
                    "Continue operation is already in progress",
                )
            except _LifecycleError as error:
                return _error(error.code, _LIFECYCLE_ERROR_MESSAGES[error.code])
            except KeyError:
                return _error("RUN_NOT_FOUND", "Run was not found")
            except _RuntimeNotConfigured:
                return _error(
                    "RUNTIME_NOT_CONFIGURED",
                    "Runtime is not configured",
                )
            except (TypeError, ValueError):
                return _error("INVALID_ARGUMENT", "Invalid request")
            except Exception:
                return _error("MCP_TOOL_FAILURE", "MCP operation failed")

        return safe_handler


def register_mcp_tools(server, interface: ThemeChokepointInterface) -> None:
    for name, handler in interface.tool_handlers().items():
        server.tool(name=name)(handler)


def _validate_receipt_matrix(status, anchor_ids, actor, confirmed_at):
    has_ids = bool(anchor_ids)
    has_actor = isinstance(actor, str) and bool(actor.strip())
    has_time = (
        isinstance(confirmed_at, datetime)
        and confirmed_at.tzinfo is not None
        and confirmed_at.utcoffset() is not None
    )
    if len(anchor_ids) != len(set(anchor_ids)):
        raise _ResponseSchemaError("duplicate confirmation anchor")
    if status in _UNCONFIRMED_STATUSES:
        if has_ids or actor is not None or confirmed_at is not None:
            raise _ResponseSchemaError("unconfirmed status has a receipt")
        return
    if not (has_ids and has_actor and has_time):
        raise _ResponseSchemaError("confirmed status lacks a complete receipt")
    if actor != actor.strip():
        raise _ResponseSchemaError("confirmation actor is not normalized")


def _materialize_research_request(payload):
    expected = {field.name for field in fields(ResearchRequest)}
    if set(payload) != expected:
        raise ValueError("request fields do not match the schema")
    values = dict(payload)
    if not isinstance(values["as_of_date"], str):
        raise ValueError("as_of_date must be an ISO date")
    values["as_of_date"] = date.fromisoformat(values["as_of_date"])
    for name in ("seed_products", "seed_companies"):
        values[name] = tuple(_validate_list(values[name]))
        if not all(isinstance(value, str) for value in values[name]):
            raise ValueError(f"{name} must contain text")
    integer_fields = {
        "time_horizon_months",
        "max_depth",
        "max_nodes",
        "max_iterations",
        "max_sources",
        "max_time_seconds",
        "max_product_anchors",
    }
    for name in integer_fields:
        if isinstance(values[name], bool) or not isinstance(values[name], int):
            raise ValueError(f"{name} must be an integer")
    cost = values["max_cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost):
        raise ValueError("max_cost_usd must be finite")
    for name in expected - integer_fields - {"as_of_date", "seed_products", "seed_companies", "max_cost_usd"}:
        if not isinstance(values[name], str):
            raise ValueError(f"{name} must be text")
    return ResearchRequest(**values)


def _validate_run_id(value):
    return _validate_text(value, "run_id")


def _validate_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _validate_list(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list")
    return value


def _error(code, message, *, retryable=False):
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
