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
let networkDelay = 0;
const locationDelays = new Map();
let pingState = 'ok';
let authModels = [];
const activeChatIds = new Set();
let routeCounter = 0;
let sharedItems = [
  {id: 'vps-a', name: '节点 A', server_addr: 'fixture-a.invalid', server_port: 7000, country: 'JP', city: '东京'},
  {id: 'vps-b', name: '节点 B', server_addr: 'fixture-b.invalid', server_port: 7000, country: 'SG', city: '新加坡'}
].map(item => ({...item, pending: false, services: {
  ollama: {configured: true, provider: 'ollama_fixture', local_port: 11436},
  sub2api: {configured: true, provider: 'sub2api_fixture', local_port: 18081}}}));
let currentNodes = {ollama: 'vps-a', sub2api: 'vps-b'};
let sharedTemplates = {ollama: {provider: 'actual-current-model-provider', local_port: 11436},
  sub2api: {provider: 'actual-current-api-provider', local_port: 18081}};
function sharedSnapshot() {
  return {items: sharedItems.map(item => ({...item, active: Object.values(currentNodes).includes(item.id),
    active_services: ['ollama', 'sub2api'].filter(service => currentNodes[service] === item.id)})),
    templates: sharedTemplates, default_server_port: sharedTemplates.ollama?.provider ? 7000 : null,
    current: currentNodes, selected: currentNodes.ollama === currentNodes.sub2api ? currentNodes.ollama : null,
    split: currentNodes.ollama !== currentNodes.sub2api};
}
const requests = [];
const pageErrors = [];

function location(host) {
  const places = {'8.8.8.8': ['US', '美国', 'Mountain View'], '1.1.1.1': ['AU', '澳大利亚', 'Sydney']};
  const [country, country_name, city] = places[host] || (host === '9.9.9.9' ? ['', '', ''] : ['SG', '新加坡', '新加坡']);
  return {state: country ? 'ok' : 'unknown', country, country_name, city,
    checked_at: new Date().toISOString(), source: 'offline-fixture'};
}

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
  if (url.pathname === '/api/vps-nodes') {
    if (!payload) return json(sharedSnapshot());
    assert.equal(req.headers['x-local-csrf'], 'fixture-csrf');
    const item = sharedItems.find(value => value.id === payload.id);
    if (payload.action === 'location') {
      const host = payload.server_addr || item?.server_addr;
      if (locationDelays.get(host)) await new Promise(resolve => setTimeout(resolve, locationDelays.get(host)));
      return json(location(host));
    }
    if (payload.action === 'check') {
      if (networkDelay) await new Promise(resolve => setTimeout(resolve, networkDelay));
      return json({ping: {state: pingState, latency_ms: pingState === 'ok' ? 23 : null}, tcp: {state: 'ok', latency_ms: 18}, checked_at: new Date().toISOString(), location: location(item?.server_addr)});
    }
    if (payload.action === 'copy') {
      assert.ok(item && ['ollama', 'sub2api'].includes(payload.service));
      return json({message: 'fixture clipboard copied'});
    }
    if (payload.action === 'apply') {
      assert.ok(item);
      currentNodes = {ollama: item.id, sub2api: item.id};
      sharedItems.forEach(value => { value.pending = false; });
      return json({message: '已应用共用节点，连接开关保持原状态'});
    }
    let savedId;
    if (payload.action === 'delete') {
      if (Object.values(currentNodes).includes(payload.id)) return json({error: 'cannot delete active node'}, 409);
      sharedItems = sharedItems.filter(value => value.id !== payload.id);
    } else if (payload.action === 'save' || payload.action === 'duplicate') {
      const fields = payload.fields?.common || {};
      const host = fields.server_addr || (payload.visitor_toml ? 'imported.fixture.invalid' : item?.server_addr);
      const port = fields.server_port || item?.server_port || 7000;
      savedId = item && payload.action !== 'duplicate' && item.server_addr === host && item.server_port === port ? item.id : `saved-vps-${++routeCounter}`;
      if (sharedItems.some(value => value.id !== item?.id && value.server_addr === host && value.server_port === port)) return json({error: '节点已存在，请编辑现有节点'}, 409);
      const source = item || sharedItems[0];
      const saved = {id: savedId, name: payload.name, server_addr: host, server_port: port,
        country: fields.country || '', city: fields.city || '',
        pending: !!item && Object.values(currentNodes).includes(item.id) && (item.pending || savedId !== item.id),
        services: structuredClone(source?.services || {ollama: {configured: true, provider: 'ollama_fixture', local_port: 11436}, sub2api: {configured: true, provider: 'sub2api_fixture', local_port: 18081}})};
      for (const service of ['ollama', 'sub2api']) if (payload.fields?.services?.[service]?.server_name) saved.services[service].provider = payload.fields.services[service].server_name;
      if (item && payload.action !== 'duplicate') {
        sharedItems.splice(sharedItems.indexOf(item), 1, saved);
        for (const service of ['ollama', 'sub2api']) if (currentNodes[service] === item.id) currentNodes[service] = savedId;
      } else sharedItems.push(saved);
    }
    return json({message: 'fixture 节点已保存', saved_id: savedId, pending: sharedItems.find(value => value.id === savedId)?.pending || false});
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
  const layoutEvidence = {home: [], longText: [], manager: []};
  const evidence = process.env.LOCAL_AI_EVIDENCE;
  if (evidence) fs.mkdirSync(evidence, {recursive: true});
  function sameTypography(items, label, width) {
    for (const item of items.slice(1)) {
      assert.deepEqual(item.typography, items[0].typography,
        `${label} typography differs at ${width}: ${item.label}`);
    }
  }
  function alignedBounds(items, label, width) {
    for (const edge of ['top', 'bottom']) {
      for (const item of items.slice(1)) {
        assert.ok(Math.abs(item.bounds[edge] - items[0].bounds[edge]) <= 1,
          `${label} ${edge} differs at ${width}: ${item.label}`);
      }
    }
  }
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch(process.env.LOCAL_AI_BROWSER === 'chromium' ? {headless: true}
      : {channel: process.env.LOCAL_AI_BROWSER_CHANNEL || 'chrome', headless: true});
    const page = await browser.newPage({viewport: {width: 1000, height: 760}});
    page.on('pageerror', error => pageErrors.push(error.message));
    const dialogs = [];
    let acceptDialogs = true;
    page.on('dialog', async dialog => { dialogs.push(dialog.message());await (acceptDialogs ? dialog.accept() : dialog.dismiss()); });
    await page.route('**/*', route => route.request().url().startsWith(base)
      ? route.continue() : route.abort());
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], {origin: base});
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
      const layout = await page.evaluate(() => {
        const cards = [...document.querySelectorAll('.connection-grid>.card')].filter(node => !node.hidden);
        function snapshot(node) {
          const style = getComputedStyle(node), bounds = node.getBoundingClientRect();
          return {label: node.id || node.textContent.trim(), typography: {
            fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight
          }, bounds: {left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom}};
        }
        return {
          titles: [...cards.map(node => node.querySelector('.card-title h2')),
            document.querySelector('.shared-nodes h3')].map(snapshot),
          states: cards.map(node => snapshot(node.querySelector('.connection-overview strong'))),
          addresses: cards.map(node => snapshot(node.querySelector('.connection-overview code'))),
          currentNodes: [...cards.map(node => node.querySelector('.connection-node')),
            document.querySelector('#vps-current')].map(snapshot),
          rows: Object.fromEntries(['card-title', 'connection-overview', 'connection-health',
            'launch-actions', 'connection-node'].map(name => [name,
            cards.map(node => snapshot(node.querySelector(`.${name}`)))])),
          buttons: {
            switches: cards.map(node => snapshot(node.querySelector('.switch'))),
            diagnostics: ['#ollama-diagnostics', '#sub2api-check', '#sub2api-diagnostics']
              .map(selector => snapshot(document.querySelector(selector))),
            launch: [...document.querySelectorAll('.connection-grid .launch-actions .secondary')].map(snapshot)
          }
        };
      });
      assert.equal(layout.titles.length, 3, `both service titles and shared VPS title must exist at ${width}`);
      assert.equal(layout.states.length, 2, `both service cards must be visible at ${width}`);
      for (const name of ['titles', 'states', 'addresses', 'currentNodes']) sameTypography(layout[name], name, width);
      for (const name of ['diagnostics', 'launch']) sameTypography(layout.buttons[name], `${name} buttons`, width);
      if (width >= 720) {
        for (const [name, items] of Object.entries(layout.rows)) alignedBounds(items, name, width);
        for (const [name, items] of Object.entries(layout.buttons)) alignedBounds(items, `${name} buttons`, width);
        const rows = await page.locator('#vps-nodes .home-node').all();
        const first = await rows[0].boundingBox(), second = await rows[1].boundingBox();
        assert.ok(second.y >= first.y + first.height - 1, `shared nodes must be one full-width list at ${width}`);
        assert.equal(Math.round(first.width), Math.round(second.width));
      }
      layoutEvidence.home.push({width, dimensions, ...layout});
      if (evidence) await page.screenshot({path: path.join(evidence, `ui-home-${width}.png`)});
    }
    checks.push('matching service/shared VPS title typography, status/address/current-node text and button typography at 900/720/390px; desktop service rows and actual button top/bottom edges align');
    const longIp = '2001:0db8:1234:5678:90ab:cdef:1234:5678';
    const stressText = {
      '#ollama-url': `http://[${longIp}]:11435/v1/${'relay-without-spaces-'.repeat(9)}responses`,
      '#ollama-node': `当前 VPS · ${'VeryLongBackupWindowsNodeWithoutSpaces'.repeat(4)} · [${longIp}]:7000`
    };
    const originalText = await page.evaluate(() => Object.fromEntries(
      ['#ollama-url', '#ollama-node', '#sub2api-url', '#sub2api-node']
        .map(selector => [selector, document.querySelector(selector).innerHTML])));
    try {
      await page.evaluate(texts => {
        for (const [selector, text] of Object.entries(texts)) document.querySelector(selector).textContent = text;
      }, stressText);
      for (const width of [900, 720, 390]) {
        await page.setViewportSize({width, height: 700});
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const stress = await page.evaluate(() => {
          const cards = [...document.querySelectorAll('.connection-grid>.card')].filter(node => !node.hidden);
          function bounds(node) {
            const rect = node.getBoundingClientRect();
            return {label: node.id || node.className, bounds: {
              left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom}};
          }
          function textVisibility(selector) {
            const node = document.querySelector(selector), style = getComputedStyle(node);
            const box = node.getBoundingClientRect(), range = document.createRange();
            range.selectNodeContents(node);
            const lines = [...range.getClientRects()];
            return {selector, text: node.textContent, lineCount: lines.length,
              textOverflow: style.textOverflow, lineClamp: style.webkitLineClamp,
              scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
              scrollHeight: node.scrollHeight, clientHeight: node.clientHeight,
              allTextInside: lines.every(rect => rect.left >= box.left - 1 && rect.right <= box.right + 1
                && rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1)};
          }
          return {
            viewportWidth: innerWidth, scrollWidth: document.documentElement.scrollWidth,
            cards: cards.map(bounds),
            states: cards.map(node => bounds(node.querySelector('.connection-overview strong'))),
            addresses: cards.map(node => bounds(node.querySelector('.connection-overview code'))),
            rows: Object.fromEntries(['connection-overview', 'connection-health', 'launch-actions', 'connection-node']
              .map(name => [name, cards.map(node => bounds(node.querySelector(`.${name}`)))])),
            buttons: {
              diagnostics: ['#ollama-diagnostics', '#sub2api-check', '#sub2api-diagnostics']
                .map(selector => bounds(document.querySelector(selector))),
              launch: [...document.querySelectorAll('.connection-grid .launch-actions .secondary')].map(bounds)
            },
            longTexts: ['#ollama-url', '#ollama-node'].map(textVisibility),
            shortTexts: Object.fromEntries(['#sub2api-url', '#sub2api-node']
              .map(selector => [selector, document.querySelector(selector).innerHTML]))
          };
        });
        assert.ok(stress.scrollWidth <= stress.viewportWidth, `long service text overflows viewport at ${width}`);
        for (const visible of stress.longTexts) {
          assert.equal(visible.text, stressText[visible.selector], `complete long address/name must remain at ${width}`);
          assert.ok(visible.text.includes(longIp), `complete IPv6 address must remain at ${width}`);
          assert.ok(visible.lineCount > 1, `long text must actually wrap at ${width}`);
          assert.notEqual(visible.textOverflow, 'ellipsis', `long text must not be ellipsized at ${width}`);
          assert.ok(['none', '0'].includes(visible.lineClamp), `long text must not be line-clamped at ${width}`);
          assert.ok(visible.allTextInside && visible.scrollWidth <= visible.clientWidth + 1
            && visible.scrollHeight <= visible.clientHeight + 1,
          `all long address/name lines must remain visible inside their element at ${width}`);
        }
        for (const [selector, html] of Object.entries(stress.shortTexts)) assert.equal(html, originalText[selector]);
        if (width >= 720) {
          alignedBounds(stress.states, 'long-text status row', width);
          // Wrapped addresses may occupy different numbers of text lines. Their start and enclosing
          // grid-row boundaries must align so the following health/actions/current-node rows stay level.
          assert.ok(Math.abs(stress.addresses[0].bounds.top - stress.addresses[1].bounds.top) <= 1,
            `long-text address row starts must align at ${width}`);
          for (const [name, items] of Object.entries(stress.rows)) alignedBounds(items, `long-text ${name}`, width);
          for (const [name, items] of Object.entries(stress.buttons)) alignedBounds(items, `long-text ${name} buttons`, width);
          alignedBounds(stress.cards, 'long-text card edges', width);
        }
        layoutEvidence.longText.push({width, ...stress});
        await page.evaluate(() => scrollTo(0, 0));
        if (evidence) await page.screenshot({path: path.join(evidence, `ui-home-long-text-${width}.png`), fullPage: true});
      }
    } finally {
      await page.evaluate(originals => {
        for (const [selector, html] of Object.entries(originals)) document.querySelector(selector).innerHTML = html;
      }, originalText);
    }
    assert.deepEqual(await page.evaluate(() => Object.fromEntries(
      ['#ollama-url', '#ollama-node', '#sub2api-url', '#sub2api-node']
        .map(selector => [selector, document.querySelector(selector).innerHTML]))), originalText,
    'long-text stress fixture must restore the complete original DOM of all affected nodes');
    checks.push('one-sided long unbroken API address/current VPS name and full IPv6 remain visible without truncation or horizontal overflow at 900/720/390px; desktop row/card alignment and actual button edges hold; original DOM fully restored');
    await page.setViewportSize({width: 1000, height: 760});
    assert.equal(requests.filter(item => item.method === 'POST').length, 0);
    assert.equal(await page.locator('#vps-nodes .home-node').count(), 2);
    assert.equal(await page.locator('#model-nodes, #sub2api-nodes, #routes-service').count(), 0);
    assert.equal(await page.locator('#manage-vps-nodes').count(), 1);
    assert.match(await page.locator('#vps-current').innerText(), /当前使用不同节点/);
    assert.match(await page.locator('#ollama-node').innerText(), /fixture-a.invalid/);
    assert.match(await page.locator('#sub2api-node').innerText(), /fixture-b.invalid/);
    const homeNode = id => page.locator(`#vps-nodes .home-node[data-node-id="${id}"]`);
    const homeAction = (id, action) => homeNode(id).locator(`[data-node-action="${action}"]`);
    assert.match(await homeNode('vps-a').innerText(), /模型使用中/);
    assert.match(await homeNode('vps-b').innerText(), /Sub2 使用中/);
    assert.equal(await homeAction('vps-a', 'use').isDisabled(), false);
    status.active_chats = 1;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => !document.querySelector('#ollama-guard').hidden);
    assert.equal(await homeAction('vps-b', 'use').isDisabled(), true);
    status.active_chats = 0;
    status.sub2api.running = false;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => !document.querySelector('#vps-nodes [data-node-id="vps-b"] [data-node-action="use"]').disabled);
    await homeAction('vps-b', 'use').click();
    await page.waitForFunction(() => document.querySelector('#vps-nodes [data-node-id="vps-b"] .node-badge').textContent === '两条连接使用中');
    assert.deepEqual(currentNodes, {ollama: 'vps-b', sub2api: 'vps-b'});
    assert.equal(status.sub2api.running, false);
    assert.equal(dialogs.some(text => text.includes('已关闭的保持关闭')), true);
    await homeAction('vps-b', 'copy-ip').click();
    await page.getByRole('status').filter({hasText: '节点 IP 已复制'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'fixture-b.invalid');
    await homeAction('vps-a', 'check').click();
    await page.waitForFunction(() => document.querySelector('#vps-nodes [data-node-id="vps-a"] .node-network')?.textContent.includes('Ping 23 ms'));
    assert.deepEqual(requests.filter(item => item.payload?.action === 'check').map(item => item.payload), [{action: 'check', id: 'vps-a'}]);
    pingState = 'timeout';
    await homeAction('vps-a', 'check').click();
    await page.waitForFunction(() => document.querySelector('#vps-nodes [data-node-id="vps-a"] .node-network')?.textContent.includes('Ping 超时'));
    pingState = 'ok';
    checks.push('one shared VPS list and one manager; split current service nodes remain distinct until one explicit apply; apply changes both nodes and preserves an off connection; generation guard and actual clipboard');

    await page.locator('#manage-vps-nodes').click();
    const managerNode = id => page.locator(`#routes-list [data-node-id="${id}"]`);
    await managerNode('vps-b').waitFor();
    assert.equal(await page.locator('#route-name').inputValue(), '节点 B');
    assert.equal(await page.locator('#route-country').isVisible(), false);
    assert.equal(await page.locator('#route-city').isVisible(), false);
    assert.equal(await page.locator('#route-delete').isDisabled(), true);
    await page.locator('#route-name').fill('Renamed shared node');
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: 'fixture 节点已保存'}).waitFor();
    assert.equal(requests.filter(item => item.payload?.action === 'save').at(-1).payload.id, 'vps-b');
    assert.equal(sharedItems.length, 2);
    await page.locator('#route-check').click();
    await page.waitForFunction(() => document.querySelector('#route-check-result').textContent.includes('Ping 23 ms'));
    await page.locator('#route-server').fill('changed.fixture.invalid');
    assert.equal(await page.locator('#route-check').isDisabled(), true);
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: '已保存。点'}).waitFor();
    let changedId = requests.filter(item => item.payload?.action === 'save').at(-1);
    changedId = sharedItems.find(item => item.server_addr === 'changed.fixture.invalid').id;
    assert.equal(await page.locator('#route-check-result').innerText(), '未检测');
    assert.equal((await homeNode(changedId).innerText()).includes('Ping 23 ms'), false);
    networkDelay = 600;
    await page.locator('#route-check').click();
    await page.waitForFunction(() => document.querySelector('#route-check-result').textContent === '检测中…');
    await page.locator('#route-server').fill('newer.fixture.invalid');
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: '已保存。点'}).waitFor();
    await page.waitForTimeout(700);
    assert.equal(await page.locator('#route-check-result').innerText(), '未检测');
    networkDelay = 0;
    await page.locator('#route-save-use').click();
    await page.getByRole('status').filter({hasText: '节点已使用'}).waitFor();
    assert.equal(currentNodes.ollama, currentNodes.sub2api);
    checks.push('editing shared original updates same endpoint id; IP changes get new id; save stays pending; old endpoint checks never attach to new IP; one save-and-use applies both services');

    await page.locator('#route-new').click();
    await page.locator('#route-name').fill('备用节点');
    locationDelays.set('8.8.8.8', 900);
    await page.locator('#route-server').fill('8.8.8.8');
    await page.locator('#route-server').blur();
    await page.waitForFunction(() => document.querySelector('#route-location').textContent === '识别中…');
    await page.locator('#route-server').fill('1.1.1.1');
    await page.locator('#route-server').dispatchEvent('paste');
    await page.locator('#route-server').blur();
    await page.waitForFunction(() => document.querySelector('#route-location').textContent.includes('Sydney'));
    await page.waitForTimeout(1000);
    assert.match(await page.locator('#route-location').innerText(), /澳大利亚.*Sydney/);
    assert.equal(requests.filter(item => item.payload?.action === 'location' && item.payload.server_addr === '1.1.1.1').length, 1);
    locationDelays.clear();
    assert.equal(await page.locator('#route-advanced').evaluate(node => node.open), false);
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: 'fixture 节点已保存'}).waitFor();
    const newCall = requests.filter(item => item.payload?.action === 'save').at(-1);
    assert.equal(newCall.payload.id, undefined);
    assert.equal(newCall.payload.fields.common.country, 'AU');
    assert.equal(newCall.payload.fields.common.city, 'Sydney');
    assert.equal(newCall.payload.fields.services.ollama.secret_key, '');
    assert.equal(newCall.payload.fields.services.sub2api.secret_key, '');
    assert.equal(newCall.payload.fields.services.ollama.server_name, 'actual-current-model-provider');
    assert.equal(newCall.payload.fields.services.sub2api.server_name, 'actual-current-api-provider');
    const newRouteId = sharedItems.at(-1).id;
    await page.locator('#route-advanced>summary').click();
    await page.locator('#route-port').fill('7002');
    await page.locator('#route-ollama-provider').fill('fixture_model_provider');
    await page.locator('#route-sub2api-provider').fill('fixture_sub_provider');
    await page.locator('#route-ollama-secret').fill('fixture-model-secret');
    await page.locator('#route-sub2api-secret').fill('fixture-sub-secret');
    await page.locator('#route-token').fill('fixture-common-token');
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: 'fixture 节点已保存'}).waitFor();
    const advancedCall = requests.filter(item => item.payload?.action === 'save').at(-1).payload;
    assert.equal(advancedCall.fields.services.ollama.server_name, 'fixture_model_provider');
    assert.equal(advancedCall.fields.services.sub2api.server_name, 'fixture_sub_provider');
    assert.equal(await page.locator('#route-ollama-secret').inputValue(), '');
    assert.equal(await page.locator('#route-sub2api-secret').inputValue(), '');
    assert.equal(await page.locator('#route-token').inputValue(), '');
    await page.locator('#route-name').fill('unsaved shared draft');
    await page.locator('#route-advanced>summary').click();
    await page.locator('#route-copy-ollama').click();
    await page.getByRole('status').filter({hasText: '完整 visitor 已复制'}).waitFor();
    assert.equal(requests.filter(item => item.payload?.action === 'copy').at(-1).payload.service, 'ollama');
    assert.equal(await page.locator('#route-name').inputValue(), 'unsaved shared draft');
    acceptDialogs = false;
    await managerNode('vps-a').click();
    assert.equal(await page.locator('#route-name').inputValue(), 'unsaved shared draft');
    acceptDialogs = true;
    await page.locator('#route-edit-cancel').click();
    await page.locator('#route-duplicate').click();
    assert.equal(await page.locator('#route-editor-title').innerText(), '复制节点');
    await page.locator('#route-server').fill('copied.fixture.invalid');
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: 'fixture 节点已保存'}).waitFor();
    assert.equal(requests.filter(item => item.payload?.action === 'duplicate').at(-1).payload.id,
      sharedItems.find(item => item.server_addr === '1.1.1.1' && item.server_port === 7002).id);
    const copiedId = sharedItems.at(-1).id;
    await page.locator('#route-delete').click();
    await page.locator('#route-delete-yes').click();
    await page.waitForFunction(id => !document.querySelector(`#routes-list [data-node-id="${id}"]`), copiedId);
    checks.push('new common node inherits both service settings with name/IP only; independent advanced providers and secrets preserved; copying visitor keeps draft; direct node switch cancel preserves draft; explicit copied node can be removed');

    await page.locator('#route-import').click();
    await page.locator('#route-name').fill('Imported visitor');
    const importedTOML = 'serverAddr = "fixture.invalid"\n# fixture visitor only\n';
    await page.locator('#route-file').setInputFiles({name: 'fixture-visitor.toml', mimeType: 'text/plain', buffer: Buffer.from(importedTOML)});
    await page.waitForFunction(() => document.querySelector('#route-toml').value.includes('fixture visitor only'));
    await page.locator('#route-save').click();
    await page.getByRole('status').filter({hasText: 'fixture 节点已保存'}).waitFor();
    assert.equal(requests.filter(item => item.payload?.visitor_toml).at(-1).payload.visitor_toml, importedTOML);
    assert.equal(await page.locator('#route-toml').inputValue(), '');
    const importId = sharedItems.at(-1).id;
    locationDelays.set('9.9.9.9', 400);
    await page.locator('#route-server').fill('9.9.9.9');
    const unknownSaved = page.waitForResponse(response => response.url().endsWith('/api/vps-nodes')
      && response.request().method() === 'POST' && response.request().postDataJSON().action === 'save');
    const unknownStarted = Date.now();
    await page.locator('#route-save').click();
    await unknownSaved;
    assert.ok(Date.now() - unknownStarted >= 350);
    await page.waitForFunction(() => !document.querySelector('#route-save').disabled);
    assert.equal(requests.filter(item => item.payload?.action === 'save').at(-1).payload.fields.common.country, '');
    assert.equal(requests.filter(item => item.payload?.action === 'location' && item.payload.server_addr === '9.9.9.9').length, 1);
    locationDelays.clear();
    const cleanDialogs = dialogs.length;
    await page.locator('#routes-close').click();
    assert.equal(dialogs.length, cleanDialogs);
    const unlocated = sharedItems[0];
    Object.assign(unlocated, {country: '', city: '', server_addr: '4.2.2.2'});
    await homeAction(unlocated.id, 'edit').click();
    await page.waitForFunction(() => document.querySelector('#route-location').textContent.includes('新加坡'));
    assert.equal(requests.filter(item => item.payload?.action === 'location' && item.payload.server_addr === '4.2.2.2').length, 1);
    const autoDialogs = dialogs.length;
    await page.locator('#routes-close').click();
    assert.equal(dialogs.length, autoDialogs);
    checks.push('automatic public-IP blur/paste location coalesces, old-IP responses are ignored, auto location is saved; bounded lookup finishes before save, unknown location does not block; opening old node automatically queries once without dirtying editor');

    await page.locator('#manage-vps-nodes').click();
    for (const [width, height] of [[1000, 720], [720, 700], [390, 700]]) {
      await page.setViewportSize({width, height});
      const box = await page.locator('#route-manager').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width);
      assert.ok(box.height <= height * .91);
      assert.equal(await page.locator('#route-manager').evaluate(node => node.scrollWidth <= node.clientWidth), true);
      if (evidence) await page.screenshot({path: path.join(evidence, `ui-nodes-manager-${width}.png`)});
      if (width === 390) {
        const advanced = page.locator('#route-advanced');
        const wasOpen = await advanced.evaluate(node => node.open);
        if (!wasOpen) await advanced.locator('summary').click();
        const footerButtons = [];
        for (const selector of ['#route-save', '#route-edit-cancel']) {
          const button = page.locator(selector);
          await button.scrollIntoViewIfNeeded();
          const reachability = await button.evaluate(node => {
            const bounds = node.getBoundingClientRect(), dialog = node.closest('dialog');
            const dialogBounds = dialog.getBoundingClientRect();
            const hit = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
            return {id: node.id, top: bounds.top, bottom: bounds.bottom,
              inViewport: bounds.top >= 0 && bounds.bottom <= innerHeight,
              inDialog: bounds.top >= dialogBounds.top && bounds.bottom <= dialogBounds.bottom,
              hitTarget: hit === node || node.contains(hit), scrollTop: dialog.scrollTop,
              scrollWidth: dialog.scrollWidth, clientWidth: dialog.clientWidth,
              pageWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth};
          });
          assert.ok(reachability.inViewport && reachability.inDialog && reachability.hitTarget,
            `${selector} must be reachable inside the scrolled 390px manager`);
          assert.ok(reachability.scrollTop > 0, 'expanded mobile manager must scroll to reach footer buttons');
          assert.ok(reachability.scrollWidth <= reachability.clientWidth, 'expanded mobile manager must not scroll horizontally');
          assert.ok(reachability.pageWidth <= reachability.viewportWidth, 'mobile manager must not overflow the viewport');
          footerButtons.push(reachability);
        }
        layoutEvidence.manager.push({width, height, advancedOpen: true, footerButtons});
        if (evidence) await page.screenshot({path: path.join(evidence, 'ui-nodes-manager-390-bottom.png')});
        if (!wasOpen) await advanced.locator('summary').click();
        await page.locator('#route-manager').evaluate(node => { node.scrollTop = 0; });
      }
    }
    checks.push('expanded 390px VPS editor scrolls to usable save/restore buttons without dialog or viewport horizontal overflow');
    await page.setViewportSize({width: 1000, height: 760});
    await page.locator('#routes-close').click();
    const keptItems = sharedItems, keptTemplates = sharedTemplates, keptCurrent = currentNodes;
    sharedItems = [];sharedTemplates = {ollama: {provider: null}, sub2api: {provider: null}};currentNodes = {};
    await page.locator('#manage-vps-nodes').click();
    await page.waitForFunction(() => document.querySelector('#route-editor-title').textContent === '新增节点');
    assert.equal(await page.locator('#route-ollama-provider').inputValue(), '');
    assert.equal(await page.locator('#route-sub2api-provider').inputValue(), '');
    assert.equal(await page.locator('#route-port').inputValue(), '');
    assert.equal(await page.locator('#route-advanced').evaluate(node => node.open), true);
    await page.locator('#route-name').fill('No template first node');
    await page.locator('#route-server').fill('first.fixture.invalid');
    assert.equal(await page.locator('#route-form').evaluate(node => node.reportValidity()), false);
    assert.equal(await page.locator('#route-port').evaluate(node => document.activeElement === node), true);
    sharedItems = keptItems;sharedTemplates = keptTemplates;currentNodes = keptCurrent;
    await page.locator('#route-edit-cancel').click();
    await page.locator('#routes-refresh').click();
    await page.waitForFunction(() => document.querySelector('#routes-list').children.length > 0);
    await page.locator('#routes-close').click();
    assert.equal(requests.some(item => item.path === '/api/routes'), false);
    assert.equal(requests.some(item => item.path === '/api/action' && ['route', 'model-route'].includes(item.payload?.service)), false);
    checks.push('one unified manager at desktop/720px/390px without overflow; no service selector or duplicate lists; all VPS actions use shared-node API');
    checks.push('new nodes use actual current-template provider metadata; no-template provider values remain blank and advanced required credentials stay visible');

    if (!await page.locator('.management').evaluate(node => node.open)) await page.locator('.management>summary').click();
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
    if (!await page.locator('.management').evaluate(node => node.open)) await page.locator('.management>summary').click();
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
    const result = {offline: true, checks, page_errors: pageErrors};
    if (evidence) fs.writeFileSync(path.join(evidence, 'ui-verification.json'),
      JSON.stringify({...result, layout: layoutEvidence}, null, 2) + '\n');
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    const failed = browser?.contexts()[0]?.pages()[0];
    if (failed) {
      if (evidence) await failed.screenshot({path: path.join(evidence, 'ui-failure.png')}).catch(() => {});
      console.error(JSON.stringify({fixture_failure_state: await failed.evaluate(() => ({
        title: document.title, send: document.querySelector('#send') ? {disabled: document.querySelector('#send').disabled, bounds: document.querySelector('#send').getBoundingClientRect().toJSON()} : null,
        activeDialogs: [...document.querySelectorAll('dialog[open]')].map(node => node.id),
        managementOpen: document.querySelector('.management')?.open,
        message: document.querySelector('#message')?.textContent
      })).catch(() => null)}));
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections?.();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error.stack || error.message);process.exitCode = 1; });
