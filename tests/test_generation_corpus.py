import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "src" / "event_collector" / "generation_corpus.py"
SPEC = importlib.util.spec_from_file_location("generation_corpus_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
generation_corpus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generation_corpus
SPEC.loader.exec_module(generation_corpus)

GenerationCorpusError = generation_corpus.GenerationCorpusError
build_generation_corpus_snapshot = generation_corpus.build_generation_corpus_snapshot
load_generation_manifest = generation_corpus.load_generation_manifest
write_generation_manifest = generation_corpus.write_generation_manifest


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _create_database(path: Path, rows: list[tuple[object, ...]]) -> None:
    connection = sqlite3.connect(path)
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
    connection.executemany(
        """
        INSERT INTO articles (
            id, source, content_path, active_content_sha256,
            content_status, content_validation_status, index_status, quarantined_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def _row(
    article_id: int,
    path: str | None,
    content_hash: str | None,
    *,
    source: str = "news",
    content_status: str = "ready",
    validation_status: str = "verified",
    index_status: str = "pending",
    quarantined_at: str | None = None,
) -> tuple[object, ...]:
    return (
        article_id,
        source,
        path,
        content_hash,
        content_status,
        validation_status,
        index_status,
        quarantined_at,
    )


def test_builds_complete_sorted_snapshot_independent_of_old_index_status_and_preserves_db(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    contents = {1: "alpha", 2: "bravo", 3: "company filing"}
    for article_id, content in contents.items():
        target = root / str(article_id) / f"{_sha(content)}.txt"
        target.parent.mkdir()
        target.write_text(content, encoding="utf-8")

    database = tmp_path / "source.db"
    _create_database(
        database,
        [
            _row(3, f"3/{_sha(contents[3])}.txt", _sha(contents[3]), source="filing", validation_status="not_applicable", index_status="ready"),
            _row(2, f"2/{_sha(contents[2])}.txt", _sha(contents[2]), index_status="failed"),
            _row(1, f"1/{_sha(contents[1])}.txt", _sha(contents[1]), index_status="pending"),
            _row(4, None, None, content_status="pending"),
            _row(5, "unused.txt", "a" * 64, validation_status="rejected"),
            _row(6, "unused.txt", "b" * 64, quarantined_at="2026-08-15T00:00:00Z"),
            _row(7, "unused.txt", None),
            _row(8, "unused.txt", "c" * 64, validation_status="not_applicable"),
        ],
    )
    database_before = database.read_bytes()
    siblings_before = sorted(path.name for path in tmp_path.iterdir())

    snapshot = build_generation_corpus_snapshot(
        database,
        root,
        source_database_logical_sha256="d" * 64,
        snapshot_id="migration-snapshot-7",
    )

    assert snapshot.candidates == tuple(sorted(snapshot.candidates, key=lambda item: item.article_id))
    assert [(item.article_id, item.content, item.content_sha256) for item in snapshot.candidates] == [
        (1, "alpha", _sha("alpha")),
        (2, "bravo", _sha("bravo")),
        (3, "company filing", _sha("company filing")),
    ]
    fingerprint_payload = json.dumps(
        [[article_id, _sha(contents[article_id])] for article_id in (1, 2, 3)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert snapshot.eligible_snapshot_fingerprint == hashlib.sha256(fingerprint_payload).hexdigest()
    assert snapshot.source_database_logical_sha256 == "d" * 64
    assert snapshot.snapshot_id == "migration-snapshot-7"
    assert database.read_bytes() == database_before
    assert sorted(path.name for path in tmp_path.iterdir()) == siblings_before


@pytest.mark.parametrize("defect", ["traversal", "absolute", "missing", "empty", "hash_mismatch"])
def test_any_eligible_row_with_invalid_canonical_content_fails_the_whole_snapshot(tmp_path, defect):
    root = tmp_path / "content"
    root.mkdir()
    good = "good content"
    (root / "good.txt").write_text(good, encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    if defect == "traversal":
        stored_path, expected = "../outside.txt", _sha("outside")
    elif defect == "absolute":
        stored_path, expected = str(outside.resolve()), _sha("outside")
    elif defect == "missing":
        stored_path, expected = "missing.txt", _sha("missing")
    elif defect == "empty":
        (root / "bad.txt").write_text("", encoding="utf-8")
        stored_path, expected = "bad.txt", _sha("")
    else:
        (root / "bad.txt").write_text("changed", encoding="utf-8")
        stored_path, expected = "bad.txt", _sha("original")

    database = tmp_path / "source.db"
    _create_database(
        database,
        [
            _row(1, "good.txt", _sha(good)),
            _row(2, stored_path, expected),
        ],
    )
    before = database.read_bytes()

    with pytest.raises(GenerationCorpusError, match=r"article 2"):
        build_generation_corpus_snapshot(database, root)

    assert database.read_bytes() == before


def test_symlink_escape_is_rejected_for_an_eligible_row(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are unavailable on this host")
    database = tmp_path / "source.db"
    _create_database(database, [_row(1, "escape.txt", _sha("outside"))])

    with pytest.raises(GenerationCorpusError, match=r"article 1.*escapes"):
        build_generation_corpus_snapshot(database, root)


def test_manifest_round_trip_is_content_free_exclusive_and_tamper_checked(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    content = "manifest content must not be persisted"
    (root / "1.txt").write_text(content, encoding="utf-8")
    database = tmp_path / "source.db"
    _create_database(database, [_row(1, "1.txt", _sha(content))])
    snapshot = build_generation_corpus_snapshot(
        database,
        root,
        source_database_logical_sha256="a" * 64,
        snapshot_id="snapshot-1",
    )
    destination = tmp_path / "manifests" / "generation-input.json"

    written = write_generation_manifest(snapshot, destination)
    loaded = load_generation_manifest(written)

    assert loaded == snapshot.to_manifest()
    raw = json.loads(destination.read_text(encoding="utf-8"))
    assert raw == {
        "schema_version": 1,
        "source_database_logical_sha256": "a" * 64,
        "snapshot_id": "snapshot-1",
        "eligible_snapshot_fingerprint": snapshot.eligible_snapshot_fingerprint,
        "articles": [{"article_id": 1, "content_sha256": _sha(content)}],
    }
    assert content not in destination.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_generation_manifest(snapshot, destination)

    raw["articles"][0]["content_sha256"] = "b" * 64
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GenerationCorpusError, match="fingerprint"):
        load_generation_manifest(destination)


@pytest.mark.parametrize(
    "articles",
    [
        [
            {"article_id": 2, "content_sha256": "b" * 64},
            {"article_id": 1, "content_sha256": "a" * 64},
        ],
        [
            {"article_id": 1, "content_sha256": "a" * 64},
            {"article_id": 1, "content_sha256": "a" * 64},
        ],
    ],
)
def test_manifest_reader_rejects_noncanonical_article_order_or_duplicates(tmp_path, articles):
    path = tmp_path / "manifest.json"
    payload = {
        "schema_version": 1,
        "source_database_logical_sha256": None,
        "snapshot_id": None,
        "eligible_snapshot_fingerprint": hashlib.sha256(
            json.dumps(
                [[entry["article_id"], entry["content_sha256"]] for entry in articles],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "articles": articles,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GenerationCorpusError, match="sorted|unique"):
        load_generation_manifest(path)
