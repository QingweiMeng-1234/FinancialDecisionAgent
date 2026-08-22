from __future__ import annotations

from datetime import date

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    DependencyDraft,
    ProductAnchorDraft,
    ResearchRequest,
    RunStatus,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage1 import AssistedThemeFramingService
from event_collector.theme_chokepoint.stage2 import SupplyChainGraphService


def research_request(run_id="graph-run", **overrides):
    values = dict(
        run_id=run_id,
        theme="AI servers",
        trigger="AI accelerator deployments",
        region="global",
        as_of_date=date(2026, 8, 16),
        time_horizon_months=24,
        analysis_goal="map upstream chokepoints",
        seed_products=("AI server",),
        seed_companies=(),
        research_mode="assisted",
        max_depth=2,
        max_nodes=5,
        max_iterations=3,
        max_sources=40,
        max_time_seconds=900,
        max_cost_usd=10.0,
        max_product_anchors=2,
    )
    values.update(overrides)
    return ResearchRequest(**values)


class Framer:
    def frame(self, request):
        return DemandFrame(
            normalized_theme="ai servers",
            scope="Global AI-server supply chain",
            exclusions=("consumer PCs",),
            demand_hypothesis="AI accelerator deployments increase demand for AI servers.",
            measurable_demand_variables=("AI accelerators deployed",),
            time_horizon_months=request.time_horizon_months,
            unresolved_questions=(),
        )


class AnchorProposer:
    def propose(self, request, demand_frame):
        return [
            ProductAnchorDraft(
                product_name="AI server",
                buyer_or_user="hyperscaler",
                demand_variable="AI accelerators deployed",
                theme_link="Accelerators are installed in AI servers.",
                confidence=0.9,
                supporting_evidence_ids=("ev-root",),
                missing_evidence=(),
            )
        ]


def prepare_run(repository, *, confirm=True, **request_overrides):
    stage1 = AssistedThemeFramingService(repository, Framer(), AnchorProposer())
    result = stage1.start(research_request(**request_overrides))
    if confirm:
        stage1.confirm_product_anchors(
            result.run_id,
            anchor_ids=(result.product_anchors[0].anchor_id,),
            confirmed_by="owner",
        )
    return result


def dependency(name, *, aliases=(), status="proposed", stop_reason=None):
    return DependencyDraft(
        upstream_name=name,
        node_type="component",
        description=f"{name} required by the downstream product.",
        aliases=tuple(aliases),
        relation_type="requires_component",
        demand_transmission=f"More downstream output increases required {name} units.",
        criticality_hypothesis=f"A shortage of {name} may constrain downstream output.",
        substitute_hypothesis=f"Alternative {name} supply remains to be verified.",
        confidence=0.7,
        supporting_claim_ids=("claim-1",) if status == "supported" else (),
        verification_questions=(f"Is qualified {name} supply sufficient?",),
        evidence_status=status,
        stop_reason=stop_reason,
    )


class MappingDependencyProposer:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def propose_upstream(self, request, demand_frame, node):
        self.calls.append((node.normalized_name, node.depth))
        return list(self.mapping.get(node.normalized_name, ()))


def test_stage2_refuses_expansion_before_durable_anchor_confirmation(tmp_path):
    """SELECT INVARIANT: graph expansion cannot bypass the assisted Stage 1 gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    result = prepare_run(repository, confirm=False)
    proposer = MappingDependencyProposer({})

    with pytest.raises(ValueError, match="READY_FOR_SUPPLY_CHAIN"):
        SupplyChainGraphService(repository, proposer).build(result.run_id)

    assert proposer.calls == []


def test_stage2_builds_typed_graph_dedupes_aliases_and_preserves_evidence_status(tmp_path):
    """SELECT INVARIANT: aliases resolve before node budget and proposed/support states stay distinct."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    result = prepare_run(repository)
    proposer = MappingDependencyProposer(
        {
            "ai server": [
                dependency("High Bandwidth Memory", aliases=("HBM",), status="supported"),
                dependency("HBM", aliases=("High-Bandwidth Memory",), status="proposed"),
                dependency("advanced packaging", status="proposed"),
            ],
            "high bandwidth memory": [dependency("DRAM wafer", status="proposed")],
            "advanced packaging": [],
        }
    )

    graph = SupplyChainGraphService(repository, proposer).build(result.run_id)

    assert graph.status is RunStatus.SUPPLY_CHAIN_GRAPH_READY
    assert len(graph.nodes) == 4
    assert [node.normalized_name for node in graph.nodes] == [
        "ai server",
        "high bandwidth memory",
        "advanced packaging",
        "dram wafer",
    ]
    hbm = next(node for node in graph.nodes if node.normalized_name == "high bandwidth memory")
    assert hbm.status == "supported"
    assert "HBM" in hbm.aliases
    assert len([edge for edge in graph.edges if edge.upstream_node_id == hbm.node_id]) == 1
    assert all(edge.demand_transmission for edge in graph.edges)
    assert all(edge.verification_questions for edge in graph.edges)
    assert {edge.status for edge in graph.edges} == {"proposed", "supported"}
    product_anchor_id = graph.nodes[0].product_anchor_id
    assert product_anchor_id
    assert {node.product_anchor_id for node in graph.nodes} == {product_anchor_id}


def test_stage2_enforces_depth_and_node_budgets_with_explicit_truncation(tmp_path):
    """SELECT INVARIANT: budget exhaustion is visible and deeper proposals are not requested."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    result = prepare_run(repository, max_depth=1, max_nodes=2)
    proposer = MappingDependencyProposer(
        {
            "ai server": [dependency("HBM"), dependency("advanced packaging")],
            "hbm": [dependency("DRAM wafer")],
        }
    )

    graph = SupplyChainGraphService(repository, proposer).build(result.run_id)

    assert len(graph.nodes) == 2
    assert graph.truncation_reasons == ("max_nodes:2",)
    assert proposer.calls == [("ai server", 0)]
    assert graph.nodes[1].depth == 1
    assert graph.nodes[1].stop_reason == "max_depth"


def test_stage2_honors_semantic_stop_reason_without_recursive_expansion(tmp_path):
    """SELECT INVARIANT: non-critical/commoditized/out-of-scope/unsupported nodes stop explicitly."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    result = prepare_run(repository)
    proposer = MappingDependencyProposer(
        {
            "ai server": [dependency("standard fastener", stop_reason="sufficiently_commoditized")],
            "standard fastener": [dependency("steel")],
        }
    )

    graph = SupplyChainGraphService(repository, proposer).build(result.run_id)

    fastener = next(node for node in graph.nodes if node.normalized_name == "standard fastener")
    assert fastener.stop_reason == "sufficiently_commoditized"
    assert proposer.calls == [("ai server", 0)]
    assert all(node.normalized_name != "steel" for node in graph.nodes)


def test_stage2_rejects_edges_without_transmission_or_verification_contract(tmp_path):
    """SELECT INVARIANT: an inspectable edge cannot be created from an empty causal story."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    result = prepare_run(repository)
    invalid = dependency("HBM")
    invalid = DependencyDraft(**{**invalid.__dict__, "demand_transmission": ""})
    proposer = MappingDependencyProposer({"ai server": [invalid]})

    with pytest.raises(ValueError, match="demand_transmission"):
        SupplyChainGraphService(repository, proposer).build(result.run_id)

    assert repository.get_run(result.run_id).status is RunStatus.READY_FOR_SUPPLY_CHAIN
