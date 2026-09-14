"""Original-source counter-search transport."""

from datetime import datetime, timezone
import json
from uuid import uuid4

from event_collector.theme_chokepoint.contracts import CounterSearchRawProviderResponse


class OriginalSourceCounterExecutor:
    identity = 'original-source-counter-adapter-v3-scoped-thesis'

    def __init__(self, search, fetcher, extractor):
        self.search, self.fetcher, self.extractor = search, fetcher, extractor

    def execute(self, *, route_id, query, node, ordinal_draft, claims, evidence_cards, request):
        dimension = {'demand': 'demand_pressure', 'supply': 'effective_supply_concentration',
                     'alternatives': 'substitute_weakness'}[route_id]
        thesis = {
            'demand': 'The target segment faces sustained demand pressure transmitted from downstream demand.',
            'supply': 'Effective qualified supply to the target segment is concentrated or constrained.',
            'alternatives': 'Qualified substitutes cannot materially replace the target product in its assessment scope.',
        }[route_id]
        stance_target = dict(thesis=thesis, segment=getattr(node, 'normalized_name', node.node_id),
            downstream_theme=getattr(request, 'theme', None),
            customer_scopes=sorted({card.assessment_scope.customer_or_platform_scope for card in evidence_cards
                if getattr(card, 'assessment_scope', None) is not None}),
            geography=getattr(request, 'region', None), as_of_date=getattr(request, 'as_of_date', None),
            time_horizon_months=getattr(request, 'time_horizon_months', None),
            existing_claims=[claim.statement for claim in claims if claim.primary_scoring_dimension == dimension])
        extraction_query = (query + '\nSTANCE_TARGET_JSON:\n' + json.dumps(stance_target, default=str)
            + '\nClassify stance against this original thesis, not the search phrase. '
            'Use contradicts only for evidence weakening the thesis within its scope. '
            'Growing demand contradicts a slowdown query but SUPPORTS the demand-pressure thesis; '
            'never label it contradicts just because the search asks about slowdown. '
            'A different geography, downstream product class or unquantified possibility is context_only '
            'unless transfer to the specified downstream theme and customer scope is explicit.')
        candidates, retrievals = [], []
        for hit in self.search.search(query):
            document = self.fetcher.fetch(hit)
            retrievals.append({'url': hit.url, 'original_fetched': document is not None})
            if document is None:
                continue
            if (document.publication_date is not None
                    and document.publication_date > request.as_of_date):
                retrievals[-1]['excluded_reason'] = 'publication_after_as_of'
                continue
            for span in self.extractor.extract(document=document, node=node, material_field=dimension, query=extraction_query):
                if span.stance != 'contradicts':
                    continue
                if not span.exact_quote.strip() or span.exact_quote not in document.text:
                    raise ValueError('Counter quotation is absent from the fetched original')
                candidates.append(dict(canonical_url=document.canonical_url, original_text=document.text,
                    exact_quote=span.exact_quote, claim_statement=span.statement, source_title=document.title,
                    publisher=document.publisher, source_type=document.source_type))
        trace = 'counter-adapter-' + uuid4().hex
        payload = dict(query_log_id=trace, status='supported' if candidates else 'unknown',
            coverage_state='found' if candidates else 'unknown', counter_evidence=candidates,
            evidence_ids=[card.evidence_id for card in evidence_cards if card.primary_scoring_dimension == dimension],
            finding='Independent original-source retrieval; unproven negative coverage remains unknown.',
            adapter_provenance=dict(run_id=request.run_id, route_id=route_id, query=query, retrievals=retrievals,
                stance_target=stance_target,
                trace_authority='local_adapter_execution_not_external_search_provider', cost_known=False))
        return CounterSearchRawProviderResponse(provider=self.identity, provider_trace_id=trace,
            http_status=200, raw_body=json.dumps(payload, ensure_ascii=False, default=str).encode(),
            retrieved_at=datetime.now(timezone.utc), cost_usd=0.0)
