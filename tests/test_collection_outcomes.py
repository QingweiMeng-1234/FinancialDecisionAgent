from __future__ import annotations

from datetime import datetime, timezone

import pytest

from event_collector.event_collection import (
    CollectionBatchError,
    CollectionSourceOutcome,
    NewsAPIBatchRequest,
    EventSourceCollector,
    NewsCollector,
    RawEventInput,
    collect_collection_result,
)


class _Response:
    def __init__(
        self,
        payload: dict,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self) -> dict:
        return self._payload


def _news_collector(http_get) -> NewsCollector:
    # Existing one-page contract tests intentionally use the explicit compatibility mode.
    return NewsCollector(endpoint="top-headlines", max_pages=1, http_get=http_get)


def test_news_verified_provider_empty_is_the_only_verified_empty(monkeypatch):
    """SELECT INVARIANT: a successful provider `articles=[]` is a verified empty result."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    collector = _news_collector(
        lambda url, params=None, headers=None, timeout=None: _Response(
            {"status": "ok", "articles": []}
        )
    )

    outcome = collector.collect_result()

    assert outcome.collector_status == "succeeded"
    assert outcome.raw_inputs == []
    assert outcome.source_row_count == 0
    assert outcome.rejected_source_row_count == 0
    assert outcome.empty_reason == "no_matching_articles"
    assert outcome.failure_code is None


def test_news_rows_rejected_by_validation_are_not_a_verified_empty(monkeypatch):
    """SELECT INVARIANT: nonempty provider rows rejected locally cannot complete an empty gate."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    collector = _news_collector(
        lambda url, params=None, headers=None, timeout=None: _Response(
            {
                "status": "ok",
                "articles": [
                    {"title": "[Removed]", "url": "https://example.com/removed"},
                    {"title": "missing usable url", "url": "not-a-url"},
                ],
            }
        )
    )

    outcome = collector.collect_result()

    assert outcome.collector_status == "succeeded"
    assert outcome.raw_inputs == []
    assert outcome.source_row_count == 2
    assert outcome.rejected_source_row_count == 2
    assert outcome.empty_reason is None


def test_news_collection_normalizes_missing_key_without_leaking_detail(monkeypatch):
    """SELECT INVARIANT: collection failures expose stable, non-secret-bearing facts only."""
    monkeypatch.delenv("NEWSAPI_API_KEY", raising=False)

    outcome = _news_collector(lambda *args, **kwargs: None).collect_result()

    assert outcome.collector_status == "failed"
    assert outcome.failure_code == "missing_api_key"
    assert outcome.retryable is False
    assert not hasattr(outcome, "failure_detail")


def test_news_collection_normalizes_http_429_as_retryable(monkeypatch):
    """SELECT INVARIANT: retry behavior derives from stable HTTP categories, not exception text."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    outcome = _news_collector(
        lambda url, params=None, headers=None, timeout=None: _Response({}, status_code=429)
    ).collect_result()

    assert outcome.collector_status == "failed"
    assert outcome.failure_code == "http_429"
    assert outcome.retryable is True


def test_news_collection_normalizes_provider_error_and_no_sources(monkeypatch):
    """Provider-declared errors and an empty source directory are failed collection facts."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    provider_error = _news_collector(
        lambda url, params=None, headers=None, timeout=None: _Response(
            {"status": "error", "code": "apiKeyInvalid", "message": "do not expose this"}
        )
    ).collect_result()
    no_sources = NewsCollector(
        endpoint="everything",
        http_get=lambda url, params=None, headers=None, timeout=None: _Response(
            {"status": "ok", "sources": []}
        ),
    ).collect_result()

    assert (provider_error.collector_status, provider_error.failure_code, provider_error.retryable) == (
        "failed",
        "provider_error",
        False,
    )
    assert (no_sources.collector_status, no_sources.failure_code, no_sources.retryable) == (
        "failed",
        "no_sources_available",
        True,
    )


class _SuccessfulCollector(EventSourceCollector):
    def collect(self) -> list[RawEventInput]:
        return [RawEventInput(source="manual", raw_text="manual input")]


class _FailedCollector(EventSourceCollector):
    def collect(self) -> list[RawEventInput]:
        raise RuntimeError("provider said: secret-token-should-not-escape")


class _OutcomeCollector(EventSourceCollector):
    def __init__(self, outcome: CollectionSourceOutcome) -> None:
        self._outcome = outcome

    def collect(self) -> list[RawEventInput]:
        return self._outcome.raw_inputs

    def collect_result(self) -> CollectionSourceOutcome:
        return self._outcome


def test_multi_collector_preserves_success_when_another_source_fails():
    """SELECT INVARIANT: one failed source cannot discard another source's successful inputs."""
    result = collect_collection_result([_SuccessfulCollector(), _FailedCollector()])

    assert result.collector_status == "succeeded"
    assert result.raw_inputs == [RawEventInput(source="manual", raw_text="manual input")]
    assert len(result.source_outcomes) == 2
    assert len(result.source_errors) == 1
    assert result.source_errors[0].failure_code == "collector_error"
    assert "secret-token" not in repr(result.source_errors[0])


def test_aggregate_empty_reason_requires_all_configured_sources_to_be_verified_empty():
    """SELECT INVARIANT: aggregate empty is explicit only when every source proves it."""
    verified_empty = CollectionSourceOutcome(
        source_name="news-a",
        collector_status="succeeded",
        empty_reason="no_matching_articles",
    )
    also_verified_empty = CollectionSourceOutcome(
        source_name="news-b",
        collector_status="succeeded",
        empty_reason="no_matching_articles",
    )
    unverified_manual_skip = CollectionSourceOutcome(
        source_name="manual",
        collector_status="succeeded",
    )

    verified_result = collect_collection_result(
        [_OutcomeCollector(verified_empty), _OutcomeCollector(also_verified_empty)]
    )
    mixed_result = collect_collection_result(
        [_OutcomeCollector(verified_empty), _OutcomeCollector(unverified_manual_skip)]
    )

    assert verified_result.empty_reason == "no_matching_articles"
    assert mixed_result.empty_reason is None


def test_existing_collect_still_returns_raw_input_list(monkeypatch):
    """SELECT INVARIANT: the structured path does not change the legacy `collect()` contract."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    collector = _news_collector(
        lambda url, params=None, headers=None, timeout=None: _Response(
            {
                "status": "ok",
                "articles": [
                    {
                        "title": "Market headline",
                        "description": "A valid description.",
                        "url": "https://example.com/article",
                    }
                ],
            }
        )
    )

    raw_inputs = collector.collect()

    assert isinstance(raw_inputs, list)
    assert len(raw_inputs) == 1


def test_everything_runner_visits_all_source_batches_and_pages_with_financial_query(monkeypatch):
    """SELECT INVARIANT: source coverage is batched, never silently truncated to 20 IDs."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    calls = []
    source_ids = [f"publisher-{index}" for index in range(45)]

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        calls.append((url, params))
        if url.endswith("/top-headlines/sources"):
            return _Response({"status": "ok", "sources": [{"id": item} for item in source_ids]})
        requested_sources = params["sources"].split(",")
        if params["page"] == 2:
            return _Response({"status": "ok", "articles": []})
        first_source = requested_sources[0]
        return _Response(
            {
                "status": "ok",
                "articles": [
                    {
                        "source": {"id": first_source, "name": first_source},
                        "title": f"{first_source} reports quarterly market update",
                        "description": "Equity markets and corporate earnings moved during trading.",
                        "url": f"https://{first_source}.example/news",
                    }
                ],
            }
        )

    outcome = NewsCollector(
        endpoint="everything",
        source_batch_size=20,
        max_pages=3,
        financial_query="equities OR earnings",
        http_get=fake_get,
    ).collect_result()

    everything_calls = [params for url, params in calls if url.endswith("/everything")]
    assert [call["sources"].split(",") for call in everything_calls] == [
        source_ids[:20],
        source_ids[:20],
        source_ids[20:40],
        source_ids[20:40],
        source_ids[40:],
        source_ids[40:],
    ]
    assert [call["page"] for call in everything_calls] == [1, 2, 1, 2, 1, 2]
    assert all(call["q"] == "equities OR earnings" for call in everything_calls)
    assert outcome.collector_status == "succeeded"
    assert len(outcome.raw_inputs) == 3
    assert outcome.source_row_count == 3
    assert outcome.page_count == 6
    assert outcome.source_batch_count == 3


def test_everything_keeps_completed_pages_when_provider_reports_its_result_cap(monkeypatch):
    """SELECT INVARIANT: a provider-declared page cap ends only an already-proven page sequence."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    requested_pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            return _Response({"status": "ok", "sources": [{"id": "publisher-0"}]})
        requested_pages.append(params["page"])
        if params["page"] == 1:
            return _Response(
                {
                    "status": "ok",
                    "articles": [
                        {
                            "title": "Markets react to earnings report",
                            "description": "Equities moved as investors assessed corporate earnings.",
                            "url": "https://publisher-0.example/earnings",
                        }
                    ],
                }
            )
        return _Response(
            {
                "status": "error",
                "code": "maximumResultsReached",
                "message": "provider detail must not become a public collection fact",
            },
            status_code=426,
        )

    outcome = NewsCollector(
        endpoint="everything", max_pages=3, http_get=fake_get
    ).collect_result()

    assert requested_pages == [1, 2]
    assert outcome.collector_status == "succeeded"
    assert outcome.source_row_count == 1
    assert len(outcome.raw_inputs) == 1
    assert outcome.page_count == 1
    assert outcome.batch_errors == []


def test_everything_fails_closed_when_result_cap_precedes_any_successful_page(monkeypatch):
    """SELECT INVARIANT: a result cap is not a successful empty result without prior page evidence."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            return _Response({"status": "ok", "sources": [{"id": "publisher-0"}]})
        return _Response(
            {"status": "error", "code": "maximumResultsReached", "message": "not public"},
            status_code=426,
        )

    outcome = NewsCollector(endpoint="everything", max_pages=3, http_get=fake_get).collect_result()

    assert outcome.collector_status == "failed"
    assert outcome.failure_code == "http_4xx"
    assert outcome.source_row_count == 0
    assert outcome.page_count == 0
    assert outcome.successful_source_batch_count == 0


def test_top_headlines_runner_uses_the_injected_session_for_every_page(monkeypatch):
    """SELECT INVARIANT: a collector owns one reusable HTTP session, rather than one request client per page."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, headers=None, timeout=None):
            del headers, timeout
            self.calls.append((url, params))
            if params["page"] == 1:
                return _Response(
                    {
                        "status": "ok",
                        "articles": [
                            {
                                "title": "Markets react to central-bank decision",
                                "description": "Stocks and bond yields moved after the policy announcement.",
                                "url": "https://news.example/policy",
                            }
                        ],
                    }
                )
            return _Response({"status": "ok", "articles": []})

    session = Session()
    outcome = NewsCollector(
        endpoint="top-headlines", max_pages=3, session=session
    ).collect_result()

    assert [params["page"] for _, params in session.calls] == [1, 2]
    assert outcome.page_count == 2
    assert len(outcome.raw_inputs) == 1


@pytest.mark.parametrize(
    ("status_code", "failure_code"), [(429, "http_429"), (503, "http_5xx")]
)
def test_newsapi_retryable_status_retains_bounded_retry_after_metadata(
    monkeypatch, status_code, failure_code
):
    """SELECT INVARIANT: the runner returns scheduling metadata but never sleeps inside collection."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    outcome = NewsCollector(
        endpoint="top-headlines",
        http_get=lambda *args, **kwargs: _Response(
            {}, status_code=status_code, headers={"Retry-After": "120"}
        ),
        now_provider=lambda: now,
        default_retry_after_seconds=30,
        max_retry_after_seconds=90,
    ).collect_result()

    assert outcome.collector_status == "failed"
    assert outcome.failure_code == failure_code
    assert outcome.retryable is True
    assert outcome.retry_after_seconds == 90
    assert outcome.retry_after_at is None


def test_single_page_compatibility_mode_does_not_fetch_the_next_page(monkeypatch):
    """SELECT INVARIANT: callers that need the legacy one-page boundary can opt into it explicitly."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    requested_pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        del url, headers, timeout
        requested_pages.append(params["page"])
        return _Response({"status": "ok", "articles": []})

    outcome = NewsCollector(
        endpoint="top-headlines", page=3, max_pages=1, http_get=fake_get
    ).collect_result()

    assert requested_pages == [3]
    assert outcome.page_count == 1
    assert outcome.empty_reason == "no_matching_articles"


def test_everything_batch_failure_preserves_prior_success_and_reports_batch_error(monkeypatch):
    """SELECT INVARIANT: a later batch timeout cannot roll back earlier NewsAPI evidence."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    source_ids = [f"publisher-{index}" for index in range(21)]

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            return _Response({"status": "ok", "sources": [{"id": item} for item in source_ids]})
        if params["sources"].split(",")[0] == "publisher-20":
            import requests

            raise requests.Timeout("second batch timed out")
        if params["page"] == 2:
            return _Response({"status": "ok", "articles": []})
        return _Response(
            {
                "status": "ok",
                "articles": [
                    {
                        "title": "Markets react to earnings report",
                        "description": "Equities moved as investors assessed corporate earnings.",
                        "url": "https://publisher-0.example/earnings",
                    }
                ],
            }
        )

    outcome = NewsCollector(
        endpoint="everything", source_batch_size=20, max_pages=3, http_get=fake_get
    ).collect_result()

    assert outcome.collector_status == "succeeded"
    assert len(outcome.raw_inputs) == 1
    assert outcome.source_row_count == 1
    assert outcome.source_batch_count == 2
    assert outcome.successful_source_batch_count == 1
    assert len(outcome.batch_errors) == 1
    assert outcome.batch_errors[0].failure_code == "network_timeout"
    assert outcome.batch_errors[0].retryable is True
    assert outcome.empty_reason is None


def test_targeted_source_batch_retry_skips_other_batches_and_keeps_retry_after_evidence(
    monkeypatch,
):
    """SELECT INVARIANT: retrying batch N invokes only N and reports its normalized failure fact."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    everything_source_batches = []
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            return _Response(
                {"status": "ok", "sources": [{"id": "already-succeeded"}, {"id": "retry-me"}]}
            )
        everything_source_batches.append(params["sources"])
        return _Response({}, status_code=429, headers={"Retry-After": "120"})

    outcome = NewsCollector(
        endpoint="everything",
        source_batch_size=1,
        http_get=fake_get,
        now_provider=lambda: now,
        default_retry_after_seconds=30,
        max_retry_after_seconds=90,
    ).retry_source_batch(1)

    assert everything_source_batches == ["retry-me"]
    assert outcome.collector_status == "failed"
    assert outcome.failure_code == "http_429"
    assert outcome.retryable is True
    assert outcome.retry_after_seconds == 90
    assert outcome.source_batch_count == 1
    assert outcome.successful_source_batch_count == 0
    assert outcome.batch_errors == [
        CollectionBatchError(
            source_batch_index=1,
            failure_code="http_429",
            retryable=True,
            retry_after_seconds=90,
            provider_request=NewsAPIBatchRequest(
                source_batch_index=1,
                source_ids=("retry-me",),
                financial_query=NewsCollector.DEFAULT_FINANCIAL_QUERY,
                from_at="2026-08-08T12:00:00Z",
                to_at="2026-08-15T12:00:00Z",
                language="en",
                sort_by="publishedAt",
                page_size=100,
                start_page=1,
                max_pages=10,
            ),
        )
    ]


def test_frozen_batch_retry_never_reloads_or_reorders_the_newsapi_source_directory(monkeypatch):
    """SELECT INVARIANT: a retry invokes the exact secret-free provider request frozen at failure."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    source_directory_calls = 0
    requested_sources = []

    def first_get(url, params=None, headers=None, timeout=None):
        nonlocal source_directory_calls
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            source_directory_calls += 1
            return _Response(
                {"status": "ok", "sources": [{"id": "first"}, {"id": "retry-me"}]}
            )
        if params["sources"] == "retry-me":
            import requests

            raise requests.Timeout("retry-me failed")
        return _Response({"status": "ok", "articles": []})

    first = NewsCollector(
        endpoint="everything",
        source_batch_size=1,
        max_pages=1,
        http_get=first_get,
        now_provider=lambda: now,
    ).collect_result()
    frozen = first.batch_errors[0].provider_request
    assert frozen is not None

    def retry_get(url, params=None, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/top-headlines/sources"):
            raise AssertionError("frozen retry must not reload the mutable source directory")
        requested_sources.append(params["sources"])
        return _Response({"status": "ok", "articles": []})

    retried = NewsCollector(
        endpoint="everything",
        source_batch_size=1,
        max_pages=1,
        http_get=retry_get,
        now_provider=lambda: now.replace(day=16),
    ).retry_frozen_source_batch(frozen)

    assert source_directory_calls == 1
    assert requested_sources == ["retry-me"]
    assert retried.collector_status == "succeeded"


def test_everything_reports_failed_only_when_no_source_batch_completes(monkeypatch):
    """SELECT INVARIANT: all failed batches are an overall failure, distinct from partial success."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")

    def fake_get(url, params=None, headers=None, timeout=None):
        del headers, params, timeout
        if url.endswith("/top-headlines/sources"):
            return _Response(
                {"status": "ok", "sources": [{"id": "first"}, {"id": "second"}]}
            )
        import requests

        raise requests.Timeout("all batches timed out")

    outcome = NewsCollector(
        endpoint="everything", source_batch_size=1, http_get=fake_get
    ).collect_result()

    assert outcome.collector_status == "failed"
    assert outcome.raw_inputs == []
    assert outcome.failure_code == "network_timeout"
    assert outcome.source_batch_count == 2
    assert outcome.successful_source_batch_count == 0
    assert len(outcome.batch_errors) == 2


def test_default_newsapi_session_installs_bounded_retry_policy():
    """SELECT INVARIANT: collector-owned transport retries only finite retryable NewsAPI failures."""
    collector = NewsCollector()

    https_adapter = collector._session.get_adapter("https://")
    http_adapter = collector._session.get_adapter("http://")
    retries = https_adapter.max_retries

    assert http_adapter is https_adapter
    assert retries.total == 3
    assert retries.connect == 3
    assert retries.status == 3
    assert retries.read == 0
    assert retries.backoff_factor == 0.5
    assert retries.backoff_max == 30
    assert retries.respect_retry_after_header is True
    assert retries.raise_on_status is False
    assert {429, 500, 502, 503, 504}.issubset(set(retries.status_forcelist))
    assert retries.allowed_methods == frozenset({"GET"})


def test_injected_newsapi_transport_is_not_reconfigured():
    """SELECT INVARIANT: retry policy ownership belongs only to the default collector-created Session."""

    class Session:
        def __init__(self):
            self.mounts = []

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter))

        def get(self, *args, **kwargs):
            del args, kwargs
            return _Response({"status": "ok", "articles": []})

    session = Session()
    session_collector = NewsCollector(endpoint="top-headlines", session=session)
    http_get_collector = NewsCollector(endpoint="top-headlines", http_get=lambda *args, **kwargs: None)

    assert session_collector._session is session
    assert session.mounts == []
    assert http_get_collector._session is None


def test_retry_exhausted_adapter_response_keeps_retry_after_metadata(monkeypatch):
    """SELECT INVARIANT: exhausted status retries return the final response for structured scheduling metadata."""
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-key")
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    collector = NewsCollector(
        endpoint="top-headlines",
        now_provider=lambda: now,
        default_retry_after_seconds=30,
        max_retry_after_seconds=90,
    )
    adapter = collector._session.get_adapter("https://")
    sent = []

    def exhausted_send(request, **kwargs):
        del kwargs
        sent.append(request.url)
        response = __import__("requests").Response()
        response.status_code = 503
        response.headers["Retry-After"] = "120"
        response.url = request.url
        response.request = request
        return response

    monkeypatch.setattr(adapter, "send", exhausted_send)

    outcome = collector.collect_result()

    assert sent
    assert adapter.max_retries.raise_on_status is False
    assert outcome.collector_status == "failed"
    assert outcome.failure_code == "http_5xx"
    assert outcome.retryable is True
    assert outcome.retry_after_seconds == 90


def test_default_adapter_bounds_retry_after_sleep_to_configured_backoff_cap(monkeypatch):
    """SELECT INVARIANT: a provider Retry-After cannot make the default adapter sleep unboundedly."""
    from urllib3.response import HTTPResponse

    collector = NewsCollector(max_retry_backoff_seconds=30)
    retries = collector._session.get_adapter("https://").max_retries
    slept = []
    monkeypatch.setattr("urllib3.util.retry.time.sleep", slept.append)

    did_sleep = retries.sleep_for_retry(
        HTTPResponse(status=429, headers={"Retry-After": "86400"})
    )

    assert did_sleep is True
    assert slept == [30]
