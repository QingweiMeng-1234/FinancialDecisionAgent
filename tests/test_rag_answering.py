from pydantic import ValidationError

from event_collector.rag_answering import (
    AnswerQueryRequest,
    ConfidenceLevel,
    RAGAnswerResponse,
    RAGAnsweringAgent,
    RetrievedEvidence,
    answer_query,
)
from event_collector.reranking import RAGRerankingAgent


class FakeAnsweringClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def answer_query(self, request):
        self.calls.append(request)
        return self.response


class FakeVectorStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.results


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


def test_answering_agent_returns_structured_grounded_answer():
    request = AnswerQueryRequest(
        question="What is the bullish case for oil?",
        evidence=[
            RetrievedEvidence(
                id=1,
                title="Oil rises on supply concerns",
                url="https://example.com/oil",
                summary="- Supply concerns pushed crude higher.",
                excerpt="Oil climbed after new supply disruptions were reported.",
                snippet="Oil climbed after new supply disruptions were reported.",
            )
        ],
    )
    client = FakeAnsweringClient(
        {
            "answer": "The bullish case is tightening supply and stronger pricing power [1].",
            "sources": [
                {
                    "id": 1,
                    "title": "Oil rises on supply concerns",
                    "url": "https://example.com/oil",
                    "snippet": "Oil climbed after new supply disruptions were reported.",
                }
            ],
            "confidence": "high",
            "insufficient_evidence": False,
            "supporting_points": [
                {
                    "text": "Supply disruptions pushed crude prices higher.",
                    "citations": [1],
                }
            ],
            "counter_points": [],
        }
    )

    result = RAGAnsweringAgent(llm_client=client).answer_query(request)

    assert result.answer.startswith("The bullish case")
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.sources[0].id == 1
    assert client.calls == [request]


def test_answering_agent_surfaces_conflicting_evidence():
    request = AnswerQueryRequest(
        question="Is this company outlook positive?",
        evidence=[
            RetrievedEvidence(
                id=1,
                title="Strong revenue growth",
                url="https://example.com/growth",
                summary="- Revenue exceeded expectations.",
                excerpt="Revenue grew 20% year over year.",
                snippet="Revenue grew 20% year over year.",
            ),
            RetrievedEvidence(
                id=2,
                title="Margins under pressure",
                url="https://example.com/margins",
                summary="- Margin pressure persisted.",
                excerpt="Operating margins declined due to higher costs.",
                snippet="Operating margins declined due to higher costs.",
            ),
        ],
    )
    client = FakeAnsweringClient(
        {
            "answer": "The outlook is mixed: growth is strong, but margin pressure is a real counterweight [1] [2].",
            "sources": [
                {
                    "id": 1,
                    "title": "Strong revenue growth",
                    "url": "https://example.com/growth",
                    "snippet": "Revenue grew 20% year over year.",
                },
                {
                    "id": 2,
                    "title": "Margins under pressure",
                    "url": "https://example.com/margins",
                    "snippet": "Operating margins declined due to higher costs.",
                },
            ],
            "confidence": "medium",
            "insufficient_evidence": False,
            "supporting_points": [
                {"text": "Revenue growth was strong.", "citations": [1]},
            ],
            "counter_points": [
                {"text": "Margins declined because costs rose.", "citations": [2]},
            ],
        }
    )

    result = RAGAnsweringAgent(llm_client=client).answer_query(request)

    assert len(result.supporting_points) == 1
    assert len(result.counter_points) == 1
    assert result.confidence == ConfidenceLevel.MEDIUM


def test_answering_agent_rejects_malformed_output():
    request = AnswerQueryRequest(
        question="What happened?",
        evidence=[
            RetrievedEvidence(
                id=1,
                title="Headline",
                url="https://example.com/article",
                summary=None,
                excerpt="A grounded excerpt.",
                snippet="A grounded excerpt.",
            )
        ],
    )
    client = FakeAnsweringClient(
        {
            "answer": "Something happened [1].",
            "sources": [
                {
                    "id": 1,
                    "title": "Headline",
                    "url": "https://example.com/article",
                    "snippet": "A grounded excerpt.",
                }
            ],
            "confidence": "certain",
            "insufficient_evidence": False,
            "supporting_points": [],
            "counter_points": [],
        }
    )

    try:
        RAGAnsweringAgent(llm_client=client).answer_query(request)
        assert False, "Expected malformed response to be rejected"
    except ValidationError:
        pass


def test_answering_agent_rejects_unknown_source_citation():
    request = AnswerQueryRequest(
        question="What happened?",
        evidence=[
            RetrievedEvidence(
                id=1,
                title="Headline",
                url="https://example.com/article",
                summary=None,
                excerpt="A grounded excerpt.",
                snippet="A grounded excerpt.",
            )
        ],
    )
    client = FakeAnsweringClient(
        {
            "answer": "The evidence is mixed [1].",
            "sources": [
                {
                    "id": 1,
                    "title": "Headline",
                    "url": "https://example.com/article",
                    "snippet": "A grounded excerpt.",
                }
            ],
            "confidence": "low",
            "insufficient_evidence": False,
            "supporting_points": [
                {"text": "A cited point.", "citations": [2]},
            ],
            "counter_points": [],
        }
    )

    try:
        RAGAnsweringAgent(llm_client=client).answer_query(request)
        assert False, "Expected citation validation to fail"
    except ValueError as exc:
        assert "unknown source ids" in str(exc)


def test_answer_query_reranks_top_five_then_answers_from_best_three():
    vector_store = FakeVectorStore(
        [
            {
                "title": "Article one",
                "url": "https://example.com/1",
                "summary": "- Summary one",
                "content": "A" * 900,
            },
            {
                "title": "Article two",
                "url": "https://example.com/2",
                "summary": None,
                "content": "Second article content with enough length to create an excerpt.",
            },
            {
                "title": "Article three",
                "url": "https://example.com/3",
                "summary": "- Summary three",
                "content": "Third article content.",
            },
            {
                "title": "Article four",
                "url": "https://example.com/4",
                "summary": "- Summary four",
                "content": "Fourth article content.",
            },
            {
                "title": "Article five",
                "url": "https://example.com/5",
                "summary": "- Summary five",
                "content": "Fifth article content.",
            },
        ]
    )
    reranking_client = FakeRerankingClient(
        {
            "ranked_candidates": [
                {"candidate_id": "4", "reason": "Most relevant."},
                {"candidate_id": "2", "reason": "Second most relevant."},
                {"candidate_id": "5", "reason": "Third most relevant."},
                {"candidate_id": "1", "reason": "Background context."},
                {"candidate_id": "3", "reason": "Least relevant."},
            ]
        }
    )
    client = FakeAnsweringClient(
        {
            "answer": "Here is a grounded synthesis [1] [2].",
            "sources": [
                {"id": 1, "title": "Article four", "url": "https://example.com/4", "snippet": "- Summary four"},
                {"id": 2, "title": "Article two", "url": "https://example.com/2", "snippet": "Second article content with enough length to create an excerpt."},
            ],
            "confidence": "medium",
            "insufficient_evidence": False,
            "supporting_points": [{"text": "Used article one.", "citations": [1]}],
            "counter_points": [{"text": "Used article two.", "citations": [2]}],
        }
    )

    result = answer_query(
        "What matters here?",
        vector_store,
        answering_agent=RAGAnsweringAgent(llm_client=client),
        reranking_agent=RAGRerankingAgent(llm_client=reranking_client),
    )

    rerank_request = reranking_client.calls[0]
    request = client.calls[0]
    assert vector_store.calls == [("What matters here?", 5)]
    assert [candidate.candidate_id for candidate in rerank_request.candidates] == ["1", "2", "3", "4", "5"]
    assert [evidence.id for evidence in request.evidence] == [1, 2, 3]
    assert [evidence.title for evidence in request.evidence] == ["Article four", "Article two", "Article five"]
    assert request.evidence[0].summary == "- Summary four"
    assert request.evidence[0].snippet == "- Summary four"
    assert request.evidence[1].summary is None
    assert [item.candidate_id for item in result.rerank_metadata.ranked_candidates] == ["4", "2", "5", "1", "3"]


def test_answer_query_returns_insufficient_evidence_without_results():
    result = answer_query("Any evidence?", FakeVectorStore([]))

    assert isinstance(result, RAGAnswerResponse)
    assert result.insufficient_evidence is True
    assert result.confidence == ConfidenceLevel.LOW
    assert result.sources == []
    assert result.rerank_metadata is None


def test_answer_query_reranker_failure_raises_without_fallback():
    vector_store = FakeVectorStore(
        [
            {
                "title": "Article one",
                "url": "https://example.com/1",
                "summary": "- Summary one",
                "content": "Article one content.",
            }
        ]
    )
    answer_client = FakeAnsweringClient(
        {
            "answer": "Unused.",
            "sources": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "supporting_points": [],
            "counter_points": [],
        }
    )

    try:
        answer_query(
            "What matters here?",
            vector_store,
            answering_agent=RAGAnsweringAgent(llm_client=answer_client),
            reranking_agent=RAGRerankingAgent(
                llm_client=FakeRerankingClient(error=RuntimeError("reranker unavailable"))
            ),
        )
        assert False, "Expected reranker failure to stop the query"
    except RuntimeError as exc:
        assert "reranker unavailable" in str(exc)

    assert answer_client.calls == []
