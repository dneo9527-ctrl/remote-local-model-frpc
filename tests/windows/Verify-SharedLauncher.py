"""Native Windows build/cache checks; only the final GUI creation is mocked.

Run on Windows with existing Python 3.11+ and PowerShell 5.1. All configuration,
build caches and source baselines use a disposable isolated directory. No GUI,
Ollama, frpc or VPS process is started.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "mac/console"))
from app.releases import UpdateError, _verified_receipt, register_baseline  # noqa: E402


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def verify(output):
    checks = []
    build_runs = []
    gui_launches = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    report = {"schema": 1, "commit": os.environ.get("GITHUB_SHA"), "success": False,
              "scope": "Real Windows PowerShell build and immutable-source/cache checks. "
                       "Final GUI calls only are mocked; no window or model/tunnel service is started.",
              "checks": checks}
    try:
        if os.name != "nt":
            raise RuntimeError("Native Windows is required; this check does not simulate a compiler on other platforms.")
        with tempfile.TemporaryDirectory(prefix="local-ai-shared-launcher-") as folder:
            temporary = Path(folder).resolve()
            state = temporary / "updates"
            register_baseline(state, REPOSITORY)
            source = state / "baseline"
            receipt_path = state / "baseline.receipt.json"
            receipt_before = receipt_path.read_bytes()
            source_before = snapshot(source)
            project = temporary / "Existing project with spaces"
            (project / "config").mkdir(parents=True)
            (project / "scripts").mkdir()
            (project / "config/frpc.toml").write_text(
                "# Offline placeholder. No credentials and no running frpc.\n"
                "serverAddr = '192.0.2.10'\nserverPort = 7000\n", encoding="utf-8")
            (project / "scripts/start-services.ps1").write_text(
                "throw 'CI placeholder: this project helper must never execute.'\n", encoding="utf-8")
            project_before = snapshot(project)
            runtime = temporary / "Separate runtime"
            config = temporary / "private-settings.json"
            config.write_text(json.dumps({"runtime": str(runtime),
                                          "windows": {"project_root": str(project)}}), encoding="utf-8")
            settings_before = config.read_bytes()
            module_file = source / "tools/windows_console.py"
            spec = importlib.util.spec_from_file_location("verified_windows_console", module_file)
            launcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher)
            panel = source / "windows/local-model-console"

            def real_build(arguments, **kwargs):
                # Forward the production command unchanged to the real standard
                # library. Replacing this module binding does not mock the
                # stdlib Popen used internally by subprocess.run.
                check(arguments[0].lower() == "powershell.exe", "launcher invokes the real PowerShell compiler host")
                check(Path(arguments[arguments.index("-File") + 1]) == panel / "build.ps1",
                      "only the verified native build script is executed")
                destination = Path(arguments[arguments.index("-OutputDirectory") + 1])
                check(runtime.resolve() in destination.resolve().parents,
                      "native compilation writes only to the separate runtime")
                result = subprocess.run(arguments, **kwargs)
                build_runs.append(result.returncode)
                (output / "shared-launcher-build-stdout.txt").write_text(
                    result.stdout.decode("utf-8", errors="replace"), encoding="utf-8")
                (output / "shared-launcher-build-stderr.txt").write_text(
                    result.stderr.decode("utf-8", errors="replace"), encoding="utf-8")
                return result

            def final_gui(arguments, **kwargs):
                check(arguments[1:] == ["--project-root", str(project.resolve())],
                      "GUI receives the existing project path without secrets")
                check(Path(arguments[0]).is_file() and runtime.resolve() in Path(arguments[0]).resolve().parents,
                      "final GUI target is a real compiled executable in the runtime")
                check(Path(kwargs["cwd"]) == project.resolve(), "GUI working directory is the explicit existing project")
                gui_launches.append(arguments)
                return 0

            def final_gui_background(arguments, **kwargs):
                check(all(kwargs.get(name) == subprocess.DEVNULL for name in ("stdin", "stdout", "stderr")),
                      "background GUI detaches its standard streams")
                final_gui(arguments, **kwargs)
                return SimpleNamespace(pid=0)  # No GUI subprocess is created.

            proxy = SimpleNamespace(run=real_build, call=final_gui, Popen=final_gui_background,
                                    DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
            with patch.object(launcher, "subprocess", proxy):
                check(launcher.launch_windows(source, config, wait=True) == 0,
                      "first shared launch completes a real native build")
                check(build_runs == [0] and len(gui_launches) == 1,
                      "first launch compiles once and reaches only the mocked GUI boundary")
                executable = Path(gui_launches[0][0])
                cache = executable.parent
                check(executable.read_bytes()[:2] == b"MZ", "native build creates a PE executable")
                check((cache / "app.ico").is_file() and (cache / "app.ico").stat().st_size > 0,
                      "native build creates the application icon resource")
                check((cache / "build.json").is_file(), "cache integrity receipt is created outside source")
                build_receipt = json.loads((cache / "build.json").read_text(encoding="utf-8"))
                check(set(build_receipt) == {"LocalAI-Control.exe", "app.ico"},
                      "cache receipt names exactly the produced native executable and icon")
                check(all(hashlib.sha256((cache / name).read_bytes()).hexdigest() == digest
                          for name, digest in build_receipt.items()), "native output hashes match the cache receipt")

                resource_script = temporary / "Read-Resources.ps1"
                resource_script.write_text(
                    "param([string]$Executable)\n$ErrorActionPreference='Stop'\n"
                    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
                    "$assembly=[Reflection.Assembly]::ReflectionOnlyLoadFrom($Executable)\n"
                    "$assembly.GetManifestResourceNames() | ConvertTo-Json -Compress\n", encoding="utf-8")
                resources_result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(resource_script), "-Executable", str(executable)],
                    capture_output=True, timeout=30, check=True)
                resources = set(json.loads(resources_result.stdout.decode("utf-8-sig")))
                expected_resources = {"LocalAI.Backend.ps1", "LocalAI.UI.xaml", "LocalAI.StartServices.ps1"}
                expected_resources.update("LocalAI.Flag." + code + ".png"
                                          for code in ("CN", "DE", "GB", "HK", "JP", "SG", "US"))
                check(expected_resources <= resources, "compiled executable embeds the real backend, WPF, helper and seven flags")
                report["embedded_resources"] = sorted(resources)
                cache_before = snapshot(cache)
                timestamps_before = {path.name: path.stat().st_mtime_ns for path in cache.iterdir()}
                check(launcher.launch_windows(source, config, wait=False) == 0,
                      "second shared launch accepts the existing verified cache")
                check(build_runs == [0] and len(gui_launches) == 2,
                      "second launch reuses the native cache without recompiling")
                check(snapshot(cache) == cache_before and
                      {path.name: path.stat().st_mtime_ns for path in cache.iterdir()} == timestamps_before,
                      "cache reuse does not rewrite executable, icon or receipt")
                executable.write_bytes(executable.read_bytes() + b"CI TAMPER FIXTURE")
                try:
                    launcher.launch_windows(source, config, wait=True)
                except UpdateError:
                    checks.append("tampered compiled cache is rejected")
                else:
                    raise AssertionError("tampered compiled cache was accepted")
                check(build_runs == [0] and len(gui_launches) == 2,
                      "tampered cache neither recompiles nor reaches GUI creation")

            check(snapshot(project) == project_before, "private project and its service helper remain byte-for-byte unchanged")
            check(config.read_bytes() == settings_before, "private launcher settings remain byte-for-byte unchanged")
            check(snapshot(source) == source_before, "verified source remains byte-for-byte unchanged after build/cache checks")
            check(receipt_path.read_bytes() == receipt_before, "source verification receipt is not rewritten or polluted")
            check(_verified_receipt(state, "baseline")["files"] == source_before,
                  "source verification still succeeds after build, cache reuse and tamper rejection")
            check(not list(source.rglob("__pycache__")) and not list(source.rglob("*.exe")) and
                  not list(source.rglob("app.ico")), "compiled binaries, icons and Python caches never enter verified source")
            check(not list((runtime / "data/windows-builds").glob(".build-*")),
                  "temporary build directories are removed after compilation")
            report["native_builds"] = len(build_runs)
            report["mocked_gui_boundaries"] = len(gui_launches)
            report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["passed"] = len(checks)
        (output / "shared-launcher-checks.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SHARED_WINDOWS_LAUNCHER_CHECKS_PASSED=" + str(len(checks)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    verify(arguments.output_dir.resolve())
