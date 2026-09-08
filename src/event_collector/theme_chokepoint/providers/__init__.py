"""Concrete provider adapters for Theme Chokepoint runtime interfaces."""

from dataclasses import dataclass

from event_collector.theme_chokepoint.providers.tavily import (
    OriginalTextFetcher,
    TavilyOriginalEvidenceAcquirer,
    TavilySearchProvider,
)
from event_collector.theme_chokepoint.providers.llm import (
    OpenAICompatibleEvidenceSpanExtractor,
)
from event_collector.theme_chokepoint.providers.scorer import (
    DeepSeekV14SegmentScorer,
    SEGMENT_DIMENSIONS,
)
from event_collector.theme_chokepoint.providers.facts_v16 import (
    DeepSeekV16SegmentFactExtractor,
    V16FactExtractionResult,
)
from event_collector.theme_chokepoint.providers.facts_v161 import (
    DeepSeekV161SegmentFactExtractor,
)
from event_collector.theme_chokepoint.providers.critic import (
    EvidenceBoundSegmentCritic,
)
from event_collector.theme_chokepoint.providers.company import (
    CompanyBusinessFactAssertionWriter,
    EvidenceBoundCompanyResearcher,
    ProviderChallengerSetBuilder,
    build_default_company_researcher,
)
from event_collector.theme_chokepoint.providers.counter_search import (
    EvidenceBoundCounterSearchProvider,
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
from event_collector.theme_chokepoint.providers.dependency import (
    DEPENDENCY_PROPOSER_PROMPT_VERSION,
    DeepSeekUpstreamDependencyProposer,
    DependencyProposalError,
)


@dataclass(frozen=True)
class DefaultProviderComposition:
    company_researcher: EvidenceBoundCompanyResearcher
    segment_critic: EvidenceBoundSegmentCritic


def build_default_provider_composition(
    *,
    company_discovery_client,
    company_evidence_client,
    company_scoring_model_client,
    company_critic_model_client,
    counter_search_executor,
    repository=None,
    company_assertion_writer=None,
    company_source_identity_resolver=None,
    counter_max_queries: int,
    counter_max_time_seconds: int,
    counter_max_cost_usd: float,
    max_companies: int = 10,
    enable_v15_company_chain: bool = False,
):
    """Compose the public Company and Segment critic chains from provider transports."""
    company_researcher = build_default_company_researcher(
        discovery_client=company_discovery_client,
        evidence_client=company_evidence_client,
        scoring_model_client=company_scoring_model_client,
        critic_model_client=company_critic_model_client,
        repository=repository,
        assertion_writer=company_assertion_writer,
        source_identity_resolver=company_source_identity_resolver,
        max_companies=max_companies,
        enable_v15_company_chain=enable_v15_company_chain,
    )
    counter_provider = EvidenceBoundCounterSearchProvider(
        counter_search_executor,
        repository=repository,
        max_queries=counter_max_queries,
        max_time_seconds=counter_max_time_seconds,
        max_cost_usd=counter_max_cost_usd,
    )
    return DefaultProviderComposition(
        company_researcher=company_researcher,
        segment_critic=EvidenceBoundSegmentCritic(counter_provider),
    )

__all__ = [
    "AuthenticatedEndpointConfig",
    "DeepSeekV14SegmentScorer",
    "DeepSeekV16SegmentFactExtractor",
    "DeepSeekV161SegmentFactExtractor",
    "DeepSeekUpstreamDependencyProposer",
    "DependencyProposalError",
    "DEPENDENCY_PROPOSER_PROMPT_VERSION",
    "DefaultProviderComposition",
    "CompanyBusinessFactAssertionWriter",
    "EvidenceBoundSegmentCritic",
    "EvidenceBoundCompanyResearcher",
    "ProviderChallengerSetBuilder",
    "EvidenceBoundCounterSearchProvider",
    "HttpBusinessFactVerifierClient",
    "HttpCompanyCriticClient",
    "HttpCompanyDiscoveryClient",
    "HttpCompanyEvidenceClient",
    "HttpCompanyScoringClient",
    "HttpSourceResolutionClient",
    "build_default_company_researcher",
    "build_default_provider_composition",
    "OriginalTextFetcher",
    "OpenAICompatibleEvidenceSpanExtractor",
    "SEGMENT_DIMENSIONS",
    "TavilyOriginalEvidenceAcquirer",
    "TavilySearchProvider",
    "V16FactExtractionResult",
]
