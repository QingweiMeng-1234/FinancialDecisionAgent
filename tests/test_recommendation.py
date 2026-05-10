from datetime import datetime
from types import SimpleNamespace
import tempfile

import pytest
from pydantic import ValidationError

from event_collector.event_structuring import (
    EventDirection,
    EventImportance,
    EventType,
    StructuredEvent,
    TimeHorizon,
)
from event_collector.rag_answering import ConfidenceLevel
from event_collector.recommendation import (
    AggregatedSignal,
    RecommendationAgent,
    RecommendationDecision,
    RecommendationDecisionRequest,
    RecommendationResponse,
    build_report_filename,
    build_recommendation_evidence,
    normalize_target,
    recommend_target,
    render_recommendation_report,
    sanitize_target_for_filename,
    write_recommendation_report,
)
from event_collector.reranking import RAGRerankingAgent


class FakeVectorStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.results


class FakeStorage:
    def __init__(self, event_map):
        self.event_map = event_map
        self.calls = []

    def list_structured_events_for_article(self, article_id):
        self.calls.append(article_id)
        return self.event_map.get(article_id, [])


class FakeRecommendationClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def recommend(self, request):
        self.calls.append(request)
        return self.response


class FakeRerankingClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def rerank_candidates(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def make_event(
    article_id,
    event_type,
    direction,
    importance,
    time_horizon,
    affected_asset,
    reasoning,
    excerpt,
):
    return StructuredEvent(
        event_id=f"event-{article_id}-{event_type.value}",
        article_id=article_id,
        event_type=event_type,
        direction=direction,
        importance=importance,
        time_horizon=time_horizon,
        affected_asset=affected_asset,
        reasoning=reasoning,
        evidence_excerpt=excerpt,
    )


def make_search_results():
    return [
        {
            "id": "10",
            "title": "Microsoft cloud strength",
            "url": "https://example.com/msft",
            "summary": "- Azure demand remained strong.",
            "content": "Microsoft reported strong enterprise demand and cloud momentum.",
            "published_at": "2026-05-10T10:00:00",
        },
        {
            "id": "11",
            "title": "Macro rates pressure",
            "url": "https://example.com/macro",
            "summary": "- Higher rates could pressure valuations.",
            "content": "The Fed signaled rates may stay higher for longer.",
            "published_at": "2026-05-10T11:00:00",
        },
    ]


def test_recommendation_agent_accepts_valid_structured_output():
    aggregation = AggregatedSignal.model_validate(
        {
            "target": "MSFT",
            "target_specific_event_count": 1,
            "macro_score": -2,
            "company_score": 3,
            "sector_score": 0,
            "market_score": 0,
            "net_score": 1,
            "dominant_driver": "Company signals are the dominant positive driver",
            "summary": "Signals are mildly positive overall.",
            "conflicts": ["Short-term headwinds conflict with more durable long-term positives."],
            "target_events": [],
        }
    )
    request = RecommendationDecisionRequest(target="msft", aggregation=aggregation, evidence=[])
    client = FakeRecommendationClient(
        {
            "decision": "HOLD",
            "confidence": "medium",
            "time_horizon": "Long-term",
            "reasoning": "Company signals are positive, but macro pressure keeps the discipline on hold.",
            "key_risks": ["Rates could stay high for longer."],
            "insufficient_evidence": False,
            "sources": [],
        }
    )

    result = RecommendationAgent(llm_client=client).recommend(request)

    assert result.decision == RecommendationDecision.HOLD
    assert result.confidence == ConfidenceLevel.MEDIUM
    assert client.calls == [request]


def test_recommendation_agent_rejects_invalid_decision():
    aggregation = AggregatedSignal.model_validate(
        {
            "target": "MSFT",
            "target_specific_event_count": 1,
            "macro_score": 0,
            "company_score": 0,
            "sector_score": 0,
            "market_score": 0,
            "net_score": 0,
            "dominant_driver": "No dominant driver",
            "summary": "Mixed.",
            "conflicts": [],
            "target_events": [],
        }
    )
    request = RecommendationDecisionRequest(target="msft", aggregation=aggregation, evidence=[])
    client = FakeRecommendationClient(
        {
            "decision": "WATCH",
            "confidence": "low",
            "time_horizon": "Long-term",
            "reasoning": "Invalid decision output.",
            "key_risks": [],
            "insufficient_evidence": True,
            "sources": [],
        }
    )

    with pytest.raises(ValidationError):
        RecommendationAgent(llm_client=client).recommend(request)


def test_recommend_target_aggregates_scores_and_calls_decision_agent():
    vector_store = FakeVectorStore(make_search_results())
    storage = FakeStorage(
        {
            10: [
                make_event(
                    10,
                    EventType.COMPANY,
                    EventDirection.POSITIVE,
                    EventImportance.HIGH,
                    TimeHorizon.LONG_TERM,
                    "MSFT",
                    "Azure demand remained strong.",
                    "Microsoft reported strong enterprise demand.",
                )
            ],
            11: [
                make_event(
                    11,
                    EventType.MACRO,
                    EventDirection.NEGATIVE,
                    EventImportance.MEDIUM,
                    TimeHorizon.SHORT_TERM,
                    "General Market",
                    "Higher rates pressure valuations.",
                    "Rates may stay higher for longer.",
                )
            ],
        }
    )
    reranker = RAGRerankingAgent(
        llm_client=FakeRerankingClient(
            {
                "ranked_candidates": [
                    {"candidate_id": "1", "reason": "Directly about Microsoft."},
                    {"candidate_id": "2", "reason": "Useful macro context."},
                ]
            }
        )
    )
    client = FakeRecommendationClient(
        {
            "decision": "HOLD",
            "confidence": "medium",
            "time_horizon": "Long-term",
            "reasoning": "Microsoft-specific strength is offset by macro valuation pressure.",
            "key_risks": ["Rates may remain elevated."],
            "insufficient_evidence": False,
            "sources": [
                {
                    "id": 1,
                    "title": "Microsoft cloud strength",
                    "url": "https://example.com/msft",
                    "snippet": "- Azure demand remained strong.",
                },
                {
                    "id": 2,
                    "title": "Macro rates pressure",
                    "url": "https://example.com/macro",
                    "snippet": "- Higher rates could pressure valuations.",
                },
            ],
        }
    )

    result = recommend_target(
        "MSFT",
        vector_store,
        storage,
        recommendation_agent=RecommendationAgent(llm_client=client),
        reranking_agent=reranker,
    )

    assert vector_store.calls == [("MSFT", 5)]
    assert result.decision == RecommendationDecision.HOLD
    assert result.aggregation.company_score == 3
    assert result.aggregation.macro_score == -2
    assert result.aggregation.net_score == 1
    assert result.aggregation.target_specific_event_count == 1
    assert result.aggregation.conflicts
    assert [event.source_id for event in result.aggregation.target_events] == [1, 2]
    assert client.calls[0].aggregation.net_score == 1
    assert len(client.calls[0].evidence) == 2


def test_recommend_target_returns_hold_when_only_general_market_context_exists():
    vector_store = FakeVectorStore(make_search_results())
    storage = FakeStorage(
        {
            10: [],
            11: [
                make_event(
                    11,
                    EventType.MACRO,
                    EventDirection.NEGATIVE,
                    EventImportance.MEDIUM,
                    TimeHorizon.SHORT_TERM,
                    "General Market",
                    "Higher rates pressure valuations.",
                    "Rates may stay higher for longer.",
                )
            ],
        }
    )
    reranker = RAGRerankingAgent(
        llm_client=FakeRerankingClient(
            {
                "ranked_candidates": [
                    {"candidate_id": "1", "reason": "Closest match."},
                    {"candidate_id": "2", "reason": "Macro context."},
                ]
            }
        )
    )

    result = recommend_target("MSFT", vector_store, storage, reranking_agent=reranker)

    assert result.decision == RecommendationDecision.HOLD
    assert result.insufficient_evidence is True
    assert result.confidence == ConfidenceLevel.LOW
    assert result.aggregation is not None
    assert result.aggregation.target_specific_event_count == 0


def test_recommend_target_supports_general_market_requests():
    vector_store = FakeVectorStore(make_search_results())
    storage = FakeStorage(
        {
            10: [],
            11: [
                make_event(
                    11,
                    EventType.MARKET,
                    EventDirection.POSITIVE,
                    EventImportance.MEDIUM,
                    TimeHorizon.SHORT_TERM,
                    "General Market",
                    "Broad market breadth improved.",
                    "Breadth improved across major indices.",
                )
            ],
        }
    )
    reranker = RAGRerankingAgent(
        llm_client=FakeRerankingClient(
            {
                "ranked_candidates": [
                    {"candidate_id": "2", "reason": "Direct market evidence."},
                    {"candidate_id": "1", "reason": "Background."},
                ]
            }
        )
    )
    client = FakeRecommendationClient(
        {
            "decision": "BUY",
            "confidence": "low",
            "time_horizon": "Short-term",
            "reasoning": "This is only a news-grounded market read, but breadth improved.",
            "key_risks": ["The signal is short-term and may reverse quickly."],
            "insufficient_evidence": False,
            "sources": [
                {
                    "id": 1,
                    "title": "Macro rates pressure",
                    "url": "https://example.com/macro",
                    "snippet": "- Higher rates could pressure valuations.",
                }
            ],
        }
    )

    result = recommend_target(
        "general market",
        vector_store,
        storage,
        recommendation_agent=RecommendationAgent(llm_client=client),
        reranking_agent=reranker,
    )

    assert result.aggregation.market_score == 2
    assert result.aggregation.target_specific_event_count == 1


def test_recommend_target_returns_insufficient_evidence_when_no_results():
    result = recommend_target("MSFT", FakeVectorStore([]), FakeStorage({}))

    assert result.decision == RecommendationDecision.HOLD
    assert result.insufficient_evidence is True
    assert result.sources == []
    assert result.aggregation is None


def test_build_recommendation_evidence_preserves_article_ids():
    search_results = make_search_results()
    retrieved = [
        SimpleNamespace(
            id=1,
            title="Microsoft cloud strength",
            url="https://example.com/msft",
            summary="- Azure demand remained strong.",
            excerpt="Microsoft reported strong enterprise demand.",
            snippet="- Azure demand remained strong.",
        ),
        SimpleNamespace(
            id=2,
            title="Macro rates pressure",
            url="https://example.com/macro",
            summary="- Higher rates could pressure valuations.",
            excerpt="Rates may stay higher for longer.",
            snippet="- Higher rates could pressure valuations.",
        ),
    ]

    evidence = build_recommendation_evidence(search_results, retrieved)

    assert [item.article_id for item in evidence] == [10, 11]
    assert evidence[0].published_at == "2026-05-10T10:00:00"


def test_filename_helpers_normalize_target_tokens():
    stamp = datetime(2026, 5, 10, 20, 30, 15)

    assert build_report_filename("AAPL", stamp) == "2026-05-10_203015_AAPL.md"
    assert sanitize_target_for_filename("general market") == "general-market"
    assert normalize_target("General Market") == "general-market"


def test_render_and_write_recommendation_report():
    response = RecommendationResponse.model_validate(
        {
            "decision": "HOLD",
            "confidence": "low",
            "time_horizon": "Long-term",
            "reasoning": "Evidence is too thin for a stronger call.",
            "key_risks": ["Coverage is mostly macro."],
            "insufficient_evidence": True,
            "sources": [
                {
                    "id": 1,
                    "title": "Macro rates pressure",
                    "url": "https://example.com/macro",
                    "snippet": "Rates may stay higher for longer.",
                }
            ],
            "aggregation": {
                "target": "MSFT",
                "target_specific_event_count": 0,
                "macro_score": -2,
                "company_score": 0,
                "sector_score": 0,
                "market_score": 0,
                "net_score": -2,
                "dominant_driver": "Macro signals are the dominant negative driver",
                "summary": "Signals are negative overall.",
                "conflicts": [],
                "target_events": [],
            },
        }
    )

    rendered = render_recommendation_report("MSFT", response, generated_at=datetime(2026, 5, 10, 20, 30, 15))
    with tempfile.TemporaryDirectory() as tmpdir:
        report_path = write_recommendation_report(
            "MSFT",
            response,
            output_dir=tmpdir,
            generated_at=datetime(2026, 5, 10, 20, 30, 15),
        )
        saved_text = open(report_path, encoding="utf-8").read()

        assert report_path.endswith("2026-05-10_203015_MSFT.md")
        assert "Evidence is too thin for a stronger call." in saved_text

    assert "# Recommendation Report: MSFT" in rendered
    assert "## Key Risks" in rendered
    assert "Signals are negative overall." in rendered
