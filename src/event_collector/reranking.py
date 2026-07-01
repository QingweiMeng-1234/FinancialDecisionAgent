"""LLM-backed retrieval reranking for grounded news answers."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent, normalize_retrieval_intent


DEFAULT_RERANK_TOP_K = 5


class RerankCandidate(BaseModel):
    """Compact whole-article candidate passed into the reranking step."""

    candidate_id: str
    title: str
    summary: str | None = None
    snippet: str
    original_rank: int = Field(ge=1)

    @field_validator("candidate_id", "title", "snippet")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Candidate fields must be non-empty")
        return cleaned

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class RerankedItem(BaseModel):
    candidate_id: str
    reason: str

    @field_validator("candidate_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Reranked items require non-empty text")
        return cleaned


class RerankResponse(BaseModel):
    ranked_candidates: list[RerankedItem] = Field(min_length=1)


class RerankMetadata(BaseModel):
    ranked_candidates: list[RerankedItem] = Field(default_factory=list)


class RerankingRequest(BaseModel):
    question: str
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT
    candidates: list[RerankCandidate] = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Question must be non-empty")
        return cleaned

    @field_validator("retrieval_intent")
    @classmethod
    def validate_retrieval_intent(cls, value: str) -> RetrievalIntent:
        return normalize_retrieval_intent(value)


class RerankingLLMClient(Protocol):
    def rerank_candidates(self, request: RerankingRequest) -> Any:
        """Return data compatible with RerankResponse."""
        ...


class OpenAIRAGRerankingClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for retrieval reranking."""

    DEFAULT_MODEL = "deepseek-v4-flash"
    MODEL_ENV_VAR = "OPENAI_RERANK_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for retrieval reranking"
    REFUSAL_ERROR_PREFIX = "OpenAI refused retrieval reranking request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed reranking response"

    def rerank_candidates(self, request: RerankingRequest) -> RerankResponse:
        return self.parse_structured_output(
            system_prompt=_system_prompt_for_intent(request.retrieval_intent),
            user_content=_format_request(request),
            response_format=RerankResponse,
        )


class DeepSeekRAGRerankingClient(OpenAIStructuredOutputClient):
    """DeepSeek structured-output adapter for retrieval reranking."""

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL_ENV_VAR = "DEEPSEEK_BASE_URL"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-pro"
    MODEL_ENV_VAR = "DEEPSEEK_RERANK_MODEL"
    FALLBACK_TO_OPENAI_MODEL = False
    MISSING_KEY_MESSAGE = "DEEPSEEK_API_KEY is required for retrieval reranking"
    REFUSAL_ERROR_PREFIX = "DeepSeek refused retrieval reranking request"
    EMPTY_RESPONSE_MESSAGE = "DeepSeek returned no parsed reranking response"

    def rerank_candidates(self, request: RerankingRequest) -> RerankResponse:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _deepseek_system_prompt_for_intent(request.retrieval_intent)},
                {"role": "user", "content": _format_deepseek_request(request)},
            ],
            response_format={"type": "json_object"},
        )
        message = completion.choices[0].message
        content = " ".join((getattr(message, "content", "") or "").split()).strip()
        if not content:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return RerankResponse.model_validate(json.loads(content))


RERANKING_SYSTEM_PROMPT = """
You are the Retrieval Reranking Agent for a financial news system.

Reorder the provided whole-article candidates by relevance to the user's question.

Requirements:
- Reorder only. Do not filter, merge, invent, or drop candidates.
- Return every provided candidate_id exactly once.
- Base ranking only on the provided title, summary, and snippet.
- Provide a short reason for each candidate's relative relevance.
""".strip()


INDIRECT_RERANKING_SYSTEM_PROMPT = """
You are the Retrieval Reranking Agent for a financial news system.

Reorder the provided whole-article candidates by indirect relevance to the user's question.

Requirements:
- Reorder only. Do not filter, merge, invent, or drop candidates.
- Return every provided candidate_id exactly once.
- Base ranking only on the provided title, summary, and snippet.
- Treat competitor developments, supplier/customer exposure, platform or ecosystem ties,
  business-line exposure, and industry-theme spillovers as valid indirect relevance signals.
- Prefer target-adjacent business impact over generic market background.
- Provide a short reason for each candidate's relative relevance.
""".strip()


DEEPSEEK_RERANKING_SYSTEM_PROMPT = """
You are the Retrieval Reranking Agent for a financial news system.

Return only valid json matching this schema:
{
  "ranked_candidates": [
    {"candidate_id": "1", "reason": "short reason"}
  ]
}

Requirements:
- Reorder only. Do not filter, merge, invent, or drop candidates.
- Return every provided candidate_id exactly once.
- Base ranking only on the provided title, summary, and snippet.
- Keep each reason short.
""".strip()


DEEPSEEK_INDIRECT_RERANKING_SYSTEM_PROMPT = """
You are the Retrieval Reranking Agent for a financial news system.

Return only valid json matching this schema:
{
  "ranked_candidates": [
    {"candidate_id": "1", "reason": "short reason"}
  ]
}

Requirements:
- Reorder only. Do not filter, merge, invent, or drop candidates.
- Return every provided candidate_id exactly once.
- Base ranking only on the provided title, summary, and snippet.
- Treat competitors, suppliers, customers, platform/ecosystem ties, business-line exposure,
  and theme or industry spillovers as valid indirect relevance signals.
- Keep each reason short.
""".strip()


class RAGRerankingAgent:
    """Validates and returns a strict reranked ordering of provided candidates."""

    def __init__(self, llm_client: RerankingLLMClient | None = None):
        self.llm_client = llm_client or DeepSeekRAGRerankingClient()

    def rerank_candidates(
        self,
        question: str,
        candidates: list[RerankCandidate],
        *,
        retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT,
    ) -> RerankMetadata:
        request = RerankingRequest(
            question=question,
            retrieval_intent=normalize_retrieval_intent(retrieval_intent),
            candidates=candidates,
        )
        raw_response = self.llm_client.rerank_candidates(request)
        response = RerankResponse.model_validate(raw_response)
        _validate_rerank_response(request.candidates, response)
        return RerankMetadata(ranked_candidates=response.ranked_candidates)


def rerank_candidates(
    question: str,
    candidates: list[RerankCandidate],
    reranking_agent: RAGRerankingAgent | None = None,
    *,
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT,
) -> RerankMetadata:
    """Rerank compact retrieval candidates for downstream answering."""
    agent = reranking_agent or RAGRerankingAgent()
    return agent.rerank_candidates(
        question,
        candidates,
        retrieval_intent=normalize_retrieval_intent(retrieval_intent),
    )


def _system_prompt_for_intent(retrieval_intent: RetrievalIntent) -> str:
    if retrieval_intent == "indirect":
        return INDIRECT_RERANKING_SYSTEM_PROMPT
    return RERANKING_SYSTEM_PROMPT


def _deepseek_system_prompt_for_intent(retrieval_intent: RetrievalIntent) -> str:
    if retrieval_intent == "indirect":
        return DEEPSEEK_INDIRECT_RERANKING_SYSTEM_PROMPT
    return DEEPSEEK_RERANKING_SYSTEM_PROMPT


def _validate_rerank_response(
    candidates: list[RerankCandidate],
    response: RerankResponse,
) -> None:
    expected_ids = [candidate.candidate_id for candidate in candidates]
    actual_ids = [item.candidate_id for item in response.ranked_candidates]

    if len(actual_ids) != len(expected_ids):
        raise ValueError("Reranker must return the same number of candidate IDs it received")

    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Reranker returned duplicate candidate IDs")

    expected_id_set = set(expected_ids)
    actual_id_set = set(actual_ids)
    if actual_id_set != expected_id_set:
        invented_ids = sorted(actual_id_set - expected_id_set)
        missing_ids = sorted(expected_id_set - actual_id_set)
        details = []
        if invented_ids:
            details.append(f"invented IDs: {invented_ids}")
        if missing_ids:
            details.append(f"missing IDs: {missing_ids}")
        raise ValueError("Reranker returned invalid candidate IDs: " + ", ".join(details))


def _format_request(request: RerankingRequest) -> str:
    candidate_blocks = []
    for candidate in request.candidates:
        summary_block = candidate.summary or "None"
        candidate_blocks.append(
            (
                f"Candidate ID: {candidate.candidate_id}\n"
                f"Original Rank: {candidate.original_rank}\n"
                f"Title: {candidate.title}\n"
                f"Summary:\n{summary_block}\n"
                f"Snippet:\n{candidate.snippet}"
            )
        )
    return (
        f"User Question:\n{request.question}\n\n"
        f"Retrieval Intent:\n{request.retrieval_intent}\n\n"
        "Candidates:\n"
        f"{'\n\n'.join(candidate_blocks)}"
    )


def _format_deepseek_request(request: RerankingRequest) -> str:
    return (
        "Return the reranked candidates as json.\n\n"
        f"{_format_request(request)}"
    )
