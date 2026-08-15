# Entity KB v2 API and Data Contract

## Document Control

| Field | Value |
| --- | --- |
| Status | Approved v1.0 |
| Date | 2026-08-15 |
| Product | Financial Agent |
| API family | `entity-kb.v2` |
| Transport | Repo-native Python service with MCP and CLI adapters |
| Product contract | [Entity KB Upgrade PRD](../product/entity-kb-upgrade-prd.md) |
| System design | [Entity KB v2 System Design](../architecture/entity-kb-v2-system-design.md) |
| Governing ADR | [ADR-0001](../adr/0001-entity-kb-v2-snapshots-and-signal-boundary.md) |

## 1. Purpose and Proof Boundary

This document defines the planned client/service, service/repository, and MCP response contracts for Entity KB v2. It covers:

- entity resolution;
- entity profile reads;
- KB-aware supporting-article retrieval;
- grounded news query integration;
- common KB context, typed signals, score explanation, errors, and degradation;
- offline administrative command contracts.

It does not define an HTTP API. It does not prove that any function or MCP tool is implemented, reachable, authorized, deployed, or backed by an active v2 release.

## 2. Compatibility Strategy

Existing tools remain unchanged during the compatibility window:

- `get_company_profile`;
- `retrieve_supporting_articles`;
- `query_news_research`.

Entity KB v2 is introduced through new versioned tools and the shared Python service:

- `resolve_financial_entities_v2`;
- `get_entity_profile_v2`;
- `retrieve_supporting_articles_v2`;
- `query_news_research_v2`.

The v1 names shall not silently start returning a breaking v2 envelope. After migration and explicit client review, a future major release may retire or redirect v1.

The v2 contract is storage-neutral. Clients shall not receive SQLite row IDs, SQL query details, graph-native internal IDs, or database-specific consistency tokens. The committed future graph-database stage shall preserve these domain IDs and response semantics; graph-specific path fields require an additive contract review or a new major API version according to their semantics.

## 3. Common Conventions

### 3.1 Serialization

- JSON field names use `snake_case`.
- Timestamps use RFC 3339 UTC with a `Z` suffix.
- Calendar dates use `YYYY-MM-DD`.
- IDs are opaque strings and must not be parsed by clients.
- Missing optional values are `null`, not empty strings.
- Empty collections are `[]`.
- Floats are finite JSON numbers; `NaN` and infinity are invalid.
- Unknown enum values are rejected at the service boundary.
- Candidate and signal arrays preserve deterministic service order.

### 3.2 Version Fields

Every v2 response contains:

```json
{
  "api_version": "entity-kb.v2",
  "request_id": "req_01...",
  "status": "success"
}
```

`api_version` describes the transport contract. KB snapshot and policy versions are separate fields under `kb_context`.

### 3.3 Response Status

| Status | Meaning |
| --- | --- |
| `success` | The requested contract completed without semantic degradation. |
| `degraded` | The request completed under an explicitly allowed fallback and reports why. |
| `error` | The requested contract could not be satisfied; `data` is null. |

An unresolved entity can be a successful output of the resolver API. It becomes a degraded or error state only when a downstream retrieval request requires a resolved target.

### 3.4 Common Envelope

```json
{
  "api_version": "entity-kb.v2",
  "request_id": "req_01...",
  "status": "success",
  "data": {},
  "warnings": [],
  "error": null
}
```

Error envelope:

```json
{
  "api_version": "entity-kb.v2",
  "request_id": "req_01...",
  "status": "error",
  "data": null,
  "warnings": [],
  "error": {
    "code": "ENTITY_AMBIGUOUS",
    "message": "The request requires one resolved target, but multiple candidates remain.",
    "retryable": false,
    "details": {
      "candidate_entity_ids": ["entity_1", "entity_2"]
    }
  }
}
```

### 3.5 KB Mode

| Value | Meaning |
| --- | --- |
| `required` | A compatible release and resolved target are mandatory. No semantic-only fallback. |
| `preferred` | Apply KB when possible; otherwise return a visible semantic-only degradation. |
| `disabled` | Do not read or apply the KB. |

Defaults:

- `resolve_financial_entities_v2`: KB is inherently required;
- `get_entity_profile_v2`: KB is required;
- `retrieve_supporting_articles_v2`: `required`;
- `query_news_research_v2`: `preferred`.

### 3.6 `as_of`

`as_of` controls fact validity and freshness. If omitted, the service captures one UTC request timestamp and reuses it through the request. Callers must not observe different `as_of` values across resolution and attribution.

## 4. Common Data Types

### 4.1 `KBContext`

```json
{
  "requested_mode": "required",
  "applied_mode": "applied",
  "degradation_reason": null,
  "namespace": "financial-agent",
  "release_id": "kbr_01...",
  "snapshot_id": "kbs_01...",
  "schema_version": "2.0",
  "resolver_policy_version": "resolver.v1",
  "freshness_policy_version": "freshness.v1",
  "signal_policy_version": "signals.price_in.v1",
  "evaluation_run_id": "kbeval_01...",
  "as_of": "2026-08-15T08:00:00Z"
}
```

Field rules:

| Field | Required | Semantics |
| --- | --- | --- |
| `requested_mode` | yes | Caller input |
| `applied_mode` | yes | `applied`, `semantic_only`, `not_applicable`, or `error` |
| `degradation_reason` | yes, nullable | Non-null exactly when `applied_mode=semantic_only` due to degradation; otherwise null |
| `namespace` | yes, nullable | Requested namespace; null only when KB is disabled before namespace selection |
| `release_id`, `snapshot_id`, `schema_version` | yes, nullable | All non-null when a release was pinned; all null in disabled or unavailable semantic-only mode |
| policy version fields, `evaluation_run_id` | yes, nullable | All non-null when a release was pinned; all null in disabled or unavailable semantic-only mode |
| `as_of` | yes | Single request evaluation time |

### 4.2 `EntitySummary`

```json
{
  "entity_id": "entity:company:msft",
  "entity_type": "company",
  "canonical_name": "Microsoft Corporation",
  "primary_symbol": "MSFT",
  "jurisdiction": "US",
  "status": "active"
}
```

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `entity_id` | yes | Non-null stable domain ID from the pinned snapshot repository |
| `entity_type` | yes | Non-null repository enum |
| `canonical_name` | yes | Non-null reviewed name from the pinned snapshot |
| `primary_symbol` | yes, nullable | Snapshot asset mapper; null for non-traded entities or entities without one approved primary symbol |
| `jurisdiction` | yes, nullable | Repository; null when not applicable or not established by approved evidence |
| `status` | yes | Non-null entity status from the pinned snapshot |

### 4.3 `AliasMatch`

```json
{
  "alias_id": "alias_01...",
  "entity_id": "entity:company:msft",
  "mention_text": "微软",
  "mention_start": 0,
  "mention_end": 2,
  "alias_type": "common_name",
  "language": "zh-CN",
  "match_policy": "exact_phrase",
  "strength_class": "strong",
  "ambiguity_class": "unique"
}
```

Offsets are zero-based Unicode code-point offsets with an exclusive end.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `alias_id`, `entity_id` | yes | Non-null alias fact and target IDs from the pinned snapshot |
| `mention_text` | yes | Non-null exact query slice produced by mention extraction |
| `mention_start`, `mention_end` | yes | Non-null resolver offsets; `0 <= start < end` |
| `alias_type`, `language`, `match_policy` | yes | Non-null alias metadata from the matched fact |
| `strength_class`, `ambiguity_class` | yes | Non-null resolver inputs from the matched fact |

### 4.4 `ResolutionDecision`

```json
{
  "decision_id": "kbres_01...",
  "status": "resolved",
  "selected_entity": {
    "entity_id": "entity:company:msft",
    "entity_type": "company",
    "canonical_name": "Microsoft Corporation",
    "primary_symbol": "MSFT",
    "jurisdiction": "US",
    "status": "active"
  },
  "candidate_entities": [],
  "alias_matches": [],
  "reason_codes": ["EXACT_TICKER"]
}
```

Rules:

- `resolved`: `selected_entity` is non-null;
- `ambiguous`: `selected_entity` is null and `candidate_entities` has at least two entries;
- `unresolved`: `selected_entity` is null; candidates may be empty or weak suggestions;
- `reason_codes` is non-empty.

### 4.5 `QueryExpansion`

```json
{
  "original_query": "微软最近的 Azure 需求有什么变化？",
  "effective_query": "MSFT Microsoft Azure cloud infrastructure",
  "retrieval_intent": "direct",
  "included_terms": [
    {
      "term": "MSFT",
      "source_type": "primary_symbol",
      "source_id": "entity:company:msft",
      "priority": 1
    }
  ],
  "excluded_terms": [
    {
      "term": "Dynamics 365",
      "reason_code": "TERM_BUDGET_EXCEEDED"
    }
  ],
  "relationship_hops": 0,
  "term_limit": 8
}
```

`original_query` may be omitted from logs and persisted audit records even though it is present in the in-memory service response.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `original_query`, `effective_query` | yes | Non-null expansion-service values in API responses; raw query may be omitted only from logs/audit storage |
| `retrieval_intent` | yes | Non-null caller intent validated by the application service |
| `included_terms`, `excluded_terms` | yes | Non-null deterministic arrays; empty is `[]` |
| included `term`, `source_type`, `source_id`, `priority` | yes | All non-null; source ID is the domain fact/entity ID authorizing the term |
| excluded `term`, `reason_code` | yes | Both non-null; reason code comes from expansion policy |
| `relationship_hops`, `term_limit` | yes | Non-null non-negative policy outputs |

### 4.6 `EvidenceRef`

```json
{
  "evidence_id": "evidence_01...",
  "source_type": "issuer",
  "publisher": "Example issuer",
  "source_url": "https://example.com/source",
  "source_record_id": null,
  "published_at": "2026-08-01T00:00:00Z",
  "data_as_of": "2026-08-01T00:00:00Z",
  "content_sha256": "sha256...",
  "locator": {
    "section": "Products"
  }
}
```

Full supporting text is returned only when the caller is authorized and `include_evidence_text=true`.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `evidence_id`, `source_type`, `publisher` | yes | Non-null evidence-repository fields |
| `source_url` | yes, nullable | Canonical URL from source adapter; null only when no URL exists and `source_record_id` is non-null |
| `source_record_id` | yes, nullable | External stable record ID; null only when `source_url` is non-null |
| `published_at` | yes, nullable | Source publication time; null when the source has no publication timestamp |
| `data_as_of` | yes, nullable | Fact effective time; null when not stated by the source |
| `content_sha256` | yes, nullable | Source content hash; null only for immutable structured references identified by `source_record_id` |
| `locator` | yes, nullable | Section/page/selector offsets from the adapter; null when the whole referenced record is the evidence unit |

### 4.7 `KBSignal`

```json
{
  "signal_id": "kbsig_01...",
  "signal_type": "direct_product_owner",
  "target_entity_id": "entity:company:msft",
  "matched_entity_id": "entity:product:azure",
  "alias_id": "alias_azure",
  "relationship_id": "rel_msft_owns_azure",
  "strength_class": "strong",
  "direction": "direct",
  "freshness_state": "current",
  "ambiguity_state": "unique",
  "evidence_id": "evidence_01...",
  "kb_snapshot_id": "kbs_01...",
  "freshness_policy_version": "freshness.v1",
  "signal_policy_version": "signals.price_in.v1",
  "eligible_for_boost": true,
  "rejection_reason": null,
  "base_contribution": 0.05,
  "accepted_contribution": 0.05,
  "cap_reduction": 0.0
}
```

Rules:

- rejected signals remain in debug/evaluation output with `eligible_for_boost=false`, `base_contribution=0`, and `accepted_contribution=0`;
- every eligible signal has an evidence ID;
- every signal identifies the KB snapshot, freshness policy, and signal policy that produced its runtime state;
- `base_contribution` and `accepted_contribution` are assigned by retrieval policy, not stored as KB truth;
- `cap_reduction` is non-negative and equals `base_contribution - accepted_contribution`;
- repeated aliases proving the same fact do not create repeated contributions.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| identity/type/entity/state/version fields | yes | Non-null values from attribution plus the pinned release/policies |
| `alias_id` | yes, nullable | Non-null for alias-backed signals; otherwise null |
| `relationship_id` | yes, nullable | Non-null for relationship-backed direct or indirect signals; otherwise null |
| `evidence_id` | yes, nullable | Non-null for eligible signals; null only for an ineligible `MISSING_EVIDENCE` signal |
| `eligible_for_boost` | yes | Attribution eligibility after ambiguity, validity, verification, evidence, freshness, and winner checks |
| `rejection_reason` | yes, nullable | Null exactly when eligible; otherwise one stable signal rejection code |
| contribution fields | yes | Finite non-negative numbers from the pinned signal policy; rejected signals are zero |

For indirect intent, otherwise-eligible signals are ordered by contribution descending and then `relationship_id` ascending. Only the first remains eligible. Every other such signal has zero contribution and `rejection_reason=LOWER_PRIORITY_RELATIONSHIP`.

### 4.8 `ScoreBreakdown`

```json
{
  "raw_vector_distance": 0.21,
  "semantic_score": 0.84,
  "semantic_score_type": "candidate_set_min_max_v1",
  "semantic_weight": 0.70,
  "weighted_semantic_score": 0.588,
  "kb_signals": [
    {
      "signal_id": "kbsig_01...",
      "signal_type": "direct_product_owner",
      "target_entity_id": "entity:company:msft",
      "matched_entity_id": "entity:product:azure",
      "alias_id": "alias_azure",
      "relationship_id": "rel_msft_owns_azure",
      "strength_class": "strong",
      "direction": "direct",
      "freshness_state": "current",
      "ambiguity_state": "unique",
      "evidence_id": "evidence_01...",
      "kb_snapshot_id": "kbs_01...",
      "freshness_policy_version": "freshness.v1",
      "signal_policy_version": "signals.price_in.v1",
      "eligible_for_boost": true,
      "rejection_reason": null,
      "base_contribution": 0.05,
      "accepted_contribution": 0.05,
      "cap_reduction": 0.0
    }
  ],
  "kb_total_before_cap": 0.05,
  "kb_total_cap": 0.15,
  "kb_total_after_cap": 0.05,
  "combined_pre_rerank_score": 0.638,
  "pre_rerank_position": 2,
  "signal_policy_version": "signals.price_in.v1"
}
```

`semantic_score_type` is mandatory because the current relative normalized score is not a calibrated probability and future policies may use a different representation. `semantic_weight` and `weighted_semantic_score` are mandatory so the pre-rerank calculation can be reproduced without reading policy files out of band.

Neither `semantic_score` nor `combined_pre_rerank_score` is a probability. The combined score may exceed `1.0` when an additive signal policy is active.

The direct-product numeric example above corresponds to the locked initial `signals.price_in.v1` policy. Runtime authority still comes only from the pinned `signal_policy_version` and its evaluated release; future examples must not silently redefine policy defaults.

For `candidate_set_min_max_v1`, the semantic score is relative to the current candidate set. The best candidate is normalized to `1.0` and the worst to `0.0` when distances differ; it is not comparable across unrelated requests.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `raw_vector_distance` | yes, nullable | Vector-store distance; null only when that store does not expose a raw distance |
| `semantic_score`, `semantic_score_type` | yes | Non-null retrieval scoring-adapter outputs |
| `semantic_weight`, `weighted_semantic_score` | yes | Non-null score-policy outputs |
| `kb_signals` | yes | Non-null deterministic array; semantic-only is `[]` |
| `kb_total_before_cap`, `kb_total_after_cap` | yes | Non-null score-policy outputs; both zero when no KB contribution is applied |
| `kb_total_cap` | yes | Intent-specific cap from the pinned policy (`0.15` direct, `0.30` indirect under `signals.price_in.v1`); zero only when no signal policy is pinned |
| `combined_pre_rerank_score`, `pre_rerank_position` | yes | Non-null pre-rerank scoring outputs |
| `signal_policy_version` | yes, nullable | Pinned version when a release is pinned; null only for disabled or KB-unavailable semantic-only retrieval |

### 4.9 `ArticleEvidenceV2`

```json
{
  "source_id": 1,
  "article_id": 123,
  "story_group_id": 120,
  "title": "Example title",
  "url": "https://example.com/article",
  "summary": "Example summary",
  "snippet": "Example matched text",
  "published_at": "2026-08-14T00:00:00Z",
  "retrieval_intent": "direct",
  "score": {},
  "rerank_position": 1,
  "rerank_reason": "Most directly addresses the target product change.",
  "duplicate_article_ids": [123, 124]
}
```

`rerank_reason` is model-generated explanatory metadata, not a numeric relevance score or fact source.

Field rules:

| Field | Required | Null rule and producer |
| --- | --- | --- |
| `source_id`, `article_id` | yes | Non-null final-order ID and stable article ID from the retrieval application service |
| `story_group_id` | yes, nullable | Canonicalization service ID; null when no story group is assigned |
| `title`, `url`, `snippet` | yes | Non-null article/retrieval fields; unavailable snippet is an empty string, not null |
| `summary` | yes, nullable | Article pipeline summary; null when no summary exists |
| `published_at` | yes, nullable | Article timestamp; null when source metadata has no trustworthy publication time |
| `retrieval_intent`, `score` | yes | Non-null application-service values |
| `rerank_position` | yes, nullable | Reranker output; null only when an explicitly documented reranker-bypass mode is used |
| `rerank_reason` | yes, nullable | Reranker explanation; null when reranker is bypassed or explanations are disabled |
| `duplicate_article_ids` | yes | Non-null deduplication array containing at least `article_id` |

## 5. Python Service Contract

The Python service is authoritative. MCP adapters validate and map these contracts but do not reimplement behavior.

### 5.1 Release Provider

```python
class KBReleaseProvider(Protocol):
    def pin_active_release(
        self,
        *,
        namespace: str,
        as_of: datetime,
    ) -> KBReleaseContext:
        ...
```

Guarantees:

- reads the active pointer once;
- validates release and policy compatibility;
- returns an immutable context;
- raises a typed error rather than returning partial context.

### 5.2 Entity Resolution

```python
def resolve_entities(
    request: ResolveEntitiesRequest,
    *,
    release: KBReleaseContext,
    repository: KBReadRepository,
) -> ResolveEntitiesResult:
    ...
```

`ResolveEntitiesRequest`:

| Field | Type | Required | Constraints |
| --- | --- | --- | --- |
| `query` | string | yes | trimmed, 1..2000 characters |
| `target_entity_id` | string | no | must exist in pinned snapshot |
| `language_hint` | string | no | BCP-47 or `auto` |
| `max_candidates` | integer | no | default 5, range 1..20 |
| `as_of` | timestamp | yes | supplied by request boundary |

### 5.3 Query Expansion

```python
def build_kb_query_expansion(
    request: QueryExpansionRequest,
    *,
    resolution: ResolutionDecision,
    release: KBReleaseContext,
    repository: KBReadRepository,
) -> QueryExpansion:
    ...
```

The function is deterministic and side-effect free.

### 5.4 Candidate Attribution

```python
def attribute_retrieval_candidates(
    request: AttributionRequest,
    *,
    release: KBReleaseContext,
    repository: KBReadRepository,
) -> list[CandidateAttribution]:
    ...
```

Input candidates contain stable article/story IDs, title, summary, matched chunks, and raw vector distance. Output preserves candidate order and contains accepted and rejected signals.

### 5.5 KB-Aware Retrieval Application Service

```python
def retrieve_evidence_v2(
    request: RetrieveEvidenceV2Request,
    *,
    release_provider: KBReleaseProvider,
    kb_repository: KBReadRepository,
    vector_store: VectorStore,
    reranking_agent: RAGRerankingAgent,
    signal_policy_provider: SignalPolicyProvider,
) -> RetrieveEvidenceV2Result:
    ...
```

This service owns:

- `kb_mode` enforcement;
- one request-scoped `as_of`;
- release pinning;
- resolution and expansion;
- vector retrieval;
- signal attribution and contribution;
- pre-rerank score explanation;
- rerank handoff;
- degradation and error mapping.

## 6. MCP Tool: `resolve_financial_entities_v2`

### 6.1 Request

```json
{
  "query": "微软最近的 Azure 需求有什么变化？",
  "target_entity_id": null,
  "language_hint": "zh-CN",
  "max_candidates": 5,
  "as_of": "2026-08-15T08:00:00Z"
}
```

### 6.2 Response Data

```json
{
  "kb_context": {},
  "decisions": [],
  "primary_decision": {},
  "requires_clarification": false
}
```

Rules:

- zero mentions returns one `unresolved` primary decision;
- multiple equally requested assets return `ambiguous` unless `target_entity_id` validates one;
- this API never performs article retrieval;
- `status=success` is valid for resolved, ambiguous, and unresolved results because resolution itself completed.

## 7. MCP Tool: `get_entity_profile_v2`

### 7.1 Request

Exactly one of `entity_id` or `symbol` is required.

```json
{
  "entity_id": null,
  "symbol": "MSFT",
  "as_of": "2026-08-15T08:00:00Z",
  "include_aliases": true,
  "include_relationships": true,
  "include_stale": false,
  "include_evidence_text": false
}
```

### 7.2 Response Data

```json
{
  "kb_context": {},
  "entity": {},
  "aliases": [],
  "relationships": [],
  "freshness_summary": {
    "current": 0,
    "aging": 0,
    "stale": 0
  }
}
```

Each alias or relationship contains its verification state, freshness where applicable, and evidence reference.

This is a read-only profile API. It does not refresh missing or stale data during the request.

## 8. MCP Tool: `retrieve_supporting_articles_v2`

### 8.1 Request

```json
{
  "query": "What changed for Microsoft Azure demand?",
  "target_entity_id": null,
  "retrieval_intent": "direct",
  "kb_mode": "required",
  "top_k": 3,
  "retrieval_top_k": 5,
  "as_of": "2026-08-15T08:00:00Z",
  "include_explanations": true,
  "include_rejected_signals": false,
  "include_evidence_text": false
}
```

Constraints:

| Field | Constraint |
| --- | --- |
| `query` | trimmed, 1..2000 characters |
| `retrieval_intent` | `direct` or `indirect` |
| `kb_mode` | `required`, `preferred`, or `disabled` |
| `top_k` | default 3, range 1..20 |
| `retrieval_top_k` | default 5, range 1..50, must be >= `top_k` |
| `target_entity_id` | optional explicit target, validated against release |

### 8.2 Successful Response Data

```json
{
  "query": "What changed for Microsoft Azure demand?",
  "kb_context": {},
  "resolution": {},
  "expansion": {},
  "article_count": 1,
  "articles": [
    {
      "source_id": 1,
      "article_id": 123,
      "story_group_id": 120,
      "title": "Example title",
      "url": "https://example.com/article",
      "summary": "Example summary",
      "snippet": "Example snippet",
      "published_at": "2026-08-14T00:00:00Z",
      "retrieval_intent": "direct",
      "score": {
        "raw_vector_distance": 0.21,
        "semantic_score": 0.84,
        "semantic_score_type": "candidate_set_min_max_v1",
        "semantic_weight": 0.70,
        "weighted_semantic_score": 0.588,
        "kb_signals": [
          {
            "signal_id": "kbsig_01...",
            "signal_type": "direct_product_owner",
            "target_entity_id": "entity:company:msft",
            "matched_entity_id": "entity:product:azure",
            "alias_id": "alias_azure",
            "relationship_id": "rel_msft_owns_azure",
            "strength_class": "strong",
            "direction": "direct",
            "freshness_state": "current",
            "ambiguity_state": "unique",
            "evidence_id": "evidence_01...",
            "kb_snapshot_id": "kbs_01...",
            "freshness_policy_version": "freshness.v1",
            "signal_policy_version": "signals.price_in.v1",
            "eligible_for_boost": true,
            "rejection_reason": null,
            "base_contribution": 0.05,
            "accepted_contribution": 0.05,
            "cap_reduction": 0.0
          }
        ],
        "kb_total_before_cap": 0.05,
        "kb_total_cap": 0.15,
        "kb_total_after_cap": 0.05,
        "combined_pre_rerank_score": 0.638,
        "pre_rerank_position": 2,
        "signal_policy_version": "signals.price_in.v1"
      },
      "rerank_position": 1,
      "rerank_reason": "Most directly addresses the target product change.",
      "duplicate_article_ids": [123]
    }
  ]
}
```

Rules:

- `article_count == len(articles)`;
- `source_id` is sequential in final evidence order, starting at 1;
- `pre_rerank_position` is based on combined pre-rerank score;
- `rerank_position` is final candidate order and does not overwrite score fields;
- raw vector distance and semantic-score type are always present when supplied by the vector store;
- every non-zero KB contribution references a signal and evidence ID;
- `include_explanations=false` may omit rejected signals and verbose reasons but cannot omit release/policy versions or score totals.

### 8.3 Preferred-Mode Degradation

If the target is ambiguous or the KB is unavailable, the response remains structurally complete. This example shows an ambiguous target after a valid release was pinned:

```json
{
  "api_version": "entity-kb.v2",
  "request_id": "req_01...",
  "status": "degraded",
  "data": {
    "query": "What changed at Target?",
    "kb_context": {
      "requested_mode": "preferred",
      "applied_mode": "semantic_only",
      "degradation_reason": "ENTITY_AMBIGUOUS",
      "namespace": "financial-agent",
      "release_id": "kbr_01...",
      "snapshot_id": "kbs_01...",
      "schema_version": "2.0",
      "resolver_policy_version": "resolver.v1",
      "freshness_policy_version": "freshness.v1",
      "signal_policy_version": "signals.price_in.v1",
      "evaluation_run_id": "kbeval_01...",
      "as_of": "2026-08-15T08:00:00Z"
    },
    "resolution": {
      "decision_id": "kbres_ambiguous_01...",
      "status": "ambiguous",
      "selected_entity": null,
      "candidate_entities": [
        {
          "entity_id": "entity:company:target",
          "entity_type": "company",
          "canonical_name": "Target Corporation",
          "primary_symbol": "TGT",
          "jurisdiction": "US",
          "status": "active"
        },
        {
          "entity_id": "entity:company:target_hospitality",
          "entity_type": "company",
          "canonical_name": "Target Hospitality Corp.",
          "primary_symbol": "TH",
          "jurisdiction": "US",
          "status": "active"
        }
      ],
      "alias_matches": [
        {
          "alias_id": "alias_target_01...",
          "entity_id": "entity:company:target",
          "mention_text": "Target",
          "mention_start": 16,
          "mention_end": 22,
          "alias_type": "common_name",
          "language": "en",
          "match_policy": "exact_phrase",
          "strength_class": "strong",
          "ambiguity_class": "ambiguous"
        },
        {
          "alias_id": "alias_target_hospitality_01...",
          "entity_id": "entity:company:target_hospitality",
          "mention_text": "Target",
          "mention_start": 16,
          "mention_end": 22,
          "alias_type": "common_name",
          "language": "en",
          "match_policy": "exact_phrase",
          "strength_class": "strong",
          "ambiguity_class": "ambiguous"
        }
      ],
      "reason_codes": ["MULTIPLE_STRONG_MATCHES"]
    },
    "expansion": null,
    "article_count": 1,
    "articles": [
      {
        "source_id": 1,
        "article_id": 901,
        "story_group_id": null,
        "title": "Retail demand changed during the quarter",
        "url": "https://example.com/retail-demand",
        "summary": null,
        "snippet": "The report discussed retail demand without resolving the requested company.",
        "published_at": "2026-08-14T00:00:00Z",
        "retrieval_intent": "direct",
        "score": {
          "raw_vector_distance": 0.18,
          "semantic_score": 0.82,
          "semantic_score_type": "candidate_set_min_max_v1",
          "semantic_weight": 0.70,
          "weighted_semantic_score": 0.574,
          "kb_signals": [],
          "kb_total_before_cap": 0.0,
          "kb_total_cap": 0.15,
          "kb_total_after_cap": 0.0,
          "combined_pre_rerank_score": 0.574,
          "pre_rerank_position": 1,
          "signal_policy_version": "signals.price_in.v1"
        },
        "rerank_position": 1,
        "rerank_reason": "Best semantic match after target-specific KB attribution was disabled.",
        "duplicate_article_ids": [901]
      }
    ]
  },
  "warnings": [
    {
      "code": "ENTITY_AMBIGUOUS",
      "message": "KB target was not selected; semantic-only retrieval was used."
    }
  ],
  "error": null
}
```

Semantic-only articles have `kb_signals=[]` and zero KB totals.

## 9. MCP Tool: `query_news_research_v2`

### 9.1 Request

Uses the retrieval request fields plus answer controls:

```json
{
  "question": "What changed for Microsoft Azure demand?",
  "target_entity_id": null,
  "retrieval_intent": "direct",
  "kb_mode": "preferred",
  "top_k": 3,
  "retrieval_top_k": 5,
  "as_of": "2026-08-15T08:00:00Z",
  "include_retrieval_explanations": false
}
```

### 9.2 Response Data

```json
{
  "question": "What changed for Microsoft Azure demand?",
  "answer": "Grounded answer with source citations [1].",
  "confidence": "medium",
  "insufficient_evidence": false,
  "supporting_points": [],
  "counter_points": [],
  "sources": [],
  "kb_context": {},
  "resolution": {},
  "retrieval_summary": {
    "effective_query": "MSFT Microsoft Azure cloud infrastructure",
    "candidate_count": 5,
    "evidence_count": 3,
    "kb_influenced_count": 2,
    "semantic_only": false
  },
  "retrieval_explanations": null
}
```

Rules:

- answer confidence remains an LLM output constrained by evidence; it is not derived from the KB score;
- citations must reference returned source IDs;
- preferred-mode semantic-only degradation remains visible in `status`, `warnings`, `kb_context`, and `retrieval_summary`;
- no KB signal is presented as source evidence for a financial claim;
- setting `include_retrieval_explanations=true` returns the same article score contracts as `retrieve_supporting_articles_v2`.

## 10. Offline Administrative CLI Contract

Administrative mutation is intentionally not an MCP API in v1.0.

Proposed command family:

```text
python -m event_collector.kb_admin <command> [options]
```

### 10.1 Commands

| Command | Mutation | Required contract |
| --- | --- | --- |
| `status` | no | show active release, lock version, schema, policies |
| `snapshot-build` | draft rows only | build ID, parent release, source manifest, report path |
| `snapshot-validate` | validation metadata | snapshot ID, policy versions, findings report |
| `snapshot-approve` | lifecycle metadata | snapshot ID, reviewer, reason, expected checksum |
| `release-candidate-create` | candidate row | approved snapshot, snapshot checksum, compatible policy versions/checksums, schema version, ID-algorithm version |
| `release-evaluate` | evaluation row and candidate metadata | candidate ID, fixed-input manifest hash, immutable report checksum, signed result |
| `release-approve` | candidate lifecycle plus Final Release insert | evaluated candidate, passed or explicitly approved evaluation run, reviewer, reason; creates one immutable Final Release |
| `release-activate` | active pointer | release ID, expected current release, expected lock version, actor, reason |
| `release-rollback` | active pointer | target prior release, expected current release, expected lock version, actor, reason |
| `snapshot-diff` | no | base and candidate snapshot IDs |
| `migration-dry-run` | no active mutation | legacy paths, report output |
| `migration-apply` | draft rows only | approved plan ID and expected input hashes |
| `migration-verify` | no | snapshot ID, expected counts/checksums |

### 10.2 Candidate, Evaluation, and Finalization Rules

- `release-candidate-create` is idempotent only for the same caller-supplied candidate ID and identical bound input tuple; conflicting reuse returns `BUILD_ID_CONFLICT`.
- The candidate input tuple is immutable after successful candidate creation.
- `release-evaluate` persists a completed authoritative `kb_evaluation_runs` record only after its manifest and report checksums are finalized, then attaches that run to the candidate.
- A failed evaluation can only transition the candidate to `REJECTED`. A passed or explicitly approved-with-exception evaluation can transition it to `APPROVED`.
- In one short transaction, `release-approve` verifies the candidate/evaluation, marks the candidate approved, and creates the Final Release once. Failure rolls back both changes; an identical retry returns the existing release. Its ID hashes snapshot ID/checksum, all policy versions/checksums, evaluation run ID/manifest hash, schema version, and ID-algorithm version.
- Final Release rows are immutable and have no mutable status. Active/Superseded is derived from the Active Pointer and Activation Events.

### 10.3 Activation Result

```json
{
  "status": "activated",
  "namespace": "financial-agent",
  "from_release_id": "kbr_old",
  "to_release_id": "kbr_new",
  "previous_lock_version": 7,
  "resulting_lock_version": 8,
  "activation_event_id": "kba_01...",
  "committed_at": "2026-08-15T08:00:00Z"
}
```

If the target is already active, the command returns `status=already_active`, makes no pointer change, and does not increment `lock_version`.

If expected state does not match, it returns `KB_ACTIVATION_CONFLICT` and makes no change.

Bootstrap uses `from_release_id=null`, `expected_current_release_id=null`, and `previous_lock_version=0`.

### 10.4 Exit Codes

| Exit code | Meaning |
| ---: | --- |
| 0 | command completed or confirmed idempotent no-op |
| 2 | invalid arguments or contract validation |
| 3 | validation or evaluation gate failed |
| 4 | expected-state conflict |
| 5 | database or integrity failure |
| 6 | external source failure during build |
| 7 | outcome requires reconciliation |

An activation timeout or interrupted client must run `status` and inspect activation events before retrying.

## 11. Reason Codes

### 11.1 Resolution Reason Codes

| Code | Meaning |
| --- | --- |
| `EXACT_TICKER` | Explicit unique ticker matched |
| `EXCHANGE_QUALIFIED_TICKER` | Exchange and ticker matched |
| `STRONG_ALIAS` | Reviewed unique strong alias matched |
| `CANONICAL_NAME` | Canonical entity name matched |
| `TARGET_OVERRIDE_VALIDATED` | Caller target matches extracted context |
| `CONTEXTUAL_PRODUCT_MATCH` | Product supports an already resolved target |
| `MULTIPLE_STRONG_MATCHES` | More than one strong target remains |
| `WEAK_ALIAS_ONLY` | Only weak/contextual aliases matched |
| `PRODUCT_OWNER_CANDIDATE_ONLY` | Product suggests owners but cannot select one |
| `TARGET_OVERRIDE_MISMATCH` | Caller target conflicts with query evidence |
| `NO_MATCH` | No candidate satisfied policy |

### 11.2 Signal Rejection Codes

| Code | Meaning |
| --- | --- |
| `STALE_FACT` | Fact exceeded freshness policy |
| `AGING_FACT` | Fact is in the warning window and contributes zero under `freshness.v1` |
| `OUTSIDE_VALIDITY` | Fact not valid at request `as_of` |
| `AMBIGUOUS_ALIAS` | Alias cannot authorize this signal |
| `UNVERIFIED_FACT` | Fact not verified in active release |
| `MISSING_EVIDENCE` | Evidence reference is invalid or unavailable |
| `CONTEXT_ONLY` | Signal may inform display but not boosting |
| `DUPLICATE_FACT` | Equivalent fact already counted |
| `OUTSIDE_SCOPE` | Relationship product/region scope does not match |
| `POLICY_DISABLED` | Active signal policy disables this class |
| `CLASS_CAP_REACHED` | Per-class cap removed some contribution |
| `TOTAL_CAP_REACHED` | Total KB cap removed some contribution |
| `LOWER_PRIORITY_RELATIONSHIP` | Otherwise-valid indirect relationship lost the deterministic strongest-signal selection |

### 11.3 Expansion Reason Codes

| Code | Meaning |
| --- | --- |
| `TERM_BUDGET_EXCEEDED` | A lower-priority term exceeded the configured term limit |
| `RELATIONSHIP_HOP_LIMIT` | A relationship term exceeded the allowed hop count |
| `UNRESOLVED_TARGET` | Expansion cannot select target-specific terms |
| `DIRECT_INTENT_RELATION_BLOCKED` | Direct intent rejected an indirect relationship term |
| `STALE_EXPANSION_FACT` | The fact behind an expansion term is stale |
| `LOW_PRIORITY_TERM` | A valid term was omitted in favor of higher-priority terms |

### 11.4 Validation Finding Codes

Minimum stable set:

```text
ALIAS_EMPTY
ALIAS_NORMALIZATION_EMPTY
ALIAS_TRUNCATED
ALIAS_PROHIBITED_GENERIC
ALIAS_STRONG_COLLISION
ALIAS_LANGUAGE_UNSUPPORTED
ENTITY_TYPE_INVALID
ENTITY_EVIDENCE_MISSING
RELATION_TYPE_INVALID
RELATION_DIRECTION_INVALID
RELATION_SCOPE_INVALID
RELATION_EVIDENCE_MISSING
TIER_A_REQUIRED_FIELD_MISSING
TIER_A_ALIAS_COVERAGE_MISSING
SNAPSHOT_COUNT_MISMATCH
SNAPSHOT_CHECKSUM_MISMATCH
POLICY_VERSION_INCOMPATIBLE
```

## 12. Error Catalog

| Code | Retryable | Meaning | Caller action |
| --- | --- | --- | --- |
| `INVALID_ARGUMENT` | no | Input failed contract validation | fix request |
| `KB_REQUIRED_UNAVAILABLE` | maybe | Required active release unavailable | operator check or choose preferred mode |
| `KB_RELEASE_INVALID` | no | Active release or evidence integrity invalid | operator rollback/repair |
| `KB_POLICY_INCOMPATIBLE` | no | Runtime cannot load bound policy tuple | deploy compatible code/policy or rollback |
| `KB_SNAPSHOT_NOT_VALIDATED` | no | Administrative target lacks validation | validate first |
| `KB_EVALUATION_NOT_APPROVED` | no | Release Candidate lacks an approved evaluation gate | evaluate/adjudicate the candidate |
| `BUILD_ID_CONFLICT` | no | Build ID was reused with different inputs | choose a new ID or restore original inputs |
| `KB_ACTIVATION_CONFLICT` | yes | Expected active state changed | reread status and retry deliberately |
| `KB_ACTIVATION_OUTCOME_UNKNOWN` | no blind retry | Client did not observe commit outcome | reconcile active state/events |
| `ENTITY_AMBIGUOUS` | no | Required request has multiple targets | provide target or clarify |
| `ENTITY_UNRESOLVED` | no | Required request has no target | correct target or curate KB |
| `TARGET_ENTITY_MISMATCH` | no | Explicit target conflicts with resolution | correct request |
| `CORPUS_UNAVAILABLE` | maybe | Vector corpus cannot serve retrieval | corpus/runtime recovery |
| `CORPUS_INTEGRITY_ERROR` | no | Active corpus failed eligibility/integrity | corpus rollback/repair |
| `RERANKER_FAILED` | maybe | Reranker did not return valid ordering | model/runtime recovery |
| `ANSWER_GENERATION_FAILED` | maybe | Answer model failed after retrieval | model/runtime recovery |
| `INTERNAL_ERROR` | maybe | Unexpected failure | inspect request ID and logs |

Errors from source adapters during offline build do not automatically affect the active release. They fail or partially complete the draft build according to the build plan and are recorded in its report.

## 13. Idempotency and Concurrency

### 13.1 Read APIs

Read APIs are side-effect free by default. The same request, release, corpus generation, and model outputs should produce the same deterministic pre-rerank data. LLM rerank and answer outputs may vary unless model determinism is separately controlled.

### 13.2 Snapshot Build

`snapshot-build` accepts a caller-generated `build_id` and input manifest hash. Reusing the same `build_id` with identical content returns the existing result. Reusing it with different content returns `BUILD_ID_CONFLICT`.

### 13.3 Activation

Activation requires:

- target release ID;
- expected current release ID;
- expected `lock_version`;
- actor and reason.

The database acquires `BEGIN IMMEDIATE` before authoritative reads. A conflict never applies a partial update.

### 13.4 Request Pinning

The request context stores the pinned release. Repository and attribution methods require it explicitly and shall not re-resolve the active release internally.

## 14. Field Ownership

| Field group | Producer | Authoritative consumer |
| --- | --- | --- |
| entity, alias, relationship facts | snapshot builder and review workflow | resolver and attribution |
| evidence provenance | source adapter/repository | validator, profile, attribution explanation |
| freshness state | runtime freshness policy | attribution and score policy |
| resolution status | entity resolver | query expansion and application service |
| expansion terms | query expansion | vector retrieval |
| raw vector distance | vector store | score policy and response |
| semantic score | retrieval scoring adapter | score policy and response |
| semantic weight and weighted semantic score | pinned signal policy | pre-rerank ordering and response |
| KB signal eligibility | attribution engine | score policy |
| numeric signal contribution | signal policy | pre-rerank ordering and response |
| rerank position/reason | reranker adapter | final evidence ordering |
| answer confidence | answer model | answer response only |
| release and policy versions | release provider | all downstream stages and response |

## 15. Contract Validation Rules

1. `retrieval_top_k >= top_k`.
2. `required` mode with no valid release returns an error before vector retrieval.
3. `required` mode with ambiguous or unresolved target returns an error before KB expansion.
4. `preferred` degradation retains warnings and zero KB contributions.
5. `disabled` mode does not call release provider or resolver.
6. A resolved decision has exactly one selected entity.
7. Every eligible KB signal has valid alias or relationship provenance and evidence.
8. Every ineligible signal has a rejection reason.
9. `kb_total_before_cap` equals the sum of eligible signal `base_contribution` values.
10. `kb_total_after_cap` equals the sum of signal `accepted_contribution` values and is <= `kb_total_cap`.
11. Under `signals.price_in.v1`, `semantic_weight == 0.70` and `weighted_semantic_score == semantic_score * semantic_weight`.
12. `combined_pre_rerank_score == weighted_semantic_score + kb_total_after_cap` under `signals.price_in.v1`.
13. Candidate IDs returned by the reranker equal the supplied candidate set exactly.
14. Source citation IDs reference returned sources.
15. A response with `status=degraded` has at least one warning and degradation reason.
16. A response with `status=error` has `data=null` and a non-null error.
17. All release and policy fields used by one request are identical across articles and answer output.
18. Public IDs and fields do not expose SQLite row identity or graph-vendor internal identity.
19. Under `signals.price_in.v1`, `kb_total_cap` is `0.15` for direct intent and `0.30` for indirect intent; the indirect range is `0.18-0.30`. Otherwise-eligible indirect signals are ordered by contribution descending then `relationship_id` ascending, and only the first contributes for one article and target.

## 16. Contract Test Matrix

| Case | Expected result |
| --- | --- |
| exact `MSFT` ticker | resolved with `EXACT_TICKER` |
| Chinese reviewed alias | resolved; English expansion terms |
| `Target` without company context | ambiguous; no boost |
| explicit target conflicts with query | `TARGET_ENTITY_MISMATCH` |
| product mention only | owner candidates, no automatic target |
| stale executive only | signal returned rejected with zero contribution |
| aging product-owner fact | `AGING_FACT`, zero contribution under `freshness.v1` |
| theme-only NVDA article | contextual signal, zero direct boost |
| required mode, KB unavailable | error before retrieval |
| preferred mode, KB unavailable | degraded semantic-only retrieval |
| disabled mode | no KB calls; success semantic-only |
| duplicate aliases for same fact | one contribution |
| semantic score `0.84` | weighted semantic score `0.588` under weight `0.70` |
| direct ticker + alias prove same identity | identity class contribution `0.10`, not `0.20` |
| direct signals reach cap | total direct contribution `0.15` |
| dependency and supplier paths both match | strongest indirect contribution `0.30`, no path stacking |
| equal-contribution indirect relationships | ascending `relationship_id` wins; loser gets `LOWER_PRIORITY_RELATIONSHIP` |
| weakest verified indirect relationship | contribution `0.18`, greater than direct total cap |
| low semantic score remains in vector candidate pool | no additional semantic-score hard gate before KB contribution |
| class/total cap reached | adjustment exposed |
| reranker invents ID | `RERANKER_FAILED` |
| activation expected state changed | `KB_ACTIVATION_CONFLICT`, no mutation |
| first activation from bootstrap | expected release null and lock 0 succeeds atomically |
| Final Release update attempt | rejected; release remains byte-for-byte unchanged |
| activation response interrupted | reconcile; no blind retry |
| rollback to prior release | new event, history preserved |

## 17. API Closure Review

The API is ready for implementation only when:

1. every field has one producer, consumer, null rule, and version owner;
2. System Design state names match this document exactly;
3. required/preferred/disabled behavior is identical in Python and MCP contracts;
4. administrative mutation remains outside MCP;
5. score fields cannot be mistaken for probabilities or LLM confidence;
6. ambiguity, staleness, degradation, conflict, and unknown-outcome semantics have focused acceptance tests;
7. v1 compatibility and v2 tool names are approved;
8. no response drops the release or policy versions needed for audit;
9. examples remain illustrative and are not presented as current runtime output;
10. implementation begins with expected-RED tests for the selected invariant.

Closure Review result on 2026-08-15: **GO for implementation planning**. The 22 JSON examples parse, lifecycle and policy names match the approved System Design, and no P1 API-contract blocker remains. This approval is a design-contract decision, not proof that any API or command is implemented.
