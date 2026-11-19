"""Bounded, explicit gateway checks and local-only, allowlisted diagnostics.

No raw log lines, response text, configured addresses or credentials leave this
module. A stored success belongs to one connection generation, for five minutes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request

from .core import UserError, private_key

TTL = 300
MAX_JSON = 1024 * 1024
MODEL = re.compile(r"^[^\x00-\x20\x7f]{1,200}$")
SERVICES = ("sub2api", "ollama", "console")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def age(timestamp):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return TTL + 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the downstream key to a redirected endpoint.


def local_url(base, suffix):
    url = urllib.parse.urlsplit(base)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1") or
            url.username or url.password or url.query or url.fragment):
        raise UserError("诊断仅允许配置的本机 HTTP 接口。")
    return base.rstrip("/") + suffix


def request(url, *, key=None, payload=None, timeout=8):
    headers = {"Accept": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    if payload is not None:
        headers.update({"Content-Type": "application/json", "Accept": "text/event-stream"})
    req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout)


def read_json(response):
    chunks, total = [], 0
    deadline = time.monotonic() + 12
    read = getattr(response, "read1", response.read)
    while time.monotonic() < deadline:
        chunk = read(min(65536, MAX_JSON + 1 - total))
        if not chunk:
            return json.loads(b"".join(chunks))
        total += len(chunk)
        if total > MAX_JSON:
            raise UserError("接口响应超过诊断大小限制。")
        chunks.append(chunk)
    raise UserError("接口读取超过诊断时间限制；未重试。")


def model_names(data, service):
    values = data.get("data" if service == "sub2api" else "models") if isinstance(data, dict) else None
    if not isinstance(values, list) or len(values) > 4096:
        raise UserError("模型列表格式不符合预期。")
    field = "id" if service == "sub2api" else "name"
    names = []
    for value in values:
        name = value.get(field) if isinstance(value, dict) else None
        if isinstance(name, str) and MODEL.fullmatch(name) and name not in names:
            names.append(name)
    return names


def ollama_models(config):
    with request(local_url(config.get("ollama", {}).get("url", ""), "/api/tags")) as response:
        return model_names(read_json(response), "ollama")


def consume_response(response, timeout=40):
    """Accept only a text delta and an explicit completed event; discard text."""
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        raise UserError("Responses 未返回 SSE 流。")
    deadline, total, text_seen, completed = time.monotonic() + timeout, 0, False, False
    data, event_name = [], ""

    def event():
        nonlocal text_seen, completed
        if not data:
            return
        value = json.loads("\n".join(data))
        kind = value.get("type", event_name) if isinstance(value, dict) else ""
        if kind == "response.output_text.delta" and isinstance(value.get("delta"), str) and value["delta"]:
            text_seen = True
        if kind == "response.completed":
            completed = True
        if kind in ("error", "response.failed", "response.incomplete"):
            raise UserError("Responses 返回失败或未完成事件。")

    buffer = b""
    read = getattr(response, "read1", response.read)
    while time.monotonic() < deadline and not completed:
        chunk = read(4096)
        total += len(chunk)
        if total > MAX_JSON:
            raise UserError("Responses 超过诊断流大小限制。")
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if len(line) > 65536:
                raise UserError("Responses 事件行超过大小限制。")
            value = line.decode("utf-8").rstrip("\r")
            if not value:
                event()
                data, event_name = [], ""
                if completed:
                    break
            elif value.startswith("data:"):
                data.append(value[5:].lstrip(" "))
            elif value.startswith("event:"):
                event_name = value[6:].strip()
        if len(buffer) > 65536:
            raise UserError("Responses 事件行超过大小限制。")
    if not (text_seen and completed):
        raise UserError("Responses 未收到文本增量和完整结束事件；未重试。")


# The original line is never returned, even if it contains a token or address.
EVENTS = (
    (re.compile(r"custom listener for \[sub2api_ke\] doesn't exist", re.I), "provider", "error", "Windows provider 尚未注册"),
    (re.compile(r"login to server success", re.I), "frps", "ok", "frps 登录成功"),
    (re.compile(r"start visitor success|visitor.*started successfully", re.I), "visitor", "ok", "STCP visitor 已启动"),
    (re.compile(r"login to the server failed|connect to server error", re.I), "frps", "error", "frps 连接或认证失败"),
    (re.compile(r"connection closed|reconnect to server|try to connect to server", re.I), "frps", "stale", "隧道正在连接或重连"),
    (re.compile(r"operation failed", re.I), "operation", "error", "本机操作未完成"),
    (re.compile(r"local connection shutdown incomplete", re.I), "shutdown", "error", "本机连接停止未完成"),
    (re.compile(r"gateway.*(?:started|listening)|listening.*gateway", re.I), "gateway", "ok", "本机模型网关已监听"),
    (re.compile(r"connection refused|connection reset|timed out", re.I), "connection", "error", "连接失败或超时"),
)


class Diagnostics:
    def __init__(self, controller):
        self.controller = controller
        self.records = {service: {} for service in SERVICES}
        self.events = {service: [] for service in SERVICES}
        self.check_lock = threading.Lock()
        self.baselines = {}
        for component in SERVICES:
            for path in self.paths(component):
                self.baselines[path] = self.stat(path)

    @staticmethod
    def stat(path):
        try:
            value = path.stat()
            return value.st_ino, value.st_size
        except OSError:
            return None, 0

    def paths(self, component):
        root = self.controller.runtime / "logs"
        if component == "sub2api":
            return [root / "frpc.log", root / "worker.log"]
        if component == "console":
            return [root / "console.log"]
        directory = self.controller.config.get("ollama", {}).get("log_directory")
        return sorted(Path(directory).glob("*.log"))[:8] if directory and Path(directory).is_dir() else []

    def log_events(self, component):
        if component not in SERVICES:
            raise UserError("未知日志组件。")
        events = []
        for path in self.paths(component):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                inode, size = self.stat(path)
                old_inode, offset = self.baselines.get(path, (None, 0))
                if old_inode is not None and old_inode != inode:
                    # Rotation means only the newly created current file is read.
                    offset = 0
                if component == "sub2api" and path.name == "frpc.log":
                    receipt = self.controller.tunnel.local_state()
                    if not receipt.get("running"):
                        continue
                    offset = max(offset, int(receipt.get("log_offset", 0)))
                if size < offset:
                    continue
                with path.open("rb") as stream:
                    stream.seek(max(offset, size - 65536))
                    for line in stream.read(65536).decode("utf-8", "replace").splitlines():
                        for pattern, identity, state, detail in EVENTS:
                            if pattern.search(line):
                                events.append((identity, state, detail))
                                break
            except (OSError, ValueError, TypeError):
                continue
        return events[-100:]

    def logs(self, component):
        with self.controller.lock:
            entries = [item[2] for item in self.log_events(component)] + self.events[component]
            return {"component": component, "lines": entries[-100:]}

    @property
    def busy(self):
        return self.check_lock.locked()

    def record(self, service, identity, label, state, detail, **extra):
        with self.controller.lock:
            signature = extra.pop("signature", None)
            self.records[service][identity] = {
                "id": identity, "label": label, "state": state, "detail": detail,
                "checked_at": now(), "signature": signature if signature is not None else self.controller.connection_signature(service), **extra,
            }
            self.events[service].append(label + "：" + detail)
            self.events[service] = self.events[service][-100:]

    def snapshot(self, service):
        if service not in SERVICES:
            raise UserError("未知诊断服务。")
        with self.controller.lock:
            layers, models, model, checked_at = [], [], None, None
            if service == "console":
                layers.append({"id": "console", "label": "本机控制台", "state": "ok",
                               "detail": "本机后台正在运行"})
            else:
                signature = self.controller.connection_signature(service)
                if service == "sub2api":
                    cfg = self.controller.config.get(service, {})
                    layers.extend([
                        {"id": "listener", "label": "本机监听", "state": "ok" if signature[-1] else "unknown",
                         "detail": "本机端口正在监听" if signature[-1] else "未检测到本机监听"},
                        {"id": "worker", "label": "本机隧道", "state": "ok" if self.controller.tunnel.running else "unknown",
                         "detail": "控制台管理的 visitor 正在运行" if self.controller.tunnel.running else "未确认控制台管理的 visitor"},
                    ])
                    facts = {}
                    for identity, state, detail in self.log_events(service):
                        facts[identity] = (state, detail)
                    for identity, label in (("frps", "frps 连接"), ("provider", "Windows provider")):
                        state, detail = facts.get(identity, ("unknown", "尚无本次连接的确认事件"))
                        layers.append({"id": identity, "label": label, "state": state, "detail": detail})
                    model = cfg.get("model")
                health = self.controller.health_state(service)
                layers.append({"id": "health", "label": "API 健康", "state": health["state"] if health["state"] != "unchecked" else "unknown",
                               "detail": health["detail"]})
                checked_at = health.get("checked_at")
                for identity, record in self.records[service].items():
                    stale = record.get("stale", False) or record["signature"] != signature or age(record["checked_at"]) > TTL
                    layer = {key: record[key] for key in ("id", "label", "state", "detail")}
                    if stale:
                        record["stale"] = True
                        layer.update(state="stale", detail="连接已变化或验收超过五分钟，请重新检查")
                    else:
                        models = record.get("models", models)
                        model = record.get("model", model)
                    layers.append(layer)
                    checked_at = max(filter(None, (checked_at, record["checked_at"])), default=None)
            return {"service": service, "checked_at": checked_at, "layers": layers,
                    "models": models, "model": model}

    def run(self, service, level, model=None):
        if service not in ("ollama", "sub2api") or level not in ("health", "auth", "response"):
            raise UserError("不支持的诊断操作。")
        if service != "sub2api" and level != "health":
            raise UserError("此服务仅支持显式健康检查。")
        if not self.check_lock.acquire(blocking=False):
            raise UserError("已有诊断正在执行，请等待完成；未重复请求。")
        try:
            return self._run(service, level, model)
        finally:
            self.check_lock.release()

    def _run(self, service, level, model):
        with self.controller.lock:
            if self.controller.closing:
                raise UserError("正在全部退出，无法执行诊断。")
            signature = self.controller.connection_signature(service)
            settings = self.controller.config.get("sub2api", {}).copy()
        self.controller.check_health(service)
        with self.controller.lock:
            health_ok = self.controller.health_state(service)["state"] == "ok"
            if level != "health":
                self.records[service].clear()
        if level == "health" or not health_ok:
            return self.snapshot(service)
        identity, label = "auth", "下游 Key 认证"
        try:
            base = settings.get("base_url", "")
            models_url = local_url(base, "/models")
            try:
                with request(models_url) as response:
                    response.read(1)
                self.record(service, identity, label, "error", "无 Key 请求未被拒绝，已停止认证验收", signature=signature)
                return self.snapshot(service)
            except urllib.error.HTTPError as error:
                if error.code not in (401, 403):
                    raise UserError("无 Key 检查未返回认证拒绝状态。") from None
            key_file = settings.get("key_file")
            if not key_file or not Path(key_file).is_file() or Path(key_file).stat().st_size > 65536:
                raise UserError("本机下游 Key 文件缺失或大小异常。")
            key = private_key(key_file)
            with request(models_url, key=key) as response:
                models = model_names(read_json(response), "sub2api")
            if not models:
                raise UserError("网关当前没有返回可用模型。")
            self.record(service, identity, label, "ok", "无 Key 被拒绝；本机下游 Key 模型列表成功", models=models, signature=signature)
            if level == "response":
                identity, label = "response", "Responses 流式请求"
                if not isinstance(model, str) or model not in models:
                    raise UserError("请选择网关本次实际返回的精确模型名称。")
                with self.controller.lock:
                    if self.controller.closing or self.controller.connection_signature(service) != signature:
                        raise UserError("连接已变化，已取消本次推理请求。")
                payload = {"model": model, "input": "Reply OK.", "stream": True, "max_output_tokens": 64}
                with request(local_url(base, "/responses"), key=key, payload=payload) as response:
                    consume_response(response)
                self.record(service, identity, label, "ok", "已收到文本增量和 response.completed；未保存回复正文", model=model, signature=signature)
        except Exception as error:
            code = getattr(error, "code", None)
            # Only our own fixed UserError strings or numeric HTTP statuses.
            detail = str(error) if isinstance(error, UserError) else (f"HTTP {code}；未重试" if isinstance(code, int) else "请求未完成或响应格式无效；未重试")
            self.record(service, identity, label, "error", detail, signature=signature)
        return self.snapshot(service)
