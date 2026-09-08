# RAG Ingestion and Generation Serving PRD

## Document Control

| Field | Value |
| --- | --- |
| Status | `FROZEN_FOR_IMPLEMENTATION` |
| Product version | 1.0 |
| Contract family | `financial-agent.rag.v1` |
| Freeze date | 2026-09-01 |
| Product | Financial Agent |
| Scope | Core news ingestion, canonical corpus integrity, immutable generation refresh, and grounded retrieval serving |
| System Design | [RAG Ingestion and Generation Serving System Design](../architecture/rag-ingestion-serving-system-design.md) |
| API Contract | [RAG Ingestion and Serving API and Data Contract](../api/rag-ingestion-serving-api.md) |
| Freeze manifest | [RAG Ingestion and Serving Freeze Manifest v1.0](rag-ingestion-serving-freeze-manifest-v1.0.json) |

## 1. Product Decision

Financial Agent shall maintain a news-grounded RAG corpus whose canonical
content, derived index, refresh state, and served evidence can be reconciled to
one another. The product shall prefer an explicit unavailable or insufficient
result over an answer produced from stale, unverified, mixed-generation, or
uncited evidence.

This PRD freezes the v1 product behavior and acceptance boundary. It does not
claim that every target environment is deployed, that every external publisher
is reachable, or that future retrieval quality is already proven.

## 2. Problem Statement

A financial-news RAG system can appear functional while its storage layers no
longer describe the same corpus. Typical failure modes include:

- article rows marked ready while their content files are missing or tied to a
  different machine;
- local files whose hashes do not match the authoritative database value;
- vector records built from content that is no longer active;
- refresh jobs reported as successful even though required items failed;
- retries that repeat successful work or race another worker;
- queries that scan a legacy collection without checking canonical
  eligibility;
- answers that expose model-invented sources or uncited material claims.

These failures make retrieval metrics, model evaluation, and user-visible
answers difficult to trust. The product must establish corpus integrity and
serving provenance before optimizing embeddings, chunking, reranking, or model
prompts.

## 3. Product Goal

For every completed refresh and every served research request, a reviewer must
be able to answer:

1. Which canonical article versions were eligible?
2. Which immutable index generation represented them?
3. Did that generation cover the frozen candidate set without gaps or mixed
   configuration?
4. Which generation and evidence IDs produced this answer?
5. Were partial failures, retry work, and unavailable states disclosed rather
   than hidden?

## 4. Users and Jobs

### 4.1 Human financial researcher

The researcher needs recent, attributable evidence and must know when the
system lacks enough verified material. The workflow supports research
prioritization and grounded analysis, not autonomous trade execution.

### 4.2 Operator or developer

The operator needs deterministic refresh status, durable failure evidence,
bounded retry, reversible generation activation, and safe migration/rollback.

### 4.3 MCP client or workflow orchestrator

The client needs stable request/response contracts, idempotent refresh behavior,
immutable corpus provenance, and machine-readable failures.

## 5. Scope

### 5.1 In scope

- NewsAPI collection and optional manual input;
- publisher-content acquisition and validation;
- article identity and canonical URL reconciliation;
- immutable, hash-addressed canonical content;
- summary and vector index derivation bound to active content hash;
- refresh run, attempt, lease, retry, and daily-scope state;
- complete successor-generation build, verification, activation, and rollback;
- request-pinned eligibility, time filtering, retrieval, reranking, and grounded
  answering;
- MCP/Python contracts for refresh and read operations;
- audit, observability, security, migration, and verification requirements.

### 5.2 Out of scope

- autonomous investment decisions, order execution, and portfolio sizing;
- `QuantGPT/` and `rag-from-zero/`;
- distributed queues, multi-region storage, or microservice decomposition;
- public REST/OpenAPI resources in v1;
- paywall or access-policy bypass;
- unbounded crawling or unbounded concurrency;
- semantic chunking or model replacement as a substitute for data integrity;
- Entity KB lifecycle semantics, except for additive retrieval enrichment.

## 6. Frozen Product Principles

1. **Canonical content first.** Exact canonical text is the source of truth;
   summaries, chunks, embeddings, and snippets are derived artifacts.
2. **Fail closed.** Missing or inconsistent control, canonical, or vector state
   cannot fall back to an unfiltered legacy corpus.
3. **Immutable generations.** Index generations are built separately, verified
   completely, and activated atomically.
4. **Truthful refresh status.** Partial, failed, scheduled, and unavailable
   states remain visible and cannot be normalized into success.
5. **Bounded recovery.** Retries are targeted, durable, attempt-limited, and
   single-owner.
6. **Request provenance.** Every successful read identifies its immutable
   generation and corpus snapshot.
7. **Server-owned grounding.** Source metadata and citation identity come from
   server evidence, not model invention.
8. **Proof boundaries.** Local tests, local migration, external reachability,
   deployment health, and long-horizon quality are distinct claims.

## 7. Functional Requirements

### PRD-RAG-001 — Stable article identity

The product shall maintain a stable integer `article_id` and reconcile original,
normalized, redirected, and canonical URLs without silently creating multiple
active identities for the same canonical article.

Acceptance boundary:

- normalized and canonical URL uniqueness is protected by storage constraints;
- canonical collisions create explicit merge/cleanup evidence;
- content changes do not create a new article identity by themselves;
- retired identities cannot remain independently retrieval-eligible.

### PRD-RAG-002 — Verifiable canonical content

An article may be considered content-ready only when its canonical file is a
safe relative path, exists as non-empty UTF-8 text, passes source validation,
matches `active_content_sha256`, and is not quarantined.

Acceptance boundary:

- machine-specific absolute content paths are ineligible;
- file write and pointer change cannot corrupt the previous active version;
- unknown or conflicting files are quarantined rather than promoted silently;
- summary and index hashes cannot replace the authoritative active content
  hash.

### PRD-RAG-003 — Hash-bound derived artifacts

Summary and index artifacts shall be valid only for the active content hash and
their declared model/configuration identity.

Acceptance boundary:

- unchanged content with identical derivation configuration is idempotent;
- stale summary may be omitted but not presented as current;
- stale index content is excluded from serving;
- duplicate execution does not create duplicate chunks for the same generation.

### PRD-RAG-004 — Truthful refresh lifecycle

The product shall expose `completed`, `partial`, `failed`,
`already_completed`, `in_progress`, and `retry_scheduled` with the semantics
frozen in the API Contract.

Acceptance boundary:

- only `completed` establishes a completed-scope gate;
- mixed usable and unresolved work is `partial`;
- a live owner prevents a competing execution;
- a completed duplicate is a structured no-op;
- all accepted items becoming permanent exclusions is not reported as a usable
  completed refresh.

### PRD-RAG-005 — Durable collection and processing evidence

The product shall retain refresh run identity, collection outcomes, per-article
attempts, stable failure codes, retry policy, retry targets, article
observations, and generation proof.

Acceptance boundary:

- attempt rows are append-only identities;
- a running attempt makes at most one terminal transition;
- provider errors are separated from per-article failures;
- secret-bearing request data is not persisted in ledger snapshots.

### PRD-RAG-006 — Bounded targeted retry

Retry execution shall process only frozen unresolved targets from the same
scope chain.

Acceptance boundary:

- retry child uses a new `run_id` and retains parent lineage;
- successful unrelated articles are not reprocessed;
- attempt limits are frozen per scope and cannot drift between workers;
- provider `Retry-After` or explicit policy time controls due time;
- retry exhaustion is visible and requires manual review or a new authorized
  decision.

### PRD-RAG-007 — Complete immutable successor generation

Every refresh that changes the eligible corpus shall build a separate complete
generation from a newly frozen canonical candidate snapshot.

Acceptance boundary:

- manifest article IDs equal the complete frozen candidate set;
- every article hash equals the canonical snapshot hash;
- expected and actual chunk counts match and are positive;
- chunks have complete unique indices and one generation/config identity;
- no orphan, mixed-generation, hash-mismatch, or config-mismatch chunk exists;
- a failed candidate is not repaired or activated in place.

### PRD-RAG-008 — Atomic activation and rollback

Only a verified successor may become active, using a compare-and-swap against
the previously observed active generation.

Acceptance boundary:

- concurrent pointer change rejects activation;
- activation failure leaves the prior generation active;
- superseded generations remain available through the rollback window;
- rollback changes the pointer rather than modifying an old collection.

### PRD-RAG-009 — Request-pinned fail-closed serving

Every read request shall resolve one active generation, build one canonical
eligibility snapshot, and use one generation-bound reader for the request
lifetime.

Acceptance boundary:

- missing pointer, invalid state/proof/manifest, unavailable canonical storage,
  invalid eligibility, or reader failure returns `CORPUS_UNAVAILABLE`;
- no read path falls back to an unverified legacy collection;
- activation during a request affects later requests, not the pinned request;
- successful responses expose immutable corpus provenance.

### PRD-RAG-010 — Finite and attributable time policy

Research reads shall apply explicit time bounds or a finite configured default
window.

Acceptance boundary:

- source publication time is distinct from fetch time;
- unknown date is not coerced to current time;
- explicit caller bounds are preserved;
- default queries use a UTC anchor and the configured finite lookback.

### PRD-RAG-011 — Grounded response enforcement

All material answer claims shall be supported by citations to the request's
server-owned evidence set.

Acceptance boundary:

- returned source IDs belong to request evidence;
- source title, URL, and snippet are server-owned;
- citations refer only to existing response sources;
- uncited material claims fail response validation;
- insufficient evidence is a valid result and must not be filled with external
  model knowledge.

### PRD-RAG-012 — Stable client contracts

The product shall preserve the v1 MCP/Python contracts for `refresh_news`,
`query_news_research`, and `retrieve_supporting_articles`.

Acceptance boundary:

- current field names retain type and meaning;
- runtime paths and credentials remain server-owned;
- expected unavailable outcomes are structured;
- breaking envelope or status changes require versioned v2 tool names or an
  approved migration.

### PRD-RAG-013 — Secure acquisition and serving

The product shall treat provider rows, publisher pages, model output, and MCP
client input as untrusted.

Acceptance boundary:

- provider credentials are sent through approved secret-bearing headers and do
  not appear in URLs, scope snapshots, logs, or responses;
- fetch redirects cannot reach local, private, link-local, or metadata targets;
- logs omit sensitive headers and full unredacted page content;
- mutating MCP tools remain explicitly allowlisted;
- runtime corpus assets are not source-control fixtures.

### PRD-RAG-014 — Observable and auditable operation

Refresh and read operations shall disclose enough identity and counts to
diagnose corpus state without relying on model text.

Acceptance boundary:

- refresh exposes run/scope/attempt identity, counts, status, failure and retry
  fields, and generation proof when available;
- read responses expose generation and corpus provenance;
- operator evidence can reconcile canonical candidates, manifest entries, and
  observed chunks;
- messages are diagnostic while codes and structured fields remain the stable
  machine contract.

## 8. Non-Functional Requirements

### 8.1 Consistency

- SQLite transactions around claims and activation must be short.
- Provider, LLM, embedding, and vector-backend work must occur outside those
  transactions.
- One refresh scope has at most one unexpired owner.
- One read request uses one immutable generation snapshot.

### 8.2 Recoverability

- Canonical DB, content, Chroma, and control state require recoverable backups
  independent from Git.
- Migration supports dry-run, apply, and verify.
- Previous active generation is retained during the defined rollback window.
- Destructive cleanup is not part of incident rollback.

### 8.3 Performance and resource control

- External retries and concurrency are bounded.
- Unchanged hash-bound artifacts are reused where valid.
- The product may optimize chunking or embeddings only after integrity and
  fixed-corpus evaluation gates pass.
- V1 defines no fixed latency SLO; deployments must measure and set one using
  target hardware and providers.

### 8.4 Compatibility

- V1 MCP tool names and payload meanings remain stable.
- Storage internals and SQLite row layout are not client contracts except where
  explicitly documented as internal data contracts.
- Entity KB enrichment remains additive and versioned separately.

## 9. Success Metrics and Release Gates

### 9.1 Integrity gates

The following must be zero for the activated generation:

- ready article with missing, empty, unsafe, non-UTF-8, or hash-mismatched file;
- quarantined or source-validation-ineligible article served;
- manifest missing/unexpected article;
- manifest hash/config/chunk-count mismatch;
- wrong-generation, orphan, duplicate-index, missing-index, or invalid-index
  chunk;
- request path that bypasses active-generation eligibility;
- material answer claim with an invalid or missing citation.

### 9.2 Refresh behavior gates

The deterministic suite must prove:

1. all-success completion;
2. all-content-failure failure;
3. mixed-result partial status;
4. unchanged-content derivation reuse;
5. generation build/embedding failure without pointer corruption;
6. crash/lease-loss behavior without false completion;
7. concurrent same-scope single ownership;
8. classified retry scheduling and attempt exhaustion;
9. targeted retry without repeating successful work;
10. full successor coverage and post-activation new-hash serving.

### 9.3 Serving gates

- every successful read exposes matching top-level and nested generation IDs;
- unavailable corpus returns stable stage/reason data and no normal answer;
- time-window tests reject stale and unknown-date evidence where applicable;
- source/citation tampering is rejected;
- fixed-corpus retrieval evaluation is no worse than the approved baseline,
  with sample size and unannotated coverage disclosed.

### 9.4 Environment gates

Local implementation evidence is necessary but insufficient for release. Each
target environment requires:

- CI result for the frozen commit and assets;
- backup and restore evidence;
- migration or bootstrap verification;
- active pointer and controlled query smoke evidence;
- provider reachability evidence separated from deterministic tests;
- health, observation, rollback, and deployed-version evidence.

## 10. Primary User Journeys

### 10.1 Daily refresh

1. Client calls `refresh_news` for a business date and provider scope.
2. Service returns completed, partial, failed, already-completed, in-progress,
   or retry-scheduled with durable identity and counts.
3. Completed refresh exposes generation proof.
4. Later read requests expose the actually active generation.

### 10.2 Grounded research query

1. Client submits a question and optional finite time window.
2. Service pins the active generation and canonical eligibility.
3. Service retrieves, reranks, answers, and validates citations.
4. Client receives answer, evidence, insufficiency state, and corpus provenance,
   or a structured corpus-unavailable result.

### 10.3 Supporting-evidence retrieval

1. Client submits a query and retrieval intent.
2. Service uses the same generation/eligibility/time boundary as answering.
3. Client receives ranked server-owned evidence and provenance without answer
   generation.

### 10.4 Operator rollback

1. Operator identifies the last verified prior generation.
2. Operator verifies target proof and current pointer.
3. Operator performs an authorized pointer rollback.
4. Operator runs a controlled query and records evidence.
5. Cleanup remains a later retention action, not part of rollback.

## 11. Dependencies and Assumptions

- SQLite remains authoritative for canonical metadata, refresh execution, and
  generation control in v1.
- Exact content remains filesystem-backed and path-relative.
- Chroma remains the vector backend for v1 generations.
- External providers, publisher sites, embedding artifacts, and LLMs may fail
  independently.
- Provider credentials and deployment permissions are operator-owned.
- The existing Entity KB may enrich retrieval but cannot bypass corpus
  eligibility or grounding rules.

## 12. Frozen Decisions

The following decisions are frozen for `financial-agent.rag.v1`:

1. canonical content is authoritative;
2. content files are immutable and hash-addressed;
3. refresh scope and retry lineage are durable;
4. only completed scopes establish the success gate;
5. retries are targeted, bounded, and single-owner;
6. index generations are immutable and complete;
7. activation and rollback use the corpus pointer;
8. reads pin one active generation and fail closed;
9. default read time is finite;
10. material claims require server-validated citations;
11. MCP/Python is the v1 public contract; REST/OpenAPI is not inferred;
12. local proof does not equal deployment or production proof.

## 13. Open Work Within the Frozen Contract

Implementation or operational work may continue without reopening this PRD
when it preserves the frozen decisions, including:

- deployment-specific CI, staging, production, and rollback evidence;
- closing any remaining entry point that bypasses the pinned reader;
- adding read-only operator inspection tools with additive v1 fields or a
  versioned tool;
- improving request validation without changing accepted valid inputs;
- adding metrics, alerts, fixtures, and deterministic fault tests;
- evaluating semantic chunking or new embeddings behind a new immutable index
  configuration and approved quality gate.

## 14. Change Control

The PRD must be reopened or superseded before any change that:

- allows serving from an unverified or mixed generation;
- makes partial/failed work establish the completed gate;
- permits unbounded retries or concurrent owners for one scope;
- removes corpus provenance from successful reads;
- permits uncited material claims or model-owned source identity;
- exposes secrets or runtime paths as client-controlled refresh fields;
- changes v1 field meaning or status semantics without versioning;
- replaces canonical content as the authoritative source;
- claims production release without target-environment evidence.

Editorial clarification, additional tests, observability, additive nullable
fields, and implementation detail changes that preserve every frozen decision
do not require a PRD revision. Any permitted edit to a frozen artifact still
requires a new manifest version and hashes; frozen files are not silently
modified in place.

## 15. Approval and Proof Boundary

The user instruction on 2026-09-01 authorizes freezing this PRD together with
the linked System Design and API Contract as v1.0 repository artifacts.

Approval means the product, architecture, and interface semantics are stable
enough for implementation and verification. It does not authorize production
deployment, external data mutation, provider spending, destructive migration,
or relaxation of the documented safety and evidence gates.
