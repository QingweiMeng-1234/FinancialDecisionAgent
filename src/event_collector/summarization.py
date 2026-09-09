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
    from event_collector.article_processing import needs_article_processing, process_article

    candidate_records = storage.list_article_records(source=source)
    if force:
        records = candidate_records[:limit] if limit is not None else candidate_records
        skipped = 0
    else:
        records = [
            record for record in candidate_records
            if needs_article_processing(record, include_index=vector_store is not None)
        ]
        skipped = len(candidate_records) - len(records)
        if limit is not None:
            records = records[:limit]

    processed = 0
    indexed = 0

    agent = summarizer

    for record in records:
        def summarize(article):
            nonlocal agent
            if agent is None:
                agent = SummarizationAgent()
            return agent.summarize_article(
                ArticleForSummarization(
                    article_id=record.id, title=article.title,
                    description=article.description, content=article.content, url=article.url,
                )
            )

        result = process_article(
            storage, record.id, vector_store, summarize=summarize, force_summary=force,
        )
        if result.error is not None:
            raise ArticleSummarizationError(record.id, str(result.error)) from result.error
        processed += int(result.summarized)
        indexed += int(result.indexed)

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
