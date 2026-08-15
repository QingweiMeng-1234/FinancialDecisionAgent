"""
Vector store for semantic retrieval of news articles using ChromaDB.
Uses sentence-transformers for embeddings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
import re
from typing import Dict, List

import chromadb
from sentence_transformers import SentenceTransformer

from event_collector.document_pipeline import split_article_document
from event_collector.news_storage import NewsArticle, compute_content_sha256

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_MATCHED_CHUNKS = 2


class VectorStore(ABC):
    """Abstract base class for vector storage and semantic search."""

    @abstractmethod
    def add_article(self, article_id: int, article: NewsArticle) -> List[str]:
        """Index one article and return its chunk record ids."""

    @abstractmethod
    def delete_article(self, article_id: int) -> None:
        """Delete all indexed chunks for one article."""

    @abstractmethod
    def search(self, query: str, top_k: int = 5, *, allowed_article_ids: set[int] | None = None) -> List[Dict]:
        """
        Search for articles semantically similar to the query.
        Returns article-level results aggregated from chunk matches.
        """


class ChromaVectorStore(VectorStore):
    """
    Vector store backed by ChromaDB with sentence-transformers embeddings.
    """

    def __init__(
        self,
        persist_dir: str = "./chroma_data",
        model_name: str = "all-MiniLM-L6-v2",
        collection_name: str = "news_articles",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        max_matched_chunks: int = DEFAULT_MATCHED_CHUNKS,
        embedder=None,
        eligible_article_ids_provider=None,
    ):
        self.persist_dir = persist_dir
        self.model_name = model_name
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_matched_chunks = max_matched_chunks
        self.eligible_article_ids_provider = eligible_article_ids_provider

        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedder = embedder or SentenceTransformer(
            model_name,
            local_files_only=_resolve_local_files_only(),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_article(self, article_id: int, article: NewsArticle) -> List[str]:
        """
        Add or replace one article in the vector store as chunk-level records.
        """
        if article.source == "news" and article.content_validation_status != "verified":
            raise ValueError("News article content must be verified before vector indexing")
        if article.content_status != "ready":
            raise ValueError("Article content must be ready before vector indexing")
        active_content_sha256 = article.active_content_sha256 or article.content_sha256
        if not active_content_sha256 or compute_content_sha256(article.content) != active_content_sha256:
            raise ValueError("Article active content hash must match before vector indexing")
        self.delete_article(article_id)

        chunk_documents = split_article_document(
            article_id,
            article,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        if not chunk_documents:
            return []

        chunks = [document.page_content for document in chunk_documents]
        record_ids = [build_chunk_id(article_id, index) for index in range(len(chunk_documents))]
        embeddings = self.embedder.encode(chunks).tolist()
        metadata = [dict(document.metadata) for document in chunk_documents]
        self.collection.upsert(
            ids=record_ids,
            embeddings=embeddings,
            metadatas=metadata,
            documents=chunks,
        )
        return record_ids

    def delete_article(self, article_id: int) -> None:
        # Best-effort delete by metadata first.
        self.collection.delete(where={"article_id": article_id})

        # Older collections may store article_id metadata inconsistently or use
        # legacy chunk ids, so sweep ids as a fallback.
        try:
            existing = self.collection.get(include=["metadatas"])
        except Exception:
            return

        ids = existing.get("ids") or []
        metadatas = existing.get("metadatas") or []
        ids_to_delete: list[str] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            metadata_article_id = metadata.get("article_id") if isinstance(metadata, dict) else None
            if str(metadata_article_id).strip() == str(article_id):
                ids_to_delete.append(chunk_id)
                continue
            try:
                if parse_article_id_from_chunk_id(str(chunk_id)) == article_id:
                    ids_to_delete.append(chunk_id)
            except ValueError:
                continue

        if ids_to_delete:
            self.collection.delete(ids=ids_to_delete)

    def search(self, query: str, top_k: int = 5, *, allowed_article_ids: set[int] | None = None) -> List[Dict]:
        query_embedding = self.embedder.encode(query).tolist()
        chunk_limit = max(top_k * self.max_matched_chunks, top_k)
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": chunk_limit,
        }
        provider = getattr(self, "eligible_article_ids_provider", None)
        if provider is not None:
            live_eligible_ids = set(provider())
            allowed_article_ids = (
                live_eligible_ids
                if allowed_article_ids is None
                else set(allowed_article_ids) & live_eligible_ids
            )
        if allowed_article_ids is not None:
            if not allowed_article_ids:
                return []
            query_kwargs["where"] = {"article_id": {"$in": sorted(allowed_article_ids)}}
        results = self.collection.query(**query_kwargs)
        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        grouped: dict[int, dict] = {}
        ids = results["ids"][0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]

        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if metadatas and len(metadatas) > index else {}
            document = documents[index] if documents and len(documents) > index else ""
            distance = distances[index] if distances and len(distances) > index else 0.0
            if metadata.get("source") == "news" and metadata.get("content_validation_status") != "verified":
                continue
            article_id = int(metadata.get("article_id") or parse_article_id_from_chunk_id(chunk_id))
            story_group_id = int(metadata.get("story_group_id") or article_id)

            grouped_result = grouped.setdefault(
                story_group_id,
                {
                    "id": str(article_id),
                    "article_id": article_id,
                    "story_group_id": story_group_id,
                    "title": metadata.get("title", "Untitled"),
                    "url": metadata.get("canonical_url") or metadata.get("original_url") or metadata.get("url") or "N/A",
                    "original_url": metadata.get("original_url"),
                    "canonical_url": metadata.get("canonical_url") or None,
                    "summary": metadata.get("summary") or None,
                    "source": metadata.get("source"),
                    "published_at": metadata.get("published_at"),
                    "content_sha256": metadata.get("content_sha256"),
                    "publisher_source_id": metadata.get("publisher_source_id") or None,
                    "publisher_source_name": metadata.get("publisher_source_name") or None,
                    "distance": distance,
                    "matched_chunks": [],
                    "publisher_sources": {},
                    "duplicate_article_ids": [],
                },
            )
            if article_id not in grouped_result["duplicate_article_ids"]:
                grouped_result["duplicate_article_ids"].append(article_id)
            grouped_result["publisher_sources"][article_id] = {
                "article_id": article_id,
                "source_id": metadata.get("publisher_source_id") or None,
                "source_name": metadata.get("publisher_source_name") or None,
                "url": metadata.get("canonical_url") or metadata.get("original_url") or metadata.get("url") or "N/A",
            }
            grouped_result["distance"] = min(grouped_result["distance"], distance)
            grouped_result["matched_chunks"].append(
                {
                    "chunk_id": chunk_id,
                    "chunk_index": int(metadata.get("chunk_index", 0)),
                    "content": document,
                    "distance": distance,
                }
            )

        article_results = []
        for grouped_result in grouped.values():
            matched_chunks = sorted(grouped_result["matched_chunks"], key=lambda item: item["distance"])
            top_chunks = matched_chunks[: self.max_matched_chunks]
            article_results.append(
                {
                    **grouped_result,
                    "publisher_sources": list(grouped_result["publisher_sources"].values()),
                    "matched_chunks": top_chunks,
                    "content": "\n".join(chunk["content"] for chunk in top_chunks if chunk["content"]).strip(),
                }
            )

        article_results.sort(key=lambda item: item["distance"])
        return article_results[:top_k]


def build_chunk_id(article_id: int, chunk_index: int) -> str:
    return f"{article_id}:{chunk_index}"


def parse_article_id_from_chunk_id(chunk_id: str) -> int:
    text = str(chunk_id).strip()
    if not text:
        raise ValueError("chunk_id must be non-empty")
    prefix = text.split(":", 1)[0]
    if prefix.isdigit():
        return int(prefix)
    match = re.search(r"(?:^|[_/:-])(\d+)$", text)
    if match:
        return int(match.group(1))
    raise ValueError(f"Could not parse article id from chunk_id: {chunk_id!r}")


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[str]:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - chunk_overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def _resolve_local_files_only() -> bool:
    raw_value = os.getenv("SENTENCE_TRANSFORMERS_LOCAL_FILES_ONLY")
    if raw_value is None:
        return True
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}
