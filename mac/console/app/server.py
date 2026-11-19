from __future__ import annotations

import concurrent.futures
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import copy
import signal
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request

from . import VERSION
from .configuration import CodexConfig
from .model_routes import switch_route
from .core import FileLock, Tunnel, UserError, atomic_write, opener, port_open, port_listening, probe, write_json
from .releases import REPOSITORY, UpdateError, discover
from .diagnostics import Diagnostics, TTL, age, local_url, now, ollama_models
from .route_profiles import list_routes, manage_route, settings_lock_path

WEB = Path(__file__).with_name("web")


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback-only panel does not need reverse DNS. HTTPServer otherwise
        # calls getfqdn here, blocking startup on hosts with slow DNS resolution.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


class ChatSession:
    def __init__(self, identity):
        self.identity = identity
        self.cancelled = threading.Event()
        self.complete = threading.Event()
        self.upstream = None
        self.transport = None

    def interrupt(self):
        self.cancelled.set()
        if self.transport is not None and self.transport.sock is not None:
            try:
                self.transport.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.upstream is not None:
            try:
                self.upstream.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass


class Controller:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.runtime = Path(self.config["runtime"]).resolve()
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tunnel = Tunnel(self.config.get("sub2api", {}), self.runtime)
        self.sub2api_check = {"state": "unchecked", "detail": "尚未检查远端 API"}
        self.ollama_check = {"state": "unchecked", "detail": "尚未检查远端 API"}
        self.health_signatures = {}
        self.active_chats = 0
        self.chat_sessions = {}
        self.cancelled_chat_ids = {}
        self.closing = False
        self.connections_stopped = False
        self.codex = CodexConfig(self.config.get("codex_config", str(Path.home() / ".codex/config.toml")),
                                 self.runtime, self.config.get("sub2api", {}))
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.diagnostics = Diagnostics(self)

    def ollama_running(self):
        o = self.config.get("ollama", {})
        if sys.platform == "darwin" and o.get("launchd_label"):
            r = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/" + o["launchd_label"]],
                               capture_output=True, timeout=3)
            return r.returncode == 0
        return port_listening(urllib.parse.urlsplit(o.get("url", "http://127.0.0.1:11434")).port or 11434)

    def connection_signature(self, service):
        if service == "sub2api":
            settings = self.config.get("sub2api", {})
            listener = (settings.get("mode") == "direct" or
                        port_listening(settings.get("port", 18081)))
            return (self.config.get("selected_route"), self.tunnel.connection_generation,
                    bool(listener))
        settings = self.config.get("ollama", {})
        port = urllib.parse.urlsplit(settings.get("url", "http://127.0.0.1:11435")).port or 11435
        return (self.config.get("selected_model_route"), bool(port_listening(port)))

    def health_state(self, service):
        with self.lock:
            return self._health_state(service)

    def _health_state(self, service):
        value = (self.sub2api_check if service == "sub2api" else self.ollama_check).copy()
        value.setdefault("checked_at", None)
        value.update(ttl_seconds=TTL, stale=False)
        if value["checked_at"]:
            stale = (value["state"] == "stale" or
                     self.health_signatures.get(service) != self.connection_signature(service) or
                     age(value["checked_at"]) > TTL)
            if stale:
                value.update(state="stale", stale=True, detail="连接已变化或检查超过五分钟，请重新检查")
                target = self.sub2api_check if service == "sub2api" else self.ollama_check
                target.update(state="stale", detail=value["detail"])
        return value

    def check_health(self, service):
        with self.lock:
            settings = self.config.get(service, {})
            url = settings.get("health_url") if service == "sub2api" else settings.get("url", "").rstrip("/") + "/api/version"
            if not url:
                raise UserError("尚未配置健康检查地址。")
            local_url(url, "")
            before = self.connection_signature(service)
        checked = probe(url)
        value = {"state": "ok" if checked["ok"] else "error", "detail": checked["detail"],
                 "latency_ms": checked.get("latency_ms"), "checked_at": now()}
        with self.lock:
            if service == "sub2api":
                self.sub2api_check = value
            else:
                self.ollama_check = value
            self.health_signatures[service] = before
        return value

    def preferences(self):
        with self.lock:
            try:
                path = self.runtime / "data/chat-preferences.json"
                value = json.loads(path.read_text(encoding="utf-8")) if path.stat().st_size <= 4096 else {}
                model = value.get("default_model")
                return {"default_model": model if isinstance(model, str) else None}
            except (OSError, ValueError, AttributeError):
                return {"default_model": None}

    def models(self):
        with self.lock:
            config = {"ollama": self.config.get("ollama", {}).copy()}
            signature = self.connection_signature("ollama")
        models = ollama_models(config)
        with self.lock:
            if signature != self.connection_signature("ollama"):
                raise UserError("模型连接已变化，请重新读取模型列表。")
            default = self.preferences()["default_model"]
            return {"models": models, "default_model": default if default in models else None}

    def save_preferences(self, default_model):
        with self.lock:
            if self.closing:
                raise UserError("正在全部退出，无法保存偏好。")
            config = {"ollama": self.config.get("ollama", {}).copy()}
            signature = self.connection_signature("ollama")
        if not isinstance(default_model, str) or default_model not in ollama_models(config):
            raise UserError("请选择当前模型接口实际返回的精确模型名称。")
        with self.lock:
            if self.closing or signature != self.connection_signature("ollama"):
                raise UserError("模型连接已变化，未保存偏好，请重新读取模型列表。")
            path = self.runtime / "data/chat-preferences.json"
            with FileLock(path.with_suffix(".lock")):
                write_json(path, {"default_model": default_model})
            return {"default_model": default_model}

    def begin_chat(self, request_id=None):
        with self.lock:
            if self.closing:
                raise UserError("正在全部退出，已停止接收新聊天请求。")
            if request_id is None:
                request_id = secrets.token_hex(24)
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
                raise UserError("聊天请求标识无效。")
            if request_id in self.chat_sessions:
                raise UserError("该聊天请求正在执行，未重复发送。")
            cutoff = time.monotonic() - 60
            self.cancelled_chat_ids = {key: stamp for key, stamp in self.cancelled_chat_ids.items() if stamp >= cutoff}
            if request_id in self.cancelled_chat_ids:
                raise UserError("该聊天请求已被取消，未发送到模型。")
            url = local_url(self.config.get("ollama", {}).get("url", ""), "/api/chat")
            session = ChatSession(request_id)
            self.chat_sessions[request_id] = session
            self.active_chats = len(self.chat_sessions)
            return url, session

    def end_chat(self, session):
        with self.lock:
            if self.chat_sessions.get(session.identity) is session:
                self.chat_sessions.pop(session.identity)
            self.active_chats = len(self.chat_sessions)

    def cancel_chat(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
            raise UserError("聊天请求标识无效。")
        with self.lock:
            self.cancelled_chat_ids[request_id] = time.monotonic()
            while len(self.cancelled_chat_ids) > 256:
                self.cancelled_chat_ids.pop(next(iter(self.cancelled_chat_ids)))
            session = self.chat_sessions.get(request_id)
            if session is not None:
                self.end_chat(session)
            result = {"cancelled": True, "active_chats": self.active_chats}
        if session is not None:
            session.interrupt()
        return result

    def status(self, passive=False):
        with self.lock:
            automatic = not passive and self.config.get("automatic_health_checks", True)
        if automatic:
            # Compatibility for older callers only. Native/web callers use the
            # declared passive endpoint; even legacy I/O never holds our lock.
            self.check_health("ollama")
        with self.lock:
            return self._status(passive)

    def _status(self, passive=False):
        o, s = self.config.get("ollama", {}), self.config.get("sub2api", {})
        legacy = self.config.get("include_legacy_integrations", False)
        health = self.health_state("ollama")
        checks = {"ollama": {"ok": health["state"] == "ok", "checked": bool(health["checked_at"]), **health}}
        active_provider = None
        try:
            if legacy:
                active_provider = tomllib.loads(self.codex.path.read_text()).get("model_provider", "openai")
        except Exception:
            pass
        result = {"version": VERSION, "platform": sys.platform,
                "role": self.config.get("role", "mac-receiver"),
                "active_chats": self.active_chats, "passive": bool(passive),
                "model_routes": [{"id": k, "name": v.get("name", k)}
                                 for k, v in self.config.get("ollama_routes", {}).items()],
                "selected_model_route": self.config.get("selected_model_route"),
                "ollama": {"running": self.ollama_running(), "managed": bool(o.get("manager")),
                           "url": o.get("url"), **checks["ollama"]},
                "routes": [{"id": k, "name": v.get("display_name", k)}
                           for k, v in self.config.get("sub2api_profiles", {}).items()],
                "selected_route": self.config.get("selected_route"),
                "codex_provider": active_provider,
                "codex_plan": (self.runtime / "data/codex-plan.json").is_file(),
                "github_configured": bool(self.config.get("github_repository")),
                "apps": {"ccswitch": bool(self.config.get("apps", {}).get("ccswitch") and
                                           Path(self.config["apps"]["ccswitch"]).exists())},
                "notice": "Windows 负责推理；Mac 只管理连接。接口在线不等于模型已验收。"}

        if not legacy:
            for key in ("codex_provider", "codex_plan"):
                result.pop(key, None)
        if s:
            # A listener is a local fact, not proof of remote provider availability.
            # Health is checked only by the explicit Sub2API/check action.
            listener = s.get("mode") == "stcp" and port_listening(s.get("port", 18081))
            running = self.tunnel.running
            result["sub2api"] = {
                "configured": s.get("mode") in ("stcp", "direct"),
                "running": running, "listener": bool(listener),
                "ownership": "owned" if running else "external" if listener else "stopped",
                "mode": s.get("mode", "unconfigured"), "url": s.get("base_url"),
                "model": s.get("model"),
                "key_available": bool(s.get("key_file") and Path(s["key_file"]).is_file()),
                "remote": self.health_state("sub2api"),
            }
        return result

    def action(self, service, action):
        if service == "sub2api" and action == "check":
            self.diagnostics.run("sub2api", "health")
            with self.lock:
                return ("Sub2API 健康检查通过；认证、Responses 和 Codex 对话仍需分别验收。"
                        if self.health_state("sub2api")["state"] == "ok" else
                        "Sub2API API 尚不可达，请确认 Windows 服务与 provider；本地监听不代表远端在线。")
        with self.lock, (FileLock(settings_lock_path(self)) if service in ("route", "model-route")
                         else contextlib.nullcontext()):
            if self.closing and action not in ("stop",):
                raise UserError("正在全部退出，请等待连接停止。")
            if (self.active_chats and not self.closing and
                    (service in ("model-route", "route") or (service == "ollama" and action == "stop"))):
                raise UserError("网页聊天正在生成，请结束后再切换线路或停止 Ollama；全部退出仍可停止连接。")
            if (self.diagnostics.busy and not self.closing and
                    (service in ("model-route", "route") or
                     (service in ("sub2api", "ollama") and action in ("start", "stop")))):
                raise UserError("连接正在执行显式诊断，请完成后再切换或停止；全部退出仍可停止连接。")
            if service == "model-route":
                return switch_route(self, action)
            if service == "route":
                profiles = self.config.get("sub2api_profiles", {})
                if action not in profiles:
                    raise UserError("未找到该连接配置。")
                if action == self.config.get("selected_route"):
                    return "已使用该连接配置。"
                # Do not overwrite a concurrent edit in another console/editor.
                original = self.config_path.read_bytes()
                if json.loads(original) != self.config:
                    raise UserError("连接设置已在其他窗口修改，请重新打开控制台后再切换。")
                current = self.config.get("sub2api", {})
                if (current.get("mode") == "stcp" and not self.tunnel.running and
                        port_listening(current.get("port", 18081))):
                    raise UserError("端口由外部连接占用，请先在原入口停止连接；未接管或切换。")
                candidate = copy.deepcopy(self.config)
                candidate["selected_route"] = action
                candidate["sub2api"] = profiles[action].copy()
                backup = self.runtime / "backups" / ("sub2api-route-" + str(time.time_ns()) + ".json")
                atomic_write(backup, original)
                self.tunnel.stop()
                if self.config_path.read_bytes() != original:
                    raise UserError("连接设置已更新，保留其他窗口修改；原隧道已停止，请重新打开控制台。")
                write_json(self.config_path, candidate)
                self.config = candidate
                self.tunnel = Tunnel(self.config["sub2api"], self.runtime)
                self.codex.settings = self.config["sub2api"]
                self.sub2api_check = {"state": "unchecked", "detail": "尚未检查远端 API"}
                return "已选择 Sub2API 连接，请按需开启；再在 CC Switch 选择对应供应商。"
            if service == "sub2api":
                if action in ("start", "stop"):
                    message = self.tunnel.start() if action == "start" else self.tunnel.stop()
                    self.sub2api_check = {"state": "unchecked", "detail": "尚未检查远端 API"}
                    return message
            if service == "ollama" and action in ("start", "stop"):
                cfg = self.config.get("ollama", {})
                if not cfg.get("manager"):
                    raise UserError("本机未接入该平台的 Ollama 管理适配器。")
                r = subprocess.run([cfg["python"], cfg["manager"], "resume" if action == "start" else "pause"],
                                   capture_output=True, timeout=35)
                if r.returncode:
                    raise UserError("Ollama 连接管理失败，请查看原项目日志。")
                self.ollama_check = {"state": "unchecked", "detail": "连接已变化，请按需检查远端 API"}
                return "Ollama 连接已" + ("启动。" if action == "start" else "暂停（含原服务登录启动）。")
            if service == "ollama" and action == "logs":
                directory = self.config.get("ollama", {}).get("log_directory")
                if not directory or not Path(directory).is_dir() or sys.platform != "darwin":
                    raise UserError("请在本机查看原模型连接项目的 logs 目录。")
                subprocess.run(["/usr/bin/open", directory], check=True, timeout=5)
                return "已打开本机日志目录。"
            if service == "codex":
                raise UserError("请通过 CC Switch 管理 Codex 供应商和认证。")
            if service == "app" and action in ("cherry", "codex", "tailscale", "ccswitch"):
                target = self.config.get("apps", {}).get(action)
                if not target or not Path(target).exists():
                    raise UserError("未找到该应用，请先在本机配置应用路径。")
                if sys.platform == "darwin":
                    subprocess.run(["/usr/bin/open", "-a", target], check=True, timeout=5)
                elif os.name == "nt":
                    subprocess.Popen([target], close_fds=True)
                else:
                    raise UserError("当前平台尚未配置应用启动器。")
                return ("已打开 CC Switch，请在 Codex 分类选择 Windows Sub2API；切换后重启 Codex。"
                        if action == "ccswitch" else "已打开应用；新提供商的 GUI 聊天仍需实际验证。")
            if service == "update" and action == "check":
                if self.config.get("github_repository", REPOSITORY) != REPOSITORY:
                    raise UserError("请将更新源设为本项目官方仓库，未访问其他仓库。")
                try:
                    result = discover(self.config.get("update_channel", "preview"), VERSION)
                except UpdateError as error:
                    raise UserError(str(error)) from None
                return f"已安装 {VERSION}；" + result["message"]
            raise UserError("不支持的操作。")

    def shutdown_connections(self):
        """Stop every local connection configured for this console, then verify.

        The existing manager owns the Ollama gateway/visitor and its restart
        policy. Never use process-name killing or modify a remote Windows host.
        """
        with self.lock:
            if self.connections_stopped:
                return
            self.closing = True
            for session in list(self.chat_sessions.values()):
                session.interrupt()
            self.chat_sessions.clear()
            self.active_chats = 0
            errors = []
            ollama = self.config.get("ollama", {})
            if ollama.get("manager"):
                try:
                    self.action("ollama", "stop")
                except Exception:
                    errors.append("Ollama 连接尚未停止")
            try:
                self.tunnel.stop()
            except Exception:
                errors.append("Sub2API 连接尚未停止")
            try:
                ports = set()
                if ollama.get("manager"):
                    url = urllib.parse.urlsplit(ollama.get("url", ""))
                    if url.hostname in ("127.0.0.1", "localhost", "::1") and url.port:
                        ports.add(url.port)
                    else:
                        errors.append("无法确认本机 Ollama 接口端口")
                    visitor = ollama.get("visitor_config")
                    if visitor and Path(visitor).is_file():
                        data = tomllib.loads(Path(visitor).read_text(encoding="utf-8-sig"))
                        ports.update(v["bindPort"] for v in data.get("visitors", [])
                                     if v.get("bindAddr") == "127.0.0.1" and
                                     isinstance(v.get("bindPort"), int))
                    else:
                        errors.append("无法读取 Ollama 本机 visitor 配置")
                    if (sys.platform == "darwin" and ollama.get("launchd_label") and
                            self.ollama_running()):
                        errors.append("Ollama 后台仍在运行")
                sub2api = self.config.get("sub2api", {})
                if sub2api.get("mode") == "stcp":
                    ports.add(sub2api.get("port", 18081))
                deadline = time.monotonic() + 5
                while any(port_listening(port) for port in ports):
                    if time.monotonic() >= deadline:
                        errors.append("本地连接端口尚未关闭")
                        break
                    time.sleep(0.1)
            except Exception:
                errors.append("无法确认所有本地连接已停止")
            if errors:
                self.closing = False
                raise UserError("；".join(errors) + "。后台保留，请重试全部退出。")
            self.connections_stopped = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # No URLs, keys, chat content or arbitrary HTTP input in logs.

    def trusted_host(self):
        return self.headers.get("Host") == self.server.authority

    def allowed_origin(self):
        origin = self.headers.get("Origin")
        return origin in (None, "http://" + self.server.authority)

    def send(self, code, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.trusted_host() or not self.allowed_origin():
            self.send(403, {"error": "本机来源校验失败"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if route == "/api/session":
                self.send(200, {"service": "local-ai-console", "version": VERSION, "csrf": self.server.token,
                                "passive_status": True,
                                "instance": getattr(self.server, "instance", None)})
            elif route == "/api/status":
                data = self.server.controller.status(passive=query.get("passive") == ["1"])
                data["instance"] = getattr(self.server, "instance", None)
                self.send(200, data)
            elif route == "/api/diagnostics":
                self.send(200, self.server.controller.diagnostics.snapshot(query.get("service", ["console"])[0]))
            elif route == "/api/logs":
                self.send(200, self.server.controller.diagnostics.logs(query.get("component", ["console"])[0]))
            elif route == "/api/preferences":
                self.send(200, self.server.controller.preferences())
            elif route == "/api/routes":
                self.send(200, list_routes(self.server.controller, query.get("service", [""])[0]))
            elif route == "/api/models":
                self.send(200, self.server.controller.models())
            else:
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/chat": ("chat.html", "text/html; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/console.js": ("console.js", "text/javascript; charset=utf-8"),
                          "/chat.js": ("chat.js", "text/javascript; charset=utf-8")}
                if route not in assets:
                    self.send(404, {"error": "未找到"})
                    return
                name, kind = assets[route]
                self.send(200, (WEB / name).read_bytes(), kind)
        except UserError as error:
            self.send(409, {"error": str(error)})
        except Exception:
            self.send(503, {"error": "接口暂不可用，请确认 Windows 和所选隧道服务已启动。"})

    def do_POST(self):
        if (not self.trusted_host() or not self.allowed_origin() or
                not secrets.compare_digest(self.headers.get("X-Local-CSRF", ""), self.server.token)):
            self.close_connection = True
            self.send(403, {"error": "本机操作校验失败"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 256 * 1024:
                raise UserError("请求大小不符合限制。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise UserError("请求格式不符合限制。")
            if self.path == "/api/chat":
                self.chat(data)
                return
            if self.path == "/api/chat/cancel":
                self.send(200, self.server.controller.cancel_chat(data.get("request_id")))
                return
            if self.path == "/api/shutdown":
                self.server.controller.shutdown_connections()
                self.server.full_quit_requested = True
                try:
                    self.send(200, {"message": "本机 Ollama、Sub2API 连接均已停止，正在退出控制台。",
                                    "shutdown_complete": True})
                finally:
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path == "/api/diagnostics":
                self.send(200, self.server.controller.diagnostics.run(
                    data.get("service"), data.get("level"), data.get("model")))
                return
            if self.path == "/api/preferences":
                self.send(200, self.server.controller.save_preferences(data.get("default_model")))
                return
            if self.path == "/api/routes":
                self.send(200, manage_route(self.server.controller, data))
                return
            if self.path != "/api/action":
                raise UserError("不支持的操作。")
            message = self.server.controller.action(data.get("service"), data.get("action"))
            self.send(200, {"message": message})
        except UserError as e:
            self.send(409, {"error": str(e)})
        except Exception as e:
            logging.getLogger("local-ai-console").warning("operation failed: %s", type(e).__name__)
            self.send(503, {"error": "操作未完成，请检查配置和服务状态；原有登录及会话保持独立。"})

    def chat(self, data):
        model, messages = data.get("model"), data.get("messages")
        if not isinstance(model, str) or not model or not isinstance(messages, list) or not messages:
            raise UserError("请选择模型并输入消息。")
        if len(messages) > 12:
            raise UserError("网页聊天最多发送最近 12 条消息，请开始新对话。")
        for m in messages:
            if (not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or
                    not isinstance(m.get("content"), str) or len(m["content"]) > 16000):
                raise UserError("消息格式或长度不符合限制。")
        url, session = self.server.controller.begin_chat(data.get("request_id"))
        self.close_connection = True
        self.chat_session = session
        self.chat_cancelled = session.cancelled
        self.chat_complete = session.complete
        lease_lock = threading.Lock()
        released = False

        def release():
            nonlocal released
            with lease_lock:
                if not released:
                    released = True
                    self.server.controller.end_chat(session)

        def disconnected():
            # Observe only this handler's socket. A browser AbortController
            # closes it even while the model is still silent.
            while not self.chat_complete.wait(0.2):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if not readable or self.connection.recv(1, socket.MSG_PEEK):
                        continue
                except OSError:
                    pass
                session.interrupt()
                release()
                return

        watcher = threading.Thread(target=disconnected, daemon=True)
        watcher.start()
        try:
            self.stream_chat(model, messages, url)
        finally:
            self.chat_complete.set()
            release()

    def stream_chat(self, model, messages, url):
        # Only the configured local Ollama path is allowed; no arbitrary proxy.
        caps = {"qwen3-8b-4k:latest": 1024, "qwen3-8b-8k:latest": 2048,
                "qwen3-8b-16k:latest": 4096}
        payload = {"model": model, "messages": messages, "stream": True}
        if model in caps:
            payload["options"] = {"num_predict": caps[model]}
        endpoint = urllib.parse.urlsplit(url)
        # Keep the exact owned socket reachable even before response headers,
        # so a silent model can be cancelled without waiting for its timeout.
        transport = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=180)
        self.chat_session.transport = transport
        try:
            if self.chat_cancelled.is_set():
                transport.close()
                return
            transport.request("POST", endpoint.path or "/", body=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
            if self.chat_cancelled.is_set():
                transport.close()
                return
            upstream = transport.getresponse()
            if upstream.status != 200:
                raise UserError("模型接口没有接受请求。")
        except Exception:
            transport.close()
            if self.chat_cancelled.is_set():
                return
            self.send(503, {"error": "模型未能开始回答，请检查 Windows 推理服务与连接；未自动重试。"})
            return
        self.chat_session.upstream = upstream
        if self.chat_cancelled.is_set():
            upstream.close()
            transport.close()
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
        except OSError:
            upstream.close()
            transport.close()
            return
        self.close_connection = True
        done = False
        try:
            with upstream:
                while not self.chat_cancelled.is_set():
                    line = upstream.readline(1024 * 1024 + 1)
                    if not line:
                        break
                    # Bound and validate each event before rendering client-side.
                    if len(line) > 1024 * 1024:
                        break
                    event = json.loads(line)
                    self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
                    if event.get("done"):
                        done = True
                        break
            if not done:
                self.wfile.write(b'{"error":"Stream interrupted; request was not replayed."}\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self.wfile.write(b'{"error":"Stream failed; request was not replayed."}\n')
                self.wfile.flush()
            except OSError:
                pass
        finally:
            transport.close()


def serve(config_path, owner_pid=None):
    os.umask(0o077)
    controller = Controller(config_path)
    lock = FileLock(controller.runtime / "data/console.lock")
    port = controller.config.get("port", 18765)
    server = LocalHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.controller = controller
    server.authority = f"127.0.0.1:{port}"
    server.token = secrets.token_urlsafe(32)
    server.instance = secrets.token_hex(16)
    server.full_quit_requested = False
    # A previous instance's completion is never an exit receipt for this one.
    (controller.runtime / "data/shutdown.json").unlink(missing_ok=True)
    logs = controller.runtime / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    logger = logging.getLogger("local-ai-console")
    logger.setLevel(logging.INFO)
    logger.addHandler(RotatingFileHandler(logs / "console.log", maxBytes=1024 * 1024, backupCount=2))
    write_json(controller.runtime / "data/server.json", {"pid": os.getpid(), "url": "http://" + server.authority,
                                                        "instance": server.instance})

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    owner_done = threading.Event()
    if owner_pid is not None:
        def watch_owner():
            # A crashed/force-quit menu must not orphan its console and visitor.
            # PPID also protects against PID reuse; no unrelated process is killed.
            while not owner_done.wait(0.5):
                if os.getppid() != owner_pid:
                    server.shutdown()
                    return
        threading.Thread(target=watch_owner, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        owner_done.set()
        cleanup_complete = False
        try:
            controller.shutdown_connections()
            cleanup_complete = True
        except Exception:
            logger.warning("local connection shutdown incomplete")
        controller.pool.shutdown(wait=False, cancel_futures=True)
        server.server_close()
        if cleanup_complete:
            write_json(controller.runtime / "data/shutdown.json", {
                "instance": server.instance, "complete": True,
                "full_quit_requested": server.full_quit_requested,
            })
        lock.close()
