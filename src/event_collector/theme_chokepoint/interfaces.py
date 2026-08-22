"""Thin JSON-friendly boundary shared by CLI and MCP tool registration."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from event_collector.theme_chokepoint.contracts import FeedbackCorrectionDraft, RunStatus


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


class ThemeChokepointInterface:
    def __init__(self, repository, product_service):
        self.repository = repository
        self.product_service = product_service

    def get_run_status(self, run_id: str) -> dict:
        run = self.repository.get_run(run_id)
        return {
            "run_id": run_id,
            "status": run.status.value,
            "next_stage": NEXT_STAGE.get(run.status),
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

    def tool_handlers(self) -> dict:
        """Handlers can be registered directly on a FastMCP-compatible server."""
        return {
            "theme_chokepoint_get_run": self.get_run_status,
            "theme_chokepoint_finalize": self.finalize,
            "theme_chokepoint_compare_runs": self.compare_runs,
            "theme_chokepoint_record_feedback": self.record_feedback,
        }


def register_mcp_tools(server, interface: ThemeChokepointInterface) -> None:
    for name, handler in interface.tool_handlers().items():
        server.tool(name=name)(handler)


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
