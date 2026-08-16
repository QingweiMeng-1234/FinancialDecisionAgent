from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time

import pytest

from event_collector.active_generation_reader import (
    ActiveGenerationSnapshot,
    ActiveManifestEntry,
)
from event_collector.generation_eligibility import (
    EligibleGenerationArticle,
    EligibilitySnapshot,
)
from event_collector.chroma_generation_reader import (
    ChromaGenerationReader,
    GenerationReaderContractError,
)


HASH_1 = "1" * 64
HASH_2 = "2" * 64
CONFIG_FINGERPRINT = "f" * 64


def _active_generation(*, observed_chunk_count: int = 2) -> ActiveGenerationSnapshot:
    manifest = (
        ActiveManifestEntry("gen-1", 1, HASH_1, 1, 1, CONFIG_FINGERPRINT, "indexed", "now"),
        ActiveManifestEntry("gen-1", 2, HASH_2, 1, 1, CONFIG_FINGERPRINT, "indexed", "now"),
    )
    return ActiveGenerationSnapshot(
        generation_id="gen-1",
        corpus_id="news",
        collection_name="news_gen_1",
        corpus_snapshot_id="snapshot-1",
        embedding_artifact="model-1",
        index_config={"chunk_overlap": 20, "chunk_size": 100, "splitter": "fixed-character-v1"},
        index_config_fingerprint=CONFIG_FINGERPRINT,
        observed_chunk_count=observed_chunk_count,
        manifest=manifest,
        status="active",
    )


def _article(article_id: int, digest: str) -> EligibleGenerationArticle:
    return EligibleGenerationArticle(
        article_id=article_id,
        indexed_content_sha256=digest,
        content=f"canonical article {article_id}",
        title=f"SQLite title {article_id}",
        description=f"SQLite description {article_id}",
        url=f"https://legacy.example/{article_id}",
        canonical_url=f"https://canonical.example/{article_id}",
        original_url=f"https://original.example/{article_id}",
        normalized_url=f"https://normalized.example/{article_id}",
        summary=f"SQLite summary {article_id}",
        source="news",
        published_at="2026-08-15T00:00:00+00:00",
        source_published_at="2026-08-15T00:00:00+00:00",
        published_at_provenance="source_metadata",
        publisher_source_id=f"publisher-{article_id}",
        publisher_source_name=f"Publisher {article_id}",
        story_group_id=article_id + 100,
        story_dedupe_method="canonical-url",
        content_validation_status="verified",
        content_validation_reason="full_article",
        content_validator_version="v1",
        final_response_url=f"https://final.example/{article_id}",
        response_status_code=200,
        response_content_type="text/html",
        extractor_version="extractor-v1",
        extracted_char_count=1234,
        fetched_at="2026-08-15T01:00:00+00:00",
        raw_json='{"provider":"news"}',
    )


def _eligibility(*, articles=None, active=None) -> EligibilitySnapshot:
    active = active or _active_generation()
    return EligibilitySnapshot(
        active_generation=active,
        generation_id=active.generation_id,
        corpus_id=active.corpus_id,
        collection_name=active.collection_name,
        corpus_snapshot_id=active.corpus_snapshot_id,
        embedding_artifact=active.embedding_artifact,
        index_config_fingerprint=active.index_config_fingerprint,
        articles=tuple(articles or (_article(1, HASH_1), _article(2, HASH_2))),
        exclusions=(),
    )


def _collection_metadata(active=None):
    active = active or _active_generation()
    config_json = json.dumps(dict(active.index_config), sort_keys=True, separators=(",", ":"))
    return {
        "hnsw:space": "cosine",
        "generation_id": active.generation_id,
        "corpus_snapshot_id": active.corpus_snapshot_id,
        "embedding_artifact": active.embedding_artifact,
        "index_config_fingerprint": active.index_config_fingerprint,
        "chunk_config_json": config_json,
        **dict(active.index_config),
    }


class FakeCollection:
    def __init__(self, *, metadata=None, count=2, payload=None):
        self.metadata = metadata or _collection_metadata()
        self._count = count
        self.payload = payload or {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}
        self.query_calls = []

    def count(self):
        return self._count

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self.payload


class FakeClient:
    def __init__(self, collection):
        self.collection = collection
        self.get_calls = []

    def get_collection(self, *, name):
        self.get_calls.append(name)
        return self.collection

    def __getattr__(self, name):
        if name in {"create_collection", "get_or_create_collection", "delete_collection"}:
            raise AssertionError(f"reader called forbidden client method: {name}")
        raise AttributeError(name)


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, text):
        self.calls.append(text)
        return [0.1, 0.2]


def test_opens_exact_collection_read_only_and_validates_metadata_and_count_before_loading_embedder():
    collection = FakeCollection()
    client = FakeClient(collection)
    events = []

    reader = ChromaGenerationReader(
        _eligibility(),
        client=client,
        embedder_factory=lambda artifact: events.append(("embedder", artifact)) or FakeEmbedder(),
    )

    assert client.get_calls == ["news_gen_1"]
    assert events == []
    assert reader.search("question", allowed_article_ids=set()) == []
    assert events == []
    assert collection.query_calls == []
    assert not hasattr(reader, "add_article")
    assert not hasattr(reader, "delete_article")


def test_rejects_an_eligibility_snapshot_not_bound_to_its_active_generation_before_chroma_open():
    client = FakeClient(FakeCollection())
    inconsistent = replace(_eligibility(), generation_id="other")

    with pytest.raises(GenerationReaderContractError, match="eligibility snapshot"):
        ChromaGenerationReader(inconsistent, client=client, embedder=FakeEmbedder())

    assert client.get_calls == []


@pytest.mark.parametrize(
    ("metadata_patch", "count", "message"),
    [
        ({"generation_id": "other"}, 2, "generation_id"),
        ({"corpus_snapshot_id": "other"}, 2, "corpus_snapshot_id"),
        ({"embedding_artifact": "other"}, 2, "embedding_artifact"),
        ({"index_config_fingerprint": "other"}, 2, "index_config_fingerprint"),
        ({"hnsw:space": "l2"}, 2, "hnsw:space"),
        ({"chunk_config_json": "{}"}, 2, "chunk_config_json"),
        ({"chunk_size": 999}, 2, "chunk_size"),
        ({}, 1, "count"),
    ],
)
def test_rejects_collection_contract_mismatch_before_embedder_load(metadata_patch, count, message):
    metadata = _collection_metadata()
    metadata.update(metadata_patch)
    events = []
    with pytest.raises(GenerationReaderContractError, match=message):
        ChromaGenerationReader(
            _eligibility(),
            client=FakeClient(FakeCollection(metadata=metadata, count=count)),
            embedder_factory=lambda artifact: events.append(artifact) or FakeEmbedder(),
        )
    assert events == []


def _chunk_metadata(article_id: int, digest: str, chunk_index: int = 0):
    return {
        "generation_id": "gen-1",
        "corpus_snapshot_id": "snapshot-1",
        "article_id": article_id,
        "indexed_content_sha256": digest,
        "chunk_index": chunk_index,
        "index_config_fingerprint": CONFIG_FINGERPRINT,
        "embedding_model": "model-1",
        "embedding_artifact": "model-1",
        # Untrusted display metadata must never win over SQLite hydration.
        "title": "CHROMA TITLE",
        "url": "https://untrusted.example",
        "summary": "CHROMA SUMMARY",
        "source": "untrusted",
        "published_at": "1900-01-01",
        "story_group_id": 9999,
    }


def test_search_intersects_allowed_ids_validates_chunks_and_hydrates_only_from_snapshot():
    payload = {
        "ids": [["gen-1:2:0"]],
        "metadatas": [[_chunk_metadata(2, HASH_2)]],
        "documents": [["indexed chunk two"]],
        "distances": [[0.25]],
    }
    collection = FakeCollection(payload=payload)
    embedder = FakeEmbedder()
    reader = ChromaGenerationReader(
        _eligibility(), client=FakeClient(collection), embedder=embedder
    )

    results = reader.search("question", top_k=3, allowed_article_ids={2, 999})

    assert embedder.calls == ["question"]
    assert collection.query_calls == [
        {
            "query_embeddings": [[0.1, 0.2]],
            "n_results": 6,
            "where": {"article_id": {"$in": [2]}},
            "include": ["metadatas", "documents", "distances"],
        }
    ]
    assert len(results) == 1
    result = results[0]
    assert result["article_id"] == 2
    assert result["title"] == "SQLite title 2"
    assert result["url"] == "https://canonical.example/2"
    assert result["summary"] == "SQLite summary 2"
    assert result["source"] == "news"
    assert result["published_at"] == "2026-08-15T00:00:00+00:00"
    assert result["story_group_id"] == 102
    assert result["publisher_source_id"] == "publisher-2"
    assert result["content_validation_reason"] == "full_article"
    assert result["final_response_url"] == "https://final.example/2"
    assert result["generation_id"] == "gen-1"
    assert result["corpus_snapshot_id"] == "snapshot-1"
    assert result["content"] == "indexed chunk two"


def test_time_window_intersects_pinned_article_metadata_before_chroma_query():
    articles = (
        replace(
            _article(1, HASH_1),
            published_at="2026-08-01T00:00:00+00:00",
            source_published_at="2026-08-01T00:00:00+00:00",
        ),
        replace(
            _article(2, HASH_2),
            published_at="2026-08-15T12:00:00+00:00",
            source_published_at="2026-08-15T12:00:00+00:00",
        ),
    )
    collection = FakeCollection(
        payload={
            "ids": [["gen-1:2:0"]],
            "metadatas": [[_chunk_metadata(2, HASH_2)]],
            "documents": [["fresh chunk"]],
            "distances": [[0.1]],
        }
    )
    reader = ChromaGenerationReader(
        _eligibility(articles=articles), client=FakeClient(collection), embedder=FakeEmbedder()
    )

    results = reader.search(
        "question",
        start_at="2026-08-15T00:00:00+00:00",
        end_at="2026-08-16T00:00:00+00:00",
    )

    assert [result["article_id"] for result in results] == [2]
    assert collection.query_calls[0]["where"] == {"article_id": {"$in": [2]}}


def test_latest_lookback_window_excludes_unknown_or_naive_dates_without_querying_them():
    articles = (
        replace(
            _article(1, HASH_1),
            published_at="2026-08-01T00:00:00+00:00",
            source_published_at="2026-08-01T00:00:00+00:00",
        ),
        replace(
            _article(2, HASH_2),
            published_at=None,
            source_published_at=None,
            published_at_provenance="unknown",
        ),
    )
    collection = FakeCollection()
    embedder = FakeEmbedder()
    reader = ChromaGenerationReader(
        _eligibility(articles=articles), client=FakeClient(collection), embedder=embedder
    )

    results = reader.search(
        "question",
        latest_at="2026-08-16T00:00:00+00:00",
        lookback_days=1,
    )

    assert results == []
    assert embedder.calls == []
    assert collection.query_calls == []


def test_time_window_excludes_unknown_provenance_even_when_legacy_published_at_is_recent():
    articles = (
        replace(
            _article(1, HASH_1),
            published_at="2026-08-15T12:00:00+00:00",
            source_published_at=None,
            published_at_provenance="unknown",
        ),
    )
    collection = FakeCollection()
    embedder = FakeEmbedder()
    reader = ChromaGenerationReader(
        _eligibility(articles=articles), client=FakeClient(collection), embedder=embedder
    )

    results = reader.search(
        "question",
        start_at="2026-08-15T00:00:00+00:00",
        end_at="2026-08-16T00:00:00+00:00",
    )

    assert results == []
    assert embedder.calls == []
    assert collection.query_calls == []


def test_time_window_uses_verified_source_date_instead_of_compatibility_published_at():
    """SELECT INVARIANT: latest filtering is bound to the evidenced publisher date."""
    articles = (
        replace(
            _article(1, HASH_1),
            published_at="2026-08-15T12:00:00+00:00",
            source_published_at="2026-05-20T16:37:39+00:00",
            published_at_provenance="publisher_metadata",
        ),
    )
    collection = FakeCollection()
    embedder = FakeEmbedder()
    reader = ChromaGenerationReader(
        _eligibility(articles=articles), client=FakeClient(collection), embedder=embedder
    )

    results = reader.search(
        "question",
        start_at="2026-08-15T00:00:00+00:00",
        end_at="2026-08-16T00:00:00+00:00",
    )

    assert results == []
    assert embedder.calls == []
    assert collection.query_calls == []


def test_rejects_mixed_or_naive_time_window_boundaries_before_embedder_load():
    embedder = FakeEmbedder()
    reader = ChromaGenerationReader(
        _eligibility(), client=FakeClient(FakeCollection()), embedder=embedder
    )

    with pytest.raises(ValueError, match="UTC-aware"):
        reader.search("question", start_at="2026-08-15T00:00:00")
    with pytest.raises(ValueError, match="cannot be combined"):
        reader.search(
            "question",
            start_at="2026-08-15T00:00:00+00:00",
            latest_at="2026-08-16T00:00:00+00:00",
            lookback_days=1,
        )
    assert embedder.calls == []


def test_missing_persist_dir_fails_before_persistent_client_can_create_it(tmp_path):
    missing = tmp_path / "missing-chroma"

    with pytest.raises(GenerationReaderContractError, match="persist directory"):
        ChromaGenerationReader(_eligibility(), persist_dir=missing, embedder=FakeEmbedder())

    assert not missing.exists()


def _source_tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            stat.st_mtime_ns,
            stat.st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return snapshot


def _acquire_write_rows(database: Path) -> int:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM acquire_write").fetchone()[0])


def test_real_reader_queries_writable_runtime_clone_without_touching_source_generation(tmp_path):
    import chromadb
    from chromadb.api.client import SharedSystemClient

    source = tmp_path / "immutable-source"
    client = chromadb.PersistentClient(path=str(source))
    collection = client.create_collection(
        name="news_gen_1",
        metadata=_collection_metadata(),
    )
    collection.add(
        ids=["gen-1:1:0", "gen-1:2:0"],
        embeddings=[[0.1, 0.2], [0.2, 0.1]],
        documents=["canonical article 1", "canonical article 2"],
        metadatas=[_chunk_metadata(1, HASH_1), _chunk_metadata(2, HASH_2)],
    )
    del collection
    del client
    SharedSystemClient.clear_system_cache()

    source_before = _source_tree_snapshot(source)
    acquire_rows_before = _acquire_write_rows(source / "chroma.sqlite3")
    reader = ChromaGenerationReader(
        _eligibility(),
        persist_dir=source,
        embedder=FakeEmbedder(),
    )
    results = reader.search("question", allowed_article_ids={1, 2})
    source_after = _source_tree_snapshot(source)

    assert Path(reader.runtime_persist_dir).resolve() != source.resolve()
    assert len(results) == 2
    assert source_after == source_before
    assert _acquire_write_rows(source / "chroma.sqlite3") == acquire_rows_before
    reader.close()


def test_retired_runtime_clone_is_reclaimed_after_last_pinned_reader_closes(tmp_path):
    import chromadb
    from chromadb.api.client import SharedSystemClient

    source = tmp_path / "immutable-source"
    client = chromadb.PersistentClient(path=str(source))
    collection = client.create_collection(
        name="news_gen_1",
        metadata=_collection_metadata(),
    )
    collection.add(
        ids=["gen-1:1:0", "gen-1:2:0"],
        embeddings=[[0.1, 0.2], [0.2, 0.1]],
        documents=["canonical article 1", "canonical article 2"],
        metadatas=[_chunk_metadata(1, HASH_1), _chunk_metadata(2, HASH_2)],
    )
    del collection
    client.close()
    SharedSystemClient.clear_system_cache()

    first = ChromaGenerationReader(
        _eligibility(), persist_dir=source, embedder=FakeEmbedder()
    )
    first_runtime = Path(first.runtime_persist_dir)
    (source / "generation-version.txt").write_text("v2", encoding="utf-8")
    second = ChromaGenerationReader(
        _eligibility(), persist_dir=source, embedder=FakeEmbedder()
    )
    second_runtime = Path(second.runtime_persist_dir)

    assert first_runtime != second_runtime
    assert first_runtime.exists()
    assert second_runtime.exists()

    first.close()

    assert not first_runtime.exists()
    assert second_runtime.exists()
    second.close()


def test_shared_generation_reader_serializes_concurrent_searches() -> None:
    """SELECT INVARIANT: one request-pinned reader is safe for watchlist fan-out."""
    state_lock = Lock()
    active = 0
    max_active = 0

    class ConcurrentCollection(FakeCollection):
        def query(self, **kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return super().query(**kwargs)

    reader = ChromaGenerationReader(
        _eligibility(),
        client=FakeClient(ConcurrentCollection()),
        embedder=FakeEmbedder(),
    )

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(reader.search, f"query-{index}") for index in range(5)]
        for future in futures:
            assert future.result() == []

    assert max_active == 1


@pytest.mark.parametrize(
    ("record_id", "patch", "message"),
    [
        ("other:2:0", {}, "record id"),
        ("gen-1:1:0", {}, "record id"),
        ("gen-1:2:1", {}, "record id"),
        ("gen-1:2:0", {"generation_id": "other"}, "generation_id"),
        ("gen-1:2:0", {"corpus_snapshot_id": "other"}, "corpus_snapshot_id"),
        ("gen-1:2:0", {"article_id": 1}, "article_id"),
        ("gen-1:2:0", {"indexed_content_sha256": HASH_1}, "indexed_content_sha256"),
        ("gen-1:2:0", {"chunk_index": 1}, "chunk_index"),
        ("gen-1:2:0", {"index_config_fingerprint": "x"}, "index_config_fingerprint"),
        ("gen-1:2:0", {"embedding_model": "other"}, "embedding_model"),
        ("gen-1:2:0", {"embedding_artifact": "other"}, "embedding_artifact"),
    ],
)
def test_rejects_entire_query_when_a_returned_chunk_breaks_generation_contract(record_id, patch, message):
    metadata = _chunk_metadata(2, HASH_2)
    metadata.update(patch)
    collection = FakeCollection(
        payload={
            "ids": [[record_id]],
            "metadatas": [[metadata]],
            "documents": [["chunk"]],
            "distances": [[0.1]],
        }
    )
    reader = ChromaGenerationReader(
        _eligibility(), client=FakeClient(collection), embedder=FakeEmbedder()
    )
    with pytest.raises(GenerationReaderContractError, match=message):
        reader.search("question", allowed_article_ids={2})


def test_rejects_unrequested_or_ineligible_chunk_even_if_chroma_ignores_where_filter():
    collection = FakeCollection(
        payload={
            "ids": [["gen-1:1:0"]],
            "metadatas": [[_chunk_metadata(1, HASH_1)]],
            "documents": [["chunk"]],
            "distances": [[0.1]],
        }
    )
    reader = ChromaGenerationReader(
        _eligibility(), client=FakeClient(collection), embedder=FakeEmbedder()
    )
    with pytest.raises(GenerationReaderContractError, match="eligible query scope"):
        reader.search("question", allowed_article_ids={2})


def test_uses_injected_client_factory_for_persist_dir_and_still_opens_exact_collection(tmp_path):
    client = FakeClient(FakeCollection())
    calls = []
    ChromaGenerationReader(
        _eligibility(),
        persist_dir=tmp_path,
        client_factory=lambda path: calls.append(path) or client,
        embedder=FakeEmbedder(),
    )
    assert calls == [str(tmp_path)]
    assert client.get_calls == ["news_gen_1"]


def test_wraps_missing_exact_collection_without_loading_embedder():
    class MissingClient:
        def get_collection(self, *, name):
            raise KeyError(name)

    calls = []
    with pytest.raises(GenerationReaderContractError, match="collection is unavailable"):
        ChromaGenerationReader(
            _eligibility(),
            client=MissingClient(),
            embedder_factory=lambda artifact: calls.append(artifact),
        )
    assert calls == []


def test_rejects_malformed_parallel_query_payload_instead_of_partially_hydrating():
    collection = FakeCollection(
        payload={
            "ids": [["gen-1:2:0"]],
            "metadatas": [[_chunk_metadata(2, HASH_2)]],
            "documents": [[]],
            "distances": [[0.1]],
        }
    )
    reader = ChromaGenerationReader(
        _eligibility(), client=FakeClient(collection), embedder=FakeEmbedder()
    )
    with pytest.raises(GenerationReaderContractError, match="lengths are inconsistent"):
        reader.search("question", allowed_article_ids={2})
