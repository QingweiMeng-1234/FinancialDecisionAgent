"""Live production provider composition for Theme Chokepoint."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_collector.theme_chokepoint.contracts import (
    CounterSearchRawProviderResponse,
    DemandFrame,
    ProductAnchorDraft,
)
from event_collector.theme_chokepoint.production import (
    AuthenticatedEndpointConfig,
    ProductionThemeChokepointConfig,
    build_production_theme_chokepoint_runtime,
)
from event_collector.theme_chokepoint.providers import (
    DeepSeekV14SegmentScorer,
    OpenAICompatibleEvidenceSpanExtractor,
    OriginalTextFetcher,
    TavilyOriginalEvidenceAcquirer,
    TavilySearchProvider,
)
from event_collector.theme_chokepoint.providers.tavily import (
    TAVILY_SEARCH_URL,
    tavily_cost_from_response,
)


class LiveProviderError(RuntimeError):
    """Stable boundary for live provider configuration and response failures."""


class _FramePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    normalized_theme: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    exclusions: list[str]
    demand_hypothesis: str = Field(min_length=1)
    measurable_demand_variables: list[str] = Field(min_length=1)
    unresolved_questions: list[str]


class _AnchorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    product_name: str = Field(min_length=1)
    buyer_or_user: str = Field(min_length=1)
    demand_variable: str = Field(min_length=1)
    theme_link: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str]
    missing_evidence: list[str]


class _AnchorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    anchors: list[_AnchorPayload] = Field(min_length=1)


class _DeepSeekStructuredProvider:
    def __init__(
        self,
        *,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("live provider timeout must be positive")
        self.model = model or os.getenv("THEME_CHOKEPOINT_LLM_MODEL")
        if not self.model:
            raise LiveProviderError("live provider model is not configured")
        if client is None:
            if os.getenv("THEME_CHOKEPOINT_LLM_PROVIDER", "").strip().casefold() != "deepseek":
                raise LiveProviderError("live provider must be configured for deepseek")
            resolved_key = (
                api_key
                or os.getenv("THEME_CHOKEPOINT_LLM_API_KEY")
                or os.getenv("DEEPSEEK_API_KEY")
            )
            if not resolved_key:
                raise LiveProviderError("live provider API key is not configured")
            from openai import OpenAI

            client = OpenAI(
                api_key=resolved_key,
                base_url=(
                    base_url
                    or os.getenv("THEME_CHOKEPOINT_LLM_BASE_URL")
                    or "https://api.deepseek.com"
                ),
                timeout=timeout_seconds,
            )
        self.client = client
        self.timeout_seconds = timeout_seconds

    def _complete(self, *, system: str, user: str, decoder):
        violation = "invalid_response"
        for attempt in range(2):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            if attempt:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The prior response violated the strict JSON contract "
                            f"({violation}). Return only a corrected JSON object."
                        ),
                    }
                )
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                    timeout=self.timeout_seconds,
                )
                content = completion.choices[0].message.content
                payload = json.loads(content)
                return decoder(payload)
            except ValidationError as error:
                violation = ",".join(
                    f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                    for item in error.errors()
                )
                if attempt == 0:
                    continue
            except (AttributeError, IndexError, TypeError, json.JSONDecodeError) as error:
                violation = type(error).__name__
                if attempt == 0:
                    continue
            except Exception:
                raise LiveProviderError("live provider request failed") from None
        raise LiveProviderError("live provider response failed strict validation")


class DeepSeekThemeFramer(_DeepSeekStructuredProvider):
    def frame(self, research_request) -> DemandFrame:
        payload = self._complete(
            system=(
                "Frame an investment research theme into a measurable demand scope. "
                "Return exactly one JSON object with normalized_theme, scope, exclusions, "
                "demand_hypothesis, measurable_demand_variables, unresolved_questions. "
                "All list items must be strings. Do not include markdown."
            ),
            user=json.dumps(
                {
                    "theme": research_request.theme,
                    "trigger": research_request.trigger,
                    "region": research_request.region,
                    "as_of_date": research_request.as_of_date.isoformat(),
                    "time_horizon_months": research_request.time_horizon_months,
                    "analysis_goal": research_request.analysis_goal,
                    "seed_products": list(research_request.seed_products),
                    "seed_companies": list(research_request.seed_companies),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            decoder=_FramePayload.model_validate,
        )
        return DemandFrame(
            normalized_theme=payload.normalized_theme,
            scope=payload.scope,
            exclusions=tuple(payload.exclusions),
            demand_hypothesis=payload.demand_hypothesis,
            measurable_demand_variables=tuple(payload.measurable_demand_variables),
            time_horizon_months=research_request.time_horizon_months,
            unresolved_questions=tuple(payload.unresolved_questions),
        )


class DeepSeekProductAnchorProposer(_DeepSeekStructuredProvider):
    def propose(self, research_request, demand_frame) -> list[ProductAnchorDraft]:
        payload = self._complete(
            system=(
                "Propose concrete purchasable product anchors for a Theme Chokepoint run. "
                "Return exactly one JSON object {\"anchors\":[...]} with product_name, buyer_or_user, "
                "demand_variable, theme_link, confidence, supporting_evidence_ids, "
                "missing_evidence. confidence must be 0..1. Do not invent evidence IDs; "
                "use an empty list when Stage 1 has no persisted evidence. Do not include markdown."
            ),
            user=json.dumps(
                {
                    "request": {
                        "theme": research_request.theme,
                        "region": research_request.region,
                        "as_of_date": research_request.as_of_date.isoformat(),
                        "max_product_anchors": research_request.max_product_anchors,
                    },
                    "demand_frame": {
                        "normalized_theme": demand_frame.normalized_theme,
                        "scope": demand_frame.scope,
                        "demand_hypothesis": demand_frame.demand_hypothesis,
                        "measurable_demand_variables": list(
                            demand_frame.measurable_demand_variables
                        ),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            decoder=_AnchorResponse.model_validate,
        )
        return [
            ProductAnchorDraft(
                product_name=item.product_name,
                buyer_or_user=item.buyer_or_user,
                demand_variable=item.demand_variable,
                theme_link=item.theme_link,
                confidence=item.confidence,
                supporting_evidence_ids=tuple(item.supporting_evidence_ids),
                missing_evidence=tuple(item.missing_evidence),
            )
            for item in payload.anchors[: research_request.max_product_anchors]
        ]


class TavilyCounterSearchExecutor:
    """Execute a real discovery query without treating snippets as original evidence."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session=None,
        timeout_seconds: float = 20.0,
        max_results: int = 5,
        search_depth: str = "advanced",
        estimated_cost_usd_per_credit: float | None = None,
    ):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise LiveProviderError("Tavily API key is not configured")
        if timeout_seconds <= 0 or not 1 <= max_results <= 20:
            raise ValueError("Tavily counter-search budgets are invalid")
        if search_depth not in {"basic", "advanced"}:
            raise ValueError("Tavily counter-search depth is invalid")
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results
        self.search_depth = search_depth
        self.estimated_cost_usd_per_credit = (
            float(os.getenv("TAVILY_ESTIMATED_COST_USD_PER_CREDIT", "0.004"))
            if estimated_cost_usd_per_credit is None
            else float(estimated_cost_usd_per_credit)
        )
        if self.estimated_cost_usd_per_credit < 0:
            raise ValueError("Tavily estimated credit cost cannot be negative")

    def execute(self, *, route_id: str, query: str, **_context):
        if not route_id.strip() or not query.strip():
            raise ValueError("counter-search route and query are required")
        try:
            response = self.session.post(
                TAVILY_SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query.strip(),
                    "search_depth": self.search_depth,
                    "max_results": self.max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            upstream = response.json()
        except Exception:
            raise LiveProviderError("Tavily counter-search request failed") from None
        results = upstream.get("results", []) if isinstance(upstream, dict) else []
        result_count = len(results) if isinstance(results, list) else 0
        retrieved_at = datetime.now(timezone.utc)
        trace_id = (
            response.headers.get("X-Request-Id")
            or response.headers.get("X-Provider-Trace-Id")
            or (upstream.get("request_id") if isinstance(upstream, dict) else None)
            or "tavily-" + sha256(
                f"{route_id}\0{query}\0{retrieved_at.isoformat()}".encode("utf-8")
            ).hexdigest()[:24]
        )
        _, cost_usd, _ = tavily_cost_from_response(
            response.headers,
            upstream,
            search_depth=self.search_depth,
            estimated_cost_usd_per_credit=self.estimated_cost_usd_per_credit,
        )
        normalized = {
            "query_log_id": "tavily-query-"
            + sha256(f"{route_id}\0{query}".encode("utf-8")).hexdigest()[:24],
            "status": "unknown",
            "coverage_state": "unknown",
            "evidence_ids": [],
            "finding": (
                f"Executed {route_id} route; {result_count} discovery result"
                f"{'s' if result_count != 1 else ''} require original-text verification."
            ),
            "counter_evidence": [],
        }
        return CounterSearchRawProviderResponse(
            provider="tavily-search-live",
            provider_trace_id=str(trace_id).strip(),
            http_status=int(response.status_code),
            raw_body=json.dumps(
                normalized, ensure_ascii=False, sort_keys=True
            ).encode("utf-8"),
            retrieved_at=retrieved_at,
            cost_usd=cost_usd,
        )


def build_live_runtime_from_env(runtime_config):
    """Shipping factory used by ``FINANCIAL_AGENT_THEME_RUNTIME_FACTORY``."""

    db_path = Path(runtime_config.theme_db_path)
    runtime_root = db_path.parent
    governance_value = os.getenv("THEME_CHOKEPOINT_GOVERNANCE_BUNDLE_PATH", "").strip()
    config = ProductionThemeChokepointConfig(
        db_path=db_path,
        artifact_root=Path(
            os.getenv(
                "FINANCIAL_AGENT_THEME_ARTIFACT_ROOT",
                str(runtime_root / "theme-artifacts"),
            )
        ),
        signal_export_root=Path(
            os.getenv(
                "FINANCIAL_AGENT_THEME_SIGNAL_ROOT",
                str(runtime_root / "theme-signals"),
            )
        ),
        manifest_root=Path(
            os.getenv(
                "FINANCIAL_AGENT_THEME_MANIFEST_ROOT",
                str(runtime_root / "theme-manifests"),
            )
        ),
        company_discovery=_endpoint_from_env("company-discovery"),
        company_evidence=_endpoint_from_env("company-evidence"),
        company_scoring=_endpoint_from_env("company-scoring"),
        company_critic=_endpoint_from_env("company-critic"),
        fact_verifier=_endpoint_from_env("business-fact-verifier"),
        source_resolver=_endpoint_from_env("source-identity-resolver"),
        verifier_execution_id=os.getenv(
            "THEME_CHOKEPOINT_VERIFIER_EXECUTION_ID",
            "business-fact-verifier-live-v1",
        ),
        assertion_producer_execution_id=os.getenv(
            "THEME_CHOKEPOINT_ASSERTION_PRODUCER_EXECUTION_ID",
            "company-assertion-producer-live-v1",
        ),
        governance_bundle_path=(Path(governance_value) if governance_value else None),
        allow_unfrozen_overlay=True,
        counter_max_queries=3,
        counter_max_time_seconds=int(
            os.getenv("THEME_CHOKEPOINT_COUNTER_MAX_TIME_SECONDS", "90")
        ),
        counter_max_cost_usd=float(
            os.getenv("THEME_CHOKEPOINT_COUNTER_MAX_COST_USD", "1")
        ),
        max_companies=int(os.getenv("THEME_CHOKEPOINT_MAX_COMPANIES", "1")),
    )
    search = TavilySearchProvider(
        max_results=int(os.getenv("THEME_CHOKEPOINT_SEARCH_MAX_RESULTS", "3"))
    )
    evidence_acquirer = TavilyOriginalEvidenceAcquirer(
        search,
        OriginalTextFetcher(),
        OpenAICompatibleEvidenceSpanExtractor(),
        metered=True,
    )
    return build_production_theme_chokepoint_runtime(
        config,
        theme_framer=DeepSeekThemeFramer(),
        product_anchor_proposer=DeepSeekProductAnchorProposer(),
        evidence_acquirer=evidence_acquirer,
        segment_scorer=DeepSeekV14SegmentScorer(),
        counter_search_executor=TavilyCounterSearchExecutor(),
    )


def _endpoint_from_env(role: str) -> AuthenticatedEndpointConfig:
    prefix = "THEME_CHOKEPOINT_" + role.replace("-", "_").upper()
    endpoint = os.getenv(f"{prefix}_ENDPOINT", "").strip()
    token = os.getenv(f"{prefix}_TOKEN", "").strip()
    if not endpoint:
        raise LiveProviderError(f"{role} endpoint is not configured")
    if not token:
        raise LiveProviderError(f"{role} credential is not configured")
    return AuthenticatedEndpointConfig(
        role=role,
        endpoint=endpoint,
        provider_identity=os.getenv(
            f"{prefix}_PROVIDER_IDENTITY", f"provider:{role}:live"
        ),
        authenticated_boundary_id=os.getenv(
            f"{prefix}_BOUNDARY_ID", f"account-route:{role}:live"
        ),
        bearer_token=token,
        timeout_seconds=float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "90")),
    )


__all__ = [
    "DeepSeekProductAnchorProposer",
    "DeepSeekThemeFramer",
    "LiveProviderError",
    "TavilyCounterSearchExecutor",
    "build_live_runtime_from_env",
]
