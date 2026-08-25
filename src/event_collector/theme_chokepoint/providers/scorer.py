"""Evidence-bounded v1.4 Segment scorer adapter."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult,
    BoundBasis,
    DimensionRatingDraft,
    SegmentScoreDraft,
)
from event_collector.theme_chokepoint.providers.llm import (
    _configured_api_key,
    _configured_base_url,
    _configured_model,
    _estimate_deepseek_completion_cost,
    _price,
)


SEGMENT_SCORER_PROMPT_VERSION = "theme-chokepoint-segment-scorer-v1"
SEGMENT_DIMENSIONS = (
    "demand_pressure",
    "downstream_criticality",
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
)
SUPPLY_DIMENSIONS = frozenset(SEGMENT_DIMENSIONS[2:])


class _BoundBasisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor_anchor: int | None = Field(default=None, ge=0, le=4)
    ceiling_anchor: int | None = Field(default=None, ge=0, le=4)
    exact_basis: Literal[
        "natural_cap", "direct_upper_bound", "contract_exclusivity"
    ] | None = None
    unresolved_higher_anchors: list[int] = Field(default_factory=list)
    excluded_higher_anchors: list[int] = Field(default_factory=list)


class _DimensionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "demand_pressure",
        "downstream_criticality",
        "effective_supply_concentration",
        "qualification_barrier",
        "capacity_inelasticity",
        "substitute_weakness",
    ]
    rating_min: int = Field(ge=0, le=4)
    rating_max: int = Field(ge=0, le=4)
    evidence_state: Literal["supported", "conflicted", "unknown"]
    bound_type: Literal["exact", "lower_bound", "upper_bound", "interval", "none"]
    bound_basis: _BoundBasisPayload
    evidence_ids: list[str]
    stale: bool
    rationale: str = Field(min_length=1)
    missing_material_questions: list[str]


class _SegmentScoringResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: list[_DimensionPayload] = Field(min_length=6, max_length=6)


class _ModelContractViolation(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DeepSeekV14SegmentScorer:
    """Score only persisted Claim Ledger evidence under the frozen v1.4 contract."""

    def __init__(
        self,
        *,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        deepseek_cache_hit_usd_per_million: float | None = None,
        deepseek_cache_miss_usd_per_million: float | None = None,
        deepseek_output_usd_per_million: float | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("segment scorer timeout must be positive")
        self.model = model or _configured_model()
        if not self.model:
            raise RuntimeError("a configured model is required for Segment scoring")
        if client is None:
            resolved_key, provider = _configured_api_key(api_key)
            if not resolved_key:
                raise RuntimeError("a configured API key is required for Segment scoring")
            from openai import OpenAI

            kwargs = {"api_key": resolved_key, "timeout": timeout_seconds}
            resolved_base = base_url or _configured_base_url(provider)
            if resolved_base:
                kwargs["base_url"] = resolved_base
            client = OpenAI(**kwargs)
        self.client = client
        self.deepseek_cache_hit_usd_per_million = _price(
            deepseek_cache_hit_usd_per_million,
            "DEEPSEEK_CACHE_HIT_USD_PER_MILLION_TOKENS",
            0.028,
        )
        self.deepseek_cache_miss_usd_per_million = _price(
            deepseek_cache_miss_usd_per_million,
            "DEEPSEEK_CACHE_MISS_USD_PER_MILLION_TOKENS",
            0.28,
        )
        self.deepseek_output_usd_per_million = _price(
            deepseek_output_usd_per_million,
            "DEEPSEEK_OUTPUT_USD_PER_MILLION_TOKENS",
            0.42,
        )
        self._unreported_cost_usd = 0.0

    def assess(
        self,
        node,
        claims,
        evidence_cards,
        *,
        request,
        contract_version,
    ):
        if contract_version != "theme-chokepoint-scoring-v1.4":
            raise ValueError("DeepSeekV14SegmentScorer requires the frozen v1.4 contract")
        _validate_ledger(node, claims, evidence_cards, request)
        if not claims and not evidence_cards:
            dimensions = tuple(_unknown_dimension(name) for name in SEGMENT_DIMENSIONS)
            return _build_draft(node.node_id, dimensions, claims, evidence_cards)

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _format_request(node, claims, evidence_cards, request),
            },
        ]
        violation_code = "unspecified_contract_violation"
        for attempt in range(2):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append(
                    {"role": "user", "content": _repair_prompt(violation_code)}
                )
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=attempt_messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            if "deepseek" in self.model.casefold():
                self._unreported_cost_usd += _estimate_deepseek_completion_cost(
                    completion,
                    cache_hit_rate=self.deepseek_cache_hit_usd_per_million,
                    cache_miss_rate=self.deepseek_cache_miss_usd_per_million,
                    output_rate=self.deepseek_output_usd_per_million,
                )
            try:
                dimensions = _decode_dimensions(completion, claims, evidence_cards)
            except _ModelContractViolation as error:
                violation_code = error.code
                if attempt == 0:
                    continue
                raise RuntimeError(
                    "segment scorer failed the structured-output contract after one repair: "
                    + violation_code
                ) from None
            return _build_draft(node.node_id, dimensions, claims, evidence_cards)
        raise AssertionError("unreachable Segment scorer repair state")

    def consume_cost_usd(self) -> float:
        cost = round(self._unreported_cost_usd, 6)
        self._unreported_cost_usd = 0.0
        return cost


def _decode_dimensions(completion, claims, evidence_cards):
    try:
        message = completion.choices[0].message
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str) or not content.strip():
            raise _ModelContractViolation("empty_response")
    except (AttributeError, IndexError, TypeError):
        raise _ModelContractViolation("invalid_response_envelope") from None
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise _ModelContractViolation("invalid_json") from None
    try:
        response = _SegmentScoringResponse.model_validate(payload)
    except ValidationError as error:
        details = []
        for item in error.errors()[:3]:
            location = ".".join(str(part) for part in item["loc"])
            details.append(f"{location}:{item['type']}")
        code = "schema_violation"
        if details:
            code += ":" + "|".join(details)
        raise _ModelContractViolation(code) from None
    try:
        return _validated_dimensions(response, claims, evidence_cards)
    except ValueError as error:
        raise _ModelContractViolation(str(error)) from None


def _validate_ledger(node, claims, evidence_cards, request):
    claim_by_id = {claim.claim_id: claim for claim in claims}
    card_by_id = {card.evidence_id: card for card in evidence_cards}
    if len(claim_by_id) != len(claims) or len(card_by_id) != len(evidence_cards):
        raise ValueError("Claim Ledger identifiers must be unique")
    for claim in claims:
        if claim.node_id != node.node_id:
            raise ValueError("scoring claim belongs to a different segment")
        if not set(claim.evidence_ids) <= set(card_by_id):
            raise ValueError("claim references evidence outside the supplied ledger")
    for card in evidence_cards:
        claim = claim_by_id.get(card.claim_id)
        if claim is None or card.evidence_id not in claim.evidence_ids:
            raise ValueError("evidence card is not linked to its supplied claim")
        for source_date in (card.publication_date, card.data_as_of_date):
            if source_date and source_date > request.as_of_date:
                raise ValueError("future-dated evidence cannot enter an as-of assessment")


def _validated_dimensions(response, claims, evidence_cards):
    by_name = {item.dimension: item for item in response.dimensions}
    if len(by_name) != len(response.dimensions) or set(by_name) != set(SEGMENT_DIMENSIONS):
        raise ValueError("segment scorer must return every frozen dimension exactly once")
    claim_by_id = {claim.claim_id: claim for claim in claims}
    card_by_id = {card.evidence_id: card for card in evidence_cards}
    result = []
    for name in SEGMENT_DIMENSIONS:
        item = by_name[name]
        _validate_dimension_payload(item, claim_by_id, card_by_id)
        basis = item.bound_basis
        anchor_conditions = _anchor_conditions(item, claim_by_id, card_by_id)
        result.append(
            DimensionRatingDraft(
                dimension=name,
                rating_min=item.rating_min,
                rating_max=item.rating_max,
                evidence_state=item.evidence_state,
                bound_type=item.bound_type,
                bound_basis=BoundBasis(
                    floor_anchor=basis.floor_anchor,
                    ceiling_anchor=basis.ceiling_anchor,
                    exact_basis=basis.exact_basis,
                    unresolved_higher_anchors=tuple(basis.unresolved_higher_anchors),
                    excluded_higher_anchors=tuple(basis.excluded_higher_anchors),
                ),
                evidence_ids=tuple(item.evidence_ids),
                stale=item.stale,
                rationale=item.rationale.strip(),
                missing_material_questions=tuple(
                    question.strip()
                    for question in item.missing_material_questions
                    if question.strip()
                ),
                anchor_conditions=anchor_conditions,
                decisive_evidence_ids=tuple(item.evidence_ids),
            )
        )
    return tuple(result)


def _validate_dimension_payload(item, claim_by_id, card_by_id):
    basis = item.bound_basis
    if item.rating_min > item.rating_max:
        raise ValueError("dimension lower bound cannot exceed its upper bound")
    if item.evidence_state == "unknown":
        if (
            item.bound_type != "none"
            or (item.rating_min, item.rating_max) != (0, 4)
            or item.evidence_ids
            or any(
                value is not None
                for value in (basis.floor_anchor, basis.ceiling_anchor, basis.exact_basis)
            )
            or basis.unresolved_higher_anchors
            or basis.excluded_higher_anchors
        ):
            raise ValueError("unknown dimensions must preserve none + [0,4] without evidence")
        return
    if not item.evidence_ids or not set(item.evidence_ids) <= set(card_by_id):
        raise ValueError("resolved dimensions must cite supplied Evidence Cards")
    scoring_claims = []
    for evidence_id in item.evidence_ids:
        card = card_by_id[evidence_id]
        claim = claim_by_id[card.claim_id]
        if claim.material_field != item.dimension or claim.scoring_use == "context_only":
            raise ValueError("dimension cites evidence without scoring ownership")
        scoring_claims.append(claim)
    stances = {card_by_id[evidence_id].stance for evidence_id in item.evidence_ids}
    if item.evidence_state == "supported" and stances != {"supports"}:
        raise ValueError(
            "supported dimensions require every decisive Evidence Card to support"
        )
    if item.evidence_state == "conflicted":
        if not {"supports", "contradicts"} <= stances:
            raise ValueError("conflicted dimensions require both supports and contradicts")
    if item.rating_min >= 3 and not any(
        claim.scoring_use == "primary"
        and claim.primary_scoring_dimension == item.dimension
        for claim in scoring_claims
    ):
        raise ValueError("ratings 3-4 require primary high-grade evidence ownership")
    if item.bound_type == "exact":
        _validate_exact_basis(item, scoring_claims)
        return
    if item.bound_type == "lower_bound":
        _validate_lower_bound_basis(item)
        return
    if item.bound_type == "upper_bound":
        _validate_upper_bound_basis(item)
        return
    if item.bound_type == "interval":
        _validate_interval_basis(item)
        return
    raise ValueError("only exact and unknown bounds are implemented")


def _validate_exact_basis(item, scoring_claims):
    basis = item.bound_basis
    if item.rating_min != item.rating_max:
        raise ValueError("exact bounds require one rating")
    rating = item.rating_min
    if basis.floor_anchor != rating or basis.ceiling_anchor != rating:
        raise ValueError("exact Bound Basis must match the submitted rating")
    if basis.unresolved_higher_anchors:
        raise ValueError("exact bounds cannot retain unresolved higher anchors")
    if rating == 4:
        if basis.exact_basis != "natural_cap" or basis.excluded_higher_anchors:
            raise ValueError("exact 4 requires natural_cap")
    else:
        required = set(range(rating + 1, 5))
        if basis.exact_basis not in {"direct_upper_bound", "contract_exclusivity"}:
            raise ValueError("exact ratings below 4 require a supported upper-bound basis")
        if set(basis.excluded_higher_anchors) != required:
            raise ValueError("exact rating must exclude every higher anchor")
        ceiling_capabilities = {"direct_upper_bound", "contract_exclusivity"}
        if not any(
            ceiling_capabilities & set(claim.claim_capabilities)
            for claim in scoring_claims
        ):
            raise ValueError(
                "exact ratings below 4 require decisive upper-bound evidence capability"
            )


def _anchor_conditions(item, claim_by_id, card_by_id):
    if item.evidence_state != "supported":
        return ()
    claim_ids = tuple(
        dict.fromkeys(card_by_id[evidence_id].claim_id for evidence_id in item.evidence_ids)
    )
    for claim_id in claim_ids:
        if claim_id not in claim_by_id:
            raise ValueError("anchor condition decisive claim is missing")
    conditions = []
    if item.bound_basis.floor_anchor is not None:
        conditions.append(
            AnchorConditionResult(
                condition_id=(
                    f"{item.dimension}.anchor_{item.bound_basis.floor_anchor}.floor"
                ),
                condition_type="floor",
                evidence_state="supported",
                evidence_ids=tuple(item.evidence_ids),
                decisive_claim_ids=claim_ids,
                anchor=item.bound_basis.floor_anchor,
            )
        )
    if item.bound_basis.ceiling_anchor is not None and item.rating_max < 4:
        conditions.append(
            AnchorConditionResult(
                condition_id=(
                    f"{item.dimension}.anchor_{item.bound_basis.ceiling_anchor}.ceiling"
                ),
                condition_type="ceiling",
                evidence_state="supported",
                evidence_ids=tuple(item.evidence_ids),
                decisive_claim_ids=claim_ids,
                anchor=item.bound_basis.ceiling_anchor,
                excluded_higher_anchors=tuple(
                    item.bound_basis.excluded_higher_anchors
                ),
            )
        )
    return tuple(conditions)


def _validate_lower_bound_basis(item):
    basis = item.bound_basis
    if item.rating_min >= 4 or item.rating_max != 4:
        raise ValueError("lower bounds must preserve every unresolved rating above the floor")
    if basis.floor_anchor != item.rating_min:
        raise ValueError("lower-bound floor_anchor must match rating_min")
    if basis.ceiling_anchor is not None or basis.exact_basis is not None:
        raise ValueError("lower bounds cannot carry a ceiling or exact_basis")
    if basis.excluded_higher_anchors:
        raise ValueError("lower bounds cannot exclude unresolved higher anchors")
    required = set(range(item.rating_min + 1, 5))
    if set(basis.unresolved_higher_anchors) != required:
        raise ValueError("lower bound must list every unresolved higher anchor")


def _validate_upper_bound_basis(item):
    basis = item.bound_basis
    if item.rating_min != 0 or not 0 < item.rating_max < 4:
        raise ValueError("upper bounds must use a finite [0,k] interval")
    if basis.floor_anchor is not None or basis.ceiling_anchor != item.rating_max:
        raise ValueError("upper-bound ceiling_anchor must match rating_max")
    if basis.exact_basis is not None or basis.unresolved_higher_anchors:
        raise ValueError("upper bounds cannot carry exact or unresolved-higher basis")
    required = set(range(item.rating_max + 1, 5))
    if set(basis.excluded_higher_anchors) != required:
        raise ValueError("upper bound must exclude every higher anchor")


def _validate_interval_basis(item):
    basis = item.bound_basis
    if not 0 < item.rating_min < item.rating_max < 4:
        raise ValueError("finite intervals must satisfy 0 < floor < ceiling < 4")
    if (
        basis.floor_anchor != item.rating_min
        or basis.ceiling_anchor != item.rating_max
        or basis.exact_basis is not None
    ):
        raise ValueError("interval Bound Basis must match both submitted boundaries")
    feasible_higher = set(range(item.rating_min + 1, item.rating_max + 1))
    if set(basis.unresolved_higher_anchors) != feasible_higher:
        raise ValueError("interval must preserve every unresolved anchor through its ceiling")
    excluded = set(range(item.rating_max + 1, 5))
    if set(basis.excluded_higher_anchors) != excluded:
        raise ValueError("interval must exclude every anchor above its ceiling")


def _build_draft(node_id, dimensions, claims, evidence_cards):
    card_by_id = {card.evidence_id: card for card in evidence_cards}
    claim_by_id = {claim.claim_id: claim for claim in claims}
    by_name = {item.dimension: item for item in dimensions}

    def has_direct_primary(name):
        item = by_name[name]
        if item.evidence_state != "supported":
            return False
        for evidence_id in item.evidence_ids:
            card = card_by_id[evidence_id]
            claim = claim_by_id[card.claim_id]
            if (
                card.stance == "supports"
                and claim.scoring_use == "primary"
                and claim.primary_scoring_dimension == name
            ):
                return True
        return False

    missing = tuple(
        item.dimension
        for item in dimensions
        if item.evidence_state != "supported"
        or item.rating_min != item.rating_max
        or item.stale
    )
    return SegmentScoreDraft(
        segment_id=node_id,
        dimensions=tuple(dimensions),
        missing_material_fields=missing,
        demand_direct_evidence=has_direct_primary("demand_pressure"),
        supply_direct_evidence=any(has_direct_primary(name) for name in SUPPLY_DIMENSIONS),
        counter_evidence_search_complete=False,
        mandatory_conflict=any(
            item.evidence_state == "conflicted" for item in dimensions
        ),
        relief_horizon=None,
    )


def _unknown_dimension(name: str) -> DimensionRatingDraft:
    return DimensionRatingDraft(
        dimension=name,
        rating_min=0,
        rating_max=4,
        evidence_state="unknown",
        bound_type="none",
        bound_basis=BoundBasis(),
        evidence_ids=(),
        stale=False,
        rationale="No scoring-eligible evidence is available for this dimension.",
        missing_material_questions=(
            f"What original-source evidence resolves {name}?",
        ),
    )


def _format_request(node, claims, evidence_cards, request):
    ledger = {
        "claims": [
            {
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "material_field": claim.material_field,
                "primary_scoring_dimension": claim.primary_scoring_dimension,
                "scoring_use": claim.scoring_use,
                "source_ambiguity": claim.source_ambiguity,
                "scoring_eligible": claim.scoring_eligible,
                "evidence_ids": list(claim.evidence_ids),
            }
            for claim in claims
        ],
        "evidence_cards": [
            {
                "evidence_id": card.evidence_id,
                "claim_id": card.claim_id,
                "canonical_url": card.canonical_url,
                "source_title": card.source_title,
                "publisher": card.publisher,
                "source_type": card.source_type,
                "publication_date": card.publication_date.isoformat()
                if card.publication_date
                else None,
                "data_as_of_date": card.data_as_of_date.isoformat()
                if card.data_as_of_date
                else None,
                "location": card.location,
                "exact_quote": card.exact_quote,
                "stance": card.stance,
                "limitations": card.limitations,
                "source_ambiguity": card.source_ambiguity,
                "scoring_eligible": card.scoring_eligible,
            }
            for card in evidence_cards
        ],
    }
    return (
        f"CONTRACT_VERSION: theme-chokepoint-scoring-v1.4\n"
        f"SEGMENT_ID: {node.node_id}\n"
        f"SEGMENT_NAME: {node.normalized_name}\n"
        f"SEGMENT_DESCRIPTION: {node.description}\n"
        f"THEME: {request.theme}\n"
        f"GEOGRAPHY: {request.region}\n"
        f"AS_OF_DATE: {request.as_of_date.isoformat()}\n"
        f"TIME_HORIZON_MONTHS: {request.time_horizon_months}\n\n"
        "CLAIM_LEDGER_JSON:\n"
        + json.dumps(ledger, ensure_ascii=False, sort_keys=True)
    )


_REPAIR_PROMPT = """
REPAIR REQUIRED: the previous response violated the frozen structured-output contract.
Re-evaluate the same supplied Claim Ledger and return one corrected JSON object only.
Do not add evidence, loosen a bound, infer missing facts, or include commentary.
""".strip()


def _repair_prompt(violation_code: str) -> str:
    guidance = ""
    if violation_code == "exact 4 requires natural_cap":
        guidance = (
            "\nFor exact rating 4, the bound_basis object must be exactly "
            '{"floor_anchor": 4, "ceiling_anchor": 4, '
            '"exact_basis": "natural_cap", "unresolved_higher_anchors": [], '
            '"excluded_higher_anchors": []}. This is only a representation repair: '
            "if the supplied evidence does not support exact 4, return a supported "
            "lower bound or the canonical unknown object instead."
        )
    elif "bound_basis.exact_basis:literal_error" in violation_code:
        guidance = (
            "\nexact_basis must be JSON null unless bound_type is exact. "
            "For exact, use only natural_cap, direct_upper_bound, or "
            "contract_exclusivity; never use strings such as none, unknown, or N/A."
        )
    return f"{_REPAIR_PROMPT}\nVIOLATION_CODE: {violation_code}{guidance}"


_SYSTEM_PROMPT = """
You are the v1.4 Segment Chokepoint ordinal scorer. Use only supplied Claim Ledger quotes.
Return one JSON object with a dimensions array containing each of the six frozen dimensions
exactly once. Never return a total score, candidate state, company conclusion, or investment view.

Return exactly this schema with no additional keys. Repeat the dimension object exactly six times,
once for each frozen dimension, and include every field even when its value is null or an empty list:
{
  "dimensions": [
    {
      "dimension": "demand_pressure",
      "rating_min": 2,
      "rating_max": 4,
      "evidence_state": "supported",
      "bound_type": "lower_bound",
      "bound_basis": {
        "floor_anchor": 2,
        "ceiling_anchor": null,
        "exact_basis": null,
        "unresolved_higher_anchors": [3, 4],
        "excluded_higher_anchors": []
      },
      "evidence_ids": ["one supplied Evidence Card ID"],
      "stale": false,
      "rationale": "complete predicate, boundary, scope and limitation explanation",
      "missing_material_questions": []
    }
  ]
}
The canonical unknown object is:
{
  "dimension": "qualification_barrier",
  "rating_min": 0,
  "rating_max": 4,
  "evidence_state": "unknown",
  "bound_type": "none",
  "bound_basis": {
    "floor_anchor": null,
    "ceiling_anchor": null,
    "exact_basis": null,
    "unresolved_higher_anchors": [],
    "excluded_higher_anchors": []
  },
  "evidence_ids": [],
  "stale": false,
  "rationale": "no complete scoring-eligible anchor predicate is supported",
  "missing_material_questions": ["what original-source evidence resolves this dimension?"]
}

Missing evidence is unknown + none + [0,4], never 0 or neutral. Search absence is not negative
evidence. A 0 requires explicit failure, non-impact, adequate coverage, or another anchor-specific
negative fact. An exact rating needs both a supported floor and a supported ceiling. Missing a
higher predicate creates a lower bound, not exact. One atomic fact has only one primary high-grade
dimension; floor_only cannot support ratings above 2 and context_only cannot change a bound.
For exact rating 4, the bound_basis object must be exactly
{"floor_anchor": 4, "ceiling_anchor": 4, "exact_basis": "natural_cap",
 "unresolved_higher_anchors": [], "excluded_higher_anchors": []} because 4 is the natural cap.
If the complete anchor-4 predicate is not supported, return a supported lower bound or unknown;
never relabel weaker evidence as exact 4.

Frozen anchors:
- demand_pressure: 0 no growth/decline/no transmission; 1 >0% and <5%; 2 >=5% and <10%;
  3 >=10% and <20%; 4 >=20% with direct transmission and inventory handled. A same-scope comparable denominator
  for the same period and definition is mandatory; absolute orders/backlog or "strong demand" alone remains unknown.
- downstream_criticality: 0 explicit no impact; 1 delay <1 month and impact <5% with a positive
  consequence; 2 delay 1-3 months or impact 5-10%; 3 delay >3 months, impact >10%, or material
  performance degradation; 4 directly blocks shipment, commissioning, compliance, or critical
  performance. Do not count substitutes here.
- effective_supply_concentration: map N_eff / largest effective share / qualified failover ratio
  separately and take the supported envelope, never an average. Only like-for-like qualified supply.
  Anchors 0..4 are N_eff >=4, >=3, >=2, >=1.25, <1.25; largest share <30%, 30-40%, 40-60%,
  60-80%, >=80%; failover >=30%, 20-30%, 10-20%, >0-10%, 0.
- qualification_barrier months: 0 <=3; 1 >3-6; 2 >6-12; 3 >12-18; 4 >=18 with direct
  steps, historical cycle, or current milestone evidence.
- capacity_inelasticity after qualification months: 0 <=3; 1 >3-6; 2 >6-12; 3 >12-18;
  4 >=18 plus at least two physical constraint classes. Future capex alone is not current output.
- substitute_weakness: 0 production alternatives cover >=30%; 1 ready/production covers 10-30%;
  2 a credible challenge exists but ready coverage <10%; 3 routes remain prototype/sample/test or
  unfulfilled expansion; 4 requires a completed counterevidence search plus explicit failure,
  ineligibility, cancellation, or insufficient-capacity evidence for every frozen route.

Every resolved dimension must cite supplied evidence_ids and explain the complete anchor predicate,
upper-bound basis, limitations, stale status, and missing material questions. Return JSON only.
""".strip()
