import { MockLanguageModelV3 } from 'ai/test';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { extractPackets } from './domain.js';

export function liveConfigured(env) {
  return Boolean(env.NEGOTIATION_API_KEY && env.NEGOTIATION_BASE_URL && env.NEGOTIATION_MODEL);
}

export function fixtureAction(prompt) {
  // This policy reads ONLY the latest role packet, even in the shared arm.
  // Equal outcomes are intentional: the fixture tests context exposure, not intelligence.
  const packet = extractPackets(prompt).at(-1);
  if (!packet) throw new Error('FIXTURE_PACKET_MISSING');
  const { owner, limit } = packet.private;
  const offers = packet.publicHistory.filter(m => m.role === owner && m.action === 'offer').length;
  const latest = packet.publicHistory.at(-1);
  const target = owner === 'buyer'
    ? Math.max(1, Math.min(limit, Math.round(limit * (0.85 + 0.025 * offers))))
    : Math.min(1000000, Math.max(limit, Math.round(limit * (1.2 - 0.025 * offers))));
  if (latest?.action === 'offer' && latest.role !== owner && (owner === 'buyer' ? latest.price <= target : latest.price >= target)) {
    return { action: 'accept', price: latest.price, reason: 'acceptable' };
  }
  return { action: 'offer', price: target, reason: offers ? 'counter' : 'opening' };
}

export function createModel({ backend, env, beforeCall, afterCall, captureWire, fixtureResponse = fixtureAction, fixtureModelId = 'negotiation-fixture-v1' }) {
  let model;
  if (backend === 'fixture') {
    model = new MockLanguageModelV3({
      provider: 'local-fixture', modelId: fixtureModelId,
      doGenerate: async ({ prompt }) => ({
        content: [{ type: 'text', text: JSON.stringify(fixtureResponse(prompt)) }],
        finishReason: { unified: 'stop', raw: 'stop' },
        usage: { inputTokens: { total: undefined }, outputTokens: { total: undefined } },
        warnings: [],
      }),
    });
  } else {
    if (!liveConfigured(env)) throw new Error('LIVE_MODEL_NOT_CONFIGURED');
    const provider = createOpenAICompatible({
      name: 'negotiation-live', baseURL: env.NEGOTIATION_BASE_URL,
      apiKey: env.NEGOTIATION_API_KEY,
      fetch: async (url, init) => {
        // Capture request body only. Never record authorization headers or API keys.
        captureWire(JSON.parse(init.body));
        return fetch(url, init);
      },
    });
    model = provider.chatModel(env.NEGOTIATION_MODEL);
  }
  return new Proxy(model, {
    get(target, prop) {
      if (prop === 'doGenerate') return async options => {
        const receipt = beforeCall(options, target.modelId);
        try {
          const output = await target.doGenerate(options);
          afterCall(receipt, output, null);
          return output;
        } catch (error) { afterCall(receipt, null, error); throw error; }
      };
      if (prop === 'doStream') return async () => { throw new Error('UNAUDITED_STREAM_FORBIDDEN'); };
      const value = Reflect.get(target, prop, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}
