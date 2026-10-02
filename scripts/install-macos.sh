#!/bin/sh
set -eu
TASK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_DIR"
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'This installer is for macOS. It made no changes.' >&2
    exit 1
fi
TASK_PYTHON=${GAME_ALERT_PYTHON:-python3.12}
"$TASK_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 (GAME_ALERT_PYTHON=/path/to/python3.12)"'
sh scripts/setup.sh
if [ ! -e .venv/bin/python ]; then "$TASK_PYTHON" -m venv .venv; fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Existing venv is not Python 3.12"'
.venv/bin/python -m pip install --no-deps --require-hashes -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/python scripts/macos_service.py install
