"""Evidence-bound Segment critic and counter-search receipt validation."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from event_collector.theme_chokepoint.providers.counter_search import (
    _in_assessment_window,
    verify_counter_search_route_binding,
)


SUPPLY_DIMENSIONS = {
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
}
HIGH_QUALITY_SOURCE_TYPES = {
    "company_filing",
    "regulatory_filing",
    "official_statistics",
    "customer_filing",
}


class EvidenceBoundSegmentCritic:
    """Derive every non-Ordinal Segment gate from a counter-search receipt."""

    def __init__(self, counter_search_provider):
        self.counter_search_provider = counter_search_provider

    def apply(
        self, *, node, ordinal_draft, claims, evidence_cards, request
    ):
        receipt = self.counter_search_provider.search(
            node=node,
            ordinal_draft=ordinal_draft,
            claims=claims,
            evidence_cards=evidence_cards,
            request=request,
        )
        if receipt.segment_id != node.node_id:
            raise ValueError("Segment critic receipt is bound to another segment")
        if receipt.protocol_version != "segment-counter-search-v1.4":
            raise ValueError("Segment critic receipt protocol is not canonical")
        run_id = getattr(request, "run_id", "").strip()
        assessment_as_of = request.as_of_date.isoformat()
        scope_sha256 = sha256(
            "\0".join((run_id, node.node_id, assessment_as_of, request.region)).encode("utf-8")
        ).hexdigest()
        if (
            not run_id
            or receipt.run_id != run_id
            or receipt.assessment_as_of != assessment_as_of
        ):
            raise ValueError("Segment critic receipt is not bound to the current run window")
        if (
            not receipt.query_log_ids
            or len(set(receipt.query_log_ids)) != len(receipt.query_log_ids)
        ):
            raise ValueError("Segment critic receipt requires unique query logs")
        routes = {route.route_id: route for route in receipt.route_findings}
        required_routes = {"demand", "supply", "alternatives"}
        if len(routes) != len(receipt.route_findings) or set(routes) != required_routes:
            raise ValueError(
                "Segment critic receipt requires three executed counter-search routes"
            )
        reconciliations = {
            item.route_id: item
            for item in self.counter_search_provider.repository.list_reconciled_counter_routes(
                run_id, node.node_id, scope_sha256
            )
        }
        if set(reconciliations) != required_routes:
            raise ValueError("Segment critic requires repository-reconciled counter routes")
        materialized_counter_items = {
            route_id: self.counter_search_provider.repository.load_reconciled_counter_evidence(
                reconciliation.receipt_id
            )
            for route_id, reconciliation in reconciliations.items()
        }
        if (
            receipt.max_queries < len(routes)
            or receipt.max_time_seconds <= 0
            or receipt.max_cost_usd < 0
            or not 0 <= receipt.cost_usd_spent <= receipt.max_cost_usd
            or len(set(receipt.provider_request_receipt_ids))
            != len(receipt.provider_request_receipt_ids)
            or not receipt.provider_request_receipt_ids
        ):
            raise ValueError("Segment critic receipt budget or provider receipts are invalid")
        for route in routes.values():
            if (
                not route.query.strip()
                or route.query_log_id not in receipt.query_log_ids
                or route.provider_request_receipt_id
                not in receipt.provider_request_receipt_ids
                or route.status not in {"supported", "explicit_negative", "unknown"}
                or not route.finding.strip()
                or (route.status == "supported") != bool(route.evidence_ids)
                or route.cost_usd < 0
                or route.executed_at.tzinfo is None
                or route.executed_at > receipt.completed_at
                or not _in_assessment_window(route.executed_at, assessment_as_of)
                or not verify_counter_search_route_binding(
                    route,
                    repository=self.counter_search_provider.repository,
                    run_id=run_id,
                    assessment_as_of=assessment_as_of,
                )
                or reconciliations[route.route_id].receipt_id
                != route.reconciliation_receipt_id
                or reconciliations[route.route_id].new_counter_evidence_ids
                != route.new_counter_evidence_ids
                or route.evidence_ids != route.new_counter_evidence_ids
            ):
                raise ValueError("Segment critic executed counter-search routes are incomplete")

        cards_by_id = {card.evidence_id: card for card in evidence_cards}
        claims_by_id = {claim.claim_id: claim for claim in claims}
        all_receipt_ids = {
            *receipt.demand_evidence_ids,
            *receipt.supply_evidence_ids,
            *receipt.key_source_evidence_ids,
        }
        if not all_receipt_ids <= set(cards_by_id):
            raise ValueError("Segment critic receipt cites unknown evidence")
        dimension_by_name = {
            item.dimension: item for item in ordinal_draft.dimensions
        }

        def direct_primary(evidence_id, allowed_dimensions):
            card = cards_by_id[evidence_id]
            claim = claims_by_id.get(card.claim_id)
            dimension = card.primary_scoring_dimension
            return bool(
                claim
                and card.scoring_eligible
                and card.stance == "supports"
                and claim.scoring_use == "primary"
                and claim.primary_scoring_dimension == dimension
                and dimension in allowed_dimensions
                and evidence_id in dimension_by_name[dimension].evidence_ids
            )

        def route_is_explicit_negative(route_id):
            return (
                reconciliations[route_id].coverage_state == "explicit_negative"
                and not routes[route_id].evidence_ids
                and not materialized_counter_items[route_id]
            )
        demand_direct = (
            bool(receipt.demand_evidence_ids)
            and all(
                direct_primary(evidence_id, {"demand_pressure"})
                for evidence_id in receipt.demand_evidence_ids
            )
            and route_is_explicit_negative("demand")
        )
        supply_direct = (
            bool(receipt.supply_evidence_ids)
            and all(
                direct_primary(evidence_id, SUPPLY_DIMENSIONS)
                for evidence_id in receipt.supply_evidence_ids
            )
            and route_is_explicit_negative("supply")
            and route_is_explicit_negative("alternatives")
        )
        independent_events = {
            event_identity
            for evidence_id in receipt.supply_evidence_ids
            if direct_primary(evidence_id, SUPPLY_DIMENSIONS)
            and not cards_by_id[evidence_id].source_ambiguity
            and (
                event_identity
                := self.counter_search_provider.repository.reconcile_independent_source_event(
                    cards_by_id[evidence_id]
                )
            )
            is not None
        }
        key_source_quality_high = bool(receipt.key_source_evidence_ids) and all(
            cards_by_id[evidence_id].source_type in HIGH_QUALITY_SOURCE_TYPES
            and cards_by_id[evidence_id].scoring_eligible
            and not cards_by_id[evidence_id].source_ambiguity
            for evidence_id in receipt.key_source_evidence_ids
        )
        counter_search_complete = (
            receipt.stop_reason == "protocol_complete"
            and not receipt.unresolved_routes
            and set(routes) == required_routes
            and all(
                (
                    item.coverage_state == "found"
                    and bool(materialized_counter_items[item.route_id])
                )
                or (
                    item.coverage_state == "explicit_negative"
                    and not materialized_counter_items[item.route_id]
                )
                for item in reconciliations.values()
            )
        )
        mandatory_conflict = any(
            item.evidence_state == "conflicted" for item in ordinal_draft.dimensions
        ) or any(
            reconciliation.coverage_state == "found"
            and bool(routes[route_id].evidence_ids)
            for route_id, reconciliation in reconciliations.items()
        )
        return replace(
            ordinal_draft,
            demand_direct_evidence=demand_direct,
            supply_direct_evidence=supply_direct,
            counter_evidence_search_complete=counter_search_complete,
            mandatory_conflict=mandatory_conflict,
            independent_supply_constraint_count=len(independent_events),
            key_source_quality_high=key_source_quality_high,
            critic_receipt=receipt,
        )
