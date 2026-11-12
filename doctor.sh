#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="${PYTHON:-python3}"; fi
pass=0; fail=0
check(){ local name="$1"; shift; if "$@"; then echo "PASS: $name"; pass=$((pass+1)); else echo "FAIL: $name"; fail=$((fail+1)); fi; }

echo "G502V v0.2.0-alpha.6 doctor"
echo "Python: $PY"
echo
check "VERSION" grep -qx '0.2.0-alpha.6' VERSION
check "Python source compile" "$PY" -m py_compile g502viz.py input_remapper_auto.py evdev_global_input.py motion_direction.py
check "pynput dependency" "$PY" -c 'import pynput'
check "evdev dependency" "$PY" -c 'import evdev'
check "three-profile + native-click bridge + auto-map + evdev tests" "$PY" -m unittest discover -s tests -v
check "base mouse image present" test -f images/3502base.png
check "clicked overlay assets present" test -f images/G8clicked.png
check "settings file valid JSON or not yet created" "$PY" -c 'import json, pathlib; p=pathlib.Path("settings.crumbs"); json.loads(p.read_text()) if p.is_file() else None'
check "global-input access helper present" test -x setup_global_input_access.sh
check "portable launcher present" test -x run.sh

RULE="/etc/udev/rules.d/70-g502v-input-remapper-uaccess.rules"
echo
echo "--- Persistent access rule ---"
if [[ -r "$RULE" ]]; then
    cat "$RULE"
    if grep -q 'GROUP=' "$RULE" && grep -q 'Logitech G502' "$RULE"; then
        echo "PASS: boot-persistent narrow group rule installed"
        pass=$((pass+1))
    else
        echo "WARN: rule exists but is the older session-only form"
        echo "      Run: bash ./setup_global_input_access.sh"
    fi
else
    echo "WARN: $RULE is not installed/readable"
    echo "      Run: bash ./setup_global_input_access.sh"
fi

echo
echo "--- Global input runtime probe ---"
set +e
"$PY" - <<'PYCODE'
from evdev_global_input import EvdevGlobalInput
b=EvdevGlobalInput(
    on_input=lambda *_:None,
    on_scroll=lambda *_:None,
    on_move=lambda *_:None,
    on_physical_visual=lambda *_:None,
    on_physical_scroll=lambda *_:None,
)
b._load_evdev(); b.discover()
print(b.report_text())
virtual_ready=any(d.role == "virtual-output" for d in b.device_info)
mouse_ready=any(d.role in ("g502-physical-mouse", "g502-hardware-mouse") for d in b.device_info)
keyboard_ready=any(d.role in ("g502-physical-keyboard", "g502-hardware-keyboard") for d in b.device_info)
b.stop()
if not mouse_ready: raise SystemExit(2)
if not keyboard_ready: raise SystemExit(3)
if not virtual_ready: raise SystemExit(4)
raise SystemExit(0)
PYCODE
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  echo "PASS: profile-independent G502 telemetry is runtime-ready"
  pass=$((pass+1))
elif [[ $rc -eq 2 ]]; then
  echo "WARN: G502 mouse-side proxy/hardware not readable/open"
  echo "      Run: bash ./setup_global_input_access.sh"
elif [[ $rc -eq 3 ]]; then
  echo "WARN: G502 keyboard-side proxy/hardware not readable/open"
  echo "      Run: bash ./setup_global_input_access.sh"
elif [[ $rc -eq 4 ]]; then
  echo "WARN: raw G502 telemetry ready, virtual macro/unmapped-click output unavailable"
else
  echo "FAIL: global backend runtime probe crashed"
  fail=$((fail+1))
fi

echo
echo "PASS checks: $pass"
echo "FAIL checks: $fail"
if (( fail )); then echo "OVERALL: FAIL"; exit 1; fi
echo "OVERALL: PASS"
