"""Stage 5: resumable research product, immutable artifacts and human corrections."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile

from event_collector.theme_chokepoint.contracts import (
    ArtifactFile,
    ArtifactManifest,
    FeedbackCorrection,
    FeedbackCorrectionDraft,
    ResumeOutcome,
    RunComparison,
    RunComparisonChange,
    RunStatus,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


ARTIFACT_NAMES = (
    "run.json",
    "theme-card.md",
    "supply-chain-nodes.jsonl",
    "supply-chain-edges.jsonl",
    "supply-chain-map.md",
    "claim-ledger.jsonl",
    "evidence-cards.jsonl",
    "source-snapshots.jsonl",
    "chokepoint-scorecard.md",
    "company-exposure.md",
    "company-competition-scorecard.md",
    "counter-evidence.md",
    "counter-search-receipts.jsonl",
    "monitoring-triggers.md",
)
LEGACY_FEEDBACK_FIELDS = {"truth_label", "true", "false", "unknown", "conflicted"}


class PersistentResearchProductService:
    def __init__(self, repository: ThemeChokepointRepository, artifact_root: str | Path):
        self.repository = repository
        self.artifact_root = Path(artifact_root)

    def resume(self, run_id: str, *, stage2, stage3, stage4) -> ResumeOutcome:
        status = self.repository.get_run(run_id).status
        dispatch = {
            RunStatus.READY_FOR_SUPPLY_CHAIN: (2, stage2),
            RunStatus.SUPPLY_CHAIN_GRAPH_READY: (3, stage3),
            RunStatus.CHOKEPOINT_ASSESSMENT_READY: (4, stage4),
            RunStatus.COMPANY_ASSESSMENT_INCOMPLETE: (4, stage4),
        }.get(status)
        if dispatch is None:
            return ResumeOutcome(run_id, status, None, False)
        next_stage, handler = dispatch
        handler(run_id)
        return ResumeOutcome(run_id, status, next_stage, True)

    def finalize(self, run_id: str) -> ArtifactManifest:
        status = self.repository.get_run(run_id).status
        if status not in {
            RunStatus.COMPANY_ASSESSMENT_READY,
            RunStatus.PERSISTENT_RESEARCH_READY,
            RunStatus.MONITORING_READY,
            RunStatus.SIGNAL_EXPORT_READY,
        }:
            raise ValueError(
                "Stage 5 finalize requires COMPANY_ASSESSMENT_READY; "
                f"actual={status.value}"
            )
        target = self.artifact_root / run_id
        if status in {RunStatus.MONITORING_READY, RunStatus.SIGNAL_EXPORT_READY} and not target.exists():
            raise ValueError("later-stage run is missing its immutable Stage 5 artifact set")
        if target.exists():
            manifest = _load_and_verify_manifest(target)
        else:
            manifest = self._write_artifacts(run_id, target)
        if status is RunStatus.COMPANY_ASSESSMENT_READY:
            self.repository.mark_persistent_research_ready(run_id)
        return manifest

    def record_feedback(self, draft: FeedbackCorrectionDraft) -> FeedbackCorrection:
        if draft.field_name in LEGACY_FEEDBACK_FIELDS:
            raise ValueError("legacy feedback field is not valid for v1.4 corrections")
        values = {
            "object_type": draft.object_type,
            "object_id": draft.object_id,
            "field_name": draft.field_name,
            "proposed_value": draft.proposed_value,
            "rationale": draft.rationale,
            "actor": draft.actor,
        }
        for field, value in values.items():
            if not value.strip():
                raise ValueError(f"feedback {field} is required")
        object_ids = self.repository.list_research_object_ids(draft.run_id)
        if (draft.object_type, draft.object_id) not in object_ids:
            raise KeyError(
                f"object_id is not present in run: {draft.object_type}/{draft.object_id}"
            )
        now = datetime.now(timezone.utc)
        digest = sha256(
            "\0".join(
                (
                    draft.run_id,
                    draft.object_type,
                    draft.object_id,
                    draft.field_name,
                    draft.proposed_value,
                    draft.actor,
                    now.isoformat(),
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        correction = FeedbackCorrection(
            correction_id=f"correction_{digest}",
            run_id=draft.run_id,
            object_type=draft.object_type,
            object_id=draft.object_id,
            field_name=draft.field_name,
            proposed_value=draft.proposed_value,
            rationale=draft.rationale.strip(),
            actor=draft.actor.strip(),
            created_at=now,
        )
        return self.repository.save_feedback(correction)

    def compare_runs(self, before_run_id: str, after_run_id: str) -> RunComparison:
        if before_run_id == after_run_id:
            raise ValueError("comparison requires two different run IDs")
        segment_before = {
            item.segment_id: item
            for item in self.repository.get_stage3_result(before_run_id).assessments
        }
        segment_after = {
            item.segment_id: item
            for item in self.repository.get_stage3_result(after_run_id).assessments
        }
        company_before = {
            item.assessment_id: item
            for item in self.repository.get_stage4_result(before_run_id).company_assessments
        }
        company_after = {
            item.assessment_id: item
            for item in self.repository.get_stage4_result(after_run_id).company_assessments
        }
        changes = []
        changes.extend(
            _field_changes(
                "segment_assessment",
                segment_before,
                segment_after,
                ("primary_state", "score_min", "score_max", "decision_coverage"),
            )
        )
        changes.extend(
            _field_changes(
                "company_assessment",
                company_before,
                company_after,
                ("competition_primary_state", "challenger_label"),
            )
        )
        return RunComparison(before_run_id, after_run_id, tuple(changes))

    def _write_artifacts(self, run_id: str, target: Path) -> ArtifactManifest:
        run = self.repository.get_run(run_id)
        graph = self.repository.get_supply_chain_graph(run_id)
        stage3 = self.repository.get_stage3_result(run_id)
        stage4 = self.repository.get_stage4_result(run_id)
        _verify_executable_contract_lineage(stage3, stage4)
        contents = _render_artifacts(run, graph, stage3, stage4)
        if set(contents) != set(ARTIFACT_NAMES):
            raise ValueError("artifact renderer did not produce the frozen Stage 5 set")
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        temp_path = Path(
            tempfile.mkdtemp(prefix=f".{run_id}-", dir=str(self.artifact_root))
        )
        try:
            files = []
            for name in ARTIFACT_NAMES:
                data = contents[name].encode("utf-8")
                (temp_path / name).write_bytes(data)
                files.append(
                    ArtifactFile(
                        name=name,
                        sha256="sha256:" + sha256(data).hexdigest(),
                        size_bytes=len(data),
                    )
                )
            manifest = ArtifactManifest(
                run_id=run_id,
                executable_contract_id=stage4.executable_contract_id,
                executable_contract_sha256=stage4.executable_contract_sha256,
                files=tuple(files),
                created_at=datetime.now(timezone.utc),
            )
            (temp_path / "artifact-manifest.json").write_text(
                json.dumps(_jsonable(asdict(manifest)), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            target.mkdir()
            for name in ARTIFACT_NAMES:
                shutil.copy2(temp_path / name, target / name)
            shutil.copy2(
                temp_path / "artifact-manifest.json",
                target / "artifact-manifest.json",
            )
            shutil.rmtree(temp_path)
            return manifest
        except Exception:
            if target.exists() and not (target / "artifact-manifest.json").exists():
                shutil.rmtree(target)
            if temp_path.exists():
                shutil.rmtree(temp_path)
            raise


def _render_artifacts(run, graph, stage3, stage4):
    run_payload = {
        "run_id": run.run_id,
        "status_at_export": run.status.value,
        "request": _jsonable(asdict(run.request)),
        "scoring_contract": stage4.contract_version,
        "executable_contract_id": stage4.executable_contract_id,
        "executable_contract_sha256": stage4.executable_contract_sha256,
        "company_provider_request_receipt_ids": list(
            stage4.provider_request_receipt_ids
        ),
        "company_research_cost_usd": stage4.cost_usd_spent,
        "company_chain_receipts": _jsonable(
            tuple(asdict(item) for item in stage4.company_chain_receipts)
        ),
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }
    nodes_jsonl = "".join(
        json.dumps(_jsonable(asdict(item)), ensure_ascii=False, sort_keys=True) + "\n"
        for item in graph.nodes
    )
    edges_jsonl = "".join(
        json.dumps(_jsonable(asdict(item)), ensure_ascii=False, sort_keys=True) + "\n"
        for item in graph.edges
    )
    all_claims = (*stage3.claims, *stage4.company_claims)
    if len({item.claim_id for item in all_claims}) != len(all_claims):
        raise ValueError("claim ledger requires unique claim_id values across stages")
    claim_jsonl = "".join(
        json.dumps(_jsonable(asdict(item)), ensure_ascii=False, sort_keys=True) + "\n"
        for item in all_claims
    )
    all_snapshots = (*stage3.source_snapshots, *stage4.company_source_snapshots)
    all_evidence_cards = (*stage3.evidence_cards, *stage4.company_evidence_cards)
    if len({item.evidence_id for item in all_evidence_cards}) != len(
        all_evidence_cards
    ):
        raise ValueError("evidence ledger requires unique evidence_id values across stages")
    snapshots_by_article = {item.article_id: item for item in all_snapshots}
    if len(snapshots_by_article) != len(all_snapshots):
        raise ValueError("source snapshots require unique article_id values")
    for card in all_evidence_cards:
        snapshot = snapshots_by_article.get(card.article_id)
        if snapshot is None:
            raise ValueError(f"evidence card is missing source snapshot: {card.evidence_id}")
        actual_hash = "sha256:" + sha256(snapshot.original_text.encode("utf-8")).hexdigest()
        if (
            snapshot.canonical_url != card.canonical_url
            or snapshot.content_hash != card.content_hash
            or snapshot.content_hash != actual_hash
        ):
            raise ValueError(f"source snapshot does not match evidence card: {card.evidence_id}")
    evidence_jsonl = "".join(
        json.dumps(_jsonable(asdict(item)), ensure_ascii=False, sort_keys=True) + "\n"
        for item in all_evidence_cards
    )
    snapshot_jsonl = "".join(
        json.dumps(_jsonable(asdict(item)), ensure_ascii=False, sort_keys=True) + "\n"
        for item in all_snapshots
    )
    counter_records = [
        {
            "record_type": "segment_counter_search_receipt",
            **asdict(item.critic_receipt),
        }
        for item in stage3.assessments
        if item.critic_receipt is not None
    ]
    counter_records.extend(
        {
            "record_type": "company_counter_search_receipt",
            **asdict(item),
        }
        for item in stage4.challenger_sets
    )
    counter_search_jsonl = "".join(
        json.dumps(_jsonable(item), ensure_ascii=False, sort_keys=True) + "\n"
        for item in counter_records
    )
    theme_card = (
        f"# {run.request.theme}\n\n"
        f"Run: `{run.run_id}`  \n"
        f"Scope: {run.demand_frame.scope if run.demand_frame else 'unresolved'}  \n"
        f"As of: {run.request.as_of_date.isoformat()}\n"
    )
    supply_map = "# Supply-chain map\n\n" + "\n".join(
        f"- `{edge.downstream_node_id}` -> `{edge.upstream_node_id}` ({edge.status})"
        for edge in graph.edges
    ) + "\n"
    segment_lines = ["# Chokepoint scorecard", ""]
    for item in stage3.assessments:
        segment_lines.append(
            f"- `{item.segment_id}`: {item.primary_state or 'withheld'}, "
            f"score {item.score_min}-{item.score_max}, decision coverage {item.decision_coverage:.0%}"
        )
    exposure_lines = ["# Company exposure", ""]
    competition_lines = ["# Company competition scorecard", ""]
    counter_lines = ["# Counter-evidence and falsification", ""]
    for company in stage4.company_assessments:
        exposure_lines.append(
            f"- `{company.assessment_id}` {company.company_name}: roles={', '.join(company.roles)}; "
            f"revenue={company.exposure.revenue}; earnings={company.exposure.earnings}"
        )
        competition_lines.append(
            f"- {company.company_name}: competition={company.competition_primary_state or 'withheld'}; "
            f"defensibility={_state(company.defensibility)}; replacement={_state(company.replacement)}; "
            f"earnings={_state(company.earnings)}"
        )
        for review in company.red_team_reviews:
            counter_lines.append(
                f"- {company.company_name}/{review.thesis}: falsify when "
                + "; ".join(review.falsification_conditions)
            )
    return {
        "run.json": json.dumps(run_payload, ensure_ascii=False, indent=2) + "\n",
        "theme-card.md": theme_card,
        "supply-chain-nodes.jsonl": nodes_jsonl,
        "supply-chain-edges.jsonl": edges_jsonl,
        "supply-chain-map.md": supply_map,
        "claim-ledger.jsonl": claim_jsonl,
        "evidence-cards.jsonl": evidence_jsonl,
        "source-snapshots.jsonl": snapshot_jsonl,
        "chokepoint-scorecard.md": "\n".join(segment_lines) + "\n",
        "company-exposure.md": "\n".join(exposure_lines) + "\n",
        "company-competition-scorecard.md": "\n".join(competition_lines) + "\n",
        "counter-evidence.md": "\n".join(counter_lines) + "\n",
        "counter-search-receipts.jsonl": counter_search_jsonl,
        "monitoring-triggers.md": (
            "# Monitoring triggers\n\n"
            "No Stage 6 trigger has been committed for this immutable run.\n"
        ),
    }


def _state(assessment):
    return assessment.primary_state if assessment and assessment.primary_state else "withheld"


def _verify_executable_contract_lineage(stage3, stage4):
    if (
        stage3.executable_contract_id != stage4.executable_contract_id
        or stage3.executable_contract_sha256 != stage4.executable_contract_sha256
    ):
        raise ValueError("Stage 5 executable scoring contract lineage mismatch")


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, RunStatus):
        return value.value
    return value


def _load_and_verify_manifest(target: Path) -> ArtifactManifest:
    manifest_path = target / "artifact-manifest.json"
    if not manifest_path.exists():
        raise ValueError("immutable artifact manifest is missing")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = tuple(ArtifactFile(**item) for item in payload["files"])
    if {item.name for item in files} != set(ARTIFACT_NAMES):
        raise ValueError("immutable artifact set does not match Stage 5 contract")
    for item in files:
        path = target / item.name
        if not path.is_file():
            raise ValueError(f"immutable artifact is missing: {item.name}")
        data = path.read_bytes()
        actual = "sha256:" + sha256(data).hexdigest()
        if actual != item.sha256 or len(data) != item.size_bytes:
            raise ValueError(f"immutable artifact hash mismatch: {item.name}")
    return ArtifactManifest(
        run_id=payload["run_id"],
        executable_contract_id=payload["executable_contract_id"],
        executable_contract_sha256=payload["executable_contract_sha256"],
        files=files,
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def _field_changes(object_type, before, after, fields):
    changes = []
    for object_id in sorted(set(before) | set(after)):
        old = before.get(object_id)
        new = after.get(object_id)
        if old is None or new is None:
            changes.append(
                RunComparisonChange(
                    object_type,
                    object_id,
                    "object_presence",
                    "present" if old else None,
                    "present" if new else None,
                )
            )
            continue
        for field in fields:
            old_value = getattr(old, field)
            new_value = getattr(new, field)
            if old_value != new_value:
                changes.append(
                    RunComparisonChange(
                        object_type,
                        object_id,
                        field,
                        None if old_value is None else str(old_value),
                        None if new_value is None else str(new_value),
                    )
                )
    return changes
