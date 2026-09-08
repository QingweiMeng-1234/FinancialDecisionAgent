"""Validate v1.5 model-extracted facts and delegate ordinal mapping to code."""

from __future__ import annotations

from event_collector.theme_chokepoint.scoring_v15 import (
    CapacityTask,
    DownstreamImpactCohort,
    OrdinalResult,
    SubstituteRoute,
    SupplierSupply,
    score_capacity_inelasticity,
    score_demand_pressure,
    score_downstream_criticality,
    score_effective_supply_concentration,
    score_substitute_weakness,
)


SCHEMA_VERSION = "theme-chokepoint-segment-facts-v1.5"
DIMENSIONS = (
    "demand_pressure",
    "downstream_criticality",
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
)
_FORBIDDEN_MODEL_SCORE_KEYS = {
    "rating",
    "score",
    "bound_type",
    "primary_state",
}


def score_v15_segment_facts(payload: dict) -> dict[str, tuple[int, int]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("v1.5 Segment fact schema version is invalid")
    if _contains_forbidden_score(payload):
        raise ValueError("v1.5 fact payload contains a model-authored rating")
    missing = [dimension for dimension in DIMENSIONS if dimension not in payload]
    if missing:
        raise ValueError("v1.5 Segment fact payload is incomplete: " + missing[0])

    demand = payload["demand_pressure"]
    downstream = payload["downstream_criticality"]
    concentration = payload["effective_supply_concentration"]
    qualification = payload["qualification_barrier"]
    capacity = payload["capacity_inelasticity"]
    substitute = payload["substitute_weakness"]
    try:
        results = {
            "demand_pressure": score_demand_pressure(**demand),
            "downstream_criticality": score_downstream_criticality(
                structural_hard_block=downstream["structural_hard_block"],
                explicit_no_impact=downstream.get("explicit_no_impact", False),
                cohorts=tuple(
                    DownstreamImpactCohort(**item)
                    for item in downstream.get("cohorts", ())
                ),
            ),
            "effective_supply_concentration": score_effective_supply_concentration(
                suppliers=tuple(
                    SupplierSupply(**item) for item in concentration["suppliers"]
                ),
                target_demand=concentration["target_demand"],
            ).rating,
            "qualification_barrier": _qualification_interval(qualification),
            "capacity_inelasticity": score_capacity_inelasticity(
                top1_loss_output=capacity["top1_loss_output"],
                failover_output_90d=capacity["failover_output_90d"],
                stable_incremental_output=capacity["stable_incremental_output"],
                tasks=tuple(CapacityTask(**item) for item in capacity["tasks"]),
            ).rating,
            "substitute_weakness": score_substitute_weakness(
                routes=tuple(SubstituteRoute(**item) for item in substitute["routes"]),
                target_demand=substitute["target_demand"],
                discovery_rounds_without_new_routes=substitute[
                    "discovery_rounds_without_new_routes"
                ],
                required_categories=tuple(substitute["required_categories"]),
                searched_categories=tuple(substitute["searched_categories"]),
                explicit_negative_categories=tuple(
                    substitute.get("explicit_negative_categories", ())
                ),
                budget_exhausted=substitute.get("budget_exhausted", False),
            ).rating,
        }
    except (KeyError, TypeError) as error:
        raise ValueError("v1.5 Segment fact payload shape is invalid") from error
    return {
        dimension: (result.rating_min, result.rating_max)
        for dimension, result in results.items()
    }


def _qualification_interval(payload) -> OrdinalResult:
    low = payload.get("rating_min")
    high = payload.get("rating_max")
    if (
        isinstance(low, bool)
        or isinstance(high, bool)
        or not isinstance(low, int)
        or not isinstance(high, int)
    ):
        raise ValueError("qualification barrier interval must use integer ordinals")
    return OrdinalResult.of(low, high)


def _contains_forbidden_score(value) -> bool:
    if isinstance(value, dict):
        return bool(_FORBIDDEN_MODEL_SCORE_KEYS & set(value)) or any(
            _contains_forbidden_score(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_score(item) for item in value)
    return False
