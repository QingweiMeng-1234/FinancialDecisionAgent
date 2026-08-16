from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.entity_resolution import AliasFact, EntityFact, RelationshipFact  # noqa: E402
from event_collector.kb_attribution import CandidateMatch  # noqa: E402
from event_collector.kb_contracts import KBReleaseContext  # noqa: E402
from event_collector.kb_retrieval_pipeline import retrieve_resolved_evidence_v2  # noqa: E402
from event_collector.kb_retrieval_service import RetrievalRequest, VectorCandidate  # noqa: E402
from event_collector.kb_signal_policy import load_signal_policy  # noqa: E402


SHA = "a" * 64
POLICY = load_signal_policy(ROOT / "config" / "entity_kb_signal_policy_v1.json")


class Provider:
    def __init__(self):
        self.calls = 0

    def pin_active_release(self, namespace, as_of):
        self.calls += 1
        return KBReleaseContext(
            namespace=namespace,
            release_id="kbr_01",
            snapshot_id="kbs_01",
            snapshot_checksum=SHA,
            schema_version="2.0",
            resolver_policy_version="resolver.v1",
            resolver_policy_checksum=SHA,
            freshness_policy_version="freshness.v1",
            freshness_policy_checksum=SHA,
            signal_policy_version=POLICY.policy_version,
            signal_policy_checksum=POLICY.content_sha256,
            evaluation_run_id="kbeval_01",
            evaluation_manifest_hash=SHA,
            id_algorithm_version="domain-id.v1",
        )


class CapturingVectorStore:
    def __init__(self):
        self.query = None

    def search(self, query, top_k):
        self.query = query
        return (
            VectorCandidate(
                article_id=42,
                title="Azure demand update",
                raw_vector_distance=0.2,
                semantic_score=0.8,
                matches=(
                    CandidateMatch(
                        signal_type="direct_product_owner",
                        target_entity_id="entity:company:msft",
                        matched_entity_id="entity:product:azure",
                        alias_id=None,
                        relationship_id="rel_owns_azure",
                        strength_class="strong",
                        freshness_state="current",
                        ambiguity_state="unique",
                        evidence_id="evidence_01",
                        verified=True,
                    ),
                ),
            ),
        )


def test_resolver_expansion_vector_attribution_and_score_share_one_pinned_release():
    entities = (
        EntityFact("entity:company:msft", "company", "Microsoft Corporation", "MSFT"),
        EntityFact("entity:product:azure", "product", "Microsoft Azure", None),
    )
    aliases = (
        AliasFact(
            "alias_msft_zh",
            "entity:company:msft",
            "微软",
            "strong",
            "unique",
            language="zh-CN",
        ),
        AliasFact(
            "alias_microsoft",
            "entity:company:msft",
            "Microsoft",
            "strong",
            "unique",
        ),
        AliasFact(
            "alias_azure",
            "entity:product:azure",
            "Azure",
            "strong",
            "unique",
        ),
    )
    relationships = (
        RelationshipFact(
            "rel_owns_azure",
            "entity:company:msft",
            "owns_product",
            "entity:product:azure",
        ),
    )
    provider = Provider()
    vector = CapturingVectorStore()

    result = retrieve_resolved_evidence_v2(
        RetrievalRequest(
            query="微软 Azure 需求",
            retrieval_intent="direct",
            kb_mode="required",
            top_k=1,
            retrieval_top_k=1,
            as_of="2026-08-15T08:00:00Z",
        ),
        entities=entities,
        aliases=aliases,
        relationships=relationships,
        release_provider=provider,
        vector_store=vector,
        signal_policy=POLICY,
        reranker=lambda candidates: (42,),
    )

    assert provider.calls == 1
    assert result.resolution.selected_entity.entity_id == "entity:company:msft"
    assert result.expansion.effective_query == vector.query
    assert "MSFT" in vector.query and "Microsoft Azure" in vector.query
    article = result.retrieval.articles[0]
    assert article.kb_snapshot_id == "kbs_01"
    assert article.weighted_semantic_score == 0.56
    assert article.kb_total_after_cap == 0.05
    assert article.combined_pre_rerank_score == 0.61
