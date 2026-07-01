"""LLM-backed article summarization for retrieval-friendly news storage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field, field_validator

from event_collector.errors import ArticleSummarizationError
from event_collector.openai_client_base import OpenAIStructuredOutputClient

if TYPE_CHECKING:
    from event_collector.news_storage import ArticleRecord, SQLiteNewsStore
    from event_collector.vector_store import VectorStore


@dataclass
class ArticleForSummarization:
    """Article payload passed to the summarization agent."""

    article_id: int
    title: str
    description: str
    content: str
    url: str


class SummaryResponse(BaseModel):
    bullets: list[str] = Field(min_length=3, max_length=5)

    @field_validator("bullets")
    @classmethod
    def validate_bullets(cls, bullets: list[str]) -> list[str]:
        normalized = []
        for bullet in bullets:
            cleaned = bullet.strip().lstrip("-* ").strip()
            if not cleaned:
                raise ValueError("Summary bullets must be non-empty")
            normalized.append(cleaned)
        return normalized


class SummarizationLLMClient(Protocol):
    def summarize_article(self, article: ArticleForSummarization) -> Any:
        """Return data compatible with SummaryResponse."""
        ...


class OpenAIArticleSummarizationClient(OpenAIStructuredOutputClient):
    """OpenAI structured-output adapter for article summarization."""

    DEFAULT_MODEL = "deepseek-v4-flash"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required for article summarization"
    REFUSAL_ERROR_PREFIX = "OpenAI refused summarization request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed article summary"

    def summarize_article(self, article: ArticleForSummarization) -> SummaryResponse:
        return self.parse_structured_output(
            system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
            user_content=_format_article(article),
            response_format=SummaryResponse,
        )


class DeepSeekArticleSummarizationClient(OpenAIStructuredOutputClient):
    """DeepSeek structured-output adapter for article summarization."""

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL_ENV_VAR = "DEEPSEEK_BASE_URL"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-flash"
    MODEL_ENV_VAR = "DEEPSEEK_SUMMARIZATION_MODEL"
    FALLBACK_TO_OPENAI_MODEL = False
    MISSING_KEY_MESSAGE = "DEEPSEEK_API_KEY is required for article summarization"
    REFUSAL_ERROR_PREFIX = "DeepSeek refused summarization request"
    EMPTY_RESPONSE_MESSAGE = "DeepSeek returned no parsed article summary"

    def summarize_article(self, article: ArticleForSummarization) -> SummaryResponse:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": DEEPSEEK_SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": _format_article(article)},
            ],
            response_format={"type": "json_object"},
        )
        message = completion.choices[0].message
        content = " ".join((getattr(message, "content", "") or "").split()).strip()
        if not content:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return SummaryResponse.model_validate(json.loads(content))


SUMMARIZATION_SYSTEM_PROMPT = """
You are the Article Summarization Agent for a financial news system.

Summarize one article into 3 to 5 factual bullet points for retrieval and downstream reasoning.

Boundaries:
- Return only facts supported by the article.
- Do not recommend BUY, HOLD, or SELL.
- Do not add market opinions not stated in the article.
- Do not rewrite the title or description.
- Focus on the main event, important entities, concrete numbers, and why the article matters.
""".strip()


DEEPSEEK_SUMMARIZATION_SYSTEM_PROMPT = """
You are the Article Summarization Agent for a financial news system.

Return only valid json matching this schema:
{
  "bullets": [
    "bullet 1",
    "bullet 2",
    "bullet 3"
  ]
}

Requirements:
- Summarize one article into 3 to 5 factual bullet points for retrieval and downstream reasoning.
- Return only facts supported by the article.
- Do not recommend BUY, HOLD, or SELL.
- Do not add market opinions not stated in the article.
- Do not rewrite the title or description.
- Focus on the main event, important entities, concrete numbers, and why the article matters.
""".strip()


class SummarizationAgent:
    """Turns article content into a compact factual bullet summary."""

    def __init__(self, llm_client: SummarizationLLMClient | None = None):
        self.llm_client = llm_client or DeepSeekArticleSummarizationClient()

    def summarize_article(self, article: ArticleForSummarization) -> str:
        raw_response = self.llm_client.summarize_article(article)
        response = SummaryResponse.model_validate(raw_response)
        return "\n".join(f"- {bullet}" for bullet in response.bullets)


def summarize_stored_articles(
    storage: "SQLiteNewsStore",
    vector_store: "VectorStore | None" = None,
    summarizer: SummarizationAgent | None = None,
    source: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    """Summarize stored articles and optionally upsert them into the vector store."""
    if force:
        candidate_records = storage.list_article_records(source=source, limit=limit)
        records = candidate_records
        skipped = 0
    else:
        candidate_records = storage.list_article_records(source=source, limit=limit)
        records = storage.list_article_records_missing_summary(source=source, limit=limit)
        skipped = len(candidate_records) - len(records)

    processed = 0
    indexed = 0

    agent = summarizer or SummarizationAgent() if records else None

    for record in records:
        article = ArticleForSummarization(
            article_id=record.id,
            title=record.article.title,
            description=record.article.description,
            content=record.article.content,
            url=record.article.url,
        )

        try:
            summary = agent.summarize_article(article)
            storage.update_article_summary(record.id, summary)
            record.article.summary = summary
            processed += 1

            if vector_store:
                try:
                    vector_store.add_article(record.id, record.article)
                    storage.mark_article_processing_status(record.id, index_status="ready")
                    indexed += 1
                except Exception:
                    storage.mark_article_processing_status(record.id, index_status="failed")
                    raise
        except Exception as exc:
            storage.mark_article_processing_status(record.id, summary_status="failed")
            raise ArticleSummarizationError(record.id, str(exc)) from exc

    return {
        "processed": processed,
        "indexed": indexed,
        "skipped": skipped,
        "total_candidates": len(candidate_records),
    }


def _format_article(article: ArticleForSummarization) -> str:
    return (
        f"Article ID: {article.article_id}\n"
        f"URL: {article.url}\n"
        f"Title: {article.title}\n"
        f"Description: {article.description}\n"
        f"Content:\n{article.content}"
    )
