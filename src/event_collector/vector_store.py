"""
Vector store for semantic retrieval of news articles using ChromaDB.
Uses sentence-transformers for embeddings.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import chromadb
from sentence_transformers import SentenceTransformer

from event_collector.news_storage import NewsArticle


class VectorStore(ABC):
    """Abstract base class for vector storage and semantic search."""
    
    @abstractmethod
    def add_article(self, article_id: int, article: NewsArticle) -> str:
        """Add or replace an article in the vector store by stable article ID."""
        pass
    
    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search for articles semantically similar to the query.
        Returns a list of articles with metadata.
        """
        pass


class ChromaVectorStore(VectorStore):
    """
    Vector store backed by ChromaDB with sentence-transformers embeddings.
    """
    
    def __init__(
        self,
        persist_dir: str = "./chroma_data",
        model_name: str = "all-MiniLM-L6-v2",
        collection_name: str = "news_articles"
    ):
        """
        Initialize ChromaVectorStore.
        
        Args:
            persist_dir: Directory to persist ChromaDB data
            model_name: Sentence-transformers model to use
            collection_name: Name of the ChromaDB collection
        """
        self.persist_dir = persist_dir
        self.model_name = model_name
        self.collection_name = collection_name
        
        # Initialize ChromaDB client with persistence using new API
        self.client = chromadb.PersistentClient(path=persist_dir)
        
        # Initialize embedding model
        self.embedder = SentenceTransformer(model_name)
        
        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        
    
    def add_article(self, article_id: int, article: NewsArticle) -> str:
        """
        Add or replace an article in the vector store.
        Generates embeddings and stores article metadata.
        """
        vector_id = str(article_id)
        
        # Prefer compact summary-led embeddings when available.
        text_to_embed = self._build_embedding_text(article)
        
        # Generate embedding
        embedding = self.embedder.encode(text_to_embed).tolist()
        
        # Prepare metadata (ChromaDB can store arbitrary metadata)
        metadata = {
            "source": article.source,
            "url": article.url,
            "published_at": article.published_at.isoformat(),
            "has_summary": article.summary is not None,
        }
        
        # Prepare document (what we show in results)
        document = f"{article.title}\n{article.description}"
        
        # Upsert to collection so reindexing replaces prior vectors cleanly.
        self.collection.upsert(
            ids=[vector_id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[document],
            # Store full article as additional data
            uris=[article.url],
        )
        
        # Store full content separately for retrieval
        self._store_full_content(vector_id, article)
        
        return vector_id
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search for semantically similar articles.
        
        Args:
            query: Search query text
            top_k: Number of results to return
            
        Returns:
            List of dicts with article metadata and content
        """
        # Embed the query
        query_embedding = self.embedder.encode(query).tolist()
        
        # Search the collection
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        # Format results
        formatted_results = []
        if results and results['ids'] and len(results['ids']) > 0:
            for i, article_id in enumerate(results['ids'][0]):
                result = {
                    "id": article_id,
                    "distance": results['distances'][0][i] if results['distances'] else 0,
                }
                
                # Add metadata
                if results['metadatas'] and len(results['metadatas'][0]) > i:
                    result.update(results['metadatas'][0][i])
                
                # Add document
                if results['documents'] and len(results['documents'][0]) > i:
                    result["title"] = results['documents'][0][i].split('\n')[0]
                
                # Add full content if available
                full_content = self._retrieve_full_content(article_id)
                if full_content:
                    result.update(full_content)
                
                formatted_results.append(result)
        
        return formatted_results
    
    def _store_full_content(self, article_id: str, article: NewsArticle):
        """Store full article content in memory cache."""
        if not hasattr(self, '_content_cache'):
            self._content_cache = {}
        
        self._content_cache[article_id] = {
            "title": article.title,
            "description": article.description,
            "content": article.content,
            "source": article.source,
            "url": article.url,
            "published_at": article.published_at.isoformat(),
            "summary": article.summary,
        }
    
    def _retrieve_full_content(self, article_id: str) -> Optional[Dict]:
        """Retrieve full article content from memory cache."""
        if not hasattr(self, '_content_cache'):
            return None
        return self._content_cache.get(article_id)

    def _build_embedding_text(self, article: NewsArticle) -> str:
        if article.summary:
            return f"{article.summary}\n{article.title}\n{article.description}"
        return f"{article.title}. {article.description}. {article.content}"
