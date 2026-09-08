"""Validate v1.6.1 inference facts and apply conservative proxy mappings."""

from __future__ import annotations

from event_collector.theme_chokepoint.scoring_v16 import (
    CapacityTask,
    DownstreamImpactCohort,
    SupplierSupply,
    SubstituteRoute,
    score_demand_pressure,
    score_capacity_inelasticity,
    score_downstream_criticality,
    score_effective_supply_concentration,
    score_qualification_barrier,
    score_substitute_weakness,
)


SCHEMA_VERSION = "theme-chokepoint-segment-facts-v1.6.1"
DIMENSIONS = (
    "demand_pressure",
    "downstream_criticality",
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
)


def score_v161_segment_facts(
    payload: dict,
    *,
    dimensions: tuple[str, ...] = DIMENSIONS,
) -> dict[str, tuple[int, int]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("v1.6.1 Segment fact schema version is invalid")
    if not dimensions or any(dimension not in DIMENSIONS for dimension in dimensions):
        raise ValueError("v1.6.1 requested dimensions are invalid")
    missing = [dimension for dimension in dimensions if dimension not in payload]
    if missing:
        raise ValueError("v1.6.1 Segment fact payload is incomplete: " + missing[0])

    results = {}
    if "demand_pressure" in dimensions:
        demand = payload["demand_pressure"]
        if demand.get("unresolved") is True:
            results["demand_pressure"] = (0, 4)
        else:
            proxy_growth = demand.get("demand_proxy_annual_growth")
            if (
                demand["canonical_annual_growth"] is None
                and proxy_growth is not None
                and demand.get("demand_proxy_directly_attributed") is True
                and demand.get("demand_proxy_capacity_prebooked") is True
            ):
                proxy_rating = score_demand_pressure(
                    canonical_annual_growth=proxy_growth,
                    direct_transmission_verified=True,
                    inventory_verified=True,
                )
                results["demand_pressure"] = (proxy_rating.rating_min, 4)
                demand = None
            if demand is None:
                pass
            else:
                rating = score_demand_pressure(
                    canonical_annual_growth=demand["canonical_annual_growth"],
                    direct_transmission_verified=demand["direct_transmission_verified"],
                    inventory_verified=demand["inventory_verified"],
                )
                bounds = (rating.rating_min, rating.rating_max)
                if (
                    demand["canonical_annual_growth"] is not None
                    and demand["canonical_annual_growth"] > 0
                    and demand["direct_transmission_verified"] is True
                    and demand["inventory_verified"] is False
                ):
                    bounds = (rating.rating_min, 4)
                results["demand_pressure"] = bounds
    if "downstream_criticality" in dimensions:
        downstream = payload["downstream_criticality"]
        if downstream.get("unresolved") is True:
            results["downstream_criticality"] = (0, 4)
        else:
            structural = (
                "pass"
                if downstream.get("scope_dependency_verified") is True
                else downstream["structural_hard_block"]
            )
            rating = score_downstream_criticality(
                structural_hard_block=structural,
                explicit_no_impact=downstream.get("explicit_no_impact", False),
                cohorts=tuple(
                    DownstreamImpactCohort(**item)
                    for item in downstream.get("cohorts", ())
                ),
            )
            results["downstream_criticality"] = (
                rating.rating_min,
                rating.rating_max,
            )
    if "effective_supply_concentration" in dimensions:
        concentration = payload["effective_supply_concentration"]
        if concentration.get("unresolved") is True:
            results["effective_supply_concentration"] = (0, 4)
        elif concentration.get("sole_effective_supplier_verified") is True:
            results["effective_supply_concentration"] = (4, 4)
        else:
            floor = 0
            if concentration.get("qualified_failover_absence_verified") is True:
                floor = 2
            supplier_cap = concentration.get("effective_supplier_count_upper_bound")
            if supplier_cap is not None and supplier_cap <= 3:
                floor = max(floor, 1)
            if floor:
                results["effective_supply_concentration"] = (floor, 4)
            elif (
                concentration.get("target_demand") is None
                or not concentration.get("suppliers")
                or any(
                    any(
                        supplier.get(field) is None
                        for field in (
                            "nameplate_output",
                            "yield_fraction",
                            "qualification_fraction",
                            "target_scope_allocation_fraction",
                            "availability_fraction",
                            "additional_qualified_output_90d",
                        )
                    )
                    for supplier in concentration.get("suppliers", ())
                )
            ):
                results["effective_supply_concentration"] = (0, 4)
            else:
                rating = score_effective_supply_concentration(
                    suppliers=tuple(
                        SupplierSupply(**item)
                        for item in concentration.get("suppliers", ())
                    ),
                    target_demand=concentration["target_demand"],
                ).rating
                results["effective_supply_concentration"] = (
                    rating.rating_min,
                    rating.rating_max,
                )
    if "qualification_barrier" in dimensions:
        qualification = payload["qualification_barrier"]
        if qualification.get("unresolved") is True:
            results["qualification_barrier"] = (0, 4)
        else:
            rating = score_qualification_barrier(
                duration_months=qualification.get("duration_months"),
                qualification_required=qualification.get("qualification_required"),
                explicit_no_special_qualification=qualification.get(
                    "explicit_no_special_qualification", False
                ),
                process_signals=tuple(qualification.get("process_signals", ())),
            )
            results["qualification_barrier"] = (
                rating.rating_min,
                rating.rating_max,
            )
    if "capacity_inelasticity" in dimensions:
        capacity = payload["capacity_inelasticity"]
        if capacity.get("unresolved") is True:
            results["capacity_inelasticity"] = (0, 4)
        else:
            lead_months = capacity.get("minimum_physical_lead_time_months")
            signals = capacity.get("physical_constraint_signals", ())
            if lead_months is not None and lead_months >= 12:
                results["capacity_inelasticity"] = (3, 4)
            elif len(set(signals)) >= 2:
                results["capacity_inelasticity"] = (2, 4)
            elif capacity.get("physical_expansion_required") is True:
                results["capacity_inelasticity"] = (1, 4)
            elif all(
                capacity.get(field) is not None
                for field in (
                    "top1_loss_output",
                    "failover_output_90d",
                    "stable_incremental_output",
                )
            ):
                rating = score_capacity_inelasticity(
                    top1_loss_output=capacity["top1_loss_output"],
                    failover_output_90d=capacity["failover_output_90d"],
                    stable_incremental_output=capacity["stable_incremental_output"],
                    tasks=tuple(
                        CapacityTask(**item) for item in capacity.get("tasks", ())
                    ),
                ).rating
                results["capacity_inelasticity"] = (
                    rating.rating_min,
                    rating.rating_max,
                )
            else:
                results["capacity_inelasticity"] = (0, 4)
    if "substitute_weakness" in dimensions:
        substitute = payload["substitute_weakness"]
        if substitute.get("unresolved") is True:
            results["substitute_weakness"] = (0, 4)
        elif any(
            route.get("status") in {"ready", "production", "credible"}
            and route.get("qualified_output") is None
            for route in substitute.get("routes", ())
        ):
            results["substitute_weakness"] = (0, 2)
        elif substitute.get("target_demand") is None:
            results["substitute_weakness"] = (0, 4)
        else:
            rating = score_substitute_weakness(
                routes=tuple(
                    SubstituteRoute(**item) for item in substitute.get("routes", ())
                ),
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
            ).rating
            results["substitute_weakness"] = (
                rating.rating_min,
                rating.rating_max,
            )
    return results


__all__ = ["DIMENSIONS", "SCHEMA_VERSION", "score_v161_segment_facts"]
