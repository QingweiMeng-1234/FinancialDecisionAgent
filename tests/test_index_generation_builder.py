import hashlib
import importlib.util
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "src" / "event_collector" / "index_generation_builder.py"
SPEC = importlib.util.spec_from_file_location("index_generation_builder_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class FakeCollection:
    def __init__(self):
        self.writes = []
        self._readback_mutator = lambda records: records

    def add_chunks(self, chunks):
        self.writes.extend(chunks)

    def read_chunk_metadata(self):
        records = [dict(chunk.metadata) for chunk in self.writes]
        return self._readback_mutator(records)


class FakeClient:
    def __init__(self, collections=None):
        self.collections = dict(collections or {})
        self.deleted = []

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name):
        if collection_name in self.collections:
            raise AssertionError("existing collection was reused")
        collection = FakeCollection()
        self.collections[collection_name] = collection
        return collection


def _database():
    connection = sqlite3.connect(":memory:")
    builder.index_generation.initialize_schema(connection)
    return connection


def _candidate(article_id, content):
    return builder.CanonicalIndexCandidate(
        article_id=article_id,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def _split(text):
    return tuple(text[index:index + 5] for index in range(0, len(text), 5))


def _embed(chunks):
    return tuple((len(chunk),) for chunk in chunks)


def _build(connection, client, generation_id="gen-v2", collection_name="news_v2"):
    candidates = [_candidate(2, "bravo bravo"), _candidate(1, "alpha alpha")]
    snapshot_id = builder.index_generation.compute_eligible_snapshot_fingerprint(
        (candidate.article_id, candidate.content_sha256) for candidate in candidates
    )
    return builder.build_generation(
        connection,
        client,
        generation_id=generation_id,
        corpus_id="news",
        collection_name=collection_name,
        embedding_artifact="deterministic-test-model@v1",
        chunk_config={"chunk_size": 5, "overlap": 0, "splitter": "fixed-test-v1"},
        corpus_snapshot_id=snapshot_id,
        candidates=candidates,
        splitter=_split,
        embedder=_embed,
    )


def test_builds_new_verified_generation_with_required_chunk_metadata_and_keeps_v1_untouched():
    connection = _database()
    v1 = FakeCollection()
    v1.add_chunks((builder.CollectionChunk("legacy", "legacy", {"legacy": True}, None),))
    client = FakeClient({"news_v1": v1})

    result = _build(connection, client)

    assert result.successful is True
    assert result.verification_report and result.verification_report.valid
    assert "news_v1" in client.collections
    assert len(v1.writes) == 1
    assert "news_v2" in client.collections
    assert client.deleted == []
    metadata = [chunk.metadata for chunk in client.collections["news_v2"].writes]
    generation = connection.execute(
        "SELECT status, index_config_fingerprint, corpus_snapshot_id "
        "FROM index_generations WHERE generation_id = 'gen-v2'"
    ).fetchone()
    assert generation[0] == "verified"
    assert connection.execute("SELECT active_generation_id FROM corpus_index_state").fetchone() is None
    assert {item["article_id"] for item in metadata} == {1, 2}
    assert all(item["generation_id"] == "gen-v2" for item in metadata)
    assert all(item["indexed_content_sha256"] for item in metadata)
    assert all(isinstance(item["chunk_index"], int) for item in metadata)
    assert all(item["index_config_fingerprint"] == generation[1] for item in metadata)
    assert all(item["embedding_model"] == "deterministic-test-model@v1" for item in metadata)
    assert all(item["embedding_artifact"] == "deterministic-test-model@v1" for item in metadata)
    assert all(item["corpus_snapshot_id"] == generation[2] for item in metadata)


def test_existing_candidate_collection_is_rejected_and_failed_generation_is_retained():
    connection = _database()
    existing = FakeCollection()
    client = FakeClient({"news_v2": existing})

    with pytest.raises(builder.NewCollectionRequiredError):
        _build(connection, client)

    row = connection.execute(
        "SELECT status, failure_reason FROM index_generations WHERE generation_id = 'gen-v2'"
    ).fetchone()
    assert row[0] == "failed"
    assert "already exists" in row[1]
    assert existing.writes == []
    assert client.deleted == []


@pytest.mark.parametrize(
    ("name", "mutator"),
    [
        ("orphan", lambda records: records + [dict(records[0], article_id=99)]),
        ("hash", lambda records: [dict(record, indexed_content_sha256="wrong") for record in records]),
        ("config", lambda records: [dict(record, index_config_fingerprint="wrong") for record in records]),
        ("snapshot", lambda records: [dict(record, corpus_snapshot_id="wrong") for record in records]),
        ("artifact", lambda records: [dict(record, embedding_artifact="wrong") for record in records]),
        ("missing", lambda records: records[1:]),
        ("duplicate", lambda records: records + [dict(records[0])]),
    ],
)
def test_readback_reconciliation_fails_for_orphan_hash_config_missing_and_duplicate_chunks(name, mutator):
    connection = _database()
    client = FakeClient()
    original_create = client.create_collection

    def create_with_corruption(collection_name):
        collection = original_create(collection_name)
        collection._readback_mutator = mutator
        return collection

    client.create_collection = create_with_corruption
    result = _build(connection, client, generation_id=f"bad-{name}", collection_name=f"news_{name}")

    assert result.successful is False
    if name in {"snapshot", "artifact"}:
        assert result.chunk_report is None
        assert result.error
    else:
        assert result.chunk_report is not None
        assert result.chunk_report.valid is False
    assert result.verification_report is None
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = ?", (f"bad-{name}",)
    ).fetchone()[0] == "failed"
    assert connection.execute("SELECT active_generation_id FROM corpus_index_state").fetchone() is None
    assert client.deleted == []


def test_candidate_hash_failure_is_retained_without_creating_or_deleting_a_collection():
    connection = _database()
    client = FakeClient()
    candidate = replace(_candidate(1, "alpha"), content_sha256="bad")

    result = builder.build_generation(
        connection,
        client,
        generation_id="bad-candidate",
        corpus_id="news",
        collection_name="news_bad_candidate",
        embedding_artifact="deterministic-test-model@v1",
        chunk_config={"chunk_size": 5},
        corpus_snapshot_id="frozen-snapshot-v2",
        candidates=[candidate],
        splitter=_split,
        embedder=_embed,
    )

    assert result.successful is False
    assert "does not match" in result.error
    assert client.collections == {}
    assert client.deleted == []
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'bad-candidate'"
    ).fetchone()[0] == "failed"


def test_control_plane_progress_is_durable_before_and_between_external_writes(tmp_path):
    database_path = tmp_path / "generation-control.db"
    connection = sqlite3.connect(database_path)
    builder.index_generation.initialize_schema(connection)
    observer = sqlite3.connect(database_path)

    class DurabilityCheckingCollection(FakeCollection):
        def add_chunks(self, chunks):
            if self.writes:
                first_manifest = observer.execute(
                    "SELECT article_id FROM article_index_manifest "
                    "WHERE generation_id = 'gen-v2' ORDER BY article_id LIMIT 1"
                ).fetchone()
                assert first_manifest == (1,)
            super().add_chunks(chunks)

    class DurabilityCheckingClient(FakeClient):
        def collection_exists(self, collection_name):
            generation = observer.execute(
                "SELECT status FROM index_generations WHERE generation_id = 'gen-v2'"
            ).fetchone()
            assert generation == ("building",)
            return super().collection_exists(collection_name)

        def create_collection(self, collection_name):
            collection = DurabilityCheckingCollection()
            self.collections[collection_name] = collection
            return collection

    result = _build(connection, DurabilityCheckingClient())

    assert result.successful is True


def test_generation_rejects_a_snapshot_identity_not_bound_to_the_complete_candidate_set():
    connection = _database()
    client = FakeClient()
    candidate = _candidate(1, "canonical alpha")

    result = builder.build_generation(
        connection,
        client,
        generation_id="unbound-snapshot",
        corpus_id="news",
        collection_name="news_unbound_snapshot",
        embedding_artifact="deterministic-test-model@v1",
        chunk_config={"chunk_size": 5},
        corpus_snapshot_id="caller-asserted-but-unbound",
        candidates=[candidate],
        splitter=_split,
        embedder=_embed,
    )

    assert result.successful is False
    assert "candidate set" in (result.error or "")
    assert client.collections == {}
    assert connection.execute(
        "SELECT status FROM index_generations WHERE generation_id = 'unbound-snapshot'"
    ).fetchone()[0] == "failed"
