"""Retrieval-layer A/B evaluation for ticker identity knowledge-base experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
import json
import os
from typing import Any

import yaml

from event_collector.retrieval_orchestration import (
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_SNIPPET_CHARS,
    apply_company_signals,
    build_article_evidence,
    build_rerank_candidates,
    filter_low_quality_search_results,
    rescore_search_results,
    rerank_candidates,
)
from event_collector.entity_kb import (
    CompanyContext,
    SQLiteEntityStore,
    build_direct_expanded_query,
    build_indirect_expanded_query,
    load_tickers_from_annotation_dir,
)
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT, RetrievalIntent, normalize_retrieval_intent
from event_collector.reranking import RAGRerankingAgent, RerankMetadata
from event_collector.ticker_kb import TickerIdentity
from event_collector.vector_store import VectorStore
from event_collector.watchlist_triage import normalize_ticker
from event_collector.news_storage import SQLiteNewsStore


DEFAULT_ANNOTATION_TARGET = 12
DEFAULT_REPORTS_DIR = os.path.join("reports", "retrieval_eval")
IMPACT_LABELS = ("positive", "negative", "neutral", "unclear")
RELEVANCE_TYPES = ("direct", "indirect")


@dataclass(frozen=True)
class RetrievedEvalArticle:
    article_id: int
    title: str
    url: str
    excerpt: str
    source_chain: str


@dataclass(frozen=True)
class RetrievalChainResult:
    chain_name: str
    query: str
    search_results: list[dict[str, Any]]
    filtered_results: list[dict[str, Any]]
    reranked_results: list[dict[str, Any]]
    rerank_metadata: RerankMetadata | None


@dataclass(frozen=True)
class RetrievalComparison:
    ticker: str
    ticker_only: RetrievalChainResult
    has_company_profile: bool = False
    entity_kb_enhanced: RetrievalChainResult | None = None
    indirect_entity_kb_enhanced: RetrievalChainResult | None = None
    deepseek_entity_kb_enhanced: RetrievalChainResult | None = None
    deepseek_indirect_entity_kb_enhanced: RetrievalChainResult | None = None


@dataclass(frozen=True)
class AnnotationRecord:
    article_id: int
    title: str
    url: str
    excerpt: str
    content_path: str
    dedupe_key: str
    label: str | None = None
    relevance_type: str | None = None
    notes: str = ""
    related_tickers: tuple[str, ...] = field(default_factory=tuple)
    suggested_tickers: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnnotationFile:
    ticker: str
    annotations: list[AnnotationRecord]


@dataclass(frozen=True)
class MasterAnnotationRecord:
    article_id: int
    title: str
    url: str
    excerpt: str
    content_path: str
    dedupe_key: str
    notes: str
    ticker_impacts: dict[str, tuple[str, ...]]
    ticker_relevance_types: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class MasterAnnotationFile:
    articles: list[MasterAnnotationRecord]


@dataclass(frozen=True)
class TickerImpactAnnotation:
    article_id: int
    title: str
    url: str
    excerpt: str
    content_path: str
    dedupe_key: str
    relevance: str
    impact_label: str | None
    relevance_type: str | None
    notes: str


@dataclass(frozen=True)
class TickerImpactFile:
    ticker: str
    annotations: list[TickerImpactAnnotation]


@dataclass(frozen=True)
class ChainMetrics:
    precision_at_k: float
    recall_at_k: float
    hit_at_k: bool
    false_positive_count: int
    false_positive_rate: float
    retrieved_top_k_count: int
    returned_count: int
    relevant_count: int
    retrieved_but_unlabeled_count: int


@dataclass(frozen=True)
class EvaluationTickerResult:
    ticker: str
    has_company_profile: bool
    total_labeled: int
    total_relevant: int
    evaluation_k: int
    ticker_only: ChainMetrics
    ticker_only_articles: list[int]
    entity_kb_enhanced: ChainMetrics | None = None
    indirect_entity_kb_enhanced: ChainMetrics | None = None
    deepseek_entity_kb_enhanced: ChainMetrics | None = None
    deepseek_indirect_entity_kb_enhanced: ChainMetrics | None = None
    entity_kb_enhanced_articles: list[int] = field(default_factory=list)
    indirect_entity_kb_enhanced_articles: list[int] = field(default_factory=list)
    deepseek_entity_kb_enhanced_articles: list[int] = field(default_factory=list)
    deepseek_indirect_entity_kb_enhanced_articles: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationResult:
    evaluation_top_k_mode: str
    relevance_type_filter: str
    per_ticker: list[EvaluationTickerResult]
    aggregate: dict[str, dict[str, float | int]]
    grouped_aggregate: dict[str, dict[str, dict[str, float | int]]]
    raw_results: dict[str, Any]


@dataclass(frozen=True)
class FreshAnnotationEligibility:
    allowed_article_ids: set[int]
    latest_fetched_at_iso: str | None


def run_retrieval_comparison(
    ticker: str,
    vector_store: VectorStore,
    *,
    company_kb: SQLiteEntityStore | None = None,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    reranking_agent: RAGRerankingAgent | None = None,
    deepseek_reranking_agent: RAGRerankingAgent | None = None,
    entity_chain_name: str = "entity_kb_enhanced",
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    allowed_article_ids: set[int] | None = None,
    retrieval_intent: str = "both",
) -> RetrievalComparison:
    """Run ticker-only and entity-KB-enhanced retrieval for one ticker."""
    retrieval_intent = "both" if retrieval_intent == "both" else normalize_retrieval_intent(retrieval_intent)
    company = company_kb.load_company_by_ticker(ticker) if company_kb is not None else None
    ticker_only = _run_chain(
        chain_name="ticker_only",
        query=normalize_ticker(ticker),
        vector_store=vector_store,
        retrieval_top_k=retrieval_top_k,
        reranking_agent=reranking_agent,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
        company=None,
        company_kb=None,
        allowed_article_ids=allowed_article_ids,
        retrieval_intent=DEFAULT_RETRIEVAL_INTENT,
    )
    entity_kb_enhanced = None
    indirect_entity_kb_enhanced = None
    if retrieval_intent in {"both", "direct"}:
        entity_kb_enhanced = _run_chain(
            chain_name=entity_chain_name,
            query=build_direct_expanded_query(company) if company is not None else normalize_ticker(ticker),
            vector_store=vector_store,
            retrieval_top_k=retrieval_top_k,
            reranking_agent=reranking_agent,
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
            company=company,
            company_kb=company_kb,
            allowed_article_ids=allowed_article_ids,
            retrieval_intent="direct",
        )
    if retrieval_intent in {"both", "indirect"}:
        indirect_entity_kb_enhanced = _run_chain(
            chain_name=_indirect_chain_name(entity_chain_name),
            query=build_indirect_expanded_query(company) if company is not None else normalize_ticker(ticker),
            vector_store=vector_store,
            retrieval_top_k=retrieval_top_k,
            reranking_agent=reranking_agent,
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
            company=company,
            company_kb=company_kb,
            allowed_article_ids=allowed_article_ids,
            retrieval_intent="indirect",
        )
    deepseek_entity_kb_enhanced = None
    deepseek_indirect_entity_kb_enhanced = None
    if deepseek_reranking_agent is not None:
        if retrieval_intent in {"both", "direct"}:
            deepseek_entity_kb_enhanced = _run_chain(
                chain_name="deepseek_entity_kb_enhanced",
                query=build_direct_expanded_query(company) if company is not None else normalize_ticker(ticker),
                vector_store=vector_store,
                retrieval_top_k=retrieval_top_k,
                reranking_agent=deepseek_reranking_agent,
                excerpt_chars=excerpt_chars,
                snippet_chars=snippet_chars,
                company=company,
                company_kb=company_kb,
                allowed_article_ids=allowed_article_ids,
                retrieval_intent="direct",
            )
        if retrieval_intent in {"both", "indirect"}:
            deepseek_indirect_entity_kb_enhanced = _run_chain(
                chain_name="deepseek_indirect_entity_kb_enhanced",
                query=build_indirect_expanded_query(company) if company is not None else normalize_ticker(ticker),
                vector_store=vector_store,
                retrieval_top_k=retrieval_top_k,
                reranking_agent=deepseek_reranking_agent,
                excerpt_chars=excerpt_chars,
                snippet_chars=snippet_chars,
                company=company,
                company_kb=company_kb,
                allowed_article_ids=allowed_article_ids,
                retrieval_intent="indirect",
            )
    return RetrievalComparison(
        ticker=normalize_ticker(ticker),
        ticker_only=ticker_only,
        has_company_profile=company is not None,
        entity_kb_enhanced=entity_kb_enhanced if entity_chain_name == "entity_kb_enhanced" else None,
        indirect_entity_kb_enhanced=(
            indirect_entity_kb_enhanced if entity_chain_name == "entity_kb_enhanced" else None
        ),
        deepseek_entity_kb_enhanced=(
            entity_kb_enhanced if entity_chain_name == "deepseek_entity_kb_enhanced" else deepseek_entity_kb_enhanced
        ),
        deepseek_indirect_entity_kb_enhanced=(
            indirect_entity_kb_enhanced
            if entity_chain_name == "deepseek_entity_kb_enhanced"
            else deepseek_indirect_entity_kb_enhanced
        ),
    )


def build_annotation_file(
    comparison: RetrievalComparison,
    *,
    storage: SQLiteNewsStore | None = None,
    annotation_target: int = DEFAULT_ANNOTATION_TARGET,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> AnnotationFile:
    """Build the prefilled annotation skeleton for one ticker comparison."""
    prioritized = _prioritize_candidate_articles(
        comparison.ticker_only.search_results,
        _primary_kb_chain(comparison).filtered_results,
        annotation_target=annotation_target,
        excerpt_chars=excerpt_chars,
        snippet_chars=snippet_chars,
    )
    return AnnotationFile(
        ticker=comparison.ticker,
        annotations=_build_annotation_records(
            prioritized,
            storage=storage,
            snippet_chars=snippet_chars,
        ),
    )


def save_annotation_file(annotation_file: AnnotationFile, output_dir: str | Path, *, force: bool = False) -> Path:
    """Persist one per-ticker annotation YAML file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    target = output_path / f"{annotation_file.ticker}.yaml"

    if target.exists() and not force:
        existing = load_annotation_file(target)
        if any(item.label is not None for item in existing.annotations):
            raise ValueError(f"Annotation file already contains labels: {target}")

    payload = {
        "ticker": annotation_file.ticker,
        "annotations": [
            {
                "article_id": item.article_id,
                "title": item.title,
                "url": item.url,
                "excerpt": item.excerpt,
                "content_path": item.content_path,
                "dedupe_key": item.dedupe_key,
                "label": item.label,
                "relevance_type": item.relevance_type,
                "notes": item.notes,
                "related_tickers": list(item.related_tickers),
                "suggested_tickers": list(item.suggested_tickers),
            }
            for item in annotation_file.annotations
        ],
    }
    target.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return target


def load_annotation_file(path: str | Path) -> AnnotationFile:
    """Load one per-ticker annotation YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Annotation file must contain a top-level mapping: {path}")

    unknown = sorted(set(raw) - {"ticker", "annotations"})
    if unknown:
        raise ValueError(f"Annotation file contains unsupported top-level fields: {unknown}")

    ticker = normalize_ticker(_require_annotation_text(raw.get("ticker"), "ticker"))
    annotations_raw = raw.get("annotations")
    if not isinstance(annotations_raw, list):
        raise ValueError("Annotation file must contain an 'annotations' list")

    annotations: list[AnnotationRecord] = []
    for index, item in enumerate(annotations_raw):
        if not isinstance(item, dict):
            raise ValueError(f"annotations[{index}] must be an object")
        unknown_fields = sorted(
            set(item)
            - {
                "article_id",
                "title",
                "url",
                "excerpt",
                "content_path",
                "dedupe_key",
                "label",
                "relevance",
                "impact_label",
                "relevance_type",
                "notes",
                "related_tickers",
                "suggested_tickers",
            }
        )
        if unknown_fields:
            raise ValueError(f"annotations[{index}] contains unsupported fields: {unknown_fields}")

        article_id = item.get("article_id")
        if not isinstance(article_id, int):
            raise ValueError(f"annotations[{index}].article_id must be an integer")

        label = _normalize_annotation_label(item, index)

        relevance_type = item.get("relevance_type")
        if relevance_type not in {None, *RELEVANCE_TYPES}:
            raise ValueError(
                f"annotations[{index}].relevance_type must be null or one of {sorted(RELEVANCE_TYPES)}"
            )

        notes = item.get("notes", "")
        if notes is None:
            notes = ""
        if not isinstance(notes, str):
            raise ValueError(f"annotations[{index}].notes must be a string")

        related_tickers = _normalize_related_tickers(
            item.get("related_tickers", []),
            field_name=f"annotations[{index}].related_tickers",
        )
        suggested_tickers = _normalize_related_tickers(
            item.get("suggested_tickers", []),
            field_name=f"annotations[{index}].suggested_tickers",
        )

        annotations.append(
            AnnotationRecord(
                article_id=article_id,
                title=_require_annotation_text(item.get("title"), f"annotations[{index}].title"),
                url=_require_annotation_text(item.get("url"), f"annotations[{index}].url"),
                excerpt=_require_annotation_text(item.get("excerpt"), f"annotations[{index}].excerpt"),
                content_path=_require_annotation_text(item.get("content_path"), f"annotations[{index}].content_path"),
                dedupe_key=_require_annotation_text(item.get("dedupe_key"), f"annotations[{index}].dedupe_key"),
                label=label,
                relevance_type=relevance_type,
                notes=notes,
                related_tickers=related_tickers,
                suggested_tickers=suggested_tickers,
            )
        )

    return AnnotationFile(ticker=ticker, annotations=annotations)


def _normalize_annotation_label(item: dict[str, Any], index: int) -> str | None:
    label = item.get("label")
    relevance = item.get("relevance")
    impact_label = item.get("impact_label")

    if label is not None and relevance is not None:
        raise ValueError(f"annotations[{index}] must not define both label and relevance")

    if label is not None:
        if label not in {"relevant", "not_relevant"}:
            raise ValueError(f"annotations[{index}].label must be null, 'relevant', or 'not_relevant'")
        return label

    if relevance is not None:
        if relevance not in {"relevant", "irrelevant"}:
            raise ValueError(f"annotations[{index}].relevance must be 'relevant' or 'irrelevant'")
        if impact_label not in {None, "positive", "negative", "neutral", "unclear"}:
            raise ValueError(
                f"annotations[{index}].impact_label must be null or one of {sorted(IMPACT_LABELS)}"
            )
        return "relevant" if relevance == "relevant" else "not_relevant"

    return None


def load_master_annotation_file(path: str | Path) -> MasterAnnotationFile:
    """Load the master retrieval-eval YAML keyed by article."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Master annotation file must contain a top-level mapping: {path}")

    unknown = sorted(set(raw) - {"articles"})
    if unknown:
        raise ValueError(f"Master annotation file contains unsupported top-level fields: {unknown}")

    articles_raw = raw.get("articles")
    if not isinstance(articles_raw, list):
        raise ValueError("Master annotation file must contain an 'articles' list")

    articles: list[MasterAnnotationRecord] = []
    for index, item in enumerate(articles_raw):
        if not isinstance(item, dict):
            raise ValueError(f"articles[{index}] must be an object")
        unknown_fields = sorted(set(item) - {"article_id", "title", "url", "excerpt", "content_path", "dedupe_key", "notes", "ticker_impacts"})
        unknown_fields = sorted(
            set(item)
            - {
                "article_id",
                "title",
                "url",
                "excerpt",
                "content_path",
                "dedupe_key",
                "notes",
                "ticker_impacts",
                "ticker_relevance_types",
            }
        )
        if unknown_fields:
            raise ValueError(f"articles[{index}] contains unsupported fields: {unknown_fields}")

        article_id = item.get("article_id")
        if not isinstance(article_id, int):
            raise ValueError(f"articles[{index}].article_id must be an integer")

        notes = item.get("notes", "")
        if notes is None:
            notes = ""
        if not isinstance(notes, str):
            raise ValueError(f"articles[{index}].notes must be a string")

        ticker_impacts = _normalize_ticker_impacts(
            item.get("ticker_impacts", {}),
            field_name=f"articles[{index}].ticker_impacts",
        )
        ticker_relevance_types = _normalize_ticker_relevance_types(
            item.get("ticker_relevance_types", {}),
            field_name=f"articles[{index}].ticker_relevance_types",
            ticker_impacts=ticker_impacts,
        )
        articles.append(
            MasterAnnotationRecord(
                article_id=article_id,
                title=_require_annotation_text(item.get("title"), f"articles[{index}].title"),
                url=_require_annotation_text(item.get("url"), f"articles[{index}].url"),
                excerpt=_require_annotation_text(item.get("excerpt"), f"articles[{index}].excerpt"),
                content_path=_require_annotation_text(item.get("content_path"), f"articles[{index}].content_path"),
                dedupe_key=_require_annotation_text(item.get("dedupe_key"), f"articles[{index}].dedupe_key"),
                notes=notes,
                ticker_impacts=ticker_impacts,
                ticker_relevance_types=ticker_relevance_types,
            )
        )
    return MasterAnnotationFile(articles=articles)


def split_master_annotations_by_ticker(
    master_path: str | Path,
    output_dir: str | Path,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Split a master per-article annotation file into one YAML per ticker."""
    master_file = load_master_annotation_file(master_path)
    all_tickers: set[str] = set()
    per_article_impacts: list[tuple[MasterAnnotationRecord, dict[str, str]]] = []

    for article in master_file.articles:
        seen_tickers: dict[str, str] = {}
        for impact_label in IMPACT_LABELS:
            for ticker in article.ticker_impacts.get(impact_label, ()):
                existing = seen_tickers.get(ticker)
                if existing is not None and existing != impact_label:
                    raise ValueError(
                        f"Article {article.article_id} assigns ticker {ticker} to multiple impact labels: "
                        f"{existing}, {impact_label}"
                    )
                seen_tickers[ticker] = impact_label
                all_tickers.add(ticker)
        per_article_impacts.append((article, seen_tickers))

    grouped: dict[str, list[TickerImpactAnnotation]] = {ticker: [] for ticker in sorted(all_tickers)}
    for ticker in sorted(all_tickers):
        for article, seen_tickers in per_article_impacts:
            impact_label = seen_tickers.get(ticker)
            relevance_type = _resolve_relevance_type(article.ticker_relevance_types, ticker)
            grouped[ticker].append(
                TickerImpactAnnotation(
                    article_id=article.article_id,
                    title=article.title,
                    url=article.url,
                    excerpt=article.excerpt,
                    content_path=article.content_path,
                    dedupe_key=article.dedupe_key,
                    relevance="relevant" if impact_label is not None else "irrelevant",
                    impact_label=impact_label,
                    relevance_type=relevance_type if impact_label is not None else None,
                    notes=article.notes,
                )
            )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for ticker, annotations in sorted(grouped.items()):
        target = output_path / f"{ticker}.yaml"
        if target.exists() and not force:
            raise ValueError(f"Refusing to overwrite existing ticker file without force: {target}")
        payload = {
            "ticker": ticker,
            "annotations": [
                {
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "excerpt": item.excerpt,
                    "content_path": item.content_path,
                    "dedupe_key": item.dedupe_key,
                    "relevance": item.relevance,
                    "impact_label": item.impact_label,
                    "relevance_type": item.relevance_type,
                    "notes": item.notes,
                }
                for item in sorted(annotations, key=lambda entry: (entry.article_id, entry.title))
            ],
        }
        target.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
        counts[ticker] = len(annotations)
    return counts


def evaluate_retrieval_comparisons(
    comparisons: list[RetrievalComparison],
    annotation_dir: str | Path,
    *,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    top_k_mode: str = "fixed",
    relevance_type_filter: str = "all",
) -> EvaluationResult:
    """Evaluate ticker-only and entity-KB-enhanced retrieval results against completed labels."""
    if relevance_type_filter not in {"all", *RELEVANCE_TYPES}:
        raise ValueError(
            f"relevance_type_filter must be 'all' or one of {sorted(RELEVANCE_TYPES)}"
        )
    annotation_path = Path(annotation_dir)
    results: list[EvaluationTickerResult] = []
    raw_results: dict[str, Any] = {}

    for comparison in comparisons:
        annotation_file = load_annotation_file(annotation_path / f"{comparison.ticker}.yaml")
        annotation_file = _filter_annotation_file_by_relevance_type(
            annotation_file,
            relevance_type_filter=relevance_type_filter,
        )
        _validate_dedupe_groups(annotation_file)
        label_lookup = {item.article_id: item for item in annotation_file.annotations}
        unlabeled = [item.article_id for item in annotation_file.annotations if item.label is None]
        if unlabeled:
            raise ValueError(f"Annotation file contains unlabeled entries for {comparison.ticker}: {unlabeled}")

        total_relevant = len(
            {
                item.dedupe_key
                for item in annotation_file.annotations
                if item.label == "relevant"
            }
        )
        evaluation_k = top_k if top_k_mode == "fixed" else max(total_relevant, 1)
        ticker_only_articles = _extract_article_ids(comparison.ticker_only.reranked_results[:evaluation_k])
        ticker_only_metrics = _compute_metrics(ticker_only_articles, label_lookup, total_relevant)
        entity_kb_metrics = None
        entity_kb_articles: list[int] = []
        if comparison.entity_kb_enhanced is not None:
            entity_kb_articles = _extract_article_ids(comparison.entity_kb_enhanced.reranked_results[:evaluation_k])
            entity_kb_metrics = _compute_metrics(entity_kb_articles, label_lookup, total_relevant)

        indirect_entity_kb_metrics = None
        indirect_entity_kb_articles: list[int] = []
        if comparison.indirect_entity_kb_enhanced is not None:
            indirect_entity_kb_articles = _extract_article_ids(
                comparison.indirect_entity_kb_enhanced.reranked_results[:evaluation_k]
            )
            indirect_entity_kb_metrics = _compute_metrics(
                indirect_entity_kb_articles,
                label_lookup,
                total_relevant,
            )

        deepseek_entity_kb_metrics = None
        deepseek_entity_kb_articles: list[int] = []
        if comparison.deepseek_entity_kb_enhanced is not None:
            deepseek_entity_kb_articles = _extract_article_ids(
                comparison.deepseek_entity_kb_enhanced.reranked_results[:evaluation_k]
            )
            deepseek_entity_kb_metrics = _compute_metrics(deepseek_entity_kb_articles, label_lookup, total_relevant)

        deepseek_indirect_entity_kb_metrics = None
        deepseek_indirect_entity_kb_articles: list[int] = []
        if comparison.deepseek_indirect_entity_kb_enhanced is not None:
            deepseek_indirect_entity_kb_articles = _extract_article_ids(
                comparison.deepseek_indirect_entity_kb_enhanced.reranked_results[:evaluation_k]
            )
            deepseek_indirect_entity_kb_metrics = _compute_metrics(
                deepseek_indirect_entity_kb_articles,
                label_lookup,
                total_relevant,
            )

        result = EvaluationTickerResult(
            ticker=comparison.ticker,
            has_company_profile=comparison.has_company_profile,
            total_labeled=len(annotation_file.annotations),
            total_relevant=total_relevant,
            evaluation_k=evaluation_k,
            ticker_only=ticker_only_metrics,
            entity_kb_enhanced=entity_kb_metrics,
            indirect_entity_kb_enhanced=indirect_entity_kb_metrics,
            deepseek_entity_kb_enhanced=deepseek_entity_kb_metrics,
            deepseek_indirect_entity_kb_enhanced=deepseek_indirect_entity_kb_metrics,
            ticker_only_articles=ticker_only_articles,
            entity_kb_enhanced_articles=entity_kb_articles,
            indirect_entity_kb_enhanced_articles=indirect_entity_kb_articles,
            deepseek_entity_kb_enhanced_articles=deepseek_entity_kb_articles,
            deepseek_indirect_entity_kb_enhanced_articles=deepseek_indirect_entity_kb_articles,
        )
        results.append(result)
        ticker_raw_results = {
            "ticker_only": _serialize_chain_result(comparison.ticker_only),
            "has_company_profile": comparison.has_company_profile,
            "annotations": [asdict(item) for item in annotation_file.annotations],
        }
        for chain_name, chain_result in _iter_comparison_chains(comparison):
            if chain_name == "ticker_only":
                continue
            ticker_raw_results[chain_name] = _serialize_chain_result(chain_result)
        raw_results[comparison.ticker] = ticker_raw_results

    aggregate = _aggregate_metrics(results)
    grouped_aggregate = _group_aggregate_metrics(results)
    return EvaluationResult(
        evaluation_top_k_mode=top_k_mode,
        relevance_type_filter=relevance_type_filter,
        per_ticker=results,
        aggregate=aggregate,
        grouped_aggregate=grouped_aggregate,
        raw_results=raw_results,
    )


def write_evaluation_outputs(
    evaluation: EvaluationResult,
    output_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> tuple[Path, Path]:
    """Write the Markdown report and JSON artifact for one evaluation run."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    markdown_path = output_path / "retrieval_eval_report.md"
    json_path = output_path / "retrieval_eval_results.json"

    markdown_path.write_text(render_evaluation_report(evaluation), encoding="utf-8")
    json_payload = {
        "evaluation_top_k_mode": evaluation.evaluation_top_k_mode,
        "relevance_type_filter": evaluation.relevance_type_filter,
        "per_ticker": [asdict(item) for item in evaluation.per_ticker],
        "aggregate": evaluation.aggregate,
        "grouped_aggregate": evaluation.grouped_aggregate,
        "raw_results": evaluation.raw_results,
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
    return markdown_path, json_path


def render_evaluation_report(evaluation: EvaluationResult) -> str:
    """Render a Markdown A/B comparison report for retrieval evaluation."""
    lines = [
        "# Retrieval Evaluation Report",
        "",
        f"- Evaluation Top-K Mode: `{evaluation.evaluation_top_k_mode}`",
        f"- Relevance Type Filter: `{evaluation.relevance_type_filter}`",
        "",
        "## Reading Guide",
        "",
        "- `Retrieved Top-K`: the number of distinct stories the chain actually returned in the top-k, whether or not they were labeled.",
        "- `Scored Returned`: the number of returned stories that also appear in the golden annotation file and were therefore counted in metrics.",
        "- `Retrieved But Unlabeled`: returned stories that were not present in the current annotation file, so they were retrieved but not scored.",
        "",
        "## Aggregate Metrics",
        "",
        "| Chain | Macro Precision@K | Macro Recall@K | Micro Precision@K | Micro Recall@K | Hit@K | False Positive Rate | Retrieved Top-K | Scored Returned | Relevant Hits | Retrieved But Unlabeled |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for chain_name in evaluation.aggregate:
        chain = evaluation.aggregate[chain_name]
        lines.append(
            f"| {chain_name} | {chain['precision_at_k']:.3f} | {chain['recall_at_k']:.3f} | "
            f"{chain['micro_precision_at_k']:.3f} | {chain['micro_recall_at_k']:.3f} | "
            f"{chain['hit_at_k']:.3f} | {chain['false_positive_rate']:.3f} | "
            f"{int(chain['retrieved_top_k_count'])} | {int(chain['returned_count'])} | "
            f"{int(chain['relevant_count'])} | {int(chain['retrieved_but_unlabeled_count'])} |"
        )

    for group_name, group_metrics in evaluation.grouped_aggregate.items():
        lines.extend(
            [
                "",
                f"## Group: {group_name}",
                "",
                f"- Tickers in group: {int(group_metrics['ticker_count'])}",
                "",
                "| Chain | Macro Precision@K | Macro Recall@K | Micro Precision@K | Micro Recall@K | Hit@K | False Positive Rate | Retrieved Top-K | Scored Returned | Relevant Hits | Retrieved But Unlabeled |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for chain_name, chain in group_metrics["chains"].items():
            lines.append(
                f"| {chain_name} | {chain['precision_at_k']:.3f} | {chain['recall_at_k']:.3f} | "
                f"{chain['micro_precision_at_k']:.3f} | {chain['micro_recall_at_k']:.3f} | "
                f"{chain['hit_at_k']:.3f} | {chain['false_positive_rate']:.3f} | "
                f"{int(chain['retrieved_top_k_count'])} | {int(chain['returned_count'])} | "
                f"{int(chain['relevant_count'])} | {int(chain['retrieved_but_unlabeled_count'])} |"
            )

    for ticker_result in evaluation.per_ticker:
        lines.extend(
            [
                "",
                f"## {ticker_result.ticker}",
                "",
                f"- Asset group: {'company_ticker' if ticker_result.has_company_profile else 'non_company_asset'}",
                f"- Labeled candidates: {ticker_result.total_labeled}",
                f"- Relevant candidates: {ticker_result.total_relevant}",
                f"- Evaluation K: {ticker_result.evaluation_k}",
                "",
                "| Chain | Precision@K | Recall@K | Hit@K | False Positives | False Positive Rate | Retrieved Top-K | Scored Returned | Relevant Hits | Retrieved But Unlabeled |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for chain_name, metrics, articles in _iter_ticker_result_chains(ticker_result):
            lines.append(
                f"| {chain_name} | {metrics.precision_at_k:.3f} | "
                f"{metrics.recall_at_k:.3f} | "
                f"{'yes' if metrics.hit_at_k else 'no'} | "
                f"{metrics.false_positive_count} | "
                f"{metrics.false_positive_rate:.3f} | "
                f"{metrics.retrieved_top_k_count} | {metrics.returned_count} | "
                f"{metrics.relevant_count} | {metrics.retrieved_but_unlabeled_count} |"
            )
        lines.extend(["", "### Top-K Article IDs", ""])
        for chain_name, _, articles in _iter_ticker_result_chains(ticker_result):
            lines.append(f"- {chain_name}: {articles}")
    return "\n".join(lines).strip() + "\n"


def _run_chain(
    *,
    chain_name: str,
    query: str,
    vector_store: VectorStore,
    retrieval_top_k: int,
    reranking_agent: RAGRerankingAgent | None,
    excerpt_chars: int,
    snippet_chars: int,
    company: CompanyContext | None,
    company_kb: SQLiteEntityStore | None,
    allowed_article_ids: set[int] | None,
    retrieval_intent: RetrievalIntent,
) -> RetrievalChainResult:
    search_results = filter_low_quality_search_results(
        vector_store.search(
            query,
            top_k=retrieval_top_k,
            allowed_article_ids=allowed_article_ids,
        )
    )
    filtered_results = apply_company_signals(
        search_results,
        retrieval_intent=retrieval_intent,
        company=company,
        effective_query=query,
        company_kb=company_kb,
        snippet_chars=snippet_chars,
    )
    candidates = build_rerank_candidates(
        filtered_results,
        max_results=retrieval_top_k,
        snippet_chars=snippet_chars,
    )
    if not candidates:
        rerank_metadata = None
        reranked_results: list[dict[str, Any]] = []
    else:
        rerank_metadata = rerank_candidates(
            query,
            candidates,
            reranking_agent=reranking_agent,
            retrieval_intent=retrieval_intent,
        )
        reranked_results = _reorder_filtered_results(filtered_results, rerank_metadata)
    return RetrievalChainResult(
        chain_name=chain_name,
        query=query,
        search_results=search_results,
        filtered_results=filtered_results,
        reranked_results=reranked_results,
        rerank_metadata=rerank_metadata,
    )


def _build_match_snippet(result: dict[str, Any], snippet_chars: int) -> str:
    content = " ".join(str(result.get("content", "")).split()).strip()
    if len(content) <= snippet_chars:
        return content
    return f"{content[:snippet_chars].rstrip()}..."


def _prioritize_candidate_articles(
    baseline_results: list[dict[str, Any]],
    kb_filtered_results: list[dict[str, Any]],
    *,
    annotation_target: int,
    excerpt_chars: int,
    snippet_chars: int,
) -> list[RetrievedEvalArticle]:
    baseline_ids = {_result_key(item) for item in baseline_results}
    kb_ids = {_result_key(item) for item in kb_filtered_results}

    groups: list[list[dict[str, Any]]] = [
        [item for item in baseline_results if _result_key(item) in baseline_ids & kb_ids],
        [item for item in kb_filtered_results if _result_key(item) in kb_ids - baseline_ids],
        [item for item in baseline_results if _result_key(item) in baseline_ids - kb_ids],
    ]

    prioritized: list[RetrievedEvalArticle] = []
    seen: set[int] = set()
    for group in groups:
        evidence = build_article_evidence(
            group,
            max_results=len(group),
            excerpt_chars=excerpt_chars,
            snippet_chars=snippet_chars,
        )
        for item in evidence:
            if item.article_id in seen:
                continue
            seen.add(item.article_id)
            prioritized.append(
                RetrievedEvalArticle(
                    article_id=item.article_id,
                    title=item.title,
                    url=item.url,
                    excerpt=item.excerpt,
                    source_chain="candidate",
                )
            )
            if len(prioritized) >= annotation_target:
                return prioritized
    return prioritized


def _build_annotation_record(
    item: RetrievedEvalArticle,
    *,
    storage: SQLiteNewsStore | None,
    snippet_chars: int,
) -> AnnotationRecord | None:
    if storage is None:
        excerpt = _require_annotation_excerpt(item.excerpt, item.article_id)
        return AnnotationRecord(
            article_id=item.article_id,
            title=item.title,
            url=item.url,
            excerpt=excerpt,
            content_path=f"article-{item.article_id}",
            dedupe_key=_build_dedupe_key(item.title, item.article_id),
        )

    article = storage.get_article(item.article_id)
    if article is None:
        return None

    excerpt = _truncate_preview(article.content, snippet_chars)
    content_path = _resolve_annotation_content_path(storage, article_id=item.article_id, article=article)
    if not excerpt or content_path is None:
        return None
    return AnnotationRecord(
        article_id=item.article_id,
        title=article.title,
        url=article.url,
        excerpt=excerpt,
        content_path=content_path,
        dedupe_key=_build_dedupe_key(article.title, item.article_id),
    )


def rebalance_annotation_directory(
    annotation_dir: str | Path,
    *,
    identities: dict[str, TickerIdentity],
) -> dict[str, int]:
    annotation_path = Path(annotation_dir)
    files = {
        path.stem.upper(): load_annotation_file(path)
        for path in sorted(annotation_path.glob("*.yaml"))
    }
    pooled: dict[int, tuple[AnnotationRecord, set[str], set[str]]] = {}

    for ticker, annotation_file in files.items():
        for record in annotation_file.annotations:
            explicit_related = set(record.related_tickers)
            if record.article_id not in pooled:
                pooled[record.article_id] = (record, {ticker}, explicit_related)
                continue
            existing_record, source_tickers, related = pooled[record.article_id]
            preferred = _merge_annotation_records(existing_record, record)
            pooled[record.article_id] = (
                preferred,
                source_tickers | {ticker},
                related | explicit_related,
            )

    redistributed: dict[str, list[AnnotationRecord]] = {ticker: [] for ticker in files}
    for _, (record, source_tickers, related_tickers) in sorted(pooled.items()):
        target_tickers = source_tickers | {ticker for ticker in related_tickers if ticker in redistributed}
        for target_ticker in sorted(target_tickers):
            per_record_related = tuple(sorted(ticker for ticker in target_tickers if ticker != target_ticker))
            distributed_record = AnnotationRecord(
                article_id=record.article_id,
                title=record.title,
                url=record.url,
                excerpt=record.excerpt,
                content_path=record.content_path,
                dedupe_key=record.dedupe_key,
                label=record.label if target_ticker in source_tickers else None,
                relevance_type=record.relevance_type if target_ticker in source_tickers else None,
                notes=record.notes,
                related_tickers=per_record_related,
                suggested_tickers=record.suggested_tickers,
            )
            redistributed[target_ticker].append(distributed_record)

    for ticker, records in redistributed.items():
        deduped_records: list[AnnotationRecord] = []
        seen_article_ids: set[int] = set()
        for record in sorted(records, key=lambda item: (item.article_id, item.title)):
            if record.article_id in seen_article_ids:
                continue
            seen_article_ids.add(record.article_id)
            deduped_records.append(record)
        save_annotation_file(
            AnnotationFile(ticker=ticker, annotations=deduped_records),
            annotation_path,
            force=True,
        )
        files[ticker] = AnnotationFile(ticker=ticker, annotations=deduped_records)
    return {ticker: len(annotation_file.annotations) for ticker, annotation_file in files.items()}


def suggest_annotation_directory(
    annotation_dir: str | Path,
    *,
    identities: dict[str, TickerIdentity],
    promote_single_match: bool = True,
    replace_existing_related: bool = False,
) -> dict[str, int]:
    annotation_path = Path(annotation_dir)
    counts: dict[str, int] = {}
    for path in sorted(annotation_path.glob("*.yaml")):
        annotation_file = load_annotation_file(path)
        updated_records: list[AnnotationRecord] = []
        promoted = 0
        for record in annotation_file.annotations:
            detected = tuple(
                ticker for ticker in _detect_related_tickers(record, identities)
                if ticker != annotation_file.ticker
            )
            related_tickers = () if replace_existing_related else record.related_tickers
            if promote_single_match and not related_tickers and len(detected) == 1:
                related_tickers = detected
                promoted += 1
            updated_records.append(
                AnnotationRecord(
                    article_id=record.article_id,
                    title=record.title,
                    url=record.url,
                    excerpt=record.excerpt,
                    content_path=record.content_path,
                    dedupe_key=record.dedupe_key,
                    label=record.label,
                    relevance_type=record.relevance_type,
                    notes=record.notes,
                    related_tickers=related_tickers,
                    suggested_tickers=detected,
                )
            )
        save_annotation_file(
            AnnotationFile(ticker=annotation_file.ticker, annotations=updated_records),
            annotation_path,
            force=True,
        )
        counts[annotation_file.ticker] = promoted
    return counts


def _reorder_filtered_results(
    filtered_results: list[dict[str, Any]],
    rerank_metadata: RerankMetadata,
) -> list[dict[str, Any]]:
    indexed_results = {
        str(index): result
        for index, result in enumerate(filtered_results[: len(rerank_metadata.ranked_candidates)], start=1)
    }
    return [indexed_results[item.candidate_id] for item in rerank_metadata.ranked_candidates]


def _filter_annotation_file_by_relevance_type(
    annotation_file: AnnotationFile,
    *,
    relevance_type_filter: str,
) -> AnnotationFile:
    if relevance_type_filter == "all":
        return annotation_file

    filtered_annotations: list[AnnotationRecord] = []
    for item in annotation_file.annotations:
        label = item.label
        if label == "relevant" and item.relevance_type != relevance_type_filter:
            label = "not_relevant"
        filtered_annotations.append(
            AnnotationRecord(
                article_id=item.article_id,
                title=item.title,
                url=item.url,
                excerpt=item.excerpt,
                content_path=item.content_path,
                dedupe_key=item.dedupe_key,
                label=label,
                notes=item.notes,
                related_tickers=item.related_tickers,
                suggested_tickers=item.suggested_tickers,
            )
        )
    return AnnotationFile(
        ticker=annotation_file.ticker,
        annotations=filtered_annotations,
    )


def _compute_metrics(
    article_ids: list[int],
    label_lookup: dict[int, AnnotationRecord],
    total_relevant: int,
) -> ChainMetrics:
    returned_dedupe_keys: set[str] = set()
    relevant_hit_keys: set[str] = set()
    false_positive_keys: set[str] = set()
    retrieved_dedupe_keys: set[str] = set()
    unlabeled_keys: set[str] = set()
    for article_id in article_ids:
        label = label_lookup.get(article_id)
        if label is None:
            unlabeled_keys.add(f"unlabeled:{article_id}")
            continue
        retrieved_dedupe_keys.add(label.dedupe_key)
        if label is None:
            continue
        if label.dedupe_key in returned_dedupe_keys:
            continue
        returned_dedupe_keys.add(label.dedupe_key)
        if label.label == "relevant":
            relevant_hit_keys.add(label.dedupe_key)
        elif label.label == "not_relevant":
            false_positive_keys.add(label.dedupe_key)

    returned = len(returned_dedupe_keys)
    relevant_hits = len(relevant_hit_keys)
    false_positives = len(false_positive_keys)
    precision = relevant_hits / returned if returned else 0.0
    recall = relevant_hits / total_relevant if total_relevant else 0.0
    false_positive_rate = false_positives / returned if returned else 0.0
    return ChainMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        hit_at_k=relevant_hits > 0,
        false_positive_count=false_positives,
        false_positive_rate=false_positive_rate,
        retrieved_top_k_count=len(retrieved_dedupe_keys) + len(unlabeled_keys),
        returned_count=returned,
        relevant_count=relevant_hits,
        retrieved_but_unlabeled_count=len(unlabeled_keys),
    )


def _aggregate_metrics(results: list[EvaluationTickerResult]) -> dict[str, dict[str, float | int]]:
    aggregate: dict[str, dict[str, float | int]] = {}
    chain_names = sorted({chain_name for item in results for chain_name, _, _ in _iter_ticker_result_chains(item)})

    for chain_name in chain_names:
        chain_metrics = [
            getattr(item, chain_name)
            for item in results
            if getattr(item, chain_name) is not None
        ]
        count = len(chain_metrics)
        returned_count = sum(item.returned_count for item in chain_metrics)
        relevant_count = sum(item.relevant_count for item in chain_metrics)
        total_relevant = sum(item.total_relevant for item in results if getattr(item, chain_name) is not None)
        aggregate[chain_name] = {
            "precision_at_k": sum(item.precision_at_k for item in chain_metrics) / count if count else 0.0,
            "recall_at_k": sum(item.recall_at_k for item in chain_metrics) / count if count else 0.0,
            "micro_precision_at_k": relevant_count / returned_count if returned_count else 0.0,
            "micro_recall_at_k": relevant_count / total_relevant if total_relevant else 0.0,
            "hit_at_k": sum(1 for item in chain_metrics if item.hit_at_k) / count if count else 0.0,
            "false_positive_rate": sum(item.false_positive_rate for item in chain_metrics) / count if count else 0.0,
            "retrieved_top_k_count": sum(item.retrieved_top_k_count for item in chain_metrics),
            "returned_count": returned_count,
            "relevant_count": relevant_count,
            "retrieved_but_unlabeled_count": sum(item.retrieved_but_unlabeled_count for item in chain_metrics),
        }
    return aggregate


def _group_aggregate_metrics(results: list[EvaluationTickerResult]) -> dict[str, dict[str, dict[str, float | int]]]:
    groups = {
        "company_tickers": [item for item in results if item.has_company_profile],
        "non_company_assets": [item for item in results if not item.has_company_profile],
        "relevant_count_eq_1": [item for item in results if item.total_relevant == 1],
        "relevant_count_eq_2": [item for item in results if item.total_relevant == 2],
        "relevant_count_ge_3": [item for item in results if item.total_relevant >= 3],
    }
    grouped: dict[str, dict[str, dict[str, float | int]]] = {}
    for group_name, group_results in groups.items():
        if not group_results:
            continue
        grouped[group_name] = {
            "ticker_count": len(group_results),
            "chains": _aggregate_metrics(group_results),
        }
    return grouped


def _primary_kb_chain(comparison: RetrievalComparison) -> RetrievalChainResult:
    return (
        comparison.entity_kb_enhanced
        or comparison.deepseek_entity_kb_enhanced
        or comparison.indirect_entity_kb_enhanced
        or comparison.deepseek_indirect_entity_kb_enhanced
        or comparison.ticker_only
    )


def _iter_ticker_result_chains(
    ticker_result: EvaluationTickerResult,
) -> list[tuple[str, ChainMetrics, list[int]]]:
    chains: list[tuple[str, ChainMetrics, list[int]]] = [
        ("ticker_only", ticker_result.ticker_only, ticker_result.ticker_only_articles)
    ]
    if ticker_result.entity_kb_enhanced is not None:
        chains.append(
            (
                "entity_kb_enhanced",
                ticker_result.entity_kb_enhanced,
                ticker_result.entity_kb_enhanced_articles,
            )
        )
    if ticker_result.indirect_entity_kb_enhanced is not None:
        chains.append(
            (
                "indirect_entity_kb_enhanced",
                ticker_result.indirect_entity_kb_enhanced,
                ticker_result.indirect_entity_kb_enhanced_articles,
            )
        )
    if ticker_result.deepseek_entity_kb_enhanced is not None:
        chains.append(
            (
                "deepseek_entity_kb_enhanced",
                ticker_result.deepseek_entity_kb_enhanced,
                ticker_result.deepseek_entity_kb_enhanced_articles,
            )
        )
    if ticker_result.deepseek_indirect_entity_kb_enhanced is not None:
        chains.append(
            (
                "deepseek_indirect_entity_kb_enhanced",
                ticker_result.deepseek_indirect_entity_kb_enhanced,
                ticker_result.deepseek_indirect_entity_kb_enhanced_articles,
            )
        )
    return chains


def _iter_comparison_chains(
    comparison: RetrievalComparison,
) -> list[tuple[str, RetrievalChainResult]]:
    chains: list[tuple[str, RetrievalChainResult]] = [("ticker_only", comparison.ticker_only)]
    if comparison.entity_kb_enhanced is not None:
        chains.append(("entity_kb_enhanced", comparison.entity_kb_enhanced))
    if comparison.indirect_entity_kb_enhanced is not None:
        chains.append(("indirect_entity_kb_enhanced", comparison.indirect_entity_kb_enhanced))
    if comparison.deepseek_entity_kb_enhanced is not None:
        chains.append(("deepseek_entity_kb_enhanced", comparison.deepseek_entity_kb_enhanced))
    if comparison.deepseek_indirect_entity_kb_enhanced is not None:
        chains.append(("deepseek_indirect_entity_kb_enhanced", comparison.deepseek_indirect_entity_kb_enhanced))
    return chains


def _indirect_chain_name(chain_name: str) -> str:
    if chain_name.startswith("deepseek_"):
        return f"deepseek_indirect_{chain_name[len('deepseek_'):]}"
    return f"indirect_{chain_name}"


def _extract_article_ids(results: list[dict[str, Any]]) -> list[int]:
    article_ids: list[int] = []
    for item in results:
        article_id = _result_article_id(item)
        if article_id is not None:
            article_ids.append(article_id)
    return article_ids


def _serialize_chain_result(result: RetrievalChainResult) -> dict[str, Any]:
    return {
        "chain_name": result.chain_name,
        "query": result.query,
        "search_results": result.search_results,
        "filtered_results": result.filtered_results,
        "reranked_results": result.reranked_results,
        "rerank_metadata": result.rerank_metadata.model_dump() if result.rerank_metadata else None,
    }


def _result_key(result: dict[str, Any]) -> str:
    article_id = result.get("article_id")
    if article_id is not None:
        return str(article_id)
    return str(result.get("id"))


def _result_article_id(result: dict[str, Any]) -> int | None:
    for candidate in (result.get("article_id"), result.get("id")):
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)
    return None


def _normalize_match_text(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in value)


def _require_annotation_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    return cleaned


def _truncate_preview(value: str, limit: int) -> str:
    cleaned = " ".join((value or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}..."


def _require_annotation_excerpt(value: str, article_id: int) -> str:
    cleaned = _require_annotation_text(value, f"annotations[{article_id}].excerpt")
    return cleaned


def _normalize_ticker_impacts(value: object, *, field_name: str) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {label: () for label in IMPACT_LABELS}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object when present")

    unknown = sorted(set(value) - set(IMPACT_LABELS))
    if unknown:
        raise ValueError(f"{field_name} contains unsupported impact labels: {unknown}")

    normalized: dict[str, tuple[str, ...]] = {}
    for impact_label in IMPACT_LABELS:
        normalized[impact_label] = _normalize_related_tickers(
            value.get(impact_label, []),
            field_name=f"{field_name}.{impact_label}",
        )
    return normalized


def _normalize_ticker_relevance_types(
    value: object,
    *,
    field_name: str,
    ticker_impacts: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object when present")

    unknown = sorted(set(value) - set(RELEVANCE_TYPES))
    if unknown:
        raise ValueError(f"{field_name} contains unsupported relevance types: {unknown}")

    normalized: dict[str, tuple[str, ...]] = {}
    impacted_tickers = {ticker for tickers in ticker_impacts.values() for ticker in tickers}
    typed_tickers: set[str] = set()
    for relevance_type in RELEVANCE_TYPES:
        normalized[relevance_type] = _normalize_related_tickers(
            value.get(relevance_type, []),
            field_name=f"{field_name}.{relevance_type}",
        )
        overlap = typed_tickers & set(normalized[relevance_type])
        if overlap:
            raise ValueError(
                f"{field_name}.{relevance_type} duplicates tickers across relevance types: {sorted(overlap)}"
            )
        typed_tickers.update(normalized[relevance_type])

    extra_typed = sorted(typed_tickers - impacted_tickers)
    if extra_typed:
        raise ValueError(
            f"{field_name} assigns relevance types to tickers not present in ticker_impacts: {extra_typed}"
        )
    return normalized


def _resolve_relevance_type(
    ticker_relevance_types: dict[str, tuple[str, ...]],
    ticker: str,
) -> str | None:
    for relevance_type in RELEVANCE_TYPES:
        if ticker in ticker_relevance_types.get(relevance_type, ()):
            return relevance_type
    return None


def _normalize_related_tickers(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list when present")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        ticker = normalize_ticker(_require_annotation_text(item, f"{field_name}[{index}]"))
        if ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return tuple(normalized)


def _build_annotation_records(
    items: list[RetrievedEvalArticle],
    *,
    storage: SQLiteNewsStore | None,
    snippet_chars: int,
) -> list[AnnotationRecord]:
    records: list[AnnotationRecord] = []
    for item in items:
        record = _build_annotation_record(
            item,
            storage=storage,
            snippet_chars=snippet_chars,
        )
        if record is not None:
            records.append(record)
    return records


def _build_dedupe_key(title: str, article_id: int) -> str:
    tokens = [
        token
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in title).split()
        if token
    ]
    slug = "-".join(tokens[:8]).strip("-")
    if not slug:
        return f"article-{article_id}"
    return slug


def _detect_related_tickers(
    record: AnnotationRecord,
    identities: dict[str, TickerIdentity],
) -> tuple[str, ...]:
    haystack_parts = [record.title, record.excerpt]
    if os.path.exists(record.content_path):
        try:
            haystack_parts.append(_load_annotation_content(record.content_path))
        except OSError:
            pass
    haystack = _normalize_match_text(" ".join(part for part in haystack_parts if part))

    matches: list[str] = []
    for ticker, identity in identities.items():
        normalized_names = {_normalize_match_text(name) for name in identity.all_names}
        if any(name and name in haystack for name in normalized_names):
            matches.append(ticker)
    return tuple(matches)


def _merge_annotation_records(left: AnnotationRecord, right: AnnotationRecord) -> AnnotationRecord:
    return AnnotationRecord(
        article_id=left.article_id,
        title=left.title if len(left.title) >= len(right.title) else right.title,
        url=left.url if len(left.url) >= len(right.url) else right.url,
        excerpt=left.excerpt if len(left.excerpt) >= len(right.excerpt) else right.excerpt,
        content_path=left.content_path if os.path.exists(left.content_path) else right.content_path,
        dedupe_key=left.dedupe_key,
        label=left.label or right.label,
        relevance_type=left.relevance_type or right.relevance_type,
        notes=left.notes or right.notes,
        related_tickers=tuple(sorted(set(left.related_tickers) | set(right.related_tickers))),
        suggested_tickers=tuple(sorted(set(left.suggested_tickers) | set(right.suggested_tickers))),
    )


def _resolve_annotation_content_path(
    storage: SQLiteNewsStore | None,
    *,
    article_id: int,
    article: Any,
) -> str | None:
    content_path = getattr(article, "content_path", None)
    if content_path and os.path.exists(content_path):
        return os.path.abspath(content_path)

    content = getattr(article, "content", "")
    if not content or storage is None:
        return None

    # Older rows may still have inline SQLite content but no persisted article file yet.
    final_path = storage.write_article_content(article_id, content)
    if storage.conn is not None:
        storage.conn.execute(
            "UPDATE articles SET content_path = ? WHERE id = ?",
            (final_path, article_id),
        )
        storage.conn.commit()
    return os.path.abspath(final_path)


def _validate_dedupe_groups(annotation_file: AnnotationFile) -> None:
    labels_by_key: dict[str, set[str | None]] = {}
    for item in annotation_file.annotations:
        labels_by_key.setdefault(item.dedupe_key, set()).add(item.label)

    inconsistent = sorted(
        dedupe_key
        for dedupe_key, labels in labels_by_key.items()
        if len({label for label in labels if label is not None}) > 1
    )
    if inconsistent:
        raise ValueError(
            f"Annotation file contains inconsistent labels within dedupe groups for "
            f"{annotation_file.ticker}: {inconsistent}"
        )


def build_fresh_annotation_eligibility(
    storage: SQLiteNewsStore,
    *,
    fresh_window_hours: int,
) -> FreshAnnotationEligibility:
    records = storage.list_article_records(source="news")
    eligible_records = []
    for record in records:
        article = record.article
        if article.content_status != "ready":
            continue
        if article.index_status != "ready":
            continue
        if not article.content_path or not os.path.exists(article.content_path):
            continue
        content = _load_annotation_content(article.content_path)
        if not content or filter_low_quality_search_results([{"content": content}]) == []:
            continue
        eligible_records.append(record)

    if not eligible_records:
        return FreshAnnotationEligibility(allowed_article_ids=set(), latest_fetched_at_iso=None)

    latest_fetched_at = max(record.article.fetched_at for record in eligible_records if record.article.fetched_at is not None)
    cutoff = latest_fetched_at - timedelta(hours=fresh_window_hours)
    allowed_ids = {
        record.id
        for record in eligible_records
        if record.article.fetched_at is not None and record.article.fetched_at >= cutoff
    }
    return FreshAnnotationEligibility(
        allowed_article_ids=allowed_ids,
        latest_fetched_at_iso=latest_fetched_at.isoformat(),
    )


def _load_annotation_content(content_path: str) -> str:
    with open(content_path, "r", encoding="utf-8") as handle:
        return " ".join(handle.read().split()).strip()
