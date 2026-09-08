from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from event_collector.theme_chokepoint.contracts import (
    BusinessFactVerificationParsedResultRecord,
    BusinessFactVerificationRawResponseRecord,
    BusinessFactVerificationRawProviderResponse,
    BusinessFactVerificationReconciliationRecord,
    BusinessFactVerificationRequestRecord,
)
from event_collector.theme_chokepoint.fact_verification import (
    business_fact_assertion_sha256,
    business_fact_verification_source_context_sha256,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.providers.fact_verifier import (
    EvidenceBoundBusinessFactVerifier,
)
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceResolutionRawResponse,
)
from event_collector.theme_chokepoint.stage4 import _authorized_claim_capabilities
from test_theme_chokepoint_stage4 import _ready_stage3, _verified_assertion


GOVERNANCE_BUNDLE_SHA256 = (
    "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
)


class _ControlledSourceResolutionClient:
    def resolve(self, **kwargs):
        payload = {
            "observed_url": kwargs["source_url"],
            "terminal_url": kwargs["source_url"],
            "redirect_chain": [],
            "relation": "original",
            "origin_url": None,
            "resolution_status": "verified",
        }
        return SourceResolutionRawResponse(
            provider="controlled-fact-source-resolver",
            provider_trace_id=(
                "fact-source-resolution-"
                + sha256(kwargs["source_url"].encode("utf-8")).hexdigest()[:16]
            ),
            http_status=200,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
            retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        )


def _subject(repository):
    request = _ready_stage3(repository, persist_fact_verifications=False)
    result = repository.get_stage3_result(request.run_id)
    capability = "production_use_confirmed"
    card = next(
        item for item in result.evidence_cards if capability in item.claim_capabilities
    )
    claim = next(item for item in result.claims if item.claim_id == card.claim_id)
    assertion = _verified_assertion(
        card,
        capability,
        company_id="challengerco",
        product_id="ups-y",
    )
    card = replace(card, business_fact_assertions=(assertion,))
    return request.run_id, capability, claim, card, assertion


def _persist_source_context(repository, run_id, assertion):
    stage3 = repository.get_stage3_result(run_id)
    card = next(
        item for item in stage3.evidence_cards if item.evidence_id == assertion.evidence_id
    )
    snapshot = next(
        item for item in stage3.source_snapshots if item.article_id == card.article_id
    )
    identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(
            _ControlledSourceResolutionClient()
        ).resolve(card.canonical_url),
        canonical_publisher_id=f"issuer:{assertion.subject_company_id}",
        canonical_document_id=card.article_id,
        origin_event_id=card.origin_event_id,
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        raw_bytes=snapshot.original_text.encode("utf-8"),
        normalized_content=snapshot.original_text,
        quote_span=(card.quote_start, card.quote_end, card.exact_quote),
        content_type="text/plain",
        language="en",
        publication_time=None,
        updated_time=None,
    )
    context = repository.load_business_fact_verification_source_context(
        source_identity_id=identity.source_identity_id,
        source_version_id=version.source_version_id,
        source_type=card.source_type,
        issuer_company_id=assertion.subject_company_id,
        product_id=assertion.subject_product_id,
        fiscal_period_id=assertion.fiscal_period_id,
        accounting_metric=assertion.accounting_metric,
        quote_start=assertion.quote_start,
        quote_end=assertion.quote_end,
        exact_quote_sha256=assertion.exact_quote_sha256,
    )
    return identity, version, context


def _persist_verification(repository, run_id, assertion, *, same_actor=False):
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    assertion_sha256 = business_fact_assertion_sha256(assertion)
    _, version, source_context = _persist_source_context(repository, run_id, assertion)
    source_context_sha256 = business_fact_verification_source_context_sha256(
        source_context
    )
    request = BusinessFactVerificationRequestRecord(
        request_record_id=f"fact-request-{assertion.assertion_id}",
        run_id=run_id,
        assertion_id=assertion.assertion_id,
        evidence_id=assertion.evidence_id,
        assertion_sha256=assertion_sha256,
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
        producer_execution_id="company-evidence-producer-1",
        verifier_execution_id=(
            "company-evidence-producer-1" if same_actor else "fact-verifier-1"
        ),
        source_context=source_context,
        source_context_sha256=source_context_sha256,
        created_at=now,
    )
    raw_body = json.dumps(
        {
            "assertion_id": assertion.assertion_id,
            "assertion_sha256": assertion_sha256,
            "decision": "verified",
        },
        sort_keys=True,
    ).encode("utf-8")
    response = BusinessFactVerificationRawResponseRecord(
        response_record_id=f"fact-response-{assertion.assertion_id}",
        request_record_id=request.request_record_id,
        run_id=run_id,
        verifier=request.verifier_execution_id,
        provider_trace_id=f"fact-trace-{assertion.assertion_id}",
        http_status=200,
        raw_body=raw_body,
        raw_response_sha256=sha256(raw_body).hexdigest(),
        retrieved_at=now,
        cost_usd=0.0,
    )
    result = BusinessFactVerificationParsedResultRecord(
        result_record_id=f"fact-result-{assertion.assertion_id}",
        request_record_id=request.request_record_id,
        response_record_id=response.response_record_id,
        run_id=run_id,
        assertion_id=assertion.assertion_id,
        assertion_sha256=assertion_sha256,
        decision="verified",
        parsed_at=now,
    )
    repository.save_business_fact_verification_request(request)
    repository.save_business_fact_verification_raw_response(response)
    repository.save_business_fact_verification_result(result)
    reconciliation = repository.reconcile_business_fact_verification(
        BusinessFactVerificationReconciliationRecord(
            receipt_id=f"fact-reconciliation-{assertion.assertion_id}",
            run_id=run_id,
            assertion_id=assertion.assertion_id,
            request_record_id=request.request_record_id,
            response_record_id=response.response_record_id,
            result_record_id=result.result_record_id,
            governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
            source_identity_id=source_context.source_identity_id,
            source_version_id=source_context.source_version_id,
            source_context_sha256=source_context_sha256,
            reconciled_at=now,
        ),
        assertion=assertion,
    )
    return reconciliation, version


def _node_authorizes(card, capability, assertion, repository, run_id):
    validator_uri = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "theme-chokepoint"
        / "semantic-validator-v1.4.mjs"
    ).as_uri()
    payload = {
        "evidence": {
            **asdict(card),
            "business_fact_assertions": [asdict(assertion)],
        },
        "capability": capability,
        "repositoryPath": str(repository.db_path),
        "runId": run_id,
        "evidencePackId": f"theme-run:{run_id}",
        "runtimeGovernanceBundlePath": str(
            Path(__file__).resolve().parents[1]
            / "tools"
            / "theme-chokepoint"
            / "runtime-governance-bundle-v1.json"
        ),
    }
    script = (
        f'import {{ evidenceAuthorizesBusinessCapability, loadRepositoryVerificationAuthority }} from "{validator_uri}";'
        "let input = '';"
        "for await (const chunk of process.stdin) input += chunk;"
        "const payload = JSON.parse(input);"
        "const verificationAuthority = loadRepositoryVerificationAuthority({"
        "repositoryPath: payload.repositoryPath, runId: payload.runId,"
        "evidencePackId: payload.evidencePackId,"
        "runtimeGovernanceBundlePath: payload.runtimeGovernanceBundlePath,"
        "expectedRuntimeGovernanceBundleId: 'theme-chokepoint-evidence-trust-runtime-v1',"
        f"expectedRuntimeGovernanceBundleSha256: '{GOVERNANCE_BUNDLE_SHA256}',"
        "});"
        "process.stdout.write(String(evidenceAuthorizesBusinessCapability({"
        "evidence: payload.evidence, capability: payload.capability,"
        "verificationAuthority," 
        "})));"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        input=json.dumps(payload, default=str),
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout == "true"


def _verification_ledger(repository, run_id):
    return repository.export_business_fact_verification_ledger(
        run_id=run_id,
        evidence_pack_id=f"theme-run:{run_id}",
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )


def test_assertion_producer_receipt_cannot_self_authorize(tmp_path):
    """SELECT INVARIANT: nested producer metadata is never verification authority."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, capability, claim, card, _ = _subject(repository)

    assert capability not in _authorized_claim_capabilities(
        claim,
        card,
        repository=repository,
        run_id=run_id,
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )


def test_persisted_independent_verification_authorizes_exact_assertion(tmp_path):
    """SELECT INVARIANT: authority requires request/raw/result/reconciliation reload."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, capability, claim, card, assertion = _subject(repository)
    _persist_verification(repository, run_id, assertion)

    assert capability in _authorized_claim_capabilities(
        claim,
        card,
        repository=repository,
        run_id=run_id,
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )


def test_python_node_business_fact_authorization_differential_matrix(tmp_path):
    """SELECT INVARIANT: Python and Node share one fail-closed fact decision."""
    cases = (
        ("positive", {}, True, True),
        ("negative", {"polarity": "negative"}, True, False),
        ("unknown", {"polarity": "unknown"}, True, False),
        ("conflicted", {"polarity": "conflicted"}, True, False),
        ("missing", {}, False, False),
        ("hash_mismatch", {"exact_quote_sha256": "0" * 64}, True, False),
    )
    for name, mutation, persist, expected in cases:
        repository = ThemeChokepointRepository(tmp_path / name / "theme.db")
        run_id, capability, claim, card, original = _subject(repository)
        if persist:
            _persist_verification(repository, run_id, original)
        assertion = replace(original, **mutation)
        candidate_card = replace(card, business_fact_assertions=(assertion,))
        python_authorized = capability in _authorized_claim_capabilities(
            claim,
            candidate_card,
            repository=repository,
            run_id=run_id,
            governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
        )
        node_authorized = _node_authorizes(
            candidate_card,
            capability,
            assertion,
            repository,
            run_id,
        )

        assert python_authorized is expected, name
        assert node_authorized is expected, name


def test_verifier_cannot_share_producer_execution_identity(tmp_path):
    """SELECT INVARIANT: persistence does not make self-verification independent."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, _, _, _, assertion = _subject(repository)

    try:
        _persist_verification(repository, run_id, assertion, same_actor=True)
    except ValueError as error:
        assert "independent" in str(error)
    else:
        raise AssertionError("same producer/verifier identity was accepted")


def test_verifier_service_persists_request_before_low_level_io(tmp_path):
    """SELECT INVARIANT: completed verifier objects cannot be injected by a producer."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, capability, claim, card, assertion = _subject(repository)
    identity, version, expected_source_context = _persist_source_context(
        repository, run_id, assertion
    )

    class RecordingRawVerifierClient:
        def verify_fact(
            self, *, request_record_id, assertion_sha256, source_context, **_kwargs
        ):
            persisted = repository.get_business_fact_verification_request(
                request_record_id
            )
            assert persisted.assertion_id == assertion.assertion_id
            assert persisted.source_context == source_context == expected_source_context
            assert source_context.original_document_bytes == (
                source_context.original_document_text.encode("utf-8")
            )
            assert source_context.exact_quote == card.exact_quote
            assert source_context.issuer_company_id == assertion.subject_company_id
            assert source_context.product_id == assertion.subject_product_id
            raw_body = json.dumps(
                {
                    "assertion_id": assertion.assertion_id,
                    "assertion_sha256": assertion_sha256,
                    "decision": "verified",
                },
                sort_keys=True,
            ).encode("utf-8")
            return BusinessFactVerificationRawProviderResponse(
                provider_trace_id="independent-verifier-trace-1",
                http_status=200,
                raw_body=raw_body,
                retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
                cost_usd=0.01,
            )

    verifier = EvidenceBoundBusinessFactVerifier(
        repository,
        RecordingRawVerifierClient(),
        verifier_execution_id="independent-verifier-runtime-1",
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )
    verifier.verify(
        run_id=run_id,
        assertion=assertion,
        producer_execution_id="company-evidence-producer-1",
        source_identity_id=identity.source_identity_id,
        source_version_id=version.source_version_id,
        source_type=card.source_type,
    )

    assert capability in _authorized_claim_capabilities(
        claim,
        card,
        repository=repository,
        run_id=run_id,
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )


def test_verifier_rejects_negated_source_text_before_raw_client_io(tmp_path):
    """SELECT INVARIANT: callers cannot bypass writer-level negation checks."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, _, _, card, assertion = _subject(repository)
    identity, _, _ = _persist_source_context(repository, run_id, assertion)
    negated_quote = (
        "ChallengerCo has not confirmed production use for UPS-Y."
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 17, 1, tzinfo=timezone.utc),
        raw_bytes=negated_quote.encode("utf-8"),
        normalized_content=negated_quote,
        quote_span=(0, len(negated_quote), negated_quote),
        content_type="text/plain",
        language="en",
        publication_time=None,
        updated_time=None,
    )
    negated_assertion = replace(
        assertion,
        assertion_id=assertion.assertion_id + "-negated-bypass",
        quote_start=0,
        quote_end=len(negated_quote),
        exact_quote_sha256=sha256(negated_quote.encode("utf-8")).hexdigest(),
    )

    class RecordingRawClient:
        calls = 0

        def verify_fact(self, **_kwargs):
            self.calls += 1
            raise AssertionError("negated verifier input reached raw I/O")

    raw_client = RecordingRawClient()
    verifier = EvidenceBoundBusinessFactVerifier(
        repository,
        raw_client,
        verifier_execution_id="independent-negation-verifier",
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )

    with pytest.raises(ValueError, match="not affirmative"):
        verifier.verify(
            run_id=run_id,
            assertion=negated_assertion,
            producer_execution_id="company-evidence-producer-1",
            source_identity_id=identity.source_identity_id,
            source_version_id=version.source_version_id,
            source_type=card.source_type,
        )
    assert raw_client.calls == 0


def test_verification_consumer_fails_closed_after_source_document_hash_drift(tmp_path):
    """SELECT INVARIANT: a refreshed/broken source version invalidates old authority."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    run_id, capability, claim, card, assertion = _subject(repository)
    _, version = _persist_verification(repository, run_id, assertion)

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """UPDATE theme_chokepoint_source_versions
               SET raw_bytes = ? WHERE source_version_id = ?""",
            (b"tampered or refreshed document", version.source_version_id),
        )

    assert capability not in _authorized_claim_capabilities(
        claim,
        card,
        repository=repository,
        run_id=run_id,
        governance_bundle_sha256=GOVERNANCE_BUNDLE_SHA256,
    )
    with pytest.raises(ValueError, match="source|lineage|reconciliation"):
        _verification_ledger(repository, run_id)
