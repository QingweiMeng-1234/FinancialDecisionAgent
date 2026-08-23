from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    CounterSearchRawProviderResponse,
    ResearchRequest,
)
from event_collector.theme_chokepoint.live_runtime import (
    DeepSeekProductAnchorProposer,
    DeepSeekThemeFramer,
    TavilyCounterSearchExecutor,
    build_live_runtime_from_env,
)


class _Completions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload))
                )
            ]
        )


class _Client:
    def __init__(self, payloads):
        self.chat = SimpleNamespace(completions=_Completions(payloads))


def _request():
    return ResearchRequest(
        run_id="prod-e2e-1",
        theme="AI data-center power infrastructure",
        trigger="AI compute deployment growth",
        region="global",
        as_of_date=date(2026, 8, 23),
        time_horizon_months=18,
        analysis_goal="identify upstream chokepoints",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=1,
        max_nodes=5,
        max_iterations=1,
        max_sources=5,
        max_time_seconds=300,
        max_cost_usd=3.0,
        max_product_anchors=1,
    )


def test_live_stage1_providers_decode_strict_frame_and_anchor_contracts():
    """SELECT INVARIANT: live Stage 1 providers return strict production contracts."""

    client = _Client(
        [
            {
                "normalized_theme": "AI data-center power infrastructure",
                "scope": "global hyperscale and colocation data centers",
                "exclusions": ["consumer electronics"],
                "demand_hypothesis": "AI compute growth increases critical power demand.",
                "measurable_demand_variables": ["commissioned MW", "UPS shipments"],
                "unresolved_questions": [],
            },
            {
                "anchors": [
                    {
                        "product_name": "data-center UPS systems",
                        "buyer_or_user": "hyperscale and colocation operators",
                        "demand_variable": "UPS capacity commissioned in MW",
                        "theme_link": "AI clusters require resilient conditioned power.",
                        "confidence": 0.86,
                        "supporting_evidence_ids": [],
                        "missing_evidence": ["current supplier lead times"],
                    }
                ]
            },
        ]
    )
    request = _request()

    frame = DeepSeekThemeFramer(client=client, model="deepseek-chat").frame(request)
    anchors = DeepSeekProductAnchorProposer(
        client=client, model="deepseek-chat"
    ).propose(request, frame)

    assert frame.time_horizon_months == request.time_horizon_months
    assert frame.measurable_demand_variables == (
        "commissioned MW",
        "UPS shipments",
    )
    assert len(anchors) == 1
    assert anchors[0].product_name == "data-center UPS systems"
    assert anchors[0].confidence == 0.86
    assert all(
        call["response_format"] == {"type": "json_object"}
        for call in client.chat.completions.calls
    )
    assert "JSON" in client.chat.completions.calls[1]["messages"][0]["content"]


def test_live_stage1_repair_names_only_the_schema_path_and_error_type():
    """SELECT INVARIANT: repair is actionable without echoing rejected values."""

    invalid_marker = "VALUE_MUST_NOT_BE_ECHOED"
    valid = {
        "normalized_theme": "AI power",
        "scope": "global data centers",
        "exclusions": ["consumer devices"],
        "demand_hypothesis": "AI compute increases resilient power demand.",
        "measurable_demand_variables": ["commissioned MW"],
        "unresolved_questions": [],
    }
    client = _Client([{**valid, "exclusions": invalid_marker}, valid])

    frame = DeepSeekThemeFramer(client=client, model="deepseek-chat").frame(
        _request()
    )

    repair = client.chat.completions.calls[1]["messages"][-1]["content"]
    assert frame.exclusions == ("consumer devices",)
    assert "exclusions:list_type" in repair
    assert invalid_marker not in repair


def test_tavily_counter_search_executes_route_without_promoting_snippets_to_evidence():
    """SELECT INVARIANT: search discovery is executed but is never original evidence."""

    class Response:
        status_code = 200
        content = b'{"results":[{"url":"https://example.com/source","content":"snippet"}]}'
        headers = {"X-Request-Id": "tavily-live-1", "X-Cost-Usd": "0.004"}

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(self.content)

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    executor = TavilyCounterSearchExecutor(
        api_key="test-tavily-key",
        session=session,
        max_results=2,
    )

    raw = executor.execute(route_id="demand", query="UPS demand slowdown")

    assert isinstance(raw, CounterSearchRawProviderResponse)
    assert raw.provider_trace_id == "tavily-live-1"
    assert raw.cost_usd == 0.004
    payload = json.loads(raw.raw_body)
    assert payload["status"] == "unknown"
    assert payload["coverage_state"] == "unknown"
    assert payload["evidence_ids"] == []
    assert payload["counter_evidence"] == []
    assert "1 discovery result" in payload["finding"]
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer test-tavily-key"


def test_shipping_live_factory_builds_production_runtime_from_isolated_env(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: the shipping factory owns explicit live Stage 1-7 wiring."""

    roles = (
        "company-discovery",
        "company-evidence",
        "company-scoring",
        "company-critic",
        "business-fact-verifier",
        "source-identity-resolver",
    )
    for index, role in enumerate(roles, start=1):
        prefix = "THEME_CHOKEPOINT_" + role.replace("-", "_").upper()
        monkeypatch.setenv(f"{prefix}_ENDPOINT", f"http://127.0.0.1:9911/{role}")
        monkeypatch.setenv(f"{prefix}_TOKEN", f"isolated-token-{index}")
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("FINANCIAL_AGENT_THEME_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("FINANCIAL_AGENT_THEME_SIGNAL_ROOT", str(tmp_path / "signals"))
    monkeypatch.setenv("FINANCIAL_AGENT_THEME_MANIFEST_ROOT", str(tmp_path / "manifests"))
    governance_path = tmp_path / "runtime-governance-bundle-v1.json"
    monkeypatch.setenv(
        "THEME_CHOKEPOINT_GOVERNANCE_BUNDLE_PATH", str(governance_path)
    )

    captured = {}

    def fake_build(config, **providers):
        captured["config"] = config
        captured["providers"] = providers
        return SimpleNamespace(
            repository=object(), stage1=object(), orchestrator=object(), stage5=object()
        )

    monkeypatch.setattr(
        "event_collector.theme_chokepoint.live_runtime.build_production_theme_chokepoint_runtime",
        fake_build,
    )
    runtime_config = SimpleNamespace(theme_db_path=str(tmp_path / "theme.sqlite3"))

    runtime = build_live_runtime_from_env(runtime_config)

    assert runtime.stage1 is not None
    assert Path(captured["config"].db_path) == tmp_path / "theme.sqlite3"
    assert Path(captured["config"].governance_bundle_path) == governance_path
    assert captured["config"].allow_unfrozen_overlay is True
    assert len({item.endpoint for item in (
        captured["config"].company_discovery,
        captured["config"].company_evidence,
        captured["config"].company_scoring,
        captured["config"].company_critic,
    )}) == 4
    assert len({item.bearer_token for item in (
        captured["config"].company_discovery,
        captured["config"].company_evidence,
        captured["config"].company_scoring,
        captured["config"].company_critic,
    )}) == 4
    assert set(captured["providers"]) >= {
        "theme_framer",
        "product_anchor_proposer",
        "evidence_acquirer",
        "segment_scorer",
        "counter_search_executor",
    }


def test_shipping_live_factory_fails_closed_when_a_boundary_is_missing(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: no live runtime is built with an implicit provider boundary."""

    for name in tuple(__import__("os").environ):
        if name.startswith("THEME_CHOKEPOINT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")

    with pytest.raises(Exception, match="company-discovery endpoint is not configured"):
        build_live_runtime_from_env(
            SimpleNamespace(theme_db_path=str(tmp_path / "theme.sqlite3"))
        )
