"""Private-profile CRUD using isolated files; never touches live connections."""
import copy
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import tomllib
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.core import FileLock, UserError, atomic_write
from app.route_profiles import list_routes, manage_route, settings_lock_path
from app.server import Controller, Handler, LocalHTTPServer


TOKEN, SECRET = "fixture-frps-control-token", "fixture-stcp-secret-key"


def visitor(port=11436, addr="192.0.2.20", name="ollama"):
    # Mirrors Windows' export: omitted TLS/encryption rely on defaults, which
    # the new profile manager must explicitly enable in its owned copy.
    return ('serverAddr = ' + json.dumps(addr) + '\nserverPort = 7000\n'
            'auth.method = "token"\nauth.token = ' + json.dumps(TOKEN) + '\n'
            'auth.additionalScopes = ["HeartBeats", "NewWorkConns"]\n'
            'transport.protocol = "tcp"\ntransport.tcpMux = true\n'
            'transport.heartbeatInterval = 30\ntransport.tls.serverName = "frps.example.invalid"\n'
            '[log]\nto = "console"\nlevel = "info"\n'
            '[[visitors]]\nname = "fixture-visitor"\ntype = "stcp"\nserverName = ' + json.dumps(name) + '\n'
            'secretKey = ' + json.dumps(SECRET) + '\nserverUser = "fixture-user"\n'
            'bindAddr = "127.0.0.1"\nbindPort = ' + str(port) + '\n'
            'transport.useCompression = true\n')


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "live-visitor.toml"
        self.other = self.root / "other-visitor.toml"
        self.sub = self.root / "sub2-visitor.toml"
        self.live.write_text(visitor(), encoding="utf-8")
        self.other.write_text(visitor(addr="192.0.2.30"), encoding="utf-8")
        self.sub.write_text(visitor(18081, name="sub2api_ke"), encoding="utf-8")
        self.key = self.root / "downstream.key"
        self.key.write_text("fixture-downstream-never-in-visitor", encoding="utf-8")
        self.config_path = self.root / "settings.json"
        self.config = {
            "runtime": str(self.root / "runtime"), "automatic_health_checks": False,
            "codex_config": str(self.root / "unused-codex.toml"),
            "ollama": {"url": "http://127.0.0.1:11435", "visitor_config": str(self.live),
                       "manager": "/fixture/manager"},
            "selected_model_route": "current",
            "ollama_routes": {"current": {"name": "现用 Ollama", "visitor_config": str(self.live), "extra": "keep"},
                              "other": {"name": "另一 VPS", "visitor_config": str(self.other)}},
            "selected_route": "current-sub",
            "sub2api": {"mode": "stcp", "visitor_config": str(self.sub), "port": 18081,
                        "frpc": "/fixture/private-frpc", "key_file": str(self.key),
                        "base_url": "http://127.0.0.1:18081/v1", "health_url": "http://127.0.0.1:18081/health",
                        "model": "fixture-response-model"},
            "sub2api_profiles": {"legacy-direct": {"display_name": "旧直连", "mode": "direct", "base_url": "http://127.0.0.1:9999/v1"}},
            "unrelated": {"MCP": "preserve", "permissions": ["keep"]},
        }
        self.config["sub2api_profiles"]["current-sub"] = dict(self.config["sub2api"], display_name="现用 Sub2")
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.controller = Controller(self.config_path)

    def tearDown(self):
        self.controller.pool.shutdown(wait=True)
        self.temp.cleanup()

    def save(self, service="ollama", **kwargs):
        return manage_route(self.controller, {"service": service, "action": "save", **kwargs})

    def registered_file(self, service, identity):
        key = "ollama_routes" if service == "ollama" else "sub2api_profiles"
        return Path(self.controller.config[key][identity]["visitor_config"])

    def test_listing_is_local_and_never_exposes_credentials_or_paths(self):
        with patch("app.server.probe", side_effect=AssertionError("No network")), \
                patch("app.route_profiles.subprocess.run", side_effect=AssertionError("No process")):
            results = [list_routes(self.controller, service) for service in ("ollama", "sub2api")]
        raw = json.dumps(results)
        for private in (TOKEN, SECRET, str(self.live), str(self.root), str(self.key), self.key.read_text(encoding="utf-8")):
            self.assertNotIn(private, raw)
        item = results[0]["items"][0]
        self.assertTrue(item["editable"])
        self.assertTrue(item["active"])
        self.assertEqual(item["server_addr"], "192.0.2.20")
        self.assertIsNone(item["tls"])
        self.assertIsNone(item["encryption"])
        self.assertEqual(results[1]["items"][0]["mode"], "direct")
        self.assertFalse(results[1]["items"][0]["editable"])

    def test_edit_current_keeps_identity_and_marks_pending_without_live_mutation(self):
        before_settings, before_live = self.config_path.read_bytes(), self.live.read_bytes()
        with patch.object(self.controller, "action", side_effect=AssertionError("must not apply")), \
                patch.object(self.controller.tunnel, "start", side_effect=AssertionError("must not start")), \
                patch.object(self.controller.tunnel, "stop", side_effect=AssertionError("must not stop")):
            result = self.save(id="current", name="另一 VPS", fields={"server_addr": "192.0.2.99", "server_port": 7443,
                                                                        "token": "", "secret_key": ""})
        self.assertFalse(result["created_copy"])
        self.assertEqual(result["saved_id"], "current")
        self.assertTrue(result["pending"])
        self.assertEqual(self.live.read_bytes(), before_live)
        self.assertEqual(self.controller.config["selected_model_route"], "current")
        self.assertEqual(len(self.controller.config["ollama_routes"]), 2)
        self.assertTrue(next(i for i in list_routes(self.controller, "ollama")["items"] if i["id"] == "current")["pending"])
        path = self.registered_file("ollama", result["saved_id"])
        self.assertEqual(path.parent, self.controller.runtime / "config/route-profiles")
        if os.name == "nt":
            # Windows chmod controls the read-only bit, not POSIX owner/group
            # permission bits. The same test still verifies file creation and
            # supported writable state without claiming a POSIX mode or ACL.
            self.assertTrue(path.is_file())
            self.assertTrue(os.access(path, os.R_OK | os.W_OK))
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["auth"]["token"], TOKEN)
        self.assertEqual(document["visitors"][0]["secretKey"], SECRET)
        self.assertEqual(document["serverAddr"], "192.0.2.99")
        self.assertTrue(document["transport"]["tls"]["enable"])
        self.assertTrue(document["visitors"][0]["transport"]["useEncryption"])
        self.assertTrue(document["visitors"][0]["transport"]["useCompression"])
        self.assertEqual(document["transport"]["heartbeatInterval"], 30)
        self.assertEqual(document["auth"]["additionalScopes"], ["HeartBeats", "NewWorkConns"])
        self.assertEqual(document["transport"]["tls"]["serverName"], "frps.example.invalid")
        self.assertEqual(self.controller.config["unrelated"], self.config["unrelated"])
        backups = list((self.controller.runtime / "backups").glob("*.json"))
        self.assertTrue(any(path.read_bytes() == before_settings for path in backups))

    def test_noncurrent_edit_replaces_only_registration_and_keeps_original_file(self):
        before = self.other.read_bytes()
        result = self.save(id="other", name="更新方案", fields={"server_name": "ollama-new", "token": "fixture-new-token"})
        self.assertEqual(result["saved_id"], "other")
        self.assertFalse(result["created_copy"])
        self.assertEqual(self.other.read_bytes(), before)
        path = self.registered_file("ollama", "other")
        self.assertNotEqual(path, self.other)
        self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8"))["auth"]["token"], "fixture-new-token")

    def test_rename_and_region_only_edit_current_without_duplicate_or_pending(self):
        live = self.live.read_bytes()
        result = self.save(id="current", name="东京备用", fields={"country": "jp", "city": "东京"})
        self.assertEqual(result["saved_id"], "current")
        self.assertFalse(result["created_copy"])
        self.assertFalse(result["pending"])
        self.assertEqual(self.registered_file("ollama", "current"), self.live)
        self.assertEqual(self.live.read_bytes(), live)
        self.assertEqual(len(self.controller.config["ollama_routes"]), 2)
        item = next(i for i in list_routes(self.controller, "ollama")["items"] if i["id"] == "current")
        self.assertEqual((item["name"], item["country"], item["city"]), ("东京备用", "JP", "东京"))
        self.assertFalse(item["pending"])

    def test_add_backup_by_only_name_ip_and_existing_template(self):
        before = self.live.read_bytes()
        result = self.save(template_id="current", name="备用节点", fields={"server_addr": "192.0.2.52"})
        self.assertNotEqual(result["saved_id"], "current")
        saved = tomllib.loads(self.registered_file("ollama", result["saved_id"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["serverAddr"], "192.0.2.52")
        self.assertEqual(saved["auth"]["token"], TOKEN)
        self.assertEqual(saved["visitors"][0]["secretKey"], SECRET)
        self.assertEqual(self.controller.config["selected_model_route"], "current")
        self.assertEqual(self.live.read_bytes(), before)
        self.assertNotIn(TOKEN, json.dumps(result))
        with self.assertRaises(UserError):
            self.save(id="current", template_id="other", fields={})

    def test_current_edit_then_apply_same_node_is_not_treated_as_already_applied(self):
        self.save(id="current", name="同一节点", fields={"server_addr": "192.0.2.55"})
        calls = []
        def action(service, operation):
            calls.append((service, operation))
        from app.model_routes import switch_route
        with patch.object(self.controller, "ollama_running", return_value=True), \
                patch.object(self.controller, "action", side_effect=action):
            switch_route(self.controller, "current")
        self.assertEqual(calls, [("ollama", "stop"), ("ollama", "start")])
        self.assertEqual(tomllib.loads(self.live.read_text(encoding="utf-8"))["serverAddr"], "192.0.2.55")
        item = next(i for i in list_routes(self.controller, "ollama")["items"] if i["id"] == "current")
        self.assertFalse(item["pending"])
        self.assertEqual(len(self.controller.config["ollama_routes"]), 2)

    def test_manual_node_check_caches_only_matching_endpoint_and_get_never_probes(self):
        result = {"checked_at": "2026-11-20T00:00:00+00:00", "ping": {"state": "failed", "latency_ms": None},
                  "tcp": {"state": "ok", "latency_ms": 12}}
        before = self.config_path.read_bytes()
        with patch("app.route_connectivity.probe_node", return_value=result) as probe:
            self.assertNotIn("connectivity", list_routes(self.controller, "ollama")["items"][0])
            probe.assert_not_called()
            checked = manage_route(self.controller, {"service": "ollama", "action": "check", "id": "current"})
            self.assertEqual(checked, result)
            probe.assert_called_once_with("192.0.2.20", 7000)
            item = list_routes(self.controller, "ollama")["items"][0]
            self.assertEqual(item["connectivity"], result)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.save(id="current", fields={"server_addr": "192.0.2.99"})
        self.assertNotIn("connectivity", list_routes(self.controller, "ollama")["items"][0])
        with self.controller.route_check_lock, patch("app.route_connectivity.probe_node") as probe:
            with self.assertRaises(UserError):
                manage_route(self.controller, {"service": "ollama", "action": "check", "id": "current"})
            probe.assert_not_called()
        for invalid in ({"id": "not-registered"}, {"id": "current", "fields": {"server_addr": "attacker.invalid"}}):
            with self.assertRaises(UserError):
                manage_route(self.controller, {"service": "ollama", "action": "check", **invalid})

    def test_invalid_location_does_not_mutate_any_settings(self):
        before = self.config_path.read_bytes()
        for fields in ({"country": "Japan"}, {"city": "bad\ncity"}, {"country": 1}):
            with self.assertRaises(UserError):
                self.save(id="current", fields=fields)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_auto_location_for_saved_and_draft_nodes_is_passive_and_endpoint_bound(self):
        result = {"state": "ok", "country": "JP", "country_name": "日本", "region": "东京都", "city": "东京",
                  "source": "ipwho.is", "cached": False, "checked_at": "2026-11-20T00:00:00Z"}
        before_settings, before_live, before_other = self.config_path.read_bytes(), self.live.read_bytes(), self.other.read_bytes()
        with patch("app.route_connectivity.lookup_location", return_value=result) as lookup, \
                patch.object(self.controller.tunnel, "start", side_effect=AssertionError("must not start")), \
                patch.object(self.controller.tunnel, "stop", side_effect=AssertionError("must not stop")):
            self.assertNotIn("location", list_routes(self.controller, "ollama")["items"][0])
            lookup.assert_not_called()
            located = manage_route(self.controller, {"service": "ollama", "action": "location", "id": "current"})
            self.assertEqual(located, result)
            lookup.assert_called_once_with("192.0.2.20")
            located["city"] = "must not mutate cached location"
            item = list_routes(self.controller, "ollama")["items"][0]
            self.assertEqual((item["country"], item["city"], item["location_source"]), ("JP", "东京", "ipwho.is"))
            self.assertEqual(lookup.call_count, 1)
            manage_route(self.controller, {"service": "ollama", "action": "location", "server_addr": "192.0.2.99"})
            self.assertEqual(lookup.call_args.args, ("192.0.2.99",))
        self.assertEqual(self.config_path.read_bytes(), before_settings)
        self.assertEqual(self.live.read_bytes(), before_live)
        self.assertEqual(self.other.read_bytes(), before_other)
        self.save(id="current", fields={"server_addr": "192.0.2.55"})
        changed = list_routes(self.controller, "ollama")["items"][0]
        self.assertNotIn("location", changed)
        self.assertEqual((changed["country"], changed["city"]), ("", ""))

    def test_check_exposes_auto_location_and_keeps_manual_overrides(self):
        self.save(id="current", fields={"country": "US", "city": "手工城市"})
        location = {"state": "ok", "country": "JP", "city": "东京", "source": "ipwho.is"}
        result = {"ping": {"state": "ok", "latency_ms": 1}, "tcp": {"state": "ok", "latency_ms": 2},
                  "location": location, "checked_at": "2026-11-20T00:00:00Z"}
        with patch("app.route_connectivity.probe_node", return_value=result):
            manage_route(self.controller, {"service": "ollama", "action": "check", "id": "current"})
        item = list_routes(self.controller, "ollama")["items"][0]
        self.assertEqual(item["location"], location)
        self.assertEqual((item["country"], item["city"], item["location_source"]), ("US", "手工城市", "manual"))

    def test_location_action_rejects_ambiguous_inputs_unknown_nodes_and_busy_checks(self):
        invalid = [{}, {"id": "current", "server_addr": "8.8.8.8"}, {"id": None}, {"id": "unknown"},
                   {"server_addr": None}, {"server_addr": "https://vps.example"},
                   {"server_addr": "8.8.8.8", "token": TOKEN}, {"id": "current", "fields": {}}]
        before = self.config_path.read_bytes()
        with patch("app.route_connectivity.lookup_location") as lookup:
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaises(UserError):
                    manage_route(self.controller, {"service": "ollama", "action": "location", **fields})
            with self.controller.route_check_lock, self.assertRaises(UserError):
                manage_route(self.controller, {"service": "ollama", "action": "location", "id": "current"})
            lookup.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_registered_log_file_is_editable_but_only_managed_copy_uses_console(self):
        raw = visitor().replace('to = "console"', 'to = "/PRIVATE/existing-project/frpc.log"')
        self.live.write_text(raw, encoding="utf-8")
        before = self.live.read_bytes()
        current = next(item for item in list_routes(self.controller, "ollama")["items"] if item["active"])
        self.assertTrue(current["editable"])
        self.assertIn("控制台接管", current["notice"])
        self.assertNotIn("/PRIVATE", json.dumps(current))
        result = self.save(id="current", name="现用线路副本", fields={"server_port": 7443})
        self.assertFalse(result["created_copy"])
        document = tomllib.loads(self.registered_file("ollama", result["saved_id"]).read_text(encoding="utf-8"))
        self.assertEqual(document["log"]["to"], "console")
        self.assertEqual(self.live.read_bytes(), before)
        with patch("app.route_profiles.sys.platform", "darwin"), \
                patch("app.route_profiles.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            manage_route(self.controller, {"service": "ollama", "action": "copy", "id": "current"})
        self.assertEqual(tomllib.loads(run.call_args.kwargs["input"].decode())["log"]["to"], "console")
        with self.assertRaises(UserError):
            self.save(name="未登记导入", visitor_toml=raw)
        self.assertEqual(self.live.read_bytes(), before)

    def test_new_manual_profile_requires_credentials_and_does_not_apply(self):
        fields = {"server_addr": "vps.example.invalid", "server_port": 7001, "server_name": "ollama"}
        original = self.config_path.read_bytes()
        with self.assertRaises(UserError):
            self.save(name="缺失认证", fields=fields)
        self.assertEqual(self.config_path.read_bytes(), original)
        result = self.save(name="新 VPS", fields=dict(fields, token=TOKEN, secret_key=SECRET))
        self.assertEqual(self.controller.config["selected_model_route"], "current")
        self.assertNotEqual(result["saved_id"], "current")

    def test_import_windows_visitor_normalizes_safe_defaults_and_reuses_sub2_adapter(self):
        before = self.sub.read_bytes()
        result = self.save("sub2api", name="新 Sub2 VPS", visitor_toml=visitor(18081, "192.0.2.51", "sub2api_ke"))
        path = self.registered_file("sub2api", result["saved_id"])
        profile = self.controller.config["sub2api_profiles"][result["saved_id"]]
        for field in ("frpc", "key_file", "base_url", "health_url", "port", "model"):
            self.assertEqual(profile[field], self.config["sub2api"][field])
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(document["transport"]["tls"]["enable"])
        self.assertTrue(document["visitors"][0]["transport"]["useEncryption"])
        self.assertEqual(document["visitors"][0]["bindPort"], 18081)
        self.assertNotIn(self.key.read_text(encoding="utf-8"), path.read_text(encoding="utf-8"))
        self.assertEqual(self.sub.read_bytes(), before)
        self.assertEqual(self.controller.config["selected_route"], "current-sub")

    def test_duplicate_then_delete_leaves_private_files_and_active_profile_intact(self):
        result = manage_route(self.controller, {"service": "ollama", "action": "duplicate", "id": "current", "name": "复制 VPS"})
        self.assertTrue(result["created_copy"])
        path = self.registered_file("ollama", result["saved_id"])
        original = path.read_bytes()
        manage_route(self.controller, {"service": "ollama", "action": "delete", "id": result["saved_id"]})
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn(result["saved_id"], self.controller.config["ollama_routes"])
        with self.assertRaises(UserError):
            manage_route(self.controller, {"service": "ollama", "action": "delete", "id": "current"})

    def test_copy_only_writes_pbcopy_and_never_returns_private_text(self):
        before = self.config_path.read_bytes()
        with patch("app.route_profiles.sys.platform", "darwin"), \
                patch("app.route_profiles.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result = manage_route(self.controller, {"service": "ollama", "action": "copy", "id": "current"})
        self.assertEqual(run.call_args.args[0], ["/usr/bin/pbcopy"])
        document = tomllib.loads(run.call_args.kwargs["input"].decode())
        self.assertEqual(document["auth"]["token"], TOKEN)
        self.assertEqual(document["visitors"][0]["secretKey"], SECRET)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(self.config_path.read_bytes(), before)
        with patch("app.route_profiles.sys.platform", "linux"), patch("app.route_profiles.subprocess.run") as run:
            with self.assertRaises(UserError):
                manage_route(self.controller, {"service": "ollama", "action": "copy", "id": "current"})
        run.assert_not_called()

    def test_direct_profiles_are_list_only_and_unknown_paths_are_rejected(self):
        for action in ("save", "duplicate", "copy"):
            with self.subTest(action=action), self.assertRaises(UserError):
                manage_route(self.controller, {"service": "sub2api", "action": action, "id": "legacy-direct"})
        for fields in ({"visitor_config": "/fixture/injected-path"}, {"key_file": "/fixture/private-key"}):
            with self.assertRaises(UserError):
                self.save(fields=fields)
        with self.assertRaises(UserError):
            manage_route(self.controller, {"service": "ollama", "action": "save", "visitor_config": "/fixture/path"})

    def test_import_rejects_unsafe_and_unsupported_content_without_echo_or_mutation(self):
        original = self.config_path.read_bytes()
        base = visitor()
        candidates = [
            'includes = []\n' + base,
            'proxies = []\n' + base,
            'auth.tokenSource.type = "file"\nauth.tokenSource.file.path = "/PRIVATE/file"\n' + base,
            base + '\n[webServer]\naddr = "127.0.0.1"\nport = 9900\n',
            base + '\n[visitors.plugin]\ntype = "unix_domain_socket"\n',
            base.replace('to = "console"', 'to = "/PRIVATE/frpc.log"'),
            base.replace('transport.protocol = "tcp"', 'transport.protocol = "tcp"\ntransport.proxyURL = "http://PRIVATE"'),
            base.replace('transport.protocol = "tcp"', 'transport.protocol = "tcp"\ntransport.tls.certFile = "/PRIVATE/file"'),
            base.replace('transport.protocol = "tcp"', 'transport.protocol = "tcp"\ntransport.tls.enable = false'),
            base + '\n[visitors.transport]\nuseEncryption = false\n',
            base.replace('bindAddr = "127.0.0.1"', 'bindAddr = "0.0.0.0"'),
            base.replace('bindPort = 11436', 'bindPort = 18081'),
            base.replace('type = "stcp"', 'type = "xtcp"'),
            base.replace('192.0.2.20', 'https://PRIVATE@example.invalid/path'),
            base.replace(TOKEN, '{{ .Envs.PRIVATE_TOKEN }}'),
            base.replace(SECRET, '${PRIVATE_SECRET}'),
            base + '\n[[visitors]]\ntype="stcp"\n',
            'PRIVATE RAW INVALID TOML \n' + base,
            base + '\ntransport = "PRIVATE BAD TABLE"\n',
            base + 'x' * 65536,
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidates.index(candidate)), self.assertRaises(UserError) as error:
                self.save(name="测试", visitor_toml=candidate)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertNotIn(TOKEN, str(error.exception))
            self.assertNotIn(SECRET, str(error.exception))
            self.assertEqual(self.config_path.read_bytes(), original)

    def test_concurrent_settings_change_is_preserved_and_unregistered_file_removed(self):
        original = self.config_path.read_bytes()
        later = dict(self.config, unrelated={"later": "preserve"})
        def write(path, content):
            atomic_write(path, content)
            if path.suffix == ".toml":
                self.config_path.write_text(json.dumps(later), encoding="utf-8")
        with patch("app.route_profiles.atomic_write", side_effect=write):
            with self.assertRaises(UserError):
                self.save(name="新的 VPS", visitor_toml=visitor())
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8")), later)
        self.assertEqual(list((self.controller.runtime / "config/route-profiles").glob("*.toml")), [])
        self.assertTrue(any(p.read_bytes() == original for p in (self.controller.runtime / "backups").glob("*.json")))

    def test_settings_write_failure_cleans_only_new_file_and_preserves_live(self):
        original, live = self.config_path.read_bytes(), self.live.read_bytes()
        with patch("app.route_profiles.write_json", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.save(id="other", fields={"server_port": 7443})
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(self.live.read_bytes(), live)
        self.assertEqual(self.controller.config, self.config)
        self.assertEqual(list((self.controller.runtime / "config/route-profiles").glob("*.toml")), [])

    def test_preexisting_disk_edit_and_same_lock_block_all_writer_entry_points(self):
        with FileLock(settings_lock_path(self.controller)):
            for action in (lambda: self.save(name="测试", visitor_toml=visitor()),
                           lambda: self.controller.action("route", "current-sub"),
                           lambda: self.controller.action("model-route", "current")):
                with self.assertRaises(UserError):
                    action()
        later = dict(self.config, later_edit="preserve")
        self.config_path.write_text(json.dumps(later), encoding="utf-8")
        with self.assertRaises(UserError):
            self.save(name="测试", visitor_toml=visitor())
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8")), later)

    def test_managed_directory_symlink_and_source_symlink_rejected(self):
        target = self.root / "unrelated"
        target.mkdir()
        (self.controller.runtime / "config").symlink_to(target, target_is_directory=True)
        with self.assertRaises(UserError):
            self.save(name="测试", visitor_toml=visitor())
        self.assertEqual(list(target.iterdir()), [])
        self.other.unlink()
        self.other.symlink_to(self.live)
        result = list_routes(self.controller, "ollama")
        self.assertFalse(next(item for item in result["items"] if item["id"] == "other")["editable"])
        with self.assertRaises(UserError):
            self.save(id="other", fields={"server_port": 7001})

    def test_api_routes_same_origin_csrf_and_safe_results(self):
        server = LocalHTTPServer(("127.0.0.1", 0), Handler)
        server.controller, server.authority, server.token = self.controller, f"127.0.0.1:{server.server_port}", "fixture-csrf"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = "http://" + server.authority
        data = json.dumps({"service": "ollama", "action": "save", "name": "接口 VPS", "visitor_toml": visitor()}).encode()
        try:
            with op.open(base + "/api/routes?service=ollama", timeout=2) as response:
                self.assertNotIn(TOKEN, response.read().decode())
            for headers in ({}, {"X-Local-CSRF": server.token, "Origin": "https://attacker.invalid"}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    op.open(urllib.request.Request(base + "/api/routes", data=data, headers=headers), timeout=2)
                self.assertEqual(error.exception.code, 403)
            with op.open(urllib.request.Request(base + "/api/routes", data=data,
                         headers={"X-Local-CSRF": server.token, "Content-Type": "application/json"}), timeout=2) as response:
                saved = json.load(response)
            self.assertIn("saved_id", saved)
            self.assertNotIn(TOKEN, json.dumps(saved))
            self.assertEqual(self.controller.config["selected_model_route"], "current")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
