"""Thin compatibility and presentation seam for watchlist workflows."""

from __future__ import annotations

from event_collector.retrieval_orchestration import (
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_SNIPPET_CHARS,
)
from event_collector.watchlist_domain import (
    DEFAULT_TOP_N,
    REVIEWER_PROMPT_VERSION,
    TRIAGE_PROMPT_VERSION,
    DeepSeekWatchlistReviewerClient,
    DeepSeekWatchlistTriageClient,
    FollowupInput,
    HumanReviewInput,
    OpenAIWatchlistReviewerClient,
    OpenAIWatchlistTriageClient,
    RankedWatchlistItem,
    RetrievedTickerEvidence,
    ReviewerFinding,
    ReviewerRequest,
    StructuredTickerSignal,
    TickerStructuringAttempt,
    TickerTriageRecord,
    TriageCard,
    TriageConfidence,
    TriageDecisionRequest,
    TriagePriority,
    WatchlistRetrievalFailure,
    WatchlistReviewerAgent,
    WatchlistRunRequest,
    WatchlistRunResult,
    WatchlistTriageAgent,
    _format_reviewer_request,
    _resolve_agent_model,
    build_structured_ticker_signals,
    build_ticker_evidence,
    normalize_ticker,
    normalize_tickers,
    rank_watchlist_items,
)
from event_collector.watchlist_presentation import render_watchlist_report, render_watchlist_summary
from event_collector.watchlist_research import WatchlistResearchConfig, run_watchlist_research
from event_collector.watchlist_workflow import DEFAULT_REPORTS_DIR, write_watchlist_report


def run_watchlist(
    request: WatchlistRunRequest,
    vector_store,
    storage,
    triage_agent: WatchlistTriageAgent | None = None,
    reviewer_agent: WatchlistReviewerAgent | None = None,
    reranking_agent=None,
    structuring_agent=None,
    company_kb=None,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> WatchlistRunResult:
    """Backward-compatible watchlist seam over the deeper research workflow."""
    research_run = run_watchlist_research(
        request,
        storage,
        vector_store,
        config=WatchlistResearchConfig(
            auto_batch_threshold=max(1, len(normalize_tickers(request.tickers))),
            auto_batch_size=max(1, len(normalize_tickers(request.tickers))),
            max_concurrency=1,
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
        ),
        triage_agent=triage_agent,
        reviewer_agent=reviewer_agent,
        reranking_agent=reranking_agent,
        structuring_agent=structuring_agent,
        company_kb_provider=company_kb,
    )
    _log_watchlist_structuring_failures(research_run.result.run_id, research_run.result.items)
    return research_run.result


def _log_watchlist_structuring_failures(run_id: str, items: list[TickerTriageRecord]) -> None:
    failed_counts: dict[str, int] = {}
    for item in items:
        failed = sum(1 for attempt in item.structuring_attempts if attempt.status == "failed")
        if failed:
            failed_counts[item.ticker] = failed

    if not failed_counts:
        return

    import logging

    logger = logging.getLogger(__name__)
    summary = ", ".join(f"{ticker}({count})" for ticker, count in sorted(failed_counts.items()))
    logger.warning(
        "Watchlist run %s completed with %s structuring failure(s) affecting %s ticker(s): %s",
        run_id,
        sum(failed_counts.values()),
        len(failed_counts),
        summary,
    )
