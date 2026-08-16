"""Read-only legacy import planning with deterministic alias validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from event_collector.kb_repository import SQLiteKBRepository


_PROHIBITED_GENERIC = {"about", "company", "inc", "nyse"}


@dataclass(frozen=True)
class LegacyCompanyRecord:
    symbol: str
    canonical_name: str
    aliases: tuple[str, ...]
    canonical_evidence_id: str


@dataclass(frozen=True)
class ImportedEntity:
    entity_id: str
    canonical_name: str
    canonical_evidence_id: str


@dataclass(frozen=True)
class ImportedAlias:
    alias_id: str
    entity_id: str
    display_value: str
    normalized_value: str
    alias_type: str
    language: str
    match_policy: str
    evidence_id: str


@dataclass(frozen=True)
class ImportFinding:
    finding_id: str
    code: str
    object_value: str
    message: str


@dataclass(frozen=True)
class LegacyImportPlan:
    snapshot_id: str
    namespace: str
    source_manifest_hash: str
    schema_version: str
    entities: tuple[ImportedEntity, ...]
    aliases: tuple[ImportedAlias, ...]
    findings: tuple[ImportFinding, ...]
    content_checksum: str


def read_legacy_company_records(database: str | Path) -> tuple[LegacyCompanyRecord, ...]:
    path = Path(database).resolve()
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT symbol, canonical_name, aliases_json, canonical_evidence_id
            FROM companies ORDER BY symbol
            """
        ).fetchall()
    finally:
        connection.close()
    records = []
    for row in rows:
        aliases = json.loads(row["aliases_json"])
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ValueError(f"legacy aliases for {row['symbol']} must be a string array")
        records.append(
            LegacyCompanyRecord(
                symbol=row["symbol"],
                canonical_name=row["canonical_name"],
                aliases=tuple(aliases),
                canonical_evidence_id=row["canonical_evidence_id"],
            )
        )
    return tuple(records)


def build_legacy_import_plan(
    records: Iterable[LegacyCompanyRecord],
    *,
    snapshot_id: str,
    namespace: str,
    source_manifest_hash: str,
) -> LegacyImportPlan:
    entities: list[ImportedEntity] = []
    aliases: list[ImportedAlias] = []
    findings: list[ImportFinding] = []
    for record in sorted(records, key=lambda item: item.symbol):
        entity_id = f"entity:company:{record.symbol.lower()}"
        entities.append(
            ImportedEntity(
                entity_id=entity_id,
                canonical_name=record.canonical_name.strip(),
                canonical_evidence_id=record.canonical_evidence_id,
            )
        )
        candidates = (
            (record.symbol, "ticker"),
            *((value, "common_name") for value in record.aliases),
        )
        for display, alias_type in candidates:
            normalized = _normalize(display)
            code = _alias_finding_code(display, normalized)
            if code is not None:
                findings.append(
                    ImportFinding(
                        finding_id=_stable_id("finding", entity_id, display, code),
                        code=code,
                        object_value=display,
                        message=f"alias {display!r} rejected by {code}",
                    )
                )
                continue
            aliases.append(
                ImportedAlias(
                    alias_id=_stable_id("alias", entity_id, alias_type, normalized),
                    entity_id=entity_id,
                    display_value=display,
                    normalized_value=normalized,
                    alias_type=alias_type,
                    language="zh-CN" if _contains_cjk(display) else "en",
                    match_policy="exact_ticker" if alias_type == "ticker" else "exact_phrase",
                    evidence_id=record.canonical_evidence_id,
                )
            )
    entities_tuple = tuple(entities)
    aliases_tuple = tuple(sorted(aliases, key=lambda item: item.alias_id))
    findings_tuple = tuple(sorted(findings, key=lambda item: item.finding_id))
    content = {
        "snapshot_id": snapshot_id,
        "namespace": namespace,
        "source_manifest_hash": source_manifest_hash,
        "schema_version": "2.0",
        "entities": [asdict(item) for item in entities_tuple],
        "aliases": [asdict(item) for item in aliases_tuple],
        "findings": [asdict(item) for item in findings_tuple],
    }
    checksum = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return LegacyImportPlan(
        snapshot_id=snapshot_id,
        namespace=namespace,
        source_manifest_hash=source_manifest_hash,
        schema_version="2.0",
        entities=entities_tuple,
        aliases=aliases_tuple,
        findings=findings_tuple,
        content_checksum=checksum,
    )


def apply_legacy_import_plan(
    repository: SQLiteKBRepository,
    plan: LegacyImportPlan,
) -> str:
    return repository.apply_draft_import(
        snapshot={
            "snapshot_id": plan.snapshot_id,
            "namespace": plan.namespace,
            "source_manifest_hash": plan.source_manifest_hash,
            "schema_version": plan.schema_version,
            "content_checksum": plan.content_checksum,
        },
        entities=(
            {"snapshot_id": plan.snapshot_id, **asdict(entity)} for entity in plan.entities
        ),
        aliases=(
            {
                "snapshot_id": plan.snapshot_id,
                "alias_id": alias.alias_id,
                "entity_id": alias.entity_id,
                "alias": alias.display_value,
                "normalized_alias": alias.normalized_value,
                "alias_type": alias.alias_type,
                "language": alias.language,
                "match_policy": alias.match_policy,
                "evidence_id": alias.evidence_id,
            }
            for alias in plan.aliases
        ),
        findings=(
            {
                "finding_id": finding.finding_id,
                "snapshot_id": plan.snapshot_id,
                "code": finding.code,
                "message": finding.message,
                "details_json": json.dumps({"object_value": finding.object_value}),
                "created_at": "1970-01-01T00:00:00Z",
            }
            for finding in plan.findings
        ),
    )


def _alias_finding_code(display: str, normalized: str) -> str | None:
    if not normalized:
        return "ALIAS_NORMALIZATION_EMPTY"
    if normalized in _PROHIBITED_GENERIC:
        return "ALIAS_PROHIBITED_GENERIC"
    if display.endswith(("…", "...")):
        return "ALIAS_TRUNCATED"
    return None


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _stable_id(prefix: str, *values: str) -> str:
    canonical = "\x1f".join(values)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


__all__ = [
    "ImportFinding",
    "ImportedAlias",
    "ImportedEntity",
    "LegacyCompanyRecord",
    "LegacyImportPlan",
    "apply_legacy_import_plan",
    "build_legacy_import_plan",
    "read_legacy_company_records",
]
