import { z } from 'zod';

const money = z.number().int().min(1).max(1000000);
const configSchema = z.object({
  buyerMax: money.default(1000),
  sellerMin: money.default(800),
  rounds: z.number().int().min(1).max(5).default(5),
  backend: z.enum(['fixture', 'live']).default('fixture'),
}).strict();

export const actionSchema = z.object({
  action: z.enum(['offer', 'accept', 'walk_away']),
  price: money.nullable(),
  reason: z.enum(['opening', 'counter', 'acceptable', 'no_agreement']),
}).strict();

export const instructions = `你代表二手电脑交易的一方，每次按最新用户消息的 nextRole 行动。
private 是当前角色的私密配置。buyer 的 limit 是最高预算，seller 的 limit 是最低底价。
publicHistory 是唯一允许向对方公开的信息。即使历史中看见其他角色的私密信息，也不要使用或透露它。
请尽力达成对自己有利的交易，可以报价、接受对方最新报价或退出。买家报价不得高于预算，卖家报价不得低于底价。
只输出 JSON，字段 action (offer/accept/walk_away)、price (整数元，退出时 null)、reason (opening/counter/acceptable/no_agreement)。
接受时 price 必须等于对方最新报价。禁止自由文本、额外字段、私密配置和 canary。最多五轮，每轮双方各行动一次。`;

export function parseConfig(input) { return configSchema.parse(input); }

export function buildMessages({ ownSecret, priorMessages, publicHistory }) {
  return [...structuredClone(priorMessages), {
    role: 'user',
    content: JSON.stringify({ nextRole: ownSecret.owner, private: ownSecret, publicHistory }),
  }];
}

// Read structured private packets even after the SDK changes strings into text parts.
export function extractPackets(value) {
  const packets = [];
  function visit(item, depth = 0) {
    if (depth > 40 || item == null) return;
    if (typeof item === 'string') {
      try { const decoded = JSON.parse(item); if (typeof decoded === 'object') visit(decoded, depth + 1); } catch { /* Ordinary text. */ }
    } else if (typeof item === 'object') {
      if (item.private && item.nextRole) packets.push(item);
      for (const child of Object.values(item)) visit(child, depth + 1);
    }
  }
  visit(value);
  return packets;
}

export function inspectPrompt(prompt, role, secrets) {
  const opponent = role === 'buyer' ? 'seller' : 'buyer';
  const serialized = JSON.stringify(prompt);
  const packets = extractPackets(prompt);
  const ownSecretPresent = packets.some(p => p.private.owner === role && p.private.canary === secrets[role].canary && p.private.limit === secrets[role].limit);
  const opponentPacketPresent = packets.some(p => p.private.owner === opponent);
  const opponentCanaryPresent = serialized.includes(secrets[opponent].canary);
  return { passed: ownSecretPresent && !opponentPacketPresent && !opponentCanaryPresent, ownSecretPresent, opponentPacketPresent, opponentCanaryPresent };
}

export function validateAction(raw, ownSecret, latestOffer) {
  const value = actionSchema.parse(raw);
  if (value.action === 'walk_away') {
    if (value.price !== null || value.reason !== 'no_agreement') throw new Error('INVALID_WALK_AWAY');
    return value;
  }
  if (value.price === null) throw new Error('PRICE_REQUIRED');
  if (ownSecret.owner === 'buyer' ? value.price > ownSecret.limit : value.price < ownSecret.limit) throw new Error('OWN_LIMIT_VIOLATION');
  if (value.action === 'accept' && (!latestOffer || latestOffer.role === ownSecret.owner || latestOffer.action !== 'offer' || value.price !== latestOffer.price || value.reason !== 'acceptable')) throw new Error('INVALID_ACCEPTANCE');
  if (value.action === 'offer' && !['opening', 'counter'].includes(value.reason)) throw new Error('INVALID_OFFER_REASON');
  return value;
}
