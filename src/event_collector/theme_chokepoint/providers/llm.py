"""OpenAI-compatible structured extractors for Theme Chokepoint evidence."""

from __future__ import annotations

from datetime import date
import json
import os
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from event_collector.theme_chokepoint.contracts import ExtractedEvidenceSpan


EVIDENCE_SPAN_PROMPT_VERSION = "theme-chokepoint-evidence-span-v1"


class _EvidenceSpanPayload(BaseModel):
    exact_quote: str = Field(min_length=1)
    claim_type: Literal[
        "source_fact",
        "normalized_fact",
        "model_inference",
        "analyst_judgment",
        "open_question",
    ]
    statement: str = Field(min_length=1)
    stance: Literal["supports", "contradicts", "context_only"]
    limitations: str = Field(min_length=1)
    location: str = Field(min_length=1)
    primary_scoring_dimension: str | None = None
    scoring_use: Literal["primary", "floor_only", "context_only"]
    data_as_of_date: date | None = None
    origin_event_key: str | None = None


class _EvidenceSpanResponse(BaseModel):
    spans: list[_EvidenceSpanPayload]


class OpenAICompatibleEvidenceSpanExtractor:
    prompt_version = EVIDENCE_SPAN_PROMPT_VERSION

    def __init__(
        self,
        *,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        max_document_chars: int = 60_000,
    ):
        if max_document_chars <= 0 or timeout_seconds <= 0:
            raise ValueError("LLM evidence extraction budgets must be positive")
        self.model = model or _configured_model()
        if not self.model:
            raise RuntimeError(
                "THEME_CHOKEPOINT_LLM_MODEL, OPENAI_MODEL or DEEPSEEK_STRUCTURING_MODEL is required"
            )
        if client is None:
            resolved_key, provider = _configured_api_key(api_key)
            if not resolved_key:
                raise RuntimeError(
                    "THEME_CHOKEPOINT_LLM_API_KEY, OPENAI_API_KEY or DEEPSEEK_API_KEY is required"
                )
            from openai import OpenAI

            kwargs = {"api_key": resolved_key, "timeout": timeout_seconds}
            resolved_base = base_url or _configured_base_url(provider)
            if resolved_base:
                kwargs["base_url"] = resolved_base
            client = OpenAI(**kwargs)
        self.client = client
        self.max_document_chars = max_document_chars

    @property
    def model_version(self) -> str:
        return self.model

    def extract(self, *, document, node, material_field, query):
        content = document.text[: self.max_document_chars]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _format_request(
                    document=document,
                    content=content,
                    node=node,
                    material_field=material_field,
                    query=query,
                ),
            },
        ]
        last_error: RuntimeError | ValueError = RuntimeError(
            "evidence extractor returned invalid structured output"
        )
        repair_violation = None
        for attempt in range(2):
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            message = completion.choices[0].message
            raw_content = (getattr(message, "content", "") or "").strip()
            if not raw_content:
                last_error = RuntimeError("evidence extractor returned an empty response")
            else:
                try:
                    response = _EvidenceSpanResponse.model_validate(json.loads(raw_content))
                except ValidationError as error:
                    repair_violation = ",".join(
                        f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                        for item in error.errors()
                    )
                    last_error = RuntimeError(
                        "evidence extractor returned invalid structured output"
                    )
                except (json.JSONDecodeError, ValueError):
                    repair_violation = None
                    last_error = RuntimeError(
                        "evidence extractor returned invalid structured output"
                    )
                else:
                    try:
                        return _validated_spans(
                            response,
                            content,
                            material_field,
                            drop_invalid=attempt > 0,
                        )
                    except ValueError as error:
                        repair_violation = None
                        last_error = error
            if attempt == 0:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": _repair_instruction(
                            last_error, violation=repair_violation
                        ),
                    },
                ]
        raise last_error from None


_SYSTEM_PROMPT = """
You extract audit-grade evidence spans for a supply-chain chokepoint research system.

Return only JSON with exactly this shape. Every span must include every field, using null where
the schema permits null:
{
  "spans": [
    {
      "exact_quote": "one contiguous verbatim substring from ORIGINAL_TEXT",
      "claim_type": "source_fact | normalized_fact | model_inference | analyst_judgment | open_question",
      "statement": "concise statement supported by the quote",
      "stance": "supports | contradicts | context_only",
      "limitations": "material source and inference limitations",
      "location": "page, section, heading, or other reproducible location",
      "primary_scoring_dimension": "requested field name or null",
      "scoring_use": "primary | floor_only | context_only",
      "data_as_of_date": "YYYY-MM-DD or null",
      "origin_event_key": "explicit issuer/source event identifier or null"
    }
  ]
}
Return an empty spans array when the supplied original document does not directly support,
contradict, or materially contextualize the requested field.

Rules:
- exact_quote must be one contiguous verbatim substring copied from ORIGINAL_TEXT.
- Never turn a title, search summary, model inference, or missing disclosure into a source fact.
- Missing public evidence remains missing; it is not a zero score or a negative fact.
- A source fact may have only one primary high-grade scoring dimension. Reuse elsewhere must be
  floor_only or context_only.
- If scoring_use is primary, primary_scoring_dimension must equal REQUESTED_FIELD exactly.
- If scoring_use is floor_only or context_only, primary_scoring_dimension must be null or a
  different dimension; it must never equal REQUESTED_FIELD.
- Use supports, contradicts, or context_only for stance.
- Set origin_event_key only when ORIGINAL_TEXT explicitly identifies the originating issuer,
  filing, release, transcript, order, incident, or other source event. Use null when provenance
  is inferred only from similar wording; the runtime will then keep families separate.
- State material limitations, including missing denominator, scope, period, customer, geography,
  qualification status, or whether the statement is forward-looking.
- Do not assign a rating, business state, recommendation, or investment conclusion.
""".strip()


def _format_request(*, document, content, node, material_field, query):
    return (
        f"REQUESTED_FIELD: {material_field}\n"
        f"SEGMENT_ID: {node.node_id}\n"
        f"SEGMENT_NAME: {node.normalized_name}\n"
        f"TARGETED_QUERY: {query}\n"
        f"SOURCE_URL: {document.canonical_url}\n"
        f"SOURCE_TITLE: {document.title}\n"
        f"PUBLICATION_DATE: {document.publication_date or 'unknown'}\n\n"
        f"ORIGINAL_TEXT:\n{content}"
    )


def _configured_api_key(explicit_key):
    selected_provider = _configured_provider()
    if explicit_key:
        return explicit_key, selected_provider or "explicit"
    if os.getenv("THEME_CHOKEPOINT_LLM_API_KEY"):
        return os.getenv("THEME_CHOKEPOINT_LLM_API_KEY"), selected_provider or "theme"
    if selected_provider == "openai":
        return os.getenv("OPENAI_API_KEY"), "openai"
    if selected_provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY"), "deepseek"
    if os.getenv("OPENAI_API_KEY"):
        return os.getenv("OPENAI_API_KEY"), "openai"
    if os.getenv("DEEPSEEK_API_KEY"):
        return os.getenv("DEEPSEEK_API_KEY"), "deepseek"
    return None, None


def _configured_model():
    selected_provider = _configured_provider()
    provider_model = None
    if selected_provider == "openai":
        provider_model = os.getenv("OPENAI_MODEL")
    elif selected_provider == "deepseek":
        provider_model = os.getenv("DEEPSEEK_STRUCTURING_MODEL")
    return (
        os.getenv("THEME_CHOKEPOINT_LLM_MODEL")
        or provider_model
        or os.getenv("OPENAI_MODEL")
        or os.getenv("DEEPSEEK_STRUCTURING_MODEL")
    )


def _configured_base_url(provider):
    if os.getenv("THEME_CHOKEPOINT_LLM_BASE_URL"):
        return os.getenv("THEME_CHOKEPOINT_LLM_BASE_URL")
    if provider == "openai":
        return os.getenv("OPENAI_BASE_URL")
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return None


def _configured_provider():
    value = os.getenv("THEME_CHOKEPOINT_LLM_PROVIDER", "").strip().casefold()
    if not value:
        return None
    if value not in {"openai", "deepseek"}:
        raise RuntimeError("THEME_CHOKEPOINT_LLM_PROVIDER must be openai or deepseek")
    return value


def _validated_spans(response, content, material_field, *, drop_invalid=False):
    result = []
    errors = []
    for item in response.spans:
        try:
            _validate_span(item, content, material_field)
        except ValueError as error:
            errors.append(error)
            if not drop_invalid:
                raise
            continue
        result.append(
            ExtractedEvidenceSpan(
                exact_quote=item.exact_quote,
                claim_type=item.claim_type,
                statement=item.statement.strip(),
                stance=item.stance,
                limitations=item.limitations.strip(),
                location=item.location.strip(),
                primary_scoring_dimension=item.primary_scoring_dimension,
                scoring_use=item.scoring_use,
                data_as_of_date=item.data_as_of_date,
                origin_event_key=(
                    item.origin_event_key.strip() if item.origin_event_key else None
                ),
            )
        )
    if errors and not result:
        raise errors[0]
    return result


def _validate_span(item, content, material_field):
    if item.exact_quote not in content:
        raise ValueError("evidence extractor exact_quote is not verbatim original text")
    if item.scoring_use == "primary" and item.primary_scoring_dimension != material_field:
        raise ValueError("primary evidence must belong to the requested scoring dimension")
    if item.scoring_use != "primary" and item.primary_scoring_dimension == material_field:
        raise ValueError("non-primary reuse cannot claim primary scoring ownership")


def _repair_instruction(error, *, violation=None):
    message = str(error)
    if message == "non-primary reuse cannot claim primary scoring ownership":
        rule = (
            "If scoring_use is floor_only or context_only, primary_scoring_dimension must be "
            "null or a different dimension and must not equal REQUESTED_FIELD."
        )
    elif message == "primary evidence must belong to the requested scoring dimension":
        rule = (
            "If scoring_use is primary, primary_scoring_dimension must equal REQUESTED_FIELD "
            "exactly."
        )
    elif message == "evidence extractor exact_quote is not verbatim original text":
        rule = "Every exact_quote must be one contiguous verbatim substring from ORIGINAL_TEXT."
    else:
        rule = "Every span must contain every field from the required JSON schema."
    schema_detail = f" Schema violation paths: {violation}." if violation else ""
    return (
        "Your previous response violated the evidence contract. "
        f"{rule}{schema_detail} Drop any span you cannot correct. Return a complete corrected JSON object "
        "only; do not explain the correction."
    )
