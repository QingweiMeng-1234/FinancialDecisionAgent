import { Agent } from '@mastra/core/agent';
import { z } from 'zod';
import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { createModel, liveConfigured } from './models.js';

const configSchema = z.object({ concurrency: z.number().int().min(1).max(3).default(3), delayMs: z.number().int().min(10).max(5000).default(3000), failB: z.boolean().default(false), backend: z.enum(['fixture', 'live']).default('fixture') }).strict();
export const documents = [
  { companyId: 'A', company: '青岚科技（虚构）', text: 'A 公司模拟财报。2025 年营收为 120 亿元，2024 年为 100 亿元。主要风险：单一大客户贡献较高，存在客户集中风险。' },
  { companyId: 'B', company: '远川制造（虚构）', text: 'B 公司模拟财报。2025 年营收为 180 亿元，2024 年为 200 亿元。主要风险：原材料价格波动可能挤压利润率。' },
  { companyId: 'C', company: '星禾能源（虚构）', text: 'C 公司模拟财报。2025 年营收为 90 亿元，2024 年为 75 亿元。主要风险：新项目审批延期可能影响产能释放。' },
];
const schema = z.object({ companyId: z.enum(['A', 'B', 'C']), revenue: z.number(), unit: z.literal('亿元'), yoyPercent: z.number(), risk: z.string(), evidenceQuote: z.string(), riskQuote: z.string() }).strict();
const instructions = '你是一名财报研究员。只使用当前消息提供的模拟财报，不使用外部知识。输出一个 JSON：companyId、revenue（2025年营收）、unit（亿元）、yoyPercent（同比百分数，非小数比例）、risk（主要风险内容）、evidenceQuote（完整营收原句）、riskQuote（以主要风险开头的完整原句）。引文须逐字匹配，禁止额外字段。不调用工具。';
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
function facts(report) {
  const revenue = report.text.match(/2025 年营收为 (\d+) 亿元，2024 年为 (\d+) 亿元。/);
  const risk = report.text.match(/主要风险：([^。]+)。/);
  if (!revenue || !risk) throw new Error('INVALID_DOCUMENT');
  return { companyId: report.companyId, revenue: Number(revenue[1]), unit: '亿元', yoyPercent: Math.round((Number(revenue[1]) / Number(revenue[2]) - 1) * 10000) / 100, risk: risk[1], evidenceQuote: revenue[0], riskQuote: risk[0] };
}
export function validateResearch(raw, report) {
  const output = schema.parse(raw); const expected = facts(report);
  if (Object.keys(expected).some(key => expected[key] !== output[key])) throw new Error('EVIDENCE_VALIDATION_FAILED');
  return output;
}
export function parseResearchConfig(input) { return configSchema.parse(input); }
function fixtureResponse(prompt) {
  const content = prompt.filter(m => m.role === 'user').at(-1).content;
  const text = typeof content === 'string' ? content : content.filter(p => p.type === 'text').map(p => p.text).join('');
  return facts(JSON.parse(text).report);
}
function requireId(id) { if (!/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/.test(id)) throw new Error('INVALID_RUN_ID'); }

// One service process owns this local ledger. Atomic snapshots survive restart;
// this is not a distributed lease or a claim of independent process failure domains.
export function createResearchService({ directory, env = process.env }) {
  const cache = new Map(); const jobs = new Map(); let writeChain = Promise.resolve(); let mutationBusy = false;
  async function save(run) {
    const bytes = JSON.stringify(run); const filename = join(directory, `${run.id}.json`);
    const operation = writeChain.then(async () => { await mkdir(directory, { recursive: true }); const tmp = `${filename}.${randomUUID()}.tmp`; await writeFile(tmp, bytes); await rename(tmp, filename); });
    writeChain = operation.catch(() => {}); return operation;
  }
  async function load(id) {
    requireId(id);
    if (cache.has(id)) return cache.get(id);
    let run;
    try { run = JSON.parse(await readFile(join(directory, `${id}.json`), 'utf8')); } catch (error) { if (error.code === 'ENOENT') throw new Error('RUN_NOT_FOUND'); throw error; }
    if (run.id !== id || run.version !== 1 || run.documentsSha256 !== hash(documents)) throw new Error('LEDGER_VERSION_MISMATCH');
    if (run.status === 'running' || run.status === 'queued') {
      for (const mode of ['serial', 'parallel']) {
        for (const task of run[mode].tasks) {
          if (task.status === 'running') {
            task.status = 'failed_retryable'; task.attempts.at(-1).error = 'INTERRUPTED'; task.attempts.at(-1).finishedAt = Date.now();
          }
        }
        run[mode].active = 0;
        const phase = run[mode].phases.at(-1); if (phase && !phase.finishedAt) { phase.finishedAt = Date.now(); phase.interrupted = true; }
      }
      run.status = 'partial'; await save(run);
    }
    cache.set(id, run); return run;
  }
  async function execute(run) {
    const signal = AbortSignal.timeout(180000);
    try {
      for (const mode of ['serial', 'parallel']) {
        const arm = run[mode]; const pending = arm.tasks.filter(t => ['pending', 'failed_retryable'].includes(t.status));
        if (!pending.length) continue;
        const phase = { number: arm.phases.length + 1, startedAt: Date.now(), finishedAt: null, taskIds: pending.map(t => t.companyId) };
        arm.phases.push(phase); await save(run);
        function worker() {
          const agentId = `research-${mode}-${randomUUID()}`; let current;
          const model = createModel({ backend: run.config.backend, env, fixtureModelId: 'research-fixture-v1', fixtureResponse,
            beforeCall: (options, modelId) => {
              if (options.tools?.length) throw new Error('TOOLS_FORBIDDEN');
              current.modelId = modelId; current.providerPrompt = structuredClone(options.prompt); current.promptSha256 = hash(current.providerPrompt); current.tools = [];
              return current;
            },
            afterCall: (attempt, result) => { attempt.rawOutput = result?.content?.filter(p => p.type === 'text').map(p => p.text).join('') ?? null; attempt.usage = JSON.parse(JSON.stringify(result?.usage ?? null)); },
            captureWire: body => { current.wireRequests.push(body); },
          });
          const agent = new Agent({ id: agentId, name: '财报研究员', instructions, model, tools: {} });
          return async task => {
            const attempt = { id: randomUUID(), number: task.attempts.length + 1, phase: phase.number, agentId, startedAt: Date.now(), finishedAt: null, error: null, wireRequests: [] };
            current = attempt; task.attempts.push(attempt); task.status = 'running'; arm.active++; arm.peakConcurrency = Math.max(arm.peakConcurrency, arm.active);
            try {
              await save(run);
              // The artificial wait models document retrieval, in BOTH backends.
              await sleep(run.config.delayMs, undefined, { signal });
              if (run.config.failB && task.companyId === 'B' && attempt.number === 1) throw new Error('INJECTED_RETRIEVAL_TIMEOUT');
              const report = run.documents.find(d => d.companyId === task.companyId);
              const output = await agent.generate([{ role: 'user', content: JSON.stringify({ report }) }], { maxSteps: 1, modelSettings: { temperature: 0, maxOutputTokens: 600, maxRetries: 0 }, abortSignal: AbortSignal.any([signal, AbortSignal.timeout(30000)]) });
              task.output = validateResearch(JSON.parse(output.text), report); task.status = 'succeeded';
            } catch (error) {
              attempt.error = error.message === 'INJECTED_RETRIEVAL_TIMEOUT' ? error.message : signal.aborted ? 'RUN_DEADLINE' : 'MODEL_OR_VALIDATION_FAILED';
              task.status = 'failed_retryable';
            } finally { attempt.finishedAt = Date.now(); arm.active--; await save(run); }
          };
        }
        let cursor = 0;
        const count = Math.min(mode === 'serial' ? 1 : run.config.concurrency, pending.length);
        await Promise.all(Array.from({ length: count }, async () => { const perform = worker(); while (cursor < pending.length) { const task = pending[cursor++]; await perform(task); } }));
        phase.finishedAt = Date.now(); await save(run);
      }
      run.status = ['serial', 'parallel'].every(mode => run[mode].tasks.every(t => t.status === 'succeeded')) ? 'completed' : 'partial';
      run.finishedAt = Date.now(); await save(run);
    } catch { run.status = 'error'; run.error = 'LEDGER_OR_EXECUTION_FAILED'; }
  }
  function launch(run) { const job = execute(run).finally(() => jobs.delete(run.id)); jobs.set(run.id, job); }
  return {
    isBusy: () => mutationBusy || jobs.size > 0,
    async start(input) {
      if (mutationBusy || jobs.size) throw new Error('EXPERIMENT_BUSY');
      mutationBusy = true;
      try {
        const config = parseResearchConfig(input);
        if (config.backend === 'live' && !liveConfigured(env)) throw new Error('LIVE_MODEL_NOT_CONFIGURED');
        const arm = () => ({ tasks: documents.map(d => ({ companyId: d.companyId, status: 'pending', output: null, attempts: [] })), active: 0, peakConcurrency: 0, phases: [] });
        const run = { id: randomUUID(), version: 1, config, documents: structuredClone(documents), documentsSha256: hash(documents), instructionsSha256: hash(instructions), createdAt: Date.now(), finishedAt: null, status: 'running', serial: arm(), parallel: arm(), claim: 'scheduling_and_task_recovery_only' };
        await save(run); cache.set(run.id, run); launch(run); return structuredClone(run);
      } finally { mutationBusy = false; }
    },
    async get(id) { return structuredClone(await load(id)); },
    async wait(id) { await jobs.get(id); },
    async retry(id) {
      if (mutationBusy || jobs.size) throw new Error('EXPERIMENT_BUSY');
      mutationBusy = true;
      try {
        const run = await load(id);
        if (!['serial', 'parallel'].some(mode => run[mode].tasks.some(t => ['pending', 'failed_retryable'].includes(t.status)))) throw new Error('NOTHING_TO_RETRY');
        if (run.config.backend === 'live' && !liveConfigured(env)) throw new Error('LIVE_MODEL_NOT_CONFIGURED');
        run.status = 'running'; run.finishedAt = null; await save(run); launch(run); return structuredClone(run);
      } finally { mutationBusy = false; }
    },
  };
}
