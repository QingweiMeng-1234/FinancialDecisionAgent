"""Frozen data contracts for the staged Theme Chokepoint workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from event_collector.theme_chokepoint.relief import ReliefHorizonAssessment


class RunStatus(str, Enum):
    REQUEST_STORED = "REQUEST_STORED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AWAITING_PRODUCT_CONFIRMATION = "AWAITING_PRODUCT_CONFIRMATION"
    READY_FOR_SUPPLY_CHAIN = "READY_FOR_SUPPLY_CHAIN"
    SUPPLY_CHAIN_GRAPH_READY = "SUPPLY_CHAIN_GRAPH_READY"
    CHOKEPOINT_ASSESSMENT_READY = "CHOKEPOINT_ASSESSMENT_READY"
    INCOMPLETE_BUDGET_EXHAUSTED = "INCOMPLETE_BUDGET_EXHAUSTED"
    COMPANY_ASSESSMENT_INCOMPLETE = "COMPANY_ASSESSMENT_INCOMPLETE"
    COMPANY_ASSESSMENT_READY = "COMPANY_ASSESSMENT_READY"
    PERSISTENT_RESEARCH_READY = "PERSISTENT_RESEARCH_READY"
    MONITORING_READY = "MONITORING_READY"
    SIGNAL_EXPORT_READY = "SIGNAL_EXPORT_READY"


@dataclass(frozen=True)
class ResearchRequest:
    run_id: str
    theme: str
    trigger: str
    region: str
    as_of_date: date
    time_horizon_months: int
    analysis_goal: str
    seed_products: tuple[str, ...]
    seed_companies: tuple[str, ...]
    research_mode: str
    max_depth: int
    max_nodes: int
    max_iterations: int
    max_sources: int
    max_time_seconds: int
    max_cost_usd: float
    max_product_anchors: int


@dataclass(frozen=True)
class AssessmentScope:
    company_id: str | None
    product_id: str
    segment_id: str
    customer_or_platform_scope: str
    geography: str
    time_horizon_months: int
    as_of_date: date


@dataclass(frozen=True)
class DemandFrame:
    normalized_theme: str
    scope: str
    exclusions: tuple[str, ...]
    demand_hypothesis: str | None
    measurable_demand_variables: tuple[str, ...]
    time_horizon_months: int
    unresolved_questions: tuple[str, ...]


@dataclass(frozen=True)
class ProductAnchorDraft:
    product_name: str
    buyer_or_user: str
    demand_variable: str
    theme_link: str
    confidence: float
    supporting_evidence_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...]


@dataclass(frozen=True)
class ProductAnchor(ProductAnchorDraft):
    anchor_id: str
    status: str


@dataclass(frozen=True)
class Stage1RunSnapshot:
    run_id: str
    request: ResearchRequest
    status: RunStatus
    demand_frame: DemandFrame | None
    product_anchors: tuple[ProductAnchor, ...]
    created_at: datetime
    updated_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True)
class ConfirmationReceipt:
    run_id: str
    status: RunStatus
    confirmed_anchor_ids: tuple[str, ...]
    confirmed_by: str
    confirmed_at: datetime


@dataclass(frozen=True)
class DependencyDraft:
    upstream_name: str
    node_type: str
    description: str
    aliases: tuple[str, ...]
    relation_type: str
    demand_transmission: str
    criticality_hypothesis: str
    substitute_hypothesis: str
    confidence: float
    supporting_claim_ids: tuple[str, ...]
    verification_questions: tuple[str, ...]
    evidence_status: str
    stop_reason: str | None = None


@dataclass(frozen=True)
class SupplyChainNode:
    node_id: str
    normalized_name: str
    node_type: str
    depth: int
    status: str
    description: str
    aliases: tuple[str, ...]
    product_anchor_id: str | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class DependencyEdge:
    edge_id: str
    downstream_node_id: str
    upstream_node_id: str
    relation_type: str
    demand_transmission: str
    criticality_hypothesis: str
    substitute_hypothesis: str
    confidence: float
    supporting_claim_ids: tuple[str, ...]
    verification_questions: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class SupplyChainGraph:
    run_id: str
    status: RunStatus
    nodes: tuple[SupplyChainNode, ...]
    edges: tuple[DependencyEdge, ...]
    truncation_reasons: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class ClaimDraft:
    claim_id: str
    node_id: str
    claim_type: str
    statement: str
    material_field: str
    primary_scoring_dimension: str | None
    scoring_use: str
    fact_key: str = ""
    assessment_scope: AssessmentScope | None = None
    condition_ids: tuple[str, ...] = ()
    claim_capabilities: tuple[str, ...] = ()
    source_ambiguity: bool = False
    scoring_eligible: bool = True
    subject_company_ids: tuple[str, ...] = ()
    derived_from_evidence_id: str | None = None


@dataclass(frozen=True)
class EvidenceCandidate:
    article_id: str
    canonical_url: str
    source_title: str
    publisher: str
    source_type: str
    publication_date: date | None
    data_as_of_date: date | None
    location: str
    quote_start: int
    quote_end: int
    exact_quote: str
    original_text: str
    source_mode: str
    stance: str
    limitations: str
    extraction_model: str
    prompt_version: str
    origin_event_id: str
    evidence_family_id: str
    claim: ClaimDraft
    source_identity_id: str | None = None
    source_version_id: str | None = None


@dataclass(frozen=True)
class EvidenceAcquisitionBatch:
    candidates: tuple[EvidenceCandidate, ...]
    cost_usd: float
    request_receipt_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExtractedEvidenceSpan:
    exact_quote: str
    claim_type: str
    statement: str
    stance: str
    limitations: str
    location: str
    primary_scoring_dimension: str | None
    scoring_use: str
    data_as_of_date: date | None = None
    origin_event_key: str | None = None


@dataclass(frozen=True)
class Claim:
    claim_id: str
    node_id: str
    claim_type: str
    statement: str
    material_field: str
    primary_scoring_dimension: str | None
    scoring_use: str
    evidence_ids: tuple[str, ...]
    fact_key: str = ""
    assessment_scope: AssessmentScope | None = None
    condition_ids: tuple[str, ...] = ()
    claim_capabilities: tuple[str, ...] = ()
    source_ambiguity: bool = False
    scoring_eligible: bool = True
    subject_company_ids: tuple[str, ...] = ()
    derived_from_evidence_id: str | None = None


@dataclass(frozen=True)
class FactVerificationReceipt:
    request_record_id: str
    response_record_id: str
    receipt_id: str
    verifier: str
    verified_at: datetime
    raw_response_sha256: str
    decision: str


@dataclass(frozen=True)
class BusinessFactAssertion:
    assertion_id: str
    evidence_id: str
    subject_company_id: str
    subject_product_id: str
    canonical_predicate_id: str
    polarity: str
    lifecycle_state: str
    quote_start: int
    quote_end: int
    exact_quote_sha256: str
    verification: FactVerificationReceipt | None = None
    accounting_metric: str | None = None
    accounting_value: float | None = None
    accounting_unit: str | None = None
    currency: str | None = None
    fiscal_period_type: str | None = None
    fiscal_period_id: str | None = None
    accounting_basis: str | None = None


@dataclass(frozen=True)
class BusinessFactVerificationSourceContext:
    """Exact persisted source bytes and business coordinates shown to a verifier."""

    source_identity_id: str
    source_version_id: str
    canonical_url: str
    canonical_publisher_id: str
    canonical_document_id: str | None
    origin_event_id: str | None
    source_relation: str
    source_provenance: str
    source_identity_policy_id: str
    source_identity_policy_sha256: str
    source_governance_bundle_id: str
    source_governance_bundle_sha256: str
    source_resolver_receipt_id: str
    source_resolver_receipt_sha256: str
    source_type: str
    version_sequence: int
    original_document_bytes: bytes
    original_document_text: str
    raw_bytes_sha256: str
    normalized_content_sha256: str
    quote_start: int
    quote_end: int
    exact_quote: str
    exact_quote_sha256: str
    issuer_company_id: str
    product_id: str
    fiscal_period_id: str | None
    accounting_metric: str | None


@dataclass(frozen=True)
class BusinessFactVerificationRequestRecord:
    request_record_id: str
    run_id: str
    assertion_id: str
    evidence_id: str
    assertion_sha256: str
    governance_bundle_sha256: str
    producer_execution_id: str
    verifier_execution_id: str
    source_context: BusinessFactVerificationSourceContext
    source_context_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class BusinessFactVerificationRawProviderResponse:
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class BusinessFactVerificationRawResponseRecord:
    response_record_id: str
    request_record_id: str
    run_id: str
    verifier: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    raw_response_sha256: str
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class BusinessFactVerificationParsedResultRecord:
    result_record_id: str
    request_record_id: str
    response_record_id: str
    run_id: str
    assertion_id: str
    assertion_sha256: str
    decision: str
    parsed_at: datetime


@dataclass(frozen=True)
class BusinessFactVerificationReconciliationRecord:
    receipt_id: str
    run_id: str
    assertion_id: str
    request_record_id: str
    response_record_id: str
    result_record_id: str
    governance_bundle_sha256: str
    source_identity_id: str
    source_version_id: str
    source_context_sha256: str
    reconciled_at: datetime


@dataclass(frozen=True)
class EvidenceCard:
    evidence_id: str
    claim_id: str
    article_id: str
    canonical_url: str
    source_title: str
    publisher: str
    source_type: str
    publication_date: date | None
    data_as_of_date: date | None
    location: str
    quote_start: int
    quote_end: int
    exact_quote: str
    content_hash: str
    stance: str
    limitations: str
    extraction_model: str
    prompt_version: str
    origin_event_id: str
    evidence_family_id: str
    business_fact_assertions: tuple[BusinessFactAssertion, ...] = ()
    fact_key: str = ""
    assessment_scope: AssessmentScope | None = None
    condition_ids: tuple[str, ...] = ()
    claim_capabilities: tuple[str, ...] = ()
    primary_scoring_dimension: str | None = None
    scoring_use: str = "context_only"
    source_ambiguity: bool = False
    scoring_eligible: bool = True
    accounting_period_type: str | None = None
    accounting_period_id: str | None = None
    source_identity_id: str | None = None
    source_version_id: str | None = None
    subject_company_ids: tuple[str, ...] = ()
    derived_from_evidence_id: str | None = None


@dataclass(frozen=True)
class BoundBasis:
    floor_anchor: int | None = None
    ceiling_anchor: int | None = None
    exact_basis: str | None = None
    unresolved_higher_anchors: tuple[int, ...] = ()
    excluded_higher_anchors: tuple[int, ...] = ()


@dataclass(frozen=True)
class AnchorConditionResult:
    condition_id: str
    condition_type: str
    evidence_state: str
    evidence_ids: tuple[str, ...]
    decisive_claim_ids: tuple[str, ...]
    anchor: int | None = None
    excluded_higher_anchors: tuple[int, ...] = ()


@dataclass(frozen=True)
class DimensionRatingDraft:
    dimension: str
    rating_min: int
    rating_max: int
    evidence_state: str
    bound_type: str
    bound_basis: BoundBasis
    evidence_ids: tuple[str, ...]
    stale: bool
    rationale: str = ""
    missing_material_questions: tuple[str, ...] = ()
    anchor_conditions: tuple[AnchorConditionResult, ...] = ()
    decisive_evidence_ids: tuple[str, ...] = ()
    anchor_profile: str | None = None


@dataclass(frozen=True)
class SegmentCounterSearchRoute:
    route_id: str
    query: str
    query_log_id: str
    provider_request_receipt_id: str
    status: str
    evidence_ids: tuple[str, ...]
    finding: str
    executed_at: datetime
    cost_usd: float
    run_id: str = ""
    assessment_as_of: str = ""
    request_record_id: str = ""
    response_record_id: str = ""
    result_record_id: str = ""
    result_semantics: str = ""
    new_counter_evidence_ids: tuple[str, ...] = ()
    reconciliation_receipt_id: str = ""


@dataclass(frozen=True)
class CounterSearchExecution:
    route_id: str
    query: str
    query_log_id: str
    provider_request_receipt_id: str
    status: str
    evidence_ids: tuple[str, ...]
    finding: str
    executed_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CounterEvidenceCandidate:
    """A parser proposal; repository materialization owns its durable IDs."""

    canonical_url: str
    original_text: str
    exact_quote: str
    claim_statement: str
    source_title: str = ""
    publisher: str = ""
    source_type: str = "counter_search"


@dataclass(frozen=True)
class CounterSearchRawProviderResponse:
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CounterSearchRequestRecord:
    request_record_id: str
    run_id: str
    route_id: str
    query: str
    assessment_as_of: str
    created_at: datetime
    segment_id: str = ""
    assessment_scope_sha256: str = ""


@dataclass(frozen=True)
class CounterSearchRawResponseRecord:
    response_record_id: str
    request_record_id: str
    run_id: str
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    raw_response_sha256: str
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CounterSearchParsedResultRecord:
    result_record_id: str
    response_record_id: str
    request_record_id: str
    run_id: str
    route_id: str
    query: str
    status: str
    evidence_ids: tuple[str, ...]
    new_counter_evidence_ids: tuple[str, ...]
    finding: str
    query_log_id: str
    result_semantics: str
    parsed_at: datetime
    coverage_state: str = "unknown"
    segment_id: str = ""
    assessment_scope_sha256: str = ""


@dataclass(frozen=True)
class CounterSearchReconciliationReceipt:
    receipt_id: str
    request_record_id: str
    response_record_id: str
    result_record_id: str
    run_id: str
    route_id: str
    decision: str
    reconciled_at: datetime
    segment_id: str = ""
    assessment_scope_sha256: str = ""
    coverage_state: str = "unknown"
    new_counter_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SegmentCriticReceipt:
    segment_id: str
    protocol_version: str
    query_log_ids: tuple[str, ...]
    stop_reason: str
    unresolved_routes: tuple[str, ...]
    demand_evidence_ids: tuple[str, ...]
    supply_evidence_ids: tuple[str, ...]
    key_source_evidence_ids: tuple[str, ...]
    completed_at: datetime
    route_findings: tuple[SegmentCounterSearchRoute, ...] = ()
    provider_request_receipt_ids: tuple[str, ...] = ()
    max_queries: int = 0
    max_time_seconds: int = 0
    max_cost_usd: float = 0.0
    cost_usd_spent: float = 0.0
    run_id: str = ""
    assessment_as_of: str = ""


@dataclass(frozen=True)
class SegmentScoreDraft:
    segment_id: str
    dimensions: tuple[DimensionRatingDraft, ...]
    missing_material_fields: tuple[str, ...]
    demand_direct_evidence: bool
    supply_direct_evidence: bool
    counter_evidence_search_complete: bool
    mandatory_conflict: bool
    relief_horizon: str | None
    independent_supply_constraint_count: int = 0
    key_source_quality_high: bool = False
    relief_assessment: ReliefHorizonAssessment | None = None
    critic_receipt: SegmentCriticReceipt | None = None


@dataclass(frozen=True)
class SegmentAssessment:
    segment_id: str
    contract_version: str
    dimensions: tuple[DimensionRatingDraft, ...]
    score_min: float
    score_max: float
    presence_coverage: float
    resolved_coverage: float
    decision_coverage: float
    conflicted_weight_share: float
    unknown_weight_share: float
    stale_weight_share: float
    primary_state: str | None
    missing_material_fields: tuple[str, ...]
    relief_horizon: str | None
    eligible: bool = False
    achieved_gates: tuple[str, ...] = ()
    withheld_reason: str | None = None
    relief_assessment: ReliefHorizonAssessment | None = None
    critic_receipt: SegmentCriticReceipt | None = None


@dataclass(frozen=True)
class SourceSnapshot:
    article_id: str
    canonical_url: str
    content_hash: str
    original_text: str


@dataclass(frozen=True)
class Stage3Result:
    run_id: str
    status: RunStatus
    contract_version: str
    executable_contract_id: str
    executable_contract_sha256: str
    claims: tuple[Claim, ...]
    evidence_cards: tuple[EvidenceCard, ...]
    source_snapshots: tuple[SourceSnapshot, ...]
    assessments: tuple[SegmentAssessment, ...]
    iterations_completed: int
    incomplete_reasons: tuple[str, ...]
    created_at: datetime
    provider_request_receipt_ids: tuple[str, ...] = ()
    cost_usd_spent: float = 0.0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class CompanyScope:
    company_id: str
    segment_id: str
    product_id: str
    customer_or_platform_scope: str
    geography: str
    time_horizon_months: int
    as_of_date: date


@dataclass(frozen=True)
class CompanyCandidate:
    company_name: str
    scope: CompanyScope
    roles: tuple[str, ...]


@dataclass(frozen=True)
class CompanyMappingBatch:
    candidates: tuple[CompanyCandidate, ...]
    request_receipt_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyRawProviderResponse:
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime
    cost_usd: float = 0.0


@dataclass(frozen=True)
class CompanyProviderRequestRecord:
    request_record_id: str
    run_id: str
    role: str
    company_id: str | None
    request_payload_json: str
    request_payload_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class CompanyProviderRawResponseRecord:
    response_record_id: str
    request_record_id: str
    run_id: str
    role: str
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    raw_response_sha256: str
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CompanyProviderParsedResultRecord:
    result_record_id: str
    request_record_id: str
    response_record_id: str
    run_id: str
    role: str
    parsed_payload_json: str
    parsed_payload_sha256: str
    parser_version: str
    parsed_at: datetime


@dataclass(frozen=True)
class CompanyProviderReconciliationRecord:
    receipt_id: str
    request_record_id: str
    response_record_id: str
    result_record_id: str
    run_id: str
    role: str
    reconciled_at: datetime


@dataclass(frozen=True)
class CompanyExposure:
    narrative: str
    operational: str
    revenue: str
    earnings: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class MilestoneAxes:
    product_readiness: str
    lifecycle: str
    qualification: str
    production: str
    adoption: str
    financial: str
    share_trajectory: str
    displacement: str


@dataclass(frozen=True)
class RedTeamReview:
    thesis: str
    counter_evidence_queries: tuple[str, ...]
    unresolved_counterarguments: tuple[str, ...]
    falsification_conditions: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CounterSearchRouteFinding:
    route_id: str
    status: str
    query_log_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    finding: str
    query: str = ""
    provider_request_receipt_id: str = ""
    executed_at: datetime | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class CounterSearchReceipt:
    challenger_set_id: str
    protocol_version: str
    query_log_ids: tuple[str, ...]
    max_queries: int
    max_time_seconds: int
    max_cost_usd: float
    stop_reason: str
    route_findings: tuple[CounterSearchRouteFinding, ...]
    negative_findings: tuple[str, ...]
    completed_at: datetime
    provider_request_receipt_ids: tuple[str, ...] = ()
    cost_usd_spent: float = 0.0
    discovery_rounds_without_new_material_routes: int = 0


@dataclass(frozen=True)
class ChallengerSet:
    challenger_set_id: str
    segment_id: str
    assessment_scope: AssessmentScope | str
    search_protocol_version: str
    included_company_ids: tuple[str, ...]
    excluded_candidates_with_reason: tuple[tuple[str, str], ...]
    search_completed_at: datetime
    as_of_date: date
    coverage_gate: bool
    counter_search_receipt: CounterSearchReceipt | None = None
    included_assessment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChallengerSetBuildBatch:
    challenger_sets: tuple[ChallengerSet, ...]
    request_receipt_ids: tuple[str, ...]
    cost_usd: float


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    label: str
    required_predicate_ids: tuple[str, ...]
    decisive_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyAssessmentDraft:
    company_name: str
    scope: CompanyScope
    roles: tuple[str, ...]
    exposure: CompanyExposure
    defensibility: tuple[DimensionRatingDraft, ...]
    replacement: tuple[DimensionRatingDraft, ...]
    earnings: tuple[DimensionRatingDraft, ...]
    replacement_mode: str | None
    ecosystem_compatibility: DimensionRatingDraft | None
    displacement: DimensionRatingDraft | None
    milestones: MilestoneAxes | None
    scope_valid: bool
    freshness_valid: bool
    evidence_mapping_error: bool
    gate_results: tuple[GateResult, ...]
    red_team_reviews: tuple[RedTeamReview, ...]


@dataclass(frozen=True)
class CompanyScoringResult:
    draft: CompanyAssessmentDraft
    request_receipt_id: str


@dataclass(frozen=True)
class CompanyCriticResult:
    draft: CompanyAssessmentDraft
    request_receipt_id: str


@dataclass(frozen=True)
class CompanyChainReceipt:
    company_id: str
    mapper_request_receipt_ids: tuple[str, ...]
    acquisition_request_receipt_ids: tuple[str, ...]
    scorer_request_receipt_id: str
    critic_request_receipt_id: str
    acquired_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyResearchResult:
    drafts: tuple[CompanyAssessmentDraft, ...]
    challenger_sets: tuple[ChallengerSet, ...]
    claims: tuple[Claim, ...]
    evidence_cards: tuple[EvidenceCard, ...]
    source_snapshots: tuple[SourceSnapshot, ...]
    chain_receipts: tuple[CompanyChainReceipt, ...]
    provider_request_receipt_ids: tuple[str, ...]
    cost_usd_spent: float


@dataclass(frozen=True)
class ScoreFamilyAssessment:
    family: str
    dimensions: tuple[DimensionRatingDraft, ...]
    score_min: float
    score_max: float
    presence_coverage: float
    resolved_coverage: float
    decision_coverage: float
    primary_state: str | None
    achieved_gates: tuple[str, ...]
    withheld_reason: str | None


@dataclass(frozen=True)
class CompanyAssessment:
    assessment_id: str
    company_name: str
    scope: CompanyScope
    roles: tuple[str, ...]
    exposure: CompanyExposure
    defensibility: ScoreFamilyAssessment | None
    replacement: ScoreFamilyAssessment | None
    earnings: ScoreFamilyAssessment | None
    replacement_mode: str | None
    milestones: MilestoneAxes | None
    competition_primary_state: str | None
    competition_achieved_gates: tuple[str, ...]
    competition_withheld_reason: str | None
    challenger_label: str | None
    gate_results: tuple[GateResult, ...]
    red_team_reviews: tuple[RedTeamReview, ...]


@dataclass(frozen=True)
class Stage4Result:
    run_id: str
    status: RunStatus
    contract_version: str
    executable_contract_id: str
    executable_contract_sha256: str
    challenger_sets: tuple[ChallengerSet, ...]
    company_assessments: tuple[CompanyAssessment, ...]
    created_at: datetime
    company_claims: tuple[Claim, ...] = ()
    company_evidence_cards: tuple[EvidenceCard, ...] = ()
    company_source_snapshots: tuple[SourceSnapshot, ...] = ()
    company_chain_receipts: tuple[CompanyChainReceipt, ...] = ()
    provider_request_receipt_ids: tuple[str, ...] = ()
    cost_usd_spent: float = 0.0
    incomplete_reasons: tuple[str, ...] = ()
    segment_recompute_requests: tuple[SegmentRecomputeRequest, ...] = ()


@dataclass(frozen=True)
class SegmentRecomputeRequest:
    request_id: str
    run_id: str
    segment_id: str
    triggering_claim_ids: tuple[str, ...]
    triggering_evidence_ids: tuple[str, ...]
    source_stage3_contract_id: str
    source_stage3_contract_sha256: str
    reason: str
    created_at: datetime
    status: str = "required"


@dataclass(frozen=True)
class ArtifactFile:
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactManifest:
    run_id: str
    executable_contract_id: str
    executable_contract_sha256: str
    files: tuple[ArtifactFile, ...]
    created_at: datetime


@dataclass(frozen=True)
class ResumeOutcome:
    run_id: str
    status_before: RunStatus
    next_stage: int | None
    dispatched: bool


@dataclass(frozen=True)
class FeedbackCorrectionDraft:
    run_id: str
    object_type: str
    object_id: str
    field_name: str
    proposed_value: str
    rationale: str
    actor: str


@dataclass(frozen=True)
class FeedbackCorrection:
    correction_id: str
    run_id: str
    object_type: str
    object_id: str
    field_name: str
    proposed_value: str
    rationale: str
    actor: str
    created_at: datetime


@dataclass(frozen=True)
class RunComparisonChange:
    object_type: str
    object_id: str
    field_name: str
    before_value: str | None
    after_value: str | None


@dataclass(frozen=True)
class RunComparison:
    before_run_id: str
    after_run_id: str
    changes: tuple[RunComparisonChange, ...]


@dataclass(frozen=True)
class HotspotEvent:
    event_id: str
    occurred_at: datetime
    source_id: str
    primary_source: bool
    companies: tuple[str, ...]
    sectors: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class HotspotDraft:
    theme: str
    window: str
    volume_acceleration: float
    source_diversity: float
    novelty: float
    company_breadth: float
    sector_breadth: float
    primary_source_confirmation: float
    persistence: float
    trigger_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class HotspotCandidate:
    hotspot_id: str
    theme: str
    window: str
    priority_score: float
    score_purpose: str
    scoring_inputs: dict[str, float]
    trigger_event_ids: tuple[str, ...]
    status: str
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class MonitoringTriggerDraft:
    target_type: str
    target_id: str
    event_types: tuple[str, ...]
    created_by: str


@dataclass(frozen=True)
class MonitoringTrigger:
    trigger_id: str
    run_id: str
    target_type: str
    target_id: str
    event_types: tuple[str, ...]
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class MonitoringEvidence:
    event_id: str
    event_type: str
    target_type: str
    target_id: str
    evidence_ids: tuple[str, ...]
    event_time: datetime
    published_at: datetime
    assessment_as_of: datetime
    new_claims: tuple[Claim, ...] = ()
    new_evidence_cards: tuple[EvidenceCard, ...] = ()
    new_source_snapshots: tuple[SourceSnapshot, ...] = ()
    source_version_ids: tuple[str, ...] = ()
    governance_bundle_sha256: str | None = None


@dataclass(frozen=True)
class MonitoringEvaluatorRawResponse:
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class MonitoringEvaluatorRequestRecord:
    request_record_id: str
    run_id: str
    target_type: str
    target_id: str
    evidence_event_ids: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class MonitoringEvaluatorRawResponseRecord:
    response_record_id: str
    request_record_id: str
    run_id: str
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    raw_response_sha256: str
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class MonitoringEvaluatorParsedResultRecord:
    result_record_id: str
    request_record_id: str
    response_record_id: str
    run_id: str
    target_type: str
    target_id: str
    parsed_payload_json: str
    parsed_at: datetime


@dataclass(frozen=True)
class CanonicalScoringRawProviderResponse:
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CanonicalScoringRequestRecord:
    request_record_id: str
    operation_id: str
    run_id: str
    target_type: str
    target_id: str
    evaluator_result_record_id: str
    expected_head_revision_id: str
    request_payload_json: str
    request_payload_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class CanonicalScoringRawResponseRecord:
    response_record_id: str
    request_record_id: str
    operation_id: str
    run_id: str
    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    raw_response_sha256: str
    retrieved_at: datetime
    cost_usd: float


@dataclass(frozen=True)
class CanonicalScoringParsedResultRecord:
    result_record_id: str
    request_record_id: str
    response_record_id: str
    operation_id: str
    run_id: str
    parsed_payload_json: str
    parsed_payload_sha256: str
    parser_version: str
    parsed_at: datetime


@dataclass(frozen=True)
class CanonicalScoringReconciliationRecord:
    receipt_id: str
    request_record_id: str
    response_record_id: str
    result_record_id: str
    operation_id: str
    run_id: str
    reconciled_at: datetime


@dataclass(frozen=True)
class AssessmentRevisionRecord:
    revision_id: str
    run_id: str
    target_type: str
    target_id: str
    parent_revision_id: str | None
    result_reconciliation_id: str | None
    payload_json: str
    payload_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class AssessmentHeadRecord:
    run_id: str
    target_type: str
    target_id: str
    revision_id: str
    revision: object
    updated_at: datetime


@dataclass(frozen=True)
class ObjectRefreshDraft:
    target_type: str
    target_id: str
    before_state: str | None
    after_state: str | None
    changed_claim_ids: tuple[str, ...]
    changed_dimensions: tuple[str, ...]
    change_type: str
    explanation: str
    before_score_min: float
    after_score_min: float
    before_score_max: float
    after_score_max: float
    before_mandatory_gate_level: int
    after_mandatory_gate_level: int
    before_mandatory_floor_valid: bool
    after_mandatory_floor_valid: bool
    original_constraint_active: bool
    former_constraint_easing: bool
    adjacent_constraint_strengthening: bool
    underlying_demand_intact: bool
    relief_assessment: ReliefHorizonAssessment | None = None
    recompute_receipt_id: str | None = None
    executable_contract_id: str | None = None
    executable_contract_sha256: str | None = None


@dataclass(frozen=True)
class MonitoringChange:
    change_id: str
    target_type: str
    target_id: str
    before_state: str | None
    after_state: str | None
    changed_claim_ids: tuple[str, ...]
    changed_dimensions: tuple[str, ...]
    trend_state: str
    change_type: str
    explanation: str
    responsible_event_ids: tuple[str, ...]
    responsible_evidence_ids: tuple[str, ...]
    recompute_receipt_id: str | None = None
    executable_contract_id: str | None = None
    executable_contract_sha256: str | None = None


@dataclass(frozen=True)
class MonitoringRefreshResult:
    refresh_id: str
    run_id: str
    status: RunStatus
    trigger_ids: tuple[str, ...]
    changes: tuple[MonitoringChange, ...]
    created_at: datetime


@dataclass(frozen=True)
class SignalExportManifest:
    run_id: str
    executable_contract_id: str
    executable_contract_sha256: str
    csv_path: str
    parquet_path: str
    definitions_path: str
    lineage_path: str
    quant_validation_path: str | None
    files: tuple[ArtifactFile, ...]
    created_at: datetime
