let csrf = '';
let state = null;
let busy = false;

const $ = id => document.getElementById(id);

function say(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}

async function post(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Local-CSRF': csrf},
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '操作失败');
  return data;
}

function renderRoutes(id, routes, selected, fallback) {
  const select = $(id);
  // Periodic status refreshes must not interrupt a route the user is selecting.
  if (document.activeElement === select) return;
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

function renderSub2API() {
  const service = state.sub2api;
  $('sub2api-card').hidden = !service;
  if (!service) return;

  const external = service.ownership === 'external';
  const direct = service.mode === 'direct';
  const toggle = $('sub2api-toggle');
  toggle.setAttribute('aria-checked', String(!!service.running));
  toggle.disabled = busy || !service.configured || direct || external;
  $('sub2api-url').textContent = service.url || '未配置 API 地址';

  let localState = '本地连接已关闭';
  let localDetail = '开启连接后，再按需检查 Windows API。';
  let localClass = 'off';
  if (!service.configured) {
    localState = 'Sub2API 尚未配置';
    localDetail = '请先在本机配置连接资料。';
  } else if (direct) {
    localState = '使用直连地址';
    localDetail = '此连接无需启动本地 frp。';
  } else if (external) {
    localState = '端口由外部连接占用';
    localDetail = '控制台保留原连接，不接管或停止外部 visitor。';
    localClass = 'warn';
  } else if (service.running) {
    localState = service.listener ? '本地连接已开启' : '本地连接正在启动';
    localDetail = service.listener
      ? '本地 visitor 已监听，远端 API 状态单独检查。'
      : '连接进程已启动，正在等待本地监听。';
    localClass = service.listener ? 'ok' : 'warn';
  }
  $('sub2api-state').textContent = localState;
  $('sub2api-state').className = localClass;
  $('sub2api-detail').textContent = localDetail;

  const remote = service.remote || {state: 'unchecked'};
  const apiState = $('sub2api-api-state');
  apiState.textContent = remote.state === 'ok' ? '上次检查通过'
    : remote.state === 'error' ? '上次检查未通过' : '未检查';
  apiState.className = remote.state === 'ok' ? 'ok'
    : remote.state === 'error' ? 'warn' : 'off';
  $('sub2api-api-detail').textContent = remote.state === 'unchecked'
    ? '点击“检查 API”后才会发送健康检查；不会自动检查或请求模型。'
    : `${remote.detail || '已完成健康检查'}${Number.isFinite(remote.latency_ms) ? ` · ${remote.latency_ms} ms` : ''}。模型回复仍需另行验收。`;
  $('sub2api-check').disabled = busy || !service.configured || (!direct && !service.listener);

  renderRoutes('sub2api-route', state.routes, state.selected_route, '当前连接');
  $('sub2api-route').disabled = busy || external || !state.routes?.length;
  $('change-sub2api-route').disabled = busy || external || !state.routes?.length
    || !$('sub2api-route').value || $('sub2api-route').value === state.selected_route;
  $('ccswitch').disabled = busy || !state.apps?.ccswitch;
}

function renderState() {
  if (!state) return;
  const ollama = state.ollama;
  const unchecked = ollama.checked === false;
  $('version').textContent = state.version;
  $('ollama-toggle').setAttribute('aria-checked', String(!!ollama.running));
  $('ollama-toggle').disabled = busy || !ollama.managed;
  $('ollama-url').textContent = ollama.url || '未配置';
  $('ollama-state').textContent = unchecked
    ? ollama.running ? '连接已开 · API 未检查' : '接收连接已关闭'
    : ollama.ok ? 'Windows 模型已连通'
      : ollama.running ? '连接已开 · 远端不可达' : '接收连接已关闭';
  $('ollama-state').className = unchecked ? 'off'
    : ollama.ok ? 'ok' : ollama.running ? 'warn' : 'off';
  $('ollama-detail').textContent = unchecked ? '仅显示本地连接状态，尚未检查远端 API。'
    : ollama.ok ? `API 响应 ${ollama.latency_ms} ms`
      : `${ollama.detail} · 请检查 Windows 模型与同名线路`;
  renderRoutes('model-route', state.model_routes, state.selected_model_route, '当前已配置线路');
  $('model-route').disabled = busy || !state.model_routes?.length;
  $('change-route').disabled = busy || !ollama.managed || !state.model_routes?.length;
  renderSub2API();
}

async function refresh() {
  if (busy) return false;
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw Error('状态暂不可用');
    state = await response.json();
    renderState();
    return true;
  } catch (error) {
    say(error.message, true);
    return false;
  }
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll('button, select').forEach(element => {
    element.disabled = value;
  });
  if (!value) renderState();
}

async function act(service, action) {
  if (busy) return;
  setBusy(true);
  say('正在处理…');
  try {
    const data = await post('/api/action', {service, action});
    say(data.message);
  } catch (error) {
    say(error.message, true);
  } finally {
    setBusy(false);
    await refresh();
  }
}

$('ollama-toggle').onclick = () => {
  if (state) act('ollama', state.ollama.running ? 'stop' : 'start');
};
$('refresh').onclick = refresh;
$('cherry').onclick = () => act('app', 'cherry');
$('change-route').onclick = () => act('model-route', $('model-route').value);
$('updates').onclick = () => act('update', 'check');
$('logs').onclick = () => act('ollama', 'logs');
$('sub2api-toggle').onclick = () => {
  if (state?.sub2api) act('sub2api', state.sub2api.running ? 'stop' : 'start');
};
$('sub2api-check').onclick = () => act('sub2api', 'check');
$('ccswitch').onclick = () => act('app', 'ccswitch');
$('sub2api-route').onchange = () => {
  const service = state?.sub2api;
  $('change-sub2api-route').disabled = busy || !service || service.ownership === 'external'
    || !$('sub2api-route').value || $('sub2api-route').value === state.selected_route;
};
$('change-sub2api-route').onclick = () => act('route', $('sub2api-route').value);
$('quit').onclick = async () => {
  if (busy) return;
  setBusy(true);
  try {
    const data = await post('/api/shutdown', {});
    say(data.message);
  } catch (error) {
    setBusy(false);
    say(error.message, true);
  }
};

(async () => {
  try {
    const response = await fetch('/api/session');
    if (!response.ok) throw Error('控制台会话不可用');
    csrf = (await response.json()).csrf;
    if (await refresh()) say('就绪');
  } catch (error) {
    say('无法连接本机控制台，请从应用入口重开。', true);
  }
})();

setInterval(() => {
  if (!document.hidden && !busy) refresh();
}, 20000);
