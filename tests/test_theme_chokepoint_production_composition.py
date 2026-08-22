from __future__ import annotations

from datetime import date
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    ProductAnchorDraft,
    ResearchRequest,
    RunStatus,
)
from event_collector.theme_chokepoint.orchestrator import RootStageOrchestrator
from event_collector.theme_chokepoint.production import (
    AuthenticatedEndpointConfig,
    ProductionThemeChokepointConfig,
    build_production_theme_chokepoint_runtime,
)
from event_collector.theme_chokepoint.providers.company_clients import (
    HttpCompanyCriticClient,
    HttpCompanyDiscoveryClient,
    HttpCompanyEvidenceClient,
    HttpCompanyScoringClient,
)
from event_collector.theme_chokepoint.providers.dependency import (
    DeepSeekUpstreamDependencyProposer,
)
from event_collector.theme_chokepoint.providers.company import ProviderChallengerSetBuilder


def test_production_v15_mode_installs_company_builder_and_incomplete_semantics(tmp_path):
    """SELECT INVARIANT: v1.5 production wiring activates both halves of the Stage 4 gate."""
    runtime = build_production_theme_chokepoint_runtime(
        replace(_config(tmp_path), enable_v15_company_chain=True),
        theme_framer=SimpleNamespace(),
        product_anchor_proposer=SimpleNamespace(),
        dependency_proposer=SimpleNamespace(propose_upstream=lambda *_args: ()),
        evidence_acquirer=SimpleNamespace(),
        segment_scorer=SimpleNamespace(),
        counter_search_executor=SimpleNamespace(execute=lambda **_kwargs: None),
        session_factory=_session_factory,
    )

    assert runtime.stage4.enable_v15_company_chain is True
    assert isinstance(
        runtime.stage4.researcher.challenger_set_builder,
        ProviderChallengerSetBuilder,
    )


def test_production_composition_is_exported_from_public_packages():
    """SELECT INVARIANT: callers can reach the production root without private imports."""
    import event_collector.theme_chokepoint as public_runtime
    import event_collector.theme_chokepoint.providers as public_providers

    assert (
        public_runtime.build_production_theme_chokepoint_runtime
        is build_production_theme_chokepoint_runtime
    )
    assert public_runtime.ProductionThemeChokepointConfig is ProductionThemeChokepointConfig
    assert public_runtime.AuthenticatedEndpointConfig is AuthenticatedEndpointConfig
    assert public_providers.HttpCompanyDiscoveryClient is HttpCompanyDiscoveryClient
    assert public_providers.HttpCompanyEvidenceClient is HttpCompanyEvidenceClient
    assert public_providers.HttpCompanyScoringClient is HttpCompanyScoringClient
    assert public_providers.HttpCompanyCriticClient is HttpCompanyCriticClient
    assert (
        public_providers.DeepSeekUpstreamDependencyProposer
        is DeepSeekUpstreamDependencyProposer
    )


def _endpoint(role):
    return AuthenticatedEndpointConfig(
        role=role,
        endpoint=f"https://{role}.provider.example/v1/invoke",
        provider_identity=f"provider:{role}",
        authenticated_boundary_id=f"account-route:{role}",
        bearer_token=f"controlled-{role}-token",
    )


def _config(tmp_path):
    return ProductionThemeChokepointConfig(
        db_path=tmp_path / "theme.db",
        artifact_root=tmp_path / "artifacts",
        signal_export_root=tmp_path / "signals",
        manifest_root=tmp_path / "manifests",
        company_discovery=_endpoint("company-discovery"),
        company_evidence=_endpoint("company-evidence"),
        company_scoring=_endpoint("company-scoring"),
        company_critic=_endpoint("company-critic"),
        fact_verifier=_endpoint("business-fact-verifier"),
        source_resolver=_endpoint("source-identity-resolver"),
        verifier_execution_id="business-fact-verifier-runtime-1",
        assertion_producer_execution_id="company-assertion-producer-runtime-1",
        allow_unfrozen_overlay=True,
    )


def _session_factory():
    return SimpleNamespace(post=lambda *_args, **_kwargs: None)


def _install_fake_deepseek(monkeypatch, payloads):
    import openai

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            payload = payloads.pop(0)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(payload))
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    constructor_calls = []

    def fake_openai(**kwargs):
        constructor_calls.append(kwargs)
        return client

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_API_KEY", "test-only-not-real")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_BASE_URL", "https://deepseek.invalid")
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_MODEL", "deepseek-chat-test")
    return calls, constructor_calls


def test_public_production_root_builds_concrete_dependency_proposer_and_stage1_to7(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: production composition owns clients and every stage service."""
    sessions = []

    def session_factory():
        session = SimpleNamespace(post=lambda *_args, **_kwargs: None)
        sessions.append(session)
        return session

    _, constructor_calls = _install_fake_deepseek(monkeypatch, [])
    config = _config(tmp_path)

    runtime = build_production_theme_chokepoint_runtime(
        config,
        theme_framer=SimpleNamespace(frame=lambda _request: None),
        product_anchor_proposer=SimpleNamespace(propose=lambda *_args: []),
        evidence_acquirer=SimpleNamespace(acquire=lambda **_kwargs: []),
        segment_scorer=SimpleNamespace(assess=lambda *_args, **_kwargs: None),
        counter_search_executor=SimpleNamespace(execute=lambda **_kwargs: None),
        session_factory=session_factory,
    )

    assert isinstance(runtime.orchestrator, RootStageOrchestrator)
    assert isinstance(runtime.company_discovery_client, HttpCompanyDiscoveryClient)
    assert isinstance(runtime.company_evidence_client, HttpCompanyEvidenceClient)
    assert isinstance(runtime.company_scoring_client, HttpCompanyScoringClient)
    assert isinstance(runtime.company_critic_client, HttpCompanyCriticClient)
    assert isinstance(runtime.stage2.proposer, DeepSeekUpstreamDependencyProposer)
    assert constructor_calls == [
        {
            "api_key": "test-only-not-real",
            "timeout": 60.0,
            "base_url": "https://deepseek.invalid",
        }
    ]
    assert len({id(item.session) for item in runtime.company_clients}) == 4
    assert len(sessions) == 6
    assert runtime.orchestrator.stage1 is runtime.stage1
    assert runtime.orchestrator.stage2 is runtime.stage2
    assert runtime.orchestrator.stage3 is runtime.stage3
    assert runtime.orchestrator.stage4 is runtime.stage4
    assert runtime.orchestrator.stage5 is runtime.stage5
    assert runtime.orchestrator.stage6 is runtime.stage6
    assert runtime.orchestrator.stage7 is runtime.stage7


def test_production_root_preserves_explicit_dependency_proposer_injection(tmp_path):
    """SELECT INVARIANT: explicit test injection replaces construction, not provider failure."""
    injected = SimpleNamespace(propose_upstream=lambda *_args: [])

    runtime = build_production_theme_chokepoint_runtime(
        _config(tmp_path),
        theme_framer=SimpleNamespace(frame=lambda _request: None),
        product_anchor_proposer=SimpleNamespace(propose=lambda *_args: []),
        dependency_proposer=injected,
        evidence_acquirer=SimpleNamespace(acquire=lambda **_kwargs: []),
        segment_scorer=SimpleNamespace(assess=lambda *_args, **_kwargs: None),
        counter_search_executor=SimpleNamespace(execute=lambda **_kwargs: None),
        session_factory=_session_factory,
    )

    assert runtime.stage2.proposer is injected


def test_confirmed_anchor_crosses_concrete_proposer_into_persisted_graph(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: production Stage 2 invokes the model only after the durable gate."""
    dependency = {
        "upstream_name": "High Bandwidth Memory",
        "node_type": "component",
        "description": "Memory positioned close to AI accelerators.",
        "aliases": ["HBM"],
        "relation_type": "requires_component",
        "demand_transmission": "More accelerators increase required HBM stacks.",
        "criticality_hypothesis": "Qualified HBM shortages may constrain server output.",
        "substitute_hypothesis": "Alternative memory routes require verification.",
        "confidence": 0.72,
        "verification_questions": ["Is qualified HBM supply sufficient?"],
        "stop_reason": "non_critical",
    }
    model_calls, _ = _install_fake_deepseek(
        monkeypatch, [{"dependencies": [dependency]}]
    )

    class Framer:
        def frame(self, request):
            return DemandFrame(
                normalized_theme="ai servers",
                scope="Global AI-server supply chain",
                exclusions=("consumer PCs",),
                demand_hypothesis="Accelerator deployments increase AI-server demand.",
                measurable_demand_variables=("accelerators deployed",),
                time_horizon_months=request.time_horizon_months,
                unresolved_questions=(),
            )

    class AnchorProposer:
        def propose(self, _request, _demand_frame):
            return [
                ProductAnchorDraft(
                    product_name="AI server",
                    buyer_or_user="hyperscaler",
                    demand_variable="accelerators deployed",
                    theme_link="Accelerators are installed in AI servers.",
                    confidence=0.9,
                    supporting_evidence_ids=("ev-root",),
                    missing_evidence=(),
                )
            ]

    runtime = build_production_theme_chokepoint_runtime(
        _config(tmp_path),
        theme_framer=Framer(),
        product_anchor_proposer=AnchorProposer(),
        evidence_acquirer=SimpleNamespace(acquire=lambda **_kwargs: []),
        segment_scorer=SimpleNamespace(assess=lambda *_args, **_kwargs: None),
        counter_search_executor=SimpleNamespace(execute=lambda **_kwargs: None),
        session_factory=_session_factory,
    )
    request = ResearchRequest(
        run_id="production-dependency-run",
        theme="AI servers",
        trigger="AI accelerator deployments",
        region="global",
        as_of_date=date(2026, 8, 19),
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
    result = runtime.stage1.start(request)

    with pytest.raises(ValueError, match="READY_FOR_SUPPLY_CHAIN"):
        runtime.stage2.build(result.run_id)
    assert model_calls == []

    runtime.stage1.confirm_product_anchors(
        result.run_id,
        anchor_ids=(result.product_anchors[0].anchor_id,),
        confirmed_by="owner",
    )
    graph = runtime.stage2.build(result.run_id)
    persisted = runtime.repository.get_supply_chain_graph(result.run_id)

    assert graph.status is RunStatus.SUPPLY_CHAIN_GRAPH_READY
    assert [node.normalized_name for node in graph.nodes] == [
        "ai server",
        "high bandwidth memory",
    ]
    edge = graph.edges[0]
    assert edge.status == "proposed"
    assert edge.supporting_claim_ids == ()
    assert persisted.nodes == graph.nodes
    assert persisted.edges == graph.edges
    assert len(model_calls) == 1
    assert model_calls[0]["temperature"] == 0


def test_production_concrete_proposer_expands_multiple_layers_in_bfs_order(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: production Stage 2 persists a multi-layer BFS graph."""
    def dependency(
        name,
        *,
        node_type,
        relation_type,
        aliases=(),
    ):
        return {
            "upstream_name": name,
            "node_type": node_type,
            "description": f"{name} is required by the downstream node.",
            "aliases": list(aliases),
            "relation_type": relation_type,
            "demand_transmission": (
                f"More downstream output increases demand for {name}."
            ),
            "criticality_hypothesis": (
                f"Qualified {name} shortages may constrain downstream output."
            ),
            "substitute_hypothesis": (
                f"Alternative {name} routes require original-source verification."
            ),
            "confidence": 0.7,
            "verification_questions": [
                f"Is qualified {name} supply sufficient?"
            ],
            "stop_reason": None,
        }

    model_calls, _ = _install_fake_deepseek(
        monkeypatch,
        [
            {
                "dependencies": [
                    dependency(
                        "HBM",
                        node_type="component",
                        relation_type="requires_component",
                        aliases=("High Bandwidth Memory",),
                    ),
                    dependency(
                        "advanced packaging",
                        node_type="infrastructure",
                        relation_type="requires_process",
                    ),
                ]
            },
            {
                "dependencies": [
                    dependency(
                        "DRAM wafer",
                        node_type="material",
                        relation_type="requires_material",
                    )
                ]
            },
            {
                "dependencies": [
                    dependency(
                        "advanced packaging equipment",
                        node_type="equipment",
                        relation_type="requires_equipment",
                    )
                ]
            },
        ],
    )

    class Framer:
        def frame(self, request):
            return DemandFrame(
                normalized_theme="ai servers",
                scope="Global AI-server supply chain",
                exclusions=("consumer PCs",),
                demand_hypothesis=(
                    "Accelerator deployments increase AI-server demand."
                ),
                measurable_demand_variables=("accelerators deployed",),
                time_horizon_months=request.time_horizon_months,
                unresolved_questions=(),
            )

    class AnchorProposer:
        def propose(self, _request, _demand_frame):
            return [
                ProductAnchorDraft(
                    product_name="AI server",
                    buyer_or_user="hyperscaler",
                    demand_variable="accelerators deployed",
                    theme_link="Accelerators are installed in AI servers.",
                    confidence=0.9,
                    supporting_evidence_ids=("ev-root",),
                    missing_evidence=(),
                )
            ]

    runtime = build_production_theme_chokepoint_runtime(
        _config(tmp_path),
        theme_framer=Framer(),
        product_anchor_proposer=AnchorProposer(),
        evidence_acquirer=SimpleNamespace(acquire=lambda **_kwargs: []),
        segment_scorer=SimpleNamespace(assess=lambda *_args, **_kwargs: None),
        counter_search_executor=SimpleNamespace(execute=lambda **_kwargs: None),
        session_factory=_session_factory,
    )
    request = ResearchRequest(
        run_id="production-multilayer-bfs-run",
        theme="AI servers",
        trigger="AI accelerator deployments",
        region="global",
        as_of_date=date(2026, 8, 19),
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
    stage1_result = runtime.stage1.start(request)
    runtime.stage1.confirm_product_anchors(
        stage1_result.run_id,
        anchor_ids=(stage1_result.product_anchors[0].anchor_id,),
        confirmed_by="owner",
    )

    graph = runtime.stage2.build(stage1_result.run_id)
    persisted = runtime.repository.get_supply_chain_graph(stage1_result.run_id)

    def called_node(call):
        prompt = call["messages"][1]["content"]
        context = json.loads(prompt.split("PROPOSAL_CONTEXT_JSON:\n", 1)[1])
        node = context["downstream_node"]
        return node["normalized_name"], node["depth"]

    assert [called_node(call) for call in model_calls] == [
        ("ai server", 0),
        ("hbm", 1),
        ("advanced packaging", 1),
    ]
    assert [(node.normalized_name, node.depth) for node in graph.nodes] == [
        ("ai server", 0),
        ("hbm", 1),
        ("advanced packaging", 1),
        ("dram wafer", 2),
        ("advanced packaging equipment", 2),
    ]
    name_by_id = {node.node_id: node.normalized_name for node in graph.nodes}
    assert {
        (
            name_by_id[edge.downstream_node_id],
            name_by_id[edge.upstream_node_id],
        )
        for edge in graph.edges
    } == {
        ("ai server", "hbm"),
        ("ai server", "advanced packaging"),
        ("hbm", "dram wafer"),
        ("advanced packaging", "advanced packaging equipment"),
    }
    assert all(node.stop_reason == "max_depth" for node in graph.nodes if node.depth == 2)
    assert all(edge.status == "proposed" for edge in graph.edges)
    assert all(edge.supporting_claim_ids == () for edge in graph.edges)
    assert persisted.nodes == graph.nodes
    assert persisted.edges == graph.edges


def test_production_root_rejects_reused_authenticated_company_boundary(tmp_path):
    """SELECT INVARIANT: distinct wrappers cannot alias one authenticated route."""
    shared = AuthenticatedEndpointConfig(
        role="company-discovery",
        endpoint="https://shared.provider.example/v1/invoke",
        provider_identity="provider:shared",
        authenticated_boundary_id="account-route:shared",
        bearer_token="controlled-shared-token",
    )
    config = ProductionThemeChokepointConfig(
        db_path=tmp_path / "theme.db",
        artifact_root=tmp_path / "artifacts",
        signal_export_root=tmp_path / "signals",
        manifest_root=tmp_path / "manifests",
        company_discovery=shared,
        company_evidence=shared,
        company_scoring=shared,
        company_critic=shared,
        fact_verifier=_endpoint("business-fact-verifier"),
        source_resolver=_endpoint("source-identity-resolver"),
        verifier_execution_id="business-fact-verifier-runtime-1",
        assertion_producer_execution_id="company-assertion-producer-runtime-1",
        allow_unfrozen_overlay=True,
    )

    try:
        build_production_theme_chokepoint_runtime(
            config,
            theme_framer=SimpleNamespace(),
            product_anchor_proposer=SimpleNamespace(),
            dependency_proposer=SimpleNamespace(),
            evidence_acquirer=SimpleNamespace(),
            segment_scorer=SimpleNamespace(),
            counter_search_executor=SimpleNamespace(),
            session_factory=lambda: SimpleNamespace(
                post=lambda *_args, **_kwargs: None
            ),
        )
    except ValueError as error:
        assert "authenticated company boundaries" in str(error)
    else:
        raise AssertionError("shared authenticated company boundary was accepted")


def test_production_root_rejects_one_credential_relabelled_as_four_roles(tmp_path):
    """SELECT INVARIANT: local labels cannot split one credential into four principals."""
    def shared_principal(role):
        return AuthenticatedEndpointConfig(
            role=role,
            endpoint=f"https://shared.provider.example/{role}",
            provider_identity="provider:one-shared-principal",
            authenticated_boundary_id=f"account-route:{role}",
            bearer_token="one-shared-bearer-secret",
        )

    config = ProductionThemeChokepointConfig(
        db_path=tmp_path / "theme.db",
        artifact_root=tmp_path / "artifacts",
        signal_export_root=tmp_path / "signals",
        manifest_root=tmp_path / "manifests",
        company_discovery=shared_principal("company-discovery"),
        company_evidence=shared_principal("company-evidence"),
        company_scoring=shared_principal("company-scoring"),
        company_critic=shared_principal("company-critic"),
        fact_verifier=_endpoint("business-fact-verifier"),
        source_resolver=_endpoint("source-identity-resolver"),
        verifier_execution_id="business-fact-verifier-runtime-1",
        assertion_producer_execution_id="company-assertion-producer-runtime-1",
        allow_unfrozen_overlay=True,
    )

    try:
        build_production_theme_chokepoint_runtime(
            config,
            theme_framer=SimpleNamespace(),
            product_anchor_proposer=SimpleNamespace(),
            dependency_proposer=SimpleNamespace(),
            evidence_acquirer=SimpleNamespace(),
            segment_scorer=SimpleNamespace(),
            counter_search_executor=SimpleNamespace(),
            session_factory=lambda: SimpleNamespace(
                post=lambda *_args, **_kwargs: None
            ),
        )
    except ValueError as error:
        assert "credentials" in str(error)
    else:
        raise AssertionError("one bearer credential was accepted for four roles")


def test_concrete_company_clients_preserve_upstream_raw_trace_and_auth_boundary():
    """SELECT INVARIANT: each role emits raw bytes under its authenticated route."""
    class Response:
        status_code = 200

        def __init__(self, role):
            self.content = (f'{{"role":"{role}"}}').encode("utf-8")
            self.headers = {
                "X-Provider-Trace-Id": f"upstream-trace:{role}",
                "X-Cost-Usd": "0.01",
            }

    class Session:
        def __init__(self, role):
            self.role = role
            self.calls = []

        def post(self, endpoint, **kwargs):
            self.calls.append((endpoint, kwargs))
            return Response(self.role)

    cases = (
        (HttpCompanyDiscoveryClient, "company-discovery", "map_companies"),
        (HttpCompanyEvidenceClient, "company-evidence", "acquire_company_evidence"),
        (HttpCompanyScoringClient, "company-scoring", "score_company"),
        (HttpCompanyCriticClient, "company-critic", "review_company"),
    )
    clients = []
    for client_type, role, method_name in cases:
        session = Session(role)
        client = client_type(_endpoint(role), session=session)
        clients.append(client)
        raw = getattr(client, method_name)(request_record_id=f"request:{role}")

        assert raw.provider == f"provider:{role}#account-route:{role}"
        assert raw.provider_trace_id == f"upstream-trace:{role}"
        assert raw.raw_body == (f'{{"role":"{role}"}}').encode("utf-8")
        assert session.calls[0][1]["headers"]["X-Provider-Role"] == role
        assert session.calls[0][1]["json"]["request_record_id"] == f"request:{role}"

    assert len({id(item.session) for item in clients}) == 4
