import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from event_collector import active_generation_reader
from event_collector import index_generation


def _build_generation_database(tmp_path, *, activate=True):
    database_path = tmp_path / "generation-control.sqlite3"
    connection = sqlite3.connect(database_path)
    index_generation.initialize_schema(connection)
    generation = index_generation.create_generation(
        connection,
        generation_id="news-generation-v2",
        corpus_id="news",
        collection_name="news_articles_v2",
        embedding_artifact="all-MiniLM-L6-v2",
        chunk_config={
            "chunk_size": 1000,
            "splitter": {"name": "fixed-character-v1", "separators": ["\n", " "]},
        },
        corpus_snapshot_id="snapshot-sha256",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id=generation.generation_id,
        article_id=7,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=2,
        actual_chunk_count=2,
    )
    index_generation.record_article_manifest(
        connection,
        generation_id=generation.generation_id,
        article_id=11,
        indexed_content_sha256="b" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )
    index_generation.reconcile_generation_chunks(
        connection,
        generation.generation_id,
        [
            index_generation.IndexedChunkRecord(
                generation.generation_id, 7, "a" * 64, 0, generation.index_config_fingerprint
            ),
            index_generation.IndexedChunkRecord(
                generation.generation_id, 7, "a" * 64, 1, generation.index_config_fingerprint
            ),
            index_generation.IndexedChunkRecord(
                generation.generation_id, 11, "b" * 64, 0, generation.index_config_fingerprint
            ),
        ],
    )
    assert index_generation.verify_generation(
        connection,
        generation.generation_id,
        {7: "a" * 64, 11: "b" * 64},
    ).valid
    if activate:
        index_generation.activate_generation(
            connection,
            generation.generation_id,
            expected_current_generation_id=None,
        )
    connection.commit()
    connection.close()
    return database_path, generation


def test_serving_resolver_opens_control_db_read_only_and_fails_closed_without_pointer(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "generation-control.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE corpus_index_state (
            corpus_id TEXT PRIMARY KEY,
            active_generation_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE index_generations (
            generation_id TEXT PRIMARY KEY,
            corpus_id TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            embedding_artifact TEXT NOT NULL,
            index_config_json TEXT NOT NULL,
            index_config_fingerprint TEXT NOT NULL,
            corpus_snapshot_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE generation_chunk_verification (
            generation_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            observed_chunk_count INTEGER NOT NULL,
            issue_count INTEGER NOT NULL
        );
        CREATE TABLE article_index_manifest (
            generation_id TEXT NOT NULL,
            article_id INTEGER NOT NULL,
            indexed_content_sha256 TEXT NOT NULL,
            expected_chunk_count INTEGER NOT NULL,
            actual_chunk_count INTEGER NOT NULL,
            index_config_fingerprint TEXT NOT NULL,
            index_status TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );
        """
    )
    connection.close()

    connect_calls = []
    statements = []
    original_connect = sqlite3.connect

    class RecordingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            statements.append(sql)
            return super().execute(sql, parameters)

    def recording_connect(*args, **kwargs):
        connect_calls.append((args, dict(kwargs)))
        kwargs["factory"] = RecordingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(active_generation_reader.sqlite3, "connect", recording_connect)

    resolver = active_generation_reader.ActiveGenerationResolver(database_path)
    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        resolver.resolve("news")

    assert caught.value.code == active_generation_reader.MISSING_POINTER
    assert connect_calls[0][1]["uri"] is True
    assert "mode=ro" in connect_calls[0][0][0]
    assert any(statement.strip().upper() == "PRAGMA QUERY_ONLY = ON" for statement in statements)


def test_serving_resolver_returns_a_deeply_immutable_active_snapshot(tmp_path):
    database_path, generation = _build_generation_database(tmp_path)

    snapshot = active_generation_reader.ActiveGenerationResolver(database_path).resolve("news")

    assert snapshot == active_generation_reader.ActiveGenerationSnapshot(
        generation_id="news-generation-v2",
        corpus_id="news",
        collection_name="news_articles_v2",
        corpus_snapshot_id="snapshot-sha256",
        embedding_artifact="all-MiniLM-L6-v2",
        index_config=snapshot.index_config,
        index_config_fingerprint=generation.index_config_fingerprint,
        observed_chunk_count=3,
        manifest=snapshot.manifest,
        status="active",
    )
    assert [entry.article_id for entry in snapshot.manifest] == [7, 11]
    assert sum(entry.actual_chunk_count for entry in snapshot.manifest) == 3
    with pytest.raises(FrozenInstanceError):
        snapshot.collection_name = "other"
    with pytest.raises(TypeError):
        snapshot.index_config["chunk_size"] = 10
    with pytest.raises(TypeError):
        snapshot.index_config["splitter"]["name"] = "other"
    with pytest.raises(TypeError):
        snapshot.index_config["splitter"]["separators"][0] = "x"


def test_serving_resolver_rejects_a_pointed_verified_but_not_active_generation(tmp_path):
    database_path, generation = _build_generation_database(tmp_path, activate=False)
    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO corpus_index_state (corpus_id, active_generation_id, updated_at) "
        "VALUES (?, ?, ?)",
        ("news", generation.generation_id, "2026-08-15T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(database_path).resolve("news")

    assert caught.value.code == active_generation_reader.VERIFIED_NOT_ACTIVE


def test_serving_resolver_rejects_a_cross_corpus_pointer(tmp_path):
    database_path, _ = _build_generation_database(tmp_path)
    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE index_generations SET corpus_id = ? WHERE generation_id = ?",
        ("other-corpus", "news-generation-v2"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(database_path).resolve("news")

    assert caught.value.code == active_generation_reader.POINTER_MISMATCH


@pytest.mark.parametrize(
    "corrupt_sql",
    [
        "DELETE FROM generation_chunk_verification",
        "UPDATE generation_chunk_verification SET status = 'failed'",
        "UPDATE generation_chunk_verification SET issue_count = 1",
    ],
)
def test_serving_resolver_rejects_missing_or_invalid_chunk_proof(tmp_path, corrupt_sql):
    database_path, _ = _build_generation_database(tmp_path)
    connection = sqlite3.connect(database_path)
    connection.execute(corrupt_sql)
    connection.commit()
    connection.close()

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(database_path).resolve("news")

    assert caught.value.code == active_generation_reader.PROOF_INVALID


@pytest.mark.parametrize(
    "corrupt_sql",
    [
        "UPDATE article_index_manifest SET index_status = 'failed' WHERE article_id = 7",
        "UPDATE article_index_manifest SET expected_chunk_count = 0, actual_chunk_count = 0 "
        "WHERE article_id = 7",
        "UPDATE article_index_manifest SET actual_chunk_count = 1 WHERE article_id = 7",
        "UPDATE article_index_manifest SET index_config_fingerprint = 'wrong' WHERE article_id = 7",
        "UPDATE generation_chunk_verification SET observed_chunk_count = 4",
        "UPDATE index_generations SET index_config_json = '{\"chunk_size\":999}'",
    ],
)
def test_serving_resolver_rejects_manifest_or_config_corruption(tmp_path, corrupt_sql):
    database_path, _ = _build_generation_database(tmp_path)
    connection = sqlite3.connect(database_path)
    connection.execute(corrupt_sql)
    connection.commit()
    connection.close()

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(database_path).resolve("news")

    assert caught.value.code == active_generation_reader.MANIFEST_INVALID


@pytest.mark.parametrize(
    ("activate", "expected_status"),
    [(False, "verified"), (True, "active")],
)
def test_operator_can_open_a_verified_or_active_candidate(
    tmp_path, activate, expected_status
):
    database_path, generation = _build_generation_database(tmp_path, activate=activate)

    snapshot = active_generation_reader.ActiveGenerationResolver(
        database_path
    ).open_verified_candidate(generation.generation_id)

    assert snapshot.generation_id == generation.generation_id
    assert snapshot.status == expected_status
    assert snapshot.observed_chunk_count == 3


@pytest.mark.parametrize("generation_status", ["building", "failed"])
def test_operator_rejects_a_candidate_that_is_not_verified(tmp_path, generation_status):
    database_path, generation = _build_generation_database(tmp_path, activate=False)
    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE index_generations SET status = ? WHERE generation_id = ?",
        (generation_status, generation.generation_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(
            database_path
        ).open_verified_candidate(generation.generation_id)

    assert caught.value.code == active_generation_reader.CANDIDATE_NOT_VERIFIED


def test_operator_rejects_an_unknown_candidate(tmp_path):
    database_path, _ = _build_generation_database(tmp_path, activate=False)

    with pytest.raises(active_generation_reader.ActiveGenerationResolutionError) as caught:
        active_generation_reader.ActiveGenerationResolver(
            database_path
        ).open_verified_candidate("does-not-exist")

    assert caught.value.code == active_generation_reader.CANDIDATE_NOT_FOUND


def test_serving_resolution_never_uses_operator_candidate_path(tmp_path, monkeypatch):
    database_path, _ = _build_generation_database(tmp_path)
    resolver = active_generation_reader.ActiveGenerationResolver(database_path)

    def forbidden_operator_path(_generation_id):
        raise AssertionError("serving resolution used the operator-only candidate path")

    monkeypatch.setattr(resolver, "open_verified_candidate", forbidden_operator_path)

    assert resolver.resolve("news").status == "active"
