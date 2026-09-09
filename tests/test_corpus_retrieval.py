"""Public entry points must agree on live corpus eligibility."""

from datetime import datetime
from pathlib import Path
from importlib import import_module
from types import SimpleNamespace

import pytest

from event_collector.financial_agent_mcp import FinancialAgentRuntimeConfig, create_financial_agent_server
from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.reranking import RerankMetadata
from event_collector.vector_store import ChromaVectorStore
from event_collector.corpus_retrieval import create_corpus_vector_store
from event_collector.retrieval_execution import RetrievalWorkItem, execute_retrieval_batch
from tests.deterministic_embedder import DeterministicEmbedder


@pytest.mark.parametrize("invalid", ["rejected", "missing", "tampered", "new_version", "stale_vectors"])
def test_mcp_retrieval_excludes_articles_invalidated_after_indexing(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer",
                        lambda *args, **kwargs: DeterministicEmbedder())
    monkeypatch.setattr("event_collector.retrieval_orchestration.rerank_candidates",
                        lambda query, candidates, **kwargs: RerankMetadata(
                            ranked_candidates=[{"candidate_id": c.candidate_id, "reason": "test"}
                                               for c in candidates]))
    config = FinancialAgentRuntimeConfig(db_path=str(tmp_path / "news.db"),
                                        entity_db_path=str(tmp_path / "entities.db"),
                                        persist_dir=str(tmp_path / "chroma"))
    storage = SQLiteNewsStore(db_path=config.db_path)
    try:
        article_id = storage.save_article(NewsArticle(
            source="news", title="Microsoft cloud revenue rises", description="Azure growth",
            content="Microsoft reported strong Azure revenue growth. Enterprise cloud demand increased. " * 8,
            url="https://example.com/cloud", published_at=datetime.now(),
        ))
        article = storage.get_article(article_id)
        index = ChromaVectorStore(persist_dir=config.persist_dir, embedder=DeterministicEmbedder())
        index.add_article(article_id, article)
        storage.mark_article_index_ready(article_id, article.content_sha256)
        server = create_financial_agent_server(config)
        assert server.retrieve_supporting_articles("cloud")["article_count"] == 1

        if invalid == "rejected":
            storage.mark_article_content_failure(article_id, reason="rejected")
        elif invalid == "missing":
            Path(article.content_path).unlink()
        elif invalid == "tampered":
            Path(article.content_path).write_text("tampered", encoding="utf-8")
        else:
            storage.update_article_content(article_id, content=article.content + "Updated revision.")
            if invalid == "stale_vectors":
                # A late old writer can leave old vectors under a currently eligible ID.
                current = storage.get_article(article_id)
                storage.mark_article_index_ready(article_id, current.content_sha256)
        assert server.retrieve_supporting_articles("cloud")["article_count"] == 0
    finally:
        storage.close()


@pytest.mark.parametrize("entry", ["query_news", "recommend_stock", "run_watchlist", "theme_research", "news_pipeline", "evaluation"])
def test_workflow_entry_points_use_live_corpus_eligibility(tmp_path, monkeypatch, entry):
    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer",
                        lambda *args, **kwargs: DeterministicEmbedder())
    db_path, persist_dir = str(tmp_path / "news.db"), str(tmp_path / "chroma")
    storage = SQLiteNewsStore(db_path=db_path)
    try:
        article_id = storage.save_article(NewsArticle(
            source="manual", title="Cloud", description="Demand", content="Cloud demand increased. " * 10,
            url="manual://cloud", published_at=datetime.now(),
        ))
        article = storage.get_article(article_id)
        index = ChromaVectorStore(persist_dir=persist_dir, embedder=DeterministicEmbedder())
        index.add_article(article_id, article)
        storage.mark_article_index_ready(article_id, article.content_sha256)
        storage.mark_article_content_failure(article_id, reason="rejected")
    finally:
        storage.close()

    observed = []

    class ProbeComplete(Exception):
        pass

    def probe(*args, **kwargs):
        vector_store = kwargs.get("vector_store")
        if vector_store is None:
            vector_store = args[2] if entry == "run_watchlist" else args[1]
        observed.append(vector_store.search("cloud"))
        raise ProbeComplete()

    if entry == "evaluation":
        module = import_module("run_retrieval_eval")
        monkeypatch.setattr(module, "run_retrieval_comparison", probe)
        monkeypatch.setattr(module, "_build_reranking_agents", lambda *args: (None, None, "test"))
        tickers = tmp_path / "tickers.yaml"
        tickers.write_text("tickers:\n  - MSFT\n", encoding="utf-8")
        invoke = lambda: module.main([
            "generate-annotations", "--ticker-list-path", str(tickers),
            "--entity-db-path", str(tmp_path / "entities.db"),
            "--db-path", db_path, "--persist-dir", persist_dir,
        ])
    elif entry == "news_pipeline":
        module = import_module("event_collector.news_pipeline")
        monkeypatch.setattr(module, "run_news_ingestion", lambda *args, **kwargs: SimpleNamespace())
        monkeypatch.setattr(module, "run_question", probe)
        invoke = lambda: module.run_news_pipeline(module.NewsPipelineRequest(
            db_path=db_path, persist_dir=persist_dir, question="cloud"))
    else:
        module = import_module(f"event_collector.cli.{entry}")
        downstream = {"query_news": "run_question", "recommend_stock": "recommend_target",
                      "run_watchlist": "run_watchlist_triage_workflow", "theme_research": "run_theme_research"}[entry]
        monkeypatch.setattr(module, downstream, probe)
        arguments = {"query_news": ["--question", "cloud"], "recommend_stock": ["--target", "MSFT"],
                     "run_watchlist": ["--tickers", "MSFT"],
                     "theme_research": ["--theme", "Cloud", "--analysis-goal", "demand"]}[entry]
        invoke = lambda: module.main(arguments + ["--db-path", db_path, "--persist-dir", persist_dir])
    try:
        invoke()
    except ProbeComplete:
        pass
    assert observed == [[]]


def test_corpus_search_is_thread_local_and_intersects_caller_filters(tmp_path, monkeypatch):
    monkeypatch.setattr("event_collector.vector_store.SentenceTransformer",
                        lambda *args, **kwargs: DeterministicEmbedder())
    monkeypatch.setattr("event_collector.retrieval_orchestration.rerank_candidates",
                        lambda query, candidates, **kwargs: RerankMetadata(
                            ranked_candidates=[{"candidate_id": c.candidate_id, "reason": "test"}
                                               for c in candidates]))
    db_path, persist_dir = str(tmp_path / "news.db"), str(tmp_path / "chroma")
    storage = SQLiteNewsStore(db_path=db_path)
    index = ChromaVectorStore(persist_dir=persist_dir, embedder=DeterministicEmbedder())
    article_id = storage.save_article(NewsArticle(
        source="manual", title="Cloud", description="Demand", content="Cloud demand increased. " * 10,
        url="manual://threaded", published_at=datetime.now(),
    ))
    article = storage.get_article(article_id)
    index.add_article(article_id, article)
    storage.mark_article_index_ready(article_id, article.content_sha256)
    storage.close()

    def factory():
        return create_corpus_vector_store(db_path=db_path, persist_dir=persist_dir)

    results = execute_retrieval_batch(
        [RetrievalWorkItem(key=str(i), query="cloud", top_k=1) for i in range(4)],
        factory, max_concurrency=2,
    )
    assert all(result.succeeded for result in results)
    assert [[item.article_id for item in result.bundle.evidence] for result in results] == [[article_id]] * 4
    assert factory().search("cloud", allowed_article_ids=set()) == []

    storage = SQLiteNewsStore(db_path=db_path)
    try:
        storage.mark_article_content_failure(article_id, reason="rejected")
    finally:
        storage.close()
    assert factory().search("cloud", allowed_article_ids={article_id}) == []
