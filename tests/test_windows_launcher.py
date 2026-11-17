"""Isolated build-cache checks; never runs a Windows service or real compiler."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mac/console"))
from app.releases import UpdateError

spec = importlib.util.spec_from_file_location("native_windows_launcher", ROOT / "tools/windows_console.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.source = self.folder / "audited-source"
        self.panel = self.source / "windows/local-model-console"
        for name, content in {
            "build.ps1": b"# compiler fixture; not executed\n",
            "LocalAI-Control.cs": b"// audited source fixture\n",
            "LocalAI.UI.xaml": b"<Window />\n",
            "LocalAI-GUI.ps1": b"# backend fixture\n",
            "app.manifest": b"<assembly />\n",
            "flags/CN.png": b"PNG fixture\n",
            "scripts/start-services.ps1": b"# service fixture; never executed\n",
        }.items():
            path = self.panel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.project = self.folder / "private-project"
        for name, content in {
            "config/frpc.toml": b'auth.token = "PRIVATE_FIXTURE_NOT_FOR_PROCESS_ARGUMENTS"\n',
            "config/model-settings.json": b'{"defaultModel":"existing-model"}\n',
            "scripts/start-services.ps1": b"# existing installation: must remain untouched\n",
            "data/user-state.json": b'{"preserve":"user data"}\n',
        }.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.runtime = self.folder / "private-runtime"
        self.config = self.folder / "private-settings.json"
        self.config.write_text(json.dumps({"runtime": str(self.runtime),
                                           "windows": {"project_root": str(self.project)}}), encoding="utf-8")
        self.before_source = snapshot(self.source)
        self.before_project = snapshot(self.project)
        self.before_config = self.config.read_bytes()

    def compile_fixture(self, arguments, **kwargs):
        self.assertEqual(arguments[0], "powershell.exe")
        self.assertEqual(kwargs["cwd"], self.panel)
        self.assertEqual(Path(arguments[arguments.index("-File") + 1]), self.panel / "build.ps1")
        self.assertNotIn("PRIVATE_FIXTURE_NOT_FOR_PROCESS_ARGUMENTS", " ".join(arguments))
        output = Path(arguments[arguments.index("-OutputDirectory") + 1])
        self.assertTrue(output.is_relative_to(self.runtime))
        self.assertFalse(output.is_relative_to(self.source))
        (output / "LocalAI-Control.exe").write_bytes(b"MZ isolated compiler output\n")
        (output / "app.ico").write_bytes(b"ICO fixture\n")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    def assert_private_and_source_untouched(self):
        self.assertEqual(snapshot(self.source), self.before_source)
        self.assertEqual(snapshot(self.project), self.before_project)
        self.assertEqual(self.config.read_bytes(), self.before_config)

    def cached_executable(self):
        return next((self.runtime / "data/windows-builds").glob("*/LocalAI-Control.exe"))

    def test_builds_outside_source_and_launches_existing_project_without_edits(self):
        with mock.patch.object(launcher.subprocess, "run", side_effect=self.compile_fixture) as compile_call, \
                mock.patch.object(launcher.subprocess, "call", return_value=0) as start:
            self.assertEqual(launcher.launch_windows(self.source, self.config, wait=True), 0)
        self.assertEqual(compile_call.call_count, 1)
        start.assert_called_once_with([str(self.cached_executable()), "--project-root", str(self.project)],
                                      cwd=self.project)
        self.assert_private_and_source_untouched()

    def test_reuses_verified_cache_and_rejects_modified_executable(self):
        with mock.patch.object(launcher.subprocess, "run", side_effect=self.compile_fixture) as compile_call, \
                mock.patch.object(launcher.subprocess, "call", return_value=0) as start:
            launcher.launch_windows(self.source, self.config, wait=True)
            launcher.launch_windows(self.source, self.config, wait=True)
            self.assertEqual(compile_call.call_count, 1)
            self.assertEqual(start.call_count, 2)
            executable = self.cached_executable()
            executable.write_bytes(b"tampered compiled program")
            with self.assertRaises(UpdateError):
                launcher.launch_windows(self.source, self.config, wait=True)
            self.assertEqual(compile_call.call_count, 1)
            self.assertEqual(start.call_count, 2)
            self.assertEqual(executable.read_bytes(), b"tampered compiled program")
        self.assert_private_and_source_untouched()

    def test_nonblocking_launch_has_no_inherited_stdio(self):
        with mock.patch.object(launcher.subprocess, "run", side_effect=self.compile_fixture), \
                mock.patch.object(launcher.subprocess, "Popen") as start:
            self.assertEqual(launcher.launch_windows(self.source, self.config), 0)
        start.assert_called_once_with([str(self.cached_executable()), "--project-root", str(self.project)],
                                      cwd=self.project, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assert_private_and_source_untouched()

    def test_failed_next_build_keeps_old_cache_and_cleans_temporary_output(self):
        with mock.patch.object(launcher.subprocess, "run", side_effect=self.compile_fixture), \
                mock.patch.object(launcher.subprocess, "call", return_value=0):
            launcher.launch_windows(self.source, self.config, wait=True)
        old_cache = snapshot(self.runtime / "data/windows-builds")
        source_file = self.panel / "LocalAI-Control.cs"
        source_file.write_bytes(source_file.read_bytes() + b"// new version\n")
        self.before_source = snapshot(self.source)
        failures = [subprocess.CompletedProcess(["compiler"], 1, b"", b"fixture error"),
                    subprocess.TimeoutExpired(["compiler"], 120), OSError("fixture compiler unavailable")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                def failed_build(arguments, **kwargs):
                    output = Path(arguments[arguments.index("-OutputDirectory") + 1])
                    (output / "partial.exe").write_bytes(b"incomplete")
                    if isinstance(failure, BaseException):
                        raise failure
                    return failure
                with mock.patch.object(launcher.subprocess, "run", side_effect=failed_build), \
                        mock.patch.object(launcher.subprocess, "Popen") as start:
                    with self.assertRaises(UpdateError):
                        launcher.launch_windows(self.source, self.config)
                    start.assert_not_called()
                self.assertEqual(snapshot(self.runtime / "data/windows-builds"), old_cache)
                self.assertFalse(list((self.runtime / "data/windows-builds").glob(".build-*")))
                self.assert_private_and_source_untouched()

    def test_missing_compiler_output_never_starts_program(self):
        with mock.patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                mock.patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaises(UpdateError):
                launcher.launch_windows(self.source, self.config)
            start.assert_not_called()
        self.assertFalse(list((self.runtime / "data/windows-builds").glob(".build-*")))
        self.assert_private_and_source_untouched()

    def test_cache_symlink_replacement_is_rejected_even_with_matching_bytes(self):
        with mock.patch.object(launcher.subprocess, "run", side_effect=self.compile_fixture), \
                mock.patch.object(launcher.subprocess, "call", return_value=0):
            launcher.launch_windows(self.source, self.config, wait=True)
        executable = self.cached_executable()
        external = self.folder / "external.exe"
        external.write_bytes(executable.read_bytes())
        executable.unlink()
        try:
            executable.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("This runner cannot create file symlinks.")
        with mock.patch.object(launcher.subprocess, "run") as compile_call, \
                mock.patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaises(UpdateError):
                launcher.launch_windows(self.source, self.config)
            compile_call.assert_not_called()
            start.assert_not_called()
        self.assert_private_and_source_untouched()

    def test_source_change_during_compilation_is_not_published(self):
        def concurrent_build(arguments, **kwargs):
            result = self.compile_fixture(arguments, **kwargs)
            (self.panel / "LocalAI-Control.cs").write_bytes(b"// externally edited during compilation\n")
            return result
        with mock.patch.object(launcher.subprocess, "run", side_effect=concurrent_build), \
                mock.patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaises(UpdateError):
                launcher.launch_windows(self.source, self.config)
            start.assert_not_called()
        self.assertEqual((self.panel / "LocalAI-Control.cs").read_bytes(), b"// externally edited during compilation\n")
        self.assertEqual(snapshot(self.project), self.before_project)
        self.assertEqual(self.config.read_bytes(), self.before_config)
        self.assertFalse(list((self.runtime / "data/windows-builds").iterdir()))

    def test_in_source_private_directories_are_rejected_before_compiling(self):
        for project, runtime in [(self.project, self.source / "runtime"),
                                 (self.source / "project", self.runtime)]:
            with self.subTest(project=project, runtime=runtime):
                self.config.write_text(json.dumps({"runtime": str(runtime),
                                                   "windows": {"project_root": str(project)}}), encoding="utf-8")
                with mock.patch.object(launcher.subprocess, "run") as compile_call:
                    with self.assertRaises(UpdateError):
                        launcher.launch_windows(self.source, self.config)
                    compile_call.assert_not_called()
        self.assertEqual(snapshot(self.source), self.before_source)
        self.assertEqual(snapshot(self.project), self.before_project)


if __name__ == "__main__":
    unittest.main()
