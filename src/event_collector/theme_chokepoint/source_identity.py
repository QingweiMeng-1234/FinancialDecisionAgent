"""Shared canonical source and content identity for ingestion and monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from html import unescape
import json
import re
from threading import Lock
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import weakref

from event_collector.theme_chokepoint.governance import (
    CANONICAL_GOVERNANCE_BUNDLE_ID,
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    CANONICAL_SOURCE_IDENTITY_POLICY_ID,
    CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
    RuntimeGovernanceBundle,
)


TRACKING_QUERY_NAMES = {"fbclid", "gclid", "msclkid", "dclid"}
TRACKING_QUERY_PREFIXES = ("utm_",)
UNRESERVED_URL_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
)


class SourceRelation(str, Enum):
    """The observed source's relationship to the underlying source event."""

    ORIGINAL = "original"
    REDIRECT = "redirect"
    ALIAS = "alias"
    REPRINT = "reprint"


class SourceProvenance(str, Enum):
    """Confidence in the source-event relationship, not confidence in its text."""

    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceIdentity:
    """Immutable source identity resolved under the canonical URL policy."""

    canonical_url: str
    redirect_chain: tuple[str, ...]
    relation: SourceRelation
    provenance: SourceProvenance
    origin_canonical_url: str | None
    policy_id: str | None = None
    policy_sha256: str | None = None
    governance_bundle_id: str | None = None
    governance_bundle_sha256: str | None = None
    resolver_receipt_id: str | None = None
    resolver_receipt_sha256: str | None = None

    @property
    def may_establish_independent_event(self) -> bool:
        """Only a verified original source can establish an independent event."""
        return (
            self.relation is SourceRelation.ORIGINAL
            and self.provenance is SourceProvenance.VERIFIED
            and self.policy_id == CANONICAL_SOURCE_IDENTITY_POLICY_ID
            and self.policy_sha256 == CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
            and self.governance_bundle_id == CANONICAL_GOVERNANCE_BUNDLE_ID
            and self.governance_bundle_sha256
            == CANONICAL_GOVERNANCE_BUNDLE_SHA256
            and isinstance(self.resolver_receipt_id, str)
            and bool(self.resolver_receipt_id.strip())
            and isinstance(self.resolver_receipt_sha256, str)
            and len(self.resolver_receipt_sha256) == 64
        )


_CONTROLLED_RESOLVER_IDENTITIES: dict[
    int, tuple[weakref.ReferenceType[SourceIdentity], dict[str, object]]
] = {}
_CONTROLLED_RESOLVER_IDENTITIES_LOCK = Lock()


def _register_controlled_source_identity(
    identity: SourceIdentity, execution: dict[str, object]
) -> SourceIdentity:
    identity_id = id(identity)

    def discard(reference, *, expected_id=identity_id):
        with _CONTROLLED_RESOLVER_IDENTITIES_LOCK:
            current = _CONTROLLED_RESOLVER_IDENTITIES.get(expected_id)
            if current is not None and current[0] is reference:
                _CONTROLLED_RESOLVER_IDENTITIES.pop(expected_id, None)

    reference = weakref.ref(identity, discard)
    with _CONTROLLED_RESOLVER_IDENTITIES_LOCK:
        _CONTROLLED_RESOLVER_IDENTITIES[identity_id] = (reference, execution)
    return identity


def _is_controlled_source_identity(identity: SourceIdentity) -> bool:
    with _CONTROLLED_RESOLVER_IDENTITIES_LOCK:
        entry = _CONTROLLED_RESOLVER_IDENTITIES.get(id(identity))
        return entry is not None and entry[0]() is identity


def _controlled_source_resolution_execution(
    identity: SourceIdentity,
) -> dict[str, object] | None:
    with _CONTROLLED_RESOLVER_IDENTITIES_LOCK:
        entry = _CONTROLLED_RESOLVER_IDENTITIES.get(id(identity))
        if entry is None or entry[0]() is not identity:
            return None
        return dict(entry[1])


@dataclass(frozen=True)
class StoredSourceIdentity:
    source_identity_id: str
    canonical_publisher_id: str
    canonical_document_id: str | None
    origin_event_id: str | None
    canonical_url_key: str
    redirect_terminal_identity_id: str | None
    redirect_chain: tuple[str, ...]
    relation: SourceRelation
    provenance: SourceProvenance
    origin_canonical_url: str | None
    policy_id: str | None = None
    policy_sha256: str | None = None
    governance_bundle_id: str | None = None
    governance_bundle_sha256: str | None = None
    resolver_receipt_id: str | None = None
    resolver_receipt_sha256: str | None = None

    @property
    def may_establish_independent_event(self) -> bool:
        return (
            self.relation is SourceRelation.ORIGINAL
            and self.provenance is SourceProvenance.VERIFIED
            and self.policy_id == CANONICAL_SOURCE_IDENTITY_POLICY_ID
            and self.policy_sha256 == CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
            and self.governance_bundle_id == CANONICAL_GOVERNANCE_BUNDLE_ID
            and self.governance_bundle_sha256
            == CANONICAL_GOVERNANCE_BUNDLE_SHA256
            and isinstance(self.resolver_receipt_id, str)
            and bool(self.resolver_receipt_id.strip())
            and isinstance(self.resolver_receipt_sha256, str)
            and len(self.resolver_receipt_sha256) == 64
        )


@dataclass(frozen=True)
class SourceResolutionRawResponse:
    """Raw response returned by the isolated origin/redirect resolver boundary."""

    provider: str
    provider_trace_id: str
    http_status: int
    raw_body: bytes
    retrieved_at: datetime


class CanonicalSourceIdentityResolver:
    """The sole policy-bound authority allowed to mint VERIFIED provenance."""

    def __init__(
        self,
        resolution_client,
        *,
        governance_bundle_path=None,
        expected_governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    ) -> None:
        self.resolution_client = resolution_client
        self.governance_bundle_path = governance_bundle_path
        if expected_governance_bundle_sha256 != CANONICAL_GOVERNANCE_BUNDLE_SHA256:
            raise ValueError("source resolver requires the exact governance bundle")
        self.expected_governance_bundle_sha256 = expected_governance_bundle_sha256
        self.governance_bundle = self._load_governance()

    def _load_governance(self) -> RuntimeGovernanceBundle:
        bundle = RuntimeGovernanceBundle(
            self.governance_bundle_path,
            expected_sha256=self.expected_governance_bundle_sha256,
        )
        if bundle.bundle_id != CANONICAL_GOVERNANCE_BUNDLE_ID:
            raise ValueError("source resolver governance bundle ID mismatch")
        if (
            bundle.artifact_sha256["source_identity_policy"]
            != CANONICAL_SOURCE_IDENTITY_POLICY_SHA256
        ):
            raise ValueError("source identity policy SHA-256 mismatch")
        try:
            policy_payload = json.loads(
                bundle.artifact_path("source_identity_policy")
                .read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as error:
            raise ValueError("source identity policy is invalid JSON") from error
        if (
            policy_payload.get("policy_id")
            != CANONICAL_SOURCE_IDENTITY_POLICY_ID
            or policy_payload.get("status") != "frozen_for_implementation"
        ):
            raise ValueError("source identity policy identity is not canonical")
        return bundle

    def resolve(self, source_url: str) -> SourceIdentity:
        bundle = self._load_governance()
        policy_path = bundle.artifact_path("source_identity_policy")
        policy_bytes = policy_path.read_bytes()
        raw = self.resolution_client.resolve(
            source_url=source_url,
            policy_id=CANONICAL_SOURCE_IDENTITY_POLICY_ID,
            policy_sha256=CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
            policy_bytes=policy_bytes,
        )
        if not isinstance(raw, SourceResolutionRawResponse):
            raise ValueError("source resolver must return a raw resolution response")
        if (
            not raw.provider.strip()
            or not raw.provider_trace_id.strip()
            or not 200 <= raw.http_status < 300
            or not raw.raw_body
            or raw.retrieved_at.tzinfo is None
            or raw.retrieved_at.utcoffset() is None
        ):
            raise ValueError("source resolver raw response is incomplete")
        try:
            payload = json.loads(raw.raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("source resolver raw response is invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("source resolver raw response must be an object")

        observed_url = canonicalize_source_url(source_url)
        if canonicalize_source_url(str(payload.get("observed_url", ""))) != observed_url:
            raise ValueError("source resolver changed the observed URL")
        try:
            relation = SourceRelation(payload["relation"])
        except (KeyError, ValueError) as error:
            raise ValueError("source resolver relation is invalid") from error
        redirects = payload.get("redirect_chain")
        if not isinstance(redirects, list) or not all(
            isinstance(item, str) and item.strip() for item in redirects
        ):
            raise ValueError("source resolver redirect chain is invalid")
        normalized_redirects = tuple(canonicalize_source_url(item) for item in redirects)
        terminal_url = canonicalize_source_url(str(payload.get("terminal_url", "")))
        expected_terminal = normalized_redirects[-1] if normalized_redirects else observed_url
        if terminal_url != expected_terminal:
            raise ValueError("source resolver terminal URL does not match redirect chain")
        if relation is SourceRelation.ORIGINAL and normalized_redirects:
            raise ValueError("redirected source cannot be classified as original")
        if relation is SourceRelation.REDIRECT and not normalized_redirects:
            raise ValueError("redirect relation requires a redirect chain")
        origin_value = payload.get("origin_url")
        if origin_value is not None and not isinstance(origin_value, str):
            raise ValueError("source resolver origin URL is invalid")
        origin_url = canonicalize_source_url(origin_value) if origin_value else None
        status = payload.get("resolution_status")
        if status not in {item.value for item in SourceProvenance}:
            raise ValueError("source resolver provenance status is invalid")
        provenance = SourceProvenance(status)
        if provenance is SourceProvenance.VERIFIED:
            if relation in {SourceRelation.ALIAS, SourceRelation.REPRINT} and not origin_url:
                raise ValueError("verified alias or reprint requires a resolved origin")
            if relation is SourceRelation.ORIGINAL and origin_url not in {None, observed_url}:
                raise ValueError("verified original source has a conflicting origin")

        raw_sha256 = sha256(raw.raw_body).hexdigest()
        receipt_material = json.dumps(
            {
                "provider": raw.provider,
                "provider_trace_id": raw.provider_trace_id,
                "raw_response_sha256": raw_sha256,
                "policy_id": CANONICAL_SOURCE_IDENTITY_POLICY_ID,
                "policy_sha256": CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
                "governance_bundle_id": bundle.bundle_id,
                "governance_bundle_sha256": bundle.sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt_sha256 = sha256(receipt_material.encode("utf-8")).hexdigest()
        receipt_id = "source_resolution_" + receipt_sha256[:24]
        return _register_controlled_source_identity(
            SourceIdentity(
                canonical_url=terminal_url,
                redirect_chain=(observed_url, *normalized_redirects),
                relation=relation,
                provenance=provenance,
                origin_canonical_url=origin_url,
                policy_id=CANONICAL_SOURCE_IDENTITY_POLICY_ID,
                policy_sha256=CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
                governance_bundle_id=bundle.bundle_id,
                governance_bundle_sha256=bundle.sha256,
                resolver_receipt_id=receipt_id,
                resolver_receipt_sha256=receipt_sha256,
            ),
            {
                "receipt_id": receipt_id,
                "provider": raw.provider,
                "provider_trace_id": raw.provider_trace_id,
                "http_status": raw.http_status,
                "raw_body": bytes(raw.raw_body),
                "raw_response_sha256": raw_sha256,
                "retrieved_at": raw.retrieved_at,
                "policy_id": CANONICAL_SOURCE_IDENTITY_POLICY_ID,
                "policy_sha256": CANONICAL_SOURCE_IDENTITY_POLICY_SHA256,
                "governance_bundle_id": bundle.bundle_id,
                "governance_bundle_sha256": bundle.sha256,
                "receipt_sha256": receipt_sha256,
            },
        )


@dataclass(frozen=True)
class SourceVersion:
    source_version_id: str
    source_identity_id: str
    retrieved_at: datetime
    raw_bytes_sha256: str
    normalized_content_sha256: str
    quote_span_sha256: str | None
    content_type: str
    language: str
    publication_time: datetime | None
    updated_time: datetime | None
    version_sequence: int


@dataclass(frozen=True)
class SourceUsage:
    usage_id: str
    run_id: str
    stage: str
    source_snapshot_article_id: str
    source_identity_id: str
    source_version_id: str
    created_at: datetime


def _normalize_percent_encoding(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        encoded = match.group(1).upper()
        character = chr(int(encoded, 16))
        return character if character in UNRESERVED_URL_CHARACTERS else f"%{encoded}"

    return re.sub(r"%([0-9a-fA-F]{2})", replace, value)


def _canonical_netloc(parts) -> str:
    hostname = parts.hostname
    if not hostname:
        return parts.netloc.casefold()
    canonical_host = hostname.encode("idna").decode("ascii").casefold()
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    port = parts.port
    default_port = {"http": 80, "https": 443}.get(parts.scheme.casefold())
    if port is not None and port != default_port:
        canonical_host = f"{canonical_host}:{port}"
    userinfo = parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else ""
    return f"{userinfo}@{canonical_host}" if userinfo else canonical_host


def canonicalize_source_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query_items = [
        (name, item)
        for name, item in parse_qsl(parts.query, keep_blank_values=True)
        if name.casefold() not in TRACKING_QUERY_NAMES
        and not name.casefold().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(sorted(query_items, key=lambda item: (item[0], item[1])))
    path = _normalize_percent_encoding(parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (parts.scheme.casefold(), _canonical_netloc(parts), path, query, "")
    )


def resolve_source_identity(
    source_url: str,
    *,
    redirect_chain: Iterable[str] = (),
    relation: SourceRelation = SourceRelation.ORIGINAL,
    provenance: SourceProvenance = SourceProvenance.UNKNOWN,
    origin_url: str | None = None,
) -> SourceIdentity:
    """Resolve observation metadata without treating a URL variation as a new event.

    A supplied redirect chain has a terminal canonical identity.  Reprints,
    aliases, redirects, ambiguous provenance, and unknown provenance cannot
    establish an independent industry event from this policy alone.
    """
    if provenance is SourceProvenance.VERIFIED:
        raise ValueError(
            "VERIFIED provenance requires the controlled source provenance resolver"
        )
    observed_url = canonicalize_source_url(source_url)
    normalized_redirects = tuple(
        canonicalize_source_url(redirect_url) for redirect_url in redirect_chain
    )
    chain = (observed_url, *normalized_redirects)
    resolved_relation = (
        SourceRelation.REDIRECT if normalized_redirects else relation
    )
    return SourceIdentity(
        canonical_url=chain[-1],
        redirect_chain=chain,
        relation=resolved_relation,
        provenance=provenance,
        origin_canonical_url=(
            canonicalize_source_url(origin_url) if origin_url is not None else None
        ),
    )


def normalize_source_text(value: str) -> str:
    without_markup = re.sub(r"<[^>]+>", " ", unescape(value))
    return " ".join(without_markup.replace("\r\n", "\n").split())


def normalized_source_content_sha256(value: str) -> str:
    return sha256(normalize_source_text(value).encode("utf-8")).hexdigest()


def normalized_quote_sha256(value: str) -> str:
    return sha256(normalize_source_text(value).encode("utf-8")).hexdigest()
