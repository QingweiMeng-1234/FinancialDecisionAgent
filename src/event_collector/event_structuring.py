"""LLM-backed event structuring for investment-relevant news signals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field


class EventType(str, Enum):
    MACRO = "Macro"
    COMPANY = "Company"
    SECTOR = "Sector"
    MARKET = "Market"


class EventDirection(str, Enum):
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    NEUTRAL = "Neutral"


class EventImportance(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TimeHorizon(str, Enum):
    SHORT_TERM = "Short-term"
    LONG_TERM = "Long-term"
    BOTH = "Both"


@dataclass
class ArticleForStructuring:
    """Article payload passed to the Event Structuring Agent."""

    article_id: int
    title: str
    description: str
    content: str
    url: str


@dataclass
class StructuredEvent:
    """A normalized market signal derived from an article."""

    event_id: str
    article_id: int
    event_type: EventType
    direction: EventDirection
    importance: EventImportance
    time_horizon: TimeHorizon
    affected_asset: str
    reasoning: str
    evidence_excerpt: str


class StructuredEventDraft(BaseModel):
    event_type: EventType
    direction: EventDirection
    importance: EventImportance
    time_horizon: TimeHorizon
    affected_asset: str = Field(default="General Market")
    reasoning: str
    evidence_excerpt: str


class StructuredEventResponse(BaseModel):
    events: list[StructuredEventDraft]


class StructuringLLMClient(Protocol):
    def extract_events(self, article: ArticleForStructuring) -> Any:
        """Return data compatible with StructuredEventResponse."""
        ...


class MissingOpenAIKeyError(ValueError):
    """Raised when OPENAI_API_KEY is required but missing."""


class OpenAIEventStructuringClient:
    """OpenAI structured-output adapter for article event extraction."""

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, model: str | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingOpenAIKeyError("OPENAI_API_KEY is required for event structuring")

        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL

    def extract_events(self, article: ArticleForStructuring) -> StructuredEventResponse:
        completion = self.client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": STRUCTURING_SYSTEM_PROMPT},
                {"role": "user", "content": _format_article(article)},
            ],
            response_format=StructuredEventResponse,
        )
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError(f"OpenAI refused event structuring request: {message.refusal}")
        if message.parsed is None:
            raise RuntimeError("OpenAI returned no parsed structured events")
        return message.parsed


STRUCTURING_SYSTEM_PROMPT = """
You are the Event Structuring Agent for a financial news system.

Convert article content into zero or more investment-relevant structured events.
Return zero events when the article has no clear market, macro, sector, or company signal.

Boundaries:
- Do not summarize the article.
- Do not aggregate multiple articles.
- Do not recommend BUY, HOLD, or SELL.
- Do not invent facts not supported by the article.
- Use evidence_excerpt to quote or closely paraphrase the source sentence that supports the label.
- Use "General Market" when no specific ticker, company, sector, or asset is identified.
""".strip()


class EventStructuringAgent:
    """Turns article content into durable normalized market signals."""

    def __init__(self, llm_client: StructuringLLMClient | None = None):
        self.llm_client = llm_client or OpenAIEventStructuringClient()

    def structure_article(self, article: ArticleForStructuring) -> list[StructuredEvent]:
        raw_response = self.llm_client.extract_events(article)
        response = StructuredEventResponse.model_validate(raw_response)

        structured_events = []
        for draft in response.events:
            structured_events.append(
                StructuredEvent(
                    event_id=str(uuid.uuid4()),
                    article_id=article.article_id,
                    event_type=draft.event_type,
                    direction=draft.direction,
                    importance=draft.importance,
                    time_horizon=draft.time_horizon,
                    affected_asset=draft.affected_asset.strip() or "General Market",
                    reasoning=draft.reasoning,
                    evidence_excerpt=draft.evidence_excerpt,
                )
            )
        return structured_events


def _format_article(article: ArticleForStructuring) -> str:
    return (
        f"Article ID: {article.article_id}\n"
        f"URL: {article.url}\n"
        f"Title: {article.title}\n"
        f"Description: {article.description}\n"
        f"Content:\n{article.content}"
    )
