from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace

import uvicorn

from event_collector.financial_agent_mcp import (
    FinancialAgentMCPServer,
    FinancialAgentRuntimeConfig,
)
from event_collector.theme_chokepoint.contracts import (
    ConfirmationReceipt,
    ProductAnchor,
    RunStatus,
    Stage1RunSnapshot,
)
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface
from event_collector.theme_chokepoint.orchestrator import RootStageOrchestrator


class IntegrationRepository:
    def __init__(self):
        now = datetime(2026, 8, 23, 11, 0, tzinfo=timezone.utc)
        self.runs = {
            "python-1": SimpleNamespace(
                run_id="python-1",
                status=RunStatus.READY_FOR_SUPPLY_CHAIN,
                product_anchors=(
                    ProductAnchor(
                        anchor_id="anchor-1",
                        product_name="UPS",
                        buyer_or_user="operator",
                        demand_variable="MW",
                        theme_link="power",
                        confidence=0.9,
                        supporting_evidence_ids=(),
                        missing_evidence=(),
                        status="confirmed",
                    ),
                ),
                confirmed_by="owner",
                confirmed_at=now,
            )
        }

    def get_run(self, run_id):
        if run_id not in self.runs:
            raise KeyError("missing")
        return self.runs[run_id]


class IntegrationStage1:
    def __init__(self, repository):
        self.repository = repository

    def start(self, request):
        now = datetime.now(timezone.utc)
        snapshot = Stage1RunSnapshot(
            run_id=request.run_id,
            request=request,
            status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
            demand_frame=None,
            product_anchors=(
                ProductAnchor(
                    anchor_id="anchor-production-1",
                    product_name="UPS",
                    buyer_or_user="operator",
                    demand_variable="MW",
                    theme_link="power",
                    confidence=0.9,
                    supporting_evidence_ids=(),
                    missing_evidence=(),
                    status="proposed",
                ),
            ),
            created_at=now,
            updated_at=now,
        )
        self.repository.runs[request.run_id] = snapshot
        return snapshot

    def confirm_product_anchors(self, run_id, *, anchor_ids, confirmed_by):
        current = self.repository.get_run(run_id)
        if current.status is not RunStatus.AWAITING_PRODUCT_CONFIRMATION:
            raise ValueError("run is not awaiting confirmation")
        proposed = {item.anchor_id for item in current.product_anchors}
        if not set(anchor_ids) <= proposed:
            raise ValueError("anchor is not proposed")
        now = datetime.now(timezone.utc)
        selected = set(anchor_ids)
        self.repository.runs[run_id] = replace(
            current,
            status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            product_anchors=tuple(
                replace(
                    item,
                    status="confirmed" if item.anchor_id in selected else "rejected",
                )
                for item in current.product_anchors
            ),
            confirmed_by=confirmed_by,
            confirmed_at=now,
            updated_at=now,
        )
        return ConfirmationReceipt(
            run_id=run_id,
            status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            confirmed_anchor_ids=tuple(anchor_ids),
            confirmed_by=confirmed_by,
            confirmed_at=now,
        )


class AdvancingStage:
    def __init__(self, repository, method, output_status):
        self.repository = repository
        self.method = method
        self.output_status = output_status

    def __getattr__(self, name):
        if name != self.method:
            raise AttributeError(name)

        def advance(run_id):
            current = self.repository.get_run(run_id)
            self.repository.runs[run_id] = replace(
                current,
                status=self.output_status,
                updated_at=datetime.now(timezone.utc),
            )
            return SimpleNamespace(
                status=self.output_status,
                run_id=run_id,
                executable_contract_id="integration-contract-v1",
                executable_contract_sha256="a" * 64,
            )

        return advance


class MonitoringStage:
    def __init__(self, repository):
        self.repository = repository

    def run(self, run_id):
        return SimpleNamespace(
            status=self.repository.get_run(run_id).status,
            run_id=run_id,
            outcome="no_events",
        )


def create_integration_runtime(_runtime_config=None):
    """Build a complete controlled runtime through the shipping factory contract."""
    repository = IntegrationRepository()
    stage1 = IntegrationStage1(repository)
    manifest_root = Path(tempfile.mkdtemp(prefix="m0-http-manifests-"))
    orchestrator = RootStageOrchestrator(
        repository=repository,
        stage1=stage1,
        stage2=AdvancingStage(
            repository, "build", RunStatus.SUPPLY_CHAIN_GRAPH_READY
        ),
        stage3=AdvancingStage(
            repository, "run", RunStatus.CHOKEPOINT_ASSESSMENT_READY
        ),
        stage4=AdvancingStage(
            repository, "run", RunStatus.COMPANY_ASSESSMENT_READY
        ),
        stage5=AdvancingStage(
            repository, "finalize", RunStatus.PERSISTENT_RESEARCH_READY
        ),
        stage6=MonitoringStage(repository),
        stage7=AdvancingStage(
            repository, "export", RunStatus.SIGNAL_EXPORT_READY
        ),
        manifest_root=manifest_root,
        executable_contract_id="integration-contract-v1",
        executable_contract_sha256="a" * 64,
    )
    return SimpleNamespace(
        repository=repository,
        stage1=stage1,
        orchestrator=orchestrator,
        stage5=object(),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    runtime = create_integration_runtime()
    interface = ThemeChokepointInterface(
        runtime.repository,
        product_service=runtime.stage5,
        stage1=runtime.stage1,
        orchestrator=runtime.orchestrator,
    )
    server = FinancialAgentMCPServer(
        runtime_config=FinancialAgentRuntimeConfig(
            http_host="127.0.0.1",
            http_port=args.port,
            http_allowed_hosts=(
                f"127.0.0.1:{args.port}",
                f"localhost:{args.port}",
            ),
            refresh_retry_scheduler_enabled=False,
        ),
        successor_generation_coordinator=object(),
        theme_interface=interface,
    )
    app = server.streamable_http_app()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
