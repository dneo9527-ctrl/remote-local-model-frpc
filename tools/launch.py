#!/usr/bin/env python3
"""Explicit console launcher using the selected, verified source release.

Private settings stay at the supplied --config path. This launcher starts the
web console only; it never automatically starts/stops Windows model/container
services. Quit the old console before switching versions; an already-running
console is reused by its platform launcher and is not replaced on the fly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if sys.version_info < (3, 11):
    raise SystemExit("An existing Python 3.11+ is required. Nothing was installed.")

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "mac/console"))
from app.core import FileLock, UserError  # noqa: E402
from app.releases import UpdateError, resolve_active  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=os.environ.get("REMOTE_MODEL_CONFIG"))
    parser.add_argument("--state", type=Path, default=os.environ.get("REMOTE_MODEL_UPDATE_STATE"))
    parser.add_argument("--foreground", action="store_true", help="Keep console in this terminal")
    parser.add_argument("--owner-pid", type=int, help="Menu app parent; only with --foreground")
    parser.add_argument("--show-source", action="store_true", help="Resolve selected source without starting it")
    args = parser.parse_args()
    if args.owner_pid is not None and (not args.foreground or args.owner_pid != os.getppid()):
        parser.error("--owner-pid must name the foreground launcher's actual parent")
    if not args.config:
        parser.error("Supply --config /private/settings.json or REMOTE_MODEL_CONFIG. Do not put credentials in the source tree.")
    config = args.config.expanduser().resolve()
    try:
        if not config.is_file() or config == ROOT or ROOT in config.parents:
            raise UpdateError("请选择源码目录之外的现有私人配置文件。")
        def run(source):
            if source in config.parents:
                raise UpdateError("私人配置不能放在已下载源码目录中。")
            if args.show_source:
                print(json.dumps({"source": str(source), "started": False}, ensure_ascii=False))
                return 0
            if os.name == "nt":
                if not (source / "tools/windows_console.py").is_file():
                    raise UpdateError("所选版本尚无 Windows 原生启动器；update.py 可独立检查更新。")
                sys.path.insert(0, str(source / "tools"))
                from windows_console import launch_windows
                return launch_windows(source, config, args.foreground)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            # Prevent an inherited Python module path from replacing this source.
            environment.pop("PYTHONPATH", None)
            arguments = [sys.executable, "-B", "-m", "app",
                         "serve" if args.foreground else "open", "--config", str(config)]
            if args.owner_pid is not None:
                arguments.extend(["--owner-pid", str(args.owner_pid)])
            if args.foreground:
                # The foreground console owns this process, so a targeted stop
                # cannot orphan a nested service behind a launcher process.
                os.chdir(source / "mac/console")
                os.execvpe(sys.executable, arguments, environment)
            return subprocess.call(arguments, cwd=source / "mac/console", env=environment)
        if args.state:
            state = args.state.expanduser().resolve()
            if state == ROOT or ROOT in state.parents:
                raise UpdateError("更新存储应放在源码目录之外。")
            with FileLock(state / "update.lock"):
                source = resolve_active(state, ROOT)
            # Previous code directories remain intact while a console uses them.
            return run(source)
        return run(ROOT)
    except (UpdateError, UserError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
