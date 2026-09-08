"""SQLite persistence for staged Theme Chokepoint runs."""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Iterable
from urllib.parse import urlsplit

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult,
    AssessmentScope,
    AssessmentHeadRecord,
    AssessmentRevisionRecord,
    BusinessFactAssertion,
    BusinessFactVerificationParsedResultRecord,
    BusinessFactVerificationRawResponseRecord,
    BusinessFactVerificationReconciliationRecord,
    BusinessFactVerificationRequestRecord,
    BusinessFactVerificationSourceContext,
    BoundBasis,
    ChallengerSet,
    CanonicalScoringParsedResultRecord,
    CanonicalScoringRawResponseRecord,
    CanonicalScoringReconciliationRecord,
    CanonicalScoringRequestRecord,
    Claim,
    CompanyAssessment,
    CompanyChainReceipt,
    CompanyExposure,
    CompanyProviderParsedResultRecord,
    CompanyProviderRawResponseRecord,
    CompanyProviderReconciliationRecord,
    CompanyProviderRequestRecord,
    CompanyScope,
    ConfirmationReceipt,
    CounterEvidenceCandidate,
    CounterSearchParsedResultRecord,
    CounterSearchRawResponseRecord,
    CounterSearchReconciliationReceipt,
    CounterSearchRequestRecord,
    CounterSearchReceipt,
    CounterSearchRouteFinding,
    DependencyEdge,
    DemandFrame,
    DimensionRatingDraft,
    EvidenceCard,
    FactVerificationReceipt,
    FeedbackCorrection,
    GateResult,
    HotspotCandidate,
    MilestoneAxes,
    MonitoringChange,
    MonitoringEvidence,
    MonitoringEvaluatorParsedResultRecord,
    MonitoringEvaluatorRawResponseRecord,
    MonitoringEvaluatorRequestRecord,
    MonitoringRefreshResult,
    MonitoringTrigger,
    ProductAnchor,
    RedTeamReview,
    ResearchRequest,
    RunStatus,
    SegmentCounterSearchRoute,
    SegmentCriticReceipt,
    SegmentAssessment,
    SegmentRecomputeRequest,
    ScoreFamilyAssessment,
    SourceSnapshot,
    Stage3Result,
    Stage4Result,
    Stage1RunSnapshot,
    SupplyChainGraph,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.fact_verification import (
    business_fact_assertion_sha256,
    business_fact_verification_source_context_from_json,
    business_fact_verification_source_context_json,
    business_fact_verification_source_context_sha256,
    parse_business_fact_verification_raw,
    validate_business_fact_verifier_input,
)
from event_collector.theme_chokepoint.relief import (
    ReliefHorizonAssessment,
    ReliefPeriodResult,
    ReliefScenarioResult,
)
from event_collector.theme_chokepoint.source_identity import (
    SourceIdentity,
    SourceProvenance,
    SourceRelation,
    SourceVersion,
    SourceUsage,
    StoredSourceIdentity,
    _controlled_source_resolution_execution,
    _is_controlled_source_identity,
    canonicalize_source_url,
    normalized_quote_sha256,
    normalized_source_content_sha256,
    resolve_source_identity,
)
from event_collector.theme_chokepoint.governance import (
    CANONICAL_GOVERNANCE_BUNDLE_ID,
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    CANONICAL_SOURCE_IDENTITY_POLICY_ID,
    CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
    RuntimeGovernanceBundle,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _source_identity_payload_from_row(row) -> dict:
    try:
        redirect_chain = json.loads(row["redirect_chain_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("stored source identity redirect chain is invalid") from error
    if not isinstance(redirect_chain, list):
        raise ValueError("stored source identity redirect chain is invalid")
    payload = {
        "canonical_publisher_id": row["canonical_publisher_id"],
        "canonical_document_id": row["canonical_document_id"],
        "origin_event_id": row["origin_event_id"],
        "canonical_url_key": row["canonical_url_key"],
        "redirect_chain": redirect_chain,
        "relation_type": row["relation_type"],
        "provenance_state": row["provenance_state"],
        "origin_canonical_url": row["origin_canonical_url"],
    }
    binding = {
        "policy_id": row["policy_id"],
        "policy_sha256": row["policy_sha256"],
        "governance_bundle_id": row["governance_bundle_id"],
        "governance_bundle_sha256": row["governance_bundle_sha256"],
        "resolver_receipt_id": row["resolver_receipt_id"],
        "resolver_receipt_sha256": row["resolver_receipt_sha256"],
    }
    if any(value is not None for value in binding.values()):
        payload.update(binding)
    return payload


def _source_resolution_receipt_is_valid(row) -> bool:
    """Recompute a persisted resolver execution without trusting identity fields."""
    required = (
        "resolution_receipt_id",
        "resolution_provider",
        "resolution_provider_trace_id",
        "resolution_raw_body",
        "resolution_raw_response_sha256",
        "resolution_policy_id",
        "resolution_policy_sha256",
        "resolution_governance_bundle_id",
        "resolution_governance_bundle_sha256",
        "resolution_receipt_sha256",
    )
    if any(row[name] is None for name in required):
        return False
    raw_body = bytes(row["resolution_raw_body"])
    raw_sha256 = sha256(raw_body).hexdigest()
    receipt_material = _json(
        {
            "provider": row["resolution_provider"],
            "provider_trace_id": row["resolution_provider_trace_id"],
            "raw_response_sha256": raw_sha256,
            "policy_id": row["resolution_policy_id"],
            "policy_sha256": row["resolution_policy_sha256"],
            "governance_bundle_id": row["resolution_governance_bundle_id"],
            "governance_bundle_sha256": row[
                "resolution_governance_bundle_sha256"
            ],
        }
    )
    receipt_sha256 = sha256(receipt_material.encode("utf-8")).hexdigest()
    if (
        raw_sha256 != row["resolution_raw_response_sha256"]
        or receipt_sha256 != row["resolution_receipt_sha256"]
        or row["resolution_receipt_id"]
        != "source_resolution_" + receipt_sha256[:24]
        or row["resolver_receipt_id"] != row["resolution_receipt_id"]
        or row["resolver_receipt_sha256"] != receipt_sha256
        or row["policy_id"] != row["resolution_policy_id"]
        or row["policy_sha256"] != row["resolution_policy_sha256"]
        or row["governance_bundle_id"]
        != row["resolution_governance_bundle_id"]
        or row["governance_bundle_sha256"]
        != row["resolution_governance_bundle_sha256"]
        or not 200 <= row["resolution_http_status"] < 300
    ):
        return False
    try:
        retrieved_at = datetime.fromisoformat(row["resolution_retrieved_at"])
        payload = json.loads(raw_body.decode("utf-8"))
        redirect_chain = json.loads(row["redirect_chain_json"])
        relation = SourceRelation(payload["relation"])
        provenance = SourceProvenance(payload["resolution_status"])
        observed_url = canonicalize_source_url(str(payload["observed_url"]))
        redirects = payload["redirect_chain"]
        if not isinstance(redirects, list) or not all(
            isinstance(item, str) and item.strip() for item in redirects
        ):
            return False
        normalized_redirects = [canonicalize_source_url(item) for item in redirects]
        terminal_url = canonicalize_source_url(str(payload["terminal_url"]))
        origin_value = payload.get("origin_url")
        origin_url = canonicalize_source_url(origin_value) if origin_value else None
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False
    expected_terminal = normalized_redirects[-1] if normalized_redirects else observed_url
    return bool(
        _is_aware(retrieved_at)
        and relation.value == row["relation_type"]
        and provenance.value == row["provenance_state"]
        and redirect_chain == [observed_url, *normalized_redirects]
        and terminal_url == expected_terminal == row["canonical_url_key"]
        and origin_url == row["origin_canonical_url"]
        and not (relation is SourceRelation.ORIGINAL and normalized_redirects)
        and not (relation is SourceRelation.REDIRECT and not normalized_redirects)
        and not (
            provenance is SourceProvenance.VERIFIED
            and relation in {SourceRelation.ALIAS, SourceRelation.REPRINT}
            and not origin_url
        )
        and not (
            provenance is SourceProvenance.VERIFIED
            and relation is SourceRelation.ORIGINAL
            and origin_url not in {None, observed_url}
        )
    )


_COMPANY_PROVIDER_ROLES = ("discovery", "evidence", "scoring", "critic")


def _company_provider_table(role: str, suffix: str) -> str:
    if role not in _COMPANY_PROVIDER_ROLES:
        raise ValueError("company provider role is invalid")
    if suffix not in {"requests", "raw_responses", "results", "reconciliations"}:
        raise ValueError("company provider record kind is invalid")
    return f"theme_chokepoint_company_{role}_{suffix}"


def _counter_candidates_from_raw(raw_body: bytes) -> tuple[CounterEvidenceCandidate, ...]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        return tuple(
            CounterEvidenceCandidate(
                canonical_url=str(item["canonical_url"]),
                original_text=str(item["original_text"]),
                exact_quote=str(item["exact_quote"]),
                claim_statement=str(item["claim_statement"]),
                source_title=str(item.get("source_title", "")),
                publisher=str(item.get("publisher", "")),
                source_type=str(item.get("source_type", "counter_search")),
            )
            for item in payload.get("counter_evidence", ())
        )
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("counter-search raw materialization payload is invalid") from error


class ThemeChokepointRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS theme_chokepoint_runs (
                    run_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    demand_frame_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_by TEXT,
                    confirmed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_product_anchors (
                    anchor_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    ordinal INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    buyer_or_user TEXT NOT NULL,
                    demand_variable TEXT NOT NULL,
                    theme_link TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    supporting_evidence_ids_json TEXT NOT NULL,
                    missing_evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE(run_id, ordinal)
                );

                CREATE INDEX IF NOT EXISTS idx_theme_anchor_run
                ON theme_chokepoint_product_anchors(run_id, ordinal);

                CREATE TABLE IF NOT EXISTS theme_chokepoint_graphs (
                    run_id TEXT PRIMARY KEY REFERENCES theme_chokepoint_runs(run_id),
                    truncation_reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_nodes (
                    node_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    ordinal INTEGER NOT NULL,
                    normalized_name TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    product_anchor_id TEXT,
                    stop_reason TEXT,
                    UNIQUE(run_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_edges (
                    edge_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    ordinal INTEGER NOT NULL,
                    downstream_node_id TEXT NOT NULL REFERENCES theme_chokepoint_nodes(node_id),
                    upstream_node_id TEXT NOT NULL REFERENCES theme_chokepoint_nodes(node_id),
                    relation_type TEXT NOT NULL,
                    demand_transmission TEXT NOT NULL,
                    criticality_hypothesis TEXT NOT NULL,
                    substitute_hypothesis TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    supporting_claim_ids_json TEXT NOT NULL,
                    verification_questions_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE(run_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_stage3_results (
                    run_id TEXT PRIMARY KEY REFERENCES theme_chokepoint_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_stage4_results (
                    run_id TEXT PRIMARY KEY REFERENCES theme_chokepoint_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_feedback (
                    correction_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    proposed_value TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_theme_feedback_run
                ON theme_chokepoint_feedback(run_id, created_at, correction_id);

                CREATE TABLE IF NOT EXISTS theme_chokepoint_hotspots (
                    hotspot_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitoring_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitoring_refreshes (
                    refresh_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitoring_evidence (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_search_requests (
                    request_record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    assessment_as_of TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_provider_executions (
                    execution_claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_identity TEXT NOT NULL,
                    provider_account_or_route_identity TEXT NOT NULL,
                    upstream_trace_id TEXT NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    consumer_kind TEXT NOT NULL,
                    consumer_record_id TEXT NOT NULL UNIQUE,
                    claimed_at TEXT NOT NULL,
                    UNIQUE(provider_identity, upstream_trace_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_theme_chokepoint_provider_raw_execution
                    ON theme_chokepoint_provider_executions(
                        provider_identity, raw_response_sha256
                    );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_search_raw_responses (
                    response_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_requests(request_record_id),
                    run_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cost_usd REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_search_results (
                    result_record_id TEXT PRIMARY KEY,
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_raw_responses(response_record_id),
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_requests(request_record_id),
                    run_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    new_counter_evidence_ids_json TEXT NOT NULL,
                    finding TEXT NOT NULL,
                    query_log_id TEXT NOT NULL,
                    result_semantics TEXT NOT NULL,
                    parsed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_source_versions (
                    source_version_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_results(result_record_id),
                    canonical_url TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    UNIQUE(run_id, segment_id, route_id, canonical_url, content_sha256)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_claims (
                    claim_id TEXT PRIMARY KEY,
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_results(result_record_id),
                    source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_source_versions(source_version_id),
                    run_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    statement_sha256 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_evidence_cards (
                    evidence_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_claims(claim_id),
                    source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_source_versions(source_version_id),
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_results(result_record_id),
                    run_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    assessment_scope_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(result_record_id, evidence_id)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_counter_search_reconciliations (
                    receipt_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_raw_responses(response_record_id),
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_counter_search_results(result_record_id),
                    run_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    assessment_scope_sha256 TEXT NOT NULL,
                    coverage_state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL,
                    UNIQUE(result_record_id)
                );
                
                CREATE TABLE IF NOT EXISTS theme_chokepoint_fact_verification_requests (
                    request_record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    assertion_sha256 TEXT NOT NULL,
                    governance_bundle_sha256 TEXT NOT NULL,
                    producer_execution_id TEXT NOT NULL,
                    verifier_execution_id TEXT NOT NULL,
                    source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
                    source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_source_versions(source_version_id),
                    source_context_json TEXT NOT NULL,
                    source_context_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK(producer_execution_id <> verifier_execution_id)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_fact_verification_raw_responses (
                    response_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_requests(request_record_id),
                    run_id TEXT NOT NULL,
                    verifier TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cost_usd REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_fact_verification_results (
                    result_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_raw_responses(response_record_id),
                    run_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    assertion_sha256 TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    parsed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_fact_verification_reconciliations (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_raw_responses(response_record_id),
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_fact_verification_results(result_record_id),
                    governance_bundle_sha256 TEXT NOT NULL,
                    source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
                    source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_source_versions(source_version_id),
                    source_context_sha256 TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL,
                    UNIQUE(run_id, assertion_id, governance_bundle_sha256)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitor_evaluator_requests (
                    request_record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    evidence_event_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitor_evaluator_raw_responses (
                    response_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_monitor_evaluator_requests(request_record_id),
                    run_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cost_usd REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_monitor_evaluator_results (
                    result_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_monitor_evaluator_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_monitor_evaluator_raw_responses(response_record_id),
                    run_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    parsed_payload_json TEXT NOT NULL,
                    parsed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_source_resolution_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    governance_bundle_id TEXT NOT NULL,
                    governance_bundle_sha256 TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(provider, provider_trace_id),
                    UNIQUE(provider, raw_response_sha256)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_theme_source_resolution_provider_trace
                ON theme_chokepoint_source_resolution_receipts(provider, provider_trace_id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_theme_source_resolution_provider_raw
                ON theme_chokepoint_source_resolution_receipts(provider, raw_response_sha256);

                CREATE TABLE IF NOT EXISTS theme_chokepoint_source_identities (
                    source_identity_id TEXT PRIMARY KEY,
                    canonical_publisher_id TEXT NOT NULL,
                    canonical_document_id TEXT,
                    origin_event_id TEXT,
                    canonical_url_key TEXT NOT NULL,
                    redirect_terminal_identity_id TEXT,
                    redirect_chain_json TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    provenance_state TEXT NOT NULL,
                    origin_canonical_url TEXT,
                    policy_id TEXT,
                    policy_sha256 TEXT,
                    governance_bundle_id TEXT,
                    governance_bundle_sha256 TEXT,
                    resolver_receipt_id TEXT UNIQUE REFERENCES theme_chokepoint_source_resolution_receipts(receipt_id),
                    resolver_receipt_sha256 TEXT,
                    identity_payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(canonical_publisher_id, canonical_document_id, canonical_url_key, relation_type)
                );

                CREATE INDEX IF NOT EXISTS idx_theme_source_identity_url
                ON theme_chokepoint_source_identities(canonical_url_key, relation_type);

                CREATE TABLE IF NOT EXISTS theme_chokepoint_source_versions (
                    source_version_id TEXT PRIMARY KEY,
                    source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
                    retrieved_at TEXT NOT NULL,
                    raw_bytes BLOB NOT NULL,
                    raw_bytes_sha256 TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    normalized_content_sha256 TEXT NOT NULL,
                    quote_span_json TEXT,
                    quote_span_sha256 TEXT,
                    content_type TEXT NOT NULL,
                    language TEXT NOT NULL,
                    publication_time TEXT,
                    updated_time TEXT,
                    version_sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_identity_id, version_sequence)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_source_usages (
                    usage_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    source_snapshot_article_id TEXT NOT NULL,
                    source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
                    source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_source_versions(source_version_id),
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, stage, source_snapshot_article_id)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_canonical_scoring_requests (
                    request_record_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    evaluator_result_record_id TEXT NOT NULL,
                    expected_head_revision_id TEXT NOT NULL,
                    request_payload_json TEXT NOT NULL,
                    request_payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_canonical_scoring_raw_responses (
                    response_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_requests(request_record_id),
                    operation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cost_usd REAL NOT NULL,
                    UNIQUE(provider, provider_trace_id)
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_canonical_scoring_results (
                    result_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_raw_responses(response_record_id),
                    operation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    parsed_payload_json TEXT NOT NULL,
                    parsed_payload_sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    parsed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_canonical_scoring_reconciliations (
                    receipt_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_requests(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_raw_responses(response_record_id),
                    result_record_id TEXT NOT NULL REFERENCES theme_chokepoint_canonical_scoring_results(result_record_id),
                    operation_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_assessment_revisions (
                    revision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    parent_revision_id TEXT,
                    result_reconciliation_id TEXT,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_chokepoint_assessment_heads (
                    run_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL REFERENCES theme_chokepoint_assessment_revisions(revision_id),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, target_type, target_id)
                );
                """
            )
            self._initialize_company_provider_tables(connection)
            for column in (
                "policy_id",
                "policy_sha256",
                "governance_bundle_id",
                "governance_bundle_sha256",
                "resolver_receipt_id",
                "resolver_receipt_sha256",
            ):
                self._ensure_column(
                    connection,
                    "theme_chokepoint_source_identities",
                    column,
                    "TEXT",
                )
            self._ensure_column(
                connection,
                "theme_chokepoint_counter_search_requests",
                "segment_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "theme_chokepoint_counter_search_requests",
                "assessment_scope_sha256",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "theme_chokepoint_counter_search_results",
                "coverage_state",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._ensure_column(
                connection,
                "theme_chokepoint_counter_search_results",
                "segment_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            for column in (
                "source_identity_id",
                "source_version_id",
                "source_context_json",
                "source_context_sha256",
            ):
                self._ensure_column(
                    connection,
                    "theme_chokepoint_fact_verification_requests",
                    column,
                    "TEXT NOT NULL DEFAULT ''",
                )
            for column in (
                "source_identity_id",
                "source_version_id",
                "source_context_sha256",
            ):
                self._ensure_column(
                    connection,
                    "theme_chokepoint_fact_verification_reconciliations",
                    column,
                    "TEXT NOT NULL DEFAULT ''",
                )
            self._ensure_column(
                connection,
                "theme_chokepoint_counter_search_results",
                "assessment_scope_sha256",
                "TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _initialize_company_provider_tables(connection: sqlite3.Connection) -> None:
        for role in _COMPANY_PROVIDER_ROLES:
            requests = _company_provider_table(role, "requests")
            raw_responses = _company_provider_table(role, "raw_responses")
            results = _company_provider_table(role, "results")
            reconciliations = _company_provider_table(role, "reconciliations")
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {requests} (
                    request_record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    company_id TEXT,
                    request_payload_json TEXT NOT NULL,
                    request_payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS {raw_responses} (
                    response_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES {requests}(request_record_id),
                    run_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_trace_id TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_response_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cost_usd REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS {results} (
                    result_record_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES {requests}(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES {raw_responses}(response_record_id),
                    run_id TEXT NOT NULL,
                    parsed_payload_json TEXT NOT NULL,
                    parsed_payload_sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    parsed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS {reconciliations} (
                    receipt_id TEXT PRIMARY KEY,
                    request_record_id TEXT NOT NULL REFERENCES {requests}(request_record_id),
                    response_record_id TEXT NOT NULL REFERENCES {raw_responses}(response_record_id),
                    result_record_id TEXT NOT NULL REFERENCES {results}(result_record_id),
                    run_id TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL,
                    UNIQUE(result_record_id)
                );
                """
            )

    @staticmethod
    def _ensure_column(connection, table, column, definition) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_company_provider_request(
        self, record: CompanyProviderRequestRecord
    ) -> CompanyProviderRequestRecord:
        _company_provider_table(record.role, "requests")
        if (
            not record.request_record_id.strip()
            or not record.run_id.strip()
            or (record.company_id is not None and not record.company_id.strip())
            or not record.request_payload_json
            or not _is_aware(record.created_at)
        ):
            raise ValueError("company provider request is incomplete")
        try:
            json.loads(record.request_payload_json)
        except json.JSONDecodeError as error:
            raise ValueError("company provider request payload is invalid JSON") from error
        actual_sha256 = sha256(record.request_payload_json.encode("utf-8")).hexdigest()
        if record.request_payload_sha256 != actual_sha256:
            raise ValueError("company provider request SHA-256 mismatch")
        table = _company_provider_table(record.role, "requests")
        with self._connect() as connection:
            connection.execute(
                f"""INSERT INTO {table}(
                    request_record_id, run_id, company_id,
                    request_payload_json, request_payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.request_record_id,
                    record.run_id,
                    record.company_id,
                    record.request_payload_json,
                    record.request_payload_sha256,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def get_company_provider_request(
        self, *, role: str, request_record_id: str
    ) -> CompanyProviderRequestRecord:
        table = _company_provider_table(role, "requests")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE request_record_id = ?",
                (request_record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(request_record_id)
        return CompanyProviderRequestRecord(
            request_record_id=row["request_record_id"],
            run_id=row["run_id"],
            role=role,
            company_id=row["company_id"],
            request_payload_json=row["request_payload_json"],
            request_payload_sha256=row["request_payload_sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_company_provider_raw_response(
        self, record: CompanyProviderRawResponseRecord
    ) -> CompanyProviderRawResponseRecord:
        if (
            not all(
                value.strip()
                for value in (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.provider,
                    record.provider_trace_id,
                )
            )
            or not 200 <= record.http_status < 300
            or not record.raw_body
            or record.cost_usd < 0
            or not _is_aware(record.retrieved_at)
        ):
            raise ValueError("company provider raw response is incomplete")
        actual_sha256 = sha256(record.raw_body).hexdigest()
        if record.raw_response_sha256 != actual_sha256:
            raise ValueError("company provider raw response SHA-256 mismatch")
        try:
            json.loads(record.raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("company provider raw response is invalid JSON") from error
        requests = _company_provider_table(record.role, "requests")
        raw_responses = _company_provider_table(record.role, "raw_responses")
        with self._connect() as connection:
            request = connection.execute(
                f"SELECT run_id FROM {requests} WHERE request_record_id = ?",
                (record.request_record_id,),
            ).fetchone()
            if request is None or request["run_id"] != record.run_id:
                raise ValueError("company provider raw response lineage mismatch")
            self._claim_provider_execution(
                connection,
                provider_identity=record.provider,
                provider_account_or_route_identity=record.role,
                upstream_trace_id=record.provider_trace_id,
                raw_response_sha256=record.raw_response_sha256,
                raw_body=record.raw_body,
                consumer_kind=f"company_{record.role}",
                consumer_record_id=record.response_record_id,
                claimed_at=record.retrieved_at,
            )
            connection.execute(
                f"""INSERT INTO {raw_responses}(
                    response_record_id, request_record_id, run_id, provider,
                    provider_trace_id, http_status, raw_body,
                    raw_response_sha256, retrieved_at, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.provider,
                    record.provider_trace_id,
                    record.http_status,
                    record.raw_body,
                    record.raw_response_sha256,
                    record.retrieved_at.isoformat(),
                    record.cost_usd,
                ),
            )
        return record

    def save_company_provider_parsed_result(
        self, record: CompanyProviderParsedResultRecord
    ) -> CompanyProviderParsedResultRecord:
        if (
            not all(
                value.strip()
                for value in (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.run_id,
                    record.parsed_payload_json,
                    record.parser_version,
                )
            )
            or not _is_aware(record.parsed_at)
        ):
            raise ValueError("company provider parsed result is incomplete")
        try:
            json.loads(record.parsed_payload_json)
        except json.JSONDecodeError as error:
            raise ValueError("company provider parsed result is invalid JSON") from error
        actual_sha256 = sha256(record.parsed_payload_json.encode("utf-8")).hexdigest()
        if record.parsed_payload_sha256 != actual_sha256:
            raise ValueError("company provider parsed result SHA-256 mismatch")
        requests = _company_provider_table(record.role, "requests")
        raw_responses = _company_provider_table(record.role, "raw_responses")
        results = _company_provider_table(record.role, "results")
        with self._connect() as connection:
            lineage = connection.execute(
                f"""SELECT req.run_id AS request_run_id, raw.run_id AS response_run_id
                FROM {requests} AS req
                JOIN {raw_responses} AS raw
                  ON raw.request_record_id = req.request_record_id
                WHERE req.request_record_id = ? AND raw.response_record_id = ?""",
                (record.request_record_id, record.response_record_id),
            ).fetchone()
            if lineage is None or (
                lineage["request_run_id"], lineage["response_run_id"]
            ) != (record.run_id, record.run_id):
                raise ValueError("company provider parsed result lineage mismatch")
            connection.execute(
                f"""INSERT INTO {results}(
                    result_record_id, request_record_id, response_record_id,
                    run_id, parsed_payload_json, parsed_payload_sha256,
                    parser_version, parsed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.run_id,
                    record.parsed_payload_json,
                    record.parsed_payload_sha256,
                    record.parser_version,
                    record.parsed_at.isoformat(),
                ),
            )
        return record

    def reconcile_company_provider_execution(
        self, record: CompanyProviderReconciliationRecord
    ) -> CompanyProviderReconciliationRecord:
        if (
            not all(
                value.strip()
                for value in (
                    record.receipt_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                    record.run_id,
                )
            )
            or not _is_aware(record.reconciled_at)
        ):
            raise ValueError("company provider reconciliation is incomplete")
        requests = _company_provider_table(record.role, "requests")
        raw_responses = _company_provider_table(record.role, "raw_responses")
        results = _company_provider_table(record.role, "results")
        reconciliations = _company_provider_table(record.role, "reconciliations")
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT req.run_id AS request_run_id,
                           req.request_payload_json, req.request_payload_sha256,
                           raw.run_id AS response_run_id, raw.raw_body,
                           raw.raw_response_sha256,
                           result.run_id AS result_run_id,
                           result.parsed_payload_json,
                           result.parsed_payload_sha256
                FROM {requests} AS req
                JOIN {raw_responses} AS raw
                  ON raw.request_record_id = req.request_record_id
                JOIN {results} AS result
                  ON result.request_record_id = req.request_record_id
                 AND result.response_record_id = raw.response_record_id
                WHERE req.request_record_id = ?
                  AND raw.response_record_id = ?
                  AND result.result_record_id = ?""",
                (
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                ),
            ).fetchone()
            valid = row is not None and (
                row["request_run_id"],
                row["response_run_id"],
                row["result_run_id"],
            ) == (record.run_id, record.run_id, record.run_id)
            if valid:
                valid = (
                    sha256(row["request_payload_json"].encode("utf-8")).hexdigest()
                    == row["request_payload_sha256"]
                    and sha256(row["raw_body"]).hexdigest()
                    == row["raw_response_sha256"]
                    and sha256(row["parsed_payload_json"].encode("utf-8")).hexdigest()
                    == row["parsed_payload_sha256"]
                )
            if not valid:
                raise ValueError("company provider execution did not reconcile")
            connection.execute(
                f"""INSERT INTO {reconciliations}(
                    receipt_id, request_record_id, response_record_id,
                    result_record_id, run_id, reconciled_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.receipt_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                    record.run_id,
                    record.reconciled_at.isoformat(),
                ),
            )
        return record

    def list_company_provider_reconciliations(
        self, run_id: str
    ) -> tuple[CompanyProviderReconciliationRecord, ...]:
        records = []
        with self._connect() as connection:
            for role in _COMPANY_PROVIDER_ROLES:
                requests = _company_provider_table(role, "requests")
                raw_responses = _company_provider_table(role, "raw_responses")
                results = _company_provider_table(role, "results")
                table = _company_provider_table(role, "reconciliations")
                expected_count = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["count"]
                rows = connection.execute(
                    f"""SELECT rec.*,
                               req.run_id AS request_run_id,
                               req.request_payload_json,
                               req.request_payload_sha256,
                               raw.run_id AS response_run_id,
                               raw.raw_body, raw.raw_response_sha256,
                               result.run_id AS result_run_id,
                               result.parsed_payload_json,
                               result.parsed_payload_sha256
                        FROM {table} AS rec
                        JOIN {requests} AS req
                          ON req.request_record_id = rec.request_record_id
                        JOIN {raw_responses} AS raw
                          ON raw.response_record_id = rec.response_record_id
                         AND raw.request_record_id = req.request_record_id
                        JOIN {results} AS result
                          ON result.result_record_id = rec.result_record_id
                         AND result.request_record_id = req.request_record_id
                         AND result.response_record_id = raw.response_record_id
                        WHERE rec.run_id = ?
                        ORDER BY rec.reconciled_at, rec.receipt_id""",
                    (run_id,),
                ).fetchall()
                if len(rows) != expected_count:
                    raise ValueError("company provider reconciliation lineage is missing")
                for row in rows:
                    if (
                        (row["request_run_id"], row["response_run_id"], row["result_run_id"])
                        != (run_id, run_id, run_id)
                        or sha256(row["request_payload_json"].encode("utf-8")).hexdigest()
                        != row["request_payload_sha256"]
                        or sha256(row["raw_body"]).hexdigest()
                        != row["raw_response_sha256"]
                        or sha256(row["parsed_payload_json"].encode("utf-8")).hexdigest()
                        != row["parsed_payload_sha256"]
                    ):
                        raise ValueError("company provider reconciliation lineage is invalid")
                records.extend(
                    CompanyProviderReconciliationRecord(
                        receipt_id=row["receipt_id"],
                        request_record_id=row["request_record_id"],
                        response_record_id=row["response_record_id"],
                        result_record_id=row["result_record_id"],
                        run_id=row["run_id"],
                        role=role,
                        reconciled_at=datetime.fromisoformat(row["reconciled_at"]),
                    )
                    for row in rows
                )
        return tuple(records)

    def load_business_fact_verification_source_context(
        self,
        *,
        source_identity_id: str,
        source_version_id: str,
        source_type: str,
        issuer_company_id: str,
        product_id: str,
        fiscal_period_id: str | None,
        accounting_metric: str | None,
        quote_start: int,
        quote_end: int,
        exact_quote_sha256: str,
    ) -> BusinessFactVerificationSourceContext:
        text_values = (
            source_identity_id,
            source_version_id,
            source_type,
            issuer_company_id,
            product_id,
        )
        if not all(value.strip() for value in text_values):
            raise ValueError("business-fact source locator and scope are required")
        if any(
            value is not None and not value.strip()
            for value in (fiscal_period_id, accounting_metric)
        ):
            raise ValueError("business-fact accounting coordinates cannot be blank")
        if (
            not isinstance(quote_start, int)
            or not isinstance(quote_end, int)
            or quote_start < 0
            or quote_end <= quote_start
            or not _is_sha256(exact_quote_sha256)
        ):
            raise ValueError("business-fact exact quote coordinates are invalid")
        with self._connect() as connection:
            return self._load_business_fact_verification_source_context(
                connection,
                source_identity_id=source_identity_id,
                source_version_id=source_version_id,
                source_type=source_type,
                issuer_company_id=issuer_company_id,
                product_id=product_id,
                fiscal_period_id=fiscal_period_id,
                accounting_metric=accounting_metric,
                quote_start=quote_start,
                quote_end=quote_end,
                exact_quote_sha256=exact_quote_sha256,
            )

    @staticmethod
    def _load_business_fact_verification_source_context(
        connection: sqlite3.Connection,
        *,
        source_identity_id: str,
        source_version_id: str,
        source_type: str,
        issuer_company_id: str,
        product_id: str,
        fiscal_period_id: str | None,
        accounting_metric: str | None,
        quote_start: int,
        quote_end: int,
        exact_quote_sha256: str,
    ) -> BusinessFactVerificationSourceContext:
        row = connection.execute(
            """
            SELECT identity.*, version.source_version_id,
                   version.source_identity_id AS version_source_identity_id,
                   version.retrieved_at, version.raw_bytes,
                   version.raw_bytes_sha256, version.normalized_content,
                   version.normalized_content_sha256, version.quote_span_json,
                   version.quote_span_sha256, version.content_type,
                   version.language, version.publication_time,
                   version.updated_time, version.version_sequence
            FROM theme_chokepoint_source_identities AS identity
            JOIN theme_chokepoint_source_versions AS version
              ON version.source_identity_id = identity.source_identity_id
            WHERE identity.source_identity_id = ? AND version.source_version_id = ?
            """,
            (source_identity_id, source_version_id),
        ).fetchone()
        if row is None:
            raise ValueError("business-fact source identity/version lineage is missing")
        try:
            redirect_chain = json.loads(row["redirect_chain_json"])
            quote_span = json.loads(row["quote_span_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("business-fact source identity/version JSON is invalid") from error
        identity_payload = _source_identity_payload_from_row(row)
        identity_payload_sha256 = sha256(
            _json(identity_payload).encode("utf-8")
        ).hexdigest()
        raw_bytes = bytes(row["raw_bytes"])
        normalized_content = row["normalized_content"]
        if (
            not isinstance(redirect_chain, list)
            or row["version_source_identity_id"] != source_identity_id
            or row["identity_payload_sha256"] != identity_payload_sha256
            or source_identity_id != "source_identity_" + identity_payload_sha256[:24]
            or row["relation_type"] != SourceRelation.ORIGINAL.value
            or row["provenance_state"] != SourceProvenance.VERIFIED.value
            or row["policy_id"] != CANONICAL_SOURCE_IDENTITY_POLICY_ID
            or row["policy_sha256"] != CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
            or row["governance_bundle_id"] != CANONICAL_GOVERNANCE_BUNDLE_ID
            or row["governance_bundle_sha256"]
            != CANONICAL_GOVERNANCE_BUNDLE_SHA256
            or not isinstance(row["resolver_receipt_id"], str)
            or not row["resolver_receipt_id"].strip()
            or not _is_sha256(row["resolver_receipt_sha256"])
            or sha256(raw_bytes).hexdigest() != row["raw_bytes_sha256"]
            or normalized_source_content_sha256(normalized_content)
            != row["normalized_content_sha256"]
            or not isinstance(quote_span, list)
            or len(quote_span) != 3
        ):
            raise ValueError("business-fact source identity/version lineage is invalid")
        stored_start, stored_end, exact_quote = quote_span
        version_material = _json(
            {
                "source_identity_id": source_identity_id,
                "retrieved_at": row["retrieved_at"],
                "raw_bytes_sha256": row["raw_bytes_sha256"],
                "normalized_content_sha256": row["normalized_content_sha256"],
                "quote_span_sha256": row["quote_span_sha256"],
                "version_sequence": row["version_sequence"],
            }
        )
        expected_version_id = "source_version_" + sha256(
            version_material.encode("utf-8")
        ).hexdigest()[:24]
        if (
            source_version_id != expected_version_id
            or stored_start != quote_start
            or stored_end != quote_end
            or not isinstance(exact_quote, str)
            or normalized_content[stored_start:stored_end] != exact_quote
            or normalized_quote_sha256(exact_quote) != row["quote_span_sha256"]
            or sha256(exact_quote.encode("utf-8")).hexdigest()
            != exact_quote_sha256
        ):
            raise ValueError("business-fact source quote lineage is invalid")
        return BusinessFactVerificationSourceContext(
            source_identity_id=source_identity_id,
            source_version_id=source_version_id,
            canonical_url=row["canonical_url_key"],
            canonical_publisher_id=row["canonical_publisher_id"],
            canonical_document_id=row["canonical_document_id"],
            origin_event_id=row["origin_event_id"],
            source_relation=row["relation_type"],
            source_provenance=row["provenance_state"],
            source_identity_policy_id=row["policy_id"],
            source_identity_policy_sha256=row["policy_sha256"],
            source_governance_bundle_id=row["governance_bundle_id"],
            source_governance_bundle_sha256=row["governance_bundle_sha256"],
            source_resolver_receipt_id=row["resolver_receipt_id"],
            source_resolver_receipt_sha256=row["resolver_receipt_sha256"],
            source_type=source_type.strip().casefold(),
            version_sequence=row["version_sequence"],
            original_document_bytes=raw_bytes,
            original_document_text=normalized_content,
            raw_bytes_sha256=row["raw_bytes_sha256"],
            normalized_content_sha256=row["normalized_content_sha256"],
            quote_start=stored_start,
            quote_end=stored_end,
            exact_quote=exact_quote,
            exact_quote_sha256=exact_quote_sha256,
            issuer_company_id=issuer_company_id,
            product_id=product_id,
            fiscal_period_id=fiscal_period_id,
            accounting_metric=accounting_metric,
        )

    def save_business_fact_verification_request(
        self, record: BusinessFactVerificationRequestRecord
    ) -> BusinessFactVerificationRequestRecord:
        text_values = (
            record.request_record_id,
            record.run_id,
            record.assertion_id,
            record.evidence_id,
            record.producer_execution_id,
            record.verifier_execution_id,
        )
        if not all(value.strip() for value in text_values):
            raise ValueError("business-fact verification request is incomplete")
        if not _is_sha256(record.assertion_sha256) or not _is_sha256(
            record.governance_bundle_sha256
        ) or not _is_sha256(record.source_context_sha256):
            raise ValueError("business-fact verification request SHA-256 is invalid")
        if record.producer_execution_id == record.verifier_execution_id:
            raise ValueError("business-fact verifier must be independent from producer")
        if not _is_aware(record.created_at):
            raise ValueError("business-fact verification request time must be timezone-aware")
        if not isinstance(record.source_context, BusinessFactVerificationSourceContext):
            raise ValueError("business-fact verification source context is required")
        actual_source_context_sha256 = (
            business_fact_verification_source_context_sha256(record.source_context)
        )
        if record.source_context_sha256 != actual_source_context_sha256:
            raise ValueError("business-fact verification source context SHA-256 mismatch")
        source_context_json = business_fact_verification_source_context_json(
            record.source_context
        )
        with self._connect() as connection:
            reloaded_source_context = (
                self._load_business_fact_verification_source_context(
                    connection,
                    source_identity_id=record.source_context.source_identity_id,
                    source_version_id=record.source_context.source_version_id,
                    source_type=record.source_context.source_type,
                    issuer_company_id=record.source_context.issuer_company_id,
                    product_id=record.source_context.product_id,
                    fiscal_period_id=record.source_context.fiscal_period_id,
                    accounting_metric=record.source_context.accounting_metric,
                    quote_start=record.source_context.quote_start,
                    quote_end=record.source_context.quote_end,
                    exact_quote_sha256=record.source_context.exact_quote_sha256,
                )
            )
            if reloaded_source_context != record.source_context:
                raise ValueError("business-fact verification source context drifted")
            connection.execute(
                """
                INSERT INTO theme_chokepoint_fact_verification_requests(
                    request_record_id, run_id, assertion_id, evidence_id,
                    assertion_sha256, governance_bundle_sha256,
                    producer_execution_id, verifier_execution_id,
                    source_identity_id, source_version_id,
                    source_context_json, source_context_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.request_record_id,
                    record.run_id,
                    record.assertion_id,
                    record.evidence_id,
                    record.assertion_sha256,
                    record.governance_bundle_sha256,
                    record.producer_execution_id,
                    record.verifier_execution_id,
                    record.source_context.source_identity_id,
                    record.source_context.source_version_id,
                    source_context_json,
                    record.source_context_sha256,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def save_business_fact_verification_raw_response(
        self, record: BusinessFactVerificationRawResponseRecord
    ) -> BusinessFactVerificationRawResponseRecord:
        if (
            not all(
                value.strip()
                for value in (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.verifier,
                    record.provider_trace_id,
                )
            )
            or not 200 <= record.http_status < 300
            or not record.raw_body
            or record.cost_usd < 0
            or not _is_aware(record.retrieved_at)
        ):
            raise ValueError("business-fact verifier raw response is incomplete")
        actual_sha256 = sha256(record.raw_body).hexdigest()
        if record.raw_response_sha256 != actual_sha256:
            raise ValueError("business-fact verifier raw response SHA-256 mismatch")
        parse_business_fact_verification_raw(record.raw_body)
        self.get_business_fact_verification_request(record.request_record_id)
        with self._connect() as connection:
            request = connection.execute(
                """
                SELECT run_id, verifier_execution_id
                FROM theme_chokepoint_fact_verification_requests
                WHERE request_record_id = ?
                """,
                (record.request_record_id,),
            ).fetchone()
            if request is None:
                raise ValueError("business-fact verification request is missing")
            if (
                request["run_id"] != record.run_id
                or request["verifier_execution_id"] != record.verifier
            ):
                raise ValueError("business-fact verifier raw response lineage mismatch")
            self._claim_provider_execution(
                connection,
                provider_identity=record.verifier,
                provider_account_or_route_identity=record.verifier,
                upstream_trace_id=record.provider_trace_id,
                raw_response_sha256=record.raw_response_sha256,
                raw_body=record.raw_body,
                consumer_kind="business_fact_verification",
                consumer_record_id=record.response_record_id,
                claimed_at=record.retrieved_at,
            )
            connection.execute(
                """
                INSERT INTO theme_chokepoint_fact_verification_raw_responses(
                    response_record_id, request_record_id, run_id, verifier,
                    provider_trace_id, http_status, raw_body,
                    raw_response_sha256, retrieved_at, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.verifier,
                    record.provider_trace_id,
                    record.http_status,
                    record.raw_body,
                    record.raw_response_sha256,
                    record.retrieved_at.isoformat(),
                    record.cost_usd,
                ),
            )
        return record

    def get_business_fact_verification_request(
        self, request_record_id: str
    ) -> BusinessFactVerificationRequestRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM theme_chokepoint_fact_verification_requests
                WHERE request_record_id = ?
                """,
                (request_record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(request_record_id)
        record = BusinessFactVerificationRequestRecord(
            request_record_id=row["request_record_id"],
            run_id=row["run_id"],
            assertion_id=row["assertion_id"],
            evidence_id=row["evidence_id"],
            assertion_sha256=row["assertion_sha256"],
            governance_bundle_sha256=row["governance_bundle_sha256"],
            producer_execution_id=row["producer_execution_id"],
            verifier_execution_id=row["verifier_execution_id"],
            source_context=business_fact_verification_source_context_from_json(
                row["source_context_json"]
            ),
            source_context_sha256=row["source_context_sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        if (
            business_fact_verification_source_context_sha256(record.source_context)
            != record.source_context_sha256
            or record.source_context.source_identity_id != row["source_identity_id"]
            or record.source_context.source_version_id != row["source_version_id"]
        ):
            raise ValueError("business-fact verification request source context is invalid")
        reloaded = self.load_business_fact_verification_source_context(
            source_identity_id=record.source_context.source_identity_id,
            source_version_id=record.source_context.source_version_id,
            source_type=record.source_context.source_type,
            issuer_company_id=record.source_context.issuer_company_id,
            product_id=record.source_context.product_id,
            fiscal_period_id=record.source_context.fiscal_period_id,
            accounting_metric=record.source_context.accounting_metric,
            quote_start=record.source_context.quote_start,
            quote_end=record.source_context.quote_end,
            exact_quote_sha256=record.source_context.exact_quote_sha256,
        )
        if reloaded != record.source_context:
            raise ValueError("business-fact verification request source lineage drifted")
        return record

    def save_business_fact_verification_result(
        self, record: BusinessFactVerificationParsedResultRecord
    ) -> BusinessFactVerificationParsedResultRecord:
        if (
            not all(
                value.strip()
                for value in (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.run_id,
                    record.assertion_id,
                )
            )
            or not _is_sha256(record.assertion_sha256)
            or record.decision not in {"verified", "rejected", "unknown", "conflicted"}
            or not _is_aware(record.parsed_at)
        ):
            raise ValueError("business-fact verification result is incomplete")
        self.get_business_fact_verification_request(record.request_record_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT req.run_id, req.assertion_id, req.assertion_sha256,
                       raw.raw_body
                FROM theme_chokepoint_fact_verification_requests AS req
                JOIN theme_chokepoint_fact_verification_raw_responses AS raw
                  ON raw.request_record_id = req.request_record_id
                WHERE req.request_record_id = ? AND raw.response_record_id = ?
                """,
                (record.request_record_id, record.response_record_id),
            ).fetchone()
            if row is None:
                raise ValueError("business-fact verification raw lineage is missing")
            raw_payload = parse_business_fact_verification_raw(row["raw_body"])
            expected = (
                row["run_id"],
                row["assertion_id"],
                row["assertion_sha256"],
                raw_payload["assertion_id"],
                raw_payload["assertion_sha256"],
                raw_payload["decision"],
            )
            actual = (
                record.run_id,
                record.assertion_id,
                record.assertion_sha256,
                record.assertion_id,
                record.assertion_sha256,
                record.decision,
            )
            if expected != actual:
                raise ValueError("business-fact verification parsed result mismatch")
            connection.execute(
                """
                INSERT INTO theme_chokepoint_fact_verification_results(
                    result_record_id, request_record_id, response_record_id,
                    run_id, assertion_id, assertion_sha256, decision, parsed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.run_id,
                    record.assertion_id,
                    record.assertion_sha256,
                    record.decision,
                    record.parsed_at.isoformat(),
                ),
            )
        return record

    def reconcile_business_fact_verification(
        self,
        record: BusinessFactVerificationReconciliationRecord,
        *,
        assertion: BusinessFactAssertion,
    ) -> BusinessFactVerificationReconciliationRecord:
        if not _is_aware(record.reconciled_at):
            raise ValueError("business-fact reconciliation time must be timezone-aware")
        if not _is_sha256(record.governance_bundle_sha256):
            raise ValueError("business-fact reconciliation governance SHA-256 is invalid")
        if not _is_sha256(record.source_context_sha256):
            raise ValueError("business-fact reconciliation source SHA-256 is invalid")
        if not record.source_identity_id.strip() or not record.source_version_id.strip():
            raise ValueError("business-fact reconciliation source lineage is required")
        assertion_sha256 = business_fact_assertion_sha256(assertion)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT req.run_id, req.assertion_id, req.evidence_id,
                       req.assertion_sha256, req.governance_bundle_sha256,
                       req.producer_execution_id, req.verifier_execution_id,
                       req.source_identity_id, req.source_version_id,
                       req.source_context_json, req.source_context_sha256,
                       raw.response_record_id, raw.verifier, raw.raw_body,
                       raw.raw_response_sha256,
                       result.result_record_id, result.assertion_id AS result_assertion_id,
                       result.assertion_sha256 AS result_assertion_sha256,
                       result.decision
                FROM theme_chokepoint_fact_verification_requests AS req
                JOIN theme_chokepoint_fact_verification_raw_responses AS raw
                  ON raw.request_record_id = req.request_record_id
                JOIN theme_chokepoint_fact_verification_results AS result
                  ON result.request_record_id = req.request_record_id
                 AND result.response_record_id = raw.response_record_id
                WHERE req.request_record_id = ?
                  AND raw.response_record_id = ?
                  AND result.result_record_id = ?
                """,
                (
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("business-fact reconciliation lineage is incomplete")
            raw_payload = parse_business_fact_verification_raw(row["raw_body"])
            source_context = business_fact_verification_source_context_from_json(
                row["source_context_json"]
            )
            persisted_source_context = (
                self._load_business_fact_verification_source_context(
                    connection,
                    source_identity_id=row["source_identity_id"],
                    source_version_id=row["source_version_id"],
                    source_type=source_context.source_type,
                    issuer_company_id=source_context.issuer_company_id,
                    product_id=source_context.product_id,
                    fiscal_period_id=source_context.fiscal_period_id,
                    accounting_metric=source_context.accounting_metric,
                    quote_start=source_context.quote_start,
                    quote_end=source_context.quote_end,
                    exact_quote_sha256=source_context.exact_quote_sha256,
                )
            )
            validate_business_fact_verifier_input(assertion, source_context)
            valid = (
                row["run_id"] == record.run_id
                and row["assertion_id"] == record.assertion_id == assertion.assertion_id
                and row["evidence_id"] == assertion.evidence_id
                and row["assertion_sha256"] == assertion_sha256
                and row["governance_bundle_sha256"]
                == record.governance_bundle_sha256
                and row["source_context_sha256"]
                == record.source_context_sha256
                and row["source_identity_id"]
                == record.source_identity_id
                == source_context.source_identity_id
                and row["source_version_id"]
                == record.source_version_id
                == source_context.source_version_id
                and business_fact_verification_source_context_sha256(source_context)
                == row["source_context_sha256"]
                and persisted_source_context == source_context
                and row["producer_execution_id"] != row["verifier_execution_id"]
                and row["verifier_execution_id"] == row["verifier"]
                and sha256(row["raw_body"]).hexdigest()
                == row["raw_response_sha256"]
                and row["result_assertion_id"] == assertion.assertion_id
                and row["result_assertion_sha256"] == assertion_sha256
                and row["decision"] == "verified"
                and raw_payload
                == {
                    "assertion_id": assertion.assertion_id,
                    "assertion_sha256": assertion_sha256,
                    "decision": "verified",
                }
            )
            if not valid:
                raise ValueError("business-fact verification did not reconcile")
            connection.execute(
                """
                INSERT INTO theme_chokepoint_fact_verification_reconciliations(
                    receipt_id, run_id, assertion_id, request_record_id,
                    response_record_id, result_record_id,
                    governance_bundle_sha256, source_identity_id,
                    source_version_id, source_context_sha256, reconciled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.receipt_id,
                    record.run_id,
                    record.assertion_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                    record.governance_bundle_sha256,
                    record.source_identity_id,
                    record.source_version_id,
                    record.source_context_sha256,
                    record.reconciled_at.isoformat(),
                ),
            )
        return record

    def business_fact_assertion_is_verified(
        self,
        *,
        run_id: str,
        assertion: BusinessFactAssertion,
        governance_bundle_sha256: str,
    ) -> bool:
        assertion_sha256 = business_fact_assertion_sha256(assertion)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT req.evidence_id, req.assertion_sha256,
                       req.governance_bundle_sha256,
                       req.producer_execution_id, req.verifier_execution_id,
                       req.source_identity_id, req.source_version_id,
                       req.source_context_json, req.source_context_sha256,
                       rec.source_identity_id AS rec_source_identity_id,
                       rec.source_version_id AS rec_source_version_id,
                       rec.source_context_sha256 AS rec_source_context_sha256,
                       raw.verifier, raw.raw_body, raw.raw_response_sha256,
                       result.assertion_id AS result_assertion_id,
                       result.assertion_sha256 AS result_assertion_sha256,
                       result.decision
                FROM theme_chokepoint_fact_verification_reconciliations AS rec
                JOIN theme_chokepoint_fact_verification_requests AS req
                  ON req.request_record_id = rec.request_record_id
                JOIN theme_chokepoint_fact_verification_raw_responses AS raw
                  ON raw.response_record_id = rec.response_record_id
                 AND raw.request_record_id = req.request_record_id
                JOIN theme_chokepoint_fact_verification_results AS result
                  ON result.result_record_id = rec.result_record_id
                 AND result.request_record_id = req.request_record_id
                 AND result.response_record_id = raw.response_record_id
                WHERE rec.run_id = ? AND rec.assertion_id = ?
                  AND rec.governance_bundle_sha256 = ?
                """,
                (run_id, assertion.assertion_id, governance_bundle_sha256),
            ).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        try:
            raw_payload = parse_business_fact_verification_raw(row["raw_body"])
            source_context = business_fact_verification_source_context_from_json(
                row["source_context_json"]
            )
            persisted_source_context = self.load_business_fact_verification_source_context(
                source_identity_id=row["source_identity_id"],
                source_version_id=row["source_version_id"],
                source_type=source_context.source_type,
                issuer_company_id=source_context.issuer_company_id,
                product_id=source_context.product_id,
                fiscal_period_id=source_context.fiscal_period_id,
                accounting_metric=source_context.accounting_metric,
                quote_start=source_context.quote_start,
                quote_end=source_context.quote_end,
                exact_quote_sha256=source_context.exact_quote_sha256,
            )
            validate_business_fact_verifier_input(assertion, source_context)
        except (KeyError, ValueError):
            return False
        return (
            row["evidence_id"] == assertion.evidence_id
            and row["assertion_sha256"] == assertion_sha256
            and row["governance_bundle_sha256"] == governance_bundle_sha256
            and row["source_context_sha256"] == row["rec_source_context_sha256"]
            and row["source_identity_id"] == row["rec_source_identity_id"]
            and row["source_version_id"] == row["rec_source_version_id"]
            and business_fact_verification_source_context_sha256(source_context)
            == row["source_context_sha256"]
            and persisted_source_context == source_context
            and row["producer_execution_id"] != row["verifier_execution_id"]
            and row["verifier_execution_id"] == row["verifier"]
            and sha256(row["raw_body"]).hexdigest() == row["raw_response_sha256"]
            and row["result_assertion_id"] == assertion.assertion_id
            and row["result_assertion_sha256"] == assertion_sha256
            and row["decision"] == "verified"
            and raw_payload
            == {
                "assertion_id": assertion.assertion_id,
                "assertion_sha256": assertion_sha256,
                "decision": "verified",
            }
        )

    def export_business_fact_verification_ledger(
        self,
        *,
        run_id: str,
        evidence_pack_id: str,
        governance_bundle_sha256: str,
    ) -> dict:
        """Export only repository-reconciled verifier lineage for the Node evaluator."""
        if not run_id.strip() or not evidence_pack_id.strip():
            raise ValueError("verification ledger run and evidence-pack identities are required")
        if not _is_sha256(governance_bundle_sha256):
            raise ValueError("verification ledger governance SHA-256 is invalid")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    req.request_record_id, req.run_id, req.assertion_id,
                    req.evidence_id, req.assertion_sha256,
                    req.governance_bundle_sha256,
                    req.producer_execution_id, req.verifier_execution_id,
                    req.source_identity_id, req.source_version_id,
                    req.source_context_json, req.source_context_sha256,
                    raw.response_record_id,
                    raw.request_record_id AS raw_request_record_id,
                    raw.run_id AS raw_run_id, raw.verifier, raw.provider_trace_id,
                    raw.http_status, raw.raw_body, raw.raw_response_sha256,
                    result.result_record_id,
                    result.request_record_id AS result_request_record_id,
                    result.response_record_id AS result_response_record_id,
                    result.run_id AS result_run_id,
                    result.assertion_id AS result_assertion_id,
                    result.assertion_sha256 AS result_assertion_sha256,
                    result.decision,
                    rec.receipt_id,
                    rec.request_record_id AS rec_request_record_id,
                    rec.response_record_id AS rec_response_record_id,
                    rec.result_record_id AS rec_result_record_id,
                    rec.run_id AS rec_run_id,
                    rec.assertion_id AS rec_assertion_id,
                    rec.governance_bundle_sha256 AS rec_governance_bundle_sha256,
                    rec.source_identity_id AS rec_source_identity_id,
                    rec.source_version_id AS rec_source_version_id,
                    rec.source_context_sha256 AS rec_source_context_sha256
                FROM theme_chokepoint_fact_verification_reconciliations AS rec
                JOIN theme_chokepoint_fact_verification_requests AS req
                  ON req.request_record_id = rec.request_record_id
                JOIN theme_chokepoint_fact_verification_raw_responses AS raw
                  ON raw.response_record_id = rec.response_record_id
                JOIN theme_chokepoint_fact_verification_results AS result
                  ON result.result_record_id = rec.result_record_id
                WHERE rec.run_id = ? AND rec.governance_bundle_sha256 = ?
                ORDER BY req.assertion_id, rec.receipt_id
                """,
                (run_id, governance_bundle_sha256),
            ).fetchall()
        ledger = {
            "schema_version": "theme-chokepoint-verification-ledger-v2",
            "evidence_pack_id": evidence_pack_id,
            "requests": [],
            "raw_responses": [],
            "results": [],
            "reconciliations": [],
        }
        seen_assertions: set[str] = set()
        for row in rows:
            raw_body = bytes(row["raw_body"])
            raw_payload = parse_business_fact_verification_raw(raw_body)
            source_context = business_fact_verification_source_context_from_json(
                row["source_context_json"]
            )
            persisted_source_context = self.load_business_fact_verification_source_context(
                source_identity_id=row["source_identity_id"],
                source_version_id=row["source_version_id"],
                source_type=source_context.source_type,
                issuer_company_id=source_context.issuer_company_id,
                product_id=source_context.product_id,
                fiscal_period_id=source_context.fiscal_period_id,
                accounting_metric=source_context.accounting_metric,
                quote_start=source_context.quote_start,
                quote_end=source_context.quote_end,
                exact_quote_sha256=source_context.exact_quote_sha256,
            )
            valid = (
                row["run_id"] == run_id
                and row["raw_run_id"] == run_id
                and row["result_run_id"] == run_id
                and row["rec_run_id"] == run_id
                and row["raw_request_record_id"] == row["request_record_id"]
                and row["result_request_record_id"] == row["request_record_id"]
                and row["result_response_record_id"] == row["response_record_id"]
                and row["rec_request_record_id"] == row["request_record_id"]
                and row["rec_response_record_id"] == row["response_record_id"]
                and row["rec_result_record_id"] == row["result_record_id"]
                and row["result_assertion_id"] == row["assertion_id"]
                and row["rec_assertion_id"] == row["assertion_id"]
                and row["result_assertion_sha256"] == row["assertion_sha256"]
                and row["governance_bundle_sha256"] == governance_bundle_sha256
                and row["rec_governance_bundle_sha256"] == governance_bundle_sha256
                and row["source_context_sha256"]
                == row["rec_source_context_sha256"]
                and row["source_identity_id"] == row["rec_source_identity_id"]
                and row["source_version_id"] == row["rec_source_version_id"]
                and business_fact_verification_source_context_sha256(source_context)
                == row["source_context_sha256"]
                and persisted_source_context == source_context
                and row["verifier"] == row["verifier_execution_id"]
                and row["producer_execution_id"] != row["verifier_execution_id"]
                and sha256(raw_body).hexdigest() == row["raw_response_sha256"]
                and raw_payload["assertion_id"] == row["assertion_id"]
                and raw_payload["assertion_sha256"] == row["assertion_sha256"]
                and raw_payload["decision"] == row["decision"]
            )
            if not valid:
                raise ValueError("verification ledger repository reconciliation failed")
            if row["assertion_id"] in seen_assertions:
                raise ValueError("verification ledger assertion has duplicate reconciliations")
            seen_assertions.add(row["assertion_id"])
            ledger["requests"].append(
                {
                    "request_record_id": row["request_record_id"],
                    "evidence_pack_id": evidence_pack_id,
                    "assertion_id": row["assertion_id"],
                    "evidence_id": row["evidence_id"],
                    "assertion_sha256": row["assertion_sha256"],
                    "governance_bundle_sha256": governance_bundle_sha256,
                    "producer_execution_id": row["producer_execution_id"],
                    "verifier_execution_id": row["verifier_execution_id"],
                    "source_context": json.loads(row["source_context_json"]),
                    "source_context_sha256": row["source_context_sha256"],
                }
            )
            ledger["raw_responses"].append(
                {
                    "response_record_id": row["response_record_id"],
                    "request_record_id": row["request_record_id"],
                    "evidence_pack_id": evidence_pack_id,
                    "verifier_execution_id": row["verifier_execution_id"],
                    "provider_trace_id": row["provider_trace_id"],
                    "http_status": row["http_status"],
                    "raw_body_base64": base64.b64encode(raw_body).decode("ascii"),
                    "raw_response_sha256": row["raw_response_sha256"],
                }
            )
            ledger["results"].append(
                {
                    "result_record_id": row["result_record_id"],
                    "request_record_id": row["request_record_id"],
                    "response_record_id": row["response_record_id"],
                    "evidence_pack_id": evidence_pack_id,
                    "assertion_id": row["assertion_id"],
                    "assertion_sha256": row["assertion_sha256"],
                    "decision": row["decision"],
                }
            )
            ledger["reconciliations"].append(
                {
                    "reconciliation_receipt_id": row["receipt_id"],
                    "request_record_id": row["request_record_id"],
                    "response_record_id": row["response_record_id"],
                    "result_record_id": row["result_record_id"],
                    "evidence_pack_id": evidence_pack_id,
                    "assertion_id": row["assertion_id"],
                    "governance_bundle_sha256": governance_bundle_sha256,
                    "source_identity_id": row["source_identity_id"],
                    "source_version_id": row["source_version_id"],
                    "source_context_sha256": row["source_context_sha256"],
                }
            )
        return ledger

    def save_source_identity(
        self,
        identity: SourceIdentity,
        *,
        canonical_publisher_id: str,
        canonical_document_id: str | None,
        origin_event_id: str | None,
    ) -> StoredSourceIdentity:
        if not isinstance(identity, SourceIdentity):
            raise ValueError("source identity policy result is required")
        if (
            identity.provenance is SourceProvenance.VERIFIED
            and not _is_controlled_source_identity(identity)
        ):
            raise ValueError(
                "VERIFIED provenance requires a controlled source resolver execution"
            )
        execution = _controlled_source_resolution_execution(identity)
        if identity.provenance is SourceProvenance.VERIFIED and execution is None:
            raise ValueError(
                "VERIFIED provenance requires durable resolver execution material"
            )
        if not canonical_publisher_id.strip():
            raise ValueError("canonical publisher identity is required")
        if canonical_document_id is not None and not canonical_document_id.strip():
            raise ValueError("canonical document identity cannot be blank")
        if origin_event_id is not None and not origin_event_id.strip():
            raise ValueError("origin event identity cannot be blank")
        payload = {
            "canonical_publisher_id": canonical_publisher_id,
            "canonical_document_id": canonical_document_id,
            "origin_event_id": origin_event_id,
            "canonical_url_key": identity.canonical_url,
            "redirect_chain": list(identity.redirect_chain),
            "relation_type": identity.relation.value,
            "provenance_state": identity.provenance.value,
            "origin_canonical_url": identity.origin_canonical_url,
        }
        binding = {
            "policy_id": identity.policy_id,
            "policy_sha256": identity.policy_sha256,
            "governance_bundle_id": identity.governance_bundle_id,
            "governance_bundle_sha256": identity.governance_bundle_sha256,
            "resolver_receipt_id": identity.resolver_receipt_id,
            "resolver_receipt_sha256": identity.resolver_receipt_sha256,
        }
        if any(value is not None for value in binding.values()):
            if not all(isinstance(value, str) and value.strip() for value in binding.values()):
                raise ValueError("source resolver policy binding is incomplete")
            payload.update(binding)
        payload_json = _json(payload)
        payload_sha256 = sha256(payload_json.encode("utf-8")).hexdigest()
        source_identity_id = "source_identity_" + payload_sha256[:24]
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if execution is not None:
                raw_body = bytes(execution["raw_body"])
                if (
                    sha256(raw_body).hexdigest()
                    != execution["raw_response_sha256"]
                    or execution["receipt_id"] != identity.resolver_receipt_id
                    or execution["receipt_sha256"]
                    != identity.resolver_receipt_sha256
                    or execution["policy_id"] != identity.policy_id
                    or execution["policy_sha256"] != identity.policy_sha256
                    or execution["governance_bundle_id"]
                    != identity.governance_bundle_id
                    or execution["governance_bundle_sha256"]
                    != identity.governance_bundle_sha256
                    or not isinstance(execution["retrieved_at"], datetime)
                    or not _is_aware(execution["retrieved_at"])
                ):
                    raise ValueError("source resolver execution binding mismatch")
                receipt_values = (
                    execution["receipt_id"],
                    execution["provider"],
                    execution["provider_trace_id"],
                    execution["http_status"],
                    raw_body,
                    execution["raw_response_sha256"],
                    execution["retrieved_at"].isoformat(),
                    execution["policy_id"],
                    execution["policy_sha256"],
                    execution["governance_bundle_id"],
                    execution["governance_bundle_sha256"],
                    execution["receipt_sha256"],
                )
                existing_receipt = connection.execute(
                    """SELECT receipt_id, provider, provider_trace_id, http_status,
                              raw_body, raw_response_sha256, retrieved_at, policy_id,
                              policy_sha256, governance_bundle_id,
                              governance_bundle_sha256, receipt_sha256
                       FROM theme_chokepoint_source_resolution_receipts
                       WHERE receipt_id = ?""",
                    (execution["receipt_id"],),
                ).fetchone()
                if existing_receipt is not None:
                    stored_values = tuple(
                        bytes(existing_receipt[index]) if index == 4 else existing_receipt[index]
                        for index in range(len(receipt_values))
                    )
                    if stored_values != receipt_values:
                        raise ValueError("source resolver receipt immutable payload mismatch")
                else:
                    connection.execute(
                        """INSERT INTO theme_chokepoint_source_resolution_receipts(
                               receipt_id, provider, provider_trace_id, http_status,
                               raw_body, raw_response_sha256, retrieved_at, policy_id,
                               policy_sha256, governance_bundle_id,
                               governance_bundle_sha256, receipt_sha256, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (*receipt_values, now.isoformat()),
                    )
            terminal = None
            terminal_url = (
                identity.origin_canonical_url
                if identity.relation in {SourceRelation.ALIAS, SourceRelation.REPRINT}
                else identity.canonical_url
            )
            if identity.relation is not SourceRelation.ORIGINAL and terminal_url:
                terminal = connection.execute(
                    """SELECT source_identity_id
                       FROM theme_chokepoint_source_identities
                       WHERE canonical_url_key = ? AND relation_type = ?
                       ORDER BY created_at, source_identity_id LIMIT 1""",
                    (terminal_url, SourceRelation.ORIGINAL.value),
                ).fetchone()
            existing = connection.execute(
                "SELECT identity_payload_sha256 FROM theme_chokepoint_source_identities WHERE source_identity_id = ?",
                (source_identity_id,),
            ).fetchone()
            if existing is not None:
                if existing["identity_payload_sha256"] != payload_sha256:
                    raise ValueError("source identity immutable payload mismatch")
            else:
                connection.execute(
                    """INSERT INTO theme_chokepoint_source_identities(
                           source_identity_id, canonical_publisher_id,
                           canonical_document_id, origin_event_id, canonical_url_key,
                           redirect_terminal_identity_id, redirect_chain_json,
                           relation_type, provenance_state, origin_canonical_url,
                           policy_id, policy_sha256, governance_bundle_id,
                           governance_bundle_sha256, resolver_receipt_id,
                           resolver_receipt_sha256, identity_payload_sha256, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_identity_id,
                        canonical_publisher_id,
                        canonical_document_id,
                        origin_event_id,
                        identity.canonical_url,
                        terminal["source_identity_id"] if terminal else None,
                        _json(list(identity.redirect_chain)),
                        identity.relation.value,
                        identity.provenance.value,
                        identity.origin_canonical_url,
                        identity.policy_id,
                        identity.policy_sha256,
                        identity.governance_bundle_id,
                        identity.governance_bundle_sha256,
                        identity.resolver_receipt_id,
                        identity.resolver_receipt_sha256,
                        payload_sha256,
                        now.isoformat(),
                    ),
                )
        return self.get_source_identity(source_identity_id)

    def _materialize_legacy_source_usages(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        stage: str,
        snapshots: tuple[SourceSnapshot, ...],
        retrieved_at: datetime,
    ) -> None:
        if stage not in {"stage3", "stage4"}:
            raise ValueError("source usage stage is invalid")
        for snapshot in snapshots:
            identity = resolve_source_identity(
                snapshot.canonical_url,
                relation=SourceRelation.ORIGINAL,
                provenance=SourceProvenance.UNKNOWN,
            )
            publisher_host = urlsplit(identity.canonical_url).hostname
            if not publisher_host:
                raise ValueError("legacy source snapshot canonical URL has no publisher")
            identity_payload = {
                "canonical_publisher_id": f"publisher-host:{publisher_host.casefold()}",
                "canonical_document_id": None,
                "origin_event_id": None,
                "canonical_url_key": identity.canonical_url,
                "redirect_chain": list(identity.redirect_chain),
                "relation_type": identity.relation.value,
                "provenance_state": SourceProvenance.UNKNOWN.value,
                "origin_canonical_url": None,
            }
            identity_payload_json = _json(identity_payload)
            identity_payload_sha256 = sha256(
                identity_payload_json.encode("utf-8")
            ).hexdigest()
            source_identity_id = "source_identity_" + identity_payload_sha256[:24]
            existing_identity = connection.execute(
                "SELECT identity_payload_sha256 FROM theme_chokepoint_source_identities "
                "WHERE source_identity_id = ?",
                (source_identity_id,),
            ).fetchone()
            if existing_identity is None:
                connection.execute(
                    """INSERT INTO theme_chokepoint_source_identities(
                           source_identity_id, canonical_publisher_id,
                           canonical_document_id, origin_event_id, canonical_url_key,
                           redirect_terminal_identity_id, redirect_chain_json,
                           relation_type, provenance_state, origin_canonical_url,
                           identity_payload_sha256, created_at
                       ) VALUES (?, ?, NULL, NULL, ?, NULL, ?, ?, ?, NULL, ?, ?)""",
                    (
                        source_identity_id,
                        identity_payload["canonical_publisher_id"],
                        identity.canonical_url,
                        _json(list(identity.redirect_chain)),
                        SourceRelation.ORIGINAL.value,
                        SourceProvenance.UNKNOWN.value,
                        identity_payload_sha256,
                        retrieved_at.isoformat(),
                    ),
                )
            elif existing_identity["identity_payload_sha256"] != identity_payload_sha256:
                raise ValueError("legacy source identity immutable payload mismatch")

            raw_bytes = snapshot.original_text.encode("utf-8")
            raw_bytes_sha256 = sha256(raw_bytes).hexdigest()
            declared_content_sha256 = snapshot.content_hash.removeprefix("sha256:")
            if (
                not _is_sha256(declared_content_sha256)
                or declared_content_sha256 != raw_bytes_sha256
            ):
                raise ValueError("legacy source snapshot content hash mismatch")
            normalized_content_sha256 = normalized_source_content_sha256(
                snapshot.original_text
            )
            existing_version = connection.execute(
                """SELECT source_version_id FROM theme_chokepoint_source_versions
                   WHERE source_identity_id = ? AND raw_bytes_sha256 = ?
                     AND normalized_content_sha256 = ? AND quote_span_sha256 IS NULL
                   ORDER BY version_sequence LIMIT 1""",
                (
                    source_identity_id,
                    raw_bytes_sha256,
                    normalized_content_sha256,
                ),
            ).fetchone()
            if existing_version is None:
                sequence_row = connection.execute(
                    """SELECT COALESCE(MAX(version_sequence), 0) AS maximum
                       FROM theme_chokepoint_source_versions
                       WHERE source_identity_id = ?""",
                    (source_identity_id,),
                ).fetchone()
                version_sequence = int(sequence_row["maximum"]) + 1
                version_material = _json(
                    {
                        "source_identity_id": source_identity_id,
                        "retrieved_at": retrieved_at.isoformat(),
                        "raw_bytes_sha256": raw_bytes_sha256,
                        "normalized_content_sha256": normalized_content_sha256,
                        "quote_span_sha256": None,
                        "version_sequence": version_sequence,
                    }
                )
                source_version_id = "source_version_" + sha256(
                    version_material.encode("utf-8")
                ).hexdigest()[:24]
                connection.execute(
                    """INSERT INTO theme_chokepoint_source_versions(
                           source_version_id, source_identity_id, retrieved_at,
                           raw_bytes, raw_bytes_sha256, normalized_content,
                           normalized_content_sha256, quote_span_json,
                           quote_span_sha256, content_type, language,
                           publication_time, updated_time, version_sequence, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL, ?, ?)""",
                    (
                        source_version_id,
                        source_identity_id,
                        retrieved_at.isoformat(),
                        raw_bytes,
                        raw_bytes_sha256,
                        snapshot.original_text,
                        normalized_content_sha256,
                        "text/plain",
                        "und",
                        version_sequence,
                        retrieved_at.isoformat(),
                    ),
                )
            else:
                source_version_id = existing_version["source_version_id"]

            usage_material = "\0".join(
                (
                    run_id,
                    stage,
                    snapshot.article_id,
                    source_identity_id,
                    source_version_id,
                )
            )
            usage_id = "source_usage_" + sha256(
                usage_material.encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """INSERT INTO theme_chokepoint_source_usages(
                       usage_id, run_id, stage, source_snapshot_article_id,
                       source_identity_id, source_version_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    usage_id,
                    run_id,
                    stage,
                    snapshot.article_id,
                    source_identity_id,
                    source_version_id,
                    retrieved_at.isoformat(),
                ),
            )

    def list_source_usages(self, run_id: str, *, stage: str) -> tuple[SourceUsage, ...]:
        if stage not in {"stage3", "stage4"}:
            raise ValueError("source usage stage is invalid")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT usage.*, identity.canonical_publisher_id,
                          identity.canonical_document_id, identity.origin_event_id,
                          identity.canonical_url_key, identity.redirect_chain_json,
                          identity.relation_type, identity.provenance_state,
                          identity.origin_canonical_url,
                          identity.identity_payload_sha256,
                          version.raw_bytes, version.raw_bytes_sha256,
                          version.normalized_content,
                          version.normalized_content_sha256,
                          version.quote_span_json, version.quote_span_sha256
                   FROM theme_chokepoint_source_usages AS usage
                   JOIN theme_chokepoint_source_identities AS identity
                     ON identity.source_identity_id = usage.source_identity_id
                   JOIN theme_chokepoint_source_versions AS version
                     ON version.source_version_id = usage.source_version_id
                    AND version.source_identity_id = usage.source_identity_id
                   WHERE usage.run_id = ? AND usage.stage = ?
                   ORDER BY usage.source_snapshot_article_id, usage.usage_id""",
                (run_id, stage),
            ).fetchall()
        usages = []
        for row in rows:
            identity_payload = {
                "canonical_publisher_id": row["canonical_publisher_id"],
                "canonical_document_id": row["canonical_document_id"],
                "origin_event_id": row["origin_event_id"],
                "canonical_url_key": row["canonical_url_key"],
                "redirect_chain": json.loads(row["redirect_chain_json"]),
                "relation_type": row["relation_type"],
                "provenance_state": row["provenance_state"],
                "origin_canonical_url": row["origin_canonical_url"],
            }
            identity_hash_valid = (
                sha256(_json(identity_payload).encode("utf-8")).hexdigest()
                == row["identity_payload_sha256"]
            )
            raw_hash_valid = (
                sha256(bytes(row["raw_bytes"])).hexdigest()
                == row["raw_bytes_sha256"]
            )
            normalized_hash_valid = (
                normalized_source_content_sha256(row["normalized_content"])
                == row["normalized_content_sha256"]
            )
            quote_hash_valid = row["quote_span_json"] is None
            if row["quote_span_json"] is not None:
                start, end, exact_quote = json.loads(row["quote_span_json"])
                quote_hash_valid = (
                    row["normalized_content"][start:end] == exact_quote
                    and normalized_quote_sha256(exact_quote)
                    == row["quote_span_sha256"]
                )
            if not all(
                (
                    identity_hash_valid,
                    raw_hash_valid,
                    normalized_hash_valid,
                    quote_hash_valid,
                )
            ):
                raise ValueError("source usage lineage hash mismatch")
            usages.append(
                SourceUsage(
                    usage_id=row["usage_id"],
                    run_id=row["run_id"],
                    stage=row["stage"],
                    source_snapshot_article_id=row["source_snapshot_article_id"],
                    source_identity_id=row["source_identity_id"],
                    source_version_id=row["source_version_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return tuple(usages)

    def get_source_identity(self, source_identity_id: str) -> StoredSourceIdentity:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM theme_chokepoint_source_identities WHERE source_identity_id = ?",
                (source_identity_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_identity_id)
        payload = _source_identity_payload_from_row(row)
        payload_sha256 = sha256(_json(payload).encode("utf-8")).hexdigest()
        if (
            row["identity_payload_sha256"] != payload_sha256
            or source_identity_id != "source_identity_" + payload_sha256[:24]
        ):
            raise ValueError("stored source identity immutable payload mismatch")
        return StoredSourceIdentity(
            source_identity_id=row["source_identity_id"],
            canonical_publisher_id=row["canonical_publisher_id"],
            canonical_document_id=row["canonical_document_id"],
            origin_event_id=row["origin_event_id"],
            canonical_url_key=row["canonical_url_key"],
            redirect_terminal_identity_id=row["redirect_terminal_identity_id"],
            redirect_chain=tuple(json.loads(row["redirect_chain_json"])),
            relation=SourceRelation(row["relation_type"]),
            provenance=SourceProvenance(row["provenance_state"]),
            origin_canonical_url=row["origin_canonical_url"],
            policy_id=row["policy_id"],
            policy_sha256=row["policy_sha256"],
            governance_bundle_id=row["governance_bundle_id"],
            governance_bundle_sha256=row["governance_bundle_sha256"],
            resolver_receipt_id=row["resolver_receipt_id"],
            resolver_receipt_sha256=row["resolver_receipt_sha256"],
        )

    def save_source_version(
        self,
        *,
        source_identity_id: str,
        retrieved_at: datetime,
        raw_bytes: bytes,
        normalized_content: str,
        quote_span: tuple[int, int, str] | None,
        content_type: str,
        language: str,
        publication_time: datetime | None,
        updated_time: datetime | None,
    ) -> SourceVersion:
        if not _is_aware(retrieved_at) or any(
            value is not None and not _is_aware(value)
            for value in (publication_time, updated_time)
        ):
            raise ValueError("source version times must be timezone-aware")
        if not raw_bytes or not isinstance(normalized_content, str):
            raise ValueError("source version requires raw bytes and normalized content")
        if not content_type.strip() or not language.strip():
            raise ValueError("source version content type and language are required")
        quote_span_json = None
        quote_hash = None
        if quote_span is not None:
            start, end, exact_quote = quote_span
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or normalized_content[start:end] != exact_quote
            ):
                raise ValueError("source version quote span does not match content")
            quote_span_json = _json([start, end, exact_quote])
            quote_hash = normalized_quote_sha256(exact_quote)
        raw_hash = sha256(raw_bytes).hexdigest()
        normalized_hash = normalized_source_content_sha256(normalized_content)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute(
                "SELECT 1 FROM theme_chokepoint_source_identities WHERE source_identity_id = ?",
                (source_identity_id,),
            ).fetchone()
            if identity is None:
                raise KeyError(source_identity_id)
            row = connection.execute(
                "SELECT COALESCE(MAX(version_sequence), 0) AS maximum FROM theme_chokepoint_source_versions WHERE source_identity_id = ?",
                (source_identity_id,),
            ).fetchone()
            sequence = int(row["maximum"]) + 1
            identity_material = _json(
                {
                    "source_identity_id": source_identity_id,
                    "retrieved_at": retrieved_at.isoformat(),
                    "raw_bytes_sha256": raw_hash,
                    "normalized_content_sha256": normalized_hash,
                    "quote_span_sha256": quote_hash,
                    "version_sequence": sequence,
                }
            )
            source_version_id = "source_version_" + sha256(
                identity_material.encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """INSERT INTO theme_chokepoint_source_versions(
                       source_version_id, source_identity_id, retrieved_at,
                       raw_bytes, raw_bytes_sha256, normalized_content,
                       normalized_content_sha256, quote_span_json,
                       quote_span_sha256, content_type, language,
                       publication_time, updated_time, version_sequence, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_version_id,
                    source_identity_id,
                    retrieved_at.isoformat(),
                    raw_bytes,
                    raw_hash,
                    normalized_content,
                    normalized_hash,
                    quote_span_json,
                    quote_hash,
                    content_type,
                    language,
                    publication_time.isoformat() if publication_time else None,
                    updated_time.isoformat() if updated_time else None,
                    sequence,
                    _utc_now().isoformat(),
                ),
            )
        return self.get_source_version(source_version_id)

    def get_source_version(self, source_version_id: str) -> SourceVersion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM theme_chokepoint_source_versions WHERE source_version_id = ?",
                (source_version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_version_id)
        return SourceVersion(
            source_version_id=row["source_version_id"],
            source_identity_id=row["source_identity_id"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            raw_bytes_sha256=row["raw_bytes_sha256"],
            normalized_content_sha256=row["normalized_content_sha256"],
            quote_span_sha256=row["quote_span_sha256"],
            content_type=row["content_type"],
            language=row["language"],
            publication_time=(
                datetime.fromisoformat(row["publication_time"])
                if row["publication_time"]
                else None
            ),
            updated_time=(
                datetime.fromisoformat(row["updated_time"])
                if row["updated_time"]
                else None
            ),
            version_sequence=row["version_sequence"],
        )

    def authorizes_independent_industry_event(self, source_version_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT version.*, identity.canonical_publisher_id,
                          identity.canonical_document_id, identity.origin_event_id,
                          identity.canonical_url_key,
                          identity.redirect_terminal_identity_id,
                          identity.redirect_chain_json, identity.relation_type,
                          identity.provenance_state, identity.origin_canonical_url,
                          identity.policy_id, identity.policy_sha256,
                          identity.governance_bundle_id,
                          identity.governance_bundle_sha256,
                          identity.resolver_receipt_id,
                          identity.resolver_receipt_sha256,
                          identity.identity_payload_sha256,
                          receipt.receipt_id AS resolution_receipt_id,
                          receipt.provider AS resolution_provider,
                          receipt.provider_trace_id AS resolution_provider_trace_id,
                          receipt.http_status AS resolution_http_status,
                          receipt.raw_body AS resolution_raw_body,
                          receipt.raw_response_sha256 AS resolution_raw_response_sha256,
                          receipt.retrieved_at AS resolution_retrieved_at,
                          receipt.policy_id AS resolution_policy_id,
                          receipt.policy_sha256 AS resolution_policy_sha256,
                          receipt.governance_bundle_id AS resolution_governance_bundle_id,
                          receipt.governance_bundle_sha256 AS resolution_governance_bundle_sha256,
                          receipt.receipt_sha256 AS resolution_receipt_sha256
                   FROM theme_chokepoint_source_versions AS version
                   JOIN theme_chokepoint_source_identities AS identity
                     ON identity.source_identity_id = version.source_identity_id
                   LEFT JOIN theme_chokepoint_source_resolution_receipts AS receipt
                     ON receipt.receipt_id = identity.resolver_receipt_id
                   WHERE version.source_version_id = ?""",
                (source_version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_version_id)
        if (
            row["relation_type"] != SourceRelation.ORIGINAL.value
            or row["provenance_state"] != SourceProvenance.VERIFIED.value
        ):
            return False
        payload_sha256 = sha256(
            _json(_source_identity_payload_from_row(row)).encode("utf-8")
        ).hexdigest()
        if row["identity_payload_sha256"] != payload_sha256:
            return False
        bundle = RuntimeGovernanceBundle(
            expected_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256
        )
        if (
            bundle.bundle_id != CANONICAL_GOVERNANCE_BUNDLE_ID
            or bundle.artifact_sha256["source_identity_policy"]
            != CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
            or row["policy_id"] != CANONICAL_SOURCE_IDENTITY_POLICY_ID
            or row["policy_sha256"] != CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
            or row["governance_bundle_id"] != CANONICAL_GOVERNANCE_BUNDLE_ID
            or row["governance_bundle_sha256"]
            != CANONICAL_GOVERNANCE_BUNDLE_SHA256
            or not isinstance(row["resolver_receipt_id"], str)
            or not row["resolver_receipt_id"].strip()
            or not _is_sha256(row["resolver_receipt_sha256"])
            or not _source_resolution_receipt_is_valid(row)
        ):
            return False
        quote_valid = True
        if row["quote_span_json"]:
            start, end, exact_quote = json.loads(row["quote_span_json"])
            quote_valid = (
                row["normalized_content"][start:end] == exact_quote
                and normalized_quote_sha256(exact_quote) == row["quote_span_sha256"]
            )
        return bool(
            sha256(bytes(row["raw_bytes"])).hexdigest()
            == row["raw_bytes_sha256"]
            and normalized_source_content_sha256(row["normalized_content"])
            == row["normalized_content_sha256"]
            and quote_valid
        )

    def source_version_matches_snapshot(
        self, source_version_id: str, snapshot: SourceSnapshot
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT version.raw_bytes, version.raw_bytes_sha256,
                          version.normalized_content,
                          version.normalized_content_sha256,
                          identity.canonical_url_key
                   FROM theme_chokepoint_source_versions AS version
                   JOIN theme_chokepoint_source_identities AS identity
                     ON identity.source_identity_id = version.source_identity_id
                   WHERE version.source_version_id = ?""",
                (source_version_id,),
            ).fetchone()
        if row is None:
            return False
        raw = snapshot.original_text.encode("utf-8")
        return bool(
            row["canonical_url_key"]
            == resolve_source_identity(snapshot.canonical_url).canonical_url
            and bytes(row["raw_bytes"]) == raw
            and row["raw_bytes_sha256"] == sha256(raw).hexdigest()
            and row["normalized_content"] == snapshot.original_text
            and row["normalized_content_sha256"]
            == normalized_source_content_sha256(snapshot.original_text)
        )

    def resolve_industry_event_identity(self, source_version_id: str) -> str:
        version = self.get_source_version(source_version_id)
        identity = self.get_source_identity(version.source_identity_id)
        if identity.relation is SourceRelation.ORIGINAL:
            return identity.source_identity_id
        if identity.redirect_terminal_identity_id:
            return identity.redirect_terminal_identity_id
        origin_url = identity.origin_canonical_url or identity.canonical_url_key
        with self._connect() as connection:
            row = connection.execute(
                """SELECT source_identity_id
                   FROM theme_chokepoint_source_identities
                   WHERE canonical_url_key = ? AND relation_type = ?
                   ORDER BY created_at, source_identity_id LIMIT 1""",
                (origin_url, SourceRelation.ORIGINAL.value),
            ).fetchone()
        if row is not None:
            return row["source_identity_id"]
        return "unresolved_event_" + sha256(origin_url.encode("utf-8")).hexdigest()[:24]

    def reconcile_independent_source_event(self, card: EvidenceCard) -> str | None:
        """Return one controlled event key only when the persisted source binds the card."""
        if not card.source_identity_id or not card.source_version_id:
            return None
        try:
            version = self.get_source_version(card.source_version_id)
            identity = self.get_source_identity(card.source_identity_id)
            authorized = self.authorizes_independent_industry_event(
                card.source_version_id
            )
        except (KeyError, ValueError):
            return None
        declared_content_sha256 = card.content_hash.removeprefix("sha256:")
        if (
            not authorized
            or version.source_identity_id != identity.source_identity_id
            or identity.canonical_url_key != canonicalize_source_url(card.canonical_url)
            or version.raw_bytes_sha256 != declared_content_sha256
            or (
                version.quote_span_sha256 is not None
                and version.quote_span_sha256
                != normalized_quote_sha256(card.exact_quote)
            )
        ):
            return None
        return identity.origin_event_id or self.resolve_industry_event_identity(
            card.source_version_id
        )

    def _ensure_assessment_head(
        self, run_id: str, target_type: str, target_id: str
    ) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT 1 FROM theme_chokepoint_assessment_heads
                   WHERE run_id = ? AND target_type = ? AND target_id = ?""",
                (run_id, target_type, target_id),
            ).fetchone()
        if existing is not None:
            return
        if target_type == "company_assessment":
            assessment = next(
                (
                    item
                    for item in self.get_stage4_result(run_id).company_assessments
                    if item.assessment_id == target_id
                ),
                None,
            )
        elif target_type == "segment_assessment":
            assessment = next(
                (
                    item
                    for item in self.get_stage3_result(run_id).assessments
                    if item.segment_id == target_id
                ),
                None,
            )
        else:
            assessment = None
        if assessment is None:
            raise KeyError((run_id, target_type, target_id))
        payload = _to_jsonable(asdict(assessment))
        payload_json = _json(payload)
        payload_sha256 = sha256(payload_json.encode("utf-8")).hexdigest()
        revision_id = "assessment_revision_base_" + sha256(
            f"{run_id}\0{target_type}\0{target_id}\0{payload_sha256}".encode("utf-8")
        ).hexdigest()[:24]
        now = _utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO theme_chokepoint_assessment_revisions(
                       revision_id, run_id, target_type, target_id,
                       parent_revision_id, result_reconciliation_id,
                       payload_json, payload_sha256, created_at
                   ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    revision_id,
                    run_id,
                    target_type,
                    target_id,
                    payload_json,
                    payload_sha256,
                    now,
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO theme_chokepoint_assessment_heads(
                       run_id, target_type, target_id, revision_id, updated_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (run_id, target_type, target_id, revision_id, now),
            )

    def get_assessment_revision(self, revision_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM theme_chokepoint_assessment_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        payload = json.loads(row["payload_json"])
        return SimpleNamespace(**payload)

    def get_assessment_head(
        self, run_id: str, target_type: str, target_id: str
    ) -> AssessmentHeadRecord:
        self._ensure_assessment_head(run_id, target_type, target_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM theme_chokepoint_assessment_heads
                   WHERE run_id = ? AND target_type = ? AND target_id = ?""",
                (run_id, target_type, target_id),
            ).fetchone()
        return AssessmentHeadRecord(
            run_id=run_id,
            target_type=target_type,
            target_id=target_id,
            revision_id=row["revision_id"],
            revision=self.get_assessment_revision(row["revision_id"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def append_assessment_revision(self, revision):
        required = (
            "revision_id",
            "run_id",
            "target_type",
            "target_id",
            "parent_revision_id",
            "result_reconciliation_id",
        )
        if any(not hasattr(revision, field) for field in required):
            raise ValueError("assessment revision is incomplete")
        head = self.get_assessment_head(
            revision.run_id, revision.target_type, revision.target_id
        )
        if revision.parent_revision_id != head.revision_id:
            raise ValueError("stale expected assessment head")
        if isinstance(revision, AssessmentRevisionRecord):
            payload_json = revision.payload_json
            payload_sha256 = revision.payload_sha256
            created_at = revision.created_at
        else:
            payload_json = _json(_to_jsonable(vars(revision)))
            payload_sha256 = sha256(payload_json.encode("utf-8")).hexdigest()
            created_at = _utc_now()
        if payload_sha256 != sha256(payload_json.encode("utf-8")).hexdigest():
            raise ValueError("assessment revision payload SHA-256 mismatch")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT revision_id FROM theme_chokepoint_assessment_heads
                   WHERE run_id = ? AND target_type = ? AND target_id = ?""",
                (revision.run_id, revision.target_type, revision.target_id),
            ).fetchone()
            if current is None or current["revision_id"] != revision.parent_revision_id:
                raise ValueError("stale expected assessment head")
            connection.execute(
                """INSERT INTO theme_chokepoint_assessment_revisions(
                       revision_id, run_id, target_type, target_id,
                       parent_revision_id, result_reconciliation_id,
                       payload_json, payload_sha256, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    revision.revision_id,
                    revision.run_id,
                    revision.target_type,
                    revision.target_id,
                    revision.parent_revision_id,
                    revision.result_reconciliation_id,
                    payload_json,
                    payload_sha256,
                    created_at.isoformat(),
                ),
            )
            updated = connection.execute(
                """UPDATE theme_chokepoint_assessment_heads
                   SET revision_id = ?, updated_at = ?
                   WHERE run_id = ? AND target_type = ? AND target_id = ?
                     AND revision_id = ?""",
                (
                    revision.revision_id,
                    created_at.isoformat(),
                    revision.run_id,
                    revision.target_type,
                    revision.target_id,
                    revision.parent_revision_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("stale expected assessment head")
        return revision

    def compare_and_swap_assessment_head(
        self,
        run_id: str,
        target_type: str,
        target_id: str,
        *,
        expected_revision_id: str,
        new_revision_id: str,
    ) -> AssessmentHeadRecord:
        head = self.get_assessment_head(run_id, target_type, target_id)
        if head.revision_id != expected_revision_id:
            raise ValueError("stale expected assessment head")
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM theme_chokepoint_assessment_revisions WHERE revision_id = ?",
                (new_revision_id,),
            ).fetchone()
            if exists is None:
                raise KeyError(new_revision_id)
            updated = connection.execute(
                """UPDATE theme_chokepoint_assessment_heads
                   SET revision_id = ?, updated_at = ?
                   WHERE run_id = ? AND target_type = ? AND target_id = ?
                     AND revision_id = ?""",
                (
                    new_revision_id,
                    _utc_now().isoformat(),
                    run_id,
                    target_type,
                    target_id,
                    expected_revision_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("stale expected assessment head")
        return self.get_assessment_head(run_id, target_type, target_id)

    def save_canonical_scoring_request(
        self, record: CanonicalScoringRequestRecord
    ) -> CanonicalScoringRequestRecord:
        if (
            not _is_aware(record.created_at)
            or record.request_payload_sha256
            != sha256(record.request_payload_json.encode("utf-8")).hexdigest()
        ):
            raise ValueError("canonical scoring request is invalid")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO theme_chokepoint_canonical_scoring_requests(
                       request_record_id, operation_id, run_id, target_type,
                       target_id, evaluator_result_record_id,
                       expected_head_revision_id, request_payload_json,
                       request_payload_sha256, status, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', ?)""",
                (
                    record.request_record_id,
                    record.operation_id,
                    record.run_id,
                    record.target_type,
                    record.target_id,
                    record.evaluator_result_record_id,
                    record.expected_head_revision_id,
                    record.request_payload_json,
                    record.request_payload_sha256,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def save_canonical_scoring_raw_response(
        self, record: CanonicalScoringRawResponseRecord
    ) -> CanonicalScoringRawResponseRecord:
        if (
            not _is_aware(record.retrieved_at)
            or record.raw_response_sha256 != sha256(record.raw_body).hexdigest()
        ):
            raise ValueError("canonical scoring raw response is invalid")
        with self._connect() as connection:
            self._claim_provider_execution(
                connection,
                provider_identity=record.provider,
                provider_account_or_route_identity="canonical_scoring",
                upstream_trace_id=record.provider_trace_id,
                raw_response_sha256=record.raw_response_sha256,
                raw_body=record.raw_body,
                consumer_kind="canonical_scoring",
                consumer_record_id=record.response_record_id,
                claimed_at=record.retrieved_at,
            )
            connection.execute(
                """INSERT INTO theme_chokepoint_canonical_scoring_raw_responses(
                       response_record_id, request_record_id, operation_id, run_id,
                       provider, provider_trace_id, http_status, raw_body,
                       raw_response_sha256, retrieved_at, cost_usd
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.response_record_id,
                    record.request_record_id,
                    record.operation_id,
                    record.run_id,
                    record.provider,
                    record.provider_trace_id,
                    record.http_status,
                    record.raw_body,
                    record.raw_response_sha256,
                    record.retrieved_at.isoformat(),
                    record.cost_usd,
                ),
            )
        return record

    def save_canonical_scoring_result(
        self, record: CanonicalScoringParsedResultRecord
    ) -> CanonicalScoringParsedResultRecord:
        if (
            not _is_aware(record.parsed_at)
            or record.parsed_payload_sha256
            != sha256(record.parsed_payload_json.encode("utf-8")).hexdigest()
        ):
            raise ValueError("canonical scoring parsed result is invalid")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO theme_chokepoint_canonical_scoring_results(
                       result_record_id, request_record_id, response_record_id,
                       operation_id, run_id, parsed_payload_json,
                       parsed_payload_sha256, parser_version, parsed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.operation_id,
                    record.run_id,
                    record.parsed_payload_json,
                    record.parsed_payload_sha256,
                    record.parser_version,
                    record.parsed_at.isoformat(),
                ),
            )
        return record

    def reconcile_canonical_scoring(
        self, record: CanonicalScoringReconciliationRecord
    ) -> CanonicalScoringReconciliationRecord:
        if not _is_aware(record.reconciled_at):
            raise ValueError("canonical scoring reconciliation time is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT req.operation_id AS request_operation_id,
                          req.run_id AS request_run_id,
                          req.request_payload_json, req.request_payload_sha256,
                          raw.operation_id AS raw_operation_id,
                          raw.run_id AS raw_run_id, raw.raw_body,
                          raw.raw_response_sha256,
                          result.operation_id AS result_operation_id,
                          result.run_id AS result_run_id,
                          result.parsed_payload_json, result.parsed_payload_sha256
                   FROM theme_chokepoint_canonical_scoring_requests AS req
                   JOIN theme_chokepoint_canonical_scoring_raw_responses AS raw
                     ON raw.request_record_id = req.request_record_id
                   JOIN theme_chokepoint_canonical_scoring_results AS result
                     ON result.request_record_id = req.request_record_id
                    AND result.response_record_id = raw.response_record_id
                   WHERE req.request_record_id = ? AND raw.response_record_id = ?
                     AND result.result_record_id = ?""",
                (
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                ),
            ).fetchone()
            valid = row is not None and (
                row["request_operation_id"],
                row["raw_operation_id"],
                row["result_operation_id"],
            ) == (record.operation_id, record.operation_id, record.operation_id)
            valid = valid and (
                row["request_run_id"], row["raw_run_id"], row["result_run_id"]
            ) == (record.run_id, record.run_id, record.run_id)
            valid = valid and (
                sha256(row["request_payload_json"].encode("utf-8")).hexdigest()
                == row["request_payload_sha256"]
                and sha256(row["raw_body"]).hexdigest()
                == row["raw_response_sha256"]
                and sha256(row["parsed_payload_json"].encode("utf-8")).hexdigest()
                == row["parsed_payload_sha256"]
            )
            if not valid:
                raise ValueError("canonical scoring lineage did not reconcile")
            connection.execute(
                """INSERT INTO theme_chokepoint_canonical_scoring_reconciliations(
                       receipt_id, request_record_id, response_record_id,
                       result_record_id, operation_id, run_id, reconciled_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.receipt_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.result_record_id,
                    record.operation_id,
                    record.run_id,
                    record.reconciled_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE theme_chokepoint_canonical_scoring_requests SET status = 'RECONCILED' WHERE operation_id = ?",
                (record.operation_id,),
            )
        return record

    def load_reconciled_canonical_scoring_result(self, receipt_id: str):
        """Reload the complete canonical-scoring chain for its state consumer."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT reconciliation.receipt_id,
                          reconciliation.request_record_id AS reconciliation_request_id,
                          reconciliation.response_record_id AS reconciliation_response_id,
                          reconciliation.result_record_id AS reconciliation_result_id,
                          reconciliation.operation_id AS reconciliation_operation_id,
                          reconciliation.run_id AS reconciliation_run_id,
                          request.request_record_id, request.operation_id AS request_operation_id,
                          request.run_id AS request_run_id, request.status,
                          request.request_payload_json, request.request_payload_sha256,
                          raw.response_record_id, raw.request_record_id AS raw_request_id,
                          raw.operation_id AS raw_operation_id, raw.run_id AS raw_run_id,
                          raw.raw_body, raw.raw_response_sha256,
                          result.result_record_id, result.request_record_id AS result_request_id,
                          result.response_record_id AS result_response_id,
                          result.operation_id AS result_operation_id,
                          result.run_id AS result_run_id, result.parsed_payload_json,
                          result.parsed_payload_sha256
                   FROM theme_chokepoint_canonical_scoring_reconciliations AS reconciliation
                   JOIN theme_chokepoint_canonical_scoring_requests AS request
                     ON request.request_record_id = reconciliation.request_record_id
                   JOIN theme_chokepoint_canonical_scoring_raw_responses AS raw
                     ON raw.response_record_id = reconciliation.response_record_id
                    AND raw.request_record_id = request.request_record_id
                   JOIN theme_chokepoint_canonical_scoring_results AS result
                     ON result.result_record_id = reconciliation.result_record_id
                    AND result.request_record_id = request.request_record_id
                    AND result.response_record_id = raw.response_record_id
                   WHERE reconciliation.receipt_id = ?""",
                (receipt_id,),
            ).fetchone()
        if row is None:
            raise ValueError("stored canonical scoring result is missing")
        ids_valid = (
            row["reconciliation_request_id"] == row["request_record_id"]
            and row["reconciliation_response_id"] == row["response_record_id"]
            and row["reconciliation_result_id"] == row["result_record_id"]
            and row["raw_request_id"] == row["request_record_id"]
            and row["result_request_id"] == row["request_record_id"]
            and row["result_response_id"] == row["response_record_id"]
        )
        operations_valid = len(
            {
                row["reconciliation_operation_id"],
                row["request_operation_id"],
                row["raw_operation_id"],
                row["result_operation_id"],
            }
        ) == 1
        runs_valid = len(
            {
                row["reconciliation_run_id"],
                row["request_run_id"],
                row["raw_run_id"],
                row["result_run_id"],
            }
        ) == 1
        hashes_valid = (
            sha256(row["request_payload_json"].encode("utf-8")).hexdigest()
            == row["request_payload_sha256"]
            and sha256(row["raw_body"]).hexdigest() == row["raw_response_sha256"]
            and sha256(row["parsed_payload_json"].encode("utf-8")).hexdigest()
            == row["parsed_payload_sha256"]
        )
        if not (
            row["status"] == "RECONCILED"
            and ids_valid
            and operations_valid
            and runs_valid
            and hashes_valid
        ):
            raise ValueError("stored canonical scoring result did not reconcile")
        return SimpleNamespace(
            receipt_id=row["receipt_id"],
            raw_body=row["raw_body"],
            parsed_payload_json=row["parsed_payload_json"],
        )

    def mark_canonical_scoring_outcome_unknown(self, operation_id: str):
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE theme_chokepoint_canonical_scoring_requests SET status = 'OUTCOME_UNKNOWN' WHERE operation_id = ?",
                (operation_id,),
            )
        if updated.rowcount != 1:
            raise KeyError(operation_id)
        return SimpleNamespace(operation_id=operation_id, status="OUTCOME_UNKNOWN")

    def reconcile_canonical_scoring_outcome_unknown(self, operation_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM theme_chokepoint_canonical_scoring_requests WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return SimpleNamespace(operation_id=operation_id, status=row["status"])

    def save_counter_search_request(
        self, record: CounterSearchRequestRecord
    ) -> CounterSearchRequestRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO theme_chokepoint_counter_search_requests(
                    request_record_id, run_id, route_id, query,
                    assessment_as_of, created_at, segment_id, assessment_scope_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.request_record_id,
                    record.run_id,
                    record.route_id,
                    record.query,
                    record.assessment_as_of,
                    record.created_at.isoformat(),
                    record.segment_id,
                    record.assessment_scope_sha256,
                ),
            )
        return record

    def save_counter_search_raw_response(
        self, record: CounterSearchRawResponseRecord
    ) -> CounterSearchRawResponseRecord:
        with self._connect() as connection:
            self._claim_provider_execution(
                connection,
                provider_identity=record.provider,
                provider_account_or_route_identity=record.provider,
                upstream_trace_id=record.provider_trace_id,
                raw_response_sha256=record.raw_response_sha256,
                raw_body=record.raw_body,
                consumer_kind="counter_search",
                consumer_record_id=record.response_record_id,
                claimed_at=record.retrieved_at,
            )
            connection.execute(
                """
                INSERT INTO theme_chokepoint_counter_search_raw_responses(
                    response_record_id, request_record_id, run_id, provider,
                    provider_trace_id, http_status, raw_body,
                    raw_response_sha256, retrieved_at, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.provider,
                    record.provider_trace_id,
                    record.http_status,
                    record.raw_body,
                    record.raw_response_sha256,
                    record.retrieved_at.isoformat(),
                    record.cost_usd,
                ),
            )
        return record

    def save_counter_search_result(
        self, record: CounterSearchParsedResultRecord
    ) -> CounterSearchParsedResultRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO theme_chokepoint_counter_search_results(
                    result_record_id, response_record_id, request_record_id,
                    run_id, route_id, query, status, evidence_ids_json,
                    new_counter_evidence_ids_json, finding, query_log_id,
                    result_semantics, parsed_at, coverage_state, segment_id,
                    assessment_scope_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.result_record_id,
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.route_id,
                    record.query,
                    record.status,
                    _json(record.evidence_ids),
                    _json(record.new_counter_evidence_ids),
                    record.finding,
                    record.query_log_id,
                    record.result_semantics,
                    record.parsed_at.isoformat(),
                    record.coverage_state,
                    record.segment_id,
                    record.assessment_scope_sha256,
                ),
            )
        return record

    def list_counter_search_requests(
        self, run_id: str
    ) -> tuple[CounterSearchRequestRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM theme_chokepoint_counter_search_requests
                WHERE run_id = ? ORDER BY created_at, request_record_id""",
                (run_id,),
            ).fetchall()
        return tuple(
            CounterSearchRequestRecord(
                request_record_id=row["request_record_id"],
                run_id=row["run_id"],
                route_id=row["route_id"],
                query=row["query"],
                assessment_as_of=row["assessment_as_of"],
                created_at=datetime.fromisoformat(row["created_at"]),
                segment_id=row["segment_id"],
                assessment_scope_sha256=row["assessment_scope_sha256"],
            )
            for row in rows
        )

    def list_counter_search_raw_responses(
        self, run_id: str
    ) -> tuple[CounterSearchRawResponseRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM theme_chokepoint_counter_search_raw_responses
                WHERE run_id = ? ORDER BY retrieved_at, response_record_id""",
                (run_id,),
            ).fetchall()
        return tuple(
            CounterSearchRawResponseRecord(
                response_record_id=row["response_record_id"],
                request_record_id=row["request_record_id"],
                run_id=row["run_id"],
                provider=row["provider"],
                provider_trace_id=row["provider_trace_id"],
                http_status=row["http_status"],
                raw_body=bytes(row["raw_body"]),
                raw_response_sha256=row["raw_response_sha256"],
                retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
                cost_usd=row["cost_usd"],
            )
            for row in rows
        )

    def list_counter_search_results(
        self, run_id: str
    ) -> tuple[CounterSearchParsedResultRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM theme_chokepoint_counter_search_results
                WHERE run_id = ? ORDER BY parsed_at, result_record_id""",
                (run_id,),
            ).fetchall()
        return tuple(
            CounterSearchParsedResultRecord(
                result_record_id=row["result_record_id"],
                response_record_id=row["response_record_id"],
                request_record_id=row["request_record_id"],
                run_id=row["run_id"],
                route_id=row["route_id"],
                query=row["query"],
                status=row["status"],
                evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
                new_counter_evidence_ids=tuple(
                    json.loads(row["new_counter_evidence_ids_json"])
                ),
                finding=row["finding"],
                query_log_id=row["query_log_id"],
                result_semantics=row["result_semantics"],
                parsed_at=datetime.fromisoformat(row["parsed_at"]),
                coverage_state=row["coverage_state"],
                segment_id=row["segment_id"],
                assessment_scope_sha256=row["assessment_scope_sha256"],
            )
            for row in rows
        )

    def materialize_counter_evidence(
        self,
        *,
        result: CounterSearchParsedResultRecord,
        candidates: tuple[CounterEvidenceCandidate, ...],
        retrieved_at: datetime,
    ) -> tuple[str, ...]:
        """Persist parser proposals under the planned result ID and mint no caller IDs."""
        if not _is_aware(retrieved_at):
            raise ValueError("counter evidence retrieval time must be timezone-aware")
        if result.coverage_state != "found" and candidates:
            raise ValueError(
                "non-found counter coverage cannot materialize affirmative evidence"
            )
        if result.coverage_state == "found" and not candidates:
            raise ValueError("counter evidence found requires materialized counter evidence")
        expected_ids = self.derive_counter_evidence_ids(result, candidates)
        ids = []
        with self._connect() as connection:
            for candidate in candidates:
                content_sha = sha256(candidate.original_text.encode("utf-8")).hexdigest()
                source_version_id = "counter_source_" + sha256(
                    "\0".join((result.result_record_id, candidate.canonical_url, content_sha)).encode("utf-8")
                ).hexdigest()[:24]
                claim_id = "counter_claim_" + sha256(
                    "\0".join((source_version_id, candidate.claim_statement)).encode("utf-8")
                ).hexdigest()[:24]
                evidence_id = expected_ids[len(ids)]
                connection.execute(
                    """INSERT INTO theme_chokepoint_counter_source_versions(
                    source_version_id, run_id, segment_id, route_id, result_record_id,
                    canonical_url, content_sha256, original_text, retrieved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (source_version_id, result.run_id, result.segment_id, result.route_id,
                     result.result_record_id, candidate.canonical_url, content_sha,
                     candidate.original_text, retrieved_at.isoformat()),
                )
                connection.execute(
                    """INSERT INTO theme_chokepoint_counter_claims(
                    claim_id, result_record_id, source_version_id, run_id, segment_id,
                    route_id, statement, statement_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (claim_id, result.result_record_id, source_version_id, result.run_id,
                     result.segment_id, result.route_id, candidate.claim_statement,
                     sha256(candidate.claim_statement.encode("utf-8")).hexdigest()),
                )
                connection.execute(
                    """INSERT INTO theme_chokepoint_counter_evidence_cards(
                    evidence_id, claim_id, source_version_id, result_record_id, run_id,
                    segment_id, route_id, assessment_scope_sha256, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (evidence_id, claim_id, source_version_id, result.result_record_id,
                     result.run_id, result.segment_id, result.route_id,
                     result.assessment_scope_sha256, _json(asdict(candidate))),
                )
                ids.append(evidence_id)
        return tuple(ids)

    @staticmethod
    def derive_counter_evidence_ids(result, candidates) -> tuple[str, ...]:
        ids = []
        for candidate in candidates:
            if not all(
                value.strip()
                for value in (
                    candidate.canonical_url,
                    candidate.original_text,
                    candidate.exact_quote,
                    candidate.claim_statement,
                )
            ) or candidate.exact_quote not in candidate.original_text:
                raise ValueError("materialized counter evidence is incomplete")
            content_sha = sha256(candidate.original_text.encode("utf-8")).hexdigest()
            source_version_id = "counter_source_" + sha256(
                "\0".join((result.result_record_id, candidate.canonical_url, content_sha)).encode("utf-8")
            ).hexdigest()[:24]
            claim_id = "counter_claim_" + sha256(
                "\0".join((source_version_id, candidate.claim_statement)).encode("utf-8")
            ).hexdigest()[:24]
            ids.append("counter_evidence_" + sha256(
                "\0".join((claim_id, sha256(candidate.exact_quote.encode("utf-8")).hexdigest())).encode("utf-8")
            ).hexdigest()[:24])
        return tuple(ids)

    def reconcile_counter_search_route(
        self, result: CounterSearchParsedResultRecord
    ) -> CounterSearchReconciliationReceipt:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT req.run_id AS request_run_id, req.route_id AS request_route_id,
                req.segment_id AS request_segment_id, req.assessment_scope_sha256 AS request_scope,
                raw.request_record_id, raw.raw_body, raw.raw_response_sha256,
                res.response_record_id, res.coverage_state
                FROM theme_chokepoint_counter_search_results AS res
                JOIN theme_chokepoint_counter_search_requests AS req
                  ON req.request_record_id = res.request_record_id
                JOIN theme_chokepoint_counter_search_raw_responses AS raw
                  ON raw.response_record_id = res.response_record_id
                WHERE res.result_record_id = ?""",
                (result.result_record_id,),
            ).fetchone()
            if row is None or (
                row["request_run_id"] != result.run_id
                or row["request_route_id"] != result.route_id
                or row["request_segment_id"] != result.segment_id
                or row["request_scope"] != result.assessment_scope_sha256
                or row["request_record_id"] != result.request_record_id
                or row["response_record_id"] != result.response_record_id
                or sha256(row["raw_body"]).hexdigest() != row["raw_response_sha256"]
            ):
                raise ValueError("counter-search reconciliation lineage is incomplete")
            ids = tuple(
                item["evidence_id"]
                for item in connection.execute(
                    """SELECT evidence_id FROM theme_chokepoint_counter_evidence_cards
                    WHERE result_record_id = ? AND run_id = ? AND segment_id = ?
                    AND route_id = ? AND assessment_scope_sha256 = ? ORDER BY evidence_id""",
                    (result.result_record_id, result.run_id, result.segment_id,
                     result.route_id, result.assessment_scope_sha256),
                ).fetchall()
            )
            expected_ids = tuple(sorted(self.derive_counter_evidence_ids(
                result, _counter_candidates_from_raw(bytes(row["raw_body"]))
            )))
            if (
                tuple(sorted(result.new_counter_evidence_ids)) != ids
                or ids != expected_ids
            ):
                raise ValueError("counter-search result new IDs are not derived materialized cards")
            if result.coverage_state == "found" and not ids:
                raise ValueError("counter evidence found requires materialized counter evidence")
            if result.coverage_state != "found" and ids:
                raise ValueError(
                    "non-found counter coverage cannot reconcile materialized evidence"
                )
            if result.coverage_state not in {"found", "explicit_negative", "unknown"}:
                raise ValueError("counter-search coverage state is invalid")
            receipt_id = "counter_reconciliation_" + sha256(
                "\0".join((result.result_record_id, result.coverage_state, *ids)).encode("utf-8")
            ).hexdigest()[:24]
            now = _utc_now()
            connection.execute(
                """INSERT INTO theme_chokepoint_counter_search_reconciliations(
                receipt_id, request_record_id, response_record_id, result_record_id,
                run_id, segment_id, route_id, assessment_scope_sha256, coverage_state,
                decision, reconciled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, result.request_record_id, result.response_record_id,
                 result.result_record_id, result.run_id, result.segment_id,
                 result.route_id, result.assessment_scope_sha256, result.coverage_state,
                 "reconciled", now.isoformat()),
            )
        return CounterSearchReconciliationReceipt(
            receipt_id=receipt_id, request_record_id=result.request_record_id,
            response_record_id=result.response_record_id, result_record_id=result.result_record_id,
            run_id=result.run_id, route_id=result.route_id, decision="reconciled",
            reconciled_at=now, segment_id=result.segment_id,
            assessment_scope_sha256=result.assessment_scope_sha256,
            coverage_state=result.coverage_state, new_counter_evidence_ids=ids,
        )

    def list_reconciled_counter_routes(self, run_id: str, segment_id: str, scope_sha256: str):
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM theme_chokepoint_counter_search_reconciliations
                WHERE run_id = ? AND segment_id = ? AND assessment_scope_sha256 = ?
                AND decision = 'reconciled' ORDER BY reconciled_at, receipt_id""",
                (run_id, segment_id, scope_sha256),
            ).fetchall()
            receipts = []
            for row in rows:
                ids = tuple(item["evidence_id"] for item in connection.execute(
                    "SELECT evidence_id FROM theme_chokepoint_counter_evidence_cards WHERE result_record_id = ? ORDER BY evidence_id",
                    (row["result_record_id"],),
                ).fetchall())
                receipts.append(CounterSearchReconciliationReceipt(
                    receipt_id=row["receipt_id"], request_record_id=row["request_record_id"],
                    response_record_id=row["response_record_id"], result_record_id=row["result_record_id"],
                    run_id=row["run_id"], route_id=row["route_id"], decision=row["decision"],
                    reconciled_at=datetime.fromisoformat(row["reconciled_at"]),
                    segment_id=row["segment_id"], assessment_scope_sha256=row["assessment_scope_sha256"],
                    coverage_state=row["coverage_state"], new_counter_evidence_ids=ids,
                ))
        return tuple(receipts)

    def load_reconciled_counter_evidence(self, receipt_id: str):
        """Reload the materialized current-route counter items consumed by gates."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT rec.run_id, rec.result_record_id, rec.coverage_state,
                          raw.raw_body, raw.raw_response_sha256
                   FROM theme_chokepoint_counter_search_reconciliations AS rec
                   JOIN theme_chokepoint_counter_search_results AS result
                     ON result.result_record_id = rec.result_record_id
                   JOIN theme_chokepoint_counter_search_raw_responses AS raw
                     ON raw.response_record_id = result.response_record_id
                    AND raw.request_record_id = result.request_record_id
                   WHERE rec.receipt_id = ? AND rec.decision = 'reconciled'""",
                (receipt_id,),
            ).fetchone()
            if row is None or sha256(row["raw_body"]).hexdigest() != row["raw_response_sha256"]:
                raise ValueError("counter evidence consumer reconciliation is missing")
            materialized_rows = connection.execute(
                """SELECT card.evidence_id, card.claim_id AS card_claim_id,
                          card.source_version_id AS card_source_version_id,
                          card.run_id AS card_run_id,
                          card.segment_id AS card_segment_id,
                          card.route_id AS card_route_id,
                          card.assessment_scope_sha256, card.payload_json,
                          claim.claim_id, claim.source_version_id AS claim_source_version_id,
                          claim.run_id AS claim_run_id,
                          claim.segment_id AS claim_segment_id,
                          claim.route_id AS claim_route_id,
                          claim.statement, claim.statement_sha256,
                          source.source_version_id, source.run_id AS source_run_id,
                          source.segment_id AS source_segment_id,
                          source.route_id AS source_route_id,
                          source.canonical_url, source.content_sha256,
                          source.original_text
                     FROM theme_chokepoint_counter_evidence_cards AS card
                     JOIN theme_chokepoint_counter_claims AS claim
                       ON claim.claim_id = card.claim_id
                      AND claim.result_record_id = card.result_record_id
                     JOIN theme_chokepoint_counter_source_versions AS source
                       ON source.source_version_id = card.source_version_id
                      AND source.source_version_id = claim.source_version_id
                      AND source.result_record_id = card.result_record_id
                    WHERE card.result_record_id = ?
                    ORDER BY card.evidence_id""",
                (row["result_record_id"],),
            ).fetchall()
            stored_ids = tuple(item["evidence_id"] for item in materialized_rows)
        result = next(
            (
                item
                for item in self.list_counter_search_results(row["run_id"])
                if item.result_record_id == row["result_record_id"]
            ),
            None,
        )
        if result is None:
            raise ValueError("counter evidence consumer result is missing")
        candidates = _counter_candidates_from_raw(bytes(row["raw_body"]))
        derived_ids = self.derive_counter_evidence_ids(result, candidates)
        expected_ids = tuple(sorted(derived_ids))
        if stored_ids != expected_ids or tuple(sorted(result.new_counter_evidence_ids)) != stored_ids:
            raise ValueError("counter evidence consumer materialization mismatch")
        if (row["coverage_state"] == "found") != bool(candidates):
            raise ValueError("counter evidence consumer semantics mismatch")
        candidate_by_id = dict(zip(derived_ids, candidates))
        consumed = []
        for item in materialized_rows:
            candidate = candidate_by_id.get(item["evidence_id"])
            if candidate is None:
                raise ValueError("counter evidence consumer materialization mismatch")
            content_sha = sha256(candidate.original_text.encode("utf-8")).hexdigest()
            source_version_id = "counter_source_" + sha256(
                "\0".join(
                    (result.result_record_id, candidate.canonical_url, content_sha)
                ).encode("utf-8")
            ).hexdigest()[:24]
            claim_id = "counter_claim_" + sha256(
                "\0".join((source_version_id, candidate.claim_statement)).encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
            lineage = (
                item["card_claim_id"] == item["claim_id"] == claim_id
                and item["card_source_version_id"]
                == item["claim_source_version_id"]
                == item["source_version_id"]
                == source_version_id
                and item["card_run_id"]
                == item["claim_run_id"]
                == item["source_run_id"]
                == result.run_id
                and item["card_segment_id"]
                == item["claim_segment_id"]
                == item["source_segment_id"]
                == result.segment_id
                and item["card_route_id"]
                == item["claim_route_id"]
                == item["source_route_id"]
                == result.route_id
                and item["assessment_scope_sha256"]
                == result.assessment_scope_sha256
                and item["canonical_url"] == candidate.canonical_url
                and item["content_sha256"] == content_sha
                and item["original_text"] == candidate.original_text
                and item["statement"] == candidate.claim_statement
                and item["statement_sha256"]
                == sha256(candidate.claim_statement.encode("utf-8")).hexdigest()
                and item["payload_json"] == _json(asdict(candidate))
            )
            if not lineage:
                raise ValueError("counter evidence consumer materialization mismatch")
            consumed.append(
                SimpleNamespace(
                    evidence_id=item["evidence_id"],
                    claim_id=claim_id,
                    source_version_id=source_version_id,
                    run_id=result.run_id,
                    segment_id=result.segment_id,
                    route_id=result.route_id,
                    candidate=candidate,
                )
            )
        return tuple(consumed)

    def save_monitoring_evaluator_request(self, record):
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO theme_chokepoint_monitor_evaluator_requests(
                request_record_id, run_id, target_type, target_id,
                evidence_event_ids_json, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.request_record_id,
                    record.run_id,
                    record.target_type,
                    record.target_id,
                    _json(record.evidence_event_ids),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def save_monitoring_evaluator_raw_response(self, record):
        with self._connect() as connection:
            self._claim_provider_execution(
                connection,
                provider_identity=record.provider,
                provider_account_or_route_identity=record.provider,
                upstream_trace_id=record.provider_trace_id,
                raw_response_sha256=record.raw_response_sha256,
                raw_body=record.raw_body,
                consumer_kind="monitoring_evaluator",
                consumer_record_id=record.response_record_id,
                claimed_at=record.retrieved_at,
            )
            connection.execute(
                """INSERT INTO theme_chokepoint_monitor_evaluator_raw_responses(
                response_record_id, request_record_id, run_id, provider,
                provider_trace_id, http_status, raw_body, raw_response_sha256,
                retrieved_at, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.response_record_id,
                    record.request_record_id,
                    record.run_id,
                    record.provider,
                    record.provider_trace_id,
                    record.http_status,
                    record.raw_body,
                    record.raw_response_sha256,
                    record.retrieved_at.isoformat(),
                    record.cost_usd,
                ),
            )
        return record

    def _claim_provider_execution(
        self,
        connection,
        *,
        provider_identity,
        provider_account_or_route_identity,
        upstream_trace_id,
        raw_response_sha256,
        raw_body,
        consumer_kind,
        consumer_record_id,
        claimed_at,
    ):
        text_values = (
            provider_identity,
            provider_account_or_route_identity,
            upstream_trace_id,
            consumer_kind,
            consumer_record_id,
        )
        if not all(isinstance(value, str) and value.strip() for value in text_values):
            raise ValueError("provider execution identity is incomplete")
        if not _is_sha256(raw_response_sha256):
            raise ValueError("provider execution raw response SHA-256 is invalid")
        if raw_response_sha256 != sha256(raw_body).hexdigest():
            raise ValueError("provider execution raw response SHA-256 mismatch")
        if not _is_aware(claimed_at):
            raise ValueError("provider execution claim time must be timezone-aware")
        if connection.execute(
            """SELECT 1 FROM theme_chokepoint_provider_executions
               WHERE provider_identity = ? AND upstream_trace_id = ?""",
            (provider_identity, upstream_trace_id),
        ).fetchone():
            raise ValueError("provider execution trace replay")
        if connection.execute(
            """SELECT 1 FROM theme_chokepoint_provider_executions
               WHERE provider_identity = ? AND raw_response_sha256 = ?""",
            (provider_identity, raw_response_sha256),
        ).fetchone():
            raise ValueError("provider raw execution replay")
        try:
            connection.execute(
                """
                INSERT INTO theme_chokepoint_provider_executions(
                    provider_identity, provider_account_or_route_identity,
                    upstream_trace_id, raw_response_sha256, consumer_kind,
                    consumer_record_id, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_identity,
                    provider_account_or_route_identity,
                    upstream_trace_id,
                    raw_response_sha256,
                    consumer_kind,
                    consumer_record_id,
                    claimed_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("provider execution trace replay") from error

    def save_monitoring_evaluator_result(self, record):
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO theme_chokepoint_monitor_evaluator_results(
                result_record_id, request_record_id, response_record_id, run_id,
                target_type, target_id, parsed_payload_json, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.result_record_id,
                    record.request_record_id,
                    record.response_record_id,
                    record.run_id,
                    record.target_type,
                    record.target_id,
                    record.parsed_payload_json,
                    record.parsed_at.isoformat(),
                ),
            )
        return record

    def list_monitoring_evaluator_requests(self, run_id):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM theme_chokepoint_monitor_evaluator_requests WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return tuple(
            MonitoringEvaluatorRequestRecord(
                request_record_id=row["request_record_id"],
                run_id=row["run_id"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                evidence_event_ids=tuple(json.loads(row["evidence_event_ids_json"])),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def list_monitoring_evaluator_raw_responses(self, run_id):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM theme_chokepoint_monitor_evaluator_raw_responses WHERE run_id = ? ORDER BY retrieved_at",
                (run_id,),
            ).fetchall()
        return tuple(
            MonitoringEvaluatorRawResponseRecord(
                response_record_id=row["response_record_id"],
                request_record_id=row["request_record_id"],
                run_id=row["run_id"],
                provider=row["provider"],
                provider_trace_id=row["provider_trace_id"],
                http_status=row["http_status"],
                raw_body=bytes(row["raw_body"]),
                raw_response_sha256=row["raw_response_sha256"],
                retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
                cost_usd=row["cost_usd"],
            )
            for row in rows
        )

    def monitoring_evaluator_trace_exists(self, provider, provider_trace_id):
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM theme_chokepoint_monitor_evaluator_raw_responses
                WHERE provider = ? AND provider_trace_id = ? LIMIT 1""",
                (provider, provider_trace_id),
            ).fetchone()
        return row is not None

    def get_monitoring_evaluator_raw_response(self, response_record_id):
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM theme_chokepoint_monitor_evaluator_raw_responses
                WHERE response_record_id = ?""",
                (response_record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(response_record_id)
        return MonitoringEvaluatorRawResponseRecord(
            response_record_id=row["response_record_id"],
            request_record_id=row["request_record_id"],
            run_id=row["run_id"],
            provider=row["provider"],
            provider_trace_id=row["provider_trace_id"],
            http_status=row["http_status"],
            raw_body=bytes(row["raw_body"]),
            raw_response_sha256=row["raw_response_sha256"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            cost_usd=row["cost_usd"],
        )

    def list_monitoring_evaluator_results(self, run_id):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM theme_chokepoint_monitor_evaluator_results WHERE run_id = ? ORDER BY parsed_at",
                (run_id,),
            ).fetchall()
        return tuple(
            MonitoringEvaluatorParsedResultRecord(
                result_record_id=row["result_record_id"],
                request_record_id=row["request_record_id"],
                response_record_id=row["response_record_id"],
                run_id=row["run_id"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                parsed_payload_json=row["parsed_payload_json"],
                parsed_at=datetime.fromisoformat(row["parsed_at"]),
            )
            for row in rows
        )

    def create_request(self, request: ResearchRequest) -> Stage1RunSnapshot:
        now = _utc_now().isoformat()
        payload = asdict(request)
        payload["as_of_date"] = request.as_of_date.isoformat()
        payload["seed_products"] = list(request.seed_products)
        payload["seed_companies"] = list(request.seed_companies)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_runs(
                        run_id, request_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (request.run_id, _json(payload), RunStatus.REQUEST_STORED.value, now, now),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"run_id already exists: {request.run_id}") from error
        return self.get_run(request.run_id)

    def save_framing(
        self,
        run_id: str,
        frame: DemandFrame,
        *,
        status: RunStatus,
        anchors: Iterable[ProductAnchor] = (),
    ) -> Stage1RunSnapshot:
        if status not in {
            RunStatus.NEEDS_CLARIFICATION,
            RunStatus.AWAITING_PRODUCT_CONFIRMATION,
        }:
            raise ValueError(f"invalid framing status: {status.value}")
        frame_payload = asdict(frame)
        frame_payload["exclusions"] = list(frame.exclusions)
        frame_payload["measurable_demand_variables"] = list(frame.measurable_demand_variables)
        frame_payload["unresolved_questions"] = list(frame.unresolved_questions)
        now = _utc_now().isoformat()
        anchor_list = tuple(anchors)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if current is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if current["status"] != RunStatus.REQUEST_STORED.value:
                raise ValueError(f"run is not awaiting framing: {current['status']}")
            connection.execute(
                """
                UPDATE theme_chokepoint_runs
                SET demand_frame_json = ?, status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (_json(frame_payload), status.value, now, run_id),
            )
            for ordinal, anchor in enumerate(anchor_list):
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_product_anchors(
                        anchor_id, run_id, ordinal, product_name, buyer_or_user,
                        demand_variable, theme_link, confidence,
                        supporting_evidence_ids_json, missing_evidence_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anchor.anchor_id,
                        run_id,
                        ordinal,
                        anchor.product_name,
                        anchor.buyer_or_user,
                        anchor.demand_variable,
                        anchor.theme_link,
                        anchor.confidence,
                        _json(list(anchor.supporting_evidence_ids)),
                        _json(list(anchor.missing_evidence)),
                        anchor.status,
                    ),
                )
        return self.get_run(run_id)

    def confirm_product_anchors(
        self,
        run_id: str,
        anchor_ids: tuple[str, ...],
        *,
        confirmed_by: str,
    ) -> ConfirmationReceipt:
        selected = tuple(dict.fromkeys(anchor_ids))
        if not selected:
            raise ValueError("at least one product anchor must be confirmed")
        actor = confirmed_by.strip()
        if not actor:
            raise ValueError("confirmed_by is required")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if run["status"] != RunStatus.AWAITING_PRODUCT_CONFIRMATION.value:
                raise ValueError(f"run is not awaiting product confirmation: {run['status']}")
            proposed_rows = connection.execute(
                "SELECT anchor_id FROM theme_chokepoint_product_anchors WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            proposed = {row["anchor_id"] for row in proposed_rows}
            unknown = [anchor_id for anchor_id in selected if anchor_id not in proposed]
            if unknown:
                raise ValueError(f"anchor is not proposed for this run: {unknown[0]}")
            connection.execute(
                """
                UPDATE theme_chokepoint_product_anchors
                SET status = CASE
                    WHEN anchor_id IN ({}) THEN 'confirmed'
                    ELSE 'rejected'
                END
                WHERE run_id = ?
                """.format(",".join("?" for _ in selected)),
                (*selected, run_id),
            )
            connection.execute(
                """
                UPDATE theme_chokepoint_runs
                SET status = ?, confirmed_by = ?, confirmed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    RunStatus.READY_FOR_SUPPLY_CHAIN.value,
                    actor,
                    now.isoformat(),
                    now.isoformat(),
                    run_id,
                ),
            )
        return ConfirmationReceipt(
            run_id=run_id,
            status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            confirmed_anchor_ids=selected,
            confirmed_by=actor,
            confirmed_at=now,
        )

    def get_run(self, run_id: str) -> Stage1RunSnapshot:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id: {run_id}")
            anchor_rows = connection.execute(
                """
                SELECT * FROM theme_chokepoint_product_anchors
                WHERE run_id = ? ORDER BY ordinal
                """,
                (run_id,),
            ).fetchall()
        request_payload = json.loads(run["request_json"])
        request_payload["as_of_date"] = date.fromisoformat(request_payload["as_of_date"])
        request_payload["seed_products"] = tuple(request_payload["seed_products"])
        request_payload["seed_companies"] = tuple(request_payload["seed_companies"])
        frame = None
        if run["demand_frame_json"]:
            frame_payload = json.loads(run["demand_frame_json"])
            frame_payload["exclusions"] = tuple(frame_payload["exclusions"])
            frame_payload["measurable_demand_variables"] = tuple(
                frame_payload["measurable_demand_variables"]
            )
            frame_payload["unresolved_questions"] = tuple(frame_payload["unresolved_questions"])
            frame = DemandFrame(**frame_payload)
        anchors = tuple(
            ProductAnchor(
                anchor_id=row["anchor_id"],
                product_name=row["product_name"],
                buyer_or_user=row["buyer_or_user"],
                demand_variable=row["demand_variable"],
                theme_link=row["theme_link"],
                confidence=row["confidence"],
                supporting_evidence_ids=tuple(json.loads(row["supporting_evidence_ids_json"])),
                missing_evidence=tuple(json.loads(row["missing_evidence_json"])),
                status=row["status"],
            )
            for row in anchor_rows
        )
        return Stage1RunSnapshot(
            run_id=run["run_id"],
            request=ResearchRequest(**request_payload),
            status=RunStatus(run["status"]),
            demand_frame=frame,
            product_anchors=anchors,
            created_at=datetime.fromisoformat(run["created_at"]),
            updated_at=datetime.fromisoformat(run["updated_at"]),
            confirmed_by=run["confirmed_by"],
            confirmed_at=(datetime.fromisoformat(run["confirmed_at"]) if run["confirmed_at"] else None),
        )

    def list_run_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM theme_chokepoint_runs ORDER BY created_at, run_id"
            ).fetchall()
        return tuple(row["run_id"] for row in rows)

    def save_supply_chain_graph(
        self,
        run_id: str,
        *,
        nodes: tuple[SupplyChainNode, ...],
        edges: tuple[DependencyEdge, ...],
        truncation_reasons: tuple[str, ...],
    ) -> SupplyChainGraph:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if run["status"] != RunStatus.READY_FOR_SUPPLY_CHAIN.value:
                raise ValueError(
                    "supply-chain graph requires READY_FOR_SUPPLY_CHAIN; "
                    f"actual={run['status']}"
                )
            for ordinal, node in enumerate(nodes):
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_nodes(
                        node_id, run_id, ordinal, normalized_name, node_type, depth,
                        status, description, aliases_json, product_anchor_id, stop_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.node_id,
                        run_id,
                        ordinal,
                        node.normalized_name,
                        node.node_type,
                        node.depth,
                        node.status,
                        node.description,
                        _json(list(node.aliases)),
                        node.product_anchor_id,
                        node.stop_reason,
                    ),
                )
            for ordinal, edge in enumerate(edges):
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_edges(
                        edge_id, run_id, ordinal, downstream_node_id, upstream_node_id,
                        relation_type, demand_transmission, criticality_hypothesis,
                        substitute_hypothesis, confidence, supporting_claim_ids_json,
                        verification_questions_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.edge_id,
                        run_id,
                        ordinal,
                        edge.downstream_node_id,
                        edge.upstream_node_id,
                        edge.relation_type,
                        edge.demand_transmission,
                        edge.criticality_hypothesis,
                        edge.substitute_hypothesis,
                        edge.confidence,
                        _json(list(edge.supporting_claim_ids)),
                        _json(list(edge.verification_questions)),
                        edge.status,
                    ),
                )
            connection.execute(
                """
                INSERT INTO theme_chokepoint_graphs(run_id, truncation_reasons_json, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, _json(list(truncation_reasons)), now.isoformat()),
            )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.SUPPLY_CHAIN_GRAPH_READY.value, now.isoformat(), run_id),
            )
        return self.get_supply_chain_graph(run_id)

    def get_supply_chain_graph(self, run_id: str) -> SupplyChainGraph:
        with self._connect() as connection:
            graph = connection.execute(
                "SELECT * FROM theme_chokepoint_graphs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if graph is None:
                raise KeyError(f"supply-chain graph not found for run_id: {run_id}")
            run = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            node_rows = connection.execute(
                "SELECT * FROM theme_chokepoint_nodes WHERE run_id = ? ORDER BY ordinal",
                (run_id,),
            ).fetchall()
            edge_rows = connection.execute(
                "SELECT * FROM theme_chokepoint_edges WHERE run_id = ? ORDER BY ordinal",
                (run_id,),
            ).fetchall()
        nodes = tuple(
            SupplyChainNode(
                node_id=row["node_id"],
                normalized_name=row["normalized_name"],
                node_type=row["node_type"],
                depth=row["depth"],
                status=row["status"],
                description=row["description"],
                aliases=tuple(json.loads(row["aliases_json"])),
                product_anchor_id=row["product_anchor_id"],
                stop_reason=row["stop_reason"],
            )
            for row in node_rows
        )
        edges = tuple(
            DependencyEdge(
                edge_id=row["edge_id"],
                downstream_node_id=row["downstream_node_id"],
                upstream_node_id=row["upstream_node_id"],
                relation_type=row["relation_type"],
                demand_transmission=row["demand_transmission"],
                criticality_hypothesis=row["criticality_hypothesis"],
                substitute_hypothesis=row["substitute_hypothesis"],
                confidence=row["confidence"],
                supporting_claim_ids=tuple(json.loads(row["supporting_claim_ids_json"])),
                verification_questions=tuple(json.loads(row["verification_questions_json"])),
                status=row["status"],
            )
            for row in edge_rows
        )
        return SupplyChainGraph(
            run_id=run_id,
            status=RunStatus(run["status"]),
            nodes=nodes,
            edges=edges,
            truncation_reasons=tuple(json.loads(graph["truncation_reasons_json"])),
            created_at=datetime.fromisoformat(graph["created_at"]),
        )

    def save_stage3_result(
        self,
        run_id: str,
        *,
        status: RunStatus,
        contract_version: str,
        executable_contract_id: str,
        executable_contract_sha256: str,
        claims: tuple[Claim, ...],
        evidence_cards: tuple[EvidenceCard, ...],
        source_snapshots: tuple[SourceSnapshot, ...],
        assessments: tuple[SegmentAssessment, ...],
        iterations_completed: int,
        incomplete_reasons: tuple[str, ...],
        provider_request_receipt_ids: tuple[str, ...] = (),
        cost_usd_spent: float = 0.0,
        elapsed_seconds: float = 0.0,
    ) -> Stage3Result:
        if status not in {
            RunStatus.CHOKEPOINT_ASSESSMENT_READY,
            RunStatus.INCOMPLETE_BUDGET_EXHAUSTED,
        }:
            raise ValueError(f"invalid Stage 3 status: {status.value}")
        now = _utc_now()
        result = Stage3Result(
            run_id=run_id,
            status=status,
            contract_version=contract_version,
            executable_contract_id=executable_contract_id,
            executable_contract_sha256=executable_contract_sha256,
            claims=claims,
            evidence_cards=evidence_cards,
            source_snapshots=source_snapshots,
            assessments=assessments,
            iterations_completed=iterations_completed,
            incomplete_reasons=incomplete_reasons,
            created_at=now,
            provider_request_receipt_ids=provider_request_receipt_ids,
            cost_usd_spent=cost_usd_spent,
            elapsed_seconds=elapsed_seconds,
        )
        payload = _stage3_to_payload(result)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if run["status"] != RunStatus.SUPPLY_CHAIN_GRAPH_READY.value:
                raise ValueError(
                    "Stage 3 persistence requires SUPPLY_CHAIN_GRAPH_READY; "
                    f"actual={run['status']}"
                )
            self._materialize_legacy_source_usages(
                connection,
                run_id=run_id,
                stage="stage3",
                snapshots=source_snapshots,
                retrieved_at=now,
            )
            connection.execute(
                """
                INSERT INTO theme_chokepoint_stage3_results(run_id, payload_json, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, _json(payload), now.isoformat()),
            )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status.value, now.isoformat(), run_id),
            )
        return result

    def get_stage3_result(self, run_id: str) -> Stage3Result:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM theme_chokepoint_stage3_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Stage 3 result not found for run_id: {run_id}")
        return _stage3_from_payload(json.loads(row["payload_json"]))

    def list_evidence_cards(self, run_id: str) -> tuple[EvidenceCard, ...]:
        try:
            cards = self.get_stage3_result(run_id).evidence_cards
        except KeyError:
            return ()
        try:
            company_cards = self.get_stage4_result(run_id).company_evidence_cards
        except KeyError:
            company_cards = ()
        combined = (*cards, *company_cards)
        if len({card.evidence_id for card in combined}) != len(combined):
            raise ValueError("run evidence ledger contains duplicate evidence IDs")
        return combined

    def save_stage4_result(
        self,
        run_id: str,
        *,
        contract_version: str,
        executable_contract_id: str,
        executable_contract_sha256: str,
        challenger_sets: tuple[ChallengerSet, ...],
        company_assessments: tuple[CompanyAssessment, ...],
        company_claims: tuple[Claim, ...] = (),
        company_evidence_cards: tuple[EvidenceCard, ...] = (),
        company_source_snapshots: tuple[SourceSnapshot, ...] = (),
        company_chain_receipts: tuple[CompanyChainReceipt, ...] = (),
        provider_request_receipt_ids: tuple[str, ...] = (),
        cost_usd_spent: float = 0.0,
        status: RunStatus = RunStatus.COMPANY_ASSESSMENT_READY,
        incomplete_reasons: tuple[str, ...] = (),
        segment_recompute_requests: tuple[SegmentRecomputeRequest, ...] = (),
    ) -> Stage4Result:
        if status not in {
            RunStatus.COMPANY_ASSESSMENT_READY,
            RunStatus.COMPANY_ASSESSMENT_INCOMPLETE,
        }:
            raise ValueError("Stage 4 result status is invalid")
        if status is RunStatus.COMPANY_ASSESSMENT_READY and incomplete_reasons:
            raise ValueError("ready Stage 4 result cannot contain incomplete reasons")
        if status is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE and not incomplete_reasons:
            raise ValueError("incomplete Stage 4 result requires reasons")
        now = _utc_now()
        result = Stage4Result(
            run_id=run_id,
            status=status,
            contract_version=contract_version,
            executable_contract_id=executable_contract_id,
            executable_contract_sha256=executable_contract_sha256,
            challenger_sets=challenger_sets,
            company_assessments=company_assessments,
            created_at=now,
            company_claims=company_claims,
            company_evidence_cards=company_evidence_cards,
            company_source_snapshots=company_source_snapshots,
            company_chain_receipts=company_chain_receipts,
            provider_request_receipt_ids=provider_request_receipt_ids,
            cost_usd_spent=cost_usd_spent,
            incomplete_reasons=incomplete_reasons,
            segment_recompute_requests=segment_recompute_requests,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if run["status"] not in {
                RunStatus.CHOKEPOINT_ASSESSMENT_READY.value,
                RunStatus.COMPANY_ASSESSMENT_INCOMPLETE.value,
            }:
                raise ValueError(
                    "Stage 4 persistence requires CHOKEPOINT_ASSESSMENT_READY; "
                    f"actual={run['status']}"
                )
            self._materialize_legacy_source_usages(
                connection,
                run_id=run_id,
                stage="stage4",
                snapshots=company_source_snapshots,
                retrieved_at=now,
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO theme_chokepoint_stage4_results(run_id, payload_json, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, _json(_to_jsonable(asdict(result))), now.isoformat()),
            )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status.value, now.isoformat(), run_id),
            )
        return result

    def get_stage4_result(self, run_id: str) -> Stage4Result:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM theme_chokepoint_stage4_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Stage 4 result not found for run_id: {run_id}")
        return _stage4_from_payload(json.loads(row["payload_json"]))

    def mark_persistent_research_ready(self, run_id: str) -> None:
        now = _utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if row["status"] != RunStatus.COMPANY_ASSESSMENT_READY.value:
                raise ValueError(
                    "persistent product transition requires COMPANY_ASSESSMENT_READY; "
                    f"actual={row['status']}"
                )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.PERSISTENT_RESEARCH_READY.value, now, run_id),
            )

    def mark_signal_export_ready(self, run_id: str) -> None:
        now = _utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown run_id: {run_id}")
            if row["status"] not in {
                RunStatus.PERSISTENT_RESEARCH_READY.value,
                RunStatus.MONITORING_READY.value,
            }:
                raise ValueError(
                    "signal export transition requires persistent or monitored research"
                )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.SIGNAL_EXPORT_READY.value, now, run_id),
            )

    def list_research_object_ids(self, run_id: str) -> set[tuple[str, str]]:
        self.get_run(run_id)
        result: set[tuple[str, str]] = {("run", run_id)}
        with self._connect() as connection:
            for object_type, table, id_column in (
                ("product_anchor", "theme_chokepoint_product_anchors", "anchor_id"),
                ("supply_chain_node", "theme_chokepoint_nodes", "node_id"),
                ("dependency_edge", "theme_chokepoint_edges", "edge_id"),
            ):
                rows = connection.execute(
                    f"SELECT {id_column} AS object_id FROM {table} WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
                result.update((object_type, row["object_id"]) for row in rows)
        try:
            stage3 = self.get_stage3_result(run_id)
        except KeyError:
            stage3 = None
        if stage3:
            result.update(("claim", item.claim_id) for item in stage3.claims)
            result.update(("evidence_card", item.evidence_id) for item in stage3.evidence_cards)
            result.update(
                ("segment_assessment", item.segment_id) for item in stage3.assessments
            )
        try:
            stage4 = self.get_stage4_result(run_id)
        except KeyError:
            stage4 = None
        if stage4:
            result.update(
                ("company_assessment", item.assessment_id)
                for item in stage4.company_assessments
            )
            result.update(
                ("challenger_set", item.challenger_set_id)
                for item in stage4.challenger_sets
            )
        return result

    def save_feedback(self, correction: FeedbackCorrection) -> FeedbackCorrection:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO theme_chokepoint_feedback(
                    correction_id, run_id, object_type, object_id, field_name,
                    proposed_value, rationale, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction.correction_id,
                    correction.run_id,
                    correction.object_type,
                    correction.object_id,
                    correction.field_name,
                    correction.proposed_value,
                    correction.rationale,
                    correction.actor,
                    correction.created_at.isoformat(),
                ),
            )
        return correction

    def list_feedback(self, run_id: str) -> tuple[FeedbackCorrection, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM theme_chokepoint_feedback
                WHERE run_id = ? ORDER BY created_at, correction_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            FeedbackCorrection(
                correction_id=row["correction_id"],
                run_id=row["run_id"],
                object_type=row["object_type"],
                object_id=row["object_id"],
                field_name=row["field_name"],
                proposed_value=row["proposed_value"],
                rationale=row["rationale"],
                actor=row["actor"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def save_hotspot_candidates(
        self, candidates: tuple[HotspotCandidate, ...]
    ) -> tuple[HotspotCandidate, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in candidates:
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_hotspots(hotspot_id, payload_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        item.hotspot_id,
                        _json(_to_jsonable(asdict(item))),
                        item.created_at.isoformat(),
                    ),
                )
        return candidates

    def decide_hotspot(self, hotspot_id: str, decision: str, actor: str) -> HotspotCandidate:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM theme_chokepoint_hotspots WHERE hotspot_id = ?",
                (hotspot_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown hotspot_id: {hotspot_id}")
            current = _hotspot_from_payload(json.loads(row["payload_json"]))
            if current.status != "proposed":
                raise ValueError("hotspot decision is already committed")
            updated = HotspotCandidate(
                **{
                    **current.__dict__,
                    "status": decision,
                    "decided_by": actor,
                    "decided_at": now,
                }
            )
            connection.execute(
                "UPDATE theme_chokepoint_hotspots SET payload_json = ? WHERE hotspot_id = ?",
                (_json(_to_jsonable(asdict(updated))), hotspot_id),
            )
        return updated

    def list_hotspots(self) -> tuple[HotspotCandidate, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM theme_chokepoint_hotspots ORDER BY created_at, hotspot_id"
            ).fetchall()
        return tuple(_hotspot_from_payload(json.loads(row["payload_json"])) for row in rows)

    def save_monitoring_trigger(self, trigger: MonitoringTrigger) -> MonitoringTrigger:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO theme_chokepoint_monitoring_triggers(
                    trigger_id, run_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    trigger.trigger_id,
                    trigger.run_id,
                    _json(_to_jsonable(asdict(trigger))),
                    trigger.created_at.isoformat(),
                ),
            )
        return trigger

    def list_monitoring_triggers(self, run_id: str) -> tuple[MonitoringTrigger, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM theme_chokepoint_monitoring_triggers
                WHERE run_id = ? ORDER BY created_at, trigger_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            _monitoring_trigger_from_payload(json.loads(row["payload_json"]))
            for row in rows
        )

    def save_monitoring_refresh(
        self, result: MonitoringRefreshResult
    ) -> MonitoringRefreshResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM theme_chokepoint_runs WHERE run_id = ?", (result.run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown run_id: {result.run_id}")
            if row["status"] not in {
                RunStatus.PERSISTENT_RESEARCH_READY.value,
                RunStatus.MONITORING_READY.value,
            }:
                raise ValueError("monitoring refresh transition requires a persistent product")
            connection.execute(
                """
                INSERT INTO theme_chokepoint_monitoring_refreshes(
                    refresh_id, run_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    result.refresh_id,
                    result.run_id,
                    _json(_to_jsonable(asdict(result))),
                    result.created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE theme_chokepoint_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.MONITORING_READY.value, result.created_at.isoformat(), result.run_id),
            )
        return result

    def save_monitoring_evidence(
        self, run_id: str, evidence: MonitoringEvidence
    ) -> MonitoringEvidence:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO theme_chokepoint_monitoring_evidence(
                        event_id, run_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        evidence.event_id,
                        run_id,
                        _json(_to_jsonable(asdict(evidence))),
                        evidence.assessment_as_of.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("monitoring event_id is already persisted") from error
        return evidence

    def list_monitoring_evidence(
        self, run_id: str
    ) -> tuple[MonitoringEvidence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM theme_chokepoint_monitoring_evidence
                WHERE run_id = ? ORDER BY created_at, event_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            _monitoring_evidence_from_payload(json.loads(row["payload_json"]))
            for row in rows
        )

    def list_monitoring_refreshes(
        self, run_id: str
    ) -> tuple[MonitoringRefreshResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM theme_chokepoint_monitoring_refreshes
                WHERE run_id = ? ORDER BY created_at, refresh_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            _monitoring_refresh_from_payload(json.loads(row["payload_json"]))
            for row in rows
        )


def _stage3_to_payload(result: Stage3Result) -> dict:
    return _to_jsonable(asdict(result))


def _assessment_scope_from_payload(payload: dict | None) -> AssessmentScope | None:
    if not payload:
        return None
    return AssessmentScope(
        **{
            **payload,
            "as_of_date": date.fromisoformat(payload["as_of_date"]),
        }
    )


def _business_fact_assertions_from_payload(payloads) -> tuple[BusinessFactAssertion, ...]:
    return tuple(
        BusinessFactAssertion(
            **{
                **payload,
                "verification": (
                    FactVerificationReceipt(
                        **{
                            **payload["verification"],
                            "verified_at": datetime.fromisoformat(
                                payload["verification"]["verified_at"]
                            ),
                        }
                    )
                    if payload.get("verification")
                    else None
                ),
            }
        )
        for payload in payloads or ()
    )


def _stage3_from_payload(payload: dict) -> Stage3Result:
    claims = tuple(
        Claim(
            **{
                **claim,
                "evidence_ids": tuple(claim["evidence_ids"]),
                "assessment_scope": _assessment_scope_from_payload(
                    claim.get("assessment_scope")
                ),
                "condition_ids": tuple(claim.get("condition_ids", ())),
                "claim_capabilities": tuple(claim.get("claim_capabilities", ())),
                "subject_company_ids": tuple(claim.get("subject_company_ids", ())),
            }
        )
        for claim in payload["claims"]
    )
    cards = tuple(
        EvidenceCard(
            **{
                **card,
                "publication_date": (
                    date.fromisoformat(card["publication_date"])
                    if card["publication_date"]
                    else None
                ),
                "data_as_of_date": (
                    date.fromisoformat(card["data_as_of_date"])
                    if card["data_as_of_date"]
                    else None
                ),
                "assessment_scope": _assessment_scope_from_payload(
                    card.get("assessment_scope")
                ),
                "condition_ids": tuple(card.get("condition_ids", ())),
                "claim_capabilities": tuple(card.get("claim_capabilities", ())),
                "subject_company_ids": tuple(card.get("subject_company_ids", ())),
                "business_fact_assertions": _business_fact_assertions_from_payload(
                    card.get("business_fact_assertions", ())
                ),
            }
        )
        for card in payload["evidence_cards"]
    )
    assessments = []
    for assessment in payload["assessments"]:
        dimensions = tuple(
            DimensionRatingDraft(
                **{
                    **dimension,
                    "bound_basis": BoundBasis(
                        **{
                            **dimension["bound_basis"],
                            "unresolved_higher_anchors": tuple(
                                dimension["bound_basis"]["unresolved_higher_anchors"]
                            ),
                            "excluded_higher_anchors": tuple(
                                dimension["bound_basis"]["excluded_higher_anchors"]
                            ),
                        }
                    ),
                    "evidence_ids": tuple(dimension["evidence_ids"]),
                    "missing_material_questions": tuple(
                        dimension.get("missing_material_questions", ())
                    ),
                    "anchor_conditions": tuple(
                        AnchorConditionResult(
                            **{
                                **condition,
                                "evidence_ids": tuple(condition["evidence_ids"]),
                                "decisive_claim_ids": tuple(condition["decisive_claim_ids"]),
                                "excluded_higher_anchors": tuple(
                                    condition.get("excluded_higher_anchors", ())
                                ),
                            }
                        )
                        for condition in dimension.get("anchor_conditions", ())
                    ),
                    "decisive_evidence_ids": tuple(
                        dimension.get("decisive_evidence_ids", ())
                    ),
                }
            )
            for dimension in assessment["dimensions"]
        )
        assessments.append(
            SegmentAssessment(
                **{
                    **assessment,
                    "dimensions": dimensions,
                    "missing_material_fields": tuple(
                        assessment["missing_material_fields"]
                    ),
                    "achieved_gates": tuple(assessment.get("achieved_gates", ())),
                    "relief_assessment": _relief_assessment_from_payload(
                        assessment.get("relief_assessment")
                    ),
                    "critic_receipt": (
                        SegmentCriticReceipt(
                            **{
                                **assessment["critic_receipt"],
                                "route_findings": tuple(
                                    SegmentCounterSearchRoute(
                                        **{
                                            **route,
                                            "evidence_ids": tuple(route["evidence_ids"]),
                                            "new_counter_evidence_ids": tuple(
                                                route.get("new_counter_evidence_ids", ())
                                            ),
                                            "executed_at": datetime.fromisoformat(
                                                route["executed_at"]
                                            ),
                                        }
                                    )
                                    for route in assessment["critic_receipt"].get(
                                        "route_findings", ()
                                    )
                                ),
                                "provider_request_receipt_ids": tuple(
                                    assessment["critic_receipt"].get(
                                        "provider_request_receipt_ids", ()
                                    )
                                ),
                                "query_log_ids": tuple(
                                    assessment["critic_receipt"]["query_log_ids"]
                                ),
                                "unresolved_routes": tuple(
                                    assessment["critic_receipt"]["unresolved_routes"]
                                ),
                                "demand_evidence_ids": tuple(
                                    assessment["critic_receipt"]["demand_evidence_ids"]
                                ),
                                "supply_evidence_ids": tuple(
                                    assessment["critic_receipt"]["supply_evidence_ids"]
                                ),
                                "key_source_evidence_ids": tuple(
                                    assessment["critic_receipt"]["key_source_evidence_ids"]
                                ),
                                "completed_at": datetime.fromisoformat(
                                    assessment["critic_receipt"]["completed_at"]
                                ),
                            }
                        )
                        if assessment.get("critic_receipt")
                        else None
                    ),
                }
            )
        )
    return Stage3Result(
        run_id=payload["run_id"],
        status=RunStatus(payload["status"]),
        contract_version=payload["contract_version"],
        executable_contract_id=payload["executable_contract_id"],
        executable_contract_sha256=payload["executable_contract_sha256"],
        claims=claims,
        evidence_cards=cards,
        source_snapshots=tuple(
            SourceSnapshot(**snapshot)
            for snapshot in payload.get("source_snapshots", ())
        ),
        assessments=tuple(assessments),
        iterations_completed=payload["iterations_completed"],
        incomplete_reasons=tuple(payload["incomplete_reasons"]),
        created_at=datetime.fromisoformat(payload["created_at"]),
        provider_request_receipt_ids=tuple(
            payload.get("provider_request_receipt_ids", ())
        ),
        cost_usd_spent=float(payload.get("cost_usd_spent", 0.0)),
        elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
    )


def _relief_assessment_from_payload(payload):
    if payload is None:
        return None

    def scenario(item):
        return ReliefScenarioResult(
            **{
                **item,
                "periods": tuple(
                    ReliefPeriodResult(
                        **{
                            **period,
                            "period_start": date.fromisoformat(period["period_start"]),
                            "period_end": date.fromisoformat(period["period_end"]),
                        }
                    )
                    for period in item["periods"]
                ),
                "node_relief_date": (
                    date.fromisoformat(item["node_relief_date"])
                    if item["node_relief_date"]
                    else None
                ),
                "system_relief_date": (
                    date.fromisoformat(item["system_relief_date"])
                    if item["system_relief_date"]
                    else None
                ),
            }
        )

    return ReliefHorizonAssessment(
        base=scenario(payload["base"]),
        stress=scenario(payload["stress"]),
    )


def _to_jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, RunStatus):
        return value.value
    return value


def _dimension_from_payload(dimension: dict) -> DimensionRatingDraft:
    return DimensionRatingDraft(
        **{
            **dimension,
            "bound_basis": BoundBasis(
                **{
                    **dimension["bound_basis"],
                    "unresolved_higher_anchors": tuple(
                        dimension["bound_basis"]["unresolved_higher_anchors"]
                    ),
                    "excluded_higher_anchors": tuple(
                        dimension["bound_basis"]["excluded_higher_anchors"]
                    ),
                }
            ),
            "evidence_ids": tuple(dimension["evidence_ids"]),
            "missing_material_questions": tuple(
                dimension.get("missing_material_questions", ())
            ),
            "anchor_conditions": tuple(
                AnchorConditionResult(
                    **{
                        **condition,
                        "evidence_ids": tuple(condition["evidence_ids"]),
                        "decisive_claim_ids": tuple(condition["decisive_claim_ids"]),
                        "excluded_higher_anchors": tuple(
                            condition.get("excluded_higher_anchors", ())
                        ),
                    }
                )
                for condition in dimension.get("anchor_conditions", ())
            ),
            "decisive_evidence_ids": tuple(
                dimension.get("decisive_evidence_ids", ())
            ),
        }
    )


def _score_family_from_payload(payload: dict | None) -> ScoreFamilyAssessment | None:
    if payload is None:
        return None
    return ScoreFamilyAssessment(
        **{
            **payload,
            "dimensions": tuple(
                _dimension_from_payload(item) for item in payload["dimensions"]
            ),
            "achieved_gates": tuple(payload["achieved_gates"]),
        }
    )


def _stage4_from_payload(payload: dict) -> Stage4Result:
    challenger_sets = tuple(
        ChallengerSet(
            **{
                **item,
                "included_company_ids": tuple(item["included_company_ids"]),
                "included_assessment_ids": tuple(
                    item.get("included_assessment_ids", ())
                ),
                "assessment_scope": (
                    _assessment_scope_from_payload(item["assessment_scope"])
                    if isinstance(item.get("assessment_scope"), dict)
                    else item["assessment_scope"]
                ),
                "excluded_candidates_with_reason": tuple(
                    tuple(pair) for pair in item["excluded_candidates_with_reason"]
                ),
                "search_completed_at": datetime.fromisoformat(item["search_completed_at"]),
                "as_of_date": date.fromisoformat(item["as_of_date"]),
                "counter_search_receipt": (
                    CounterSearchReceipt(
                        **{
                            **item["counter_search_receipt"],
                            "query_log_ids": tuple(
                                item["counter_search_receipt"]["query_log_ids"]
                            ),
                            "route_findings": tuple(
                                CounterSearchRouteFinding(
                                    **{
                                        **finding,
                                        "query_log_ids": tuple(finding["query_log_ids"]),
                                        "evidence_ids": tuple(finding["evidence_ids"]),
                                        "executed_at": (
                                            datetime.fromisoformat(
                                                finding["executed_at"]
                                            )
                                            if finding.get("executed_at")
                                            else None
                                        ),
                                    }
                                )
                                for finding in item["counter_search_receipt"][
                                    "route_findings"
                                ]
                            ),
                            "negative_findings": tuple(
                                item["counter_search_receipt"]["negative_findings"]
                            ),
                            "completed_at": datetime.fromisoformat(
                                item["counter_search_receipt"]["completed_at"]
                            ),
                            "provider_request_receipt_ids": tuple(
                                item["counter_search_receipt"].get(
                                    "provider_request_receipt_ids", ()
                                )
                            ),
                        }
                    )
                    if item.get("counter_search_receipt")
                    else None
                ),
            }
        )
        for item in payload["challenger_sets"]
    )
    assessments = []
    for item in payload["company_assessments"]:
        scope = CompanyScope(
            **{
                **item["scope"],
                "as_of_date": date.fromisoformat(item["scope"]["as_of_date"]),
            }
        )
        exposure = CompanyExposure(
            **{
                **item["exposure"],
                "evidence_ids": tuple(item["exposure"]["evidence_ids"]),
            }
        )
        milestones = MilestoneAxes(**item["milestones"]) if item["milestones"] else None
        reviews = tuple(
            RedTeamReview(
                **{
                    **review,
                    "counter_evidence_queries": tuple(review["counter_evidence_queries"]),
                    "unresolved_counterarguments": tuple(
                        review["unresolved_counterarguments"]
                    ),
                    "falsification_conditions": tuple(review["falsification_conditions"]),
                    "evidence_ids": tuple(review["evidence_ids"]),
                }
            )
            for review in item["red_team_reviews"]
        )
        assessments.append(
            CompanyAssessment(
                **{
                    **item,
                    "scope": scope,
                    "roles": tuple(item["roles"]),
                    "exposure": exposure,
                    "defensibility": _score_family_from_payload(item["defensibility"]),
                    "replacement": _score_family_from_payload(item["replacement"]),
                    "earnings": _score_family_from_payload(item["earnings"]),
                    "milestones": milestones,
                    "competition_achieved_gates": tuple(
                        item["competition_achieved_gates"]
                    ),
                    "gate_results": tuple(
                        GateResult(
                            **{
                                **gate,
                                "required_predicate_ids": tuple(
                                    gate["required_predicate_ids"]
                                ),
                                "decisive_evidence_ids": tuple(
                                    gate["decisive_evidence_ids"]
                                ),
                            }
                        )
                        for gate in item.get("gate_results", ())
                    ),
                    "red_team_reviews": reviews,
                }
            )
        )
    return Stage4Result(
        run_id=payload["run_id"],
        status=RunStatus(payload["status"]),
        contract_version=payload["contract_version"],
        executable_contract_id=payload["executable_contract_id"],
        executable_contract_sha256=payload["executable_contract_sha256"],
        challenger_sets=challenger_sets,
        company_assessments=tuple(assessments),
        created_at=datetime.fromisoformat(payload["created_at"]),
        company_claims=tuple(
            Claim(
                **{
                    **claim,
                    "evidence_ids": tuple(claim["evidence_ids"]),
                    "assessment_scope": _assessment_scope_from_payload(
                        claim.get("assessment_scope")
                    ),
                    "condition_ids": tuple(claim.get("condition_ids", ())),
                    "claim_capabilities": tuple(
                        claim.get("claim_capabilities", ())
                    ),
                    "subject_company_ids": tuple(
                        claim.get("subject_company_ids", ())
                    ),
                }
            )
            for claim in payload.get("company_claims", ())
        ),
        company_evidence_cards=tuple(
            EvidenceCard(
                **{
                    **card,
                    "publication_date": (
                        date.fromisoformat(card["publication_date"])
                        if card["publication_date"]
                        else None
                    ),
                    "data_as_of_date": (
                        date.fromisoformat(card["data_as_of_date"])
                        if card["data_as_of_date"]
                        else None
                    ),
                    "assessment_scope": _assessment_scope_from_payload(
                        card.get("assessment_scope")
                    ),
                    "condition_ids": tuple(card.get("condition_ids", ())),
                    "claim_capabilities": tuple(
                        card.get("claim_capabilities", ())
                    ),
                    "subject_company_ids": tuple(
                        card.get("subject_company_ids", ())
                    ),
                    "business_fact_assertions": _business_fact_assertions_from_payload(
                        card.get("business_fact_assertions", ())
                    ),
                }
            )
            for card in payload.get("company_evidence_cards", ())
        ),
        company_source_snapshots=tuple(
            SourceSnapshot(**snapshot)
            for snapshot in payload.get("company_source_snapshots", ())
        ),
        company_chain_receipts=tuple(
            CompanyChainReceipt(
                **{
                    **receipt,
                    "mapper_request_receipt_ids": tuple(
                        receipt["mapper_request_receipt_ids"]
                    ),
                    "acquisition_request_receipt_ids": tuple(
                        receipt["acquisition_request_receipt_ids"]
                    ),
                    "acquired_evidence_ids": tuple(
                        receipt["acquired_evidence_ids"]
                    ),
                }
            )
            for receipt in payload.get("company_chain_receipts", ())
        ),
        provider_request_receipt_ids=tuple(
            payload.get("provider_request_receipt_ids", ())
        ),
        cost_usd_spent=payload.get("cost_usd_spent", 0.0),
        incomplete_reasons=tuple(payload.get("incomplete_reasons", ())),
        segment_recompute_requests=tuple(
            SegmentRecomputeRequest(
                **{
                    **item,
                    "triggering_claim_ids": tuple(item["triggering_claim_ids"]),
                    "triggering_evidence_ids": tuple(
                        item["triggering_evidence_ids"]
                    ),
                    "created_at": datetime.fromisoformat(item["created_at"]),
                }
            )
            for item in payload.get("segment_recompute_requests", ())
        ),
    )


def _hotspot_from_payload(payload: dict) -> HotspotCandidate:
    return HotspotCandidate(
        **{
            **payload,
            "trigger_event_ids": tuple(payload["trigger_event_ids"]),
            "decided_at": (
                datetime.fromisoformat(payload["decided_at"])
                if payload["decided_at"]
                else None
            ),
            "created_at": datetime.fromisoformat(payload["created_at"]),
        }
    )


def _monitoring_trigger_from_payload(payload: dict) -> MonitoringTrigger:
    return MonitoringTrigger(
        **{
            **payload,
            "event_types": tuple(payload["event_types"]),
            "created_at": datetime.fromisoformat(payload["created_at"]),
        }
    )


def _monitoring_refresh_from_payload(payload: dict) -> MonitoringRefreshResult:
    changes = tuple(
        MonitoringChange(
            **{
                **item,
                "changed_claim_ids": tuple(item["changed_claim_ids"]),
                "changed_dimensions": tuple(item["changed_dimensions"]),
                "responsible_event_ids": tuple(item["responsible_event_ids"]),
                "responsible_evidence_ids": tuple(item["responsible_evidence_ids"]),
            }
        )
        for item in payload["changes"]
    )
    return MonitoringRefreshResult(
        refresh_id=payload["refresh_id"],
        run_id=payload["run_id"],
        status=RunStatus(payload["status"]),
        trigger_ids=tuple(payload["trigger_ids"]),
        changes=changes,
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def _monitoring_evidence_from_payload(payload: dict) -> MonitoringEvidence:
    claims = tuple(
        Claim(
            **{
                **claim,
                "evidence_ids": tuple(claim["evidence_ids"]),
                "assessment_scope": _assessment_scope_from_payload(
                    claim.get("assessment_scope")
                ),
                "condition_ids": tuple(claim.get("condition_ids", ())),
                "claim_capabilities": tuple(claim.get("claim_capabilities", ())),
                "subject_company_ids": tuple(
                    claim.get("subject_company_ids", ())
                ),
            }
        )
        for claim in payload.get("new_claims", ())
    )
    cards = tuple(
        EvidenceCard(
            **{
                **card,
                "publication_date": (
                    date.fromisoformat(card["publication_date"])
                    if card.get("publication_date")
                    else None
                ),
                "data_as_of_date": (
                    date.fromisoformat(card["data_as_of_date"])
                    if card.get("data_as_of_date")
                    else None
                ),
                "assessment_scope": _assessment_scope_from_payload(
                    card.get("assessment_scope")
                ),
                "condition_ids": tuple(card.get("condition_ids", ())),
                "claim_capabilities": tuple(card.get("claim_capabilities", ())),
                "subject_company_ids": tuple(
                    card.get("subject_company_ids", ())
                ),
                "business_fact_assertions": _business_fact_assertions_from_payload(
                    card.get("business_fact_assertions", ())
                ),
            }
        )
        for card in payload.get("new_evidence_cards", ())
    )
    return MonitoringEvidence(
        event_id=payload["event_id"],
        event_type=payload["event_type"],
        target_type=payload["target_type"],
        target_id=payload["target_id"],
        evidence_ids=tuple(payload["evidence_ids"]),
        event_time=datetime.fromisoformat(payload["event_time"]),
        published_at=datetime.fromisoformat(payload["published_at"]),
        assessment_as_of=datetime.fromisoformat(payload["assessment_as_of"]),
        new_claims=claims,
        new_evidence_cards=cards,
        new_source_snapshots=tuple(
            SourceSnapshot(**snapshot)
            for snapshot in payload.get("new_source_snapshots", ())
        ),
        source_version_ids=tuple(payload.get("source_version_ids", ())),
        governance_bundle_sha256=payload.get("governance_bundle_sha256"),
    )
    DimensionRatingDraft,
    EvidenceCard,
    RedTeamReview,
