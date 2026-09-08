"""Independent, raw-boundary verification for proposed business facts."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from event_collector.theme_chokepoint.contracts import (
    BusinessFactAssertion,
    BusinessFactVerificationParsedResultRecord,
    BusinessFactVerificationRawProviderResponse,
    BusinessFactVerificationRawResponseRecord,
    BusinessFactVerificationReconciliationRecord,
    BusinessFactVerificationRequestRecord,
)
from event_collector.theme_chokepoint.fact_verification import (
    business_fact_assertion_sha256,
    business_fact_verification_source_context_sha256,
    parse_business_fact_verification_raw,
    validate_business_fact_verifier_input,
)


class EvidenceBoundBusinessFactVerifier:
    """Persist each verifier boundary and reconcile before returning authority."""

    def __init__(
        self,
        repository,
        raw_client,
        *,
        verifier_execution_id: str,
        governance_bundle_sha256: str,
    ) -> None:
        if not verifier_execution_id.strip():
            raise ValueError("business-fact verifier execution identity is required")
        if len(governance_bundle_sha256) != 64:
            raise ValueError("business-fact verifier governance SHA-256 is required")
        self.repository = repository
        self.raw_client = raw_client
        self.verifier_execution_id = verifier_execution_id
        self.governance_bundle_sha256 = governance_bundle_sha256

    def verify(
        self,
        *,
        run_id: str,
        assertion: BusinessFactAssertion,
        producer_execution_id: str,
        source_identity_id: str,
        source_version_id: str,
        source_type: str,
    ) -> BusinessFactVerificationReconciliationRecord:
        if producer_execution_id == self.verifier_execution_id:
            raise ValueError("business-fact verifier must be independent from producer")
        source_context = self.repository.load_business_fact_verification_source_context(
            source_identity_id=source_identity_id,
            source_version_id=source_version_id,
            source_type=source_type,
            issuer_company_id=assertion.subject_company_id,
            product_id=assertion.subject_product_id,
            fiscal_period_id=assertion.fiscal_period_id,
            accounting_metric=assertion.accounting_metric,
            quote_start=assertion.quote_start,
            quote_end=assertion.quote_end,
            exact_quote_sha256=assertion.exact_quote_sha256,
        )
        validate_business_fact_verifier_input(assertion, source_context)
        assertion_hash = business_fact_assertion_sha256(assertion)
        source_context_hash = business_fact_verification_source_context_sha256(
            source_context
        )
        created_at = datetime.now(timezone.utc)
        operation_id = uuid4().hex
        request = BusinessFactVerificationRequestRecord(
            request_record_id=f"fact-request-{operation_id}",
            run_id=run_id,
            assertion_id=assertion.assertion_id,
            evidence_id=assertion.evidence_id,
            assertion_sha256=assertion_hash,
            governance_bundle_sha256=self.governance_bundle_sha256,
            producer_execution_id=producer_execution_id,
            verifier_execution_id=self.verifier_execution_id,
            source_context=source_context,
            source_context_sha256=source_context_hash,
            created_at=created_at,
        )
        self.repository.save_business_fact_verification_request(request)

        raw = self.raw_client.verify_fact(
            request_record_id=request.request_record_id,
            run_id=run_id,
            assertion=assertion,
            assertion_sha256=assertion_hash,
            governance_bundle_sha256=self.governance_bundle_sha256,
            source_context=source_context,
            source_context_sha256=source_context_hash,
        )
        if not isinstance(raw, BusinessFactVerificationRawProviderResponse):
            raise ValueError("business-fact verifier client must return a raw response")
        response = BusinessFactVerificationRawResponseRecord(
            response_record_id=f"fact-response-{operation_id}",
            request_record_id=request.request_record_id,
            run_id=run_id,
            verifier=self.verifier_execution_id,
            provider_trace_id=raw.provider_trace_id,
            http_status=raw.http_status,
            raw_body=raw.raw_body,
            raw_response_sha256=sha256(raw.raw_body).hexdigest(),
            retrieved_at=raw.retrieved_at,
            cost_usd=raw.cost_usd,
        )
        self.repository.save_business_fact_verification_raw_response(response)

        payload = parse_business_fact_verification_raw(raw.raw_body)
        result = BusinessFactVerificationParsedResultRecord(
            result_record_id=f"fact-result-{operation_id}",
            request_record_id=request.request_record_id,
            response_record_id=response.response_record_id,
            run_id=run_id,
            assertion_id=payload["assertion_id"],
            assertion_sha256=payload["assertion_sha256"],
            decision=payload["decision"],
            parsed_at=datetime.now(timezone.utc),
        )
        self.repository.save_business_fact_verification_result(result)
        reconciliation = BusinessFactVerificationReconciliationRecord(
            receipt_id=f"fact-reconciliation-{operation_id}",
            run_id=run_id,
            assertion_id=assertion.assertion_id,
            request_record_id=request.request_record_id,
            response_record_id=response.response_record_id,
            result_record_id=result.result_record_id,
            governance_bundle_sha256=self.governance_bundle_sha256,
            source_identity_id=source_context.source_identity_id,
            source_version_id=source_context.source_version_id,
            source_context_sha256=source_context_hash,
            reconciled_at=datetime.now(timezone.utc),
        )
        return self.repository.reconcile_business_fact_verification(
            reconciliation,
            assertion=assertion,
        )
