from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

MODULE_PATH = ROOT / "tools" / "theme-chokepoint" / "verify-repository-migration-v1.py"
SPEC = importlib.util.spec_from_file_location(
    "theme_chokepoint_repository_migration_verifier", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def test_migration_verifier_covers_schema_constraints_and_populated_round_trips(capsys):
    """SELECT INVARIANT: migration proof covers structure, data, and uniqueness."""
    VERIFIER.main()

    report = json.loads(capsys.readouterr().out)

    assert report["valid"] is True
    assert report["apply"]["legacy_theme_rows_preserved"] is True
    assert report["schema_contract"]["missing_required_tables"] == []
    assert report["schema_contract"]["missing_required_columns"] == {}
    assert report["schema_contract"]["missing_required_indexes"] == []
    assert report["schema_contract"]["missing_required_foreign_keys"] == {}
    assert report["schema_contract"]["foreign_key_check"] == []
    assert report["schema_contract"]["checked_table_count"] >= 50

    expected_round_trips = {
        "source_lineage",
        "source_resolution_receipt",
        "counter_evidence",
        "fact_verification",
        "monitor_evaluator",
        "canonical_scoring",
        "company_provider",
    }
    assert set(report["representative_round_trips"]) == expected_round_trips
    assert all(report["representative_round_trips"].values())
    assert report["uniqueness_probes"] == {
        "counter_raw_execution_replay_rejected": True,
        "provider_execution_identity_replay_rejected": True,
        "source_resolution_raw_replay_rejected": True,
        "source_resolution_trace_replay_rejected": True,
    }
