"""Storage-neutral Entity KB v2 contracts with fail-closed validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ContractValidationError(ValueError):
    """A KB contract violates a required invariant."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class KBMode(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    DISABLED = "disabled"


class AppliedKBMode(StrEnum):
    APPLIED = "applied"
    SEMANTIC_ONLY = "semantic_only"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class SnapshotStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    VALIDATED = "validated"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReleaseCandidateStatus(StrEnum):
    DRAFT = "draft"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    REJECTED = "rejected"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class FreshnessState(StrEnum):
    CURRENT = "current"
    AGING = "aging"
    STALE = "stale"


class AmbiguityState(StrEnum):
    UNIQUE = "unique"
    CONTEXTUAL = "contextual"
    AMBIGUOUS = "ambiguous"
    PROHIBITED = "prohibited"


class SignalDirection(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    CONTEXTUAL = "contextual"


class SignalType(StrEnum):
    DIRECT_TICKER = "direct_ticker"
    DIRECT_COMPANY_ALIAS = "direct_company_alias"
    DIRECT_PRODUCT_OWNER = "direct_product_owner"
    DIRECT_EXECUTIVE_CONTEXT = "direct_executive_context"
    INDIRECT_SUPPLIER = "indirect_supplier"
    INDIRECT_CUSTOMER = "indirect_customer"
    INDIRECT_DEPENDENCY = "indirect_dependency"
    INDIRECT_COMPETITOR = "indirect_competitor"
    INDIRECT_SUBSTITUTE = "indirect_substitute"
    CONTEXT_BUSINESS_LINE = "context_business_line"
    CONTEXT_THEME = "context_theme"


class SignalRejectionReason(StrEnum):
    STALE_FACT = "STALE_FACT"
    AGING_FACT = "AGING_FACT"
    OUTSIDE_VALIDITY = "OUTSIDE_VALIDITY"
    AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
    UNVERIFIED_FACT = "UNVERIFIED_FACT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    DUPLICATE_FACT = "DUPLICATE_FACT"
    OUTSIDE_SCOPE = "OUTSIDE_SCOPE"
    POLICY_DISABLED = "POLICY_DISABLED"
    CLASS_CAP_REACHED = "CLASS_CAP_REACHED"
    TOTAL_CAP_REACHED = "TOTAL_CAP_REACHED"
    LOWER_PRIORITY_RELATIONSHIP = "LOWER_PRIORITY_RELATIONSHIP"


@dataclass(frozen=True)
class KBReleaseContext:
    """Complete immutable Final Release tuple pinned once for one request."""

    namespace: str
    release_id: str
    snapshot_id: str
    snapshot_checksum: str
    schema_version: str
    resolver_policy_version: str
    resolver_policy_checksum: str
    freshness_policy_version: str
    freshness_policy_checksum: str
    signal_policy_version: str
    signal_policy_checksum: str
    evaluation_run_id: str
    evaluation_manifest_hash: str
    id_algorithm_version: str

    def __post_init__(self) -> None:
        for name in (
            "namespace",
            "release_id",
            "snapshot_id",
            "schema_version",
            "resolver_policy_version",
            "freshness_policy_version",
            "signal_policy_version",
            "evaluation_run_id",
            "id_algorithm_version",
        ):
            _require_nonempty(getattr(self, name), name)
        for name in (
            "snapshot_checksum",
            "resolver_policy_checksum",
            "freshness_policy_checksum",
            "signal_policy_checksum",
            "evaluation_manifest_hash",
        ):
            _require_sha256(getattr(self, name), name)


@dataclass(frozen=True)
class KBAttributionSignal:
    """Typed KB output before Retrieval assigns any numeric contribution."""

    signal_id: str
    signal_type: SignalType
    target_entity_id: str
    matched_entity_id: str
    alias_id: str | None
    relationship_id: str | None
    strength_class: str
    direction: SignalDirection
    freshness_state: FreshnessState
    ambiguity_state: AmbiguityState
    evidence_id: str | None
    kb_snapshot_id: str
    freshness_policy_version: str
    signal_policy_version: str
    eligible_for_boost: bool
    rejection_reason: SignalRejectionReason | None

    def __post_init__(self) -> None:
        for name in (
            "signal_id",
            "target_entity_id",
            "matched_entity_id",
            "kb_snapshot_id",
            "freshness_policy_version",
            "signal_policy_version",
        ):
            _require_nonempty(getattr(self, name), name)
        if self.strength_class not in {"strong", "weak", "contextual"}:
            raise _invalid("strength_class has an unknown value")

        expected_direction = _direction_for(self.signal_type)
        if self.direction is not expected_direction:
            raise _invalid(
                f"{self.signal_type.value} requires direction={expected_direction.value}"
            )

        if self.signal_type in {
            SignalType.DIRECT_TICKER,
            SignalType.DIRECT_COMPANY_ALIAS,
        }:
            _require_nonempty(self.alias_id, "alias_id")
        if self.signal_type in {
            SignalType.DIRECT_PRODUCT_OWNER,
            SignalType.DIRECT_EXECUTIVE_CONTEXT,
            SignalType.INDIRECT_SUPPLIER,
            SignalType.INDIRECT_CUSTOMER,
            SignalType.INDIRECT_DEPENDENCY,
            SignalType.INDIRECT_COMPETITOR,
            SignalType.INDIRECT_SUBSTITUTE,
        }:
            _require_nonempty(self.relationship_id, "relationship_id")

        if self.eligible_for_boost:
            if self.rejection_reason is not None:
                raise _invalid("eligible signal cannot have a rejection_reason")
            _require_nonempty(self.evidence_id, "evidence_id")
            if self.freshness_state is not FreshnessState.CURRENT:
                raise _invalid("only current signals can be boost-eligible")
            if self.ambiguity_state is not AmbiguityState.UNIQUE:
                raise _invalid("only unique signals can be boost-eligible")
        elif self.rejection_reason is None:
            raise _invalid("ineligible signal requires a rejection_reason")


def _direction_for(signal_type: SignalType) -> SignalDirection:
    if signal_type.value.startswith("direct_"):
        return SignalDirection.DIRECT
    if signal_type.value.startswith("indirect_"):
        return SignalDirection.INDIRECT
    return SignalDirection.CONTEXTUAL


def _require_nonempty(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be a non-empty string")


def _require_sha256(value: object, field: str) -> None:
    _require_nonempty(value, field)
    assert isinstance(value, str)
    if len(value) != 64 or value.lower() != value:
        raise _invalid(f"{field} must be a lowercase SHA-256 hex digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise _invalid(f"{field} must be a lowercase SHA-256 hex digest")


def _invalid(message: str) -> ContractValidationError:
    return ContractValidationError("CONTRACT_FIELD_INVALID", message)


__all__ = [
    "AmbiguityState",
    "AppliedKBMode",
    "ContractValidationError",
    "FreshnessState",
    "KBAttributionSignal",
    "KBMode",
    "KBReleaseContext",
    "ReleaseCandidateStatus",
    "ResolutionStatus",
    "SignalDirection",
    "SignalRejectionReason",
    "SignalType",
    "SnapshotStatus",
]
