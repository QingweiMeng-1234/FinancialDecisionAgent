"""Stage 1: assisted theme framing and durable product-anchor confirmation."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    ConfirmationReceipt,
    DemandFrame,
    ProductAnchor,
    ProductAnchorDraft,
    ResearchRequest,
    RunStatus,
    Stage1RunSnapshot,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


class ThemeFramer(Protocol):
    def frame(self, research_request: ResearchRequest) -> DemandFrame: ...


class ProductAnchorProposer(Protocol):
    def propose(
        self, research_request: ResearchRequest, demand_frame: DemandFrame
    ) -> list[ProductAnchorDraft]: ...


class AssistedGateClosed(RuntimeError):
    pass


class AssistedThemeFramingService:
    def __init__(
        self,
        repository: ThemeChokepointRepository,
        framer: ThemeFramer,
        proposer: ProductAnchorProposer,
    ):
        self.repository = repository
        self.framer = framer
        self.proposer = proposer

    def start(self, request: ResearchRequest) -> Stage1RunSnapshot:
        _validate_request(request)
        self.repository.create_request(request)
        frame = self.framer.frame(request)
        _validate_frame_contract(frame, request)
        if not _is_clear_frame(frame):
            return self.repository.save_framing(
                request.run_id,
                frame,
                status=RunStatus.NEEDS_CLARIFICATION,
            )

        drafts = self.proposer.propose(request, frame)
        bounded = tuple(
            _materialize_anchor(request.run_id, draft)
            for draft in drafts[: request.max_product_anchors]
        )
        if not bounded:
            unresolved = tuple(frame.unresolved_questions) + (
                "No product anchor could be proposed within the fixed evidence and budget.",
            )
            clarification_frame = DemandFrame(
                normalized_theme=frame.normalized_theme,
                scope=frame.scope,
                exclusions=frame.exclusions,
                demand_hypothesis=frame.demand_hypothesis,
                measurable_demand_variables=frame.measurable_demand_variables,
                time_horizon_months=frame.time_horizon_months,
                unresolved_questions=unresolved,
            )
            return self.repository.save_framing(
                request.run_id,
                clarification_frame,
                status=RunStatus.NEEDS_CLARIFICATION,
            )
        return self.repository.save_framing(
            request.run_id,
            frame,
            status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
            anchors=bounded,
        )

    def confirm_product_anchors(
        self,
        run_id: str,
        *,
        anchor_ids: tuple[str, ...],
        confirmed_by: str,
    ) -> ConfirmationReceipt:
        return self.repository.confirm_product_anchors(
            run_id,
            anchor_ids,
            confirmed_by=confirmed_by,
        )

    def confirmed_anchors_for_supply_chain(self, run_id: str) -> tuple[ProductAnchor, ...]:
        run = self.repository.get_run(run_id)
        if run.status is not RunStatus.READY_FOR_SUPPLY_CHAIN:
            raise AssistedGateClosed(
                f"product anchor confirmation is required before supply-chain expansion: {run.status.value}"
            )
        confirmed = tuple(anchor for anchor in run.product_anchors if anchor.status == "confirmed")
        if not confirmed or not run.confirmed_at or not run.confirmed_by:
            raise AssistedGateClosed("durable product anchor confirmation receipt is missing")
        return confirmed


def _validate_request(request: ResearchRequest) -> None:
    required_text = {
        "run_id": request.run_id,
        "theme": request.theme,
        "trigger": request.trigger,
        "region": request.region,
        "analysis_goal": request.analysis_goal,
    }
    for field_name, value in required_text.items():
        if not value.strip():
            raise ValueError(f"{field_name} is required")
    if request.research_mode != "assisted":
        raise ValueError("research_mode must be assisted for Stage 1")
    positive_budgets = {
        "time_horizon_months": request.time_horizon_months,
        "max_depth": request.max_depth,
        "max_nodes": request.max_nodes,
        "max_iterations": request.max_iterations,
        "max_sources": request.max_sources,
        "max_time_seconds": request.max_time_seconds,
        "max_product_anchors": request.max_product_anchors,
    }
    for field_name, value in positive_budgets.items():
        if value <= 0:
            raise ValueError(f"{field_name} must be greater than zero")
    if request.max_cost_usd < 0:
        raise ValueError("max_cost_usd must not be negative")


def _validate_frame_contract(frame: DemandFrame, request: ResearchRequest) -> None:
    if frame.time_horizon_months != request.time_horizon_months:
        raise ValueError("demand frame time horizon must match the stored request")
    if not frame.normalized_theme.strip():
        raise ValueError("normalized_theme is required")


def _is_clear_frame(frame: DemandFrame) -> bool:
    return bool(
        frame.scope.strip()
        and frame.demand_hypothesis
        and frame.demand_hypothesis.strip()
        and frame.measurable_demand_variables
    )


def _materialize_anchor(run_id: str, draft: ProductAnchorDraft) -> ProductAnchor:
    _validate_anchor_draft(draft)
    normalized_name = " ".join(draft.product_name.casefold().split())
    digest = sha256(f"{run_id}\0{normalized_name}".encode("utf-8")).hexdigest()[:20]
    return ProductAnchor(
        anchor_id=f"anchor_{digest}",
        product_name=draft.product_name.strip(),
        buyer_or_user=draft.buyer_or_user.strip(),
        demand_variable=draft.demand_variable.strip(),
        theme_link=draft.theme_link.strip(),
        confidence=float(draft.confidence),
        supporting_evidence_ids=tuple(draft.supporting_evidence_ids),
        missing_evidence=tuple(draft.missing_evidence),
        status="proposed",
    )


def _validate_anchor_draft(draft: ProductAnchorDraft) -> None:
    for field_name in ("product_name", "buyer_or_user", "demand_variable", "theme_link"):
        if not getattr(draft, field_name).strip():
            raise ValueError(f"product anchor {field_name} is required")
    if not 0 <= draft.confidence <= 1:
        raise ValueError("product anchor confidence must be between 0 and 1")

