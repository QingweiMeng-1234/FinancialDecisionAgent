from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_attribution import CandidateMatch  # noqa: E402
from event_collector.kb_contracts import KBReleaseContext  # noqa: E402
from event_collector.kb_retrieval_service import (  # noqa: E402
    KBRetrievalError,
    RetrievalRequest,
    VectorCandidate,
    retrieve_evidence_v2,
)
from event_collector.kb_signal_policy import load_signal_policy  # noqa: E402


SHA = "a" * 64
POLICY = load_signal_policy(ROOT / "config" / "entity_kb_signal_policy_v1.json")


def _release(snapshot="kbs_01"):
    return KBReleaseContext(
        namespace="financial-agent",
        release_id=f"kbr_{snapshot}",
        snapshot_id=snapshot,
        snapshot_checksum=SHA,
        schema_version="2.0",
        resolver_policy_version="resolver.v1",
        resolver_policy_checksum=SHA,
        freshness_policy_version="freshness.v1",
        freshness_policy_checksum=SHA,
        signal_policy_version="signals.price_in.v1",
        signal_policy_checksum=POLICY.content_sha256,
        evaluation_run_id="kbeval_01",
        evaluation_manifest_hash=SHA,
        id_algorithm_version="domain-id.v1",
    )


class SwitchingReleaseProvider:
    def __init__(self):
        self.calls = 0

    def pin_active_release(self, namespace, as_of):
        self.calls += 1
        return _release("kbs_01" if self.calls == 1 else "kbs_02")


class StaticVectorStore:
    def search(self, query, top_k):
        return (
            VectorCandidate(
                article_id=1,
                title="Direct product update",
                raw_vector_distance=0.21,
                semantic_score=0.84,
                matches=(
                    CandidateMatch(
                        signal_type="direct_product_owner",
                        target_entity_id="entity:company:msft",
                        matched_entity_id="entity:product:azure",
                        alias_id=None,
                        relationship_id="rel_product",
                        strength_class="strong",
                        freshness_state="current",
                        ambiguity_state="unique",
                        evidence_id="evidence_01",
                        verified=True,
                    ),
                ),
            ),
            VectorCandidate(
                article_id=2,
                title="Low semantic dependency signal",
                raw_vector_distance=0.80,
                semantic_score=0.10,
                matches=(
                    CandidateMatch(
                        signal_type="indirect_dependency",
                        target_entity_id="entity:company:msft",
                        matched_entity_id="entity:company:supplier",
                        alias_id=None,
                        relationship_id="rel_dependency",
                        strength_class="strong",
                        freshness_state="current",
                        ambiguity_state="unique",
                        evidence_id="evidence_02",
                        verified=True,
                    ),
                ),
            ),
        )


def test_request_pins_release_once_and_preserves_score_components():
    provider = SwitchingReleaseProvider()
    result = retrieve_evidence_v2(
        RetrievalRequest(
            query="Microsoft outlook",
            retrieval_intent="direct",
            kb_mode="required",
            top_k=2,
            retrieval_top_k=2,
            as_of="2026-08-15T08:00:00Z",
        ),
        release_provider=provider,
        vector_store=StaticVectorStore(),
        signal_policy=POLICY,
        reranker=lambda candidates: (1, 2),
    )

    assert provider.calls == 1
    assert result.release.snapshot_id == "kbs_01"
    assert {article.kb_snapshot_id for article in result.articles} == {"kbs_01"}
    first = result.articles[0]
    assert first.raw_vector_distance == 0.21
    assert first.semantic_score == 0.84
    assert first.weighted_semantic_score == pytest.approx(0.588)
    assert first.combined_pre_rerank_score == pytest.approx(0.638)


def test_low_semantic_candidate_is_not_hard_gated_and_rerank_keeps_score_explanation():
    result = retrieve_evidence_v2(
        RetrievalRequest(
            query="Microsoft dependencies",
            retrieval_intent="indirect",
            kb_mode="required",
            top_k=2,
            retrieval_top_k=2,
            as_of="2026-08-15T08:00:00Z",
        ),
        release_provider=SwitchingReleaseProvider(),
        vector_store=StaticVectorStore(),
        signal_policy=POLICY,
        reranker=lambda candidates: (2, 1),
    )

    assert [article.article_id for article in result.articles] == [2, 1]
    low_semantic = result.articles[0]
    assert low_semantic.semantic_score == 0.10
    assert low_semantic.kb_total_after_cap == 0.30
    assert low_semantic.pre_rerank_position == 2
    assert low_semantic.rerank_position == 1


def test_reranker_cannot_invent_or_drop_candidate_ids():
    with pytest.raises(KBRetrievalError) as caught:
        retrieve_evidence_v2(
            RetrievalRequest(
                query="Microsoft",
                retrieval_intent="direct",
                kb_mode="required",
                top_k=2,
                retrieval_top_k=2,
                as_of="2026-08-15T08:00:00Z",
            ),
            release_provider=SwitchingReleaseProvider(),
            vector_store=StaticVectorStore(),
            signal_policy=POLICY,
            reranker=lambda candidates: (1, 999),
        )

    assert caught.value.code == "RERANKER_FAILED"


def test_release_provider_domain_error_is_normalized_before_vector_retrieval():
    class ProviderError(RuntimeError):
        code = "KB_REQUIRED_UNAVAILABLE"

    class FailingProvider:
        def pin_active_release(self, namespace, as_of):
            raise ProviderError("active release is unavailable")

    vector = StaticVectorStore()
    vector.search = lambda query, top_k: pytest.fail("vector search must not run")

    with pytest.raises(KBRetrievalError) as caught:
        retrieve_evidence_v2(
            RetrievalRequest(
                query="Microsoft",
                retrieval_intent="direct",
                kb_mode="required",
                top_k=1,
                retrieval_top_k=1,
                as_of="2026-08-15T08:00:00Z",
            ),
            release_provider=FailingProvider(),
            vector_store=vector,
            signal_policy=POLICY,
            reranker=lambda candidates: (1,),
        )

    assert caught.value.code == "KB_REQUIRED_UNAVAILABLE"
    assert str(caught.value) == "active release is unavailable"
