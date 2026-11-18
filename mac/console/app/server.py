from __future__ import annotations

import concurrent.futures
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

WEB = Path(__file__).with_name("web")


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback-only panel does not need reverse DNS. HTTPServer otherwise
        # calls getfqdn here, blocking startup on hosts with slow DNS resolution.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


class Controller:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.runtime = Path(self.config["runtime"]).resolve()
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tunnel = Tunnel(self.config.get("sub2api", {}), self.runtime)
        self.sub2api_check = {"state": "unchecked", "detail": "尚未检查远端 API"}
        self.codex = CodexConfig(self.config.get("codex_config", str(Path.home() / ".codex/config.toml")),
                                 self.runtime, self.config.get("sub2api", {}))
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def ollama_running(self):
        o = self.config.get("ollama", {})
        if sys.platform == "darwin" and o.get("launchd_label"):
            r = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/" + o["launchd_label"]],
                               capture_output=True, timeout=3)
            return r.returncode == 0
        return port_open(urllib.parse.urlsplit(o.get("url", "http://127.0.0.1:11434")).port or 11434)

    def status(self):
        o, s = self.config.get("ollama", {}), self.config.get("sub2api", {})
        legacy = self.config.get("include_legacy_integrations", False)
        jobs = {}
        if self.config.get("automatic_health_checks", True):
            jobs["ollama"] = self.pool.submit(probe, o.get("url", "http://127.0.0.1:11435") + "/api/version")
        checks = {k: f.result() for k, f in jobs.items()}
        checks.setdefault("ollama", {"ok": False, "checked": False, "detail": "尚未检查远端 API"})
        active_provider = None
        try:
            if legacy:
                active_provider = tomllib.loads(self.codex.path.read_text()).get("model_provider", "openai")
        except Exception:
            pass
        result = {"version": VERSION, "platform": sys.platform,
                "role": self.config.get("role", "mac-receiver"),
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
                "remote": self.sub2api_check.copy(),
            }
        return result

    def action(self, service, action):
        with self.lock:
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
                if action == "check":
                    settings = self.config.get("sub2api", {})
                    if not settings.get("health_url"):
                        raise UserError("尚未配置 Sub2API 健康检查地址。")
                    checked = probe(settings["health_url"])
                    self.sub2api_check = {
                        "state": "ok" if checked["ok"] else "error", "detail": checked["detail"],
                        "latency_ms": checked["latency_ms"],
                    }
                    return ("Sub2API 健康检查通过；认证、Responses 和 Codex 对话仍需分别验收。"
                            if checked["ok"] else "Sub2API API 尚不可达，请确认 Windows 服务与 provider；本地监听不代表远端在线。")
            if service == "ollama" and action in ("start", "stop"):
                cfg = self.config.get("ollama", {})
                if not cfg.get("manager"):
                    raise UserError("本机未接入该平台的 Ollama 管理适配器。")
                r = subprocess.run([cfg["python"], cfg["manager"], "resume" if action == "start" else "pause"],
                                   capture_output=True, timeout=35)
                if r.returncode:
                    raise UserError("Ollama 连接管理失败，请查看原项目日志。")
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
        route = urllib.parse.urlsplit(self.path).path
        try:
            if route == "/api/session":
                self.send(200, {"service": "local-ai-console", "version": VERSION, "csrf": self.server.token,
                                "instance": getattr(self.server, "instance", None)})
            elif route == "/api/status":
                self.send(200, self.server.controller.status())
            elif route == "/api/models":
                url = self.server.controller.config["ollama"]["url"] + "/api/tags"
                with opener().open(url, timeout=15) as r:
                    data = json.load(r)
                self.send(200, {"models": [m["name"] for m in data.get("models", [])]})
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
            if self.path == "/api/chat":
                self.chat(data)
                return
            if self.path == "/api/shutdown":
                self.send(200, {"message": "正在关闭控制台及其访客子进程；独立的 Windows 服务和 Ollama 保持原状态。"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
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
            if (m.get("role") not in ("user", "assistant") or
                    not isinstance(m.get("content"), str) or len(m["content"]) > 16000):
                raise UserError("消息格式或长度不符合限制。")
        # Only the configured local Ollama path is allowed; no arbitrary proxy.
        caps = {"qwen3-8b-4k:latest": 1024, "qwen3-8b-8k:latest": 2048,
                "qwen3-8b-16k:latest": 4096}
        payload = {"model": model, "messages": messages, "stream": True}
        if model in caps:
            payload["options"] = {"num_predict": caps[model]}
        req = urllib.request.Request(self.server.controller.config["ollama"]["url"] + "/api/chat",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            upstream = opener().open(req, timeout=180)
        except Exception:
            self.send(503, {"error": "模型未能开始回答，请检查 Windows 推理服务与连接；未自动重试。"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        done = False
        try:
            with upstream:
                for line in upstream:
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
        controller.tunnel.stop()
        controller.pool.shutdown(wait=False, cancel_futures=True)
        server.server_close()
        lock.close()
