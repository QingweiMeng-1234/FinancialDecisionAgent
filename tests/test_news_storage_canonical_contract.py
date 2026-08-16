"""Dependency-independent contract tests for canonical article storage.

This module deliberately loads the two storage modules without importing the
``event_collector`` package initializer.  The normal package imports the
fetching stack (including optional extraction dependencies), while these tests
exercise only SQLite/filesystem invariants.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVENT_COLLECTOR_ROOT = REPOSITORY_ROOT / "src" / "event_collector"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_news_storage():
    """Load the storage module without executing event_collector.__init__."""
    package_prefix = "event_collector"
    preserved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == package_prefix or name.startswith(f"{package_prefix}.")
    }
    try:
        package = types.ModuleType(package_prefix)
        package.__path__ = [str(EVENT_COLLECTOR_ROOT)]
        sys.modules[package_prefix] = package

        # event_structuring only needs this base class at import time.  Avoid its
        # real OpenAI client dependency because none of these storage contracts use
        # it.
        openai_base = types.ModuleType("event_collector.openai_client_base")

        class OpenAIStructuredOutputClient:  # pragma: no cover - import shim only
            pass

        openai_base.OpenAIStructuredOutputClient = OpenAIStructuredOutputClient
        sys.modules["event_collector.openai_client_base"] = openai_base
        _load_module("event_collector.event_structuring", EVENT_COLLECTOR_ROOT / "event_structuring.py")
        return _load_module("event_collector.news_storage", EVENT_COLLECTOR_ROOT / "news_storage.py")
    finally:
        for name in tuple(sys.modules):
            if name == package_prefix or name.startswith(f"{package_prefix}."):
                sys.modules.pop(name, None)
        sys.modules.update(preserved_modules)


news_storage = _load_news_storage()
SQLiteNewsStore = news_storage.SQLiteNewsStore
NewsArticle = news_storage.NewsArticle
compute_content_sha256 = news_storage.compute_content_sha256


def _new_article(url: str, content: str = "canonical article body"):
    return NewsArticle(
        source="news",
        title="Canonical storage test",
        description="A deterministic article fixture",
        content=content,
        url=url,
        published_at=datetime(2026, 8, 15, 12, 0, 0),
    )


@pytest.fixture
def store(tmp_path):
    instance = SQLiteNewsStore(
        str(tmp_path / "news_articles.db"),
        content_dir=str(tmp_path / "content"),
    )
    instance.init_db()
    try:
        yield instance
    finally:
        instance.close()


def _save_ready(store: SQLiteNewsStore, *, suffix: str = "one", content: str = "canonical article body") -> int:
    article_id = store.save_article(_new_article(f"https://example.test/{suffix}", content))
    assert article_id is not None
    return article_id


def _state(store: SQLiteNewsStore, article_id: int):
    return store.conn.execute(
        """
        SELECT content_path, content_sha256, active_content_sha256, content_status,
               summary, summary_status, summary_content_sha256,
               index_status, indexed_content_sha256
        FROM articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()


def test_save_article_write_failure_never_exposes_ready_row(store, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_article_content", fail_write)
    with pytest.raises(OSError, match="disk full"):
        store.save_article(_new_article("https://example.test/write-failure"))

    rows = store.conn.execute(
        "SELECT content_status FROM articles WHERE url = ?",
        ("https://example.test/write-failure",),
    ).fetchall()
    assert all(row["content_status"] != "ready" for row in rows)


def test_empty_content_cannot_be_promoted_to_ready(store):
    article_id, created = store.create_or_get_article_reference(
        source="manual",
        title="Empty content",
        description="Must remain pending",
        original_url="internal://manual/empty",
        published_at=datetime.now(),
    )
    assert created
    with pytest.raises(ValueError, match="non-empty"):
        store.update_article_content(
            article_id,
            content="   ",
            validation_status="not_applicable",
        )
    assert store.conn.execute(
        "SELECT content_status FROM articles WHERE id = ?", (article_id,)
    ).fetchone()["content_status"] == "pending"


def test_save_article_preserves_explicit_legacy_states_without_unbound_ready_hashes(store):
    indexed = _new_article("https://example.test/preindexed", "preindexed body")
    indexed.index_status = "ready"
    indexed_id = store.save_article(indexed)
    indexed_row = _state(store, indexed_id)
    assert indexed_row["content_status"] == "ready"
    assert indexed_row["index_status"] == "ready"
    assert indexed_row["indexed_content_sha256"] == indexed_row["active_content_sha256"]

    failed = _new_article("https://example.test/failed-import", "untrusted imported body")
    failed.content_status = "failed"
    failed.index_status = "failed"
    failed_id = store.save_article(failed)
    failed_row = store.conn.execute(
        """
        SELECT content_status, active_content_sha256, content_validation_status,
               index_status, indexed_content_sha256
        FROM articles WHERE id = ?
        """,
        (failed_id,),
    ).fetchone()
    assert tuple(failed_row) == ("failed", None, "rejected", "failed", None)


def test_content_db_failure_preserves_old_active_version_and_derived_state(store):
    first_content = "first verified body"
    article_id = _save_ready(store, content=first_content)
    first_hash = compute_content_sha256(first_content)
    assert store.update_article_summary(article_id, "first summary", content_sha256=first_hash)
    assert store.mark_article_index_ready(article_id, first_hash)
    before = tuple(_state(store, article_id))

    store.conn.execute(
        """
        CREATE TRIGGER reject_new_canonical_version
        BEFORE UPDATE ON articles
        WHEN NEW.active_content_sha256 != OLD.active_content_sha256
        BEGIN
            SELECT RAISE(ABORT, 'simulated DB update failure');
        END
        """
    )
    second_content = "second verified body"
    with pytest.raises(sqlite3.DatabaseError, match="simulated DB update failure"):
        store.update_article_content(article_id, content=second_content)

    assert tuple(_state(store, article_id)) == before
    second_hash = compute_content_sha256(second_content)
    assert (Path(store.content_dir) / str(article_id) / f"{second_hash}.txt").read_text(encoding="utf-8") == second_content


def test_tampered_immutable_file_is_repaired_before_hash_named_path_is_reused(store):
    article_id = _save_ready(store)
    content = "version which must win over a tampered file"
    digest = compute_content_sha256(content)
    relative_path = store.write_article_content(article_id, content, content_sha256=digest)
    version_path = Path(store.content_dir, *relative_path.split("/"))
    version_path.write_text("tampered", encoding="utf-8")

    assert store.write_article_content(article_id, content, content_sha256=digest) == relative_path
    repaired = version_path.read_text(encoding="utf-8")
    assert repaired == content
    assert compute_content_sha256(repaired) == digest


def test_content_path_resolution_never_reads_outside_root_and_uses_safe_legacy_fallback(store, tmp_path):
    article_id = 42
    external = tmp_path / "outside.txt"
    external.write_text("must never be read", encoding="utf-8")
    fallback = Path(store.content_dir) / f"{article_id}.txt"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text("safe legacy content", encoding="utf-8")

    assert store.resolve_content_path(str(external), article_id=article_id) == str(fallback.resolve())
    assert store.resolve_content_path(str(external)) is None
    assert store.resolve_content_path("../outside.txt", article_id=article_id) is None


def test_symlink_escape_is_rejected_when_supported(store, tmp_path):
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    link = Path(store.content_dir) / "escape.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(external, link)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable in this environment")

    assert store.resolve_content_path("escape.txt") is None


def test_refresh_failure_keeps_verified_active_content_and_records_attempt(store):
    content = "stable verified body"
    article_id = _save_ready(store, content=content)
    digest = compute_content_sha256(content)
    assert store.update_article_summary(article_id, "stable summary", content_sha256=digest)
    assert store.mark_article_index_ready(article_id, digest)
    before = tuple(_state(store, article_id))

    assert store.mark_article_content_failure(article_id, reason="publisher timeout")
    assert tuple(_state(store, article_id)) == before
    attempt = store.conn.execute(
        "SELECT last_content_attempt_status, last_content_attempt_reason, last_content_attempt_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    assert tuple(attempt[:2]) == ("failed", "publisher timeout")
    assert attempt["last_content_attempt_at"]


def test_summary_and_index_compare_and_set_reject_stale_content_version(store):
    first_content = "H1 content"
    article_id = _save_ready(store, content=first_content)
    first_hash = compute_content_sha256(first_content)
    second_content = "H2 content"
    second_hash = compute_content_sha256(second_content)
    assert store.update_article_content(article_id, content=second_content)

    assert not store.update_article_summary(article_id, "stale H1 summary", content_sha256=first_hash)
    assert not store.mark_article_index_ready(article_id, first_hash)
    row = _state(store, article_id)
    assert row["active_content_sha256"] == second_hash
    assert row["summary"] is None
    assert row["summary_status"] == "pending"
    assert row["summary_content_sha256"] is None
    assert row["index_status"] == "pending"
    assert row["indexed_content_sha256"] is None


def test_late_same_hash_failure_cannot_overwrite_success(store):
    content = "same hash concurrent result"
    article_id = _save_ready(store, suffix="late-failure", content=content)
    digest = compute_content_sha256(content)
    assert store.update_article_summary(article_id, "successful summary", content_sha256=digest)
    assert store.mark_article_index_ready(article_id, digest)

    assert not store.mark_article_summary_failure(article_id, digest)
    assert not store.mark_article_index_failure(article_id, digest)
    row = _state(store, article_id)
    assert row["summary_status"] == "ready"
    assert row["summary_content_sha256"] == digest
    assert row["index_status"] == "ready"
    assert row["indexed_content_sha256"] == digest


def test_quarantined_article_cannot_be_silently_promoted(store):
    article_id = _save_ready(store, suffix="quarantine")
    store.conn.execute(
        """
        UPDATE articles
        SET content_status = 'failed', content_validation_status = 'quarantined',
            quarantined_at = ?, quarantine_reason = 'hash mismatch'
        WHERE id = ?
        """,
        (datetime.now().isoformat(), article_id),
    )
    store.conn.commit()

    with pytest.raises(ValueError, match="quarantined"):
        store.update_article_content(article_id, content="replacement without operator release")
    with pytest.raises(ValueError, match="quarantined"):
        store.mark_article_processing_status(article_id, content_status="ready")
    with pytest.raises(ValueError, match="quarantined"):
        store.mark_article_processing_status(article_id, index_status="ready")
    with pytest.raises(ValueError, match="quarantined"):
        store.mark_article_processing_status(article_id, summary_status="ready")

    assert store.mark_article_content_failure(article_id, reason="retry failed")

    row = store.conn.execute(
        """
        SELECT content_status, content_validation_status,
               quarantine_reason, last_content_attempt_reason
        FROM articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()
    assert tuple(row) == ("failed", "quarantined", "hash mismatch", "retry failed")


def test_delete_article_keeps_immutable_content_version_for_controlled_gc(store):
    content = "retained immutable version"
    article_id = _save_ready(store, content=content)
    digest = compute_content_sha256(content)
    version_path = Path(store.content_dir) / str(article_id) / f"{digest}.txt"
    assert version_path.is_file()

    assert store.delete_article(article_id)
    assert store.conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is None
    assert version_path.is_file()
    assert version_path.read_text(encoding="utf-8") == content


def test_relative_corpus_remains_readable_after_directory_relocation(tmp_path):
    original_root = tmp_path / "original"
    original_root.mkdir()
    original = SQLiteNewsStore(str(original_root / "news_articles.db"))
    original.init_db()
    article_id = original.save_article(
        _new_article("https://example.test/portable", "portable canonical body")
    )
    original.close()

    relocated_root = tmp_path / "relocated"
    shutil.copytree(original_root, relocated_root)
    relocated = SQLiteNewsStore(str(relocated_root / "news_articles.db"))
    relocated.init_db()
    try:
        article = relocated.get_article(article_id)
        row = relocated.conn.execute(
            "SELECT content_path FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        assert article is not None
        assert article.content == "portable canonical body"
        assert article.content_status == "ready"
        assert not os.path.isabs(row["content_path"])
        assert article.content_path.startswith(str(relocated_root.resolve()))
    finally:
        relocated.close()


def test_identity_merge_receipt_uses_portable_path_and_cleans_after_relocation(tmp_path):
    """SELECT INVARIANT: merge cleanup receipts retain a root-relative immutable version."""
    original_root = tmp_path / "original"
    original_root.mkdir()
    original = SQLiteNewsStore(str(original_root / "news_articles.db"))
    original.init_db()
    try:
        survivor_id, survivor_created = original.create_or_get_article_reference(
            source="news",
            title="Survivor",
            description="Lower provisional identity",
            original_url="https://example.test/survivor",
            published_at=datetime(2026, 8, 15, 12, 0, 0),
        )
        retired_id, retired_created = original.create_or_get_article_reference(
            source="news",
            title="Retired",
            description="Higher completed identity",
            original_url="https://example.test/retired",
            published_at=datetime(2026, 8, 15, 12, 0, 0),
        )
        assert (survivor_id, survivor_created, retired_id, retired_created) == (1, True, 2, True)

        content = "portable retired canonical body"
        canonical_url = "https://publisher.example/portable-merge"
        assert original.update_article_content(
            retired_id, content=content, canonical_url=canonical_url
        )
        digest = compute_content_sha256(content)
        assert original.update_article_summary(retired_id, "ready summary", content_sha256=digest)
        assert original.mark_article_index_ready(retired_id, digest)

        resolved_id, reused, retired_article_id, receipt_path = original.reconcile_article_identity(
            survivor_id, canonical_url
        )
        receipt = original.conn.execute(
            "SELECT retired_content_path FROM article_identity_merges WHERE retired_article_id = ?",
            (retired_id,),
        ).fetchone()
        assert (resolved_id, reused, retired_article_id) == (survivor_id, False, retired_id)
        assert receipt_path == receipt["retired_content_path"] == f"{retired_id}/{digest}.txt"
        assert not os.path.isabs(receipt["retired_content_path"])
    finally:
        original.close()

    relocated_root = tmp_path / "relocated"
    shutil.copytree(original_root, relocated_root)
    relocated = SQLiteNewsStore(str(relocated_root / "news_articles.db"))
    relocated.init_db()
    try:
        retired_file = relocated_root / "data" / "articles" / str(retired_id) / f"{digest}.txt"
        assert retired_file.is_file()
        assert relocated.complete_identity_merge_cleanup(
            retired_id, vector_cleanup_status="deleted"
        )
        receipt = relocated.conn.execute(
            "SELECT content_cleanup_status FROM article_identity_merges WHERE retired_article_id = ?",
            (retired_id,),
        ).fetchone()
        assert receipt["content_cleanup_status"] == "deleted"
        assert not retired_file.exists()
    finally:
        relocated.close()


def test_identity_merge_cleanup_never_opens_external_legacy_absolute_receipt(store, tmp_path):
    """SELECT INVARIANT: a migrated receipt cannot turn an external absolute path into cleanup authority."""
    survivor_id = _save_ready(store, suffix="legacy-receipt")
    digest = compute_content_sha256("canonical article body")
    assert store.update_article_summary(survivor_id, "ready summary", content_sha256=digest)
    assert store.mark_article_index_ready(survivor_id, digest)
    external = tmp_path / "external-retired.txt"
    external.write_text("must remain outside canonical cleanup", encoding="utf-8")
    store.conn.execute(
        """
        INSERT INTO article_identity_merges (
            retired_article_id, survivor_article_id, canonical_url,
            retired_content_path, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (99, survivor_id, "https://publisher.example/legacy-receipt", str(external), datetime.now().isoformat()),
    )
    store.conn.commit()

    assert store.complete_identity_merge_cleanup(99, vector_cleanup_status="deleted")
    receipt = store.conn.execute(
        "SELECT content_cleanup_status FROM article_identity_merges WHERE retired_article_id = 99"
    ).fetchone()
    assert receipt["content_cleanup_status"] == "not_present"
    assert external.read_text(encoding="utf-8") == "must remain outside canonical cleanup"
