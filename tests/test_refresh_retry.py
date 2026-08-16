from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from event_collector.article_content import FetchResult
from event_collector.event_collection import CollectionSourceOutcome
from event_collector.news_storage import SQLiteNewsStore
from event_collector.refresh_ledger import (
    RefreshCounts,
    claim_due_retry_run,
    claim_refresh_run,
    finalize_refresh_run,
    finish_processing_attempt,
    initialize_schema,
    record_collection_attempt,
    start_processing_attempt,
)
from event_collector.refresh_retry import (
    RetryExecutionResult,
    build_default_content_retry_executor,
    build_default_summary_retry_executor,
    run_retry_child,
)


NOW = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)
SCOPE = "refresh:sha256:retry-runner-test"


def _parent_with_targets(path, stages):
    initialize_schema(path)
    claim = claim_refresh_run(
        path,
        scope_key=SCOPE,
        requested_date="2026-08-15",
        lease_owner="root-worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
        config_snapshot={"retry_policy": "bounded-v1"},
    )


    for offset, (article_id, stage, input_hash) in enumerate(stages, start=1):
        attempt = start_processing_attempt(
            path,
            run_id=claim.run_id,
            article_id=article_id,
            stage=stage,
            worker_id="root-worker",
            started_at=NOW + timedelta(seconds=offset),
            input_content_sha256=input_hash,
        )
        assert finish_processing_attempt(
            path,
            attempt_id=attempt.attempt_id,
            worker_id="root-worker",
            status="retry_scheduled",
            finished_at=NOW + timedelta(seconds=offset + 1),
            failure_code=f"{stage}_transient",
            retryable=True,
            next_retry_at=NOW + timedelta(seconds=10),
        )
    assert finalize_refresh_run(
        path,
        run_id=claim.run_id,
        lease_owner="root-worker",
        status="failed",
        collector_status="succeeded",
        counts=RefreshCounts(
            total_inputs=len(stages),
            accepted_inputs=len(stages),
            failed_items=len(stages),
            retryable_failures=len(stages),
        ),
        finished_at=NOW + timedelta(seconds=9),
    )
    return claim_due_retry_run(
        path,
        scope_key=SCOPE,
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=10),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={
            "content_fetch": 3,
            "summary": 3,
            "index": 3,
            "reconcile": 1,
        },
    )


def _parent_with_collection_and_article_target(path):
    initialize_schema(path)
    root = claim_refresh_run(
        path,
        scope_key=SCOPE,
        requested_date="2026-08-15",
        lease_owner="root-worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
        config_snapshot={"retry_policy": "bounded-v1"},
    )
    assert record_collection_attempt(
        path,
        run_id=root.run_id,
        source_name="news#batch:1",
        collector_status="failed",
        source_row_count=0,
        accepted_input_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="http_429",
        retryable=True,
        next_retry_at=NOW + timedelta(seconds=10),
        recorded_at=NOW + timedelta(seconds=1),
        worker_id="root-worker",
    )
    article_attempt = start_processing_attempt(
        path,
        run_id=root.run_id,
        article_id=41,
        stage="content_fetch",
        worker_id="root-worker",
        started_at=NOW + timedelta(seconds=2),
        input_content_sha256=None,
    )
    assert finish_processing_attempt(
        path,
        attempt_id=article_attempt.attempt_id,
        worker_id="root-worker",
        status="retry_scheduled",
        finished_at=NOW + timedelta(seconds=3),
        failure_code="content_fetch_transient",
        retryable=True,
        next_retry_at=NOW + timedelta(seconds=10),
    )
    assert finalize_refresh_run(
        path,
        run_id=root.run_id,
        lease_owner="root-worker",
        status="failed",
        collector_status="failed",
        counts=RefreshCounts(
            total_inputs=1,
            accepted_inputs=1,
            failed_items=1,
            retryable_failures=2,
            source_errors=1,
        ),
        finished_at=NOW + timedelta(seconds=9),
    )
    return claim_due_retry_run(
        path,
        scope_key=SCOPE,
        lease_owner="retry-worker",
        now=NOW + timedelta(seconds=10),
        lease_expires_at=NOW + timedelta(minutes=2),
        max_attempts_by_stage={"content_fetch": 3, "collection": 3},
    )


def test_runner_dispatches_only_exact_content_and_summary_stages_without_holding_a_db_write_lock(
    tmp_path,
):
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_targets(
        path,
        [
            (41, "content_fetch", None),
            (42, "summary", "a" * 64),
        ],
    )
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE external_probe(value TEXT NOT NULL)")

    called = []

    def execute(target):
        # This immediate write proves the ledger claim/start transaction was
        # committed before the external operation began.
        with sqlite3.connect(path, timeout=0.05) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO external_probe(value) VALUES (?)", (target.stage,))
            conn.commit()
        called.append((target.article_id, target.stage))
        return RetryExecutionResult(status="succeeded")

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=execute,
        summary_executor=execute,
    )

    assert result.run_id == child.run_id
    assert result.lost_lease is False
    assert called == [(41, "content_fetch"), (42, "summary")]
    assert [outcome.status for outcome in result.outcomes] == ["succeeded", "succeeded"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM external_probe ORDER BY rowid").fetchall() == [
            ("content_fetch",),
            ("summary",),
        ]
        assert conn.execute(
            "SELECT article_id, stage, status FROM article_processing_attempts "
            "WHERE run_id = ? ORDER BY article_id",
            (child.run_id,),
        ).fetchall() == [
            (41, "content_fetch", "succeeded"),
            (42, "summary", "succeeded"),
        ]


def test_runner_watchdog_fails_closed_when_lease_is_lost_during_a_blocking_executor(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: child ownership is renewed independently and stale results are not committed."""
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_targets(path, [(41, "content_fetch", None)])
    heartbeat_failed = Event()
    calls = 0

    def fake_renew(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls >= 2:
            heartbeat_failed.set()
            return False
        return True

    monkeypatch.setattr("event_collector.refresh_retry.renew_refresh_lease", fake_renew)

    def blocking_executor(target):
        del target
        assert heartbeat_failed.wait(timeout=1)
        return RetryExecutionResult(status="succeeded")

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        lease_heartbeat_interval_seconds=0.01,
        content_executor=blocking_executor,
        summary_executor=blocking_executor,
    )

    assert result.lost_lease is True
    assert result.outcomes == ()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT status FROM article_processing_attempts WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == ("running",)


def test_runner_persists_a_new_due_time_from_the_executor(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_targets(path, [(41, "content_fetch", None)])
    retry_at = NOW + timedelta(minutes=4)

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: RetryExecutionResult(
            status="retry_scheduled",
            failure_code="http_429",
            retryable=True,
            next_retry_at=retry_at,
        ),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
    )

    assert result.outcomes[0].status == "retry_scheduled"
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT status, failure_code, retryable, next_retry_at "
            "FROM article_processing_attempts WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == (
            "retry_scheduled",
            "http_429",
            1,
            "2026-08-15T01:04:00.000000Z",
        )


def test_resumed_child_does_not_execute_a_target_that_already_reached_terminal_state(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_targets(path, [(41, "content_fetch", None)])
    calls = []

    first = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: calls.append(target.article_id)
        or RetryExecutionResult(status="succeeded"),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
    )
    second = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=12),
        lease_seconds=60,
        content_executor=lambda target: calls.append(target.article_id)
        or RetryExecutionResult(status="succeeded"),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
    )

    assert first.outcomes[0].status == "succeeded"
    assert second.outcomes[0].status == "succeeded"
    assert calls == [41]
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM article_processing_attempts WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == (1,)


def test_runner_executes_collection_targets_before_articles_and_persists_provider_due_time(tmp_path):
    """SELECT INVARIANT: source retries are durably closed before article retries begin."""
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_collection_and_article_target(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE external_probe(value TEXT NOT NULL)")
    calls = []

    def collection_executor(target):
        with sqlite3.connect(path, timeout=0.05) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO external_probe(value) VALUES ('collection')")
            conn.commit()
        calls.append(("collection", target.source_name))
        return CollectionSourceOutcome(
            source_name="news#batch:1",
            collector_status="failed",
            failure_code="http_429",
            retryable=True,
            retry_after_seconds=120,
        )

    def content_executor(target):
        calls.append(("article", target.article_id))
        return RetryExecutionResult(status="succeeded")

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=content_executor,
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
        collection_executor=collection_executor,
    )

    assert calls == [("collection", "news#batch:1"), ("article", 41)]
    assert [(item.stage, item.status) for item in result.outcomes] == [
        ("collection", "failed"),
        ("content_fetch", "succeeded"),
    ]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM external_probe").fetchall() == [("collection",)]
        assert conn.execute(
            "SELECT source_name, collector_status, failure_code, retryable, next_retry_at "
            "FROM collection_attempts WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == (
            "news#batch:1",
            "failed",
            "http_429",
            1,
            "2026-08-15T01:02:11.000000Z",
        )


def test_resumed_child_does_not_repeat_a_collection_callback_after_terminal_persistence(tmp_path):
    """SELECT INVARIANT: a resumed child reuses its terminal source attempt evidence."""
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_collection_and_article_target(path)
    calls = []

    def collection_executor(target):
        calls.append(target.source_name)
        return CollectionSourceOutcome(
            source_name=target.source_name,
            collector_status="succeeded",
            empty_reason="no_matching_articles",
        )

    first = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: RetryExecutionResult(status="succeeded"),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
        collection_executor=collection_executor,
    )
    second = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=12),
        lease_seconds=60,
        content_executor=lambda target: RetryExecutionResult(status="succeeded"),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
        collection_executor=collection_executor,
    )

    assert calls == ["news#batch:1"]
    assert first.outcomes[0].status == "succeeded"
    assert second.outcomes[0].status == "succeeded"


def test_runner_fails_closed_when_a_collection_target_has_no_executor(tmp_path):
    """SELECT INVARIANT: a claimed source target is never silently skipped."""
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_collection_and_article_target(path)

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: RetryExecutionResult(status="succeeded"),
        summary_executor=lambda target: RetryExecutionResult(status="succeeded"),
    )

    assert result.outcomes[0].stage == "collection"
    assert result.outcomes[0].status == "failed"
    assert result.outcomes[0].failure_code == "collector_error"
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT collector_status, failure_code, retryable FROM collection_attempts "
            "WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == ("failed", "collector_error", 0)


def test_index_retry_never_uses_content_or_summary_and_requires_a_successor_coordinator(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    child = _parent_with_targets(path, [(51, "index", "b" * 64)])
    ordinary_calls = []

    result = run_retry_child(
        path,
        claim=child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: ordinary_calls.append("content"),
        summary_executor=lambda target: ordinary_calls.append("summary"),
    )

    assert ordinary_calls == []
    assert result.outcomes[0].status == "rebuild_required"
    assert result.outcomes[0].failure_code == "generation_rebuild_required"
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT stage, status, failure_code, retryable "
            "FROM article_processing_attempts WHERE run_id = ?",
            (child.run_id,),
        ).fetchone() == (
            "index",
            "failed",
            "generation_rebuild_required",
            1,
        )

    # A coordinator is an explicit successor-generation seam; the runner has
    # no vector-store import or legacy mutation path of its own.
    second_path = tmp_path / "coordinated.sqlite3"
    coordinated_child = _parent_with_targets(second_path, [(52, "index", "c" * 64)])
    coordinated = []
    coordinated_result = run_retry_child(
        second_path,
        claim=coordinated_child,
        clock=lambda: NOW + timedelta(seconds=11),
        lease_seconds=60,
        content_executor=lambda target: ordinary_calls.append("content"),
        summary_executor=lambda target: ordinary_calls.append("summary"),
        successor_generation_coordinator=lambda target: coordinated.append(target.article_id)
        or RetryExecutionResult(status="succeeded"),
    )
    assert coordinated == [52]
    assert coordinated_result.outcomes[0].status == "succeeded"


def test_default_content_and_summary_adapters_execute_only_the_target_stage(tmp_path):
    storage = SQLiteNewsStore(
        db_path=str(tmp_path / "news.sqlite3"),
        content_dir=str(tmp_path / "content"),
    )
    storage.init_db()
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Central bank signals a measured policy shift",
        description="Officials described a measured policy shift after inflation eased.",
        original_url="https://publisher.example/policy",
        published_at=NOW,
    )

    class Fetcher:
        def fetch(self, url):
            assert url == "https://publisher.example/policy"
            return FetchResult(
                original_url=url,
                canonical_url=url,
                content=(
                    "Central bank officials described a measured policy shift after inflation eased. "
                    * 4
                ),
            )

    content_executor = build_default_content_retry_executor(
        storage=storage,
        fetcher=Fetcher(),
        clock=lambda: NOW,
    )
    content_result = content_executor(
        type(
            "Target",
            (),
            {
                "article_id": article_id,
                "stage": "content_fetch",
                "input_content_sha256": None,
            },
        )()
    )
    record = storage.get_article_record(article_id)
    assert content_result.status == "succeeded"
    assert record.content_status == "ready"
    assert record.summary_status == "pending"
    assert record.index_status == "pending"
    content_hash = record.article.active_content_sha256

    class Summarizer:
        def summarize_article(self, article):
            assert article.article_id == article_id
            return "- Inflation eased and officials signaled a measured policy shift."

    summary_executor = build_default_summary_retry_executor(
        storage=storage,
        summarizer=Summarizer(),
        clock=lambda: NOW,
    )
    summary_result = summary_executor(
        type(
            "Target",
            (),
            {
                "article_id": article_id,
                "stage": "summary",
                "input_content_sha256": content_hash,
            },
        )()
    )
    final_record = storage.get_article_record(article_id)
    assert summary_result.status == "succeeded"
    assert final_record.summary_status == "ready"
    assert final_record.article.summary_content_sha256 == content_hash
    assert final_record.index_status == "pending"
    storage.close()


def test_default_content_adapter_checks_lease_after_provider_before_canonical_write(tmp_path):
    """SELECT INVARIANT: a publisher response obtained after lease loss cannot mutate canonical state."""
    storage = SQLiteNewsStore(
        db_path=str(tmp_path / "news.sqlite3"),
        content_dir=str(tmp_path / "content"),
    )
    storage.init_db()
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Central bank signals measured policy shift",
        description="Officials described a measured policy shift after inflation eased.",
        original_url="https://publisher.example/policy",
        published_at=NOW,
    )

    class Fetcher:
        def fetch(self, url):
            return FetchResult(
                original_url=url,
                canonical_url=url,
                content=("Central bank officials described a measured policy shift after " * 30),
                status_code=200,
                content_type="text/html",
                extractor_version="test-v1",
            )

    executor = build_default_content_retry_executor(
        storage=storage,
        fetcher=Fetcher(),
        clock=lambda: NOW,
        lease_guard=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
    )
    target = type("Target", (), {"stage": "content_fetch", "article_id": article_id})()

    with pytest.raises(RuntimeError, match="lease lost"):
        executor(target)

    assert storage.get_article_record(article_id).content_status == "pending"
    storage.close()


def test_default_summary_adapter_checks_lease_after_provider_before_canonical_write(tmp_path):
    """SELECT INVARIANT: a summary obtained after lease loss cannot mutate canonical state."""
    storage = SQLiteNewsStore(
        db_path=str(tmp_path / "news.sqlite3"),
        content_dir=str(tmp_path / "content"),
    )
    storage.init_db()
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Central bank signals measured policy shift",
        description="Officials described a measured policy shift after inflation eased.",
        original_url="https://publisher.example/policy",
        published_at=NOW,
    )
    assert storage.update_article_content(
        article_id,
        content="Central bank policy evidence " * 80,
        title="Central bank signals measured policy shift",
        description="Officials described a measured policy shift after inflation eased.",
        original_url="https://publisher.example/policy",
        canonical_url="https://publisher.example/policy",
        published_at=NOW,
        validation_status="verified",
        validator_version="test-v1",
        response_status_code=200,
        response_content_type="text/html",
        extractor_version="test-v1",
        final_response_url="https://publisher.example/policy",
    )
    content_hash = storage.get_article_record(article_id).article.active_content_sha256
    assert content_hash is not None

    class Summarizer:
        def summarize_article(self, article):
            del article
            return "A grounded summary."

    executor = build_default_summary_retry_executor(
        storage=storage,
        summarizer=Summarizer(),
        clock=lambda: NOW,
        lease_guard=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
    )
    target = type(
        "Target",
        (),
        {"stage": "summary", "article_id": article_id, "input_content_sha256": content_hash},
    )()

    with pytest.raises(RuntimeError, match="lease lost"):
        executor(target)

    assert storage.get_article_record(article_id).summary_status == "pending"
    storage.close()
