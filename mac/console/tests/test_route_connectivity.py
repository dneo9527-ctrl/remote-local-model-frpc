"""Isolated VPS transport checks: no real VPS, provider or model requests."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.core import UserError
from app import route_connectivity as connectivity


IPV4 = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.21", 7000))
IPV6 = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2001:db8::21", 7000, 0, 0))


class NodeConnectivityTests(unittest.TestCase):
    def ping(self, *, platform="darwin", address=IPV4, output="64 bytes: time=8.125 ms", code=0):
        with patch.object(connectivity.sys, "platform", platform), \
                patch.object(connectivity.shutil, "which", return_value="/fixture/ping") as binary, \
                patch.object(connectivity.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "PRIVATE STDERR")) as run:
            result = connectivity._ping(address)
        return result, binary, run

    def test_invalid_parameters_are_rejected_before_dns_or_commands(self):
        pairs = [(None, 7000), ("", 7000), ("a" * 254, 7000), ("https://vps.example/", 7000),
                 ("vps.example;touch", 7000), ("-vps.example", 7000), ("vps.example\n", 7000),
                 ("vps.example$(x)", 7000), ("user@vps.example", 7000), ("主机.example", 7000),
                 ("vps.example..", 7000),
                 ("fe80::1%en0", 7000), ("[::1]", 7000), ("vps.example", "7000"),
                 ("vps.example", True), ("vps.example", 0), ("vps.example", 65536)]
        with patch.object(connectivity.socket, "getaddrinfo") as dns, \
                patch.object(connectivity.subprocess, "run") as command, \
                patch.object(connectivity.socket, "socket") as tcp:
            for host, port in pairs:
                with self.subTest(host=host, port=port), self.assertRaises(UserError) as error:
                    connectivity.probe_node(host, port)
                self.assertEqual(str(error.exception), "请先填写有效的 VPS 地址和控制端口。")
        dns.assert_not_called(); command.assert_not_called(); tcp.assert_not_called()

    def test_literals_do_not_trigger_dns_and_ipv6_is_supported(self):
        with patch.object(connectivity.socket, "getaddrinfo") as dns, \
                patch.object(connectivity, "_ping", return_value=connectivity._result("ok", 1)) as ping, \
                patch.object(connectivity, "_tcp", return_value=connectivity._result("ok", 2)) as tcp:
            result = connectivity.probe_node("2001:db8::21", 7000)
        dns.assert_not_called()
        self.assertEqual(ping.call_args.args[0], IPV6)
        self.assertEqual(tcp.call_args.args[0], [IPV6])
        self.assertEqual(result["tcp"], {"state": "ok", "latency_ms": 2})
        self.assertTrue(result["checked_at"].endswith("Z"))

    def test_hostname_resolves_once_and_uses_only_numeric_addresses_afterwards(self):
        with patch.object(connectivity.socket, "getaddrinfo", return_value=[IPV6, IPV4, IPV4]) as dns, \
                patch.object(connectivity, "_ping", return_value=connectivity._result("failed")) as ping, \
                patch.object(connectivity, "_tcp", return_value=connectivity._result("ok", 2)) as tcp:
            result = connectivity.probe_node("vps.example.invalid", 7000)
        dns.assert_called_once_with("vps.example.invalid", 7000, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        self.assertEqual(ping.call_args.args[0], IPV6)
        self.assertEqual(tcp.call_args.args[0], [IPV6, IPV4])
        self.assertEqual(result["ping"]["state"], "failed")
        self.assertEqual(result["tcp"]["state"], "ok")
        self.assertNotIn("vps.example", json.dumps(result))

    def test_dns_error_is_safe_and_does_not_start_ping_or_tcp(self):
        with patch.object(connectivity.socket, "getaddrinfo", side_effect=OSError("PRIVATE host token")), \
                patch.object(connectivity, "_ping") as ping, patch.object(connectivity, "_tcp") as tcp:
            result = connectivity.probe_node("vps.example.invalid", 7000)
        self.assertEqual(result["tcp"]["state"], "failed")
        self.assertEqual(result["ping"]["state"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(result))
        ping.assert_not_called(); tcp.assert_not_called()

    def test_slow_os_resolver_has_bounded_caller_wait_and_daemon_worker(self):
        release, started = threading.Event(), threading.Event()

        def dns(*args, **kwargs):
            self.assertTrue(threading.current_thread().daemon)
            started.set(); release.wait(timeout=2)
            return [IPV4]

        try:
            with patch.object(connectivity, "DNS_TIMEOUT", .05), \
                    patch.object(connectivity.socket, "getaddrinfo", side_effect=dns):
                begin = time.monotonic()
                result = connectivity.probe_node("vps.example.invalid", 7000)
                self.assertTrue(started.is_set())
                self.assertLess(time.monotonic() - begin, .5)
                self.assertEqual(result["tcp"]["state"], "timeout")
                self.assertEqual(result["ping"]["state"], "timeout")
        finally:
            release.set()

    def test_ping_tcp_wait_share_one_deadline_and_never_keep_app_alive(self):
        release, ping_started, tcp_started = threading.Event(), threading.Event(), threading.Event()

        def blocked(event):
            self.assertTrue(threading.current_thread().daemon)
            event.set(); release.wait(timeout=2)
            return connectivity._result("ok", 1)

        try:
            with patch.object(connectivity, "JOIN_TIMEOUT", .05), \
                    patch.object(connectivity, "_ping", side_effect=lambda _: blocked(ping_started)), \
                    patch.object(connectivity, "_tcp", side_effect=lambda _: blocked(tcp_started)):
                begin = time.monotonic()
                result = connectivity.probe_node("192.0.2.21", 7000)
                self.assertTrue(ping_started.is_set() and tcp_started.is_set())
                self.assertLess(time.monotonic() - begin, .5)
                self.assertEqual(result["tcp"]["state"], "timeout")
                self.assertEqual(result["ping"]["state"], "timeout")
        finally:
            release.set()

    def test_ping_macos_argv_numeric_target_and_no_shell(self):
        result, binary, run = self.ping()
        binary.assert_called_once_with("ping")
        self.assertEqual(run.call_args.args[0], ["/fixture/ping", "-n", "-c", "1", "192.0.2.21"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], connectivity.PROBE_TIMEOUT)
        self.assertEqual(result, {"state": "ok", "latency_ms": 8.125})

    def test_ping_macos_ipv6_uses_ping6(self):
        result, binary, run = self.ping(address=IPV6)
        binary.assert_called_once_with("ping6")
        self.assertEqual(run.call_args.args[0][-1], "2001:db8::21")
        self.assertEqual(result["state"], "ok")

    def test_ping_linux_ipv6_command(self):
        result, binary, run = self.ping(platform="linux", address=IPV6)
        self.assertEqual(run.call_args.args[0], ["/fixture/ping", "-6", "-n", "-c", "1", "-W", "1", "2001:db8::21"])
        self.assertEqual(result["state"], "ok")

    def test_ping_windows_command_and_localized_ms(self):
        result, binary, run = self.ping(platform="win32", output="来自 192.0.2.21 的回复: 字节=32 时间=12ms TTL=54")
        binary.assert_called_once_with("ping.exe")
        self.assertEqual(run.call_args.args[0], ["/fixture/ping", "-4", "-n", "1", "-w", "1200", "192.0.2.21"])
        self.assertEqual(result, {"state": "ok", "latency_ms": 12.0})

    def test_ping_success_without_exact_latency_does_not_invent_measurement(self):
        for output in ("time<1ms", "PRIVATE command output", "time=999999999999ms"):
            with self.subTest(output=output):
                result, _, _ = self.ping(output=output)
                self.assertEqual(result, {"state": "ok", "latency_ms": None})
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_ping_unreachable_is_separate_from_tcp(self):
        result, _, _ = self.ping(code=1, output="PRIVATE vps address")
        self.assertEqual(result, {"state": "failed", "latency_ms": None})
        with patch.object(connectivity, "_ping", return_value=result), \
                patch.object(connectivity, "_tcp", return_value=connectivity._result("ok", .1)):
            full = connectivity.probe_node("192.0.2.21", 7000)
        self.assertEqual(full["ping"]["state"], "failed")
        self.assertEqual(full["tcp"]["state"], "ok")

    def test_ping_missing_unsupported_denied_and_timeout_are_safe(self):
        with patch.object(connectivity.sys, "platform", "other"), patch.object(connectivity.subprocess, "run") as run:
            self.assertEqual(connectivity._ping(IPV4)["state"], "unavailable")
        run.assert_not_called()
        with patch.object(connectivity.sys, "platform", "darwin"), patch.object(connectivity.shutil, "which", return_value=None):
            self.assertEqual(connectivity._ping(IPV4)["state"], "unavailable")
        for error, state in ((PermissionError("PRIVATE binary"), "unavailable"),
                             (subprocess.TimeoutExpired("PRIVATE binary", 2), "timeout")):
            with self.subTest(state=state), patch.object(connectivity.sys, "platform", "darwin"), \
                    patch.object(connectivity.shutil, "which", return_value="/fixture/ping"), \
                    patch.object(connectivity.subprocess, "run", side_effect=error):
                result = connectivity._ping(IPV4)
                self.assertEqual(result["state"], state)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_tcp_tries_second_resolved_address_without_dns(self):
        failed, connected = MagicMock(), MagicMock()
        failed.__enter__.return_value = failed
        connected.__enter__.return_value = connected
        failed.connect.side_effect = ConnectionRefusedError("PRIVATE endpoint")
        with patch.object(connectivity.socket, "socket", side_effect=[failed, connected]) as sockets, \
                patch.object(connectivity.socket, "getaddrinfo") as dns:
            result = connectivity._tcp([IPV6, IPV4])
        dns.assert_not_called()
        self.assertEqual(sockets.call_count, 2)
        connected.connect.assert_called_once_with(("192.0.2.21", 7000))
        self.assertEqual(result["state"], "ok")
        self.assertLessEqual(connected.settimeout.call_args.args[0], connectivity.PROBE_TIMEOUT)
        self.assertLessEqual(failed.settimeout.call_args.args[0], connectivity.PROBE_TIMEOUT / 2)

    def test_tcp_refused_and_timeout_are_separate(self):
        for error, state in ((ConnectionRefusedError("PRIVATE endpoint"), "failed"),
                             (socket.timeout("PRIVATE endpoint"), "timeout")):
            connection = MagicMock(); connection.__enter__.return_value = connection
            connection.connect.side_effect = error
            with self.subTest(state=state), patch.object(connectivity.socket, "socket", return_value=connection):
                result = connectivity._tcp([IPV4])
            self.assertEqual(result, {"state": state, "latency_ms": None})
            connection.__exit__.assert_called_once()

    def test_dns_answers_are_numeric_deduplicated_bounded_and_use_configured_port(self):
        invalid = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("PRIVATE-host", 123))
        other = (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("192.0.2.22", 123))
        changed_port = (*IPV4[:4], ("192.0.2.21", 1))
        with patch.object(connectivity.socket, "getaddrinfo", return_value=[invalid, other, changed_port, IPV4, IPV6]):
            addresses = connectivity._resolve("vps.example.invalid", 7000, None)
        self.assertEqual(addresses, [IPV4, IPV6])
        with patch.object(connectivity, "MAX_ADDRESSES", 1), \
                patch.object(connectivity.socket, "getaddrinfo", return_value=[IPV4, IPV6]):
            self.assertEqual(connectivity._resolve("vps.example.invalid", 7000, None), [IPV4])

    def test_actual_loopback_tcp_success_and_closed_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0)); listener.listen(1)
            port = listener.getsockname()[1]
            with patch.object(connectivity, "_ping", return_value=connectivity._result("unavailable")):
                result = connectivity.probe_node("127.0.0.1", port)
            self.assertEqual(result["tcp"]["state"], "ok")
            self.assertIsInstance(result["tcp"]["latency_ms"], float)
            self.assertEqual(result["ping"]["state"], "unavailable")
        with patch.object(connectivity, "_ping", return_value=connectivity._result("unavailable")):
            closed = connectivity.probe_node("127.0.0.1", port)
        # A closed port must remain unreachable. Windows may hit the short
        # connect deadline before reporting that the connection was refused.
        self.assertIn(closed["tcp"]["state"], ("failed", "timeout"))
        self.assertIsNone(closed["tcp"]["latency_ms"])


if __name__ == "__main__":
    unittest.main()
