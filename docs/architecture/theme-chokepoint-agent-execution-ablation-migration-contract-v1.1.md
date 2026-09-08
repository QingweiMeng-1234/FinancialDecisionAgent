# Theme Chokepoint Agent Execution Ablation Migration Contract v1.1

## Document control

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-draft` |
| Predecessors | PRD v1.0, System Design v1.0, API v1.0 |
| Predecessor SHA-256 | `e3fb44cedbabfea5d7a54ea032605d2d5df73ffda2fd15a13f1990f4cbc7aa30`; `a7eefe7052040562ec21ed52b717b69401b9cbe469548b1c042578cad1a54510`; `f0ce51926898fef7f5a8f0f68b5289c414c3b61addd93036ccecd1f458fec32e` |
| Scope | Forward-compatible persistence migration only; no product-code or migration implementation is supplied by this document. |

## 1. Required logical records

Before ablation admission can be enabled, the datastore must durably represent an execution, immutable plan, task, attempt/lease lifecycle, append-only dispatch and terminal receipts, idempotency operation, budget reservation/settlement, candidate artifact set, candidate result/projection hash, pair manifest/report, and provider-lineage correlation. These may be separate records or an equivalent representation, but their logical keys and atomic behavior must satisfy System Design v1.1.

New candidate artifacts must be namespaced by execution and task. New provider lineage must correlate to execution, task, and attempt through columns or a durable relation. Existing canonical tables/records remain the authority for existing production runs and cannot become candidate storage by implicit reuse.

## 2. Existing data and backfill rule

No backfill is required or permitted for existing canonical runs, `theme_chokepoint_stage3_results`, source usages, provider lineage, or historical receipts. In particular, the migration must not synthesize an execution, pair, task, attempt, candidate artifact, candidate result, task key, budget settlement, or `candidate_result_sha256` from old Stage 3 data. These data lack the required frozen plan and execution identity.

After migration, old runs retain their exact canonical status and interpretation. They may be a `base_run_id` only if they are currently eligible under the start contract; that action creates a new v1.1 candidate execution. “No backfill” is a mandatory compatibility result, not a temporary implementation shortcut.

## 3. Upgrade protocol

1. Preflight verifies the expected legacy schema/version, migration checksum, available transaction/backup capability, and that the feature is disabled for new claims.
2. Apply creates only additive v1.1 records/relations and the durable migration journal. It must not rewrite legacy Stage 3 payloads or run statuses.
3. Verify checks required logical uniqueness, required columns/relations, atomic-write capability, and read access to legacy canonical data. It records the exact schema-contract version and checksum.
4. Only after verification may a separately configured feature flag admit new v1.1 executions. The migration itself neither enables the flag nor starts an execution.

For transactional DDL, steps 2–3 are one transaction. Where the chosen datastore cannot transact DDL, the implementation must use an equivalent journaled expand/verify protocol in which partial records are not admitted by readers or feature flags. A failed preflight/apply/verify records `FAILED`; it must leave the feature disabled. A rollback records `ROLLED_BACK` only after the migration's own verification proves that no partially admitted v1.1 execution is stranded. Destructive schema rollback is not required and must not delete retained v1.1 data merely to downgrade code.

## 4. Forward compatibility, rollback, and feature disable

Each v1.1 record carries a schema/contract version. Readers reject unknown required semantic versions with `MIGRATION_REQUIRED`; additive unknown fields may be retained only when they do not change the documented projection or state machine. A later migration may add fields/records but may not reinterpret an existing receipt, task terminal state, candidate hash, or canonical run.

Disabling ablation prevents new starts and claims. It retains all plans, leases, attempts, receipts, reservations, provider lineage, candidate artifacts/results, pairs, and migration journal entries. A completion already holding a valid pre-disable fence may commit; an expired lease does not become newly claimable until re-enabled. Existing canonical Stage 3 processing continues unchanged.

Rollback of application code is supported only by retaining the expanded schema and keeping the feature disabled, unless the downgraded reader is proven forward-compatible. No automatic purge, backfill, canonical promotion, or RunStatus change is allowed during rollback. If human operators choose to remove retained data later, that is a separate authorized retention operation outside this contract.

## 5. Developer discretion

The physical table count/names, equivalent SQL types, migration framework, non-unique performance indexes, online-copy mechanics, backup location, and repository class/file layout are Developer discretion. They are acceptable only if they preserve the logical records, scoped uniqueness, no-backfill rule, atomic boundaries, feature-disable behavior, and retention requirements above.
