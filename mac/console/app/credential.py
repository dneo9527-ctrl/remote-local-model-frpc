"""Codex auth-command contract. Token output is consumed only by Codex."""
import argparse
from pathlib import Path
import sys

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--key-file", required=True, type=Path)
    args = p.parse_args()
    try:
        value = args.key_file.read_text(encoding="utf-8-sig").strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError()
        sys.stdout.write(value)
    except Exception:
        print("Local gateway credential is unavailable.", file=sys.stderr)
        raise SystemExit(1)
