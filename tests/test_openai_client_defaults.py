from types import SimpleNamespace

import pytest

from event_collector.errors import MissingOpenAIKeyError
from event_collector.event_structuring import OpenAIEventStructuringClient
from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.rag_answering import OpenAIRAGAnsweringClient
from event_collector.reranking import OpenAIRAGRerankingClient
from event_collector.summarization import OpenAIArticleSummarizationClient


class FakeMessage:
    def __init__(self, parsed=None, refusal=None):
        self.parsed = parsed
        self.refusal = refusal


class FakeCompletion:
    def __init__(self, parsed=None, refusal=None):
        self.choices = [SimpleNamespace(message=FakeMessage(parsed=parsed, refusal=refusal))]


def install_fake_openai(monkeypatch, responses):
    calls = []
    api_keys = []

    class FakeCompletions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    class FakeOpenAI:
        def __init__(self, api_key):
            api_keys.append(api_key)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    return calls, api_keys


class DummyStructuredClient(OpenAIStructuredOutputClient):
    DEFAULT_MODEL = "dummy-default"
    MODEL_ENV_VAR = "DUMMY_MODEL"
    MISSING_KEY_MESSAGE = "missing key"
    REFUSAL_ERROR_PREFIX = "dummy refusal"
    EMPTY_RESPONSE_MESSAGE = "dummy empty"


def test_base_client_raises_missing_key_error_without_openai_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingOpenAIKeyError) as exc_info:
        OpenAIArticleSummarizationClient()

    assert "article summarization" in str(exc_info.value)


def test_answering_client_prefers_answer_model_env_over_shared_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    monkeypatch.setenv("OPENAI_ANSWER_MODEL", "answer-model")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = OpenAIRAGAnsweringClient()

    assert client.model == "answer-model"


def test_answering_client_falls_back_to_shared_model_then_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    monkeypatch.delenv("OPENAI_ANSWER_MODEL", raising=False)
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = OpenAIRAGAnsweringClient()
    assert client.model == "shared-model"

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    default_client = OpenAIRAGAnsweringClient()
    assert default_client.model == "gpt-5.4"


def test_reranking_client_prefers_rerank_model_env_over_shared_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    monkeypatch.setenv("OPENAI_RERANK_MODEL", "rerank-model")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = OpenAIRAGRerankingClient()

    assert client.model == "rerank-model"


def test_reranking_client_falls_back_to_shared_model_then_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    monkeypatch.delenv("OPENAI_RERANK_MODEL", raising=False)
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = OpenAIRAGRerankingClient()
    assert client.model == "shared-model"

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    default_client = OpenAIRAGRerankingClient()
    assert default_client.model == "gpt-5.4-mini"


def test_structuring_and_summarization_clients_use_shared_model_then_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "shared-model")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    structuring_client = OpenAIEventStructuringClient()
    assert structuring_client.model == "shared-model"

    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    summarization_client = OpenAIArticleSummarizationClient()
    assert summarization_client.model == "shared-model"

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    default_structuring_client = OpenAIEventStructuringClient()
    assert default_structuring_client.model == "gpt-5.4-mini"

    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    default_summarization_client = OpenAIArticleSummarizationClient()
    assert default_summarization_client.model == "gpt-5.4-mini"


def test_base_parse_structured_output_uses_template_flow(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DUMMY_MODEL", "dummy-model")
    calls, api_keys = install_fake_openai(monkeypatch, [FakeCompletion(parsed={"result": 1})])

    client = DummyStructuredClient()
    result = client.parse_structured_output(
        system_prompt="system prompt",
        user_content="user payload",
        response_format=dict,
    )

    assert client.model == "dummy-model"
    assert api_keys == ["test-key"]
    assert result == {"result": 1}
    assert calls == [
        {
            "model": "dummy-model",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user payload"},
            ],
            "response_format": dict,
        }
    ]


def test_base_parse_structured_output_surfaces_refusals(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(refusal="No thanks")])
    client = DummyStructuredClient()

    with pytest.raises(RuntimeError) as exc_info:
        client.parse_structured_output(
            system_prompt="system prompt",
            user_content="user payload",
            response_format=dict,
        )

    assert "dummy refusal: No thanks" in str(exc_info.value)


def test_base_parse_structured_output_rejects_empty_parsed_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed=None)])
    client = DummyStructuredClient()

    with pytest.raises(RuntimeError) as exc_info:
        client.parse_structured_output(
            system_prompt="system prompt",
            user_content="user payload",
            response_format=dict,
        )

    assert "dummy empty" in str(exc_info.value)
