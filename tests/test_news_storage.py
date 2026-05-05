import pytest
import tempfile
import os
from datetime import datetime
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from event_collector.news_storage import (
    SQLiteNewsStore,
    NewsArticle,
)


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_news.db")
        yield db_path


@pytest.fixture
def storage(temp_db):
    """Create a fresh SQLiteNewsStore for each test."""
    store = SQLiteNewsStore(db_path=temp_db)
    store.init_db()
    yield store
    # Ensure connection is closed for cleanup
    store.close()


def test_init_db_creates_schema(temp_db):
    """Test that init_db creates the articles table."""
    store = SQLiteNewsStore(db_path=temp_db)
    store.init_db()
    
    # Verify table exists by attempting to insert
    article = NewsArticle(
        source="news",
        title="Test Article",
        description="Test description",
        content="Full article content",
        url="https://example.com/article",
        published_at=datetime.now(),
        summary=None,
    )
    store.save_article(article)
    assert store.count_articles() == 1
    store.close()


def test_save_article(storage):
    """Test saving a news article."""
    article = NewsArticle(
        source="news",
        title="Bitcoin Surges",
        description="Bitcoin rises to new highs",
        content="Full article about bitcoin",
        url="https://example.com/bitcoin",
        published_at=datetime.now(),
        summary=None,
    )
    
    article_id = storage.save_article(article)
    
    assert article_id is not None
    assert storage.count_articles() == 1


def test_get_article(storage):
    """Test retrieving a saved article."""
    article = NewsArticle(
        source="news",
        title="ETH Analysis",
        description="Ethereum technical analysis",
        content="Ethereum is trading at $2000",
        url="https://example.com/eth",
        published_at=datetime.now(),
        summary=None,
    )
    
    article_id = storage.save_article(article)
    retrieved = storage.get_article(article_id)
    
    assert retrieved is not None
    assert retrieved.title == "ETH Analysis"
    assert retrieved.url == "https://example.com/eth"


def test_deduplication_on_url(storage):
    """Test that saving the same URL twice only stores it once."""
    article1 = NewsArticle(
        source="news",
        title="Market Update",
        description="Daily update",
        content="Market content",
        url="https://example.com/market",
        published_at=datetime.now(),
        summary=None,
    )
    
    article2 = NewsArticle(
        source="news",
        title="Market Update (Duplicate)",
        description="Daily update duplicate",
        content="Market content duplicate",
        url="https://example.com/market",  # Same URL
        published_at=datetime.now(),
        summary=None,
    )
    
    storage.save_article(article1)
    storage.save_article(article2)
    
    assert storage.count_articles() == 1


def test_list_articles(storage):
    """Test retrieving all articles."""
    articles = [
        NewsArticle(
            source="news",
            title=f"Article {i}",
            description=f"Description {i}",
            content=f"Content {i}",
            url=f"https://example.com/article{i}",
            published_at=datetime.now(),
            summary=None,
        )
        for i in range(3)
    ]
    
    for article in articles:
        storage.save_article(article)
    
    retrieved = storage.list_articles()
    assert len(retrieved) == 3
    assert all(isinstance(a, NewsArticle) for a in retrieved)


def test_delete_article(storage):
    """Test deleting an article."""
    article = NewsArticle(
        source="news",
        title="To Delete",
        description="Temporary",
        content="Temp content",
        url="https://example.com/delete",
        published_at=datetime.now(),
        summary=None,
    )
    
    article_id = storage.save_article(article)
    assert storage.count_articles() == 1
    
    storage.delete_article(article_id)
    assert storage.count_articles() == 0


def test_search_by_source(storage):
    """Test filtering articles by source."""
    article1 = NewsArticle(
        source="news",
        title="News Article",
        description="From news API",
        content="News content",
        url="https://example.com/news1",
        published_at=datetime.now(),
        summary=None,
    )
    
    article2 = NewsArticle(
        source="api",
        title="API Article",
        description="From market API",
        content="API content",
        url="https://example.com/api1",
        published_at=datetime.now(),
        summary=None,
    )
    
    storage.save_article(article1)
    storage.save_article(article2)
    
    news_articles = storage.list_articles(source="news")
    assert len(news_articles) == 1
    assert news_articles[0].title == "News Article"


def test_article_with_summary_placeholder(storage):
    """Test that articles can store summary (for future summarizer subagent)."""
    article = NewsArticle(
        source="news",
        title="Long Article",
        description="Description",
        content="Very long article content " * 100,
        url="https://example.com/long",
        published_at=datetime.now(),
        summary="This is a placeholder summary to be filled by summarizer.",
    )
    
    article_id = storage.save_article(article)
    retrieved = storage.get_article(article_id)
    
    assert retrieved.summary == "This is a placeholder summary to be filled by summarizer."
