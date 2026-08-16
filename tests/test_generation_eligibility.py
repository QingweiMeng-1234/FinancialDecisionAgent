import hashlib
import os
import sqlite3
from dataclasses import replace
from types import MappingProxyType

import pytest

from event_collector import generation_eligibility
from event_collector.active_generation_reader import (
    ActiveGenerationSnapshot,
    ActiveManifestEntry,
)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _create_canonical_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT,
            published_at TEXT NOT NULL,
            source_published_at TEXT,
            published_at_provenance TEXT NOT NULL DEFAULT 'legacy_unverified',
            summary TEXT,
            original_url TEXT,
            normalized_url TEXT,
            canonical_url TEXT,
            content_path TEXT,
            content_status TEXT NOT NULL,
            active_content_sha256 TEXT,
            summary_status TEXT NOT NULL,
            summary_content_sha256 TEXT,
            content_validation_status TEXT NOT NULL,
            content_validation_reason TEXT,
            content_validator_version TEXT,
            publisher_source_id TEXT,
            publisher_source_name TEXT,
            story_group_id INTEGER,
            story_dedupe_method TEXT,
            final_response_url TEXT,
            response_status_code INTEGER,
            response_content_type TEXT,
            extractor_version TEXT,
            extracted_char_count INTEGER,
            fetched_at TEXT,
            raw_json TEXT,
            quarantined_at TEXT,
            quarantine_reason TEXT,
            index_status TEXT,
            indexed_content_sha256 TEXT
        );
        """
    )
    connection.close()


def _insert_article(database, *, article_id, content_hash, **overrides):
    values = {
        "id": article_id,
        "source": "news",
        "title": f"Article {article_id}",
        "description": f"Description {article_id}",
        "url": f"https://display.example/{article_id}",
        "published_at": "2026-08-15T10:00:00",
        "source_published_at": "2026-08-15T10:00:00+00:00",
        "published_at_provenance": "source_metadata",
        "summary": f"Summary {article_id}",
        "original_url": f"https://origin.example/{article_id}",
        "normalized_url": f"https://normalized.example/{article_id}",
        "canonical_url": f"https://canonical.example/{article_id}",
        "content_path": f"{article_id}.txt",
        "content_status": "ready",
        "active_content_sha256": content_hash,
        "summary_status": "ready",
        "summary_content_sha256": content_hash,
        "content_validation_status": "verified",
        "content_validation_reason": "policy-pass",
        "content_validator_version": "validator-v2",
        "publisher_source_id": "publisher-1",
        "publisher_source_name": "Publisher One",
        "story_group_id": 77,
        "story_dedupe_method": "canonical-url",
        "final_response_url": f"https://final.example/{article_id}",
        "response_status_code": 200,
        "response_content_type": "text/html",
        "extractor_version": "extractor-v3",
        "extracted_char_count": 1234,
        "fetched_at": "2026-08-15T09:00:00",
        "raw_json": '{"provider":"fixture"}',
        "quarantined_at": None,
        "quarantine_reason": None,
        # These legacy fields deliberately disagree and must not affect v2 eligibility.
        "index_status": "failed",
        "indexed_content_sha256": "legacy-stale-hash",
    }
    values.update(overrides)
    columns = tuple(values)
    placeholders = ", ".join("?" for _ in columns)
    connection = sqlite3.connect(database)
    connection.execute(
        f"INSERT INTO articles ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    connection.commit()
    connection.close()


def _active_snapshot(*entries):
    manifest = tuple(
        ActiveManifestEntry(
            generation_id="generation-v2",
            article_id=article_id,
            indexed_content_sha256=content_hash,
            expected_chunk_count=1,
            actual_chunk_count=1,
            index_config_fingerprint="f" * 64,
            index_status="indexed",
            indexed_at="2026-08-15T11:00:00Z",
        )
        for article_id, content_hash in entries
    )
    return ActiveGenerationSnapshot(
        generation_id="generation-v2",
        corpus_id="news",
        collection_name="news_articles_v2",
        corpus_snapshot_id="corpus-snapshot-v2",
        embedding_artifact="all-MiniLM-L6-v2",
        index_config=MappingProxyType({"chunk_size": 1000}),
        index_config_fingerprint="f" * 64,
        observed_chunk_count=len(entries),
        manifest=manifest,
        status="active",
    )


def test_builds_request_pinned_hydrated_snapshot_and_ignores_legacy_index_fields(
    tmp_path, monkeypatch
):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    content = "canonical immutable article"
    content_hash = _digest(content)
    (content_root / "1.txt").write_text(content, encoding="utf-8")
    _insert_article(database, article_id=1, content_hash=content_hash)

    calls = []
    statements = []
    original_connect = sqlite3.connect

    class RecordingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            statements.append(sql)
            return super().execute(sql, parameters)

    def recording_connect(*args, **kwargs):
        calls.append((args, dict(kwargs)))
        kwargs["factory"] = RecordingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(generation_eligibility.sqlite3, "connect", recording_connect)

    active_generation = _active_snapshot((1, content_hash))
    snapshot = generation_eligibility.build_eligibility_snapshot(
        active_generation,
        database,
        content_root,
    )

    assert snapshot.active_generation is active_generation
    assert snapshot.active_generation.index_config["chunk_size"] == 1000
    assert snapshot.generation_id == "generation-v2"
    assert snapshot.eligible_article_ids == frozenset({1})
    assert snapshot.exclusions == ()
    article = snapshot.articles[0]
    assert article.article_id == 1
    assert article.content == content
    assert article.indexed_content_sha256 == content_hash
    assert article.title == "Article 1"
    assert article.description == "Description 1"
    assert article.url == "https://display.example/1"
    assert article.canonical_url == "https://canonical.example/1"
    assert article.original_url == "https://origin.example/1"
    assert article.normalized_url == "https://normalized.example/1"
    assert article.summary == "Summary 1"
    assert article.source == "news"
    assert article.published_at == "2026-08-15T10:00:00"
    assert article.source_published_at == "2026-08-15T10:00:00+00:00"
    assert article.published_at_provenance == "source_metadata"
    assert article.publisher_source_name == "Publisher One"
    assert article.story_group_id == 77
    assert article.content_validator_version == "validator-v2"
    assert article.final_response_url == "https://final.example/1"
    assert article.extractor_version == "extractor-v3"
    assert article.fetched_at == "2026-08-15T09:00:00"
    assert article.raw_json == '{"provider":"fixture"}'
    assert calls[0][1]["uri"] is True
    assert "mode=ro" in calls[0][0][0]
    assert any(statement.strip().upper() == "PRAGMA QUERY_ONLY = ON" for statement in statements)

    connection = sqlite3.connect(database)
    connection.execute("UPDATE articles SET title = 'changed after request snapshot' WHERE id = 1")
    connection.commit()
    connection.close()
    (content_root / "1.txt").write_text("changed after snapshot", encoding="utf-8")

    assert snapshot.articles[0].title == "Article 1"
    assert snapshot.articles[0].content == content
    assert generation_eligibility.intersect_allowed_article_ids(snapshot, {1, 2}) == frozenset({1})


def _build_one(tmp_path, *, content="canonical content", row_overrides=None, manifest_hash=None):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    content_hash = _digest(content)
    (content_root / "1.txt").write_text(content, encoding="utf-8")
    _insert_article(
        database,
        article_id=1,
        content_hash=content_hash,
        **(row_overrides or {}),
    )
    return generation_eligibility.build_eligibility_snapshot(
        _active_snapshot((1, manifest_hash or content_hash)),
        database,
        content_root,
    )


@pytest.mark.parametrize(
    ("row_overrides", "expected_code"),
    [
        ({"content_status": "failed"}, generation_eligibility.CONTENT_NOT_READY),
        (
            {"quarantined_at": "2026-08-15T12:00:00Z"},
            generation_eligibility.ARTICLE_QUARANTINED,
        ),
        (
            {"source": "news", "content_validation_status": "not_applicable"},
            generation_eligibility.SOURCE_VALIDATION_INELIGIBLE,
        ),
        (
            {"source": "manual", "content_validation_status": "pending"},
            generation_eligibility.SOURCE_VALIDATION_INELIGIBLE,
        ),
    ],
)
def test_metadata_failures_are_stable_per_article_exclusions(
    tmp_path, row_overrides, expected_code
):
    snapshot = _build_one(tmp_path, row_overrides=row_overrides)

    assert snapshot.articles == ()
    assert snapshot.exclusions == (generation_eligibility.EligibilityExclusion(1, expected_code),)


def test_non_news_not_applicable_validation_is_eligible(tmp_path):
    snapshot = _build_one(
        tmp_path,
        row_overrides={"source": "manual", "content_validation_status": "not_applicable"},
    )

    assert snapshot.eligible_article_ids == frozenset({1})


def test_active_hash_must_match_the_manifest_even_when_legacy_index_fields_claim_ready(tmp_path):
    snapshot = _build_one(
        tmp_path,
        row_overrides={"index_status": "ready", "indexed_content_sha256": "f" * 64},
        manifest_hash="f" * 64,
    )

    assert snapshot.articles == ()
    assert snapshot.exclusions[0].code == generation_eligibility.ACTIVE_HASH_MISMATCH


@pytest.mark.parametrize(
    ("summary_status", "summary_hash", "summary", "expected"),
    [
        ("failed", "active", "stale summary", None),
        ("ready", "stale", "stale summary", None),
        ("ready", "active", "   ", None),
        ("ready", "active", "bound summary", "bound summary"),
    ],
)
def test_summary_is_hydrated_only_when_nonempty_ready_and_bound_to_active_hash(
    tmp_path, summary_status, summary_hash, summary, expected
):
    content = "summary-bound content"
    active_hash = _digest(content)
    snapshot = _build_one(
        tmp_path,
        content=content,
        row_overrides={
            "summary_status": summary_status,
            "summary_content_sha256": active_hash if summary_hash == "active" else "a" * 64,
            "summary": summary,
        },
    )

    assert snapshot.articles[0].summary == expected


@pytest.mark.parametrize(
    ("path_value", "expected_code"),
    [
        ("../outside.txt", generation_eligibility.INVALID_CONTENT_PATH),
        ("C:\\outside.txt", generation_eligibility.INVALID_CONTENT_PATH),
        ("missing.txt", generation_eligibility.CONTENT_FILE_MISSING),
    ],
)
def test_unsafe_or_missing_content_paths_exclude_only_that_article(
    tmp_path, path_value, expected_code
):
    snapshot = _build_one(tmp_path, row_overrides={"content_path": path_value})

    assert snapshot.articles == ()
    assert snapshot.exclusions[0].code == expected_code


def test_symlink_escape_is_excluded(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = content_root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    _create_canonical_database(database)
    content_hash = _digest("outside")
    _insert_article(
        database,
        article_id=1,
        content_hash=content_hash,
        content_path="escape.txt",
    )

    snapshot = generation_eligibility.build_eligibility_snapshot(
        _active_snapshot((1, content_hash)), database, content_root
    )

    assert snapshot.exclusions[0].code == generation_eligibility.INVALID_CONTENT_PATH


def test_invalid_utf8_empty_and_hash_mismatch_are_distinct_exclusions(tmp_path):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    fixtures = {
        1: (b"\xff", "1.txt", "a" * 64),
        2: (b"   ", "2.txt", _digest("   ")),
        3: (b"observed", "3.txt", _digest("expected")),
    }
    for article_id, (payload, path, content_hash) in fixtures.items():
        (content_root / path).write_bytes(payload)
        _insert_article(
            database,
            article_id=article_id,
            content_hash=content_hash,
            content_path=path,
        )

    snapshot = generation_eligibility.build_eligibility_snapshot(
        _active_snapshot(*((article_id, values[2]) for article_id, values in fixtures.items())),
        database,
        content_root,
    )

    assert {item.article_id: item.code for item in snapshot.exclusions} == {
        1: generation_eligibility.CONTENT_FILE_NOT_UTF8,
        2: generation_eligibility.CONTENT_FILE_EMPTY,
        3: generation_eligibility.CONTENT_FILE_HASH_MISMATCH,
    }


def test_missing_manifest_article_is_excluded_without_blocking_a_valid_sibling(tmp_path):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    content = "available sibling"
    digest = _digest(content)
    (content_root / "2.txt").write_text(content, encoding="utf-8")
    _insert_article(database, article_id=2, content_hash=digest, content_path="2.txt")

    snapshot = generation_eligibility.build_eligibility_snapshot(
        _active_snapshot((1, "a" * 64), (2, digest)), database, content_root
    )

    assert snapshot.eligible_article_ids == frozenset({2})
    assert snapshot.exclusions == (
        generation_eligibility.EligibilityExclusion(
            1, generation_eligibility.MISSING_CANONICAL_ROW
        ),
    )


@pytest.mark.parametrize("missing_target", ["database", "root"])
def test_missing_global_inputs_fail_closed(tmp_path, missing_target):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    if missing_target != "database":
        _create_canonical_database(database)
    if missing_target != "root":
        content_root.mkdir()

    expected_code = (
        generation_eligibility.SOURCE_DATABASE_UNAVAILABLE
        if missing_target == "database"
        else generation_eligibility.CONTENT_ROOT_UNAVAILABLE
    )
    with pytest.raises(generation_eligibility.EligibilitySnapshotBuildError) as caught:
        generation_eligibility.build_eligibility_snapshot(
            _active_snapshot(), database, content_root
        )

    assert caught.value.code == expected_code


def test_missing_required_schema_fails_closed(tmp_path):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    sqlite3.connect(database).execute("CREATE TABLE articles (id INTEGER PRIMARY KEY)").connection.close()

    with pytest.raises(generation_eligibility.EligibilitySnapshotBuildError) as caught:
        generation_eligibility.build_eligibility_snapshot(
            _active_snapshot(), database, content_root
        )

    assert caught.value.code == generation_eligibility.CANONICAL_SCHEMA_INVALID


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda snapshot: replace(
            snapshot,
            manifest=(replace(snapshot.manifest[0], generation_id="other-generation"),),
        ),
        lambda snapshot: replace(
            snapshot,
            manifest=(replace(snapshot.manifest[0], index_status="failed"),),
        ),
        lambda snapshot: replace(
            snapshot,
            manifest=(replace(snapshot.manifest[0], index_config_fingerprint="a" * 64),),
        ),
        lambda snapshot: replace(
            snapshot,
            manifest=(replace(snapshot.manifest[0], actual_chunk_count=2),),
        ),
        lambda snapshot: replace(snapshot, observed_chunk_count=2),
    ],
)
def test_forged_active_snapshot_manifest_fails_closed(tmp_path, corrupt):
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    content = "manifest-bound content"
    content_hash = _digest(content)
    (content_root / "1.txt").write_text(content, encoding="utf-8")
    _insert_article(database, article_id=1, content_hash=content_hash)
    active_generation = corrupt(_active_snapshot((1, content_hash)))

    with pytest.raises(generation_eligibility.EligibilitySnapshotBuildError) as caught:
        generation_eligibility.build_eligibility_snapshot(
            active_generation, database, content_root
        )

    assert caught.value.code == generation_eligibility.ACTIVE_SNAPSHOT_INVALID
def test_verified_candidate_has_explicit_eval_only_eligibility_path(tmp_path) -> None:
    database = tmp_path / "canonical.sqlite3"
    content_root = tmp_path / "content"
    content_root.mkdir()
    _create_canonical_database(database)
    content = "verified candidate article"
    content_hash = _digest(content)
    (content_root / "1.txt").write_text(content, encoding="utf-8")
    _insert_article(database, article_id=1, content_hash=content_hash)
    candidate = replace(_active_snapshot((1, content_hash)), status="verified")

    snapshot = generation_eligibility.build_verified_candidate_eligibility_snapshot(
        candidate,
        database,
        content_root,
    )

    assert snapshot.generation_id == candidate.generation_id
    assert snapshot.policy_version == "verified-candidate-eligibility-v1"
    with pytest.raises(generation_eligibility.EligibilitySnapshotBuildError):
        generation_eligibility.build_verified_candidate_eligibility_snapshot(
            replace(candidate, status="active"),
            database,
            content_root,
        )
