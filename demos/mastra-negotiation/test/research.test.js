import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createResearchService, parseResearchConfig, validateResearch } from '../src/research.js';

async function service() { const directory = await mkdtemp(join(tmpdir(), 'mastra-research-')); return { directory, service: createResearchService({ directory, env: {} }) }; }

test('research config caps concurrency and rejects invalid values', () => {
  assert.deepEqual(parseResearchConfig({}), { concurrency: 3, delayMs: 3000, failB: false, backend: 'fixture' });
  for (const input of [{ concurrency: 4 }, { concurrency: 0 }, { delayMs: -1 }, { backend: 'other' }, { failB: 'yes' }]) assert.throws(() => parseResearchConfig(input));
});

test('same actual Mastra results, stable A/B/C order, serial vs bounded overlapping execution', async () => {
  const { service: s } = await service();
  const run = await s.start({ delayMs: 80, concurrency: 3 });
  await s.wait(run.id);
  const result = await s.get(run.id);
  assert.equal(result.status, 'completed');
  assert.deepEqual(result.serial.tasks.map(t => t.companyId), ['A', 'B', 'C']);
  assert.deepEqual(result.serial.tasks.map(t => t.output), result.parallel.tasks.map(t => t.output));
  assert.equal(result.serial.peakConcurrency, 1);
  assert.equal(result.parallel.peakConcurrency, 3);
  const serial = result.serial.tasks.map(t => t.attempts[0]);
  const parallel = result.parallel.tasks.map(t => t.attempts[0]);
  assert.ok(serial.slice(1).every((a, i) => a.startedAt >= serial[i].finishedAt));
  assert.ok(Math.max(...parallel.map(a => a.startedAt)) < Math.min(...parallel.map(a => a.finishedAt)));
  assert.equal(new Set(serial.map(a => a.agentId)).size, 1);
  assert.equal(new Set(parallel.map(a => a.agentId)).size, 3);
  assert.ok(parallel.every(a => a.promptSha256.length === 64 && a.providerPrompt.length > 0));
});

test('concurrency 1 provides no task overlap in either arm', async () => {
  const { service: s } = await service(); const run = await s.start({ delayMs: 10, concurrency: 1 }); await s.wait(run.id);
  const result = await s.get(run.id); assert.equal(result.parallel.peakConcurrency, 1);
});

test('B failure preserves A/C on disk; a new service retries only B once in each arm', async () => {
  const { directory, service: s } = await service();
  const run = await s.start({ delayMs: 10, failB: true }); await s.wait(run.id);
  const before = await s.get(run.id);
  assert.equal(before.status, 'partial');
  for (const arm of [before.serial, before.parallel]) {
    assert.deepEqual(arm.tasks.map(t => t.status), ['succeeded', 'failed_retryable', 'succeeded']);
    assert.equal(arm.tasks[1].attempts[0].error, 'INJECTED_RETRIEVAL_TIMEOUT');
  }
  const persisted = JSON.parse(await readFile(join(directory, `${run.id}.json`), 'utf8'));
  assert.deepEqual(persisted.serial.tasks, before.serial.tasks);
  const restarted = createResearchService({ directory, env: {} });
  await restarted.retry(run.id); await restarted.wait(run.id);
  const after = await restarted.get(run.id);
  assert.equal(after.status, 'completed');
  for (const mode of ['serial', 'parallel']) {
    assert.deepEqual(after[mode].tasks[0], before[mode].tasks[0]);
    assert.deepEqual(after[mode].tasks[2], before[mode].tasks[2]);
    assert.equal(after[mode].tasks[1].attempts.length, 2);
    assert.equal(after[mode].tasks[1].status, 'succeeded');
  }
  await assert.rejects(restarted.retry(run.id), /NOTHING_TO_RETRY/);
});

test('evidence validator rejects fabricated quotation, numeric result and wrong company', async () => {
  const { service: s } = await service(); const run = await s.start({ delayMs: 10 }); await s.wait(run.id); const result = await s.get(run.id);
  const report = result.documents[0]; const output = result.serial.tasks[0].output;
  assert.deepEqual(validateResearch(output, report), output);
  for (const patch of [{ companyId: 'B' }, { revenue: 9999 }, { yoyPercent: 999 }, { evidenceQuote: 'invented quote' }, { risk: 'invented risk' }]) assert.throws(() => validateResearch({ ...output, ...patch }, report));
  await assert.rejects(s.get('../secret'), /INVALID_RUN_ID/);
});

test('restart marks in-flight work retryable without losing completed tasks', async () => {
  const { directory, service: s } = await service(); const run = await s.start({ delayMs: 10 }); await s.wait(run.id);
  const interrupted = await s.get(run.id); const a = structuredClone(interrupted.serial.tasks[0]);
  interrupted.status = 'running'; interrupted.serial.tasks[1].status = 'running'; interrupted.serial.tasks[1].output = null;
  interrupted.serial.tasks[1].attempts[0].finishedAt = null;
  await writeFile(join(directory, `${run.id}.json`), JSON.stringify(interrupted));
  const restored = await createResearchService({ directory, env: {} }).get(run.id);
  assert.equal(restored.status, 'partial');
  assert.equal(restored.serial.tasks[1].status, 'failed_retryable');
  assert.equal(restored.serial.tasks[1].attempts[0].error, 'INTERRUPTED');
  assert.deepEqual(restored.serial.tasks[0], a);
});
