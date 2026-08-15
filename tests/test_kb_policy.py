import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_policy import (  # noqa: E402
    PolicyLoadError,
    compute_policy_content_sha256,
    load_freshness_policy,
    load_resolver_policy,
)


def test_loads_approved_resolver_policy_without_auto_resolution():
    policy = load_resolver_policy(
        ROOT / "config" / "entity_kb_resolver_policy_v1.json",
        expected_policy_version="resolver.v1",
    )

    assert policy.resolution_order == (
        "exchange_qualified_or_unambiguous_ticker",
        "reviewed_strong_alias",
        "canonical_name",
        "reviewed_product_owner_context",
        "weak_or_ambiguous_candidates",
    )
    assert policy.product_owner_auto_select is False
    assert policy.ambiguous_auto_select is False
    assert policy.max_selected_targets == 1


def test_loads_approved_freshness_intervals_and_zero_aging_contribution():
    policy = load_freshness_policy(
        ROOT / "config" / "entity_kb_freshness_policy_v1.json",
        expected_policy_version="freshness.v1",
    )

    assert policy.review_intervals_days == {
        "ticker_canonical_identity": 365,
        "key_executive": 30,
        "company_alias": 180,
        "product_ownership": 90,
        "business_line": 180,
        "theme_exposure": 90,
        "supplier_customer_dependency": 90,
        "competitor_substitute": 90,
    }
    assert policy.contribution_multipliers == {"current": 1.0, "aging": 0.0, "stale": 0.0}
    assert policy.ticker_identity_remains_resolvable_after_interval is True


def test_operational_policy_rejects_unknown_settings_with_valid_checksum(tmp_path):
    source = ROOT / "config" / "entity_kb_resolver_policy_v1.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["settings"]["llm_guess_on_ambiguity"] = True
    raw["content_sha256"] = compute_policy_content_sha256(raw)
    candidate = tmp_path / "resolver-unknown.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PolicyLoadError) as caught:
        load_resolver_policy(candidate)

    assert caught.value.code == "POLICY_SCHEMA_INVALID"
    assert "llm_guess_on_ambiguity" in str(caught.value)
