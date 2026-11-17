#!/bin/sh
# Read-only. Use tools/update.py explicitly to stage/select a source release.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_PATH=${REMOTE_MODEL_PYTHON:-python3}
if ! "$PYTHON_PATH" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo '需要已有 Python 3.11+；可用 REMOTE_MODEL_PYTHON 指定解释器。未安装任何软件。'
    exit 1
fi
exec "$PYTHON_PATH" "$PROJECT_DIR/tools/update.py" check --channel preview
