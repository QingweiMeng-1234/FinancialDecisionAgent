from __future__ import annotations

from hashlib import sha256
import json

import pytest

from event_collector.theme_chokepoint.governance import RuntimeGovernanceBundle
from event_collector.theme_chokepoint.stage3 import FrozenScoringContract


def _write_bundle(tmp_path):
    artifacts = {}
    for name in (
        "runtime_overlay",
        "business_fact_decision_table",
        "source_identity_schema",
        "source_identity_policy",
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"artifact": name}), encoding="utf-8")
        artifacts[name] = {
            "path": path.name,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    bundle_path = tmp_path / "runtime-governance-bundle-v1.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "theme-chokepoint-runtime-governance-bundle-v1",
                "bundle_id": "theme-chokepoint-evidence-trust-runtime-v1",
                "status": "frozen_for_implementation",
                "artifacts": artifacts,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return bundle_path, artifacts


def test_runtime_governance_bundle_requires_caller_expected_sha(tmp_path):
    """SELECT INVARIANT: a runtime cannot trust an unpinned governance bundle."""
    bundle_path, _ = _write_bundle(tmp_path)

    with pytest.raises(ValueError, match="expected governance bundle SHA-256"):
        RuntimeGovernanceBundle(bundle_path)


def test_runtime_governance_bundle_verifies_every_child_artifact(tmp_path):
    """SELECT INVARIANT: a child control cannot drift behind a valid bundle file."""
    bundle_path, artifacts = _write_bundle(tmp_path)
    expected_sha256 = sha256(bundle_path.read_bytes()).hexdigest()

    bundle = RuntimeGovernanceBundle(
        bundle_path,
        expected_sha256=expected_sha256,
    )
    assert bundle.bundle_id == "theme-chokepoint-evidence-trust-runtime-v1"

    decision_table = tmp_path / artifacts["business_fact_decision_table"]["path"]
    decision_table.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="business_fact_decision_table SHA-256"):
        RuntimeGovernanceBundle(
            bundle_path,
            expected_sha256=expected_sha256,
        )


def test_unfrozen_runtime_overlay_cannot_start_without_governance_bundle():
    """SELECT INVARIANT: overlay opt-in cannot bypass the other runtime controls."""
    with pytest.raises(ValueError, match="governance bundle"):
        FrozenScoringContract(
            expected_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
            allow_unfrozen_overlay=True,
        )
