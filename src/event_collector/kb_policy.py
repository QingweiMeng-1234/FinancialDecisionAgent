"""Strict loaders for versioned Entity KB resolver and freshness policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


POLICY_SCHEMA_VERSION = "1.0"

_ROOT_FIELDS = {
    "policy_type",
    "policy_version",
    "schema_version",
    "effective_from",
    "settings",
    "content_sha256",
}
_RESOLVER_FIELDS = {
    "resolution_order",
    "product_owner_auto_select",
    "ambiguous_auto_select",
    "max_selected_targets",
}
_RESOLUTION_ORDER = (
    "exchange_qualified_or_unambiguous_ticker",
    "reviewed_strong_alias",
    "canonical_name",
    "reviewed_product_owner_context",
    "weak_or_ambiguous_candidates",
)
_FRESHNESS_FIELDS = {
    "review_intervals_days",
    "contribution_multipliers",
    "ticker_identity_remains_resolvable_after_interval",
}
_REVIEW_INTERVAL_FIELDS = {
    "ticker_canonical_identity",
    "key_executive",
    "company_alias",
    "product_ownership",
    "business_line",
    "theme_exposure",
    "supplier_customer_dependency",
    "competitor_substitute",
}
_CONTRIBUTION_STATES = {"current", "aging", "stale"}


class PolicyLoadError(ValueError):
    """A policy document cannot be loaded without integrity or semantic drift."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolverPolicy:
    policy_type: str
    policy_version: str
    schema_version: str
    effective_from: str
    content_sha256: str
    resolution_order: tuple[str, ...]
    product_owner_auto_select: bool
    ambiguous_auto_select: bool
    max_selected_targets: int


@dataclass(frozen=True)
class FreshnessPolicy:
    policy_type: str
    policy_version: str
    schema_version: str
    effective_from: str
    content_sha256: str
    review_intervals_days: Mapping[str, int]
    contribution_multipliers: Mapping[str, float]
    ticker_identity_remains_resolvable_after_interval: bool


@dataclass(frozen=True)
class _PolicyDocument:
    policy_type: str
    policy_version: str
    schema_version: str
    effective_from: str
    content_sha256: str
    settings: dict[str, Any]


def compute_policy_content_sha256(raw_policy: Mapping[str, Any]) -> str:
    """Hash canonical JSON while excluding the self-referential checksum field."""

    payload = dict(raw_policy)
    payload.pop("content_sha256", None)
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise _schema_error(f"policy contains non-canonical JSON values: {error}") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_resolver_policy(
    path: str | Path,
    *,
    expected_policy_version: str | None = None,
    expected_content_sha256: str | None = None,
) -> ResolverPolicy:
    document = _load_document(
        path,
        expected_policy_type="resolver",
        expected_policy_version=expected_policy_version,
        expected_content_sha256=expected_content_sha256,
    )
    _require_exact_fields(document.settings, _RESOLVER_FIELDS, "settings")
    order = _require_string_array(document.settings["resolution_order"], "resolution_order")
    if order != _RESOLUTION_ORDER:
        raise _schema_error("resolution_order does not match resolver schema 1.0")
    product_owner_auto_select = _require_bool(
        document.settings["product_owner_auto_select"],
        "product_owner_auto_select",
    )
    ambiguous_auto_select = _require_bool(
        document.settings["ambiguous_auto_select"],
        "ambiguous_auto_select",
    )
    if product_owner_auto_select or ambiguous_auto_select:
        raise _schema_error(
            "resolver schema 1.0 forbids automatic product-owner or ambiguous selection"
        )
    max_selected_targets = _require_positive_int(
        document.settings["max_selected_targets"],
        "max_selected_targets",
    )
    if max_selected_targets != 1:
        raise _schema_error("resolver schema 1.0 requires exactly one selected target")
    return ResolverPolicy(
        policy_type=document.policy_type,
        policy_version=document.policy_version,
        schema_version=document.schema_version,
        effective_from=document.effective_from,
        content_sha256=document.content_sha256,
        resolution_order=order,
        product_owner_auto_select=product_owner_auto_select,
        ambiguous_auto_select=ambiguous_auto_select,
        max_selected_targets=max_selected_targets,
    )


def load_freshness_policy(
    path: str | Path,
    *,
    expected_policy_version: str | None = None,
    expected_content_sha256: str | None = None,
) -> FreshnessPolicy:
    document = _load_document(
        path,
        expected_policy_type="freshness",
        expected_policy_version=expected_policy_version,
        expected_content_sha256=expected_content_sha256,
    )
    _require_exact_fields(document.settings, _FRESHNESS_FIELDS, "settings")
    raw_intervals = _require_object(
        document.settings["review_intervals_days"],
        "review_intervals_days",
    )
    _require_exact_fields(raw_intervals, _REVIEW_INTERVAL_FIELDS, "review_intervals_days")
    intervals = {
        name: _require_positive_int(raw_intervals[name], f"review_intervals_days.{name}")
        for name in sorted(_REVIEW_INTERVAL_FIELDS)
    }
    raw_multipliers = _require_object(
        document.settings["contribution_multipliers"],
        "contribution_multipliers",
    )
    _require_exact_fields(raw_multipliers, _CONTRIBUTION_STATES, "contribution_multipliers")
    multipliers = {
        name: _require_nonnegative_number(
            raw_multipliers[name],
            f"contribution_multipliers.{name}",
        )
        for name in sorted(_CONTRIBUTION_STATES)
    }
    if multipliers != {"current": 1.0, "aging": 0.0, "stale": 0.0}:
        raise _schema_error("freshness schema 1.0 requires current=1, aging=0, stale=0")
    ticker_resolvable = _require_bool(
        document.settings["ticker_identity_remains_resolvable_after_interval"],
        "ticker_identity_remains_resolvable_after_interval",
    )
    if not ticker_resolvable:
        raise _schema_error("ticker identity must remain resolvable after its review interval")
    return FreshnessPolicy(
        policy_type=document.policy_type,
        policy_version=document.policy_version,
        schema_version=document.schema_version,
        effective_from=document.effective_from,
        content_sha256=document.content_sha256,
        review_intervals_days=MappingProxyType(intervals),
        contribution_multipliers=MappingProxyType(multipliers),
        ticker_identity_remains_resolvable_after_interval=ticker_resolvable,
    )


def _load_document(
    path: str | Path,
    *,
    expected_policy_type: str,
    expected_policy_version: str | None,
    expected_content_sha256: str | None,
) -> _PolicyDocument:
    policy_path = Path(path)
    try:
        raw_value = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise PolicyLoadError(
            "POLICY_FILE_UNAVAILABLE",
            f"could not read policy {policy_path}: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise PolicyLoadError(
            "POLICY_JSON_INVALID",
            f"policy is not valid JSON: {error}",
        ) from error
    raw = _require_object(raw_value, "policy")
    _require_exact_fields(raw, _ROOT_FIELDS, "policy")
    declared_checksum = _require_sha256(raw["content_sha256"], "content_sha256")
    calculated_checksum = compute_policy_content_sha256(raw)
    if declared_checksum != calculated_checksum:
        raise PolicyLoadError("POLICY_CHECKSUM_MISMATCH", "policy checksum does not match content")
    if expected_content_sha256 is not None:
        expected_checksum = _require_sha256(
            expected_content_sha256,
            "expected_content_sha256",
        )
        if calculated_checksum != expected_checksum:
            raise PolicyLoadError(
                "POLICY_CHECKSUM_MISMATCH",
                "policy checksum does not match release",
            )
    policy_type = _require_string(raw["policy_type"], "policy_type")
    if policy_type != expected_policy_type:
        raise _schema_error(
            f"expected policy_type {expected_policy_type!r}, got {policy_type!r}"
        )
    policy_version = _require_string(raw["policy_version"], "policy_version")
    if expected_policy_version is not None and policy_version != expected_policy_version:
        raise PolicyLoadError(
            "POLICY_VERSION_MISMATCH",
            f"expected policy {expected_policy_version!r}, got {policy_version!r}",
        )
    schema_version = _require_string(raw["schema_version"], "schema_version")
    if schema_version != POLICY_SCHEMA_VERSION:
        raise PolicyLoadError(
            "POLICY_SCHEMA_UNSUPPORTED",
            f"unsupported policy schema {schema_version!r}",
        )
    return _PolicyDocument(
        policy_type=policy_type,
        policy_version=policy_version,
        schema_version=schema_version,
        effective_from=_require_effective_from(raw["effective_from"]),
        content_sha256=calculated_checksum,
        settings=_require_object(raw["settings"], "settings"),
    )


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _schema_error(f"{field} must be an object")
    return value


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise _schema_error(f"{field} fields invalid: {'; '.join(details)}")


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _schema_error(f"{field} must be a non-empty string")
    return value


def _require_string_array(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _schema_error(f"{field} must be an array")
    result = tuple(_require_string(item, f"{field}[]") for item in value)
    if len(result) != len(set(result)):
        raise _schema_error(f"{field} cannot contain duplicates")
    return result


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise _schema_error(f"{field} must be a boolean")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _schema_error(f"{field} must be a positive integer")
    return value


def _require_nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _schema_error(f"{field} must be a number")
    result = float(value)
    if result < 0 or result == float("inf") or result != result:
        raise _schema_error(f"{field} must be finite and non-negative")
    return result


def _require_sha256(value: Any, field: str) -> str:
    result = _require_string(value, field)
    if len(result) != 64 or result.lower() != result:
        raise _schema_error(f"{field} must be a lowercase SHA-256 hex digest")
    if any(character not in "0123456789abcdef" for character in result):
        raise _schema_error(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def _require_effective_from(value: Any) -> str:
    result = _require_string(value, "effective_from")
    if not result.endswith("Z"):
        raise _schema_error("effective_from must end in Z")
    try:
        datetime.fromisoformat(result[:-1] + "+00:00")
    except ValueError as error:
        raise _schema_error("effective_from must be an RFC 3339 UTC timestamp") from error
    return result


def _schema_error(message: str) -> PolicyLoadError:
    return PolicyLoadError("POLICY_SCHEMA_INVALID", message)


__all__ = [
    "FreshnessPolicy",
    "POLICY_SCHEMA_VERSION",
    "PolicyLoadError",
    "ResolverPolicy",
    "compute_policy_content_sha256",
    "load_freshness_policy",
    "load_resolver_policy",
]
