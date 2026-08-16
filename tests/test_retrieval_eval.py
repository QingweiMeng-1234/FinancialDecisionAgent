import json

from datetime import datetime, timedelta
from pathlib import Path
import pytest

from event_collector.entity_kb import CompanyProfile, SQLiteEntityStore
from event_collector.news_storage import NewsArticle, SQLiteNewsStore, compute_content_sha256
from event_collector.retrieval_eval import (
    AnnotationFile,
    AnnotationRecord,
    GenerationAnnotationBinding,
    RetrievalChainResult,
    RetrievalComparison,
    build_fresh_annotation_eligibility,
    build_annotation_file,
    evaluate_retrieval_comparisons,
    load_annotation_file,
    load_master_annotation_file,
    rebalance_annotation_directory,
    render_evaluation_report,
    run_retrieval_comparison,
    save_annotation_file,
    split_master_annotations_by_ticker,
    suggest_annotation_directory,
    write_evaluation_outputs,
    validate_annotation_file_binding,
)
from event_collector.reranking import RAGRerankingAgent
from event_collector.ticker_kb import TickerIdentity


class FakeVectorStore:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    def search(self, query, top_k=5, *, allowed_article_ids=None):
        self.calls.append((query, top_k, allowed_article_ids))
        results = list(self.results_by_query.get(query, []))[:top_k]
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


def _result(article_id, title, summary="", content="", url=None):
    return {
        "id": str(article_id),
        "article_id": article_id,
        "title": title,
        "summary": summary or None,
        "content": content or summary or title,
        "url": url or f"https://example.com/{article_id}",
        "published_at": "2026-05-15T00:00:00",
    }


def _company_kb(*profiles: CompanyProfile) -> SQLiteEntityStore:
    store = SQLiteEntityStore(db_path=":memory:")
    store.init_db()
    for profile in profiles:
        store.upsert_company_profile(profile)
    return store


def test_run_retrieval_comparison_soft_boosts_matching_results_without_dropping_others():
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(2, "Cloud spending rises", "AI spending benefits several tech firms."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(2, "Cloud spending rises", "AI spending benefits several tech firms."),
                _result(3, "Microsoft Corp boosts buybacks", "The company expanded repurchases."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())

    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )

    assert [item["article_id"] for item in comparison.ticker_only.search_results] == [1, 2]
    assert [item["article_id"] for item in comparison.entity_kb_enhanced.filtered_results] == [1, 3, 2]
    assert comparison.entity_kb_enhanced.filtered_results[0]["matches_company"] is True
    assert comparison.entity_kb_enhanced.filtered_results[-1]["kb_boost"] == 0.0
    assert [item["article_id"] for item in comparison.entity_kb_enhanced.reranked_results] == [1, 3, 2]
    company_kb.close()


def test_build_annotation_file_prioritizes_both_then_kb_then_baseline():
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(2, "Sector demand rises", "Cloud demand across the industry is improving."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(3, "Microsoft Corp boosts buybacks", "The company expanded repurchases."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())

    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )
    annotation_file = build_annotation_file(comparison, annotation_target=3)

    assert annotation_file.ticker == "MSFT"
    assert [item.article_id for item in annotation_file.annotations] == [1, 3, 2]
    assert all(item.label is None for item in annotation_file.annotations)
    company_kb.close()


def test_generation_bound_annotation_records_carry_exact_generation_and_hash():
    comparison = RetrievalComparison(
        ticker="MSFT",
        ticker_only=RetrievalChainResult(
            chain_name="ticker_only",
            query="MSFT",
            search_results=[
                {
                    **_result(7, "Microsoft contract", content="Microsoft contract detail."),
                    "generation_id": "generation-7",
                    "corpus_snapshot_id": "snapshot-7",
                    "index_config_fingerprint": "fingerprint-7",
                    "indexed_content_sha256": "a" * 64,
                }
            ],
            filtered_results=[],
            reranked_results=[],
            rerank_metadata=None,
        ),
    )
    binding = GenerationAnnotationBinding(
        generation_id="generation-7",
        corpus_snapshot_id="snapshot-7",
        index_config_fingerprint="fingerprint-7",
        article_content_hashes={7: "a" * 64},
    )

    annotation = build_annotation_file(
        comparison,
        annotation_target=1,
        generation_binding=binding,
    )

    assert annotation.generation_id == "generation-7"
    assert annotation.annotations[0].indexed_content_sha256 == "a" * 64
    assert annotation.annotations[0].content_path == "generation://generation-7/articles/7/a" + "a" * 63
    validate_annotation_file_binding(annotation, binding)


def test_generation_bound_annotation_rejects_hash_that_differs_from_pinned_eligibility():
    binding = GenerationAnnotationBinding(
        generation_id="generation-7",
        corpus_snapshot_id="snapshot-7",
        index_config_fingerprint="fingerprint-7",
        article_content_hashes={7: "a" * 64},
    )
    annotation = AnnotationFile(
        ticker="MSFT",
        generation_id="generation-7",
        corpus_snapshot_id="snapshot-7",
        index_config_fingerprint="fingerprint-7",
        annotations=[
            AnnotationRecord(
                article_id=7,
                title="Microsoft contract",
                url="https://example.com/7",
                excerpt="Microsoft contract detail.",
                content_path="generation://generation-7/articles/7/" + "b" * 64,
                dedupe_key="microsoft-contract",
                indexed_content_sha256="b" * 64,
            )
        ],
    )

    with pytest.raises(ValueError, match="content hash"):
        validate_annotation_file_binding(annotation, binding)


def test_save_and_load_annotation_file_preserves_null_label(tmp_path):
    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(
                article_id=1,
                title="Microsoft wins contract",
                url="https://example.com/1",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-1.txt",
                dedupe_key="microsoft-wins-contract",
            )
        ],
    )

    path = save_annotation_file(annotation_file, tmp_path)
    reloaded = load_annotation_file(path)

    assert reloaded.ticker == "MSFT"
    assert reloaded.annotations[0].label is None
    assert reloaded.annotations[0].notes == ""
    assert reloaded.annotations[0].content_path == "C:/tmp/article-1.txt"
    assert reloaded.annotations[0].related_tickers == ()
    assert reloaded.annotations[0].suggested_tickers == ()


def test_build_annotation_file_enriches_excerpt_from_sqlite_content(tmp_path):
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="Microsoft announced a large AI contract with clear business impact.",
            url="https://example.com/1",
            published_at=datetime.now(),
            summary="- Microsoft signed a major contract.\n- The deal has near-term impact.\n- Azure is involved.",
        )
    )

    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(article_id, "Microsoft wins contract", content="Microsoft announced a large AI contract.")
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(article_id, "Microsoft wins contract", content="Microsoft announced a large AI contract.")
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )

    annotation_file = build_annotation_file(comparison, storage=storage, annotation_target=1)

    assert annotation_file.annotations[0].excerpt.startswith("Microsoft announced a large AI contract with clear business impact.")
    content_path = Path(annotation_file.annotations[0].content_path)
    assert content_path.parent.name == str(article_id)
    assert content_path.name == (
        f"{compute_content_sha256('Microsoft announced a large AI contract with clear business impact.')}.txt"
    )
    assert "data" in content_path.parts
    assert annotation_file.annotations[0].dedupe_key == "microsoft-wins-contract"
    assert annotation_file.annotations[0].url == "https://example.com/1"
    storage.close()
    company_kb.close()


def test_build_annotation_file_skips_articles_without_original_content(tmp_path):
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft wins contract",
            description="Large AI contract",
            content="",
            url="https://example.com/1",
            published_at=datetime.now(),
        )
    )

    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(article_id, "Microsoft wins contract", content="Microsoft announced a large AI contract.")
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(article_id, "Microsoft wins contract", content="Microsoft announced a large AI contract.")
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )

    annotation_file = build_annotation_file(comparison, storage=storage, annotation_target=1)

    assert annotation_file.annotations == []
    storage.close()
    company_kb.close()


def test_build_annotation_file_never_repairs_missing_canonical_content_file(tmp_path):
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Microsoft contract",
            description="Contract",
            content="Microsoft contract content that is only present inline in the database.",
            url="https://example.com/1",
            published_at=datetime.now(),
        )
    )
    before = storage.get_article(article_id)
    missing_path = Path(before.content_path)
    missing_path.unlink()

    comparison = RetrievalComparison(
        ticker="MSFT",
        ticker_only=RetrievalChainResult(
            chain_name="ticker_only",
            query="MSFT",
            search_results=[_result(article_id, "Microsoft contract", content="contract")],
            filtered_results=[],
            reranked_results=[],
            rerank_metadata=None,
        ),
    )

    annotation = build_annotation_file(comparison, storage=storage, annotation_target=1)

    assert annotation.annotations == []
    assert not missing_path.exists()
    assert storage.get_article(article_id).content_path == before.content_path
    storage.close()


def test_evaluate_retrieval_comparisons_computes_metrics_and_outputs(tmp_path):
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(2, "Sector demand rises", "Cloud demand across the industry is improving."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", "Microsoft announced a large contract."),
                _result(3, "Microsoft Corp boosts buybacks", "The company expanded repurchases."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )

    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(
                article_id=1,
                title="Microsoft wins contract",
                url="https://example.com/1",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-1.txt",
                dedupe_key="microsoft-wins-contract",
                label="relevant",
            ),
            AnnotationRecord(
                article_id=2,
                title="Sector demand rises",
                url="https://example.com/2",
                excerpt="Cloud demand across the industry is improving.",
                content_path="C:/tmp/article-2.txt",
                dedupe_key="sector-demand-rises",
                label="not_relevant",
            ),
            AnnotationRecord(
                article_id=3,
                title="Microsoft Corp boosts buybacks",
                url="https://example.com/3",
                excerpt="The company expanded repurchases.",
                content_path="C:/tmp/article-3.txt",
                dedupe_key="microsoft-corp-boosts-buybacks",
                label="relevant",
            ),
        ],
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(annotation_file, annotation_dir)

    evaluation = evaluate_retrieval_comparisons([comparison], annotation_dir, top_k=5)
    report = render_evaluation_report(evaluation)
    markdown_path, json_path = write_evaluation_outputs(evaluation, tmp_path / "reports")

    assert evaluation.per_ticker[0].ticker_only.precision_at_k == 0.5
    assert evaluation.per_ticker[0].ticker_only.recall_at_k == 0.5
    assert evaluation.per_ticker[0].entity_kb_enhanced.precision_at_k == 1.0
    assert evaluation.per_ticker[0].entity_kb_enhanced.recall_at_k == 1.0
    assert evaluation.aggregate["ticker_only"]["micro_precision_at_k"] == 0.5
    assert evaluation.aggregate["ticker_only"]["micro_recall_at_k"] == 0.5
    assert evaluation.aggregate["entity_kb_enhanced"]["micro_precision_at_k"] == 1.0
    assert evaluation.aggregate["entity_kb_enhanced"]["micro_recall_at_k"] == 1.0
    assert evaluation.evaluation_top_k_mode == "fixed"
    assert evaluation.per_ticker[0].evaluation_k == 5
    assert "Macro Precision@K" in report
    assert "Micro Precision@K" in report
    assert "Evaluation Top-K Mode" in report
    assert "Evaluation K: 5" in report
    assert "## MSFT" in report
    assert markdown_path.exists()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["per_ticker"][0]["ticker"] == "MSFT"
    assert saved["aggregate"]["ticker_only"]["micro_precision_at_k"] == 0.5
    assert saved["aggregate"]["ticker_only"]["micro_recall_at_k"] == 0.5
    company_kb.close()


def test_load_annotation_file_rejects_missing_dedupe_key(tmp_path):
    path = tmp_path / "MSFT.yaml"
    path.write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    label: relevant\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    try:
        load_annotation_file(path)
        assert False, "Expected missing dedupe_key to fail"
    except ValueError as exc:
        assert "dedupe_key" in str(exc)


def test_load_annotation_file_accepts_retrieval_eval_by_ticker_schema(tmp_path):
    path = tmp_path / "MSFT.yaml"
    path.write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: microsoft-wins-contract\n"
            "    relevance: relevant\n"
            "    impact_label: positive\n"
            "    notes: ''\n"
            "  - article_id: 2\n"
            "    title: Macro article\n"
            "    url: https://example.com/2\n"
            "    excerpt: Macro article.\n"
            "    content_path: C:/tmp/article-2.txt\n"
            "    dedupe_key: macro-article\n"
            "    relevance: irrelevant\n"
            "    impact_label: null\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    loaded = load_annotation_file(path)

    assert loaded.ticker == "MSFT"
    assert loaded.annotations[0].label == "relevant"
    assert loaded.annotations[1].label == "not_relevant"


def test_evaluate_retrieval_comparisons_dedupes_duplicate_story_hits(tmp_path):
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Microsoft wins contract", content="Microsoft announced a large contract."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Microsoft wins contract", content="Microsoft announced a large contract."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )
    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(
                article_id=1,
                title="Microsoft wins contract",
                url="https://example.com/1",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-1.txt",
                dedupe_key="microsoft-wins-contract",
                label="relevant",
            ),
            AnnotationRecord(
                article_id=2,
                title="Microsoft wins contract",
                url="https://example.com/2",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-2.txt",
                dedupe_key="microsoft-wins-contract",
                label="relevant",
            ),
        ],
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(annotation_file, annotation_dir)

    evaluation = evaluate_retrieval_comparisons([comparison], annotation_dir, top_k=5)

    assert evaluation.per_ticker[0].ticker_only.returned_count == 1
    assert evaluation.per_ticker[0].ticker_only.relevant_count == 1
    assert evaluation.per_ticker[0].ticker_only.recall_at_k == 1.0
    company_kb.close()


def test_evaluate_retrieval_comparisons_supports_relevant_count_top_k_mode(tmp_path):
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Microsoft cloud grows", content="Azure revenue grew quickly."),
                _result(3, "Microsoft buybacks", content="Microsoft expanded repurchases."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Microsoft cloud grows", content="Azure revenue grew quickly."),
                _result(3, "Microsoft buybacks", content="Microsoft expanded repurchases."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )
    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(1, "Microsoft wins contract", "https://example.com/1", "Microsoft announced a large contract.", "C:/tmp/article-1.txt", "microsoft-wins-contract", "relevant"),
            AnnotationRecord(2, "Microsoft cloud grows", "https://example.com/2", "Azure revenue grew quickly.", "C:/tmp/article-2.txt", "microsoft-cloud-grows", "relevant"),
            AnnotationRecord(3, "Microsoft buybacks", "https://example.com/3", "Microsoft expanded repurchases.", "C:/tmp/article-3.txt", "microsoft-buybacks", "relevant"),
        ],
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(annotation_file, annotation_dir)

    evaluation = evaluate_retrieval_comparisons(
        [comparison],
        annotation_dir,
        top_k=1,
        top_k_mode="relevant_count",
    )

    assert evaluation.evaluation_top_k_mode == "relevant_count"
    assert evaluation.per_ticker[0].evaluation_k == 3
    assert evaluation.per_ticker[0].ticker_only.returned_count == 3
    assert evaluation.per_ticker[0].ticker_only.recall_at_k == 1.0
    company_kb.close()


def test_evaluate_retrieval_comparisons_supports_relevance_type_filter(tmp_path):
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Bond yields pressure growth stocks", content="Higher yields hurt large-cap tech."),
            ],
            "MSFT Microsoft Microsoft Corp": [
                _result(1, "Microsoft wins contract", content="Microsoft announced a large contract."),
                _result(2, "Bond yields pressure growth stocks", content="Higher yields hurt large-cap tech."),
            ],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )
    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(
                article_id=1,
                title="Microsoft wins contract",
                url="https://example.com/1",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-1.txt",
                dedupe_key="microsoft-wins-contract",
                label="relevant",
                notes="",
            ),
            AnnotationRecord(
                article_id=2,
                title="Bond yields pressure growth stocks",
                url="https://example.com/2",
                excerpt="Higher yields hurt large-cap tech.",
                content_path="C:/tmp/article-2.txt",
                dedupe_key="bond-yields-pressure-growth-stocks",
                label="relevant",
                notes="",
            ),
        ],
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(annotation_file, annotation_dir)

    path = annotation_dir / "MSFT.yaml"
    path.write_text(
        (
            "ticker: MSFT\n"
            "annotations:\n"
            "  - article_id: 1\n"
            "    title: Microsoft wins contract\n"
            "    url: https://example.com/1\n"
            "    excerpt: Microsoft announced a large contract.\n"
            "    content_path: C:/tmp/article-1.txt\n"
            "    dedupe_key: microsoft-wins-contract\n"
            "    relevance: relevant\n"
            "    impact_label: positive\n"
            "    relevance_type: direct\n"
            "    notes: ''\n"
            "  - article_id: 2\n"
            "    title: Bond yields pressure growth stocks\n"
            "    url: https://example.com/2\n"
            "    excerpt: Higher yields hurt large-cap tech.\n"
            "    content_path: C:/tmp/article-2.txt\n"
            "    dedupe_key: bond-yields-pressure-growth-stocks\n"
            "    relevance: relevant\n"
            "    impact_label: negative\n"
            "    relevance_type: indirect\n"
            "    notes: ''\n"
        ),
        encoding="utf-8",
    )

    direct_evaluation = evaluate_retrieval_comparisons(
        [comparison],
        annotation_dir,
        top_k=2,
        relevance_type_filter="direct",
    )
    indirect_evaluation = evaluate_retrieval_comparisons(
        [comparison],
        annotation_dir,
        top_k=2,
        relevance_type_filter="indirect",
    )

    assert direct_evaluation.relevance_type_filter == "direct"
    assert direct_evaluation.per_ticker[0].total_relevant == 1
    assert direct_evaluation.per_ticker[0].ticker_only.relevant_count == 1
    assert direct_evaluation.per_ticker[0].ticker_only.false_positive_count == 1
    assert indirect_evaluation.relevance_type_filter == "indirect"
    assert indirect_evaluation.per_ticker[0].total_relevant == 1
    assert indirect_evaluation.per_ticker[0].ticker_only.relevant_count == 1
    assert indirect_evaluation.per_ticker[0].ticker_only.false_positive_count == 1
    company_kb.close()


def test_evaluate_retrieval_comparisons_rejects_inconsistent_dedupe_labels(tmp_path):
    company_kb = _company_kb(
        CompanyProfile(
            ticker="MSFT",
            canonical_name="Microsoft",
            website="https://www.microsoft.com",
            ir_url="https://www.microsoft.com/en-us/Investor",
            aliases=("Microsoft Corp",),
        )
    )
    vector_store = FakeVectorStore(
        {
            "MSFT": [_result(1, "Microsoft wins contract", content="Microsoft announced a large contract.")],
            "MSFT Microsoft Microsoft Corp": [_result(1, "Microsoft wins contract", content="Microsoft announced a large contract.")],
        }
    )
    reranker = RAGRerankingAgent(PassThroughRerankingClient())
    comparison = run_retrieval_comparison(
        "MSFT",
        vector_store,
        company_kb=company_kb,
        retrieval_top_k=5,
        reranking_agent=reranker,
    )
    annotation_file = AnnotationFile(
        ticker="MSFT",
        annotations=[
            AnnotationRecord(
                article_id=1,
                title="Microsoft wins contract",
                url="https://example.com/1",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-1.txt",
                dedupe_key="microsoft-wins-contract",
                label="relevant",
            ),
            AnnotationRecord(
                article_id=2,
                title="Microsoft wins contract duplicate",
                url="https://example.com/2",
                excerpt="Microsoft announced a large contract.",
                content_path="C:/tmp/article-2.txt",
                dedupe_key="microsoft-wins-contract",
                label="not_relevant",
            ),
        ],
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(annotation_file, annotation_dir)

    try:
        evaluate_retrieval_comparisons([comparison], annotation_dir, top_k=5)
        assert False, "Expected inconsistent dedupe labels to fail"
    except ValueError as exc:
        assert "inconsistent labels" in str(exc)
    company_kb.close()


def test_build_fresh_annotation_eligibility_only_keeps_recent_clean_news(tmp_path):
    db_path = tmp_path / "news.db"
    storage = SQLiteNewsStore(db_path=str(db_path))
    storage.init_db()
    now = datetime.now()

    fresh_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Fresh clean article",
            description="Clean recent article",
            content="Fresh clean article content with enough detail for annotation." * 5,
            url="https://example.com/fresh",
            published_at=now,
            fetched_at=now,
            index_status="ready",
        )
    )
    stale_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Stale article",
            description="Older article",
            content="Older article content with enough detail for annotation." * 5,
            url="https://example.com/stale",
            published_at=now,
            fetched_at=now - timedelta(days=2),
            index_status="ready",
        )
    )
    manual_id = storage.save_article(
        NewsArticle(
            source="manual",
            title="Manual article",
            description="Manual note",
            content="Manual article content with enough detail for annotation." * 5,
            url="internal://manual/1",
            published_at=now,
            fetched_at=now,
            index_status="ready",
        )
    )
    failed_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Failed article",
            description="Failed note",
            content="Failed article content with enough detail for annotation." * 5,
            url="https://example.com/failed",
            published_at=now,
            fetched_at=now,
            content_status="failed",
            index_status="failed",
        )
    )

    eligibility = build_fresh_annotation_eligibility(storage, fresh_window_hours=24)

    assert fresh_id in eligibility.allowed_article_ids
    assert stale_id not in eligibility.allowed_article_ids
    assert manual_id not in eligibility.allowed_article_ids
    assert failed_id not in eligibility.allowed_article_ids
    storage.close()


def test_rebalance_annotation_directory_copies_related_articles_to_other_tickers(tmp_path):
    article_path = tmp_path / "article-1.txt"
    article_path.write_text(
        "Microsoft announced a cloud expansion while Apple prepared a device refresh.",
        encoding="utf-8",
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(
        AnnotationFile(
            ticker="AAPL",
            annotations=[
                AnnotationRecord(
                    article_id=1,
                    title="Microsoft cloud expansion touches Apple supply plans",
                    url="https://example.com/1",
                    excerpt="Microsoft announced a cloud expansion while Apple prepared a device refresh.",
                    content_path=str(article_path),
                    dedupe_key="microsoft-cloud-expansion-touches-apple-supply-plans",
                    related_tickers=("MSFT",),
                )
            ],
        ),
        annotation_dir,
    )
    save_annotation_file(
        AnnotationFile(
            ticker="MSFT",
            annotations=[],
        ),
        annotation_dir,
    )

    counts = rebalance_annotation_directory(
        annotation_dir,
        identities={
            "AAPL": TickerIdentity(ticker="AAPL", company_name="Apple", aliases=("Apple Inc",)),
            "MSFT": TickerIdentity(ticker="MSFT", company_name="Microsoft", aliases=("Microsoft Corp",)),
        },
    )

    reloaded_aapl = load_annotation_file(annotation_dir / "AAPL.yaml")
    reloaded_msft = load_annotation_file(annotation_dir / "MSFT.yaml")

    assert counts["AAPL"] == 1
    assert counts["MSFT"] == 1
    assert reloaded_aapl.annotations[0].related_tickers == ("MSFT",)
    assert reloaded_msft.annotations[0].article_id == 1
    assert reloaded_msft.annotations[0].label is None
    assert reloaded_msft.annotations[0].related_tickers == ("AAPL",)


def test_suggest_annotation_directory_sets_suggested_tickers_and_promotes_single_match(tmp_path):
    article_path = tmp_path / "article-2.txt"
    article_path.write_text(
        "Oracle signed a large cloud contract unrelated to Apple.",
        encoding="utf-8",
    )
    annotation_dir = tmp_path / "annotations"
    save_annotation_file(
        AnnotationFile(
            ticker="AAPL",
            annotations=[
                AnnotationRecord(
                    article_id=2,
                    title="Oracle signs large cloud contract",
                    url="https://example.com/2",
                    excerpt="Oracle signed a large cloud contract.",
                    content_path=str(article_path),
                    dedupe_key="oracle-signs-large-cloud-contract",
                )
            ],
        ),
        annotation_dir,
    )

    counts = suggest_annotation_directory(
        annotation_dir,
        identities={
            "AAPL": TickerIdentity(ticker="AAPL", company_name="Apple", aliases=("Apple Inc",)),
            "ORCL": TickerIdentity(ticker="ORCL", company_name="Oracle", aliases=("Oracle Corp",)),
        },
    )
    reloaded = load_annotation_file(annotation_dir / "AAPL.yaml")

    assert counts["AAPL"] == 1
    assert reloaded.annotations[0].suggested_tickers == ("ORCL",)
    assert reloaded.annotations[0].related_tickers == ("ORCL",)


def test_load_master_annotation_file_and_split_by_ticker(tmp_path):
    master_path = tmp_path / "retrieval_eval_master.yaml"
    master_path.write_text(
        (
            "articles:\n"
            "  - article_id: 10\n"
            "    title: Microsoft cloud demand rises\n"
            "    url: https://example.com/10\n"
            "    excerpt: Microsoft cloud demand rises on AI demand.\n"
            "    content_path: C:/tmp/article-10.txt\n"
            "    dedupe_key: microsoft-cloud-demand-rises\n"
            "    notes: direct company mention\n"
            "    ticker_impacts:\n"
            "      positive: [MSFT]\n"
            "      negative: []\n"
            "      neutral: [QQQ]\n"
            "      unclear: []\n"
            "    ticker_relevance_types:\n"
            "      direct: [MSFT]\n"
            "      indirect: [QQQ]\n"
            "  - article_id: 11\n"
            "    title: Bond yields pressure growth stocks\n"
            "    url: https://example.com/11\n"
            "    excerpt: Bond yields pressure growth stocks and indices.\n"
            "    content_path: C:/tmp/article-11.txt\n"
            "    dedupe_key: bond-yields-pressure-growth-stocks\n"
            "    notes: macro signal\n"
            "    ticker_impacts:\n"
            "      positive: []\n"
            "      negative: [MSFT, QQQ]\n"
            "      neutral: []\n"
            "      unclear: []\n"
            "    ticker_relevance_types:\n"
            "      direct: []\n"
            "      indirect: [MSFT, QQQ]\n"
        ),
        encoding="utf-8",
    )

    master_file = load_master_annotation_file(master_path)
    assert len(master_file.articles) == 2
    assert master_file.articles[0].ticker_impacts["positive"] == ("MSFT",)
    assert master_file.articles[0].ticker_relevance_types["direct"] == ("MSFT",)
    assert master_file.articles[0].ticker_relevance_types["indirect"] == ("QQQ",)

    output_dir = tmp_path / "ticker_splits"
    counts = split_master_annotations_by_ticker(master_path, output_dir, force=True)

    assert counts == {"MSFT": 2, "QQQ": 2}
    msft_yaml = (output_dir / "MSFT.yaml").read_text(encoding="utf-8")
    qqq_yaml = (output_dir / "QQQ.yaml").read_text(encoding="utf-8")
    assert msft_yaml.count("relevance: relevant") == 2
    assert qqq_yaml.count("relevance: relevant") == 2
    assert "impact_label: positive" in msft_yaml
    assert "impact_label: negative" in msft_yaml
    assert "relevance_type: direct" in msft_yaml
    assert "relevance_type: indirect" in msft_yaml
    assert "impact_label: neutral" in qqq_yaml
    assert "impact_label: negative" in qqq_yaml
    assert "relevance_type: indirect" in qqq_yaml

    master_path.write_text(
        (
            "articles:\n"
            "  - article_id: 10\n"
            "    title: Microsoft cloud demand rises\n"
            "    url: https://example.com/10\n"
            "    excerpt: Microsoft cloud demand rises on AI demand.\n"
            "    content_path: C:/tmp/article-10.txt\n"
            "    dedupe_key: microsoft-cloud-demand-rises\n"
            "    notes: direct company mention\n"
            "    ticker_impacts:\n"
            "      positive: [MSFT]\n"
            "      negative: []\n"
            "      neutral: []\n"
            "      unclear: []\n"
            "    ticker_relevance_types:\n"
            "      direct: [MSFT]\n"
            "      indirect: []\n"
            "  - article_id: 11\n"
            "    title: Bond yields pressure growth stocks\n"
            "    url: https://example.com/11\n"
            "    excerpt: Bond yields pressure growth stocks and indices.\n"
            "    content_path: C:/tmp/article-11.txt\n"
            "    dedupe_key: bond-yields-pressure-growth-stocks\n"
            "    notes: macro signal\n"
            "    ticker_impacts:\n"
            "      positive: []\n"
            "      negative: [QQQ]\n"
            "      neutral: []\n"
            "      unclear: []\n"
            "    ticker_relevance_types:\n"
            "      direct: []\n"
            "      indirect: [QQQ]\n"
        ),
        encoding="utf-8",
    )
    counts = split_master_annotations_by_ticker(master_path, output_dir, force=True)
    msft_yaml = (output_dir / "MSFT.yaml").read_text(encoding="utf-8")
    assert counts == {"MSFT": 2, "QQQ": 2}
    assert "relevance: irrelevant" in msft_yaml
    assert "impact_label: null" in msft_yaml
    assert "relevance_type: null" in msft_yaml
