from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from event_collector.theme_chokepoint.governance import (
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    CANONICAL_SOURCE_IDENTITY_POLICY_ID,
    CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
)
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceResolutionRawResponse,
    SourceProvenance,
    SourceRelation,
    canonicalize_source_url,
    resolve_source_identity,
)


class RecordingResolutionClient:
    def __init__(self):
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return SourceResolutionRawResponse(
            provider="controlled-origin-resolver",
            provider_trace_id="resolver-trace-001",
            http_status=200,
            raw_body=json.dumps(
                {
                    "observed_url": kwargs["source_url"],
                    "terminal_url": kwargs["source_url"],
                    "redirect_chain": [],
                    "relation": "original",
                    "origin_url": None,
                    "resolution_status": "verified",
                },
                sort_keys=True,
            ).encode("utf-8"),
            retrieved_at=datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    ("variant", "expected"),
    (
        (
            "HTTPS://Ex\u00e4mple.COM:443/report/%7Eissuer/?utm_source=feed&b=2&a=1#page",
            "https://xn--exmple-cua.com/report/~issuer?a=1&b=2",
        ),
        ("http://Example.COM:80", "http://example.com/"),
        ("https://example.com/report/", "https://example.com/report"),
    ),
)
def test_canonical_source_url_applies_the_frozen_identity_policy(variant, expected):
    """SELECT INVARIANT: URL wrappers cannot mint a new source identity."""
    assert canonicalize_source_url(variant) == expected


def test_known_redirect_resolves_to_the_same_terminal_source_identity():
    """SELECT INVARIANT: a known redirect cannot mint a second industry event."""
    redirected = resolve_source_identity(
        "https://news.example/short-link?utm_source=feed",
        redirect_chain=("https://publisher.example/reports/terminal-event/",),
    )
    terminal = resolve_source_identity("https://publisher.example/reports/terminal-event")

    assert redirected.canonical_url == terminal.canonical_url
    assert redirected.redirect_chain == (
        "https://news.example/short-link",
        "https://publisher.example/reports/terminal-event",
    )
    assert redirected.relation is SourceRelation.REDIRECT
    assert redirected.may_establish_independent_event is False


def test_unknown_paraphrased_reprint_cannot_establish_an_independent_event():
    """SELECT INVARIANT: unknown provenance fails closed for independent events."""
    identity = resolve_source_identity(
        "https://syndicator.example/paraphrased-report",
        relation=SourceRelation.REPRINT,
        provenance=SourceProvenance.UNKNOWN,
    )

    assert identity.provenance is SourceProvenance.UNKNOWN
    assert identity.relation is SourceRelation.REPRINT
    assert identity.may_establish_independent_event is False
    with pytest.raises(AttributeError):
        identity.canonical_url = "https://different.example/event"


def test_unresolved_source_identity_defaults_to_unknown_provenance():
    """SELECT INVARIANT: caller URL input alone cannot prove an original event."""
    identity = resolve_source_identity(
        "https://publisher.example/releases/caller-asserted-original"
    )

    assert identity.provenance is SourceProvenance.UNKNOWN
    assert identity.may_establish_independent_event is False


def test_plain_source_identity_api_cannot_self_attest_verified_provenance():
    """SELECT INVARIANT: only the controlled resolver may mint VERIFIED."""
    with pytest.raises(ValueError, match="controlled source provenance resolver"):
        resolve_source_identity(
            "https://publisher.example/releases/caller-asserted-original",
            relation=SourceRelation.ORIGINAL,
            provenance=SourceProvenance.VERIFIED,
        )


def test_controlled_resolver_reads_exact_policy_before_minting_verified_identity():
    """SELECT INVARIANT: VERIFIED binds a real resolution call and frozen bytes."""
    client = RecordingResolutionClient()
    resolver = CanonicalSourceIdentityResolver(client)

    identity = resolver.resolve(
        "https://publisher.example/releases/controlled-original"
    )

    assert len(client.calls) == 1
    assert client.calls[0]["policy_id"] == CANONICAL_SOURCE_IDENTITY_POLICY_ID
    assert (
        client.calls[0]["policy_sha256"]
        == CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
    )
    assert identity.provenance is SourceProvenance.VERIFIED
    assert identity.relation is SourceRelation.ORIGINAL
    assert identity.policy_id == CANONICAL_SOURCE_IDENTITY_POLICY_ID
    assert identity.policy_sha256 == CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
    assert identity.governance_bundle_sha256 == CANONICAL_GOVERNANCE_BUNDLE_SHA256
    assert identity.resolver_receipt_id.startswith("source_resolution_")
    assert len(identity.resolver_receipt_sha256) == 64
    assert identity.may_establish_independent_event is True


def test_controlled_resolver_cannot_accept_a_caller_selected_bundle_sha():
    """SELECT INVARIANT: resolver authority is pinned to the frozen bundle."""
    with pytest.raises(ValueError, match="exact governance bundle"):
        CanonicalSourceIdentityResolver(
            RecordingResolutionClient(),
            expected_governance_bundle_sha256="0" * 64,
        )
