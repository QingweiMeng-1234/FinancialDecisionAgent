"""Shared OpenAI structured-output client helpers."""

from __future__ import annotations

import os
from typing import Any

from event_collector.errors import MissingAPIKeyError, MissingOpenAIKeyError


class OpenAIStructuredOutputClient:
    """Template base for OpenAI clients that return structured outputs."""

    API_KEY_ENV_VAR = "OPENAI_API_KEY"
    BASE_URL_ENV_VAR = ""
    DEFAULT_BASE_URL = ""
    DEFAULT_MODEL = ""
    MODEL_ENV_VAR = "OPENAI_MODEL"
    FALLBACK_TO_OPENAI_MODEL = True
    MISSING_KEY_MESSAGE = "OPENAI_API_KEY is required"
    REFUSAL_ERROR_PREFIX = "OpenAI refused request"
    EMPTY_RESPONSE_MESSAGE = "OpenAI returned no parsed response"
    DEFAULT_TIMEOUT_SECONDS = 90.0

    def __init__(self, model: str | None = None):
        api_key = os.getenv(self.API_KEY_ENV_VAR)
        if not api_key:
            if self.API_KEY_ENV_VAR == "OPENAI_API_KEY":
                raise MissingOpenAIKeyError(self.MISSING_KEY_MESSAGE)
            raise MissingAPIKeyError(self.MISSING_KEY_MESSAGE)

        from openai import OpenAI

        client_kwargs = {"api_key": api_key}
        base_url = self._resolve_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        timeout_seconds = self._resolve_timeout_seconds()
        if timeout_seconds is not None:
            client_kwargs["timeout"] = timeout_seconds
        self.client = OpenAI(**client_kwargs)
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
        if env_model:
            return env_model
        if self.FALLBACK_TO_OPENAI_MODEL:
            return os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL
        return self.DEFAULT_MODEL

    def _resolve_base_url(self) -> str:
        if self.BASE_URL_ENV_VAR:
            env_base_url = os.getenv(self.BASE_URL_ENV_VAR)
            if env_base_url:
                return env_base_url
        return self.DEFAULT_BASE_URL

    def _resolve_timeout_seconds(self) -> float | None:
        raw_value = os.getenv("LLM_TIMEOUT_SECONDS")
        if raw_value is None or not raw_value.strip():
            return self.DEFAULT_TIMEOUT_SECONDS
        try:
            timeout_seconds = float(raw_value)
        except ValueError:
            return self.DEFAULT_TIMEOUT_SECONDS
        if timeout_seconds <= 0:
            return None
        return timeout_seconds
