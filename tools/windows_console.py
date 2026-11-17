"""Build the selected native Windows UI outside immutable source releases."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def launch_windows(source, config, wait=False):
    from app.core import FileLock
    from app.releases import UpdateError

    source = Path(source).resolve()
    try:
        settings = json.loads(config.read_text(encoding="utf-8-sig"))
        project = Path(settings["windows"]["project_root"]).expanduser()
        runtime = Path(settings["runtime"]).expanduser()
    except (OSError, ValueError, KeyError, TypeError):
        raise UpdateError("Windows 启动需要私人设置中的 runtime 和 windows.project_root；update.py 仍可独立检查更新。") from None
    if not project.is_absolute() or not runtime.is_absolute():
        raise UpdateError("Windows 项目和运行目录须使用明确的绝对路径。")
    project, runtime = project.resolve(), runtime.resolve()
    if (runtime == source or source in runtime.parents or
            project == source or source in project.parents):
        raise UpdateError("Windows 项目和编译缓存必须放在已校验源码之外。")
    if not all((project / name).is_file() for name in
               ("config/frpc.toml", "scripts/start-services.ps1")):
        raise UpdateError("Windows 原模型项目缺少配置或启动入口；未创建或覆盖项目配置。")
    panel = source / "windows/local-model-console"
    if not all((panel / name).is_file() for name in
               ("build.ps1", "LocalAI-Control.cs", "LocalAI.UI.xaml", "LocalAI-GUI.ps1")):
        raise UpdateError("所选源码尚无 Windows 原生面板，请回退或选择包含该组件的版本。")
    # Bind compiled output to its exact input, independent of a mutable version label.
    def input_manifest():
        files = [(path.relative_to(panel).as_posix(), path) for path in panel.rglob("*")
                 if path.is_file() and path.suffix.lower() in (".cs", ".ps1", ".xaml", ".png", ".manifest")]
        if any(path.is_symlink() for _, path in files):
            raise UpdateError("Windows 面板源码不能包含符号链接。")
        return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files)}
    manifest = input_manifest()
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    cache = runtime / "data/windows-builds"
    if cache.is_symlink():
        raise UpdateError("Windows 编译缓存目录不能是符号链接。")
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / digest
    with FileLock(runtime / "data/windows-build.lock"):
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=".build-", dir=cache))
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(panel / "build.ps1"), "-OutputDirectory", str(temporary)],
                    capture_output=True, timeout=120, cwd=panel)
                if result.returncode:
                    raise UpdateError("Windows 面板编译未完成；旧版本、模型和私人配置保持原状。")
                if input_manifest() != manifest:
                    raise UpdateError("编译过程中 Windows 源码发生变化；未使用该产物。")
                executable = temporary / "LocalAI-Control.exe"
                if not executable.is_file() or executable.is_symlink():
                    raise UpdateError("Windows 编译没有生成预期面板；未启动。")
                # Write a receipt for local cache integrity, never a Release signature.
                receipt = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in temporary.iterdir() if path.is_file()}
                (temporary / "build.json").write_text(json.dumps(receipt), encoding="utf-8")
                temporary.rename(destination)
            except (OSError, subprocess.TimeoutExpired):
                raise UpdateError("Windows 面板编译失败或超时；未改变原模型服务。") from None
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        try:
            if (destination.is_symlink() or not destination.is_dir() or
                    any(path.is_symlink() or not path.is_file() for path in destination.iterdir())):
                raise ValueError()
            receipt = json.loads((destination / "build.json").read_text(encoding="utf-8"))
            actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in destination.iterdir() if path.is_file() and path.name != "build.json"}
            if actual != receipt or "LocalAI-Control.exe" not in actual:
                raise ValueError()
        except (OSError, ValueError):
            raise UpdateError("Windows 编译缓存发生变化；未启动，请保留旧版并重新准备缓存。") from None
    arguments = [str(destination / "LocalAI-Control.exe"), "--project-root", str(project)]
    if wait:
        return subprocess.call(arguments, cwd=project)
    subprocess.Popen(arguments, cwd=project, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0
