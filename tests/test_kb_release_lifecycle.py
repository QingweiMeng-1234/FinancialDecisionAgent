from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_release import (  # noqa: E402
    KBActivationError,
    SQLiteReleaseController,
    SQLiteReleaseProvider,
)
from event_collector.kb_repository import (  # noqa: E402
    KBRepositoryError,
    ReleaseManifest,
    SQLiteKBRepository,
    initialize_kb_schema,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _manifest(snapshot_id, checksum):
    return ReleaseManifest(
        namespace="financial-agent",
        snapshot_id=snapshot_id,
        snapshot_checksum=checksum,
        resolver_policy_version="resolver.v1",
        resolver_policy_checksum=SHA_A,
        freshness_policy_version="freshness.v1",
        freshness_policy_checksum=SHA_A,
        signal_policy_version="signals.price_in.v1",
        signal_policy_checksum=SHA_A,
        schema_version="2.0",
        id_algorithm_version="domain-id.v1",
    )


def _release(repository, suffix, *, evaluation_result="passed"):
    snapshot_id = f"kbs_{suffix}"
    checksum = hashlib_for(suffix)
    repository.create_snapshot(
        snapshot_id=snapshot_id,
        namespace="financial-agent",
        source_manifest_hash=SHA_A,
        schema_version="2.0",
        content_checksum=checksum,
    )
    repository.transition_snapshot(snapshot_id, "validating")
    repository.transition_snapshot(snapshot_id, "validated")
    repository.transition_snapshot(snapshot_id, "approved", actor="reviewer")
    candidate_id = f"kbc_{suffix}"
    repository.create_release_candidate(
        candidate_id,
        _manifest(snapshot_id, checksum),
        actor="builder",
    )
    repository.record_evaluation_run(
        candidate_id=candidate_id,
        evaluation_run_id=f"kbeval_{suffix}",
        evaluation_manifest_hash=hashlib_for("eval-" + suffix),
        result=evaluation_result,
        report_uri=f"reports/{suffix}.json",
        report_checksum=SHA_B,
    )
    return candidate_id


def hashlib_for(value):
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def test_failed_evaluation_cannot_create_final_release(tmp_path):
    database = tmp_path / "kb.sqlite3"
    initialize_kb_schema(database, namespaces=("financial-agent",))
    repository = SQLiteKBRepository(database)
    candidate_id = _release(repository, "failed", evaluation_result="failed")

    with pytest.raises(KBRepositoryError) as caught:
        repository.approve_release_candidate(candidate_id, actor="reviewer")

    assert caught.value.code == "KB_EVALUATION_NOT_APPROVED"


def test_concurrent_compare_and_swap_allows_exactly_one_activation(tmp_path):
    database = tmp_path / "kb.sqlite3"
    initialize_kb_schema(database, namespaces=("financial-agent",))
    repository = SQLiteKBRepository(database)
    release_a = repository.approve_release_candidate(
        _release(repository, "a"), actor="reviewer"
    )
    release_b = repository.approve_release_candidate(
        _release(repository, "b"), actor="reviewer"
    )
    barrier = threading.Barrier(2)

    def activate(release_id):
        controller = SQLiteReleaseController(database)
        barrier.wait()
        try:
            return controller.activate(
                namespace="financial-agent",
                target_release_id=release_id,
                expected_current_release_id=None,
                expected_lock_version=0,
                actor="operator",
                reason="first activation",
            )
        except KBActivationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, (release_a.release_id, release_b.release_id)))

    successes = [outcome for outcome in outcomes if not isinstance(outcome, str)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, str)]
    assert len(successes) == 1
    assert failures == ["KB_ACTIVATION_CONFLICT"]
    state = repository.get_active_state("financial-agent")
    assert state.active_release_id == successes[0].to_release_id
    assert state.lock_version == 1


def test_provider_pins_complete_release_and_rollback_appends_event(tmp_path):
    database = tmp_path / "kb.sqlite3"
    initialize_kb_schema(database, namespaces=("financial-agent",))
    repository = SQLiteKBRepository(database)
    release_a = repository.approve_release_candidate(
        _release(repository, "a"), actor="reviewer"
    )
    release_b = repository.approve_release_candidate(
        _release(repository, "b"), actor="reviewer"
    )
    controller = SQLiteReleaseController(database)
    controller.activate(
        namespace="financial-agent",
        target_release_id=release_a.release_id,
        expected_current_release_id=None,
        expected_lock_version=0,
        actor="operator",
        reason="initial",
    )
    controller.activate(
        namespace="financial-agent",
        target_release_id=release_b.release_id,
        expected_current_release_id=release_a.release_id,
        expected_lock_version=1,
        actor="operator",
        reason="promote b",
    )

    pinned = SQLiteReleaseProvider(database).pin_active_release(
        "financial-agent", "2026-08-15T08:00:00Z"
    )
    assert pinned.release_id == release_b.release_id
    assert pinned.snapshot_id == "kbs_b"

    rollback = controller.rollback(
        namespace="financial-agent",
        target_release_id=release_a.release_id,
        expected_current_release_id=release_b.release_id,
        expected_lock_version=2,
        actor="operator",
        reason="regression",
    )

    assert rollback.operation == "rollback"
    assert rollback.resulting_lock_version == 3
    assert repository.get_active_state("financial-agent").active_release_id == release_a.release_id
    events = controller.list_events("financial-agent")
    assert [event.operation for event in events] == ["activate", "activate", "rollback"]
