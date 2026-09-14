from datetime import date
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest

from event_collector.theme_chokepoint.providers.live_counter import OriginalSourceCounterExecutor
from event_collector.theme_chokepoint.providers.tavily import OriginalDocument, SearchHit


def test_counter_executor_reads_original_and_materializes_only_opposing_spans():
    """SELECT INVARIANT: live search summaries never become counter evidence."""
    calls = []
    hit = SearchHit('https://example.com/original', 'Order cancellation', 'A fabricated search summary.', .9, date(2026, 9, 1))
    original = 'The customer cancelled the turbine order. The remaining orders remain unchanged.'
    doc = OriginalDocument('article-1', hit.url, hit.title, 'publisher', 'original_web', hit.publication_date,
                           original, sha256(original.encode()).hexdigest())
    class Search:
        def search(self, query):
            calls.append(('search', query))
            return (hit,)
    class Fetch:
        def fetch(self, selected):
            calls.append(('fetch', selected.url))
            return doc
    class Extract:
        def extract(self, **kwargs):
            assert kwargs['document'] == doc
            return [SimpleNamespace(stance='contradicts', exact_quote='The customer cancelled the turbine order.',
                                    statement='The turbine order was cancelled.', location='opening paragraph',
                                    limitations='One customer only.')]
    executor = OriginalSourceCounterExecutor(Search(), Fetch(), Extract())
    result = executor.execute(route_id='demand', query='scope cancellation', node=SimpleNamespace(node_id='segment'),
        ordinal_draft=None, claims=(), evidence_cards=(SimpleNamespace(evidence_id='E1', primary_scoring_dimension='demand_pressure'),),
        request=SimpleNamespace(run_id='run', as_of_date=date(2026, 9, 1)))
    payload = json.loads(result.raw_body)
    assert calls == [('search', 'scope cancellation'), ('fetch', hit.url)]
    assert payload['coverage_state'] == 'found'
    assert payload['counter_evidence'][0]['original_text'] == original
    assert 'fabricated' not in result.raw_body.decode()
    assert result.provider == executor.identity


def test_counter_executor_rejects_quote_absent_from_fetched_original():
    """SELECT INVARIANT: a model cannot invent a materialized counter quotation."""
    hit = SearchHit('https://example.com/original', 'Original', '', .9, None)
    search = SimpleNamespace(search=lambda query: (hit,))
    document = OriginalDocument('article', hit.url, 'Title', 'Publisher', 'original_web', None,
                                'The order remains in place.', 'hash')
    fetcher = SimpleNamespace(fetch=lambda hit: document)
    extractor = SimpleNamespace(extract=lambda **kwargs: [SimpleNamespace(stance='contradicts',
        exact_quote='The order was cancelled.', statement='Cancellation.')])
    with pytest.raises(ValueError, match='original'):
        OriginalSourceCounterExecutor(search, fetcher, extractor).execute(route_id='demand', query='scope',
            node=SimpleNamespace(node_id='segment'), ordinal_draft=None, claims=(), evidence_cards=(),
            request=SimpleNamespace(run_id='run'))


def test_counter_executor_keeps_no_hits_unknown_and_propagates_search_failure():
    """Empty search is not proof that counter evidence does not exist."""
    search = SimpleNamespace(search=lambda query: ())
    executor = OriginalSourceCounterExecutor(search, None, None)
    kwargs = dict(route_id='demand', query='scope', node=SimpleNamespace(node_id='segment'),
                  ordinal_draft=None, claims=(), evidence_cards=(), request=SimpleNamespace(run_id='run'))
    payload = json.loads(executor.execute(**kwargs).raw_body)
    assert payload['coverage_state'] == payload['status'] == 'unknown'
    assert payload['counter_evidence'] == []
    def failed(query):
        raise ConnectionError('search unavailable')
    search.search = failed
    with pytest.raises(ConnectionError, match='search unavailable'):
        executor.execute(**kwargs)


def test_counter_executor_keeps_future_source_out_of_as_of_assessment():
    """SELECT INVARIANT: a later publication cannot refute an earlier assessment."""
    hit = SearchHit('https://example.com/original', 'Later report', '', .9, date(2026, 9, 2))
    search = SimpleNamespace(search=lambda query: (hit,))
    document = OriginalDocument('article', hit.url, 'Title', 'Publisher', 'original_web', hit.publication_date,
                                'The order was cancelled.', 'hash')
    fetcher = SimpleNamespace(fetch=lambda hit: document)
    extractor = SimpleNamespace(extract=lambda **kwargs: [SimpleNamespace(stance='contradicts',
        exact_quote='The order was cancelled.', statement='Cancellation.')])
    result = OriginalSourceCounterExecutor(search, fetcher, extractor).execute(route_id='demand', query='scope',
        node=SimpleNamespace(node_id='segment'), ordinal_draft=None, claims=(), evidence_cards=(),
        request=SimpleNamespace(run_id='run', as_of_date=date(2026, 9, 1)))
    payload = json.loads(result.raw_body)
    assert payload['coverage_state'] == 'unknown'
    assert payload['counter_evidence'] == []


def test_production_root_wires_live_counter_from_original_source_acquirer(tmp_path):
    """SELECT INVARIANT: omitting a counter transport installs the real original-source path."""
    from event_collector.theme_chokepoint.production import build_production_theme_chokepoint_runtime
    from event_collector.theme_chokepoint.providers.tavily import TavilyOriginalEvidenceAcquirer
    from tests.test_theme_chokepoint_production_composition import _config, _session_factory
    search, fetcher, extractor = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    runtime = build_production_theme_chokepoint_runtime(_config(tmp_path),
        theme_framer=SimpleNamespace(), product_anchor_proposer=SimpleNamespace(),
        dependency_proposer=SimpleNamespace(), segment_scorer=SimpleNamespace(),
        evidence_acquirer=TavilyOriginalEvidenceAcquirer(search, fetcher, extractor),
        session_factory=_session_factory)
    transport = runtime.stage3.critic.counter_search_provider.executor
    assert isinstance(transport, OriginalSourceCounterExecutor)
    assert (transport.search, transport.fetcher, transport.extractor) == (search, fetcher, extractor)


def test_counter_stance_targets_original_thesis_not_the_negative_search_phrase():
    """SELECT INVARIANT: contradicting 'demand slowdown' is not counterevidence to demand pressure."""
    hit = SearchHit('https://example.com/demand', 'Demand', '', .9, None)
    document = OriginalDocument('article', hit.url, 'Demand', 'Publisher', 'original_web', None,
        'Transformer demand increased.', 'hash')
    queries = []
    def extract(**kwargs):
        queries.append(kwargs['query'])
        anchored = 'STANCE_TARGET_JSON:' in kwargs['query']
        return [SimpleNamespace(stance='supports' if anchored else 'contradicts',
            exact_quote=document.text, statement='Demand increased, opposing a slowdown.')]
    executor = OriginalSourceCounterExecutor(SimpleNamespace(search=lambda query:(hit,)),
        SimpleNamespace(fetch=lambda hit:document), SimpleNamespace(extract=extract))
    result = executor.execute(route_id='demand', query='demand slowdown cancellation',
        node=SimpleNamespace(node_id='segment'), ordinal_draft=None, claims=(), evidence_cards=(),
        request=SimpleNamespace(run_id='run', theme='Large power transformers only'))
    assert json.loads(result.raw_body)['counter_evidence'] == []
    assert 'not the search phrase' in queries[0]
    assert 'Large power transformers only' in queries[0]
