from __future__ import annotations

import argparse
from datetime import datetime, timezone
from types import SimpleNamespace

import uvicorn

from event_collector.financial_agent_mcp import (
    FinancialAgentMCPServer,
    FinancialAgentRuntimeConfig,
)
from event_collector.theme_chokepoint.contracts import RunStatus
from event_collector.theme_chokepoint.interfaces import ThemeChokepointInterface


class IntegrationRepository:
    def get_run(self, run_id):
        if run_id != "python-1":
            raise KeyError("missing")
        return SimpleNamespace(
            run_id=run_id,
            status=RunStatus.READY_FOR_SUPPLY_CHAIN,
            product_anchors=(
                SimpleNamespace(anchor_id="anchor-1", status="confirmed"),
            ),
            confirmed_by="owner",
            confirmed_at=datetime(2026, 8, 23, 11, 0, tzinfo=timezone.utc),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    repository = IntegrationRepository()
    interface = ThemeChokepointInterface(repository, product_service=None)
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
