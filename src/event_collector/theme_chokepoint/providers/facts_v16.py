"""Structured v1.6 fact extraction with deterministic ordinal scoring."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_collector.theme_chokepoint.providers.llm import (
    _configured_api_key,
    _configured_base_url,
    _configured_model,
)
from event_collector.theme_chokepoint.validation_v16 import score_v16_segment_facts


V16_FACT_PROMPT_SHA256 = (
    "76414029343b863bda9983faf133a543fdffccae60381f8cbc82665ae38af642"
)
V16_FACT_SCHEMA_SHA256 = (
    "9924038787b83eae2692c82e27bec16f61007990327fdd2a5af58840202e2d1f"
)
_SCHEMA_VERSION = "theme-chokepoint-segment-facts-v1.6"
_FORBIDDEN_SCORE_KEYS = {
    "rating",
    "rating_min",
    "rating_max",
    "score",
    "bound_type",
    "primary_state",
}


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
    ] | None = None
    evidence_ids: list[str] = Field(min_length=1)


class _DownstreamCohort(_StrictModel):
    delay_months: float = Field(ge=0)
    impact_ratio: float = Field(ge=0, le=1)
    material_performance_degradation: bool = False
    positive_consequence: bool = True


class _DownstreamCriticality(_StrictModel):
    unresolved: Literal[False]
    structural_hard_block: Literal["pass", "fail", "unknown"]
    explicit_no_impact: bool = False
    cohorts: list[_DownstreamCohort]
    evidence_ids: list[str] = Field(min_length=1)


class _Supplier(_StrictModel):
    supplier_id: str = Field(min_length=1)
    nameplate_output: float = Field(ge=0)
    yield_fraction: float = Field(ge=0, le=1)
    qualification_fraction: float = Field(ge=0, le=1)
    target_scope_allocation_fraction: float = Field(ge=0, le=1)
    availability_fraction: float = Field(ge=0, le=1)
    additional_qualified_output_90d: float = Field(default=0, ge=0)


class _SupplyConcentration(_StrictModel):
    unresolved: Literal[False]
    target_demand: float = Field(gt=0)
    suppliers: list[_Supplier] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class _QualificationBarrier(_StrictModel):
    unresolved: Literal[False]
    duration_months: float | None = Field(default=None, ge=0)
    qualification_required: bool | None
    explicit_no_special_qualification: bool
    process_signals: list[
        Literal[
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
    ]
    evidence_ids: list[str] = Field(min_length=1)


class _CapacityTask(_StrictModel):
    task_id: str = Field(min_length=1)
    duration_months: float = Field(ge=0)
    dependencies: list[str]
    constraint_class: str = Field(min_length=1)


class _CapacityInelasticity(_StrictModel):
    unresolved: Literal[False]
    top1_loss_output: float = Field(ge=0)
    failover_output_90d: float = Field(ge=0)
    stable_incremental_output: float = Field(ge=0)
    tasks: list[_CapacityTask]
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
    qualified_output: float = Field(ge=0)
    capacity_pool_id: str | None = None
    category: str = "product"


class _SubstituteWeakness(_StrictModel):
    unresolved: Literal[False]
    target_demand: float = Field(gt=0)
    discovery_rounds_without_new_routes: int = Field(ge=0)
    required_categories: list[str]
    searched_categories: list[str]
    explicit_negative_categories: list[str] = Field(default_factory=list)
    budget_exhausted: bool = False
    routes: list[_SubstituteRoute]
    evidence_ids: list[str] = Field(min_length=1)


class _SegmentFacts(_StrictModel):
    schema_version: Literal["theme-chokepoint-segment-facts-v1.6"]
    demand_pressure: _DemandPressure | _UnresolvedDimension
    downstream_criticality: _DownstreamCriticality | _UnresolvedDimension
    effective_supply_concentration: _SupplyConcentration | _UnresolvedDimension
    qualification_barrier: _QualificationBarrier | _UnresolvedDimension
    capacity_inelasticity: _CapacityInelasticity | _UnresolvedDimension
    substitute_weakness: _SubstituteWeakness | _UnresolvedDimension


@dataclass(frozen=True)
class V16FactExtractionResult:
    case_id: str
    facts: dict
    ratings: dict[str, tuple[int, int]]
    attempts: int
    model: str
    prompt_sha256: str
    schema_sha256: str
    raw_response_sha256: str
    raw_response_sha256s: tuple[str, ...]


class _FactContractViolation(Exception):
    pass


class DeepSeekV16SegmentFactExtractor:
    """Extract v1.6 observable facts; deterministic code owns every ordinal."""

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
        expected_prompt_sha256: str = V16_FACT_PROMPT_SHA256,
        expected_schema_sha256: str = V16_FACT_SCHEMA_SHA256,
        expected_schema_id: str = _SCHEMA_VERSION,
        decoder=None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("v1.6 fact extractor timeout must be positive")
        root = Path(__file__).resolve().parents[4]
        self.prompt_path = Path(prompt_path) if prompt_path is not None else (
            root / "tools/theme-chokepoint/prompts/segment-fact-extractor-v1.6.md"
        )
        self.schema_path = Path(schema_path) if schema_path is not None else (
            root / "tools/theme-chokepoint/schemas/theme-chokepoint-segment-facts-v1.6.schema.json"
        )
        prompt_bytes = self.prompt_path.read_bytes()
        schema_bytes = self.schema_path.read_bytes()
        self.prompt_sha256 = sha256(prompt_bytes).hexdigest()
        self.schema_sha256 = sha256(schema_bytes).hexdigest()
        if self.prompt_sha256 != expected_prompt_sha256:
            raise ValueError(
                "v1.6 fact extractor prompt SHA-256 mismatch: "
                f"expected={expected_prompt_sha256} actual={self.prompt_sha256}"
            )
        if self.schema_sha256 != expected_schema_sha256:
            raise ValueError(
                "v1.6 fact extractor schema SHA-256 mismatch: "
                f"expected={expected_schema_sha256} actual={self.schema_sha256}"
            )
        self.prompt = prompt_bytes.decode("utf-8")
        self.schema = json.loads(schema_bytes.decode("utf-8"))
        if self.schema.get("$id") != expected_schema_id:
            raise ValueError("v1.6 fact extractor schema ID is not canonical")
        self._decoder = decoder or _decode_and_score

        self.model = model or _configured_model()
        if not self.model:
            raise RuntimeError("a configured model is required for v1.6 fact extraction")
        if client is None:
            resolved_key, provider = _configured_api_key(api_key)
            if not resolved_key:
                raise RuntimeError(
                    "a configured API key is required for v1.6 fact extraction"
                )
            from openai import OpenAI

            kwargs = {"api_key": resolved_key, "timeout": timeout_seconds}
            resolved_base = base_url or _configured_base_url(provider)
            if resolved_base:
                kwargs["base_url"] = resolved_base
            client = OpenAI(**kwargs)
        self.client = client

    def extract(self, *, case: dict, evidence: tuple[dict, ...]) -> V16FactExtractionResult:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("v1.6 fact extraction requires a case_id")
        expected_ids = case.get("evidence_ids")
        if (
            not isinstance(expected_ids, list)
            or len(expected_ids) != len(set(expected_ids))
            or any(not isinstance(item, str) or not item.strip() for item in expected_ids)
        ):
            raise ValueError("v1.6 case requires unique Evidence IDs")
        supplied_ids = [item.get("evidence_id") for item in evidence]
        if (
            len(supplied_ids) != len(set(supplied_ids))
            or set(supplied_ids) != set(expected_ids)
        ):
            raise ValueError("inference must receive exactly the case-bound Evidence IDs")

        messages = [
            {"role": "system", "content": self.prompt},
            {
                "role": "user",
                "content": _format_request(case, evidence, self.schema),
            },
        ]
        violation = "unspecified contract violation"
        response_hashes = []
        for attempt in range(1, 3):
            attempt_messages = list(messages)
            if attempt == 2:
                attempt_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "REPAIR REQUIRED: the previous response violated the v1.6 "
                            "fact-only contract. Return one corrected JSON object only. "
                            "Do not add ratings, bounds, states or commentary.\n"
                            f"VIOLATION: {violation}"
                        ),
                    }
                )
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=attempt_messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = _response_content(completion)
            response_hashes.append(sha256(content.encode("utf-8")).hexdigest())
            try:
                facts, ratings = self._decoder(content, set(expected_ids))
            except _FactContractViolation as error:
                violation = str(error)
                if attempt == 1:
                    continue
                raise RuntimeError(
                    "v1.6 fact extractor failed the structured contract after one repair: "
                    + violation
                ) from None
            return V16FactExtractionResult(
                case_id=case_id,
                facts=facts,
                ratings=ratings,
                attempts=attempt,
                model=self.model,
                prompt_sha256=self.prompt_sha256,
                schema_sha256=self.schema_sha256,
                raw_response_sha256=response_hashes[-1],
                raw_response_sha256s=tuple(response_hashes),
            )
        raise AssertionError("unreachable v1.6 fact extraction repair state")


def _response_content(completion) -> str:
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise _FactContractViolation("invalid response envelope") from None
    if not isinstance(content, str) or not content.strip():
        raise _FactContractViolation("empty response")
    return content


def _decode_and_score(
    content: str, allowed_evidence_ids: set[str]
) -> tuple[dict, dict[str, tuple[int, int]]]:
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
        ratings = score_v16_segment_facts(facts)
    except ValueError as error:
        raise _FactContractViolation(f"deterministic scoring violation:{error}") from None
    return facts, ratings


def _contains_model_score(value) -> bool:
    if isinstance(value, dict):
        return bool(_FORBIDDEN_SCORE_KEYS & set(value)) or any(
            _contains_model_score(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_model_score(item) for item in value)
    return False


def _collect_evidence_ids(value) -> set[str]:
    if isinstance(value, dict):
        result = set(value.get("evidence_ids", []))
        for item in value.values():
            result.update(_collect_evidence_ids(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_collect_evidence_ids(item))
        return result
    return set()


def _format_request(case: dict, evidence: tuple[dict, ...], schema: dict) -> str:
    return (
        "CASE_CONTEXT_JSON:\n"
        + json.dumps(case, ensure_ascii=False, sort_keys=True)
        + "\n\nCASE_BOUND_EVIDENCE_JSON:\n"
        + json.dumps(list(evidence), ensure_ascii=False, sort_keys=True)
        + "\n\nOUTPUT_SCHEMA_JSON:\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    )


__all__ = [
    "DeepSeekV16SegmentFactExtractor",
    "V16FactExtractionResult",
    "V16_FACT_PROMPT_SHA256",
    "V16_FACT_SCHEMA_SHA256",
]
