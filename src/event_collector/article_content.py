"""Canonical article-content fetching and extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import json
import math
import re
import socket
import threading
import time
from enum import StrEnum
from typing import Callable, Iterable, Mapping
from urllib.parse import quote, urljoin, urlsplit

import requests
import trafilatura
import urllib3

from event_collector.news_storage import normalize_url


class FetchFailureReason(StrEnum):
    HTTP_401_403 = "http_401_403"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    HTTP_4XX = "http_4xx"
    PAYWALL_SUSPECTED = "paywall_suspected"
    DYNAMIC_PAGE_SUSPECTED = "dynamic_page_suspected"
    EXTRACT_EMPTY = "extract_empty"
    TRUNCATED_PREVIEW_SUSPECTED = "truncated_preview_suspected"
    NETWORK_ERROR = "network_error"
    DUPLICATE_URL_CONFLICT = "duplicate_url_conflict"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    ERROR_PAGE_SUSPECTED = "error_page_suspected"
    CONTENT_TOO_SHORT = "content_too_short"
    TITLE_CONTENT_MISMATCH = "title_content_mismatch"
    INVALID_OR_PRIVATE_URL = "invalid_or_private_url"
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_CONNECTION_ERROR = "network_connection_error"
    RESPONSE_BODY_TOO_LARGE = "response_body_too_large"
    DNS_RESOLUTION_ERROR = "dns_resolution_error"
    DNS_NONPUBLIC_RESOLUTION = "dns_nonpublic_resolution"


PAYWALL_MARKERS = (
    "paywall",
    "subscribe to continue",
    "subscription required",
    "subscriber-only",
    "subscriber only",
    "sign in to continue",
    "already a subscriber",
    "premium content",
    "become a subscriber",
)

DYNAMIC_PAGE_MARKERS = (
    'id="__next"',
    "id='__next'",
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "data-reactroot",
    "__nuxt",
    "window.__initial_state__",
    "__apollo_state__",
    "enable javascript",
    "loading...",
    "skeleton",
)

TRUNCATED_PREVIEW_MAX_LENGTH = 320
MIN_ARTICLE_CONTENT_LENGTH = 200
CONTENT_VALIDATOR_VERSION = "article-content-v1"
EXTRACTOR_VERSION = "trafilatura-v1"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 300
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DOH_BOOTSTRAP_ADDRESSES = ("1.1.1.1", "1.0.0.1")
DOH_SERVER_NAME = "cloudflare-dns.com"
MAX_DOH_RESPONSE_BYTES = 256 * 1024

ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
)

ERROR_PAGE_MARKERS = (
    "javascript is disabled in your browser",
    "please enable javascript to proceed",
    "access denied",
    "request blocked",
    "page not found",
    "the page you requested could not be found",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "verify you are human",
    "checking your browser before accessing",
)

TITLE_STOPWORDS = {
    "about",
    "after",
    "against",
    "amid",
    "and",
    "are",
    "but",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "its",
    "new",
    "not",
    "over",
    "says",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "who",
    "why",
    "will",
    "with",
}


@dataclass
class FetchResult:
    original_url: str
    canonical_url: str | None
    content: str
    status_code: int = 200
    content_type: str = "text/html"
    extractor_version: str = EXTRACTOR_VERSION
    validation_version: str = CONTENT_VALIDATOR_VERSION


@dataclass
class FetchAttempt:
    requested_url: str
    response_url: str
    status_code: int
    html: str
    extracted_content: str
    canonical_url: str | None
    content_type: str = ""


class ArticleFetchError(RuntimeError):
    def __init__(
        self,
        reason: FetchFailureReason,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        retryable: bool | None = None,
        retry_after_seconds: int | None = None,
        retry_after_at: datetime | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.url = url
        self.retryable = _is_retryable_reason(reason) if retryable is None else retryable
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_at = retry_after_at


class HostnameRequestLimiter:
    """Conservatively pace transport starts independently for each hostname.

    A reservation is recorded before sleeping, so concurrent callers sharing a
    hostname cannot both start a request in the same interval.  The lock only
    protects that reservation; it is never held while sleeping, which leaves
    unrelated hostnames independent.
    """

    def __init__(
        self,
        min_interval_seconds: int | float = 0,
        *,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        try:
            interval = float(min_interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("min_interval_seconds must be a non-negative number") from exc
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("min_interval_seconds must be a non-negative finite number")
        self.min_interval_seconds = interval
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._next_allowed_by_hostname: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait_for_hostname(self, hostname: str) -> None:
        """Sleep only until this hostname's reserved next transport start."""
        if self.min_interval_seconds == 0:
            return
        normalized_hostname = _normalize_hostname(hostname)
        now = float(self._monotonic_clock())
        if not math.isfinite(now):
            raise ValueError("monotonic_clock must return a finite number")
        with self._lock:
            scheduled_at = max(now, self._next_allowed_by_hostname.get(normalized_hostname, now))
            self._next_allowed_by_hostname[normalized_hostname] = scheduled_at + self.min_interval_seconds
        remaining = scheduled_at - now
        if remaining > 0:
            self._sleeper(remaining)


class _PinnedTransportResponse:
    """Small requests-compatible view over one pinned urllib3 response."""

    def __init__(self, raw_response: object, *, url: str):
        self._raw_response = raw_response
        self.url = url
        self.status_code = getattr(raw_response, "status", None)
        self.headers = getattr(raw_response, "headers", {})
        self.encoding = None

    def raise_for_status(self) -> None:
        if self.status_code is None or self.status_code < 400:
            return
        response = requests.Response()
        response.status_code = self.status_code
        response.url = self.url
        response.headers = self.headers
        raise requests.HTTPError(
            f"HTTP {self.status_code} while fetching publisher content",
            response=response,
        )

    def iter_content(self, chunk_size: int):
        try:
            yield from self._raw_response.stream(chunk_size, decode_content=True)
        except urllib3.exceptions.TimeoutError as exc:
            raise requests.Timeout("Pinned publisher connection timed out") from exc
        except urllib3.exceptions.HTTPError as exc:
            raise requests.ConnectionError("Pinned publisher connection failed") from exc
        finally:
            self.close()

    def close(self) -> None:
        close = getattr(self._raw_response, "close", None)
        if callable(close):
            close()


class PinnedPublicAddressTransport:
    """Direct HTTP(S) transport that never lets a publisher hostname re-resolve.

    HTTPS pools connect to a validated numeric address but preserve both TLS SNI
    and certificate hostname verification for the original URL hostname.
    Redirects are deliberately not followed here; the fetcher validates and
    pins every redirect target as a separate request.
    """

    def __init__(
        self,
        *,
        https_pool_factory: Callable[..., object] = urllib3.HTTPSConnectionPool,
        http_pool_factory: Callable[..., object] = urllib3.HTTPConnectionPool,
    ):
        self._https_pool_factory = https_pool_factory
        self._http_pool_factory = http_pool_factory

    def get(
        self,
        url: str,
        *,
        addresses: tuple[str, ...],
        timeout: tuple[float, float],
        headers: Mapping[str, str],
    ) -> _PinnedTransportResponse:
        parsed = _parse_fetch_url(url)
        hostname = parsed.hostname
        assert hostname is not None
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        request_headers = dict(headers)
        request_headers["Host"] = parsed.netloc
        request_timeout = urllib3.Timeout(connect=timeout[0], read=timeout[1])

        last_error: Exception | None = None
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except (TypeError, ValueError) as exc:
                raise ValueError("Pinned transport received an invalid address") from exc
            if not is_globally_routable_address(ip):
                raise ValueError("Pinned transport received a non-public address")
            try:
                if parsed.scheme.lower() == "https":
                    pool = self._https_pool_factory(
                        ip.compressed,
                        port=port,
                        cert_reqs="CERT_REQUIRED",
                        ca_certs=requests.certs.where(),
                        server_hostname=hostname,
                        assert_hostname=hostname,
                    )
                else:
                    pool = self._http_pool_factory(ip.compressed, port=port)
                raw_response = pool.urlopen(
                    "GET",
                    target,
                    headers=request_headers,
                    retries=False,
                    redirect=False,
                    preload_content=False,
                    timeout=request_timeout,
                )
                return _PinnedTransportResponse(raw_response, url=url)
            except urllib3.exceptions.TimeoutError as exc:
                last_error = requests.Timeout("Pinned publisher connection timed out")
                last_error.__cause__ = exc
            except urllib3.exceptions.HTTPError as exc:
                last_error = requests.ConnectionError("Pinned publisher connection failed")
                last_error.__cause__ = exc
        if last_error is not None:
            raise last_error
        raise requests.ConnectionError("Pinned publisher connection had no usable address")


class ArticleContentFetcher:
    """Fetch article pages with URL/public-IP preflight and redirect guards.

    Each requested URL (including redirect targets) is parsed as an absolute
    HTTP(S) URL and DNS-preflighted before it reaches the injected transport.
    The default production path pins every connection to a preflighted public
    address, preserving TLS SNI and certificate hostname validation.  An
    injected ``session`` remains an explicit test/integration escape hatch and
    is responsible for equivalent DNS-rebinding protection itself.
    """

    def __init__(
        self,
        timeout: int | float | tuple[int | float, int | float] | None = None,
        *,
        resolver: Callable[[str], Iterable[str]] | None = None,
        session: object | None = None,
        transport: PinnedPublicAddressTransport | object | None = None,
        max_redirects: int = 5,
        connect_timeout_seconds: int | float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: int | float = DEFAULT_READ_TIMEOUT_SECONDS,
        default_retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
        max_retry_after_seconds: int = MAX_RETRY_AFTER_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        clock: Callable[[], datetime] | None = None,
        min_interval_seconds: int | float = 0,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        hostname_limiter: HostnameRequestLimiter | None = None,
    ):
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        if timeout is not None:
            if isinstance(timeout, tuple):
                if len(timeout) != 2:
                    raise ValueError("timeout tuple must contain connect and read values")
                connect_timeout_seconds, read_timeout_seconds = timeout
            else:
                connect_timeout_seconds = read_timeout_seconds = timeout
        self.timeout = _validate_timeout_pair(connect_timeout_seconds, read_timeout_seconds)
        if max_retry_after_seconds <= 0:
            raise ValueError("max_retry_after_seconds must be positive")
        if default_retry_after_seconds <= 0:
            raise ValueError("default_retry_after_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.default_retry_after_seconds = min(default_retry_after_seconds, max_retry_after_seconds)
        self.max_retry_after_seconds = max_retry_after_seconds
        self.max_response_bytes = max_response_bytes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if session is not None and transport is not None:
            raise ValueError("session and transport are mutually exclusive")
        self._resolver = resolver or resolve_hostname_addresses
        self._session = session
        self._transport = transport or (PinnedPublicAddressTransport() if session is None else None)
        # This cache is intentionally instance-local: it amortizes DNS/DoH
        # during one refresh without turning a prior run's network observation
        # into durable authority for a later run.
        self._verified_addresses_by_hostname: dict[str, tuple[str, ...]] = {}
        self._verified_addresses_lock = threading.Lock()
        self.max_redirects = max_redirects
        self._hostname_limiter = hostname_limiter or HostnameRequestLimiter(
            min_interval_seconds,
            monotonic_clock=monotonic_clock,
            sleeper=sleeper,
        )

    def fetch(self, url: str) -> FetchResult:
        return self.fetch_with_classification(url)

    def fetch_attempt(self, url: str) -> FetchAttempt:
        response = self._request_with_safe_redirects(url)
        response.raise_for_status()
        content_type = _extract_content_type(response.headers)
        if not is_allowed_content_type(content_type):
            raise ArticleFetchError(
                FetchFailureReason.INVALID_CONTENT_TYPE,
                f"Unsupported publisher content type {content_type or '<missing>'} for {response.url}",
                status_code=response.status_code,
                url=response.url,
            )
        self._ensure_response_body_within_limit(response)
        html = self._read_response_body(response)
        extracted = trafilatura.extract(html, include_links=False, include_formatting=False)
        cleaned = " ".join((extracted or "").split()).strip()
        canonical_url = extract_canonical_url(html, response.url)
        if canonical_url is not None:
            self._validate_public_url(canonical_url)
        return FetchAttempt(
            requested_url=url,
            response_url=response.url,
            status_code=response.status_code,
            html=html,
            extracted_content=cleaned,
            canonical_url=canonical_url,
            content_type=content_type,
        )

    def _request_with_safe_redirects(self, url: str):
        current_url = url
        for hop in range(self.max_redirects + 1):
            addresses = self._validate_public_url(current_url)
            current_hostname = _parse_fetch_url(current_url).hostname
            assert current_hostname is not None
            self._hostname_limiter.wait_for_hostname(current_hostname)
            headers = {"User-Agent": "FinancialAgent/1.0"}
            if self._session is not None:
                response = self._session.get(
                    current_url,
                    timeout=self.timeout,
                    headers=headers,
                    allow_redirects=False,
                    stream=True,
                )
            else:
                assert self._transport is not None
                response = self._transport.get(
                    current_url,
                    addresses=addresses,
                    timeout=self.timeout,
                    headers=headers,
                )
            response_url = getattr(response, "url", None)
            self._validate_public_url(response_url)
            if not _is_redirect_response(response):
                return response
            # ``stream=True`` leaves this hop's response connection open.
            # Its body is never consumed because redirects are handled
            # manually, so close before *any* redirect terminal branch.
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if hop >= self.max_redirects:
                raise ArticleFetchError(
                    FetchFailureReason.NETWORK_ERROR,
                    f"Too many redirects while fetching {url}",
                    url=response_url,
                    retryable=False,
                )
            location = _header_value(getattr(response, "headers", None), "Location")
            if not location:
                raise ArticleFetchError(
                    FetchFailureReason.NETWORK_ERROR,
                    f"Redirect response without a Location header while fetching {response_url}",
                    status_code=getattr(response, "status_code", None),
                    url=response_url,
                    retryable=False,
                )
            current_url = urljoin(response_url, location)
        raise AssertionError("redirect loop exited unexpectedly")

    def _ensure_response_body_within_limit(self, response: object) -> None:
        content_length = _parse_content_length(_header_value(getattr(response, "headers", None), "Content-Length"))
        if content_length is not None and content_length > self.max_response_bytes:
            raise _body_too_large_error(response, self.max_response_bytes)

    def _read_response_body(self, response: object) -> str:
        """Read at most the configured response-body limit before extraction.

        ``requests`` receives response headers first because requests are made
        with ``stream=True``.  The fallback exists only for deterministic fake
        transports that expose an already-decoded ``text`` attribute.
        """
        stream = getattr(response, "iter_content", None)
        if callable(stream):
            body = bytearray()
            for chunk in stream(chunk_size=1024):
                if not chunk:
                    continue
                if len(body) + len(chunk) > self.max_response_bytes:
                    raise _body_too_large_error(response, self.max_response_bytes)
                body.extend(chunk)
            encoding = getattr(response, "encoding", None) or "utf-8"
            return bytes(body).decode(encoding, errors="replace")

        body = getattr(response, "content", None)
        if body is not None:
            if len(body) > self.max_response_bytes:
                raise _body_too_large_error(response, self.max_response_bytes)
            encoding = getattr(response, "encoding", None) or "utf-8"
            return bytes(body).decode(encoding, errors="replace")
        html = getattr(response, "text", "")
        if len(html.encode("utf-8")) > self.max_response_bytes:
            raise _body_too_large_error(response, self.max_response_bytes)
        return html

    def _validate_public_url(self, url: str | None) -> tuple[str, ...]:
        parsed = _parse_fetch_url(url)
        hostname = parsed.hostname
        assert hostname is not None
        if _is_local_or_metadata_hostname(hostname):
            raise _unsafe_url_error(url, "localhost or metadata endpoint hostname")
        try:
            literal_ip = ipaddress.ip_address(hostname)
        except ValueError:
            literal_ip = None
        if literal_ip is not None:
            if not is_globally_routable_address(literal_ip):
                raise _unsafe_url_error(url, f"URL uses non-public address {literal_ip.compressed}")
            return (literal_ip.compressed,)
        return self._resolve_and_cache_verified_addresses(url, hostname)

    def _resolve_and_cache_verified_addresses(self, url: str | None, hostname: str) -> tuple[str, ...]:
        normalized_hostname = _normalize_hostname(hostname)
        # Hold the small instance-local lock through first resolution so two
        # concurrent refresh workers cannot duplicate an expensive DoH lookup.
        # It is never shared across fetcher instances or persisted.
        with self._verified_addresses_lock:
            cached = self._verified_addresses_by_hostname.get(normalized_hostname)
            if cached is not None:
                return cached
            addresses = self._resolve_verified_addresses_uncached(url, normalized_hostname)
            self._verified_addresses_by_hostname[normalized_hostname] = addresses
            return addresses

    def _resolve_verified_addresses_uncached(
        self,
        url: str | None,
        normalized_hostname: str,
    ) -> tuple[str, ...]:
        try:
            addresses = tuple(self._resolver(normalized_hostname))
        except (OSError, ValueError, TypeError) as exc:
            raise _dns_resolution_error(
                url,
                "hostname could not be resolved",
                retry_after_seconds=self.default_retry_after_seconds,
            ) from exc
        if not addresses:
            raise _dns_resolution_error(
                url,
                "hostname did not resolve to an address",
                retry_after_seconds=self.default_retry_after_seconds,
            )
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except (TypeError, ValueError) as exc:
                raise _dns_resolution_error(
                    url,
                    "resolver returned an invalid address",
                    retry_after_seconds=self.default_retry_after_seconds,
                ) from exc
            if not is_globally_routable_address(ip):
                raise _dns_nonpublic_resolution_error(
                    url,
                    retry_after_seconds=self.default_retry_after_seconds,
                )
        return addresses

    def fetch_with_classification(self, url: str) -> FetchResult:
        try:
            attempt = self.fetch_attempt(url)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            response = exc.response
            if status_code in {401, 403}:
                raise ArticleFetchError(
                    FetchFailureReason.HTTP_401_403,
                    f"HTTP {status_code} while fetching {url}",
                    status_code=status_code,
                    url=url,
                ) from exc
            if status_code == 429:
                retry_after_seconds, retry_after_at = self._retry_after_contract(response)
                raise ArticleFetchError(
                    FetchFailureReason.HTTP_429,
                    f"HTTP 429 while fetching {url}",
                    status_code=status_code,
                    url=url,
                    retryable=True,
                    retry_after_seconds=retry_after_seconds,
                    retry_after_at=retry_after_at,
                ) from exc
            if status_code is not None and 500 <= status_code <= 599:
                retry_after_seconds, retry_after_at = self._retry_after_contract(response)
                raise ArticleFetchError(
                    FetchFailureReason.HTTP_5XX,
                    f"HTTP {status_code} while fetching {url}",
                    status_code=status_code,
                    url=url,
                    retryable=True,
                    retry_after_seconds=retry_after_seconds,
                    retry_after_at=retry_after_at,
                ) from exc
            if status_code is not None and 400 <= status_code <= 499:
                raise ArticleFetchError(
                    FetchFailureReason.HTTP_4XX,
                    f"HTTP {status_code} while fetching {url}",
                    status_code=status_code,
                    url=url,
                    retryable=False,
                ) from exc
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_ERROR,
                f"HTTP error while fetching {url}: {exc}",
                status_code=status_code,
                url=url,
                retryable=True,
                retry_after_seconds=self.default_retry_after_seconds,
            ) from exc
        except requests.Timeout as exc:
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_TIMEOUT,
                f"Network timeout while fetching {url}: {exc}",
                url=url,
                retryable=True,
                retry_after_seconds=self.default_retry_after_seconds,
            ) from exc
        except requests.ConnectionError as exc:
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_CONNECTION_ERROR,
                f"Network connection error while fetching {url}: {exc}",
                url=url,
                retryable=True,
                retry_after_seconds=self.default_retry_after_seconds,
            ) from exc
        except requests.RequestException as exc:
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_ERROR,
                f"Network error while fetching {url}: {exc}",
                url=url,
                retryable=True,
                retry_after_seconds=self.default_retry_after_seconds,
            ) from exc

        try:
            validate_extracted_article(
                attempt.extracted_content,
                content_type=attempt.content_type,
                status_code=attempt.status_code,
                url=attempt.response_url,
                html=attempt.html,
            )
        except ArticleFetchError:
            raise

        return FetchResult(
            original_url=attempt.response_url,
            canonical_url=attempt.canonical_url,
            content=attempt.extracted_content,
            status_code=attempt.status_code,
            content_type=attempt.content_type,
        )

    def _retry_after_contract(self, response: object | None) -> tuple[int, datetime | None]:
        return parse_retry_after(
            _header_value(getattr(response, "headers", None), "Retry-After"),
            now=self._clock(),
            default_seconds=self.default_retry_after_seconds,
            max_seconds=self.max_retry_after_seconds,
        )


def validate_extracted_article(
    content: str,
    *,
    content_type: str,
    status_code: int,
    url: str,
    html: str = "",
) -> None:
    """Fail closed when publisher output cannot be evidenced as article text."""
    if not is_allowed_content_type(content_type):
        raise ArticleFetchError(
            FetchFailureReason.INVALID_CONTENT_TYPE,
            f"Unsupported publisher content type {content_type or '<missing>'} for {url}",
            status_code=status_code,
            url=url,
        )

    cleaned = " ".join((content or "").split()).strip()
    if not cleaned:
        reason = classify_empty_extraction(html)
        raise ArticleFetchError(
            reason,
            f"Could not extract article content from {url}",
            status_code=status_code,
            url=url,
        )

    leading_text = cleaned[:800].lower()
    if any(marker in leading_text for marker in ERROR_PAGE_MARKERS):
        raise ArticleFetchError(
            FetchFailureReason.ERROR_PAGE_SUSPECTED,
            f"Publisher output looks like an error or browser-interstitial page for {url}",
            status_code=status_code,
            url=url,
        )
    if len(cleaned) < MIN_ARTICLE_CONTENT_LENGTH:
        raise ArticleFetchError(
            FetchFailureReason.CONTENT_TOO_SHORT,
            f"Extracted article content is shorter than {MIN_ARTICLE_CONTENT_LENGTH} characters for {url}",
            status_code=status_code,
            url=url,
        )
    if is_suspected_truncated_preview(cleaned):
        raise ArticleFetchError(
            FetchFailureReason.TRUNCATED_PREVIEW_SUSPECTED,
            f"Extracted content looks like a truncated preview for {url}",
            status_code=status_code,
            url=url,
        )


def validate_title_content_alignment(title: str, description: str, content: str, *, url: str) -> None:
    """Require deterministic lexical evidence that fetched text belongs to the NewsAPI item."""
    content_tokens = set(_meaningful_tokens(content[:8000]))
    title_tokens = set(_meaningful_tokens(title))
    description_tokens = set(_meaningful_tokens(description))
    if len(title_tokens) < 3 and len(description_tokens) < 3:
        return
    title_overlap = title_tokens & content_tokens
    description_overlap = description_tokens & content_tokens
    if title_overlap or len(description_overlap) >= 2:
        return
    raise ArticleFetchError(
        FetchFailureReason.TITLE_CONTENT_MISMATCH,
        f"Publisher content does not share meaningful terms with the source title or description for {url}",
        url=url,
    )


def is_allowed_content_type(content_type: str) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized in ALLOWED_CONTENT_TYPES


def parse_retry_after(
    value: str,
    *,
    now: datetime,
    default_seconds: int,
    max_seconds: int,
) -> tuple[int, datetime | None]:
    """Return a bounded retry delay without sleeping or scheduling a retry.

    Delta-seconds are deliberately represented only as a duration.  HTTP-date
    values retain the resulting absolute time so a later retry worker can
    persist or compare it without depending on this process's wall clock.
    """
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    candidate = (value or "").strip()
    if candidate.isdecimal():
        return min(int(candidate), max_seconds), None
    if candidate:
        try:
            retry_at = parsedate_to_datetime(candidate)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            else:
                retry_at = retry_at.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError, OverflowError):
            retry_at = None
        if retry_at is not None:
            seconds = min(max(0, int((retry_at - now).total_seconds())), max_seconds)
            return seconds, now + timedelta(seconds=seconds)
    return min(default_seconds, max_seconds), None


def _validate_timeout_pair(connect_timeout: int | float, read_timeout: int | float) -> tuple[float, float]:
    try:
        pair = (float(connect_timeout), float(read_timeout))
    except (TypeError, ValueError) as exc:
        raise ValueError("connect and read timeout values must be numbers") from exc
    if pair[0] <= 0 or pair[1] <= 0:
        raise ValueError("connect and read timeout values must be positive")
    return pair


def _is_retryable_reason(reason: FetchFailureReason) -> bool:
    return reason in {
        FetchFailureReason.NETWORK_ERROR,
        FetchFailureReason.NETWORK_TIMEOUT,
        FetchFailureReason.NETWORK_CONNECTION_ERROR,
        FetchFailureReason.HTTP_429,
        FetchFailureReason.HTTP_5XX,
        FetchFailureReason.DNS_RESOLUTION_ERROR,
        FetchFailureReason.DNS_NONPUBLIC_RESOLUTION,
    }


def _parse_content_length(value: str) -> int | None:
    if not value or not value.isdecimal():
        return None
    return int(value)


def _body_too_large_error(response: object, max_response_bytes: int) -> ArticleFetchError:
    response_url = getattr(response, "url", None)
    return ArticleFetchError(
        FetchFailureReason.RESPONSE_BODY_TOO_LARGE,
        f"Publisher response exceeds the {max_response_bytes}-byte fetch limit for {response_url}",
        status_code=getattr(response, "status_code", None),
        url=response_url,
        retryable=False,
    )


def _extract_content_type(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    return (headers.get("Content-Type") or headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def resolve_hostname_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve a publisher hostname, bypassing local fake-IP answers safely.

    A normal public local DNS answer is used directly.  Empty, failed, or
    non-public local resolution is re-checked through Cloudflare DoH using a
    fixed, certificate-verified bootstrap IP.  The returned addresses are only
    safe because :class:`PinnedPublicAddressTransport` connects to them
    directly; callers must not pass them to a hostname-resolving transport.
    """
    local_error: Exception | None = None
    try:
        local_addresses = tuple(
            dict.fromkeys(
                result[4][0]
                for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            )
        )
    except (OSError, ValueError, TypeError) as exc:
        local_addresses = ()
        local_error = exc

    if local_addresses and _all_globally_routable_addresses(local_addresses):
        return local_addresses
    try:
        return resolve_hostname_addresses_via_doh(hostname)
    except (OSError, ValueError, TypeError) as exc:
        if local_error is not None:
            raise OSError("publisher DNS resolution and DoH fallback failed") from exc
        if local_addresses:
            # Preserve a non-public answer for the caller's fail-closed,
            # retryable DNS_NONPUBLIC_RESOLUTION classification.
            return local_addresses
        raise OSError("publisher DoH DNS fallback failed") from exc


def resolve_hostname_addresses_via_doh(hostname: str) -> tuple[str, ...]:
    """Query A and AAAA records through certificate-verified Cloudflare DoH.

    The resolver endpoint is bootstrapped by fixed public addresses and never
    by the host's potentially fake local DNS.  No redirect or proxy path is
    followed.  Failure deliberately raises instead of returning a guessed IP.
    """
    answers: list[str] = []
    last_error: Exception | None = None
    for record_type in ("A", "AAAA"):
        payload: Mapping[str, object] | None = None
        for bootstrap_address in DOH_BOOTSTRAP_ADDRESSES:
            response = None
            try:
                pool = urllib3.HTTPSConnectionPool(
                    bootstrap_address,
                    port=443,
                    cert_reqs="CERT_REQUIRED",
                    ca_certs=requests.certs.where(),
                    server_hostname=DOH_SERVER_NAME,
                    assert_hostname=DOH_SERVER_NAME,
                )
                response = pool.urlopen(
                    "GET",
                    f"/dns-query?name={quote(hostname, safe='')}&type={record_type}",
                    headers={
                        "Accept": "application/dns-json",
                        "Host": DOH_SERVER_NAME,
                    },
                    retries=False,
                    redirect=False,
                    preload_content=True,
                    timeout=urllib3.Timeout(
                        connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
                        read=DEFAULT_READ_TIMEOUT_SECONDS,
                    ),
                )
                if getattr(response, "status", None) != 200:
                    raise OSError("DoH resolver returned a non-success status")
                raw_payload = getattr(response, "data", b"")
                if not isinstance(raw_payload, bytes) or len(raw_payload) > MAX_DOH_RESPONSE_BYTES:
                    raise OSError("DoH resolver returned an invalid response body")
                decoded = json.loads(raw_payload.decode("utf-8"))
                if not isinstance(decoded, Mapping):
                    raise OSError("DoH resolver returned an invalid payload")
                payload = decoded
                break
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, urllib3.exceptions.HTTPError) as exc:
                last_error = exc
            finally:
                release = getattr(response, "release_conn", None)
                if callable(release):
                    release()
        if payload is None:
            continue
        raw_answers = payload.get("Answer", ())
        if not isinstance(raw_answers, list):
            continue
        expected_type = 1 if record_type == "A" else 28
        for record in raw_answers:
            if not isinstance(record, Mapping) or record.get("type") != expected_type:
                continue
            address = record.get("data")
            if not isinstance(address, str):
                continue
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            answers.append(address)
    resolved = tuple(dict.fromkeys(answers))
    if resolved:
        return resolved
    if last_error is not None:
        raise OSError("DoH resolver did not return an address") from last_error
    return ()


def _all_globally_routable_addresses(addresses: Iterable[str]) -> bool:
    try:
        return all(is_globally_routable_address(ipaddress.ip_address(address)) for address in addresses)
    except (TypeError, ValueError):
        return False


def _parse_fetch_url(url: str | None):
    if not isinstance(url, str) or not url or url != url.strip() or any(ord(char) < 32 for char in url):
        raise _unsafe_url_error(url, "URL is empty or contains control characters")
    try:
        parsed = urlsplit(url)
        # Accessing port explicitly detects malformed values such as ':bad'.
        _ = parsed.port
    except ValueError as exc:
        raise _unsafe_url_error(url, "URL contains an invalid port") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise _unsafe_url_error(url, "URL must be absolute HTTP(S) with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise _unsafe_url_error(url, "URL userinfo is not allowed")
    if any(char.isspace() for char in parsed.hostname):
        raise _unsafe_url_error(url, "URL hostname contains whitespace")
    return parsed


def _unsafe_url_error(url: str | None, detail: str) -> ArticleFetchError:
    return ArticleFetchError(
        FetchFailureReason.INVALID_OR_PRIVATE_URL,
        f"Rejected unsafe article URL ({detail}): {url!r}",
        url=url,
        retryable=False,
    )


def _dns_resolution_error(
    url: str | None,
    detail: str,
    *,
    retry_after_seconds: int,
) -> ArticleFetchError:
    """Return a retryable DNS-preflight failure without exposing resolver output."""
    return ArticleFetchError(
        FetchFailureReason.DNS_RESOLUTION_ERROR,
        f"Publisher hostname DNS preflight failed ({detail}) for {url!r}",
        url=url,
        retryable=True,
        retry_after_seconds=retry_after_seconds,
    )


def _dns_nonpublic_resolution_error(
    url: str | None,
    *,
    retry_after_seconds: int,
) -> ArticleFetchError:
    """Never connect to a resolver answer that is not globally routable."""
    return ArticleFetchError(
        FetchFailureReason.DNS_NONPUBLIC_RESOLUTION,
        f"Publisher hostname DNS preflight returned no public address for {url!r}",
        url=url,
        retryable=True,
        retry_after_seconds=retry_after_seconds,
    )


def _is_local_or_metadata_hostname(hostname: str) -> bool:
    normalized = _normalize_hostname(hostname)
    return normalized == "localhost" or normalized.endswith(".localhost") or normalized in {
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.aws.internal",
    }


def _normalize_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def is_globally_routable_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject every non-unicast/special-use class, including multicast.

    ``ipaddress.is_global`` alone is not sufficient here because it may return
    true for multicast addresses, which must never be valid fetch targets.
    """
    return not any(
        (
            not address.is_global,
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
            getattr(address, "is_site_local", False),
        )
    )


def _is_redirect_response(response: object) -> bool:
    return getattr(response, "status_code", None) in {301, 302, 303, 307, 308}


def _header_value(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    return (headers.get(name) or headers.get(name.lower()) or "").strip()


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) >= 3 and token not in TITLE_STOPWORDS and not token.isdigit()
    ]


def extract_canonical_url(html: str, fallback_url: str) -> str | None:
    match = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
            html,
            flags=re.IGNORECASE,
        )
    candidate = urljoin(fallback_url, match.group(1).strip()) if match else fallback_url
    normalized = normalize_url(candidate)
    return normalized or None


def classify_empty_extraction(html: str) -> FetchFailureReason:
    lowered = (html or "").lower()
    if any(marker in lowered for marker in PAYWALL_MARKERS):
        return FetchFailureReason.PAYWALL_SUSPECTED
    if looks_like_dynamic_page(lowered):
        return FetchFailureReason.DYNAMIC_PAGE_SUSPECTED
    return FetchFailureReason.EXTRACT_EMPTY


def looks_like_dynamic_page(lowered_html: str) -> bool:
    script_count = lowered_html.count("<script")
    placeholder_signals = sum(1 for marker in DYNAMIC_PAGE_MARKERS if marker in lowered_html)
    if placeholder_signals >= 2:
        return True
    return script_count >= 8 and placeholder_signals >= 1


def is_suspected_truncated_preview(content: str) -> bool:
    cleaned = " ".join((content or "").split()).strip()
    if not cleaned:
        return False
    if len(cleaned) > TRUNCATED_PREVIEW_MAX_LENGTH:
        return False
    if not cleaned.endswith("..."):
        return False
    if re.search(r"\b[A-Z][a-z]{0,20}\.\.\.$", cleaned):
        return True
    return True
