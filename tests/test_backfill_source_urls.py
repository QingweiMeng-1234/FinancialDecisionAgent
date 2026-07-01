import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backfill_source_urls import (
    BackfillReportRow,
    MatchResult,
    SearchResult,
    build_search_queries,
    choose_best_match,
    maybe_review_with_llm,
    parse_duckduckgo_results,
    should_trigger_llm_review,
    write_report,
)
from event_collector.news_storage import ArticleRecord, NewsArticle


def make_record(title: str, description: str = "", content: str = "") -> ArticleRecord:
    return ArticleRecord(
        id=1,
        article=NewsArticle(
            source="news",
            title=title,
            description=description,
            content=content,
            url="internal://news/1",
            published_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )


def test_build_search_queries_prefers_clean_legacy_title():
    record = make_record(
        "OpenAI Launches Daybreak for AI-Powered Vulnerability Detection - The Hacker News. OpenAI launched Daybreak with GPT-5.5 tools.",
        "Legacy description",
    )

    queries = build_search_queries(record)

    assert "OpenAI Launches Daybreak for AI-Powered Vulnerability Detection" in queries[0]


def test_build_search_queries_strips_noise_and_none_suffix():
    record = make_record(
        "Stock Market Today: Dow, Nasdaq Sink on Soaring Inflation — Live Updates - WSJ. None",
        "Stock Market Today: Dow, Nasdaq Sink on Soaring Inflation — Live Updates - WSJ. None",
    )

    queries = build_search_queries(record)

    assert "None" not in queries[0]
    assert len(queries[0]) <= 120


def test_choose_best_match_uses_title_similarity():
    record = make_record(
        "Netflix sued by Texas AG for alleged surveillance, addictive features - Politico. The complaint follows a product liability playbook.",
        "Legacy description",
    )
    exactish = SearchResult(
        title="Netflix sued by Texas AG for alleged surveillance, addictive features - Politico",
        description="The complaint follows a product liability playbook that has worked against other tech platforms.",
        url="https://www.politico.com/story",
        published_at=None,
        source_name="politico.com",
        raw={},
    )
    distractor = SearchResult(
        title="Texas AG sues streaming platform over consumer practices",
        description="Different article.",
        url="https://example.com/other-story",
        published_at=None,
        source_name="example.com",
        raw={},
    )

    match = choose_best_match(record, [distractor, exactish], min_score=0.72)

    assert match is not None
    assert match.candidate.url == "https://www.politico.com/story"
    assert match.score >= 0.72


def test_choose_best_match_penalizes_wrong_publisher_domain():
    record = make_record(
        "Stock Market Today: Dow, Nasdaq Sink on Soaring Inflation - WSJ. None",
        "Legacy description",
    )
    wrong_publisher = SearchResult(
        title="Stock Market Today: Dow, Nasdaq Sink on Soaring Inflation",
        description="",
        url="https://www.youtube.com/watch?v=abc",
        published_at=None,
        source_name="youtube.com",
        raw={},
    )

    match = choose_best_match(record, [wrong_publisher], min_score=0.72)

    assert match is None


def test_parse_duckduckgo_results_extracts_direct_links():
    html_doc = """
    <html><body>
      <a class="result__a" href="https://thehackernews.com/2026/05/openai-launches-daybreak.html">
        OpenAI Launches Daybreak for AI-Powered Vulnerability Detection and ...
      </a>
    </body></html>
    """

    results = parse_duckduckgo_results(html_doc, limit=5)

    assert len(results) == 1
    assert results[0].url == "https://thehackernews.com/2026/05/openai-launches-daybreak.html"
    assert "OpenAI Launches Daybreak" in results[0].title


def test_write_report_outputs_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "backfill_report.csv")
        write_report(
            path,
            [
                BackfillReportRow(
                    article_id=1,
                    status="url_backfilled",
                    original_title="Legacy title",
                    original_url="internal://news/1",
                    recovered_url="https://example.com/story",
                    fetched_content="no",
                    score="0.91",
                    query="'legacy title example'",
                    failure_reason="",
                    llm_reviewed="no",
                    llm_decision="",
                    llm_reason="",
                )
            ],
        )

        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()

    assert "article_id,status,original_title" in content
    assert "https://example.com/story" in content


class FakeReviewer:
    def review_candidates(self, prompt):
        return type(
            "Review",
            (),
            {
                "best_candidate_index": 1,
                "is_confident_match": True,
                "is_original_source_likely": True,
                "reason": "Second candidate is the original publisher.",
            },
        )()


def test_should_trigger_llm_review_for_suspicious_domain():
    ranked = [
        MatchResult(
            candidate=SearchResult(
                title="Stock Market Today",
                description="",
                url="https://www.youtube.com/watch?v=abc",
                published_at=None,
                source_name="youtube.com",
                raw={},
            ),
            score=0.99,
        )
    ]

    assert should_trigger_llm_review(ranked, 0.90, 0.03) is True


def test_maybe_review_with_llm_can_override_top_rule_match():
    record = make_record("Stock Market Today - WSJ. None", "Legacy description")
    ranked = [
        MatchResult(
            candidate=SearchResult(
                title="Stock Market Today",
                description="",
                url="https://www.youtube.com/watch?v=abc",
                published_at=None,
                source_name="youtube.com",
                raw={},
            ),
            score=0.99,
        ),
        MatchResult(
            candidate=SearchResult(
                title="Stock Market Today - WSJ",
                description="",
                url="https://www.wsj.com/business/story",
                published_at=None,
                source_name="wsj.com",
                raw={},
            ),
            score=0.88,
        ),
    ]

    chosen, reviewed, decision, reason = maybe_review_with_llm(
        record,
        ranked,
        FakeReviewer(),
        score_threshold=0.90,
        margin_threshold=0.03,
    )

    assert chosen is not None
    assert chosen.candidate.url == "https://www.wsj.com/business/story"
    assert reviewed == "yes"
    assert decision == "accepted_original"
    assert "original publisher" in reason
