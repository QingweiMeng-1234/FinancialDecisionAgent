from __future__ import annotations

import sqlite3
from datetime import datetime

import backfill_publication_metadata as cli
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.publication_metadata_backfill import (
    FetchResponse,
    PublicationMetadataBackfill,
)


def _legacy_article(store: SQLiteNewsStore, url: str = "https://publisher.example/story") -> int:
    article_id = store.save_article(
        NewsArticle(
            source="news",
            title="Legacy story",
            description="Legacy source",
            content="Stored article text.",
            url=url,
            published_at=datetime(2026, 1, 1),
            published_at_provenance="legacy_unverified",
        )
    )
    store.conn.execute(
        "UPDATE articles SET source_published_at = NULL, published_at_provenance = 'legacy_unverified' WHERE id = ?",
        (article_id,),
    )
    store.conn.commit()
    return article_id


def _html(date_value: str, *, field: str = "datePublished") -> bytes:
    return (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"NewsArticle",'
        f'"{field}":"{date_value}"}}'
        "</script></head><body>story</body></html>"
    ).encode("utf-8")


def _html_date_published_candidates(*values: str) -> bytes:
    scripts = "".join(
        (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"NewsArticle",'
            f'"datePublished":"{value}"}}'
            "</script>"
        )
        for value in values
    )
    return f"<html><head>{scripts}</head><body>story</body></html>".encode("utf-8")


def test_dry_run_extracts_utc_json_ld_without_writing_database(tmp_path):
    """SELECT INVARIANT: dry-run produces evidence but never creates receipts or changes a row."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    article_id = _legacy_article(store)
    store.close()

    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(200, {}, _html("2026-08-15T08:30:00-04:00"), url),
    )

    report = runner.run(dry_run=True)

    assert report.applied_count == 0
    assert report.successes[0].article_id == article_id
    assert report.successes[0].normalized_utc == "2026-08-15T12:30:00+00:00"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT source_published_at, published_at_provenance FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        receipt_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'publication_metadata_backfill_receipts'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert row == (None, "legacy_unverified")
    assert receipt_table == 0


def test_apply_persists_receipt_and_publisher_provenance_together(tmp_path):
    """SELECT INVARIANT: apply atomically upgrades only a legacy-unverified row."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    article_id = _legacy_article(store)
    store.close()
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(200, {}, _html("2026-08-15T08:30:00Z"), url),
    )

    report = runner.run(dry_run=False)

    assert report.applied_count == 1
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT source_published_at, published_at_provenance FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        receipt = connection.execute(
            "SELECT article_id, field_name, normalized_utc FROM publication_metadata_backfill_receipts"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("2026-08-15T08:30:00+00:00", "publisher_metadata")
    assert receipt == (article_id, "datePublished", "2026-08-15T08:30:00+00:00")


def test_rejects_naive_and_date_modified_only_values(tmp_path):
    """SELECT INVARIANT: the tool never guesses a publisher time from an ambiguous metadata value."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    _legacy_article(store)
    store.close()
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(200, {}, _html("2026-08-15T08:30:00"), url),
    )

    report = runner.run(dry_run=True)

    assert [failure.code for failure in report.failures] == ["publication_date_not_utc_aware"]

    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(200, {}, _html("2026-08-15T08:30:00Z", field="dateModified"), url),
    )
    assert [failure.code for failure in runner.run(dry_run=True).failures] == ["publication_date_missing"]


def test_uses_a_later_utc_aware_date_published_when_an_earlier_candidate_is_naive(tmp_path):
    """SELECT INVARIANT: an ambiguous metadata duplicate cannot hide a later verified publisher timestamp."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    _legacy_article(store)
    store.close()
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(
            200, {},
            _html_date_published_candidates("2026-05-19T21:19:55", "2026-05-19T21:19:55Z"),
            url,
        ),
    )

    report = runner.run(dry_run=True)

    assert not report.failures
    assert report.successes[0].normalized_utc == "2026-05-19T21:19:55+00:00"


def test_rejects_conflicting_utc_aware_date_published_candidates(tmp_path):
    """SELECT INVARIANT: competing verified timestamps fail closed instead of selecting an arbitrary page order."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    _legacy_article(store)
    store.close()
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(
            200, {},
            _html_date_published_candidates("2026-05-19T21:19:55Z", "2026-05-19T21:19:56+00:00"),
            url,
        ),
    )

    report = runner.run(dry_run=True)

    assert [failure.code for failure in report.failures] == ["publication_date_conflict"]


def test_rejects_unallowlisted_redirect_before_following_it(tmp_path):
    """SELECT INVARIANT: every network hop is an exact-host allowlist boundary."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    _legacy_article(store)
    store.close()
    requests = []

    def fetch(url, **kwargs):
        requests.append(url)
        return FetchResponse(302, {"location": "https://evil.example/story"}, b"", url)

    report = PublicationMetadataBackfill(database, allow_hosts={"publisher.example"}, fetcher=fetch).run(dry_run=True)

    assert requests == ["https://publisher.example/story"]
    assert [failure.code for failure in report.failures] == ["redirect_host_not_allowed"]


def test_apply_guard_rolls_back_receipt_when_row_no_longer_legacy_unverified(tmp_path):
    """SELECT INVARIANT: an optimistic-state conflict cannot leave an audit receipt claiming an apply."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    article_id = _legacy_article(store)
    store.close()

    class ConflictRunner(PublicationMetadataBackfill):
        def _apply_receipt(self, receipt):
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE articles SET published_at_provenance = 'source_metadata' WHERE id = ?", (article_id,)
                )
            return super()._apply_receipt(receipt)

    report = ConflictRunner(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: FetchResponse(200, {}, _html("2026-08-15T08:30:00Z"), url),
    ).run(dry_run=False)

    assert report.applied_count == 0
    assert [failure.code for failure in report.failures] == ["apply_guard_rejected"]
    connection = sqlite3.connect(database)
    try:
        receipt_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'publication_metadata_backfill_receipts'"
        ).fetchone()[0]
        assert receipt_table == 0
    finally:
        connection.close()


def test_cli_defaults_to_dry_run_and_writes_a_report_json(tmp_path, monkeypatch):
    """SELECT INVARIANT: invoking the operator CLI without --apply cannot authorize a write."""
    captured = {}

    class FakeRunner:
        def __init__(self, database, **kwargs):
            captured["database"] = database
            captured["options"] = kwargs

        def run(self, *, dry_run, limit, ready_only, article_ids):
            captured["dry_run"] = dry_run
            captured["limit"] = limit
            captured["ready_only"] = ready_only
            captured["article_ids"] = article_ids
            return type("Report", (), {"failures": (), "to_dict": lambda self: {"dry_run": dry_run, "successes": [], "failures": [], "applied_count": 0}})()

    monkeypatch.setattr(cli, "PublicationMetadataBackfill", FakeRunner)
    report = tmp_path / "dry-run.json"

    assert cli.main([
        "--db-path", str(tmp_path / "news.db"), "--allow-host", "publisher.example",
        "--report-json", str(report),
    ]) == 0

    assert captured["dry_run"] is True
    assert captured["ready_only"] is False
    assert captured["article_ids"] is None
    assert captured["options"]["allow_hosts"] == ["publisher.example"]
    assert report.exists()


def test_cli_requires_explicit_apply_to_request_persistence(tmp_path, monkeypatch):
    """SELECT INVARIANT: only --apply sends a non-dry-run request to the backfill core."""
    captured = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *, dry_run, limit, ready_only, article_ids):
            captured["dry_run"] = dry_run
            captured["article_ids"] = article_ids
            return type("Report", (), {"failures": (), "to_dict": lambda self: {"dry_run": dry_run, "successes": [], "failures": [], "applied_count": 0}})()

    monkeypatch.setattr(cli, "PublicationMetadataBackfill", FakeRunner)

    assert cli.main([
        "--db-path", str(tmp_path / "news.db"), "--allow-host", "publisher.example",
        "--apply", "--report-json", str(tmp_path / "apply.json"),
    ]) == 0

    assert captured["dry_run"] is False
    assert captured["article_ids"] is None


def test_cli_dry_run_accepts_a_relative_database_path(tmp_path, monkeypatch):
    """SELECT INVARIANT: a normal relative --db-path is resolved before the read-only URI is opened."""
    monkeypatch.chdir(tmp_path)
    store = SQLiteNewsStore("relative-news.db")
    store.init_db()
    store.close()

    assert cli.main([
        "--db-path", "relative-news.db", "--allow-host", "publisher.example",
        "--report-json", "report.json",
    ]) == 0
    assert (tmp_path / "report.json").exists()


def test_ready_only_limits_backfill_candidates_to_ready_articles(tmp_path):
    """SELECT INVARIANT: --ready-only avoids spending a publisher request on pending legacy rows."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    ready_id = _legacy_article(store, "https://publisher.example/ready")
    pending_id, _ = store.create_or_get_article_reference(
        source="news",
        title="Pending legacy story",
        description="No fetched content yet",
        original_url="https://publisher.example/pending",
        published_at=datetime(2026, 1, 1),
        source_published_at=None,
        published_at_provenance="legacy_unverified",
    )
    store.conn.execute(
        "UPDATE articles SET source_published_at = NULL, published_at_provenance = 'legacy_unverified' WHERE id = ?",
        (pending_id,),
    )
    store.conn.commit()
    store.close()
    requested_urls = []
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: requested_urls.append(url) or FetchResponse(200, {}, _html("2026-08-15T08:30:00Z"), url),
    )

    report = runner.run(dry_run=True, ready_only=True)

    assert [item.article_id for item in report.successes] == [ready_id]
    assert requested_urls == ["https://publisher.example/ready"]


def test_cli_forwards_ready_only_without_changing_default(tmp_path, monkeypatch):
    """SELECT INVARIANT: ready filtering is opt-in at the CLI boundary."""
    calls = []

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            calls.append(kwargs)
            return type("Report", (), {"failures": (), "to_dict": lambda self: {"dry_run": True, "successes": [], "failures": [], "applied_count": 0}})()

    monkeypatch.setattr(cli, "PublicationMetadataBackfill", FakeRunner)
    common = ["--db-path", str(tmp_path / "news.db"), "--allow-host", "publisher.example"]

    assert cli.main([*common, "--report-json", str(tmp_path / "all.json")]) == 0
    assert cli.main([*common, "--ready-only", "--report-json", str(tmp_path / "ready.json")]) == 0
    assert calls == [
        {"dry_run": True, "limit": None, "ready_only": False, "article_ids": None},
        {"dry_run": True, "limit": None, "ready_only": True, "article_ids": None},
    ]


def test_explicit_article_ids_limit_apply_candidates(tmp_path):
    """SELECT INVARIANT: a reviewed apply cannot expand beyond its explicit article-ID allowlist."""
    database = tmp_path / "news.db"
    store = SQLiteNewsStore(str(database))
    store.init_db()
    first_id = _legacy_article(store, "https://publisher.example/first")
    second_id = _legacy_article(store, "https://publisher.example/second")
    store.close()
    requested_urls = []
    runner = PublicationMetadataBackfill(
        database,
        allow_hosts={"publisher.example"},
        fetcher=lambda url, **kwargs: requested_urls.append(url)
        or FetchResponse(200, {}, _html("2026-08-15T08:30:00Z"), url),
    )

    report = runner.run(dry_run=False, article_ids=[second_id])

    assert report.applied_count == 1
    assert requested_urls == ["https://publisher.example/second"]
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id, published_at_provenance FROM articles ORDER BY id"
        ).fetchall()
    assert rows == [
        (first_id, "legacy_unverified"),
        (second_id, "publisher_metadata"),
    ]


def test_cli_forwards_reviewed_article_id_allowlist(tmp_path, monkeypatch):
    """SELECT INVARIANT: repeated --article-id values reach the guarded core unchanged."""
    captured = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Report",
                (),
                {
                    "failures": (),
                    "to_dict": lambda self: {
                        "dry_run": False,
                        "successes": [],
                        "failures": [],
                        "applied_count": 0,
                    },
                },
            )()

    monkeypatch.setattr(cli, "PublicationMetadataBackfill", FakeRunner)

    assert cli.main([
        "--db-path", str(tmp_path / "news.db"),
        "--allow-host", "publisher.example",
        "--apply",
        "--article-id", "312",
        "--article-id", "657",
        "--report-json", str(tmp_path / "apply.json"),
    ]) == 0
    assert captured["article_ids"] == [312, 657]
