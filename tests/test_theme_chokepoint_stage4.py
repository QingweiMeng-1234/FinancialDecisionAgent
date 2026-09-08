from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    AnchorConditionResult,
    BoundBasis,
    BusinessFactAssertion,
    BusinessFactVerificationParsedResultRecord,
    BusinessFactVerificationRawResponseRecord,
    BusinessFactVerificationReconciliationRecord,
    BusinessFactVerificationRequestRecord,
    ChallengerSet,
    Claim,
    CompanyAssessmentDraft,
    CompanyChainReceipt,
    CompanyExposure,
    CompanyProviderReconciliationRecord,
    CompanyScope,
    CounterSearchReceipt,
    CounterSearchRouteFinding,
    DemandFrame,
    DependencyEdge,
    DimensionRatingDraft,
    EvidenceCard,
    FactVerificationReceipt,
    GateResult,
    MilestoneAxes,
    ProductAnchor,
    RedTeamReview,
    ResearchRequest,
    RunStatus,
    SegmentAssessment,
    SourceSnapshot,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.fact_verification import (
    business_fact_assertion_sha256,
    business_fact_verification_source_context_sha256,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceResolutionRawResponse,
)
from event_collector.theme_chokepoint.stage4 import (
    CANONICAL_GATE_PREDICATES,
    CompanyExposureRedTeamService,
    _authorized_claim_capabilities,
    _business_fact_decision_table,
    _index_company_drafts,
    _company_assessment_id,
    _validate_earnings_evidence,
    _validate_counter_search_receipt,
)
from event_collector.theme_chokepoint import stage4 as stage4_module


DEFENSIBILITY = (
    "technical_performance_gap",
    "qualification_lock_in",
    "switching_cost",
    "qualified_effective_capacity",
    "quality_delivery_reliability",
    "customer_sourcing_evidence",
)
REPLACEMENT = (
    "performance_parity",
    "qualification_progress",
    "capacity_readiness",
    "customer_adoption",
    "cost_tco_advantage",
    "execution_delivery",
    "regulatory_tailwind",
    "architecture_tailwind",
)
EARNINGS = (
    "revenue_materiality",
    "volume_realization_leverage",
    "pricing_power",
    "margin_transmission",
    "time_to_revenue",
    "capital_cash_burden",
    "customer_concentration_exposure",
    "customer_relationship_protection",
    "earnings_persistence",
)
CONTROLLED_OVERLAY_ID = "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
CONTROLLED_OVERLAY_SHA256 = (
    "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
)
CONTROLLED_GOVERNANCE_BUNDLE_SHA256 = (
    "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
)


def test_stage4_state_consumer_requires_reloaded_four_role_reconciliations():
    """SELECT INVARIANT: receipt strings cannot replace the repository four-role JOIN."""
    receipt_ids = (
        "company-discovery-reconciliation-1",
        "company-evidence-reconciliation-1",
        "company-scoring-reconciliation-1",
        "company-critic-reconciliation-1",
    )
    stored = tuple(
        CompanyProviderReconciliationRecord(
            receipt_id=receipt_id,
            request_record_id=f"request-{role}",
            response_record_id=f"response-{role}",
            result_record_id=f"result-{role}",
            run_id="run-four-role-reload",
            role=role,
            reconciled_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        )
        for role, receipt_id in zip(
            ("discovery", "evidence", "scoring", "critic"), receipt_ids
        )
    )
    repository = SimpleNamespace(
        list_company_provider_reconciliations=lambda _run_id: stored[:-1]
    )
    result = SimpleNamespace(provider_request_receipt_ids=receipt_ids)

    with pytest.raises(ValueError, match="four-role repository reconciliation"):
        stage4_module._reload_company_provider_reconciliations(
            repository, "run-four-role-reload", result
        )


def test_business_fact_assertion_contract_carries_semantic_authority_lineage():
    """SELECT INVARIANT: capability authority is a persisted typed fact, never text tags."""
    assertion_fields = {
        "assertion_id",
        "evidence_id",
        "subject_company_id",
        "subject_product_id",
        "canonical_predicate_id",
        "polarity",
        "lifecycle_state",
        "quote_start",
        "quote_end",
        "exact_quote_sha256",
        "verification",
        "accounting_metric",
        "accounting_value",
        "accounting_unit",
        "currency",
        "fiscal_period_type",
        "fiscal_period_id",
        "accounting_basis",
    }
    verification_fields = {
        "request_record_id",
        "response_record_id",
        "receipt_id",
        "verifier",
        "verified_at",
        "raw_response_sha256",
        "decision",
    }

    assert assertion_fields <= set(BusinessFactAssertion.__dataclass_fields__)
    assert verification_fields <= set(FactVerificationReceipt.__dataclass_fields__)
    assert "business_fact_assertions" in EvidenceCard.__dataclass_fields__


def _verified_assertion(
    card,
    capability,
    *,
    company_id,
    product_id,
    polarity="affirmative",
    lifecycle_state="current",
    decision="verified",
):
    is_accounting = capability in {
        "accounting_revenue_confirmed",
        "multi_period_revenue_confirmed",
    }
    return BusinessFactAssertion(
        assertion_id=f"assertion-{card.evidence_id}-{capability}",
        evidence_id=card.evidence_id,
        subject_company_id=company_id,
        subject_product_id=product_id,
        canonical_predicate_id=f"capability.{capability}.v1.4",
        polarity=polarity,
        lifecycle_state=lifecycle_state,
        quote_start=card.quote_start,
        quote_end=card.quote_end,
        exact_quote_sha256=sha256(card.exact_quote.encode("utf-8")).hexdigest(),
        verification=FactVerificationReceipt(
            request_record_id=f"verify-request-{card.evidence_id}",
            response_record_id=f"verify-response-{card.evidence_id}",
            receipt_id=f"verify-receipt-{card.evidence_id}",
            verifier="independent-business-fact-verifier-v1",
            verified_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
            raw_response_sha256="a" * 64,
            decision=decision,
        ),
        accounting_metric="revenue" if is_accounting else None,
        accounting_value=25.0 if is_accounting else None,
        accounting_unit="million" if is_accounting else None,
        currency="USD" if is_accounting else None,
        fiscal_period_type=(
            card.accounting_period_type or "quarter" if is_accounting else None
        ),
        fiscal_period_id=(
            card.accounting_period_id or "2026Q2" if is_accounting else None
        ),
        accounting_basis="GAAP" if is_accounting else None,
    )


def _persist_assertion_verification(repository, run_id, assertion):
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    assertion_hash = business_fact_assertion_sha256(assertion)
    stage3 = repository.get_stage3_result(run_id)
    card = next(
        item for item in stage3.evidence_cards if item.evidence_id == assertion.evidence_id
    )
    snapshot = next(
        item for item in stage3.source_snapshots if item.article_id == card.article_id
    )
    class ResolutionClient:
        def resolve(self, **kwargs):
            payload = {
                "observed_url": kwargs["source_url"],
                "terminal_url": kwargs["source_url"],
                "redirect_chain": [],
                "relation": "original",
                "origin_url": None,
                "resolution_status": "verified",
            }
            return SourceResolutionRawResponse(
                provider="controlled-stage4-source-resolver",
                provider_trace_id=(
                    "stage4-source-resolution-"
                    + sha256(kwargs["source_url"].encode("utf-8")).hexdigest()[:16]
                ),
                http_status=200,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=now,
            )

    identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(ResolutionClient()).resolve(card.canonical_url),
        canonical_publisher_id=f"issuer:{assertion.subject_company_id}",
        canonical_document_id=card.article_id,
        origin_event_id=card.origin_event_id,
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=now,
        raw_bytes=snapshot.original_text.encode("utf-8"),
        normalized_content=snapshot.original_text,
        quote_span=(card.quote_start, card.quote_end, card.exact_quote),
        content_type="text/plain",
        language="en",
        publication_time=None,
        updated_time=None,
    )
    source_context = repository.load_business_fact_verification_source_context(
        source_identity_id=identity.source_identity_id,
        source_version_id=version.source_version_id,
        source_type=card.source_type,
        issuer_company_id=assertion.subject_company_id,
        product_id=assertion.subject_product_id,
        fiscal_period_id=assertion.fiscal_period_id,
        accounting_metric=assertion.accounting_metric,
        quote_start=assertion.quote_start,
        quote_end=assertion.quote_end,
        exact_quote_sha256=assertion.exact_quote_sha256,
    )
    source_context_sha256 = business_fact_verification_source_context_sha256(
        source_context
    )
    request = BusinessFactVerificationRequestRecord(
        request_record_id=f"fact-request-{assertion.assertion_id}",
        run_id=run_id,
        assertion_id=assertion.assertion_id,
        evidence_id=assertion.evidence_id,
        assertion_sha256=assertion_hash,
        governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        producer_execution_id="controlled-evidence-producer",
        verifier_execution_id="controlled-independent-verifier",
        source_context=source_context,
        source_context_sha256=source_context_sha256,
        created_at=now,
    )
    raw_body = json.dumps(
        {
            "assertion_id": assertion.assertion_id,
            "assertion_sha256": assertion_hash,
            "decision": "verified",
        },
        sort_keys=True,
    ).encode("utf-8")
    response = BusinessFactVerificationRawResponseRecord(
        response_record_id=f"fact-response-{assertion.assertion_id}",
        request_record_id=request.request_record_id,
        run_id=run_id,
        verifier=request.verifier_execution_id,
        provider_trace_id=f"fact-trace-{assertion.assertion_id}",
        http_status=200,
        raw_body=raw_body,
        raw_response_sha256=sha256(raw_body).hexdigest(),
        retrieved_at=now,
        cost_usd=0.0,
    )
    result = BusinessFactVerificationParsedResultRecord(
        result_record_id=f"fact-result-{assertion.assertion_id}",
        request_record_id=request.request_record_id,
        response_record_id=response.response_record_id,
        run_id=run_id,
        assertion_id=assertion.assertion_id,
        assertion_sha256=assertion_hash,
        decision="verified",
        parsed_at=now,
    )
    repository.save_business_fact_verification_request(request)
    repository.save_business_fact_verification_raw_response(response)
    repository.save_business_fact_verification_result(result)
    repository.reconcile_business_fact_verification(
        BusinessFactVerificationReconciliationRecord(
            receipt_id=f"fact-reconciliation-{assertion.assertion_id}",
            run_id=run_id,
            assertion_id=assertion.assertion_id,
            request_record_id=request.request_record_id,
            response_record_id=response.response_record_id,
            result_record_id=result.result_record_id,
            governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
            source_identity_id=source_context.source_identity_id,
            source_version_id=source_context.source_version_id,
            source_context_sha256=source_context_sha256,
            reconciled_at=now,
        ),
        assertion=assertion,
    )


def _exact(
    name, score=3, *, company_id, evidence_key=None, replacement_mode=None
):
    evidence_key = evidence_key or company_id
    evidence_id = f"ev-{evidence_key}-{name}"
    return DimensionRatingDraft(
        dimension=name,
        rating_min=score,
        rating_max=score,
        evidence_state="supported",
        bound_type="exact",
        bound_basis=BoundBasis(
            floor_anchor=score,
            ceiling_anchor=score,
            exact_basis="natural_cap" if score == 4 else "direct_upper_bound",
            excluded_higher_anchors=tuple(range(score + 1, 5)),
        ),
        evidence_ids=(evidence_id,),
        decisive_evidence_ids=(evidence_id,),
        stale=False,
        anchor_conditions=(
            AnchorConditionResult(
                condition_id=f"{name}.anchor_{score}.floor",
                condition_type="floor",
                evidence_state="supported",
                evidence_ids=(evidence_id,),
                decisive_claim_ids=(f"claim-{evidence_key}-{name}",),
                anchor=score,
            ),
            *(
                (
                    AnchorConditionResult(
                        condition_id=f"{name}.anchor_{score}.ceiling",
                        condition_type="ceiling",
                        evidence_state="supported",
                        evidence_ids=(evidence_id,),
                        decisive_claim_ids=(f"claim-{evidence_key}-{name}",),
                        anchor=score,
                        excluded_higher_anchors=tuple(range(score + 1, 5)),
                    ),
                )
                if score < 4
                else ()
            ),
        ),
        anchor_profile=(
            f"replacement.{replacement_mode}.{name}.v1.4"
            if replacement_mode
            else None
        ),
    )


def _unknown(name):
    return DimensionRatingDraft(
        dimension=name,
        rating_min=0,
        rating_max=4,
        evidence_state="unknown",
        bound_type="none",
        bound_basis=BoundBasis(),
        evidence_ids=(),
        stale=False,
    )


def _replacement_unknown(name, mode="product"):
    return replace(
        _unknown(name),
        anchor_profile=f"replacement.{mode}.{name}.v1.4",
    )


def _bind_dimension_evidence(dimension, evidence_ids):
    claim_ids = tuple(
        evidence_id.replace("ev-", "claim-", 1) for evidence_id in evidence_ids
    )
    return replace(
        dimension,
        evidence_ids=tuple(evidence_ids),
        decisive_evidence_ids=tuple(evidence_ids),
        anchor_conditions=tuple(
            replace(
                condition,
                evidence_ids=tuple(evidence_ids),
                decisive_claim_ids=claim_ids,
            )
            for condition in dimension.anchor_conditions
        ),
    )


def _ready_stage3(
    repository,
    *,
    ready=True,
    scoring_use="primary",
    shared_fact_key_dimensions=(),
    additional_powerco_scopes=(),
    exact_zero_evidence_mode=None,
    extra_capabilities=(),
    evidence_stance_overrides=(),
    earnings_evidence_mode="valid",
    persist_fact_verifications=True,
):
    request = ResearchRequest(
        run_id="run-stage4-001",
        theme="AI data-center power",
        trigger="AI load growth",
        region="global",
        as_of_date=date(2026, 8, 16),
        time_horizon_months=24,
        analysis_goal="company exposure",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=2,
        max_nodes=10,
        max_iterations=2,
        max_sources=10,
        max_time_seconds=900,
        max_cost_usd=10,
        max_product_anchors=1,
    )
    repository.create_request(request)
    frame = DemandFrame(
        normalized_theme=request.theme,
        scope="global 24 months",
        exclusions=(),
        demand_hypothesis="AI loads raise UPS demand.",
        measurable_demand_variables=("MW",),
        time_horizon_months=24,
        unresolved_questions=(),
    )
    anchor = ProductAnchor(
        anchor_id="anchor-1",
        product_name="UPS",
        buyer_or_user="operator",
        demand_variable="MW",
        theme_link="AI load needs UPS",
        confidence=0.8,
        supporting_evidence_ids=(),
        missing_evidence=(),
        status="proposed",
    )
    repository.save_framing(
        request.run_id,
        frame,
        status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
        anchors=(anchor,),
    )
    repository.confirm_product_anchors(
        request.run_id, (anchor.anchor_id,), confirmed_by="analyst"
    )
    product = SupplyChainNode(
        node_id="node-product",
        normalized_name="ups",
        node_type="product",
        depth=0,
        status="confirmed",
        description="UPS",
        aliases=(),
        product_anchor_id=anchor.anchor_id,
    )
    segment = SupplyChainNode(
        node_id="segment-ups",
        normalized_name="qualified data center ups",
        node_type="equipment_segment",
        depth=1,
        status="supported",
        description="qualified UPS segment",
        aliases=(),
    )
    edge = DependencyEdge(
        edge_id="edge-1",
        downstream_node_id=product.node_id,
        upstream_node_id=segment.node_id,
        relation_type="equipment",
        demand_transmission="AI load raises UPS demand",
        criticality_hypothesis="No UPS delays commissioning",
        substitute_hypothesis="Multiple qualified vendors may substitute",
        confidence=0.8,
        supporting_claim_ids=(),
        verification_questions=("Who is qualified?",),
        status="supported",
    )
    repository.save_supply_chain_graph(
        request.run_id,
        nodes=(product, segment),
        edges=(edge,),
        truncation_reasons=(),
    )
    if ready:
        claims = []
        cards = []
        snapshots = []

        def add_evidence(
            company_id,
            product_id,
            dimension,
            *,
            context=False,
            evidence_key=None,
            accounting_period_type=None,
            accounting_period_id=None,
        ):
            evidence_key = evidence_key or company_id
            evidence_id = f"ev-{evidence_key}-{dimension}"
            claim_id = f"claim-{evidence_key}-{dimension}"
            use = "context_only" if context else scoring_use
            scope = AssessmentScope(
                company_id=company_id,
                product_id=product_id,
                segment_id=segment.node_id,
                customer_or_platform_scope="global hyperscale data centers",
                geography="global ex-China",
                time_horizon_months=24,
                as_of_date=date(2026, 8, 16),
            )
            is_exact_zero_target = (
                company_id == "powerco"
                and dimension == DEFENSIBILITY[0]
                and evidence_key == "powerco"
            )
            effective_scope = (
                None
                if is_exact_zero_target and exact_zero_evidence_mode == "scopeless"
                else scope
            )
            scoring_eligible = not context and not (
                is_exact_zero_target and exact_zero_evidence_mode == "ineligible"
            )
            gate_capabilities = {
                "customer_sourcing_evidence": (
                    "customer_dependency_current_confirmed",
                ),
                "capital_cash_burden": ("capital_hard_fail_clear_confirmed",),
                "customer_adoption": (
                    "production_use_confirmed",
                    "scaled_adoption_confirmed",
                ),
                "capacity_readiness": (
                    "qualified_saleable_output_confirmed",
                    "scaled_capacity_confirmed",
                ),
                "execution_delivery": ("execution_delivery_confirmed",),
                "cost_tco_advantage": ("tco_fatal_blocker_clear_confirmed",),
                "regulatory_tailwind": (
                    "regulatory_fatal_blocker_clear_confirmed",
                ),
                "architecture_tailwind": (
                    "architecture_fatal_blocker_clear_confirmed",
                ),
            }
            earnings_capabilities = {
                "revenue_materiality": ("accounting_revenue_confirmed",),
                "time_to_revenue": ("accounting_revenue_confirmed",),
                "earnings_persistence": (
                    "accounting_revenue_confirmed",
                    "multi_period_revenue_confirmed",
                ),
            }
            injected_capabilities = tuple(
                capability
                for target_dimension, capability in extra_capabilities
                if target_dimension == dimension
            )
            evidence_stance = next(
                (
                    stance
                    for target_dimension, stance in evidence_stance_overrides
                    if target_dimension == dimension
                ),
                "supports",
            )
            semantic_capabilities = (
                gate_capabilities.get(dimension, ())
                if earnings_evidence_mode == "generic"
                else (
                    *gate_capabilities.get(dimension, ()),
                    *earnings_capabilities.get(dimension, ()),
                )
            )
            condition_ids = (
                ()
                if context
                else (
                    f"{dimension}.anchor",
                    *(
                        f"capability.{capability}.v1.4"
                        for capability in semantic_capabilities
                    ),
                )
            )
            semantic_text = "; ".join(
                capability.removesuffix("_confirmed").replace("_", " ")
                for capability in semantic_capabilities
            )
            exact_quote = (
                f"{company_id} {product_id} {semantic_text}"
                if semantic_text
                else "direct fact"
            )
            original_text = f"{exact_quote}. {evidence_key} fact for {dimension}"
            content_hash = "sha256:" + sha256(original_text.encode("utf-8")).hexdigest()
            article_id = f"article-{evidence_key}-{dimension}"
            canonical_url = f"https://example.com/{evidence_key}/{dimension}"
            claim = Claim(
                claim_id=claim_id,
                node_id=segment.node_id,
                claim_type="source_fact",
                statement=f"{company_id}: {exact_quote}",
                material_field=dimension,
                primary_scoring_dimension=(dimension if use == "primary" else None),
                scoring_use=use,
                evidence_ids=(evidence_id,),
                fact_key=(
                    "fact-powerco-shared-high"
                    if company_id == "powerco" and dimension in shared_fact_key_dimensions
                    else f"fact-{evidence_key}-{dimension}"
                ),
                assessment_scope=effective_scope,
                condition_ids=condition_ids,
                claim_capabilities=(
                    "general_scoring_evidence",
                    *semantic_capabilities,
                    *injected_capabilities,
                ),
                scoring_eligible=scoring_eligible,
            )
            card = EvidenceCard(
                evidence_id=evidence_id,
                claim_id=claim_id,
                article_id=article_id,
                canonical_url=canonical_url,
                source_title="Company filing",
                publisher="Example",
                source_type="company_filing",
                publication_date=date(2026, 8, 1),
                data_as_of_date=date(2026, 6, 30),
                location="segment note",
                quote_start=0,
                quote_end=len(exact_quote),
                exact_quote=exact_quote,
                content_hash=content_hash,
                stance=evidence_stance,
                limitations="single source",
                extraction_model="extractor-v1",
                prompt_version="prompt-v1",
                origin_event_id=f"event-{evidence_key}-{dimension}",
                evidence_family_id=f"family-{evidence_key}-{dimension}",
                fact_key=claim.fact_key,
                assessment_scope=effective_scope,
                condition_ids=condition_ids,
                claim_capabilities=claim.claim_capabilities,
                primary_scoring_dimension=claim.primary_scoring_dimension,
                scoring_use=use,
                scoring_eligible=claim.scoring_eligible,
                accounting_period_type=accounting_period_type,
                accounting_period_id=accounting_period_id,
            )
            card = replace(
                card,
                business_fact_assertions=tuple(
                    _verified_assertion(
                        card,
                        capability,
                        company_id=company_id,
                        product_id=product_id,
                    )
                    for capability in semantic_capabilities
                ),
            )
            claims.append(claim)
            cards.append(card)
            snapshots.append(
                SourceSnapshot(
                    article_id=article_id,
                    canonical_url=canonical_url,
                    content_hash=content_hash,
                    original_text=original_text,
                )
            )

        add_evidence("powerco", "ups-x", "context", context=True)
        add_evidence("challengerco", "ups-y", "context", context=True)
        for dimension in (*DEFENSIBILITY, *EARNINGS[:-1]):
            add_evidence("powerco", "ups-x", dimension)
        period_count = {
            "valid": 4,
            "non_quarter": 4,
            "three_quarters": 3,
            "single_period": 1,
            "generic": 1,
        }[earnings_evidence_mode]
        period_type = "annual" if earnings_evidence_mode == "non_quarter" else "quarter"
        for period_number in range(1, period_count + 1):
            add_evidence(
                "powerco",
                "ups-x",
                "earnings_persistence",
                evidence_key=f"powerco-earnings-q{period_number}",
                accounting_period_type=period_type,
                accounting_period_id=f"2025Q{period_number}",
            )
        for evidence_key, product_id in additional_powerco_scopes:
            add_evidence(
                "powerco", product_id, "context", context=True,
                evidence_key=evidence_key,
            )
            for dimension in (*DEFENSIBILITY, *EARNINGS[:-1]):
                add_evidence(
                    "powerco", product_id, dimension, evidence_key=evidence_key
                )
            for period_number in range(1, 5):
                add_evidence(
                    "powerco",
                    product_id,
                    "earnings_persistence",
                    evidence_key=f"{evidence_key}-earnings-q{period_number}",
                    accounting_period_type="quarter",
                    accounting_period_id=f"2025Q{period_number}",
                )
        for dimension in (*REPLACEMENT, "ecosystem_compatibility", "displacement"):
            add_evidence("challengerco", "ups-y", dimension)
        segment_assessment = SegmentAssessment(
            segment_id=segment.node_id,
            contract_version="theme-chokepoint-scoring-v1.4",
            dimensions=(),
            score_min=75,
            score_max=75,
            presence_coverage=1,
            resolved_coverage=1,
            decision_coverage=1,
            conflicted_weight_share=0,
            unknown_weight_share=0,
            stale_weight_share=0,
            primary_state="candidate_chokepoint",
            missing_material_fields=(),
            relief_horizon="12_to_24_months",
        )
        repository.save_stage3_result(
            request.run_id,
            status=RunStatus.CHOKEPOINT_ASSESSMENT_READY,
            contract_version="theme-chokepoint-scoring-v1.4",
            executable_contract_id=CONTROLLED_OVERLAY_ID,
            executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
            claims=tuple(claims),
            evidence_cards=tuple(cards),
            source_snapshots=tuple(snapshots),
            assessments=(segment_assessment,),
            iterations_completed=1,
            incomplete_reasons=(),
        )
        if persist_fact_verifications:
            for card in cards:
                for assertion in card.business_fact_assertions:
                    _persist_assertion_verification(
                        repository,
                        request.run_id,
                        assertion,
                    )
    return request


def _scope(company_id, product_id):
    return CompanyScope(
        company_id=company_id,
        segment_id="segment-ups",
        product_id=product_id,
        customer_or_platform_scope="global hyperscale data centers",
        geography="global ex-China",
        time_horizon_months=24,
        as_of_date=date(2026, 8, 16),
    )


def _red_team(thesis, company_id):
    return RedTeamReview(
        thesis=thesis,
        counter_evidence_queries=("search alternatives and failures",),
        unresolved_counterarguments=("customer may qualify a second source",),
        falsification_conditions=("named customer switches material volume",),
        evidence_ids=(f"ev-{company_id}-context",),
    )


def _gate(gate_id, label, *evidence_ids):
    return GateResult(
        gate_id=gate_id,
        label=label,
        required_predicate_ids=CANONICAL_GATE_PREDICATES[gate_id]["predicate_ids"],
        decisive_evidence_ids=tuple(evidence_ids),
    )


@pytest.mark.parametrize(
    ("quote", "source_type", "capability"),
    (
        (
            "challengerco ups-y production use is not confirmed.",
            "company_filing",
            "production_use_confirmed",
        ),
        (
            "challengerco ups-y production use means a deployment-stage definition.",
            "company_filing",
            "production_use_confirmed",
        ),
        (
            "otherco other-product production use confirmed.",
            "company_filing",
            "production_use_confirmed",
        ),
        (
            "powerco ups-x accounting revenue confirmed at $25 million.",
            "industry_blog",
            "accounting_revenue_confirmed",
        ),
    ),
)
def test_stage4_capability_authorization_requires_affirmative_subject_semantics(
    tmp_path, quote, source_type, capability
):
    """SELECT INVARIANT: tags/markers cannot bypass stance, subject, or official semantics."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    result = repository.get_stage3_result(request.run_id)
    card = next(
        item for item in result.evidence_cards if capability in item.claim_capabilities
    )
    claim = next(item for item in result.claims if item.claim_id == card.claim_id)

    assert capability not in _authorized_claim_capabilities(
        claim, replace(card, exact_quote=quote, source_type=source_type)
    )


@pytest.mark.parametrize(
    ("polarity", "lifecycle_state", "decision"),
    (
        ("negative", "current", "verified"),
        ("unknown", "current", "verified"),
        ("conflicted", "current", "verified"),
        ("affirmative", "pending_qualification", "verified"),
        ("affirmative", "planned", "verified"),
        ("affirmative", "historical_ended", "verified"),
        ("affirmative", "withdrawn", "verified"),
        ("affirmative", "current", "rejected"),
    ),
)
def test_stage4_structured_assertion_fails_closed_for_non_authoritative_states(
    tmp_path, polarity, lifecycle_state, decision
):
    """SELECT INVARIANT: tags and positive-looking text never override typed fact state."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    result = repository.get_stage3_result(request.run_id)
    capability = "production_use_confirmed"
    card = next(
        item for item in result.evidence_cards if capability in item.claim_capabilities
    )
    claim = next(item for item in result.claims if item.claim_id == card.claim_id)
    assertion = _verified_assertion(
        card,
        capability,
        company_id="challengerco",
        product_id="ups-y",
        polarity=polarity,
        lifecycle_state=lifecycle_state,
        decision=decision,
    )

    assert capability not in _authorized_claim_capabilities(
        claim, replace(card, business_fact_assertions=(assertion,))
    )


def test_earnings_persistence_rejects_mixed_verified_accounting_basis(tmp_path):
    """SELECT INVARIANT: four quarters must share a comparable accounting basis."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository, persist_fact_verifications=False)
    stage3 = repository.get_stage3_result(request.run_id)
    claims = {claim.claim_id: claim for claim in stage3.claims}
    cards = {card.evidence_id: card for card in stage3.evidence_cards}
    persistence_ids = tuple(
        f"ev-powerco-earnings-q{period_number}-earnings_persistence"
        for period_number in range(1, 5)
    )
    for index, evidence_id in enumerate(persistence_ids):
        card = cards[evidence_id]
        assertions = tuple(
            replace(assertion, accounting_basis="IFRS")
            if index == 0
            else assertion
            for assertion in card.business_fact_assertions
        )
        card = replace(card, business_fact_assertions=assertions)
        cards[evidence_id] = card
        for assertion in assertions:
            _persist_assertion_verification(repository, request.run_id, assertion)
    for dimension in ("revenue_materiality", "time_to_revenue"):
        card = cards[f"ev-powerco-{dimension}"]
        for assertion in card.business_fact_assertions:
            _persist_assertion_verification(repository, request.run_id, assertion)

    with pytest.raises(ValueError, match="comparable accounting basis"):
        _validate_earnings_evidence(
            _incumbent(),
            {
                evidence_id: claims[card.claim_id]
                for evidence_id, card in cards.items()
            },
            cards,
            repository=repository,
            run_id=request.run_id,
            governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
            decision_table=_business_fact_decision_table(),
        )


def _incumbent(
    *, product_id="ups-x", evidence_key="powerco", earnings_period_count=4
):
    earnings = []
    for name in EARNINGS:
        dimension = _exact(name, 3, company_id="powerco", evidence_key=evidence_key)
        if name == "earnings_persistence":
            dimension = _bind_dimension_evidence(
                dimension,
                tuple(
                    f"ev-{evidence_key}-earnings-q{period_number}-earnings_persistence"
                    for period_number in range(1, earnings_period_count + 1)
                ),
            )
        earnings.append(dimension)
    return CompanyAssessmentDraft(
        company_name="PowerCo",
        scope=_scope("powerco", product_id),
        roles=("bottleneck_owner", "capacity_expander"),
        exposure=CompanyExposure(
            narrative="management names AI UPS demand",
            operational="qualified installed capacity",
            revenue="recognized UPS segment revenue",
            earnings="segment margin evidence",
            evidence_ids=(f"ev-{evidence_key}-context",),
        ),
        defensibility=tuple(
            _exact(name, 4, company_id="powerco", evidence_key=evidence_key)
            for name in DEFENSIBILITY
        ),
        replacement=(),
        earnings=tuple(earnings),
        replacement_mode=None,
        ecosystem_compatibility=None,
        displacement=None,
        milestones=None,
        scope_valid=True,
        freshness_valid=True,
        evidence_mapping_error=False,
        gate_results=(
            _gate(
                "customer_dependency_current",
                "pass",
                f"ev-{evidence_key}-customer_sourcing_evidence",
            ),
            _gate(
                "capital_hard_fail",
                "fail",
                f"ev-{evidence_key}-capital_cash_burden",
            ),
        ),
        red_team_reviews=(
            _red_team("defensibility", evidence_key),
            _red_team("earnings_transmission", evidence_key),
        ),
    )


def _challenger():
    return CompanyAssessmentDraft(
        company_name="ChallengerCo",
        scope=_scope("challengerco", "ups-y"),
        roles=("substitute_supplier", "bottleneck_solver"),
        exposure=CompanyExposure(
            narrative="alternative UPS",
            operational="qualified production",
            revenue="recognized product revenue",
            earnings="margin not inferred from revenue",
            evidence_ids=("ev-challengerco-context",),
        ),
        defensibility=(),
        replacement=tuple(
            _exact(
                name,
                4,
                company_id="challengerco",
                replacement_mode="product",
            )
            for name in REPLACEMENT
        ),
        earnings=tuple(_unknown(name) for name in EARNINGS),
        replacement_mode="product",
        ecosystem_compatibility=_exact(
            "ecosystem_compatibility",
            4,
            company_id="challengerco",
            replacement_mode="product",
        ),
        displacement=_exact(
            "displacement",
            4,
            company_id="challengerco",
            replacement_mode="product",
        ),
        milestones=MilestoneAxes(
            product_readiness="sample_ready",
            lifecycle="active",
            qualification="production_approved",
            production="stable_scaled_output",
            adoption="multi_platform_use",
            financial="material_revenue",
            share_trajectory="sustained_gain",
            displacement="confirmed",
        ),
        scope_valid=True,
        freshness_valid=True,
        evidence_mapping_error=False,
        gate_results=(
            _gate("failed_or_withdrawn", "unknown"),
            _gate(
                "production_use",
                "pass",
                "ev-challengerco-customer_adoption",
            ),
            _gate(
                "qualified_saleable_output",
                "pass",
                "ev-challengerco-capacity_readiness",
            ),
            _gate(
                "scale_gate",
                "pass",
                "ev-challengerco-capacity_readiness",
                "ev-challengerco-customer_adoption",
                "ev-challengerco-execution_delivery",
            ),
            _gate(
                "fatal_blocker_clear",
                "pass",
                "ev-challengerco-cost_tco_advantage",
                "ev-challengerco-regulatory_tailwind",
                "ev-challengerco-architecture_tailwind",
            ),
        ),
        red_team_reviews=(
            _red_team("replacement_momentum", "challengerco"),
            _red_team("earnings_transmission", "challengerco"),
        ),
    )


class FixedResearcher:
    def __init__(self, drafts, challenger_sets):
        self.drafts = drafts
        self.challenger_sets = challenger_sets

    def research(self, run, graph, stage3_result):
        return list(self.drafts), list(self.challenger_sets)


def _service(repository, researcher):
    return CompanyExposureRedTeamService(
        repository,
        researcher,
        expected_contract_sha256=CONTROLLED_OVERLAY_SHA256,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        allow_legacy_research_result=True,
    )


def test_stage4_production_entry_rejects_legacy_tuple_research_result(tmp_path):
    """SELECT INVARIANT: production Stage 4 cannot bypass the company chain receipts."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    service = CompanyExposureRedTeamService(
        repository,
        FixedResearcher([_incumbent()], []),
        expected_contract_sha256=CONTROLLED_OVERLAY_SHA256,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    )

    with pytest.raises(ValueError, match="CompanyResearchResult"):
        service.run(request.run_id)


def _challenger_set(*, coverage=True):
    completed_at = datetime(2026, 8, 16, tzinfo=timezone.utc)
    receipt = CounterSearchReceipt(
        challenger_set_id="challengers-ups-v1",
        protocol_version="challenger-search-v1",
        query_log_ids=("query-log-ups-qualified", "query-log-ups-alternatives"),
        max_queries=10,
        max_time_seconds=300,
        max_cost_usd=2.0,
        stop_reason="protocol_complete" if coverage else "budget_exhausted",
        route_findings=(
            CounterSearchRouteFinding(
                route_id="qualified-ups-vendors",
                status="supported_candidate",
                query_log_ids=("query-log-ups-qualified",),
                evidence_ids=("ev-challengerco-context",),
                finding="ChallengerCo is in the frozen candidate set.",
            ),
            CounterSearchRouteFinding(
                route_id="otherco",
                status="explicitly_ineligible",
                query_log_ids=("query-log-ups-alternatives",),
                evidence_ids=("ev-powerco-context",),
                finding="OtherCo is explicitly not qualified for this Scope.",
            ),
        ),
        negative_findings=("OtherCo is explicitly not qualified for this Scope.",),
        completed_at=completed_at,
    )
    return ChallengerSet(
        challenger_set_id="challengers-ups-v1",
        segment_id="segment-ups",
        assessment_scope="global hyperscale data centers; 24 months; 2026-08-16",
        search_protocol_version="challenger-search-v1",
        included_company_ids=("challengerco",),
        excluded_candidates_with_reason=(("otherco", "not qualified"),),
        search_completed_at=completed_at,
        as_of_date=date(2026, 8, 16),
        coverage_gate=coverage,
        counter_search_receipt=receipt,
    )


def _v15_challenger_set(*, coverage=True):
    legacy = _challenger_set(coverage=coverage)
    return replace(
        legacy,
        assessment_scope=AssessmentScope(
            company_id=None,
            product_id="anchor-1",
            segment_id="segment-ups",
            customer_or_platform_scope="global hyperscale data centers",
            geography="global",
            time_horizon_months=24,
            as_of_date=date(2026, 8, 16),
        ),
        included_company_ids=(),
        included_assessment_ids=(_company_assessment_id(_challenger().scope),),
        counter_search_receipt=replace(
            legacy.counter_search_receipt,
            discovery_rounds_without_new_material_routes=(2 if coverage else 1),
        ),
    )


def _v15_service(repository, researcher):
    return CompanyExposureRedTeamService(
        repository,
        researcher,
        expected_contract_sha256=CONTROLLED_OVERLAY_SHA256,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        allow_legacy_research_result=True,
        enable_v15_company_chain=True,
    )


def test_stage4_v15_contract_uses_typed_scope_and_assessment_ids():
    """SELECT INVARIANT: v1.5 ChallengerSet identifies atomic Scope and assessments."""
    challenger_set = _v15_challenger_set()

    assert isinstance(challenger_set.assessment_scope, AssessmentScope)
    assert challenger_set.assessment_scope.company_id is None
    assert challenger_set.included_assessment_ids == (
        _company_assessment_id(_challenger().scope),
    )
    assert (
        challenger_set.counter_search_receipt.discovery_rounds_without_new_material_routes
        == 2
    )


def test_stage4_v15_coverage_requires_two_converged_discovery_rounds(tmp_path):
    """SELECT INVARIANT: one no-new-route round cannot freeze a v1.5 ChallengerSet."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    challenger_set = _v15_challenger_set()
    challenger_set = replace(
        challenger_set,
        counter_search_receipt=replace(
            challenger_set.counter_search_receipt,
            discovery_rounds_without_new_material_routes=1,
        ),
    )

    with pytest.raises(ValueError, match="two converged discovery rounds"):
        _v15_service(
            repository,
            FixedResearcher(
                [_incumbent(), _challenger()],
                [challenger_set],
            ),
        ).run(request.run_id)


def test_stage4_v15_persists_incomplete_when_challenger_set_is_missing(tmp_path):
    """SELECT INVARIANT: missing v1.5 ChallengerSet persists progress but withholds Competition."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)

    result = _v15_service(
        repository,
        FixedResearcher([_incumbent()], []),
    ).run(request.run_id)

    assert result.status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    assert result.incomplete_reasons == ("missing_challenger_set:segment-ups",)
    assert result.company_assessments[0].competition_primary_state is None
    assert repository.get_run(request.run_id).status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    assert repository.get_stage4_result(request.run_id) == result


def test_stage4_v15_complete_typed_challenger_set_reaches_ready(tmp_path):
    """SELECT INVARIANT: only a complete typed v1.5 ChallengerSet can release Competition."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)

    result = _v15_service(
        repository,
        FixedResearcher(
            [_incumbent(), _challenger()],
            [_v15_challenger_set()],
        ),
    ).run(request.run_id)

    assert result.status is RunStatus.COMPANY_ASSESSMENT_READY
    assert result.incomplete_reasons == ()
    incumbent = next(
        item for item in result.company_assessments if "bottleneck_owner" in item.roles
    )
    assert incumbent.competition_primary_state == "vulnerable_incumbent"


def test_stage4_v15_retry_replaces_persisted_partial_result(tmp_path):
    """SELECT INVARIANT: a resumed v1.5 search can atomically replace its partial result."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    first = _v15_service(
        repository,
        FixedResearcher([_incumbent()], []),
    ).run(request.run_id)

    second = _v15_service(
        repository,
        FixedResearcher(
            [_incumbent(), _challenger()],
            [_v15_challenger_set()],
        ),
    ).run(request.run_id)

    assert first.status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    assert second.status is RunStatus.COMPANY_ASSESSMENT_READY
    assert repository.get_stage4_result(request.run_id) == second


def test_stage4_v15_resume_passes_persisted_search_progress_to_researcher(tmp_path):
    """SELECT INVARIANT: retry receives prior receipts instead of restarting blind."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    _v15_service(
        repository,
        FixedResearcher([_incumbent()], []),
    ).run(request.run_id)

    class ResumableResearcher:
        previous = None

        def research(self, *_args, **_kwargs):
            raise AssertionError("resume must not restart the company search")

        def resume(self, run, graph, stage3_result, previous_result):
            self.previous = previous_result
            return (
                [_incumbent(), _challenger()],
                [_v15_challenger_set()],
            )

    researcher = ResumableResearcher()
    result = _v15_service(repository, researcher).run(request.run_id)

    assert researcher.previous.status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    assert researcher.previous.challenger_sets == ()
    assert result.status is RunStatus.COMPANY_ASSESSMENT_READY


def test_stage4_requires_complete_stage3_not_budget_incomplete(tmp_path):
    """SELECT INVARIANT: incomplete evidence cannot silently enter company ranking."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository, ready=False)

    with pytest.raises(ValueError, match="CHOKEPOINT_ASSESSMENT_READY"):
        _service(repository, FixedResearcher([], [])).run(request.run_id)


def test_stage4_contract_replaces_business_gate_booleans_with_evidence_results():
    """SELECT INVARIANT: business gates are evidence-bound records, never naked booleans."""
    fields = set(CompanyAssessmentDraft.__dataclass_fields__)
    assert "gate_results" in fields
    assert not {
        "customer_dependency_current",
        "failed_or_withdrawn",
        "production_use",
        "qualified_saleable_output",
        "scale_gate_pass",
        "fatal_blocker",
        "capital_hard_fail",
    } & fields


def test_stage4_contract_persists_counter_search_receipt_and_route_findings():
    """SELECT INVARIANT: challenger coverage is backed by a reproducible receipt."""
    assert {
        "challenger_set_id",
        "protocol_version",
        "query_log_ids",
        "max_queries",
        "max_time_seconds",
        "max_cost_usd",
        "stop_reason",
        "route_findings",
        "negative_findings",
        "completed_at",
    } <= set(CounterSearchReceipt.__dataclass_fields__)


def test_stage4_production_counter_search_requires_route_transport_receipts():
    """SELECT INVARIANT: company challenger coverage cannot rest on query IDs alone."""
    challenger_set = _challenger_set()

    with pytest.raises(ValueError, match="provider request receipts"):
        _validate_counter_search_receipt(
            challenger_set,
            {"ev-challengerco-context", "ev-powerco-context"},
            require_execution_lineage=True,
        )
    assert {
        "route_id",
        "status",
        "query_log_ids",
        "evidence_ids",
        "finding",
    } <= set(CounterSearchRouteFinding.__dataclass_fields__)
    assert "counter_search_receipt" in ChallengerSet.__dataclass_fields__


def test_stage4_dimension_contract_carries_typed_replacement_anchor_profile():
    """SELECT INVARIANT: replacement profile is durable data, not transient prompt prose."""
    assert "anchor_profile" in DimensionRatingDraft.__dataclass_fields__


def test_stage4_evidence_contract_carries_accounting_period_lineage():
    """SELECT INVARIANT: quarter persistence is source lineage, not narrative text."""
    assert {
        "accounting_period_type",
        "accounting_period_id",
    } <= set(EvidenceCard.__dataclass_fields__)


def test_stage4_rejects_missing_replacement_anchor_profile(tmp_path):
    """SELECT INVARIANT: every replacement ordinal binds the selected mode profile."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)

    challenger = _challenger()
    challenger = replace(
        challenger,
        replacement=(
            replace(challenger.replacement[0], anchor_profile=None),
            *challenger.replacement[1:],
        ),
    )

    with pytest.raises(ValueError, match="replacement anchor_profile"):
        _service(
            repository,
            FixedResearcher([_incumbent(), challenger], [_challenger_set()]),
        ).run(request.run_id)


def test_stage4_rejects_replacement_profile_for_another_mode(tmp_path):
    """SELECT INVARIANT: product and service anchor profiles cannot be mixed."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    challenger = _challenger()
    challenger = replace(
        challenger,
        replacement=(
            replace(
                challenger.replacement[0],
                anchor_profile="replacement.service.performance_parity.v1.4",
            ),
            *challenger.replacement[1:],
        ),
    )

    with pytest.raises(ValueError, match="replacement anchor_profile"):
        _service(
            repository,
            FixedResearcher([_incumbent(), challenger], [_challenger_set()]),
        ).run(request.run_id)


@pytest.mark.parametrize(
    ("positive_dimension", "evidence_state", "expected_state"),
    [
        ("performance_parity", "supported", "early_signal"),
        ("qualification_progress", "supported", "early_signal"),
        ("customer_adoption", "supported", "early_signal"),
        ("capacity_readiness", "supported", None),
        ("qualification_progress", "conflicted", None),
        ("customer_adoption", "conflicted", None),
    ],
)
def test_stage4_replacement_discovery_matches_canonical_supported_truth_table(
    tmp_path, positive_dimension, evidence_state, expected_state
):
    """SELECT INVARIANT: runtime discovery equals the canonical v1.4 predicate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    challenger = _challenger()
    dimensions = []
    for name in REPLACEMENT:
        if name != positive_dimension:
            dimensions.append(_replacement_unknown(name))
            continue
        dimension = _exact(
            name,
            1,
            company_id="challengerco",
            replacement_mode="product",
        )
        if evidence_state == "conflicted":
            dimension = replace(
                dimension,
                evidence_state="conflicted",
                bound_type="lower_bound",
                rating_max=4,
                bound_basis=BoundBasis(
                    floor_anchor=1,
                    unresolved_higher_anchors=(2, 3, 4),
                ),
                anchor_conditions=(),
            )
        dimensions.append(dimension)
    challenger = replace(
        challenger,
        replacement=tuple(dimensions),
        ecosystem_compatibility=_replacement_unknown("ecosystem_compatibility"),
        displacement=_replacement_unknown("displacement"),
        gate_results=tuple(
            _gate(gate.gate_id, "unknown") for gate in challenger.gate_results
        ),
    )

    result = _service(
        repository,
        FixedResearcher([challenger], [_challenger_set()]),
    ).run(request.run_id)

    assert result.company_assessments[0].replacement.primary_state == expected_state
    if expected_state is None:
        assert (
            result.company_assessments[0].replacement.withheld_reason
            == "replacement_state_not_eligible"
        )


def test_stage4_budget_counts_unique_companies_and_allows_multiple_atomic_scopes():
    """SELECT INVARIANT: one company may have multiple cards without cross-Scope merging."""
    first = _incumbent()
    second = replace(first, scope=replace(first.scope, product_id="ups-x-next-generation"))

    indexed = _index_company_drafts((first, second), max_unique_companies=10)

    assert len(indexed) == 2
    assert {draft.scope.company_id for draft in indexed.values()} == {"powerco"}
    too_many = tuple(
        replace(first, scope=replace(first.scope, company_id=f"company-{index}"))
        for index in range(11)
    )
    with pytest.raises(ValueError, match="at most 10 unique companies"):
        _index_company_drafts(too_many, max_unique_companies=10)


def test_stage4_persists_multiple_scopes_for_one_company_end_to_end(tmp_path):
    """SELECT INVARIANT: two atomic Scope cards survive service and SQLite round trip."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        additional_powerco_scopes=(("powerco-next", "ups-x-next-generation"),),
    )
    first = _incumbent()
    second = _incumbent(
        product_id="ups-x-next-generation", evidence_key="powerco-next"
    )

    result = _service(
        repository,
        FixedResearcher([first, second, _challenger()], [_challenger_set()]),
    ).run(request.run_id)

    powerco_cards = tuple(
        item for item in result.company_assessments
        if item.scope.company_id == "powerco"
    )
    assert len(powerco_cards) == 2
    assert {item.scope.product_id for item in powerco_cards} == {
        "ups-x",
        "ups-x-next-generation",
    }
    assert len({item.assessment_id for item in powerco_cards}) == 2
    assert repository.get_stage4_result(request.run_id) == result


def test_stage4_separates_axes_and_actual_displacement_overrides_incumbent_history(tmp_path):
    """SELECT INVARIANT: realized replacement makes even a high-defense owner vulnerable."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)

    result = _service(
        repository,
        FixedResearcher([_incumbent(), _challenger()], [_challenger_set()]),
    ).run(request.run_id)

    assert result.status is RunStatus.COMPANY_ASSESSMENT_READY
    incumbent, challenger = result.company_assessments
    assert incumbent.defensibility.primary_state == "high_defensibility"
    assert incumbent.earnings.primary_state == "material_earnings_path"
    assert incumbent.competition_primary_state == "vulnerable_incumbent"
    assert challenger.replacement.primary_state == "scaled_replacement"
    assert challenger.challenger_label == "realized_replacement"
    assert challenger.earnings.primary_state is None
    assert challenger.earnings.withheld_reason == "mandatory_earnings_fields_unresolved"
    assert not hasattr(incumbent, "recommendation")
    assert repository.get_stage4_result(request.run_id) == result


def test_stage4_does_not_call_high_incumbent_durable_without_frozen_set_coverage(tmp_path):
    """SELECT INVARIANT: open-world absence of challengers cannot prove durability."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    empty_set = ChallengerSet(
        **{
            **_challenger_set(coverage=False).__dict__,
            "included_company_ids": (),
        }
    )

    result = _service(
        repository, FixedResearcher([_incumbent()], [empty_set])
    ).run(request.run_id)

    incumbent = result.company_assessments[0]
    assert incumbent.competition_primary_state is None
    assert incumbent.competition_withheld_reason == "challenger_set_coverage_gate_not_passed"


def test_stage4_rejects_coverage_pass_without_complete_counter_search_receipt(tmp_path):
    """SELECT INVARIANT: a coverage boolean cannot substitute for search provenance."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    invalid_set = ChallengerSet(
        **{
            **_challenger_set().__dict__,
            "counter_search_receipt": None,
        }
    )

    with pytest.raises(ValueError, match="counter-search receipt"):
        _service(
            repository,
            FixedResearcher([_incumbent(), _challenger()], [invalid_set]),
        ).run(request.run_id)


def test_stage4_incomplete_counter_search_receipt_cannot_unlock_replacement_ready(tmp_path):
    """SELECT INVARIANT: query prose cannot substitute for a completed receipt."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)

    result = _service(
        repository,
        FixedResearcher([_incumbent(), _challenger()], [_challenger_set(coverage=False)]),
    ).run(request.run_id)

    challenger = next(
        item for item in result.company_assessments
        if item.scope.company_id == "challengerco"
    )
    assert challenger.replacement.primary_state == "credible_challenge"
    assert "fatal_blocker_gate" not in challenger.replacement.achieved_gates


def test_stage4_rejects_supported_candidate_without_thesis_specific_red_team(tmp_path):
    """SELECT INVARIANT: every supported axis carries counter-search and falsifiers."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    incumbent = _incumbent()
    incumbent = CompanyAssessmentDraft(
        **{**incumbent.__dict__, "red_team_reviews": ()}
    )

    with pytest.raises(ValueError, match="red-team review"):
        _service(
            repository, FixedResearcher([incumbent], [_challenger_set()])
        ).run(request.run_id)

    assert repository.get_run(request.run_id).status is RunStatus.CHOKEPOINT_ASSESSMENT_READY


def test_stage4_rejects_context_only_evidence_as_decisive_high_score_support(tmp_path):
    """SELECT INVARIANT: context_only evidence cannot satisfy a 3/4 anchor or Hard Gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository, scoring_use="context_only")

    with pytest.raises(ValueError, match="context_only evidence cannot support scoring"):
        _service(
            repository,
            FixedResearcher([_incumbent(), _challenger()], [_challenger_set()]),
        ).run(request.run_id)


@pytest.mark.parametrize("evidence_mode", ["context_only", "ineligible", "scopeless"])
def test_stage4_rejects_supported_exact_zero_without_authorized_evidence(
    tmp_path, evidence_mode
):
    """SELECT INVARIANT: exact zero needs eligible same-scope scoring evidence too."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        scoring_use="context_only" if evidence_mode == "context_only" else "primary",
        exact_zero_evidence_mode=evidence_mode,
    )
    incumbent = _incumbent()
    incumbent = replace(
        incumbent,
        defensibility=(
            _exact(DEFENSIBILITY[0], 0, company_id="powerco"),
            *(_unknown(name) for name in DEFENSIBILITY[1:]),
        ),
        earnings=tuple(_unknown(name) for name in EARNINGS),
        gate_results=tuple(
            _gate(gate.gate_id, "unknown") for gate in incumbent.gate_results
        ),
        red_team_reviews=(_red_team("defensibility", "powerco"),),
    )
    empty_set = replace(
        _challenger_set(coverage=False),
        included_company_ids=(),
    )

    with pytest.raises(
        ValueError,
        match="scoring evidence|assessment Scope|context_only|cannot support scoring",
    ):
        _service(
            repository,
            FixedResearcher([incumbent], [empty_set]),
        ).run(request.run_id)


def test_stage4_rejects_decisive_evidence_from_another_company_scope(tmp_path):
    """SELECT INVARIANT: company scoring evidence must match the complete assessment Scope."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    incumbent = _incumbent()
    first = incumbent.defensibility[0]
    wrong_scope = DimensionRatingDraft(
        **{
            **first.__dict__,
            "evidence_ids": ("ev-challengerco-performance_parity",),
            "decisive_evidence_ids": ("ev-challengerco-performance_parity",),
        }
    )
    incumbent = CompanyAssessmentDraft(
        **{
            **incumbent.__dict__,
            "defensibility": (wrong_scope, *incumbent.defensibility[1:]),
        }
    )

    with pytest.raises(ValueError, match="assessment Scope"):
        _service(
            repository,
            FixedResearcher([incumbent, _challenger()], [_challenger_set()]),
        ).run(request.run_id)


def test_stage4_rejects_self_reported_pass_backed_only_by_semantically_unrelated_evidence(tmp_path):
    """SELECT INVARIANT: same-Scope Evidence cannot self-authorize an unrelated Hard Gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    challenger = _challenger()
    gates = tuple(
        GateResult(
            gate_id=gate.gate_id,
            label=gate.label,
            required_predicate_ids=("replacement.production_use_confirmed.v1.4",),
            decisive_evidence_ids=("ev-challengerco-cost_tco_advantage",),
        )
        if gate.gate_id == "production_use"
        else gate
        for gate in challenger.gate_results
    )
    challenger = replace(challenger, gate_results=gates)

    with pytest.raises(ValueError, match="does not satisfy canonical gate predicates"):
        _service(
            repository,
            FixedResearcher([_incumbent(), challenger], [_challenger_set()]),
        ).run(request.run_id)


def test_stage4_rejects_false_capability_tag_on_unrelated_source_span(tmp_path):
    """SELECT INVARIANT: provider capability tags cannot authorize a Hard Gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        extra_capabilities=(("cost_tco_advantage", "production_use_confirmed"),),
    )
    challenger = _challenger()
    gates = tuple(
        replace(
            gate,
            decisive_evidence_ids=("ev-challengerco-cost_tco_advantage",),
        )
        if gate.gate_id == "production_use"
        else gate
        for gate in challenger.gate_results
    )

    with pytest.raises(ValueError, match="canonical gate predicates|capability"):
        _service(
            repository,
            FixedResearcher(
                [_incumbent(), replace(challenger, gate_results=gates)],
                [_challenger_set()],
            ),
        ).run(request.run_id)


@pytest.mark.parametrize("score", [0, 4])
def test_stage4_rejects_supported_company_score_backed_by_contradiction(
    tmp_path, score
):
    """SELECT INVARIANT: company supported bounds require supporting Evidence Cards."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        evidence_stance_overrides=((DEFENSIBILITY[0], "contradicts"),),
    )
    incumbent = _incumbent()
    incumbent = replace(
        incumbent,
        defensibility=(
            _exact(DEFENSIBILITY[0], score, company_id="powerco"),
            *incumbent.defensibility[1:],
        ),
    )

    with pytest.raises(ValueError, match="supporting Evidence"):
        _service(
            repository,
            FixedResearcher([incumbent, _challenger()], [_challenger_set()]),
        ).run(request.run_id)


@pytest.mark.parametrize(
    ("mode", "period_count", "expected_error"),
    [
        ("generic", 1, "accounting-revenue capability"),
        ("single_period", 1, "four distinct fiscal quarters"),
        ("three_quarters", 3, "four distinct fiscal quarters"),
        ("non_quarter", 4, "quarter lineage"),
    ],
)
def test_stage4_earnings_requires_accounting_and_quarter_period_lineage(
    tmp_path, mode, period_count, expected_error
):
    """SELECT INVARIANT: narrative or non-quarter revenue cannot prove persistence."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository, earnings_evidence_mode=mode)
    incumbent = _incumbent(earnings_period_count=period_count)
    empty_set = replace(_challenger_set(coverage=False), included_company_ids=())

    with pytest.raises(ValueError, match=expected_error):
        _service(
            repository,
            FixedResearcher([incumbent], [empty_set]),
        ).run(request.run_id)


def test_stage4_false_accounting_capability_tag_does_not_unlock_earnings(tmp_path):
    """SELECT INVARIANT: accounting capability is derived from the source span."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        earnings_evidence_mode="generic",
        extra_capabilities=(
            ("revenue_materiality", "accounting_revenue_confirmed"),
            ("time_to_revenue", "accounting_revenue_confirmed"),
            ("earnings_persistence", "multi_period_revenue_confirmed"),
        ),
    )
    empty_set = replace(_challenger_set(coverage=False), included_company_ids=())

    with pytest.raises(ValueError, match="accounting-revenue capability"):
        _service(
            repository,
            FixedResearcher(
                [_incumbent(earnings_period_count=1)],
                [empty_set],
            ),
        ).run(request.run_id)


def test_stage4_rejects_one_atomic_fact_as_two_primary_high_dimensions(tmp_path):
    """SELECT INVARIANT: one fact_key has only one primary 3/4-point ownership."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(
        repository,
        shared_fact_key_dimensions=(
            "technical_performance_gap",
            "qualification_lock_in",
        ),
    )

    with pytest.raises(ValueError, match="one primary high-score dimension"):
        _service(
            repository,
            FixedResearcher([_incumbent(), _challenger()], [_challenger_set()]),
        ).run(request.run_id)


def test_stage4_rejects_frozen_challenger_set_with_missing_assessment(tmp_path):
    """SELECT INVARIANT: an included challenger cannot disappear before Competition state."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    incomplete_set = ChallengerSet(
        **{
            **_challenger_set().__dict__,
            "included_company_ids": ("challengerco", "missingco"),
        }
    )

    with pytest.raises(ValueError, match="included challenger assessment is missing"):
        _service(
            repository,
            FixedResearcher([_incumbent(), _challenger()], [incomplete_set]),
        ).run(request.run_id)
