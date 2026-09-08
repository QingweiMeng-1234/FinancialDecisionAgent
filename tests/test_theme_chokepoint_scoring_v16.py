from __future__ import annotations

import json
from pathlib import Path

import pytest

from event_collector.theme_chokepoint.scoring_v16 import (
    score_qualification_barrier,
)
from event_collector.theme_chokepoint.validation_v16 import score_v16_segment_facts


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("qualification_required", "process_signals", "expected"),
    [
        (None, (), (0, 4)),
        (True, (), (1, 4)),
        (True, ("type_test", "factory_acceptance_test"), (2, 4)),
        (True, ("customer_specific_validation",), (3, 4)),
        (True, ("field_pilot",), (3, 4)),
    ],
)
def test_v16_qualification_process_evidence_sets_conservative_floors(
    qualification_required,
    process_signals,
    expected,
):
    """SELECT INVARIANT: public process evidence can bound qualification without months."""
    result = score_qualification_barrier(
        duration_months=None,
        qualification_required=qualification_required,
        explicit_no_special_qualification=False,
        process_signals=process_signals,
    )

    assert (result.rating_min, result.rating_max) == expected


@pytest.mark.parametrize(
    ("months", "signals", "expected"),
    [
        (2, (), (0, 0)),
        (5, (), (1, 1)),
        (9, (), (2, 2)),
        (15, (), (3, 3)),
        (18, ("type_test", "factory_acceptance_test"), (4, 4)),
        (18, (), (3, 4)),
    ],
)
def test_v16_qualification_duration_preserves_time_anchors_and_four_requires_process(
    months,
    signals,
    expected,
):
    """SELECT INVARIANT: exact duration remains primary and exact four needs process proof."""
    result = score_qualification_barrier(
        duration_months=months,
        qualification_required=True,
        explicit_no_special_qualification=False,
        process_signals=signals,
    )

    assert (result.rating_min, result.rating_max) == expected


def test_v16_qualification_rejects_conflicting_no_qualification_evidence():
    """SELECT INVARIANT: explicit no-gate evidence cannot coexist with a gate process."""
    with pytest.raises(ValueError, match="conflicts"):
        score_qualification_barrier(
            duration_months=None,
            qualification_required=True,
            explicit_no_special_qualification=True,
            process_signals=("type_test",),
        )


def test_v16_fact_validator_mechanically_scores_qualification_process():
    """SELECT INVARIANT: the model supplies facts, never the qualification ordinal."""
    scored = score_v16_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6",
            "qualification_barrier": {
                "duration_months": None,
                "qualification_required": True,
                "explicit_no_special_qualification": False,
                "process_signals": ["design_review", "factory_acceptance_test"],
            },
        },
        dimensions=("qualification_barrier",),
    )

    assert scored == {"qualification_barrier": (2, 4)}


def test_v16_fact_validator_ignores_demand_audit_metadata_when_scoring():
    """SELECT INVARIANT: demand audit metadata is retained outside ordinal inputs."""
    scored = score_v16_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6",
            "demand_pressure": {
                "canonical_annual_growth": 0.15,
                "direct_transmission_verified": True,
                "inventory_verified": False,
                "annual_growth_basis": "quarterly_yoy",
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("demand_pressure",),
    )

    assert scored == {"demand_pressure": (2, 2)}


def test_v16_fact_validator_rejects_model_authored_qualification_rating():
    """SELECT INVARIANT: R6 cannot restore the v1.5 model-authored interval escape hatch."""
    with pytest.raises(ValueError, match="model-authored rating"):
        score_v16_segment_facts(
            {
                "schema_version": "theme-chokepoint-segment-facts-v1.6",
                "qualification_barrier": {
                    "rating_min": 2,
                    "rating_max": 4,
                },
            },
            dimensions=("qualification_barrier",),
        )


def test_v16_schema_exposes_qualification_facts_instead_of_ordinals():
    """SELECT INVARIANT: the v1.6 JSON schema persists observable qualification facts."""
    schema = json.loads(
        (
            ROOT
            / "tools/theme-chokepoint/schemas/theme-chokepoint-segment-facts-v1.6.schema.json"
        ).read_text(encoding="utf-8")
    )
    qualification = schema["properties"]["qualification_barrier"]

    assert schema["$id"] == "theme-chokepoint-segment-facts-v1.6"
    assert schema["properties"]["schema_version"]["const"] == (
        "theme-chokepoint-segment-facts-v1.6"
    )
    assert set(qualification["required"]) == {"unresolved", "evidence_ids"}
    assert set(qualification["allOf"][0]["then"]["required"]) == {
        "duration_months",
        "qualification_required",
        "explicit_no_special_qualification",
        "process_signals",
    }
    assert "rating_min" not in qualification["properties"]
    assert "rating_max" not in qualification["properties"]


def test_v16_machine_contract_declares_dual_path_qualification_semantics():
    """SELECT INVARIANT: the executable contract owns the R6 process proxy ladder."""
    contract = json.loads(
        (
            ROOT / "tools/theme-chokepoint/semantic-task-contract-v1.6.json"
        ).read_text(encoding="utf-8")
    )
    qualification = contract["segment_semantics"]["qualification_barrier"]

    assert contract["contract_id"] == "theme-chokepoint-semantic-task-contract-v1.6"
    assert contract["inherits_scoring_contract"] == "theme-chokepoint-scoring-v1.6"
    assert qualification["model"] == "duration_or_observable_process"
    assert qualification["process_only_floors"] == {
        "formal_requirement": [1, 4],
        "multi_step_process": [2, 4],
        "customer_specific_or_field_validation": [3, 4],
    }
