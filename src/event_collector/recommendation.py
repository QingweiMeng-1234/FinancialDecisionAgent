"""News-grounded Buffett-lens stock recommendation flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
)
from event_collector.news_storage import SQLiteNewsStore
from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.rag_answering import (
    ConfidenceLevel,
    RAGSource,
    RetrievedEvidence,
    build_retrieved_evidence,
    build_rerank_candidates,
    reorder_search_results,
)
from event_collector.reranking import (
    DEFAULT_RERANK_TOP_K,
    RAGRerankingAgent,
    RerankMetadata,
    rerank_candidates,
)
from event_collector.vector_store import VectorStore


DEFAULT_TOP_K = 3
DEFAULT_RETRIEVAL_TOP_K = DEFAULT_RERANK_TOP_K
DEFAULT_EXCERPT_CHARS = 700
DEFAULT_SNIPPET_CHARS = 220
DEFAULT_REPORTS_DIR = os.path.join("reports", "recommendations")
GENERAL_MARKET_SLUG = "general-market"
GENERAL_MARKET_LABEL = "General Market"


class RecommendationDecision(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass
class TargetRecommendationRequest:
    target: str
    top_k: int = DEFAULT_TOP_K
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS
    snippet_chars: int = DEFAULT_SNIPPET_CHARS
    output_dir: str = DEFAULT_REPORTS_DIR


@dataclass
class RecommendationEvidence:
    article_id: int
    source_id: int
    title: str
    url: str
    summary: str | None
    excerpt: str
    snippet: str
    published_at: str | None


class AggregatedTargetEvent(BaseModel):
    source_id: int = Field(ge=1)
    article_id: int = Field(ge=1)
    article_title: str
    affected_asset: str
    event_type: EventType
    direction: EventDirection
    importance: EventImportance
    time_horizon: TimeHorizon
    score: int
    reasoning: str
    evidence_excerpt: str


class AggregatedSignal(BaseModel):
    target: str
    target_specific_event_count: int = Field(ge=0)
    macro_score: int = 0
    company_score: int = 0
    sector_score: int = 0
    market_score: int = 0
    net_score: int = 0
    dominant_driver: str
    summary: str
    conflicts: list[str] = Field(default_factory=list)
    target_events: list[AggregatedTargetEvent] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    decision: RecommendationDecision
    confidence: ConfidenceLevel
    time_horizon: TimeHorizon
    reasoning: str
    key_risks: list[str] = Field(default_factory=list, max_length=3)
    insufficient_evidence: bool = False
    sources: list[RAGSource] = Field(default_factory=list)
    aggregation: AggregatedSignal | None = None
    rerank_metadata: RerankMetadata | None = None

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Reasoning must be non-empty")
        return cleaned

    @field_validator("key_risks")
    @classmethod
    def validate_key_risks(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()).strip() for value in values if value and value.strip()]
        return cleaned[:3]


@dataclass
class RecommendationDecisionRequest:
    target: str
    aggregation: AggregatedSignal
    evidence: list[RecommendationEvidence]


class RecommendationLLMClient(Protocol):
    def recommend(self, request: RecommendationDecisionRequest) -> Any:
        """Return data compatible with RecommendationResponse."""
        ...


class OpenAIRecommendationClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for Buffett-lens recommendations."""

    DEFAULT_MODEL = "gpt-5.4"
    MODEL_ENV_VAR = "OPENAI_ANSWER_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for recommendation generation"
    REFUSAL_ERROR_PREFIX = "OpenAI refused recommendation request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed recommendation"

    def recommend(self, request: RecommendationDecisionRequest) -> RecommendationResponse:
        return self.parse_structured_output(
            system_prompt=RECOMMENDATION_SYSTEM_PROMPT,
            user_content=_format_recommendation_request(request),
            response_format=RecommendationResponse,
        )


RECOMMENDATION_SYSTEM_PROMPT = """
You are the Decision Agent for a financial news system using a Buffett-style lens.

You must make a recommendation for the requested target using only the provided news-grounded aggregation and cited evidence.

Requirements:
- Use only the supplied evidence. Do not use outside knowledge.
- This is a news-grounded recommendation, not a full intrinsic-value appraisal.
- Acknowledge when long-term fundamentals, valuation, moat, or management information are missing.
- Separate short-term news noise from durable business impairment when possible.
- Prefer HOLD when evidence is mixed, weak, mostly macro, or insufficiently target-specific.
- If evidence is insufficient, set insufficient_evidence to true and keep confidence low.
- Return only BUY, HOLD, or SELL.
- key_risks must contain at most 3 concise items.
- Only include sources that were actually relevant to the reasoning.
""".strip()


class RecommendationAgent:
    """Produces a Buffett-lens recommendation from aggregated news signals."""

    def __init__(self, llm_client: RecommendationLLMClient | None = None):
        self.llm_client = llm_client or OpenAIRecommendationClient()

    def recommend(self, request: RecommendationDecisionRequest) -> RecommendationResponse:
        raw_response = self.llm_client.recommend(request)
        return RecommendationResponse.model_validate(raw_response)


def recommend_target(
    target: str,
    vector_store: VectorStore,
    storage: SQLiteNewsStore,
    top_k: int = DEFAULT_TOP_K,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    recommendation_agent: RecommendationAgent | None = None,
    reranking_agent: RAGRerankingAgent | None = None,
) -> RecommendationResponse:
    """Retrieve evidence, aggregate target-specific signals, and recommend."""
    normalized_target = normalize_target(target)
    search_results = vector_store.search(target, top_k=max(top_k, retrieval_top_k))
    candidates = build_rerank_candidates(
        search_results,
        max_results=max(top_k, retrieval_top_k),
        snippet_chars=snippet_chars,
    )
    if not candidates:
        return _build_insufficient_recommendation(
            target=target,
            reason=(
                "I do not have enough retrieved evidence to make a news-grounded Buffett-style "
                "recommendation for this target yet."
            ),
        )

    rerank_metadata = rerank_candidates(
        target,
        candidates,
        reranking_agent=reranking_agent,
    )
    reranked_results = reorder_search_results(search_results, rerank_metadata)
    retrieved_evidence = build_retrieved_evidence(
        reranked_results,
        max_results=top_k,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
    )
    recommendation_evidence = build_recommendation_evidence(reranked_results, retrieved_evidence)
    aggregation = aggregate_recommendation_signals(normalized_target, recommendation_evidence, storage)

    if aggregation is None:
        response = _build_insufficient_recommendation(
            target=target,
            reason=(
                "I found news articles, but they do not provide enough target-specific structured "
                "evidence to support a disciplined recommendation."
            ),
        )
        response.rerank_metadata = rerank_metadata
        return response

    if aggregation.target_specific_event_count == 0 and normalized_target != GENERAL_MARKET_SLUG:
        response = _build_insufficient_recommendation(
            target=target,
            reason=(
                "The retrieved evidence is mostly general-market context rather than target-specific "
                "signals, so the disciplined default is HOLD."
            ),
            aggregation=aggregation,
            sources=_build_sources_from_evidence(recommendation_evidence),
        )
        response.rerank_metadata = rerank_metadata
        return response

    agent = recommendation_agent or RecommendationAgent()
    response = agent.recommend(
        RecommendationDecisionRequest(
            target=normalized_target,
            aggregation=aggregation,
            evidence=recommendation_evidence,
        )
    )
    response.aggregation = aggregation
    response.rerank_metadata = rerank_metadata
    return response


def build_recommendation_evidence(
    reranked_results: list[dict],
    retrieved_evidence: list[RetrievedEvidence],
) -> list[RecommendationEvidence]:
    """Attach article IDs and published timestamps to retrieved evidence."""
    evidence = []
    for result, item in zip(reranked_results[: len(retrieved_evidence)], retrieved_evidence):
        evidence.append(
            RecommendationEvidence(
                article_id=int(result["id"]),
                source_id=item.id,
                title=item.title,
                url=item.url,
                summary=item.summary,
                excerpt=item.excerpt,
                snippet=item.snippet,
                published_at=result.get("published_at"),
            )
        )
    return evidence


def aggregate_recommendation_signals(
    target: str,
    evidence: list[RecommendationEvidence],
    storage: SQLiteNewsStore,
) -> AggregatedSignal | None:
    """Aggregate structured events relevant to the target from retrieved evidence."""
    target_events: list[AggregatedTargetEvent] = []
    target_specific_event_count = 0
    bucket_scores = {
        EventType.MACRO: 0,
        EventType.COMPANY: 0,
        EventType.SECTOR: 0,
        EventType.MARKET: 0,
    }

    for source in evidence:
        events = storage.list_structured_events_for_article(source.article_id)
        for event in events:
            match = classify_target_match(target, event.affected_asset)
            if not match:
                continue

            score = score_structured_event(event)
            target_events.append(
                AggregatedTargetEvent(
                    source_id=source.source_id,
                    article_id=source.article_id,
                    article_title=source.title,
                    affected_asset=event.affected_asset,
                    event_type=event.event_type,
                    direction=event.direction,
                    importance=event.importance,
                    time_horizon=event.time_horizon,
                    score=score,
                    reasoning=event.reasoning,
                    evidence_excerpt=event.evidence_excerpt,
                )
            )
            bucket_scores[event.event_type] += score
            if match == "target":
                target_specific_event_count += 1

    if not target_events:
        return None

    net_score = sum(bucket_scores.values())
    conflicts = detect_conflicts(target_events)
    dominant_driver = describe_dominant_driver(bucket_scores)
    summary = build_aggregation_summary(target, net_score, dominant_driver, conflicts, target_events)

    return AggregatedSignal(
        target=display_target(target),
        target_specific_event_count=target_specific_event_count,
        macro_score=bucket_scores[EventType.MACRO],
        company_score=bucket_scores[EventType.COMPANY],
        sector_score=bucket_scores[EventType.SECTOR],
        market_score=bucket_scores[EventType.MARKET],
        net_score=net_score,
        dominant_driver=dominant_driver,
        summary=summary,
        conflicts=conflicts,
        target_events=target_events,
    )


def score_structured_event(event: StructuredEvent) -> int:
    direction_score = {
        EventDirection.POSITIVE: 1,
        EventDirection.NEUTRAL: 0,
        EventDirection.NEGATIVE: -1,
    }[event.direction]
    importance_multiplier = {
        EventImportance.HIGH: 3,
        EventImportance.MEDIUM: 2,
        EventImportance.LOW: 1,
    }[event.importance]
    return direction_score * importance_multiplier


def classify_target_match(target: str, affected_asset: str) -> str | None:
    normalized_asset = normalize_target(affected_asset)
    if normalized_asset == GENERAL_MARKET_SLUG and target == GENERAL_MARKET_SLUG:
        return "target"
    if normalized_asset == GENERAL_MARKET_SLUG:
        return "market"
    if normalized_asset == target:
        return "target"

    target_token = normalized_target_token(target)
    asset_tokens = set(re.findall(r"[a-z0-9]+", normalized_asset))
    if target_token and target_token in asset_tokens:
        return "target"
    return None


def detect_conflicts(events: list[AggregatedTargetEvent]) -> list[str]:
    conflicts = []
    has_positive = any(event.score > 0 for event in events)
    has_negative = any(event.score < 0 for event in events)
    short_term_negative = any(
        event.score < 0 and event.time_horizon in {TimeHorizon.SHORT_TERM, TimeHorizon.BOTH}
        for event in events
    )
    long_term_positive = any(
        event.score > 0 and event.time_horizon in {TimeHorizon.LONG_TERM, TimeHorizon.BOTH}
        for event in events
    )
    if has_positive and has_negative:
        conflicts.append("Positive and negative signals are both present in the retrieved evidence.")
    if short_term_negative and long_term_positive:
        conflicts.append("Short-term headwinds conflict with more durable long-term positives.")
    return conflicts


def describe_dominant_driver(bucket_scores: dict[EventType, int]) -> str:
    non_zero = {event_type: score for event_type, score in bucket_scores.items() if score != 0}
    if not non_zero:
        return "No dominant driver"

    strongest_type, strongest_score = max(non_zero.items(), key=lambda item: abs(item[1]))
    tied = [
        event_type
        for event_type, score in non_zero.items()
        if event_type != strongest_type and abs(score) == abs(strongest_score)
    ]
    if tied:
        labels = ", ".join(sorted(event_type.value for event_type in [strongest_type, *tied]))
        return f"Mixed drivers across {labels}"

    tone = "positive" if strongest_score > 0 else "negative"
    return f"{strongest_type.value} signals are the dominant {tone} driver"


def build_aggregation_summary(
    target: str,
    net_score: int,
    dominant_driver: str,
    conflicts: list[str],
    events: list[AggregatedTargetEvent],
) -> str:
    if net_score > 0:
        leaning = "overall positive"
    elif net_score < 0:
        leaning = "overall negative"
    else:
        leaning = "balanced or mixed"

    summary = (
        f"Retrieved signals for {display_target(target)} are {leaning}. "
        f"{dominant_driver}."
    )
    if conflicts:
        summary += f" Key conflict: {conflicts[0]}"
    else:
        summary += f" The aggregation is based on {len(events)} structured event(s)."
    return summary


def write_recommendation_report(
    target: str,
    response: RecommendationResponse,
    output_dir: str = DEFAULT_REPORTS_DIR,
    generated_at: datetime | None = None,
    debug_rerank: bool = False,
    debug_aggregation: bool = False,
) -> str:
    """Persist one Markdown recommendation report and return its path."""
    timestamp = generated_at or datetime.now()
    filename = build_report_filename(target, timestamp)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            render_recommendation_report(
                target,
                response,
                generated_at=timestamp,
                debug_rerank=debug_rerank,
                debug_aggregation=debug_aggregation,
            )
        )
    return path


def build_report_filename(target: str, generated_at: datetime) -> str:
    timestamp = generated_at.strftime("%Y-%m-%d_%H%M%S")
    slug = sanitize_target_for_filename(target)
    return f"{timestamp}_{slug}.md"


def render_recommendation_report(
    target: str,
    response: RecommendationResponse,
    generated_at: datetime | None = None,
    debug_rerank: bool = False,
    debug_aggregation: bool = False,
) -> str:
    """Render a Markdown recommendation report."""
    created_at = generated_at or datetime.now()
    lines = [
        f"# Recommendation Report: {display_target(normalize_target(target))}",
        "",
        f"- Generated at: {created_at.isoformat()}",
        f"- Target: {display_target(normalize_target(target))}",
        f"- Decision: {response.decision.value}",
        f"- Confidence: {response.confidence.value}",
        f"- Time horizon: {response.time_horizon.value}",
        f"- Insufficient evidence: {'yes' if response.insufficient_evidence else 'no'}",
        "",
        "## Reasoning",
        response.reasoning,
    ]

    if response.aggregation is not None:
        lines.extend(
            [
                "",
                "## Aggregation Summary",
                response.aggregation.summary,
                "",
                f"- Dominant driver: {response.aggregation.dominant_driver}",
                f"- Net score: {response.aggregation.net_score}",
                f"- Macro score: {response.aggregation.macro_score}",
                f"- Company score: {response.aggregation.company_score}",
                f"- Sector score: {response.aggregation.sector_score}",
                f"- Market score: {response.aggregation.market_score}",
            ]
        )
        if response.aggregation.conflicts:
            lines.extend(["", "## Conflicts"])
            for conflict in response.aggregation.conflicts:
                lines.append(f"- {conflict}")

    lines.extend(["", "## Key Risks"])
    if response.key_risks:
        for risk in response.key_risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- No specific additional risks were surfaced beyond the evidence limits.")

    lines.extend(["", "## Sources"])
    if response.sources:
        for source in response.sources:
            lines.append(f"- [{source.id}] {source.title} ({source.url})")
            lines.append(f"  - Snippet: {source.snippet}")
    else:
        lines.append("- No sources were cited.")

    if debug_aggregation and response.aggregation is not None:
        lines.extend(["", "## Aggregated Events"])
        for event in response.aggregation.target_events:
            lines.append(
                (
                    f"- [{event.source_id}] {event.article_title}: {event.event_type.value} / "
                    f"{event.direction.value} / {event.importance.value} / {event.time_horizon.value} "
                    f"(score {event.score})"
                )
            )
            lines.append(f"  - Asset: {event.affected_asset}")
            lines.append(f"  - Evidence: {event.evidence_excerpt}")

    if debug_rerank and response.rerank_metadata is not None:
        lines.extend(["", "## Rerank Debug"])
        for index, item in enumerate(response.rerank_metadata.ranked_candidates, start=1):
            lines.append(f"{index}. Candidate {item.candidate_id}: {item.reason}")

    return "\n".join(lines).strip() + "\n"


def normalize_target(target: str) -> str:
    cleaned = " ".join((target or "").split()).strip()
    lowered = cleaned.lower()
    if lowered in {"general market", "market", "broad market"}:
        return GENERAL_MARKET_SLUG
    return lowered


def normalized_target_token(target: str) -> str:
    normalized = normalize_target(target)
    if normalized == GENERAL_MARKET_SLUG:
        return normalized
    return re.sub(r"[^a-z0-9]+", "", normalized)


def sanitize_target_for_filename(target: str) -> str:
    normalized = normalize_target(target)
    if normalized == GENERAL_MARKET_SLUG:
        return GENERAL_MARKET_SLUG
    slug = re.sub(r"[^A-Za-z0-9]+", "-", target.strip()).strip("-")
    return slug.upper() or "TARGET"


def display_target(target: str) -> str:
    normalized = normalize_target(target)
    if normalized == GENERAL_MARKET_SLUG:
        return GENERAL_MARKET_LABEL
    return target.strip().upper() if target.strip() else "UNKNOWN"


def _build_sources_from_evidence(evidence: list[RecommendationEvidence]) -> list[RAGSource]:
    return [
        RAGSource(
            id=item.source_id,
            title=item.title,
            url=item.url,
            snippet=item.snippet,
        )
        for item in evidence
    ]


def _build_insufficient_recommendation(
    target: str,
    reason: str,
    aggregation: AggregatedSignal | None = None,
    sources: list[RAGSource] | None = None,
) -> RecommendationResponse:
    return RecommendationResponse(
        decision=RecommendationDecision.HOLD,
        confidence=ConfidenceLevel.LOW,
        time_horizon=TimeHorizon.LONG_TERM,
        reasoning=reason,
        key_risks=[
            "Retrieved evidence is not specific enough to separate temporary noise from durable impairment.",
            "Long-term valuation and business-quality evidence are missing from this news-only pass.",
        ],
        insufficient_evidence=True,
        sources=sources or [],
        aggregation=aggregation,
    )


def _format_recommendation_request(request: RecommendationDecisionRequest) -> str:
    source_blocks = []
    for source in request.evidence:
        summary = source.summary or "None"
        source_blocks.append(
            (
                f"Source [{source.source_id}]\n"
                f"Article ID: {source.article_id}\n"
                f"Title: {source.title}\n"
                f"URL: {source.url}\n"
                f"Published At: {source.published_at or 'Unknown'}\n"
                f"Summary:\n{summary}\n"
                f"Excerpt:\n{source.excerpt}"
            )
        )

    aggregation = request.aggregation
    event_lines = []
    for event in aggregation.target_events:
        event_lines.append(
            (
                f"- Source [{event.source_id}] {event.event_type.value} / {event.direction.value} / "
                f"{event.importance.value} / {event.time_horizon.value} / Asset: {event.affected_asset} / "
                f"Score: {event.score}\n"
                f"  Reasoning: {event.reasoning}\n"
                f"  Evidence: {event.evidence_excerpt}"
            )
        )

    return (
        f"Target:\n{display_target(request.target)}\n\n"
        "Aggregated Signal:\n"
        f"- Summary: {aggregation.summary}\n"
        f"- Dominant driver: {aggregation.dominant_driver}\n"
        f"- Net score: {aggregation.net_score}\n"
        f"- Macro score: {aggregation.macro_score}\n"
        f"- Company score: {aggregation.company_score}\n"
        f"- Sector score: {aggregation.sector_score}\n"
        f"- Market score: {aggregation.market_score}\n"
        f"- Target-specific events: {aggregation.target_specific_event_count}\n"
        f"- Conflicts: {aggregation.conflicts or ['None']}\n\n"
        "Target Events:\n"
        f"{os.linesep.join(event_lines) if event_lines else 'None'}\n\n"
        "Retrieved Sources:\n"
        f"{'\n\n'.join(source_blocks)}"
    )
