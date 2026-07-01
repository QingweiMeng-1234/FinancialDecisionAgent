#!/usr/bin/env python3
"""Backfill missing external source URLs for legacy article rows via web search."""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.article_content import ArticleContentFetcher
from event_collector.news_storage import ArticleRecord, SQLiteNewsStore
from event_collector.openai_client_base import OpenAIStructuredOutputClient


DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DEFAULT_MIN_SCORE = 0.72
DEFAULT_RESULT_LIMIT = 10
DEFAULT_REQUEST_DELAY_SECONDS = 2.0
DEFAULT_MAX_RETRIES = 3
MAX_QUERY_LENGTH = 140
DEFAULT_LLM_REVIEW_SCORE_THRESHOLD = 0.90
DEFAULT_LLM_REVIEW_MARGIN_THRESHOLD = 0.03
SUSPICIOUS_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "msn.com",
    "newsbreak.com",
    "upstract.com",
    "publicnow.com",
}


@dataclass
class SearchResult:
    title: str
    description: str
    url: str
    published_at: datetime | None
    source_name: str
    raw: dict[str, Any]


@dataclass
class MatchResult:
    candidate: SearchResult
    score: float


@dataclass
class BackfillReportRow:
    article_id: int
    status: str
    original_title: str
    original_url: str
    recovered_url: str
    fetched_content: str
    score: str
    query: str
    failure_reason: str
    llm_reviewed: str
    llm_decision: str
    llm_reason: str


class LLMReviewResponse(BaseModel):
    best_candidate_index: int = Field(ge=0)
    is_confident_match: bool
    is_original_source_likely: bool
    reason: str = Field(min_length=3)


class OpenAIBackfillReviewerClient(OpenAIStructuredOutputClient):
    DEFAULT_MODEL = "deepseek-v4-flash"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for LLM backfill review"
    REFUSAL_ERROR_PREFIX = "OpenAI refused backfill review"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed backfill review"

    def review_candidates(self, prompt: str) -> LLMReviewResponse:
        return self.parse_structured_output(
            system_prompt=LLM_REVIEW_SYSTEM_PROMPT,
            user_content=prompt,
            response_format=LLMReviewResponse,
        )


LLM_REVIEW_SYSTEM_PROMPT = """
You review candidate source URLs for a legacy news article backfill task.

Choose the candidate that most likely corresponds to the same article as the legacy record.
Prefer the original publisher over aggregators, mirrors, social posts, and video pages.

Rules:
- Prioritize same story match first, original source second.
- If all candidates look weak, still return the best index but mark is_confident_match as false.
- Mark is_original_source_likely false for aggregators, social posts, mirrors, reposts, or generic feed pages.
- Keep the reason short and specific.
""".strip()


class DuckDuckGoSearchClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.session = session or requests.Session()
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries
        self._last_request_started_at = 0.0

    def search(self, query: str, limit: int = DEFAULT_RESULT_LIMIT) -> list[SearchResult]:
        for attempt in range(1, self.max_retries + 1):
            self._apply_rate_limit()
            response = self.session.post(
                DUCKDUCKGO_HTML_URL,
                data={"q": query},
                headers={"User-Agent": "FinancialAgent/1.0"},
                timeout=20,
            )
            if response.status_code in {202, 403, 429}:
                if attempt == self.max_retries:
                    response.raise_for_status()
                time.sleep(max(self.request_delay_seconds * attempt, 2.0))
                continue

            response.raise_for_status()
            return parse_duckduckgo_results(response.text, limit=limit)

        raise RuntimeError("DuckDuckGo search failed after retries")

    def _apply_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_started_at
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)
        self._last_request_started_at = time.monotonic()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing external source URLs for legacy article rows via web search."
    )
    parser.add_argument("--db-path", default="news_articles.db", help="SQLite article database path")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of candidate rows to process")
    parser.add_argument("--result-limit", type=int, default=DEFAULT_RESULT_LIMIT, help="Search results to score per query")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="Minimum title-match score to accept")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS, help="Seconds to wait between search requests")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Max retries for transient search failures")
    parser.add_argument("--report-path", default="backfill_report.csv", help="CSV report output path")
    parser.add_argument("--llm-review", action="store_true", help="Use an OpenAI reviewer for borderline or suspicious matches")
    parser.add_argument("--llm-review-score-threshold", type=float, default=DEFAULT_LLM_REVIEW_SCORE_THRESHOLD, help="Review matches at or below this score")
    parser.add_argument("--llm-review-margin-threshold", type=float, default=DEFAULT_LLM_REVIEW_MARGIN_THRESHOLD, help="Review when top candidates are within this score margin")
    parser.add_argument("--fetch-content", action="store_true", help="Fetch exact article text after URL recovery")
    parser.add_argument("--force", action="store_true", help="Re-check rows even if they already have external URLs")
    return parser.parse_args(argv)


def normalize_text(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in (value or ""))
    return " ".join(normalized.split())


def first_sentence(value: str) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return ""
    for delimiter in (". ", " — ", " – "):
        if delimiter in text:
            head = text.split(delimiter, 1)[0].strip()
            if len(head) >= 20:
                return head
    return text


def strip_publisher_suffix(value: str) -> str:
    text = " ".join((value or "").split()).strip()
    if " - " not in text:
        return text
    headline, suffix = text.rsplit(" - ", 1)
    if len(headline.strip()) >= 20 and len(suffix.strip()) <= 40:
        return headline.strip()
    return text


def clean_query_text(value: str) -> str:
    text = " ".join((value or "").split()).strip()
    text = text.replace("&amp;", "&")
    text = text.replace("None", " ")
    text = re.sub(r"[\"'`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:;,.()[]{}")


def shorten_query(value: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    text = clean_query_text(value)
    if len(text) <= max_length:
        return text
    shortened = text[:max_length].rsplit(" ", 1)[0].strip()
    return shortened or text[:max_length].strip()


def extract_publisher_hint(value: str) -> str:
    text = " ".join((value or "").split()).strip()
    if " - " in text:
        return text.rsplit(" - ", 1)[-1].strip()
    return ""


def publisher_tokens(value: str) -> list[str]:
    cleaned = normalize_text(value)
    common = {"the", "and", "news", "daily", "live", "coverage", "updates"}
    return [token for token in cleaned.split() if len(token) >= 3 and token not in common]


def publisher_matches_candidate(publisher_hint: str, candidate: SearchResult) -> bool:
    tokens = publisher_tokens(publisher_hint)
    if not tokens:
        return True
    candidate_domain = normalize_text(candidate.source_name)
    candidate_title = normalize_text(candidate.title)
    return any(token in candidate_domain or token in candidate_title for token in tokens)


def build_search_queries(record: ArticleRecord) -> list[str]:
    title = shorten_query(strip_publisher_suffix(first_sentence(record.article.title)))
    description = shorten_query(strip_publisher_suffix(first_sentence(record.article.description)))
    publisher = clean_query_text(extract_publisher_hint(record.article.title))
    date_iso = ensure_utc(record.article.published_at).date().isoformat()
    date_year = str(ensure_utc(record.article.published_at).year)

    raw_queries = [
        f"\"{title}\" \"{publisher}\" \"{date_iso}\"" if publisher else f"\"{title}\" \"{date_iso}\"",
        f"\"{title}\" \"{publisher}\"" if publisher else f"\"{title}\"",
        f"{title} {publisher} {date_year}".strip(),
        f"{description} {publisher} {date_year}".strip(),
    ]

    queries = []
    seen = set()
    for raw_query in raw_queries:
        query = shorten_query(raw_query)
        if len(clean_query_text(query)) < 10:
            continue
        normalized = normalize_text(query)
        if normalized in seen:
            continue
        seen.add(normalized)
        queries.append(query)
    return queries


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(a=normalize_text(left), b=normalize_text(right)).ratio()


def description_overlap(left: str, right: str) -> float:
    left_tokens = set(normalize_text(left).split())
    right_tokens = set(normalize_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return intersection / max(min(len(left_tokens), len(right_tokens)), 1)


def domain_to_source_name(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def parse_duckduckgo_results(html_text: str, limit: int = DEFAULT_RESULT_LIMIT) -> list[SearchResult]:
    results = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        url = html.unescape(match.group(1)).strip()
        title_html = match.group(2)
        title = clean_query_text(re.sub(r"<.*?>", "", html.unescape(title_html)))
        if not url or not title:
            continue
        results.append(
            SearchResult(
                title=title,
                description="",
                url=url,
                published_at=None,
                source_name=domain_to_source_name(url),
                raw={"provider": "duckduckgo_html"},
            )
        )
        if len(results) >= limit:
            break
    return results


def score_candidate(record: ArticleRecord, candidate: SearchResult) -> float:
    legacy_title = strip_publisher_suffix(first_sentence(record.article.title))
    candidate_title = strip_publisher_suffix(first_sentence(candidate.title))
    score = title_similarity(legacy_title, candidate_title)

    if record.article.description and candidate.description:
        score += 0.15 * description_overlap(record.article.description, candidate.description)

    legacy_publisher = clean_query_text(extract_publisher_hint(record.article.title))
    if legacy_publisher:
        if publisher_matches_candidate(legacy_publisher, candidate):
            score += 0.08
        else:
            score -= 0.35

    return min(score, 0.99)


def choose_best_match(
    record: ArticleRecord,
    candidates: list[SearchResult],
    min_score: float,
) -> MatchResult | None:
    if not candidates:
        return None

    best = None
    for candidate in candidates:
        if not candidate.url or not candidate.title:
            continue
        score = score_candidate(record, candidate)
        if best is None or score > best.score:
            best = MatchResult(candidate=candidate, score=score)

    if best is None or best.score < min_score:
        return None
    return best


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def should_process(record: ArticleRecord, force: bool) -> bool:
    if record.article.source != "news":
        return False
    if force:
        return True
    current_url = record.article.original_url or record.article.url or ""
    return not current_url.startswith("http")


def parse_detail(detail: str) -> tuple[str, str, str]:
    recovered_url = ""
    score = ""
    query = ""
    parts = detail.split(" query=", 1)
    head = parts[0]
    if parts[1:]:
        query = parts[1].strip()
    url_score = head.split(" score=", 1)
    recovered_url = url_score[0].strip()
    if url_score[1:]:
        score = url_score[1].strip()
    return recovered_url, score, query


def write_report(report_path: str, rows: list[BackfillReportRow]) -> None:
    with open(report_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "article_id",
                "status",
                "original_title",
                "original_url",
                "recovered_url",
                "fetched_content",
                "score",
                "query",
                "failure_reason",
                "llm_reviewed",
                "llm_decision",
                "llm_reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "article_id": row.article_id,
                    "status": row.status,
                    "original_title": row.original_title,
                    "original_url": row.original_url,
                    "recovered_url": row.recovered_url,
                    "fetched_content": row.fetched_content,
                    "score": row.score,
                    "query": row.query,
                    "failure_reason": row.failure_reason,
                    "llm_reviewed": row.llm_reviewed,
                    "llm_decision": row.llm_decision,
                    "llm_reason": row.llm_reason,
                }
            )


def suspicious_domain(candidate: SearchResult) -> bool:
    domain = domain_to_source_name(candidate.url)
    return domain in SUSPICIOUS_DOMAINS


def rank_candidates(record: ArticleRecord, candidates: list[SearchResult]) -> list[MatchResult]:
    ranked = [
        MatchResult(candidate=candidate, score=score_candidate(record, candidate))
        for candidate in candidates
        if candidate.url and candidate.title
    ]
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


def should_trigger_llm_review(
    ranked_candidates: list[MatchResult],
    score_threshold: float,
    margin_threshold: float,
) -> bool:
    if not ranked_candidates:
        return False
    top = ranked_candidates[0]
    if top.score <= score_threshold:
        return True
    if suspicious_domain(top.candidate):
        return True
    if len(ranked_candidates) >= 2 and (top.score - ranked_candidates[1].score) <= margin_threshold:
        return True
    return False


def format_llm_review_prompt(record: ArticleRecord, ranked_candidates: list[MatchResult], max_candidates: int = 5) -> str:
    lines = [
        f"Legacy article id: {record.id}",
        f"Legacy title: {record.article.title}",
        f"Legacy description: {record.article.description}",
        f"Legacy published_at: {record.article.published_at.isoformat()}",
        "",
        "Candidates:",
    ]
    for index, item in enumerate(ranked_candidates[:max_candidates]):
        lines.extend(
            [
                f"[{index}] title: {item.candidate.title}",
                f"[{index}] url: {item.candidate.url}",
                f"[{index}] source_name: {item.candidate.source_name}",
                f"[{index}] rule_score: {item.score:.2f}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def maybe_review_with_llm(
    record: ArticleRecord,
    ranked_candidates: list[MatchResult],
    reviewer: OpenAIBackfillReviewerClient | None,
    *,
    score_threshold: float,
    margin_threshold: float,
) -> tuple[MatchResult | None, str, str, str]:
    if not ranked_candidates:
        return None, "no", "", ""
    if reviewer is None or not should_trigger_llm_review(ranked_candidates, score_threshold, margin_threshold):
        return ranked_candidates[0], "no", "", ""

    prompt = format_llm_review_prompt(record, ranked_candidates)
    response = reviewer.review_candidates(prompt)
    chosen_index = response.best_candidate_index
    if chosen_index < 0 or chosen_index >= min(len(ranked_candidates), 5):
        chosen_index = 0
    chosen = ranked_candidates[chosen_index]
    if not response.is_confident_match:
        return None, "yes", "rejected", response.reason
    decision = "accepted_original" if response.is_original_source_likely else "accepted_related"
    return chosen, "yes", decision, response.reason


def backfill_record(
    storage: SQLiteNewsStore,
    client: DuckDuckGoSearchClient,
    record: ArticleRecord,
    *,
    result_limit: int,
    min_score: float,
    fetch_content: bool,
    reviewer: OpenAIBackfillReviewerClient | None = None,
    llm_review_score_threshold: float = DEFAULT_LLM_REVIEW_SCORE_THRESHOLD,
    llm_review_margin_threshold: float = DEFAULT_LLM_REVIEW_MARGIN_THRESHOLD,
    content_fetcher: ArticleContentFetcher | None = None,
) -> tuple[str, str, str, str, str]:
    queries = build_search_queries(record)
    if not queries:
        return "skipped", "query too short", "no", "", ""

    all_candidates = []
    best_match = None
    query_used = None

    for query in queries:
        candidates = client.search(query=query, limit=result_limit)
        all_candidates.extend(candidates)
        match = choose_best_match(record, candidates, min_score=min_score)
        if match is not None:
            best_match = match
            query_used = query
            break

    ranked_candidates = rank_candidates(record, all_candidates)
    if best_match is None:
        best_match = choose_best_match(record, all_candidates, min_score=min_score)
    if best_match is None:
        reviewed_match, llm_reviewed, llm_decision, llm_reason = maybe_review_with_llm(
            record,
            ranked_candidates,
            reviewer,
            score_threshold=llm_review_score_threshold,
            margin_threshold=llm_review_margin_threshold,
        )
        if reviewed_match is None:
            return "unmatched", f"no candidate >= {min_score:.2f}", llm_reviewed, llm_decision, llm_reason
        best_match = reviewed_match
    else:
        reviewed_match, llm_reviewed, llm_decision, llm_reason = maybe_review_with_llm(
            record,
            ranked_candidates,
            reviewer,
            score_threshold=llm_review_score_threshold,
            margin_threshold=llm_review_margin_threshold,
        )
        if reviewed_match is not None:
            best_match = reviewed_match

    matched = best_match.candidate
    query_label = query_used or queries[0]
    if fetch_content:
        fetcher = content_fetcher or ArticleContentFetcher()
        fetch_result = fetcher.fetch(matched.url)
        storage.update_article_content(
            record.id,
            content=fetch_result.content,
            title=matched.title or record.article.title,
            description=matched.description or record.article.description,
            original_url=fetch_result.original_url,
            canonical_url=fetch_result.canonical_url,
            published_at=record.article.published_at,
        )
        return "content_backfilled", f"{matched.url} score={best_match.score:.2f} query={query_label!r}", llm_reviewed, llm_decision, llm_reason

    storage.update_article_source_metadata(
        record.id,
        original_url=matched.url,
        title=matched.title or None,
        description=matched.description or None,
        published_at=record.article.published_at,
    )
    return "url_backfilled", f"{matched.url} score={best_match.score:.2f} query={query_label!r}", llm_reviewed, llm_decision, llm_reason


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    storage = SQLiteNewsStore(db_path=args.db_path)
    storage.init_db()
    client = DuckDuckGoSearchClient(
        request_delay_seconds=args.request_delay,
        max_retries=args.max_retries,
    )
    reviewer = OpenAIBackfillReviewerClient() if args.llm_review else None

    try:
        records = storage.list_article_records(source="news", limit=args.limit)
        candidates = [record for record in records if should_process(record, force=args.force)]
        print(f"Loaded {len(records)} news rows; {len(candidates)} need backfill.")
        report_rows: list[BackfillReportRow] = []

        stats = {
            "url_backfilled": 0,
            "content_backfilled": 0,
            "unmatched": 0,
            "skipped": 0,
            "failed": 0,
        }

        for index, record in enumerate(candidates, start=1):
            try:
                status, detail, llm_reviewed, llm_decision, llm_reason = backfill_record(
                    storage,
                    client,
                    record,
                    result_limit=args.result_limit,
                    min_score=args.min_score,
                    fetch_content=args.fetch_content,
                    reviewer=reviewer,
                    llm_review_score_threshold=args.llm_review_score_threshold,
                    llm_review_margin_threshold=args.llm_review_margin_threshold,
                )
                stats[status] += 1
                recovered_url, score, query = parse_detail(detail)
                report_rows.append(
                    BackfillReportRow(
                        article_id=record.id,
                        status=status,
                        original_title=record.article.title,
                        original_url=record.article.original_url or record.article.url or "",
                        recovered_url=recovered_url,
                        fetched_content="yes" if status == "content_backfilled" else "no",
                        score=score,
                        query=query,
                        failure_reason="",
                        llm_reviewed=llm_reviewed,
                        llm_decision=llm_decision,
                        llm_reason=llm_reason,
                    )
                )
                print(f"[{index}/{len(candidates)}] article {record.id}: {status} -> {detail}")
            except Exception as exc:
                stats["failed"] += 1
                report_rows.append(
                    BackfillReportRow(
                        article_id=record.id,
                        status="failed",
                        original_title=record.article.title,
                        original_url=record.article.original_url or record.article.url or "",
                        recovered_url="",
                        fetched_content="no",
                        score="",
                        query="",
                        failure_reason=str(exc),
                        llm_reviewed="no",
                        llm_decision="",
                        llm_reason="",
                    )
                )
                print(f"[{index}/{len(candidates)}] article {record.id}: failed -> {exc}")

        write_report(args.report_path, report_rows)

        print()
        print("Backfill complete.")
        for key, value in stats.items():
            print(f"{key}: {value}")
        print(f"report_path: {args.report_path}")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
