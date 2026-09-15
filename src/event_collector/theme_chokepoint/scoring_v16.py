"""Deterministic Theme Chokepoint v1.5 Segment scoring semantics.

Models extract scoped facts. This module owns the v1.6 mechanical boundary mapping,
including public-process qualification evidence when exact duration is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path


class V16ScoringContract:
    """Hash-pinned reader for the unfrozen v1.6 semantic task contract."""

    def __init__(self, path: str | Path, *, expected_sha256: str) -> None:
        contract_path = Path(path)
        raw = contract_path.read_bytes()
        actual = sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise ValueError(
                "v1.6 executable scoring contract SHA-256 mismatch: "
                f"expected={expected_sha256} actual={actual}"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("v1.6 executable scoring contract is invalid JSON") from error
        if payload.get("contract_id") != "theme-chokepoint-semantic-task-contract-v1.6":
            raise ValueError("v1.6 executable scoring contract ID is not canonical")
        if payload.get("status") != "freeze_candidate":
            raise ValueError("v1.6 executable scoring contract is not a freeze candidate")
        if payload.get("inherits_scoring_contract") != "theme-chokepoint-scoring-v1.6":
            raise ValueError("v1.6 human scoring contract lineage is invalid")
        weights = payload.get("segment_weights", {})
        if not isinstance(weights, dict) or sum(weights.values()) != 100:
            raise ValueError("v1.6 Segment weights must sum to 100")
        semantics = payload.get("segment_semantics", {})
        required = {
            "demand_pressure",
            "downstream_criticality",
            "effective_supply_concentration",
            "qualification_barrier",
            "capacity_inelasticity",
            "substitute_weakness",
        }
        if set(semantics) != required:
            raise ValueError("v1.6 Segment semantics are incomplete")
        self.path = contract_path.resolve()
        self.sha256 = actual
        self.version = payload["inherits_scoring_contract"]
        self.contract_id = payload["contract_id"]
        self.status = payload["status"]
        self.segment_weights = dict(weights)
        self.segment_semantics = dict(semantics)


@dataclass(frozen=True)
class OrdinalResult:
    rating_min: int
    rating_max: int
    bound_type: str

    @classmethod
    def of(cls, low: int, high: int) -> "OrdinalResult":
        if not (0 <= low <= high <= 4):
            raise ValueError("ordinal bounds must satisfy 0 <= low <= high <= 4")
        return cls(low, high, "exact" if low == high else "interval")


def score_demand_pressure(
    *,
    canonical_annual_growth: float | None,
    direct_transmission_verified: bool,
    inventory_verified: bool,
) -> OrdinalResult:
    """Map like-for-like annual growth without inventing missing gate evidence."""
    if canonical_annual_growth is None:
        return OrdinalResult.of(0, 4)
    if not isfinite(canonical_annual_growth):
        raise ValueError("canonical annual growth must be finite")
    if canonical_annual_growth <= 0:
        return OrdinalResult.of(0, 0)
    if canonical_annual_growth < 0.10:
        growth_anchor = 1
    elif canonical_annual_growth < 0.20:
        growth_anchor = 2
    elif canonical_annual_growth <= 0.30:
        growth_anchor = 3
    else:
        growth_anchor = 4
    if not direct_transmission_verified:
        return OrdinalResult.of(0, growth_anchor)
    if growth_anchor < 4:
        return OrdinalResult.of(growth_anchor, growth_anchor)
    if inventory_verified:
        return OrdinalResult.of(4, 4)
    return OrdinalResult.of(3, 4)


@dataclass(frozen=True)
class DownstreamImpactCohort:
    delay_months: float
    impact_ratio: float
    material_performance_degradation: bool = False
    positive_consequence: bool = True


def score_downstream_criticality(
    *,
    structural_hard_block: str,
    cohorts: tuple[DownstreamImpactCohort, ...],
    explicit_no_impact: bool = False,
) -> OrdinalResult:
    if structural_hard_block not in {"pass", "fail", "unknown"}:
        raise ValueError("structural_hard_block must be pass, fail or unknown")
    if structural_hard_block == "pass":
        return OrdinalResult.of(4, 4)
    if explicit_no_impact and cohorts:
        raise ValueError("explicit no-impact evidence conflicts with impact cohorts")
    if explicit_no_impact:
        operational = 0
    elif not cohorts:
        return OrdinalResult.of(0, 4)
    else:
        operational = max(_operational_criticality(cohort) for cohort in cohorts)
    if structural_hard_block == "unknown":
        return OrdinalResult.of(operational, 4)
    return OrdinalResult.of(operational, operational)


def _operational_criticality(cohort: DownstreamImpactCohort) -> int:
    if (
        not isfinite(cohort.delay_months)
        or not isfinite(cohort.impact_ratio)
        or cohort.delay_months < 0
        or not 0 <= cohort.impact_ratio <= 1
    ):
        raise ValueError("downstream cohort values are outside the canonical range")
    if (
        cohort.delay_months > 3
        or cohort.impact_ratio > 0.10
        or cohort.material_performance_degradation
    ):
        return 3
    if cohort.delay_months >= 1 or cohort.impact_ratio >= 0.05:
        return 2
    if cohort.positive_consequence:
        return 1
    return 0


QUALIFICATION_PROCESS_SIGNALS = frozenset(
    {
        "formal_qualification",
        "approved_vendor_list",
        "type_test",
        "design_review",
        "factory_acceptance_test",
        "site_acceptance_test",
        "customer_specific_validation",
        "field_pilot",
        "regulatory_approval",
        "requalification",
    }
)
_HIGH_QUALIFICATION_PROCESS_SIGNALS = frozenset(
    {
        "customer_specific_validation",
        "field_pilot",
        "requalification",
    }
)


def score_qualification_barrier(
    *,
    duration_months: float | None,
    qualification_required: bool | None,
    explicit_no_special_qualification: bool,
    process_signals: tuple[str, ...],
) -> OrdinalResult:
    """Score exact duration or conservative public-process qualification evidence."""
    if qualification_required not in {True, False, None}:
        raise ValueError("qualification_required must be true, false or null")
    if not isinstance(explicit_no_special_qualification, bool):
        raise ValueError("explicit_no_special_qualification must be boolean")
    if not isinstance(process_signals, tuple) or len(set(process_signals)) != len(
        process_signals
    ):
        raise ValueError("qualification process signals must be a unique tuple")
    unknown_signals = set(process_signals) - QUALIFICATION_PROCESS_SIGNALS
    if unknown_signals:
        raise ValueError("qualification process signal is not canonical")
    if explicit_no_special_qualification and (
        qualification_required is True or process_signals or duration_months not in {None, 0}
    ):
        raise ValueError("explicit no-qualification evidence conflicts with qualification facts")
    if qualification_required is False and (process_signals or duration_months not in {None, 0}):
        raise ValueError("qualification not-required evidence conflicts with qualification facts")

    if duration_months is not None:
        if isinstance(duration_months, bool) or not isfinite(duration_months) or duration_months < 0:
            raise ValueError("qualification duration must be finite and non-negative")
        if duration_months <= 3:
            return OrdinalResult.of(0, 0)
        if duration_months <= 6:
            return OrdinalResult.of(1, 1)
        if duration_months <= 12:
            return OrdinalResult.of(2, 2)
        if duration_months < 18:
            return OrdinalResult.of(3, 3)
        if process_signals:
            return OrdinalResult.of(4, 4)
        return OrdinalResult.of(3, 4)

    if explicit_no_special_qualification or qualification_required is False:
        return OrdinalResult.of(0, 0)
    if _HIGH_QUALIFICATION_PROCESS_SIGNALS.intersection(process_signals):
        return OrdinalResult.of(3, 4)
    if len(process_signals) >= 2:
        return OrdinalResult.of(2, 4)
    if qualification_required is True or process_signals:
        return OrdinalResult.of(1, 4)
    return OrdinalResult.of(0, 4)


@dataclass(frozen=True)
class SupplierSupply:
    supplier_id: str
    nameplate_output: float
    yield_fraction: float
    qualification_fraction: float
    target_scope_allocation_fraction: float
    availability_fraction: float
    additional_qualified_output_90d: float = 0.0

    @property
    def effective_output(self) -> float:
        values = (
            self.nameplate_output,
            self.yield_fraction,
            self.qualification_fraction,
            self.target_scope_allocation_fraction,
            self.availability_fraction,
            self.additional_qualified_output_90d,
        )
        if any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("supplier supply inputs must be finite and non-negative")
        if any(
            value > 1
            for value in (
                self.yield_fraction,
                self.qualification_fraction,
                self.target_scope_allocation_fraction,
                self.availability_fraction,
            )
        ):
            raise ValueError("supplier fractions cannot exceed one")
        return (
            self.nameplate_output
            * self.yield_fraction
            * self.qualification_fraction
            * self.target_scope_allocation_fraction
            * self.availability_fraction
        )


@dataclass(frozen=True)
class SupplyConcentrationResult:
    effective_supplier_count: float
    largest_effective_share: float
    qualified_failover_ratio: float
    component_scores: tuple[int, int, int]
    rating: OrdinalResult


def score_effective_supply_concentration(
    *, suppliers: tuple[SupplierSupply, ...], target_demand: float
) -> SupplyConcentrationResult:
    if not suppliers or not isfinite(target_demand) or target_demand <= 0:
        raise ValueError("concentration scoring requires suppliers and positive target demand")
    outputs = tuple(item.effective_output for item in suppliers)
    total = sum(outputs)
    if total <= 0:
        raise ValueError("effective supply must be positive")
    shares = tuple(output / total for output in outputs)
    largest_index = max(range(len(outputs)), key=outputs.__getitem__)
    n_eff = 1 / sum(share * share for share in shares)
    failover_output = sum(
        item.additional_qualified_output_90d
        for index, item in enumerate(suppliers)
        if index != largest_index
    )
    failover = min(1.0, failover_output / target_demand)
    components = (
        _n_eff_score(n_eff),
        _largest_share_score(shares[largest_index]),
        _failover_score(failover),
    )
    return SupplyConcentrationResult(
        effective_supplier_count=n_eff,
        largest_effective_share=shares[largest_index],
        qualified_failover_ratio=failover,
        component_scores=components,
        rating=combine_concentration_scores(components),
    )


def combine_concentration_scores(
    component_scores: tuple[int | None, int | None, int | None]
) -> OrdinalResult:
    if len(component_scores) != 3:
        raise ValueError("concentration requires N_eff, Top1 and Failover components")
    lows = tuple(0 if value is None else _component_score(value) for value in component_scores)
    highs = tuple(4 if value is None else _component_score(value) for value in component_scores)
    low = _weighted_ordinal(lows)
    high = _weighted_ordinal(highs)
    top1, failover = component_scores[1], component_scores[2]
    if top1 == 4 and failover == 4:
        low = high = 4
    return OrdinalResult.of(low, high)


def _component_score(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
        raise ValueError("component scores must be integer ordinals")
    return value


def _weighted_ordinal(values: tuple[int, int, int]) -> int:
    weighted = (
        Decimal(values[0]) * Decimal("0.30")
        + Decimal(values[1]) * Decimal("0.30")
        + Decimal(values[2]) * Decimal("0.40")
    )
    return int(weighted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _n_eff_score(value: float) -> int:
    if value >= 4:
        return 0
    if value >= 3:
        return 1
    if value >= 2:
        return 2
    if value >= 1.25:
        return 3
    return 4


def _largest_share_score(value: float) -> int:
    if value < 0.30:
        return 0
    if value < 0.40:
        return 1
    if value < 0.60:
        return 2
    if value < 0.80:
        return 3
    return 4


def _failover_score(value: float) -> int:
    if value >= 0.30:
        return 0
    if value >= 0.20:
        return 1
    if value >= 0.10:
        return 2
    if value > 0:
        return 3
    return 4


@dataclass(frozen=True)
class CapacityTask:
    task_id: str
    duration_months: float
    dependencies: tuple[str, ...]
    constraint_class: str


@dataclass(frozen=True)
class CapacityInelasticityResult:
    required_increment: float
    critical_path_months: float
    stable_output_months: float
    constraint_classes: tuple[str, ...]
    rating: OrdinalResult


def score_capacity_inelasticity(
    *,
    top1_loss_output: float,
    failover_output_90d: float,
    stable_incremental_output: float,
    tasks: tuple[CapacityTask, ...],
) -> CapacityInelasticityResult:
    values = (top1_loss_output, failover_output_90d, stable_incremental_output)
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("capacity quantities must be finite and non-negative")
    required = max(0.0, top1_loss_output - failover_output_90d)
    if required == 0:
        return CapacityInelasticityResult(0, 0, 0, (), OrdinalResult.of(0, 0))
    if stable_incremental_output < required:
        return CapacityInelasticityResult(required, 0, 0, (), OrdinalResult.of(0, 4))
    duration, classes = _critical_path(tasks)
    stable_months = duration + 3
    if stable_months <= 3:
        rating = OrdinalResult.of(0, 0)
    elif stable_months <= 6:
        rating = OrdinalResult.of(1, 1)
    elif stable_months <= 12:
        rating = OrdinalResult.of(2, 2)
    elif stable_months < 18:
        rating = OrdinalResult.of(3, 3)
    elif len(classes) >= 2:
        rating = OrdinalResult.of(4, 4)
    else:
        rating = OrdinalResult.of(3, 4)
    return CapacityInelasticityResult(
        required, duration, stable_months, tuple(sorted(classes)), rating
    )


def _critical_path(tasks: tuple[CapacityTask, ...]) -> tuple[float, frozenset[str]]:
    if not tasks:
        return 0.0, frozenset()
    by_id = {task.task_id: task for task in tasks}
    if len(by_id) != len(tasks) or any(not key.strip() for key in by_id):
        raise ValueError("capacity task IDs must be unique and non-empty")
    visiting: set[str] = set()
    memo: dict[str, tuple[float, frozenset[str]]] = {}

    def visit(task_id: str) -> tuple[float, frozenset[str]]:
        if task_id in memo:
            return memo[task_id]
        if task_id in visiting:
            raise ValueError("capacity task graph contains a cycle")
        if task_id not in by_id:
            raise ValueError("capacity task references an unknown dependency")
        task = by_id[task_id]
        if not isfinite(task.duration_months) or task.duration_months < 0:
            raise ValueError("capacity task duration must be finite and non-negative")
        if not task.constraint_class.strip():
            raise ValueError("capacity task requires a physical constraint class")
        visiting.add(task_id)
        predecessors = [visit(dependency) for dependency in task.dependencies]
        visiting.remove(task_id)
        prior_duration, prior_classes = max(
            predecessors,
            default=(0.0, frozenset()),
            key=lambda item: item[0],
        )
        result = (
            prior_duration + task.duration_months,
            prior_classes | {task.constraint_class},
        )
        memo[task_id] = result
        return result

    return max((visit(task_id) for task_id in by_id), key=lambda item: item[0])


@dataclass(frozen=True)
class SubstituteRoute:
    route_id: str
    status: str
    qualified_output: float
    capacity_pool_id: str | None = None
    category: str = "product"


@dataclass(frozen=True)
class SubstituteWeaknessResult:
    ready_coverage: float
    protocol_complete: bool
    rating: OrdinalResult


_READY_STATES = {"ready", "production"}
_NEGATIVE_STATES = {
    "explicit_failure",
    "ineligible",
    "cancelled",
    "time_window_outside",
    "insufficient_capacity",
}


def score_substitute_weakness(
    *,
    routes: tuple[SubstituteRoute, ...],
    target_demand: float,
    discovery_rounds_without_new_routes: int,
    required_categories: tuple[str, ...],
    searched_categories: tuple[str, ...],
    explicit_negative_categories: tuple[str, ...] = (),
    budget_exhausted: bool = False,
) -> SubstituteWeaknessResult:
    if not isfinite(target_demand) or target_demand <= 0:
        raise ValueError("substitute scoring requires positive target demand")
    if (
        not required_categories
        or len(set(required_categories)) != len(required_categories)
        or any(not item.strip() for item in required_categories)
    ):
        raise ValueError("substitute scoring requires non-empty required route categories")
    if len(set(searched_categories)) != len(searched_categories) or any(
        not item.strip() for item in searched_categories
    ):
        raise ValueError("searched route categories must be unique and non-empty")
    route_ids = {route.route_id for route in routes}
    if len(route_ids) != len(routes) or any(not item.strip() for item in route_ids):
        raise ValueError("substitute route IDs must be unique and non-empty")
    allowed = _READY_STATES | _NEGATIVE_STATES | {"credible", "prototype", "unresolved"}
    if any(route.status not in allowed for route in routes):
        raise ValueError("substitute route has an unsupported status")
    if any(
        not isfinite(route.qualified_output) or route.qualified_output < 0
        for route in routes
    ):
        raise ValueError("substitute output must be finite and non-negative")

    output_by_pool: dict[str, float] = {}
    for route in routes:
        if route.status not in _READY_STATES:
            continue
        pool = route.capacity_pool_id or f"route:{route.route_id}"
        output_by_pool[pool] = max(output_by_pool.get(pool, 0.0), route.qualified_output)
    coverage = min(1.0, sum(output_by_pool.values()) / target_demand)
    unresolved = any(route.status == "unresolved" for route in routes)
    categories_complete = set(required_categories) <= set(searched_categories)
    protocol_complete = (
        discovery_rounds_without_new_routes >= 2
        and categories_complete
        and not unresolved
        and not budget_exhausted
    )
    if coverage >= 0.30:
        known_ceiling = 0
    elif coverage >= 0.10:
        known_ceiling = 1
    elif coverage > 0 or any(route.status == "credible" for route in routes):
        known_ceiling = 2
    elif any(route.status == "prototype" for route in routes):
        known_ceiling = 3
    else:
        known_ceiling = 4
    if not protocol_complete:
        return SubstituteWeaknessResult(
            coverage, False, OrdinalResult.of(0, known_ceiling)
        )
    if known_ceiling < 4:
        rating = OrdinalResult.of(known_ceiling, known_ceiling)
    else:
        negative_categories = {
            route.category for route in routes if route.status in _NEGATIVE_STATES
        } | set(explicit_negative_categories)
        every_route_negative = all(route.status in _NEGATIVE_STATES for route in routes)
        if (
            protocol_complete
            and every_route_negative
            and set(required_categories) <= negative_categories
        ):
            rating = OrdinalResult.of(4, 4)
        else:
            rating = OrdinalResult.of(0, 4)
    return SubstituteWeaknessResult(coverage, protocol_complete, rating)
