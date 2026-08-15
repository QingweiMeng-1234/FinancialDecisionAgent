"""Canonical article-content fetching and extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from enum import StrEnum
from typing import Mapping

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
    INVALID_CONTENT_TYPE = "invalid_content_type"
    ERROR_PAGE_SUSPECTED = "error_page_suspected"
    CONTENT_TOO_SHORT = "content_too_short"
    TITLE_CONTENT_MISMATCH = "title_content_mismatch"


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
    "about", "after", "against", "amid", "and", "are", "but", "for",
    "from", "has", "have", "how", "into", "its", "new", "not", "over",
    "says", "that", "the", "their", "this", "to", "was", "were", "what",
    "when", "where", "who", "why", "will", "with",
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
        return self.fetch_with_classification(url)

    def fetch_attempt(self, url: str) -> FetchAttempt:
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": "FinancialAgent/1.0"})
        response.raise_for_status()
        content_type = _extract_content_type(response.headers)
        if not is_allowed_content_type(content_type):
            raise ArticleFetchError(
                FetchFailureReason.INVALID_CONTENT_TYPE,
                f"Unsupported publisher content type {content_type or '<missing>'} for {response.url}",
                status_code=response.status_code,
                url=response.url,
            )
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
            content_type=content_type,
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

        validate_extracted_article(
            attempt.extracted_content,
            content_type=attempt.content_type,
            status_code=attempt.status_code,
            url=attempt.response_url,
            html=attempt.html,
        )

        return FetchResult(
            original_url=attempt.response_url,
            canonical_url=attempt.canonical_url,
            content=attempt.extracted_content,
            status_code=attempt.status_code,
            content_type=attempt.content_type,
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
    if title_tokens & content_tokens or len(description_tokens & content_tokens) >= 2:
        return
    raise ArticleFetchError(
        FetchFailureReason.TITLE_CONTENT_MISMATCH,
        f"Publisher content does not share meaningful terms with the source title or description for {url}",
        url=url,
    )


def is_allowed_content_type(content_type: str) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized in ALLOWED_CONTENT_TYPES


def _extract_content_type(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    return (headers.get("Content-Type") or headers.get("content-type") or "").split(";", 1)[0].strip().lower()


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
