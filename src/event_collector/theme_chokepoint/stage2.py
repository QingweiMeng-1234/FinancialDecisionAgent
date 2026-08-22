"""Stage 2: bounded, typed and inspectable upstream supply-chain graph."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from hashlib import sha256
import re
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    DependencyDraft,
    DependencyEdge,
    ResearchRequest,
    RunStatus,
    SupplyChainGraph,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


ALLOWED_EVIDENCE_STATUS = {"proposed", "supported"}
ALLOWED_STOP_REASONS = {
    "non_critical",
    "sufficiently_commoditized",
    "duplicated",
    "outside_scope",
    "unsupported",
    "max_depth",
}


class UpstreamDependencyProposer(Protocol):
    def propose_upstream(
        self,
        request: ResearchRequest,
        demand_frame,
        node: SupplyChainNode,
    ) -> list[DependencyDraft]: ...


class SupplyChainGraphService:
    def __init__(
        self,
        repository: ThemeChokepointRepository,
        proposer: UpstreamDependencyProposer,
    ):
        self.repository = repository
        self.proposer = proposer

    def build(self, run_id: str) -> SupplyChainGraph:
        run = self.repository.get_run(run_id)
        if run.status is not RunStatus.READY_FOR_SUPPLY_CHAIN:
            raise ValueError(
                "supply-chain expansion requires READY_FOR_SUPPLY_CHAIN; "
                f"actual={run.status.value}"
            )
        confirmed = tuple(anchor for anchor in run.product_anchors if anchor.status == "confirmed")
        if not confirmed or run.demand_frame is None:
            raise ValueError("confirmed product anchors and demand frame are required")

        nodes_by_id: dict[str, SupplyChainNode] = {}
        node_order: list[str] = []
        alias_index: dict[tuple[str, str], str] = {}
        edges_by_id: dict[str, DependencyEdge] = {}
        edge_order: list[str] = []
        queue: deque[str] = deque()
        truncation_reasons: list[str] = []

        for anchor in confirmed:
            normalized = _normalize_name(anchor.product_name)
            node = SupplyChainNode(
                node_id=_stable_id("node", run_id, "product", normalized),
                normalized_name=normalized,
                node_type="product",
                depth=0,
                status="confirmed",
                description=anchor.theme_link,
                aliases=(),
                product_anchor_id=anchor.anchor_id,
            )
            if node.node_id in nodes_by_id:
                continue
            nodes_by_id[node.node_id] = node
            node_order.append(node.node_id)
            alias_index[(anchor.anchor_id, normalized)] = node.node_id
            queue.append(node.node_id)

        while queue:
            downstream_id = queue.popleft()
            downstream = nodes_by_id[downstream_id]
            if downstream.stop_reason:
                continue
            if downstream.depth >= run.request.max_depth:
                nodes_by_id[downstream_id] = replace(downstream, stop_reason="max_depth")
                continue
            drafts = self.proposer.propose_upstream(run.request, run.demand_frame, downstream)
            for draft in drafts:
                _validate_dependency(draft)
                if not downstream.product_anchor_id:
                    raise ValueError("every supply-chain path requires a product_anchor_id")
                scope_key = downstream.product_anchor_id
                upstream_key = _normalize_name(draft.upstream_name)
                existing_id = alias_index.get((scope_key, upstream_key))
                if existing_id is None:
                    for alias in draft.aliases:
                        existing_id = alias_index.get((scope_key, _normalize_name(alias)))
                        if existing_id:
                            break
                if existing_id is None and len(nodes_by_id) >= run.request.max_nodes:
                    reason = f"max_nodes:{run.request.max_nodes}"
                    if reason not in truncation_reasons:
                        truncation_reasons.append(reason)
                    break

                if existing_id is None:
                    upstream_id = _stable_id(
                        "node", run_id, scope_key, draft.node_type, upstream_key
                    )
                    stop_reason = draft.stop_reason
                    if downstream.depth + 1 >= run.request.max_depth and stop_reason is None:
                        stop_reason = "max_depth"
                    upstream = SupplyChainNode(
                        node_id=upstream_id,
                        normalized_name=upstream_key,
                        node_type=draft.node_type,
                        depth=downstream.depth + 1,
                        status=draft.evidence_status,
                        description=draft.description.strip(),
                        aliases=tuple(dict.fromkeys(alias.strip() for alias in draft.aliases if alias.strip())),
                        product_anchor_id=scope_key,
                        stop_reason=stop_reason,
                    )
                    nodes_by_id[upstream_id] = upstream
                    node_order.append(upstream_id)
                    alias_index[(scope_key, upstream_key)] = upstream_id
                    for alias in upstream.aliases:
                        alias_index[(scope_key, _normalize_name(alias))] = upstream_id
                    if upstream.stop_reason is None:
                        queue.append(upstream_id)
                else:
                    upstream_id = existing_id
                    existing = nodes_by_id[upstream_id]
                    merged_aliases = tuple(
                        dict.fromkeys((*existing.aliases, draft.upstream_name, *draft.aliases))
                    )
                    merged_status = (
                        "supported"
                        if "supported" in {existing.status, draft.evidence_status}
                        else existing.status
                    )
                    nodes_by_id[upstream_id] = replace(
                        existing,
                        aliases=merged_aliases,
                        status=merged_status,
                    )
                    for alias in merged_aliases:
                        alias_index[(scope_key, _normalize_name(alias))] = upstream_id

                edge_id = _stable_id(
                    "edge", run_id, downstream_id, upstream_id, draft.relation_type
                )
                candidate_edge = DependencyEdge(
                    edge_id=edge_id,
                    downstream_node_id=downstream_id,
                    upstream_node_id=upstream_id,
                    relation_type=draft.relation_type,
                    demand_transmission=draft.demand_transmission.strip(),
                    criticality_hypothesis=draft.criticality_hypothesis.strip(),
                    substitute_hypothesis=draft.substitute_hypothesis.strip(),
                    confidence=float(draft.confidence),
                    supporting_claim_ids=tuple(draft.supporting_claim_ids),
                    verification_questions=tuple(draft.verification_questions),
                    status=draft.evidence_status,
                )
                existing_edge = edges_by_id.get(edge_id)
                if existing_edge is None:
                    edges_by_id[edge_id] = candidate_edge
                    edge_order.append(edge_id)
                elif candidate_edge.status == "supported" and existing_edge.status != "supported":
                    edges_by_id[edge_id] = candidate_edge

        nodes = tuple(nodes_by_id[node_id] for node_id in node_order)
        edges = tuple(edges_by_id[edge_id] for edge_id in edge_order)
        return self.repository.save_supply_chain_graph(
            run_id,
            nodes=nodes,
            edges=edges,
            truncation_reasons=tuple(truncation_reasons),
        )


def _validate_dependency(draft: DependencyDraft) -> None:
    required = {
        "upstream_name": draft.upstream_name,
        "node_type": draft.node_type,
        "description": draft.description,
        "relation_type": draft.relation_type,
        "demand_transmission": draft.demand_transmission,
        "criticality_hypothesis": draft.criticality_hypothesis,
        "substitute_hypothesis": draft.substitute_hypothesis,
    }
    for field_name, value in required.items():
        if not value.strip():
            raise ValueError(f"dependency {field_name} is required")
    if not draft.verification_questions or not all(
        question.strip() for question in draft.verification_questions
    ):
        raise ValueError("dependency verification_questions are required")
    if draft.evidence_status not in ALLOWED_EVIDENCE_STATUS:
        raise ValueError("dependency evidence_status must be proposed or supported")
    if draft.stop_reason is not None and draft.stop_reason not in ALLOWED_STOP_REASONS:
        raise ValueError(f"unsupported stop_reason: {draft.stop_reason}")
    if not 0 <= draft.confidence <= 1:
        raise ValueError("dependency confidence must be between 0 and 1")


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return " ".join(normalized.split())


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{sha256(payload).hexdigest()[:20]}"
