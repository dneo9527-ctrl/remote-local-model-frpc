from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class UserError(Exception):
    """A message safe to show in the local console."""


class FileLock:
    """An OS-held lock, released on crash; a PID file alone is not a lock."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise UserError("已有本项目实例在运行，请复用现有窗口。") from None

    def close(self):
        if not self.file.closed:
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def write_json(path: Path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def opener():
    # Local calls must never be rerouted by HTTP_PROXY/HTTPS_PROXY.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url, timeout=10):
    started = time.monotonic()
    try:
        with opener().open(url, timeout=timeout) as response:
            data = json.loads(response.read(2 * 1024 * 1024))
        ok = isinstance(data, dict) and (data.get("status") == "ok" or
                                        isinstance(data.get("version"), str))
        return {"ok": ok, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": "API 已响应" if ok else "API 响应格式不匹配"}
    except Exception as e:
        # Exception strings may contain an address, credentials or remote bodies.
        code = getattr(e, "code", None)
        return {"ok": False, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": f"HTTP {code}" if code else type(e).__name__}


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def private_key(path):
    key = Path(path).read_text(encoding="utf-8-sig").strip()
    if not key or "\n" in key or "\r" in key:
        raise UserError("下游 API key 文件为空或格式无效，请在本机配置。")
    return key


def validate_visitor(config, port):
    import tomllib
    d = tomllib.loads(Path(config).read_text(encoding="utf-8-sig"))
    visitors = d.get("visitors", [])
    if (len(visitors) != 1 or visitors[0].get("type") != "stcp" or
            visitors[0].get("bindAddr") != "127.0.0.1" or
            visitors[0].get("bindPort") != port or d.get("proxies")):
        raise UserError("访客配置必须只包含本服务的 STCP、127.0.0.1 与约定端口。")
    return d


class Tunnel:
    """Owns its worker handle, never finds/kills other frpc by name or PID file."""
    def __init__(self, settings, runtime: Path):
        self.settings = settings
        self.runtime = runtime
        self.process = None

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.settings.get("mode") != "stcp":
            raise UserError("当前 Sub2API 使用直连地址，无需启动 frp。")
        if self.running:
            return "Sub2API 隧道已运行，复用现有进程。"
        cfg = self.settings.get("visitor_config")
        binary = self.settings.get("frpc")
        if not cfg or not binary or not Path(binary).is_file():
            raise UserError("请先导入本机 frpc 与受限的 STCP 配置。")
        validate_visitor(cfg, self.settings["port"])
        if port_open(self.settings["port"]):
            raise UserError("本地端口已被其他实例占用；不会接管或停止它。")
        checked = subprocess.run([binary, "verify", "-c", cfg], cwd=str(Path(cfg).parent),
                                 capture_output=True, timeout=10)
        if checked.returncode:
            raise UserError("frpc 配置校验失败，未启动；请在本机检查私有配置。")
        log = self.runtime / "logs/worker.log"
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with log.open("ab", buffering=0) as out:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("worker.py")),
                 "--parent", str(os.getpid()), "--binary", binary, "--config", cfg,
                 "--runtime", str(self.runtime)],
                stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                cwd=str(Path(cfg).parent), close_fds=True)
        return "已启动 Sub2API 隧道；远端可用性以 API 检查为准。"

    def stop(self):
        if self.running:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Do not force-kill the supervisor before it can clean its child.
                raise UserError("隧道正在退出，请稍后刷新；未操作其他进程。") from None
        return "Sub2API 隧道已关闭。Ollama 连接不受影响。"
