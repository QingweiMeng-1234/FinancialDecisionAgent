import pytest
import requests
from datetime import datetime, timezone

from event_collector import article_content
from event_collector.article_content import ArticleContentFetcher, ArticleFetchError, FetchFailureReason


class FakeResponse:
    def __init__(self, *, url, status_code=200, headers=None, text="<html></html>"):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html"}
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeMonotonicClock:
    def __init__(self, value=0.0):
        self.value = value
        self.sleeps = []

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


def public_resolver(hostname):
    return {"publisher.example": ["93.184.216.34"]}[hostname]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/article",
        "http://127.0.0.1/article",
        "http://0.0.0.0/article",
        "http://224.0.0.1/article",
        "http://[::1]/article",
        "https://metadata.google.internal/computeMetadata/v1/",
        "http://user:pass@publisher.example/article",
        "http://publisher.example:bad/article",
        "file:///etc/passwd",
    ],
)
def test_rejects_unsafe_or_malformed_initial_urls_before_transport(url):
    session = FakeSession([])
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch_attempt(url)

    assert error.value.reason == FetchFailureReason.INVALID_OR_PRIVATE_URL
    assert error.value.retryable is False
    assert session.calls == []


def test_rejects_nonpublic_dns_answer_before_transport_with_retryable_dns_reason():
    session = FakeSession([])
    fetcher = ArticleContentFetcher(session=session, resolver=lambda hostname: ["10.0.0.8", "93.184.216.34"])

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch_attempt("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.DNS_NONPUBLIC_RESOLUTION
    assert error.value.retryable is True
    assert session.calls == []


@pytest.mark.parametrize(
    ("resolver", "expected_reason"),
    [
        (lambda hostname: (), "dns_resolution_error"),
        (lambda hostname: (_ for _ in ()).throw(OSError("resolver unavailable")), "dns_resolution_error"),
        (lambda hostname: ["198.18.0.1"], "dns_nonpublic_resolution"),
    ],
)
def test_hostname_dns_preflight_failures_are_retryable_and_never_reach_transport(resolver, expected_reason):
    """SELECT INVARIANT: a legal hostname with unusable DNS is retryable, never an SSRF allow."""
    session = FakeSession([])
    fetcher = ArticleContentFetcher(session=session, resolver=resolver, default_retry_after_seconds=37)

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason.value == expected_reason
    assert error.value.retryable is True
    assert error.value.retry_after_seconds == 37
    assert session.calls == []


def test_default_resolver_replaces_clash_fake_ip_with_pinned_public_doh_answer(monkeypatch):
    """SELECT INVARIANT: default resolution never treats 198.18/15 as a fetch target."""
    monkeypatch.setattr(
        article_content.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("198.18.0.9", 0))],
    )
    monkeypatch.setattr(
        article_content,
        "resolve_hostname_addresses_via_doh",
        lambda hostname: ("93.184.216.34",),
    )

    assert article_content.resolve_hostname_addresses("publisher.example") == ("93.184.216.34",)


def test_pinned_transport_uses_verified_ip_with_tls_hostname_validation():
    """SELECT INVARIANT: HTTPS reaches only the supplied IP but validates the URL hostname certificate."""
    observed = {}

    class RawResponse:
        status = 200
        headers = {"Content-Type": "text/html"}

        def stream(self, chunk_size, decode_content=True):
            yield b"body"

        def close(self):
            observed["closed"] = True

    class Pool:
        def __init__(self, *args, **kwargs):
            observed["pool_args"] = args
            observed["pool_kwargs"] = kwargs

        def urlopen(self, method, target, **kwargs):
            observed["urlopen"] = (method, target, kwargs)
            return RawResponse()

    transport = article_content.PinnedPublicAddressTransport(
        https_pool_factory=Pool,
    )
    response = transport.get(
        "https://publisher.example:8443/news?id=7#ignored",
        addresses=("93.184.216.34",),
        timeout=(3.0, 11.0),
        headers={"User-Agent": "FinancialAgent/test"},
    )

    assert observed["pool_args"] == ("93.184.216.34",)
    assert observed["pool_kwargs"]["port"] == 8443
    assert observed["pool_kwargs"]["server_hostname"] == "publisher.example"
    assert observed["pool_kwargs"]["assert_hostname"] == "publisher.example"
    assert observed["pool_kwargs"]["ca_certs"] == requests.certs.where()
    method, target, kwargs = observed["urlopen"]
    assert (method, target) == ("GET", "/news?id=7")
    assert kwargs["headers"]["Host"] == "publisher.example:8443"
    assert kwargs["redirect"] is False
    assert kwargs["retries"] is False
    assert response.url == "https://publisher.example:8443/news?id=7#ignored"


def test_default_fetcher_sends_validated_addresses_to_the_pinned_transport():
    """SELECT INVARIANT: default production fetches cannot fall back to hostname DNS in requests."""
    observed = {}

    class Transport:
        def get(self, url, *, addresses, timeout, headers):
            observed["call"] = (url, addresses, timeout, headers)
            return FakeResponse(url=url)

    fetcher = ArticleContentFetcher(
        resolver=public_resolver,
        transport=Transport(),
    )
    fetcher.fetch_attempt("https://publisher.example/article")

    assert observed["call"][1] == ("93.184.216.34",)


def test_pinned_transport_receives_new_verified_addresses_for_every_redirect_hop():
    """Regression: redirect hosts are resolved and pinned independently."""
    observed = []

    class Transport:
        def get(self, url, *, addresses, timeout, headers):
            observed.append((url, addresses))
            if len(observed) == 1:
                return FakeResponse(
                    url=url,
                    status_code=302,
                    headers={"Location": "https://redirect.example/published"},
                )
            return FakeResponse(url=url)

    fetcher = ArticleContentFetcher(
        resolver=lambda hostname: {
            "publisher.example": ("93.184.216.34",),
            "redirect.example": ("93.184.216.35",),
        }[hostname],
        transport=Transport(),
    )
    fetcher.fetch_attempt("https://publisher.example/article")

    assert observed == [
        ("https://publisher.example/article", ("93.184.216.34",)),
        ("https://redirect.example/published", ("93.184.216.35",)),
    ]


def test_redirect_response_is_closed_before_the_next_pinned_hop():
    """SELECT INVARIANT: manual redirect handling cannot retain a streamed connection."""
    class RedirectResponse(FakeResponse):
        def __init__(self):
            super().__init__(
                url="https://publisher.example/article",
                status_code=302,
                headers={"Location": "https://redirect.example/published"},
            )
            self.closed = False

        def close(self):
            self.closed = True

    redirect = RedirectResponse()

    class Transport:
        def __init__(self):
            self.calls = 0

        def get(self, url, *, addresses, timeout, headers):
            self.calls += 1
            if self.calls == 1:
                return redirect
            assert redirect.closed is True
            return FakeResponse(url=url)

    fetcher = ArticleContentFetcher(
        resolver=lambda hostname: ["93.184.216.34"],
        transport=Transport(),
    )
    fetcher.fetch_attempt("https://publisher.example/article")


def test_redirect_response_is_closed_before_redirect_limit_error():
    """SELECT INVARIANT: a rejected redirect cannot leak its streamed response."""
    class RedirectResponse(FakeResponse):
        def __init__(self):
            super().__init__(
                url="https://publisher.example/article",
                status_code=302,
                headers={"Location": "https://redirect.example/published"},
            )
            self.closed = False

        def close(self):
            self.closed = True

    redirect = RedirectResponse()
    fetcher = ArticleContentFetcher(
        resolver=lambda hostname: ["93.184.216.34"],
        transport=type("Transport", (), {"get": lambda *args, **kwargs: redirect})(),
        max_redirects=0,
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch_attempt("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.NETWORK_ERROR
    assert redirect.closed is True


def test_one_fetcher_caches_verified_addresses_for_same_normalized_host_across_response_and_canonical(monkeypatch):
    """SELECT INVARIANT: one refresh does not repeatedly invoke DoH for one verified publisher host."""
    monkeypatch.setattr(article_content.trafilatura, "extract", lambda *args, **kwargs: "Article content " * 30)
    resolved_hosts = []

    def resolver(hostname):
        resolved_hosts.append(hostname)
        return ("93.184.216.34",)

    fetcher = ArticleContentFetcher(
        session=FakeSession(
            [
                FakeResponse(
                    url="https://Publisher.Example/first",
                    text='<link rel="canonical" href="https://PUBLISHER.EXAMPLE/canonical-one">',
                ),
                FakeResponse(
                    url="https://publisher.example/second",
                    text='<link rel="canonical" href="https://publisher.example/canonical-two">',
                ),
            ]
        ),
        resolver=resolver,
    )

    fetcher.fetch_attempt("https://publisher.example/first")
    fetcher.fetch_attempt("https://PUBLISHER.EXAMPLE/second")

    assert resolved_hosts == ["publisher.example"]

    fresh_fetcher = ArticleContentFetcher(
        session=FakeSession([FakeResponse(url="https://publisher.example/fresh")]),
        resolver=resolver,
    )
    fresh_fetcher.fetch_attempt("https://publisher.example/fresh")
    assert resolved_hosts == ["publisher.example", "publisher.example"]


def test_redirect_host_receives_its_own_verified_address_cache_entry():
    """SELECT INVARIANT: a redirect host is never covered by its origin's cached addresses."""
    resolved_hosts = []

    def resolver(hostname):
        resolved_hosts.append(hostname)
        return {
            "publisher.example": ("93.184.216.34",),
            "redirect.example": ("93.184.216.35",),
        }[hostname]

    fetcher = ArticleContentFetcher(
        session=FakeSession(
            [
                FakeResponse(
                    url="https://publisher.example/article",
                    status_code=302,
                    headers={"Location": "https://redirect.example/published"},
                ),
                FakeResponse(url="https://redirect.example/published"),
            ]
        ),
        resolver=resolver,
    )

    fetcher.fetch_attempt("https://publisher.example/article")

    assert resolved_hosts == ["publisher.example", "redirect.example"]


def test_redirect_target_is_resolved_and_rejected_before_second_transport_call():
    session = FakeSession(
        [
            FakeResponse(
                url="https://publisher.example/article",
                status_code=302,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )
        ]
    )
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch_attempt("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.INVALID_OR_PRIVATE_URL
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_relative_redirect_and_canonical_are_resolved_against_response_url(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(
                url="https://publisher.example/news/article",
                status_code=302,
                headers={"Location": "../published"},
            ),
            FakeResponse(
                url="https://publisher.example/published",
                text='<link rel="canonical" href="/canonical?utm_source=news">',
            ),
        ]
    )
    monkeypatch.setattr(article_content.trafilatura, "extract", lambda *args, **kwargs: "Article content " * 30)
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)

    attempt = fetcher.fetch_attempt("https://publisher.example/news/article")

    assert [call[0] for call in session.calls] == [
        "https://publisher.example/news/article",
        "https://publisher.example/published",
    ]
    assert attempt.response_url == "https://publisher.example/published"
    assert attempt.canonical_url == "https://publisher.example/canonical"


def test_same_normalized_hostname_waits_only_the_remaining_interval_before_transport():
    monotonic = FakeMonotonicClock(value=100.0)
    session = FakeSession(
        [
            FakeResponse(url="https://publisher.example/one"),
            FakeResponse(url="https://publisher.example/two"),
        ]
    )
    fetcher = ArticleContentFetcher(
        session=session,
        resolver=lambda hostname: ["93.184.216.34"],
        min_interval_seconds=1.0,
        monotonic_clock=monotonic.now,
        sleeper=monotonic.sleep,
    )

    fetcher.fetch_attempt("https://Publisher.Example/one")
    monotonic.advance(0.25)
    fetcher.fetch_attempt("https://publisher.example/two")

    assert monotonic.sleeps == [pytest.approx(0.75)]
    assert len(session.calls) == 2


def test_different_normalized_hostnames_do_not_block_each_other():
    monotonic = FakeMonotonicClock(value=100.0)
    session = FakeSession(
        [
            FakeResponse(url="https://publisher.example/one"),
            FakeResponse(url="https://other.example/two"),
        ]
    )
    fetcher = ArticleContentFetcher(
        session=session,
        resolver=lambda hostname: ["93.184.216.34"],
        min_interval_seconds=1.0,
        monotonic_clock=monotonic.now,
        sleeper=monotonic.sleep,
    )

    fetcher.fetch_attempt("https://publisher.example/one")
    fetcher.fetch_attempt("https://other.example/two")

    assert monotonic.sleeps == []
    assert len(session.calls) == 2


def test_zero_min_interval_disables_hostname_pacing():
    monotonic = FakeMonotonicClock(value=100.0)
    session = FakeSession(
        [
            FakeResponse(url="https://publisher.example/one"),
            FakeResponse(url="https://publisher.example/two"),
        ]
    )
    fetcher = ArticleContentFetcher(
        session=session,
        resolver=public_resolver,
        min_interval_seconds=0,
        monotonic_clock=monotonic.now,
        sleeper=monotonic.sleep,
    )

    fetcher.fetch_attempt("https://publisher.example/one")
    fetcher.fetch_attempt("https://publisher.example/two")

    assert monotonic.sleeps == []
    assert len(session.calls) == 2


def test_default_fetcher_enables_nonzero_hostname_pacing():
    fetcher = ArticleContentFetcher(
        session=FakeSession([]),
        resolver=public_resolver,
    )

    assert fetcher._hostname_limiter.min_interval_seconds > 0


def test_redirect_target_is_paced_as_its_own_hostname():
    monotonic = FakeMonotonicClock(value=100.0)
    session = FakeSession(
        [
            FakeResponse(url="https://target.example/seed"),
            FakeResponse(
                url="https://origin.example/article",
                status_code=302,
                headers={"Location": "https://target.example/published"},
            ),
            FakeResponse(url="https://target.example/published"),
        ]
    )
    fetcher = ArticleContentFetcher(
        session=session,
        resolver=lambda hostname: ["93.184.216.34"],
        min_interval_seconds=1.0,
        monotonic_clock=monotonic.now,
        sleeper=monotonic.sleep,
    )

    fetcher.fetch_attempt("https://target.example/seed")
    monotonic.advance(0.2)
    fetcher.fetch_attempt("https://origin.example/article")

    assert monotonic.sleeps == [pytest.approx(0.8)]
    assert [call[0] for call in session.calls] == [
        "https://target.example/seed",
        "https://origin.example/article",
        "https://target.example/published",
    ]


def test_rejects_unsafe_final_response_and_untrusted_canonical(monkeypatch):
    monkeypatch.setattr(article_content.trafilatura, "extract", lambda *args, **kwargs: "Article content " * 30)
    session = FakeSession([FakeResponse(url="http://127.0.0.1/internal")])
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)

    with pytest.raises(ArticleFetchError) as final_error:
        fetcher.fetch_attempt("https://publisher.example/article")

    assert final_error.value.reason == FetchFailureReason.INVALID_OR_PRIVATE_URL

    session = FakeSession(
        [FakeResponse(url="https://publisher.example/article", text='<link rel="canonical" href="javascript:alert(1)">')]
    )
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)
    with pytest.raises(ArticleFetchError) as canonical_error:
        fetcher.fetch_attempt("https://publisher.example/article")

    assert canonical_error.value.reason == FetchFailureReason.INVALID_OR_PRIVATE_URL


def test_request_timeout_uses_explicit_connect_read_timeout_and_has_retry_contract():
    session = FakeSession([requests.ConnectTimeout("slow connect")])
    fetcher = ArticleContentFetcher(
        session=session,
        resolver=public_resolver,
        connect_timeout_seconds=3,
        read_timeout_seconds=11,
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.NETWORK_TIMEOUT
    assert error.value.retryable is True
    assert error.value.retry_after_seconds == 60
    assert error.value.retry_after_at is None
    assert session.calls[0][1]["timeout"] == (3.0, 11.0)


def test_connection_errors_have_a_distinct_retryable_reason():
    session = FakeSession([requests.ConnectionError("connection refused")])
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver)

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.NETWORK_CONNECTION_ERROR
    assert error.value.retryable is True
    assert error.value.retry_after_seconds == 60


@pytest.mark.parametrize(
    ("status_code", "retry_after", "expected_reason", "retryable", "expected_seconds"),
    [
        (429, "120", FetchFailureReason.HTTP_429, True, 90),
        (503, None, FetchFailureReason.HTTP_5XX, True, 60),
        (404, None, FetchFailureReason.HTTP_4XX, False, None),
        (401, None, FetchFailureReason.HTTP_401_403, False, None),
    ],
)
def test_http_failures_have_stable_reason_and_retry_contract(
    status_code,
    retry_after,
    expected_reason,
    retryable,
    expected_seconds,
):
    headers = {"Content-Type": "text/html"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response = FakeResponse(url="https://publisher.example/article", status_code=status_code, headers=headers)
    response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError(response=response))
    fetcher = ArticleContentFetcher(
        session=FakeSession([response]),
        resolver=public_resolver,
        max_retry_after_seconds=90,
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == expected_reason
    assert error.value.retryable is retryable
    assert error.value.retry_after_seconds == expected_seconds


def test_http_date_retry_after_is_calculated_with_the_injected_clock():
    response = FakeResponse(
        url="https://publisher.example/article",
        status_code=429,
        headers={"Content-Type": "text/html", "Retry-After": "Wed, 21 Oct 2015 07:30:00 GMT"},
    )
    response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError(response=response))
    fetcher = ArticleContentFetcher(
        session=FakeSession([response]),
        resolver=public_resolver,
        clock=lambda: datetime(2015, 10, 21, 7, 28, tzinfo=timezone.utc),
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.HTTP_429
    assert error.value.retry_after_seconds == 120
    assert error.value.retry_after_at == datetime(2015, 10, 21, 7, 30, tzinfo=timezone.utc)


def test_invalid_retry_after_uses_the_bounded_policy_fallback():
    response = FakeResponse(
        url="https://publisher.example/article",
        status_code=429,
        headers={"Content-Type": "text/html", "Retry-After": "not-a-date"},
    )
    response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError(response=response))
    fetcher = ArticleContentFetcher(
        session=FakeSession([response]),
        resolver=public_resolver,
        default_retry_after_seconds=75,
        max_retry_after_seconds=90,
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.retry_after_seconds == 75
    assert error.value.retry_after_at is None


def test_response_body_limit_fails_before_extraction(monkeypatch):
    response = FakeResponse(
        url="https://publisher.example/article",
        headers={"Content-Type": "text/html", "Content-Length": "2048"},
        text="x" * 2048,
    )
    monkeypatch.setattr(article_content.trafilatura, "extract", lambda *args, **kwargs: pytest.fail("must not extract"))
    fetcher = ArticleContentFetcher(
        session=FakeSession([response]),
        resolver=public_resolver,
        max_response_bytes=1024,
    )

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.RESPONSE_BODY_TOO_LARGE
    assert error.value.retryable is False


def test_response_body_is_streamed_and_stops_at_the_configured_limit(monkeypatch):
    class StreamingResponse(FakeResponse):
        def __init__(self):
            super().__init__(url="https://publisher.example/article", text="unused")
            self.iterated = False

        def iter_content(self, chunk_size):
            self.iterated = True
            assert chunk_size == 1024
            yield b"a" * 1024
            yield b"b" * 1024

    response = StreamingResponse()
    monkeypatch.setattr(article_content.trafilatura, "extract", lambda *args, **kwargs: pytest.fail("must not extract"))
    session = FakeSession([response])
    fetcher = ArticleContentFetcher(session=session, resolver=public_resolver, max_response_bytes=1536)

    with pytest.raises(ArticleFetchError) as error:
        fetcher.fetch("https://publisher.example/article")

    assert error.value.reason == FetchFailureReason.RESPONSE_BODY_TOO_LARGE
    assert response.iterated is True
    assert session.calls[0][1]["stream"] is True
