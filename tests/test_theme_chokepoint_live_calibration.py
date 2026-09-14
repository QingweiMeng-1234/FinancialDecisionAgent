"""Opt-in assertions over preserved real-model calibration output.

Set THEME_GOLDEN_RUN_DIR to a completed live run; no network calls happen here.
These tests prove the selected run only, not repeatability or unseen performance.
"""
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001"


@pytest.fixture
def live_result():
    run_dir = os.environ.get("THEME_GOLDEN_RUN_DIR")
    if not run_dir:
        pytest.skip("set THEME_GOLDEN_RUN_DIR to a completed real-model run")
    run = Path(run_dir)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["completion_events"]
    assert manifest["reference_supplied"] is False
    assert manifest["item_types"] == ["agent_message"]
    filename = "computed-response.json" if manifest.get("inference_mode") == "facts_v161" else "response.json"
    return json.loads((run / filename).read_text(encoding="utf-8"))


def test_live_structural_dependency_does_not_require_top1_outage_measurement(live_result):
    """Whole-route structural blocks remain exact 4 without 90-day cohort data."""
    task = json.loads((FROZEN / "post-adjudication-blind-package/calibration/annotation-task-v1.6.1.json").read_text(encoding="utf-8"))
    reference = json.loads((FROZEN / "adjudication/adjudicated-calibration-result-v1.6.1.json").read_text(encoding="utf-8"))
    expected = {item["item_id"]: item for item in reference["ordinal"]}
    actual = {item["item_id"]: item for item in live_result["ordinal"]}
    fields = ("evidence_state", "bound_type", "rating_min", "rating_max")
    mismatches = {}
    for item in task["ordinal_tasks"]:
        if item["dimension"] != "downstream_criticality":
            continue
        key = item["item_id"]
        want = tuple(expected[key][field] for field in fields)
        got = tuple(actual[key][field] for field in fields)
        if got != want:
            mismatches[key] = {"expected": want, "actual": got, "rationale": actual[key]["rationale"]}
    assert not mismatches, mismatches
