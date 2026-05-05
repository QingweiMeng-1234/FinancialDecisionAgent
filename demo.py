#!/usr/bin/env python3
"""
Demo script for the Event Collector Agent.
Shows how to collect market events from all sources and display the results.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from event_collector import collect_from_all_sources, ManualCollector, NewsCollector

def main():
    print("🤖 Financial Agent - Event Collector Demo")
    print("=" * 50)
    
    # Check for API keys
    news_key = os.getenv("NEWSAPI_API_KEY")
    
    print(f"NewsAPI key: {'✅ Set' if news_key else '❌ Not set'}")
    print()
    
    # Create collectors
    collectors = [
        ManualCollector(),
        NewsCollector(),
    ]
    
    print("Collecting events from all sources...")
    batch = collect_from_all_sources(collectors)
    
    print(f"\n✅ Collected {len(batch.events)} events in batch {batch.batch_id}")
    print(f"Batch created at: {batch.created_at}")
    print()
    
    for i, event in enumerate(batch.events, 1):
        print(f"Event {i}:")
        print(f"  ID: {event.id}")
        print(f"  Source: {event.source.value}")
        print(f"  Timestamp: {event.timestamp}")
        print(f"  Text: {event.raw_text[:100]}{'...' if len(event.raw_text) > 100 else ''}")
        print()

if __name__ == "__main__":
    main()