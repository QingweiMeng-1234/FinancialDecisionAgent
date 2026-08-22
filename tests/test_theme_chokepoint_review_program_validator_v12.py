from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
VALIDATOR_PATH = (
    ROOT / "tools" / "theme-chokepoint" / "validate-review-program-v1_2.py"
)
V11_TEST_SUPPORT_PATH = ROOT / "tests" / "test_theme_chokepoint_review_program_validator.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
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


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _finding_receipt(path: Path, cycle: int, finding_id: str, after_id: str) -> Path:
    return _write(
        path,
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "after_snapshot_id": after_id,
            "findings": [
                {
                    "finding_id": finding_id,
                    "severity": "P1",
                    "root_cause": f"cycle-{cycle}-reported-root",
                }
            ],
        },
    )


def _after_receipt(path: Path, cycle: int) -> Path:
    inventory = str(cycle) * 64
    return _write(
        path,
        {
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": cycle,
            "snapshot_phase": "after",
            "snapshot_id": f"etrp-cycle{cycle}-after-{inventory}",
            "inventory_sha256": inventory,
        },
    )


def _edge(
    *,
    current_cycle: int,
    current_finding: Path,
    current_after: Path,
    prior_cycle: int,
    prior_finding: Path,
    prior_after: Path,
    classification: str,
    root_id: str | None,
) -> dict:
    current_finding_payload = json.loads(current_finding.read_text(encoding="utf-8"))
    prior_finding_payload = json.loads(prior_finding.read_text(encoding="utf-8"))
    current_after_payload = json.loads(current_after.read_text(encoding="utf-8"))
    prior_after_payload = json.loads(prior_after.read_text(encoding="utf-8"))
    current_findings = current_finding_payload.get("findings") or current_finding_payload.get(
        "open_P0_P1", []
    )
    prior_findings = prior_finding_payload.get("findings") or prior_finding_payload.get(
        "open_P0_P1", []
    )
    same = "same" if classification == "SAME_ROOT" else "different"
    return {
        "current_finding_id": current_findings[0]["finding_id"],
        "current_cycle": current_cycle,
        "current_receipt_path": str(current_finding.resolve()),
        "current_receipt_sha256": _sha(current_finding),
        "current_after_snapshot_id": current_after_payload["snapshot_id"],
        "current_after_snapshot_sha256": _sha(current_after),
        "prior_finding_id": prior_findings[0]["finding_id"],
        "prior_cycle": prior_cycle,
        "prior_receipt_path": str(prior_finding.resolve()),
        "prior_receipt_sha256": _sha(prior_finding),
        "prior_after_snapshot_id": prior_after_payload["snapshot_id"],
        "prior_after_snapshot_sha256": _sha(prior_after),
        "five_part_results": {
            "violated_invariant": same,
            "trust_boundary": same,
            "causal_mechanism": same,
            "architecture_remedy": same,
            "counterfactual_explanation": same,
        },
        "classification": classification,
        "evidence_state": "supported",
        "canonical_root_id": root_id,
    }


def _fixture(
    tmp_path: Path,
    *,
    repeated: bool,
    decision: str,
    caller_history: dict[str, list[int]] | None = None,
) -> dict[str, Path | None]:
    support = _load(V11_TEST_SUPPORT_PATH, f"v11_support_{tmp_path.name}")
    paths = support._fixture(
        tmp_path / "cycle",
        reviewer_open=[support._finding("F-OPEN", root="caller-reported-root")],
        root_cause_history=caller_history or {},
        decision=decision,
        cycle=3,
    )

    cycle1_after = _after_receipt(tmp_path / "history" / "cycle1-after.json", 1)
    cycle2_after = _after_receipt(tmp_path / "history" / "cycle2-after.json", 2)
    cycle3_after = paths["after_snapshot"]
    assert cycle3_after is not None
    cycle1_finding = _finding_receipt(
        tmp_path / "history" / "cycle1-finding.json",
        1,
        "F-OPEN",
        json.loads(cycle1_after.read_text(encoding="utf-8"))["snapshot_id"],
    )
    cycle2_finding = _finding_receipt(
        tmp_path / "history" / "cycle2-finding.json",
        2,
        "F-OPEN",
        json.loads(cycle2_after.read_text(encoding="utf-8"))["snapshot_id"],
    )
    cycle3_finding = paths["reviewer_a"]
    assert cycle3_finding is not None

    contract = tmp_path / "rc1-contract.md"
    contract.write_text("RC-1 test contract\n", encoding="utf-8")
    first_pass = _write(tmp_path / "first-pass.json", {"sealed": True})
    second_pass = _write(tmp_path / "second-pass.json", {"sealed": True})
    root_id = "RC1-ROOT-TEST" if repeated else None
    classification = "SAME_ROOT" if repeated else "SAME_FAMILY_DIFFERENT_ROOT"
    edges = [
        _edge(
            current_cycle=2,
            current_finding=cycle2_finding,
            current_after=cycle2_after,
            prior_cycle=1,
            prior_finding=cycle1_finding,
            prior_after=cycle1_after,
            classification=classification,
            root_id=root_id,
        ),
        _edge(
            current_cycle=3,
            current_finding=cycle3_finding,
            current_after=cycle3_after,
            prior_cycle=2,
            prior_finding=cycle2_finding,
            prior_after=cycle2_after,
            classification=classification,
            root_id=root_id,
        ),
    ]
    evidence_paths = [
        cycle1_finding,
        cycle1_after,
        cycle2_finding,
        cycle2_after,
        cycle3_finding,
        cycle3_after,
    ]
    root_adjudication = _write(
        tmp_path / "final-root-adjudication.json",
        {
            "schema_version": "theme-chokepoint-final-root-cause-adjudication-rc1-v1",
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "agent_identity": {
                "role": "Independent Root Cause Adjudicator",
                "task_identity": "agent-root-adjudicator",
                "first_pass_session_identity": "session-root-first",
                "second_pass_session_identity": "session-root-second",
                "distinct_from_developer_reviewer_challenger_exit_roles": True,
            },
            "isolation_receipt": {
                "fresh_context": True,
                "first_pass_blind": True,
                "first_pass_sealed_before_hypothesis_disclosure": True,
                "first_pass_modified": False,
            },
            "input_bindings": {
                "sealed_first_pass": {
                    "path": str(first_pass.resolve()),
                    "sha256": _sha(first_pass),
                },
                "second_pass_difference_analysis": {
                    "path": str(second_pass.resolve()),
                    "sha256": _sha(second_pass),
                },
                "rc1_contract": {
                    "path": str(contract.resolve()),
                    "sha256": _sha(contract),
                },
            },
            "evidence_receipts": [
                {"path": str(path.resolve()), "sha256": _sha(path)}
                for path in evidence_paths
            ],
            "snapshot_bindings": {
                f"cycle{cycle}": {
                    "after_snapshot_id": json.loads(path.read_text(encoding="utf-8"))[
                        "snapshot_id"
                    ],
                    "after_snapshot_sha256": _sha(path),
                }
                for cycle, path in (
                    (1, cycle1_after),
                    (2, cycle2_after),
                    (3, cycle3_after),
                )
            },
            "final_group_adjudications": [
                {
                    "group_id": "RC1-GROUP-TEST",
                    "classification": classification,
                    "evidence_state": "supported",
                    "canonical_root_id": root_id,
                    "cycle_presence": (
                        {"cycle1": "PRESENT", "cycle2": "PRESENT", "cycle3": "PRESENT"}
                        if repeated
                        else None
                    ),
                    "equivalence_edges": edges,
                }
            ],
            "sealed": True,
        },
    )
    program = paths["program"]
    assert program is not None
    program_binding = _write(
        tmp_path / "program-binding.json",
        {
            "schema_version": "theme-chokepoint-review-program-correction-binding-v1.2",
            "review_program_id": "theme-chokepoint-evidence-trust-review-v1",
            "cycle_number": 3,
            "historical_correction": True,
            "canonical_program": {
                "path": str(program.resolve()),
                "schema_version": "theme-chokepoint-review-program-protocol-v1.1",
                "sha256": _sha(program),
            },
            "validator": {
                "path": str(VALIDATOR_PATH.resolve()),
                "sha256": _sha(VALIDATOR_PATH),
            },
            "rc1_contract": {
                "path": str(contract.resolve()),
                "sha256": _sha(contract),
            },
            "root_cause_adjudication": {
                "path": str(root_adjudication.resolve()),
                "schema_version": "theme-chokepoint-final-root-cause-adjudication-rc1-v1",
                "sha256": _sha(root_adjudication),
            },
        },
    )
    cycle_decision = paths["cycle_decision"]
    assert cycle_decision is not None
    decision_payload = json.loads(cycle_decision.read_text(encoding="utf-8"))
    decision_payload["bindings"].update(
        {
            "program": _sha(program),
            "program_binding": _sha(program_binding),
            "rc1_contract": _sha(contract),
            "root_cause_adjudication": _sha(root_adjudication),
        }
    )
    decision_payload["repeated_root_causes"] = [root_id] if repeated else []
    _write(cycle_decision, decision_payload)
    paths.update(
        {
            "program_binding": program_binding,
            "rc1_contract": contract,
            "root_cause_adjudication": root_adjudication,
        }
    )
    return paths


def _validate(paths: dict[str, Path | None]) -> dict:
    return _load(VALIDATOR_PATH, "review_validator_v12").validate_review_program_v12(paths)


def test_bound_supported_adjudication_derives_repeated_root_and_allows_block(tmp_path):
    report = _validate(
        _fixture(
            tmp_path,
            repeated=True,
            decision="BLOCKED_REPEATED_ROOT_CAUSE",
        )
    )
    assert report["valid"] is True
    assert report["transition"] == "BLOCKED_REPEATED_ROOT_CAUSE"
    assert report["repeated_root_causes"] == ["RC1-ROOT-TEST"]


def test_adjudication_cannot_be_substituted_for_canonical_program(tmp_path):
    paths = _fixture(
        tmp_path,
        repeated=True,
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
    )
    paths["program"] = paths["root_cause_adjudication"]
    report = _validate(paths)
    assert report["valid"] is False
    assert any(error.startswith("V12_CANONICAL_PROGRAM_") for error in report["errors"])


def test_caller_reported_history_cannot_create_recurrence(tmp_path):
    paths = _fixture(
        tmp_path,
        repeated=False,
        caller_history={"caller-reported-root": [1, 2, 3]},
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
    )
    report = _validate(paths)
    assert report["valid"] is False
    assert report["repeated_root_causes"] == []
    assert "OPEN_FINDINGS_REQUIRE_CONTINUE_NEXT_CYCLE" in report["errors"]


def test_supported_three_cycle_root_cannot_continue(tmp_path):
    paths = _fixture(
        tmp_path,
        repeated=True,
        decision="CONTINUE_NEXT_CYCLE",
    )
    report = _validate(paths)
    assert report["valid"] is False
    assert "G12_REPEATED_ROOT_CAUSE_MUST_BLOCK" in report["errors"]


def test_missing_or_mutated_root_adjudication_fails_closed(tmp_path):
    missing = _fixture(
        tmp_path / "missing",
        repeated=True,
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
    )
    missing["root_cause_adjudication"] = None
    missing_report = _validate(missing)
    assert missing_report["valid"] is False
    assert "V12_ROOT_ADJUDICATION_REQUIRED" in missing_report["errors"]

    mutated = _fixture(
        tmp_path / "mutated",
        repeated=True,
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
    )
    root_path = mutated["root_cause_adjudication"]
    assert root_path is not None
    payload = json.loads(root_path.read_text(encoding="utf-8"))
    payload["sealed"] = False
    _write(root_path, payload)
    mutated_report = _validate(mutated)
    assert mutated_report["valid"] is False
    assert {
        "V12_ROOT_ADJUDICATION_HASH_MISMATCH",
        "V12_ROOT_ADJUDICATION_NOT_SEALED",
    } & set(mutated_report["errors"])


def test_cycle_decision_must_bind_program_contract_and_adjudication(tmp_path):
    paths = _fixture(
        tmp_path,
        repeated=True,
        decision="BLOCKED_REPEATED_ROOT_CAUSE",
    )
    decision = paths["cycle_decision"]
    assert decision is not None
    payload = json.loads(decision.read_text(encoding="utf-8"))
    payload["bindings"].pop("root_cause_adjudication")
    _write(decision, payload)
    report = _validate(paths)
    assert report["valid"] is False
    assert "V12_DECISION_BINDING_MISMATCH:root_cause_adjudication" in report["errors"]
