from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    CompanyRawProviderResponse,
    CompanyScope,
    CounterSearchRawProviderResponse,
    RunStatus,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.orchestrator import (
    MonitoringStageOutcome,
    NoEventsMonitoringStage,
    RootStageOrchestrator,
    _continue_owner,
)
from event_collector.theme_chokepoint.stage1 import AssistedThemeFramingService
from event_collector.theme_chokepoint.stage2 import SupplyChainGraphService
from event_collector.theme_chokepoint.stage3 import EvidenceChokepointLoop
from event_collector.theme_chokepoint.stage4 import CompanyExposureRedTeamService
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.stage7 import SignalExportService
from event_collector.theme_chokepoint.providers import (
    build_default_provider_composition,
)
from test_theme_chokepoint_stage1 import (
    RecordingFramer,
    RecordingProposer,
    anchor,
    clear_frame,
    request,
)
from test_theme_chokepoint_stage2 import MappingDependencyProposer, dependency
from test_theme_chokepoint_stage3 import ProgressiveScorer, _all_dimension_candidates


CONTROLLED_OVERLAY_ID = "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
CONTROLLED_OVERLAY_SHA256 = (
    "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
)
CONTROLLED_GOVERNANCE_BUNDLE_SHA256 = (
    "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
)


class FakeRepository:
    def __init__(self):
        self.run_id = "root-e2e-001"
        self.status = None

    def get_run(self, run_id):
        assert run_id == self.run_id
        return SimpleNamespace(status=self.status)


class FakeStage1:
    def __init__(self, repository):
        self.repository = repository

    def start(self, request):
        self.repository.status = RunStatus.AWAITING_PRODUCT_CONFIRMATION
        return SimpleNamespace(run_id=request.run_id, status=self.repository.status)


class AdvancingStage:
    def __init__(self, repository, method, output_status, artifact_field):
        self.repository = repository
        self.method = method
        self.output_status = output_status
        self.artifact_field = artifact_field
        self.calls = []

    def __getattr__(self, name):
        if name != self.method:
            raise AttributeError(name)

        def advance(run_id):
            self.calls.append(run_id)
            self.repository.status = self.output_status
            return SimpleNamespace(
                status=self.output_status,
                executable_contract_id=CONTROLLED_OVERLAY_ID,
                executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
                **{self.artifact_field: f"{self.method}-{run_id}"},
            )

        return advance


class FakeMonitoringStage:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    def run(self, run_id):
        self.calls.append(run_id)
        return MonitoringStageOutcome(
            run_id=run_id,
            status=self.repository.status,
            trigger_count=0,
            change_count=0,
            outcome="no_events",
        )


def test_continue_has_one_durable_owner_across_two_orchestrator_instances(tmp_path):
    """SELECT INVARIANT: two runtimes can dispatch the current Stage only once."""
    repository = FakeRepository()
    entered = Event()
    release = Event()

    class BlockingStage2:
        def __init__(self):
            self.calls = 0
            self.lock = Lock()

        def build(self, run_id):
            with self.lock:
                self.calls += 1
                call_number = self.calls
            if call_number > 1:
                raise AssertionError("duplicate Stage 2 dispatch")
            entered.set()
            assert release.wait(5)
            repository.status = RunStatus.SUPPLY_CHAIN_GRAPH_READY
            return SimpleNamespace(run_id=run_id, status=repository.status)

    stage2 = BlockingStage2()
    manifest_root = tmp_path / "m0-owner"
    shared = {
        "repository": repository,
        "stage1": FakeStage1(repository),
        "stage2": stage2,
        "stage3": AdvancingStage(
            repository, "run", RunStatus.CHOKEPOINT_ASSESSMENT_READY, "run_id"
        ),
        "stage4": AdvancingStage(
            repository, "run", RunStatus.COMPANY_ASSESSMENT_READY, "run_id"
        ),
        "stage5": AdvancingStage(
            repository, "finalize", RunStatus.PERSISTENT_RESEARCH_READY, "run_id"
        ),
        "stage6": FakeMonitoringStage(repository),
        "stage7": AdvancingStage(
            repository, "export", RunStatus.SIGNAL_EXPORT_READY, "run_id"
        ),
        "manifest_root": manifest_root,
        "executable_contract_id": CONTROLLED_OVERLAY_ID,
        "executable_contract_sha256": CONTROLLED_OVERLAY_SHA256,
    }
    first = RootStageOrchestrator(**shared)
    second = RootStageOrchestrator(**shared)
    first.start(SimpleNamespace(run_id=repository.run_id))
    repository.status = RunStatus.READY_FOR_SUPPLY_CHAIN
    first_errors = []

    def run_first():
        try:
            first.continue_run(repository.run_id)
        except Exception as error:  # pragma: no cover - asserted below
            first_errors.append(error)

    worker = Thread(target=run_first)
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(RuntimeError, match="continue already in progress"):
            second.continue_run(repository.run_id)
    finally:
        release.set()
        worker.join(5)

    assert worker.is_alive() is False
    assert first_errors == []
    assert stage2.calls == 1
    assert second.get_manifest(repository.run_id).final_status is RunStatus.SIGNAL_EXPORT_READY


def test_continue_owner_lock_is_visible_to_an_independent_process(tmp_path):
    """SELECT INVARIANT: direct continue ownership survives process boundaries."""
    lock_path = tmp_path / "process-visible.continue.lock"
    child = """
from pathlib import Path
import sys
from event_collector.theme_chokepoint.orchestrator import (
    ContinueInProgressError,
    _continue_owner,
)

try:
    with _continue_owner(Path(sys.argv[1])):
        pass
except ContinueInProgressError:
    raise SystemExit(23)
"""

    with _continue_owner(lock_path):
        blocked = subprocess.run(
            [sys.executable, "-c", child, str(lock_path)],
            check=False,
            capture_output=True,
            text=True,
        )

    assert blocked.returncode == 23, blocked.stderr


def test_root_orchestrator_stops_for_human_gate_then_runs_one_id_through_stage7(tmp_path):
    """SELECT INVARIANT: one durable run_id crosses every stage with an auditable manifest."""
    repository = FakeRepository()
    stage1 = FakeStage1(repository)
    stage2 = AdvancingStage(repository, "build", RunStatus.SUPPLY_CHAIN_GRAPH_READY, "run_id")
    stage3 = AdvancingStage(repository, "run", RunStatus.CHOKEPOINT_ASSESSMENT_READY, "run_id")
    stage4 = AdvancingStage(repository, "run", RunStatus.COMPANY_ASSESSMENT_READY, "run_id")
    stage5 = AdvancingStage(repository, "finalize", RunStatus.PERSISTENT_RESEARCH_READY, "run_id")
    stage6 = FakeMonitoringStage(repository)
    stage7 = AdvancingStage(repository, "export", RunStatus.SIGNAL_EXPORT_READY, "run_id")
    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        stage6=stage6,
        stage7=stage7,
        manifest_root=tmp_path / "manifests",
        executable_contract_id=CONTROLLED_OVERLAY_ID,
        executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
    )
    request = SimpleNamespace(run_id=repository.run_id)

    awaiting = orchestrator.start(request)

    assert awaiting.final_status is RunStatus.AWAITING_PRODUCT_CONFIRMATION
    assert [item.stage for item in awaiting.stages] == [1]

    repository.status = RunStatus.READY_FOR_SUPPLY_CHAIN
    completed = orchestrator.continue_run(repository.run_id)

    assert completed.final_status is RunStatus.SIGNAL_EXPORT_READY
    assert [item.stage for item in completed.stages] == [1, 2, 3, 4, 5, 6, 7]
    assert completed.stages[5].outcome == "no_events"
    manifest_path = tmp_path / "manifests" / repository.run_id / "stage1-7-e2e-run-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == repository.run_id
    assert payload["contract_id"] == "theme-chokepoint-scoring-v1.4"
    assert payload["executable_contract_id"] == CONTROLLED_OVERLAY_ID
    assert payload["executable_contract_sha256"] == CONTROLLED_OVERLAY_SHA256
    assert payload["final_status"] == "SIGNAL_EXPORT_READY"
    assert [item["stage"] for item in payload["stages"]] == [1, 2, 3, 4, 5, 6, 7]


def test_root_orchestrator_stops_after_persisted_incomplete_company_stage(tmp_path):
    """SELECT INVARIANT: Stage 4 incomplete is durable and cannot fall through to Stage 5."""
    repository = FakeRepository()
    stage1 = FakeStage1(repository)
    stage2 = AdvancingStage(
        repository, "build", RunStatus.SUPPLY_CHAIN_GRAPH_READY, "run_id"
    )
    stage3 = AdvancingStage(
        repository, "run", RunStatus.CHOKEPOINT_ASSESSMENT_READY, "run_id"
    )
    stage4 = AdvancingStage(
        repository, "run", RunStatus.COMPANY_ASSESSMENT_INCOMPLETE, "run_id"
    )
    stage5 = AdvancingStage(
        repository, "finalize", RunStatus.PERSISTENT_RESEARCH_READY, "run_id"
    )
    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        stage6=FakeMonitoringStage(repository),
        stage7=AdvancingStage(
            repository, "export", RunStatus.SIGNAL_EXPORT_READY, "run_id"
        ),
        manifest_root=tmp_path / "manifests",
        executable_contract_id=CONTROLLED_OVERLAY_ID,
        executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
    )
    orchestrator.start(SimpleNamespace(run_id=repository.run_id))
    repository.status = RunStatus.READY_FOR_SUPPLY_CHAIN

    result = orchestrator.continue_run(repository.run_id)

    assert result.final_status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    assert [item.stage for item in result.stages] == [1, 2, 3, 4]
    assert stage5.calls == []


def test_root_orchestrator_persists_each_successful_stage_receipt_before_later_crash(tmp_path):
    """SELECT INVARIANT: a later crash cannot erase an already committed Stage receipt."""
    repository = FakeRepository()
    stage1 = FakeStage1(repository)
    stage2 = AdvancingStage(
        repository, "build", RunStatus.SUPPLY_CHAIN_GRAPH_READY, "run_id"
    )

    class CrashingStage3:
        def run(self, run_id):
            raise RuntimeError("simulated Stage 3 crash")

    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=stage2,
        stage3=CrashingStage3(),
        stage4=None,
        stage5=None,
        stage6=None,
        stage7=None,
        manifest_root=tmp_path / "manifests",
        executable_contract_id=CONTROLLED_OVERLAY_ID,
        executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
    )
    orchestrator.start(SimpleNamespace(run_id=repository.run_id))
    repository.status = RunStatus.READY_FOR_SUPPLY_CHAIN

    with pytest.raises(RuntimeError, match="simulated Stage 3 crash"):
        orchestrator.continue_run(repository.run_id)

    manifest_path = (
        tmp_path / "manifests" / repository.run_id / "stage1-7-e2e-run-manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [item["stage"] for item in payload["stages"]] == [1, 2]
    assert payload["final_status"] == "SUPPLY_CHAIN_GRAPH_READY"
    assert payload["executable_contract_id"] == CONTROLLED_OVERLAY_ID
    assert payload["executable_contract_sha256"] == CONTROLLED_OVERLAY_SHA256


def test_root_orchestrator_fails_closed_when_stage_status_commits_before_receipt(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: recovery cannot skip a stage whose durable receipt is missing."""
    repository = FakeRepository()
    stage1 = FakeStage1(repository)
    stage2 = AdvancingStage(
        repository, "build", RunStatus.SUPPLY_CHAIN_GRAPH_READY, "run_id"
    )
    stage3 = AdvancingStage(
        repository, "run", RunStatus.CHOKEPOINT_ASSESSMENT_READY, "run_id"
    )
    stage4 = AdvancingStage(
        repository, "run", RunStatus.COMPANY_ASSESSMENT_READY, "run_id"
    )
    stage5 = AdvancingStage(
        repository, "finalize", RunStatus.PERSISTENT_RESEARCH_READY, "run_id"
    )
    stage6 = FakeMonitoringStage(repository)
    stage7 = AdvancingStage(
        repository, "export", RunStatus.SIGNAL_EXPORT_READY, "run_id"
    )
    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        stage6=stage6,
        stage7=stage7,
        manifest_root=tmp_path / "manifests",
        executable_contract_id=CONTROLLED_OVERLAY_ID,
        executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
    )
    orchestrator.start(SimpleNamespace(run_id=repository.run_id))
    repository.status = RunStatus.READY_FOR_SUPPLY_CHAIN

    write_manifest = orchestrator._write_manifest

    def crash_before_stage_receipt(_manifest):
        raise RuntimeError("simulated crash before Stage 2 receipt")

    monkeypatch.setattr(orchestrator, "_write_manifest", crash_before_stage_receipt)
    with pytest.raises(RuntimeError, match="simulated crash before Stage 2 receipt"):
        orchestrator.continue_run(repository.run_id)

    assert repository.status is RunStatus.SUPPLY_CHAIN_GRAPH_READY
    monkeypatch.setattr(orchestrator, "_write_manifest", write_manifest)

    with pytest.raises(ValueError, match="durable receipt is missing for Stage 2"):
        orchestrator.continue_run(repository.run_id)

    assert stage3.calls == []
    manifest_path = (
        tmp_path / "manifests" / repository.run_id / "stage1-7-e2e-run-manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["final_status"] == "AWAITING_PRODUCT_CONFIRMATION"
    assert [item["stage"] for item in payload["stages"]] == [1]


def test_controlled_local_e2e_uses_real_stages_sqlite_and_artifacts(tmp_path):
    """SELECT INVARIANT: one persisted run crosses the real Stage 1-7 services."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    stage1 = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([anchor("UPS", 0.9)]),
    )
    stage2 = SupplyChainGraphService(
        repository,
        MappingDependencyProposer(
            {"ups": [dependency("qualified ups power modules", status="supported")]}
        ),
    )

    class ControlledAcquirer:
        def __init__(self):
            self.completed = False

        def acquire(self, *, query, run, node, material_field):
            if self.completed:
                return []
            self.completed = True
            candidates = []
            for candidate in _all_dimension_candidates():
                scope = replace(
                    candidate.claim.assessment_scope,
                    product_id=node.product_anchor_id,
                    segment_id=node.node_id,
                )
                candidates.append(
                    replace(
                        candidate,
                        claim=replace(
                            candidate.claim,
                            node_id=node.node_id,
                            assessment_scope=scope,
                        ),
                    )
                )
            return candidates

    class ControlledCounterSearchTransport:
        def execute(
            self,
            *,
            route_id,
            query,
            node,
            ordinal_draft,
            claims,
            evidence_cards,
            request,
        ):
            by_dimension = {
                card.primary_scoring_dimension: card for card in evidence_cards
            }
            evidence_id = {
                "demand": by_dimension["demand_pressure"].evidence_id,
                "supply": by_dimension["effective_supply_concentration"].evidence_id,
                "alternatives": by_dimension["substitute_weakness"].evidence_id,
            }[route_id]
            payload = {
                    "query_log_id": f"controlled-counter-query-{route_id}",
                        "status": "explicit_negative",
                        "coverage_state": "explicit_negative",
                    "evidence_ids": [evidence_id],
                    "finding": f"Controlled executed {route_id} counter-search route.",
                        "counter_evidence": [],
                }
            return CounterSearchRawProviderResponse(
                provider="controlled-counter-search",
                provider_trace_id=f"controlled-counter-provider-{route_id}",
                http_status=200,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                cost_usd=0.1,
            )

    class DeferredCompanyTransport:
        delegate = None

        def __getattr__(self, name):
            if self.delegate is None:
                raise RuntimeError("controlled company transport is not configured")
            return getattr(self.delegate, name)

    company_discovery_transport = DeferredCompanyTransport()
    company_evidence_transport = DeferredCompanyTransport()
    company_scoring_transport = DeferredCompanyTransport()
    company_critic_transport = DeferredCompanyTransport()
    company_discovery_client = SimpleNamespace(
        map_companies=lambda **kwargs: company_discovery_transport.map_companies(
            **kwargs
        )
    )
    company_evidence_client = SimpleNamespace(
        acquire_company_evidence=lambda **kwargs: (
            company_evidence_transport.acquire_company_evidence(**kwargs)
        )
    )
    company_scoring_model_client = SimpleNamespace(
        score_company=lambda **kwargs: company_scoring_transport.score_company(**kwargs)
    )
    company_critic_model_client = SimpleNamespace(
        review_company=lambda **kwargs: company_critic_transport.review_company(**kwargs)
    )
    provider_composition = build_default_provider_composition(
        company_discovery_client=company_discovery_client,
        company_evidence_client=company_evidence_client,
        company_scoring_model_client=company_scoring_model_client,
        company_critic_model_client=company_critic_model_client,
        counter_search_executor=ControlledCounterSearchTransport(),
        repository=repository,
        counter_max_queries=3,
        counter_max_time_seconds=60,
        counter_max_cost_usd=1.0,
    )

    stage3 = EvidenceChokepointLoop(
        repository,
        ControlledAcquirer(),
        ProgressiveScorer(),
        expected_contract_sha256=CONTROLLED_OVERLAY_SHA256,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        critic=provider_composition.segment_critic,
    )

    company_scope = CompanyScope(
        company_id="controlled-powerco",
        segment_id="node-qualified-ups-power-modules",
        product_id="ups-x",
        customer_or_platform_scope="global hyperscale data centers",
        geography="global",
        time_horizon_months=24,
        as_of_date=date(2026, 8, 16),
    )
    defensibility_dimensions = (
        "technical_performance_gap",
        "qualification_lock_in",
        "switching_cost",
        "qualified_effective_capacity",
        "quality_delivery_reliability",
        "customer_sourcing_evidence",
    )

    class ControlledCompanyProviderTransport:
        @staticmethod
        def _raw(payload, trace_id, *, cost_usd=0.0):
            return CompanyRawProviderResponse(
                provider="controlled-company-provider",
                provider_trace_id=trace_id,
                http_status=200,
                raw_body=json.dumps(
                    payload,
                    default=lambda value: value.isoformat(),
                    sort_keys=True,
                ).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                cost_usd=cost_usd,
            )

        def map_companies(self, **kwargs):
                kwargs.pop("request_record_id")
                run = kwargs["run"]
                graph = kwargs["graph"]
                stage3_result = kwargs["stage3_result"]
                scope = replace(
                    company_scope,
                    segment_id=stage3_result.assessments[0].segment_id,
                    customer_or_platform_scope=run.demand_frame.scope,
                    geography=run.request.region,
                    product_id=graph.nodes[0].product_anchor_id,
                )
                return self._raw(
                    {
                        "companies": [
                            {
                                "name": "Controlled PowerCo",
                                "company_id": scope.company_id,
                                "roles": ["bottleneck_owner"],
                                "segment_id": scope.segment_id,
                                "product_id": scope.product_id,
                                "customer_or_platform_scope": scope.customer_or_platform_scope,
                                "geography": scope.geography,
                                "time_horizon_months": scope.time_horizon_months,
                                "as_of_date": scope.as_of_date.isoformat(),
                            }
                        ]
                    },
                    "controlled-company-map-1",
                )

        def acquire_company_evidence(self, **kwargs):
                kwargs.pop("request_record_id")
                documents = []
                for dimension in ("company_exposure", *defensibility_dimensions):
                    quote = f"Controlled original fact for {dimension}."
                    primary = dimension != "company_exposure"
                    documents.append(
                        {
                            "canonical_url": f"https://example.com/company/{dimension}",
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
                                    "material_field": dimension,
                                    "primary_scoring_dimension": (
                                        dimension if primary else None
                                    ),
                                    "scoring_use": (
                                        "primary" if primary else "context_only"
                                    ),
                                    "stance": "supports",
                                    "limitations": "controlled E2E raw transport",
                                    "condition_ids": (
                                        [f"{dimension}.anchor_3"] if primary else []
                                    ),
                                    "claim_capabilities": [
                                        "general_scoring_evidence"
                                    ],
                                }
                            ],
                        }
                    )
                return self._raw(
                    {"documents": documents},
                    "controlled-company-acquisition-1",
                    cost_usd=0.5,
                )

        def score_company(self, **kwargs):
                kwargs.pop("request_record_id")
                company = kwargs["company"]
                evidence_cards = kwargs["evidence_cards"]
                cards = {
                    card.primary_scoring_dimension: card
                    for card in evidence_cards
                    if card.primary_scoring_dimension
                }
                exposure_id = next(
                    card.evidence_id
                    for card in evidence_cards
                    if card.scoring_use == "context_only"
                )
                ordinal_outputs = []
                for dimension in defensibility_dimensions:
                    ordinal_outputs.append(
                        {
                            "family": "defensibility",
                            "dimension": dimension,
                            "rating": 3,
                            "evidence_state": "supported",
                            "bound_type": "exact",
                            "bound_basis": {
                                "floor_anchor": 3,
                                "ceiling_anchor": 3,
                                "exact_basis": "direct_upper_bound",
                                "unresolved_higher_anchors": [],
                                "excluded_higher_anchors": [4],
                            },
                            "evidence_ids": [cards[dimension].evidence_id],
                            "decisive_evidence_ids": [cards[dimension].evidence_id],
                            "stale": False,
                        }
                    )
                return self._raw(
                    {
                        "entity": {
                            "company_id": company.scope.company_id,
                            "product_id": company.scope.product_id,
                        },
                        "exposure_summary": {
                            "narrative": "Controlled original company filing.",
                            "operational": "Qualified UPS module exposure.",
                            "revenue": "Revenue remains unscored.",
                            "earnings": "Earnings remains unscored.",
                            "evidence_ids": [exposure_id],
                        },
                        "ordinal_outputs": ordinal_outputs,
                        "replacement_mode": None,
                        "milestone_observations": None,
                        "scope_valid": True,
                        "freshness_valid": True,
                        "evidence_mapping_error": False,
                        "gate_proposals": [],
                    },
                    "controlled-company-scorer-1",
                )

        def review_company(self, **kwargs):
                kwargs.pop("request_record_id")
                company = kwargs["company"]
                context_id = next(
                    card.evidence_id
                    for card in kwargs["evidence_cards"]
                    if card.scoring_use == "context_only"
                )
                return self._raw(
                    {
                        "entity": {
                            "company_id": company.scope.company_id,
                            "product_id": company.scope.product_id,
                        },
                        "admissible": True,
                        "red_team_reviews": [
                            {
                                "thesis": "defensibility",
                                "counter_evidence_queries": [
                                    "search alternate qualified suppliers"
                                ],
                                "unresolved_counterarguments": [
                                    "second source may qualify"
                                ],
                                "falsification_conditions": [
                                    "named customer switches volume"
                                ],
                                "evidence_ids": [context_id],
                            }
                        ],
                    },
                    "controlled-company-critic-1",
                )

    company_discovery_transport.delegate = ControlledCompanyProviderTransport()
    company_evidence_transport.delegate = ControlledCompanyProviderTransport()
    company_scoring_transport.delegate = ControlledCompanyProviderTransport()
    company_critic_transport.delegate = ControlledCompanyProviderTransport()

    stage4 = CompanyExposureRedTeamService(
        repository,
        provider_composition.company_researcher,
        expected_contract_sha256=CONTROLLED_OVERLAY_SHA256,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    )
    stage5 = PersistentResearchProductService(repository, tmp_path / "artifacts")
    stage6 = NoEventsMonitoringStage(repository)
    stage7 = SignalExportService(repository, tmp_path / "signal-exports")
    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        stage6=stage6,
        stage7=stage7,
        manifest_root=tmp_path / "manifests",
        executable_contract_id=CONTROLLED_OVERLAY_ID,
        executable_contract_sha256=CONTROLLED_OVERLAY_SHA256,
    )
    run_request = request(run_id="controlled-stage1-7-e2e", max_depth=1)

    awaiting = orchestrator.start(run_request)
    stage1.confirm_product_anchors(
        run_request.run_id,
        anchor_ids=(repository.get_run(run_request.run_id).product_anchors[0].anchor_id,),
        confirmed_by="controlled-e2e-owner",
    )
    completed = orchestrator.continue_run(run_request.run_id)

    assert awaiting.final_status is RunStatus.AWAITING_PRODUCT_CONFIRMATION
    assert completed.final_status is RunStatus.SIGNAL_EXPORT_READY
    assert [item.stage for item in completed.stages] == [1, 2, 3, 4, 5, 6, 7]
    stage3_result = repository.get_stage3_result(run_request.run_id)
    assert stage3_result.status is RunStatus.CHOKEPOINT_ASSESSMENT_READY
    assert stage3_result.assessments[0].primary_state == "candidate_chokepoint"
    assert stage3_result.assessments[0].critic_receipt is not None
    stage4_result = repository.get_stage4_result(run_request.run_id)
    assert stage4_result.company_assessments[0].defensibility.primary_state == "high_defensibility"
    assert stage4_result.company_claims
    assert stage4_result.company_evidence_cards
    assert stage4_result.company_source_snapshots
    assert {
        card.evidence_id for card in stage4_result.company_evidence_cards
    } <= {card.evidence_id for card in repository.list_evidence_cards(run_request.run_id)}
    assert stage4_result.company_chain_receipts[0].critic_request_receipt_id.startswith(
        "company-critic-reconciliation-"
    )
    assert {
        receipt.role
        for receipt in repository.list_company_provider_reconciliations(
            run_request.run_id
        )
    } == {"discovery", "evidence", "scoring", "critic"}
    assert len(stage4_result.provider_request_receipt_ids) == 4
    assert all(
        "-reconciliation-" in receipt_id
        for receipt_id in stage4_result.provider_request_receipt_ids
    )
    artifact_dir = tmp_path / "artifacts" / run_request.run_id
    assert (artifact_dir / "artifact-manifest.json").is_file()
    assert (artifact_dir / "counter-search-receipts.jsonl").read_text(
        encoding="utf-8"
    ).strip()
    artifact_claim_ids = {
        json.loads(line)["claim_id"]
        for line in (artifact_dir / "claim-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    artifact_evidence_ids = {
        json.loads(line)["evidence_id"]
        for line in (artifact_dir / "evidence-cards.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert {claim.claim_id for claim in stage4_result.company_claims} <= artifact_claim_ids
    assert {
        card.evidence_id for card in stage4_result.company_evidence_cards
    } <= artifact_evidence_ids
    artifact_run = json.loads((artifact_dir / "run.json").read_text(encoding="utf-8"))
    assert artifact_run["company_provider_request_receipt_ids"] == list(
        stage4_result.provider_request_receipt_ids
    )
    assert (tmp_path / "signal-exports" / run_request.run_id / "signal-export-manifest.json").is_file()
