from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from event_collector import index_generation
from event_collector.successor_generation_coordinator import (
    RuntimeSuccessorGenerationCoordinator,
    SuccessorGenerationCoordinatorConfig,
    SuccessorGenerationCoordinatorError,
)
from event_collector.watchlist_workflow import (
    GenerationArticleProof,
    SuccessorGenerationBuildRequest,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_source(tmp_path):
    content_root = tmp_path / "articles"
    content_root.mkdir()
    database_path = tmp_path / "canonical.db"
    rows = [(1, "alpha market update"), (2, "bravo company filing")]
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source TEXT,
            content_path TEXT,
            active_content_sha256 TEXT,
            content_status TEXT,
            content_validation_status TEXT,
            index_status TEXT,
            quarantined_at TEXT
        )
        """
    )
    for article_id, content in rows:
        digest = _sha(content)
        relative_path = f"{article_id}/{digest}.txt"
        target = content_root / relative_path
        target.parent.mkdir()
        target.write_text(content, encoding="utf-8")
        connection.execute(
            """
            INSERT INTO articles VALUES (?, 'news', ?, ?, 'ready', 'verified', 'ready', NULL)
            """,
            (article_id, relative_path, digest),
        )
    connection.commit()
    connection.close()
    return database_path, content_root, rows


class _Collection:
    def __init__(self):
        self.writes = []

    def add_chunks(self, chunks):
        self.writes.extend(chunks)

    def read_chunk_metadata(self):
        return [dict(chunk.metadata) for chunk in self.writes]


class _CollectionClient:
    def __init__(self):
        self.collections = {}

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, name):
        collection = _Collection()
        self.collections[name] = collection
        return collection


def _dependencies(client):
    return SimpleNamespace(
        collection_client=client,
        embedder=lambda chunks: [[float(len(chunk))] for chunk in chunks],
    )


def test_builds_a_verified_successor_from_the_current_canonical_snapshot_without_activation(tmp_path):
    """SELECT INVARIANT: a refresh candidate is proven by a new generation, never an active pointer."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    control_db = tmp_path / "control.db"
    client = _CollectionClient()
    config = SuccessorGenerationCoordinatorConfig(
        control_db_path=control_db,
        canonical_db_path=canonical_db,
        canonical_content_root=content_root,
        chroma_persist_dir=tmp_path / "chroma",
        embedding_artifact="deterministic-test-model@v1",
        chunk_size=8,
        chunk_overlap=0,
        collection_prefix="news_articles_successor",
    )
    coordinator = RuntimeSuccessorGenerationCoordinator(
        config,
        dependencies_factory=lambda **kwargs: _dependencies(client),
        generation_token_factory=lambda: "run-0001",
    )

    proof = coordinator(
        SuccessorGenerationBuildRequest(
            run_id="refresh-1",
            scope_key="scope-1",
            corpus_id="news",
            articles=tuple(GenerationArticleProof(article_id, _sha(content)) for article_id, content in rows),
        )
    )

    assert proof.status == "verified"
    assert proof.chunk_verification_valid is True
    assert proof.generation_id == "news-successor-run-0001"
    assert proof.corpus_id == "news"
    assert [(item.article_id, item.indexed_content_sha256) for item in proof.articles] == [
        (article_id, _sha(content)) for article_id, content in rows
    ]
    assert set(client.collections) == {"news_articles_successor_run-0001"}

    connection = sqlite3.connect(control_db)
    try:
        generation = connection.execute(
            "SELECT status, corpus_snapshot_id, index_config_fingerprint FROM index_generations"
        ).fetchone()
        proof_row = connection.execute(
            "SELECT status, issue_count, observed_chunk_count FROM generation_chunk_verification"
        ).fetchone()
        manifest = connection.execute(
            "SELECT article_id, indexed_content_sha256, index_status FROM article_index_manifest ORDER BY article_id"
        ).fetchall()
        active = connection.execute("SELECT * FROM corpus_index_state").fetchall()
    finally:
        connection.close()

    assert generation == ("verified", proof.corpus_snapshot_id, proof.index_config_fingerprint)
    assert proof_row == ("verified", 0, sum(len(chunk.document) > 0 for chunk in client.collections["news_articles_successor_run-0001"].writes))
    assert manifest == [(article_id, _sha(content), "indexed") for article_id, content in rows]
    assert active == []


def test_explicit_activation_switches_the_pointer_only_after_a_verified_successor_readback(tmp_path):
    """SELECT INVARIANT: an opt-in runtime coordinator atomically promotes its own verified successor."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    control_db = tmp_path / "control.db"
    client = _CollectionClient()
    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=control_db,
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
            embedding_artifact="deterministic-test-model@v1",
            chunk_size=8,
            chunk_overlap=0,
            activate_verified_generation=True,
        ),
        dependencies_factory=lambda **kwargs: _dependencies(client),
        generation_token_factory=lambda: "activate-0001",
    )

    proof = coordinator(
        SuccessorGenerationBuildRequest(
            run_id="refresh-activate",
            scope_key="scope-activate",
            corpus_id="news",
            articles=tuple(GenerationArticleProof(article_id, _sha(content)) for article_id, content in rows),
        )
    )

    with sqlite3.connect(control_db) as connection:
        active = connection.execute(
            "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = 'news'"
        ).fetchone()
        status = connection.execute(
            "SELECT status FROM index_generations WHERE generation_id = ?", (proof.generation_id,)
        ).fetchone()
    assert active == (proof.generation_id,)
    assert status == ("active",)


def test_activation_uses_the_observed_active_pointer_and_rejects_a_lost_update(
    tmp_path, monkeypatch
):
    """SELECT INVARIANT: coordinator activation is a CAS against the pointer observed before its build."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    control_db = tmp_path / "control.db"
    with sqlite3.connect(control_db) as connection:
        index_generation.initialize_schema(connection)
        for generation_id in ("old-active", "concurrent-winner"):
            index_generation.create_generation(
                connection,
                generation_id=generation_id,
                corpus_id="news",
                collection_name=f"news_articles_{generation_id}",
                embedding_artifact="deterministic-test-model@v1",
                chunk_config={"chunk_size": 8, "chunk_overlap": 0},
                corpus_snapshot_id=f"snapshot-{generation_id}",
            )
            connection.execute(
                "UPDATE index_generations SET status = 'verified' WHERE generation_id = ?",
                (generation_id,),
            )
        connection.commit()
        index_generation.activate_generation(
            connection,
            "old-active",
            expected_current_generation_id=None,
        )

    original_activate = index_generation.activate_generation
    observed_expectations = []

    def activate_after_concurrent_winner(
        connection, generation_id, *, expected_current_generation_id
    ):
        observed_expectations.append(expected_current_generation_id)
        with sqlite3.connect(control_db) as contender_connection:
            index_generation.initialize_schema(contender_connection)
            original_activate(
                contender_connection,
                "concurrent-winner",
                expected_current_generation_id="old-active",
            )
        return original_activate(
            connection,
            generation_id,
            expected_current_generation_id=expected_current_generation_id,
        )

    monkeypatch.setattr(
        index_generation,
        "activate_generation",
        activate_after_concurrent_winner,
    )
    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=control_db,
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
            embedding_artifact="deterministic-test-model@v1",
            chunk_size=8,
            chunk_overlap=0,
            activate_verified_generation=True,
        ),
        dependencies_factory=lambda **kwargs: _dependencies(_CollectionClient()),
        generation_token_factory=lambda: "stale-contender",
    )

    with pytest.raises(
        SuccessorGenerationCoordinatorError,
        match="verified successor generation activation failed",
    ):
        coordinator(
            SuccessorGenerationBuildRequest(
                run_id="refresh-stale-contender",
                scope_key="scope-stale-contender",
                corpus_id="news",
                articles=tuple(
                    GenerationArticleProof(article_id, _sha(content))
                    for article_id, content in rows
                ),
            )
        )

    assert observed_expectations == ["old-active"]
    with sqlite3.connect(control_db) as connection:
        assert connection.execute(
            "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = 'news'"
        ).fetchone() == ("concurrent-winner",)
        assert connection.execute(
            "SELECT status FROM index_generations WHERE generation_id = 'news-successor-stale-contender'"
        ).fetchone() == ("verified",)


def test_successor_build_heartbeats_the_refresh_lease_during_full_snapshot_work(tmp_path):
    """SELECT INVARIANT: a full-corpus rebuild renews its owning refresh lease while building."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    client = _CollectionClient()
    heartbeats = []
    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=tmp_path / "control.db",
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
            embedding_artifact="deterministic-test-model@v1",
            chunk_size=8,
            chunk_overlap=0,
        ),
        dependencies_factory=lambda **kwargs: _dependencies(client),
        generation_token_factory=lambda: "heartbeat-1",
    )

    coordinator(
        SuccessorGenerationBuildRequest(
            run_id="refresh-heartbeat",
            scope_key="scope-heartbeat",
            corpus_id="news",
            articles=tuple(
                GenerationArticleProof(article_id, _sha(content))
                for article_id, content in rows
            ),
            lease_heartbeat=lambda: heartbeats.append("renewed"),
        )
    )

    assert len(heartbeats) >= len(rows) + 2


def test_watchdog_renews_lease_while_dependency_factory_is_blocked_and_stops_after_build(tmp_path):
    """SELECT INVARIANT: dependency/model startup cannot silently outlive the refresh lease."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    client = _CollectionClient()
    heartbeats = []
    dependency_started = threading.Event()

    def slow_dependencies(**kwargs):
        dependency_started.set()
        time.sleep(0.06)
        return _dependencies(client)

    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=tmp_path / "control.db",
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
            embedding_artifact="deterministic-test-model@v1",
            chunk_size=8,
            chunk_overlap=0,
        ),
        dependencies_factory=slow_dependencies,
        generation_token_factory=lambda: "watchdog-blocked",
        lease_heartbeat_interval_seconds=0.01,
    )

    coordinator(
        SuccessorGenerationBuildRequest(
            run_id="refresh-watchdog",
            scope_key="scope-watchdog",
            corpus_id="news",
            articles=tuple(
                GenerationArticleProof(article_id, _sha(content))
                for article_id, content in rows
            ),
            lease_heartbeat=lambda: heartbeats.append(time.monotonic()),
        )
    )

    assert dependency_started.is_set()
    assert len(heartbeats) >= 4
    assert not any(thread.name.startswith("successor-lease-watchdog") for thread in threading.enumerate())


def test_watchdog_lease_renewal_failure_blocks_candidate_builder_after_dependency_stall(tmp_path):
    """SELECT INVARIANT: a lost lease must fail closed before candidate persistence can begin."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    client = _CollectionClient()
    heartbeat_calls = []
    builder_calls = []

    def heartbeat():
        heartbeat_calls.append(time.monotonic())
        if len(heartbeat_calls) >= 2:
            raise RuntimeError("lease owner lost")

    def slow_dependencies(**kwargs):
        time.sleep(0.04)
        return _dependencies(client)

    def builder(*args, **kwargs):
        builder_calls.append((args, kwargs))
        pytest.fail("candidate builder must not run after watchdog lease failure")

    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=tmp_path / "control.db",
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
        ),
        dependencies_factory=slow_dependencies,
        generation_builder=builder,
        lease_heartbeat_interval_seconds=0.01,
    )

    with pytest.raises(SuccessorGenerationCoordinatorError, match="lease heartbeat failed"):
        coordinator(
            SuccessorGenerationBuildRequest(
                run_id="refresh-lease-lost",
                scope_key="scope-lease-lost",
                corpus_id="news",
                articles=tuple(
                    GenerationArticleProof(article_id, _sha(content))
                    for article_id, content in rows
                ),
                lease_heartbeat=heartbeat,
            )
        )

    assert len(heartbeat_calls) >= 2
    assert builder_calls == []
    assert not any(thread.name.startswith("successor-lease-watchdog") for thread in threading.enumerate())


def test_watchdog_lease_loss_invalidates_candidate_written_before_failure_is_observed(tmp_path):
    """SELECT INVARIANT: a late watchdog failure cannot leave a verified candidate activatable."""
    canonical_db, content_root, rows = _canonical_source(tmp_path)
    control_db = tmp_path / "control.db"
    heartbeats = []
    candidate_written = threading.Event()

    def heartbeat():
        heartbeats.append(time.monotonic())
        if candidate_written.is_set():
            raise RuntimeError("lease owner lost")

    def dependencies(**kwargs):
        return SimpleNamespace(collection_client=_CollectionClient(), embedder=lambda chunks: [])

    def writes_verified_candidate(connection, collection_client, **kwargs):
        generation = index_generation.create_generation(
            connection,
            generation_id=kwargs["generation_id"],
            corpus_id=kwargs["corpus_id"],
            collection_name=kwargs["collection_name"],
            embedding_artifact=kwargs["embedding_artifact"],
            chunk_config=kwargs["chunk_config"],
            corpus_snapshot_id=kwargs["corpus_snapshot_id"],
        )
        connection.execute(
            "UPDATE index_generations SET status = 'verified' WHERE generation_id = ?",
            (generation.generation_id,),
        )
        connection.commit()
        candidate_written.set()
        time.sleep(0.04)
        return SimpleNamespace(successful=True, error=None)

    coordinator = RuntimeSuccessorGenerationCoordinator(
        SuccessorGenerationCoordinatorConfig(
            control_db_path=control_db,
            canonical_db_path=canonical_db,
            canonical_content_root=content_root,
            chroma_persist_dir=tmp_path / "chroma",
            activate_verified_generation=True,
        ),
        dependencies_factory=dependencies,
        generation_builder=writes_verified_candidate,
        generation_token_factory=lambda: "late-lease-loss",
        lease_heartbeat_interval_seconds=0.01,
    )

    with pytest.raises(SuccessorGenerationCoordinatorError, match="lease heartbeat failed"):
        coordinator(
            SuccessorGenerationBuildRequest(
                run_id="refresh-late-lease-loss",
                scope_key="scope-late-lease-loss",
                corpus_id="news",
                articles=tuple(
                    GenerationArticleProof(article_id, _sha(content))
                    for article_id, content in rows
                ),
                lease_heartbeat=heartbeat,
            )
        )

    with sqlite3.connect(control_db) as connection:
        assert connection.execute(
            "SELECT status, failure_reason FROM index_generations"
        ).fetchone() == ("failed", "refresh lease heartbeat failed")
        assert connection.execute("SELECT * FROM corpus_index_state").fetchall() == []


def test_rejects_pipeline_article_hash_that_is_not_in_the_frozen_canonical_snapshot_before_opening_chroma(tmp_path):
    """SELECT INVARIANT: accepted refresh evidence cannot be upgraded from an active or stale index hash."""
    canonical_db, content_root, _rows = _canonical_source(tmp_path)
    dependency_calls = []
    config = SuccessorGenerationCoordinatorConfig(
        control_db_path=tmp_path / "control.db",
        canonical_db_path=canonical_db,
        canonical_content_root=content_root,
        chroma_persist_dir=tmp_path / "chroma",
        embedding_artifact="deterministic-test-model@v1",
    )
    coordinator = RuntimeSuccessorGenerationCoordinator(
        config,
        dependencies_factory=lambda **kwargs: dependency_calls.append(kwargs),
    )

    with pytest.raises(SuccessorGenerationCoordinatorError, match="does not match frozen canonical snapshot"):
        coordinator(
            SuccessorGenerationBuildRequest(
                run_id="refresh-2",
                scope_key="scope-2",
                corpus_id="news",
                articles=(GenerationArticleProof(1, "f" * 64),),
            )
        )

    assert dependency_calls == []
    assert not (tmp_path / "control.db").exists()
