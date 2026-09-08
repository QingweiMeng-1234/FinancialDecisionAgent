import { Agent } from '@mastra/core/agent';
import { createHash, randomUUID } from 'node:crypto';
import { buildMessages, inspectPrompt, instructions, parseConfig, validateAction } from './domain.js';
import { createModel, liveConfigured } from './models.js';

const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

async function runArm(mode, config, secrets, env, signal) {
  const calls = [];
  const publicHistory = [];
  const histories = { shared: [], buyer: [], seller: [] };
  let current = null;
  let lastReceipt = null;
  const arm = { mode, status: 'running', dealPrice: null, calls, publicHistory, modelId: null, tools: [], memory: 'explicit-transcripts-only' };
  function makeAgent(key) {
    const agentId = `negotiation-${mode}-${key}-${randomUUID()}`;
    const model = createModel({ backend: config.backend, env,
      beforeCall: (options, modelId) => {
        if (options.tools?.length) throw new Error('TOOLS_FORBIDDEN');
        const providerPrompt = structuredClone(options.prompt);
        const receipt = {
          id: randomUUID(), agentId, role: current.role, round: current.round,
          modelId, transport: 'mastra-model-doGenerate', startedAt: Date.now(), finishedAt: null,
          providerPrompt, promptSha256: hash(providerPrompt), promptChars: JSON.stringify(providerPrompt).length,
          tools: [], audit: inspectPrompt(providerPrompt, current.role, secrets), wireRequests: [],
        };
        calls.push(receipt);
        lastReceipt = receipt;
        arm.modelId = modelId;
        return receipt;
      },
      afterCall: (receipt, output, error) => {
        receipt.finishedAt = Date.now();
        receipt.rawOutput = output?.content?.filter(c => c.type === 'text').map(c => c.text).join('') ?? null;
        receipt.usage = output?.usage ?? null;
        receipt.error = error ? 'MODEL_CALL_FAILED' : null;
      },
      captureWire: body => {
        lastReceipt.wireRequests.push({
          body, sha256: hash(body),
          audit: inspectPrompt(body.messages, current.role, secrets),
        });
      },
    });
    // No memory storage, tools, skills that access files, or agent delegation.
    return new Agent({ id: agentId, name: key, instructions, model, tools: {} });
  }
  const agents = mode === 'shared' ? { shared: makeAgent('shared') } : { buyer: makeAgent('buyer'), seller: makeAgent('seller') };
  try {
    for (let round = 1; round <= config.rounds; round++) {
      for (const role of ['buyer', 'seller']) {
        signal?.throwIfAborted();
        current = { role, round };
        const key = mode === 'shared' ? 'shared' : role;
        const messages = buildMessages({ ownSecret: secrets[role], priorMessages: histories[key], publicHistory });
        const callSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000);
        const output = await agents[key].generate(messages, {
          maxSteps: 1, modelSettings: { temperature: 0, maxOutputTokens: 300, maxRetries: 0 }, abortSignal: callSignal,
        });
        if (!lastReceipt || lastReceipt.role !== role) throw new Error('AUDIT_RECEIPT_MISSING');
        const action = validateAction(JSON.parse(output.text), secrets[role], publicHistory.at(-1));
        lastReceipt.acceptedOutput = action;
        histories[key] = [...messages, { role: 'assistant', content: JSON.stringify(action) }];
        publicHistory.push({ role, ...action });
        if (action.action === 'accept') { arm.status = 'agreed'; arm.dealPrice = action.price; return arm; }
        if (action.action === 'walk_away') { arm.status = 'walked_away'; return arm; }
      }
    }
    arm.status = 'round_limit';
  } catch (error) {
    arm.status = 'error';
    // Provider errors may contain request details. Return a stable code, not arbitrary error text.
    arm.error = signal?.aborted ? 'RUN_CANCELLED' : 'GENERATION_OR_VALIDATION_FAILED';
    if (lastReceipt && !lastReceipt.acceptedOutput) lastReceipt.outputRejected = true;
    if (env.NEGOTIATION_TEST_DEBUG === '1') console.error(error);
  }
  return arm;
}

export async function compare(input, { env = process.env, signal } = {}) {
  const config = parseConfig(input);
  if (config.backend === 'live' && !liveConfigured(env)) throw new Error('LIVE_MODEL_NOT_CONFIGURED');
  const id = randomUUID();
  const secrets = {
    buyer: { owner: 'buyer', limit: config.buyerMax, canary: `BUYER_PRIVATE_${randomUUID()}` },
    seller: { owner: 'seller', limit: config.sellerMin, canary: `SELLER_PRIVATE_${randomUUID()}` },
  };
  const startedAt = Date.now();
  // The two arms are serial as well; concurrency is intentionally not the experimental variable.
  const shared = await runArm('shared', config, secrets, env, signal);
  const isolated = await runArm('isolated', config, secrets, env, signal);
  for (const arm of [shared, isolated]) {
    arm.audit = {
      observedCalls: arm.calls.length,
      violations: arm.calls.filter(c => !c.audit.passed || c.wireRequests.some(w => !w.audit.passed)).length,
      result: arm.status === 'error' || !arm.calls.length ? 'incomplete' : arm.calls.every(c => c.audit.passed && c.wireRequests.every(w => w.audit.passed)) ? 'pass' : 'fail',
    };
  }
  return { id, backend: config.backend, config, secrets, startedAt, finishedAt: Date.now(),
    claim: 'context_boundary_only', shared, isolated,
    protocol: { framework: 'Mastra Agent', version: '1.64.0', concurrency: 1, maxRounds: config.rounds, maxCallsPerArm: 2 * config.rounds, instructionsSha256: hash(instructions), tools: [], externalMemory: false },
  };
}
