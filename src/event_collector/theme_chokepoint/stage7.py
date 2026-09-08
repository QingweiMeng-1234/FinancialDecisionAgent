"""Stage 7: dated numeric signal export with optional, separate quant validation."""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from typing import Protocol

from event_collector.theme_chokepoint.contracts import (
    ArtifactFile,
    RunStatus,
    SignalExportManifest,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


CSV_FIELDS = (
    "snapshot_date",
    "run_id",
    "entity_type",
    "entity_id",
    "signal_name",
    "signal_value",
    "scoring_contract",
    "executable_contract_id",
    "executable_contract_sha256",
)
RESEARCH_FILES = (
    "signals.csv",
    "signals.parquet",
    "signal-definitions.json",
    "signal-lineage.jsonl",
)


class QuantValidator(Protocol):
    def validate(self, *, csv_path: Path, parquet_path: Path, definitions_path: Path) -> dict: ...


class SignalExportService:
    def __init__(
        self,
        repository: ThemeChokepointRepository,
        export_root: str | Path,
        *,
        quant_validator: QuantValidator | None = None,
    ):
        self.repository = repository
        self.export_root = Path(export_root)
        self.quant_validator = quant_validator

    def export(self, run_id: str) -> SignalExportManifest:
        status = self.repository.get_run(run_id).status
        allowed = {
            RunStatus.PERSISTENT_RESEARCH_READY,
            RunStatus.MONITORING_READY,
            RunStatus.SIGNAL_EXPORT_READY,
        }
        if status not in allowed:
            raise ValueError("signal export requires a persistent Stage 5 research product")
        target = self.export_root / run_id
        if target.exists():
            manifest = _load_and_verify_manifest(target)
        else:
            manifest = self._write_export(run_id, target)
        if status is not RunStatus.SIGNAL_EXPORT_READY:
            self.repository.mark_signal_export_ready(run_id)
        return manifest

    def _write_export(self, run_id: str, target: Path) -> SignalExportManifest:
        run = self.repository.get_run(run_id)
        stage3 = self.repository.get_stage3_result(run_id)
        stage4 = self.repository.get_stage4_result(run_id)
        if (
            stage3.executable_contract_id != stage4.executable_contract_id
            or stage3.executable_contract_sha256
            != stage4.executable_contract_sha256
        ):
            raise ValueError("Stage 7 executable scoring contract lineage mismatch")
        rows, lineage = _build_signal_rows(run, stage3, stage4)
        definitions = _signal_definitions()
        undeclared = {row["signal_name"] for row in rows} - set(definitions)
        if undeclared:
            raise ValueError(f"signal definition missing for: {sorted(undeclared)[0]}")
        self.export_root.mkdir(parents=True, exist_ok=True)
        temp_path = Path(
            tempfile.mkdtemp(prefix=f".{run_id}-signals-", dir=str(self.export_root))
        )
        try:
            _write_csv(temp_path / "signals.csv", rows)
            _write_parquet(temp_path / "signals.parquet", rows)
            (temp_path / "signal-definitions.json").write_text(
                json.dumps(definitions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (temp_path / "signal-lineage.jsonl").write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                    for item in lineage
                ),
                encoding="utf-8",
            )
            quant_path = None
            if self.quant_validator is not None:
                validation = self.quant_validator.validate(
                    csv_path=temp_path / "signals.csv",
                    parquet_path=temp_path / "signals.parquet",
                    definitions_path=temp_path / "signal-definitions.json",
                )
                quant_dir = temp_path / "quant-validation"
                quant_dir.mkdir()
                (quant_dir / "validation.json").write_text(
                    json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                quant_path = "quant-validation/validation.json"
            files = tuple(_artifact_file(temp_path / name, name) for name in RESEARCH_FILES)
            if quant_path:
                files = (*files, _artifact_file(temp_path / quant_path, quant_path))
            manifest = SignalExportManifest(
                run_id=run_id,
                executable_contract_id=stage4.executable_contract_id,
                executable_contract_sha256=stage4.executable_contract_sha256,
                csv_path="signals.csv",
                parquet_path="signals.parquet",
                definitions_path="signal-definitions.json",
                lineage_path="signal-lineage.jsonl",
                quant_validation_path=quant_path,
                files=tuple(files),
                created_at=datetime.now(timezone.utc),
            )
            (temp_path / "signal-export-manifest.json").write_text(
                json.dumps(_jsonable(asdict(manifest)), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            target.mkdir()
            for name in RESEARCH_FILES:
                shutil.copy2(temp_path / name, target / name)
            if quant_path:
                quant_target = target / "quant-validation"
                quant_target.mkdir()
                shutil.copy2(temp_path / quant_path, target / quant_path)
            shutil.copy2(
                temp_path / "signal-export-manifest.json",
                target / "signal-export-manifest.json",
            )
            shutil.rmtree(temp_path)
            return manifest
        except Exception:
            if target.exists() and not (target / "signal-export-manifest.json").exists():
                shutil.rmtree(target)
            if temp_path.exists():
                shutil.rmtree(temp_path)
            raise


def _build_signal_rows(run, stage3, stage4):
    rows = []
    lineage = []
    snapshot_date = run.request.as_of_date.isoformat()
    contract = stage4.contract_version
    executable_contract_id = stage4.executable_contract_id
    executable_contract_sha256 = stage4.executable_contract_sha256
    for item in stage3.assessments:
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for dimension in item.dimensions
                for evidence_id in dimension.evidence_ids
            )
        )
        for name, value in (
            ("segment_score_min", item.score_min),
            ("segment_score_max", item.score_max),
            ("segment_decision_coverage", item.decision_coverage),
        ):
            _append_signal(
                rows,
                lineage,
                snapshot_date,
                run.run_id,
                "segment",
                item.segment_id,
                name,
                value,
                contract,
                executable_contract_id,
                executable_contract_sha256,
                (item.segment_id,),
                evidence_ids,
            )
    for company in stage4.company_assessments:
        for family_name, assessment in (
            ("defensibility", company.defensibility),
            ("replacement", company.replacement),
            ("earnings", company.earnings),
        ):
            if assessment is None:
                continue
            evidence_ids = tuple(
                dict.fromkeys(
                    evidence_id
                    for dimension in assessment.dimensions
                    for evidence_id in dimension.evidence_ids
                )
            )
            for suffix, value in (
                ("score_min", assessment.score_min),
                ("score_max", assessment.score_max),
                ("decision_coverage", assessment.decision_coverage),
            ):
                _append_signal(
                    rows,
                    lineage,
                    company.scope.as_of_date.isoformat(),
                    run.run_id,
                    "company",
                    company.assessment_id,
                    f"{family_name}_{suffix}",
                    value,
                    contract,
                    executable_contract_id,
                    executable_contract_sha256,
                    (company.assessment_id,),
                    evidence_ids,
                )
    return rows, lineage


def _append_signal(
    rows,
    lineage,
    snapshot_date,
    run_id,
    entity_type,
    entity_id,
    signal_name,
    value,
    contract,
    executable_contract_id,
    executable_contract_sha256,
    source_object_ids,
    evidence_ids,
):
    row = {
        "snapshot_date": snapshot_date,
        "run_id": run_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "signal_name": signal_name,
        "signal_value": float(value),
        "scoring_contract": contract,
        "executable_contract_id": executable_contract_id,
        "executable_contract_sha256": executable_contract_sha256,
    }
    rows.append(row)
    lineage.append(
        {
            "row_number": len(rows),
            "run_id": run_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "signal_name": signal_name,
            "source_object_ids": list(source_object_ids),
            "evidence_ids": list(evidence_ids),
            "scoring_contract": contract,
            "executable_contract_id": executable_contract_id,
            "executable_contract_sha256": executable_contract_sha256,
        }
    )


def _signal_definitions():
    definitions = {
        "segment_score_min": {
            "formula": "sum(weight_d * rating_min_d / 4)",
            "source_fields": ["segment.dimensions[].weight", "segment.dimensions[].rating_min"],
            "interpretation": "Supported lower bound of the segment chokepoint score; not a return forecast.",
        },
        "segment_score_max": {
            "formula": "sum(weight_d * rating_max_d / 4)",
            "source_fields": ["segment.dimensions[].weight", "segment.dimensions[].rating_max"],
            "interpretation": "Evidence-bounded upper score; not an upside target.",
        },
        "segment_decision_coverage": {
            "formula": "sum(weight_d * resolved_credit_d * resolution_credit_d) / 100",
            "source_fields": ["segment.dimensions[].evidence_state", "segment.dimensions[].rating_interval"],
            "interpretation": "How much weighted decision precision is supported.",
        },
    }
    for family in ("defensibility", "replacement", "earnings"):
        definitions[f"{family}_score_min"] = {
            "formula": "sum(weight_d * rating_min_d / 4)",
            "source_fields": [f"company.{family}.dimensions[].weight", f"company.{family}.dimensions[].rating_min"],
            "interpretation": f"Supported lower bound for {family}; not an investment recommendation.",
        }
        definitions[f"{family}_score_max"] = {
            "formula": "sum(weight_d * rating_max_d / 4)",
            "source_fields": [f"company.{family}.dimensions[].weight", f"company.{family}.dimensions[].rating_max"],
            "interpretation": f"Evidence-bounded upper score for {family}.",
        }
        definitions[f"{family}_decision_coverage"] = {
            "formula": "sum(weight_d * resolved_credit_d * resolution_credit_d) / 100",
            "source_fields": [f"company.{family}.dimensions[].evidence_state", f"company.{family}.dimensions[].rating_interval"],
            "interpretation": f"Weighted decision precision for {family}.",
        }
    return definitions


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_parquet(path, rows):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - environment dependency gate
        raise RuntimeError("Parquet export requires pyarrow>=14.0") from error
    schema = pa.schema(
        [
            ("snapshot_date", pa.string()),
            ("run_id", pa.string()),
            ("entity_type", pa.string()),
            ("entity_id", pa.string()),
            ("signal_name", pa.string()),
            ("signal_value", pa.float64()),
            ("scoring_contract", pa.string()),
            ("executable_contract_id", pa.string()),
            ("executable_contract_sha256", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")


def _artifact_file(path: Path, name: str) -> ArtifactFile:
    data = path.read_bytes()
    return ArtifactFile(name, "sha256:" + sha256(data).hexdigest(), len(data))


def _load_and_verify_manifest(target: Path) -> SignalExportManifest:
    path = target / "signal-export-manifest.json"
    if not path.is_file():
        raise ValueError("signal export manifest is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = tuple(ArtifactFile(**item) for item in payload["files"])
    for item in files:
        artifact = target / item.name
        if not artifact.is_file():
            raise ValueError(f"signal export file is missing: {item.name}")
        data = artifact.read_bytes()
        if "sha256:" + sha256(data).hexdigest() != item.sha256 or len(data) != item.size_bytes:
            raise ValueError(f"signal export hash mismatch: {item.name}")
    return SignalExportManifest(
        run_id=payload["run_id"],
        executable_contract_id=payload["executable_contract_id"],
        executable_contract_sha256=payload["executable_contract_sha256"],
        csv_path=payload["csv_path"],
        parquet_path=payload["parquet_path"],
        definitions_path=payload["definitions_path"],
        lineage_path=payload["lineage_path"],
        quant_validation_path=payload["quant_validation_path"],
        files=files,
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
