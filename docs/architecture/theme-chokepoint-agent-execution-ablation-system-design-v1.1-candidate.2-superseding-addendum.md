# Theme Chokepoint Agent Execution Ablation System Design v1.1 candidate.2 — Superseding Addendum

## Document control and precedence

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-candidate.2` |
| Immediate predecessor | `docs/architecture/theme-chokepoint-agent-execution-ablation-system-design-v1.1-superseding-addendum.md` |
| Immediate predecessor SHA-256 | `5b4edc49f99316873a18cd83de81a7359405dc2be998d1ca82c328aecfdc828f` |
| Original predecessor | `docs/architecture/theme-chokepoint-agent-execution-ablation-system-design.md` (`a7eefe7052040562ec21ed52b717b69401b9cbe469548b1c042578cad1a54510`) |
| Controls | Only the canonical encoding, candidate projection, and retry-policy clauses of System Design v1.1. All other predecessor clauses remain unchanged. |

This append-only candidate preserves all predecessor bytes. It is neither a freeze nor an implementation claim.

## 1. Executable canonical JSON (replaces v1.1 §2 CJ1)

`CJ1` means the [RFC 8785 JSON Canonicalization Scheme (JCS)](https://www.rfc-editor.org/rfc/rfc8785), with this required preprocessing before JCS:

1. Recursively normalize every object member name and every string value to Unicode NFC. Duplicate member names created by NFC normalization are rejected.
2. Convert every monetary value to a signed, integral micro-USD value before object construction. Values that cannot convert exactly, or overflow the implementation's validated integer domain, are rejected.
3. Reject `NaN`, infinities, and numbers outside JCS/IEEE-754 finite-number serialization. Dates are preformatted `YYYY-MM-DD` strings.

After preprocessing, JCS alone governs UTF-8 encoding, member ordering by UTF-16 code units, ECMAScript number serialization, and string serialization. In particular, a solidus `/` is not additionally escaped; non-ASCII characters are emitted as JCS strings; quotation mark, reverse solidus, and required control characters use JCS escaping. Implementations must not substitute a language's ordinary JSON serializer unless it is proven JCS-compatible.

The existing task payload top-level field set remains exact. Its `contracts` array must contain exactly one descriptor with `role="retry_policy"`; its `id` and `sha256` are the frozen retry policy identity. The plan manifest, execution fairness identity, and pair manifest must each repeat that same `retry_policy_id` and `retry_policy_sha256`. A missing, duplicate, or mismatching descriptor rejects planning with `CONTRACT_MISMATCH`.

### 1.1 Frozen cross-implementation vectors

These vectors use UTF-8 bytes of the displayed canonical JCS text. They are normative tests, not examples.

| Vector | Preprocessed canonical JCS text | Expected SHA-256 |
| --- | --- | --- |
| `JCS-EDGE-001` | `{"\u0001":"SOH","\r":"CR","a/":"slash/é","z":"line\n\u0000","é":"NFC","😀":"face","":"private"}` | `d96bd726cb17daf8ce2db891c5a74924ca0b81005255f7658cce3ee299e54738` |
| `TASK-KEY-001` | The exact task payload below after CJ1 | `505d1220986e63ce5f35b144c5367c31391626b5e9b7bd04d4712a37731b4c5f` |
| `TASK-ID-001` | `{"execution_id":"exec/é","identity_version":"financial-agent.theme-chokepoint.task-id.v1","task_key":"505d1220986e63ce5f35b144c5367c31391626b5e9b7bd04d4712a37731b4c5f"}` | `92d311d0152f6afc3425eab46dfc17ccdbc869ec6759c66c9ed4922fa954113c` |

`JCS-EDGE-001` is supplied from source input with key `e\u0301`; NFC makes it `é`. It deliberately tests a control key/value, a slash, non-ASCII, and the UTF-16 ordering boundary where `😀` sorts before ``.

```json
{
  "contracts": [
    {"id":"execution-contract/v1","role":"execution","sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":"1"},
    {"id":"financial-agent.theme-chokepoint.retry-policy.v1","role":"retry_policy","sha256":"53ec14866e7edf6084165add4a1322f8213698db8cc330c6338ad93ba92401fd","version":"1"}
  ],
  "identity_version":"financial-agent.theme-chokepoint.task-key.v1",
  "input_snapshot_sha256":"0000000000000000000000000000000000000000000000000000000000000000",
  "iteration":1,
  "material_field_or_route":"qualification_barrier",
  "node_id":"node/é\u0001",
  "scope":{"as_of_date":"2026-09-04","horizon_months":12,"region":"全球/Global"},
  "sealed_query":"supply/é\n",
  "source_policy":{"id":"source-policy/v1","sha256":"1111111111111111111111111111111111111111111111111111111111111111"},
  "task_budget":{"max_cost_usd_micros":500000,"max_input_tokens":0,"max_output_tokens":0,"max_queries":1,"max_sources":5,"max_time_seconds":60},
  "task_kind":"evidence"
}
```

## 2. Retry policy v1 (replaces the ambiguous v1.1 §1.1/§3 mapping)

Recommended frozen policy: `retry_policy_id = financial-agent.theme-chokepoint.retry-policy.v1`; `retry_policy_sha256 = 53ec14866e7edf6084165add4a1322f8213698db8cc330c6338ad93ba92401fd`; `max_attempts_per_task = 2`, including the first successfully claimed attempt. Every claimed attempt counts even if its response is later uncertain; a rejected stale completion does not create an attempt. The policy ID/SHA are mandatory plan identity, as §1 states. The SHA is JCS of this exact policy object:

```json
{"attempt_counting":"each_claimed_attempt","exhaustion_precedence":["ATTEMPT_LIMIT_REACHED","SHARED_BUDGET_EXHAUSTED"],"max_attempts_per_task":2,"policy_id":"financial-agent.theme-chokepoint.retry-policy.v1","reservation_mode":"full_task_cap","stable_mapping":{"deadline":"EXECUTION_TIME_BUDGET_EXHAUSTED","lease_after_provider_dispatch":"ATTEMPT_RESPONSE_NOT_DURABLE","lease_before_provider_dispatch":"LEASE_EXPIRED_RETRYABLE","retryable_provider_failure":"RETRYABLE_PROVIDER_FAILURE","terminal_contract_failure":["CONTRACT_MISMATCH","EVIDENCE_ID_OUT_OF_SCOPE","RECONCILIATION_FAILED","SCHEMA_VALIDATION_FAILED"],"uncertain_provider_response":"ATTEMPT_RESPONSE_NOT_DURABLE"},"version":"1"}
```

Before every claim, reserve the full configured per-task maxima from the execution's remaining shared queries, sources, tokens where enforceable, cost, and time. Remaining capacity is `configured - settled - unreconciled - active reservations`. On accepted completion, settle actual metered usage and release only the unused portion. A possibly charged call without a durable response settles its full reservation as `unreconciled`; it is never silently released. A claim is forbidden when a full reservation cannot fit, and the task becomes `exhausted`.

| Durable event | If another attempt is allowed and a full reservation fits | Otherwise | Stable task reason codes |
| --- | --- | --- | --- |
| Provider/model transport failure before a durable accepted result | `failed_retryable` | `exhausted` | `RETRYABLE_PROVIDER_FAILURE`; then `ATTEMPT_LIMIT_REACHED` or `SHARED_BUDGET_EXHAUSTED` |
| Provider may have been called but response is not durable | `failed_retryable`; the full reservation remains settled as unreconciled | `exhausted` | `ATTEMPT_RESPONSE_NOT_DURABLE`; then `ATTEMPT_LIMIT_REACHED` or `SHARED_BUDGET_EXHAUSTED` |
| Lease expires without a provider-dispatch marker | `failed_retryable`; release its unspent reservation | `exhausted` | `LEASE_EXPIRED_RETRYABLE`; then `ATTEMPT_LIMIT_REACHED` or `SHARED_BUDGET_EXHAUSTED` |
| Lease expires after a provider-dispatch marker but before durable intake | Use the uncertain-provider row | Use the uncertain-provider row | `ATTEMPT_RESPONSE_NOT_DURABLE` |
| Schema/evidence binding/reconciliation/contract failure | `failed_terminal` | `failed_terminal` | `SCHEMA_VALIDATION_FAILED`, `EVIDENCE_ID_OUT_OF_SCOPE`, `RECONCILIATION_FAILED`, or `CONTRACT_MISMATCH` |
| Execution time deadline arrives before dispatch, or a post-deadline completion is fenced | `exhausted` | `exhausted` | `EXECUTION_TIME_BUDGET_EXHAUSTED` |

For rows with two exhaustion causes, `ATTEMPT_LIMIT_REACHED` wins over `SHARED_BUDGET_EXHAUSTED`; retain the other as a detail code. A valid completion must hold an unexpired fence and arrive before the execution deadline. No new claim or heartbeat may extend work past that deadline. These mappings are the only v1 mappings; an operator retry uses the same rules and cannot override cap, reservation, status, or fence.

## 3. Complete `stage3_candidate_projection_v1` (replaces v1.1 §5)

`candidate_result_sha256 = SHA-256(JCS(NFC(stage3_candidate_projection_v1)))`. The projection has exactly these top-level fields; omitted optional values are `null`, every collection is present (empty as `[]`), and all numeric values are finite JCS numbers.

```text
projection_version
stage3 = {contract_version, executable_contract:{id,sha256}, status,
          iterations_completed, incomplete_reason_codes}
claims, evidence_cards, source_snapshots, counter_outcomes, assessments
```

`claim_key` is the hash of `{node_id,claim_type,statement,material_field,primary_scoring_dimension,scoring_use,fact_key,assessment_scope,condition_ids,claim_capabilities,source_ambiguity,scoring_eligible,subject_company_ids}`. `evidence_key` is the hash of every EvidenceCard field except `evidence_id`, `claim_id`, `article_id`, `derived_from_evidence_id`, `source_identity_id`, and `source_version_id`; its business assertions are represented by `assertion_key` and semantic assertion payload. `source_key` is the hash of `{canonical_url,content_hash,original_text}`. `assertion_key` is the hash of every BusinessFactAssertion field except `assertion_id`, `evidence_id`, and receipt IDs/timestamps inside `verification`. `counter_outcome_key` is the hash of the complete Counter outcome payload in the table below.

The projection retains every non-excluded contract field as follows:

| Object | Exact semantic payload and references | Sort key |
| --- | --- | --- |
| Claim | `claim_key`, all fields named in its key, `evidence_keys` (replacing evidence IDs), and `derived_from_evidence_key`. | `claim_key` |
| EvidenceCard | `evidence_key`; `claim_key`; `source_key`; source title/publisher/type/dates/location/quote/content hash/stance/limitations/extraction model/prompt version/origin event/evidence family; fact/scope/condition/capability/scoring/ambiguity/company fields; `derived_from_evidence_key`; and `business_fact_assertions` as `{assertion_key,payload}`. | `evidence_key` |
| SourceSnapshot | `source_key`, canonical URL, content hash, original text. | `source_key` |
| Counter outcome | `segment_id`, protocol version, max query/time/cost caps (cost in micro-USD), stop reason, unresolved routes, demand/supply/key-source `evidence_keys`, and routes `{route_id,query,status,evidence_keys,finding,result_semantics,coverage_state,new_counter_evidence_keys}`. | `(segment_id, route_id)`; receipt-level fields sort by segment ID |
| Assessment | segment ID, contract version, all score/coverage/state fields, missing fields, eligibility/gates/withheld reason, `relief_horizon`, full relief assessment, dimensions, and `counter_outcome_key` or `null`. A dimension retains every DimensionRatingDraft and BoundBasis field, with evidence/claim IDs replaced by semantic keys; anchor conditions retain every field with the same replacement. Relief retains both scenarios and every period/result field. | `segment_id`; dimensions by `dimension`; conditions by `condition_id`; periods by `(period_start, period_end)` |

`assessment_scope` is always the complete `{company_id,product_id,segment_id,customer_or_platform_scope,geography,time_horizon_months,as_of_date}` object or `null`. Unordered identifier collections are deduplicated and sorted by their semantic key or JCS scalar form: incomplete reasons, condition/capability/company IDs, evidence keys, claim keys, missing fields/questions, gates, and route IDs. Original order is retained only for text inside a scalar; all listed record collections use the table sort key. A duplicate semantic key with non-identical normalized payload is `CANDIDATE_ARTIFACT_CONFLICT`; identical duplicates collapse to one.

Excluded operational metadata is exactly: base/pair/execution/namespace/task/attempt/receipt/request/response/result/reconciliation/query-log/provider trace/worker/session/lease/fence/storage IDs; `run_id`; article ID; source identity/version IDs; created/executed/completed/reconciled/verified timestamps; actual token/cost/time usage; retry count; reservations; transport headers/raw locations; and feature flags. Counter provider receipt IDs and verifier receipt IDs are excluded, but their semantic decision and raw-response hash remain through the assertion payload. No other field may be excluded.

`PROJECTION-EMPTY-001` is a frozen complete-root vector:

```json
{"assessments":[],"claims":[],"counter_outcomes":[],"evidence_cards":[],"projection_version":"financial-agent.theme-chokepoint.stage3-candidate-projection.v1","source_snapshots":[],"stage3":{"contract_version":"v1","executable_contract":{"id":"exec/v1","sha256":"4444444444444444444444444444444444444444444444444444444444444444"},"incomplete_reason_codes":[],"iterations_completed":1,"status":"CHOKEPOINT_ASSESSMENT_READY"}}
```

Its expected `candidate_result_sha256` is `d9bbc0d35db4bf04e7eb9d284a6914d6ed9c7c3c9bcc837d4e8a36b7ed5561df`.

## 4. Preserved discretion

This candidate does not select a JSON library, queue, database layout, physical SQL type, performance index, class/file organization, opaque-ID format, clock, or lease duration. Any choice is acceptable only if it passes the frozen vectors and preserves the exact retry, projection, ordering, CAS, and retention semantics.
