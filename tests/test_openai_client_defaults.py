from types import SimpleNamespace

import pytest

from event_collector.errors import MissingOpenAIKeyError
from event_collector.event_structuring import DeepSeekEventStructuringClient, EventStructuringAgent, OpenAIEventStructuringClient
from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.rag_answering import DeepSeekRAGAnsweringClient, OpenAIRAGAnsweringClient, RAGAnsweringAgent
from event_collector.recommendation import DeepSeekRecommendationClient, RecommendationAgent
from event_collector.reranking import (
    DeepSeekRAGRerankingClient,
    OpenAIRAGRerankingClient,
    RAGRerankingAgent,
    RerankCandidate,
    RerankingRequest,
)
from event_collector.summarization import DeepSeekArticleSummarizationClient, OpenAIArticleSummarizationClient, SummarizationAgent
from event_collector.watchlist_triage import (
    DeepSeekWatchlistReviewerClient,
    DeepSeekWatchlistTriageClient,
    WatchlistReviewerAgent,
    WatchlistTriageAgent,
)


class FakeMessage:
    def __init__(self, parsed=None, refusal=None, content=None):
        self.parsed = parsed
        self.refusal = refusal
        self.content = content


class FakeCompletion:
    def __init__(self, parsed=None, refusal=None, content=None):
        self.choices = [SimpleNamespace(message=FakeMessage(parsed=parsed, refusal=refusal, content=content))]


def install_fake_openai(monkeypatch, responses):
    calls = []
    api_keys = []

    class FakeCompletions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        def create(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    class FakeOpenAI:
        def __init__(self, api_key, base_url=None, timeout=None):
            api_keys.append(api_key)
            calls.append({"client_base_url": base_url, "client_timeout": timeout})
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
    assert default_client.model == "deepseek-v4-pro"


def test_deepseek_answering_client_uses_deepseek_envs_and_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_ANSWER_MODEL", "deepseek-answer-model")
    calls, api_keys = install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = DeepSeekRAGAnsweringClient()

    assert client.model == "deepseek-answer-model"
    assert api_keys == ["deepseek-test-key"]
    assert calls[0] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}


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
    assert default_client.model == "deepseek-v4-flash"


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
    assert default_structuring_client.model == "deepseek-v4-flash"

    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])
    default_summarization_client = OpenAIArticleSummarizationClient()
    assert default_summarization_client.model == "deepseek-v4-flash"


def test_deepseek_summarization_client_uses_deepseek_envs_and_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_SUMMARIZATION_MODEL", "deepseek-summary-model")
    calls, api_keys = install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = DeepSeekArticleSummarizationClient()

    assert client.model == "deepseek-summary-model"
    assert api_keys == ["deepseek-test-key"]
    assert calls[0] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}


def test_deepseek_structuring_client_uses_deepseek_envs_and_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_STRUCTURING_MODEL", "deepseek-structuring-model")
    calls, api_keys = install_fake_openai(
        monkeypatch,
        [FakeCompletion(content='{"events": []}')],
    )

    client = DeepSeekEventStructuringClient()
    result = client.extract_events(
        type(
            "Article",
            (),
            {
                "article_id": 1,
                "title": "T",
                "description": "D",
                "content": "C",
                "url": "U",
            },
        )()
    )

    assert result.events == []
    assert client.model == "deepseek-structuring-model"
    assert api_keys == ["deepseek-test-key"]
    assert calls[0] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}


def test_event_structuring_agent_defaults_to_deepseek_client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(content='{"events": []}')])

    agent = EventStructuringAgent()

    assert isinstance(agent.llm_client, DeepSeekEventStructuringClient)


def test_answering_and_summarization_agents_default_to_deepseek_clients(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True}), FakeCompletion(parsed={"ok": True})])

    answering_agent = RAGAnsweringAgent()
    summarization_agent = SummarizationAgent()

    assert isinstance(answering_agent.llm_client, DeepSeekRAGAnsweringClient)
    assert isinstance(summarization_agent.llm_client, DeepSeekArticleSummarizationClient)


def test_reranking_agent_defaults_to_deepseek_client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    install_fake_openai(
        monkeypatch,
        [FakeCompletion(content='{"ranked_candidates":[{"candidate_id":"1","reason":"ok"}]}')],
    )

    agent = RAGRerankingAgent()

    assert isinstance(agent.llm_client, DeepSeekRAGRerankingClient)


def test_watchlist_agents_default_to_deepseek_clients(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True}), FakeCompletion(parsed={"ok": True})])

    triage_agent = WatchlistTriageAgent()
    reviewer_agent = WatchlistReviewerAgent()

    assert isinstance(triage_agent.llm_client, DeepSeekWatchlistTriageClient)
    assert isinstance(reviewer_agent.llm_client, DeepSeekWatchlistReviewerClient)


def test_recommendation_agent_defaults_to_deepseek_client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    agent = RecommendationAgent()

    assert isinstance(agent.llm_client, DeepSeekRecommendationClient)


def test_deepseek_watchlist_and_recommendation_clients_use_deepseek_envs_and_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_TRIAGE_MODEL", "deepseek-triage-model")
    monkeypatch.setenv("DEEPSEEK_REVIEWER_MODEL", "deepseek-reviewer-model")
    monkeypatch.setenv("DEEPSEEK_RECOMMENDATION_MODEL", "deepseek-recommendation-model")
    calls, api_keys = install_fake_openai(
        monkeypatch,
        [FakeCompletion(parsed={"ok": True}), FakeCompletion(parsed={"ok": True}), FakeCompletion(parsed={"ok": True})],
    )

    triage_client = DeepSeekWatchlistTriageClient()
    reviewer_client = DeepSeekWatchlistReviewerClient()
    recommendation_client = DeepSeekRecommendationClient()

    assert triage_client.model == "deepseek-triage-model"
    assert reviewer_client.model == "deepseek-reviewer-model"
    assert recommendation_client.model == "deepseek-recommendation-model"
    assert api_keys == ["deepseek-test-key", "deepseek-test-key", "deepseek-test-key"]
    assert calls[0] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}
    assert calls[1] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}
    assert calls[2] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}


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
        {"client_base_url": None, "client_timeout": 90.0},
        {
            "model": "dummy-model",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user payload"},
            ],
            "response_format": dict,
        }
    ]


def test_deepseek_reranking_client_uses_deepseek_envs_and_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_RERANK_MODEL", "deepseek-custom-model")
    calls, api_keys = install_fake_openai(
        monkeypatch,
        [FakeCompletion(content='{"ranked_candidates":[{"candidate_id":"1","reason":"short"}]}')],
    )

    client = DeepSeekRAGRerankingClient()
    result = client.rerank_candidates(
        RerankingRequest(
            question="Q",
            candidates=[
                RerankCandidate(
                    candidate_id="1",
                    title="T",
                    snippet="S",
                    original_rank=1,
                )
            ],
        )
    )

    assert result.ranked_candidates[0].candidate_id == "1"
    assert client.model == "deepseek-custom-model"
    assert api_keys == ["deepseek-test-key"]
    assert calls[0] == {"client_base_url": "https://api.deepseek.com", "client_timeout": 90.0}


def test_base_client_uses_env_override_for_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DUMMY_MODEL", "dummy-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
    calls, _ = install_fake_openai(monkeypatch, [FakeCompletion(parsed={"result": 1})])

    DummyStructuredClient()

    assert calls[0] == {"client_base_url": None, "client_timeout": 12.5}


def test_deepseek_reranking_client_requires_deepseek_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(Exception) as exc_info:
        DeepSeekRAGRerankingClient()

    assert "DEEPSEEK_API_KEY" in str(exc_info.value)


def test_deepseek_reranking_client_does_not_fall_back_to_openai_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    install_fake_openai(monkeypatch, [FakeCompletion(parsed={"ok": True})])

    client = DeepSeekRAGRerankingClient()

    assert client.model == "deepseek-v4-pro"


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
