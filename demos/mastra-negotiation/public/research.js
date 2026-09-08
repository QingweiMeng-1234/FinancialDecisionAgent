const $ = selector => document.querySelector(selector);
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const seconds = ms => `${(ms / 1000).toFixed(2)}s`;
const labels = { pending: '待执行', running: '处理中', succeeded: '已保存', failed_retryable: '可重试' };
let currentRun = null;
let selectedTask = { mode: 'serial', index: 0 };
let polling = false;
let pollTimer;
let submitting = false;

function selectScenario(name, focus = false) {
  for (const key of ['negotiation', 'research', 'interrupt']) {
    const selected = key === name;
    $(`#scenario-${key}`).hidden = !selected;
    $(`#tab-${key}`).setAttribute('aria-selected', String(selected));
    $(`#tab-${key}`).tabIndex = selected ? 0 : -1;
  }
  history.replaceState(null, '', `#${name}`);
  if (focus) $(`#tab-${name}`).focus();
}
const scenarios = ['negotiation', 'research', 'interrupt'];
for (const name of scenarios) {
  $(`#tab-${name}`).addEventListener('click', () => selectScenario(name));
  $(`#tab-${name}`).addEventListener('keydown', event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? scenarios.length - 1 : (scenarios.indexOf(name) + (event.key === 'ArrowRight' ? 1 : -1) + scenarios.length) % scenarios.length;
      selectScenario(scenarios[next], true);
    }
  });
}
selectScenario(scenarios.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'negotiation');

async function request(path, data) {
  const response = await fetch(path, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const body = await response.json(); if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`); return body;
}
function status(text, isError = false) { $('#research-status').textContent = text; $('#research-status').className = isError ? 'error' : ''; }
function busy(value) {
  document.querySelectorAll('#research-config input, #research-config select, #research-config button').forEach(el => { el.disabled = value; });
  $('#research-retry').disabled = value || !currentRun || currentRun.status !== 'partial';
  $('#research-run').textContent = value ? '研究执行中…' : '运行研究对照 ↗';
}
function duration(phase) { return (phase.finishedAt ?? Date.now()) - phase.startedAt; }
function chart(arm, phase) {
  const otherPhases = ['serial', 'parallel'].flatMap(mode => currentRun[mode].phases.filter(p => p.number === phase.number));
  const measured = Math.max(...otherPhases.map(duration), 1);
  const running = otherPhases.some(p => !p.finishedAt);
  const span = Math.max(measured, running ? phase.taskIds.length * currentRun.config.delayMs + 200 : 1);
  const left = 25; const width = 550;
  let svg = `<svg viewBox="0 0 600 135" role="img" aria-label="第 ${phase.number} 批任务实测时间线"><text x="25" y="15" font-size="10" fill="#6b777b">0s</text><text x="530" y="15" font-size="10" fill="#6b777b">${seconds(span)}</text>`;
  arm.tasks.forEach((task, i) => {
    const attempt = task.attempts.find(a => a.phase === phase.number); const y = 28 + i * 32;
    svg += `<text x="5" y="${y + 16}" font-size="11" fill="#6b777b">${task.companyId}</text><line x1="25" y1="${y + 12}" x2="580" y2="${y + 12}" stroke="#dbe2df"/>`;
    if (attempt) {
      const x = left + Math.max(0, attempt.startedAt - phase.startedAt) / span * width;
      const w = Math.max(2, ((attempt.finishedAt ?? Date.now()) - attempt.startedAt) / span * width);
      const fill = attempt.error ? '#c46b51' : attempt.finishedAt ? '#39896d' : '#90b9a4';
      svg += `<rect x="${x.toFixed(2)}" y="${y}" width="${w.toFixed(2)}" height="24" rx="3" fill="${fill}"><title>${task.companyId}：${seconds((attempt.finishedAt ?? Date.now()) - attempt.startedAt)}${attempt.error ? ' · 超时/失败' : ''}</title></rect>`;
    }
  });
  return `${svg}</svg>`;
}
function inspect() {
  if (!currentRun) return;
  const { mode, index } = selectedTask; const task = currentRun[mode].tasks[index]; const report = currentRun.documents[index];
  const out = task.output;
  $('#research-inspection').innerHTML = `<p class="audit-title">${mode === 'serial' ? '串行组' : '并行组'} · ${esc(report.company)} · ${labels[task.status]}</p><blockquote class="report-source">${esc(report.text)}</blockquote>
    ${out ? `<p class="research-risk">2025 年营收：<strong>${out.revenue} ${esc(out.unit)}</strong> · 同比 <strong>${out.yoyPercent > 0 ? '+' : ''}${out.yoyPercent}%</strong><br>风险：${esc(out.risk)}<br>原文校验：营收、同比、风险与引用均匹配。</p>` : '<p class="explanation">尚无通过校验的研究结果。超时不会被当作零营收或无风险。</p>'}
    ${task.attempts.map(a => `<details><summary>尝试 ${a.number} · ${a.error ? esc(a.error) : a.finishedAt ? '完成' : '处理中'} · ${a.id.slice(0, 8)}</summary><pre>${esc(JSON.stringify(a, null, 2))}</pre></details>`).join('')}
    <p class="explanation">刷新可恢复同一运行。成功任务的尝试 ID 与结果保持不变，重试只追加失败任务的记录。</p>`;
}
function render() {
  for (const mode of ['serial', 'parallel']) {
    const arm = currentRun[mode]; const root = $(`#research-${mode}`);
    const done = arm.tasks.filter(t => t.status === 'succeeded').length;
    const failed = arm.tasks.filter(t => t.status === 'failed_retryable').length;
    const badge = root.querySelector('[data-research-badge]');
    badge.textContent = arm.active ? `${arm.active} 个任务处理中` : failed ? `${failed} 项失败 · ${done} 项保留` : done === 3 ? '3 项已保存' : '等待执行';
    badge.className = `badge ${failed ? 'fail' : done === 3 ? 'pass' : 'neutral'}`;
    const first = arm.phases[0];
    root.querySelector('[data-research-content]').innerHTML = `<div class="metrics"><div><span>初次批次耗时（含失败）</span><strong>${first ? seconds(duration(first)) : '—'}</strong></div><div><span>实测峰值并发</span><strong>${arm.peakConcurrency}<small> / ${mode === 'serial' ? 1 : currentRun.config.concurrency}</small></strong></div></div>
      ${arm.phases.map(p => `<div class="research-phase"><p>${p.number === 1 ? '首次执行' : `定向重试 #${p.number - 1}`} · ${p.taskIds.join(' / ')} · ${seconds(duration(p))}${p.interrupted ? ' · 服务中断' : ''}</p>${chart(arm, p)}</div>`).join('')}
      <div data-research-tasks></div><p class="research-stats-note">按 A / B / C 稳定汇总 · ${arm.tasks.reduce((n, t) => n + t.attempts.length, 0)} 次任务尝试 · ${arm.tasks.reduce((n, t) => n + t.attempts.filter(a => a.providerPrompt).length, 0)} 次模型调用</p>`;
    const list = root.querySelector('[data-research-tasks]');
    arm.tasks.forEach((task, index) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'research-task';
      if (selectedTask.mode === mode && selectedTask.index === index) button.classList.add('active');
      const out = task.output;
      button.innerHTML = `<span class="research-task-head"><span>${esc(currentRun.documents[index].company)}</span><span class="badge ${task.status === 'succeeded' ? 'pass' : task.status === 'failed_retryable' ? 'fail' : 'neutral'}">${labels[task.status]}</span></span><p>${out ? `营收 ${out.revenue} 亿元 · 同比 ${out.yoyPercent > 0 ? '+' : ''}${out.yoyPercent}%<br>${esc(out.risk)}` : task.status === 'failed_retryable' ? '结果未生成；其他公司产物保留。' : '等待材料检索与模型提取。'}</p><p><small>尝试 ${task.attempts.length} 次${task.attempts.length ? ` · ${task.attempts.at(-1).id.slice(0, 8)}` : ''}</small></p>`;
      button.addEventListener('click', () => { selectedTask = { mode, index }; document.querySelectorAll('.research-task').forEach(el => el.classList.remove('active')); button.classList.add('active'); inspect(); }); list.append(button);
    });
  }
  const terminal = currentRun.status !== 'running';
  busy(!terminal || submitting); $('#research-export').disabled = false;
  if (terminal) {
    const phaseA = currentRun.serial.phases[0]; const phaseB = currentRun.parallel.phases[0];
    const ratio = phaseA?.finishedAt && phaseB?.finishedAt ? duration(phaseA) / Math.max(1, duration(phaseB)) : null;
    status(currentRun.status === 'completed' ? `研究完成 · 两组均保存 3 家结果${ratio ? ` · 初次批次实测加速 ${ratio.toFixed(2)}×` : ''}。恢复能力来自共同的任务账本。` : currentRun.status === 'partial' ? '部分完成：成功产物已保存。点击「只重试失败任务」，观察 A/C 的尝试次数保持 1。' : `执行异常：${currentRun.error || '请查看任务记录'}`, currentRun.status === 'error');
  } else status(`研究运行中 · ${currentRun.config.backend === 'fixture' ? '本地模拟' : '真实模型'} · 每份材料人为等待 ${currentRun.config.delayMs / 1000}s · 先运行串行组，再运行并行组。`);
  // Preserve an expanded receipt while polling; completed rendering refreshes it once.
  if (!$('#research-inspection details[open]')) inspect();
}
async function poll(id) {
  if (polling) return; polling = true;
  try {
    currentRun = await request(`/api/research/runs/${encodeURIComponent(id)}`); render();
    if (currentRun.status === 'running') pollTimer = setTimeout(() => poll(id), 400);
  } catch (error) { status(`无法读取任务：${error.message}。记录已保存在服务端，刷新页面可重新读取。`, true); busy(false); }
  finally { polling = false; }
}
async function submit(retry) {
  if (submitting) return; submitting = true; clearTimeout(pollTimer); busy(true); status(retry ? '正在领取失败任务，成功结果保持不变…' : '正在创建持久化研究任务…');
  try {
    const path = retry ? `/api/research/runs/${currentRun.id}/retry` : '/api/research/runs';
    const config = retry ? {} : { concurrency: Number($('#research-concurrency').value), delayMs: Number($('#research-delay').value), failB: $('#research-fail').checked, backend: $('#research-backend').value };
    currentRun = await request(path, config);
    try { localStorage.setItem('mastra-research-last-run', currentRun.id); } catch { /* Ledger remains on server. */ }
    selectedTask = { mode: 'serial', index: retry ? 1 : 0 }; render(); pollTimer = setTimeout(() => poll(currentRun.id), 200);
  } catch (error) { status(`无法启动：${error.message}`, true); busy(false); }
  finally { submitting = false; }
}
$('#research-config').addEventListener('submit', event => { event.preventDefault(); submit(false); });
$('#research-retry').addEventListener('click', () => submit(true));
$('#research-export').addEventListener('click', () => {
  if (!currentRun) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentRun, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `research-audit-${currentRun.id}.json`; link.click(); URL.revokeObjectURL(url);
});
request('/api/config').then(config => {
  if (config.liveConfigured) { const option = $('#research-backend option[value=live]'); option.disabled = false; option.textContent = `真实模型 · ${config.model}`; }
}).catch(() => {});
$('#research-backend').addEventListener('change', () => {
  $('#research-backend-note').textContent = $('#research-backend').value === 'fixture' ? '全部为虚构财报。模拟等待代表材料检索耗时，模型提取经过真实 Mastra 调用；不衡量真实供应商延迟。' : '使用同一个真实模型，会产生供应商费用；初次最多 6 次模型调用。仍含人为检索等待，真实输出可能校验失败。';
});
try { const id = localStorage.getItem('mastra-research-last-run'); if (id) poll(id); } catch { /* Optional local navigation state. */ }
