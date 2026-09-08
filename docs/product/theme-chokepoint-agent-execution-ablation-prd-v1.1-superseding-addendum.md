# Theme Chokepoint Agent Execution Ablation PRD v1.1 — Superseding Addendum

## Document control and precedence

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-draft` |
| Date | `2026-09-04` |
| Predecessor | `docs/product/theme-chokepoint-agent-execution-ablation-prd.md` |
| Predecessor SHA-256 | `e3fb44cedbabfea5d7a54ea032605d2d5df73ffda2fd15a13f1990f4cbc7aa30` |
| Companion contracts | System Design and API v1.1 addenda named below; Migration Contract v1.1 |
| Supersession rule | If this addendum conflicts with PRD v1.0 sections 10–13, 15, 17, or 21–24, this addendum controls. All other v1.0 text remains unchanged. |

This is a new, versioned product artifact. It does not alter, replace, or claim a new freeze for the v1.0 bytes. A future Frozen Bundle must bind this path and its SHA-256 together with the companion v1.1 paths; it must not bind an unversioned alias.

## 1. Product decisions needed before WP0–WP4 implementation

### 1.1 Execution is the budget and retry boundary

An `execution_id` is the sole owner of its frozen input, ordered task plan, candidate namespace, and aggregate budget. Its configured budget is immutable after creation. A task in `exhausted` is terminal **for that execution** and cannot be retried, revived, or given extra budget by an operator, recovery process, or feature-flag change.

A request for additional budget or a fresh attempt creates a new execution with a new `execution_id`, new task IDs, new budget settlement, and a new candidate namespace. It does not mutate the prior execution, receipts, or task state. The same frozen semantic plan may yield the same `task_key` in that new execution; it must yield a different `task_id`.

For a proof-eligible pair, a fresh-budget rerun is a new pair (or two new paired arms): both arms must receive identical frozen budget caps and fairness identity. A pair cannot add a replacement arm of a mode already registered. A new execution never advances the base run or replaces an earlier candidate automatically.

### 1.2 Required task, execution, and pair outcome precedence

`failed_retryable` is not terminal: an execution with any pending, running, or retryable task remains `running` until recovery or a retry policy closes every task. A task outcome is terminal only when it is `succeeded`, `failed_terminal`, or `exhausted`.

Once all planned tasks are terminal, the execution result is selected in this strict order:

1. `failed` if candidate finalization cannot produce a valid, durably reconciled candidate result, or any task is `failed_terminal`.
2. `exhausted` if rule 1 does not apply and one or more tasks are `exhausted`.
3. `degraded` if rules 1–2 do not apply and a valid candidate exists but the deterministic Stage 3 contract marks it incomplete for a non-budget protocol reason.
4. `succeeded` only if every task completed protocol work successfully and a valid candidate result exists.

`succeeded` describes completed protocol work, not a favorable research conclusion. A counter route whose supported outcome is `explicit_negative` or `unknown` can be task-`succeeded`; it is not a task failure. The execution must persist all contributing terminal reasons, so the aggregate label never hides mixed outcomes.

Pair reporting is deterministic as well. `invalid` has highest priority whenever any immutable fairness identity, plan identity/order, or required candidate-result contract check differs; invalidation does not erase running-arm evidence. Before two valid terminal arm results are available, the report is `incomplete` and may not state a winner. When both results are available, a report is `completed` only if neither arm is `failed` or `exhausted`; otherwise it remains `incomplete` with both arm outcomes shown. `cancelled` is unavailable in WP0–WP4 and must not be exposed until a separately reviewed exact mapping exists.

### 1.3 Product-level persistence and recovery promises

The following are mandatory observable semantics:

- A successful task's accepted candidate artifacts, lineage references, terminal receipt, budget settlement, and task terminal state become visible together or not at all.
- A crash cannot cause a completed artifact to be silently lost, a stale worker to overwrite a newer owner, or a provider call with uncertain persistence to be replayed under the same attempt.
- Restart resumes only durable, still-eligible work. It never treats logs, an in-memory future, or a worker self-report as success.
- Candidate data is execution-scoped. No ablation arm may write a canonical Stage 3 object, canonical `RunStatus`, signal, or trading consumer.
- Candidate results are comparable by a semantic `candidate_result_sha256`; worker, timing, telemetry, storage IDs, receipt IDs, and cost accounting cannot change that hash.

These promises apply to single and isolated-worker arms alike. They do not select a queue, database, transaction library, agent framework, or persistence topology.

## 2. Mandatory versus Developer discretion

Mandatory semantics are the state precedence, retry/budget boundary, canonical identities, idempotency and fencing behavior, atomic recovery boundaries, candidate/provider lineage isolation, semantic result hash, and migration/retention rules in the v1.1 System Design, API, and Migration Contract.

Developer discretion includes physical table splitting or co-location, equivalent SQL data types, non-unique performance indexes, queue/process arrangement, class/module/file organization, and the opaque UUID/ULID representation of server-generated IDs. Such choices are permitted only when they preserve every v1.1 externally observable identity, ordering, atomicity, retention, and error behavior.

## 3. Compatibility and non-goals

Existing canonical Stage 3 results and `RunStatus` retain their v1.0 interpretation. They are not backfilled into ablation executions, task ledgers, receipts, or candidate namespaces. A legacy run can be evaluated only by creating a new v1.1 execution from a qualifying frozen Stage 2 snapshot; no inferred historical task history is permitted.

Disabling the feature is admission control, not data deletion: it blocks new execution creation and new claims, retains all v1.1 data, and allows an already valid completion CAS to finish. It does not reclassify a task, candidate, pair, or canonical run. Promotion remains outside WP0–WP4 and no report field is an authorization to promote.

## 4. WP acceptance additions

WP0–WP4 are not ready to claim implementation complete unless tests prove at least: exhausted-task rejection and fresh-execution behavior; every precedence row above; canonical `task_key`/`task_id` fixtures; same-key idempotency under concurrent duplicates and conflict on changed payload; stale-lease fencing; each documented crash boundary; deterministic candidate hash under changed runtime metadata; candidate/provider lineage isolation; empty-backfill migration; safe feature disable; and legacy canonical-result regression.

Human approval, a new freeze, a Developer start, or a GO decision is intentionally outside this document.
