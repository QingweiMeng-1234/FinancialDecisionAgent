"""Public production composition root for the Theme Chokepoint Stage 1-7 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from event_collector.theme_chokepoint.governance import (
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
)
from event_collector.theme_chokepoint.orchestrator import (
    NoEventsMonitoringStage,
    RootStageOrchestrator,
)
from event_collector.theme_chokepoint.providers import (
    DeepSeekUpstreamDependencyProposer,
    build_default_provider_composition,
)
from event_collector.theme_chokepoint.providers.company import (
    CompanyBusinessFactAssertionWriter,
)
from event_collector.theme_chokepoint.providers.company_clients import (
    AuthenticatedEndpointConfig,
    HttpBusinessFactVerifierClient,
    HttpCompanyCriticClient,
    HttpCompanyDiscoveryClient,
    HttpCompanyEvidenceClient,
    HttpCompanyScoringClient,
    HttpSourceResolutionClient,
)
from event_collector.theme_chokepoint.providers.fact_verifier import (
    EvidenceBoundBusinessFactVerifier,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
)
from event_collector.theme_chokepoint.stage1 import AssistedThemeFramingService
from event_collector.theme_chokepoint.stage2 import SupplyChainGraphService
from event_collector.theme_chokepoint.stage3 import EvidenceChokepointLoop
from event_collector.theme_chokepoint.stage4 import CompanyExposureRedTeamService
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.stage7 import SignalExportService


@dataclass(frozen=True)
class ProductionThemeChokepointConfig:
    db_path: str | Path
    artifact_root: str | Path
    signal_export_root: str | Path
    manifest_root: str | Path
    company_discovery: AuthenticatedEndpointConfig
    company_evidence: AuthenticatedEndpointConfig
    company_scoring: AuthenticatedEndpointConfig
    company_critic: AuthenticatedEndpointConfig
    fact_verifier: AuthenticatedEndpointConfig
    source_resolver: AuthenticatedEndpointConfig
    verifier_execution_id: str
    assertion_producer_execution_id: str
    governance_bundle_path: str | Path | None = None
    governance_bundle_sha256: str = CANONICAL_GOVERNANCE_BUNDLE_SHA256
    scoring_contract_path: str | Path | None = None
    scoring_contract_sha256: str | None = None
    allow_unfrozen_overlay: bool = False
    counter_max_queries: int = 3
    counter_max_time_seconds: int = 90
    counter_max_cost_usd: float = 5.0
    max_companies: int = 10
    dependency_max_candidates_per_node: int = 6
    dependency_timeout_seconds: float = 60.0
    dependency_max_input_chars: int = 12_000
    enable_v15_company_chain: bool = False


@dataclass(frozen=True)
class ProductionThemeChokepointRuntime:
    repository: ThemeChokepointRepository
    orchestrator: RootStageOrchestrator
    stage1: AssistedThemeFramingService
    stage2: SupplyChainGraphService
    stage3: EvidenceChokepointLoop
    stage4: CompanyExposureRedTeamService
    stage5: PersistentResearchProductService
    stage6: object
    stage7: SignalExportService
    company_discovery_client: HttpCompanyDiscoveryClient
    company_evidence_client: HttpCompanyEvidenceClient
    company_scoring_client: HttpCompanyScoringClient
    company_critic_client: HttpCompanyCriticClient
    fact_verifier_client: HttpBusinessFactVerifierClient
    source_resolution_client: HttpSourceResolutionClient

    @property
    def company_clients(self):
        return (
            self.company_discovery_client,
            self.company_evidence_client,
            self.company_scoring_client,
            self.company_critic_client,
        )


def build_production_theme_chokepoint_runtime(
    config: ProductionThemeChokepointConfig,
    *,
    theme_framer,
    product_anchor_proposer,
    evidence_acquirer,
    segment_scorer,
    counter_search_executor,
    dependency_proposer=None,
    relief_provider=None,
    monitoring_stage=None,
    quant_validator=None,
    session_factory=None,
) -> ProductionThemeChokepointRuntime:
    """Own all Stage services and isolated authenticated Company HTTP clients."""
    if not isinstance(config, ProductionThemeChokepointConfig):
        raise ValueError("production Theme Chokepoint configuration is required")
    company_endpoint_configs = (
        config.company_discovery,
        config.company_evidence,
        config.company_scoring,
        config.company_critic,
    )
    boundary_ids = {
        item.authenticated_boundary_id for item in company_endpoint_configs
    }
    endpoints = {item.endpoint for item in company_endpoint_configs}
    credential_materials = {item.bearer_token for item in company_endpoint_configs}
    if len(boundary_ids) != 4 or len(endpoints) != 4:
        raise ValueError(
            "production root requires four isolated authenticated company boundaries"
        )
    if len(credential_materials) != 4:
        raise ValueError(
            "production root requires four independent company credentials"
        )
    if not config.verifier_execution_id.strip() or not (
        config.assertion_producer_execution_id.strip()
    ):
        raise ValueError("production fact verifier execution identities are required")
    if config.verifier_execution_id == config.assertion_producer_execution_id:
        raise ValueError("production fact verifier must be independent from producer")

    if dependency_proposer is None:
        dependency_proposer = DeepSeekUpstreamDependencyProposer(
            max_candidates_per_node=config.dependency_max_candidates_per_node,
            timeout_seconds=config.dependency_timeout_seconds,
            max_input_chars=config.dependency_max_input_chars,
        )

    if session_factory is None:
        import requests

        session_factory = requests.Session
    sessions = tuple(session_factory() for _ in range(6))
    if len({id(item) for item in sessions}) != len(sessions):
        raise ValueError("production provider HTTP sessions must be pairwise independent")

    discovery_client = HttpCompanyDiscoveryClient(
        config.company_discovery, session=sessions[0]
    )
    evidence_client = HttpCompanyEvidenceClient(
        config.company_evidence, session=sessions[1]
    )
    scoring_client = HttpCompanyScoringClient(
        config.company_scoring, session=sessions[2]
    )
    critic_client = HttpCompanyCriticClient(
        config.company_critic, session=sessions[3]
    )
    fact_verifier_client = HttpBusinessFactVerifierClient(
        config.fact_verifier, session=sessions[4]
    )
    source_resolution_client = HttpSourceResolutionClient(
        config.source_resolver, session=sessions[5]
    )

    repository = ThemeChokepointRepository(config.db_path)
    source_identity_resolver = CanonicalSourceIdentityResolver(
        source_resolution_client,
        governance_bundle_path=config.governance_bundle_path,
        expected_governance_bundle_sha256=config.governance_bundle_sha256,
    )
    fact_verifier = EvidenceBoundBusinessFactVerifier(
        repository,
        fact_verifier_client,
        verifier_execution_id=config.verifier_execution_id,
        governance_bundle_sha256=config.governance_bundle_sha256,
    )
    assertion_writer = CompanyBusinessFactAssertionWriter(
        fact_verifier,
        producer_execution_id=config.assertion_producer_execution_id,
    )
    provider_composition = build_default_provider_composition(
        company_discovery_client=discovery_client,
        company_evidence_client=evidence_client,
        company_scoring_model_client=scoring_client,
        company_critic_model_client=critic_client,
        counter_search_executor=counter_search_executor,
        repository=repository,
        company_assertion_writer=assertion_writer,
        company_source_identity_resolver=source_identity_resolver,
        counter_max_queries=config.counter_max_queries,
        counter_max_time_seconds=config.counter_max_time_seconds,
        counter_max_cost_usd=config.counter_max_cost_usd,
        max_companies=config.max_companies,
        enable_v15_company_chain=config.enable_v15_company_chain,
    )

    contract_kwargs = {
        "contract_path": config.scoring_contract_path,
        "expected_contract_sha256": config.scoring_contract_sha256,
        "allow_unfrozen_overlay": config.allow_unfrozen_overlay,
        "governance_bundle_path": config.governance_bundle_path,
        "expected_governance_bundle_sha256": config.governance_bundle_sha256,
    }
    stage1 = AssistedThemeFramingService(
        repository, theme_framer, product_anchor_proposer
    )
    stage2 = SupplyChainGraphService(repository, dependency_proposer)
    stage3 = EvidenceChokepointLoop(
        repository,
        evidence_acquirer,
        segment_scorer,
        relief_provider=relief_provider,
        critic=provider_composition.segment_critic,
        **contract_kwargs,
    )
    stage4 = CompanyExposureRedTeamService(
        repository,
        provider_composition.company_researcher,
        enable_v15_company_chain=config.enable_v15_company_chain,
        **contract_kwargs,
    )
    stage5 = PersistentResearchProductService(repository, config.artifact_root)
    stage6 = monitoring_stage or NoEventsMonitoringStage(repository)
    stage7 = SignalExportService(
        repository,
        config.signal_export_root,
        quant_validator=quant_validator,
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
        manifest_root=config.manifest_root,
        executable_contract_id=stage3.contract.executable_contract_id,
        executable_contract_sha256=stage3.contract.executable_contract_sha256,
    )
    return ProductionThemeChokepointRuntime(
        repository=repository,
        orchestrator=orchestrator,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        stage6=stage6,
        stage7=stage7,
        company_discovery_client=discovery_client,
        company_evidence_client=evidence_client,
        company_scoring_client=scoring_client,
        company_critic_client=critic_client,
        fact_verifier_client=fact_verifier_client,
        source_resolution_client=source_resolution_client,
    )


__all__ = [
    "AuthenticatedEndpointConfig",
    "ProductionThemeChokepointConfig",
    "ProductionThemeChokepointRuntime",
    "build_production_theme_chokepoint_runtime",
]
