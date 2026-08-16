"""Resolved Entity KB v2 retrieval application seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from event_collector.entity_resolution import (
    AliasFact,
    EntityFact,
    EntityResolutionError,
    RelationshipFact,
    ResolutionDecision,
    resolve_entities,
)
from event_collector.kb_contracts import KBReleaseContext
from event_collector.kb_query_expansion import (
    ExpansionError,
    QueryExpansion,
    build_query_expansion,
)
from event_collector.kb_retrieval_service import (
    CandidateVectorStore,
    KBRetrievalError,
    ReleaseProvider,
    RetrievalRequest,
    RetrievalResultV2,
    RetrievedArticleV2,
    retrieve_evidence_v2,
)
from event_collector.kb_signal_policy import SignalPolicy


@dataclass(frozen=True)
class ResolvedRetrievalResultV2:
    resolution: ResolutionDecision
    expansion: QueryExpansion
    retrieval: RetrievalResultV2


@dataclass(frozen=True)
class _PinnedReleaseProvider:
    release: KBReleaseContext

    def pin_active_release(self, namespace: str, as_of: str) -> KBReleaseContext:
        return self.release


def retrieve_resolved_evidence_v2(
    request: RetrievalRequest,
    *,
    entities: Iterable[EntityFact],
    aliases: Iterable[AliasFact],
    relationships: Iterable[RelationshipFact],
    release_provider: ReleaseProvider,
    vector_store: CandidateVectorStore,
    signal_policy: SignalPolicy,
    reranker: Callable[[tuple[RetrievedArticleV2, ...]], tuple[int, ...]],
    target_entity_id: str | None = None,
) -> ResolvedRetrievalResultV2:
    """Pin once, resolve, expand, retrieve, attribute, and score one request."""

    try:
        release = release_provider.pin_active_release(request.namespace, request.as_of)
    except KBRetrievalError:
        raise
    except Exception as error:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            raise KBRetrievalError(code, str(error)) from error
        raise

    entity_facts = tuple(entities)
    alias_facts = tuple(aliases)
    relationship_facts = tuple(relationships)
    try:
        resolution = resolve_entities(
            request.query,
            entity_facts,
            alias_facts,
            relationship_facts,
            target_entity_id=target_entity_id,
        )
        expansion = build_query_expansion(
            request.query,
            resolution,
            entities=entity_facts,
            aliases=alias_facts,
            relationships=relationship_facts,
            retrieval_intent=request.retrieval_intent,
        )
    except (EntityResolutionError, ExpansionError) as error:
        code = getattr(error, "code", "ENTITY_UNRESOLVED")
        raise KBRetrievalError(code, str(error)) from error

    expanded_request = RetrievalRequest(
        query=expansion.effective_query,
        retrieval_intent=request.retrieval_intent,
        kb_mode=request.kb_mode,
        top_k=request.top_k,
        retrieval_top_k=request.retrieval_top_k,
        as_of=request.as_of,
        namespace=request.namespace,
    )
    retrieval = retrieve_evidence_v2(
        expanded_request,
        release_provider=_PinnedReleaseProvider(release),
        vector_store=vector_store,
        signal_policy=signal_policy,
        reranker=reranker,
    )
    return ResolvedRetrievalResultV2(resolution, expansion, retrieval)


__all__ = ["ResolvedRetrievalResultV2", "retrieve_resolved_evidence_v2"]
