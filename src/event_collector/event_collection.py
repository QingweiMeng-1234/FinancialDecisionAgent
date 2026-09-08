from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import os
import uuid
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from event_collector.article_content import parse_retry_after
from event_collector.errors import InvalidEventSourceError, InvalidEventTextError, MissingAPIKeyError
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.vector_store import VectorStore


_ORIGINAL_REQUESTS_GET = requests.get


class EventSource(Enum):
    MANUAL = "manual"
    NEWS = "news"
    API = "api"


@dataclass
class RawEventInput:
    source: str
    raw_text: str
    title: str = ""
    description: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    publisher_source_id: str = ""
    publisher_source_name: str = ""


@dataclass
class Event:
    id: str
    source: EventSource
    raw_text: str
    timestamp: datetime
    title: str = ""
    description: str = ""
    url: str = ""
    publisher_source_id: str = ""
    publisher_source_name: str = ""
    source_published_at: Optional[datetime] = None
    published_at_provenance: str = "unknown"


@dataclass
class EventBatch:
    events: list[Event]
    batch_id: str
    created_at: datetime


@dataclass(frozen=True)
class NewsAPIBatchRequest:
    """Secret-free immutable identity for one NewsAPI ``everything`` batch."""

    source_batch_index: int
    source_ids: tuple[str, ...]
    financial_query: str
    from_at: str
    to_at: str
    language: str
    sort_by: str
    page_size: int
    start_page: int
    max_pages: int

    def __post_init__(self) -> None:
        if self.source_batch_index < 0 or not self.source_ids:
            raise ValueError("NewsAPI batch request requires an index and source IDs")
        if any(not isinstance(value, str) or not value.strip() for value in self.source_ids):
            raise ValueError("NewsAPI batch source IDs must be non-empty strings")
        for value, name in (
            (self.financial_query, "financial_query"),
            (self.from_at, "from_at"),
            (self.to_at, "to_at"),
            (self.language, "language"),
            (self.sort_by, "sort_by"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if min(self.page_size, self.start_page, self.max_pages) <= 0:
            raise ValueError("NewsAPI batch paging values must be positive")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "provider": "newsapi",
            "endpoint": "everything",
            "source_batch_index": self.source_batch_index,
            "source_ids": list(self.source_ids),
            "financial_query": self.financial_query,
            "from_at": self.from_at,
            "to_at": self.to_at,
            "language": self.language,
            "sort_by": self.sort_by,
            "page_size": self.page_size,
            "page": self.start_page,
            "max_pages": self.max_pages,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NewsAPIBatchRequest":
        if value.get("provider") != "newsapi" or value.get("endpoint") != "everything":
            raise ValueError("Unsupported frozen collection provider request")
        source_ids = value.get("source_ids")
        if not isinstance(source_ids, (list, tuple)):
            raise ValueError("Frozen NewsAPI request requires source_ids")
        return cls(
            source_batch_index=value.get("source_batch_index"),
            source_ids=tuple(source_ids),
            financial_query=value.get("financial_query"),
            from_at=value.get("from_at"),
            to_at=value.get("to_at"),
            language=value.get("language"),
            sort_by=value.get("sort_by"),
            page_size=value.get("page_size"),
            start_page=value.get("page"),
            max_pages=value.get("max_pages"),
        )


@dataclass(frozen=True)
class CollectionBatchError:
    """Secret-safe terminal fact for one failed NewsAPI source batch."""

    source_batch_index: int
    failure_code: str
    retryable: bool
    retry_after_seconds: int | None = None
    retry_after_at: datetime | None = None
    provider_request: NewsAPIBatchRequest | None = None

    def __post_init__(self) -> None:
        if self.source_batch_index < 0:
            raise ValueError("source_batch_index must be non-negative")
        if not self.failure_code:
            raise ValueError("Collection batch errors require a failure_code")
        if self.retry_after_seconds is not None and (
            not isinstance(self.retry_after_seconds, int) or self.retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative integer or None")
        if self.retry_after_at is not None and not isinstance(self.retry_after_at, datetime):
            raise ValueError("retry_after_at must be a datetime or None")


@dataclass(frozen=True)
class CollectionSourceOutcome:
    """Secret-safe result for one configured collector invocation.

    A succeeded outcome may contain no inputs, but it is a *verified empty*
    only when ``empty_reason`` is present.  Callers must therefore not infer a
    successful empty collection from an empty ``raw_inputs`` list alone.
    """

    source_name: str
    collector_status: str
    raw_inputs: list[RawEventInput] = field(default_factory=list)
    source_row_count: int = 0
    rejected_source_row_count: int = 0
    empty_reason: str | None = None
    failure_code: str | None = None
    retryable: bool = False
    retry_after_seconds: int | None = None
    retry_after_at: datetime | None = None
    page_count: int = 0
    source_batch_count: int = 0
    successful_source_batch_count: int = 0
    batch_errors: list[CollectionBatchError] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.collector_status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported collector_status: {self.collector_status!r}")
        if (
            self.source_row_count < 0
            or self.rejected_source_row_count < 0
            or self.page_count < 0
            or self.source_batch_count < 0
            or self.successful_source_batch_count < 0
        ):
            raise ValueError("Collection row counts must be non-negative")
        if self.rejected_source_row_count > self.source_row_count:
            raise ValueError("rejected_source_row_count cannot exceed source_row_count")
        if self.successful_source_batch_count > self.source_batch_count:
            raise ValueError("successful_source_batch_count cannot exceed source_batch_count")
        if len(self.batch_errors) > self.source_batch_count:
            raise ValueError("batch_errors cannot exceed source_batch_count")
        if self.collector_status == "succeeded":
            if (
                self.failure_code is not None
                or self.retryable
                or self.retry_after_seconds is not None
                or self.retry_after_at is not None
            ):
                raise ValueError("Successful collection outcomes cannot report failures")
        elif self.failure_code is None:
            raise ValueError("Failed collection outcomes require a failure_code")
        if self.retry_after_seconds is not None and (
            not isinstance(self.retry_after_seconds, int) or self.retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative integer or None")
        if self.retry_after_at is not None and not isinstance(self.retry_after_at, datetime):
            raise ValueError("retry_after_at must be a datetime or None")
        if self.empty_reason is not None and (
            self.collector_status != "succeeded"
            or self.source_row_count != 0
            or self.rejected_source_row_count != 0
            or self.raw_inputs
        ):
            raise ValueError("empty_reason is valid only for a verified empty outcome")


@dataclass(frozen=True)
class CollectionResult:
    """Aggregate collection facts without discarding successful source inputs."""

    collector_status: str
    raw_inputs: list[RawEventInput]
    source_outcomes: list[CollectionSourceOutcome]
    source_errors: list[CollectionSourceOutcome]
    empty_reason: str | None = None

    def __post_init__(self) -> None:
        if self.collector_status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported collector_status: {self.collector_status!r}")
        expected_errors = [
            outcome for outcome in self.source_outcomes if outcome.collector_status == "failed"
        ]
        if self.source_errors != expected_errors:
            raise ValueError("source_errors must exactly mirror failed source outcomes")
        if self.empty_reason != _aggregate_empty_reason(self.source_outcomes):
            raise ValueError("empty_reason must be derived from all configured source outcomes")


class _CollectionFailure(RuntimeError):
    """Internal failure marker whose public form is a normalized code only."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
        retry_after_at: datetime | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_at = retry_after_at
        super().__init__(code)


class _NewsAPIMaximumResultsReached(requests.HTTPError):
    """Internal pagination terminator for NewsAPI's documented result ceiling."""


class _BoundedRetry(Retry):
    """Respect provider Retry-After values without allowing unbounded sleeps."""

    def get_retry_after(self, response) -> float | None:
        retry_after = super().get_retry_after(response)
        if retry_after is None:
            return None
        return min(float(retry_after), float(self.backoff_max))


@dataclass(frozen=True)
class _NewsCollectionAggregate:
    raw_inputs: list[RawEventInput]
    source_row_count: int
    rejected_source_row_count: int
    page_count: int
    source_batch_count: int
    successful_source_batch_count: int
    batch_errors: list[CollectionBatchError] = field(default_factory=list)


class EventSourceCollector(ABC):
    """Abstract base class for collecting RawEventInputs from different sources."""

    @abstractmethod
    def collect(self) -> list[RawEventInput]:
        """Collect raw event inputs from this source."""

    @property
    def source_name(self) -> str:
        return self.__class__.__name__.removesuffix("Collector").lower()

    def collect_result(self) -> CollectionSourceOutcome:
        """Return a structured outcome while retaining legacy ``collect`` APIs."""
        try:
            raw_inputs = self.collect()
        except Exception:
            return CollectionSourceOutcome(
                source_name=self.source_name,
                collector_status="failed",
                failure_code="collector_error",
                retryable=False,
            )
        return CollectionSourceOutcome(
            source_name=self.source_name,
            collector_status="succeeded",
            raw_inputs=raw_inputs,
            source_row_count=len(raw_inputs),
        )


class ManualCollector(EventSourceCollector):
    """Collector for manual user input."""

    def collect(self) -> list[RawEventInput]:
        print("Enter market event information (or press Enter to skip):")
        user_input = input("> ").strip()
        if user_input:
            return [RawEventInput(source="manual", raw_text=user_input)]
        return []


class NewsCollector(EventSourceCollector):
    """Collector for news APIs."""

    DEFAULT_MAX_PAGES = 10
    DEFAULT_SOURCE_BATCH_SIZE = 20
    DEFAULT_FINANCIAL_QUERY = "markets OR stocks OR earnings OR economy OR finance"
    DEFAULT_RETRY_AFTER_SECONDS = 60
    MAX_RETRY_AFTER_SECONDS = 900
    DEFAULT_HTTP_RETRIES = 3
    DEFAULT_RETRY_BACKOFF_FACTOR = 0.5
    DEFAULT_MAX_RETRY_BACKOFF_SECONDS = 30
    RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        country: str = "us",
        category: str = "business",
        page_size: int = 100,
        endpoint: str = "everything",
        days_back: int = 7,
        page: int = 1,
        sort_by: str = "publishedAt",
        language: str = "en",
        no_cache: bool = True,
        http_get: Callable[..., object] | None = None,
        session: object | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        source_batch_size: int = DEFAULT_SOURCE_BATCH_SIZE,
        financial_query: str = DEFAULT_FINANCIAL_QUERY,
        now_provider: Callable[[], datetime] | None = None,
        default_retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
        max_retry_after_seconds: int = MAX_RETRY_AFTER_SECONDS,
        max_http_retries: int = DEFAULT_HTTP_RETRIES,
        retry_backoff_factor: float = DEFAULT_RETRY_BACKOFF_FACTOR,
        max_retry_backoff_seconds: int = DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
    ):
        if not isinstance(page, int) or page <= 0:
            raise ValueError("page must be a positive integer")
        if not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")
        if not isinstance(source_batch_size, int) or source_batch_size <= 0:
            raise ValueError("source_batch_size must be a positive integer")
        normalized_financial_query = " ".join((financial_query or "").split())
        if endpoint == "everything" and not normalized_financial_query:
            raise ValueError("financial_query is required for the NewsAPI everything endpoint")
        if default_retry_after_seconds <= 0 or max_retry_after_seconds <= 0:
            raise ValueError("retry-after bounds must be positive")
        if not isinstance(max_http_retries, int) or max_http_retries < 0:
            raise ValueError("max_http_retries must be a non-negative integer")
        if retry_backoff_factor < 0:
            raise ValueError("retry_backoff_factor must be non-negative")
        if not isinstance(max_retry_backoff_seconds, int) or max_retry_backoff_seconds <= 0:
            raise ValueError("max_retry_backoff_seconds must be a positive integer")
        self.country = country
        self.category = category
        self.page_size = page_size
        self.endpoint = endpoint
        self.days_back = days_back
        self.page = page
        self.sort_by = sort_by
        self.language = language
        self.no_cache = no_cache
        self._http_get = http_get
        self._session = (
            session
            if session is not None
            else (None if http_get else self._build_default_session(
                max_http_retries=max_http_retries,
                retry_backoff_factor=retry_backoff_factor,
                max_retry_backoff_seconds=max_retry_backoff_seconds,
            ))
        )
        self.max_pages = max_pages
        self.source_batch_size = source_batch_size
        self.financial_query = normalized_financial_query
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.default_retry_after_seconds = min(
            default_retry_after_seconds, max_retry_after_seconds
        )
        self.max_retry_after_seconds = max_retry_after_seconds

    @classmethod
    def _build_default_session(
        cls,
        *,
        max_http_retries: int,
        retry_backoff_factor: float,
        max_retry_backoff_seconds: int,
    ) -> requests.Session:
        """Create the only transport this collector is allowed to configure.

        ``raise_on_status=False`` is deliberate: after the bounded adapter
        retries are exhausted, ``_get_json`` receives the final HTTP response
        and can preserve its Retry-After scheduling metadata in the outcome.
        """
        retry_policy = _BoundedRetry(
            total=max_http_retries,
            connect=max_http_retries,
            read=0,
            status=max_http_retries,
            other=0,
            allowed_methods=frozenset({"GET"}),
            status_forcelist=cls.RETRYABLE_HTTP_STATUS_CODES,
            backoff_factor=retry_backoff_factor,
            backoff_max=max_retry_backoff_seconds,
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_policy)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @property
    def source_name(self) -> str:
        return "news"

    def collect(self) -> list[RawEventInput]:
        api_key = os.getenv("NEWSAPI_API_KEY")
        if not api_key:
            raise MissingAPIKeyError("NEWSAPI_API_KEY is required to fetch news from NewsAPI")

        try:
            aggregate = self._collect_inputs(api_key)
            if aggregate.successful_source_batch_count == 0 and aggregate.batch_errors:
                raise _failure_from_batch_error(aggregate.batch_errors[0])
            return aggregate.raw_inputs
        except Exception as exc:
            raise RuntimeError(f"News API error: {exc}")

    def collect_result(self) -> CollectionSourceOutcome:
        """Collect NewsAPI rows as explicit success, verified-empty, or failure facts."""
        api_key = os.getenv("NEWSAPI_API_KEY")
        if not api_key:
            return self._failed_outcome("missing_api_key", retryable=False)

        try:
            aggregate = self._collect_inputs(api_key)
        except Exception as exc:
            failure_code, retryable = _normalize_collection_exception(exc)
            return self._failed_outcome(
                failure_code,
                retryable=retryable,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                retry_after_at=getattr(exc, "retry_after_at", None),
            )

        if aggregate.successful_source_batch_count == 0 and aggregate.batch_errors:
            first_error = aggregate.batch_errors[0]
            return self._failed_outcome(
                first_error.failure_code,
                retryable=first_error.retryable,
                retry_after_seconds=first_error.retry_after_seconds,
                retry_after_at=first_error.retry_after_at,
                source_row_count=aggregate.source_row_count,
                rejected_source_row_count=aggregate.rejected_source_row_count,
                page_count=aggregate.page_count,
                source_batch_count=aggregate.source_batch_count,
                successful_source_batch_count=aggregate.successful_source_batch_count,
                batch_errors=aggregate.batch_errors,
            )

        empty_reason = (
            "no_matching_articles"
            if (
                aggregate.source_row_count == 0
                and not aggregate.raw_inputs
                and aggregate.rejected_source_row_count == 0
                and not aggregate.batch_errors
            )
            else None
        )
        return CollectionSourceOutcome(
            source_name=self.source_name,
            collector_status="succeeded",
            raw_inputs=aggregate.raw_inputs,
            source_row_count=aggregate.source_row_count,
            rejected_source_row_count=aggregate.rejected_source_row_count,
            empty_reason=empty_reason,
            page_count=aggregate.page_count,
            source_batch_count=aggregate.source_batch_count,
            successful_source_batch_count=aggregate.successful_source_batch_count,
            batch_errors=aggregate.batch_errors,
        )

    def retry_source_batch(self, source_batch_index: int) -> CollectionSourceOutcome:
        """Retry exactly one ``everything`` source batch identified by its index.

        The source directory is read to resolve the named batch, but no other
        ``/everything`` batch is fetched.  This keeps a failed batch retry
        isolated from already successful NewsAPI batches and from other
        collectors such as manual input.
        """
        if (
            not isinstance(source_batch_index, int)
            or isinstance(source_batch_index, bool)
            or source_batch_index < 0
        ):
            raise ValueError("source_batch_index must be a non-negative integer")

        try:
            if self.endpoint != "everything":
                raise _CollectionFailure("unsupported_endpoint", retryable=False)
            api_key = os.getenv("NEWSAPI_API_KEY")
            if not api_key:
                raise _CollectionFailure("missing_api_key", retryable=False)

            source_batches = list(
                _chunks(
                    _unique_text_values(self._fetch_source_ids(api_key)),
                    self.source_batch_size,
                )
            )
            if not source_batches:
                raise _CollectionFailure("no_sources_available", retryable=True)
            if source_batch_index >= len(source_batches):
                raise _CollectionFailure("source_batch_not_found", retryable=False)
            request = self._build_everything_batch_request(
                source_batch_index, source_batches[source_batch_index]
            )
            return self.retry_frozen_source_batch(request)
        except Exception as exc:
            batch_error = _batch_error_from_exception(source_batch_index, exc)
            return self._failed_outcome(
                batch_error.failure_code,
                retryable=batch_error.retryable,
                retry_after_seconds=batch_error.retry_after_seconds,
                retry_after_at=batch_error.retry_after_at,
                source_batch_count=1,
                batch_errors=[batch_error],
            )

    def retry_frozen_source_batch(
        self, request: NewsAPIBatchRequest | Mapping[str, Any]
    ) -> CollectionSourceOutcome:
        """Retry exactly the persisted provider request without source discovery."""
        frozen = (
            request
            if isinstance(request, NewsAPIBatchRequest)
            else NewsAPIBatchRequest.from_mapping(request)
        )
        try:
            if self.endpoint != "everything":
                raise _CollectionFailure("unsupported_endpoint", retryable=False)
            api_key = os.getenv("NEWSAPI_API_KEY")
            if not api_key:
                raise _CollectionFailure("missing_api_key", retryable=False)
            raw_inputs, source_rows, rejected_rows, page_count, _ = self._collect_paginated(
                lambda page: self._fetch_frozen_everything_page(api_key, frozen, page),
                source_batch_count=1,
                start_page=frozen.start_page,
                max_pages=frozen.max_pages,
            )
        except Exception as exc:
            batch_error = _batch_error_from_exception(
                frozen.source_batch_index, exc, provider_request=frozen
            )
            return self._failed_outcome(
                batch_error.failure_code,
                retryable=batch_error.retryable,
                retry_after_seconds=batch_error.retry_after_seconds,
                retry_after_at=batch_error.retry_after_at,
                source_batch_count=1,
                batch_errors=[batch_error],
            )
        return CollectionSourceOutcome(
            source_name=self.source_name,
            collector_status="succeeded",
            raw_inputs=raw_inputs,
            source_row_count=source_rows,
            rejected_source_row_count=rejected_rows,
            empty_reason=(
                "no_matching_articles"
                if source_rows == 0 and not raw_inputs and rejected_rows == 0
                else None
            ),
            page_count=page_count,
            source_batch_count=1,
            successful_source_batch_count=1,
        )

    def _failed_outcome(
        self,
        failure_code: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
        retry_after_at: datetime | None = None,
        source_row_count: int = 0,
        rejected_source_row_count: int = 0,
        page_count: int = 0,
        source_batch_count: int = 0,
        successful_source_batch_count: int = 0,
        batch_errors: list[CollectionBatchError] | None = None,
    ) -> CollectionSourceOutcome:
        return CollectionSourceOutcome(
            source_name=self.source_name,
            collector_status="failed",
            failure_code=failure_code,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            retry_after_at=retry_after_at,
            source_row_count=source_row_count,
            rejected_source_row_count=rejected_source_row_count,
            page_count=page_count,
            source_batch_count=source_batch_count,
            successful_source_batch_count=successful_source_batch_count,
            batch_errors=list(batch_errors or []),
        )

    def _collect_inputs(self, api_key: str) -> _NewsCollectionAggregate:
        if self.endpoint == "top-headlines":
            raw_inputs, source_rows, rejected_rows, page_count, source_batch_count = self._collect_paginated(
                lambda page: self._fetch_top_headlines_page(api_key, page),
                source_batch_count=1,
            )
            return _NewsCollectionAggregate(
                raw_inputs=raw_inputs,
                source_row_count=source_rows,
                rejected_source_row_count=rejected_rows,
                page_count=page_count,
                source_batch_count=source_batch_count,
                successful_source_batch_count=1,
            )
        elif self.endpoint == "everything":
            source_ids = self._fetch_source_ids(api_key)
            if not source_ids:
                raise _CollectionFailure("no_sources_available", retryable=True)
            all_raw_inputs: list[RawEventInput] = []
            source_row_count = 0
            rejected_source_row_count = 0
            page_count = 0
            source_batches = list(_chunks(_unique_text_values(source_ids), self.source_batch_size))
            if not source_batches:
                raise _CollectionFailure("no_sources_available", retryable=True)
            batch_errors: list[CollectionBatchError] = []
            successful_source_batch_count = 0
            for batch_index, source_batch in enumerate(source_batches):
                request = self._build_everything_batch_request(batch_index, source_batch)
                try:
                    batch_inputs, batch_rows, batch_rejected, batch_pages, _ = self._collect_paginated(
                        lambda page, request=request: self._fetch_frozen_everything_page(
                            api_key, request, page
                        ),
                        source_batch_count=1,
                    )
                except Exception as exc:
                    batch_errors.append(
                        _batch_error_from_exception(
                            batch_index, exc, provider_request=request
                        )
                    )
                    continue
                all_raw_inputs.extend(batch_inputs)
                source_row_count += batch_rows
                rejected_source_row_count += batch_rejected
                page_count += batch_pages
                successful_source_batch_count += 1
            return _NewsCollectionAggregate(
                raw_inputs=all_raw_inputs,
                source_row_count=source_row_count,
                rejected_source_row_count=rejected_source_row_count,
                page_count=page_count,
                source_batch_count=len(source_batches),
                successful_source_batch_count=successful_source_batch_count,
                batch_errors=batch_errors,
            )
        else:
            raise _CollectionFailure("unsupported_endpoint", retryable=False)

    def _collect_paginated(
        self,
        fetch_page: Callable[[int], dict],
        *,
        source_batch_count: int,
        start_page: int | None = None,
        max_pages: int | None = None,
    ) -> tuple[list[RawEventInput], int, int, int, int]:
        all_raw_inputs: list[RawEventInput] = []
        source_row_count = 0
        rejected_source_row_count = 0
        page_count = 0
        resolved_start_page = self.page if start_page is None else start_page
        resolved_max_pages = self.max_pages if max_pages is None else max_pages
        for page_offset in range(resolved_max_pages):
            try:
                data = fetch_page(resolved_start_page + page_offset)
            except _NewsAPIMaximumResultsReached:
                # A provider cap proves neither an empty result nor a successful
                # first page.  It can only terminate a sequence already proven
                # by one or more successful page responses.
                if page_count == 0:
                    raise
                break
            page_inputs, page_rows, page_rejected = self._build_raw_inputs_with_counts(data)
            page_count += 1
            all_raw_inputs.extend(page_inputs)
            source_row_count += page_rows
            rejected_source_row_count += page_rejected
            if page_rows == 0:
                break
        return (
            all_raw_inputs,
            source_row_count,
            rejected_source_row_count,
            page_count,
            source_batch_count,
        )

    def _fetch_top_headlines(self, api_key: str) -> dict:
        """Legacy one-page entrypoint retained for callers that need a single request."""
        return self._fetch_top_headlines_page(api_key, self.page)

    def _fetch_top_headlines_page(self, api_key: str, page: int) -> dict:
        return self._get_json(
            "https://newsapi.org/v2/top-headlines",
            {
                "country": self.country,
                "category": self.category,
                "pageSize": self.page_size,
                "page": page,
                "apiKey": api_key,
            },
        )

    def _fetch_recent_everything(self, api_key: str) -> dict:
        """Legacy one-page entrypoint retained for callers that need a single request."""
        source_ids = self._fetch_source_ids(api_key)
        if not source_ids:
            raise _CollectionFailure("no_sources_available", retryable=True)
        return self._fetch_everything_page(
            api_key,
            _unique_text_values(source_ids)[: self.source_batch_size],
            self.page,
        )

    def _fetch_everything_page(self, api_key: str, source_ids: list[str], page: int) -> dict:
        if not source_ids:
            raise _CollectionFailure("no_sources_available", retryable=True)

        now = _as_utc_datetime(self._now_provider())
        window_start = now - timedelta(days=max(self.days_back, 1))
        return self._get_json(
            "https://newsapi.org/v2/everything",
            {
                "sources": ",".join(source_ids),
                "q": self.financial_query,
                "from": window_start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "to": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "language": self.language,
                "sortBy": self.sort_by,
                "pageSize": self.page_size,
                "page": page,
                "apiKey": api_key,
            },
        )

    def _build_everything_batch_request(
        self, source_batch_index: int, source_ids: list[str]
    ) -> NewsAPIBatchRequest:
        now = _as_utc_datetime(self._now_provider())
        window_start = now - timedelta(days=max(self.days_back, 1))
        return NewsAPIBatchRequest(
            source_batch_index=source_batch_index,
            source_ids=tuple(source_ids),
            financial_query=self.financial_query,
            from_at=window_start.isoformat(timespec="seconds").replace("+00:00", "Z"),
            to_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            language=self.language,
            sort_by=self.sort_by,
            page_size=self.page_size,
            start_page=self.page,
            max_pages=self.max_pages,
        )

    def _fetch_frozen_everything_page(
        self, api_key: str, request: NewsAPIBatchRequest, page: int
    ) -> dict:
        return self._get_json(
            "https://newsapi.org/v2/everything",
            {
                "sources": ",".join(request.source_ids),
                "q": request.financial_query,
                "from": request.from_at,
                "to": request.to_at,
                "language": request.language,
                "sortBy": request.sort_by,
                "pageSize": request.page_size,
                "page": page,
                "apiKey": api_key,
            },
        )

    def _fetch_source_ids(self, api_key: str) -> list[str]:
        data = self._get_json(
            "https://newsapi.org/v2/top-headlines/sources",
            {
                "category": self.category,
                "country": self.country,
                "language": self.language,
                "apiKey": api_key,
            },
        )
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise RuntimeError("NewsAPI sources response must contain a sources list")
        return [
            source_id
            for source in sources
            if isinstance(source, dict)
            for source_id in [source.get("id")]
            if source_id
        ]

    def _get_json(self, url: str, params: dict) -> dict:
        request_params = dict(params)
        api_key = request_params.pop("apiKey", None)
        headers = {"X-Api-Key": api_key} if api_key else {}
        if self.no_cache:
            headers["X-No-Cache"] = "true"
        # Preserve an explicit test/compatibility transport override while normal
        # collection uses the collector-owned reusable Session.
        http_get = self._http_get or (
            requests.get
            if requests.get is not _ORIGINAL_REQUESTS_GET
            else getattr(self._session, "get", None) or requests.get
        )
        response = http_get(url, params=request_params, headers=headers or None, timeout=10)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = getattr(response, "status_code", None)
            if _is_newsapi_maximum_results_reached(response):
                raise _NewsAPIMaximumResultsReached(
                    "NewsAPI maximum result cap", response=response
                ) from exc
            if status_code == 429 or (isinstance(status_code, int) and 500 <= status_code <= 599):
                retry_after_seconds, retry_after_at = self._retry_after_contract(response)
                raise _CollectionFailure(
                    "http_429" if status_code == 429 else "http_5xx",
                    retryable=True,
                    retry_after_seconds=retry_after_seconds,
                    retry_after_at=retry_after_at,
                ) from exc
            raise
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("NewsAPI response must be a JSON object")
        if data.get("status") != "ok":
            code = data.get("code") or "unknown_error"
            message = data.get("message") or "NewsAPI returned a non-ok status"
            raise RuntimeError(f"NewsAPI {code}: {message}")
        return data

    def _retry_after_contract(self, response: object) -> tuple[int, datetime | None]:
        response_headers = getattr(response, "headers", {}) or {}
        retry_after_value = response_headers.get("Retry-After", "")
        return parse_retry_after(
            retry_after_value,
            now=_as_utc_datetime(self._now_provider()),
            default_seconds=self.default_retry_after_seconds,
            max_seconds=self.max_retry_after_seconds,
        )

    def _build_raw_inputs(self, data: dict) -> list[RawEventInput]:
        return self._build_raw_inputs_with_counts(data)[0]

    def _build_raw_inputs_with_counts(self, data: dict) -> tuple[list[RawEventInput], int, int]:
        raw_inputs = []
        articles = data.get("articles")
        if not isinstance(articles, list):
            raise _CollectionFailure("provider_invalid_response", retryable=False)
        rejected_source_row_count = 0
        for article in articles:
            if not isinstance(article, dict):
                rejected_source_row_count += 1
                continue
            title = article.get("title") or ""
            description = article.get("description") or ""
            content = f"{title}. {description}".strip()
            url_value = article.get("url") or ""
            source = article.get("source") if isinstance(article.get("source"), dict) else {}
            if not _is_valid_newsapi_article(title, url_value):
                rejected_source_row_count += 1
                continue
            try:
                published_at = (
                    datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00"))
                    if article.get("publishedAt")
                    else None
                )
            except (TypeError, ValueError):
                rejected_source_row_count += 1
                continue
            raw_inputs.append(
                RawEventInput(
                    source="news",
                    raw_text=content,
                    title=title,
                    description=description,
                    url=url_value,
                    published_at=published_at,
                    publisher_source_id=source.get("id") or "",
                    publisher_source_name=source.get("name") or "",
                )
            )
        return raw_inputs, len(articles), rejected_source_row_count


def _is_valid_newsapi_article(title: str, url: str) -> bool:
    normalized_title = " ".join((title or "").split()).strip()
    if not normalized_title or normalized_title.lower() == "[removed]":
        return False
    parsed = urlparse((url or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _unique_text_values(values: Iterable[object]) -> list[str]:
    """Keep NewsAPI source IDs deterministic while removing blank/duplicate values."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip() if value is not None else ""
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _as_utc_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("now_provider must return a datetime")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _is_newsapi_maximum_results_reached(response: object) -> bool:
    """Recognize only NewsAPI's typed result-cap response, never a bare 426."""
    if getattr(response, "status_code", None) != 426:
        return False
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "error"
        and payload.get("code") == "maximumResultsReached"
    )


def _batch_error_from_exception(
    batch_index: int,
    exc: Exception,
    *,
    provider_request: NewsAPIBatchRequest | None = None,
) -> CollectionBatchError:
    failure_code, retryable = _normalize_collection_exception(exc)
    return CollectionBatchError(
        source_batch_index=batch_index,
        failure_code=failure_code,
        retryable=retryable,
        retry_after_seconds=getattr(exc, "retry_after_seconds", None),
        retry_after_at=getattr(exc, "retry_after_at", None),
        provider_request=provider_request,
    )


def _failure_from_batch_error(error: CollectionBatchError) -> _CollectionFailure:
    return _CollectionFailure(
        error.failure_code,
        retryable=error.retryable,
        retry_after_seconds=error.retry_after_seconds,
        retry_after_at=error.retry_after_at,
    )


def _normalize_collection_exception(exc: Exception) -> tuple[str, bool]:
    """Map provider/client exceptions to public, secret-safe collection facts."""
    if isinstance(exc, _CollectionFailure):
        return exc.code, exc.retryable
    if isinstance(exc, requests.Timeout):
        return "network_timeout", True
    if isinstance(exc, requests.ConnectionError):
        return "network_connection_error", True
    if isinstance(exc, requests.HTTPError):
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 429:
            return "http_429", True
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return "http_5xx", True
        if status_code in {401, 403}:
            return "http_401_403", False
        if isinstance(status_code, int) and 400 <= status_code <= 499:
            return "http_4xx", False
    return "provider_error", False


def _aggregate_empty_reason(outcomes: list[CollectionSourceOutcome]) -> str | None:
    """Return a verified aggregate empty marker only when every source proves it."""
    if not outcomes:
        return None
    if all(
        outcome.collector_status == "succeeded"
        and outcome.source_row_count == 0
        and outcome.rejected_source_row_count == 0
        and not outcome.raw_inputs
        and outcome.empty_reason == "no_matching_articles"
        for outcome in outcomes
    ):
        return "no_matching_articles"
    return None


class ApiCollector(EventSourceCollector):
    """Collector for market data APIs."""

    def collect(self) -> list[RawEventInput]:
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if not api_key:
            print("ALPHA_VANTAGE_API_KEY not set, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5.",
                )
            ]

        try:
            url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=SPY&apikey={api_key}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "Time Series (Daily)" in data:
                latest_date = max(data["Time Series (Daily)"].keys())
                latest_data = data["Time Series (Daily)"][latest_date]
                close = latest_data.get("4. close", "N/A")
                volume = latest_data.get("5. volume", "N/A")
                content = f"SPY daily close: ${close}, volume: {volume}. Market data from Alpha Vantage API."
                return [RawEventInput(source="api", raw_text=content)]
            raise ValueError("Invalid API response")
        except Exception as exc:
            print(f"Alpha Vantage API error: {exc}, using fallback")
            return [
                RawEventInput(
                    source="api",
                    raw_text="Fallback API Data: Treasury yields spike 15bps across curve following FOMC minutes release, 10-year at 4.25%. VIX up 5 points to 18.5.",
                )
            ]


def create_event(raw_input: RawEventInput) -> Event:
    from event_collector.news_ingestion import validate_raw_input

    failure_reason = validate_raw_input(raw_input)
    if failure_reason == "invalid_source":
        raise InvalidEventSourceError(f"Invalid source: {raw_input.source}")
    if failure_reason == "missing_text":
        raise InvalidEventTextError("raw_text or url is required")
    if failure_reason == "short_news_text_without_url":
        raise InvalidEventTextError("news raw_text must be at least 50 characters when no article url is present")
    if failure_reason == "invalid_news_url":
        raise InvalidEventTextError("news url must be an absolute http or https URL")
    if failure_reason == "short_text":
        raise InvalidEventTextError("raw_text must be at least 50 characters")

    source_published_at, published_at_provenance = resolve_source_publication_metadata(
        raw_input
    )
    return Event(
        id=str(uuid.uuid4()),
        source=EventSource(raw_input.source),
        raw_text=raw_input.raw_text,
        timestamp=raw_input.published_at or datetime.now(),
        title=raw_input.title,
        description=raw_input.description,
        url=raw_input.url,
        publisher_source_id=raw_input.publisher_source_id,
        publisher_source_name=raw_input.publisher_source_name,
        source_published_at=source_published_at,
        published_at_provenance=published_at_provenance,
    )


def create_event_batch(events: list[Event]) -> EventBatch:
    return EventBatch(
        events=events,
        batch_id=str(uuid.uuid4()),
        created_at=datetime.now(),
    )


def collect_events_batch(raw_inputs: list[RawEventInput]) -> EventBatch:
    events = []
    for raw_input in raw_inputs:
        try:
            events.append(create_event(raw_input))
        except (InvalidEventSourceError, InvalidEventTextError):
            continue
    return create_event_batch(events)


def collect_from_all_sources(collectors: list[EventSourceCollector]) -> EventBatch:
    return collect_events_batch(collect_raw_inputs_from_sources(collectors))


def collect_collection_result(collectors: list[EventSourceCollector]) -> CollectionResult:
    """Collect all configured sources without turning one source error into data loss.

    This is deliberately separate from the legacy list-returning helpers.  A
    workflow may adopt the structured result when it is ready, while current
    callers retain their existing exception and list semantics.
    """
    source_outcomes: list[CollectionSourceOutcome] = []
    raw_inputs: list[RawEventInput] = []
    for collector in collectors:
        outcome = collector.collect_result()
        source_outcomes.append(outcome)
        if outcome.collector_status == "succeeded":
            raw_inputs.extend(outcome.raw_inputs)

    source_errors = [
        outcome for outcome in source_outcomes if outcome.collector_status == "failed"
    ]
    collector_status = (
        "succeeded"
        if any(outcome.collector_status == "succeeded" for outcome in source_outcomes)
        else "failed"
    )
    return CollectionResult(
        collector_status=collector_status,
        raw_inputs=raw_inputs,
        source_outcomes=source_outcomes,
        source_errors=source_errors,
        empty_reason=_aggregate_empty_reason(source_outcomes),
    )


def collect_raw_inputs_from_sources(collectors: list[EventSourceCollector]) -> list[RawEventInput]:
    all_raw_inputs = []
    for collector in collectors:
        all_raw_inputs.extend(collector.collect())
    return all_raw_inputs


def raw_event_input_to_news_article(raw_input: RawEventInput, url: str = "") -> NewsArticle:
    title = raw_input.raw_text[:100]
    description = raw_input.raw_text[:200]

    source_published_at, published_at_provenance = resolve_source_publication_metadata(
        raw_input
    )
    return NewsArticle(
        source=raw_input.source,
        title=raw_input.title or title,
        description=raw_input.description or description,
        content=raw_input.raw_text,
        url=url or raw_input.url or f"internal://{raw_input.source}/{datetime.now().timestamp()}",
        published_at=raw_input.published_at or datetime.now(),
        summary=None,
        publisher_source_id=raw_input.publisher_source_id,
        publisher_source_name=raw_input.publisher_source_name,
        source_published_at=source_published_at,
        published_at_provenance=published_at_provenance,
    )


def resolve_source_publication_metadata(
    raw_input: RawEventInput,
) -> tuple[datetime | None, str]:
    """Return only a provider-observed publication date as source metadata."""

    if raw_input.source in {EventSource.NEWS.value, EventSource.API.value} and raw_input.published_at is not None:
        return raw_input.published_at, "source_metadata"
    return None, "unknown"


def ingest_events_to_storage(
    batch: EventBatch,
    storage: SQLiteNewsStore,
    vector_store: Optional[VectorStore] = None,
    summarizer=None,
    content_fetcher=None,
    show_progress: bool = False,
) -> dict:
    from event_collector.news_ingestion import ingest_event_batch

    return ingest_event_batch(
        batch.events,
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=content_fetcher,
        show_progress=show_progress,
    ).stats
