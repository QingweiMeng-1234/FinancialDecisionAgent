"""Deeper watchlist research orchestration with staged execution and bounded concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import uuid
from typing import Any, Callable

from event_collector.entity_kb import SQLiteEntityStore
from event_collector.event_structuring import EventStructuringAgent
from event_collector.news_storage import SQLiteNewsStore
from event_collector.reranking import RAGRerankingAgent
from event_collector.retrieval_execution import (
    RetrievalWorkItem,
    RetrievalWorkResult,
    execute_retrieval_batch,
)
from event_collector.retrieval_intent import normalize_retrieval_intent
from event_collector.structuring_runtime import StructuringAttemptOutcome, ensure_article_structured
from event_collector.vector_store import VectorStore
from event_collector.watchlist_domain import (
    REVIEWER_PROMPT_VERSION,
    TRIAGE_PROMPT_VERSION,
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
    _resolve_agent_model,
    build_structured_ticker_signals,
    build_ticker_evidence,
    normalize_tickers,
    rank_watchlist_items,
)
from event_collector.watchlist_progress import (
    WatchlistProgressEvent,
    WatchlistProgressSink,
    utc_now,
)
from event_collector.retrieval_orchestration import DEFAULT_EXCERPT_CHARS, DEFAULT_SNIPPET_CHARS


@dataclass(frozen=True)
class WatchlistResearchConfig:
    auto_batch_threshold: int = 12
    auto_batch_size: int = 8
    max_concurrency: int = 5
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS
    snippet_chars: int = DEFAULT_SNIPPET_CHARS


@dataclass(frozen=True)
class WatchlistResearchRun:
    result: WatchlistRunResult
    batching: dict[str, Any]


@dataclass(frozen=True)
class _TickerEvidenceContext:
    ticker: str
    evidence: list[RetrievedTickerEvidence]
    rerank_metadata: Any | None


@dataclass(frozen=True)
class _TickerAnalysisContext:
    ticker: str
    evidence: list[RetrievedTickerEvidence]
    structured_signals: list[StructuredTickerSignal]
    structuring_attempts: list[TickerStructuringAttempt]
    rerank_metadata: Any | None


@dataclass(frozen=True)
class _StageOutcome:
    ticker: str
    payload: Any | None
    error_message: str | None

    @property
    def succeeded(self) -> bool:
        return self.error_message is None


def run_watchlist_research(
    request: WatchlistRunRequest,
    storage: SQLiteNewsStore,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    config: WatchlistResearchConfig | None = None,
    triage_agent: WatchlistTriageAgent | None = None,
    reviewer_agent: WatchlistReviewerAgent | None = None,
    reranking_agent: RAGRerankingAgent | None = None,
    structuring_agent: EventStructuringAgent | None = None,
    company_kb_provider: SQLiteEntityStore | Callable[[], SQLiteEntityStore | None] | None = None,
    execute_batch_fn: Callable[..., list[RetrievalWorkResult]] = execute_retrieval_batch,
    progress_sink: WatchlistProgressSink | None = None,
) -> WatchlistResearchRun:
    """Run full watchlist research with staged execution and bounded concurrency."""
    policy = config or WatchlistResearchConfig()
    normalized_tickers = normalize_tickers(request.tickers)
    retrieval_intent = normalize_retrieval_intent(request.retrieval_intent)
    triage = triage_agent or WatchlistTriageAgent()
    reviewer = reviewer_agent or WatchlistReviewerAgent()
    run_id = str(uuid.uuid4())
    run_at = datetime.now()
    retrieval_failures: list[WatchlistRetrievalFailure] = []
    items_by_ticker: dict[str, TickerTriageRecord] = {}
    batches = _build_batches(
        normalized_tickers,
        auto_batch_threshold=policy.auto_batch_threshold,
        auto_batch_size=policy.auto_batch_size,
    )
    retrieval_started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="retrieval",
            status="started",
            started_at=retrieval_started_at,
            metrics={
                "input_ticker_count": len(normalized_tickers),
                "batch_count": len(batches),
            },
        ),
    )
    evidence_contexts: list[_TickerEvidenceContext] = []
    try:
        for batch in batches:
            batch_started_at = utc_now()
            started_by_ticker = {ticker: utc_now() for ticker in batch}
            for ticker in batch:
                _emit_progress(
                    progress_sink,
                    WatchlistProgressEvent(
                        scope="ticker",
                        stage="retrieval",
                        status="started",
                        ticker=ticker,
                        started_at=started_by_ticker[ticker],
                    ),
                )
            work_items = [
                RetrievalWorkItem(
                    key=ticker,
                    query=ticker,
                    top_k=request.retrieval_top_k,
                    retrieval_top_k=request.retrieval_top_k,
                    excerpt_chars=policy.excerpt_chars,
                    snippet_chars=policy.snippet_chars,
                    retrieval_intent=retrieval_intent,
                )
                for ticker in batch
            ]
            retrieval_results = execute_batch_fn(
                work_items,
                vector_store_provider,
                reranking_agent=reranking_agent,
                company_kb_provider=company_kb_provider,
                max_concurrency=policy.max_concurrency,
                provider_mode=_watchlist_provider_mode(vector_store_provider, policy.max_concurrency),
            )

            for ticker, retrieval_result in zip(batch, retrieval_results, strict=True):
                result_finished_at = utc_now()
                elapsed_seconds = retrieval_result.elapsed_seconds or max(
                    (result_finished_at - batch_started_at).total_seconds(),
                    0.0,
                )
                duration_ms = max(0, int(round(elapsed_seconds * 1000)))
                if not retrieval_result.succeeded:
                    _emit_progress(
                        progress_sink,
                        WatchlistProgressEvent(
                            scope="ticker",
                            stage="retrieval",
                            status="failed",
                            ticker=ticker,
                            started_at=started_by_ticker[ticker],
                            finished_at=result_finished_at,
                            duration_ms=duration_ms,
                            message=retrieval_result.error_message,
                            metrics={"result_status": retrieval_result.status},
                        ),
                    )
                    retrieval_failures.append(
                        WatchlistRetrievalFailure(
                            ticker=ticker,
                            status=retrieval_result.status,
                            error_message=retrieval_result.error_message,
                        )
                    )
                    items_by_ticker[ticker] = _build_fallback_record(
                        ticker=ticker,
                        reason=(
                            "Retrieval failed before target-specific evidence was available. "
                            "Refresh the evidence path and rerun this name."
                        ),
                        should_flag_human_review=True,
                    )
                    continue

                bundle = retrieval_result.bundle
                assert bundle is not None
                evidence = build_ticker_evidence(bundle.evidence)
                _emit_progress(
                    progress_sink,
                    WatchlistProgressEvent(
                        scope="ticker",
                        stage="retrieval",
                        status="finished",
                        ticker=ticker,
                        started_at=started_by_ticker[ticker],
                        finished_at=result_finished_at,
                        duration_ms=duration_ms,
                        metrics={
                            "evidence_count": len(evidence),
                            "result_status": retrieval_result.status,
                        },
                    ),
                )
                if not evidence:
                    items_by_ticker[ticker] = _build_fallback_record(
                        ticker=ticker,
                        reason=(
                            "No sufficiently relevant, ticker-specific evidence was retrieved in this run. "
                            "Treat this as low priority until stronger evidence appears."
                        ),
                        should_flag_human_review=True,
                    )
                    continue

                evidence_contexts.append(
                    _TickerEvidenceContext(
                        ticker=ticker,
                        evidence=evidence,
                        rerank_metadata=bundle.rerank_metadata,
                    )
                )
    except Exception as exc:
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="retrieval",
                status="failed",
                started_at=retrieval_started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - retrieval_started_at).total_seconds() * 1000))),
                message=str(exc),
            ),
        )
        raise
    else:
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="retrieval",
                status="finished",
                started_at=retrieval_started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - retrieval_started_at).total_seconds() * 1000))),
                metrics={
                    "succeeded_ticker_count": len(evidence_contexts),
                    "failed_ticker_count": len(retrieval_failures),
                },
            ),
        )

    shared_structuring_started_at = utc_now()
    article_count = len(
        {
            evidence_item.article_id
            for context in evidence_contexts
            for evidence_item in context.evidence
        }
    )
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="shared_structuring",
            status="started",
            started_at=shared_structuring_started_at,
            metrics={"article_count": article_count},
        ),
    )
    try:
        structuring_outcomes_by_article = _structure_articles_for_run(
            evidence_contexts,
            storage,
            structuring_agent=structuring_agent,
            force_structure=request.force_structure,
        )
    except Exception as exc:
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="shared_structuring",
                status="failed",
                started_at=shared_structuring_started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - shared_structuring_started_at).total_seconds() * 1000))),
                message=str(exc),
                metrics={"article_count": article_count},
            ),
        )
        raise
    finished_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="shared_structuring",
            status="finished",
            started_at=shared_structuring_started_at,
            finished_at=finished_at,
            duration_ms=max(0, int(round((finished_at - shared_structuring_started_at).total_seconds() * 1000))),
            metrics={
                "article_count": article_count,
                "attempted_article_count": sum(
                    1 for outcome in structuring_outcomes_by_article.values() if outcome.attempted
                ),
            },
        ),
    )
    analysis_contexts = [
        _build_analysis_context(
            context,
            storage,
            run_id=run_id,
            structuring_outcomes_by_article=structuring_outcomes_by_article,
            structuring_agent=structuring_agent,
            force_structure=request.force_structure,
        )
        for context in evidence_contexts
    ]

    triage_outcomes = _execute_stage(
        analysis_contexts,
        max_concurrency=policy.max_concurrency,
        stage_name="triage",
        worker=_run_triage,
        agent=triage,
        progress_sink=progress_sink,
    )
    review_inputs = [
        context
        for context in analysis_contexts
        if triage_outcomes[context.ticker].succeeded
    ]
    review_outcomes = _execute_stage(
        review_inputs,
        max_concurrency=policy.max_concurrency,
        stage_name="review",
        worker=_run_review,
        agent=reviewer,
        progress_sink=progress_sink,
        triage_cards={
            context.ticker: triage_outcomes[context.ticker].payload
            for context in review_inputs
        },
    )

    for context in analysis_contexts:
        triage_outcome = triage_outcomes[context.ticker]
        if not triage_outcome.succeeded:
            items_by_ticker[context.ticker] = _build_stage_failure_record(
                ticker=context.ticker,
                stage_name="triage",
                error_message=triage_outcome.error_message,
            )
            continue

        review_outcome = review_outcomes.get(context.ticker)
        reviewer_finding: ReviewerFinding
        if review_outcome is None or not review_outcome.succeeded:
            reviewer_finding = _build_review_stage_fallback(
                context.ticker,
                review_outcome.error_message if review_outcome is not None else "review stage missing output",
            )
        else:
            reviewer_finding = review_outcome.payload

        items_by_ticker[context.ticker] = TickerTriageRecord(
            ticker=context.ticker,
            card=triage_outcome.payload,
            reviewer_finding=reviewer_finding,
            evidence=context.evidence,
            structured_signals=context.structured_signals,
            structuring_attempts=context.structuring_attempts,
            rerank_metadata=context.rerank_metadata,
        )

    items = [items_by_ticker[ticker] for ticker in normalized_tickers if ticker in items_by_ticker]
    ranked_items = rank_watchlist_items(items)
    result = WatchlistRunResult(
        run_id=run_id,
        run_at=run_at,
        tickers=normalized_tickers,
        top_n=request.top_n,
        retrieval_top_k=request.retrieval_top_k,
        triage_model=_resolve_agent_model(triage.llm_client),
        reviewer_model=_resolve_agent_model(reviewer.llm_client),
        triage_prompt_version=TRIAGE_PROMPT_VERSION,
        reviewer_prompt_version=REVIEWER_PROMPT_VERSION,
        items=items,
        ranked_items=ranked_items,
        retrieval_failures=retrieval_failures,
    )
    persist_started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="started",
            started_at=persist_started_at,
            message="persist_watchlist_run",
        ),
    )
    try:
        storage.save_watchlist_run(result)
    except Exception as exc:
        finished_at = utc_now()
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage="persist_report",
                status="failed",
                started_at=persist_started_at,
                finished_at=finished_at,
                duration_ms=max(0, int(round((finished_at - persist_started_at).total_seconds() * 1000))),
                message=f"persist_watchlist_run: {exc}",
            ),
        )
        raise
    finished_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage="persist_report",
            status="finished",
            started_at=persist_started_at,
            finished_at=finished_at,
            duration_ms=max(0, int(round((finished_at - persist_started_at).total_seconds() * 1000))),
            message="persist_watchlist_run",
            metrics={"item_count": len(items)},
        ),
    )
    return WatchlistResearchRun(
        result=result,
        batching={
            "enabled": len(batches) > 1,
            "batch_count": len(batches),
            "batch_size": max((len(batch) for batch in batches), default=0),
            "input_ticker_count": len(normalized_tickers),
            "report_supported": True,
            "max_concurrency": policy.max_concurrency,
        },
    )


def _build_batches(
    tickers: list[str],
    *,
    auto_batch_threshold: int,
    auto_batch_size: int,
) -> list[list[str]]:
    if len(tickers) <= max(1, auto_batch_threshold):
        return [tickers]
    batch_size = max(1, auto_batch_size)
    return [tickers[index : index + batch_size] for index in range(0, len(tickers), batch_size)]


def _watchlist_provider_mode(
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    max_concurrency: int,
) -> str:
    if max_concurrency <= 1:
        return "shared_read"
    if callable(vector_store_provider):
        return "thread_local_factory"
    return "shared_read"


def _structure_articles_for_run(
    contexts: list[_TickerEvidenceContext],
    storage: SQLiteNewsStore,
    *,
    structuring_agent: EventStructuringAgent | None,
    force_structure: bool,
) -> dict[int, StructuringAttemptOutcome]:
    structuring_outcomes_by_article: dict[int, StructuringAttemptOutcome] = {}
    ordered_article_ids: list[int] = []
    seen_article_ids: set[int] = set()

    for context in contexts:
        for evidence_item in context.evidence:
            if evidence_item.article_id in seen_article_ids:
                continue
            seen_article_ids.add(evidence_item.article_id)
            ordered_article_ids.append(evidence_item.article_id)

    for article_id in ordered_article_ids:
        structuring_outcomes_by_article[article_id] = ensure_article_structured(
            article_id,
            storage,
            structuring_agent=structuring_agent,
            force_restructure=force_structure,
        )

    return structuring_outcomes_by_article


def _build_analysis_context(
    context: _TickerEvidenceContext,
    storage: SQLiteNewsStore,
    *,
    run_id: str,
    structuring_outcomes_by_article: dict[int, StructuringAttemptOutcome],
    structuring_agent: EventStructuringAgent | None,
    force_structure: bool,
) -> _TickerAnalysisContext:
    signals, structuring_attempts = build_structured_ticker_signals(
        context.ticker,
        context.evidence,
        storage,
        run_id=run_id,
        structuring_outcomes_by_article=structuring_outcomes_by_article,
        structuring_agent=structuring_agent,
        force_structure=force_structure,
    )
    return _TickerAnalysisContext(
        ticker=context.ticker,
        evidence=context.evidence,
        structured_signals=signals,
        structuring_attempts=structuring_attempts,
        rerank_metadata=context.rerank_metadata,
    )


def _execute_stage(
    contexts: list[_TickerAnalysisContext],
    *,
    max_concurrency: int,
    stage_name: str,
    worker: Callable[..., _StageOutcome],
    progress_sink: WatchlistProgressSink | None = None,
    **worker_kwargs: Any,
) -> dict[str, _StageOutcome]:
    if not contexts:
        return {}

    workflow_started_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage=stage_name,
            status="started",
            started_at=workflow_started_at,
            metrics={"ticker_count": len(contexts)},
        ),
    )
    started_by_ticker = {context.ticker: utc_now() for context in contexts}
    worker_count = min(max(1, max_concurrency), len(contexts))
    if worker_count == 1:
        outcomes = {
            context.ticker: _run_stage_with_progress(
                context,
                stage_name=stage_name,
                worker=worker,
                started_at=started_by_ticker[context.ticker],
                progress_sink=progress_sink,
                **worker_kwargs,
            )
            for context in contexts
        }
        _emit_progress(
            progress_sink,
            WatchlistProgressEvent(
                scope="workflow",
                stage=stage_name,
                status="finished",
                started_at=workflow_started_at,
                finished_at=utc_now(),
                duration_ms=max(0, int(round((utc_now() - workflow_started_at).total_seconds() * 1000))),
                metrics={"ticker_count": len(contexts)},
            ),
        )
        return outcomes

    ordered_outcomes: list[_StageOutcome | None] = [None] * len(contexts)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(worker, context, **worker_kwargs): index
            for index, context in enumerate(contexts)
        }
        for context in contexts:
            _emit_progress(
                progress_sink,
                WatchlistProgressEvent(
                    scope="ticker",
                    stage=stage_name,
                    status="started",
                    ticker=context.ticker,
                    started_at=started_by_ticker[context.ticker],
                ),
            )
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            context = contexts[index]
            ordered_outcomes[index] = _emit_stage_result_progress(
                context.ticker,
                stage_name=stage_name,
                outcome=future.result(),
                started_at=started_by_ticker[context.ticker],
                progress_sink=progress_sink,
            )

    outcomes = {
        contexts[index].ticker: outcome
        for index, outcome in enumerate(ordered_outcomes)
        if outcome is not None
    }
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="workflow",
            stage=stage_name,
            status="finished",
            started_at=workflow_started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int(round((utc_now() - workflow_started_at).total_seconds() * 1000))),
            metrics={"ticker_count": len(contexts)},
        ),
    )
    return outcomes


def _run_triage(
    context: _TickerAnalysisContext,
    *,
    agent: WatchlistTriageAgent,
) -> _StageOutcome:
    try:
        card = agent.triage_ticker(
            TriageDecisionRequest(
                ticker=context.ticker,
                evidence=context.evidence,
                structured_signals=context.structured_signals,
            )
        )
        return _StageOutcome(ticker=context.ticker, payload=card, error_message=None)
    except Exception as exc:  # pragma: no cover - runtime/provider-specific failure shapes vary
        return _StageOutcome(ticker=context.ticker, payload=None, error_message=str(exc) or exc.__class__.__name__)


def _run_review(
    context: _TickerAnalysisContext,
    *,
    agent: WatchlistReviewerAgent,
    triage_cards: dict[str, TriageCard],
) -> _StageOutcome:
    try:
        finding = agent.review_ticker(
            ReviewerRequest(
                ticker=context.ticker,
                card=triage_cards[context.ticker],
                evidence=context.evidence,
                structured_signals=context.structured_signals,
            )
        )
        return _StageOutcome(ticker=context.ticker, payload=finding, error_message=None)
    except Exception as exc:  # pragma: no cover - runtime/provider-specific failure shapes vary
        return _StageOutcome(ticker=context.ticker, payload=None, error_message=str(exc) or exc.__class__.__name__)


def _build_stage_failure_record(
    *,
    ticker: str,
    stage_name: str,
    error_message: str | None,
) -> TickerTriageRecord:
    return _build_fallback_record(
        ticker=ticker,
        reason=(
            f"The {stage_name} stage failed for this ticker"
            f"{': ' + error_message if error_message else ''}. "
            "Treat the name as requiring human review before acting on this run."
        ),
        should_flag_human_review=True,
    )


def _build_review_stage_fallback(ticker: str, error_message: str | None) -> ReviewerFinding:
    return ReviewerFinding(
        evidence_too_generic=False,
        missing_target_specific_signal=False,
        reasoning_jump=False,
        missing_counter_evidence=True,
        next_action_too_vague=True,
        summary=(
            f"Review stage failed for {ticker}"
            f"{': ' + error_message if error_message else ''}. "
            "Flag this name for human review before relying on the triage card."
        ),
        should_flag_human_review=True,
    )


def _build_fallback_record(
    *,
    ticker: str,
    reason: str,
    should_flag_human_review: bool,
) -> TickerTriageRecord:
    card = TriageCard(
        ticker=ticker,
        priority=TriagePriority.LOW,
        confidence=TriageConfidence.LOW,
        why_now=reason,
        key_evidence=[],
        counter_evidence=[],
        missing_questions=["What changed in the latest target-specific news flow?"],
        next_action="Refresh or narrow the evidence set before spending deeper research time.",
    )
    finding = ReviewerFinding(
        evidence_too_generic=True,
        missing_target_specific_signal=True,
        reasoning_jump=False,
        missing_counter_evidence=True,
        next_action_too_vague=False,
        summary="The research run did not have enough reliable ticker-specific evidence for a stronger call.",
        should_flag_human_review=should_flag_human_review,
    )
    return TickerTriageRecord(
        ticker=ticker,
        card=card,
        reviewer_finding=finding,
        evidence=[],
        structured_signals=[],
        structuring_attempts=[],
        rerank_metadata=None,
    )


def _run_stage_with_progress(
    context: _TickerAnalysisContext,
    *,
    stage_name: str,
    worker: Callable[..., _StageOutcome],
    started_at: datetime,
    progress_sink: WatchlistProgressSink | None,
    **worker_kwargs: Any,
) -> _StageOutcome:
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="ticker",
            stage=stage_name,
            status="started",
            ticker=context.ticker,
            started_at=started_at,
        ),
    )
    outcome = worker(context, **worker_kwargs)
    return _emit_stage_result_progress(
        context.ticker,
        stage_name=stage_name,
        outcome=outcome,
        started_at=started_at,
        progress_sink=progress_sink,
    )


def _emit_stage_result_progress(
    ticker: str,
    *,
    stage_name: str,
    outcome: _StageOutcome,
    started_at: datetime,
    progress_sink: WatchlistProgressSink | None,
) -> _StageOutcome:
    finished_at = utc_now()
    _emit_progress(
        progress_sink,
        WatchlistProgressEvent(
            scope="ticker",
            stage=stage_name,
            status="finished" if outcome.succeeded else "failed",
            ticker=ticker,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int(round((finished_at - started_at).total_seconds() * 1000))),
            message=outcome.error_message,
        ),
    )
    return outcome


def _emit_progress(
    progress_sink: WatchlistProgressSink | None,
    event: WatchlistProgressEvent,
) -> None:
    if progress_sink is not None:
        progress_sink(event)
