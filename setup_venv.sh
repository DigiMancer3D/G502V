#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VENV="$ROOT/.venv"
PY=${PYTHON:-python3}

echo "G502V v0.2.0-alpha.6 local venv setup"
if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
  echo "ERROR: Python tkinter is missing. On CachyOS/Arch run:"
  echo "  sudo pacman -S --needed tk"
  exit 2
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install pynput evdev

echo "PASS: local runtime ready: $VENV"
echo "Launch with: ./run.sh"
