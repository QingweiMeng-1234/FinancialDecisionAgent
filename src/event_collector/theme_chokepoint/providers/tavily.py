"""Tavily discovery plus independent original-text retrieval for Stage 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import ipaddress
import os
import re
from typing import Protocol
from urllib.parse import urlsplit

import requests
import trafilatura

from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    ClaimDraft,
    EvidenceAcquisitionBatch,
    EvidenceCandidate,
    ExtractedEvidenceSpan,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.source_identity import canonicalize_source_url


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    discovery_summary: str
    score: float
    publication_date: date | None


@dataclass(frozen=True)
class TavilySearchBatch:
    hits: tuple[SearchHit, ...]
    request_receipt_id: str
    credits: float
    cost_usd: float
    cost_is_estimated: bool


@dataclass(frozen=True)
class OriginalDocument:
    article_id: str
    canonical_url: str
    title: str
    publisher: str
    source_type: str
    publication_date: date | None
    text: str
    content_hash: str
    publication_date_verified: bool = False


class EvidenceSpanExtractor(Protocol):
    model_version: str
    prompt_version: str

    def extract(
        self,
        *,
        document: OriginalDocument,
        node: SupplyChainNode,
        material_field: str,
        query: str,
    ) -> list[ExtractedEvidenceSpan]: ...


class TavilySearchProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        session=None,
        timeout_seconds: float = 20.0,
        max_results: int = 5,
        search_depth: str = "advanced",
        estimated_cost_usd_per_credit: float | None = None,
    ):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is required for Theme Chokepoint web search")
        if not 1 <= max_results <= 20:
            raise ValueError("Tavily max_results must be between 1 and 20")
        if timeout_seconds <= 0:
            raise ValueError("Tavily timeout_seconds must be positive")
        if search_depth not in {"basic", "advanced"}:
            raise ValueError("Tavily search_depth must be basic or advanced")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results
        self.search_depth = search_depth
        self.estimated_cost_usd_per_credit = (
            float(
                os.getenv(
                    "TAVILY_ESTIMATED_COST_USD_PER_CREDIT",
                    "0.004",
                )
            )
            if estimated_cost_usd_per_credit is None
            else float(estimated_cost_usd_per_credit)
        )
        if self.estimated_cost_usd_per_credit < 0:
            raise ValueError("Tavily estimated credit cost cannot be negative")

    def search(self, query: str) -> tuple[SearchHit, ...]:
        return self.search_with_receipt(query).hits

    def search_with_receipt(self, query: str) -> TavilySearchBatch:
        if not query.strip():
            raise ValueError("Tavily search query is required")
        try:
            response = self.session.post(
                TAVILY_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query.strip(),
                    "search_depth": self.search_depth,
                    "max_results": self.max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise RuntimeError("Tavily search request failed") from None
        results = payload.get("results", []) if isinstance(payload, dict) else []
        hits = []
        for item in results:
            if not isinstance(item, dict) or not str(item.get("url", "")).strip():
                continue
            hits.append(
                SearchHit(
                    url=str(item["url"]).strip(),
                    title=str(item.get("title", "Untitled source")).strip()
                    or "Untitled source",
                    discovery_summary=str(item.get("content", "")).strip(),
                    score=float(item.get("score", 0.0) or 0.0),
                    publication_date=_parse_date(item.get("published_date")),
                )
            )
        credits, cost_usd, cost_is_estimated = tavily_cost_from_response(
            response.headers,
            payload,
            search_depth=self.search_depth,
            estimated_cost_usd_per_credit=self.estimated_cost_usd_per_credit,
        )
        request_receipt_id = str(
            response.headers.get("X-Request-Id")
            or response.headers.get("X-Provider-Trace-Id")
            or (payload.get("request_id") if isinstance(payload, dict) else None)
            or "tavily-"
            + sha256(query.strip().encode("utf-8")).hexdigest()[:24]
        ).strip()
        return TavilySearchBatch(
            hits=tuple(hits),
            request_receipt_id=request_receipt_id,
            credits=credits,
            cost_usd=cost_usd,
            cost_is_estimated=cost_is_estimated,
        )


class OriginalTextFetcher:
    def __init__(
        self,
        *,
        session=None,
        timeout_seconds: float = 20.0,
        max_content_bytes: int = 8_000_000,
        minimum_text_chars: int = 200,
        user_agent: str = "FinancialAgent-ThemeChokepoint/1.0",
    ):
        if timeout_seconds <= 0 or max_content_bytes <= 0 or minimum_text_chars <= 0:
            raise ValueError("original-text fetch budgets must be positive")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.max_content_bytes = max_content_bytes
        self.minimum_text_chars = minimum_text_chars
        self.user_agent = user_agent

    def fetch(self, hit: SearchHit) -> OriginalDocument | None:
        if not _is_public_http_url(hit.url):
            return None
        try:
            response = self.session.get(
                hit.url,
                headers={"User-Agent": self.user_agent, "Accept": "text/html,application/pdf"},
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return None
        final_url = str(response.url or hit.url)
        if not _is_public_http_url(final_url):
            return None
        declared_length = response.headers.get("Content-Length")
        if declared_length:
            try:
                if int(declared_length) > self.max_content_bytes:
                    return None
            except ValueError:
                return None
        content = bytes(response.content)
        if len(content) > self.max_content_bytes:
            return None
        content_type = response.headers.get("Content-Type", "").casefold()
        publication_date = hit.publication_date
        publication_date_verified = False
        if "application/pdf" in content_type or urlsplit(final_url).path.casefold().endswith(".pdf"):
            text = _extract_pdf_text(content)
            source_type = "original_pdf"
        else:
            metadata = trafilatura.extract_metadata(response.text)
            metadata_date = _parse_date(getattr(metadata, "date", None))
            if metadata_date is not None:
                publication_date = metadata_date
                publication_date_verified = True
            text = trafilatura.extract(
                response.text,
                include_links=False,
                include_images=False,
                include_formatting=False,
            ) or ""
            source_type = "original_web"
        text = _normalize_text(text)
        if len(text) < self.minimum_text_chars:
            return None
        canonical_url = canonicalize_source_url(final_url)
        content_hash = sha256(text.encode("utf-8")).hexdigest()
        article_id = "article_" + sha256(
            f"{canonical_url}\0{content_hash}".encode("utf-8")
        ).hexdigest()[:20]
        return OriginalDocument(
            article_id=article_id,
            canonical_url=canonical_url,
            title=hit.title,
            publisher=(urlsplit(canonical_url).hostname or "unknown").casefold(),
            source_type=source_type,
            publication_date=publication_date,
            text=text,
            content_hash=content_hash,
            publication_date_verified=publication_date_verified,
        )


class TavilyOriginalEvidenceAcquirer:
    def __init__(
        self,
        search_provider: TavilySearchProvider,
        fetcher: OriginalTextFetcher,
        extractor: EvidenceSpanExtractor,
        *,
        metered: bool = False,
    ):
        self.search_provider = search_provider
        self.fetcher = fetcher
        self.extractor = extractor
        self.metered = metered

    def acquire(self, *, query, run, node, material_field):
        candidates = []
        search_with_receipt = getattr(self.search_provider, "search_with_receipt", None)
        if self.metered and callable(search_with_receipt):
            search_batch = search_with_receipt(query)
            hits = search_batch.hits
            cost_usd = search_batch.cost_usd
            request_receipt_ids = [search_batch.request_receipt_id]
        else:
            hits = self.search_provider.search(query)
            cost_usd = 0.0
            request_receipt_ids = []
        for hit in hits:
            document = self.fetcher.fetch(hit)
            if document is None:
                continue
            spans = self.extractor.extract(
                document=document,
                node=node,
                material_field=material_field,
                query=query,
            )
            if self.metered:
                consume_cost = getattr(self.extractor, "consume_cost_usd", None)
                if callable(consume_cost):
                    cost_usd += float(consume_cost())
            for ordinal, span in enumerate(spans):
                quote = span.exact_quote.strip()
                start = document.text.find(quote)
                if not quote or start < 0:
                    raise ValueError("evidence extractor quote is not present in original text")
                if not span.statement.strip() or not span.limitations.strip() or not span.location.strip():
                    raise ValueError("extracted evidence requires statement, limitations and location")
                if span.scoring_use in {"primary", "floor_only"} and not node.product_anchor_id:
                    raise ValueError("scoring evidence requires an atomic product scope")
                scoring_use = (
                    span.scoring_use
                    if document.publication_date_verified
                    else "context_only"
                )
                primary_scoring_dimension = (
                    span.primary_scoring_dimension
                    if document.publication_date_verified
                    else None
                )
                scope = AssessmentScope(
                    company_id=None,
                    product_id=node.product_anchor_id or "",
                    segment_id=node.node_id,
                    customer_or_platform_scope=run.demand_frame.scope,
                    geography=run.request.region,
                    time_horizon_months=run.request.time_horizon_months,
                    as_of_date=run.request.as_of_date,
                )
                normalized_fact = " ".join(span.statement.casefold().split())
                fact_key = "fact_" + sha256(
                    "\0".join(
                        (
                            scope.product_id,
                            scope.segment_id,
                            scope.customer_or_platform_scope,
                            scope.geography,
                            span.data_as_of_date.isoformat() if span.data_as_of_date else "",
                            normalized_fact,
                        )
                    ).encode("utf-8")
                ).hexdigest()[:20]
                claim_id = "claim_" + sha256(
                    "\0".join(
                        (
                            node.node_id,
                            material_field,
                            document.article_id,
                            str(ordinal),
                            quote,
                            fact_key,
                            span.claim_type,
                            primary_scoring_dimension or "",
                            scoring_use,
                        )
                    ).encode("utf-8")
                ).hexdigest()[:20]
                scoring_dimension = primary_scoring_dimension or material_field
                event_identity = (
                    "explicit:" + " ".join(span.origin_event_key.casefold().split())
                    if span.origin_event_key and span.origin_event_key.strip()
                    else "fallback:"
                    + _event_fingerprint(
                        "\n".join(
                            (
                                quote,
                                span.data_as_of_date.isoformat()
                                if span.data_as_of_date
                                else "",
                                node.node_id,
                                material_field,
                            )
                        )
                    )
                )
                event_hash = sha256(event_identity.encode("utf-8")).hexdigest()[:20]
                candidates.append(
                    EvidenceCandidate(
                        article_id=document.article_id,
                        canonical_url=document.canonical_url,
                        source_title=document.title,
                        publisher=document.publisher,
                        source_type=document.source_type,
                        publication_date=document.publication_date,
                        data_as_of_date=span.data_as_of_date,
                        location=span.location.strip(),
                        quote_start=start,
                        quote_end=start + len(quote),
                        exact_quote=quote,
                        original_text=document.text,
                        source_mode="original_text",
                        stance=span.stance,
                        limitations=span.limitations.strip(),
                        extraction_model=self.extractor.model_version,
                        prompt_version=self.extractor.prompt_version,
                        origin_event_id=f"web_event_{event_hash}",
                        evidence_family_id=f"web_family_{event_hash}",
                        claim=ClaimDraft(
                            claim_id=claim_id,
                            node_id=node.node_id,
                            claim_type=span.claim_type,
                            statement=span.statement.strip(),
                            material_field=material_field,
                            primary_scoring_dimension=primary_scoring_dimension,
                            scoring_use=scoring_use,
                            fact_key=fact_key,
                            assessment_scope=scope,
                            condition_ids=(f"{scoring_dimension}.source_fact",),
                            claim_capabilities=("general_scoring_evidence",),
                            source_ambiguity=not document.publication_date_verified,
                            scoring_eligible=document.publication_date_verified,
                        ),
                    )
                )
        if self.metered:
            return EvidenceAcquisitionBatch(
                candidates=tuple(candidates),
                cost_usd=round(cost_usd, 6),
                request_receipt_ids=tuple(request_receipt_ids),
            )
        return candidates


def tavily_cost_from_response(
    headers,
    payload,
    *,
    search_depth: str,
    estimated_cost_usd_per_credit: float,
) -> tuple[float, float, bool]:
    """Return credits, USD cost and whether USD was estimated."""
    try:
        actual_cost = float(headers.get("X-Cost-Usd"))
    except (TypeError, ValueError):
        actual_cost = None
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    raw_credits = (
        usage.get("credits") if isinstance(usage, dict) else None
    )
    if raw_credits is None and isinstance(payload, dict):
        raw_credits = payload.get("credits")
    try:
        credits = float(raw_credits)
    except (TypeError, ValueError):
        credits = 2.0 if search_depth == "advanced" else 1.0
    credits = max(0.0, credits)
    if actual_cost is not None and actual_cost >= 0:
        return credits, round(actual_cost, 6), False
    return (
        credits,
        round(credits * estimated_cost_usd_per_credit, 6),
        True,
    )


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_public_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return False
    hostname = parts.hostname.casefold().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - dependency gate
        raise RuntimeError("PDF original-text extraction requires pypdf>=5.0") from error
    try:
        reader = PdfReader(BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _event_fingerprint(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
