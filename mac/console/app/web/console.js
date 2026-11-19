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

const $ = id => document.getElementById(id);

function say(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}

async function jsonRequest(path, payload) {
  const options = payload === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Local-CSRF': csrf},
    body: JSON.stringify(payload)
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '操作失败');
  return data;
}

function routeName(routes, id, fallback) {
  return (routes || []).find(route => route.id === id)?.name || fallback;
}

function routeSelection(id, routes, selected, fallback) {
  const select = $(id);
  const multiple = routes?.length > 1;
  const name = $(`${id}-name`);
  select.hidden = !multiple;
  $(id === 'model-route' ? 'change-route' : 'change-sub2api-route').hidden = !multiple;
  name.hidden = multiple;
  name.textContent = `使用中：${routeName(routes, selected, '未配置')}`;
  $(`${id}-state`).hidden = !multiple;
  $(`${id}-hint`).hidden = !multiple;
  if (multiple && document.activeElement === select) return;
  const pending = select.dataset.appliedRoute === (selected || '') ? select.value : '';
  select.replaceChildren();
  for (const route of routes || []) {
    const option = document.createElement('option');
    option.value = route.id;
    option.textContent = route.name;
    select.append(option);
  }
  if (!select.options.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = fallback;
    select.append(option);
  }
  const values = Array.from(select.options, option => option.value);
  select.value = pending && values.includes(pending) ? pending : selected || '';
  select.dataset.appliedRoute = selected || '';
}

function renderRouteState(selectId, outputId, routes, current) {
  const selected = $(selectId).value;
  const currentName = routeName(routes, current, '未配置');
  const pending = !!selected && selected !== current;
  $(outputId).textContent = pending
    ? `使用中：${currentName}；待应用：${routeName(routes, selected, selected)}`
    : `使用中：${currentName}`;
  $(outputId).classList.toggle('pending', pending);
  return pending;
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
  const ollamaPending = renderRouteState('model-route', 'model-route-state', state.model_routes,
    state.selected_model_route, '线路');
  $('change-route').disabled = busy || active || !state.ollama.managed || !ollamaPending;
  $('model-route').disabled = busy || !state.model_routes?.length;
  $('ollama-guard').textContent = active
    ? `当前有 ${state.active_chats} 个网页回答进行中，暂不能关闭连接或切换线路。外部客户端状态无法确认。`
    : '与 Windows 选择同一 VPS，请在回答结束后切换；外部客户端的生成状态无法确认。';
  if (state.sub2api) {
    const pending = renderRouteState('sub2api-route', 'sub2api-route-state', state.routes,
      state.selected_route, '连接');
    const external = state.sub2api.ownership === 'external';
    $('sub2api-route').disabled = busy || external || !state.routes?.length;
    $('change-sub2api-route').disabled = busy || external || !pending;
  }
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
  routeSelection('sub2api-route', state.routes, state.selected_route, '当前连接');
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
  routeSelection('model-route', state.model_routes, state.selected_model_route, '当前已配置线路');
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
  $('routes-quit').disabled = shuttingDown;
  if (!busy) renderState();
}

async function act(service, action) {
  if (busy) return;
  if (service === 'ollama' && action === 'stop' && Number(state?.active_chats) > 0) {
    say('当前网页正在生成回答，暂不能关闭模型连接。', true);
    return;
  }
  if (service === 'model-route' && (Number(state?.active_chats) > 0 || action === state?.selected_model_route)) return;
  if (service === 'route' && action === state?.selected_route) return;
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
  return routesData.items.find(item => item.id === $('routes-list').value);
}

function routeManagerControls() {
  const item = selectedSavedRoute();
  const editable = item?.mode === 'stcp' && item.editable;
  $('routes-service').disabled = routesBusy;
  $('routes-refresh').disabled = routesBusy;
  $('routes-list').disabled = routesBusy || !routesData.items.length;
  $('route-new').disabled = routesBusy;
  for (const id of ['route-edit', 'route-duplicate', 'route-copy']) $(id).disabled = routesBusy || !editable;
  $('route-delete').disabled = routesBusy || !item || item.active;
  $('route-delete-yes').disabled = routesBusy;
  $('route-save').disabled = routesBusy;
  $('route-edit-cancel').disabled = routesBusy;
  $('route-form').querySelectorAll('input, select, textarea').forEach(field => { field.disabled = routesBusy; });
  $('routes-quit').disabled = shuttingDown;
}

function showSavedRoute() {
  const item = selectedSavedRoute();
  const security = item?.mode === 'stcp' && item.editable
    ? ` · TLS ${item.tls === true ? '已开启' : '默认（未声明）'} · 加密 ${item.encryption === true ? '已开启' : '默认（未声明）'}` : '';
  $('route-delete-confirm').hidden = true;
  $('route-item-detail').textContent = item
    ? `${item.active ? '使用中' : '备选'} · ${item.mode === 'stcp' ? 'STCP' : '直连'}${item.server_addr ? ` · ${item.server_addr}:${item.server_port}` : ''}${item.server_name ? ` · ${item.server_name}` : ''}${security}${item.mode !== 'stcp' || !item.editable ? ' · 此方案仅可选择应用，无法编辑或导出' : ''}`
    : '暂无已保存方案。';
  routeManagerControls();
}

function closeRouteEditor() {
  editingRoute = null;
  $('route-form').reset();
  $('route-token').value = '';
  $('route-secret').value = '';
  $('route-toml').value = '';
  $('route-file').value = '';
  $('route-form').hidden = true;
  $('route-editor-empty').hidden = false;
  $('route-delete-confirm').hidden = true;
}

async function loadRoutes(preferred) {
  const sequence = ++routesSequence;
  const service = $('routes-service').value;
  routesBusy = true;
  routeManagerControls();
  try {
    const data = await jsonRequest(`/api/routes?service=${encodeURIComponent(service)}`);
    if (sequence !== routesSequence) return false;
    routesData = {items: Array.isArray(data.items) ? data.items : [], selected: data.selected};
    $('routes-list').replaceChildren();
    for (const item of routesData.items) {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = `${item.active ? '使用中 · ' : ''}${item.name}`;
      $('routes-list').append(option);
    }
    $('routes-list').value = routesData.items.some(item => item.id === preferred) ? preferred
      : routesData.items.some(item => item.id === data.selected) ? data.selected : routesData.items[0]?.id || '';
    showSavedRoute();
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

async function openRouteManager(service) {
  closeRouteEditor();
  $('routes-service').value = service;
  routesData = {items: [], selected: ''};
  $('route-manager').showModal();
  routesNote('仅管理本机方案，不自动测试或应用。');
  await loadRoutes();
}

function routeInputMode() {
  const importing = $('route-input-mode').value === 'import';
  $('route-manual-fields').hidden = importing;
  $('route-import-fields').hidden = !importing;
  for (const id of ['route-server', 'route-port', 'route-provider']) $(id).required = !importing;
  $('route-token').required = !importing && !editingRoute;
  $('route-secret').required = !importing && !editingRoute;
  $('route-toml').required = importing;
}

function editSavedRoute(createNew) {
  const item = createNew ? null : selectedSavedRoute();
  if (!createNew && (!item?.editable || item.mode !== 'stcp')) return;
  closeRouteEditor();
  editingRoute = item?.id || null;
  $('route-form').hidden = false;
  $('route-editor-empty').hidden = true;
  $('route-editor-title').textContent = item ? item.active ? '编辑使用中方案 · 另存副本' : '编辑线路' : '新增 VPS 线路';
  $('route-name').value = item?.name || '';
  $('route-server').value = item?.server_addr || '';
  $('route-port').value = item?.server_port || '';
  $('route-provider').value = item?.server_name || '';
  const sub2api = $('routes-service').value === 'sub2api';
  $('route-fixed-settings').textContent = `STCP · TLS 与传输加密保持开启 · visitor 固定监听 127.0.0.1:${sub2api ? '18081' : '11436（模型 API 11435 不变）'}`;
  routeInputMode();
  routeManagerControls();
  $('route-name').focus();
}

async function mutateRoute(action, extra = {}) {
  if (routesBusy) return;
  const service = $('routes-service').value;
  const item = selectedSavedRoute();
  if (action !== 'save' && !item) return;
  const sequence = ++routesSequence;
  routesBusy = true;
  routeManagerControls();
  routesNote('正在保存本机操作…');
  try {
    const data = await jsonRequest('/api/routes', {service, action,
      ...(action === 'save' ? {} : {id: item.id}), ...extra});
    // The saved route only becomes a pending selection; this never starts or applies it.
    await refresh();
    if (data.saved_id && state) {
      const select = $(service === 'ollama' ? 'model-route' : 'sub2api-route');
      if (Array.from(select.options).some(option => option.value === data.saved_id)) {
        select.value = data.saved_id;
        renderRouteControls();
      }
    }
    if (sequence !== routesSequence) return;
    closeRouteEditor();
    routesBusy = false;
    if (!await loadRoutes(data.saved_id || item?.id)) return;
    if (!$('route-manager').open || $('routes-service').value !== service) return;
    routesNote(action === 'copy' ? 'visitor 配置已复制到本机剪贴板，含隧道凭据，仅传给自己的设备。'
      : data.saved_id ? `${data.message || '方案已保存'}。尚未应用或验证；关闭本窗后点击“应用${service === 'ollama' ? '线路' : '连接'}”。`
        : data.message || '操作已完成。');
  } catch (error) {
    if (sequence === routesSequence) routesNote(error.message, true);
  } finally {
    if (sequence === routesSequence) {
      routesBusy = false;
      routeManagerControls();
    }
  }
}

$('manage-model-routes').onclick = event => { event.preventDefault();openRouteManager('ollama'); };
$('manage-sub2api-routes').onclick = event => { event.preventDefault();openRouteManager('sub2api'); };
$('routes-close').onclick = () => $('route-manager').close();
$('route-manager').addEventListener('close', () => {
  ++routesSequence;
  routesBusy = false;
  closeRouteEditor();
});
$('routes-service').onchange = () => { closeRouteEditor();loadRoutes(); };
$('routes-refresh').onclick = () => { closeRouteEditor();loadRoutes($('routes-list').value); };
$('routes-list').onchange = () => { closeRouteEditor();showSavedRoute(); };
$('route-new').onclick = () => editSavedRoute(true);
$('route-edit').onclick = () => editSavedRoute(false);
$('route-edit-cancel').onclick = closeRouteEditor;
$('route-input-mode').onchange = routeInputMode;
$('route-duplicate').onclick = () => mutateRoute('duplicate');
$('route-copy').onclick = () => mutateRoute('copy');
$('route-delete').onclick = () => {
  const item = selectedSavedRoute();
  if (!item || item.active || routesBusy) return;
  $('route-delete-question').textContent = `删除本机方案“${item.name}”？`;
  $('route-delete-confirm').hidden = false;
};
$('route-delete-no').onclick = () => { $('route-delete-confirm').hidden = true; };
$('route-delete-yes').onclick = () => mutateRoute('delete');
$('route-file').onchange = async () => {
  const file = $('route-file').files[0];
  if (!file) return;
  const sequence = routesSequence;
  if (file.size > 65536) { routesNote('文件过大，请选择 visitor TOML（最多 64 KB）。', true);return; }
  try {
    const content = await file.text();
    if (sequence !== routesSequence || !$('route-manager').open || $('route-file').files[0] !== file) return;
    $('route-toml').value = content;
    routesNote('已在本机读取文件；保存前不会发送配置。');
  } catch (_) { routesNote('无法读取文件，请重新选择。', true); }
};
$('route-form').onsubmit = event => {
  event.preventDefault();
  const payload = {name: $('route-name').value.trim()};
  if (editingRoute) payload.id = editingRoute;
  if ($('route-input-mode').value === 'import') payload.visitor_toml = $('route-toml').value;
  else payload.fields = {server_addr: $('route-server').value.trim(), server_port: Number($('route-port').value),
    server_name: $('route-provider').value.trim(), token: $('route-token').value, secret_key: $('route-secret').value};
  mutateRoute('save', payload);
};

$('ollama-toggle').onclick = () => {
  if (state) act('ollama', state.ollama.running ? 'stop' : 'start');
};
$('refresh').onclick = refresh;
$('cherry').onclick = () => act('app', 'cherry');
$('change-route').onclick = () => act('model-route', $('model-route').value);
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
$('model-route').onchange = renderRouteControls;
$('sub2api-route').onchange = renderRouteControls;
$('change-sub2api-route').onclick = () => act('route', $('sub2api-route').value);
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
$('routes-quit').onclick = quitAll;

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
