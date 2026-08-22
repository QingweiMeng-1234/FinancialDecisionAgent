"""Production composition for evidence-bound company research in Stage 4."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Protocol
from uuid import uuid4

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult,
    AssessmentScope,
    BoundBasis,
    BusinessFactAssertion,
    ChallengerSet,
    ChallengerSetBuildBatch,
    ClaimDraft,
    CompanyAssessmentDraft,
    CompanyCandidate,
    CompanyChainReceipt,
    CompanyCriticResult,
    CompanyMappingBatch,
    CompanyRawProviderResponse,
    CompanyResearchResult,
    CompanyScoringResult,
    CompanyScope,
    CompanyExposure,
    CompanyProviderParsedResultRecord,
    CompanyProviderRawResponseRecord,
    CompanyProviderReconciliationRecord,
    CompanyProviderRequestRecord,
    CounterSearchReceipt,
    CounterSearchRouteFinding,
    DimensionRatingDraft,
    EvidenceAcquisitionBatch,
    EvidenceCandidate,
    GateResult,
    MilestoneAxes,
    RedTeamReview,
)
from event_collector.theme_chokepoint.stage3 import (
    EvidenceChokepointLoop,
    _candidate_fact_key,
    _validate_candidate,
)
from event_collector.theme_chokepoint.fact_verification import (
    parse_business_fact_text_semantics,
)
from event_collector.theme_chokepoint.source_identity import (
    resolve_source_identity,
)


class CompanyMapper(Protocol):
    def map(self, *, run, graph, stage3_result) -> CompanyMappingBatch: ...


class CompanyEvidenceAcquirer(Protocol):
    def acquire(
        self, *, run, graph, stage3_result, company: CompanyCandidate
    ) -> EvidenceAcquisitionBatch: ...


class CompanyOrdinalScorer(Protocol):
    def score(
        self, *, run, graph, stage3_result, company, claims, evidence_cards
    ) -> CompanyScoringResult: ...


class IndependentCompanyCritic(Protocol):
    def review(
        self, *, run, graph, stage3_result, company, draft, claims, evidence_cards
    ) -> CompanyCriticResult: ...


class ChallengerSetBuilder(Protocol):
    def build(self, **kwargs): ...


class MappedChallengerSetBuilder:
    """Seed typed v1.5 sets from mapping without claiming search completion.

    This builder deliberately leaves ``coverage_gate`` false.  A route/company
    counter-search provider must later replace the discovery-only set before
    Competition State can be released.
    """

    _CHALLENGER_ROLES = {"substitute_supplier", "bottleneck_solver"}

    def build(
        self, *, mapping: CompanyMappingBatch, completed_at: datetime, **_context
    ) -> tuple[ChallengerSet, ...]:
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("challenger discovery completion time must be timezone-aware")
        results = []
        owners = [
            item for item in mapping.candidates if "bottleneck_owner" in item.roles
        ]
        for owner in owners:
            owner_scope = owner.scope
            atomic_scope = AssessmentScope(
                company_id=None,
                product_id=owner_scope.product_id,
                segment_id=owner_scope.segment_id,
                customer_or_platform_scope=owner_scope.customer_or_platform_scope,
                geography=owner_scope.geography,
                time_horizon_months=owner_scope.time_horizon_months,
                as_of_date=owner_scope.as_of_date,
            )
            candidates = [
                item
                for item in mapping.candidates
                if item.scope.company_id != owner_scope.company_id
                and _same_competition_scope(item.scope, owner_scope)
            ]
            included = tuple(
                _mapped_assessment_id(item.scope)
                for item in candidates
                if set(item.roles) & self._CHALLENGER_ROLES
            )
            excluded = tuple(
                (item.scope.company_id, "not_classified_as_material_challenger")
                for item in candidates
                if not set(item.roles) & self._CHALLENGER_ROLES
            )
            identity = _stable_id(
                "challenger-set-v15-",
                *(
                    str(value)
                    for value in (
                        atomic_scope.product_id,
                        atomic_scope.segment_id,
                        atomic_scope.customer_or_platform_scope,
                        atomic_scope.geography,
                        atomic_scope.time_horizon_months,
                        atomic_scope.as_of_date,
                    )
                ),
            )
            results.append(
                ChallengerSet(
                    challenger_set_id=identity,
                    segment_id=atomic_scope.segment_id,
                    assessment_scope=atomic_scope,
                    search_protocol_version="challenger-search-v1.5",
                    included_company_ids=(),
                    excluded_candidates_with_reason=excluded,
                    search_completed_at=completed_at,
                    as_of_date=atomic_scope.as_of_date,
                    coverage_gate=False,
                    counter_search_receipt=None,
                    included_assessment_ids=included,
                )
            )
        return tuple(results)


class ProviderChallengerSetBuilder:
    """Persist provider-backed route/company counter-search and parse typed sets."""

    def __init__(self, transport, repository):
        if repository is None:
            raise ValueError("provider ChallengerSet builder requires a durable repository")
        method = getattr(transport, "build_challenger_sets", None)
        if not callable(method):
            raise ValueError("company discovery client requires build_challenger_sets")
        self.transport = transport
        self.repository = repository

    def build(
        self,
        *,
        run,
        graph,
        stage3_result,
        mapping,
        drafts,
        claims,
        evidence_cards,
        completed_at,
        previous_challenger_sets=(),
    ) -> ChallengerSetBuildBatch:
        execution = _start_company_execution(
            self.repository,
            role="discovery",
            run=run,
            company=None,
            payload={
                "operation": "challenger_set_builder_v1.5",
                "mapped_company_ids": tuple(
                    item.scope.company_id for item in mapping.candidates
                ),
                "assessment_ids": tuple(
                    _mapped_assessment_id(draft.scope) for draft in drafts
                ),
                "claim_ids": tuple(claim.claim_id for claim in claims),
                "evidence_ids": tuple(card.evidence_id for card in evidence_cards),
                "stage3_run_id": getattr(stage3_result, "run_id", None),
                "resume_challenger_set_ids": tuple(
                    item.challenger_set_id for item in previous_challenger_sets
                ),
                "completed_query_log_ids": tuple(
                    query_log_id
                    for item in previous_challenger_sets
                    if item.counter_search_receipt is not None
                    for query_log_id in item.counter_search_receipt.query_log_ids
                ),
                "completed_route_provider_receipt_ids": tuple(
                    receipt_id
                    for item in previous_challenger_sets
                    if item.counter_search_receipt is not None
                    for receipt_id in item.counter_search_receipt.provider_request_receipt_ids
                ),
            },
        )
        raw = self.transport.build_challenger_sets(
            run=run,
            graph=graph,
            stage3_result=stage3_result,
            mapping=mapping,
            drafts=tuple(drafts),
            claims=tuple(claims),
            evidence_cards=tuple(evidence_cards),
            completed_at=completed_at,
            previous_challenger_sets=tuple(previous_challenger_sets),
            **_request_id_kwargs(execution),
        )
        payload = _raw_payload(raw, "company challenger-set builder")
        completed_query_ids = {
            query_log_id
            for item in previous_challenger_sets
            if item.counter_search_receipt is not None
            for query_log_id in item.counter_search_receipt.query_log_ids
        }
        if completed_query_ids and not completed_query_ids <= set(
            payload.get("resumed_from_query_log_ids", ())
        ):
            raise ValueError(
                "company challenger-set provider did not acknowledge persisted query progress"
            )
        challenger_sets = _challenger_sets_from_provider_payload(payload)
        receipt_id = _finish_company_execution(
            self.repository,
            execution=execution,
            role="discovery",
            raw=raw,
            parsed_payload={"challenger_sets": challenger_sets},
        )
        return ChallengerSetBuildBatch(
            challenger_sets=challenger_sets,
            request_receipt_ids=(receipt_id,),
            cost_usd=raw.cost_usd,
        )


def _mapped_assessment_id(scope: CompanyScope) -> str:
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


def _same_competition_scope(left: CompanyScope, right: CompanyScope) -> bool:
    return (
        left.segment_id == right.segment_id
        and left.customer_or_platform_scope == right.customer_or_platform_scope
        and left.geography == right.geography
        and left.time_horizon_months == right.time_horizon_months
        and left.as_of_date == right.as_of_date
    )


class CompanyBusinessFactAssertionWriter:
    """Create producer-only proposals and delegate authority to the fact verifier."""

    def __init__(self, verifier, *, producer_execution_id: str):
        if verifier is None or not producer_execution_id.strip():
            raise ValueError("company assertion writer requires verifier and producer identity")
        self.verifier = verifier
        self.producer_execution_id = producer_execution_id.strip()

    def write_and_verify(self, *, run_id, company, claims, evidence_cards):
        claims_by_id = {claim.claim_id: claim for claim in claims}
        updated = []
        for card in evidence_cards:
            claim = claims_by_id.get(card.claim_id)
            if claim is None:
                raise ValueError("company assertion writer requires linked claims")
            assertions = list(card.business_fact_assertions)
            declared = tuple(
                capability
                for capability in claim.claim_capabilities
                if capability != "general_scoring_evidence"
            )
            for capability in declared:
                condition_id = f"capability.{capability}.v1.4"
                if condition_id not in claim.condition_ids:
                    raise ValueError(
                        "company capability proposal lacks its canonical condition"
                    )
                assertion = _business_fact_assertion_from_card(
                    company=company,
                    card=card,
                    capability=capability,
                )
                if not card.source_identity_id or not card.source_version_id:
                    raise ValueError(
                        "company fact proposal requires persisted source identity/version"
                    )
                self.verifier.verify(
                    run_id=run_id,
                    assertion=assertion,
                    producer_execution_id=self.producer_execution_id,
                    source_identity_id=card.source_identity_id,
                    source_version_id=card.source_version_id,
                    source_type=card.source_type,
                )
                assertions.append(assertion)
            updated.append(
                replace(card, business_fact_assertions=tuple(assertions))
            )
        return tuple(updated)


def _business_fact_assertion_from_card(*, company, card, capability):
    text = card.exact_quote.strip()
    polarity, lifecycle_state = parse_business_fact_text_semantics(text)
    if polarity == "negative":
        raise ValueError("company fact proposal is not affirmative and current")
    if polarity != "affirmative":
        raise ValueError("company fact proposal lacks affirmative original text")
    accounting = _accounting_assertion_fields(capability, text, card)
    quote_sha256 = sha256(card.exact_quote.encode("utf-8")).hexdigest()
    assertion_id = _stable_id(
        "company-assertion-",
        card.evidence_id,
        capability,
        quote_sha256,
    )
    return BusinessFactAssertion(
        assertion_id=assertion_id,
        evidence_id=card.evidence_id,
        subject_company_id=company.scope.company_id,
        subject_product_id=company.scope.product_id,
        canonical_predicate_id=f"capability.{capability}.v1.4",
        polarity="affirmative",
        lifecycle_state=lifecycle_state,
        quote_start=card.quote_start,
        quote_end=card.quote_end,
        exact_quote_sha256=quote_sha256,
        verification=None,
        **accounting,
    )


def _accounting_assertion_fields(capability, text, card):
    if capability not in {
        "accounting_revenue_confirmed",
        "multi_period_revenue_confirmed",
    }:
        return {}
    official_source_types = {
        "company_filing",
        "regulatory_filing",
        "annual_report",
        "quarterly_report",
        "sec",
        "sec_filing",
    }
    if card.source_type.strip().casefold() not in official_source_types:
        raise ValueError("accounting proposal requires an official accounting source")
    value_match = re.search(
        r"(?P<currency>USD|US\$|\$)\s*(?P<value>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>thousand|million|billion)?",
        text,
        flags=re.IGNORECASE,
    )
    period_match = re.search(
        r"\b(?P<quarter>Q[1-4]\s*(?:FY\s*)?20\d{2})\b|"
        r"\b(?P<year>FY\s*20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    basis_match = re.search(r"\b(GAAP|IFRS)\b", text, flags=re.IGNORECASE)
    if (
        not value_match
        or not period_match
        or not basis_match
        or "revenue" not in text.casefold()
    ):
        raise ValueError("accounting proposal lacks metric/value/unit/period/basis")
    unit = (value_match.group("unit") or "").casefold()
    if not unit:
        raise ValueError("accounting proposal requires an explicit numeric unit")
    period = (period_match.group("quarter") or period_match.group("year")).upper()
    return {
        "accounting_metric": "revenue",
        "accounting_value": float(value_match.group("value")),
        "accounting_unit": unit,
        "currency": "USD",
        "fiscal_period_type": "quarter" if period.startswith("Q") else "fiscal_year",
        "fiscal_period_id": re.sub(r"\s+", "", period),
        "accounting_basis": basis_match.group(1).upper(),
    }


class ProviderCompanyMapper:
    def __init__(self, transport, repository=None):
        self.transport = transport
        self.repository = repository

    def map(self, *, run, graph, stage3_result):
        execution = _start_company_execution(
            self.repository,
            role="discovery",
            run=run,
            company=None,
            payload={
                "graph_node_ids": tuple(
                    getattr(node, "node_id", "") for node in getattr(graph, "nodes", ())
                ),
                "stage3_run_id": getattr(stage3_result, "run_id", None),
            },
        )
        raw = self.transport.map_companies(
            run=run,
            graph=graph,
            stage3_result=stage3_result,
            **_request_id_kwargs(execution),
        )
        payload = _raw_payload(raw, "company mapper")
        candidates = _company_candidates_from_discovery_payload(payload)
        receipt_id = _finish_company_execution(
            self.repository,
            execution=execution,
            role="discovery",
            raw=raw,
            parsed_payload={"candidates": candidates},
        )
        return CompanyMappingBatch(
            candidates=candidates,
            request_receipt_ids=(receipt_id,),
        )


class ProviderCompanyEvidenceAcquirer:
    def __init__(self, transport, repository=None, source_identity_resolver=None):
        self.transport = transport
        self.repository = repository
        self.source_identity_resolver = source_identity_resolver

    def acquire(self, *, run, graph, stage3_result, company):
        execution = _start_company_execution(
            self.repository,
            role="evidence",
            run=run,
            company=company,
            payload={
                "company": company,
                "stage3_run_id": getattr(stage3_result, "run_id", None),
            },
        )
        raw = self.transport.acquire_company_evidence(
            run=run,
            graph=graph,
            stage3_result=stage3_result,
            company=company,
            **_request_id_kwargs(execution),
        )
        payload = _raw_payload(raw, "company evidence acquisition")
        candidates = _company_evidence_candidates_from_payload(payload, company)
        if self.repository is not None:
            candidates = _persist_company_candidate_sources(
                self.repository,
                candidates,
                retrieved_at=raw.retrieved_at,
                source_identity_resolver=self.source_identity_resolver,
            )
        receipt_id = _finish_company_execution(
            self.repository,
            execution=execution,
            role="evidence",
            raw=raw,
            parsed_payload={"candidates": candidates},
        )
        return EvidenceAcquisitionBatch(
            candidates=candidates,
            cost_usd=raw.cost_usd,
            request_receipt_ids=(receipt_id,),
        )


class ProviderCompanyOrdinalScorer:
    def __init__(self, transport, repository=None):
        self.transport = transport
        self.repository = repository

    def score(self, *, run, graph, stage3_result, company, claims, evidence_cards):
        execution = _start_company_execution(
            self.repository,
            role="scoring",
            run=run,
            company=company,
            payload={
                "company": company,
                "claim_ids": tuple(claim.claim_id for claim in claims),
                "evidence_ids": tuple(card.evidence_id for card in evidence_cards),
            },
        )
        raw = self.transport.score_company(
            run=run,
            graph=graph,
            stage3_result=stage3_result,
            company=company,
            claims=claims,
            evidence_cards=evidence_cards,
            **_request_id_kwargs(execution),
        )
        payload = _raw_payload(raw, "company scorer", expected="raw model response")
        draft = _company_draft_from_model_payload(
            payload, company, claims, evidence_cards
        )
        receipt_id = _finish_company_execution(
            self.repository,
            execution=execution,
            role="scoring",
            raw=raw,
            parsed_payload={"draft": draft},
        )
        return CompanyScoringResult(
            draft=draft,
            request_receipt_id=receipt_id,
        )


class ProviderIndependentCompanyCritic:
    def __init__(self, transport, repository=None):
        self.transport = transport
        self.repository = repository

    def review(
        self, *, run, graph, stage3_result, company, draft, claims, evidence_cards
    ):
        execution = _start_company_execution(
            self.repository,
            role="critic",
            run=run,
            company=company,
            payload={
                "company": company,
                "scored_draft": draft,
                "claim_ids": tuple(claim.claim_id for claim in claims),
                "evidence_ids": tuple(card.evidence_id for card in evidence_cards),
            },
        )
        raw = self.transport.review_company(
            run=run,
            graph=graph,
            stage3_result=stage3_result,
            company=company,
            draft=draft,
            claims=claims,
            evidence_cards=evidence_cards,
            **_request_id_kwargs(execution),
        )
        payload = _raw_payload(raw, "company critic", expected="raw model response")
        reviewed_draft = _company_draft_from_critic_payload(payload, company, draft)
        receipt_id = _finish_company_execution(
            self.repository,
            execution=execution,
            role="critic",
            raw=raw,
            parsed_payload={"draft": reviewed_draft},
        )
        return CompanyCriticResult(
            draft=reviewed_draft,
            request_receipt_id=receipt_id,
        )


class EvidenceBoundCompanyResearcher:
    """Execute mapper -> acquisition -> scorer -> independent critic with lineage."""

    def __init__(
        self,
        mapper,
        acquirer,
        scorer,
        critic,
        *,
        assertion_writer=None,
        challenger_set_builder: ChallengerSetBuilder | None = None,
        max_companies: int = 10,
    ):
        if not 1 <= max_companies <= 10:
            raise ValueError("company review budget must be between 1 and 10")
        self.mapper = mapper
        self.acquirer = acquirer
        self.scorer = scorer
        self.critic = critic
        self.assertion_writer = assertion_writer
        self.challenger_set_builder = challenger_set_builder
        self.max_companies = max_companies

    def research(self, run, graph, stage3_result) -> CompanyResearchResult:
        return self._research(run, graph, stage3_result, previous_result=None)

    def resume(
        self, run, graph, stage3_result, previous_result
    ) -> CompanyResearchResult:
        return self._research(
            run, graph, stage3_result, previous_result=previous_result
        )

    def _research(
        self, run, graph, stage3_result, *, previous_result
    ) -> CompanyResearchResult:
        mapping = self.mapper.map(run=run, graph=graph, stage3_result=stage3_result)
        if not isinstance(mapping, CompanyMappingBatch):
            raise ValueError("company mapper must return CompanyMappingBatch")
        if not mapping.candidates or len(mapping.candidates) > self.max_companies:
            raise ValueError("company mapper must return 1..10 review candidates")
        _require_receipts(mapping.request_receipt_ids, "company mapper")
        company_ids = [item.scope.company_id for item in mapping.candidates]
        if any(not item.strip() for item in company_ids) or len(set(company_ids)) != len(
            company_ids
        ):
            raise ValueError("company mapper candidates require unique company IDs")
        _validate_mapping_scopes(mapping.candidates, graph, stage3_result)

        cards_by_id = {}
        claims_by_id = {}
        snapshots_by_article_id = {}
        fact_keys = set()
        drafts = []
        chain_receipts = []
        provider_receipts = list(mapping.request_receipt_ids)
        total_cost = 0.0

        for company in mapping.candidates:
            _validate_company_candidate(company)
            acquired = self.acquirer.acquire(
                run=run,
                graph=graph,
                stage3_result=stage3_result,
                company=company,
            )
            if not isinstance(acquired, EvidenceAcquisitionBatch):
                raise ValueError(
                    "company evidence acquirer must return EvidenceAcquisitionBatch"
                )
            _require_receipts(
                acquired.request_receipt_ids, "company evidence acquisition"
            )
            if acquired.cost_usd < 0:
                raise ValueError("company evidence acquisition cost cannot be negative")
            total_cost += acquired.cost_usd
            if total_cost > run.request.max_cost_usd:
                raise ValueError("company research cost exceeds the run budget")
            if not acquired.candidates:
                raise ValueError("company evidence acquisition returned no original evidence")
            for candidate in acquired.candidates:
                _validate_candidate(candidate)
                if not _scope_matches(candidate.claim.assessment_scope, company.scope):
                    raise ValueError("company acquisition evidence crosses assessment Scope")
                key = _candidate_fact_key(candidate)
                if key in fact_keys:
                    continue
                EvidenceChokepointLoop._materialize(
                    (candidate,),
                    cards_by_id,
                    claims_by_id,
                    snapshots_by_article_id,
                    fact_keys,
                )
            acquired_evidence_ids = tuple(
                card.evidence_id
                for card in cards_by_id.values()
                if _scope_matches(card.assessment_scope, company.scope)
            )
            if not acquired_evidence_ids:
                raise ValueError("company evidence acquisition produced no materialized evidence")
            acquired_id_set = set(acquired_evidence_ids)
            claims = tuple(
                claim
                for claim in claims_by_id.values()
                if _scope_matches(claim.assessment_scope, company.scope)
            )
            cards = tuple(
                card for card in cards_by_id.values() if card.evidence_id in acquired_id_set
            )
            requiring_verification = tuple(
                claim
                for claim in claims
                if set(claim.claim_capabilities) - {"general_scoring_evidence"}
            )
            if requiring_verification:
                if self.assertion_writer is None:
                    raise ValueError(
                        "verified capability evidence requires a production assertion writer"
                    )
                cards = tuple(
                    self.assertion_writer.write_and_verify(
                        run_id=_run_id(run),
                        company=company,
                        claims=claims,
                        evidence_cards=cards,
                    )
                )
                if {card.evidence_id for card in cards} != acquired_id_set:
                    raise ValueError(
                        "production assertion writer changed acquired evidence identity"
                    )
                cards_by_id.update({card.evidence_id: card for card in cards})

            scored = self.scorer.score(
                run=run,
                graph=graph,
                stage3_result=stage3_result,
                company=company,
                claims=claims,
                evidence_cards=cards,
            )
            if not isinstance(scored, CompanyScoringResult):
                raise ValueError("company scorer must return CompanyScoringResult")
            _require_receipts((scored.request_receipt_id,), "company scorer")
            _validate_draft_identity(scored.draft, company)
            _validate_draft_evidence(scored.draft, acquired_id_set)

            reviewed = self.critic.review(
                run=run,
                graph=graph,
                stage3_result=stage3_result,
                company=company,
                draft=scored.draft,
                claims=claims,
                evidence_cards=cards,
            )
            if not isinstance(reviewed, CompanyCriticResult):
                raise ValueError("company critic must return CompanyCriticResult")
            _require_receipts((reviewed.request_receipt_id,), "company critic")
            _validate_draft_identity(reviewed.draft, company)
            _validate_draft_evidence(reviewed.draft, acquired_id_set)
            drafts.append(reviewed.draft)
            provider_receipts.extend(acquired.request_receipt_ids)
            provider_receipts.extend(
                (scored.request_receipt_id, reviewed.request_receipt_id)
            )
            chain_receipts.append(
                CompanyChainReceipt(
                    company_id=company.scope.company_id,
                    mapper_request_receipt_ids=tuple(mapping.request_receipt_ids),
                    acquisition_request_receipt_ids=tuple(
                        acquired.request_receipt_ids
                    ),
                    scorer_request_receipt_id=scored.request_receipt_id,
                    critic_request_receipt_id=reviewed.request_receipt_id,
                    acquired_evidence_ids=acquired_evidence_ids,
                )
            )

        challenger_sets = ()
        if self.challenger_set_builder is not None:
            built_sets = self.challenger_set_builder.build(
                run=run,
                graph=graph,
                stage3_result=stage3_result,
                mapping=mapping,
                drafts=tuple(drafts),
                claims=tuple(claims_by_id.values()),
                evidence_cards=tuple(cards_by_id.values()),
                completed_at=datetime.now(timezone.utc),
                previous_challenger_sets=(
                    tuple(previous_result.challenger_sets)
                    if previous_result is not None
                    else ()
                ),
            )
            if isinstance(built_sets, ChallengerSetBuildBatch):
                challenger_sets = built_sets.challenger_sets
                _require_receipts(
                    built_sets.request_receipt_ids, "company challenger-set builder"
                )
                if built_sets.cost_usd < 0:
                    raise ValueError("company challenger-set search cost cannot be negative")
                total_cost += built_sets.cost_usd
                if total_cost > run.request.max_cost_usd:
                    raise ValueError("company research cost exceeds the run budget")
                provider_receipts.extend(built_sets.request_receipt_ids)
            else:
                challenger_sets = tuple(built_sets)
        if len(set(provider_receipts)) != len(provider_receipts):
            raise ValueError("company research provider receipts must be unique")
        return CompanyResearchResult(
            drafts=tuple(drafts),
            challenger_sets=tuple(challenger_sets),
            claims=tuple(claims_by_id.values()),
            evidence_cards=tuple(cards_by_id.values()),
            source_snapshots=tuple(snapshots_by_article_id.values()),
            chain_receipts=tuple(chain_receipts),
            provider_request_receipt_ids=tuple(provider_receipts),
            cost_usd_spent=round(total_cost, 6),
        )


def build_default_company_researcher(
    *,
    discovery_client,
    evidence_client,
    scoring_model_client,
    critic_model_client,
    repository=None,
    assertion_writer=None,
    source_identity_resolver=None,
    max_companies: int = 10,
    enable_v15_company_chain: bool = False,
):
    """Compose business parsers over four low-level raw-response clients."""
    clients = (
        discovery_client,
        evidence_client,
        scoring_model_client,
        critic_model_client,
    )
    if any(client is None for client in clients):
        raise ValueError("all company low-level clients are required")
    if len({id(client) for client in clients}) != len(clients):
        raise ValueError("company runtime requires four pairwise-independent clients")
    role_methods = (
        "map_companies",
        "acquire_company_evidence",
        "score_company",
        "review_company",
    )
    boundary_ids = tuple(
        _company_client_boundary_identity(client, method_name)
        for client, method_name in zip(clients, role_methods)
    )
    if len(set(boundary_ids)) != len(boundary_ids):
        raise ValueError(
            "company runtime rejects wrappers over a shared low-level transport facade"
        )
    if repository is None:
        raise ValueError("company runtime requires a durable repository")
    return EvidenceBoundCompanyResearcher(
        ProviderCompanyMapper(discovery_client, repository),
        ProviderCompanyEvidenceAcquirer(
            evidence_client,
            repository,
            source_identity_resolver=source_identity_resolver,
        ),
        ProviderCompanyOrdinalScorer(scoring_model_client, repository),
        ProviderIndependentCompanyCritic(critic_model_client, repository),
        assertion_writer=assertion_writer,
        challenger_set_builder=(
            ProviderChallengerSetBuilder(discovery_client, repository)
            if enable_v15_company_chain
            else None
        ),
        max_companies=max_companies,
    )


def _company_client_boundary_identity(client, method_name):
    method = getattr(client, method_name, None)
    if not callable(method):
        raise ValueError(f"company {method_name} client method is required")
    owner = getattr(method, "__self__", None)
    if owner is not None:
        return ("bound-owner", id(owner))
    closure = getattr(method, "__closure__", None) or ()
    captured = []
    for cell in closure:
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, (str, bytes, int, float, bool, tuple, frozenset, type(None))):
            continue
        captured.append(id(value))
    if captured:
        return ("closure-owner", *sorted(captured))
    return ("client", id(client))


def _run_id(run) -> str:
    run_id = getattr(getattr(run, "request", None), "run_id", None) or getattr(
        run, "run_id", None
    )
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("company provider execution requires a persisted run ID")
    return run_id


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _start_company_execution(repository, *, role, run, company, payload):
    if repository is None:
        return None
    operation_id = uuid4().hex
    run_id = _run_id(run)
    request_payload_json = _canonical_json(payload)
    request = CompanyProviderRequestRecord(
        request_record_id=f"company-{role}-request-{operation_id}",
        run_id=run_id,
        role=role,
        company_id=(company.scope.company_id if company is not None else None),
        request_payload_json=request_payload_json,
        request_payload_sha256=sha256(request_payload_json.encode("utf-8")).hexdigest(),
        created_at=datetime.now(timezone.utc),
    )
    repository.save_company_provider_request(request)
    return operation_id, request


def _request_id_kwargs(execution):
    if execution is None:
        return {}
    return {"request_record_id": execution[1].request_record_id}


def _finish_company_execution(
    repository, *, execution, role, raw, parsed_payload
) -> str:
    if repository is None or execution is None:
        return raw.provider_trace_id
    operation_id, request = execution
    response = CompanyProviderRawResponseRecord(
        response_record_id=f"company-{role}-response-{operation_id}",
        request_record_id=request.request_record_id,
        run_id=request.run_id,
        role=role,
        provider=raw.provider,
        provider_trace_id=raw.provider_trace_id,
        http_status=raw.http_status,
        raw_body=raw.raw_body,
        raw_response_sha256=sha256(raw.raw_body).hexdigest(),
        retrieved_at=raw.retrieved_at,
        cost_usd=raw.cost_usd,
    )
    repository.save_company_provider_raw_response(response)
    parsed_payload_json = _canonical_json(parsed_payload)
    result = CompanyProviderParsedResultRecord(
        result_record_id=f"company-{role}-result-{operation_id}",
        request_record_id=request.request_record_id,
        response_record_id=response.response_record_id,
        run_id=request.run_id,
        role=role,
        parsed_payload_json=parsed_payload_json,
        parsed_payload_sha256=sha256(parsed_payload_json.encode("utf-8")).hexdigest(),
        parser_version=f"company-{role}-parser-v1",
        parsed_at=datetime.now(timezone.utc),
    )
    repository.save_company_provider_parsed_result(result)
    reconciliation = CompanyProviderReconciliationRecord(
        receipt_id=f"company-{role}-reconciliation-{operation_id}",
        request_record_id=request.request_record_id,
        response_record_id=response.response_record_id,
        result_record_id=result.result_record_id,
        run_id=request.run_id,
        role=role,
        reconciled_at=datetime.now(timezone.utc),
    )
    return repository.reconcile_company_provider_execution(reconciliation).receipt_id


def _raw_payload(raw, component, *, expected="raw provider response"):
    if not isinstance(raw, CompanyRawProviderResponse):
        raise ValueError(f"{component} transport must return {expected}")
    if (
        not raw.provider.strip()
        or not raw.provider_trace_id.strip()
        or not 200 <= raw.http_status < 300
        or not raw.raw_body
        or raw.retrieved_at.tzinfo is None
        or raw.retrieved_at.utcoffset() is None
        or raw.cost_usd < 0
    ):
        raise ValueError(f"{component} raw provider response is incomplete")
    try:
        payload = json.loads(raw.raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{component} raw provider response is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{component} raw provider response must be an object")
    return payload


def _company_scope_from_payload(payload):
    return CompanyScope(
        **{
            **payload,
            "as_of_date": date.fromisoformat(payload["as_of_date"]),
        }
    )


def _company_candidate_from_discovery_item(payload):
    return CompanyCandidate(
        company_name=str(payload["name"]).strip(),
        scope=CompanyScope(
            company_id=str(payload["company_id"]).strip(),
            segment_id=str(payload["segment_id"]).strip(),
            product_id=str(payload["product_id"]).strip(),
            customer_or_platform_scope=str(
                payload["customer_or_platform_scope"]
            ).strip(),
            geography=str(payload["geography"]).strip(),
            time_horizon_months=int(payload["time_horizon_months"]),
            as_of_date=date.fromisoformat(payload["as_of_date"]),
        ),
        roles=tuple(payload["roles"]),
    )


def _company_candidates_from_discovery_payload(payload):
    if "candidates" in payload or not isinstance(payload.get("companies"), list):
        raise ValueError(
            "company discovery raw response must contain low-level companies"
        )
    try:
        return tuple(
            _company_candidate_from_discovery_item(item)
            for item in payload["companies"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("company discovery entity/scope payload is invalid") from error


def _challenger_sets_from_provider_payload(payload):
    raw_sets = payload.get("challenger_sets")
    if not isinstance(raw_sets, list):
        raise ValueError(
            "company challenger-set raw response must contain challenger_sets"
        )
    try:
        results = []
        for item in raw_sets:
            receipt_payload = item.get("counter_search_receipt")
            receipt = None
            if receipt_payload is not None:
                receipt = CounterSearchReceipt(
                    **{
                        **receipt_payload,
                        "query_log_ids": tuple(receipt_payload["query_log_ids"]),
                        "route_findings": tuple(
                            CounterSearchRouteFinding(
                                **{
                                    **finding,
                                    "query_log_ids": tuple(finding["query_log_ids"]),
                                    "evidence_ids": tuple(finding["evidence_ids"]),
                                    "executed_at": datetime.fromisoformat(
                                        finding["executed_at"]
                                    ),
                                }
                            )
                            for finding in receipt_payload["route_findings"]
                        ),
                        "negative_findings": tuple(
                            receipt_payload["negative_findings"]
                        ),
                        "completed_at": datetime.fromisoformat(
                            receipt_payload["completed_at"]
                        ),
                        "provider_request_receipt_ids": tuple(
                            receipt_payload.get("provider_request_receipt_ids", ())
                        ),
                    }
                )
            results.append(
                ChallengerSet(
                    challenger_set_id=str(item["challenger_set_id"]),
                    segment_id=str(item["segment_id"]),
                    assessment_scope=_assessment_scope_from_payload(
                        item["assessment_scope"]
                    ),
                    search_protocol_version=str(item["search_protocol_version"]),
                    included_company_ids=(),
                    included_assessment_ids=tuple(item["included_assessment_ids"]),
                    excluded_candidates_with_reason=tuple(
                        tuple(pair)
                        for pair in item["excluded_candidates_with_reason"]
                    ),
                    search_completed_at=datetime.fromisoformat(
                        item["search_completed_at"]
                    ),
                    as_of_date=date.fromisoformat(item["as_of_date"]),
                    coverage_gate=item["coverage_gate"] is True,
                    counter_search_receipt=receipt,
                )
            )
        return tuple(results)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("company"):
            raise
        raise ValueError("company challenger-set payload is invalid") from error


def _assessment_scope_from_payload(payload):
    return AssessmentScope(
        **{
            **payload,
            "as_of_date": date.fromisoformat(payload["as_of_date"]),
        }
    )


def _stable_id(prefix: str, *values) -> str:
    material = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}{sha256(material).hexdigest()[:24]}"


def _company_evidence_candidates_from_payload(payload, company):
    if "candidates" in payload or not isinstance(payload.get("documents"), list):
        raise ValueError(
            "company evidence raw response must contain original-text documents"
        )
    candidates = []
    scope = company.scope
    assessment_scope = AssessmentScope(
        company_id=scope.company_id,
        product_id=scope.product_id,
        segment_id=scope.segment_id,
        customer_or_platform_scope=scope.customer_or_platform_scope,
        geography=scope.geography,
        time_horizon_months=scope.time_horizon_months,
        as_of_date=scope.as_of_date,
    )
    try:
        for document in payload["documents"]:
            canonical_url = str(document["canonical_url"]).strip()
            original_text = str(document["original_text"])
            if not canonical_url or not original_text:
                raise ValueError("company evidence requires canonical URL and original text")
            document_hash = sha256(original_text.encode("utf-8")).hexdigest()
            article_id = _stable_id(
                "company-source-", canonical_url, document_hash
            )
            quotes = document.get("quotes")
            if not isinstance(quotes, list) or not quotes:
                raise ValueError("company evidence document requires exact quote spans")
            for quote in quotes:
                start = int(quote["start"])
                end = int(quote["end"])
                exact_quote = original_text[start:end]
                if (
                    start < 0
                    or end <= start
                    or end > len(original_text)
                    or exact_quote != str(quote["statement"])
                ):
                    raise ValueError("company evidence exact quote span is invalid")
                claim_id = _stable_id(
                    "company-claim-",
                    scope.company_id,
                    scope.product_id,
                    canonical_url,
                    document_hash,
                    start,
                    end,
                    exact_quote,
                )
                candidates.append(
                    EvidenceCandidate(
                        article_id=article_id,
                        canonical_url=canonical_url,
                        source_title=str(document["title"]).strip(),
                        publisher=str(document["publisher"]).strip(),
                        source_type=str(document["source_type"]).strip(),
                        publication_date=(
                            date.fromisoformat(document["publication_date"])
                            if document.get("publication_date")
                            else None
                        ),
                        data_as_of_date=(
                            date.fromisoformat(document["data_as_of_date"])
                            if document.get("data_as_of_date")
                            else None
                        ),
                        location=str(document.get("location", "original text")),
                        quote_start=start,
                        quote_end=end,
                        exact_quote=exact_quote,
                        original_text=original_text,
                        source_mode="original_text",
                        stance=str(quote["stance"]),
                        limitations=str(quote.get("limitations", "")),
                        extraction_model="company-original-text-parser-v1",
                        prompt_version="company-evidence-raw-v1",
                        origin_event_id=_stable_id(
                            "company-event-", canonical_url, document_hash
                        ),
                        evidence_family_id=_stable_id(
                            "company-family-", canonical_url, exact_quote
                        ),
                        claim=ClaimDraft(
                            claim_id=claim_id,
                            node_id=scope.segment_id,
                            claim_type="source_fact",
                            statement=exact_quote,
                            material_field=str(quote["material_field"]),
                            primary_scoring_dimension=quote.get(
                                "primary_scoring_dimension"
                            ),
                            scoring_use=str(quote.get("scoring_use", "context_only")),
                            fact_key="provider-fact-key-must-be-overridden",
                            assessment_scope=assessment_scope,
                            condition_ids=tuple(quote.get("condition_ids", ())),
                            claim_capabilities=tuple(
                                quote.get("claim_capabilities", ())
                            ),
                            source_ambiguity=bool(
                                quote.get("source_ambiguity", False)
                            ),
                            scoring_eligible=bool(
                                quote.get("scoring_eligible", True)
                            ),
                        ),
                    )
                )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("company evidence"):
            raise
        raise ValueError("company original-text evidence payload is invalid") from error
    return tuple(candidates)


def _persist_company_candidate_sources(
    repository,
    candidates,
    *,
    retrieved_at,
    source_identity_resolver=None,
):
    persisted = []
    for candidate in candidates:
        resolved_identity = (
            source_identity_resolver.resolve(candidate.canonical_url)
            if source_identity_resolver is not None
            else resolve_source_identity(candidate.canonical_url)
        )
        identity = repository.save_source_identity(
            resolved_identity,
            canonical_publisher_id=(
                "publisher:" + re.sub(
                    r"[^a-z0-9]+",
                    "-",
                    candidate.publisher.casefold(),
                ).strip("-")
            ),
            canonical_document_id=candidate.article_id,
            origin_event_id=candidate.origin_event_id,
        )
        publication_time = (
            datetime.combine(
                candidate.publication_date,
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            if candidate.publication_date is not None
            else None
        )
        version = repository.save_source_version(
            source_identity_id=identity.source_identity_id,
            retrieved_at=retrieved_at,
            raw_bytes=candidate.original_text.encode("utf-8"),
            normalized_content=candidate.original_text,
            quote_span=(
                candidate.quote_start,
                candidate.quote_end,
                candidate.exact_quote,
            ),
            content_type="text/plain",
            language="und",
            publication_time=publication_time,
            updated_time=None,
        )
        persisted.append(
            replace(
                candidate,
                source_identity_id=identity.source_identity_id,
                source_version_id=version.source_version_id,
            )
        )
    return tuple(persisted)


def _dimension_from_payload(payload):
    basis = payload["bound_basis"]
    return DimensionRatingDraft(
        **{
            **payload,
            "bound_basis": BoundBasis(
                **{
                    **basis,
                    "unresolved_higher_anchors": tuple(
                        basis.get("unresolved_higher_anchors", ())
                    ),
                    "excluded_higher_anchors": tuple(
                        basis.get("excluded_higher_anchors", ())
                    ),
                }
            ),
            "evidence_ids": tuple(payload.get("evidence_ids", ())),
            "missing_material_questions": tuple(
                payload.get("missing_material_questions", ())
            ),
            "anchor_conditions": tuple(
                AnchorConditionResult(
                    **{
                        **item,
                        "evidence_ids": tuple(item.get("evidence_ids", ())),
                        "decisive_claim_ids": tuple(
                            item.get("decisive_claim_ids", ())
                        ),
                        "excluded_higher_anchors": tuple(
                            item.get("excluded_higher_anchors", ())
                        ),
                    }
                )
                for item in payload.get("anchor_conditions", ())
            ),
            "decisive_evidence_ids": tuple(
                payload.get("decisive_evidence_ids", ())
            ),
        }
    )


def _dimension_from_ordinal_output(payload, claim_id_by_evidence_id):
    item = dict(payload)
    item.pop("family", None)
    if "anchor_conditions" in item:
        raise ValueError("company scorer cannot self-report canonical anchor conditions")
    if "rating" in item:
        rating = item.pop("rating")
        item.setdefault("rating_min", rating)
        item.setdefault("rating_max", rating)
    item.setdefault("evidence_ids", ())
    item.setdefault("stale", False)
    item.setdefault("rationale", "")
    item.setdefault("missing_material_questions", ())
    item["anchor_conditions"] = ()
    item.setdefault("decisive_evidence_ids", item["evidence_ids"])
    item.setdefault("anchor_profile", None)
    dimension = _dimension_from_payload(item)
    _validate_ordinal_dimension(dimension)
    if dimension.evidence_state != "supported":
        return dimension
    try:
        decisive_claim_ids = tuple(
            dict.fromkeys(
                claim_id_by_evidence_id[evidence_id]
                for evidence_id in dimension.evidence_ids
            )
        )
    except KeyError as error:
        raise ValueError("company ordinal cites evidence outside the scoring request") from error
    conditions = []
    if dimension.bound_basis.floor_anchor is not None:
        conditions.append(
            AnchorConditionResult(
                condition_id=(
                    f"{dimension.dimension}.anchor_"
                    f"{dimension.bound_basis.floor_anchor}.floor"
                ),
                condition_type="floor",
                evidence_state="supported",
                evidence_ids=dimension.evidence_ids,
                decisive_claim_ids=decisive_claim_ids,
                anchor=dimension.bound_basis.floor_anchor,
            )
        )
    if (
        dimension.bound_basis.ceiling_anchor is not None
        and dimension.rating_max < 4
    ):
        conditions.append(
            AnchorConditionResult(
                condition_id=(
                    f"{dimension.dimension}.anchor_"
                    f"{dimension.bound_basis.ceiling_anchor}.ceiling"
                ),
                condition_type="ceiling",
                evidence_state="supported",
                evidence_ids=dimension.evidence_ids,
                decisive_claim_ids=decisive_claim_ids,
                anchor=dimension.bound_basis.ceiling_anchor,
                excluded_higher_anchors=dimension.bound_basis.excluded_higher_anchors,
            )
        )
    return replace(dimension, anchor_conditions=tuple(conditions))


def _validate_ordinal_dimension(item):
    if (
        isinstance(item.rating_min, bool)
        or isinstance(item.rating_max, bool)
        or not isinstance(item.rating_min, int)
        or not isinstance(item.rating_max, int)
        or not 0 <= item.rating_min <= item.rating_max <= 4
    ):
        raise ValueError("company ordinal rating must be an integer interval within 0..4")
    if item.evidence_state not in {"supported", "conflicted", "unknown"}:
        raise ValueError("company ordinal evidence state is invalid")
    basis = item.bound_basis
    if item.evidence_state == "unknown":
        if (
            item.bound_type != "none"
            or (item.rating_min, item.rating_max) != (0, 4)
            or item.evidence_ids
            or any(
                value is not None
                for value in (basis.floor_anchor, basis.ceiling_anchor, basis.exact_basis)
            )
            or basis.unresolved_higher_anchors
            or basis.excluded_higher_anchors
        ):
            raise ValueError("unknown company ordinal must preserve none + [0,4]")
        return
    if not item.evidence_ids:
        raise ValueError("resolved company ordinal requires evidence IDs")
    if item.bound_type == "exact":
        if item.rating_min != item.rating_max:
            raise ValueError("exact company ordinal requires one rating")
        if (
            basis.floor_anchor != item.rating_min
            or basis.ceiling_anchor != item.rating_max
            or not basis.exact_basis
            or basis.unresolved_higher_anchors
        ):
            raise ValueError("exact company ordinal Bound Basis is invalid")
        return
    if item.bound_type == "lower_bound":
        if (
            item.rating_min >= 4
            or item.rating_max != 4
            or basis.floor_anchor != item.rating_min
            or basis.ceiling_anchor is not None
            or basis.exact_basis is not None
            or set(basis.unresolved_higher_anchors)
            != set(range(item.rating_min + 1, 5))
            or basis.excluded_higher_anchors
        ):
            raise ValueError("lower-bound company ordinal Bound Basis is invalid")
        return
    if item.bound_type == "upper_bound":
        if (
            item.rating_min != 0
            or not 0 < item.rating_max < 4
            or basis.floor_anchor is not None
            or basis.ceiling_anchor != item.rating_max
            or basis.exact_basis is not None
            or basis.unresolved_higher_anchors
            or set(basis.excluded_higher_anchors)
            != set(range(item.rating_max + 1, 5))
        ):
            raise ValueError("upper-bound company ordinal Bound Basis is invalid")
        return
    if item.bound_type == "interval":
        if (
            not 0 < item.rating_min < item.rating_max < 4
            or basis.floor_anchor != item.rating_min
            or basis.ceiling_anchor != item.rating_max
            or basis.exact_basis is not None
            or set(basis.unresolved_higher_anchors)
            != set(range(item.rating_min + 1, item.rating_max + 1))
            or set(basis.excluded_higher_anchors)
            != set(range(item.rating_max + 1, 5))
        ):
            raise ValueError("interval company ordinal Bound Basis is invalid")
        return
    raise ValueError("company ordinal bound type is invalid")


def _validate_model_entity(payload, company, component):
    entity = payload.get("entity")
    if not isinstance(entity, dict) or (
        entity.get("company_id"), entity.get("product_id")
    ) != (company.scope.company_id, company.scope.product_id):
        raise ValueError(f"{component} entity does not match mapped company Scope")


def _company_draft_from_model_payload(payload, company, claims, evidence_cards):
    if "draft" in payload:
        raise ValueError("company scorer must return low-level ordinal model output")
    _validate_model_entity(payload, company, "company scorer")
    try:
        exposure = payload["exposure_summary"]
        by_family = {
            "defensibility": [],
            "replacement": [],
            "earnings": [],
            "ecosystem_compatibility": [],
            "displacement": [],
        }
        claims_by_id = {claim.claim_id: claim for claim in claims}
        claim_id_by_evidence_id = {}
        for card in evidence_cards:
            if card.claim_id not in claims_by_id:
                raise ValueError("company scorer received an unlinked evidence card")
            claim_id_by_evidence_id[card.evidence_id] = card.claim_id
        seen = set()
        for raw_dimension in payload.get("ordinal_outputs", ()):
            family = raw_dimension["family"]
            dimension = _dimension_from_ordinal_output(
                raw_dimension, claim_id_by_evidence_id
            )
            key = (family, dimension.dimension)
            if family not in by_family or key in seen:
                raise ValueError("company scorer returned duplicate or unknown ordinal family")
            seen.add(key)
            by_family[family].append(dimension)
        for singleton in ("ecosystem_compatibility", "displacement"):
            if len(by_family[singleton]) > 1:
                raise ValueError("company scorer returned duplicate singleton ordinal")
        return CompanyAssessmentDraft(
            company_name=company.company_name,
            scope=company.scope,
            roles=company.roles,
            exposure=CompanyExposure(
                narrative=str(exposure["narrative"]),
                operational=str(exposure["operational"]),
                revenue=str(exposure["revenue"]),
                earnings=str(exposure["earnings"]),
                evidence_ids=tuple(exposure["evidence_ids"]),
            ),
            defensibility=tuple(by_family["defensibility"]),
            replacement=tuple(by_family["replacement"]),
            earnings=tuple(by_family["earnings"]),
            replacement_mode=payload.get("replacement_mode"),
            ecosystem_compatibility=(
                by_family["ecosystem_compatibility"][0]
                if by_family["ecosystem_compatibility"]
                else None
            ),
            displacement=(
                by_family["displacement"][0]
                if by_family["displacement"]
                else None
            ),
            milestones=(
                MilestoneAxes(**payload["milestone_observations"])
                if payload.get("milestone_observations")
                else None
            ),
            scope_valid=payload["scope_valid"] is True,
            freshness_valid=payload["freshness_valid"] is True,
            evidence_mapping_error=payload["evidence_mapping_error"] is True,
            gate_results=tuple(
                GateResult(
                    gate_id=str(item["gate_id"]),
                    label=str(item["label"]),
                    required_predicate_ids=tuple(item["required_predicate_ids"]),
                    decisive_evidence_ids=tuple(item["decisive_evidence_ids"]),
                )
                for item in payload.get("gate_proposals", ())
            ),
            red_team_reviews=(),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("company"):
            raise
        raise ValueError("company scorer raw ordinal payload is invalid") from error


def _company_draft_from_critic_payload(payload, company, draft):
    if "draft" in payload:
        raise ValueError("company critic must return a low-level critique")
    _validate_model_entity(payload, company, "company critic")
    if payload.get("admissible") is not True:
        raise ValueError("company critic did not admit the scored draft")
    try:
        reviews = tuple(
            RedTeamReview(
                thesis=str(item["thesis"]),
                counter_evidence_queries=tuple(item["counter_evidence_queries"]),
                unresolved_counterarguments=tuple(
                    item["unresolved_counterarguments"]
                ),
                falsification_conditions=tuple(item["falsification_conditions"]),
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in payload.get("red_team_reviews", ())
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("company critic raw critique payload is invalid") from error
    return replace(draft, red_team_reviews=reviews)


def _require_receipts(values, component):
    if not values or any(not value.strip() for value in values):
        raise ValueError(f"{component} requires provider request receipts")


def _validate_company_candidate(company):
    if not company.company_name.strip() or not company.roles:
        raise ValueError("company mapper candidate identity and roles are required")
    scope = company.scope
    if not all(
        value.strip()
        for value in (
            scope.company_id,
            scope.segment_id,
            scope.product_id,
            scope.customer_or_platform_scope,
            scope.geography,
        )
    ) or scope.time_horizon_months <= 0:
        raise ValueError("company mapper candidate requires a complete Scope")


def _validate_mapping_scopes(companies, graph, stage3_result):
    assessment_segments = {
        assessment.segment_id
        for assessment in getattr(stage3_result, "assessments", ())
        if getattr(assessment, "segment_id", None)
    }
    nodes_by_id = {
        node.node_id: node
        for node in getattr(graph, "nodes", ())
        if getattr(node, "node_id", None)
    }
    for company in companies:
        scope = company.scope
        if assessment_segments and scope.segment_id not in assessment_segments:
            raise ValueError("mapped company Scope is outside Stage 3 assessments")
        if nodes_by_id:
            node = nodes_by_id.get(scope.segment_id)
            if node is None:
                raise ValueError("mapped company Scope is outside the supply-chain graph")
            expected_product_id = getattr(node, "product_anchor_id", None)
            if expected_product_id and scope.product_id != expected_product_id:
                raise ValueError("mapped company Scope uses the wrong product anchor")


def _scope_matches(assessment_scope, company_scope):
    return bool(
        assessment_scope
        and assessment_scope.company_id == company_scope.company_id
        and assessment_scope.product_id == company_scope.product_id
        and assessment_scope.segment_id == company_scope.segment_id
        and assessment_scope.customer_or_platform_scope
        == company_scope.customer_or_platform_scope
        and assessment_scope.geography == company_scope.geography
        and assessment_scope.time_horizon_months
        == company_scope.time_horizon_months
        and assessment_scope.as_of_date == company_scope.as_of_date
    )


def _validate_draft_identity(draft, company):
    if (
        draft.company_name != company.company_name
        or draft.scope != company.scope
        or tuple(draft.roles) != tuple(company.roles)
    ):
        raise ValueError("company scorer or critic changed the mapped company identity")


def _validate_draft_evidence(draft, acquired_evidence_ids):
    referenced = set(draft.exposure.evidence_ids)
    for dimension in (
        *draft.defensibility,
        *draft.replacement,
        *draft.earnings,
        *((draft.ecosystem_compatibility,) if draft.ecosystem_compatibility else ()),
        *((draft.displacement,) if draft.displacement else ()),
    ):
        referenced.update(dimension.evidence_ids)
        referenced.update(dimension.decisive_evidence_ids)
    for gate in draft.gate_results:
        referenced.update(gate.decisive_evidence_ids)
    for review in draft.red_team_reviews:
        referenced.update(review.evidence_ids)
    if not referenced or not referenced <= acquired_evidence_ids:
        raise ValueError("company draft evidence was not acquired for the company")
