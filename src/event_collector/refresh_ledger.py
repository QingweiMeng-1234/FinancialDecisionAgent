"""SQLite refresh-run and article-stage attempt ledger primitives.

Each public persistence function opens its own SQLite connection.  Claims use a
short ``BEGIN IMMEDIATE`` transaction and never encompass provider or vector
store work.  An expired lease is taken over on the same running run so already
persisted stage evidence remains attached to one execution identity.  Targeted
retry selection and parent/child retry-chain mechanics belong to WP5.

Attempt rows are append-only identities: callers may only transition their own
``running`` row once to a terminal status.  Rows are never reused or deleted by
this module.

Schema changes here apply to newly initialized ledgers only.  This prototype
does not provide or prove a migration path for already-created ledger DBs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping


RUNNING_STATUS = "running"
TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed"})
ATTEMPT_STAGES = frozenset({"content_fetch", "summary", "index", "reconcile"})
COLLECTION_RETRY_STAGE = "collection"
RETRY_POLICY_STAGES = ATTEMPT_STAGES | frozenset({COLLECTION_RETRY_STAGE})
TERMINAL_ATTEMPT_STATUSES = frozenset({"succeeded", "failed", "retry_scheduled"})
COLLECTION_FAILURE_CODES = frozenset(
    {
        "collector_error",
        "missing_api_key",
        "network_timeout",
        "network_connection_error",
        "unsupported_endpoint",
        "no_sources_available",
        "provider_invalid_response",
        "http_429",
        "http_5xx",
        "http_401_403",
        "http_4xx",
        "provider_error",
    }
)


@dataclass(frozen=True)
class RefreshClaim:
    disposition: str
    run_id: str
    attempt_no: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    resumed: bool = False


@dataclass(frozen=True)
class RetryTarget:
    target_id: str
    run_id: str
    source_attempt_id: str
    article_id: int
    stage: str
    input_content_sha256: str | None
    due_at: datetime
    chain_attempt_number: int


@dataclass(frozen=True)
class CollectionRetryTarget:
    """Immutable source-level collection work materialized for one retry child."""

    target_id: str
    run_id: str
    source_attempt_id: str
    source_name: str
    due_at: datetime
    chain_attempt_number: int
    provider_request: dict[str, Any] | None = None


@dataclass(frozen=True)
class RetryClaim:
    disposition: str
    run_id: str
    parent_run_id: str | None
    attempt_no: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    retry_after: datetime | None = None
    resumed: bool = False


@dataclass(frozen=True)
class RetrySchedule:
    """Read-only production polling fact for one scope with due child work."""

    scope_key: str
    requested_date: str
    due_at: datetime
    config_snapshot: dict[str, Any]


@dataclass(frozen=True)
class ProcessingAttempt:
    attempt_id: str
    run_id: str
    article_id: int
    stage: str
    attempt_number: int
    worker_id: str


@dataclass(frozen=True)
class TerminalAttemptObservation:
    status: str
    failure_code: str | None


class LedgerProcessingAttemptRecorder:
    """Ingestion-facing adapter for the durable article-stage attempt ledger.

    This module deliberately does not import the ingestion protocol: structural
    typing keeps the persistence boundary one-way.  The adapter records only a
    normalized failure category; provider exception text and retry scheduling
    are outside the WP4 attempt contract.
    """

    def __init__(
        self,
        *,
        ledger_path: str | os.PathLike[str],
        run_id: str,
        worker_id: str,
        clock: Callable[[], datetime],
        default_retry_delay_seconds: int = 60,
    ) -> None:
        _require_text(run_id, "run_id")
        _require_text(worker_id, "worker_id")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(default_retry_delay_seconds, bool)
            or not isinstance(default_retry_delay_seconds, int)
            or default_retry_delay_seconds <= 0
        ):
            raise ValueError("default_retry_delay_seconds must be a positive integer")
        self._ledger_path = ledger_path
        self._run_id = run_id
        self._worker_id = worker_id
        self._clock = clock
        self._default_retry_delay_seconds = default_retry_delay_seconds

    def begin_attempt(
        self,
        *,
        article_id: int,
        stage: str,
        input_content_sha256: str | None,
    ) -> ProcessingAttempt:
        """Append a running attempt using the adapter's current clock value."""
        return start_processing_attempt(
            self._ledger_path,
            run_id=self._run_id,
            article_id=article_id,
            stage=stage,
            worker_id=self._worker_id,
            started_at=self._clock(),
            input_content_sha256=input_content_sha256,
        )

    def finish_attempt(
        self,
        attempt: object,
        *,
        status: str,
        failure_code: str | None = None,
        retryable: bool = False,
        next_retry_at: datetime | None = None,
    ) -> bool:
        """CAS an owned attempt to a normalized terminal ingestion state."""
        if not isinstance(attempt, ProcessingAttempt):
            raise TypeError("attempt must be a ProcessingAttempt")
        if status not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError(f"Unsupported ingestion attempt status: {status!r}")
        normalized_failure_code = (
            _normalize_failure_code(failure_code)
            if status in {"failed", "retry_scheduled"}
            else None
        )
        finished_at = self._clock()
        if status == "failed" and retryable and next_retry_at is None:
            status = "retry_scheduled"
            next_retry_at = finished_at + timedelta(seconds=self._default_retry_delay_seconds)
        return finish_processing_attempt(
            self._ledger_path,
            attempt_id=attempt.attempt_id,
            worker_id=self._worker_id,
            status=status,
            finished_at=finished_at,
            failure_code=normalized_failure_code,
            error_detail=None,
            retryable=retryable,
            next_retry_at=next_retry_at,
        )


@dataclass(frozen=True)
class RefreshCounts:
    total_inputs: int = 0
    accepted_inputs: int = 0
    rejected_inputs: int = 0
    rejected_permanent_exclusions: int = 0
    unchanged_ready: int = 0
    content_ready: int = 0
    summary_ready: int = 0
    index_ready: int = 0
    usable_items: int = 0
    failed_items: int = 0
    permanent_exclusions: int = 0
    retryable_failures: int = 0
    source_errors: int = 0

    def __post_init__(self) -> None:
        _validate_nonnegative_counts(self)


@dataclass(frozen=True)
class RefreshArticleObservation:
    article_id: int
    content_sha256: str | None
    content_status: str
    summary_status: str
    permanent_exclusion: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.article_id, int) or isinstance(self.article_id, bool) or self.article_id <= 0:
            raise ValueError("article_id must be a positive integer")
        if self.content_sha256 is not None and not _is_sha256(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest or None")
        if self.content_status not in {"pending", "ready", "failed"}:
            raise ValueError("Unsupported content_status")
        if self.summary_status not in {"pending", "ready", "failed"}:
            raise ValueError("Unsupported summary_status")


@dataclass(frozen=True)
class CumulativeScopeState:
    articles: tuple[RefreshArticleObservation, ...]
    verified_index_articles: tuple[tuple[int, str], ...]
    root_counts: RefreshCounts
    root_collector_status: str
    root_empty_reason: str | None


@dataclass(frozen=True)
class RefreshOutcomeFacts:
    collector_status: str
    total_inputs: int
    accepted_inputs: int
    usable_items: int
    rejected_inputs: int = 0
    rejected_permanent_exclusions: int = 0
    content_failures: int = 0
    index_failures: int = 0
    summary_failures: int = 0
    permanent_exclusions: int = 0
    retryable_failures: int = 0
    source_errors: int = 0
    empty_reason: str | None = None

    def __post_init__(self) -> None:
        if self.collector_status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported collector_status: {self.collector_status!r}")
        _validate_nonnegative_counts(self, excluded={"empty_reason"})
        if self.accepted_inputs > self.total_inputs:
            raise ValueError("accepted_inputs cannot exceed total_inputs")
        if self.rejected_inputs > self.total_inputs - self.accepted_inputs:
            raise ValueError("accepted_inputs plus rejected_inputs cannot exceed total_inputs")
        if self.usable_items > self.accepted_inputs:
            raise ValueError("usable_items cannot exceed accepted_inputs")
        if self.permanent_exclusions > self.accepted_inputs:
            raise ValueError("permanent_exclusions cannot exceed accepted_inputs")
        if self.rejected_permanent_exclusions > self.rejected_inputs:
            raise ValueError("rejected_permanent_exclusions cannot exceed rejected_inputs")


def initialize_schema(db_path: str | os.PathLike[str]) -> None:
    """Create or upgrade the durable refresh ledger schema in place."""
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
                run_id TEXT PRIMARY KEY,
                parent_run_id TEXT,
                scope_key TEXT NOT NULL,
                requested_date TEXT NOT NULL,
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'partial', 'failed')),
                collector_status TEXT NOT NULL,
                total_inputs INTEGER NOT NULL DEFAULT 0 CHECK (total_inputs >= 0),
                accepted_inputs INTEGER NOT NULL DEFAULT 0 CHECK (accepted_inputs >= 0),
                rejected_inputs INTEGER NOT NULL DEFAULT 0 CHECK (rejected_inputs >= 0),
                rejected_permanent_exclusions INTEGER NOT NULL DEFAULT 0
                    CHECK (rejected_permanent_exclusions >= 0),
                unchanged_ready INTEGER NOT NULL DEFAULT 0 CHECK (unchanged_ready >= 0),
                content_ready INTEGER NOT NULL DEFAULT 0 CHECK (content_ready >= 0),
                summary_ready INTEGER NOT NULL DEFAULT 0 CHECK (summary_ready >= 0),
                index_ready INTEGER NOT NULL DEFAULT 0 CHECK (index_ready >= 0),
                usable_items INTEGER NOT NULL DEFAULT 0 CHECK (usable_items >= 0),
                failed_items INTEGER NOT NULL DEFAULT 0 CHECK (failed_items >= 0),
                permanent_exclusions INTEGER NOT NULL DEFAULT 0
                    CHECK (permanent_exclusions >= 0),
                retryable_failures INTEGER NOT NULL DEFAULT 0
                    CHECK (retryable_failures >= 0),
                source_errors INTEGER NOT NULL DEFAULT 0 CHECK (source_errors >= 0),
                empty_reason TEXT,
                lease_owner TEXT,
                lease_expires_at TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                config_snapshot TEXT NOT NULL,
                UNIQUE (scope_key, attempt_no),
                FOREIGN KEY(parent_run_id) REFERENCES refresh_runs(run_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_refresh_runs_running_scope
                ON refresh_runs(scope_key) WHERE status = 'running';
            CREATE UNIQUE INDEX IF NOT EXISTS uq_refresh_runs_completed_scope
                ON refresh_runs(scope_key) WHERE status = 'completed';

            CREATE TABLE IF NOT EXISTS refresh_retry_policies (
                scope_key TEXT PRIMARY KEY,
                max_attempts_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_generation_proofs (
                run_id TEXT PRIMARY KEY,
                corpus_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                corpus_snapshot_id TEXT NOT NULL,
                index_config_fingerprint TEXT NOT NULL,
                manifest_fingerprint TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS refresh_generation_proof_articles (
                run_id TEXT NOT NULL,
                article_id INTEGER NOT NULL,
                indexed_content_sha256 TEXT NOT NULL,
                PRIMARY KEY (run_id, article_id),
                FOREIGN KEY(run_id) REFERENCES refresh_generation_proofs(run_id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS refresh_article_observations (
                run_id TEXT NOT NULL,
                article_id INTEGER NOT NULL,
                content_sha256 TEXT,
                content_status TEXT NOT NULL
                    CHECK (content_status IN ('pending', 'ready', 'failed')),
                summary_status TEXT NOT NULL
                    CHECK (summary_status IN ('pending', 'ready', 'failed')),
                permanent_exclusion INTEGER NOT NULL CHECK (permanent_exclusion IN (0, 1)),
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (run_id, article_id),
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS article_processing_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                article_id INTEGER NOT NULL,
                stage TEXT NOT NULL
                    CHECK (stage IN ('content_fetch', 'summary', 'index', 'reconcile')),
                input_content_sha256 TEXT,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'succeeded', 'failed', 'retry_scheduled')),
                failure_code TEXT,
                error_detail TEXT,
                retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                next_retry_at TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                worker_id TEXT NOT NULL,
                UNIQUE (run_id, article_id, stage, attempt_number),
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id),
                CHECK (
                    (status = 'running' AND finished_at IS NULL)
                    OR (status != 'running' AND finished_at IS NOT NULL)
                ),
                CHECK (status NOT IN ('failed', 'retry_scheduled') OR failure_code IS NOT NULL),
                CHECK (
                    status != 'retry_scheduled'
                    OR (retryable = 1 AND next_retry_at IS NOT NULL)
                ),
                CHECK (
                    status != 'succeeded'
                    OR (failure_code IS NULL AND retryable = 0 AND next_retry_at IS NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS idx_processing_attempts_run_article_stage
                ON article_processing_attempts(run_id, article_id, stage, attempt_number);

            CREATE TABLE IF NOT EXISTS refresh_retry_targets (
                target_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_attempt_id TEXT NOT NULL UNIQUE,
                article_id INTEGER NOT NULL,
                stage TEXT NOT NULL
                    CHECK (stage IN ('content_fetch', 'summary', 'index', 'reconcile')),
                input_content_sha256 TEXT,
                due_at TEXT NOT NULL,
                chain_attempt_number INTEGER NOT NULL CHECK (chain_attempt_number > 1),
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id),
                FOREIGN KEY(source_attempt_id) REFERENCES article_processing_attempts(attempt_id),
                UNIQUE (run_id, article_id, stage)
            );

            CREATE INDEX IF NOT EXISTS idx_refresh_retry_targets_run
                ON refresh_retry_targets(run_id, due_at, article_id, stage);

            CREATE TRIGGER IF NOT EXISTS trg_refresh_retry_targets_no_update
            BEFORE UPDATE ON refresh_retry_targets
            BEGIN
                SELECT RAISE(ABORT, 'refresh retry targets are immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_refresh_retry_targets_no_delete
            BEFORE DELETE ON refresh_retry_targets
            BEGIN
                SELECT RAISE(ABORT, 'refresh retry targets are immutable');
            END;

            CREATE TABLE IF NOT EXISTS collection_retry_targets (
                target_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_attempt_id TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL,
                provider_request_json TEXT,
                due_at TEXT NOT NULL,
                chain_attempt_number INTEGER NOT NULL CHECK (chain_attempt_number > 1),
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id),
                FOREIGN KEY(source_attempt_id) REFERENCES collection_attempts(attempt_id),
                UNIQUE (run_id, source_name)
            );

            CREATE INDEX IF NOT EXISTS idx_collection_retry_targets_run
                ON collection_retry_targets(run_id, due_at, source_name);

            CREATE TRIGGER IF NOT EXISTS trg_collection_retry_targets_no_update
            BEFORE UPDATE ON collection_retry_targets
            BEGIN
                SELECT RAISE(ABORT, 'collection retry targets are immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_collection_retry_targets_no_delete
            BEFORE DELETE ON collection_retry_targets
            BEGIN
                SELECT RAISE(ABORT, 'collection retry targets are immutable');
            END;

            CREATE TABLE IF NOT EXISTS collection_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                provider_request_json TEXT,
                collector_status TEXT NOT NULL
                    CHECK (collector_status IN ('succeeded', 'failed')),
                source_row_count INTEGER NOT NULL CHECK (source_row_count >= 0),
                accepted_input_count INTEGER NOT NULL CHECK (accepted_input_count >= 0),
                rejected_source_row_count INTEGER NOT NULL
                    CHECK (rejected_source_row_count >= 0),
                empty_reason TEXT,
                failure_code TEXT,
                retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                next_retry_at TEXT,
                recorded_at TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES refresh_runs(run_id),
                CHECK (accepted_input_count + rejected_source_row_count = source_row_count),
                CHECK (
                    (collector_status = 'succeeded'
                        AND failure_code IS NULL AND retryable = 0)
                    OR (collector_status = 'failed'
                        AND failure_code IS NOT NULL AND empty_reason IS NULL)
                ),
                CHECK (
                    (collector_status = 'succeeded' AND source_row_count = 0
                        AND (empty_reason IS NULL OR empty_reason = 'no_matching_articles'))
                    OR (collector_status = 'succeeded' AND source_row_count > 0
                        AND empty_reason IS NULL)
                    OR (collector_status = 'failed' AND empty_reason IS NULL)
                ),
                CHECK (
                    (collector_status = 'failed' AND retryable = 1 AND next_retry_at IS NOT NULL)
                    OR (retryable = 0 AND next_retry_at IS NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS idx_collection_attempts_run_source_recorded
                ON collection_attempts(run_id, source_name, recorded_at);
            """
        )
        for table_name in ("collection_attempts", "collection_retry_targets"):
            existing_columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")
            }
            if "provider_request_json" not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN provider_request_json TEXT"
                )


def compute_scope_key(config: Mapping[str, Any]) -> str:
    """Hash a canonical JSON scope; mapping key order cannot change identity."""
    canonical = _canonical_json(config)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"refresh:sha256:{digest}"


def claim_refresh_run(
    db_path: str | os.PathLike[str],
    *,
    scope_key: str,
    requested_date: str,
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
    config_snapshot: Mapping[str, Any],
) -> RefreshClaim:
    """Claim one scope, observe its gate, or take over its expired lease."""
    _require_text(scope_key, "scope_key")
    _require_text(requested_date, "requested_date")
    _require_text(lease_owner, "lease_owner")
    now_text = _utc_text(now)
    expires_text = _utc_text(lease_expires_at)
    if expires_text <= now_text:
        raise ValueError("lease_expires_at must be later than now")
    config_text = _canonical_json(config_snapshot)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        completed = conn.execute(
            """
            SELECT run_id, attempt_no
            FROM refresh_runs
            WHERE scope_key = ? AND status = 'completed'
            """,
            (scope_key,),
        ).fetchone()
        if completed is not None:
            conn.commit()
            return RefreshClaim(
                disposition="already_completed",
                run_id=completed["run_id"],
                attempt_no=completed["attempt_no"],
                lease_owner=None,
                lease_expires_at=None,
            )

        running = conn.execute(
            """
            SELECT run_id, attempt_no, lease_owner, lease_expires_at
            FROM refresh_runs
            WHERE scope_key = ? AND status = 'running'
            """,
            (scope_key,),
        ).fetchone()
        if running is not None and running["lease_expires_at"] > now_text:
            conn.commit()
            return RefreshClaim(
                disposition="in_progress",
                run_id=running["run_id"],
                attempt_no=running["attempt_no"],
                lease_owner=running["lease_owner"],
                lease_expires_at=_parse_utc_text(running["lease_expires_at"]),
            )
        if running is not None:
            cursor = conn.execute(
                """
                UPDATE refresh_runs
                SET lease_owner = ?, lease_expires_at = ?
                WHERE run_id = ? AND status = 'running' AND lease_expires_at <= ?
                """,
                (lease_owner, expires_text, running["run_id"], now_text),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("Expired refresh lease could not be taken over")
            conn.commit()
            return RefreshClaim(
                disposition="claimed",
                run_id=running["run_id"],
                attempt_no=running["attempt_no"],
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at.astimezone(timezone.utc),
                resumed=True,
            )

        attempt_no = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM refresh_runs WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()[0]
        run_id = f"run_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO refresh_runs
            (run_id, scope_key, requested_date, attempt_no, status, collector_status,
             lease_owner, lease_expires_at, started_at, config_snapshot)
            VALUES (?, ?, ?, ?, 'running', 'running', ?, ?, ?, ?)
            """,
            (
                run_id,
                scope_key,
                requested_date,
                attempt_no,
                lease_owner,
                expires_text,
                now_text,
                config_text,
            ),
        )
        conn.commit()
        return RefreshClaim(
            disposition="claimed",
            run_id=run_id,
            attempt_no=attempt_no,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at.astimezone(timezone.utc),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_due_retry_run(
    db_path: str | os.PathLike[str],
    *,
    scope_key: str,
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
    max_attempts_by_stage: Mapping[str, int],
    max_targets: int = 100,
) -> RetryClaim:
    """Claim a due immutable retry batch or observe the current scope state.

    A terminal parent always produces a new child.  An expired running child is
    resumed in place so its materialized target snapshot cannot drift as more
    work becomes due.  Selection and child creation share one short write
    transaction; no external work is performed here.
    """
    _require_text(scope_key, "scope_key")
    _require_text(lease_owner, "lease_owner")
    if isinstance(max_targets, bool) or not isinstance(max_targets, int) or max_targets <= 0:
        raise ValueError("max_targets must be a positive integer")
    limits = _validate_attempt_limits(max_attempts_by_stage)
    now_text = _utc_text(now)
    expires_text = _utc_text(lease_expires_at)
    if expires_text <= now_text:
        raise ValueError("lease_expires_at must be later than now")

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        completed = conn.execute(
            "SELECT run_id, parent_run_id, attempt_no FROM refresh_runs "
            "WHERE scope_key = ? AND status = 'completed'",
            (scope_key,),
        ).fetchone()
        if completed is not None:
            conn.commit()
            return RetryClaim(
                "already_completed",
                completed["run_id"],
                completed["parent_run_id"],
                completed["attempt_no"],
                None,
                None,
            )

        policy_text = _canonical_json(limits)
        existing_policy = conn.execute(
            "SELECT max_attempts_json FROM refresh_retry_policies WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
        if existing_policy is None:
            conn.execute(
                "INSERT INTO refresh_retry_policies "
                "(scope_key, max_attempts_json, created_at) VALUES (?, ?, ?)",
                (scope_key, policy_text, now_text),
            )
        elif existing_policy["max_attempts_json"] != policy_text:
            raise ValueError("retry attempt policy cannot change within one scope")

        running = conn.execute(
            """
            SELECT run_id, parent_run_id, attempt_no, lease_owner, lease_expires_at
            FROM refresh_runs
            WHERE scope_key = ? AND status = 'running' AND parent_run_id IS NOT NULL
            """,
            (scope_key,),
        ).fetchone()
        if running is not None and running["lease_expires_at"] > now_text:
            conn.commit()
            return RetryClaim(
                "in_progress",
                running["run_id"],
                running["parent_run_id"],
                running["attempt_no"],
                running["lease_owner"],
                _parse_utc_text(running["lease_expires_at"]),
            )
        if running is not None:
            cursor = conn.execute(
                """
                UPDATE refresh_runs SET lease_owner = ?, lease_expires_at = ?
                WHERE run_id = ? AND status = 'running' AND lease_expires_at <= ?
                """,
                (lease_owner, expires_text, running["run_id"], now_text),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("Expired retry lease could not be taken over")
            conn.commit()
            return RetryClaim(
                "claimed",
                running["run_id"],
                running["parent_run_id"],
                running["attempt_no"],
                lease_owner,
                lease_expires_at.astimezone(timezone.utc),
                resumed=True,
            )

        parent = conn.execute(
            """
            SELECT * FROM refresh_runs
            WHERE scope_key = ? AND status IN ('partial', 'failed')
            ORDER BY attempt_no DESC LIMIT 1
            """,
            (scope_key,),
        ).fetchone()
        if parent is None:
            conn.commit()
            return RetryClaim("retry_unavailable", "", None, 0, None, None)

        latest_article_rows = conn.execute(
            """
            WITH ranked AS (
                SELECT a.*, r.attempt_no AS run_attempt_no,
                       COUNT(*) OVER (
                           PARTITION BY a.article_id, a.stage
                       ) AS chain_attempt_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY a.article_id, a.stage
                           ORDER BY r.attempt_no DESC, a.attempt_number DESC,
                                    a.finished_at DESC, a.attempt_id DESC
                       ) AS latest_rank
                FROM article_processing_attempts AS a
                JOIN refresh_runs AS r ON r.run_id = a.run_id
                WHERE r.scope_key = ? AND a.status != 'running'
            )
            SELECT * FROM ranked
            WHERE latest_rank = 1 AND status = 'retry_scheduled' AND retryable = 1
            ORDER BY next_retry_at, article_id, stage
            """,
            (scope_key,),
        ).fetchall()
        eligible_article_rows = [
            row
            for row in latest_article_rows
            if row["stage"] in limits and row["chain_attempt_count"] < limits[row["stage"]]
        ]
        latest_collection_rows = conn.execute(
            """
            WITH ranked AS (
                SELECT c.*, r.attempt_no AS run_attempt_no,
                       COUNT(*) OVER (
                           PARTITION BY c.source_name
                       ) AS chain_attempt_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.source_name
                           ORDER BY r.attempt_no DESC, c.recorded_at DESC, c.attempt_id DESC
                       ) AS latest_rank
                FROM collection_attempts AS c
                JOIN refresh_runs AS r ON r.run_id = c.run_id
                WHERE r.scope_key = ?
            )
            SELECT * FROM ranked
            WHERE latest_rank = 1 AND collector_status = 'failed'
              AND retryable = 1 AND next_retry_at IS NOT NULL
            ORDER BY next_retry_at, source_name
            """,
            (scope_key,),
        ).fetchall()
        eligible_collection_rows = [
            row
            for row in latest_collection_rows
            if (
                COLLECTION_RETRY_STAGE in limits
                and row["chain_attempt_count"] < limits[COLLECTION_RETRY_STAGE]
            )
        ]
        candidates = [
            (row["next_retry_at"], "article", row) for row in eligible_article_rows
        ] + [
            (row["next_retry_at"], "collection", row) for row in eligible_collection_rows
        ]
        candidates.sort(key=lambda item: (item[0], item[1], item[2]["attempt_id"]))
        due = [candidate for candidate in candidates if candidate[0] <= now_text][:max_targets]
        if not due:
            future = [candidate[0] for candidate in candidates if candidate[0] > now_text]
            disposition = "retry_scheduled" if future else "retry_exhausted"
            retry_after = _parse_utc_text(min(future)) if future else None
            conn.commit()
            return RetryClaim(
                disposition,
                parent["run_id"],
                parent["parent_run_id"],
                parent["attempt_no"],
                None,
                None,
                retry_after=retry_after,
            )

        attempt_no = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM refresh_runs WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()[0]
        run_id = f"run_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO refresh_runs
            (run_id, parent_run_id, scope_key, requested_date, attempt_no, status,
             collector_status, lease_owner, lease_expires_at, started_at, config_snapshot)
            VALUES (?, ?, ?, ?, ?, 'running', 'running', ?, ?, ?, ?)
            """,
            (
                run_id,
                parent["run_id"],
                scope_key,
                parent["requested_date"],
                attempt_no,
                lease_owner,
                expires_text,
                now_text,
                parent["config_snapshot"],
            ),
        )
        for _, target_kind, row in due:
            if target_kind == "article":
                conn.execute(
                    """
                    INSERT INTO refresh_retry_targets
                    (target_id, run_id, source_attempt_id, article_id, stage,
                     input_content_sha256, due_at, chain_attempt_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"retry_target_{uuid.uuid4().hex}",
                        run_id,
                        row["attempt_id"],
                        row["article_id"],
                        row["stage"],
                        row["input_content_sha256"],
                        row["next_retry_at"],
                        row["chain_attempt_count"] + 1,
                    ),
                )
                continue
            conn.execute(
                """
                INSERT INTO collection_retry_targets
                (target_id, run_id, source_attempt_id, source_name, provider_request_json, due_at,
                 chain_attempt_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"collection_retry_target_{uuid.uuid4().hex}",
                    run_id,
                    row["attempt_id"],
                    row["source_name"],
                    row["provider_request_json"],
                    row["next_retry_at"],
                    row["chain_attempt_count"] + 1,
                ),
            )
        conn.commit()
        return RetryClaim(
            "claimed",
            run_id,
            parent["run_id"],
            attempt_no,
            lease_owner,
            lease_expires_at.astimezone(timezone.utc),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_retry_targets(
    db_path: str | os.PathLike[str], run_id: str
) -> tuple[RetryTarget, ...]:
    """Read the immutable target snapshot materialized for one retry child."""
    _require_text(run_id, "run_id")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM refresh_retry_targets
            WHERE run_id = ? ORDER BY due_at, article_id, stage, target_id
            """,
            (run_id,),
        ).fetchall()
    return tuple(
        RetryTarget(
            row["target_id"],
            row["run_id"],
            row["source_attempt_id"],
            row["article_id"],
            row["stage"],
            row["input_content_sha256"],
            _parse_utc_text(row["due_at"]),
            row["chain_attempt_number"],
        )
        for row in rows
    )


def list_collection_retry_targets(
    db_path: str | os.PathLike[str], run_id: str
) -> tuple[CollectionRetryTarget, ...]:
    """Read the immutable source-level collection targets for one retry child."""
    _require_text(run_id, "run_id")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM collection_retry_targets
            WHERE run_id = ? ORDER BY due_at, source_name, target_id
            """,
            (run_id,),
        ).fetchall()
    return tuple(
        CollectionRetryTarget(
            row["target_id"],
            row["run_id"],
            row["source_attempt_id"],
            row["source_name"],
            _parse_utc_text(row["due_at"]),
            row["chain_attempt_number"],
            json.loads(row["provider_request_json"])
            if row["provider_request_json"] is not None
            else None,
        )
        for row in rows
    )


def list_due_retry_schedules(
    db_path: str | os.PathLike[str],
    *,
    now: datetime,
    limit: int = 100,
) -> tuple[RetrySchedule, ...]:
    """Discover due retry scopes without claiming or mutating their ledgers.

    The claim operation remains the concurrency boundary.  This inventory is
    deliberately allowed to become stale between read and dispatch; a racing
    worker will then observe ``in_progress`` or ``already_completed`` when it
    calls :func:`claim_due_retry_run`.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    now_text = _utc_text(now)
    if not os.path.exists(os.fspath(db_path)):
        return ()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run.scope_key, run.requested_date, run.config_snapshot,
                   run.run_id, run.parent_run_id, run.status,
                   run.lease_expires_at
            FROM refresh_runs AS run
            WHERE run.attempt_no = (
                SELECT MAX(candidate.attempt_no)
                FROM refresh_runs AS candidate
                WHERE candidate.scope_key = run.scope_key
            )
              AND NOT EXISTS (
                  SELECT 1 FROM refresh_runs AS completed
                  WHERE completed.scope_key = run.scope_key
                    AND completed.status = 'completed'
              )
            ORDER BY run.scope_key
            """
        ).fetchall()
        schedules: list[RetrySchedule] = []
        for row in rows:
            due_at: str | None = None
            if row["status"] == RUNNING_STATUS:
                # Only an expired child is retry scheduler work.  An expired
                # root remains owned by the explicit root-refresh entrypoint.
                if (
                    row["parent_run_id"] is not None
                    and row["lease_expires_at"] is not None
                    and row["lease_expires_at"] <= now_text
                ):
                    due_at = row["lease_expires_at"]
            elif row["status"] in {"partial", "failed"}:
                policy_row = conn.execute(
                    "SELECT max_attempts_json FROM refresh_retry_policies WHERE scope_key = ?",
                    (row["scope_key"],),
                ).fetchone()
                if policy_row is None:
                    continue
                limits = json.loads(policy_row["max_attempts_json"])
                article_rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT attempt.stage, attempt.status, attempt.retryable,
                               attempt.next_retry_at,
                               COUNT(*) OVER (
                                   PARTITION BY attempt.article_id, attempt.stage
                               ) AS chain_attempt_count,
                               ROW_NUMBER() OVER (
                                   PARTITION BY attempt.article_id, attempt.stage
                                   ORDER BY run.attempt_no DESC,
                                            attempt.attempt_number DESC,
                                            attempt.finished_at DESC,
                                            attempt.attempt_id DESC
                               ) AS latest_rank
                        FROM article_processing_attempts AS attempt
                        JOIN refresh_runs AS run ON run.run_id = attempt.run_id
                        WHERE run.scope_key = ? AND attempt.status != 'running'
                    )
                    SELECT * FROM ranked
                    WHERE latest_rank = 1 AND status = 'retry_scheduled'
                      AND retryable = 1 AND next_retry_at IS NOT NULL
                    """,
                    (row["scope_key"],),
                ).fetchall()
                collection_rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT attempt.retryable, attempt.next_retry_at,
                               COUNT(*) OVER (
                                   PARTITION BY attempt.source_name
                               ) AS chain_attempt_count,
                               ROW_NUMBER() OVER (
                                   PARTITION BY attempt.source_name
                                   ORDER BY run.attempt_no DESC,
                                            attempt.recorded_at DESC,
                                            attempt.attempt_id DESC
                               ) AS latest_rank
                        FROM collection_attempts AS attempt
                        JOIN refresh_runs AS run ON run.run_id = attempt.run_id
                        WHERE run.scope_key = ?
                    )
                    SELECT * FROM ranked
                    WHERE latest_rank = 1 AND retryable = 1
                      AND next_retry_at IS NOT NULL
                    """,
                    (row["scope_key"],),
                ).fetchall()
                due_candidates = [
                    attempt["next_retry_at"]
                    for attempt in article_rows
                    if (
                        attempt["stage"] in limits
                        and attempt["chain_attempt_count"] < limits[attempt["stage"]]
                        and attempt["next_retry_at"] <= now_text
                    )
                ]
                due_candidates.extend(
                    attempt["next_retry_at"]
                    for attempt in collection_rows
                    if (
                        COLLECTION_RETRY_STAGE in limits
                        and attempt["chain_attempt_count"]
                        < limits[COLLECTION_RETRY_STAGE]
                        and attempt["next_retry_at"] <= now_text
                    )
                )
                if due_candidates:
                    due_at = min(due_candidates)
            if due_at is None:
                continue
            config_snapshot = json.loads(row["config_snapshot"])
            if not isinstance(config_snapshot, dict):
                continue
            schedules.append(
                RetrySchedule(
                    scope_key=row["scope_key"],
                    requested_date=row["requested_date"],
                    due_at=_parse_utc_text(due_at),
                    config_snapshot=config_snapshot,
                )
            )
        schedules.sort(key=lambda item: (item.due_at, item.scope_key))
        return tuple(schedules[:limit])


def read_retry_target_terminal_attempt(
    db_path: str | os.PathLike[str], target: RetryTarget
) -> TerminalAttemptObservation | None:
    """Return this child's terminal attempt for a target, if already recorded."""
    if not isinstance(target, RetryTarget):
        raise TypeError("target must be a RetryTarget")
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, failure_code
            FROM article_processing_attempts
            WHERE run_id = ? AND article_id = ? AND stage = ? AND status != 'running'
            ORDER BY attempt_number DESC, finished_at DESC, attempt_id DESC
            LIMIT 1
            """,
            (target.run_id, target.article_id, target.stage),
        ).fetchone()
    if row is None:
        return None
    return TerminalAttemptObservation(row["status"], row["failure_code"])


def renew_refresh_lease(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
) -> bool:
    """Extend one still-live owner lease using a short compare-and-set write."""
    _require_text(run_id, "run_id")
    _require_text(lease_owner, "lease_owner")
    now_text = _utc_text(now)
    expires_text = _utc_text(lease_expires_at)
    if expires_text <= now_text:
        raise ValueError("lease_expires_at must be later than now")
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE refresh_runs SET lease_expires_at = ?
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (expires_text, run_id, lease_owner, now_text),
        )
        return cursor.rowcount == 1


def finalize_retry_run_from_chain(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    lease_owner: str,
    cumulative_counts: RefreshCounts,
    finished_at: datetime,
) -> str:
    """Finalize a child using latest evidence across its entire scope chain.

    ``cumulative_counts`` is the caller's reconciliation of the durable article
    store and generation proof.  The ledger independently refuses a completed
    gate while any latest retryable stage or collection failure remains.
    """
    _require_text(run_id, "run_id")
    _require_text(lease_owner, "lease_owner")
    if not isinstance(cumulative_counts, RefreshCounts):
        raise TypeError("cumulative_counts must be RefreshCounts")
    finished_text = _utc_text(finished_at)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            """
            SELECT run_id, scope_key FROM refresh_runs
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (run_id, lease_owner, finished_text),
        ).fetchone()
        if run is None:
            raise PermissionError("Worker does not own an active retry run lease")
        incomplete_article_target = conn.execute(
            """
            SELECT 1
            FROM refresh_retry_targets AS target
            WHERE target.run_id = ?
              AND NOT EXISTS (
                    SELECT 1 FROM article_processing_attempts AS attempt
                    WHERE attempt.run_id = target.run_id
                      AND attempt.article_id = target.article_id
                      AND attempt.stage = target.stage
                      AND attempt.status != 'running'
              )
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        incomplete_collection_target = conn.execute(
            """
            SELECT 1
            FROM collection_retry_targets AS target
            WHERE target.run_id = ?
              AND NOT EXISTS (
                    SELECT 1 FROM collection_attempts AS attempt
                    WHERE attempt.run_id = target.run_id
                      AND attempt.source_name = target.source_name
              )
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if incomplete_article_target is not None or incomplete_collection_target is not None:
            raise ValueError("retry child cannot finalize before every target is terminal")

        policy_row = conn.execute(
            "SELECT max_attempts_json FROM refresh_retry_policies WHERE scope_key = ?",
            (run["scope_key"],),
        ).fetchone()
        limits = json.loads(policy_row["max_attempts_json"]) if policy_row is not None else {}
        latest_attempts = conn.execute(
            """
            WITH ranked AS (
                SELECT a.stage, a.status, a.retryable, a.article_id,
                       COUNT(*) OVER (
                           PARTITION BY a.article_id, a.stage
                       ) AS chain_attempt_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY a.article_id, a.stage
                           ORDER BY r.attempt_no DESC, a.attempt_number DESC,
                                    a.finished_at DESC, a.attempt_id DESC
                       ) AS latest_rank
                FROM article_processing_attempts AS a
                JOIN refresh_runs AS r ON r.run_id = a.run_id
                WHERE r.scope_key = ? AND a.status != 'running'
            )
            SELECT * FROM ranked WHERE latest_rank = 1
            """,
            (run["scope_key"],),
        ).fetchall()
        outstanding = sum(
            1
            for row in latest_attempts
            if row["retryable"] == 1
            and row["status"] in {"failed", "retry_scheduled"}
            and row["chain_attempt_count"] < limits.get(row["stage"], 0)
        )

        latest_collections = conn.execute(
            """
            WITH ranked AS (
                SELECT c.collector_status AS collector_status, c.source_name AS source_name,
                       c.source_row_count AS source_row_count,
                       c.accepted_input_count AS accepted_input_count,
                       c.rejected_source_row_count AS rejected_source_row_count,
                       c.empty_reason AS empty_reason,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_name
                           ORDER BY r.attempt_no DESC, c.recorded_at DESC, c.attempt_id DESC
                       ) AS latest_rank
                FROM collection_attempts AS c
                JOIN refresh_runs AS r ON r.run_id = c.run_id
                WHERE r.scope_key = ?
            )
            SELECT collector_status, source_row_count, accepted_input_count,
                   rejected_source_row_count, empty_reason
            FROM ranked WHERE latest_rank = 1
            """,
            (run["scope_key"],),
        ).fetchall()
        source_errors = sum(row["collector_status"] == "failed" for row in latest_collections)
        counts = replace(
            cumulative_counts,
            retryable_failures=max(cumulative_counts.retryable_failures, outstanding),
            source_errors=max(cumulative_counts.source_errors, source_errors),
        )
        verified_empty = (
            counts.total_inputs == 0
            and counts.accepted_inputs == 0
            and counts.rejected_inputs == 0
            and counts.usable_items == 0
            and counts.failed_items == 0
            and counts.permanent_exclusions == 0
            and counts.retryable_failures == 0
            and counts.source_errors == 0
            and bool(latest_collections)
            and all(
                row["collector_status"] == "succeeded"
                and row["source_row_count"] == 0
                and row["accepted_input_count"] == 0
                and row["rejected_source_row_count"] == 0
                and row["empty_reason"] == "no_matching_articles"
                for row in latest_collections
            )
        )
        unresolved_counts = (
            counts.retryable_failures > 0
            or counts.failed_items > counts.permanent_exclusions
            or (counts.usable_items == 0 and not verified_empty)
        )
        status = (
            "partial"
            if counts.usable_items > 0
            and (outstanding > 0 or source_errors > 0 or unresolved_counts)
            else "failed"
            if outstanding > 0 or source_errors > 0 or unresolved_counts
            else "completed"
        )
        terminal_empty_reason = "no_matching_articles" if verified_empty else None
        if status == "completed":
            _validate_terminal_contract(status, "succeeded", counts, terminal_empty_reason)
        values = [getattr(counts, field.name) for field in fields(counts)]
        cursor = conn.execute(
            """
            UPDATE refresh_runs
            SET status = ?, collector_status = 'succeeded',
                total_inputs = ?, accepted_inputs = ?, rejected_inputs = ?,
                rejected_permanent_exclusions = ?,
                unchanged_ready = ?, content_ready = ?, summary_ready = ?, index_ready = ?,
                usable_items = ?, failed_items = ?, permanent_exclusions = ?,
                retryable_failures = ?, source_errors = ?, empty_reason = ?,
                finished_at = ?, lease_owner = NULL, lease_expires_at = NULL
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (
                status,
                *values,
                terminal_empty_reason,
                finished_text,
                run_id,
                lease_owner,
                finished_text,
            ),
        )
        if cursor.rowcount != 1:
            raise PermissionError("Retry run finalization lost its lease CAS")
        conn.commit()
        return status
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finalize_refresh_run(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    lease_owner: str,
    status: str,
    collector_status: str,
    counts: RefreshCounts,
    finished_at: datetime,
    empty_reason: str | None = None,
) -> bool:
    """CAS one owner-held running run to one immutable terminal state."""
    _require_text(run_id, "run_id")
    _require_text(lease_owner, "lease_owner")
    _require_text(collector_status, "collector_status")
    if status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"Unsupported terminal refresh status: {status!r}")
    if not isinstance(counts, RefreshCounts):
        raise TypeError("counts must be RefreshCounts")
    _validate_terminal_contract(status, collector_status, counts, empty_reason)
    finished_text = _utc_text(finished_at)
    values = [getattr(counts, field.name) for field in fields(counts)]

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE refresh_runs
            SET status = ?, collector_status = ?,
                total_inputs = ?, accepted_inputs = ?, rejected_inputs = ?,
                rejected_permanent_exclusions = ?,
                unchanged_ready = ?, content_ready = ?, summary_ready = ?, index_ready = ?,
                usable_items = ?, failed_items = ?, permanent_exclusions = ?,
                retryable_failures = ?, source_errors = ?, empty_reason = ?, finished_at = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (
                status,
                collector_status,
                *values,
                empty_reason,
                finished_text,
                run_id,
                lease_owner,
                finished_text,
            ),
        )
        return cursor.rowcount == 1


def record_refresh_generation_proof(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    lease_owner: str,
    corpus_id: str,
    generation_id: str,
    corpus_snapshot_id: str,
    index_config_fingerprint: str,
    manifest: Mapping[int, str],
    recorded_at: datetime,
) -> bool:
    """Bind one owner-held refresh run to one verified generation identity."""
    for value, name in (
        (run_id, "run_id"),
        (lease_owner, "lease_owner"),
        (corpus_id, "corpus_id"),
        (generation_id, "generation_id"),
        (corpus_snapshot_id, "corpus_snapshot_id"),
        (index_config_fingerprint, "index_config_fingerprint"),
    ):
        _require_text(value, name)
    if not _is_sha256(index_config_fingerprint):
        raise ValueError("index_config_fingerprint must be a lowercase SHA-256 digest")
    normalized_manifest: list[tuple[int, str]] = []
    for article_id, content_sha256 in manifest.items():
        if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id <= 0:
            raise ValueError("manifest article IDs must be positive integers")
        if not _is_sha256(content_sha256):
            raise ValueError("manifest content hashes must be lowercase SHA-256 digests")
        normalized_manifest.append((article_id, content_sha256))
    if not normalized_manifest:
        raise ValueError("generation proof manifest cannot be empty")
    normalized_manifest.sort()
    manifest_json = json.dumps(normalized_manifest, separators=(",", ":"))
    manifest_fingerprint = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    recorded_text = _utc_text(recorded_at)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        owned = conn.execute(
            """
            SELECT 1 FROM refresh_runs
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (run_id, lease_owner, recorded_text),
        ).fetchone()
        if owned is None:
            conn.commit()
            return False
        conn.execute(
            """
            INSERT INTO refresh_generation_proofs (
                run_id, corpus_id, generation_id, corpus_snapshot_id,
                index_config_fingerprint, manifest_fingerprint, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                corpus_id,
                generation_id,
                corpus_snapshot_id,
                index_config_fingerprint,
                manifest_fingerprint,
                recorded_text,
            ),
        )
        conn.executemany(
            """
            INSERT INTO refresh_generation_proof_articles
            (run_id, article_id, indexed_content_sha256) VALUES (?, ?, ?)
            """,
            ((run_id, article_id, content_hash) for article_id, content_hash in normalized_manifest),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_refresh_article_observations(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    lease_owner: str,
    observations: tuple[RefreshArticleObservation, ...],
    recorded_at: datetime,
) -> bool:
    """Append one per-article scope observation for an owner-held run."""
    _require_text(run_id, "run_id")
    _require_text(lease_owner, "lease_owner")
    if not isinstance(observations, tuple) or any(
        not isinstance(item, RefreshArticleObservation) for item in observations
    ):
        raise TypeError("observations must be a tuple of RefreshArticleObservation")
    if len({item.article_id for item in observations}) != len(observations):
        raise ValueError("observations cannot repeat an article_id within one run")
    recorded_text = _utc_text(recorded_at)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        owned = conn.execute(
            """
            SELECT 1 FROM refresh_runs
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (run_id, lease_owner, recorded_text),
        ).fetchone()
        if owned is None:
            conn.commit()
            return False
        conn.executemany(
            """
            INSERT INTO refresh_article_observations
            (run_id, article_id, content_sha256, content_status, summary_status,
             permanent_exclusion, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    run_id,
                    item.article_id,
                    item.content_sha256,
                    item.content_status,
                    item.summary_status,
                    int(item.permanent_exclusion),
                    recorded_text,
                )
                for item in observations
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_cumulative_scope_state(
    db_path: str | os.PathLike[str], scope_key: str
) -> CumulativeScopeState:
    """Read latest per-article facts and the latest verified manifest in a scope."""
    _require_text(scope_key, "scope_key")
    with _connect(db_path) as conn:
        root = conn.execute(
            """
            SELECT * FROM refresh_runs
            WHERE scope_key = ? AND status != 'running'
            ORDER BY attempt_no DESC LIMIT 1
            """,
            (scope_key,),
        ).fetchone()
        article_rows = conn.execute(
            """
            WITH ranked AS (
                SELECT observation.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY observation.article_id
                           ORDER BY run.attempt_no DESC, observation.recorded_at DESC
                       ) AS latest_rank
                FROM refresh_article_observations AS observation
                JOIN refresh_runs AS run ON run.run_id = observation.run_id
                WHERE run.scope_key = ?
            )
            SELECT * FROM ranked WHERE latest_rank = 1 ORDER BY article_id
            """,
            (scope_key,),
        ).fetchall()
        proof = conn.execute(
            """
            SELECT proof.run_id
            FROM refresh_generation_proofs AS proof
            JOIN refresh_runs AS run ON run.run_id = proof.run_id
            WHERE run.scope_key = ?
            ORDER BY run.attempt_no DESC, proof.recorded_at DESC LIMIT 1
            """,
            (scope_key,),
        ).fetchone()
        manifest_rows = (
            conn.execute(
                """
                SELECT article_id, indexed_content_sha256
                FROM refresh_generation_proof_articles
                WHERE run_id = ? ORDER BY article_id
                """,
                (proof["run_id"],),
            ).fetchall()
            if proof is not None
            else []
        )
    return CumulativeScopeState(
        articles=tuple(
            RefreshArticleObservation(
                row["article_id"],
                row["content_sha256"],
                row["content_status"],
                row["summary_status"],
                bool(row["permanent_exclusion"]),
            )
            for row in article_rows
        ),
        verified_index_articles=tuple(
            (row["article_id"], row["indexed_content_sha256"]) for row in manifest_rows
        ),
        root_counts=(
            RefreshCounts(
                total_inputs=root["total_inputs"],
                accepted_inputs=root["accepted_inputs"],
                rejected_inputs=root["rejected_inputs"],
                rejected_permanent_exclusions=root["rejected_permanent_exclusions"],
                unchanged_ready=root["unchanged_ready"],
                content_ready=root["content_ready"],
                summary_ready=root["summary_ready"],
                index_ready=root["index_ready"],
                usable_items=root["usable_items"],
                failed_items=root["failed_items"],
                permanent_exclusions=root["permanent_exclusions"],
                retryable_failures=root["retryable_failures"],
                source_errors=root["source_errors"],
            )
            if root is not None
            else RefreshCounts()
        ),
        root_collector_status=(root["collector_status"] if root is not None else "failed"),
        root_empty_reason=(root["empty_reason"] if root is not None else None),
    )


def start_processing_attempt(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    article_id: int,
    stage: str,
    worker_id: str,
    started_at: datetime,
    input_content_sha256: str | None = None,
) -> ProcessingAttempt:
    """Append a running article-stage attempt for the current run owner."""
    _require_text(run_id, "run_id")
    _require_text(worker_id, "worker_id")
    if stage not in ATTEMPT_STAGES:
        raise ValueError(f"Unsupported processing stage: {stage!r}")
    if not isinstance(article_id, int) or article_id <= 0:
        raise ValueError("article_id must be a positive integer")
    started_text = _utc_text(started_at)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        owned_run = conn.execute(
            """
            SELECT 1 FROM refresh_runs
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (run_id, worker_id, started_text),
        ).fetchone()
        if owned_run is None:
            raise PermissionError("Worker does not own an active refresh run lease")
        attempt_number = conn.execute(
            """
            SELECT COALESCE(MAX(attempt_number), 0) + 1
            FROM article_processing_attempts
            WHERE run_id = ? AND article_id = ? AND stage = ?
            """,
            (run_id, article_id, stage),
        ).fetchone()[0]
        attempt_id = f"attempt_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO article_processing_attempts
            (attempt_id, run_id, article_id, stage, input_content_sha256, status,
             attempt_number, started_at, worker_id)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                attempt_id,
                run_id,
                article_id,
                stage,
                input_content_sha256,
                attempt_number,
                started_text,
                worker_id,
            ),
        )
        conn.commit()
        return ProcessingAttempt(
            attempt_id=attempt_id,
            run_id=run_id,
            article_id=article_id,
            stage=stage,
            attempt_number=attempt_number,
            worker_id=worker_id,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_processing_attempt(
    db_path: str | os.PathLike[str],
    *,
    attempt_id: str,
    worker_id: str,
    status: str,
    finished_at: datetime,
    failure_code: str | None = None,
    error_detail: str | None = None,
    retryable: bool = False,
    next_retry_at: datetime | None = None,
) -> bool:
    """CAS one running attempt to terminal if its worker still owns the run."""
    _require_text(attempt_id, "attempt_id")
    _require_text(worker_id, "worker_id")
    if status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError(f"Unsupported terminal attempt status: {status!r}")
    if status in {"failed", "retry_scheduled"} and not (failure_code or "").strip():
        raise ValueError(f"{status} attempts require a non-empty failure_code")
    if status == "succeeded" and (failure_code or error_detail or retryable or next_retry_at):
        raise ValueError("succeeded attempts cannot include failure or retry fields")
    if status == "retry_scheduled" and (not retryable or next_retry_at is None):
        raise ValueError("retry_scheduled attempts require retryable=True and next_retry_at")

    finished_text = _utc_text(finished_at)
    next_retry_text = _utc_text(next_retry_at) if next_retry_at is not None else None
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE article_processing_attempts
            SET status = ?, failure_code = ?, error_detail = ?, retryable = ?,
                next_retry_at = ?, finished_at = ?
            WHERE attempt_id = ? AND status = 'running' AND worker_id = ?
              AND EXISTS (
                    SELECT 1 FROM refresh_runs
                    WHERE refresh_runs.run_id = article_processing_attempts.run_id
                      AND refresh_runs.status = 'running'
                      AND refresh_runs.lease_owner = ?
                      AND refresh_runs.lease_expires_at > ?
              )
            """,
            (
                status,
                failure_code,
                error_detail,
                int(bool(retryable)),
                next_retry_text,
                finished_text,
                attempt_id,
                worker_id,
                worker_id,
                finished_text,
            ),
        )
        return cursor.rowcount == 1


def record_collection_attempt(
    db_path: str | os.PathLike[str],
    *,
    run_id: str,
    source_name: str,
    collector_status: str,
    source_row_count: int,
    accepted_input_count: int,
    rejected_source_row_count: int,
    empty_reason: str | None,
    failure_code: str | None,
    retryable: bool,
    recorded_at: datetime,
    worker_id: str,
    next_retry_at: datetime | None = None,
    default_retry_delay_seconds: int = 60,
    provider_request: Mapping[str, Any] | None = None,
) -> bool:
    """Append one terminal source invocation if the caller still owns the lease.

    The function records a normalized, allow-listed failure category only.  It
    intentionally has no exception-detail parameter, so raw provider payloads
    or secrets cannot enter this audit table through this API.
    """
    _require_text(run_id, "run_id")
    _require_text(source_name, "source_name")
    _require_text(worker_id, "worker_id")
    _validate_collection_attempt_contract(
        collector_status=collector_status,
        source_row_count=source_row_count,
        accepted_input_count=accepted_input_count,
        rejected_source_row_count=rejected_source_row_count,
        empty_reason=empty_reason,
        failure_code=failure_code,
        retryable=retryable,
    )
    recorded_text = _utc_text(recorded_at)
    safe_failure_code = failure_code if collector_status == "failed" else None
    provider_request_text = (
        _canonical_json(provider_request) if provider_request is not None else None
    )
    if (
        isinstance(default_retry_delay_seconds, bool)
        or not isinstance(default_retry_delay_seconds, int)
        or default_retry_delay_seconds <= 0
    ):
        raise ValueError("default_retry_delay_seconds must be a positive integer")
    if next_retry_at is not None:
        next_retry_text = _utc_text(next_retry_at)
    else:
        next_retry_text = None
    if collector_status == "failed" and retryable:
        if next_retry_text is None:
            next_retry_text = _utc_text(
                recorded_at.astimezone(timezone.utc)
                + timedelta(seconds=default_retry_delay_seconds)
            )
        if next_retry_text <= recorded_text:
            raise ValueError("next_retry_at must be later than recorded_at")
    elif next_retry_text is not None:
        raise ValueError("only retryable failed collection attempts may include next_retry_at")

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        owned_run = conn.execute(
            """
            SELECT 1 FROM refresh_runs
            WHERE run_id = ? AND status = 'running' AND lease_owner = ?
              AND lease_expires_at > ?
            """,
            (run_id, worker_id, recorded_text),
        ).fetchone()
        if owned_run is None:
            conn.commit()
            return False
        conn.execute(
            """
            INSERT INTO collection_attempts
            (attempt_id, run_id, source_name, provider_request_json, collector_status, source_row_count,
             accepted_input_count, rejected_source_row_count, empty_reason,
             failure_code, retryable, next_retry_at, recorded_at, worker_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"collection_{uuid.uuid4().hex}",
                run_id,
                source_name,
                provider_request_text,
                collector_status,
                source_row_count,
                accepted_input_count,
                rejected_source_row_count,
                empty_reason,
                safe_failure_code,
                int(retryable),
                next_retry_text,
                recorded_text,
                worker_id,
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def classify_refresh_outcome(facts: RefreshOutcomeFacts) -> str:
    """Derive a fail-closed refresh terminal status from aggregate facts."""
    if not isinstance(facts, RefreshOutcomeFacts):
        raise TypeError("facts must be RefreshOutcomeFacts")
    if facts.collector_status != "succeeded":
        return "failed"

    verified_empty = (
        facts.total_inputs == 0
        and facts.accepted_inputs == 0
        and facts.rejected_inputs == 0
        and facts.rejected_permanent_exclusions == 0
        and facts.usable_items == 0
        and facts.content_failures == 0
        and facts.index_failures == 0
        and facts.summary_failures == 0
        and facts.permanent_exclusions == 0
        and facts.retryable_failures == 0
        and facts.source_errors == 0
        and facts.empty_reason == "no_matching_articles"
    )
    if verified_empty:
        return "completed"
    if facts.usable_items == 0:
        return "failed"
    if (
        facts.content_failures > 0
        or facts.index_failures > 0
        or facts.summary_failures > 0
        or facts.retryable_failures > 0
        or facts.source_errors > 0
    ):
        return "partial"
    return "completed"


def _validate_terminal_contract(
    status: str,
    collector_status: str,
    counts: RefreshCounts,
    empty_reason: str | None,
) -> None:
    """Prevent callers from persisting a success gate that facts cannot support."""
    if collector_status not in {"succeeded", "failed"}:
        raise ValueError(f"Unsupported collector_status: {collector_status!r}")
    if status != "completed":
        return
    if collector_status != "succeeded":
        raise ValueError("completed refresh requires a succeeded collector")
    verified_empty = (
        counts.total_inputs == 0
        and counts.accepted_inputs == 0
        and counts.rejected_inputs == 0
        and counts.rejected_permanent_exclusions == 0
        and counts.usable_items == 0
        and counts.failed_items == 0
        and counts.permanent_exclusions == 0
        and counts.retryable_failures == 0
        and counts.source_errors == 0
        and empty_reason == "no_matching_articles"
    )
    resolved_usable = (
        counts.usable_items > 0
        and counts.retryable_failures == 0
        and counts.source_errors == 0
        and counts.failed_items == counts.permanent_exclusions
    )
    if not (verified_empty or resolved_usable):
        raise ValueError("completed refresh requires usable resolved work or verified empty input")


def _connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(os.fspath(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise TypeError("canonical config must be a mapping")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc_text(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _normalize_failure_code(value: str | None) -> str:
    """Reduce an ingestion-facing failure label to a stable non-sensitive code."""
    raw = (value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return normalized or "unspecified_failure"


def _validate_attempt_limits(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("max_attempts_by_stage must be a non-empty mapping")
    limits: dict[str, int] = {}
    for stage, limit in value.items():
        if stage not in RETRY_POLICY_STAGES:
            raise ValueError(f"Unsupported processing stage: {stage!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("attempt limits must be positive integers")
        limits[stage] = limit
    return limits


def _validate_collection_attempt_contract(
    *,
    collector_status: str,
    source_row_count: int,
    accepted_input_count: int,
    rejected_source_row_count: int,
    empty_reason: str | None,
    failure_code: str | None,
    retryable: bool,
) -> None:
    if collector_status not in {"succeeded", "failed"}:
        raise ValueError(f"Unsupported collection collector_status: {collector_status!r}")
    for name, value in (
        ("source_row_count", source_row_count),
        ("accepted_input_count", accepted_input_count),
        ("rejected_source_row_count", rejected_source_row_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if accepted_input_count + rejected_source_row_count != source_row_count:
        raise ValueError(
            "accepted_input_count plus rejected_source_row_count must equal source_row_count"
        )
    if collector_status == "succeeded":
        if source_row_count == 0:
            if empty_reason not in {None, "no_matching_articles"}:
                raise ValueError(
                    "empty_reason must be omitted or no_matching_articles for zero source rows"
                )
        elif empty_reason is not None:
            raise ValueError("empty_reason is only allowed for zero source rows")
        if failure_code is not None or retryable:
            raise ValueError("succeeded collection attempts cannot include failure or retry fields")
        return
    if empty_reason is not None:
        raise ValueError("failed collection attempts cannot include empty_reason")
    if failure_code not in COLLECTION_FAILURE_CODES:
        raise ValueError("failed collection attempts require an allow-listed failure_code")
    if not isinstance(retryable, bool):
        raise ValueError("retryable must be a boolean")


def _validate_nonnegative_counts(value: object, excluded: set[str] | None = None) -> None:
    ignored = excluded or set()
    for field in fields(value):
        if field.name in ignored or field.name == "collector_status":
            continue
        count = getattr(value, field.name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{field.name} must be a non-negative integer")
