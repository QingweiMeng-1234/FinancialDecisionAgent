"""Stage 6: automatic research-priority hotspots and evidence-bounded monitoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult,
    AssessmentRevisionRecord,
    BoundBasis,
    CanonicalScoringParsedResultRecord,
    CanonicalScoringRawProviderResponse,
    CanonicalScoringRawResponseRecord,
    CanonicalScoringReconciliationRecord,
    CanonicalScoringRequestRecord,
    DimensionRatingDraft,
    HotspotCandidate,
    HotspotDraft,
    HotspotEvent,
    MonitoringChange,
    MonitoringEvidence,
    MonitoringEvaluatorParsedResultRecord,
    MonitoringEvaluatorRawResponse,
    MonitoringEvaluatorRawResponseRecord,
    MonitoringEvaluatorRequestRecord,
    MonitoringRefreshResult,
    MonitoringTrigger,
    MonitoringTriggerDraft,
    ObjectRefreshDraft,
    RunStatus,
    SegmentScoreDraft,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.governance import (
    CANONICAL_GOVERNANCE_BUNDLE_ID,
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    RuntimeGovernanceBundle,
)
from event_collector.theme_chokepoint.source_identity import (
    canonicalize_source_url,
    normalized_quote_sha256,
    normalized_source_content_sha256,
)
from event_collector.theme_chokepoint.stage3 import (
    FrozenScoringContract,
    _finalize_assessment,
)
from event_collector.theme_chokepoint.stage4 import _score_defensibility


TREND_STATES = {
    "assessment_revised",
    "coexisting_constraints",
    "emerging",
    "strengthening",
    "weakening",
    "transferred",
    "invalidated",
    "unchanged",
}
CHANGE_TYPES = {"knowledge_revision", "industry_event"}
@dataclass(frozen=True)
class PersistedAssessmentSnapshot:
    state: str | None
    score_min: float
    score_max: float
    mandatory_gate_level: int
    mandatory_floor_valid: bool
    executable_contract_id: str
    executable_contract_sha256: str


@dataclass(frozen=True)
class CanonicalAssessmentResult:
    target_type: str
    target_id: str
    after_state: str | None
    after_score_min: float
    after_score_max: float
    after_mandatory_gate_level: int
    after_mandatory_floor_valid: bool
    changed_claim_ids: tuple[str, ...]
    changed_dimensions: tuple[str, ...]
    explanation: str
    recompute_receipt_id: str
    executable_contract_id: str
    executable_contract_sha256: str
    original_constraint_active: bool = False
    former_constraint_easing: bool = False
    adjacent_constraint_strengthening: bool = False
    underlying_demand_intact: bool = True
    relief_assessment: object | None = None


@dataclass(frozen=True)
class MonitoringObservation:
    """A monitoring evaluator's non-authoritative fact-change proposal."""

    target_type: str
    target_id: str
    changed_claim_ids: tuple[str, ...]
    changed_dimensions: tuple[str, ...]
    explanation: str


class Stage34CanonicalCalculator:
    """Concrete Stage 3/4 state authority under one exact governance bundle."""

    def __init__(
        self,
        repository,
        raw_executor,
        *,
        governance_bundle_path=None,
        expected_governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    ):
        self.repository = repository
        self.raw_executor = raw_executor
        if expected_governance_bundle_sha256 != CANONICAL_GOVERNANCE_BUNDLE_SHA256:
            raise ValueError("canonical scoring requires the exact governance bundle")
        self.governance_bundle_path = governance_bundle_path
        self.expected_governance_bundle_sha256 = expected_governance_bundle_sha256
        self.governance_bundle = self._load_governance_bundle()

    def _load_governance_bundle(self):
        self.governance_bundle = RuntimeGovernanceBundle(
            self.governance_bundle_path,
            expected_sha256=self.expected_governance_bundle_sha256,
        )
        if self.governance_bundle.bundle_id != CANONICAL_GOVERNANCE_BUNDLE_ID:
            raise ValueError("canonical scoring governance bundle ID mismatch")
        try:
            overlay = json.loads(
                self.governance_bundle.artifact_path("runtime_overlay").read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as error:
            raise ValueError("canonical runtime overlay is invalid JSON") from error
        contract_id = overlay.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ValueError("canonical runtime overlay contract ID is required")
        self.executable_contract_id = contract_id
        self.executable_contract_sha256 = self.governance_bundle.artifact_sha256[
            "runtime_overlay"
        ]
        self.contract = FrozenScoringContract(
            self.governance_bundle.artifact_path("runtime_overlay"),
            expected_sha256=self.executable_contract_sha256,
            allow_unfrozen_overlay=True,
            governance_bundle_path=self.governance_bundle.path,
            expected_governance_bundle_sha256=self.governance_bundle.sha256,
        )
        return self.governance_bundle

    @property
    def calls(self):
        return getattr(self.raw_executor, "calls", ())

    def score(
        self,
        *,
        base_run_id: str,
        target_type: str,
        target_id: str,
        evidence: tuple[MonitoringEvidence, ...],
        observation: MonitoringObservation,
        persisted: PersistedAssessmentSnapshot,
    ) -> CanonicalScoringRawProviderResponse:
        bundle = self._load_governance_bundle()
        if (
            persisted.executable_contract_id != self.executable_contract_id
            or persisted.executable_contract_sha256
            != self.executable_contract_sha256
        ):
            raise ValueError(
                "persisted Stage 3/4 executable contract is not the canonical runtime"
            )
        if not evidence:
            raise ValueError(
                "canonical Stage 3/4 result requires verified monitoring evidence"
            )
        event_ids = {item.event_id for item in evidence}
        if len(event_ids) != len(evidence):
            raise ValueError("canonical Stage 3/4 evidence event IDs must be unique")
        new_claim_ids = set()
        canonical_dimensions = set()
        for item in evidence:
            if (item.target_type, item.target_id) != (target_type, target_id):
                raise ValueError("canonical Stage 3/4 evidence target mismatch")
            if item.governance_bundle_sha256 != bundle.sha256:
                raise ValueError("canonical Stage 3/4 evidence governance mismatch")
            persisted_card_ids = _validate_new_monitoring_records(item)
            if not persisted_card_ids or not set(item.evidence_ids) <= persisted_card_ids:
                raise ValueError(
                    "canonical Stage 3/4 evidence is not bound to new evidence cards"
                )
            if (
                not item.source_version_ids
                or len(item.source_version_ids) != len(item.new_source_snapshots)
            ):
                raise ValueError("canonical Stage 3/4 source-version lineage is incomplete")
            for version_id, snapshot in zip(
                item.source_version_ids, item.new_source_snapshots
            ):
                if not self.repository.authorizes_independent_industry_event(version_id):
                    raise ValueError(
                        "canonical Stage 3/4 source provenance is not independently verified"
                    )
                if not self.repository.source_version_matches_snapshot(
                    version_id, snapshot
                ):
                    raise ValueError(
                        "canonical Stage 3/4 source version does not match snapshot"
                    )
            if not _monitoring_fact_candidates_verified(
                self.repository, base_run_id, item
            ):
                raise ValueError(
                    "canonical Stage 3/4 business facts are not independently verified"
                )
            for claim in item.new_claims:
                new_claim_ids.add(claim.claim_id)
                canonical_dimensions.update(
                    value
                    for value in (
                        claim.material_field,
                        claim.primary_scoring_dimension,
                    )
                    if isinstance(value, str) and value.strip()
                )
        if (
            not observation.changed_claim_ids
            or not set(observation.changed_claim_ids) <= new_claim_ids
        ):
            raise ValueError(
                "canonical Stage 3/4 changed claims are not in verified evidence"
            )
        if not set(observation.changed_dimensions) <= canonical_dimensions:
            raise ValueError(
                "canonical Stage 3/4 changed dimensions are not evidence-bound"
            )

        raw = self.raw_executor.score(
            base_run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            evidence=evidence,
            observation=observation,
            persisted=persisted,
        )
        if not isinstance(raw, CanonicalScoringRawProviderResponse):
            return raw
        proposed, dimension_updates = _parse_canonical_scoring_proposal(raw.raw_body)
        expected = self._recompute_stage34(
            base_run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            evidence=evidence,
            observation=observation,
            persisted=persisted,
            proposed=proposed,
            dimension_updates=dimension_updates,
        )
        proposed_authority = (
            proposed.target_type,
            proposed.target_id,
            proposed.after_state,
            proposed.after_score_min,
            proposed.after_score_max,
            proposed.after_mandatory_gate_level,
            proposed.after_mandatory_floor_valid,
            proposed.changed_claim_ids,
            proposed.changed_dimensions,
            proposed.executable_contract_id,
            proposed.executable_contract_sha256,
            proposed.original_constraint_active,
            proposed.former_constraint_easing,
            proposed.adjacent_constraint_strengthening,
            proposed.underlying_demand_intact,
        )
        expected_authority = (
            expected.target_type,
            expected.target_id,
            expected.after_state,
            expected.after_score_min,
            expected.after_score_max,
            expected.after_mandatory_gate_level,
            expected.after_mandatory_floor_valid,
            expected.changed_claim_ids,
            expected.changed_dimensions,
            expected.executable_contract_id,
            expected.executable_contract_sha256,
            expected.original_constraint_active,
            expected.former_constraint_easing,
            expected.adjacent_constraint_strengthening,
            expected.underlying_demand_intact,
        )
        if proposed_authority != expected_authority:
            raise ValueError(
                "raw executor output does not match the canonical Stage 3/4 result"
            )
        payload = asdict(expected)
        payload.pop("recompute_receipt_id", None)
        payload.pop("relief_assessment", None)
        return replace(
            raw,
            raw_body=json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8"),
        )

    def _recompute_stage34(
        self,
        *,
        base_run_id,
        target_type,
        target_id,
        evidence,
        observation,
        persisted,
        proposed,
        dimension_updates,
    ):
        if dimension_updates and {
            item.dimension for item in dimension_updates
        } != set(observation.changed_dimensions):
            raise ValueError(
                "canonical dimension proposals must cover the observed dimensions exactly"
            )
        if len({item.dimension for item in dimension_updates}) != len(
            dimension_updates
        ):
            raise ValueError("canonical dimension proposals must be unique")

        stage3 = self.repository.get_stage3_result(base_run_id)
        stage4 = self.repository.get_stage4_result(base_run_id)
        new_cards = {
            card.evidence_id: card for item in evidence for card in item.new_evidence_cards
        }
        new_claims = {
            claim.claim_id: claim for item in evidence for claim in item.new_claims
        }
        for update in dimension_updates:
            if not update.evidence_ids or not set(update.evidence_ids) <= set(new_cards):
                raise ValueError(
                    "canonical dimension proposal is not bound to new evidence"
                )
            for evidence_id in update.evidence_ids:
                claim = new_claims.get(new_cards[evidence_id].claim_id)
                if (
                    claim is None
                    or claim.material_field != update.dimension
                    or claim.primary_scoring_dimension != update.dimension
                ):
                    raise ValueError(
                        "canonical dimension proposal does not match its new claim"
                    )

        if target_type == "segment_assessment":
            assessment = next(
                item for item in stage3.assessments if item.segment_id == target_id
            )
            # Old controlled fixtures may contain an intentionally skeletal snapshot.
            # They remain no-change only; every executable production snapshot has the
            # complete frozen family and therefore takes the canonical path below.
            if not assessment.dimensions:
                if dimension_updates:
                    raise ValueError(
                        "canonical Segment recompute requires a complete persisted family"
                    )
                scored = None
            else:
                dimensions = _merge_dimension_updates(
                    assessment.dimensions, dimension_updates
                )
                dimension_by_name = {item.dimension: item for item in dimensions}
                supply_names = {
                    "effective_supply_concentration",
                    "qualification_barrier",
                    "capacity_inelasticity",
                    "substitute_weakness",
                }
                draft = SegmentScoreDraft(
                    segment_id=target_id,
                    dimensions=dimensions,
                    missing_material_fields=assessment.missing_material_fields,
                    demand_direct_evidence=_dimension_supported(
                        dimension_by_name.get("demand_pressure")
                    ),
                    supply_direct_evidence=any(
                        _dimension_supported(dimension_by_name.get(name))
                        for name in supply_names
                    ),
                    counter_evidence_search_complete=(
                        "candidate_gate" in assessment.achieved_gates
                        or "strong_candidate_gate" in assessment.achieved_gates
                        or (
                            assessment.critic_receipt is not None
                            and not assessment.critic_receipt.unresolved_routes
                        )
                    ),
                    mandatory_conflict=any(
                        item.evidence_state == "conflicted" for item in dimensions
                    ),
                    relief_horizon=assessment.relief_horizon,
                    independent_supply_constraint_count=(
                        2
                        if "strong_candidate_gate" in assessment.achieved_gates
                        else 0
                    ),
                    key_source_quality_high=(
                        "strong_candidate_gate" in assessment.achieved_gates
                    ),
                    relief_assessment=assessment.relief_assessment,
                    critic_receipt=assessment.critic_receipt,
                )
                evidence_ids = {
                    card.evidence_id
                    for card in (*stage3.evidence_cards, *new_cards.values())
                }
                scored = _finalize_assessment(
                    draft,
                    self.contract.weights,
                    evidence_ids,
                    self.contract.version,
                )
        elif target_type == "company_assessment":
            company = next(
                item
                for item in stage4.company_assessments
                if item.assessment_id == target_id
            )
            changed = set(observation.changed_dimensions)
            families = tuple(
                family
                for family in (
                    company.defensibility,
                    company.replacement,
                    company.earnings,
                )
                if family is not None
                and changed.intersection(
                    dimension.dimension for dimension in family.dimensions
                )
            )
            if len(families) != 1:
                raise ValueError(
                    "canonical Stage 4 recompute must select one persisted family"
                )
            family = families[0]
            if family.family != "defensibility":
                if dimension_updates:
                    raise ValueError(
                        "canonical Stage 4 recompute lacks the persisted family inputs"
                    )
                scored = family
            else:
                dimensions = _merge_dimension_updates(
                    family.dimensions, dimension_updates
                )
                evidence_ids = {
                    card.evidence_id
                    for card in (
                        *stage3.evidence_cards,
                        *stage4.company_evidence_cards,
                        *new_cards.values(),
                    )
                }
                scored = _score_defensibility(
                    SimpleNamespace(
                        defensibility=dimensions,
                        scope_valid=True,
                        freshness_valid=True,
                        evidence_mapping_error=False,
                    ),
                    self.contract,
                    evidence_ids,
                )
        else:
            raise ValueError("canonical Stage 3/4 target type is not supported")

        if scored is None:
            after_state = persisted.state
            after_score_min = persisted.score_min
            after_score_max = persisted.score_max
            achieved_gates = range(persisted.mandatory_gate_level)
            floor_valid = persisted.mandatory_floor_valid
        else:
            after_state = scored.primary_state
            after_score_min = scored.score_min
            after_score_max = scored.score_max
            achieved_gates = scored.achieved_gates
            floor_valid = scored.primary_state is not None
        return CanonicalAssessmentResult(
            target_type=target_type,
            target_id=target_id,
            after_state=after_state,
            after_score_min=after_score_min,
            after_score_max=after_score_max,
            after_mandatory_gate_level=len(tuple(achieved_gates)),
            after_mandatory_floor_valid=floor_valid,
            changed_claim_ids=observation.changed_claim_ids,
            changed_dimensions=observation.changed_dimensions,
            explanation=proposed.explanation,
            recompute_receipt_id="pending-reconciliation",
            executable_contract_id=self.executable_contract_id,
            executable_contract_sha256=self.executable_contract_sha256,
        )


class CanonicalMonitoringRecomputer:
    """Persist evaluator observations; accept after-state only from a canonical scorer."""

    def __init__(self, repository, evaluator, canonical_scorer):
        self.repository = repository
        self.evaluator = evaluator
        if evaluator is canonical_scorer:
            raise ValueError(
                "monitoring evaluator and canonical scorer require distinct execution identities"
            )
        if type(canonical_scorer) is not Stage34CanonicalCalculator:
            raise ValueError(
                "canonical monitoring requires the concrete Stage 3/4 canonical calculator"
            )
        if evaluator is canonical_scorer.raw_executor:
            raise ValueError(
                "monitoring evaluator and canonical scorer require distinct execution identities"
            )
        if canonical_scorer.repository is not repository:
            raise ValueError("canonical calculator repository identity mismatch")
        self.canonical_scorer = canonical_scorer

    def refresh(self, *, base_run_id, target_type, target_id, evidence):
        now = datetime.now(timezone.utc)
        request_record = MonitoringEvaluatorRequestRecord(
            request_record_id=_stable_id(
                "monitor_eval_request",
                base_run_id,
                target_type,
                target_id,
                *(item.event_id for item in evidence),
            ),
            run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            evidence_event_ids=tuple(item.event_id for item in evidence),
            created_at=now,
        )
        self.repository.save_monitoring_evaluator_request(request_record)
        raw = self.evaluator.recompute(
            base_run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            evidence=evidence,
        )
        if not isinstance(raw, MonitoringEvaluatorRawResponse):
            raise ValueError(
                "canonical monitoring evaluator must return raw provider response"
            )
        if (
            not raw.provider.strip()
            or not raw.provider_trace_id.strip()
            or not 200 <= raw.http_status < 300
            or not raw.raw_body
            or raw.retrieved_at.tzinfo is None
            or raw.retrieved_at.utcoffset() is None
            or raw.cost_usd < 0
        ):
            raise ValueError("canonical monitoring raw response is incomplete")
        if self.repository.monitoring_evaluator_trace_exists(
            raw.provider, raw.provider_trace_id
        ):
            raise ValueError("canonical monitoring provider trace replay")
        raw_hash = sha256(raw.raw_body).hexdigest()
        response_record = MonitoringEvaluatorRawResponseRecord(
            response_record_id=_stable_id(
                "monitor_eval_response",
                request_record.request_record_id,
                raw.provider,
                raw.provider_trace_id,
                raw_hash,
            ),
            request_record_id=request_record.request_record_id,
            run_id=base_run_id,
            provider=raw.provider,
            provider_trace_id=raw.provider_trace_id,
            http_status=raw.http_status,
            raw_body=raw.raw_body,
            raw_response_sha256=raw_hash,
            retrieved_at=raw.retrieved_at,
            cost_usd=raw.cost_usd,
        )
        self.repository.save_monitoring_evaluator_raw_response(response_record)
        stored_response = self.repository.get_monitoring_evaluator_raw_response(
            response_record.response_record_id
        )
        if (
            stored_response.request_record_id != request_record.request_record_id
            or stored_response.run_id != base_run_id
            or stored_response.provider != raw.provider
            or stored_response.provider_trace_id != raw.provider_trace_id
            or stored_response.raw_response_sha256
            != sha256(stored_response.raw_body).hexdigest()
            or stored_response.raw_body != raw.raw_body
        ):
            raise ValueError(
                "canonical monitoring stored raw response reconciliation failed"
            )
        try:
            payload = json.loads(raw.raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("canonical monitoring raw response is invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("canonical monitoring raw response must be an object")
        observation = _parse_monitoring_observation(payload)
        parsed_record = MonitoringEvaluatorParsedResultRecord(
            result_record_id=_stable_id(
                "monitor_eval_result",
                response_record.response_record_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
            request_record_id=request_record.request_record_id,
            response_record_id=response_record.response_record_id,
            run_id=base_run_id,
            target_type=observation.target_type,
            target_id=observation.target_id,
            parsed_payload_json=json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ),
            parsed_at=datetime.now(timezone.utc),
        )
        self.repository.save_monitoring_evaluator_result(parsed_record)
        if (observation.target_type, observation.target_id) != (target_type, target_id):
            raise ValueError("canonical monitoring evaluator changed the target identity")
        persisted = _persisted_assessment_snapshot(
            self.repository,
            base_run_id,
            target_type,
            target_id,
            observation.changed_dimensions,
        )
        head = self.repository.get_assessment_head(
            base_run_id, target_type, target_id
        )
        scoring_payload_json = json.dumps(
            {
                "base_run_id": base_run_id,
                "target_type": target_type,
                "target_id": target_id,
                "observation": asdict(observation),
                "persisted": asdict(persisted),
                "expected_head_revision_id": head.revision_id,
                "governance_bundle_id": self.canonical_scorer.governance_bundle.bundle_id,
                "governance_bundle_sha256": self.canonical_scorer.governance_bundle.sha256,
                "governance_artifact_sha256": self.canonical_scorer.governance_bundle.artifact_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        operation_id = request_record.request_record_id
        scoring_request = CanonicalScoringRequestRecord(
            request_record_id=_stable_id(
                "canonical_scoring_request", operation_id, head.revision_id
            ),
            operation_id=operation_id,
            run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            evaluator_result_record_id=parsed_record.result_record_id,
            expected_head_revision_id=head.revision_id,
            request_payload_json=scoring_payload_json,
            request_payload_sha256=sha256(
                scoring_payload_json.encode("utf-8")
            ).hexdigest(),
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_canonical_scoring_request(scoring_request)
        try:
            scorer_raw = self.canonical_scorer.score(
                base_run_id=base_run_id,
                target_type=target_type,
                target_id=target_id,
                evidence=tuple(evidence),
                observation=observation,
                persisted=persisted,
            )
        except TimeoutError:
            self.repository.mark_canonical_scoring_outcome_unknown(operation_id)
            raise
        if isinstance(scorer_raw, CanonicalAssessmentResult):
            raise ValueError(
                "canonical scoring request/raw/result/reconciliation is required"
            )
        if not isinstance(scorer_raw, CanonicalScoringRawProviderResponse):
            raise ValueError("canonical scorer must return a raw provider response")
        if (
            not scorer_raw.provider.strip()
            or not scorer_raw.provider_trace_id.strip()
            or not 200 <= scorer_raw.http_status < 300
            or not scorer_raw.raw_body
            or scorer_raw.retrieved_at.tzinfo is None
            or scorer_raw.retrieved_at.utcoffset() is None
            or scorer_raw.cost_usd < 0
        ):
            raise ValueError("canonical scoring raw response is incomplete")
        if scorer_raw.provider.strip().casefold() == raw.provider.strip().casefold():
            raise ValueError(
                "monitoring evaluator and canonical scorer require distinct provider role identities"
            )
        scoring_raw_hash = sha256(scorer_raw.raw_body).hexdigest()
        scoring_response = CanonicalScoringRawResponseRecord(
            response_record_id=_stable_id(
                "canonical_scoring_response",
                scoring_request.request_record_id,
                scorer_raw.provider,
                scorer_raw.provider_trace_id,
                scoring_raw_hash,
            ),
            request_record_id=scoring_request.request_record_id,
            operation_id=operation_id,
            run_id=base_run_id,
            provider=scorer_raw.provider,
            provider_trace_id=scorer_raw.provider_trace_id,
            http_status=scorer_raw.http_status,
            raw_body=scorer_raw.raw_body,
            raw_response_sha256=scoring_raw_hash,
            retrieved_at=scorer_raw.retrieved_at,
            cost_usd=scorer_raw.cost_usd,
        )
        self.repository.save_canonical_scoring_raw_response(scoring_response)
        result = _parse_canonical_scoring_result(scorer_raw.raw_body)
        parsed_payload_json = json.dumps(
            asdict(result), sort_keys=True, separators=(",", ":")
        )
        scoring_result = CanonicalScoringParsedResultRecord(
            result_record_id=_stable_id(
                "canonical_scoring_result",
                scoring_response.response_record_id,
                parsed_payload_json,
            ),
            request_record_id=scoring_request.request_record_id,
            response_record_id=scoring_response.response_record_id,
            operation_id=operation_id,
            run_id=base_run_id,
            parsed_payload_json=parsed_payload_json,
            parsed_payload_sha256=sha256(
                parsed_payload_json.encode("utf-8")
            ).hexdigest(),
            parser_version="canonical-monitoring-result-parser-v1",
            parsed_at=datetime.now(timezone.utc),
        )
        self.repository.save_canonical_scoring_result(scoring_result)
        reconciliation = CanonicalScoringReconciliationRecord(
            receipt_id=_stable_id(
                "canonical_scoring_reconciliation",
                scoring_request.request_record_id,
                scoring_response.response_record_id,
                scoring_result.result_record_id,
            ),
            request_record_id=scoring_request.request_record_id,
            response_record_id=scoring_response.response_record_id,
            result_record_id=scoring_result.result_record_id,
            operation_id=operation_id,
            run_id=base_run_id,
            reconciled_at=datetime.now(timezone.utc),
        )
        self.repository.reconcile_canonical_scoring(reconciliation)
        stored = self.repository.load_reconciled_canonical_scoring_result(
            reconciliation.receipt_id
        )
        stored_result = _parse_canonical_scoring_result(stored.raw_body)
        stored_payload_json = json.dumps(
            asdict(stored_result), sort_keys=True, separators=(",", ":")
        )
        if stored_payload_json != stored.parsed_payload_json:
            raise ValueError("stored canonical scoring result does not match stored raw")
        result = replace(
            stored_result, recompute_receipt_id=reconciliation.receipt_id
        )
        if (result.target_type, result.target_id) != (target_type, target_id):
            raise ValueError("canonical monitoring scorer changed the target identity")
        if (
            result.changed_claim_ids != observation.changed_claim_ids
            or result.changed_dimensions != observation.changed_dimensions
        ):
            raise ValueError("canonical scorer result is not bound to evaluator observation")
        if (
            result.executable_contract_id != persisted.executable_contract_id
            or result.executable_contract_sha256
            != persisted.executable_contract_sha256
        ):
            raise ValueError("canonical evaluator executable contract lineage mismatch")
        revision_payload_json = json.dumps(
            asdict(result), sort_keys=True, separators=(",", ":"), default=str
        )
        revision = AssessmentRevisionRecord(
            revision_id=_stable_id(
                "assessment_revision",
                base_run_id,
                target_type,
                target_id,
                head.revision_id,
                reconciliation.receipt_id,
            ),
            run_id=base_run_id,
            target_type=target_type,
            target_id=target_id,
            parent_revision_id=head.revision_id,
            result_reconciliation_id=reconciliation.receipt_id,
            payload_json=revision_payload_json,
            payload_sha256=sha256(revision_payload_json.encode("utf-8")).hexdigest(),
            created_at=datetime.now(timezone.utc),
        )
        self.repository.append_assessment_revision(revision)
        return ObjectRefreshDraft(
            target_type=target_type,
            target_id=target_id,
            before_state=persisted.state,
            after_state=result.after_state,
            changed_claim_ids=result.changed_claim_ids,
            changed_dimensions=result.changed_dimensions,
            change_type="industry_event",
            explanation=result.explanation,
            before_score_min=persisted.score_min,
            after_score_min=result.after_score_min,
            before_score_max=persisted.score_max,
            after_score_max=result.after_score_max,
            before_mandatory_gate_level=persisted.mandatory_gate_level,
            after_mandatory_gate_level=result.after_mandatory_gate_level,
            before_mandatory_floor_valid=persisted.mandatory_floor_valid,
            after_mandatory_floor_valid=result.after_mandatory_floor_valid,
            original_constraint_active=result.original_constraint_active,
            former_constraint_easing=result.former_constraint_easing,
            adjacent_constraint_strengthening=result.adjacent_constraint_strengthening,
            underlying_demand_intact=result.underlying_demand_intact,
            relief_assessment=result.relief_assessment,
            recompute_receipt_id=result.recompute_receipt_id,
            executable_contract_id=result.executable_contract_id,
            executable_contract_sha256=result.executable_contract_sha256,
        )

    def reconcile_outcome_unknown(self, *, operation_id: str):
        return self.repository.reconcile_canonical_scoring_outcome_unknown(
            operation_id
        )


def _parse_canonical_scoring_proposal(raw_body: bytes):
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("canonical scoring raw response is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("canonical scoring raw response must be an object")
    raw_updates = payload.pop("dimension_updates", [])
    if not isinstance(raw_updates, list):
        raise ValueError("canonical dimension proposals must be a list")
    proposed = _parse_canonical_scoring_result(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return proposed, tuple(_parse_dimension_rating(item) for item in raw_updates)


def _parse_dimension_rating(payload):
    required = {
        "dimension",
        "rating_min",
        "rating_max",
        "evidence_state",
        "bound_type",
        "bound_basis",
        "evidence_ids",
        "stale",
    }
    optional = {
        "rationale",
        "missing_material_questions",
        "anchor_conditions",
        "decisive_evidence_ids",
        "anchor_profile",
    }
    if not isinstance(payload, dict) or not required <= set(payload):
        raise ValueError("canonical dimension proposal is incomplete")
    if set(payload) - required - optional:
        raise ValueError("canonical dimension proposal has unsupported fields")
    basis = payload["bound_basis"]
    if not isinstance(basis, dict) or set(basis) != set(BoundBasis.__dataclass_fields__):
        raise ValueError("canonical dimension bound basis is invalid")
    conditions = payload.get("anchor_conditions", [])
    if not isinstance(conditions, list) or any(
        not isinstance(item, dict)
        or set(item) != set(AnchorConditionResult.__dataclass_fields__)
        for item in conditions
    ):
        raise ValueError("canonical dimension anchor conditions are invalid")
    sequence_fields = (
        "evidence_ids",
        "missing_material_questions",
        "decisive_evidence_ids",
    )
    if any(
        not isinstance(payload.get(name, []), list)
        or not all(isinstance(item, str) for item in payload.get(name, []))
        for name in sequence_fields
    ):
        raise ValueError("canonical dimension evidence bindings are invalid")
    try:
        return DimensionRatingDraft(
            dimension=payload["dimension"],
            rating_min=int(payload["rating_min"]),
            rating_max=int(payload["rating_max"]),
            evidence_state=payload["evidence_state"],
            bound_type=payload["bound_type"],
            bound_basis=BoundBasis(
                floor_anchor=basis["floor_anchor"],
                ceiling_anchor=basis["ceiling_anchor"],
                exact_basis=basis["exact_basis"],
                unresolved_higher_anchors=tuple(basis["unresolved_higher_anchors"]),
                excluded_higher_anchors=tuple(basis["excluded_higher_anchors"]),
            ),
            evidence_ids=tuple(payload["evidence_ids"]),
            stale=payload["stale"],
            rationale=payload.get("rationale", ""),
            missing_material_questions=tuple(
                payload.get("missing_material_questions", [])
            ),
            anchor_conditions=tuple(
                AnchorConditionResult(
                    condition_id=item["condition_id"],
                    condition_type=item["condition_type"],
                    evidence_state=item["evidence_state"],
                    evidence_ids=tuple(item["evidence_ids"]),
                    decisive_claim_ids=tuple(item["decisive_claim_ids"]),
                    anchor=item["anchor"],
                    excluded_higher_anchors=tuple(item["excluded_higher_anchors"]),
                )
                for item in conditions
            ),
            decisive_evidence_ids=tuple(payload.get("decisive_evidence_ids", [])),
            anchor_profile=payload.get("anchor_profile"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("canonical dimension proposal is invalid") from error


def _merge_dimension_updates(persisted_dimensions, updates):
    by_name = {item.dimension: item for item in persisted_dimensions}
    if len(by_name) != len(persisted_dimensions):
        raise ValueError("persisted canonical dimensions are duplicated")
    unknown = {item.dimension for item in updates} - set(by_name)
    if unknown:
        raise ValueError("canonical dimension proposal is outside the persisted family")
    by_name.update({item.dimension: item for item in updates})
    return tuple(by_name[item.dimension] for item in persisted_dimensions)


def _dimension_supported(item):
    return item is not None and item.evidence_state == "supported" and bool(
        item.evidence_ids
    )


def _parse_canonical_scoring_result(raw_body: bytes) -> CanonicalAssessmentResult:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("canonical scoring raw response is invalid JSON") from error
    required = {
        "target_type",
        "target_id",
        "after_state",
        "after_score_min",
        "after_score_max",
        "after_mandatory_gate_level",
        "after_mandatory_floor_valid",
        "changed_claim_ids",
        "changed_dimensions",
        "explanation",
        "executable_contract_id",
        "executable_contract_sha256",
    }
    optional = {
        "original_constraint_active",
        "former_constraint_easing",
        "adjacent_constraint_strengthening",
        "underlying_demand_intact",
    }
    if not isinstance(payload, dict) or not required <= set(payload):
        raise ValueError("canonical scoring raw response is incomplete")
    if set(payload) - required - optional:
        raise ValueError("canonical scoring raw response has unsupported fields")
    if (
        not isinstance(payload["target_type"], str)
        or not payload["target_type"].strip()
        or not isinstance(payload["target_id"], str)
        or not payload["target_id"].strip()
        or not isinstance(payload["changed_claim_ids"], list)
        or not isinstance(payload["changed_dimensions"], list)
        or not all(
            isinstance(item, str) and item.strip()
            for item in (*payload["changed_claim_ids"], *payload["changed_dimensions"])
        )
        or not isinstance(payload["explanation"], str)
        or not payload["explanation"].strip()
    ):
        raise ValueError("canonical scoring raw response fields are invalid")
    return CanonicalAssessmentResult(
        target_type=payload["target_type"],
        target_id=payload["target_id"],
        after_state=payload["after_state"],
        after_score_min=float(payload["after_score_min"]),
        after_score_max=float(payload["after_score_max"]),
        after_mandatory_gate_level=int(payload["after_mandatory_gate_level"]),
        after_mandatory_floor_valid=bool(payload["after_mandatory_floor_valid"]),
        changed_claim_ids=tuple(payload["changed_claim_ids"]),
        changed_dimensions=tuple(payload["changed_dimensions"]),
        explanation=payload["explanation"],
        recompute_receipt_id="pending-reconciliation",
        executable_contract_id=payload["executable_contract_id"],
        executable_contract_sha256=payload["executable_contract_sha256"],
        original_constraint_active=bool(
            payload.get("original_constraint_active", False)
        ),
        former_constraint_easing=bool(payload.get("former_constraint_easing", False)),
        adjacent_constraint_strengthening=bool(
            payload.get("adjacent_constraint_strengthening", False)
        ),
        underlying_demand_intact=bool(payload.get("underlying_demand_intact", True)),
    )


def _parse_monitoring_observation(payload) -> MonitoringObservation:
    if not isinstance(payload, dict):
        raise ValueError("canonical monitoring raw response must be an object")
    forbidden = {
        "after_state",
        "after_score_min",
        "after_score_max",
        "after_mandatory_gate_level",
        "after_mandatory_floor_valid",
        "recompute_receipt_id",
        "executable_contract_id",
        "executable_contract_sha256",
        "trend_state",
    }
    if forbidden.intersection(payload):
        raise ValueError("monitoring evaluator raw JSON must not contain after-state fields")
    expected = {
        "target_type",
        "target_id",
        "changed_claim_ids",
        "changed_dimensions",
        "explanation",
    }
    if set(payload) != expected:
        raise ValueError("monitoring evaluator raw JSON is not an observation payload")
    target_type = payload["target_type"]
    target_id = payload["target_id"]
    changed_claim_ids = payload["changed_claim_ids"]
    changed_dimensions = payload["changed_dimensions"]
    explanation = payload["explanation"]
    if (
        not isinstance(target_type, str)
        or not target_type.strip()
        or not isinstance(target_id, str)
        or not target_id.strip()
        or not isinstance(changed_claim_ids, list)
        or not all(isinstance(item, str) and item.strip() for item in changed_claim_ids)
        or not isinstance(changed_dimensions, list)
        or not all(isinstance(item, str) and item.strip() for item in changed_dimensions)
        or not isinstance(explanation, str)
        or not explanation.strip()
    ):
        raise ValueError("monitoring evaluator observation is incomplete")
    if not changed_claim_ids and not changed_dimensions:
        raise ValueError("monitoring evaluator observation must identify changed inputs")
    return MonitoringObservation(
        target_type=target_type,
        target_id=target_id,
        changed_claim_ids=tuple(changed_claim_ids),
        changed_dimensions=tuple(changed_dimensions),
        explanation=explanation,
    )


class HotspotClusterer(Protocol):
    def cluster(self, events: tuple[HotspotEvent, ...]) -> list[HotspotDraft]: ...


class ObjectRefresher(Protocol):
    def refresh(
        self,
        *,
        base_run_id: str,
        target_type: str,
        target_id: str,
        evidence: tuple[MonitoringEvidence, ...],
    ) -> ObjectRefreshDraft: ...


class HotspotMonitoringService:
    def __init__(
        self,
        repository,
        clusterer: HotspotClusterer,
        refresher: ObjectRefresher,
        *,
        canonical_recomputer: ObjectRefresher | None = None,
    ):
        self.repository = repository
        self.clusterer = clusterer
        self.refresher = refresher
        if canonical_recomputer is not None and not isinstance(
            canonical_recomputer, CanonicalMonitoringRecomputer
        ):
            raise ValueError(
                "industry recompute requires CanonicalMonitoringRecomputer"
            )
        self.canonical_recomputer = canonical_recomputer

    def propose_hotspots(self, events) -> tuple[HotspotCandidate, ...]:
        events = tuple(events)
        if not events:
            return ()
        event_ids = {event.event_id for event in events}
        if len(event_ids) != len(events):
            raise ValueError("hotspot events require unique event_id values")
        drafts = tuple(self.clusterer.cluster(events))
        now = datetime.now(timezone.utc)
        candidates = []
        for draft in drafts:
            inputs = _hotspot_inputs(draft)
            if not draft.theme.strip() or not draft.window.strip():
                raise ValueError("hotspot theme and window are required")
            if not draft.trigger_event_ids or not set(draft.trigger_event_ids) <= event_ids:
                raise ValueError("hotspot trigger events must come from the supplied event window")
            if any(not 0 <= value <= 1 for value in inputs.values()):
                raise ValueError("hotspot scoring inputs must be normalized to 0..1")
            hotspot_id = _stable_id(
                "hotspot", draft.theme.casefold(), draft.window, *sorted(draft.trigger_event_ids)
            )
            candidates.append(
                HotspotCandidate(
                    hotspot_id=hotspot_id,
                    theme=draft.theme.strip(),
                    window=draft.window.strip(),
                    priority_score=round(sum(inputs.values()) / len(inputs) * 100, 1),
                    score_purpose="research_priority_only",
                    scoring_inputs=inputs,
                    trigger_event_ids=tuple(draft.trigger_event_ids),
                    status="proposed",
                    decided_by=None,
                    decided_at=None,
                    created_at=now,
                )
            )
        return self.repository.save_hotspot_candidates(tuple(candidates))

    def decide_hotspot(self, hotspot_id: str, *, decision: str, actor: str):
        if decision not in {"accepted", "rejected"}:
            raise ValueError("hotspot decision must be accepted or rejected")
        if not actor.strip():
            raise ValueError("hotspot decision actor is required")
        return self.repository.decide_hotspot(hotspot_id, decision, actor.strip())

    def add_monitoring_trigger(
        self, run_id: str, draft: MonitoringTriggerDraft
    ) -> MonitoringTrigger:
        status = self.repository.get_run(run_id).status
        if status not in {RunStatus.PERSISTENT_RESEARCH_READY, RunStatus.MONITORING_READY}:
            raise ValueError("monitoring trigger requires PERSISTENT_RESEARCH_READY")
        if (draft.target_type, draft.target_id) not in self.repository.list_research_object_ids(
            run_id
        ):
            raise KeyError("monitoring target is not present in the run")
        if not draft.event_types or not all(item.strip() for item in draft.event_types):
            raise ValueError("monitoring event_types are required")
        if not draft.created_by.strip():
            raise ValueError("monitoring created_by is required")
        trigger_id = _stable_id(
            "trigger",
            run_id,
            draft.target_type,
            draft.target_id,
            *sorted(set(draft.event_types)),
        )
        trigger = MonitoringTrigger(
            trigger_id=trigger_id,
            run_id=run_id,
            target_type=draft.target_type,
            target_id=draft.target_id,
            event_types=tuple(dict.fromkeys(draft.event_types)),
            created_by=draft.created_by.strip(),
            created_at=datetime.now(timezone.utc),
        )
        return self.repository.save_monitoring_trigger(trigger)

    def refresh(self, run_id: str, evidence) -> MonitoringRefreshResult:
        status = self.repository.get_run(run_id).status
        if status not in {RunStatus.PERSISTENT_RESEARCH_READY, RunStatus.MONITORING_READY}:
            raise ValueError("monitoring refresh requires PERSISTENT_RESEARCH_READY")
        evidence = tuple(evidence)
        run = self.repository.get_run(run_id)
        triggers = self.repository.list_monitoring_triggers(run_id)
        matched: dict[tuple[str, str], list[MonitoringEvidence]] = {}
        trigger_ids = []
        evidence_ids_in_run = {
            card.evidence_id for card in self.repository.list_evidence_cards(run_id)
        }
        canonical_urls, content_hashes, quote_hashes = _persisted_source_identities(
            self.repository, run_id
        )
        for item in evidence:
            _validate_monitoring_evidence_time(item)
            new_evidence_ids = _validate_new_monitoring_records(item)
            item_cards_by_article = {
                card.article_id: card for card in item.new_evidence_cards
            }
            for snapshot in item.new_source_snapshots:
                canonical_url = canonicalize_source_url(snapshot.canonical_url)
                content_hash = normalized_source_content_sha256(
                    snapshot.original_text
                )
                card = item_cards_by_article.get(snapshot.article_id)
                quote_hash = (
                    normalized_quote_sha256(card.exact_quote) if card else None
                )
                if (
                    canonical_url in canonical_urls
                    or content_hash in content_hashes
                    or quote_hash in quote_hashes
                ):
                    raise ValueError(
                        "monitoring source already exists in the base ledger"
                    )
                canonical_urls.add(canonical_url)
                content_hashes.add(content_hash)
                if quote_hash:
                    quote_hashes.add(quote_hash)
            if not set(item.evidence_ids) <= evidence_ids_in_run | new_evidence_ids:
                raise ValueError("monitoring evidence must reference persisted evidence cards")
            self.repository.save_monitoring_evidence(run_id, item)
            matching = [
                trigger
                for trigger in triggers
                if trigger.target_type == item.target_type
                and trigger.target_id == item.target_id
                and item.event_type in trigger.event_types
            ]
            if not matching:
                continue
            matched.setdefault((item.target_type, item.target_id), []).append(item)
            trigger_ids.extend(trigger.trigger_id for trigger in matching)
        changes = []
        for (target_type, target_id), target_evidence in matched.items():
            proposal = self.refresher.refresh(
                base_run_id=run_id,
                target_type=target_type,
                target_id=target_id,
                evidence=tuple(target_evidence),
            )
            change_type = _derive_change_type(
                target_evidence,
                base_as_of=run.request.as_of_date,
                repository=self.repository,
                run_id=run_id,
            )
            if change_type == "industry_event":
                if self.canonical_recomputer is None:
                    raise ValueError(
                        "industry event requires an independent canonical recomputer"
                    )
                draft = self.canonical_recomputer.refresh(
                    base_run_id=run_id,
                    target_type=target_type,
                    target_id=target_id,
                    evidence=tuple(target_evidence),
                )
                persisted = _persisted_assessment_snapshot(
                    self.repository,
                    run_id,
                    target_type,
                    target_id,
                    draft.changed_dimensions,
                )
                _assert_persisted_before_snapshot(draft, persisted)
                _validate_recompute_lineage(draft, persisted, target_evidence)
            else:
                draft = _derive_knowledge_revision_draft(
                    self.repository,
                    run_id,
                    target_type,
                    target_id,
                    proposal,
                )
            _validate_refresh_draft(draft, target_type, target_id)
            trend_state = _derive_trend(draft, change_type=change_type)
            responsible_events = tuple(item.event_id for item in target_evidence)
            responsible_evidence = tuple(
                dict.fromkeys(
                    evidence_id
                    for item in target_evidence
                    for evidence_id in item.evidence_ids
                )
            )
            change_id = _stable_id(
                "monitor_change", run_id, target_type, target_id, *responsible_events
            )
            changes.append(
                MonitoringChange(
                    change_id=change_id,
                    target_type=target_type,
                    target_id=target_id,
                    before_state=draft.before_state,
                    after_state=draft.after_state,
                    changed_claim_ids=tuple(draft.changed_claim_ids),
                    changed_dimensions=tuple(draft.changed_dimensions),
                    trend_state=trend_state,
                    change_type=change_type,
                    explanation=draft.explanation.strip(),
                    responsible_event_ids=responsible_events,
                    responsible_evidence_ids=responsible_evidence,
                    recompute_receipt_id=(
                        draft.recompute_receipt_id
                        if change_type == "industry_event"
                        else None
                    ),
                    executable_contract_id=(
                        draft.executable_contract_id
                        if change_type == "industry_event"
                        else None
                    ),
                    executable_contract_sha256=(
                        draft.executable_contract_sha256
                        if change_type == "industry_event"
                        else None
                    ),
                )
            )
        now = datetime.now(timezone.utc)
        refresh_id = _stable_id(
            "refresh", run_id, now.isoformat(), *(change.change_id for change in changes)
        )
        result = MonitoringRefreshResult(
            refresh_id=refresh_id,
            run_id=run_id,
            status=RunStatus.MONITORING_READY,
            trigger_ids=tuple(dict.fromkeys(trigger_ids)),
            changes=tuple(changes),
            created_at=now,
        )
        return self.repository.save_monitoring_refresh(result)


def _hotspot_inputs(draft):
    return {
        "volume_acceleration": float(draft.volume_acceleration),
        "source_diversity": float(draft.source_diversity),
        "novelty": float(draft.novelty),
        "company_breadth": float(draft.company_breadth),
        "sector_breadth": float(draft.sector_breadth),
        "primary_source_confirmation": float(draft.primary_source_confirmation),
        "persistence": float(draft.persistence),
    }


def _validate_refresh_draft(draft, target_type, target_id):
    if (draft.target_type, draft.target_id) != (target_type, target_id):
        raise ValueError("refresher may only update the triggered object")
    if not draft.explanation.strip():
        raise ValueError("monitoring change explanation is required")
    if not draft.changed_claim_ids and not draft.changed_dimensions:
        raise ValueError("monitoring change must identify affected claims or dimensions")
    if not all(
        0 <= score <= 100
        for score in (
            draft.before_score_min,
            draft.after_score_min,
            draft.before_score_max,
            draft.after_score_max,
        )
    ):
        raise ValueError("monitoring scores must be within 0..100")
    if draft.before_score_min > draft.before_score_max:
        raise ValueError("before score interval is invalid")
    if draft.after_score_min > draft.after_score_max:
        raise ValueError("after score interval is invalid")
    if min(draft.before_mandatory_gate_level, draft.after_mandatory_gate_level) < 0:
        raise ValueError("mandatory gate levels cannot be negative")
    if draft.relief_assessment is not None:
        allowed = {"unchanged", "relieved", "transferred", "coexisting_constraints"}
        transitions = {
            draft.relief_assessment.base.constraint_transition_state,
            draft.relief_assessment.stress.constraint_transition_state,
        }
        if not transitions <= allowed:
            raise ValueError("Relief assessment contains an unsupported transition")


def _derive_knowledge_revision_draft(
    repository, run_id, target_type, target_id, proposal
):
    """Bind a knowledge-only revision to the persisted canonical assessment.

    The refresher is an observation producer.  It may identify affected claims,
    dimensions, and an explanation, but it is not an authority for assessment
    state, score, gate, relief, or constraint-transition values.
    """
    if not isinstance(proposal, ObjectRefreshDraft):
        raise ValueError("monitoring refresher must return an ObjectRefreshDraft")
    if (proposal.target_type, proposal.target_id) != (target_type, target_id):
        raise ValueError("refresher may only update the triggered object")
    if not proposal.explanation.strip():
        raise ValueError("monitoring change explanation is required")
    if not proposal.changed_claim_ids and not proposal.changed_dimensions:
        raise ValueError("monitoring change must identify affected claims or dimensions")

    persisted = _persisted_assessment_snapshot(
        repository,
        run_id,
        target_type,
        target_id,
        proposal.changed_dimensions,
    )
    return ObjectRefreshDraft(
        target_type=target_type,
        target_id=target_id,
        before_state=persisted.state,
        after_state=persisted.state,
        changed_claim_ids=tuple(proposal.changed_claim_ids),
        changed_dimensions=tuple(proposal.changed_dimensions),
        change_type="knowledge_revision",
        explanation=proposal.explanation.strip(),
        before_score_min=persisted.score_min,
        after_score_min=persisted.score_min,
        before_score_max=persisted.score_max,
        after_score_max=persisted.score_max,
        before_mandatory_gate_level=persisted.mandatory_gate_level,
        after_mandatory_gate_level=persisted.mandatory_gate_level,
        before_mandatory_floor_valid=persisted.mandatory_floor_valid,
        after_mandatory_floor_valid=persisted.mandatory_floor_valid,
        original_constraint_active=False,
        former_constraint_easing=False,
        adjacent_constraint_strengthening=False,
        underlying_demand_intact=False,
        relief_assessment=None,
    )


def _persisted_assessment_snapshot(
    repository, run_id, target_type, target_id, changed_dimensions
):
    stage3 = repository.get_stage3_result(run_id)
    stage4 = repository.get_stage4_result(run_id)
    if target_type == "segment_assessment":
        assessment = next(
            (item for item in stage3.assessments if item.segment_id == target_id),
            None,
        )
        if assessment is None:
            raise ValueError("monitoring target has no persisted Segment snapshot")
    elif target_type == "company_assessment":
        company = next(
            (
                item
                for item in stage4.company_assessments
                if item.assessment_id == target_id
            ),
            None,
        )
        if company is None:
            raise ValueError("monitoring target has no persisted Company snapshot")
        changed = set(changed_dimensions)
        families = tuple(
            family
            for family in (company.defensibility, company.replacement, company.earnings)
            if family is not None
            and changed.intersection(
                dimension.dimension for dimension in family.dimensions
            )
        )
        if len(families) != 1:
            raise ValueError(
                "industry recompute must resolve one persisted company score family"
            )
        assessment = families[0]
    else:
        raise ValueError("industry recompute target type is not canonical")
    return PersistedAssessmentSnapshot(
        state=assessment.primary_state,
        score_min=assessment.score_min,
        score_max=assessment.score_max,
        mandatory_gate_level=len(assessment.achieved_gates),
        mandatory_floor_valid=assessment.primary_state is not None,
        executable_contract_id=stage4.executable_contract_id,
        executable_contract_sha256=stage4.executable_contract_sha256,
    )


def _assert_persisted_before_snapshot(draft, persisted):
    actual = (
        draft.before_state,
        draft.before_score_min,
        draft.before_score_max,
        draft.before_mandatory_gate_level,
        draft.before_mandatory_floor_valid,
    )
    expected = (
        persisted.state,
        persisted.score_min,
        persisted.score_max,
        persisted.mandatory_gate_level,
        persisted.mandatory_floor_valid,
    )
    if actual != expected:
        raise ValueError("industry recompute before values do not match persisted base snapshot")


def _validate_recompute_lineage(draft, persisted, evidence):
    if not draft.recompute_receipt_id or not draft.recompute_receipt_id.strip():
        raise ValueError("industry recompute requires an independent scorer receipt")
    if (
        draft.executable_contract_id != persisted.executable_contract_id
        or draft.executable_contract_sha256
        != persisted.executable_contract_sha256
    ):
        raise ValueError("industry recompute executable contract lineage mismatch")
    new_claim_ids = {
        claim.claim_id for item in evidence for claim in item.new_claims
    }
    if not draft.changed_claim_ids or not set(draft.changed_claim_ids) <= new_claim_ids:
        raise ValueError("industry recompute changed claims are not in the new event ledger")


def _derive_trend(draft: ObjectRefreshDraft, *, change_type: str | None = None) -> str:
    change_type = change_type or draft.change_type
    if change_type == "knowledge_revision":
        return "assessment_revised"
    if draft.before_mandatory_floor_valid and not draft.after_mandatory_floor_valid:
        return "invalidated"
    if draft.relief_assessment is not None:
        transitions = {
            draft.relief_assessment.base.constraint_transition_state,
            draft.relief_assessment.stress.constraint_transition_state,
        }
        if "coexisting_constraints" in transitions:
            return "coexisting_constraints"
        if "transferred" in transitions:
            return "transferred"
    if draft.original_constraint_active and draft.adjacent_constraint_strengthening:
        return "coexisting_constraints"
    if all(
        (
            draft.former_constraint_easing,
            draft.adjacent_constraint_strengthening,
            draft.underlying_demand_intact,
        )
    ):
        return "transferred"
    if (
        draft.after_score_min - draft.before_score_min >= 10
        or draft.after_mandatory_gate_level > draft.before_mandatory_gate_level
    ):
        return "strengthening"
    if (
        draft.before_score_max - draft.after_score_max >= 10
        or draft.after_mandatory_gate_level < draft.before_mandatory_gate_level
    ):
        return "weakening"
    return "unchanged"


def _validate_monitoring_evidence_time(item: MonitoringEvidence) -> None:
    timestamps = (item.event_time, item.published_at, item.assessment_as_of)
    if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
        raise ValueError("monitoring evidence timestamps must be timezone-aware")
    if not item.event_time <= item.published_at <= item.assessment_as_of:
        raise ValueError("monitoring evidence time order is invalid")


def _derive_change_type(
    evidence, *, base_as_of, repository=None, run_id: str | None = None
) -> str:
    return (
        "industry_event"
        if any(
            item.event_time.date() > base_as_of
            and bool(item.new_claims)
            and bool(item.new_evidence_cards)
            and bool(item.new_source_snapshots)
            and bool(item.source_version_ids)
            and len(item.source_version_ids) == len(item.new_source_snapshots)
            and repository is not None
            and run_id is not None
            and all(
                repository.authorizes_independent_industry_event(version_id)
                for version_id in item.source_version_ids
            )
            and all(
                repository.source_version_matches_snapshot(version_id, snapshot)
                for version_id, snapshot in zip(
                    item.source_version_ids, item.new_source_snapshots
                )
            )
            and _monitoring_fact_candidates_verified(
                repository, run_id, item
            )
            and all(
                card.publication_date == item.published_at.date()
                and card.publication_date > base_as_of
                for card in item.new_evidence_cards
            )
            for item in evidence
        )
        else "knowledge_revision"
    )


def _monitoring_fact_candidates_verified(repository, run_id, item) -> bool:
    assertions = tuple(
        assertion
        for card in item.new_evidence_cards
        for assertion in card.business_fact_assertions
    )
    if not assertions:
        return True
    if (
        not item.governance_bundle_sha256
        or len(item.governance_bundle_sha256) != 64
    ):
        return False
    return all(
        any(
            card.evidence_id == assertion.evidence_id
            for card in item.new_evidence_cards
        )
        and repository.business_fact_assertion_is_verified(
            run_id=run_id,
            assertion=assertion,
            governance_bundle_sha256=item.governance_bundle_sha256,
        )
        for assertion in assertions
    )


def _validate_new_monitoring_records(item: MonitoringEvidence) -> set[str]:
    claims = {claim.claim_id: claim for claim in item.new_claims}
    cards = {card.evidence_id: card for card in item.new_evidence_cards}
    snapshots = {
        snapshot.article_id: snapshot for snapshot in item.new_source_snapshots
    }
    if len(claims) != len(item.new_claims) or len(cards) != len(item.new_evidence_cards):
        raise ValueError("monitoring ledger records require unique IDs")
    if not (claims or cards or snapshots):
        return set()
    if not claims or not cards or not snapshots:
        raise ValueError("new monitoring evidence requires Claim, Evidence Card and Source Snapshot")
    for card in cards.values():
        claim = claims.get(card.claim_id)
        snapshot = snapshots.get(card.article_id)
        if claim is None or card.evidence_id not in claim.evidence_ids:
            raise ValueError("monitoring Evidence Card is not bound to its Claim")
        if snapshot is None or (
            snapshot.content_hash != card.content_hash
            or snapshot.canonical_url != card.canonical_url
        ):
            raise ValueError("monitoring Evidence Card is not bound to its Source Snapshot")
        expected_hash = "sha256:" + sha256(
            snapshot.original_text.encode("utf-8")
        ).hexdigest()
        if expected_hash != snapshot.content_hash:
            raise ValueError("monitoring Source Snapshot hash mismatch")
        if snapshot.original_text[card.quote_start:card.quote_end] != card.exact_quote:
            raise ValueError("monitoring exact quote does not match the Source Snapshot")
    return set(cards)


def _persisted_source_identities(repository, run_id):
    snapshots = []
    stage3 = repository.get_stage3_result(run_id)
    snapshots.extend(stage3.source_snapshots)
    try:
        snapshots.extend(repository.get_stage4_result(run_id).company_source_snapshots)
    except KeyError:
        pass
    for item in repository.list_monitoring_evidence(run_id):
        snapshots.extend(item.new_source_snapshots)
    cards = repository.list_evidence_cards(run_id)
    for item in repository.list_monitoring_evidence(run_id):
        cards = (*cards, *item.new_evidence_cards)
    return (
        {canonicalize_source_url(snapshot.canonical_url) for snapshot in snapshots},
        {
            normalized_source_content_sha256(snapshot.original_text)
            for snapshot in snapshots
        },
        {normalized_quote_sha256(card.exact_quote) for card in cards},
    )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"
