# Financial Agent Entity KB Upgrade PRD

## Document Control

| Field | Value |
| --- | --- |
| Status | Approved v1.0 for architecture and implementation planning |
| Date | 2026-08-15 |
| Product | Financial Agent |
| Capability | Entity KB v2 for retrieval augmentation |
| Primary users | Investment researcher, retrieval workflow, KB operator |
| Delivery strategy | Identity foundation first, directed relationships second |
| Default operating mode | Evidence-backed, versioned, fail-closed on ambiguity |

## 1. Executive Summary

Financial Agent already uses a company entity knowledge base to expand ticker queries and add company-aware signals to retrieved news. The existing KB proves that entity knowledge can improve retrieval, but its current coverage, freshness, alias quality, resolution behavior, and score explainability are not reliable enough to serve as a stable ranking dependency.

Entity KB v2 will turn the current profile collection into a versioned retrieval knowledge product. The first release will focus on high-confidence identity and attribution:

1. resolve tickers and company mentions from natural-language questions;
2. maintain curated English and Chinese aliases for the priority universe;
3. attach source, observation time, validity, review status, and freshness to every material fact;
4. reject noisy, truncated, ambiguous, or stale facts before activation;
5. emit typed KB signals instead of an opaque blended score;
6. expose the score contribution and evidence behind every retrieval boost;
7. evaluate aggregate and per-asset retrieval quality on a fixed, versioned Golden Set.

The second release will add directed supplier, customer, dependency, competitor, and substitute relationships for indirect relevance. It will not treat broad theme overlap as proof of a company relationship.

The product will not attempt to build a universal financial knowledge graph. It will start with the current evaluation and watchlist universe, prove retrieval value without per-ticker regressions, and then expand through an explicit promotion process.

SQLite is the required authoritative store for the v1.0-v1.2 delivery stages. A later major relationship-intelligence stage shall introduce a graph database for bounded multi-hop path retrieval and graph analysis. The graph stage is a committed product direction, not an optional response to an unspecified scale threshold; its vendor, authority model, and cutover design require a superseding ADR after the one-hop relationship foundation and Golden Set are proven.

## 2. Current State and Evidence

### 2.1 Current Capability

The repository currently supports:

- SQLite-backed company, entity, alias, relationship, and sync-run records;
- company lookup by ticker;
- direct query expansion with ticker, company name, CEO, and product names;
- indirect query expansion with products, business lines, and themes;
- exact normalized alias matching against title, summary, and a short content snippet;
- fixed KB boosts for company, product, CEO, ticker, business-line, and theme matches;
- retrieval comparison and annotation-driven evaluation;
- historical relationship records through `is_current`.

### 2.2 Verified Local Snapshot

The local `company_entities.db` snapshot inspected on 2026-08-15 contains:

- 33 companies;
- 34 ticker records;
- 394 entities;
- 432 aliases;
- 295 current relationships;
- 10 of 33 companies without a current CEO relationship;
- 228 historical sync attempts: 137 success, 52 failed, and 39 missing;
- latest successful company sync timestamps from 2026-05-22.

The current seed also contains quality defects such as:

- empty common-company alias lists for most profiles;
- missing CEO values for well-covered public companies;
- truncated aliases such as `Constellation Energy Corporatio` and `Taiwan Semiconductor Manufactur`;
- generic website-navigation tokens incorrectly classified as products, such as `Company`, `Inc`, `NYSE`, and `American`;
- broad themes that can match many unrelated companies.

These counts describe the inspected local snapshot, not production state.

### 2.3 Current Behavioral Gaps

1. **Exact-ticker resolver boundary.** Company context is loaded by comparing the entire input string with a normalized ticker. `MSFT` can resolve; `What changed for MSFT?` does not.
2. **Missing aliases create false negatives.** Exact token-phrase matching cannot infer that `Apple` means `Apple Inc.` when the short name is absent.
3. **No ambiguity contract.** A matched alias is treated as a positive signal without a stored ambiguity level, collision policy, or resolution explanation.
4. **No fact-level freshness gate.** Relationships have observation timestamps, but retrieval does not consistently suppress or downgrade stale facts by fact type.
5. **No activation snapshot.** Current records can be updated, but the retrieval consumer is not bound to one immutable, verified KB version.
6. **Weak relationship semantics.** Indirect relevance uses products, business lines, and themes but lacks explicit suppliers, customers, competitors, dependencies, and substitute relationships.
7. **Opaque ranking contribution.** The external response can expose `kb_boost` and `final_score`, but not every raw KB signal, rejected signal, source, freshness result, or ambiguity decision.
8. **Entry-point inconsistency.** Some retrieval paths provide the company KB while ordinary grounded query paths can bypass it.
9. **Relative vector normalization amplifies KB defects.** A bad KB signal can materially reorder a small candidate pool even when the underlying semantic distances are close.
10. **Aggregate metrics can hide asset regressions.** Historical evaluation improved in aggregate while some tickers became worse.

### 2.4 Historical Evaluation Boundary

The 2026-05-27 evaluation reported aggregate improvements from the entity-KB-enhanced chain:

- macro precision: `0.266 -> 0.319`;
- macro recall: `0.247 -> 0.274`;
- micro precision: `0.270 -> 0.358`;
- micro recall: `0.248 -> 0.312`;
- false-positive rate: `0.734 -> 0.681`.

The same report showed regressions for individual tickers, including AVGO, TGT, and WMT, while TSLA improved materially. The historical report demonstrates potential value and instability. It shall not be used as the Entity KB v2 acceptance baseline because the corpus, code, annotations, and active index may have changed.

## 3. Problem Statement

Financial Agent needs entity knowledge to answer two different questions:

1. **Direct identity:** Is this article actually about the requested company or asset?
2. **Indirect exposure:** Could this article affect the requested company through a verified business relationship?

The current KB mixes identity clues, broad thematic overlap, and ranking weights without sufficient provenance, ambiguity handling, or freshness controls. This creates two failure classes:

- **false negative:** a relevant article is missed because the company name, product, executive, or relationship is absent or cannot be resolved;
- **false positive:** an irrelevant article is promoted because a generic, ambiguous, stale, or incorrectly typed alias matched.

The product must therefore establish a trustworthy contract:

```text
Source evidence
-> proposed fact
-> validation and ambiguity review
-> immutable KB snapshot
-> entity resolution
-> typed retrieval signal
-> bounded score contribution
-> per-result explanation
-> Golden Set evaluation and feedback
```

## 4. Target Users and Jobs to Be Done

### 4.1 Human Investment Researcher

When asking about a company or asset, the researcher wants the system to retrieve direct and indirect evidence without silently promoting unrelated articles, and wants to understand why each source was selected.

### 4.2 Retrieval Workflow

When building a candidate pool, the retrieval workflow needs stable entity IDs, approved expansion terms, typed relationships, freshness state, and bounded signals. It must not infer provenance or fact validity from raw strings.

### 4.3 KB Operator or Curator

When refreshing the KB, the operator needs to see proposed changes, validation failures, alias collisions, stale facts, affected assets, and expected retrieval impact before activation. The operator needs rollback to a known prior Final Release.

### 4.4 Evaluation Owner

When assessing an upgrade, the evaluation owner needs a fixed corpus, KB snapshot, annotation version, retrieval configuration, and per-asset metrics so aggregate gains cannot hide regressions.

## 5. Product Goals

### 5.1 Goals

1. Resolve priority tickers and common English and Chinese company mentions from natural-language queries.
2. Separate direct identity signals from indirect exposure signals.
3. Bind every activated alias and relationship to source evidence, observation time, validity, and review status.
4. Prevent ambiguous, stale, truncated, generic, or unverified facts from contributing a positive boost.
5. Version KB data and retrieval policy independently but record both on every decision.
6. Make every accepted and rejected KB contribution explainable.
7. Establish deterministic validation and activation gates.
8. Improve retrieval quality without material per-asset regression on the priority universe.
9. Support human corrections as durable reviewed facts rather than one-off prompt instructions.
10. Expand coverage only after the existing tier passes data and retrieval quality gates.
11. Preserve stable, storage-neutral entity, relationship, evidence, snapshot, and release identities so the approved relationship corpus can migrate to a graph database without changing product semantics.

### 5.2 Non-Goals

Entity KB v2 will not initially:

- cover every listed company, fund, index, executive, product, or supply-chain relationship;
- autonomously crawl the entire web or accept LLM-generated facts without source evidence;
- replace the article corpus, vector store, embedding model, reranker, or answer model;
- redesign the complete RAG scoring algorithm beyond the KB signal boundary;
- treat a theme match as proof of company exposure;
- provide autonomous investment recommendations;
- infer revenue or earnings impact from identity or relationship data alone;
- become a general-purpose ontology or graph database platform in the v1.0-v1.2 stages; the committed later graph stage remains domain-bounded to financial relationship intelligence;
- make live provider availability a prerequisite for read-only retrieval;
- use the historical May evaluation as proof of current quality.

## 6. Product Principles

1. **Identity before expansion.** The system shall resolve the target before adding query terms.
2. **Strong and weak signals stay separate.** Exact ticker and reviewed company alias signals shall not be blended with themes under one undifferentiated label.
3. **Direction matters.** `supplier_of`, `customer_of`, and `competes_with` are not interchangeable.
4. **Provenance before activation.** No material fact contributes to retrieval without evidence and observation metadata.
5. **Ambiguous means no automatic boost.** The system may return candidates for review but shall not silently choose one.
6. **Stale is not current.** Each fact type has a freshness policy; expired facts do not contribute as current facts.
7. **Unknown is a valid result.** Missing relationships shall not be inferred as false, and missing data shall not be filled from model memory.
8. **KB emits signals; retrieval owns ranking.** The KB shall not hide a final relevance score inside the store.
9. **Activation is snapshot-based.** Retrieval consumes one verified KB snapshot at a time.
10. **Per-asset regressions are visible.** Aggregate improvements do not automatically authorize activation.
11. **Human corrections outrank unreviewed extraction.** Corrections retain provenance and audit history.
12. **Expansion is bounded.** Query expansion has deterministic term and relationship-hop limits.
13. **Indirect relationships carry the larger ranking value.** The product strategy treats direct company information as more likely to be already reflected in price and semantic retrieval. Direct KB signals are meaningful secondary ranking features, while verified non-direct relationship signals receive a strictly larger contribution. This is a ranking hypothesis that remains subject to fixed-input retrieval evaluation, not a claim that market price-in has been empirically proven by the current repository.

## 7. Scope and Universe

### 7.1 Tier A: Acceptance-Critical Assets

The recommended initial Tier A universe is the existing historical evaluation set:

- companies: AAPL, AMD, AMZN, AVGO, GOOG/GOOGL, META, MSFT, NVDA, ORCL, TGT, TSLA, WMT;
- non-company assets: DJI, QQQ, SPY, XLE.

GOOG and GOOGL shall resolve to one company entity with multiple ticker aliases.

Non-company assets shall use an `index` or `fund` entity type and shall not be forced into the company schema.

### 7.2 Tier B: Existing Company Coverage

Tier B contains the remaining companies already present in the local KB. Tier B must meet data validation gates before activation but is not allowed to block the first Tier A retrieval experiment unless it creates cross-entity ambiguity.

### 7.3 Expansion Policy

A new asset can be promoted only when:

1. required identity fields are complete;
2. aliases pass collision and quality checks;
3. current material facts have evidence and freshness state;
4. at least the minimum asset-specific Golden Set exists;
5. retrieval comparison produces no unreviewed critical regression;
6. the asset is included in an activated snapshot.

## 8. Ubiquitous Language

| Term | Product Meaning |
| --- | --- |
| Entity | A stable real-world object such as company, fund, index, person, product, business line, or theme. |
| Asset | A company, fund, or index that can be the target of a user query or evaluation. |
| Alias | A language- and policy-scoped name that may refer to an entity. |
| Strong Alias | A reviewed alias that uniquely resolves within the active universe under its match policy. |
| Weak Alias | A potentially useful but ambiguous alias that cannot independently authorize resolution or boosting. |
| Fact | A versioned statement about an entity or relationship, bound to evidence and validity. |
| Relationship | A directional typed fact connecting two entities within an optional product, region, and time scope. |
| KB Snapshot | An immutable, validated set of entities, aliases, facts, and relationship versions. |
| Entity Resolution | Extracting mentions from a query or article and mapping them to zero, one, or several candidate entity IDs. |
| Direct Attribution | Evidence that an article directly concerns the requested asset. |
| Indirect Exposure | Evidence that an article may affect the requested asset through a verified directed relationship. |
| KB Signal | A typed, explainable retrieval feature emitted by the KB consumer. |
| Signal Policy | Versioned rules that convert resolved facts into bounded strength, direction, and eligibility. |
| Ambiguous | More than one plausible entity remains or the alias is not sufficiently discriminative. |
| Stale | A fact has exceeded its fact-type freshness policy and has not been revalidated. |
| Curated | Reviewed by a human or imported from an approved deterministic source with validation. |
| Proposed | Extracted or imported but not yet eligible for active retrieval use. |
| Golden Set | Versioned queries, target assets, candidate articles, labels, and relevance types used for evaluation. |

## 9. End-to-End User Journeys

### 9.1 Direct Company Question

1. User asks: `微软最近的 Azure 需求有什么变化？`
2. Mention extraction identifies `微软` and `Azure`.
3. The resolver maps `微软` to the Microsoft entity through a reviewed Chinese alias.
4. `Azure` resolves to a product owned by Microsoft in the snapshot bound to the pinned active Final Release.
5. The resolver returns the target entity, confidence class, matched aliases, rejected alternatives, and snapshot ID.
6. Query expansion uses bounded approved English retrieval terms.
7. Candidate attribution emits direct signals separately for company, ticker, and product.
8. Retrieval combines semantic and KB features under a versioned signal policy.
9. The response exposes why each source received or did not receive KB support.

### 9.2 Ambiguous Alias

1. User asks about `Target` without a ticker or company context.
2. The resolver recognizes that the token can be a company name or ordinary noun.
3. The result is `ambiguous`; no company boost is applied.
4. The caller may use surrounding context, ask for clarification, or continue with semantic retrieval without an identity boost.
5. The resolution decision is recorded for evaluation.

### 9.3 Indirect Relationship Question

1. User asks how a supplier capacity event may affect NVIDIA.
2. NVIDIA resolves as the target company.
3. The KB retrieves only active, evidenced, in-scope relationships such as supplier, dependency, customer, competitor, or substitute.
4. The query expands one relationship hop within a deterministic term budget.
5. Candidate attribution records the exact relationship path and freshness.
6. Broad `AI infrastructure` theme overlap may be shown as contextual evidence but cannot independently produce a direct-company boost.

### 9.4 KB Refresh and Activation

1. A source adapter imports proposed entity facts into a staging snapshot.
2. Deterministic validators check required fields, alias collisions, generic terms, truncation, entity types, relationship direction, evidence, and freshness.
3. High-risk changes enter human review.
4. A retrieval impact report compares the proposed snapshot with the snapshot bound to the active Final Release on a fixed corpus and Golden Set.
5. The operator approves or rejects activation.
6. Activation atomically changes the active Final Release pointer.
7. Rollback restores the prior pointer without rewriting historical facts.

## 10. Functional Requirements

### FR-01 Universe and Asset Typing

The system shall represent at least these top-level asset types:

- `company`;
- `fund`;
- `index`.

The system shall not assign company-only fields such as CEO or owned product to funds and indexes.

Each asset shall have a stable `entity_id`, canonical name, primary symbol, aliases, status, and jurisdiction or market where applicable.

### FR-02 Source and Evidence Policy

Every proposed material fact shall identify:

- source URL or approved source identifier;
- publisher or source owner;
- source type;
- retrieved timestamp;
- source publication or as-of date when available;
- content hash or immutable source version when available;
- exact supporting text or structured source field;
- extraction method and version;
- reviewer and review timestamp when reviewed.

Recommended source priority:

1. regulator, exchange, filing, or issuer investor-relations material;
2. official company, product, or executive page;
3. approved structured reference source;
4. reputable secondary reporting;
5. general web extraction for proposal only.

An LLM may extract a proposed fact from supplied evidence but may not be recorded as the fact source.

### FR-03 Alias Contract

Every alias shall store:

- entity ID;
- raw and normalized alias;
- alias type;
- language;
- match policy;
- strength class;
- ambiguity class;
- valid-from and valid-to dates when applicable;
- evidence ID;
- review status;
- created and superseded timestamps.

Supported match policies for v1.0:

- exact ticker;
- exact normalized phrase;
- token-bounded phrase;
- curated abbreviation.

Unreviewed regex, fuzzy edit-distance, and unrestricted substring matching are out of scope for automatic boosting.

### FR-04 Alias Validation

Before activation, the validator shall reject or quarantine:

- empty aliases;
- normalized duplicates for different entities without an ambiguity policy;
- aliases ending in likely truncation;
- navigation and boilerplate tokens;
- generic corporate suffixes used alone;
- generic product candidates such as `Company`, `About`, `Inc`, `NYSE`, and country or region words without product context;
- ticker-like aliases that collide with ordinary high-frequency words unless explicitly reviewed;
- aliases without evidence or source policy;
- unsupported language or encoding values.

Validation output shall contain stable reason codes, not only prose errors.

### FR-05 Fact and Relationship Freshness

Every fact type shall have a versioned freshness policy. Recommended initial planning values:

| Fact type | Review interval | Stale behavior |
| --- | ---: | --- |
| ticker and canonical identity | 365 days | remain resolvable unless contradicted; flag for review |
| CEO or key executive | 30 days | no executive-based boost after expiry |
| company alias | 180 days | retain if reviewed; recheck collisions |
| product ownership | 90 days | suppress boost after expiry unless revalidated |
| business line | 180 days | contextual only after expiry |
| theme exposure | 90 days | contextual only after expiry |
| supplier/customer/dependency relationship | 90 days | suppress indirect boost after expiry |
| competitor/substitute relationship | 90 days | suppress indirect boost after expiry |

These intervals are planning inputs and must be tunable without schema changes. A stale fact remains auditable but cannot silently act as current.

The initial `freshness.v1` contribution semantics are locked:

```text
current = configured signal contribution
aging   = 0
stale   = 0
```

`aging` remains visible as an operational warning state, but it is not boost-eligible. A later policy may introduce a reduced aging contribution only under a new freshness-policy and signal-policy version with fixed-input evaluation.

### FR-06 Snapshot, Release Candidate, and Activation Lifecycle

KB snapshots shall use this state model:

```text
DRAFT
-> VALIDATING
-> VALIDATED
-> APPROVED

DRAFT | VALIDATING | VALIDATED
-> REJECTED
```

Release Candidates shall use this state model:

```text
DRAFT -> EVALUATED -> APPROVED
  |          |
  +----------+-> REJECTED
```

An approved Release Candidate creates one immutable Final Release. Final Releases do not carry mutable `ACTIVE` or `SUPERSEDED` lifecycle states. Active and superseded views are derived only from the namespace Active Pointer and append-only Activation Events.

Requirements:

- a snapshot is immutable after approval;
- one namespace has at most one active Final Release; the bootstrap state has no active release;
- retrieval records the pinned Final Release and its snapshot ID;
- validation failure prevents activation;
- a Final Release is created only after an authoritative evaluation run is passed or explicitly approved with exception;
- a Final Release immutably binds the snapshot checksum, policy versions and checksums, evaluation run and manifest hash, and schema version;
- activation and rollback update only the active pointer in one short transaction;
- a failed refresh cannot partially replace the active Final Release or its bound snapshot;
- historical snapshots and evidence remain readable.

### FR-07 Natural-Language Entity Resolution

The resolver shall accept a full user query rather than requiring the entire string to equal a ticker.

Resolution order for v1.0:

1. explicit exchange-qualified or unambiguous ticker;
2. exact reviewed strong alias;
3. exact canonical name;
4. reviewed product-to-owner context;
5. weak or ambiguous alias candidates.

The resolver shall return:

- extracted mention text and offsets;
- selected entity ID, if any;
- candidate entities;
- matched alias IDs and match policies;
- resolution status: `resolved`, `ambiguous`, or `unresolved`;
- reason codes;
- KB snapshot ID;
- resolver-policy version.

An ambiguous or unresolved result shall not authorize an automatic identity boost.

### FR-08 Direct Attribution

Direct article attribution may use:

- exact ticker mention;
- reviewed company alias;
- canonical company name;
- reviewed product ownership;
- current executive relationship with additional company context.

Executive names alone shall not prove company attribution when the person can be discussed outside the current company context.

The attribution result shall include accepted and rejected signals, not only a boolean match.

### FR-09 Directed Relationship Model

Entity KB v1.1 shall support at least:

- `has_ticker`;
- `has_executive`;
- `owns_product`;
- `operates_business_line`;
- `exposed_to_theme`;
- `supplies_to`;
- `customer_of`;
- `depends_on`;
- `competes_with`;
- `substitutes_for`;
- `member_of_index`;
- `held_by_fund`.

Every non-identity relationship shall declare:

- direction;
- subject and object entity IDs;
- optional product, business-line, region, and market scope;
- valid-from and valid-to;
- observation time;
- evidence ID;
- verification and freshness state.

Symmetric relationships such as `competes_with` shall declare symmetry explicitly. The storage layer shall not infer inverse semantics from naming alone.

### FR-10 Bounded Query Expansion

Query expansion shall be deterministic for the same query, snapshot, and policy version.

Default v1.0 limits:

- maximum one resolved target asset;
- maximum eight direct expansion terms;
- maximum four product terms;
- no relationship hop for direct intent;
- maximum one relationship hop for indirect intent in v1.1;
- maximum twelve total expansion terms for indirect intent.

The expansion result shall identify which terms were included, excluded, or truncated and why.

### FR-11 KB Signal Contract

The KB shall emit typed signals rather than a final relevance score.

Each signal shall include:

```text
signal_id
signal_type
target_entity_id
matched_entity_id
relationship_id (nullable)
alias_id (nullable)
strength_class
direction
freshness_state
ambiguity_state
evidence_id
kb_snapshot_id
signal_policy_version
eligible_for_boost
rejection_reason (nullable)
```

Initial signal classes:

- `direct_ticker`;
- `direct_company_alias`;
- `direct_product_owner`;
- `direct_executive_context`;
- `indirect_supplier`;
- `indirect_customer`;
- `indirect_dependency`;
- `indirect_competitor`;
- `indirect_substitute`;
- `context_business_line`;
- `context_theme`.

Broad business-line and theme signals shall be contextual by default and shall not independently establish direct attribution.

### FR-12 Signal-to-Score Policy

Retrieval orchestration owns numeric combination. The signal policy shall be configuration-versioned and evaluated independently from KB data.

For v1.0:

- only current, unambiguous, verified signals are boost-eligible;
- one fact shall not be counted repeatedly through duplicate aliases;
- signal contributions shall be capped by class and in total;
- rejected or stale signals contribute zero;
- theme-only evidence contributes zero direct boost;
- the returned result shall preserve the semantic score, every KB contribution, pre-rerank order, and post-rerank order;
- changing weights requires a retrieval evaluation run and policy-version increment.

The approved initial policy is `signals.price_in.v1`:

```text
weighted_semantic_score = 0.70 * semantic_score
combined_pre_rerank_score = weighted_semantic_score + kb_total_after_cap
```

The raw vector distance and unweighted semantic score remain visible. Direct and indirect contributions are intent-specific and are not added together for one candidate decision.

The policy uses these direct contributions:

| Direct signal | Contribution | Rule |
| --- | ---: | --- |
| `direct_ticker` | 0.10 | Only an unambiguous resolved ticker; shares the identity-class cap |
| `direct_company_alias` | 0.10 | Only a reviewed strong alias; shares the identity-class cap |
| `direct_product_owner` | 0.05 | Product must belong to the already resolved target |
| `direct_executive_context` | 0.02 | Current reviewed executive fact only |
| theme or business-line context | 0.00 | Context display only |

The identity-class cap is `0.10` and the total direct cap is `0.15`. Ticker and company-alias matches proving the same identity do not stack. Direct signals are meaningful ranking features but remain smaller than every verified indirect relationship contribution because direct company information is expected to be captured substantially by semantic retrieval and may already be reflected in market price.

The approved v1.1 one-hop indirect policy uses these contributions:

| Indirect signal | Contribution |
| --- | ---: |
| `indirect_dependency` | 0.30 |
| `indirect_supplier` | 0.27 |
| `indirect_customer` | 0.23 |
| `indirect_substitute` | 0.20 |
| `indirect_competitor` | 0.18 |
| theme or business-line context without a verified relationship | 0.00 |

For one article and requested target, v1.1 uses only the strongest eligible verified relationship contribution and applies an indirect cap of `0.30`. Eligible indirect signals are ordered by contribution descending, then by `relationship_id` ascending. Only the first signal remains boost-eligible; every other otherwise-valid indirect signal contributes zero with `LOWER_PRIORITY_RELATIONSHIP`. Multiple paths, aliases, or relationship labels therefore do not stack. The smallest indirect contribution, `0.18`, remains greater than the maximum total direct contribution, `0.15`.

The legacy direct cap `0.35`, indirect cap `0.20`, and alias-count bonuses remain an evaluation comparison arm only; they are not the v2 defaults. The approved values above are still subject to the refreshed fixed-corpus activation gates and do not become production truth merely because they are documented.

Weight selection shall use a versioned tuning split that is separate from the final activation holdout. Canonical or duplicate stories, revisions of the same story, and equivalent hard negatives shall not cross the split. Hard data-quality, ambiguity, no-hidden-regression, and explainability gates are applied before aggregate metric optimization; weights shall not be chosen by a single aggregate score that can hide an asset regression.

### FR-13 Explainability

For every candidate article, the retrieval result shall be able to answer:

1. Which target entity was resolved?
2. Which aliases or relationships matched?
3. Which signals were accepted, rejected, stale, or ambiguous?
4. What evidence supports each active KB fact?
5. How much did each signal contribute?
6. What were the raw semantic score, semantic weight, and weighted semantic score before KB signals?
7. What was the combined pre-rerank score?
8. What was the LLM rerank position and reason?
9. Which KB snapshot and policies produced the decision?

Debug detail may be hidden from the default end-user view but must be available through a structured contract.

### FR-14 Human Review and Correction

The system shall support review actions:

- approve proposed fact;
- reject proposed fact with reason;
- add or remove alias;
- mark alias ambiguous;
- supersede stale relationship;
- merge duplicate entities;
- split incorrectly merged entities;
- override source priority with justification;
- mark a retrieval signal as useful, missing, or incorrect.

Corrections shall be append-only decisions that produce a new proposed snapshot. They shall not directly mutate an approved snapshot.

### FR-15 Evaluation

Every proposed snapshot or signal-policy change shall be evaluated with:

- fixed article corpus generation or manifest;
- fixed KB snapshot candidate;
- fixed active-snapshot baseline;
- fixed Golden Set version;
- fixed embedding, candidate-pool, and reranker configuration;
- direct and indirect relevance labels;
- per-asset and aggregate metrics;
- resolution metrics;
- data-quality metrics;
- a machine-readable diff report.

The evaluation shall distinguish:

- entity resolution failure;
- query expansion failure;
- candidate recall failure;
- KB attribution failure;
- score-combination failure;
- reranker failure.

### FR-16 Entry-Point Consistency

All production retrieval entry points that claim company-aware behavior shall consume the same resolver, pinned Final Release and snapshot, signal policy, and explanation contract.

An entry point may explicitly choose semantic-only retrieval, but it shall report `kb_mode=disabled` rather than silently bypassing the KB.

### FR-17 Failure Behavior

The system shall fail closed for KB-specific uncertainty:

- unavailable KB: continue only in explicitly reported semantic-only mode if the caller permits it;
- ambiguous target: no identity boost;
- invalid or stale fact: no contribution;
- snapshot mismatch: stop company-aware retrieval;
- corrupt evidence link: quarantine the fact;
- evaluation regression: block automatic activation pending review.

### FR-18 Committed Graph-Database Evolution

The v1.0-v1.2 contracts shall keep storage details behind repository and application-service boundaries. Stable entity, relationship, evidence, snapshot, release, direction, scope, validity, and provenance semantics shall not depend on SQLite row IDs or SQL-specific response fields.

A later major release shall introduce a graph database when the product enters multi-hop relationship intelligence. That release shall:

- support bounded paths of at least two verified relationship hops;
- preserve immutable snapshot, release pinning, evidence, freshness, ambiguity, audit, activation, and rollback semantics;
- select exactly one authoritative fact owner, or define a rebuildable graph projection with checksum and readiness gates; SQLite and the graph database shall not become competing editable authorities;
- provide fixed-input path-retrieval and per-relationship Golden Sets before cutover;
- use a superseding ADR to select the graph technology, authority model, consistency boundary, deployment topology, and migration plan.

Entity KB v1.0-v1.2 shall not introduce graph-specific fields into public retrieval responses merely to anticipate that migration.

## 11. Data Contract

### 11.1 `kb_snapshots`

| Field | Type | Required | Producer | Consumer | Meaning |
| --- | --- | --- | --- | --- | --- |
| `snapshot_id` | string | yes | snapshot builder | all KB consumers | Immutable version ID |
| `namespace` | string | yes | configuration | activation controller | KB namespace |
| `status` | enum | yes | controller | resolver, operator | Lifecycle state |
| `parent_snapshot_id` | string | no | builder | diff tooling | Prior snapshot |
| `source_manifest_hash` | string | yes | builder | validator | Input identity |
| `schema_version` | string | yes | migration layer | readers | Data-contract version |
| `created_at` | UTC timestamp | yes | builder | audit | Creation time |
| `validated_at` | UTC timestamp | no | validator | activation gate | Validation completion |
| `approved_at` | UTC timestamp | no | reviewer | audit | Approval time |
| `approved_by` | string | no | reviewer | audit | Approval identity |
| `rejected_at` | UTC timestamp | no | reviewer/controller | audit | Rejection time |
| `rejection_reason` | string | no | reviewer/controller | audit | Stable rejection explanation |

Snapshot state never records `ACTIVE` or `SUPERSEDED`; activation time is recorded by `kb_activation_events`.

### 11.2 `kb_entities`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `entity_id` | string | yes | Stable entity ID |
| `entity_type` | enum | yes | company, fund, index, person, product, business_line, theme |
| `canonical_name` | string | yes | Reviewed canonical name |
| `canonical_evidence_id` | string | yes | Evidence for the canonical identity |
| `status` | enum | yes | proposed, active, superseded, rejected |
| `jurisdiction` | string | conditional | Legal or market scope |
| `valid_from` | date | no | Validity start |
| `valid_to` | date | no | Validity end |
| `snapshot_id` | string | yes | Owning snapshot |

### 11.3 `kb_aliases`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `alias_id` | string | yes | Stable alias fact ID |
| `entity_id` | string | yes | Target entity |
| `alias` | string | yes | Display form |
| `normalized_alias` | string | yes | Match form |
| `alias_type` | enum | yes | ticker, legal_name, common_name, abbreviation, former_name, product_name |
| `language` | BCP-47 string | yes | Alias language |
| `match_policy` | enum | yes | exact_ticker, exact_phrase, token_phrase, curated_abbreviation |
| `strength_class` | enum | yes | strong or weak |
| `ambiguity_class` | enum | yes | unique, contextual, ambiguous, prohibited |
| `evidence_id` | string | yes | Supporting evidence |
| `review_status` | enum | yes | proposed, verified, rejected |
| `valid_from` / `valid_to` | date | no | Alias validity |
| `snapshot_id` | string | yes | Owning snapshot |

### 11.4 `kb_relationships`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `relationship_id` | string | yes | Stable fact ID |
| `subject_entity_id` | string | yes | Directed source |
| `relation_type` | enum | yes | Typed relationship |
| `object_entity_id` | string | yes | Directed target |
| `scope_product_id` | string | no | Product scope |
| `scope_region` | string | no | Region scope |
| `evidence_id` | string | yes | Supporting evidence |
| `observed_at` | UTC timestamp | yes | Observation time |
| `valid_from` / `valid_to` | date | no | Business validity |
| `verification_status` | enum | yes | proposed, verified, rejected |
| `snapshot_id` | string | yes | Owning snapshot |

`freshness_state` is derived at resolution or retrieval time from the fact type, observation time, validity interval, and freshness-policy version. It is not an immutable source-of-truth field on the relationship.

### 11.5 `kb_evidence`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `evidence_id` | string | yes | Evidence record ID |
| `source_type` | enum | yes | regulator, filing, issuer, structured_reference, news, web |
| `publisher` | string | yes | Source owner |
| `source_url` | string | conditional | Canonical source URL |
| `source_record_id` | string | conditional | External immutable ID |
| `published_at` | UTC timestamp | no | Publication time |
| `data_as_of` | UTC timestamp | no | Fact effective time |
| `retrieved_at` | UTC timestamp | yes | Fetch time |
| `content_sha256` | string | conditional | Source content version |
| `supporting_text` | string | yes | Exact source field or quote |
| `locator` | JSON | no | Page, section, selector, or text offsets |
| `extraction_method` | string | yes | Parser or model version |

### 11.6 `kb_resolution_decisions`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `decision_id` | string | yes | Resolution audit ID |
| `query_hash` | string | yes | Privacy-aware query identity |
| `mention_text` | string | yes | Extracted mention |
| `mention_start` / `mention_end` | integer | yes | Query offsets |
| `candidate_entity_ids` | array | yes | Considered entities |
| `selected_entity_id` | string | no | Chosen entity |
| `status` | enum | yes | resolved, ambiguous, unresolved |
| `matched_alias_ids` | array | yes | Alias evidence |
| `reason_codes` | array | yes | Deterministic explanation |
| `kb_snapshot_id` | string | yes | Data version |
| `resolver_policy_version` | string | yes | Logic version |

### 11.7 `kb_release_candidates`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `candidate_id` | string | yes | Mutable pre-release workflow identity |
| `namespace` | string | yes | KB namespace |
| `snapshot_id` | string | yes | Approved snapshot under evaluation |
| policy versions and checksums | strings | yes | Exact resolver, freshness, and signal policy inputs |
| `evaluation_run_id` | string | no | Authoritative completed evaluation once attached |
| `status` | enum | yes | draft, evaluated, approved, rejected |
| review fields | strings/timestamps | conditional | Evaluation and approval audit |

### 11.8 `kb_evaluation_runs`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `evaluation_run_id` | string | yes | Authoritative evaluation identity |
| `candidate_id` | string | yes | Candidate evaluated |
| `evaluation_manifest_hash` | string | yes | Hash of corpus, Golden Set, split, model, policy, and runtime inputs |
| `result` | enum | yes | passed, failed, approved_with_exception |
| `report_uri` / `report_checksum` | strings | yes | Immutable result artifact and integrity value |
| approval fields | strings/timestamps | conditional | Required for approved exception |

### 11.9 `kb_releases`

Final Release rows are immutable and are created only by approving an evaluated Release Candidate.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `release_id` | string | yes | Hash of all immutable release inputs |
| `namespace` | string | yes | KB namespace |
| `snapshot_id` / `snapshot_checksum` | strings | yes | Approved data identity and integrity |
| policy versions and checksums | strings | yes | Exact resolver, freshness, and signal policy inputs |
| `evaluation_run_id` / `evaluation_manifest_hash` | strings | yes | Approved evaluation identity and fixed-input manifest |
| `schema_version` | string | yes | Reader compatibility boundary |
| `id_algorithm_version` | string | yes | Canonical serialization and release-ID algorithm |
| `created_at` / `created_by` | timestamp/string | yes | Finalization audit |

### 11.10 `kb_active_state` and `kb_activation_events`

Each namespace is seeded with `active_release_id = null` and `lock_version = 0`. Activation and rollback compare the expected nullable release ID and lock version, update only this pointer, and append an event. Active/Superseded is a derived view over the pointer and event history, never a mutable Snapshot or Final Release state.

### 11.11 `retrieval_kb_signals`

This may be persisted for evaluations and debug runs or emitted transiently in online retrieval. It shall follow the FR-11 signal contract and include the candidate article ID, accepted numeric contribution, and rejection reason.

## 12. Ownership and System Boundaries

| Component | Owns | Does not own |
| --- | --- | --- |
| Source adapters | Fetch source fields and evidence | Fact activation or retrieval weights |
| KB snapshot builder | Staging records and deterministic IDs | Human approval |
| KB validator | Schema, quality, collision, provenance, and freshness gates | Retrieval ranking |
| Human review | High-risk fact decisions and overrides | Direct database mutation of approved snapshots |
| KB store | Versioned entities, facts, evidence, active pointer | Semantic similarity |
| Entity resolver | Mention extraction and entity decisions | Article relevance ranking |
| Attribution engine | Candidate-to-target KB signals | Final blended score |
| Retrieval orchestration | Candidate retrieval, signal combination, rerank handoff | KB fact creation |
| Reranker | Reorder supplied candidates | Entity truth, KB mutation, candidate invention |
| Evaluation pipeline | Fixed-input comparisons and activation evidence | Automatic approval |

## 13. Metrics and Acceptance Criteria

### 13.1 Data Quality Gates

For Tier A activation:

- 100% of assets have correct type, canonical name, primary symbol, and at least one reviewed common alias;
- 100% of Tier A companies have reviewed English aliases;
- 100% of Tier A companies have at least one reviewed Chinese alias for supported Chinese query resolution;
- 100% of activated material facts have evidence, observation time, verification status, and snapshot ID;
- 0 prohibited generic product aliases;
- 0 unexplained truncated aliases;
- 0 unresolved strong-alias collisions;
- 0 stale facts contributing an automatic boost;
- 100% of snapshot validation failures carry stable reason codes.

For Tier B activation:

- the same integrity gates apply;
- Chinese aliases may remain optional until the asset is promoted to Tier A.

### 13.2 Resolver Gates

On a versioned Tier A resolver Golden Set:

- precision among automatically resolved queries >= 0.98;
- recall for explicit ticker and reviewed company-name queries >= 0.95;
- false automatic resolution for labeled ambiguous queries = 0;
- 100% of decisions return a status, reason codes, snapshot ID, and policy version;
- English and Chinese results are reported separately.

### 13.3 Retrieval Gates

Before setting numeric gates, the team shall rerun the baseline on a verified, fixed corpus and active index generation. The historical May report is context only.

Recommended activation gates against that refreshed baseline:

- Tier A macro precision@K does not regress by more than 0.01 absolute;
- Tier A macro recall@K improves by at least 10% relative for the target relevance type, or an equivalent improvement is approved before implementation;
- false-positive rate decreases by at least 5% relative;
- no Tier A asset loses a relevant top-K hit without an adjudicated annotation or documented tradeoff;
- direct and indirect relevance are evaluated separately;
- semantic-only, current-KB, and proposed-KB chains use identical corpus and reranker inputs;
- all unlabeled returned documents are reported and are not silently counted as irrelevant;
- 100% of KB-influenced results expose score components and provenance.

These approved relative gates become binding against the refreshed baseline. The baseline values themselves must be measured on the verified corpus before implementation can claim that the gates pass. The no-hidden-regression and explainability gates are mandatory.

### 13.4 Operational Gates

- activation and rollback complete through an atomic pointer update;
- a failed build or validation leaves the active Final Release unchanged;
- resolver and retrieval startup detect schema or snapshot mismatches and fail closed for KB mode;
- an operator can produce a snapshot diff and retrieval-impact report before approval;
- no live external source is required to serve an already active Final Release;
- Tier A freshness violations are visible before retrieval use.

## 14. Evaluation Design

### 14.1 Resolver Golden Set

The resolver set shall include:

- explicit tickers;
- legal and common company names;
- Chinese company names;
- product-led questions;
- renamed companies and historical aliases;
- ordinary-word collisions such as `Target`;
- multiple companies in one query;
- unsupported assets;
- misspellings for observation only, not automatic fuzzy resolution in v1.0.

### 14.2 Retrieval Golden Set

Each Tier A asset shall include:

- direct relevant articles;
- indirect relevant articles with labeled relationship paths;
- hard negatives sharing products or themes;
- generic market background;
- duplicate stories;
- stale relationship cases;
- ambiguous alias cases;
- counterexamples where KB boost should be zero.

The retrieval set shall publish a tuning/activation-holdout split. Story groups, near-duplicate publisher copies, and revisions of the same underlying event belong to one side only. The activation holdout remains untouched while candidate weights are selected; opening it for error analysis creates a new annotation version and requires a new untouched holdout before an activation claim.

### 14.3 Required Experiment Arms

1. semantic-only baseline;
2. current KB and current signal policy;
3. proposed KB with current signal policy;
4. proposed KB with proposed signal policy;
5. optional reranker-disabled diagnostic arm.

This separation identifies whether a change came from data, signal rules, or the reranker.

### 14.4 Report Contract

Every report shall record:

- Git SHA and dirty-state indicator;
- corpus/index generation ID;
- article manifest hash;
- annotation version;
- tuning/activation-holdout split manifest and story-group leakage check;
- KB snapshot IDs;
- resolver and signal-policy versions;
- embedding and reranker configuration;
- candidate and answer top-K values;
- per-asset and aggregate metrics;
- newly relevant hits, lost relevant hits, false positives, and unlabeled returns;
- a human adjudication queue for critical regressions.

## 15. Release Plan

### Phase 0: Baseline and Contract Freeze

Deliverables:

- refreshed read-only inventory of entities, aliases, facts, collisions, and freshness;
- fixed Tier A universe;
- resolver and retrieval Golden Set versions;
- refreshed baseline after the corpus integrity work reaches a verified index snapshot;
- approved data and signal contracts.

Exit gate:

- inputs are versioned and the baseline is reproducible.

### Phase 1: Entity KB v1.0 Identity Foundation

Deliverables:

- snapshot tables and active pointer;
- evidence-backed alias and fact records;
- validators and stable reason codes;
- curated Tier A English and Chinese aliases;
- natural-language entity resolver;
- typed direct-attribution signals;
- score-component explanation contract;
- consistent KB mode across retrieval entry points;
- migration tooling with dry-run, apply, verify, and rollback.

Exit gate:

- Tier A data, resolver, retrieval, and operational acceptance criteria pass.

### Phase 2: Entity KB v1.1 Directed Relationships

Deliverables:

- supplier, customer, dependency, competitor, and substitute relationships;
- product, region, and time scope;
- one-hop indirect expansion;
- indirect signal policy;
- indirect-relevance Golden Set and per-relationship diagnostics.

Exit gate:

- indirect retrieval improves without direct-attribution regression and every relationship-influenced result is provenance-backed.

### Phase 3: Entity KB v1.2 Coverage and Refresh

Deliverables:

- Tier B promotion workflow;
- scheduled proposal refresh with source-specific budgets;
- stale-fact review queue;
- impact-based re-evaluation of affected assets;
- coverage and freshness dashboard or report.

Exit gate:

- new assets can be promoted without bypassing data and retrieval quality gates.

### Phase 4: Entity KB v2.0 Graph Relationship Intelligence

Entry gate:

- the v1.1 one-hop relationship corpus passes provenance, freshness, direct-versus-indirect, and per-asset retrieval gates;
- approved user journeys require bounded multi-hop paths or graph analysis that the one-hop repository contract cannot express cleanly;
- a superseding graph-storage ADR and fixed-input graph Golden Set are approved.

Deliverables:

- a production graph database selected by the superseding ADR;
- versioned graph import or projection keyed by immutable KB snapshot and release IDs;
- bounded multi-hop path retrieval with evidence on every edge;
- graph readiness, checksum, activation, reconciliation, rollback, backup, and restore procedures;
- parity comparison against the approved one-hop behavior before graph cutover.

Exit gate:

- graph-backed multi-hop retrieval passes path correctness, provenance, per-asset quality, performance, failure, and rollback gates without creating dual fact authority.

## 16. Migration and Rollback

1. Treat the current database and YAML as source material, not automatically trusted active v2 data.
2. Export current entities, aliases, and relationships into a proposed v2 snapshot.
3. Preserve legacy IDs through an explicit mapping table.
4. Run validators and quarantine invalid records instead of deleting them.
5. Curate Tier A defects and evidence before activation.
6. Build the resolver and retrieval evaluation against the proposed snapshot.
7. Activate v2 through a new pointer only after approval.
8. Keep the current reader available during a defined compatibility window.
9. Roll back by switching the active pointer to the last verified snapshot.
10. Do not delete the current DB, YAML, or historical sync records as part of activation.

## 17. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| More aliases increase false matches | Incorrect company boost | Strong/weak classes, collision validation, ambiguous=no boost |
| Automated extraction creates noisy products | False positives and unstable expansion | Proposal-only extraction, generic-token denylist, evidence and review gates |
| Relationship graph grows without proof | Indirect retrieval becomes thematic guessing | Directed typed relations, source evidence, one-hop budget |
| Stale executive or ownership facts remain active | Wrong attribution | Fact-type freshness policy and stale suppression |
| Aggregate metrics hide regressions | Some assets become materially worse | Per-asset activation gates and adjudication queue |
| KB data and weights change together | Root cause cannot be identified | Separate snapshot ID and signal-policy version; experiment arms |
| Corpus changes invalidate comparisons | False quality claims | Fixed corpus/index generation and manifest hash |
| Human review becomes a bottleneck | Slow expansion | Tiered universe and risk-based review |
| Chinese aliases resolve but embedding remains English-focused | Query resolution improves but semantic retrieval may remain weak | Resolve to entity, expand into approved English terms, report language metrics |
| Entry points bypass the KB | Inconsistent product behavior | Explicit `kb_mode`, shared resolver/signal seam, contract tests |
| SQLite-specific IDs or SQL fields leak into product contracts | Future graph migration changes semantics or breaks clients | Stable domain IDs, storage-neutral repository contracts, contract tests |
| SQLite and the future graph store both become editable authorities | Divergent facts and mixed-release paths | One authoritative owner or immutable rebuildable projection, checksum/readiness gate, superseding ADR |

## 18. Dependencies

- The corpus integrity and retrieval-eligibility remediation must provide a verified, fixed article/index snapshot before final retrieval acceptance metrics are claimed.
- Existing SQLite entity data and sync code provide migration inputs but do not define the v2 trust boundary.
- Retrieval orchestration must accept structured KB signals and expose score components.
- The evaluation pipeline must support fixed manifests, direct/indirect labels, and per-asset reports.
- Runtime behavior changes shall follow the repository's strict TDD workflow.

Data curation, schema design, resolver Golden Set creation, and migration dry runs may proceed in parallel with corpus remediation. Activation-quality claims may not.

## 19. Approved Product Decisions

PRD approval adopts these defaults for downstream design:

1. Tier A is the 16-asset historical evaluation universe defined in Section 7.1.
2. Every Tier A company requires at least one reviewed Chinese alias in v1.0.
3. The refreshed fixed-corpus baseline is authoritative. The default activation target is at least 10% relative macro recall improvement for the target relevance type, no more than 0.01 absolute macro precision regression, at least 5% relative false-positive-rate improvement, and no unadjudicated lost relevant Tier A hit.
4. A reviewed product mention may strengthen an already resolved company and may produce owner candidates, but it does not independently auto-select a company target in v1.0.
5. v1.1 requires supplier, customer, dependency, competitor, and substitute relationships. Platform/ecosystem relations are deferred unless a later PRD revision adds them.
6. Approved deterministic regulator or exchange identity fields may become verified after deterministic validation. Facts extracted from unstructured pages require human review before activation.
7. The Section 10 freshness intervals are the initial versioned policy. Changes require a policy-version increment and affected evaluation.
8. General grounded queries default to visible `preferred` degradation; the dedicated company-aware supporting-article contract defaults to `required`; callers may explicitly select `disabled` where allowed.
9. SQLite is required for v1.0-v1.2, and a graph database is required for the later major multi-hop relationship-intelligence stage. The graph vendor and authority/cutover model remain decisions for a superseding ADR; the current contracts must remain storage-neutral.
10. The locked initial `signals.price_in.v1` policy uses `semantic_weight=0.70`, direct cap `0.15`, and one strongest verified indirect contribution from `0.18` to `0.30` capped at `0.30`. Direct and indirect contributions are intent-specific and do not accumulate together. Theme/business-line context contributes zero without a verified relationship. Changing any coefficient, contribution, cap, stacking rule, or combination formula requires a new signal-policy version and fixed-input evaluation.

Changing one of these decisions requires a PRD revision and updated acceptance examples before the affected implementation contract is frozen.

## 20. Acceptance Scenarios

### AC-01 Natural-Language Ticker Resolution

Given the snapshot bound to the Tier A active Final Release contains reviewed aliases for Microsoft,
when the user asks `What changed for MSFT?`,
then the resolver selects the Microsoft entity,
records the matched ticker alias and offsets,
and returns the pinned Final Release, snapshot, and policy versions.

### AC-02 Chinese Alias Resolution

Given `微软` is a reviewed Chinese alias for Microsoft,
when the user asks `微软最近的云业务有什么变化？`,
then the resolver selects Microsoft,
expands into approved English company and product terms,
and does not pass the untranslated whole query as the only semantic retrieval input.

### AC-03 Ambiguous Ordinary Word

Given `Target` is labeled contextual or ambiguous,
when the query lacks company or ticker context,
then the resolver does not automatically apply a TGT identity boost,
and returns an ambiguous decision with candidate entities and reason codes.

### AC-04 Generic Product Rejection

Given a source extractor proposes `Company`, `Inc`, and `NYSE` as product names,
when snapshot validation runs,
then the facts are quarantined with stable quality reason codes,
and none can appear in active query expansion or candidate scoring.

### AC-05 Stale Executive

Given an executive relationship exceeds its freshness policy,
when an article mentions only that person,
then the executive signal is marked stale and contributes zero boost,
while the historical fact remains auditable.

### AC-06 Direct Versus Theme Signal

Given an article mentions `AI infrastructure` but no reviewed NVIDIA identity, product, or directed relationship,
when direct attribution runs for NVDA,
then the theme may be returned as context,
but direct company attribution remains false and direct KB boost remains zero.

### AC-07 Directed Supplier Signal

Given an active, current, evidenced supplier relationship connects Company A to an NVIDIA-scoped product,
when indirect retrieval runs for NVDA,
then the result includes the relationship path, evidence ID, freshness, signal contribution, and policy version.

### AC-08 Snapshot Failure Isolation

Given a proposed snapshot contains an unresolved strong-alias collision,
when validation completes,
then activation is blocked,
the prior active Final Release remains unchanged,
and ordinary retrieval continues using the prior snapshot.

### AC-09 Score Explanation

Given a candidate's order changes because of KB signals,
when debug or evaluation output is requested,
then the response contains the semantic score, each accepted and rejected KB signal, each contribution, the pre-rerank score, rerank position, snapshot ID, and policy versions.

### AC-10 Per-Asset Regression Gate

Given aggregate precision and recall improve but one Tier A asset loses a relevant top-K result,
when activation review runs,
then the regression is explicitly reported and requires annotation adjudication or product approval before activation.

### AC-11 Price-In-Aware Weight Distribution

Given one candidate has a verified direct company or product signal and another has a verified one-hop dependency, supplier, customer, substitute, or competitor relationship to the requested target,
when the initial signal policy computes contributions,
then the semantic score is multiplied by `0.70`,
the direct candidate receives at most `0.15`,
the indirect candidate receives the strongest applicable relationship contribution from `0.18` to `0.30`,
multiple aliases or paths do not stack,
and no additional semantic-score hard gate is applied before that contribution is calculated.

## 21. Definition of Done

Entity KB v1.0 is complete only when:

1. the approved data contract and migrations exist;
2. Tier A data passes all quality and freshness gates;
3. the natural-language resolver passes English, Chinese, and ambiguity tests;
4. active retrieval entry points use or explicitly disable the same KB seam;
5. typed signals and full score explanations are available;
6. the proposed snapshot passes refreshed per-asset retrieval acceptance;
7. activation and rollback are verified without altering historical snapshots;
8. focused tests, work-package regression, static checks, migration dry-run, and known proof limits are recorded;
9. documentation identifies the active Final Release, its snapshot, policy versions, and operator workflow;
10. no claim is made about production retrieval quality without a deployed version and production evidence.
