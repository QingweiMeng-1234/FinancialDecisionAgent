from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile

from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.article_content import ArticleFetchError, FetchFailureReason
from event_collector.theme_research import (
    SearchResult,
    ThemeEvidence,
    ThemeResearchRequest,
    classify_source_kind,
    compute_sufficiency,
    normalize_theme_research_request,
    run_theme_research,
    slugify_theme,
)


class FakeSearchProvider:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    def search(self, query: str, *, num_results: int):
        self.calls.append((query, num_results))
        return list(self.results_by_query.get(query, []))


class FakeFetcher:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def fetch_with_classification(self, url: str):
        self.calls.append(url)
        payload = self.payloads[url]
        return type(
            "FetchResult",
            (),
            {
                "original_url": url,
                "canonical_url": payload.get("canonical_url"),
                "content": payload["content"],
            },
        )()


class FakeVectorStore:
    def __init__(self, search_results=None):
        self.search_results = search_results or {}
        self.added = []
        self.search_calls = []

    def add_article(self, article_id, article):
        self.added.append((article_id, article.title))
        return [f"{article_id}:0"]

    def search(self, query: str, top_k: int = 5, **kwargs):
        self.search_calls.append((query, top_k))
        return list(self.search_results.get(query, []))


def test_normalization_and_slug_generation():
    normalized = normalize_theme_research_request(
        ThemeResearchRequest(
            theme="  AI Infrastructure  ",
            analysis_goal="  map bottlenecks ",
            seed_query=" ai power demand ",
            tickers=["nvda", " msft ", "NVDA"],
        )
    )

    assert normalized.theme == "AI Infrastructure"
    assert normalized.analysis_goal == "map bottlenecks"
    assert normalized.seed_query == "ai power demand"
    assert normalized.tickers == ["NVDA", "MSFT"]
    assert slugify_theme(normalized.theme) == "ai-infrastructure"


def test_ticker_hints_are_optional_and_not_primary_input():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        provider = FakeSearchProvider(
            {
                "AI datacenter power demand": [
                    SearchResult(
                        title="Power demand rises",
                        url="https://www.reuters.com/world/us/power-demand-rises",
                        snippet="Utilities and hyperscalers discuss load growth.",
                        published_at=datetime.now(),
                    )
                ]
            }
        )
        fetcher = FakeFetcher(
            {
                "https://www.reuters.com/world/us/power-demand-rises": {
                    "content": "Utilities and hyperscalers are spending more on power and cooling.",
                    "canonical_url": "https://www.reuters.com/world/us/power-demand-rises",
                }
            }
        )
        vector_store = FakeVectorStore()

        result = run_theme_research(
            ThemeResearchRequest(
                theme="AI",
                analysis_goal="datacenter power demand",
                seed_query="AI datacenter power demand",
                tickers=None,
                db_path=os.path.join(tmpdir, "news.db"),
                research_root=os.path.join(tmpdir, "research"),
                local_retrieval_top_k=0,
            ),
            storage=storage,
            local_vector_reader=vector_store,
            search_provider=provider,
            content_fetcher=fetcher,
        )

        assert result.metadata.theme == "AI"
        assert result.metadata.tickers == []
        assert provider.calls[0][0] == "AI datacenter power demand"
        storage.close()


def test_source_aware_sufficiency_allows_low_count_primary_and_company_evidence():
    evidence = [
        ThemeEvidence(
            article_id=1,
            title="10-K filing",
            url="https://www.sec.gov/Archives/edgar/data/1/test.htm",
            summary=None,
            published_at=datetime.now(),
            source_kind="primary",
            acquisition_channel="open_search",
            domain="www.sec.gov",
        ),
        ThemeEvidence(
            article_id=2,
            title="Investor presentation",
            url="https://investor.nvidia.com/presentation.pdf",
            summary=None,
            published_at=datetime.now(),
            source_kind="company",
            acquisition_channel="open_search",
            domain="investor.nvidia.com",
        ),
    ]

    sufficiency = compute_sufficiency(evidence)

    assert sufficiency.structure_sufficient is True
    assert sufficiency.fresh_monitoring_sufficient is True
    assert sufficiency.counts_by_kind["primary"] == 1
    assert sufficiency.counts_by_kind["company"] == 1


def test_classify_source_kind_distinguishes_primary_company_secondary_and_commentary():
    assert classify_source_kind("https://www.sec.gov/Archives/edgar/data/1/test.htm") == "primary"
    assert classify_source_kind("https://investor.nvidia.com/news/default.aspx") == "company"
    assert classify_source_kind("https://www.reuters.com/markets/us/chips") == "secondary"
    assert classify_source_kind("https://www.seekingalpha.com/article/123") == "commentary"


def test_acquisition_persists_discovered_urls_without_mutating_active_index():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        storage = SQLiteNewsStore(db_path=db_path)
        storage.init_db()
        provider = FakeSearchProvider(
            {
                "AI infrastructure map bottlenecks": [
                    SearchResult(
                        title="NVIDIA discusses rack scale demand",
                        url="https://investor.nvidia.com/news/rack-scale-demand",
                        snippet="Investor relations update.",
                        published_at=datetime.now(),
                    )
                ]
            }
        )
        fetcher = FakeFetcher(
            {
                "https://investor.nvidia.com/news/rack-scale-demand": {
                    "content": "NVIDIA discussed rack scale AI demand, power needs, and supply constraints.",
                    "canonical_url": "https://investor.nvidia.com/news/rack-scale-demand",
                }
            }
        )
        vector_store = FakeVectorStore()
        generation_handoffs = []

        result = run_theme_research(
            ThemeResearchRequest(
                theme="AI infrastructure",
                analysis_goal="map bottlenecks",
                seed_query="AI infrastructure map bottlenecks",
                tickers=["NVDA"],
                db_path=db_path,
                research_root=os.path.join(tmpdir, "research"),
                local_retrieval_top_k=0,
            ),
            storage=storage,
            local_vector_reader=vector_store,
            discovered_article_sink=lambda article_id, content_sha256: generation_handoffs.append(
                (article_id, content_sha256)
            ),
            search_provider=provider,
            content_fetcher=fetcher,
        )

        records = storage.list_article_records(source="theme_search")
        assert len(records) == 1
        assert records[0].article.original_url == "https://investor.nvidia.com/news/rack-scale-demand"
        assert records[0].article.content_status == "ready"
        assert records[0].article.summary_status == "ready"
        assert records[0].article.index_status != "ready"
        assert vector_store.added == []
        assert generation_handoffs == [
            (records[0].id, records[0].article.active_content_sha256)
        ]
        assert result.evidence_provenance["acquisition"]["generation_handoff"] == {
            "scheduled": 1,
            "article_ids": [records[0].id],
        }
        assert result.evidence[0].is_newly_ingested is True
        storage.close()


def test_acquisition_isolates_one_publisher_fetch_failure_and_records_safe_outcome():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        storage = SQLiteNewsStore(db_path=db_path)
        storage.init_db()
        failed_url = "https://publisher.example/blocked"
        normalized_failure_url = "https://publisher.example/broken-client"
        accepted_url = "https://www.reuters.com/technology/accepted"
        provider = FakeSearchProvider(
            {
                "AI infrastructure map bottlenecks": [
                    SearchResult("Blocked publisher", failed_url),
                    SearchResult("Broken client", normalized_failure_url),
                    SearchResult("Accepted publisher", accepted_url),
                ]
            }
        )

        class PartiallyFailingFetcher(FakeFetcher):
            def fetch_with_classification(self, url: str):
                if url == failed_url:
                    self.calls.append(url)
                    raise ArticleFetchError(
                        FetchFailureReason.HTTP_429,
                        "publisher response exposed token=do-not-record",
                        url=url,
                    )
                if url == normalized_failure_url:
                    self.calls.append(url)
                    raise RuntimeError("client error carried credential=also-do-not-record")
                return super().fetch_with_classification(url)

        fetcher = PartiallyFailingFetcher(
            {
                accepted_url: {
                    "content": "Accepted publisher evidence describes AI infrastructure bottlenecks.",
                    "canonical_url": accepted_url,
                }
            }
        )

        result = run_theme_research(
            ThemeResearchRequest(
                theme="AI infrastructure",
                analysis_goal="map bottlenecks",
                seed_query="AI infrastructure map bottlenecks",
                db_path=db_path,
                research_root=os.path.join(tmpdir, "research"),
                local_retrieval_top_k=0,
            ),
            storage=storage,
            local_vector_reader=FakeVectorStore(),
            retrieval_provenance={
                "generation_id": "gen-active",
                "corpus_snapshot_id": "snapshot-1",
                "index_config_fingerprint": "config-1",
            },
            search_provider=provider,
            content_fetcher=fetcher,
        )

        assert fetcher.calls == [failed_url, normalized_failure_url, accepted_url]
        assert [item.url for item in result.evidence] == [accepted_url]
        assert result.evidence_provenance["acquisition"] == {
            "attempted": 3,
            "accepted": 1,
            "failed": 2,
            "failure_codes": {"fetch_exception": 1, "http_429": 1},
            "generation_handoff": {"scheduled": 0, "article_ids": []},
        }
        assert "token=do-not-record" not in str(result.evidence_provenance)
        assert "also-do-not-record" not in str(result.evidence_provenance)
        storage.close()


def test_asset_updates_rewrite_state_files_and_append_history_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        research_root = os.path.join(tmpdir, "research")
        theme_dir = os.path.join(research_root, "power-cooling")
        os.makedirs(theme_dir, exist_ok=True)
        with open(os.path.join(theme_dir, "theme-card.md"), "w", encoding="utf-8") as handle:
            handle.write("OLD THEME CARD\n")
        with open(os.path.join(theme_dir, "supply-chain-map.md"), "w", encoding="utf-8") as handle:
            handle.write("OLD SUPPLY MAP\n")
        with open(os.path.join(theme_dir, "evidence-log.md"), "w", encoding="utf-8") as handle:
            handle.write("# Evidence Log\n\nlegacy evidence")
        with open(os.path.join(theme_dir, "monitoring-triggers.md"), "w", encoding="utf-8") as handle:
            handle.write("# Monitoring Triggers\n\nlegacy trigger")

        storage = SQLiteNewsStore(db_path=db_path)
        storage.init_db()
        provider = FakeSearchProvider(
            {
                "power cooling monitor datacenter buildout": [
                    SearchResult(
                        title="Cooling vendors add capacity",
                        url="https://www.reuters.com/technology/cooling-vendors-capacity",
                        snippet="Cooling supply tightens.",
                        published_at=datetime.now(),
                    )
                ]
            }
        )
        fetcher = FakeFetcher(
            {
                "https://www.reuters.com/technology/cooling-vendors-capacity": {
                    "content": "Cooling vendors are adding manufacturing capacity for data center demand.",
                    "canonical_url": "https://www.reuters.com/technology/cooling-vendors-capacity",
                }
            }
        )

        run_theme_research(
            ThemeResearchRequest(
                theme="Power Cooling",
                analysis_goal="monitor datacenter buildout",
                seed_query="power cooling monitor datacenter buildout",
                db_path=db_path,
                research_root=research_root,
                local_retrieval_top_k=0,
            ),
            storage=storage,
            local_vector_reader=FakeVectorStore(),
            retrieval_provenance={
                "generation_id": "gen-active",
                "corpus_snapshot_id": "snapshot-1",
                "index_config_fingerprint": "config-1",
            },
            search_provider=provider,
            content_fetcher=fetcher,
        )

        with open(os.path.join(theme_dir, "theme-card.md"), "r", encoding="utf-8") as handle:
            theme_card = handle.read()
        with open(os.path.join(theme_dir, "supply-chain-map.md"), "r", encoding="utf-8") as handle:
            supply_map = handle.read()
        with open(os.path.join(theme_dir, "evidence-log.md"), "r", encoding="utf-8") as handle:
            evidence_log = handle.read()
        with open(os.path.join(theme_dir, "monitoring-triggers.md"), "r", encoding="utf-8") as handle:
            monitoring_log = handle.read()

        assert "OLD THEME CARD" not in theme_card
        assert "Generation ID: gen-active" in theme_card
        assert "OLD SUPPLY MAP" not in supply_map
        assert "legacy evidence" in evidence_log
        assert "Cooling vendors add capacity" in evidence_log
        assert "Corpus snapshot ID: snapshot-1" in evidence_log
        assert "legacy trigger" in monitoring_log
        assert "Fresh monitoring sufficient" in monitoring_log
        storage.close()


def test_local_corpus_support_and_result_payload_include_related_companies_and_segments():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "news.db")
        storage = SQLiteNewsStore(db_path=db_path)
        storage.init_db()
        local_article_id = storage.save_article(
            NewsArticle(
                source="news",
                title="TSLA builds new battery materials supply line",
                description="Battery materials and logistics matter more.",
                content="Battery materials, logistics, and upstream suppliers are expanding.",
                url="https://www.reuters.com/business/autos-transportation/tsla-battery-materials",
                published_at=datetime.now() - timedelta(days=3),
                summary="- Battery materials are a growing constraint.",
            )
        )
        storage.mark_article_processing_status(local_article_id, index_status="ready", summary_status="ready")
        provider = FakeSearchProvider({})
        vector_store = FakeVectorStore(
            search_results={
                "battery supply chain map bottlenecks": [{"article_id": local_article_id}],
                "battery supply chain industry structure primary sources": [{"article_id": local_article_id}],
                "battery supply chain investor relations annual report supply chain": [{"article_id": local_article_id}],
            }
        )

        result = run_theme_research(
            ThemeResearchRequest(
                theme="battery supply chain",
                analysis_goal="map bottlenecks",
                tickers=["TSLA"],
                db_path=db_path,
                research_root=os.path.join(tmpdir, "research"),
                local_retrieval_top_k=2,
            ),
            storage=storage,
            local_vector_reader=vector_store,
            search_provider=provider,
            content_fetcher=FakeFetcher({}),
        )

        assert "TSLA" in result.related_companies
        assert any(segment in result.candidate_segments for segment in ("materials", "logistics", "upstream inputs"))
        assert set(result.artifact_paths) == {"theme_card", "supply_chain_map", "evidence_log", "monitoring_triggers"}
        assert "counts_by_kind" in result.evidence_provenance
        storage.close()


def test_local_support_uses_request_pinned_reader_metadata_not_live_database():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        reader = FakeVectorStore(
            search_results={
                "AI infrastructure map bottlenecks": [
                    {
                        "article_id": 7,
                        "generation_id": "gen-active",
                        "indexed_content_sha256": "a" * 64,
                        "title": "Pinned title",
                        "url": "https://www.reuters.com/technology/pinned",
                        "summary": "Pinned summary",
                        "published_at": "2026-08-15T10:00:00",
                    }
                ]
            }
        )
        storage.get_article_record = lambda article_id: (_ for _ in ()).throw(
            AssertionError("live article metadata must not be read")
        )

        result = run_theme_research(
            ThemeResearchRequest(
                theme="AI infrastructure",
                analysis_goal="map bottlenecks",
                seed_query="AI infrastructure map bottlenecks",
                db_path=os.path.join(tmpdir, "news.db"),
                research_root=os.path.join(tmpdir, "research"),
                local_retrieval_top_k=2,
                max_discovered_articles=0,
            ),
            storage=storage,
            local_vector_reader=reader,
            search_provider=FakeSearchProvider({}),
            content_fetcher=FakeFetcher({}),
        )

        assert result.evidence[0].title == "Pinned title"
        assert result.evidence[0].summary == "Pinned summary"
        storage.close()


def test_theme_research_isolates_one_summary_failure_and_keeps_other_discovery():
    """A summary error is per article, not a fatal error for the theme run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        failed_url = "https://www.reuters.com/technology/summary-fails"
        accepted_url = "https://www.reuters.com/technology/summary-succeeds"

        class OneSummaryFails:
            def summarize(self, article):
                if article.title == "Summary fails":
                    raise RuntimeError("summary provider unavailable")
                return "- Valid summary"

        try:
            result = run_theme_research(
                ThemeResearchRequest(
                    theme="AI infrastructure",
                    analysis_goal="monitor constraints",
                    seed_query="AI infrastructure monitor constraints",
                    db_path=os.path.join(tmpdir, "news.db"),
                    research_root=os.path.join(tmpdir, "research"),
                    local_retrieval_top_k=0,
                ),
                storage=storage,
                local_vector_reader=FakeVectorStore(),
                search_provider=FakeSearchProvider(
                    {
                        "AI infrastructure monitor constraints": [
                            SearchResult("Summary fails", failed_url),
                            SearchResult("Summary succeeds", accepted_url),
                        ]
                    }
                ),
                content_fetcher=FakeFetcher(
                    {
                        failed_url: {"content": "A fetched article with a failing summary.", "canonical_url": failed_url},
                        accepted_url: {"content": "A fetched article with a valid summary.", "canonical_url": accepted_url},
                    }
                ),
                summarizer=OneSummaryFails(),
            )
            assert any(item.title == "Summary succeeds" for item in result.evidence)
        finally:
            storage.close()


def test_theme_research_isolates_one_successor_handoff_failure_and_keeps_other_discovery():
    """A failed successor-generation handoff cannot abort unrelated discovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        failed_url = "https://www.reuters.com/technology/handoff-fails"
        accepted_url = "https://www.reuters.com/technology/handoff-succeeds"

        handoff_calls = []

        def handoff(article_id: int, content_sha256: str) -> None:
            handoff_calls.append((article_id, content_sha256))
            if len(handoff_calls) == 1:
                raise RuntimeError("successor generation scheduler unavailable")

        try:
            result = run_theme_research(
                ThemeResearchRequest(
                    theme="AI infrastructure",
                    analysis_goal="monitor constraints",
                    seed_query="AI infrastructure monitor constraints",
                    db_path=os.path.join(tmpdir, "news.db"),
                    research_root=os.path.join(tmpdir, "research"),
                    local_retrieval_top_k=0,
                ),
                storage=storage,
                local_vector_reader=FakeVectorStore(),
                discovered_article_sink=handoff,
                search_provider=FakeSearchProvider(
                    {
                        "AI infrastructure monitor constraints": [
                            SearchResult("Handoff fails", failed_url),
                            SearchResult("Handoff succeeds", accepted_url),
                        ]
                    }
                ),
                content_fetcher=FakeFetcher(
                    {
                        failed_url: {"content": "A fetched article with a failing handoff.", "canonical_url": failed_url},
                        accepted_url: {"content": "A fetched article with a valid handoff.", "canonical_url": accepted_url},
                    }
                ),
            )
            assert any(item.title == "Handoff succeeds" for item in result.evidence)
        finally:
            storage.close()


def test_unknown_source_published_date_remains_unknown_in_theme_evidence():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        storage.init_db()
        url = "https://www.reuters.com/technology/unknown-source-date"
        try:
            result = run_theme_research(
                ThemeResearchRequest(
                    theme="AI infrastructure",
                    analysis_goal="monitor constraints",
                    seed_query="AI infrastructure monitor constraints",
                    db_path=os.path.join(tmpdir, "news.db"),
                    research_root=os.path.join(tmpdir, "research"),
                    local_retrieval_top_k=0,
                ),
                storage=storage,
                local_vector_reader=FakeVectorStore(),
                search_provider=FakeSearchProvider(
                    {"AI infrastructure monitor constraints": [SearchResult("Unknown date", url)]}
                ),
                content_fetcher=FakeFetcher(
                    {url: {"content": "Publisher did not provide a publication date.", "canonical_url": url}}
                ),
            )
            assert result.evidence[0].source_published_at is None
            assert result.evidence[0].published_at_provenance == "unknown"
            row = storage.conn.execute(
                "SELECT published_at, source_published_at, published_at_provenance FROM articles"
            ).fetchone()
            assert row["published_at"]
            assert row["source_published_at"] is None
            assert row["published_at_provenance"] == "unknown"
        finally:
            storage.close()


def test_sufficiency_handles_utc_aware_source_dates_without_naive_datetime_comparison():
    status = compute_sufficiency(
        [
            ThemeEvidence(
                article_id=1,
                title="UTC source date",
                url="https://www.reuters.com/technology/utc-date",
                summary=None,
                published_at=datetime.now(timezone.utc),
                source_kind="secondary",
                acquisition_channel="open_search",
                domain="www.reuters.com",
            )
        ]
    )

    assert status.fresh_evidence == 1


def test_old_secondary_and_commentary_items_do_not_make_fresh_monitoring_sufficient():
    old = datetime.now() - timedelta(days=366)
    status = compute_sufficiency(
        [
            ThemeEvidence(1, "Old secondary one", "https://www.reuters.com/one", None, old, "secondary", "open_search", "www.reuters.com"),
            ThemeEvidence(2, "Old secondary two", "https://www.reuters.com/two", None, old, "secondary", "open_search", "www.reuters.com"),
            ThemeEvidence(3, "Old commentary", "https://www.seekingalpha.com/three", None, old, "commentary", "open_search", "www.seekingalpha.com"),
        ]
    )

    assert status.fresh_evidence == 0
    assert status.fresh_monitoring_sufficient is False


def test_unverified_generic_domain_is_not_classified_as_company_source():
    assert classify_source_kind("https://example.com/investor-update") == "secondary"


def test_theme_evidence_runtime_schema_carries_source_date_and_provenance():
    source_published_at = datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)
    evidence = ThemeEvidence(
        article_id=1,
        title="Source metadata is preserved",
        url="https://www.reuters.com/technology/source-metadata",
        summary=None,
        published_at=source_published_at,
        source_published_at=source_published_at,
        published_at_provenance="source_metadata",
        source_kind="secondary",
        acquisition_channel="open_search",
        domain="www.reuters.com",
    )

    assert evidence.source_published_at == source_published_at
    assert evidence.published_at_provenance == "source_metadata"

