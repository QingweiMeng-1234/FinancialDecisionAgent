# ADR-0001: Version Entity KB Data as Immutable Releases and Emit Typed Retrieval Signals

## Status

Accepted after the 2026-08-15 System Design and API closure review.

## Date

2026-08-15

## Decision Owners

- Financial Agent product owner
- retrieval architecture owner
- Entity KB implementation owner

## Context

Financial Agent currently stores company entities, aliases, relationships, and sync history in SQLite. Retrieval can expand a ticker query and apply fixed KB boosts before LLM reranking.

The current model has four architectural problems:

1. mutable `is_current` rows do not identify one immutable point-in-time KB used by a retrieval request;
2. data changes and ranking-weight changes are not isolated, so evaluation cannot attribute gains or regressions;
3. ambiguous or stale matches can be converted directly into a numeric boost without a stable typed-signal contract;
4. different retrieval entry points can use or bypass the KB without reporting that difference.

The approved PRD requires versioned activation, fail-closed ambiguity behavior, provenance-backed facts, explainable signals, deterministic expansion, and per-asset evaluation.

## Decision

We will keep Entity KB v2 inside the existing repository and use SQLite as the authoritative local store for the MVP. We will add two related architectural boundaries.

### 1. Immutable Snapshot and Runtime Release Boundary

KB entities, aliases, relationships, and evidence will be assembled into immutable snapshots. A snapshot is never edited after approval.

Runtime activation will point to an immutable **KB release**, not to loose mutable rows. A release binds:

```text
kb_snapshot_id
kb_snapshot_checksum
resolver_policy_version + resolver_policy_checksum
freshness_policy_version + freshness_policy_checksum
signal_policy_version + signal_policy_checksum
evaluation_run_id
evaluation_manifest_hash
schema_version
```

Snapshot content follows `DRAFT -> VALIDATING -> VALIDATED -> APPROVED/REJECTED`. A mutable Release Candidate follows `DRAFT -> EVALUATED -> APPROVED/REJECTED` and owns the pre-release `evaluation_run_id` attachment. Approving an evaluated candidate creates the Final Release once; no Final Release is created with a missing evaluation, and a Final Release is never mutated afterward.

The active state will contain one nullable release ID per namespace and starts at `active_release_id=null`, `lock_version=0`. Activation and rollback update only that pointer in one short SQLite transaction. `ACTIVE` and `SUPERSEDED` are derived views from the pointer and append-only Activation Events, not mutable Snapshot or Final Release states.

A request reads the active release once at its start and passes that pinned release through resolution, expansion, attribution, scoring, and response construction. A concurrent activation cannot change the release used by an in-flight request.

### 2. Typed-Signal Boundary Between KB and Retrieval

The KB layer will not return a final relevance score. It will return typed signals containing:

- target and matched entity IDs;
- alias or relationship IDs;
- direct, indirect, or contextual signal type;
- ambiguity and freshness state;
- provenance evidence ID;
- boost eligibility and rejection reason;
- snapshot and policy versions.

Retrieval orchestration will own numeric combination, caps, semantic-score preservation, candidate ordering before rerank, and handoff to the reranker.

KB data versions and signal-weight versions remain independently versioned, but every evaluation and response records both.

## Additional Decisions

### Storage and Process Model

- SQLite remains the authoritative store for v1.0-v1.2 because the current universe and zero/one-hop access pattern do not justify a graph database or distributed service in those stages.
- A later major multi-hop relationship-intelligence stage is committed to introducing a graph database. This ADR is deliberately transitional: it fixes the invariants that the graph design must preserve, but it does not select the graph vendor or authority/cutover model.
- Repository and public service contracts remain storage-neutral. Stable domain IDs, evidence, scope, direction, validity, snapshot, and release semantics must not depend on SQLite row IDs or SQL-specific response fields.
- Snapshot build, validation, review, and evaluation occur outside the activation transaction.
- Evaluation runs are authoritative immutable records. The Final Release ID binds the approved snapshot checksum, every policy version and checksum, evaluation run and manifest hash, schema version, and versioned canonical-ID algorithm.
- Runtime reads use short-lived or thread-owned connections. SQLite connections are not assumed safe for uncontrolled cross-thread sharing.
- External source access is never required to serve an active release.
- Administrative mutation is exposed through a local CLI or controlled operator workflow, not through the initial MCP surface.

### Ambiguity and Degradation

- ambiguous or unresolved entities do not receive identity boosts;
- stale or rejected facts contribute zero;
- a caller explicitly selects `required`, `preferred`, or `disabled` KB mode;
- `required` fails when a valid KB release or resolved target is unavailable;
- `preferred` may continue in reported semantic-only mode;
- `disabled` never consults the KB.

### Relationship Depth

- direct intent uses no relationship hop;
- indirect intent is limited to one verified relationship hop in v1.1;
- broad theme and business-line matches are contextual by default and cannot independently prove direct company attribution.

### Price-In-Aware Weighting Direction

- direct identity, product, and executive signals are meaningful secondary ranking features because the product strategy assumes direct company information is more likely to be captured by semantic retrieval and already reflected in market price;
- verified non-direct dependency, supplier, customer, substitute, and competitor relationships receive materially larger contributions because relationship-mediated impact is the intended differentiated retrieval value;
- the locked initial `signals.price_in.v1` policy multiplies the semantic score by `0.70`, caps direct contribution at `0.15`, and uses a one-hop indirect range of `0.18` to `0.30` capped at `0.30`;
- v1.1 uses only the strongest eligible relationship for one article and target; duplicate aliases, labels, or paths do not stack;
- strongest-relationship selection is deterministic: order otherwise-eligible indirect signals by contribution descending and then `relationship_id` ascending; only the first contributes, and the remainder are rejected with `LOWER_PRIORITY_RELATIONSHIP`;
- direct and indirect contributions are intent-specific and are not accumulated together for one candidate decision;
- theme and business-line context without a verified directed relationship contributes zero;
- under `freshness.v1`, only `current` can contribute; `aging` and `stale` contribute zero;
- these values belong to the versioned signal policy and require fixed-input evaluation before activation. This decision records a product ranking hypothesis, not proof of market price-in behavior.

### Committed Graph Evolution

- the graph stage begins only after the one-hop relationship corpus and fixed-input evaluation pass their quality gates;
- it supports bounded paths of at least two verified relationship hops and preserves evidence on every edge;
- a superseding ADR must choose one authoritative fact owner or a rebuildable immutable graph projection; dual editable authority is prohibited;
- graph activation must remain release-pinned, checksum-verifiable, auditable, and rollback-safe;
- graph technology selection, deployment topology, consistency, backup/restore, and cutover are deferred to that superseding ADR.

## Alternatives Considered

### Alternative A: Continue Mutating `is_current` Rows

Rejected because a request cannot prove which complete KB state it consumed, partial refreshes can produce mixed versions, rollback requires row reconstruction, and evaluation cannot pin a stable input.

### Alternative B: Store a Final `kb_score` in the KB

Rejected because data truth and ranking policy would be coupled. Changing weights would look like a data migration, and the same fact could not be evaluated under multiple policies.

### Alternative C: Create a New SQLite Database File for Every Snapshot

Rejected for the MVP because file lifecycle, connection switching, backup, path safety, and cross-snapshot evidence deduplication add operational complexity. Logical snapshot rows plus an atomic release pointer provide the required isolation at the current scale.

This alternative may be revisited if database size or migration isolation becomes a measured bottleneck.

### Alternative D: Introduce a Graph Database Now

Rejected for v1.0-v1.2 because current traversal is bounded to zero or one relationship hop, the universe is small, and correctness, provenance, and evaluation are the immediate problems. A graph database would not solve bad aliases, missing evidence, stale facts, or evaluation drift.

This is a deferral, not a permanent rejection. The later major multi-hop relationship-intelligence stage must introduce a graph database under a superseding ADR after the current relationship corpus and evaluation foundation are proven.

### Alternative E: Run Entity Resolution Inside the LLM Reranker

Rejected because resolution would become non-deterministic, difficult to test, and impossible to fail closed consistently. The reranker consumes already prepared candidates and does not own entity truth.

### Alternative F: Silently Fall Back Whenever the KB Fails

Rejected because callers could mistake semantic-only results for KB-enhanced results. Fallback is allowed only under explicit `preferred` mode and must be visible in the response.

## Consequences

### Positive

- every response can identify the exact KB and policy versions it used;
- activation and rollback are constant-scope pointer updates;
- immutable snapshots allow safe request pinning and caching;
- data changes can be evaluated separately from score-policy changes;
- ambiguous, stale, and rejected facts have explicit zero-contribution semantics;
- entry-point differences become observable through `kb_mode` and applied-mode fields;
- SQLite remains sufficient and operationally simple for the MVP.

### Negative

- snapshot rows duplicate some logical data across versions;
- migration tooling and a release manifest are required;
- callers must handle structured resolution and KB-mode states;
- operator approval and evaluation become explicit release gates;
- legacy readers require a compatibility window.

### Risks

- snapshot growth may eventually require compaction or archival;
- a release can bind incompatible policy versions unless validation checks the complete tuple;
- pinning works only if all downstream functions receive the same release context;
- optional semantic-only degradation can still confuse users if adapters omit degradation fields.
- implementation may accidentally leak SQLite-specific IDs or query behavior into public contracts and make the committed graph migration breaking;
- the future graph migration may create two editable fact authorities unless its ADR selects one owner and defines checksum, readiness, reconciliation, and rollback.

## Required Invariants

1. At most one active release exists per KB namespace.
2. An approved snapshot is immutable.
3. A Final Release is created only from an approved Release Candidate and immutably binds an approved snapshot checksum, compatible policy versions and checksums, an approved evaluation run and manifest hash, and the schema version.
4. Activation uses compare-and-swap semantics against the expected current release.
5. Failed build, validation, evaluation, or activation leaves the current release unchanged.
6. One retrieval request uses one pinned release from start to finish.
7. Ambiguous, stale, rejected, or provenance-invalid facts contribute zero.
8. The KB never owns or hides the final relevance score.
9. Every non-zero KB contribution is traceable to a signal and evidence record.
10. MCP responses report requested mode, applied mode, degradation, release ID, and policy versions.
11. Administrative activation and rollback are not exposed as public MCP tools in v1.0.
12. Historical snapshots and activation events remain auditable after rollback.
13. Domain IDs and public contracts remain storage-neutral across the SQLite and future graph stages.
14. A future graph store preserves immutable release pinning, provenance, freshness, ambiguity, zero-contribution, audit, and rollback semantics.
15. The active signal policy keeps the direct total cap below every non-zero indirect relationship contribution unless a superseding product decision explicitly changes the price-in-aware ranking direction.
16. `signals.price_in.v1` uses `semantic_weight=0.70`, direct cap `0.15`, indirect range `0.18-0.30`, indirect cap `0.30`, and deterministic strongest-relationship-only semantics; changing any of these requires a new policy version and evaluation.
17. Under `freshness.v1`, `aging` and `stale` signals contribute zero; reduced aging credit requires new evaluated policy versions.

## Verification Requirements

The implementation must provide:

- schema and migration tests;
- expected-RED then GREEN tests for every invariant-changing work package;
- snapshot immutability tests;
- activation compare-and-swap concurrency tests using independent SQLite connections;
- request-pinning tests across a concurrent activation;
- ambiguity, freshness, and zero-contribution tests;
- signal-policy tests proving data and weights are independently versioned;
- MCP contract tests for required, preferred, and disabled modes;
- migration dry-run, apply, verify, and rollback evidence;
- fixed-input retrieval evaluation before activation.
- storage-neutral repository contract tests that do not expose SQLite row IDs;
- graph-export or projection fixtures proving that approved nodes, directed edges, evidence, scope, and validity can be represented without semantic loss before the graph-stage ADR is accepted.

## Review and Supersession

This ADR becomes `Accepted` only after the System Design and API/Data Contract pass closure review. The committed graph-database stage must supersede this ADR before implementation. Its ADR may change storage and traversal decisions, but it must explicitly preserve or deliberately replace each invariant recorded here rather than silently weakening release pinning, provenance, explainability, or rollback.
