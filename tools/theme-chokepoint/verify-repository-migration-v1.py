"""Controlled dry-run/apply/verify/rollback for the evidence-trust SQLite schema."""

from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


COMPANY_ROLES = ("discovery", "evidence", "scoring", "critic")
LEGACY_THEME_TABLES = {
    "theme_chokepoint_runs",
    "theme_chokepoint_stage3_results",
    "theme_chokepoint_stage4_results",
    "theme_chokepoint_feedback",
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "theme_chokepoint_runs": {
        "run_id", "request_json", "status", "demand_frame_json", "created_at",
        "updated_at", "confirmed_by", "confirmed_at",
    },
    "theme_chokepoint_product_anchors": {
        "anchor_id", "run_id", "ordinal", "product_name", "buyer_or_user",
        "demand_variable", "theme_link", "confidence",
        "supporting_evidence_ids_json", "missing_evidence_json", "status",
    },
    "theme_chokepoint_graphs": {
        "run_id", "truncation_reasons_json", "created_at",
    },
    "theme_chokepoint_nodes": {
        "node_id", "run_id", "ordinal", "normalized_name", "node_type", "depth",
        "status", "description", "aliases_json", "product_anchor_id", "stop_reason",
    },
    "theme_chokepoint_edges": {
        "edge_id", "run_id", "ordinal", "downstream_node_id", "upstream_node_id",
        "relation_type", "demand_transmission", "criticality_hypothesis",
        "substitute_hypothesis", "confidence", "supporting_claim_ids_json",
        "verification_questions_json", "status",
    },
    "theme_chokepoint_stage3_results": {"run_id", "payload_json", "created_at"},
    "theme_chokepoint_stage4_results": {"run_id", "payload_json", "created_at"},
    "theme_chokepoint_feedback": {
        "correction_id", "run_id", "object_type", "object_id", "field_name",
        "proposed_value", "rationale", "actor", "created_at",
    },
    "theme_chokepoint_hotspots": {"hotspot_id", "payload_json", "created_at"},
    "theme_chokepoint_monitoring_triggers": {
        "trigger_id", "run_id", "payload_json", "created_at",
    },
    "theme_chokepoint_monitoring_refreshes": {
        "refresh_id", "run_id", "payload_json", "created_at",
    },
    "theme_chokepoint_monitoring_evidence": {
        "event_id", "run_id", "payload_json", "created_at",
    },
    "theme_chokepoint_counter_search_requests": {
        "request_record_id", "run_id", "segment_id", "route_id", "query",
        "assessment_as_of", "assessment_scope_sha256", "created_at",
    },
    "theme_chokepoint_provider_executions": {
        "execution_claim_id", "provider_identity", "provider_account_or_route_identity",
        "upstream_trace_id", "raw_response_sha256", "consumer_kind",
        "consumer_record_id", "claimed_at",
    },
    "theme_chokepoint_counter_search_raw_responses": {
        "response_record_id", "request_record_id", "run_id", "provider",
        "provider_trace_id", "http_status", "raw_body", "raw_response_sha256",
        "retrieved_at", "cost_usd",
    },
    "theme_chokepoint_counter_search_results": {
        "result_record_id", "response_record_id", "request_record_id", "run_id",
        "route_id", "query", "status", "evidence_ids_json",
        "new_counter_evidence_ids_json", "finding", "query_log_id",
        "result_semantics", "coverage_state", "segment_id",
        "assessment_scope_sha256", "parsed_at",
    },
    "theme_chokepoint_counter_source_versions": {
        "source_version_id", "run_id", "segment_id", "route_id", "result_record_id",
        "canonical_url", "content_sha256", "original_text", "retrieved_at",
    },
    "theme_chokepoint_counter_claims": {
        "claim_id", "result_record_id", "source_version_id", "run_id", "segment_id",
        "route_id", "statement", "statement_sha256",
    },
    "theme_chokepoint_counter_evidence_cards": {
        "evidence_id", "claim_id", "source_version_id", "result_record_id", "run_id",
        "segment_id", "route_id", "assessment_scope_sha256", "payload_json",
    },
    "theme_chokepoint_counter_search_reconciliations": {
        "receipt_id", "request_record_id", "response_record_id", "result_record_id",
        "run_id", "segment_id", "route_id", "assessment_scope_sha256",
        "coverage_state", "decision", "reconciled_at",
    },
    "theme_chokepoint_fact_verification_requests": {
        "request_record_id", "run_id", "assertion_id", "evidence_id",
        "assertion_sha256", "governance_bundle_sha256", "producer_execution_id",
        "verifier_execution_id", "source_identity_id", "source_version_id",
        "source_context_json", "source_context_sha256", "created_at",
    },
    "theme_chokepoint_fact_verification_raw_responses": {
        "response_record_id", "request_record_id", "run_id", "verifier",
        "provider_trace_id", "http_status", "raw_body", "raw_response_sha256",
        "retrieved_at", "cost_usd",
    },
    "theme_chokepoint_fact_verification_results": {
        "result_record_id", "request_record_id", "response_record_id", "run_id",
        "assertion_id", "assertion_sha256", "decision", "parsed_at",
    },
    "theme_chokepoint_fact_verification_reconciliations": {
        "receipt_id", "run_id", "assertion_id", "request_record_id",
        "response_record_id", "result_record_id", "governance_bundle_sha256",
        "source_identity_id", "source_version_id", "source_context_sha256",
        "reconciled_at",
    },
    "theme_chokepoint_monitor_evaluator_requests": {
        "request_record_id", "run_id", "target_type", "target_id",
        "evidence_event_ids_json", "created_at",
    },
    "theme_chokepoint_monitor_evaluator_raw_responses": {
        "response_record_id", "request_record_id", "run_id", "provider",
        "provider_trace_id", "http_status", "raw_body", "raw_response_sha256",
        "retrieved_at", "cost_usd",
    },
    "theme_chokepoint_monitor_evaluator_results": {
        "result_record_id", "request_record_id", "response_record_id", "run_id",
        "target_type", "target_id", "parsed_payload_json", "parsed_at",
    },
    "theme_chokepoint_source_resolution_receipts": {
        "receipt_id", "provider", "provider_trace_id", "http_status", "raw_body",
        "raw_response_sha256", "retrieved_at", "policy_id", "policy_sha256",
        "governance_bundle_id", "governance_bundle_sha256", "receipt_sha256",
        "created_at",
    },
    "theme_chokepoint_source_identities": {
        "source_identity_id", "canonical_publisher_id", "canonical_document_id",
        "origin_event_id", "canonical_url_key", "redirect_terminal_identity_id",
        "redirect_chain_json", "relation_type", "provenance_state",
        "origin_canonical_url", "policy_id", "policy_sha256",
        "governance_bundle_id", "governance_bundle_sha256",
        "resolver_receipt_id", "resolver_receipt_sha256",
        "identity_payload_sha256", "created_at",
    },
    "theme_chokepoint_source_versions": {
        "source_version_id", "source_identity_id", "retrieved_at", "raw_bytes",
        "raw_bytes_sha256", "normalized_content", "normalized_content_sha256",
        "quote_span_json", "quote_span_sha256", "content_type", "language",
        "publication_time", "updated_time", "version_sequence", "created_at",
    },
    "theme_chokepoint_source_usages": {
        "usage_id", "run_id", "stage", "source_snapshot_article_id",
        "source_identity_id", "source_version_id", "created_at",
    },
    "theme_chokepoint_canonical_scoring_requests": {
        "request_record_id", "operation_id", "run_id", "target_type", "target_id",
        "evaluator_result_record_id", "expected_head_revision_id",
        "request_payload_json", "request_payload_sha256", "status", "created_at",
    },
    "theme_chokepoint_canonical_scoring_raw_responses": {
        "response_record_id", "request_record_id", "operation_id", "run_id",
        "provider", "provider_trace_id", "http_status", "raw_body",
        "raw_response_sha256", "retrieved_at", "cost_usd",
    },
    "theme_chokepoint_canonical_scoring_results": {
        "result_record_id", "request_record_id", "response_record_id", "operation_id",
        "run_id", "parsed_payload_json", "parsed_payload_sha256", "parser_version",
        "parsed_at",
    },
    "theme_chokepoint_canonical_scoring_reconciliations": {
        "receipt_id", "request_record_id", "response_record_id", "result_record_id",
        "operation_id", "run_id", "reconciled_at",
    },
    "theme_chokepoint_assessment_revisions": {
        "revision_id", "run_id", "target_type", "target_id", "parent_revision_id",
        "result_reconciliation_id", "payload_json", "payload_sha256", "created_at",
    },
    "theme_chokepoint_assessment_heads": {
        "run_id", "target_type", "target_id", "revision_id", "updated_at",
    },
}

for _role in COMPANY_ROLES:
    _prefix = f"theme_chokepoint_company_{_role}"
    REQUIRED_COLUMNS[f"{_prefix}_requests"] = {
        "request_record_id", "run_id", "company_id", "request_payload_json",
        "request_payload_sha256", "created_at",
    }
    REQUIRED_COLUMNS[f"{_prefix}_raw_responses"] = {
        "response_record_id", "request_record_id", "run_id", "provider",
        "provider_trace_id", "http_status", "raw_body", "raw_response_sha256",
        "retrieved_at", "cost_usd",
    }
    REQUIRED_COLUMNS[f"{_prefix}_results"] = {
        "result_record_id", "request_record_id", "response_record_id", "run_id",
        "parsed_payload_json", "parsed_payload_sha256", "parser_version", "parsed_at",
    }
    REQUIRED_COLUMNS[f"{_prefix}_reconciliations"] = {
        "receipt_id", "request_record_id", "response_record_id", "result_record_id",
        "run_id", "reconciled_at",
    }

REQUIRED_TABLES = set(REQUIRED_COLUMNS)

REQUIRED_INDEXES = {
    "idx_theme_anchor_run": (False, ("run_id", "ordinal")),
    "idx_theme_feedback_run": (False, ("run_id", "created_at", "correction_id")),
    "idx_theme_chokepoint_provider_raw_execution": (
        True,
        ("provider_identity", "raw_response_sha256"),
    ),
    "idx_theme_source_identity_url": (
        False,
        ("canonical_url_key", "relation_type"),
    ),
    "idx_theme_source_resolution_provider_trace": (
        True,
        ("provider", "provider_trace_id"),
    ),
    "idx_theme_source_resolution_provider_raw": (
        True,
        ("provider", "raw_response_sha256"),
    ),
}

REQUIRED_FOREIGN_KEYS = {
    "theme_chokepoint_source_identities": {
        (
            "resolver_receipt_id",
            "theme_chokepoint_source_resolution_receipts",
            "receipt_id",
        )
    },
    "theme_chokepoint_counter_search_raw_responses": {
        ("request_record_id", "theme_chokepoint_counter_search_requests", "request_record_id")
    },
    "theme_chokepoint_counter_search_results": {
        ("request_record_id", "theme_chokepoint_counter_search_requests", "request_record_id"),
        ("response_record_id", "theme_chokepoint_counter_search_raw_responses", "response_record_id"),
    },
    "theme_chokepoint_counter_claims": {
        ("result_record_id", "theme_chokepoint_counter_search_results", "result_record_id"),
        ("source_version_id", "theme_chokepoint_counter_source_versions", "source_version_id"),
    },
    "theme_chokepoint_counter_evidence_cards": {
        ("claim_id", "theme_chokepoint_counter_claims", "claim_id"),
        ("source_version_id", "theme_chokepoint_counter_source_versions", "source_version_id"),
        ("result_record_id", "theme_chokepoint_counter_search_results", "result_record_id"),
    },
    "theme_chokepoint_fact_verification_requests": {
        ("source_identity_id", "theme_chokepoint_source_identities", "source_identity_id"),
        ("source_version_id", "theme_chokepoint_source_versions", "source_version_id"),
    },
    "theme_chokepoint_fact_verification_reconciliations": {
        ("source_identity_id", "theme_chokepoint_source_identities", "source_identity_id"),
        ("source_version_id", "theme_chokepoint_source_versions", "source_version_id"),
    },
    "theme_chokepoint_source_versions": {
        ("source_identity_id", "theme_chokepoint_source_identities", "source_identity_id")
    },
    "theme_chokepoint_source_usages": {
        ("source_identity_id", "theme_chokepoint_source_identities", "source_identity_id"),
        ("source_version_id", "theme_chokepoint_source_versions", "source_version_id"),
    },
    "theme_chokepoint_canonical_scoring_reconciliations": {
        ("request_record_id", "theme_chokepoint_canonical_scoring_requests", "request_record_id"),
        ("response_record_id", "theme_chokepoint_canonical_scoring_raw_responses", "response_record_id"),
        ("result_record_id", "theme_chokepoint_canonical_scoring_results", "result_record_id"),
    },
}

for _role in COMPANY_ROLES:
    _prefix = f"theme_chokepoint_company_{_role}"
    REQUIRED_FOREIGN_KEYS[f"{_prefix}_reconciliations"] = {
        ("request_record_id", f"{_prefix}_requests", "request_record_id"),
        ("response_record_id", f"{_prefix}_raw_responses", "response_record_id"),
        ("result_record_id", f"{_prefix}_results", "result_record_id"),
    }


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _digest(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return sha256(payload).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _create_populated_legacy_schema(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE theme_chokepoint_runs (
                run_id TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                demand_frame_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed_by TEXT,
                confirmed_at TEXT
            );
            CREATE TABLE theme_chokepoint_stage3_results (
                run_id TEXT PRIMARY KEY REFERENCES theme_chokepoint_runs(run_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE theme_chokepoint_stage4_results (
                run_id TEXT PRIMARY KEY REFERENCES theme_chokepoint_runs(run_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE theme_chokepoint_feedback (
                correction_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES theme_chokepoint_runs(run_id),
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                proposed_value TEXT NOT NULL,
                rationale TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        timestamp = "2026-08-17T00:00:00+00:00"
        connection.execute(
            "INSERT INTO theme_chokepoint_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-run-1", '{"theme":"legacy"}', "DRAFT", None, timestamp, timestamp, None, None),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_stage3_results VALUES (?, ?, ?)",
            ("legacy-run-1", '{"legacy_stage":3}', timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_stage4_results VALUES (?, ?, ?)",
            ("legacy-run-1", '{"legacy_stage":4}', timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-feedback-1", "legacy-run-1", "segment", "segment-1", "state", "unknown", "preserve", "migration-fixture", timestamp),
        )


def _legacy_rows_preserved(connection: sqlite3.Connection) -> bool:
    run = connection.execute(
        "SELECT request_json, status FROM theme_chokepoint_runs WHERE run_id = ?",
        ("legacy-run-1",),
    ).fetchone()
    stage3 = connection.execute(
        "SELECT payload_json FROM theme_chokepoint_stage3_results WHERE run_id = ?",
        ("legacy-run-1",),
    ).fetchone()
    stage4 = connection.execute(
        "SELECT payload_json FROM theme_chokepoint_stage4_results WHERE run_id = ?",
        ("legacy-run-1",),
    ).fetchone()
    feedback = connection.execute(
        "SELECT rationale FROM theme_chokepoint_feedback WHERE correction_id = ?",
        ("legacy-feedback-1",),
    ).fetchone()
    return (
        run == ('{"theme":"legacy"}', "DRAFT")
        and stage3 == ('{"legacy_stage":3}',)
        and stage4 == ('{"legacy_stage":4}',)
        and feedback == ("preserve",)
    )


def _schema_contract(connection: sqlite3.Connection) -> dict[str, object]:
    tables = _tables(connection)
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - actual)
        if missing:
            missing_columns[table] = missing

    actual_indexes: dict[str, tuple[bool, tuple[str, ...]]] = {}
    for name, unique in connection.execute(
        "SELECT name, sql LIKE 'CREATE UNIQUE INDEX%' FROM sqlite_master "
        "WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex%'"
    ):
        columns = tuple(
            row[2] for row in connection.execute(f"PRAGMA index_info({name})")
        )
        actual_indexes[name] = (bool(unique), columns)
    missing_indexes = sorted(
        name
        for name, contract in REQUIRED_INDEXES.items()
        if actual_indexes.get(name) != contract
    )

    missing_foreign_keys: dict[str, list[list[str]]] = {}
    for table, required in REQUIRED_FOREIGN_KEYS.items():
        if table not in tables:
            continue
        actual = {
            (row[3], row[2], row[4])
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        missing = sorted(required - actual)
        if missing:
            missing_foreign_keys[table] = [list(item) for item in missing]

    foreign_key_check = [
        list(row) for row in connection.execute("PRAGMA foreign_key_check")
    ]
    return {
        "checked_table_count": len(REQUIRED_TABLES),
        "missing_required_tables": missing_tables,
        "missing_required_columns": missing_columns,
        "missing_required_indexes": missing_indexes,
        "missing_required_foreign_keys": missing_foreign_keys,
        "foreign_key_check": foreign_key_check,
    }


def _insert_representative_rows(database: Path) -> tuple[dict[str, bool], dict[str, bool]]:
    timestamp = "2026-08-17T01:00:00+00:00"
    run_id = "migration-run-1"
    scope_sha = _digest("scope")
    raw = b'{"decision":"accepted"}'
    raw_sha = _digest(raw)
    normalized = "Company reported USD 25 million revenue in Q1 2026 GAAP."
    source_policy_id = "theme-chokepoint-source-identity-policy-v1"
    source_policy_sha = "8bf45510ca03a416f1a3d43105cb5d129547c7368583790d157dc20afae43a69"
    source_governance_id = "theme-chokepoint-evidence-trust-runtime-v1"
    source_governance_sha = "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
    canonical_url = "https://example.test/q1"
    resolver_provider = "source-resolution-provider"
    resolver_trace = "source-resolution-trace-1"
    resolver_raw = _json(
        {
            "observed_url": canonical_url,
            "terminal_url": canonical_url,
            "redirect_chain": [],
            "relation": "original",
            "origin_url": canonical_url,
            "resolution_status": "verified",
        }
    ).encode("utf-8")
    resolver_raw_sha = _digest(resolver_raw)
    resolver_receipt_material = _json(
        {
            "provider": resolver_provider,
            "provider_trace_id": resolver_trace,
            "raw_response_sha256": resolver_raw_sha,
            "policy_id": source_policy_id,
            "policy_sha256": source_policy_sha,
            "governance_bundle_id": source_governance_id,
            "governance_bundle_sha256": source_governance_sha,
        }
    )
    resolver_receipt_sha = _digest(resolver_receipt_material)
    resolver_receipt_id = "source_resolution_" + resolver_receipt_sha[:24]
    identity_payload = {
        "canonical_publisher_id": "example-corp",
        "canonical_document_id": "filing-q1",
        "origin_event_id": "event-q1",
        "canonical_url_key": canonical_url,
        "redirect_chain": [canonical_url],
        "relation_type": "original",
        "provenance_state": "verified",
        "origin_canonical_url": canonical_url,
        "policy_id": source_policy_id,
        "policy_sha256": source_policy_sha,
        "governance_bundle_id": source_governance_id,
        "governance_bundle_sha256": source_governance_sha,
        "resolver_receipt_id": resolver_receipt_id,
        "resolver_receipt_sha256": resolver_receipt_sha,
    }
    identity_payload_sha = _digest(_json(identity_payload))
    source_identity_id = "source_identity_" + identity_payload_sha[:24]
    raw_bytes_sha = _digest(normalized)
    normalized_content_sha = _digest(normalized)
    quote_span_sha = _digest(normalized)
    version_material = {
        "source_identity_id": source_identity_id,
        "retrieved_at": timestamp,
        "raw_bytes_sha256": raw_bytes_sha,
        "normalized_content_sha256": normalized_content_sha,
        "quote_span_sha256": quote_span_sha,
        "version_sequence": 1,
    }
    source_version_id = "source_version_" + _digest(_json(version_material))[:24]
    source_context = _json(
        {
            "source_identity_id": source_identity_id,
            "source_version_id": source_version_id,
            "canonical_url": canonical_url,
            "canonical_publisher_id": "example-corp",
            "canonical_document_id": "filing-q1",
            "origin_event_id": "event-q1",
            "source_relation": "original",
            "source_provenance": "verified",
            "source_identity_policy_id": source_policy_id,
            "source_identity_policy_sha256": source_policy_sha,
            "source_governance_bundle_id": source_governance_id,
            "source_governance_bundle_sha256": source_governance_sha,
            "source_resolver_receipt_id": resolver_receipt_id,
            "source_resolver_receipt_sha256": resolver_receipt_sha,
            "source_type": "company_filing",
            "version_sequence": 1,
            "original_document_bytes_base64": base64.b64encode(
                normalized.encode("utf-8")
            ).decode("ascii"),
            "original_document_text": normalized,
            "raw_bytes_sha256": raw_bytes_sha,
            "normalized_content_sha256": normalized_content_sha,
            "quote_start": 0,
            "quote_end": len(normalized),
            "exact_quote": normalized,
            "exact_quote_sha256": _digest(normalized),
            "issuer_company_id": "example-corp",
            "product_id": "product-1",
            "fiscal_period_id": "Q12026",
            "accounting_metric": "revenue",
        }
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """INSERT INTO theme_chokepoint_source_resolution_receipts(
                receipt_id, provider, provider_trace_id, http_status, raw_body,
                raw_response_sha256, retrieved_at, policy_id, policy_sha256,
                governance_bundle_id, governance_bundle_sha256, receipt_sha256,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resolver_receipt_id, resolver_provider, resolver_trace, 200,
                resolver_raw, resolver_raw_sha, timestamp, source_policy_id,
                source_policy_sha, source_governance_id, source_governance_sha,
                resolver_receipt_sha, timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO theme_chokepoint_source_identities(
                source_identity_id, canonical_publisher_id, canonical_document_id,
                origin_event_id, canonical_url_key, redirect_terminal_identity_id,
                redirect_chain_json, relation_type, provenance_state,
                origin_canonical_url, policy_id, policy_sha256,
                governance_bundle_id, governance_bundle_sha256,
                resolver_receipt_id, resolver_receipt_sha256,
                identity_payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_identity_id, "example-corp", "filing-q1", "event-q1", canonical_url, None, _json([canonical_url]), "original", "verified", canonical_url, source_policy_id, source_policy_sha, source_governance_id, source_governance_sha, resolver_receipt_id, resolver_receipt_sha, identity_payload_sha, timestamp),
        )
        connection.execute(
            """INSERT INTO theme_chokepoint_source_versions(
                source_version_id, source_identity_id, retrieved_at, raw_bytes,
                raw_bytes_sha256, normalized_content, normalized_content_sha256,
                quote_span_json, quote_span_sha256, content_type, language,
                publication_time, updated_time, version_sequence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_version_id, source_identity_id, timestamp, normalized.encode(), raw_bytes_sha, normalized, normalized_content_sha, _json([0, len(normalized), normalized]), quote_span_sha, "text/plain", "en", timestamp, None, 1, timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_source_usages VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("source-usage-1", run_id, "stage4", "article-1", source_identity_id, source_version_id, timestamp),
        )

        connection.execute(
            """INSERT INTO theme_chokepoint_counter_search_requests(
                request_record_id, run_id, route_id, query, assessment_as_of,
                created_at, segment_id, assessment_scope_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("counter-request-1", run_id, "route-1", "counter query", "2026-08-17", timestamp, "segment-1", scope_sha),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_provider_executions(provider_identity, provider_account_or_route_identity, upstream_trace_id, raw_response_sha256, consumer_kind, consumer_record_id, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("counter-provider", "account-1", "upstream-trace-1", raw_sha, "counter_search", "counter-response-1", timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_counter_search_raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("counter-response-1", "counter-request-1", run_id, "counter-provider", "upstream-trace-1", 200, raw, raw_sha, timestamp, 0.01),
        )
        connection.execute(
            """INSERT INTO theme_chokepoint_counter_search_results(
                result_record_id, response_record_id, request_record_id, run_id, route_id,
                query, status, evidence_ids_json, new_counter_evidence_ids_json,
                finding, query_log_id, result_semantics, parsed_at, coverage_state,
                segment_id, assessment_scope_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("counter-result-1", "counter-response-1", "counter-request-1", run_id, "route-1", "counter query", "COMPLETED", '["counter-card-1"]', '["counter-card-1"]', "explicit alternative", "query-log-1", "explicit_negative", timestamp, "covered", "segment-1", scope_sha),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_counter_source_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("counter-source-version-1", run_id, "segment-1", "route-1", "counter-result-1", "https://example.test/counter", _digest("counter text"), "counter text", timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_counter_claims VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("counter-claim-1", "counter-result-1", "counter-source-version-1", run_id, "segment-1", "route-1", "counter statement", _digest("counter statement")),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_counter_evidence_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("counter-card-1", "counter-claim-1", "counter-source-version-1", "counter-result-1", run_id, "segment-1", "route-1", scope_sha, '{"semantics":"explicit_negative"}'),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_counter_search_reconciliations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("counter-receipt-1", "counter-request-1", "counter-response-1", "counter-result-1", run_id, "segment-1", "route-1", scope_sha, "covered", "accepted", timestamp),
        )

        assertion_sha = _digest(normalized)
        context_sha = _digest(source_context)
        bundle_sha = _digest("runtime-governance-bundle")
        connection.execute(
            """INSERT INTO theme_chokepoint_fact_verification_requests(
                request_record_id, run_id, assertion_id, evidence_id, assertion_sha256,
                governance_bundle_sha256, producer_execution_id, verifier_execution_id,
                source_identity_id, source_version_id, source_context_json,
                source_context_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("fact-request-1", run_id, "assertion-1", "evidence-1", assertion_sha, bundle_sha, "producer-1", "verifier-1", source_identity_id, source_version_id, source_context, context_sha, timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_fact_verification_raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("fact-response-1", "fact-request-1", run_id, "independent-verifier", "fact-trace-1", 200, raw, raw_sha, timestamp, 0.02),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_fact_verification_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("fact-result-1", "fact-request-1", "fact-response-1", run_id, "assertion-1", assertion_sha, "accepted", timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_fact_verification_reconciliations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("fact-receipt-1", run_id, "assertion-1", "fact-request-1", "fact-response-1", "fact-result-1", bundle_sha, source_identity_id, source_version_id, context_sha, timestamp),
        )

        connection.execute(
            "INSERT INTO theme_chokepoint_monitor_evaluator_requests VALUES (?, ?, ?, ?, ?, ?)",
            ("monitor-request-1", run_id, "segment", "segment-1", '["event-1"]', timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_monitor_evaluator_raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("monitor-response-1", "monitor-request-1", run_id, "monitor-provider", "monitor-trace-1", 200, raw, raw_sha, timestamp, 0.01),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_monitor_evaluator_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("monitor-result-1", "monitor-request-1", "monitor-response-1", run_id, "segment", "segment-1", '{"new_evidence_ids":["event-1"]}', timestamp),
        )

        scoring_request = '{"evidence_ids":["event-1"]}'
        scoring_result = '{"state":"unknown","score":[0,4],"gate_level":0}'
        connection.execute(
            "INSERT INTO theme_chokepoint_canonical_scoring_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("score-request-1", "operation-1", run_id, "segment", "segment-1", "monitor-result-1", "revision-before-1", scoring_request, _digest(scoring_request), "COMPLETED", timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_canonical_scoring_raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("score-response-1", "score-request-1", "operation-1", run_id, "canonical-stage3", "score-trace-1", 200, scoring_result.encode(), _digest(scoring_result), timestamp, 0.0),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_canonical_scoring_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("score-result-1", "score-request-1", "score-response-1", "operation-1", run_id, scoring_result, _digest(scoring_result), "canonical-v1", timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_canonical_scoring_reconciliations VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("score-receipt-1", "score-request-1", "score-response-1", "score-result-1", "operation-1", run_id, timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_assessment_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("revision-after-1", run_id, "segment", "segment-1", "revision-before-1", "score-receipt-1", scoring_result, _digest(scoring_result), timestamp),
        )
        connection.execute(
            "INSERT INTO theme_chokepoint_assessment_heads VALUES (?, ?, ?, ?, ?)",
            (run_id, "segment", "segment-1", "revision-after-1", timestamp),
        )

        for role in COMPANY_ROLES:
            prefix = f"theme_chokepoint_company_{role}"
            request_id = f"company-{role}-request-1"
            response_id = f"company-{role}-response-1"
            result_id = f"company-{role}-result-1"
            payload = json.dumps({"role": role}, sort_keys=True, separators=(",", ":"))
            connection.execute(
                f"INSERT INTO {prefix}_requests VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, run_id, "company-1", payload, _digest(payload), timestamp),
            )
            connection.execute(
                f"INSERT INTO {prefix}_raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (response_id, request_id, run_id, f"{role}-provider", f"{role}-trace-1", 200, payload.encode(), _digest(payload), timestamp, 0.01),
            )
            connection.execute(
                f"INSERT INTO {prefix}_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, request_id, response_id, run_id, payload, _digest(payload), "parser-v1", timestamp),
            )
            connection.execute(
                f"INSERT INTO {prefix}_reconciliations VALUES (?, ?, ?, ?, ?, ?)",
                (f"company-{role}-receipt-1", request_id, response_id, result_id, run_id, timestamp),
            )
        connection.commit()

        round_trips = {
            "source_resolution_receipt": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_source_identities i JOIN theme_chokepoint_source_resolution_receipts r ON r.receipt_id=i.resolver_receipt_id"
            ).fetchone()[0] == 1,
            "source_lineage": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_source_usages u JOIN theme_chokepoint_source_versions v ON v.source_version_id=u.source_version_id JOIN theme_chokepoint_source_identities i ON i.source_identity_id=v.source_identity_id"
            ).fetchone()[0] == 1,
            "counter_evidence": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_counter_search_reconciliations r JOIN theme_chokepoint_counter_evidence_cards c ON c.result_record_id=r.result_record_id JOIN theme_chokepoint_counter_claims cl ON cl.claim_id=c.claim_id JOIN theme_chokepoint_counter_source_versions v ON v.source_version_id=cl.source_version_id"
            ).fetchone()[0] == 1,
            "fact_verification": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_fact_verification_reconciliations r JOIN theme_chokepoint_fact_verification_requests q ON q.request_record_id=r.request_record_id JOIN theme_chokepoint_source_versions v ON v.source_version_id=q.source_version_id"
            ).fetchone()[0] == 1,
            "monitor_evaluator": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_monitor_evaluator_results r JOIN theme_chokepoint_monitor_evaluator_raw_responses raw ON raw.response_record_id=r.response_record_id JOIN theme_chokepoint_monitor_evaluator_requests q ON q.request_record_id=r.request_record_id"
            ).fetchone()[0] == 1,
            "canonical_scoring": connection.execute(
                "SELECT COUNT(*) FROM theme_chokepoint_assessment_heads h JOIN theme_chokepoint_assessment_revisions v ON v.revision_id=h.revision_id JOIN theme_chokepoint_canonical_scoring_reconciliations r ON r.receipt_id=v.result_reconciliation_id"
            ).fetchone()[0] == 1,
            "company_provider": sum(
                connection.execute(
                    f"SELECT COUNT(*) FROM theme_chokepoint_company_{role}_reconciliations"
                ).fetchone()[0]
                for role in COMPANY_ROLES
            ) == 4,
        }

        def rejected(params: tuple[object, ...]) -> bool:
            try:
                connection.execute(
                    "INSERT INTO theme_chokepoint_provider_executions(provider_identity, provider_account_or_route_identity, upstream_trace_id, raw_response_sha256, consumer_kind, consumer_record_id, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                return True
            return False

        def rejected_resolution(params: tuple[object, ...]) -> bool:
            try:
                connection.execute(
                    "INSERT INTO theme_chokepoint_source_resolution_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                return True
            return False

        uniqueness = {
            "provider_execution_identity_replay_rejected": rejected(
                ("counter-provider", "account-1", "upstream-trace-1", _digest("different raw"), "counter_search", "counter-response-duplicate-trace", timestamp)
            ),
            "counter_raw_execution_replay_rejected": rejected(
                ("counter-provider", "account-1", "upstream-trace-2", raw_sha, "counter_search", "counter-response-duplicate-raw", timestamp)
            ),
            "source_resolution_trace_replay_rejected": rejected_resolution(
                (
                    "source-resolution-duplicate-trace", resolver_provider,
                    resolver_trace, 200, b'{"different":true}',
                    _digest(b'{"different":true}'), timestamp, source_policy_id,
                    source_policy_sha, source_governance_id, source_governance_sha,
                    _digest("different receipt"), timestamp,
                )
            ),
            "source_resolution_raw_replay_rejected": rejected_resolution(
                (
                    "source-resolution-duplicate-raw", resolver_provider,
                    "source-resolution-trace-2", 200, resolver_raw,
                    resolver_raw_sha, timestamp, source_policy_id,
                    source_policy_sha, source_governance_id, source_governance_sha,
                    _digest("other receipt"), timestamp,
                )
            ),
        }
        return round_trips, uniqueness


def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="theme-evidence-trust-migration-", ignore_cleanup_errors=True
    ) as temp:
        root = Path(temp)
        database = root / "legacy.db"
        backup = root / "legacy.backup.db"
        _create_populated_legacy_schema(database)
        shutil.copy2(database, backup)
        before_sha256 = _sha256(database)
        with sqlite3.connect(database) as connection:
            missing_before = sorted(REQUIRED_TABLES - _tables(connection))

        ThemeChokepointRepository(database)
        round_trips, uniqueness = _insert_representative_rows(database)
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            legacy_preserved = _legacy_rows_preserved(connection)
            schema_contract = _schema_contract(connection)
        applied_sha256 = _sha256(database)

        shutil.copy2(backup, database)
        rollback_sha256 = _sha256(database)
        with sqlite3.connect(database) as connection:
            rollback_tables = _tables(connection)
            rollback_legacy_preserved = _legacy_rows_preserved(connection)

        report = {
            "schema_version": "theme-chokepoint-evidence-trust-migration-verification-v2",
            "dry_run": {
                "missing_required_tables": missing_before,
                "would_apply": bool(missing_before),
                "legacy_theme_tables_present": sorted(
                    LEGACY_THEME_TABLES & rollback_tables
                ),
            },
            "apply": {
                "integrity_check": integrity,
                "legacy_theme_rows_preserved": legacy_preserved,
                "before_sha256": before_sha256,
                "applied_sha256": applied_sha256,
            },
            "schema_contract": schema_contract,
            "representative_round_trips": round_trips,
            "uniqueness_probes": uniqueness,
            "rollback": {
                "restored_original_sha256": rollback_sha256 == before_sha256,
                "new_required_tables_absent_again": not bool(
                    (REQUIRED_TABLES - LEGACY_THEME_TABLES) & rollback_tables
                ),
                "legacy_theme_rows_preserved": rollback_legacy_preserved,
            },
        }
        valid = (
            bool(missing_before)
            and integrity == "ok"
            and legacy_preserved
            and not schema_contract["missing_required_tables"]
            and not schema_contract["missing_required_columns"]
            and not schema_contract["missing_required_indexes"]
            and not schema_contract["missing_required_foreign_keys"]
            and not schema_contract["foreign_key_check"]
            and all(round_trips.values())
            and all(uniqueness.values())
            and report["rollback"]["restored_original_sha256"]
            and report["rollback"]["new_required_tables_absent_again"]
            and rollback_legacy_preserved
        )
        report["valid"] = valid
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        if not valid:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
