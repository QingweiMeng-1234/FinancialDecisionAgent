# Theme Chokepoint Agent Execution Ablation v1.1 candidate.2 Document Review Report

## Review control

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Immediate predecessor review | `docs/architecture/theme-chokepoint-agent-execution-ablation-document-review-v1.1.md` |
| Immediate predecessor SHA-256 | `ec9c8e6d3d184fbebda34a5df28f231fe4640083dc825e1b107ae0f620caa4a9` |
| Trigger evidence | Fresh coding preflight remained `NEEDS_DOCUMENT_REVIEW`; four blocking issues were supplied. This review independently verified the cited v1.1 text and legacy repository schema. |
| Scope | Append-only documentation candidates only. No v1.0 or first-v1.1 candidate byte, code, test, migration implementation, Bundle, profile, manifest, Git index/history, commit, or push changed. |

## 1. Predecessor audit

The immediate predecessor bytes were rehashed and matched:

| Artifact | SHA-256 |
| --- | --- |
| System Design v1.1 candidate | `5b4edc49f99316873a18cd83de81a7359405dc2be998d1ca82c328aecfdc828f` |
| Migration Contract v1.1 candidate | `98660eb94aec2de1c05fd3c730ba8669798fcd89c91bc77aed0bc8e23164d6dc` |
| Review Report v1.1 candidate | `ec9c8e6d3d184fbebda34a5df28f231fe4640083dc825e1b107ae0f620caa4a9` |

Read-only evidence confirmed that the current repository creates `theme_chokepoint_runs`, `theme_chokepoint_graphs`, `theme_chokepoint_nodes`, and one canonical `theme_chokepoint_stage3_results` row per run with the constraints captured in the candidate. No persisted schema-version record exists in the legacy schema, so a concrete structural signature is necessary.

## 2. Exact candidate.2 diff

No predecessor file was changed. The exact revision is three new paths:

| New path | Exact diff shape | Superseded clause | Exact semantic change | Compatibility reason |
| --- | --- | --- | --- | --- |
| `docs/architecture/theme-chokepoint-agent-execution-ablation-system-design-v1.1-candidate.2-superseding-addendum.md` | New file: `+117/-0` lines | System Design v1.1 §§1.1, 2, 3, 5 | Replaces informal CJ1 with NFC preprocessing plus RFC 8785 JCS and frozen SHA vectors; puts retry-policy ID/SHA in plan identity; fixes a two-attempt, full-reservation, reason-code mapping; and freezes per-object candidate projection/key/sort/null/metadata rules with a result-hash vector. | Makes cross-language identity, execution reduction, recovery, and candidate comparison deterministic without selecting a JSON library or physical store. |
| `docs/architecture/theme-chokepoint-agent-execution-ablation-migration-contract-v1.1-candidate.2-superseding-addendum.md` | New file: `+34/-0` lines | Migration Contract v1.1 §3 | Defines accepted A/B legacy SQLite structural signatures for the actual Stage 2/3 dependency surface and a zero-write `MIGRATION_REQUIRED` outcome for unknown, drifted, or partial databases. | Preserves legacy data and permits additive unrelated tables/indexes; does not freeze unrelated schema or DDL. |
| this report | New file: `+50/-0` lines | Review report v1.1 | Records predecessor hashes, repository evidence, exact candidate paths, product-decision checklist, and status. | Keeps the first review immutable and gives a future Frozen Bundle unique candidate.2 paths/SHA slots. |

## 3. Mandatory semantics and retained discretion

Mandatory: RFC 8785 JCS after NFC/micro-USD preprocessing; the frozen test vectors; retry-policy identity in every plan/fairness manifest; the v1 attempt/budget/uncertain-response/lease-expiry mappings; full semantic projection/key/sort/null rules; and baseline A/B structural preflight with zero-write failure.

Developer discretion remains: JCS-capable library choice, persistence layout, equivalent SQL spelling, additive non-unique indexes, migration framework, queues, class/file organization, opaque-ID format, and lease duration. None can alter a mandatory vector, reason-code mapping, state transition, projection byte, or preflight result.

## 4. Human-review checklist

- [ ] Confirm RFC 8785 JCS after NFC and integer micro-USD preprocessing is the required cross-implementation identity format.
- [ ] Confirm the supplied task and candidate-result hash vectors are the intended immutable interoperability fixtures.
- [ ] **Product semantic decision:** approve or change the recommended retry policy of two total claimed attempts, full per-task budget reservation, conservative settlement for an uncertain provider response, and the exact stable reason-code precedence. A change requires a new policy SHA and candidate revision.
- [ ] Confirm candidate projection includes all contract-visible semantic fields and excludes precisely the listed operational metadata.
- [ ] Confirm the minimal A/B legacy signatures are the correct Stage 2/3 admission boundary and that unknown/drifted/partial schemas must fail `MIGRATION_REQUIRED` with zero writes.
- [ ] Confirm no implementation, freeze, GO, or Developer-start authority is implied.

Final state: `READY_FOR_HUMAN_REVIEW`.
