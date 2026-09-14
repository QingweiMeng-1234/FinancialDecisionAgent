"""One bounded live staging run. Missing Company services stop execution explicitly.

This operational runner does not promote the staging contract or impersonate
unconfigured providers. All model/search/fetch calls are recorded in its run directory.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import requests

from dotenv import load_dotenv
from openai import OpenAI

from event_collector.theme_chokepoint.contracts import DemandFrame, ProductAnchorDraft, ResearchRequest, RunStatus
from event_collector.theme_chokepoint.governance import CANONICAL_GOVERNANCE_BUNDLE_SHA256
from event_collector.theme_chokepoint.orchestrator import RootStageOrchestrator, NoEventsMonitoringStage
from event_collector.theme_chokepoint.providers import (
    TavilySearchProvider, OriginalTextFetcher, OpenAICompatibleEvidenceSpanExtractor,
    TavilyOriginalEvidenceAcquirer, DeepSeekUpstreamDependencyProposer, DeepSeekV161SegmentFactExtractor,
    EvidenceBoundCounterSearchProvider, EvidenceBoundSegmentCritic,
)
from event_collector.theme_chokepoint.providers.live_counter import OriginalSourceCounterExecutor
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.runtime_v161 import V161SegmentScorer
from event_collector.theme_chokepoint.stage1 import AssistedThemeFramingService
from event_collector.theme_chokepoint.stage2 import SupplyChainGraphService
from event_collector.theme_chokepoint.stage3 import EvidenceChokepointLoop
from event_collector.theme_chokepoint.stage4 import CompanyExposureRedTeamService
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.stage7 import SignalExportService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    run_id = out.name
    started = datetime.now(timezone.utc)
    summary = dict(run_id=run_id, started_at=started.isoformat(), mode='live_staging',
        production_eligible=False, completed_e2e=False, live_monitoring=False,
        live_search_calls=0, original_fetch_calls=0, model_calls=0,
        missing_services=['company_discovery', 'company_evidence', 'company_scoring',
                          'company_critic', 'business_fact_verifier', 'source_identity_resolver'])

    def save(name, value):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    def progress(message):
        print(datetime.now(timezone.utc).isoformat(), message, flush=True)
        save('summary.json', summary)

    save('summary.json', summary)
    try:
        if not os.getenv('TAVILY_API_KEY') or not os.getenv('DEEPSEEK_API_KEY'):
            raise RuntimeError('TAVILY_API_KEY and DEEPSEEK_API_KEY are required')
        real_client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com',
                             timeout=90, max_retries=0)

        class RecordedCompletions:
            def create(self, **kwargs):
                if any(item.get('content', '').startswith('REQUESTED_FIELD:') for item in kwargs.get('messages', [])):
                    kwargs['messages'] = [dict(item) for item in kwargs['messages']]
                    kwargs['messages'][0]['content'] += '\nLive smoke budget: return at most TWO directly relevant spans; preserve missing evidence.'
                summary['model_calls'] += 1
                index = summary['model_calls']
                save(f'model-{index:03d}-request.json', kwargs)
                progress(f'Model request {index}')
                result = real_client.chat.completions.create(**kwargs)
                save(f'model-{index:03d}-response.json', result.model_dump(mode='json'))
                return result

        client = SimpleNamespace(chat=SimpleNamespace(completions=RecordedCompletions()))

        def infer(instruction, data):
            result = client.chat.completions.create(model='deepseek-chat', temperature=0,
                response_format={'type':'json_object'}, messages=[{'role':'system','content':instruction},
                    {'role':'user','content':json.dumps(data, default=str)}])
            return json.loads(result.choices[0].message.content)

        class Framer:
            def frame(self, request):
                data = infer('Normalize this research request without adding evidence or claiming verified facts. '
                    'Return JSON with normalized_theme:string, scope:string, exclusions:string[], '
                    'demand_hypothesis:string, measurable_demand_variables:string[], '
                    'time_horizon_months:integer matching the request, unresolved_questions:string[]. '
                    'Keep the scope narrow enough for one end-to-end engineering smoke run.', asdict(request))
                for key in ('exclusions','measurable_demand_variables','unresolved_questions'):
                    data[key] = tuple(data[key])
                return DemandFrame(**data)

        class Proposer:
            def propose(self, request, frame):
                data = infer('Propose exactly one atomic product anchor from the seed product, as a research '
                    'hypothesis. Return JSON {"anchors":[{product_name:string,buyer_or_user:string,'
                    'demand_variable:string,theme_link:string,confidence:number between 0 and 1,'
                    'supporting_evidence_ids:[],missing_evidence:string[]}]}. No verified evidence has been '
                    'supplied; do not invent evidence IDs.', dict(request=asdict(request),frame=asdict(frame)))
                return [ProductAnchorDraft(**{**row, 'supporting_evidence_ids':tuple(row['supporting_evidence_ids']),
                    'missing_evidence':tuple(row['missing_evidence'])}) for row in data['anchors']]

        class Search(TavilySearchProvider):
            def search(self, query):
                summary['live_search_calls'] += 1
                index = summary['live_search_calls']
                save(f'search-{index:03d}-request.json', {'query':query})
                progress(f'Live search {index}: {query}')
                hits = super().search(query)
                save(f'search-{index:03d}-discovery.json', [asdict(hit) for hit in hits])
                return hits

        class Fetcher(OriginalTextFetcher):
            def fetch(self, hit):
                summary['original_fetch_calls'] += 1
                index = summary['original_fetch_calls']
                progress(f'Original fetch {index}: {hit.url}')
                document = super().fetch(hit)
                save(f'original-{index:03d}.json', asdict(document) if document else {'url':hit.url,'fetched':False})
                return document

        class OriginalSession(requests.Session):
            def get(self, *args, **kwargs):
                response = super().get(*args, **kwargs)
                # Preserve publisher metadata as well as extracted text for date-provenance review.
                index = summary['original_fetch_calls']
                (out/f'original-{index:03d}-http-body.bin').write_bytes(response.content)
                save(f'original-{index:03d}-http-metadata.json', dict(url=response.url,
                    status=response.status_code, content_type=response.headers.get('Content-Type')))
                return response

        class MissingCompanyResearcher:
            def research(self, *args):
                raise RuntimeError('Stage 4 blocked: authenticated Company research and verification services are not configured')

        repo = ThemeChokepointRepository(out/'research.sqlite3')
        search = Search(max_results=3, timeout_seconds=25)
        fetcher = Fetcher(timeout_seconds=20, session=OriginalSession())
        spans = OpenAICompatibleEvidenceSpanExtractor(client=client, model='deepseek-chat', max_document_chars=30000)
        spans.prompt_version += '+live-smoke-two-span-budget'
        extractor = DeepSeekV161SegmentFactExtractor(client=client, model='deepseek-chat')
        counter = EvidenceBoundCounterSearchProvider(OriginalSourceCounterExecutor(search, fetcher, spans),
            repository=repo, max_queries=3, max_time_seconds=900, max_cost_usd=5)
        common = dict(allow_unfrozen_overlay=True,
            expected_governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
            enable_v161_segment_chain=True)
        stage1 = AssistedThemeFramingService(repo, Framer(), Proposer())
        stage2 = SupplyChainGraphService(repo, DeepSeekUpstreamDependencyProposer(client=client,
            model='deepseek-chat', max_candidates_per_node=1))
        stage3 = EvidenceChokepointLoop(repo, TavilyOriginalEvidenceAcquirer(search, fetcher, spans),
            V161SegmentScorer(extractor), critic=EvidenceBoundSegmentCritic(counter), **common)
        stage4 = CompanyExposureRedTeamService(repo, MissingCompanyResearcher(), **common)
        orchestrator = RootStageOrchestrator(repository=repo, stage1=stage1, stage2=stage2, stage3=stage3,
            stage4=stage4, stage5=PersistentResearchProductService(repo,out/'artifacts'),
            stage6=NoEventsMonitoringStage(repo), stage7=SignalExportService(repo,out/'signals'),
            manifest_root=out/'manifests', executable_contract_id=stage3.contract.executable_contract_id,
            executable_contract_sha256=stage3.contract.executable_contract_sha256, contract_id=stage3.contract.version)
        request = ResearchRequest(run_id, 'US large power transformers for incremental data-center electricity demand',
            'User-authorized live E2E after golden calibration and fresh counter-search integration', 'United States',
            started.date(), 12, 'Test a narrow supply-chain chokepoint hypothesis using original sources and counter evidence',
            ('large power transformers',), (), 'assisted', 1, 2, 2, 16, 900, 5.0, 1)
        save('request.json', asdict(request))
        summary['contract_id'] = stage3.contract.version
        summary['executable_contract_sha256'] = stage3.contract.executable_contract_sha256
        progress('Starting live Stage 1; Company services remain explicitly unavailable')
        manifest = orchestrator.start(request)
        if manifest.final_status is RunStatus.AWAITING_PRODUCT_CONFIRMATION:
            stage1.confirm_product_anchors(run_id, anchor_ids=tuple(a.anchor_id for a in repo.get_run(run_id).product_anchors),
                                            confirmed_by='codex-on-user-authorized-e2e-request')
            manifest = orchestrator.continue_run(run_id)
        summary['final_status'] = manifest.final_status.value
        summary['completed_stages'] = [receipt.stage for receipt in manifest.stages]
        summary['completed_e2e'] = manifest.final_status is RunStatus.SIGNAL_EXPORT_READY
        try:
            save('stage3-result.json', asdict(repo.get_stage3_result(run_id)))
        except ValueError:
            pass
    except Exception as error:
        summary['error_type'] = type(error).__name__
        message = str(error)
        for name in ('TAVILY_API_KEY','DEEPSEEK_API_KEY','OPENAI_API_KEY','THEME_CHOKEPOINT_LLM_API_KEY'):
            value = os.getenv(name)
            if value:
                message = message.replace(value, '[REDACTED]')
        summary['error'] = message[:2500]
        summary['final_status'] = 'BLOCKED_OR_FAILED'
        progress(f'Run stopped: {type(error).__name__}: {message[:500]}')
    finally:
        summary['finished_at'] = datetime.now(timezone.utc).isoformat()
        summary['elapsed_seconds'] = (datetime.now(timezone.utc)-started).total_seconds()
        save('summary.json', summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary['completed_e2e'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
