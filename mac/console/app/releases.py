"""Same-repository source updates. Downloading never executes or overwrites code.

SHA-256 protects release-asset integrity; repository maintainers remain the trust
boundary. Code selection is an atomic pointer, distinct from service deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "dneo9527-ctrl/remote-local-model-frpc"
PROJECT = "remote-local-model-frpc"
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(preview|rc)\.(0|[1-9]\d*))?$")
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_FILES = 1000
GITHUB_HOSTS = frozenset({"api.github.com", "github.com", "release-assets.githubusercontent.com",
                          "objects.githubusercontent.com", "github-releases.githubusercontent.com"})


class UpdateError(Exception):
    """Safe operational error; never includes a remote response body."""


def version_key(version):
    match = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise UpdateError("发布版本格式无效。")
    major, minor, patch, kind, number = match.groups()
    return (int(major), int(minor), int(patch), 2 if kind is None else 1 if kind == "rc" else 0,
            int(number or 0))


def _write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _official_url(target):
    try:
        parsed = urllib.parse.urlsplit(target)
        return (parsed.scheme == "https" and not parsed.username and not parsed.password and
                parsed.port in (None, 443) and parsed.hostname in GITHUB_HOSTS)
    except ValueError:
        return False


def _download(url, maximum):
    class OfficialRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            if not _official_url(newurl):
                raise UpdateError("下载跳转不属于 GitHub 官方域名。")
            return super().redirect_request(request, fp, code, message, headers, newurl)
    if not _official_url(url):
        raise UpdateError("更新地址不属于 GitHub 官方域名。")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": PROJECT + "-updater"})
    with urllib.request.build_opener(OfficialRedirects()).open(request, timeout=20) as response:
        if not _official_url(response.geturl()):
            raise UpdateError("下载跳转不属于 GitHub 官方域名。")
        body = response.read(maximum + 1)
        if len(body) > maximum:
            raise UpdateError("发布附件超过大小限制。")
        return body


def discover(channel="stable", current="0.2.0-preview.1", fetch=None):
    """Return release metadata, or an honest unpublished result (including 404)."""
    if channel not in ("stable", "preview"):
        raise UpdateError("更新通道只能是 stable 或 preview。")
    version_key(current)
    fetch = fetch or _download
    # GitHub's /latest is a maintainer-selected label (make_latest=false can
    # exclude a newer stable release); choose the highest semantic version.
    releases = []
    try:
        for page in range(1, 11):
            suffix = "/releases?per_page=100" + ("&page=" + str(page) if page > 1 else "")
            data = json.loads(fetch("https://api.github.com/repos/" + REPOSITORY + suffix, 2 * 1024 * 1024))
            if not isinstance(data, list):
                raise UpdateError("GitHub 发布列表格式不匹配。")
            releases.extend(data)
            if len(data) < 100:
                break
        else:
            raise UpdateError("发布列表过长，未能完整核对最新版；当前程序保持原状。")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise UpdateError("GitHub 更新检查未完成，请稍后重试。") from None
        releases = []
    except (OSError, ValueError, TimeoutError):
        raise UpdateError("无法读取 GitHub 发布信息；本机程序保持原状。") from None
    candidates = []
    for item in releases:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        version = str(item.get("tag_name", "")).removeprefix("v")
        if not VERSION_RE.fullmatch(version):
            continue
        if channel == "stable" and (item.get("prerelease") or "-" in version):
            continue
        candidates.append((version_key(version), version, item))
    if not candidates:
        return {"status": "unpublished", "channel": channel, "current": current,
                "repository": REPOSITORY, "message": "该通道尚无已发布版本；当前版本保持原状。"}
    _, version, release = max(candidates, key=lambda item: item[0])
    expected_url = "https://github.com/" + REPOSITORY + "/releases/tag/v" + version
    if release.get("html_url") != expected_url or release.get("tag_name") != "v" + version:
        raise UpdateError("发布标签或仓库地址不符合项目约定。")
    status = "available" if version_key(version) > version_key(current) else "current"
    return {"status": status, "channel": channel, "current": current, "version": version,
            "repository": REPOSITORY, "url": expected_url, "release": release,
            "message": ("发现新版本 " if status == "available" else "当前版本未落后于发布版本 ") + version +
                       "；下载校验与源码切换需单独执行，服务不会自动重启。"}


def _asset(release, name, version):
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    expected = "https://github.com/" + REPOSITORY + "/releases/download/v" + version + "/" + name
    if len(matches) != 1 or matches[0].get("browser_download_url") != expected:
        raise UpdateError("发布缺少唯一、同仓库的源码包或校验附件。")
    return expected


def _safe_relative(name):
    if (not name or any(ord(char) < 32 or char in '\\:*?<>|"' for char in name) or
            name.startswith("/")):
        raise UpdateError("源码包包含不安全路径。")
    path = PurePosixPath(name)
    if any(part in ("", ".", "..") or part[-1:] in (".", " ") for part in name.split("/")):
        raise UpdateError("源码包包含不安全路径。")
    reserved = {"CON", "PRN", "AUX", "NUL", *["COM" + str(n) for n in range(1, 10)],
                *["LPT" + str(n) for n in range(1, 10)]}
    if any(part.split(".")[0].upper() in reserved for part in path.parts):
        raise UpdateError("源码包包含平台保留路径。")
    denied_parts = {".git", "data", "logs", "backups", "secrets", "evidence", "delivery", "__pycache__", "node_modules"}
    denied_files = {".env", "api-key.txt", "settings.json", "frpc.toml", "auth.json", "frpc-visitor.toml"}
    if (any(part.casefold() in denied_parts for part in path.parts) or path.name.casefold() in denied_files or
            (path.name.casefold().startswith(".env.") and path.name != ".env.example")):
        raise UpdateError("源码包混入私人配置或运行数据。")
    if path.suffix.casefold() in (".exe", ".dll", ".so", ".dylib", ".db", ".sqlite", ".key", ".p12", ".pem", ".pyc"):
        raise UpdateError("源码包混入二进制、数据库或密钥文件。")
    return path


def extract_source(archive, destination, version):
    """Validate the whole ZIP before writing; no links, collisions or runtime data."""
    version_key(version)
    files = []
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > MAX_FILES:
            raise UpdateError("源码包文件数量超过限制。")
        for entry in entries:
            # ZipInfo normalizes backslashes on Windows and truncates NULs on
            # every platform. Validate the original archive spelling too, so
            # a malformed member cannot turn into an accepted source path.
            if entry.orig_filename != entry.filename:
                raise UpdateError("源码包包含不安全路径。")
            name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
            path = _safe_relative(name)
            if len(path.parts) < 2 or path.parts[0] != PROJECT:
                if entry.is_dir() and str(path) == PROJECT:
                    continue
                raise UpdateError("源码包根目录不符合项目约定。")
            relative = PurePosixPath(*path.parts[1:])
            key = unicodedata.normalize("NFC", str(relative)).casefold()
            if key in seen:
                raise UpdateError("源码包包含重复或大小写冲突路径。")
            seen.add(key)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise UpdateError("源码包包含符号链接或特殊文件。")
            if entry.flag_bits & 1:
                raise UpdateError("源码包不允许加密条目。")
            total += entry.file_size
            if total > MAX_EXPANDED or (entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 250):
                raise UpdateError("源码包解压大小或压缩比超过限制。")
            if not entry.is_dir():
                files.append((entry, relative))
        names = {str(path) for _, path in files}
        folded_names = {unicodedata.normalize("NFC", name).casefold() for name in names}
        if not {"version.json", "tools/update.py", "mac/console/app/__main__.py", "windows/sub2api-local/Start-Sub2API.ps1"} <= names:
            raise UpdateError("源码包缺少双端入口或版本信息。")
        # Reject file/directory conflicts before creating any output.
        for _, path in files:
            if any(unicodedata.normalize("NFC", str(parent)).casefold() in folded_names
                   for parent in path.parents if str(parent) != "."):
                raise UpdateError("源码包包含文件与目录冲突。")
        try:
            metadata = json.loads(package.read(PROJECT + "/version.json"))
            if metadata.get("version") != version or metadata.get("repository") != REPOSITORY:
                raise UpdateError("源码包版本与发布标签不匹配。")
        except (KeyError, ValueError):
            raise UpdateError("源码包版本信息无效。") from None
        manifest = {}
        for entry, path in files:
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = package.read(entry)
            target.write_bytes(content)
            os.chmod(target, 0o700 if entry.external_attr >> 16 & 0o111 else 0o600)
            manifest[str(path)] = hashlib.sha256(content).hexdigest()
    return manifest


def stage(state, channel="stable", current="0.2.0-preview.1", fetch=None):
    state = Path(state).resolve()
    fetch = fetch or _download
    result = discover(channel, current, fetch)
    if result["status"] == "unpublished":
        return result
    version = result["version"]
    release = result.pop("release")
    stem = PROJECT + "-" + version
    name = stem + "-source.zip"
    archive_url = _asset(release, name, version)
    sum_url = _asset(release, stem + "-SHA256SUMS.txt", version)
    try:
        sums = fetch(sum_url, 64 * 1024).decode("utf-8")
        matches = []
        for line in sums.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*") == name:
                matches.append(fields[0])
        if len(matches) != 1 or not re.fullmatch(r"[a-fA-F0-9]{64}", matches[0]):
            raise UpdateError("校验附件缺少唯一的源码包 SHA-256。")
        body = fetch(archive_url, MAX_ARCHIVE)
        checksum = hashlib.sha256(body).hexdigest()
        if checksum != matches[0].lower():
            raise UpdateError("源码包 SHA-256 不匹配；未切换程序。")
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        releases = state / "releases"
        if releases.is_symlink():
            raise UpdateError("更新版本目录不能是符号链接。")
        releases.mkdir(exist_ok=True, mode=0o700)
        destination = releases / version
        if destination.exists():
            receipt = _verified_receipt(state, version)
            if receipt.get("archive_sha256") != checksum:
                raise UpdateError("同版本附件发生变化，已拒绝覆盖。")
            return {**result, "status": "staged", "path": str(destination), "sha256": checksum,
                    "message": "该版本已下载并校验；未改动正在运行的服务。"}
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=state) as temporary:
            temporary = Path(temporary)
            archive = temporary / "source.zip"
            archive.write_bytes(body)
            extracted = temporary / "source"
            extracted.mkdir()
            manifest = extract_source(archive, extracted, version)
            _write_json(temporary / "receipt.json", {"schema": 1, "version": version,
                        "repository": REPOSITORY, "archive_sha256": checksum, "files": manifest})
            # Atomic no-overwrite publication under the process lock in the CLI.
            if destination.exists():
                raise UpdateError("该版本已由另一实例准备，请重新检查。")
            extracted.rename(destination)
            try:
                (temporary / "receipt.json").rename(state / (version + ".receipt.json"))
            except Exception:
                shutil.rmtree(destination)
                raise
        return {**result, "status": "staged", "path": str(destination), "sha256": checksum,
                "message": "源码包已下载并校验；未执行下载代码，未覆盖旧程序或私人配置。"}
    except UpdateError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        raise UpdateError("源码包下载或校验未完成；当前程序保持原状。") from None


def _verified_receipt(state, version):
    if version != "baseline":
        version_key(version)
    root = state / ("baseline" if version == "baseline" else "releases/" + version)
    receipt_file = state / (version + ".receipt.json")
    if (state.joinpath("releases").is_symlink() or root.is_symlink() or not root.is_dir() or
            receipt_file.is_symlink()):
        raise UpdateError("未找到已校验的版本目录。")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        if receipt.get("version") != version or receipt.get("repository") != REPOSITORY:
            raise UpdateError("版本校验记录不匹配。")
        expected = receipt["files"]
        actual = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise UpdateError("已下载版本被替换为符号链接。")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                _safe_relative(relative)
                actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise UpdateError("已下载源码发生变化，请重新核验后使用。")
        return receipt
    except (OSError, ValueError, KeyError):
        raise UpdateError("版本校验记录无法读取。") from None


def _baseline_files(source):
    """Finite code locations; never snapshot an installed runtime wholesale."""
    top = {"version.json", "README.md", "LICENSE", ".gitignore", ".gitattributes"}
    suffixes = {".py", ".js", ".css", ".html", ".ps1", ".cmd", ".bat", ".command", ".md", ".txt", ".yaml"}
    paths = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        name = relative.as_posix()
        if relative.parts[0] not in {"mac", "windows", "tools"} and name not in top:
            continue
        try:
            _safe_relative(name)
        except UpdateError:
            continue
        if any(part in {"bin", "dist", "cache", ".venv", "venv", "runtime", "config", "run"}
               for part in relative.parts[:-1]):
            continue
        included = (name in top or
                    name in {"mac/console/config.example.json", "mac/console/config.windows.example.json"} or
                    (name.startswith("mac/console/app/") and path.suffix in {".py", ".js", ".css", ".html"}) or
                    (name.startswith("tools/") and path.suffix in {".py", ".ps1", ".cmd", ".command"}) or
                    (name.startswith("windows/") and (path.suffix in suffixes or
                     path.name in {".env.example", "frpc.example.toml"})))
        if included and path.is_file():
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source.parent):
                raise UpdateError("基线源码不能包含符号链接。")
            paths.append((path, name))
    return paths


def register_baseline(state, source):
    """Snapshot current audited source once, before the first source selection.

    This snapshots known program locations, never config/data or an old live
    service installation. A baseline is immutable and separate from release IDs.
    """
    state = Path(state).resolve()
    source = Path(source).resolve()
    if source == state or source in state.parents or state in source.parents:
        raise UpdateError("基线源码与私人更新目录必须分开。")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = state / "baseline"
    if destination.exists() or (state / "baseline.receipt.json").exists():
        _verified_receipt(state, "baseline")
        return {"status": "baseline", "path": str(destination), "message": "已有基线已复核，未覆盖。"}
    if (state / "active.json").exists():
        raise UpdateError("已有版本选择但没有基线；请先手动恢复原安装，未猜测回退来源。")
    try:
        metadata = json.loads((source / "version.json").read_text(encoding="utf-8"))
        version = metadata["version"]
        version_key(version)
        if metadata.get("repository") != REPOSITORY:
            raise UpdateError("基线必须来自本项目已经审查的统一源码。")
        files = _baseline_files(source)
        required = {"version.json", "tools/update.py", "mac/console/app/__main__.py",
                    "windows/sub2api-local/Start-Sub2API.ps1"}
        if not required <= {name for _, name in files}:
            raise UpdateError("基线缺少本项目双端程序入口。")
        with tempfile.TemporaryDirectory(prefix=".baseline-", dir=state) as temporary:
            temporary = Path(temporary)
            archive = temporary / "source.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
                for path, name in sorted(files, key=lambda pair: pair[1]):
                    package.write(path, PROJECT + "/" + name)
            extracted = temporary / "source"
            extracted.mkdir()
            manifest = extract_source(archive, extracted, version)
            receipt = {"schema": 1, "version": "baseline", "source_version": version,
                       "repository": REPOSITORY, "files": manifest,
                       "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            _write_json(temporary / "receipt.json", receipt)
            extracted.rename(destination)
            try:
                (temporary / "receipt.json").rename(state / "baseline.receipt.json")
            except Exception:
                shutil.rmtree(destination)
                raise
        return {"status": "baseline", "source_version": version, "path": str(destination),
                "message": "当前审核源码已独立保存为回滚基线；私人配置和现用服务未改动。"}
    except UpdateError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        raise UpdateError("无法保存基线；未选择新版本。") from None


def select(state, version):
    """Select source for the next deployment. Does not migrate/restart any service."""
    state = Path(state).resolve()
    _verified_receipt(state, version)
    previous = "baseline"
    active = state / "active.json"
    if active.exists():
        try:
            previous = json.loads(active.read_text(encoding="utf-8"))["version"]
            if previous != "baseline":
                version_key(previous)
        except (OSError, ValueError, KeyError):
            raise UpdateError("现用版本选择记录损坏；未覆盖。") from None
    else:
        # A successful first upgrade must have a real, launcher-readable rollback.
        _verified_receipt(state, "baseline")
    if previous == version:
        return {"status": "selected", "version": version, "message": "已选择该源码版本；服务未重启。"}
    _write_json(active, {"schema": 1, "version": version, "previous": previous})
    return {"status": "selected", "version": version, "previous": previous,
            "path": str(state / ("baseline" if version == "baseline" else "releases/" + version)),
            "message": "已选择下次部署使用的源码；请按平台部署步骤切换。现有服务与配置未改动。"}


def rollback(state):
    state = Path(state).resolve()
    try:
        active = json.loads((state / "active.json").read_text(encoding="utf-8"))
        previous = active["previous"]
        if not previous:
            raise UpdateError("尚无可回退的源码版本；原有安装保持原状。")
    except (OSError, ValueError, KeyError):
        raise UpdateError("没有可读取的源码版本切换记录。") from None
    return select(state, previous)


def resolve_active(state, fallback):
    """Resolve and reverify selected source before a caller explicitly launches it."""
    state = Path(state).resolve()
    active = state / "active.json"
    if not active.exists():
        return Path(fallback).resolve()
    if active.is_symlink():
        raise UpdateError("源码选择记录不能是符号链接。")
    try:
        version = json.loads(active.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        raise UpdateError("源码选择记录损坏；未启动其他代码。") from None
    _verified_receipt(state, version)
    return state / ("baseline" if version == "baseline" else "releases/" + version)
