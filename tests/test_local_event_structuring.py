import json
from types import SimpleNamespace

import pytest

from event_collector.event_structuring import ArticleForStructuring, EventStructuringAgent
from event_collector.local_event_structuring import (
    LocalEventConfig, LocalEventStructuringClient, LocalStructuringError,
    FallbackEventStructuringClient, build_event_structuring_client,
)


@pytest.fixture
def article():
    return ArticleForStructuring(7, "Guidance", "", "Acme lowered its annual revenue guidance.", "https://example.com/a")


def event(**changes):
    return dict(event_type="Company", direction="Negative", importance="Medium",
                time_horizon="Short-term", affected_asset="Acme",
                reasoning="The company lowered its guidance.",
                evidence_excerpt="Acme lowered its annual revenue guidance.", **changes)


class Transport:
    def __init__(self, payload=None, error=None, finish_reason="stop"):
        self.calls = []
        self.error = error
        self.payload = payload if payload is not None else {"events": [event()]}
        self.finish_reason = finish_reason
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.payload), refusal=None),
            finish_reason=self.finish_reason)],
            usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40))


def local(transport=None, **config):
    return LocalEventStructuringClient(LocalEventConfig(model="test-local", **config), client=transport or Transport())


def test_local_call_uses_own_model_and_preserves_contract(article, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://remote.invalid")
    transport = Transport()
    client = local(transport)
    result = EventStructuringAgent(client).structure_article(article)
    assert result[0].affected_asset == "Acme"
    assert transport.calls[0]["model"] == "test-local"
    assert transport.calls[0]["temperature"] == 0
    assert client.last_trace["provider"] == "local"
    assert client.last_trace["completion_tokens"] == 40


@pytest.mark.parametrize("bad", [
    {"events": [{**event(), "evidence_excerpt": "Acme beat revenue forecasts."}]},
    {"events": [{**event(), "direction": "Buy"}]},
    {"events": [{**event(), "affected_asset": " "}]},
    {"events": [{**event(), "reasoning": ""}]},
    {"events": [{k: v for k, v in event().items() if k != "affected_asset"}]},
    {"events": [], "comment": "not part of contract"},
])
def test_bad_output_is_rejected(article, bad):
    with pytest.raises(LocalStructuringError):
        local(Transport(bad)).extract_events(article)


def test_length_finish_reason_is_failure_even_with_valid_json(article):
    with pytest.raises(LocalStructuringError, match="truncated"):
        local(Transport(finish_reason="length")).extract_events(article)


def test_local_requests_server_side_schema_constraints(article):
    transport = Transport()
    local(transport).extract_events(article)
    response_format = transport.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "events" in schema["required"]


def test_long_article_is_not_silently_truncated(article):
    transport = Transport()
    with pytest.raises(LocalStructuringError, match="input_budget"):
        local(transport, max_input_chars=10).extract_events(article)
    assert transport.calls == []


def test_valid_empty_result_does_not_trigger_fallback(article):
    calls = []
    client = FallbackEventStructuringClient(local(Transport({"events": []})), lambda: calls.append(1))
    assert client.extract_events(article).events == []
    assert calls == []
    assert not client.last_trace["fallback_used"]


def test_fallback_is_lazy_and_records_actual_source(article):
    calls = []
    class Remote:
        model = "remote-test"
        def extract_events(self, article):
            calls.append(article.article_id)
            return {"events": []}
    client = FallbackEventStructuringClient(local(Transport({"invalid": True})), Remote)
    assert calls == []
    assert client.extract_events(article).events == []
    assert calls == [7]
    assert client.model == "remote-test"
    assert client.last_trace["provider"] == "deepseek"
    assert client.last_trace["fallback_used"]


def test_failed_fallback_does_not_become_empty_success(article):
    def unavailable():
        raise RuntimeError("remote unavailable")
    with pytest.raises(RuntimeError, match="remote unavailable"):
        FallbackEventStructuringClient(local(Transport({"bad": 1})), unavailable).extract_events(article)


def test_config_rejects_nonlocal_endpoint_and_invalid_budget():
    with pytest.raises(ValueError):
        LocalEventConfig(model="m", base_url="https://remote.invalid/v1")
    with pytest.raises(ValueError):
        LocalEventConfig(model="m", timeout_seconds=0)


def test_default_provider_stays_deepseek_and_local_needs_no_remote_key(monkeypatch):
    import event_collector.event_structuring as es
    sentinel = object()
    monkeypatch.setattr(es, "DeepSeekEventStructuringClient", lambda: sentinel)
    monkeypatch.delenv("EVENT_STRUCTURING_PROVIDER", raising=False)
    assert build_event_structuring_client() is sentinel
    monkeypatch.setenv("EVENT_STRUCTURING_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVENT_MODEL", "test-local")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert isinstance(EventStructuringAgent().llm_client, LocalEventStructuringClient)
    monkeypatch.setenv("EVENT_STRUCTURING_PROVIDER", "typo")
    with pytest.raises(ValueError):
        EventStructuringAgent()


def test_runtime_records_model_after_fallback_and_local_prompt(article):
    from event_collector.structuring_runtime import ensure_article_structured
    class Store:
        def get_article_structuring_state(self, article_id):
            return "pending", None, None
        def get_article_record(self, article_id):
            return SimpleNamespace(id=article_id, article=article)
        def save_structured_events(self, *args, **kwargs):
            pass
    class Remote:
        model = "actual-remote"
        def extract_events(self, article):
            return {"events": []}
    fallback = FallbackEventStructuringClient(local(Transport({"bad": 1})), Remote)
    outcome = ensure_article_structured(7, Store(), EventStructuringAgent(fallback))
    assert outcome.structuring_model == "actual-remote"
    local_outcome = ensure_article_structured(7, Store(), EventStructuringAgent(local()))
    assert local_outcome.structuring_prompt_version == "event-structuring-local-v2"


def test_batch_cli_reports_selected_local_provider(monkeypatch, capsys):
    import structure_events as cli
    monkeypatch.setenv("EVENT_STRUCTURING_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EVENT_MODEL", "test-local")
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "parse_args", lambda: SimpleNamespace(db_path="unused", limit=1, source=None, force_structure=False))
    monkeypatch.setattr(cli, "SQLiteNewsStore", lambda **kw: SimpleNamespace(init_db=lambda: None, close=lambda: None))
    monkeypatch.setattr(cli, "run_structuring", lambda *a, **kw: dict(processed=0, events_created=0, skipped=0, failures=0))
    cli.main()
    output = capsys.readouterr().out
    assert "Provider: local" in output
    assert "Model: test-local" in output
