#!/usr/bin/env python3
"""
News Retrieval Pipeline - Query the stored news and vector index.
Demonstrates semantic retrieval for RAG use cases.
"""

import os
import sys
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from event_collector import SQLiteNewsStore, ChromaVectorStore


def main():
    print("🤖 Financial Agent - News Retrieval Pipeline")
    print("=" * 50)
    print(f"Started at: {datetime.now()}")
    print()

    # Setup storage
    db_path = "news_articles.db"
    chroma_dir = "./chroma_data"

    # Initialize storage
    storage = SQLiteNewsStore(db_path=db_path)
    
    # Initialize vector store
    vector_store = ChromaVectorStore(persist_dir=chroma_dir)

    # Check what's in storage
    total = storage.count_articles()
    print(f"📊 Database has {total} articles")
    
    if total == 0:
        print("\n❌ No articles in database. Run ingest_news.py first!")
        storage.close()
        return

    print()

    # Interactive search loop
    print("Search for news articles (type 'quit' to exit)")
    print("-" * 50)
    print()

    while True:
        query = input("🔍 Search query: ").strip()
        
        if query.lower() in ['quit', 'exit', 'q']:
            break
        
        if not query:
            print("Please enter a search query")
            continue

        print()

        # Semantic search with vector store
        print(f"🔎 Searching for: '{query}'")
        results = vector_store.search(query, top_k=3)

        if not results:
            print("No results found")
        else:
            print(f"\nFound {len(results)} result(s):\n")
            
            for i, result in enumerate(results, 1):
                print(f"{i}. {result.get('title', 'Untitled')}")
                print(f"   Source: {result.get('source', 'Unknown')}")
                print(f"   URL: {result.get('url', 'N/A')}")
                
                if result.get('distance'):
                    relevance = 1 - result['distance']  # Convert distance to relevance
                    relevance_pct = max(0, min(100, int(relevance * 100)))
                    print(f"   Relevance: {relevance_pct}%")
                
                if result.get('summary'):
                    print(f"   Summary: {result['summary']}")
                
                if result.get('content'):
                    content_preview = result['content'][:150]
                    print(f"   Preview: {content_preview}...")
                
                print()

        print("-" * 50)
        print()

    print("\n✅ Retrieval session complete!")
    storage.close()


if __name__ == "__main__":
    main()
