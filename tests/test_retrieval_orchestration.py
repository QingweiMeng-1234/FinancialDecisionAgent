import os
import tempfile

from event_collector.entity_kb import (
    CompanyProfile,
    SQLiteEntityStore,
    build_expanded_query,
    build_indirect_expanded_query,
)
from event_collector.retrieval_orchestration import (
    RetrievedArticleEvidence,
    resolve_article_id,
    retrieve_evidence_bundle,
)
from event_collector.reranking import RAGRerankingAgent


class FakeVectorStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.results


class FakeRerankingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def rerank_candidates(self, request):
        self.calls.append(request)
        return self.response


def test_retrieve_evidence_bundle_reranks_and_shapes_shared_evidence():
    vector_store = FakeVectorStore(
        [
            {
                "id": "10",
                "article_id": 10,
                "title": "Microsoft cloud strength",
                "url": "https://example.com/msft",
                "summary": "- Azure demand remained strong.",
                "content": "Microsoft reported strong enterprise demand and cloud momentum.",
                "published_at": "2026-05-10T10:00:00",
            },
            {
                "id": "11",
                "article_id": 11,
                "title": "Macro rates pressure",
                "url": "https://example.com/macro",
                "summary": None,
                "content": "The Fed signaled rates may stay higher for longer.",
                "published_at": "2026-05-10T11:00:00",
            },
        ]
    )
    reranking_agent = RAGRerankingAgent(
        llm_client=FakeRerankingClient(
            {
                "ranked_candidates": [
                    {"candidate_id": "2", "reason": "Closer match."},
                    {"candidate_id": "1", "reason": "Useful company context."},
                ]
            }
        )
    )

    bundle = retrieve_evidence_bundle(
        "MSFT",
        vector_store,
        top_k=2,
        retrieval_top_k=2,
        reranking_agent=reranking_agent,
    )

    assert vector_store.calls == [("MSFT", 2)]
    assert [item.candidate_id for item in bundle.rerank_metadata.ranked_candidates] == ["2", "1"]
    assert [item.article_id for item in bundle.evidence] == [11, 10]
    assert [item.rerank_position for item in bundle.evidence] == [1, 2]
    assert bundle.evidence[0].summary is None
    assert bundle.evidence[0].snippet == "The Fed signaled rates may stay higher for longer."
    assert bundle.evidence[1].snippet == "Microsoft reported strong enterprise demand and cloud momentum."
    assert isinstance(bundle.evidence[0], RetrievedArticleEvidence)


def test_retrieve_evidence_bundle_returns_empty_when_search_finds_nothing():
    bundle = retrieve_evidence_bundle(
        "MSFT",
        FakeVectorStore([]),
        top_k=3,
        retrieval_top_k=5,
    )

    assert bundle.evidence == []
    assert bundle.rerank_metadata is None
    assert bundle.reranked_results == []


def test_retrieve_evidence_bundle_filters_truncated_preview_articles():
    vector_store = FakeVectorStore(
        [
            {
                "id": "103",
                "article_id": 103,
                "title": "Short teaser",
                "url": "https://example.com/103",
                "summary": None,
                "content": "Kaspersky warns that passwords hashed with MD5 remain among the worst choices for storing passwords. In...",
                "published_at": "2026-05-10T10:00:00",
            },
            {
                "id": "11",
                "article_id": 11,
                "title": "Macro rates pressure",
                "url": "https://example.com/macro",
                "summary": None,
                "content": "The Fed signaled rates may stay higher for longer.",
                "published_at": "2026-05-10T11:00:00",
            },
        ]
    )
    reranking_agent = RAGRerankingAgent(
        llm_client=FakeRerankingClient(
            {
                "ranked_candidates": [
                    {"candidate_id": "1", "reason": "Only remaining valid article."},
                ]
            }
        )
    )

    bundle = retrieve_evidence_bundle(
        "MSFT",
        vector_store,
        top_k=2,
        retrieval_top_k=2,
        reranking_agent=reranking_agent,
    )

    assert [item.article_id for item in bundle.evidence] == [11]


def test_resolve_article_id_keeps_legacy_suffix_support():
    article_id = resolve_article_id(
        {
            "id": "internal://news/f106d0be-7da9-4d56-baa6-fccac6f01089_6",
            "url": "internal://news/f106d0be-7da9-4d56-baa6-fccac6f01089",
        }
    )

    assert article_id == 6


def test_retrieve_evidence_bundle_uses_company_kb_for_query_expansion_and_attribution():
    with tempfile.TemporaryDirectory() as tmpdir:
        company_kb = SQLiteEntityStore(db_path=os.path.join(tmpdir, "entities.db"))
        company_kb.init_db()
        company_kb.upsert_company_profile(
            CompanyProfile(
                ticker="MSFT",
                canonical_name="Microsoft",
                website="https://www.microsoft.com",
                ir_url="https://www.microsoft.com/en-us/Investor",
                ceo_name="Satya Nadella",
                products=("Azure",),
            )
        )
        company = company_kb.load_company_by_ticker("MSFT")
        vector_store = FakeVectorStore(
            [
                {
                    "id": "10",
                    "article_id": 10,
                    "title": "Azure backlog stayed strong",
                    "url": "https://example.com/azure",
                    "summary": None,
                    "content": "Azure demand accelerated among enterprise customers.",
                    "published_at": "2026-05-10T10:00:00",
                },
                {
                    "id": "11",
                    "article_id": 11,
                    "title": "Macro rates pressure",
                    "url": "https://example.com/macro",
                    "summary": None,
                    "content": "The Fed signaled rates may stay higher for longer.",
                    "published_at": "2026-05-10T11:00:00",
                },
            ]
        )

        bundle = retrieve_evidence_bundle(
            "MSFT",
            vector_store,
            top_k=2,
            retrieval_top_k=2,
            company_kb=company_kb,
            reranking_agent=RAGRerankingAgent(
                llm_client=FakeRerankingClient(
                    {
                        "ranked_candidates": [
                            {"candidate_id": "1", "reason": "Attributed product match."},
                            {"candidate_id": "2", "reason": "Still useful background."},
                        ]
                    }
                )
            ),
        )

        assert company is not None
        assert vector_store.calls == [(build_expanded_query(company), 2)]
        assert [item.article_id for item in bundle.evidence] == [10, 11]
        assert bundle.evidence[0].company_id == company.company_id
        assert bundle.evidence[0].attribution_match_types == ("product",)
        assert bundle.evidence[0].attribution_matched_aliases == ("Azure",)
        assert bundle.evidence[0].kb_boost > bundle.evidence[1].kb_boost
        assert bundle.evidence[1].kb_boost == 0.0
        company_kb.close()


def test_retrieve_evidence_bundle_supports_indirect_intent_without_company_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        company_kb = SQLiteEntityStore(db_path=os.path.join(tmpdir, "entities.db"))
        company_kb.init_db()
        company_kb.upsert_company_profile(
            CompanyProfile(
                ticker="MSFT",
                canonical_name="Microsoft",
                website="https://www.microsoft.com",
                ir_url="https://www.microsoft.com/en-us/Investor",
                ceo_name="Satya Nadella",
                products=("Azure",),
                business_lines=("cloud infrastructure",),
                themes=("enterprise software",),
            )
        )
        company = company_kb.load_company_by_ticker("MSFT")
        vector_store = FakeVectorStore(
            [
                {
                    "id": "10",
                    "article_id": 10,
                    "title": "Cloud infrastructure spending rises",
                    "url": "https://example.com/cloud",
                    "summary": None,
                    "content": "Enterprise software demand is lifting cloud infrastructure budgets.",
                    "published_at": "2026-05-10T10:00:00",
                },
            ]
        )

        bundle = retrieve_evidence_bundle(
            "MSFT",
            vector_store,
            top_k=1,
            retrieval_top_k=1,
            company_kb=company_kb,
            retrieval_intent="indirect",
            reranking_agent=RAGRerankingAgent(
                llm_client=FakeRerankingClient(
                    {"ranked_candidates": [{"candidate_id": "1", "reason": "Indirect theme match."}]}
                )
            ),
        )

        assert company is not None
        assert vector_store.calls == [(build_indirect_expanded_query(company), 1)]
        assert bundle.evidence[0].retrieval_intent == "indirect"
        assert bundle.evidence[0].company_id is None
        assert bundle.evidence[0].matches_business_line is True
        assert bundle.evidence[0].matches_theme is True
        assert bundle.evidence[0].indirect_kb_boost > 0.0
        company_kb.close()
