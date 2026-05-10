"""Shared OpenAI structured-output client helpers."""

from __future__ import annotations

import os
from typing import Any

from event_collector.errors import MissingOpenAIKeyError


class OpenAIStructuredOutputClient:
    """Template base for OpenAI clients that return structured outputs."""

    DEFAULT_MODEL = ""
    MODEL_ENV_VAR = "OPENAI_MODEL"
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required"
    REFUSAL_ERROR_PREFIX = "OpenAI refused request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed response"

    def __init__(self, model: str | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingOpenAIKeyError(self.MISSING_KEY_MESSAGE)

        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = self._resolve_model(model)

    def parse_structured_output(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_format: Any,
    ) -> Any:
        completion = self.client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=response_format,
        )
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError(f"{self.REFUSAL_ERROR_PREFIX}: {message.refusal}")
        if message.parsed is None:
            raise RuntimeError(self.EMPTY_RESPONSE_MESSAGE)
        return message.parsed

    def _resolve_model(self, model: str | None) -> str:
        if model:
            return model
        if self.MODEL_ENV_VAR == "OPENAI_MODEL":
            return os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL
        env_model = os.getenv(self.MODEL_ENV_VAR) if self.MODEL_ENV_VAR else None
        return env_model or os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL
