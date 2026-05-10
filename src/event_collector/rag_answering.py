"""Minimal true-RAG answering over retrieved news articles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.reranking import (
    DEFAULT_RERANK_TOP_K,
    RAGRerankingAgent,
    RerankCandidate,
    RerankMetadata,
    rerank_candidates,
)
from event_collector.vector_store import VectorStore


DEFAULT_TOP_K = 3
DEFAULT_RETRIEVAL_TOP_K = DEFAULT_RERANK_TOP_K
DEFAULT_EXCERPT_CHARS = 700
DEFAULT_SNIPPET_CHARS = 220


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class RetrievedEvidence:
    """Compact retrieved article payload passed into the answering step."""

    id: int
    title: str
    url: str
    summary: str | None
    excerpt: str
    snippet: str


@dataclass
class AnswerQueryRequest:
    """Grounded answer request built from retrieved evidence."""

    question: str
    evidence: list[RetrievedEvidence]


class RAGSource(BaseModel):
    id: int = Field(ge=1)
    title: str
    url: str
    snippet: str


class CitedPoint(BaseModel):
    text: str
    citations: list[int] = Field(default_factory=list, min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Point text must be non-empty")
        return cleaned

    @field_validator("citations")
    @classmethod
    def normalize_citations(cls, values: list[int]) -> list[int]:
        normalized = sorted({citation for citation in values if citation >= 1})
        if not normalized:
            raise ValueError("Point citations must contain at least one positive source id")
        return normalized


class RAGAnswerResponse(BaseModel):
    answer: str
    sources: list[RAGSource] = Field(default_factory=list)
    confidence: ConfidenceLevel
    insufficient_evidence: bool = False
    supporting_points: list[CitedPoint] = Field(default_factory=list)
    counter_points: list[CitedPoint] = Field(default_factory=list)
    rerank_metadata: RerankMetadata | None = None

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Answer must be non-empty")
        return cleaned


class AnsweringLLMClient(Protocol):
    def answer_query(self, request: AnswerQueryRequest) -> Any:
        """Return data compatible with RAGAnswerResponse."""
        ...


class OpenAIRAGAnsweringClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for grounded RAG answers."""

    DEFAULT_MODEL = "gpt-5.4"
    MODEL_ENV_VAR = "OPENAI_ANSWER_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for grounded RAG answering"
    REFUSAL_ERROR_PREFIX = "OpenAI refused grounded RAG answer request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed RAG answer"

    def answer_query(self, request: AnswerQueryRequest) -> RAGAnswerResponse:
        return self.parse_structured_output(
            system_prompt=ANSWERING_SYSTEM_PROMPT,
            user_content=_format_request(request),
            response_format=RAGAnswerResponse,
        )


ANSWERING_SYSTEM_PROMPT = """
You are the Grounded RAG Answering Agent for a financial news system.

Answer the user's question using only the retrieved sources provided.

Requirements:
- Do not use outside knowledge.
- Cite claims with numbered source references like [1] that match the provided source ids.
- If evidence is conflicting, surface both supporting_points and counter_points.
- If evidence is insufficient, say so clearly, set insufficient_evidence to true, and use low confidence.
- confidence must reflect the strength of the retrieved evidence, not your own certainty.
- Only include sources that were actually used in the answer or cited points.
- supporting_points and counter_points must each cite at least one source id.
""".strip()


class RAGAnsweringAgent:
    """Turns retrieved article evidence into a grounded structured answer."""

    def __init__(self, llm_client: AnsweringLLMClient | None = None):
        self.llm_client = llm_client or OpenAIRAGAnsweringClient()

    def answer_query(self, request: AnswerQueryRequest) -> RAGAnswerResponse:
        raw_response = self.llm_client.answer_query(request)
        response = RAGAnswerResponse.model_validate(raw_response)
        _validate_response_citations(response)
        return response


def answer_query(
    question: str,
    vector_store: VectorStore,
    top_k: int = DEFAULT_TOP_K,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    answering_agent: RAGAnsweringAgent | None = None,
    reranking_agent: RAGRerankingAgent | None = None,
) -> RAGAnswerResponse:
    """Retrieve evidence and generate a grounded answer."""
    search_results = vector_store.search(question, top_k=max(top_k, retrieval_top_k))
    candidates = build_rerank_candidates(
        search_results,
        max_results=max(top_k, retrieval_top_k),
        snippet_chars=snippet_chars,
    )

    if not candidates:
        return RAGAnswerResponse(
            answer="I do not have enough retrieved evidence to answer that question yet.",
            sources=[],
            confidence=ConfidenceLevel.LOW,
            insufficient_evidence=True,
            supporting_points=[],
            counter_points=[],
            rerank_metadata=None,
        )

    rerank_metadata = rerank_candidates(
        question,
        candidates,
        reranking_agent=reranking_agent,
    )
    reranked_results = reorder_search_results(search_results, rerank_metadata)
    evidence = build_retrieved_evidence(
        reranked_results,
        max_results=top_k,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
    )

    if not evidence:
        return RAGAnswerResponse(
            answer="I do not have enough retrieved evidence to answer that question yet.",
            sources=[],
            confidence=ConfidenceLevel.LOW,
            insufficient_evidence=True,
            supporting_points=[],
            counter_points=[],
            rerank_metadata=rerank_metadata,
        )

    agent = answering_agent or RAGAnsweringAgent()
    response = agent.answer_query(AnswerQueryRequest(question=question, evidence=evidence))
    response.rerank_metadata = rerank_metadata
    return response


def build_retrieved_evidence(
    search_results: list[dict],
    max_results: int = DEFAULT_TOP_K,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RetrievedEvidence]:
    """Convert raw vector-store results into compact promptable evidence."""
    evidence = []
    for index, result in enumerate(search_results[:max_results], start=1):
        title = _clean_text(result.get("title", "Untitled"))
        url = _clean_text(result.get("url", "N/A"))
        summary = result.get("summary")
        cleaned_summary = _clean_text(summary) if summary else None
        content = _clean_text(result.get("content", ""))
        excerpt = _truncate_text(content, excerpt_chars)
        snippet_source = cleaned_summary or excerpt or title
        snippet = _truncate_text(snippet_source, snippet_chars)
        evidence.append(
            RetrievedEvidence(
                id=index,
                title=title,
                url=url,
                summary=cleaned_summary,
                excerpt=excerpt,
                snippet=snippet,
            )
        )
    return evidence


def build_rerank_candidates(
    search_results: list[dict],
    max_results: int = DEFAULT_RETRIEVAL_TOP_K,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RerankCandidate]:
    """Convert raw retrieval output into compact reranking candidates."""
    candidates = []
    for index, result in enumerate(search_results[:max_results], start=1):
        title = _clean_text(result.get("title", "Untitled"))
        summary = result.get("summary")
        cleaned_summary = _clean_text(summary) if summary else None
        content = _clean_text(result.get("content", ""))
        snippet_source = cleaned_summary or content or title
        snippet = _truncate_text(snippet_source, snippet_chars)
        candidates.append(
            RerankCandidate(
                candidate_id=str(index),
                title=title,
                summary=cleaned_summary,
                snippet=snippet,
                original_rank=index,
            )
        )
    return candidates


def reorder_search_results(search_results: list[dict], rerank_metadata: RerankMetadata) -> list[dict]:
    """Reorder raw retrieval results according to validated reranker output."""
    indexed_results = {
        str(index): result
        for index, result in enumerate(search_results[: len(rerank_metadata.ranked_candidates)], start=1)
    }
    return [indexed_results[item.candidate_id] for item in rerank_metadata.ranked_candidates]


def render_citation_list(citations: list[int]) -> str:
    return " ".join(f"[{citation}]" for citation in citations)


def _validate_response_citations(response: RAGAnswerResponse) -> None:
    valid_source_ids = {source.id for source in response.sources}
    for point in response.supporting_points + response.counter_points:
        missing = set(point.citations) - valid_source_ids
        if missing:
            raise ValueError(f"Point citations reference unknown source ids: {sorted(missing)}")


def _format_request(request: AnswerQueryRequest) -> str:
    evidence_blocks = []
    for evidence in request.evidence:
        summary_block = evidence.summary or "None"
        evidence_blocks.append(
            (
                f"Source [{evidence.id}]\n"
                f"Title: {evidence.title}\n"
                f"URL: {evidence.url}\n"
                f"Summary:\n{summary_block}\n"
                f"Excerpt:\n{evidence.excerpt}"
            )
        )
    return (
        f"User Question:\n{request.question}\n\n"
        "Retrieved Sources:\n"
        f"{'\n\n'.join(evidence_blocks)}"
    )


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    trimmed = value[:limit].rstrip()
    return f"{trimmed}..."
