import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from event_collector.news_storage import NewsArticle, SQLiteNewsStore
from event_collector.summarization import (
    ArticleForSummarization,
    ArticleSummarizationError,
    SummarizationAgent,
    summarize_stored_articles,
)
from event_collector.vector_store import ChromaVectorStore


class FakeSummarizationClient:
    def __init__(self, responses=None, error=None):
        self.responses = responses or []
        self.error = error
        self.calls = []

    def summarize_article(self, article):
        self.calls.append(article)
        if self.error:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return {
            "bullets": [
                "Main event happened.",
                "Named entities were involved.",
                "The article included concrete numbers.",
            ]
        }


@pytest.fixture
def article():
    return ArticleForSummarization(
        article_id=101,
        title="Nvidia launches new AI chips",
        description="The company announced a new product cycle for data centers.",
        content=(
            "Nvidia introduced a new family of AI chips for data centers, "
            "targeting faster training and inference workloads."
        ),
        url="https://example.com/nvda",
    )


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteNewsStore(db_path=os.path.join(tmpdir, "news.db"))
        store.init_db()
        yield store
        store.close()


@pytest.fixture
def temp_chroma_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_summarization_agent_returns_bullet_string(article):
    agent = SummarizationAgent(
        llm_client=FakeSummarizationClient(
            responses=[
                {
                    "bullets": [
                        "Nvidia launched new AI chips for data centers.",
                        "The announcement focused on faster training and inference.",
                        "The release signals a new product cycle.",
                    ]
                }
            ]
        )
    )

    summary = agent.summarize_article(article)

    assert summary == (
        "- Nvidia launched new AI chips for data centers.\n"
        "- The announcement focused on faster training and inference.\n"
        "- The release signals a new product cycle."
    )


def test_summarization_agent_rejects_malformed_output(article):
    agent = SummarizationAgent(
        llm_client=FakeSummarizationClient(
            responses=[
                {
                    "bullets": [
                        "Only one bullet.",
                        "Still not enough.",
                    ]
                }
            ]
        )
    )

    with pytest.raises(ValidationError):
        agent.summarize_article(article)


def test_summarization_agent_calls_client_with_article(article):
    client = FakeSummarizationClient()
    agent = SummarizationAgent(llm_client=client)

    agent.summarize_article(article)

    assert client.calls == [article]


def test_summarize_stored_articles_skips_existing_summary_unless_force(storage):
    recent = datetime.now()
    storage.save_article(
        NewsArticle(
            source="news",
            title="Already summarized",
            description="Ready",
            content="This article already has a summary stored in SQLite.",
            url="https://example.com/already",
            published_at=recent,
            summary="- Existing bullet 1\n- Existing bullet 2\n- Existing bullet 3",
        )
    )
    missing_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Needs summary",
            description="Pending",
            content="This article needs a generated summary before indexing.",
            url="https://example.com/needs",
            published_at=recent - timedelta(seconds=1),
            summary=None,
        )
    )

    stats = summarize_stored_articles(
        storage=storage,
        summarizer=SummarizationAgent(
            llm_client=FakeSummarizationClient(
                responses=[
                    {
                        "bullets": [
                            "The article still needs a summary.",
                            "It will be processed in default mode.",
                            "Existing summaries are skipped.",
                        ]
                    }
                ]
            )
        ),
        force=False,
    )

    assert stats["processed"] == 1
    assert stats["skipped"] == 1
    assert storage.get_article(missing_id).summary.startswith("- The article still needs a summary.")


def test_summarize_stored_articles_force_overwrites_summary(storage):
    article_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Rewrite summary",
            description="Force mode",
            content="This article summary will be regenerated in force mode.",
            url="https://example.com/rewrite",
            published_at=datetime.now(),
            summary="- Old bullet 1\n- Old bullet 2\n- Old bullet 3",
        )
    )

    stats = summarize_stored_articles(
        storage=storage,
        summarizer=SummarizationAgent(
            llm_client=FakeSummarizationClient(
                responses=[
                    {
                        "bullets": [
                            "Force mode regenerated the summary.",
                            "The previous bullets were replaced.",
                            "The stored text now reflects the new output.",
                        ]
                    }
                ]
            )
        ),
        force=True,
    )

    assert stats["processed"] == 1
    assert storage.get_article(article_id).summary.startswith("- Force mode regenerated the summary.")


def test_summarize_stored_articles_stops_on_first_failure(storage):
    first_id = storage.save_article(
        NewsArticle(
            source="news",
            title="First article",
            description="Will fail",
            content="This article should trigger the first failure in the batch run.",
            url="https://example.com/first-failure",
            published_at=datetime.now(),
            summary=None,
        )
    )
    second_id = storage.save_article(
        NewsArticle(
            source="news",
            title="Second article",
            description="Should not run",
            content="This article should not be processed after the first failure.",
            url="https://example.com/second-failure",
            published_at=datetime.now() - timedelta(seconds=1),
            summary=None,
        )
    )

    with pytest.raises(ArticleSummarizationError) as exc_info:
        summarize_stored_articles(
            storage=storage,
            summarizer=SummarizationAgent(
                llm_client=FakeSummarizationClient(error=RuntimeError("summary failed"))
            ),
        )

    assert exc_info.value.article_id == first_id
    assert storage.get_article(first_id).summary is None
    assert storage.get_article(second_id).summary is None


def test_summarize_stored_articles_reindexes_existing_article_cleanly(storage, temp_chroma_dir):
    vector_store = ChromaVectorStore(persist_dir=temp_chroma_dir)
    published_at = datetime.now()
    article = NewsArticle(
        source="news",
        title="Copper gains",
        description="Industrial metals move",
        content="Copper prices gained after supply disruptions intensified.",
        url="https://example.com/copper",
        published_at=published_at,
        summary=None,
    )
    article_id = storage.save_article(article)
    vector_store.add_article(article_id, article)

    stats = summarize_stored_articles(
        storage=storage,
        vector_store=vector_store,
        summarizer=SummarizationAgent(
            llm_client=FakeSummarizationClient(
                responses=[
                    {
                        "bullets": [
                            "Copper prices rose after supply disruptions intensified.",
                            "The move centered on industrial metals supply concerns.",
                            "Commodity traders responded to tighter availability expectations.",
                        ]
                    }
                ]
            )
        ),
    )

    assert stats["processed"] == 1
    assert stats["indexed"] == 1
    assert vector_store.collection.count() == 1
    results = vector_store.search("tighter availability expectations", top_k=1)
    assert results[0]["id"] == str(article_id)
    assert results[0]["summary"].startswith("- Copper prices rose")
    vector_store.client = None


def test_summarize_articles_script_skips_existing_summaries_without_openai_key(storage, temp_chroma_dir):
    storage.save_article(
        NewsArticle(
            source="news",
            title="Already summarized",
            description="No work needed",
            content="This article already has a summary and should be skipped by the CLI.",
            url="https://example.com/cli-skip",
            published_at=datetime.now(),
            summary="- Existing bullet 1\n- Existing bullet 2\n- Existing bullet 3",
        )
    )

    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "summarize_articles.py",
            "--db-path",
            storage.db_path,
            "--persist-dir",
            temp_chroma_dir,
        ],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Articles processed: 0" in result.stdout
    assert "Skipped:            1" in result.stdout
