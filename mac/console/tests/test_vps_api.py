"""Unified-node HTTP boundary checks with no model or tunnel calls."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.core import UserError
from app.server import Handler, LocalHTTPServer


class UnifiedNodeAPITests(unittest.TestCase):
    def setUp(self):
        self.server = LocalHTTPServer(("127.0.0.1", 0), Handler)
        self.server.authority = "127.0.0.1:" + str(self.server.server_port)
        self.server.token = "isolated-fixture-csrf"
        self.server.controller = object()
        self.base = "http://" + self.server.authority
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, payload=None, *, csrf=True, origin=None):
        headers = {"Origin": self.base if origin is None else origin}
        if csrf:
            headers["X-Local-CSRF"] = self.server.token
        body = None if payload is None else json.dumps(payload).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + "/api/vps-nodes", data=body, headers=headers)
        with self.opener.open(request, timeout=2) as response:
            return json.load(response)

    def test_one_list_endpoint_reads_both_connections_without_mutating_or_probing(self):
        value = {"items": [{"id": "vps-fixture", "active_services": ["ollama", "sub2api"]}],
                 "current": {"ollama": "vps-fixture", "sub2api": "vps-fixture"}, "split": False}
        with patch("app.server.list_nodes", return_value=value) as listing, \
                patch("app.server.manage_node") as change:
            self.assertEqual(self.request(), value)
        listing.assert_called_once_with(self.server.controller)
        change.assert_not_called()

    def test_unified_apply_requires_local_origin_and_csrf_before_dispatch(self):
        payload = {"action": "apply", "id": "vps-fixture"}
        with patch("app.server.manage_node", return_value={"message": "both configured"}) as change:
            for options in ({"csrf": False}, {"origin": "https://outside.invalid"}):
                with self.subTest(options=options), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(payload, **options)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            change.assert_not_called()
            self.assertEqual(self.request(payload), {"message": "both configured"})
        change.assert_called_once_with(self.server.controller, payload)

    def test_transaction_refusal_is_reported_without_internal_exception_text(self):
        with patch("app.server.manage_node", side_effect=UserError("连接设置已变化，未应用。")):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request({"action": "apply", "id": "vps-fixture"})
            with error.exception as response:
                self.assertEqual(response.code, 409)
                self.assertEqual(json.load(response), {"error": "连接设置已变化，未应用。"})
