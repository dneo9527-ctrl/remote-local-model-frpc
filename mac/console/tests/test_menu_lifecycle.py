"""Real local process lifecycle checks for the Mac menu-bar owner.

Only temporary config/runtime files and ephemeral loopback ports are used. The
fixture never asks for status, starts a visitor or contacts a model endpoint.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from app.core import UserError
from app.server import Controller, Handler


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / 'tools/launch.py'
CONSOLE = ROOT / 'mac/console'

# A real intermediary owns the foreground server. Its death must not leave the
# backend behind; unlike a fake PID check, this exercises the launcher's exec.
OWNER = r'''
import os, subprocess, sys
child = subprocess.Popen([sys.executable, sys.argv[1], '--config', sys.argv[2],
                          '--foreground', '--owner-pid', str(os.getpid())],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
raise SystemExit(child.wait())
'''


@unittest.skipIf(os.name == 'nt', 'macOS/POSIX menu owner lifecycle')
class MenuLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.assertNotIn(self.port, (18765, 18081, 11435, 11436))
        self.base = f'http://127.0.0.1:{self.port}'
        self.config = self.root / 'settings.json'
        self.config.write_text(json.dumps({
            'runtime': str(self.runtime), 'port': self.port,
            'automatic_health_checks': False,
            'codex_config': str(self.root / 'unused-codex.toml'),
            'ollama': {},
        }))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.owner = None
        self.child_pid = None

    def tearDown(self):
        # Graceful cleanup only for the exact temporary process we created.
        if self.owner is not None:
            if self.owner.poll() is None:
                try:
                    self.shutdown()
                except (OSError, ValueError, urllib.error.URLError):
                    self.owner.terminate()
                try:
                    self.owner.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.owner.kill()
                    self.owner.wait(timeout=3)
            if self.owner.stdout:
                self.owner.stdout.close()
        if self.child_pid:
            deadline = time.monotonic() + 4
            while not self.child_exited() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not self.child_exited():
                # The PID belongs to our own fixture; no PID-file discovery.
                os.kill(self.child_pid, 15)
        self.temp.cleanup()

    def start_owned(self):
        self.owner = subprocess.Popen(
            [sys.executable, '-u', '-c', OWNER, str(LAUNCHER), str(self.config)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, cwd=CONSOLE)
        self.child_pid = int(self.owner.stdout.readline().strip())
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                return self.session()
            except (OSError, ValueError, urllib.error.URLError):
                if self.owner.poll() is not None:
                    self.fail('Temporary menu-owned backend exited before readiness')
                time.sleep(0.05)
        self.fail('Temporary menu-owned backend did not become ready')

    def session(self):
        with self.opener.open(self.base + '/api/session', timeout=0.5) as response:
            return json.load(response)

    def shutdown(self):
        session = self.session()
        request = urllib.request.Request(
            self.base + '/api/shutdown', data=b'{}',
            headers={'Content-Type': 'application/json',
                     'X-Local-CSRF': session['csrf']})
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def configure_local_manager(self, fail=False):
        """Install only a temporary recorder, never an actual service manager."""
        calls = self.root / 'manager-calls.jsonl'
        failure = self.root / 'manager-failure'
        manager = self.root / 'fixture-manager.py'
        manager.write_text(
            'import json, pathlib, sys\n'
            f'calls = pathlib.Path({str(calls)!r})\n'
            f'failure = pathlib.Path({str(failure)!r})\n'
            'with calls.open("a") as stream:\n'
            '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'raise SystemExit(1 if failure.exists() else 0)\n')
        if fail:
            failure.write_text('fixture-only failure')
        ports = []
        while len(ports) < 3:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            if port != self.port and port not in ports and port not in (18765, 18081, 11435, 11436):
                ports.append(port)
        visitor = self.root / 'fixture-visitor.toml'
        visitor.write_text('[[visitors]]\ntype="stcp"\nbindAddr="127.0.0.1"\n'
                           f'bindPort={ports[1]}\n')
        config = json.loads(self.config.read_text())
        config['ollama'] = {
            'python': sys.executable, 'manager': str(manager),
            'url': f'http://127.0.0.1:{ports[0]}',
            'visitor_config': str(visitor),
        }
        config['sub2api'] = {'mode': 'stcp', 'port': ports[2]}
        self.config.write_text(json.dumps(config))
        return calls, failure, visitor

    def child_exited(self):
        # A reparented child can briefly be a zombie awaiting the OS reaper.
        # Either absence or a zombie confirms no background code is running.
        result = subprocess.run(['ps', '-o', 'stat=', '-p', str(self.child_pid)],
                                capture_output=True, text=True, timeout=1)
        state = result.stdout.strip()
        return not state or state.startswith('Z')

    def assert_no_listener(self):
        with socket.socket() as sock:
            sock.settimeout(0.2)
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', self.port)), 0)

    def test_owned_launcher_session_matches_private_runtime_record(self):
        session = self.start_owned()
        record = json.loads((self.runtime / 'data/server.json').read_text())
        self.assertEqual(session['service'], 'local-ai-console')
        self.assertIsInstance(session['instance'], str)
        self.assertTrue(session['instance'])
        self.assertEqual(session['instance'], record['instance'])
        self.assertEqual(record['pid'], self.child_pid)
        self.assertEqual(record['url'], self.base)
        self.assertNotEqual(session['instance'], session['csrf'])
        self.assertNotIn('csrf', record)

    def test_authenticated_shutdown_waits_for_actual_backend_exit(self):
        self.start_owned()
        self.shutdown()
        self.assertEqual(self.owner.wait(timeout=6), 0)
        self.assertTrue(self.child_exited())
        self.assert_no_listener()

    def test_full_shutdown_pauses_manager_and_preserves_configuration(self):
        calls, _, visitor = self.configure_local_manager()
        original = self.config.read_bytes()
        visitor_original = visitor.read_bytes()
        self.start_owned()
        result = self.shutdown()
        self.assertIs(result.get('shutdown_complete'), True)
        self.assertEqual(self.owner.wait(timeout=6), 0)
        self.assertTrue(self.child_exited())
        self.assert_no_listener()
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertTrue(recorded, 'Full shutdown did not pause the configured manager')
        self.assertTrue(all(call == ['pause'] for call in recorded))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(visitor.read_bytes(), visitor_original)

    def test_failed_manager_keeps_backend_available_for_shutdown_retry(self):
        calls, failure, visitor = self.configure_local_manager(fail=True)
        original = self.config.read_bytes()
        visitor_original = visitor.read_bytes()
        session = self.start_owned()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.shutdown()
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.assertIsNone(self.owner.poll(), 'A failed stop must retain the backend')
        self.assertFalse(self.child_exited())
        self.assertEqual(self.session()['instance'], session['instance'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(visitor.read_bytes(), visitor_original)
        failure.unlink()
        result = self.shutdown()
        self.assertIs(result.get('shutdown_complete'), True)
        self.assertEqual(self.owner.wait(timeout=6), 0)
        self.assert_no_listener()
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertGreaterEqual(len(recorded), 2)
        self.assertTrue(all(call == ['pause'] for call in recorded))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(visitor.read_bytes(), visitor_original)

    def test_missing_visitor_retains_backend_until_configuration_is_restored(self):
        calls, _, visitor = self.configure_local_manager()
        original = self.config.read_bytes()
        visitor_original = visitor.read_bytes()
        visitor.unlink()
        session = self.start_owned()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.shutdown()
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.assertIsNone(self.owner.poll())
        self.assertEqual(self.session()['instance'], session['instance'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(visitor.exists(), 'Shutdown must not silently create missing config')
        self.assertIn(['pause'], [json.loads(line) for line in calls.read_text().splitlines()])
        visitor.write_bytes(visitor_original)
        self.assertIs(self.shutdown().get('shutdown_complete'), True)
        self.assertEqual(self.owner.wait(timeout=6), 0)
        self.assert_no_listener()
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(visitor.read_bytes(), visitor_original)

    def test_completed_shutdown_rejects_new_start_route_and_chat(self):
        controller = Controller(self.config)
        try:
            with patch.object(controller.tunnel, 'stop'):
                controller.shutdown_connections()
            self.assertTrue(controller.connections_stopped)
            for service, action in [('ollama', 'start'), ('sub2api', 'start'),
                                    ('route', 'fixture-route'), ('model-route', 'fixture-route')]:
                with self.subTest(service=service), patch('app.server.subprocess.run') as command:
                    with self.assertRaisesRegex(UserError, '正在全部退出'):
                        controller.action(service, action)
                    command.assert_not_called()
            handler = SimpleNamespace(server=SimpleNamespace(controller=controller))
            with patch('app.server.opener') as outbound:
                with self.assertRaisesRegex(UserError, '停止接收新聊天'):
                    Handler.chat(handler, {'model': 'fixture', 'messages': [
                        {'role': 'user', 'content': 'must not reach a model'}]})
                outbound.assert_not_called()
        finally:
            controller.pool.shutdown(wait=True)

    def test_owner_death_cleans_up_foreground_backend(self):
        self.start_owned()
        self.owner.terminate()
        self.owner.wait(timeout=3)
        deadline = time.monotonic() + 6
        while not self.child_exited() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.child_exited(), 'Owner death left backend running')
        self.assert_no_listener()

    def test_unrelated_owner_pid_is_rejected_without_binding(self):
        # A valid but unrelated PID is stronger than checking an invalid number.
        unrelated = os.getpid()
        command = [sys.executable, '-m', 'app', 'serve', '--config', str(self.config),
                   '--owner-pid', str(unrelated)]
        intermediary = r'''
import subprocess, sys
raise SystemExit(subprocess.run(sys.argv[1:], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode)
'''
        result = subprocess.run([sys.executable, '-c', intermediary, *command],
                                cwd=CONSOLE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_listener()
        self.assertFalse((self.runtime / 'data/server.json').exists())


if __name__ == '__main__':
    unittest.main()
