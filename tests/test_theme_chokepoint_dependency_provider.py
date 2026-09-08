from __future__ import annotations

from datetime import date
import json
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    ResearchRequest,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.providers.dependency import (
    DeepSeekUpstreamDependencyProposer,
)
import event_collector.theme_chokepoint.providers.dependency as dependency_provider


class FakeCompletions:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _client(*payloads):
    completions = FakeCompletions(*payloads)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions_spy=completions,
    )


def _request():
    return ResearchRequest(
        run_id="dependency-provider-run",
        theme="AI servers",
        trigger="AI accelerator deployments",
        region="global",
        as_of_date=date(2026, 8, 19),
        time_horizon_months=24,
        analysis_goal="map upstream chokepoints",
        seed_products=("AI server",),
        seed_companies=(),
        research_mode="assisted",
        max_depth=3,
        max_nodes=30,
        max_iterations=3,
        max_sources=40,
        max_time_seconds=900,
        max_cost_usd=10.0,
        max_product_anchors=2,
    )


def _demand_frame():
    return DemandFrame(
        normalized_theme="ai servers",
        scope="Global AI-server supply chain",
        exclusions=("consumer PCs",),
        demand_hypothesis="Accelerator deployments increase AI-server demand.",
        measurable_demand_variables=("accelerators deployed",),
        time_horizon_months=24,
        unresolved_questions=("What is the deployment mix?",),
    )


def _node():
    return SupplyChainNode(
        node_id="node-ai-server",
        normalized_name="ai server",
        node_type="product",
        depth=0,
        status="confirmed",
        description="Accelerators are installed in AI servers.",
        aliases=(),
        product_anchor_id="anchor-ai-server",
    )


def _dependency_payload(**overrides):
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
        "stop_reason": None,
    }
    dependency.update(overrides)
    return dependency


def test_valid_structured_response_returns_complete_proposed_dependency_draft():
    """SELECT INVARIANT: model output creates proposals, never persisted evidence support."""
    client = _client({"dependencies": [_dependency_payload()]})
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
        max_candidates_per_node=4,
        timeout_seconds=17,
    )

    drafts = proposer.propose_upstream(_request(), _demand_frame(), _node())

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.upstream_name == "High Bandwidth Memory"
    assert draft.node_type == "component"
    assert draft.description == "Memory positioned close to AI accelerators."
    assert draft.aliases == ("HBM",)
    assert draft.relation_type == "requires_component"
    assert draft.demand_transmission == "More accelerators increase required HBM stacks."
    assert draft.criticality_hypothesis == (
        "Qualified HBM shortages may constrain server output."
    )
    assert draft.substitute_hypothesis == (
        "Alternative memory routes require verification."
    )
    assert draft.confidence == 0.72
    assert draft.verification_questions == ("Is qualified HBM supply sufficient?",)
    assert draft.stop_reason is None
    assert draft.evidence_status == "proposed"
    assert draft.supporting_claim_ids == ()

    call = client.completions_spy.calls[0]
    assert call["model"] == "deepseek-chat-test"
    assert call["temperature"] == 0
    assert call["response_format"] == {"type": "json_object"}
    assert call["timeout"] == 17
    prompt = "\n".join(item["content"] for item in call["messages"])
    assert "maximum 4 dependencies" in prompt
    for required_field in (
        "upstream_name",
        "node_type",
        "description",
        "aliases",
        "relation_type",
        "demand_transmission",
        "criticality_hypothesis",
        "substitute_hypothesis",
        "confidence",
        "verification_questions",
        "stop_reason",
    ):
        assert required_field in prompt


def test_valid_empty_dependencies_is_a_successful_no_proposal_result():
    """SELECT INVARIANT: a schema-valid empty array is distinct from provider failure."""
    client = _client({"dependencies": []})
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
    )

    assert proposer.propose_upstream(_request(), _demand_frame(), _node()) == []
    assert len(client.completions_spy.calls) == 1


def test_model_cannot_forge_supported_status_or_claim_ids():
    """SELECT INVARIANT: model-authored support metadata is rejected, never persisted."""
    forged = _dependency_payload(
        evidence_status="supported",
        supporting_claim_ids=["invented-claim"],
    )
    client = _client({"dependencies": [forged]}, {"dependencies": [forged]})
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
    )

    with pytest.raises(dependency_provider.DependencyProposalError) as captured:
        proposer.propose_upstream(_request(), _demand_frame(), _node())

    assert captured.value.code == "schema_violation"
    assert "after one repair" in str(captured.value)


@pytest.mark.parametrize(
    ("first", "second", "expected_code"),
    [
        ("", "", "empty_response"),
        ("not-json", "still-not-json", "invalid_json"),
        (
            {
                "dependencies": [
                    {
                        key: value
                        for key, value in _dependency_payload().items()
                        if key != "relation_type"
                    }
                ]
            },
            {
                "dependencies": [
                    {
                        key: value
                        for key, value in _dependency_payload().items()
                        if key != "relation_type"
                    }
                ]
            },
            "schema_violation",
        ),
        (
            {"dependencies": [_dependency_payload(confidence=1.01)]},
            {"dependencies": [_dependency_payload(confidence=-0.01)]},
            "schema_violation",
        ),
        (
            {"dependencies": [_dependency_payload(confidence="0.72")]},
            {"dependencies": [_dependency_payload(confidence="0.72")]},
            "schema_violation",
        ),
    ],
    ids=(
        "empty",
        "invalid-json",
        "missing-field",
        "illegal-confidence",
        "coercive-confidence",
    ),
)
def test_invalid_structured_responses_fail_closed_with_stable_error(
    first, second, expected_code
):
    """SELECT INVARIANT: malformed model output never degrades into no dependencies."""
    client = _client(first, second)
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
    )

    with pytest.raises(dependency_provider.DependencyProposalError) as captured:
        proposer.propose_upstream(_request(), _demand_frame(), _node())

    assert captured.value.code == expected_code
    assert str(captured.value) == (
        "dependency proposer failed structured-output contract after one repair: "
        + expected_code
    )
    assert len(client.completions_spy.calls) == 2


def test_first_invalid_response_can_be_repaired_once():
    """SELECT INVARIANT: one structural repair is allowed and its valid result is enforced."""
    client = _client("not-json", {"dependencies": [_dependency_payload()]})
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
    )

    drafts = proposer.propose_upstream(_request(), _demand_frame(), _node())

    assert len(drafts) == 1
    assert drafts[0].evidence_status == "proposed"
    assert drafts[0].supporting_claim_ids == ()
    assert len(client.completions_spy.calls) == 2
    repair_prompt = client.completions_spy.calls[1]["messages"][-1]["content"]
    assert "REPAIR REQUIRED" in repair_prompt
    assert "invalid_json" in repair_prompt


def test_transport_failure_fails_closed_without_becoming_empty_dependencies():
    """SELECT INVARIANT: network failure has a stable provider error and no semantic fallback."""
    client = _client(TimeoutError("socket timed out"))
    proposer = DeepSeekUpstreamDependencyProposer(
        client=client,
        model="deepseek-chat-test",
    )

    with pytest.raises(dependency_provider.DependencyProposalError) as captured:
        proposer.propose_upstream(_request(), _demand_frame(), _node())

    assert captured.value.code == "request_failed"
    assert str(captured.value) == "dependency proposer request failed"
    assert len(client.completions_spy.calls) == 1


def test_candidate_and_input_budgets_are_enforced_per_node():
    """SELECT INVARIANT: one node cannot exceed configured output or prompt budgets."""
    too_many = {"dependencies": [_dependency_payload(), _dependency_payload()]}
    output_client = _client(too_many, too_many)
    output_limited = DeepSeekUpstreamDependencyProposer(
        client=output_client,
        model="deepseek-chat-test",
        max_candidates_per_node=1,
    )

    with pytest.raises(dependency_provider.DependencyProposalError) as captured:
        output_limited.propose_upstream(_request(), _demand_frame(), _node())
    assert captured.value.code == "candidate_budget_exceeded"

    input_client = _client({"dependencies": []})
    input_limited = DeepSeekUpstreamDependencyProposer(
        client=input_client,
        model="deepseek-chat-test",
        max_input_chars=200,
    )
    long_node = SimpleNamespace(**{**_node().__dict__, "description": "x" * 5_000})

    assert input_limited.propose_upstream(
        _request(), _demand_frame(), long_node
    ) == []
    user_content = input_client.completions_spy.calls[0]["messages"][1]["content"]
    assert len(user_content) <= 200
    assert user_content.endswith("[INPUT_TRUNCATED]")
