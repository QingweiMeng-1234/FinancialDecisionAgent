from dataclasses import is_dataclass
from pathlib import Path
import sqlite3
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_repository import (  # noqa: E402
    ReleaseManifest,
    SQLiteKBRepository,
    compute_release_id,
    initialize_kb_schema,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _repository(tmp_path):
    database = tmp_path / "entity-kb.sqlite3"
    initialize_kb_schema(database, namespaces=("financial-agent",))
    return database, SQLiteKBRepository(database)


def _approved_snapshot(repository):
    repository.create_snapshot(
        snapshot_id="kbs_01",
        namespace="financial-agent",
        source_manifest_hash=SHA_A,
        schema_version="2.0",
        content_checksum=SHA_B,
    )
    repository.add_entity(
        snapshot_id="kbs_01",
        entity_id="entity:company:msft",
        entity_type="company",
        canonical_name="Microsoft Corporation",
        canonical_evidence_id="evidence_01",
    )
    repository.transition_snapshot("kbs_01", "validating")
    repository.transition_snapshot("kbs_01", "validated")
    repository.transition_snapshot("kbs_01", "approved", actor="reviewer")


def _manifest(**overrides):
    values = {
        "namespace": "financial-agent",
        "snapshot_id": "kbs_01",
        "snapshot_checksum": SHA_B,
        "resolver_policy_version": "resolver.v1",
        "resolver_policy_checksum": SHA_A,
        "freshness_policy_version": "freshness.v1",
        "freshness_policy_checksum": SHA_A,
        "signal_policy_version": "signals.price_in.v1",
        "signal_policy_checksum": SHA_A,
        "schema_version": "2.0",
        "id_algorithm_version": "domain-id.v1",
    }
    values.update(overrides)
    return ReleaseManifest(**values)


def test_bootstrap_active_state_is_nullable_and_domain_typed(tmp_path):
    _, repository = _repository(tmp_path)

    state = repository.get_active_state("financial-agent")

    assert is_dataclass(state)
    assert state.namespace == "financial-agent"
    assert state.active_release_id is None
    assert state.lock_version == 0
    assert not isinstance(state, sqlite3.Row)


def test_approved_snapshot_content_is_database_immutable(tmp_path):
    database, repository = _repository(tmp_path)
    _approved_snapshot(repository)

    connection = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match="approved snapshot content is immutable"):
        connection.execute(
            "UPDATE kb_entities SET canonical_name='Changed' "
            "WHERE snapshot_id='kbs_01' AND entity_id='entity:company:msft'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="approved snapshot content is immutable"):
        connection.execute(
            "DELETE FROM kb_entities "
            "WHERE snapshot_id='kbs_01' AND entity_id='entity:company:msft'"
        )
    connection.close()


def test_completed_evaluation_and_final_release_are_database_immutable(tmp_path):
    database, repository = _repository(tmp_path)
    _approved_snapshot(repository)
    manifest = _manifest()
    repository.create_release_candidate("kbc_01", manifest, actor="builder")
    repository.record_evaluation_run(
        candidate_id="kbc_01",
        evaluation_run_id="kbeval_01",
        evaluation_manifest_hash=SHA_A,
        result="passed",
        report_uri="reports/eval-01.json",
        report_checksum=SHA_B,
    )
    release = repository.approve_release_candidate("kbc_01", actor="reviewer")

    connection = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match="evaluation run is immutable"):
        connection.execute(
            "UPDATE kb_evaluation_runs SET result='failed' WHERE evaluation_run_id='kbeval_01'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="final release is immutable"):
        connection.execute(
            "UPDATE kb_releases SET schema_version='9.9' WHERE release_id=?",
            (release.release_id,),
        )
    connection.close()


def test_release_id_binds_every_immutable_manifest_input():
    baseline = _manifest()
    changed = _manifest(signal_policy_checksum=SHA_B)

    assert compute_release_id(baseline, "kbeval_01", SHA_A) == compute_release_id(
        baseline, "kbeval_01", SHA_A
    )
    assert compute_release_id(baseline, "kbeval_01", SHA_A) != compute_release_id(
        changed, "kbeval_01", SHA_A
    )
    assert compute_release_id(baseline, "kbeval_01", SHA_A) != compute_release_id(
        baseline, "kbeval_01", SHA_B
    )
