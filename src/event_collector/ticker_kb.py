"""Ticker identity knowledge-base loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from event_collector.watchlist_triage import normalize_ticker


_DISALLOWED_ALIAS_TERMS = {
    "ai",
    "artificial intelligence",
    "cloud",
    "chips",
    "chip",
    "software",
    "hardware",
    "infrastructure",
    "platform",
    "enterprise",
    "consumer",
    "mobility",
    "automotive",
}


@dataclass(frozen=True)
class TickerIdentity:
    ticker: str
    company_name: str
    aliases: tuple[str, ...] = ()

    @property
    def all_names(self) -> tuple[str, ...]:
        names: list[str] = []
        seen: set[str] = set()
        for value in (self.ticker, self.company_name, *self.aliases):
            normalized = _normalize_identity_text(value)
            if normalized in seen:
                continue
            seen.add(normalized)
            names.append(value)
        return tuple(names)


def load_ticker_identities(path: str | Path) -> dict[str, TickerIdentity]:
    """Load a repo-local YAML knowledge base of ticker identities."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Ticker KB YAML must contain a top-level mapping")

    unknown = sorted(set(raw) - {"tickers"})
    if unknown:
        raise ValueError(f"Ticker KB YAML contains unsupported top-level fields: {unknown}")

    tickers = raw.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("Ticker KB YAML must contain a non-empty 'tickers' list")

    records: dict[str, TickerIdentity] = {}
    for index, item in enumerate(tickers):
        if not isinstance(item, dict):
            raise ValueError(f"tickers[{index}] must be an object")

        unknown_fields = sorted(set(item) - {"ticker", "company_name", "aliases"})
        if unknown_fields:
            raise ValueError(f"tickers[{index}] contains unsupported fields: {unknown_fields}")

        ticker = normalize_ticker(_require_text(item.get("ticker"), f"tickers[{index}].ticker"))
        company_name = _require_text(item.get("company_name"), f"tickers[{index}].company_name")
        aliases = _validate_aliases(item.get("aliases"), index, ticker, company_name)

        if ticker in records:
            raise ValueError(f"Duplicate ticker in KB YAML: {ticker}")

        records[ticker] = TickerIdentity(
            ticker=ticker,
            company_name=company_name,
            aliases=tuple(aliases),
        )
    return records


def load_ticker_list(path: str | Path) -> list[str]:
    """Load the experiment tickers YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Ticker list YAML must contain a top-level mapping")

    unknown = sorted(set(raw) - {"tickers"})
    if unknown:
        raise ValueError(f"Ticker list YAML contains unsupported top-level fields: {unknown}")

    tickers = raw.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("Ticker list YAML must contain a non-empty 'tickers' list")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(tickers):
        ticker = normalize_ticker(_require_text(value, f"tickers[{index}]"))
        if ticker in seen:
            raise ValueError(f"Duplicate ticker in ticker list YAML: {ticker}")
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def build_expanded_query(identity: TickerIdentity) -> str:
    """Build a single expanded retrieval query from ticker identity fields."""
    return " ".join(identity.all_names)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    return cleaned


def _validate_aliases(
    value: object,
    index: int,
    ticker: str,
    company_name: str,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"tickers[{index}].aliases must be a list when present")

    aliases: list[str] = []
    seen = {_normalize_identity_text(ticker), _normalize_identity_text(company_name)}
    for alias_index, alias_value in enumerate(value):
        alias = _require_text(alias_value, f"tickers[{index}].aliases[{alias_index}]")
        normalized_alias = _normalize_identity_text(alias)
        if normalized_alias in seen:
            raise ValueError(f"tickers[{index}].aliases[{alias_index}] duplicates another identity name")
        lowered = alias.casefold()
        if lowered in _DISALLOWED_ALIAS_TERMS:
            raise ValueError(
                f"tickers[{index}].aliases[{alias_index}] looks like a product, theme, or business-line term"
            )
        seen.add(normalized_alias)
        aliases.append(alias)
    return aliases


def _normalize_identity_text(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return cleaned
