import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag_index_generation


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeCollection:
    def __init__(self):
        self.writes = []

    def add_chunks(self, chunks):
        self.writes.extend(chunks)

    def read_chunk_metadata(self):
        return tuple(dict(chunk.metadata) for chunk in self.writes)


class FakeCollectionClient:
    def __init__(self):
        self.collections = {}

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, name):
        collection = FakeCollection()
        self.collections[name] = collection
        return collection


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.client = FakeCollectionClient()

    def create_chroma_generation_dependencies(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            collection_client=self.client,
            embedder=lambda chunks: [[float(len(chunk))] for chunk in chunks],
        )


def _runtime(tmp_path: Path, *, verification_valid: bool = True):
    runtime = rag_index_generation._load_runtime_modules()
    stage = tmp_path / "stage"
    source_database = stage / "news_articles.db"
    content_root = stage / "data" / "articles"
    content_root.mkdir(parents=True)
    source_database.touch()
    (stage / "manifest.json").write_text("{}", encoding="utf-8")

    plan = SimpleNamespace(
        source_database_logical_sha256="a" * 64,
        plan_id="reviewed-plan-id",
    )
    verification = SimpleNamespace(
        valid=verification_valid,
        checked_ready_articles=1,
        audit_rows=1,
        issues=() if verification_valid else ("staged hash mismatch",),
    )
    migration = SimpleNamespace(
        STAGED_DATABASE_NAME="news_articles.db",
        STAGED_ARTICLES_DIR="data/articles",
        verify_staged_corpus=lambda supplied: verification,
        load_plan_manifest=lambda supplied: plan,
    )
    secret_content = "canonical secret article body"
    snapshot = runtime.generation_corpus.GenerationCorpusSnapshot(
        candidates=(
            runtime.generation_corpus.CanonicalGenerationCandidate(
                7, secret_content, _sha(secret_content)
            ),
        ),
        eligible_snapshot_fingerprint=runtime.generation_corpus.compute_eligible_snapshot_fingerprint(
            [(7, _sha(secret_content))]
        ),
        source_database_logical_sha256=plan.source_database_logical_sha256,
        snapshot_id=plan.plan_id,
    )
    corpus_calls = []

    def build_snapshot(database, root, **kwargs):
        corpus_calls.append((Path(database), Path(root), kwargs))
        return snapshot

    generation_corpus = SimpleNamespace(
        build_generation_corpus_snapshot=build_snapshot,
        write_generation_manifest=runtime.generation_corpus.write_generation_manifest,
    )
    adapter = FakeAdapter()
    injected = rag_index_generation.RuntimeModules(
        migration=migration,
        generation_corpus=generation_corpus,
        index_generation=runtime.index_generation,
        builder=runtime.builder,
        chroma_adapter=adapter,
    )
    return injected, adapter, corpus_calls, stage, secret_content


def _arguments(stage: Path, generation_db: Path, persist_root: Path):
    return [
        "build",
        "--staging-dir", str(stage),
        "--generation-db", str(generation_db),
        "--persist-root", str(persist_root),
        "--generation-id", "gen-2026-08-15",
        "--collection-name", "news_gen_20260815",
        "--corpus-id", "news",
        "--embedding-artifact", "local-model@sha256:abc",
        "--chunk-size", "12",
        "--chunk-overlap", "3",
    ]


def test_build_creates_verified_generation_manifest_offline_and_never_activates(
    tmp_path, capsys
):
    runtime, adapter, corpus_calls, stage, secret_content = _runtime(tmp_path)
    generation_db = tmp_path / "control" / "generations.sqlite3"
    persist_root = tmp_path / "candidate-chroma"

    result = rag_index_generation.main(
        _arguments(stage, generation_db, persist_root), runtime=runtime
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == rag_index_generation.EXIT_OK
    assert payload["status"] == "verified"
    assert payload["generation_id"] == "gen-2026-08-15"
    assert payload["corpus_snapshot_id"]
    assert payload["counts"] == {"eligible_articles": 1, "indexed_chunks": 3}
    assert payload["active_generation_changed"] is False
    assert Path(payload["generation_db"]) == generation_db.resolve()
    assert Path(payload["persist_root"]) == persist_root.resolve()
    assert corpus_calls == [
        (
            (stage / "news_articles.db").resolve(),
            (stage / "data" / "articles").resolve(),
            {
                "source_database_logical_sha256": "a" * 64,
                "snapshot_id": "reviewed-plan-id",
            },
        )
    ]
    assert adapter.calls[0]["allow_download"] is False
    assert adapter.calls[0]["persist_dir"] == persist_root.resolve()
    manifest = persist_root / "generation-input-manifest.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert secret_content not in manifest.read_text(encoding="utf-8")
    assert manifest_payload["snapshot_id"] == "reviewed-plan-id"
    assert manifest_payload["source_database_logical_sha256"] == "a" * 64

    import sqlite3

    connection = sqlite3.connect(generation_db)
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = ?",
        ("gen-2026-08-15",),
    ).fetchone() == ("verified",)
    assert connection.execute("SELECT COUNT(*) FROM corpus_index_state").fetchone() == (0,)
    connection.close()


def test_allow_model_download_is_explicitly_forwarded(tmp_path, capsys):
    runtime, adapter, _calls, stage, _content = _runtime(tmp_path)
    arguments = _arguments(
        stage, tmp_path / "generation.sqlite3", tmp_path / "persist"
    ) + ["--allow-model-download"]

    assert rag_index_generation.main(arguments, runtime=runtime) == rag_index_generation.EXIT_OK
    capsys.readouterr()
    assert adapter.calls[0]["allow_download"] is True


def test_failed_build_returns_stable_error_and_retains_diagnostic_outputs(
    tmp_path, capsys
):
    runtime, adapter, _calls, stage, _content = _runtime(tmp_path)
    adapter.client.collections["news_gen_20260815"] = FakeCollection()
    generation_db = tmp_path / "generation.sqlite3"
    persist_root = tmp_path / "persist"

    result = rag_index_generation.main(
        _arguments(stage, generation_db, persist_root), runtime=runtime
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == rag_index_generation.EXIT_BUILD_FAILED
    assert payload["status"] == "build_failed"
    assert generation_db.is_file()
    assert (persist_root / "generation-input-manifest.json").is_file()

    import sqlite3

    connection = sqlite3.connect(generation_db)
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = ?",
        ("gen-2026-08-15",),
    ).fetchone() == ("failed",)
    connection.close()


def test_invalid_staging_is_rejected_before_any_output_or_dependency_creation(
    tmp_path, capsys
):
    runtime, adapter, corpus_calls, stage, _content = _runtime(
        tmp_path, verification_valid=False
    )
    generation_db = tmp_path / "generation.sqlite3"
    persist_root = tmp_path / "persist"

    result = rag_index_generation.main(
        _arguments(stage, generation_db, persist_root), runtime=runtime
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == rag_index_generation.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "staged hash mismatch" in payload["error"]
    assert not generation_db.exists()
    assert not persist_root.exists()
    assert adapter.calls == []
    assert corpus_calls == []


@pytest.mark.parametrize("unsafe_target", ["stage", "live", "outputs_overlap"])
def test_preflight_rejects_protected_or_overlapping_output_paths_without_writes(
    tmp_path, capsys, monkeypatch, unsafe_target
):
    runtime, adapter, _calls, stage, _content = _runtime(tmp_path)
    live_root = tmp_path / "live-chroma"
    monkeypatch.setattr(rag_index_generation, "LIVE_CHROMA_ROOT", live_root)
    if unsafe_target == "stage":
        generation_db = stage / "unsafe.sqlite3"
        persist_root = tmp_path / "persist"
    elif unsafe_target == "live":
        generation_db = tmp_path / "generation.sqlite3"
        persist_root = live_root / "candidate"
    else:
        persist_root = tmp_path / "candidate"
        generation_db = persist_root / "generation.sqlite3"

    result = rag_index_generation.main(
        _arguments(stage, generation_db, persist_root), runtime=runtime
    )

    assert result == rag_index_generation.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not generation_db.exists()
    assert not persist_root.exists()
    assert adapter.calls == []


@pytest.mark.parametrize("existing", ["generation_db", "persist_root"])
def test_preflight_rejects_existing_outputs(tmp_path, capsys, existing):
    runtime, adapter, _calls, stage, _content = _runtime(tmp_path)
    generation_db = tmp_path / "generation.sqlite3"
    persist_root = tmp_path / "persist"
    target = generation_db if existing == "generation_db" else persist_root
    if existing == "generation_db":
        target.touch()
    else:
        target.mkdir()

    result = rag_index_generation.main(
        _arguments(stage, generation_db, persist_root), runtime=runtime
    )

    assert result == rag_index_generation.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"), [(0, 0), (10, -1), (10, 10), (10, 11)]
)
def test_invalid_chunk_contract_is_rejected_before_outputs(
    tmp_path, capsys, chunk_size, chunk_overlap
):
    runtime, adapter, _calls, stage, _content = _runtime(tmp_path)
    generation_db = tmp_path / "generation.sqlite3"
    persist_root = tmp_path / "persist"
    arguments = _arguments(stage, generation_db, persist_root)
    arguments[arguments.index("--chunk-size") + 1] = str(chunk_size)
    arguments[arguments.index("--chunk-overlap") + 1] = str(chunk_overlap)

    result = rag_index_generation.main(arguments, runtime=runtime)

    assert result == rag_index_generation.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not generation_db.exists()
    assert not persist_root.exists()
    assert adapter.calls == []
