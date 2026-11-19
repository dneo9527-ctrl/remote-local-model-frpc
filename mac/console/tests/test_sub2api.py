"""Credential-free checks for the independent Mac Sub2API controls.

These use temporary files and mocked network/process operations. They never
contact a Windows provider, frps, a real downstream API or the user's apps.
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.core import UserError, port_listening
from app.server import Controller


class Sub2ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / 'downstream-fixture-only.key'
        self.key.write_text('fixture-downstream-value-never-send-to-browser')
        self.visitor = self.root / 'private-fixture-only.toml'
        self.visitor.write_text(
            'serverAddr="example.invalid"\nserverPort=7000\n'
            '[auth]\nmethod="token"\ntoken="fixture-control-token"\n'
            '[transport.tls]\nenable=true\n'
            '[[visitors]]\nname="fixture-visitor"\ntype="stcp"\n'
            'serverName="sub2api_ke"\nsecretKey="fixture-stcp-secret"\n'
            'bindAddr="127.0.0.1"\nbindPort=18081\n'
            '[visitors.transport]\nuseEncryption=true\n')
        self.binary = self.root / 'frpc-fixture-only'
        self.binary.write_text('fixture-not-an-executable')
        self.ccswitch = self.root / 'Fixture CC Switch.app'
        self.ccswitch.mkdir()
        self.codex = self.root / 'codex-fixture-only.toml'
        self.codex.write_text('model_provider="original"\n[mcp_servers.fixture]\ncommand="keep"\n')
        self.sub2api = {
            'mode': 'stcp', 'port': 18081,
            'base_url': 'http://127.0.0.1:18081/v1',
            'health_url': 'http://127.0.0.1:18081/health',
            'model': 'fixture-model', 'frpc': str(self.binary),
            'visitor_config': str(self.visitor), 'key_file': str(self.key),
        }
        self.config = {
            'runtime': str(self.root / 'runtime'),
            'codex_config': str(self.codex),
            'include_legacy_integrations': False,
            'apps': {'ccswitch': str(self.ccswitch)},
            'ollama': {'url': 'http://127.0.0.1:11435',
                       'manager': '/fixture/ollama-manager',
                       'visitor_config': '/fixture/ollama-private.toml'},
            'selected_model_route': 'keep-ollama-route',
            'ollama_routes': {'keep-ollama-route': {'name': 'Original Ollama'}},
            'selected_route': 'windows',
            'sub2api': copy.deepcopy(self.sub2api),
            'sub2api_profiles': {
                'windows': dict(self.sub2api, display_name='Windows Sub2API'),
                'next': dict(self.sub2api, display_name='Alternative connection',
                             model='fixture-next-model'),
            },
            'unrelated_setting': {'preserve': ['original', 42]},
        }
        self.settings = self.root / 'settings.json'
        self.settings.write_text(json.dumps(self.config, indent=2) + '\n')
        self.controller = Controller(self.settings)

    def tearDown(self):
        self.controller.tunnel.stop()
        self.controller.pool.shutdown(wait=True)
        self.temp.cleanup()

    def local_status(self):
        with patch('app.server.port_listening', return_value=False), \
                patch.object(self.controller, 'ollama_running', return_value=False):
            return self.controller.status()

    def test_refresh_does_not_probe_sub2api_or_expose_private_material(self):
        with patch('app.server.probe', return_value={'ok': True, 'detail': 'fixture',
                                                    'latency_ms': 1}) as probe:
            status = self.local_status()
        self.assertEqual([call.args[0] for call in probe.call_args_list],
                         ['http://127.0.0.1:11435/api/version'])
        self.assertTrue(status['sub2api']['configured'])
        self.assertEqual(status['sub2api']['remote']['state'], 'unchecked')
        self.assertEqual(status['sub2api']['ownership'], 'stopped')
        self.assertTrue(status['apps']['ccswitch'])
        self.assertEqual(status['selected_route'], 'windows')
        self.assertEqual({route['id'] for route in status['routes']}, {'windows', 'next'})
        browser_payload = json.dumps(status)
        for private in (self.key.read_text(), str(self.key), str(self.visitor),
                        str(self.binary), str(self.codex), 'fixture-control-token',
                        'fixture-stcp-secret'):
            self.assertNotIn(private, browser_payload)

    def test_external_listener_is_reported_without_being_claimed_or_stopped(self):
        with patch('app.server.probe', return_value={'ok': False, 'detail': 'fixture'}), \
                patch('app.server.port_listening', return_value=True), \
                patch.object(self.controller, 'ollama_running', return_value=False):
            state = self.controller.status()['sub2api']
        self.assertTrue(state['listener'])
        self.assertFalse(state['running'])
        self.assertEqual(state['ownership'], 'external')
        with patch('app.core.port_listening', return_value=True), \
                patch('app.core.subprocess.run') as run, \
                patch('app.core.subprocess.Popen') as start:
            with self.assertRaises(UserError):
                self.controller.action('sub2api', 'start')
            self.controller.action('sub2api', 'stop')
        run.assert_not_called()
        start.assert_not_called()

    def test_passive_mac_listener_query_never_opens_a_forwarding_connection(self):
        command = ['/usr/sbin/lsof', '-nP', '-iTCP:18081', '-sTCP:LISTEN', '-t']
        for returncode, stdout, stderr, expected in (
                (0, b'12345\n', b'', True),
                (1, b'', b'', False),
                (2, b'', b'fixture lsof error', None)):
            with self.subTest(returncode=returncode), \
                    patch('app.core.sys.platform', 'darwin'), \
                    patch('app.core.subprocess.run', return_value=subprocess.CompletedProcess(
                        command, returncode, stdout=stdout, stderr=stderr)) as run, \
                    patch('app.core.socket.socket') as socket_factory:
                if expected is None:
                    with self.assertRaises(UserError):
                        port_listening(18081)
                else:
                    self.assertIs(port_listening(18081), expected)
                run.assert_called_once_with(command, capture_output=True, timeout=3)
                socket_factory.assert_not_called()
                socket_factory.return_value.__enter__.return_value.connect_ex.assert_not_called()

    def test_status_uses_passive_listener_lookup_without_socket_connections(self):
        self.controller.config['automatic_health_checks'] = False
        with patch('app.core.sys.platform', 'darwin'), \
                patch('app.core.subprocess.run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout=b'12345\n', stderr=b'')) as run, \
                patch('app.core.socket.socket') as socket_factory, \
                patch('app.server.probe') as probe, \
                patch.object(self.controller, 'ollama_running', return_value=False):
            status = self.controller.status()
        self.assertEqual(status['sub2api']['ownership'], 'external')
        self.assertEqual(status['sub2api']['remote']['state'], 'unchecked')
        self.assertEqual(run.call_args.args[0][0], '/usr/sbin/lsof')
        socket_factory.assert_not_called()
        socket_factory.return_value.__enter__.return_value.connect_ex.assert_not_called()
        probe.assert_not_called()

    def test_include_and_public_management_configs_are_rejected_before_frpc_runs(self):
        original = self.visitor.read_text()
        for candidate in ('includes=["external-fixture.toml"]\n' + original,
                          original + '\n[webServer]\naddr="0.0.0.0"\nport=9999\n'):
            self.visitor.write_text(candidate)
            with self.subTest(candidate=candidate[:40]), \
                    patch('app.core.port_listening', return_value=False), \
                    patch('app.core.subprocess.run') as run, \
                    patch('app.core.subprocess.Popen') as start:
                with self.assertRaises(UserError):
                    self.controller.action('sub2api', 'start')
                run.assert_not_called()
                start.assert_not_called()

    def test_manual_api_check_is_cached_until_another_explicit_check(self):
        def probe_result(url):
            return {'ok': True, 'detail': 'API fixture response', 'latency_ms': 7}

        with patch('app.server.probe', side_effect=probe_result) as probe, \
                patch('app.server.port_listening', return_value=False):
            self.controller.action('sub2api', 'check')
            first = self.local_status()['sub2api']['remote']
            second = self.local_status()['sub2api']['remote']
        health_calls = [call for call in probe.call_args_list
                        if call.args[0] == self.sub2api['health_url']]
        self.assertEqual(len(health_calls), 1)
        self.assertEqual(first['state'], 'ok')
        self.assertEqual(first['latency_ms'], 7)
        self.assertEqual(first, second)

    def test_failed_manual_check_is_visible_without_refresh_retry(self):
        with patch('app.server.port_listening', return_value=False), \
                patch('app.server.probe', return_value={'ok': False,
                                                    'detail': 'ConnectionResetError',
                                                    'latency_ms': 9}) as probe:
            try:
                self.controller.action('sub2api', 'check')
            except UserError:
                pass
            remote = self.local_status()['sub2api']['remote']
        health_calls = [call for call in probe.call_args_list
                        if call.args[0] == self.sub2api['health_url']]
        self.assertEqual(len(health_calls), 1)
        self.assertEqual(remote['state'], 'error')
        self.assertIn('ConnectionResetError', remote['detail'])

    def test_start_and_stop_invalidate_previous_api_result(self):
        with patch('app.server.probe', return_value={'ok': True, 'detail': 'fixture',
                                                    'latency_ms': 1}):
            for action in ('start', 'stop'):
                self.controller.action('sub2api', 'check')
                with patch.object(self.controller.tunnel, action, return_value='fixture'):
                    self.controller.action('sub2api', action)
                self.assertEqual(self.local_status()['sub2api']['remote']['state'], 'unchecked')

    def test_local_only_mode_does_not_make_even_ollama_health_requests(self):
        self.controller.config['automatic_health_checks'] = False
        with patch('app.server.probe', side_effect=AssertionError('No health request allowed')) as probe:
            state = self.local_status()
            again = self.local_status()
        probe.assert_not_called()
        self.assertFalse(state['ollama']['checked'])
        self.assertFalse(again['ollama']['checked'])
        self.assertEqual(state['sub2api']['remote']['state'], 'unchecked')

    def test_open_cc_switch_uses_the_configured_app_without_credentials(self):
        with patch('app.server.sys.platform', 'darwin'), \
                patch('app.server.subprocess.run') as run:
            self.controller.action('app', 'ccswitch')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-a', str(self.ccswitch)])
        self.assertNotIn(self.key.read_text(), str(run.call_args))

    def test_old_codex_writer_is_blocked_in_cc_switch_mode(self):
        original = self.codex.read_bytes()
        for legacy in (False, True):
            self.controller.config['include_legacy_integrations'] = legacy
            for action in ('prepare', 'apply', 'restore'):
                with self.subTest(action=action, legacy=legacy), \
                        patch.object(self.controller.codex, action) as writer:
                    with self.assertRaisesRegex(UserError, 'CC Switch'):
                        self.controller.action('codex', action)
                    writer.assert_not_called()
        self.assertEqual(self.codex.read_bytes(), original)

    def test_route_change_backs_up_settings_and_preserves_other_connections(self):
        before = self.settings.read_bytes()
        with patch.object(self.controller.tunnel, 'stop') as stop, \
                patch('app.server.port_listening', return_value=False):
            self.controller.action('route', 'next')
        stop.assert_called_once()
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved['selected_route'], 'next')
        self.assertEqual(saved['sub2api']['model'], 'fixture-next-model')
        for field in ('ollama', 'ollama_routes', 'selected_model_route',
                      'unrelated_setting', 'apps'):
            self.assertEqual(saved[field], self.config[field])
        backups = list((self.root / 'runtime' / 'backups').rglob('*'))
        self.assertTrue(any(path.is_file() and path.read_bytes() == before for path in backups),
                        'A byte-for-byte settings backup must precede the route change')
        self.assertEqual(self.codex.read_text(),
                         'model_provider="original"\n[mcp_servers.fixture]\ncommand="keep"\n')

    def test_concurrent_settings_edit_is_preserved_before_route_stop(self):
        later = dict(self.config, unrelated_setting={'preserve': 'later editor'})
        self.settings.write_text(json.dumps(later) + '\n')
        changed = self.settings.read_bytes()
        with patch.object(self.controller.tunnel, 'stop') as stop, \
                patch('app.server.port_listening', return_value=False):
            with self.assertRaises(UserError):
                self.controller.action('route', 'next')
        stop.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), changed)
        self.assertEqual(self.controller.config['selected_route'], 'windows')


if __name__ == '__main__':
    unittest.main()
