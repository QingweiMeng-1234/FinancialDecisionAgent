"""RC-1 fail-closed correction validator for Review Program transitions.

Version 1.2 preserves the frozen v1.1 validator and adds canonical program
binding plus independently adjudicated root-cause history derivation.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


PROGRAM_ID = "theme-chokepoint-evidence-trust-review-v1"
PROGRAM_SCHEMA = "theme-chokepoint-review-program-protocol-v1.1"
BINDING_SCHEMA = "theme-chokepoint-review-program-correction-binding-v1.2"
ROOT_ADJUDICATION_SCHEMA = "theme-chokepoint-final-root-cause-adjudication-rc1-v1"
ALLOWED_CLASSIFICATIONS = {
    "SAME_ROOT",
    "SAME_FAMILY_DIFFERENT_ROOT",
    "NEW_ROOT",
    "DUPLICATE_SAME_CYCLE",
    "UNKNOWN",
    "CONFLICTED",
}
FIVE_PART_KEYS = {
    "violated_invariant",
    "trust_boundary",
    "causal_mechanism",
    "architecture_remedy",
    "counterfactual_explanation",
}
EXTRA_DECISION_BINDINGS = {
    "program": "program",
    "program_binding": "program_binding",
    "rc1_contract": "rc1_contract",
    "root_cause_adjudication": "root_cause_adjudication",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _resolve(path: Path | str) -> Path:
    return Path(path).resolve()


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(str(_resolve(left))) == os.path.normcase(str(_resolve(right)))


def _load_json(
    path_value: Path | str | None, label: str, errors: list[str]
) -> dict[str, Any] | None:
    if path_value is None:
        errors.append(f"V12_{label.upper()}_REQUIRED")
        return None
    path = Path(path_value)
    if not path.is_file():
        errors.append(f"V12_{label.upper()}_MISSING")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"V12_{label.upper()}_INVALID_JSON")
        return None
    if not isinstance(value, dict):
        errors.append(f"V12_{label.upper()}_NOT_OBJECT")
        return None
    return value


def _load_v11_validator():
    path = Path(__file__).with_name("validate-review-program-v1_1.py")
    spec = importlib.util.spec_from_file_location("theme_review_validator_v11", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen v1.1 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity_values(payload: Mapping[str, Any] | None, key: str) -> set[str]:
    if not payload:
        return set()
    identity = payload.get(key)
    if not isinstance(identity, dict):
        return set()
    return {
        value.strip()
        for value in identity.values()
        if isinstance(value, str) and value.strip()
    }


def _finding_ids(payload: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("findings", "open_P0_P1", "accepted_P0_P1", "same_root_cause_different_path"):
        values = payload.get(key, [])
        if not isinstance(values, list):
            continue
        for finding in values:
            if isinstance(finding, dict) and isinstance(finding.get("finding_id"), str):
                ids.add(finding["finding_id"])
    return ids


def _open_findings_by_id(
    reviewer: Mapping[str, Any], adjudication: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    sources = [reviewer.get("open_P0_P1", [])]
    if adjudication:
        sources.extend(
            [
                adjudication.get("accepted_P0_P1", []),
                adjudication.get("same_root_cause_different_path", []),
            ]
        )
    for values in sources:
        if not isinstance(values, list):
            continue
        for finding in values:
            if isinstance(finding, dict) and isinstance(finding.get("finding_id"), str):
                result[finding["finding_id"]] = finding
    return result


def _validate_bound_file(
    binding: Mapping[str, Any] | None,
    actual_path: Path | str | None,
    label: str,
    errors: list[str],
    *,
    expected_schema: str | None = None,
) -> None:
    prefix = f"V12_{label.upper()}"
    if not isinstance(binding, dict) or actual_path is None:
        errors.append(f"{prefix}_BINDING_MISSING")
        return
    bound_path = binding.get("path")
    bound_sha = binding.get("sha256")
    actual = Path(actual_path)
    if not isinstance(bound_path, str) or not _same_path(bound_path, actual):
        errors.append(f"{prefix}_PATH_MISMATCH")
    if not actual.is_file() or not isinstance(bound_sha, str) or _sha256(actual) != bound_sha:
        errors.append(f"{prefix}_HASH_MISMATCH")
    if expected_schema is not None and binding.get("schema_version") != expected_schema:
        errors.append(f"{prefix}_SCHEMA_BINDING_MISMATCH")


def _validate_program_binding(
    paths: Mapping[str, Path | str | None],
    program: Mapping[str, Any],
    binding: Mapping[str, Any] | None,
    root_adjudication: Mapping[str, Any] | None,
    errors: list[str],
) -> None:
    if binding is None:
        return
    if binding.get("schema_version") != BINDING_SCHEMA:
        errors.append("V12_PROGRAM_BINDING_SCHEMA_INVALID")
    if binding.get("review_program_id") != PROGRAM_ID:
        errors.append("V12_PROGRAM_BINDING_ID_INVALID")
    cycle_start = _load_json(paths.get("cycle_start"), "cycle_start_binding_check", errors)
    if cycle_start and binding.get("cycle_number") != cycle_start.get("cycle_number"):
        errors.append("V12_PROGRAM_BINDING_CYCLE_MISMATCH")
    if binding.get("historical_correction") is not True:
        errors.append("V12_PROGRAM_BINDING_NOT_HISTORICAL_CORRECTION")

    _validate_bound_file(
        binding.get("canonical_program"),
        paths.get("program"),
        "canonical_program",
        errors,
        expected_schema=PROGRAM_SCHEMA,
    )
    if program.get("schema_version") != PROGRAM_SCHEMA:
        errors.append("V12_CANONICAL_PROGRAM_SCHEMA_INVALID")
    if program.get("review_program_id") != PROGRAM_ID:
        errors.append("V12_CANONICAL_PROGRAM_ID_INVALID")

    _validate_bound_file(
        binding.get("validator"),
        Path(__file__),
        "validator",
        errors,
    )
    _validate_bound_file(
        binding.get("rc1_contract"),
        paths.get("rc1_contract"),
        "rc1_contract",
        errors,
    )
    _validate_bound_file(
        binding.get("root_cause_adjudication"),
        paths.get("root_cause_adjudication"),
        "root_adjudication",
        errors,
        expected_schema=ROOT_ADJUDICATION_SCHEMA,
    )
    if root_adjudication and root_adjudication.get("schema_version") != ROOT_ADJUDICATION_SCHEMA:
        errors.append("V12_ROOT_ADJUDICATION_SCHEMA_INVALID")


def _validate_reference(
    reference: Mapping[str, Any] | None,
    label: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(reference, dict):
        errors.append(f"V12_ROOT_ADJUDICATION_INPUT_MISSING:{label}")
        return None
    path_value = reference.get("path")
    digest = reference.get("sha256")
    if not isinstance(path_value, str):
        errors.append(f"V12_ROOT_ADJUDICATION_INPUT_PATH_INVALID:{label}")
        return None
    path = Path(path_value)
    if not path.is_file() or not isinstance(digest, str) or _sha256(path) != digest:
        errors.append(f"V12_ROOT_ADJUDICATION_INPUT_HASH_MISMATCH:{label}")
        return None
    return path


def _validate_root_adjudication(
    root: Mapping[str, Any] | None,
    paths: Mapping[str, Path | str | None],
    role_receipts: Mapping[str, Mapping[str, Any] | None],
    cycle_number: int | None,
    errors: list[str],
) -> tuple[dict[str, list[int]], dict[str, str]]:
    histories: dict[str, set[int]] = {}
    current_finding_roots: dict[str, str] = {}
    if root is None:
        return {}, {}
    start_error_count = len(errors)
    if root.get("schema_version") != ROOT_ADJUDICATION_SCHEMA:
        errors.append("V12_ROOT_ADJUDICATION_SCHEMA_INVALID")
    if root.get("review_program_id") != PROGRAM_ID:
        errors.append("V12_ROOT_ADJUDICATION_ID_INVALID")
    if root.get("sealed") is not True:
        errors.append("V12_ROOT_ADJUDICATION_NOT_SEALED")

    isolation = root.get("isolation_receipt")
    if not isinstance(isolation, dict) or any(
        isolation.get(key) is not expected
        for key, expected in {
            "fresh_context": True,
            "first_pass_blind": True,
            "first_pass_sealed_before_hypothesis_disclosure": True,
            "first_pass_modified": False,
        }.items()
    ):
        errors.append("V12_ROOT_ADJUDICATION_ISOLATION_INVALID")
    identity = root.get("agent_identity")
    if not isinstance(identity, dict):
        errors.append("V12_ROOT_ADJUDICATOR_IDENTITY_INVALID")
    else:
        root_identity_values = {
            identity.get("task_identity"),
            identity.get("first_pass_session_identity"),
            identity.get("second_pass_session_identity"),
        }
        if not all(isinstance(value, str) and value.strip() for value in root_identity_values):
            errors.append("V12_ROOT_ADJUDICATOR_IDENTITY_INVALID")
        elif len(root_identity_values) != 3:
            errors.append("V12_ROOT_ADJUDICATOR_SESSIONS_NOT_DISTINCT")
        prior_identity_values: set[str] = set()
        for key, receipt in role_receipts.items():
            prior_identity_values.update(_identity_values(receipt, key))
        if root_identity_values & prior_identity_values:
            errors.append("V12_ROOT_ADJUDICATOR_ROLE_NOT_ISOLATED")

    input_bindings = root.get("input_bindings")
    if not isinstance(input_bindings, dict):
        errors.append("V12_ROOT_ADJUDICATION_INPUT_BINDINGS_INVALID")
        input_bindings = {}
    _validate_reference(input_bindings.get("sealed_first_pass"), "first_pass", errors)
    _validate_reference(
        input_bindings.get("second_pass_difference_analysis"), "second_pass", errors
    )
    contract_path = _validate_reference(input_bindings.get("rc1_contract"), "rc1_contract", errors)
    if contract_path is not None and paths.get("rc1_contract") is not None:
        if not _same_path(contract_path, paths["rc1_contract"]):
            errors.append("V12_ROOT_ADJUDICATION_CONTRACT_PATH_MISMATCH")

    evidence = root.get("evidence_receipts")
    evidence_by_path: dict[str, str] = {}
    evidence_hashes: set[str] = set()
    if not isinstance(evidence, list) or not evidence:
        errors.append("V12_ROOT_ADJUDICATION_EVIDENCE_EMPTY")
        evidence = []
    for index, item in enumerate(evidence):
        path = _validate_reference(item, f"evidence_{index}", errors)
        if path is not None and isinstance(item, dict):
            digest = item.get("sha256")
            normalized = os.path.normcase(str(path.resolve()))
            evidence_by_path[normalized] = digest
            evidence_hashes.add(digest)

    after = _load_json(paths.get("after_snapshot"), "after_snapshot_v12", errors) or {}
    snapshots = root.get("snapshot_bindings")
    current_snapshot = snapshots.get(f"cycle{cycle_number}") if isinstance(snapshots, dict) else None
    if (
        not isinstance(current_snapshot, dict)
        or current_snapshot.get("after_snapshot_id") != after.get("snapshot_id")
        or paths.get("after_snapshot") is None
        or current_snapshot.get("after_snapshot_sha256") != _sha256(Path(paths["after_snapshot"]))
    ):
        errors.append("V12_ROOT_ADJUDICATION_CURRENT_SNAPSHOT_MISMATCH")

    groups = root.get("final_group_adjudications")
    if not isinstance(groups, list) or not groups:
        errors.append("V12_ROOT_ADJUDICATION_GROUPS_EMPTY")
        groups = []
    group_ids: set[str] = set()
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            errors.append(f"V12_ROOT_GROUP_INVALID:{group_index}")
            continue
        group_id = group.get("group_id")
        classification = group.get("classification")
        evidence_state = group.get("evidence_state")
        canonical_root = group.get("canonical_root_id")
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            errors.append(f"V12_ROOT_GROUP_ID_INVALID:{group_index}")
        else:
            group_ids.add(group_id)
        if classification not in ALLOWED_CLASSIFICATIONS:
            errors.append(f"V12_ROOT_GROUP_CLASSIFICATION_INVALID:{group_id}")
        if evidence_state not in {"supported", "unknown", "conflicted"}:
            errors.append(f"V12_ROOT_GROUP_EVIDENCE_STATE_INVALID:{group_id}")
        if classification == "SAME_ROOT" and evidence_state == "supported":
            if not isinstance(canonical_root, str) or not canonical_root:
                errors.append(f"V12_ROOT_GROUP_CANONICAL_ID_MISSING:{group_id}")
            else:
                presence = group.get("cycle_presence")
                if not isinstance(presence, dict):
                    errors.append(f"V12_ROOT_GROUP_PRESENCE_INVALID:{group_id}")
                else:
                    for key, state in presence.items():
                        if (
                            isinstance(key, str)
                            and key.startswith("cycle")
                            and key[5:].isdigit()
                            and state == "PRESENT"
                        ):
                            histories.setdefault(canonical_root, set()).add(int(key[5:]))

        edges = group.get("equivalence_edges")
        if not isinstance(edges, list):
            errors.append(f"V12_ROOT_GROUP_EDGES_INVALID:{group_id}")
            continue
        for edge_index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f"V12_ROOT_EDGE_INVALID:{group_id}:{edge_index}")
                continue
            edge_class = edge.get("classification")
            edge_state = edge.get("evidence_state")
            current_cycle = edge.get("current_cycle")
            prior_cycle = edge.get("prior_cycle")
            current_id = edge.get("current_finding_id")
            prior_id = edge.get("prior_finding_id")
            edge_root = edge.get("canonical_root_id")
            if edge_class not in ALLOWED_CLASSIFICATIONS or edge_state not in {
                "supported",
                "unknown",
                "conflicted",
            }:
                errors.append(f"V12_ROOT_EDGE_CLASSIFICATION_INVALID:{group_id}:{edge_index}")
            if not isinstance(current_cycle, int) or not isinstance(prior_cycle, int) or current_cycle <= prior_cycle:
                errors.append(f"V12_ROOT_EDGE_CYCLES_INVALID:{group_id}:{edge_index}")

            for side, finding_id in (("current", current_id), ("prior", prior_id)):
                receipt_path = edge.get(f"{side}_receipt_path")
                receipt_sha = edge.get(f"{side}_receipt_sha256")
                if not isinstance(receipt_path, str):
                    errors.append(f"V12_ROOT_EDGE_RECEIPT_PATH_INVALID:{group_id}:{edge_index}:{side}")
                    continue
                normalized = os.path.normcase(str(Path(receipt_path).resolve()))
                if evidence_by_path.get(normalized) != receipt_sha:
                    errors.append(f"V12_ROOT_EDGE_RECEIPT_NOT_BOUND:{group_id}:{edge_index}:{side}")
                    continue
                receipt = _load_json(receipt_path, f"root_edge_{group_id}_{edge_index}_{side}", errors)
                if receipt is None or finding_id not in _finding_ids(receipt):
                    errors.append(f"V12_ROOT_EDGE_FINDING_NOT_PRESENT:{group_id}:{edge_index}:{side}")
                expected_cycle = current_cycle if side == "current" else prior_cycle
                if receipt is not None and receipt.get("cycle_number") != expected_cycle:
                    errors.append(f"V12_ROOT_EDGE_RECEIPT_CYCLE_MISMATCH:{group_id}:{edge_index}:{side}")
                after_sha = edge.get(f"{side}_after_snapshot_sha256")
                if after_sha not in evidence_hashes:
                    errors.append(f"V12_ROOT_EDGE_SNAPSHOT_NOT_BOUND:{group_id}:{edge_index}:{side}")

            if edge_class == "SAME_ROOT" and edge_state == "supported":
                five = edge.get("five_part_results")
                if (
                    not isinstance(five, dict)
                    or set(five) != FIVE_PART_KEYS
                    or any(five.get(key) != "same" for key in FIVE_PART_KEYS)
                    or not isinstance(edge_root, str)
                    or not edge_root
                ):
                    errors.append(f"V12_ROOT_EDGE_FIVE_PART_NOT_SAME:{group_id}:{edge_index}")
                else:
                    histories.setdefault(edge_root, set()).update((prior_cycle, current_cycle))
                    if current_cycle == cycle_number and isinstance(current_id, str):
                        current_finding_roots[current_id] = edge_root

    if len(errors) != start_error_count:
        return {}, {}
    return (
        {root_id: sorted(cycles) for root_id, cycles in histories.items()},
        current_finding_roots,
    )


def _decision_binding_errors(
    paths: Mapping[str, Path | str | None], decision: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    bindings = decision.get("bindings")
    if not isinstance(bindings, dict):
        return ["V12_DECISION_BINDINGS_INVALID"]
    for path_key, binding_key in EXTRA_DECISION_BINDINGS.items():
        path_value = paths.get(path_key)
        if (
            path_value is None
            or not Path(path_value).is_file()
            or bindings.get(binding_key) != _sha256(Path(path_value))
        ):
            errors.append(f"V12_DECISION_BINDING_MISMATCH:{binding_key}")
    return errors


def validate_review_program_v12(
    paths: Mapping[str, Path | str | None]
) -> dict[str, Any]:
    """Validate an RC-1 correction transition without trusting caller history."""
    errors: list[str] = []
    program = _load_json(paths.get("program"), "canonical_program", errors) or {}
    binding = _load_json(paths.get("program_binding"), "program_binding", errors)
    root = _load_json(paths.get("root_cause_adjudication"), "root_adjudication", errors)
    rc1_contract = paths.get("rc1_contract")
    if rc1_contract is None or not Path(rc1_contract).is_file():
        errors.append("V12_RC1_CONTRACT_REQUIRED")

    cycle_start = _load_json(paths.get("cycle_start"), "cycle_start", errors) or {}
    reviewer = _load_json(paths.get("reviewer_a"), "reviewer_a", errors) or {}
    challenger = _load_json(paths.get("fresh_challenger"), "fresh_challenger", errors)
    challenger_adjudication = _load_json(
        paths.get("challenger_adjudication"), "challenger_adjudication", errors
    )
    decision = _load_json(paths.get("cycle_decision"), "cycle_decision", errors) or {}

    _validate_program_binding(paths, program, binding, root, errors)
    histories, current_finding_roots = _validate_root_adjudication(
        root,
        paths,
        {
            "developer_identity": cycle_start,
            "reviewer_identity": reviewer,
            "challenger_identity": challenger,
            "adjudicator_identity": challenger_adjudication,
        },
        cycle_start.get("cycle_number") if isinstance(cycle_start.get("cycle_number"), int) else None,
        errors,
    )
    errors.extend(_decision_binding_errors(paths, decision))

    open_findings = _open_findings_by_id(reviewer, challenger_adjudication)
    derived_reported_history: dict[str, list[int]] = {}
    for finding_id, finding in open_findings.items():
        canonical_root = current_finding_roots.get(finding_id)
        reported_root = finding.get("root_cause") or finding.get("root_cause_fingerprint")
        if canonical_root and isinstance(reported_root, str):
            derived_reported_history[reported_root] = histories.get(canonical_root, [])

    sanitized_program = dict(program)
    sanitized_program["root_cause_history"] = derived_reported_history
    with tempfile.TemporaryDirectory(prefix="theme-review-v12-") as temp_dir:
        sanitized_path = Path(temp_dir) / "canonical-program-derived-history.json"
        sanitized_path.write_text(
            json.dumps(sanitized_program, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        base_paths = dict(paths)
        base_paths["program"] = sanitized_path
        base_report = _load_v11_validator().validate_review_program(base_paths)

    cycle_number = cycle_start.get("cycle_number")
    expected_tail = (
        [cycle_number - 2, cycle_number - 1, cycle_number]
        if isinstance(cycle_number, int)
        else []
    )
    current_root_ids = {
        current_finding_roots[finding_id]
        for finding_id in open_findings
        if finding_id in current_finding_roots
    }
    repeated_roots = sorted(
        root_id
        for root_id in current_root_ids
        if histories.get(root_id, [])[-3:] == expected_tail
    )
    declared_repeated = decision.get("repeated_root_causes")
    if declared_repeated != repeated_roots:
        errors.append("V12_DECISION_REPEATED_ROOTS_MISMATCH")

    errors.extend(base_report.get("errors", []))
    errors = list(dict.fromkeys(errors))
    valid = not errors
    return {
        "schema_version": "theme-chokepoint-review-program-validation-report-v1.2",
        "review_program_id": PROGRAM_ID,
        "review_program_version": "1.1",
        "validator_contract_version": "1.2",
        "cycle_number": cycle_number,
        "after_snapshot_id": base_report.get("after_snapshot_id"),
        "valid": valid,
        "recommended_status": (
            decision.get("decision") if valid else "INVALID_REVIEW_PROGRAM_TRANSITION"
        ),
        "transition": decision.get("decision") if valid else None,
        "open_P0_P1": base_report.get("open_P0_P1", []),
        "derived_root_cause_history": histories,
        "repeated_root_causes": repeated_roots,
        "caller_reported_program_history_ignored": True,
        "errors": errors,
        "proof_boundary": (
            "This v1.2 report validates the append-only RC-1 correction transition. "
            "It does not close findings, authorize Cycle 4, Holdout, deployment, "
            "release, GO or user-visible delivery."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an RC-1 Theme Chokepoint correction transition."
    )
    parser.add_argument("--program", required=True)
    parser.add_argument("--program-binding", required=True)
    parser.add_argument("--rc1-contract", required=True)
    parser.add_argument("--root-cause-adjudication", required=True)
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
    report = validate_review_program_v12(
        {
            "program": args.program,
            "program_binding": args.program_binding,
            "rc1_contract": args.rc1_contract,
            "root_cause_adjudication": args.root_cause_adjudication,
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
