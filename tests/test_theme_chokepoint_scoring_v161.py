from __future__ import annotations

import pytest

from event_collector.theme_chokepoint.validation_v161 import (
    score_v161_segment_facts,
)


def test_v161_positive_canonical_demand_without_inventory_stays_lower_bounded():
    """SELECT INVARIANT: missing inventory proof blocks exact positive demand."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "demand_pressure": {
                "unresolved": False,
                "canonical_annual_growth": 0.24,
                "direct_transmission_verified": True,
                "inventory_verified": False,
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("demand_pressure",),
    )

    assert scored == {"demand_pressure": (3, 4)}


def test_v161_attributed_prebooked_capacity_growth_sets_demand_proxy_floor():
    """SELECT INVARIANT: an attributed, prebooked annual proxy sets only a floor."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "demand_pressure": {
                "unresolved": False,
                "canonical_annual_growth": None,
                "direct_transmission_verified": False,
                "inventory_verified": False,
                "demand_proxy_annual_growth": 0.30,
                "demand_proxy_directly_attributed": True,
                "demand_proxy_capacity_prebooked": True,
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("demand_pressure",),
    )

    assert scored == {"demand_pressure": (3, 4)}


def test_v161_verified_scope_dependency_mechanically_sets_structural_block():
    """SELECT INVARIANT: a required Segment dependency owns the removal counterfactual."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "downstream_criticality": {
                "unresolved": False,
                "scope_dependency_verified": True,
                "structural_hard_block": "unknown",
                "explicit_no_impact": False,
                "cohorts": [],
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("downstream_criticality",),
    )

    assert scored == {"downstream_criticality": (4, 4)}


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"sole_effective_supplier_verified": True}, (4, 4)),
        ({"qualified_failover_absence_verified": True}, (2, 4)),
        ({"effective_supplier_count_upper_bound": 3}, (1, 4)),
    ],
)
def test_v161_concentration_observable_proxy_ladder(facts, expected):
    """SELECT INVARIANT: observable supply structure sets conservative bounds."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "effective_supply_concentration": {
                "unresolved": False,
                "target_demand": None,
                "suppliers": [],
                "sole_effective_supplier_verified": False,
                "qualified_failover_absence_verified": False,
                "effective_supplier_count_upper_bound": None,
                "evidence_ids": ["E1"],
                **facts,
            },
        },
        dimensions=("effective_supply_concentration",),
    )

    assert scored == {"effective_supply_concentration": expected}


def test_v161_empty_resolved_concentration_facts_degrade_to_unknown():
    """SELECT INVARIANT: an empty resolved claim cannot crash or invent supply data."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "effective_supply_concentration": {
                "unresolved": False,
                "target_demand": None,
                "suppliers": [],
                "sole_effective_supplier_verified": False,
                "qualified_failover_absence_verified": False,
                "effective_supplier_count_upper_bound": None,
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("effective_supply_concentration",),
    )

    assert scored == {"effective_supply_concentration": (0, 4)}


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            {
                "physical_expansion_required": True,
                "physical_constraint_signals": ["cleanroom", "advanced_packaging"],
            },
            (2, 4),
        ),
        ({"minimum_physical_lead_time_months": 12}, (3, 4)),
    ],
)
def test_v161_capacity_observable_proxy_ladder(facts, expected):
    """SELECT INVARIANT: public physical clocks set floors without fake output data."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "capacity_inelasticity": {
                "unresolved": False,
                "top1_loss_output": None,
                "failover_output_90d": None,
                "stable_incremental_output": None,
                "tasks": [],
                "minimum_physical_lead_time_months": None,
                "physical_expansion_required": False,
                "physical_constraint_signals": [],
                "evidence_ids": ["E1"],
                **facts,
            },
        },
        dimensions=("capacity_inelasticity",),
    )

    assert scored == {"capacity_inelasticity": expected}


def test_v161_credible_substitute_with_unknown_coverage_sets_ceiling_only():
    """SELECT INVARIANT: a real route without comparable output excludes scores 3-4."""
    scored = score_v161_segment_facts(
        {
            "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
            "substitute_weakness": {
                "unresolved": False,
                "target_demand": None,
                "discovery_rounds_without_new_routes": 2,
                "required_categories": ["different_technology"],
                "searched_categories": ["different_technology"],
                "explicit_negative_categories": [],
                "budget_exhausted": False,
                "routes": [
                    {
                        "route_id": "route-1",
                        "status": "production",
                        "qualified_output": None,
                        "category": "different_technology",
                    }
                ],
                "evidence_ids": ["E1"],
            },
        },
        dimensions=("substitute_weakness",),
    )

    assert scored == {"substitute_weakness": (0, 2)}
