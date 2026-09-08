"""Versioned v1.6.1 fact adapter with conservative observable proxies."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_collector.theme_chokepoint.providers.facts_v16 import (
    DeepSeekV16SegmentFactExtractor,
    _FactContractViolation,
    _collect_evidence_ids,
    _contains_model_score,
)
from event_collector.theme_chokepoint.validation_v161 import score_v161_segment_facts


V161_FACT_PROMPT_SHA256 = (
    "983e3cbcb946267d9c59a1f0eb4feaf0c5315761f06887120f995b9b7bd0a3bb"
)
V161_FACT_SCHEMA_SHA256 = (
    "3ff8a5bfc53c40c84a3713705963d8811aa14fd515ddd38efd7301057b31621d"
)
_SCHEMA_VERSION = "theme-chokepoint-segment-facts-v1.6.1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _UnresolvedDimension(_StrictModel):
    unresolved: Literal[True]
    evidence_ids: list[str] = Field(min_length=1)


class _DemandPressure(_StrictModel):
    unresolved: Literal[False]
    canonical_annual_growth: float | None
    direct_transmission_verified: bool
    inventory_verified: bool
    annual_growth_basis: Literal[
        "monthly_yoy",
        "quarterly_yoy",
        "ttm_yoy",
        "cagr_annualized",
        "seasonally_adjusted_annualized",
    ] | None
    demand_proxy_annual_growth: float | None
    demand_proxy_directly_attributed: bool
    demand_proxy_capacity_prebooked: bool
    evidence_ids: list[str] = Field(min_length=1)


class _DownstreamCohort(_StrictModel):
    delay_months: float = Field(ge=0)
    impact_ratio: float = Field(ge=0, le=1)
    material_performance_degradation: bool
    positive_consequence: bool


class _DownstreamCriticality(_StrictModel):
    unresolved: Literal[False]
    scope_dependency_verified: bool
    structural_hard_block: Literal["pass", "fail", "unknown"]
    explicit_no_impact: bool
    cohorts: list[_DownstreamCohort]
    evidence_ids: list[str] = Field(min_length=1)


class _Supplier(_StrictModel):
    supplier_id: str = Field(min_length=1)
    nameplate_output: float | None = Field(ge=0)
    yield_fraction: float | None = Field(ge=0, le=1)
    qualification_fraction: float | None = Field(ge=0, le=1)
    target_scope_allocation_fraction: float | None = Field(ge=0, le=1)
    availability_fraction: float | None = Field(ge=0, le=1)
    additional_qualified_output_90d: float | None = Field(ge=0)


class _SupplyConcentration(_StrictModel):
    unresolved: Literal[False]
    target_demand: float | None = Field(gt=0)
    suppliers: list[_Supplier]
    sole_effective_supplier_verified: bool
    qualified_failover_absence_verified: bool
    effective_supplier_count_upper_bound: int | None = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1)


_PROCESS_SIGNAL = Literal[
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
]


class _QualificationBarrier(_StrictModel):
    unresolved: Literal[False]
    duration_months: float | None = Field(ge=0)
    qualification_required: bool | None
    explicit_no_special_qualification: bool
    process_signals: list[_PROCESS_SIGNAL]
    scope_coverage: Literal["full_scope", "partial_subtype", "unknown"]
    evidence_ids: list[str] = Field(min_length=1)


class _CapacityTask(_StrictModel):
    task_id: str = Field(min_length=1)
    duration_months: float = Field(ge=0)
    dependencies: list[str]
    constraint_class: str = Field(min_length=1)


class _CapacityInelasticity(_StrictModel):
    unresolved: Literal[False]
    top1_loss_output: float | None = Field(ge=0)
    failover_output_90d: float | None = Field(ge=0)
    stable_incremental_output: float | None = Field(ge=0)
    tasks: list[_CapacityTask]
    minimum_physical_lead_time_months: float | None = Field(ge=0)
    physical_expansion_required: bool
    physical_constraint_signals: list[
        Literal[
            "advanced_process",
            "cleanroom",
            "greenfield_fab",
            "advanced_packaging",
            "long_lead_component",
            "equipment_build",
            "construction",
            "skilled_labor",
            "commissioning",
        ]
    ]
    evidence_ids: list[str] = Field(min_length=1)


class _SubstituteRoute(_StrictModel):
    route_id: str = Field(min_length=1)
    status: Literal[
        "ready",
        "production",
        "credible",
        "prototype",
        "unresolved",
        "explicit_failure",
        "ineligible",
        "cancelled",
        "time_window_outside",
        "insufficient_capacity",
    ]
    qualified_output: float | None = Field(ge=0)
    capacity_pool_id: str | None
    category: str = Field(min_length=1)


class _SubstituteWeakness(_StrictModel):
    unresolved: Literal[False]
    target_demand: float | None = Field(gt=0)
    discovery_rounds_without_new_routes: int = Field(ge=0)
    required_categories: list[str] = Field(min_length=1)
    searched_categories: list[str]
    explicit_negative_categories: list[str]
    budget_exhausted: bool
    routes: list[_SubstituteRoute]
    evidence_ids: list[str] = Field(min_length=1)


class _SegmentFacts(_StrictModel):
    schema_version: Literal["theme-chokepoint-segment-facts-v1.6.1"]
    demand_pressure: _DemandPressure | _UnresolvedDimension
    downstream_criticality: _DownstreamCriticality | _UnresolvedDimension
    effective_supply_concentration: _SupplyConcentration | _UnresolvedDimension
    qualification_barrier: _QualificationBarrier | _UnresolvedDimension
    capacity_inelasticity: _CapacityInelasticity | _UnresolvedDimension
    substitute_weakness: _SubstituteWeakness | _UnresolvedDimension


def _decode_and_score_v161(
    content: str,
    allowed_evidence_ids: set[str],
) -> tuple[dict, dict[str, tuple[int, int]]]:
    import json

    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise _FactContractViolation("invalid JSON") from None
    if _contains_model_score(payload):
        raise _FactContractViolation("model-authored rating or state is forbidden")
    try:
        validated = _SegmentFacts.model_validate(payload)
    except ValidationError as error:
        details = []
        for item in error.errors()[:3]:
            location = ".".join(str(part) for part in item["loc"])
            details.append(f"{location}:{item['type']}")
        raise _FactContractViolation(
            "schema violation" + (":" + "|".join(details) if details else "")
        ) from None
    facts = validated.model_dump(mode="json")
    cited_ids = _collect_evidence_ids(facts)
    if not cited_ids <= allowed_evidence_ids:
        raise _FactContractViolation("response cites evidence outside the case binding")
    try:
        ratings = score_v161_segment_facts(facts)
    except (KeyError, TypeError, ValueError) as error:
        raise _FactContractViolation(f"deterministic scoring violation:{error}") from None
    return facts, ratings


class DeepSeekV161SegmentFactExtractor(DeepSeekV16SegmentFactExtractor):
    """Extract v1.6.1 proxy-capable facts while code owns every ordinal."""

    def __init__(
        self,
        *,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        prompt_path: str | Path | None = None,
        schema_path: str | Path | None = None,
        expected_prompt_sha256: str = V161_FACT_PROMPT_SHA256,
        expected_schema_sha256: str = V161_FACT_SCHEMA_SHA256,
    ) -> None:
        root = Path(__file__).resolve().parents[4]
        super().__init__(
            client=client,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            prompt_path=prompt_path
            or root
            / "tools/theme-chokepoint/prompts/segment-fact-extractor-v1.6.1.md",
            schema_path=schema_path
            or root
            / "tools/theme-chokepoint/schemas/theme-chokepoint-segment-facts-v1.6.1.schema.json",
            expected_prompt_sha256=expected_prompt_sha256,
            expected_schema_sha256=expected_schema_sha256,
            expected_schema_id=_SCHEMA_VERSION,
            decoder=_decode_and_score_v161,
        )


__all__ = [
    "DeepSeekV161SegmentFactExtractor",
    "V161_FACT_PROMPT_SHA256",
    "V161_FACT_SCHEMA_SHA256",
]
