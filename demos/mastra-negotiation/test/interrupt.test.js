import test from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as sleep } from 'node:timers/promises';
import { createInterruptService, parseInterruptConfig } from '../src/interrupt.js';

const config = { researchMs: 600, stepMs: 100, alertMs: 80, autoAfterMs: 40, deadlineMs: 250 };
const overlap = (a, b) => a.startedAt < b.finishedAt && b.startedAt < a.finishedAt;

test('interrupt config: bounded work, divisible checkpoints and event before research ends', () => {
  assert.equal(parseInterruptConfig({}).researchMs, 60000);
  for (const bad of [{ stepMs: 0 }, { researchMs: 999999 }, { researchMs: 600, stepMs: 101 }, { ...config, autoAfterMs: 600 }, { backend: 'live' }]) assert.throws(() => parseInterruptConfig(bad));
});

test('three schedulers perform equal work: queue waits, hook pauses at checkpoint, independent alert overlaps', async () => {
  const service = createInterruptService(); const initial = service.start(config);
  await service.wait(initial.id); const run = service.get(initial.id);
  assert.equal(run.status, 'completed'); assert.equal(run.event.source, 'auto');
  const outputs = [];
  for (const arm of run.arms) {
    assert.equal(arm.status, 'completed'); assert.deepEqual(arm.completedSteps, [1, 2, 3, 4, 5, 6]);
    assert.equal(arm.calls.length, 7); assert.ok(arm.calls.every(c => c.providerPrompt && c.promptSha256));
    assert.equal(arm.alert.injectedAt, run.event.at); assert.equal(arm.alert.output.risk, 'earnings_warning');
    assert.equal(arm.metrics.responseMs, arm.alert.finishedAt - run.event.at);
    assert.equal(arm.metrics.metDeadline, arm.metrics.responseMs <= config.deadlineMs);
    outputs.push(arm.research.map(r => r.output));
  }
  assert.deepEqual(outputs[0], outputs[1]); assert.deepEqual(outputs[1], outputs[2]);
  const [queue, hook, independent] = run.arms;
  assert.ok(queue.alert.startedAt >= queue.research.at(-1).finishedAt);
  assert.equal(queue.metrics.pauseMs, 0);
  assert.equal(hook.metrics.pauseMs, hook.alert.finishedAt - hook.alert.startedAt);
  assert.ok(hook.research.every(r => !overlap(r, hook.alert)));
  assert.ok(hook.research.some(r => r.startedAt >= hook.alert.finishedAt));
  assert.ok(independent.research.some(r => overlap(r, independent.alert)));
  assert.equal(independent.metrics.pauseMs, 0);
  assert.equal(new Set(queue.calls.map(c => c.agentId)).size, 1);
  assert.equal(new Set(hook.calls.map(c => c.agentId)).size, 1);
  assert.equal(new Set(independent.calls.map(c => c.agentId)).size, 2);
  assert.ok(queue.metrics.responseMs > independent.metrics.responseMs);
});

test('manual event is broadcast once; busy admission and unknown run fail without extra work', async () => {
  const service = createInterruptService(); const run = service.start({ ...config, autoAfterMs: 500 });
  assert.throws(() => service.start(config), /EXPERIMENT_BUSY/);
  await sleep(25); const receipt = service.inject(run.id);
  assert.equal(receipt.event.source, 'manual');
  assert.throws(() => service.inject(run.id), /EVENT_ALREADY_SENT/);
  assert.throws(() => service.get('missing'), /RUN_NOT_FOUND/);
  await service.wait(run.id);
  const done = service.get(run.id);
  assert.equal(done.event.at, receipt.event.at);
  assert.ok(done.arms.every(a => a.calls.filter(c => c.kind === 'alert').length === 1));
  assert.equal(service.isBusy(), false);
});

test('coarse hook does not interrupt an in-flight step, and saved steps are supplied on resume', async () => {
  const service = createInterruptService(); const run = service.start({ ...config, researchMs: 600, stepMs: 300, autoAfterMs: 30 });
  await service.wait(run.id); const hook = service.get(run.id).arms[1];
  assert.ok(hook.alert.startedAt >= hook.research[0].finishedAt);
  assert.ok(hook.alert.startedAt < hook.research[1].startedAt);
  const resumed = hook.calls.find(c => c.kind === 'research' && c.step === 2);
  assert.match(JSON.stringify(resumed.providerPrompt), /completedSteps/);
  assert.deepEqual(hook.research[1].checkpoint, [1]);
});
