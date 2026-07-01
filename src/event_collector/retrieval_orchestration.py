"""Shared retrieval orchestration for evidence-first workflows."""

from __future__ import annotations

from dataclasses import dataclass
import re

from event_collector.article_content import is_suspected_truncated_preview
from event_collector.entity_kb import (
    AttributionResult,
    CompanyContext,
    SQLiteEntityStore,
    alias_in_text,
    build_direct_expanded_query,
    build_indirect_expanded_query,
    normalize_search_text,
)
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent, normalize_retrieval_intent
from event_collector.reranking import (
    DEFAULT_RERANK_TOP_K,
    RAGRerankingAgent,
    RerankCandidate,
    RerankMetadata,
    rerank_candidates,
)
from event_collector.vector_store import VectorStore


DEFAULT_RETRIEVAL_TOP_K = DEFAULT_RERANK_TOP_K
DEFAULT_EXCERPT_CHARS = 700
DEFAULT_SNIPPET_CHARS = 220
DEFAULT_KB_WEIGHT_COMPANY = 0.20
DEFAULT_KB_WEIGHT_PRODUCT = 0.14
DEFAULT_KB_WEIGHT_CEO = 0.06
DEFAULT_KB_WEIGHT_TICKER = 0.04
DEFAULT_KB_ALIAS_BONUS_PER_MATCH = 0.02
DEFAULT_KB_ALIAS_BONUS_CAP = 0.06
DEFAULT_KB_BOOST_CAP = 0.35
DEFAULT_INDIRECT_KB_WEIGHT_PRODUCT = 0.08
DEFAULT_INDIRECT_KB_WEIGHT_BUSINESS_LINE = 0.06
DEFAULT_INDIRECT_KB_WEIGHT_THEME = 0.05
DEFAULT_INDIRECT_ALIAS_BONUS_PER_MATCH = 0.01
DEFAULT_INDIRECT_ALIAS_BONUS_CAP = 0.04
DEFAULT_INDIRECT_BOOST_CAP = 0.20


@dataclass
class RetrievedArticleEvidence:
    """Unified retrieval evidence used across downstream workflows."""

    id: int
    article_id: int
    title: str
    url: str
    summary: str | None
    excerpt: str
    snippet: str
    published_at: str | None
    rerank_position: int
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT
    company_id: str | None = None
    expanded_query: str | None = None
    normalized_vector_score: float | None = None
    kb_boost: float = 0.0
    final_score: float | None = None
    kb_match_count: int = 0
    matches_business_line: bool = False
    matches_theme: bool = False
    indirect_kb_boost: float = 0.0
    indirect_match_types: tuple[str, ...] = ()
    attribution_match_types: tuple[str, ...] = ()
    attribution_matched_aliases: tuple[str, ...] = ()


@dataclass
class RetrievalEvidenceBundle:
    """All retrieval artifacts needed by downstream decision modules."""

    query: str
    search_results: list[dict]
    reranked_results: list[dict]
    evidence: list[RetrievedArticleEvidence]
    rerank_metadata: RerankMetadata | None
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT
    resolved_company: CompanyContext | None = None


def retrieve_evidence_bundle(
    query: str,
    vector_store: VectorStore,
    *,
    top_k: int,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    reranking_agent: RAGRerankingAgent | None = None,
    company_kb: SQLiteEntityStore | None = None,
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT,
) -> RetrievalEvidenceBundle:
    """Search, rerank, and shape retrieval output into one reusable bundle."""
    retrieval_intent = normalize_retrieval_intent(retrieval_intent)
    resolved_company = company_kb.load_company_by_ticker(query) if company_kb is not None else None
    if resolved_company is None:
        effective_query = query
    elif retrieval_intent == "indirect":
        effective_query = build_indirect_expanded_query(resolved_company)
    else:
        effective_query = build_direct_expanded_query(resolved_company)
    search_limit = max(top_k, retrieval_top_k)
    search_results = filter_low_quality_search_results(vector_store.search(effective_query, top_k=search_limit))
    search_results = apply_company_signals(
        search_results,
        retrieval_intent=retrieval_intent,
        company=resolved_company,
        effective_query=effective_query,
        company_kb=company_kb,
        snippet_chars=snippet_chars,
    )
    candidates = build_rerank_candidates(
        search_results,
        max_results=search_limit,
        snippet_chars=snippet_chars,
    )

    if not candidates:
        return RetrievalEvidenceBundle(
            query=effective_query,
            search_results=search_results,
            reranked_results=[],
            evidence=[],
            rerank_metadata=None,
            retrieval_intent=retrieval_intent,
            resolved_company=resolved_company,
        )

    rerank_metadata = rerank_candidates(
        query,
        candidates,
        reranking_agent=reranking_agent,
        retrieval_intent=retrieval_intent,
    )
    reranked_results = reorder_search_results(search_results, rerank_metadata)
    evidence = build_article_evidence(
        reranked_results,
        max_results=top_k,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
    )
    return RetrievalEvidenceBundle(
        query=effective_query,
        search_results=search_results,
        reranked_results=reranked_results,
        evidence=evidence,
        rerank_metadata=rerank_metadata,
        retrieval_intent=retrieval_intent,
        resolved_company=resolved_company,
    )


def build_article_evidence(
    search_results: list[dict],
    max_results: int,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RetrievedArticleEvidence]:
    """Attach stable article IDs and compact text payloads to retrieval hits."""
    evidence = []
    for position, result in enumerate(search_results[:max_results], start=1):
        title = _clean_text(result.get("title", "Untitled"))
        url = _clean_text(result.get("url", "N/A"))
        summary = result.get("summary")
        cleaned_summary = _clean_text(summary) if summary else None
        content = _clean_text(result.get("content", ""))
        excerpt = _truncate_text(content, excerpt_chars)
        snippet_source = excerpt or title
        snippet = _truncate_text(snippet_source, snippet_chars)
        evidence.append(
            RetrievedArticleEvidence(
                id=position,
                article_id=resolve_article_id(result),
                title=title,
                url=url,
                summary=cleaned_summary,
                excerpt=excerpt,
                snippet=snippet,
                published_at=result.get("published_at"),
                rerank_position=position,
                retrieval_intent=normalize_retrieval_intent(result.get("retrieval_intent")),
                company_id=result.get("company_id"),
                expanded_query=result.get("expanded_query"),
                normalized_vector_score=result.get("normalized_vector_score"),
                kb_boost=float(result.get("kb_boost", 0.0) or 0.0),
                final_score=result.get("final_score"),
                kb_match_count=int(result.get("kb_match_count", 0) or 0),
                matches_business_line=bool(result.get("matches_business_line", False)),
                matches_theme=bool(result.get("matches_theme", False)),
                indirect_kb_boost=float(result.get("indirect_kb_boost", 0.0) or 0.0),
                indirect_match_types=tuple(result.get("indirect_match_types", ()) or ()),
                attribution_match_types=tuple(result.get("attribution_match_types", ()) or ()),
                attribution_matched_aliases=tuple(result.get("attribution_matched_aliases", ()) or ()),
            )
        )
    return evidence


def build_rerank_candidates(
    search_results: list[dict],
    max_results: int = DEFAULT_RETRIEVAL_TOP_K,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RerankCandidate]:
    """Convert raw retrieval output into compact reranking candidates."""
    candidates = []
    for index, result in enumerate(search_results[:max_results], start=1):
        title = _clean_text(result.get("title", "Untitled"))
        content = _clean_text(result.get("content", ""))
        snippet_source = content or title
        snippet = _truncate_text(snippet_source, snippet_chars)
        candidates.append(
            RerankCandidate(
                candidate_id=str(index),
                title=title,
                summary=_build_candidate_summary(result),
                snippet=snippet,
                original_rank=index,
            )
        )
    return candidates


def reorder_search_results(search_results: list[dict], rerank_metadata: RerankMetadata) -> list[dict]:
    """Reorder raw retrieval results according to validated reranker output."""
    indexed_results = {
        str(index): result
        for index, result in enumerate(search_results[: len(rerank_metadata.ranked_candidates)], start=1)
    }
    return [indexed_results[item.candidate_id] for item in rerank_metadata.ranked_candidates]


def resolve_article_id(result: dict) -> int:
    """Resolve the backing SQLite article ID from a vector-store search result."""
    for candidate in (result.get("article_id"), result.get("id"), result.get("url")):
        article_id = _parse_article_id_candidate(candidate)
        if article_id is not None:
            return article_id
    raise ValueError(f"Could not resolve SQLite article id from search result: {result.get('id')!r}")


def build_retrieved_evidence(
    search_results: list[dict],
    max_results: int = 3,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[RetrievedArticleEvidence]:
    """Backward-compatible alias for shared retrieval evidence shaping."""
    return build_article_evidence(
        search_results,
        max_results=max_results,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
    )


def filter_low_quality_search_results(search_results: list[dict]) -> list[dict]:
    filtered: list[dict] = []
    for result in search_results:
        content = _clean_text(result.get("content", ""))
        if content and is_suspected_truncated_preview(content):
            continue
        filtered.append(result)
    return filtered


def apply_company_signals(
    search_results: list[dict],
    *,
    retrieval_intent: RetrievalIntent,
    company: CompanyContext | None,
    effective_query: str,
    company_kb: SQLiteEntityStore | None,
    snippet_chars: int,
) -> list[dict]:
    if not search_results:
        return []

    if company is None or company_kb is None:
        return _attach_base_scores(search_results, retrieval_intent=retrieval_intent)

    if retrieval_intent == "indirect":
        return apply_indirect_company_signals(
            search_results,
            company=company,
            effective_query=effective_query,
            snippet_chars=snippet_chars,
        )

    attributed_results: list[dict] = []
    for result in search_results:
        article_text = " ".join(
            part
            for part in (
                result.get("title", ""),
                result.get("summary", "") or "",
                _build_match_snippet(result, snippet_chars),
            )
            if part
        )
        attributed_results.append(
            enrich_result_with_company_features(
                result,
                company=company,
                effective_query=effective_query,
                attribution=company_kb.attribute_article_to_company(article_text, company),
            )
        )

    return rescore_search_results(attributed_results)


def apply_indirect_company_signals(
    search_results: list[dict],
    *,
    company: CompanyContext,
    effective_query: str,
    snippet_chars: int,
) -> list[dict]:
    indirect_results: list[dict] = []
    for result in search_results:
        article_text = " ".join(
            part
            for part in (
                result.get("title", ""),
                result.get("summary", "") or "",
                _build_match_snippet(result, snippet_chars),
            )
            if part
        )
        indirect_results.append(
            enrich_result_with_indirect_features(
                result,
                company=company,
                effective_query=effective_query,
                article_text=article_text,
            )
        )
    return rescore_search_results(indirect_results)


def enrich_result_with_company_features(
    result: dict,
    *,
    company: CompanyContext,
    effective_query: str,
    attribution: AttributionResult,
) -> dict:
    enriched = dict(result)
    match_types = set(attribution.match_types)
    matched_alias_count = len(attribution.matched_aliases)

    enriched["retrieval_intent"] = "direct"
    enriched["company_id"] = company.company_id if attribution.is_match else None
    enriched["expanded_query"] = effective_query
    enriched["matches_ticker"] = "ticker" in match_types
    enriched["matches_company"] = "company" in match_types
    enriched["matches_product"] = "product" in match_types
    enriched["matches_ceo"] = "ceo" in match_types
    enriched["kb_match_count"] = matched_alias_count
    enriched["matches_business_line"] = False
    enriched["matches_theme"] = False
    enriched["indirect_kb_boost"] = 0.0
    enriched["indirect_match_types"] = []
    enriched["attribution_match_types"] = list(attribution.match_types)
    enriched["attribution_matched_aliases"] = list(attribution.matched_aliases)
    enriched["kb_boost"] = compute_kb_boost(enriched)
    return enriched


def enrich_result_with_indirect_features(
    result: dict,
    *,
    company: CompanyContext,
    effective_query: str,
    article_text: str,
) -> dict:
    enriched = dict(result)
    haystack = normalize_search_text(article_text)
    matched_aliases: list[str] = []
    match_types: list[str] = []
    groups = (
        ("product", company.product_names),
        ("business_line", company.business_lines),
        ("theme", company.themes),
    )
    for match_type, aliases in groups:
        group_hits = [alias for alias in aliases if alias and alias_in_text(alias, haystack)]
        if not group_hits:
            continue
        match_types.append(match_type)
        matched_aliases.extend(group_hits)

    enriched["retrieval_intent"] = "indirect"
    enriched["company_id"] = None
    enriched["expanded_query"] = effective_query
    enriched["matches_ticker"] = False
    enriched["matches_company"] = False
    enriched["matches_product"] = "product" in match_types
    enriched["matches_ceo"] = False
    enriched["matches_business_line"] = "business_line" in match_types
    enriched["matches_theme"] = "theme" in match_types
    enriched["kb_match_count"] = len(matched_aliases)
    enriched["attribution_match_types"] = []
    enriched["attribution_matched_aliases"] = []
    enriched["indirect_match_types"] = match_types
    enriched["indirect_kb_boost"] = compute_indirect_kb_boost(enriched)
    enriched["kb_boost"] = enriched["indirect_kb_boost"]
    return enriched


def rescore_search_results(search_results: list[dict]) -> list[dict]:
    if not search_results:
        return []

    normalized_scores = _normalize_vector_scores(search_results)
    rescored: list[dict] = []
    for index, result in enumerate(search_results):
        enriched = dict(result)
        normalized_vector_score = normalized_scores[index]
        kb_boost = float(enriched.get("kb_boost", 0.0) or 0.0)
        enriched["normalized_vector_score"] = normalized_vector_score
        enriched["final_score"] = normalized_vector_score + kb_boost
        rescored.append(enriched)

    return sorted(
        rescored,
        key=lambda item: (
            float(item.get("final_score", 0.0) or 0.0),
            float(item.get("normalized_vector_score", 0.0) or 0.0),
        ),
        reverse=True,
    )


def compute_kb_boost(result: dict) -> float:
    boost = 0.0
    if result.get("matches_company"):
        boost += DEFAULT_KB_WEIGHT_COMPANY
    if result.get("matches_product"):
        boost += DEFAULT_KB_WEIGHT_PRODUCT
    if result.get("matches_ceo"):
        boost += DEFAULT_KB_WEIGHT_CEO
    if result.get("matches_ticker"):
        boost += DEFAULT_KB_WEIGHT_TICKER

    alias_bonus = min(
        DEFAULT_KB_ALIAS_BONUS_PER_MATCH * int(result.get("kb_match_count", 0) or 0),
        DEFAULT_KB_ALIAS_BONUS_CAP,
    )
    return min(boost + alias_bonus, DEFAULT_KB_BOOST_CAP)


def compute_indirect_kb_boost(result: dict) -> float:
    boost = 0.0
    if result.get("matches_product"):
        boost += DEFAULT_INDIRECT_KB_WEIGHT_PRODUCT
    if result.get("matches_business_line"):
        boost += DEFAULT_INDIRECT_KB_WEIGHT_BUSINESS_LINE
    if result.get("matches_theme"):
        boost += DEFAULT_INDIRECT_KB_WEIGHT_THEME
    alias_bonus = min(
        DEFAULT_INDIRECT_ALIAS_BONUS_PER_MATCH * int(result.get("kb_match_count", 0) or 0),
        DEFAULT_INDIRECT_ALIAS_BONUS_CAP,
    )
    return min(boost + alias_bonus, DEFAULT_INDIRECT_BOOST_CAP)


def _attach_base_scores(
    search_results: list[dict],
    *,
    retrieval_intent: RetrievalIntent = DEFAULT_RETRIEVAL_INTENT,
) -> list[dict]:
    base_results = [dict(result) for result in search_results]
    for result in base_results:
        result.setdefault("retrieval_intent", retrieval_intent)
        result.setdefault("company_id", None)
        result.setdefault("expanded_query", None)
        result.setdefault("kb_boost", 0.0)
        result.setdefault("kb_match_count", 0)
        result.setdefault("matches_ticker", False)
        result.setdefault("matches_company", False)
        result.setdefault("matches_product", False)
        result.setdefault("matches_ceo", False)
        result.setdefault("matches_business_line", False)
        result.setdefault("matches_theme", False)
        result.setdefault("indirect_kb_boost", 0.0)
        result.setdefault("indirect_match_types", [])
        result.setdefault("attribution_match_types", [])
        result.setdefault("attribution_matched_aliases", [])
    return rescore_search_results(base_results)


def _normalize_vector_scores(search_results: list[dict]) -> list[float]:
    distances: list[float] = []
    for result in search_results:
        distance = result.get("distance")
        if isinstance(distance, (int, float)):
            distances.append(float(distance))
        else:
            distances.append(float("nan"))

    if distances and all(not _is_nan(distance) for distance in distances):
        minimum = min(distances)
        maximum = max(distances)
        if maximum == minimum:
            return [1.0] * len(search_results)
        return [1.0 - ((distance - minimum) / (maximum - minimum)) for distance in distances]

    total = len(search_results)
    if total == 1:
        return [1.0]
    return [1.0 - (0.3 * index / (total - 1)) for index in range(total)]


def _build_match_snippet(result: dict, snippet_chars: int) -> str:
    content = _clean_text(result.get("content", ""))
    return _truncate_text(content, snippet_chars)


def _build_candidate_summary(result: dict) -> str | None:
    attribution_bits: list[str] = []
    if result.get("matches_company"):
        attribution_bits.append("company")
    if result.get("matches_product"):
        attribution_bits.append("product")
    if result.get("matches_ceo"):
        attribution_bits.append("ceo")
    if result.get("matches_ticker"):
        attribution_bits.append("ticker")
    if result.get("matches_business_line"):
        attribution_bits.append("business_line")
    if result.get("matches_theme"):
        attribution_bits.append("theme")

    if not attribution_bits:
        return None

    matched_aliases = list(result.get("attribution_matched_aliases", ()) or ())
    if not matched_aliases:
        matched_aliases = list(result.get("indirect_match_types", ()) or ())
    alias_preview = ", ".join(matched_aliases[:3])
    boost = float(result.get("kb_boost", 0.0) or 0.0)
    summary = f"KB signals: {', '.join(attribution_bits)}; boost={boost:.2f}"
    if alias_preview:
        summary = f"{summary}; aliases={alias_preview}"
    return summary


def _is_nan(value: float) -> bool:
    return value != value


def _parse_article_id_candidate(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"(?:^|[_/:-])(\d+)$", text)
    if match:
        return int(match.group(1))
    return None


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    trimmed = value[:limit].rstrip()
    return f"{trimmed}..."
