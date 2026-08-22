"""Stage 4: scoped company exposure, three-axis scoring and red-team review."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    ChallengerSet,
    CompanyAssessment,
    CompanyAssessmentDraft,
    CompanyResearchResult,
    RunStatus,
    ScoreFamilyAssessment,
    Stage4Result,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.recompute_v15 import (
    build_segment_recompute_requests,
)
from event_collector.theme_chokepoint.stage3 import (
    CONTRACT_VERSION,
    FrozenScoringContract,
    _validate_dimension,
)


ALLOWED_ROLES = {
    "bottleneck_owner",
    "capacity_expander",
    "bottleneck_solver",
    "downstream_buyer",
    "substitute_supplier",
    "equipment_enabler",
}
POSITIVE_REPLACEMENT_STATES = {
    "credible_challenge",
    "replacement_ready",
    "production_alternative",
    "scaled_alternative",
    "realized_replacement",
    "scaled_replacement",
}
READY_REPLACEMENT_STATES = {
    "replacement_ready",
    "production_alternative",
    "scaled_alternative",
    "realized_replacement",
    "scaled_replacement",
}
REALIZED_REPLACEMENT_STATES = {"realized_replacement", "scaled_replacement"}
CANONICAL_GATE_PREDICATES = {
    "customer_dependency_current": {
        "predicate_ids": ("competition.customer_dependency_current.v1.4",),
        "pass_all": ("customer_dependency_current_confirmed",),
        "fail_all": ("customer_dependency_absent_confirmed",),
    },
    "capital_hard_fail": {
        "predicate_ids": ("earnings.capital_hard_fail.v1.4",),
        "pass_all": ("capital_hard_fail_confirmed",),
        "fail_all": ("capital_hard_fail_clear_confirmed",),
    },
    "failed_or_withdrawn": {
        "predicate_ids": ("replacement.failed_or_withdrawn.v1.4",),
        "pass_all": ("failed_or_withdrawn_confirmed",),
        "fail_all": ("active_lifecycle_confirmed",),
    },
    "production_use": {
        "predicate_ids": ("replacement.production_use_confirmed.v1.4",),
        "pass_all": ("production_use_confirmed",),
        "fail_all": ("production_use_absent_confirmed",),
    },
    "qualified_saleable_output": {
        "predicate_ids": ("replacement.qualified_saleable_output_confirmed.v1.4",),
        "pass_all": ("qualified_saleable_output_confirmed",),
        "fail_all": ("qualified_saleable_output_absent_confirmed",),
    },
    "scale_gate": {
        "predicate_ids": (
            "replacement.scaled_capacity_confirmed.v1.4",
            "replacement.scaled_adoption_confirmed.v1.4",
            "replacement.execution_delivery_confirmed.v1.4",
        ),
        "pass_all": (
            "scaled_capacity_confirmed",
            "scaled_adoption_confirmed",
            "execution_delivery_confirmed",
        ),
        "fail_all": (),
    },
    "fatal_blocker_clear": {
        "predicate_ids": (
            "replacement.tco_fatal_blocker_clear.v1.4",
            "replacement.regulatory_fatal_blocker_clear.v1.4",
            "replacement.architecture_fatal_blocker_clear.v1.4",
        ),
        "pass_all": (
            "tco_fatal_blocker_clear_confirmed",
            "regulatory_fatal_blocker_clear_confirmed",
            "architecture_fatal_blocker_clear_confirmed",
        ),
        "fail_all": (),
    },
}


class CompanyResearcher(Protocol):
    def research(self, run, graph, stage3_result):
        """Return company drafts and frozen challenger sets."""


class CompanyExposureRedTeamService:
    def __init__(
        self,
        repository: ThemeChokepointRepository,
        researcher: CompanyResearcher,
        *,
        contract_path: str | Path | None = None,
        expected_contract_sha256: str | None = None,
        allow_unfrozen_overlay: bool = False,
        governance_bundle_path: str | Path | None = None,
        expected_governance_bundle_sha256: str | None = None,
        allow_legacy_research_result: bool = False,
        enable_v15_company_chain: bool = False,
    ):
        self.repository = repository
        self.researcher = researcher
        self.contract = FrozenScoringContract(
            contract_path,
            expected_sha256=expected_contract_sha256,
            allow_unfrozen_overlay=allow_unfrozen_overlay,
            governance_bundle_path=governance_bundle_path,
            expected_governance_bundle_sha256=expected_governance_bundle_sha256,
        )
        self.allow_legacy_research_result = allow_legacy_research_result
        self.enable_v15_company_chain = enable_v15_company_chain

    def run(self, run_id: str) -> Stage4Result:
        run = self.repository.get_run(run_id)
        allowed_statuses = {RunStatus.CHOKEPOINT_ASSESSMENT_READY}
        if self.enable_v15_company_chain:
            allowed_statuses.add(RunStatus.COMPANY_ASSESSMENT_INCOMPLETE)
        if run.status not in allowed_statuses:
            raise ValueError(
                "Stage 4 requires CHOKEPOINT_ASSESSMENT_READY; "
                f"actual={run.status.value}"
            )
        graph = self.repository.get_supply_chain_graph(run_id)
        stage3_result = self.repository.get_stage3_result(run_id)
        if (
            stage3_result.executable_contract_id != self.contract.executable_contract_id
            or stage3_result.executable_contract_sha256
            != self.contract.executable_contract_sha256
        ):
            raise ValueError("Stage 4 executable scoring contract lineage mismatch")
        resume_method = getattr(self.researcher, "resume", None)
        if (
            self.enable_v15_company_chain
            and run.status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
            and callable(resume_method)
        ):
            research_result = resume_method(
                run,
                graph,
                stage3_result,
                self.repository.get_stage4_result(run_id),
            )
        else:
            research_result = self.researcher.research(run, graph, stage3_result)
        production_chain = isinstance(research_result, CompanyResearchResult)
        if production_chain:
            _validate_company_research_result(research_result, run)
            _reload_company_provider_reconciliations(
                self.repository, run_id, research_result
            )
            drafts = research_result.drafts
            challenger_sets = research_result.challenger_sets
            company_claims = research_result.claims
            company_evidence_cards = research_result.evidence_cards
            company_source_snapshots = research_result.source_snapshots
            company_chain_receipts = research_result.chain_receipts
            provider_request_receipt_ids = (
                research_result.provider_request_receipt_ids
            )
            company_cost_usd_spent = research_result.cost_usd_spent
        elif self.allow_legacy_research_result:
            drafts, challenger_sets = research_result
            company_claims = ()
            company_evidence_cards = ()
            company_source_snapshots = ()
            company_chain_receipts = ()
            provider_request_receipt_ids = ()
            company_cost_usd_spent = 0.0
        else:
            raise ValueError(
                "Stage 4 production researcher must return CompanyResearchResult"
            )
        drafts = tuple(drafts)
        challenger_sets = tuple(challenger_sets)
        if not drafts:
            raise ValueError("Stage 4 requires at least one company assessment")
        draft_by_assessment = _index_company_drafts(
            drafts, max_unique_companies=10
        )
        all_claims = (*stage3_result.claims, *company_claims)
        all_evidence_cards = (
            *stage3_result.evidence_cards,
            *company_evidence_cards,
        )
        evidence_ids = {card.evidence_id for card in all_evidence_cards}
        claims_by_id = {claim.claim_id: claim for claim in all_claims}
        evidence_claims = {
            card.evidence_id: claims_by_id[card.claim_id]
            for card in all_evidence_cards
            if card.claim_id in claims_by_id
        }
        evidence_cards_by_id = {
            card.evidence_id: card for card in all_evidence_cards
        }
        segment_ids = {item.segment_id for item in stage3_result.assessments}
        set_by_segment = _validate_challenger_sets(
            challenger_sets,
            segment_ids,
            evidence_ids,
            require_execution_lineage=production_chain,
            require_v15_scope=self.enable_v15_company_chain,
        )

        assessments: list[CompanyAssessment] = []
        for draft in drafts:
            draft = _derive_company_gate_results(
                draft,
                evidence_claims,
                evidence_cards_by_id,
                repository=self.repository,
                run_id=run_id,
                governance_bundle_sha256=self.contract.governance_bundle_sha256,
                decision_table=self.contract.business_fact_decision_table,
            )
            draft_by_assessment[_company_assessment_id(draft.scope)] = draft
            _validate_company_draft(
                draft,
                evidence_ids,
                evidence_claims,
                evidence_cards_by_id,
                segment_ids,
            )
            defensibility = (
                _score_defensibility(draft, self.contract, evidence_ids)
                if draft.defensibility
                else None
            )
            replacement_score = (
                _score_replacement(
                    draft,
                    self.contract,
                    evidence_ids,
                    set_by_segment.get(draft.scope.segment_id),
                )
                if draft.replacement
                else None
            )
            earnings = (
                _score_earnings(
                    draft,
                    self.contract,
                    evidence_ids,
                    evidence_claims,
                    evidence_cards_by_id,
                    repository=self.repository,
                    run_id=run_id,
                )
                if draft.earnings
                else None
            )
            assessments.append(
                CompanyAssessment(
                    assessment_id=_company_assessment_id(draft.scope),
                    company_name=draft.company_name.strip(),
                    scope=draft.scope,
                    roles=tuple(draft.roles),
                    exposure=draft.exposure,
                    defensibility=defensibility,
                    replacement=replacement_score,
                    earnings=earnings,
                    replacement_mode=draft.replacement_mode,
                    milestones=draft.milestones,
                    competition_primary_state=None,
                    competition_achieved_gates=(),
                    competition_withheld_reason=None,
                    challenger_label=_challenger_label(replacement_score),
                    gate_results=tuple(draft.gate_results),
                    red_team_reviews=tuple(draft.red_team_reviews),
                )
            )

        by_company = {}
        for item in assessments:
            by_company.setdefault(item.scope.company_id, []).append(item)
        assessment_by_id = {item.assessment_id: item for item in assessments}
        for challenger_set in challenger_sets:
            if self.enable_v15_company_chain:
                missing_assessments = {
                    assessment_id
                    for assessment_id in challenger_set.included_assessment_ids
                    if assessment_id not in assessment_by_id
                    or assessment_by_id[assessment_id].scope.segment_id
                    != challenger_set.segment_id
                }
                if missing_assessments:
                    raise ValueError(
                        "included challenger assessment is missing: "
                        + sorted(missing_assessments)[0]
                    )
                continue
            missing = {
                company_id
                for company_id in challenger_set.included_company_ids
                if not any(
                    item.scope.segment_id == challenger_set.segment_id
                    for item in by_company.get(company_id, ())
                )
            }
            if missing:
                raise ValueError(
                    "included challenger assessment is missing: " + sorted(missing)[0]
                )
        incomplete_reasons = (
            _v15_company_incomplete_reasons(drafts, set_by_segment)
            if self.enable_v15_company_chain
            else ()
        )
        segment_recompute_requests = (
            build_segment_recompute_requests(
                run_id=run_id,
                stage3_result=stage3_result,
                company_claims=company_claims,
            )
            if self.enable_v15_company_chain
            else ()
        )
        finalized = []
        for assessment in assessments:
            draft = draft_by_assessment[assessment.assessment_id]
            if "bottleneck_owner" not in assessment.roles:
                finalized.append(assessment)
                continue
            if incomplete_reasons:
                finalized.append(
                    replace(
                        assessment,
                        competition_primary_state=None,
                        competition_achieved_gates=(),
                        competition_withheld_reason="company_assessment_incomplete",
                    )
                )
                continue
            challenger_set = set_by_segment.get(assessment.scope.segment_id)
            state, gates, withheld = _competition_state(
                assessment,
                draft,
                challenger_set,
                by_company,
                assessment_by_id=assessment_by_id,
            )
            finalized.append(
                replace(
                    assessment,
                    competition_primary_state=state,
                    competition_achieved_gates=gates,
                    competition_withheld_reason=withheld,
                )
            )
        return self.repository.save_stage4_result(
            run_id,
            contract_version=self.contract.version,
            executable_contract_id=self.contract.executable_contract_id,
            executable_contract_sha256=self.contract.executable_contract_sha256,
            challenger_sets=challenger_sets,
            company_assessments=tuple(finalized),
            company_claims=tuple(company_claims),
            company_evidence_cards=tuple(company_evidence_cards),
            company_source_snapshots=tuple(company_source_snapshots),
            company_chain_receipts=tuple(company_chain_receipts),
            provider_request_receipt_ids=tuple(provider_request_receipt_ids),
            cost_usd_spent=company_cost_usd_spent,
            status=(
                RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
                if incomplete_reasons
                else RunStatus.COMPANY_ASSESSMENT_READY
            ),
            incomplete_reasons=incomplete_reasons,
            segment_recompute_requests=segment_recompute_requests,
        )


def _reload_company_provider_reconciliations(repository, run_id, result):
    stored = tuple(repository.list_company_provider_reconciliations(run_id))
    expected_ids = tuple(result.provider_request_receipt_ids)
    actual_ids = tuple(item.receipt_id for item in stored)
    required_roles = {"discovery", "evidence", "scoring", "critic"}
    if (
        not expected_ids
        or len(set(expected_ids)) != len(expected_ids)
        or set(actual_ids) != set(expected_ids)
        or len(actual_ids) != len(expected_ids)
        or {item.role for item in stored} != required_roles
        or any(item.run_id != run_id for item in stored)
    ):
        raise ValueError("company four-role repository reconciliation is incomplete")
    return stored


def _validate_company_research_result(result, run):
    if result.cost_usd_spent < 0 or result.cost_usd_spent > run.request.max_cost_usd:
        raise ValueError("company research result exceeds the run cost budget")
    if (
        not result.provider_request_receipt_ids
        or len(set(result.provider_request_receipt_ids))
        != len(result.provider_request_receipt_ids)
        or any(not item.strip() for item in result.provider_request_receipt_ids)
    ):
        raise ValueError("company research result requires unique provider receipts")
    claims_by_id = {claim.claim_id: claim for claim in result.claims}
    cards_by_id = {card.evidence_id: card for card in result.evidence_cards}
    snapshots_by_id = {
        snapshot.article_id: snapshot for snapshot in result.source_snapshots
    }
    if (
        not claims_by_id
        or len(claims_by_id) != len(result.claims)
        or not cards_by_id
        or len(cards_by_id) != len(result.evidence_cards)
        or len(snapshots_by_id) != len(result.source_snapshots)
    ):
        raise ValueError("company research evidence ledger is empty or duplicated")
    for card in result.evidence_cards:
        claim = claims_by_id.get(card.claim_id)
        snapshot = snapshots_by_id.get(card.article_id)
        if claim is None or card.evidence_id not in claim.evidence_ids:
            raise ValueError("company Evidence Card is not bound to its persisted Claim")
        if (
            snapshot is None
            or snapshot.canonical_url != card.canonical_url
            or snapshot.content_hash != card.content_hash
            or snapshot.original_text[card.quote_start : card.quote_end]
            != card.exact_quote
        ):
            raise ValueError("company Evidence Card is not bound to its Source Snapshot")
    receipt_by_company = {
        receipt.company_id: receipt for receipt in result.chain_receipts
    }
    draft_company_ids = {draft.scope.company_id for draft in result.drafts}
    if (
        len(receipt_by_company) != len(result.chain_receipts)
        or set(receipt_by_company) != draft_company_ids
    ):
        raise ValueError("company chain receipts must cover every company draft exactly once")
    provider_ids = set(result.provider_request_receipt_ids)
    for receipt in result.chain_receipts:
        receipt_provider_ids = {
            *receipt.mapper_request_receipt_ids,
            *receipt.acquisition_request_receipt_ids,
            receipt.scorer_request_receipt_id,
            receipt.critic_request_receipt_id,
        }
        if not receipt_provider_ids <= provider_ids:
            raise ValueError("company chain receipt references an unknown provider receipt")
        if not receipt.acquired_evidence_ids or not set(
            receipt.acquired_evidence_ids
        ) <= set(cards_by_id):
            raise ValueError("company chain receipt has invalid acquired evidence lineage")


def _validate_challenger_sets(
    challenger_sets,
    segment_ids,
    evidence_ids,
    *,
    require_execution_lineage=False,
    require_v15_scope=False,
):
    result = {}
    for item in challenger_sets:
        if item.segment_id not in segment_ids:
            raise ValueError("challenger_set references an unassessed segment")
        if item.segment_id in result:
            raise ValueError("only one frozen challenger_set is allowed per segment")
        required = (item.challenger_set_id, item.search_protocol_version)
        if not all(value.strip() for value in required):
            raise ValueError("challenger_set identity and search scope are required")
        if require_v15_scope:
            scope = item.assessment_scope
            if (
                not isinstance(scope, AssessmentScope)
                or scope.company_id is not None
                or scope.segment_id != item.segment_id
                or scope.as_of_date != item.as_of_date
                or not all(
                    value.strip()
                    for value in (
                        scope.product_id,
                        scope.customer_or_platform_scope,
                        scope.geography,
                    )
                )
                or scope.time_horizon_months <= 0
            ):
                raise ValueError(
                    "v1.5 challenger_set requires a complete company-neutral AssessmentScope"
                )
            if item.included_company_ids:
                raise ValueError("v1.5 challenger_set must use included_assessment_ids")
        elif not isinstance(item.assessment_scope, str) or not item.assessment_scope.strip():
            raise ValueError("challenger_set identity and search scope are required")
        _validate_counter_search_receipt(
            item,
            evidence_ids,
            require_execution_lineage=require_execution_lineage,
        )
        if (
            require_v15_scope
            and item.coverage_gate
            and (
                item.counter_search_receipt is None
                or item.counter_search_receipt.discovery_rounds_without_new_material_routes
                < 2
            )
        ):
            raise ValueError(
                "v1.5 challenger coverage requires two converged discovery rounds"
            )
        result[item.segment_id] = item
    return result


def _v15_company_incomplete_reasons(drafts, set_by_segment):
    reasons = []
    owner_segments = sorted(
        {draft.scope.segment_id for draft in drafts if "bottleneck_owner" in draft.roles}
    )
    for segment_id in owner_segments:
        challenger_set = set_by_segment.get(segment_id)
        if challenger_set is None:
            reasons.append(f"missing_challenger_set:{segment_id}")
        elif not challenger_set.coverage_gate:
            reasons.append(f"challenger_set_incomplete:{segment_id}")
    return tuple(reasons)


def _index_company_drafts(drafts, *, max_unique_companies):
    company_ids = {draft.scope.company_id for draft in drafts}
    if len(company_ids) > max_unique_companies:
        raise ValueError(
            f"Stage 4 review_candidate budget is at most {max_unique_companies} unique companies"
        )
    result = {}
    for draft in drafts:
        assessment_id = _company_assessment_id(draft.scope)
        if assessment_id in result:
            raise ValueError("duplicate company assessment Scope in one Stage 4 run")
        result[assessment_id] = draft
    return result


def _validate_counter_search_receipt(
    challenger_set, evidence_ids, *, require_execution_lineage=False
):
    receipt = challenger_set.counter_search_receipt
    if receipt is None:
        if challenger_set.coverage_gate:
            raise ValueError("challenger coverage requires a counter-search receipt")
        return
    if receipt.challenger_set_id != challenger_set.challenger_set_id:
        raise ValueError("counter-search receipt is bound to another challenger set")
    if receipt.protocol_version != challenger_set.search_protocol_version:
        raise ValueError("counter-search receipt protocol does not match challenger set")
    if receipt.completed_at != challenger_set.search_completed_at:
        raise ValueError("counter-search receipt completion time does not match")
    if (
        receipt.max_queries <= 0
        or receipt.max_time_seconds <= 0
        or receipt.max_cost_usd <= 0
    ):
        raise ValueError("counter-search receipt budgets must be positive")
    query_log_ids = set(receipt.query_log_ids)
    if not query_log_ids or len(query_log_ids) != len(receipt.query_log_ids):
        raise ValueError("counter-search receipt requires unique query_log_ids")
    if not receipt.stop_reason.strip() or not receipt.route_findings:
        raise ValueError("counter-search receipt requires stop reason and route findings")
    route_ids = set()
    unresolved = False
    allowed_statuses = {
        "supported_candidate",
        "explicitly_ineligible",
        "explicit_failure",
        "insufficient_capacity",
        "unresolved",
    }
    for finding in receipt.route_findings:
        if not finding.route_id.strip() or finding.route_id in route_ids:
            raise ValueError("counter-search receipt requires unique route_id values")
        route_ids.add(finding.route_id)
        if finding.status not in allowed_statuses:
            raise ValueError("counter-search route finding has unsupported status")
        unresolved = unresolved or finding.status == "unresolved"
        if (
            not finding.finding.strip()
            or not finding.query_log_ids
            or not set(finding.query_log_ids) <= query_log_ids
        ):
            raise ValueError("counter-search route finding is not bound to query logs")
        if not set(finding.evidence_ids) <= evidence_ids:
            raise ValueError("counter-search route finding cites unknown evidence")
    if challenger_set.coverage_gate and (
        receipt.stop_reason != "protocol_complete" or unresolved
    ):
        raise ValueError("counter-search receipt cannot pass coverage before completion")
    if require_execution_lineage:
        provider_ids = tuple(receipt.provider_request_receipt_ids)
        if (
            not provider_ids
            or len(set(provider_ids)) != len(provider_ids)
            or any(not item.strip() for item in provider_ids)
        ):
            raise ValueError(
                "production counter-search requires unique provider request receipts"
            )
        route_provider_ids = []
        route_cost = 0.0
        for finding in receipt.route_findings:
            if (
                not finding.query.strip()
                or not finding.provider_request_receipt_id.strip()
                or finding.executed_at is None
                or finding.executed_at.tzinfo is None
                or finding.executed_at.utcoffset() is None
                or finding.executed_at > receipt.completed_at
                or finding.cost_usd < 0
            ):
                raise ValueError(
                    "production counter-search route lacks execution lineage"
                )
            route_provider_ids.append(finding.provider_request_receipt_id)
            route_cost += finding.cost_usd
        if tuple(route_provider_ids) != provider_ids:
            raise ValueError(
                "production counter-search provider request receipts do not match routes"
            )
        if (
            receipt.cost_usd_spent < 0
            or receipt.cost_usd_spent > receipt.max_cost_usd
            or round(route_cost, 6) != round(receipt.cost_usd_spent, 6)
        ):
            raise ValueError("production counter-search cost receipt is invalid")


def _validate_company_draft(
    draft, evidence_ids, evidence_claims, evidence_cards_by_id, segment_ids
):
    if not draft.company_name.strip() or not draft.scope.company_id.strip():
        raise ValueError("company identity is required")
    if draft.scope.segment_id not in segment_ids:
        raise ValueError("company scope references an unassessed segment")
    scope_values = (
        draft.scope.product_id,
        draft.scope.customer_or_platform_scope,
        draft.scope.geography,
    )
    if not all(value.strip() for value in scope_values) or draft.scope.time_horizon_months <= 0:
        raise ValueError("complete company assessment scope is required")
    if not draft.roles or not set(draft.roles) <= ALLOWED_ROLES:
        raise ValueError("company roles must be explicit and recognized")
    exposure_values = (
        draft.exposure.narrative,
        draft.exposure.operational,
        draft.exposure.revenue,
        draft.exposure.earnings,
    )
    if not all(value.strip() for value in exposure_values):
        raise ValueError("narrative, operational, revenue and earnings exposure must be separate")
    if not draft.exposure.evidence_ids or not set(draft.exposure.evidence_ids) <= evidence_ids:
        raise ValueError("company exposure must bind to persisted evidence")
    gate_by_id = {}
    for gate in draft.gate_results:
        if gate.gate_id in gate_by_id:
            raise ValueError("duplicate evidence-bound business gate")
        if gate.label not in {"pass", "fail", "unknown"}:
            raise ValueError("business gate label must be pass, fail or unknown")
        if not gate.required_predicate_ids:
            raise ValueError("business gate requires explicit predicates")
        if gate.label in {"pass", "fail"} and not gate.decisive_evidence_ids:
            raise ValueError("resolved business gate requires decisive evidence")
        if not set(gate.decisive_evidence_ids) <= evidence_ids:
            raise ValueError("business gate cites unknown decisive evidence")
        for evidence_id in gate.decisive_evidence_ids:
            claim = evidence_claims.get(evidence_id)
            if claim is None or not claim.scoring_eligible:
                raise ValueError("business gate decisive evidence must be scoring eligible")
            if not _claim_scope_matches_company(claim.assessment_scope, draft.scope):
                raise ValueError("business gate evidence does not match assessment Scope")
        gate_by_id[gate.gate_id] = gate
    scored_dimensions = tuple(
        item
        for item in (
            *draft.defensibility,
            *draft.replacement,
            *draft.earnings,
            *((draft.ecosystem_compatibility,) if draft.ecosystem_compatibility else ()),
            *((draft.displacement,) if draft.displacement else ()),
        )
        if item.evidence_state == "supported"
    )
    high_fact_owners = {}
    for dimension in scored_dimensions:
        primary_support_found = False
        for evidence_id in dimension.evidence_ids:
            claim = evidence_claims.get(evidence_id)
            card = evidence_cards_by_id.get(evidence_id)
            if claim is None:
                raise ValueError("scoring evidence must resolve to a persisted claim")
            if card is None or card.stance != "supports":
                raise ValueError(
                    "supported company dimensions require supporting Evidence Cards"
                )
            if not claim.scoring_eligible:
                raise ValueError("ineligible evidence cannot support scoring")
            if not _claim_scope_matches_company(claim.assessment_scope, draft.scope):
                raise ValueError("scoring evidence does not match the company assessment Scope")
            if claim.scoring_use == "context_only":
                raise ValueError("context_only evidence cannot support scoring")
            if claim.scoring_use == "primary":
                if claim.primary_scoring_dimension != dimension.dimension:
                    raise ValueError("primary scoring evidence belongs to another dimension")
                primary_support_found = True
                if dimension.rating_min >= 3:
                    owner = high_fact_owners.setdefault(claim.fact_key, dimension.dimension)
                    if owner != dimension.dimension:
                        raise ValueError(
                            "one atomic fact may have only one primary high-score dimension"
                        )
        if dimension.rating_min >= 3 and not primary_support_found:
            raise ValueError("high-score dimension requires matching primary evidence")
    needed_reviews = set()
    if draft.defensibility:
        needed_reviews.add("defensibility")
    if draft.replacement:
        needed_reviews.add("replacement_momentum")
    if any(item.evidence_state != "unknown" for item in draft.earnings):
        needed_reviews.add("earnings_transmission")
    reviews = {review.thesis: review for review in draft.red_team_reviews}
    if not needed_reviews <= set(reviews):
        raise ValueError("each supported thesis requires a thesis-specific red-team review")
    for thesis in needed_reviews:
        review = reviews[thesis]
        if not review.counter_evidence_queries or not review.falsification_conditions:
            raise ValueError("red-team review requires counter-search and falsification conditions")
        if not set(review.evidence_ids) <= evidence_ids:
            raise ValueError("red-team review cites unknown evidence")
    if draft.replacement:
        if draft.replacement_mode not in {"product", "service"}:
            raise ValueError("replacement_mode must be product or service")
        if not draft.ecosystem_compatibility or not draft.displacement or not draft.milestones:
            raise ValueError("replacement requires independent gates and milestone axes")


def _derive_company_gate_results(
    draft,
    evidence_claims,
    evidence_cards_by_id,
    *,
    repository=None,
    run_id=None,
    governance_bundle_sha256=None,
    decision_table=None,
):
    derived = []
    for gate in draft.gate_results:
        specification = CANONICAL_GATE_PREDICATES.get(gate.gate_id)
        if specification is None:
            raise ValueError("business gate is not in the canonical predicate directory")
        if tuple(gate.required_predicate_ids) != specification["predicate_ids"]:
            raise ValueError("business gate predicate IDs are not canonical")
        capabilities = set()
        for evidence_id in gate.decisive_evidence_ids:
            claim = evidence_claims.get(evidence_id)
            card = evidence_cards_by_id.get(evidence_id)
            if claim is None or card is None:
                continue
            capabilities.update(
                _authorized_claim_capabilities(
                    claim,
                    card,
                    repository=repository,
                    run_id=run_id,
                    governance_bundle_sha256=governance_bundle_sha256,
                    decision_table=decision_table,
                )
            )
        pass_all = set(specification["pass_all"])
        fail_all = set(specification["fail_all"])
        if pass_all and pass_all <= capabilities:
            label = "pass"
        elif fail_all and fail_all <= capabilities:
            label = "fail"
        else:
            label = "unknown"
        if gate.label != label:
            raise ValueError(
                "self-reported business gate does not satisfy canonical gate predicates"
            )
        derived.append(replace(gate, label=label))
    return replace(draft, gate_results=tuple(derived))


def _authorized_claim_capabilities(
    claim,
    card,
    *,
    repository=None,
    run_id=None,
    governance_bundle_sha256=None,
    decision_table=None,
):
    """Derive capability authority from independently verified typed assertions."""
    if card.stance != "supports" or not claim.scoring_eligible or not card.scoring_eligible:
        return ()
    if claim.assessment_scope is None or card.assessment_scope != claim.assessment_scope:
        return ()
    decision_table = decision_table or _business_fact_decision_table()
    declared = set(claim.claim_capabilities) & set(card.claim_capabilities)
    conditions = set(claim.condition_ids) & set(card.condition_ids)
    authorized = []
    for capability in declared:
        condition_id = f"capability.{capability}.v1.4"
        if condition_id not in conditions:
            continue
        if capability in decision_table["unstructured_exempt_capabilities"]:
            authorized.append(capability)
            continue
        if not any(
            _assertion_authorizes_capability(
                assertion,
                capability,
                claim,
                card,
                decision_table,
                repository=repository,
                run_id=run_id,
                governance_bundle_sha256=governance_bundle_sha256,
            )
            for assertion in card.business_fact_assertions
        ):
            continue
        authorized.append(capability)
    return tuple(authorized)


def _business_fact_decision_table():
    table_path = (
        Path(__file__).resolve().parents[3]
        / "tools"
        / "theme-chokepoint"
        / "business-fact-decision-table-v1.4.json"
    )
    return json.loads(table_path.read_text(encoding="utf-8"))


def _assertion_authorizes_capability(
    assertion,
    capability,
    claim,
    card,
    table,
    *,
    repository,
    run_id,
    governance_bundle_sha256,
):
    scope = claim.assessment_scope
    if (
        repository is None
        or not run_id
        or not governance_bundle_sha256
        or not repository.business_fact_assertion_is_verified(
            run_id=run_id,
            assertion=assertion,
            governance_bundle_sha256=governance_bundle_sha256,
        )
    ):
        return False
    if assertion.polarity not in table["allowed_polarities"]:
        return False
    if assertion.lifecycle_state not in table["allowed_lifecycle_states"]:
        return False
    if assertion.evidence_id != card.evidence_id:
        return False
    if assertion.canonical_predicate_id != f"capability.{capability}.v1.4":
        return False
    if assertion.subject_company_id != scope.company_id:
        return False
    if assertion.subject_product_id != scope.product_id:
        return False
    if assertion.quote_start != card.quote_start or assertion.quote_end != card.quote_end:
        return False
    if assertion.exact_quote_sha256 != sha256(card.exact_quote.encode("utf-8")).hexdigest():
        return False
    if capability in table["accounting_capabilities"]:
        if not any(
            marker in card.source_type.casefold()
            for marker in table["official_accounting_source_markers"]
        ):
            return False
        if any(
            getattr(assertion, field, None) in (None, "")
            for field in table["required_accounting_fields"]
        ):
            return False
    return True


def _claim_scope_matches_company(claim_scope, company_scope):
    if claim_scope is None:
        return False
    return (
        claim_scope.company_id == company_scope.company_id
        and claim_scope.product_id == company_scope.product_id
        and claim_scope.segment_id == company_scope.segment_id
        and claim_scope.customer_or_platform_scope
        == company_scope.customer_or_platform_scope
        and claim_scope.geography == company_scope.geography
        and claim_scope.time_horizon_months == company_scope.time_horizon_months
        and claim_scope.as_of_date == company_scope.as_of_date
    )


def _family_metrics(dimensions, weights, evidence_ids, family):
    by_name = {item.dimension: item for item in dimensions}
    if len(by_name) != len(dimensions) or set(by_name) != set(weights):
        raise ValueError(f"{family} dimensions must match frozen v1.4 contract")
    score_min = score_max = presence = resolved = decision = 0.0
    total = float(sum(weights.values()))
    for name, weight in weights.items():
        item = by_name[name]
        _validate_dimension(item, evidence_ids)
        score_min += weight * item.rating_min / 4
        score_max += weight * item.rating_max / 4
        if item.evidence_state in {"supported", "conflicted"}:
            presence += weight
        if item.evidence_state == "supported":
            resolved += weight
            decision += weight * (1 - (item.rating_max - item.rating_min) / 4)
    return (
        by_name,
        round(score_min, 1),
        round(score_max, 1),
        round(presence / total, 4),
        round(resolved / total, 4),
        round(decision / total, 4),
    )


def _score_defensibility(draft, contract, evidence_ids):
    weights = contract.weights_for("defensibility")
    by_name, low, high, presence, resolved, decision = _family_metrics(
        draft.defensibility, weights, evidence_ids, "defensibility"
    )
    sourcing = by_name["customer_sourcing_evidence"]
    eligible = (
        draft.scope_valid
        and draft.freshness_valid
        and not draft.evidence_mapping_error
        and decision >= 0.65
        and sourcing.evidence_state == "supported"
    )
    state = None
    gates = []
    withheld = None
    if not eligible:
        withheld = "defensibility_state_not_eligible"
    else:
        high_gate = (
            low >= 70
            and decision >= 0.70
            and sourcing.rating_min >= 2
            and any(
                by_name[name].rating_min >= 3
                for name in (
                    "technical_performance_gap",
                    "qualification_lock_in",
                    "switching_cost",
                    "qualified_effective_capacity",
                )
            )
        )
        low_gate = high < 50 and sourcing.rating_max <= 2
        if high_gate:
            state = "high_defensibility"
            gates.append("high_defensibility")
        elif low_gate:
            state = "low_defensibility"
            gates.append("low_defensibility")
        else:
            state = "medium_defensibility"
            gates.append("medium_defensibility")
    return ScoreFamilyAssessment(
        "defensibility", tuple(draft.defensibility), low, high, presence, resolved,
        decision, state, tuple(gates), withheld
    )


def _score_replacement(draft, contract, evidence_ids, challenger_set):
    for dimension in (
        *draft.replacement,
        draft.ecosystem_compatibility,
        draft.displacement,
    ):
        expected_profile = contract.replacement_anchor_profile(
            draft.replacement_mode, dimension.dimension
        )
        if dimension.anchor_profile != expected_profile:
            raise ValueError(
                "replacement anchor_profile does not match the selected mode"
            )
    weights = contract.weights_for("replacement")
    by_name, low, high, presence, resolved, decision = _family_metrics(
        draft.replacement, weights, evidence_ids, "replacement"
    )
    ecosystem = draft.ecosystem_compatibility
    displacement = draft.displacement
    _validate_dimension(ecosystem, evidence_ids)
    _validate_dimension(displacement, evidence_ids)
    positive = any(
        by_name[name].evidence_state == "supported"
        and by_name[name].rating_min >= 1
        for name in (
            "performance_parity",
            "qualification_progress",
            "customer_adoption",
        )
    )
    eligible = (
        draft.scope_valid
        and draft.freshness_valid
        and not draft.evidence_mapping_error
        and (positive or _gate_label(draft, "failed_or_withdrawn") == "pass")
    )
    if not eligible:
        return ScoreFamilyAssessment(
            "replacement", tuple(draft.replacement), low, high, presence, resolved,
            decision, None, (), "replacement_state_not_eligible"
        )
    performance = by_name["performance_parity"]
    qualification = by_name["qualification_progress"]
    capacity = by_name["capacity_readiness"]
    adoption = by_name["customer_adoption"]
    fatal_label = _gate_label(draft, "fatal_blocker_clear")
    fatal_pass = fatal_label == "pass" and _has_completed_counter_search(
        draft, "replacement_momentum", challenger_set
    )
    hard_gates = {
        "performance_gate": performance.evidence_state == "supported" and performance.rating_min >= 2,
        "qualification_gate": qualification.evidence_state == "supported" and qualification.rating_min >= 3,
        "capacity_gate": capacity.evidence_state == "supported" and capacity.rating_min >= 2,
        "ecosystem_gate": ecosystem.evidence_state == "supported" and ecosystem.rating_min >= 2,
        "fatal_blocker_gate": fatal_pass,
    }
    credible = (
        low >= 40
        and decision >= 0.35
        and (
            (
                qualification.evidence_state == "supported"
                and qualification.rating_min >= 1
            )
            or (
                adoption.evidence_state == "supported"
                and adoption.rating_min >= 1
            )
        )
        and not (performance.evidence_state == "supported" and performance.rating_max < 2)
        and fatal_label != "fail"
    )
    ready = low >= 60 and decision >= 0.60 and all(hard_gates.values())
    production = (
        ready
        and _gate_label(draft, "production_use") == "pass"
        and _gate_label(draft, "qualified_saleable_output") == "pass"
    )
    scaled_alt = (
        production
        and adoption.evidence_state == "supported"
        and adoption.rating_min >= 3
        and capacity.evidence_state == "supported"
        and capacity.rating_min >= 3
        and _gate_label(draft, "scale_gate") == "pass"
    )
    realized = (
        production
        and all(
            item.evidence_state == "supported" and item.rating_min >= 3
            for item in (performance, qualification, capacity, adoption, ecosystem, displacement)
        )
        and fatal_pass
    )
    scaled_replacement = realized and displacement.rating_min >= 4 and scaled_alt
    gates = tuple(name for name, passed in hard_gates.items() if passed)
    positive_gates = []
    if credible:
        positive_gates.append("credible_challenge")
    if ready:
        positive_gates.append("replacement_ready")
    if production:
        positive_gates.append("production_alternative")
    if scaled_alt:
        positive_gates.append("scaled_alternative")
    if realized:
        positive_gates.append("realized_replacement")
    if scaled_replacement:
        positive_gates.append("scaled_replacement")
    if _gate_label(draft, "failed_or_withdrawn") == "pass":
        if positive_gates:
            raise ValueError("failed product cannot also carry a positive replacement state")
        state = "failed_or_withdrawn"
    elif scaled_replacement:
        state = "scaled_replacement"
    elif realized:
        state = "realized_replacement"
    elif scaled_alt:
        state = "scaled_alternative"
    elif production:
        state = "production_alternative"
    elif ready:
        state = "replacement_ready"
    elif credible:
        state = "credible_challenge"
    else:
        state = "early_signal"
    return ScoreFamilyAssessment(
        "replacement", tuple(draft.replacement), low, high, presence, resolved,
        decision, state, tuple((*gates, *positive_gates)), None
    )


def _score_earnings(
    draft,
    contract,
    evidence_ids,
    evidence_claims,
    evidence_cards_by_id,
    *,
    repository,
    run_id,
):
    _validate_earnings_evidence(
        draft,
        evidence_claims,
        evidence_cards_by_id,
        repository=repository,
        run_id=run_id,
        governance_bundle_sha256=contract.governance_bundle_sha256,
        decision_table=contract.business_fact_decision_table,
    )
    weights = contract.weights_for("earnings")
    by_name, low, high, presence, resolved, decision = _family_metrics(
        draft.earnings, weights, evidence_ids, "earnings"
    )
    mandatory = tuple(
        by_name[name]
        for name in ("revenue_materiality", "time_to_revenue", "margin_transmission")
    )
    eligible = (
        draft.scope_valid
        and draft.freshness_valid
        and not draft.evidence_mapping_error
        and decision >= 0.65
        and all(item.evidence_state == "supported" for item in mandatory)
    )
    if not eligible:
        return ScoreFamilyAssessment(
            "earnings", tuple(draft.earnings), low, high, presence, resolved,
            decision, None, (), "mandatory_earnings_fields_unresolved"
        )
    hard_fail = _gate_label(draft, "capital_hard_fail") == "pass" or any(
        item.rating_max < 2 for item in mandatory
    )
    material = not hard_fail and low >= 65 and decision >= 0.70 and all(
        item.rating_min >= 2 for item in mandatory
    )
    weak = hard_fail or (decision >= 0.65 and high < 50)
    if weak:
        state, gates = "weak_earnings_capture", ("weak_earnings_gate",)
    elif material:
        state, gates = "material_earnings_path", ("material_earnings_gate",)
    else:
        state, gates = "moderate_earnings_path", ("moderate_earnings_path",)
    return ScoreFamilyAssessment(
        "earnings", tuple(draft.earnings), low, high, presence, resolved,
        decision, state, gates, None
    )


def _validate_earnings_evidence(
    draft,
    evidence_claims,
    evidence_cards_by_id,
    *,
    repository,
    run_id,
    governance_bundle_sha256,
    decision_table,
):
    by_name = {item.dimension: item for item in draft.earnings}
    for dimension_name in ("revenue_materiality", "time_to_revenue"):
        dimension = by_name[dimension_name]
        if dimension.evidence_state != "supported":
            continue
        authorized = set()
        for evidence_id in dimension.evidence_ids:
            authorized.update(
                _authorized_claim_capabilities(
                    evidence_claims[evidence_id],
                    evidence_cards_by_id[evidence_id],
                    repository=repository,
                    run_id=run_id,
                    governance_bundle_sha256=governance_bundle_sha256,
                    decision_table=decision_table,
                )
            )
        if "accounting_revenue_confirmed" not in authorized:
            raise ValueError(
                f"{dimension_name} requires accounting-revenue capability"
            )

    persistence = by_name["earnings_persistence"]
    if persistence.evidence_state != "supported":
        return
    cards = tuple(
        evidence_cards_by_id[evidence_id]
        for evidence_id in persistence.evidence_ids
    )
    authorized = set()
    for card in cards:
        authorized.update(
            _authorized_claim_capabilities(
                evidence_claims[card.evidence_id],
                card,
                repository=repository,
                run_id=run_id,
                governance_bundle_sha256=governance_bundle_sha256,
                decision_table=decision_table,
            )
        )
    if not {
        "accounting_revenue_confirmed",
        "multi_period_revenue_confirmed",
    } <= authorized:
        raise ValueError(
            "earnings_persistence requires accounting-revenue capability"
        )
    if any(card.accounting_period_type != "quarter" for card in cards):
        raise ValueError("earnings_persistence requires quarter lineage")
    accounting_assertions = []
    for card in cards:
        claim = evidence_claims[card.evidence_id]
        matches = tuple(
            assertion
            for assertion in card.business_fact_assertions
            if assertion.canonical_predicate_id
            == "capability.accounting_revenue_confirmed.v1.4"
            and _assertion_authorizes_capability(
                assertion,
                "accounting_revenue_confirmed",
                claim,
                card,
                decision_table,
                repository=repository,
                run_id=run_id,
                governance_bundle_sha256=governance_bundle_sha256,
            )
        )
        if len(matches) != 1:
            raise ValueError(
                "earnings_persistence requires one verified accounting fact per quarter"
            )
        assertion = matches[0]
        if (
            assertion.fiscal_period_type != card.accounting_period_type
            or assertion.fiscal_period_id != card.accounting_period_id
        ):
            raise ValueError("earnings_persistence quarter lineage mismatch")
        accounting_assertions.append(assertion)
    accounting_bases = {
        (
            assertion.accounting_metric,
            assertion.accounting_unit,
            assertion.currency,
            assertion.accounting_basis,
            assertion.subject_company_id,
            assertion.subject_product_id,
        )
        for assertion in accounting_assertions
    }
    if len(accounting_bases) != 1:
        raise ValueError("earnings_persistence requires comparable accounting basis")
    distinct_quarters = {
        assertion.fiscal_period_id
        for assertion in accounting_assertions
        if assertion.fiscal_period_id
    }
    if len(distinct_quarters) < 4:
        raise ValueError(
            "earnings_persistence requires four distinct fiscal quarters"
        )


def _has_completed_counter_search(draft, thesis, challenger_set):
    reviews = tuple(
        review
        for review in draft.red_team_reviews
        if review.thesis == thesis and bool(review.counter_evidence_queries)
    )
    if not reviews or challenger_set is None or not challenger_set.coverage_gate:
        return False
    receipt = challenger_set.counter_search_receipt
    if receipt is None or receipt.stop_reason != "protocol_complete":
        return False
    if any(finding.status == "unresolved" for finding in receipt.route_findings):
        return False
    review_evidence_ids = {
        evidence_id for review in reviews for evidence_id in review.evidence_ids
    }
    return any(
        finding.query_log_ids
        and review_evidence_ids.intersection(finding.evidence_ids)
        for finding in receipt.route_findings
    )


def _gate_label(draft, gate_id):
    for gate in draft.gate_results:
        if gate.gate_id == gate_id:
            return gate.label
    return "unknown"


def _challenger_label(replacement):
    if replacement is None or replacement.primary_state in {None, "early_signal", "failed_or_withdrawn"}:
        return None
    return {
        "credible_challenge": "emerging_replacement",
        "replacement_ready": "qualified_replacement",
        "production_alternative": "production_supplier",
        "scaled_alternative": "scaled_supplier",
        "realized_replacement": "realized_replacement",
        "scaled_replacement": "realized_replacement",
    }.get(replacement.primary_state)


def _competition_state(
    incumbent, draft, challenger_set, by_company, *, assessment_by_id=None
):
    if incumbent.defensibility is None or incumbent.defensibility.primary_state is None:
        return None, (), "incumbent_defensibility_unresolved"
    if challenger_set is None:
        return None, (), "challenger_set_missing"
    if not challenger_set.coverage_gate:
        return None, (), "challenger_set_coverage_gate_not_passed"
    if challenger_set.included_assessment_ids:
        assessment_by_id = assessment_by_id or {}
        challengers = [
            assessment_by_id[assessment_id]
            for assessment_id in challenger_set.included_assessment_ids
            if assessment_id in assessment_by_id
            and _competition_scope_matches(
                incumbent.scope, assessment_by_id[assessment_id].scope
            )
        ]
    else:
        challengers = [
            candidate
            for company_id in challenger_set.included_company_ids
            for candidate in by_company.get(company_id, ())
            if _competition_scope_matches(incumbent.scope, candidate.scope)
        ]
    states = {
        challenger.replacement.primary_state
        for challenger in challengers
        if challenger.replacement is not None
    }
    defense = incumbent.defensibility.primary_state
    realized = bool(states & REALIZED_REPLACEMENT_STATES)
    ready = bool(states & READY_REPLACEMENT_STATES)
    credible = bool(states & POSITIVE_REPLACEMENT_STATES)
    if realized or (defense == "low_defensibility" and ready):
        return "vulnerable_incumbent", ("vulnerable_incumbent",), None
    if defense in {"high_defensibility", "medium_defensibility"} and credible:
        return "contested_chokepoint_owner", ("contested_chokepoint_owner",), None
    if (
        defense == "high_defensibility"
        and not credible
        and _gate_label(draft, "customer_dependency_current") == "pass"
    ):
        return "durable_chokepoint_owner", ("durable_chokepoint_owner",), None
    if defense == "medium_defensibility" and not credible:
        return "differentiated_incumbent", ("differentiated_incumbent",), None
    if defense == "low_defensibility" and not ready:
        return (
            "commodity_or_weakly_differentiated",
            ("commodity_or_weakly_differentiated",),
            None,
        )
    return None, (), "competition_state_contract_unresolved"


def _competition_scope_matches(incumbent_scope, challenger_scope):
    return (
        incumbent_scope.segment_id == challenger_scope.segment_id
        and incumbent_scope.customer_or_platform_scope
        == challenger_scope.customer_or_platform_scope
        and incumbent_scope.geography == challenger_scope.geography
        and incumbent_scope.time_horizon_months
        == challenger_scope.time_horizon_months
        and incumbent_scope.as_of_date == challenger_scope.as_of_date
    )


def _company_assessment_id(scope) -> str:
    parts = (
        scope.company_id,
        scope.segment_id,
        scope.product_id,
        scope.customer_or_platform_scope,
        scope.geography,
        str(scope.time_horizon_months),
        scope.as_of_date.isoformat(),
    )
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"company_assessment_{digest}"
