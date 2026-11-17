import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

from .core import FileLock, UserError, port_open
from .server import serve


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["open", "serve"], default="open", nargs="?")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.config)
        return
    cfg = json.loads(args.config.read_text())
    runtime = Path(cfg["runtime"])
    port = cfg.get("port", 18765)
    url = f"http://127.0.0.1:{port}"
    with FileLock(runtime / "data/launcher.lock"):
        if port_open(port):
            # Verify identity; never open an unrelated service just by port.
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with op.open(url + "/api/session", timeout=2) as r:
                    value = json.load(r)
                if value.get("service") != "local-ai-console" or not isinstance(value.get("csrf"), str):
                    raise ValueError()
            except Exception:
                raise UserError("控制台端口被其他服务占用；未接管。") from None
        else:
            log = runtime / "logs/startup.log"
            log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with log.open("ab", buffering=0) as out:
                subprocess.Popen([sys.executable, "-m", "app", "serve", "--config", str(args.config.resolve())],
                                 cwd=str(Path(__file__).resolve().parent.parent),
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True)
            for _ in range(50):
                if port_open(port):
                    break
                time.sleep(0.1)
            else:
                raise UserError("控制台未能启动，请查看本机 startup.log。")
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "/api/session", timeout=2) as r:
                    if json.load(r).get("service") != "local-ai-console":
                        raise ValueError()
            except Exception:
                raise UserError("启动后的服务身份不匹配，未打开页面。") from None
    webbrowser.open(url)


if __name__ == "__main__":
    try:
        main()
    except UserError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
