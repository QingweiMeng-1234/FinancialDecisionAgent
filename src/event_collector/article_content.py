"""Canonical article-content fetching and extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from enum import StrEnum

import requests
import trafilatura

from event_collector.news_storage import normalize_url


class FetchFailureReason(StrEnum):
    HTTP_401_403 = "http_401_403"
    PAYWALL_SUSPECTED = "paywall_suspected"
    DYNAMIC_PAGE_SUSPECTED = "dynamic_page_suspected"
    EXTRACT_EMPTY = "extract_empty"
    TRUNCATED_PREVIEW_SUSPECTED = "truncated_preview_suspected"
    NETWORK_ERROR = "network_error"
    DUPLICATE_URL_CONFLICT = "duplicate_url_conflict"


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


@dataclass
class FetchResult:
    original_url: str
    canonical_url: str | None
    content: str


@dataclass
class FetchAttempt:
    requested_url: str
    response_url: str
    status_code: int
    html: str
    extracted_content: str
    canonical_url: str | None


class ArticleFetchError(RuntimeError):
    def __init__(
        self,
        reason: FetchFailureReason,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.url = url


class ArticleContentFetcher:
    """Fetch article pages and extract canonical text content."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResult:
        attempt = self.fetch_attempt(url)
        if not attempt.extracted_content:
            raise ValueError(f"Could not extract article content from {url}")
        if is_suspected_truncated_preview(attempt.extracted_content):
            raise ValueError(f"Extracted content looks like a truncated preview for {url}")
        return FetchResult(
            original_url=attempt.response_url,
            canonical_url=attempt.canonical_url,
            content=attempt.extracted_content,
        )

    def fetch_attempt(self, url: str) -> FetchAttempt:
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": "FinancialAgent/1.0"})
        response.raise_for_status()
        html = response.text
        extracted = trafilatura.extract(html, include_links=False, include_formatting=False)
        cleaned = " ".join((extracted or "").split()).strip()
        canonical_url = extract_canonical_url(html, response.url)
        return FetchAttempt(
            requested_url=url,
            response_url=response.url,
            status_code=response.status_code,
            html=html,
            extracted_content=cleaned,
            canonical_url=canonical_url,
        )

    def fetch_with_classification(self, url: str) -> FetchResult:
        try:
            attempt = self.fetch_attempt(url)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {401, 403}:
                raise ArticleFetchError(
                    FetchFailureReason.HTTP_401_403,
                    f"HTTP {status_code} while fetching {url}",
                    status_code=status_code,
                    url=url,
                ) from exc
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_ERROR,
                f"HTTP error while fetching {url}: {exc}",
                status_code=status_code,
                url=url,
            ) from exc
        except requests.RequestException as exc:
            raise ArticleFetchError(
                FetchFailureReason.NETWORK_ERROR,
                f"Network error while fetching {url}: {exc}",
                url=url,
            ) from exc

        if not attempt.extracted_content:
            reason = classify_empty_extraction(attempt.html)
            raise ArticleFetchError(
                reason,
                f"Could not extract article content from {url}",
                status_code=attempt.status_code,
                url=attempt.response_url,
            )
        if is_suspected_truncated_preview(attempt.extracted_content):
            raise ArticleFetchError(
                FetchFailureReason.TRUNCATED_PREVIEW_SUSPECTED,
                f"Extracted content looks like a truncated preview for {url}",
                status_code=attempt.status_code,
                url=attempt.response_url,
            )

        return FetchResult(
            original_url=attempt.response_url,
            canonical_url=attempt.canonical_url,
            content=attempt.extracted_content,
        )


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
    candidate = match.group(1).strip() if match else fallback_url
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
