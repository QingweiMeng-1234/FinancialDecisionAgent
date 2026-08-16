import os
import tempfile
from types import SimpleNamespace

import theme_research
from event_collector.service_defaults import ServiceDefaults
from event_collector.serving_generation_factory import CorpusUnavailableError
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationProof,
)


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


class FakeReader:
    def with_time_window(self, **kwargs):
        return self


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
    reader = FakeReader()
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
        request,
        storage,
        local_vector_reader,
        retrieval_provenance,
        discovered_article_sink,
        publish_assets,
    ):
        assert publish_assets is False
        calls["request"] = request
        calls["storage"] = storage.db_path
        calls["reader"] = local_vector_reader
        calls["provenance"] = retrieval_provenance
        calls["sink"] = discovered_article_sink
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
                "candidate_segments": ["semiconductors"],
                "theme_summary": "AI infrastructure research",
                "artifact_paths": {
                    "theme_card": os.path.join("docs", "research", "theme-card.md"),
                    "supply_chain_map": os.path.join("docs", "research", "supply-chain-map.md"),
                },
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_theme_research", fake_run_theme_research)
    monkeypatch.setattr(
        f"{cli_path}.write_theme_research_assets",
        lambda *args, **kwargs: {
            "theme_card": os.path.join("docs", "research", "theme-card.md"),
            "supply_chain_map": os.path.join("docs", "research", "supply-chain-map.md"),
        },
    )

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
    assert callable(calls["sink"])
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


def test_theme_cli_scopes_each_reader_once_with_defaults_or_explicit_window(
    monkeypatch
):
    """SELECT INVARIANT: every theme CLI run has one finite local-news scope."""
    cli_path = "event_collector.cli.theme_research"

    class RecordingReader:
        def __init__(self):
            self.calls = []
            self.scoped = object()

        def with_time_window(self, **kwargs):
            self.calls.append(kwargs)
            return self.scoped

    def pinned(reader):
        return SimpleNamespace(
            reader=reader,
            active_generation=SimpleNamespace(
                generation_id="gen-active",
                corpus_id="news",
                collection_name="news-active",
                corpus_snapshot_id="snapshot-1",
                embedding_artifact="embedder-v1",
                index_config_fingerprint="config-1",
            ),
            eligibility=SimpleNamespace(
                policy_version="eligibility-v1", articles=(), exclusions=()
            ),
        )

    default_reader = RecordingReader()
    explicit_reader = RecordingReader()
    readers = iter((default_reader, explicit_reader))
    monkeypatch.setattr(
        f"{cli_path}.create_active_generation_reader",
        lambda config: pinned(next(readers)),
    )
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    monkeypatch.setattr(
        f"{cli_path}.load_service_defaults",
        lambda: ServiceDefaults(query_lookback_days=23),
    )
    downstream_readers = []

    def run_research(
        request,
        storage,
        local_vector_reader,
        retrieval_provenance,
        discovered_article_sink,
        publish_assets,
    ):
        assert publish_assets is False
        downstream_readers.append(local_vector_reader)
        return type(
            "Result",
            (),
            {
                "metadata": type(
                    "Metadata", (), {"theme": request.theme, "analysis_goal": request.analysis_goal}
                )(),
                "evidence": [],
                "sufficiency": type(
                    "Sufficiency",
                    (),
                    {"structure_sufficient": False, "fresh_monitoring_sufficient": False},
                )(),
                    "related_companies": [],
                    "candidate_segments": [],
                    "theme_summary": "AI research",
                "artifact_paths": {},
            },
        )()

    monkeypatch.setattr(f"{cli_path}.run_theme_research", run_research)
    monkeypatch.setattr(f"{cli_path}.write_theme_research_assets", lambda *args, **kwargs: {})

    base = ["--theme", "AI", "--analysis-goal", "monitor"]
    assert theme_research.main(base) == 0
    assert theme_research.main(
        base
        + [
            "--start-at",
            "2026-08-01T00:00:00+00:00",
            "--end-at",
            "2026-08-10T00:00:00+00:00",
        ]
    ) == 0

    assert len(default_reader.calls) == 1
    assert default_reader.calls[0]["lookback_days"] == 23
    latest_at = __import__("datetime").datetime.fromisoformat(
        default_reader.calls[0]["latest_at"]
    )
    assert latest_at.utcoffset() is not None
    assert len(explicit_reader.calls) == 1
    assert explicit_reader.calls[0] == {
        "start_at": "2026-08-01T00:00:00+00:00",
        "end_at": "2026-08-10T00:00:00+00:00",
        "latest_at": None,
        "lookback_days": None,
    }
    assert downstream_readers == [default_reader.scoped, explicit_reader.scoped]


def test_theme_cli_handoff_builds_and_activates_a_successor_generation(monkeypatch):
    """SELECT INVARIANT: the real CLI wires each accepted discovery to a durable generation build."""
    cli_path = "event_collector.cli.theme_research"
    reader = FakeReader()
    pinned = SimpleNamespace(
        reader=reader,
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            corpus_id="news",
            collection_name="news-active",
            corpus_snapshot_id="snapshot-1",
            embedding_artifact="embedder-v1",
            index_config_fingerprint="config-1",
        ),
        eligibility=SimpleNamespace(policy_version="eligibility-v1", articles=(), exclusions=()),
    )
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: pinned)
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    coordinator_requests = []
    coordinator_configs = []

    def coordinator_factory(config):
        coordinator_configs.append(config)

        def coordinate(request):
            coordinator_requests.append(request)
            return SuccessorGenerationProof(
                corpus_id="news",
                generation_id="gen-successor",
                corpus_snapshot_id="snapshot-successor",
                index_config_fingerprint="config-successor",
                status="verified",
                chunk_verification_valid=True,
                articles=(
                    GenerationArticleProof(41, "a" * 64),
                    GenerationArticleProof(42, "b" * 64),
                ),
            )

        return coordinate

    monkeypatch.setattr(
        f"{cli_path}.create_runtime_successor_generation_coordinator", coordinator_factory
    )

    def run_research(request, **kwargs):
        kwargs["discovered_article_sink"](41, "a" * 64)
        kwargs["discovered_article_sink"](42, "b" * 64)
        return SimpleNamespace(
            metadata=SimpleNamespace(theme=request.theme, analysis_goal=request.analysis_goal),
            evidence=[],
            sufficiency=SimpleNamespace(
                structure_sufficient=False, fresh_monitoring_sufficient=False
            ),
            related_companies=[],
            candidate_segments=[],
            theme_summary="AI research",
            artifact_paths={},
        )

    monkeypatch.setattr(f"{cli_path}.run_theme_research", run_research)
    monkeypatch.setattr(
        f"{cli_path}._read_activation_receipt",
        lambda *args, **kwargs: {
            "corpus_id": "news",
            "generation_id": "gen-successor",
            "corpus_snapshot_id": "snapshot-successor",
            "index_config_fingerprint": "config-successor",
            "status": "active",
            "activated_at": "2026-08-16T01:02:03+00:00",
        },
    )
    monkeypatch.setattr(f"{cli_path}.write_theme_research_assets", lambda *args, **kwargs: {})

    assert theme_research.main(["--theme", "AI", "--analysis-goal", "monitor"]) == 0
    assert coordinator_configs[0].activate_verified_generation is True
    assert len(coordinator_requests) == 1
    assert coordinator_requests[0].corpus_id == "news"
    assert [(proof.article_id, proof.indexed_content_sha256) for proof in coordinator_requests[0].articles] == [
        (41, "a" * 64),
        (42, "b" * 64),
    ]


def test_theme_cli_does_not_publish_assets_when_successor_activation_fails(
    monkeypatch, tmp_path, capsys
):
    """SELECT INVARIANT: an activation failure leaves no success research artifact behind."""
    cli_path = "event_collector.cli.theme_research"
    pinned = SimpleNamespace(
        reader=FakeReader(),
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            corpus_id="news",
            collection_name="news-active",
            corpus_snapshot_id="snapshot-before",
            embedding_artifact="embedder-v1",
            index_config_fingerprint="config-before",
        ),
        eligibility=SimpleNamespace(policy_version="eligibility-v1", articles=(), exclusions=()),
    )
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: pinned)
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    artifact_path = tmp_path / "research" / "ai" / "theme-card.md"
    calls = {}

    def run_research(request, **kwargs):
        calls["publish_assets"] = kwargs.get("publish_assets", True)
        if calls["publish_assets"]:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text("published too early", encoding="utf-8")
        kwargs["discovered_article_sink"](41, "a" * 64)
        return SimpleNamespace(
            metadata=SimpleNamespace(theme=request.theme, analysis_goal=request.analysis_goal),
            evidence=[],
            sufficiency=SimpleNamespace(
                structure_sufficient=False, fresh_monitoring_sufficient=False
            ),
            related_companies=[],
            candidate_segments=[],
            theme_summary="pending activation",
            artifact_paths={},
        )

    monkeypatch.setattr(f"{cli_path}.run_theme_research", run_research)
    monkeypatch.setattr(
        f"{cli_path}.create_runtime_successor_generation_coordinator",
        lambda config: lambda request: (_ for _ in ()).throw(RuntimeError("activation lost")),
    )

    assert theme_research.main(["--theme", "AI", "--analysis-goal", "monitor"]) == 1
    assert calls["publish_assets"] is False
    assert not artifact_path.exists()
    assert "activation lost" in capsys.readouterr().out


def test_theme_cli_persists_handoff_proof_with_activation_receipt_before_publishing(
    monkeypatch
):
    """SELECT INVARIANT: published assets bind the handoff run to the activated generation receipt."""
    cli_path = "event_collector.cli.theme_research"
    pinned = SimpleNamespace(
        reader=FakeReader(),
        active_generation=SimpleNamespace(
            generation_id="gen-active",
            corpus_id="news",
            collection_name="news-active",
            corpus_snapshot_id="snapshot-before",
            embedding_artifact="embedder-v1",
            index_config_fingerprint="config-before",
        ),
        eligibility=SimpleNamespace(policy_version="eligibility-v1", articles=(), exclusions=()),
    )
    monkeypatch.setattr(f"{cli_path}.create_active_generation_reader", lambda config: pinned)
    monkeypatch.setattr(f"{cli_path}.SQLiteNewsStore", FakeStorage)
    events = []

    def run_research(request, **kwargs):
        assert kwargs["publish_assets"] is False
        events.append("research")
        kwargs["discovered_article_sink"](41, "a" * 64)
        return SimpleNamespace(
            metadata=SimpleNamespace(theme=request.theme, analysis_goal=request.analysis_goal),
            evidence=[],
            sufficiency=SimpleNamespace(
                structure_sufficient=False, fresh_monitoring_sufficient=False
            ),
            related_companies=[],
            candidate_segments=[],
            theme_summary="activated research",
            artifact_paths={},
        )

    proof = SuccessorGenerationProof(
        corpus_id="news",
        generation_id="gen-successor",
        corpus_snapshot_id="snapshot-successor",
        index_config_fingerprint="config-successor",
        status="verified",
        chunk_verification_valid=True,
        articles=(GenerationArticleProof(41, "a" * 64),),
    )
    monkeypatch.setattr(f"{cli_path}.run_theme_research", run_research)
    monkeypatch.setattr(
        f"{cli_path}.create_runtime_successor_generation_coordinator",
        lambda config: lambda request: events.append("activate") or proof,
    )
    monkeypatch.setattr(
        f"{cli_path}._read_activation_receipt",
        lambda *args, **kwargs: {
            "corpus_id": "news",
            "generation_id": "gen-successor",
            "corpus_snapshot_id": "snapshot-successor",
            "index_config_fingerprint": "config-successor",
            "status": "active",
            "activated_at": "2026-08-16T01:02:03+00:00",
        },
        raising=False,
    )
    published = {}

    def write_assets(*args, **kwargs):
        events.append("publish")
        published["handoff"] = kwargs["generation_handoff_provenance"]
        return {"theme_card": "theme-card.md"}

    monkeypatch.setattr(f"{cli_path}.write_theme_research_assets", write_assets, raising=False)

    assert theme_research.main(["--theme", "AI", "--analysis-goal", "monitor"]) == 0
    assert events == ["research", "activate", "publish"]
    assert published["handoff"]["generation_id"] == "gen-successor"
    assert published["handoff"]["corpus_snapshot_id"] == "snapshot-successor"
    assert published["handoff"]["activation_receipt"]["activated_at"] == "2026-08-16T01:02:03+00:00"
    assert published["handoff"]["handoff_run_id"].startswith("theme-")
