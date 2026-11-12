#!/usr/bin/env python3
"""Wayland-safe global input backend for G502V.

v0.2.0-alpha.5 adds a profile-independent physical G502 telemetry path.
It reads Input-Remapper-owned G502 forwarding proxies and, where readable,
the exact Logitech G502 HERO hardware event interfaces (046d:c08b). Input
Remapper virtual-output devices remain available for mapped macro confirmation.
Unmapped BTN_LEFT and BTN_RIGHT can also be bridged from the generic virtual
mouse when the selected Input Remapper preset leaves them untouched.

The backend never EVIOCGRABs a device and never reads ordinary physical
keyboards or unrelated mice. A grabbed physical G502 may not deliver events;
its Input Remapper forwarding proxy is preferred where available.
"""
from __future__ import annotations

from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import re
import select
import stat
import threading
from typing import Callable, Optional

PUNCT = {
    "KEY_LEFTBRACE": "[", "KEY_RIGHTBRACE": "]", "KEY_SLASH": "/",
    "KEY_BACKSLASH": "\\", "KEY_SEMICOLON": ";", "KEY_APOSTROPHE": "'",
    "KEY_COMMA": ",", "KEY_DOT": ".", "KEY_MINUS": "-", "KEY_EQUAL": "=",
    "KEY_GRAVE": "`",
}
SPECIAL = {
    "KEY_ESC": "escape", "KEY_SPACE": "space", "KEY_ENTER": "enter",
    "KEY_KPENTER": "enter", "KEY_TAB": "tab", "KEY_BACKSPACE": "backspace",
    "KEY_DELETE": "delete", "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "left-shift", "KEY_RIGHTSHIFT": "right-shift",
    "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt", "KEY_UP": "arrow up",
    "KEY_DOWN": "arrow down", "KEY_LEFT": "arrow left", "KEY_RIGHT": "arrow right",
    "KEY_PAGEUP": "page up", "KEY_PAGEDOWN": "page down", "KEY_HOME": "home",
    "KEY_END": "end", "BTN_LEFT": "button.left", "BTN_RIGHT": "button.right",
    "BTN_MIDDLE": "button.middle", "BTN_SIDE": "button.x1", "BTN_EXTRA": "button.x2",
}

# Raw EV_KEY codes produced by the accepted G502 onboard layout before
# Input Remapper translates them. These codes identify the physical control and
# therefore remain useful across Cyberpunk, Megabonk, and future presets.
G502_RAW_CODE_TO_VISUAL = {
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


def normalize_evdev_symbol(symbol) -> str:
    if isinstance(symbol, (list, tuple)):
        symbol = symbol[0] if symbol else ""
    s = str(symbol or "").upper()
    if s in SPECIAL:
        return SPECIAL[s]
    if s in PUNCT:
        return PUNCT[s]
    if s.startswith("KEY_"):
        tail = s[4:]
        if len(tail) == 1:
            return tail.lower()
        if tail.startswith("F") and tail[1:].isdigit():
            return tail.lower()
        if tail.isdigit():
            return tail
        return tail.lower().replace("_", " ")
    if s.startswith("BTN_"):
        return "button." + s[4:].lower()
    return s.lower()


@dataclass(frozen=True)
class ProcInputDevice:
    name: str
    phys: str
    handlers: tuple[str, ...]
    vendor: int = 0
    product: int = 0

    @property
    def event_paths(self) -> tuple[str, ...]:
        return tuple(f"/dev/input/{h}" for h in self.handlers if h.startswith("event"))


@dataclass(frozen=True)
class CandidateInfo:
    path: str
    name: str
    phys: str
    readable: bool
    mode: str
    owner: str
    group: str
    error: str = ""


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    name: str
    has_keys: bool
    has_rel: bool
    role: str = "virtual-output"


def parse_proc_bus_input_devices(text: str) -> list[ProcInputDevice]:
    """Parse /proc/bus/input/devices without requiring event-node access."""
    result: list[ProcInputDevice] = []
    for block in (text or "").split("\n\n"):
        if not block.strip():
            continue
        name = ""
        phys = ""
        handlers: tuple[str, ...] = ()
        vendor = product = 0
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith("I: "):
                v = re.search(r"\bVendor=([0-9a-fA-F]+)", line)
                q = re.search(r"\bProduct=([0-9a-fA-F]+)", line)
                if v and q:
                    vendor, product = int(v.group(1), 16), int(q.group(1), 16)
            elif line.startswith('N: Name='):
                value = line.split("=", 1)[1].strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                name = value
            elif line.startswith("P: Phys="):
                phys = line.split("=", 1)[1].strip()
            elif line.startswith("H: Handlers="):
                handlers = tuple(line.split("=", 1)[1].split())
        result.append(ProcInputDevice(name=name, phys=phys, handlers=handlers,
                                      vendor=vendor, product=product))
    return result


class EvdevGlobalInput:
    NAME_MARKERS = ("input-remapper", "input remapper", "inputremapper")

    def __init__(self, *, on_input: Callable[[str, bool], None],
                 on_scroll: Callable[[str], None], on_move: Callable[[int, int], None],
                 on_status: Optional[Callable[[str], None]] = None,
                 on_physical_visual: Optional[Callable[[str, bool], None]] = None,
                 on_physical_scroll: Optional[Callable[[str], None]] = None,
                 on_button_visual: Optional[Callable[[str, bool, str], None]] = None):
        self.on_input = on_input
        self.on_scroll = on_scroll
        self.on_move = on_move
        self.on_status = on_status
        self.on_physical_visual = on_physical_visual
        self.on_physical_scroll = on_physical_scroll
        self.on_button_visual = on_button_visual
        self.last_error = ""
        self.devices = []
        self.device_info: list[DeviceInfo] = []
        self.candidates: list[CandidateInfo] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending_move: dict[int, list[int]] = {}
        self._motion_only_fds: set[int] = set()
        self._dedicated_motion_fds: set[int] = set()
        self._physical_g502_fds: set[int] = set()
        self._physical_mouse_fds: set[int] = set()
        self._physical_keyboard_fds: set[int] = set()
        self._hardware_g502_fds: set[int] = set()
        self._virtual_mouse_fds: set[int] = set()
        self._held_sources: dict[str, set[int]] = {}
        self._native_click_codes: frozenset[int] = frozenset()
        self._button_lock = threading.RLock()
        self._click_counts: dict[str, dict[str, int]] = {}
        self._evdev = None
        self._ecodes = None
        self._last_status = None

    def _status(self, text: str):
        if text == self._last_status:
            return
        self._last_status = text
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _load_evdev(self) -> bool:
        try:
            import evdev
            from evdev import ecodes
            self._evdev = evdev
            self._ecodes = ecodes
            return True
        except Exception as exc:
            self.last_error = f"evdev unavailable: {type(exc).__name__}: {exc}"
            return False

    @classmethod
    def is_input_remapper_name(cls, name: str) -> bool:
        low = (name or "").lower()
        return any(marker in low for marker in cls.NAME_MARKERS)

    @classmethod
    def is_input_remapper_identity(cls, name: str, phys: str) -> bool:
        return cls.is_input_remapper_name(name) or cls.is_input_remapper_name(phys)

    @classmethod
    def is_virtual_output_identity(cls, name: str, phys: str) -> bool:
        low_phys = (phys or "").lower().rstrip("/")
        return cls.is_input_remapper_name(name) or low_phys == "input-remapper"

    @staticmethod
    def is_g502_proxy_identity(name: str, phys: str) -> bool:
        low_name = (name or "").lower()
        low_phys = (phys or "").lower()
        return low_phys.startswith("input-remapper/") and "g502" in low_name

    @classmethod
    def is_g502_motion_identity(cls, name: str, phys: str) -> bool:
        low_name = (name or "").lower()
        return (
            cls.is_g502_proxy_identity(name, phys)
            and "gaming mouse" in low_name
            and "keyboard" not in low_name
        )

    @classmethod
    def is_g502_keyboard_proxy_identity(cls, name: str, phys: str) -> bool:
        low_name = (name or "").lower()
        return cls.is_g502_proxy_identity(name, phys) and "keyboard" in low_name

    @classmethod
    def is_g502_hardware_identity(cls, name: str, phys: str,
                                  vendor: int, product: int) -> bool:
        """Exact HERO USB identity, excluding uinput forwarding clones."""
        return (vendor == 0x046d and product == 0xc08b
                and "g502" in (name or "").lower()
                and not cls.is_input_remapper_identity(name, phys))

    def set_native_click_passthrough(self, codes) -> None:
        """Update click bridge without leaving held lights on after a profile switch."""
        with self._button_lock:
            accepted = frozenset({272, 273} & set(codes))
            changed = self._native_click_codes - accepted
            self._native_click_codes = accepted
            for code in changed:
                visual = "L1" if code == 272 else "R1"
                for fd in list(self._held_sources.get(visual, ())):
                    if fd in self._virtual_mouse_fds:
                        self._emit_button(visual, False, fd, "profile-switch")

    def _emit_button(self, visual: str, pressed: bool, fd: int, origin: str):
        """Merge independent sources: one source releasing cannot unlight another."""
        with self._button_lock:
            held = self._held_sources.setdefault(visual, set())
            was_down = bool(held)
            if pressed:
                # Repeated DOWNs from the same event node don't create a counter.
                if fd not in held:
                    counts = self._click_counts.setdefault(origin, {})
                    counts[visual] = counts.get(visual, 0) + 1
                held.add(fd)
            else:
                held.discard(fd)
            is_down = bool(held)
            if not held:
                self._held_sources.pop(visual, None)
            if was_down != is_down:
                if self.on_button_visual:
                    self.on_button_visual(visual, is_down, origin)
                elif self.on_physical_visual:
                    self.on_physical_visual(visual, is_down)

    def _proc_candidates(self) -> dict[str, tuple[str, str]]:
        found: dict[str, tuple[str, str]] = {}
        try:
            text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
            for item in parse_proc_bus_input_devices(text):
                if not (self.is_input_remapper_identity(item.name, item.phys) or
                        self.is_g502_hardware_identity(item.name, item.phys,
                                                       item.vendor, item.product)):
                    continue
                for path in item.event_paths:
                    found[path] = (item.name, item.phys)
        except Exception:
            pass
        return found

    def _sysfs_candidates(self) -> dict[str, tuple[str, str]]:
        found: dict[str, tuple[str, str]] = {}
        base = Path("/sys/class/input")
        try:
            entries = list(base.glob("event*"))
        except Exception:
            return found
        for event in entries:
            try:
                name = (event / "device" / "name").read_text(errors="replace").strip()
            except Exception:
                name = ""
            try:
                phys = (event / "device" / "phys").read_text(errors="replace").strip()
            except Exception:
                phys = ""
            try:
                vendor = int((event / "device" / "id" / "vendor").read_text().strip(), 16)
                product = int((event / "device" / "id" / "product").read_text().strip(), 16)
            except Exception:
                vendor = product = 0
            if (self.is_input_remapper_identity(name, phys) or
                    self.is_g502_hardware_identity(name, phys, vendor, product)):
                found[f"/dev/input/{event.name}"] = (name, phys)
        return found

    @staticmethod
    def _path_security(path: str) -> tuple[str, str, str, bool]:
        try:
            st = os.stat(path)
            mode = stat.filemode(st.st_mode)
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except Exception:
                owner = str(st.st_uid)
            try:
                group = grp.getgrgid(st.st_gid).gr_name
            except Exception:
                group = str(st.st_gid)
            return mode, owner, group, os.access(path, os.R_OK)
        except Exception:
            return "??????????", "?", "?", False

    def _candidate_paths(self) -> dict[str, tuple[str, str]]:
        found = self._proc_candidates()
        for path, ident in self._sysfs_candidates().items():
            found.setdefault(path, ident)
        # Final compatibility path: if the node is already readable, evdev can
        # identify it even if /proc/sysfs are unusual on a distribution.
        if self._evdev is not None:
            try:
                for path in self._evdev.list_devices():
                    if path in found:
                        continue
                    try:
                        dev = self._evdev.InputDevice(path)
                        name = dev.name or ""
                        phys = getattr(dev, "phys", "") or ""
                        vendor, product = dev.info.vendor, dev.info.product
                        dev.close()
                        if (self.is_input_remapper_identity(name, phys) or
                                self.is_g502_hardware_identity(
                                    name, phys, vendor, product)):
                            found[path] = (name, phys)
                    except Exception:
                        pass
            except Exception:
                pass
        return found

    def _close_devices(self):
        # Release stale overlays if Input Remapper rebuilds its event devices.
        with self._button_lock:
            for visual, sources in list(self._held_sources.items()):
                if sources:
                    if self.on_button_visual:
                        self.on_button_visual(visual, False, "device-disconnect")
                    elif self.on_physical_visual:
                        self.on_physical_visual(visual, False)
            self._held_sources.clear()
        for dev in self.devices:
            try:
                dev.close()
            except Exception:
                pass
        self.devices = []
        self.device_info = []
        self._pending_move.clear()
        self._motion_only_fds.clear()
        self._dedicated_motion_fds.clear()
        self._physical_g502_fds.clear()
        self._physical_mouse_fds.clear()
        self._physical_keyboard_fds.clear()
        self._hardware_g502_fds.clear()
        self._virtual_mouse_fds.clear()

    def discover(self) -> int:
        if self._evdev is None and not self._load_evdev():
            return 0
        self._close_devices()
        opened = []
        info: list[DeviceInfo] = []
        candidate_info: list[CandidateInfo] = []
        found = self._candidate_paths()

        for path in sorted(found):
            name_hint, phys_hint = found[path]
            mode, owner, group, access = self._path_security(path)
            error = ""
            try:
                dev = self._evdev.InputDevice(path)
                name = dev.name or name_hint or "Input Remapper device"
                phys = getattr(dev, "phys", "") or phys_hint
                hardware_g502 = self.is_g502_hardware_identity(
                    name, phys, dev.info.vendor, dev.info.product)
                if not (self.is_input_remapper_identity(name, phys) or hardware_g502):
                    dev.close()
                    continue
                caps = dev.capabilities()
                has_keys = self._ecodes.EV_KEY in caps
                has_rel = self._ecodes.EV_REL in caps
                virtual_output = self.is_virtual_output_identity(name, phys)
                g502_proxy = self.is_g502_proxy_identity(name, phys)
                g502_mouse = self.is_g502_motion_identity(name, phys)
                g502_keyboard = self.is_g502_keyboard_proxy_identity(name, phys)
                if not (virtual_output or g502_proxy or hardware_g502):
                    dev.close()
                    error = "not an Input Remapper output or dedicated G502 source"
                elif not (has_keys or has_rel):
                    dev.close()
                    error = "no EV_KEY/EV_REL capabilities"
                else:
                    if (g502_mouse or hardware_g502 and "keyboard" not in name.lower()) and not virtual_output:
                        role = "g502-physical-mouse" if g502_mouse else "g502-hardware-mouse"
                    elif (g502_keyboard or hardware_g502) and not virtual_output:
                        role = "g502-physical-keyboard" if g502_keyboard else "g502-hardware-keyboard"
                    else:
                        role = "virtual-output"
                    opened.append(dev)
                    info.append(DeviceInfo(path, name, has_keys, has_rel, role))
                    if role.startswith("g502-physical-") or role.startswith("g502-hardware-"):
                        self._physical_g502_fds.add(dev.fd)
                    if role in ("g502-physical-mouse", "g502-hardware-mouse"):
                        self._physical_mouse_fds.add(dev.fd)
                        if role == "g502-hardware-mouse":
                            self._hardware_g502_fds.add(dev.fd)
                        else:
                            self._dedicated_motion_fds.add(dev.fd)
                    elif role in ("g502-physical-keyboard", "g502-hardware-keyboard"):
                        self._physical_keyboard_fds.add(dev.fd)
                        if role == "g502-hardware-keyboard":
                            self._hardware_g502_fds.add(dev.fd)
                    elif role == "virtual-output" and "mouse" in name.lower():
                        self._virtual_mouse_fds.add(dev.fd)
            except PermissionError:
                error = "permission denied"
            except FileNotFoundError:
                error = "event node disappeared"
            except OSError as exc:
                error = f"OSError: {exc}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            candidate_info.append(CandidateInfo(
                path=path, name=name_hint or "Input Remapper device", phys=phys_hint,
                readable=bool(access and not error), mode=mode, owner=owner, group=group,
                error=error,
            ))

        # Prefer the forwarding proxy for motion/wheel; hardware evdev is a
        # backup only if no proxy mouse was available. Avoid doubling reports.
        if not self._dedicated_motion_fds:
            self._dedicated_motion_fds.update(
                dev.fd for dev in opened if dev.fd in self._hardware_g502_fds
                and dev.fd in self._physical_mouse_fds
            )
        self.devices = opened
        self.device_info = info
        self.candidates = candidate_info

        if opened:
            names = ", ".join(sorted({d.name for d in info}))
            self.last_error = ""
            self._status(f"GLOBAL EVDEV: {names}")
        elif found:
            denied = [c for c in candidate_info if c.error == "permission denied" or not c.readable]
            paths = ", ".join(c.path for c in denied) or ", ".join(found)
            self.last_error = (
                "G502 / Input Remapper device(s) were found, but G502V cannot read them: "
                + paths + ". Run setup_global_input_access.sh, then restart G502V."
            )
            self._status("EVDEV PERMISSION NEEDED")
        else:
            self.last_error = (
                "No G502 hardware or Input Remapper event devices found in /proc, sysfs, or readable evdev. "
                "Check USB connection and Input Remapper."
            )
            self._status("EVDEV DEVICE NOT FOUND")
        return len(opened)

    def start(self) -> bool:
        if not self._load_evdev():
            self._status(self.last_error)
            return False
        if self.discover() == 0:
            return False
        if not any(d.role == "virtual-output" or d.role.startswith("g502-physical-")
                   or d.role.startswith("g502-hardware-") for d in self.device_info):
            self.last_error = "No readable Input Remapper output or G502 proxy device is available."
            self._status("EVDEV INPUT DEVICE NOT FOUND")
            self._close_devices()
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="g502v-evdev", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._close_devices()

    @property
    def physical_buttons_ready(self) -> bool:
        return bool(self._physical_g502_fds)

    @property
    def physical_mouse_ready(self) -> bool:
        return bool(self._physical_mouse_fds)

    @property
    def physical_keyboard_ready(self) -> bool:
        return bool(self._physical_keyboard_fds)

    def report_text(self) -> str:
        virtual_ready = any(d.role == "virtual-output" for d in self.device_info)
        physical_mouse_ready = any(d.role in ("g502-physical-mouse", "g502-hardware-mouse")
                                   for d in self.device_info)
        physical_keyboard_ready = any(d.role in ("g502-physical-keyboard", "g502-hardware-keyboard")
                                      for d in self.device_info)
        physical_ready = physical_mouse_ready or physical_keyboard_ready
        active = virtual_ready or physical_ready
        lines = [
            "G502V Global Input Backend",
            "==========================",
            f"Backend ready: {'YES — evdev global' if active else 'NO — GUI is using compatibility fallback'}",
            f"Profile-independent G502 buttons: {'YES — G502 proxy/hardware telemetry' if physical_ready else 'NO — raw G502 source not readable/open'}",
            "Native unremapped click bridge: " + (
                ", ".join({272: "L1", 273: "R1"}[c] for c in sorted(self._native_click_codes))
                or "disabled / active profile maps both main clicks"),
            f"G502 keyboard-side controls: {'YES' if physical_keyboard_ready else 'NO'}",
            f"2D movement direction source: {'YES — G502 REL_X/REL_Y proxy/hardware' if physical_mouse_ready else 'NO — G502 mouse source not readable/open'}",
            f"Virtual macro/action confirmation: {'YES' if virtual_ready else 'NO — physical visualization can still operate'}",
            f"Window focus required when global backend is ready: NO",
            "Device grabbing by G502V: NO",
            "Discovery: /proc/bus/input/devices + sysfs + evdev fallback",
            "The visualizer never grabs input or changes a preset.",
            "",
        ]
        if self.candidates:
            lines.append("Input Remapper / G502 event candidates:")
            for c in self.candidates:
                status = "READABLE" if c.readable and not c.error else f"NOT READABLE ({c.error or 'access denied'})"
                lines.append(f"  - {c.path}: {c.name} — {status}")
                lines.append(f"      {c.mode} owner={c.owner} group={c.group}")
                if c.phys:
                    lines.append(f"      phys={c.phys}")
        else:
            lines.append("Input Remapper / G502 event candidates: none found")
        if self.device_info:
            lines += ["", "Opened devices:"]
            for d in self.device_info:
                kinds = []
                if d.has_keys: kinds.append("keys/buttons")
                if d.has_rel: kinds.append("relative mouse/wheel")
                kinds.append(d.role)
                lines.append(f"  - {d.path}: {d.name} ({', '.join(kinds)})")
        if self._click_counts:
            lines += ["", "Observed button DOWN events by source (since launch):"]
            for origin, counts in sorted(self._click_counts.items()):
                lines.append("  " + origin + ": " + ", ".join(
                    f"{key}={value}" for key, value in sorted(counts.items())
                ))
        if self.last_error:
            lines += ["", "Current blocker:", f"  {self.last_error}"]
        lines += [
            "",
            "Interpretation:",
            "  G502 proxy devices provide profile-independent physical button proof.",
            "  Unmapped main buttons may pass through the Input Remapper virtual mouse.",
            "  Exact G502 HERO hardware events are monitored if readable/not grabbed.",
            "  Virtual output devices provide macro/action confirmation and profile-aware labels.",
            "  The G502 mouse-side proxy also provides REL_X/REL_Y movement telemetry.",
            "  If a needed G502 proxy is NOT READABLE, run the current",
            "  ./setup_global_input_access.sh once, then restart G502V.",
        ]
        return "\n".join(lines)

    def _event_symbol(self, code: int):
        table = self._ecodes.bytype.get(self._ecodes.EV_KEY, {})
        return table.get(code, f"KEY_{code}")

    def _process(self, dev, event):
        e = self._ecodes
        fd = dev.fd
        if event.type == e.EV_KEY:
            if event.value == 2:
                return
            if fd in self._physical_g502_fds:
                visual = G502_RAW_CODE_TO_VISUAL.get(event.code)
                if visual:
                    origin = ("g502-hardware" if fd in self._hardware_g502_fds
                              else "g502-forwarding-proxy")
                    self._emit_button(visual, event.value == 1, fd, origin)
                return
            if fd in self._motion_only_fds:
                return
            # Unmapped core mouse inputs have no reverse profile action. Some
            # Input Remapper versions send these native clicks through the
            # generic virtual mouse rather than the G502 forwarding clone.
            # Never synthesize this for a profile that remaps the original
            # click: Ctrl+LMB, Shift+LMB etc. are handled by the raw source.
            if fd in self._virtual_mouse_fds and event.code in self._native_click_codes:
                self._emit_button("L1" if event.code == 272 else "R1",
                                  event.value == 1, fd, "unmapped-virtual-mouse")
                return
            key = normalize_evdev_symbol(self._event_symbol(event.code))
            if key:
                self.on_input(key, event.value == 1)
            return
        if event.type == e.EV_REL:
            # If a dedicated G502 motion proxy is open, use it as the authority
            # for REL_X/REL_Y. This avoids relying on virtual output devices that
            # may expose REL capability but do not forward ordinary cursor motion.
            use_motion = fd in self._dedicated_motion_fds or not self._dedicated_motion_fds
            if event.code == e.REL_X and use_motion:
                self._pending_move.setdefault(fd, [0, 0])[0] += int(event.value)
            elif event.code == e.REL_Y and use_motion:
                self._pending_move.setdefault(fd, [0, 0])[1] += int(event.value)
            elif (fd in self._dedicated_motion_fds or (not self._dedicated_motion_fds and fd in self._physical_mouse_fds)) and event.code == e.REL_WHEEL and event.value:
                if self.on_physical_scroll:
                    self.on_physical_scroll("up" if event.value > 0 else "down")
            elif (fd in self._dedicated_motion_fds or (not self._dedicated_motion_fds and fd in self._physical_mouse_fds)) and hasattr(e, "REL_HWHEEL") and event.code == e.REL_HWHEEL and event.value:
                visual = "M3 (Tilt Right)" if event.value > 0 else "M3 (Tilt Left)"
                self._emit_button(visual, True, fd, "g502-wheel-tilt")
                self._emit_button(visual, False, fd, "g502-wheel-tilt")
            elif fd not in self._motion_only_fds and fd not in self._physical_g502_fds and event.code == e.REL_WHEEL and event.value:
                # Use virtual wheel only when there is no dedicated physical G502
                # mouse source to avoid duplicate pulses.
                if not self._physical_mouse_fds:
                    self.on_scroll("up" if event.value > 0 else "down")
            elif fd not in self._motion_only_fds and fd not in self._physical_g502_fds and hasattr(e, "REL_HWHEEL") and event.code == e.REL_HWHEEL and event.value:
                if not self._physical_mouse_fds:
                    key = "tilt.right" if event.value > 0 else "tilt.left"
                    self.on_input(key, True); self.on_input(key, False)
            return
        if event.type == e.EV_SYN and event.code == e.SYN_REPORT:
            dx, dy = self._pending_move.pop(fd, [0, 0])
            if dx or dy:
                self.on_move(dx, dy)

    def _run(self):
        while not self._stop.is_set():
            if not self.devices:
                if self.discover() == 0:
                    self._stop.wait(1.0)
                    continue
            try:
                ready, _, _ = select.select(self.devices, [], [], 1.0)
                for dev in ready:
                    try:
                        for event in dev.read():
                            self._process(dev, event)
                    except (OSError, BlockingIOError):
                        self._close_devices(); break
            except (OSError, ValueError):
                self._close_devices(); self._stop.wait(0.25)
            except Exception as exc:
                self.last_error = f"reader error: {type(exc).__name__}: {exc}"
                self._status(f"EVDEV RECOVERING: {self.last_error}")
                self._close_devices(); self._stop.wait(0.5)
