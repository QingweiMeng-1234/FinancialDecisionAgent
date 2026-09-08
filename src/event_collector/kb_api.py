"""Storage-neutral v2 API adapter and explicit KB-mode mapping."""

from __future__ import annotations

from typing import Any, Callable

from event_collector.kb_retrieval_service import (
    KBRetrievalError,
    RetrievalRequest,
    RetrievedArticleV2,
    retrieve_evidence_v2,
)
from event_collector.kb_signal_policy import SignalPolicy


PUBLIC_KB_V2_TOOLS = {
    "resolve_financial_entities_v2",
    "get_entity_profile_v2",
    "retrieve_supporting_articles_v2",
    "query_news_research_v2",
}
_DEGRADABLE_KB_ERRORS = {
    "KB_REQUIRED_UNAVAILABLE",
    "ENTITY_AMBIGUOUS",
    "ENTITY_UNRESOLVED",
}


def retrieve_supporting_articles_v2(
    payload: dict[str, Any],
    *,
    release_provider: Any,
    vector_store: Any,
    signal_policy: SignalPolicy,
    reranker: Callable,
    request_id: str,
) -> dict[str, Any]:
    try:
        request = RetrievalRequest(**payload)
    except (TypeError, KBRetrievalError) as error:
        return _error(request_id, "INVALID_ARGUMENT", str(error))

    if request.kb_mode == "disabled":
        return _semantic_only_response(
            request,
            request_id=request_id,
            vector_store=vector_store,
            reranker=reranker,
            status="success",
            degradation_reason=None,
        )
    try:
        result = retrieve_evidence_v2(
            request,
            release_provider=release_provider,
            vector_store=vector_store,
            signal_policy=signal_policy,
            reranker=reranker,
        )
    except KBRetrievalError as error:
        if request.kb_mode == "preferred" and error.code in _DEGRADABLE_KB_ERRORS:
            return _semantic_only_response(
                request,
                request_id=request_id,
                vector_store=vector_store,
                reranker=reranker,
                status="degraded",
                degradation_reason=error.code,
            )
        return _error(request_id, error.code, str(error))

    context = {
        "requested_mode": request.kb_mode,
        "applied_mode": "applied",
        "degradation_reason": None,
        "namespace": result.release.namespace,
        "release_id": result.release.release_id,
        "snapshot_id": result.release.snapshot_id,
        "schema_version": result.release.schema_version,
        "resolver_policy_version": result.release.resolver_policy_version,
        "freshness_policy_version": result.release.freshness_policy_version,
        "signal_policy_version": result.release.signal_policy_version,
        "evaluation_run_id": result.release.evaluation_run_id,
        "as_of": request.as_of,
    }
    articles = [_serialize_applied_article(article) for article in result.articles]
    return _envelope(
        request_id,
        "success",
        {
            "query": request.query,
            "kb_context": context,
            "resolution": None,
            "expansion": None,
            "article_count": len(articles),
            "articles": articles,
        },
    )


def _semantic_only_response(
    request: RetrievalRequest,
    *,
    request_id: str,
    vector_store: Any,
    reranker: Callable,
    status: str,
    degradation_reason: str | None,
) -> dict[str, Any]:
    candidates = tuple(vector_store.search(request.query, request.retrieval_top_k))
    candidate_ids = {candidate.article_id for candidate in candidates}
    ordered_ids = tuple(reranker(candidates))
    if len(ordered_ids) != len(candidate_ids) or set(ordered_ids) != candidate_ids:
        return _error(request_id, "RERANKER_FAILED", "reranker candidate set mismatch")
    by_id = {candidate.article_id: candidate for candidate in candidates}
    preordered = sorted(candidates, key=lambda item: (-item.semantic_score, item.article_id))
    pre_positions = {item.article_id: index for index, item in enumerate(preordered, 1)}
    articles = []
    for rerank_position, article_id in enumerate(ordered_ids[: request.top_k], 1):
        candidate = by_id[article_id]
        articles.append(
            {
                "source_id": rerank_position,
                "article_id": candidate.article_id,
                "story_group_id": candidate.story_group_id,
                "title": candidate.title,
                "url": candidate.url,
                "summary": candidate.summary,
                "snippet": candidate.snippet,
                "published_at": candidate.published_at,
                "retrieval_intent": request.retrieval_intent,
                "score": {
                    "raw_vector_distance": candidate.raw_vector_distance,
                    "semantic_score": candidate.semantic_score,
                    "semantic_score_type": "candidate_set_min_max_v1",
                    "semantic_weight": 1.0,
                    "weighted_semantic_score": candidate.semantic_score,
                    "kb_signals": [],
                    "kb_total_before_cap": 0.0,
                    "kb_total_cap": 0.0,
                    "kb_total_after_cap": 0.0,
                    "combined_pre_rerank_score": candidate.semantic_score,
                    "pre_rerank_position": pre_positions[candidate.article_id],
                    "signal_policy_version": None,
                },
                "rerank_position": rerank_position,
                "rerank_reason": None,
                "duplicate_article_ids": [candidate.article_id],
            }
        )
    context = {
        "requested_mode": request.kb_mode,
        "applied_mode": "semantic_only",
        "degradation_reason": degradation_reason,
        "namespace": None if request.kb_mode == "disabled" else request.namespace,
        "release_id": None,
        "snapshot_id": None,
        "schema_version": None,
        "resolver_policy_version": None,
        "freshness_policy_version": None,
        "signal_policy_version": None,
        "evaluation_run_id": None,
        "as_of": request.as_of,
    }
    warnings = []
    if degradation_reason is not None:
        warnings.append(
            {
                "code": degradation_reason,
                "message": "KB was not applied; semantic-only retrieval was used.",
            }
        )
    return _envelope(
        request_id,
        status,
        {
            "query": request.query,
            "kb_context": context,
            "resolution": None,
            "expansion": None,
            "article_count": len(articles),
            "articles": articles,
        },
        warnings=warnings,
    )


def _serialize_applied_article(article: RetrievedArticleV2) -> dict[str, Any]:
    return {
        "source_id": article.rerank_position,
        "article_id": article.article_id,
        "story_group_id": article.story_group_id,
        "title": article.title,
        "url": article.url,
        "summary": article.summary,
        "snippet": article.snippet,
        "published_at": article.published_at,
        "retrieval_intent": article.retrieval_intent,
        "score": {
            "raw_vector_distance": article.raw_vector_distance,
            "semantic_score": article.semantic_score,
            "semantic_score_type": "candidate_set_min_max_v1",
            "semantic_weight": article.semantic_weight,
            "weighted_semantic_score": article.weighted_semantic_score,
            "kb_signals": [
                {
                    "signal_id": item.signal.signal_id,
                    "signal_type": item.signal.signal_type.value,
                    "target_entity_id": item.signal.target_entity_id,
                    "matched_entity_id": item.signal.matched_entity_id,
                    "alias_id": item.signal.alias_id,
                    "relationship_id": item.signal.relationship_id,
                    "strength_class": item.signal.strength_class,
                    "direction": item.signal.direction.value,
                    "freshness_state": item.signal.freshness_state.value,
                    "ambiguity_state": item.signal.ambiguity_state.value,
                    "evidence_id": item.signal.evidence_id,
                    "kb_snapshot_id": item.signal.kb_snapshot_id,
                    "freshness_policy_version": item.signal.freshness_policy_version,
                    "signal_policy_version": item.signal.signal_policy_version,
                    "eligible_for_boost": item.signal.eligible_for_boost,
                    "rejection_reason": item.rejection_reason,
                    "base_contribution": item.base_contribution,
                    "accepted_contribution": item.accepted_contribution,
                    "cap_reduction": item.cap_reduction,
                }
                for item in article.scored_signals
            ],
            "kb_total_before_cap": article.kb_total_before_cap,
            "kb_total_cap": article.kb_total_cap,
            "kb_total_after_cap": article.kb_total_after_cap,
            "combined_pre_rerank_score": article.combined_pre_rerank_score,
            "pre_rerank_position": article.pre_rerank_position,
            "signal_policy_version": article.signal_policy_version,
        },
        "rerank_position": article.rerank_position,
        "rerank_reason": None,
        "duplicate_article_ids": [article.article_id],
    }


def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "api_version": "entity-kb.v2",
        "request_id": request_id,
        "status": "error",
        "data": None,
        "warnings": [],
        "error": {"code": code, "message": message, "retryable": False, "details": None},
    }


def _envelope(
    request_id: str,
    status: str,
    data: dict[str, Any],
    *,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "api_version": "entity-kb.v2",
        "request_id": request_id,
        "status": status,
        "data": data,
        "warnings": warnings or [],
        "error": None,
    }


__all__ = ["PUBLIC_KB_V2_TOOLS", "retrieve_supporting_articles_v2"]
