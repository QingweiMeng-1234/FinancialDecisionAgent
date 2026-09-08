from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_attribution import CandidateMatch, attribute_matches  # noqa: E402
from event_collector.kb_signal_policy import load_signal_policy, score_signals  # noqa: E402


POLICY = load_signal_policy(ROOT / "config" / "entity_kb_signal_policy_v1.json")


def _match(signal_type, **overrides):
    values = {
        "signal_type": signal_type,
        "target_entity_id": "entity:company:msft",
        "matched_entity_id": "entity:company:msft",
        "alias_id": None,
        "relationship_id": None,
        "strength_class": "strong",
        "freshness_state": "current",
        "ambiguity_state": "unique",
        "evidence_id": "evidence_01",
        "verified": True,
    }
    values.update(overrides)
    return CandidateMatch(**values)


def test_direct_classes_apply_identity_and_total_caps_without_alias_stacking():
    signals = attribute_matches(
        (
            _match("direct_ticker", alias_id="alias_ticker"),
            _match("direct_company_alias", alias_id="alias_company"),
            _match(
                "direct_product_owner",
                matched_entity_id="entity:product:azure",
                relationship_id="rel_product",
            ),
            _match(
                "direct_executive_context",
                matched_entity_id="entity:person:ceo",
                relationship_id="rel_exec",
            ),
        ),
        kb_snapshot_id="kbs_01",
        freshness_policy_version="freshness.v1",
        signal_policy_version="signals.price_in.v1",
    )

    score = score_signals(signals, POLICY, intent="direct")

    assert score.kb_total_before_cap == 0.17
    assert score.kb_total_cap == 0.15
    assert score.kb_total_after_cap == 0.15
    assert sum(item.accepted_contribution for item in score.signals) == pytest.approx(0.15)
    identity = [
        item for item in score.signals if item.signal.signal_type.startswith("direct_")
    ][0:2]
    assert sum(item.accepted_contribution for item in identity) == pytest.approx(0.10)


def test_indirect_selection_is_deterministic_and_only_one_relationship_contributes():
    signals = attribute_matches(
        (
            _match(
                "indirect_dependency",
                relationship_id="rel_z",
                matched_entity_id="entity:company:z",
            ),
            _match(
                "indirect_dependency",
                relationship_id="rel_a",
                matched_entity_id="entity:company:a",
            ),
            _match(
                "indirect_supplier",
                relationship_id="rel_supplier",
                matched_entity_id="entity:company:supplier",
            ),
        ),
        kb_snapshot_id="kbs_01",
        freshness_policy_version="freshness.v1",
        signal_policy_version="signals.price_in.v1",
    )

    score = score_signals(signals, POLICY, intent="indirect")

    winners = [item for item in score.signals if item.accepted_contribution > 0]
    losers = [item for item in score.signals if item.accepted_contribution == 0]
    assert len(winners) == 1
    assert winners[0].signal.relationship_id == "rel_a"
    assert winners[0].accepted_contribution == 0.30
    assert {item.rejection_reason for item in losers} == {"LOWER_PRIORITY_RELATIONSHIP"}
    assert score.kb_total_after_cap == 0.30


def test_invalid_or_contextual_matches_are_visible_but_contribute_zero():
    signals = attribute_matches(
        (
            _match("direct_company_alias", alias_id="alias_stale", freshness_state="stale"),
            _match("direct_company_alias", alias_id="alias_aging", freshness_state="aging"),
            _match("direct_company_alias", alias_id="alias_amb", ambiguity_state="ambiguous"),
            _match("direct_company_alias", alias_id="alias_unverified", verified=False),
            _match("context_theme", matched_entity_id="entity:theme:ai"),
        ),
        kb_snapshot_id="kbs_01",
        freshness_policy_version="freshness.v1",
        signal_policy_version="signals.price_in.v1",
    )

    score = score_signals(signals, POLICY, intent="direct")

    assert score.kb_total_after_cap == 0.0
    assert {item.rejection_reason for item in score.signals} == {
        "STALE_FACT",
        "AGING_FACT",
        "AMBIGUOUS_ALIAS",
        "UNVERIFIED_FACT",
        "CONTEXT_ONLY",
    }
