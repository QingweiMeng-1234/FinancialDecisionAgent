"""Shared service-side defaults for MCP and CLI entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


DEFAULT_SERVICE_DEFAULTS_PATH = "config/financial_agent_service_defaults.json"


@dataclass(frozen=True)
class ServiceDefaults:
    query_top_k: int = 3
    retrieval_top_k: int = 5
    recommendation_top_k: int = 3
    watchlist_top_n: int = 3
    watchlist_retrieval_top_k: int = 5
    query_lookback_days: int = 30


def load_service_defaults(path: str = DEFAULT_SERVICE_DEFAULTS_PATH) -> ServiceDefaults:
    defaults_path = Path(path)
    if not defaults_path.exists():
        return ServiceDefaults()

    payload = json.loads(defaults_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return ServiceDefaults()

    return ServiceDefaults(
        query_top_k=_coerce_positive_int(payload.get("query_top_k"), ServiceDefaults.query_top_k),
        retrieval_top_k=_coerce_positive_int(payload.get("retrieval_top_k"), ServiceDefaults.retrieval_top_k),
        recommendation_top_k=_coerce_positive_int(
            payload.get("recommendation_top_k"),
            ServiceDefaults.recommendation_top_k,
        ),
        watchlist_top_n=_coerce_positive_int(payload.get("watchlist_top_n"), ServiceDefaults.watchlist_top_n),
        watchlist_retrieval_top_k=_coerce_positive_int(
            payload.get("watchlist_retrieval_top_k"),
            ServiceDefaults.watchlist_retrieval_top_k,
        ),
        query_lookback_days=_coerce_positive_int(
            payload.get("query_lookback_days"), ServiceDefaults.query_lookback_days
        ),
    )


def resolve_query_time_window(
    defaults: ServiceDefaults,
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    latest_at: str | None = None,
    lookback_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, str | int | None]:
    """Return a finite UTC window unless a caller supplied start/end bounds."""

    if start_at is not None or end_at is not None:
        return {
            "start_at": start_at,
            "end_at": end_at,
            "latest_at": latest_at,
            "lookback_days": lookback_days,
        }
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("query window clock must be UTC-aware")
    return {
        "start_at": None,
        "end_at": None,
        "latest_at": latest_at or anchor.astimezone(timezone.utc).isoformat(),
        "lookback_days": defaults.query_lookback_days if lookback_days is None else lookback_days,
    }


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default
