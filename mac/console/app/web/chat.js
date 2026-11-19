const $ = id => document.getElementById(id);
let csrf = '';
let history = [];
let abort = null;
let activeRequest = null;
let availableModels = [];
let modelsFresh = false;
let modelsLoading = false;
let rememberedModel = '';
let preferenceBusy = false;

function note(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}

function controls() {
  const generating = !!abort;
  $('send').disabled = generating || !modelsFresh || !availableModels.includes($('model').value);
  $('stop').disabled = !generating || !!activeRequest?.stopped;
  $('model').disabled = generating || modelsLoading || !availableModels.length;
  $('reload').disabled = generating || modelsLoading;
  $('new-chat').disabled = generating;
  $('remember-model').disabled = generating || modelsLoading || preferenceBusy || !modelsFresh
    || !availableModels.includes($('model').value) || $('model').value === rememberedModel;
  $('model-default').textContent = rememberedModel ? `网页默认：${rememberedModel}` : '尚未设置网页默认模型';
}

async function requestJSON(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Local-CSRF': csrf},
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '操作失败');
  return data;
}

async function models() {
  if (modelsLoading || abort) return;
  modelsLoading = true;
  modelsFresh = false;
  controls();
  $('model-status').textContent = '正在刷新模型列表…';
  try {
    const selected = $('model').value;
    const data = await requestJSON('/api/models');
    const names = [...new Set((data.models || []).filter(name => typeof name === 'string' && name))];
    availableModels = names;
    rememberedModel = typeof data.default_model === 'string' ? data.default_model : '';
    $('model').replaceChildren();
    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      $('model').append(option);
    }
    if (names.length) {
      $('model').value = names.includes(selected) ? selected
        : names.includes(rememberedModel) ? rememberedModel : names[0];
      const modelChanged = !!selected && $('model').value !== selected;
      if (modelChanged) {
        history = [];
        $('messages').replaceChildren();
      }
      modelsFresh = true;
      $('model-status').textContent = `已读取 ${names.length} 个实际模型`;
      note(modelChanged ? '原模型已不在列表，已选择可用模型并开始新对话；草稿保留。'
        : '模型列表已刷新。');
    } else {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '远端暂无模型';
      $('model').append(option);
      $('model-status').textContent = '远端未返回可用模型';
      note('远端暂无模型，发送已停用。', true);
    }
  } catch (error) {
    if (!availableModels.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '尚未取得模型';
      $('model').replaceChildren(option);
    }
    $('model-status').textContent = availableModels.length
      ? '模型列表已过期 · 请刷新后再发送' : '尚未取得可用模型列表';
    note(error.message || '模型读取失败，请检查连接。', true);
  } finally {
    modelsLoading = false;
    controls();
  }
}

function bubble(role, text = '') {
  const empty = $('empty');
  if (empty) empty.remove();
  const block = document.createElement('div');
  block.className = `bubble ${role}`;
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? '你' : '助手';
  const content = document.createElement('div');
  content.textContent = text;
  block.append(label, content);
  $('messages').append(block);
  return {block, content};
}

function scroll() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

$('form').onsubmit = async event => {
  event.preventDefault();
  if (abort) return;
  const model = $('model').value;
  const text = $('input').value.trim();
  if (!modelsFresh || !availableModels.includes(model)) {
    note('模型列表不可用或已过期，请显式刷新后再发送。', true);
    return;
  }
  if (!model || !text) {
    note('请先选择模型并输入消息。', true);
    return;
  }
  bubble('user', text);
  // Only the submitted text is cleared; the user can draft the next message while streaming.
  $('input').value = '';
  const answer = bubble('assistant');
  const thinking = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = '推理内容';
  const thought = document.createElement('div');
  thinking.append(summary, thought);
  answer.block.append(thinking);
  thinking.hidden = true;
  let content = '';
  let reasoning = '';
  let done = false;
  let streamReader = null;
  const submittedHistory = [...history, {role: 'user', content: text}].slice(-12);
  abort = new AbortController();
  const request = {request_id: crypto.randomUUID(), controller: abort, stopped: false, cancellation: null};
  activeRequest = request;
  controls();
  note('正在等待回答…');
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Local-CSRF': csrf},
      body: JSON.stringify({model, messages: submittedHistory, request_id: request.request_id}),
      signal: request.controller.signal
    });
    if (!response.ok) {
      const data = await response.json();
      throw Error(data.error || '请求失败');
    }
    if (!response.body) throw Error('回答未返回流式内容。');
    const reader = response.body.getReader();
    streamReader = reader;
    const decoder = new TextDecoder();
    let buffer = '';
    function eventLine(line) {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.error) throw Error(event.error);
      if (event.message) {
        content += event.message.content || '';
        reasoning += event.message.thinking || '';
        answer.content.textContent = content;
        thought.textContent = reasoning;
        thinking.hidden = !reasoning;
        scroll();
      }
      if (event.done) done = true;
    }
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, {stream: true});
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        eventLine(buffer.slice(0, index));
        buffer = buffer.slice(index + 1);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) eventLine(buffer);
    if (!done) throw Error('连接中断，回答未完整结束；未自动重发。');
    history = [...submittedHistory, {role: 'assistant', content}].slice(-12);
    note(content ? '回答完成。' : '请求已结束，未生成正文；可能输出预算被推理消耗。');
  } catch (error) {
    note(error.name === 'AbortError' ? '已停止；未自动重发。' : error.message, true);
    answer.content.textContent = content ? `${content}\n\n[回答未完整结束]` : '[回答未完成]';
    if (error.name !== 'AbortError') {
      modelsFresh = false;
      $('model-status').textContent = '连接未完成 · 模型列表已过期，请刷新后再发送';
    }
    // Restore a failed prompt only if the user has not started a new draft.
    if (!$('input').value) $('input').value = text;
  } finally {
    if (streamReader && !done) {
      try { await streamReader.cancel(); } catch (_) { /* Connection already closed. */ }
    }
    if (request.cancellation && !await request.cancellation) {
      note('已停止网页接收；本机取消未确认，未自动重试。', true);
    }
    activeRequest = null;
    abort = null;
    controls();
  }
};

$('stop').onclick = () => {
  const request = activeRequest;
  if (!request || request.stopped) return;
  request.stopped = true;
  request.controller.abort();
  // Each cancellation targets this page's exact request and is sent only once.
  request.cancellation = requestJSON('/api/chat/cancel', {request_id: request.request_id})
    .then(() => true, () => false);
  controls();
};
$('reload').onclick = models;
$('new-chat').onclick = () => {
  if (abort) return;
  history = [];
  $('messages').replaceChildren();
  note('已开始新对话，草稿已保留。');
};
$('model').onchange = () => {
  history = [];
  $('messages').replaceChildren();
  controls();
  note('已切换模型并开始新对话，草稿已保留。');
};
$('remember-model').onclick = async () => {
  const selected = $('model').value;
  if (preferenceBusy || !modelsFresh || !availableModels.includes(selected)) return;
  preferenceBusy = true;
  controls();
  try {
    const data = await requestJSON('/api/preferences', {default_model: selected});
    rememberedModel = data.default_model;
    note('已记住网页聊天默认模型。');
  } catch (error) {
    note(error.message, true);
  } finally {
    preferenceBusy = false;
    controls();
  }
};

(async () => {
  try {
    csrf = (await requestJSON('/api/session')).csrf;
    await models();
  } catch (error) {
    modelsFresh = false;
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '尚未取得模型';
    $('model').replaceChildren(option);
    $('model-status').textContent = '尚未取得可用模型列表';
    controls();
    note('本机控制台未连接，草稿保留。', true);
  }
})();
