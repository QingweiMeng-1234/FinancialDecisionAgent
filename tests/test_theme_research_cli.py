import os
import tempfile
from types import SimpleNamespace

import theme_research
from event_collector.serving_generation_factory import CorpusUnavailableError


def test_theme_cli_defaults_to_activated_v2_corpus():
    args = theme_research.parse_args(["--theme", "AI", "--analysis-goal", "monitor"])

    assert args.canonical_db_path == "data/rag_corpus_v2_20260815/news_articles.db"
    assert args.canonical_content_root == "data/rag_corpus_v2_20260815/data/articles"
    assert args.chroma_persist_dir == "data/rag_index_v2_20260815"


class FakeStorage:
    def __init__(self, db_path):
        self.db_path = db_path

    def init_db(self):
        return None

    def close(self):
        return None


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
    reader = object()
    pinned = SimpleNamespace(
        reader=reader,
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            corpus_id="news-v2",
            collection_name="news-active",
            corpus_snapshot_id="snapshot-1",
            embedding_artifact="embedder-v1",
            index_config_fingerprint="config-1",
        ),
        eligibility=SimpleNamespace(
            policy_version="eligibility-v1",
            articles=(object(), object()),
            exclusions=(object(),),
        ),
    )
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: pinned)

    calls = {}

    def fake_run_theme_research(
        request, storage, local_vector_reader, retrieval_provenance
    ):
        calls["request"] = request
        calls["storage"] = storage.db_path
        calls["reader"] = local_vector_reader
        calls["provenance"] = retrieval_provenance
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
                "--canonical-db-path",
                os.path.join(tmpdir, "news.db"),
                "--chroma-persist-dir",
                os.path.join(tmpdir, "chroma"),
            ]
        )
        captured = capsys.readouterr()

    assert exit_code == 0
    assert calls["request"].tickers == ["NVDA", "MSFT"]
    assert calls["reader"] is reader
    assert calls["provenance"]["generation_id"] == "gen-active"
    assert calls["provenance"]["eligible_article_count"] == 2
    assert "Theme Research:" in captured.out
    assert "Artifacts:" in captured.out


def test_theme_cli_corpus_unavailable_does_not_open_storage_or_run_discovery(
    monkeypatch, capsys
):
    cli_path = "event_collector.cli.theme_research"
    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: (_ for _ in ()).throw(
            CorpusUnavailableError(
                stage="active_generation",
                reason_code="missing_pointer",
                message="no active generation",
            )
        ),
    )
    monkeypatch.setattr(
        f"{cli_path}.SQLiteNewsStore",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("storage opened")),
    )
    monkeypatch.setattr(
        f"{cli_path}.run_theme_research",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("discovery ran")),
    )

    assert theme_research.main(
        ["--theme", "AI infra", "--analysis-goal", "map bottlenecks"]
    ) == 1
    assert "CORPUS_UNAVAILABLE:missing_pointer" in capsys.readouterr().out
