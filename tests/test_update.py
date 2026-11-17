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
            archive.writestr(PROJECT + "/" + name, content)
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
        names = ["../escape", "/escape", "C:/escape", "a\\escape", "data/private.txt", "logs/a.log",
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
            process = subprocess.Popen([sys.executable, "-B", str(repository / "tools/launch.py"),
                                        "--state", str(state), "--config", str(config), "--foreground"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = "http://127.0.0.1:" + str(port)
            try:
                session = None
                for attempt in range(100):
                    try:
                        with op.open(base + "/api/session", timeout=0.2) as response:
                            session = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
                self.assertIsNotNone(session, process.stderr.read().decode() if process.poll() is not None else "console did not start")
                self.assertEqual(session["service"], "local-ai-console")
                request = urllib.request.Request(base + "/api/shutdown", data=b"{}",
                                                  headers={"Content-Type": "application/json", "X-Local-CSRF": session["csrf"]})
                with op.open(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0)
                self.assertFalse(list((state / "baseline").rglob("__pycache__")))
                updates._verified_receipt(state, "baseline")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
