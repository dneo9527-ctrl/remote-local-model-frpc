import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.configuration import CodexConfig, merge
from app.core import FileLock, Tunnel, UserError, port_open
from app.server import Controller, Handler, LocalHTTPServer


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / 'private.key'
        self.key.write_text('fixture-only-not-a-real-key')
        self.cfg = self.root / 'config.toml'
        self.original = ('# keep comment\nmodel_provider = "custom"\n'
                         '[model_providers.custom]\nname="Original"\nrequires_openai_auth=true\n'
                         '[mcp_servers.example]\ncommand="unchanged"\n'
                         '[features]\nflag=true\n').encode()
        self.cfg.write_bytes(self.original)
        self.c = CodexConfig(self.cfg, self.root, {'key_file': str(self.key),
                            'base_url': 'http://127.0.0.1:18081/v1', 'model': 'gpt-6.1-sol'})

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_preserves_unrelated_config_and_is_idempotent(self):
        first = merge(self.original.decode(), self.c.provider(), 'gpt-6.1-sol')
        self.assertIn('# keep comment', first)
        new = tomllib.loads(first)
        old = tomllib.loads(self.original.decode())
        self.assertEqual(new['features'], old['features'])
        self.assertEqual(new['mcp_servers'], old['mcp_servers'])
        self.assertEqual(new['model_providers']['custom'], old['model_providers']['custom'])
        self.assertEqual(first, merge(first, self.c.provider(), 'gpt-6.1-sol'))
        self.assertNotIn(self.key.read_text(), first)

    def test_prepare_does_not_change_live_config(self):
        self.c.prepare()
        self.assertEqual(self.cfg.read_bytes(), self.original)

    @patch('app.configuration.app_running', return_value=False)
    def test_apply_backup_and_restore(self, _):
        self.c.prepare()
        self.c.apply(None)
        self.assertEqual(tomllib.loads(self.cfg.read_text())['model_provider'], 'sub2api_local')
        self.c.restore(None)
        self.assertEqual(self.cfg.read_bytes(), self.original)

    @patch('app.configuration.app_running', return_value=True)
    def test_running_app_blocks_switch(self, _):
        self.c.prepare()
        with self.assertRaises(UserError):
            self.c.apply(None)
        self.assertEqual(self.cfg.read_bytes(), self.original)

    @patch('app.configuration.app_running', return_value=False)
    def test_concurrent_edits_are_not_overwritten(self, _):
        self.c.prepare()
        self.cfg.write_bytes(self.original + b'new_setting=1\n')
        with self.assertRaises(UserError):
            self.c.apply(None)

    @patch('app.configuration.app_running', return_value=False)
    def test_route_change_invalidates_candidate(self, _):
        self.c.prepare()
        self.c.settings = dict(self.c.settings, base_url='https://different.invalid/v1')
        with self.assertRaises(UserError):
            self.c.apply(None)

    @patch('app.configuration.app_running', return_value=False)
    def test_restore_does_not_clobber_later_changes(self, _):
        self.c.prepare()
        self.c.apply(None)
        self.cfg.write_text(self.cfg.read_text() + '\n# added after switch\n')
        with self.assertRaises(UserError):
            self.c.restore(None)

    def test_unusual_quoted_managed_key_fails_safely(self):
        with self.assertRaises(UserError):
            merge('"model" = "previous"\n', self.c.provider(), 'next')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_os_lock_excludes_duplicate(self):
        with FileLock(self.root / 'service.lock'):
            with self.assertRaises(UserError):
                FileLock(self.root / 'service.lock')

    @unittest.skipIf(os.name == 'nt', 'Native Windows worker lifecycle is pending')
    def test_owned_worker_lifecycle_parent_death_and_unrelated_listener(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        fake = self.root / 'fake-frpc'
        fake.write_text(f'#!{sys.executable}\n' +
                        'import sys,socket,time\n'
                        'if "verify" in sys.argv:raise SystemExit(0)\n'
                        f's=socket.socket();s.bind(("127.0.0.1",{port}));s.listen()\n'
                        'while True:time.sleep(.1)\n')
        fake.chmod(0o700)
        cfg = self.root / 'visitor.toml'
        cfg.write_text(f'[[visitors]]\ntype="stcp"\nbindAddr="127.0.0.1"\nbindPort={port}\n')
        settings = {'mode': 'stcp', 'frpc': str(fake), 'visitor_config': str(cfg), 'port': port}
        tunnel = Tunnel(settings, self.root)
        try:
            tunnel.start()
            first = tunnel.process.pid
            tunnel.start()
            self.assertEqual(tunnel.process.pid, first)
            for _ in range(40):
                if port_open(port): break
                time.sleep(.05)
            self.assertTrue(port_open(port))
        finally:
            tunnel.stop()
        self.assertFalse(port_open(port))
        # Orphan prevention: the independently spawned supervisor detects owner exit.
        parent = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / 'app/worker.py'),
                                   '--parent', str(parent.pid), '--binary', str(fake), '--config', str(cfg),
                                   '--runtime', str(self.root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(40):
                if port_open(port): break
                time.sleep(.05)
            self.assertTrue(port_open(port))
            parent.terminate(); parent.wait(timeout=5)
            worker.wait(timeout=8)
            self.assertFalse(port_open(port))
        finally:
            if parent.poll() is None: parent.terminate();parent.wait(timeout=5)
            if worker.poll() is None: worker.terminate();worker.wait(timeout=8)
        with socket.socket() as unrelated:
            unrelated.bind(('127.0.0.1', port));unrelated.listen()
            with self.assertRaises(UserError):tunnel.start()
            self.assertTrue(port_open(port))


class Upstream(BaseHTTPRequestHandler):
    posts = 0
    last_body = None
    def log_message(self, *_): pass
    def do_POST(self):
        type(self).posts += 1
        type(self).last_body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
        self.wfile.write(b'{"message":{"content":"first"}}\n')
        # Deliberately end mid-generation without done=true.


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = LocalHTTPServer(('127.0.0.1',0), Upstream)
        threading.Thread(target=cls.up.serve_forever,daemon=True).start()
        cls.server = LocalHTTPServer(('127.0.0.1',0), Handler)
        cls.server.authority = f'127.0.0.1:{cls.server.server_port}'
        cls.server.token = 'csrf-fixture'
        cls.server.controller = type('TestController', (), {'config': {'ollama': {'url':f'http://127.0.0.1:{cls.up.server_port}'}}})()
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.base = 'http://' + cls.server.authority
        cls.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.up.shutdown();cls.up.server_close()

    def request(self,path='/',data=None,headers=None):
        return self.op.open(urllib.request.Request(self.base+path,data=data,headers=headers or {}),timeout=5)

    def test_local_page_session_and_no_private_file_routes(self):
        with self.request() as r:
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        with self.request('/api/session') as r:self.assertIn('csrf',json.load(r))
        for path in ['/api-key.txt','/config.json','/../config.toml']:
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path)
            self.assertEqual(e.exception.code,404)

    def test_cross_origin_host_and_missing_csrf_rejected(self):
        for headers,data in [({'Host':'attacker.invalid'},None),
                             ({'Origin':'https://attacker.invalid'},None),
                             ({'Content-Type':'application/json'},b'{}')]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/action',data,headers)
            self.assertEqual(e.exception.code,403)

    def test_loopback_bind_works_when_reverse_dns_is_unavailable(self):
        with patch('socket.getfqdn', side_effect=OSError('DNS unavailable')) as lookup:
            server = LocalHTTPServer(('127.0.0.1', 0), Handler)
            try:
                self.assertEqual(server.server_address[0], '127.0.0.1')
                self.assertGreater(server.server_port, 0)
                lookup.assert_not_called()
            finally:
                server.server_close()

    def test_stream_interruption_reported_without_request_replay(self):
        before=Upstream.posts
        body=json.dumps({'model':'qwen3-8b-8k:latest','messages':[{'role':'user','content':'hello'}]}).encode()
        with self.request('/api/chat',body,{'Content-Type':'application/json','X-Local-CSRF':'csrf-fixture'}) as r:
            events=[json.loads(l) for l in r]
        self.assertEqual(events[0]['message']['content'],'first')
        self.assertIn('not replayed',events[-1]['error'])
        self.assertEqual(Upstream.posts,before+1)
        self.assertEqual(Upstream.last_body['options']['num_predict'],2048)


if __name__ == '__main__': unittest.main()
