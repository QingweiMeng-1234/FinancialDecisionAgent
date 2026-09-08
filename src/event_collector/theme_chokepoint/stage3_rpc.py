"""Opt-in local Stage 3 RPC shadow execution, never a canonical writer.

This experimental Python-local protocol is NOT the frozen paired-ablation API
or the unapproved v1.1 JCS/migration policy. Workers are trusted local programs:
one JSON request on stdin, one JSON EvidenceAcquisitionBatch on stdout. They
must bound their own provider spend and must not spawn descendants or write to
the canonical repository. Process separation is not an OS security sandbox.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


PROTOCOL = "stage3-rpc-local-v1"
SERVER_CONCURRENCY_CAP = 8


def _json(value):
    def default(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unsupported JSON type: {type(item).__name__}")
    return json.dumps(value, default=default, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _microusd(value):
    if type(value) not in {int, float}:
        raise ValueError("cost must be a JSON number, not a string or boolean")
    amount = Decimal(str(value)) * 1_000_000
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        raise ValueError("cost must be finite, nonnegative, with at most six decimals")
    return int(amount)


@dataclass(frozen=True)
class WorkerPolicy:
    # Explicit operator choices, not silently adopted candidate.2 defaults.
    max_concurrency: int
    task_timeout_seconds: float
    max_attempts: int
    task_max_sources: int
    task_max_cost_microusd: int

    def __post_init__(self):
        for name in ("max_concurrency", "max_attempts", "task_max_sources",
                     "task_max_cost_microusd"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if not 1 <= self.max_concurrency <= SERVER_CONCURRENCY_CAP:
            raise ValueError("max_concurrency exceeds server cap")
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be 1..3")
        if self.task_max_sources < 1 or self.task_max_cost_microusd < 0:
            raise ValueError("invalid task budget")
        if (not math.isfinite(self.task_timeout_seconds)
                or self.task_timeout_seconds <= 0):
            raise ValueError("invalid task deadline")


@dataclass(frozen=True)
class EvidenceTask:
    task_key: str
    task_id: str
    iteration: int
    node: object
    material_field: str
    query: str


def plan_tasks(*, execution_id, run, iteration, pairs, snapshot_hash,
               policy, query_builder):
    """Round robin over nodes, preserving each node's missing-field order."""
    queues = OrderedDict()
    for node, field in pairs:
        queue = queues.setdefault(node.node_id, [])
        if not any(existing[1] == field for existing in queue):
            queue.append((node, field))
    result = []
    scope = asdict(run.request)
    scope.pop("run_id")
    semantic_policy = asdict(policy)
    semantic_policy.pop("max_concurrency")
    for index in range(max(map(len, queues.values()), default=0)):
        for queue in queues.values():
            if index >= len(queue):
                continue
            node, field = queue[index]
            query = query_builder(run.request, node, field)
            key = _hash(dict(protocol=PROTOCOL, snapshot=snapshot_hash,
                             iteration=iteration, node=asdict(node), field=field,
                             scope=scope, query=query, policy=semantic_policy))
            result.append(EvidenceTask(key, _hash([execution_id, key]), iteration,
                                       node, field, query))
    return tuple(result)


def _decode_batch(payload):
    from .contracts import AssessmentScope, ClaimDraft, EvidenceCandidate, EvidenceAcquisitionBatch
    if set(payload) != {"candidates", "cost_usd", "request_receipt_ids"}:
        raise ValueError("invalid batch fields; explicit usage is required")
    _microusd(payload["cost_usd"])
    if not isinstance(payload["candidates"], list):
        raise ValueError("candidates must be a list")
    receipts = payload["request_receipt_ids"]
    if not isinstance(receipts, list) or any(not isinstance(x, str) or not x for x in receipts):
        raise ValueError("invalid receipt IDs")
    candidates = []
    for item in payload["candidates"]:
        values = dict(item)
        claim = dict(values.pop("claim"))
        scope = claim.get("assessment_scope")
        if scope is not None:
            claim["assessment_scope"] = AssessmentScope(**{
                **scope, "as_of_date": date.fromisoformat(scope["as_of_date"])})
        for field in ("condition_ids", "claim_capabilities", "subject_company_ids"):
            claim[field] = tuple(claim.get(field, ()))
        values["claim"] = ClaimDraft(**claim)
        for field in ("publication_date", "data_as_of_date"):
            values[field] = date.fromisoformat(values[field]) if values[field] else None
        candidates.append(EvidenceCandidate(**values))
    return EvidenceAcquisitionBatch(tuple(candidates), float(payload["cost_usd"]), tuple(receipts))


class Stage3RPCExecution:
    """One local, execution-scoped candidate sidecar with crash-released locking.

    Usage reservation is conservative: an uncertain attempt consumes its full
    reservation. No claim of vendor-side cancellation or billing rollback is
    made. Worker versions/configuration are frozen at prepare; caller must use
    a versioned, budget-enforcing trusted adapter, never an LLM-supplied command.
    """
    MAX_OUTPUT_BYTES = 8 * 1024 * 1024
    TABLES = {"rpc_meta", "rpc_tasks", "rpc_attempts", "rpc_rejections"}

    def __init__(self, path, *, execution_id, policy, worker_entrypoint,
                 worker_config, worker_version):
        if not execution_id or not worker_version or worker_entrypoint.count(":") != 1:
            raise ValueError("explicit execution and versioned worker identity required")
        self.path = Path(path).resolve()
        self.execution_id = execution_id
        self.policy = policy
        self.worker_entrypoint = worker_entrypoint
        self.worker_config = json.loads(_json(worker_config))
        self.worker_version = worker_version
        self.peak_active = 0
        self._active = {}
        self.graph = None
        self.snapshot_hash = None
        if self.path.exists():
            # Zero-write admission: never run DDL against a legacy/domain DB.
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if tables != self.TABLES:
                    raise ValueError("requires a dedicated RPC candidate database")
                row = conn.execute("SELECT protocol, execution_id FROM rpc_meta").fetchone()
                if row != (PROTOCOL, execution_id):
                    raise ValueError("RPC candidate identity mismatch")
        else:
            with self._db() as conn:
                conn.executescript("""
                    CREATE TABLE rpc_meta (
                        protocol TEXT NOT NULL, execution_id TEXT PRIMARY KEY,
                        binding TEXT, started REAL, deadline REAL,
                        spent_cost INTEGER NOT NULL DEFAULT 0,
                        spent_sources INTEGER NOT NULL DEFAULT 0,
                        result TEXT);
                    CREATE TABLE rpc_tasks (
                        task_id TEXT PRIMARY KEY, task_key TEXT UNIQUE NOT NULL,
                        iteration INTEGER NOT NULL, position INTEGER NOT NULL,
                        material_field TEXT NOT NULL, status TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        attempt_id TEXT, deadline REAL, error_code TEXT,
                        outcome TEXT, batch TEXT);
                    CREATE TABLE rpc_attempts (
                        attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                        fence INTEGER NOT NULL, started REAL NOT NULL, finished REAL,
                        deadline REAL NOT NULL, status TEXT NOT NULL, error_code TEXT,
                        reserved_cost INTEGER NOT NULL, reserved_sources INTEGER NOT NULL,
                        charged_cost INTEGER, charged_sources INTEGER, worker_pid INTEGER);
                    CREATE TABLE rpc_rejections (
                        rejection_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                        attempt_id TEXT NOT NULL, received REAL NOT NULL,
                        output_hash TEXT NOT NULL);
                """)
                conn.execute("INSERT INTO rpc_meta(protocol, execution_id) VALUES (?, ?)",
                             (PROTOCOL, execution_id))

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _lock(self):
        # Local single-coordinator guarantee, not a distributed lease service.
        with open(str(self.path) + ".lock", "a+b") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("EXECUTION_BUSY") from error
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def prepare(self, run, graph, *, contract_identity):
        snapshot = {"request": asdict(run.request), "nodes": [asdict(n) for n in graph.nodes],
                    "edges": [asdict(e) for e in graph.edges],
                    "truncation_reasons": graph.truncation_reasons,
                    "contract": contract_identity,
                    "worker": self.worker_entrypoint, "worker_version": self.worker_version,
                    "worker_config_sha256": _hash(self.worker_config)}
        snapshot["request"].pop("run_id")
        self.snapshot_hash = _hash(snapshot)
        binding = _hash(dict(snapshot=self.snapshot_hash, base_run_id=run.run_id,
                             policy=asdict(self.policy), worker=self.worker_entrypoint,
                             worker_version=self.worker_version, config=self.worker_config))
        with self._db() as conn:
            old = conn.execute("SELECT binding FROM rpc_meta").fetchone()[0]
            if old is not None and old != binding:
                raise ValueError("RPC input/configuration changed; use a new execution")
            if old is None:
                now = time.time()
                conn.execute("UPDATE rpc_meta SET binding=?, started=?, deadline=?",
                             (binding, now, now + run.request.max_time_seconds))
        self.graph = graph
        self.run = run
        self.max_cost = _microusd(run.request.max_cost_usd)
        self.max_sources = run.request.max_sources
        self._prepared_configuration = self._configuration_hash()

    def _configuration_hash(self):
        return _hash([self.worker_entrypoint, self.worker_version,
                      self.worker_config, asdict(self.policy)])

    def records(self):
        with self._db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM rpc_tasks ORDER BY iteration, position")]

    def receipts(self):
        with self._db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM rpc_attempts ORDER BY started, attempt_id")]

    def usage(self):
        with self._db() as conn:
            meta = conn.execute("SELECT * FROM rpc_meta").fetchone()
            reserved = conn.execute("""SELECT COALESCE(SUM(reserved_cost), 0),
                COALESCE(SUM(reserved_sources), 0) FROM rpc_attempts WHERE status='running'""").fetchone()
            return dict(cost_microusd=meta["spent_cost"], sources=meta["spent_sources"],
                        reserved_cost_microusd=reserved[0], reserved_sources=reserved[1],
                        elapsed_seconds=max(0, time.time() - (meta["started"] or time.time())))

    def get_result(self):
        from .repository import _stage3_from_payload
        with self._db() as conn:
            payload = conn.execute("SELECT result FROM rpc_meta").fetchone()[0]
        return _stage3_from_payload(json.loads(payload)) if payload is not None else None

    def save_result(self, run_id, **values):
        from datetime import timezone
        from .contracts import Stage3Result, RunStatus
        from .repository import _stage3_from_payload, _stage3_to_payload
        if self.graph is None or run_id != self.run.run_id:
            raise ValueError("RPC result does not match prepared base run")
        rows = self.records()
        if any(r["status"] in {"running", "pending", "failed_retryable"} for r in rows):
            raise ValueError("RPC tasks must be terminal before candidate scoring commit")
        errors = tuple(dict.fromkeys(r["error_code"] for r in rows if r["error_code"]))
        if values["status"] is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED:
            values["incomplete_reasons"] = tuple(dict.fromkeys((*values["incomplete_reasons"], *errors)))
        usage = self.usage()
        values["cost_usd_spent"] = usage["cost_microusd"] / 1_000_000
        values["elapsed_seconds"] = round(usage["elapsed_seconds"], 6)
        result = Stage3Result(run_id=run_id, created_at=datetime.now(timezone.utc), **values)
        payload = _json(_stage3_to_payload(result))
        with self._db() as conn:
            old = conn.execute("SELECT result FROM rpc_meta").fetchone()[0]
            if old is not None:
                # This seam never overwrites a previously finalized candidate.
                previous, current = json.loads(old), json.loads(payload)
                for key in ("created_at", "elapsed_seconds"):
                    previous.pop(key, None)
                    current.pop(key, None)
                if previous != current:
                    raise ValueError("IDEMPOTENCY_CONFLICT: finalized candidate differs")
                return _stage3_from_payload(json.loads(old))
            conn.execute("UPDATE rpc_meta SET result=?", (payload,))
        return result

    @property
    def active_pids(self):
        return tuple(item[0].pid for item in self._active.values())

    def _seal(self, tasks, iteration):
        with self._db() as conn:
            old = list(conn.execute("SELECT task_id FROM rpc_tasks WHERE iteration=? ORDER BY position",
                                    (iteration,)))
            if old:
                if [r[0] for r in old] != [t.task_id for t in tasks]:
                    raise ValueError("TASK_PLAN_DIVERGED")
                return
            for position, task in enumerate(tasks):
                conn.execute("""INSERT INTO rpc_tasks
                    (task_id, task_key, iteration, position, material_field, status)
                    VALUES (?, ?, ?, ?, ?, 'pending')""",
                    (task.task_id, task.task_key, iteration, position, task.material_field))

    def _claim(self, task_id):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM rpc_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row["status"] not in {"pending", "failed_retryable"}:
                return None
            meta = conn.execute("SELECT * FROM rpc_meta").fetchone()
            reserved = conn.execute("""SELECT COUNT(*), COALESCE(SUM(reserved_cost),0),
                COALESCE(SUM(reserved_sources),0) FROM rpc_attempts WHERE status='running'""").fetchone()
            if reserved[0] >= self.policy.max_concurrency:
                return None
            if (meta["spent_cost"] + reserved[1] + self.policy.task_max_cost_microusd > self.max_cost
                    or meta["spent_sources"] + reserved[2] + self.policy.task_max_sources > self.max_sources
                    or time.time() >= meta["deadline"]):
                if not reserved[0]:
                    conn.execute("UPDATE rpc_tasks SET status='exhausted', error_code='BUDGET_EXHAUSTED' WHERE task_id=?", (task_id,))
                return None
            now = time.time()
            deadline = min(meta["deadline"], now + self.policy.task_timeout_seconds)
            attempt_id, fence = uuid4().hex, row["attempt_count"] + 1
            conn.execute("""UPDATE rpc_tasks SET status='running', attempt_count=?,
                attempt_id=?, deadline=?, error_code=NULL WHERE task_id=?""",
                (fence, attempt_id, deadline, task_id))
            conn.execute("""INSERT INTO rpc_attempts
                (attempt_id, task_id, fence, started, deadline, status, reserved_cost, reserved_sources)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
                (attempt_id, task_id, fence, now, deadline,
                 self.policy.task_max_cost_microusd, self.policy.task_max_sources))
            return dict(attempt_id=attempt_id, fence=fence, deadline=deadline)

    def _finish(self, task_id, attempt, *, batch=None, error=None, expired=False):
        """CAS fences both retries and late responses, including expired leases."""
        encoded = _json(batch) if batch is not None else None
        with self._db() as conn:
            row = conn.execute("SELECT * FROM rpc_tasks WHERE task_id=?", (task_id,)).fetchone()
            if (row["status"] != "running" or row["attempt_id"] != attempt["attempt_id"]
                    or row["attempt_count"] != attempt["fence"]
                    or (not expired and time.time() >= row["deadline"])):
                # Audit a rejection without touching either the new owner or its budget.
                conn.execute("INSERT INTO rpc_rejections VALUES (?, ?, ?, ?, ?)",
                             (uuid4().hex, task_id, attempt["attempt_id"], time.time(),
                              _hash([encoded, error])))
                return False
            cost = self.policy.task_max_cost_microusd
            sources = self.policy.task_max_sources
            if batch is not None:
                cost = _microusd(batch["cost_usd"])
                sources = len(batch["candidates"])
            retryable = error in {"PROVIDER_TIMEOUT", "PROVIDER_TRANSPORT_ERROR"}
            status = ("succeeded" if error is None else
                      "failed_retryable" if retryable and row["attempt_count"] < self.policy.max_attempts else
                      "exhausted" if retryable or error == "BUDGET_EXHAUSTED" else "failed_terminal")
            outcome = ("evidence" if batch["candidates"] else "unknown") if error is None else None
            conn.execute("""UPDATE rpc_tasks SET status=?, error_code=?, outcome=?, batch=? WHERE task_id=?""",
                         (status, error, outcome, encoded if error is None else None, task_id))
            conn.execute("""UPDATE rpc_attempts SET status=?, error_code=?, finished=?,
                charged_cost=?, charged_sources=? WHERE attempt_id=? AND status='running'""",
                (status, error, time.time(), cost, sources, attempt["attempt_id"]))
            conn.execute("UPDATE rpc_meta SET spent_cost=spent_cost+?, spent_sources=spent_sources+?",
                         (cost, sources))
            return True

    def _start(self, task, attempt):
        packet = dict(protocol=PROTOCOL, task_id=task.task_id, task_key=task.task_key,
                      execution_id=self.execution_id, attempt_id=attempt["attempt_id"],
                      attempt_number=attempt["fence"], deadline=attempt["deadline"],
                      request=asdict(self.run.request), node=asdict(task.node),
                      material_field=task.material_field, query=task.query,
                      max_sources=self.policy.task_max_sources,
                      max_cost_microusd=self.policy.task_max_cost_microusd,
                      worker_config=self.worker_config)
        envelope = dict(entrypoint=self.worker_entrypoint, packet=packet,
                        parent_pid=os.getpid(), deadline=attempt["deadline"])
        # Files avoid blocked pipe writes/reads on Windows and bound parent memory.
        input_file, output_file = tempfile.TemporaryFile(), tempfile.TemporaryFile()
        try:
            input_file.write(_json(envelope).encode("utf-8"))
            input_file.seek(0)
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(str(Path(p or os.getcwd()).resolve()) for p in sys.path)
            process = subprocess.Popen(
                [sys.executable, "-u", str(Path(__file__).with_name("stage3_rpc_worker.py"))],
                stdin=input_file, stdout=output_file, stderr=subprocess.DEVNULL,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except BaseException:
            output_file.close()
            raise
        finally:
            input_file.close()
        self._active[task.task_id] = (process, output_file, attempt)
        self.peak_active = max(self.peak_active, len(self._active))
        with self._db() as conn:
            conn.execute("UPDATE rpc_attempts SET worker_pid=? WHERE attempt_id=?",
                         (process.pid, attempt["attempt_id"]))

    def _reap(self, task_id):
        process, output, attempt = self._active.pop(task_id)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        output.seek(0)
        raw = output.read(self.MAX_OUTPUT_BYTES + 1)
        output.close()
        return raw, process.returncode, attempt

    def _validate(self, payload, task):
        from .stage3 import EvidenceChokepointLoop, _validate_candidate
        batch = _decode_batch(payload)
        for candidate in batch.candidates:
            _validate_candidate(candidate)
            claim = candidate.claim
            scope = claim.assessment_scope
            if claim.node_id != task.node.node_id or claim.material_field != task.material_field:
                raise ValueError("EVIDENCE_ID_OUT_OF_SCOPE")
            if scope is not None and (
                scope.segment_id != task.node.node_id
                or scope.geography != self.run.request.region
                or scope.as_of_date != self.run.request.as_of_date
                or scope.time_horizon_months != self.run.request.time_horizon_months
            ):
                raise ValueError("EVIDENCE_ID_OUT_OF_SCOPE")
            if any(d and d > self.run.request.as_of_date
                   for d in (candidate.publication_date, candidate.data_as_of_date)):
                raise ValueError("SOURCE_DATE_INVALID")
        EvidenceChokepointLoop._materialize(batch.candidates, {}, {}, {}, set())
        return batch

    def acquire_round(self, *, run, iteration, pairs, query_builder):
        from .contracts import EvidenceAcquisitionBatch
        if self.graph is None or run != self.run:
            raise ValueError("execution must be prepared with the same run")
        if self._configuration_hash() != self._prepared_configuration:
            raise ValueError("RPC worker configuration changed after prepare")
        tasks = plan_tasks(execution_id=self.execution_id, run=run, iteration=iteration,
                           pairs=pairs, snapshot_hash=self.snapshot_hash,
                           policy=self.policy, query_builder=query_builder)
        by_id = {t.task_id: t for t in tasks}
        with self._lock():
            self._seal(tasks, iteration)
            try:
                while True:
                    # Collect independently: no wait-for-all or ordered Future.result().
                    for task_id, (process, output, attempt) in list(self._active.items()):
                        expired = time.time() >= attempt["deadline"]
                        oversized = os.fstat(output.fileno()).st_size > self.MAX_OUTPUT_BYTES
                        if process.poll() is None and not expired and not oversized:
                            continue
                        raw, code, attempt = self._reap(task_id)
                        error, payload = None, None
                        if expired or code == 124:
                            error = "PROVIDER_TIMEOUT"
                        elif oversized:
                            error = "PROVIDER_BAD_RESPONSE"
                        elif code:
                            error = "PROVIDER_TRANSPORT_ERROR"
                        else:
                            try:
                                response = json.loads(raw)
                                if "error_code" in response:
                                    error = response["error_code"]
                                    if error not in {"PROVIDER_TIMEOUT", "PROVIDER_TRANSPORT_ERROR", "PROVIDER_BAD_RESPONSE"}:
                                        error = "PROVIDER_BAD_RESPONSE"
                                else:
                                    payload = response
                                    batch = self._validate(payload, by_id[task_id])
                                    if (_microusd(batch.cost_usd) > self.policy.task_max_cost_microusd
                                            or len(batch.candidates) > self.policy.task_max_sources):
                                        error = "BUDGET_EXHAUSTED"
                            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                                error, payload = "SCHEMA_VALIDATION_FAILED", None
                        self._finish(task_id, attempt, batch=payload, error=error,
                                     expired=expired or code == 124)
                    records = [r for r in self.records() if r["iteration"] == iteration]
                    # Orphaned attempts from a crashed coordinator remain reserved
                    # until their watchdog deadline; never oversubscribe on restart.
                    for row in records:
                        if row["status"] == "running" and row["task_id"] not in self._active and time.time() >= row["deadline"]:
                            self._finish(row["task_id"], dict(attempt_id=row["attempt_id"],
                                fence=row["attempt_count"]), error="PROVIDER_TIMEOUT", expired=True)
                    records = [r for r in self.records() if r["iteration"] == iteration]
                    pending = sorted((r for r in records if r["status"] in {"pending", "failed_retryable"}),
                                     key=lambda r: (r["attempt_count"], r["position"]))
                    for row in pending:
                        attempt = self._claim(row["task_id"])
                        if attempt:
                            try:
                                self._start(by_id[row["task_id"]], attempt)
                            except (OSError, ValueError):
                                self._finish(row["task_id"], attempt, error="PROVIDER_TRANSPORT_ERROR")
                    if not any(r["status"] in {"pending", "running", "failed_retryable"}
                               for r in self.records() if r["iteration"] == iteration):
                        break
                    time.sleep(.01)
            finally:
                # Cancellation/validation failure cannot strand occupied slots.
                for task_id in list(self._active):
                    _, _, attempt = self._reap(task_id)
                    self._finish(task_id, attempt, error="PROVIDER_TRANSPORT_ERROR", expired=True)
        candidates, receipts = [], []
        for row in self.records():
            if row["iteration"] == iteration and row["status"] == "succeeded":
                batch = _decode_batch(json.loads(row["batch"]))
                candidates.extend(batch.candidates)
                receipts.extend(batch.request_receipt_ids)
        # Full round accounting (also on replay), not newly incurred billing.
        # Includes conservative reservations consumed by uncertain attempts.
        round_cost = sum(row["charged_cost"] or 0 for row in self.receipts()
                         if row["task_id"] in by_id)
        return EvidenceAcquisitionBatch(tuple(candidates), round_cost / 1_000_000, tuple(receipts))
