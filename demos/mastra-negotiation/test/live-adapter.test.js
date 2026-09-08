import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { compare } from '../src/experiment.js';
import { fixtureAction } from '../src/models.js';

test('compatible HTTP adapter: audit matches actual wire requests, never exposes auth headers', async t => {
  const received = [];
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw); received.push(body);
    assert.equal(req.headers.authorization, 'Bearer TEST_KEY_NEVER_IN_REPORT');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ id: 'test', object: 'chat.completion', created: 1, model: 'local-http-fixture',
      choices: [{ index: 0, message: { role: 'assistant', content: JSON.stringify(fixtureAction(body.messages)) }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 },
    }));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const result = await compare({ backend: 'live', rounds: 2 }, { env: {
    NEGOTIATION_API_KEY: 'TEST_KEY_NEVER_IN_REPORT', NEGOTIATION_BASE_URL: `http://127.0.0.1:${server.address().port}/v1`, NEGOTIATION_MODEL: 'local-http-fixture',
  } });
  assert.equal(result.shared.status, 'round_limit');
  assert.equal(result.isolated.audit.result, 'pass');
  const wires = [...result.shared.calls, ...result.isolated.calls].flatMap(c => c.wireRequests);
  assert.equal(wires.length, 8);
  assert.deepEqual(wires.map(w => w.body), received);
  assert.ok(!JSON.stringify(result).includes('TEST_KEY_NEVER_IN_REPORT'));
});

test('invalid output is retained for audit, not relayed; failed shared arm does not erase isolated results', async t => {
  let count = 0;
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    const action = fixtureAction(body.messages);
    if (++count === 1) action.reason = 'private information must never be relayed';
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ id: 'rejection-test', object: 'chat.completion', created: 1, model: 'local-http-fixture',
      choices: [{ index: 0, message: { role: 'assistant', content: JSON.stringify(action) }, finish_reason: 'stop' }],
    }));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const result = await compare({ backend: 'live', rounds: 1 }, { env: {
    NEGOTIATION_API_KEY: 'test', NEGOTIATION_BASE_URL: `http://127.0.0.1:${server.address().port}/v1`, NEGOTIATION_MODEL: 'local-http-fixture',
  } });
  assert.equal(result.shared.audit.result, 'incomplete');
  assert.equal(result.shared.publicHistory.length, 0);
  assert.equal(result.shared.calls[0].outputRejected, true);
  assert.match(result.shared.calls[0].rawOutput, /private information/);
  assert.equal(result.isolated.audit.result, 'pass');
  assert.equal(result.isolated.calls.length, 2);
  assert.ok(!JSON.stringify(result.isolated.calls).includes('private information must never be relayed'));
});

test('provider failure: no hidden retries and no false passing isolation verdict', async t => {
  let requests = 0;
  const server = createServer(async (req, res) => {
    for await (const chunk of req) { /* Drain request. */ }
    requests++;
    res.writeHead(503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'fixture unavailable' } }));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const result = await compare({ backend: 'live' }, { env: {
    NEGOTIATION_API_KEY: 'test', NEGOTIATION_BASE_URL: `http://127.0.0.1:${server.address().port}/v1`, NEGOTIATION_MODEL: 'local-http-fixture',
  } });
  assert.equal(requests, 2, 'one attempt per arm, no retries');
  for (const arm of [result.shared, result.isolated]) {
    assert.equal(arm.audit.result, 'incomplete');
    assert.equal(arm.publicHistory.length, 0);
    assert.equal(arm.calls[0].error, 'MODEL_CALL_FAILED');
  }
});
