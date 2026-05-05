from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import os
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import requests

# Import storage and vector store classes
from event_collector.news_storage import SQLiteNewsStore, NewsArticle, ArticleRecord
from event_collector.vector_store import VectorStore, ChromaVectorStore
from event_collector.event_structuring import (
    ArticleForStructuring,
    EventDirection,
    EventImportance,
    EventStructuringAgent,
    EventType,
    MissingOpenAIKeyError,
    OpenAIEventStructuringClient,
    StructuredEvent,
    StructuredEventResponse,
    TimeHorizon,
)


class EventSource(Enum):
    MANUAL = "manual"
    NEWS = "news"
    API = "api"


class InvalidEventSourceError(ValueError):
    """Raised when an invalid event source is provided."""
    pass


class InvalidEventTextError(ValueError):
    """Raised when event text is invalid (empty or too short)."""
    pass


class MissingAPIKeyError(ValueError):
    """Raised when a required API key is missing."""
    pass


@dataclass
class RawEventInput:
    source: str
    raw_text: str


@dataclass
class Event:
    id: str
    source: EventSource
    raw_text: str
    timestamp: datetime


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
        pass


class ManualCollector(EventSourceCollector):
    """Collector for manual user input."""
    
    def collect(self) -> list[RawEventInput]:
        """Prompt user for manual input."""
        print("Enter market event information (or press Enter to skip):")
        user_input = input("> ").strip()
        if user_input:
            return [RawEventInput(source="manual", raw_text=user_input)]
        return []


class NewsCollector(EventSourceCollector):
    """Collector for news APIs."""

    def __init__(self, country: str = "us", category: str = "business", page_size: int = 100):
        self.country = country
        self.category = category
        self.page_size = page_size

    def collect(self) -> list[RawEventInput]:
        """Fetch news from NewsAPI."""
        api_key = os.getenv("NEWSAPI_API_KEY")
        if not api_key:
            raise MissingAPIKeyError("NEWSAPI_API_KEY is required to fetch news from NewsAPI")

        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "country": self.country,
                "category": self.category,
                "pageSize": self.page_size,
                "apiKey": api_key,
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            raw_inputs = []
            for article in data.get("articles", []):
                title = article.get("title", "")
                description = article.get("description", "")
                content = f"{title}. {description}".strip()
                if len(content) >= 50:
                    raw_inputs.append(RawEventInput(source="news", raw_text=content))

            return raw_inputs
        except Exception as e:
            raise RuntimeError(f"News API error: {e}")


class ApiCollector(EventSourceCollector):
    """Collector for market data APIs."""
    
    def collect(self) -> list[RawEventInput]:
        """Fetch market data from Alpha Vantage."""
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if not api_key:
            print("ALPHA_VANTAGE_API_KEY not set, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5."
                )
            ]
        
        try:
            # Get SPY data as market indicator
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
            else:
                raise ValueError("Invalid API response")
        except Exception as e:
            print(f"Alpha Vantage API error: {e}, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5."
                )
            ]


def create_event(raw_input: RawEventInput) -> Event:
    """Create an Event from RawEventInput after validation."""
    # Validate source
    if raw_input.source not in ["manual", "news", "api"]:
        raise InvalidEventSourceError(f"Invalid source: {raw_input.source}")

    # Validate raw_text
    if not raw_input.raw_text or len(raw_input.raw_text) < 50:
        raise InvalidEventTextError("raw_text must be at least 50 characters")

    # Create Event
    event_source = EventSource(raw_input.source)
    event_id = str(uuid.uuid4())
    timestamp = datetime.now()

    return Event(
        id=event_id,
        source=event_source,
        raw_text=raw_input.raw_text,
        timestamp=timestamp
    )


def create_event_batch(events: list[Event]) -> EventBatch:
    """Create an EventBatch from a list of Events."""
    batch_id = str(uuid.uuid4())
    created_at = datetime.now()
    return EventBatch(
        events=events,
        batch_id=batch_id,
        created_at=created_at
    )


def collect_events_batch(raw_inputs: list[RawEventInput]) -> EventBatch:
    """Collect Events from a list of RawEventInputs, validating each and creating an EventBatch."""
    events = []
    for raw_input in raw_inputs:
        try:
            event = create_event(raw_input)
            events.append(event)
        except (InvalidEventSourceError, InvalidEventTextError):
            # Skip invalid inputs for batch processing
            continue
    return create_event_batch(events)


def collect_from_all_sources(collectors: list[EventSourceCollector]) -> EventBatch:
    """Collect RawEventInputs from all sources and create an EventBatch."""
    all_raw_inputs = []
    for collector in collectors:
        raw_inputs = collector.collect()
        all_raw_inputs.extend(raw_inputs)
    return collect_events_batch(all_raw_inputs)


def raw_event_input_to_news_article(raw_input: RawEventInput, url: str = "") -> NewsArticle:
    """
    Convert a RawEventInput to a NewsArticle for storage.
    
    Args:
        raw_input: RawEventInput with source and raw_text
        url: Optional URL for the article (if available)
        
    Returns:
        NewsArticle object ready for storage
    """
    # For now, title and description come from raw_text
    # The summarizer subagent can enhance these later
    title = raw_input.raw_text[:100]  # First 100 chars as title
    description = raw_input.raw_text[:200]  # First 200 chars as description
    
    return NewsArticle(
        source=raw_input.source,
        title=title,
        description=description,
        content=raw_input.raw_text,
        url=url or f"internal://{raw_input.source}/{datetime.now().timestamp()}",
        published_at=datetime.now(),
        summary=None,  # Placeholder for summarizer subagent
    )


def ingest_events_to_storage(
    batch: EventBatch,
    storage: SQLiteNewsStore,
    vector_store: Optional[VectorStore] = None,
) -> dict:
    """
    Ingest an EventBatch into SQLite storage and optionally into vector store.
    
    Args:
        batch: EventBatch from collector
        storage: SQLiteNewsStore instance
        vector_store: Optional VectorStore for semantic indexing
        
    Returns:
        Dict with ingestion statistics
    """
    saved_count = 0
    indexed_count = 0
    skipped_count = 0
    
    for event in batch.events:
        # Convert Event to NewsArticle
        article = NewsArticle(
            source=event.source.value,
            title=event.raw_text[:100],
            description=event.raw_text[:200],
            content=event.raw_text,
            url=f"internal://{event.source.value}/{event.id}",
            published_at=event.timestamp,
            summary=None,
        )
        
        # Save to SQLite storage
        article_id = storage.save_article(article)
        
        if article_id is None:
            skipped_count += 1
            continue
        
        saved_count += 1
        
        # Index in vector store if provided
        if vector_store:
            try:
                vector_store.add_article(article)
                indexed_count += 1
            except Exception as e:
                print(f"Warning: Failed to index article {article_id}: {e}")
    
    return {
        "total_events": len(batch.events),
        "saved": saved_count,
        "indexed": indexed_count,
        "skipped": skipped_count,
    }
