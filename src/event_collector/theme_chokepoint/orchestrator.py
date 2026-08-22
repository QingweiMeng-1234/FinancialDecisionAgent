"""Root Stage 1-7 orchestration with an auditable, same-run manifest."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path

from event_collector.theme_chokepoint.contracts import RunStatus


_REQUIRED_STAGE_RECEIPTS_BY_STATUS = {
    RunStatus.SUPPLY_CHAIN_GRAPH_READY: (2,),
    RunStatus.CHOKEPOINT_ASSESSMENT_READY: (2, 3),
    RunStatus.INCOMPLETE_BUDGET_EXHAUSTED: (2, 3),
    RunStatus.COMPANY_ASSESSMENT_INCOMPLETE: (2, 3, 4),
    RunStatus.COMPANY_ASSESSMENT_READY: (2, 3, 4),
    RunStatus.PERSISTENT_RESEARCH_READY: (2, 3, 4, 5),
    RunStatus.MONITORING_READY: (2, 3, 4, 5, 6),
    RunStatus.SIGNAL_EXPORT_READY: (2, 3, 4, 5, 6, 7),
}


@dataclass(frozen=True)
class MonitoringStageOutcome:
    run_id: str
    status: RunStatus
    trigger_count: int
    change_count: int
    outcome: str


class NoEventsMonitoringStage:
    """Stage 6 adapter for a scheduled run in which no new event batch was supplied."""

    def __init__(self, repository):
        self.repository = repository

    def run(self, run_id: str) -> MonitoringStageOutcome:
        status = self.repository.get_run(run_id).status
        if status not in {
            RunStatus.PERSISTENT_RESEARCH_READY,
            RunStatus.MONITORING_READY,
        }:
            raise ValueError("Stage 6 no-events adapter requires persistent research")
        triggers = self.repository.list_monitoring_triggers(run_id)
        return MonitoringStageOutcome(
            run_id=run_id,
            status=status,
            trigger_count=len(triggers),
            change_count=0,
            outcome="no_events",
        )


class ContinueInProgressError(RuntimeError):
    pass


class ManifestNotFoundError(KeyError):
    pass


class ManifestCorruptionError(ValueError):
    pass


@dataclass(frozen=True)
class OrchestrationStageReceipt:
    stage: int
    input_status: RunStatus | None
    output_status: RunStatus
    outcome: str
    artifact_ids: tuple[str, ...]
    completed_at: datetime


@dataclass(frozen=True)
class Stage1To7RunManifest:
    run_id: str
    contract_id: str
    executable_contract_id: str
    executable_contract_sha256: str
    stages: tuple[OrchestrationStageReceipt, ...]
    final_status: RunStatus
    created_at: datetime
    updated_at: datetime


class RootStageOrchestrator:
    def __init__(
        self,
        *,
        repository,
        stage1,
        stage2,
        stage3,
        stage4,
        stage5,
        stage6,
        stage7,
        manifest_root: str | Path,
        executable_contract_id: str,
        executable_contract_sha256: str,
    ):
        if not executable_contract_id.strip():
            raise ValueError("root orchestrator executable contract ID is required")
        normalized_sha = executable_contract_sha256.strip().casefold()
        if len(normalized_sha) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_sha
        ):
            raise ValueError("root orchestrator executable contract SHA-256 is invalid")
        self.repository = repository
        self.stage1 = stage1
        self.stage2 = stage2
        self.stage3 = stage3
        self.stage4 = stage4
        self.stage5 = stage5
        self.stage6 = stage6
        self.stage7 = stage7
        self.manifest_root = Path(manifest_root)
        self.executable_contract_id = executable_contract_id.strip()
        self.executable_contract_sha256 = normalized_sha

    def start(self, request) -> Stage1To7RunManifest:
        result = self.stage1.start(request)
        now = datetime.now(timezone.utc)
        receipt = OrchestrationStageReceipt(
            stage=1,
            input_status=None,
            output_status=result.status,
            outcome=_stage_outcome(result.status),
            artifact_ids=_artifact_ids(result),
            completed_at=now,
        )
        manifest = Stage1To7RunManifest(
            run_id=request.run_id,
            contract_id="theme-chokepoint-scoring-v1.4",
            executable_contract_id=self.executable_contract_id,
            executable_contract_sha256=self.executable_contract_sha256,
            stages=(receipt,),
            final_status=result.status,
            created_at=now,
            updated_at=now,
        )
        self._write_manifest(manifest)
        return manifest

    def continue_run(self, run_id: str) -> Stage1To7RunManifest:
        with _continue_owner(self._manifest_path(run_id).with_name(".continue.lock")):
            return self._continue_run_owned(run_id)

    def _continue_run_owned(self, run_id: str) -> Stage1To7RunManifest:
        manifest = self._load_manifest(run_id)
        completed_stages = {item.stage for item in manifest.stages}
        receipts = list(manifest.stages)

        while True:
            status = self.repository.get_run(run_id).status
            self._reconcile_durable_receipts(run_id, status, receipts)
            if status in {
                RunStatus.NEEDS_CLARIFICATION,
                RunStatus.AWAITING_PRODUCT_CONFIRMATION,
                RunStatus.INCOMPLETE_BUDGET_EXHAUSTED,
                RunStatus.COMPANY_ASSESSMENT_INCOMPLETE,
                RunStatus.SIGNAL_EXPORT_READY,
            }:
                return self._finish(manifest, receipts, status)

            if status is RunStatus.READY_FOR_SUPPLY_CHAIN:
                self._run_stage(
                    receipts, 2, status, self.stage2.build, run_id,
                    {RunStatus.SUPPLY_CHAIN_GRAPH_READY},
                )
                continue
            if status is RunStatus.SUPPLY_CHAIN_GRAPH_READY:
                self._run_stage(
                    receipts, 3, status, self.stage3.run, run_id,
                    {
                        RunStatus.CHOKEPOINT_ASSESSMENT_READY,
                        RunStatus.INCOMPLETE_BUDGET_EXHAUSTED,
                    },
                )
                continue
            if status is RunStatus.CHOKEPOINT_ASSESSMENT_READY:
                self._run_stage(
                    receipts, 4, status, self.stage4.run, run_id,
                    {
                        RunStatus.COMPANY_ASSESSMENT_READY,
                        RunStatus.COMPANY_ASSESSMENT_INCOMPLETE,
                    },
                )
                continue
            if status is RunStatus.COMPANY_ASSESSMENT_READY:
                self._run_stage(
                    receipts, 5, status, self.stage5.finalize, run_id,
                    {RunStatus.PERSISTENT_RESEARCH_READY},
                )
                continue
            if status in {RunStatus.PERSISTENT_RESEARCH_READY, RunStatus.MONITORING_READY}:
                if 6 not in completed_stages and not any(item.stage == 6 for item in receipts):
                    self._run_stage(
                        receipts, 6, status, self.stage6.run, run_id,
                        {RunStatus.PERSISTENT_RESEARCH_READY, RunStatus.MONITORING_READY},
                    )
                    continue
                self._run_stage(
                    receipts, 7, status, self.stage7.export, run_id,
                    {RunStatus.SIGNAL_EXPORT_READY},
                )
                continue
            raise ValueError(f"unsupported orchestrator run status: {status.value}")

    def get_manifest(self, run_id: str) -> Stage1To7RunManifest:
        return self._load_manifest(run_id)

    @staticmethod
    def _reconcile_durable_receipts(run_id, status, receipts):
        recorded_stages = {item.stage for item in receipts}
        for stage in _REQUIRED_STAGE_RECEIPTS_BY_STATUS.get(status, ()):
            if stage not in recorded_stages:
                raise ValueError(
                    f"run {run_id} has durable status {status.value}, but the "
                    f"durable receipt is missing for Stage {stage}; recovery "
                    "cannot advance"
                )

    def _run_stage(
        self,
        receipts,
        stage,
        input_status,
        handler,
        run_id,
        allowed_statuses,
    ):
        if any(item.stage == stage for item in receipts):
            raise ValueError(f"Stage {stage} is already recorded for run {run_id}")
        result = handler(run_id)
        if stage in {3, 4} and (
            getattr(result, "executable_contract_id", None)
            != self.executable_contract_id
            or getattr(result, "executable_contract_sha256", None)
            != self.executable_contract_sha256
        ):
            raise ValueError(
                f"Stage {stage} executable scoring contract lineage mismatch"
            )
        output_status = self.repository.get_run(run_id).status
        if output_status not in allowed_statuses:
            raise ValueError(
                f"Stage {stage} returned invalid status: {output_status.value}"
            )
        receipts.append(
            OrchestrationStageReceipt(
                stage=stage,
                input_status=input_status,
                output_status=output_status,
                outcome=getattr(result, "outcome", _stage_outcome(output_status)),
                artifact_ids=_artifact_ids(result),
                completed_at=datetime.now(timezone.utc),
            )
        )
        prior = self._load_manifest(run_id)
        now = datetime.now(timezone.utc)
        self._write_manifest(
            Stage1To7RunManifest(
                run_id=run_id,
                contract_id=prior.contract_id,
                executable_contract_id=prior.executable_contract_id,
                executable_contract_sha256=prior.executable_contract_sha256,
                stages=tuple(receipts),
                final_status=output_status,
                created_at=prior.created_at,
                updated_at=now,
            )
        )

    def _finish(self, prior, receipts, status):
        now = datetime.now(timezone.utc)
        manifest = Stage1To7RunManifest(
            run_id=prior.run_id,
            contract_id=prior.contract_id,
            executable_contract_id=prior.executable_contract_id,
            executable_contract_sha256=prior.executable_contract_sha256,
            stages=tuple(receipts),
            final_status=status,
            created_at=prior.created_at,
            updated_at=now,
        )
        self._write_manifest(manifest)
        return manifest

    def _manifest_path(self, run_id):
        return self.manifest_root / run_id / "stage1-7-e2e-run-manifest.json"

    def _write_manifest(self, manifest):
        path = self._manifest_path(manifest.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(_jsonable(asdict(manifest)), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _load_manifest(self, run_id):
        path = self._manifest_path(run_id)
        if not path.is_file():
            raise ManifestNotFoundError("run manifest is missing")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = {
                "run_id",
                "contract_id",
                "executable_contract_id",
                "executable_contract_sha256",
                "stages",
                "final_status",
                "created_at",
                "updated_at",
            }
            if not isinstance(payload, dict) or set(payload) != expected:
                raise ManifestCorruptionError
            if payload["run_id"] != run_id or not isinstance(payload["stages"], list):
                raise ManifestCorruptionError
            stage_expected = {
                "stage",
                "input_status",
                "output_status",
                "outcome",
                "artifact_ids",
                "completed_at",
            }
            if any(
                not isinstance(item, dict) or set(item) != stage_expected
                for item in payload["stages"]
            ):
                raise ManifestCorruptionError
            manifest = Stage1To7RunManifest(
                run_id=payload["run_id"],
                contract_id=payload["contract_id"],
                executable_contract_id=payload["executable_contract_id"],
                executable_contract_sha256=payload["executable_contract_sha256"],
                stages=tuple(
                    OrchestrationStageReceipt(
                        stage=item["stage"],
                        input_status=(
                            RunStatus(item["input_status"])
                            if item["input_status"]
                            else None
                        ),
                        output_status=RunStatus(item["output_status"]),
                        outcome=item["outcome"],
                        artifact_ids=tuple(item["artifact_ids"]),
                        completed_at=datetime.fromisoformat(item["completed_at"]),
                    )
                    for item in payload["stages"]
                ),
                final_status=RunStatus(payload["final_status"]),
                created_at=datetime.fromisoformat(payload["created_at"]),
                updated_at=datetime.fromisoformat(payload["updated_at"]),
            )
            if (
                not isinstance(manifest.contract_id, str)
                or not isinstance(manifest.executable_contract_id, str)
                or not isinstance(manifest.executable_contract_sha256, str)
                or any(
                    not isinstance(item.stage, int)
                    or isinstance(item.stage, bool)
                    or not isinstance(item.outcome, str)
                    or not all(isinstance(value, str) for value in item.artifact_ids)
                    for item in manifest.stages
                )
            ):
                raise ManifestCorruptionError
            return manifest
        except ManifestCorruptionError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ManifestCorruptionError("run manifest is corrupt") from None


def _artifact_ids(result) -> tuple[str, ...]:
    values = []
    for field in (
        "run_id",
        "refresh_id",
        "csv_path",
        "parquet_path",
        "definitions_path",
        "lineage_path",
    ):
        value = getattr(result, field, None)
        if value is not None and str(value) not in values:
            values.append(str(value))
    return tuple(values)


@contextmanager
def _continue_owner(lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    acquired = False
    try:
        try:
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError as error:
            raise ContinueInProgressError("continue already in progress") from error
        yield
    finally:
        if acquired:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        lock_file.close()


def _stage_outcome(status: RunStatus) -> str:
    if status is RunStatus.NEEDS_CLARIFICATION:
        return "needs_clarification"
    if status is RunStatus.AWAITING_PRODUCT_CONFIRMATION:
        return "awaiting_human_confirmation"
    if status is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED:
        return "incomplete_budget_exhausted"
    return "completed"


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, RunStatus):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
