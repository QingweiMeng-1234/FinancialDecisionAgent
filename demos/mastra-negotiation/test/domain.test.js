import test from 'node:test';
import assert from 'node:assert/strict';
import { parseConfig, buildMessages, inspectPrompt, validateAction } from '../src/domain.js';

const secrets = {
  buyer: { owner: 'buyer', limit: 1000, canary: 'BUYER_PRIVATE_Q17' },
  seller: { owner: 'seller', limit: 800, canary: 'SELLER_PRIVATE_Z93' },
};
const publicHistory = [{ role: 'buyer', action: 'offer', price: 850, reason: 'counter' }];

test('input: defaults, no-overlap allowed, reject coercion, unknown fields and excessive rounds', () => {
  assert.deepEqual(parseConfig({}), { buyerMax: 1000, sellerMin: 800, rounds: 5, backend: 'fixture' });
  assert.equal(parseConfig({ buyerMax: 700, sellerMin: 900 }).sellerMin, 900);
  for (const patch of [{ rounds: 6 }, { rounds: 0 }, { buyerMax: '1000' }, { sellerMin: -1 }, { backend: 'fake' }, { model: 'injected' }]) {
    assert.throws(() => parseConfig(patch));
  }
});

test('shared history retains first role secret; isolated request contains only own private packet', () => {
  const first = buildMessages({ ownSecret: secrets.buyer, priorMessages: [], publicHistory: [] });
  const shared = buildMessages({ ownSecret: secrets.seller, priorMessages: first, publicHistory });
  const isolated = buildMessages({ ownSecret: secrets.seller, priorMessages: [], publicHistory });
  assert.equal(inspectPrompt(shared, 'seller', secrets).passed, false);
  assert.equal(inspectPrompt(isolated, 'seller', secrets).passed, true);
  assert.ok(JSON.stringify(isolated).includes('850'));
  assert.ok(!JSON.stringify(isolated).includes(secrets.buyer.canary));
  assert.ok(!JSON.stringify(isolated).includes('1000'));
  assert.equal(first.length, 1, 'context construction must not mutate stored history');
});

test('auditor detects own packet missing, opponent canary and opponent packet; public price collisions are not leaks', () => {
  const own = buildMessages({ ownSecret: secrets.buyer, priorMessages: [], publicHistory: [] });
  assert.equal(inspectPrompt([], 'buyer', secrets).ownSecretPresent, false);
  assert.equal(inspectPrompt([], 'buyer', secrets).passed, false);
  assert.equal(inspectPrompt([...own, { role: 'user', content: 'public offer: 800' }], 'buyer', secrets).passed, true);
  const leak = inspectPrompt([...own, { role: 'user', content: secrets.seller.canary }], 'buyer', secrets);
  assert.equal(leak.passed, false);
  assert.equal(leak.opponentCanaryPresent, true);
});

test('public gate restricts schema, private-budget violations, invalid acceptance and arbitrary explanatory text', () => {
  const action = { action: 'offer', price: 850, reason: 'counter' };
  assert.deepEqual(validateAction(action, secrets.buyer, null), action);
  for (const invalid of [{ ...action, price: 1001 }, { ...action, price: 1.5 }, { ...action, reason: secrets.buyer.canary }, { ...action, private: 1000 }]) {
    assert.throws(() => validateAction(invalid, secrets.buyer, null));
  }
  assert.throws(() => validateAction({ action: 'accept', price: 850, reason: 'acceptable' }, secrets.buyer, null));
  assert.throws(() => validateAction({ action: 'accept', price: 850, reason: 'acceptable' }, secrets.buyer, { ...action, role: 'seller', price: 900 }));
  assert.deepEqual(validateAction({ action: 'accept', price: 900, reason: 'acceptable' }, secrets.buyer, { ...action, role: 'seller', price: 900 }), { action: 'accept', price: 900, reason: 'acceptable' });
  assert.throws(() => validateAction({ ...action, price: 700 }, secrets.seller, null));
});
