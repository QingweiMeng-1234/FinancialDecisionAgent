# Theme Chokepoint Agent Execution Ablation System Design

## Document Control

| Field | Value |
| --- | --- |
| Status | `IMPLEMENTATION_DRAFT_ALIGNED_TO_FROZEN_PRD` |
| Version | 1.0 |
| Date | 2026-09-04 |
| Product | Financial Agent / Theme Chokepoint Research |
| Scope | Stage 3 single-agent versus isolated-worker execution ablation |
| Product contract | [Theme Chokepoint Agent Execution Ablation PRD](../product/theme-chokepoint-agent-execution-ablation-prd.md) |
| API contract | [Theme Chokepoint Agent Execution Ablation API](../api/theme-chokepoint-agent-execution-ablation-api.md) |
| Freeze manifest | [Theme Chokepoint Agent Execution Ablation Freeze Manifest](../product/theme-chokepoint-agent-execution-ablation-freeze-manifest-v1.0.json) |
| Framework decision | Deferred; Mastra, LangGraph, and a lightweight executor are all adapters |

## 1. Purpose and Proof Boundary

This design specifies a framework-independent execution layer for the existing
Theme Chokepoint workflow. It makes the Stage 3 evidence loop executable in two
topologies: one shared sequential reasoning session (`single`) and bounded
isolated workers (`isolated_workers`). Both topologies implement the same
research protocol, evidence contract, scoring contract, persistence rules, and
final `Stage3Result` contract.

The design tests whether isolated workers provide measurable system benefits in
parallelism, context isolation, blind review, and failure isolation. It does
not assume or claim that more agents produce better research. Research quality
must be measured separately on frozen fixtures, golden sets, and holdouts.

This document is a design baseline, not evidence that the new executor has been
implemented, that live providers are available, or that multi-agent execution
has improved investment research quality.

## 2. Scope

### 2.1 In scope

- a stable Stage 3 task planner for `node × missing_material_field`;
- sequential and bounded isolated execution strategies;
- task leases, attempts, receipts, budgets, retries, and recovery;
- blind counter-evidence tasks using demand, supply, and alternatives routes;
- common schema, source lineage, evidence materialization, and deterministic scoring;
- paired single/multi executions and machine-readable comparison reports;
- adapters for future Mastra, LangGraph, or a local worker pool;
- metrics and testable invariants for concurrency and context boundaries.

### 2.2 Out of scope

- changing Stage 1 framing, Stage 2 graph semantics, Stage 3 scoring dimensions,
  or Stage 4 company scoring;
- making Stage 5–7 agent-driven;
- public REST deployment or a framework-specific runtime topology;
- agent voting or probabilistic aggregation of ordinal scores;
- expanding live-provider budgets or adding new data-source permissions;
- claiming that isolated workers improve factual accuracy without evaluation.

## 3. Current Repository Baseline

The existing implementation is a staged, persisted workflow rather than a
general agent graph:

| Existing component | Current responsibility | Design treatment |
| --- | --- | --- |
| `stage1.py` / `AssistedThemeFramingService` | Demand frame and product anchors | unchanged; one logical agent is sufficient |
| `stage2.py` / `SupplyChainGraphService` | Bounded upstream graph expansion | unchanged; graph merge remains deterministic |
| `stage3.py` / `EvidenceChokepointLoop` | Gap-driven acquisition, materialization, scoring, relief, critic | add planner and replaceable executor seam |
| `stage4.py` / `CompanyExposureRedTeamService` | Scoped company evidence, challenger sets, gates | unchanged in work package 1; independent verifier is later |
| `stage5.py` / `PersistentResearchProductService` | Immutable artifact set, manifest, feedback, resume | deterministic code; no worker fan-out |
| `stage6.py` / monitoring services | Trigger/refresh and semantic evaluation | deterministic scheduler plus optional evaluator |
| `stage7.py` / `SignalExportService` | CSV/Parquet/definitions/lineage export | deterministic code; no agent authority |
| `orchestrator.py` / `RootStageOrchestrator` | Stage status transitions and durable receipts | remains the run-level authority |
| `repository.py` / `ThemeChokepointRepository` | durable domain and provider records | add task ledger methods/tables without leaking DB identity |

The current Stage 3 loop executes each missing pair in order and then calls
`_materialize`, `_assess`, relief, and `_attach_critic`. The existing critic is
not blind because it receives `ordinal_draft`. The new blind counter protocol
must therefore be an explicit execution boundary rather than a prompt-only
instruction.

Today `EvidenceChokepointLoop.run()` calls
`ThemeChokepointRepository.save_stage3_result()` directly, and that repository
method requires `SUPPLY_CHAIN_GRAPH_READY`, inserts the canonical result, and
advances `RunStatus`. The candidate namespace and promotion service described
below do not yet exist. The ablation path must add them as a new repository
seam; it must not reuse the current direct canonical write for either arm.

Repository evidence verified on 2026-09-04:

| Evidence | Current location |
| --- | --- |
| Frozen root statuses | `src/event_collector/theme_chokepoint/contracts.py:14-26` |
| Sequential missing-pair loop and direct final save | `src/event_collector/theme_chokepoint/stage3.py:237-394` |
| Canonical Stage 3 state check, insert, and run-status update | `src/event_collector/theme_chokepoint/repository.py:4319-4391` |
| Legacy handler map and FastMCP registration helper | `src/event_collector/theme_chokepoint/interfaces.py:50-62` |

## 4. Architectural Drivers

### 4.1 Protocol equivalence

Execution mode may change scheduling, context shape, and failure boundaries,
but not scope, source policy, schema, scoring, state semantics, or downstream
contracts.

### 4.2 Deterministic authority

Agents may propose evidence-bound facts. They may not authoritatively create
ordinal score/state, alter graph identity, bypass evidence validation, or mark a
run complete. Python validators, repository transactions, and the frozen
scoring contract remain authoritative.

### 4.3 Recoverability

Every task attempt has a durable identity and terminal result. A worker failure
must preserve unrelated successful work and permit targeted retry. Missing
durable receipts fail closed during recovery.

### 4.4 Fair comparison

Single and isolated runs are paired by hashes of scope, graph, source corpus,
model/prompt/schema, scoring/governance contracts, and budgets. Any mismatch
invalidates the comparison rather than being reported as a mode advantage.

## 5. High-Level Architecture

```text
RootStageOrchestrator
        |
        v
Stage3ExecutionCoordinator
        |
        +--> Stage3TaskPlanner --> stable EvidenceTask[]
        |                              |
        |                +-------------+-------------+
        |                |                           |
        |       SequentialExecutor          IsolatedWorkerExecutor
        |       one shared session          bounded fresh contexts
        |                |                           |
        +----------------+-------------+-------------+
                                     |
                                     v
                           post-model validation
                                     |
                     execution-scoped candidate materializer
                                     |
                           blind counter planner
                                     |
                    sequential or isolated counter workers
                                     |
                           reconciliation and gates
                                     |
                           deterministic scorer
                                     |
                    candidate Stage3Result + receipts
                                     |
                          explicit promotion gate
                                     |
                     canonical Stage3Result (at most one)
```

Provider calls, model calls, and file/network work occur outside short
repository transactions. The coordinator owns task planning, global budgets,
leases, completion collection, stable merge order, and candidate finalization.
Only an explicitly authorized promotion path may write canonical Stage 3 domain
state and advance the base run.

## 6. Component Responsibilities

| Component | Owns | Must not own |
| --- | --- | --- |
| `Stage3TaskPlanner` | deterministic task keys, task packets, plan hash | execution or attempt identity, model output, score |
| `Stage3ExecutionCoordinator` | mode selection, budgets, leases, retries, merge, recovery | semantic claims or scoring rules |
| `SequentialExecutor` | one ordered session implementing the task protocol | bypassing per-task receipts |
| `IsolatedWorkerExecutor` | bounded fan-out and fresh worker contexts | changing task set or source policy |
| `EvidenceWorker` | evidence acquisition and evidence-bound fact proposals | final score/state |
| `BlindCounterWorker` | demand/supply/alternatives counter-search | reading primary conclusion or changing score |
| `EvidenceValidator` | schema, source, quote, ID and date validation | inventing missing evidence |
| `CandidateMaterializer` | idempotent execution-scoped Claim/EvidenceCard/SourceSnapshot projections | accepting invalid output or advancing `RunStatus` |
| `DeterministicScorer` | versioned dimensions, gates, ordinal state | LLM-selected score |
| `TaskLedgerRepository` | task/attempt/lease/receipt persistence | provider/network calls |
| `PairComparator` | fairness checks and metrics report | declaring quality winner without metrics |
| `Stage3PromotionService` | explicit validated candidate-to-canonical commit and run transition | choosing a winner or auto-promoting an ablation arm |

## 7. Run and Stage State Model

The root state machine remains the one in `contracts.py` and
`RootStageOrchestrator`:

```text
REQUEST_STORED
  -> NEEDS_CLARIFICATION
  -> AWAITING_PRODUCT_CONFIRMATION
  -> READY_FOR_SUPPLY_CHAIN
  -> SUPPLY_CHAIN_GRAPH_READY
  -> CHOKEPOINT_ASSESSMENT_READY
  -> COMPANY_ASSESSMENT_READY
  -> PERSISTENT_RESEARCH_READY
  -> MONITORING_READY
  -> SIGNAL_EXPORT_READY
```

Permitted incomplete branches are:

```text
Stage 3 -> INCOMPLETE_BUDGET_EXHAUSTED
Stage 4 -> COMPANY_ASSESSMENT_INCOMPLETE
```

The executor adds task-level states without replacing `RunStatus`:

```text
PENDING --------------------> RUNNING -> SUCCEEDED
                                  |       \
                                  |        -> FAILED_RETRYABLE -> RUNNING
                                  |        -> FAILED_TERMINAL
                                  |        -> EXHAUSTED
```

The API and persistence serialization uses the lowercase values `pending`,
`running`, `succeeded`, `failed_retryable`, `failed_terminal`, and `exhausted`;
uppercase labels in the diagram are explanatory only.

Claim is an atomic `PENDING` or policy-permitted `FAILED_RETRYABLE` to `RUNNING`
transition that also records lease owner, expiry, and fencing metadata; there is
no public `CLAIMED` task state. Lease expiry makes a task claimable only if its
prior attempt has no durable terminal success. A late fenced response is an
orphaned attempt and cannot change the task. An execution candidate may be
incomplete when required tasks are exhausted, while successful task artifacts
remain readable and reusable. Only promotion maps that candidate outcome to the
base run's `RunStatus`.

## 8. Task and Receipt Contracts

### 8.1 EvidenceTask

An evidence task is one frozen Stage 3 snapshot × iteration × node ×
material-field semantic unit. Identity is deliberately split into a
mode-independent key and an execution-specific ID:

```text
task_key = SHA-256(canonical semantic task payload)
task_id  = SHA-256(execution_id + task_key)
```

The `task_key` payload includes the frozen input snapshot, iteration, node,
field/route, sealed query, source policy, task budget, and applicable contract
versions. It excludes `base_run_id`, `pair_id`, `execution_id`, execution mode,
worker identity, and attempt identity. Paired executions must therefore have
identical ordered `task_key` sets while retaining distinct `task_id` values.
Changing a semantic field changes `task_key`; changing only the execution
changes `task_id`.

The budget embedded in `task_key` is the canonical tuple of configured caps,
not runtime reservations, actual usage, completion time, or provider billing.

```json
{
  "task_key": "mode-independent-sha256",
  "task_id": "execution-specific-sha256",
  "pair_id": "pair-id",
  "base_run_id": "run-stopped-at-stage2",
  "execution_id": "execution-id",
  "iteration": 1,
  "task_kind": "evidence",
  "node_id": "segment-id",
  "material_field": "qualification_barrier",
  "scope": {"region": "global", "as_of_date": "2026-09-04", "horizon_months": 12},
  "query": "sealed deterministic query",
  "source_policy_id": "policy-v1",
  "input_snapshot_sha256": "sha256",
  "budget": {"max_queries": 1, "max_sources": 5, "max_time_seconds": 60, "max_cost_usd": 0.50}
}
```

### 8.2 BlindCounterTask

The task contains `base_run_id`, `execution_id`, `task_key`, `task_id`, optional
`pair_id`, the node, scope, date, source policy, objective counter questions,
and the fixed routes `demand`, `supply`, and `alternatives`. It must not contain
`ordinal_draft`, candidate state, primary conclusion, score bounds, researcher
confidence, or main-research summary. A source identity list may be passed only
to prevent duplicate fetches.

### 8.3 AgentExecutionReceipt

Receipts are append-only facts about attempts:

```text
receipt_id, pair_id, base_run_id, execution_id
task_key, task_id, attempt_id
execution_mode, worker_role, worker_id
model_id, prompt_version, schema_version
input_snapshot_sha256, context_tokens, context_payload_sha256
started_at, finished_at, provider_receipt_ids, output_artifact_ids
status, error_code, input_tokens, output_tokens, cost_usd
```

Natural-language errors are supplementary only; retries and reports use stable
machine-readable error codes.

`worker_id` identifies the session/worker that actually performs the attempt.
A single execution may reuse one worker ID across tasks; isolated execution
uses identities whose context separation can be demonstrated. `worker_id` is
neither the arm-level `execution_id` nor retry-level `attempt_id`. `pair_id` is
required throughout a paired execution and may be null only for an explicitly
standalone deterministic fixture execution.

Counter receipts also record `packet_blind` and `context_blind`. Both modes must
receive a forbidden-field-clean counter packet. The single arm reuses its
primary session and therefore records `packet_blind=true,
context_blind=false`; the isolated arm must use a different worker ID and fresh
context without the primary transcript, and records both values as true.

Identity cardinality is:

```text
one pair_id
  -> one base_run_id + one frozen Stage-2 snapshot
  -> one single execution_id + one isolated-workers execution_id
each execution_id
  -> one task_id per shared task_key
each task_id
  -> one or more attempt_id values
each attempt
  -> one immutable receipt_id
```

## 9. Execution Modes

### 9.1 Single

The coordinator generates the same task plan as multi mode and presents tasks
to one ordered session. Each task still gets a separate receipt, validation,
materialization boundary, and budget accounting. Counter-search remains a
required protocol step, not an optional self-review. Its packet is blind, but
the retained session history means this arm is not context-blind.

### 9.2 Isolated workers

The coordinator gives each worker a minimal explicit TaskPacket and a fresh
context. Evidence tasks may run concurrently up to `max_concurrency`. Counter
workers run after the evidence boundary and are blind to primary score/state.
Results are collected by execution-specific task ID and merged by the planner's
canonical `task_key` order, never by completion order.

Worker isolation is meaningful only when the worker cannot access another task's
conversation history or hidden coordinator state. The source policy and scope
remain identical to single mode, so context reduction cannot become an
unauthorized evidence advantage.

### 9.3 Plan profiles

Phase A/B comparisons use `strict_replay`: the coordinator seals the complete
ordered task-key plan before either arm runs, and both arms replay it without
adding or deleting tasks based on their own intermediate outputs. This is the
proof-eligible topology ablation profile.

A production-like dynamic shadow may plan later iterations from each arm's
candidate state. If the plans diverge, the pair remains observable but its
topology-only comparison is `PAIR_INVALID` with reason `TASK_PLAN_DIVERGED`.
Planner divergence cannot be counted as an agent advantage. Runtime budget
reservations and actual usage never affect task keys in either profile.

## 10. Hooks and Deterministic Gates

Hooks are lifecycle controls, not extra reasoning agents. Both modes execute
the same hooks.

| Hook/gate | Required checks |
| --- | --- |
| Pre-run | status is `SUPPLY_CHAIN_GRAPH_READY`; contract ID/hash and pair manifest match |
| Pre-task | stable task identity, lease ownership, remaining global/task budget, minimal packet |
| Blind pre-task | forbidden-field scan over serialized packet and metadata |
| Post-provider | request/raw bytes/trace/hash persistence, transport/date/cost checks |
| Post-model | schema, allowed evidence IDs, exact quote/source offsets/hash, fact/inference type; reject model rating/state |
| Materialization | idempotent Claim/EvidenceCard/SourceSnapshot write and lineage reconciliation |
| Post-task | terminal receipt, metrics, output IDs, retry classification |
| Pre-score | all planned tasks terminal; successful outputs materialized; counter routes reconciled |
| Recovery | durable receipts present; retry only permitted task states; fail closed on missing receipt |
| Pair validity | scope/graph/source/model/prompt/schema/scoring/governance/plan/budget identities equal |

The blind pre-task gate proves only `packet_blind`. `context_blind` additionally
requires a new worker ID/context identity and evidence that no primary transcript
or hidden coordinator conclusion entered the model request.

## 11. Persistence Model

The existing repository remains the domain authority. A task ledger may be
implemented as new SQLite tables or an equivalent repository projection, but
callers receive domain contracts rather than row IDs or DB connections.

Required logical tables/records:

| Record | Key | Important fields |
| --- | --- | --- |
| `stage3_execution` | `execution_id` | base run, mode, frozen input/plan hashes, budget, candidate status |
| `stage3_task_plan` | `(execution_id, iteration, plan_hash)` | ordered task keys and IDs, graph/scope hashes, mode-independent budgets |
| `stage3_task` | `task_id` | execution ID, task key, kind, node/field/route, input hash, public status, output IDs |
| `stage3_task_attempt` | `attempt_id` | task ID, worker identity, lease/fencing, timing, token/cost, error, orphaned flag |
| `stage3_task_lease` | `task_id` | owner, fencing token, expiry, heartbeat |
| `stage3_execution_receipt` | `receipt_id` | immutable attempt summary and artifact IDs |
| `stage3_candidate_artifact` | `(execution_id, artifact_id)` | typed candidate domain record and lineage; never canonical by implication |
| `stage3_candidate_result` | `execution_id` | contract-compatible candidate `Stage3Result`, validation and result hash |
| `stage3_pair_manifest` | `pair_id` | base run, both execution IDs, frozen snapshot and fairness hashes |
| `stage3_pair_report` | `pair_id` | validity, metrics, differences, limitations |
| `stage3_promotion_receipt` | `promotion_id` | authorized execution, candidate hash, canonical revision, actor/policy, status |

Existing provider request/raw/parsed/reconciliation records remain the source
lineage for external calls and must be correlated by `execution_id` and
`task_id`. In ablation and shadow modes, task-level writes and validated domain
objects remain in the execution candidate namespace. Existing
`save_stage3_result()` is invoked only by an explicitly authorized promotion,
or by a normal production execution designated canonical before it starts.
Neither evaluator arm can infer canonical authority from its mode or quality
score.

## 12. Transactions, Idempotency, and Fencing

- Task claims use one short transaction to move `pending` or policy-permitted
  `failed_retryable` directly to `running` and assign an owner plus monotonically
  increasing fencing token. A stale owner cannot commit a completion after a
  newer claim.
- Provider/model/network calls happen outside transactions.
- Completion uses compare-and-set on `(task_id, attempt_id, fencing_token)`.
- A successful completion with the same `execution_id`, `task_key`, and input
  hash is a no-op,
  returning the existing receipt and artifact IDs.
- Conflicting output for the same immutable task identity is rejected and
  recorded as `IDEMPOTENCY_CONFLICT`; it is never silently overwritten.
- Candidate materialization deduplicates within an execution by canonical fact
  key, article/source identity, and evidence ID, consistent with current
  `_candidate_fact_key` behavior. Promotion re-validates cross-run conflicts.
- Merge order is canonical plan `task_key` order. Completion timing does not
  alter candidate `Stage3Result` serialization or scoring inputs.
- A late response from an expired lease is persisted as an orphan attempt for
  audit but cannot alter task state.

## 13. Budget and Concurrency Accounting

The coordinator owns a global budget derived from `ResearchRequest`:
`max_sources`, `max_iterations`, `max_time_seconds`, and `max_cost_usd`.
Workers receive a task budget but cannot reserve the full global budget each.
Reservations are atomically accounted for before dispatch; actual usage is
settled on completion. Unused reservations may be released by the coordinator.

The server enforces `1 <= max_concurrency <= configured_cap`. Peak active
workers, queue wait, cumulative worker time, provider calls, tokens, and cost
are recorded. `max_concurrency=1` is a valid isolated-worker configuration and
is useful to separate context isolation from parallelism.

## 14. Blind Counter-Evidence Protocol

For every eligible segment, the counter protocol executes fixed routes:

```text
demand       slowdown, inventory, cancellation, order reduction
supply       excess capacity, lead-time normalization, capacity expansion
alternatives qualified substitute, alternative process, replacement failure
```

Each route records request, raw response, parsed result, materialized evidence,
and reconciliation receipt using the existing counter-search repository seam.
The result is one of `found`, `explicit_negative`, or `unknown`; insufficient
coverage cannot be converted to a negative finding. A blind worker may add
counter evidence or coverage status, but only the deterministic scorer can
change ordinal state.

## 15. Failure and Recovery

Stable error classes include `PROVIDER_TIMEOUT`, `PROVIDER_TRANSPORT_ERROR`,
`MODEL_TIMEOUT`, `SCHEMA_VALIDATION_FAILED`, `EVIDENCE_ID_OUT_OF_SCOPE`,
`SOURCE_DATE_INVALID`, `BUDGET_EXHAUSTED`, `LEASE_FENCED`, and
`IDEMPOTENCY_CONFLICT`.

Retryable provider/transport/model failures create a new attempt for the same
task identity. Invalid schema or evidence binding is retryable only under an
explicit policy; otherwise it is terminal. Budget exhaustion is terminal for
the current execution but does not erase completed tasks or alter the base run.

On process restart, the coordinator reads the durable plan and receipts,
reconciles leases, and resumes only pending/retryable tasks whose input hash is
unchanged. It does not infer completion from logs or in-memory futures. Missing
stage receipts with a durable advanced `RunStatus` cause the existing root
orchestrator to fail closed.

## 16. Stage 3 Completion Semantics

After evidence and counter tasks reach terminal states, the coordinator:

1. validates and materializes accepted outputs into the `execution_id`
   candidate namespace;
2. recomputes segment drafts through the existing scorer;
3. attaches relief and counter results under existing contracts;
4. validates scoring ownership and evidence boundaries;
5. finalizes assessments and unresolved/incomplete reasons;
6. persists a contract-compatible candidate `Stage3Result` and its hash without
   changing the base run's canonical Stage 3 state.

Workers and providers can submit only candidate outputs and lineage receipts;
they cannot write canonical Claim, EvidenceCard, SourceSnapshot, counter,
assessment, or `Stage3Result` records. A separate promotion service may call
`save_stage3_result()` only after authorization, candidate hash verification,
full validation, and a compare-and-set that proves the base run has no
conflicting Stage 3 revision. The commit order is:

```text
attempt receipt and provider lineage
  -> candidate validation/materialization
  -> deterministic candidate score/result
  -> explicit promotion receipt
  -> canonical Stage3Result
  -> stage receipt and RunStatus transition
```

An isolated worker count, worker prompt history, or execution receipt must not
appear in the meaning of a segment score. Those details belong in execution
metadata and comparison reports.

## 17. Paired Evaluation

A pair consists of one `base_run_id` stopped at a frozen Stage 2 snapshot and
two isolated evaluation executions under one `pair_id`: one `single` and one
`isolated_workers`. The pair manifest records `single_execution_id` and
`isolated_execution_id`. The pair gate compares hashes for scope, date/region,
graph, source snapshot, model family/version, prompt family/version, schema,
scoring/governance contracts, plan profile, ordered task-key set, and all
configured budget caps. Any mismatch yields `PAIR_INVALID`.

Both arms persist candidate results under their own `execution_id`; running or
comparing a pair never mutates canonical Stage 3 state. Promotion is a separate,
explicitly authorized operation and is not evidence that an arm "won."

Phase A/B require a pre-sealed `strict_replay` plan. A dynamic shadow that
produces different later-iteration task-key sets is retained for diagnosis but
is invalid for topology-only causal claims (`TASK_PLAN_DIVERGED`).

The report includes:

- task-key equality and per-task terminal-state differences;
- wall-clock time, cumulative worker time, peak concurrency, queue delay;
- provider calls, input/output tokens, cost, retries, and failure recovery;
- context payload size/hash distributions, packet/context blindness, and violations;
- evidence/card/claim counts, source levels, duplicates, unresolved fields;
- counter-route coverage and `found`/`explicit_negative`/`unknown` rates;
- validator failures and deterministic-score differences;
- golden/holdout quality metrics when available;
- explicit proof limitations and no-winner status when quality is inconclusive.

The first experiment should use deterministic mock latency and injected failure
fixtures to prove execution properties. Frozen source fixtures and the same
model/provider responses should be used when isolating topology effects.

## 18. Security and Data Boundaries

- Workers receive only the scope and source policy needed for their task.
- Blind workers never receive primary conclusions or scores.
- Source URLs, quotes, and provider responses are data; they are not executable
  instructions.
- Provider credentials are held by the provider boundary, not task payloads.
- Default logs contain hashes, IDs, counts, and error codes, not full evidence
  text or raw prompts; debug persistence is explicit and retention-controlled.
- Worker identity is recorded. A future Stage 4 verifier must not use the same
  `worker_id` as the assertion producer.

## 19. Observability

Every task and execution should expose structured fields for correlation ID,
pair/base-run/execution IDs, mode, worker ID/role, task key/ID, contract hashes,
lease/fencing state,
queue/active/completion timestamps, provider receipt IDs, token/cost usage,
terminal status, retry count, and artifact IDs.

Operational metrics include active/queued/expired leases, peak concurrency,
task latency percentiles, retry and terminal failure rates, budget utilization,
materialization conflicts, blind-field violations, pair invalidation rate, and
quality metrics from frozen evaluation sets.

## 20. Migration and Rollout

1. Freeze this framework-independent task-key, execution, receipt, candidate,
   promotion, and pair contract.
2. Add execution candidate repository/ledger schema and deterministic planner
   behind tests; keep the existing direct production write path unchanged.
3. Implement `SequentialExecutor` and run it against existing Stage 3 fixtures;
   production behavior must remain the default.
4. Implement isolated executor with `max_concurrency=1`, then bounded fan-out.
5. Add blind counter packet and forbidden-field tests.
6. Run paired mock and frozen-source experiments; publish reports.
7. Keep multi mode opt-in until fairness, recovery, and quality gates pass.
8. Select Mastra, LangGraph, or a local executor only after the contracts and
   measurements demonstrate a framework need.

Rollback is a configuration change to `single` plus preservation of task,
candidate, and receipt data. A failed isolated execution cannot touch the prior
canonical Stage 3 result or existing active research artifacts.

## 21. TDD Work Packages

Each package follows `SELECT INVARIANT -> WRITE TEST -> VERIFY RED ->
IMPLEMENT -> VERIFY GREEN -> REGRESSION -> SCOPE CHECK`.

| Package | Required invariants |
| --- | --- |
| WP-01 contracts/planner | stable shared task keys and execution-specific task IDs; equal paired plans; unknown modes fail closed |
| WP-02 ledger/leases | unique task identity; append-only attempts; fencing rejects stale completion; durable terminal states |
| WP-03 sequential executor | existing Stage 3 fixtures remain equivalent; every task gets receipt and validation |
| WP-04 isolated executor | bounded concurrency; no cross-task context; stable merge order; `max_concurrency=1` valid |
| WP-05 blind counter | forbidden fields rejected; packet/context blindness distinguished; isolated worker ID differs; three routes recorded; unknown distinct from explicit negative |
| WP-06 hooks/materialization | evidence ID/quote/date/schema gates; idempotent materialization; conflicts fail closed |
| WP-07 recovery/budget | targeted retry; successful tasks not rerun; global budget enforced; crash recovery from receipts |
| WP-08 paired evaluation | isolated candidate namespaces; mismatch becomes `PAIR_INVALID`; no canonical mutation; metrics reproducible; no quality winner without evidence |
| WP-09 adapter boundary | framework adapter cannot alter domain contracts, scorer, source policy, or stage status |

## 22. Known Proof Limits

- Isolated contexts do not guarantee independent reasoning when the same model,
  prompt family, and sources are used.
- A blind packet proves input separation, not that a worker found meaningful
  counter-evidence.
- Parallel execution lowers wall-clock time only for genuinely independent
  tasks and subject to provider rate limits and global budgets.
- Task-level recovery proves system resilience, not research correctness.
- Frozen fixtures and local mock latency do not prove live-provider performance.
- The existing Stage 3 critic remains non-blind until the new counter task
  boundary is implemented and validated.
- Mastra/LangGraph selection is intentionally not justified by this design;
  selection requires measured needs for checkpointing, fan-out, recovery, or
  observability after the framework-neutral executor is proven.
