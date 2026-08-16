from __future__ import annotations

from datetime import datetime, timezone

from event_collector.refresh_ledger import RetrySchedule
from event_collector.refresh_retry_scheduler import RefreshRetryScheduler


NOW = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)


def _schedule(scope_key: str) -> RetrySchedule:
    return RetrySchedule(
        scope_key=scope_key,
        requested_date="2026-08-15",
        due_at=NOW,
        config_snapshot={"requested_date": "2026-08-15"},
    )


def test_scheduler_runs_each_due_scope_once_and_isolates_handler_failures(tmp_path):
    """SELECT INVARIANT: one bad scope cannot stop other due production retries."""
    schedules = (_schedule("scope-a"), _schedule("scope-b"))
    calls: list[str] = []

    def handler(schedule):
        calls.append(schedule.scope_key)
        if schedule.scope_key == "scope-a":
            raise RuntimeError("provider secret must remain local")
        return {"status": "completed", "run_id": "child-b"}

    scheduler = RefreshRetryScheduler(
        ledger_path=tmp_path / "refresh.sqlite3",
        retry_handler=handler,
        clock=lambda: NOW,
        schedule_provider=lambda path, now: schedules,
    )

    results = scheduler.run_due_once()

    assert calls == ["scope-a", "scope-b"]
    assert [(item.scope_key, item.status, item.run_id) for item in results] == [
        ("scope-a", "handler_failed", None),
        ("scope-b", "completed", "child-b"),
    ]
    assert all(not hasattr(item, "error_detail") for item in results)


def test_scheduler_lifecycle_is_idempotent_and_stops_its_background_worker(tmp_path):
    """SELECT INVARIANT: MCP startup owns one stoppable automatic retry poller."""
    scheduler = RefreshRetryScheduler(
        ledger_path=tmp_path / "missing.sqlite3",
        retry_handler=lambda schedule: None,
        poll_interval_seconds=0.01,
        clock=lambda: NOW,
    )

    assert scheduler.start() is True
    assert scheduler.start() is False
    assert scheduler.stop(timeout_seconds=1.0) is True
    assert scheduler.stop(timeout_seconds=1.0) is False
