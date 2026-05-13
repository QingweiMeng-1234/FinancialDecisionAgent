"""Runtime helpers for lazy article structuring on retrieval hits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from event_collector.event_structuring import (
    STRUCTURING_PROMPT_VERSION,
    ArticleForStructuring,
    EventStructuringAgent,
    StructuredEvent,
)
from event_collector.news_storage import SQLiteNewsStore


@dataclass
class StructuringAttemptOutcome:
    attempted: bool
    status: str
    events: list[StructuredEvent]
    structuring_model: str | None = None
    structuring_prompt_version: str | None = None
    error: str | None = None
    attempted_at: datetime | None = None


def ensure_article_structured(
    article_id: int,
    storage: SQLiteNewsStore,
    structuring_agent: EventStructuringAgent | None = None,
    force_restructure: bool = False,
    on_failure: Callable[[int, Exception], None] | None = None,
) -> StructuringAttemptOutcome:
    """
    Ensure one article has cached structuring output.

    Success with zero events is cached as a completed structuring pass.
    Failed attempts are recorded and retried on the next retrieval hit.
    """
    status, _, _ = storage.get_article_structuring_state(article_id)
    if status == "success" and not force_restructure:
        return StructuringAttemptOutcome(
            attempted=False,
            status="reused_cache",
            events=storage.list_structured_events_for_article(article_id),
        )

    agent = structuring_agent or EventStructuringAgent()
    attempted_at = datetime.now()
    structuring_model = _resolve_structuring_model(agent)
    article_record = storage.get_article_record(article_id)
    if article_record is None:
        raise ValueError(f"Article {article_id} not found in storage")

    article = ArticleForStructuring(
        article_id=article_record.id,
        title=article_record.article.title,
        description=article_record.article.description,
        content=article_record.article.content,
        url=article_record.article.url,
    )

    try:
        events = agent.structure_article(article)
        storage.save_structured_events(article_id, events, replace=force_restructure)
        return StructuringAttemptOutcome(
            attempted=True,
            status="success",
            events=events,
            structuring_model=structuring_model,
            structuring_prompt_version=STRUCTURING_PROMPT_VERSION,
            attempted_at=attempted_at,
        )
    except Exception as exc:
        storage.mark_article_structuring_result(article_id, status="failed", error=str(exc))
        if on_failure is not None:
            on_failure(article_id, exc)
        return StructuringAttemptOutcome(
            attempted=True,
            status="failed",
            events=[],
            structuring_model=structuring_model,
            structuring_prompt_version=STRUCTURING_PROMPT_VERSION,
            error=str(exc),
            attempted_at=attempted_at,
        )


def _resolve_structuring_model(agent: EventStructuringAgent) -> str | None:
    llm_client = getattr(agent, "llm_client", None)
    for candidate in (llm_client, agent):
        model = getattr(candidate, "model", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None
