"""Shared service-side defaults for MCP and CLI entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
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
    )


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default
