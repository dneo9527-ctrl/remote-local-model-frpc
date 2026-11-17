"""Change one project's STCP visitor; credentials never go to the web page."""
import json
from pathlib import Path
import time
import tomllib
import uuid

from .core import FileLock, UserError, atomic_write


def _visitor_snapshot(path):
    """Validate exactly the bytes that will be installed, not a second read."""
    try:
        content = path.read_bytes()
        config = tomllib.loads(content.decode("utf-8-sig"))
        visitors = config.get("visitors", [])
        if (not isinstance(visitors, list) or len(visitors) != 1 or
                not isinstance(visitors[0], dict)):
            raise ValueError()
        visitor = visitors[0]
        if (visitor.get("type") != "stcp" or visitor.get("bindAddr") != "127.0.0.1" or
                type(visitor.get("bindPort")) is not int or visitor["bindPort"] != 11436 or
                config.get("proxies") or config.get("includes")):
            raise ValueError()
        for field in ("name", "serverName", "secretKey"):
            if not isinstance(visitor.get(field), str) or not visitor[field].strip():
                raise ValueError()
        if (not isinstance(config.get("serverAddr"), str) or not config["serverAddr"].strip() or
                type(config.get("serverPort")) is not int or not 1 <= config["serverPort"] <= 65535):
            raise ValueError()
        # A visitor switch must not turn on an unrelated public management API.
        web = config.get("webServer")
        if web and (not isinstance(web, dict) or web.get("addr") != "127.0.0.1"):
            raise ValueError()
        return content
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        raise UserError("线路配置无效：须为单个 STCP 访客、本机 11436 端口及完整服务器信息，不能含其他代理或公网管理接口。") from None


def _read(path):
    try:
        return path.read_bytes()
    except OSError:
        return None


def switch_route(controller, identity):
    routes = controller.config.get("ollama_routes", {})
    if not isinstance(identity, str) or identity not in routes:
        raise UserError("该线路尚未在本机配置，请先导入私有访客配置。")
    settings = controller.config.get("ollama", {})
    target = Path(settings.get("visitor_config", ""))
    source = Path(routes[identity].get("visitor_config", ""))
    if (not target.is_absolute() or not source.is_absolute() or
            not target.is_file() or not source.is_file() or
            target.is_symlink() or source.is_symlink()):
        raise UserError("线路配置文件缺失、路径非绝对或是链接；原配置保持不变。")
    # This lock serializes switches using the same visitor even across consoles.
    # Existing external managers retain their own locks; byte checks below also
    # reject concurrent edits instead of overwriting them.
    with FileLock(target.with_name("." + target.name + ".route.lock")):
        candidate = _visitor_snapshot(source)
        previous = _read(target)
        if previous is None:
            raise UserError("无法读取当前访客配置；原设置保持不变。")
        config_path = controller.config_path
        original_settings = _read(config_path)
        try:
            if original_settings is None or json.loads(original_settings) != controller.config:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise UserError("本机设置已被其他程序修改，请重新打开控制台后再切换。") from None
        if (identity == controller.config.get("selected_model_route") and candidate == previous):
            return "已使用该线路；Windows 也需选择同名 VPS。"
        selected = dict(controller.config, selected_model_route=identity)
        selected_bytes = (json.dumps(selected, ensure_ascii=False, indent=2) + "\n").encode()
        backup = controller.runtime / "backups" / ("ollama-route-" + str(time.time_ns()) + "-" + uuid.uuid4().hex)
        atomic_write(backup.with_suffix(".toml"), previous)
        atomic_write(backup.with_suffix(".json"), original_settings)
        running = controller.ollama_running()
        visitor_written = settings_written = False
        pause_attempted = False
        try:
            if running:
                pause_attempted = True
                controller.action("ollama", "stop")
            if _read(target) != previous or _read(config_path) != original_settings:
                raise UserError("线路或设置被其他程序修改，已拒绝覆盖。")
            atomic_write(target, candidate)
            visitor_written = True
            atomic_write(config_path, selected_bytes)
            settings_written = True
            if running:
                controller.action("ollama", "start")
        except Exception:
            # Restore only bytes this transaction wrote; preserve later edits.
            if visitor_written and _read(target) == candidate:
                try:
                    atomic_write(target, previous)
                except OSError:
                    pass
            if settings_written and _read(config_path) == selected_bytes:
                try:
                    atomic_write(config_path, original_settings)
                except OSError:
                    pass
            restored = _read(target) == previous and _read(config_path) == original_settings
            if pause_attempted and restored:
                try:
                    controller.action("ollama", "start")
                except Exception:
                    raise UserError("切换失败，原配置已恢复但连接未能重启；请在管理入口恢复连接。") from None
            if not restored:
                raise UserError("切换未完成，检测到后续修改或恢复失败，未覆盖这些文件；请从本机备份核对。") from None
            raise UserError("切换未完成，原配置与线路选择已恢复；请检查本项目状态和日志。") from None
        controller.config = selected
        return "已切换本机访客配置；" + ("连接已重启。" if running else "连接仍暂停。") + "Windows 须选择同名 VPS；API 检查通过后才算连通。"
