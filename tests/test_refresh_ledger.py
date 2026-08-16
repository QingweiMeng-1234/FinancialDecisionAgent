from __future__ import annotations

import importlib.util
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

def _load_refresh_ledger():
    # Keep the standalone contract-test module isolated.  Replacing the
    # package module after refresh_retry has imported RetryClaim creates two
    # distinct dataclass identities and makes full-suite order observable.
    module_name = "event_collector._refresh_ledger_test_core"
    path = Path(__file__).resolve().parents[1] / "src" / "event_collector" / "refresh_ledger.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


refresh_ledger = _load_refresh_ledger()
RefreshCounts = refresh_ledger.RefreshCounts
RefreshArticleObservation = refresh_ledger.RefreshArticleObservation
RefreshOutcomeFacts = refresh_ledger.RefreshOutcomeFacts
claim_refresh_run = refresh_ledger.claim_refresh_run
claim_due_retry_run = refresh_ledger.claim_due_retry_run
classify_refresh_outcome = refresh_ledger.classify_refresh_outcome
compute_scope_key = refresh_ledger.compute_scope_key
finalize_refresh_run = refresh_ledger.finalize_refresh_run
finalize_retry_run_from_chain = refresh_ledger.finalize_retry_run_from_chain
finish_processing_attempt = refresh_ledger.finish_processing_attempt
initialize_schema = refresh_ledger.initialize_schema
list_retry_targets = refresh_ledger.list_retry_targets
list_collection_retry_targets = refresh_ledger.list_collection_retry_targets
list_due_retry_schedules = refresh_ledger.list_due_retry_schedules
LedgerProcessingAttemptRecorder = refresh_ledger.LedgerProcessingAttemptRecorder
record_collection_attempt = refresh_ledger.record_collection_attempt
record_refresh_generation_proof = refresh_ledger.record_refresh_generation_proof
record_refresh_article_observations = refresh_ledger.record_refresh_article_observations
read_cumulative_scope_state = refresh_ledger.read_cumulative_scope_state
renew_refresh_lease = refresh_ledger.renew_refresh_lease
start_processing_attempt = refresh_ledger.start_processing_attempt


NOW = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger_path(tmp_path):
    path = tmp_path / "refresh-ledger.db"
    initialize_schema(path)
    return path


def test_initialize_schema_upgrades_legacy_collection_tables_for_frozen_requests(tmp_path):
    """SELECT INVARIANT: an existing live ledger gains frozen-request columns in place."""
    path = tmp_path / "legacy-refresh-ledger.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE collection_retry_targets (
                target_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_attempt_id TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL,
                due_at TEXT NOT NULL,
                chain_attempt_number INTEGER NOT NULL,
                UNIQUE (run_id, source_name)
            );
            CREATE TABLE collection_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                collector_status TEXT NOT NULL,
                source_row_count INTEGER NOT NULL,
                accepted_input_count INTEGER NOT NULL,
                rejected_source_row_count INTEGER NOT NULL,
                empty_reason TEXT,
                failure_code TEXT,
                retryable INTEGER NOT NULL,
                next_retry_at TEXT,
                recorded_at TEXT NOT NULL,
                worker_id TEXT NOT NULL
            );
            """
        )

    initialize_schema(path)
    claim = _claim(path)
    frozen_request = {
        "endpoint": "everything",
        "source_ids": ["publisher-a", "publisher-b"],
        "query": "finance OR markets",
    }

    assert record_collection_attempt(
        path,
        run_id=claim.run_id,
        source_name="news#batch:1",
        collector_status="succeeded",
        source_row_count=1,
        accepted_input_count=1,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code=None,
        retryable=False,
        next_retry_at=None,
        recorded_at=NOW,
        worker_id="worker-a",
        provider_request=frozen_request,
    )
    with sqlite3.connect(path) as conn:
        assert "provider_request_json" in {
            row[1] for row in conn.execute("PRAGMA table_info(collection_attempts)")
        }
        assert "provider_request_json" in {
            row[1] for row in conn.execute("PRAGMA table_info(collection_retry_targets)")
        }
        assert conn.execute(
            "SELECT provider_request_json FROM collection_attempts"
        ).fetchone() == (
            '{"endpoint":"everything","query":"finance OR markets",'
            '"source_ids":["publisher-a","publisher-b"]}',
        )


def _scope_key() -> str:
    return compute_scope_key(
        {
            "requested_date": "2026-08-15",
            "news_endpoint": "everything",
            "news_page": 1,
            "news_days_back": 7,
            "sources": ["newsapi"],
            "corpus": {"collection": "news_articles_v2", "generation": "gen-7"},
            "ingestion_contract_version": "refresh-v1",
        }
    )


def _claim(ledger_path, *, owner="worker-a", now=NOW, lease_seconds=60):
    return claim_refresh_run(
        ledger_path,
        scope_key=_scope_key(),
        requested_date="2026-08-15",
        lease_owner=owner,
        now=now,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        config_snapshot={"endpoint": "everything", "page": 1},
    )


def test_scope_key_is_deterministic_for_canonical_config_and_changes_with_scope():
    left = {
        "requested_date": "2026-08-15",
        "sources": ["newsapi", "manual"],
        "corpus": {"generation": "gen-7", "collection": "news-v2"},
        "page": 1,
    }
    reordered = {
        "page": 1,
        "corpus": {"collection": "news-v2", "generation": "gen-7"},
        "sources": ["newsapi", "manual"],
        "requested_date": "2026-08-15",
    }

    assert compute_scope_key(left) == compute_scope_key(reordered)
    assert compute_scope_key(left).startswith("refresh:sha256:")
    assert compute_scope_key({**left, "page": 2}) != compute_scope_key(left)


def test_completed_run_is_owner_finalized_once_and_becomes_the_scope_gate(ledger_path):
    claim = _claim(ledger_path)
    assert claim.disposition == "claimed"
    assert claim.attempt_no == 1

    assert not finalize_refresh_run(
        ledger_path,
        run_id=claim.run_id,
        lease_owner="stale-worker",
        status="completed",
        collector_status="succeeded",
        counts=RefreshCounts(total_inputs=1, accepted_inputs=1, usable_items=1),
        finished_at=NOW + timedelta(seconds=10),
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=claim.run_id,
        lease_owner="worker-a",
        status="completed",
        collector_status="succeeded",
        counts=RefreshCounts(total_inputs=1, accepted_inputs=1, usable_items=1),
        finished_at=NOW + timedelta(seconds=10),
    )
    assert not finalize_refresh_run(
        ledger_path,
        run_id=claim.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(),
        finished_at=NOW + timedelta(seconds=11),
    )

    gated = _claim(ledger_path, owner="worker-b", now=NOW + timedelta(minutes=2))
    assert gated.disposition == "already_completed"
    assert gated.run_id == claim.run_id
    assert gated.attempt_no == 1

    with sqlite3.connect(ledger_path) as conn:
        rows = conn.execute(
            "SELECT status, lease_owner, finished_at, usable_items FROM refresh_runs"
        ).fetchall()
    assert rows == [("completed", None, "2026-08-15T01:00:10.000000Z", 1)]


def test_finalizer_refuses_a_false_completed_gate_with_no_usable_result(ledger_path):
    claim = _claim(ledger_path)

    with pytest.raises(ValueError, match="completed"):
        finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner="worker-a",
            status="completed",
            collector_status="succeeded",
            counts=RefreshCounts(
                total_inputs=1,
                accepted_inputs=1,
                usable_items=0,
                failed_items=1,
                retryable_failures=1,
            ),
            finished_at=NOW + timedelta(seconds=10),
        )

    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT status FROM refresh_runs WHERE run_id = ?", (claim.run_id,)
        ).fetchone() == ("running",)


def test_source_errors_are_persisted_and_cannot_create_a_completed_scope_gate(ledger_path):
    claim = _claim(ledger_path)
    counts = RefreshCounts(
        total_inputs=1,
        accepted_inputs=1,
        usable_items=1,
        source_errors=1,
    )

    with pytest.raises(ValueError, match="completed"):
        finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner="worker-a",
            status="completed",
            collector_status="succeeded",
            counts=counts,
            finished_at=NOW + timedelta(seconds=10),
        )

    assert finalize_refresh_run(
        ledger_path,
        run_id=claim.run_id,
        lease_owner="worker-a",
        status="partial",
        collector_status="succeeded",
        counts=counts,
        finished_at=NOW + timedelta(seconds=10),
    )
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT source_errors FROM refresh_runs WHERE run_id = ?", (claim.run_id,)
        ).fetchone() == (1,)

    verified_empty = _claim(ledger_path)
    with pytest.raises(ValueError, match="completed"):
        finalize_refresh_run(
            ledger_path,
            run_id=verified_empty.run_id,
            lease_owner="worker-a",
            status="completed",
            collector_status="succeeded",
            counts=RefreshCounts(source_errors=1),
            empty_reason="no_matching_articles",
            finished_at=NOW + timedelta(seconds=11),
        )


def test_collection_attempt_is_append_only_owned_and_records_only_safe_audit_fields(ledger_path):
    run = _claim(ledger_path)

    assert record_collection_attempt(
        ledger_path,
        run_id=run.run_id,
        source_name="newsapi",
        collector_status="succeeded",
        source_row_count=2,
        accepted_input_count=1,
        rejected_source_row_count=1,
        empty_reason=None,
        failure_code=None,
        retryable=False,
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )
    assert record_collection_attempt(
        ledger_path,
        run_id=run.run_id,
        source_name="newsapi",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="provider_error",
        retryable=True,
        recorded_at=NOW + timedelta(seconds=2),
        worker_id="worker-a",
    )

    with sqlite3.connect(ledger_path) as conn:
        rows = conn.execute(
            """
            SELECT source_name, collector_status, source_row_count, accepted_input_count,
                   rejected_source_row_count, empty_reason, failure_code, retryable,
                   recorded_at, worker_id
            FROM collection_attempts
            ORDER BY recorded_at
            """
        ).fetchall()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(collection_attempts)")]
    assert rows == [
        (
            "newsapi",
            "succeeded",
            2,
            1,
            1,
            None,
            None,
            0,
            "2026-08-15T01:00:01.000000Z",
            "worker-a",
        ),
        (
            "newsapi",
            "failed",
            0,
            0,
            0,
            None,
            "provider_error",
            1,
            "2026-08-15T01:00:02.000000Z",
            "worker-a",
        ),
    ]
    assert "error_detail" not in columns


def test_collection_attempt_records_non_verified_empty_success_without_minting_empty_proof(ledger_path):
    """A skipped generic collector is auditable but cannot itself prove a clean empty corpus."""
    run = _claim(ledger_path)

    assert record_collection_attempt(
        ledger_path,
        run_id=run.run_id,
        source_name="manual",
        collector_status="succeeded",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code=None,
        retryable=False,
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )

    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT collector_status, source_row_count, empty_reason FROM collection_attempts"
        ).fetchone() == ("succeeded", 0, None)


def test_collection_attempt_rejects_invalid_terminal_contract_or_stale_lease(ledger_path):
    run = _claim(ledger_path, lease_seconds=5)
    base = dict(
        db_path=ledger_path,
        run_id=run.run_id,
        source_name="newsapi",
        source_row_count=1,
        accepted_input_count=1,
        rejected_source_row_count=0,
        worker_id="worker-a",
    )

    with pytest.raises(ValueError, match="succeeded"):
        record_collection_attempt(
            **base,
            collector_status="succeeded",
            empty_reason=None,
            failure_code="network_timeout",
            retryable=True,
            recorded_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="failed"):
        record_collection_attempt(
            **base,
            collector_status="failed",
            empty_reason=None,
            failure_code=None,
            retryable=False,
            recorded_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="allow-listed"):
        record_collection_attempt(
            **base,
            collector_status="failed",
            empty_reason=None,
            failure_code="provider_timeout_token_secret",
            retryable=True,
            recorded_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="empty_reason"):
        record_collection_attempt(
            **base,
            collector_status="succeeded",
            empty_reason="no_matching_articles",
            failure_code=None,
            retryable=False,
            recorded_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="source_row_count"):
        record_collection_attempt(
            **{**base, "source_row_count": 2},
            collector_status="succeeded",
            empty_reason=None,
            failure_code=None,
            retryable=False,
            recorded_at=NOW + timedelta(seconds=1),
        )

    _claim(ledger_path, owner="worker-b", now=NOW + timedelta(seconds=6))
    assert not record_collection_attempt(
        **base,
        collector_status="failed",
        empty_reason=None,
        failure_code="network_timeout",
        retryable=True,
        recorded_at=NOW + timedelta(seconds=7),
    )
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_attempts").fetchone() == (0,)


@pytest.mark.parametrize(
    "failure_code",
    [
        "collector_error",
        "missing_api_key",
        "unsupported_endpoint",
        "no_sources_available",
        "provider_invalid_response",
        "network_timeout",
        "network_connection_error",
        "http_429",
        "http_5xx",
        "http_401_403",
        "http_4xx",
        "provider_error",
    ],
)
def test_collection_attempt_accepts_every_real_collector_failure_code(ledger_path, failure_code):
    run = _claim(ledger_path)

    assert record_collection_attempt(
        ledger_path,
        run_id=run.run_id,
        source_name="newsapi",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code=failure_code,
        retryable=True,
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT failure_code FROM collection_attempts"
        ).fetchone() == (failure_code,)


def test_rejected_permanent_exclusions_are_persisted_separately_and_cannot_complete(ledger_path):
    """Invalid rejected inputs must not be miscounted as accepted exclusions."""
    facts = RefreshOutcomeFacts(
        collector_status="succeeded",
        total_inputs=2,
        accepted_inputs=0,
        rejected_inputs=2,
        rejected_permanent_exclusions=2,
        usable_items=0,
    )
    assert classify_refresh_outcome(facts) == "failed"

    claim = _claim(ledger_path)
    counts = RefreshCounts(
        total_inputs=2,
        accepted_inputs=0,
        rejected_inputs=2,
        rejected_permanent_exclusions=2,
    )
    with pytest.raises(ValueError, match="completed"):
        finalize_refresh_run(
            ledger_path,
            run_id=claim.run_id,
            lease_owner="worker-a",
            status="completed",
            collector_status="succeeded",
            counts=counts,
            finished_at=NOW + timedelta(seconds=10),
        )

    assert finalize_refresh_run(
        ledger_path,
        run_id=claim.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="succeeded",
        counts=counts,
        finished_at=NOW + timedelta(seconds=10),
    )
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT rejected_permanent_exclusions FROM refresh_runs WHERE run_id = ?",
            (claim.run_id,),
        ).fetchone() == (2,)


def test_same_scope_concurrent_claim_has_exactly_one_owner(ledger_path):
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait()
        return _claim(ledger_path, owner=owner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))

    assert sorted(result.disposition for result in results) == ["claimed", "in_progress"]
    assert len({result.run_id for result in results}) == 1
    winner = next(result for result in results if result.disposition == "claimed")
    observer = next(result for result in results if result.disposition == "in_progress")
    assert observer.lease_owner == winner.lease_owner

    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM refresh_runs").fetchone()[0] == 1


def test_expired_lease_is_taken_over_on_same_run_and_stale_owner_loses_cas(ledger_path):
    first = _claim(ledger_path, owner="worker-a", lease_seconds=5)
    takeover = _claim(
        ledger_path,
        owner="worker-b",
        now=NOW + timedelta(seconds=6),
        lease_seconds=60,
    )

    assert takeover.disposition == "claimed"
    assert takeover.resumed is True
    assert takeover.run_id == first.run_id
    assert takeover.attempt_no == first.attempt_no
    assert takeover.lease_owner == "worker-b"

    assert not finalize_refresh_run(
        ledger_path,
        run_id=first.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(),
        finished_at=NOW + timedelta(seconds=7),
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=first.run_id,
        lease_owner="worker-b",
        status="partial",
        collector_status="succeeded",
        counts=RefreshCounts(total_inputs=2, accepted_inputs=2, usable_items=1, failed_items=1),
        finished_at=NOW + timedelta(seconds=8),
    )


def test_retry_selector_leaves_an_expired_root_for_root_reclaim(ledger_path):
    """SELECT INVARIANT: only expired retry children resume through the child runner."""
    root = _claim(ledger_path, owner="root-a", lease_seconds=5)

    retry_claim = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=6),
        lease_expires_at=NOW + timedelta(seconds=60),
        max_attempts_by_stage={"content_fetch": 3},
    )

    assert retry_claim.disposition == "retry_unavailable"
    resumed_root = _claim(
        ledger_path,
        owner="root-b",
        now=NOW + timedelta(seconds=6),
        lease_seconds=60,
    )
    assert resumed_root.disposition == "claimed"
    assert resumed_root.resumed is True
    assert resumed_root.run_id == root.run_id
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT parent_run_id FROM refresh_runs WHERE run_id = ?",
            (resumed_root.run_id,),
        ).fetchone() == (None,)


def test_attempt_history_is_retained_and_finish_rejects_stale_or_repeated_worker(ledger_path):
    run = _claim(ledger_path)
    first = start_processing_attempt(
        ledger_path,
        run_id=run.run_id,
        article_id=42,
        stage="content_fetch",
        worker_id="worker-a",
        started_at=NOW,
    )
    assert first.attempt_number == 1

    assert not finish_processing_attempt(
        ledger_path,
        attempt_id=first.attempt_id,
        worker_id="worker-b",
        status="failed",
        finished_at=NOW + timedelta(seconds=1),
        failure_code="network_timeout",
        error_detail="timed out",
        retryable=True,
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=first.attempt_id,
        worker_id="worker-a",
        status="failed",
        finished_at=NOW + timedelta(seconds=1),
        failure_code="network_timeout",
        error_detail="timed out",
        retryable=True,
    )
    assert not finish_processing_attempt(
        ledger_path,
        attempt_id=first.attempt_id,
        worker_id="worker-a",
        status="succeeded",
        finished_at=NOW + timedelta(seconds=2),
    )

    second = start_processing_attempt(
        ledger_path,
        run_id=run.run_id,
        article_id=42,
        stage="content_fetch",
        worker_id="worker-a",
        started_at=NOW + timedelta(seconds=2),
    )
    assert second.attempt_number == 2
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=second.attempt_id,
        worker_id="worker-a",
        status="succeeded",
        finished_at=NOW + timedelta(seconds=3),
    )

    with sqlite3.connect(ledger_path) as conn:
        rows = conn.execute(
            """
            SELECT attempt_number, status, failure_code, retryable
            FROM article_processing_attempts
            ORDER BY attempt_number
            """
        ).fetchall()
    assert rows == [
        (1, "failed", "network_timeout", 1),
        (2, "succeeded", None, 0),
    ]


def test_old_attempt_worker_cannot_finish_after_run_lease_takeover(ledger_path):
    run = _claim(ledger_path, owner="worker-a", lease_seconds=5)
    attempt = start_processing_attempt(
        ledger_path,
        run_id=run.run_id,
        article_id=42,
        stage="index",
        worker_id="worker-a",
        started_at=NOW,
    )
    _claim(ledger_path, owner="worker-b", now=NOW + timedelta(seconds=6))

    assert not finish_processing_attempt(
        ledger_path,
        attempt_id=attempt.attempt_id,
        worker_id="worker-a",
        status="succeeded",
        finished_at=NOW + timedelta(seconds=7),
    )


def test_processing_attempt_recorder_adapts_ingestion_shape_and_normalizes_failure_code(ledger_path):
    """The ingestion-facing adapter keeps durable attempts content-free and CAS-backed."""
    run = _claim(ledger_path)
    recorder = LedgerProcessingAttemptRecorder(
        ledger_path=ledger_path,
        run_id=run.run_id,
        worker_id="worker-a",
        clock=lambda: NOW + timedelta(seconds=1),
    )

    attempt = recorder.begin_attempt(
        article_id=42,
        stage="index",
        input_content_sha256="a" * 64,
    )

    assert recorder.finish_attempt(
        attempt,
        status="failed",
        failure_code=" Network timeout! ",
    ) is True
    assert recorder.finish_attempt(attempt, status="failed", failure_code="network_timeout") is False

    with sqlite3.connect(ledger_path) as conn:
        row = conn.execute(
            """
            SELECT input_content_sha256, status, failure_code, error_detail, retryable, next_retry_at,
                   started_at, finished_at
            FROM article_processing_attempts
            """
        ).fetchone()
    assert row == (
        "a" * 64,
        "failed",
        "network_timeout",
        None,
        0,
        None,
        "2026-08-15T01:00:01.000000Z",
        "2026-08-15T01:00:01.000000Z",
    )


def test_processing_attempt_recorder_persists_a_normalized_scheduled_retry(ledger_path):
    run = _claim(ledger_path)
    recorder = LedgerProcessingAttemptRecorder(
        ledger_path=ledger_path,
        run_id=run.run_id,
        worker_id="worker-a",
        clock=lambda: NOW + timedelta(seconds=1),
    )
    attempt = recorder.begin_attempt(
        article_id=42,
        stage="content_fetch",
        input_content_sha256=None,
    )
    retry_at = NOW + timedelta(minutes=2)

    assert recorder.finish_attempt(
        attempt,
        status="retry_scheduled",
        failure_code=" HTTP 429! ",
        retryable=True,
        next_retry_at=retry_at,
    ) is True

    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT status, failure_code, retryable, next_retry_at FROM article_processing_attempts"
        ).fetchone() == (
            "retry_scheduled",
            "http_429",
            1,
            "2026-08-15T01:02:00.000000Z",
        )


def test_processing_attempt_recorder_assigns_a_durable_default_due_time_to_retryable_stage_failure(
    ledger_path,
):
    run = _claim(ledger_path)
    recorder = LedgerProcessingAttemptRecorder(
        ledger_path=ledger_path,
        run_id=run.run_id,
        worker_id="worker-a",
        clock=lambda: NOW + timedelta(seconds=1),
        default_retry_delay_seconds=30,
    )
    attempt = recorder.begin_attempt(
        article_id=42,
        stage="summary",
        input_content_sha256="a" * 64,
    )

    assert recorder.finish_attempt(
        attempt,
        status="failed",
        failure_code="summary_failed",
        retryable=True,
    )
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT status, failure_code, retryable, next_retry_at "
            "FROM article_processing_attempts WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone() == (
            "retry_scheduled",
            "summary_failed",
            1,
            "2026-08-15T01:00:31.000000Z",
        )


def test_attempt_stage_and_terminal_payload_constraints_are_enforced(ledger_path):
    run = _claim(ledger_path)
    with pytest.raises(ValueError, match="stage"):
        start_processing_attempt(
            ledger_path,
            run_id=run.run_id,
            article_id=42,
            stage="publish",
            worker_id="worker-a",
            started_at=NOW,
        )

    attempt = start_processing_attempt(
        ledger_path,
        run_id=run.run_id,
        article_id=42,
        stage="summary",
        worker_id="worker-a",
        started_at=NOW,
    )
    with pytest.raises(ValueError, match="failure_code"):
        finish_processing_attempt(
            ledger_path,
            attempt_id=attempt.attempt_id,
            worker_id="worker-a",
            status="failed",
            finished_at=NOW + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=2,
                accepted_inputs=2,
                usable_items=2,
            ),
            "completed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=2,
                accepted_inputs=2,
                usable_items=0,
                content_failures=2,
            ),
            "failed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=2,
                accepted_inputs=2,
                usable_items=0,
                index_failures=2,
            ),
            "failed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=2,
                accepted_inputs=2,
                usable_items=1,
                content_failures=1,
                retryable_failures=1,
            ),
            "partial",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=1,
                accepted_inputs=1,
                usable_items=1,
                summary_failures=1,
            ),
            "partial",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=2,
                accepted_inputs=2,
                usable_items=0,
                permanent_exclusions=2,
            ),
            "failed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=0,
                accepted_inputs=0,
                usable_items=0,
                empty_reason="no_matching_articles",
            ),
            "completed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="succeeded",
                total_inputs=0,
                accepted_inputs=0,
                usable_items=0,
                empty_reason="no_matching_articles",
                source_errors=1,
            ),
            "failed",
        ),
        (
            RefreshOutcomeFacts(
                collector_status="failed",
                total_inputs=0,
                accepted_inputs=0,
                usable_items=0,
            ),
            "failed",
        ),
    ],
)
def test_classifier_is_fail_closed_for_required_expected_and_empty_cases(facts, expected):
    assert classify_refresh_outcome(facts) == expected


def _terminal_parent_with_scheduled_retry(
    ledger_path,
    *,
    retry_at=NOW + timedelta(minutes=2),
    lease_seconds=300,
):
    run = _claim(ledger_path, lease_seconds=lease_seconds)
    attempt = start_processing_attempt(
        ledger_path,
        run_id=run.run_id,
        article_id=42,
        stage="content_fetch",
        worker_id="worker-a",
        started_at=NOW,
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=attempt.attempt_id,
        worker_id="worker-a",
        status="retry_scheduled",
        finished_at=NOW + timedelta(seconds=1),
        failure_code="http_429",
        retryable=True,
        next_retry_at=retry_at,
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=run.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="succeeded",
        counts=RefreshCounts(
            total_inputs=1,
            accepted_inputs=1,
            failed_items=1,
            retryable_failures=1,
        ),
        finished_at=NOW + timedelta(seconds=2),
    )
    return run, attempt


def test_due_retry_claim_creates_one_child_with_an_immutable_target_snapshot(ledger_path):
    parent, source_attempt = _terminal_parent_with_scheduled_retry(ledger_path)
    scope_key = _scope_key()

    not_due = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner="retry-worker",
        now=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
        max_attempts_by_stage={"content_fetch": 3, "summary": 3, "index": 1},
    )
    assert not_due.disposition == "retry_scheduled"
    assert not_due.run_id == parent.run_id
    assert not_due.retry_after == NOW + timedelta(minutes=2)

    child = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner="retry-worker",
        now=NOW + timedelta(minutes=2),
        lease_expires_at=NOW + timedelta(minutes=7),
        max_attempts_by_stage={"content_fetch": 3, "summary": 3, "index": 1},
    )
    assert child.disposition == "claimed"
    assert child.parent_run_id == parent.run_id
    assert child.run_id != parent.run_id
    assert child.attempt_no == 2
    assert child.resumed is False

    targets = list_retry_targets(ledger_path, child.run_id)
    assert len(targets) == 1
    assert targets[0].source_attempt_id == source_attempt.attempt_id
    assert targets[0].article_id == 42
    assert targets[0].stage == "content_fetch"
    assert targets[0].due_at == NOW + timedelta(minutes=2)
    assert targets[0].chain_attempt_number == 2

    with sqlite3.connect(ledger_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO refresh_retry_targets "
                "(target_id, run_id, source_attempt_id, article_id, stage, due_at, "
                " chain_attempt_number) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "duplicate-target",
                    child.run_id,
                    source_attempt.attempt_id,
                    42,
                    "content_fetch",
                    "2026-08-15T01:02:00.000000Z",
                    2,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE refresh_retry_targets SET due_at = ? WHERE target_id = ?",
                ("2026-08-15T01:03:00.000000Z", targets[0].target_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM refresh_retry_targets WHERE target_id = ?",
                (targets[0].target_id,),
            )


def test_due_retry_claim_materializes_an_exact_due_news_batch_collection_target(ledger_path):
    """SELECT INVARIANT: a due NewsAPI batch is retried as its own durable source target."""
    parent = _claim(ledger_path)
    due_at = NOW + timedelta(minutes=2)
    assert record_collection_attempt(
        ledger_path,
        run_id=parent.run_id,
        source_name="news#batch:1",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="http_429",
        retryable=True,
        next_retry_at=due_at,
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
        provider_request={
            "provider": "newsapi",
            "endpoint": "everything",
            "source_batch_index": 1,
            "source_ids": ["publisher-b", "publisher-c"],
            "from_at": "2026-08-08T01:00:00Z",
            "to_at": "2026-08-15T01:00:00Z",
            "page": 1,
            "max_pages": 10,
        },
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=parent.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(source_errors=1, retryable_failures=1),
        finished_at=NOW + timedelta(seconds=2),
    )

    waiting = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
        max_attempts_by_stage={"collection": 3},
    )
    assert waiting.disposition == "retry_scheduled"
    assert waiting.retry_after == due_at

    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=due_at,
        lease_expires_at=NOW + timedelta(minutes=7),
        max_attempts_by_stage={"collection": 3},
    )
    assert child.disposition == "claimed"
    assert list_retry_targets(ledger_path, child.run_id) == ()
    targets = list_collection_retry_targets(ledger_path, child.run_id)
    assert len(targets) == 1
    assert targets[0].source_name == "news#batch:1"
    assert targets[0].due_at == due_at
    assert targets[0].chain_attempt_number == 2
    assert targets[0].provider_request == {
        "endpoint": "everything",
        "from_at": "2026-08-08T01:00:00Z",
        "max_pages": 10,
        "page": 1,
        "provider": "newsapi",
        "source_batch_index": 1,
        "source_ids": ["publisher-b", "publisher-c"],
        "to_at": "2026-08-15T01:00:00Z",
    }

    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT next_retry_at FROM collection_attempts WHERE run_id = ?",
            (parent.run_id,),
        ).fetchone() == ("2026-08-15T01:02:00.000000Z",)


def test_scheduler_inventory_includes_due_collection_scope_and_excludes_future_work(ledger_path):
    """SELECT INVARIANT: production polling discovers source retries without claiming them."""
    parent = _claim(ledger_path)
    assert record_collection_attempt(
        ledger_path,
        run_id=parent.run_id,
        source_name="news#batch:2",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="http_429",
        retryable=True,
        next_retry_at=NOW + timedelta(minutes=2),
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=parent.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(source_errors=1, retryable_failures=1),
        finished_at=NOW + timedelta(seconds=2),
    )
    # Freeze the scope's retry policy exactly as a real refresh invocation does.
    waiting = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="inventory-probe",
        now=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=6),
        max_attempts_by_stage={"collection": 3},
    )
    assert waiting.disposition == "retry_scheduled"

    assert list_due_retry_schedules(
        ledger_path, now=NOW + timedelta(minutes=1)
    ) == ()
    schedules = list_due_retry_schedules(
        ledger_path, now=NOW + timedelta(minutes=2)
    )
    assert len(schedules) == 1
    assert schedules[0].scope_key == _scope_key()
    assert schedules[0].requested_date == "2026-08-15"
    assert schedules[0].due_at == NOW + timedelta(minutes=2)
    assert schedules[0].config_snapshot == {"endpoint": "everything", "page": 1}


def test_cumulative_gate_never_completes_with_unresolved_counts_after_attempt_exhaustion(
    ledger_path,
):
    """SELECT INVARIANT: absence of another retry is not proof that the parent chain resolved."""
    _terminal_parent_with_scheduled_retry(
        ledger_path,
        retry_at=NOW + timedelta(seconds=3),
    )
    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"content_fetch": 2},
    )
    target = list_retry_targets(ledger_path, child.run_id)[0]
    attempt = start_processing_attempt(
        ledger_path,
        run_id=child.run_id,
        article_id=target.article_id,
        stage=target.stage,
        worker_id="retry-worker",
        started_at=NOW + timedelta(seconds=4),
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=attempt.attempt_id,
        worker_id="retry-worker",
        status="failed",
        finished_at=NOW + timedelta(seconds=5),
        failure_code="network_timeout",
        retryable=False,
    )

    assert finalize_retry_run_from_chain(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-worker",
        cumulative_counts=RefreshCounts(
            total_inputs=1,
            accepted_inputs=1,
            failed_items=1,
            retryable_failures=1,
        ),
        finished_at=NOW + timedelta(seconds=6),
    ) == "failed"


def test_retry_child_cannot_finalize_before_its_collection_target_is_terminal(ledger_path):
    """SELECT INVARIANT: a child lease cannot close before its claimed source work is recorded."""
    parent = _claim(ledger_path)
    assert record_collection_attempt(
        ledger_path,
        run_id=parent.run_id,
        source_name="news#batch:1",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="network_timeout",
        retryable=True,
        next_retry_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=parent.run_id,
        lease_owner="worker-a",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(source_errors=1, retryable_failures=1),
        finished_at=NOW + timedelta(seconds=2),
    )
    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(minutes=1),
        max_attempts_by_stage={"collection": 3},
    )

    with pytest.raises(ValueError, match="every target is terminal"):
        finalize_retry_run_from_chain(
            ledger_path,
            run_id=child.run_id,
            lease_owner="retry-worker",
            cumulative_counts=RefreshCounts(total_inputs=1, accepted_inputs=1, usable_items=1),
            finished_at=NOW + timedelta(seconds=4),
        )


def test_retry_attempt_limits_are_scope_bound_and_cannot_drift_between_workers(ledger_path):
    _terminal_parent_with_scheduled_retry(ledger_path)
    first = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-a",
        now=NOW + timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"content_fetch": 2},
    )
    assert first.disposition == "retry_scheduled"

    with pytest.raises(ValueError, match="policy"):
        claim_due_retry_run(
            ledger_path,
            scope_key=_scope_key(),
            lease_owner="retry-b",
            now=NOW + timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(minutes=2),
            max_attempts_by_stage={"content_fetch": 3},
        )


def test_expired_retry_child_is_resumed_in_place_and_chain_limit_blocks_a_grandchild(ledger_path):
    _terminal_parent_with_scheduled_retry(ledger_path, retry_at=NOW + timedelta(seconds=3))
    scope_key = _scope_key()
    child = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner="retry-a",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(seconds=8),
        max_attempts_by_stage={"content_fetch": 2},
    )

    takeover = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner="retry-b",
        now=NOW + timedelta(seconds=9),
        lease_expires_at=NOW + timedelta(seconds=30),
        max_attempts_by_stage={"content_fetch": 2},
    )
    assert takeover.disposition == "claimed"
    assert takeover.resumed is True
    assert takeover.run_id == child.run_id
    assert takeover.parent_run_id == child.parent_run_id
    assert list_retry_targets(ledger_path, takeover.run_id) == list_retry_targets(
        ledger_path, child.run_id
    )

    retry_attempt = start_processing_attempt(
        ledger_path,
        run_id=child.run_id,
        article_id=42,
        stage="content_fetch",
        worker_id="retry-b",
        started_at=NOW + timedelta(seconds=10),
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=retry_attempt.attempt_id,
        worker_id="retry-b",
        status="retry_scheduled",
        finished_at=NOW + timedelta(seconds=11),
        failure_code="network_timeout",
        retryable=True,
        next_retry_at=NOW + timedelta(seconds=12),
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-b",
        status="failed",
        collector_status="succeeded",
        counts=RefreshCounts(
            total_inputs=1,
            accepted_inputs=1,
            failed_items=1,
            retryable_failures=1,
        ),
        finished_at=NOW + timedelta(seconds=13),
    )

    exhausted = claim_due_retry_run(
        ledger_path,
        scope_key=scope_key,
        lease_owner="retry-c",
        now=NOW + timedelta(seconds=14),
        lease_expires_at=NOW + timedelta(seconds=40),
        max_attempts_by_stage={"content_fetch": 2},
    )
    assert exhausted.disposition == "retry_exhausted"
    assert exhausted.run_id == child.run_id


def test_retry_lease_renewal_is_owner_and_expiry_cas_guarded(ledger_path):
    _terminal_parent_with_scheduled_retry(ledger_path, retry_at=NOW + timedelta(seconds=3))
    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-a",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(seconds=10),
        max_attempts_by_stage={"content_fetch": 3},
    )

    assert not renew_refresh_lease(
        ledger_path,
        run_id=child.run_id,
        lease_owner="wrong-owner",
        now=NOW + timedelta(seconds=4),
        lease_expires_at=NOW + timedelta(seconds=20),
    )
    assert renew_refresh_lease(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-a",
        now=NOW + timedelta(seconds=4),
        lease_expires_at=NOW + timedelta(seconds=20),
    )
    assert not renew_refresh_lease(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-a",
        now=NOW + timedelta(seconds=21),
        lease_expires_at=NOW + timedelta(seconds=30),
    )


def test_cumulative_child_gate_stays_partial_until_every_parent_chain_target_is_resolved(
    ledger_path,
):
    parent, _ = _terminal_parent_with_scheduled_retry(
        ledger_path,
        retry_at=NOW + timedelta(seconds=3),
    )
    # The parent is already terminal, so append the second source attempt using
    # direct fixture setup to model two immutable parent obligations.
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            """
            INSERT INTO article_processing_attempts
            (attempt_id, run_id, article_id, stage, input_content_sha256, status,
             failure_code, retryable, attempt_number, next_retry_at,
             started_at, finished_at, worker_id)
            VALUES (?, ?, ?, ?, ?, 'retry_scheduled', ?, 1, 1, ?, ?, ?, ?)
            """,
            (
                "fixture-second-retry",
                parent.run_id,
                43,
                "summary",
                "b" * 64,
                "summary_failed",
                "2026-08-15T01:00:03.000000Z",
                "2026-08-15T01:00:00.000000Z",
                "2026-08-15T01:00:01.000000Z",
                "worker-a",
            ),
        )
        conn.commit()

    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-a",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"content_fetch": 3, "summary": 3},
        max_targets=1,
    )
    target = list_retry_targets(ledger_path, child.run_id)[0]
    attempt = start_processing_attempt(
        ledger_path,
        run_id=child.run_id,
        article_id=target.article_id,
        stage=target.stage,
        worker_id="retry-a",
        started_at=NOW + timedelta(seconds=4),
        input_content_sha256=target.input_content_sha256,
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=attempt.attempt_id,
        worker_id="retry-a",
        status="succeeded",
        finished_at=NOW + timedelta(seconds=5),
    )
    status = finalize_retry_run_from_chain(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-a",
        cumulative_counts=RefreshCounts(
            total_inputs=2,
            accepted_inputs=2,
            usable_items=1,
            failed_items=1,
            retryable_failures=1,
        ),
        finished_at=NOW + timedelta(seconds=6),
    )
    assert status == "partial"

    grandchild = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-b",
        now=NOW + timedelta(seconds=7),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"content_fetch": 3, "summary": 3},
    )
    remaining = list_retry_targets(ledger_path, grandchild.run_id)[0]
    final_attempt = start_processing_attempt(
        ledger_path,
        run_id=grandchild.run_id,
        article_id=remaining.article_id,
        stage=remaining.stage,
        worker_id="retry-b",
        started_at=NOW + timedelta(seconds=8),
        input_content_sha256=remaining.input_content_sha256,
    )
    assert finish_processing_attempt(
        ledger_path,
        attempt_id=final_attempt.attempt_id,
        worker_id="retry-b",
        status="succeeded",
        finished_at=NOW + timedelta(seconds=9),
    )
    assert finalize_retry_run_from_chain(
        ledger_path,
        run_id=grandchild.run_id,
        lease_owner="retry-b",
        cumulative_counts=RefreshCounts(
            total_inputs=2,
            accepted_inputs=2,
            content_ready=2,
            summary_ready=2,
            index_ready=2,
            usable_items=2,
        ),
        finished_at=NOW + timedelta(seconds=10),
    ) == "completed"
    assert claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="observer",
        now=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(minutes=3),
        max_attempts_by_stage={"content_fetch": 3, "summary": 3},
    ).disposition == "already_completed"


def test_cumulative_scope_state_uses_latest_article_observation_and_verified_manifest(ledger_path):
    root = _claim(ledger_path)
    assert record_refresh_article_observations(
        ledger_path,
        run_id=root.run_id,
        lease_owner="worker-a",
        observations=(
            RefreshArticleObservation(42, "a" * 64, "ready", "failed", False),
            RefreshArticleObservation(43, "b" * 64, "ready", "ready", False),
        ),
        recorded_at=NOW + timedelta(seconds=1),
    )
    assert record_refresh_generation_proof(
        ledger_path,
        run_id=root.run_id,
        lease_owner="worker-a",
        corpus_id="news",
        generation_id="gen-root",
        corpus_snapshot_id="snapshot-root",
        index_config_fingerprint="c" * 64,
        manifest={43: "b" * 64},
        recorded_at=NOW + timedelta(seconds=2),
    )

    state = read_cumulative_scope_state(ledger_path, _scope_key())
    assert [(item.article_id, item.summary_status) for item in state.articles] == [
        (42, "failed"),
        (43, "ready"),
    ]
    assert state.verified_index_articles == ((43, "b" * 64),)


def test_cumulative_scope_state_includes_root_input_baseline(ledger_path):
    """SELECT INVARIANT: child reconciliation preserves root input and rejection totals."""
    root = _claim(ledger_path)
    assert finalize_refresh_run(
        ledger_path,
        run_id=root.run_id,
        lease_owner="worker-a",
        status="partial",
        collector_status="succeeded",
        counts=RefreshCounts(
            total_inputs=3,
            accepted_inputs=2,
            rejected_inputs=1,
            rejected_permanent_exclusions=1,
            content_ready=1,
            failed_items=1,
            retryable_failures=1,
        ),
        finished_at=NOW + timedelta(seconds=1),
    )

    state = read_cumulative_scope_state(ledger_path, _scope_key())

    assert state.root_counts.total_inputs == 3
    assert state.root_counts.accepted_inputs == 2
    assert state.root_counts.rejected_inputs == 1
    assert state.root_counts.rejected_permanent_exclusions == 1
    assert state.root_collector_status == "succeeded"
    assert state.root_empty_reason is None


def test_cumulative_scope_state_advances_to_latest_terminal_child_counts(ledger_path):
    """SELECT INVARIANT: later retries retain newly observed accepted, rejected, and permanent rows."""
    root = _claim(ledger_path)
    assert record_collection_attempt(
        ledger_path,
        run_id=root.run_id,
        source_name="news#batch:1",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="network_timeout",
        retryable=True,
        next_retry_at=NOW + timedelta(seconds=3),
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="worker-a",
    )
    assert finalize_refresh_run(
        ledger_path,
        run_id=root.run_id,
        lease_owner="worker-a",
        status="partial",
        collector_status="succeeded",
        counts=RefreshCounts(total_inputs=1, accepted_inputs=1, usable_items=1, source_errors=1),
        finished_at=NOW + timedelta(seconds=2),
    )
    child = claim_due_retry_run(
        ledger_path,
        scope_key=_scope_key(),
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=3),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"collection": 3},
    )
    assert record_collection_attempt(
        ledger_path,
        run_id=child.run_id,
        source_name="news#batch:1",
        collector_status="succeeded",
        source_row_count=3,
        accepted_input_count=2,
        rejected_source_row_count=1,
        empty_reason=None,
        failure_code=None,
        retryable=False,
        recorded_at=NOW + timedelta(seconds=4),
        worker_id="retry-worker",
    )
    cumulative = RefreshCounts(
        total_inputs=4,
        accepted_inputs=2,
        rejected_inputs=2,
        rejected_permanent_exclusions=2,
        usable_items=1,
        failed_items=1,
        permanent_exclusions=1,
    )
    assert finalize_retry_run_from_chain(
        ledger_path,
        run_id=child.run_id,
        lease_owner="retry-worker",
        cumulative_counts=cumulative,
        finished_at=NOW + timedelta(seconds=5),
    ) == "completed"

    state = read_cumulative_scope_state(ledger_path, _scope_key())
    assert state.root_counts == cumulative
