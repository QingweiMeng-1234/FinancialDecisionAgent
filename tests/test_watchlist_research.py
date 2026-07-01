from __future__ import annotations

from datetime import datetime
import os
import tempfile
import threading
import time

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
    EventStructuringAgent,
)
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.retrieval_execution import RetrievalWorkResult
from event_collector.retrieval_orchestration import RetrievalEvidenceBundle, RetrievedArticleEvidence
from event_collector.watchlist_research import (
    WatchlistResearchConfig,
    run_watchlist_research,
)
from event_collector.watchlist_triage import (
    WatchlistRunRequest,
    WatchlistReviewerAgent,
    WatchlistTriageAgent,
)


class FakeTriageClient:
    def __init__(self):
        self.calls = []
        self.model = "fake-triage-model"

    def triage_ticker(self, request):
        self.calls.append(request.ticker)
        priority = "High" if request.ticker == "MSFT" else "Medium"
        confidence = "High" if request.ticker == "MSFT" else "Low"
        return {
            "ticker": request.ticker,
            "priority": priority,
            "confidence": confidence,
            "why_now": f"{request.ticker} has target-specific evidence.",
            "key_evidence": [f"{request.ticker} evidence"],
            "counter_evidence": [],
            "missing_questions": ["What changed most recently?"],
            "next_action": f"Review the latest {request.ticker} setup.",
        }


class FakeReviewerClient:
    def __init__(self):
        self.calls = []
        self.model = "fake-reviewer-model"

    def review_ticker(self, request):
        self.calls.append(request.ticker)
        return {
            "evidence_too_generic": False,
            "missing_target_specific_signal": False,
            "reasoning_jump": False,
            "missing_counter_evidence": False,
            "next_action_too_vague": False,
            "summary": f"{request.ticker} is specific enough.",
            "should_flag_human_review": False,
        }


class FakeStructuringClient:
    def __init__(self, response_by_article_id=None):
        self.response_by_article_id = response_by_article_id or {}
        self.calls = []

    def extract_events(self, article):
        self.calls.append(article.article_id)
        return {"events": list(self.response_by_article_id.get(article.article_id, []))}


def _store_article(storage: SQLiteNewsStore, title: str, url: str) -> int:
    return storage.save_article(
        NewsArticle(
            source="news",
            title=title,
            description=title,
            content=f"{title} content with enough detail for retrieval and structuring.",
            url=url,
            published_at=datetime.now(),
            summary=f"{title} summary",
        )
    )


def _store_event(storage: SQLiteNewsStore, article_id: int, ticker: str, event_id: str) -> None:
    storage.save_structured_events(
        article_id,
        [
            StructuredEvent(
                event_id=event_id,
                article_id=article_id,
                event_type=EventType.COMPANY,
                direction=EventDirection.POSITIVE,
                importance=EventImportance.HIGH,
                time_horizon=TimeHorizon.SHORT_TERM,
                affected_asset=ticker,
                reasoning=f"{ticker} had company-specific momentum.",
                evidence_excerpt=f"{ticker} had strong commentary.",
            )
        ],
    )


def _bundle_for(ticker: str, article_id: int) -> RetrievalEvidenceBundle:
    return RetrievalEvidenceBundle(
        query=ticker,
        search_results=[],
        reranked_results=[],
        evidence=[
            RetrievedArticleEvidence(
                id=1,
                article_id=article_id,
                title=f"{ticker} title",
                url=f"https://example.com/{ticker.lower()}",
                summary=f"{ticker} summary",
                excerpt=f"{ticker} excerpt",
                snippet=f"{ticker} snippet",
                published_at=datetime.now().isoformat(),
                rerank_position=1,
            )
        ],
        rerank_metadata=None,
    )


def test_watchlist_research_batches_internally_and_keeps_triage_review_serial():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        msft_article = _store_article(storage, "MSFT article", "https://example.com/msft")
        nvda_article = _store_article(storage, "NVDA article", "https://example.com/nvda")
        aapl_article = _store_article(storage, "AAPL article", "https://example.com/aapl")
        _store_event(storage, msft_article, "MSFT", "evt-msft")
        _store_event(storage, nvda_article, "NVDA", "evt-nvda")
        _store_event(storage, aapl_article, "AAPL", "evt-aapl")

        batches: list[list[str]] = []

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            batches.append([item.key for item in work_items])
            article_map = {
                "MSFT": msft_article,
                "NVDA": nvda_article,
                "AAPL": aapl_article,
            }
            return [
                RetrievalWorkResult(
                    key=item.key,
                    query=item.query,
                    status="success",
                    bundle=_bundle_for(item.key, article_map[item.key]),
                    error_message=None,
                    elapsed_seconds=0.01,
                )
                for item in work_items
            ]

        triage_client = FakeTriageClient()
        reviewer_client = FakeReviewerClient()
        outcome = run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT", "NVDA", "AAPL"], top_n=2, retrieval_top_k=1),
            storage,
            object(),
            config=WatchlistResearchConfig(auto_batch_threshold=2, auto_batch_size=2, max_concurrency=5),
            triage_agent=WatchlistTriageAgent(triage_client),
            reviewer_agent=WatchlistReviewerAgent(reviewer_client),
            execute_batch_fn=fake_execute_batch,
        )

        assert batches == [["MSFT", "NVDA"], ["AAPL"]]
        assert triage_client.calls == ["MSFT", "NVDA", "AAPL"]
        assert reviewer_client.calls == ["MSFT", "NVDA", "AAPL"]
        assert [item.ticker for item in outcome.result.ranked_items] == ["MSFT", "AAPL", "NVDA"]
        assert outcome.batching["enabled"] is True
        assert outcome.batching["batch_count"] == 2
        assert outcome.batching["report_supported"] is True

        summary = storage.fetch_watchlist_run_summary(outcome.result.run_id)
        assert summary is not None
        assert summary["run"]["watchlist_size"] == 3
        storage.close()


def test_watchlist_research_returns_complete_result_when_some_retrievals_fail():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        msft_article = _store_article(storage, "MSFT article", "https://example.com/msft")
        _store_event(storage, msft_article, "MSFT", "evt-msft")

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            return [
                RetrievalWorkResult(
                    key="MSFT",
                    query="MSFT",
                    status="success",
                    bundle=_bundle_for("MSFT", msft_article),
                    error_message=None,
                    elapsed_seconds=0.01,
                ),
                RetrievalWorkResult(
                    key="NVDA",
                    query="NVDA",
                    status="error",
                    bundle=None,
                    error_message="backend unavailable",
                    elapsed_seconds=0.01,
                ),
            ]

        outcome = run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"], top_n=2, retrieval_top_k=1),
            storage,
            object(),
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            execute_batch_fn=fake_execute_batch,
        )

        assert [item.ticker for item in outcome.result.ranked_items] == ["MSFT", "NVDA"]
        assert outcome.result.retrieval_failures is not None
        assert outcome.result.retrieval_failures[0].ticker == "NVDA"
        assert outcome.result.retrieval_failures[0].status == "error"
        assert outcome.result.items[1].card.priority.value == "Low"
        storage.close()


def test_watchlist_research_structures_shared_articles_once_per_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        shared_article = _store_article(storage, "Shared article", "https://example.com/shared")

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            return [
                RetrievalWorkResult(
                    key=item.key,
                    query=item.query,
                    status="success",
                    bundle=_bundle_for(item.key, shared_article),
                    error_message=None,
                    elapsed_seconds=0.01,
                )
                for item in work_items
            ]

        structuring_client = FakeStructuringClient(
            response_by_article_id={
                shared_article: [
                    {
                        "event_type": "Company",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Short-term",
                        "affected_asset": "MSFT",
                        "reasoning": "Shared evidence helped MSFT.",
                        "evidence_excerpt": "MSFT excerpt",
                    },
                    {
                        "event_type": "Company",
                        "direction": "Positive",
                        "importance": "High",
                        "time_horizon": "Short-term",
                        "affected_asset": "NVDA",
                        "reasoning": "Shared evidence helped NVDA.",
                        "evidence_excerpt": "NVDA excerpt",
                    },
                ]
            }
        )

        outcome = run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"], top_n=2, retrieval_top_k=1),
            storage,
            lambda: object(),
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            structuring_agent=EventStructuringAgent(llm_client=structuring_client),
            execute_batch_fn=fake_execute_batch,
        )

        assert structuring_client.calls == [shared_article]
        assert [item.ticker for item in outcome.result.items] == ["MSFT", "NVDA"]
        storage.close()


def test_watchlist_research_runs_triage_and_review_with_bounded_concurrency():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        article_ids = {
            ticker: _store_article(storage, f"{ticker} article", f"https://example.com/{ticker.lower()}")
            for ticker in ("MSFT", "NVDA", "AAPL", "TSLA")
        }
        for ticker, article_id in article_ids.items():
            _store_event(storage, article_id, ticker, f"evt-{ticker.lower()}")

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            return [
                RetrievalWorkResult(
                    key=item.key,
                    query=item.query,
                    status="success",
                    bundle=_bundle_for(item.key, article_ids[item.key]),
                    error_message=None,
                    elapsed_seconds=0.01,
                )
                for item in work_items
            ]

        triage_lock = threading.Lock()
        review_lock = threading.Lock()
        triage_active = 0
        review_active = 0
        triage_max = 0
        review_max = 0

        class SlowTriageClient(FakeTriageClient):
            def triage_ticker(self, request):
                nonlocal triage_active, triage_max
                with triage_lock:
                    triage_active += 1
                    triage_max = max(triage_max, triage_active)
                time.sleep(0.03)
                try:
                    return super().triage_ticker(request)
                finally:
                    with triage_lock:
                        triage_active -= 1

        class SlowReviewerClient(FakeReviewerClient):
            def review_ticker(self, request):
                nonlocal review_active, review_max
                with review_lock:
                    review_active += 1
                    review_max = max(review_max, review_active)
                time.sleep(0.03)
                try:
                    return super().review_ticker(request)
                finally:
                    with review_lock:
                        review_active -= 1

        outcome = run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT", "NVDA", "AAPL", "TSLA"], top_n=2, retrieval_top_k=1),
            storage,
            lambda: object(),
            config=WatchlistResearchConfig(auto_batch_threshold=10, auto_batch_size=10, max_concurrency=2),
            triage_agent=WatchlistTriageAgent(SlowTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(SlowReviewerClient()),
            execute_batch_fn=fake_execute_batch,
        )

        assert triage_max >= 2
        assert triage_max <= 2
        assert review_max >= 2
        assert review_max <= 2
        assert [item.ticker for item in outcome.result.items] == ["MSFT", "NVDA", "AAPL", "TSLA"]
        assert len(outcome.result.ranked_items) == 4
        storage.close()


def test_watchlist_research_emits_workflow_and_ticker_progress_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        msft_article = _store_article(storage, "MSFT article", "https://example.com/msft")
        _store_event(storage, msft_article, "MSFT", "evt-msft")
        events = []

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            return [
                RetrievalWorkResult(
                    key="MSFT",
                    query="MSFT",
                    status="success",
                    bundle=_bundle_for("MSFT", msft_article),
                    error_message=None,
                    elapsed_seconds=0.01,
                )
            ]

        run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            storage,
            object(),
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            execute_batch_fn=fake_execute_batch,
            progress_sink=events.append,
        )

        workflow_stages = [(event.stage, event.status) for event in events if event.scope == "workflow"]
        ticker_stages = [(event.ticker, event.stage, event.status) for event in events if event.scope == "ticker"]

        assert ("retrieval", "started") in workflow_stages
        assert ("retrieval", "finished") in workflow_stages
        assert ("shared_structuring", "started") in workflow_stages
        assert ("shared_structuring", "finished") in workflow_stages
        assert ("triage", "started") in workflow_stages
        assert ("triage", "finished") in workflow_stages
        assert ("review", "started") in workflow_stages
        assert ("review", "finished") in workflow_stages
        assert ("persist_report", "started") in workflow_stages
        assert ("persist_report", "finished") in workflow_stages
        assert ("MSFT", "retrieval", "started") in ticker_stages
        assert ("MSFT", "retrieval", "finished") in ticker_stages
        assert ("MSFT", "triage", "started") in ticker_stages
        assert ("MSFT", "triage", "finished") in ticker_stages
        assert ("MSFT", "review", "started") in ticker_stages
        assert ("MSFT", "review", "finished") in ticker_stages
        assert all(event.duration_ms is None or event.duration_ms >= 0 for event in events)
        assert all(
            event.scope != "ticker" or event.stage != "shared_structuring"
            for event in events
        )
        storage.close()


def test_watchlist_research_emits_failed_ticker_progress_when_triage_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        msft_article = _store_article(storage, "MSFT article", "https://example.com/msft")
        nvda_article = _store_article(storage, "NVDA article", "https://example.com/nvda")
        _store_event(storage, msft_article, "MSFT", "evt-msft")
        _store_event(storage, nvda_article, "NVDA", "evt-nvda")
        events = []

        def fake_execute_batch(work_items, vector_store_provider, **kwargs):
            article_map = {"MSFT": msft_article, "NVDA": nvda_article}
            return [
                RetrievalWorkResult(
                    key=item.key,
                    query=item.query,
                    status="success",
                    bundle=_bundle_for(item.key, article_map[item.key]),
                    error_message=None,
                    elapsed_seconds=0.01,
                )
                for item in work_items
            ]

        class FailingTriageClient(FakeTriageClient):
            def triage_ticker(self, request):
                if request.ticker == "NVDA":
                    raise RuntimeError("triage boom")
                return super().triage_ticker(request)

        outcome = run_watchlist_research(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"], top_n=2, retrieval_top_k=1),
            storage,
            object(),
            triage_agent=WatchlistTriageAgent(FailingTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            execute_batch_fn=fake_execute_batch,
            progress_sink=events.append,
        )

        assert [item.ticker for item in outcome.result.ranked_items] == ["MSFT", "NVDA"]
        nvda_failed = [
            event
            for event in events
            if event.scope == "ticker" and event.ticker == "NVDA" and event.stage == "triage"
        ]
        assert [event.status for event in nvda_failed] == ["started", "failed"]
        assert "triage boom" in (nvda_failed[-1].message or "")
        storage.close()
