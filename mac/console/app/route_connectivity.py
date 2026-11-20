"""Explicit VPS transport checks and public node-IP location lookup.

The caller supplies the address from a registered profile. DNS runs at most
once, Ping uses a resolved numeric address, and TCP uses resolved socket
addresses. Returned data contains no address, command output or credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
import ipaddress
import json
import math
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import UserError

DNS_TIMEOUT = 1.0
PROBE_TIMEOUT = 2.0
# Includes a small scheduling margin after each operation's own timeout.
JOIN_TIMEOUT = 3.0
MAX_ADDRESSES = 16
LOCATION_CACHE_LIMIT = 256
_POLICY_PATH = Path(__file__).resolve().parents[3] / "shared" / "node-policy.json"
_LOCATION_CACHE = OrderedDict()
_LOCATION_PENDING = {}
_LOCATION_LOCK = threading.Lock()


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _location_result(state, **values):
    return {"state": state, "country": "", "country_name": "", "region": "", "city": "",
            "checked_at": _timestamp(), "source": "ipwho.is", "cached": False, **values}


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (not address.is_global or address.is_private or address.is_loopback or
            address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified):
        return None
    # Transition addresses are not ordinary VPS endpoints and may embed local IPs.
    if address.version == 6 and (address.sixtofour or address.teredo):
        return None
    return str(address)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _location_text(value, limit=80):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError()
    value.encode("utf-8", "strict")
    return value.strip()


def _load_location_policy():
    """Both connection types use this repository's single metadata policy.

    An absent or malformed policy disables lookup. Endpoint validation keeps a
    policy edit from turning this fixed-purpose request into an arbitrary URL.
    """
    try:
        with _POLICY_PATH.open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        document = json.loads(raw)
        if not isinstance(document, dict) or type(document.get("schema")) is not int or document["schema"] != 1:
            raise ValueError()
        policy = document["geo"]
        if not isinstance(policy, dict):
            raise ValueError()
        if (policy.get("endpoint") != "https://ipwho.is/" or policy.get("language") != "zh-CN" or
                policy.get("fields") != ["success", "country_code", "country", "region", "city"]):
            raise ValueError()
        for field, maximum in (("success_ttl_seconds", 30 * 86400), ("failure_ttl_seconds", 86400),
                               ("timeout_ttl_seconds", 3600), ("max_response_bytes", 65536)):
            value = policy[field]
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError()
        timeout = policy["request_timeout_seconds"]
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 2.5:
            raise ValueError()
        return policy
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        return None


def _fetch_location(numeric_ip, policy=None):
    """Send only a public numeric IP to one fixed HTTPS metadata service.

    No configured proxy, auth handler, cookies, redirects, node hostname or FRP
    settings are used. The caller also places an overall deadline around this
    operation, since OS/service DNS may not honor the socket timeout.
    """
    if _public_ip(numeric_ip) != numeric_ip:
        return _location_result("skipped")
    policy = policy if policy is not None else _load_location_policy()
    if policy is None:
        return _location_result("unknown")
    endpoint = policy["endpoint"] + urllib.parse.quote(numeric_ip, safe=":")
    endpoint += "?" + urllib.parse.urlencode({"lang": policy["language"], "fields": ",".join(policy["fields"])}, safe=",")
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    opener.addheaders = [("User-Agent", "LocalAIConsole/1.0")]
    try:
        with opener.open(request, timeout=policy["request_timeout_seconds"]) as response:
            if response.status != 200:
                return _location_result("unknown")
            content = response.read(policy["max_response_bytes"] + 1)
        if len(content) > policy["max_response_bytes"]:
            raise ValueError()
        body = json.loads(content)
        if not isinstance(body, dict) or body.get("success") is not True:
            return _location_result("unknown")
        country = body.get("country_code")
        if not isinstance(country, str) or not re.fullmatch("[A-Za-z]{2}", country):
            raise ValueError()
        return _location_result("ok", country=country.upper(),
                                country_name=_location_text(body.get("country", "")),
                                region=_location_text(body.get("region", "")),
                                city=_location_text(body.get("city", "")))
    except (socket.timeout, TimeoutError):
        return _location_result("timeout")
    except urllib.error.URLError as error:
        return _location_result("timeout" if isinstance(error.reason, (socket.timeout, TimeoutError)) else "unknown")
    except (OSError, ValueError, TypeError, UnicodeError):
        return _location_result("unknown")


def _location_for_ip(numeric_ip):
    policy = _load_location_policy()
    if policy is None:
        return _location_result("unknown")
    with _LOCATION_LOCK:
        now = time.monotonic()
        cached = _LOCATION_CACHE.get(numeric_ip)
        if cached and cached[0] > now:
            _LOCATION_CACHE.move_to_end(numeric_ip)
            return {**cached[1], "cached": True}
        _LOCATION_CACHE.pop(numeric_ip, None)
        pending = _LOCATION_PENDING.get(numeric_ip)
        owner = pending is None
        if owner:
            pending = _LOCATION_PENDING[numeric_ip] = threading.Event()
    if not owner:
        if not pending.wait(policy["request_timeout_seconds"]):
            return _location_result("timeout")
        with _LOCATION_LOCK:
            cached = _LOCATION_CACHE.get(numeric_ip)
            return {**cached[1], "cached": True} if cached else _location_result("unknown")
    try:
        result = _fetch_location(numeric_ip, policy)
        ttl_field = ("success_ttl_seconds" if result["state"] == "ok" else
                     "timeout_ttl_seconds" if result["state"] == "timeout" else "failure_ttl_seconds")
        ttl = policy[ttl_field]
        with _LOCATION_LOCK:
            _LOCATION_CACHE[numeric_ip] = (time.monotonic() + ttl, result)
            _LOCATION_CACHE.move_to_end(numeric_ip)
            while len(_LOCATION_CACHE) > LOCATION_CACHE_LIMIT:
                _LOCATION_CACHE.popitem(last=False)
        return dict(result)
    finally:
        with _LOCATION_LOCK:
            _LOCATION_PENDING.pop(numeric_ip, None)
            pending.set()


def _location_for_addresses(addresses):
    for address in addresses:
        numeric = _public_ip(address[4][0])
        if numeric:
            return _location_for_ip(numeric)
    return _location_result("skipped")


def lookup_location(host: str) -> dict:
    """Identify the supplied node, never the Mac's own public IP."""
    literal = _validate(host, 443)
    if literal is None:
        success, addresses = _collect(_async(lambda: _resolve(host, 443, None)), DNS_TIMEOUT)
    else:
        success, addresses = True, _resolve(host, 443, literal)
    if not success or not addresses:
        return _location_result("timeout" if success is None else "unknown")
    success, result = _collect(_async(lambda: _location_for_addresses(addresses)), JOIN_TIMEOUT)
    return result if success else _location_result("timeout" if success is None else "unknown")


def _result(state, latency=None):
    return {"state": state, "latency_ms": latency}


def _validate(host, port):
    if (not isinstance(host, str) or not 0 < len(host) <= 253 or
            not host.isascii() or type(port) is not int or not 1 <= port <= 65535):
        raise UserError("请先填写有效的 VPS 地址和控制端口。")
    try:
        address = ipaddress.ip_address(host)
        # Zone identifiers are not node names and vary between machines.
        if "%" in host:
            raise ValueError()
        return address
    except ValueError:
        labels = host.removesuffix(".").split(".")
        if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
               for label in labels):
            raise UserError("请先填写有效的 VPS 地址和控制端口。") from None
        return None


def _async(call):
    """An OS resolver may ignore timeouts; it must not keep the App alive."""
    result = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((True, call()))
        except Exception:
            # Never return exception text: DNS and command errors contain hosts.
            result.put((False, None))

    threading.Thread(target=run, daemon=True, name="vps-node-check").start()
    return result


def _collect(result, timeout):
    try:
        return result.get(timeout=max(0, timeout))
    except queue.Empty:
        return None, None


def _resolve(host, port, literal):
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        endpoint = (str(literal), port, 0, 0) if literal.version == 6 else (str(literal), port)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)]
    values = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    addresses, seen = [], set()
    for family, kind, protocol, _, endpoint in values:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            continue
        try:
            numeric = ipaddress.ip_address(endpoint[0])
            if "%" in endpoint[0] or numeric.version != (6 if family == socket.AF_INET6 else 4):
                continue
        except (ValueError, TypeError, IndexError):
            continue
        # Use the configured port rather than trusting an altered resolver tuple.
        target = ((str(numeric), port, 0, endpoint[3] if len(endpoint) > 3 else 0)
                  if family == socket.AF_INET6 else (str(numeric), port))
        key = family, target
        if key not in seen:
            seen.add(key)
            addresses.append((family, kind, protocol, "", target))
        if len(addresses) >= MAX_ADDRESSES:
            break
    return addresses


def _ping(address):
    family, _, _, _, endpoint = address
    if sys.platform == "darwin":
        binary = shutil.which("ping6" if family == socket.AF_INET6 else "ping")
        arguments = ["-n", "-c", "1"]
    elif sys.platform.startswith("linux"):
        binary = shutil.which("ping")
        arguments = ["-6" if family == socket.AF_INET6 else "-4", "-n", "-c", "1", "-W", "1"]
    elif sys.platform == "win32":
        binary = shutil.which("ping.exe")
        arguments = ["-6" if family == socket.AF_INET6 else "-4", "-n", "1", "-w", "1200"]
    else:
        return _result("unavailable")
    if not binary:
        return _result("unavailable")
    try:
        completed = subprocess.run([binary, *arguments, endpoint[0]], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=PROBE_TIMEOUT, shell=False)
    except subprocess.TimeoutExpired:
        return _result("timeout")
    except OSError:
        return _result("unavailable")
    if completed.returncode:
        return _result("failed")
    # This matches English and localized Windows replies without returning any
    # raw output. A '<1 ms' reply has no exact measured value, so keep it null.
    match = re.search(r"([=<])\s*(\d+(?:[.,]\d+)?)\s*ms\b", completed.stdout or "", re.I)
    latency = None
    if match and match[1] == "=":
        value = float(match[2].replace(",", "."))
        if 0 <= value <= PROBE_TIMEOUT * 1000:
            latency = round(value, 3)
    return _result("ok", latency)


def _tcp(addresses):
    deadline = time.monotonic() + PROBE_TIMEOUT
    timed_out = False
    for family, kind, protocol, _, endpoint in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _result("timeout")
        try:
            with socket.socket(family, kind, protocol) as connection:
                # A broken IPv6 path must not consume the entire budget before
                # the resolver's IPv4 fallback gets a chance (or vice versa).
                connection.settimeout(min(remaining, PROBE_TIMEOUT / min(len(addresses), 4)))
                started = time.monotonic()
                connection.connect(endpoint)
                return _result("ok", round((time.monotonic() - started) * 1000, 3))
        except (socket.timeout, TimeoutError):
            timed_out = True
        except OSError:
            pass
    return _result("timeout" if timed_out else "failed")


def probe_node(host: str, port: int) -> dict:
    """Measure this VPS only on explicit request, with a bounded caller wait.

    Success means ICMP or the control TCP port is reachable. It does not verify
    FRP authentication, provider registration, health or model/API responses.
    """
    literal = _validate(host, port)
    if literal is None:
        success, addresses = _collect(_async(lambda: _resolve(host, port, literal)), DNS_TIMEOUT)
    else:
        success, addresses = True, _resolve(host, port, literal)
    if not success or not addresses:
        state = "timeout" if success is None else "failed"
        return {"ping": _result(state), "tcp": _result(state),
                "location": _location_result("timeout" if success is None else "unknown"), "checked_at": _timestamp()}
    deadline = time.monotonic() + JOIN_TIMEOUT
    pending = {"ping": _async(lambda: _ping(addresses[0])), "tcp": _async(lambda: _tcp(addresses)),
               "location": _async(lambda: _location_for_addresses(addresses))}
    result = {}
    for kind, task in pending.items():
        success, value = _collect(task, deadline - time.monotonic())
        failure = "timeout" if success is None else "failed"
        result[kind] = value if success else (_location_result("timeout" if success is None else "unknown")
                                            if kind == "location" else _result(failure))
    result["checked_at"] = _timestamp()
    return result
