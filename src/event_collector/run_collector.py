#!/usr/bin/env python3
"""
Production runner for the Event Collector Agent.
Collects events from all sources and outputs the EventBatch.
"""

import os
import sys
import json
from datetime import datetime

from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

load_dotenv()

from event_collector import collect_from_all_sources, ManualCollector, NewsCollector


def main():
    print("🤖 Financial Agent - Event Collector Runner")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    # Check environment
    news_key = os.getenv("NEWSAPI_API_KEY")

    print(f"NewsAPI key: {'✅ Set' if news_key else '❌ Not set'}")
    print()

    # Create collectors
    collectors = [
        ManualCollector(),
        NewsCollector(),
    ]

    # Collect events
    print("Collecting events from all sources...")
    batch = collect_from_all_sources(collectors)

    print(f"✅ Collected {len(batch.events)} events in batch {batch.batch_id}")
    print(f"Batch created at: {batch.created_at}")
    print()

    # Output batch as JSON (for next agent or logging)
    batch_json = {
        "batch_id": batch.batch_id,
        "created_at": batch.created_at.isoformat(),
        "events": [
            {
                "id": event.id,
                "source": event.source.value,
                "raw_text": event.raw_text,
                "timestamp": event.timestamp.isoformat(),
            }
            for event in batch.events
        ]
    }

    print("Batch JSON output:")
    print(json.dumps(batch_json, indent=2))

    # In production, you might:
    # - Send to Event Structuring Agent
    # - Save to database
    # - Log to monitoring system

    print()
    print("Event Collector run complete.")


if __name__ == "__main__":
    main()