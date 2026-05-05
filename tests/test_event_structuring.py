import os
import subprocess
import sys
import tempfile
from datetime import datetime

import pytest
from pydantic import ValidationError

from event_collector.event_structuring import (
    ArticleForStructuring,
    EventDirection,
    EventImportance,
    EventStructuringAgent,
    EventType,
    TimeHorizon,
)
from event_collector.news_storage import NewsArticle, SQLiteNewsStore


class FakeStructuringClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def extract_events(self, article):
        self.calls.append(article)
        return self.response


@pytest.fixture
def article():
    return ArticleForStructuring(
        article_id=42,
        title="Apple earnings beat expectations",
        description="Apple reported stronger services growth and resilient iPhone demand.",
        content=(
            "Apple reported stronger-than-expected earnings, led by services growth "
            "and resilient iPhone demand."
        ),
        url="https://example.com/apple",
    )


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        store.init_db()
        yield store
        store.close()


def test_company_article_produces_positive_company_event(article):
    agent = EventStructuringAgent(
        llm_client=FakeStructuringClient(
            {
                "events": [
                    {
                        "event_type": "Company",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Long-term",
                        "affected_asset": "AAPL",
                        "reasoning": "Earnings strength points to durable business performance.",
                        "evidence_excerpt": "Apple reported stronger-than-expected earnings.",
                    }
                ]
            }
        )
    )

    events = agent.structure_article(article)

    assert len(events) == 1
    assert events[0].article_id == 42
    assert events[0].event_id
    assert events[0].event_type == EventType.COMPANY
    assert events[0].direction == EventDirection.POSITIVE
    assert events[0].importance == EventImportance.HIGH
    assert events[0].time_horizon == TimeHorizon.LONG_TERM
    assert events[0].affected_asset == "AAPL"


def test_mixed_article_can_produce_multiple_events(article):
    agent = EventStructuringAgent(
        llm_client=FakeStructuringClient(
            {
                "events": [
                    {
                        "event_type": "Company",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Long-term",
                        "affected_asset": "AAPL",
                        "reasoning": "Company earnings were stronger than expected.",
                        "evidence_excerpt": "Apple reported stronger-than-expected earnings.",
                    },
                    {
                        "event_type": "Macro",
                        "direction": "Negative",
                        "importance": "Medium",
                        "time_horizon": "Short-term",
                        "affected_asset": "General Market",
                        "reasoning": "Higher rates can pressure equity valuations.",
                        "evidence_excerpt": "The Fed signaled rates may stay high for longer.",
                    },
                ]
            }
        )
    )

    events = agent.structure_article(article)

    assert len(events) == 2
    assert {event.event_type for event in events} == {EventType.COMPANY, EventType.MACRO}


def test_irrelevant_article_can_produce_zero_events(article):
    agent = EventStructuringAgent(llm_client=FakeStructuringClient({"events": []}))

    assert agent.structure_article(article) == []


def test_invalid_enum_output_is_rejected(article):
    agent = EventStructuringAgent(
        llm_client=FakeStructuringClient(
            {
                "events": [
                    {
                        "event_type": "Weather",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Long-term",
                        "affected_asset": "General Market",
                        "reasoning": "Invalid event type should not validate.",
                        "evidence_excerpt": "No relevant evidence.",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValidationError):
        agent.structure_article(article)


def test_structured_events_persist_with_evidence(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Apple earnings beat expectations",
            description="Apple reports stronger services growth.",
            content="Apple reported stronger-than-expected earnings.",
            url="https://example.com/apple",
            published_at=datetime.now(),
        )
    )
    agent = EventStructuringAgent(
        llm_client=FakeStructuringClient(
            {
                "events": [
                    {
                        "event_type": "Company",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Long-term",
                        "affected_asset": "AAPL",
                        "reasoning": "Earnings beat expectations.",
                        "evidence_excerpt": "Apple reported stronger-than-expected earnings.",
                    }
                ]
            }
        )
    )

    record = storage.get_article_record(article_id)
    events = agent.structure_article(
        ArticleForStructuring(
            article_id=record.id,
            title=record.article.title,
            description=record.article.description,
            content=record.article.content,
            url=record.article.url,
        )
    )
    storage.save_structured_events(article_id, events)

    stored_events = storage.list_structured_events_for_article(article_id)
    assert len(stored_events) == 1
    assert stored_events[0].article_id == article_id
    assert stored_events[0].evidence_excerpt == "Apple reported stronger-than-expected earnings."


def test_batch_script_skips_existing_events_unless_force(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Market update",
            description="Market update",
            content="The stock market rallied after inflation cooled more than expected.",
            url="https://example.com/market",
            published_at=datetime.now(),
        )
    )
    existing_event = EventStructuringAgent(
        llm_client=FakeStructuringClient(
            {
                "events": [
                    {
                        "event_type": "Market",
                        "direction": "Positive",
                        "importance": "Medium",
                        "time_horizon": "Short-term",
                        "affected_asset": "General Market",
                        "reasoning": "Cooling inflation supported stocks.",
                        "evidence_excerpt": "The stock market rallied after inflation cooled.",
                    }
                ]
            }
        )
    ).structure_article(
        ArticleForStructuring(
            article_id=article_id,
            title="Market update",
            description="Market update",
            content="The stock market rallied after inflation cooled more than expected.",
            url="https://example.com/market",
        )
    )
    storage.save_structured_events(article_id, existing_event)

    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "structure_events.py",
            "--db-path",
            storage.db_path,
        ],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Skipped:            1" in result.stdout
    assert "Articles processed: 0" in result.stdout
    assert len(storage.list_structured_events_for_article(article_id)) == 1

    unstructured = storage.list_unstructured_article_records()
    forced = storage.list_article_records()
    assert unstructured == []
    assert len(forced) == 1
