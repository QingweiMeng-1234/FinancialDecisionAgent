from pydantic import ValidationError

from event_collector.reranking import (
    RAGRerankingAgent,
    RerankCandidate,
    rerank_candidates,
)


class FakeRerankingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def rerank_candidates(self, request):
        self.calls.append(request)
        return self.response


def make_candidates():
    return [
        RerankCandidate(
            candidate_id="1",
            title="Fed holds rates steady",
            summary="- Policymakers signaled patience.",
            snippet="The Fed left rates unchanged and emphasized incoming data.",
            original_rank=1,
        ),
        RerankCandidate(
            candidate_id="2",
            title="Oil jumps on supply disruptions",
            summary="- Crude rose after supply concerns resurfaced.",
            snippet="Supply disruptions pushed oil prices higher.",
            original_rank=2,
        ),
        RerankCandidate(
            candidate_id="3",
            title="Retail sales slow",
            summary="- Consumer spending cooled.",
            snippet="Retail sales growth slowed versus last month.",
            original_rank=3,
        ),
    ]


def test_reranking_agent_accepts_valid_reordering_and_preserves_reasons():
    client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "2", "reason": "Most directly addresses the supply-driven oil move."},
                {"candidate_id": "1", "reason": "Relevant macro backdrop for rates and risk assets."},
                {"candidate_id": "3", "reason": "Useful but less directly tied to the question."},
            ]
        }
    )

    result = RAGRerankingAgent(llm_client=client).rerank_candidates(
        "Why is oil moving?",
        make_candidates(),
    )

    assert [item.candidate_id for item in result.ranked_candidates] == ["2", "1", "3"]
    assert result.ranked_candidates[0].reason.startswith("Most directly")
    assert client.calls[0].question == "Why is oil moving?"


def test_reranking_agent_rejects_invented_candidate_ids():
    client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "1", "reason": "Relevant."},
                {"candidate_id": "2", "reason": "Relevant."},
                {"candidate_id": "9", "reason": "Invented."},
            ]
        }
    )

    try:
        RAGRerankingAgent(llm_client=client).rerank_candidates("Question", make_candidates())
        assert False, "Expected invented candidate IDs to fail validation"
    except ValueError as exc:
        assert "invented IDs" in str(exc)


def test_reranking_agent_rejects_duplicate_candidate_ids():
    client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "1", "reason": "Relevant."},
                {"candidate_id": "1", "reason": "Duplicate."},
                {"candidate_id": "3", "reason": "Relevant."},
            ]
        }
    )

    try:
        RAGRerankingAgent(llm_client=client).rerank_candidates("Question", make_candidates())
        assert False, "Expected duplicate candidate IDs to fail validation"
    except ValueError as exc:
        assert "duplicate candidate IDs" in str(exc)


def test_reranking_agent_rejects_omitted_candidate_ids():
    client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "1", "reason": "Relevant."},
                {"candidate_id": "2", "reason": "Relevant."},
            ]
        }
    )

    try:
        RAGRerankingAgent(llm_client=client).rerank_candidates("Question", make_candidates())
        assert False, "Expected omitted candidate IDs to fail validation"
    except ValueError as exc:
        assert "same number of candidate IDs" in str(exc)


def test_rerank_candidates_helper_uses_injected_agent():
    client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "3", "reason": "Best match."},
                {"candidate_id": "1", "reason": "Next best."},
                {"candidate_id": "2", "reason": "Still relevant."},
            ]
        }
    )

    result = rerank_candidates(
        "Question",
        make_candidates(),
        reranking_agent=RAGRerankingAgent(llm_client=client),
    )

    assert [item.candidate_id for item in result.ranked_candidates] == ["3", "1", "2"]
    assert all(item.reason for item in result.ranked_candidates)
