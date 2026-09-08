# RAG Ingestion and Generation Serving System Design

## Document Control

| Field | Value |
| --- | --- |
| Status | `FROZEN_FOR_IMPLEMENTATION` |
| Version | 1.0 |
| Freeze date | 2026-09-01 |
| Product | Financial Agent |
| Scope | Core news ingestion, canonical corpus, immutable index generations, refresh control, and grounded retrieval serving |
| Product contract | [RAG Ingestion and Generation Serving PRD](../product/rag-ingestion-serving-prd.md) |
| API contract | [RAG Ingestion and Serving API and Data Contract](../api/rag-ingestion-serving-api.md) |
| Remediation source | [RAG 抓取与检索可靠性修复方案](rag-ingestion-reliability-remediation-plan.md) |
| Operations | [RAG Corpus Migration Backup and Rollback Runbook](../operations/rag-corpus-migration-runbook.md) |
| Freeze manifest | [RAG Ingestion and Serving Freeze Manifest v1.0](../product/rag-ingestion-serving-freeze-manifest-v1.0.json) |

## 1. Purpose and Proof Boundary

This document describes the system that turns external news inputs into a
versioned, queryable RAG corpus and serves grounded answers from one immutable
index generation per request.

It defines:

- component and data ownership;
- canonical article identity and content invariants;
- refresh, retry, index-generation, activation, and serving state models;
- transaction, concurrency, recovery, and fail-closed behavior;
- security, observability, testing, and operational boundaries;
- the implemented baseline versus remaining closure work.

This design is backed by current repository code and local migration evidence.
It does **not** prove that GitHub CI has run, that an external staging or
production service is deployed, that every publisher is reachable, or that
retrieval quality remains acceptable on future corpora.

## 2. Scope

### 2.1 In scope

- NewsAPI collection and optional manual input;
- publisher-page fetching and exact-text extraction;
- article identity, canonical URL reconciliation, and story grouping;
- immutable canonical content files and content-integrity metadata;
- summarization as a derived artifact;
- fixed-window chunking, embeddings, and Chroma generation construction;
- refresh run and processing-attempt ledgers;
- bounded retry scheduling and lease-based single-owner execution;
- immutable generation verification, activation, and rollback;
- request-pinned eligibility and retrieval;
- grounded answering and supporting-article responses;
- MCP and Python service boundaries for refresh and query.

### 2.2 Out of scope

- `QuantGPT/` and `rag-from-zero/`;
- autonomous trading or portfolio execution;
- a public REST API or an OpenAPI contract;
- distributed queues, distributed locks, or a multi-region database;
- paywall bypass or access-policy circumvention;
- semantic chunking and model-quality optimization before corpus integrity;
- Entity KB lifecycle design, except where Entity KB signals enrich retrieval.

Entity resolution and KB-aware query contracts are specified separately in
[Entity KB v2 System Design](entity-kb-v2-system-design.md) and
[Entity KB v2 API and Data Contract](../api/entity-kb-v2-api.md).

## 3. Current Assessed State

The current local baseline is materially newer than the initial status block in
the remediation plan.

| Capability | Assessed state | Evidence boundary |
| --- | --- | --- |
| Canonical corpus migration | Completed locally | Runbook records 482 audited rows and 421 verified ready articles |
| Canonical content paths and hashes | Verified locally | Relative immutable paths; one historical conflict quarantined |
| Generation control plane | Implemented | SQLite generations, manifests, chunk proof, active pointer |
| Chroma v2 generation | Built and activated locally | Runbook records 421 articles and 2,810 verified chunks |
| Refresh run/attempt ledger | Implemented | SQLite run, collection, article attempt, retry target, and generation-proof tables |
| Lease and bounded retry | Implemented | Single-owner claims, heartbeat, due retry children, attempt limits |
| Successor generation on refresh | Implemented in runtime composition | Full canonical snapshot build, verification, optional CAS activation |
| Request-pinned serving | Implemented | Active generation resolution + canonical eligibility + generation reader |
| MCP refresh/query surface | Implemented locally | Python/FastMCP tool surface; allowlist controlled |
| External staging/production deployment | Not proven | No deployment claim is made by this document |
| Semantic chunking | Not implemented | Current runtime uses fixed windows with overlap |

The operational state and exact local paths remain operator-owned. The runbook,
not this design, is the authority for a specific migration or activation run.

## 4. Architectural Drivers

### 4.1 Correctness

1. A stored `ready` state must be supported by an existing, non-empty content
   file whose SHA-256 matches the authoritative hash.
2. A retrieval request must never mix index generations.
3. An index generation must cover one frozen canonical snapshot exactly.
4. A model must never create authoritative source identity or citation data.
5. A partial or failed refresh must not establish a daily success gate.

### 4.2 Recoverability

1. Canonical content and index generations are immutable after publication.
2. Activation is a short compare-and-swap transaction over one corpus pointer.
3. A failed successor build leaves the previous active generation readable.
4. Runtime data is backed up independently from Git.

### 4.3 Operability

1. Provider calls, LLM calls, embedding, and Chroma writes occur outside
   SQLite transactions.
2. One lease owner executes a refresh scope at a time.
3. Failures use stable machine-readable codes and append-only attempt evidence.
4. Every served response exposes its immutable corpus provenance.

## 5. High-Level Architecture

```text
                         write/control path

NewsAPI / manual input
        |
        v
  Source collectors -----> collection attempt ledger
        |
        v
  Ingestion orchestrator -> article attempt ledger
        |                         |
        |                         +--> retry targets / retry scheduler
        v
  SQLite canonical DB <------> immutable content files
        |                          data/articles/{id}/{sha256}.txt
        |
        +--> summary derived from active content hash
        |
        v
  Freeze full eligible canonical snapshot
        |
        v
  Build isolated Chroma successor collection
        |
        v
  Verify manifest + chunks + hashes + config
        |
        v
  CAS corpus_index_state.active_generation_id

                         read/serving path

MCP / CLI request
        |
        v
  Resolve one active generation from control DB
        |
        v
  Build read-only canonical eligibility snapshot
        |
        v
  Open generation-bound Chroma reader
        |
        v
  Time scope -> retrieve -> rerank -> grounded answer
        |
        v
  Response + generation/corpus provenance
```

## 6. Component Responsibilities

| Component | Responsibility | Must not own |
| --- | --- | --- |
| `event_collection.py` | Source requests, NewsAPI batching, normalized raw inputs, source-level outcomes | Article persistence or generation activation |
| `article_content.py` | Safe HTTP fetch, response metadata, extraction, failure classification | Refresh terminal status |
| `news_ingestion.py` | Validate inputs, reconcile identities, commit content, summarize, record per-item outcomes | Daily gate or active-generation pointer |
| `news_storage.py` | Article identity, canonical metadata, status projection, content path/hash, merge receipts | Chroma generation lifecycle |
| `refresh_ledger.py` | Run claims, leases, terminal state, attempts, retry targets, generation proof | Network/LLM/vector work |
| `refresh_retry.py` | Execute only frozen due retry targets | Re-run unrelated successful work |
| `refresh_retry_scheduler.py` | Poll due scopes and re-enter the frozen scope contract | Invent new provider parameters |
| `watchlist_workflow.py` | Compose refresh, classify outcome, finalize ledger, expose refresh payload | Low-level storage implementation |
| `generation_corpus.py` | Freeze verified canonical candidates and snapshot fingerprint | Vector backend mutation |
| `index_generation_builder.py` | Materialize candidate chunks and record observations | Activation policy |
| `index_generation.py` | Generation state, manifest verification, chunk proof, CAS activation | Chroma dependency loading |
| `successor_generation_coordinator.py` | Freeze, build, read back, verify, optionally activate a complete successor | In-place mutation of active collection |
| `active_generation_reader.py` | Resolve and validate one immutable active generation | Write or repair control state |
| `generation_eligibility.py` | Intersect active manifest with canonical file/hash/validation state | Fall back to unfiltered retrieval |
| `serving_generation_factory.py` | Construct a request-pinned reader or return stable corpus-unavailable failure | Legacy collection fallback |
| `rag_answering.py` | Produce and validate cited answer objects from server-owned evidence | External-knowledge completion |
| `financial_agent_mcp.py` | Transport mapping, allowlist, runtime composition, response mapping | Corpus truth or model-created provenance |

## 7. Data Topology and Ownership

### 7.1 Canonical article store

The canonical SQLite database owns article identity and active metadata. The
filesystem owns exact canonical text.

The active content invariant is:

```text
content_status == "ready"
AND content_validation_status is serving-eligible
AND content_path is a safe relative path
AND file exists, is regular UTF-8 text, and is non-empty
AND sha256(file) == active_content_sha256
AND quarantined_at IS NULL
```

Important article fields include:

- identity: `id`, `original_url`, `normalized_url`, `canonical_url`;
- content: `content_path`, `active_content_sha256`, `content_status`;
- validation: `content_validation_status`, reason, validator version;
- derivation: `summary_content_sha256`, `indexed_content_sha256`;
- publication: `source_published_at`, `published_at_provenance`;
- acquisition: final response URL/status/type, extractor, fetched time;
- safety: quarantine fields and identity-merge receipts.

`content_sha256` is retained as a compatibility/migration field. New serving
decisions use `active_content_sha256`.

### 7.2 Refresh ledger

The refresh ledger is separate from article truth. It owns execution evidence:

- `refresh_runs`;
- `article_processing_attempts`;
- `collection_attempts`;
- immutable article and collection retry targets;
- scope-bound retry policy;
- per-run article observations;
- successor-generation proof and article/hash coverage.

Attempt identities are append-only. A running attempt can make one terminal
transition; it is not reused or deleted.

### 7.3 Generation control database

The independent control database owns:

- `index_generations`;
- `article_index_manifest`;
- `generation_chunk_verification`;
- singleton `corpus_index_state(corpus_id, active_generation_id)`.

Generation states are:

```text
building -> verified -> active
    |           |
    +-> failed  +-> verified  (when superseded)
```

A failed immutable generation is not repaired in place. A new successor is
built instead.

### 7.4 Chroma collections

Each generation uses a distinct collection name. Every chunk is bound to:

- `generation_id`;
- `article_id`;
- `indexed_content_sha256`;
- `chunk_index`;
- `index_config_fingerprint`;
- embedding artifact and corpus snapshot through generation metadata.

The vector backend is a derived store. It is never authoritative for article
identity or canonical content.

## 8. Core Invariants

### 8.1 Article identity

- Normalized and canonical URL uniqueness is enforced by SQLite constraints.
- Canonical collisions produce explicit merge/cleanup evidence.
- Content hash changes do not create a new article identity by themselves.
- A retired identity cannot remain independently eligible.

### 8.2 Derived artifacts

- Summary is valid only when `summary_content_sha256` equals the active hash.
- Index evidence is valid only when the active manifest hash equals the active
  canonical hash.
- Identical content and identical derivation configuration are idempotent.

### 8.3 Generation completeness

Before activation:

- candidate article IDs equal manifest article IDs;
- every manifest hash equals the frozen canonical snapshot hash;
- expected and actual chunk counts are equal and positive;
- chunk indices are complete, unique, and generation-bound;
- no orphan, wrong-generation, hash-mismatch, or config-mismatch chunk exists;
- the active pointer still equals the value observed before the build.

### 8.4 Serving

- One request resolves one active generation once.
- Eligibility is the intersection of that manifest and current canonical truth.
- An unavailable control DB, invalid proof, invalid manifest, unavailable
  canonical store, or invalid reader fails closed.
- There is no unfiltered scan of a legacy collection as a fallback.

## 9. Refresh Workflow

### 9.1 Scope identity

The refresh scope key is a deterministic hash over:

- requested date;
- endpoint, days-back, page, sort, and page size;
- manual-input flag;
- corpus collection and corpus ID;
- ingestion contract version.

The current contract identifier is `refresh-generation-proof-v5`.

### 9.2 Claim and lease

1. Initialize or upgrade the ledger schema.
2. Check for a due retry child for the same scope.
3. Otherwise claim the root scope in a short `BEGIN IMMEDIATE` transaction.
4. Return `already_completed` or `in_progress` without duplicate execution.
5. Start a heartbeat watchdog for the owner lease.

Provider, LLM, embedding, and Chroma operations do not run inside the claim
transaction.

### 9.3 Collection and ingestion

1. Collect source rows and record a source-level outcome.
2. Validate each raw input independently.
3. Create or reuse stable article identity.
4. Fetch, validate, hash, and atomically commit canonical content.
5. Reconcile canonical URL and merge receipts.
6. Reuse unchanged, hash-bound derivations where valid.
7. Record article observations and terminal attempts.

One bad URL must not abort unrelated accepted inputs.

### 9.4 Successor generation

The refresh output identifies accepted ready articles, but it does not define
the complete candidate set. The coordinator freezes the full current canonical
eligible snapshot again, verifies refreshed article hashes against it, builds a
fresh collection, records the complete manifest, reads back all chunks, and
verifies the generation.

Runtime MCP composition currently enables activation after verification. The
activation transaction compare-and-swaps the pointer against the generation
observed before the build.

### 9.5 Finalization

The run commits:

- collection evidence;
- article observations;
- generation proof when available;
- terminal counts and status.

If lease ownership is lost before finalization, the public result is failed and
the worker cannot publish a successful terminal transition.

## 10. Refresh and Retry State Models

### 10.1 Public refresh statuses

| Status | Meaning | Establishes completed-scope gate |
| --- | --- | --- |
| `completed` | Collector succeeded; cumulative scope has usable, generation-verified articles and no unresolved retryable work | Yes |
| `partial` | At least one item is usable, but required or expected work remains unresolved | No |
| `failed` | Collection/system failure, no usable corpus result, exhausted retry, or lost execution ownership | No |
| `already_completed` | A prior completed run owns the same scope; no execution is created | Existing gate remains |
| `in_progress` | A live lease owns the scope | No change |
| `retry_scheduled` | Frozen retry work exists but is not due | No |

`disposition` preserves more specific retry decisions such as
`retry_exhausted` even when the public status is normalized to `failed`.

### 10.2 Retry behavior

- Retry children have new `run_id` values and retain `parent_run_id` lineage.
- Targets are frozen from terminal attempt evidence; successful unrelated work
  is not repeated.
- Default attempt limits are three for collection, content, summary, and index,
  and one for reconcile.
- Retry scheduling uses provider `Retry-After` or an explicit policy clock; the
  ingestion pass does not sleep or retry recursively.
- A retry child can establish completion only after cumulative scope
  reconciliation, including a verified generation proof.

## 11. Serving Workflow

### 11.1 Request pinning

For every query/recommendation/watchlist request:

1. Resolve `corpus_index_state.active_generation_id` read-only.
2. Validate active state, generation config fingerprint, chunk proof, and
   article manifest.
3. Open the canonical DB read-only and hydrate an eligibility snapshot.
4. Open a Chroma reader constrained to that generation and eligibility set.
5. Reuse the pinned reader for the entire request.

### 11.2 Time policy

When the caller supplies no explicit start/end bounds, the service applies a
finite UTC window. The current default is 30 days. Source publication time and
its provenance are distinct from fetch time; unknown dates do not become
current automatically.

### 11.3 Retrieval and grounding

The serving pipeline is:

```text
question
 -> optional entity resolution/query expansion
 -> generation-bound semantic retrieval
 -> evidence shaping and reranking
 -> server-owned evidence IDs
 -> structured grounded answer
 -> citation validation
 -> response with corpus provenance
```

If corpus construction fails, the MCP boundary returns `CORPUS_UNAVAILABLE`
with a stage and stable reason code. It does not call the reranker or answer LLM
against an unverified fallback corpus.

## 12. Concurrency and Transaction Boundaries

| Operation | Consistency mechanism |
| --- | --- |
| Root refresh claim | Short SQLite `BEGIN IMMEDIATE`; unique running scope |
| Retry child claim | Short transaction over immutable due targets and attempt limits |
| Lease renewal | Owner/expiry compare-and-set |
| Article content commit | File write/hash/atomic rename followed by SQLite pointer update |
| Generation build | Isolated collection and append-only control records |
| Generation activation | `BEGIN IMMEDIATE` + expected-current-generation compare-and-swap |
| Serving request | Read-only transaction and immutable in-memory snapshots |

Long-running provider and vector work must remain outside SQLite transactions
and global process locks.

## 13. Failure and Recovery Semantics

### 13.1 Collection and content failures

Stable categories include network timeout/connection errors, `429`, `5xx`,
authorization failures, invalid responses, paywall/dynamic-page suspicion,
empty or truncated extraction, invalid/private URLs, and content-integrity
mismatch.

Retryability is policy, not inference from arbitrary exception text.

### 13.2 Generation failures

- Build, manifest, or chunk reconciliation failure marks the candidate failed.
- Lease loss invalidates any non-active candidate owned by that execution.
- Activation failure leaves the prior active pointer unchanged.
- A serving pointer/proof/manifest mismatch returns corpus unavailable.

### 13.3 Grounding failures

- Source IDs and citation numbers must belong to the request evidence set.
- Server-owned title, URL, and snippet values are authoritative.
- Material claims without citations fail response validation.
- An insufficient-evidence response is allowed; invented support is not.

## 14. Security and Privacy

- NewsAPI credentials are sent in headers and must not appear in query strings,
  scope snapshots, logs, or response bodies.
- Publisher fetching validates scheme and network destination and blocks local,
  private, link-local, and metadata endpoints.
- Logs truncate and sanitize provider errors and do not persist sensitive
  headers or full page content.
- MCP tools are exposed through an explicit allowlist.
- The default HTTP listener is loopback with an allowed-host policy.
- Runtime DBs, Chroma files, canonical articles, and secrets are operational
  assets, not source-control fixtures.

## 15. Observability

Every refresh should expose or persist:

- `run_id`, parent lineage, scope key, attempt number, and contract version;
- collector and item counts;
- content/summary/index outcomes and unchanged count;
- retryable/permanent failure counts and stable codes;
- generation, corpus snapshot, and index config fingerprints;
- lease ownership failures and retry due time;
- terminal status and gate decision.

Every serving response should expose:

- active `generation_id`;
- `corpus_id` and collection name;
- `corpus_snapshot_id`;
- embedding artifact;
- index config fingerprint.

## 16. Configuration

Runtime-owned configuration includes:

- canonical DB and content root;
- generation control DB and corpus ID;
- Chroma persistence root;
- active collection compatibility value;
- refresh ledger location;
- query defaults and finite lookback;
- MCP listener and allowed hosts;
- retry scheduler and polling interval.

Paths in code are defaults, not deployment guarantees. Operators must override
them together so canonical, control, and vector stores refer to the same corpus.

## 17. Verification Strategy

### 17.1 Focused unit and contract tests

- article identity, URL normalization, relative paths, hash validation;
- refresh claim/finalization, leases, status classification, and retry limits;
- generation manifest and chunk reconciliation;
- active pointer and request pinning;
- eligibility exclusions and fail-closed serving;
- response citation validation;
- exact MCP request/response mapping.

### 17.2 Integration tests

- temporary SQLite + content root + fake or temporary vector backend;
- migration dry-run/apply/verify;
- full generation build, activation, and rollback;
- refresh partial failure and targeted retry;
- fault injection for file, DB, summary, embedding, vector write, and lease loss;
- concurrent root and retry claims.

### 17.3 Operational verification

- snapshot and backup hashes;
- canonical audit with zero issues;
- generation manifest/chunk reconciliation with zero issues;
- active pointer inspection;
- controlled query smoke test with provenance;
- external provider smoke test recorded separately from deterministic tests.

## 18. Migration and Rollback

The local migration described by the runbook has completed. Any new workspace
or deployed environment must repeat an explicitly authorized process:

1. Freeze writers and record code/data manifests.
2. Create recoverable backups outside Git.
3. Run canonical migration in dry-run, apply, and verify modes.
4. Build a separate generation from verified canonical candidates.
5. Reconcile manifest and chunks.
6. Run fixed-corpus regression and controlled query smoke tests.
7. Activate with an expected-current pointer.
8. Retain the prior generation through the rollback window.

Rollback changes the active pointer; it does not patch the old collection or
delete the new one during incident response.

## 19. Known Gaps and Next Decisions

1. External staging and production deployment are not proven.
2. The MCP/Python contract is documented, but no public REST/OpenAPI contract
   exists.
3. The public v1 response envelopes are not fully uniform across tools;
   incompatible normalization requires a versioned v2 contract.
4. Current chunking is fixed-window; semantic chunking remains a quality work
   package after integrity baselines are stable.
5. Runtime quality must be measured on a refreshed fixed corpus with sample
   sizes and annotation coverage disclosed.
6. Schema migration evidence is local; deployment-specific backup, restore,
   permissions, and retention still require operator verification.

## 20. Design Closure Checklist

The design is implementation-closed for a local runtime only when:

- canonical audit has zero ready-file/hash/path violations;
- a full successor generation verifies with zero manifest/chunk issues;
- activation and rollback both pass compare-and-swap tests;
- partial/failed refresh never establishes a completed gate;
- retry children process only frozen unresolved targets;
- every serving entry point uses a request-pinned active-generation reader;
- corpus-unavailable paths never fall back to legacy unfiltered retrieval;
- MCP contract tests cover all documented fields and statuses;
- secrets and runtime assets are absent from source-control fixtures.

Production closure additionally requires CI, deployment, health, external
provider, observation, backup/restore, and rollback evidence from the target
environment.

## 21. Frozen Decisions and Change Control

The v1 component ownership, canonical-content authority, refresh/retry state
semantics, immutable full-generation construction, compare-and-swap activation,
request pinning, fail-closed serving, and server-owned grounding boundaries are
frozen.

Implementation detail may evolve without reopening the design when all frozen
PRD and API behavior remains intact. A change requires a superseding design and
new freeze manifest when it changes component authority, transaction or
generation semantics, serving eligibility, public failure behavior, or any
security/proof boundary. Frozen artifacts are never edited silently; even an
allowed clarification receives a new manifest version and hashes.
