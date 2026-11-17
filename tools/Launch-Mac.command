#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_PATH=${REMOTE_MODEL_PYTHON:-python3}
if ! "$PYTHON_PATH" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo '需要已有 Python 3.11+；用 REMOTE_MODEL_PYTHON 指定，不会自动安装。'
    exit 1
fi
exec "$PYTHON_PATH" -B "$PROJECT_DIR/tools/launch.py" "$@"
