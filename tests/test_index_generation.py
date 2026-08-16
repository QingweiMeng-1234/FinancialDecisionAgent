import importlib.util
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "src" / "event_collector" / "index_generation.py"
SPEC = importlib.util.spec_from_file_location("index_generation_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
index_generation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = index_generation
SPEC.loader.exec_module(index_generation)


def test_index_config_fingerprint_is_public_deterministic_and_order_independent():
    first = index_generation.compute_index_config_fingerprint(
        {"chunk_size": 1000, "chunk_overlap": 200, "splitter": "recursive-character-v1"}
    )
    second = index_generation.compute_index_config_fingerprint(
        {"splitter": "recursive-character-v1", "chunk_overlap": 200, "chunk_size": 1000}
    )

    assert first == second
    assert len(first) == 64


def _database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    index_generation.initialize_schema(connection)
    return connection


def _build_verified_generation(connection, generation_id="gen-1", corpus_id="news"):
    generation = index_generation.create_generation(
        connection,
        generation_id=generation_id,
        corpus_id=corpus_id,
        collection_name=f"news_articles_{generation_id}",
        embedding_artifact="text-embedding-3-large@2026-08",
        chunk_config={"version": "v2", "chunk_size": 500, "overlap": 50},
        corpus_snapshot_id="snapshot-1",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id=generation_id,
        article_id=1,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=2,
        actual_chunk_count=2,
    )
    index_generation.record_article_manifest(
        connection,
        generation_id=generation_id,
        article_id=2,
        indexed_content_sha256="b" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )
    proof = index_generation.reconcile_generation_chunks(
        connection,
        generation_id,
        [
            index_generation.IndexedChunkRecord(generation_id, 1, "a" * 64, 0, generation.index_config_fingerprint),
            index_generation.IndexedChunkRecord(generation_id, 1, "a" * 64, 1, generation.index_config_fingerprint),
            index_generation.IndexedChunkRecord(generation_id, 2, "b" * 64, 0, generation.index_config_fingerprint),
        ],
    )
    assert proof.valid
    report = index_generation.verify_generation(
        connection,
        generation_id,
        {1: "a" * 64, 2: "b" * 64},
    )
    assert report.valid


def test_verify_requires_exact_bidirectional_eligible_coverage_hash_counts_and_config():
    connection = _database()
    generation = index_generation.create_generation(
        connection,
        generation_id="bad",
        corpus_id="news",
        collection_name="news_articles_bad",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 500, "overlap": 50},
        corpus_snapshot_id="snapshot-bad",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id="bad",
        article_id=1,
        indexed_content_sha256="wrong-hash",
        expected_chunk_count=2,
        actual_chunk_count=0,
        index_config_fingerprint="wrong-config",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id="bad",
        article_id=3,
        indexed_content_sha256="c" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )

    report = index_generation.verify_generation(
        connection,
        "bad",
        {1: "a" * 64, 2: "b" * 64},
    )

    assert report.valid is False
    assert report.missing_article_ids == (2,)
    assert report.unexpected_article_ids == (3,)
    assert report.hash_mismatch_article_ids == (1,)
    assert report.chunk_count_mismatch_article_ids == (1,)
    assert report.non_positive_chunk_article_ids == (1,)
    assert report.config_mismatch_article_ids == (1,)
    assert report.chunk_proof_missing is True
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'bad'"
    ).fetchone()[0] == "failed"


def test_first_verified_generation_activation_compares_against_an_empty_pointer():
    """SELECT INVARIANT: only a contender that observed no active generation may perform first activation."""
    connection = _database()
    _build_verified_generation(connection, "gen-1")
    _build_verified_generation(connection, "gen-2")

    index_generation.activate_generation(
        connection,
        "gen-1",
        expected_current_generation_id=None,
    )

    with pytest.raises(
        index_generation.GenerationStateError,
        match="active generation changed concurrently",
    ):
        index_generation.activate_generation(
            connection,
            "gen-2",
            expected_current_generation_id=None,
        )

    assert index_generation.read_active_generation(connection, "news").generation_id == "gen-1"
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'gen-2'"
    ).fetchone()[0] == "verified"


def test_verified_generation_activates_atomically_and_rejects_a_stale_existing_pointer():
    """SELECT INVARIANT: a stale successor cannot replace a concurrently activated successor."""
    connection = _database()
    _build_verified_generation(connection, "gen-1")
    _build_verified_generation(connection, "gen-2")
    _build_verified_generation(connection, "gen-3")
    index_generation.activate_generation(
        connection,
        "gen-1",
        expected_current_generation_id=None,
    )
    index_generation.activate_generation(
        connection,
        "gen-2",
        expected_current_generation_id="gen-1",
    )

    with pytest.raises(
        index_generation.GenerationStateError,
        match="active generation changed concurrently",
    ):
        index_generation.activate_generation(
            connection,
            "gen-3",
            expected_current_generation_id="gen-1",
        )

    active = index_generation.read_active_generation(connection, "news")
    assert active is not None
    assert active.generation_id == "gen-2"
    assert active.collection_name == "news_articles_gen-2"
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'gen-1'"
    ).fetchone()[0] == "verified"
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'gen-2'"
    ).fetchone()[0] == "active"
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'gen-3'"
    ).fetchone()[0] == "verified"


def test_concurrent_verified_successors_allow_exactly_one_compare_and_swap(tmp_path):
    """Two contenders with the same observation cannot both replace the active pointer."""
    database_path = tmp_path / "control.db"
    with sqlite3.connect(database_path) as connection:
        index_generation.initialize_schema(connection)
        for generation_id in ("gen-1", "gen-2", "gen-3"):
            _build_verified_generation(connection, generation_id)
        index_generation.activate_generation(
            connection,
            "gen-1",
            expected_current_generation_id=None,
        )

    ready = threading.Barrier(2)

    def contend(generation_id):
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            ready.wait()
            index_generation.activate_generation(
                connection,
                generation_id,
                expected_current_generation_id="gen-1",
            )
        except index_generation.GenerationStateError:
            return generation_id, "stale"
        finally:
            connection.close()
        return generation_id, "active"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = dict(executor.map(contend, ("gen-2", "gen-3")))

    assert sorted(outcomes.values()) == ["active", "stale"]
    winner = next(
        generation_id for generation_id, outcome in outcomes.items() if outcome == "active"
    )
    loser = next(
        generation_id for generation_id, outcome in outcomes.items() if outcome == "stale"
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT active_generation_id FROM corpus_index_state WHERE corpus_id = 'news'"
        ).fetchone() == (winner,)
        assert connection.execute(
            "SELECT status FROM index_generations WHERE generation_id = ?", (winner,)
        ).fetchone() == ("active",)
        assert connection.execute(
            "SELECT status FROM index_generations WHERE generation_id = ?", (loser,)
        ).fetchone() == ("verified",)


def test_valid_manifest_without_chunk_proof_cannot_verify_or_activate():
    connection = _database()
    index_generation.create_generation(
        connection,
        generation_id="no-proof",
        corpus_id="news",
        collection_name="news_no_proof",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 100},
        corpus_snapshot_id="snapshot-no-proof",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id="no-proof",
        article_id=1,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )

    report = index_generation.verify_generation(connection, "no-proof", {1: "a" * 64})

    assert report.valid is False
    assert report.chunk_proof_missing is True
    with pytest.raises(index_generation.GenerationStateError):
        index_generation.activate_generation(
            connection,
            "no-proof",
            expected_current_generation_id=None,
        )


def test_only_verified_generation_can_be_activated_and_corpus_cannot_be_crossed():
    connection = _database()
    index_generation.create_generation(
        connection,
        generation_id="building",
        corpus_id="other",
        collection_name="other_collection",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 100},
        corpus_snapshot_id="snapshot-building",
    )
    connection.commit()

    with pytest.raises(index_generation.GenerationStateError):
        index_generation.activate_generation(
            connection,
            "building",
            expected_current_generation_id=None,
        )
    with pytest.raises(index_generation.GenerationStateError):
        index_generation.activate_generation(
            connection,
            "missing",
            expected_current_generation_id=None,
        )


def test_schema_setup_supports_a_plain_sqlite_connection():
    connection = sqlite3.connect(":memory:")
    index_generation.initialize_schema(connection)
    generation = index_generation.create_generation(
        connection,
        generation_id="plain",
        corpus_id="news",
        collection_name="news_plain",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 100},
        corpus_snapshot_id="snapshot-plain",
    )

    index_generation.record_article_manifest(
        connection,
        generation_id="plain",
        article_id=1,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )
    assert index_generation.reconcile_generation_chunks(
        connection,
        "plain",
        [index_generation.IndexedChunkRecord("plain", 1, "a" * 64, 0, generation.index_config_fingerprint)],
    ).valid
    assert index_generation.verify_generation(connection, "plain", {1: "a" * 64}).valid


def test_chunk_reconciliation_rejects_orphans_wrong_identity_and_non_contiguous_indexes():
    connection = _database()
    generation = index_generation.create_generation(
        connection,
        generation_id="chunks-bad",
        corpus_id="news",
        collection_name="news_chunks_bad",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 100},
        corpus_snapshot_id="snapshot-chunks-bad",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id="chunks-bad",
        article_id=1,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=2,
        actual_chunk_count=2,
    )

    report = index_generation.reconcile_generation_chunks(
        connection,
        "chunks-bad",
        [
            index_generation.IndexedChunkRecord("wrong-generation", 1, "a" * 64, 0, generation.index_config_fingerprint),
            index_generation.IndexedChunkRecord("chunks-bad", 99, "z" * 64, 0, generation.index_config_fingerprint),
            index_generation.IndexedChunkRecord("chunks-bad", 1, "wrong-hash", 0, generation.index_config_fingerprint),
            index_generation.IndexedChunkRecord(
                "chunks-bad", 1, "a" * 64, 0, index_config_fingerprint="wrong-config"
            ),
        ],
    )

    assert report.valid is False
    assert report.wrong_generation_chunk_count == 1
    assert report.orphan_article_ids == (99,)
    assert report.hash_mismatch_article_ids == (1,)
    assert report.config_mismatch_article_ids == (1,)
    assert report.duplicate_chunk_indices == ((1, 0),)
    assert report.missing_chunk_indices == ((1, 1),)
    assert connection.execute(
        "SELECT status FROM generation_chunk_verification WHERE generation_id = 'chunks-bad'"
    ).fetchone()[0] == "failed"
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'chunks-bad'"
    ).fetchone()[0] == "failed"


def test_verify_fails_closed_when_its_optimistic_state_transition_loses_a_race():
    class LostTransitionConnection(sqlite3.Connection):
        lose_verified_transition = False

        def execute(self, sql, parameters=()):
            normalized = " ".join(str(sql).split())
            if self.lose_verified_transition and normalized.startswith(
                "UPDATE index_generations SET status = ?, verified_at = ?"
            ):
                self.lose_verified_transition = False
                return super().execute(
                    "UPDATE index_generations SET status = status WHERE 0"
                )
            return super().execute(sql, parameters)

    connection = sqlite3.connect(":memory:", factory=LostTransitionConnection)
    index_generation.initialize_schema(connection)
    generation = index_generation.create_generation(
        connection,
        generation_id="verify-race",
        corpus_id="news",
        collection_name="news_verify_race",
        embedding_artifact="model@1",
        chunk_config={"chunk_size": 100},
        corpus_snapshot_id="snapshot-race",
    )
    index_generation.record_article_manifest(
        connection,
        generation_id="verify-race",
        article_id=1,
        indexed_content_sha256="a" * 64,
        expected_chunk_count=1,
        actual_chunk_count=1,
    )
    assert index_generation.reconcile_generation_chunks(
        connection,
        "verify-race",
        [index_generation.IndexedChunkRecord(
            "verify-race", 1, "a" * 64, 0, generation.index_config_fingerprint
        )],
    ).valid
    connection.lose_verified_transition = True

    with pytest.raises(index_generation.GenerationStateError, match="changed concurrently"):
        index_generation.verify_generation(connection, "verify-race", {1: "a" * 64})
