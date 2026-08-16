from types import SimpleNamespace

import query_news
from event_collector.rag_answering import RAGAnswerResponse
from event_collector.serving_generation_factory import CorpusUnavailableError


def test_query_cli_defaults_to_activated_v2_corpus():
    args = query_news.parse_args([])

    assert args.canonical_db_path == "data/rag_corpus_v2_20260815/news_articles.db"
    assert args.canonical_content_root == "data/rag_corpus_v2_20260815/data/articles"
    assert args.chroma_persist_dir == "data/rag_index_v2_20260815"


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
    pinned = SimpleNamespace(
        reader=SimpleNamespace(with_time_window=lambda **kwargs: object()),
        active_generation=SimpleNamespace(generation_id="gen-active"),
        eligibility=SimpleNamespace(articles=(object(), object()), exclusions=()),
    )
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: pinned)
    monkeypatch.setattr(
        f"{cli_path}.run_question",
        lambda question, vector_store, top_k=3, retrieval_top_k=5, debug_rerank=False, retrieval_intent="direct": "formatted answer",
    )

    result = query_news.main(["--question", "What changed?"])
    captured = capsys.readouterr()

    assert result == 0
    assert "formatted answer" in captured.out
    assert "Active generation: gen-active" in captured.out
    assert "Eligible articles: 2" in captured.out


def test_main_builds_generation_config_and_passes_only_pinned_reader(monkeypatch):
    cli_path = "event_collector.cli.query_news"
    captured = {}

    def create_reader(config):
        captured["config"] = config
        return SimpleNamespace(
            reader=SimpleNamespace(with_time_window=lambda **kwargs: "readonly-generation-reader"),
            active_generation=SimpleNamespace(generation_id="gen-7"),
            eligibility=SimpleNamespace(articles=(object(),), exclusions=(object(),)),
        )

    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", create_reader)

    def run_question(question, vector_store, **kwargs):
        captured["reader"] = vector_store
        return "answer"

    monkeypatch.setattr(f"{cli_path}.run_question", run_question)

    assert query_news.main(
        [
            "--question", "What changed?",
            "--index-control-db-path", "control.db",
            "--index-corpus-id", "news-v2",
            "--canonical-db-path", "canonical.db",
            "--canonical-content-root", "canonical",
            "--chroma-persist-dir", "chroma-v2",
        ]
    ) == 0
    assert captured["reader"] == "readonly-generation-reader"
    assert captured["config"].index_control_db_path == "control.db"
    assert captured["config"].index_corpus_id == "news-v2"
    assert captured["config"].canonical_db_path == "canonical.db"
    assert captured["config"].canonical_content_root == "canonical"
    assert captured["config"].chroma_persist_dir == "chroma-v2"


def test_main_binds_utc_time_window_to_the_pinned_generation_reader(monkeypatch):
    cli_path = "event_collector.cli.query_news"
    captured = {}

    class Reader:
        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return "time-scoped-reader"

    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: SimpleNamespace(
            reader=Reader(),
            active_generation=SimpleNamespace(generation_id="gen-7"),
            eligibility=SimpleNamespace(articles=(object(),), exclusions=()),
        ),
    )
    monkeypatch.setattr(
        f"{cli_path}.run_question", lambda question, vector_store, **kwargs: captured.setdefault("reader", vector_store) or "answer"
    )

    assert query_news.main(
        [
            "--question", "What changed?",
            "--start-at", "2026-08-15T00:00:00+00:00",
            "--end-at", "2026-08-16T00:00:00+00:00",
        ]
    ) == 0
    assert captured["window"] == {
        "start_at": "2026-08-15T00:00:00+00:00",
        "end_at": "2026-08-16T00:00:00+00:00",
        "latest_at": None,
        "lookback_days": None,
    }
    assert captured["reader"] == "time-scoped-reader"


def test_main_uses_configured_finite_lookback_when_no_window_is_supplied(monkeypatch):
    cli_path = "event_collector.cli.query_news"
    captured = {}

    class Reader:
        def with_time_window(self, **kwargs):
            captured["window"] = kwargs
            return "time-scoped-reader"

    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: SimpleNamespace(
            reader=Reader(),
            active_generation=SimpleNamespace(generation_id="gen-7"),
            eligibility=SimpleNamespace(articles=(object(),), exclusions=()),
        ),
    )
    monkeypatch.setattr(
        f"{cli_path}.resolve_query_time_window",
        lambda defaults, **kwargs: {
            "start_at": None,
            "end_at": None,
            "latest_at": "2026-08-15T12:00:00+00:00",
            "lookback_days": 30,
        },
    )
    monkeypatch.setattr(f"{cli_path}.run_question", lambda *args, **kwargs: "answer")

    assert query_news.main(["--question", "What changed?"]) == 0
    assert captured["window"]["latest_at"] == "2026-08-15T12:00:00+00:00"
    assert captured["window"]["lookback_days"] == 30


def test_main_surfaces_reranker_error(monkeypatch, capsys):
    cli_path = "event_collector.cli.query_news"
    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: SimpleNamespace(
            reader=SimpleNamespace(with_time_window=lambda **kwargs: object()),
            active_generation=SimpleNamespace(generation_id="gen-active"),
            eligibility=SimpleNamespace(articles=(object(),), exclusions=()),
        ),
    )

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


def test_main_fails_closed_when_no_active_corpus_and_never_runs_question(monkeypatch, capsys):
    cli_path = "event_collector.cli.query_news"

    def unavailable(config):
        raise CorpusUnavailableError(
            stage="active_generation",
            reason_code="missing_pointer",
            message="no active generation",
        )

    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", unavailable)
    monkeypatch.setattr(
        f"{cli_path}.run_question",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not query")),
    )

    assert query_news.main(["--question", "What changed?"]) == 1
    captured = capsys.readouterr()
    assert "CORPUS_UNAVAILABLE:missing_pointer" in captured.out
