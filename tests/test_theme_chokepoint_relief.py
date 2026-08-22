from __future__ import annotations

from datetime import date, timedelta

from event_collector.theme_chokepoint.relief import (
    ReliefHorizonService,
    ReliefPeriodInput,
    ReliefRouteOutput,
    ReliefScenarioInput,
)


def _period(month, *, adjacent=False):
    next_month = date(2026, month + 1, 1) if month < 12 else date(2027, 1, 1)
    return ReliefPeriodInput(
        period_start=date(2026, month, 1),
        period_end=next_month - timedelta(days=1),
        inventory_begin=0,
        route_outputs=(
            ReliefRouteOutput(
                route_id="route-a",
                shared_pool_id="fab-1",
                nameplate=100,
                yield_rate=1,
                qualification_fraction=1,
                target_scope_allocation=1,
                availability=1,
            ),
            ReliefRouteOutput(
                route_id="route-b",
                shared_pool_id="fab-1",
                nameplate=100,
                yield_rate=1,
                qualification_fraction=1,
                target_scope_allocation=1,
                availability=1,
            ),
        ),
        scrap_or_unavailable=0,
        firm_orders_due=80,
        forecast=100,
        forecast_includes_orders=True,
        backlog_begin=0,
        safety_stock_target=0,
        required_fill_rate=1,
        adjacent_constraint_active=adjacent,
    )


def test_relief_horizon_deduplicates_demand_and_shared_output_then_requires_a_quarter():
    """SELECT INVARIANT: relief is a sustained inventory-flow result, not a capacity claim."""
    service = ReliefHorizonService()
    base = ReliefScenarioInput(
        scenario="base",
        replenishment_cycle_days=30,
        unit="qualified_units_per_bucket",
        periods=(_period(1, adjacent=True), _period(2, adjacent=True), _period(3, adjacent=True)),
    )
    stress = ReliefScenarioInput(
        scenario="stress",
        replenishment_cycle_days=30,
        unit="qualified_units_per_bucket",
        periods=(
            ReliefPeriodInput(
                **{
                    **_period(1, adjacent=True).__dict__,
                    "forecast": 120,
                }
            ),
            ReliefPeriodInput(
                **{
                    **_period(2, adjacent=True).__dict__,
                    "forecast": 120,
                    "backlog_begin": 20,
                }
            ),
            ReliefPeriodInput(
                **{
                    **_period(3, adjacent=True).__dict__,
                    "forecast": 120,
                    "backlog_begin": 40,
                }
            ),
        ),
    )

    result = service.assess(base=base, stress=stress)

    first = result.base.periods[0]
    assert first.qualified_output == 100
    assert first.demand == 100
    assert first.gross_requirement == 100
    assert first.backlog_end == 0
    assert result.base.node_relief_date == date(2026, 3, 31)
    assert result.base.system_relief_date is None
    assert result.base.constraint_transition_state == "transferred"
    assert result.stress.node_relief_date is None
    assert result.stress.constraint_transition_state == "coexisting_constraints"


def test_relief_horizon_adds_only_residual_forecast_to_firm_orders():
    """SELECT INVARIANT: forecast semantics are explicit and mechanically de-duplicated."""
    period = ReliefPeriodInput(
        **{
            **_period(1).__dict__,
            "firm_orders_due": 80,
            "forecast": 20,
            "forecast_includes_orders": False,
        }
    )

    result = ReliefHorizonService().assess(
        base=ReliefScenarioInput("base", 30, "qualified_units_per_bucket", (period,)),
        stress=ReliefScenarioInput("stress", 30, "qualified_units_per_bucket", (period,)),
    )

    assert result.base.periods[0].demand == 100


def test_three_consecutive_week_buckets_do_not_satisfy_a_quarter_of_relief():
    """SELECT INVARIANT: period count cannot substitute for 90 days of sustained relief."""
    weekly = tuple(
        ReliefPeriodInput(
            **{
                **_period(1).__dict__,
                "period_start": date(2026, 1, 1) + timedelta(days=7 * index),
                "period_end": date(2026, 1, 7) + timedelta(days=7 * index),
            }
        )
        for index in range(3)
    )

    result = ReliefHorizonService().assess(
        base=ReliefScenarioInput("base", 30, "units_per_bucket", weekly),
        stress=ReliefScenarioInput("stress", 30, "units_per_bucket", weekly),
    )

    assert result.base.node_relief_date is None
    assert result.stress.node_relief_date is None
