from __future__ import annotations

from datetime import date
import sys
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    ExtractedEvidenceSpan,
    ResearchRequest,
    Stage1RunSnapshot,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.providers.tavily import (
    OriginalDocument,
    OriginalTextFetcher,
    SearchHit,
    TavilyOriginalEvidenceAcquirer,
    TavilySearchProvider,
)
from event_collector.theme_chokepoint.providers.llm import (
    OpenAICompatibleEvidenceSpanExtractor,
)


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", url="", content_type="text/html"):
        self._payload = payload
        self.content = content
        self.url = url
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, search_payload, pages):
        self.search_payload = search_payload
        self.pages = pages
        self.posts = []
        self.gets = []

    def post(self, url, *, headers, json, timeout):
        self.posts.append((url, headers, json, timeout))
        return FakeResponse(payload=self.search_payload, url=url)

    def get(self, url, *, headers, timeout, allow_redirects):
        self.gets.append((url, headers, timeout, allow_redirects))
        return self.pages[url]


class ExactSentenceExtractor:
    model_version = "evidence-extractor-test-v1"
    prompt_version = "evidence-span-v1"

    def extract(self, *, document, node, material_field, query):
        quote = "Qualified supply grew only five percent year over year."
        assert quote in document.text
        assert material_field == "effective_supply_concentration"
        return [
            ExtractedEvidenceSpan(
                exact_quote=quote,
                claim_type="source_fact",
                statement=quote,
                stance="supports",
                limitations="Company-reported and lacks an industry denominator.",
                location="Capacity section",
                primary_scoring_dimension=material_field,
                scoring_use="primary",
                data_as_of_date=date(2026, 6, 30),
            )
        ]


def _run():
    request = ResearchRequest(
        run_id="run-provider-1",
        theme="AI data-center power",
        trigger="AI load growth",
        region="global",
        as_of_date=date(2026, 8, 16),
        time_horizon_months=24,
        analysis_goal="find chokepoints",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=2,
        max_nodes=10,
        max_iterations=2,
        max_sources=5,
        max_time_seconds=300,
        max_cost_usd=5,
        max_product_anchors=1,
    )
    return Stage1RunSnapshot(
        run_id=request.run_id,
        request=request,
        status=None,
        demand_frame=DemandFrame(
            normalized_theme=request.theme,
            scope="global 24 months",
            exclusions=(),
            demand_hypothesis="AI load increases UPS demand.",
            measurable_demand_variables=("MW",),
            time_horizon_months=24,
            unresolved_questions=(),
        ),
        product_anchors=(),
        created_at=None,
        updated_at=None,
    )


def _node():
    return SupplyChainNode(
        node_id="segment-goes",
        normalized_name="grain oriented electrical steel",
        node_type="material_segment",
        depth=1,
        status="proposed",
        description="transformer input",
        aliases=("GOES",),
        product_anchor_id="anchor-ups",
    )


def test_tavily_provider_requires_key_before_network_and_never_echoes_key(monkeypatch):
    """SELECT INVARIANT: missing/failed credentials never leak or trigger blind requests."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    session = FakeSession({}, {})

    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        TavilySearchProvider(session=session)
    assert session.posts == []

    class FailingSession:
        def post(self, *args, **kwargs):
            raise RuntimeError("transport accidentally included secret-test-key")

    provider = TavilySearchProvider(
        api_key="secret-test-key", session=FailingSession()
    )
    with pytest.raises(RuntimeError) as exc_info:
        provider.search("qualified transformer supply")
    assert "secret-test-key" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_tavily_discovers_url_but_evidence_comes_from_refetched_original(monkeypatch):
    """SELECT INVARIANT: Tavily summary is discovery-only; exact quote comes from GET body."""
    monkeypatch.setenv("TAVILY_API_KEY", "secret-test-key")
    url = "https://issuer.example.com/q2-report?utm_source=tavily"
    html = b"""
        <html><head><title>Official Q2 report</title></head><body>
        <main><h1>Capacity section</h1>
        <p>Capacity is constrained. Qualified supply grew only five percent year over year.</p>
        </main></body></html>
    """
    session = FakeSession(
        {
            "results": [
                {
                    "url": url,
                    "title": "Official Q2 report",
                    "content": "SEARCH SUMMARY MUST NEVER BECOME EVIDENCE",
                    "score": 0.92,
                    "published_date": "2026-08-01",
                }
            ]
        },
        {
            url: FakeResponse(
                content=html,
                url="https://issuer.example.com/q2-report",
                content_type="text/html; charset=utf-8",
            )
        },
    )
    provider = TavilySearchProvider(session=session, max_results=3)
    fetcher = OriginalTextFetcher(session=session, minimum_text_chars=40)
    acquirer = TavilyOriginalEvidenceAcquirer(
        provider, fetcher, ExactSentenceExtractor()
    )

    candidates = acquirer.acquire(
        query='"grain oriented electrical steel" effective supply concentration',
        run=_run(),
        node=_node(),
        material_field="effective_supply_concentration",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_mode == "original_text"
    assert "SEARCH SUMMARY" not in candidate.original_text
    assert candidate.original_text[candidate.quote_start : candidate.quote_end] == candidate.exact_quote
    assert candidate.canonical_url == "https://issuer.example.com/q2-report"
    assert candidate.publication_date == date(2026, 8, 1)
    assert candidate.claim.node_id == "segment-goes"
    assert candidate.claim.fact_key
    assert candidate.claim.assessment_scope.product_id == "anchor-ups"
    assert candidate.claim.assessment_scope.segment_id == "segment-goes"
    assert candidate.claim.assessment_scope.customer_or_platform_scope == "global 24 months"
    assert candidate.claim.assessment_scope.geography == "global"
    assert candidate.claim.condition_ids == (
        "effective_supply_concentration.source_fact",
    )
    post_url, headers, payload, timeout = session.posts[0]
    assert post_url == "https://api.tavily.com/search"
    assert headers["Authorization"] == "Bearer secret-test-key"
    assert payload["include_raw_content"] is False
    assert payload["max_results"] == 3
    assert timeout > 0
    assert session.gets[0][0] == url


def test_tavily_reprints_share_one_event_family_and_search_dates_remain_ambiguous():
    """SELECT INVARIANT: page boilerplate cannot manufacture independent source events."""
    quote = "Qualified supply grew only five percent year over year."
    hits = (
        SearchHit("https://issuer.example.com/release", "Issuer", "", 1, date(2026, 8, 1)),
        SearchHit("https://wire.example.com/reprint", "Wire", "", 1, date(2026, 8, 2)),
    )
    documents = {
        hits[0].url: OriginalDocument(
            article_id="article-official",
            canonical_url=hits[0].url,
            title="Issuer",
            publisher="issuer.example.com",
            source_type="original_web",
            publication_date=hits[0].publication_date,
            text=f"Issuer navigation. {quote}",
            content_hash="a" * 64,
        ),
        hits[1].url: OriginalDocument(
            article_id="article-reprint",
            canonical_url=hits[1].url,
            title="Wire",
            publisher="wire.example.com",
            source_type="original_web",
            publication_date=hits[1].publication_date,
            text=f"Wire navigation and footer. {quote}",
            content_hash="b" * 64,
        ),
    }

    class SearchProvider:
        def search(self, query):
            return hits

    class Fetcher:
        def fetch(self, hit):
            return documents[hit.url]

    candidates = TavilyOriginalEvidenceAcquirer(
        SearchProvider(), Fetcher(), ExactSentenceExtractor()
    ).acquire(
        query="qualified supply",
        run=_run(),
        node=_node(),
        material_field="effective_supply_concentration",
    )

    assert len(candidates) == 2
    assert len({item.origin_event_id for item in candidates}) == 1
    assert len({item.evidence_family_id for item in candidates}) == 1
    assert all(item.claim.source_ambiguity for item in candidates)
    assert all(not item.claim.scoring_eligible for item in candidates)


def test_tavily_fallback_family_is_stable_across_extractor_paraphrases():
    """SELECT INVARIANT: generated statement wording cannot mint source-family credit."""
    quote = "Qualified supply grew only five percent year over year."
    hits = (
        SearchHit("https://issuer.example.com/release", "Issuer", "", 1, date(2026, 8, 1)),
        SearchHit("https://wire.example.com/reprint", "Wire", "", 1, date(2026, 8, 2)),
    )
    documents = {
        hit.url: OriginalDocument(
            article_id=f"article-{index}",
            canonical_url=hit.url,
            title=hit.title,
            publisher=hit.url.split("/")[2],
            source_type="original_web",
            publication_date=hit.publication_date,
            text=f"Page {index} navigation. {quote}",
            content_hash=str(index) * 64,
        )
        for index, hit in enumerate(hits, start=1)
    }

    class SearchProvider:
        def search(self, query):
            return hits

    class Fetcher:
        def fetch(self, hit):
            return documents[hit.url]

    class ParaphrasingExtractor:
        model_version = "test"
        prompt_version = "test"

        def extract(self, *, document, node, material_field, query):
            return [
                ExtractedEvidenceSpan(
                    exact_quote=quote,
                    claim_type="source_fact",
                    statement=f"{document.publisher} described limited qualified growth.",
                    stance="supports",
                    limitations="No explicit origin event key.",
                    location="body",
                    primary_scoring_dimension=material_field,
                    scoring_use="primary",
                    data_as_of_date=date(2026, 6, 30),
                )
            ]

    candidates = TavilyOriginalEvidenceAcquirer(
        SearchProvider(), Fetcher(), ParaphrasingExtractor()
    ).acquire(
        query="qualified supply",
        run=_run(),
        node=_node(),
        material_field="effective_supply_concentration",
    )

    assert len({item.origin_event_id for item in candidates}) == 1
    assert len({item.evidence_family_id for item in candidates}) == 1


def test_tavily_different_reprint_wording_uses_explicit_origin_event_key_for_one_family():
    """SELECT INVARIANT: explicit event provenance, not sentence text, defines a family."""
    hits = (
        SearchHit("https://issuer.example.com/release", "Issuer", "", 1, date(2026, 8, 1)),
        SearchHit("https://wire.example.com/reprint", "Wire", "", 1, date(2026, 8, 2)),
    )
    documents = {
        hits[0].url: OriginalDocument(
            "article-official", hits[0].url, "Issuer", "issuer.example.com",
            "original_web", hits[0].publication_date,
            "Qualified supply grew five percent in the quarter.", "a" * 64,
        ),
        hits[1].url: OriginalDocument(
            "article-reprint", hits[1].url, "Wire", "wire.example.com",
            "original_web", hits[1].publication_date,
            "The issuer reported quarterly qualified supply growth of 5%.", "b" * 64,
        ),
    }

    class SearchProvider:
        def search(self, query):
            return hits

    class Fetcher:
        def fetch(self, hit):
            return documents[hit.url]

    class ProvenanceExtractor:
        model_version = "test"
        prompt_version = "test"

        def extract(self, *, document, node, material_field, query):
            quote = document.text
            return [
                ExtractedEvidenceSpan(
                    exact_quote=quote,
                    claim_type="source_fact",
                    statement=quote,
                    stance="supports",
                    limitations="Reprint provenance explicitly names the same issuer release.",
                    location="body",
                    primary_scoring_dimension=material_field,
                    scoring_use="primary",
                    data_as_of_date=date(2026, 6, 30),
                    origin_event_key="issuer.example.com:2026q2-qualified-supply-release",
                )
            ]

    candidates = TavilyOriginalEvidenceAcquirer(
        SearchProvider(), Fetcher(), ProvenanceExtractor()
    ).acquire(
        query="qualified supply",
        run=_run(),
        node=_node(),
        material_field="effective_supply_concentration",
    )

    assert len({item.origin_event_id for item in candidates}) == 1
    assert len({item.evidence_family_id for item in candidates}) == 1


def test_original_fetcher_rejects_local_or_private_search_result_urls(monkeypatch):
    """SELECT INVARIANT: search results cannot turn original-text fetch into SSRF."""
    monkeypatch.setenv("TAVILY_API_KEY", "secret-test-key")
    session = FakeSession(
        {
            "results": [
                {
                    "url": "http://127.0.0.1:8080/admin",
                    "title": "internal",
                    "content": "summary",
                    "score": 1,
                }
            ]
        },
        {},
    )
    acquirer = TavilyOriginalEvidenceAcquirer(
        TavilySearchProvider(session=session),
        OriginalTextFetcher(session=session),
        ExactSentenceExtractor(),
    )

    assert acquirer.acquire(
        query="private target",
        run=_run(),
        node=_node(),
        material_field="effective_supply_concentration",
    ) == []
    assert session.gets == []


class FakeLLMCompletions:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": __import__("json").dumps(self.payload)})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class FakeLLMClient:
    def __init__(self, payload):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeLLMCompletions(payload)


class SequenceLLMCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        message = type("Message", (), {"content": __import__("json").dumps(payload)})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class SequenceLLMClient:
    def __init__(self, payloads):
        self.chat = type("Chat", (), {})()
        self.chat.completions = SequenceLLMCompletions(payloads)


def _document():
    text = "Capacity is constrained. Qualified supply grew only five percent year over year."
    return OriginalDocument(
        article_id="article-1",
        canonical_url="https://issuer.example.com/q2-report",
        title="Official Q2 report",
        publisher="issuer.example.com",
        source_type="original_web",
        publication_date=date(2026, 8, 1),
        text=text,
        content_hash="a" * 64,
    )


def test_openai_compatible_extractor_returns_only_verbatim_original_spans():
    """SELECT INVARIANT: LLM output is schema-bound and quote-checked against the document."""
    quote = "Qualified supply grew only five percent year over year."
    client = FakeLLMClient(
        {
            "spans": [
                {
                    "exact_quote": quote,
                    "claim_type": "source_fact",
                    "statement": quote,
                    "stance": "supports",
                    "limitations": "No industry-wide denominator.",
                    "location": "Capacity section",
                    "primary_scoring_dimension": "effective_supply_concentration",
                    "scoring_use": "primary",
                    "data_as_of_date": "2026-06-30",
                }
            ]
        }
    )
    extractor = OpenAICompatibleEvidenceSpanExtractor(
        client=client, model="test-model"
    )

    spans = extractor.extract(
        document=_document(),
        node=_node(),
        material_field="effective_supply_concentration",
        query="qualified effective supply",
    )

    assert spans[0].exact_quote == quote
    assert spans[0].data_as_of_date == date(2026, 6, 30)
    assert extractor.model_version == "test-model"
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    system_prompt = call["messages"][0]["content"]
    for required_field in (
        "exact_quote",
        "claim_type",
        "statement",
        "stance",
        "limitations",
        "location",
        "primary_scoring_dimension",
        "scoring_use",
        "data_as_of_date",
    ):
        assert required_field in system_prompt
    assert "If scoring_use is primary" in system_prompt
    assert "If scoring_use is floor_only or context_only" in system_prompt
    assert "SEARCH SUMMARY" not in call["messages"][1]["content"]
    assert _document().text in call["messages"][1]["content"]


def test_openai_compatible_extractor_rejects_paraphrase_as_exact_quote():
    """SELECT INVARIANT: a plausible paraphrase cannot be promoted to an original quote."""
    client = FakeLLMClient(
        {
            "spans": [
                {
                    "exact_quote": "Qualified supply increased by about five percent.",
                    "claim_type": "source_fact",
                    "statement": "Supply grew five percent.",
                    "stance": "supports",
                    "limitations": "No denominator.",
                    "location": "Capacity section",
                    "primary_scoring_dimension": "effective_supply_concentration",
                    "scoring_use": "primary",
                    "data_as_of_date": None,
                }
            ]
        }
    )
    extractor = OpenAICompatibleEvidenceSpanExtractor(client=client, model="test-model")

    with pytest.raises(ValueError, match="not verbatim"):
        extractor.extract(
            document=_document(),
            node=_node(),
            material_field="effective_supply_concentration",
            query="qualified effective supply",
        )


def test_openai_compatible_extractor_hides_invalid_model_payload_from_error_chain():
    """SELECT INVARIANT: malformed model output is rejected without echoing source text."""
    client = FakeLLMClient(
        {
            "spans": [
                {
                    "exact_quote": "Qualified supply grew only five percent year over year.",
                    "unexpected_private_payload": "must-not-appear-in-traceback",
                }
            ]
        }
    )
    extractor = OpenAICompatibleEvidenceSpanExtractor(client=client, model="test-model")

    with pytest.raises(RuntimeError, match="invalid structured output") as exc_info:
        extractor.extract(
            document=_document(),
            node=_node(),
            material_field="effective_supply_concentration",
            query="qualified effective supply",
        )

    assert exc_info.value.__cause__ is None


def test_openai_compatible_extractor_repairs_one_contract_violation():
    """SELECT INVARIANT: one model retry may repair output, but code never rewrites ownership."""
    quote = "Qualified supply grew only five percent year over year."
    invalid = {
        "spans": [
            {
                "exact_quote": quote,
                "claim_type": "source_fact",
                "statement": quote,
                "stance": "context_only",
                "limitations": "No industry-wide denominator.",
                "location": "Capacity section",
                "primary_scoring_dimension": "effective_supply_concentration",
                "scoring_use": "context_only",
                "data_as_of_date": "2026-06-30",
            }
        ]
    }
    repaired = {
        "spans": [
            {
                **invalid["spans"][0],
                "primary_scoring_dimension": None,
            }
        ]
    }
    client = SequenceLLMClient([invalid, repaired])
    extractor = OpenAICompatibleEvidenceSpanExtractor(client=client, model="test-model")

    spans = extractor.extract(
        document=_document(),
        node=_node(),
        material_field="effective_supply_concentration",
        query="qualified effective supply",
    )

    assert spans[0].scoring_use == "context_only"
    assert spans[0].primary_scoring_dimension is None
    assert len(client.chat.completions.calls) == 2
    repair_messages = client.chat.completions.calls[1]["messages"]
    assert "floor_only or context_only" in repair_messages[-1]["content"]


def test_theme_provider_selector_pins_deepseek_without_copying_its_key(
    monkeypatch,
):
    """SELECT INVARIANT: an explicit DeepSeek pin bypasses a configured OpenAI key."""
    captured = {}

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=RecordingOpenAI))
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("THEME_CHOKEPOINT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("THEME_CHOKEPOINT_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-must-not-be-selected")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-selected-key")

    extractor = OpenAICompatibleEvidenceSpanExtractor()

    assert extractor.model_version == "deepseek-chat"
    assert captured["api_key"] == "deepseek-selected-key"
    assert captured["base_url"] == "https://api.deepseek.com"


def test_extractor_drops_still_invalid_spans_after_repair_without_rewriting_them():
    """SELECT INVARIANT: fail closed per span; never sacrifice valid evidence to bad context."""
    quote = "Qualified supply grew only five percent year over year."
    valid = {
        "exact_quote": quote,
        "claim_type": "source_fact",
        "statement": quote,
        "stance": "supports",
        "limitations": "No industry-wide denominator.",
        "location": "Capacity section",
        "primary_scoring_dimension": "effective_supply_concentration",
        "scoring_use": "primary",
        "data_as_of_date": "2026-06-30",
    }
    invalid_context = {
        **valid,
        "stance": "context_only",
        "scoring_use": "context_only",
    }
    client = FakeLLMClient({"spans": [valid, invalid_context]})
    extractor = OpenAICompatibleEvidenceSpanExtractor(client=client, model="test-model")

    spans = extractor.extract(
        document=_document(),
        node=_node(),
        material_field="effective_supply_concentration",
        query="qualified effective supply",
    )

    assert len(spans) == 1
    assert spans[0].scoring_use == "primary"
    assert spans[0].primary_scoring_dimension == "effective_supply_concentration"
    assert len(client.chat.completions.calls) == 2
