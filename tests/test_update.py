import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "mac/console/app/releases.py"
spec = importlib.util.spec_from_file_location("project_releases", MODULE)
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)
PROJECT = updates.PROJECT
REPO = updates.REPOSITORY


def source_zip(version="0.2.0-preview.1", extra=None):
    files = {
        "version.json": json.dumps({"version": version, "repository": REPO}),
        "tools/update.py": "# source fixture, never executed\n",
        "mac/console/app/__main__.py": "# source fixture\n",
        "windows/sub2api-local/Start-Sub2API.ps1": "# source fixture\n",
        "windows/sub2api-local/.env.example": "EXAMPLE_ONLY=not-a-secret\n",
    }
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            # ZipInfo construction normalizes Windows backslashes and NULs.
            # Retain the exact supplied spelling to exercise hostile archives
            # identically on every runner, including Windows.
            info = zipfile.ZipInfo(PROJECT + "/fixture")
            info.filename = info.orig_filename = PROJECT + "/" + name
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def remote_fixture(version="0.2.0-preview.1", body=None):
    archive = source_zip(version) if body is None else body
    stem = PROJECT + "-" + version
    archive_name = stem + "-source.zip"
    checksum_name = stem + "-SHA256SUMS.txt"
    base = "https://github.com/" + REPO + "/releases/download/v" + version + "/"
    release = {"tag_name": "v" + version, "draft": False, "prerelease": "-" in version,
               "html_url": "https://github.com/" + REPO + "/releases/tag/v" + version,
               "assets": [{"name": name, "browser_download_url": base + name}
                          for name in (archive_name, checksum_name)]}
    def fetch(url, maximum):
        if url.endswith("/releases/latest") or url.endswith("/releases?per_page=100"):
            data = [release] if "?" in url else release
            return json.dumps(data).encode()
        if url == base + archive_name:
            return archive
        if url == base + checksum_name:
            return (hashlib.sha256(archive).hexdigest() + "  " + archive_name + "\n").encode()
        raise AssertionError("Unexpected URL: " + url)
    return fetch, release


def register_test_baseline(state):
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder)
        updates.extract_source(io.BytesIO(source_zip("0.1.0")), source, "0.1.0")
        # A source checkout can have ignored private runtime files. Baseline
        # collection must never copy them into the frozen code snapshot.
        (source / "mac/console/config").mkdir()
        (source / "mac/console/config/settings.json").write_text("PRIVATE_FIXTURE")
        (source / "windows/sub2api-local/.env").write_text("PRIVATE_FIXTURE")
        return updates.register_baseline(state, source)


class UpdateTests(unittest.TestCase):
    def test_repository_baseline_keeps_native_windows_build_inputs(self):
        repository = MODULE.parents[3]
        expected = {
            "windows/local-model-console/LocalAI-Control.cs",
            "windows/local-model-console/LocalAI.UI.xaml",
            "windows/local-model-console/LocalAI-GUI.ps1",
            "windows/local-model-console/build.ps1",
            "windows/local-model-console/app.manifest",
            "windows/local-model-console/scripts/start-services.ps1",
            "windows/local-model-console/examples/model-settings.example.json",
            "windows/local-model-console/examples/panel-routes.example.json",
            "tools/windows_console.py",
            "mac/console/config.windows.example.json",
        }
        expected.update("windows/local-model-console/flags/" + code + ".png"
                        for code in ("CN", "DE", "GB", "HK", "JP", "SG", "US"))
        allowlist = set(json.loads((repository / "source-files.json").read_text(encoding="utf-8")))
        self.assertTrue(expected <= allowlist, "Release manifest omits Windows build or launcher inputs")
        before = {name: (repository / name).read_bytes() for name in expected}
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "updates"
            updates.register_baseline(state, repository)
            receipt = updates._verified_receipt(state, "baseline")
            self.assertTrue(expected <= set(receipt["files"]))
            for name, content in before.items():
                self.assertEqual((state / "baseline" / name).read_bytes(), content, name)
                self.assertEqual((repository / name).read_bytes(), content, name)

    def test_legacy_baseline_excludes_windows_runtime_and_fixture_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            updates.extract_source(io.BytesIO(source_zip("0.1.0")), source, "0.1.0")
            private = {
                "windows/local-model-console/state/panel-runtime/LocalAI-Backend.ps1",
                "windows/local-model-console/fixtures/fixture-start-services.ps1",
                "windows/local-model-console/ui-regression/fixture.cs",
                "windows/local-model-console/switch-recovery-fixtures-123/helper.ps1",
                "windows/local-model-console/config/panel-routes.json",
                "windows/local-model-console/logs/event.txt",
            }
            for name in private:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"PRIVATE FIXTURE: must not enter an update baseline")
            for name in ("LocalAI-Control.cs", "LocalAI.UI.xaml", "app.manifest", "flags/CN.png"):
                original = MODULE.parents[3] / "windows/local-model-console" / name
                target = source / "windows/local-model-console" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original.read_bytes())
            state = root / "updates"
            updates.register_baseline(state, source)
            receipt = updates._verified_receipt(state, "baseline")
            self.assertTrue(private.isdisjoint(receipt["files"]))
            for name in private:
                self.assertFalse((state / "baseline" / name).exists(), name)
                self.assertEqual((source / name).read_bytes(), b"PRIVATE FIXTURE: must not enter an update baseline")
            for name in ("LocalAI-Control.cs", "LocalAI.UI.xaml", "app.manifest", "flags/CN.png"):
                relative = "windows/local-model-console/" + name
                self.assertEqual((state / "baseline" / relative).read_bytes(), (source / relative).read_bytes())

    def test_unpublished_is_not_a_failure(self):
        def missing(url, maximum):
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        self.assertEqual(updates.discover("stable", fetch=missing)["status"], "unpublished")
        self.assertEqual(updates.discover("preview", fetch=lambda u, m: b"[]")["status"], "unpublished")

    def test_channel_and_semver_order(self):
        fetch, _ = remote_fixture()
        self.assertEqual(updates.discover("stable", fetch=fetch)["status"], "unpublished")
        self.assertEqual(updates.discover("preview", "0.1.0", fetch)["status"], "available")
        self.assertGreater(updates.version_key("0.2.0"), updates.version_key("0.2.0-rc.9"))
        self.assertGreater(updates.version_key("0.2.0-rc.1"), updates.version_key("0.2.0-preview.99"))

    def test_stable_chooses_highest_semver_not_github_latest_label(self):
        _, older = remote_fixture("0.2.0")
        _, newer = remote_fixture("0.3.0")
        _, preview = remote_fixture("0.4.0-preview.1")
        def fetch(url, maximum):
            self.assertNotIn("/latest", url)
            return json.dumps([older, preview, newer]).encode()
        self.assertEqual(updates.discover("stable", "0.1.0", fetch)["version"], "0.3.0")

    def test_redirect_domain_allowlist_is_exact(self):
        for host in updates.GITHUB_HOSTS:
            self.assertTrue(updates._official_url("https://" + host + "/asset"))
        for url in ["https://usercontent.example/a", "https://user-images.githubusercontent.com/a",
                    "https://raw.githubusercontent.com/a", "http://github.com/a",
                    "https://github.com.evil.example/a", "https://github.com:444/a", "https://user@github.com/a"]:
            self.assertFalse(updates._official_url(url))

    def test_stage_select_rollback_preserves_private_and_old_code(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "updates"
            private = Path(folder) / "config/settings.json"
            private.parent.mkdir()
            private.write_bytes(b"PRIVATE FIXTURE; must remain unchanged")
            register_test_baseline(state)
            fetch, _ = remote_fixture()
            staged = updates.stage(state, "preview", "0.1.0", fetch)
            self.assertEqual(staged["status"], "staged")
            self.assertFalse((state / "active.json").exists())
            first = Path(staged["path"])
            first_bytes = (first / "version.json").read_bytes()
            updates.select(state, "0.2.0-preview.1")
            fetch, _ = remote_fixture("0.3.0-preview.1")
            updates.stage(state, "preview", "0.2.0-preview.1", fetch)
            updates.select(state, "0.3.0-preview.1")
            self.assertEqual(updates.rollback(state)["version"], "0.2.0-preview.1")
            self.assertEqual((first / "version.json").read_bytes(), first_bytes)
            self.assertEqual(private.read_bytes(), b"PRIVATE FIXTURE; must remain unchanged")

    def test_bad_hash_no_selection_no_version_files(self):
        fetch, _ = remote_fixture()
        def corrupt(url, maximum):
            data = fetch(url, maximum)
            return b"tampered" if url.endswith("source.zip") else data
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with self.assertRaisesRegex(updates.UpdateError, "SHA-256"):
                updates.stage(state, "preview", fetch=corrupt)
            self.assertFalse((state / "active.json").exists())
            self.assertFalse((state / "releases").exists())

    def test_other_repository_or_duplicate_asset_rejected(self):
        _, release = remote_fixture()
        release["assets"][0]["browser_download_url"] = "https://evil.example/source.zip"
        with self.assertRaises(updates.UpdateError):
            updates._asset(release, release["assets"][0]["name"], "0.2.0-preview.1")
        release["assets"].append(release["assets"][1])
        with self.assertRaises(updates.UpdateError):
            updates._asset(release, release["assets"][1]["name"], "0.2.0-preview.1")

    def test_unsafe_zip_paths_and_private_files(self):
        names = ["../escape", "/escape", "C:/escape", "a\\escape", "a\x00hidden", "data/private.txt", "logs/a.log",
                 "config/settings.json", "windows/sub2api-local/.env", "frp/frpc.toml", "CON.txt",
                 "a./file", "private.key"]
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(updates.UpdateError):
                    updates.extract_source(io.BytesIO(source_zip(extra={name: "bad"})), Path(folder), "0.2.0-preview.1")
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_zip_symlink_duplicate_and_file_directory_conflict(self):
        for variant in ("symlink", "duplicate", "conflict"):
            buffer = io.BytesIO(source_zip())
            with zipfile.ZipFile(buffer, "a") as archive:
                if variant == "symlink":
                    info = zipfile.ZipInfo(PROJECT + "/link")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, "/private")
                elif variant == "duplicate":
                    archive.writestr(PROJECT + "/VERSION.JSON", "conflict")
                else:
                    archive.writestr(PROJECT + "/tools", "conflict")
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(updates.UpdateError):
                    updates.extract_source(io.BytesIO(buffer.getvalue()), Path(folder), "0.2.0-preview.1")
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_local_changes_block_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            fetch, _ = remote_fixture()
            stage = updates.stage(state, "preview", fetch=fetch)
            (Path(stage["path"]) / "tools/update.py").write_text("changed")
            with self.assertRaises(updates.UpdateError):
                updates.select(state, "0.2.0-preview.1")
            self.assertFalse((state / "active.json").exists())

    def test_selected_source_is_resolved_by_actual_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "updates"
            config = Path(folder) / "settings.json"
            config.write_text("{}")
            fallback = Path(folder) / "original"
            self.assertEqual(updates.resolve_active(state, fallback), fallback.resolve())
            register_test_baseline(state)
            fetch, _ = remote_fixture()
            staged = updates.stage(state, "preview", fetch=fetch)
            updates.select(state, "0.2.0-preview.1")
            self.assertEqual(updates.resolve_active(state, fallback), Path(staged["path"]))
            launcher = MODULE.parents[3] / "tools/launch.py"
            process = subprocess.run([sys.executable, "-B", str(launcher), "--state", str(state),
                                      "--config", str(config), "--show-source"],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["source"], staged["path"])
            self.assertFalse(result["started"])
            self.assertEqual(config.read_text(), "{}")

    def test_first_selection_rolls_back_to_real_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            fetch, _ = remote_fixture()
            updates.stage(state, "preview", fetch=fetch)
            with self.assertRaises(updates.UpdateError):
                updates.select(state, "0.2.0-preview.1")
            register_test_baseline(state)
            updates.select(state, "0.2.0-preview.1")
            self.assertEqual(updates.rollback(state)["version"], "baseline")
            self.assertEqual(updates.resolve_active(state, Path("/unused")), state.resolve() / "baseline")
            self.assertFalse((state / "baseline/mac/console/config/settings.json").exists())
            self.assertFalse((state / "baseline/windows/sub2api-local/.env").exists())
            metadata = json.loads((state / "baseline/version.json").read_text())
            self.assertEqual(metadata["version"], "0.1.0")
            self.assertEqual(updates.register_baseline(state, Path("/not-used"))["status"], "baseline")

    def test_version_mismatch_and_zip_expansion_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(updates.UpdateError):
                updates.extract_source(io.BytesIO(source_zip("0.3.0")), Path(folder), "0.2.0-preview.1")
            old = updates.MAX_EXPANDED
            try:
                updates.MAX_EXPANDED = 50
                with self.assertRaises(updates.UpdateError):
                    updates.extract_source(io.BytesIO(source_zip()), Path(folder), "0.2.0-preview.1")
            finally:
                updates.MAX_EXPANDED = old

    def test_actual_baseline_console_start_does_not_pollute_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "updates"
            repository = MODULE.parents[3]
            updates.register_baseline(state, repository)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            config = root / "settings.json"
            config.write_text(json.dumps({"runtime": str(root / "runtime"), "port": port,
                                          "ollama": {"url": "http://127.0.0.1:1"},
                                          "sub2api": {"mode": "unconfigured"}}))
            # Simulate a selected newer release, then a rollback to baseline.
            fetch, _ = remote_fixture()
            updates.stage(state, "preview", fetch=fetch)
            updates.select(state, "0.2.0-preview.1")
            updates.rollback(state)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "0"  # Launcher must override it.
            # A timer installed by a -c/runpy wrapper is lost at the launcher's
            # real os.execvpe. An isolated user-site hook is imported by both
            # interpreters, so a slow post-exec startup produces useful stacks
            # without modifying the selected, integrity-checked source.
            userbase = root / "child-python-diagnostics"
            environment["PYTHONUSERBASE"] = str(userbase)
            environment.pop("PYTHONNOUSERSITE", None)
            site_query = subprocess.run([sys.executable, "-S", "-c",
                                         "import json,site; print(json.dumps(site.getusersitepackages()))"],
                                        env=environment, capture_output=True, text=True, check=True, timeout=10)
            user_site = Path(json.loads(site_query.stdout))
            self.assertIn(userbase.resolve(), user_site.resolve().parents)
            user_site.mkdir(parents=True)
            (user_site / "sitecustomize.py").write_text(
                "import faulthandler\n"
                "faulthandler.enable()\n"
                "faulthandler.dump_traceback_later(15, repeat=True)\n", encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-B", str(repository / "tools/launch.py"),
                                        "--state", str(state), "--config", str(config), "--foreground"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = "http://127.0.0.1:" + str(port)
            started_at = time.monotonic()
            child_output = None

            def collect_child_output(stop=False):
                nonlocal child_output
                if child_output is None:
                    if stop and process.poll() is None:
                        process.terminate()  # Only the child created by this test.
                    try:
                        child_output = process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        child_output = process.communicate(timeout=10)
                return child_output

            try:
                if os.name == "nt":
                    # A Mac-only settings file cannot select a Windows project;
                    # the launcher must fail safely without opening either UI.
                    stdout, stderr = collect_child_output()
                    self.assertEqual(process.returncode, 1, stderr)
                    self.assertEqual(stdout, b"")
                    self.assertIn(b"Windows", stderr)
                    self.assertIn(b"update.py", stderr)
                else:
                    session = None
                    last_error = "no response"
                    deadline = started_at + 60
                    while time.monotonic() < deadline:
                        try:
                            with op.open(base + "/api/session", timeout=0.2) as response:
                                session = json.load(response)
                            break
                        except OSError as error:
                            last_error = type(error).__name__
                            if process.poll() is not None:
                                break
                            time.sleep(0.05)
                    startup_elapsed = time.monotonic() - started_at
                    if session is None:
                        # Read pipes after terminating the child: reading a live
                        # pipe can block forever and previously hid all stacks.
                        stdout, stderr = collect_child_output(stop=True)
                        self.fail("baseline console did not become ready after %.2fs; exit=%s; last=%s\n"
                                  "child stdout:\n%s\nchild stderr/stacks:\n%s" %
                                  (startup_elapsed, process.returncode, last_error,
                                   stdout.decode("utf-8", errors="replace"),
                                   stderr.decode("utf-8", errors="replace")))
                    self.assertEqual(session["service"], "local-ai-console")
                    request = urllib.request.Request(base + "/api/shutdown", data=b"{}",
                                                      headers={"Content-Type": "application/json", "X-Local-CSRF": session["csrf"]})
                    with op.open(request, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                    stdout, stderr = collect_child_output()
                    if stderr:
                        print("baseline child startup %.2fs; stderr/stacks:\n%s" %
                              (startup_elapsed, stderr.decode("utf-8", errors="replace")), file=sys.stderr)
                    self.assertEqual(process.returncode, 0)
                self.assertFalse(list((state / "baseline").rglob("__pycache__")))
                updates._verified_receipt(state, "baseline")
            finally:
                collect_child_output(stop=True)
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
