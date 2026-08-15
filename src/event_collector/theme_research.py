"""Theme-first research workflow for persistent Serenity research assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
import re
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from event_collector.article_content import ArticleContentFetcher
from event_collector.news_storage import NewsArticle, SQLiteNewsStore

RESEARCH_ROOT = os.path.join(
    "docs",
    "research",
    "serenity-chokepoint-analysis",
    "themes",
)
DEFAULT_SEARCH_RESULTS_PER_QUERY = 5
DEFAULT_MAX_DISCOVERED_ARTICLES = 8
DEFAULT_LOCAL_RETRIEVAL_TOP_K = 4
DEFAULT_FRESH_LOOKBACK_DAYS = 45

PRIMARY_DOMAINS = {
    "sec.gov",
}
SECONDARY_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "cnbc.com",
    "marketwatch.com",
    "apnews.com",
}
COMMENTARY_DOMAINS = {
    "seekingalpha.com",
    "substack.com",
    "medium.com",
    "x.com",
    "twitter.com",
}
GENERIC_HOST_PARTS = {
    "www",
    "investor",
    "ir",
    "news",
    "media",
    "press",
    "corp",
    "global",
}
STOPWORDS = {
    "A",
    "AN",
    "AND",
    "ARE",
    "AS",
    "AT",
    "BY",
    "FOR",
    "FROM",
    "HAS",
    "IN",
    "INTO",
    "IS",
    "IT",
    "ITS",
    "OF",
    "ON",
    "OR",
    "THE",
    "TO",
    "WITH",
}
SEGMENT_KEYWORDS = {
    "upstream": "upstream inputs",
    "materials": "materials",
    "equipment": "equipment",
    "manufacturing": "manufacturing",
    "foundry": "foundry capacity",
    "packaging": "advanced packaging",
    "power": "power demand",
    "infrastructure": "infrastructure",
    "software": "software stack",
    "customers": "end demand",
    "regulation": "regulation",
    "logistics": "logistics",
}


@dataclass(frozen=True)
class ThemeResearchRequest:
    theme: str
    analysis_goal: str
    seed_query: str | None = None
    tickers: list[str] | None = None
    db_path: str = "news_articles.db"
    persist_dir: str = "./chroma_data"
    collection_name: str = "news_articles"
    research_root: str = RESEARCH_ROOT
    search_results_per_query: int = DEFAULT_SEARCH_RESULTS_PER_QUERY
    max_discovered_articles: int = DEFAULT_MAX_DISCOVERED_ARTICLES
    local_retrieval_top_k: int = DEFAULT_LOCAL_RETRIEVAL_TOP_K
    fresh_lookback_days: int = DEFAULT_FRESH_LOOKBACK_DAYS


@dataclass(frozen=True)
class ThemeMetadata:
    theme: str
    theme_slug: str
    analysis_goal: str
    seed_query: str | None
    tickers: list[str]
    search_intents: list[str]
    generated_at: datetime


@dataclass(frozen=True)
class ThemeEvidence:
    article_id: int
    title: str
    url: str
    summary: str | None
    published_at: datetime | None
    source_kind: str
    acquisition_channel: str
    domain: str
    is_newly_ingested: bool = False


@dataclass(frozen=True)
class SufficiencyStatus:
    structure_sufficient: bool
    fresh_monitoring_sufficient: bool
    counts_by_kind: dict[str, int]
    total_evidence: int
    fresh_evidence: int


@dataclass(frozen=True)
class ThemeResearchResult:
    metadata: ThemeMetadata
    theme_summary: str
    candidate_segments: list[str]
    related_companies: list[str]
    artifact_paths: dict[str, str]
    evidence_provenance: dict[str, Any]
    sufficiency: SufficiencyStatus
    evidence: list[ThemeEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None


class SearchProvider(Protocol):
    def search(self, query: str, *, num_results: int) -> list[SearchResult]:
        """Return discovered URLs for one theme intent."""


class SummaryGenerator(Protocol):
    def summarize(self, article: NewsArticle) -> str:
        """Return a retrieval-friendly summary for one article."""


class SerperSearchProvider:
    """Small Serper-backed search provider for open web discovery."""

    endpoint = "https://google.serper.dev/search"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.getenv("SERPER_API_KEY")
        self.timeout = timeout

    def search(self, query: str, *, num_results: int) -> list[SearchResult]:
        if not self.api_key:
            raise RuntimeError("SERPER_API_KEY is required for theme research discovery")
        response = requests.post(
            self.endpoint,
            headers={
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": max(1, num_results)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results: list[SearchResult] = []
        for item in payload.get("organic", [])[:num_results]:
            results.append(
                SearchResult(
                    title=_clean_text(item.get("title", "")) or "Untitled",
                    url=_clean_text(item.get("link", "")),
                    snippet=_clean_text(item.get("snippet", "")),
                    published_at=_parse_datetime(item.get("date")),
                )
            )
        return [result for result in results if result.url]


class SimpleSummaryGenerator:
    """Deterministic summarizer that keeps the default workflow offline-friendly."""

    def summarize(self, article: NewsArticle) -> str:
        body = " ".join((article.content or "").split()).strip()
        sentences = re.split(r"(?<=[.!?])\s+", body)
        bullets: list[str] = []
        if article.description.strip():
            bullets.append(article.description.strip().rstrip("."))
        for sentence in sentences:
            cleaned = sentence.strip()
            if len(cleaned) < 35:
                continue
            bullets.append(cleaned.rstrip("."))
            if len(bullets) >= 3:
                break
        if not bullets:
            bullets.append(article.title.strip())
        return "\n".join(f"- {bullet}" for bullet in bullets[:3])


def run_theme_research(
    request: ThemeResearchRequest,
    *,
    storage: SQLiteNewsStore | None = None,
    vector_store: Any | None = None,
    search_provider: SearchProvider | None = None,
    content_fetcher: ArticleContentFetcher | None = None,
    summarizer: SummaryGenerator | None = None,
) -> ThemeResearchResult:
    normalized = normalize_theme_research_request(request)
    metadata = ThemeMetadata(
        theme=normalized.theme,
        theme_slug=slugify_theme(normalized.theme),
        analysis_goal=normalized.analysis_goal,
        seed_query=normalized.seed_query,
        tickers=normalized.tickers or [],
        search_intents=build_theme_search_intents(normalized),
        generated_at=datetime.now(),
    )

    owns_storage = storage is None
    runtime_storage = storage or SQLiteNewsStore(db_path=normalized.db_path)
    runtime_storage.init_db()
    runtime_search = search_provider or SerperSearchProvider()
    runtime_fetcher = content_fetcher or ArticleContentFetcher()
    runtime_summarizer = summarizer or SimpleSummaryGenerator()

    try:
        discovered = _acquire_search_evidence(
            metadata,
            normalized,
            runtime_storage,
            vector_store=vector_store,
            search_provider=runtime_search,
            content_fetcher=runtime_fetcher,
            summarizer=runtime_summarizer,
        )
        local_support = _retrieve_local_support(
            metadata,
            normalized,
            runtime_storage,
            vector_store=vector_store,
            exclude_article_ids={item.article_id for item in discovered},
        )
        consolidated = _dedupe_evidence(discovered + local_support)
        sufficiency = compute_sufficiency(
            consolidated,
            fresh_lookback_days=normalized.fresh_lookback_days,
        )
        related_companies = extract_related_companies(consolidated, metadata.tickers)
        candidate_segments = derive_candidate_segments(metadata, consolidated)
        theme_summary = build_theme_summary(
            metadata,
            consolidated,
            sufficiency,
            related_companies,
            candidate_segments,
        )
        artifact_paths = write_theme_research_assets(
            metadata,
            normalized,
            consolidated,
            sufficiency,
            theme_summary,
            related_companies,
            candidate_segments,
        )
        provenance = build_evidence_provenance_summary(consolidated)
        return ThemeResearchResult(
            metadata=metadata,
            theme_summary=theme_summary,
            candidate_segments=candidate_segments,
            related_companies=related_companies,
            artifact_paths=artifact_paths,
            evidence_provenance=provenance,
            sufficiency=sufficiency,
            evidence=consolidated,
        )
    finally:
        if owns_storage:
            runtime_storage.close()


def normalize_theme_research_request(request: ThemeResearchRequest) -> ThemeResearchRequest:
    theme = _clean_text(request.theme)
    analysis_goal = _clean_text(request.analysis_goal)
    if not theme:
        raise ValueError("theme is required")
    if not analysis_goal:
        raise ValueError("analysis_goal is required")
    seed_query = _clean_text(request.seed_query) or None
    tickers = normalize_ticker_hints(request.tickers or [])
    return ThemeResearchRequest(
        theme=theme,
        analysis_goal=analysis_goal,
        seed_query=seed_query,
        tickers=tickers,
        db_path=request.db_path,
        persist_dir=request.persist_dir,
        collection_name=request.collection_name,
        research_root=request.research_root,
        search_results_per_query=max(1, request.search_results_per_query),
        max_discovered_articles=max(1, request.max_discovered_articles),
        local_retrieval_top_k=max(0, request.local_retrieval_top_k),
        fresh_lookback_days=max(1, request.fresh_lookback_days),
    )


def normalize_ticker_hints(tickers: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = re.sub(r"[^A-Za-z0-9.-]", "", (raw or "").strip().upper())
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def slugify_theme(theme: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", theme.lower()).strip("-")
    return slug or "theme"


def build_theme_search_intents(request: ThemeResearchRequest) -> list[str]:
    intents: list[str] = []
    if request.seed_query:
        intents.append(request.seed_query)
    intents.extend(
        [
            f"{request.theme} {request.analysis_goal}",
            f"{request.theme} industry structure primary sources",
            f"{request.theme} investor relations annual report supply chain",
        ]
    )
    for ticker in request.tickers or []:
        intents.append(f"{ticker} {request.theme} {request.analysis_goal}")
    deduped: list[str] = []
    seen: set[str] = set()
    for intent in intents:
        normalized = intent.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(intent)
    return deduped


def classify_source_kind(url: str, *, tickers: list[str] | None = None) -> str:
    domain = _domain_for_url(url)
    if not domain:
        return "secondary"
    root = _root_domain(domain)
    if root in PRIMARY_DOMAINS or root.endswith(".gov"):
        return "primary"
    if root in COMMENTARY_DOMAINS:
        return "commentary"
    if root in SECONDARY_DOMAINS:
        return "secondary"
    host_parts = [part for part in domain.split(".") if part not in {"com", "net", "org", "io", "co", "gov"}]
    if any(part in {"investor", "ir", "press", "newsroom"} for part in host_parts):
        return "company"
    if tickers and any(ticker.lower() in domain for ticker in tickers):
        return "company"
    if len(host_parts) <= 2 and host_parts[0] not in {"reuters", "bloomberg", "marketwatch"}:
        return "company"
    return "secondary"


def compute_sufficiency(
    evidence: list[ThemeEvidence],
    *,
    fresh_lookback_days: int = DEFAULT_FRESH_LOOKBACK_DAYS,
) -> SufficiencyStatus:
    counts = {"primary": 0, "company": 0, "secondary": 0, "commentary": 0}
    fresh_cutoff = datetime.now() - timedelta(days=fresh_lookback_days)
    fresh_evidence = 0
    for item in evidence:
        counts[item.source_kind] = counts.get(item.source_kind, 0) + 1
        if item.published_at and item.published_at >= fresh_cutoff:
            fresh_evidence += 1

    high_weight = counts["primary"] + counts["company"]
    structure_sufficient = high_weight >= 2 or (high_weight >= 1 and counts["secondary"] >= 2)
    fresh_monitoring_sufficient = (
        fresh_evidence >= 3
        or (fresh_evidence >= 2 and high_weight >= 1)
        or (counts["secondary"] + counts["commentary"] >= 3)
    )
    return SufficiencyStatus(
        structure_sufficient=structure_sufficient,
        fresh_monitoring_sufficient=fresh_monitoring_sufficient,
        counts_by_kind=counts,
        total_evidence=len(evidence),
        fresh_evidence=fresh_evidence,
    )


def extract_related_companies(evidence: list[ThemeEvidence], ticker_hints: list[str]) -> list[str]:
    related: list[str] = []
    seen: set[str] = set()
    for ticker in ticker_hints:
        if ticker not in seen:
            seen.add(ticker)
            related.append(ticker)

    for item in evidence:
        for token in re.findall(r"\b[A-Z]{2,5}\b", item.title):
            if token in STOPWORDS or token in seen:
                continue
            seen.add(token)
            related.append(token)
        inferred = infer_company_name_from_url(item.url)
        if inferred and inferred not in seen:
            seen.add(inferred)
            related.append(inferred)
    return related[:12]


def infer_company_name_from_url(url: str) -> str | None:
    domain = _domain_for_url(url)
    if not domain:
        return None
    parts = [
        part
        for part in domain.split(".")
        if part not in {"com", "net", "org", "io", "co", "gov", "edu"} and part not in GENERIC_HOST_PARTS
    ]
    if not parts:
        return None
    root = parts[-2] if len(parts) >= 2 else parts[0]
    if len(root) <= 2:
        return None
    return root.replace("-", " ").title()


def derive_candidate_segments(metadata: ThemeMetadata, evidence: list[ThemeEvidence]) -> list[str]:
    haystack = " ".join(
        [
            metadata.theme,
            metadata.analysis_goal,
            metadata.seed_query or "",
            *[item.title for item in evidence],
            *[item.summary or "" for item in evidence],
        ]
    ).lower()
    matches = [label for keyword, label in SEGMENT_KEYWORDS.items() if keyword in haystack]
    if not matches:
        matches = [metadata.theme]
    deduped: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        deduped.append(match)
    return deduped[:8]


def build_theme_summary(
    metadata: ThemeMetadata,
    evidence: list[ThemeEvidence],
    sufficiency: SufficiencyStatus,
    related_companies: list[str],
    candidate_segments: list[str],
) -> str:
    weights = sufficiency.counts_by_kind
    companies_text = ", ".join(related_companies[:6]) if related_companies else "none yet"
    segments_text = ", ".join(candidate_segments[:6]) if candidate_segments else metadata.theme
    return (
        f"{metadata.theme} research for {metadata.analysis_goal}. "
        f"Reviewed {len(evidence)} evidence items across "
        f"{weights['primary']} primary, {weights['company']} company, "
        f"{weights['secondary']} secondary, and {weights['commentary']} commentary sources. "
        f"Candidate segments include {segments_text}. "
        f"Related companies: {companies_text}. "
        f"Structure sufficient: {'yes' if sufficiency.structure_sufficient else 'no'}. "
        f"Fresh monitoring sufficient: {'yes' if sufficiency.fresh_monitoring_sufficient else 'no'}."
    )


def build_evidence_provenance_summary(evidence: list[ThemeEvidence]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    newly_ingested = 0
    for item in evidence:
        by_kind[item.source_kind] = by_kind.get(item.source_kind, 0) + 1
        by_channel[item.acquisition_channel] = by_channel.get(item.acquisition_channel, 0) + 1
        if item.is_newly_ingested:
            newly_ingested += 1
    return {
        "counts_by_kind": by_kind,
        "counts_by_channel": by_channel,
        "newly_ingested": newly_ingested,
        "total_evidence": len(evidence),
    }


def write_theme_research_assets(
    metadata: ThemeMetadata,
    request: ThemeResearchRequest,
    evidence: list[ThemeEvidence],
    sufficiency: SufficiencyStatus,
    theme_summary: str,
    related_companies: list[str],
    candidate_segments: list[str],
) -> dict[str, str]:
    theme_dir = os.path.join(request.research_root, metadata.theme_slug)
    os.makedirs(theme_dir, exist_ok=True)
    checked_date = metadata.generated_at.strftime("%Y-%m-%d %H:%M:%S")
    theme_card_path = os.path.join(theme_dir, "theme-card.md")
    supply_chain_map_path = os.path.join(theme_dir, "supply-chain-map.md")
    evidence_log_path = os.path.join(theme_dir, "evidence-log.md")
    monitoring_triggers_path = os.path.join(theme_dir, "monitoring-triggers.md")

    theme_card = "\n".join(
        [
            f"# {metadata.theme}",
            "",
            "## Current State",
            f"- Checked at: {checked_date}",
            f"- Analysis goal: {metadata.analysis_goal}",
            f"- Theme summary: {theme_summary}",
            f"- Structure sufficient: {sufficiency.structure_sufficient}",
            f"- Fresh monitoring sufficient: {sufficiency.fresh_monitoring_sufficient}",
            f"- Related companies: {', '.join(related_companies) if related_companies else 'None'}",
            f"- Candidate segments: {', '.join(candidate_segments) if candidate_segments else metadata.theme}",
        ]
    )
    supply_chain_map = "\n".join(
        [
            f"# {metadata.theme} Supply Chain Map",
            "",
            "## Current State",
            f"- Checked at: {checked_date}",
            f"- Candidate segments: {', '.join(candidate_segments) if candidate_segments else metadata.theme}",
            f"- Primary/company evidence count: {sufficiency.counts_by_kind['primary'] + sufficiency.counts_by_kind['company']}",
            f"- Secondary/commentary evidence count: {sufficiency.counts_by_kind['secondary'] + sufficiency.counts_by_kind['commentary']}",
            "## Current Signals",
            *[
                f"- {item.title} [{item.source_kind}]"
                for item in evidence[:8]
            ],
        ]
    )
    evidence_log_entry = "\n".join(
        [
            f"## Run {checked_date}",
            f"- Theme: {metadata.theme}",
            f"- Analysis goal: {metadata.analysis_goal}",
            f"- Search intents: {', '.join(metadata.search_intents)}",
            *[
                f"- [{item.source_kind}/{item.acquisition_channel}] {item.title} ({item.url})"
                for item in evidence[:12]
            ],
        ]
    )
    monitoring_entry = "\n".join(
        [
            f"## Run {checked_date}",
            f"- Fresh monitoring sufficient: {sufficiency.fresh_monitoring_sufficient}",
            f"- Fresh evidence items: {sufficiency.fresh_evidence}",
            f"- Watch related companies: {', '.join(related_companies[:6]) if related_companies else 'None'}",
            f"- Focus segments: {', '.join(candidate_segments[:6]) if candidate_segments else metadata.theme}",
        ]
    )

    _write_text(theme_card_path, theme_card)
    _write_text(supply_chain_map_path, supply_chain_map)
    _append_text(evidence_log_path, "# Evidence Log\n\n", evidence_log_entry)
    _append_text(monitoring_triggers_path, "# Monitoring Triggers\n\n", monitoring_entry)
    return {
        "theme_card": theme_card_path,
        "supply_chain_map": supply_chain_map_path,
        "evidence_log": evidence_log_path,
        "monitoring_triggers": monitoring_triggers_path,
    }


def _acquire_search_evidence(
    metadata: ThemeMetadata,
    request: ThemeResearchRequest,
    storage: SQLiteNewsStore,
    *,
    vector_store: Any | None,
    search_provider: SearchProvider,
    content_fetcher: ArticleContentFetcher,
    summarizer: SummaryGenerator,
) -> list[ThemeEvidence]:
    collected: list[ThemeEvidence] = []
    seen_article_ids: set[int] = set()
    for intent in metadata.search_intents:
        if len(collected) >= request.max_discovered_articles:
            break
        results = search_provider.search(intent, num_results=request.search_results_per_query)
        for result in results:
            if len(collected) >= request.max_discovered_articles:
                break
            article_id, created = storage.create_or_get_article_reference(
                source="theme_search",
                title=result.title,
                description=result.snippet,
                original_url=result.url,
                published_at=result.published_at or datetime.now(),
            )
            record = storage.get_article_record(article_id)
            is_newly_ingested = created
            if record is not None and record.content_status != "ready":
                fetched = content_fetcher.fetch_with_classification(result.url)
                title = result.title or record.article.title
                description = result.snippet or record.article.description
                storage.update_article_content(
                    article_id,
                    content=fetched.content,
                    title=title,
                    description=description,
                    original_url=fetched.original_url,
                    canonical_url=fetched.canonical_url,
                    published_at=result.published_at or record.article.published_at,
                )
                article = storage.get_article(article_id)
                assert article is not None
                summary = summarizer.summarize(article)
                storage.update_article_summary(article_id, summary)
                article.summary = summary
                if vector_store is not None:
                    vector_store.add_article(article_id, article)
                    storage.mark_article_processing_status(article_id, index_status="ready")
            record = storage.get_article_record(article_id)
            if record is None or article_id in seen_article_ids or record.content_status != "ready":
                continue
            seen_article_ids.add(article_id)
            collected.append(
                ThemeEvidence(
                    article_id=article_id,
                    title=record.article.title,
                    url=record.article.url,
                    summary=record.article.summary,
                    published_at=record.article.published_at,
                    source_kind=classify_source_kind(record.article.url, tickers=metadata.tickers),
                    acquisition_channel="open_search",
                    domain=_domain_for_url(record.article.url),
                    is_newly_ingested=is_newly_ingested,
                )
            )
    return collected


def _retrieve_local_support(
    metadata: ThemeMetadata,
    request: ThemeResearchRequest,
    storage: SQLiteNewsStore,
    *,
    vector_store: Any | None,
    exclude_article_ids: set[int],
) -> list[ThemeEvidence]:
    if vector_store is None or request.local_retrieval_top_k <= 0:
        return []
    results: list[ThemeEvidence] = []
    seen_article_ids = set(exclude_article_ids)
    for intent in metadata.search_intents[:3]:
        for raw in vector_store.search(intent, top_k=request.local_retrieval_top_k):
            article_id = int(raw.get("article_id") or raw.get("id"))
            if article_id in seen_article_ids:
                continue
            record = storage.get_article_record(article_id)
            if record is None:
                continue
            seen_article_ids.add(article_id)
            results.append(
                ThemeEvidence(
                    article_id=article_id,
                    title=record.article.title,
                    url=record.article.url,
                    summary=record.article.summary,
                    published_at=record.article.published_at,
                    source_kind=classify_source_kind(record.article.url, tickers=metadata.tickers),
                    acquisition_channel="local_corpus",
                    domain=_domain_for_url(record.article.url),
                    is_newly_ingested=False,
                )
            )
    return results


def _dedupe_evidence(evidence: list[ThemeEvidence]) -> list[ThemeEvidence]:
    deduped: list[ThemeEvidence] = []
    seen: set[int] = set()
    for item in evidence:
        if item.article_id in seen:
            continue
        seen.add(item.article_id)
        deduped.append(item)
    return deduped


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content.rstrip() + "\n")


def _append_text(path: str, header: str, entry: str) -> None:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read().rstrip()
    else:
        existing = header.rstrip()
    with open(path, "w", encoding="utf-8") as handle:
        if existing:
            handle.write(existing + "\n\n")
        handle.write(entry.rstrip() + "\n")


def _domain_for_url(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _root_domain(domain: str) -> str:
    parts = [part for part in domain.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _clean_text(str(value)) if value is not None else ""
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None
