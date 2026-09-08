"""Request-pinned KB-aware retrieval score handoff and rerank boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from event_collector.kb_attribution import CandidateMatch, attribute_matches
from event_collector.kb_contracts import KBReleaseContext
from event_collector.kb_signal_policy import SignalPolicy, ScoredSignal, score_signals


class KBRetrievalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    retrieval_intent: str
    kb_mode: str
    top_k: int
    retrieval_top_k: int
    as_of: str
    namespace: str = "financial-agent"

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise KBRetrievalError("INVALID_ARGUMENT", "query must be non-empty")
        if self.retrieval_intent not in {"direct", "indirect"}:
            raise KBRetrievalError("INVALID_ARGUMENT", "invalid retrieval_intent")
        if self.kb_mode not in {"required", "preferred", "disabled"}:
            raise KBRetrievalError("INVALID_ARGUMENT", "invalid kb_mode")
        if self.retrieval_top_k < self.top_k:
            raise KBRetrievalError("INVALID_ARGUMENT", "retrieval_top_k must be >= top_k")


@dataclass(frozen=True)
class VectorCandidate:
    article_id: int
    title: str
    raw_vector_distance: float | None
    semantic_score: float
    matches: tuple[CandidateMatch, ...]
    story_group_id: int | None = None
    url: str = ""
    summary: str | None = None
    snippet: str = ""
    published_at: str | None = None


@dataclass(frozen=True)
class RetrievedArticleV2:
    article_id: int
    title: str
    raw_vector_distance: float | None
    semantic_score: float
    semantic_weight: float
    weighted_semantic_score: float
    scored_signals: tuple[ScoredSignal, ...]
    kb_total_before_cap: float
    kb_total_cap: float
    kb_total_after_cap: float
    combined_pre_rerank_score: float
    pre_rerank_position: int
    rerank_position: int
    kb_snapshot_id: str
    signal_policy_version: str
    retrieval_intent: str
    story_group_id: int | None
    url: str
    summary: str | None
    snippet: str
    published_at: str | None


@dataclass(frozen=True)
class RetrievalResultV2:
    release: KBReleaseContext
    articles: tuple[RetrievedArticleV2, ...]
    requested_mode: str
    applied_mode: str
    degradation_reason: str | None = None


class ReleaseProvider(Protocol):
    def pin_active_release(self, namespace: str, as_of: str) -> KBReleaseContext: ...


class CandidateVectorStore(Protocol):
    def search(self, query: str, top_k: int) -> tuple[VectorCandidate, ...]: ...


def retrieve_evidence_v2(
    request: RetrievalRequest,
    *,
    release_provider: ReleaseProvider,
    vector_store: CandidateVectorStore,
    signal_policy: SignalPolicy,
    reranker: Callable[[tuple[RetrievedArticleV2, ...]], tuple[int, ...]],
) -> RetrievalResultV2:
    if request.kb_mode == "disabled":
        raise KBRetrievalError(
            "KB_MODE_NOT_SUPPORTED",
            "disabled-mode transport mapping is owned by the API adapter",
        )
    try:
        release = release_provider.pin_active_release(request.namespace, request.as_of)
    except KBRetrievalError:
        raise
    except Exception as error:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            raise KBRetrievalError(code, str(error)) from error
        raise
    if release.signal_policy_version != signal_policy.policy_version:
        raise KBRetrievalError("KB_POLICY_INCOMPATIBLE", "signal policy version mismatch")
    if release.signal_policy_checksum != signal_policy.content_sha256:
        raise KBRetrievalError("KB_POLICY_INCOMPATIBLE", "signal policy checksum mismatch")

    candidates = tuple(vector_store.search(request.query, request.retrieval_top_k))
    scored_rows = []
    for candidate in candidates:
        signals = attribute_matches(
            candidate.matches,
            kb_snapshot_id=release.snapshot_id,
            freshness_policy_version=release.freshness_policy_version,
            signal_policy_version=release.signal_policy_version,
        )
        signal_score = score_signals(
            signals,
            signal_policy,
            intent=request.retrieval_intent,
        )
        weighted_semantic = round(candidate.semantic_score * signal_policy.semantic_weight, 12)
        combined = round(weighted_semantic + signal_score.kb_total_after_cap, 12)
        scored_rows.append((candidate, signal_score, weighted_semantic, combined))

    preordered = sorted(scored_rows, key=lambda row: (-row[3], row[0].article_id))
    pre_positions = {row[0].article_id: index for index, row in enumerate(preordered, 1)}
    provisional = tuple(
        RetrievedArticleV2(
            article_id=candidate.article_id,
            title=candidate.title,
            raw_vector_distance=candidate.raw_vector_distance,
            semantic_score=candidate.semantic_score,
            semantic_weight=signal_policy.semantic_weight,
            weighted_semantic_score=weighted_semantic,
            scored_signals=signal_score.signals,
            kb_total_before_cap=signal_score.kb_total_before_cap,
            kb_total_cap=signal_score.kb_total_cap,
            kb_total_after_cap=signal_score.kb_total_after_cap,
            combined_pre_rerank_score=combined,
            pre_rerank_position=pre_positions[candidate.article_id],
            rerank_position=0,
            kb_snapshot_id=release.snapshot_id,
            signal_policy_version=release.signal_policy_version,
            retrieval_intent=request.retrieval_intent,
            story_group_id=candidate.story_group_id,
            url=candidate.url,
            summary=candidate.summary,
            snippet=candidate.snippet,
            published_at=candidate.published_at,
        )
        for candidate, signal_score, weighted_semantic, combined in preordered
    )
    reranked_ids = tuple(reranker(provisional))
    candidate_ids = {article.article_id for article in provisional}
    if len(reranked_ids) != len(candidate_ids) or set(reranked_ids) != candidate_ids:
        raise KBRetrievalError(
            "RERANKER_FAILED",
            "reranker must return every supplied candidate ID exactly once",
        )
    by_id = {article.article_id: article for article in provisional}
    reranked = tuple(
        _with_rerank_position(by_id[article_id], index)
        for index, article_id in enumerate(reranked_ids, 1)
    )
    return RetrievalResultV2(
        release=release,
        articles=reranked[: request.top_k],
        requested_mode=request.kb_mode,
        applied_mode="applied",
    )


def _with_rerank_position(article: RetrievedArticleV2, position: int) -> RetrievedArticleV2:
    return RetrievedArticleV2(
        article_id=article.article_id,
        title=article.title,
        raw_vector_distance=article.raw_vector_distance,
        semantic_score=article.semantic_score,
        semantic_weight=article.semantic_weight,
        weighted_semantic_score=article.weighted_semantic_score,
        scored_signals=article.scored_signals,
        kb_total_before_cap=article.kb_total_before_cap,
        kb_total_cap=article.kb_total_cap,
        kb_total_after_cap=article.kb_total_after_cap,
        combined_pre_rerank_score=article.combined_pre_rerank_score,
        pre_rerank_position=article.pre_rerank_position,
        rerank_position=position,
        kb_snapshot_id=article.kb_snapshot_id,
        signal_policy_version=article.signal_policy_version,
        retrieval_intent=article.retrieval_intent,
        story_group_id=article.story_group_id,
        url=article.url,
        summary=article.summary,
        snippet=article.snippet,
        published_at=article.published_at,
    )


__all__ = [
    "KBRetrievalError",
    "RetrievalRequest",
    "RetrievalResultV2",
    "RetrievedArticleV2",
    "VectorCandidate",
    "retrieve_evidence_v2",
]
