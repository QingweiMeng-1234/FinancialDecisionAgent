import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { compare } from './experiment.js';
import { parseConfig } from './domain.js';
import { liveConfigured } from './models.js';
import { createResearchService, parseResearchConfig } from './research.js';
import { createInterruptService, parseInterruptConfig } from './interrupt.js';

const publicRoot = new URL('../public/', import.meta.url);
const staticFiles = { '/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/research.js': ['research.js', 'text/javascript'], '/research.css': ['research.css', 'text/css'], '/style.css': ['style.css', 'text/css'] };
staticFiles['/interrupt.js'] = ['interrupt.js', 'text/javascript'];
staticFiles['/interrupt.css'] = ['interrupt.css', 'text/css'];

export function createDemoServer({ env = process.env, run = compare, researchDirectory = fileURLToPath(new URL('../artifacts/research/', import.meta.url)) } = {}) {
  let busy = false;
  const research = createResearchService({ directory: researchDirectory, env });
  const interrupt = createInterruptService();
  return createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
    function json(status, body) {
      if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(body)); }
    }
    try {
      const port = req.socket.localPort;
      const allowed = [`127.0.0.1:${port}`, `localhost:${port}`];
      if (!allowed.includes(req.headers.host) || (req.headers.origin && !allowed.some(h => req.headers.origin === `http://${h}`))) return json(403, { error: 'LOCAL_ORIGIN_REQUIRED' });
      const path = new URL(req.url, 'http://localhost').pathname;
      if (req.method === 'GET' && staticFiles[path]) {
        const [file, type] = staticFiles[path];
        const bytes = await readFile(new URL(file, publicRoot));
        res.writeHead(200, { 'Content-Type': `${type}; charset=utf-8` }); res.end(bytes); return;
      }
      if (req.method === 'GET' && path === '/api/config') return json(200, {
        liveConfigured: liveConfigured(env), model: liveConfigured(env) ? env.NEGOTIATION_MODEL : null,
        framework: 'Mastra 1.64.0', maxRounds: 5,
      });
      const researchPath = path.match(/^\/api\/research\/runs\/([^/]+)(\/retry)?$/);
      const interruptPath = path.match(/^\/api\/interrupt\/runs\/([^/]+)(\/event)?$/);
      if (req.method === 'GET' && interruptPath && !interruptPath[2]) return json(200, interrupt.get(interruptPath[1]));
      const isInterruptStart = path === '/api/interrupt/runs';
      const isEvent = Boolean(interruptPath?.[2]);
      if (req.method === 'GET' && researchPath && !researchPath[2]) return json(200, await research.get(researchPath[1]));
      const isResearchStart = path === '/api/research/runs';
      const isRetry = researchPath && researchPath[2];
      if (req.method !== 'POST' || !(path === '/api/compare' || isResearchStart || isRetry || isInterruptStart || isEvent)) return json(404, { error: 'NOT_FOUND' });
      if (!isEvent && (busy || research.isBusy() || interrupt.isBusy())) return json(409, { error: 'EXPERIMENT_BUSY' });
      let raw = '';
      for await (const chunk of req) {
        raw += chunk.toString('utf8');
        if (Buffer.byteLength(raw) > 8192) return json(413, { error: 'BODY_TOO_LARGE' });
      }
      let input;
      try {
        const parsed = JSON.parse(raw);
        if (isRetry || isEvent) { if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.keys(parsed).length) throw new Error('INVALID_INPUT'); input = {}; }
        else input = (isInterruptStart ? parseInterruptConfig : isResearchStart ? parseResearchConfig : parseConfig)(parsed);
      } catch { return json(400, { error: 'INVALID_INPUT' }); }
      // Recheck after body I/O: another request may have acquired the single slot.
      if (isEvent) return json(202, interrupt.inject(interruptPath[1]));
      if (busy || research.isBusy() || interrupt.isBusy()) return json(409, { error: 'EXPERIMENT_BUSY' });
      if (isInterruptStart) return json(202, interrupt.start(input));
      if (input.backend === 'live' && !liveConfigured(env)) return json(400, { error: 'LIVE_MODEL_NOT_CONFIGURED' });
      if (isResearchStart || isRetry) {
        busy = true;
        try { return json(202, await (isRetry ? research.retry(researchPath[1]) : research.start(input))); }
        finally { busy = false; }
      }
      busy = true;
      const controller = new AbortController();
      const closed = () => { if (!res.writableEnded) controller.abort(); };
      res.on('close', closed);
      try {
        const result = await run(input, { env, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(180000)]) });
        json(200, result);
      } finally { busy = false; res.off('close', closed); }
    } catch (error) {
      const statuses = { INVALID_RUN_ID: 400, RUN_NOT_FOUND: 404, NOTHING_TO_RETRY: 409, EXPERIMENT_BUSY: 409, LIVE_MODEL_NOT_CONFIGURED: 400, EVENT_ALREADY_SENT: 409, RUN_NOT_RUNNING: 409 };
      json(statuses[error.message] || 500, { error: statuses[error.message] ? error.message : 'EXPERIMENT_FAILED' });
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.NEGOTIATION_PORT || 4317);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('INVALID_PORT');
  const server = createDemoServer();
  server.listen(port, '127.0.0.1', () => console.log(`Mastra negotiation demo: http://127.0.0.1:${port}`));
  server.on('error', error => { console.error(`Server failed: ${error.code}`); process.exitCode = 1; });
}
