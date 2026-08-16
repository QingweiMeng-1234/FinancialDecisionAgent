"""Auditable, opt-in publisher publication-date backfill for legacy rows.

The module deliberately treats a legacy ``published_at`` as untrusted.  It
only accepts an offset-aware ``datePublished`` extracted from the publisher's
own HTML, and it binds each accepted value to an immutable fetch receipt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlparse

import requests


DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_BODY_BYTES = 1_000_000
DEFAULT_DELAY_SECONDS = 1.0
MAX_REDIRECTS = 5
RECEIPT_TABLE = "publication_metadata_backfill_receipts"
_DATE_PUBLISHED_META_NAMES = frozenset({"datepublished", "article:published_time", "og:published_time"})


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


@dataclass(frozen=True)
class PublicationMetadataReceipt:
    article_id: int
    source_url: str
    final_url: str
    field_name: str
    raw_value: str
    normalized_utc: str
    fetched_at: str
    body_sha256: str


@dataclass(frozen=True)
class BackfillFailure:
    article_id: int
    source_url: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class BackfillReport:
    dry_run: bool
    successes: tuple[PublicationMetadataReceipt, ...]
    failures: tuple[BackfillFailure, ...]
    applied_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "successes": [asdict(item) for item in self.successes],
            "failures": [asdict(item) for item in self.failures],
            "applied_count": self.applied_count,
        }


class PublicationMetadataBackfill:
    """Fetch and optionally apply verifiable publisher publication dates."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        allow_hosts: Iterable[str],
        fetcher: Callable[..., FetchResponse] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.allow_hosts = _normalize_allow_hosts(allow_hosts)
        if not self.allow_hosts:
            raise ValueError("allow_hosts must contain at least one exact hostname")
        if timeout_seconds <= 0 or max_body_bytes <= 0 or delay_seconds < 0:
            raise ValueError("timeout/max-body must be positive and delay cannot be negative")
        self.timeout_seconds = float(timeout_seconds)
        self.max_body_bytes = int(max_body_bytes)
        self.delay_seconds = float(delay_seconds)
        self._fetcher = fetcher or _requests_fetch
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper

    def run(
        self,
        *,
        dry_run: bool = True,
        limit: int | None = None,
        ready_only: bool = False,
        article_ids: Iterable[int] | None = None,
    ) -> BackfillReport:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive when provided")
        normalized_article_ids = _normalize_article_ids(article_ids)
        candidates = self._candidates(
            limit,
            ready_only=ready_only,
            article_ids=normalized_article_ids,
        )
        successes: list[PublicationMetadataReceipt] = []
        failures: list[BackfillFailure] = []
        applied = 0
        for position, (article_id, source_url) in enumerate(candidates):
            if position:
                self._sleeper(self.delay_seconds)
            try:
                receipt = self._extract_receipt(article_id, source_url)
            except _BackfillError as error:
                failures.append(BackfillFailure(article_id, source_url, error.code, error.detail))
                continue
            successes.append(receipt)
            if not dry_run:
                try:
                    self._apply_receipt(receipt)
                except _BackfillError as error:
                    failures.append(BackfillFailure(article_id, source_url, error.code, error.detail))
                    continue
                applied += 1
        return BackfillReport(dry_run, tuple(successes), tuple(failures), applied)

    def _candidates(
        self,
        limit: int | None,
        *,
        ready_only: bool,
        article_ids: tuple[int, ...] | None,
    ) -> list[tuple[int, str]]:
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        if article_ids == ():
            return []
        connection = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        try:
            ready_clause = " AND content_status = 'ready'" if ready_only else ""
            query = """
                SELECT id, COALESCE(canonical_url, original_url, url)
                FROM articles
                WHERE published_at_provenance = 'legacy_unverified'
                  AND source_published_at IS NULL
                  AND COALESCE(canonical_url, original_url, url) IS NOT NULL
            """ + ready_clause
            params: list[int] = []
            if article_ids is not None:
                query += " AND id IN (" + ",".join("?" for _ in article_ids) + ")"
                params.extend(article_ids)
            query += " ORDER BY id"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            return [(int(row[0]), str(row[1])) for row in connection.execute(query, params)]
        finally:
            connection.close()

    def _extract_receipt(self, article_id: int, source_url: str) -> PublicationMetadataReceipt:
        response = self._fetch_with_safe_redirects(source_url)
        if response.status_code < 200 or response.status_code >= 300:
            raise _BackfillError("http_status", str(response.status_code))
        try:
            html = response.body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _BackfillError("response_not_utf8", type(error).__name__) from error
        candidates = extract_date_published_candidates(html)
        if not candidates:
            raise _BackfillError("publication_date_missing")
        field_name, raw_value, normalized = select_verified_date_published(candidates)
        final_url = _validate_url(response.final_url, self.allow_hosts, "final_host_not_allowed")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("clock must return an aware timestamp")
        return PublicationMetadataReceipt(
            article_id=article_id,
            source_url=source_url,
            final_url=final_url,
            field_name=field_name,
            raw_value=raw_value,
            normalized_utc=normalized,
            fetched_at=now.astimezone(timezone.utc).isoformat(),
            body_sha256=sha256(response.body).hexdigest(),
        )

    def _fetch_with_safe_redirects(self, source_url: str) -> FetchResponse:
        current = _validate_url(source_url, self.allow_hosts, "source_host_not_allowed")
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self._fetcher(
                    current,
                    timeout_seconds=self.timeout_seconds,
                    max_body_bytes=self.max_body_bytes,
                )
            except (requests.Timeout, TimeoutError) as error:
                raise _BackfillError("timeout", type(error).__name__) from error
            except requests.RequestException as error:
                raise _BackfillError("fetch_failed", type(error).__name__) from error
            if len(response.body) > self.max_body_bytes:
                raise _BackfillError("response_body_too_large")
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location") or response.headers.get("Location")
            if not location:
                raise _BackfillError("redirect_missing_location")
            current = _validate_url(urljoin(current, location), self.allow_hosts, "redirect_host_not_allowed")
        raise _BackfillError("too_many_redirects")

    def _apply_receipt(self, receipt: PublicationMetadataReceipt) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _create_receipt_table(connection)
            connection.execute(
                f"""
                INSERT INTO {RECEIPT_TABLE}
                (article_id, source_url, final_url, field_name, raw_value, normalized_utc, fetched_at, body_sha256, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.article_id, receipt.source_url, receipt.final_url,
                    receipt.field_name, receipt.raw_value, receipt.normalized_utc,
                    receipt.fetched_at, receipt.body_sha256, receipt.fetched_at,
                ),
            )
            update = connection.execute(
                """
                UPDATE articles
                   SET source_published_at = ?, published_at_provenance = 'publisher_metadata'
                 WHERE id = ? AND published_at_provenance = 'legacy_unverified'
                   AND source_published_at IS NULL
                """,
                (receipt.normalized_utc, receipt.article_id),
            )
            if update.rowcount != 1:
                raise _BackfillError("apply_guard_rejected")
            connection.commit()
        except _BackfillError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise _BackfillError("database_write_failed", type(error).__name__) from error
        finally:
            connection.close()


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[tuple[str, str]] = []
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "script" and (attributes.get("type") or "").split(";", 1)[0].strip().lower() == "application/ld+json":
            self._json_ld_depth += 1
            self._json_ld_parts = []
        if tag.lower() != "meta":
            return
        name = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").strip().lower()
        content = (attributes.get("content") or "").strip()
        if name in _DATE_PUBLISHED_META_NAMES and content:
            self.values.append(("datePublished", content))

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._json_ld_depth:
            return
        self._json_ld_depth -= 1
        if self._json_ld_depth:
            return
        try:
            payload = json.loads("".join(self._json_ld_parts))
        except json.JSONDecodeError:
            return
        for value in _json_ld_date_published_values(payload):
            self.values.append(("datePublished", value))


class _BackfillError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def extract_date_published(html: str) -> tuple[str, str] | None:
    """Backward-compatible first-candidate accessor for callers that need raw HTML order."""

    candidates = extract_date_published_candidates(html)
    return candidates[0] if candidates else None


def extract_date_published_candidates(html: str) -> tuple[tuple[str, str], ...]:
    parser = _MetadataParser()
    parser.feed(html)
    parser.close()
    return tuple(parser.values)


def select_verified_date_published(
    candidates: Iterable[tuple[str, str]],
) -> tuple[str, str, str]:
    """Select one unambiguous offset-aware publisher timestamp from page metadata."""

    valid: list[tuple[str, str, str]] = []
    failures: list[_BackfillError] = []
    for field_name, raw_value in candidates:
        try:
            normalized = normalize_utc_timestamp(raw_value)
        except _BackfillError as error:
            failures.append(error)
            continue
        valid.append((field_name, raw_value, normalized))
    if not valid:
        if any(error.code == "publication_date_not_utc_aware" for error in failures):
            raise _BackfillError("publication_date_not_utc_aware")
        raise _BackfillError("publication_date_invalid")
    normalized_values = {item[2] for item in valid}
    if len(normalized_values) != 1:
        raise _BackfillError("publication_date_conflict")
    return valid[0]


def normalize_utc_timestamp(value: str) -> str:
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError) as error:
            raise _BackfillError("publication_date_invalid", type(error).__name__) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _BackfillError("publication_date_not_utc_aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _json_ld_date_published_values(payload: Any) -> Iterable[str]:
    if isinstance(payload, Mapping):
        value = payload.get("datePublished")
        if isinstance(value, str) and value.strip():
            yield value.strip()
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _json_ld_date_published_values(item)
    elif isinstance(payload, list):
        for item in payload:
            yield from _json_ld_date_published_values(item)


def _normalize_allow_hosts(hosts: Iterable[str]) -> frozenset[str]:
    values = set()
    for host in hosts:
        text = str(host).strip().lower().rstrip(".")
        if not text or ":" in text or "/" in text:
            raise ValueError("allow_hosts must contain exact hostnames without scheme, port, or wildcard")
        values.add(text)
    return frozenset(values)


def _normalize_article_ids(article_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if article_ids is None:
        return None
    values: set[int] = set()
    for article_id in article_ids:
        if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id <= 0:
            raise ValueError("article_ids must contain positive integers")
        values.add(article_id)
    return tuple(sorted(values))


def _validate_url(value: str, allow_hosts: frozenset[str], code: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise _BackfillError("invalid_url")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname not in allow_hosts:
        raise _BackfillError(code, hostname)
    return parsed.geturl()


def _requests_fetch(url: str, *, timeout_seconds: float, max_body_bytes: int) -> FetchResponse:
    with requests.Session() as session:
        response = session.get(url, timeout=timeout_seconds, allow_redirects=False, stream=True)
        body = bytearray()
        for chunk in response.iter_content(chunk_size=min(64 * 1024, max_body_bytes + 1)):
            body.extend(chunk)
            if len(body) > max_body_bytes:
                break
        return FetchResponse(response.status_code, dict(response.headers), bytes(body), response.url)


def _create_receipt_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RECEIPT_TABLE} (
            receipt_id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            final_url TEXT NOT NULL,
            field_name TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            normalized_utc TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            body_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            UNIQUE(article_id, final_url, field_name, raw_value, body_sha256)
        )
        """
    )
