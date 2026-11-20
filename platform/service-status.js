// Configuration and past calls do not prove that a provider is live now.
const RECENT_CALL_MS = 15 * 60 * 1000;
const REFRESH_MS = 60 * 1000;
const TRACE_TASK_LIMIT = 6;

function latestCall(traces, provider, model) {
  return traces.filter(call => call.provider === provider && (!model || call.model === model))
    .filter(call => Number.isFinite(Date.parse(call.timestamp)))
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0];
}

function callStatus(call, now, detail) {
  if (!call) return { state: 'warning', label: '待首次调用', detail };
  const time = new Date(call.timestamp).toLocaleString('zh-CN', { hour12: false });
  if (call.status === 'failed') {
    const code = call.error?.httpStatus;
    return { state: 'error', label: '调用异常', detail: `${detail} 最近一次调用失败：${time}${code ? `（HTTP ${code}）` : ''}。` };
  }
  const recent = now - Date.parse(call.timestamp) <= RECENT_CALL_MS;
  if (call.status === 'started') {
    return { state: 'warning', label: recent ? '调用中' : '结果未确认', detail: `${detail} 调用开始于 ${time}，尚未取得结束记录。` };
  }
  if (call.status !== 'succeeded') return { state: 'warning', label: '待验证', detail };
  return {
    state: recent ? 'success' : 'neutral', label: recent ? '最近成功' : '上次成功',
    detail: `${detail} 最近一次成功：${time}；历史调用结果不代表此刻实时可用。`
  };
}

export function deriveServiceStatus({ osint = null, intelligence = null, kb = null, viking = null, traces = [], now = Date.now() } = {}) {
  const apiOk = osint?.ok === true;
  const api = {
    name: '研究 API', state: apiOk ? 'success' : 'error', label: apiOk ? '可用' : '不可用',
    detail: apiOk ? '研究服务接口响应正常。外部采集、模型与数据库分别核验。' : '研究服务接口未响应或返回错误；外部依赖状态无法据此确认。'
  };
  let firecrawl = { name: 'Firecrawl AI', state: 'neutral', label: '状态未知', detail: '无法读取采集工具状态。' };
  if (apiOk) {
    if (osint.sources?.length && !osint.sources.some(source => source.engine === 'firecrawl')) {
      firecrawl = { name: 'Firecrawl AI', state: 'neutral', label: '未启用', detail: '当前使用 RSS / Atom 来源模式。' };
    } else {
      firecrawl = { name: 'Firecrawl AI', ...callStatus(latestCall(traces, 'firecrawl'), now,
        `采集工具为 Firecrawl。来源列表不能证明凭据或服务可用，状态依据最近 ${TRACE_TASK_LIMIT} 个任务的真实调用记录。`) };
    }
  }
  const provider = intelligence?.trendProvider || osint?.provider || intelligence?.provider;
  const model = provider?.model;
  const name = model ? `DeepSeek · ${model.replace(/^deepseek-/, '')}` : 'DeepSeek';
  let deepseek = { name, state: 'neutral', label: '状态未知', detail: '无法读取大模型配置或调用状态。' };
  if (provider) {
    const configured = provider.configured === true && String(provider.id || '').includes('deepseek');
    if (!configured) {
      deepseek = { name: 'DeepSeek', state: 'neutral', label: '未配置', detail: '尚未启用 DeepSeek 模型服务；当前可使用规则统计。' };
    } else {
      const call = latestCall(traces, 'deepseek', model);
      deepseek = { name, ...callStatus(call, now, `已配置模型 ${model || '未指定'}。调用状态取自最近 ${TRACE_TASK_LIMIT} 个任务的记录。`) };
      if (!call) deepseek.label = '已配置 · 待首次调用';
      if (call?.status === 'succeeded' && call.details?.actualModel && call.details.actualModel !== model) {
        deepseek.state = 'warning';
        deepseek.label = '模型不匹配';
        deepseek.detail = `配置模型 ${model}，实际返回 ${call.details.actualModel}；需核对模型路由。`;
      }
    }
  }
  const openviking = viking?.ready?.status === 'ready' && viking?.health?.healthy === true
    ? { name: 'OpenViking', state: 'success', label: '已启动 · 待接入',
        detail: '本地 OpenViking 健康与就绪探针均通过。目前采编 Agent 仍未调用其上下文检索或写入接口。' }
    : viking?.health?.healthy === true
      ? { name: 'OpenViking', state: 'warning', label: '初始化中', detail: '服务进程已响应，但上下文存储或向量检索尚未就绪；采编流程尚未接入。' }
      : { name: 'OpenViking', state: 'neutral', label: '未连接', detail: '无法读取本地 OpenViking 健康状态；采编流程尚未接入上下文服务。' };
  let postgres = { name: 'PostgreSQL', state: 'neutral', label: '状态未知', detail: '知识库数据库健康接口未返回有效状态。' };
  if (kb?.ok === true) {
    const store = kb.store || {};
    if (store.kind === 'postgres') {
      postgres = { name: 'PostgreSQL', state: 'success', label: '已连接', detail: '知识库健康接口已成功建立 PostgreSQL 连接。' };
    } else if (store.kind === 'sqlite') {
      postgres = { name: 'PostgreSQL', state: store.fallback ? 'warning' : 'neutral',
        label: store.fallback ? '未连接' : '未启用',
        detail: store.fallback ? 'PostgreSQL 当前未连接；知识库已回退到本地只读 SQLite。采编任务与草稿另用 SQLite 持久化。' : '知识库当前配置为本地只读 SQLite；未使用 PostgreSQL。采编任务与草稿另用 SQLite 持久化。' };
    }
  }
  return { api, firecrawl, deepseek, openviking, postgres };
}

async function readStatus(url) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error('Status request failed');
  const payload = await response.json();
  if (payload.ok !== true) throw new Error('Invalid status response');
  return payload;
}

function fulfilled(result) { return result.status === 'fulfilled' ? result.value : null; }

async function readVikingStatus() {
  const responses = await Promise.allSettled(['health', 'ready'].map(async path => {
    const response = await fetch(`http://127.0.0.1:1933/${path}`, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('OpenViking not ready');
    return response.json();
  }));
  return { health: fulfilled(responses[0]), ready: fulfilled(responses[1]) };
}

function renderStatuses(statuses) {
  for (const [id, status] of Object.entries(statuses)) {
    const indicator = document.querySelector(`[data-service="${id}"]`);
    if (!indicator) continue;
    indicator.dataset.state = status.state;
    const name = indicator.querySelector('[data-service-name]');
    const value = indicator.querySelector('[data-service-value]');
    if (name && name.textContent !== status.name) name.textContent = status.name;
    if (value && value.textContent !== status.label) value.textContent = status.label;
    indicator.title = status.detail;
    indicator.setAttribute('aria-label', `${status.name}：${status.label}。${status.detail}`);
  }
}

let pendingRefresh = null;
export function refreshServiceStatus() {
  if (pendingRefresh) return pendingRefresh;
  pendingRefresh = refresh().finally(() => { pendingRefresh = null; });
  return pendingRefresh;
}

async function refresh() {
  const button = document.getElementById('refresh-service-status');
  if (button) button.disabled = true;
  try {
    const results = await Promise.allSettled([
      readStatus('/api/osint/state'), readStatus('/api/intelligence/state'), readStatus('/api/kb/health'), readVikingStatus()
    ]);
    const snapshot = { osint: fulfilled(results[0]), intelligence: fulfilled(results[1]), kb: fulfilled(results[2]), viking: fulfilled(results[3]) };
    renderStatuses(deriveServiceStatus(snapshot));
    const tasks = (snapshot.osint?.tasks || []).slice().sort((a, b) =>
      (Date.parse(b.updatedAt || b.createdAt) || 0) - (Date.parse(a.updatedAt || a.createdAt) || 0)
    ).slice(0, TRACE_TASK_LIMIT);
    snapshot.traces = [];
    // Bound concurrent reads so health checks do not crowd out workbench requests.
    for (let index = 0; index < tasks.length; index += 3) {
      const traces = await Promise.allSettled(tasks.slice(index, index + 3).map(task => readStatus(`/api/osint/tasks/${encodeURIComponent(task.id)}/execution`)));
      snapshot.traces.push(...traces.flatMap(result => fulfilled(result)?.executionTrace || []));
    }
    renderStatuses(deriveServiceStatus(snapshot));
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    if (button) button.title = `刷新服务状态（上次检查 ${time}）；不会发起付费采集或模型推理。`;
  } finally {
    if (button) button.disabled = false;
  }
}

let started = false;
export function startServiceStatus() {
  if (started) return;
  started = true;
  document.getElementById('refresh-service-status')?.addEventListener('click', refreshServiceStatus);
  const masthead = document.querySelector('.agents-mast');
  if (masthead && typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      document.documentElement.style.setProperty('--osint-mast-height', `${Math.ceil(masthead.getBoundingClientRect().height)}px`);
    }).observe(masthead);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshServiceStatus(); });
  setInterval(() => { if (!document.hidden) refreshServiceStatus(); }, REFRESH_MS);
  refreshServiceStatus();
}
