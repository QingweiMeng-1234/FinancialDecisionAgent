# Theme Chokepoint Agent Execution Ablation System Design v1.1 — Superseding Addendum

## Document control and precedence

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-draft` |
| Predecessor | `docs/architecture/theme-chokepoint-agent-execution-ablation-system-design.md` |
| Predecessor SHA-256 | `a7eefe7052040562ec21ed52b717b69401b9cbe469548b1c042578cad1a54510` |
| Controls | v1.0 sections 7–16 and 20 where this addendum is more specific |
| Related | API v1.1 addendum and Migration Contract v1.1 |

This new path supersedes only the listed semantic clauses. It preserves the v1.0 artifact byte-for-byte and is not a freeze or implementation claim.

## 1. Normative lifecycle

### 1.1 Task state machine

Public task states are exactly `pending`, `running`, `succeeded`, `failed_retryable`, `failed_terminal`, and `exhausted`; lease ownership is not a public state.

```text
pending --claim--> running --accepted completion--> succeeded
                         |--retryable close-------> failed_retryable --new claim--> running
                         |--terminal close--------> failed_terminal
                         `--budget close----------> exhausted
```

Only a frozen retry policy or the versioned retry operation may move `failed_retryable` to `running`. A task may enter `exhausted` only because the execution's immutable shared or task cap precludes another safe attempt. `succeeded`, `failed_terminal`, and `exhausted` have no outgoing transition. A new budget always means a new execution, not a transition out of `exhausted`.

### 1.2 Execution and pair state reduction

An execution begins `pending`, becomes `running` on its first claim, and remains running while any task is pending, running, or retryable. After all tasks are terminal, reduce outcomes using the ordered rules in PRD v1.1 §1.2: `failed` > `exhausted` > `degraded` > `succeeded`. Persist the winning status plus all task-status counts and terminal reason codes. Never infer success from a nonempty candidate namespace.

The pair record keeps registered arms and immutable fairness identity. Its report state is:

| Condition, evaluated in order | Pair status | Winner conclusion |
| --- | --- | --- |
| A fairness/plan/contract mismatch is detected | `invalid` | prohibited |
| Either required arm/result is absent or nonterminal | `incomplete` | prohibited |
| Both arms terminal but either is `failed` or `exhausted` | `incomplete` | prohibited |
| Both arms have valid `succeeded`/`degraded` results and identities match | `completed` | evaluation policy may assess |

`created` and `running` are operational progress values before the above report reduction. `invalid` does not delete or cancel an arm. Cancellation is not an implementation option in WP0–WP4.

## 2. Canonical identity and hash framing

All SHA-256 values in this section are lower-case hexadecimal hashes of UTF-8 bytes. There is no delimiter-concatenation hash format.

`task_key` is `SHA-256(CJ1(task_semantic_payload))`, where `CJ1` is canonical JSON with: UTF-8; Unicode NFC strings; object keys sorted by Unicode code point; arrays retained in stated order; no insignificant whitespace; JSON string escaping; booleans/null in standard JSON; integral quantities represented as JSON integers; and decimal money represented as integer micro-USD. Inputs that cannot be represented uniquely under these rules fail validation rather than being silently normalized.

The payload has exactly these top-level keys:

```json
{
  "identity_version": "financial-agent.theme-chokepoint.task-key.v1",
  "task_kind": "evidence|blind_counter",
  "input_snapshot_sha256": "...",
  "iteration": 1,
  "node_id": "...",
  "material_field_or_route": "...",
  "scope": {"as_of_date": "YYYY-MM-DD", "horizon_months": 12, "region": "..."},
  "sealed_query": "...",
  "source_policy": {"id": "...", "sha256": "..."},
  "task_budget": {"max_cost_usd_micros": 500000, "max_input_tokens": 0, "max_output_tokens": 0, "max_queries": 1, "max_sources": 5, "max_time_seconds": 60},
  "contracts": [{"id": "...", "role": "...", "sha256": "...", "version": "..."}]
}
```

`contracts` is ordered ascending by `role`, then `id`. A zero token cap means the capability is not enforceable and is part of the identity; an omitted field is invalid. The sealed query is the exact planner output after canonical text validation. `base_run_id`, `pair_id`, `execution_id`, execution mode, worker/attempt IDs, reservations, actual usage, completion time, provider billing IDs, and storage IDs are forbidden from this payload.

`task_id` is `SHA-256(CJ1({"execution_id": execution_id, "identity_version": "financial-agent.theme-chokepoint.task-id.v1", "task_key": task_key}))`. `execution_id`, `pair_id`, `attempt_id`, and `receipt_id` are server-generated opaque IDs; their textual UUID/ULID choice is Developer discretion. Recreating a logical task inside one execution is rejected before dispatch.

## 3. Idempotency, leases, and fencing

Every mutating operation first durably reserves an idempotency record. Its logical key is `(authenticated actor subject, API version, operation, resource scope, idempotency_key)`. Resource scope is `(base_run_id, pair_id-or-empty)` for start, `(execution_id, task_id)` for retry, and the operation's target tuple for any future promotion/cancel. The stored request fingerprint is `SHA-256(CJ1(request after removing idempotency_key and server-generated fields))`.

Same logical key plus equal fingerprint returns the original object/receipt, including while concurrent callers race. Same logical key plus a different fingerprint returns `IDEMPOTENCY_CONFLICT` and performs no task claim, provider call, or additional charge. A crash after reservation is recovered by completing the single reserved operation or recording one stable failure; it never creates a second logical operation under that key.

A lease is scoped to one `(execution_id, task_id, attempt_id)` and contains an opaque owner plus a monotonically increasing fencing token for that task. Claim atomically verifies dispatchable task state, idempotency, and available reservation; creates the attempt and lease; increments the fencing token; records a dispatch receipt; and changes the task to `running`. Heartbeat is a CAS on task, attempt, owner, token, and unexpired lease; it may only extend the current lease. Completion is a CAS on task, attempt, token, and unexpired ownership. An expired or superseded token may write an orphan-audit receipt but cannot settle budget, materialize candidate data, or alter task status.

Expiry recovery atomically closes the lease, classifies the attempt as expired, and either moves the task to `failed_retryable` or `exhausted` according to frozen retry/budget policy. It never lets an old owner reclaim itself. Clock source, lease duration, worker transport, and physical counter representation are Developer discretion provided this CAS behavior holds.

## 4. Durable atomic boundaries and crash recovery

The logical boundaries below must be one local transaction (or demonstrably equivalent all-or-nothing commit); external provider calls are outside them.

| Boundary | Atomic durable effect | Recovery rule |
| --- | --- | --- |
| Dispatch | attempt registry + lease/fence + budget reservation + dispatch receipt + task `running` | expired lease follows §3; no in-memory dispatch is evidence of completion |
| Provider intake | request identity, raw-response hash/bytes or an explicit `response_not_durable` marker, and parsed/reconciliation lineage | if a provider may have been called but no durable response exists, retain/settle the conservative reservation, close the attempt as uncertain, and require a new attempt for any re-call |
| Accepted completion | active-fence CAS + accepted candidate artifact set + lineage references + immutable terminal receipt + actual budget settlement/release + terminal task state | on restart, either all are visible or none; retry only after the task's durable state permits it |
| Candidate finalization | deterministic read of terminal artifact sets + validation result + candidate semantic projection/hash + execution reduction | regenerate from artifacts if absent; if an existing projection/hash differs, fail the execution with `IDEMPOTENCY_CONFLICT` |

The accepted artifact set contains the task's candidate claims, evidence cards, source references, counter result where applicable, and their reconciliation references. A receipt is append-only; a started/expired attempt is represented by a distinct lifecycle/dispatch record, not by mutating an accepted terminal receipt. Reservations are never silently released after an uncertain provider call.

Recovery reads only durable plans, leases, attempts, receipts, reservations, and artifacts. It does not reconstruct success from logs, worker memory, completion order, or provider traces not linked to a durable attempt.

## 5. Candidate namespace, provider lineage, and result hash

Every ablation write is bound to `candidate_namespace_id = execution_id`. Candidate Claim, EvidenceCard, SourceSnapshot usage, counter result, assessment, artifact set, and candidate result must carry this namespace and `task_id`; they cannot be queried as canonical facts merely by matching an object ID. Existing global source identity/version records may be referenced read-only, but candidate source usage and every new provider request/raw/parsed/reconciliation record must be correlated to `execution_id`, `task_id`, and `attempt_id` through fields or an equivalent durable relation. No v1.1 candidate write may insert into the legacy canonical Stage 3 result slot or mutate a canonical source usage.

`candidate_result_sha256` is `SHA-256(CJ1(stage3_candidate_projection_v1))`. The projection includes exactly the versioned Stage 3 semantic contract: executable-contract ID/hash and domain contract version; result status; iterations completed; sorted stable incomplete reason codes; semantic claims; semantic evidence cards and quote/source-version identity; source snapshots; reconciled counter-route outcomes; and assessments keyed by segment. It includes every contract-visible domain field within those objects. Object IDs and references are replaced by deterministic semantic keys/source identity and version hashes; collections sort by those keys (assessments by segment ID, counter outcomes by route).

The projection excludes `base_run_id`, `pair_id`, `execution_id`, candidate namespace, task/attempt/receipt/provider request IDs, worker/model session IDs, leases/fences, timestamps, queue order, retry counts, transport traces, raw storage locations, token/cost/time usage, feature flags, and all other runtime telemetry. Changing only an excluded value must not change the hash; changing a contract-visible semantic value must. A candidate result is not promotable merely because its projection exists.

## 6. Explicit discretion boundary

The logical records above may be implemented as one or many physical tables; with equivalent SQL types; with additional non-unique performance indexes; or through repository projections, a queue, or a framework adapter. Class/file placement, transaction helper shape, and opaque ID format are also open. These choices must not change canonical bytes, scoped uniqueness, CAS checks, atomic visibility, retention, error codes, or stable merge order.
