"""Fail-closed validator for Theme Chokepoint Review Program protocol v1.1."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


SHA256_KEYS = {
    "cycle_start": "cycle_start",
    "before_snapshot": "before_snapshot",
    "after_snapshot": "after_snapshot",
    "reviewer_a": "reviewer_a",
    "fresh_challenger": "fresh_challenger",
    "challenger_adjudication": "challenger_adjudication",
    "exit_auditor": "exit_auditor",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path | str | None, label: str, errors: list[str]) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.is_file():
        errors.append(f"RECEIPT_MISSING:{label}")
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"RECEIPT_INVALID_JSON:{label}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"RECEIPT_NOT_OBJECT:{label}")
        return None
    return payload


def _identity(payload: Mapping[str, Any] | None, key: str) -> tuple[str, str] | None:
    value = payload.get(key) if payload else None
    if not isinstance(value, dict):
        return None
    agent_id = value.get("agent_id")
    session_id = value.get("session_id")
    if not all(isinstance(item, str) and item.strip() for item in (agent_id, session_id)):
        return None
    return agent_id.strip(), session_id.strip()


def _after_snapshot_id(payload: Mapping[str, Any] | None) -> Any:
    if not payload:
        return None
    return payload.get("after_snapshot_id") or payload.get("target", {}).get(
        "after_snapshot_id"
    )


def _finding_list(payload: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    value = payload.get(key, []) if payload else []
    return value if isinstance(value, list) else []


def _valid_findings(
    findings: list[dict[str, Any]], label: str, errors: list[str]
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"RECEIPT_FINDING_INVALID:{label}:{index}")
            continue
        finding_id = finding.get("finding_id")
        severity = finding.get("severity")
        root = finding.get("root_cause") or finding.get("root_cause_fingerprint")
        if (
            not isinstance(finding_id, str)
            or not finding_id.strip()
            or severity not in {"P0", "P1"}
            or not isinstance(root, str)
            or not root.strip()
        ):
            errors.append(f"RECEIPT_FINDING_INVALID:{label}:{index}")
            continue
        valid.append(finding)
    return valid


def _snapshot_binding_errors(
    receipts: Mapping[str, dict[str, Any] | None],
    paths: Mapping[str, Path | str | None],
) -> list[str]:
    errors: list[str] = []
    before = receipts["before_snapshot"] or {}
    after = receipts["after_snapshot"] or {}
    before_id = before.get("snapshot_id")
    after_id = after.get("snapshot_id")
    before_inventory = before.get("inventory_sha256")
    after_inventory = after.get("inventory_sha256")
    if (
        not isinstance(before_id, str)
        or not isinstance(after_id, str)
        or before_id == after_id
        or before.get("snapshot_phase") not in {"start", "before"}
        or after.get("snapshot_phase") != "after"
        or after.get("before_snapshot_id") not in {None, before_id}
        or not isinstance(before_inventory, str)
        or not isinstance(after_inventory, str)
        or len(before_inventory) != 64
        or len(after_inventory) != 64
        or not before_id.endswith(before_inventory)
        or not after_id.endswith(after_inventory)
    ):
        errors.append("G11_INVALID_SNAPSHOT_CHANGED")
        return errors

    after_path = paths.get("after_snapshot")
    if after_path is None or not Path(after_path).is_file():
        errors.append("G11_INVALID_SNAPSHOT_CHANGED")
        return errors
    after_sha = _sha256(Path(after_path))
    for label in (
        "reviewer_a",
        "fresh_challenger",
        "challenger_adjudication",
        "exit_auditor",
    ):
        receipt = receipts.get(label)
        if receipt is None:
            continue
        claimed = receipt.get("after_snapshot_sha256") or receipt.get("target", {}).get(
            "after_snapshot_receipt_sha256"
        )
        if claimed != after_sha:
            errors.append(f"G11_INVALID_SNAPSHOT_CHANGED:{label}")

    adjudication = receipts.get("challenger_adjudication")
    challenger_path = paths.get("fresh_challenger")
    if adjudication is not None and challenger_path is not None:
        if not Path(challenger_path).is_file() or adjudication.get(
            "challenger_receipt_sha256"
        ) != _sha256(Path(challenger_path)):
            errors.append("G11_INVALID_SNAPSHOT_CHANGED:challenger_adjudication")

    decision = receipts.get("cycle_decision") or {}
    bindings = decision.get("bindings")
    if not isinstance(bindings, dict):
        errors.append("G11_INVALID_SNAPSHOT_CHANGED:decision_bindings")
        return errors
    for receipt_key, binding_key in SHA256_KEYS.items():
        path_value = paths.get(receipt_key)
        if path_value is None:
            continue
        path = Path(path_value)
        if not path.is_file() or bindings.get(binding_key) != _sha256(path):
            errors.append(f"G11_INVALID_SNAPSHOT_CHANGED:{binding_key}")
    return errors


def validate_review_program(
    paths: Mapping[str, Path | str | None]
) -> dict[str, Any]:
    """Validate one proposed Cycle transition and return a sealed machine report."""
    errors: list[str] = []
    receipts = {
        key: _load(paths.get(key), key, errors)
        for key in (
            "program",
            "cycle_start",
            "before_snapshot",
            "after_snapshot",
            "reviewer_a",
            "fresh_challenger",
            "challenger_adjudication",
            "exit_auditor",
            "cycle_decision",
        )
    }
    program = receipts["program"] or {}
    cycle_start = receipts["cycle_start"] or {}
    before = receipts["before_snapshot"] or {}
    after = receipts["after_snapshot"] or {}
    reviewer = receipts["reviewer_a"] or {}
    challenger = receipts["fresh_challenger"]
    adjudication = receipts["challenger_adjudication"]
    exit_auditor = receipts["exit_auditor"]
    decision_receipt = receipts["cycle_decision"] or {}

    if (
        program.get("review_program_version") != "1.1"
        or program.get("review_program_id")
        != "theme-chokepoint-evidence-trust-review-v1"
        or decision_receipt.get("review_program_version") != "1.1"
    ):
        errors.append("PROGRAM_VERSION_NOT_V1_1")
    program_id = program.get("review_program_id")
    for label, receipt in receipts.items():
        if label == "program" or receipt is None:
            continue
        if receipt.get("review_program_id") != program_id:
            errors.append(f"PROGRAM_ID_MISMATCH:{label}")

    cycle_number = cycle_start.get("cycle_number")
    cycle_receipts = {
        label: receipt
        for label, receipt in receipts.items()
        if label != "program" and receipt is not None
    }
    if not isinstance(cycle_number, int) or cycle_number < 1:
        errors.append("G1_CYCLE_NUMBER_INVALID")
    for label, receipt in cycle_receipts.items():
        if receipt.get("cycle_number") != cycle_number:
            errors.append(f"G1_CYCLE_NUMBER_MISMATCH:{label}")

    increment_events = cycle_start.get("cycle_increment_events")
    if increment_events is None and isinstance(cycle_number, int):
        increment_events = [
            {"cycle_number": cycle_number, "state": f"CYCLE_{cycle_number}_START"}
        ]
    if (
        not isinstance(increment_events, list)
        or len(increment_events) != 1
        or increment_events[0].get("cycle_number") != cycle_number
        or increment_events[0].get("state") != f"CYCLE_{cycle_number}_START"
    ):
        errors.append("G10_CYCLE_INCREMENT_NOT_EXACTLY_ONCE")

    after_id = after.get("snapshot_id")
    for label in (
        "reviewer_a",
        "fresh_challenger",
        "challenger_adjudication",
        "exit_auditor",
        "cycle_decision",
    ):
        receipt = receipts.get(label)
        if receipt is not None and _after_snapshot_id(receipt) != after_id:
            errors.append(f"G2_AFTER_SNAPSHOT_MISMATCH:{label}")

    if challenger is None:
        errors.append("G6_MISSING_FRESH_CHALLENGER")
    else:
        if challenger.get("sealed") is not True or challenger.get("fresh_context") is not True:
            errors.append("G3_CHALLENGER_NOT_SEALED_FRESH_CONTEXT")
        first_pass = challenger.get("first_pass")
        if (
            not isinstance(first_pass, dict)
            or first_pass.get("old_findings_bound") is not False
            or first_pass.get("closure_ledger_bound") is not False
            or first_pass.get("developer_explanation_bound") is not False
            or first_pass.get("sealed_before_context_expansion") is not True
        ):
            errors.append("G5_CHALLENGER_FIRST_PASS_NOT_BLIND")
        if adjudication is None:
            errors.append("CHALLENGER_ADJUDICATION_REQUIRED")

    identities = {
        "developer": _identity(cycle_start, "developer_identity")
        or _identity(decision_receipt, "developer_identity"),
        "reviewer_a": _identity(reviewer, "reviewer_identity"),
        "challenger": _identity(challenger, "challenger_identity"),
        "adjudicator": _identity(adjudication, "adjudicator_identity"),
    }
    if challenger is not None and any(value is None for value in identities.values()):
        errors.append("G4_ROLE_IDENTITY_INCOMPLETE")
    present_identities = [value for value in identities.values() if value is not None]
    if (
        len({value[0] for value in present_identities}) != len(present_identities)
        or len({value[1] for value in present_identities}) != len(present_identities)
    ):
        errors.append("G4_CHALLENGER_ROLE_NOT_ISOLATED")

    reviewer_open = _valid_findings(
        _finding_list(reviewer, "open_P0_P1"), "reviewer_a", errors
    )
    accepted = _valid_findings(
        _finding_list(adjudication, "accepted_P0_P1"),
        "challenger_adjudication.accepted",
        errors,
    )
    same_root = _valid_findings(
        _finding_list(adjudication, "same_root_cause_different_path"),
        "challenger_adjudication.same_root",
        errors,
    )
    by_finding_id = {
        finding["finding_id"]: finding
        for finding in (*reviewer_open, *accepted, *same_root)
    }
    open_findings = list(by_finding_id.values())

    history = program.get("root_cause_history", {})
    if not isinstance(history, dict):
        errors.append("ROOT_CAUSE_HISTORY_INVALID")
        history = {}
    repeated_roots: list[str] = []
    if isinstance(cycle_number, int):
        expected_tail = [cycle_number - 2, cycle_number - 1, cycle_number]
        for finding in open_findings:
            root = finding.get("root_cause") or finding.get("root_cause_fingerprint")
            cycles = history.get(root, [])
            if isinstance(cycles, list) and cycles[-3:] == expected_tail:
                repeated_roots.append(root)

    decision = decision_receipt.get("decision")
    max_cycles = program.get("max_fix_cycles")
    if open_findings:
        if exit_auditor is None or exit_auditor.get("status") != "NOT_ELIGIBLE_OPEN_FINDINGS":
            errors.append("G7_EXIT_MUST_BE_NOT_ELIGIBLE_OPEN_FINDINGS")
        if repeated_roots:
            if decision != "BLOCKED_REPEATED_ROOT_CAUSE":
                errors.append("G12_REPEATED_ROOT_CAUSE_MUST_BLOCK")
        elif isinstance(max_cycles, int) and isinstance(cycle_number, int) and cycle_number >= max_cycles:
            if decision != "BLOCKED_MAX_CYCLES":
                errors.append("MAX_FIX_CYCLES_MUST_BLOCK")
        elif decision != "CONTINUE_NEXT_CYCLE":
            errors.append("OPEN_FINDINGS_REQUIRE_CONTINUE_NEXT_CYCLE")
    else:
        if (
            exit_auditor is None
            or exit_auditor.get("status") != "FRESH_EXIT_AUDITOR_COMPLETE"
            or exit_auditor.get("sealed") is not True
            or exit_auditor.get("fresh_context") is not True
            or _identity(exit_auditor, "exit_auditor_identity") is None
        ):
            errors.append("G8_FRESH_EXIT_AUDITOR_REQUIRED")
        elif _valid_findings(
            _finding_list(exit_auditor, "accepted_P0_P1"), "exit_auditor", errors
        ):
            errors.append("EXIT_AUDITOR_OPEN_FINDINGS_REQUIRE_NEW_CYCLE")
        if decision != "CANDIDATE_FOR_HOLDOUT":
            errors.append("CLEAN_EXIT_REQUIRED_FOR_HOLDOUT")

    errors.extend(_snapshot_binding_errors(receipts, paths))
    errors = list(dict.fromkeys(errors))
    snapshot_invalid = any(error.startswith("G11_") for error in errors)
    valid = not errors
    return {
        "schema_version": "theme-chokepoint-review-program-validation-report-v1.1",
        "review_program_id": program_id,
        "review_program_version": program.get("review_program_version"),
        "cycle_number": cycle_number,
        "after_snapshot_id": after_id,
        "valid": valid,
        "recommended_status": (
            decision
            if valid
            else (
                "INVALID_SNAPSHOT_CHANGED"
                if snapshot_invalid
                else "INVALID_REVIEW_PROGRAM_TRANSITION"
            )
        ),
        "transition": decision if valid else None,
        "open_P0_P1": sorted(by_finding_id),
        "repeated_root_causes": sorted(set(repeated_roots)),
        "errors": errors,
        "proof_boundary": (
            "This report validates only Review Program v1.1 transition mechanics. "
            "It does not close findings, authorize GO, prove holdout success, "
            "deployment, release, or user-visible delivery."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one Theme Chokepoint Review Program v1.1 transition."
    )
    parser.add_argument("--program", required=True)
    parser.add_argument("--cycle-start", required=True)
    parser.add_argument("--before-snapshot", required=True)
    parser.add_argument("--after-snapshot", required=True)
    parser.add_argument("--reviewer-a", required=True)
    parser.add_argument("--fresh-challenger")
    parser.add_argument("--challenger-adjudication")
    parser.add_argument("--exit-auditor")
    parser.add_argument("--cycle-decision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_review_program(
        {
            "program": args.program,
            "cycle_start": args.cycle_start,
            "before_snapshot": args.before_snapshot,
            "after_snapshot": args.after_snapshot,
            "reviewer_a": args.reviewer_a,
            "fresh_challenger": args.fresh_challenger,
            "challenger_adjudication": args.challenger_adjudication,
            "exit_auditor": args.exit_auditor,
            "cycle_decision": args.cycle_decision,
        }
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
