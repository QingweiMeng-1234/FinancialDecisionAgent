import json
from datetime import datetime, timedelta

from event_collector.entity_kb import CompanyProfile, SQLiteEntityStore
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
import run_retrieval_eval
from event_collector.reranking import RAGRerankingAgent as RealRerankingAgent


class FakeVectorStore:
    def __init__(self, persist_dir, collection_name, *, db_path):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.results_by_query = {
            "MSFT": [
                {
                    "id": "1",
                    "article_id": 1,
                    "title": "Microsoft wins contract",
                    "summary": "Microsoft announced a large contract.",
                    "content": "Microsoft announced a large contract.",
                    "url": "https://example.com/1",
                    "published_at": "2026-05-15T00:00:00",
                }
            ],
            "MSFT Microsoft Microsoft Corp": [
                {
                    "id": "1",
                    "article_id": 1,
                    "title": "Microsoft wins contract",
                    "summary": "Microsoft announced a large contract.",
                    "content": "Microsoft announced a large contract.",
                    "url": "https://example.com/1",
                    "published_at": "2026-05-15T00:00:00",
                }
            ],
        }

    def search(self, query, top_k=5, *, allowed_article_ids=None):
        results = self.results_by_query.get(query, [])[:top_k]
        if allowed_article_ids is None:
            return results
        return [item for item in results if item.get("article_id") in allowed_article_ids]


class PassThroughRerankingClient:
    def rerank_candidates(self, request):
        return {
            "ranked_candidates": [
                {"candidate_id": candidate.candidate_id, "reason": f"Kept {candidate.title}"}
                for candidate in request.candidates
            ]
        }


def _create_entity_db(path, *profiles: CompanyProfile):
    store = SQLiteEntityStore(db_path=str(path))
    store.init_db()
    for profile in profiles:
        store.upsert_company_profile(profile)
    store.close()


def _patch_rerankers(monkeypatch, *, include_deepseek: bool = False):
    primary = RealRerankingAgent(PassThroughRerankingClient())
    deepseek = RealRerankingAgent(PassThroughRerankingClient()) if include_deepseek else None
    chain_name = "entity_kb_enhanced"
    monkeypatch.setattr(run_retrieval_eval, "_build_reranking_agents", lambda provider: (primary, deepseek, chain_name))


def test_run_retrieval_eval_generates_annotations_and_evaluates(tmp_path, monkeypatch):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - MSFT\n", encoding="utf-8")

    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        ),
    )

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", FakeVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="Microsoft announced a large AI contract.",
            url="https://example.com/1",
            published_at=datetime.now(),
            index_status="ready",
            summary="- Microsoft signed a contract.\n- The contract is material.\n- Azure is involved.",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    exit_code = run_retrieval_eval.main(
        [
            "generate-annotations",
            "--ticker-list-path",
            str(ticker_list),
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-output-dir",
            str(annotation_dir),
        ]
    )

    assert exit_code == 0
    annotation_file = annotation_dir / "MSFT.yaml"
    assert annotation_file.exists()

    content = annotation_file.read_text(encoding="utf-8")
    assert "excerpt:" in content
    assert "content_path:" in content
    assert "dedupe_key:" in content
    assert "summary:" not in content
    content = content.replace("label: null", "label: relevant", 1)
    annotation_file.write_text(content, encoding="utf-8")

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
        ]
    )

    assert exit_code == 0
    assert (report_dir / "retrieval_eval_report.md").exists()
    assert (report_dir / "retrieval_eval_results.json").exists()


def test_run_retrieval_eval_relevant_count_mode_expands_candidate_pool(tmp_path, monkeypatch):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - MSFT\n", encoding="utf-8")

    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        ),
    )

    class MultiResultVectorStore(FakeVectorStore):
        def __init__(self, persist_dir, collection_name, *, db_path):
            super().__init__(persist_dir, collection_name, db_path=db_path)
            self.results_by_query = {
                "MSFT": [
                    {
                        "id": "1",
                        "article_id": 1,
                        "title": "Microsoft article 1",
                        "summary": "Microsoft announced detail 1.",
                        "content": "Microsoft announced detail 1.",
                        "url": "https://example.com/1",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Microsoft article 2",
                        "summary": "Microsoft announced detail 2.",
                        "content": "Microsoft announced detail 2.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "3",
                        "article_id": 3,
                        "title": "Microsoft article 3",
                        "summary": "Microsoft announced detail 3.",
                        "content": "Microsoft announced detail 3.",
                        "url": "https://example.com/3",
                        "published_at": "2026-05-15T00:00:00",
                    },
                ],
                "MSFT Microsoft Microsoft Corp": [
                    {
                        "id": "1",
                        "article_id": 1,
                        "title": "Microsoft article 1",
                        "summary": "Microsoft announced detail 1.",
                        "content": "Microsoft announced detail 1.",
                        "url": "https://example.com/1",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Microsoft article 2",
                        "summary": "Microsoft announced detail 2.",
                        "content": "Microsoft announced detail 2.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "3",
                        "article_id": 3,
                        "title": "Microsoft article 3",
                        "summary": "Microsoft announced detail 3.",
                        "content": "Microsoft announced detail 3.",
                        "url": "https://example.com/3",
                        "published_at": "2026-05-15T00:00:00",
                    },
                ],
            }

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", MultiResultVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    for article_id in range(1, 5):
        storage.save_article(
            NewsArticle(
                source="news",
                title=f"Microsoft article {article_id}",
                description="Microsoft relevant article",
                content=f"Microsoft announced detail {article_id}.",
                url=f"https://example.com/{article_id}",
                published_at=datetime.now(),
                index_status="ready",
            )
        )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir(parents=True, exist_ok=True)
    (annotation_dir / "MSFT.yaml").write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft article 1\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced detail 1.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: msft-1\n"
            "    label: relevant\n"
            "    notes: ''\n"
            "  - article_id: 2\n"
            "    title: Microsoft article 2\n"
            "    url: https://example.com/2\n"
            "    excerpt: Microsoft announced detail 2.\n"
            "    content_path: C:/tmp/article-2.txt\n"
            "    dedupe_key: msft-2\n"
            "    label: relevant\n"
            "    notes: ''\n"
            "  - article_id: 3\n"
            "    title: Microsoft article 3\n"
            "    url: https://example.com/3\n"
            "    excerpt: Microsoft announced detail 3.\n"
            "    content_path: C:/tmp/article-3.txt\n"
            "    dedupe_key: msft-3\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
            "--retrieval-top-k",
            "1",
            "--evaluation-top-k-mode",
            "relevant_count",
            "--no-show-progress",
        ]
    )

    assert exit_code == 0
    results = json.loads((report_dir / "retrieval_eval_results.json").read_text(encoding="utf-8"))
    assert results["per_ticker"][0]["evaluation_k"] == 3


def test_run_retrieval_eval_generate_annotations_uses_fresh_ingest_only(tmp_path, monkeypatch):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - MSFT\n", encoding="utf-8")

    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        ),
    )

    class FreshFilterVectorStore(FakeVectorStore):
        def __init__(self, persist_dir, collection_name, *, db_path):
            super().__init__(persist_dir, collection_name, db_path=db_path)
            self.results_by_query = {
                "MSFT": [
                    {
                        "id": "1",
                        "article_id": 1,
                        "title": "Fresh Microsoft wins contract",
                        "summary": None,
                        "content": "Fresh Microsoft announced a large AI contract with real detail.",
                        "url": "https://example.com/1",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Old Microsoft wins contract",
                        "summary": None,
                        "content": "Old Microsoft announced a large AI contract with real detail.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-12T00:00:00",
                    },
                ],
                "MSFT Microsoft Microsoft Corp": [
                    {
                        "id": "1",
                        "article_id": 1,
                        "title": "Fresh Microsoft wins contract",
                        "summary": None,
                        "content": "Fresh Microsoft announced a large AI contract with real detail.",
                        "url": "https://example.com/1",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Old Microsoft wins contract",
                        "summary": None,
                        "content": "Old Microsoft announced a large AI contract with real detail.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-12T00:00:00",
                    },
                ],
            }

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", FreshFilterVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    now = datetime.now()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Fresh Microsoft wins contract",
            description="Large AI contract",
            content="Fresh Microsoft announced a large AI contract with real detail." * 4,
            url="https://example.com/1",
            published_at=now,
            fetched_at=now,
            index_status="ready",
        )
    )
    storage.save_article(
        NewsArticle(
            source="news",
            title="Old Microsoft wins contract",
            description="Old AI contract",
            content="Old Microsoft announced a large AI contract with real detail." * 4,
            url="https://example.com/2",
            published_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
            index_status="ready",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    exit_code = run_retrieval_eval.main(
        [
            "generate-annotations",
            "--ticker-list-path",
            str(ticker_list),
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-output-dir",
            str(annotation_dir),
            "--fresh-window-hours",
            "24",
        ]
    )

    assert exit_code == 0
    content = (annotation_dir / "MSFT.yaml").read_text(encoding="utf-8")
    assert "article_id: 1" in content
    assert "article_id: 2" not in content


def test_run_retrieval_eval_split_master_by_ticker(tmp_path):
    master_path = tmp_path / "retrieval_eval_master.yaml"
    master_path.write_text(
        (
            "articles:\n"
            "  - article_id: 20\n"
            "    title: Oracle signs deal\n"
            "    url: https://example.com/20\n"
            "    excerpt: Oracle signs a deal.\n"
            "    content_path: C:/tmp/article-20.txt\n"
            "    dedupe_key: oracle-signs-deal\n"
            "    notes: ''\n"
            "    ticker_impacts:\n"
            "      positive: [ORCL]\n"
            "      negative: []\n"
            "      neutral: []\n"
            "      unclear: []\n"
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "ticker_splits"

    exit_code = run_retrieval_eval.main(
        [
            "split-master-by-ticker",
            "--master-path",
            str(master_path),
            "--output-dir",
            str(output_dir),
            "--force",
        ]
    )

    assert exit_code == 0
    content = (output_dir / "ORCL.yaml").read_text(encoding="utf-8")
    assert "ticker: ORCL" in content
    assert "relevance: relevant" in content
    assert "impact_label: positive" in content


def test_run_retrieval_eval_evaluate_both_adds_deepseek_chain_to_report(tmp_path, monkeypatch):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - MSFT\n", encoding="utf-8")

    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        ),
    )

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", FakeVectorStore)
    _patch_rerankers(monkeypatch, include_deepseek=True)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="Microsoft announced a large AI contract.",
            url="https://example.com/1",
            published_at=datetime.now(),
            index_status="ready",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir()
    (annotation_dir / "MSFT.yaml").write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large AI contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: microsoft-wins-contract\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
            "--reranker-provider",
            "both",
        ]
    )

    assert exit_code == 0
    report_content = (report_dir / "retrieval_eval_report.md").read_text(encoding="utf-8")
    results_content = (report_dir / "retrieval_eval_results.json").read_text(encoding="utf-8")
    assert "deepseek_entity_kb_enhanced" in report_content
    assert "deepseek_entity_kb_enhanced" in results_content


def test_run_retrieval_eval_evaluate_filters_tickers_by_min_relevant(tmp_path, monkeypatch):
    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        ),
        CompanyProfile(
            ticker="AAPL",
            canonical_name="Apple",
            website="https://www.apple.com",
            ir_url="https://investor.apple.com",
            aliases=("Apple Inc",),
        ),
    )

    class FilterVectorStore(FakeVectorStore):
        def __init__(self, persist_dir, collection_name, *, db_path):
            super().__init__(persist_dir, collection_name, db_path=db_path)
            self.results_by_query = {
                "MSFT": [self.results_by_query["MSFT"][0]],
                "MSFT Microsoft Microsoft Corp": [self.results_by_query["MSFT Microsoft Microsoft Corp"][0]],
                "AAPL": [
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Apple article",
                        "summary": "Apple article summary.",
                        "content": "Apple article summary.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    }
                ],
                "AAPL Apple Apple Inc": [
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "Apple article",
                        "summary": "Apple article summary.",
                        "content": "Apple article summary.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    }
                ],
            }

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", FilterVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="Microsoft announced a large AI contract.",
            url="https://example.com/1",
            published_at=datetime.now(),
            index_status="ready",
        )
    )
    storage.save_article(
        NewsArticle(
            source="news",
            title="Apple article",
            description="Apple article summary.",
            content="Apple article summary.",
            url="https://example.com/2",
            published_at=datetime.now(),
            index_status="ready",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir()
    (annotation_dir / "MSFT.yaml").write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large AI contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: microsoft-wins-contract\n"
            "    label: relevant\n"
            "    notes: ''\n"
            "  - article_id: 3\n"
            "    title: Microsoft second article\n"
            "    url: https://example.com/3\n"
            "    excerpt: Another relevant article.\n"
            "    content_path: C:/tmp/article-3.txt\n"
            "    dedupe_key: microsoft-second-article\n"
            "    label: relevant\n"
            "    notes: ''\n"
            "  - article_id: 4\n"
            "    title: Microsoft third article\n"
            "    url: https://example.com/4\n"
            "    excerpt: Third relevant article.\n"
            "    content_path: C:/tmp/article-4.txt\n"
            "    dedupe_key: microsoft-third-article\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )
    (annotation_dir / "AAPL.yaml").write_text(
        (
            "ticker: AAPL\n"
            "annotations:\n"
            "  - article_id: 2\n"
            "    title: Apple article\n"
            "    url: https://example.com/2\n"
            "    excerpt: Apple article summary.\n"
            "    content_path: C:/tmp/article-2.txt\n"
            "    dedupe_key: apple-article\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
            "--min-relevant",
            "3",
        ]
    )

    assert exit_code == 0
    report_content = (report_dir / "retrieval_eval_report.md").read_text(encoding="utf-8")
    assert "## MSFT" in report_content
    assert "## AAPL" not in report_content


def test_run_retrieval_eval_evaluate_filters_company_only(tmp_path, monkeypatch):
    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
            asset_type="company",
        ),
        CompanyProfile(
            ticker="QQQ",
            canonical_name="Invesco QQQ Trust",
            website="https://www.invesco.com",
            ir_url="https://www.invesco.com",
            aliases=("QQQ ETF",),
            asset_type="etf",
        ),
    )

    class CompanyOnlyVectorStore(FakeVectorStore):
        def __init__(self, persist_dir, collection_name, *, db_path):
            super().__init__(persist_dir, collection_name, db_path=db_path)
            self.results_by_query = {
                "MSFT": [self.results_by_query["MSFT"][0]],
                "MSFT Microsoft Microsoft Corp": [self.results_by_query["MSFT Microsoft Microsoft Corp"][0]],
                "QQQ": [
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "QQQ article",
                        "summary": "QQQ article summary.",
                        "content": "QQQ article summary.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    }
                ],
                "QQQ Invesco QQQ Trust QQQ ETF": [
                    {
                        "id": "2",
                        "article_id": 2,
                        "title": "QQQ article",
                        "summary": "QQQ article summary.",
                        "content": "QQQ article summary.",
                        "url": "https://example.com/2",
                        "published_at": "2026-05-15T00:00:00",
                    }
                ],
            }

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", CompanyOnlyVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="Microsoft announced a large AI contract.",
            url="https://example.com/1",
            published_at=datetime.now(),
            index_status="ready",
        )
    )
    storage.save_article(
        NewsArticle(
            source="news",
            title="QQQ article",
            description="QQQ article summary.",
            content="QQQ article summary.",
            url="https://example.com/2",
            published_at=datetime.now(),
            index_status="ready",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir()
    (annotation_dir / "MSFT.yaml").write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large AI contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: microsoft-wins-contract\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )
    (annotation_dir / "QQQ.yaml").write_text(
        (
            "ticker: QQQ\n"
            "annotations:\n"
            "  - article_id: 2\n"
            "    title: QQQ article\n"
            "    url: https://example.com/2\n"
            "    excerpt: QQQ article summary.\n"
            "    content_path: C:/tmp/article-2.txt\n"
            "    dedupe_key: qqq-article\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
            "--company-only",
        ]
    )

    assert exit_code == 0
    report_content = (report_dir / "retrieval_eval_report.md").read_text(encoding="utf-8")
    assert "## MSFT" in report_content
    assert "## QQQ" not in report_content


def test_run_retrieval_eval_evaluate_filters_by_max_article_id(tmp_path, monkeypatch):
    entity_db_path = tmp_path / "entities.db"
    _create_entity_db(
        entity_db_path,
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
            asset_type="company",
        ),
    )

    class MaxIdVectorStore(FakeVectorStore):
        def __init__(self, persist_dir, collection_name, *, db_path):
            super().__init__(persist_dir, collection_name, db_path=db_path)
            self.results_by_query = {
                "MSFT": [
                    {
                        "id": "229",
                        "article_id": 229,
                        "title": "Older Microsoft article",
                        "summary": "Older Microsoft article.",
                        "content": "Older Microsoft article.",
                        "url": "https://example.com/229",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "231",
                        "article_id": 231,
                        "title": "Newer Microsoft article",
                        "summary": "Newer Microsoft article.",
                        "content": "Newer Microsoft article.",
                        "url": "https://example.com/231",
                        "published_at": "2026-05-15T00:00:00",
                    },
                ],
                "MSFT Microsoft Microsoft Corp": [
                    {
                        "id": "229",
                        "article_id": 229,
                        "title": "Older Microsoft article",
                        "summary": "Older Microsoft article.",
                        "content": "Older Microsoft article.",
                        "url": "https://example.com/229",
                        "published_at": "2026-05-15T00:00:00",
                    },
                    {
                        "id": "231",
                        "article_id": 231,
                        "title": "Newer Microsoft article",
                        "summary": "Newer Microsoft article.",
                        "content": "Newer Microsoft article.",
                        "url": "https://example.com/231",
                        "published_at": "2026-05-15T00:00:00",
                    },
                ],
            }

    monkeypatch.setattr(run_retrieval_eval, "create_corpus_vector_store", MaxIdVectorStore)
    _patch_rerankers(monkeypatch)

    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    now = datetime.now()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Older Microsoft article",
            description="Older Microsoft article.",
            content="Older Microsoft article.",
            url="https://example.com/229",
            published_at=now,
            fetched_at=now,
            index_status="ready",
        )
    )
    storage.save_article(
        NewsArticle(
            source="news",
            title="Newer Microsoft article",
            description="Newer Microsoft article.",
            content="Newer Microsoft article.",
            url="https://example.com/231",
            published_at=now,
            fetched_at=now,
            index_status="ready",
        )
    )
    storage.close()

    annotation_dir = tmp_path / "annotations"
    annotation_dir.mkdir()
    (annotation_dir / "MSFT.yaml").write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 229\n"
            "    title: Older Microsoft article\n"
            "    url: https://example.com/229\n"
            "    excerpt: Older Microsoft article.\n"
            "    content_path: C:/tmp/article-229.txt\n"
            "    dedupe_key: older-microsoft-article\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    report_dir = tmp_path / "reports"
    exit_code = run_retrieval_eval.main(
        [
            "evaluate",
            "--db-path",
            str(db_path),
            "--entity-db-path",
            str(entity_db_path),
            "--annotation-input-dir",
            str(annotation_dir),
            "--output-dir",
            str(report_dir),
            "--max-article-id",
            "230",
        ]
    )

    assert exit_code == 0
    results_content = (report_dir / "retrieval_eval_results.json").read_text(encoding="utf-8")
    assert '"article_id": 231' not in results_content
