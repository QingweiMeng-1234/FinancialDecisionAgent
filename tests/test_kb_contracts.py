from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_contracts import (  # noqa: E402
    AmbiguityState,
    ContractValidationError,
    FreshnessState,
    KBAttributionSignal,
    KBMode,
    KBReleaseContext,
    SignalDirection,
    SignalRejectionReason,
    SignalType,
)


SHA = "a" * 64


def _release_context():
    return KBReleaseContext(
        namespace="financial-agent",
        release_id="kbr_01",
        snapshot_id="kbs_01",
        snapshot_checksum=SHA,
        schema_version="2.0",
        resolver_policy_version="resolver.v1",
        resolver_policy_checksum=SHA,
        freshness_policy_version="freshness.v1",
        freshness_policy_checksum=SHA,
        signal_policy_version="signals.price_in.v1",
        signal_policy_checksum=SHA,
        evaluation_run_id="kbeval_01",
        evaluation_manifest_hash=SHA,
        id_algorithm_version="domain-id.v1",
    )


def test_release_context_is_complete_strict_and_immutable():
    context = _release_context()

    assert context.signal_policy_version == "signals.price_in.v1"
    with pytest.raises(FrozenInstanceError):
        context.release_id = "changed"
    with pytest.raises(ContractValidationError) as caught:
        KBReleaseContext(**{**context.__dict__, "snapshot_checksum": "not-a-sha"})

    assert caught.value.code == "CONTRACT_FIELD_INVALID"


def test_contract_enums_reject_unknown_semantics():
    assert KBMode("preferred") is KBMode.PREFERRED
    assert FreshnessState("aging") is FreshnessState.AGING

    with pytest.raises(ValueError):
        KBMode("automatic")
    with pytest.raises(ValueError):
        SignalType("indirect_platform_ecosystem")


def test_kb_attribution_signal_has_no_numeric_score_ownership():
    signal = KBAttributionSignal(
        signal_id="kbsig_01",
        signal_type=SignalType.INDIRECT_DEPENDENCY,
        target_entity_id="entity:company:msft",
        matched_entity_id="entity:company:openai",
        alias_id=None,
        relationship_id="rel_msft_depends_openai",
        strength_class="strong",
        direction=SignalDirection.INDIRECT,
        freshness_state=FreshnessState.CURRENT,
        ambiguity_state=AmbiguityState.UNIQUE,
        evidence_id="evidence_01",
        kb_snapshot_id="kbs_01",
        freshness_policy_version="freshness.v1",
        signal_policy_version="signals.price_in.v1",
        eligible_for_boost=True,
        rejection_reason=None,
    )

    contract_fields = {field.name for field in fields(signal)}
    assert signal.relationship_id == "rel_msft_depends_openai"
    assert "base_contribution" not in contract_fields
    assert "accepted_contribution" not in contract_fields
    assert "combined_pre_rerank_score" not in contract_fields


def test_ineligible_signal_requires_reason_and_eligible_signal_requires_evidence():
    common = {
        "signal_id": "kbsig_01",
        "signal_type": SignalType.DIRECT_COMPANY_ALIAS,
        "target_entity_id": "entity:company:msft",
        "matched_entity_id": "entity:company:msft",
        "alias_id": "alias_msft",
        "relationship_id": None,
        "strength_class": "strong",
        "direction": SignalDirection.DIRECT,
        "freshness_state": FreshnessState.CURRENT,
        "ambiguity_state": AmbiguityState.UNIQUE,
        "kb_snapshot_id": "kbs_01",
        "freshness_policy_version": "freshness.v1",
        "signal_policy_version": "signals.price_in.v1",
    }

    with pytest.raises(ContractValidationError):
        KBAttributionSignal(
            **common,
            evidence_id="evidence_01",
            eligible_for_boost=False,
            rejection_reason=None,
        )

    with pytest.raises(ContractValidationError):
        KBAttributionSignal(
            **common,
            evidence_id=None,
            eligible_for_boost=True,
            rejection_reason=None,
        )

    rejected = KBAttributionSignal(
        **common,
        evidence_id=None,
        eligible_for_boost=False,
        rejection_reason=SignalRejectionReason.MISSING_EVIDENCE,
    )
    assert rejected.rejection_reason is SignalRejectionReason.MISSING_EVIDENCE
