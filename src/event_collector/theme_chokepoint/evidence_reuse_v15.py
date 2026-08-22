"""Safe SourceVersion reuse across the Segment and Company assessment boundary."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from event_collector.theme_chokepoint.contracts import AssessmentScope, CompanyScope


def rebind_segment_evidence_to_company(
    *, claim, card, company_scope: CompanyScope, company_dimension: str
):
    """Create company-scoped derivatives without authorizing them for scoring.

    The immutable source and quote lineage are reused.  A later company evidence
    verifier must attach canonical capabilities before the derivative can become
    decisive scoring evidence.
    """

    scope = claim.assessment_scope
    if scope is None or scope.company_id is not None:
        raise ValueError("reusable Segment evidence must have a company-neutral Scope")
    if card.claim_id != claim.claim_id or card.assessment_scope != scope:
        raise ValueError("Segment Claim and EvidenceCard lineage is inconsistent")
    if company_scope.company_id not in set(claim.subject_company_ids):
        raise ValueError("Segment evidence does not identify the requested subject company")
    if set(card.subject_company_ids) != set(claim.subject_company_ids):
        raise ValueError("Claim and EvidenceCard subject company lineage differs")
    compared = (
        (scope.product_id, company_scope.product_id),
        (scope.segment_id, company_scope.segment_id),
        (scope.customer_or_platform_scope, company_scope.customer_or_platform_scope),
        (scope.geography, company_scope.geography),
        (scope.time_horizon_months, company_scope.time_horizon_months),
        (scope.as_of_date, company_scope.as_of_date),
    )
    if any(left != right for left, right in compared):
        raise ValueError("Segment evidence cannot cross the Company assessment Scope")
    if not card.source_identity_id or not card.source_version_id:
        raise ValueError("evidence reuse requires immutable SourceIdentity and SourceVersion")
    if not company_dimension.strip():
        raise ValueError("company evidence reuse requires a target dimension")

    rebound_scope = AssessmentScope(
        company_id=company_scope.company_id,
        product_id=company_scope.product_id,
        segment_id=company_scope.segment_id,
        customer_or_platform_scope=company_scope.customer_or_platform_scope,
        geography=company_scope.geography,
        time_horizon_months=company_scope.time_horizon_months,
        as_of_date=company_scope.as_of_date,
    )
    identity = "\0".join(
        (
            card.evidence_id,
            company_scope.company_id,
            company_dimension,
            card.source_version_id,
            card.exact_quote,
        )
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
    claim_id = f"company_rebound_claim_{digest}"
    evidence_id = f"company_rebound_evidence_{digest}"
    rebound_claim = replace(
        claim,
        claim_id=claim_id,
        material_field=company_dimension,
        primary_scoring_dimension=company_dimension,
        scoring_use="context_only",
        evidence_ids=(evidence_id,),
        fact_key=f"company_rebound_fact_{digest}",
        assessment_scope=rebound_scope,
        condition_ids=(),
        claim_capabilities=(),
        scoring_eligible=False,
        subject_company_ids=(company_scope.company_id,),
        derived_from_evidence_id=card.evidence_id,
    )
    rebound_card = replace(
        card,
        evidence_id=evidence_id,
        claim_id=claim_id,
        fact_key=f"company_rebound_fact_{digest}",
        assessment_scope=rebound_scope,
        condition_ids=(),
        claim_capabilities=(),
        primary_scoring_dimension=company_dimension,
        scoring_use="context_only",
        scoring_eligible=False,
        business_fact_assertions=(),
        subject_company_ids=(company_scope.company_id,),
        derived_from_evidence_id=card.evidence_id,
    )
    return rebound_claim, rebound_card
