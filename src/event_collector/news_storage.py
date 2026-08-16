"""
SQLite-backed news article storage with deduplication.
Stores full article text and metadata for RAG retrieval.
"""

import hashlib
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    """Represents a news article with canonical content and metadata."""
    source: str  # 'news', 'api', 'manual'
    title: str
    description: str
    published_at: datetime
    content: str = ""
    url: str = ""
    summary: Optional[str] = None
    original_url: Optional[str] = None
    normalized_url: Optional[str] = None
    canonical_url: Optional[str] = None
    content_path: Optional[str] = None
    content_sha256: Optional[str] = None
    fetched_at: Optional[datetime] = None
    content_status: str = "pending"
    index_status: str = "pending"
    summary_status: str = "pending"
    publisher_source_id: Optional[str] = None
    publisher_source_name: Optional[str] = None
    story_group_id: Optional[int] = None
    story_dedupe_method: Optional[str] = None
    active_content_sha256: Optional[str] = None
    summary_content_sha256: Optional[str] = None
    indexed_content_sha256: Optional[str] = None
    content_validation_status: str = "pending"
    content_validation_reason: Optional[str] = None
    content_validator_version: Optional[str] = None
    final_response_url: Optional[str] = None
    response_status_code: Optional[int] = None
    response_content_type: Optional[str] = None
    extractor_version: Optional[str] = None
    extracted_char_count: Optional[int] = None
    quarantined_at: Optional[datetime] = None
    quarantine_reason: Optional[str] = None
    source_published_at: Optional[datetime] = None
    published_at_provenance: str = "source_metadata"

    def __post_init__(self):
        if self.published_at_provenance == "source_metadata" and self.source_published_at is None:
            self.source_published_at = self.published_at
        elif self.published_at_provenance == "unknown":
            self.source_published_at = None
        if not self.original_url:
            self.original_url = self.url or ""
        if not self.normalized_url and self.original_url:
            self.normalized_url = normalize_url(self.original_url)
        if not self.url:
            self.url = self.canonical_url or self.original_url or ""


@dataclass
class ArticleRecord:
    """A stored article with its SQLite identifier."""

    id: int
    article: NewsArticle
    structured_at: Optional[datetime] = None
    structuring_status: Optional[str] = None
    structuring_error: Optional[str] = None
    content_status: str = "pending"
    index_status: str = "pending"
    summary_status: str = "pending"


class SQLiteNewsStore:
    """Stores news articles in SQLite with deduplication by URL."""
    
    def __init__(self, db_path: str = "news_articles.db", content_dir: Optional[str] = None):
        self.db_path = db_path
        base_dir = os.path.dirname(os.path.abspath(db_path)) or os.getcwd()
        self.content_dir = os.path.abspath(content_dir or os.path.join(base_dir, "data", "articles"))
        self.conn = None
    
    def init_db(self):
        """Initialize the database schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                content TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                published_at TEXT NOT NULL,
                source_published_at TEXT,
                published_at_provenance TEXT NOT NULL DEFAULT 'legacy_unverified',
                summary TEXT,
                original_url TEXT,
                normalized_url TEXT,
                canonical_url TEXT,
                content_path TEXT,
                content_sha256 TEXT,
                content_status TEXT NOT NULL DEFAULT 'pending',
                index_status TEXT NOT NULL DEFAULT 'pending',
                summary_status TEXT NOT NULL DEFAULT 'pending',
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
        self._ensure_column("articles", "original_url", "TEXT")
        self._ensure_column("articles", "normalized_url", "TEXT")
        self._ensure_column("articles", "canonical_url", "TEXT")
        self._ensure_column("articles", "content_path", "TEXT")
        self._ensure_column("articles", "content_sha256", "TEXT")
        self._ensure_column("articles", "source_published_at", "TEXT")
        self._ensure_column(
            "articles", "published_at_provenance", "TEXT NOT NULL DEFAULT 'legacy_unverified'"
        )
        self._ensure_column("articles", "content_status", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("articles", "index_status", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("articles", "summary_status", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("articles", "publisher_source_id", "TEXT")
        self._ensure_column("articles", "publisher_source_name", "TEXT")
        self._ensure_column("articles", "story_group_id", "INTEGER")
        self._ensure_column("articles", "story_dedupe_method", "TEXT")
        self._ensure_column("articles", "active_content_sha256", "TEXT")
        self._ensure_column("articles", "summary_content_sha256", "TEXT")
        self._ensure_column("articles", "indexed_content_sha256", "TEXT")
        self._ensure_column("articles", "content_validation_status", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("articles", "content_validation_reason", "TEXT")
        self._ensure_column("articles", "content_validator_version", "TEXT")
        self._ensure_column("articles", "final_response_url", "TEXT")
        self._ensure_column("articles", "response_status_code", "INTEGER")
        self._ensure_column("articles", "response_content_type", "TEXT")
        self._ensure_column("articles", "extractor_version", "TEXT")
        self._ensure_column("articles", "extracted_char_count", "INTEGER")
        self._ensure_column("articles", "quarantined_at", "TEXT")
        self._ensure_column("articles", "quarantine_reason", "TEXT")
        self._ensure_column("articles", "quarantine_source_relpath", "TEXT")
        self._ensure_column("articles", "quarantine_expected_sha256", "TEXT")
        self._ensure_column("articles", "quarantine_observed_sha256", "TEXT")
        self._ensure_column("articles", "quarantine_plan_id", "TEXT")
        self._ensure_column("articles", "last_content_attempt_status", "TEXT")
        self._ensure_column("articles", "last_content_attempt_reason", "TEXT")
        self._ensure_column("articles", "last_content_attempt_at", "TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_normalized_url ON articles(normalized_url)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_canonical_url ON articles(canonical_url)")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_articles_normalized_url "
            "ON articles(normalized_url) WHERE normalized_url IS NOT NULL AND normalized_url != ''"
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_articles_canonical_url "
            "ON articles(canonical_url) WHERE canonical_url IS NOT NULL AND canonical_url != ''"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS story_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story_key TEXT UNIQUE NOT NULL,
                normalized_title TEXT NOT NULL,
                publication_date TEXT,
                representative_article_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(representative_article_id) REFERENCES articles(id) ON DELETE SET NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_url_aliases (
                normalized_url TEXT PRIMARY KEY,
                article_id INTEGER NOT NULL,
                original_url TEXT NOT NULL,
                alias_kind TEXT NOT NULL,
                publisher_source_id TEXT,
                publisher_source_name TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_article_url_aliases_article_id ON article_url_aliases(article_id)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_identity_merges (
                retired_article_id INTEGER PRIMARY KEY,
                survivor_article_id INTEGER NOT NULL,
                canonical_url TEXT NOT NULL,
                retired_content_path TEXT,
                vector_cleanup_status TEXT NOT NULL DEFAULT 'pending',
                content_cleanup_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(survivor_article_id) REFERENCES articles(id) ON DELETE RESTRICT
            )
            """
        )
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
                report_path TEXT,
                status TEXT NOT NULL
            )
        """)
        self._ensure_column("watchlist_runs", "report_path", "TEXT")
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
        Save an article without ever exposing a ready row before its file exists.

        This compatibility API treats supplied content as the canonical payload.
        New ingestion code should prefer create_or_get_article_reference() followed
        by update_article_content(), which carries the publisher validation data.
        Returns the article ID, or None if it already exists (deduplication).
        """
        if not self.conn:
            self.init_db()
        
        fetched_at = (article.fetched_at or datetime.now()).isoformat()
        original_url = article.original_url or article.url
        normalized_url = article.normalized_url or normalize_url(original_url)
        canonical_url = article.canonical_url or ""
        if self.find_article_id_by_url(canonical_url or None, normalized_url):
            return None

        content_sha256 = compute_content_sha256(article.content) if article.content else None
        requested_content_status = normalize_processing_status(article.content_status)
        if not article.content and requested_content_status == "ready":
            raise ValueError("Ready content requires a non-empty canonical payload")
        committed_content_status = (
            "failed" if article.content and requested_content_status == "failed"
            else "ready" if article.content
            else requested_content_status
        )
        validation_status = article.content_validation_status
        if committed_content_status == "failed" and validation_status == "pending":
            validation_status = "rejected"
        elif article.content and validation_status == "pending":
            validation_status = "verified" if article.source == "news" else "not_applicable"
        summary_status = (
            "ready"
            if article.summary is not None and committed_content_status == "ready"
            else normalize_processing_status(article.summary_status if article.summary is None else "pending")
        )
        index_status = normalize_processing_status(article.index_status)
        if index_status == "ready" and committed_content_status != "ready":
            raise ValueError("A ready compatibility index must reference ready canonical content")

        try:
            self.conn.execute("SAVEPOINT save_article")
            cursor = self.conn.execute(
                """
                INSERT INTO articles
                (
                    source, title, description, content, url, published_at,
                    source_published_at, published_at_provenance, summary,
                    original_url, normalized_url, canonical_url, content_path, content_sha256,
                    active_content_sha256, content_status, index_status, summary_status,
                    summary_content_sha256, indexed_content_sha256,
                    content_validation_status, content_validation_reason,
                    content_validator_version, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.source,
                    article.title,
                    article.description,
                    "",
                    original_url or article.url,
                    article.published_at.isoformat(),
                    article.source_published_at.isoformat() if article.source_published_at else None,
                    article.published_at_provenance,
                    article.summary,
                    original_url,
                    normalized_url,
                    canonical_url or None,
                    None,
                    content_sha256,
                    content_sha256 if committed_content_status == "ready" else None,
                    "pending",
                    index_status,
                    summary_status,
                    content_sha256 if article.summary and committed_content_status == "ready" else None,
                    content_sha256 if index_status == "ready" else None,
                    validation_status,
                    article.content_validation_reason,
                    article.content_validator_version,
                    fetched_at,
                )
            )
            article_id = cursor.lastrowid
            if article.content:
                final_path = self.write_article_content(
                    article_id,
                    article.content,
                    content_sha256=content_sha256,
                )
                self.conn.execute(
                    """
                    UPDATE articles
                    SET content_path = ?, content_status = ?,
                        last_content_attempt_status = 'succeeded',
                        last_content_attempt_reason = NULL,
                        last_content_attempt_at = ?
                    WHERE id = ?
                    """,
                    (final_path, committed_content_status, fetched_at, article_id),
                )
            self.conn.execute("RELEASE SAVEPOINT save_article")
            return article_id
        except sqlite3.IntegrityError:
            self.conn.execute("ROLLBACK TO SAVEPOINT save_article")
            self.conn.execute("RELEASE SAVEPOINT save_article")
            if self.find_article_id_by_url(canonical_url or None, normalized_url) is not None:
                return None
            raise
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT save_article")
            self.conn.execute("RELEASE SAVEPOINT save_article")
            raise

    def create_or_get_article_reference(
        self,
        *,
        source: str,
        title: str,
        description: str,
        original_url: str,
        published_at: datetime,
        source_published_at: Optional[datetime] = None,
        published_at_provenance: str = "source_metadata",
        publisher_source_id: Optional[str] = None,
        publisher_source_name: Optional[str] = None,
    ) -> tuple[int, bool]:
        if not self.conn:
            self.init_db()

        if source_published_at is None and published_at_provenance == "source_metadata":
            source_published_at = published_at

        normalized_url = normalize_url(original_url)
        existing_id = self.find_article_id_by_url(None, normalized_url)
        if existing_id is not None:
            self.register_url_alias(
                existing_id,
                original_url,
                alias_kind="source",
                publisher_source_id=publisher_source_id,
                publisher_source_name=publisher_source_name,
            )
            return existing_id, False

        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO articles
            (
                source, title, description, content, url, published_at,
                source_published_at, published_at_provenance, summary,
                original_url, normalized_url, content_status, index_status, summary_status, fetched_at,
                publisher_source_id, publisher_source_name, content_validation_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                title,
                description,
                "",
                original_url,
                published_at.isoformat(),
                source_published_at.isoformat() if source_published_at else None,
                published_at_provenance,
                None,
                original_url,
                normalized_url,
                "pending",
                "pending",
                "pending",
                datetime.now().isoformat(),
                publisher_source_id or None,
                publisher_source_name or None,
                "pending",
            ),
        )
        self.conn.commit()
        if cursor.rowcount > 0:
            article_id = cursor.lastrowid
            self.register_url_alias(
                article_id,
                original_url,
                alias_kind="source",
                publisher_source_id=publisher_source_id,
                publisher_source_name=publisher_source_name,
            )
            return article_id, True
        existing_id = self.find_article_id_by_url(None, normalized_url)
        if existing_id is None:
            raise sqlite3.IntegrityError("Article insert conflicted without a reusable normalized URL identity")
        return existing_id, False

    def find_article_id_by_url(self, canonical_url: Optional[str], normalized_url: Optional[str]) -> Optional[int]:
        if not self.conn:
            self.init_db()
        if canonical_url:
            canonical_url = normalize_url(canonical_url)
            alias = self.conn.execute(
                "SELECT article_id FROM article_url_aliases WHERE normalized_url = ?",
                (canonical_url,),
            ).fetchone()
            if alias:
                return alias["article_id"]
            row = self.conn.execute(
                "SELECT id FROM articles WHERE canonical_url = ? OR normalized_url = ? OR url = ?",
                (canonical_url, canonical_url, canonical_url),
            ).fetchone()
            if row:
                return row["id"]
        if normalized_url:
            normalized_url = normalize_url(normalized_url)
            alias = self.conn.execute(
                "SELECT article_id FROM article_url_aliases WHERE normalized_url = ?",
                (normalized_url,),
            ).fetchone()
            if alias:
                return alias["article_id"]
            row = self.conn.execute(
                "SELECT id FROM articles WHERE normalized_url = ? OR url = ?",
                (normalized_url, normalized_url),
            ).fetchone()
            if row:
                return row["id"]
        return None

    def register_url_alias(
        self,
        article_id: int,
        url: str,
        *,
        alias_kind: str,
        publisher_source_id: Optional[str] = None,
        publisher_source_name: Optional[str] = None,
    ) -> None:
        normalized_url = normalize_url(url)
        if not normalized_url:
            return
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO article_url_aliases
                (normalized_url, article_id, original_url, alias_kind,
                 publisher_source_id, publisher_source_name, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                publisher_source_id = COALESCE(article_url_aliases.publisher_source_id, excluded.publisher_source_id),
                publisher_source_name = COALESCE(article_url_aliases.publisher_source_name, excluded.publisher_source_name)
            """,
            (
                normalized_url,
                article_id,
                url,
                alias_kind,
                publisher_source_id or None,
                publisher_source_name or None,
                now,
                now,
            ),
        )
        self.conn.commit()

    def reconcile_article_identity(
        self, article_id: int, canonical_url: Optional[str]
    ) -> tuple[int, bool, int | None, str | None]:
        """Collapse canonical aliases onto the lowest provisional article id.

        References are created in caller input order. Choosing the lower id
        makes a same-batch redirect collision independent of network completion
        order. A lower pending reference may therefore replace a higher ready
        provisional row before its own canonical content is committed.
        """
        normalized_canonical = normalize_url(canonical_url or "")
        if not normalized_canonical:
            return article_id, False, None, None
        existing_id = self.find_article_id_by_url(normalized_canonical, normalized_canonical)
        if existing_id is None or existing_id == article_id:
            self.register_url_alias(article_id, normalized_canonical, alias_kind="canonical")
            return article_id, False, None, None
        row = self.conn.execute(
            "SELECT content_status, content_path, content, story_group_id FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if (
            article_id < existing_id
            and row
            and row["content_status"] == "pending"
            and not row["content_path"]
            and not row["content"]
        ):
            duplicate = self.conn.execute(
                "SELECT * FROM articles WHERE id = ?", (existing_id,)
            ).fetchone()
            if duplicate is None:
                raise sqlite3.IntegrityError("canonical duplicate disappeared during reconciliation")
            retired_content_path = self.resolve_content_path(
                duplicate["content_path"], article_id=existing_id
            )
            copied_columns = (
                "title", "description", "content", "published_at", "source_published_at",
                "published_at_provenance", "summary", "canonical_url", "content_path",
                "content_sha256", "content_status", "index_status", "summary_status",
                "structured_at", "structuring_status", "structuring_error", "fetched_at",
                "publisher_source_id", "publisher_source_name", "story_group_id",
                "story_dedupe_method", "active_content_sha256", "summary_content_sha256",
                "indexed_content_sha256", "content_validation_status",
                "content_validation_reason", "content_validator_version", "final_response_url",
                "response_status_code", "response_content_type", "extractor_version",
                "extracted_char_count", "last_content_attempt_status",
                "last_content_attempt_reason", "last_content_attempt_at",
            )
            self.conn.execute("SAVEPOINT reconcile_ready_identity")
            try:
                self.conn.execute(
                    "UPDATE articles SET canonical_url = NULL WHERE id = ?", (existing_id,)
                )
                assignments = ", ".join(f"{column} = ?" for column in copied_columns)
                self.conn.execute(
                    f"UPDATE articles SET {assignments} WHERE id = ?",
                    [duplicate[column] for column in copied_columns] + [article_id],
                )
                self.conn.execute(
                    "UPDATE story_groups SET representative_article_id = ? "
                    "WHERE representative_article_id = ?",
                    (article_id, existing_id),
                )
                self.conn.execute(
                    "UPDATE structured_events SET article_id = ? WHERE article_id = ?",
                    (article_id, existing_id),
                )
                self.conn.execute(
                    "UPDATE watchlist_ticker_evidence SET article_id = ? WHERE article_id = ?",
                    (article_id, existing_id),
                )
                self.conn.execute(
                    "UPDATE OR IGNORE watchlist_ticker_structuring_attempts SET article_id = ? "
                    "WHERE article_id = ?",
                    (article_id, existing_id),
                )
                self.conn.execute(
                    "DELETE FROM watchlist_ticker_structuring_attempts WHERE article_id = ?",
                    (existing_id,),
                )
                self.conn.execute(
                    """
                    INSERT INTO article_identity_merges (
                        retired_article_id, survivor_article_id, canonical_url,
                        retired_content_path, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        existing_id,
                        article_id,
                        normalized_canonical,
                        retired_content_path,
                        datetime.now().isoformat(),
                    ),
                )
                aliases = self.conn.execute(
                    "SELECT * FROM article_url_aliases WHERE article_id = ?",
                    (existing_id,),
                ).fetchall()
                for alias in aliases:
                    self.conn.execute(
                        "UPDATE article_url_aliases SET article_id = ?, last_seen_at = ? WHERE normalized_url = ?",
                        (article_id, datetime.now().isoformat(), alias["normalized_url"]),
                    )
                self.conn.execute("DELETE FROM articles WHERE id = ?", (existing_id,))
                self.conn.execute("RELEASE SAVEPOINT reconcile_ready_identity")
            except Exception:
                self.conn.execute("ROLLBACK TO SAVEPOINT reconcile_ready_identity")
                self.conn.execute("RELEASE SAVEPOINT reconcile_ready_identity")
                raise
            duplicate_story_group_id = duplicate["story_group_id"]
            if duplicate_story_group_id is not None:
                remaining = self.conn.execute(
                    "SELECT 1 FROM articles WHERE story_group_id = ? LIMIT 1",
                    (duplicate_story_group_id,),
                ).fetchone()
                if remaining is None:
                    self.conn.execute(
                        "DELETE FROM story_groups WHERE id = ?", (duplicate_story_group_id,)
                    )
            self.conn.commit()
            self.register_url_alias(article_id, normalized_canonical, alias_kind="canonical")
            return article_id, False, existing_id, retired_content_path
        if row and row["content_status"] == "pending" and not row["content_path"] and not row["content"]:
            aliases = self.conn.execute(
                "SELECT * FROM article_url_aliases WHERE article_id = ?",
                (article_id,),
            ).fetchall()
            for alias in aliases:
                self.conn.execute(
                    "UPDATE article_url_aliases SET article_id = ?, last_seen_at = ? WHERE normalized_url = ?",
                    (existing_id, datetime.now().isoformat(), alias["normalized_url"]),
                )
            self.conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            self.conn.commit()
            self.register_url_alias(existing_id, normalized_canonical, alias_kind="canonical")
            return existing_id, True, None, None
        raise sqlite3.IntegrityError(
            f"Canonical URL {normalized_canonical} already belongs to article {existing_id}"
        )

    def complete_identity_merge_cleanup(
        self,
        retired_article_id: int,
        *,
        vector_cleanup_status: str,
    ) -> bool:
        """Complete tracked external cleanup only after the survivor is fully usable."""
        if vector_cleanup_status not in {"deleted", "successor_generation_required"}:
            raise ValueError("invalid vector cleanup status")
        row = self.conn.execute(
            """
            SELECT merge.*, article.content_status, article.summary_status, article.index_status,
                   article.content_path AS survivor_content_path,
                   COALESCE(article.active_content_sha256, article.content_sha256) AS survivor_content_sha256
            FROM article_identity_merges AS merge
            JOIN articles AS article ON article.id = merge.survivor_article_id
            WHERE merge.retired_article_id = ?
            """,
            (retired_article_id,),
        ).fetchone()
        if row is None:
            return False
        if row["content_status"] != "ready" or row["summary_status"] != "ready":
            return False
        if vector_cleanup_status == "deleted" and row["index_status"] != "ready":
            return False
        retired_path = row["retired_content_path"]
        survivor_path = self.resolve_content_path(
            row["survivor_content_path"], article_id=row["survivor_article_id"]
        )
        content_status = "not_present"
        if retired_path and os.path.isfile(retired_path):
            resolved = os.path.realpath(retired_path)
            root = os.path.realpath(self.content_dir)
            if os.path.commonpath((root, resolved)) != root:
                raise ValueError("retired content path escapes canonical content root")
            if survivor_path and os.path.realpath(survivor_path) == resolved:
                with open(resolved, "r", encoding="utf-8") as handle:
                    content = handle.read()
                content_hash = row["survivor_content_sha256"]
                if not content_hash or compute_content_sha256(content) != content_hash:
                    raise ValueError("retired content does not match survivor hash")
                survivor_relpath = self.write_article_content(
                    row["survivor_article_id"], content, content_sha256=content_hash
                )
                self.conn.execute(
                    "UPDATE articles SET content_path = ? WHERE id = ?",
                    (survivor_relpath, row["survivor_article_id"]),
                )
            os.remove(resolved)
            content_status = "deleted"
        self.conn.execute(
            """
            UPDATE article_identity_merges
            SET vector_cleanup_status = ?, content_cleanup_status = ?, completed_at = ?
            WHERE retired_article_id = ?
            """,
            (vector_cleanup_status, content_status, datetime.now().isoformat(), retired_article_id),
        )
        self.conn.commit()
        return True

    def list_pending_identity_merge_ids(self) -> tuple[int, ...]:
        """Return durable cleanup work left by prior ingestion runs."""
        rows = self.conn.execute(
            """
            SELECT retired_article_id
            FROM article_identity_merges
            WHERE completed_at IS NULL
            ORDER BY retired_article_id
            """
        ).fetchall()
        return tuple(row["retired_article_id"] for row in rows)

    def identity_merge_cleanup_ready(
        self, retired_article_id: int, *, require_index_ready: bool
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT article.content_status, article.summary_status, article.index_status
            FROM article_identity_merges AS receipt
            JOIN articles AS article ON article.id = receipt.survivor_article_id
            WHERE receipt.retired_article_id = ? AND receipt.completed_at IS NULL
            """,
            (retired_article_id,),
        ).fetchone()
        return bool(
            row
            and row["content_status"] == "ready"
            and row["summary_status"] == "ready"
            and (not require_index_ready or row["index_status"] == "ready")
        )

    def update_article_content(
        self,
        article_id: int,
        *,
        content: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        original_url: Optional[str] = None,
        canonical_url: Optional[str] = None,
        published_at: Optional[datetime] = None,
        source_published_at: Optional[datetime] = None,
        published_at_provenance: Optional[str] = None,
        validation_status: str = "verified",
        validation_reason: Optional[str] = None,
        validator_version: Optional[str] = None,
        response_status_code: Optional[int] = None,
        response_content_type: Optional[str] = None,
        extractor_version: Optional[str] = None,
        final_response_url: Optional[str] = None,
    ) -> bool:
        if not self.conn:
            self.init_db()
        if validation_status not in {"verified", "not_applicable"}:
            raise ValueError(
                "Canonical content may become ready only with verified or not_applicable validation"
            )
        if not (content or "").strip():
            raise ValueError("Canonical content must be non-empty")

        state = self.conn.execute(
            "SELECT quarantined_at, quarantine_reason FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if state is None:
            return False
        if state["quarantined_at"]:
            raise ValueError(
                f"Article {article_id} is quarantined: {state['quarantine_reason'] or 'integrity review required'}"
            )

        content_sha256 = compute_content_sha256(content)
        if self.has_verified_content_hash(article_id, content_sha256):
            self.update_article_source_metadata(
                article_id,
                original_url=original_url,
                canonical_url=canonical_url,
                title=title,
                description=description,
                published_at=published_at,
                source_published_at=source_published_at,
                published_at_provenance=published_at_provenance,
                validation_status=validation_status,
                validation_reason=validation_reason,
                validator_version=validator_version,
                response_status_code=response_status_code,
                response_content_type=response_content_type,
                extractor_version=extractor_version,
                final_response_url=final_response_url,
            )
            return False

        final_path = self.write_article_content(article_id, content, content_sha256=content_sha256)
        updates = {
            "content": "",
            "content_path": final_path,
            "content_sha256": content_sha256,
            "active_content_sha256": content_sha256,
            "content_status": "ready",
            "index_status": "pending",
            "summary_status": "pending",
            "summary": None,
            "summary_content_sha256": None,
            "indexed_content_sha256": None,
            "structured_at": None,
            "structuring_status": None,
            "structuring_error": None,
            "story_group_id": None,
            "story_dedupe_method": None,
            "fetched_at": datetime.now().isoformat(),
            "content_validation_status": validation_status,
            "content_validation_reason": validation_reason,
            "content_validator_version": validator_version,
            "final_response_url": final_response_url or original_url,
            "response_status_code": response_status_code,
            "response_content_type": response_content_type,
            "extractor_version": extractor_version,
            "extracted_char_count": len(" ".join((content or "").split()).strip()),
            "last_content_attempt_status": "succeeded",
            "last_content_attempt_reason": None,
            "last_content_attempt_at": datetime.now().isoformat(),
        }
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if original_url is not None:
            updates["original_url"] = original_url
            updates["normalized_url"] = normalize_url(original_url)
            updates["url"] = original_url
        if canonical_url is not None:
            updates["canonical_url"] = normalize_url(canonical_url)
        if published_at is not None:
            updates["published_at"] = published_at.isoformat()
        if published_at_provenance is not None:
            updates["source_published_at"] = (
                source_published_at.isoformat() if source_published_at is not None else None
            )
            updates["published_at_provenance"] = published_at_provenance

        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [article_id]
        self.conn.execute("SAVEPOINT canonical_content_commit")
        try:
            cursor = self.conn.execute(
                f"UPDATE articles SET {assignments} WHERE id = ? AND quarantined_at IS NULL",
                values,
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError(
                    f"Article {article_id} disappeared or was quarantined before content commit"
                )
            self.conn.execute("RELEASE SAVEPOINT canonical_content_commit")
            return True
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT canonical_content_commit")
            self.conn.execute("RELEASE SAVEPOINT canonical_content_commit")
            raise

    def update_article_source_metadata(
        self,
        article_id: int,
        *,
        original_url: Optional[str] = None,
        canonical_url: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        published_at: Optional[datetime] = None,
        source_published_at: Optional[datetime] = None,
        published_at_provenance: Optional[str] = None,
        validation_status: Optional[str] = None,
        validation_reason: Optional[str] = None,
        validator_version: Optional[str] = None,
        response_status_code: Optional[int] = None,
        response_content_type: Optional[str] = None,
        extractor_version: Optional[str] = None,
        final_response_url: Optional[str] = None,
    ) -> bool:
        """Update article source-identifying metadata without touching content or summary."""
        if not self.conn:
            self.init_db()

        updates = {}
        if original_url is not None:
            updates["original_url"] = original_url
            updates["normalized_url"] = normalize_url(original_url)
            updates["url"] = original_url
        if canonical_url is not None:
            updates["canonical_url"] = normalize_url(canonical_url)
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if published_at is not None:
            updates["published_at"] = published_at.isoformat()
        if published_at_provenance is not None:
            updates["source_published_at"] = (
                source_published_at.isoformat() if source_published_at is not None else None
            )
            updates["published_at_provenance"] = published_at_provenance
        if validation_status is not None:
            updates["content_validation_status"] = validation_status
            updates["content_validation_reason"] = validation_reason
        if validator_version is not None:
            updates["content_validator_version"] = validator_version
        if final_response_url is not None:
            updates["final_response_url"] = final_response_url
        elif original_url is not None:
            updates["final_response_url"] = original_url
        if response_status_code is not None:
            updates["response_status_code"] = response_status_code
        if response_content_type is not None:
            updates["response_content_type"] = response_content_type
        if extractor_version is not None:
            updates["extractor_version"] = extractor_version
        updates["last_content_attempt_status"] = "succeeded"
        updates["last_content_attempt_reason"] = None
        updates["last_content_attempt_at"] = datetime.now().isoformat()
        if not updates:
            return False

        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [article_id]
        cursor = self.conn.execute(
            f"UPDATE articles SET {assignments} WHERE id = ? AND quarantined_at IS NULL",
            values,
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_article_processing_status(
        self,
        article_id: int,
        *,
        content_status: Optional[str] = None,
        index_status: Optional[str] = None,
        summary_status: Optional[str] = None,
    ) -> bool:
        if not self.conn:
            self.init_db()
        updates = {}
        normalized_content_status = (
            normalize_processing_status(content_status) if content_status is not None else None
        )
        normalized_index_status = (
            normalize_processing_status(index_status) if index_status is not None else None
        )
        normalized_summary_status = (
            normalize_processing_status(summary_status) if summary_status is not None else None
        )
        requires_unquarantined = "ready" in {
            normalized_content_status,
            normalized_index_status,
            normalized_summary_status,
        }
        if requires_unquarantined:
            promotion_state = self.conn.execute(
                """
                SELECT quarantined_at, quarantine_reason,
                       active_content_sha256, content_sha256
                FROM articles WHERE id = ?
                """,
                (article_id,),
            ).fetchone()
            if promotion_state is None:
                return False
            if promotion_state["quarantined_at"]:
                raise ValueError(
                    f"Article {article_id} is quarantined: "
                    f"{promotion_state['quarantine_reason'] or 'integrity review required'}"
                )
            promotion_hash = (
                promotion_state["active_content_sha256"] or promotion_state["content_sha256"]
            )
            if normalized_index_status == "ready":
                if not promotion_hash:
                    raise ValueError("Ready index status requires an active content hash")
                updates["indexed_content_sha256"] = promotion_hash
            if normalized_summary_status == "ready":
                if not promotion_hash:
                    raise ValueError("Ready summary status requires an active content hash")
                updates["summary_content_sha256"] = promotion_hash
        if content_status is not None:
            updates["content_status"] = normalized_content_status
        if index_status is not None:
            updates["index_status"] = normalized_index_status
            if normalized_index_status != "ready":
                updates["indexed_content_sha256"] = None
        if summary_status is not None:
            updates["summary_status"] = normalized_summary_status
            if normalized_summary_status != "ready":
                updates["summary_content_sha256"] = None
        if not updates:
            return False
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [article_id]
        where = "id = ? AND quarantined_at IS NULL" if requires_unquarantined else "id = ?"
        cursor = self.conn.execute(f"UPDATE articles SET {assignments} WHERE {where}", values)
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_article_content_failure(
        self,
        article_id: int,
        *,
        reason: str,
        validator_version: Optional[str] = None,
        final_response_url: Optional[str] = None,
        response_status_code: Optional[int] = None,
        response_content_type: Optional[str] = None,
        extractor_version: Optional[str] = None,
    ) -> bool:
        if not self.conn:
            self.init_db()
        row = self.conn.execute(
            """
            SELECT active_content_sha256, content_sha256, quarantined_at
            FROM articles WHERE id = ?
            """,
            (article_id,),
        ).fetchone()
        if row is None:
            return False
        active_hash = row["active_content_sha256"] or row["content_sha256"]
        now = datetime.now().isoformat()
        if row["quarantined_at"] or (active_hash and self.has_verified_content_hash(article_id, active_hash)):
            cursor = self.conn.execute(
                """
                UPDATE articles
                SET last_content_attempt_status = 'failed',
                    last_content_attempt_reason = ?,
                    last_content_attempt_at = ?,
                    final_response_url = COALESCE(?, final_response_url),
                    response_status_code = COALESCE(?, response_status_code),
                    response_content_type = COALESCE(?, response_content_type),
                    extractor_version = COALESCE(?, extractor_version)
                WHERE id = ?
                """,
                (
                    reason,
                    now,
                    final_response_url,
                    response_status_code,
                    response_content_type,
                    extractor_version,
                    article_id,
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

        cursor = self.conn.execute(
            """
            UPDATE articles
            SET content_status = 'failed',
                index_status = 'pending',
                summary_status = 'pending',
                summary = NULL,
                indexed_content_sha256 = NULL,
                summary_content_sha256 = NULL,
                content_validation_status = 'rejected',
                content_validation_reason = ?,
                content_validator_version = ?,
                final_response_url = COALESCE(?, final_response_url),
                response_status_code = COALESCE(?, response_status_code),
                response_content_type = COALESCE(?, response_content_type),
                extractor_version = COALESCE(?, extractor_version),
                last_content_attempt_status = 'failed',
                last_content_attempt_reason = ?,
                last_content_attempt_at = ?
            WHERE id = ?
              AND quarantined_at IS NULL
              AND active_content_sha256 IS ?
              AND content_sha256 IS ?
            """,
            (
                reason,
                validator_version,
                final_response_url,
                response_status_code,
                response_content_type,
                extractor_version,
                reason,
                now,
                article_id,
                row["active_content_sha256"],
                row["content_sha256"],
            ),
        )
        self.conn.commit()
        if cursor.rowcount > 0:
            return True

        # A concurrent worker committed or quarantined a newer state after the
        # read above. Record this attempt without changing that aggregate.
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET last_content_attempt_status = 'failed',
                last_content_attempt_reason = ?,
                last_content_attempt_at = ?
            WHERE id = ?
            """,
            (reason, now, article_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def has_verified_content_hash(self, article_id: int, content_sha256: str) -> bool:
        if not self.conn:
            self.init_db()
        row = self.conn.execute(
            """
            SELECT content_path, active_content_sha256, content_sha256, content_validation_status
            FROM articles WHERE id = ?
            """,
            (article_id,),
        ).fetchone()
        if not row or row["content_validation_status"] not in {"verified", "not_applicable"}:
            return False
        stored_hash = row["active_content_sha256"] or row["content_sha256"]
        if stored_hash != content_sha256:
            return False
        resolved_path = self.resolve_content_path(row["content_path"], article_id=article_id)
        if not resolved_path or not os.path.isfile(resolved_path):
            return False
        with open(resolved_path, "r", encoding="utf-8") as handle:
            return compute_content_sha256(handle.read()) == content_sha256

    def assign_story_group(
        self,
        article_id: int,
        *,
        title: str,
        published_at: datetime,
        content_sha256: str,
    ) -> tuple[int, str]:
        """Assign only evidence-backed duplicate groups; never discard publisher rows."""
        if not self.conn:
            self.init_db()
        duplicate = self.conn.execute(
            """
            SELECT story_group_id
            FROM articles
            WHERE id != ?
              AND story_group_id IS NOT NULL
              AND COALESCE(active_content_sha256, content_sha256) = ?
            ORDER BY id
            LIMIT 1
            """,
            (article_id, content_sha256),
        ).fetchone()
        normalized_title = normalize_story_title(title)
        publication_date = published_at.date().isoformat()
        if duplicate:
            story_group_id = duplicate["story_group_id"]
            method = "exact_content_sha256"
        else:
            story_key = compute_story_key(normalized_title, publication_date)
            self.conn.execute(
                """
                INSERT OR IGNORE INTO story_groups
                    (story_key, normalized_title, publication_date, representative_article_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (story_key, normalized_title, publication_date, article_id, datetime.now().isoformat()),
            )
            group = self.conn.execute(
                "SELECT id, representative_article_id FROM story_groups WHERE story_key = ?",
                (story_key,),
            ).fetchone()
            assert group is not None
            story_group_id = group["id"]
            method = "new_story" if group["representative_article_id"] == article_id else "exact_title_date"
        self.conn.execute(
            "UPDATE articles SET story_group_id = ?, story_dedupe_method = ? WHERE id = ?",
            (story_group_id, method, article_id),
        )
        self.conn.commit()
        return story_group_id, method

    def get_article_content(self, article_id: int) -> str:
        record = self.get_article_record(article_id)
        if record is None:
            raise ValueError(f"Article {article_id} not found")
        return record.article.content

    def write_article_content(
        self,
        article_id: int,
        content: str,
        *,
        content_sha256: Optional[str] = None,
    ) -> str:
        """Atomically write immutable content and return a machine-independent relative path."""
        digest = content_sha256 or compute_content_sha256(content)
        article_dir = os.path.join(self.content_dir, str(article_id))
        os.makedirs(article_dir, exist_ok=True)
        final_path = os.path.join(article_dir, f"{digest}.txt")
        must_write = True
        if os.path.isfile(final_path):
            with open(final_path, "r", encoding="utf-8") as handle:
                must_write = compute_content_sha256(handle.read()) != digest
        if must_write:
            fd, temporary_path = tempfile.mkstemp(prefix=".content-", suffix=".tmp", dir=article_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                with open(temporary_path, "r", encoding="utf-8") as handle:
                    if compute_content_sha256(handle.read()) != digest:
                        raise OSError("Content hash changed during atomic write")
                os.replace(temporary_path, final_path)
            finally:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)
        return os.path.relpath(final_path, self.content_dir).replace("\\", "/")

    def resolve_content_path(
        self,
        content_path: Optional[str],
        *,
        article_id: Optional[int] = None,
    ) -> Optional[str]:
        """Resolve a stored path without reading outside the configured content root.

        New rows use relative version paths. For legacy rows whose absolute path
        names another machine, callers may provide article_id to try the safe flat
        fallback ``content_root/{article_id}.txt``. The external absolute path is
        never opened.
        """
        if not content_path:
            return None
        content_root = os.path.realpath(self.content_dir)
        if os.path.isabs(content_path):
            absolute_candidate = os.path.realpath(content_path)
            if _path_is_within(absolute_candidate, content_root):
                return absolute_candidate
            if article_id is None:
                return None
            legacy_candidate = os.path.realpath(os.path.join(content_root, f"{article_id}.txt"))
            return legacy_candidate if _path_is_within(legacy_candidate, content_root) and os.path.isfile(legacy_candidate) else None

        normalized = content_path.replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            return None
        candidate = os.path.realpath(os.path.join(content_root, *normalized.split("/")))
        if not _path_is_within(candidate, content_root):
            return None
        if os.path.isfile(candidate) or article_id is None:
            return candidate
        legacy_candidate = os.path.realpath(os.path.join(content_root, f"{article_id}.txt"))
        return legacy_candidate if _path_is_within(legacy_candidate, content_root) and os.path.isfile(legacy_candidate) else candidate

    def write_article_content_file_placeholder(self, content: str) -> str:
        del content
        os.makedirs(self.content_dir, exist_ok=True)
        return os.path.join(self.content_dir, "_pending.txt")
    
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

    def list_retrieval_eligible_article_ids(self) -> set[int]:
        """Return only articles whose current canonical bytes still match the active index version."""
        if not self.conn:
            self.init_db()
        rows = self.conn.execute(
            """
            SELECT id, source, content_path,
                   COALESCE(active_content_sha256, content_sha256) AS active_hash,
                   indexed_content_sha256, content_validation_status
            FROM articles
            WHERE content_status = 'ready'
              AND index_status = 'ready'
              AND quarantined_at IS NULL
              AND COALESCE(active_content_sha256, content_sha256) IS NOT NULL
              AND indexed_content_sha256 = COALESCE(active_content_sha256, content_sha256)
              AND (
                    (source = 'news' AND content_validation_status = 'verified')
                 OR (source != 'news' AND content_validation_status IN ('verified', 'not_applicable'))
              )
            ORDER BY id
            """
        ).fetchall()
        eligible: set[int] = set()
        for row in rows:
            resolved_path = self.resolve_content_path(row["content_path"], article_id=row["id"])
            if not resolved_path or not os.path.isfile(resolved_path):
                continue
            with open(resolved_path, "r", encoding="utf-8") as handle:
                content = handle.read()
            if not content.strip():
                continue
            if compute_content_sha256(content) != row["active_hash"]:
                continue
            eligible.add(row["id"])
        return eligible

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
            WHERE content_status = 'ready'
              AND (summary_status != 'ready' OR summary IS NULL OR TRIM(summary) = '')
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

    def update_article_summary(
        self,
        article_id: int,
        summary: str,
        *,
        content_sha256: Optional[str] = None,
    ) -> bool:
        """Persist a summary only if its input content version is still active."""
        if not self.conn:
            self.init_db()

        if content_sha256 is None:
            row = self.conn.execute(
                "SELECT active_content_sha256, content_sha256 FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if row is None:
                return False
            content_sha256 = row["active_content_sha256"] or row["content_sha256"]
        if not content_sha256:
            return False
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET summary = ?, summary_status = 'ready', summary_content_sha256 = ?
            WHERE id = ?
              AND content_status = 'ready'
              AND quarantined_at IS NULL
              AND COALESCE(active_content_sha256, content_sha256) = ?
            """,
            (summary, content_sha256, article_id, content_sha256),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_article_index_ready(self, article_id: int, content_sha256: str) -> bool:
        """Promote index state with compare-and-set against the active content hash."""
        if not self.conn:
            self.init_db()
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET index_status = 'ready', indexed_content_sha256 = ?
            WHERE id = ?
              AND content_status = 'ready'
              AND quarantined_at IS NULL
              AND COALESCE(active_content_sha256, content_sha256) = ?
            """,
            (content_sha256, article_id, content_sha256),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_article_summary_failure(self, article_id: int, content_sha256: str) -> bool:
        """Fail only the summary attempt that still targets the active version."""
        if not self.conn:
            self.init_db()
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET summary_status = 'failed', summary_content_sha256 = NULL
            WHERE id = ?
              AND quarantined_at IS NULL
              AND COALESCE(active_content_sha256, content_sha256) = ?
              AND NOT (summary_status = 'ready' AND summary_content_sha256 = ?)
            """,
            (article_id, content_sha256, content_sha256),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def mark_article_index_failure(self, article_id: int, content_sha256: str) -> bool:
        """Fail only the index attempt that still targets the active version."""
        if not self.conn:
            self.init_db()
        cursor = self.conn.execute(
            """
            UPDATE articles
            SET index_status = 'failed', indexed_content_sha256 = NULL
            WHERE id = ?
              AND quarantined_at IS NULL
              AND COALESCE(active_content_sha256, content_sha256) = ?
              AND NOT (index_status = 'ready' AND indexed_content_sha256 = ?)
            """,
            (article_id, content_sha256, content_sha256),
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
             reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                None,
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

    def save_watchlist_report_path(self, run_id: str, report_path: str) -> bool:
        """Persist the generated Markdown report path for one watchlist run."""
        if not self.conn:
            self.init_db()

        cursor = self.conn.execute(
            "UPDATE watchlist_runs SET report_path = ? WHERE run_id = ?",
            (report_path, run_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def fetch_watchlist_report_path(self, run_id: str) -> Optional[str]:
        """Fetch the persisted Markdown report path for one watchlist run."""
        if not self.conn:
            self.init_db()

        row = self.conn.execute(
            "SELECT report_path FROM watchlist_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return row["report_path"]

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
        self._ensure_watchlist_run_placeholder(review.run_id)

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
        self._ensure_watchlist_run_placeholder(followup.run_id)

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

    def _ensure_watchlist_run_placeholder(self, run_id: str) -> None:
        row = self.conn.execute(
            "SELECT run_id FROM watchlist_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is not None:
            return
        self.conn.execute(
            """
            INSERT INTO watchlist_runs
            (run_id, run_at, watchlist_size, top_n, retrieval_top_k, triage_model,
             reviewer_model, triage_prompt_version, reviewer_prompt_version, report_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now().isoformat(),
                0,
                0,
                0,
                "unknown",
                "unknown",
                "unknown",
                "unknown",
                None,
                "placeholder",
            ),
        )
        self.conn.commit()
    
    def delete_article(self, article_id: int) -> bool:
        """Delete article metadata while retaining immutable content for controlled GC."""
        if not self.conn:
            self.init_db()

        self.conn.execute("SAVEPOINT delete_article")
        try:
            event_ids = [
                record["event_id"]
                for record in self.conn.execute(
                    "SELECT event_id FROM structured_events WHERE article_id = ?",
                    (article_id,),
                ).fetchall()
            ]
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                self.conn.execute(
                    f"DELETE FROM watchlist_ticker_events WHERE event_id IN ({placeholders})",
                    event_ids,
                )
            self.conn.execute(
                "DELETE FROM watchlist_ticker_evidence WHERE article_id = ?",
                (article_id,),
            )
            self.conn.execute(
                "DELETE FROM watchlist_ticker_structuring_attempts WHERE article_id = ?",
                (article_id,),
            )
            self.conn.execute("DELETE FROM structured_events WHERE article_id = ?", (article_id,))
            cursor = self.conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            self.conn.execute("RELEASE SAVEPOINT delete_article")
            return cursor.rowcount > 0
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT delete_article")
            self.conn.execute("RELEASE SAVEPOINT delete_article")
            raise
    
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
        content = ""
        content_path = row["content_path"] if "content_path" in row.keys() else None
        resolved_content_path = self.resolve_content_path(content_path, article_id=row["id"])
        expected_hash = (
            row["active_content_sha256"]
            if "active_content_sha256" in row.keys() and row["active_content_sha256"]
            else row["content_sha256"] if "content_sha256" in row.keys() else None
        )
        integrity_ok = True
        if "quarantined_at" in row.keys() and row["quarantined_at"]:
            integrity_ok = False
        if resolved_content_path and os.path.isfile(resolved_content_path):
            with open(resolved_content_path, "r", encoding="utf-8") as handle:
                content = handle.read()
            if expected_hash and compute_content_sha256(content) != expected_hash:
                content = ""
                integrity_ok = False
        elif "content" in row.keys():
            content = row["content"] or ""
            if row["content_status"] == "ready" and not content:
                integrity_ok = False
            if expected_hash and content and compute_content_sha256(content) != expected_hash:
                content = ""
                integrity_ok = False
        return NewsArticle(
            source=row["source"],
            title=row["title"],
            description=row["description"],
            content=content,
            url=row["canonical_url"] or row["original_url"] or row["url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            summary=row["summary"],
            original_url=row["original_url"] if "original_url" in row.keys() else row["url"],
            normalized_url=row["normalized_url"] if "normalized_url" in row.keys() else normalize_url(row["url"]),
            canonical_url=row["canonical_url"] if "canonical_url" in row.keys() else None,
            content_path=resolved_content_path,
            content_sha256=row["content_sha256"] if "content_sha256" in row.keys() else None,
            fetched_at=datetime.fromisoformat(row["fetched_at"]) if row["fetched_at"] else None,
            content_status=(row["content_status"] if integrity_ok else "failed") if "content_status" in row.keys() else "ready",
            index_status=row["index_status"] if "index_status" in row.keys() else "pending",
            summary_status=row["summary_status"] if "summary_status" in row.keys() else ("ready" if row["summary"] else "pending"),
            publisher_source_id=row["publisher_source_id"] if "publisher_source_id" in row.keys() else None,
            publisher_source_name=row["publisher_source_name"] if "publisher_source_name" in row.keys() else None,
            story_group_id=row["story_group_id"] if "story_group_id" in row.keys() else None,
            story_dedupe_method=row["story_dedupe_method"] if "story_dedupe_method" in row.keys() else None,
            active_content_sha256=expected_hash,
            summary_content_sha256=row["summary_content_sha256"] if "summary_content_sha256" in row.keys() else None,
            indexed_content_sha256=row["indexed_content_sha256"] if "indexed_content_sha256" in row.keys() else None,
            content_validation_status=row["content_validation_status"] if "content_validation_status" in row.keys() else "pending",
            content_validation_reason=row["content_validation_reason"] if "content_validation_reason" in row.keys() else None,
            content_validator_version=row["content_validator_version"] if "content_validator_version" in row.keys() else None,
            final_response_url=row["final_response_url"] if "final_response_url" in row.keys() else None,
            response_status_code=row["response_status_code"] if "response_status_code" in row.keys() else None,
            response_content_type=row["response_content_type"] if "response_content_type" in row.keys() else None,
            extractor_version=row["extractor_version"] if "extractor_version" in row.keys() else None,
            extracted_char_count=row["extracted_char_count"] if "extracted_char_count" in row.keys() else None,
            quarantined_at=(
                datetime.fromisoformat(row["quarantined_at"])
                if "quarantined_at" in row.keys() and row["quarantined_at"]
                else None
            ),
            quarantine_reason=row["quarantine_reason"] if "quarantine_reason" in row.keys() else None,
            source_published_at=(
                datetime.fromisoformat(row["source_published_at"])
                if "source_published_at" in row.keys() and row["source_published_at"]
                else None
            ),
            published_at_provenance=(
                row["published_at_provenance"]
                if "published_at_provenance" in row.keys() and row["published_at_provenance"]
                else "legacy_unverified"
            ),
        )

    def _row_to_article_record(self, row: sqlite3.Row) -> ArticleRecord:
        article = self._row_to_article(row)
        return ArticleRecord(
            id=row["id"],
            article=article,
            structured_at=datetime.fromisoformat(row["structured_at"]) if row["structured_at"] else None,
            structuring_status=row["structuring_status"],
            structuring_error=row["structuring_error"],
            content_status=article.content_status,
            index_status=row["index_status"] if "index_status" in row.keys() else "pending",
            summary_status=row["summary_status"] if "summary_status" in row.keys() else ("ready" if row["summary"] else "pending"),
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


def _path_is_within(candidate: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(candidate), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:
        return False


def normalize_processing_status(status: str) -> str:
    normalized = " ".join((status or "").split()).strip().lower()
    if normalized not in {"pending", "ready", "failed"}:
        raise ValueError(f"Unsupported processing status: {status!r}")
    return normalized


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    normalized = parsed._replace(
        scheme=(parsed.scheme or "https").lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(query_pairs, doseq=True),
    )
    path = normalized.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    normalized = normalized._replace(path=path)
    return urlunparse(normalized)


def compute_content_sha256(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def normalize_story_title(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    for separator in (" reuters ", " associated press ", " business insider "):
        if normalized.endswith(separator.strip()):
            normalized = normalized[: -len(separator.strip())].strip()
    return normalized or "untitled"


def compute_story_key(normalized_title: str, publication_date: str) -> str:
    payload = f"title-date-v1\n{normalized_title}\n{publication_date}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
