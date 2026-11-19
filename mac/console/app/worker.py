"""Per-project frpc supervisor. Stops its child if its console owner exits."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from core import FileLock, write_json


def alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok and code.value == 259)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument("--parent", type=int, required=True)
    p.add_argument("--binary", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--runtime", type=Path, required=True)
    args = p.parse_args()
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    with FileLock(args.runtime / "data/sub2api-worker.lock"):
        child = None
        state = {"worker_pid": os.getpid(), "running": False}
        state_path = args.runtime / "data/sub2api-worker.json"
        try:
            while not stopped and alive(args.parent):
                log = args.runtime / "logs/frpc.log"
                if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
                    log.replace(log.with_suffix(".log.1"))
                with log.open("ab", buffering=0) as out:
                    state = {"worker_pid": os.getpid(), "generation": uuid.uuid4().hex,
                             "running": False, "log_offset": out.tell()}
                    write_json(state_path, state)
                    child = subprocess.Popen([args.binary, "-c", args.config],
                                             cwd=str(Path(args.config).parent),
                                             stdin=subprocess.DEVNULL, stdout=out, stderr=out)
                    state.update(child_pid=child.pid, running=True)
                    write_json(state_path, state)
                    while not stopped and alive(args.parent) and child.poll() is None:
                        time.sleep(0.25)
                    if stopped or not alive(args.parent):
                        break
                    state["running"] = False
                    write_json(state_path, state)
                # Unexpected exit: bounded-rate restart, while still owned.
                for _ in range(20):
                    if stopped or not alive(args.parent):
                        break
                    time.sleep(0.25)
        finally:
            state["running"] = False
            try:
                write_json(state_path, state)
            finally:
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)


if __name__ == "__main__":
    main()
