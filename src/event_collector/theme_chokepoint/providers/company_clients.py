"""Concrete authenticated HTTP clients for isolated Company-chain boundaries."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from event_collector.theme_chokepoint.contracts import (
    BusinessFactVerificationRawProviderResponse,
    CompanyRawProviderResponse,
)
from event_collector.theme_chokepoint.source_identity import SourceResolutionRawResponse


@dataclass(frozen=True)
class AuthenticatedEndpointConfig:
    role: str
    endpoint: str
    provider_identity: str
    authenticated_boundary_id: str
    bearer_token: str = field(repr=False)
    timeout_seconds: float = 90.0

    def __post_init__(self):
        values = (
            self.role,
            self.endpoint,
            self.provider_identity,
            self.authenticated_boundary_id,
            self.bearer_token,
        )
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("authenticated endpoint configuration is incomplete")
        if not self.endpoint.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("production provider endpoint must use HTTPS")
        if self.timeout_seconds <= 0:
            raise ValueError("production provider timeout must be positive")

    @property
    def persisted_provider_identity(self) -> str:
        return f"{self.provider_identity}#{self.authenticated_boundary_id}"


class _AuthenticatedJsonClient:
    expected_role: str

    def __init__(self, config: AuthenticatedEndpointConfig, *, session=None):
        if config.role != self.expected_role:
            raise ValueError(
                f"{self.expected_role} client cannot use {config.role} endpoint"
            )
        if session is None:
            import requests

            session = requests.Session()
        if not callable(getattr(session, "post", None)):
            raise ValueError("production provider requires an independent HTTP session")
        self.config = config
        self.session = session

    def _post(self, payload: dict[str, Any]):
        response = self.session.post(
            self.config.endpoint,
            json=_jsonable(payload),
            headers={
                "Authorization": f"Bearer {self.config.bearer_token}",
                "X-Provider-Role": self.config.role,
                "X-Authenticated-Boundary-Id": self.config.authenticated_boundary_id,
            },
            timeout=self.config.timeout_seconds,
        )
        raw_body = bytes(response.content)
        trace_id = (
            response.headers.get("X-Provider-Trace-Id")
            or response.headers.get("X-Request-Id")
        )
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("provider response requires an upstream trace identity")
        try:
            cost_usd = float(response.headers.get("X-Cost-Usd", "0"))
        except (TypeError, ValueError) as error:
            raise ValueError("provider response cost is invalid") from error
        if cost_usd < 0:
            raise ValueError("provider response cost cannot be negative")
        return response, raw_body, trace_id.strip(), cost_usd

    def _company_response(self, payload: dict[str, Any]) -> CompanyRawProviderResponse:
        response, raw_body, trace_id, cost_usd = self._post(payload)
        return CompanyRawProviderResponse(
            provider=self.config.persisted_provider_identity,
            provider_trace_id=trace_id,
            http_status=int(response.status_code),
            raw_body=raw_body,
            retrieved_at=datetime.now(timezone.utc),
            cost_usd=cost_usd,
        )


class HttpCompanyDiscoveryClient(_AuthenticatedJsonClient):
    expected_role = "company-discovery"

    def map_companies(self, **kwargs):
        return self._company_response({"operation": self.expected_role, **kwargs})

    def build_challenger_sets(self, **kwargs):
        return self._company_response(
            {"operation": "company-challenger-set-v1.5", **kwargs}
        )


class HttpCompanyEvidenceClient(_AuthenticatedJsonClient):
    expected_role = "company-evidence"

    def acquire_company_evidence(self, **kwargs):
        return self._company_response({"operation": self.expected_role, **kwargs})


class HttpCompanyScoringClient(_AuthenticatedJsonClient):
    expected_role = "company-scoring"

    def score_company(self, **kwargs):
        return self._company_response({"operation": self.expected_role, **kwargs})


class HttpCompanyCriticClient(_AuthenticatedJsonClient):
    expected_role = "company-critic"

    def review_company(self, **kwargs):
        return self._company_response({"operation": self.expected_role, **kwargs})


class HttpBusinessFactVerifierClient(_AuthenticatedJsonClient):
    expected_role = "business-fact-verifier"

    def verify_fact(self, **kwargs):
        response, raw_body, trace_id, cost_usd = self._post(
            {"operation": self.expected_role, **kwargs}
        )
        return BusinessFactVerificationRawProviderResponse(
            provider_trace_id=trace_id,
            http_status=int(response.status_code),
            raw_body=raw_body,
            retrieved_at=datetime.now(timezone.utc),
            cost_usd=cost_usd,
        )


class HttpSourceResolutionClient(_AuthenticatedJsonClient):
    expected_role = "source-identity-resolver"

    def resolve(self, **kwargs):
        response, raw_body, trace_id, _ = self._post(
            {"operation": self.expected_role, **kwargs}
        )
        return SourceResolutionRawResponse(
            provider=self.config.persisted_provider_identity,
            provider_trace_id=trace_id,
            http_status=int(response.status_code),
            raw_body=raw_body,
            retrieved_at=datetime.now(timezone.utc),
        )


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    raise ValueError(f"provider request value is not JSON serializable: {type(value)!r}")


__all__ = [
    "AuthenticatedEndpointConfig",
    "HttpBusinessFactVerifierClient",
    "HttpCompanyCriticClient",
    "HttpCompanyDiscoveryClient",
    "HttpCompanyEvidenceClient",
    "HttpCompanyScoringClient",
    "HttpSourceResolutionClient",
]
