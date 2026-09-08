"""Validate v1.6 model-extracted facts and delegate ordinal mapping to code."""

from __future__ import annotations

from event_collector.theme_chokepoint.scoring_v16 import (
    CapacityTask,
    DownstreamImpactCohort,
    OrdinalResult,
    SubstituteRoute,
    SupplierSupply,
    score_capacity_inelasticity,
    score_demand_pressure,
    score_downstream_criticality,
    score_effective_supply_concentration,
    score_qualification_barrier,
    score_substitute_weakness,
)


SCHEMA_VERSION = "theme-chokepoint-segment-facts-v1.6"
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
    "rating_min",
    "rating_max",
    "score",
    "bound_type",
    "primary_state",
}


def score_v16_segment_facts(
    payload: dict,
    *,
    dimensions: tuple[str, ...] = DIMENSIONS,
) -> dict[str, tuple[int, int]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("v1.6 Segment fact schema version is invalid")
    if _contains_forbidden_score(payload):
        raise ValueError("v1.6 fact payload contains a model-authored rating")
    if not dimensions or len(set(dimensions)) != len(dimensions) or any(
        dimension not in DIMENSIONS for dimension in dimensions
    ):
        raise ValueError("v1.6 requested dimensions are invalid")
    missing = [dimension for dimension in dimensions if dimension not in payload]
    if missing:
        raise ValueError("v1.6 Segment fact payload is incomplete: " + missing[0])

    results = {}
    try:
        if "demand_pressure" in dimensions:
            demand = payload["demand_pressure"]
            results["demand_pressure"] = OrdinalResult.of(0, 4) if _unresolved(demand) else score_demand_pressure(
                canonical_annual_growth=demand["canonical_annual_growth"],
                direct_transmission_verified=demand["direct_transmission_verified"],
                inventory_verified=demand["inventory_verified"],
            )
        if "downstream_criticality" in dimensions:
            downstream = payload["downstream_criticality"]
            results["downstream_criticality"] = OrdinalResult.of(0, 4) if _unresolved(downstream) else score_downstream_criticality(
                structural_hard_block=downstream["structural_hard_block"],
                explicit_no_impact=downstream.get("explicit_no_impact", False),
                cohorts=tuple(
                    DownstreamImpactCohort(**item)
                    for item in downstream.get("cohorts", ())
                ),
            )
        if "effective_supply_concentration" in dimensions:
            concentration = payload["effective_supply_concentration"]
            results["effective_supply_concentration"] = OrdinalResult.of(0, 4) if _unresolved(concentration) else score_effective_supply_concentration(
                suppliers=tuple(
                    SupplierSupply(**item) for item in concentration["suppliers"]
                ),
                target_demand=concentration["target_demand"],
            )
        if "qualification_barrier" in dimensions:
            qualification = payload["qualification_barrier"]
            results["qualification_barrier"] = OrdinalResult.of(0, 4) if _unresolved(qualification) else score_qualification_barrier(
                duration_months=qualification.get("duration_months"),
                qualification_required=qualification.get("qualification_required"),
                explicit_no_special_qualification=qualification.get(
                    "explicit_no_special_qualification", False
                ),
                process_signals=tuple(qualification.get("process_signals", ())),
            )
        if "capacity_inelasticity" in dimensions:
            capacity = payload["capacity_inelasticity"]
            results["capacity_inelasticity"] = OrdinalResult.of(0, 4) if _unresolved(capacity) else score_capacity_inelasticity(
                top1_loss_output=capacity["top1_loss_output"],
                failover_output_90d=capacity["failover_output_90d"],
                stable_incremental_output=capacity["stable_incremental_output"],
                tasks=tuple(CapacityTask(**item) for item in capacity["tasks"]),
            )
        if "substitute_weakness" in dimensions:
            substitute = payload["substitute_weakness"]
            results["substitute_weakness"] = OrdinalResult.of(0, 4) if _unresolved(substitute) else score_substitute_weakness(
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
            )
    except (KeyError, TypeError) as error:
        raise ValueError("v1.6 Segment fact payload shape is invalid") from error
    return {
        dimension: (
            (result.rating if hasattr(result, "rating") else result).rating_min,
            (result.rating if hasattr(result, "rating") else result).rating_max,
        )
        for dimension, result in results.items()
    }


def _unresolved(facts: dict) -> bool:
    return facts.get("unresolved") is True


def _contains_forbidden_score(value) -> bool:
    if isinstance(value, dict):
        return bool(_FORBIDDEN_MODEL_SCORE_KEYS & set(value)) or any(
            _contains_forbidden_score(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_score(item) for item in value)
    return False
