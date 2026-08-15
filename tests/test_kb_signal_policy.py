import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


# The repository package initializer eagerly imports optional runtime adapters.
# Isolate this contract unit test when those unrelated runtime dependencies are unavailable.
ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_signal_policy import (
    PolicyLoadError,
    compute_policy_content_sha256,
    load_signal_policy,
)


POLICY_PATH = ROOT / "config" / "entity_kb_signal_policy_v1.json"


def test_loads_locked_price_in_policy_and_verifies_declared_checksum():
    policy = load_signal_policy(
        POLICY_PATH,
        expected_policy_version="signals.price_in.v1",
    )

    assert policy.policy_type == "signal"
    assert policy.policy_version == "signals.price_in.v1"
    assert policy.schema_version == "1.0"
    assert policy.semantic_weight == 0.70
    assert policy.signal_bases == {
        "direct_ticker": 0.10,
        "direct_company_alias": 0.10,
        "direct_product_owner": 0.05,
        "direct_executive_context": 0.02,
        "indirect_dependency": 0.30,
        "indirect_supplier": 0.27,
        "indirect_customer": 0.23,
        "indirect_substitute": 0.20,
        "indirect_competitor": 0.18,
        "context_business_line": 0.0,
        "context_theme": 0.0,
    }
    assert policy.class_caps == {"direct_identity": 0.10}
    assert policy.total_caps == {"direct": 0.15, "indirect": 0.30}
    assert policy.freshness_multipliers == {"current": 1.0, "aging": 0.0, "stale": 0.0}
    assert policy.indirect_selection == "contribution_desc_relationship_id_asc"
    assert policy.direct_indirect_accumulate is False
    assert policy.allow_semantic_hard_gate is False
    assert len(policy.content_sha256) == 64


def test_rejects_policy_content_tampering(tmp_path):
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["settings"]["semantic_weight"] = 0.71
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PolicyLoadError) as caught:
        load_signal_policy(tampered)

    assert caught.value.code == "POLICY_CHECKSUM_MISMATCH"


def test_rejects_unknown_policy_fields_even_with_a_recomputed_checksum(tmp_path):
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["settings"]["silent_new_behavior"] = True
    raw["content_sha256"] = compute_policy_content_sha256(raw)
    candidate = tmp_path / "unknown-field.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PolicyLoadError) as caught:
        load_signal_policy(candidate)

    assert caught.value.code == "POLICY_SCHEMA_INVALID"
    assert "silent_new_behavior" in str(caught.value)


def test_rejects_expected_policy_version_mismatch():
    with pytest.raises(PolicyLoadError) as caught:
        load_signal_policy(
            POLICY_PATH,
            expected_policy_version="signals.future.v2",
        )

    assert caught.value.code == "POLICY_VERSION_MISMATCH"
