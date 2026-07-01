"""Shared retrieval-intent helpers for retrieval-aware workflows."""

from __future__ import annotations

from typing import Literal


RetrievalIntent = Literal["direct", "indirect"]

DEFAULT_RETRIEVAL_INTENT: RetrievalIntent = "direct"
RETRIEVAL_INTENTS: tuple[RetrievalIntent, ...] = ("direct", "indirect")
RETRIEVAL_INTENT_ALIASES: dict[str, RetrievalIntent] = {
    "detail": "direct",
}


def normalize_retrieval_intent(intent: str | None) -> RetrievalIntent:
    if intent is None:
        return DEFAULT_RETRIEVAL_INTENT
    cleaned = " ".join(intent.split()).strip().lower()
    cleaned = RETRIEVAL_INTENT_ALIASES.get(cleaned, cleaned)
    if cleaned not in RETRIEVAL_INTENTS:
        raise ValueError(
            f"retrieval_intent must be one of {', '.join(RETRIEVAL_INTENTS)}; got {intent!r}"
        )
    return cleaned  # type: ignore[return-value]
