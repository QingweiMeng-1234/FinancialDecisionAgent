"""LLM-backed retrieval reranking for grounded news answers."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.openai_client_base import OpenAIStructuredOutputClient


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
    candidates: list[RerankCandidate] = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Question must be non-empty")
        return cleaned


class RerankingLLMClient(Protocol):
    def rerank_candidates(self, request: RerankingRequest) -> Any:
        """Return data compatible with RerankResponse."""
        ...


class OpenAIRAGRerankingClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for retrieval reranking."""

    DEFAULT_MODEL = "gpt-5.4-mini"
    MODEL_ENV_VAR = "OPENAI_RERANK_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for retrieval reranking"
    REFUSAL_ERROR_PREFIX = "OpenAI refused retrieval reranking request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed reranking response"

    def rerank_candidates(self, request: RerankingRequest) -> RerankResponse:
        return self.parse_structured_output(
            system_prompt=RERANKING_SYSTEM_PROMPT,
            user_content=_format_request(request),
            response_format=RerankResponse,
        )


RERANKING_SYSTEM_PROMPT = """
You are the Retrieval Reranking Agent for a financial news system.

Reorder the provided whole-article candidates by relevance to the user's question.

Requirements:
- Reorder only. Do not filter, merge, invent, or drop candidates.
- Return every provided candidate_id exactly once.
- Base ranking only on the provided title, summary, and snippet.
- Provide a short reason for each candidate's relative relevance.
""".strip()


class RAGRerankingAgent:
    """Validates and returns a strict reranked ordering of provided candidates."""

    def __init__(self, llm_client: RerankingLLMClient | None = None):
        self.llm_client = llm_client or OpenAIRAGRerankingClient()

    def rerank_candidates(self, question: str, candidates: list[RerankCandidate]) -> RerankMetadata:
        request = RerankingRequest(question=question, candidates=candidates)
        raw_response = self.llm_client.rerank_candidates(request)
        response = RerankResponse.model_validate(raw_response)
        _validate_rerank_response(request.candidates, response)
        return RerankMetadata(ranked_candidates=response.ranked_candidates)


def rerank_candidates(
    question: str,
    candidates: list[RerankCandidate],
    reranking_agent: RAGRerankingAgent | None = None,
) -> RerankMetadata:
    """Rerank compact retrieval candidates for downstream answering."""
    agent = reranking_agent or RAGRerankingAgent()
    return agent.rerank_candidates(question, candidates)


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
        "Candidates:\n"
        f"{'\n\n'.join(candidate_blocks)}"
    )
