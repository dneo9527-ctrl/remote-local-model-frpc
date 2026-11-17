"""Conservative TOML edits; retain unrelated bytes and validate semantic changes."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from .core import UserError, atomic_write, digest, write_json

PROVIDER = "sub2api_local"
SECTION = "model_providers." + PROVIDER


def merge(text, provider, model=None):
    before = tomllib.loads(text)
    after = copy.deepcopy(before)
    after.setdefault("model_providers", {})[PROVIDER] = provider
    if model is not None:
        after.update(model=model, model_provider=PROVIDER)
    lines = text.splitlines(keepends=True)
    headers = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\[([^\[\]].*?)\]\s*(?:#.*)?$", line.rstrip("\r\n"))
        if m:
            headers.append((i, m.group(1).strip()))
    # Only unquoted managed section names are rewritten. Others are refused
    # by the final full-document semantic comparison rather than guessed.
    remove = set()
    for n, (start, name) in enumerate(headers):
        if name == SECTION or name.startswith(SECTION + "."):
            end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
            remove.update(range(start, end))
    first = headers[0][0] if headers else len(lines)
    if model is not None:
        for i in range(first):
            if re.match(r"^\s*(model|model_provider)\s*=", lines[i]):
                remove.add(i)
    result = "".join(l for i, l in enumerate(lines) if i not in remove)
    if model is not None:
        result = f"model = {json.dumps(model)}\nmodel_provider = \"{PROVIDER}\"\n" + result
    block = [f"[{SECTION}]"]
    auth = provider.get("auth")
    for k, v in provider.items():
        if k != "auth":
            block.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
    if auth:
        block += ["", f"[{SECTION}.auth]"]
        block += [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in auth.items()]
    result = result.rstrip() + "\n\n" + "\n".join(block) + "\n"
    try:
        parsed = tomllib.loads(result)
    except tomllib.TOMLDecodeError:
        raise UserError("配置采用了本编辑器无法安全处理的写法，原配置未修改。") from None
    if parsed != after:
        raise UserError("合并会影响未管理的配置，已拒绝写入。")
    return result


def app_running(app_path):
    if not app_path:
        return False
    if sys.platform == "darwin":
        from plistlib import loads
        info = loads((Path(app_path) / "Contents/Info.plist").read_bytes())
        executable = str(Path(app_path) / "Contents/MacOS" / info["CFBundleExecutable"])
        output = subprocess.check_output(["/bin/ps", "-axo", "comm="], text=True)
        return executable in [line.strip() for line in output.splitlines()]
    # Conservative: Windows launch is left to the native adapter until tested.
    raise UserError("当前平台的 GUI 退出检测尚未验收，暂不自动切换。")


class CodexConfig:
    def __init__(self, path, runtime, settings):
        self.path, self.runtime, self.settings = Path(path), Path(runtime), settings

    def provider(self):
        key = self.settings.get("key_file")
        if not key or not Path(key).is_file():
            raise UserError("当前节点缺少本机下游 key 文件，不能准备 Codex 接入。")
        # Current official command-backed auth avoids global GUI environment.
        return {"name": "Sub2API · private", "base_url": self.settings["base_url"],
                "wire_api": "responses", "auth": {
                    "command": sys.executable,
                    "args": [str(Path(__file__).with_name("credential.py").resolve()),
                             "--key-file", str(Path(key).resolve())],
                    "timeout_ms": 5000, "refresh_interval_ms": 0}}

    def prepare(self):
        original = self.path.read_bytes() if self.path.exists() else b""
        provider = self.provider()
        candidate = merge(original.decode("utf-8-sig"), provider,
                          self.settings.get("model", "gpt-6.1-sol")).encode()
        atomic_write(self.runtime / "data/codex-candidate.toml", candidate)
        write_json(self.runtime / "data/codex-plan.json", {
            "original_sha256": digest(original), "candidate_sha256": digest(candidate),
            "provider_sha256": digest(json.dumps(provider, sort_keys=True).encode())})
        return "已准备 Codex 配置，当前提供商未变更。退出 Codex 后可点“应用配置”。"

    def apply(self, app_path):
        if app_running(app_path):
            raise UserError("请先正常退出 Codex / ChatGPT，再从桌面控制台应用配置。当前任务不会被强制关闭。")
        plan = json.loads((self.runtime / "data/codex-plan.json").read_text())
        current = self.path.read_bytes() if self.path.exists() else b""
        candidate = (self.runtime / "data/codex-candidate.toml").read_bytes()
        if (digest(current) != plan["original_sha256"] or digest(candidate) != plan["candidate_sha256"] or
                digest(json.dumps(self.provider(), sort_keys=True).encode()) != plan["provider_sha256"]):
            raise UserError("配置在准备后已有变化，请重新准备；未覆盖新设置。")
        backup = self.runtime / "backups" / (time.strftime("config-%Y%m%d-%H%M%S-") + str(time.time_ns()) + ".toml")
        atomic_write(backup, current)
        atomic_write(self.path, candidate)
        write_json(self.runtime / "data/codex-applied.json", {
            "backup": str(backup), "applied_sha256": digest(candidate)})
        return "已备份并切换 Codex 配置；可重新打开 Codex 做新聊天验收。"

    def restore(self, app_path):
        if app_running(app_path):
            raise UserError("请先正常退出 Codex / ChatGPT 后再恢复配置。")
        record = json.loads((self.runtime / "data/codex-applied.json").read_text())
        if digest(self.path.read_bytes()) != record["applied_sha256"]:
            raise UserError("应用后配置已有其他修改，为避免覆盖，需手动合并备份。")
        atomic_write(self.path, Path(record["backup"]).read_bytes())
        return "已恢复切换前的 Codex 配置。"
