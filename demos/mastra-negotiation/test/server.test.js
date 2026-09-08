import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createDemoServer } from '../src/server.js';
import { mkdtemp } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout as sleep } from 'node:timers/promises';

test('interrupt API admits an event during active work, rejects duplicate injection and exposes the third tab', async t => {
  const base = await serve(t, { env: {} });
  const send = (path, data) => fetch(`${base}${path}`, { method: 'POST', body: JSON.stringify(data) });
  assert.equal((await send('/api/interrupt/runs', { stepMs: 0 })).status, 400);
  const response = await send('/api/interrupt/runs', { researchMs: 600, stepMs: 100, alertMs: 40, autoAfterMs: 500, deadlineMs: 250 });
  assert.equal(response.status, 202); const initial = await response.json();
  assert.equal((await send('/api/compare', {})).status, 409);
  assert.equal((await send('/api/research/runs', {})).status, 409);
  assert.equal((await send(`/api/interrupt/runs/${initial.id}/event`, { unexpected: true })).status, 400);
  assert.equal((await send(`/api/interrupt/runs/${initial.id}/event`, {})).status, 202);
  assert.equal((await send(`/api/interrupt/runs/${initial.id}/event`, {})).status, 409);
  let state;
  for (let i = 0; i < 150; i++) {
    state = await (await fetch(`${base}/api/interrupt/runs/${initial.id}`)).json();
    if (state.status !== 'running') break;
    await sleep(20);
  }
  assert.equal(state.status, 'completed'); assert.equal(state.event.source, 'manual');
  assert.equal((await fetch(`${base}/api/interrupt/runs/missing`)).status, 404);
  const page = await (await fetch(base)).text(); assert.match(page, /id="tab-interrupt"/); assert.match(page, /id="interrupt-inject"/);
  for (const path of ['/interrupt.js', '/interrupt.css']) assert.equal((await fetch(`${base}${path}`)).status, 200);
});

async function serve(t, options = {}) {
  const server = createDemoServer(options);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}`;
}

test('HTTP: static UI, health without secrets, actual Mastra paired run and invalid input', async t => {
  const base = await serve(t, { env: {} });
  const page = await fetch(base);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /上下文/);
  assert.ok(page.headers.get('content-security-policy'));
  assert.equal((await (await fetch(`${base}/api/config`)).json()).liveConfigured, false);
  const response = await fetch(`${base}/api/compare`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.shared.audit.result, 'fail');
  assert.equal(result.isolated.audit.result, 'pass');
  const bad = await fetch(`${base}/api/compare`, { method: 'POST', body: '{"rounds":100}' });
  assert.equal(bad.status, 400);
  assert.equal((await fetch(`${base}/api/compare`, { method: 'POST', body: '{bad' })).status, 400);
  assert.equal((await fetch(`${base}/api/compare`, { method: 'POST', body: 'x'.repeat(9000) })).status, 413);
  assert.equal((await fetch(`${base}/api/compare`, { method: 'POST', body: '{}', headers: { Origin: 'https://untrusted.example' } })).status, 403);
  assert.equal((await fetch(`${base}/src/models.js`)).status, 404);
});

test('server admits only one comparison and rejects excess work without dispatch', async t => {
  let release;
  let started;
  const entered = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let count = 0;
  const base = await serve(t, { run: async () => { count++; started(); await gate; return { ok: true }; } });
  const first = fetch(`${base}/api/compare`, { method: 'POST', body: '{}' });
  await entered;
  try {
    assert.equal((await fetch(`${base}/api/compare`, { method: 'POST', body: '{}' })).status, 409);
    assert.equal(count, 1);
  } finally { release(); }
  assert.equal((await first).status, 200);
});

test('research API returns a pollable job, checkpoints failures, retries only failed tasks and supports reload', async t => {
  const researchDirectory = await mkdtemp(join(tmpdir(), 'research-api-'));
  const base = await serve(t, { researchDirectory, env: {} });
  const send = (path, data) => fetch(`${base}${path}`, { method: 'POST', body: JSON.stringify(data) });
  assert.equal((await send('/api/research/runs', { concurrency: 9 })).status, 400);
  const response = await send('/api/research/runs', { delayMs: 30, failB: true });
  assert.equal(response.status, 202);
  const initial = await response.json();
  assert.equal((await send('/api/compare', {})).status, 409);
  async function finished() {
    for (let i = 0; i < 150; i++) {
      const state = await (await fetch(`${base}/api/research/runs/${initial.id}`)).json();
      if (state.status !== 'running') return state;
      await sleep(20);
    }
    assert.fail('job did not complete');
  }
  const before = await finished(); assert.equal(before.status, 'partial');
  assert.equal((await send(`/api/research/runs/${initial.id}/retry`, {})).status, 202);
  const after = await finished(); assert.equal(after.status, 'completed');
  assert.deepEqual(after.serial.tasks[0], before.serial.tasks[0]);
  assert.equal((await send(`/api/research/runs/${initial.id}/retry`, {})).status, 409);
  const secondBase = await serve(t, { researchDirectory, env: {} });
  const reload = await (await fetch(`${secondBase}/api/research/runs/${initial.id}`)).json();
  assert.deepEqual(reload, after);
  assert.equal((await fetch(`${base}/api/research/runs/not-an-id`)).status, 400);
  assert.equal((await fetch(`${base}/api/research/runs/11111111-1111-4111-8111-111111111111`)).status, 404);
});
