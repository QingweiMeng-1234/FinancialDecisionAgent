"""
SQLite-backed news article storage with deduplication.
Stores full article text and metadata for RAG retrieval.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
)


@dataclass
class NewsArticle:
    """Represents a news article with full content and metadata."""
    source: str  # 'news', 'api', 'manual'
    title: str
    description: str
    content: str  # Full article text
    url: str
    published_at: datetime
    summary: Optional[str] = None  # Placeholder for summarizer subagent


@dataclass
class ArticleRecord:
    """A stored article with its SQLite identifier."""

    id: int
    article: NewsArticle


class SQLiteNewsStore:
    """Stores news articles in SQLite with deduplication by URL."""
    
    def __init__(self, db_path: str = "news_articles.db"):
        self.db_path = db_path
        self.conn = None
    
    def init_db(self):
        """Initialize the database schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                content TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                published_at TEXT NOT NULL,
                summary TEXT,
                fetched_at TEXT NOT NULL,
                raw_json TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS structured_events (
                event_id TEXT PRIMARY KEY,
                article_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                importance TEXT NOT NULL,
                time_horizon TEXT NOT NULL,
                affected_asset TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                evidence_excerpt TEXT NOT NULL,
                structured_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
        """)
        self.conn.commit()
    
    def save_article(self, article: NewsArticle) -> Optional[int]:
        """
        Save an article to the database.
        Returns the article ID, or None if it already exists (deduplication).
        """
        if not self.conn:
            self.init_db()
        
        fetched_at = datetime.now().isoformat()
        
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO articles 
                (source, title, description, content, url, published_at, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.source,
                    article.title,
                    article.description,
                    article.content,
                    article.url,
                    article.published_at.isoformat(),
                    article.summary,
                    fetched_at,
                )
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # URL already exists (deduplication)
            return None
    
    def get_article(self, article_id: int) -> Optional[NewsArticle]:
        """Retrieve a single article by ID."""
        if not self.conn:
            self.init_db()
        
        cursor = self.conn.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            return None
        
        return self._row_to_article(row)

    def get_article_record(self, article_id: int) -> Optional[ArticleRecord]:
        """Retrieve a stored article together with its database ID."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_article_record(row)
    
    def list_articles(self, source: Optional[str] = None) -> List[NewsArticle]:
        """List all articles, optionally filtered by source."""
        if not self.conn:
            self.init_db()
        
        if source:
            cursor = self.conn.execute(
                "SELECT * FROM articles WHERE source = ? ORDER BY published_at DESC",
                (source,)
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM articles ORDER BY published_at DESC"
            )
        
        rows = cursor.fetchall()
        return [self._row_to_article(row) for row in rows]

    def list_article_records(
        self,
        source: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[ArticleRecord]:
        """List stored articles with SQLite IDs, optionally filtered and limited."""
        if not self.conn:
            self.init_db()

        query = "SELECT * FROM articles"
        params = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY published_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.execute(query, tuple(params))
        return [self._row_to_article_record(row) for row in cursor.fetchall()]

    def list_unstructured_article_records(
        self,
        source: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[ArticleRecord]:
        """List articles that do not yet have derived structured events."""
        if not self.conn:
            self.init_db()

        query = """
            SELECT a.*
            FROM articles a
            LEFT JOIN structured_events se ON se.article_id = a.id
            WHERE se.event_id IS NULL
        """
        params = []
        if source:
            query += " AND a.source = ?"
            params.append(source)
        query += " ORDER BY a.published_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.execute(query, tuple(params))
        return [self._row_to_article_record(row) for row in cursor.fetchall()]

    def save_structured_events(
        self,
        article_id: int,
        events: List[StructuredEvent],
        replace: bool = False,
    ) -> int:
        """Save derived structured events for an article."""
        if not self.conn:
            self.init_db()

        if replace:
            self.delete_structured_events_for_article(article_id)

        structured_at = datetime.now().isoformat()
        saved_count = 0
        for event in events:
            self.conn.execute(
                """
                INSERT INTO structured_events
                (event_id, article_id, event_type, direction, importance, time_horizon,
                 affected_asset, reasoning, evidence_excerpt, structured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    article_id,
                    event.event_type.value,
                    event.direction.value,
                    event.importance.value,
                    event.time_horizon.value,
                    event.affected_asset,
                    event.reasoning,
                    event.evidence_excerpt,
                    structured_at,
                )
            )
            saved_count += 1

        self.conn.commit()
        return saved_count

    def list_structured_events_for_article(self, article_id: int) -> List[StructuredEvent]:
        """List all derived structured events for one stored article."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            "SELECT * FROM structured_events WHERE article_id = ? ORDER BY structured_at, event_id",
            (article_id,)
        )
        return [self._row_to_structured_event(row) for row in cursor.fetchall()]

    def delete_structured_events_for_article(self, article_id: int) -> int:
        """Delete derived structured events for one article."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            "DELETE FROM structured_events WHERE article_id = ?",
            (article_id,)
        )
        self.conn.commit()
        return cursor.rowcount
    
    def delete_article(self, article_id: int) -> bool:
        """Delete an article by ID."""
        if not self.conn:
            self.init_db()

        self.delete_structured_events_for_article(article_id)
        
        cursor = self.conn.execute(
            "DELETE FROM articles WHERE id = ?",
            (article_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0
    
    def count_articles(self) -> int:
        """Get total number of articles."""
        if not self.conn:
            self.init_db()
        
        cursor = self.conn.execute("SELECT COUNT(*) FROM articles")
        return cursor.fetchone()[0]
    
    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def _row_to_article(self, row: sqlite3.Row) -> NewsArticle:
        """Convert a database row to a NewsArticle object."""
        return NewsArticle(
            source=row["source"],
            title=row["title"],
            description=row["description"],
            content=row["content"],
            url=row["url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            summary=row["summary"],
        )

    def _row_to_article_record(self, row: sqlite3.Row) -> ArticleRecord:
        return ArticleRecord(
            id=row["id"],
            article=self._row_to_article(row),
        )

    def _row_to_structured_event(self, row: sqlite3.Row) -> StructuredEvent:
        return StructuredEvent(
            event_id=row["event_id"],
            article_id=row["article_id"],
            event_type=EventType(row["event_type"]),
            direction=EventDirection(row["direction"]),
            importance=EventImportance(row["importance"]),
            time_horizon=TimeHorizon(row["time_horizon"]),
            affected_asset=row["affected_asset"],
            reasoning=row["reasoning"],
            evidence_excerpt=row["evidence_excerpt"],
        )
