from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace

from event_collector.theme_chokepoint.contracts import RunStatus
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface
from event_collector.theme_chokepoint.orchestrator import RootStageOrchestrator
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage1 import AssistedThemeFramingService
from tests.test_theme_chokepoint_stage1 import (
    RecordingFramer,
    RecordingProposer,
    anchor,
    clear_frame,
    request,
)


def _awaiting_run(database):
    repository = ThemeChokepointRepository(database)
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([anchor("large power transformers", 0.91)]),
    )
    snapshot = service.start(request(run_id="receipt-run"))
    return repository, service, snapshot


def test_get_run_projects_complete_durable_receipt_before_and_after_confirmation(tmp_path):
    """SELECT INVARIANT: get_run projects one immutable all-or-none receipt after reopen."""
    database = tmp_path / "theme.db"
    repository, service, awaiting = _awaiting_run(database)
    interface = ThemeChokepointInterface(repository, product_service=None)

    get_run = interface.tool_handlers()["theme_chokepoint_get_run"]
    assert get_run(run_id=awaiting.run_id)["data"] == {
        "schema_version": "theme-chokepoint-mcp.v1",
        "run_id": awaiting.run_id,
        "status": "AWAITING_PRODUCT_CONFIRMATION",
        "next_stage": None,
        "confirmed_anchor_ids": [],
        "confirmed_by": None,
        "confirmed_at": None,
    }

    selected = awaiting.product_anchors[0].anchor_id
    receipt = service.confirm_product_anchors(
        awaiting.run_id,
        anchor_ids=(selected,),
        confirmed_by=" product-owner ",
    )
    reopened = ThemeChokepointRepository(database)
    ready = ThemeChokepointInterface(
        reopened, product_service=None
    ).tool_handlers()["theme_chokepoint_get_run"](run_id=awaiting.run_id)["data"]
    assert ready["status"] == "READY_FOR_SUPPLY_CHAIN"
    assert ready["confirmed_anchor_ids"] == [selected]
    assert ready["confirmed_by"] == "product-owner"
    assert ready["confirmed_at"] == receipt.confirmed_at.isoformat()

    with reopened._connect() as connection:
        connection.execute(
            "UPDATE theme_chokepoint_runs SET status = ? WHERE run_id = ?",
            (RunStatus.SUPPLY_CHAIN_GRAPH_READY.value, awaiting.run_id),
        )
    later = ThemeChokepointInterface(
        ThemeChokepointRepository(database), product_service=None
    ).tool_handlers()["theme_chokepoint_get_run"](run_id=awaiting.run_id)["data"]
    assert later["status"] == "SUPPLY_CHAIN_GRAPH_READY"
    assert later["confirmed_anchor_ids"] == [selected]
    assert later["confirmed_by"] == "product-owner"
    assert later["confirmed_at"] == receipt.confirmed_at.isoformat()


def test_confirm_and_reopen_preserve_one_exact_receipt_order_for_reordered_selection(tmp_path):
    """SELECT INVARIANT: direct and reconstructed receipts have identical durable order."""
    database = tmp_path / "theme.db"
    repository = ThemeChokepointRepository(database)
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer(
            [
                anchor("large power transformers", 0.91),
                anchor("UPS and switchgear", 0.84),
            ]
        ),
    )
    awaiting = service.start(request(run_id="ordered-receipt-run"))
    proposal_order = [item.anchor_id for item in awaiting.product_anchors]
    selected_order = list(reversed(proposal_order))
    confirm = ThemeChokepointInterface(
        repository,
        product_service=None,
        stage1=service,
    ).tool_handlers()["theme_chokepoint_confirm_anchors"]

    direct = confirm(
        run_id=awaiting.run_id,
        anchor_ids=selected_order,
        confirmed_by="owner",
    )
    reconstructed = ThemeChokepointInterface(
        ThemeChokepointRepository(database),
        product_service=None,
    ).tool_handlers()["theme_chokepoint_get_run"](run_id=awaiting.run_id)

    assert direct["data"]["confirmed_anchor_ids"] == proposal_order
    assert reconstructed["data"]["confirmed_anchor_ids"] == proposal_order
    assert {
        key: direct["data"][key]
        for key in (
            "run_id",
            "confirmed_anchor_ids",
            "confirmed_by",
            "confirmed_at",
        )
    } == {
        key: reconstructed["data"][key]
        for key in (
            "run_id",
            "confirmed_anchor_ids",
            "confirmed_by",
            "confirmed_at",
        )
    }


class _SnapshotRepository:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    def get_run(self, run_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot

    def list_run_ids(self):
        return ("matrix-run", "older-run")


def _snapshot(status, *, ids=(), actor=None, confirmed_at=None):
    anchors = tuple(
        SimpleNamespace(anchor_id=anchor_id, status="confirmed")
        for anchor_id in ids
    )
    return SimpleNamespace(
        run_id="matrix-run",
        status=status,
        product_anchors=anchors,
        confirmed_by=actor,
        confirmed_at=confirmed_at,
    )


def test_get_run_tool_fails_closed_for_every_contradictory_status_receipt_matrix():
    """SELECT INVARIANT: impossible status/receipt pairs never become MCP success."""
    confirmed_at = datetime(2026, 8, 23, 10, 30, tzinfo=timezone.utc)
    contradictions = (
        _snapshot(RunStatus.READY_FOR_SUPPLY_CHAIN),
        _snapshot(
            RunStatus.AWAITING_PRODUCT_CONFIRMATION,
            ids=("anchor-1",),
            actor="owner",
            confirmed_at=confirmed_at,
        ),
        _snapshot(RunStatus.SUPPLY_CHAIN_GRAPH_READY),
        _snapshot(
            RunStatus.READY_FOR_SUPPLY_CHAIN,
            ids=("anchor-1", "anchor-1"),
            actor="owner",
            confirmed_at=confirmed_at,
        ),
        _snapshot(
            RunStatus.READY_FOR_SUPPLY_CHAIN,
            ids=("anchor-1",),
            actor=None,
            confirmed_at=confirmed_at,
        ),
    )

    for snapshot in contradictions:
        repository = _SnapshotRepository(snapshot)
        handler = ThemeChokepointInterface(
            repository, product_service=None
        ).tool_handlers()["theme_chokepoint_get_run"]

        result = handler(run_id="matrix-run")

        assert result == {
            "ok": False,
            "error": {
                "code": "MCP_RESPONSE_SCHEMA_MISMATCH",
                "message": "MCP response schema mismatch",
                "retryable": False,
            },
        }
        assert repository.calls == 1

    missing = ThemeChokepointInterface(
        _SnapshotRepository(error=KeyError("C:/secret/provider.db")),
        product_service=None,
    ).tool_handlers()["theme_chokepoint_get_run"](run_id="missing-run")
    assert missing == {
        "ok": False,
        "error": {
            "code": "RUN_NOT_FOUND",
            "message": "Run was not found",
            "retryable": False,
        },
    }
    assert "secret" not in repr(missing)


class _LifecycleStage1:
    def __init__(self, snapshot, repository=None):
        self.snapshot = snapshot
        self.repository = repository
        self.start_calls = []
        self.confirm_calls = []

    def start(self, research_request):
        self.start_calls.append(research_request)
        return self.snapshot

    def confirm_product_anchors(self, run_id, *, anchor_ids, confirmed_by):
        self.confirm_calls.append((run_id, anchor_ids, confirmed_by))
        confirmed_at = datetime(2026, 8, 23, 11, 0, tzinfo=timezone.utc)
        if self.repository is not None:
            self.repository.snapshot = _snapshot(
                RunStatus.READY_FOR_SUPPLY_CHAIN,
                ids=anchor_ids,
                actor=confirmed_by,
                confirmed_at=confirmed_at,
            )
        return SimpleNamespace(
            run_id=run_id,
            status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            confirmed_anchor_ids=anchor_ids,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
        )


class _LifecycleOrchestrator:
    def __init__(self):
        self.continue_calls = []
        self.advance_calls = []
        self.manifest_calls = []

    def continue_run(self, run_id):
        self.continue_calls.append(run_id)
        return SimpleNamespace(run_id=run_id, final_status=RunStatus.SIGNAL_EXPORT_READY)

    def advance_one_stage(self, run_id, **kwargs):
        self.advance_calls.append((run_id, kwargs))
        return SimpleNamespace(
            run_id=run_id,
            final_status=RunStatus.SUPPLY_CHAIN_GRAPH_READY,
        )

    def get_manifest(self, run_id):
        self.manifest_calls.append(run_id)
        return SimpleNamespace(run_id=run_id, final_status=RunStatus.SIGNAL_EXPORT_READY)


def test_lifecycle_handlers_validate_and_delegate_only_to_python_authorities():
    """SELECT INVARIANT: lifecycle tools route only through Stage 1 and root authority."""
    snapshot = _snapshot(RunStatus.AWAITING_PRODUCT_CONFIRMATION)
    snapshot.product_anchors = (
        SimpleNamespace(anchor_id="anchor-1", status="proposed"),
    )
    repository = _SnapshotRepository(snapshot)
    stage1 = _LifecycleStage1(snapshot, repository)
    orchestrator = _LifecycleOrchestrator()
    interface = ThemeChokepointInterface(
        repository, product_service=None
    )
    interface.stage1 = stage1
    interface.orchestrator = orchestrator
    handlers = interface.tool_handlers()

    assert {
        "theme_chokepoint_start",
        "theme_chokepoint_get_pending_anchors",
        "theme_chokepoint_confirm_anchors",
        "theme_chokepoint_continue",
        "theme_chokepoint_advance_stage",
        "theme_chokepoint_get_artifacts",
        "theme_chokepoint_list_runs",
    } <= set(handlers)

    start_payload = asdict(request(run_id="matrix-run"))
    start_payload["as_of_date"] = start_payload["as_of_date"].isoformat()
    started = handlers["theme_chokepoint_start"](**start_payload)
    assert started["ok"] is True
    assert stage1.start_calls[0].run_id == "matrix-run"

    malformed = handlers["theme_chokepoint_start"](
        **{**start_payload, "provider_token": "secret"}
    )
    assert malformed["error"]["code"] == "INVALID_ARGUMENT"
    assert len(stage1.start_calls) == 1

    confirmed = handlers["theme_chokepoint_confirm_anchors"](
        run_id="matrix-run",
        anchor_ids=["anchor-1"],
        confirmed_by="owner",
    )
    assert confirmed["data"]["confirmed_anchor_ids"] == ["anchor-1"]
    continued = handlers["theme_chokepoint_continue"](run_id="matrix-run")
    advanced = handlers["theme_chokepoint_advance_stage"](
        run_id="matrix-run",
        expected_stage=2,
        expected_status="READY_FOR_SUPPLY_CHAIN",
        idempotency_key="mastra-run:stage-2",
    )
    artifacts = handlers["theme_chokepoint_get_artifacts"](run_id="matrix-run")
    listed = handlers["theme_chokepoint_list_runs"]()
    assert continued["ok"] is True
    assert advanced["ok"] is True
    assert artifacts["ok"] is True
    assert listed == {
        "ok": True,
        "data": {
            "schema_version": "theme-chokepoint-mcp.v1",
            "run_ids": ["matrix-run", "older-run"],
        },
    }
    assert orchestrator.continue_calls == ["matrix-run"]
    assert orchestrator.advance_calls == [
        (
            "matrix-run",
            {
                "expected_stage": 2,
                "expected_status": RunStatus.READY_FOR_SUPPLY_CHAIN,
                "idempotency_key": "mastra-run:stage-2",
            },
        )
    ]
    assert orchestrator.manifest_calls == ["matrix-run"]


def test_lifecycle_handlers_return_the_complete_stable_domain_error_matrix(tmp_path):
    """SELECT INVARIANT: known lifecycle failures retain stable endpoint codes."""
    database = tmp_path / "theme.db"
    repository = ThemeChokepointRepository(database)
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([anchor("UPS", 0.9)]),
    )
    awaiting = service.start(request(run_id="error-matrix-run"))
    selected = awaiting.product_anchors[0].anchor_id
    handlers = ThemeChokepointInterface(
        repository,
        product_service=None,
        stage1=service,
        orchestrator=_LifecycleOrchestrator(),
    ).tool_handlers()

    results = [
        handlers["theme_chokepoint_confirm_anchors"](
            run_id=awaiting.run_id,
            anchor_ids=[selected, selected],
            confirmed_by="owner",
        ),
        handlers["theme_chokepoint_confirm_anchors"](
            run_id=awaiting.run_id,
            anchor_ids=["anchor-not-proposed"],
            confirmed_by="owner",
        ),
        handlers["theme_chokepoint_continue"](run_id=awaiting.run_id),
    ]
    handlers["theme_chokepoint_confirm_anchors"](
        run_id=awaiting.run_id,
        anchor_ids=[selected],
        confirmed_by="owner",
    )
    results.extend(
        [
            handlers["theme_chokepoint_get_pending_anchors"](
                run_id=awaiting.run_id
            ),
            handlers["theme_chokepoint_confirm_anchors"](
                run_id=awaiting.run_id,
                anchor_ids=[selected],
                confirmed_by="owner",
            ),
        ]
    )

    assert [
        result.get("error", {}).get("code", "UNEXPECTED_SUCCESS")
        for result in results
    ] == [
        "DUPLICATE_ANCHOR_ID",
        "ANCHOR_NOT_PROPOSED_FOR_RUN",
        "RUN_NOT_AWAITING_CONFIRMATION",
        "RUN_NOT_AWAITING_CONFIRMATION",
        "RUN_NOT_AWAITING_CONFIRMATION",
    ]
    assert all(result["error"]["retryable"] is False for result in results)


def test_malformed_existing_manifest_is_schema_failure_not_run_not_found():
    """SELECT INVARIANT: manifest corruption cannot masquerade as an absent run."""
    root = Path(tempfile.mkdtemp(prefix="m0-corrupt-", dir=Path.cwd() / ".tmp"))
    manifest = root / "matrix-run" / "stage1-7-e2e-run-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"run_id": "matrix-run", "final_status": "SIGNAL_EXPORT_READY"}),
        encoding="utf-8",
    )
    orchestrator = RootStageOrchestrator(
        repository=_SnapshotRepository(_snapshot(RunStatus.SIGNAL_EXPORT_READY)),
        stage1=None,
        stage2=None,
        stage3=None,
        stage4=None,
        stage5=None,
        stage6=None,
        stage7=None,
        manifest_root=root,
        executable_contract_id="contract-v1",
        executable_contract_sha256="a" * 64,
    )
    result = ThemeChokepointInterface(
        orchestrator.repository,
        product_service=None,
        orchestrator=orchestrator,
    ).tool_handlers()["theme_chokepoint_get_artifacts"](run_id="matrix-run")

    assert result == {
        "ok": False,
        "error": {
            "code": "MCP_RESPONSE_SCHEMA_MISMATCH",
            "message": "MCP response schema mismatch",
            "retryable": False,
        },
    }
