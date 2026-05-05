import pytest
import tempfile
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from event_collector.vector_store import (
    VectorStore,
    ChromaVectorStore,
)
from event_collector.news_storage import NewsArticle


@pytest.fixture
def temp_chroma_dir():
    """Create a temporary directory for ChromaDB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def vector_store(temp_chroma_dir):
    """Create a ChromaVectorStore for testing."""
    store = ChromaVectorStore(persist_dir=temp_chroma_dir)
    yield store
    # Cleanup: close the ChromaDB client
    if hasattr(store, 'client') and store.client:
        store.client = None


def test_vector_store_add_article(vector_store):
    """Test adding an article to the vector store."""
    article = NewsArticle(
        source="news",
        title="Bitcoin Rises",
        description="Bitcoin price increase",
        content="Bitcoin has risen by 10% today due to positive market sentiment.",
        url="https://example.com/bitcoin",
        published_at=datetime.now(),
        summary=None,
    )
    
    article_id = vector_store.add_article(article)
    
    assert article_id is not None


def test_vector_store_search_returns_results(vector_store):
    """Test that searching returns relevant articles."""
    articles = [
        NewsArticle(
            source="news",
            title="Bitcoin Market Analysis",
            description="BTC analysis",
            content="Bitcoin is a decentralized digital currency. Recent analysis shows bullish trends.",
            url="https://example.com/btc1",
            published_at=datetime.now(),
            summary=None,
        ),
        NewsArticle(
            source="news",
            title="Stock Market Update",
            description="Stock update",
            content="The stock market closed up today with major indices gaining.",
            url="https://example.com/stock1",
            published_at=datetime.now(),
            summary=None,
        ),
    ]
    
    for article in articles:
        vector_store.add_article(article)
    
    results = vector_store.search("Bitcoin cryptocurrency", top_k=1)
    
    assert len(results) > 0
    assert "Bitcoin" in results[0]["title"]


def test_vector_store_search_returns_top_k(vector_store):
    """Test that search respects top_k parameter."""
    articles = [
        NewsArticle(
            source="news",
            title=f"Article {i}",
            description=f"Description {i}",
            content=f"This is article number {i} about crypto markets.",
            url=f"https://example.com/article{i}",
            published_at=datetime.now(),
            summary=None,
        )
        for i in range(5)
    ]
    
    for article in articles:
        vector_store.add_article(article)
    
    results = vector_store.search("crypto markets", top_k=3)
    
    assert len(results) <= 3


def test_vector_store_empty_search(vector_store):
    """Test searching an empty vector store."""
    results = vector_store.search("query", top_k=5)
    
    # Should return empty list, not error
    assert isinstance(results, list)


def test_vector_store_article_metadata_preserved(vector_store):
    """Test that article metadata is preserved after indexing."""
    article = NewsArticle(
        source="news",
        title="Market Volatility",
        description="VIX spike",
        content="Market volatility index increased significantly today.",
        url="https://example.com/vix",
        published_at=datetime.now(),
        summary="Summary of market volatility",
    )
    
    vector_store.add_article(article)
    results = vector_store.search("market volatility", top_k=1)
    
    assert len(results) > 0
    retrieved = results[0]
    assert retrieved["source"] == "news"
    assert retrieved["url"] == "https://example.com/vix"
    assert retrieved["summary"] == "Summary of market volatility"
