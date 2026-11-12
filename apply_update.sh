#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: bash ./apply_update.sh /path/to/installed/G502V"
  echo "Example: bash ./apply_update.sh \"\$HOME/SBC_Streamer_Apps/G502V_v0.2.0-alpha.3_Portable\""
  exit 2
fi
TARGET="$(realpath -m -- "$TARGET")"
if [[ ! -d "$TARGET" || ! -f "$TARGET/g502viz.py" ]]; then
  echo "ERROR: destination is not a G502V installation: $TARGET" >&2
  exit 2
fi
if [[ "$SRC" == "$TARGET" ]]; then
  echo "Already running from the installation folder. No update copy is needed."
  echo "Run: bash ./doctor.sh && ./run.sh"
  exit 0
fi
if [[ ! -f "$SRC/VERSION" || "$(cat "$SRC/VERSION")" != '0.2.0-alpha.6' ]]; then
  echo "ERROR: update source is missing or has an unexpected version" >&2
  exit 2
fi
backup="$TARGET/update_backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup" "$TARGET/tests"
files=(g502viz.py evdev_global_input.py input_remapper_auto.py motion_direction.py
       run.sh doctor.sh setup_global_input_access.sh setup_venv.sh
       setup_dependencies.sh README.md README_UPDATE.md VERSION)
for name in "${files[@]}"; do
  [[ -f "$SRC/$name" ]] || { echo "Missing update file: $name" >&2; exit 2; }
  if [[ -f "$TARGET/$name" ]]; then
    cp -p -- "$TARGET/$name" "$backup/$name"
  fi
  cp -p -- "$SRC/$name" "$TARGET/$name"
done
for path in "$SRC"/tests/test_*.py; do
  cp -p -- "$path" "$TARGET/tests/$(basename "$path")"
done
chmod +x "$TARGET"/run.sh "$TARGET"/doctor.sh "$TARGET"/setup_venv.sh "$TARGET"/setup_global_input_access.sh
printf 'Updated G502V in: %s\nBackup: %s\n' "$TARGET" "$backup"
printf 'Preserved: settings.crumbs, mappings.json, current.emoji, images/, .venv/, Input Remapper presets\n'
printf 'Next: cd "%s" && bash ./doctor.sh && ./run.sh\n' "$TARGET"
