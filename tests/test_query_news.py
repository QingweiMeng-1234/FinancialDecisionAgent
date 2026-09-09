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

    def fake_answer_fn(question, vector_store, top_k=3, retrieval_top_k=5, retrieval_intent="direct"):
        calls.append((question, vector_store, top_k, retrieval_top_k, retrieval_intent))
        return make_result()

    output = query_news.run_question(
        "What changed?",
        vector_store=object(),
        top_k=5,
        retrieval_top_k=7,
        answer_fn=fake_answer_fn,
        debug_rerank=True,
    )

    assert calls and calls[0][0] == "What changed?"
    assert calls[0][2] == 5
    assert calls[0][3] == 7
    assert calls[0][4] == "direct"
    assert "Confidence: medium" in output
    assert "Rerank Debug:" in output


def test_main_supports_one_shot_question(monkeypatch, capsys):
    cli_path = "event_collector.cli.query_news"
    constructed_vector_stores = []

    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def count_articles(self):
            return 2

        def list_retrieval_eligible_article_ids(self):
            return {1, 2}

        def close(self):
            return None

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name, *, db_path):
            self.persist_dir = persist_dir
            self.collection_name = collection_name
            self.db_path = db_path
            constructed_vector_stores.append(self)

    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.create_corpus_vector_store", FakeVectorStore)
    monkeypatch.setattr(
        f"{cli_path}.run_question",
        lambda question, vector_store, top_k=3, retrieval_top_k=5, debug_rerank=False, retrieval_intent="direct": "formatted answer",
    )

    result = query_news.main(["--question", "What changed?"])
    captured = capsys.readouterr()

    assert result == 0
    assert "formatted answer" in captured.out
    assert "Database has 2 articles" in captured.out
    assert len(constructed_vector_stores) == 1
    assert constructed_vector_stores[0].db_path == "news_articles.db"


def test_main_surfaces_reranker_error(monkeypatch, capsys):
    cli_path = "event_collector.cli.query_news"

    class FakeStorage:
        def __init__(self, db_path):
            self.db_path = db_path

        def count_articles(self):
            return 2

        def list_retrieval_eligible_article_ids(self):
            return {1, 2}

        def close(self):
            return None

    class FakeVectorStore:
        def __init__(self, persist_dir, collection_name, *, db_path):
            self.persist_dir = persist_dir
            self.collection_name = collection_name
            self.db_path = db_path

    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.create_corpus_vector_store", FakeVectorStore)

    def failing_run_question(
        question,
        vector_store,
        top_k=3,
        retrieval_top_k=5,
        debug_rerank=False,
        retrieval_intent="direct",
    ):
        raise RuntimeError("reranker unavailable")

    monkeypatch.setattr(f"{cli_path}.run_question", failing_run_question)

    result = query_news.main(["--question", "What changed?"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: reranker unavailable" in captured.out
