import os
import tempfile
from datetime import datetime

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
)
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.watchlist_triage import (
    FollowupInput,
    HumanReviewInput,
    TriageConfidence,
    TriagePriority,
    WatchlistReviewerAgent,
    WatchlistRunRequest,
    WatchlistTriageAgent,
    render_watchlist_report,
    run_watchlist,
)
from event_collector.reranking import RAGRerankingAgent


class FakeVectorStore:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.results_by_query.get(query, [])[:top_k]


class PassThroughRerankingClient:
    def rerank_candidates(self, request):
        return {
            "ranked_candidates": [
                {"candidate_id": candidate.candidate_id, "reason": f"Kept {candidate.title}"}
                for candidate in request.candidates
            ]
        }


class FakeTriageClient:
    def __init__(self):
        self.calls = []
        self.model = "fake-triage-model"

    def triage_ticker(self, request):
        self.calls.append(request)
        if request.ticker == "MSFT":
            return {
                "ticker": "MSFT",
                "priority": "High",
                "confidence": "High",
                "why_now": "Company-specific demand and pricing evidence justify attention now.",
                "key_evidence": ["Azure demand commentary stayed strong."],
                "counter_evidence": ["Enterprise budgets could tighten if macro weakens."],
                "missing_questions": ["How broad is the AI demand uplift across segments?"],
                "next_action": "Review segment commentary and guidance changes.",
            }
        return {
            "ticker": request.ticker,
            "priority": "Medium",
            "confidence": "Low",
            "why_now": "There is some relevant news, but the evidence is mixed.",
            "key_evidence": ["Recent reporting mentioned the ticker directly."],
            "counter_evidence": [],
            "missing_questions": ["What changed in the latest quarter?"],
            "next_action": "Check the most recent filing and management commentary.",
        }


class FakeReviewerClient:
    def __init__(self):
        self.calls = []
        self.model = "fake-reviewer-model"

    def review_ticker(self, request):
        self.calls.append(request)
        if request.ticker == "MSFT":
            return {
                "evidence_too_generic": False,
                "missing_target_specific_signal": False,
                "reasoning_jump": False,
                "missing_counter_evidence": False,
                "next_action_too_vague": False,
                "summary": "Specific enough for a first-pass review.",
                "should_flag_human_review": False,
            }
        return {
            "evidence_too_generic": True,
            "missing_target_specific_signal": False,
            "reasoning_jump": False,
            "missing_counter_evidence": True,
            "next_action_too_vague": False,
            "summary": "Useful but still a bit generic.",
            "should_flag_human_review": True,
        }


class FakeStructuringAgent:
    def __init__(self, events_by_article_id=None, error_article_ids=None):
        self.events_by_article_id = events_by_article_id or {}
        self.error_article_ids = set(error_article_ids or [])
        self.calls = []
        self.model = "fake-structuring-model"

    def structure_article(self, article):
        self.calls.append(article)
        if article.article_id in self.error_article_ids:
            raise RuntimeError(f"boom-{article.article_id}")
        return list(self.events_by_article_id.get(article.article_id, []))


def _search_result(article_id, title, summary, content, url):
    return {
        "id": str(article_id),
        "article_id": article_id,
        "title": title,
        "summary": summary,
        "content": content,
        "url": url,
        "published_at": datetime.now().isoformat(),
    }


def _store_article(storage, title, url):
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


def _store_event(storage, article_id, affected_asset, event_id):
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
                affected_asset=affected_asset,
                reasoning=f"{affected_asset} had company-specific momentum.",
                evidence_excerpt=f"{affected_asset} had strong commentary.",
            )
        ],
    )


def test_run_watchlist_persists_ranked_results_and_report_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        aapl_article_id = _store_article(storage, "Apple supplier checks stabilize", "https://example.com/aapl")
        _store_event(storage, msft_article_id, "MSFT", "evt-msft")
        _store_event(storage, aapl_article_id, "AAPL", "evt-aapl")

        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ],
                "AAPL": [
                    _search_result(
                        aapl_article_id,
                        "Apple supplier checks stabilize",
                        "Supplier checks improved.",
                        "Apple-related channel commentary is mixed but improving.",
                        "https://example.com/aapl",
                    )
                ],
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["AAPL", "MSFT"], top_n=1, retrieval_top_k=2),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
        )

        assert [item.ticker for item in result.ranked_items] == ["MSFT", "AAPL"]
        assert result.ranked_items[0].priority == TriagePriority.HIGH
        assert result.ranked_items[1].confidence == TriageConfidence.LOW
        assert all(record.card for record in result.items)
        assert all(record.reviewer_finding for record in result.items)

        summary = storage.fetch_watchlist_run_summary(result.run_id)
        assert summary is not None
        assert summary["run"]["watchlist_size"] == 2
        assert [row["ticker"] for row in summary["tickers"]] == ["MSFT", "AAPL"]
        assert summary["evidence_count"] == 2
        assert summary["event_count"] == 2
        assert summary["structuring_attempt_count"] == 0
        assert summary["list_count"] == 5
        assert summary["reviewer_findings"][0]["ticker"] == "AAPL"

        generated_flags = storage.conn.execute(
            "SELECT generated_in_run FROM watchlist_ticker_events WHERE run_id = ? ORDER BY ticker",
            (result.run_id,),
        ).fetchall()
        assert [row["generated_in_run"] for row in generated_flags] == [0, 0]

        report = render_watchlist_report(result, debug_review=True)
        assert "## Top Summary" in report
        assert "| 1 | MSFT | High | High | no |" in report
        assert "## 2. AAPL" in report
        assert "### Reviewer" in report
        assert "### Evidence" in report

        storage.close()


def test_run_watchlist_structures_missing_events_and_persists_them():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ]
            }
        )
        structuring_agent = FakeStructuringAgent(
            events_by_article_id={
                msft_article_id: [
                    StructuredEvent(
                        event_id="evt-msft-generated",
                        article_id=msft_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="MSFT",
                        reasoning="AI demand improved the near-term setup.",
                        evidence_excerpt="Microsoft saw durable AI demand.",
                    )
                ]
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert len(structuring_agent.calls) == 1
        assert structuring_agent.calls[0].article_id == msft_article_id
        assert structuring_agent.calls[0].content.startswith("Microsoft AI demand rises content")
        assert len(result.items[0].structured_signals) == 1
        assert result.items[0].structured_signals[0].event_id == "evt-msft-generated"

        stored_events = storage.list_structured_events_for_article(msft_article_id)
        assert len(stored_events) == 1
        assert stored_events[0].event_id == "evt-msft-generated"
        status, _, error = storage.get_article_structuring_state(msft_article_id)
        assert status == "success"
        assert error is None
        assert result.items[0].structured_signals[0].generated_in_run is True

        event_row = storage.conn.execute(
            "SELECT generated_in_run FROM watchlist_ticker_events WHERE run_id = ? AND ticker = ?",
            (result.run_id, "MSFT"),
        ).fetchone()
        assert event_row["generated_in_run"] == 1
        attempt_row = storage.conn.execute(
            """
            SELECT ticker, article_id, status, error, structuring_model, structuring_prompt_version
            FROM watchlist_ticker_structuring_attempts
            WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchone()
        assert dict(attempt_row) == {
            "ticker": "MSFT",
            "article_id": msft_article_id,
            "status": "success",
            "error": None,
            "structuring_model": "fake-structuring-model",
            "structuring_prompt_version": "event-structuring-v1",
        }

        storage.close()


def test_run_watchlist_records_attempts_for_each_affected_ticker_when_one_article_is_shared():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        shared_article_id = _store_article(storage, "AI infrastructure demand rises", "https://example.com/shared")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        shared_article_id,
                        "AI infrastructure demand rises",
                        "Demand remains strong.",
                        "Microsoft and Nvidia both benefited from AI infrastructure demand.",
                        "https://example.com/shared",
                    )
                ],
                "NVDA": [
                    _search_result(
                        shared_article_id,
                        "AI infrastructure demand rises",
                        "Demand remains strong.",
                        "Microsoft and Nvidia both benefited from AI infrastructure demand.",
                        "https://example.com/shared",
                    )
                ],
            }
        )
        structuring_agent = FakeStructuringAgent(
            events_by_article_id={
                shared_article_id: [
                    StructuredEvent(
                        event_id="evt-msft-shared",
                        article_id=shared_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="MSFT",
                        reasoning="Shared demand improved Microsoft setup.",
                        evidence_excerpt="Microsoft benefited from demand.",
                    ),
                    StructuredEvent(
                        event_id="evt-nvda-shared",
                        article_id=shared_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="NVDA",
                        reasoning="Shared demand improved Nvidia setup.",
                        evidence_excerpt="Nvidia benefited from demand.",
                    ),
                ]
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT", "NVDA"], top_n=2, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert len(structuring_agent.calls) == 1
        attempt_rows = storage.conn.execute(
            """
            SELECT ticker, article_id, status
            FROM watchlist_ticker_structuring_attempts
            WHERE run_id = ?
            ORDER BY ticker
            """,
            (result.run_id,),
        ).fetchall()
        assert [dict(row) for row in attempt_rows] == [
            {"ticker": "MSFT", "article_id": shared_article_id, "status": "success"},
            {"ticker": "NVDA", "article_id": shared_article_id, "status": "success"},
        ]

        storage.close()


def test_run_watchlist_skips_restructuring_when_events_already_exist():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        _store_event(storage, msft_article_id, "MSFT", "evt-msft-existing")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ]
            }
        )
        structuring_agent = FakeStructuringAgent(
            events_by_article_id={
                msft_article_id: [
                    StructuredEvent(
                        event_id="evt-msft-generated",
                        article_id=msft_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="MSFT",
                        reasoning="Should never be used.",
                        evidence_excerpt="Should never be used.",
                    )
                ]
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert structuring_agent.calls == []
        assert len(result.items[0].structured_signals) == 1
        assert result.items[0].structured_signals[0].event_id == "evt-msft-existing"
        assert result.items[0].structured_signals[0].generated_in_run is False
        assert len(storage.list_structured_events_for_article(msft_article_id)) == 1
        status, _, error = storage.get_article_structuring_state(msft_article_id)
        assert status == "success"
        assert error is None
        attempt_count = storage.conn.execute(
            "SELECT COUNT(*) FROM watchlist_ticker_structuring_attempts WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
        assert attempt_count == 0

        storage.close()


def test_run_watchlist_skips_restructuring_after_zero_event_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        storage.mark_article_structuring_result(msft_article_id, status="success")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ]
            }
        )
        structuring_agent = FakeStructuringAgent(
            events_by_article_id={
                msft_article_id: [
                    StructuredEvent(
                        event_id="evt-msft-generated",
                        article_id=msft_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="MSFT",
                        reasoning="Should never be used.",
                        evidence_excerpt="Should never be used.",
                    )
                ]
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert structuring_agent.calls == []
        assert result.items[0].structured_signals == []
        assert result.items[0].card.ticker == "MSFT"
        assert result.items[0].reviewer_finding.should_flag_human_review is False
        status, _, error = storage.get_article_structuring_state(msft_article_id)
        assert status == "success"
        assert error is None

        storage.close()


def test_run_watchlist_force_structure_replaces_existing_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        _store_event(storage, msft_article_id, "MSFT", "evt-msft-existing")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ]
            }
        )
        structuring_agent = FakeStructuringAgent(
            events_by_article_id={
                msft_article_id: [
                    StructuredEvent(
                        event_id="evt-msft-refreshed",
                        article_id=msft_article_id,
                        event_type=EventType.COMPANY,
                        direction=EventDirection.POSITIVE,
                        importance=EventImportance.HIGH,
                        time_horizon=TimeHorizon.SHORT_TERM,
                        affected_asset="MSFT",
                        reasoning="Fresh run replaced the old signal.",
                        evidence_excerpt="Fresh signal.",
                    )
                ]
            }
        )

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1, force_structure=True),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert len(structuring_agent.calls) == 1
        assert [signal.event_id for signal in result.items[0].structured_signals] == ["evt-msft-refreshed"]
        assert result.items[0].structured_signals[0].generated_in_run is True

        stored_events = storage.list_structured_events_for_article(msft_article_id)
        assert [event.event_id for event in stored_events] == ["evt-msft-refreshed"]
        attempt_row = storage.conn.execute(
            """
            SELECT status, structuring_model, structuring_prompt_version
            FROM watchlist_ticker_structuring_attempts
            WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchone()
        assert dict(attempt_row) == {
            "status": "success",
            "structuring_model": "fake-structuring-model",
            "structuring_prompt_version": "event-structuring-v1",
        }

        storage.close()


def test_run_watchlist_continues_when_structuring_fails(caplog):
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        msft_article_id = _store_article(storage, "Microsoft AI demand rises", "https://example.com/msft")
        vector_store = FakeVectorStore(
            {
                "MSFT": [
                    _search_result(
                        msft_article_id,
                        "Microsoft AI demand rises",
                        "Azure demand remains strong.",
                        "Microsoft commentary points to durable AI demand.",
                        "https://example.com/msft",
                    )
                ]
            }
        )
        structuring_agent = FakeStructuringAgent(error_article_ids={msft_article_id})

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=structuring_agent,
        )

        assert len(structuring_agent.calls) == 1
        assert result.items[0].structured_signals == []
        assert storage.list_structured_events_for_article(msft_article_id) == []
        assert "Watchlist structuring failed" in caplog.text
        assert "completed with 1 structuring failure(s) affecting 1 ticker(s): MSFT(1)" in caplog.text
        status, _, error = storage.get_article_structuring_state(msft_article_id)
        assert status == "failed"
        assert "boom-" in error
        failed_attempt = storage.conn.execute(
            """
            SELECT ticker, article_id, status, error, structuring_model, structuring_prompt_version
            FROM watchlist_ticker_structuring_attempts
            WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchone()
        assert failed_attempt["ticker"] == "MSFT"
        assert failed_attempt["article_id"] == msft_article_id
        assert failed_attempt["status"] == "failed"
        assert failed_attempt["error"] == f"boom-{msft_article_id}"
        assert failed_attempt["structuring_model"] == "fake-structuring-model"
        assert failed_attempt["structuring_prompt_version"] == "event-structuring-v1"

        result = run_watchlist(
            WatchlistRunRequest(tickers=["MSFT"], top_n=1, retrieval_top_k=1),
            vector_store,
            storage,
            triage_agent=WatchlistTriageAgent(FakeTriageClient()),
            reviewer_agent=WatchlistReviewerAgent(FakeReviewerClient()),
            reranking_agent=RAGRerankingAgent(PassThroughRerankingClient()),
            structuring_agent=FakeStructuringAgent(
                events_by_article_id={
                    msft_article_id: [
                        StructuredEvent(
                            event_id="evt-msft-generated",
                            article_id=msft_article_id,
                            event_type=EventType.COMPANY,
                            direction=EventDirection.POSITIVE,
                            importance=EventImportance.HIGH,
                            time_horizon=TimeHorizon.SHORT_TERM,
                            affected_asset="MSFT",
                            reasoning="Retry succeeded.",
                            evidence_excerpt="Retry succeeded.",
                        )
                    ]
                }
            ),
        )

        assert len(result.items[0].structured_signals) == 1
        assert result.items[0].structured_signals[0].generated_in_run is True
        retry_status, _, retry_error = storage.get_article_structuring_state(msft_article_id)
        assert retry_status == "success"
        assert retry_error is None

        storage.close()


def test_watchlist_feedback_stub_rows_are_saved():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        review_id = storage.save_watchlist_human_review(
            HumanReviewInput(
                run_id="run-1",
                ticker="MSFT",
                worth_reviewing=True,
                evidence_specific=True,
                reasoning_sound=False,
                notes="Reasoning was a little aggressive.",
            )
        )
        followup_id = storage.save_watchlist_followup(
            FollowupInput(
                run_id="run-1",
                ticker="MSFT",
                still_worth_tracking=True,
                outcome_notes="Still looks worth tracking two days later.",
            )
        )

        review_row = storage.conn.execute(
            "SELECT * FROM watchlist_human_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        followup_row = storage.conn.execute(
            "SELECT * FROM watchlist_followups WHERE id = ?",
            (followup_id,),
        ).fetchone()

        assert review_row["worth_reviewing"] == 1
        assert review_row["reasoning_sound"] == 0
        assert followup_row["still_worth_tracking"] == 1
        assert "worth tracking" in followup_row["outcome_notes"]

        storage.close()


def test_schema_init_keeps_articles_and_structured_events_working():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()

        article_id = _store_article(storage, "Existing path still works", "https://example.com/existing")
        _store_event(storage, article_id, "MSFT", "evt-existing")

        assert storage.count_articles() == 1
        assert len(storage.list_structured_events_for_article(article_id)) == 1
        assert storage.conn.execute("SELECT COUNT(*) FROM watchlist_runs").fetchone()[0] == 0

        storage.close()
