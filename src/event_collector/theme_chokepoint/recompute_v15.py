"""Create immutable Stage 3 recompute requests from Stage 4 discoveries."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from event_collector.theme_chokepoint.contracts import SegmentRecomputeRequest


SEGMENT_DIMENSIONS = {
    "demand_pressure",
    "downstream_criticality",
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
}


def build_segment_recompute_requests(
    *, run_id: str, stage3_result, company_claims, created_at: datetime | None = None
) -> tuple[SegmentRecomputeRequest, ...]:
    created_at = created_at or datetime.now(timezone.utc)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("segment recompute request time must be timezone-aware")
    grouped = {}
    for claim in company_claims:
        scope = claim.assessment_scope
        if (
            scope is None
            or scope.company_id is None
            or not claim.scoring_eligible
            or claim.primary_scoring_dimension not in SEGMENT_DIMENSIONS
        ):
            continue
        bucket = grouped.setdefault(scope.segment_id, {"claims": [], "evidence": []})
        bucket["claims"].append(claim.claim_id)
        bucket["evidence"].extend(claim.evidence_ids)
    results = []
    for segment_id in sorted(grouped):
        claim_ids = tuple(dict.fromkeys(grouped[segment_id]["claims"]))
        evidence_ids = tuple(dict.fromkeys(grouped[segment_id]["evidence"]))
        material = "\0".join(
            (
                run_id,
                segment_id,
                stage3_result.executable_contract_sha256,
                *claim_ids,
                *evidence_ids,
            )
        )
        request_id = "segment_recompute_" + sha256(
            material.encode("utf-8")
        ).hexdigest()[:24]
        results.append(
            SegmentRecomputeRequest(
                request_id=request_id,
                run_id=run_id,
                segment_id=segment_id,
                triggering_claim_ids=claim_ids,
                triggering_evidence_ids=evidence_ids,
                source_stage3_contract_id=stage3_result.executable_contract_id,
                source_stage3_contract_sha256=stage3_result.executable_contract_sha256,
                reason="stage4_company_fact_may_change_segment_score",
                created_at=created_at,
            )
        )
    return tuple(results)
