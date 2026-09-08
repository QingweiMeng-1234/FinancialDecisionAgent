import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_migration import (  # noqa: E402
    apply_legacy_import_plan,
    build_legacy_import_plan,
    read_legacy_company_records,
)
from event_collector.kb_repository import SQLiteKBRepository, initialize_kb_schema  # noqa: E402


def _legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE companies (
            symbol TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            canonical_evidence_id TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO companies VALUES (?,?,?,?)",
        [
            (
                "MSFT",
                "Microsoft Corporation",
                json.dumps(["Microsoft", "微软", "Company", "Microsof…"]),
                "evidence_msft",
            ),
            (
                "AAPL",
                "Apple Inc.",
                json.dumps(["Apple", "苹果", "About"]),
                "evidence_aapl",
            ),
        ],
    )
    connection.commit()
    connection.close()


def test_legacy_read_is_source_read_only_and_plan_is_deterministic(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    _legacy_database(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    records = read_legacy_company_records(source)
    first = build_legacy_import_plan(
        records,
        snapshot_id="kbs_import_01",
        namespace="financial-agent",
        source_manifest_hash="a" * 64,
    )
    second = build_legacy_import_plan(
        records,
        snapshot_id="kbs_import_01",
        namespace="financial-agent",
        source_manifest_hash="a" * 64,
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert first == second
    assert first.content_checksum == second.content_checksum


def test_generic_and_truncated_aliases_are_quarantined_with_stable_codes(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    _legacy_database(source)

    plan = build_legacy_import_plan(
        read_legacy_company_records(source),
        snapshot_id="kbs_import_01",
        namespace="financial-agent",
        source_manifest_hash="a" * 64,
    )

    accepted = {alias.display_value for alias in plan.aliases}
    findings = {(finding.code, finding.object_value) for finding in plan.findings}
    assert accepted == {"MSFT", "Microsoft", "微软", "AAPL", "Apple", "苹果"}
    assert ("ALIAS_PROHIBITED_GENERIC", "Company") in findings
    assert ("ALIAS_PROHIBITED_GENERIC", "About") in findings
    assert ("ALIAS_TRUNCATED", "Microsof…") in findings


def test_apply_same_import_plan_is_idempotent(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target = tmp_path / "kb.sqlite3"
    _legacy_database(source)
    initialize_kb_schema(target, namespaces=("financial-agent",))
    repository = SQLiteKBRepository(target)
    plan = build_legacy_import_plan(
        read_legacy_company_records(source),
        snapshot_id="kbs_import_01",
        namespace="financial-agent",
        source_manifest_hash="a" * 64,
    )

    assert apply_legacy_import_plan(repository, plan) == "applied"
    assert apply_legacy_import_plan(repository, plan) == "already_applied"
    assert len(repository.list_aliases("kbs_import_01")) == 6
