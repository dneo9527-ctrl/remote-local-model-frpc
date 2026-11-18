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
import unittest
import urllib.error
import urllib.request


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
        with self.opener.open(request, timeout=1) as response:
            self.assertEqual(response.status, 200)

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
