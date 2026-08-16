"""Convert article/entity matches into typed, score-free KB signals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable

from event_collector.kb_contracts import (
    AmbiguityState,
    FreshnessState,
    KBAttributionSignal,
    SignalDirection,
    SignalRejectionReason,
    SignalType,
)


@dataclass(frozen=True)
class CandidateMatch:
    signal_type: str
    target_entity_id: str
    matched_entity_id: str
    alias_id: str | None
    relationship_id: str | None
    strength_class: str
    freshness_state: str
    ambiguity_state: str
    evidence_id: str | None
    verified: bool


def attribute_matches(
    matches: Iterable[CandidateMatch],
    *,
    kb_snapshot_id: str,
    freshness_policy_version: str,
    signal_policy_version: str,
) -> tuple[KBAttributionSignal, ...]:
    signals = []
    seen_facts: set[tuple[str, str, str | None, str | None]] = set()
    for match in matches:
        signal_type = SignalType(match.signal_type)
        fact_key = (
            match.signal_type,
            match.matched_entity_id,
            match.alias_id,
            match.relationship_id,
        )
        reason = _rejection_reason(match, signal_type)
        if reason is None and fact_key in seen_facts:
            reason = SignalRejectionReason.DUPLICATE_FACT
        seen_facts.add(fact_key)
        eligible = reason is None
        signals.append(
            KBAttributionSignal(
                signal_id=_signal_id(match, kb_snapshot_id),
                signal_type=signal_type,
                target_entity_id=match.target_entity_id,
                matched_entity_id=match.matched_entity_id,
                alias_id=match.alias_id,
                relationship_id=match.relationship_id,
                strength_class=match.strength_class,
                direction=_direction(signal_type),
                freshness_state=FreshnessState(match.freshness_state),
                ambiguity_state=AmbiguityState(match.ambiguity_state),
                evidence_id=match.evidence_id,
                kb_snapshot_id=kb_snapshot_id,
                freshness_policy_version=freshness_policy_version,
                signal_policy_version=signal_policy_version,
                eligible_for_boost=eligible,
                rejection_reason=reason,
            )
        )
    return tuple(signals)


def _rejection_reason(
    match: CandidateMatch,
    signal_type: SignalType,
) -> SignalRejectionReason | None:
    if signal_type in {SignalType.CONTEXT_BUSINESS_LINE, SignalType.CONTEXT_THEME}:
        return SignalRejectionReason.CONTEXT_ONLY
    if match.freshness_state == FreshnessState.STALE:
        return SignalRejectionReason.STALE_FACT
    if match.freshness_state == FreshnessState.AGING:
        return SignalRejectionReason.AGING_FACT
    if match.ambiguity_state != AmbiguityState.UNIQUE:
        return SignalRejectionReason.AMBIGUOUS_ALIAS
    if not match.verified:
        return SignalRejectionReason.UNVERIFIED_FACT
    if not match.evidence_id:
        return SignalRejectionReason.MISSING_EVIDENCE
    return None


def _direction(signal_type: SignalType) -> SignalDirection:
    if signal_type.value.startswith("direct_"):
        return SignalDirection.DIRECT
    if signal_type.value.startswith("indirect_"):
        return SignalDirection.INDIRECT
    return SignalDirection.CONTEXTUAL


def _signal_id(match: CandidateMatch, snapshot_id: str) -> str:
    value = "\x1f".join(
        (
            snapshot_id,
            match.signal_type,
            match.target_entity_id,
            match.matched_entity_id,
            match.alias_id or "",
            match.relationship_id or "",
        )
    )
    return "kbsig_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["CandidateMatch", "attribute_matches"]
