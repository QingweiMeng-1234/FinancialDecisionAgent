"""
SQLite-backed news article storage with deduplication.
Stores full article text and metadata for RAG retrieval.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
)

if TYPE_CHECKING:
    from event_collector.watchlist_triage import (
        FollowupInput,
        HumanReviewInput,
        TickerStructuringAttempt,
        WatchlistRunResult,
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
    structured_at: Optional[datetime] = None
    structuring_status: Optional[str] = None
    structuring_error: Optional[str] = None


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
                structured_at TEXT,
                structuring_status TEXT,
                structuring_error TEXT,
                fetched_at TEXT NOT NULL,
                raw_json TEXT
            )
        """)
        self._ensure_column("articles", "structured_at", "TEXT")
        self._ensure_column("articles", "structuring_status", "TEXT")
        self._ensure_column("articles", "structuring_error", "TEXT")
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
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_runs (
                run_id TEXT PRIMARY KEY,
                run_at TEXT NOT NULL,
                watchlist_size INTEGER NOT NULL,
                top_n INTEGER NOT NULL,
                retrieval_top_k INTEGER NOT NULL,
                triage_model TEXT NOT NULL,
                reviewer_model TEXT NOT NULL,
                triage_prompt_version TEXT NOT NULL,
                reviewer_prompt_version TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_run_tickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                rank INTEGER NOT NULL,
                priority TEXT NOT NULL,
                confidence TEXT NOT NULL,
                why_now TEXT NOT NULL,
                next_action TEXT NOT NULL,
                should_flag_human_review INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_ticker_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                article_id INTEGER NOT NULL,
                rerank_position INTEGER NOT NULL,
                used_in_triage INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_ticker_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                event_id TEXT NOT NULL,
                generated_in_run INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY(event_id) REFERENCES structured_events(event_id) ON DELETE CASCADE
            )
        """)
        self._ensure_column("watchlist_ticker_events", "generated_in_run", "INTEGER NOT NULL DEFAULT 0")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_ticker_structuring_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                article_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                structuring_model TEXT,
                structuring_prompt_version TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                UNIQUE(run_id, ticker, article_id),
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_ticker_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                list_type TEXT NOT NULL,
                item_text TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_reviewer_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                evidence_too_generic INTEGER NOT NULL,
                missing_target_specific_signal INTEGER NOT NULL,
                reasoning_jump INTEGER NOT NULL,
                missing_counter_evidence INTEGER NOT NULL,
                next_action_too_vague INTEGER NOT NULL,
                should_flag_human_review INTEGER NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_human_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                worth_reviewing INTEGER,
                evidence_specific INTEGER,
                reasoning_sound INTEGER,
                missed_important_name INTEGER,
                follow_through_status TEXT,
                notes TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                still_worth_tracking INTEGER,
                outcome_notes TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES watchlist_runs(run_id) ON DELETE CASCADE
            )
        """)
        self.conn.commit()

    def _ensure_column(self, table_name: str, column_name: str, column_type: str):
        """Add a missing column for lightweight in-place schema evolution."""
        if not _column_exists(self.conn, table_name, column_name):
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
    
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

    def list_article_records_missing_summary(
        self,
        source: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[ArticleRecord]:
        """List stored articles that do not yet have a generated summary."""
        if not self.conn:
            self.init_db()

        query = """
            SELECT *
            FROM articles
            WHERE (summary IS NULL OR TRIM(summary) = '')
        """
        params = []
        if source:
            query += " AND source = ?"
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
        """List articles that have not yet been through structuring."""
        if not self.conn:
            self.init_db()

        query = """
            SELECT a.*
            FROM articles a
            WHERE (a.structuring_status IS NULL OR TRIM(a.structuring_status) = '')
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

    def get_article_structuring_state(self, article_id: int) -> tuple[Optional[str], Optional[datetime], Optional[str]]:
        """Return the persisted structuring status metadata for one article."""
        if not self.conn:
            self.init_db()

        row = self.conn.execute(
            "SELECT structured_at, structuring_status, structuring_error FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Article {article_id} not found")
        return (
            row["structuring_status"],
            datetime.fromisoformat(row["structured_at"]) if row["structured_at"] else None,
            row["structuring_error"],
        )

    def mark_article_structuring_result(
        self,
        article_id: int,
        status: str,
        error: Optional[str] = None,
        structured_at: Optional[datetime] = None,
    ) -> bool:
        """Persist article-level structuring status independent of event rows."""
        if not self.conn:
            self.init_db()

        normalized_status = " ".join((status or "").split()).strip().lower()
        if normalized_status not in {"success", "failed"}:
            raise ValueError(f"Unsupported structuring status: {status!r}")

        timestamp = (structured_at or datetime.now()).isoformat()
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET structured_at = ?, structuring_status = ?, structuring_error = ?
            WHERE id = ?
            """,
            (
                timestamp,
                normalized_status,
                error.strip() if error else None,
                article_id,
            ),
        )
        self.conn.commit()
        return cursor.rowcount > 0

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
        self.mark_article_structuring_result(article_id, status="success", structured_at=datetime.fromisoformat(structured_at))
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

    def update_article_summary(self, article_id: int, summary: str) -> bool:
        """Persist a generated summary for one article."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            "UPDATE articles SET summary = ? WHERE id = ?",
            (summary, article_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def save_watchlist_run(self, result: "WatchlistRunResult") -> str:
        """Persist one full watchlist triage run and its per-ticker artifacts."""
        if not self.conn:
            self.init_db()

        ranking = {item.ticker: item for item in result.ranked_items}

        self.conn.execute(
            """
            INSERT INTO watchlist_runs
            (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
             reviewer_model, triage_prompt_version, reviewer_prompt_version, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id,
                result.run_at.isoformat(),
                len(result.tickers),
                result.top_n,
                result.retrieval_top_k,
                result.triage_model,
                result.reviewer_model,
                result.triage_prompt_version,
                result.reviewer_prompt_version,
                result.status,
            ),
        )

        for item in result.items:
            ticker = item.ticker
            ranked_item = ranking[ticker]
            self.conn.execute(
                """
                INSERT INTO watchlist_run_tickers
                (run_id, ticker, rank, priority, confidence, why_now, next_action, should_flag_human_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    ticker,
                    ranked_item.rank,
                    item.card.priority.value,
                    item.card.confidence.value,
                    item.card.why_now,
                    item.card.next_action,
                    int(item.reviewer_finding.should_flag_human_review),
                ),
            )

            for evidence in item.evidence:
                self.conn.execute(
                    """
                    INSERT INTO watchlist_ticker_evidence
                    (run_id, ticker, article_id, rerank_position, used_in_triage)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        ticker,
                        evidence.article_id,
                        evidence.rerank_position,
                        1,
                    ),
                )

            for signal in item.structured_signals:
                self.conn.execute(
                    """
                    INSERT INTO watchlist_ticker_events
                    (run_id, ticker, event_id, generated_in_run)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        ticker,
                        signal.event_id,
                        int(signal.generated_in_run),
                    ),
                )

            for attempt in item.structuring_attempts:
                _validate_structuring_attempt(attempt)
                self.conn.execute(
                    """
                    INSERT INTO watchlist_ticker_structuring_attempts
                    (run_id, ticker, article_id, status, error, structuring_model,
                     structuring_prompt_version, attempted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        ticker,
                        attempt.article_id,
                        attempt.status,
                        attempt.error,
                        attempt.structuring_model,
                        attempt.structuring_prompt_version,
                        attempt.attempted_at.isoformat(),
                    ),
                )

            for list_type, values in (
                ("key_evidence", item.card.key_evidence),
                ("counter_evidence", item.card.counter_evidence),
                ("missing_questions", item.card.missing_questions),
            ):
                for value in values:
                    self.conn.execute(
                        """
                        INSERT INTO watchlist_ticker_lists
                        (run_id, ticker, list_type, item_text)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            result.run_id,
                            ticker,
                            list_type,
                            value,
                        ),
                    )

            self.conn.execute(
                """
                INSERT INTO watchlist_reviewer_findings
                (run_id, ticker, evidence_too_generic, missing_target_specific_signal,
                 reasoning_jump, missing_counter_evidence, next_action_too_vague,
                 should_flag_human_review, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    ticker,
                    int(item.reviewer_finding.evidence_too_generic),
                    int(item.reviewer_finding.missing_target_specific_signal),
                    int(item.reviewer_finding.reasoning_jump),
                    int(item.reviewer_finding.missing_counter_evidence),
                    int(item.reviewer_finding.next_action_too_vague),
                    int(item.reviewer_finding.should_flag_human_review),
                    item.reviewer_finding.summary,
                ),
            )

        self.conn.commit()
        return result.run_id

    def fetch_watchlist_run_summary(self, run_id: str) -> Optional[dict]:
        """Fetch a compact summary of one persisted watchlist run for tests and reporting."""
        if not self.conn:
            self.init_db()

        run_row = self.conn.execute(
            "SELECT * FROM watchlist_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not run_row:
            return None

        tickers = self.conn.execute(
            """
            SELECT ticker, rank, priority, confidence, why_now, next_action, should_flag_human_review
            FROM watchlist_run_tickers
            WHERE run_id = ?
            ORDER BY rank, ticker
            """,
            (run_id,),
        ).fetchall()
        reviewer_rows = self.conn.execute(
            "SELECT * FROM watchlist_reviewer_findings WHERE run_id = ? ORDER BY ticker",
            (run_id,),
        ).fetchall()
        evidence_count = self.conn.execute(
            "SELECT COUNT(*) FROM watchlist_ticker_evidence WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        event_count = self.conn.execute(
            "SELECT COUNT(*) FROM watchlist_ticker_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        structuring_attempt_count = self.conn.execute(
            "SELECT COUNT(*) FROM watchlist_ticker_structuring_attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        list_count = self.conn.execute(
            "SELECT COUNT(*) FROM watchlist_ticker_lists WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]

        return {
            "run": dict(run_row),
            "tickers": [dict(row) for row in tickers],
            "reviewer_findings": [dict(row) for row in reviewer_rows],
            "evidence_count": evidence_count,
            "event_count": event_count,
            "structuring_attempt_count": structuring_attempt_count,
            "list_count": list_count,
        }

    def save_watchlist_human_review(self, review: "HumanReviewInput") -> int:
        """Persist one stub human-review record for a watchlist ticker."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            """
            INSERT INTO watchlist_human_reviews
            (run_id, ticker, worth_reviewing, evidence_specific, reasoning_sound,
             missed_important_name, follow_through_status, notes, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.run_id,
                review.ticker,
                _bool_to_sql(review.worth_reviewing),
                _bool_to_sql(review.evidence_specific),
                _bool_to_sql(review.reasoning_sound),
                _bool_to_sql(review.missed_important_name),
                review.follow_through_status,
                review.notes,
                (review.reviewed_at or datetime.now()).isoformat(),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def save_watchlist_followup(self, followup: "FollowupInput") -> int:
        """Persist one stub follow-up record for a watchlist ticker."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            """
            INSERT INTO watchlist_followups
            (run_id, ticker, checked_at, still_worth_tracking, outcome_notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                followup.run_id,
                followup.ticker,
                (followup.checked_at or datetime.now()).isoformat(),
                _bool_to_sql(followup.still_worth_tracking),
                followup.outcome_notes,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid
    
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
            structured_at=datetime.fromisoformat(row["structured_at"]) if row["structured_at"] else None,
            structuring_status=row["structuring_status"],
            structuring_error=row["structuring_error"],
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


def _bool_to_sql(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return int(bool(value))


def _validate_structuring_attempt(attempt: "TickerStructuringAttempt") -> None:
    if attempt.status not in {"success", "failed"}:
        raise ValueError(f"Unsupported structuring attempt status: {attempt.status!r}")
    if attempt.status == "success" and attempt.error is not None:
        raise ValueError("Successful structuring attempts must not include an error")
    if attempt.status == "failed" and not (attempt.error and attempt.error.strip()):
        raise ValueError("Failed structuring attempts must include a non-empty error")


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)
