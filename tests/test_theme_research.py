from __future__ import annotations

from datetime import datetime, timedelta
import os
import tempfile

import pytest

from event_collector.news_storage import NewsArticle, SQLiteNewsStore
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
            vector_store=vector_store,
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


def test_acquisition_persists_discovered_urls_and_indexes_articles():
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
            vector_store=vector_store,
            search_provider=provider,
            content_fetcher=fetcher,
        )

        records = storage.list_article_records(source="theme_search")
        assert len(records) == 1
        assert records[0].article.original_url == "https://investor.nvidia.com/news/rack-scale-demand"
        assert records[0].article.content_status == "ready"
        assert records[0].article.summary_status == "ready"
        assert records[0].article.index_status == "ready"
        assert records[0].id in storage.list_retrieval_eligible_article_ids()
        assert vector_store.added == [(records[0].id, "NVIDIA discusses rack scale demand")]
        assert result.evidence[0].is_newly_ingested is True
        storage.close()


def test_theme_research_resumes_failed_index_on_existing_content(tmp_path):
    storage = SQLiteNewsStore(db_path=str(tmp_path / "news.db"))
    url = "https://example.com/power"
    request = ThemeResearchRequest(theme="Power", analysis_goal="capacity", seed_query="power",
                                   research_root=str(tmp_path / "research"), local_retrieval_top_k=0)
    provider = FakeSearchProvider({"power": [SearchResult(title="Power", url=url, snippet="Capacity")]})
    fetcher = FakeFetcher({url: {"content": "Power capacity is expanding."}})

    class RetryIndex(FakeVectorStore):
        def add_article(self, article_id, article):
            result = super().add_article(article_id, article)
            if len(self.added) == 1:
                raise RuntimeError("index unavailable")
            return result

    index = RetryIndex()
    try:
        with pytest.raises(RuntimeError, match="index unavailable"):
            run_theme_research(request, storage=storage, vector_store=index,
                               search_provider=provider, content_fetcher=fetcher)
        result = run_theme_research(request, storage=storage, vector_store=index,
                                   search_provider=provider, content_fetcher=fetcher)
        assert result.evidence[0].article_id in storage.list_retrieval_eligible_article_ids()
        assert fetcher.calls == [url]
    finally:
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
            vector_store=FakeVectorStore(),
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
        assert "OLD SUPPLY MAP" not in supply_map
        assert "legacy evidence" in evidence_log
        assert "Cooling vendors add capacity" in evidence_log
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
            vector_store=vector_store,
            search_provider=provider,
            content_fetcher=FakeFetcher({}),
        )

        assert "TSLA" in result.related_companies
        assert any(segment in result.candidate_segments for segment in ("materials", "logistics", "upstream inputs"))
        assert set(result.artifact_paths) == {"theme_card", "supply_chain_map", "evidence_log", "monitoring_triggers"}
        assert "counts_by_kind" in result.evidence_provenance
        storage.close()

