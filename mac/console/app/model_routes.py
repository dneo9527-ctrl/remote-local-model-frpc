"""Change one project's STCP visitor; credentials never go to the web page."""
from pathlib import Path
import time
import uuid

from .core import UserError, atomic_write, validate_visitor


def switch_route(controller, identity):
    routes = controller.config.get("ollama_routes", {})
    if identity not in routes:
        raise UserError("该线路尚未在本机配置，请先导入私有访客配置。")
    if identity == controller.config.get("selected_model_route"):
        return "已使用该线路；Windows 也需选择同名 VPS。"
    settings = controller.config.get("ollama", {})
    target = Path(settings.get("visitor_config", ""))
    source = Path(routes[identity].get("visitor_config", ""))
    if not target.is_file() or not source.is_file() or target.is_symlink():
        raise UserError("线路配置文件缺失或目标是链接；原配置保持不变。")
    # Require exactly one visitor, no provider, loopback-only, same internal port.
    new_config = validate_visitor(source, 11436)
    if new_config.get("includes"):
        raise UserError("线路配置不允许包含其他文件。")
    candidate = source.read_bytes()
    previous = target.read_bytes()
    running = controller.ollama_running()
    backup = controller.runtime / "backups" / ("ollama-route-" + str(time.time_ns()) + "-" + uuid.uuid4().hex + ".toml")
    atomic_write(backup, previous)
    if running:
        controller.action("ollama", "stop")
    try:
        if target.read_bytes() != previous:
            raise UserError("线路文件被其他程序修改，已拒绝覆盖。")
        atomic_write(target, candidate)
        if running:
            controller.action("ollama", "start")
    except Exception:
        # Only undo our own bytes; never clobber another editor's later changes.
        if target.read_bytes() == candidate:
            atomic_write(target, previous)
        if running:
            try:
                controller.action("ollama", "start")
            except Exception:
                pass
        raise UserError("切换未完成，已尝试恢复原线路；请检查本项目状态和日志。") from None
    return "已切换本机访客配置；" + ("连接已重启。" if running else "连接仍暂停。") + "Windows 须选择同名 VPS；API 检查通过后才算连通。"
