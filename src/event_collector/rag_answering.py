"""Minimal true-RAG answering over retrieved news articles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Any, Protocol

from event_collector.entity_kb import SQLiteEntityStore
from pydantic import BaseModel, Field, field_validator

from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.retrieval_execution import RetrievalWorkItem, execute_retrieval_batch
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent, normalize_retrieval_intent
from event_collector.retrieval_orchestration import (
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_SNIPPET_CHARS,
    RerankMetadata,
    RetrievedArticleEvidence,
    build_retrieved_evidence as build_shared_retrieved_evidence,
)
from event_collector.reranking import RAGRerankingAgent
from event_collector.vector_store import VectorStore


DEFAULT_TOP_K = 3


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

    DEFAULT_MODEL = "deepseek-v4-pro"
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


class DeepSeekRAGAnsweringClient(OpenAIStructuredOutputClient):
    """DeepSeek structured-output adapter for grounded RAG answers."""

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL_ENV_VAR = "DEEPSEEK_BASE_URL"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-pro"
    MODEL_ENV_VAR = "DEEPSEEK_ANSWER_MODEL"
    FALLBACK_TO_OPENAI_MODEL = False
    MISSING_KEY_MESSAGE = "DEEPSEEK_API_KEY is required for grounded RAG answering"
    REFUSAL_ERROR_PREFIX = "DeepSeek refused grounded RAG answer request"
    EMPTY_RESPONSE_MESSAGE = "DeepSeek returned no parsed RAG answer"

    def answer_query(self, request: AnswerQueryRequest) -> RAGAnswerResponse:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": DEEPSEEK_ANSWERING_SYSTEM_PROMPT},
                {"role": "user", "content": _format_request(request)},
            ],
            response_format={"type": "json_object"},
        )
        message = completion.choices[0].message
        content = " ".join((getattr(message, "content", "") or "").split()).strip()
        if not content:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return RAGAnswerResponse.model_validate(json.loads(content))


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


DEEPSEEK_ANSWERING_SYSTEM_PROMPT = """
You are the Grounded RAG Answering Agent for a financial news system.

Return only valid json matching this schema:
{
  "answer": "grounded answer with [1] style citations when needed",
  "sources": [
    {"id": 1, "title": "source title", "url": "https://example.com", "snippet": "used snippet"}
  ],
  "confidence": "high",
  "insufficient_evidence": false,
  "supporting_points": [
    {"text": "claim text", "citations": [1]}
  ],
  "counter_points": [
    {"text": "counter text", "citations": [2]}
  ]
}

Requirements:
- Do not use outside knowledge.
- Cite claims with numbered source references like [1] that match the provided source ids.
- If evidence is conflicting, surface both supporting_points and counter_points.
- If evidence is insufficient, say so clearly, set insufficient_evidence to true, and use low confidence.
- confidence must reflect the strength of the retrieved evidence, not your own certainty.
- Only include sources that were actually used in the answer or cited points.
- supporting_points and counter_points must each cite at least one source id when present.
""".strip()


class RAGAnsweringAgent:
    """Turns retrieved article evidence into a grounded structured answer."""

    def __init__(self, llm_client: AnsweringLLMClient | None = None):
        self.llm_client = llm_client or DeepSeekRAGAnsweringClient()

    def answer_query(self, request: AnswerQueryRequest) -> RAGAnswerResponse:
        raw_response = self.llm_client.answer_query(request)
        response = RAGAnswerResponse.model_validate(raw_response)
        _validate_response_citations(response, request)
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
    company_kb: SQLiteEntityStore | None = None,
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT,
) -> RAGAnswerResponse:
    """Retrieve evidence and generate a grounded answer."""
    retrieval_intent = normalize_retrieval_intent(retrieval_intent)
    retrieval_result = execute_retrieval_batch(
        [
            RetrievalWorkItem(
                key=question,
                query=question,
                top_k=top_k,
                retrieval_top_k=retrieval_top_k,
                excerpt_chars=excerpt_chars,
                snippet_chars=snippet_chars,
                retrieval_intent=retrieval_intent,
            )
        ],
        vector_store,
        reranking_agent=reranking_agent,
        company_kb_provider=company_kb,
        max_concurrency=1,
    )[0]
    bundle = retrieval_result.bundle if retrieval_result.succeeded else None
    if bundle is None:
        if retrieval_result.error_message:
            raise RuntimeError(retrieval_result.error_message)
        return RAGAnswerResponse(
            answer="I could not complete retrieval for that question, so I do not have enough evidence to answer yet.",
            sources=[],
            confidence=ConfidenceLevel.LOW,
            insufficient_evidence=True,
            supporting_points=[],
            counter_points=[],
            rerank_metadata=None,
        )
    if not bundle.evidence:
        return RAGAnswerResponse(
            answer="I do not have enough retrieved evidence to answer that question yet.",
            sources=[],
            confidence=ConfidenceLevel.LOW,
            insufficient_evidence=True,
            supporting_points=[],
            counter_points=[],
            rerank_metadata=bundle.rerank_metadata,
        )

    agent = answering_agent or RAGAnsweringAgent()
    response = agent.answer_query(
        AnswerQueryRequest(
            question=question,
            evidence=_build_answering_evidence(bundle.evidence),
        )
    )
    response.rerank_metadata = bundle.rerank_metadata
    return response


def render_citation_list(citations: list[int]) -> str:
    return " ".join(f"[{citation}]" for citation in citations)


def build_retrieved_evidence(
    search_results: list[dict],
    max_results: int = DEFAULT_TOP_K,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RetrievedEvidence]:
    """Backward-compatible answering evidence helper built on shared retrieval shaping."""
    return _build_answering_evidence(
        build_shared_retrieved_evidence(
            search_results,
            max_results=max_results,
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
        )
    )


def _validate_response_citations(
    response: RAGAnswerResponse,
    request: AnswerQueryRequest,
) -> None:
    """Bind every model-returned source and citation to the retrieved evidence."""
    evidence_by_id = {evidence.id: evidence for evidence in request.evidence}
    if len(evidence_by_id) != len(request.evidence):
        raise ValueError("Input evidence contains duplicate source ids")

    invalid_source_ids = {source.id for source in response.sources} - set(evidence_by_id)
    if invalid_source_ids:
        raise ValueError(
            "Response source ids not present in input evidence: "
            f"{sorted(invalid_source_ids)}"
        )

    for source in response.sources:
        evidence = evidence_by_id[source.id]
        for field in ("title", "url", "snippet"):
            if getattr(source, field) != getattr(evidence, field):
                raise ValueError(f"Response source {field} does not match input evidence")

    valid_evidence_ids = set(evidence_by_id)
    answer_citations = {int(citation) for citation in re.findall(r"\[(\d+)\]", response.answer)}
    if not response.insufficient_evidence and not answer_citations:
        raise ValueError("Material answers require at least one inline citation")
    unknown_answer_citations = answer_citations - valid_evidence_ids
    if unknown_answer_citations:
        raise ValueError(
            "Answer citations reference unknown input evidence ids: "
            f"{sorted(unknown_answer_citations)}"
        )

    valid_source_ids = {source.id for source in response.sources}
    omitted_answer_citations = answer_citations - valid_source_ids
    if omitted_answer_citations:
        raise ValueError(
            "Answer citations reference omitted response source ids: "
            f"{sorted(omitted_answer_citations)}"
        )
    for point in response.supporting_points + response.counter_points:
        unknown_evidence_ids = set(point.citations) - valid_evidence_ids
        if unknown_evidence_ids:
            raise ValueError(f"Point citations reference unknown source ids: {sorted(unknown_evidence_ids)}")
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


def _build_answering_evidence(evidence: list[RetrievedArticleEvidence]) -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            id=item.id,
            title=item.title,
            url=item.url,
            summary=item.summary,
            excerpt=item.excerpt,
            snippet=item.snippet,
        )
        for item in evidence
    ]
