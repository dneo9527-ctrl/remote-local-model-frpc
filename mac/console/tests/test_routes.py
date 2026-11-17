from pathlib import Path
import tempfile
import unittest

from app.core import UserError
from app.model_routes import switch_route


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.active = self.root / 'active.toml'
        self.new = self.root / 'new.toml'
        self.original = b'old-fixture-not-a-real-config'
        self.active.write_bytes(self.original)
        self.new.write_text('serverAddr="example.invalid"\n[[visitors]]\ntype="stcp"\nbindAddr="127.0.0.1"\nbindPort=11436\n')
        self.calls = []
        self.running = True
        self.c = type('Project', (), {})()
        self.c.runtime = self.root
        self.c.config = {'ollama': {'visitor_config': str(self.active)},
                         'selected_model_route': 'old',
                         'ollama_routes': {'next': {'visitor_config': str(self.new)}}}
        self.c.ollama_running = lambda: self.running
        self.c.action = lambda service, action: self.calls.append((service, action))

    def tearDown(self):
        self.temp.cleanup()

    def test_switch_backs_up_and_only_restarts_owned_manager(self):
        switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.new.read_bytes())
        self.assertEqual(self.calls, [('ollama', 'stop'), ('ollama', 'start')])
        self.assertEqual(next((self.root/'backups').glob('*')).read_bytes(), self.original)

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

    def test_restart_failure_restores_previous_config(self):
        def action(service, action):
            self.calls.append((service, action))
            if action == 'start' and self.active.read_bytes() != self.original:
                raise UserError('fixture failure')
        self.c.action = action
        with self.assertRaises(UserError):
            switch_route(self.c, 'next')
        self.assertEqual(self.active.read_bytes(), self.original)
        self.assertEqual(self.calls[-1], ('ollama', 'start'))


if __name__ == '__main__':
    unittest.main()
