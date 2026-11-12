#!/usr/bin/env python3
"""Input Remapper profile reader for G502V.

Reads the active Input Remapper JSON preset and builds a reverse index from
injected output keys to the physical G502 controls that produced them.

This module does not modify Input Remapper presets.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import subprocess
import threading
from typing import Callable, Iterable, Optional

DEFAULT_DEVICE = "Logitech G502 HERO Gaming Mouse"

# G502 raw EV_KEY codes used by the user's onboard layout / soloPAW profiles.
INPUT_CODE_TO_VISUAL = {
    272: "L1",
    273: "R1",
    1: "M3",
    31: "G4",
    17: "G5",
    47: "Trigger (G-Switch)",
    33: "G7",
    57: "G8",
    16: "G9 (DPI)",
    30: "M3 (Tilt Left)",
    32: "M3 (Tilt Right)",
}

OUTPUT_ALIASES = {
    "btn_left": "button.left",
    "btn_right": "button.right",
    "btn_middle": "button.middle",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "left-shift",
    "shift_r": "right-shift",
    "escape": "escape",
    "esc": "escape",
    "key_slash": "/",
    "key_leftbrace": "[",
    "key_rightbrace": "]",
    "alt_l": "alt",
    "alt_r": "alt",
    "space": "space",
    "return": "enter",
}

NO_OUTPUT = {"", "disable", "none", "noop"}


@dataclass(frozen=True)
class ProfileAction:
    output: str
    visuals: tuple[str, ...]
    gesture: str
    shifted: bool
    mode: str
    source_expression: str

    @property
    def visual_label(self) -> str:
        return " + ".join(self.visuals)

    @property
    def status_label(self) -> str:
        gesture = self.gesture.upper() if self.gesture else "ACTION"
        return f"{self.visual_label} • {gesture} → {self.output}"


def normalize_output_symbol(symbol: str) -> str:
    s = str(symbol).strip()
    if not s:
        return ""
    low = s.lower()
    if low in OUTPUT_ALIASES:
        return OUTPUT_ALIASES[low]
    # KEY_A style names used by Linux symbols.
    if low.startswith("key_"):
        from evdev_global_input import normalize_evdev_symbol
        return normalize_evdev_symbol(low.upper())
    return low


def split_top_level(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == delimiter and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def unwrap_call(expr: str, name: str) -> Optional[list[str]]:
    expr = expr.strip()
    prefix = name + "("
    if not expr.startswith(prefix) or not expr.endswith(")"):
        return None
    # Verify the opening paren closes at the end, not before a chain suffix.
    depth = 0
    for i, ch in enumerate(expr[len(name):], start=len(name)):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(expr) - 1:
                return None
    if depth != 0:
        return None
    return split_top_level(expr[len(prefix):-1])


def _leaf_action(symbol: str, visuals: tuple[str, ...], gesture: str,
                 shifted: bool, mode: str, source: str) -> list[ProfileAction]:
    out = normalize_output_symbol(symbol)
    if out in NO_OUTPUT:
        return []
    return [ProfileAction(out, visuals, gesture, shifted, mode, source)]


def parse_output_expression(expr: str, visuals: tuple[str, ...], *,
                            gesture: str = "direct", shifted: bool = False,
                            shift_visual: str = "Trigger (G-Switch)") -> list[ProfileAction]:
    """Extract observable output leaves from Input Remapper's macro DSL.

    It intentionally interprets the subset used by soloPAW/Input Remapper
    profiles: if_eq, nested if_tap, key, hold_keys and key_down/wait/key_up.
    Unknown direct symbols are still treated as direct outputs.
    """
    expr = (expr or "").strip()
    if not expr or expr.lower() in NO_OUTPUT:
        return []

    args = unwrap_call(expr, "if_eq")
    if args and len(args) == 4:
        variable, expected, yes, no = args
        is_shift = "solopaw_shift" in variable.lower() and expected.strip() == "1"
        yes_visuals = visuals
        if is_shift and shift_visual not in yes_visuals:
            yes_visuals = tuple(list(yes_visuals) + [shift_visual])
        return (
            parse_output_expression(yes, yes_visuals, gesture=gesture,
                                    shifted=(shifted or is_shift), shift_visual=shift_visual)
            + parse_output_expression(no, visuals, gesture=gesture,
                                      shifted=shifted, shift_visual=shift_visual)
        )

    args = unwrap_call(expr, "if_tap")
    if args and len(args) >= 2:
        tap_expr, hold_expr = args[0], args[1]
        nested = unwrap_call(tap_expr, "if_tap")
        actions: list[ProfileAction] = []
        if nested and len(nested) >= 2:
            # Input Remapper nested convention used by soloPAW:
            # if_tap(if_tap(DOUBLE, SINGLE), HOLD)
            actions += parse_output_expression(nested[0], visuals, gesture="double",
                                               shifted=shifted, shift_visual=shift_visual)
            actions += parse_output_expression(nested[1], visuals, gesture="tap",
                                               shifted=shifted, shift_visual=shift_visual)
        else:
            actions += parse_output_expression(tap_expr, visuals, gesture="tap",
                                               shifted=shifted, shift_visual=shift_visual)
        actions += parse_output_expression(hold_expr, visuals, gesture="hold",
                                           shifted=shifted, shift_visual=shift_visual)
        return actions

    args = unwrap_call(expr, "key")
    if args and len(args) == 1:
        return _leaf_action(args[0], visuals, gesture if gesture != "direct" else "tap",
                            shifted, "pulse", expr)

    args = unwrap_call(expr, "hold_keys")
    if args:
        return [action
                for arg in args
                for action in _leaf_action(arg, visuals,
                                           gesture if gesture != "direct" else "hold",
                                           shifted, "held", expr)]

    # Fixed-duration macros such as key_down(e).wait(1930).key_up(e)
    fixed = re.fullmatch(
        r"key_down\(([^()]+)\)\.wait\((\d+)\)\.key_up\(([^()]+)\)", expr
    )
    if fixed and fixed.group(1).strip().lower() == fixed.group(3).strip().lower():
        return _leaf_action(fixed.group(1), visuals,
                            gesture if gesture != "direct" else "macro-hold",
                            shifted, f"fixed-{fixed.group(2)}ms", expr)

    # Modifier-state-only macros, e.g. set(...).hold().set(...), are silent.
    if expr.startswith("set("):
        return []

    # Multi-key sequenced macros: key_down(meta).key_down(shift).key(sysrq)
    # ...key_up(...). Each observable key has the same originating G502 button.
    seq = re.findall(r"(?:^|\.)(key|key_down|key_up)\(([^()]+)\)", expr)
    if seq and any(op != "key_up" for op, _ in seq):
        return [action for op, symbol in seq if op != "key_up"
                for action in _leaf_action(symbol, visuals,
                                           gesture if gesture != "direct" else "macro",
                                           shifted, "sequence", expr)]

    # Plain output_symbol in direct-key profiles follows physical press/release.
    return _leaf_action(expr, visuals, gesture, shifted, "direct", expr)


def physical_visuals_for_mapping(mapping: dict) -> tuple[str, ...]:
    visuals: list[str] = []
    for event in mapping.get("input_combination", []) or []:
        if event.get("type") != 1:
            continue
        visual = INPUT_CODE_TO_VISUAL.get(event.get("code"))
        if visual and visual not in visuals:
            visuals.append(visual)
    return tuple(visuals)


def parse_profile_data(data: list[dict]) -> list[ProfileAction]:
    actions: list[ProfileAction] = []
    for mapping in data:
        visuals = physical_visuals_for_mapping(mapping)
        if not visuals:
            continue
        actions.extend(parse_output_expression(mapping.get("output_symbol", ""), visuals))
    return actions


def build_output_index(actions: Iterable[ProfileAction]) -> dict[str, list[ProfileAction]]:
    index: dict[str, list[ProfileAction]] = {}
    for action in actions:
        index.setdefault(action.output, []).append(action)
    return index


class InputRemapperAutoMapper:
    """Active-profile detector + reverse output-to-G502 map.

    Detection order:
      1. latest current-boot input-remapper journal profile line
      2. Input Remapper autoload config entry
    A single journal follower can keep the map in sync without polling.
    """

    def __init__(self, device: str = DEFAULT_DEVICE,
                 on_profile_change: Optional[Callable[[str], None]] = None):
        self.device = device
        self.root = Path.home() / ".config" / "input-remapper-2"
        self.preset_dir = self.root / "presets" / device
        self.profile_path: Optional[Path] = None
        self.profile_name: Optional[str] = None
        self.actions: list[ProfileAction] = []
        self.mapped_input_codes: set[int] = set()
        self.index: dict[str, list[ProfileAction]] = {}
        self.catalog: dict[str, dict[str, list[ProfileAction]]] = {}
        self.catalog_errors: dict[str, str] = {}
        self.on_profile_change = on_profile_change
        self._watch_proc: Optional[subprocess.Popen] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_error = ""

    @staticmethod
    def _profile_from_journal_line(line: str) -> Optional[str]:
        patterns = [
            r'Loading preset from ["\']([^"\']+\.json)["\']',
            r'Starting injection:\s*["\'][^"\']+["\']\s*,\s*["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                value = m.group(1)
                if value.endswith(".json"):
                    return Path(value).stem
                return value
        return None

    def detect_active_profile_name(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["journalctl", "-b", "-u", "input-remapper", "--no-pager", "-n", "300", "-o", "cat"],
                text=True, capture_output=True, timeout=3
            )
            if result.returncode == 0:
                found = None
                for line in result.stdout.splitlines():
                    name = self._profile_from_journal_line(line)
                    if name:
                        found = name
                if found:
                    return found
        except Exception:
            pass

        cfg = self.root / "config.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            value = (data.get("autoload") or {}).get(self.device)
            if value:
                return str(value)
        except Exception:
            pass
        return None

    def refresh_catalog(self) -> int:
        """Parse installed presets for profile-independent consensus fallback.

        This never modifies presets. A fallback mapping is used only when all
        installed profiles that emit a given output agree on the originating
        physical G502 visual controls.
        """
        catalog: dict[str, dict[str, list[ProfileAction]]] = {}
        errors: dict[str, str] = {}
        try:
            paths = sorted(self.preset_dir.glob("*.json"))
        except Exception:
            paths = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("profile root must be a list")
                actions = parse_profile_data(data)
                catalog[path.stem] = build_output_index(actions)
            except Exception as exc:
                errors[path.stem] = f"{type(exc).__name__}: {exc}"
        self.catalog = catalog
        self.catalog_errors = errors
        return len(catalog)

    def resolve_catalog_consensus(self, normalized_output: str) -> list[ProfileAction]:
        """Resolve an output across installed profiles only when they agree.

        Different gesture classifications are allowed, but every candidate must
        point to the same physical visual tuple. This keeps the fallback useful
        for common direct controls (W/S/A/D/Space/Ctrl) without guessing when an
        output is genuinely overloaded by different G502 controls.
        """
        if not self.catalog:
            self.refresh_catalog()
        key = normalize_output_symbol(normalized_output)
        candidates: list[ProfileAction] = []
        for index in self.catalog.values():
            candidates.extend(index.get(key, []))
        if not candidates:
            return []
        visual_sets = {tuple(a.visuals) for a in candidates}
        if len(visual_sets) != 1:
            return []
        unique: list[ProfileAction] = []
        seen = set()
        for action in candidates:
            signature = (action.output, action.visuals, action.gesture, action.shifted, action.mode)
            if signature not in seen:
                seen.add(signature)
                unique.append(action)
        return unique

    def load_profile_name(self, name: str) -> bool:
        if not name:
            return False
        path = self.preset_dir / f"{name}.json"
        return self.load_profile_path(path)

    def load_profile_path(self, path: Path | str) -> bool:
        p = Path(path).expanduser()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("profile root must be a list")
            actions = parse_profile_data(data)
            self.profile_path = p
            self.profile_name = p.stem
            self.actions = actions
            self.mapped_input_codes = {
                int(event["code"])
                for mapping in data
                for event in (mapping.get("input_combination") or [])
                if event.get("type") == 1 and isinstance(event.get("code"), int)
            }
            self.index = build_output_index(actions)
            self.last_error = ""
            if not self.catalog:
                self.refresh_catalog()
            if self.on_profile_change:
                self.on_profile_change(self.profile_name)
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def unmapped_native_click_codes(self) -> frozenset[int]:
        """Only bridge main clicks that the active preset leaves *unmapped*.

        Direct mouse events normally use Input Remapper's forwarded clone, not
        its generic macro-output device. The profile JSON is authoritative
        about whether it remaps the original BTN_LEFT/BTN_RIGHT inputs.
        Unknown profiles keep this bridge disabled to avoid guessing.
        """
        if self.profile_path is None:
            return frozenset()
        return frozenset({272, 273} - self.mapped_input_codes)

    def sync_active_profile(self) -> bool:
        name = self.detect_active_profile_name()
        if not name:
            self.last_error = "No active/autoload Input Remapper profile detected"
            return False
        return self.load_profile_name(name)

    def resolve(self, normalized_output: str) -> list[ProfileAction]:
        return list(self.index.get(normalize_output_symbol(normalized_output), []))

    def report_text(self) -> str:
        lines = [
            "Input Remapper Auto Mapping",
            "===========================",
            f"Device: {self.device}",
            f"Profile: {self.profile_name or 'not loaded'}",
            f"Preset file: {self.profile_path.name if self.profile_path else 'n/a'}",
            f"Recognized output signatures: {len(self.index)}",
            f"Recognized action leaves: {len(self.actions)}",
            "Native main-click passthrough: " + (
                ", ".join({272: "L1", 273: "R1"}[code]
                          for code in sorted(self.unmapped_native_click_codes())) or "none"
            ),
            f"Installed preset catalog: {len(self.catalog) if self.catalog else self.refresh_catalog()} parsed",
            "",
        ]
        for output in sorted(self.index):
            actions = self.index[output]
            labels = []
            for a in actions:
                labels.append(f"{a.visual_label} [{a.gesture}{', shifted' if a.shifted else ''}; {a.mode}]")
            lines.append(f"{output:18} -> " + " | ".join(labels))
        lines += [
            "",
            "Notes:",
            "- Auto mode first uses the active preset's injected output mapping.",
            "- If active-profile detection races a profile switch, installed-profile consensus can",
            "  recover outputs whose physical G502 origin is identical across every matching preset.",
            "- Tap/double/hold macro leaves are recognized from the profile DSL.",
            "- A silent modifier press by itself (for example soloPAW G6 set/hold/set) has no injected",
            "  key to observe, so G6 is shown when a shifted action is recognized.",
            "- If a physical keyboard emits the same key as a mapped mouse output, pynput alone cannot",
            "  identify the source device; the profile inference may visualize that matching mouse action.",
        ]
        if self.last_error:
            lines += ["", f"Last error: {self.last_error}"]
        return "\n".join(lines)

    def start_journal_watch(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._stop.clear()

        def worker():
            try:
                self._watch_proc = subprocess.Popen(
                    # -n 0 is important: follow only NEW profile changes. Without
                    # it, journalctl -f replays tail lines and can overwrite a
                    # freshly/manual-selected profile with an older entry.
                    ["journalctl", "-n", "0", "-f", "-u", "input-remapper", "--no-pager", "-o", "cat"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1
                )
                assert self._watch_proc.stdout is not None
                for line in self._watch_proc.stdout:
                    if self._stop.is_set():
                        break
                    name = self._profile_from_journal_line(line)
                    if name and name != self.profile_name:
                        self.load_profile_name(name)
            except Exception:
                return

        self._watch_thread = threading.Thread(target=worker, daemon=True,
                                              name="g502v-input-remapper-watch")
        self._watch_thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._watch_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
