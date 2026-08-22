"""DeepSeek structured upstream dependency proposals for Stage 2."""

from __future__ import annotations

import json
import os
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_collector.theme_chokepoint.contracts import DependencyDraft


DEPENDENCY_PROPOSER_PROMPT_VERSION = "theme-chokepoint-dependency-proposer-v1"
_NonEmptyString = Annotated[str, Field(min_length=1)]


class DependencyProposalError(RuntimeError):
    """Stable fail-closed boundary for dependency-provider failures."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _DependencyPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, strict=True
    )

    upstream_name: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: list[_NonEmptyString]
    relation_type: str = Field(min_length=1)
    demand_transmission: str = Field(min_length=1)
    criticality_hypothesis: str = Field(min_length=1)
    substitute_hypothesis: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    verification_questions: list[_NonEmptyString] = Field(min_length=1)
    stop_reason: Literal[
        "non_critical",
        "sufficiently_commoditized",
        "duplicated",
        "outside_scope",
        "unsupported",
    ] | None


class _DependencyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dependencies: list[_DependencyPayload]


class DeepSeekUpstreamDependencyProposer:
    """Propose unverified upstream edges; Stage 3 owns evidence support."""

    prompt_version = DEPENDENCY_PROPOSER_PROMPT_VERSION

    def __init__(
        self,
        *,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_candidates_per_node: int = 6,
        timeout_seconds: float = 60.0,
        max_input_chars: int = 12_000,
    ):
        if (
            max_candidates_per_node <= 0
            or timeout_seconds <= 0
            or max_input_chars <= 0
        ):
            raise ValueError("dependency proposer budgets must be positive")
        self.model = model or os.getenv("THEME_CHOKEPOINT_LLM_MODEL")
        if not self.model:
            raise RuntimeError("THEME_CHOKEPOINT_LLM_MODEL is required")
        if client is None:
            provider = os.getenv("THEME_CHOKEPOINT_LLM_PROVIDER", "").strip().casefold()
            if provider != "deepseek":
                raise RuntimeError(
                    "THEME_CHOKEPOINT_LLM_PROVIDER must be deepseek for dependency proposals"
                )
            resolved_key = (
                api_key
                or os.getenv("THEME_CHOKEPOINT_LLM_API_KEY")
                or os.getenv("DEEPSEEK_API_KEY")
            )
            if not resolved_key:
                raise RuntimeError(
                    "THEME_CHOKEPOINT_LLM_API_KEY or DEEPSEEK_API_KEY is required"
                )
            from openai import OpenAI

            kwargs = {"api_key": resolved_key, "timeout": timeout_seconds}
            resolved_base = (
                base_url
                or os.getenv("THEME_CHOKEPOINT_LLM_BASE_URL")
                or "https://api.deepseek.com"
            )
            kwargs["base_url"] = resolved_base
            client = OpenAI(**kwargs)
        self.client = client
        self.max_candidates_per_node = max_candidates_per_node
        self.timeout_seconds = timeout_seconds
        self.max_input_chars = max_input_chars

    def propose_upstream(self, request, demand_frame, node) -> list[DependencyDraft]:
        user_prompt = _format_request(request, demand_frame, node)
        user_prompt = _truncate_input(user_prompt, self.max_input_chars)
        messages = [
            {
                "role": "system",
                "content": _system_prompt(self.max_candidates_per_node),
            },
            {"role": "user", "content": user_prompt},
        ]
        violation_code = "invalid_response_envelope"
        for attempt in range(2):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append(
                    {"role": "user", "content": _repair_prompt(violation_code)}
                )
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=attempt_messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                    timeout=self.timeout_seconds,
                )
            except Exception:
                raise DependencyProposalError(
                    "request_failed", "dependency proposer request failed"
                ) from None
            try:
                response = _decode_response(completion, self.max_candidates_per_node)
            except DependencyProposalError as error:
                violation_code = error.code
                if attempt == 0:
                    continue
                raise DependencyProposalError(
                    violation_code,
                    "dependency proposer failed structured-output contract "
                    f"after one repair: {violation_code}",
                ) from None
            return [_to_draft(item) for item in response.dependencies]
        raise AssertionError("unreachable dependency proposer repair state")


def _decode_response(completion, max_candidates: int) -> _DependencyResponse:
    try:
        message = completion.choices[0].message
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str) or not content.strip():
            raise DependencyProposalError("empty_response", "empty response")
    except DependencyProposalError:
        raise
    except (AttributeError, IndexError, TypeError):
        raise DependencyProposalError(
            "invalid_response_envelope", "invalid response envelope"
        ) from None
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise DependencyProposalError("invalid_json", "invalid JSON") from None
    try:
        response = _DependencyResponse.model_validate(payload)
    except ValidationError:
        raise DependencyProposalError("schema_violation", "schema violation") from None
    if len(response.dependencies) > max_candidates:
        raise DependencyProposalError(
            "candidate_budget_exceeded", "dependency candidate budget exceeded"
        )
    return response


def _to_draft(item: _DependencyPayload) -> DependencyDraft:
    return DependencyDraft(
        upstream_name=item.upstream_name,
        node_type=item.node_type,
        description=item.description,
        aliases=tuple(item.aliases),
        relation_type=item.relation_type,
        demand_transmission=item.demand_transmission,
        criticality_hypothesis=item.criticality_hypothesis,
        substitute_hypothesis=item.substitute_hypothesis,
        confidence=item.confidence,
        supporting_claim_ids=(),
        verification_questions=tuple(item.verification_questions),
        evidence_status="proposed",
        stop_reason=item.stop_reason,
    )


def _truncate_input(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    marker = "\n[INPUT_TRUNCATED]"
    if max_chars <= len(marker):
        return marker[-max_chars:]
    return content[: max_chars - len(marker)] + marker


def _repair_prompt(violation_code: str) -> str:
    return f"""
REPAIR REQUIRED: the previous response violated the dependency JSON contract.
VIOLATION_CODE: {violation_code}
Return one corrected JSON object only. Do not add evidence, supporting claim IDs, source quotes,
scores, or a supported status. A schema-valid empty dependencies array is allowed.
""".strip()


def _format_request(request, demand_frame, node) -> str:
    context = {
        "theme": request.theme,
        "trigger": request.trigger,
        "region": request.region,
        "as_of_date": request.as_of_date.isoformat(),
        "time_horizon_months": request.time_horizon_months,
        "analysis_goal": request.analysis_goal,
        "demand_frame": {
            "normalized_theme": demand_frame.normalized_theme,
            "scope": demand_frame.scope,
            "exclusions": list(demand_frame.exclusions),
            "demand_hypothesis": demand_frame.demand_hypothesis,
            "measurable_demand_variables": list(
                demand_frame.measurable_demand_variables
            ),
            "unresolved_questions": list(demand_frame.unresolved_questions),
        },
        "downstream_node": {
            "node_id": node.node_id,
            "normalized_name": node.normalized_name,
            "node_type": node.node_type,
            "depth": node.depth,
            "description": node.description,
            "aliases": list(node.aliases),
        },
    }
    return "PROPOSAL_CONTEXT_JSON:\n" + json.dumps(
        context, ensure_ascii=False, sort_keys=True
    )


def _system_prompt(max_candidates: int) -> str:
    return f"""
You propose upstream supply-chain dependencies for Theme Chokepoint Stage 2.
Return one JSON object only, with a dependencies array containing
a maximum {max_candidates} dependencies.
An empty dependencies array is valid when no upstream dependency should be proposed for this node.

Every dependency must include exactly these fields:
{{
  "upstream_name": "canonical upstream name",
  "node_type": "PRD-consistent typed segment",
  "description": "concise segment description",
  "aliases": ["known alias"],
  "relation_type": "directional dependency relationship",
  "demand_transmission": "how downstream demand reaches this upstream node",
  "criticality_hypothesis": "testable constraint hypothesis",
  "substitute_hypothesis": "testable substitute hypothesis",
  "confidence": 0.5,
  "verification_questions": ["question Stage 3 can verify against original sources"],
  "stop_reason": null
}}

Allowed non-null stop_reason values are non_critical, sufficiently_commoditized, duplicated,
outside_scope, and unsupported. Do not emit max_depth; the graph service owns depth budgets.
Use the PRD node-type vocabulary, including component, equipment, material, software/IP,
infrastructure, or regulatory dependency; do not invent scoring categories.
These are unverified proposals, not evidence. Never emit evidence_status, supporting_claim_ids,
source quotes, scores, investment conclusions, or claims of support. Stage 3 verifies proposals
against original-source evidence. Missing evidence remains unknown/proposed, never failed or zero.
""".strip()


__all__ = [
    "DEPENDENCY_PROPOSER_PROMPT_VERSION",
    "DependencyProposalError",
    "DeepSeekUpstreamDependencyProposer",
]
