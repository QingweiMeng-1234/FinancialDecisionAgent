"""Stage 3: original-source evidence, claim ledger and gap-driven scoring loop."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    BoundBasis,
    Claim,
    DimensionResourceUsage,
    DimensionRatingDraft,
    EvidenceCandidate,
    EvidenceAcquisitionBatch,
    EvidenceCard,
    ResearchRequest,
    RunStatus,
    SegmentAssessment,
    SegmentScoreDraft,
    SourceSnapshot,
    Stage3Result,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.governance import RuntimeGovernanceBundle
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.relief import ReliefHorizonService
from event_collector.theme_chokepoint.source_identity import canonicalize_source_url


CONTRACT_VERSION = "theme-chokepoint-scoring-v1.4"
HISTORICAL_FROZEN_EXECUTABLE_SHA256 = (
    "fc409e9cd6663700fce06b5b0605b26cc5fb90e6f72265da2124469a32305fdd"
)
ALLOWED_CLAIM_TYPES = {
    "source_fact",
    "normalized_fact",
    "model_inference",
    "analyst_judgment",
    "open_question",
}
ALLOWED_STANCES = {"supports", "contradicts", "context_only"}
ALLOWED_SCORING_USES = {"primary", "floor_only", "context_only"}


class EvidenceAcquirer(Protocol):
    def acquire(
        self,
        *,
        query: str,
        run,
        node: SupplyChainNode,
        material_field: str,
    ) -> list[EvidenceCandidate]: ...


class SegmentScorer(Protocol):
    def assess(
        self,
        node: SupplyChainNode,
        claims: tuple[Claim, ...],
        evidence_cards: tuple[EvidenceCard, ...],
        *,
        request: ResearchRequest,
        contract_version: str,
    ) -> SegmentScoreDraft: ...


class ReliefScenarioProvider(Protocol):
    def build(
        self,
        *,
        node: SupplyChainNode,
        claims: tuple[Claim, ...],
        evidence_cards: tuple[EvidenceCard, ...],
        request: ResearchRequest,
    ): ...


class FrozenScoringContract:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        expected_sha256: str | None = None,
        allow_unfrozen_overlay: bool = False,
        governance_bundle_path: str | Path | None = None,
        expected_governance_bundle_sha256: str | None = None,
    ):
        governance_bundle = None
        if allow_unfrozen_overlay:
            if expected_governance_bundle_sha256 is None:
                raise ValueError(
                    "unfrozen runtime overlay requires an expected governance bundle SHA-256"
                )
            governance_bundle = RuntimeGovernanceBundle(
                governance_bundle_path,
                expected_sha256=expected_governance_bundle_sha256,
            )
            governed_overlay_path = governance_bundle.artifact_path("runtime_overlay")
            if path is not None and Path(path).resolve() != governed_overlay_path:
                raise ValueError("runtime overlay path is not governed by the bundle")
            path = governed_overlay_path
            governed_overlay_sha256 = governance_bundle.artifact_sha256[
                "runtime_overlay"
            ]
            if (
                expected_sha256 is not None
                and expected_sha256 != governed_overlay_sha256
            ):
                raise ValueError("runtime overlay SHA-256 disagrees with governance bundle")
            expected_sha256 = governed_overlay_sha256
        if path is None:
            path = (
                Path(__file__).resolve().parents[3]
                / "tools"
                / "theme-chokepoint"
                / "semantic-task-contract-v1.4.json"
            )
        contract_path = Path(path)
        raw = contract_path.read_bytes()
        actual_sha256 = sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
        status = payload.get("status")
        if status == "implementation_candidate_unfrozen":
            if not allow_unfrozen_overlay:
                raise ValueError(
                    "unfrozen executable scoring contract requires explicit controlled authorization"
                )
            if (
                payload.get("contract_id")
                != "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
                or payload.get("historical_frozen_machine_contract_available")
                is not False
            ):
                raise ValueError("Stage 3 runtime overlay governance mismatch")
            governance_status = "controlled_unfrozen"
        elif status != "frozen":
            raise ValueError("Stage 3 requires a governed executable scoring contract")
        else:
            governance_status = "frozen"
        if expected_sha256 is None:
            expected_sha256 = HISTORICAL_FROZEN_EXECUTABLE_SHA256
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "executable scoring contract SHA-256 mismatch: "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
        if status == "frozen" and actual_sha256 != HISTORICAL_FROZEN_EXECUTABLE_SHA256:
            raise ValueError("frozen executable scoring contract SHA-256 is not approved")
        if payload.get("inherits_scoring_contract") != CONTRACT_VERSION:
            raise ValueError("Stage 3 scoring contract version mismatch")
        self._score_families = payload.get("score_families", {})
        self._replacement_anchor_profiles = payload.get(
            "replacement_anchor_profiles", {}
        )
        weights = self._score_families.get("segment", {}).get(
            "weighted_dimensions", {}
        )
        if not weights or sum(weights.values()) != 100:
            raise ValueError("frozen segment weights must exist and sum to 100")
        self.version = CONTRACT_VERSION
        self.weights = dict(weights)
        self.executable_contract_id = payload.get("contract_id")
        self.executable_contract_sha256 = actual_sha256
        self.governance_status = governance_status
        self.governance_bundle = governance_bundle
        self.governance_bundle_id = (
            governance_bundle.bundle_id if governance_bundle is not None else None
        )
        self.governance_bundle_sha256 = (
            governance_bundle.sha256 if governance_bundle is not None else None
        )
        self.business_fact_decision_table = (
            json.loads(
                governance_bundle.artifact_path(
                    "business_fact_decision_table"
                ).read_text(encoding="utf-8")
            )
            if governance_bundle is not None
            else None
        )

    def weights_for(self, family: str) -> dict[str, float]:
        weights = self._score_families.get(family, {}).get("weighted_dimensions", {})
        if not weights or sum(weights.values()) != 100:
            raise ValueError(f"frozen {family} weights must exist and sum to 100")
        return dict(weights)

    def replacement_anchor_profile(self, mode: str, dimension: str) -> str:
        profile = self._replacement_anchor_profiles.get(mode, {}).get(dimension)
        if not profile:
            raise ValueError(
                f"replacement anchor profile is not canonical: {mode}/{dimension}"
            )
        return profile


class EvidenceChokepointLoop:
    def __init__(
        self,
        repository: ThemeChokepointRepository,
        acquirer: EvidenceAcquirer,
        scorer: SegmentScorer,
        *,
        contract_path: str | Path | None = None,
        expected_contract_sha256: str | None = None,
        allow_unfrozen_overlay: bool = False,
        governance_bundle_path: str | Path | None = None,
        expected_governance_bundle_sha256: str | None = None,
        relief_provider: ReliefScenarioProvider | None = None,
        critic=None,
        monotonic_clock=monotonic,
    ):
        self.repository = repository
        self.acquirer = acquirer
        self.scorer = scorer
        self.contract = FrozenScoringContract(
            contract_path,
            expected_sha256=expected_contract_sha256,
            allow_unfrozen_overlay=allow_unfrozen_overlay,
            governance_bundle_path=governance_bundle_path,
            expected_governance_bundle_sha256=expected_governance_bundle_sha256,
        )
        self.relief_provider = relief_provider
        self.critic = critic
        self.monotonic_clock = monotonic_clock
        self.relief_service = ReliefHorizonService()

    def run(self, run_id: str) -> Stage3Result:
        run = self.repository.get_run(run_id)
        if run.status is not RunStatus.SUPPLY_CHAIN_GRAPH_READY:
            raise ValueError(
                "Stage 3 requires SUPPLY_CHAIN_GRAPH_READY; "
                f"actual={run.status.value}"
            )
        graph = self.repository.get_supply_chain_graph(run_id)
        nodes = tuple(node for node in graph.nodes if node.depth > 0)
        if not nodes:
            raise ValueError("Stage 3 requires at least one upstream segment node")

        cards_by_id: dict[str, EvidenceCard] = {}
        claims_by_id: dict[str, Claim] = {}
        snapshots_by_article_id: dict[str, SourceSnapshot] = {}
        fact_keys: set[tuple[str, ...]] = set()
        iterations = 0
        cost_usd_spent = 0.0
        provider_request_receipt_ids: list[str] = []
        started_at = self.monotonic_clock()
        elapsed_seconds = 0.0
        budget_stop_reason = None
        retrieval_exhausted_dimensions: tuple[str, ...] = ()
        dimension_query_counts: dict[str, int] = {}
        dimension_provider_request_counts: dict[str, int] = {}
        dimension_costs: dict[str, float] = {}

        def record_scorer_cost() -> None:
            nonlocal cost_usd_spent
            consume_cost = getattr(self.scorer, "consume_cost_usd", None)
            if not callable(consume_cost):
                return
            scorer_cost = float(consume_cost())
            if scorer_cost < 0:
                raise ValueError("provider scoring cost cannot be negative")
            cost_usd_spent += scorer_cost
            if scorer_cost:
                share = scorer_cost / len(self.contract.weights)
                for dimension in self.contract.weights:
                    dimension_costs[dimension] = (
                        dimension_costs.get(dimension, 0.0) + share
                    )

        drafts = self._assess(nodes, claims_by_id, cards_by_id, request=run.request)
        record_scorer_cost()

        while self._missing_pairs(nodes, drafts):
            elapsed_seconds = self.monotonic_clock() - started_at
            if elapsed_seconds >= run.request.max_time_seconds:
                budget_stop_reason = f"max_time_seconds:{run.request.max_time_seconds}"
                break
            if iterations >= run.request.max_iterations:
                break
            pairs = self._missing_pairs(nodes, drafts)
            missing_dimensions = tuple(dict.fromkeys(field for _, field in pairs))
            dimension_sources = {
                dimension: {
                    card.article_id
                    for card in cards_by_id.values()
                    if card.primary_scoring_dimension == dimension
                }
                for dimension in missing_dimensions
            }
            new_candidates: list[EvidenceCandidate] = []
            pending_article_ids: set[str] = set()
            pending_cards_per_article: dict[str, int] = {}
            source_budget_reached = False
            pending_dimension_sources = {
                dimension: set() for dimension in missing_dimensions
            }
            iteration_dimension_sources = {
                dimension: set() for dimension in missing_dimensions
            }
            for node, field in pairs:
                elapsed_seconds = self.monotonic_clock() - started_at
                if elapsed_seconds >= run.request.max_time_seconds:
                    budget_stop_reason = (
                        f"max_time_seconds:{run.request.max_time_seconds}"
                    )
                    break
                query = self._targeted_query(run.request, node, field)
                dimension_query_counts[field] = dimension_query_counts.get(field, 0) + 1
                acquired_result = self.acquirer.acquire(
                    query=query,
                    run=run,
                    node=node,
                    material_field=field,
                )
                if isinstance(acquired_result, EvidenceAcquisitionBatch):
                    if acquired_result.cost_usd < 0:
                        raise ValueError("provider acquisition cost cannot be negative")
                    cost_usd_spent += acquired_result.cost_usd
                    dimension_costs[field] = (
                        dimension_costs.get(field, 0.0) + acquired_result.cost_usd
                    )
                    dimension_provider_request_counts[field] = (
                        dimension_provider_request_counts.get(field, 0)
                        + len(acquired_result.request_receipt_ids)
                    )
                    provider_request_receipt_ids.extend(
                        acquired_result.request_receipt_ids
                    )
                    if cost_usd_spent > run.request.max_cost_usd:
                        budget_stop_reason = f"max_cost_usd:{run.request.max_cost_usd}"
                        break
                    acquired = acquired_result.candidates
                else:
                    acquired = acquired_result
                for candidate in acquired:
                    if candidate.source_mode == "search_summary":
                        continue
                    _validate_candidate(candidate)
                    key = _candidate_fact_key(candidate)
                    if key in fact_keys or any(
                        _candidate_fact_key(item) == key for item in new_candidates
                    ):
                        continue
                    if (
                        len(cards_by_id) + len(new_candidates)
                        >= run.request.max_evidence_cards
                    ):
                        budget_stop_reason = (
                            f"max_evidence_cards:{run.request.max_evidence_cards}"
                        )
                        break
                    dimension = candidate.claim.primary_scoring_dimension or field
                    if dimension not in pending_dimension_sources:
                        pending_dimension_sources[dimension] = set()
                        iteration_dimension_sources[dimension] = set()
                        dimension_sources[dimension] = set()
                    is_new_dimension_source = candidate.article_id not in (
                        dimension_sources[dimension]
                        | pending_dimension_sources[dimension]
                    )
                    if (
                        is_new_dimension_source
                        and len(iteration_dimension_sources[dimension])
                        >= run.request.max_sources_per_dimension_per_iteration
                    ):
                        continue
                    underserved_dimensions = {
                        item
                        for item in missing_dimensions
                        if len(
                            dimension_sources.get(item, set())
                            | pending_dimension_sources.get(item, set())
                        )
                        < run.request.min_sources_per_dimension
                    }
                    candidate_improves_fairness = (
                        dimension in underserved_dimensions
                        and is_new_dimension_source
                    )
                    reserved_card_slots = len(underserved_dimensions) - int(
                        candidate_improves_fairness
                    )
                    remaining_card_slots = (
                        run.request.max_evidence_cards
                        - len(cards_by_id)
                        - len(new_candidates)
                    )
                    if remaining_card_slots <= reserved_card_slots:
                        continue
                    is_known_source = (
                        candidate.article_id in snapshots_by_article_id
                        or candidate.article_id in pending_article_ids
                    )
                    reserved_source_slots = reserved_card_slots
                    remaining_source_slots = (
                        run.request.max_sources
                        - len(snapshots_by_article_id)
                        - len(pending_article_ids)
                    )
                    if (
                        not is_known_source
                        and remaining_source_slots <= reserved_source_slots
                    ):
                        if remaining_source_slots <= 0:
                            source_budget_reached = True
                        continue
                    existing_source_cards = sum(
                        card.article_id == candidate.article_id
                        for card in cards_by_id.values()
                    )
                    if (
                        existing_source_cards
                        + pending_cards_per_article.get(candidate.article_id, 0)
                        >= run.request.max_cards_per_source
                    ):
                        continue
                    new_candidates.append(candidate)
                    pending_article_ids.add(candidate.article_id)
                    if is_new_dimension_source:
                        pending_dimension_sources[dimension].add(candidate.article_id)
                        iteration_dimension_sources[dimension].add(candidate.article_id)
                    pending_cards_per_article[candidate.article_id] = (
                        pending_cards_per_article.get(candidate.article_id, 0) + 1
                    )
                if budget_stop_reason is not None:
                    break
            if budget_stop_reason is not None and not new_candidates:
                break
            if not new_candidates:
                if source_budget_reached:
                    budget_stop_reason = (
                        f"max_unique_sources:{run.request.max_sources}"
                    )
                else:
                    retrieval_exhausted_dimensions = missing_dimensions
                break
            self._materialize(
                new_candidates,
                cards_by_id,
                claims_by_id,
                snapshots_by_article_id,
                fact_keys,
            )
            iterations += 1
            drafts = self._assess(nodes, claims_by_id, cards_by_id, request=run.request)
            record_scorer_cost()
            if cost_usd_spent > run.request.max_cost_usd:
                budget_stop_reason = f"max_cost_usd:{run.request.max_cost_usd}"
            remaining_pairs = self._missing_pairs(nodes, drafts)
            if (
                budget_stop_reason is not None
                and budget_stop_reason.startswith("max_evidence_cards:")
                and not remaining_pairs
            ):
                budget_stop_reason = None
            if source_budget_reached and budget_stop_reason is None and remaining_pairs:
                budget_stop_reason = f"max_unique_sources:{run.request.max_sources}"
            if budget_stop_reason is not None:
                break

        drafts = self._attach_relief(
            nodes,
            drafts,
            claims_by_id,
            cards_by_id,
            request=run.request,
        )
        drafts = self._attach_critic(
            nodes,
            drafts,
            claims_by_id,
            cards_by_id,
            request=run.request,
        )
        _validate_segment_scoring_ownership(
            drafts.values(), claims_by_id, cards_by_id
        )
        assessments = tuple(
            _finalize_assessment(
                drafts[node.node_id],
                self.contract.weights,
                set(cards_by_id),
                self.contract.version,
            )
            for node in nodes
        )
        unresolved = any(
            _has_blocking_unresolved(item.dimensions) for item in assessments
        )
        if budget_stop_reason is not None:
            status = RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
            incomplete_reasons = (budget_stop_reason,)
        elif retrieval_exhausted_dimensions:
            status = RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
            incomplete_reasons = tuple(
                f"retrieval_exhausted:{dimension}"
                for dimension in retrieval_exhausted_dimensions
            )
        elif unresolved:
            status = RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
            if iterations >= run.request.max_iterations:
                incomplete_reasons = (f"max_iterations:{run.request.max_iterations}",)
            elif len(snapshots_by_article_id) >= run.request.max_sources:
                incomplete_reasons = (
                    f"max_unique_sources:{run.request.max_sources}",
                )
            else:
                unresolved_dimensions = tuple(
                    dict.fromkeys(
                        dimension.dimension
                        for assessment in assessments
                        for dimension in assessment.dimensions
                        if dimension.evidence_state in {"unknown", "conflicted"}
                    )
                )
                incomplete_reasons = tuple(
                    f"retrieval_exhausted:{dimension}"
                    for dimension in unresolved_dimensions
                ) or ("retrieval_exhausted:required_evidence_class",)
        else:
            status = RunStatus.CHOKEPOINT_ASSESSMENT_READY
            incomplete_reasons = ()
        dimension_resource_usage = tuple(
            DimensionResourceUsage(
                dimension=dimension,
                query_count=dimension_query_counts.get(dimension, 0),
                unique_source_count=len(
                    {
                        card.article_id
                        for card in cards_by_id.values()
                        if card.primary_scoring_dimension == dimension
                    }
                ),
                evidence_card_count=sum(
                    card.primary_scoring_dimension == dimension
                    for card in cards_by_id.values()
                ),
                provider_request_count=dimension_provider_request_counts.get(
                    dimension, 0
                ),
                cost_usd=round(dimension_costs.get(dimension, 0.0), 6),
            )
            for dimension in self.contract.weights
        )
        return self.repository.save_stage3_result(
            run_id,
            status=status,
            contract_version=self.contract.version,
            executable_contract_id=self.contract.executable_contract_id,
            executable_contract_sha256=self.contract.executable_contract_sha256,
            claims=tuple(claims_by_id.values()),
            evidence_cards=tuple(cards_by_id.values()),
            source_snapshots=tuple(snapshots_by_article_id.values()),
            assessments=assessments,
            iterations_completed=iterations,
            incomplete_reasons=incomplete_reasons,
            provider_request_receipt_ids=tuple(provider_request_receipt_ids),
            cost_usd_spent=round(cost_usd_spent, 6),
            elapsed_seconds=round(elapsed_seconds, 6),
            dimension_resource_usage=dimension_resource_usage,
        )

    def _attach_critic(
        self, nodes, drafts, claims_by_id, cards_by_id, *, request
    ):
        attached = {}
        for node in nodes:
            draft = drafts[node.node_id]
            mandatory_conflict = any(
                item.evidence_state == "conflicted" for item in draft.dimensions
            )
            if self.critic is None:
                attached[node.node_id] = replace(
                    draft,
                    demand_direct_evidence=False,
                    supply_direct_evidence=False,
                    counter_evidence_search_complete=False,
                    mandatory_conflict=mandatory_conflict,
                    independent_supply_constraint_count=0,
                    key_source_quality_high=False,
                )
                continue
            claims = tuple(
                claim for claim in claims_by_id.values() if claim.node_id == node.node_id
            )
            claim_ids = {claim.claim_id for claim in claims}
            cards = tuple(
                card for card in cards_by_id.values() if card.claim_id in claim_ids
            )
            attached[node.node_id] = self.critic.apply(
                node=node,
                ordinal_draft=draft,
                claims=claims,
                evidence_cards=cards,
                request=request,
            )
        return attached

    def _attach_relief(
        self, nodes, drafts, claims_by_id, cards_by_id, *, request
    ):
        if self.relief_provider is None:
            return drafts
        attached = dict(drafts)
        for node in nodes:
            claims = tuple(
                claim for claim in claims_by_id.values() if claim.node_id == node.node_id
            )
            claim_ids = {claim.claim_id for claim in claims}
            cards = tuple(
                card for card in cards_by_id.values() if card.claim_id in claim_ids
            )
            scenarios = self.relief_provider.build(
                node=node,
                claims=claims,
                evidence_cards=cards,
                request=request,
            )
            if scenarios is None:
                continue
            base, stress = scenarios
            assessment = self.relief_service.assess(base=base, stress=stress)
            attached[node.node_id] = replace(
                attached[node.node_id],
                relief_horizon=_relief_horizon_label(assessment),
                relief_assessment=assessment,
            )
        return attached

    def _assess(self, nodes, claims_by_id, cards_by_id, *, request):
        result = {}
        for node in nodes:
            claims = tuple(
                claim for claim in claims_by_id.values() if claim.node_id == node.node_id
            )
            claim_ids = {claim.claim_id for claim in claims}
            cards = tuple(
                card for card in cards_by_id.values() if card.claim_id in claim_ids
            )
            draft = self.scorer.assess(
                node,
                claims,
                cards,
                request=request,
                contract_version=self.contract.version,
            )
            if draft.segment_id != node.node_id:
                raise ValueError("scorer returned assessment for the wrong segment")
            _validate_segment_draft_evidence(
                draft,
                node=node,
                claims=claims,
                evidence_cards=cards,
                request=request,
            )
            result[node.node_id] = draft
        return result

    @staticmethod
    def _missing_pairs(nodes, drafts):
        dimensions = tuple(
            dict.fromkeys(
                field
                for node in nodes
                for field in drafts[node.node_id].missing_material_fields
            )
        )
        return tuple(
            (node, field)
            for node in nodes
            for field in dimensions
            if field in drafts[node.node_id].missing_material_fields
        )

    @staticmethod
    def _targeted_query(
        request: ResearchRequest, node: SupplyChainNode, material_field: str
    ) -> str:
        return (
            f'"{node.normalized_name}" {material_field.replace("_", " ")} '
            f'{request.region} as of {request.as_of_date.isoformat()} original source'
        )

    @staticmethod
    def _materialize(
        candidates, cards_by_id, claims_by_id, snapshots_by_article_id, fact_keys
    ):
        for candidate in candidates:
            canonical_url = canonicalize_source_url(candidate.canonical_url)
            atomic_fact_id = _atomic_fact_id(candidate)
            content_hash = "sha256:" + sha256(
                candidate.original_text.encode("utf-8")
            ).hexdigest()
            snapshot = SourceSnapshot(
                article_id=candidate.article_id,
                canonical_url=canonical_url,
                content_hash=content_hash,
                original_text=candidate.original_text,
            )
            existing_snapshot = snapshots_by_article_id.get(candidate.article_id)
            if existing_snapshot is not None and existing_snapshot != snapshot:
                raise ValueError("article_id collision with different original source content")
            snapshots_by_article_id[candidate.article_id] = snapshot
            evidence_id = _stable_id(
                "evidence",
                candidate.origin_event_id,
                candidate.evidence_family_id,
                candidate.article_id,
                candidate.claim.claim_id,
            )
            card = EvidenceCard(
                evidence_id=evidence_id,
                claim_id=candidate.claim.claim_id,
                article_id=candidate.article_id,
                canonical_url=canonical_url,
                source_title=candidate.source_title.strip(),
                publisher=candidate.publisher.strip(),
                source_type=candidate.source_type.strip(),
                publication_date=candidate.publication_date,
                data_as_of_date=candidate.data_as_of_date,
                location=candidate.location.strip(),
                quote_start=candidate.quote_start,
                quote_end=candidate.quote_end,
                exact_quote=candidate.exact_quote,
                content_hash=content_hash,
                stance=candidate.stance,
                limitations=candidate.limitations.strip(),
                extraction_model=candidate.extraction_model.strip(),
                prompt_version=candidate.prompt_version.strip(),
                origin_event_id=candidate.origin_event_id.strip(),
                evidence_family_id=candidate.evidence_family_id.strip(),
                fact_key=atomic_fact_id,
                assessment_scope=candidate.claim.assessment_scope,
                condition_ids=tuple(candidate.claim.condition_ids),
                claim_capabilities=tuple(candidate.claim.claim_capabilities),
                primary_scoring_dimension=candidate.claim.primary_scoring_dimension,
                scoring_use=candidate.claim.scoring_use,
                source_ambiguity=candidate.claim.source_ambiguity,
                scoring_eligible=candidate.claim.scoring_eligible,
                source_identity_id=candidate.source_identity_id,
                source_version_id=candidate.source_version_id,
                subject_company_ids=tuple(candidate.claim.subject_company_ids),
                derived_from_evidence_id=candidate.claim.derived_from_evidence_id,
            )
            existing = claims_by_id.get(candidate.claim.claim_id)
            if existing is None:
                draft = candidate.claim
                claims_by_id[draft.claim_id] = Claim(
                    claim_id=draft.claim_id,
                    node_id=draft.node_id,
                    claim_type=draft.claim_type,
                    statement=draft.statement.strip(),
                    material_field=draft.material_field.strip(),
                    primary_scoring_dimension=draft.primary_scoring_dimension,
                    scoring_use=draft.scoring_use,
                    evidence_ids=(evidence_id,),
                    fact_key=atomic_fact_id,
                    assessment_scope=draft.assessment_scope,
                    condition_ids=tuple(draft.condition_ids),
                    claim_capabilities=tuple(draft.claim_capabilities),
                    source_ambiguity=draft.source_ambiguity,
                    scoring_eligible=draft.scoring_eligible,
                    subject_company_ids=tuple(draft.subject_company_ids),
                    derived_from_evidence_id=draft.derived_from_evidence_id,
                )
            else:
                if (
                    existing.statement != candidate.claim.statement.strip()
                    or existing.node_id != candidate.claim.node_id
                ):
                    raise ValueError("claim_id collision with different claim content")
                claims_by_id[existing.claim_id] = replace(
                    existing,
                    evidence_ids=tuple(dict.fromkeys((*existing.evidence_ids, evidence_id))),
                )
            cards_by_id[evidence_id] = card
            fact_keys.add(_candidate_fact_key(candidate))


def _validate_candidate(candidate: EvidenceCandidate) -> None:
    if candidate.source_mode != "original_text":
        raise ValueError("scoring evidence must be fetched from original_text")
    required = {
        "article_id": candidate.article_id,
        "canonical_url": candidate.canonical_url,
        "source_title": candidate.source_title,
        "publisher": candidate.publisher,
        "source_type": candidate.source_type,
        "location": candidate.location,
        "exact_quote": candidate.exact_quote,
        "original_text": candidate.original_text,
        "limitations": candidate.limitations,
        "extraction_model": candidate.extraction_model,
        "prompt_version": candidate.prompt_version,
        "origin_event_id": candidate.origin_event_id,
        "evidence_family_id": candidate.evidence_family_id,
        "claim_id": candidate.claim.claim_id,
        "claim_statement": candidate.claim.statement,
        "material_field": candidate.claim.material_field,
    }
    for field, value in required.items():
        if not value.strip():
            raise ValueError(f"evidence {field} is required")
    if candidate.canonical_url.startswith(("http://", "https://")) is False:
        raise ValueError("canonical_url must be HTTP(S)")
    if candidate.stance not in ALLOWED_STANCES:
        raise ValueError("invalid evidence stance")
    if candidate.claim.claim_type not in ALLOWED_CLAIM_TYPES:
        raise ValueError("invalid claim_type")
    if candidate.claim.scoring_use not in ALLOWED_SCORING_USES:
        raise ValueError("invalid claim scoring_use")
    if candidate.claim.scoring_use == "primary" and not candidate.claim.primary_scoring_dimension:
        raise ValueError("primary scoring use requires primary_scoring_dimension")
    if candidate.claim.scoring_use in {"primary", "floor_only"}:
        if not candidate.claim.fact_key.strip():
            raise ValueError("scoring claim fact_key is required")
        if candidate.claim.assessment_scope is None:
            raise ValueError("scoring claim assessment_scope is required")
        if not candidate.claim.condition_ids:
            raise ValueError("scoring claim condition_ids are required")
        if not candidate.claim.scoring_eligible:
            raise ValueError("ineligible claim cannot be used as scoring evidence")
    actual = candidate.original_text[candidate.quote_start : candidate.quote_end]
    if actual != candidate.exact_quote:
        raise ValueError("exact_quote does not match original text offsets")


def _candidate_fact_key(candidate: EvidenceCandidate) -> tuple[str, ...]:
    claim = candidate.claim
    return (
        candidate.origin_event_id.strip(),
        candidate.evidence_family_id.strip(),
        claim.node_id.strip(),
        " ".join(claim.statement.split()).casefold(),
        claim.material_field.strip().casefold(),
        (claim.primary_scoring_dimension or "").strip().casefold(),
        claim.scoring_use.strip().casefold(),
        candidate.stance.strip().casefold(),
    )


def _atomic_fact_id(candidate: EvidenceCandidate) -> str:
    scope = candidate.claim.assessment_scope
    if scope is None:
        scope_parts = ("",) * 7
    else:
        scope_parts = (
            scope.company_id or "",
            scope.product_id,
            scope.segment_id,
            scope.customer_or_platform_scope,
            scope.geography,
            str(scope.time_horizon_months),
            scope.as_of_date.isoformat(),
        )
    digest = sha256(
        "\0".join(
            (
                candidate.origin_event_id.strip(),
                candidate.evidence_family_id.strip(),
                sha256(candidate.exact_quote.encode("utf-8")).hexdigest(),
                *scope_parts,
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"atomic_fact_{digest}"


def _has_blocking_unresolved(dimensions) -> bool:
    return any(
        item.evidence_state in {"unknown", "conflicted"} or item.stale
        for item in dimensions
    )


def _finalize_assessment(draft, weights, evidence_ids, contract_version):
    by_name = {dimension.dimension: dimension for dimension in draft.dimensions}
    if len(by_name) != len(draft.dimensions) or set(by_name) != set(weights):
        raise ValueError("segment assessment dimensions must match frozen v1.4 contract")
    score_min = score_max = 0.0
    presence = resolved = decision = 0.0
    conflicted = unknown = stale = 0.0
    total = float(sum(weights.values()))
    for name, weight in weights.items():
        dimension = by_name[name]
        _validate_dimension(dimension, evidence_ids)
        score_min += weight * dimension.rating_min / 4
        score_max += weight * dimension.rating_max / 4
        if dimension.evidence_state in {"supported", "conflicted"}:
            presence += weight
        if dimension.evidence_state == "supported":
            resolved += weight
            decision += weight * (1 - (dimension.rating_max - dimension.rating_min) / 4)
        if dimension.evidence_state == "conflicted":
            conflicted += weight
        if dimension.evidence_state == "unknown":
            unknown += weight
        if dimension.stale:
            stale += weight
    presence_coverage = presence / total
    resolved_coverage = resolved / total
    decision_coverage = decision / total
    demand = by_name["demand_pressure"]
    criticality = by_name["downstream_criticality"]
    supply_dimensions = tuple(
        by_name[name]
        for name in (
            "effective_supply_concentration",
            "qualification_barrier",
            "capacity_inelasticity",
            "substitute_weakness",
        )
    )
    discovery = (
        demand.evidence_state == "supported"
        and demand.rating_min >= 1
        and criticality.evidence_state == "supported"
        and criticality.rating_min >= 1
        and any(
            item.evidence_state == "supported" and item.rating_min >= 2
            for item in supply_dimensions
        )
        and draft.demand_direct_evidence
        and draft.supply_direct_evidence
    )
    hard_fail = (
        (
            demand.evidence_state == "supported" and demand.rating_max < 1
        )
        or (
            criticality.evidence_state == "supported"
            and criticality.rating_max < 1
        )
        or (decision_coverage >= 0.60 and score_max < 40)
    )
    resolved_upper_bounds_exclude_watch = (
        decision_coverage >= 0.65
        and (
            score_max < 60
            or (
                demand.evidence_state == "supported" and demand.rating_max < 1
            )
            or (
                criticality.evidence_state == "supported"
                and criticality.rating_max < 1
            )
        )
    )
    candidate = (
        by_name["demand_pressure"].rating_min >= 2
        and by_name["downstream_criticality"].rating_min >= 2
        and score_min >= 65
        and decision_coverage >= 0.65
        and draft.demand_direct_evidence
        and draft.supply_direct_evidence
        and draft.counter_evidence_search_complete
        and not draft.mandatory_conflict
    )
    strong = (
        candidate
        and score_min >= 80
        and decision_coverage >= 0.80
        and demand.rating_min >= 3
        and criticality.rating_min >= 3
        and draft.independent_supply_constraint_count >= 2
        and draft.key_source_quality_high
    )
    watch = (
        discovery
        and presence_coverage >= 0.50
        and decision_coverage >= 0.20
        and score_max >= 60
    )
    eligible = discovery or hard_fail or resolved_upper_bounds_exclude_watch
    achieved_gates = []
    primary_state = None
    withheld_reason = None
    if not eligible:
        withheld_reason = "insufficient_evidence"
    elif hard_fail:
        primary_state = "not_supported"
        achieved_gates.append("segment_hard_fail")
    elif strong:
        primary_state = "strong_candidate_chokepoint"
        achieved_gates.extend(("watch_gate", "candidate_gate", "strong_candidate_gate"))
    elif candidate:
        primary_state = "candidate_chokepoint"
        achieved_gates.extend(("watch_gate", "candidate_gate"))
    elif watch:
        primary_state = "watch_segment"
        achieved_gates.append("watch_gate")
    elif resolved_upper_bounds_exclude_watch:
        primary_state = "not_supported"
        achieved_gates.append("resolved_upper_bounds_exclude_watch")
    else:
        raise ValueError("eligible Segment did not match the frozen state priority")
    return SegmentAssessment(
        segment_id=draft.segment_id,
        contract_version=contract_version,
        dimensions=tuple(by_name[name] for name in weights),
        score_min=round(score_min, 1),
        score_max=round(score_max, 1),
        presence_coverage=round(presence_coverage, 4),
        resolved_coverage=round(resolved_coverage, 4),
        decision_coverage=round(decision_coverage, 4),
        conflicted_weight_share=round(conflicted / total, 4),
        unknown_weight_share=round(unknown / total, 4),
        stale_weight_share=round(stale / total, 4),
        primary_state=primary_state,
        missing_material_fields=tuple(draft.missing_material_fields),
        relief_horizon=draft.relief_horizon,
        eligible=eligible,
        achieved_gates=tuple(achieved_gates),
        withheld_reason=withheld_reason,
        relief_assessment=draft.relief_assessment,
        critic_receipt=draft.critic_receipt,
    )


def _validate_segment_scoring_ownership(drafts, claims_by_id, cards_by_id):
    fact_owners: dict[str, str] = {}
    for draft in drafts:
        for dimension in draft.dimensions:
            if dimension.evidence_state != "supported" or dimension.rating_min < 3:
                continue
            decisive_ids = (
                dimension.decisive_evidence_ids or dimension.evidence_ids
            )
            primary_claims = []
            for evidence_id in decisive_ids:
                card = cards_by_id.get(evidence_id)
                claim = claims_by_id.get(card.claim_id) if card is not None else None
                if claim is None:
                    continue
                if claim.scoring_use != "primary":
                    continue
                if claim.primary_scoring_dimension != dimension.dimension:
                    raise ValueError(
                        "one primary high-score dimension is allowed per atomic fact"
                    )
                if (
                    claim.assessment_scope is not None
                    and claim.assessment_scope.segment_id != draft.segment_id
                ):
                    raise ValueError("Segment evidence crosses assessment Scope")
                primary_claims.append(claim)
            if not primary_claims:
                raise ValueError(
                    "one primary high-score dimension is allowed per atomic fact"
                )
            for claim in primary_claims:
                prior = fact_owners.setdefault(claim.fact_key, dimension.dimension)
                if prior != dimension.dimension:
                    raise ValueError(
                        "one primary high-score dimension is allowed per atomic fact"
                    )


def _validate_segment_draft_evidence(
    draft, *, node, claims, evidence_cards, request
):
    """Re-authorize every scorer output against the persisted canonical ledger."""
    claims_by_id = {claim.claim_id: claim for claim in claims}
    cards_by_id = {card.evidence_id: card for card in evidence_cards}
    if len(claims_by_id) != len(claims) or len(cards_by_id) != len(evidence_cards):
        raise ValueError("Segment scorer ledger identifiers must be unique")
    for claim in claims:
        if claim.node_id != node.node_id or not set(claim.evidence_ids) <= set(cards_by_id):
            raise ValueError("Segment scoring claim is outside the persisted ledger")
    for card in evidence_cards:
        claim = claims_by_id.get(card.claim_id)
        if claim is None or card.evidence_id not in claim.evidence_ids:
            raise ValueError("Segment Evidence Card is not bound to its persisted Claim")
        if card.assessment_scope != claim.assessment_scope:
            raise ValueError("Segment Claim and Evidence Card Scope differ")
        scope = card.assessment_scope
        if (
            scope is None
            or scope.segment_id != node.node_id
            or scope.geography != request.region
            or scope.as_of_date != request.as_of_date
            or scope.time_horizon_months != request.time_horizon_months
        ):
            raise ValueError("Segment evidence crosses assessment Scope")
        if any(
            source_date is not None and source_date > request.as_of_date
            for source_date in (card.publication_date, card.data_as_of_date)
        ):
            raise ValueError("future-dated evidence cannot enter an as-of assessment")

    for dimension in draft.dimensions:
        if dimension.evidence_state == "unknown":
            continue
        cited_cards = tuple(cards_by_id.get(item) for item in dimension.evidence_ids)
        if not cited_cards or any(card is None for card in cited_cards):
            raise ValueError("resolved Segment dimension cites missing Evidence Cards")
        cited_claims = tuple(claims_by_id[card.claim_id] for card in cited_cards)
        if any(
            card.primary_scoring_dimension != claim.primary_scoring_dimension
            or claim.material_field != dimension.dimension
            for card, claim in zip(cited_cards, cited_claims)
        ):
            raise ValueError(
                "one primary high-score dimension is allowed per atomic fact"
            )
        if any(
            not card.scoring_eligible
            or not claim.scoring_eligible
            or card.scoring_use == "context_only"
            or claim.scoring_use == "context_only"
            for card, claim in zip(cited_cards, cited_claims)
        ):
            raise ValueError(
                "resolved Segment dimension lacks eligible same-owner scoring evidence"
            )
        stances = {card.stance for card in cited_cards}
        if dimension.evidence_state == "supported" and stances != {"supports"}:
            raise ValueError(
                "supported Segment dimension requires supports stance only"
            )
        if dimension.evidence_state == "conflicted" and not {
            "supports",
            "contradicts",
        } <= stances:
            raise ValueError(
                "conflicted Segment dimension requires supports and contradicts stances"
            )
        decisive_ids = set(dimension.decisive_evidence_ids)
        if not decisive_ids or not decisive_ids <= set(dimension.evidence_ids):
            raise ValueError(
                "resolved Segment dimension requires persisted decisive evidence"
            )
        for condition in dimension.anchor_conditions:
            if (
                not set(condition.evidence_ids) <= set(dimension.evidence_ids)
                or not set(condition.decisive_claim_ids) <= {
                    claims_by_id[cards_by_id[evidence_id].claim_id].claim_id
                    for evidence_id in condition.evidence_ids
                }
            ):
                raise ValueError(
                    "Segment anchor condition is not bound to decisive ledger evidence"
                )


def _relief_horizon_label(assessment) -> str:
    def label(value):
        return value.isoformat() if value is not None else "not_demonstrated"

    return (
        f"base:{label(assessment.base.node_relief_date)};"
        f"stress:{label(assessment.stress.node_relief_date)}"
    )


def _validate_dimension(dimension: DimensionRatingDraft, evidence_ids: set[str]) -> None:
    if not 0 <= dimension.rating_min <= dimension.rating_max <= 4:
        raise ValueError("dimension rating must be a 0..4 interval")
    if dimension.evidence_state not in {"supported", "conflicted", "unknown"}:
        raise ValueError("invalid evidence_state")
    basis: BoundBasis = dimension.bound_basis
    if dimension.evidence_state == "unknown":
        if (dimension.rating_min, dimension.rating_max) != (0, 4):
            raise ValueError("unknown evidence must preserve [0,4]")
        if dimension.bound_type != "none" or basis != BoundBasis():
            raise ValueError("unknown evidence cannot carry a scoring bound")
        if dimension.evidence_ids:
            raise ValueError("unknown dimension cannot cite scoring evidence")
        return
    if not dimension.evidence_ids or not set(dimension.evidence_ids) <= evidence_ids:
        raise ValueError("resolved dimension must cite persisted original evidence")
    if dimension.bound_type == "exact":
        if basis.floor_anchor != dimension.rating_min or basis.ceiling_anchor != dimension.rating_max:
            raise ValueError("exact bound_basis must match the rating")
        if not basis.exact_basis:
            raise ValueError("exact rating requires exact_basis")
        valid_conditions = {
            condition.condition_type
            for condition in dimension.anchor_conditions
            if condition.evidence_state == "supported"
            and condition.evidence_ids
            and set(condition.evidence_ids) <= set(dimension.evidence_ids)
            and condition.decisive_claim_ids
            and condition.anchor == dimension.rating_min
            and (
                condition.condition_type != "ceiling"
                or set(condition.excluded_higher_anchors)
                == set(basis.excluded_higher_anchors)
            )
        }
        required_types = {"floor"}
        if dimension.rating_min < 4:
            required_types.add("ceiling")
        if not required_types <= valid_conditions:
            raise ValueError(
                "exact rating requires decisive floor and ceiling conditions "
                "bound to the rated anchor and exclusions"
            )
        if (
            not dimension.decisive_evidence_ids
            or not set(dimension.decisive_evidence_ids) <= set(dimension.evidence_ids)
        ):
            raise ValueError("exact rating requires decisive evidence IDs")


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{sha256(payload).hexdigest()[:20]}"
