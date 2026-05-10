from types import SimpleNamespace

import query_news
from event_collector.rag_answering import RAGAnswerResponse


def make_result():
    return RAGAnswerResponse.model_validate(
        {
            "answer": "The evidence is mixed, with support for both sides [1] [2].",
            "sources": [
                {
                    "id": 1,
                    "title": "Positive article",
                    "url": "https://example.com/positive",
                    "snippet": "Positive evidence snippet.",
                },
                {
                    "id": 2,
                    "title": "Negative article",
                    "url": "https://example.com/negative",
                    "snippet": "Negative evidence snippet.",
                },
            ],
            "confidence": "medium",
            "insufficient_evidence": False,
            "rerank_metadata": {
                "ranked_candidates": [
                    {"candidate_id": "2", "reason": "Most directly addresses the question."},
                    {"candidate_id": "1", "reason": "Useful supporting context."},
                ]
            },
            "supporting_points": [
                {"text": "Growth improved.", "citations": [1]},
            ],
            "counter_points": [
                {"text": "Costs also rose.", "citations": [2]},
            ],
        }
    )


def test_render_rag_answer_includes_sections():
    rendered = query_news.render_rag_answer(make_result())

    assert "Answer:" in rendered
    assert "Supporting Points:" in rendered
    assert "Counter Points:" in rendered
    assert "Sources:" in rendered
    assert "[1] Positive article" in rendered


def test_render_rag_answer_debug_mode_includes_rerank_reasons():
    rendered = query_news.render_rag_answer(make_result(), debug_rerank=True)

    assert "Rerank Debug:" in rendered
    assert "Candidate 2: Most directly addresses the question." in rendered


def test_run_question_uses_injected_answer_function():
    calls = []

    def fake_answer_fn(question, vector_store, top_k=3):
        calls.append((question, vector_store, top_k))
        return make_result()

    output = query_news.run_question(
        "What changed?",
        vector_store=object(),
        top_k=5,
        answer_fn=fake_answer_fn,
        debug_rerank=True,
    )

    assert calls and calls[0][0] == "What changed?"
    assert calls[0][2] == 5
    assert "Confidence: medium" in output
    assert "Rerank Debug:" in output


def test_main_supports_one_shot_question(monkeypatch, capsys):
    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def count_articles(self):
            return 2

        def close(self):
            return None

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name):
            self.persist_dir = persist_dir
            self.collection_name = collection_name

    monkeypatch.setattr(query_news, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(query_news, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(
        query_news,
        "run_question",
        lambda question, vector_store, top_k=3, debug_rerank=False: "formatted answer",
    )

    result = query_news.main(["--question", "What changed?"])
    captured = capsys.readouterr()

    assert result == 0
    assert "formatted answer" in captured.out
    assert "Database has 2 articles" in captured.out


def test_main_surfaces_reranker_error(monkeypatch, capsys):
    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def count_articles(self):
            return 2

        def close(self):
            return None

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name):
            self.persist_dir = persist_dir
            self.collection_name = collection_name

    monkeypatch.setattr(query_news, "SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(query_news, "ChromaVectorStore", FakeVectorStore)

    def failing_run_question(question, vector_store, top_k=3, debug_rerank=False):
        raise RuntimeError("reranker unavailable")

    monkeypatch.setattr(query_news, "run_question", failing_run_question)

    result = query_news.main(["--question", "What changed?"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: reranker unavailable" in captured.out
