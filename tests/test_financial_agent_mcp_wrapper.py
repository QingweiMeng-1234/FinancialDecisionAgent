from __future__ import annotations

import financial_agent_mcp


def test_parse_args_reads_http_options():
    args = financial_agent_mcp.parse_args(
        [
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9901",
            "--service-defaults-path",
            "custom-defaults.json",
        ]
    )

    assert args.transport == "streamable-http"
    assert args.host == "127.0.0.1"
    assert args.port == 9901
    assert args.service_defaults_path == "custom-defaults.json"


def test_main_builds_server_with_runtime_config_and_runs(monkeypatch):
    captured = {}
    cli_path = "event_collector.cli.financial_agent_mcp"

    class FakeServer:
        def __init__(self, runtime_config):
            self.runtime_config = runtime_config

        def run(self, transport="stdio"):
            captured["transport"] = transport
            captured["runtime_config"] = self.runtime_config

    monkeypatch.setattr(
        f"{cli_path}.create_financial_agent_server",
        lambda runtime_config=None: FakeServer(runtime_config),
    )

    result = financial_agent_mcp.main(
        [
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9902",
            "--service-defaults-path",
            "shared.json",
        ]
    )

    assert result == 0
    assert captured["transport"] == "streamable-http"
    assert captured["runtime_config"].http_host == "127.0.0.1"
    assert captured["runtime_config"].http_port == 9902
    assert captured["runtime_config"].service_defaults_path == "shared.json"
