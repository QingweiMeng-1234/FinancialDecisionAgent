from __future__ import annotations

import csv
import json
from pathlib import Path

import pyarrow.parquet as pq

from event_collector.theme_chokepoint.contracts import RunStatus
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.stage7 import SignalExportService
from test_theme_chokepoint_stage5 import _ready_stage4


class RecordingQuantValidator:
    def __init__(self):
        self.calls = []

    def validate(self, *, csv_path, parquet_path, definitions_path):
        self.calls.append((csv_path, parquet_path, definitions_path))
        return {
            "status": "insufficient_history",
            "reason": "One dated cross-section is not a backtest.",
            "information_coefficient": None,
        }


def test_stage7_exports_typed_csv_and_real_parquet_with_formula_and_lineage(tmp_path):
    """SELECT INVARIANT: every numeric signal is dated, defined and evidence-traceable."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    exporter = SignalExportService(repository, tmp_path / "signal-exports")

    manifest = exporter.export(request.run_id)

    export_dir = tmp_path / "signal-exports" / request.run_id
    with (export_dir / "signals.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    table = pq.read_table(export_dir / "signals.parquet")
    parquet_rows = table.to_pylist()
    definitions = json.loads((export_dir / "signal-definitions.json").read_text("utf-8"))
    lineage = [
        json.loads(line)
        for line in (export_dir / "signal-lineage.jsonl").read_text("utf-8").splitlines()
    ]

    assert manifest.run_id == request.run_id
    assert csv_rows
    assert len(csv_rows) == len(parquet_rows) == len(lineage)
    assert set(csv_rows[0]) == {
        "snapshot_date",
        "run_id",
        "entity_type",
        "entity_id",
        "signal_name",
        "signal_value",
        "scoring_contract",
        "executable_contract_id",
        "executable_contract_sha256",
    }
    assert str(table.schema.field("signal_value").type) == "double"
    assert all(row["snapshot_date"] == "2026-08-16" for row in csv_rows)
    assert all(float(row["signal_value"]) == row["signal_value"] for row in parquet_rows)
    assert all(
        row["executable_contract_id"]
        == "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
        for row in parquet_rows
    )
    assert all(
        row["executable_contract_sha256"]
        == "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        for row in parquet_rows
    )
    assert "segment_score_min" in definitions
    assert definitions["segment_score_min"]["formula"] == "sum(weight_d * rating_min_d / 4)"
    assert definitions["segment_score_min"]["source_fields"]
    assert all(item["source_object_ids"] for item in lineage)
    assert all(
        item["executable_contract_sha256"]
        == "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        for item in lineage
    )
    assert manifest.executable_contract_id == (
        "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
    )
    assert manifest.executable_contract_sha256 == (
        "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
    )
    assert all("raw_prose" not in item for item in csv_rows)
    assert repository.get_run(request.run_id).status is RunStatus.SIGNAL_EXPORT_READY
    assert PersistentResearchProductService(
        repository, tmp_path / "artifacts"
    ).finalize(request.run_id).run_id == request.run_id


def test_stage7_keeps_optional_quant_validation_outside_research_signal_files(tmp_path):
    """SELECT INVARIANT: QuantGPT output cannot rewrite or masquerade as research evidence."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    validator = RecordingQuantValidator()
    exporter = SignalExportService(
        repository, tmp_path / "signal-exports", quant_validator=validator
    )

    manifest = exporter.export(request.run_id)

    export_dir = tmp_path / "signal-exports" / request.run_id
    assert len(validator.calls) == 1
    assert manifest.quant_validation_path == "quant-validation/validation.json"
    validation = json.loads(
        (export_dir / manifest.quant_validation_path).read_text(encoding="utf-8")
    )
    assert validation["status"] == "insufficient_history"
    assert validation["information_coefficient"] is None
    assert "quant_validation" not in json.loads(
        (export_dir / "signal-definitions.json").read_text(encoding="utf-8")
    )
    assert exporter.export(request.run_id) == manifest


def test_stage7_publish_does_not_depend_on_windows_directory_rename(tmp_path, monkeypatch):
    """SELECT INVARIANT: the manifest is the commit boundary on Windows."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    monkeypatch.setattr(
        Path,
        "rename",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("directory rename must not be used")
        ),
    )

    manifest = SignalExportService(
        repository, tmp_path / "signal-exports"
    ).export(request.run_id)

    export_dir = tmp_path / "signal-exports" / request.run_id
    assert manifest.run_id == request.run_id
    assert (export_dir / "signal-export-manifest.json").is_file()
