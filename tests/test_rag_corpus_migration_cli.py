import hashlib
import json
from pathlib import Path
import sqlite3

import rag_corpus_migration


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_source(tmp_path: Path, *, mismatch: bool = False) -> tuple[Path, Path]:
    database = tmp_path / "source.db"
    content_root = tmp_path / "content"
    content_root.mkdir()
    content = "portable article content"
    (content_root / "1.txt").write_text(content, encoding="utf-8")
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            content_path TEXT,
            content_sha256 TEXT,
            content_status TEXT NOT NULL,
            index_status TEXT NOT NULL,
            summary_status TEXT NOT NULL,
            summary TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO articles VALUES (1, ?, ?, 'ready', 'ready', 'ready', ?)",
        (r"C:\old-machine\1.txt", _sha("different") if mismatch else _sha(content), "summary"),
    )
    connection.commit()
    connection.close()
    return database, content_root


def test_dry_run_writes_manifest_and_returns_findings_without_source_mutation(tmp_path, capsys):
    database, content_root = _make_source(tmp_path, mismatch=True)
    source_before = database.read_bytes()
    manifest = tmp_path / "plan.json"

    result = rag_corpus_migration.main(
        [
            "dry-run",
            "--db-path",
            str(database),
            "--content-root",
            str(content_root),
            "--manifest-out",
            str(manifest),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == rag_corpus_migration.EXIT_FINDINGS
    assert payload["status"] == "findings"
    assert payload["counts"]["invalid_ready"] == 1
    assert manifest.is_file()
    assert database.read_bytes() == source_before


def test_apply_requires_exact_plan_confirmation_then_verify_succeeds(tmp_path, capsys):
    database, content_root = _make_source(tmp_path)
    manifest = tmp_path / "plan.json"
    assert rag_corpus_migration.main(
        [
            "dry-run",
            "--db-path",
            str(database),
            "--content-root",
            str(content_root),
            "--manifest-out",
            str(manifest),
        ]
    ) == rag_corpus_migration.EXIT_OK
    capsys.readouterr()
    plan_id = json.loads(manifest.read_text(encoding="utf-8"))["plan_id"]
    refused_output = tmp_path / "refused"

    refused = rag_corpus_migration.main(
        [
            "apply",
            "--plan",
            str(manifest),
            "--output-dir",
            str(refused_output),
            "--confirm-plan-id",
            "0" * 64,
        ]
    )
    refused_payload = json.loads(capsys.readouterr().out)
    assert refused == rag_corpus_migration.EXIT_REFUSED
    assert refused_payload["status"] == "refused"
    assert not refused_output.exists()

    output = tmp_path / "stage"
    applied = rag_corpus_migration.main(
        [
            "apply",
            "--plan",
            str(manifest),
            "--output-dir",
            str(output),
            "--confirm-plan-id",
            plan_id,
        ]
    )
    applied_payload = json.loads(capsys.readouterr().out)
    assert applied == rag_corpus_migration.EXIT_OK
    assert applied_payload["verification"]["valid"] is True

    verified = rag_corpus_migration.main(["verify", "--output-dir", str(output)])
    verify_payload = json.loads(capsys.readouterr().out)
    assert verified == rag_corpus_migration.EXIT_OK
    assert verify_payload["status"] == "verified"


def test_dry_run_refuses_manifest_inside_source_content_root(tmp_path, capsys):
    database, content_root = _make_source(tmp_path)
    manifest = content_root / "unsafe-plan.json"

    result = rag_corpus_migration.main(
        [
            "dry-run",
            "--db-path",
            str(database),
            "--content-root",
            str(content_root),
            "--manifest-out",
            str(manifest),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == rag_corpus_migration.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert not manifest.exists()
