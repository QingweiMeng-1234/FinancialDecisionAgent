from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
VALIDATOR_PATH = (
    ROOT / "tools" / "theme-chokepoint" / "validate-review-program-v1_1.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "theme_chokepoint_review_program_validator", VALIDATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _identity(role: str) -> dict[str, str]:
    return {"agent_id": f"agent-{role}", "session_id": f"session-{role}"}


def _finding(finding_id: str, *, root: str = "root-open") -> dict[str, str]:
    return {"finding_id": finding_id, "severity": "P1", "root_cause": root}


def _fixture(
    tmp_path: Path,
    *,
    reviewer_open: list[dict] | None = None,
    challenger_findings: list[dict] | None = None,
    include_challenger: bool = True,
    include_exit: bool = True,
    decision: str | None = None,
    root_cause_history: dict[str, list[int]] | None = None,
    cycle: int = 2,
) -> dict[str, Path | None]:
    reviewer_open = list(reviewer_open or [])
    challenger_findings = list(challenger_findings or [])
    before_inventory = "a" * 64
    after_inventory = "b" * 64
    before_id = f"etrp-cycle{cycle}-start-{before_inventory}"
    after_id = f"etrp-cycle{cycle}-after-{after_inventory}"
    program = _write(
        tmp_path / "program.json",
        {
            "schema_version": "theme-chokepoint-review-program-protocol-v1.1",
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "review_program_version": "1.1",
            "max_fix_cycles": 5,
            "root_cause_history": root_cause_history or {},
        },
    )
    cycle_start = _write(
        tmp_path / "cycle-start.json",
        {
            "schema_version": "theme-chokepoint-cycle-start-v1.1",
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "developer_identity": _identity("developer"),
            "cycle_increment_events": [
                {"cycle_number": cycle, "state": f"CYCLE_{cycle}_START"}
            ],
        },
    )
    before = _write(
        tmp_path / "before.json",
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "snapshot_phase": "start",
            "snapshot_id": before_id,
            "inventory_sha256": before_inventory,
        },
    )
    after = _write(
        tmp_path / "after.json",
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "snapshot_phase": "after",
            "snapshot_id": after_id,
            "inventory_sha256": after_inventory,
            "before_snapshot_id": before_id,
        },
    )
    reviewer = _write(
        tmp_path / "reviewer.json",
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "after_snapshot_id": after_id,
            "after_snapshot_sha256": _file_sha(after),
            "reviewer_identity": _identity("reviewer"),
            "closure_review_completed": True,
            "open_P0_P1": reviewer_open,
            "fresh_challenger_should_run": False,
        },
    )

    challenger: Path | None = None
    adjudication: Path | None = None
    if include_challenger:
        challenger = _write(
            tmp_path / "challenger.json",
            {
                "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
                "cycle_number": cycle,
                "after_snapshot_id": after_id,
                "after_snapshot_sha256": _file_sha(after),
                "challenger_identity": _identity("challenger"),
                "sealed": True,
                "fresh_context": True,
                "first_pass": {
                    "old_findings_bound": False,
                    "closure_ledger_bound": False,
                    "developer_explanation_bound": False,
                    "sealed_before_context_expansion": True,
                },
                "findings": challenger_findings,
            },
        )
        adjudication = _write(
            tmp_path / "challenger-adjudication.json",
            {
                "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
                "cycle_number": cycle,
                "after_snapshot_id": after_id,
                "after_snapshot_sha256": _file_sha(after),
                "challenger_receipt_sha256": _file_sha(challenger),
                "adjudicator_identity": _identity("adjudicator"),
                "accepted_P0_P1": challenger_findings,
                "same_root_cause_different_path": [],
            },
        )

    has_open = bool(reviewer_open or challenger_findings)
    exit_auditor: Path | None = None
    if include_exit:
        if has_open:
            exit_payload = {
                "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
                "cycle_number": cycle,
                "after_snapshot_id": after_id,
                "after_snapshot_sha256": _file_sha(after),
                "status": "NOT_ELIGIBLE_OPEN_FINDINGS",
            }
        else:
            exit_payload = {
                "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
                "cycle_number": cycle,
                "after_snapshot_id": after_id,
                "after_snapshot_sha256": _file_sha(after),
                "status": "FRESH_EXIT_AUDITOR_COMPLETE",
                "exit_auditor_identity": _identity("exit"),
                "sealed": True,
                "fresh_context": True,
                "accepted_P0_P1": [],
            }
        exit_auditor = _write(tmp_path / "exit.json", exit_payload)

    if decision is None:
        decision = "CONTINUE_NEXT_CYCLE" if has_open else "CANDIDATE_FOR_HOLDOUT"
    bindings = {
        "cycle_start": _file_sha(cycle_start),
        "before_snapshot": _file_sha(before),
        "after_snapshot": _file_sha(after),
        "reviewer_a": _file_sha(reviewer),
    }
    if challenger is not None:
        bindings["fresh_challenger"] = _file_sha(challenger)
    if adjudication is not None:
        bindings["challenger_adjudication"] = _file_sha(adjudication)
    if exit_auditor is not None:
        bindings["exit_auditor"] = _file_sha(exit_auditor)
    cycle_decision = _write(
        tmp_path / "decision.json",
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "review_program_version": "1.1",
            "cycle_number": cycle,
            "after_snapshot_id": after_id,
            "decision": decision,
            "bindings": bindings,
        },
    )
    return {
        "program": program,
        "cycle_start": cycle_start,
        "before_snapshot": before,
        "after_snapshot": after,
        "reviewer_a": reviewer,
        "fresh_challenger": challenger,
        "challenger_adjudication": adjudication,
        "exit_auditor": exit_auditor,
        "cycle_decision": cycle_decision,
    }


def _validate(paths: dict[str, Path | None]):
    return _load_validator().validate_review_program(paths)


def test_reopen_without_challenger_fails_and_cli_returns_nonzero(tmp_path):
    paths = _fixture(
        tmp_path, reviewer_open=[_finding("C1-P1-001")], include_challenger=False
    )
    report = _validate(paths)
    assert report["valid"] is False
    assert "G6_MISSING_FRESH_CHALLENGER" in report["errors"]

    command = [sys.executable, str(VALIDATOR_PATH)]
    for key, flag in (
        ("program", "--program"),
        ("cycle_start", "--cycle-start"),
        ("before_snapshot", "--before-snapshot"),
        ("after_snapshot", "--after-snapshot"),
        ("reviewer_a", "--reviewer-a"),
        ("cycle_decision", "--cycle-decision"),
        ("exit_auditor", "--exit-auditor"),
    ):
        command.extend((flag, str(paths[key])))
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode != 0
    assert json.loads(completed.stdout)["valid"] is False


def test_reopen_with_fresh_challenger_allows_continue(tmp_path):
    report = _validate(_fixture(tmp_path, reviewer_open=[_finding("C1-P1-001")]))
    assert report["valid"] is True
    assert report["transition"] == "CONTINUE_NEXT_CYCLE"


def test_all_closed_without_challenger_still_fails(tmp_path):
    report = _validate(_fixture(tmp_path, include_challenger=False))
    assert report["valid"] is False
    assert "G6_MISSING_FRESH_CHALLENGER" in report["errors"]


def test_closed_and_clean_challenger_without_exit_cannot_enter_holdout(tmp_path):
    report = _validate(_fixture(tmp_path, include_exit=False))
    assert report["valid"] is False
    assert "G8_FRESH_EXIT_AUDITOR_REQUIRED" in report["errors"]


def test_new_challenger_p1_requires_not_eligible_exit_and_can_continue(tmp_path):
    report = _validate(
        _fixture(tmp_path, challenger_findings=[_finding("C2-P1-NEW")])
    )
    assert report["valid"] is True
    assert report["open_P0_P1"] == ["C2-P1-NEW"]
    assert report["transition"] == "CONTINUE_NEXT_CYCLE"


@pytest.mark.parametrize("field", ["cycle_number", "after_snapshot_id"])
def test_challenger_cycle_or_snapshot_mismatch_fails(tmp_path, field):
    paths = _fixture(tmp_path, reviewer_open=[_finding("C1-P1-001")])
    challenger = json.loads(paths["fresh_challenger"].read_text(encoding="utf-8"))
    challenger[field] = 99 if field == "cycle_number" else "wrong-snapshot"
    _write(paths["fresh_challenger"], challenger)
    report = _validate(paths)
    assert report["valid"] is False
    assert any(error.startswith(("G1_", "G2_", "G11_")) for error in report["errors"])


def test_reviewer_should_run_false_has_no_scheduling_authority(tmp_path):
    paths = _fixture(tmp_path, reviewer_open=[_finding("C1-P1-001")])
    reviewer = json.loads(paths["reviewer_a"].read_text(encoding="utf-8"))
    assert reviewer["fresh_challenger_should_run"] is False
    assert _validate(paths)["valid"] is True


def test_duplicate_cycle_increment_is_rejected(tmp_path):
    paths = _fixture(tmp_path, reviewer_open=[_finding("C1-P1-001")])
    receipt = json.loads(paths["cycle_start"].read_text(encoding="utf-8"))
    receipt["cycle_increment_events"].append(
        {"cycle_number": 3, "state": "REVIEWER_A_CLOSURE_REVIEW"}
    )
    _write(paths["cycle_start"], receipt)
    report = _validate(paths)
    assert report["valid"] is False
    assert "G10_CYCLE_INCREMENT_NOT_EXACTLY_ONCE" in report["errors"]


def test_third_consecutive_same_root_must_block(tmp_path):
    history = {"root-repeated": [1, 2, 3]}
    invalid = _fixture(
        tmp_path / "invalid",
        reviewer_open=[_finding("C1-P1-001", root="root-repeated")],
        root_cause_history=history,
        decision="CONTINUE_NEXT_CYCLE",
        cycle=3,
    )
    invalid_report = _validate(invalid)
    assert invalid_report["valid"] is False
    assert "G12_REPEATED_ROOT_CAUSE_MUST_BLOCK" in invalid_report["errors"]

    valid = _fixture(
        tmp_path / "valid",
        reviewer_open=[_finding("C1-P1-001", root="root-repeated")],
        root_cause_history=history,
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
        cycle=3,
    )
    assert _validate(valid)["valid"] is True


@pytest.mark.parametrize("mutation", ["missing_field", "fake_sealed", "hash_mismatch"])
def test_missing_forged_or_hash_mismatched_receipt_fails_closed(tmp_path, mutation):
    paths = _fixture(tmp_path, reviewer_open=[_finding("C1-P1-001")])
    challenger = json.loads(paths["fresh_challenger"].read_text(encoding="utf-8"))
    if mutation == "missing_field":
        challenger.pop("fresh_context")
    elif mutation == "fake_sealed":
        challenger["sealed"] = "true"
    else:
        after = json.loads(paths["after_snapshot"].read_text(encoding="utf-8"))
        after["inventory_sha256"] = "c" * 64
        _write(paths["after_snapshot"], after)
    if mutation != "hash_mismatch":
        _write(paths["fresh_challenger"], challenger)
    report = _validate(paths)
    assert report["valid"] is False
    assert report["recommended_status"] in {
        "INVALID_REVIEW_PROGRAM_TRANSITION",
        "INVALID_SNAPSHOT_CHANGED",
    }
