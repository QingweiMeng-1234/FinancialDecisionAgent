from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from event_collector.theme_chokepoint.contracts import (
    FeedbackCorrectionDraft,
    RunStatus,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage4 import CompanyExposureRedTeamService
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface
from event_collector.cli.theme_chokepoint import main as theme_chokepoint_cli
from test_theme_chokepoint_stage4 import (
    FixedResearcher,
    _challenger,
    _challenger_set,
    _incumbent,
    _ready_stage3,
    _service,
    _v15_service,
)


EXPECTED_ARTIFACTS = {
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
    "artifact-manifest.json",
}


def _ready_stage4(repository):
    request = _ready_stage3(repository)
    _service(
        repository,
        FixedResearcher([_incumbent(), _challenger()], [_challenger_set()]),
    ).run(request.run_id)
    return request


def test_stage5_resume_dispatches_only_the_next_uncommitted_stage(tmp_path):
    """SELECT INVARIANT: resume never repeats a committed external side effect."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    calls = []

    service = PersistentResearchProductService(repository, tmp_path / "artifacts")
    outcome = service.resume(
        request.run_id,
        stage2=lambda run_id: calls.append(("stage2", run_id)),
        stage3=lambda run_id: calls.append(("stage3", run_id)),
        stage4=lambda run_id: calls.append(("stage4", run_id)),
    )

    assert outcome.next_stage == 4
    assert calls == [("stage4", request.run_id)]
    assert repository.get_run(request.run_id).status is RunStatus.CHOKEPOINT_ASSESSMENT_READY


def test_stage5_v15_incomplete_company_assessment_blocks_publish_and_resumes_stage4(
    tmp_path,
):
    """SELECT INVARIANT: incomplete v1.5 company work is resumable but unpublishable."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage3(repository)
    _v15_service(
        repository,
        FixedResearcher([_incumbent()], []),
    ).run(request.run_id)
    service = PersistentResearchProductService(repository, tmp_path / "artifacts")
    calls = []

    with pytest.raises(ValueError, match="COMPANY_ASSESSMENT_READY"):
        service.finalize(request.run_id)
    outcome = service.resume(
        request.run_id,
        stage2=lambda run_id: calls.append((2, run_id)),
        stage3=lambda run_id: calls.append((3, run_id)),
        stage4=lambda run_id: calls.append((4, run_id)),
    )

    assert outcome.dispatched is True
    assert outcome.next_stage == 4
    assert calls == [(4, request.run_id)]
    assert not (tmp_path / "artifacts" / request.run_id).exists()
    assert (
        repository.get_run(request.run_id).status
        is RunStatus.COMPANY_ASSESSMENT_INCOMPLETE
    )


def test_stage5_writes_complete_immutable_artifact_set_and_can_reopen_it(tmp_path):
    """SELECT INVARIANT: a committed run artifact directory is immutable and hash-auditable."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    service = PersistentResearchProductService(repository, tmp_path / "artifacts")

    first = service.finalize(request.run_id)
    second = service.finalize(request.run_id)

    run_dir = tmp_path / "artifacts" / request.run_id
    assert {path.name for path in run_dir.iterdir()} == EXPECTED_ARTIFACTS
    assert first == second
    assert len(first.files) == len(EXPECTED_ARTIFACTS) - 1
    assert first.executable_contract_id == "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
    assert first.executable_contract_sha256 == (
        "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
    )
    assert repository.get_run(request.run_id).status is RunStatus.PERSISTENT_RESEARCH_READY
    run_payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_payload["run_id"] == request.run_id
    assert run_payload["scoring_contract"] == "theme-chokepoint-scoring-v1.4"
    assert run_payload["executable_contract_id"] == first.executable_contract_id
    assert run_payload["executable_contract_sha256"] == first.executable_contract_sha256
    manifest_payload = json.loads(
        (run_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_payload["executable_contract_id"] == first.executable_contract_id
    assert manifest_payload["executable_contract_sha256"] == first.executable_contract_sha256
    evidence_cards = [
        json.loads(line)
        for line in (run_dir / "evidence-cards.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    source_snapshots = [
        json.loads(line)
        for line in (run_dir / "source-snapshots.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    snapshots_by_article = {item["article_id"]: item for item in source_snapshots}
    assert evidence_cards
    assert set(snapshots_by_article) == {item["article_id"] for item in evidence_cards}
    for card in evidence_cards:
        snapshot = snapshots_by_article[card["article_id"]]
        assert snapshot["canonical_url"] == card["canonical_url"]
        assert snapshot["content_hash"] == card["content_hash"]
        assert snapshot["content_hash"] == "sha256:" + sha256(
            snapshot["original_text"].encode("utf-8")
        ).hexdigest()
    counter_receipts = [
        json.loads(line)
        for line in (run_dir / "counter-search-receipts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert counter_receipts[0]["challenger_set_id"] == "challengers-ups-v1"
    assert counter_receipts[0]["counter_search_receipt"]["query_log_ids"]

    (run_dir / "theme-card.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable artifact hash mismatch"):
        service.finalize(request.run_id)


def test_stage5_publish_does_not_require_windows_directory_rename(tmp_path, monkeypatch):
    """SELECT INVARIANT: publication survives Windows directory-rename denial."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    service = PersistentResearchProductService(repository, tmp_path / "artifacts")

    def deny_directory_rename(self, target):
        raise PermissionError("simulated WinError 5")

    monkeypatch.setattr(Path, "rename", deny_directory_rename)

    manifest = service.finalize(request.run_id)

    run_dir = tmp_path / "artifacts" / request.run_id
    assert manifest.run_id == request.run_id
    assert (run_dir / "artifact-manifest.json").is_file()
    assert repository.get_run(request.run_id).status is RunStatus.PERSISTENT_RESEARCH_READY


def test_stage5_feedback_targets_exact_stable_object_without_mutating_assessment(tmp_path):
    """SELECT INVARIANT: correction is append-only and cannot use the legacy truth-label shape."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    before = repository.get_stage4_result(request.run_id)
    target = before.company_assessments[0]
    service = PersistentResearchProductService(repository, tmp_path / "artifacts")

    correction = service.record_feedback(
        FeedbackCorrectionDraft(
            run_id=request.run_id,
            object_type="company_assessment",
            object_id=target.assessment_id,
            field_name="competition_primary_state",
            proposed_value="contested_chokepoint_owner",
            rationale="Customer switching evidence needs reinterpretation.",
            actor="human-reviewer",
        )
    )

    assert correction.correction_id.startswith("correction_")
    assert repository.list_feedback(request.run_id) == (correction,)
    assert repository.get_stage4_result(request.run_id) == before

    with pytest.raises(ValueError, match="legacy feedback field"):
        service.record_feedback(
            FeedbackCorrectionDraft(
                run_id=request.run_id,
                object_type="company_assessment",
                object_id=target.assessment_id,
                field_name="truth_label",
                proposed_value="true",
                rationale="legacy shape",
                actor="human-reviewer",
            )
        )
    with pytest.raises(KeyError, match="object_id"):
        service.record_feedback(
            FeedbackCorrectionDraft(
                run_id=request.run_id,
                object_type="claim",
                object_id="missing-claim",
                field_name="source_interpretation",
                proposed_value="unsupported",
                rationale="not found",
                actor="human-reviewer",
            )
        )


def test_stage5_compares_persisted_run_objects_by_stable_id(tmp_path):
    """SELECT INVARIANT: cross-run comparison reports object changes, not prose diffs."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    stage3_before = repository.get_stage3_result(request.run_id)
    stage4_before = repository.get_stage4_result(request.run_id)
    segment_before = stage3_before.assessments[0]
    company_before = stage4_before.company_assessments[0]
    stage3_after = replace(
        stage3_before,
        run_id="run-later",
        assessments=(replace(segment_before, primary_state="watch_segment"),),
    )
    stage4_after = replace(
        stage4_before,
        run_id="run-later",
        company_assessments=(
            replace(company_before, competition_primary_state="contested_chokepoint_owner"),
            *stage4_before.company_assessments[1:],
        ),
    )

    class ComparisonRepository:
        def get_stage3_result(self, run_id):
            return stage3_before if run_id == request.run_id else stage3_after

        def get_stage4_result(self, run_id):
            return stage4_before if run_id == request.run_id else stage4_after

    comparison = PersistentResearchProductService(
        ComparisonRepository(), tmp_path / "artifacts"
    ).compare_runs(request.run_id, "run-later")

    assert comparison.before_run_id == request.run_id
    assert comparison.after_run_id == "run-later"
    assert {
        (change.object_type, change.field_name, change.before_value, change.after_value)
        for change in comparison.changes
    } >= {
        ("segment_assessment", "primary_state", "candidate_chokepoint", "watch_segment"),
        (
            "company_assessment",
            "competition_primary_state",
            "vulnerable_incumbent",
            "contested_chokepoint_owner",
        ),
    }


def test_stage5_cli_and_mcp_friendly_interface_expose_persisted_status(tmp_path, capsys):
    """SELECT INVARIANT: CLI and tool adapters read the same persisted run contract."""
    database = tmp_path / "theme.db"
    repository = ThemeChokepointRepository(database)
    request = _ready_stage4(repository)
    service = PersistentResearchProductService(repository, tmp_path / "artifacts")
    interface = ThemeChokepointInterface(repository, service)

    payload = interface.get_run_status(request.run_id)

    assert payload == {
        "run_id": request.run_id,
        "status": "COMPANY_ASSESSMENT_READY",
        "next_stage": 5,
    }
    assert set(interface.tool_handlers()) == {
        "theme_chokepoint_get_run",
        "theme_chokepoint_finalize",
        "theme_chokepoint_compare_runs",
        "theme_chokepoint_record_feedback",
    }

    exit_code = theme_chokepoint_cli(
        ["--db", str(database), "--artifacts", str(tmp_path / "artifacts"), "status", request.run_id]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == payload
