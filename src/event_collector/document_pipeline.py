"""LangChain-backed document standardization helpers for article indexing."""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from event_collector.news_storage import NewsArticle


def article_to_document(article_id: int, article: NewsArticle) -> Document:
    """Build one standardized LangChain document from a stored article."""
    return Document(
        page_content=" ".join((article.content or "").split()).strip(),
        metadata={
            "article_id": article_id,
            "title": article.title,
            "url": article.canonical_url or article.original_url or article.url,
            "original_url": article.original_url or article.url,
            "canonical_url": article.canonical_url or "",
            "source": article.source,
            "published_at": article.published_at.isoformat(),
            "content_sha256": article.content_sha256 or "",
            "summary": article.summary or "",
            "content_validation_status": article.content_validation_status,
            "publisher_source_id": article.publisher_source_id or "",
            "publisher_source_name": article.publisher_source_name or "",
            "story_group_id": article.story_group_id or article_id,
        },
    )


def build_text_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    """Create the default LangChain splitter used by the indexing pipeline."""
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        is_separator_regex=False,
    )


def split_article_document(
    article_id: int,
    article: NewsArticle,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[Document]:
    """Split one article into chunk documents while preserving metadata."""
    document = article_to_document(article_id, article)
    if not document.page_content:
        return []

    splitter = build_text_splitter(chunk_size, chunk_overlap)
    chunks = splitter.split_documents([document])

    chunk_documents: list[Document] = []
    for index, chunk in enumerate(chunks):
        content = " ".join((chunk.page_content or "").split()).strip()
        if not content:
            continue
        metadata: dict[str, Any] = dict(chunk.metadata)
        metadata["chunk_index"] = index
        chunk_documents.append(Document(page_content=content, metadata=metadata))
    return chunk_documents
