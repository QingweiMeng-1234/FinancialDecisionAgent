from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.providers.facts_v16 import (
    DeepSeekV16SegmentFactExtractor,
)


ROOT = Path(__file__).resolve().parents[1]


def _valid_facts():
    return {
        "schema_version": "theme-chokepoint-segment-facts-v1.6",
        "demand_pressure": {
            "unresolved": False,
            "canonical_annual_growth": 0.15,
            "direct_transmission_verified": True,
            "inventory_verified": False,
            "annual_growth_basis": "quarterly_yoy",
            "evidence_ids": ["E1"],
        },
        "downstream_criticality": {
            "unresolved": False,
            "structural_hard_block": "pass",
            "explicit_no_impact": False,
            "cohorts": [],
            "evidence_ids": ["E1"],
        },
        "effective_supply_concentration": {
            "unresolved": False,
            "target_demand": 100,
            "suppliers": [
                {
                    "supplier_id": "supplier-1",
                    "nameplate_output": 100,
                    "yield_fraction": 1,
                    "qualification_fraction": 1,
                    "target_scope_allocation_fraction": 1,
                    "availability_fraction": 1,
                    "additional_qualified_output_90d": 0,
                }
            ],
            "evidence_ids": ["E1"],
        },
        "qualification_barrier": {
            "unresolved": False,
            "duration_months": 18,
            "qualification_required": True,
            "explicit_no_special_qualification": False,
            "process_signals": ["type_test"],
            "evidence_ids": ["E1"],
        },
        "capacity_inelasticity": {
            "unresolved": False,
            "top1_loss_output": 100,
            "failover_output_90d": 0,
            "stable_incremental_output": 100,
            "tasks": [
                {
                    "task_id": "fab",
                    "duration_months": 8,
                    "dependencies": [],
                    "constraint_class": "cleanroom",
                },
                {
                    "task_id": "tooling",
                    "duration_months": 7,
                    "dependencies": ["fab"],
                    "constraint_class": "equipment",
                },
            ],
            "evidence_ids": ["E1"],
        },
        "substitute_weakness": {
            "unresolved": False,
            "target_demand": 100,
            "discovery_rounds_without_new_routes": 2,
            "required_categories": ["architecture"],
            "searched_categories": ["architecture"],
            "explicit_negative_categories": ["architecture"],
            "budget_exhausted": False,
            "routes": [],
            "evidence_ids": ["E1"],
        },
    }


def _unresolved_facts():
    facts = {"schema_version": "theme-chokepoint-segment-facts-v1.6"}
    for dimension in (
        "demand_pressure",
        "downstream_criticality",
        "effective_supply_concentration",
        "qualification_barrier",
        "capacity_inelasticity",
        "substitute_weakness",
    ):
        facts[dimension] = {"unresolved": True, "evidence_ids": ["E1"]}
    return facts


class _Completions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload))
                )
            ]
        )


def _client(*payloads):
    completions = _Completions(payloads)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    ), completions


def _case():
    return {
        "case_id": "case-1",
        "assessment_scope": {
            "segment_id": "segment-1",
            "as_of_date": "2026-08-23",
        },
        "evidence_ids": ["E1"],
        "state_context": {
            "counter_evidence_search_complete": True,
        },
    }


def _evidence():
    return (
        {
            "evidence_id": "E1",
            "exact_quote": "A quoted original-source fact.",
            "quote_context": "The complete bounded source context.",
            "limitations": "Scope remains explicit.",
        },
    )


def test_v16_fact_extractor_uses_pinned_assets_and_deterministic_scoring():
    """SELECT INVARIANT: the model extracts facts; code owns all six ordinals."""
    client, completions = _client(_valid_facts())
    extractor = DeepSeekV16SegmentFactExtractor(
        client=client,
        model="deepseek-v16-test",
    )

    result = extractor.extract(case=_case(), evidence=_evidence())

    assert result.case_id == "case-1"
    assert result.attempts == 1
    assert result.ratings == {
        "demand_pressure": (2, 2),
        "downstream_criticality": (4, 4),
        "effective_supply_concentration": (4, 4),
        "qualification_barrier": (4, 4),
        "capacity_inelasticity": (4, 4),
        "substitute_weakness": (4, 4),
    }
    assert result.prompt_sha256 == (
        "76414029343b863bda9983faf133a543fdffccae60381f8cbc82665ae38af642"
    )
    assert result.schema_sha256 == (
        "9924038787b83eae2692c82e27bec16f61007990327fdd2a5af58840202e2d1f"
    )
    call = completions.calls[0]
    assert call["model"] == "deepseek-v16-test"
    assert call["temperature"] == 0
    assert call["response_format"] == {"type": "json_object"}
    assert "Theme Chokepoint Segment Fact Extractor v1.6" in (
        call["messages"][0]["content"]
    )
    request_payload = call["messages"][1]["content"]
    assert "CASE_CONTEXT_JSON" in request_payload
    assert "OUTPUT_SCHEMA_JSON" in request_payload
    assert "A quoted original-source fact." in request_payload


def test_v16_fact_extractor_repairs_model_authored_rating_once():
    """SELECT INVARIANT: model-authored scores are rejected before deterministic scoring."""
    invalid = _valid_facts()
    invalid["demand_pressure"]["rating_min"] = 2
    client, completions = _client(invalid, _valid_facts())
    extractor = DeepSeekV16SegmentFactExtractor(
        client=client,
        model="deepseek-v16-test",
    )

    result = extractor.extract(case=_case(), evidence=_evidence())

    assert result.attempts == 2
    assert result.raw_response_sha256s == tuple(
        sha256(json.dumps(payload).encode("utf-8")).hexdigest()
        for payload in (invalid, _valid_facts())
    )
    assert result.raw_response_sha256 == result.raw_response_sha256s[-1]
    assert len(completions.calls) == 2
    repair = completions.calls[1]["messages"][-1]["content"]
    assert "REPAIR REQUIRED" in repair
    assert "model-authored rating" in repair


def test_v16_fact_extractor_maps_explicitly_unresolved_dimensions_to_unknown():
    """SELECT INVARIANT: missing evidence stays unresolved instead of using fake inputs."""
    client, _ = _client(_unresolved_facts())
    extractor = DeepSeekV16SegmentFactExtractor(
        client=client,
        model="deepseek-v16-test",
    )

    result = extractor.extract(case=_case(), evidence=_evidence())

    assert result.ratings == {
        dimension: (0, 4)
        for dimension in (
            "demand_pressure",
            "downstream_criticality",
            "effective_supply_concentration",
            "qualification_barrier",
            "capacity_inelasticity",
            "substitute_weakness",
        )
    }


def test_v16_fact_extractor_rejects_evidence_outside_case_binding():
    """SELECT INVARIANT: inference receives exactly the case-bound Evidence IDs."""
    client, completions = _client(_valid_facts())
    extractor = DeepSeekV16SegmentFactExtractor(
        client=client,
        model="deepseek-v16-test",
    )

    with pytest.raises(ValueError, match="case-bound Evidence IDs"):
        extractor.extract(
            case=_case(),
            evidence=({"evidence_id": "E2", "exact_quote": "outside"},),
        )

    assert completions.calls == []


def test_v16_fact_extractor_rejects_modified_prompt_bytes(tmp_path):
    """SELECT INVARIANT: prompt bytes remain hash-pinned before any model call."""
    prompt = tmp_path / "prompt.md"
    prompt.write_text("modified prompt", encoding="utf-8")
    client, completions = _client(_valid_facts())

    with pytest.raises(ValueError, match="prompt SHA-256 mismatch"):
        DeepSeekV16SegmentFactExtractor(
            client=client,
            model="deepseek-v16-test",
            prompt_path=prompt,
        )

    assert completions.calls == []
