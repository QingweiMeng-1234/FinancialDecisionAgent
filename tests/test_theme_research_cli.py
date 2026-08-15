import os
import tempfile

import theme_research


class FakeStorage:
    def __init__(self, db_path):
        self.db_path = db_path

    def init_db(self):
        return None

    def close(self):
        return None


class FakeVectorStore:
    def __init__(self, persist_dir, collection_name):
        self.persist_dir = persist_dir
        self.collection_name = collection_name


def test_theme_research_cli_parses_ticker_hints():
    args = theme_research.parse_args(
        [
            "--theme",
            "AI infra",
            "--analysis-goal",
            "map bottlenecks",
            "--tickers",
            "nvda, msft",
        ]
    )

    assert theme_research.parse_ticker_hints(args.tickers) == ["nvda", "msft"]


def test_theme_research_cli_runs_workflow_and_prints_artifacts(monkeypatch, capsys):
    cli_path = "event_collector.cli.theme_research"
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(f"{cli_path}.ChromaVectorStore", FakeVectorStore)

    calls = {}

    def fake_run_theme_research(request, storage, vector_store):
        calls["request"] = request
        calls["storage"] = storage.db_path
        calls["vector"] = (vector_store.persist_dir, vector_store.collection_name)
        return type(
            "Result",
            (),
            {
                "metadata": type("Metadata", (), {"theme": request.theme, "analysis_goal": request.analysis_goal})(),
                "evidence": [object(), object()],
                "sufficiency": type(
                    "Sufficiency",
                    (),
                    {
                        "structure_sufficient": True,
                        "fresh_monitoring_sufficient": False,
                    },
                )(),
                "related_companies": ["NVDA", "MSFT"],
                "artifact_paths": {
                    "theme_card": os.path.join("docs", "research", "theme-card.md"),
                    "supply_chain_map": os.path.join("docs", "research", "supply-chain-map.md"),
                },
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_theme_research", fake_run_theme_research)

    with tempfile.TemporaryDirectory() as tmpdir:
        exit_code = theme_research.main(
            [
                "--theme",
                "AI infra",
                "--analysis-goal",
                "map bottlenecks",
                "--tickers",
                "NVDA,MSFT",
                "--db-path",
                os.path.join(tmpdir, "news.db"),
                "--persist-dir",
                os.path.join(tmpdir, "chroma"),
            ]
        )
        captured = capsys.readouterr()

    assert exit_code == 0
    assert calls["request"].tickers == ["NVDA", "MSFT"]
    assert "Theme Research:" in captured.out
    assert "Artifacts:" in captured.out
