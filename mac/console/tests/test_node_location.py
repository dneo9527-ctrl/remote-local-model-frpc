"""Public-node geography tests; all service replies and node DNS are isolated."""
from __future__ import annotations

import json
import socket
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from app.core import UserError
from app import route_connectivity as connectivity


PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 7000))
BODY = {"success": True, "country_code": "jp", "country": "日本", "region": "东京都", "city": "东京"}
POLICY = {"schema": 1, "geo": {"endpoint": "https://ipwho.is/",
          "fields": ["success", "country_code", "country", "region", "city"], "language": "zh-CN",
          "success_ttl_seconds": 604800, "failure_ttl_seconds": 300, "timeout_ttl_seconds": 30,
          "request_timeout_seconds": 2.5, "max_response_bytes": 16384}}


class NodeLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy_path = Path(self.temp.name) / "node-policy.json"
        self.policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
        policy_patch = patch.object(connectivity, "_POLICY_PATH", self.policy_path)
        policy_patch.start()
        self.addCleanup(policy_patch.stop)
        with connectivity._LOCATION_LOCK:
            connectivity._LOCATION_CACHE.clear()
            connectivity._LOCATION_PENDING.clear()

    def fetch(self, body=BODY, *, status=200, raw=None, error=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = status
        response.read.return_value = raw if raw is not None else json.dumps(body).encode()
        opener = MagicMock()
        opener.open.return_value = response
        if error:
            opener.open.side_effect = error
        with patch.object(connectivity.urllib.request, "build_opener", return_value=opener) as build:
            result = connectivity._fetch_location("8.8.8.8")
        return result, opener, build, response

    def test_fixed_https_request_contains_only_numeric_node_and_no_credentials(self):
        result, opener, build, response = self.fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual((result["country"], result["country_name"], result["city"]), ("JP", "日本", "东京"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://ipwho.is/8.8.8.8?lang=zh-CN&fields=success,country_code,country,region,city")
        self.assertEqual(dict(request.header_items()), {"Accept": "application/json"})
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], POLICY["geo"]["request_timeout_seconds"])
        self.assertEqual(response.read.call_args.args, (POLICY["geo"]["max_response_bytes"] + 1,))
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], connectivity._NoRedirect)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "http://untrusted.invalid"))
        self.assertTrue(result["checked_at"].endswith("Z"))
        self.assertFalse(result["cached"])

    def test_all_local_reserved_documentation_and_transition_ips_skip_without_http(self):
        addresses = ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.2", "169.254.1.1", "100.64.0.1",
                     "0.0.0.0", "255.255.255.255", "224.0.0.1", "192.0.2.2", "198.51.100.2", "203.0.113.2",
                     "::", "::1", "fc00::1", "fe80::1", "ff02::1", "2001:db8::2", "::ffff:127.0.0.1",
                     "2002:7f00:1::", "not-an-IP"]
        with patch.object(connectivity.urllib.request, "build_opener") as build:
            for address in addresses:
                with self.subTest(address=address):
                    self.assertIsNone(connectivity._public_ip(address))
                    self.assertEqual(connectivity._fetch_location(address)["state"], "skipped")
        build.assert_not_called()

    def test_public_ipv6_and_mapped_public_are_numeric(self):
        self.assertEqual(connectivity._public_ip("2606:4700:4700::1111"), "2606:4700:4700::1111")
        self.assertEqual(connectivity._public_ip("::ffff:8.8.8.8"), "8.8.8.8")
        with patch.object(connectivity, "_fetch_location", return_value=connectivity._location_result("ok")) as fetch:
            connectivity.lookup_location("::ffff:8.8.8.8")
        fetch.assert_called_once_with("8.8.8.8", POLICY["geo"])

    def test_service_failures_and_malformed_metadata_return_no_raw_text(self):
        cases = [(None, 429, None), ({"success": False, "message": "PRIVATE"}, 200, None),
                 (["PRIVATE"], 200, None), (dict(BODY, country_code="PRIVATE"), 200, None),
                 (dict(BODY, city="PRIVATE\ncity"), 200, None), (dict(BODY, region="x" * 81), 200, None),
                 (dict(BODY, country=123), 200, None), (None, 200, b"PRIVATE NOT JSON"),
                 (None, 200, b"x" * (POLICY["geo"]["max_response_bytes"] + 1))]
        for body, status, raw in cases:
            with self.subTest(body=body, status=status, raw=bool(raw)):
                result, _, _, _ = self.fetch(body, status=status, raw=raw)
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(result["country"], "")
                self.assertNotIn("PRIVATE", json.dumps(result))
        for error, state in [(socket.timeout("PRIVATE"), "timeout"),
                             (urllib.error.URLError(socket.timeout("PRIVATE")), "timeout"),
                             (urllib.error.HTTPError("PRIVATE", 302, "PRIVATE", {}, None), "unknown"),
                             (OSError("PRIVATE"), "unknown")]:
            result, _, _, _ = self.fetch(error=error)
            self.assertEqual(result["state"], state)
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_cache_reuses_location_per_resolved_ip_and_expires(self):
        with patch.object(connectivity, "_fetch_location", return_value=connectivity._location_result("ok", country="JP")) as fetch:
            first = connectivity.lookup_location("8.8.8.8")
            second = connectivity.lookup_location("8.8.8.8")
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            fetch.assert_called_once_with("8.8.8.8", POLICY["geo"])
            with connectivity._LOCATION_LOCK:
                _, value = connectivity._LOCATION_CACHE["8.8.8.8"]
                connectivity._LOCATION_CACHE["8.8.8.8"] = (0, value)
            self.assertFalse(connectivity.lookup_location("8.8.8.8")["cached"])
            self.assertEqual(fetch.call_count, 2)
            connectivity.lookup_location("1.1.1.1")
            self.assertEqual(fetch.call_count, 3)

    def test_cache_is_bounded_and_returns_independent_objects(self):
        with patch.object(connectivity, "LOCATION_CACHE_LIMIT", 2), \
                patch.object(connectivity, "_fetch_location", return_value=connectivity._location_result("ok", country="JP")):
            first = connectivity.lookup_location("8.8.8.8")
            first["country"] = "BAD"
            self.assertEqual(connectivity.lookup_location("8.8.8.8")["country"], "JP")
            connectivity.lookup_location("1.1.1.1")
            connectivity.lookup_location("9.9.9.9")
        self.assertEqual(len(connectivity._LOCATION_CACHE), 2)
        self.assertNotIn("8.8.8.8", connectivity._LOCATION_CACHE)

    def test_hostname_uses_one_dns_and_sends_only_public_numeric_ip(self):
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 7000))
        with patch.object(connectivity.socket, "getaddrinfo", return_value=[private, PUBLIC]) as dns, \
                patch.object(connectivity, "_fetch_location", return_value=connectivity._location_result("ok", country="JP")) as fetch, \
                patch.object(connectivity, "_ping", return_value=connectivity._result("failed")), \
                patch.object(connectivity, "_tcp", return_value=connectivity._result("ok", 1)):
            result = connectivity.probe_node("private-name.example.invalid", 7000)
        self.assertEqual(dns.call_count, 1)
        fetch.assert_called_once_with("8.8.8.8", POLICY["geo"])
        self.assertEqual(result["location"]["state"], "ok")
        self.assertNotIn("private-name", json.dumps(result))

    def test_geo_timeout_does_not_replace_ping_tcp_and_caller_is_bounded(self):
        release, started = threading.Event(), threading.Event()
        def blocked(*_):
            self.assertTrue(threading.current_thread().daemon)
            started.set(); release.wait(2)
            return connectivity._location_result("unknown")
        try:
            with patch.object(connectivity, "JOIN_TIMEOUT", .05), \
                    patch.object(connectivity, "_fetch_location", side_effect=blocked), \
                    patch.object(connectivity, "_ping", return_value=connectivity._result("ok", 1)), \
                    patch.object(connectivity, "_tcp", return_value=connectivity._result("ok", 2)):
                begin = time.monotonic()
                result = connectivity.probe_node("8.8.8.8", 7000)
                self.assertLess(time.monotonic() - begin, .5)
                self.assertTrue(started.is_set())
                self.assertEqual(result["location"]["state"], "timeout")
                self.assertEqual((result["ping"]["state"], result["tcp"]["state"]), ("ok", "ok"))
        finally:
            with connectivity._LOCATION_LOCK:
                completed = connectivity._LOCATION_PENDING.get("8.8.8.8")
            release.set()
            if completed:
                self.assertTrue(completed.wait(.5))

    def test_location_dns_timeout_and_invalid_host_are_safe(self):
        with patch.object(connectivity, "_collect", return_value=(None, None)), \
                patch.object(connectivity.socket, "getaddrinfo", return_value=[]):
            self.assertEqual(connectivity.lookup_location("vps.example.invalid")["state"], "timeout")
        with patch.object(connectivity.socket, "getaddrinfo", side_effect=OSError("PRIVATE")):
            result = connectivity.lookup_location("vps.example.invalid")
            self.assertEqual(result["state"], "unknown")
            self.assertNotIn("PRIVATE", json.dumps(result))
        for host in ("", "https://vps.example", "vps.example;cmd", None):
            with self.assertRaises(UserError):
                connectivity.lookup_location(host)

    def test_missing_or_invalid_shared_policy_disables_external_lookup_even_if_cached(self):
        self.policy_path.unlink()
        with patch.object(connectivity.urllib.request, "build_opener") as build:
            self.assertEqual(connectivity.lookup_location("8.8.8.8")["state"], "unknown")
        build.assert_not_called()
        invalid = [{"schema": 2, "geo": POLICY["geo"]}, {"schema": 1}, {"schema": True, "geo": POLICY["geo"]},
                   {"schema": 1, "geo": dict(POLICY["geo"], endpoint="https://other.invalid/")},
                   {"schema": 1, "geo": dict(POLICY["geo"], endpoint="http://ipwho.is/")},
                   {"schema": 1, "geo": dict(POLICY["geo"], request_timeout_seconds=float("nan"))},
                   {"schema": 1, "geo": dict(POLICY["geo"], request_timeout_seconds=99)},
                   {"schema": 1, "geo": dict(POLICY["geo"], max_response_bytes=99999999)},
                   {"schema": 1, "geo": dict(POLICY["geo"], success_ttl_seconds=True)},
                   {"schema": 1, "geo": dict(POLICY["geo"], fields=["PRIVATE"])}]
        connectivity._LOCATION_CACHE["8.8.8.8"] = (time.monotonic() + 60, connectivity._location_result("ok", country="US"))
        with patch.object(connectivity.urllib.request, "build_opener") as build:
            for document in invalid:
                self.policy_path.write_text(json.dumps(document), encoding="utf-8")
                self.assertIsNone(connectivity._load_location_policy())
                self.assertEqual(connectivity.lookup_location("8.8.8.8")["state"], "unknown")
            self.policy_path.write_bytes(b"x" * 16385)
            self.assertIsNone(connectivity._load_location_policy())
        build.assert_not_called()

    def test_shared_policy_drives_timeout_response_budget_and_cache_ttl(self):
        custom = {"schema": 1, "geo": dict(POLICY["geo"], request_timeout_seconds=1.2,
                  max_response_bytes=1024, success_ttl_seconds=45)}
        self.policy_path.write_text(json.dumps(custom), encoding="utf-8")
        result, opener, _, response = self.fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 1.2)
        self.assertEqual(response.read.call_args.args, (1025,))
        with patch.object(connectivity.time, "monotonic", return_value=100), \
                patch.object(connectivity, "_fetch_location", return_value=result):
            connectivity._location_for_ip("8.8.8.8")
        self.assertEqual(connectivity._LOCATION_CACHE["8.8.8.8"][0], 145)


if __name__ == "__main__":
    unittest.main()
