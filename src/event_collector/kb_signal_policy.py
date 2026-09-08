"""Strict, checksum-verified Entity KB signal-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from event_collector.kb_contracts import KBAttributionSignal
from event_collector.kb_policy import (
    POLICY_SCHEMA_VERSION,
    PolicyLoadError,
    compute_policy_content_sha256,
)


_ROOT_FIELDS = {
    "policy_type",
    "policy_version",
    "schema_version",
    "effective_from",
    "settings",
    "content_sha256",
}
_SETTINGS_FIELDS = {
    "combination_algorithm",
    "semantic_weight",
    "signal_bases",
    "class_caps",
    "total_caps",
    "freshness_multipliers",
    "duplicate_behavior",
    "indirect_selection",
    "supported_intents",
    "direct_indirect_accumulate",
    "allow_semantic_hard_gate",
}
_SIGNAL_BASE_FIELDS = {
    "direct_ticker",
    "direct_company_alias",
    "direct_product_owner",
    "direct_executive_context",
    "indirect_dependency",
    "indirect_supplier",
    "indirect_customer",
    "indirect_substitute",
    "indirect_competitor",
    "context_business_line",
    "context_theme",
}
_CLASS_CAP_FIELDS = {"direct_identity"}
_TOTAL_CAP_FIELDS = {"direct", "indirect"}
_FRESHNESS_FIELDS = {"current", "aging", "stale"}
_SUPPORTED_COMBINATION_ALGORITHMS = {"weighted_semantic_plus_kb_v1"}
_SUPPORTED_DUPLICATE_BEHAVIORS = {"same_fact_once"}
_SUPPORTED_INDIRECT_SELECTIONS = {"contribution_desc_relationship_id_asc"}
_SUPPORTED_INTENTS = {"direct", "indirect"}


@dataclass(frozen=True)
class SignalPolicy:
    """Immutable validated signal-policy inputs; this class does not score candidates."""

    policy_type: str
    policy_version: str
    schema_version: str
    effective_from: str
    content_sha256: str
    combination_algorithm: str
    semantic_weight: float
    signal_bases: Mapping[str, float]
    class_caps: Mapping[str, float]
    total_caps: Mapping[str, float]
    freshness_multipliers: Mapping[str, float]
    duplicate_behavior: str
    indirect_selection: str
    supported_intents: tuple[str, ...]
    direct_indirect_accumulate: bool
    allow_semantic_hard_gate: bool


@dataclass(frozen=True)
class ScoredSignal:
    signal: KBAttributionSignal
    base_contribution: float
    accepted_contribution: float
    cap_reduction: float
    rejection_reason: str | None


@dataclass(frozen=True)
class SignalScore:
    signals: tuple[ScoredSignal, ...]
    kb_total_before_cap: float
    kb_total_cap: float
    kb_total_after_cap: float


def load_signal_policy(
    path: str | Path,
    *,
    expected_policy_version: str | None = None,
    expected_content_sha256: str | None = None,
) -> SignalPolicy:
    """Load a strict signal policy and fail closed on integrity or schema drift."""

    policy_path = Path(path)
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise PolicyLoadError(
            "POLICY_FILE_UNAVAILABLE",
            f"could not read signal policy {policy_path}: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise PolicyLoadError(
            "POLICY_JSON_INVALID",
            f"signal policy is not valid JSON: {error}",
        ) from error

    root = _require_object(raw, "policy")
    _require_exact_fields(root, _ROOT_FIELDS, "policy")

    declared_checksum = _require_sha256(root["content_sha256"], "content_sha256")
    calculated_checksum = compute_policy_content_sha256(root)
    if declared_checksum != calculated_checksum:
        raise PolicyLoadError(
            "POLICY_CHECKSUM_MISMATCH",
            "signal policy content does not match its declared checksum",
        )
    if expected_content_sha256 is not None and calculated_checksum != _require_sha256(
        expected_content_sha256,
        "expected_content_sha256",
    ):
        raise PolicyLoadError(
            "POLICY_CHECKSUM_MISMATCH",
            "signal policy content does not match the release checksum",
        )

    policy_type = _require_string(root["policy_type"], "policy_type")
    if policy_type != "signal":
        raise _schema_error(f"policy_type must be 'signal', got {policy_type!r}")

    policy_version = _require_string(root["policy_version"], "policy_version")
    if expected_policy_version is not None and policy_version != expected_policy_version:
        raise PolicyLoadError(
            "POLICY_VERSION_MISMATCH",
            f"expected signal policy {expected_policy_version!r}, got {policy_version!r}",
        )

    schema_version = _require_string(root["schema_version"], "schema_version")
    if schema_version != POLICY_SCHEMA_VERSION:
        raise PolicyLoadError(
            "POLICY_SCHEMA_UNSUPPORTED",
            f"unsupported signal policy schema {schema_version!r}",
        )
    effective_from = _require_effective_from(root["effective_from"])

    settings = _require_object(root["settings"], "settings")
    _require_exact_fields(settings, _SETTINGS_FIELDS, "settings")
    signal_bases = _numeric_mapping(
        settings["signal_bases"],
        _SIGNAL_BASE_FIELDS,
        "settings.signal_bases",
    )
    class_caps = _numeric_mapping(
        settings["class_caps"],
        _CLASS_CAP_FIELDS,
        "settings.class_caps",
    )
    total_caps = _numeric_mapping(
        settings["total_caps"],
        _TOTAL_CAP_FIELDS,
        "settings.total_caps",
    )
    freshness_multipliers = _numeric_mapping(
        settings["freshness_multipliers"],
        _FRESHNESS_FIELDS,
        "settings.freshness_multipliers",
    )

    combination_algorithm = _require_supported_string(
        settings["combination_algorithm"],
        "settings.combination_algorithm",
        _SUPPORTED_COMBINATION_ALGORITHMS,
    )
    duplicate_behavior = _require_supported_string(
        settings["duplicate_behavior"],
        "settings.duplicate_behavior",
        _SUPPORTED_DUPLICATE_BEHAVIORS,
    )
    indirect_selection = _require_supported_string(
        settings["indirect_selection"],
        "settings.indirect_selection",
        _SUPPORTED_INDIRECT_SELECTIONS,
    )
    supported_intents = _require_string_array(
        settings["supported_intents"],
        "settings.supported_intents",
    )
    if set(supported_intents) != _SUPPORTED_INTENTS or len(supported_intents) != 2:
        raise _schema_error("settings.supported_intents must contain direct and indirect once")

    policy = SignalPolicy(
        policy_type=policy_type,
        policy_version=policy_version,
        schema_version=schema_version,
        effective_from=effective_from,
        content_sha256=calculated_checksum,
        combination_algorithm=combination_algorithm,
        semantic_weight=_require_number(settings["semantic_weight"], "settings.semantic_weight"),
        signal_bases=MappingProxyType(signal_bases),
        class_caps=MappingProxyType(class_caps),
        total_caps=MappingProxyType(total_caps),
        freshness_multipliers=MappingProxyType(freshness_multipliers),
        duplicate_behavior=duplicate_behavior,
        indirect_selection=indirect_selection,
        supported_intents=supported_intents,
        direct_indirect_accumulate=_require_bool(
            settings["direct_indirect_accumulate"],
            "settings.direct_indirect_accumulate",
        ),
        allow_semantic_hard_gate=_require_bool(
            settings["allow_semantic_hard_gate"],
            "settings.allow_semantic_hard_gate",
        ),
    )
    _validate_policy_invariants(policy)
    return policy


def score_signals(
    signals: Any,
    policy: SignalPolicy,
    *,
    intent: str,
) -> SignalScore:
    if intent not in policy.supported_intents:
        raise PolicyLoadError("POLICY_SCHEMA_INVALID", f"unsupported intent {intent!r}")
    signal_tuple = tuple(signals)
    if intent == "indirect":
        return _score_indirect(signal_tuple, policy)
    return _score_direct(signal_tuple, policy)


def _score_direct(
    signals: tuple[KBAttributionSignal, ...],
    policy: SignalPolicy,
) -> SignalScore:
    prepared: list[tuple[KBAttributionSignal, float, str | None]] = []
    identity_remaining = policy.class_caps["direct_identity"]
    for signal in signals:
        if not signal.eligible_for_boost or signal.direction != "direct":
            prepared.append((signal, 0.0, _reason_value(signal.rejection_reason)))
            continue
        base = policy.signal_bases[signal.signal_type.value]
        reason = None
        if signal.signal_type.value in {"direct_ticker", "direct_company_alias"}:
            accepted_class = min(base, identity_remaining)
            identity_remaining -= accepted_class
            if accepted_class == 0:
                reason = "CLASS_CAP_REACHED"
            base = accepted_class
        prepared.append((signal, base, reason))
    before = round(sum(base for _, base, _ in prepared), 12)
    remaining = policy.total_caps["direct"]
    scored = []
    for signal, base, reason in prepared:
        accepted = min(base, remaining)
        remaining -= accepted
        if base > accepted and reason is None:
            reason = "TOTAL_CAP_REACHED"
        scored.append(
            ScoredSignal(
                signal=signal,
                base_contribution=base,
                accepted_contribution=round(accepted, 12),
                cap_reduction=round(base - accepted, 12),
                rejection_reason=reason,
            )
        )
    return SignalScore(
        signals=tuple(scored),
        kb_total_before_cap=before,
        kb_total_cap=policy.total_caps["direct"],
        kb_total_after_cap=round(sum(item.accepted_contribution for item in scored), 12),
    )


def _score_indirect(
    signals: tuple[KBAttributionSignal, ...],
    policy: SignalPolicy,
) -> SignalScore:
    eligible = [
        signal
        for signal in signals
        if signal.eligible_for_boost and signal.direction == "indirect"
    ]
    eligible.sort(
        key=lambda signal: (
            -policy.signal_bases[signal.signal_type.value],
            signal.relationship_id or "",
        )
    )
    winner_id = eligible[0].signal_id if eligible else None
    scored = []
    for signal in signals:
        if not signal.eligible_for_boost:
            scored.append(
                ScoredSignal(
                    signal,
                    0.0,
                    0.0,
                    0.0,
                    _reason_value(signal.rejection_reason),
                )
            )
        elif signal.signal_id != winner_id:
            scored.append(
                ScoredSignal(signal, 0.0, 0.0, 0.0, "LOWER_PRIORITY_RELATIONSHIP")
            )
        else:
            base = policy.signal_bases[signal.signal_type.value]
            accepted = min(base, policy.total_caps["indirect"])
            scored.append(
                ScoredSignal(signal, base, accepted, round(base - accepted, 12), None)
            )
    total = round(sum(item.accepted_contribution for item in scored), 12)
    return SignalScore(
        signals=tuple(scored),
        kb_total_before_cap=total,
        kb_total_cap=policy.total_caps["indirect"],
        kb_total_after_cap=total,
    )


def _reason_value(reason: Any) -> str | None:
    return None if reason is None else str(reason)


def _validate_policy_invariants(policy: SignalPolicy) -> None:
    if not 0.0 <= policy.semantic_weight <= 1.0:
        raise _schema_error("settings.semantic_weight must be between 0 and 1")
    if policy.direct_indirect_accumulate:
        raise _schema_error("direct and indirect contributions cannot accumulate in schema 1.0")
    if policy.allow_semantic_hard_gate:
        raise _schema_error("semantic hard gating is unsupported in schema 1.0")
    if policy.freshness_multipliers != {"current": 1.0, "aging": 0.0, "stale": 0.0}:
        raise _schema_error("freshness schema 1.0 requires current=1, aging=0, stale=0")
    if policy.signal_bases["direct_ticker"] != policy.signal_bases["direct_company_alias"]:
        raise _schema_error("ticker and company alias must share one identity contribution")
    if policy.class_caps["direct_identity"] > policy.total_caps["direct"]:
        raise _schema_error("direct identity cap cannot exceed direct total cap")
    indirect_values = [
        value for name, value in policy.signal_bases.items() if name.startswith("indirect_")
    ]
    if not indirect_values or min(indirect_values) <= policy.total_caps["direct"]:
        raise _schema_error("every indirect contribution must exceed the direct total cap")
    if max(indirect_values) > policy.total_caps["indirect"]:
        raise _schema_error("an indirect contribution exceeds the indirect total cap")
    if policy.signal_bases["context_business_line"] != 0.0:
        raise _schema_error("business-line context must contribute zero")
    if policy.signal_bases["context_theme"] != 0.0:
        raise _schema_error("theme context must contribute zero")


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


def _numeric_mapping(value: Any, expected: set[str], field: str) -> dict[str, float]:
    mapping = _require_object(value, field)
    _require_exact_fields(mapping, expected, field)
    return {name: _require_number(mapping[name], f"{field}.{name}") for name in sorted(expected)}


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _schema_error(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise _schema_error(f"{field} must be a finite non-negative number")
    return number


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise _schema_error(f"{field} must be a boolean")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _schema_error(f"{field} must be a non-empty string")
    return value


def _require_supported_string(value: Any, field: str, supported: set[str]) -> str:
    result = _require_string(value, field)
    if result not in supported:
        raise _schema_error(f"{field} has unsupported value {result!r}")
    return result


def _require_string_array(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _schema_error(f"{field} must be an array")
    result = tuple(_require_string(item, f"{field}[]") for item in value)
    if len(set(result)) != len(result):
        raise _schema_error(f"{field} cannot contain duplicates")
    return result


def _require_sha256(value: Any, field: str) -> str:
    result = _require_string(value, field).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise _schema_error(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def _require_effective_from(value: Any) -> str:
    result = _require_string(value, "effective_from")
    if not result.endswith("Z"):
        raise _schema_error("effective_from must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(result[:-1] + "+00:00")
    except ValueError as error:
        raise _schema_error("effective_from must be a valid RFC 3339 timestamp") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise _schema_error("effective_from must use UTC")
    return result


def _schema_error(message: str) -> PolicyLoadError:
    return PolicyLoadError("POLICY_SCHEMA_INVALID", message)


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "PolicyLoadError",
    "SignalPolicy",
    "ScoredSignal",
    "SignalScore",
    "compute_policy_content_sha256",
    "load_signal_policy",
    "score_signals",
]
