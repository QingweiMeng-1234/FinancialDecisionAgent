"""Background production polling for durable refresh child retries.

The scheduler discovers only retry obligations already persisted by a terminal
root/child chain.  It never starts a new daily root refresh.  Scope claiming is
left to the normal workflow entrypoint, which provides the cross-process CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from threading import Event, Lock, Thread
from typing import Any, Callable, Mapping

from event_collector.refresh_ledger import RetrySchedule, list_due_retry_schedules


@dataclass(frozen=True)
class RetryDispatchResult:
    scope_key: str
    status: str
    run_id: str | None = None


RetryHandler = Callable[[RetrySchedule], Mapping[str, Any] | None]
ScheduleProvider = Callable[[str | os.PathLike[str], datetime], tuple[RetrySchedule, ...]]


class RefreshRetryScheduler:
    """Own one daemon poller and isolate failures between due scopes."""

    def __init__(
        self,
        *,
        ledger_path: str | os.PathLike[str],
        retry_handler: RetryHandler,
        poll_interval_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        schedule_provider: ScheduleProvider | None = None,
    ) -> None:
        if not callable(retry_handler):
            raise TypeError("retry_handler must be callable")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be positive")
        self._ledger_path = ledger_path
        self._retry_handler = retry_handler
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._schedule_provider = schedule_provider or (
            lambda path, now: list_due_retry_schedules(path, now=now)
        )
        self._stop_event = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None

    def run_due_once(self) -> tuple[RetryDispatchResult, ...]:
        """Dispatch the current read-only inventory without leaking exceptions."""
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        schedules = self._schedule_provider(self._ledger_path, now)
        results: list[RetryDispatchResult] = []
        for schedule in schedules:
            try:
                payload = self._retry_handler(schedule)
                status = payload.get("status") if isinstance(payload, Mapping) else None
                run_id = payload.get("run_id") if isinstance(payload, Mapping) else None
                results.append(
                    RetryDispatchResult(
                        scope_key=schedule.scope_key,
                        status=status if isinstance(status, str) and status else "dispatched",
                        run_id=run_id if isinstance(run_id, str) and run_id else None,
                    )
                )
            except Exception:
                # Provider details remain in process-local logging/telemetry;
                # the durable/public scheduler result is a stable category.
                results.append(RetryDispatchResult(schedule.scope_key, "handler_failed"))
        return tuple(results)

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run_loop,
                name="financial-agent-refresh-retry",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return False
            self._thread = None
            self._stop_event.set()
        thread.join(timeout_seconds)
        return True

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_due_once()
            except Exception:
                # Inventory/schema failures must not terminate MCP serving;
                # the next bounded poll retries discovery.
                pass
            self._stop_event.wait(self._poll_interval_seconds)
