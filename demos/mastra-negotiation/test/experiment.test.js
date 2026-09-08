import test from 'node:test';
import assert from 'node:assert/strict';
import { compare } from '../src/experiment.js';

test('real Mastra executions: same fixture, serial scheduling, shared exposure vs isolated separation', async () => {
  const result = await compare({});
  const { shared, isolated } = result;
  assert.equal(result.backend, 'fixture');
  assert.equal(shared.modelId, isolated.modelId);
  assert.equal(shared.status, 'agreed');
  assert.equal(isolated.status, 'agreed');
  assert.deepEqual(shared.publicHistory, isolated.publicHistory, 'fixture deliberately demonstrates equal deal quality');
  assert.equal(shared.calls[0].audit.passed, true);
  assert.equal(shared.calls[1].audit.opponentPacketPresent, true);
  assert.ok(shared.calls.slice(1).every(c => !c.audit.passed));
  assert.ok(isolated.calls.every(c => c.audit.passed));
  assert.equal(new Set(shared.calls.map(c => c.agentId)).size, 1);
  assert.equal(new Set(isolated.calls.map(c => c.agentId)).size, 2);
  assert.ok(isolated.calls.every(c => c.tools.length === 0 && c.promptSha256.length === 64 && c.providerPrompt.length > 0));
  assert.ok(isolated.calls.every((c, i, cs) => i === 0 || c.startedAt >= cs[i - 1].finishedAt));
  assert.ok(shared.calls.every(c => c.transport === 'mastra-model-doGenerate'));
  assert.equal(result.claim, 'context_boundary_only');
});

test('no-overlap and round cap never create impossible agreements', async () => {
  const result = await compare({ buyerMax: 700, sellerMin: 900, rounds: 2 });
  for (const arm of [result.shared, result.isolated]) {
    assert.equal(arm.status, 'round_limit');
    assert.equal(arm.dealPrice, null);
    assert.equal(arm.calls.length, 4);
  }
});

test('concurrent experiments never share role memory or canaries', async () => {
  const [a, b] = await Promise.all([compare({ rounds: 1 }), compare({ rounds: 1, buyerMax: 1200 })]);
  assert.notEqual(a.id, b.id);
  assert.notEqual(a.secrets.buyer.canary, b.secrets.buyer.canary);
  assert.ok(!JSON.stringify(a.isolated.calls).includes(b.secrets.buyer.canary));
});

test('live mode fails explicitly without credentials and never silently uses fixture', async () => {
  await assert.rejects(compare({ backend: 'live' }, { env: {} }), /LIVE_MODEL_NOT_CONFIGURED/);
});
