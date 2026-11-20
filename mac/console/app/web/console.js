let csrf = '';
let state = null;
let busy = false;
let shuttingDown = false;
let inspectorMode = 'diagnostics';
let inspectorBusy = false;
let inspectorSequence = 0;
let diagnosticModels = [];
let routesData = {items: [], selected: ''};
let routesBusy = false;
let routesSequence = 0;
let editingRoute = null;
let selectedRouteId = '';
let templateRoute = null;
let copyingRoute = false;
let routeBaseline = '';
const nodeCache = {shared: {items: [], current: {}}};
const nodeChecks = {shared: {}};
const checkingNodes = new Set();
const nodeLocations = new Map();
const locatingNodes = new Map();
let routeLocationTimer;
let routeLocationVersion = 0;
let routeLocationHost = '';
let routeLocationState = 'unknown';
let routeSavePending = false;

const $ = id => document.getElementById(id);

function say(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}

async function jsonRequest(path, payload, signal) {
  const options = payload === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Local-CSRF': csrf},
    body: JSON.stringify(payload)
  };
  if (signal) options.signal = signal;
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '操作失败');
  return data;
}

function nodeEndpoint(item) {
  if (!item.server_addr) return item.mode === 'direct' ? '直连配置' : '地址未配置';
  const host = item.server_addr.includes(':') ? `[${item.server_addr}]` : item.server_addr;
  return `${host}:${item.server_port}`;
}

function nodeRegion(item) {
  const code = /^[A-Z]{2}$/.test(item.country || '') ? item.country : '';
  const flag = code ? String.fromCodePoint(...[...code].map(c => 0x1f1e6 + c.charCodeAt(0) - 65)) : '';
  let region = '';
  try { region = code ? new Intl.DisplayNames(['zh-CN'], {type: 'region'}).of(code) : ''; } catch (_) { region = code; }
  return [flag, ...new Set([region, item.city].filter(Boolean))].filter(Boolean).join(' ');
}

function publicIP(host) {
  if (/^\d+(?:\.\d+){3}$/.test(host)) {
    const octets = host.split('.').map(Number);
    if (octets.some(value => value > 255)) return false;
    const [a, b, c] = octets;
    return a > 0 && a < 224 && ![10, 127].includes(a) && !(a === 169 && b === 254)
      && !(a === 172 && b >= 16 && b <= 31) && !(a === 192 && b === 168)
      && !(a === 100 && b >= 64 && b <= 127) && !(a === 192 && b === 0 && c === 0)
      && !(a === 192 && b === 0 && c === 2) && !(a === 198 && [18, 19].includes(b))
      && !(a === 198 && b === 51 && c === 100)
      && !(a === 203 && b === 0 && c === 113);
  }
  if (!/^[23][\da-f:]+$/i.test(host) || /^2001:db8(?::|$)/i.test(host)) return false;
  try { return new URL(`http://[${host}]/`).hostname.startsWith('['); } catch (_) { return false; }
}

function locationFields(result) {
  const country = /^[A-Z]{2}$/.test(result?.country || '') ? result.country : '';
  if (country && !Array.from($('route-country').options).some(option => option.value === country)) {
    const option = document.createElement('option');option.value = country;option.textContent = country;$('route-country').append(option);
  }
  $('route-country').value = country;
  $('route-city').value = result?.city || '';
}

function renderLocation() {
  const place = nodeRegion({country: $('route-country').value, city: $('route-city').value});
  $('route-location').textContent = routeLocationState === 'loading' ? '识别中…' : place || '未识别';
  $('route-locate').disabled = routesBusy || busy || routeSavePending || routeLocationState === 'loading'
    || !$('route-server').value.trim();
}

function updateEditorLocation(result) {
  const wasClean = routeSignature() === routeBaseline;
  routeLocationState = result?.state || 'unknown';
  locationFields(result?.state === 'ok' ? result : {});
  renderLocation();
  if (wasClean) routeBaseline = routeSignature();
}

async function locateEditor(force = false, allowDomain = false) {
  const host = $('route-server').value.trim();
  const version = routeLocationVersion;
  if (!host || (!force && !allowDomain && !publicIP(host))) return;
  routeLocationState = 'loading';renderLocation();
  let request = !force && nodeLocations.has(host) ? Promise.resolve(nodeLocations.get(host)) : locatingNodes.get(host);
  if (!request) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    request = jsonRequest('/api/vps-nodes', {action: 'location', server_addr: host}, controller.signal)
      .catch(() => ({state: 'unknown', country: '', city: ''})).finally(() => clearTimeout(timeout));
    locatingNodes.set(host, request);
  }
  const result = await request;
  nodeLocations.set(host, result);
  if (locatingNodes.get(host) === request) locatingNodes.delete(host);
  if (version === routeLocationVersion && $('route-manager').open
      && $('route-server').value.trim() === host) updateEditorLocation(result);
}

function scheduleLocation() {
  clearTimeout(routeLocationTimer);
  routeLocationTimer = setTimeout(() => locateEditor(), 250);
}

function routeAddressChanged() {
  clearTimeout(routeLocationTimer);
  const host = $('route-server').value.trim();
  if (host !== routeLocationHost) {
    ++routeLocationVersion;routeLocationHost = host;routeLocationState = 'unknown';locationFields({});renderLocation();
  }
  routeManagerControls();
}

function nodeStatus(item) {
  return item.pending ? '待应用' : (item.active_services || []).length > 1 ? '两条连接使用中'
    : item.active_services?.[0] === 'ollama' ? '模型使用中' : item.active_services?.[0] === 'sub2api' ? 'Sub2 使用中' : '备用';
}

function networkText(result) {
  if (!result) return '未检测';
  const describe = (name, record) => {
    if (!record) return `${name} 未检测`;
    return ['ok', 'success'].includes(record.state)
      ? `${name} ${Number.isFinite(record.latency_ms) ? Math.round(record.latency_ms) + ' ms' : '可达'}`
      : `${name} ${record.state === 'unavailable' || record.state === 'unsupported' ? '不可用' : record.state === 'timeout' ? '超时' : '不通'}`;
  };
  const measured = Date.parse(result.checked_at);
  const timing = Number.isFinite(measured) ? Date.now() - measured > 300000 ? ' · 记录过期'
    : ` · ${new Date(measured).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})} 检测` : '';
  return `${describe('Ping', result.ping)} · ${describe('TCP', result.tcp)}${timing}`;
}

function currentNodeCheck(service, item) {
  const cached = nodeChecks[service][item.id];
  return cached?.target === nodeEndpoint(item) ? cached.result : item.connectivity;
}

function nodeButton(label, action, service, item) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'text-button';
  button.textContent = label;
  button.dataset.nodeAction = action;
  button.dataset.nodeId = item.id;
  button.dataset.service = service;
  button.onclick = () => action === 'use' ? applyNode(service, item)
    : action === 'edit' ? openRouteManager(service, item.id)
      : action === 'check' ? checkNode(service, item) : copyNodeIP(item);
  return button;
}

function renderHomeNodes(service) {
  const container = $('vps-nodes');
  const data = nodeCache[service];
  container.replaceChildren();
  for (const item of data.items || []) {
    const row = document.createElement('div');
    row.className = `home-node${item.active ? ' active' : ''}`;
    row.dataset.nodeId = item.id;
    const info = document.createElement('div');
    info.className = 'node-info';
    const heading = document.createElement('div');
    heading.className = 'node-name-line';
    const name = document.createElement('b');
    name.textContent = item.name;
    const status = document.createElement('span');
    status.className = `node-badge${item.pending ? ' pending' : item.active ? ' current' : ''}`;
    status.textContent = nodeStatus(item);
    heading.append(name, status);
    const address = document.createElement('span');
    address.className = 'node-address';
    address.textContent = [nodeEndpoint(item), nodeRegion(item)].filter(Boolean).join(' · ');
    info.append(heading, address);
    const actions = document.createElement('div');
    actions.className = 'home-node-actions';
    actions.append(nodeButton(item.pending ? '应用' : item.active_services?.length > 1 ? '已使用' : '使用', 'use', service, item),
      nodeButton('编辑', 'edit', service, item), nodeButton('检测', 'check', service, item),
      nodeButton('复制 IP', 'copy-ip', service, item));
    const measured = currentNodeCheck(service, item);
    row.append(info, actions);
    if (measured || checkingNodes.has(`${service}:${item.id}`)) {
      const result = document.createElement('span');
      result.className = 'node-network';
      result.textContent = checkingNodes.has(`${service}:${item.id}`) ? '检测中…' : networkText(measured);
      row.append(result);
    }
    container.append(row);
  }
  if (!data.items?.length) {
    const empty = document.createElement('button');
    empty.className = 'node-empty';
    empty.type = 'button';
    empty.textContent = data.error || '＋ 添加 VPS 节点';
    empty.onclick = () => openRouteManager(service);
    container.append(empty);
  }
  renderRouteControls();
}

async function refreshNodes() {
  try {
    const data = await jsonRequest('/api/vps-nodes');
    nodeCache.shared = {...data, items: Array.isArray(data.items) ? data.items : []};
  } catch (_) { nodeCache.shared.error = '节点读取失败 · 点击管理'; }
  const data = nodeCache.shared;
  for (const service of ['ollama', 'sub2api']) {
    const current = data.items.find(item => item.id === data.current?.[service]);
    $(service + '-node').textContent = current ? `当前 VPS · ${current.name} · ${nodeEndpoint(current)}` : '当前 VPS · 未配置或直连';
  }
  $('vps-current').textContent = data.split ? '当前使用不同节点；点“使用”统一切换，开关状态不变。'
    : '两条连接共用 VPS 节点，开关分别控制。';
  renderHomeNodes('shared');
}

function checkAge(check) {
  if (!check?.checked_at) return '';
  const time = Date.parse(check.checked_at);
  if (!Number.isFinite(time)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  const age = seconds < 60 ? '刚刚' : seconds < 3600
    ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 3600)} 小时前`;
  const ttl = Number.isFinite(check.ttl_seconds) ? check.ttl_seconds : 300;
  return `${age}检查${check.stale || check.state === 'stale' || seconds > ttl ? ' · 已过期' : ''}`;
}

function renderRouteControls() {
  if (!state) return;
  const active = Number(state.active_chats) > 0;
  $('ollama-guard').hidden = !active;
  $('ollama-guard').textContent = `有 ${state.active_chats} 个网页回答进行中，结束后可切换节点。`;
  document.querySelectorAll('[data-node-action]').forEach(button => {
    const service = button.dataset.service;
    const item = nodeCache[service].items.find(node => node.id === button.dataset.nodeId);
    const action = button.dataset.nodeAction;
    button.disabled = busy || !item || (action === 'use' &&
      ((item.active_services?.length > 1 && !item.pending) || active || !state.ollama.managed
        || state.sub2api?.ownership === 'external'))
      || (action === 'check' && (item.editable === false || checkingNodes.has(`${service}:${item.id}`)))
      || (action === 'copy-ip' && !item.server_addr);
  });
}

function renderSub2API() {
  const service = state.sub2api;
  $('sub2api-card').hidden = !service;
  if (!service) return;
  const external = service.ownership === 'external';
  const direct = service.mode === 'direct';
  $('sub2api-toggle').setAttribute('aria-checked', String(!!service.running));
  $('sub2api-toggle').disabled = busy || !service.configured || direct || external;
  $('sub2api-url').textContent = service.url || '未配置 API 地址';
  let text = '本地连接已关闭';
  let detail = '开启连接后，再按需检查 Windows API。';
  let tone = 'off';
  if (!service.configured) {
    text = 'Sub2API 尚未配置';
    detail = '请先在本机配置连接资料。';
  } else if (direct) {
    text = '使用直连地址';
    detail = '此连接无需启动本地 frp。';
  } else if (external) {
    text = '端口由外部连接占用';
    detail = '控制台保留原连接，不接管或停止外部 visitor。';
    tone = 'warn';
  } else if (service.running) {
    text = service.listener ? '本地连接已开启' : '本地连接正在启动';
    detail = service.listener ? '本地 visitor 已监听；远端 API 单独检查。'
      : '连接进程已启动，等待本地监听。';
    tone = service.listener ? 'ok' : 'warn';
  }
  $('sub2api-state').textContent = text;
  $('sub2api-state').className = tone;
  $('sub2api-detail').textContent = detail;
  const remote = service.remote || {state: 'unchecked'};
  const stale = remote.stale || remote.state === 'stale' || checkAge(remote).includes('已过期');
  $('sub2api-api-state').textContent = stale ? '记录已过期' : remote.state === 'ok'
    ? '上次检查通过' : remote.state === 'error' ? '上次检查未通过' : '未检查';
  $('sub2api-api-state').className = stale ? 'off' : remote.state === 'ok'
    ? 'ok' : remote.state === 'error' ? 'warn' : 'off';
  const timing = checkAge(remote);
  $('sub2api-api-detail').textContent = remote.state === 'unchecked' || !remote.checked_at
    ? '检查仅由你手动执行，尚未取得健康检查记录。'
    : `${timing} · ${remote.detail || '健康检查记录'}。不代表当前模型调用已通过。`;
  $('sub2api-check').disabled = busy || !service.configured || (!direct && !service.listener);
  $('ccswitch').disabled = busy || !state.apps?.ccswitch;
}

function renderState() {
  if (!state) return;
  const ollama = state.ollama;
  const unchecked = ollama.checked === false;
  const active = Number(state.active_chats) > 0;
  const stale = ollama.stale || checkAge(ollama).includes('已过期');
  $('version').textContent = state.version;
  $('ollama-toggle').setAttribute('aria-checked', String(!!ollama.running));
  $('ollama-toggle').disabled = busy || !ollama.managed || (active && ollama.running);
  $('ollama-url').textContent = ollama.url || '未配置';
  $('ollama-state').textContent = unchecked || stale
    ? ollama.running ? `连接已开 · API ${stale ? '记录过期' : '未检查'}` : '接收连接已关闭'
    : ollama.ok ? 'Windows 模型已连通'
      : ollama.running ? '连接已开 · 远端不可达' : '接收连接已关闭';
  $('ollama-state').className = unchecked || stale ? 'off'
    : ollama.ok ? 'ok' : ollama.running ? 'warn' : 'off';
  $('ollama-detail').textContent = unchecked ? '仅本地状态 · API 尚未检查'
    : `${checkAge(ollama) || '上次检查'} · ${ollama.ok ? 'API 响应正常' : ollama.detail || 'API 未通过'}`;
  renderSub2API();
  renderRouteControls();
}

async function refresh() {
  if (busy) return false;
  try {
    const latest = await jsonRequest('/api/status?passive=1');
    if (shuttingDown) return false;
    state = latest;
    renderState();
    await refreshNodes();
    return true;
  } catch (error) {
    say(error.message, true);
    return false;
  }
}

function setBusy(value) {
  busy = value || shuttingDown;
  document.querySelectorAll('main button, main select, header button').forEach(element => {
    element.disabled = element.id === 'quit' ? shuttingDown : busy;
  });
  $('inspector-quit').disabled = shuttingDown;
  if (!busy) renderState();
}

async function act(service, action) {
  if (busy) return;
  if (service === 'ollama' && action === 'stop' && Number(state?.active_chats) > 0) {
    say('当前网页正在生成回答，暂不能关闭模型连接。', true);
    return;
  }
  if (service === 'model-route' && Number(state?.active_chats) > 0) return;
  setBusy(true);
  say('正在处理…');
  try {
    const data = await jsonRequest('/api/action', {service, action});
    if (!shuttingDown) say(data.message);
  } catch (error) {
    if (!shuttingDown) say(error.message, true);
  } finally {
    setBusy(false);
    await refresh();
  }
}

function inspectorNote(text, error = false) {
  $('inspector-message').textContent = text;
  $('inspector-message').classList.toggle('error', error);
}

function inspectorControls() {
  const service = $('inspector-service').value;
  const diagnostics = inspectorMode === 'diagnostics';
  $('diagnostics-view').hidden = !diagnostics;
  $('logs-view').hidden = diagnostics;
  $('diagnostic-health').disabled = inspectorBusy || service === 'console';
  $('diagnostic-auth').disabled = inspectorBusy || service !== 'sub2api';
  $('diagnostic-model').disabled = inspectorBusy || service !== 'sub2api' || !diagnosticModels.length;
  $('diagnostic-response').disabled = inspectorBusy || service !== 'sub2api'
    || !diagnosticModels.includes($('diagnostic-model').value);
  $('inspector-service').disabled = inspectorBusy;
  $('inspector-refresh').disabled = inspectorBusy;
  $('diagnostic-hint').textContent = service === 'console'
    ? '控制台诊断只显示本机记录，不发送远端请求。'
    : service === 'ollama' ? '健康检查由你点击执行；模型的真实回复需另行验证。'
      : '每一步由你点击执行。最小模型请求使用所选模型，可能消耗上游额度。';
}

function renderDiagnostics(data) {
  const layers = $('diagnostic-layers');
  layers.replaceChildren();
  const titles = {ok: '通过', error: '未通过', unknown: '未检查', stale: '已过期'};
  for (const layer of data.layers || []) {
    const row = document.createElement('li');
    const status = document.createElement('span');
    status.className = `layer-status ${['ok', 'error', 'unknown', 'stale'].includes(layer.state) ? layer.state : 'unknown'}`;
    status.textContent = titles[layer.state] || '未检查';
    const content = document.createElement('div');
    const label = document.createElement('b');
    const detail = document.createElement('p');
    label.textContent = layer.label || layer.id;
    detail.textContent = layer.detail || '尚无记录';
    content.append(label, detail);
    row.append(status, content);
    layers.append(row);
  }
  $('inspector-time').textContent = data.checked_at
    ? `记录时间 ${new Date(data.checked_at).toLocaleString('zh-CN')} · 打开窗口不会自动发请求。`
    : '暂无远端检查记录；打开诊断只读取本机状态。';
  const previous = $('diagnostic-model').value;
  diagnosticModels = [...new Set((data.models || []).filter(value => typeof value === 'string' && value))];
  $('diagnostic-model').replaceChildren();
  for (const name of diagnosticModels) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    $('diagnostic-model').append(option);
  }
  if (!diagnosticModels.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '先验证模型列表';
    $('diagnostic-model').append(option);
  } else {
    $('diagnostic-model').value = diagnosticModels.includes(previous) ? previous
      : diagnosticModels.includes(data.model) ? data.model : diagnosticModels[0];
  }
  inspectorControls();
}

async function loadInspector() {
  const sequence = ++inspectorSequence;
  const service = $('inspector-service').value;
  diagnosticModels = [];
  inspectorBusy = true;
  inspectorControls();
  inspectorNote('正在读取本机记录…');
  try {
    const data = await jsonRequest(inspectorMode === 'logs'
      ? `/api/logs?component=${encodeURIComponent(service)}`
      : `/api/diagnostics?service=${encodeURIComponent(service)}`);
    if (sequence !== inspectorSequence) return;
    if (inspectorMode === 'logs') {
      $('logs-view').textContent = data.lines?.length ? data.lines.join('\n') : '暂无该组件的安全日志。';
      $('inspector-time').textContent = '仅显示白名单事件；密钥、请求正文与原始日志不会出现在此处。';
    } else renderDiagnostics(data);
    inspectorNote('记录已读取。远端检查只在点击步骤按钮后执行。');
  } catch (error) {
    if (sequence === inspectorSequence) inspectorNote(error.message, true);
  } finally {
    if (sequence === inspectorSequence) {
      inspectorBusy = false;
      inspectorControls();
    }
  }
}

async function openInspector(mode, service) {
  inspectorMode = mode;
  $('inspector-title').textContent = mode === 'logs' ? '分类日志' : '分层诊断';
  $('inspector-service').value = service;
  if (!$('inspector').open) $('inspector').showModal();
  await loadInspector();
}

async function runDiagnostic(level) {
  if (inspectorBusy) return;
  const sequence = ++inspectorSequence;
  const service = $('inspector-service').value;
  const payload = {service, level};
  if (level === 'response') {
    const model = $('diagnostic-model').value;
    if (!diagnosticModels.includes(model)) {
      inspectorNote('先验证当前模型列表，再选择实际模型。', true);
      return;
    }
    payload.model = model;
  }
  inspectorBusy = true;
  inspectorControls();
  inspectorNote(level === 'response' ? '正在发送所选模型的最小请求…' : '正在执行所选检查…');
  try {
    const data = await jsonRequest('/api/diagnostics', payload);
    if (sequence !== inspectorSequence) return;
    renderDiagnostics(data);
    inspectorNote('检查已结束，请查看各层结果；失败请求未自动重发。');
    await refresh();
  } catch (error) {
    if (sequence === inspectorSequence) {
      if (level === 'auth') diagnosticModels = [];
      inspectorNote(error.message, true);
    }
  } finally {
    if (sequence === inspectorSequence) {
      inspectorBusy = false;
      inspectorControls();
    }
  }
}

function routesNote(text, error = false) {
  $('routes-message').textContent = text;
  $('routes-message').classList.toggle('error', error);
}

function selectedSavedRoute() {
  return routesData.items.find(item => item.id === selectedRouteId);
}

function routeSignature() {
  return JSON.stringify(['route-name', 'route-server', 'route-country', 'route-city', 'route-port',
    'route-token', 'route-ollama-provider', 'route-ollama-token', 'route-ollama-secret',
    'route-sub2api-provider', 'route-sub2api-token', 'route-sub2api-secret', 'route-input-mode', 'route-toml'].map(id => $(id).value));
}

function canLeaveRoute() {
  return $('route-form').hidden || !routeBaseline || routeSignature() === routeBaseline
    || window.confirm('当前修改还没有保存，放弃修改？');
}

function routeManagerControls() {
  const item = selectedSavedRoute();
  const locked = routesBusy || busy || routeSavePending;
  const editable = !!item && item.editable !== false;
  for (const id of ['routes-refresh', 'route-new', 'route-import']) $(id).disabled = locked;
  $('routes-list').querySelectorAll('button').forEach(button => { button.disabled = locked; });
  for (const id of ['route-duplicate', 'route-copy-ollama', 'route-copy-sub2api']) $(id).disabled = locked || !editable;
  $('route-delete').disabled = locked || !item || item.active;
  $('route-delete-yes').disabled = locked;
  for (const id of ['route-save', 'route-save-use', 'route-edit-cancel']) $(id).disabled = locked;
  const changedTarget = !!item && ($('route-server').value.trim() !== item.server_addr || Number($('route-port').value) !== item.server_port);
  $('route-check').disabled = locked || !editingRoute || !editable || changedTarget || checkingNodes.has(`${'shared'}:${item.id}`);
  if (changedTarget) $('route-check-result').textContent = '保存后可检测新地址';
  $('route-copy-ip').disabled = locked || !$('route-server').value.trim();
  $('route-form').querySelectorAll('input, select, textarea').forEach(field => { field.disabled = locked; });
  renderLocation();
}

function renderManagerNodes() {
  const list = $('routes-list');
  list.replaceChildren();
  for (const item of routesData.items) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'manager-node';
    row.dataset.nodeId = item.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(item.id === selectedRouteId));
    const heading = document.createElement('span');
    heading.className = 'node-name-line';
    const name = document.createElement('b');
    name.textContent = [nodeRegion(item), item.name].filter(Boolean).join(' ');
    const badge = document.createElement('span');
    badge.className = `node-badge${item.pending ? ' pending' : item.active ? ' current' : ''}`;
    badge.textContent = nodeStatus(item);
    heading.append(name, badge);
    const address = document.createElement('span');
    address.className = 'node-address';
    address.textContent = nodeEndpoint(item);
    const network = document.createElement('span');
    network.className = 'node-network';
    network.textContent = networkText(currentNodeCheck('shared', item));
    row.append(heading, address, network);
    row.onclick = () => {
      if (routesBusy || !canLeaveRoute()) return;
      selectedRouteId = item.id;
      editSavedRoute('edit');
    };
    list.append(row);
  }
  routeManagerControls();
}

function closeRouteEditor() {
  clearTimeout(routeLocationTimer);
  ++routeLocationVersion;routeLocationHost = '';routeLocationState = 'unknown';
  editingRoute = null;
  templateRoute = null;
  copyingRoute = false;
  routeBaseline = '';
  $('route-form').reset();
  for (const id of ['route-token', 'route-ollama-token', 'route-ollama-secret', 'route-sub2api-token', 'route-sub2api-secret', 'route-toml', 'route-file']) $(id).value = '';
  $('route-form').hidden = true;
  $('route-editor-empty').hidden = false;
  $('route-delete-confirm').hidden = true;
  $('route-advanced').open = false;
}

async function loadRoutes(preferred) {
  const sequence = ++routesSequence;
  const service = 'shared';
  routesBusy = true;
  routeManagerControls();
  try {
    const data = await jsonRequest('/api/vps-nodes');
    if (sequence !== routesSequence) return false;
    routesData = {service, ...data, items: Array.isArray(data.items) ? data.items : []};
    nodeCache[service] = routesData;
    selectedRouteId = routesData.items.some(item => item.id === preferred) ? preferred
      : routesData.items.some(item => item.id === data.selected) ? data.selected : routesData.items[0]?.id || '';
    editSavedRoute('edit');
    renderHomeNodes(service);
    return true;
  } catch (error) {
    if (sequence === routesSequence) routesNote(error.message, true);
    return false;
  } finally {
    if (sequence === routesSequence) {
      routesBusy = false;
      routeManagerControls();
    }
  }
}

async function openRouteManager(service, preferred) {
  if ($('route-manager').open && !canLeaveRoute()) return;
  closeRouteEditor();
  routesData = {items: [], selected: ''};
  selectedRouteId = '';
  if (!$('route-manager').open) $('route-manager').showModal();
  routesNote('选择节点即可编辑');
  await loadRoutes(preferred);
}

function routeInputMode() {
  const importing = $('route-input-mode').value === 'import';
  $('route-manual-fields').hidden = importing;
  $('route-import-fields').hidden = !importing;
  $('route-server').required = !importing;
  $('route-port').required = !importing;
  const source = selectedSavedRoute();
  let missingTemplate = false;
  for (const service of ['ollama', 'sub2api']) {
    const inherited = ((editingRoute || copyingRoute) && source?.services?.[service]?.configured)
      || !!nodeCache.shared.templates?.[service]?.provider;
    $('route-' + service + '-provider').required = !importing && !inherited;
    $('route-' + service + '-secret').required = !importing && !inherited;
    missingTemplate ||= !inherited;
  }
  $('route-token').required = !importing && !nodeCache.shared.templates?.ollama?.provider && !nodeCache.shared.templates?.sub2api?.provider && !templateRoute && !editingRoute;
  $('route-toml').required = importing;
  if (importing || missingTemplate) $('route-advanced').open = true;
}

function editSavedRoute(mode) {
  const item = selectedSavedRoute();
  const source = item && item.editable !== false ? item : null;
  closeRouteEditor();
  if (mode === 'edit' && !source) {
    renderManagerNodes();
    if (!routesData.items.length) editSavedRoute('new');
    return;
  }
  editingRoute = mode === 'edit' ? source.id : null;
  templateRoute = mode === 'edit' ? null : source?.id || null;
  copyingRoute = mode === 'copy';
  $('route-form').hidden = false;
  $('route-editor-empty').hidden = true;
  $('route-editor-title').textContent = mode === 'edit' ? '编辑节点' : mode === 'copy' ? '复制节点' : '新增节点';
  $('route-item-detail').textContent = mode === 'edit' ? nodeStatus(source) : '未保存';
  $('route-name').value = mode === 'edit' ? source.name : mode === 'copy' ? `${source?.name || '节点'} 副本` : '';
  $('route-server').value = mode === 'new' ? '' : source?.server_addr || '';
  $('route-port').value = (mode === 'new' ? nodeCache.shared.default_server_port
    || nodeCache.shared.templates?.ollama?.server_port || nodeCache.shared.templates?.sub2api?.server_port : source?.server_port) || '';
  for (const service of ['ollama', 'sub2api']) {
    const inherited = (mode === 'new' ? null : source?.services?.[service]) || nodeCache.shared.templates?.[service];
    $('route-' + service + '-provider').value = inherited?.provider || nodeCache.shared.templates?.[service]?.provider || '';
    $('route-' + service + '-local').textContent = `本机 ${inherited?.local_port || nodeCache.shared.templates?.[service]?.local_port || (service === 'ollama' ? 11436 : 18081)}`;
  }
  routeLocationHost = $('route-server').value.trim();
  const location = mode === 'new' ? {} : source;
  locationFields(location);
  routeLocationState = location?.country || location?.city ? 'ok' : 'unknown';
  renderLocation();
  $('route-inherit-note').textContent = '节点共用 · 两条连接配套认证分别保留';
  $('route-check-result').textContent = mode === 'edit'
    ? networkText(currentNodeCheck('shared', source)) : '保存后可检测';
  routeInputMode();
  routeBaseline = routeSignature();
  renderManagerNodes();
  if (mode !== 'edit') $('route-name').focus();
  else if (!location?.country && !location?.city) locateEditor(false, true);
}

async function applyNode(service, item) {
  if (busy || !item || (item.active_services?.length > 1 && !item.pending)) return false;
  if (Number(state?.active_chats) > 0) {
    say('网页回答正在生成，结束后再切换节点。', true);return false;
  }
  const running = state?.ollama.running || state?.sub2api?.running;
  if (running && !window.confirm(`使用“${item.name}”会同时切换模型与 Sub2API 的 VPS；已开的连接会重连，已关闭的保持关闭。继续？`)) return false;
  setBusy(true);
  routeManagerControls();
  say(`正在使用 ${item.name}…`);
  let success = false;
  try {
    const data = await jsonRequest('/api/vps-nodes', {action: 'apply', id: item.id});
    if (!shuttingDown) say(data.message);
    success = true;
  } catch (error) {
    if (!shuttingDown) { say(error.message, true);if ($('route-manager').open) routesNote(error.message, true); }
  } finally {
    setBusy(false);
    await refresh();
    if ($('route-manager').open) await loadRoutes(item.id);
  }
  return success;
}

async function checkNode(service, item) {
  const key = `${service}:${item.id}`;
  if (busy || checkingNodes.has(key) || item.editable === false) return;
  const target = nodeEndpoint(item);
  const locationVersion = routeLocationVersion;
  checkingNodes.add(key);
  renderHomeNodes(service);
  routeManagerControls();
  if ($('route-manager').open) $('route-check-result').textContent = '检测中…';
  try {
    const result = await jsonRequest('/api/vps-nodes', {action: 'check', id: item.id});
    const current = nodeCache[service].items.find(value => value.id === item.id);
    if (current && nodeEndpoint(current) === target) {
      nodeChecks[service][item.id] = {target, result};
      if (result.location) {
        nodeLocations.set(current.server_addr, result.location);
        for (const collection of [nodeCache[service].items, routesData.service === service ? routesData.items : []]) {
          const saved = collection.find(value => value.id === item.id && nodeEndpoint(value) === target);
          if (!saved) continue;
          saved.location = result.location;
          if (result.location.state === 'ok') Object.assign(saved, {country: result.location.country, city: result.location.city});
        }
        if (locationVersion === routeLocationVersion && $('route-manager').open
            && $('route-server').value.trim() === current.server_addr) updateEditorLocation(result.location);
      }
      say(`${item.name} · ${networkText(result)}`);
    }
  } catch (error) {
    say(error.message, true);
    if ($('route-manager').open) routesNote(error.message, true);
  } finally {
    checkingNodes.delete(key);
    renderHomeNodes(service);
    if ($('route-manager').open) {
      renderManagerNodes();
      if (editingRoute === item.id) {
        const current = selectedSavedRoute();
        $('route-check-result').textContent = current ? networkText(currentNodeCheck(service, current)) : '未检测';
      }
    }
  }
}

async function copyNodeIP(item) {
  const value = item?.server_addr || $('route-server').value.trim();
  if (!value) return;
  let copied = false;
  try { if (navigator.clipboard) { await navigator.clipboard.writeText(value);copied = true; } } catch (_) {}
  if (!copied) {
    const input = document.createElement('textarea');
    input.value = value;input.className = 'clipboard-buffer';document.body.append(input);input.select();
    try { copied = document.execCommand('copy'); } finally { input.remove(); }
  }
  const message = copied ? '节点 IP 已复制' : '复制失败，请从节点 IP 输入框复制';
  if ($('route-manager').open) routesNote(message, !copied);else say(message, !copied);
}

async function mutateRoute(action, extra = {}, applyAfter = false) {
  if (routesBusy) return;
  const service = 'shared';
  const item = selectedSavedRoute();
  if (!['save', 'duplicate'].includes(action) && !item) return;
  const sequence = ++routesSequence;
  routesBusy = true;
  routeManagerControls();
  routesNote(action === 'copy' ? '正在复制…' : '正在保存…');
  try {
    const data = await jsonRequest('/api/vps-nodes', {action,
      ...(['save', 'duplicate'].includes(action) ? {} : {id: item.id}), ...extra});
    if (sequence !== routesSequence) return;
    if (action === 'copy') { routesNote('完整 visitor 已复制，仅私下传给自己的设备。');return; }
    if (data.saved_id) delete nodeChecks[service][data.saved_id];
    if (action === 'delete' && item) delete nodeChecks[service][item.id];
    routeBaseline = '';
    routesBusy = false;
    await refresh();
    if (!await loadRoutes(data.saved_id || item?.id)) return;
    if (!$('route-manager').open) return;
    routesNote(action === 'copy' ? '完整 visitor 已复制，仅私下传给自己的设备。'
      : data.pending ? '已保存。点“保存并使用”应用到当前连接。' : data.message || '已保存');
    if (applyAfter && data.saved_id) {
      const saved = routesData.items.find(value => value.id === data.saved_id);
      if (await applyNode(service, saved)) routesNote('节点已使用');
    }
  } catch (error) {
    if (sequence === routesSequence) routesNote(error.message, true);
  } finally {
    routesBusy = false;
    routeManagerControls();
  }
}

async function submitRoute(applyAfter) {
  if (routesBusy || routeSavePending) return;
  if (!$('route-form').reportValidity()) return;
  const version = routeLocationVersion;
  clearTimeout(routeLocationTimer);
  routeSavePending = true;routeManagerControls();
  try {
    if (['unknown', 'loading'].includes(routeLocationState)) await locateEditor(false, routeLocationState === 'loading');
  } finally {
    routeSavePending = false;routeManagerControls();
  }
  if (version !== routeLocationVersion || !$('route-manager').open) return;
  const payload = {name: $('route-name').value.trim()};
  if (editingRoute) payload.id = editingRoute;
  else if (copyingRoute && templateRoute) payload.id = templateRoute;
  payload.fields = {common: {country: $('route-country').value, city: $('route-city').value.trim()}, services: {}};
  if ($('route-input-mode').value === 'import') payload.visitor_toml = $('route-toml').value;
  else {
    Object.assign(payload.fields.common, {server_addr: $('route-server').value.trim(), server_port: Number($('route-port').value), token: $('route-token').value});
    for (const service of ['ollama', 'sub2api']) {
      payload.fields.services[service] = {token: $('route-' + service + '-token').value,
        secret_key: $('route-' + service + '-secret').value};
      const provider = $('route-' + service + '-provider').value.trim();
      if (provider) payload.fields.services[service].server_name = provider;
    }
  }
  mutateRoute(copyingRoute && templateRoute ? 'duplicate' : 'save', payload, applyAfter);
}

$('manage-vps-nodes').onclick = () => openRouteManager('shared');
$('routes-close').onclick = () => { if (canLeaveRoute()) $('route-manager').close(); };
$('route-manager').addEventListener('cancel', event => { if (!canLeaveRoute()) event.preventDefault(); });
$('route-manager').addEventListener('close', () => { ++routesSequence;routesBusy = false;closeRouteEditor(); });
$('routes-refresh').onclick = () => { if (canLeaveRoute()) loadRoutes(selectedRouteId); };
$('route-new').onclick = () => { if (canLeaveRoute()) editSavedRoute('new'); };
$('route-empty-new').onclick = () => editSavedRoute('new');
$('route-import').onclick = () => {
  if (!canLeaveRoute()) return;
  editSavedRoute('new');$('route-input-mode').value = 'import';routeInputMode();routeBaseline = routeSignature();
};
$('route-edit-cancel').onclick = () => editSavedRoute('edit');
$('route-input-mode').onchange = routeInputMode;
$('route-duplicate').onclick = () => { if (canLeaveRoute()) editSavedRoute('copy'); };
for (const service of ['ollama', 'sub2api']) $('route-copy-' + service).onclick = () => mutateRoute('copy', {service});
$('route-check').onclick = () => { const item = selectedSavedRoute();if (item) checkNode('shared', item); };
$('route-copy-ip').onclick = () => copyNodeIP({server_addr: $('route-server').value.trim()});
$('route-server').oninput = routeAddressChanged;
$('route-server').onblur = scheduleLocation;
$('route-server').onpaste = () => setTimeout(scheduleLocation, 0);
$('route-locate').onclick = () => locateEditor(true);
for (const id of ['route-country', 'route-city']) $(id).oninput = () => {
  ++routeLocationVersion;routeLocationState = 'manual';renderLocation();
};
$('route-port').oninput = routeManagerControls;
$('route-delete').onclick = () => {
  const item = selectedSavedRoute();
  if (!item || item.active || routesBusy) return;
  $('route-delete-question').textContent = `删除节点“${item.name}”？`;
  $('route-delete-confirm').hidden = false;
};
$('route-delete-no').onclick = () => { $('route-delete-confirm').hidden = true; };
$('route-delete-yes').onclick = () => mutateRoute('delete');
$('route-file').onchange = async () => {
  const file = $('route-file').files[0];
  if (!file) return;
  const sequence = routesSequence;
  if (file.size > 65536) { routesNote('文件过大，请选择 64 KB 以内的 TOML。', true);return; }
  try {
    const content = await file.text();
    if (sequence !== routesSequence || !$('route-manager').open || $('route-file').files[0] !== file) return;
    $('route-toml').value = content;
    routesNote('配置已读取，点击保存完成导入。');
  } catch (_) { routesNote('文件读取失败，请重新选择。', true); }
};
$('route-form').onsubmit = event => { event.preventDefault();submitRoute(false); };
$('route-save-use').onclick = () => submitRoute(true);

$('ollama-toggle').onclick = () => {
  if (state) act('ollama', state.ollama.running ? 'stop' : 'start');
};
$('refresh').onclick = refresh;
$('cherry').onclick = () => act('app', 'cherry');
$('updates').onclick = () => act('update', 'check');
$('logs').onclick = () => openInspector('logs', 'ollama');
$('ollama-diagnostics').onclick = () => openInspector('diagnostics', 'ollama');
$('sub2api-diagnostics').onclick = () => openInspector('diagnostics', 'sub2api');
$('console-diagnostics').onclick = () => openInspector('diagnostics', 'console');
$('sub2api-toggle').onclick = () => {
  if (state?.sub2api) act('sub2api', state.sub2api.running ? 'stop' : 'start');
};
$('sub2api-check').onclick = async () => {
  await openInspector('diagnostics', 'sub2api');
  inspectorNote('点击“1 · 检查健康”开始；打开窗口本身未发送远端请求。');
};
$('ccswitch').onclick = () => act('app', 'ccswitch');
$('inspector-service').onchange = loadInspector;
$('inspector-refresh').onclick = loadInspector;
$('inspector-close').onclick = () => $('inspector').close();
$('diagnostic-model').onchange = inspectorControls;
$('diagnostic-health').onclick = () => runDiagnostic('health');
$('diagnostic-auth').onclick = () => runDiagnostic('auth');
$('diagnostic-response').onclick = () => runDiagnostic('response');
async function quitAll() {
  if (shuttingDown) return;
  shuttingDown = true;
  ++inspectorSequence;
  $('inspector').close();
  $('route-manager').close();
  setBusy(true);
  say('正在停止全部连接…');
  try {
    const data = await jsonRequest('/api/shutdown', {});
    if (data.shutdown_complete && state) {
      state.ollama.running = false;
      state.ollama.checked = false;
      if (state.sub2api) Object.assign(state.sub2api, {running: false, listener: false,
        ownership: 'stopped', remote: {state: 'unchecked'}});
      renderState();
    }
    say(data.message);
  } catch (error) {
    shuttingDown = false;
    setBusy(false);
    say(error.message, true);
  }
}
$('quit').onclick = quitAll;
$('inspector-quit').onclick = quitAll;

(async () => {
  try {
    const response = await jsonRequest('/api/session');
    csrf = response.csrf;
    if (await refresh()) say('就绪');
  } catch (error) {
    say('无法连接本机控制台，请从应用入口重开。', true);
  }
})();

setInterval(() => {
  if (!document.hidden && !busy) refresh();
}, 20000);
