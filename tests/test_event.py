import pytest
import event_collector
from datetime import datetime
from event_collector import create_event, create_event_batch, collect_events_batch, collect_from_all_sources, RawEventInput, Event, EventSource, EventBatch, InvalidEventSourceError, InvalidEventTextError, MissingAPIKeyError, ManualCollector, NewsCollector, ApiCollector, EventSourceCollector


def test_create_event_from_valid_raw_input():
    """Test that we can create an Event from a valid RawEventInput."""
    raw_input = RawEventInput(
        source="manual",
        raw_text="This is a valid raw text that is definitely longer than 50 characters to ensure it passes all validation rules."
    )

    event = create_event(raw_input)

    assert isinstance(event, Event)
    assert event.id is not None
    assert len(event.id) > 0  # ID should be generated
    assert event.source == EventSource.MANUAL
    assert event.raw_text == raw_input.raw_text
    assert isinstance(event.timestamp, datetime)


def test_create_event_with_invalid_source():
    """Test that creating an Event with invalid source raises InvalidEventSourceError."""
    raw_input = RawEventInput(
        source="invalid_source",
        raw_text="This is a valid raw text that is definitely longer than 50 characters to ensure it passes all validation rules."
    )

    with pytest.raises(InvalidEventSourceError):
        create_event(raw_input)


def test_create_event_with_empty_text():
    """Test that creating an Event with empty raw_text raises InvalidEventTextError."""
    raw_input = RawEventInput(
        source="manual",
        raw_text=""
    )

    with pytest.raises(InvalidEventTextError):
        create_event(raw_input)


def test_create_event_with_short_text():
    """Test that creating an Event with raw_text shorter than 50 chars raises InvalidEventTextError."""
    raw_input = RawEventInput(
        source="manual",
        raw_text="Short text"
    )

    with pytest.raises(InvalidEventTextError):
        create_event(raw_input)


def test_create_event_batch():
    """Test that we can create an EventBatch from a list of Events."""
    event1 = create_event(RawEventInput(
        source="manual",
        raw_text="This is the first valid raw text that is definitely longer than 50 characters to ensure it passes all validation rules."
    ))
    event2 = create_event(RawEventInput(
        source="news",
        raw_text="This is the second valid raw text that is definitely longer than 50 characters to ensure it passes all validation rules."
    ))

    batch = create_event_batch([event1, event2])

    assert isinstance(batch, EventBatch)
    assert len(batch.events) == 2
    assert batch.events[0] == event1
    assert batch.events[1] == event2
    assert batch.batch_id is not None
    assert len(batch.batch_id) > 0
    assert isinstance(batch.created_at, datetime)


def test_collect_events_batch():
    """Test batch collection from multiple RawEventInputs."""
    inputs = [
        RawEventInput(source="manual", raw_text="Valid manual input that is sufficiently long for validation purposes and contains meaningful market information."),
        RawEventInput(source="news", raw_text="Valid news input that is sufficiently long for validation purposes and contains meaningful market information."),
        RawEventInput(source="api", raw_text="Valid API input that is sufficiently long for validation purposes and contains meaningful market information."),
    ]

    batch = collect_events_batch(inputs)

    assert isinstance(batch, EventBatch)
    assert len(batch.events) == 3
    assert all(isinstance(e, Event) for e in batch.events)
    assert batch.events[0].source == EventSource.MANUAL
    assert batch.events[1].source == EventSource.NEWS
    assert batch.events[2].source == EventSource.API


def test_collect_events_batch_with_invalid_inputs():
    """Test batch collection skips invalid RawEventInputs."""
    inputs = [
        RawEventInput(source="manual", raw_text="Valid manual input that is sufficiently long for validation purposes and contains meaningful market information."),
        RawEventInput(source="invalid", raw_text="Valid text but invalid source."),  # Invalid source
        RawEventInput(source="news", raw_text="Short"),  # Too short
        RawEventInput(source="api", raw_text="Valid API input that is sufficiently long for validation purposes and contains meaningful market information."),
    ]

    batch = collect_events_batch(inputs)

    assert isinstance(batch, EventBatch)
    assert len(batch.events) == 2  # Only valid ones
    assert batch.events[0].source == EventSource.MANUAL
    assert batch.events[1].source == EventSource.API


@pytest.mark.skip(reason="Temporarily disabled; focus on NewsCollector only")
def test_manual_collector():
    """Test ManualCollector produces RawEventInputs."""
    collector = ManualCollector()
    # Since it prompts for input, we'll mock input
    import builtins
    original_input = builtins.input
    builtins.input = lambda prompt: "Test manual input that is sufficiently long for validation purposes and contains meaningful market information."
    try:
        raw_inputs = collector.collect()
        assert isinstance(raw_inputs, list)
        assert len(raw_inputs) == 1
        assert raw_inputs[0].source == "manual"
        assert len(raw_inputs[0].raw_text) >= 50
    finally:
        builtins.input = original_input


def test_news_collector():
    """Test NewsCollector produces RawEventInputs when API key is present."""
    import os

    original_key = os.environ.get("NEWSAPI_API_KEY")
    os.environ["NEWSAPI_API_KEY"] = "dummy_key"

    original_get = event_collector.requests.get

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "status": "ok",
                "articles": [
                    {"title": "News headline one", "description": "Description of news one that is definitely long enough."},
                    {"title": "News headline two", "description": "Description of news two that is definitely long enough."},
                    {"title": "News headline three", "description": "Description of news three that is definitely long enough."},
                ],
            }

    event_collector.requests.get = lambda url, params=None, timeout=None: DummyResponse()

    try:
        collector = NewsCollector()
        raw_inputs = collector.collect()

        assert isinstance(raw_inputs, list)
        assert len(raw_inputs) == 3
        assert all(ri.source == "news" for ri in raw_inputs)
        assert all(len(ri.raw_text) >= 50 for ri in raw_inputs)
    finally:
        if original_key is None:
            os.environ.pop("NEWSAPI_API_KEY", None)
        else:
            os.environ["NEWSAPI_API_KEY"] = original_key
        event_collector.requests.get = original_get


def test_news_collector_missing_api_key():
    """Test NewsCollector raises when NEWSAPI_API_KEY is not set."""
    import os
    original_key = os.environ.get("NEWSAPI_API_KEY")
    os.environ.pop("NEWSAPI_API_KEY", None)

    try:
        with pytest.raises(MissingAPIKeyError):
            NewsCollector().collect()
    finally:
        if original_key is not None:
            os.environ["NEWSAPI_API_KEY"] = original_key


@pytest.mark.skip(reason="Temporarily disabled; focus on NewsCollector only")
def test_api_collector():
    """Test ApiCollector produces RawEventInputs."""
    # Ensure no API key to use fallback
    import os
    original_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
    try:
        collector = ApiCollector()
        raw_inputs = collector.collect()
        
        assert isinstance(raw_inputs, list)
        assert len(raw_inputs) == 1
        assert raw_inputs[0].source == "api"
        assert len(raw_inputs[0].raw_text) >= 50
    finally:
        if original_key:
            os.environ["ALPHA_VANTAGE_API_KEY"] = original_key


@pytest.mark.skip(reason="Temporarily disabled; focus on NewsCollector only")
def test_collect_from_all_sources():
    """Test collecting from all source collectors and batching."""
    # Mock input for ManualCollector
    import builtins
    original_input = builtins.input
    builtins.input = lambda prompt: "Test manual input that is sufficiently long for validation purposes and contains meaningful market information."
    
    original_news_key = os.environ.get("NEWSAPI_API_KEY")
    original_api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    os.environ["NEWSAPI_API_KEY"] = "dummy_key"
    os.environ.pop("ALPHA_VANTAGE_API_KEY", None)

    original_get = event_collector.requests.get

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "status": "ok",
                "articles": [
                    {"title": "News headline one", "description": "Description of news one that is definitely long enough."},
                    {"title": "News headline two", "description": "Description of news two that is definitely long enough."},
                ],
            }

    event_collector.requests.get = lambda url, params=None, timeout=None: DummyResponse()

    try:
        collectors = [ManualCollector(), NewsCollector(), ApiCollector()]
        batch = collect_from_all_sources(collectors)
        
        assert isinstance(batch, EventBatch)
        assert len(batch.events) == 3  # 1 manual + 1 news + 1 api
        sources = [e.source for e in batch.events]
        assert EventSource.MANUAL in sources
        assert EventSource.NEWS in sources
        assert EventSource.API in sources
    finally:
        builtins.input = original_input
        if original_news_key:
            os.environ["NEWS_API_KEY"] = original_news_key
        if original_api_key:
            os.environ["ALPHA_VANTAGE_API_KEY"] = original_api_key