"""One VPS pool for both Windows services, with an atomic local apply.

Legacy service profiles remain valid. A node groups their public frps endpoint;
provider names, STCP secrets, API keys and local ports remain service-specific.
No operation here checks a remote model or starts a previously closed service.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
from pathlib import Path
import time
import tomllib
import uuid

from .core import FileLock, Tunnel, UserError, atomic_write, port_listening
from .route_profiles import (MAX_TOML, _display_name, _host, _location, _managed_directory, _parse,
                             _parts, _pending, _port, _private_file, _serialize, _sub2_template,
                             _validate, manage_route, settings_lock_path)

SERVICES = ("ollama", "sub2api")
COMMON = {"server_addr", "server_port", "country", "city", "token"}
PRIVATE = {"server_name", "secret_key", "token"}


def _endpoint(host, port):
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = host.rstrip(".").lower()
    return host, port


def _node_id(host, port):
    host, port = _endpoint(host, port)
    return "vps-" + hashlib.sha256((host + ":" + str(port)).encode("ascii")).hexdigest()[:24]


def _groups(controller):
    nodes = {}
    for service in SERVICES:
        registry, selected_key, local_port = _parts(service)
        for identity, entry in controller.config.get(registry, {}).items():
            if service == "sub2api" and entry.get("mode", "stcp") != "stcp":
                continue
            try:
                _, document = _private_file(entry, local_port)
            except UserError:
                continue
            host, port = _endpoint(document["serverAddr"], document["serverPort"])
            identity_node = _node_id(host, port)
            node = nodes.setdefault(identity_node, {"id": identity_node, "server_addr": host,
                                    "server_port": port, "profiles": {s: [] for s in SERVICES}})
            node["profiles"][service].append((identity, entry, document))
        for node in nodes.values():
            # Preserve all aliases; use the current profile when selecting one.
            node["profiles"][service].sort(key=lambda item: item[0] != controller.config.get(selected_key))
    return nodes


def _require_node(controller, identity):
    if not isinstance(identity, str) or identity not in _groups(controller):
        raise UserError("未找到该 VPS 节点，请刷新后重试。")
    return _groups(controller)[identity]


def _label(controller, node):
    values = controller.config.get("vps_node_labels", {}).get(node["id"], {})
    if values.get("name"):
        return values["name"]
    for service in SERVICES:
        if node["profiles"][service]:
            _, entry, _ = node["profiles"][service][0]
            return entry.get("display_name" if service == "sub2api" else "name", node["server_addr"])
    return node["server_addr"]


def _current_template(controller, service):
    registry, selected_key, local_port = _parts(service)
    profiles = controller.config.get(registry, {})
    candidates = [controller.config.get(service, {}), profiles.get(controller.config.get(selected_key), {})]
    candidates.extend(profiles.values())
    for entry in candidates:
        if service == "sub2api" and entry.get("mode", "stcp") != "stcp":
            continue
        try:
            _, document = _private_file(entry, local_port)
            return entry, document
        except UserError:
            continue
    raise UserError("本机尚无可继承的" + ("模型" if service == "ollama" else "Sub2API") + " STCP 参数；请先导入该业务 visitor。")


def list_nodes(controller):
    with controller.lock:
        nodes, items = _groups(controller), []
        current = {service: None for service in SERVICES}
        for node in nodes.values():
            labels = controller.config.get("vps_node_labels", {}).get(node["id"], {})
            item = {key: node[key] for key in ("id", "server_addr", "server_port")}
            item.update(name=_label(controller, node), services={}, active_services=[],
                        country=labels.get("country", ""), city=labels.get("city", ""), pending=False)
            for service in SERVICES:
                _, selected_key, local_port = _parts(service)
                profiles = node["profiles"][service]
                detail = {"configured": bool(profiles), "active": False, "pending": False,
                          "provider": None, "local_port": local_port}
                if profiles:
                    identity, entry, document = profiles[0]
                    active = any(p[0] == controller.config.get(selected_key) for p in profiles)
                    detail.update(active=active, provider=document["visitors"][0]["serverName"],
                                  pending=_pending(controller, service, identity, document) if active else False)
                    item[service + "_id"] = identity
                    item["country"] = item["country"] or entry.get("country", "")
                    item["city"] = item["city"] or entry.get("city", "")
                    if active:
                        current[service] = node["id"]
                        item["active_services"].append(service)
                item["services"][service] = detail
                item["pending"] |= detail["pending"]
            item["active"] = bool(item["active_services"])
            location = getattr(controller, "route_locations", {}).get(node["server_addr"])
            if location:
                item["location"] = copy.deepcopy(location)
                if location.get("state") == "ok":
                    item["country"] = item["country"] or location.get("country", "")
                    item["city"] = item["city"] or location.get("city", "")
            check = getattr(controller, "vps_node_checks", {}).get((node["server_addr"], node["server_port"]))
            if check:
                item["connectivity"] = copy.deepcopy(check)
            items.append(item)
        templates = {}
        for service in SERVICES:
            try:
                _, document = _current_template(controller, service)
                templates[service] = {"provider": document["visitors"][0]["serverName"],
                                      "local_port": document["visitors"][0]["bindPort"],
                                      "server_port": document["serverPort"]}
            except UserError:
                templates[service] = {"provider": None, "local_port": _parts(service)[2]}
        selected = current["ollama"] if current["ollama"] == current["sub2api"] else None
        return {"items": items, "selected": selected, "current": current,
                "default_server_port": templates.get("ollama", {}).get("server_port"),
                "split": current["ollama"] != current["sub2api"], "templates": templates}


def _guard(controller, *, apply=False):
    if controller.closing:
        raise UserError("正在全部退出，无法修改 VPS 节点。")
    if controller.connection_settings_conflicted:
        raise UserError("连接设置已被其他程序修改，请重新打开控制台后再操作。")
    if apply and controller.active_chats:
        raise UserError("网页聊天正在生成，请结束后再应用 VPS 节点。")
    if apply and controller.diagnostics.busy:
        raise UserError("连接正在诊断，请完成后再应用 VPS 节点。")


def _read(path):
    try:
        return path.read_bytes()
    except OSError:
        return None


def _settings(controller):
    content = _read(controller.config_path)
    try:
        if content is None or json.loads(content) != controller.config:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise UserError("本机设置已被其他程序修改，未覆盖；请重新打开控制台。") from None
    return content


def _fields(data):
    fields = data.get("fields", {})
    if not isinstance(fields, dict):
        raise UserError("VPS 参数格式无效。")
    if "common" in fields:
        if set(fields) - {"common", "services"}:
            raise UserError("VPS 参数格式无效。")
        common, services = fields["common"], fields.get("services", {})
    else:
        common, services = {key: value for key, value in fields.items() if key != "services"}, fields.get("services", {})
    if (not isinstance(common, dict) or set(common) - COMMON or not isinstance(services, dict) or
            set(services) - set(SERVICES) or any(not isinstance(v, dict) or set(v) - PRIVATE for v in services.values())):
        raise UserError("VPS 参数包含不支持的字段。")
    return common, services


def _imports(data):
    imported = data.get("visitor_toml")
    if imported is None:
        return {}
    if isinstance(imported, str):
        try:
            if len(imported.encode("utf-8")) > MAX_TOML:
                raise ValueError()
            document = tomllib.loads(imported.lstrip("\ufeff"))
            port = document["visitors"][0]["bindPort"]
            service = {11436: "ollama", 18081: "sub2api"}[port]
        except (ValueError, TypeError, KeyError, IndexError):
            raise UserError("导入配置须为本机 11436 或 18081 的单个 STCP visitor。") from None
        if data.get("service", service) != service:
            raise UserError("导入配置的业务与本机端口不一致。")
        imported = {service: imported}
    if not isinstance(imported, dict) or not imported or set(imported) - set(SERVICES):
        raise UserError("导入的 visitor 配置格式无效。")
    return {service: _parse(text, _parts(service)[2]) for service, text in imported.items()}


def _prepare(controller, data, node=None, *, duplicate=False):
    common, services = _fields(data)
    imported = _imports(data)
    host = common.get("server_addr", node["server_addr"] if node else None)
    port = common.get("server_port", node["server_port"] if node else None)
    if host is None and imported:
        host = next(iter(imported.values()))["serverAddr"]
    if port is None:
        port = next(iter(imported.values()))["serverPort"] if imported else None
    # A new VPS inherits the active control port, never an invented credential.
    if port is None:
        _, current = _current_template(controller, "ollama")
        port = current["serverPort"]
    try:
        host, port = _endpoint(_host(host), _port(port))
    except (ValueError, TypeError, UnicodeError):
        raise UserError("请填写有效的 VPS 地址和控制端口。") from None
    identity = _node_id(host, port)
    if identity in _groups(controller) and (node is None or identity != node["id"] or duplicate):
        raise UserError("该 VPS 地址和端口已在节点列表中，请编辑现有节点。")
    if any(_endpoint(doc["serverAddr"], doc["serverPort"]) != (host, port) for doc in imported.values()):
        raise UserError("导入的两份配置必须使用同一 VPS 地址和控制端口。")
    candidate, created, selected = copy.deepcopy(controller.config), [], {}
    name = _display_name(data.get("name", (_label(controller, node) if node else "新 VPS 节点")))
    for service in SERVICES:
        registry, _, local_port = _parts(service)
        profiles = candidate.setdefault(registry, {})
        existing = node["profiles"][service] if node else []
        source = existing[:1] if duplicate else existing
        if not source:
            template_entry, template_document = _current_template(controller, service)
            source = [(None, template_entry, template_document)]
        for old_id, entry, document in source:
            if duplicate:
                old_id = None
            document = copy.deepcopy(document) if document is not None else _private_file(entry, local_port)[1]
            if service in imported:
                document = copy.deepcopy(imported[service])
            document["serverAddr"], document["serverPort"] = host, port
            overrides = services.get(service, {})
            for field, target, key in (("token", document["auth"], "token"),
                                       ("secret_key", document["visitors"][0], "secretKey"),
                                       ("server_name", document["visitors"][0], "serverName")):
                value = overrides.get(field, common.get("token") if field == "token" else None)
                if value not in (None, ""):
                    target[key] = value
            try:
                _validate(document, local_port)
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise UserError("VPS 或业务参数无效；原连接保持不变。") from None
            template = _sub2_template(candidate, entry) if service == "sub2api" else copy.deepcopy(entry)
            try:
                _, original_document = _private_file(entry, local_port)
                changed_endpoint = _endpoint(original_document["serverAddr"], original_document["serverPort"]) != (host, port)
            except UserError:
                changed_endpoint = False
            if changed_endpoint:
                # A label for the former IP must not follow a different VPS.
                for field in ("country", "city"):
                    template.pop(field, None)
            for field in ("country", "city"):
                if field in common:
                    template[field] = _location(field, common[field])
            unchanged = old_id is not None and _private_file(entry, local_port)[1] == document
            if not unchanged:
                path = _managed_directory(controller) / (uuid.uuid4().hex + ".toml")
                template["visitor_config"] = str(path)
                created.append((path, _serialize(document)))
            if old_id is None:
                template["display_name" if service == "sub2api" else "name"] = name
            profile_id = old_id or "vps-route-" + uuid.uuid4().hex
            profiles[profile_id] = template
            selected.setdefault(service, profile_id)
    labels = candidate.setdefault("vps_node_labels", {})
    old_label = labels.pop(node["id"], {}) if node and not duplicate else {}
    labels[identity] = dict(old_label, name=name)
    for field in ("country", "city"):
        if field in common:
            labels[identity][field] = _location(field, common[field])
        elif node and node["id"] != identity:
            labels[identity].pop(field, None)
    return candidate, created, selected, identity


def _cleanup_created(controller, created):
    try:
        current = json.loads(controller.config_path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        return
    if not isinstance(current, dict):
        return
    registered = set()
    for registry in ("ollama_routes", "sub2api_profiles"):
        entries = current.get(registry, {})
        if not isinstance(entries, dict) or any(not isinstance(entry, dict) for entry in entries.values()):
            return
        registered.update(entry.get("visitor_config") for entry in entries.values()
                          if isinstance(entry.get("visitor_config"), str))
    for path, content in created:
        try:
            if str(path) not in registered and not path.is_symlink() and _read(path) == content:
                path.unlink()
        except OSError:
            pass


def _commit_saved(controller, original, candidate, created):
    backup = controller.runtime / "backups" / ("vps-nodes-" + str(time.time_ns()) + ".json")
    atomic_write(backup, original)
    try:
        for path, content in created:
            if path.exists():
                raise UserError("VPS 私有文件目标冲突，未覆盖。")
            atomic_write(path, content)
        if _read(controller.config_path) != original:
            raise UserError("设置已被其他程序修改，未覆盖。")
        atomic_write(controller.config_path, (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode())
    except Exception:
        _cleanup_created(controller, created)
        raise
    controller.config = candidate


def _apply(controller, node):
    _guard(controller, apply=True)
    original = _settings(controller)
    old_config = copy.deepcopy(controller.config)
    candidate, created, selected, identity = _prepare(controller, {}, node)
    target = Path(old_config.get("ollama", {}).get("visitor_config", ""))
    if not target.is_absolute() or not target.is_file() or target.is_symlink():
        raise UserError("当前模型 visitor 文件不可安全读取，未切换。")
    previous = _read(target)
    if previous is None:
        raise UserError("无法读取当前模型 visitor，未切换。")
    model_entry = candidate["ollama_routes"][selected["ollama"]]
    source = Path(model_entry["visitor_config"])
    model_content = next((value for path, value in created if path == source), None)
    if model_content is None:
        _, model_document = _private_file(model_entry, 11436)
        model_content = _serialize(model_document)
    # The original installation often registered its live Ollama file as the
    # old node's only source. Preserve that node before replacing the live file.
    for profile_id, entry in candidate["ollama_routes"].items():
        if (Path(entry.get("visitor_config", "")) == target and
                profile_id != selected["ollama"]):
            snapshot = _managed_directory(controller) / (uuid.uuid4().hex + ".toml")
            entry["visitor_config"] = str(snapshot)
            created.append((snapshot, previous))
    candidate["selected_model_route"] = selected["ollama"]
    candidate["selected_route"] = selected["sub2api"]
    candidate["sub2api"] = copy.deepcopy(candidate["sub2api_profiles"][selected["sub2api"]])
    old_tunnel = controller.tunnel
    model_running, sub_running = controller.ollama_running(), old_tunnel.running
    if (old_config.get("sub2api", {}).get("mode") == "stcp" and not sub_running and
            port_listening(old_config["sub2api"].get("port", 18081))):
        raise UserError("Sub2API 端口由外部连接占用，未接管或切换 VPS。")
    selected_bytes = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode()
    backup = controller.runtime / "backups" / ("vps-apply-" + str(time.time_ns()) + "-" + uuid.uuid4().hex)
    atomic_write(backup.with_suffix(".json"), original)
    atomic_write(backup.with_suffix(".toml"), previous)
    model_paused = sub_paused = model_started = sub_started = False
    visitor_written = settings_written = False
    new_tunnel = None
    with FileLock(target.with_name("." + target.name + ".route.lock")):
        try:
            if model_running:
                model_paused = True
                controller.action("ollama", "stop")
            if sub_running:
                sub_paused = True
                old_tunnel.stop()
            if _read(target) != previous or _read(controller.config_path) != original:
                raise UserError("连接或设置已被其他程序修改，未覆盖。")
            for path, content in created:
                if path.exists():
                    raise UserError("VPS 私有文件目标冲突，未覆盖。")
                atomic_write(path, content)
            atomic_write(target, model_content)
            visitor_written = True
            atomic_write(controller.config_path, selected_bytes)
            settings_written = True
            controller.config = candidate
            new_tunnel = Tunnel(candidate["sub2api"], controller.runtime)
            controller.tunnel = new_tunnel
            if model_running:
                model_started = True
                controller.action("ollama", "start")
            if sub_running:
                sub_started = True
                new_tunnel.start()
            if _read(target) != model_content or _read(controller.config_path) != selected_bytes:
                raise UserError("切换期间检测到其他程序修改。")
        except Exception:
            restart_failed = False
            if sub_started and new_tunnel is not None:
                try:
                    new_tunnel.stop()
                except Exception:
                    restart_failed = True
            if model_started:
                try:
                    controller.action("ollama", "stop")
                except Exception:
                    restart_failed = True
            if visitor_written and _read(target) == model_content:
                try:
                    atomic_write(target, previous)
                except OSError:
                    pass
            if settings_written and _read(controller.config_path) == selected_bytes:
                try:
                    atomic_write(controller.config_path, original)
                except OSError:
                    pass
            restored = _read(target) == previous and _read(controller.config_path) == original
            controller.config, controller.tunnel = old_config, old_tunnel
            controller.codex.settings = old_config.get("sub2api", {})
            _cleanup_created(controller, created)
            if not restored:
                controller.connection_settings_conflicted = True
                raise UserError("VPS 切换未完成；保留了其他程序的修改，未自动启动旧连接。请重新打开控制台。") from None
            if model_paused:
                try:
                    controller.action("ollama", "start")
                except Exception:
                    restart_failed = True
            if sub_paused:
                try:
                    old_tunnel.start()
                except Exception:
                    restart_failed = True
            if restart_failed:
                raise UserError("VPS 切换失败，原两路配置已恢复，但连接未全部恢复；请检查本机状态。") from None
            raise UserError("VPS 切换失败，原两路配置、选择与开关状态已恢复。") from None
    controller.codex.settings = candidate["sub2api"]
    controller.ollama_check = {"state": "unchecked", "detail": "VPS 已变化，尚未检查远端 API"}
    controller.sub2api_check = {"state": "unchecked", "detail": "VPS 已变化，尚未检查远端 API"}
    return {"saved_id": identity, "message": "VPS 已同时应用到 Windows 模型和 Sub2API；两路连接保留各自原开关状态。"}


def _check(controller, data):
    from .route_connectivity import lookup_location, probe_node
    from .route_profiles import _remember_location
    allowed = {"action", "id"} if data["action"] == "check" else {"action", "id", "server_addr"}
    if set(data) - allowed or (data["action"] == "location" and ("id" in data) == ("server_addr" in data)):
        raise UserError("VPS 检测请求格式无效。")
    with controller.lock:
        _guard(controller)
        node = _require_node(controller, data.get("id")) if "id" in data else None
        host = node["server_addr"] if node else data.get("server_addr")
        port = node["server_port"] if node else None
    if not controller.route_check_lock.acquire(blocking=False):
        raise UserError("已有节点正在检测，请稍后重试。")
    try:
        result = probe_node(host, port) if data["action"] == "check" else lookup_location(host)
        with controller.lock:
            if data["action"] == "check":
                if not hasattr(controller, "vps_node_checks"):
                    controller.vps_node_checks = {}
                controller.vps_node_checks[(host, port)] = copy.deepcopy(result)
                while len(controller.vps_node_checks) > 256:
                    controller.vps_node_checks.pop(next(iter(controller.vps_node_checks)))
                if "location" in result:
                    _remember_location(controller, host, result["location"])
            else:
                _remember_location(controller, host, result)
        return copy.deepcopy(result)
    finally:
        controller.route_check_lock.release()


def manage_node(controller, data):
    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
        raise UserError("VPS 节点请求格式无效。")
    action = data["action"]
    if action in ("check", "location"):
        return _check(controller, data)
    if action == "copy":
        if set(data) - {"action", "id", "service"} or data.get("service") not in SERVICES:
            raise UserError("请选择需要复制的模型或 Sub2API visitor。")
        with controller.lock:
            node = _require_node(controller, data.get("id"))
            profiles = node["profiles"][data["service"]]
            if not profiles:
                raise UserError("该节点尚未保存这一路 visitor；请先保存节点以继承现用参数。")
            return manage_route(controller, {"service": data["service"], "action": "copy", "id": profiles[0][0]})
    if action not in ("save", "duplicate", "delete", "apply"):
        raise UserError("不支持的 VPS 节点操作。")
    allowed = {"action", "id", "name", "fields", "visitor_toml", "service"} if action in ("save", "duplicate") else {"action", "id"}
    if set(data) - allowed:
        raise UserError("VPS 节点请求包含不支持的字段。")
    with controller.lock, FileLock(settings_lock_path(controller)):
        _guard(controller, apply=action == "apply")
        node = _require_node(controller, data.get("id")) if "id" in data else None
        if action != "save" and node is None:
            raise UserError("请先选择一个 VPS 节点。")
        if action == "apply":
            return _apply(controller, node)
        original = _settings(controller)
        if action == "delete":
            for service in SERVICES:
                registry, selected_key, _ = _parts(service)
                if any(item[0] == controller.config.get(selected_key) for item in node["profiles"][service]):
                    raise UserError("任一路正在使用的 VPS 节点不能删除；请先应用其他节点。")
            candidate = copy.deepcopy(controller.config)
            for service in SERVICES:
                registry = _parts(service)[0]
                for identity, _, _ in node["profiles"][service]:
                    candidate[registry].pop(identity, None)
            candidate.setdefault("vps_node_labels", {}).pop(node["id"], None)
            _commit_saved(controller, original, candidate, [])
            return {"message": "已移除该 VPS 的两路节点注册；原私有文件保留。", "saved_id": None}
        candidate, created, _, identity = _prepare(controller, data, node, duplicate=action == "duplicate")
        _commit_saved(controller, original, candidate, created)
        return {"saved_id": identity, "created_copy": action == "duplicate", "message": "VPS 节点已保存；两路连接参数待统一应用。"}
