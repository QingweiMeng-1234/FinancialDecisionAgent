"""Package-native watchlist workflow seam for artifacts, refresh, and reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
from typing import Any, Callable

from event_collector.news_storage import SQLiteNewsStore
from event_collector.news_pipeline import NewsPipelineRequest, run_news_pipeline
from event_collector.service_defaults import DEFAULT_SERVICE_DEFAULTS_PATH, load_service_defaults
from event_collector.vector_store import VectorStore
from event_collector.watchlist_presentation import render_watchlist_report
from event_collector.watchlist_progress import (
    WatchlistProgressEvent,
    WatchlistProgressSink,
    WatchlistTimelineRecorder,
    build_watchlist_timing_summary,
    load_watchlist_timeline,
    render_watchlist_timing_text,
    utc_now,
)
from event_collector.watchlist_research import WatchlistResearchConfig, WatchlistResearchRun, run_watchlist_research

DEFAULT_REPORTS_DIR = os.path.join("reports", "watchlist_triage")
DEFAULT_TIMELINE_SUFFIX = ".timeline.jsonl"


@dataclass(frozen=True)
class RefreshNewsRequest:
    refresh_state_path: str = os.path.join("data", "runtime", "refresh_news_state.json")
    service_defaults_path: str = DEFAULT_SERVICE_DEFAULTS_PATH
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"
    today: str | None = None
    top_k: int | None = None
    question: str | None = None
    debug_rerank: bool = False
    include_manual: bool = False
    news_endpoint: str = "everything"
    news_days_back: int = 7
    news_page: int = 1
    news_sort_by: str = "publishedAt"
    news_page_size: int = 100
    show_progress: bool = False


@dataclass(frozen=True)
class WatchlistWorkflowResult:
    result: Any
    batching: dict[str, Any]
    report_path: str
    timeline_path: str | None
    timing_summary: dict[str, Any]
    timing_text: str
    refresh: dict[str, Any] | None = None


@dataclass
class RefreshRunState:
    last_refresh_date: str | None = None

    @classmethod
    def load(cls, path: str) -> "RefreshRunState":
        state_path = Path(path)
        if not state_path.exists():
            return cls()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return cls(last_refresh_date=payload.get("last_refresh_date"))

    def save(self, path: str) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"last_refresh_date": self.last_refresh_date}, indent=2),
            encoding="utf-8",
        )


def refresh_news_corpus(
    request: RefreshNewsRequest,
    *,
    progress_sink: WatchlistProgressSink | None = None,
    run_news_pipeline_fn: Callable[[NewsPipelineRequest], Any] = run_news_pipeline,
) -> dict[str, Any]:
    started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="refresh_news",
            status="started",
            started_at=started_at,
        ),
    )
    refresh_date = request.today or date.today().isoformat()
    state = RefreshRunState.load(request.refresh_state_path)
    if state.last_refresh_date == refresh_date:
        payload = {
            "status": "skipped",
            "refresh_date": refresh_date,
            "message": f"refresh_news already ran on {refresh_date}",
        }
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=payload["message"],
                metrics={"refresh_status": payload["status"]},
            ),
        )
        return payload

    defaults = load_service_defaults(request.service_defaults_path)
    try:
        result = run_news_pipeline_fn(
            NewsPipelineRequest(
                db_path=request.db_path,
                persist_dir=request.persist_dir,
                collection_name=request.collection_name,
                top_k=request.top_k or defaults.query_top_k,
                question=request.question,
                debug_rerank=request.debug_rerank,
                include_manual=request.include_manual,
                news_endpoint=request.news_endpoint,
                news_days_back=request.news_days_back,
                news_page=request.news_page,
                news_sort_by=request.news_sort_by,
                news_page_size=request.news_page_size,
                show_progress=request.show_progress,
            )
        )
        state.last_refresh_date = refresh_date
        state.save(request.refresh_state_path)
        payload = {
            "status": "refreshed",
            "refresh_date": refresh_date,
            "collected_events": result.collected_events,
            "stats": dict(result.stats),
            "total_articles": result.total_articles,
            "answer_text": result.answer_text,
        }
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="finished",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                metrics={
                    "refresh_status": payload["status"],
                    "collected_events": result.collected_events,
                    "total_articles": result.total_articles,
                },
            ),
        )
        return payload
    except Exception as exc:
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="refresh_news",
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
                message=str(exc),
            ),
        )
        raise


def run_watchlist_triage_workflow(
    request,
    storage: SQLiteNewsStore,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
    config: WatchlistResearchConfig | None = None,
    triage_agent=None,
    reviewer_agent=None,
    reranking_agent=None,
    structuring_agent=None,
    company_kb_provider=None,
    execute_batch_fn=None,
    progress_sink: WatchlistProgressSink | None = None,
    timeline_recorder: WatchlistTimelineRecorder | None = None,
    write_report_fn: Callable[..., str] | None = None,
    persist_timeline: bool | None = None,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> WatchlistWorkflowResult:
    recorder = timeline_recorder or WatchlistTimelineRecorder(sink=progress_sink)
    effective_progress_sink = recorder.emit
    research_kwargs = {
        "config": config,
        "triage_agent": triage_agent,
        "reviewer_agent": reviewer_agent,
        "reranking_agent": reranking_agent,
        "structuring_agent": structuring_agent,
        "company_kb_provider": company_kb_provider,
        "progress_sink": effective_progress_sink,
    }
    if execute_batch_fn is not None:
        research_kwargs["execute_batch_fn"] = execute_batch_fn
    research_run = run_watchlist_research(
        request,
        storage,
        vector_store_provider,
        **research_kwargs,
    )
    result = research_run.result
    report_path = persist_watchlist_report(
        result,
        storage=storage,
        output_dir=output_dir,
        progress_sink=effective_progress_sink,
        write_report_fn=write_report_fn,
        debug_review=debug_review,
        debug_rerank=debug_rerank,
    )
    should_persist_timeline = bool(timeline_recorder is not None) if persist_timeline is None else persist_timeline
    timeline_path = None
    if should_persist_timeline:
        timeline_path = write_watchlist_timeline(
            result.run_id,
            recorder,
            output_dir=output_dir,
            timeline_suffix=timeline_suffix,
        )
    timing_summary = build_watchlist_timing_summary(recorder.events)
    return WatchlistWorkflowResult(
        result=result,
        batching=research_run.batching,
        report_path=report_path,
        timeline_path=timeline_path,
        timing_summary=timing_summary,
        timing_text=render_watchlist_timing_text(timing_summary),
    )


def run_refresh_then_watchlist_workflow(
    request,
    storage: SQLiteNewsStore,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    refresh_request: RefreshNewsRequest,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
    config: WatchlistResearchConfig | None = None,
    progress_sink: WatchlistProgressSink | None = None,
    timeline_recorder: WatchlistTimelineRecorder | None = None,
) -> WatchlistWorkflowResult:
    recorder = timeline_recorder or WatchlistTimelineRecorder(sink=progress_sink)
    refresh = refresh_news_corpus(refresh_request, progress_sink=recorder.emit)
    workflow = run_watchlist_triage_workflow(
        request,
        storage,
        vector_store_provider,
        output_dir=output_dir,
        timeline_suffix=timeline_suffix,
        config=config,
        progress_sink=recorder.emit,
        timeline_recorder=recorder,
        persist_timeline=True,
    )
    return WatchlistWorkflowResult(
        result=workflow.result,
        batching=workflow.batching,
        report_path=workflow.report_path,
        timeline_path=workflow.timeline_path,
        timing_summary=workflow.timing_summary,
        timing_text=workflow.timing_text,
        refresh=refresh,
    )


def persist_watchlist_report(
    result,
    *,
    storage: SQLiteNewsStore,
    output_dir: str = DEFAULT_REPORTS_DIR,
    progress_sink: WatchlistProgressSink | None = None,
    write_report_fn: Callable[..., str] | None = None,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="started",
            started_at=started_at,
            message="write_watchlist_report",
        ),
    )
    report_path = (write_report_fn or write_watchlist_report)(
        result,
        output_dir=output_dir,
        debug_review=debug_review,
        debug_rerank=debug_rerank,
    )
    storage.save_watchlist_report_path(result.run_id, report_path)
    finished_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="finished",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
            message="write_watchlist_report",
            metrics={"report_path": report_path},
        ),
    )
    return report_path


def write_watchlist_report(
    result,
    output_dir: str = DEFAULT_REPORTS_DIR,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{result.run_at.strftime('%Y-%m-%d_%H%M%S')}_watchlist.md"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            render_watchlist_report(
                result,
                debug_review=debug_review,
                debug_rerank=debug_rerank,
            )
        )
    return path


def write_watchlist_timeline(
    run_id: str,
    recorder: WatchlistTimelineRecorder,
    *,
    output_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
) -> str:
    timeline_path = os.path.join(output_dir, f"{run_id}{timeline_suffix}")
    return recorder.write_jsonl(timeline_path)


def read_watchlist_report_artifact(
    run_id: str,
    *,
    storage: SQLiteNewsStore,
    reports_dir: str = DEFAULT_REPORTS_DIR,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "found": False,
        "report_path": None,
        "content": None,
        "error_code": None,
        "error_message": None,
    }
    report_path = storage.fetch_watchlist_report_path(run_id)
    if not report_path:
        payload["error_code"] = "report_not_found"
        payload["error_message"] = f"No watchlist report path was found for run_id={run_id}"
        return payload
    resolved_report_path = _resolve_path(report_path)
    allowed_reports_root = Path(reports_dir).resolve()
    if not _is_path_within_root(resolved_report_path, allowed_reports_root):
        payload["error_code"] = "report_not_found"
        payload["error_message"] = f"Stored report path is outside the watchlist reports directory for run_id={run_id}"
        return payload
    payload["report_path"] = report_path
    if not resolved_report_path.exists():
        payload["error_code"] = "report_file_missing"
        payload["error_message"] = f"Saved watchlist report file is missing for run_id={run_id}"
        return payload
    payload["found"] = True
    payload["content"] = resolved_report_path.read_text(encoding="utf-8")
    return payload


def read_watchlist_timeline_artifact(
    run_id: str,
    *,
    reports_dir: str = DEFAULT_REPORTS_DIR,
    timeline_suffix: str = DEFAULT_TIMELINE_SUFFIX,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "found": False,
        "timeline_path": None,
        "events": [],
        "timing_summary": None,
        "timing_text": None,
        "error_code": None,
        "error_message": None,
    }
    resolved_timeline_path = _resolve_path(os.path.join(reports_dir, f"{run_id}{timeline_suffix}"))
    allowed_reports_root = Path(reports_dir).resolve()
    if not _is_path_within_root(resolved_timeline_path, allowed_reports_root):
        payload["error_code"] = "timeline_not_found"
        payload["error_message"] = f"Timeline path is outside the watchlist reports directory for run_id={run_id}"
        return payload
    if not resolved_timeline_path.exists():
        payload["error_code"] = "timeline_not_found"
        payload["error_message"] = f"No watchlist timeline file was found for run_id={run_id}"
        return payload
    events = load_watchlist_timeline(str(resolved_timeline_path))
    timing_summary = build_watchlist_timing_summary(events)
    payload["found"] = True
    payload["timeline_path"] = str(resolved_timeline_path)
    payload["events"] = [event.to_dict() for event in events]
    payload["timing_summary"] = timing_summary
    payload["timing_text"] = render_watchlist_timing_text(timing_summary)
    return payload


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (Path.cwd() / candidate).resolve()


def _is_path_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _emit_progress(progress_sink: WatchlistProgressSink | None, event: WatchlistProgressEvent) -> None:
    if progress_sink is not None:
        progress_sink(event)
