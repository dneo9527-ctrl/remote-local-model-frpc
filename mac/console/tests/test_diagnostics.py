"""Ephemeral loopback fixtures only; no user's settings, services or upstreams."""
from http.server import BaseHTTPRequestHandler
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from app import core
from app.core import Tunnel, UserError
from app.diagnostics import consume_response, read_json
from app.server import Controller, Handler, LocalHTTPServer


KEY = "fixture-downstream-only-secret"


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value, kind="application/json"):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.calls.append((self.path, bool(self.headers.get("Authorization"))))
        if self.path in ("/health", "/api/version"):
            self.reply(200, {"status": "ok", "version": "fixture"})
        elif self.path == "/api/tags":
            self.reply(200, {"models": [{"name": "fixture-local:latest"}]})
        elif self.path == "/v1/models":
            if self.server.public_models or self.headers.get("Authorization") == "Bearer " + KEY:
                self.reply(200, {"data": [{"id": "fixture-response-model"}]})
            else:
                self.reply(401, {"error": "PRIVATE RAW ERROR " + KEY})
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/health")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.reply(404, {})

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.posts.append((self.path, value))
        if self.path == "/v1/responses":
            raw = b'data: {"type":"response.output_text.delta","delta":"PRIVATE MODEL TEXT"}\n\n'
            if self.server.complete:
                raw += b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            self.reply(200, raw, "text/event-stream")
        elif self.path == "/api/chat":
            self.server.chat_started.set()
            self.server.chat_finish.wait(timeout=3)
            try:
                self.reply(200, b'{"message":{"content":"fixture"},"done":true}\n', "application/x-ndjson")
            except OSError:
                pass  # A cancellation fixture intentionally closes this socket.
        else:
            self.reply(404, {})


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.up = LocalHTTPServer(("127.0.0.1", 0), Gateway)
        self.up.calls, self.up.posts = [], []
        self.up.complete, self.up.public_models = True, False
        self.up.chat_started, self.up.chat_finish = threading.Event(), threading.Event()
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.up.server_port}"
        self.key = self.root / "fixture.key"
        self.key.write_text(KEY)
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({
            "runtime": str(self.root / "runtime"), "automatic_health_checks": True,
            "codex_config": str(self.root / "unused-codex.toml"),
            "selected_route": "fixture", "selected_model_route": "fixture-model",
            "ollama": {"url": self.base, "manager": "/fixture/manager", "python": "/fixture/python"},
            "sub2api": {"mode": "stcp", "port": self.up.server_port,
                        "base_url": self.base + "/v1", "health_url": self.base + "/health",
                        "key_file": str(self.key)},
        }))
        self.controller = Controller(self.settings)
        self.http = LocalHTTPServer(("127.0.0.1", 0), Handler)
        self.http.controller = self.controller
        self.http.authority = f"127.0.0.1:{self.http.server_port}"
        self.http.token, self.http.instance = "fixture-csrf", "fixture-instance"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.up.chat_finish.set()
        self.http.shutdown(); self.http.server_close()
        self.up.shutdown(); self.up.server_close()
        self.controller.pool.shutdown(wait=True)
        self.temp.cleanup()

    def call(self, path, data=None, csrf=True):
        headers = {"Content-Type": "application/json"}
        if data is not None and csrf:
            headers["X-Local-CSRF"] = self.http.token
        request = urllib.request.Request("http://" + self.http.authority + path,
                                         None if data is None else json.dumps(data).encode(), headers)
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)

    def test_passive_snapshot_and_logs_never_contact_upstream(self):
        with patch("app.server.probe", side_effect=AssertionError("passive must not probe")):
            state = self.call("/api/status?passive=1")
            diagnostic = self.call("/api/diagnostics?service=sub2api")
            self.call("/api/logs?component=console")
            session = self.call("/api/session")
        self.assertEqual(self.up.calls, [])
        self.assertTrue(state["passive"])
        self.assertEqual(state["instance"], session["instance"])
        self.assertTrue(session["passive_status"])
        self.assertEqual(state["active_chats"], 0)
        self.assertIsNone(diagnostic["checked_at"])
        self.assertEqual(diagnostic["models"], [])

    def test_auth_then_responses_use_exact_endpoints_and_hide_private_content(self):
        result = self.call("/api/diagnostics", {"service": "sub2api", "level": "response", "model": "fixture-response-model"})
        layers = {item["id"]: item for item in result["layers"]}
        self.assertEqual(layers["auth"]["state"], "ok")
        self.assertEqual(layers["response"]["state"], "ok")
        self.assertEqual(result["models"], ["fixture-response-model"])
        self.assertEqual(self.up.calls, [("/health", False), ("/v1/models", False), ("/v1/models", True)])
        self.assertEqual(self.up.posts[0][0], "/v1/responses")
        self.assertTrue(self.up.posts[0][1]["stream"])
        rendered = json.dumps(result) + json.dumps(self.call("/api/logs?component=sub2api"))
        for private in (KEY, "PRIVATE MODEL TEXT", "PRIVATE RAW ERROR", str(self.key), self.base):
            self.assertNotIn(private, rendered)
        self.assertIsNotNone(result["checked_at"])

    def test_public_models_and_unknown_model_never_trigger_inference(self):
        self.up.public_models = True
        result = self.controller.diagnostics.run("sub2api", "auth")
        self.assertEqual(next(x for x in result["layers"] if x["id"] == "auth")["state"], "error")
        self.assertEqual(self.up.calls, [("/health", False), ("/v1/models", False)])
        self.up.public_models = False
        result = self.controller.diagnostics.run("sub2api", "response", "invented-model")
        self.assertEqual(next(x for x in result["layers"] if x["id"] == "response")["state"], "error")
        self.assertEqual(self.up.posts, [])

    def test_incomplete_stream_is_failure_and_never_replayed(self):
        self.up.complete = False
        result = self.controller.diagnostics.run("sub2api", "response", "fixture-response-model")
        self.assertEqual(next(x for x in result["layers"] if x["id"] == "response")["state"], "error")
        self.assertEqual(len(self.up.posts), 1)

    def test_result_becomes_permanently_stale_on_listener_loss_and_generation_change(self):
        self.controller.check_health("sub2api")
        self.assertEqual(self.controller.health_state("sub2api")["state"], "ok")
        with patch("app.server.port_listening", return_value=False):
            state = self.controller.health_state("sub2api")
        self.assertEqual(state["state"], "stale")
        self.assertTrue(state["stale"])
        self.assertEqual(self.controller.health_state("sub2api")["state"], "stale")
        self.controller.check_health("sub2api")
        self.controller.tunnel.generation = "another-worker-generation"
        self.assertEqual(self.controller.health_state("sub2api")["state"], "stale")

    def test_current_worker_child_receipt_invalidates_old_health(self):
        process = type("FixtureProcess", (), {"pid": 78901, "poll": lambda self: None})()
        self.controller.tunnel.process = process
        path = self.controller.runtime / "data/sub2api-worker.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"worker_pid": process.pid, "generation": "child-one", "running": True}))
        self.controller.check_health("sub2api")
        path.write_text(json.dumps({"worker_pid": process.pid, "generation": "child-two", "running": True}))
        self.assertEqual(self.controller.health_state("sub2api")["state"], "stale")
        self.controller.tunnel.process = None

    def test_log_export_filters_raw_lines_and_historical_success(self):
        path = self.controller.runtime / "logs/worker.log"
        path.parent.mkdir(parents=True)
        path.write_text("RAW PRIVATE " + KEY + "\nlogin to server success addr=" + self.base + " key=" + KEY + "\n")
        result = self.controller.diagnostics.logs("sub2api")
        self.assertEqual(result["lines"], ["frps 登录成功"])
        self.controller.diagnostics.baselines[path] = self.controller.diagnostics.stat(path)
        self.assertEqual(self.controller.diagnostics.logs("sub2api")["lines"], [])
        with path.open("a") as stream:
            stream.write("custom listener for [sub2api_ke] doesn't exist " + KEY + "\n")
        self.assertEqual(self.controller.diagnostics.logs("sub2api")["lines"], ["Windows provider 尚未注册"])

    def test_preferences_validate_live_names_and_preserve_config(self):
        original = self.settings.read_bytes()
        saved = self.call("/api/preferences", {"default_model": "fixture-local:latest"})
        self.assertEqual(saved, {"default_model": "fixture-local:latest"})
        self.assertEqual(self.call("/api/preferences"), saved)
        self.assertEqual(self.call("/api/models")["default_model"], "fixture-local:latest")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call("/api/preferences", {"default_model": "invented"})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertFalse((self.root / "unused-codex.toml").exists())

    def test_diagnostics_and_preferences_require_csrf(self):
        for endpoint, data in (("/api/diagnostics", {"service": "sub2api", "level": "auth"}),
                               ("/api/preferences", {"default_model": "fixture-local:latest"})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.call(endpoint, data, csrf=False)
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.up.calls, [])

    def test_active_stream_blocks_route_stop_and_releases_counter(self):
        result = []
        def chat():
            request = urllib.request.Request("http://" + self.http.authority + "/api/chat",
                json.dumps({"model": "fixture-local:latest", "messages": [{"role": "user", "content": "fixture"}]}).encode(),
                {"X-Local-CSRF": self.http.token, "Content-Type": "application/json"})
            with self.opener.open(request, timeout=5) as response:
                result.append(response.read())
        thread = threading.Thread(target=chat)
        thread.start()
        self.assertTrue(self.up.chat_started.wait(timeout=2))
        self.assertEqual(self.call("/api/status?passive=1")["active_chats"], 1)
        for service, action in (("model-route", "fixture"), ("route", "fixture"), ("ollama", "stop")):
            with self.assertRaisesRegex(UserError, "网页聊天"):
                self.controller.action(service, action)
        self.up.chat_finish.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result)
        self.assertEqual(self.controller.active_chats, 0)

    def test_diagnostic_io_does_not_hold_main_lock(self):
        entered, finish = threading.Event(), threading.Event()
        original = self.controller.check_health
        def delayed(service):
            entered.set()
            finish.wait(timeout=2)
            return original(service)
        with patch.object(self.controller, "check_health", side_effect=delayed):
            thread = threading.Thread(target=lambda: self.controller.diagnostics.run("sub2api", "health"))
            thread.start()
            self.assertTrue(entered.wait(timeout=1))
            self.assertTrue(self.controller.lock.acquire(timeout=0.2))
            self.controller.lock.release()
            with self.assertRaisesRegex(UserError, "已有诊断"):
                self.controller.diagnostics.run("sub2api", "health")
            finish.set(); thread.join(timeout=2)

    def test_cancel_only_releases_the_exact_chat_lease_and_blocks_duplicate_id(self):
        _, first = self.controller.begin_chat("fixture-chat-request-one")
        _, second = self.controller.begin_chat("fixture-chat-request-two")
        with self.assertRaises(UserError):
            self.controller.begin_chat("fixture-chat-request-one")
        self.assertEqual(self.call("/api/chat/cancel", {"request_id": "fixture-chat-request-one"}),
                         {"cancelled": True, "active_chats": 1})
        self.assertTrue(first.cancelled.is_set())
        self.assertFalse(second.cancelled.is_set())
        self.controller.end_chat(first)
        self.assertEqual(self.controller.active_chats, 1)
        self.controller.end_chat(second)
        self.assertEqual(self.controller.active_chats, 0)
        self.call("/api/chat/cancel", {"request_id": "fixture-cancel-before-register"})
        with self.assertRaisesRegex(UserError, "已被取消"):
            self.controller.begin_chat("fixture-cancel-before-register")

    def test_explicit_cancel_interrupts_silent_upstream_before_headers(self):
        finished = threading.Event()
        def chat():
            try:
                self.call("/api/chat", {"request_id": "fixture-silent-chat-request", "model": "fixture-local:latest",
                                         "messages": [{"role": "user", "content": "fixture"}]})
            except Exception:
                pass  # Cancelled before response headers; the socket must close.
            finally:
                finished.set()
        thread = threading.Thread(target=chat)
        thread.start()
        self.assertTrue(self.up.chat_started.wait(timeout=1))
        result = self.call("/api/chat/cancel", {"request_id": "fixture-silent-chat-request"})
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["active_chats"], 0)
        self.assertTrue(finished.wait(timeout=1), "Cancellation must not wait for silent upstream headers")
        self.assertFalse(self.up.chat_finish.is_set())
        self.up.chat_finish.set()
        thread.join(timeout=1)

    def test_health_never_follows_redirect_and_expired_success_is_stale(self):
        self.controller.config["sub2api"]["health_url"] = self.base + "/redirect"
        self.controller.check_health("sub2api")
        self.assertEqual(self.up.calls, [("/redirect", False)])
        self.assertEqual(self.controller.health_state("sub2api")["state"], "error")
        self.controller.config["sub2api"]["health_url"] = self.base + "/health"
        self.controller.check_health("sub2api")
        self.controller.sub2api_check["checked_at"] = "2000-01-01T00:00:00Z"
        self.assertEqual(self.controller.health_state("sub2api")["state"], "stale")

    def test_worker_always_stops_owned_child_when_receipt_write_fails(self):
        spec = importlib.util.spec_from_file_location("fixture_worker", Path(core.__file__).with_name("worker.py"))
        worker = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"core": core}):
            spec.loader.exec_module(worker)
        runtime = self.root / "worker-fixture"
        (runtime / "logs").mkdir(parents=True)
        args = SimpleNamespace(parent=999999, binary="fixture-frpc", config=str(self.root / "visitor.toml"), runtime=runtime)
        child = MagicMock()
        child.poll.return_value = None
        with patch.object(worker.os, "umask"), patch.object(worker.signal, "signal"), \
                patch.object(worker.argparse.ArgumentParser, "parse_args", return_value=args), \
                patch.object(worker, "FileLock"), patch.object(worker, "alive", side_effect=[True, False, False]), \
                patch.object(worker.subprocess, "Popen", return_value=child), \
                patch.object(worker, "write_json", side_effect=[None, None, OSError("fixture disk failure")]):
            with self.assertRaises(OSError):
                worker.main()
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)

    def test_size_limits_and_completed_without_text_are_rejected(self):
        with self.assertRaises(UserError):
            read_json(io.BytesIO(b"x" * (1024 * 1024 + 1)))
        response = io.BytesIO(b'data: {"type":"response.completed"}\n\n')
        response.headers = {"Content-Type": "text/event-stream"}
        with self.assertRaises(UserError):
            consume_response(response)


if __name__ == "__main__":
    unittest.main()
