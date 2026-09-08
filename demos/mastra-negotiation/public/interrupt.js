const $ = selector => document.querySelector(selector);
const seconds = ms => ms == null ? '—' : `${(ms / 1000).toFixed(2)}s`;
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const names = { queue: 'A · 单 Agent：排队', hook: 'B · 单 Agent＋Hook：检查点抢占', independent: 'C · 双 Agent：独立推进' };
const descriptions = { queue: '先完成研究，再处理预警。', hook: '步骤结束时检查事件；处理完预警，从保存的下一步恢复。', independent: '研究和预警各有一个执行循环；共享模型与指令。' };
let run = null, timer, epoch = 0;
function setStatus(text) { $('#interrupt-status').textContent = text; }
async function request(path, data) {
  const response = await fetch(path, data === undefined ? {} : { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data) });
  const body = await response.json(); if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`); return body;
}
function controls(working) {
  $('#interrupt-config').querySelectorAll('select,button').forEach(el => { el.disabled = working; });
  $('#interrupt-start').textContent = working ? '三组执行中…' : '开始插单对照 ↗';
  $('#interrupt-inject').disabled = !run || run.status !== 'running' || Boolean(run.event);
  $('#interrupt-export').disabled = !run;
}
function drawCharts() {
  if (!run) return;
  const elapsed = (run.finishedAt ?? Date.now()) - run.startedAt;
  const span = Math.max(run.config.researchMs + run.config.alertMs, elapsed, 1);
  for (const arm of run.arms) {
    const svg = $(`#interrupt-chart-${arm.mode}`); if (!svg) continue;
    const width = Math.max(100, svg.getBoundingClientRect().width); const available = width - 10;
    svg.setAttribute('viewBox', `0 0 ${width} 110`);
    const x = t => 5 + Math.max(0, t - run.startedAt) / span * available;
    let markup = `<title>${esc(names[arm.mode])}：研究和预警实测时间线</title><text x="5" y="12" font-size="12" fill="#6b777b">0s</text><text x="${width - 5}" y="12" text-anchor="end" font-size="12" fill="#6b777b">${seconds(span)}</text>`;
    for (const y of [30, 68]) markup += `<line x1="5" y1="${y+12}" x2="${width-5}" y2="${y+12}" stroke="#dbe2df"/>`;
    for (const record of [...arm.research, ...(arm.alert ? [arm.alert] : [])]) {
      const start = x(record.startedAt); const end = x(record.finishedAt ?? Date.now());
      markup += `<rect x="${start}" y="${record.kind === 'research' ? 30 : 68}" width="${Math.max(1,end-start)}" height="24" fill="${record.kind === 'research' ? '#39896d' : '#bb7042'}" opacity="${record.finishedAt ? 1 : .55}"><title>${record.kind === 'research' ? `研究步骤 ${record.step}` : '预警核查'} · ${seconds((record.finishedAt ?? Date.now())-record.startedAt)}</title></rect>`;
    }
    if (run.event) {
      markup += `<line x1="${x(run.event.at)}" x2="${x(run.event.at)}" y1="20" y2="98" stroke="#17252b" stroke-width="2"><title>事件到达 ${seconds(run.event.at-run.startedAt)}</title></line>`;
      const deadline = x(run.event.at + run.config.deadlineMs);
      if (deadline <= width) markup += `<line x1="${deadline}" x2="${deadline}" y1="20" y2="98" stroke="#ad4633" stroke-dasharray="4 3"><title>预警完成期限</title></line>`;
    }
    svg.innerHTML = markup;
  }
}
function render() {
  controls(run.status === 'running');
  const now = Date.now(); const total = run.config.researchMs / run.config.stepMs;
  $('#interrupt-results').innerHTML = run.arms.map(arm => {
    const alert = arm.alert; const response = alert?.output ? alert.finishedAt - run.event.at : null;
    const met = response !== null ? response <= run.config.deadlineMs : null;
    const badge = arm.status === 'error' ? '执行异常' : met === null ? '等待预警完成' : met ? '预警按时完成' : '预警超过期限';
    const pause = arm.mode === 'hook' && alert && arm.completedSteps.length < total ? (alert.finishedAt ?? now) - alert.startedAt : arm.metrics?.pauseMs ?? 0;
    const researchElapsed = (arm.researchFinishedAt ?? now) - run.startedAt;
    return `<article class="interrupt-arm"><header><h2>${names[arm.mode]}</h2><span class="badge ${arm.status === 'error' || met === false ? 'fail' : met ? 'pass' : 'neutral'}">${badge}</span></header><p>${descriptions[arm.mode]} 已完成 ${arm.completedSteps.length}/${total} 步 · ${arm.calls.length} 次模型调用。</p>
      <div class="interrupt-metrics"><div><span>预警到达 → 完成</span><strong>${seconds(response)}</strong></div><div><span>预警排队</span><strong>${run.event ? seconds(alert ? alert.startedAt-run.event.at : now-run.event.at) : '—'}</strong></div><div><span>研究主动停顿</span><strong>${seconds(pause)}</strong></div><div><span>研究${arm.researchFinishedAt ? '完成耗时' : '已历时'}</span><strong>${seconds(researchElapsed)}</strong></div></div>
      <div class="interrupt-lanes"><span>研究<br><br>预警</span><svg class="interrupt-chart" id="interrupt-chart-${arm.mode}" role="img" aria-label="${names[arm.mode]}时间线"></svg></div>
      <div class="interrupt-legend">绿色：研究步骤 · 棕色：预警处理 · 黑线：事件到达 · 红虚线：完成期限</div>
      ${alert?.output ? `<p>核查结果：${esc(alert.output.quote)}<br>风险代码：${esc(alert.output.risk)}</p>` : ''}</article>`;
  }).join('');
  drawCharts();
  const eventText = run.event ? `${run.event.source === 'manual' ? '手动' : '自动'}事件已于 ${seconds(run.event.at-run.startedAt)} 同时送达三组` : `等待插单：第 ${run.config.autoAfterMs/1000} 秒自动发送，也可立即发送`;
  setStatus(`${run.status === 'running' ? '执行中' : run.status === 'completed' ? '对照完成' : '执行异常'} · ${eventText}。预警期限 ${run.config.deadlineMs/1000} 秒；研究净工作量 ${run.config.researchMs/1000} 秒，检查间隔 ${run.config.stepMs/1000} 秒。`);
  $('#interrupt-receipts').textContent = JSON.stringify(run, null, 2);
}
async function poll(id, version) {
  try {
    const result = await request(`/api/interrupt/runs/${id}`);
    if (version !== epoch) return;
    run = result; render();
    if (run.status === 'running') timer = setTimeout(() => poll(id, version), 300);
  } catch (error) { if (version === epoch) { controls(false); setStatus(`无法读取：${error.message}。本场景记录仅保存在服务内存，服务重启后需重新运行。`); } }
}
$('#interrupt-config').addEventListener('submit', async event => {
  event.preventDefault(); const version = ++epoch; clearTimeout(timer); controls(true); $('#interrupt-inject').disabled = true;
  try {
    const researchMs = Number($('#interrupt-duration').value);
    run = await request('/api/interrupt/runs', { researchMs, stepMs:Number($('#interrupt-step').value), alertMs:1000, autoAfterMs:researchMs/6, deadlineMs:Number($('#interrupt-deadline').value) });
    try { sessionStorage.setItem('mastra-interrupt-run', run.id); } catch { /* Optional page recovery. */ }
    render(); timer = setTimeout(() => poll(run.id, version), 300);
  } catch (error) { controls(false); setStatus(`无法启动：${error.message}`); }
});
$('#interrupt-inject').addEventListener('click', async () => {
  if (!run) return; const version = ++epoch; clearTimeout(timer); $('#interrupt-inject').disabled = true;
  try { run = await request(`/api/interrupt/runs/${run.id}/event`, {}); render(); }
  catch (error) { setStatus(`发送结果：${error.message}`); }
  timer = setTimeout(() => poll(run.id, version), 100);
});
$('#interrupt-export').addEventListener('click', () => {
  if (!run) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(run,null,2)],{type:'application/json'})); const link = document.createElement('a');
  link.href = url; link.download = `interrupt-audit-${run.id}.json`; link.click(); URL.revokeObjectURL(url);
});
new ResizeObserver(drawCharts).observe($('#interrupt-results'));
try { const id = sessionStorage.getItem('mastra-interrupt-run'); if (id) poll(id, epoch); } catch { /* No stored run. */ }
