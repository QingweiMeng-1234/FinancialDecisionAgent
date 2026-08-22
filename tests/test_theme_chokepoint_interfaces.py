from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    ConfirmationReceipt, DemandFrame, ProductAnchor, RunStatus,
)
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface


class FakeRepository:
    def __init__(self):
        self.run = SimpleNamespace(
            run_id="run-m0-001",
            status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
            demand_frame=DemandFrame(
                normalized_theme="AI power", scope="global", exclusions=(),
                demand_hypothesis="AI raises load",
                measurable_demand_variables=("megawatts",),
                time_horizon_months=24, unresolved_questions=(),
            ),
            product_anchors=(ProductAnchor(
                anchor_id="anchor-1", product_name="Transformers",
                buyer_or_user="Utilities", demand_variable="unit demand",
                theme_link="Grid upgrades", confidence=0.8,
                supporting_evidence_ids=("evidence-1",), missing_evidence=(),
                status="proposed",
            ),),
            confirmed_by=None, confirmed_at=None,
        )

    def get_run(self, run_id):
        assert run_id == self.run.run_id
        return self.run


class FakeStage1:
    def __init__(self):
        self.calls = []

    def confirm_product_anchors(self, run_id, *, anchor_ids, confirmed_by):
        self.calls.append((run_id, anchor_ids, confirmed_by))
        return ConfirmationReceipt(
            run_id, RunStatus.READY_FOR_SUPPLY_CHAIN, anchor_ids, confirmed_by,
            datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
        )


class FakeOrchestrator:
    def __init__(self):
        self.start_calls, self.continue_calls = [], []

    def start(self, request):
        self.start_calls.append(request)
        return SimpleNamespace(
            run_id=request.run_id,
            final_status=RunStatus.AWAITING_PRODUCT_CONFIRMATION, stages=(),
        )

    def continue_run(self, run_id):
        self.continue_calls.append(run_id)
        return self.get_manifest(run_id)

    def get_manifest(self, run_id):
        return SimpleNamespace(
            run_id=run_id, final_status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            stages=(SimpleNamespace(
                stage=1, input_status=None,
                output_status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
                outcome="awaiting_human_confirmation", artifact_ids=(run_id,),
                completed_at=datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc),
            ),),
        )


def build_interface():
    repository, stage1, orchestrator = FakeRepository(), FakeStage1(), FakeOrchestrator()
    interface = ThemeChokepointInterface(
        repository, SimpleNamespace(), stage1=stage1, orchestrator=orchestrator,
    )
    return interface, stage1, orchestrator


def start_payload(**overrides):
    payload = dict(
        run_id="run-m0-001", theme="AI power", trigger="buildout",
        region="global", as_of_date="2026-08-22", time_horizon_months=24,
        analysis_goal="find chokepoints", seed_products=[], seed_companies=[],
        research_mode="assisted", max_depth=4, max_nodes=50,
        max_iterations=3, max_sources=30, max_time_seconds=900,
        max_cost_usd=10.0, max_product_anchors=5,
    )
    payload.update(overrides)
    return payload


def test_lifecycle_handlers_delegate_to_python_authorities_and_return_v1_envelopes():
    """SELECT INVARIANT: six lifecycle tools are thin, correlated authority calls."""
    interface, stage1, orchestrator = build_interface()

    started = interface.start(**start_payload())
    run = interface.get_run("run-m0-001")
    pending = interface.get_pending_anchors("run-m0-001")
    confirmed = interface.confirm_anchors(
        "run-m0-001", anchor_ids=["anchor-1"], confirmed_by="analyst"
    )
    continued = interface.continue_run("run-m0-001")
    artifacts = interface.get_artifacts("run-m0-001")

    assert started["data"] == {
        "schema_version": "theme-chokepoint-mcp.v1",
        "run_id": "run-m0-001",
        "status": "AWAITING_PRODUCT_CONFIRMATION",
        "next_stage": None,
    }
    assert orchestrator.start_calls[0].as_of_date == date(2026, 8, 22)
    assert run["data"]["confirmed_by"] is None
    assert pending["data"]["anchors"][0]["anchor_id"] == "anchor-1"
    assert confirmed["data"]["status"] == "READY_FOR_SUPPLY_CHAIN"
    assert stage1.calls == [("run-m0-001", ("anchor-1",), "analyst")]
    assert continued["data"]["stages"][0]["stage"] == 1
    assert orchestrator.continue_calls == ["run-m0-001"]
    assert artifacts["data"] == continued["data"]
    assert set(interface.tool_handlers()) >= {
        "theme_chokepoint_start", "theme_chokepoint_get_run",
        "theme_chokepoint_get_pending_anchors", "theme_chokepoint_confirm_anchors",
        "theme_chokepoint_continue", "theme_chokepoint_get_artifacts",
    }


def test_lifecycle_boundary_rejects_duplicate_empty_and_non_finite_input():
    """SELECT INVARIANT: malformed lifecycle input fails before authority calls."""
    interface, stage1, orchestrator = build_interface()

    duplicate = interface.confirm_anchors(
        "run-m0-001", anchor_ids=["anchor-1", "anchor-1"], confirmed_by="analyst"
    )
    empty = interface.confirm_anchors(
        "run-m0-001", anchor_ids=["anchor-1"], confirmed_by="   "
    )
    non_finite = interface.start(**start_payload(max_cost_usd=float("nan")))
    with pytest.raises(TypeError):
        interface.start(**start_payload(extra=True))

    assert duplicate["error"]["code"] == "DUPLICATE_ANCHOR_ID"
    assert empty["error"]["code"] == "INVALID_ARGUMENT"
    assert non_finite["error"]["code"] == "INVALID_ARGUMENT"
    assert stage1.calls == []
    assert orchestrator.start_calls == []


def test_lifecycle_boundary_redacts_unexpected_authority_errors():
    """SELECT INVARIANT: transport failures expose no provider token or local path."""
    interface, _stage1, orchestrator = build_interface()

    def fail(_request):
        raise RuntimeError("token=secret provider-body C:/private/system")

    orchestrator.start = fail
    with pytest.raises(RuntimeError) as captured:
        interface.start(**start_payload())

    assert str(captured.value) == "theme chokepoint lifecycle tool failed"
    assert "secret" not in str(captured.value)
    assert "C:/private" not in str(captured.value)


def test_lifecycle_handlers_require_complete_runtime_injection():
    """SELECT INVARIANT: incomplete runtime injection cannot advertise lifecycle."""
    interface = ThemeChokepointInterface(FakeRepository(), SimpleNamespace())
    assert set(interface.tool_handlers()) == {
        "theme_chokepoint_get_run", "theme_chokepoint_finalize",
        "theme_chokepoint_compare_runs", "theme_chokepoint_record_feedback",
    }
