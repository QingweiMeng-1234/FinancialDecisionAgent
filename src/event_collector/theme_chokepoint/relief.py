"""Evidence-bounded inventory-flow derivation for v1.4 Relief Horizon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil


@dataclass(frozen=True)
class ReliefRouteOutput:
    route_id: str
    shared_pool_id: str | None
    nameplate: float
    yield_rate: float
    qualification_fraction: float
    target_scope_allocation: float
    availability: float


@dataclass(frozen=True)
class ReliefPeriodInput:
    period_start: date
    period_end: date
    inventory_begin: float
    route_outputs: tuple[ReliefRouteOutput, ...]
    scrap_or_unavailable: float
    firm_orders_due: float
    forecast: float
    forecast_includes_orders: bool
    backlog_begin: float
    safety_stock_target: float
    required_fill_rate: float
    adjacent_constraint_active: bool


@dataclass(frozen=True)
class ReliefScenarioInput:
    scenario: str
    replenishment_cycle_days: int
    unit: str
    periods: tuple[ReliefPeriodInput, ...]


@dataclass(frozen=True)
class ReliefPeriodResult:
    period_start: date
    period_end: date
    qualified_output: float
    demand: float
    available_before_service: float
    gross_requirement: float
    served_demand: float
    backlog_end: float
    inventory_end: float
    fill_rate: float
    relief_condition_met: bool
    adjacent_constraint_active: bool


@dataclass(frozen=True)
class ReliefScenarioResult:
    scenario: str
    periods: tuple[ReliefPeriodResult, ...]
    required_consecutive_periods: int
    required_duration_days: int
    node_relief_date: date | None
    system_relief_date: date | None
    constraint_transition_state: str


@dataclass(frozen=True)
class ReliefHorizonAssessment:
    base: ReliefScenarioResult
    stress: ReliefScenarioResult


class ReliefHorizonService:
    def assess(
        self,
        *,
        base: ReliefScenarioInput,
        stress: ReliefScenarioInput,
    ) -> ReliefHorizonAssessment:
        if base.scenario != "base" or stress.scenario != "stress":
            raise ValueError("Relief Horizon requires base and stress scenarios")
        if not base.unit.strip() or base.unit != stress.unit:
            raise ValueError("Relief Horizon requires one explicit shared unit")
        return ReliefHorizonAssessment(
            base=_assess_scenario(base),
            stress=_assess_scenario(stress),
        )


def _assess_scenario(scenario: ReliefScenarioInput) -> ReliefScenarioResult:
    if scenario.replenishment_cycle_days <= 0 or not scenario.periods:
        raise ValueError("Relief scenario requires periods and a positive replenishment cycle")
    if not scenario.unit.strip():
        raise ValueError("Relief scenario requires an explicit unit")

    results = []
    previous = None
    for period in scenario.periods:
        _validate_period(period)
        if previous is not None:
            if period.period_start != previous.period_end + timedelta(days=1):
                raise ValueError("Relief periods must be contiguous without gaps or overlap")
            if period.inventory_begin != previous.inventory_end:
                raise ValueError("inventory_begin must equal the prior inventory_end")
            if period.backlog_begin != previous.backlog_end:
                raise ValueError("backlog_begin must equal the prior backlog_end")
        qualified_output = _deduplicated_qualified_output(period.route_outputs)
        demand = (
            max(period.firm_orders_due, period.forecast)
            if period.forecast_includes_orders
            else period.firm_orders_due + period.forecast
        )
        available = max(
            0.0,
            period.inventory_begin + qualified_output - period.scrap_or_unavailable,
        )
        gross_requirement = demand + period.backlog_begin
        served = min(available, gross_requirement)
        backlog_end = gross_requirement - served
        inventory_end = available - served
        fill_rate = 1.0 if gross_requirement == 0 else served / gross_requirement
        relief_met = (
            backlog_end == 0
            and fill_rate >= period.required_fill_rate
            and inventory_end >= period.safety_stock_target
        )
        previous = ReliefPeriodResult(
            period_start=period.period_start,
            period_end=period.period_end,
            qualified_output=_clean(qualified_output),
            demand=_clean(demand),
            available_before_service=_clean(available),
            gross_requirement=_clean(gross_requirement),
            served_demand=_clean(served),
            backlog_end=_clean(backlog_end),
            inventory_end=_clean(inventory_end),
            fill_rate=round(fill_rate, 6),
            relief_condition_met=relief_met,
            adjacent_constraint_active=period.adjacent_constraint_active,
        )
        results.append(previous)

    required_duration_days = max(90, scenario.replenishment_cycle_days)
    required_periods = max(3, ceil(required_duration_days / 30))
    node_relief_date = _first_sustained_relief_date(results, required_duration_days)
    relief_period = next(
        (item for item in results if item.period_end == node_relief_date),
        None,
    )
    adjacent_at_relief = bool(relief_period and relief_period.adjacent_constraint_active)
    system_relief_date = (
        node_relief_date if node_relief_date and not adjacent_at_relief else None
    )
    any_adjacent = any(item.adjacent_constraint_active for item in results)
    if node_relief_date and adjacent_at_relief:
        transition = "transferred"
    elif not node_relief_date and any_adjacent:
        transition = "coexisting_constraints"
    elif node_relief_date:
        transition = "relieved"
    else:
        transition = "unchanged"
    return ReliefScenarioResult(
        scenario=scenario.scenario,
        periods=tuple(results),
        required_consecutive_periods=required_periods,
        required_duration_days=required_duration_days,
        node_relief_date=node_relief_date,
        system_relief_date=system_relief_date,
        constraint_transition_state=transition,
    )


def _deduplicated_qualified_output(routes: tuple[ReliefRouteOutput, ...]) -> float:
    pools = {}
    route_ids = set()
    for route in routes:
        if not route.route_id.strip() or route.route_id in route_ids:
            raise ValueError("Relief routes require unique route_id values")
        route_ids.add(route.route_id)
        if route.nameplate < 0 or not all(
            0 <= value <= 1
            for value in (
                route.yield_rate,
                route.qualification_fraction,
                route.target_scope_allocation,
                route.availability,
            )
        ):
            raise ValueError("Relief route output factors are outside valid bounds")
        output = (
            route.nameplate
            * route.yield_rate
            * route.qualification_fraction
            * route.target_scope_allocation
            * route.availability
        )
        pool_id = route.shared_pool_id.strip() if route.shared_pool_id else route.route_id
        pools[pool_id] = max(pools.get(pool_id, 0.0), output)
    return sum(pools.values())


def _validate_period(period: ReliefPeriodInput) -> None:
    if period.period_start >= period.period_end:
        raise ValueError("Relief period_start must be before period_end")
    values = (
        period.inventory_begin,
        period.scrap_or_unavailable,
        period.firm_orders_due,
        period.forecast,
        period.backlog_begin,
        period.safety_stock_target,
    )
    if any(value < 0 for value in values):
        raise ValueError("Relief inventory, demand and backlog values cannot be negative")
    if not 0 <= period.required_fill_rate <= 1:
        raise ValueError("required_fill_rate must be within 0..1")


def _first_sustained_relief_date(
    periods: list[ReliefPeriodResult], required_duration_days: int
) -> date | None:
    streak_start = None
    for period in periods:
        if period.relief_condition_met:
            if streak_start is None:
                streak_start = period.period_start
        else:
            streak_start = None
        if (
            streak_start is not None
            and (period.period_end - streak_start).days + 1
            >= required_duration_days
        ):
            return period.period_end
    return None


def _clean(value: float) -> float:
    return round(value, 6)
