# Theme Chokepoint Agent Execution Ablation Migration Contract v1.1 candidate.2 — Superseding Addendum

## Document control and precedence

| Field | Value |
| --- | --- |
| Status | `READY_FOR_HUMAN_REVIEW` |
| Version | `1.1-candidate.2` |
| Immediate predecessor | `docs/architecture/theme-chokepoint-agent-execution-ablation-migration-contract-v1.1.md` |
| Immediate predecessor SHA-256 | `98660eb94aec2de1c05fd3c730ba8669798fcd89c91bc77aed0bc8e23164d6dc` |
| Controls | Only Migration Contract v1.1 §3 legacy preflight. |

## 1. Accepted legacy baseline signature

Before any v1.1 migration write, preflight must recognize exactly `TC_LEGACY_SQLITE_BASELINE_A`. It is the repository's actual Stage 2/3 dependency surface, not a freeze of unrelated tables or performance indexes.

| Table | Required columns and constraint signature |
| --- | --- |
| `theme_chokepoint_runs` | `run_id TEXT PRIMARY KEY`; non-null `request_json,status,created_at,updated_at`; nullable `demand_frame_json,confirmed_by,confirmed_at`. |
| `theme_chokepoint_graphs` | `run_id TEXT PRIMARY KEY` with FK to `theme_chokepoint_runs(run_id)`; non-null `truncation_reasons_json,created_at`. |
| `theme_chokepoint_nodes` | `node_id TEXT PRIMARY KEY`; non-null `run_id` FK to runs, `ordinal,normalized_name,node_type,depth,status,description,aliases_json`; nullable `product_anchor_id,stop_reason`; unique `(run_id,ordinal)`. |
| `theme_chokepoint_stage3_results` | `run_id TEXT PRIMARY KEY` with FK to runs; non-null `payload_json,created_at`. |

The direct relationships are required because v1.1 admission reads the base run and its frozen graph/nodes and must prove that no canonical Stage 3 row already occupies the one-to-one slot. No existing source, provider, counter-search, Stage 4, feedback, or unrelated application table is a prerequisite: candidate.2 requires new candidate-scoped lineage instead of assuming old lineage tables exist.

`TC_LEGACY_SQLITE_BASELINE_B` is the only allowed equivalent legacy signature: it has all A tables/constraints plus any additive legacy tables and non-unique indexes. Extra tables and non-unique indexes are ignored. Equivalent SQL type spellings are accepted only when SQLite reports the same primary-key, unique, foreign-key, nullability, and named-column semantics above.

## 2. Deterministic preflight result

Preflight inspects schema metadata read-only and compares it to A or B. A database with an absent table/column, changed PK/unique/FK/nullability constraint, duplicate canonical Stage 3 rows, unknown partial v1.1 records, or an unrecognized signature returns `MIGRATION_REQUIRED` before creating a journal, backup, table, index, lease, candidate, or execution. This is a zero-write failure.

A prior durable migration journal with the exact candidate.2 schema-contract ID/checksum is recognized as already applied only after its recorded verification succeeds. A journal indicating `APPLYING`, `FAILED`, `ROLLED_BACK`, a different checksum, or partial candidate records is not an alternative baseline; it returns `MIGRATION_REQUIRED` with zero new writes and requires an explicit recovery procedure.

After the accepted baseline passes, the additive migration and verification protocol from Migration Contract v1.1 §3 applies unchanged. This addendum freezes compatibility detection only; it does not prescribe physical DDL, migration tooling, table names for new records, or indexes.
