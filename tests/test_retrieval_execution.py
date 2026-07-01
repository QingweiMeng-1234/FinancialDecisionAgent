from __future__ import annotations

import threading
import time

from event_collector.retrieval_execution import RetrievalWorkItem, execute_retrieval_batch
from event_collector.retrieval_orchestration import RetrievalEvidenceBundle


class DummyVectorStore:
    pass


def _bundle_for(query: str) -> RetrievalEvidenceBundle:
    return RetrievalEvidenceBundle(
        query=query,
        search_results=[],
        reranked_results=[],
        evidence=[],
        rerank_metadata=None,
    )


def test_execute_retrieval_batch_preserves_input_order_and_bounds_concurrency():
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_retrieve(query, vector_store, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _bundle_for(query)

    results = execute_retrieval_batch(
        [
            RetrievalWorkItem(key=f"T{i}", query=f"T{i}", top_k=3)
            for i in range(5)
        ],
        DummyVectorStore(),
        max_concurrency=2,
        provider_mode="shared_read",
        retrieve_bundle_fn=fake_retrieve,
    )

    assert [result.key for result in results] == ["T0", "T1", "T2", "T3", "T4"]
    assert all(result.succeeded for result in results)
    assert max_active <= 2


def test_execute_retrieval_batch_normalizes_timeout_and_errors():
    def fake_retrieve(query, vector_store, **kwargs):
        if query == "TIMEOUT":
            raise TimeoutError("too slow")
        if query == "ERROR":
            raise RuntimeError("boom")
        return _bundle_for(query)

    results = execute_retrieval_batch(
        [
            RetrievalWorkItem(key="ok", query="OK", top_k=2),
            RetrievalWorkItem(key="timeout", query="TIMEOUT", top_k=2),
            RetrievalWorkItem(key="error", query="ERROR", top_k=2),
        ],
        DummyVectorStore(),
        max_concurrency=3,
        provider_mode="shared_read",
        retrieve_bundle_fn=fake_retrieve,
    )

    assert [result.status for result in results] == ["success", "timeout", "error"]
    assert results[1].error_message == "too slow"
    assert results[2].error_message == "boom"


def test_execute_retrieval_batch_requires_explicit_safe_model_for_shared_provider():
    def fake_retrieve(query, vector_store, **kwargs):
        return _bundle_for(query)

    try:
        execute_retrieval_batch(
            [
                RetrievalWorkItem(key="a", query="A", top_k=2),
                RetrievalWorkItem(key="b", query="B", top_k=2),
            ],
            DummyVectorStore(),
            max_concurrency=2,
            retrieve_bundle_fn=fake_retrieve,
        )
    except ValueError as exc:
        assert "provider_mode" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected explicit shared-provider safety failure")


def test_execute_retrieval_batch_reuses_thread_local_resources_from_factory():
    seen_resource_ids: set[int] = set()

    class CountingStore:
        pass

    def fake_retrieve(query, vector_store, **kwargs):
        seen_resource_ids.add(id(vector_store))
        time.sleep(0.02)
        return _bundle_for(query)

    results = execute_retrieval_batch(
        [
            RetrievalWorkItem(key=f"T{i}", query=f"T{i}", top_k=2)
            for i in range(6)
        ],
        lambda: CountingStore(),
        max_concurrency=3,
        retrieve_bundle_fn=fake_retrieve,
    )

    assert all(result.succeeded for result in results)
    assert len(seen_resource_ids) <= 3
