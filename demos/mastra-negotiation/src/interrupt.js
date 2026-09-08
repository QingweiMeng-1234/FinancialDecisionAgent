import { Agent } from '@mastra/core/agent';
import { z } from 'zod';
import { randomUUID, createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { setTimeout as sleep } from 'node:timers/promises';
import { createModel } from './models.js';

const configSchema = z.object({
  researchMs: z.number().int().min(100).max(60000).default(60000),
  stepMs: z.number().int().min(10).max(10000).default(1000),
  alertMs: z.number().int().min(10).max(3000).default(1000),
  autoAfterMs: z.number().int().min(10).default(10000),
  deadlineMs: z.number().int().min(10).max(10000).default(5000),
}).strict().refine(c => c.researchMs % c.stepMs === 0 && c.researchMs / c.stepMs <= 120 && c.autoAfterMs < c.researchMs, 'Invalid checkpoint or event timing');
export function parseInterruptConfig(input) { return configSchema.parse(input); }
const instructions = '你处理两类模拟任务。research：只输出 JSON {step, finding}，finding 为“主题研究片段 N”（N 为当前 step）。alert：只输出 JSON {risk:"earnings_warning",quote}，quote 逐字复制 announcement。不做投资建议，不调用工具。';
const announcement = '青岚科技（虚构）公告：预计本年度净利润同比下降 40%–50%。';
function expected(packet) { return packet.kind === 'alert' ? { risk: 'earnings_warning', quote: packet.announcement } : { step: packet.step, finding: `主题研究片段 ${packet.step}` }; }
function fixture(prompt) {
  const content = prompt.filter(m => m.role === 'user').at(-1).content;
  return expected(JSON.parse(typeof content === 'string' ? content : content.filter(p => p.type === 'text').map(p => p.text).join('')));
}
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

// Scheduling demo only: same-process async workers and in-memory checkpoints.
// No paid model, OS preemption, process isolation, or durable restart recovery.
export function createInterruptService() {
  const runs = new Map(); const jobs = new Map(); const injectors = new Map();
  function get(id) { const run = runs.get(id); if (!run) throw new Error('RUN_NOT_FOUND'); return run; }
  async function execute(run) {
    const origin = performance.now(); const now = () => run.startedAt + Math.round(performance.now() - origin);
    const signal = AbortSignal.timeout(90000);
    let releaseEvent;
    const eventReady = new Promise(resolve => { releaseEvent = resolve; });
    const inject = source => {
      if (run.event) throw new Error('EVENT_ALREADY_SENT');
      if (run.status !== 'running') throw new Error('RUN_NOT_RUNNING');
      run.event = { at: now(), source, announcement }; clearTimeout(timer); releaseEvent(run.event);
    };
    const timer = setTimeout(() => inject('auto'), run.config.autoAfterMs);
    injectors.set(run.id, inject);
    function actor(arm) {
      const agentId = randomUUID(); let current;
      const model = createModel({ backend: 'fixture', fixtureResponse: fixture, fixtureModelId: 'interrupt-fixture-v1',
        beforeCall(options, modelId) {
          current.providerPrompt = structuredClone(options.prompt); current.promptSha256 = hash(current.providerPrompt); current.modelId = modelId;
          return current;
        },
        afterCall(receipt, result, error) { receipt.rawOutput = result?.content?.filter(c => c.type === 'text').map(c => c.text).join('') ?? null; if (error) receipt.error = error.message; },
      });
      const agent = new Agent({ id: agentId, name: '研究与事件处理员', instructions, model, tools: {} });
      return async (packet, delayMs) => {
        const record = { kind: packet.kind, step: packet.step ?? null, agentId, startedAt: now(), finishedAt: null, checkpoint: [...arm.completedSteps], output: null };
        if (packet.kind === 'research') arm.research.push(record);
        else { record.injectedAt = run.event.at; arm.alert = record; }
        current = record;
        try {
          await sleep(delayMs, undefined, { signal });
          arm.calls.push(record);
          const result = await agent.generate([{ role: 'user', content: JSON.stringify(packet) }], { maxSteps: 1, modelSettings: { temperature: 0, maxOutputTokens: 250, maxRetries: 0 }, abortSignal: signal });
          const output = JSON.parse(result.text); const wanted = expected(packet);
          if (Object.keys(output).length !== Object.keys(wanted).length || Object.keys(wanted).some(k => output[k] !== wanted[k])) throw new Error('INVALID_OUTPUT');
          record.output = output;
        } catch (error) { record.error = error.message; throw error; }
        finally { record.finishedAt = now(); }
      };
    }
    await Promise.all(run.arms.map(async arm => {
      const primary = actor(arm); const urgent = arm.mode === 'independent' ? actor(arm) : primary;
      const handleAlert = () => urgent({ kind: 'alert', announcement: run.event.announcement }, run.config.alertMs);
      const research = async () => {
        for (let step = 1; step <= run.config.researchMs / run.config.stepMs; step++) {
          if (arm.mode === 'hook' && run.event && !arm.alert) await handleAlert();
          await primary({ kind: 'research', step, completedSteps: [...arm.completedSteps] }, run.config.stepMs);
          arm.completedSteps.push(step);
        }
        arm.researchFinishedAt = now();
        if (arm.mode !== 'independent') { await eventReady; if (!arm.alert) await handleAlert(); }
      };
      const results = await Promise.allSettled(arm.mode === 'independent' ? [research(), eventReady.then(handleAlert)] : [research()]);
      arm.status = results.some(r => r.status === 'rejected') ? 'error' : 'completed';
      arm.finishedAt = now();
      const responseMs = arm.alert?.output ? arm.alert.finishedAt - run.event.at : null;
      arm.metrics = {
        responseMs, metDeadline: responseMs === null ? null : responseMs <= run.config.deadlineMs,
        queueMs: arm.alert ? arm.alert.startedAt - run.event.at : null,
        pauseMs: arm.mode === 'hook' && arm.alert && arm.research.some(r => r.startedAt >= arm.alert.finishedAt) ? arm.alert.finishedAt - arm.alert.startedAt : 0,
        researchElapsedMs: arm.researchFinishedAt ? arm.researchFinishedAt - run.startedAt : null,
      };
    }));
    clearTimeout(timer); injectors.delete(run.id);
    run.status = run.arms.every(a => a.status === 'completed') ? 'completed' : 'error'; run.finishedAt = now();
  }
  return {
    isBusy: () => jobs.size > 0,
    start(input) {
      if (jobs.size) throw new Error('EXPERIMENT_BUSY');
      const config = parseInterruptConfig(input);
      if (runs.size >= 10) runs.delete(runs.keys().next().value);
      const run = { id: randomUUID(), config, startedAt: Date.now(), finishedAt: null, event: null, status: 'running', backend: 'fixture', arms: ['queue', 'hook', 'independent'].map(mode => ({ mode, status: 'running', completedSteps: [], research: [], alert: null, calls: [], metrics: null })) };
      runs.set(run.id, run);
      const job = execute(run).catch(error => { run.status = 'error'; run.error = error.message; }).finally(() => jobs.delete(run.id));
      jobs.set(run.id, job); return structuredClone(run);
    },
    get(id) { return structuredClone(get(id)); },
    inject(id) { const run = get(id); if (run.event) throw new Error('EVENT_ALREADY_SENT'); const inject = injectors.get(id); if (!inject) throw new Error('RUN_NOT_RUNNING'); inject('manual'); return structuredClone(run); },
    async wait(id) { await jobs.get(id); },
  };
}
