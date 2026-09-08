from __future__ import annotations

import json
from types import SimpleNamespace

from event_collector.theme_chokepoint.providers.facts_v161 import (
    DeepSeekV161SegmentFactExtractor,
)


class _Completions:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload))
                )
            ]
        )


def test_v161_fact_adapter_scores_observable_proxies_without_model_ordinals():
    """SELECT INVARIANT: v1.6.1 proxy facts remain code-scored and evidence-bound."""
    facts = {
        "schema_version": "theme-chokepoint-segment-facts-v1.6.1",
        "demand_pressure": {
            "unresolved": False,
            "canonical_annual_growth": None,
            "direct_transmission_verified": False,
            "inventory_verified": False,
            "annual_growth_basis": None,
            "demand_proxy_annual_growth": 0.30,
            "demand_proxy_directly_attributed": True,
            "demand_proxy_capacity_prebooked": True,
            "evidence_ids": ["E1"],
        },
        "downstream_criticality": {
            "unresolved": False,
            "scope_dependency_verified": True,
            "structural_hard_block": "unknown",
            "explicit_no_impact": False,
            "cohorts": [],
            "evidence_ids": ["E1"],
        },
        "effective_supply_concentration": {
            "unresolved": False,
            "target_demand": None,
            "suppliers": [
                {
                    "supplier_id": "sole-supplier",
                    "nameplate_output": None,
                    "yield_fraction": None,
                    "qualification_fraction": None,
                    "target_scope_allocation_fraction": None,
                    "availability_fraction": None,
                    "additional_qualified_output_90d": None,
                }
            ],
            "sole_effective_supplier_verified": True,
            "qualified_failover_absence_verified": True,
            "effective_supplier_count_upper_bound": 1,
            "evidence_ids": ["E1"],
        },
        "qualification_barrier": {
            "unresolved": False,
            "duration_months": None,
            "qualification_required": True,
            "explicit_no_special_qualification": False,
            "process_signals": ["type_test", "customer_specific_validation"],
            "scope_coverage": "partial_subtype",
            "evidence_ids": ["E1"],
        },
        "capacity_inelasticity": {
            "unresolved": False,
            "top1_loss_output": None,
            "failover_output_90d": None,
            "stable_incremental_output": None,
            "tasks": [],
            "minimum_physical_lead_time_months": 12,
            "physical_expansion_required": True,
            "physical_constraint_signals": ["equipment_build"],
            "evidence_ids": ["E1"],
        },
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
                    "capacity_pool_id": None,
                    "category": "different_technology",
                }
            ],
            "evidence_ids": ["E1"],
        },
    }
    completions = _Completions(facts)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    extractor = DeepSeekV161SegmentFactExtractor(
        client=client,
        model="deepseek-v161-test",
    )

    result = extractor.extract(
        case={
            "case_id": "case-1",
            "evidence_ids": ["E1"],
            "assessment_scope": {"segment_id": "segment-1"},
            "state_context": {},
        },
        evidence=(
            {
                "evidence_id": "E1",
                "exact_quote": "A required input with a public physical lead time.",
                "quote_context": "Complete bounded source context.",
                "limitations": "Scope limitations remain explicit.",
            },
        ),
    )

    assert result.ratings == {
        "demand_pressure": (3, 4),
        "downstream_criticality": (4, 4),
        "effective_supply_concentration": (4, 4),
        "qualification_barrier": (3, 4),
        "capacity_inelasticity": (3, 4),
        "substitute_weakness": (0, 2),
    }
    assert result.attempts == 1
    assert len(result.raw_response_sha256s) == 1
    assert completions.calls[0]["temperature"] == 0
