from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.kb_api import PUBLIC_KB_V2_TOOLS, retrieve_supporting_articles_v2  # noqa: E402
from event_collector.kb_attribution import CandidateMatch  # noqa: E402
from event_collector.kb_contracts import KBReleaseContext  # noqa: E402
from event_collector.kb_retrieval_service import KBRetrievalError, VectorCandidate  # noqa: E402
from event_collector.kb_signal_policy import load_signal_policy  # noqa: E402


POLICY = load_signal_policy(ROOT / "config" / "entity_kb_signal_policy_v1.json")
SHA = "a" * 64


class UnavailableProvider:
    def __init__(self):
        self.calls = 0

    def pin_active_release(self, namespace, as_of):
        self.calls += 1
        raise KBRetrievalError("KB_REQUIRED_UNAVAILABLE", "no active release")


class CountingVectorStore:
    def __init__(self):
        self.calls = 0

    def search(self, query, top_k):
        self.calls += 1
        return (
            VectorCandidate(
                article_id=7,
                title="Semantic result",
                raw_vector_distance=0.4,
                semantic_score=0.6,
                matches=(),
            ),
        )


def _request(mode):
    return {
        "query": "Microsoft outlook",
        "retrieval_intent": "direct",
        "kb_mode": mode,
        "top_k": 1,
        "retrieval_top_k": 1,
        "as_of": "2026-08-15T08:00:00Z",
    }


def test_required_mode_errors_before_vector_retrieval_when_kb_unavailable():
    provider = UnavailableProvider()
    vector = CountingVectorStore()

    response = retrieve_supporting_articles_v2(
        _request("required"),
        release_provider=provider,
        vector_store=vector,
        signal_policy=POLICY,
        reranker=lambda candidates: tuple(item.article_id for item in candidates),
        request_id="req_required",
    )

    assert response["status"] == "error"
    assert response["data"] is None
    assert response["error"]["code"] == "KB_REQUIRED_UNAVAILABLE"
    assert provider.calls == 1
    assert vector.calls == 0


def test_preferred_mode_degrades_visibly_with_complete_null_version_context():
    provider = UnavailableProvider()
    vector = CountingVectorStore()

    response = retrieve_supporting_articles_v2(
        _request("preferred"),
        release_provider=provider,
        vector_store=vector,
        signal_policy=POLICY,
        reranker=lambda candidates: tuple(item.article_id for item in candidates),
        request_id="req_preferred",
    )

    assert response["status"] == "degraded"
    context = response["data"]["kb_context"]
    assert context["requested_mode"] == "preferred"
    assert context["applied_mode"] == "semantic_only"
    assert context["degradation_reason"] == "KB_REQUIRED_UNAVAILABLE"
    assert context["release_id"] is None
    assert context["signal_policy_version"] is None
    assert context["as_of"] == "2026-08-15T08:00:00Z"
    assert response["data"]["article_count"] == len(response["data"]["articles"]) == 1
    assert response["data"]["articles"][0]["score"]["kb_total_after_cap"] == 0.0
    assert response["warnings"][0]["code"] == "KB_REQUIRED_UNAVAILABLE"


def test_disabled_mode_never_calls_release_provider_and_is_not_degraded():
    provider = UnavailableProvider()
    vector = CountingVectorStore()

    response = retrieve_supporting_articles_v2(
        _request("disabled"),
        release_provider=provider,
        vector_store=vector,
        signal_policy=POLICY,
        reranker=lambda candidates: tuple(item.article_id for item in candidates),
        request_id="req_disabled",
    )

    assert response["status"] == "success"
    assert response["data"]["kb_context"]["applied_mode"] == "semantic_only"
    assert response["data"]["kb_context"]["degradation_reason"] is None
    assert provider.calls == 0
    assert vector.calls == 1


def test_public_v2_surface_contains_no_administrative_mutation():
    assert PUBLIC_KB_V2_TOOLS == {
        "resolve_financial_entities_v2",
        "get_entity_profile_v2",
        "retrieve_supporting_articles_v2",
        "query_news_research_v2",
    }
    assert not any("release" in name or "snapshot" in name for name in PUBLIC_KB_V2_TOOLS)


def test_applied_response_preserves_article_metadata_intent_and_full_signal_contract():
    class Provider:
        def pin_active_release(self, namespace, as_of):
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

    class Vector:
        def search(self, query, top_k):
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
                        CandidateMatch(
                            signal_type="direct_executive_context",
                            target_entity_id="entity:company:msft",
                            matched_entity_id="entity:person:satya",
                            alias_id=None,
                            relationship_id="rel_ceo",
                            strength_class="strong",
                            freshness_state="stale",
                            ambiguity_state="unique",
                            evidence_id="evidence_02",
                            verified=True,
                        ),
                    ),
                    story_group_id=40,
                    url="https://example.com/azure",
                    summary="Demand increased.",
                    snippet="Azure demand increased.",
                    published_at="2026-08-14T00:00:00Z",
                ),
            )

    response = retrieve_supporting_articles_v2(
        _request("required"),
        release_provider=Provider(),
        vector_store=Vector(),
        signal_policy=POLICY,
        reranker=lambda candidates: (42,),
        request_id="req_applied",
    )

    article = response["data"]["articles"][0]
    assert article["story_group_id"] == 40
    assert article["url"] == "https://example.com/azure"
    assert article["summary"] == "Demand increased."
    assert article["snippet"] == "Azure demand increased."
    assert article["published_at"] == "2026-08-14T00:00:00Z"
    assert article["retrieval_intent"] == "direct"
    assert article["score"]["signal_policy_version"] == POLICY.policy_version
    signal = article["score"]["kb_signals"][0]
    assert signal["target_entity_id"] == "entity:company:msft"
    assert signal["matched_entity_id"] == "entity:product:azure"
    assert signal["relationship_id"] == "rel_owns_azure"
    assert signal["direction"] == "direct"
    assert signal["freshness_state"] == "current"
    assert signal["ambiguity_state"] == "unique"
    assert signal["evidence_id"] == "evidence_01"
    assert signal["kb_snapshot_id"] == "kbs_01"
    assert signal["freshness_policy_version"] == "freshness.v1"
    assert signal["signal_policy_version"] == POLICY.policy_version
    assert article["score"]["kb_signals"][1]["rejection_reason"] == "STALE_FACT"


def test_applied_response_keeps_pinned_signal_policy_when_article_has_no_signals():
    class Provider:
        def pin_active_release(self, namespace, as_of):
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

    response = retrieve_supporting_articles_v2(
        _request("required"),
        release_provider=Provider(),
        vector_store=CountingVectorStore(),
        signal_policy=POLICY,
        reranker=lambda candidates: (7,),
        request_id="req_no_signal",
    )

    article = response["data"]["articles"][0]
    assert article["score"]["kb_signals"] == []
    assert article["score"]["signal_policy_version"] == POLICY.policy_version
