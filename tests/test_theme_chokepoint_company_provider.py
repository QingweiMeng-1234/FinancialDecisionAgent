from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    ClaimDraft,
    CompanyAssessmentDraft,
    CompanyCandidate,
    CompanyCriticResult,
    CompanyExposure,
    CompanyMappingBatch,
    CompanyRawProviderResponse,
    CompanyScope,
    CompanyScoringResult,
    EvidenceAcquisitionBatch,
    EvidenceCandidate,
)
from event_collector.theme_chokepoint.providers.company import (
    CompanyBusinessFactAssertionWriter,
    EvidenceBoundCompanyResearcher,
    ProviderCompanyOrdinalScorer,
    MappedChallengerSetBuilder,
    ProviderChallengerSetBuilder,
    _challenger_sets_from_provider_payload,
    build_default_company_researcher,
)
from event_collector.theme_chokepoint.providers import (
    build_default_provider_composition,
)
from event_collector.theme_chokepoint.providers.critic import EvidenceBoundSegmentCritic
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceResolutionRawResponse,
)


def _candidate(scope: CompanyScope) -> EvidenceCandidate:
    quote = "ControlledCo supplies qualified UPS modules."
    assessment_scope = AssessmentScope(
        company_id=scope.company_id,
        product_id=scope.product_id,
        segment_id=scope.segment_id,
        customer_or_platform_scope=scope.customer_or_platform_scope,
        geography=scope.geography,
        time_horizon_months=scope.time_horizon_months,
        as_of_date=scope.as_of_date,
    )
    return EvidenceCandidate(
        article_id="company-article-1",
        canonical_url="https://example.com/company-article-1",
        source_title="Controlled company filing",
        publisher="Example",
        source_type="company_filing",
        publication_date=date(2026, 8, 1),
        data_as_of_date=date(2026, 6, 30),
        location="segment note",
        quote_start=0,
        quote_end=len(quote),
        exact_quote=quote,
        original_text=quote,
        source_mode="original_text",
        stance="supports",
        limitations="controlled transport fixture",
        extraction_model="controlled-extractor-v1",
        prompt_version="controlled-prompt-v1",
        origin_event_id="company-event-1",
        evidence_family_id="company-family-1",
        claim=ClaimDraft(
            claim_id="company-claim-1",
            node_id=scope.segment_id,
            claim_type="source_fact",
            statement=quote,
            material_field="company_exposure",
            primary_scoring_dimension=None,
            scoring_use="context_only",
            fact_key="provider-fact-key-must-be-overridden",
            assessment_scope=assessment_scope,
            scoring_eligible=True,
        ),
    )


def _scope() -> CompanyScope:
    return CompanyScope(
        company_id="controlledco",
        segment_id="segment-ups",
        product_id="ups-x",
        customer_or_platform_scope="global hyperscale data centers",
        geography="global",
        time_horizon_months=24,
        as_of_date=date(2026, 8, 16),
    )


def _raw_response(payload, trace_id, *, cost_usd=0.0):
    return CompanyRawProviderResponse(
        provider="controlled-company-provider",
        provider_trace_id=trace_id,
        http_status=200,
        raw_body=json.dumps(
            payload,
            default=lambda value: value.isoformat(),
            sort_keys=True,
        ).encode("utf-8"),
        retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        cost_usd=cost_usd,
    )


def _independent_company_clients(transport_type):
    discovery_transport = transport_type()
    evidence_transport = transport_type()
    scoring_transport = transport_type()
    critic_transport = transport_type()
    return {
        "discovery_client": SimpleNamespace(
            map_companies=discovery_transport.map_companies
        ),
        "evidence_client": SimpleNamespace(
            acquire_company_evidence=evidence_transport.acquire_company_evidence
        ),
        "scoring_model_client": SimpleNamespace(
            score_company=scoring_transport.score_company
        ),
        "critic_model_client": SimpleNamespace(
            review_company=critic_transport.review_company
        ),
    }


def _controlled_source_identity_resolver():
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
                provider="controlled-company-source-resolver",
                provider_trace_id="controlled-company-source-resolution-1",
                http_status=200,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            )

    return CanonicalSourceIdentityResolver(ResolutionClient())


def _raw_discovery_payload():
    scope = _scope()
    return {
        "companies": [
            {
                "name": "ControlledCo",
                "company_id": scope.company_id,
                "roles": ["equipment_enabler"],
                "segment_id": scope.segment_id,
                "product_id": scope.product_id,
                "customer_or_platform_scope": scope.customer_or_platform_scope,
                "geography": scope.geography,
                "time_horizon_months": scope.time_horizon_months,
                "as_of_date": scope.as_of_date.isoformat(),
            }
        ]
    }


def _raw_evidence_payload():
    candidate = _candidate(_scope())
    return {
        "documents": [
            {
                "canonical_url": candidate.canonical_url,
                "title": candidate.source_title,
                "publisher": candidate.publisher,
                "source_type": candidate.source_type,
                "publication_date": candidate.publication_date.isoformat(),
                "data_as_of_date": candidate.data_as_of_date.isoformat(),
                "location": candidate.location,
                "original_text": candidate.original_text,
                "quotes": [
                    {
                        "start": candidate.quote_start,
                        "end": candidate.quote_end,
                        "statement": candidate.exact_quote,
                        "material_field": candidate.claim.material_field,
                        "stance": candidate.stance,
                        "limitations": candidate.limitations,
                        "scoring_use": candidate.claim.scoring_use,
                    }
                ],
            }
        ]
    }


def _raw_scoring_payload(company, evidence_cards):
    return {
        "entity": {
            "company_id": company.scope.company_id,
            "product_id": company.scope.product_id,
        },
        "exposure_summary": {
            "narrative": "controlled company mapping",
            "operational": "qualified UPS module exposure",
            "revenue": "revenue remains unknown",
            "earnings": "earnings remains unknown",
            "evidence_ids": [evidence_cards[0].evidence_id],
        },
        "ordinal_outputs": [],
        "scope_valid": True,
        "freshness_valid": True,
        "evidence_mapping_error": False,
        "replacement_mode": None,
        "milestone_observations": None,
        "gate_proposals": [],
    }


def _raw_critic_payload(company):
    return {
        "entity": {
            "company_id": company.scope.company_id,
            "product_id": company.scope.product_id,
        },
        "admissible": True,
        "red_team_reviews": [],
    }


class Mapper:
    def map(self, *, run, graph, stage3_result):
        return CompanyMappingBatch(
            candidates=(
                CompanyCandidate(
                    company_name="ControlledCo",
                    scope=_scope(),
                    roles=("equipment_enabler",),
                ),
            ),
            request_receipt_ids=("mapper-request-1",),
        )


class Acquirer:
    def acquire(self, *, run, graph, stage3_result, company):
        return EvidenceAcquisitionBatch(
            candidates=(_candidate(company.scope),),
            cost_usd=0.25,
            request_receipt_ids=("company-search-request-1",),
        )


class Scorer:
    def __init__(self, fictional_evidence=False):
        self.fictional_evidence = fictional_evidence

    def score(self, *, run, graph, stage3_result, company, claims, evidence_cards):
        evidence_id = (
            "fictional-evidence" if self.fictional_evidence else evidence_cards[0].evidence_id
        )
        draft = CompanyAssessmentDraft(
            company_name=company.company_name,
            scope=company.scope,
            roles=company.roles,
            exposure=CompanyExposure(
                narrative="controlled company mapping",
                operational="qualified UPS module exposure",
                revenue="revenue remains unknown",
                earnings="earnings remains unknown",
                evidence_ids=(evidence_id,),
            ),
            defensibility=(),
            replacement=(),
            earnings=(),
            replacement_mode=None,
            ecosystem_compatibility=None,
            displacement=None,
            milestones=None,
            scope_valid=True,
            freshness_valid=True,
            evidence_mapping_error=False,
            gate_results=(),
            red_team_reviews=(),
        )
        return CompanyScoringResult(draft=draft, request_receipt_id="company-scorer-1")


class Critic:
    def review(
        self, *, run, graph, stage3_result, company, draft, claims, evidence_cards
    ):
        return CompanyCriticResult(draft=draft, request_receipt_id="company-critic-1")


def test_company_researcher_executes_mapper_acquirer_scorer_and_independent_critic():
    """SELECT INVARIANT: the production company chain emits persisted evidence and receipts."""
    researcher = EvidenceBoundCompanyResearcher(Mapper(), Acquirer(), Scorer(), Critic())

    result = researcher.research(
        SimpleNamespace(request=SimpleNamespace(max_cost_usd=5.0)),
        SimpleNamespace(nodes=()),
        SimpleNamespace(),
    )

    assert len(result.drafts) == 1
    assert len(result.claims) == len(result.evidence_cards) == len(result.source_snapshots) == 1
    assert result.claims[0].fact_key.startswith("atomic_fact_")
    assert result.evidence_cards[0].fact_key == result.claims[0].fact_key
    assert result.provider_request_receipt_ids == (
        "mapper-request-1",
        "company-search-request-1",
        "company-scorer-1",
        "company-critic-1",
    )
    receipt = result.chain_receipts[0]
    assert receipt.company_id == "controlledco"
    assert receipt.acquired_evidence_ids == (result.evidence_cards[0].evidence_id,)
    assert receipt.scorer_request_receipt_id == "company-scorer-1"
    assert receipt.critic_request_receipt_id == "company-critic-1"


def test_v15_mapped_challenger_builder_emits_typed_incomplete_set():
    """SELECT INVARIANT: default discovery never returns an untyped or silently empty set."""
    owner = CompanyCandidate(
        company_name="OwnerCo",
        scope=replace(_scope(), company_id="ownerco", product_id="ups-owner"),
        roles=("bottleneck_owner",),
    )
    challenger = CompanyCandidate(
        company_name="ChallengerCo",
        scope=replace(_scope(), company_id="challengerco", product_id="ups-alt"),
        roles=("substitute_supplier",),
    )

    sets = MappedChallengerSetBuilder().build(
        mapping=CompanyMappingBatch(
            candidates=(owner, challenger),
            request_receipt_ids=("mapping-reconciliation-1",),
        ),
        completed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    assert len(sets) == 1
    challenger_set = sets[0]
    assert isinstance(challenger_set.assessment_scope, AssessmentScope)
    assert challenger_set.assessment_scope.company_id is None
    assert challenger_set.included_company_ids == ()
    assert len(challenger_set.included_assessment_ids) == 1
    assert challenger_set.coverage_gate is False
    assert challenger_set.counter_search_receipt is None


def test_default_v15_company_composition_installs_challenger_builder(tmp_path):
    """SELECT INVARIANT: the v1.5 production company chain cannot return fixed empty sets."""
    class Transport:
        def map_companies(self, **_kwargs):
            raise AssertionError("not executed")

        def acquire_company_evidence(self, **_kwargs):
            raise AssertionError("not executed")

        def score_company(self, **_kwargs):
            raise AssertionError("not executed")

        def review_company(self, **_kwargs):
            raise AssertionError("not executed")

        def build_challenger_sets(self, **_kwargs):
            raise AssertionError("not executed")

    clients = _independent_company_clients(Transport)
    clients["discovery_client"].build_challenger_sets = (
        Transport().build_challenger_sets
    )
    researcher = build_default_company_researcher(
        **clients,
        repository=ThemeChokepointRepository(tmp_path / "theme.db"),
        enable_v15_company_chain=True,
    )

    assert isinstance(researcher.challenger_set_builder, ProviderChallengerSetBuilder)


def test_provider_challenger_builder_parser_preserves_two_round_execution_lineage():
    """SELECT INVARIANT: provider search becomes a typed set with route receipts, not text."""
    completed_at = "2026-08-20T08:00:00+00:00"
    scope = _scope()
    sets = _challenger_sets_from_provider_payload(
        {
            "challenger_sets": [
                {
                    "challenger_set_id": "set-1",
                    "segment_id": scope.segment_id,
                    "assessment_scope": {
                        "company_id": None,
                        "product_id": scope.product_id,
                        "segment_id": scope.segment_id,
                        "customer_or_platform_scope": scope.customer_or_platform_scope,
                        "geography": scope.geography,
                        "time_horizon_months": scope.time_horizon_months,
                        "as_of_date": scope.as_of_date.isoformat(),
                    },
                    "search_protocol_version": "challenger-search-v1.5",
                    "included_assessment_ids": ["assessment-1"],
                    "excluded_candidates_with_reason": [["other", "not qualified"]],
                    "search_completed_at": completed_at,
                    "as_of_date": scope.as_of_date.isoformat(),
                    "coverage_gate": True,
                    "counter_search_receipt": {
                        "challenger_set_id": "set-1",
                        "protocol_version": "challenger-search-v1.5",
                        "query_log_ids": ["q1"],
                        "max_queries": 10,
                        "max_time_seconds": 300,
                        "max_cost_usd": 2.0,
                        "stop_reason": "protocol_complete",
                        "route_findings": [
                            {
                                "route_id": "product-alt",
                                "status": "supported_candidate",
                                "query_log_ids": ["q1"],
                                "evidence_ids": ["e1"],
                                "finding": "Qualified alternative found.",
                                "query": "qualified alternatives",
                                "provider_request_receipt_id": "route-request-1",
                                "executed_at": completed_at,
                                "cost_usd": 0.5,
                            }
                        ],
                        "negative_findings": [],
                        "completed_at": completed_at,
                        "provider_request_receipt_ids": ["route-request-1"],
                        "cost_usd_spent": 0.5,
                        "discovery_rounds_without_new_material_routes": 2,
                    },
                }
            ]
        }
    )

    assert sets[0].coverage_gate is True
    assert sets[0].included_assessment_ids == ("assessment-1",)
    assert sets[0].counter_search_receipt.provider_request_receipt_ids == (
        "route-request-1",
    )
    assert (
        sets[0].counter_search_receipt.discovery_rounds_without_new_material_routes
        == 2
    )


def test_company_researcher_rejects_scorer_evidence_not_acquired_for_company():
    """SELECT INVARIANT: a scorer cannot invent evidence IDs outside acquisition lineage."""
    researcher = EvidenceBoundCompanyResearcher(
        Mapper(), Acquirer(), Scorer(fictional_evidence=True), Critic()
    )

    with pytest.raises(ValueError, match="not acquired for the company"):
        researcher.research(
            SimpleNamespace(request=SimpleNamespace(max_cost_usd=5.0)),
            SimpleNamespace(nodes=()),
            SimpleNamespace(),
        )


def test_provider_company_scorer_rejects_domain_object_from_transport():
    """SELECT INVARIANT: low-level model transport returns raw bytes, never final scores."""
    class DomainObjectTransport:
        def score_company(self, **_kwargs):
            return CompanyScoringResult(
                draft=Scorer().score(
                    run=None,
                    graph=None,
                    stage3_result=None,
                    company=SimpleNamespace(
                        company_name="ControlledCo",
                        scope=_scope(),
                        roles=("equipment_enabler",),
                    ),
                    claims=(),
                    evidence_cards=(SimpleNamespace(evidence_id="evidence-1"),),
                ).draft,
                request_receipt_id="forged-domain-receipt",
            )

    scorer = ProviderCompanyOrdinalScorer(DomainObjectTransport())
    with pytest.raises(ValueError, match="raw model response"):
        scorer.score(
            run=SimpleNamespace(),
            graph=SimpleNamespace(),
            stage3_result=SimpleNamespace(),
            company=SimpleNamespace(),
            claims=(),
            evidence_cards=(),
        )


def test_default_company_composition_only_injects_provider_transport(tmp_path):
    """SELECT INVARIANT: the public runtime composes all four concrete company adapters."""
    class Transport:
        def map_companies(self, **kwargs):
            kwargs.pop("request_record_id")
            return _raw_response(
                _raw_discovery_payload(),
                "mapper-request-1",
            )

        def acquire_company_evidence(self, **kwargs):
            kwargs.pop("request_record_id")
            return _raw_response(
                _raw_evidence_payload(),
                "company-search-request-1",
                cost_usd=0.25,
            )

        def score_company(self, **kwargs):
            kwargs.pop("request_record_id")
            return _raw_response(
                _raw_scoring_payload(kwargs["company"], kwargs["evidence_cards"]),
                "company-scorer-1",
            )

        def review_company(self, **kwargs):
            kwargs.pop("request_record_id")
            return _raw_response(
                _raw_critic_payload(kwargs["company"]),
                "company-critic-1",
            )

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=ThemeChokepointRepository(tmp_path / "theme.db"),
    )
    result = researcher.research(
        SimpleNamespace(
            request=SimpleNamespace(
                run_id="company-composition-run",
                max_cost_usd=5.0,
            )
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(),
    )

    assert isinstance(researcher, EvidenceBoundCompanyResearcher)
    assert result.chain_receipts[0].mapper_request_receipt_ids[0].startswith(
        "company-discovery-reconciliation-"
    )


def test_default_company_composition_rejects_distinct_wrappers_over_one_facade(tmp_path):
    """SELECT INVARIANT: wrapper identity cannot hide one shared low-level transport."""

    class SharedFacade:
        def map_companies(self, **_kwargs):
            raise AssertionError("not reached")

        def acquire_company_evidence(self, **_kwargs):
            raise AssertionError("not reached")

        def score_company(self, **_kwargs):
            raise AssertionError("not reached")

        def review_company(self, **_kwargs):
            raise AssertionError("not reached")

    facade = SharedFacade()
    with pytest.raises(ValueError, match="shared low-level transport facade"):
        build_default_company_researcher(
            discovery_client=SimpleNamespace(map_companies=facade.map_companies),
            evidence_client=SimpleNamespace(
                acquire_company_evidence=facade.acquire_company_evidence
            ),
            scoring_model_client=SimpleNamespace(score_company=facade.score_company),
            critic_model_client=SimpleNamespace(review_company=facade.review_company),
            repository=ThemeChokepointRepository(tmp_path / "theme.db"),
        )


def test_production_company_roles_persist_request_before_io_and_reconcile_each_chain(
    tmp_path,
):
    """SELECT INVARIANT: every production role persists four durable execution records."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")

    class Transport:
        def _assert_request(self, role, request_record_id):
            request = repository.get_company_provider_request(
                role=role,
                request_record_id=request_record_id,
            )
            assert request.run_id == "company-persistence-run"

        def map_companies(self, *, request_record_id, **kwargs):
            self._assert_request("discovery", request_record_id)
            return _raw_response(
                _raw_discovery_payload(),
                "company-discovery-trace-1",
            )

        def acquire_company_evidence(self, *, request_record_id, **kwargs):
            self._assert_request("evidence", request_record_id)
            return _raw_response(
                _raw_evidence_payload(),
                "company-evidence-trace-1",
                cost_usd=0.25,
            )

        def score_company(self, *, request_record_id, **kwargs):
            self._assert_request("scoring", request_record_id)
            return _raw_response(
                _raw_scoring_payload(kwargs["company"], kwargs["evidence_cards"]),
                "company-scoring-trace-1",
            )

        def review_company(self, *, request_record_id, **kwargs):
            self._assert_request("critic", request_record_id)
            return _raw_response(
                _raw_critic_payload(kwargs["company"]),
                "company-critic-trace-1",
            )

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
    )

    result = researcher.research(
        SimpleNamespace(
            request=SimpleNamespace(
                run_id="company-persistence-run",
                max_cost_usd=5.0,
            )
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(run_id="company-persistence-run"),
    )

    reconciliations = repository.list_company_provider_reconciliations(
        "company-persistence-run"
    )
    assert {record.role for record in reconciliations} == {
        "discovery",
        "evidence",
        "scoring",
        "critic",
    }
    assert len(reconciliations) == 4
    assert {
        record.receipt_id for record in reconciliations
    } == set(result.provider_request_receipt_ids)


def test_company_reconciliation_consumer_reloads_and_rejects_tampered_raw(tmp_path):
    """SELECT INVARIANT: durable receipts fail closed when any four-part row drifts."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(_raw_discovery_payload(), "tamper-discovery-trace")

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(_raw_evidence_payload(), "tamper-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                _raw_scoring_payload(company, evidence_cards), "tamper-scoring-trace"
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(_raw_critic_payload(company), "tamper-critic-trace")

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
    )
    researcher.research(
        SimpleNamespace(
            request=SimpleNamespace(run_id="company-tamper-run", max_cost_usd=5.0)
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(run_id="company-tamper-run"),
    )
    with repository._connect() as connection:
        connection.execute(
            """UPDATE theme_chokepoint_company_scoring_raw_responses
               SET raw_body = ? WHERE run_id = ?""",
            (b'{"tampered":true}', "company-tamper-run"),
        )

    with pytest.raises(ValueError, match="company provider.*reconciliation"):
        repository.list_company_provider_reconciliations("company-tamper-run")


def test_controlled_raw_fixture_is_parsed_into_company_domain_objects(tmp_path):
    """SELECT INVARIANT: fixtures stop at raw discovery/text/model response boundaries."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    quote = "ControlledCo supplies qualified UPS modules."

    class RawBoundaryTransport:
        def map_companies(self, **_kwargs):
            return _raw_response(
                {
                    "companies": [
                        {
                            "name": "ControlledCo",
                            "company_id": "controlledco",
                            "roles": ["equipment_enabler"],
                            "segment_id": "segment-ups",
                            "product_id": "ups-x",
                            "customer_or_platform_scope": (
                                "global hyperscale data centers"
                            ),
                            "geography": "global",
                            "time_horizon_months": 24,
                            "as_of_date": "2026-08-16",
                        }
                    ]
                },
                "raw-discovery-trace-1",
            )

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(
                {
                    "documents": [
                        {
                            "canonical_url": "https://example.com/company-article-1",
                            "title": "Controlled company filing",
                            "publisher": "Example",
                            "source_type": "company_filing",
                            "publication_date": "2026-08-01",
                            "data_as_of_date": "2026-06-30",
                            "location": "segment note",
                            "original_text": quote,
                            "quotes": [
                                {
                                    "start": 0,
                                    "end": len(quote),
                                    "statement": quote,
                                    "material_field": "company_exposure",
                                    "stance": "supports",
                                    "limitations": "controlled raw fixture",
                                    "scoring_use": "context_only",
                                }
                            ],
                        }
                    ]
                },
                "raw-evidence-trace-1",
                cost_usd=0.1,
            )

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                {
                    "entity": {
                        "company_id": company.scope.company_id,
                        "product_id": company.scope.product_id,
                    },
                    "exposure_summary": {
                        "narrative": "controlled company mapping",
                        "operational": "qualified UPS module exposure",
                        "revenue": "revenue remains unknown",
                        "earnings": "earnings remains unknown",
                        "evidence_ids": [evidence_cards[0].evidence_id],
                    },
                    "ordinal_outputs": [],
                    "scope_valid": True,
                    "freshness_valid": True,
                    "evidence_mapping_error": False,
                    "replacement_mode": None,
                    "milestone_observations": None,
                    "gate_proposals": [],
                },
                "raw-scoring-trace-1",
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(
                {
                    "entity": {
                        "company_id": company.scope.company_id,
                        "product_id": company.scope.product_id,
                    },
                    "admissible": True,
                    "red_team_reviews": [],
                },
                "raw-critic-trace-1",
            )

    researcher = build_default_company_researcher(
        **_independent_company_clients(RawBoundaryTransport),
        repository=repository,
    )

    result = researcher.research(
        SimpleNamespace(
            request=SimpleNamespace(run_id="raw-boundary-run", max_cost_usd=5.0)
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(run_id="raw-boundary-run"),
    )

    assert result.drafts[0].company_name == "ControlledCo"
    assert result.evidence_cards[0].exact_quote == quote
    assert result.drafts[0].exposure.evidence_ids == (
        result.evidence_cards[0].evidence_id,
    )
    assert len(repository.list_company_provider_reconciliations("raw-boundary-run")) == 4


def test_verified_capability_evidence_requires_production_assertion_writer(tmp_path):
    """SELECT INVARIANT: capability evidence cannot bypass the production verifier chain."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    evidence_payload = _raw_evidence_payload()
    quote = (
        "ControlledCo reported USD 25 million revenue for UPS-X "
        "in Q1 2026 under GAAP."
    )
    document = evidence_payload["documents"][0]
    document["original_text"] = quote
    document["quotes"] = [
        {
            "start": 0,
            "end": len(quote),
            "statement": quote,
            "material_field": "revenue_materiality",
            "stance": "supports",
            "limitations": "controlled raw fixture",
            "scoring_use": "context_only",
            "condition_ids": ["capability.accounting_revenue_confirmed.v1.4"],
            "claim_capabilities": ["accounting_revenue_confirmed"],
        }
    ]

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(_raw_discovery_payload(), "writer-discovery-trace")

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(evidence_payload, "writer-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                _raw_scoring_payload(company, evidence_cards),
                "writer-scoring-trace",
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(_raw_critic_payload(company), "writer-critic-trace")

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
    )

    with pytest.raises(ValueError, match="production assertion writer"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(
                    run_id="assertion-writer-required-run",
                    max_cost_usd=5.0,
                )
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(run_id="assertion-writer-required-run"),
        )


def test_production_assertion_writer_generates_proposal_and_invokes_fact_verifier(
    tmp_path,
):
    """SELECT INVARIANT: company evidence proposals use the independent fact verifier."""
    from event_collector.theme_chokepoint.contracts import (
        BusinessFactVerificationRawProviderResponse,
    )
    from event_collector.theme_chokepoint.providers.fact_verifier import (
        EvidenceBoundBusinessFactVerifier,
    )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    governance_sha256 = "a" * 64
    evidence_payload = _raw_evidence_payload()
    quote = (
        "ControlledCo reported USD 25 million revenue for UPS-X "
        "in Q1 2026 under GAAP."
    )
    document = evidence_payload["documents"][0]
    document["original_text"] = quote
    document["quotes"] = [
        {
            "start": 0,
            "end": len(quote),
            "statement": quote,
            "material_field": "revenue_materiality",
            "stance": "supports",
            "limitations": "controlled raw fixture",
            "scoring_use": "context_only",
            "condition_ids": ["capability.accounting_revenue_confirmed.v1.4"],
            "claim_capabilities": ["accounting_revenue_confirmed"],
        }
    ]

    class RawVerifier:
        calls = 0

        def verify_fact(
            self,
            *,
            request_record_id,
            assertion,
            assertion_sha256,
            source_context,
            source_context_sha256,
            **_kwargs,
        ):
            self.calls += 1
            assert assertion.verification is None
            persisted = repository.get_business_fact_verification_request(
                request_record_id
            )
            assert persisted.source_context == source_context
            assert persisted.source_context_sha256 == source_context_sha256
            assert source_context.original_document_text == quote
            assert source_context.original_document_bytes == quote.encode("utf-8")
            assert source_context.exact_quote == quote
            assert source_context.issuer_company_id == "controlledco"
            assert source_context.product_id == "ups-x"
            assert source_context.fiscal_period_id == "Q12026"
            assert source_context.accounting_metric == "revenue"
            assert source_context.source_identity_policy_id
            assert source_context.source_resolver_receipt_id
            return BusinessFactVerificationRawProviderResponse(
                provider_trace_id="company-fact-verifier-trace-1",
                http_status=200,
                raw_body=json.dumps(
                    {
                        "assertion_id": assertion.assertion_id,
                        "assertion_sha256": assertion_sha256,
                        "decision": "verified",
                    },
                    sort_keys=True,
                ).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
                cost_usd=0.01,
            )

    raw_verifier = RawVerifier()
    verifier = EvidenceBoundBusinessFactVerifier(
        repository,
        raw_verifier,
        verifier_execution_id="independent-company-fact-verifier",
        governance_bundle_sha256=governance_sha256,
    )
    writer = CompanyBusinessFactAssertionWriter(
        verifier,
        producer_execution_id="company-evidence-assertion-writer",
    )

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(_raw_discovery_payload(), "verified-discovery-trace")

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(evidence_payload, "verified-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                _raw_scoring_payload(company, evidence_cards),
                "verified-scoring-trace",
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(_raw_critic_payload(company), "verified-critic-trace")

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
        assertion_writer=writer,
        source_identity_resolver=_controlled_source_identity_resolver(),
    )
    result = researcher.research(
        SimpleNamespace(
            request=SimpleNamespace(
                run_id="verified-assertion-writer-run",
                max_cost_usd=5.0,
            )
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(run_id="verified-assertion-writer-run"),
    )

    assertion = result.evidence_cards[0].business_fact_assertions[0]
    assert raw_verifier.calls == 1
    assert assertion.canonical_predicate_id == (
        "capability.accounting_revenue_confirmed.v1.4"
    )
    assert repository.business_fact_assertion_is_verified(
        run_id="verified-assertion-writer-run",
        assertion=assertion,
        governance_bundle_sha256=governance_sha256,
    )


def test_accounting_absence_text_cannot_gain_repository_verification(tmp_path):
    """SELECT INVARIANT: accounting absence text cannot become verified revenue."""
    from event_collector.theme_chokepoint.contracts import (
        BusinessFactVerificationRawProviderResponse,
    )
    from event_collector.theme_chokepoint.providers.fact_verifier import (
        EvidenceBoundBusinessFactVerifier,
    )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    governance_sha256 = "b" * 64
    run_id = "accounting-absence-bypass-run"
    evidence_payload = _raw_evidence_payload()
    quote = (
        "ControlledCo revenue is absent at USD 25 million for UPS-X "
        "in Q1 2026 under GAAP."
    )
    document = evidence_payload["documents"][0]
    document["original_text"] = quote
    document["quotes"] = [
        {
            "start": 0,
            "end": len(quote),
            "statement": quote,
            "material_field": "revenue_materiality",
            "stance": "supports",
            "limitations": "controlled raw fixture",
            "scoring_use": "context_only",
            "condition_ids": ["capability.accounting_revenue_confirmed.v1.4"],
            "claim_capabilities": ["accounting_revenue_confirmed"],
        }
    ]

    class RawVerifier:
        calls = 0

        def verify_fact(self, *, assertion, assertion_sha256, **_kwargs):
            self.calls += 1
            return BusinessFactVerificationRawProviderResponse(
                provider_trace_id="accounting-absence-verifier-trace",
                http_status=200,
                raw_body=json.dumps(
                    {
                        "assertion_id": assertion.assertion_id,
                        "assertion_sha256": assertion_sha256,
                        "decision": "verified",
                    },
                    sort_keys=True,
                ).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
                cost_usd=0.01,
            )

    raw_verifier = RawVerifier()
    verifier = EvidenceBoundBusinessFactVerifier(
        repository,
        raw_verifier,
        verifier_execution_id="independent-accounting-absence-verifier",
        governance_bundle_sha256=governance_sha256,
    )

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(_raw_discovery_payload(), "absence-discovery-trace")

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(evidence_payload, "absence-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                _raw_scoring_payload(company, evidence_cards),
                "absence-scoring-trace",
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(_raw_critic_payload(company), "absence-critic-trace")

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
        assertion_writer=CompanyBusinessFactAssertionWriter(
            verifier,
            producer_execution_id="accounting-absence-writer",
        ),
        source_identity_resolver=_controlled_source_identity_resolver(),
    )

    with pytest.raises(ValueError, match="not affirmative"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(run_id=run_id, max_cost_usd=5.0)
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(run_id=run_id),
        )

    assert raw_verifier.calls == 0
    assert repository.export_business_fact_verification_ledger(
        run_id=run_id,
        evidence_pack_id=f"theme-run:{run_id}",
        governance_bundle_sha256=governance_sha256,
    )["reconciliations"] == []


def test_production_assertion_writer_rejects_unconfirmed_fact_before_verifier():
    """Regression: negative/unconfirmed text cannot become a verifier proposal."""
    negative_quote = "ControlledCo production use remains unconfirmed for UPS-X."
    base = _candidate(_scope())
    candidate = replace(
        base,
        quote_end=len(negative_quote),
        exact_quote=negative_quote,
        original_text=negative_quote,
        claim=replace(
            base.claim,
            statement=negative_quote,
            condition_ids=("capability.production_use_confirmed.v1.4",),
            claim_capabilities=("production_use_confirmed",),
        ),
    )

    class NegativeAcquirer:
        def acquire(self, *, run, graph, stage3_result, company):
            return EvidenceAcquisitionBatch(
                candidates=(candidate,),
                cost_usd=0.0,
                request_receipt_ids=("negative-evidence-receipt",),
            )

    class RecordingVerifier:
        calls = 0

        def verify(self, **_kwargs):
            self.calls += 1

    verifier = RecordingVerifier()
    researcher = EvidenceBoundCompanyResearcher(
        Mapper(),
        NegativeAcquirer(),
        Scorer(),
        Critic(),
        assertion_writer=CompanyBusinessFactAssertionWriter(
            verifier,
            producer_execution_id="company-negative-proposal-writer",
        ),
    )

    with pytest.raises(ValueError, match="not affirmative"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(
                    run_id="negative-assertion-writer-run",
                    max_cost_usd=5.0,
                )
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(),
        )
    assert verifier.calls == 0


def test_production_accounting_writer_rejects_explicit_negation_and_source_substring():
    """SELECT INVARIANT: negation and fake official source names fail before verifier I/O."""
    quote = "ControlledCo has not confirmed USD 25 million revenue in Q1 2026 GAAP."
    base = _candidate(_scope())
    candidate = replace(
        base,
        source_type="not_a_company_filing",
        quote_end=len(quote),
        exact_quote=quote,
        original_text=quote,
        claim=replace(
            base.claim,
            statement=quote,
            material_field="revenue_materiality",
            condition_ids=("capability.accounting_revenue_confirmed.v1.4",),
            claim_capabilities=("accounting_revenue_confirmed",),
        ),
    )

    class NegatedAccountingAcquirer:
        def acquire(self, *, run, graph, stage3_result, company):
            return EvidenceAcquisitionBatch(
                candidates=(candidate,),
                cost_usd=0.0,
                request_receipt_ids=("negated-accounting-evidence",),
            )

    class RecordingVerifier:
        calls = 0

        def verify(self, **_kwargs):
            self.calls += 1

    verifier = RecordingVerifier()
    researcher = EvidenceBoundCompanyResearcher(
        Mapper(),
        NegatedAccountingAcquirer(),
        Scorer(),
        Critic(),
        assertion_writer=CompanyBusinessFactAssertionWriter(
            verifier,
            producer_execution_id="negated-accounting-writer",
        ),
    )

    with pytest.raises(ValueError, match="not affirmative|official accounting source"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(
                    run_id="negated-accounting-run",
                    max_cost_usd=5.0,
                )
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(),
        )
    assert verifier.calls == 0


def test_production_accounting_writer_rejects_positive_fact_from_spoofed_source_type():
    """SELECT INVARIANT: official accounting source matching is an exact enum check."""
    quote = "ControlledCo reported USD 25 million revenue in Q1 2026 under GAAP."
    base = _candidate(_scope())
    candidate = replace(
        base,
        source_type="not_a_company_filing",
        quote_end=len(quote),
        exact_quote=quote,
        original_text=quote,
        claim=replace(
            base.claim,
            statement=quote,
            material_field="revenue_materiality",
            condition_ids=("capability.accounting_revenue_confirmed.v1.4",),
            claim_capabilities=("accounting_revenue_confirmed",),
        ),
    )

    class SpoofedSourceAcquirer:
        def acquire(self, *, run, graph, stage3_result, company):
            return EvidenceAcquisitionBatch(
                candidates=(candidate,),
                cost_usd=0.0,
                request_receipt_ids=("spoofed-accounting-evidence",),
            )

    class RecordingVerifier:
        calls = 0

        def verify(self, **_kwargs):
            self.calls += 1

    verifier = RecordingVerifier()
    researcher = EvidenceBoundCompanyResearcher(
        Mapper(),
        SpoofedSourceAcquirer(),
        Scorer(),
        Critic(),
        assertion_writer=CompanyBusinessFactAssertionWriter(
            verifier,
            producer_execution_id="spoofed-accounting-writer",
        ),
    )

    with pytest.raises(ValueError, match="official accounting source"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(
                    run_id="spoofed-accounting-run",
                    max_cost_usd=5.0,
                )
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(),
        )
    assert verifier.calls == 0


def test_production_assertion_writer_emits_official_quarter_accounting_proposal():
    """Regression: official quarterly revenue reaches the independent verifier boundary."""
    quote = (
        "ControlledCo reported USD 25 million revenue for UPS-X "
        "in Q1 2026 under GAAP."
    )
    base = _candidate(_scope())
    candidate = replace(
        base,
        source_identity_id="source_identity_accounting_fixture",
        source_version_id="source_version_accounting_fixture",
        quote_end=len(quote),
        exact_quote=quote,
        original_text=quote,
        claim=replace(
            base.claim,
            statement=quote,
            material_field="revenue_materiality",
            condition_ids=("capability.accounting_revenue_confirmed.v1.4",),
            claim_capabilities=("accounting_revenue_confirmed",),
        ),
    )

    class AccountingAcquirer:
        def acquire(self, *, run, graph, stage3_result, company):
            return EvidenceAcquisitionBatch(
                candidates=(candidate,),
                cost_usd=0.0,
                request_receipt_ids=("accounting-evidence-receipt",),
            )

    class RecordingVerifier:
        assertions = []

        def verify(self, *, assertion, **_kwargs):
            self.assertions.append(assertion)

    verifier = RecordingVerifier()
    result = EvidenceBoundCompanyResearcher(
        Mapper(),
        AccountingAcquirer(),
        Scorer(),
        Critic(),
        assertion_writer=CompanyBusinessFactAssertionWriter(
            verifier,
            producer_execution_id="company-accounting-proposal-writer",
        ),
    ).research(
        SimpleNamespace(
            request=SimpleNamespace(
                run_id="positive-accounting-writer-run",
                max_cost_usd=5.0,
            )
        ),
        SimpleNamespace(nodes=()),
        SimpleNamespace(),
    )

    assertion = result.evidence_cards[0].business_fact_assertions[0]
    assert verifier.assertions == [assertion]
    assert assertion.verification is None
    assert assertion.canonical_predicate_id == (
        "capability.accounting_revenue_confirmed.v1.4"
    )
    assert assertion.accounting_metric == "revenue"
    assert assertion.accounting_value == 25.0
    assert assertion.accounting_unit == "million"
    assert assertion.currency == "USD"
    assert assertion.fiscal_period_type == "quarter"
    assert assertion.fiscal_period_id == "Q12026"
    assert assertion.accounting_basis == "GAAP"


@pytest.mark.parametrize("case", ("wrong_scope", "malformed_ordinal", "shared_trace"))
def test_company_raw_model_counterfactuals_fail_closed(tmp_path, case):
    """Regression: Scope, ordinal, and scorer/critic trace bypasses stay closed."""
    repository = ThemeChokepointRepository(tmp_path / case / "theme.db")

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(
                _raw_discovery_payload(), f"{case}-discovery-trace"
            )

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(_raw_evidence_payload(), f"{case}-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            payload = _raw_scoring_payload(company, evidence_cards)
            if case == "wrong_scope":
                payload["entity"]["product_id"] = "wrong-product"
            elif case == "malformed_ordinal":
                payload["ordinal_outputs"] = [
                    {
                        "family": "defensibility",
                        "dimension": "technical_performance_gap",
                        "rating": 5,
                        "evidence_state": "supported",
                        "bound_type": "exact",
                        "bound_basis": {
                            "floor_anchor": 5,
                            "ceiling_anchor": 5,
                            "exact_basis": "natural_cap",
                            "unresolved_higher_anchors": [],
                            "excluded_higher_anchors": [],
                        },
                        "evidence_ids": [evidence_cards[0].evidence_id],
                    }
                ]
            return _raw_response(payload, f"{case}-model-trace")

        def review_company(self, *, company, **_kwargs):
            trace_id = (
                f"{case}-model-trace"
                if case == "shared_trace"
                else f"{case}-critic-trace"
            )
            return _raw_response(_raw_critic_payload(company), trace_id)

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
    )
    expected = {
        "wrong_scope": "Scope",
        "malformed_ordinal": "0..4",
        "shared_trace": "trace replay",
    }[case]

    with pytest.raises(ValueError, match=expected):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(
                    run_id=f"company-counterfactual-{case}",
                    max_cost_usd=5.0,
                )
            ),
            SimpleNamespace(nodes=()),
            SimpleNamespace(run_id=f"company-counterfactual-{case}"),
        )


def test_discovery_alias_cannot_bind_company_to_wrong_product_scope(tmp_path):
    """SELECT INVARIANT: mapped aliases remain bound to the Stage 3 segment/product."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")

    class Transport:
        def map_companies(self, **_kwargs):
            return _raw_response(_raw_discovery_payload(), "alias-discovery-trace")

        def acquire_company_evidence(self, **_kwargs):
            return _raw_response(_raw_evidence_payload(), "alias-evidence-trace")

        def score_company(self, *, company, evidence_cards, **_kwargs):
            return _raw_response(
                _raw_scoring_payload(company, evidence_cards), "alias-scoring-trace"
            )

        def review_company(self, *, company, **_kwargs):
            return _raw_response(_raw_critic_payload(company), "alias-critic-trace")

    researcher = build_default_company_researcher(
        **_independent_company_clients(Transport),
        repository=repository,
    )

    with pytest.raises(ValueError, match="mapped company Scope"):
        researcher.research(
            SimpleNamespace(
                request=SimpleNamespace(run_id="alias-scope-run", max_cost_usd=5.0)
            ),
            SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        node_id="segment-ups",
                        product_anchor_id="expected-product-anchor",
                    ),
                )
            ),
            SimpleNamespace(
                run_id="alias-scope-run",
                assessments=(SimpleNamespace(segment_id="segment-ups"),),
            ),
        )


def test_default_company_composition_rejects_any_shared_low_level_client():
    """SELECT INVARIANT: all four company execution roles are pairwise independent."""
    shared = SimpleNamespace()

    with pytest.raises(ValueError, match="four pairwise-independent"):
        build_default_company_researcher(
            discovery_client=shared,
            evidence_client=shared,
            scoring_model_client=SimpleNamespace(),
            critic_model_client=SimpleNamespace(),
        )


def test_default_provider_composition_includes_company_and_segment_counter_chains(tmp_path):
    """SELECT INVARIANT: one public composition exposes both production research chains."""
    class NoopTransport:
        def map_companies(self, **_kwargs):
            raise AssertionError("not reached")

        def acquire_company_evidence(self, **_kwargs):
            raise AssertionError("not reached")

        def score_company(self, **_kwargs):
            raise AssertionError("not reached")

        def review_company(self, **_kwargs):
            raise AssertionError("not reached")

    clients = _independent_company_clients(NoopTransport)
    composition = build_default_provider_composition(
        company_discovery_client=clients["discovery_client"],
        company_evidence_client=clients["evidence_client"],
        company_scoring_model_client=clients["scoring_model_client"],
        company_critic_model_client=clients["critic_model_client"],
        counter_search_executor=SimpleNamespace(),
        repository=ThemeChokepointRepository(tmp_path / "theme.db"),
        counter_max_queries=3,
        counter_max_time_seconds=60,
        counter_max_cost_usd=1.0,
    )

    assert isinstance(composition.company_researcher, EvidenceBoundCompanyResearcher)
    assert isinstance(composition.segment_critic, EvidenceBoundSegmentCritic)
