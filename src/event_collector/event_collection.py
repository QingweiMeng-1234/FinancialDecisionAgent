from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import os
import uuid
from typing import Optional

import requests
from event_collector.errors import InvalidEventSourceError, InvalidEventTextError, MissingAPIKeyError
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.vector_store import VectorStore


class EventSource(Enum):
    MANUAL = "manual"
    NEWS = "news"
    API = "api"


@dataclass
class RawEventInput:
    source: str
    raw_text: str
    title: str = ""
    description: str = ""
    url: str = ""
    published_at: Optional[datetime] = None


@dataclass
class Event:
    id: str
    source: EventSource
    raw_text: str
    timestamp: datetime
    title: str = ""
    description: str = ""
    url: str = ""


@dataclass
class EventBatch:
    events: list[Event]
    batch_id: str
    created_at: datetime


class EventSourceCollector(ABC):
    """Abstract base class for collecting RawEventInputs from different sources."""

    @abstractmethod
    def collect(self) -> list[RawEventInput]:
        """Collect raw event inputs from this source."""


class ManualCollector(EventSourceCollector):
    """Collector for manual user input."""

    def collect(self) -> list[RawEventInput]:
        print("Enter market event information (or press Enter to skip):")
        user_input = input("> ").strip()
        if user_input:
            return [RawEventInput(source="manual", raw_text=user_input)]
        return []


class NewsCollector(EventSourceCollector):
    """Collector for news APIs."""

    def __init__(
        self,
        country: str = "us",
        category: str = "business",
        page_size: int = 100,
        endpoint: str = "everything",
        days_back: int = 7,
        page: int = 1,
        sort_by: str = "publishedAt",
        language: str = "en",
        no_cache: bool = True,
    ):
        self.country = country
        self.category = category
        self.page_size = page_size
        self.endpoint = endpoint
        self.days_back = days_back
        self.page = page
        self.sort_by = sort_by
        self.language = language
        self.no_cache = no_cache

    def collect(self) -> list[RawEventInput]:
        api_key = os.getenv("NEWSAPI_API_KEY")
        if not api_key:
            raise MissingAPIKeyError("NEWSAPI_API_KEY is required to fetch news from NewsAPI")

        try:
            if self.endpoint == "top-headlines":
                data = self._fetch_top_headlines(api_key)
            elif self.endpoint == "everything":
                data = self._fetch_recent_everything(api_key)
            else:
                raise ValueError(f"Unsupported NewsAPI endpoint mode: {self.endpoint}")

            return self._build_raw_inputs(data)
        except Exception as exc:
            raise RuntimeError(f"News API error: {exc}")

    def _fetch_top_headlines(self, api_key: str) -> dict:
        return self._get_json(
            "https://newsapi.org/v2/top-headlines",
            {
                "country": self.country,
                "category": self.category,
                "pageSize": self.page_size,
                "page": self.page,
                "apiKey": api_key,
            },
        )

    def _fetch_recent_everything(self, api_key: str) -> dict:
        source_ids = self._fetch_source_ids(api_key)
        if not source_ids:
            raise RuntimeError("No NewsAPI sources available for recent everything search")

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=max(self.days_back, 1))
        return self._get_json(
            "https://newsapi.org/v2/everything",
            {
                "sources": ",".join(source_ids[:20]),
                "from": window_start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "to": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "language": self.language,
                "sortBy": self.sort_by,
                "pageSize": self.page_size,
                "page": self.page,
                "apiKey": api_key,
            },
        )

    def _fetch_source_ids(self, api_key: str) -> list[str]:
        data = self._get_json(
            "https://newsapi.org/v2/top-headlines/sources",
            {
                "category": self.category,
                "country": self.country,
                "language": self.language,
                "apiKey": api_key,
            },
        )
        return [
            source_id
            for source in data.get("sources", [])
            if isinstance(source, dict)
            for source_id in [source.get("id")]
            if source_id
        ]

    def _get_json(self, url: str, params: dict) -> dict:
        headers = {"X-No-Cache": "true"} if self.no_cache else None
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def _build_raw_inputs(self, data: dict) -> list[RawEventInput]:
        raw_inputs = []
        for article in data.get("articles", []):
            title = article.get("title", "")
            description = article.get("description", "")
            content = f"{title}. {description}".strip()
            url_value = article.get("url", "")
            if len(content) >= 10 or url_value:
                raw_inputs.append(
                    RawEventInput(
                        source="news",
                        raw_text=content,
                        title=title,
                        description=description,
                        url=url_value,
                        published_at=datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00"))
                        if article.get("publishedAt")
                        else None,
                    )
                )
        return raw_inputs


class ApiCollector(EventSourceCollector):
    """Collector for market data APIs."""

    def collect(self) -> list[RawEventInput]:
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if not api_key:
            print("ALPHA_VANTAGE_API_KEY not set, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5.",
                )
            ]

        try:
            url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=SPY&apikey={api_key}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "Time Series (Daily)" in data:
                latest_date = max(data["Time Series (Daily)"].keys())
                latest_data = data["Time Series (Daily)"][latest_date]
                close = latest_data.get("4. close", "N/A")
                volume = latest_data.get("5. volume", "N/A")
                content = f"SPY daily close: ${close}, volume: {volume}. Market data from Alpha Vantage API."
                return [RawEventInput(source="api", raw_text=content)]
            raise ValueError("Invalid API response")
        except Exception as exc:
            print(f"Alpha Vantage API error: {exc}, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5.",
                )
            ]


def create_event(raw_input: RawEventInput) -> Event:
    from event_collector.news_ingestion import validate_raw_input

    failure_reason = validate_raw_input(raw_input)
    if failure_reason == "invalid_source":
        raise InvalidEventSourceError(f"Invalid source: {raw_input.source}")
    if failure_reason == "missing_text":
        raise InvalidEventTextError("raw_text or url is required")
    if failure_reason == "short_news_text_without_url":
        raise InvalidEventTextError("news raw_text must be at least 50 characters when no article url is present")
    if failure_reason == "short_text":
        raise InvalidEventTextError("raw_text must be at least 50 characters")

    return Event(
        id=str(uuid.uuid4()),
        source=EventSource(raw_input.source),
        raw_text=raw_input.raw_text,
        timestamp=raw_input.published_at or datetime.now(),
        title=raw_input.title,
        description=raw_input.description,
        url=raw_input.url,
    )


def create_event_batch(events: list[Event]) -> EventBatch:
    return EventBatch(
        events=events,
        batch_id=str(uuid.uuid4()),
        created_at=datetime.now(),
    )


def collect_events_batch(raw_inputs: list[RawEventInput]) -> EventBatch:
    events = []
    for raw_input in raw_inputs:
        try:
            events.append(create_event(raw_input))
        except (InvalidEventSourceError, InvalidEventTextError):
            continue
    return create_event_batch(events)


def collect_from_all_sources(collectors: list[EventSourceCollector]) -> EventBatch:
    return collect_events_batch(collect_raw_inputs_from_sources(collectors))


def collect_raw_inputs_from_sources(collectors: list[EventSourceCollector]) -> list[RawEventInput]:
    all_raw_inputs = []
    for collector in collectors:
        all_raw_inputs.extend(collector.collect())
    return all_raw_inputs


def raw_event_input_to_news_article(raw_input: RawEventInput, url: str = "") -> NewsArticle:
    title = raw_input.raw_text[:100]
    description = raw_input.raw_text[:200]

    return NewsArticle(
        source=raw_input.source,
        title=raw_input.title or title,
        description=raw_input.description or description,
        content=raw_input.raw_text,
        url=url or raw_input.url or f"internal://{raw_input.source}/{datetime.now().timestamp()}",
        published_at=raw_input.published_at or datetime.now(),
        summary=None,
    )


def ingest_events_to_storage(
    batch: EventBatch,
    storage: SQLiteNewsStore,
    vector_store: Optional[VectorStore] = None,
    summarizer=None,
    content_fetcher=None,
    show_progress: bool = False,
) -> dict:
    from event_collector.news_ingestion import ingest_event_batch

    return ingest_event_batch(
        batch.events,
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=content_fetcher,
        show_progress=show_progress,
    ).stats
