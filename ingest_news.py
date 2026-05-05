#!/usr/bin/env python3
"""
News Ingestion Pipeline - Collects news and stores with semantic indexing.
Demonstrates SQLite persistence + ChromaDB vector store for RAG.
"""

import os
import sys
import json
from datetime import datetime

from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

load_dotenv()

from event_collector import (
    collect_from_all_sources,
    ingest_events_to_storage,
    ManualCollector,
    NewsCollector,
    SQLiteNewsStore,
    ChromaVectorStore,
)


def main():
    print("🤖 Financial Agent - News Ingestion Pipeline")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    # Check environment
    news_key = os.getenv("NEWSAPI_API_KEY")
    print(f"NewsAPI key: {'✅ Set' if news_key else '❌ Not set'}")
    print()

    # Setup storage
    db_path = "news_articles.db"
    chroma_dir = "./chroma_data"
    
    print(f"📁 SQLite DB: {db_path}")
    print(f"📁 ChromaDB: {chroma_dir}")
    print()

    # Initialize storage
    storage = SQLiteNewsStore(db_path=db_path)
    storage.init_db()

    # Initialize vector store
    vector_store = ChromaVectorStore(persist_dir=chroma_dir)

    # Create collectors
    collectors = [
        ManualCollector(),
        NewsCollector(),
    ]

    # Collect events from all sources
    print("🔄 Collecting events from all sources...")
    batch = collect_from_all_sources(collectors)
    print(f"✅ Collected {len(batch.events)} events")
    print()

    # Ingest to storage and vector store
    print("💾 Ingesting to storage...")
    stats = ingest_events_to_storage(batch, storage, vector_store)
    
    print(f"  Total events:  {stats['total_events']}")
    print(f"  Saved:         {stats['saved']}")
    print(f"  Indexed:       {stats['indexed']}")
    print(f"  Skipped:       {stats['skipped']}")
    print()

    # Show stored articles
    print("📰 Stored articles:")
    articles = storage.list_articles()
    for i, article in enumerate(articles[:5], 1):  # Show first 5
        print(f"\n  {i}. {article.title[:70]}...")
        print(f"     Source: {article.source}")
        print(f"     Published: {article.published_at}")

    if len(articles) > 5:
        print(f"\n  ... and {len(articles) - 5} more articles")

    print()

    # Demonstrate vector search
    print("🔍 Vector Search Examples:")
    queries = [
        "Bitcoin cryptocurrency market",
        "market volatility analysis",
        "technology stocks",
    ]

    for query in queries:
        print(f"\n  Query: '{query}'")
        results = vector_store.search(query, top_k=2)
        if results:
            for j, result in enumerate(results, 1):
                title = result.get("title", "Unknown")[:60]
                print(f"    {j}. {title}...")
                if result.get("distance"):
                    print(f"       Distance: {result['distance']:.3f}")
        else:
            print("    No results")

    print()
    print("=" * 50)
    print("✅ Ingestion pipeline complete!")
    print()

    # Summary
    total_articles = storage.count_articles()
    print(f"Total articles in database: {total_articles}")
    print(f"Ready for RAG retrieval!")
    print()

    storage.close()


if __name__ == "__main__":
    main()
