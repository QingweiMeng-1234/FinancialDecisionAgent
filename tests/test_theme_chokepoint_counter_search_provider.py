from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    CounterSearchExecution,
    CounterSearchRawResponseRecord,
    CounterSearchRawProviderResponse,
    CounterSearchRequestRecord,
)
from event_collector.theme_chokepoint.providers.counter_search import (
    EvidenceBoundCounterSearchProvider,
    verify_counter_search_route_binding,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository


class Executor:
    def __init__(self, *, duplicate_receipt=False, executed_at=None):
        self.calls = []
        self.duplicate_receipt = duplicate_receipt
        self.executed_at = executed_at or datetime(2026, 8, 17, tzinfo=timezone.utc)

    def execute(self, *, route_id, query, node, ordinal_draft, claims, evidence_cards, request):
        self.calls.append((route_id, query))
        receipt_id = "provider-receipt" if self.duplicate_receipt else f"provider-{route_id}"
        return CounterSearchExecution(
            route_id=route_id,
            query=query,
            query_log_id=f"query-log-{route_id}",
            provider_request_receipt_id=receipt_id,
            status="explicit_negative" if route_id == "alternatives" else "supported",
            evidence_ids=(f"evidence-{route_id}",),
            finding=f"Executed {route_id} route.",
            executed_at=self.executed_at,
            cost_usd=0.1,
        )


def _inputs():
    node = SimpleNamespace(node_id="segment-ups", normalized_name="qualified ups")
    cards = tuple(
        SimpleNamespace(evidence_id=f"evidence-{route}")
        for route in ("demand", "supply", "alternatives")
    )
    request = SimpleNamespace(
        run_id="run-counter-search-1",
        region="global",
        as_of_date=SimpleNamespace(isoformat=lambda: "2026-08-16"),
    )
    return node, cards, request


class RawExecutor:
    def __init__(self, repository, *, duplicate_receipt=False, executed_at=None):
        self.repository = repository
        self.pre_call_request_counts = []
        self.calls = []
        self.duplicate_receipt = duplicate_receipt
        self.executed_at = executed_at or datetime(2026, 8, 17, tzinfo=timezone.utc)

    def execute(self, *, route_id, query, **_kwargs):
        self.calls.append((route_id, query))
        self.pre_call_request_counts.append(
            len(self.repository.list_counter_search_requests("run-counter-search-1"))
        )
        raw_body = json.dumps(
            {
                "status": "explicit_negative" if route_id == "alternatives" else "supported",
                "evidence_ids": [f"evidence-{route_id}"],
                "coverage_state": (
                    "explicit_negative" if route_id == "alternatives" else "found"
                ),
                "counter_evidence": (
                    []
                    if route_id == "alternatives"
                    else [
                        {
                            "canonical_url": f"https://counter.example/{route_id}",
                            "original_text": f"Counter-search source for {route_id}.",
                            "exact_quote": f"Counter-search source for {route_id}.",
                            "claim_statement": f"Counter-search finding for {route_id}.",
                        }
                    ]
                ),
                "finding": f"Executed {route_id} route.",
                "query_log_id": f"query-log-{route_id}",
            },
            sort_keys=True,
        ).encode("utf-8")
        trace_id = "provider-receipt" if self.duplicate_receipt else f"provider-{route_id}"
        return CounterSearchRawProviderResponse(
            provider="controlled-search",
            provider_trace_id=trace_id,
            http_status=200,
            raw_body=raw_body,
            retrieved_at=self.executed_at,
            cost_usd=0.1,
        )


def test_counter_search_persists_request_raw_response_and_parsed_result_boundaries(
    tmp_path,
):
    """SELECT INVARIANT: execution lineage exists before and after each I/O boundary."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    executor = RawExecutor(repository)
    provider = EvidenceBoundCounterSearchProvider(
        executor,
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )

    assert executor.pre_call_request_counts == [1, 2, 3]
    assert len(repository.list_counter_search_raw_responses(request.run_id)) == 3
    assert len(repository.list_counter_search_results(request.run_id)) == 3
    assert all(route.request_record_id for route in receipt.route_findings)
    assert all(route.response_record_id for route in receipt.route_findings)
    assert all(route.result_record_id for route in receipt.route_findings)


def test_counter_search_rejects_string_only_new_ids_without_materialized_evidence(
    tmp_path,
):
    """SELECT INVARIANT: a parsed string ID is not counter evidence."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    class StringOnlyRawExecutor(RawExecutor):
        def execute(self, **kwargs):
            raw = super().execute(**kwargs)
            payload = json.loads(raw.raw_body.decode("utf-8"))
            payload.pop("counter_evidence")
            payload["new_counter_evidence_ids"] = ["caller-controlled-id"]
            return CounterSearchRawProviderResponse(
                provider=raw.provider,
                provider_trace_id=raw.provider_trace_id,
                http_status=raw.http_status,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=raw.retrieved_at,
                cost_usd=raw.cost_usd,
            )

    provider = EvidenceBoundCounterSearchProvider(
        StringOnlyRawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    with pytest.raises(ValueError, match="materialized counter evidence"):
        provider.search(
            node=node,
            ordinal_draft=SimpleNamespace(),
            claims=(),
            evidence_cards=cards,
            request=request,
        )


def test_counter_search_persists_unknown_coverage_separately_from_explicit_negative(
    tmp_path,
):
    """SELECT INVARIANT: unknown coverage cannot impersonate an explicit negative."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")

    class UnknownAlternativesExecutor(RawExecutor):
        def execute(self, **kwargs):
            raw = super().execute(**kwargs)
            if kwargs["route_id"] != "alternatives":
                return raw
            payload = json.loads(raw.raw_body.decode("utf-8"))
            payload.update(
                {
                    "status": "unknown",
                    "coverage_state": "unknown",
                    "evidence_ids": [],
                    "counter_evidence": [],
                }
            )
            return CounterSearchRawProviderResponse(
                provider=raw.provider,
                provider_trace_id=raw.provider_trace_id,
                http_status=raw.http_status,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=raw.retrieved_at,
                cost_usd=raw.cost_usd,
            )

    provider = EvidenceBoundCounterSearchProvider(
        UnknownAlternativesExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()
    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )

    reconciled = repository.list_reconciled_counter_routes(
        request.run_id,
        node.node_id,
        sha256(
            "\0".join((request.run_id, node.node_id, "2026-08-16", request.region)).encode()
        ).hexdigest(),
    )
    assert {item.route_id: item.coverage_state for item in reconciled} == {
        "demand": "found",
        "supply": "found",
        "alternatives": "unknown",
    }
    alternatives = next(
        item for item in receipt.route_findings if item.route_id == "alternatives"
    )
    assert alternatives.new_counter_evidence_ids == ()


def test_counter_search_reconciliation_detects_raw_response_mutation(tmp_path):
    """SELECT INVARIANT: stored metadata cannot conceal changed provider bytes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()
    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )
    route = receipt.route_findings[0]
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """UPDATE theme_chokepoint_counter_search_raw_responses
            SET raw_body = ? WHERE response_record_id = ?""",
            (b'{"status":"supported","forged":true}', route.response_record_id),
        )

    assert not verify_counter_search_route_binding(
        route,
        repository=repository,
        run_id=request.run_id,
        assessment_as_of="2026-08-16",
    )


def test_counter_search_consumer_rejects_tampered_materialized_claim(tmp_path):
    """SELECT INVARIANT: critic semantics reload the stored Claim/Card/SourceVersion chain."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()
    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )
    demand = next(item for item in receipt.route_findings if item.route_id == "demand")
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """UPDATE theme_chokepoint_counter_claims SET statement = ?
               WHERE result_record_id = ?""",
            ("tampered stored counter claim", demand.result_record_id),
        )

    with pytest.raises(ValueError, match="materialization"):
        repository.load_reconciled_counter_evidence(
            demand.reconciliation_receipt_id
        )


def test_counter_search_repository_rejects_raw_body_hash_mismatch(tmp_path):
    """SELECT INVARIANT: a caller-supplied hash cannot authorize provider bytes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    request = CounterSearchRequestRecord(
        request_record_id="counter-request-hash-mismatch",
        run_id="run-counter-search-hash-mismatch",
        route_id="demand",
        query="demand counter evidence",
        assessment_as_of="2026-08-16",
        created_at=now,
    )
    repository.save_counter_search_request(request)

    with pytest.raises(ValueError, match="raw response SHA-256 mismatch"):
        repository.save_counter_search_raw_response(
            CounterSearchRawResponseRecord(
                response_record_id="counter-response-hash-mismatch",
                request_record_id=request.request_record_id,
                run_id=request.run_id,
                provider="controlled-search",
                provider_trace_id="trace-hash-mismatch",
                http_status=200,
                raw_body=b'{"status":"supported"}',
                raw_response_sha256="0" * 64,
                retrieved_at=now,
                cost_usd=0.0,
            )
        )


def test_counter_search_provider_executes_three_routes_and_emits_transport_receipts(tmp_path):
    """SELECT INVARIANT: counter-search completion is backed by three executed routes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    executor = RawExecutor(repository)
    provider = EvidenceBoundCounterSearchProvider(
        executor,
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )

    assert [item[0] for item in executor.calls] == ["demand", "supply", "alternatives"]
    assert receipt.stop_reason == "protocol_complete"
    assert receipt.unresolved_routes == ()
    assert receipt.provider_request_receipt_ids == (
        "provider-demand",
        "provider-supply",
        "provider-alternatives",
    )
    assert {item.route_id for item in receipt.route_findings} == {
        "demand",
        "supply",
        "alternatives",
    }
    assert receipt.cost_usd_spent == pytest.approx(0.3)


def test_counter_search_provider_rejects_duplicate_transport_receipts(tmp_path):
    """SELECT INVARIANT: one transport receipt cannot impersonate three route executions."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository, duplicate_receipt=True),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    with pytest.raises(ValueError, match="unique provider receipts"):
        provider.search(
            node=node,
            ordinal_draft=SimpleNamespace(),
            claims=(),
            evidence_cards=cards,
            request=request,
        )


def test_counter_search_provider_rejects_cross_run_provider_trace_replay(tmp_path):
    """SELECT INVARIANT: changing the run cannot replay one provider execution."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    node, cards, first_request = _inputs()
    first_provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    first_provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=first_request,
    )
    replay_request = SimpleNamespace(
        run_id="run-counter-search-replay",
        region=first_request.region,
        as_of_date=first_request.as_of_date,
    )
    replay_provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )

    with pytest.raises(ValueError, match="provider execution trace replay"):
        replay_provider.search(
            node=node,
            ordinal_draft=SimpleNamespace(),
            claims=(),
            evidence_cards=cards,
            request=replay_request,
        )


def test_counter_search_provider_rejects_raw_execution_relabelled_with_new_trace(
    tmp_path,
):
    """Regression: changing a local trace cannot replay the same upstream bytes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    node, cards, first_request = _inputs()
    EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    ).search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=first_request,
    )

    class RelabelledRawExecutor(RawExecutor):
        def execute(self, **kwargs):
            raw = super().execute(**kwargs)
            return CounterSearchRawProviderResponse(
                provider=raw.provider,
                provider_trace_id=f"relabeled-{kwargs['route_id']}",
                http_status=raw.http_status,
                raw_body=raw.raw_body,
                retrieved_at=raw.retrieved_at,
                cost_usd=raw.cost_usd,
            )

    replay_request = SimpleNamespace(
        run_id="run-counter-search-relabeled",
        region=first_request.region,
        as_of_date=first_request.as_of_date,
    )
    with pytest.raises(ValueError, match="provider raw execution replay"):
        EvidenceBoundCounterSearchProvider(
            RelabelledRawExecutor(repository),
            repository=repository,
            max_queries=3,
            max_time_seconds=60,
            max_cost_usd=1.0,
        ).search(
            node=node,
            ordinal_draft=SimpleNamespace(),
            claims=(),
            evidence_cards=cards,
            request=replay_request,
        )


def test_counter_search_provider_binds_run_window_and_persisted_record_semantics(tmp_path):
    """SELECT INVARIANT: route-shaped strings are not sufficient execution lineage."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(repository),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    receipt = provider.search(
        node=node,
        ordinal_draft=SimpleNamespace(),
        claims=(),
        evidence_cards=cards,
        request=request,
    )

    assert receipt.run_id == request.run_id
    assert receipt.assessment_as_of == "2026-08-16"
    assert all(route.request_record_id.startswith("counter_request_") for route in receipt.route_findings)
    assert all(route.response_record_id.startswith("counter_response_") for route in receipt.route_findings)
    assert all(route.result_record_id.startswith("counter_result_") for route in receipt.route_findings)
    assert {route.result_semantics for route in receipt.route_findings} == {
        "counter_evidence_found",
        "explicit_negative_search_result",
    }


def test_counter_search_provider_rejects_execution_outside_assessment_window(tmp_path):
    """SELECT INVARIANT: a 1900 execution cannot unlock a current assessment."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    provider = EvidenceBoundCounterSearchProvider(
        RawExecutor(
            repository, executed_at=datetime(1900, 1, 1, tzinfo=timezone.utc)
        ),
        repository=repository,
        max_queries=3,
        max_time_seconds=60,
        max_cost_usd=1.0,
    )
    node, cards, request = _inputs()

    with pytest.raises(ValueError, match="assessment window"):
        provider.search(
            node=node,
            ordinal_draft=SimpleNamespace(),
            claims=(),
            evidence_cards=cards,
            request=request,
        )
