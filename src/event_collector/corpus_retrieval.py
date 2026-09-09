"""Construct retrieval against the live canonical article corpus."""

from event_collector.news_storage import SQLiteNewsStore
from event_collector.vector_store import ChromaVectorStore


def create_corpus_vector_store(
    *, db_path: str, persist_dir: str = "./chroma_data", collection_name: str = "news_articles",
) -> ChromaVectorStore:
    """Bind vector search to canonical eligibility, using a connection local to each search."""
    def eligible_article_versions():
        storage = SQLiteNewsStore(db_path=db_path)
        try:
            return storage.list_retrieval_eligible_article_versions()
        finally:
            storage.close()

    return ChromaVectorStore(
        persist_dir=persist_dir,
        collection_name=collection_name,
        eligible_article_versions_provider=eligible_article_versions,
    )
