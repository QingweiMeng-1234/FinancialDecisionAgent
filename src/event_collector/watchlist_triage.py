"""Watchlist triage workflow for ranking daily research priorities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import logging
import os
import re
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.event_structuring import (
    EventStructuringAgent,
    StructuredEvent,
)
from event_collector.entity_kb import SQLiteEntityStore
from event_collector.news_storage import SQLiteNewsStore
from event_collector.openai_client_base import OpenAIStructuredOutputClient
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent, normalize_retrieval_intent
from event_collector.retrieval_orchestration import (
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_SNIPPET_CHARS,
    RetrievedArticleEvidence,
    retrieve_evidence_bundle,
)
from event_collector.reranking import (
    RAGRerankingAgent,
    RerankMetadata,
)
from event_collector.structuring_runtime import StructuringAttemptOutcome, ensure_article_structured
from event_collector.vector_store import VectorStore


DEFAULT_TOP_N = 3
DEFAULT_REPORTS_DIR = os.path.join("reports", "watchlist_triage")
TRIAGE_PROMPT_VERSION = "watchlist-triage-v1"
REVIEWER_PROMPT_VERSION = "watchlist-reviewer-v1"
logger = logging.getLogger(__name__)


class TriagePriority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TriageConfidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass
class WatchlistRunRequest:
    tickers: list[str]
    top_n: int = DEFAULT_TOP_N
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT
    force_structure: bool = False
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"


@dataclass
class RetrievedTickerEvidence:
    source_id: int
    article_id: int
    title: str
    url: str
    summary: str | None
    excerpt: str
    snippet: str
    published_at: str | None
    rerank_position: int


@dataclass
class StructuredTickerSignal:
    event_id: str
    article_id: int
    source_id: int
    event_type: str
    direction: str
    importance: str
    time_horizon: str
    affected_asset: str
    reasoning: str
    evidence_excerpt: str
    generated_in_run: bool = False


@dataclass
class TickerStructuringAttempt:
    article_id: int
    status: str
    error: str | None
    structuring_model: str | None
    structuring_prompt_version: str
    attempted_at: datetime


@dataclass
class TickerTriageRecord:
    ticker: str
    card: "TriageCard"
    reviewer_finding: "ReviewerFinding"
    evidence: list[RetrievedTickerEvidence]
    structured_signals: list[StructuredTickerSignal]
    structuring_attempts: list[TickerStructuringAttempt]
    rerank_metadata: RerankMetadata | None = None


@dataclass
class HumanReviewInput:
    run_id: str
    ticker: str
    worth_reviewing: bool | None = None
    evidence_specific: bool | None = None
    reasoning_sound: bool | None = None
    missed_important_name: bool | None = None
    follow_through_status: str | None = None
    notes: str = ""
    reviewed_at: datetime | None = None


@dataclass
class FollowupInput:
    run_id: str
    ticker: str
    still_worth_tracking: bool | None = None
    outcome_notes: str = ""
    checked_at: datetime | None = None


class TriageCard(BaseModel):
    ticker: str
    priority: TriagePriority
    confidence: TriageConfidence
    why_now: str
    key_evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    next_action: str

    @field_validator("ticker", "why_now", "next_action")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Required triage fields must be non-empty")
        return cleaned

    @field_validator("key_evidence", "counter_evidence", "missing_questions")
    @classmethod
    def normalize_list_items(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()).strip() for value in values if value and value.strip()]
        return cleaned


class ReviewerFinding(BaseModel):
    evidence_too_generic: bool
    missing_target_specific_signal: bool
    reasoning_jump: bool
    missing_counter_evidence: bool
    next_action_too_vague: bool
    summary: str
    should_flag_human_review: bool

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise ValueError("Reviewer summary must be non-empty")
        return cleaned


@dataclass
class RankedWatchlistItem:
    ticker: str
    priority: TriagePriority
    confidence: TriageConfidence
    rank: int
    should_flag_human_review: bool


@dataclass
class WatchlistRetrievalFailure:
    ticker: str
    status: str
    error_message: str | None = None


@dataclass
class WatchlistRunResult:
    run_id: str
    run_at: datetime
    tickers: list[str]
    top_n: int
    retrieval_top_k: int
    triage_model: str
    reviewer_model: str
    triage_prompt_version: str
    reviewer_prompt_version: str
    items: list[TickerTriageRecord]
    ranked_items: list[RankedWatchlistItem]
    status: str = "completed"
    retrieval_failures: list[WatchlistRetrievalFailure] | None = None


class TriageDecisionRequest(BaseModel):
    ticker: str
    evidence: list[RetrievedTickerEvidence]
    structured_signals: list[StructuredTickerSignal] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, value: str) -> str:
        cleaned = normalize_ticker(value)
        if not cleaned:
            raise ValueError("Ticker must be non-empty")
        return cleaned


class ReviewerRequest(BaseModel):
    ticker: str
    card: TriageCard
    evidence: list[RetrievedTickerEvidence]
    structured_signals: list[StructuredTickerSignal] = Field(default_factory=list)


class TriageLLMClient(Protocol):
    def triage_ticker(self, request: TriageDecisionRequest) -> Any:
        """Return data compatible with TriageCard."""
        ...


class ReviewerLLMClient(Protocol):
    def review_ticker(self, request: ReviewerRequest) -> Any:
        """Return data compatible with ReviewerFinding."""
        ...


class OpenAIWatchlistTriageClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for watchlist triage cards."""

    DEFAULT_MODEL = "deepseek-v4-pro"
    MODEL_ENV_VAR = "OPENAI_TRIAGE_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for watchlist triage"
    REFUSAL_ERROR_PREFIX = "OpenAI refused watchlist triage request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed watchlist triage response"

    def triage_ticker(self, request: TriageDecisionRequest) -> TriageCard:
        return self.parse_structured_output(
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            user_content=_format_triage_request(request),
            response_format=TriageCard,
        )


class DeepSeekWatchlistTriageClient(OpenAIStructuredOutputClient):
    """DeepSeek structured-output adapter for watchlist triage cards."""

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL_ENV_VAR = "DEEPSEEK_BASE_URL"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-pro"
    MODEL_ENV_VAR = "DEEPSEEK_TRIAGE_MODEL"
    FALLBACK_TO_OPENAI_MODEL = False
    MISSING_KEY_MESSAGE = "DEEPSEEK_API_KEY is required for watchlist triage"
    REFUSAL_ERROR_PREFIX = "DeepSeek refused watchlist triage request"
    EMPTY_RESPONSE_MESSAGE = "DeepSeek returned no parsed watchlist triage response"

    def triage_ticker(self, request: TriageDecisionRequest) -> TriageCard:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": DEEPSEEK_TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": _format_triage_request(request)},
            ],
            response_format={"type": "json_object"},
        )
        message = completion.choices[0].message
        content = " ".join((getattr(message, "content", "") or "").split()).strip()
        if not content:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return TriageCard.model_validate(json.loads(content))


class OpenAIWatchlistReviewerClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for watchlist triage review."""

    DEFAULT_MODEL = "deepseek-v4-flash"
    MODEL_ENV_VAR = "OPENAI_REVIEWER_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for watchlist review"
    REFUSAL_ERROR_PREFIX = "OpenAI refused watchlist review request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed watchlist review response"

    def review_ticker(self, request: ReviewerRequest) -> ReviewerFinding:
        return self.parse_structured_output(
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            user_content=_format_reviewer_request(request),
            response_format=ReviewerFinding,
        )


class DeepSeekWatchlistReviewerClient(OpenAIStructuredOutputClient):
    """DeepSeek structured-output adapter for watchlist triage review."""

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL_ENV_VAR = "DEEPSEEK_BASE_URL"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-flash"
    MODEL_ENV_VAR = "DEEPSEEK_REVIEWER_MODEL"
    FALLBACK_TO_OPENAI_MODEL = False
    MISSING_KEY_MESSAGE = "DEEPSEEK_API_KEY is required for watchlist review"
    REFUSAL_ERROR_PREFIX = "DeepSeek refused watchlist review request"
    EMPTY_RESPONSE_MESSAGE = "DeepSeek returned no parsed watchlist review response"

    def review_ticker(self, request: ReviewerRequest) -> ReviewerFinding:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": DEEPSEEK_REVIEWER_SYSTEM_PROMPT},
                {"role": "user", "content": _format_reviewer_request(request)},
            ],
            response_format={"type": "json_object"},
        )
        message = completion.choices[0].message
        content = " ".join((getattr(message, "content", "") or "").split()).strip()
        if not content:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return ReviewerFinding.model_validate(json.loads(content))


TRIAGE_SYSTEM_PROMPT = """
You are the Triage Agent for a financial watchlist triage system.

Your job is not to make a buy or sell decision. Your job is to decide whether this ticker deserves research attention now.

Return one triage card using only the provided evidence and structured signals.

Requirements:
- Use only the supplied evidence. Do not use outside knowledge.
- Focus on what makes the ticker worth reviewing now, not a long investment thesis.
- Prefer ticker-specific evidence over generic macro noise.
- If evidence is weak or mostly generic, use lower priority and lower confidence.
- key_evidence, counter_evidence, and missing_questions should be concise and specific.
- next_action should describe the next research step, not a trade instruction.
""".strip()


DEEPSEEK_TRIAGE_SYSTEM_PROMPT = """
You are the Triage Agent for a financial watchlist triage system.

Return only valid json matching this schema:
{
  "ticker": "NVDA",
  "priority": "High",
  "confidence": "Medium",
  "why_now": "short explanation",
  "key_evidence": ["point 1"],
  "counter_evidence": ["point 2"],
  "missing_questions": ["question 1"],
  "next_action": "next research step"
}

Requirements:
- Use only the supplied evidence. Do not use outside knowledge.
- Focus on what makes the ticker worth reviewing now, not a long investment thesis.
- Prefer ticker-specific evidence over generic macro noise.
- If evidence is weak or mostly generic, use lower priority and lower confidence.
- key_evidence, counter_evidence, and missing_questions should be concise and specific.
- next_action should describe the next research step, not a trade instruction.
""".strip()


REVIEWER_SYSTEM_PROMPT = """
You are the Reviewer Agent for a financial watchlist triage system.

Review the proposed triage card without rewriting it.

Check:
- whether the evidence is too generic
- whether the case lacks ticker-specific signals
- whether the reasoning jumps beyond the evidence
- whether counter-evidence is missing
- whether the next action is too vague

Requirements:
- Do not change the triage card.
- Be critical but concise.
- Set should_flag_human_review to true when the card looks risky, weak, or underspecified.
""".strip()


DEEPSEEK_REVIEWER_SYSTEM_PROMPT = """
You are the Reviewer Agent for a financial watchlist triage system.

Return only valid json matching this schema:
{
  "evidence_too_generic": false,
  "missing_target_specific_signal": false,
  "reasoning_jump": false,
  "missing_counter_evidence": false,
  "next_action_too_vague": false,
  "summary": "short summary",
  "should_flag_human_review": false
}

Requirements:
- Do not change the triage card.
- Be critical but concise.
- Set should_flag_human_review to true when the card looks risky, weak, or underspecified.
""".strip()


class WatchlistTriageAgent:
    """Produces watchlist triage cards from retrieved evidence."""

    def __init__(self, llm_client: TriageLLMClient | None = None):
        self.llm_client = llm_client or DeepSeekWatchlistTriageClient()

    def triage_ticker(self, request: TriageDecisionRequest) -> TriageCard:
        raw_response = self.llm_client.triage_ticker(request)
        card = TriageCard.model_validate(raw_response)
        if normalize_ticker(card.ticker) != normalize_ticker(request.ticker):
            card = card.model_copy(update={"ticker": normalize_ticker(request.ticker)})
        return card


class WatchlistReviewerAgent:
    """Reviews triage cards without changing their ranking decision."""

    def __init__(self, llm_client: ReviewerLLMClient | None = None):
        self.llm_client = llm_client or DeepSeekWatchlistReviewerClient()

    def review_ticker(self, request: ReviewerRequest) -> ReviewerFinding:
        raw_response = self.llm_client.review_ticker(request)
        return ReviewerFinding.model_validate(raw_response)


def run_watchlist(
    request: WatchlistRunRequest,
    vector_store: VectorStore,
    storage: SQLiteNewsStore,
    triage_agent: WatchlistTriageAgent | None = None,
    reviewer_agent: WatchlistReviewerAgent | None = None,
    reranking_agent: RAGRerankingAgent | None = None,
    structuring_agent: EventStructuringAgent | None = None,
    company_kb: SQLiteEntityStore | None = None,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> WatchlistRunResult:
    """Backward-compatible watchlist seam over the deeper research workflow."""
    from event_collector.watchlist_research import WatchlistResearchConfig, run_watchlist_research

    research_run = run_watchlist_research(
        request,
        storage,
        vector_store,
        config=WatchlistResearchConfig(
            auto_batch_threshold=max(1, len(normalize_tickers(request.tickers))),
            auto_batch_size=max(1, len(normalize_tickers(request.tickers))),
            max_concurrency=1,
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
        ),
        triage_agent=triage_agent,
        reviewer_agent=reviewer_agent,
        reranking_agent=reranking_agent,
        structuring_agent=structuring_agent,
        company_kb_provider=company_kb,
    )
    _log_watchlist_structuring_failures(research_run.result.run_id, research_run.result.items)
    return research_run.result


def build_ticker_evidence(
    evidence: list[RetrievedArticleEvidence],
) -> list[RetrievedTickerEvidence]:
    """Convert shared retrieval evidence into watchlist-specific evidence."""
    return [
        RetrievedTickerEvidence(
            source_id=item.id,
            article_id=item.article_id,
            title=item.title,
            url=item.url,
            summary=item.summary,
            excerpt=item.excerpt,
            snippet=item.snippet,
            published_at=item.published_at,
            rerank_position=item.rerank_position,
        )
        for item in evidence
    ]


def build_structured_ticker_signals(
    ticker: str,
    evidence: list[RetrievedTickerEvidence],
    storage: SQLiteNewsStore,
    run_id: str,
    structuring_outcomes_by_article: dict[int, StructuringAttemptOutcome],
    structuring_agent: EventStructuringAgent | None = None,
    force_structure: bool = False,
) -> tuple[list[StructuredTickerSignal], list[TickerStructuringAttempt]]:
    """Collect structured signals from retrieved articles that match the ticker or market context."""
    normalized_ticker = normalize_ticker(ticker)
    signals: list[StructuredTickerSignal] = []
    attempts: list[TickerStructuringAttempt] = []
    attempted_article_ids: set[int] = set()

    for evidence_item in evidence:
        outcome = structuring_outcomes_by_article.get(evidence_item.article_id)
        if outcome is None:
            outcome = ensure_article_structured(
                evidence_item.article_id,
                storage,
                structuring_agent=structuring_agent,
                force_restructure=force_structure,
            )
            structuring_outcomes_by_article[evidence_item.article_id] = outcome

        if outcome.attempted and evidence_item.article_id not in attempted_article_ids:
            attempted_article_ids.add(evidence_item.article_id)
            attempts.append(
                TickerStructuringAttempt(
                    article_id=evidence_item.article_id,
                    status=outcome.status,
                    error=outcome.error,
                    structuring_model=outcome.structuring_model,
                    structuring_prompt_version=outcome.structuring_prompt_version or "unknown",
                    attempted_at=outcome.attempted_at or datetime.now(),
                )
            )
            if outcome.status == "failed":
                logger.warning(
                    "Watchlist structuring failed: run_id=%s ticker=%s article_id=%s error=%s",
                    run_id,
                    normalized_ticker,
                    evidence_item.article_id,
                    outcome.error,
                )

        for event in outcome.events:
            if not _structured_event_matches_ticker(normalized_ticker, event):
                continue
            signals.append(
                StructuredTickerSignal(
                    event_id=event.event_id,
                    article_id=evidence_item.article_id,
                    source_id=evidence_item.source_id,
                    event_type=event.event_type.value,
                    direction=event.direction.value,
                    importance=event.importance.value,
                    time_horizon=event.time_horizon.value,
                    affected_asset=event.affected_asset,
                    reasoning=event.reasoning,
                    evidence_excerpt=event.evidence_excerpt,
                    generated_in_run=outcome.attempted and outcome.status == "success",
                )
            )
    return signals, attempts


def _log_watchlist_structuring_failures(run_id: str, items: list[TickerTriageRecord]) -> None:
    failed_counts: dict[str, int] = {}
    for item in items:
        failed = sum(1 for attempt in item.structuring_attempts if attempt.status == "failed")
        if failed:
            failed_counts[item.ticker] = failed

    if not failed_counts:
        return

    summary = ", ".join(f"{ticker}({count})" for ticker, count in sorted(failed_counts.items()))
    logger.warning(
        "Watchlist run %s completed with %s structuring failure(s) affecting %s ticker(s): %s",
        run_id,
        sum(failed_counts.values()),
        len(failed_counts),
        summary,
    )


def rank_watchlist_items(items: list[TickerTriageRecord]) -> list[RankedWatchlistItem]:
    """Rank all watchlist items by priority, then confidence, then ticker."""
    ordered = sorted(
        items,
        key=lambda item: (
            _priority_rank(item.card.priority),
            _confidence_rank(item.card.confidence),
            normalize_ticker(item.ticker),
        ),
    )
    ranked: list[RankedWatchlistItem] = []
    for index, item in enumerate(ordered, start=1):
        ranked.append(
            RankedWatchlistItem(
                ticker=normalize_ticker(item.ticker),
                priority=item.card.priority,
                confidence=item.card.confidence,
                rank=index,
                should_flag_human_review=item.reviewer_finding.should_flag_human_review,
            )
        )
    return ranked


def write_watchlist_report(
    result: WatchlistRunResult,
    output_dir: str = DEFAULT_REPORTS_DIR,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    """Persist one Markdown watchlist triage report and return its path."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{result.run_at.strftime('%Y-%m-%d_%H%M%S')}_watchlist.md"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            render_watchlist_report(
                result,
                debug_review=debug_review,
                debug_rerank=debug_rerank,
            )
        )
    return path


def render_watchlist_report(
    result: WatchlistRunResult,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    """Render the full watchlist triage report as Markdown."""
    ranking = {item.ticker: item for item in result.ranked_items}
    ordered_records = sorted(result.items, key=lambda item: ranking[normalize_ticker(item.ticker)].rank)
    lines = [
        "# Watchlist Triage Report",
        "",
        f"- Run ID: {result.run_id}",
        f"- Generated at: {result.run_at.isoformat()}",
        f"- Watchlist size: {len(result.tickers)}",
        f"- Top N: {result.top_n}",
        f"- Retrieval top-k: {result.retrieval_top_k}",
        "",
        "## Top Summary",
        "",
        "| Rank | Ticker | Priority | Confidence | Human Review Flag |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ranked in result.ranked_items[: result.top_n]:
        lines.append(
            f"| {ranked.rank} | {ranked.ticker} | {ranked.priority.value} | "
            f"{ranked.confidence.value} | {'yes' if ranked.should_flag_human_review else 'no'} |"
        )

    for item in ordered_records:
        rank = ranking[normalize_ticker(item.ticker)].rank
        lines.extend(
            [
                "",
                f"## {rank}. {normalize_ticker(item.ticker)}",
                "",
                f"- Priority: {item.card.priority.value}",
                f"- Confidence: {item.card.confidence.value}",
                f"- Flag human review: {'yes' if item.reviewer_finding.should_flag_human_review else 'no'}",
                "",
                "### Why Now",
                item.card.why_now,
                "",
                "### Key Evidence",
            ]
        )
        if item.card.key_evidence:
            for evidence_item in item.card.key_evidence:
                lines.append(f"- {evidence_item}")
        else:
            lines.append("- None")

        lines.extend(["", "### Counter Evidence"])
        if item.card.counter_evidence:
            for evidence_item in item.card.counter_evidence:
                lines.append(f"- {evidence_item}")
        else:
            lines.append("- None")

        lines.extend(["", "### Missing Questions"])
        if item.card.missing_questions:
            for question in item.card.missing_questions:
                lines.append(f"- {question}")
        else:
            lines.append("- None")

        lines.extend(
            [
                "",
                "### Next Action",
                item.card.next_action,
                "",
                "### Reviewer",
                item.reviewer_finding.summary,
            ]
        )
        if debug_review:
            lines.extend(
                [
                    "",
                    f"- evidence_too_generic: {str(item.reviewer_finding.evidence_too_generic).lower()}",
                    (
                        "- missing_target_specific_signal: "
                        f"{str(item.reviewer_finding.missing_target_specific_signal).lower()}"
                    ),
                    f"- reasoning_jump: {str(item.reviewer_finding.reasoning_jump).lower()}",
                    (
                        "- missing_counter_evidence: "
                        f"{str(item.reviewer_finding.missing_counter_evidence).lower()}"
                    ),
                    f"- next_action_too_vague: {str(item.reviewer_finding.next_action_too_vague).lower()}",
                ]
            )

        lines.extend(["", "### Evidence"])
        if item.evidence:
            for evidence_item in item.evidence:
                lines.append(
                    f"- [{evidence_item.source_id}] {evidence_item.title} ({evidence_item.url})"
                )
                lines.append(f"  - Snippet: {evidence_item.snippet}")
        else:
            lines.append("- No retrieved evidence")

        if item.structured_signals:
            lines.extend(["", "### Structured Signals"])
            for signal in item.structured_signals:
                lines.append(
                    (
                        f"- [{signal.source_id}] {signal.event_type} / {signal.direction} / "
                        f"{signal.importance} / {signal.time_horizon}: {signal.reasoning}"
                    )
                )

        if debug_rerank and item.rerank_metadata is not None:
            lines.extend(["", "### Rerank Debug"])
            for index, candidate in enumerate(item.rerank_metadata.ranked_candidates, start=1):
                lines.append(f"{index}. Candidate {candidate.candidate_id}: {candidate.reason}")

    return "\n".join(lines).strip() + "\n"


def render_watchlist_summary(
    result: WatchlistRunResult,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    """Render a console summary that includes the ranking and decision basis."""
    ranking = {item.ticker: item for item in result.ranked_items}
    ordered_records = sorted(result.items, key=lambda item: ranking[normalize_ticker(item.ticker)].rank)
    lines = [
        "Watchlist Triage:",
        f"Run ID: {result.run_id}",
        "",
        "Top Watchlist:",
    ]
    for item in ordered_records[: result.top_n]:
        ranked = ranking[normalize_ticker(item.ticker)]
        lines.append(
            f"{ranked.rank}. {ranked.ticker} - {ranked.priority.value} / {ranked.confidence.value}"
            f"{' [review]' if ranked.should_flag_human_review else ''}"
        )
        lines.append(f"   Why now: {item.card.why_now}")

        if item.card.key_evidence:
            lines.append(f"   Key evidence: {item.card.key_evidence[0]}")
            for evidence_item in item.card.key_evidence[1:3]:
                lines.append(f"     - {evidence_item}")
        else:
            lines.append("   Key evidence: None")

        if item.card.counter_evidence:
            lines.append(f"   Counter evidence: {item.card.counter_evidence[0]}")
            for evidence_item in item.card.counter_evidence[1:3]:
                lines.append(f"     - {evidence_item}")

        if item.card.missing_questions:
            lines.append(f"   Missing question: {item.card.missing_questions[0]}")

        lines.append(f"   Next action: {item.card.next_action}")
        lines.append(f"   Reviewer: {item.reviewer_finding.summary}")

        if debug_review:
            lines.append(
                "   Review flags: "
                f"generic={str(item.reviewer_finding.evidence_too_generic).lower()}, "
                f"target_specific_missing={str(item.reviewer_finding.missing_target_specific_signal).lower()}, "
                f"reasoning_jump={str(item.reviewer_finding.reasoning_jump).lower()}, "
                f"counter_evidence_missing={str(item.reviewer_finding.missing_counter_evidence).lower()}, "
                f"next_action_vague={str(item.reviewer_finding.next_action_too_vague).lower()}"
            )

        if item.structured_signals:
            signal = item.structured_signals[0]
            lines.append(
                "   Structured signal: "
                f"{signal.event_type}/{signal.direction}/{signal.importance}/{signal.time_horizon} - "
                f"{signal.reasoning}"
            )

        if debug_rerank and item.rerank_metadata is not None and item.rerank_metadata.ranked_candidates:
            rerank_reason = item.rerank_metadata.ranked_candidates[0]
            lines.append(f"   Rerank: candidate {rerank_reason.candidate_id} kept because {rerank_reason.reason}")

        lines.append("")
    return "\n".join(lines).rstrip()


def normalize_ticker(ticker: str) -> str:
    """Normalize ticker-like input for storage and ranking."""
    return " ".join((ticker or "").split()).strip().upper()


def normalize_tickers(tickers: list[str]) -> list[str]:
    """Normalize, dedupe, and preserve order for requested tickers."""
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        cleaned = normalize_ticker(ticker)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _structured_event_matches_ticker(ticker: str, event: StructuredEvent) -> bool:
    asset = normalize_ticker(event.affected_asset)
    if asset in {"GENERAL MARKET", "MARKET", "BROAD MARKET"}:
        return True
    if asset == ticker:
        return True
    ticker_token = re.sub(r"[^A-Z0-9]+", "", ticker)
    asset_tokens = set(re.findall(r"[A-Z0-9]+", asset))
    return bool(ticker_token and ticker_token in asset_tokens)


def _priority_rank(priority: TriagePriority) -> int:
    return {
        TriagePriority.HIGH: 0,
        TriagePriority.MEDIUM: 1,
        TriagePriority.LOW: 2,
    }[priority]


def _confidence_rank(confidence: TriageConfidence) -> int:
    return {
        TriageConfidence.HIGH: 0,
        TriageConfidence.MEDIUM: 1,
        TriageConfidence.LOW: 2,
    }[confidence]


def _resolve_agent_model(llm_client: Any) -> str:
    return getattr(llm_client, "model", "") or getattr(llm_client, "DEFAULT_MODEL", "") or "unknown"


def _format_triage_request(request: TriageDecisionRequest) -> str:
    evidence_blocks = []
    for evidence in request.evidence:
        evidence_blocks.append(
            (
                f"Source [{evidence.source_id}]\n"
                f"Article ID: {evidence.article_id}\n"
                f"Title: {evidence.title}\n"
                f"URL: {evidence.url}\n"
                f"Published At: {evidence.published_at or 'Unknown'}\n"
                f"Summary:\n{evidence.summary or 'None'}\n"
                f"Excerpt:\n{evidence.excerpt}"
            )
        )

    signal_blocks = []
    for signal in request.structured_signals:
        signal_blocks.append(
            (
                f"- Source [{signal.source_id}] {signal.event_type} / {signal.direction} / "
                f"{signal.importance} / {signal.time_horizon} / Asset: {signal.affected_asset}\n"
                f"  Reasoning: {signal.reasoning}\n"
                f"  Evidence: {signal.evidence_excerpt}"
            )
        )

    return (
        f"Ticker:\n{request.ticker}\n\n"
        f"Retrieved evidence count: {len(request.evidence)}\n"
        f"Structured signal count: {len(request.structured_signals)}\n\n"
        "Structured Signals:\n"
        f"{os.linesep.join(signal_blocks) if signal_blocks else 'None'}\n\n"
        "Retrieved Sources:\n"
        f"{'\n\n'.join(evidence_blocks) if evidence_blocks else 'None'}"
    )


def _format_retrieved_sources(
    evidence: list[RetrievedTickerEvidence],
    structured_signals: list[StructuredTickerSignal],
    ticker: str,
) -> str:
    """Return only the retrieved-sources section used by the reviewer prompt."""
    triage_request = TriageDecisionRequest(
        ticker=ticker,
        evidence=evidence,
        structured_signals=structured_signals,
    )
    marker = "Retrieved Sources:\n"
    formatted = _format_triage_request(triage_request)
    _, _, sources = formatted.partition(marker)
    return sources or "None"


def _format_reviewer_request(request: ReviewerRequest) -> str:
    card = request.card
    return (
        f"Ticker:\n{request.ticker}\n\n"
        "Proposed Triage Card:\n"
        f"- Priority: {card.priority.value}\n"
        f"- Confidence: {card.confidence.value}\n"
        f"- Why now: {card.why_now}\n"
        f"- Key evidence: {card.key_evidence or ['None']}\n"
        f"- Counter evidence: {card.counter_evidence or ['None']}\n"
        f"- Missing questions: {card.missing_questions or ['None']}\n"
        f"- Next action: {card.next_action}\n\n"
        f"Retrieved evidence count: {len(request.evidence)}\n"
        f"Structured signal count: {len(request.structured_signals)}\n\n"
        "Retrieved Sources:\n"
        f"{_format_retrieved_sources(request.evidence, request.structured_signals, request.ticker)}"
    )
