"""Local RPC shadow invariants; no live models, network, or production writes."""
from dataclasses import replace
from dataclasses import asdict
import json
import threading
import time

import pytest

from event_collector.theme_chokepoint import stage3_rpc
from tests.test_theme_chokepoint_stage3 import _candidate, _request, _seed_run


def test_round_robin_interleaves_nodes_and_keeps_retry_identity_stable(tmp_path):
    """SELECT INVARIANT: A's second field never precedes B's first field."""
    from event_collector.theme_chokepoint.repository import ThemeChokepointRepository

    repo = ThemeChokepointRepository(tmp_path / "base.sqlite")
    request = _seed_run(repo)
    run = repo.get_run(request.run_id)
    node = repo.get_supply_chain_graph(request.run_id).nodes[1]
    other = replace(node, node_id="B")
    pairs = ((node, "supply"), (node, "demand"), (other, "supply"))
    kwargs = dict(run=run, iteration=1, pairs=pairs, snapshot_hash="snapshot",
                  policy=policy(), query_builder=lambda r, n, f: f"{n.node_id} {f}")
    tasks = stage3_rpc.plan_tasks(execution_id="one", **kwargs)
    paired = stage3_rpc.plan_tasks(execution_id="two", **kwargs)
    assert [(t.node.node_id, t.material_field) for t in tasks] == [
        (node.node_id, "supply"), ("B", "supply"), (node.node_id, "demand")]
    assert [t.task_key for t in tasks] == [t.task_key for t in paired]
    assert all(a.task_id != b.task_id for a, b in zip(tasks, paired))
    assert tasks == stage3_rpc.plan_tasks(execution_id="one", **kwargs)


def policy(**kwargs):
    values = dict(max_concurrency=2, task_timeout_seconds=3.0,
                  max_attempts=1, task_max_sources=1, task_max_cost_microusd=100_000)
    values.update(kwargs)
    return stage3_rpc.WorkerPolicy(**values)


def setup_execution(tmp_path, *, worker_config=None, worker_policy=None, **request_values):
    from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
    repo = ThemeChokepointRepository(tmp_path / "base.sqlite")
    request = _seed_run(repo, request=_request(**request_values))
    execution = stage3_rpc.Stage3RPCExecution(
        tmp_path / "candidate.sqlite", execution_id="exec-test",
        policy=worker_policy or policy(),
        worker_entrypoint="tests.test_theme_chokepoint_stage3_rpc_worker:acquire",
        worker_config=worker_config or {},
        worker_version="fixture-v1")
    run = repo.get_run(request.run_id)
    graph = repo.get_supply_chain_graph(request.run_id)
    execution.prepare(run, graph, contract_identity="fixture-contract")
    return repo, run, graph.nodes[1], execution


def acquire(execution, run, node, fields):
    return execution.acquire_round(
        run=run, iteration=1, pairs=tuple((node, f) for f in fields),
        query_builder=lambda r, n, f: f"{n.node_id} {f}")


def test_blocked_task_does_not_block_durable_sibling_and_is_actually_reaped(tmp_path):
    """SELECT INVARIANT: B commits while A runs; A timeout frees a real process."""
    _, run, node, execution = setup_execution(
        tmp_path, worker_config={"block": ["A"]},
        worker_policy=policy(task_timeout_seconds=2.0))
    errors = []
    def work():
        try:
            acquire(execution, run, node, ("A", "B", "C"))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=work)
    thread.start()
    observed = False
    until = time.monotonic() + 6
    while thread.is_alive() and time.monotonic() < until:
        records = {r["material_field"]: r for r in execution.records()}
        if (records.get("A", {}).get("status") == "running"
                and records.get("B", {}).get("status") == "succeeded"):
            observed = True
            break
        time.sleep(.02)
    thread.join(8)
    assert not thread.is_alive()
    assert not errors
    assert observed, "B must be durable before A's timeout"
    records = {r["material_field"]: r for r in execution.records()}
    assert records["A"]["error_code"] == "PROVIDER_TIMEOUT"
    assert records["B"]["outcome"] == "unknown"
    assert records["C"]["status"] == "succeeded"
    assert execution.active_pids == ()
    assert execution.peak_active == 2


def test_restart_reuses_success_and_only_retries_failed_attempt(tmp_path):
    """SELECT INVARIANT: retries have stable task IDs, distinct attempt IDs."""
    _, run, node, execution = setup_execution(
        tmp_path, worker_config={"fail_first": ["A"]},
        worker_policy=policy(max_attempts=2))
    acquire(execution, run, node, ("A", "B"))
    before = execution.records()
    assert [r["status"] for r in before] == ["succeeded", "succeeded"]
    assert [r["attempt_count"] for r in before] == [2, 1]
    restarted = stage3_rpc.Stage3RPCExecution(
        execution.path, execution_id="exec-test", policy=execution.policy,
        worker_entrypoint=execution.worker_entrypoint,
        worker_config=execution.worker_config, worker_version="fixture-v1")
    restarted.prepare(run, execution.graph, contract_identity="fixture-contract")
    acquire(restarted, run, node, ("A", "B"))
    assert restarted.records() == before
    attempts = restarted.receipts()
    assert len(attempts) == 3
    assert len({a["attempt_id"] for a in attempts}) == 3


def test_shared_reservations_cannot_dispatch_more_than_budget(tmp_path):
    """SELECT INVARIANT: concurrent calls share one reserved cost/source budget."""
    _, run, node, execution = setup_execution(
        tmp_path, worker_config={"block": ["A", "B", "C"]},
        worker_policy=policy(task_timeout_seconds=.5), max_cost_usd=.1)
    batch = acquire(execution, run, node, ("A", "B", "C"))
    assert len(execution.receipts()) == 1
    assert execution.usage()["cost_microusd"] == 100_000
    assert batch.cost_usd == .1
    assert all(r["status"] == "exhausted" for r in execution.records())


def test_bad_evidence_is_terminal_but_good_sibling_is_saved_and_replayable(tmp_path):
    """SELECT INVARIANT: validation uses the real Stage 3 evidence validator."""
    candidate = json.loads(stage3_rpc._json(asdict(_candidate())))
    invalid = {**candidate, "exact_quote": "invented quotation"}
    _, run, node, execution = setup_execution(tmp_path, worker_config={
        "candidates": {"effective_supply_concentration": [candidate], "bad": [invalid]}})
    batch = acquire(execution, run, node, ("bad", "effective_supply_concentration"))
    assert batch.candidates == (_candidate(),)
    records = execution.records()
    assert records[0]["status"] == "failed_terminal"
    assert records[0]["error_code"] == "SCHEMA_VALIDATION_FAILED"
    assert records[1]["status"] == "succeeded"


def test_ledger_refuses_existing_unrelated_database_without_writes(tmp_path):
    """SELECT INVARIANT: the experimental sidecar never migrates the base DB."""
    import sqlite3
    path = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE user_data(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="dedicated RPC candidate database"):
        stage3_rpc.Stage3RPCExecution(path, execution_id="x", policy=policy(),
            worker_entrypoint="tests.test_theme_chokepoint_stage3_rpc_worker:acquire",
            worker_config={}, worker_version="fixture-v1")
    assert path.read_bytes() == before


def test_real_stage3_candidate_reuses_scoring_without_canonical_write(tmp_path):
    """SELECT INVARIANT: actual Stage 3 runs/replays without advancing the base run."""
    from tests.test_theme_chokepoint_stage3 import _controlled_loop, NeverResolvedScorer, RecordingAcquirer
    from event_collector.theme_chokepoint.contracts import RunStatus
    candidate = json.loads(stage3_rpc._json(asdict(_candidate())))
    repo, run, _, execution = setup_execution(tmp_path, max_iterations=1,
        worker_config={"candidates": {"effective_supply_concentration": [candidate]}})
    # A fresh, as-yet-unbound sidecar is required for the real contract identity.
    execution = stage3_rpc.Stage3RPCExecution(tmp_path / "real-candidate.sqlite",
        execution_id="real", policy=execution.policy,
        worker_entrypoint=execution.worker_entrypoint,
        worker_config=execution.worker_config, worker_version="fixture-v1")
    legacy_acquirer = RecordingAcquirer([])
    loop = _controlled_loop(repo, legacy_acquirer, NeverResolvedScorer())
    result = loop.run_candidate(run.run_id, execution=execution)
    assert len(result.evidence_cards) == 1
    assert result.claims[0].claim_id == "claim-1"
    assert legacy_acquirer.queries == []
    assert repo.get_run(run.run_id).status is RunStatus.SUPPLY_CHAIN_GRAPH_READY
    with pytest.raises(KeyError):
        repo.get_stage3_result(run.run_id)
    receipts = execution.receipts()
    assert loop.run_candidate(run.run_id, execution=execution) == result
    assert execution.receipts() == receipts
    assert execution.get_result() == result
    values = {key: getattr(result, key) for key in result.__dataclass_fields__
              if key not in {"run_id", "created_at"}}
    values["contract_version"] = "conflicting-contract"
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        execution.save_result(run.run_id, **values)


def test_fencing_rejects_old_attempt_without_settling_new_reservation(tmp_path):
    """SELECT INVARIANT: a late A1 response cannot commit after A2 is claimed."""
    _, run, node, execution = setup_execution(tmp_path, worker_policy=policy(max_attempts=2))
    tasks = stage3_rpc.plan_tasks(execution_id=execution.execution_id, run=run,
        iteration=1, pairs=((node, "A"),), snapshot_hash=execution.snapshot_hash,
        policy=execution.policy, query_builder=lambda r,n,f: f)
    execution._seal(tasks, 1)
    task_id = tasks[0].task_id
    first = execution._claim(task_id)
    execution._finish(task_id, first, error="PROVIDER_TIMEOUT", expired=True)
    second = execution._claim(task_id)
    before = execution.usage()
    assert not execution._finish(task_id, first, batch={
        "candidates": [], "cost_usd": 0, "request_receipt_ids": []})
    assert execution.records()[0]["attempt_id"] == second["attempt_id"]
    assert execution.records()[0]["status"] == "running"
    assert execution.usage()["cost_microusd"] == before["cost_microusd"]
    assert execution.usage()["reserved_cost_microusd"] == before["reserved_cost_microusd"]


def test_stable_merge_ignores_completion_order(tmp_path):
    """SELECT INVARIANT: output order is planner order, not faster-worker order."""
    base = _candidate()
    other = replace(base, article_id="article-2", origin_event_id="event-2",
                    claim=replace(base.claim, claim_id="claim-2", material_field="second"))
    _, run, node, execution = setup_execution(tmp_path, worker_config={
        "delays": {"effective_supply_concentration": .3},
        "candidates": {"effective_supply_concentration": [json.loads(stage3_rpc._json(asdict(base)))],
                       "second": [json.loads(stage3_rpc._json(asdict(other)))]}})
    batch = acquire(execution, run, node, ("effective_supply_concentration", "second"))
    assert batch.candidates == (base, other)
    finished = {r["task_id"]: r["finished"] for r in execution.receipts()}
    rows = execution.records()
    assert finished[rows[1]["task_id"]] < finished[rows[0]["task_id"]]


def test_interruption_preserves_completed_sibling_for_recovery(tmp_path, monkeypatch):
    """SELECT INVARIANT: interrupting A never deletes already committed B."""
    _, run, node, execution = setup_execution(tmp_path,
        worker_config={"block": ["A"]},
        worker_policy=policy(max_attempts=2, task_timeout_seconds=.6))
    finish = execution._finish
    interrupted = False
    def interrupt_after_commit(*args, **kwargs):
        nonlocal interrupted
        accepted = finish(*args, **kwargs)
        if kwargs.get("batch") is not None and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("controlled coordinator interruption")
        return accepted
    monkeypatch.setattr(execution, "_finish", interrupt_after_commit)
    with pytest.raises(KeyboardInterrupt):
        acquire(execution, run, node, ("A", "B"))
    assert execution.active_pids == ()
    assert execution.records()[1]["status"] == "succeeded"
    monkeypatch.setattr(execution, "_finish", finish)
    acquire(execution, run, node, ("A", "B"))
    assert execution.records()[1]["attempt_count"] == 1
    assert execution.records()[0]["attempt_count"] == 2


def test_second_coordinator_is_rejected_by_crash_released_lock(tmp_path):
    _, run, node, execution = setup_execution(tmp_path)
    with execution._lock():
        with pytest.raises(ValueError, match="EXECUTION_BUSY"):
            acquire(execution, run, node, ("A",))
    assert execution.records() == []


def test_expired_current_attempt_cannot_publish_evidence(tmp_path):
    _, run, node, execution = setup_execution(tmp_path,
        worker_policy=policy(task_timeout_seconds=.01))
    tasks = stage3_rpc.plan_tasks(execution_id=execution.execution_id, run=run,
        iteration=1, pairs=((node, "A"),), snapshot_hash=execution.snapshot_hash,
        policy=execution.policy, query_builder=lambda r,n,f: f)
    execution._seal(tasks, 1)
    attempt = execution._claim(tasks[0].task_id)
    time.sleep(.02)
    assert not execution._finish(tasks[0].task_id, attempt, batch={
        "candidates": [], "cost_usd": 0, "request_receipt_ids": []})
    assert execution.records()[0]["batch"] is None


def test_worker_configuration_cannot_change_after_prepare(tmp_path):
    """SELECT INVARIANT: replay cannot silently switch sources/model/config."""
    _, run, node, execution = setup_execution(tmp_path)
    execution.worker_config["model"] = "different-model"
    with pytest.raises(ValueError, match="configuration changed"):
        acquire(execution, run, node, ("A",))
    assert execution.records() == []


@pytest.mark.parametrize("cost", [True, "0.1", -1, float("nan")])
def test_wire_cost_requires_finite_nonnegative_json_number(cost):
    """SELECT INVARIANT: invalid usage cannot enter the shared budget ledger."""
    with pytest.raises(ValueError):
        stage3_rpc._decode_batch({"candidates": [], "cost_usd": cost, "request_receipt_ids": []})


def test_empty_and_failed_results_do_not_get_conflated_in_stage3(tmp_path):
    from tests.test_theme_chokepoint_stage3 import _controlled_loop, _unknown_score, RecordingAcquirer
    class TwoGapScorer:
        def assess(self, node, claims, evidence_cards, **kwargs):
            return _unknown_score(node.node_id, missing=("blocked", "effective_supply_concentration"))
    candidate = json.loads(stage3_rpc._json(asdict(_candidate())))
    repo, run, _, sample = setup_execution(tmp_path, max_iterations=1,
        worker_policy=policy(task_timeout_seconds=.6), worker_config={
            "block": ["blocked"], "candidates": {"effective_supply_concentration": [candidate]}})
    execution = stage3_rpc.Stage3RPCExecution(tmp_path / "actual.sqlite", execution_id="actual",
        policy=sample.policy, worker_entrypoint=sample.worker_entrypoint,
        worker_config=sample.worker_config, worker_version="fixture-v1")
    result = _controlled_loop(repo, RecordingAcquirer([]), TwoGapScorer()).run_candidate(
        run.run_id, execution=execution)
    assert len(result.evidence_cards) == 1
    assert "PROVIDER_TIMEOUT" in result.incomplete_reasons
    assert all(d.evidence_state == "unknown" for d in result.assessments[0].dimensions)


def test_task_snapshot_binds_worker_and_source_configuration(tmp_path):
    _, run, _, first = setup_execution(tmp_path)
    second = stage3_rpc.Stage3RPCExecution(tmp_path / "different.sqlite", execution_id="different",
        policy=first.policy, worker_entrypoint=first.worker_entrypoint,
        worker_config={"source_policy": "different-source-set"}, worker_version="fixture-v1")
    second.prepare(run, first.graph, contract_identity="fixture-contract")
    assert first.snapshot_hash != second.snapshot_hash


def test_reopening_candidate_closes_preflight_connection(tmp_path, monkeypatch):
    import sqlite3
    _, _, _, original = setup_execution(tmp_path)
    connections = []
    connect = sqlite3.connect
    class TrackedConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    stage3_rpc.Stage3RPCExecution(original.path, execution_id=original.execution_id,
        policy=original.policy, worker_entrypoint=original.worker_entrypoint,
        worker_config=original.worker_config, worker_version=original.worker_version)
    assert connections and all(c.closed for c in connections)
