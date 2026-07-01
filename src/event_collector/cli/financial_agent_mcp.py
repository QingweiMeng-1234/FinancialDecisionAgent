#!/usr/bin/env python3
"""Backward-compatible wrapper for the financial-agent MCP server."""

from __future__ import annotations

import argparse

from event_collector.financial_agent_mcp import FinancialAgentRuntimeConfig, create_financial_agent_server
from event_collector.service_defaults import DEFAULT_SERVICE_DEFAULTS_PATH


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the financial-agent MCP server.")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="Which MCP transport to run")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for HTTP transports")
    parser.add_argument("--port", type=int, default=8877, help="Port for HTTP transports")
    parser.add_argument("--service-defaults-path", default=DEFAULT_SERVICE_DEFAULTS_PATH, help="Path to shared service-side defaults JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = create_financial_agent_server(
        runtime_config=FinancialAgentRuntimeConfig(
            service_defaults_path=args.service_defaults_path,
            http_host=args.host,
            http_port=args.port,
        )
    )
    server.run(transport=args.transport)
    return 0
