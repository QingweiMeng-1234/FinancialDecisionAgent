#!/usr/bin/env python3
"""Backward-compatible wrapper for the financial-agent MCP server."""

from __future__ import annotations

import argparse

from event_collector.financial_agent_mcp import FinancialAgentRuntimeConfig, create_financial_agent_server
from event_collector.service_defaults import DEFAULT_SERVICE_DEFAULTS_PATH


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the financial-agent MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Which MCP transport to run",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for HTTP transports")
    parser.add_argument("--port", type=int, default=8877, help="Port for HTTP transports")
    parser.add_argument(
        "--service-defaults-path",
        default=DEFAULT_SERVICE_DEFAULTS_PATH,
        help="Path to shared service-side defaults JSON",
    )
    parser.add_argument("--index-control-db-path", default="data/runtime/index_generation_control.db")
    parser.add_argument("--index-corpus-id", default="news")
    parser.add_argument(
        "--canonical-db-path",
        default="data/rag_corpus_v2_20260815/news_articles.db",
    )
    parser.add_argument(
        "--canonical-content-root",
        default="data/rag_corpus_v2_20260815/data/articles",
    )
    parser.add_argument("--chroma-persist-dir", default="data/rag_index_v2_20260815")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = create_financial_agent_server(
        runtime_config=FinancialAgentRuntimeConfig(
            service_defaults_path=args.service_defaults_path,
            index_control_db_path=args.index_control_db_path,
            index_corpus_id=args.index_corpus_id,
            canonical_db_path=args.canonical_db_path,
            canonical_content_root=args.canonical_content_root,
            chroma_persist_dir=args.chroma_persist_dir,
            http_host=args.host,
            http_port=args.port,
        )
    )
    server.run(transport=args.transport)
    return 0
