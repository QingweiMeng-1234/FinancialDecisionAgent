"""Executed three-route counter-search provider for the Segment critic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json

from event_collector.theme_chokepoint.contracts import (
    CounterSearchExecution,
    CounterEvidenceCandidate,
    CounterSearchParsedResultRecord,
    CounterSearchRawProviderResponse,
    CounterSearchRawResponseRecord,
    CounterSearchRequestRecord,
    SegmentCounterSearchRoute,
    SegmentCriticReceipt,
)


class EvidenceBoundCounterSearchProvider:
    def __init__(
        self,
        executor,
        *,
        repository=None,
        max_queries: int,
        max_time_seconds: int,
        max_cost_usd: float,
    ):
        if max_queries < 3 or max_time_seconds <= 0 or max_cost_usd < 0:
            raise ValueError("counter-search budgets must permit three executed routes")
        self.executor = executor
        self.repository = repository
        self.max_queries = max_queries
        self.max_time_seconds = max_time_seconds
        self.max_cost_usd = max_cost_usd

    def search(self, *, node, ordinal_draft, claims, evidence_cards, request):
        routes = []
        main_credit_ids_by_route = {}
        card_ids = {card.evidence_id for card in evidence_cards}
        run_id = getattr(request, "run_id", "").strip()
        assessment_as_of = request.as_of_date.isoformat()
        segment_id = node.node_id.strip()
        scope_sha256 = sha256(
            "\0".join((run_id, segment_id, assessment_as_of, request.region)).encode("utf-8")
        ).hexdigest()
        if not run_id:
            raise ValueError("counter-search requires current run binding")
        if self.repository is None:
            raise ValueError("counter-search requires a durable execution repository")
        for route_id, query in _queries(node, request):
            request_record = CounterSearchRequestRecord(
                request_record_id=_record_id(
                    "counter_request", run_id, assessment_as_of, route_id, query
                ),
                run_id=run_id,
                route_id=route_id,
                query=query,
                assessment_as_of=assessment_as_of,
                created_at=datetime.now(timezone.utc),
                segment_id=segment_id,
                assessment_scope_sha256=scope_sha256,
            )
            self.repository.save_counter_search_request(request_record)
            raw = self.executor.execute(
                route_id=route_id,
                query=query,
                node=node,
                ordinal_draft=ordinal_draft,
                claims=claims,
                evidence_cards=evidence_cards,
                request=request,
            )
            if not isinstance(raw, CounterSearchRawProviderResponse):
                raise ValueError("counter-search transport must return raw provider response")
            if (
                not raw.provider.strip()
                or not raw.provider_trace_id.strip()
                or raw.http_status < 200
                or raw.http_status >= 300
                or not raw.raw_body
                or raw.retrieved_at.tzinfo is None
                or raw.retrieved_at.utcoffset() is None
                or raw.cost_usd < 0
            ):
                raise ValueError("counter-search raw provider response is incomplete")
            if raw.provider_trace_id in {
                item.provider_request_receipt_id for item in routes
            }:
                raise ValueError("counter-search routes require unique provider receipts")
            raw_hash = sha256(raw.raw_body).hexdigest()
            response_record = CounterSearchRawResponseRecord(
                response_record_id=_record_id(
                    "counter_response",
                    request_record.request_record_id,
                    raw.provider,
                    raw.provider_trace_id,
                    raw_hash,
                ),
                request_record_id=request_record.request_record_id,
                run_id=run_id,
                provider=raw.provider,
                provider_trace_id=raw.provider_trace_id,
                http_status=raw.http_status,
                raw_body=raw.raw_body,
                raw_response_sha256=raw_hash,
                retrieved_at=raw.retrieved_at,
                cost_usd=raw.cost_usd,
            )
            self.repository.save_counter_search_raw_response(response_record)
            execution, candidates, coverage_state = _parse_counter_search_response(
                raw, route_id=route_id, query=query
            )
            expected_coverage_state = {
                "supported": "found",
                "explicit_negative": "explicit_negative",
                "unknown": "unknown",
            }.get(execution.status)
            if coverage_state != expected_coverage_state:
                raise ValueError("counter-search coverage state does not match result")
            if (
                not execution.query_log_id.strip()
                or not execution.provider_request_receipt_id.strip()
                or execution.status not in {"supported", "explicit_negative", "unknown"}
                or not execution.finding.strip()
                or (execution.status != "unknown" and not execution.evidence_ids)
                or not set(execution.evidence_ids) <= card_ids
                or execution.executed_at.tzinfo is None
                or execution.executed_at.utcoffset() is None
                or execution.cost_usd < 0
            ):
                raise ValueError("counter-search execution is incomplete")
            if not _in_assessment_window(execution.executed_at, assessment_as_of):
                raise ValueError("counter-search execution is outside the assessment window")
            main_credit_ids_by_route[route_id] = execution.evidence_ids
            result_semantics = {
                "supported": "counter_evidence_found",
                "explicit_negative": "explicit_negative_search_result",
                "unknown": "unknown_counter_coverage",
            }[execution.status]
            provisional_result = CounterSearchParsedResultRecord(
                result_record_id=_record_id(
                    "counter_result",
                    response_record.response_record_id,
                    execution.status,
                    result_semantics,
                    execution.finding,
                ),
                response_record_id=response_record.response_record_id,
                request_record_id=request_record.request_record_id,
                run_id=run_id,
                route_id=route_id,
                query=query,
                status=execution.status,
                evidence_ids=execution.evidence_ids,
                new_counter_evidence_ids=(),
                finding=execution.finding,
                query_log_id=execution.query_log_id,
                result_semantics=result_semantics,
                parsed_at=datetime.now(timezone.utc),
                coverage_state={
                    "supported": "found",
                    "explicit_negative": "explicit_negative",
                    "unknown": "unknown",
                }[execution.status],
                segment_id=segment_id,
                assessment_scope_sha256=scope_sha256,
            )
            new_counter_evidence_ids = self.repository.derive_counter_evidence_ids(
                provisional_result, candidates
            )
            if set(new_counter_evidence_ids) & card_ids:
                raise ValueError("materialized counter evidence cannot reuse existing cards")
            result_record = CounterSearchParsedResultRecord(
                **{
                    **provisional_result.__dict__,
                    "new_counter_evidence_ids": new_counter_evidence_ids,
                }
            )
            self.repository.save_counter_search_result(result_record)
            self.repository.materialize_counter_evidence(
                result=result_record, candidates=candidates, retrieved_at=raw.retrieved_at
            )
            reconciliation = self.repository.reconcile_counter_search_route(result_record)
            routes.append(
                SegmentCounterSearchRoute(
                    route_id=route_id,
                    query=query,
                    query_log_id=execution.query_log_id,
                    provider_request_receipt_id=raw.provider_trace_id,
                    status=execution.status,
                    evidence_ids=reconciliation.new_counter_evidence_ids,
                    finding=execution.finding,
                    executed_at=raw.retrieved_at,
                    cost_usd=raw.cost_usd,
                    run_id=run_id,
                    assessment_as_of=assessment_as_of,
                    request_record_id=request_record.request_record_id,
                    response_record_id=response_record.response_record_id,
                    result_record_id=result_record.result_record_id,
                    result_semantics=result_semantics,
                    new_counter_evidence_ids=reconciliation.new_counter_evidence_ids,
                    reconciliation_receipt_id=reconciliation.receipt_id,
                )
            )
        provider_receipts = tuple(
            item.provider_request_receipt_id for item in routes
        )
        if len(set(provider_receipts)) != len(provider_receipts):
            raise ValueError("counter-search routes require unique provider receipts")
        query_logs = tuple(item.query_log_id for item in routes)
        if len(set(query_logs)) != len(query_logs):
            raise ValueError("counter-search routes require unique query logs")
        cost = round(sum(item.cost_usd for item in routes), 6)
        if cost > self.max_cost_usd:
            raise ValueError("counter-search execution exceeds cost budget")
        completed_at = max(item.executed_at for item in routes)
        by_route = {item.route_id: item for item in routes}
        return SegmentCriticReceipt(
            segment_id=node.node_id,
            protocol_version="segment-counter-search-v1.4",
            query_log_ids=query_logs,
            stop_reason="protocol_complete",
            unresolved_routes=(),
            demand_evidence_ids=main_credit_ids_by_route["demand"],
            supply_evidence_ids=main_credit_ids_by_route["supply"],
            key_source_evidence_ids=tuple(
                dict.fromkeys(
                    (
                        *main_credit_ids_by_route["demand"],
                        *main_credit_ids_by_route["supply"],
                    )
                )
            ),
            completed_at=completed_at,
            route_findings=tuple(routes),
            provider_request_receipt_ids=provider_receipts,
            max_queries=self.max_queries,
            max_time_seconds=self.max_time_seconds,
            max_cost_usd=self.max_cost_usd,
            cost_usd_spent=cost,
            run_id=run_id,
            assessment_as_of=assessment_as_of,
        )


def _parse_counter_search_response(raw, *, route_id, query):
    try:
        payload = json.loads(raw.raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("counter-search raw response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("counter-search raw response must be an object")
    try:
        candidates = tuple(
            CounterEvidenceCandidate(
                canonical_url=str(item["canonical_url"]),
                original_text=str(item["original_text"]),
                exact_quote=str(item["exact_quote"]),
                claim_statement=str(item["claim_statement"]),
                source_title=str(item.get("source_title", "")),
                publisher=str(item.get("publisher", "")),
                source_type=str(item.get("source_type", "counter_search")),
            )
            for item in payload.get("counter_evidence", ())
        )
    except (KeyError, TypeError) as error:
        raise ValueError("counter-search materialized evidence payload is invalid") from error
    execution = CounterSearchExecution(
        route_id=route_id,
        query=query,
        query_log_id=str(payload.get("query_log_id", "")),
        provider_request_receipt_id=raw.provider_trace_id,
        status=str(payload.get("status", "")),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        finding=str(payload.get("finding", "")),
        executed_at=raw.retrieved_at,
        cost_usd=raw.cost_usd,
    )
    return execution, candidates, str(payload.get("coverage_state", ""))


def _queries(node, request):
    prefix = f'"{node.normalized_name}" {request.region} as of {request.as_of_date.isoformat()}'
    return (
        ("demand", f"{prefix} demand slowdown inventory cancellation original source"),
        ("supply", f"{prefix} excess capacity lead-time normalization original source"),
        ("alternatives", f"{prefix} qualified alternative substitute failure original source"),
    )


def bind_counter_search_route(execution, *, run_id, assessment_as_of):
    result_semantics = (
        "explicit_negative_search_result"
        if execution.status == "explicit_negative"
        else "counter_evidence_found"
    )
    request_record_id = _record_id(
        "counter_request",
        run_id,
        assessment_as_of,
        execution.route_id,
        execution.query,
    )
    response_record_id = _record_id(
        "counter_response",
        request_record_id,
        execution.provider_request_receipt_id,
        execution.executed_at.isoformat(),
        str(execution.cost_usd),
    )
    result_record_id = _record_id(
        "counter_result",
        response_record_id,
        execution.status,
        result_semantics,
        *execution.evidence_ids,
        execution.finding,
    )
    return SegmentCounterSearchRoute(
        route_id=execution.route_id,
        query=execution.query,
        query_log_id=execution.query_log_id,
        provider_request_receipt_id=execution.provider_request_receipt_id,
        status=execution.status,
        evidence_ids=execution.evidence_ids,
        finding=execution.finding,
        executed_at=execution.executed_at,
        cost_usd=execution.cost_usd,
        run_id=run_id,
        assessment_as_of=assessment_as_of,
        request_record_id=request_record_id,
        response_record_id=response_record_id,
        result_record_id=result_record_id,
        result_semantics=result_semantics,
    )


def verify_counter_search_route_binding(
    route, *, repository, run_id, assessment_as_of
):
    """Reconcile a route against records created at the three execution boundaries."""
    requests = {
        item.request_record_id: item
        for item in repository.list_counter_search_requests(run_id)
    }
    responses = {
        item.response_record_id: item
        for item in repository.list_counter_search_raw_responses(run_id)
    }
    results = {
        item.result_record_id: item
        for item in repository.list_counter_search_results(run_id)
    }
    request = requests.get(route.request_record_id)
    response = responses.get(route.response_record_id)
    result = results.get(route.result_record_id)
    if request is None or response is None or result is None:
        return False
    if (
        request.run_id != run_id
        or request.route_id != route.route_id
        or request.query != route.query
        or request.assessment_as_of != assessment_as_of
        or response.request_record_id != request.request_record_id
        or response.run_id != run_id
        or response.provider_trace_id != route.provider_request_receipt_id
        or response.raw_response_sha256 != sha256(response.raw_body).hexdigest()
        or result.response_record_id != response.response_record_id
        or result.request_record_id != request.request_record_id
        or result.run_id != run_id
        or result.route_id != route.route_id
        or result.query != route.query
    ):
        return False
    try:
        parsed, candidates, coverage_state = _parse_counter_search_response(
            CounterSearchRawProviderResponse(
                provider=response.provider,
                provider_trace_id=response.provider_trace_id,
                http_status=response.http_status,
                raw_body=response.raw_body,
                retrieved_at=response.retrieved_at,
                cost_usd=response.cost_usd,
            ),
            route_id=request.route_id,
            query=request.query,
        )
    except ValueError:
        return False
    return (
        result.status == route.status == parsed.status
        and result.evidence_ids == parsed.evidence_ids
        and result.finding == route.finding == parsed.finding
        and result.query_log_id == route.query_log_id == parsed.query_log_id
        and result.result_semantics == route.result_semantics
        and result.coverage_state == coverage_state
        and result.new_counter_evidence_ids
        == repository.derive_counter_evidence_ids(result, candidates)
        and route.evidence_ids == result.new_counter_evidence_ids
        and route.new_counter_evidence_ids == result.new_counter_evidence_ids
    )


def _in_assessment_window(executed_at, assessment_as_of):
    if executed_at.tzinfo is None or executed_at.utcoffset() is None:
        return False
    as_of = date.fromisoformat(assessment_as_of)
    today = datetime.now(timezone.utc).date()
    return as_of <= executed_at.date() <= today


def _record_id(prefix, *parts):
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"
