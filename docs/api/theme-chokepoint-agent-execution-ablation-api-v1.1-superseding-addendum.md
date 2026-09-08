# Theme Chokepoint Agent Execution Ablation API and Data Contract v1.1 — Superseding Addendum

## Document control and precedence

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-draft` |
| Predecessor | `docs/api/theme-chokepoint-agent-execution-ablation-api.md` |
| Predecessor SHA-256 | `f0ce51926898fef7f5a8f0f68b5289c414c3b61addd93036ccecd1f458fec32e` |
| Controls | v1.0 sections 5–9 where this addendum is more specific |
| Related | PRD, System Design, and Migration Contract v1.1 |

This path is a new versioned contract draft. It does not modify the v1.0 API document, register an API, or make any tool available.

## 1. Common mutation contract

All v1.1 mutation requests use the existing v1 envelope and require a nonempty opaque `idempotency_key`. The service stores the key only in the operation scope defined by System Design v1.1 §3 and stores the canonical request fingerprint. It returns one of the following deterministic outcomes:

| Condition | API status / error | Required behavior |
| --- | --- | --- |
| First valid request | `success` or `degraded` | Atomically reserve one logical operation before side effects. |
| Duplicate key and same fingerprint, completed | original status/data/error | Return the original result; no new attempt, object, materialization, or charge. |
| Duplicate key and same fingerprint, in progress | `degraded` / `IDEMPOTENCY_OPERATION_IN_PROGRESS` | Return the original operation reference; do not start work again. |
| Duplicate key and different fingerprint | `error` / `IDEMPOTENCY_CONFLICT` | No side effect. |
| Key is absent or invalid | `error` / `INVALID_REQUEST` | No side effect. |

The server, not the body, supplies authenticated actor identity, server-generated IDs, operation receipt IDs, contract configuration, model/provider identity, lease/fencing data, actual usage, and timestamps. Fingerprinting includes semantically supplied request fields such as `reason`; it excludes only `idempotency_key` and server-generated fields.

## 2. Start and retry semantics

### 2.1 `theme_chokepoint_start_ablation_v1`

The start request remains v1.0-compatible. On successful first-arm creation, the response additionally makes the immutable `budget_identity_hash` and `candidate_namespace_id` observable. The operation creates one execution and its plan atomically; it cannot partially create tasks without a durable execution/plan record.

`max_concurrency` is an execution parameter, not an authorization to amend a running execution. Budget ceilings, including the canonical task-budget representation, are immutable after the response. Reusing a `pair_id` can register only the missing execution mode and only with identical fairness identity. A caller seeking a new budget, replacement arm, or rerun must create a new pair/execution with a new idempotency key; this cannot mutate an existing arm.

`execution_status` uses `pending`, `running`, `succeeded`, `degraded`, `failed`, or `exhausted` with the exact reduction in System Design v1.1 §1.2. `cancelled` is not returned by WP0–WP4 implementations.

### 2.2 `theme_chokepoint_retry_task_v1`

The request body is unchanged. It succeeds only when the current task status is `failed_retryable`, the task is bound to the stated execution, the frozen retry policy permits the request, and a new safe reservation fits the immutable execution budget. It atomically creates a new `attempt_id` and dispatch record, increments fencing, and moves the task to `running`.

For `succeeded`, `failed_terminal`, or `exhausted`, return `error` / `RETRY_NOT_ALLOWED`. For a live lease or concurrent valid claim, return `error` / `TASK_LEASE_CONFLICT`. For a frozen budget cap that precludes the attempt, atomically record/return `BUDGET_EXHAUSTED` and do not expose a successful retry. Clients never submit a lease owner, expiry, token, cost, or status.

## 3. Read DTO minimum fields

`theme_chokepoint_get_execution_v1` must expose enough durable data to distinguish recovery states without returning sensitive payloads:

```json
{
  "execution_id": "opaque",
  "candidate_namespace_id": "same-as-execution-id",
  "execution_status": "running",
  "budget": {
    "configured_identity_hash": "<sha256>",
    "reserved": {"cost_usd_micros": 0},
    "settled": {"cost_usd_micros": 0},
    "unreconciled": {"cost_usd_micros": 0}
  },
  "task_counts": {"pending": 0, "running": 0, "succeeded": 0, "failed_retryable": 0, "failed_terminal": 0, "exhausted": 0},
  "candidate_result": {"status": "absent|valid|invalid", "candidate_result_sha256": null},
  "tasks": []
}
```

Each returned task summary includes `task_key`, `task_id`, stable planner position, state, current attempt summary, terminal reason code (when terminal), accepted artifact-set hash (when present), and nullable terminal outcome. It must not return raw prompt content, credentials, raw provider bytes, local paths, or a fencing token usable to mutate state. Pagination order is the frozen planner position, then `task_key`; completion order is never an API order.

The candidate result reference denotes an execution-scoped result only. It cannot be presented as `repository.get_stage3_result(base_run_id)`, a canonical Stage 3 result, a production status advance, or promotion eligibility.

## 4. Pair report contract

`theme_chokepoint_get_pair_report_v1` evaluates pair state in this order: invalid identity/plan/contract; missing/nonterminal arm; failed/exhausted arm; then both valid results. It returns `validity=invalid`, `error.code=PAIR_INVALID`, `quality_conclusion=insufficient_evidence`, and `deployment_recommendation=none` for the first condition. For either incomplete condition it returns `status=degraded`, `pair_status=incomplete`, no winner conclusion, and both arm statuses/reasons. Only `pair_status=completed` permits the already-defined evaluation-policy conclusions.

The report includes `candidate_result_sha256` for each valid candidate and compares both hash values only as an observation; equal or unequal hashes do not bypass quality evaluation. It also includes a stable task diff keyed by `(task_key, execution_id)` and does not conflate one arm's task ID with the other's.

## 5. Stable error additions and compatibility

The following stable codes are added to the v1.0 error registry: `IDEMPOTENCY_OPERATION_IN_PROGRESS`, `CANDIDATE_RESULT_CONFLICT`, `CANDIDATE_ARTIFACT_CONFLICT`, `LEASE_EXPIRED`, `ATTEMPT_RESPONSE_NOT_DURABLE`, and `MIGRATION_REQUIRED`. Existing codes retain their meanings. A rejected stale completion is reported as `LEASE_FENCED`; its orphan audit record is not a successful task receipt.

Legacy unversioned surfaces retain their response shape. New DTO fields are additive only. An implementation that cannot preserve these v1.1 semantics must keep the ablation tools unavailable rather than silently falling back to v1.0 ambiguity.
