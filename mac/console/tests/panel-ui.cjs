// Offline, isolated browser behavior checks. The fixture never contacts a real model service.
const {chromium} = require(process.env.LOCAL_AI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const web = path.resolve(__dirname, '../app/web');
const fixed = {
  version: 'fixture', active_chats: 0,
  ollama: {running: true, managed: true, checked: false, ok: false, url: 'http://127.0.0.1:11435'},
  model_routes: [{id: 'route-a', name: '线路 A'}, {id: 'route-b', name: '线路 B'}],
  selected_model_route: 'route-a',
  sub2api: {configured: true, running: true, listener: true, ownership: 'owned', mode: 'stcp',
    url: 'http://127.0.0.1:18081/v1', remote: {state: 'ok', checked_at: new Date(Date.now() - 360000).toISOString(), ttl_seconds: 300}},
  routes: [{id: 'sub-a', name: '连接 A'}, {id: 'sub-b', name: '连接 B'}], selected_route: 'sub-a',
  apps: {ccswitch: true}
};
let status = structuredClone(fixed);
let models = ['actual-model-a', 'actual-model-b'];
let defaultModel = 'actual-model-b';
let modelsOffline = false;
let chatMode = 'complete';
let actionDelay = 0;
let diagnosticDelay = 0;
let authModels = [];
const activeChatIds = new Set();
let routeCounter = 0;
const routeItems = Object.fromEntries([['ollama', fixed.model_routes], ['sub2api', fixed.routes]].map(([service, values]) =>
  [service, values.map(value => ({...value, mode: 'stcp', editable: true, server_addr: 'fixture.invalid',
    server_port: 7000, server_name: service === 'sub2api' ? 'sub2api_fixture' : 'ollama_fixture', tls: true, encryption: true}))]));
const requests = [];
const pageErrors = [];

function diagnostics(service) {
  return {service, checked_at: new Date().toISOString(), model: 'actual-response-model',
    models: service === 'sub2api' ? authModels : [],
    layers: [{id: 'local', label: '本地连接', state: 'ok', detail: 'fixture local listener'},
      {id: 'health', label: '远端健康', state: 'unknown', detail: '尚未检查'}]};
}

const server = http.createServer(async (req, res) => {
  let body = '';
  for await (const part of req) body += part;
  let payload;
  try { payload = body ? JSON.parse(body) : null; } catch (_) { res.writeHead(400);res.end();return; }
  const url = new URL(req.url, 'http://fixture.invalid');
  requests.push({method: req.method, path: url.pathname, query: url.search, payload,
    csrf: req.headers['x-local-csrf']});
  function json(value, code = 200) {
    res.writeHead(code, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(value));
  }
  if (url.pathname === '/api/session') return json({csrf: 'fixture-csrf', service: 'local-ai-console'});
  if (url.pathname === '/api/status') return json(status);
  if (url.pathname === '/api/models') return modelsOffline
    ? json({error: 'fixture model list offline'}, 503) : json({models, default_model: defaultModel});
  if (url.pathname === '/api/preferences') {
    if (payload) {
      assert.ok(models.includes(payload.default_model));
      defaultModel = payload.default_model;
    }
    return json({default_model: defaultModel});
  }
  if (url.pathname === '/api/routes') {
    const service = payload?.service || url.searchParams.get('service');
    const selected = service === 'ollama' ? status.selected_model_route : status.selected_route;
    const items = routeItems[service];
    if (!payload) return json({service, selected, items: items.map(item => ({...item, active: item.id === selected}))});
    assert.equal(req.headers['x-local-csrf'], 'fixture-csrf');
    const item = items.find(value => value.id === payload.id);
    let savedId;
    let createdCopy = false;
    if (payload.action === 'delete') {
      if (payload.id === selected) return json({error: 'cannot delete active route'}, 409);
      routeItems[service] = items.filter(value => value.id !== payload.id);
    } else if (payload.action === 'copy') {
      assert.ok(item?.editable && item.mode === 'stcp');
      return json({message: 'fixture clipboard copied'});
    } else if (payload.action === 'save' || payload.action === 'duplicate') {
      createdCopy = payload.action === 'duplicate' || item?.id === selected;
      savedId = item && !createdCopy ? item.id : `saved-route-${++routeCounter}`;
      const fields = payload.fields || {};
      const saved = {id: savedId, name: payload.name || `${item.name} 副本`, mode: 'stcp', editable: true,
        server_addr: fields.server_addr || item?.server_addr || 'imported.fixture.invalid',
        server_port: fields.server_port || item?.server_port || 7001,
        server_name: fields.server_name || item?.server_name || 'imported_fixture', tls: true, encryption: true};
      if (item && !createdCopy) items.splice(items.indexOf(item), 1, saved); else items.push(saved);
    }
    status[service === 'ollama' ? 'model_routes' : 'routes'] = routeItems[service].map(({id, name}) => ({id, name}));
    return json({message: 'fixture 方案已保存', saved_id: savedId, created_copy: createdCopy});
  }
  if (url.pathname === '/api/diagnostics') {
    const service = payload?.service || url.searchParams.get('service');
    if (payload?.level === 'auth') authModels = ['actual-response-model', 'second-response-model'];
    if (payload && diagnosticDelay) await new Promise(resolve => setTimeout(resolve, diagnosticDelay));
    return json(diagnostics(service));
  }
  if (url.pathname === '/api/logs') return json({component: url.searchParams.get('component'),
    lines: [`${url.searchParams.get('component')} safe fixture event`]});
  if (url.pathname === '/api/action') {
    if (payload.service === 'model-route') status.selected_model_route = payload.action;
    if (payload.service === 'route') status.selected_route = payload.action;
    if (actionDelay) await new Promise(resolve => setTimeout(resolve, actionDelay));
    return json({message: '已应用连接'});
  }
  if (url.pathname === '/api/shutdown') return json({shutdown_complete: true, message: 'fixture 全部退出'});
  if (url.pathname === '/api/chat/cancel') {
    const cancelled = activeChatIds.delete(payload.request_id);
    return json({cancelled, active_chats: activeChatIds.size});
  }
  if (url.pathname === '/api/chat') {
    assert.match(payload.request_id, /^[\da-f]{8}-[\da-f-]{27}$/i);
    res.writeHead(200, {'Content-Type': 'application/x-ndjson'});
    res.flushHeaders();
    if (chatMode === 'broken') return res.end(JSON.stringify({message: {content: 'partial reply'}}) + '\n');
    if (chatMode === 'complete') return res.end(JSON.stringify({message: {content: 'fixture answer'}, done: true}) + '\n');
    activeChatIds.add(payload.request_id);
    const first = setTimeout(() => res.write(JSON.stringify({message: {content: 'streaming'}}) + '\n'), 20);
    const last = setTimeout(() => res.end(JSON.stringify({done: true}) + '\n'), 3000);
    res.on('close', () => { clearTimeout(first);clearTimeout(last);activeChatIds.delete(payload.request_id); });
    return;
  }
  const assets = {'/': 'index.html', '/chat': 'chat.html', '/console.js': 'console.js',
    '/chat.js': 'chat.js', '/style.css': 'style.css'};
  if (!assets[url.pathname]) { res.writeHead(404);res.end();return; }
  res.writeHead(200, {'Content-Type': url.pathname.endsWith('.js') ? 'text/javascript'
    : url.pathname.endsWith('.css') ? 'text/css' : 'text/html'});
  res.end(fs.readFileSync(path.join(web, assets[url.pathname])));
});

(async () => {
  let browser;
  const checks = [];
  const evidence = process.env.LOCAL_AI_EVIDENCE;
  if (evidence) fs.mkdirSync(evidence, {recursive: true});
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch(process.env.LOCAL_AI_BROWSER === 'chromium' ? {headless: true}
      : {channel: process.env.LOCAL_AI_BROWSER_CHANNEL || 'chrome', headless: true});
    const page = await browser.newPage({viewport: {width: 1000, height: 760}});
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(base)
      ? route.continue() : route.abort());
    await page.goto(base);
    await page.getByRole('status').filter({hasText: '就绪'}).waitFor();
    for (const width of [900, 720, 390]) {
      await page.setViewportSize({width, height: 700});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const dimensions = await page.evaluate(() => ({width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight}));
      assert.ok(dimensions.scrollWidth <= dimensions.width, `home overflow at ${width}`);
      if (width >= 720) assert.ok(dimensions.scrollHeight <= 700, `home too tall at ${width}: ${dimensions.scrollHeight}`);
      assert.equal(await page.locator('header').count(), 1);
      if (evidence) await page.screenshot({path: path.join(evidence, `ui-home-${width}.png`)});
    }
    await page.setViewportSize({width: 1000, height: 760});
    assert.equal(requests.filter(item => item.method === 'POST').length, 0);
    assert.equal(await page.locator('#change-route').isDisabled(), true);
    assert.equal(await page.locator('#model-route').isVisible(), true);
    assert.equal(await page.locator('#sub2api-route').isVisible(), true);
    assert.match(await page.locator('#model-route-hint').innerText(), /选择后点应用才生效/);
    assert.match(await page.locator('#sub2api-api-detail').innerText(), /已过期/);
    await page.locator('#model-route').selectOption('route-b');
    assert.match(await page.locator('#model-route-state').innerText(), /使用中：线路 A；待应用：线路 B/);
    assert.equal(await page.locator('#change-route').isEnabled(), true);
    await page.getByRole('button', {name: '↻ 刷新'}).click();
    await page.waitForFunction(() => document.querySelector('#model-route').value === 'route-b');
    status.active_chats = 1;
    await page.getByRole('button', {name: '↻ 刷新'}).click();
    await page.waitForFunction(() => document.querySelector('#ollama-guard').textContent.includes('1 个网页回答'));
    assert.equal(await page.locator('#change-route').isDisabled(), true);
    assert.equal(await page.locator('#ollama-toggle').isDisabled(), true);
    status.active_chats = 0;
    await page.getByRole('button', {name: '↻ 刷新'}).click();
    await page.waitForFunction(() => !document.querySelector('#change-route').disabled);
    await page.locator('#change-route').click();
    await page.waitForFunction(() => document.querySelector('#model-route-state').textContent === '使用中：线路 B');
    assert.equal(await page.locator('#change-route').isDisabled(), true);
    await page.locator('#sub2api-route').selectOption('sub-b');
    assert.match(await page.locator('#sub2api-route-state').innerText(), /待应用/);
    await page.locator('#change-sub2api-route').click();
    await page.waitForFunction(() => document.querySelector('#sub2api-route-state').textContent === '使用中：连接 B');
    const previousRoutes = {model: status.model_routes, sub2api: status.routes};
    status.model_routes = [{id: 'route-b', name: '当前 VPS'}];
    status.routes = [{id: 'sub-b', name: '唯一 Sub2API'}];
    await page.getByRole('button', {name: '↻ 刷新'}).click();
    await page.waitForFunction(() => document.querySelector('#model-route').hidden);
    for (const id of ['model-route', 'sub2api-route', 'change-route', 'change-sub2api-route']) {
      assert.equal(await page.locator(`#${id}`).isVisible(), false);
    }
    assert.equal(await page.locator('#model-route-name').innerText(), '使用中：当前 VPS');
    assert.equal(await page.locator('#sub2api-route-name').innerText(), '使用中：唯一 Sub2API');
    assert.equal(await page.locator('#manage-model-routes').isVisible(), true);
    assert.equal(await page.locator('#manage-sub2api-routes').isVisible(), true);
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-home-single-route.png')});
    status.model_routes = previousRoutes.model;
    status.routes = previousRoutes.sub2api;
    await page.getByRole('button', {name: '↻ 刷新'}).click();
    await page.waitForFunction(() => !document.querySelector('#model-route').hidden);
    assert.ok(requests.filter(item => item.path === '/api/status').every(item => item.query === '?passive=1'));
    checks.push('saved route selector intent, single route static display, pending and current routes, generation guard');

    const appliesBeforeManagement = requests.filter(item => item.path === '/api/action').length;
    await page.locator('#manage-model-routes').click();
    await page.waitForFunction(() => document.querySelector('#routes-list').options.length === 2);
    assert.equal(await page.locator('#route-delete').isDisabled(), true);
    await page.locator('#route-edit').click();
    assert.equal(await page.locator('#route-token').inputValue(), '');
    assert.equal(await page.locator('#route-secret').inputValue(), '');
    assert.match(await page.locator('#route-editor-title').innerText(), /另存副本/);
    await page.locator('#route-name').fill('Active route edited copy');
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: '尚未应用或验证'}).waitFor();
    const activeCopy = requests.filter(item => item.path === '/api/routes' && item.payload?.action === 'save').at(-1);
    assert.equal(activeCopy.payload.id, 'route-b');
    assert.equal(activeCopy.payload.fields.token, '');
    assert.equal(status.selected_model_route, 'route-b');
    assert.match(await page.locator('#model-route-state').textContent(), /待应用：Active route edited copy/);
    await page.locator('#route-new').click();
    await page.locator('#route-name').fill('New VPS');
    await page.locator('#route-server').fill('another.fixture.invalid');
    await page.locator('#route-port').fill('7002');
    await page.locator('#route-provider').fill('fixture_provider');
    await page.locator('#route-token').fill('fixture-token-only');
    await page.locator('#route-secret').fill('fixture-secret-only');
    assert.equal(await page.locator('#route-token').getAttribute('type'), 'password');
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-routes-editor.png')});
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: '尚未应用或验证'}).waitFor();
    const newRouteId = await page.locator('#routes-list').inputValue();
    assert.equal(await page.locator('#route-token').inputValue(), '');
    assert.equal(await page.locator('#route-secret').inputValue(), '');
    assert.equal((await page.locator('#route-manager').innerText()).includes('fixture-token-only'), false);
    await page.locator('#route-copy').click();
    await page.getByRole('status').filter({hasText: '含隧道凭据，仅传给自己的设备'}).waitFor();
    await page.locator('#route-duplicate').click();
    await page.getByRole('status').filter({hasText: '尚未应用或验证'}).waitFor();
    assert.notEqual(await page.locator('#routes-list').inputValue(), newRouteId);
    const deleteId = await page.locator('#routes-list').inputValue();
    await page.locator('#route-delete').click();
    assert.equal(requests.some(item => item.payload?.action === 'delete'), false);
    await page.locator('#route-delete-yes').click();
    await page.waitForFunction(id => !Array.from(document.querySelector('#routes-list').options).some(option => option.value === id), deleteId);
    await page.locator('#route-new').click();
    await page.locator('#route-name').fill('Imported Windows visitor');
    await page.locator('#route-input-mode').selectOption('import');
    const importedTOML = 'serverAddr = "fixture.invalid"\n# fixture visitor only\n';
    await page.locator('#route-file').setInputFiles({name: 'fixture-visitor.toml', mimeType: 'text/plain', buffer: Buffer.from(importedTOML)});
    await page.waitForFunction(() => document.querySelector('#route-toml').value.includes('fixture visitor only'));
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: '尚未应用或验证'}).waitFor();
    const importedCall = requests.filter(item => item.path === '/api/routes' && item.payload?.visitor_toml).at(-1);
    assert.equal(importedCall.payload.visitor_toml, importedTOML);
    assert.equal(await page.locator('#route-toml').inputValue(), '');
    await page.locator('#routes-service').selectOption('sub2api');
    await page.waitForFunction(() => document.querySelector('#routes-list').value === 'sub-b');
    await page.locator('#route-new').click();
    assert.match(await page.locator('#route-fixed-settings').innerText(), /127\.0\.0\.1:18081/);
    await page.locator('#route-edit-cancel').click();
    routeItems.sub2api[0].mode = 'direct';
    routeItems.sub2api[0].editable = false;
    await page.locator('#routes-refresh').click();
    await page.waitForFunction(() => !document.querySelector('#routes-refresh').disabled);
    await page.locator('#routes-list').selectOption('sub-a');
    for (const id of ['route-edit', 'route-duplicate', 'route-copy']) assert.equal(await page.locator(`#${id}`).isDisabled(), true);
    assert.equal(await page.locator('#route-delete').isEnabled(), true);
    await page.setViewportSize({width: 390, height: 700});
    assert.equal(await page.locator('#route-manager').evaluate(node => node.scrollWidth <= node.clientWidth), true);
    await page.setViewportSize({width: 1000, height: 760});
    await page.locator('#routes-close').click();
    assert.equal(requests.filter(item => item.path === '/api/action').length, appliesBeforeManagement);
    checks.push('local route management add/edit/copy/duplicate/delete/import, active deletion guard, credentials cleared, never auto-applied');

    await page.locator('.management>summary').click();
    actionDelay = 300;
    await page.locator('#cherry').click();
    await page.waitForFunction(() => document.querySelector('#cherry').disabled);
    assert.equal(await page.locator('#quit').isEnabled(), true);
    await page.locator('#quit').click();
    await page.getByRole('status').filter({hasText: 'fixture 全部退出'}).waitFor();
    actionDelay = 0;
    await page.reload();
    await page.getByRole('status').filter({hasText: '就绪'}).waitFor();

    await page.locator('#sub2api-diagnostics').click();
    await page.getByText('fixture local listener', {exact: true}).waitFor();
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-diagnostics.png')});
    await page.setViewportSize({width: 390, height: 700});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.setViewportSize({width: 1000, height: 760});
    assert.equal(requests.filter(item => item.path === '/api/diagnostics' && item.method === 'POST').length, 0);
    assert.equal(await page.locator('#diagnostic-response').isDisabled(), true);
    await page.locator('#diagnostic-auth').click();
    await page.waitForFunction(() => !document.querySelector('#diagnostic-response').disabled);
    await page.locator('#diagnostic-model').selectOption('second-response-model');
    await page.locator('#diagnostic-response').click();
    await page.getByRole('status').filter({hasText: '检查已结束'}).waitFor();
    const responseCall = requests.find(item => item.path === '/api/diagnostics' && item.payload?.level === 'response');
    assert.equal(responseCall.payload.model, 'second-response-model');
    await page.locator('#inspector-close').click();
    await page.locator('.management>summary').click();
    await page.locator('#logs').click();
    await page.getByText('ollama safe fixture event', {exact: true}).waitFor();
    await page.locator('#inspector-service').selectOption('sub2api');
    await page.getByText('sub2api safe fixture event', {exact: true}).waitFor();
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-logs.png')});
    await page.locator('#inspector-close').click();
    checks.push('diagnostics read-only open, explicit exact model request, categorized in-place logs');

    await page.locator('#sub2api-diagnostics').click();
    await page.getByRole('status').filter({hasText: '记录已读取'}).waitFor();
    diagnosticDelay = 300;
    await page.locator('#diagnostic-auth').click();
    await page.waitForFunction(() => document.querySelector('#diagnostic-auth').disabled);
    assert.equal(await page.locator('#inspector-quit').isEnabled(), true);
    await page.locator('#inspector-quit').click();
    await page.getByRole('status').filter({hasText: 'fixture 全部退出'}).waitFor();
    assert.equal(await page.locator('#inspector').evaluate(node => node.open), false);
    diagnosticDelay = 0;
    checks.push('homepage status is explicitly passive; quit remains usable during actions and modal diagnostics');

    await page.goto(base + '/chat');
    await page.waitForFunction(() => document.querySelector('#model').value === 'actual-model-b');
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-chat.png')});
    await page.setViewportSize({width: 390, height: 700});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.locator('header').count(), 1);
    if (evidence) await page.screenshot({path: path.join(evidence, 'ui-chat-390.png')});
    await page.setViewportSize({width: 1000, height: 760});
    await page.locator('#input').fill('draft remains');
    await page.locator('#model').selectOption('actual-model-a');
    assert.equal(await page.locator('#input').inputValue(), 'draft remains');
    models = ['actual-model-b', 'actual-model-a'];
    await page.locator('#reload').click();
    await page.getByRole('status').filter({hasText: '模型列表已刷新'}).waitFor();
    assert.equal(await page.locator('#model').inputValue(), 'actual-model-a');
    await page.locator('#remember-model').click();
    await page.getByRole('status').filter({hasText: '已记住'}).waitFor();
    assert.equal(defaultModel, 'actual-model-a');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#model').value === 'actual-model-a');
    modelsOffline = true;
    await page.locator('#input').fill('offline draft');
    await page.locator('#reload').click();
    await page.getByRole('status').filter({hasText: 'fixture model list offline'}).waitFor();
    assert.match(await page.locator('#model-status').innerText(), /已过期/);
    assert.equal(await page.locator('#send').isDisabled(), true);
    assert.equal(await page.locator('#input').inputValue(), 'offline draft');
    modelsOffline = false;
    await page.locator('#reload').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    checks.push('actual default model persisted, current selection preserved, offline list blocks send and preserves draft');

    chatMode = 'delayed';
    const before = requests.filter(item => item.path === '/api/chat').length;
    await page.locator('#send').click();
    await page.waitForFunction(() => !document.querySelector('#stop').disabled);
    await page.locator('#input').fill('next draft while streaming');
    activeChatIds.add('unrelated-fixture-request');
    await page.locator('#stop').click();
    await page.getByRole('status').filter({hasText: '已停止'}).waitFor();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    assert.equal(await page.locator('#input').inputValue(), 'next draft while streaming');
    assert.equal(requests.filter(item => item.path === '/api/chat').length, before + 1);
    const stoppedRequest = requests.filter(item => item.path === '/api/chat').at(-1);
    const cancelCalls = requests.filter(item => item.path === '/api/chat/cancel');
    assert.equal(cancelCalls.length, 1);
    assert.equal(cancelCalls[0].payload.request_id, stoppedRequest.payload.request_id);
    assert.equal(cancelCalls[0].csrf, 'fixture-csrf');
    assert.equal(activeChatIds.has('unrelated-fixture-request'), true);
    chatMode = 'broken';
    await page.locator('#send').click();
    await page.getByRole('status').filter({hasText: '回答未完整结束'}).waitFor();
    assert.equal(await page.locator('#send').isDisabled(), true);
    assert.equal(await page.locator('#input').inputValue(), 'next draft while streaming');
    assert.equal(requests.filter(item => item.path === '/api/chat').length, before + 2);
    checks.push('stop cancels only its exact request once, preserves next draft, incomplete stream blocks resend, no automatic replay');

    chatMode = 'complete';
    await page.locator('#reload').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    await page.locator('#send').click();
    await page.getByRole('status').filter({hasText: '回答完成'}).waitFor();
    await page.locator('#input').fill('draft after model removal');
    models = ['actual-model-b'];
    await page.locator('#reload').click();
    await page.getByRole('status').filter({hasText: '原模型已不在列表'}).waitFor();
    assert.equal(await page.locator('#model').inputValue(), 'actual-model-b');
    assert.equal(await page.locator('#input').inputValue(), 'draft after model removal');
    assert.equal(await page.locator('#messages .bubble').count(), 0);
    await page.locator('#send').click();
    await page.getByRole('status').filter({hasText: '回答完成'}).waitFor();
    const switchedRequest = requests.filter(item => item.path === '/api/chat').at(-1);
    assert.equal(switchedRequest.payload.model, 'actual-model-b');
    assert.deepEqual(switchedRequest.payload.messages, [{role: 'user', content: 'draft after model removal'}]);
    checks.push('removed selected model falls back to an actual model, clears old history and preserves draft');
    modelsOffline = true;
    await page.reload();
    await page.getByRole('status').filter({hasText: 'fixture model list offline'}).waitFor();
    assert.equal(await page.locator('#model').innerText(), '尚未取得模型');
    assert.equal(await page.locator('#send').isDisabled(), true);
    checks.push('initial offline model list explicitly ends loading state');
    assert.deepEqual(pageErrors, []);
    console.log(JSON.stringify({offline: true, checks, page_errors: pageErrors}, null, 2));
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections?.();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack || error.message);process.exitCode = 1; });
