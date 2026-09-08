const $ = selector => document.querySelector(selector);
const money = value => value === null ? '未成交' : `¥${value.toLocaleString('zh-CN')}`;
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let result = null;
let liveModel = null;

function setStatus(message, error = false) { $('#status').textContent = message; $('#status').className = error ? 'error' : ''; }
function showBackend() {
  $('#backend-note').textContent = $('#backend').value === 'fixture'
    ? '模拟策略只读取当前角色配置，刻意让两组议价行为一致；真实运行 Mastra，不调用外部 LLM。'
    : `真实模型 ${liveModel} · 会产生供应商费用。最多每组 10 次、两组合计 20 次调用；单次 30 秒、实验 180 秒超时。`;
}
$('#backend').addEventListener('change', showBackend);

function renderArm(mode) {
  const arm = result[mode];
  const root = $(`#${mode}`);
  const label = { pass: '隔离检查通过', fail: '隔离检查失败', incomplete: '执行未完成' }[arm.audit.result];
  const badge = root.querySelector('[data-badge]');
  badge.className = `badge ${arm.audit.result === 'incomplete' ? 'neutral' : arm.audit.result}`;
  badge.textContent = label;
  root.querySelector('[data-metrics]').innerHTML = `<div><span>${arm.status === 'round_limit' ? '轮数耗尽 · 未成交' : arm.status === 'error' ? '执行或输出校验失败' : arm.status === 'walked_away' ? '一方退出' : '成交价'}</span><strong>${money(arm.dealPrice)}</strong></div><div><span>不满足隔离的请求</span><strong class="${arm.audit.violations ? 'bad-number' : ''}">${arm.audit.violations}<small> / ${arm.calls.length}</small></strong></div>`;
  const timeline = root.querySelector('[data-timeline]');
  timeline.replaceChildren();
  arm.calls.forEach((call, i) => {
    const button = document.createElement('button'); button.className = 'turn'; button.type = 'button';
    const action = call.acceptedOutput;
    const sentence = action ? action.action === 'walk_away' ? '退出议价' : `${action.action === 'accept' ? '接受' : '报价'} ${money(action.price)}` : '输出未被接受';
    button.innerHTML = `<span class="turn-index">${String(i + 1).padStart(2, '0')}</span><span class="turn-title">${call.role === 'buyer' ? '买家' : '卖家'} ${sentence}<small>第 ${call.round} 轮</small></span><span class="turn-flag ${call.audit.passed ? '' : 'bad'}">${call.audit.passed ? '私密输入隔离 ✓' : '对方信息可见 !'}</span>`;
    button.addEventListener('click', () => inspect(mode, i));
    timeline.append(button);
  });
}

function inspect(mode, index) {
  document.querySelectorAll('.turn').forEach(el => el.classList.remove('active'));
  $(`#${mode}`).querySelectorAll('.turn')[index]?.classList.add('active');
  const call = result[mode].calls[index];
  const other = call.role === 'buyer' ? 'seller' : 'buyer';
  const opponentCanary = result.secrets[other].canary;
  const promptText = JSON.stringify(call.providerPrompt, null, 2);
  const highlighted = escapeHtml(promptText).replaceAll(opponentCanary, `<mark>${opponentCanary}</mark>`);
  const audit = call.audit;
  $('#inspection').innerHTML = `<p class="audit-title">${mode === 'shared' ? 'A · 共享上下文' : 'B · 隔离上下文'} / 第 ${call.round} 轮 / ${call.role === 'buyer' ? '买家' : '卖家'}请求</p>
    <div class="checks"><span class="check ${audit.ownSecretPresent ? '' : 'bad'}">自己的配置：${audit.ownSecretPresent ? '存在' : '缺失'}</span><span class="check ${audit.opponentPacketPresent ? 'bad' : ''}">对方私密配置：${audit.opponentPacketPresent ? '存在' : '未提供'}</span><span class="check ${audit.opponentCanaryPresent ? 'bad' : ''}">对方随机标记：${audit.opponentCanaryPresent ? '存在' : '未提供'}</span><span class="check">可调用工具：0</span></div>
    <p class="explanation">${audit.passed ? '这个请求包含当前角色的私密配置和允许公开的报价。审计未检测到对方私密配置或随机标记。' : '角色虽然已经切换，完整历史仍包含对方的私密配置。下方高亮的是对方随机标记；“请忽略”没有从请求中删除这些信息。'}</p>
    <div class="code-toolbar"><span>实际模型输入 · ${call.promptChars.toLocaleString()} 字符</span><span>SHA-256 ${call.promptSha256.slice(0, 18)}…</span></div><pre>${highlighted}</pre>
    <details><summary>执行身份、原始输出与完整校验记录</summary><pre>${escapeHtml(JSON.stringify({ agentId: call.agentId, modelId: call.modelId, transport: call.transport, startedAt: call.startedAt, finishedAt: call.finishedAt, promptSha256: call.promptSha256, audit: call.audit, rawOutput: call.rawOutput, acceptedOutput: call.acceptedOutput, outputRejected: call.outputRejected, usage: call.usage }, null, 2))}</pre></details>
    ${call.wireRequests.length ? `<details><summary>实际 HTTP 请求体（不含凭证与请求头）</summary><pre>${escapeHtml(JSON.stringify(call.wireRequests, null, 2))}</pre></details>` : '<p class="explanation">本次为本地模拟：审计位置是 Mastra 调用模型的 doGenerate 边界，没有外部 HTTP 请求。</p>'}`;
}

$('#config').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#run'); button.disabled = true; $('#download').disabled = true;
  const previous = button.innerHTML; button.textContent = '实验运行中…';
  document.querySelectorAll('#config input, #config select').forEach(el => { el.disabled = true; });
  setStatus('正在通过 Mastra 串行执行两个模式。完成后可查看每轮完整请求。');
  try {
    const response = await fetch('/api/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      buyerMax: Number($('#buyerMax').value), sellerMin: Number($('#sellerMin').value), rounds: Number($('#rounds').value), backend: $('#backend').value,
    }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '请求失败');
    result = body;
    renderArm('shared'); renderArm('isolated');
    if (body.shared.calls.length) inspect('shared', Math.min(2, body.shared.calls.length - 1));
    else $('#inspection').textContent = '模型调用尚未产生审计记录，请检查配置。';
    const incomplete = [body.shared, body.isolated].some(arm => arm.status === 'error');
    const sameDeal = body.shared.dealPrice !== null && body.shared.dealPrice === body.isolated.dealPrice;
    setStatus(incomplete ? '本次有执行或输出校验失败，保留已观测记录；未完成的结果不能声称通过。' : `实验完成 · ${((body.finishedAt - body.startedAt) / 1000).toFixed(2)} 秒 · ${sameDeal ? '两组成交价相同。' : '两组交易结果见下方。'} 共享上下文 ${body.shared.audit.violations} 次隔离违规，隔离上下文 ${body.isolated.audit.violations} 次。`, incomplete);
    $('#download').disabled = false;
  } catch (error) { setStatus(`运行失败：${error.message}。${result ? '下方保留的是上一次实验结果。' : '请确认本地服务正在运行。'}`, true); }
  finally { button.disabled = false; button.innerHTML = previous; document.querySelectorAll('#config input, #config select').forEach(el => { el.disabled = false; }); }
});

$('#download').addEventListener('click', () => {
  if (!result) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `negotiation-audit-${result.id}.json`; link.click(); URL.revokeObjectURL(url);
});

fetch('/api/config').then(r => r.json()).then(config => {
  if (config.liveConfigured) {
    liveModel = config.model;
    const option = $('#backend option[value="live"]'); option.disabled = false; option.textContent = `真实模型 · ${config.model}`;
  }
}).catch(() => setStatus('无法读取服务配置，请确认本地服务正在运行。', true));
