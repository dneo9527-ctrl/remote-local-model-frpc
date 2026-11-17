#!/usr/bin/env python3
"""Check, stage and select verified source releases for both platform maintainers.

No command here executes downloaded code, restarts a service, or edits private
settings. `select`/`rollback` change only an atomic source-selection receipt.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("This updater requires an existing Python 3.11 or newer; nothing was installed.")

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "mac/console"))
from app import VERSION  # noqa: E402
from app.core import FileLock, UserError  # noqa: E402
from app.releases import UpdateError, discover, register_baseline, rollback, select, stage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "stage", "baseline", "select", "rollback", "status"])
    parser.add_argument("--channel", choices=["stable", "preview"], default="preview")
    parser.add_argument("--current", default=VERSION)
    parser.add_argument("--version", help="Verified staged source version for select")
    parser.add_argument("--source", type=Path, default=ROOT, help="Audited current source for baseline")
    parser.add_argument("--state", type=Path, help="Private update store; required for stage/select/rollback/status")
    args = parser.parse_args()
    if args.action != "check" and args.state is None:
        parser.error("--state must name a private update store outside the source checkout")
    try:
        if args.action == "check":
            result = discover(args.channel, args.current)
            result.pop("release", None)
        else:
            state = args.state.expanduser().resolve()
            if state == ROOT or ROOT in state.parents:
                raise UpdateError("更新存储应放在源码目录外，防止私人状态混入发布。")
            with FileLock(state / "update.lock"):
                if args.action == "stage":
                    result = stage(state, args.channel, args.current)
                elif args.action == "baseline":
                    result = register_baseline(state, args.source.expanduser())
                elif args.action == "select":
                    if not args.version:
                        parser.error("select requires --version")
                    result = select(state, args.version)
                elif args.action == "rollback":
                    result = rollback(state)
                else:
                    active = state / "active.json"
                    result = {"status": "selected" if active.exists() else "unselected",
                              "selection": json.loads(active.read_text()) if active.exists() else None,
                              "staged": sorted(p.name for p in (state / "releases").glob("*") if p.is_dir()),
                              "message": "这里只记录源码版本；不代表已运行服务的版本。"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (UpdateError, UserError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception:
        print('{"status":"error","message":"Update operation failed; installed services were not modified."}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
