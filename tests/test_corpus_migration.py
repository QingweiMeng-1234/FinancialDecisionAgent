import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "src" / "event_collector" / "corpus_migration.py"
SPEC = importlib.util.spec_from_file_location("corpus_migration_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
corpus_migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = corpus_migration
SPEC.loader.exec_module(corpus_migration)

MigrationSafetyError = corpus_migration.MigrationSafetyError
READY_HASH_MISMATCH = corpus_migration.READY_HASH_MISMATCH
READY_MISSING_CONTENT = corpus_migration.READY_MISSING_CONTENT
READY_VALID = corpus_migration.READY_VALID
SourceDriftError = corpus_migration.SourceDriftError
audit_chroma_snapshot = corpus_migration.audit_chroma_snapshot
apply_corpus_migration = corpus_migration.apply_corpus_migration
build_corpus_migration_plan = corpus_migration.build_corpus_migration_plan
load_plan_manifest = corpus_migration.load_plan_manifest
verify_staged_corpus = corpus_migration.verify_staged_corpus
write_plan_manifest = corpus_migration.write_plan_manifest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_legacy_corpus(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "source.db"
    content_root = tmp_path / "legacy_articles"
    content_root.mkdir()
    valid = "Canonical article one."
    mismatch = "Article two changed after it was indexed."
    versioned = content_root / "versions" / "1"
    versioned.mkdir(parents=True)
    (versioned / "current.txt").write_text(valid, encoding="utf-8")
    (content_root / "1.txt").write_text("stale flat fallback", encoding="utf-8")
    (content_root / "2.txt").write_text(mismatch, encoding="utf-8")
    (content_root / "5.txt").write_text("safe pending local content", encoding="utf-8")
    (content_root / "orphan.txt").write_text("unreferenced", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("must never be read", encoding="utf-8")

    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            content_path TEXT,
            content_sha256 TEXT,
            content_status TEXT NOT NULL,
            index_status TEXT NOT NULL,
            summary_status TEXT NOT NULL DEFAULT 'pending',
            summary TEXT
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO articles
        (id, content_path, content_sha256, content_status, index_status, summary_status, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "versions/1/current.txt", _sha(valid), "ready", "ready", "ready", "- summary"),
            (2, r"C:\old-machine\articles\2.txt", _sha("old content"), "ready", "ready", "ready", "- old summary"),
            (3, r"C:\old-machine\articles\3.txt", _sha("missing"), "ready", "ready", "pending", None),
            (4, "../outside.txt", None, "pending", "pending", "pending", None),
            (5, "5.txt", None, "pending", "ready", "pending", None),
        ],
    )
    connection.commit()
    connection.close()
    return database, content_root


def test_plan_is_deterministic_and_does_not_trust_legacy_absolute_paths(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)

    first = build_corpus_migration_plan(database, content_root)
    second = build_corpus_migration_plan(database, content_root)

    assert first.to_json() == second.to_json()
    assert {entry.article_id: entry.classification for entry in first.entries} == {
        1: READY_VALID,
        2: READY_HASH_MISMATCH,
        3: READY_MISSING_CONTENT,
        4: "non_ready",
        5: "non_ready_with_content",
    }
    assert first.entries[0].source_relpath == "versions/1/current.txt"
    assert first.entries[3].source_relpath is None
    assert [orphan.relpath for orphan in first.orphan_files] == ["1.txt", "orphan.txt"]

    manifest = tmp_path / "plan.json"
    write_plan_manifest(first, manifest)
    assert json.loads(manifest.read_text(encoding="utf-8"))["plan_id"] == first.plan_id
    assert load_plan_manifest(manifest) == first
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "must never be read"


def test_apply_creates_portable_verified_stage_and_preserves_source(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)
    source_before = database.read_bytes()
    plan = build_corpus_migration_plan(database, content_root)

    output = apply_corpus_migration(plan, tmp_path / "stage")
    verification = verify_staged_corpus(output)

    assert verification.valid is True
    assert verification.checked_ready_articles == 1
    assert verification.audit_rows == 5
    assert database.read_bytes() == source_before
    assert (content_root / "versions" / "1" / "current.txt").read_text(encoding="utf-8") == "Canonical article one."

    connection = sqlite3.connect(output / "news_articles.db")
    connection.row_factory = sqlite3.Row
    rows = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT id, content_path, content_status, index_status, summary_status, summary FROM articles ORDER BY id"
        )
    }
    audit = dict(connection.execute("SELECT article_id, classification FROM corpus_migration_audit"))
    canonical = {
        row["id"]: dict(row)
        for row in connection.execute(
            """
            SELECT id, active_content_sha256, content_validation_status,
                   summary_content_sha256, indexed_content_sha256,
                   quarantined_at, quarantine_reason, quarantine_source_relpath,
                   quarantine_expected_sha256, quarantine_observed_sha256, quarantine_plan_id
            FROM articles ORDER BY id
            """
        ).fetchall()
    }
    connection.close()
    valid_path = f"1/{_sha('Canonical article one.')}.txt"
    assert rows[1] == (valid_path, "ready", "pending", "pending", None)
    assert (output / "data" / "articles" / valid_path).read_text(encoding="utf-8") == "Canonical article one."
    assert rows[2] == (None, "failed", "pending", "pending", None)
    assert rows[3] == (None, "failed", "pending", "pending", None)
    assert rows[4][0] is None
    assert rows[5] == (None, "pending", "pending", "pending", None)
    assert canonical[1] == {
        "id": 1,
        "active_content_sha256": _sha("Canonical article one."),
        "content_validation_status": "verified",
        "summary_content_sha256": None,
        "indexed_content_sha256": None,
        "quarantined_at": None,
        "quarantine_reason": None,
        "quarantine_source_relpath": None,
        "quarantine_expected_sha256": None,
        "quarantine_observed_sha256": None,
        "quarantine_plan_id": None,
    }
    assert canonical[2]["active_content_sha256"] is None
    assert canonical[2]["content_validation_status"] == "quarantined"
    assert canonical[2]["quarantine_reason"] == "source_ready_ready_hash_mismatch"
    assert canonical[2]["quarantine_source_relpath"] == "2.txt"
    assert canonical[2]["quarantine_expected_sha256"] == _sha("old content")
    assert canonical[2]["quarantine_observed_sha256"] == _sha("Article two changed after it was indexed.")
    assert canonical[2]["quarantine_plan_id"] == plan.plan_id
    assert canonical[2]["quarantined_at"]
    assert canonical[3]["content_validation_status"] == "quarantined"
    assert canonical[3]["quarantine_reason"] == "source_ready_ready_missing_content"
    assert canonical[3]["quarantine_source_relpath"] is None
    assert canonical[3]["quarantine_expected_sha256"] == _sha("missing")
    assert canonical[3]["quarantine_observed_sha256"] is None
    assert canonical[3]["quarantine_plan_id"] == plan.plan_id
    assert audit == {
        1: READY_VALID,
        2: READY_HASH_MISMATCH,
        3: READY_MISSING_CONTENT,
        4: "non_ready",
        5: "non_ready_with_content",
    }
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))["source_fingerprint"] == plan.source_fingerprint
    (output / "data" / "articles" / "unexpected.txt").write_text("orphan", encoding="utf-8")
    assert verify_staged_corpus(output).valid is False


@pytest.mark.parametrize(
    ("article_id", "column", "value", "issue"),
    [
        (1, "active_content_sha256", "0" * 64, "staged metadata differs from manifest"),
        (1, "content_validation_status", "pending", "staged metadata differs from manifest"),
        (2, "quarantine_plan_id", "wrong-plan", "quarantine plan differs from manifest"),
        (2, "quarantine_observed_sha256", None, "quarantine observed hash differs from manifest"),
        (1, "indexed_content_sha256", "0" * 64, "all staged rows must have indexed_content_sha256=NULL"),
        (1, "summary_status", "ready", "all staged rows must have summary_status=pending"),
        (1, "summary", "stale summary", "all staged rows must have summary=NULL"),
        (1, "summary_content_sha256", "0" * 64, "all staged rows must have summary_content_sha256=NULL"),
        (1, "quarantine_reason", "forged", "valid-ready row contains quarantine evidence"),
    ],
)
def test_verify_rejects_canonical_or_quarantine_contract_tampering(tmp_path, article_id, column, value, issue):
    database, content_root = _make_legacy_corpus(tmp_path)
    stage = apply_corpus_migration(build_corpus_migration_plan(database, content_root), tmp_path / "stage")

    connection = sqlite3.connect(stage / "news_articles.db")
    connection.execute(f"UPDATE articles SET {column} = ? WHERE id = ?", (value, article_id))
    connection.commit()
    connection.close()

    verification = verify_staged_corpus(stage)

    assert verification.valid is False
    assert any(issue in item for item in verification.issues)


def test_apply_rejects_existing_output_and_source_drift(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)
    plan = build_corpus_migration_plan(database, content_root)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(MigrationSafetyError):
        apply_corpus_migration(plan, existing)

    (content_root / "versions" / "1" / "current.txt").write_text("changed", encoding="utf-8")
    output = tmp_path / "drift-stage"
    with pytest.raises(SourceDriftError):
        apply_corpus_migration(plan, output)
    assert not output.exists()


def test_plan_rejects_title_update_committed_only_to_wal(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("ALTER TABLE articles ADD COLUMN title TEXT")
        writer.execute("UPDATE articles SET title = 'before plan' WHERE id = 1")
        writer.commit()
        plan = build_corpus_migration_plan(database, content_root)
        main_database_before = database.read_bytes()

        # ``title`` is intentionally outside the ingestion audit projection.
        # The plan must still reject this committed WAL-only logical DB drift.
        writer.execute("UPDATE articles SET title = 'changed after plan' WHERE id = 1")
        writer.commit()
        wal = database.with_name(database.name + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        assert database.read_bytes() == main_database_before

        with pytest.raises(SourceDriftError):
            apply_corpus_migration(plan, tmp_path / "stage")
    finally:
        writer.close()


def test_sqlite_backup_preserves_planned_logical_snapshot(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("ALTER TABLE articles ADD COLUMN title TEXT")
        writer.execute("UPDATE articles SET title = 'included in planned WAL snapshot' WHERE id = 1")
        writer.commit()
        plan = build_corpus_migration_plan(database, content_root)
        backup = tmp_path / "backup.db"

        corpus_migration._sqlite_backup(database, backup)

        assert corpus_migration._sqlite_logical_sha256(backup) == plan.source_database_logical_sha256
    finally:
        writer.close()


def test_apply_rechecks_logical_hash_after_backup(tmp_path, monkeypatch):
    database, content_root = _make_legacy_corpus(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE articles ADD COLUMN title TEXT")
    connection.execute("UPDATE articles SET title = 'before plan' WHERE id = 1")
    connection.commit()
    connection.close()
    plan = build_corpus_migration_plan(database, content_root)
    original_backup = corpus_migration._sqlite_backup

    def backup_after_unselected_database_drift(source: Path, destination: Path) -> None:
        writer = sqlite3.connect(source)
        try:
            writer.execute("UPDATE articles SET title = 'changed before backup' WHERE id = 1")
            writer.commit()
        finally:
            writer.close()
        original_backup(source, destination)

    monkeypatch.setattr(corpus_migration, "_sqlite_backup", backup_after_unselected_database_drift)

    output = tmp_path / "stage"
    with pytest.raises(SourceDriftError, match="while creating the SQLite backup"):
        apply_corpus_migration(plan, output)
    assert not output.exists()


def test_apply_rejects_output_inside_source_content_root(tmp_path):
    database, content_root = _make_legacy_corpus(tmp_path)
    plan = build_corpus_migration_plan(database, content_root)

    with pytest.raises(MigrationSafetyError):
        apply_corpus_migration(plan, content_root / "new-stage")


def test_raw_sqlite_chroma_audit_uses_snapshot_without_embedding_client(tmp_path):
    chroma_root = tmp_path / "chroma"
    chroma_root.mkdir()
    database = chroma_root / "chroma.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE segments (id TEXT PRIMARY KEY, collection TEXT NOT NULL);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL, embedding_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER NOT NULL, key TEXT NOT NULL, string_value TEXT, int_value INTEGER);
        """
    )
    connection.execute("INSERT INTO collections VALUES ('c1', 'news_articles')")
    connection.execute("INSERT INTO segments VALUES ('s1', 'c1')")
    connection.executemany("INSERT INTO embeddings VALUES (?, 's1', ?)", [(1, "1:0"), (2, "legacy")])
    connection.executemany(
        "INSERT INTO embedding_metadata VALUES (?, ?, ?, ?)",
        [(1, "article_id", None, 11), (1, "content_sha256", "a" * 64, None), (2, "content_sha256", "b" * 64, None)],
    )
    connection.commit()
    connection.close()
    source_before = database.read_bytes()
    sys.modules.pop("event_collector.vector_store", None)
    sys.modules.pop("sentence_transformers", None)

    audit = audit_chroma_snapshot(chroma_root, collection_name="news_articles")

    assert audit.status == "ok"
    assert audit.record_count == 2
    assert audit.article_ids == (11,)
    assert audit.missing_article_id_records == 1
    assert audit.content_hash_metadata == ((11, ("a" * 64,)),)
    assert database.read_bytes() == source_before
    assert "event_collector.vector_store" not in sys.modules
    assert "sentence_transformers" not in sys.modules


def test_plan_reconciles_chroma_ids_and_hashes_against_sqlite(tmp_path, monkeypatch):
    database, content_root = _make_legacy_corpus(tmp_path)
    fake_audit = corpus_migration.ChromaAudit(
        status="ok",
        collection_name="news_articles",
        record_count=4,
        article_ids=(1, 5, 99),
        missing_article_id_records=1,
        content_hash_metadata=(
            (1, (_sha("wrong"),)),
            (5, (None,)),
            (99, (_sha("unknown"),)),
        ),
    )
    monkeypatch.setattr(corpus_migration, "audit_chroma_snapshot", lambda *args, **kwargs: fake_audit)

    plan = build_corpus_migration_plan(database, content_root, chroma_dir=tmp_path)

    assert plan.chroma_reconciliation == {
        "indexed_non_ready_article_ids": [5],
        "indexed_unknown_article_ids": [99],
        "db_index_ready_missing_article_ids": [2, 3],
        "hash_mismatch_article_ids": [1],
        "missing_hash_metadata_article_ids": [5],
    }
