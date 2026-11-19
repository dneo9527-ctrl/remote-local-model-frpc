"""Private, inert STCP profiles. Saving never changes a live visitor.

The browser receives only public connection metadata. Imports use a deliberate
FRP subset: unsupported options are refused, never silently dropped or run.
"""
from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import uuid

from .core import FileLock, UserError, atomic_write, write_json

MAX_TOML = 65536
ERROR = "线路配置不受支持或格式无效；仅接受单个 STCP、本机约定端口、显式 token 与安全传输选项。高级配置请在原入口处理。"
PARTS = {"ollama": ("ollama_routes", "selected_model_route", 11436),
         "sub2api": ("sub2api_profiles", "selected_route", 18081)}
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
FIELDS = {"server_addr", "server_port", "server_name", "token", "secret_key"}


def settings_lock_path(controller):
    return controller.config_path.with_name("." + controller.config_path.name + ".routes.lock")


def _parts(service):
    if service not in PARTS:
        raise UserError("未知线路服务。")
    return PARTS[service]


def _literal(value, limit=4096):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit or
            any(ord(char) < 32 or ord(char) == 127 for char in value) or
            any(marker in value for marker in ("{{", "}}", "${"))):
        raise ValueError()
    value.encode("utf-8", "strict")
    return value


def _host(value):
    _literal(value, 253)
    if not value.isascii() or "%" in value:
        raise ValueError()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        labels = value.rstrip(".").split(".")
        if (not labels or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                              for label in labels)):
            raise ValueError() from None
    return value


def _name(value):
    _literal(value, 128)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in (".", ".."):
        raise ValueError()
    return value


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError()
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError()


def _duration(value):
    if type(value) is not int or not -1 <= value <= 86400:
        raise ValueError()


def _table(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError()
    for key, entry in value.items():
        validator = allowed[key]
        if isinstance(validator, dict):
            _table(entry, validator)
        elif validator is not None:
            validator(entry)


def _choice(*choices):
    def validate(value):
        if not isinstance(value, str) or value not in choices:
            raise ValueError()
    return validate


def _scopes(value):
    if (not isinstance(value, list) or len(value) != len(set(value)) or
            any(entry not in ("HeartBeats", "NewWorkConns") for entry in value)):
        raise ValueError()


TRANSPORT = {"protocol": _choice("tcp", "kcp", "quic", "websocket", "wss"),
             "tcpMux": _boolean, "heartbeatInterval": _duration,
             "heartbeatTimeout": _duration, "tcpMuxKeepaliveInterval": _duration,
             "dialServerTimeout": _duration, "dialServerKeepalive": _duration,
             "tls": {"enable": _boolean, "disableCustomTLSFirstByte": _boolean,
                     "serverName": _host}}
VISITOR = {"name": _name, "type": _choice("stcp"), "serverName": _name,
           "secretKey": _literal, "bindAddr": _choice("127.0.0.1"), "bindPort": _port,
           "serverUser": _name,
           "transport": {"useEncryption": _boolean, "useCompression": _boolean}}
SCHEMA = {"serverAddr": _host, "serverPort": _port, "user": _name,
          "loginFailExit": _boolean,
          "auth": {"method": _choice("token"), "token": _literal, "additionalScopes": _scopes},
          "transport": TRANSPORT,
          "log": {"to": _choice("console"), "level": _choice("trace", "debug", "info", "warn", "error"),
                  "maxDays": _duration, "disablePrintColor": _boolean},
          "visitors": None}


def _validate(document, port):
    _table(document, SCHEMA)
    _host(document["serverAddr"])
    _port(document["serverPort"])
    auth = document["auth"]
    _literal(auth["token"])
    visitors = document["visitors"]
    if not isinstance(visitors, list) or len(visitors) != 1:
        raise ValueError()
    visitor = visitors[0]
    _table(visitor, VISITOR)
    for key in ("name", "serverName", "secretKey"):
        (_literal if key == "secretKey" else _name)(visitor[key])
    if (visitor["type"] != "stcp" or visitor["bindAddr"] != "127.0.0.1" or
            type(visitor["bindPort"]) is not int or visitor["bindPort"] != port):
        raise ValueError()
    tls = document.setdefault("transport", {}).setdefault("tls", {})
    transport = visitor.setdefault("transport", {})
    if tls.get("enable", True) is not True or transport.get("useEncryption", True) is not True:
        raise ValueError()
    auth.setdefault("method", "token")
    tls["enable"] = True
    transport["useEncryption"] = True
    return document


def _parse(content, port, *, registered_source=False):
    try:
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        if not isinstance(content, str) or not 0 < len(content.encode("utf-8")) <= MAX_TOML:
            raise ValueError()
        document = tomllib.loads(content.lstrip("\ufeff"))
        # A previously registered local visitor can already log to its own
        # project file. Its managed copy logs to this console instead; imports
        # never gain permission to name a file and the source is never changed.
        log = document.get("log", {})
        if registered_source and isinstance(log, dict) and "to" in log:
            _literal(log["to"])
            log["to"] = "console"
        return _validate(document, port)
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise UserError(ERROR) from None


def _private_file(entry, port):
    try:
        path = Path(entry.get("visitor_config", ""))
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOML:
            raise ValueError()
        content = path.read_bytes()
        return content, _parse(content, port, registered_source=True)
    except (OSError, ValueError, TypeError):
        raise UserError("本机线路文件缺失或不可安全读取；原配置保持不变。") from None


def _serialize(document):
    lines = ["# PRIVATE: frps/STCP credentials. Keep this file on this Mac."]

    def key(value):
        return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else json.dumps(value, ensure_ascii=False)

    def literal(value):
        if isinstance(value, (str, bool, int)) or (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            return json.dumps(value, ensure_ascii=False)
        raise UserError(ERROR)

    def table(values, prefix=()):
        for name, value in values.items():
            if not isinstance(value, dict) and not (isinstance(value, list) and value and isinstance(value[0], dict)):
                lines.append(key(name) + " = " + literal(value))
        for name, value in values.items():
            path = prefix + (name,)
            title = ".".join(map(key, path))
            if isinstance(value, dict):
                lines.extend(("", "[" + title + "]"))
                table(value, path)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                for entry in value:
                    lines.extend(("", "[[" + title + "]]"))
                    table(entry, path)
    table(document)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _identity(value, profiles):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value) or value not in profiles:
        raise UserError("未找到该线路方案。")
    return value


def _display_name(value):
    try:
        _literal(value, 80)
        if not value.strip():
            raise ValueError()
        return value.strip()
    except (ValueError, UnicodeError):
        raise UserError("请填写 1 至 80 个字符的线路名称，不含控制字符或模板。") from None


def list_routes(controller, service):
    registry, selected_key, port = _parts(service)
    with controller.lock:
        selected = controller.config.get(selected_key)
        items = []
        for identity, entry in controller.config.get(registry, {}).items():
            mode = entry.get("mode", "stcp") if service == "sub2api" else "stcp"
            item = {"id": identity, "name": entry.get("display_name" if service == "sub2api" else "name", identity),
                    "mode": mode, "editable": False, "active": identity == selected,
                    "server_addr": None, "server_port": None, "server_name": None,
                    "tls": None, "encryption": None}
            if mode == "stcp":
                try:
                    raw, document = _private_file(entry, port)
                    source = tomllib.loads(raw.decode("utf-8-sig"))
                    item.update(editable=True, server_addr=document["serverAddr"], server_port=document["serverPort"],
                                server_name=document["visitors"][0]["serverName"],
                                tls=source.get("transport", {}).get("tls", {}).get("enable"),
                                encryption=source["visitors"][0].get("transport", {}).get("useEncryption"))
                    if source.get("log", {}).get("to", "console") != "console":
                        item["notice"] = "保存副本后日志由控制台接管；原文件保持不变。"
                except (UserError, ValueError, UnicodeError, TypeError, KeyError):
                    pass
            items.append(item)
        return {"service": service, "selected": selected, "items": items}


def _sub2_template(config, entry=None):
    candidates = ([entry] if entry else []) + [config.get("sub2api", {})] + list(config.get("sub2api_profiles", {}).values())
    for candidate in candidates:
        if (candidate.get("mode") == "stcp" and candidate.get("port") == 18081 and
                all(isinstance(candidate.get(key), str) and candidate[key]
                    for key in ("frpc", "key_file", "base_url", "health_url"))):
            return copy.deepcopy(candidate)
    raise UserError("请先在本机配置可复用的 Sub2API STCP 接收器和下游 Key 文件；未猜测安装路径或认证。")


def _managed_directory(controller):
    path = controller.runtime
    for part in ("config", "route-profiles"):
        path = path / part
        if path.is_symlink():
            raise UserError("线路保存目录是链接；未写入任何配置。")
        path.mkdir(mode=0o700, exist_ok=True)
    return path


def manage_route(controller, data):
    if not isinstance(data, dict) or set(data) - {"service", "action", "id", "name", "fields", "visitor_toml"}:
        raise UserError("线路请求字段不受支持；未读取或写入指定路径。")
    service, action = data.get("service"), data.get("action")
    registry, selected_key, port = _parts(service)
    if action not in ("save", "duplicate", "delete", "copy"):
        raise UserError("不支持的线路操作。")
    with controller.lock, FileLock(settings_lock_path(controller)):
        if controller.closing:
            raise UserError("正在全部退出，无法修改或复制线路。")
        try:
            original = controller.config_path.read_bytes()
            if json.loads(original) != controller.config:
                raise ValueError()
        except (OSError, ValueError, UnicodeError):
            raise UserError("本机设置已被其他程序修改，请重新打开控制台后重试；未覆盖配置。") from None
        candidate = copy.deepcopy(controller.config)
        profiles = candidate.setdefault(registry, {})
        identity = data.get("id")
        entry = profiles[_identity(identity, profiles)] if identity is not None else None
        if action != "save" and entry is None:
            raise UserError("请先选择一条线路方案。")
        if action == "delete":
            if identity == candidate.get(selected_key):
                raise UserError("当前使用中的线路不能删除；请先显式应用其他方案。")
            del profiles[identity]
            message = "已移除线路注册；原私有文件保留。"
            saved_id, created_copy = None, False
        else:
            if entry is not None and service == "sub2api" and entry.get("mode", "stcp") != "stcp":
                raise UserError("旧直连方案仅可选择应用，不支持 STCP 编辑或复制。")
            if action == "copy" and sys.platform != "darwin":
                raise UserError("此操作仅支持本 Mac 的系统剪贴板。")
            fields = data.get("fields", {})
            if not isinstance(fields, dict) or set(fields) - FIELDS:
                raise UserError("线路字段不受支持；未读取或写入指定路径。")
            imported = data.get("visitor_toml")
            if action != "save" and (fields or imported is not None):
                raise UserError("复制操作仅接受现有线路方案；请用保存操作编辑字段。")
            if imported is not None:
                document = _parse(imported, port)
            elif entry is not None:
                _, document = _private_file(entry, port)
            else:
                document = {"serverAddr": "", "serverPort": 0, "auth": {"method": "token", "token": ""},
                            "log": {"to": "console", "level": "info"},
                            "visitors": [{"name": service + "-visitor", "type": "stcp", "serverName": "",
                                          "secretKey": "", "bindAddr": "127.0.0.1", "bindPort": port}]}
            if action in ("save", "duplicate"):
                for field, key in (("server_addr", "serverAddr"), ("server_port", "serverPort")):
                    if field in fields:
                        document[key] = fields[field]
                if "server_name" in fields:
                    document["visitors"][0]["serverName"] = fields["server_name"]
                for field, target, key in (("token", document["auth"], "token"),
                                           ("secret_key", document["visitors"][0], "secretKey")):
                    if field in fields and fields[field] not in ("", None):
                        target[key] = fields[field]
                try:
                    _validate(document, port)
                except (ValueError, TypeError, KeyError, UnicodeError):
                    raise UserError(ERROR) from None
            content = _serialize(document)
            if action == "copy":
                try:
                    result = subprocess.run(["/usr/bin/pbcopy"], input=content, capture_output=True, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    raise UserError("系统剪贴板写入未完成；未返回私密配置。") from None
                if result.returncode:
                    raise UserError("系统剪贴板写入未完成；未返回私密配置。")
                return {"message": "完整 visitor 已复制到本机剪贴板，含私密连接凭据；请勿公开分享。"}
            name_field = "display_name" if service == "sub2api" else "name"
            fallback = (entry.get(name_field, "线路") + " 副本")[:80] if entry else "新线路"
            name = _display_name(data.get("name", fallback))
            created_copy = entry is not None and (action == "duplicate" or identity == candidate.get(selected_key))
            saved_id = identity if entry is not None and not created_copy else "route-" + uuid.uuid4().hex
            template = _sub2_template(candidate, entry) if service == "sub2api" else copy.deepcopy(entry or {})
            path = _managed_directory(controller) / (uuid.uuid4().hex + ".toml")
            if path.exists():
                raise UserError("线路保存目标已存在，未覆盖任何文件。")
            template.update({name_field: name, "visitor_config": str(path)})
            if service == "sub2api":
                template["mode"] = "stcp"
            profiles[saved_id] = template
            message = "已保存为新方案，当前连接保持原状；请显式应用。" if created_copy else "线路方案已保存；请按需显式应用。"
        backup = controller.runtime / "backups" / ("route-profiles-" + str(time.time_ns()) + ".json")
        try:
            atomic_write(backup, original)
            if action != "delete":
                atomic_write(path, content)
            if controller.config_path.read_bytes() != original:
                raise UserError("设置已被其他程序修改，保留后续修改；未应用新线路。")
            write_json(controller.config_path, candidate)
        except Exception:
            # Remove only our unregistered UUID file, never an original source
            # or a file another settings writer has already registered.
            if action != "delete":
                try:
                    current = json.loads(controller.config_path.read_bytes())
                    registered = any(value.get("visitor_config") == str(path)
                                     for name in ("ollama_routes", "sub2api_profiles")
                                     for value in current.get(name, {}).values())
                    if not registered and not path.is_symlink() and path.read_bytes() == content:
                        path.unlink()
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            raise
        controller.config = candidate
        return {"message": message, "saved_id": saved_id, "created_copy": created_copy}
