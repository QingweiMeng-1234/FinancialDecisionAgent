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
            "--index-control-db-path",
            "control.db",
            "--index-corpus-id",
            "news-v2",
            "--canonical-db-path",
            "canonical.db",
            "--canonical-content-root",
            "canonical-content",
            "--chroma-persist-dir",
            "chroma-v2",
        ]
    )

    assert args.transport == "streamable-http"
    assert args.host == "127.0.0.1"
    assert args.port == 9901
    assert args.service_defaults_path == "custom-defaults.json"
    assert args.index_control_db_path == "control.db"
    assert args.index_corpus_id == "news-v2"
    assert args.canonical_db_path == "canonical.db"
    assert args.canonical_content_root == "canonical-content"
    assert args.chroma_persist_dir == "chroma-v2"


def test_parse_args_defaults_to_activated_v2_corpus():
    args = financial_agent_mcp.parse_args([])

    assert args.canonical_db_path == "data/rag_corpus_v2_20260815/news_articles.db"
    assert args.canonical_content_root == "data/rag_corpus_v2_20260815/data/articles"
    assert args.chroma_persist_dir == "data/rag_index_v2_20260815"


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
            "--index-control-db-path",
            "control.db",
            "--index-corpus-id",
            "news-v2",
            "--canonical-db-path",
            "canonical.db",
            "--canonical-content-root",
            "canonical-content",
            "--chroma-persist-dir",
            "chroma-v2",
        ]
    )

    assert result == 0
    assert captured["transport"] == "streamable-http"
    assert captured["runtime_config"].http_host == "127.0.0.1"
    assert captured["runtime_config"].http_port == 9902
    assert captured["runtime_config"].service_defaults_path == "shared.json"
    assert captured["runtime_config"].index_control_db_path == "control.db"
    assert captured["runtime_config"].index_corpus_id == "news-v2"
    assert captured["runtime_config"].canonical_db_path == "canonical.db"
    assert captured["runtime_config"].canonical_content_root == "canonical-content"
    assert captured["runtime_config"].chroma_persist_dir == "chroma-v2"
