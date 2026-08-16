from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import time
from types import SimpleNamespace

import pytest

from event_collector.article_content import FetchResult
from event_collector.event_collection import (
    CollectionBatchError,
    CollectionSourceOutcome,
    NewsAPIBatchRequest,
    NewsCollector,
    RawEventInput,
)
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.refresh_ledger import (
    CumulativeScopeState,
    RefreshArticleObservation,
    RefreshCounts,
    compute_scope_key,
)
from event_collector.watchlist_progress import WatchlistProgressEvent, WatchlistTimelineRecorder, utc_now
from event_collector.watchlist_research import WatchlistResearchConfig
from event_collector.watchlist_triage import WatchlistRunRequest
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationProof,
    RefreshNewsRequest,
    read_watchlist_report_artifact,
    read_watchlist_timeline_artifact,
    refresh_news_corpus,
    run_refresh_then_watchlist_workflow,
    run_watchlist_triage_workflow,
    _derive_cumulative_refresh_counts,
)


TEST_CONTENT_SHA256 = "a" * 64


def test_retry_cumulative_counts_include_new_provider_rejects_and_permanent_items():
    """New child evidence cannot disappear from the parent-chain completion counts."""
    state = CumulativeScopeState(
        articles=(
            RefreshArticleObservation(1, "a" * 64, "ready", "ready"),
            RefreshArticleObservation(2, "b" * 64, "ready", "ready"),
            RefreshArticleObservation(3, None, "failed", "failed", True),
        ),
        verified_index_articles=((1, "a" * 64), (2, "b" * 64)),
        root_counts=RefreshCounts(
            total_inputs=2,
            accepted_inputs=1,
            rejected_inputs=1,
            rejected_permanent_exclusions=1,
            usable_items=1,
        ),
        root_collector_status="succeeded",
        root_empty_reason=None,
    )
    ingestion = SimpleNamespace(total_inputs=2, accepted_inputs=2, rejected_inputs=0)
    outcome = CollectionSourceOutcome(
        source_name="news",
        collector_status="succeeded",
        raw_inputs=[
            RawEventInput(source="news", raw_text="one"),
            RawEventInput(source="news", raw_text="two"),
        ],
        source_row_count=3,
        rejected_source_row_count=1,
    )

    counts = _derive_cumulative_refresh_counts(
        state,
        collection_ingestion_results=[ingestion],
        collection_outcomes=[outcome],
    )

    assert counts.total_inputs == 5
    assert counts.accepted_inputs == 3
    assert counts.rejected_inputs == 2
    assert counts.rejected_permanent_exclusions == 2
    assert counts.usable_items == 2
    assert counts.failed_items == 1
    assert counts.permanent_exclusions == 1
    assert counts.retryable_failures == 0


def _generation_proof(
    *article_ids: int,
    content_sha256: str = TEST_CONTENT_SHA256,
    corpus_id: str = "news",
):
    return SuccessorGenerationProof(
        corpus_id=corpus_id,
        generation_id="generation-test-1",
        corpus_snapshot_id="snapshot-test-1",
        index_config_fingerprint="b" * 64,
        status="verified",
        chunk_verification_valid=True,
        articles=tuple(
            GenerationArticleProof(article_id=article_id, indexed_content_sha256=content_sha256)
            for article_id in article_ids
        ),
    )


def _make_storage(tmpdir: str) -> SQLiteNewsStore:
    storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
    storage.init_db()
    return storage


def _fake_research_run(run_id: str = "watch-1"):
    return SimpleNamespace(
        result=SimpleNamespace(run_id=run_id),
        batching={
            "enabled": False,
            "batch_count": 1,
            "batch_size": 1,
            "input_ticker_count": 1,
            "report_supported": True,
        },
    )


def _refresh_item(
    article_id: int | None,
    *,
    status: str = "accepted",
    content_status: str | None = "ready",
    summary_status: str | None = "ready",
    index_status: str | None = "ready",
    unchanged: bool = False,
    retryable: bool | None = None,
    next_retry_at=None,
    failure_reason: str | None = None,
    content_sha256: str | None = TEST_CONTENT_SHA256,
):
    return SimpleNamespace(
        article_id=article_id,
        status=status,
        content_status=content_status,
        summary_status=summary_status,
        index_status=index_status,
        unchanged=unchanged,
        retryable=retryable,
        next_retry_at=next_retry_at,
        failure_reason=failure_reason,
        content_sha256=content_sha256,
    )


def _pipeline_result(
    items,
    *,
    total_inputs=None,
    accepted_inputs=None,
    rejected_inputs=None,
    collector_status="succeeded",
    source_outcomes=None,
    source_errors=None,
    empty_reason=None,
):
    resolved_source_errors = list(source_errors or [])
    return SimpleNamespace(
        collected_events=len(items),
        stats={},
        total_articles=10,
        answer_text=None,
        items=list(items),
        total_inputs=len(items) if total_inputs is None else total_inputs,
        accepted_inputs=sum(item.status == "accepted" for item in items)
        if accepted_inputs is None
        else accepted_inputs,
        rejected_inputs=sum(item.status == "rejected" for item in items)
        if rejected_inputs is None
        else rejected_inputs,
        collector_status=collector_status,
        source_outcomes=list(source_outcomes or []),
        source_errors=resolved_source_errors,
        source_error_count=len(resolved_source_errors),
        empty_reason=empty_reason,
    )


class FakeReportPathStorage:
    def __init__(self):
        self.report_paths: dict[str, str] = {}

    def save_watchlist_report_path(self, run_id: str, report_path: str) -> bool:
        self.report_paths[run_id] = report_path
        return True

    def fetch_watchlist_report_path(self, run_id: str) -> str | None:
        return self.report_paths.get(run_id)


def test_refresh_uses_sqlite_gate_and_requires_verified_generation_proof(tmp_path):
    calls: list[object] = []
    build_requests = []
    state_path = tmp_path / "legacy-refresh.json"
    result = _pipeline_result([_refresh_item(7, index_status="ready")])

    def pipeline(request):
        calls.append(request)
        return result

    def build_successor(request):
        build_requests.append(request)
        return _generation_proof(7, corpus_id="news-canonical")

    request = RefreshNewsRequest(
        today="2026-08-15",
        refresh_state_path=str(state_path),
        db_path=str(tmp_path / "news.db"),
        collection_name="news_articles_v2",
        index_corpus_id="news-canonical",
    )

    first = refresh_news_corpus(
        request,
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=build_successor,
    )
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=build_successor,
    )

    assert first["status"] == "completed"
    assert first["counts"]["usable_items"] == 1
    assert second["status"] == "already_completed"
    assert second["run_id"] == first["run_id"]
    assert len(calls) == 1
    assert len(build_requests) == 1
    assert build_requests[0].articles == (
        GenerationArticleProof(article_id=7, indexed_content_sha256=TEST_CONTENT_SHA256),
    )
    assert first["generation_proof"]["generation_id"] == "generation-test-1"
    assert not state_path.exists()
    ledger_path = tmp_path / "legacy-refresh.sqlite3"
    assert ledger_path.is_file()
    with sqlite3.connect(ledger_path) as conn:
        assert conn.execute(
            "SELECT generation_id, corpus_snapshot_id, index_config_fingerprint "
            "FROM refresh_generation_proofs WHERE run_id = ?",
            (first["run_id"],),
        ).fetchone() == ("generation-test-1", "snapshot-test-1", "b" * 64)


def test_refresh_deduplicates_matching_article_proofs_before_successor_build(tmp_path):
    """SELECT INVARIANT: duplicate source rows for one article create one generation proof input."""
    build_requests = []
    result = _pipeline_result(
        [
            _refresh_item(7, content_sha256=TEST_CONTENT_SHA256),
            _refresh_item(7, content_sha256=TEST_CONTENT_SHA256),
        ]
    )

    def build_successor(request):
        build_requests.append(request)
        return _generation_proof(7)

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
        ),
        run_news_pipeline_fn=lambda _request: result,
        successor_generation_coordinator=build_successor,
    )

    assert payload["status"] == "completed"
    assert len(build_requests) == 1
    assert build_requests[0].articles == (
        GenerationArticleProof(7, TEST_CONTENT_SHA256),
    )


def test_refresh_rejects_conflicting_duplicate_article_proofs_before_successor_build(tmp_path):
    """SELECT INVARIANT: one article ID cannot claim two content hashes in a refresh proof."""
    result = _pipeline_result(
        [
            _refresh_item(7, content_sha256=TEST_CONTENT_SHA256),
            _refresh_item(7, content_sha256="c" * 64),
        ]
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
        ),
        run_news_pipeline_fn=lambda _request: result,
        successor_generation_coordinator=lambda _request: pytest.fail(
            "conflicting proof input must not reach the successor coordinator"
        ),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "generation_article_conflict"
    assert payload["generation_proof"] is None


def test_root_refresh_records_only_accepted_valid_article_observations(tmp_path):
    """SELECT INVARIANT: cumulative retry state starts from accepted root articles only."""
    state_path = tmp_path / "refresh.json"
    items = [
        _refresh_item(7, summary_status="failed", retryable=True),
        _refresh_item(
            8,
            content_status="failed",
            summary_status="pending",
            retryable=False,
            content_sha256=None,
        ),
        _refresh_item(None),
        _refresh_item(9, status="rejected", content_status=None, summary_status=None),
    ]

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path)),
        run_news_pipeline_fn=lambda request: _pipeline_result(items),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "partial"
    with sqlite3.connect(state_path.with_suffix(".sqlite3")) as conn:
        assert conn.execute(
            "SELECT article_id, content_sha256, content_status, summary_status, "
            "permanent_exclusion FROM refresh_article_observations ORDER BY article_id"
        ).fetchall() == [
            (7, TEST_CONTENT_SHA256, "ready", "failed", 0),
            (8, None, "failed", "pending", 1),
        ]


def test_refresh_successor_build_receives_a_live_lease_heartbeat(tmp_path, monkeypatch):
    """SELECT INVARIANT: long successor builds can renew the refresh lease before expiry."""
    renewals = []

    def renew(*args, **kwargs):
        renewals.append((args, kwargs))
        return True

    monkeypatch.setattr(
        "event_collector.watchlist_workflow.renew_refresh_lease",
        renew,
        raising=False,
    )

    def build_successor(request):
        assert callable(request.lease_heartbeat)
        request.lease_heartbeat()
        request.lease_heartbeat()
        return _generation_proof(7)

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
            db_path=str(tmp_path / "news.db"),
        ),
        run_news_pipeline_fn=lambda request: _pipeline_result([_refresh_item(7)]),
        successor_generation_coordinator=build_successor,
    )

    assert payload["status"] == "completed", payload["message"]
    assert len(renewals) == 2
    assert all(call[1]["run_id"] == payload["run_id"] for call in renewals)


def test_root_refresh_watchdog_renews_during_a_blocked_pipeline_before_any_item_callback(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: a claimed root refresh keeps its lease while collection is blocked."""
    import event_collector.watchlist_workflow as workflow

    heartbeat_seen = Event()
    renewal_results = []
    real_renew = workflow.renew_refresh_lease

    def renew(*args, **kwargs):
        result = real_renew(*args, **kwargs)
        renewal_results.append(result)
        heartbeat_seen.set()
        return result

    monkeypatch.setattr(workflow, "REFRESH_LEASE_SECONDS", 0.5)
    monkeypatch.setattr(workflow, "REFRESH_LEASE_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(workflow, "renew_refresh_lease", renew)

    def pipeline(pipeline_request):
        attempt = pipeline_request.attempt_recorder.begin_attempt(
            article_id=7,
            stage="summary",
            input_content_sha256=TEST_CONTENT_SHA256,
        )
        assert heartbeat_seen.wait(timeout=1.0)
        time.sleep(0.7)
        assert pipeline_request.attempt_recorder.finish_attempt(attempt, status="succeeded")
        return _pipeline_result(
            [],
            source_outcomes=[
                SimpleNamespace(
                    source_name="news",
                    collector_status="succeeded",
                    raw_inputs=[],
                    source_row_count=0,
                    rejected_source_row_count=0,
                    empty_reason="no_matching_articles",
                    failure_code=None,
                    retryable=False,
                )
            ],
            empty_reason="no_matching_articles",
        )

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-16",
            refresh_state_path=str(tmp_path / "refresh.json"),
        ),
        run_news_pipeline_fn=pipeline,
    )

    assert payload["status"] == "completed", (
        payload["failure_code"],
        payload["message"],
        len(renewal_results),
    )


def test_root_refresh_watchdog_failure_blocks_successor_proof_and_finishes_failed(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: a watchdog renewal failure cannot reach proof or completed finalization."""
    import event_collector.watchlist_workflow as workflow

    heartbeat_attempted = Event()

    def lease_lost(*_args, **_kwargs):
        heartbeat_attempted.set()
        return False

    monkeypatch.setattr(workflow, "REFRESH_LEASE_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(workflow, "renew_refresh_lease", lease_lost)

    def pipeline(_request):
        assert heartbeat_attempted.wait(timeout=0.5)
        return _pipeline_result([_refresh_item(7)])

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-16",
            refresh_state_path=str(tmp_path / "refresh.json"),
        ),
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=lambda _request: pytest.fail(
            "a lost root lease must block successor proof"
        ),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "refresh_lease_lost"


def test_refresh_fails_closed_when_legacy_index_status_has_no_generation_proof(tmp_path):
    result = _pipeline_result([_refresh_item(7, index_status="ready")])

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
            db_path=str(tmp_path / "news.db"),
        ),
        run_news_pipeline_fn=lambda request: result,
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "generation_rebuild_required"
    assert payload["counts"]["index_ready"] == 0
    assert payload["counts"]["usable_items"] == 0
    assert payload["counts"]["retryable_failures"] == 1


def test_refresh_with_usable_articles_and_failed_news_batch_is_partial_not_completed(tmp_path):
    """SELECT INVARIANT: partial collection coverage cannot establish the daily completed gate."""
    outcome = CollectionSourceOutcome(
        source_name="news",
        collector_status="succeeded",
        raw_inputs=[RawEventInput(source="news", raw_text="first batch article")],
        source_row_count=1,
        successful_source_batch_count=1,
        source_batch_count=2,
        batch_errors=[
            CollectionBatchError(
                source_batch_index=1,
                failure_code="network_timeout",
                retryable=True,
                retry_after_at=datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            )
        ],
    )
    result = _pipeline_result(
        [_refresh_item(7)],
        source_outcomes=[outcome],
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
            db_path=str(tmp_path / "news.db"),
        ),
        run_news_pipeline_fn=lambda request: result,
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "partial"
    assert payload["counts"]["usable_items"] == 1
    assert payload["counts"]["source_errors"] == 1
    with sqlite3.connect(tmp_path / "refresh.sqlite3") as conn:
        assert conn.execute(
            "SELECT source_name, collector_status, failure_code, retryable, next_retry_at "
            "FROM collection_attempts ORDER BY recorded_at, source_name"
        ).fetchall() == [
            ("news", "succeeded", None, 0, None),
            ("news#batch:1", "failed", "network_timeout", 1, "2030-01-02T03:04:05.000000Z"),
        ]


def test_refresh_generation_proof_contract_does_not_reuse_pre_v5_gates(tmp_path):
    """SELECT INVARIANT: pre-ledger-upgrade terminal gates cannot own the v5 refresh scope."""
    request = RefreshNewsRequest(
        today="2026-08-15",
        refresh_state_path=str(tmp_path / "refresh.json"),
    )
    payload = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda pipeline_request: _pipeline_result([]),
    )
    legacy_scope = compute_scope_key(
        {
            "requested_date": "2026-08-15",
            "news_endpoint": "everything",
            "news_days_back": 7,
            "news_page": 1,
            "news_sort_by": "publishedAt",
            "news_page_size": 100,
            "include_manual": False,
            "corpus_collection": "news_articles",
            "ingestion_contract_version": "refresh-ledger-v1",
        }
    )
    predecessor_v2_scope = compute_scope_key(
        {
            "requested_date": "2026-08-15",
            "news_endpoint": "everything",
            "news_days_back": 7,
            "news_page": 1,
            "news_sort_by": "publishedAt",
            "news_page_size": 100,
            "include_manual": False,
            "corpus_collection": "news_articles",
            "index_corpus_id": "news",
            "ingestion_contract_version": "refresh-generation-proof-v2",
        }
    )
    predecessor_v3_scope = compute_scope_key(
        {
            "requested_date": "2026-08-15",
            "news_endpoint": "everything",
            "news_days_back": 7,
            "news_page": 1,
            "news_sort_by": "publishedAt",
            "news_page_size": 100,
            "include_manual": False,
            "corpus_collection": "news_articles",
            "index_corpus_id": "news",
            "ingestion_contract_version": "refresh-generation-proof-v3",
        }
    )
    predecessor_v4_scope = compute_scope_key(
        {
            "requested_date": "2026-08-15",
            "news_endpoint": "everything",
            "news_days_back": 7,
            "news_page": 1,
            "news_sort_by": "publishedAt",
            "news_page_size": 100,
            "include_manual": False,
            "corpus_collection": "news_articles",
            "index_corpus_id": "news",
            "ingestion_contract_version": "refresh-generation-proof-v4",
        }
    )

    assert payload["scope_key"] != legacy_scope
    assert payload["scope_key"] != predecessor_v2_scope
    assert payload["scope_key"] != predecessor_v3_scope
    assert payload["scope_key"] != predecessor_v4_scope


def test_refresh_rejects_generation_proof_when_article_hash_is_not_bound(tmp_path):
    """SELECT INVARIANT: a bare ID or stale content hash cannot complete refresh."""
    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
            db_path=str(tmp_path / "news.db"),
        ),
        run_news_pipeline_fn=lambda request: _pipeline_result([_refresh_item(7)]),
        successor_generation_coordinator=lambda result: _generation_proof(
            7, content_sha256="c" * 64
        ),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "generation_proof_invalid"
    assert payload["counts"]["usable_items"] == 0


def test_refresh_records_rejected_inputs_as_permanent_without_creating_a_gate_by_itself(tmp_path):
    rejected = _refresh_item(None, status="rejected", content_status=None, summary_status=None, index_status=None)
    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result([rejected]),
    )

    assert payload["status"] == "failed"
    assert payload["counts"]["rejected_inputs"] == 1
    assert payload["counts"]["rejected_permanent_exclusions"] == 1


def test_refresh_can_complete_usable_work_with_a_permanent_rejected_input(tmp_path):
    accepted = _refresh_item(7)
    rejected = _refresh_item(None, status="rejected", content_status=None, summary_status=None, index_status=None)
    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result([accepted, rejected]),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "completed"
    assert payload["counts"]["usable_items"] == 1
    assert payload["counts"]["rejected_permanent_exclusions"] == 1


def test_refresh_completes_usable_work_with_a_nonretryable_accepted_exclusion(tmp_path):
    usable = _refresh_item(7)
    private_or_unauthorized = _refresh_item(
        8,
        content_status="failed",
        summary_status="pending",
        index_status="pending",
        retryable=False,
        failure_reason="http_401_403",
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result([usable, private_or_unauthorized]),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "completed"
    assert payload["counts"]["usable_items"] == 1
    assert payload["counts"]["failed_items"] == 1
    assert payload["counts"]["permanent_exclusions"] == 1
    assert payload["counts"]["retryable_failures"] == 0


def test_refresh_fails_when_all_accepted_failures_are_nonretryable(tmp_path):
    private_or_unauthorized = _refresh_item(
        8,
        content_status="failed",
        summary_status="pending",
        index_status="pending",
        retryable=False,
        failure_reason="invalid_or_private_url",
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result([private_or_unauthorized]),
    )

    assert payload["status"] == "failed"
    assert payload["counts"]["usable_items"] == 0
    assert payload["counts"]["permanent_exclusions"] == 1
    assert payload["counts"]["retryable_failures"] == 0


@pytest.mark.parametrize(
    ("include_usable", "expected_status"),
    [(True, "partial"), (False, "failed")],
)
def test_refresh_keeps_rate_limit_or_timeout_failures_retryable(
    tmp_path,
    include_usable,
    expected_status,
):
    items = [
        _refresh_item(
            8,
            content_status="failed",
            summary_status="pending",
            index_status="pending",
            retryable=True,
        )
    ]
    verified_ids: set[int] = set()
    if include_usable:
        items.insert(0, _refresh_item(7))
        verified_ids.add(7)

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result(items),
        successor_generation_coordinator=(
            (lambda request: _generation_proof(*verified_ids)) if verified_ids else None
        ),
    )

    assert payload["status"] == expected_status
    assert payload["counts"]["permanent_exclusions"] == 0
    assert payload["counts"]["retryable_failures"] == 1


def test_refresh_summary_failure_is_partial_when_generation_proof_keeps_article_usable(tmp_path):
    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result([_refresh_item(7, summary_status="failed")]),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "partial"
    assert payload["counts"]["usable_items"] == 1
    assert payload["counts"]["retryable_failures"] == 1


def test_refresh_returns_future_retry_without_rerunning_full_pipeline(tmp_path):
    """SELECT INVARIANT: a terminal scope with future work never falls back to root collection."""
    state_path = tmp_path / "refresh.json"
    pipeline_calls = []

    def pipeline(request):
        pipeline_calls.append(request)
        attempt = request.attempt_recorder.begin_attempt(
            article_id=7,
            stage="summary",
            input_content_sha256=TEST_CONTENT_SHA256,
        )
        assert request.attempt_recorder.finish_attempt(
            attempt,
            status="retry_scheduled",
            failure_code="summary_provider_error",
            retryable=True,
            next_retry_at=utc_now() + timedelta(hours=1),
        )
        return _pipeline_result([_refresh_item(7, summary_status="failed", retryable=True)])

    request = RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path))
    first = refresh_news_corpus(
        request,
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert first["status"] == "partial"
    assert second["status"] == "retry_scheduled"
    assert second["retry_after"] is not None
    assert len(pipeline_calls) == 1


def test_due_summary_child_runs_targeted_adapter_and_completes_from_cumulative_state(tmp_path):
    """SELECT INVARIANT: a due child executes only its target and gates on the full scope."""
    state_path = tmp_path / "refresh.json"
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Central bank signals a measured policy shift",
            description="Officials described a measured policy shift after inflation eased.",
            content=(
                "Central bank officials described a measured policy shift after inflation eased. "
                * 4
            ),
            url="https://publisher.example/policy",
            published_at=utc_now(),
        )
    )
    record = storage.get_article_record(article_id)
    content_hash = record.article.active_content_sha256
    storage.close()
    root_calls = []

    def root_pipeline(request):
        root_calls.append(request)
        attempt = request.attempt_recorder.begin_attempt(
            article_id=article_id,
            stage="summary",
            input_content_sha256=content_hash,
        )
        assert request.attempt_recorder.finish_attempt(
            attempt,
            status="retry_scheduled",
            failure_code="summary_provider_error",
            retryable=True,
            next_retry_at=utc_now() - timedelta(seconds=1),
        )
        return _pipeline_result(
            [
                _refresh_item(
                    article_id,
                    summary_status="failed",
                    retryable=True,
                    content_sha256=content_hash,
                )
            ]
        )

    class Summarizer:
        def summarize_article(self, article):
            assert article.article_id == article_id
            return "- Inflation eased and officials signaled a measured policy shift."

    request = RefreshNewsRequest(
        today="2026-08-15",
        refresh_state_path=str(state_path),
        db_path=str(db_path),
    )
    first = refresh_news_corpus(
        request,
        run_news_pipeline_fn=root_pipeline,
        successor_generation_coordinator=lambda request: _generation_proof(
            article_id,
            content_sha256=content_hash,
        ),
    )
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda request: pytest.fail("root pipeline must not rerun"),
        successor_generation_coordinator=lambda request: pytest.fail(
            "summary-only retry must reuse the still-valid generation proof"
        ),
        retry_summarizer=Summarizer(),
    )

    assert first["status"] == "partial"
    assert second["status"] == "completed"
    assert second["counts"]["usable_items"] == 1
    assert second["counts"]["summary_ready"] == 1
    assert len(root_calls) == 1


def test_content_retry_builds_one_scope_generation_and_schedules_downstream_summary(tmp_path):
    """SELECT INVARIANT: recovered content is indexed by successor proof before a partial child closes."""
    state_path = tmp_path / "refresh.json"
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Central bank signals a measured policy shift",
        description="Officials described a measured policy shift after inflation eased.",
        original_url="https://publisher.example/policy",
        published_at=utc_now(),
    )
    storage.close()

    def root_pipeline(request):
        attempt = request.attempt_recorder.begin_attempt(
            article_id=article_id,
            stage="content_fetch",
            input_content_sha256=None,
        )
        assert request.attempt_recorder.finish_attempt(
            attempt,
            status="retry_scheduled",
            failure_code="network_timeout",
            retryable=True,
            next_retry_at=utc_now() - timedelta(seconds=1),
        )
        return _pipeline_result(
            [
                _refresh_item(
                    article_id,
                    content_status="failed",
                    summary_status="pending",
                    retryable=True,
                    content_sha256=None,
                )
            ]
        )

    class Fetcher:
        def fetch(self, url):
            return FetchResult(
                original_url=url,
                canonical_url=url,
                content=(
                    "Central bank officials described a measured policy shift after inflation eased. "
                    * 4
                ),
            )

    class Summarizer:
        def summarize_article(self, article):
            pytest.fail("content-only child must leave summary for a durable downstream retry")

    generation_requests = []

    def build_generation(request):
        generation_requests.append(request)
        return _generation_proof(
            *(article.article_id for article in request.articles),
            content_sha256=request.articles[0].indexed_content_sha256,
        )

    request = RefreshNewsRequest(
        today="2026-08-15",
        refresh_state_path=str(state_path),
        db_path=str(db_path),
    )
    first = refresh_news_corpus(request, run_news_pipeline_fn=root_pipeline)
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda request: pytest.fail("root pipeline must not rerun"),
        successor_generation_coordinator=build_generation,
        retry_content_fetcher=Fetcher(),
        retry_summarizer=Summarizer(),
    )

    assert first["status"] == "failed"
    assert second["status"] == "partial"
    assert second["counts"]["content_ready"] == 1
    assert second["counts"]["index_ready"] == 1
    assert second["counts"]["summary_ready"] == 0
    assert second["counts"]["usable_items"] == 1
    assert len(generation_requests) == 1
    assert generation_requests[0].articles[0].article_id == article_id
    with sqlite3.connect(state_path.with_suffix(".sqlite3")) as conn:
        assert conn.execute(
            "SELECT stage, status, retryable FROM article_processing_attempts "
            "WHERE run_id = ? ORDER BY started_at, attempt_id",
            (second["run_id"],),
        ).fetchall() == [
            ("content_fetch", "succeeded", 0),
            ("summary", "retry_scheduled", 1),
        ]


def test_due_index_child_uses_successor_coordinator_and_completes_parent_chain(tmp_path):
    """SELECT INVARIANT: the production index adapter rebuilds one verified scope generation."""
    state_path = tmp_path / "refresh.json"
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Grid operator raises its long-term demand forecast",
            description="Data-center load increased the operator's demand forecast.",
            content=(
                "The grid operator raised its long-term demand forecast as data-center load increased. "
                * 4
            ),
            summary="- Data-center load increased the grid operator's demand forecast.",
            url="https://publisher.example/grid-demand",
            published_at=utc_now(),
        )
    )
    content_hash = storage.get_article_record(article_id).article.active_content_sha256
    storage.close()

    def root_pipeline(request):
        attempt = request.attempt_recorder.begin_attempt(
            article_id=article_id,
            stage="index",
            input_content_sha256=content_hash,
        )
        assert request.attempt_recorder.finish_attempt(
            attempt,
            status="retry_scheduled",
            failure_code="generation_rebuild_failed",
            retryable=True,
            next_retry_at=utc_now() - timedelta(seconds=1),
        )
        return _pipeline_result(
            [_refresh_item(article_id, content_sha256=content_hash)]
        )

    requests = []

    def coordinator(request):
        requests.append(request)
        return _generation_proof(article_id, content_sha256=content_hash)

    request = RefreshNewsRequest(
        today="2026-08-15",
        refresh_state_path=str(state_path),
        db_path=str(db_path),
    )
    first = refresh_news_corpus(request, run_news_pipeline_fn=root_pipeline)
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda request: pytest.fail("root pipeline must not rerun"),
        successor_generation_coordinator=coordinator,
    )

    assert first["status"] == "failed"
    assert second["status"] == "completed"
    assert second["counts"]["index_ready"] == 1
    assert len(requests) == 1
    assert requests[0].articles == (
        GenerationArticleProof(article_id, content_hash),
    )


def test_due_collection_batch_retries_only_that_news_batch_and_reuses_scope_proof(tmp_path):
    """SELECT INVARIANT: collection child dispatches news#batch:N without rerunning root/manual."""
    state_path = tmp_path / "refresh.json"
    failed_batch = CollectionSourceOutcome(
        source_name="news",
        collector_status="succeeded",
        raw_inputs=[RawEventInput(source="news", raw_text="already accepted")],
        source_row_count=1,
        successful_source_batch_count=1,
        source_batch_count=2,
        batch_errors=[
            CollectionBatchError(
                source_batch_index=1,
                failure_code="network_timeout",
                retryable=True,
                retry_after_at=utc_now() - timedelta(seconds=1),
                provider_request=NewsAPIBatchRequest(
                    source_batch_index=1,
                    source_ids=("retry-me",),
                    financial_query=NewsCollector.DEFAULT_FINANCIAL_QUERY,
                    from_at="2026-08-08T00:00:00Z",
                    to_at="2026-08-15T00:00:00Z",
                    language="en",
                    sort_by="publishedAt",
                    page_size=100,
                    start_page=1,
                    max_pages=10,
                ),
            )
        ],
    )
    request = RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path))
    first = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda request: _pipeline_result(
            [_refresh_item(7)],
            source_outcomes=[failed_batch],
        ),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    class Collector:
        def __init__(self):
            self.batch_calls = []

        def retry_frozen_source_batch(self, request):
            self.batch_calls.append(request["source_batch_index"])
            return CollectionSourceOutcome(
                source_name="news",
                collector_status="succeeded",
                raw_inputs=[],
                source_row_count=0,
                empty_reason="no_matching_articles",
                source_batch_count=1,
                successful_source_batch_count=1,
            )

        def collect_result(self):
            pytest.fail("a batch target must not rerun the full NewsCollector")

    collector = Collector()
    second = refresh_news_corpus(
        request,
        run_news_pipeline_fn=lambda request: pytest.fail("root pipeline must not rerun"),
        successor_generation_coordinator=lambda request: pytest.fail(
            "collection-only retry must reuse the still-valid generation proof"
        ),
        retry_news_collector=collector,
    )

    assert first["status"] == "partial"
    assert second["status"] == "completed"
    assert collector.batch_calls == [1]


def test_refresh_pipeline_exception_is_finalized_as_structured_failed_result(tmp_path):
    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
            db_path=str(tmp_path / "news.db"),
        ),
        run_news_pipeline_fn=lambda request: (_ for _ in ()).throw(RuntimeError("collector down")),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "pipeline_exception"
    assert payload["counts"]["usable_items"] == 0


def test_retry_exhaustion_preserves_failed_top_level_status_without_rerunning_root(tmp_path):
    """SELECT INVARIANT: internal exhaustion is detail, not a new external status enum."""
    state_path = tmp_path / "refresh.json"
    calls = []

    def pipeline(request):
        calls.append(request)
        return _pipeline_result([_refresh_item(7, retryable=False, content_status="failed")])

    request = RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path))
    first = refresh_news_corpus(request, run_news_pipeline_fn=pipeline)
    second = refresh_news_corpus(request, run_news_pipeline_fn=pipeline)

    assert first["status"] == "failed"
    assert second["status"] == "failed"
    assert second["failure_code"] == "retry_exhausted"
    assert second["disposition"] == "retry_exhausted"
    assert len(calls) == 1


def test_refresh_configuration_failure_finalizes_claimed_run(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.load_service_defaults",
        lambda path: (_ for _ in ()).throw(ValueError("invalid service defaults")),
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(
            today="2026-08-15",
            refresh_state_path=str(tmp_path / "refresh.json"),
        ),
        run_news_pipeline_fn=lambda request: pytest.fail("pipeline must not run"),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == "pipeline_exception"


def test_refresh_injects_durable_stage_attempt_recorder_into_pipeline(tmp_path):
    state_path = tmp_path / "refresh.json"

    def pipeline(request):
        attempt = request.attempt_recorder.begin_attempt(
            article_id=7,
            stage="summary",
            input_content_sha256="a" * 64,
        )
        assert request.attempt_recorder.finish_attempt(attempt, status="succeeded")
        return _pipeline_result([_refresh_item(7)])

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path)),
        run_news_pipeline_fn=pipeline,
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "completed"
    with sqlite3.connect(state_path.with_suffix(".sqlite3")) as conn:
        assert conn.execute(
            "SELECT article_id, stage, status FROM article_processing_attempts"
        ).fetchall() == [(7, "summary", "succeeded")]


def test_refresh_persists_verified_empty_collection_and_builds_gate(tmp_path):
    state_path = tmp_path / "refresh.json"
    news_outcome = SimpleNamespace(
        source_name="news",
        collector_status="succeeded",
        raw_inputs=[],
        source_row_count=0,
        rejected_source_row_count=0,
        empty_reason="no_matching_articles",
        failure_code=None,
        retryable=False,
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path)),
        run_news_pipeline_fn=lambda request: _pipeline_result(
            [],
            source_outcomes=[news_outcome],
            empty_reason="no_matching_articles",
        ),
    )

    assert payload["status"] == "completed"
    assert payload["counts"]["source_errors"] == 0
    with sqlite3.connect(state_path.with_suffix(".sqlite3")) as conn:
        assert conn.execute(
            "SELECT status, empty_reason, source_errors FROM refresh_runs"
        ).fetchone() == ("completed", "no_matching_articles", 0)
        assert conn.execute(
            "SELECT source_name, collector_status, empty_reason FROM collection_attempts"
        ).fetchone() == ("news", "succeeded", "no_matching_articles")


def test_refresh_source_error_is_partial_and_persisted_with_usable_article(tmp_path):
    state_path = tmp_path / "refresh.json"
    successful = SimpleNamespace(
        source_name="manual",
        collector_status="succeeded",
        raw_inputs=[object()],
        source_row_count=1,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code=None,
        retryable=False,
    )
    failed = SimpleNamespace(
        source_name="news",
        collector_status="failed",
        raw_inputs=[],
        source_row_count=0,
        rejected_source_row_count=0,
        empty_reason=None,
        failure_code="http_429",
        retryable=True,
    )

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(state_path)),
        run_news_pipeline_fn=lambda request: _pipeline_result(
            [_refresh_item(7)],
            source_outcomes=[successful, failed],
            source_errors=[failed],
        ),
        successor_generation_coordinator=lambda request: _generation_proof(7),
    )

    assert payload["status"] == "partial"
    assert payload["counts"]["source_errors"] == 1
    with sqlite3.connect(state_path.with_suffix(".sqlite3")) as conn:
        assert conn.execute(
            "SELECT source_name, collector_status, failure_code, retryable "
            "FROM collection_attempts ORDER BY source_name"
        ).fetchall() == [
            ("manual", "succeeded", None, 0),
            ("news", "failed", "http_429", 1),
        ]


@pytest.mark.parametrize(
    ("collector_status", "has_usable_article", "expected_status"),
    [
        ("failed", False, "failed"),
        ("succeeded", True, "partial"),
    ],
)
def test_refresh_collection_failures_expose_a_stable_failure_code(
    tmp_path, collector_status, has_usable_article, expected_status
):
    """SELECT INVARIANT: failed or partial collection refreshes expose the normalized cause."""
    failed = CollectionSourceOutcome(
        source_name="news",
        collector_status="failed",
        failure_code="network_timeout",
        retryable=True,
    )
    source_outcomes = [failed]
    if has_usable_article:
        source_outcomes.insert(
            0,
            CollectionSourceOutcome(
                source_name="manual",
                collector_status="succeeded",
                raw_inputs=[RawEventInput(source="manual", raw_text="accepted")],
                source_row_count=1,
            ),
        )
    items = [_refresh_item(7)] if has_usable_article else []

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result(
            items,
            collector_status=collector_status,
            source_outcomes=source_outcomes,
            source_errors=[failed],
        ),
        successor_generation_coordinator=(lambda request: _generation_proof(7))
        if has_usable_article
        else None,
    )

    assert payload["status"] == expected_status
    assert payload["failure_code"] == "network_timeout"


@pytest.mark.parametrize(
    ("failure_reasons", "expected_failure_code"),
    [
        (["invalid_or_private_url"] * 100, "invalid_or_private_url"),
        (["invalid_or_private_url", "http_429"], "processing_failed"),
        (["fetch failed for https://private.example/token=do-not-leak"], "processing_failed"),
        ([None], "refresh_incomplete"),
    ],
)
def test_refresh_item_failures_expose_safe_aggregate_failure_code(
    tmp_path, failure_reasons, expected_failure_code
):
    """SELECT INVARIANT: item processing failures explain an incomplete refresh without leaking details."""
    items = [
        _refresh_item(
            article_id=index + 1,
            content_status="failed",
            failure_reason=failure_reason,
        )
        for index, failure_reason in enumerate(failure_reasons)
    ]

    payload = refresh_news_corpus(
        RefreshNewsRequest(today="2026-08-15", refresh_state_path=str(tmp_path / "refresh.json")),
        run_news_pipeline_fn=lambda request: _pipeline_result(items),
    )

    assert payload["status"] == "failed"
    assert payload["failure_code"] == expected_failure_code


def test_run_refresh_then_watchlist_workflow_refreshes_before_triage(monkeypatch):
    call_order: list[str] = []

    monkeypatch.setattr(
        "event_collector.watchlist_workflow.refresh_news_corpus",
        lambda request, progress_sink=None: call_order.append("refresh")
        or {"status": "completed", "refresh_date": "2026-07-01", "counts": {"usable_items": 1}},
    )
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_triage_workflow",
        lambda request, storage, vector_store_provider, **kwargs: call_order.append("triage")
        or SimpleNamespace(
            result=SimpleNamespace(run_id="watch-ordered", ranked_items=[], retrieval_failures=[], top_n=request.top_n),
            batching={"enabled": False, "batch_count": 1, "batch_size": 2, "input_ticker_count": 2, "report_supported": True},
            report_path="report.md",
            timeline_path="timeline.jsonl",
            timing_summary={"workflow_stages": [], "ticker_stages": {}, "total_duration_ms": 0, "slowest_workflow_stage": None},
            timing_text="Timing Summary:",
        ),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        workflow = run_refresh_then_watchlist_workflow(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"]),
            storage,
            object(),
            refresh_request=RefreshNewsRequest(today="2026-07-01", refresh_state_path=os.path.join(tmpdir, "refresh.json")),
            retrieval_provenance={
                "generation_id": "generation-test-1",
                "corpus_snapshot_id": "snapshot-test-1",
                "index_config_fingerprint": "b" * 64,
            },
        )

        assert call_order == ["refresh", "triage"]
        assert workflow.refresh == {"status": "completed", "refresh_date": "2026-07-01", "counts": {"usable_items": 1}}
        storage.close()


@pytest.mark.parametrize("refresh_status", ["failed", "unexpected"])
def test_run_refresh_then_watchlist_workflow_blocks_unknown_or_failed_refresh(monkeypatch, refresh_status):
    calls: list[str] = []
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.refresh_news_corpus",
        lambda request, progress_sink=None: {"status": refresh_status, "run_id": "refresh-failed", "counts": {"usable_items": 0}},
    )
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_triage_workflow",
        lambda *args, **kwargs: calls.append("triage"),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        with pytest.raises(Exception) as excinfo:
            run_refresh_then_watchlist_workflow(
                WatchlistRunRequest(tickers=["MSFT"]),
                storage,
                object(),
                refresh_request=RefreshNewsRequest(today="2026-07-01", refresh_state_path=os.path.join(tmpdir, "refresh.json")),
                retrieval_provenance={
                    "generation_id": "generation-test-1",
                    "corpus_snapshot_id": "snapshot-test-1",
                    "index_config_fingerprint": "b" * 64,
                },
            )
        storage.close()

    assert "refresh-failed" in str(excinfo.value)
    assert calls == []


def test_run_watchlist_triage_workflow_persists_report_and_timeline(monkeypatch):
    monkeypatch.setattr(
        "event_collector.watchlist_workflow.run_watchlist_research",
        lambda request, storage, vector_store_provider, **kwargs: _fake_research_run("watch-workflow"),
    )

    def fake_write_report(result, output_dir, debug_review=False, debug_rerank=False, retrieval_provenance=None):
        path = os.path.join(output_dir, f"{result.run_id}.md")
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# {result.run_id}\n")
        return path

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FakeReportPathStorage()
        recorder = WatchlistTimelineRecorder()
        recorder.emit(
            WatchlistProgressEvent(
                scope="workflow",
                stage="triage",
                status="finished",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_ms=7,
            )
        )

        workflow = run_watchlist_triage_workflow(
            WatchlistRunRequest(tickers=["MSFT"]),
            storage,
            object(),
            output_dir=os.path.join(tmpdir, "reports"),
            config=WatchlistResearchConfig(),
            timeline_recorder=recorder,
            persist_timeline=True,
            write_report_fn=fake_write_report,
            retrieval_provenance={
                "generation_id": "generation-test-1",
                "corpus_snapshot_id": "snapshot-test-1",
                "index_config_fingerprint": "b" * 64,
            },
        )

        assert workflow.report_path.endswith("watch-workflow.md")
        assert os.path.exists(workflow.report_path)
        assert workflow.timeline_path is not None
        assert os.path.exists(workflow.timeline_path)
        assert storage.report_paths["watch-workflow"] == workflow.report_path
        assert workflow.timing_summary["total_duration_ms"] >= 7
        assert any(stage["stage"] == "persist_report" for stage in workflow.timing_summary["workflow_stages"])
        assert workflow.timing_text.startswith("Timing Summary:")


def test_read_watchlist_report_artifact_handles_found_missing_and_unsafe_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FakeReportPathStorage()
        reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        found_path = os.path.join(reports_dir, "watch-found.md")
        with open(found_path, "w", encoding="utf-8") as handle:
            handle.write("report body")
        storage.save_watchlist_report_path("watch-found", found_path)

        missing_payload = read_watchlist_report_artifact("watch-missing", storage=storage, reports_dir=reports_dir)
        found_payload = read_watchlist_report_artifact("watch-found", storage=storage, reports_dir=reports_dir)

        outside_path = os.path.join(tmpdir, "outside.md")
        with open(outside_path, "w", encoding="utf-8") as handle:
            handle.write("outside")
        storage.save_watchlist_report_path("watch-unsafe", outside_path)
        unsafe_payload = read_watchlist_report_artifact("watch-unsafe", storage=storage, reports_dir=reports_dir)

        deleted_path = os.path.join(reports_dir, "watch-deleted.md")
        storage.save_watchlist_report_path("watch-deleted", deleted_path)
        missing_file_payload = read_watchlist_report_artifact("watch-deleted", storage=storage, reports_dir=reports_dir)

        assert missing_payload["error_code"] == "report_not_found"
        assert found_payload["found"] is True
        assert found_payload["content"] == "report body"
        assert unsafe_payload["error_code"] == "report_not_found"
        assert missing_file_payload["error_code"] == "report_file_missing"


def test_read_watchlist_timeline_artifact_handles_found_missing_and_unsafe_paths(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        timeline_path = os.path.join(reports_dir, "watch-found.timeline.jsonl")
        recorder = WatchlistTimelineRecorder()
        recorder.emit(
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_ms=11,
            )
        )
        recorder.write_jsonl(timeline_path)

        found_payload = read_watchlist_timeline_artifact("watch-found", reports_dir=reports_dir)
        missing_payload = read_watchlist_timeline_artifact("watch-missing", reports_dir=reports_dir)

        unsafe_root = os.path.join(tmpdir, "safe-root")
        os.makedirs(unsafe_root, exist_ok=True)
        monkeypatch.setattr(
            "event_collector.watchlist_workflow._resolve_path",
            lambda path: Path(tmpdir, "outside", os.path.basename(path)),
        )
        unsafe_payload = read_watchlist_timeline_artifact("watch-unsafe", reports_dir=unsafe_root)

        assert found_payload["found"] is True
        assert found_payload["events"][0]["stage"] == "refresh_news"
        assert found_payload["timing_summary"]["total_duration_ms"] == 11
        assert missing_payload["error_code"] == "timeline_not_found"
        assert unsafe_payload["error_code"] == "timeline_not_found"
