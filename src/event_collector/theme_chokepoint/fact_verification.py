"""Canonical hashing and raw-result validation for business-fact verification."""

from __future__ import annotations

import base64
from dataclasses import asdict
from hashlib import sha256
import json
import re

from event_collector.theme_chokepoint.contracts import (
    BusinessFactAssertion,
    BusinessFactVerificationSourceContext,
)


def business_fact_assertion_sha256(assertion: BusinessFactAssertion) -> str:
    """Hash the proposed fact without trusting its producer-supplied receipt."""
    payload = asdict(assertion)
    payload.pop("verification", None)
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def business_fact_verification_source_context_json(
    context: BusinessFactVerificationSourceContext,
) -> str:
    payload = asdict(context)
    payload["original_document_bytes_base64"] = base64.b64encode(
        payload.pop("original_document_bytes")
    ).decode("ascii")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def business_fact_verification_source_context_sha256(
    context: BusinessFactVerificationSourceContext,
) -> str:
    return sha256(
        business_fact_verification_source_context_json(context).encode("utf-8")
    ).hexdigest()


def business_fact_verification_source_context_from_json(
    payload_json: str,
) -> BusinessFactVerificationSourceContext:
    try:
        payload = json.loads(payload_json)
        raw_bytes = base64.b64decode(
            payload.pop("original_document_bytes_base64"), validate=True
        )
        return BusinessFactVerificationSourceContext(
            **payload,
            original_document_bytes=raw_bytes,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("business-fact source context is invalid") from error


def parse_business_fact_text_semantics(text: str) -> tuple[str, str]:
    """Derive fail-closed polarity and lifecycle from the authoritative quote."""
    normalized = text.casefold()
    prohibited = (
        "unconfirmed",
        "unproven",
        "denied",
        "pending",
        "planned",
        "target",
        "expected",
        "may ",
        "might ",
        "could ",
        "not in production",
        "no production",
        "ended",
        "withdrawn",
    )
    explicit_negation = re.search(
        r"\b(?:is|are|was|were|has|have|had|do|does|did|can|could|will|would)\s+not\b",
        normalized,
    )
    absence = re.search(
        r"\b(?:absent|absence)\b|\bno\s+(?:reported\s+|recognized\s+|generated\s+)?"
        r"(?:revenue|earnings|production|deployment|use|orders?)\b",
        normalized,
    )
    if any(marker in normalized for marker in prohibited) or explicit_negation or absence:
        return "negative", "unknown"
    affirmative = re.search(
        r"\b(is|are|has|have|reported|recognized|generated|uses|used|deployed|supplies|entered)\b",
        normalized,
    )
    if not affirmative:
        return "unknown", "unknown"
    lifecycle_state = (
        "realized"
        if re.search(r"\b(reported|recognized|generated|entered)\b", normalized)
        else "current"
    )
    return "affirmative", lifecycle_state


def validate_business_fact_verifier_input(
    assertion: BusinessFactAssertion,
    source_context: BusinessFactVerificationSourceContext,
) -> None:
    """Reject non-affirmative or provenance-mismatched proposals before verifier I/O."""
    quote = source_context.exact_quote
    quote_polarity, _ = parse_business_fact_text_semantics(quote)
    if (
        assertion.polarity != "affirmative"
        or assertion.lifecycle_state not in {"current", "realized"}
        or quote_polarity == "negative"
    ):
        raise ValueError("business-fact verifier input is not affirmative and current")
    if (
        source_context.quote_start != assertion.quote_start
        or source_context.quote_end != assertion.quote_end
        or source_context.exact_quote_sha256 != assertion.exact_quote_sha256
        or source_context.issuer_company_id != assertion.subject_company_id
        or source_context.product_id != assertion.subject_product_id
        or source_context.fiscal_period_id != assertion.fiscal_period_id
        or source_context.accounting_metric != assertion.accounting_metric
    ):
        raise ValueError("business-fact verifier input does not match source context")
    is_accounting = any(
        value is not None
        for value in (
            assertion.accounting_metric,
            assertion.accounting_value,
            assertion.accounting_unit,
            assertion.currency,
            assertion.fiscal_period_type,
            assertion.fiscal_period_id,
            assertion.accounting_basis,
        )
    )
    if is_accounting:
        official_source_types = {
            "company_filing",
            "regulatory_filing",
            "annual_report",
            "quarterly_report",
            "sec",
            "sec_filing",
        }
        required_accounting_fields = (
            assertion.accounting_metric,
            assertion.accounting_value,
            assertion.accounting_unit,
            assertion.currency,
            assertion.fiscal_period_type,
            assertion.fiscal_period_id,
            assertion.accounting_basis,
        )
        if (
            not all(value is not None for value in required_accounting_fields)
            or source_context.source_type not in official_source_types
        ):
            raise ValueError(
                "business-fact accounting verifier requires an exact official source"
            )


def parse_business_fact_verification_raw(raw_body: bytes) -> dict:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("business-fact verifier raw response is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("business-fact verifier raw response must be an object")
    required = {"assertion_id", "assertion_sha256", "decision"}
    if set(payload) != required or not all(
        isinstance(payload[field], str) and payload[field]
        for field in required
    ):
        raise ValueError("business-fact verifier raw response is incomplete")
    return payload
