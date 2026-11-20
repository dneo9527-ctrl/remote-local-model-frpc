"""Unified VPS transactions using temporary private files and fake services.

No test starts FRP, calls a model/geography API, or reads the Mac's settings.
"""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from app.core import UserError
from app.server import Controller
from app.vps_nodes import list_nodes, manage_node


def visitor(host, port, provider, token, secret):
    return (f'serverAddr = "{host}"\nserverPort = 7000\n'
            f'auth.method = "token"\nauth.token = "{token}"\n'
            'transport.tls.enable = true\n'
            'transport.heartbeatInterval = 30\n'
            '[[visitors]]\nname = "fixture-visitor"\ntype = "stcp"\n'
            f'serverName = "{provider}"\nsecretKey = "{secret}"\n'
            f'bindAddr = "127.0.0.1"\nbindPort = {port}\n'
            'transport.useEncryption = true\ntransport.useCompression = true\n')


class FakeTunnel:
    def __init__(self, label, running=False, events=None, on_start=None):
        self.label, self.running = label, running
        self.events = events if events is not None else []
        self.on_start = on_start
        self.starts = self.stops = 0

    def start(self):
        self.starts += 1
        self.events.append((self.label, "start"))
        if self.on_start:
            self.on_start()
        self.running = True

    def stop(self):
        self.stops += 1
        self.events.append((self.label, "stop"))
        self.running = False


class VpsNodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.o_live = self.root / "ollama-live.toml"
        self.s_live = self.root / "sub2-live.toml"
        self.o_backup = self.root / "ollama-backup.toml"
        self.s_backup = self.root / "sub2-backup.toml"
        for path, host, port, provider, token, secret in (
            (self.o_live, "192.0.2.10", 11436, "ollama_ke", "fixture-ollama-control", "fixture-ollama-secret"),
            (self.s_live, "192.0.2.20", 18081, "sub2api_ke", "fixture-sub2-control", "fixture-sub2-secret"),
            (self.o_backup, "192.0.2.30", 11436, "ollama_ke", "fixture-ollama-control", "fixture-ollama-secret"),
            (self.s_backup, "192.0.2.30", 18081, "sub2api_ke", "fixture-sub2-control", "fixture-sub2-secret"),
        ):
            path.write_text(visitor(host, port, provider, token, secret), encoding="utf-8")
        self.key = self.root / "sub2-downstream.key"
        self.key.write_text("fixture-downstream-never-expose", encoding="utf-8")
        self.binary = self.root / "frpc-not-executable"
        self.binary.write_text("fixture-only", encoding="utf-8")
        self.codex = self.root / "codex.toml"
        self.codex.write_text('model_provider="keep"\n[mcp_servers.keep]\ncommand="keep"\n', encoding="utf-8")
        sub2 = {"mode": "stcp", "port": 18081, "frpc": str(self.binary),
                "visitor_config": str(self.s_live), "key_file": str(self.key),
                "base_url": "http://127.0.0.1:18081/v1", "health_url": "http://127.0.0.1:18081/health",
                "model": "fixture-model"}
        self.initial = {
            "runtime": str(self.root / "runtime"), "automatic_health_checks": False,
            "codex_config": str(self.codex), "include_legacy_integrations": False,
            "ollama": {"url": "http://127.0.0.1:11435", "manager": "/fixture/no-real-manager",
                       "python": "/fixture/no-real-python", "visitor_config": str(self.o_live)},
            "selected_model_route": "o-current", "selected_route": "s-current",
            "ollama_routes": {
                "o-current": {"name": "原 Ollama VPS", "visitor_config": str(self.o_live), "extra": "keep-ollama"},
                "o-backup": {"name": "共同备用 VPS", "visitor_config": str(self.o_backup), "extra": "keep-backup"},
            },
            "sub2api": sub2,
            "sub2api_profiles": {
                "s-current": dict(sub2, display_name="原 Sub2 VPS"),
                "s-backup": dict(sub2, display_name="共同备用 VPS", visitor_config=str(self.s_backup)),
            },
            "unrelated": {"MCP": "keep", "permissions": ["keep"], "official_login": "keep"},
        }
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps(self.initial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.controller = Controller(self.settings)
        self.initial_bytes = self.snapshot()

    def tearDown(self):
        self.controller.pool.shutdown(wait=True)
        self.temp.cleanup()

    def snapshot(self):
        return {path: path.read_bytes() for path in
                (self.settings, self.o_live, self.s_live, self.o_backup, self.s_backup, self.codex, self.key)}

    def node(self, host):
        return next(item for item in list_nodes(self.controller)["items"] if item["server_addr"] == host)

    @contextmanager
    def services(self, ollama=True, sub2=True, new_start=None, ollama_action=None):
        state = {"ollama": ollama, "events": [], "ollama_starts": [], "replacements": []}
        previous = FakeTunnel("old-sub2", sub2, state["events"])
        self.controller.tunnel = previous

        def action(service, operation):
            self.assertEqual(service, "ollama")
            self.assertIn(operation, ("start", "stop"))
            state["events"].append((service, operation))
            if ollama_action:
                ollama_action(operation)
            if operation == "start":
                state["ollama_starts"].append(tomllib.loads(self.o_live.read_text(encoding="utf-8"))["serverAddr"])
            state["ollama"] = operation == "start"

        def make_tunnel(settings, runtime):
            self.assertEqual(runtime, self.controller.runtime)
            replacement = FakeTunnel("new-sub2", False, state["events"], new_start)
            replacement.settings = settings
            state["replacements"].append(replacement)
            return replacement

        with patch.object(self.controller, "action", side_effect=action), \
                patch.object(self.controller, "ollama_running", side_effect=lambda: state["ollama"]), \
                patch("app.vps_nodes.Tunnel", side_effect=make_tunnel), \
                patch("app.vps_nodes.port_listening", return_value=False):
            yield state, previous

    def apply(self, host="192.0.2.30"):
        return manage_node(self.controller, {"action": "apply", "id": self.node(host)["id"]})

    def assert_applied(self, ollama, sub2):
        self.assertEqual(tomllib.loads(self.o_live.read_text(encoding="utf-8"))["serverAddr"], "192.0.2.30")
        self.assertEqual(self.controller.config["selected_model_route"], "o-backup")
        self.assertEqual(self.controller.config["selected_route"], "s-backup")
        self.assertEqual(self.controller.config["sub2api"]["visitor_config"], str(self.s_backup))
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), self.controller.config)
        self.assertEqual(self.controller.config["unrelated"], self.initial["unrelated"])
        self.assertEqual(self.controller.tunnel.running, sub2)
        self.assertEqual(self.codex.read_bytes(), self.initial_bytes[self.codex])

    def test_readonly_list_merges_duplicate_endpoint_and_reports_split_current_without_mutation(self):
        with patch("app.route_connectivity.probe_node", side_effect=AssertionError("no network")), \
                patch("app.route_connectivity.lookup_location", side_effect=AssertionError("no geography")):
            listed = list_nodes(self.controller)
            again = list_nodes(self.controller)
        self.assertEqual(len(listed["items"]), 3)
        self.assertEqual({item["server_addr"] for item in listed["items"]}, {"192.0.2.10", "192.0.2.20", "192.0.2.30"})
        self.assertTrue(listed["split"])
        self.assertIsNone(listed["selected"])
        self.assertNotEqual(listed["current"]["ollama"], listed["current"]["sub2api"])
        backup = self.node("192.0.2.30")
        self.assertTrue(backup["services"]["ollama"]["configured"])
        self.assertTrue(backup["services"]["sub2api"]["configured"])
        self.assertEqual([item["id"] for item in listed["items"]], [item["id"] for item in again["items"]])
        self.assertEqual(self.snapshot(), self.initial_bytes)
        self.assertEqual(self.controller.config, self.initial)
        public = json.dumps(listed)
        for private in ("fixture-ollama-control", "fixture-sub2-control", "fixture-ollama-secret",
                        "fixture-sub2-secret", self.key.read_text(encoding="utf-8"), str(self.root)):
            self.assertNotIn(private, public)

    def test_new_node_inherits_both_current_templates_without_applying_or_mixing_keys(self):
        old_o, old_s = set(self.initial["ollama_routes"]), set(self.initial["sub2api_profiles"])
        with self.services() as (state, _):
            saved = manage_node(self.controller, {"action": "save", "name": "新共同备用", "fields": {"server_addr": "192.0.2.44"}})
        self.assertEqual(state["events"], [])
        self.assertEqual(state["replacements"], [])
        self.assertIn("saved_id", saved)
        created_o = set(self.controller.config["ollama_routes"]) - old_o
        created_s = set(self.controller.config["sub2api_profiles"]) - old_s
        self.assertEqual((len(created_o), len(created_s)), (1, 1))
        entries = [self.controller.config["ollama_routes"][created_o.pop()],
                   self.controller.config["sub2api_profiles"][created_s.pop()]]
        for entry, original, provider, token, secret, local_port in (
            (entries[0], self.o_live, "ollama_ke", "fixture-ollama-control", "fixture-ollama-secret", 11436),
            (entries[1], self.s_live, "sub2api_ke", "fixture-sub2-control", "fixture-sub2-secret", 18081),
        ):
            document = tomllib.loads(Path(entry["visitor_config"]).read_text(encoding="utf-8"))
            self.assertEqual(document["serverAddr"], "192.0.2.44")
            self.assertEqual(document["auth"]["token"], token)
            self.assertEqual(document["visitors"][0]["serverName"], provider)
            self.assertEqual(document["visitors"][0]["secretKey"], secret)
            self.assertEqual(document["visitors"][0]["bindPort"], local_port)
            self.assertTrue(document["transport"]["tls"]["enable"])
            self.assertTrue(document["visitors"][0]["transport"]["useEncryption"])
            self.assertEqual(original.read_bytes(), self.initial_bytes[original])
        self.assertEqual(entries[1]["key_file"], str(self.key))
        self.assertEqual(entries[1]["base_url"], self.initial["sub2api"]["base_url"])
        self.assertEqual(self.controller.config["selected_model_route"], "o-current")
        self.assertEqual(self.controller.config["selected_route"], "s-current")
        self.assertEqual(self.controller.config["unrelated"], self.initial["unrelated"])

    def test_apply_from_split_current_restarts_both_running_services_on_one_node(self):
        with self.services() as (state, old_sub2):
            self.apply()
            self.assertTrue(state["ollama"])
        self.assert_applied(True, True)
        self.assertFalse(old_sub2.running)
        self.assertEqual(state["ollama_starts"], ["192.0.2.30"])
        self.assertEqual(len(state["replacements"]), 1)
        self.assertEqual(state["replacements"][0].starts, 1)
        listed = list_nodes(self.controller)
        self.assertFalse(listed["split"])
        self.assertEqual(listed["current"]["ollama"], listed["current"]["sub2api"])
        self.assertEqual({item["server_addr"] for item in listed["items"]},
                         {"192.0.2.10", "192.0.2.20", "192.0.2.30"})
        former = self.node("192.0.2.10")
        self.assertTrue(former["services"]["ollama"]["configured"])
        self.assertFalse(former["services"]["ollama"]["active"])
        preserved = Path(self.controller.config["ollama_routes"]["o-current"]["visitor_config"])
        self.assertNotEqual(preserved, self.o_live)
        preserved_document = tomllib.loads(preserved.read_text(encoding="utf-8"))
        original_document = tomllib.loads(self.initial_bytes[self.o_live].decode("utf-8"))
        self.assertEqual(preserved_document["serverAddr"], "192.0.2.10")
        self.assertEqual(preserved_document["auth"], original_document["auth"])
        self.assertEqual(preserved_document["visitors"], original_document["visitors"])

    def test_apply_keeps_sub2_off_and_restarts_only_running_ollama(self):
        with self.services(ollama=True, sub2=False) as (state, _):
            self.apply()
            self.assertTrue(state["ollama"])
        self.assert_applied(True, False)
        self.assertTrue(all(tunnel.starts == 0 for tunnel in state["replacements"]))
        self.assertEqual(state["ollama_starts"], ["192.0.2.30"])

    def test_apply_keeps_ollama_off_and_restarts_only_running_sub2(self):
        with self.services(ollama=False, sub2=True) as (state, _):
            self.apply()
            self.assertFalse(state["ollama"])
        self.assert_applied(False, True)
        self.assertEqual(state["ollama_starts"], [])

    def test_apply_while_both_services_off_does_not_start_either(self):
        with self.services(ollama=False, sub2=False) as (state, _):
            self.apply()
            self.assertFalse(state["ollama"])
        self.assert_applied(False, False)
        self.assertEqual(state["ollama_starts"], [])
        self.assertTrue(all(tunnel.starts == 0 for tunnel in state["replacements"]))

    def test_second_service_failure_restores_both_original_targets_settings_and_running_state(self):
        def fail_new_sub2():
            raise UserError("fixture second service failed")
        with self.services(new_start=fail_new_sub2) as (state, previous):
            with self.assertRaises(UserError):
                self.apply()
            self.assertTrue(state["ollama"])
        self.assertEqual(self.snapshot(), self.initial_bytes)
        self.assertEqual(self.controller.config, self.initial)
        self.assertIs(self.controller.tunnel, previous)
        self.assertTrue(previous.running)
        self.assertEqual(state["ollama_starts"], ["192.0.2.30", "192.0.2.10"])
        self.assertTrue(all(not tunnel.running for tunnel in state["replacements"]))

    def test_preexisting_settings_conflict_is_preserved_before_any_service_stop(self):
        external = dict(self.initial, unrelated={"external": "keep"})
        self.settings.write_text(json.dumps(external) + "\n", encoding="utf-8")
        before = self.snapshot()
        with self.services() as (state, previous):
            with self.assertRaises(UserError):
                self.apply()
            self.assertTrue(state["ollama"])
            self.assertTrue(previous.running)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["replacements"], [])
        self.assertEqual(self.snapshot(), before)

    def test_external_settings_edit_during_failed_restart_is_preserved_and_blocks_reuse(self):
        external_bytes = []
        def edit_and_fail():
            external = copy.deepcopy(self.controller.config)
            external["unrelated"] = {"external": "changed during new start"}
            external_bytes.append((json.dumps(external) + "\n").encode("utf-8"))
            self.settings.write_bytes(external_bytes[-1])
            raise UserError("fixture restart failed during external edit")
        with self.services(new_start=edit_and_fail) as (state, previous):
            with self.assertRaises(UserError):
                self.apply()
            self.assertFalse(state["ollama"])
        self.assertEqual(self.settings.read_bytes(), external_bytes[-1])
        self.assertEqual(self.controller.config, self.initial)
        self.assertIs(self.controller.tunnel, previous)
        self.assertFalse(previous.running)
        self.assertTrue(self.controller.connection_settings_conflicted)
        self.assertTrue(all(not tunnel.running for tunnel in state["replacements"]))

    def test_active_chat_refuses_unified_apply_without_stopping_or_writing(self):
        self.controller.active_chats = 1
        with self.services() as (state, previous):
            with self.assertRaises(UserError):
                self.apply()
            self.assertTrue(state["ollama"])
            self.assertTrue(previous.running)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["replacements"], [])
        self.assertEqual(self.snapshot(), self.initial_bytes)

    def test_advanced_fields_keep_service_specific_overrides_and_private_results(self):
        saved = manage_node(self.controller, {
            "action": "save", "name": "高级备用", "fields": {
                "common": {"server_addr": "192.0.2.45", "server_port": 7443, "country": "jp", "city": "东京",
                           "token": "fixture-common-control"},
                "services": {"ollama": {"server_name": "ollama_backup", "secret_key": "fixture-o-backup"},
                             "sub2api": {"server_name": "sub2api_backup", "secret_key": "fixture-s-backup",
                                         "token": "fixture-s-override"}},
            },
        })
        node = self.node("192.0.2.45")
        self.assertEqual((node["id"], node["server_port"], node["country"], node["city"]),
                         (saved["saved_id"], 7443, "JP", "东京"))
        for service, provider, secret, token, registry in (
            ("ollama", "ollama_backup", "fixture-o-backup", "fixture-common-control", "ollama_routes"),
            ("sub2api", "sub2api_backup", "fixture-s-backup", "fixture-s-override", "sub2api_profiles"),
        ):
            entry = self.controller.config[registry][node[service + "_id"]]
            document = tomllib.loads(Path(entry["visitor_config"]).read_text(encoding="utf-8"))
            self.assertEqual(document["auth"]["token"], token)
            self.assertEqual(document["visitors"][0]["serverName"], provider)
            self.assertEqual(document["visitors"][0]["secretKey"], secret)
            self.assertEqual(document["transport"]["heartbeatInterval"], 30)
        self.assertEqual(self.o_live.read_bytes(), self.initial_bytes[self.o_live])
        self.assertEqual(self.s_live.read_bytes(), self.initial_bytes[self.s_live])
        public = json.dumps([saved, list_nodes(self.controller)])
        for private in ("fixture-common-control", "fixture-s-override", "fixture-o-backup", "fixture-s-backup", str(self.key)):
            self.assertNotIn(private, public)

    def test_edit_group_keeps_all_alias_ids_and_original_private_files(self):
        duplicate = self.root / "ollama-backup-alias.toml"
        alias_text = visitor("192.0.2.30", 11436, "ollama_alias", "fixture-alias-control", "fixture-alias-secret")
        duplicate.write_text(alias_text, encoding="utf-8")
        self.controller.config["ollama_routes"]["o-alias"] = {
            "name": "旧别名", "visitor_config": str(duplicate), "extra": "preserve alias metadata"}
        self.settings.write_text(json.dumps(self.controller.config), encoding="utf-8")
        before = copy.deepcopy(self.controller.config)
        identity = self.node("192.0.2.30")["id"]
        result = manage_node(self.controller, {"action": "save", "id": identity, "name": "更新同节点", "fields": {
            "server_addr": "192.0.2.46", "server_port": 7443}})
        self.assertNotEqual(result["saved_id"], identity)
        for registry in ("ollama_routes", "sub2api_profiles"):
            self.assertEqual(set(self.controller.config[registry]), set(before[registry]))
        for registry, alias in (("ollama_routes", "o-backup"), ("ollama_routes", "o-alias"),
                                ("sub2api_profiles", "s-backup")):
            current = self.controller.config[registry][alias]
            self.assertNotEqual(current["visitor_config"], before[registry][alias]["visitor_config"])
            updated = tomllib.loads(Path(current["visitor_config"]).read_text(encoding="utf-8"))
            prior = tomllib.loads(Path(before[registry][alias]["visitor_config"]).read_text(encoding="utf-8"))
            self.assertEqual((updated["serverAddr"], updated["serverPort"]), ("192.0.2.46", 7443))
            self.assertEqual(updated["auth"], prior["auth"])
            self.assertEqual(updated["visitors"], prior["visitors"])
        self.assertEqual(duplicate.read_text(encoding="utf-8"), alias_text)
        self.assertEqual(self.o_backup.read_bytes(), self.initial_bytes[self.o_backup])
        self.assertEqual(self.s_backup.read_bytes(), self.initial_bytes[self.s_backup])
        self.assertEqual(self.controller.config["ollama_routes"]["o-alias"]["extra"], "preserve alias metadata")
        self.assertEqual(self.controller.config["selected_model_route"], "o-current")
        self.assertEqual(self.controller.config["selected_route"], "s-current")

    def test_delete_removes_all_endpoint_aliases_but_never_private_files(self):
        duplicate = self.root / "sub2-backup-alias.toml"
        duplicate.write_bytes(self.s_backup.read_bytes())
        self.controller.config["sub2api_profiles"]["s-alias"] = dict(
            self.controller.config["sub2api_profiles"]["s-backup"], visitor_config=str(duplicate))
        self.settings.write_text(json.dumps(self.controller.config), encoding="utf-8")
        before = self.snapshot()
        alias_bytes = duplicate.read_bytes()
        identity = self.node("192.0.2.30")["id"]
        manage_node(self.controller, {"action": "delete", "id": identity})
        self.assertNotIn("o-backup", self.controller.config["ollama_routes"])
        self.assertNotIn("s-backup", self.controller.config["sub2api_profiles"])
        self.assertNotIn("s-alias", self.controller.config["sub2api_profiles"])
        self.assertEqual(duplicate.read_bytes(), alias_bytes)
        for path, content in before.items():
            if path != self.settings:
                self.assertEqual(path.read_bytes(), content)
        self.assertEqual(self.controller.config["selected_model_route"], "o-current")
        self.assertEqual(self.controller.config["selected_route"], "s-current")

    def test_either_current_service_prevents_node_deletion(self):
        for host in ("192.0.2.10", "192.0.2.20"):
            with self.subTest(host=host), self.assertRaises(UserError):
                manage_node(self.controller, {"action": "delete", "id": self.node(host)["id"]})
        self.assertEqual(self.snapshot(), self.initial_bytes)

    def test_apply_inherits_missing_sub2_profile_from_current_preserving_closed_state(self):
        self.assertFalse(self.node("192.0.2.10")["services"]["sub2api"]["configured"])
        with self.services(ollama=True, sub2=False) as (state, _):
            self.apply("192.0.2.10")
            self.assertTrue(state["ollama"])
        self.assertFalse(self.controller.tunnel.running)
        sub2 = self.controller.config["sub2api"]
        document = tomllib.loads(Path(sub2["visitor_config"]).read_text(encoding="utf-8"))
        self.assertEqual(document["serverAddr"], "192.0.2.10")
        self.assertEqual(document["visitors"][0]["serverName"], "sub2api_ke")
        self.assertEqual(document["visitors"][0]["secretKey"], "fixture-sub2-secret")
        self.assertEqual(document["auth"]["token"], "fixture-sub2-control")
        self.assertEqual(sub2["key_file"], str(self.key))
        self.assertEqual(self.s_live.read_bytes(), self.initial_bytes[self.s_live])
        listed = list_nodes(self.controller)
        self.assertFalse(listed["split"])
        self.assertEqual(listed["selected"], self.node("192.0.2.10")["id"])

    def test_copy_delegates_only_to_matching_service_safe_clipboard_operation(self):
        identity = self.node("192.0.2.30")["id"]
        with patch("app.vps_nodes.manage_route", return_value={"message": "copied locally"}) as safe_copy:
            result = manage_node(self.controller, {"action": "copy", "id": identity, "service": "sub2api"})
        safe_copy.assert_called_once_with(self.controller, {"service": "sub2api", "action": "copy", "id": "s-backup"})
        self.assertEqual(result, {"message": "copied locally"})
        self.assertEqual(self.snapshot(), self.initial_bytes)

    def test_unknown_or_malformed_requests_never_mutate_files_or_services(self):
        identity = self.node("192.0.2.30")["id"]
        invalid = [None, [], {}, {"action": []}, {"action": "unknown"},
                   {"action": "apply", "id": identity, "server_addr": "192.0.2.99"},
                   {"action": "delete", "id": "missing"},
                   {"action": "copy", "id": identity, "service": "unknown"},
                   {"action": "save", "fields": {"common": {"server_addr": "192.0.2.99", "key_file": "unexpected"}}},
                   {"action": "save", "fields": {"common": {"server_addr": "192.0.2.99"}, "services": {"other": {}}}},
                   {"action": "save", "fields": {"server_addr": "192.0.2.99", "services": {"sub2api": {"key_file": "unexpected"}}}},
                   {"action": "save", "fields": {"server_addr": "https://invalid.example"}},
                   {"action": "save", "fields": {"server_addr": "192.0.2.99", "server_port": True}}]
        with self.services() as (state, _):
            for data in invalid:
                with self.subTest(data=data), self.assertRaises(UserError):
                    manage_node(self.controller, data)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["replacements"], [])
        self.assertEqual(self.snapshot(), self.initial_bytes)

    def test_duplicate_inherits_selected_node_templates_instead_of_current_templates(self):
        advanced = manage_node(self.controller, {"action": "save", "name": "特殊备用", "fields": {
            "common": {"server_addr": "192.0.2.47", "server_port": 7443, "token": "fixture-selected-control"},
            "services": {"ollama": {"server_name": "ollama_special", "secret_key": "fixture-selected-o"},
                         "sub2api": {"server_name": "sub2_special", "secret_key": "fixture-selected-s"}},
        }})
        copied = manage_node(self.controller, {"action": "duplicate", "id": advanced["saved_id"],
                                               "name": "特殊副本", "fields": {"server_addr": "192.0.2.48"}})
        self.assertNotEqual(copied["saved_id"], advanced["saved_id"])
        self.assertTrue(copied["created_copy"])
        duplicate = self.node("192.0.2.48")
        self.assertEqual(duplicate["server_port"], 7443)
        for service, registry, provider, secret in (
            ("ollama", "ollama_routes", "ollama_special", "fixture-selected-o"),
            ("sub2api", "sub2api_profiles", "sub2_special", "fixture-selected-s"),
        ):
            entry = self.controller.config[registry][duplicate[service + "_id"]]
            document = tomllib.loads(Path(entry["visitor_config"]).read_text(encoding="utf-8"))
            self.assertEqual(document["auth"]["token"], "fixture-selected-control")
            self.assertEqual(document["visitors"][0]["serverName"], provider)
            self.assertEqual(document["visitors"][0]["secretKey"], secret)
        self.assertEqual(self.controller.config["selected_model_route"], "o-current")
        self.assertEqual(self.controller.config["selected_route"], "s-current")

    def test_single_visitor_import_identifies_sub2_port_and_inherits_other_service(self):
        imported = visitor("192.0.2.49", 18081, "sub2_imported", "fixture-import-control", "fixture-import-secret")
        result = manage_node(self.controller, {"action": "save", "name": "导入备用", "visitor_toml": imported})
        node = self.node("192.0.2.49")
        self.assertEqual(node["id"], result["saved_id"])
        for service, registry, provider, token in (
            ("ollama", "ollama_routes", "ollama_ke", "fixture-ollama-control"),
            ("sub2api", "sub2api_profiles", "sub2_imported", "fixture-import-control"),
        ):
            entry = self.controller.config[registry][node[service + "_id"]]
            document = tomllib.loads(Path(entry["visitor_config"]).read_text(encoding="utf-8"))
            self.assertEqual(document["serverAddr"], "192.0.2.49")
            self.assertEqual(document["visitors"][0]["serverName"], provider)
            self.assertEqual(document["auth"]["token"], token)
        self.assertEqual(self.o_live.read_bytes(), self.initial_bytes[self.o_live])
        self.assertEqual(self.s_live.read_bytes(), self.initial_bytes[self.s_live])
        self.assertEqual(self.controller.config["sub2api_profiles"][node["sub2api_id"]]["key_file"], str(self.key))

    def test_existing_endpoint_rejects_new_or_duplicate_without_registration_changes(self):
        current_id = self.node("192.0.2.30")["id"]
        for data in ({"action": "save", "fields": {"server_addr": "192.0.2.30"}},
                     {"action": "duplicate", "id": current_id},
                     {"action": "duplicate", "id": current_id, "fields": {"server_addr": "192.0.2.10"}}):
            with self.subTest(data=data), self.assertRaises(UserError):
                manage_node(self.controller, data)
        self.assertEqual(self.snapshot(), self.initial_bytes)
        self.assertEqual(self.controller.config, self.initial)

    def test_corrupt_or_removed_external_settings_after_failure_remain_untouched_and_lock_reuse(self):
        # All variants represent a concurrent writer in progress. None can be
        # used to decide that newly created private files are safe to remove.
        for external in (b'[]', b'{"partial":', None):
            with self.subTest(external=external):
                self.settings.write_bytes(self.initial_bytes[self.settings])
                self.controller.config = copy.deepcopy(self.initial)
                self.controller.connection_settings_conflicted = False
                self.o_live.write_bytes(self.initial_bytes[self.o_live])
                def change_and_fail():
                    if external is None:
                        self.settings.unlink()
                    else:
                        self.settings.write_bytes(external)
                    raise UserError("fixture failure during external edit")
                with self.services(new_start=change_and_fail) as (state, previous):
                    with self.assertRaises(UserError):
                        self.apply()
                    self.assertFalse(state["ollama"])
                if external is None:
                    self.assertFalse(self.settings.exists())
                else:
                    self.assertEqual(self.settings.read_bytes(), external)
                self.assertEqual(self.controller.config, self.initial)
                self.assertIs(self.controller.tunnel, previous)
                self.assertFalse(previous.running)
                self.assertTrue(self.controller.connection_settings_conflicted)
                self.assertTrue(all(not tunnel.running for tunnel in state["replacements"]))

    def test_external_visitor_edit_during_failure_is_preserved_without_restart(self):
        external_visitor = visitor("192.0.2.88", 11436, "external_provider", "fixture-external", "fixture-external-secret").encode("utf-8")
        def change_visitor_and_fail():
            self.o_live.write_bytes(external_visitor)
            raise UserError("fixture failure with later visitor edit")
        with self.services(new_start=change_visitor_and_fail) as (state, previous):
            with self.assertRaises(UserError):
                self.apply()
            self.assertFalse(state["ollama"])
        self.assertEqual(self.o_live.read_bytes(), external_visitor)
        self.assertEqual(self.settings.read_bytes(), self.initial_bytes[self.settings])
        self.assertEqual(self.controller.config, self.initial)
        self.assertIs(self.controller.tunnel, previous)
        self.assertFalse(previous.running)
        self.assertTrue(self.controller.connection_settings_conflicted)


if __name__ == "__main__":
    unittest.main()
