import ingest_news
from event_collector.financial_agent_mcp import FinancialAgentRuntimeConfig


def test_main_runs_ingestion_only(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.ingest_news"

    def fake_run_news_pipeline(request):
        calls["request"] = request
        return type(
            "Result",
            (),
            {
                "collected_events": 2,
                "stats": {
                    "total_events": 2,
                    "saved": 2,
                    "summarized": 2,
                    "indexed": 2,
                    "skipped": 0,
                },
                "total_articles": 4,
                "answer_text": None,
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_news_pipeline", fake_run_news_pipeline)

    result = ingest_news.main([])
    captured = capsys.readouterr()

    assert result == 0
    assert "Successor generation rebuild and activation are required before grounded RAG queries." in captured.out
    assert "Ready for grounded RAG queries." not in captured.out
    assert "Successor generation: deferred (not activated)" in captured.out
    assert "Collected 2 events" in captured.out
    assert "Summarized:    2" in captured.out
    runtime = FinancialAgentRuntimeConfig()
    assert calls["request"].db_path == runtime.canonical_db_path
    assert calls["request"].persist_dir == runtime.chroma_persist_dir
    assert calls["request"].include_manual is False
    assert calls["request"].news_endpoint == "everything"
    assert calls["request"].news_days_back == 7
    assert calls["request"].show_progress is True


def test_main_rejects_question_until_a_successor_generation_is_activated(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.ingest_news"

    def fake_run_news_pipeline(request):
        calls["request"] = request
        return type(
            "Result",
            (),
            {
                "collected_events": 1,
                "stats": {
                    "total_events": 1,
                    "saved": 1,
                    "summarized": 1,
                    "indexed": 1,
                    "skipped": 0,
                },
                "total_articles": 1,
                "answer_text": "formatted grounded answer",
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_news_pipeline", fake_run_news_pipeline)

    result = ingest_news.main(["--question", "What changed?", "--top-k", "5", "--debug-rerank"])
    captured = capsys.readouterr()

    assert result == 1
    assert "ingestion does not serve grounded questions" in captured.out
    assert "run_news_pipeline" not in calls


def test_main_supports_batch_ingest_flags(monkeypatch, capsys):
    calls = {}
    cli_path = "event_collector.cli.ingest_news"

    def fake_run_news_pipeline(request):
        calls["request"] = request
        return type(
            "Result",
            (),
            {
                "collected_events": 1,
                "stats": {
                    "total_events": 1,
                    "saved": 1,
                    "summarized": 1,
                    "indexed": 1,
                    "skipped": 0,
                },
                "total_articles": 1,
                "answer_text": None,
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_news_pipeline", fake_run_news_pipeline)

    result = ingest_news.main(
        [
            "--include-manual",
            "--news-endpoint",
            "top-headlines",
            "--news-days-back",
            "3",
            "--news-page",
            "2",
            "--news-sort-by",
            "popularity",
            "--news-page-size",
            "25",
            "--no-progress",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "Collected 1 events" in captured.out
    assert calls["request"].include_manual is True
    assert calls["request"].news_endpoint == "top-headlines"
    assert calls["request"].news_days_back == 3
    assert calls["request"].news_page == 2
    assert calls["request"].news_sort_by == "popularity"
    assert calls["request"].news_page_size == 25
    assert calls["request"].show_progress is False


def test_main_surfaces_ingestion_or_query_errors(monkeypatch, capsys):
    cli_path = "event_collector.cli.ingest_news"
    monkeypatch.setattr(
        f"{cli_path}.run_news_pipeline",
        lambda request: (_ for _ in ()).throw(RuntimeError("ingest failed")),
    )

    result = ingest_news.main([])
    captured = capsys.readouterr()

    assert result == 1
    assert "Error: ingest failed" in captured.out
