import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core import UserError, atomic_write
from app.model_routes import switch_route


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.active = self.root / 'active.toml'
        self.new = self.root / 'new.toml'
        self.original = b'old-fixture-not-a-real-config'
        self.active.write_bytes(self.original)
        self.new.write_text('serverAddr="example.invalid"\nserverPort=7000\n[[visitors]]\nname="fixture"\nserverName="fixture"\nsecretKey="fixture-not-a-real-secret"\ntype="stcp"\nbindAddr="127.0.0.1"\nbindPort=11436\n')
        self.calls = []
        self.running = True
        self.c = type('Project', (), {})()
        self.c.runtime = self.root
        self.c.config = {'ollama': {'visitor_config': str(self.active)},
                         'selected_model_route': 'old',
                         'ollama_routes': {'next': {'visitor_config': str(self.new)}}}
        self.c.config_path = self.root/'settings.json'
        self.c.config_path.write_text(json.dumps(self.c.config))
        self.original_settings = self.c.config_path.read_bytes()
        self.c.ollama_running = lambda: self.running
        self.c.action = lambda service, action: self.calls.append((service, action))

    def tearDown(self):
        self.temp.cleanup()

    def test_switch_backs_up_and_only_restarts_owned_manager(self):
        switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.new.read_bytes())
        self.assertEqual(self.calls, [('ollama', 'stop'), ('ollama', 'start')])
        self.assertEqual(next((self.root/'backups').glob('*.toml')).read_bytes(), self.original)
        self.assertEqual(json.loads(self.c.config_path.read_bytes())['selected_model_route'], 'next')
        self.assertEqual(self.c.config['selected_model_route'], 'next')

    def test_paused_connection_stays_paused(self):
        self.running = False
        switch_route(self.c, 'next')
        self.assertEqual(self.calls, [])

    def test_reject_public_visitor_without_mutation(self):
        self.new.write_text(self.new.read_text().replace('127.0.0.1', '0.0.0.0'))
        with self.assertRaises(UserError):
            switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.original)
        self.assertEqual(self.calls, [])

    def test_restart_failure_restores_previous_config_and_selection(self):
        def action(service, action):
            self.calls.append((service, action))
            if action == 'start' and self.active.read_bytes() != self.original:
                raise UserError('fixture failure')
        self.c.action = action
        with self.assertRaises(UserError):
            switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.original)
        self.assertEqual(self.c.config_path.read_bytes(), self.original_settings)
        self.assertEqual(self.c.config['selected_model_route'], 'old')
        self.assertEqual(self.calls[-1], ('ollama', 'start'))

    def test_setting_write_failure_rolls_back_visitor(self):
        def write(path, content):
            if path == self.c.config_path:
                raise OSError('fixture write failure')
            return atomic_write(path, content)
        with patch('app.model_routes.atomic_write', side_effect=write):
            with self.assertRaises(UserError):
                switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.original)
        self.assertEqual(self.c.config_path.read_bytes(), self.original_settings)
        self.assertEqual(self.c.config['selected_model_route'], 'old')

    def test_external_edit_during_pause_is_preserved_without_start(self):
        def action(service, action):
            self.calls.append((service, action))
            if action == 'stop':
                self.active.write_bytes(b'new independent editor bytes')
        self.c.action = action
        with self.assertRaises(UserError):
            switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), b'new independent editor bytes')
        self.assertEqual(self.calls, [('ollama', 'stop')])
        self.assertEqual(self.c.config_path.read_bytes(), self.original_settings)

    def test_stale_controller_settings_rejected_before_pause(self):
        self.c.config_path.write_text(json.dumps(dict(self.c.config, unrelated='later edit')))
        with self.assertRaises(UserError):
            switch_route(self.c, 'next')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.active.read_bytes(), self.original)

    def test_same_selected_id_does_not_hide_actual_visitor_drift(self):
        self.c.config['selected_model_route'] = 'next'
        self.c.config_path.write_text(json.dumps(self.c.config))
        switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.new.read_bytes())
        self.assertEqual(self.calls, [('ollama', 'stop'), ('ollama', 'start')])

    def test_source_is_snapshot_validated_before_pause(self):
        validated = self.new.read_bytes()
        def action(service, action):
            if action == 'stop':
                self.new.write_text('invalid changed source after validation')
        self.c.action = action
        switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), validated)

    def test_incomplete_or_public_management_config_rejected_before_pause(self):
        valid = self.new.read_text()
        for candidate in (valid.replace('serverPort=7000\n', ''),
                          valid.replace('serverName="fixture"\n', ''),
                          '[webServer]\naddr="0.0.0.0"\n' + valid,
                          'includes=["elsewhere.toml"]\n' + valid):
            self.new.write_text(candidate)
            with self.assertRaises(UserError):
                switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.original)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
