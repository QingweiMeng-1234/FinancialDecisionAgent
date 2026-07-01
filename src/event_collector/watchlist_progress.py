from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import threading
from typing import Any, Callable


WatchlistProgressSink = Callable[["WatchlistProgressEvent"], None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def datetime_from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


@dataclass(frozen=True)
class WatchlistProgressEvent:
    scope: str
    stage: str
    status: str
    ticker: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = datetime_to_iso(self.started_at)
        payload["finished_at"] = datetime_to_iso(self.finished_at)
        payload["metrics"] = dict(self.metrics)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WatchlistProgressEvent":
        return cls(
            scope=payload["scope"],
            stage=payload["stage"],
            status=payload["status"],
            ticker=payload.get("ticker"),
            started_at=datetime_from_iso(payload.get("started_at")),
            finished_at=datetime_from_iso(payload.get("finished_at")),
            duration_ms=payload.get("duration_ms"),
            message=payload.get("message"),
            metrics=dict(payload.get("metrics") or {}),
        )


class WatchlistTimelineRecorder:
    def __init__(self, sink: WatchlistProgressSink | None = None):
        self._sink = sink
        self._events: list[WatchlistProgressEvent] = []
        self._lock = threading.Lock()

    @property
    def events(self) -> list[WatchlistProgressEvent]:
        with self._lock:
            return list(self._events)

    def emit(self, event: WatchlistProgressEvent) -> None:
        with self._lock:
            self._events.append(event)
        if self._sink is not None:
            self._sink(event)

    def write_jsonl(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=True))
                handle.write("\n")
        return path


def render_watchlist_progress_line(event: WatchlistProgressEvent) -> str:
    parts = ["[watchlist]"]
    if event.ticker:
        parts.append(f"ticker={event.ticker}")
    parts.append(f"stage={event.stage}")
    parts.append(f"status={event.status}")
    if event.duration_ms is not None:
        parts.append(f"duration_ms={event.duration_ms}")
    for key in sorted(event.metrics):
        value = event.metrics[key]
        parts.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
    if event.message:
        parts.append(f"message={json.dumps(event.message, ensure_ascii=True)}")
    return " ".join(parts)


def load_watchlist_timeline(path: str) -> list[WatchlistProgressEvent]:
    events: list[WatchlistProgressEvent] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            events.append(WatchlistProgressEvent.from_dict(json.loads(stripped)))
    return events


def build_watchlist_timing_summary(events: list[WatchlistProgressEvent]) -> dict[str, Any]:
    workflow_stages: list[dict[str, Any]] = []
    ticker_stages: dict[str, list[dict[str, Any]]] = {}

    for event in events:
        if event.status not in {"finished", "failed"}:
            continue
        stage_payload = {
            "stage": event.stage,
            "status": event.status,
            "duration_ms": event.duration_ms,
            "message": event.message,
            "metrics": dict(event.metrics),
        }
        if event.scope == "workflow":
            workflow_stages.append(stage_payload)
        elif event.scope == "ticker" and event.ticker:
            ticker_stages.setdefault(event.ticker, []).append(stage_payload)

    finished_workflow = [item for item in workflow_stages if item["duration_ms"] is not None]
    total_duration_ms = sum(int(item["duration_ms"]) for item in finished_workflow)
    slowest_workflow = max(finished_workflow, key=lambda item: int(item["duration_ms"]), default=None)

    return {
        "workflow_stages": workflow_stages,
        "ticker_stages": ticker_stages,
        "total_duration_ms": total_duration_ms,
        "slowest_workflow_stage": slowest_workflow,
    }


def render_watchlist_timing_text(summary: dict[str, Any]) -> str:
    def _fmt_ms(value: int | None) -> str:
        if value is None:
            return "n/a"
        if value >= 1000:
            seconds = value / 1000.0
            return f"{seconds:.1f}s"
        return f"{value}ms"

    lines = ["Timing Summary:"]
    lines.append(f"Total tracked workflow time: {_fmt_ms(summary.get('total_duration_ms'))}")
    slowest = summary.get("slowest_workflow_stage")
    if slowest is not None:
        lines.append(
            "Slowest workflow stage: "
            f"{slowest['stage']} ({_fmt_ms(slowest.get('duration_ms'))}, status={slowest['status']})"
        )
    workflow_stages = summary.get("workflow_stages") or []
    if workflow_stages:
        lines.append("Workflow stages:")
        for stage in workflow_stages:
            lines.append(
                f"- {stage['stage']}: status={stage['status']} duration={_fmt_ms(stage.get('duration_ms'))}"
            )
    ticker_stages = summary.get("ticker_stages") or {}
    if ticker_stages:
        lines.append("Ticker stages:")
        for ticker in sorted(ticker_stages):
            stage_bits = [
                f"{stage['stage']}={_fmt_ms(stage.get('duration_ms'))} ({stage['status']})"
                for stage in ticker_stages[ticker]
            ]
            lines.append(f"- {ticker}: " + ", ".join(stage_bits))
    return "\n".join(lines)
