"""Bounded retrieval execution for fan-out evidence collection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import perf_counter
import threading
from typing import Any, Callable

from event_collector.entity_kb import SQLiteEntityStore
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent
from event_collector.retrieval_orchestration import (
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_SNIPPET_CHARS,
    RetrievalEvidenceBundle,
    retrieve_evidence_bundle,
)
from event_collector.reranking import RAGRerankingAgent
from event_collector.vector_store import VectorStore


@dataclass(frozen=True)
class RetrievalWorkItem:
    """One retrieval task in a fan-out execution batch."""

    key: str
    query: str
    top_k: int
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS
    snippet_chars: int = DEFAULT_SNIPPET_CHARS
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT


@dataclass
class RetrievalWorkResult:
    """Normalized outcome for one retrieval task."""

    key: str
    query: str
    status: str
    bundle: RetrievalEvidenceBundle | None
    error_message: str | None
    elapsed_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.status == "success" and self.bundle is not None


def execute_retrieval_batch(
    work_items: list[RetrievalWorkItem],
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    reranking_agent: RAGRerankingAgent | None = None,
    company_kb_provider: SQLiteEntityStore | Callable[[], SQLiteEntityStore | None] | None = None,
    max_concurrency: int = 5,
    provider_mode: str = "auto",
    retrieve_bundle_fn: Callable[..., RetrievalEvidenceBundle] = retrieve_evidence_bundle,
) -> list[RetrievalWorkResult]:
    """Execute retrieval tasks with bounded concurrency and stable output ordering."""
    if not work_items:
        return []

    resolved_provider_mode = _resolve_provider_mode(vector_store_provider, provider_mode, max_concurrency)
    scoped_vector_store_provider = _prepare_provider_scope(vector_store_provider, resolved_provider_mode)
    scoped_company_kb_provider = _prepare_provider_scope(company_kb_provider, "thread_local_factory")

    try:
        if max_concurrency <= 1 or len(work_items) == 1:
            return [
                _execute_work_item(
                    work_item,
                    scoped_vector_store_provider,
                    reranking_agent=reranking_agent,
                    company_kb_provider=scoped_company_kb_provider,
                    retrieve_bundle_fn=retrieve_bundle_fn,
                )
                for work_item in work_items
            ]

        ordered_results: list[RetrievalWorkResult | None] = [None] * len(work_items)
        worker_count = min(max(1, max_concurrency), len(work_items))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(
                    _execute_work_item,
                    work_item,
                    scoped_vector_store_provider,
                    reranking_agent=reranking_agent,
                    company_kb_provider=scoped_company_kb_provider,
                    retrieve_bundle_fn=retrieve_bundle_fn,
                ): index
                for index, work_item in enumerate(work_items)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                ordered_results[index] = future.result()

        return [result for result in ordered_results if result is not None]
    finally:
        _close_provider_scope(scoped_vector_store_provider)
        _close_provider_scope(scoped_company_kb_provider)


def _execute_work_item(
    work_item: RetrievalWorkItem,
    vector_store_provider: VectorStore | Callable[[], VectorStore],
    *,
    reranking_agent: RAGRerankingAgent | None,
    company_kb_provider: SQLiteEntityStore | Callable[[], SQLiteEntityStore | None] | None,
    retrieve_bundle_fn: Callable[..., RetrievalEvidenceBundle],
) -> RetrievalWorkResult:
    started_at = perf_counter()
    company_kb: SQLiteEntityStore | None = None

    try:
        vector_store = _resolve_provider(vector_store_provider)
        company_kb = _resolve_provider(company_kb_provider)
        bundle = retrieve_bundle_fn(
            work_item.query,
            vector_store,
            top_k=work_item.top_k,
            retrieval_top_k=work_item.retrieval_top_k,
            excerpt_chars=work_item.excerpt_chars,
            snippet_chars=work_item.snippet_chars,
            reranking_agent=reranking_agent,
            company_kb=company_kb,
            retrieval_intent=work_item.retrieval_intent,
        )
        return RetrievalWorkResult(
            key=work_item.key,
            query=work_item.query,
            status="success",
            bundle=bundle,
            error_message=None,
            elapsed_seconds=perf_counter() - started_at,
        )
    except TimeoutError as exc:
        return RetrievalWorkResult(
            key=work_item.key,
            query=work_item.query,
            status="timeout",
            bundle=None,
            error_message=str(exc) or "retrieval execution timed out",
            elapsed_seconds=perf_counter() - started_at,
        )
    except Exception as exc:  # pragma: no cover - exception shapes vary by provider/runtime
        return RetrievalWorkResult(
            key=work_item.key,
            query=work_item.query,
            status="error",
            bundle=None,
            error_message=str(exc) or exc.__class__.__name__,
            elapsed_seconds=perf_counter() - started_at,
        )
    finally:
        if (
            company_kb is not None
            and hasattr(company_kb, "close")
            and not hasattr(company_kb_provider, "__thread_local_scope__")
        ):
            company_kb.close()


def _resolve_provider_mode(provider: Any, provider_mode: str, max_concurrency: int) -> str:
    if provider_mode not in {"auto", "shared_read", "thread_local_factory"}:
        raise ValueError(f"Unsupported provider_mode: {provider_mode}")
    if max_concurrency <= 1:
        return "shared_read"
    if provider_mode == "shared_read":
        return "shared_read"
    if provider_mode == "thread_local_factory":
        if not callable(provider):
            raise ValueError("provider_mode='thread_local_factory' requires a callable provider")
        return "thread_local_factory"
    if callable(provider):
        return "thread_local_factory"
    raise ValueError(
        "Concurrent retrieval requires an explicit provider_mode or a callable provider; "
        "shared providers are not assumed thread-safe by default. "
        "Choose provider_mode='shared_read' only for proven safe shared resources."
    )


def _prepare_provider_scope(provider: Any, provider_mode: str) -> Any:
    if provider is None:
        return None
    if provider_mode != "thread_local_factory" or not callable(provider):
        return provider
    return _ThreadLocalProviderScope(provider)


def _close_provider_scope(provider: Any) -> None:
    if hasattr(provider, "close_all"):
        provider.close_all()


class _ThreadLocalProviderScope:
    __thread_local_scope__ = True

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._local = threading.local()
        self._resources: list[Any] = []
        self._lock = threading.Lock()

    def __call__(self):
        if not hasattr(self._local, "resource"):
            with self._lock:
                if not hasattr(self._local, "resource"):
                    resource = self._factory()
                    self._local.resource = resource
                    self._resources.append(resource)
        return self._local.resource

    def close_all(self) -> None:
        with self._lock:
            resources = list(self._resources)
            self._resources.clear()
        for resource in resources:
            if hasattr(resource, "close"):
                resource.close()


def _resolve_provider(provider: Any) -> Any:
    if provider is None:
        return None
    if callable(provider):
        return provider()
    return provider
