"""Independent lease renewal for synchronous refresh work.

The refresh pipeline makes provider calls that can block without yielding an
item-level progress callback.  This watchdog owns only lease heartbeats: its
caller remains responsible for checking a recorded failure before publishing
proof or a terminal result.
"""

from __future__ import annotations

from threading import Event, RLock, Thread
from typing import Callable


class LeaseHeartbeatFailed(RuntimeError):
    """A refresh worker can no longer prove ownership of its lease."""


class LeaseHeartbeatWatchdog:
    """Renew a lease independently while a synchronous operation is blocked."""

    def __init__(self, heartbeat: Callable[[], None], *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._heartbeat = heartbeat
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._lock = RLock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(
            target=self._run,
            name="refresh-lease-watchdog",
            daemon=True,
        )
        self._thread.start()

    def pulse(self) -> None:
        self.raise_if_failed()
        try:
            with self._lock:
                self.raise_if_failed()
                self._heartbeat()
        except Exception as error:
            self._record_failure(error)
            raise LeaseHeartbeatFailed("lease heartbeat failed") from error
        self.raise_if_failed()

    def raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise LeaseHeartbeatFailed("lease heartbeat failed") from failure

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.pulse()
            except LeaseHeartbeatFailed:
                self._stop_event.set()
                return

    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = error
