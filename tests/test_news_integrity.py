import os
from datetime import datetime, timezone

import pytest

from event_collector.article_content import (
    CONTENT_VALIDATOR_VERSION,
    ArticleFetchError,
    FetchFailureReason,
    FetchResult,
    validate_extracted_article,
    validate_title_content_alignment,
)
from event_collector.event_collection import NewsCollector, RawEventInput
from event_collector.news_ingestion import ingest_raw_inputs
from event_collector.news_storage import SQLiteNewsStore, compute_content_sha256
from event_collector.news_storage import NewsArticle
from event_collector.vector_store import ChromaVectorStore


PUBLISHED_AT = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
ARTICLE_TEXT = (
    "Microsoft reported strong Azure cloud revenue growth and expanded data-center capacity. "
    "Executives said enterprise demand remained durable while operating margins improved. "
) * 4


class FakeFetcher:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        result = self.results[url]
        if isinstance(result, Exception):
            raise result
        return result


class RecordingSummarizer:
    def __init__(self):
        self.calls = []

    def summarize_article(self, article):
        self.calls.append(article)
        return "- Verified summary"


class RecordingVectorStore:
    def __init__(self):
        self.calls = []

    def add_article(self, article_id, article):
        self.calls.append((article_id, article.content_sha256))
        return [f"{article_id}:0"]

    def delete_article(self, article_id):
        del article_id

    def search(self, query, top_k=5, *, allowed_article_ids=None):
        del query, top_k, allowed_article_ids
        return []


@pytest.fixture
def storage(tmp_path):
    store = SQLiteNewsStore(db_path=str(tmp_path / "news.db"))
    store.init_db()
    try:
        yield store
    finally:
        store.close()


def test_newsapi_response_must_report_ok_status(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "error", "code": "rateLimited", "message": "quota exceeded"}

    monkeypatch.setattr("event_collector.event_collection.requests.get", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="rateLimited"):
        NewsCollector()._get_json("https://newsapi.org/v2/everything", {})


def test_newsapi_parser_preserves_publisher_and_rejects_removed_rows():
    rows = NewsCollector()._build_raw_inputs(
        {
            "status": "ok",
            "articles": [
                {
                    "source": {"id": "reuters", "name": "Reuters"},
                    "title": "Microsoft cloud revenue rises",
                    "description": "Azure demand remains durable.",
                    "url": "https://reuters.example/microsoft-cloud",
                    "publishedAt": "2026-08-15T12:00:00Z",
                },
                {"title": "[Removed]", "url": "https://example.com/removed"},
                {"title": "Invalid URL", "url": "javascript:alert(1)"},
            ],
        }
    )

    assert len(rows) == 1
    assert rows[0].publisher_source_id == "reuters"
    assert rows[0].publisher_source_name == "Reuters"


@pytest.mark.parametrize(
    ("content", "content_type", "reason"),
    [
        ("A" * 500, "application/json", FetchFailureReason.INVALID_CONTENT_TYPE),
        (
            "JavaScript is disabled in your browser. Please enable JavaScript to proceed. " * 5,
            "text/html",
            FetchFailureReason.ERROR_PAGE_SUSPECTED,
        ),
        ("Brief update", "text/html", FetchFailureReason.CONTENT_TOO_SHORT),
    ],
)
def test_article_validator_fails_closed(content, content_type, reason):
    with pytest.raises(ArticleFetchError) as error:
        validate_extracted_article(
            content,
            content_type=content_type,
            status_code=200,
            url="https://publisher.example/story",
        )

    assert error.value.reason == reason


def test_title_content_alignment_rejects_unrelated_success_page():
    with pytest.raises(ArticleFetchError) as error:
        validate_title_content_alignment(
            "Microsoft Azure revenue accelerates sharply",
            "Enterprise cloud demand remained strong",
            "Cookie settings privacy preferences advertising choices account login " * 20,
            url="https://publisher.example/story",
        )

    assert error.value.reason == FetchFailureReason.TITLE_CONTENT_MISMATCH


def test_title_content_alignment_rejects_when_source_metadata_cannot_prove_relevance():
    with pytest.raises(ArticleFetchError) as error:
        validate_title_content_alignment(
            "AI update",
            "",
            "Cookie settings privacy preferences advertising choices account login " * 20,
            url="https://publisher.example/story",
        )

    assert error.value.reason == FetchFailureReason.TITLE_CONTENT_MISMATCH


def test_cross_source_exact_content_groups_story_without_merging_articles(storage):
    ids = []
    for source_id, source_name, url, title in (
        ("reuters", "Reuters", "https://reuters.example/story", "Microsoft cloud revenue rises"),
        ("partner", "Partner News", "https://partner.example/syndicated", "Azure growth remains strong"),
    ):
        article_id, created = storage.create_or_get_article_reference(
            source="news",
            title=title,
            description="Azure demand remains durable.",
            original_url=url,
            published_at=PUBLISHED_AT,
            publisher_source_id=source_id,
            publisher_source_name=source_name,
        )
        assert created is True
        storage.update_article_content(
            article_id,
            content=ARTICLE_TEXT,
            title=title,
            description="Azure demand remains durable.",
            original_url=url,
            canonical_url=url,
            published_at=PUBLISHED_AT,
            validation_status="verified",
            validator_version=CONTENT_VALIDATOR_VERSION,
            response_status_code=200,
            response_content_type="text/html",
            extractor_version="trafilatura-v1",
        )
        group_id, _ = storage.assign_story_group(
            article_id,
            title=title,
            published_at=PUBLISHED_AT,
            content_sha256=compute_content_sha256(ARTICLE_TEXT),
        )
        ids.append((article_id, group_id))

    assert ids[0][0] != ids[1][0]
    assert ids[0][1] == ids[1][1]
    articles = storage.list_articles()
    assert {article.publisher_source_name for article in articles} == {"Reuters", "Partner News"}


def test_new_content_path_is_relative_in_db_and_hash_verified_on_read(storage):
    article_id, _ = storage.create_or_get_article_reference(
        source="news",
        title="Microsoft cloud revenue rises",
        description="Azure demand remains durable.",
        original_url="https://publisher.example/story",
        published_at=PUBLISHED_AT,
    )
    storage.update_article_content(
        article_id,
        content=ARTICLE_TEXT,
        validation_status="verified",
        validator_version=CONTENT_VALIDATOR_VERSION,
    )

    row = storage.conn.execute(
        "SELECT content_path, active_content_sha256 FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert not os.path.isabs(row["content_path"])
    assert row["active_content_sha256"] == compute_content_sha256(ARTICLE_TEXT)
    article = storage.get_article(article_id)
    assert article.content == ARTICLE_TEXT
    assert article.content_status == "ready"

    with open(article.content_path, "w", encoding="utf-8") as handle:
        handle.write("tampered")
    corrupted = storage.get_article(article_id)
    assert corrupted.content == ""
    assert corrupted.content_status == "failed"


def test_retrieval_eligibility_rechecks_current_file_and_hash(storage):
    article_ids = []
    for suffix in ("good", "tampered"):
        article_id, _ = storage.create_or_get_article_reference(
            source="news",
            title=f"Microsoft cloud revenue rises {suffix}",
            description="Azure demand remains durable.",
            original_url=f"https://publisher.example/{suffix}",
            published_at=PUBLISHED_AT,
        )
        storage.update_article_content(
            article_id,
            content=ARTICLE_TEXT + suffix,
            validation_status="verified",
            validator_version=CONTENT_VALIDATOR_VERSION,
        )
        content_hash = compute_content_sha256(ARTICLE_TEXT + suffix)
        assert storage.mark_article_index_ready(article_id, content_hash) is True
        article_ids.append(article_id)

    tampered = storage.get_article(article_ids[1])
    with open(tampered.content_path, "w", encoding="utf-8") as handle:
        handle.write("tampered after indexing")

    eligible = storage.list_retrieval_eligible_article_ids()

    assert eligible == {article_ids[0]}


def test_identical_refresh_is_noop_for_summary_and_index(storage):
    url = "https://publisher.example/story?utm_source=newsapi"
    raw_input = RawEventInput(
        source="news",
        raw_text="Microsoft cloud revenue rises. Azure demand remains durable.",
        title="Microsoft cloud revenue rises",
        description="Azure demand remains durable.",
        url=url,
        published_at=PUBLISHED_AT,
        publisher_source_id="publisher",
        publisher_source_name="Publisher",
    )
    fetcher = FakeFetcher(
        {
            url: FetchResult(
                original_url=url,
                canonical_url="https://publisher.example/story",
                content=ARTICLE_TEXT,
            )
        }
    )
    summarizer = RecordingSummarizer()
    vector_store = RecordingVectorStore()

    first = ingest_raw_inputs(
        [raw_input], storage, vector_store, summarizer=summarizer, content_fetcher=fetcher
    )
    second = ingest_raw_inputs(
        [raw_input], storage, vector_store, summarizer=summarizer, content_fetcher=fetcher
    )

    assert first.items[0].unchanged is False
    assert second.items[0].unchanged is True
    assert len(summarizer.calls) == 1
    assert len(vector_store.calls) == 1
    assert storage.count_articles() == 1


@pytest.mark.parametrize("failed_stage", ["summary", "index", "no_index", "legacy_index"])
def test_identical_refresh_completes_missing_derived_stages(storage, failed_stage):
    raw = RawEventInput(source="manual", raw_text=ARTICLE_TEXT,
                        title="Microsoft cloud revenue rises", url="manual://retry")

    class RetrySummarizer(RecordingSummarizer):
        def summarize_article(self, article):
            summary = super().summarize_article(article)
            if failed_stage == "summary" and len(self.calls) == 1:
                raise RuntimeError("temporary summary failure")
            return summary

    class RetryIndex(RecordingVectorStore):
        def add_article(self, article_id, article):
            chunks = super().add_article(article_id, article)
            if failed_stage == "index" and len(self.calls) == 1:
                raise RuntimeError("temporary index failure")
            return chunks

    summarizer, index = RetrySummarizer(), RetryIndex()
    first = ingest_raw_inputs([raw], storage, None if failed_stage in {"no_index", "legacy_index"} else index,
                              summarizer=summarizer)
    if failed_stage == "legacy_index":
        storage.mark_article_processing_status(first.items[0].article_id, index_status="ready")
    second = ingest_raw_inputs([raw], storage, index, summarizer=summarizer)
    article_id = first.items[0].article_id

    assert second.items[0].summary_status == "ready"
    assert second.items[0].index_status == "ready"
    assert second.items[0].failure_reason is None
    assert article_id in storage.list_retrieval_eligible_article_ids()
    assert len(summarizer.calls) == (2 if failed_stage == "summary" else 1)
    # A regenerated summary must also refresh vector metadata.
    assert len(index.calls) == (1 if failed_stage in {"no_index", "legacy_index"} else 2)


@pytest.mark.parametrize("changed_during", ["summary", "index", "index_failure"])
def test_stale_processing_cannot_publish_success_for_new_content(storage, changed_during):
    from event_collector.article_processing import process_article

    raw = RawEventInput(source="manual", raw_text=ARTICLE_TEXT,
                        title="Microsoft cloud revenue rises", url="manual://version")
    first = ingest_raw_inputs([raw], storage, summarizer=RecordingSummarizer())
    article_id = first.items[0].article_id
    updated_text = ARTICLE_TEXT + "A new revision."

    def change_content():
        storage.update_article_content(article_id, content=updated_text, validation_status="not_applicable")

    def summarize(article):
        if changed_during == "summary":
            change_content()
        return "old version summary"

    class Index(RecordingVectorStore):
        def add_article(self, article_id, article):
            if changed_during.startswith("index"):
                change_content()
            if changed_during == "index_failure":
                raise RuntimeError("old index failed")
            return super().add_article(article_id, article)

    result = process_article(storage, article_id, Index(), summarize=summarize, force_summary=True)
    current = storage.get_article_record(article_id)
    assert result.error is not None
    assert result.indexed is False
    assert current.article.content == updated_text
    assert current.summary_status == "pending"
    assert current.index_status == "pending"
    assert current.article.summary is None
    assert article_id not in storage.list_retrieval_eligible_article_ids()


@pytest.mark.parametrize("invalid", ["rejected", "missing", "tampered"])
def test_processing_never_uses_invalid_canonical_content(storage, invalid):
    from event_collector.article_processing import process_article

    first = ingest_raw_inputs(
        [RawEventInput(source="manual", raw_text=ARTICLE_TEXT, url="manual://invalid")],
        storage, summarizer=RecordingSummarizer(),
    )
    article_id = first.items[0].article_id
    article = storage.get_article(article_id)
    if invalid == "rejected":
        storage.mark_article_content_failure(article_id, reason="rejected")
    elif invalid == "missing":
        os.remove(article.content_path)
    else:
        with open(article.content_path, "w", encoding="utf-8") as handle:
            handle.write("tampered")
    summarizer, index = RecordingSummarizer(), RecordingVectorStore()
    result = process_article(storage, article_id, index,
                             summarize=summarizer.summarize_article, force_summary=True)
    assert result.error is not None
    assert summarizer.calls == []
    assert index.calls == []
    assert article_id not in storage.list_retrieval_eligible_article_ids()


def test_summary_refresh_without_index_leaves_metadata_rebuild_pending(storage):
    from event_collector.summarization import summarize_stored_articles

    raw = RawEventInput(source="manual", raw_text=ARTICLE_TEXT, url="manual://summary-version")
    index = RecordingVectorStore()
    first = ingest_raw_inputs([raw], storage, index, summarizer=RecordingSummarizer())
    article_id = first.items[0].article_id
    summarize_stored_articles(storage, summarizer=RecordingSummarizer(), force=True)
    assert storage.get_article_record(article_id).index_status == "pending"
    assert article_id not in storage.list_retrieval_eligible_article_ids()
    ingest_raw_inputs([raw], storage, index, summarizer=RecordingSummarizer())
    assert article_id in storage.list_retrieval_eligible_article_ids()
    assert len(index.calls) == 2


def test_rejected_content_persists_reason_and_never_reaches_derived_stages(storage):
    url = "https://publisher.example/interstitial"
    fetcher = FakeFetcher(
        {
            url: ArticleFetchError(
                FetchFailureReason.ERROR_PAGE_SUSPECTED,
                "browser interstitial",
                status_code=200,
                url=url,
            )
        }
    )
    summarizer = RecordingSummarizer()
    vector_store = RecordingVectorStore()

    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Microsoft cloud revenue rises. Azure demand remains durable.",
                title="Microsoft cloud revenue rises",
                description="Azure demand remains durable.",
                url=url,
                published_at=PUBLISHED_AT,
            )
        ],
        storage,
        vector_store,
        summarizer=summarizer,
        content_fetcher=fetcher,
    )

    article = storage.get_article(result.items[0].article_id)
    assert result.items[0].failure_reason == "error_page_suspected"
    assert article.content_status == "failed"
    assert article.content_validation_status == "rejected"
    assert article.content_validation_reason == "error_page_suspected"
    assert summarizer.calls == []
    assert vector_store.calls == []


def test_verified_content_persists_auditable_validation_evidence(storage):
    url = "https://publisher.example/verified-story"
    result = ingest_raw_inputs(
        [
            RawEventInput(
                source="news",
                raw_text="Microsoft cloud revenue rises. Azure demand remains durable.",
                title="Microsoft cloud revenue rises",
                description="Azure demand remains durable.",
                url=url,
                published_at=PUBLISHED_AT,
            )
        ],
        storage,
        RecordingVectorStore(),
        summarizer=RecordingSummarizer(),
        content_fetcher=FakeFetcher(
            {
                url: FetchResult(
                    original_url=url,
                    canonical_url=url,
                    content=ARTICLE_TEXT,
                    status_code=200,
                    content_type="text/html",
                    extractor_version="trafilatura-v1",
                    validation_version=CONTENT_VALIDATOR_VERSION,
                )
            }
        ),
    )

    row = storage.conn.execute(
        """
        SELECT content_validation_status, content_validator_version,
               extracted_char_count, response_status_code,
               response_content_type, extractor_version
        FROM articles WHERE id = ?
        """,
        (result.items[0].article_id,),
    ).fetchone()
    assert dict(row) == {
        "content_validation_status": "verified",
        "content_validator_version": CONTENT_VALIDATOR_VERSION,
        "extracted_char_count": len(" ".join(ARTICLE_TEXT.split()).strip()),
        "response_status_code": 200,
        "response_content_type": "text/html",
        "extractor_version": "trafilatura-v1",
    }


def test_canonical_redirect_alias_reuses_existing_article_identity(storage):
    canonical = "https://publisher.example/story"
    first_url = "https://news.example/redirect-one"
    second_url = "https://news.example/redirect-two"
    fetcher = FakeFetcher(
        {
            first_url: FetchResult(first_url, canonical, ARTICLE_TEXT),
            second_url: FetchResult(second_url, canonical, ARTICLE_TEXT),
        }
    )
    summarizer = RecordingSummarizer()
    vector_store = RecordingVectorStore()

    def raw(url):
        return RawEventInput(
            source="news",
            raw_text="Microsoft cloud revenue rises. Azure demand remains durable.",
            title="Microsoft cloud revenue rises",
            description="Azure demand remains durable.",
            url=url,
            published_at=PUBLISHED_AT,
        )

    first = ingest_raw_inputs(
        [raw(first_url)], storage, vector_store, summarizer=summarizer, content_fetcher=fetcher
    )
    second = ingest_raw_inputs(
        [raw(second_url)], storage, vector_store, summarizer=summarizer, content_fetcher=fetcher
    )

    assert first.items[0].article_id == second.items[0].article_id
    assert second.items[0].created is False
    assert second.items[0].unchanged is True
    assert storage.count_articles() == 1
    aliases = storage.conn.execute(
        "SELECT normalized_url, article_id FROM article_url_aliases ORDER BY normalized_url"
    ).fetchall()
    assert {row["normalized_url"] for row in aliases} == {canonical, first_url, second_url}
    assert {row["article_id"] for row in aliases} == {first.items[0].article_id}


def test_vector_search_collapses_story_group_and_keeps_source_provenance():
    class Encoded(list):
        def tolist(self):
            return list(self)

    class Embedder:
        def encode(self, value):
            del value
            return Encoded([0.1, 0.2])

    class Collection:
        def query(self, **kwargs):
            del kwargs
            return {
                "ids": [["10:0", "11:0"]],
                "distances": [[0.1, 0.2]],
                "documents": [["Reuters text", "Partner text"]],
                "metadatas": [[
                    {
                        "article_id": 10,
                        "story_group_id": 7,
                        "title": "Microsoft cloud revenue rises",
                        "canonical_url": "https://reuters.example/story",
                        "publisher_source_id": "reuters",
                        "publisher_source_name": "Reuters",
                    },
                    {
                        "article_id": 11,
                        "story_group_id": 7,
                        "title": "Azure growth remains strong",
                        "canonical_url": "https://partner.example/story",
                        "publisher_source_id": "partner",
                        "publisher_source_name": "Partner News",
                    },
                ]],
            }

    store = object.__new__(ChromaVectorStore)
    store.embedder = Embedder()
    store.collection = Collection()
    store.max_matched_chunks = 2

    results = store.search("Microsoft cloud", top_k=5)

    assert len(results) == 1
    assert results[0]["story_group_id"] == 7
    assert results[0]["duplicate_article_ids"] == [10, 11]
    assert {source["source_name"] for source in results[0]["publisher_sources"]} == {
        "Reuters",
        "Partner News",
    }


def test_vector_index_rejects_news_content_that_was_not_verified():
    class Collection:
        def __init__(self):
            self.upsert_calls = []

        def delete(self, **kwargs):
            del kwargs

        def get(self, include=None):
            del include
            return {"ids": [], "metadatas": []}

        def upsert(self, **kwargs):
            self.upsert_calls.append(kwargs)

    store = object.__new__(ChromaVectorStore)
    store.collection = Collection()
    store.chunk_size = 100
    store.chunk_overlap = 10
    store.embedder = None
    article = NewsArticle(
        source="news",
        title="Microsoft cloud revenue rises",
        description="Azure demand remains durable.",
        published_at=PUBLISHED_AT,
        content=ARTICLE_TEXT,
        url="https://publisher.example/story",
        content_sha256=compute_content_sha256(ARTICLE_TEXT),
        content_status="ready",
        content_validation_status="rejected",
    )

    with pytest.raises(ValueError, match="verified"):
        store.add_article(10, article)

    assert store.collection.upsert_calls == []


@pytest.mark.parametrize(
    ("content_status", "content_sha256", "message"),
    [
        ("failed", compute_content_sha256(ARTICLE_TEXT), "ready"),
        ("ready", "not-the-real-hash", "hash"),
    ],
)
def test_vector_index_requires_ready_content_with_matching_hash(
    content_status, content_sha256, message
):
    class Encoded(list):
        def tolist(self):
            return list(self)

    class Embedder:
        def encode(self, values):
            return Encoded([[float(index)] for index, _ in enumerate(values)])

    class Collection:
        def __init__(self):
            self.upsert_calls = []

        def delete(self, **kwargs):
            del kwargs

        def get(self, include=None):
            del include
            return {"ids": [], "metadatas": []}

        def upsert(self, **kwargs):
            self.upsert_calls.append(kwargs)

    store = object.__new__(ChromaVectorStore)
    store.collection = Collection()
    store.chunk_size = 100
    store.chunk_overlap = 10
    store.embedder = Embedder()
    article = NewsArticle(
        source="news",
        title="Microsoft cloud revenue rises",
        description="Azure demand remains durable.",
        published_at=PUBLISHED_AT,
        content=ARTICLE_TEXT,
        url="https://publisher.example/story",
        content_sha256=content_sha256,
        content_status=content_status,
        content_validation_status="verified",
    )

    with pytest.raises(ValueError, match=message):
        store.add_article(10, article)

    assert store.collection.upsert_calls == []


def test_vector_search_filters_stale_unverified_news_records():
    class Encoded(list):
        def tolist(self):
            return list(self)

    class Embedder:
        def encode(self, value):
            del value
            return Encoded([0.1, 0.2])

    class Collection:
        def query(self, **kwargs):
            del kwargs
            return {
                "ids": [["20:0", "21:0"]],
                "distances": [[0.1, 0.2]],
                "documents": [["Rejected stale text", "Verified article text"]],
                "metadatas": [[
                    {
                        "article_id": 20,
                        "story_group_id": 20,
                        "source": "news",
                        "title": "Rejected stale article",
                        "content_validation_status": "rejected",
                        "content_sha256": "rejected-hash",
                    },
                    {
                        "article_id": 21,
                        "story_group_id": 21,
                        "source": "news",
                        "title": "Verified article",
                        "content_validation_status": "verified",
                        "content_sha256": "verified-hash",
                    },
                ]],
            }

    store = object.__new__(ChromaVectorStore)
    store.embedder = Embedder()
    store.collection = Collection()
    store.max_matched_chunks = 2

    results = store.search("cloud revenue", top_k=5)

    assert [result["article_id"] for result in results] == [21]


def test_vector_search_applies_live_eligibility_provider_before_query():
    class Encoded(list):
        def tolist(self):
            return list(self)

    class Embedder:
        def encode(self, value):
            del value
            return Encoded([0.1, 0.2])

    class Collection:
        def __init__(self):
            self.query_kwargs = None

        def query(self, **kwargs):
            self.query_kwargs = kwargs
            return {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}

    store = object.__new__(ChromaVectorStore)
    store.embedder = Embedder()
    store.collection = Collection()
    store.max_matched_chunks = 2
    store.eligible_article_ids_provider = lambda: {21}

    assert store.search("cloud revenue", top_k=5) == []
    assert store.collection.query_kwargs["where"] == {"article_id": {"$in": [21]}}
