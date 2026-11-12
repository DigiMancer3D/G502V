#!/usr/bin/env python3
import tkinter as tk
from tkinter import PhotoImage, Menu, Toplevel, Label, Button, StringVar, messagebox, scrolledtext, colorchooser, Frame, filedialog
from pynput import keyboard, mouse
import threading
from pathlib import Path
import time
import json
import subprocess
import math

from input_remapper_auto import InputRemapperAutoMapper
from evdev_global_input import EvdevGlobalInput
from motion_direction import MotionFrameAccumulator

def run_command(cmd, timeout=8):
    try:
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

class G502Visualizer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("G502 Visualizer — Profile-Independent Telemetry")
        self.root.geometry("1000x720")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-topmost", True)

        icon_path = Path(__file__).parent / "images" / "G502Vicon.png"
        if icon_path.exists():
            try:
                self.root.iconphoto(True, PhotoImage(file=str(icon_path)))
            except:
                pass

        self.canvas = tk.Canvas(self.root, width=950, height=480, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(pady=8)

        # 2D movement-direction settings. Legacy yaw_* setting names are preserved
        # on disk for backward compatibility with existing G502V settings.
        self.yaw_visible = True
        self.yaw_center_x = 720
        self.yaw_center_y = 310
        self.yaw_ring_radius = 55
        self.yaw_dot_size = 10
        self.yaw_ring_visible = True
        self.yaw_dot_visible = True
        self.yaw_current_angle = 0
        # G502 REL_X and REL_Y may arrive in adjacent evdev SYN reports.
        # Coalesce a short display-frame worth of motion before calculating
        # direction so a diagonal stroke is not overwritten by axis-only
        # micro-packets. 16 ms matches a ~60 Hz stream/display frame.
        self.motion_frame_ms = 16
        self.motion_frame = MotionFrameAccumulator()
        self.watermark = None

        self.os_layer_visible = True
        self.detector_label_visible = True
        self.current_bg_color = "#1e1e1e"
        self.menu_button_visible = True

        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Edit Keys & 2D Direction", command=self.edit_mapped_keys)
        self.context_menu.add_command(label="Change Background Color", command=self.change_background_color)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Toggle Detector Label", command=self.toggle_detector_label)
        self.context_menu.add_command(label="Toggle Menu Button", command=self.toggle_menu_button_visibility)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Reset Settings", command=self.reset_settings)
        self.context_menu.add_command(label="Reset Mapping", command=self.reset_mappings)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Auto Sync Input Remapper Profile", command=self.sync_input_remapper_profile)
        self.context_menu.add_command(label="Choose Input Remapper Profile…", command=self.choose_input_remapper_profile)
        self.context_menu.add_command(label="Auto Mapping Report", command=self.show_input_remapper_mapping_report)
        self.context_menu.add_command(label="Global Input Backend Report", command=self.show_input_backend_report)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Smart Mouse Config Query", command=self.query_mouse_config)
        self.context_menu.add_command(label="Clear Visual", command=self.clear_visual)
        self.context_menu.add_command(label="Sim Keys (All)", command=self.sim_all_pressed)
        self.context_menu.add_command(label="Sim-Robin Keys (3.1s/key)", command=self.sim_rotate)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Close", command=self.quit_app)

        # Traditional Tk right-click path plus a root-level binding so the
        # context menu works over the canvas and the rest of the visualizer.
        self.canvas.bind("<Button-3>", self.show_context_menu, add="+")
        self.root.bind("<Button-3>", self.show_context_menu, add="+")
        self.root.bind("<Button-1>", lambda e: self.context_menu.unpost())
        self._last_context_post = 0.0
        self._fallback_pointer_xy = None

        self.menu_btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        self.menu_btn_frame.pack(fill="x", pady=5)
        self.menu_button = tk.Button(self.menu_btn_frame, text="☰ MENU", command=self.show_menu_button,
                                     bg="#333", fg="white", font=("TkDefaultFont", 11, "bold"))
        self.menu_button.pack(side="left", padx=20)

        self.debug_var = tk.StringVar(value="Ready — Press any key, click, scroll or tilt the wheel")
        self.debug_label = tk.Label(self.root, textvariable=self.debug_var, bg="#1e1e1e", fg="#00ff00",
                                    font=("TkDefaultFont", 10))
        self.debug_label.pack(fill="x", pady=5)

        self.images_dir = Path(__file__).parent / "images"
        self.button_images = {}
        self.button_mappings = self.load_mappings()
        self.device_name = None
        self.active_profile = None
        self.detect_mode = False
        self.detect_target_var = None

        # Input Remapper profile-aware action labeling. Alpha.5 additionally reads
        # the Input-Remapper-owned raw G502 proxy devices, so physical button
        # visualization no longer depends on which game profile is active.
        # Existing auto/manual mappings remain as semantic/fallback layers.
        self.auto_mapper = InputRemapperAutoMapper(on_profile_change=self.on_auto_profile_change)
        self.auto_press_started = {}
        self.auto_release_generation = {}
        self.auto_min_flash_ms = 130
        self.physical_down = set()

        self.load_visual_mouse()
        self.smart_detect_mouse()
        self.sync_input_remapper_profile(silent=True)
        self.auto_mapper.start_journal_watch()

        # Load settings AFTER UI is ready, then create direction display.
        self.load_settings()
        self.create_yaw_display()

        # Wayland-safe global input. Prefer Input Remapper's virtual evdev devices
        # so the visualizer keeps working while another application/monitor has
        # pointer focus. pynput remains a compatibility fallback for X11 or when
        # evdev is unavailable.
        self.input_backend = "starting"
        self.evdev_input = EvdevGlobalInput(
            on_input=self.queue_input,
            on_scroll=self.queue_scroll,
            on_move=self.queue_relative_mouse_move,
            on_status=self.on_input_backend_status,
            on_button_visual=self.queue_physical_visual,
            on_physical_scroll=self.queue_physical_scroll,
        )
        self.evdev_input.set_native_click_passthrough(
            self.auto_mapper.unmapped_native_click_codes()
        )
        if self.evdev_input.start():
            self.input_backend = "evdev-global"
            if self.evdev_input.physical_mouse_ready and self.evdev_input.physical_keyboard_ready:
                self.debug_var.set(
                    "GLOBAL INPUT: profile-independent G502 physical telemetry ready"
                )
            else:
                self.debug_var.set(
                    "GLOBAL INPUT: evdev active — profile-independent G502 source incomplete; see Backend Report"
                )
        else:
            self.input_backend = "pynput-fallback"
            self.start_listeners()
            self.global_mouse_listener = mouse.Listener(on_move=self.global_mouse_move)
            self.global_mouse_listener.daemon = True
            self.global_mouse_listener.start()
            self.debug_var.set(
                "INPUT FALLBACK: pynput — global evdev unavailable; see Backend Report"
            )

        # Render evdev motion at display cadence instead of posting one Tk task
        # per mouse report. This both preserves diagonal REL_X/REL_Y pairs and
        # prevents high-polling-rate mice from flooding Tk's event queue.
        if self.input_backend == "evdev-global":
            self.root.after(self.motion_frame_ms, self._flush_relative_mouse_frame)

        self.root.mainloop()

    # ==================== SETTINGS + WATERMARK ====================
    def get_default_settings(self):
        return {
            "background_color": ["#1e1e1e"],
            "detector_label_visible": [True],
            "menu_button_visible": [True],
            "yaw_center_x": [720],
            "yaw_center_y": [310],
            "yaw_ring_radius": [80],
            "yaw_dot_size": [10],
            "yaw_visible": [True],
            "yaw_ring_visible": [True],
            "yaw_dot_visible": [True]
        }

    def load_settings(self):
        path = Path(__file__).parent / "settings.crumbs"
        defaults = self.get_default_settings()

        if not path.exists():
            self.settings = defaults
            self.save_settings()
        else:
            try:
                with open(path, "r") as f:
                    loaded = json.load(f)
                self.settings = defaults.copy()
                for key, value in loaded.items():
                    if key in self.settings:
                        self.settings[key] = value
                self.save_settings()
            except:
                self.settings = defaults
                self.save_settings()

        # Apply settings
        self.current_bg_color = self.settings["background_color"][0]
        self.detector_label_visible = self.settings["detector_label_visible"][0]
        self.menu_button_visible = self.settings["menu_button_visible"][0]
        self.yaw_center_x = self.settings["yaw_center_x"][0]
        self.yaw_center_y = self.settings["yaw_center_y"][0]
        self.yaw_ring_radius = self.settings["yaw_ring_radius"][0]
        self.yaw_dot_size = self.settings["yaw_dot_size"][0]
        self.yaw_visible = self.settings["yaw_visible"][0]
        self.yaw_ring_visible = self.settings["yaw_ring_visible"][0]
        self.yaw_dot_visible = self.settings["yaw_dot_visible"][0]

        # Apply colors and visibility
        self.apply_color_to_entire_ui()
        if not self.detector_label_visible:
            self.debug_label.pack_forget()
        if not self.menu_button_visible:
            self.menu_button.pack_forget()

    def save_settings(self):
        path = Path(__file__).parent / "settings.crumbs"
        with open(path, "w") as f:
            json.dump(self.settings, f, indent=2)

    def reset_settings(self):
        if messagebox.askyesno("Reset Settings", "Reset all settings to default?"):
            path = Path(__file__).parent / "settings.crumbs"
            if path.exists():
                path.unlink()
            self.load_settings()
            self.redraw_yaw_display()
            messagebox.showinfo("Reset Complete", "Settings have been reset to default.")

    def reset_mappings(self):
        if messagebox.askyesno("Reset Mapping", "Reset all key mappings to default?"):
            path = Path(__file__).parent / "mappings.json"
            if path.exists():
                path.unlink()
            self.button_mappings = self.load_mappings()
            messagebox.showinfo("Reset Complete", "Mappings have been reset to default.")

    # ==================== 2D MOVEMENT DIRECTION + WATERMARK ====================
    def _set_motion_direction(self, dx, dy):
        """Place the dot on the ring using the last non-zero 2D mouse-motion vector."""
        if not self.yaw_visible or not self.yaw_dot_visible or not (dx or dy):
            return
        try:
            # Linux mouse REL_X/REL_Y are screen-plane deltas. Positive Y moves
            # down on screen, which matches Tk canvas coordinates directly.
            angle = math.atan2(dy, dx)
            self.yaw_current_angle = angle
            dot_x = self.yaw_center_x + math.cos(angle) * self.yaw_ring_radius
            dot_y = self.yaw_center_y + math.sin(angle) * self.yaw_ring_radius
            self.canvas.coords(
                self.yaw_dot,
                dot_x - self.yaw_dot_size/2, dot_y - self.yaw_dot_size/2,
                dot_x + self.yaw_dot_size/2, dot_y + self.yaw_dot_size/2
            )
        except Exception:
            pass

    def global_mouse_move(self, x, y):
        """Compatibility fallback: derive the movement vector from pointer deltas."""
        try:
            previous = getattr(self, "_fallback_pointer_xy", None)
            self._fallback_pointer_xy = (x, y)
            if previous is not None:
                self._set_motion_direction(x - previous[0], y - previous[1])
        except Exception:
            pass

    def global_relative_mouse_move(self, dx, dy):
        """Update the 360-degree 2D movement direction from evdev REL_X/REL_Y."""
        self._set_motion_direction(dx, dy)

    def create_yaw_display(self):
        self.canvas.delete("yaw")

        icon_path = self.images_dir / "G502Vicon.png"
        if icon_path.exists():
            self.yaw_icon_img = PhotoImage(file=str(icon_path))
            self.yaw_icon = self.canvas.create_image(
                self.yaw_center_x, self.yaw_center_y,
                image=self.yaw_icon_img, anchor="center", tags="yaw"
            )
        else:
            self.yaw_icon = None

        self.yaw_ring = self.canvas.create_oval(
            self.yaw_center_x - self.yaw_ring_radius,
            self.yaw_center_y - self.yaw_ring_radius,
            self.yaw_center_x + self.yaw_ring_radius,
            self.yaw_center_y + self.yaw_ring_radius,
            outline="#00ff00", width=2, tags="yaw"
        )
        # Neutral startup state: no movement direction has been observed yet.
        self.yaw_dot = self.canvas.create_oval(
            self.yaw_center_x - self.yaw_dot_size/2,
            self.yaw_center_y - self.yaw_dot_size/2,
            self.yaw_center_x + self.yaw_dot_size/2,
            self.yaw_center_y + self.yaw_dot_size/2,
            fill="#00ff00", outline="#00ff00", tags="yaw"
        )

        # Watermark (small G502Vicon when main icon is hidden)
        watermark_path = self.images_dir / "G502Vicon.png" # expects 64x64 or 128x128 png icon image size with 128x128 currently used
        if watermark_path.exists():
            self.watermark_img = PhotoImage(file=str(watermark_path))
            self.watermark = self.canvas.create_image(870, 420, image=self.watermark_img, anchor="center", tags="yaw")

        self.update_yaw_visibility()

    def update_yaw_visibility(self):
        state = "normal" if self.yaw_visible else "hidden"
        if self.yaw_icon:
            self.canvas.itemconfig(self.yaw_icon, state=state)
        ring_state = "normal" if (self.yaw_visible and self.yaw_ring_visible) else "hidden"
        self.canvas.itemconfig(self.yaw_ring, state=ring_state)
        dot_state = "normal" if (self.yaw_visible and self.yaw_dot_visible) else "hidden"
        self.canvas.itemconfig(self.yaw_dot, state=dot_state)

        if self.watermark:
            watermark_state = "normal" if not self.yaw_visible else "hidden"
            self.canvas.itemconfig(self.watermark, state=watermark_state)

    # ==================== REST OF PROGRAM ====================
    def load_mappings(self):
        path = Path(__file__).parent / "mappings.json"
        base = {
            "L1": ["button.left"],
            "R1": ["button.right"],
            "G8": ["button.x1", "space"],
            "G7": ["button.x2", "enter"],
            "G5": ["g5", "w"],
            "G4": ["g4", "a"],
            "G9 (DPI)": ["button.middle"],
            "M3": ["m3", "s"],
            "Trigger (G-Switch)": ["shift", "gshift"],
            "ScrollUp": ["wheel.up"],
            "ScrollDown": ["wheel.down"],
            "M3 (Tilt Left)": ["button.x1", "tilt.left", "m3.tilt.left", "pushleft"],
            "M3 (Tilt Right)": ["button.x2", "tilt.right", "m3.tilt.right", "pushright"],
        }
        if path.exists():
            try:
                saved = json.loads(path.read_text())
                for k in base:
                    if k not in saved:
                        saved[k] = base[k]
                return saved
            except:
                pass
        return base

    def save_mappings(self):
        path = Path(__file__).parent / "mappings.json"
        path.write_text(json.dumps(self.button_mappings, indent=2))

    def _post_context_menu(self, x_root, y_root):
        now = time.monotonic()
        if now - getattr(self, "_last_context_post", 0.0) < 0.12:
            return
        self._last_context_post = now
        try:
            self.context_menu.post(int(x_root), int(y_root))
        except Exception:
            pass

    def show_context_menu(self, event):
        """Traditional Tk/X11/Wayland right-click path."""
        self._post_context_menu(event.x_root, event.y_root)

    def maybe_show_context_menu_from_evdev(self):
        """Accept Input Remapper's BTN_RIGHT while the pointer is over G502V.

        The evdev backend is global, so never open the menu merely because the
        player right-clicked in a game. We first verify that Tk reports the
        current pointer over this application's widget tree.
        """
        try:
            x, y = self.root.winfo_pointerxy()
            widget = self.root.winfo_containing(x, y)
            current = widget
            while current is not None:
                if current == self.root:
                    self._post_context_menu(x, y)
                    return True
                current = getattr(current, "master", None)
        except Exception:
            pass
        return False

    def show_menu_button(self):
        self.context_menu.post(self.root.winfo_x() + 30, self.root.winfo_y() + self.root.winfo_height() - 80)

    def toggle_detector_label(self):
        self.detector_label_visible = not self.detector_label_visible
        self.settings["detector_label_visible"] = [self.detector_label_visible]
        self.save_settings()
        if self.detector_label_visible:
            self.debug_label.pack(fill="x", pady=5)
        else:
            self.debug_label.pack_forget()

    def toggle_menu_button_visibility(self):
        self.menu_button_visible = not self.menu_button_visible
        self.settings["menu_button_visible"] = [self.menu_button_visible]
        self.save_settings()
        if self.menu_button_visible:
            self.menu_button.pack(side="left", padx=20)
        else:
            self.menu_button.pack_forget()

    def change_background_color(self):
        picker = Toplevel(self.root)
        picker.title("Background Color Editor")
        picker.geometry("430x380")
        picker.attributes("-topmost", True)
        picker.configure(bg="#2a2a2a")

        Label(picker, text="Background Color for Visualizer", font=("TkDefaultFont", 12, "bold"),
              bg="#2a2a2a", fg="white").pack(pady=10)

        self.color_preview = tk.Label(picker, text="Current Color", width=26, height=2,
                                      bg=self.current_bg_color, fg="white", relief="ridge")
        self.color_preview.pack(pady=8)

        Button(picker, text="Pick Custom Color", command=self.pick_custom_color, width=26,
               bg="#444", fg="white").pack(pady=5)

        preset_frame = tk.Frame(picker, bg="#2a2a2a")
        preset_frame.pack(pady=8)

        presets = [
            ("Black", "#1e1e1e"),
            ("Magenta", "#FF00FF"),
            ("Green", "#00FF00"),
            ("Blue", "#0000FF"),
        ]
        for text, color in presets:
            btn = Button(preset_frame, text=text, width=20, bg=color,
                         fg="white" if color != "#00FF00" else "black",
                         command=lambda c=color: self.apply_preset_color(c))
            btn.pack(pady=3)

        Button(picker, text="Close", command=picker.destroy, width=22, bg="#555", fg="white").pack(pady=12)

    def pick_custom_color(self):
        color = colorchooser.askcolor(title="Choose Background Color")[1]
        if color:
            self.current_bg_color = color
            self.color_preview.configure(bg=color)
            self.apply_color_to_entire_ui()
            self.settings["background_color"] = [color]
            self.save_settings()

    def apply_preset_color(self, color):
        self.current_bg_color = color
        self.color_preview.configure(bg=color)
        self.apply_color_to_entire_ui()
        self.settings["background_color"] = [color]
        self.save_settings()

    def apply_color_to_entire_ui(self):
        self.canvas.configure(bg=self.current_bg_color)
        self.root.configure(bg=self.current_bg_color)
        self.menu_btn_frame.configure(bg=self.current_bg_color)
        self.debug_label.configure(bg=self.current_bg_color)

    def load_visual_mouse(self):
        self.canvas.delete("all")
        base_path = self.images_dir / "3502base.png"
        if not base_path.exists():
            self.canvas.create_text(475, 240, text="❌ 3502base.png not found", fill="red", font=16)
            return

        self.base_photo = PhotoImage(file=str(base_path))
        self.base_x = (950 - self.base_photo.width()) // 2
        self.base_y = (480 - self.base_photo.height()) // 2
        self.canvas.create_image(self.base_x, self.base_y, anchor="nw", image=self.base_photo)

        self.button_defs = [
            ("L1", self.base_x + 295, self.base_y + 215, "l1.png", "L1clicked.png"),
            ("R1", self.base_x + 165, self.base_y + 205, "r1.png", "R1clicked.png"),
            ("G8", self.base_x + 285, self.base_y + 285, "g8.png", "G8clicked.png"),
            ("G7", self.base_x + 400, self.base_y + 210, "g7.png", "G7clicked.png"),
            ("G5", self.base_x + 480, self.base_y + 230, "g5.png", "G5clicked.png"),
            ("G4", self.base_x + 570, self.base_y + 170, "g4.png", "G4clicked.png"),
            ("G9 (DPI)", self.base_x + 340, self.base_y + 80, "g9.png", "G9clicked.png"),
            ("M3", self.base_x + 210, self.base_y + 180, "m3.png", "M3clicked.png"),
            ("ScrollUp", self.base_x + 176, self.base_y + 198, "scrollup.png", "scrollupclicked.png"),
            ("ScrollDown", self.base_x + 220, self.base_y + 140, "scrolldown.png", "scrolldownclicked.png"),
            ("M3 (Tilt Left)", self.base_x + 214, self.base_y + 182, "pushleft.png", "pushleftclicked.png"),
            ("M3 (Tilt Right)", self.base_x + 214, self.base_y + 182, "pushright.png", "pushrightclicked.png"),
            ("Trigger (G-Switch)", self.base_x + 426, self.base_y + 319, "trigger.png", "triggerclicked.png"),
            ("G-Light (Escape)", self.base_x + 541, self.base_y + 53, "gicon.png", "Giconclicked.png"),
            ("RGB Trio G8", self.base_x + 503, self.base_y + 157, "G502lightsoff.png", "G502lightsonc.png"),
            ("RGB Trio G7", self.base_x + 503, self.base_y + 157, "G502lightsoff.png", "G502lightson3b.png"),
            ("RGB Trio G5", self.base_x + 503, self.base_y + 157, "G502lightsoffc.png", "G502lightson3.png"),
            ("RGB Trio G4", self.base_x + 503, self.base_y + 157, "G502lightsoffc.png", "G502lightson2c.png"),
            ("RGB Trio PushLeft", self.base_x + 503, self.base_y + 157, "G502lightsoffc.png", "G502lightson2b.png"),
            ("RGB Trio PushRight", self.base_x + 503, self.base_y + 157, "G502lightsoffc.png", "G502lightson2.png"),
            ("RGB Trio M3/Click", self.base_x + 503, self.base_y + 157, "G502lightsoffc.png", "G502lightson.png"),
        ]

        self.button_images.clear()
        for name, x, y, normal_file, clicked_file in self.button_defs:
            normal_path = self.images_dir / normal_file
            clicked_path = self.images_dir / clicked_file
            normal_img = PhotoImage(file=str(normal_path)) if normal_path.exists() else None
            clicked_img = PhotoImage(file=str(clicked_path)) if clicked_path.exists() else normal_img
            self.button_images[name] = {
                "x": x, "y": y, "normal": normal_img, "clicked": clicked_img, "overlay_id": None
            }

    def queue_physical_visual(self, visual, pressed, origin="g502-forwarding-proxy"):
        """Marshal merged G502 control telemetry to the Tk UI thread."""
        try:
            self.root.after(0, lambda v=visual, p=pressed, o=origin:
                            self.handle_physical_visual(v, p, o))
        except Exception:
            pass

    def handle_physical_visual(self, visual, pressed, origin="g502-forwarding-proxy"):
        """Visualize native, proxied and unmapped-passthrough main controls."""
        if visual not in self.button_images:
            return
        if pressed and visual == "R1":
            # Preserve both the classic Tk RMB menu and the globally captured
            # physical G502 RMB path. The helper posts only while pointer is over
            # the G502V window, so gameplay RMB does not summon the menu remotely.
            self.maybe_show_context_menu_from_evdev()
        if pressed:
            self.physical_down.add(visual)
        else:
            self.physical_down.discard(visual)
        state = "DOWN" if pressed else "UP"
        self.debug_var.set(f"G502 [{origin}]: {visual} {state}")
        self.toggle_overlay(visual, pressed)

    def queue_physical_scroll(self, direction):
        try:
            self.root.after(0, lambda d=direction: self._handle_physical_scroll_pulse(d))
        except Exception:
            pass

    def _handle_physical_scroll_pulse(self, direction):
        visual = "ScrollUp" if direction == "up" else "ScrollDown"
        self.debug_var.set(f"PHYSICAL G502: {visual}")
        self.toggle_overlay(visual, True)
        self.root.after(130, lambda v=visual: self.toggle_overlay(v, False))

    def queue_input(self, key, pressed):
        """Marshal background virtual-output events onto Tk's UI thread."""
        try:
            self.root.after(0, lambda k=key, p=pressed: self.handle_input(k, p))
        except Exception:
            pass

    def queue_scroll(self, direction):
        try:
            self.root.after(0, lambda d=direction: self._handle_scroll_pulse(d))
        except Exception:
            pass

    def _handle_scroll_pulse(self, direction):
        key_str = f"wheel.{direction}"
        self.handle_input(key_str, True)
        self.root.after(130, lambda k=key_str: self.handle_input(k, False))

    def queue_relative_mouse_move(self, dx, dy):
        """Collect high-rate REL_X/REL_Y packets for one visual frame."""
        try:
            self.motion_frame.add(dx, dy)
        except Exception:
            pass

    def _flush_relative_mouse_frame(self):
        """Render one coalesced 2D movement vector and keep the last angle."""
        try:
            dx, dy = self.motion_frame.take()
            if dx or dy:
                self.global_relative_mouse_move(dx, dy)
        finally:
            try:
                self.root.after(self.motion_frame_ms, self._flush_relative_mouse_frame)
            except Exception:
                pass

    def on_input_backend_status(self, status):
        try:
            self.root.after(0, lambda s=status: self.debug_var.set(s))
        except Exception:
            pass

    def start_listeners(self):
        def on_press(key):
            self.queue_input(key, True)
        def on_release(key):
            self.queue_input(key, False)
        def on_mouse_click(x, y, button, pressed):
            self.queue_input(button, pressed)
        def on_scroll(x, y, dx, dy):
            if dx != 0:
                direction = "left" if dx < 0 else "right"
                key_str = f"tilt.{direction}"
                self.queue_input(key_str, True)
                self.root.after(130, lambda k=key_str: self.handle_input(k, False))
            if dy != 0:
                self.queue_scroll("up" if dy > 0 else "down")

        self.kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.kb_listener.daemon = True
        self.kb_listener.start()

        self.mouse_listener = mouse.Listener(on_click=on_mouse_click, on_scroll=on_scroll)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    def normalize_input(self, key):
        if isinstance(key, keyboard.Key):
            if key == keyboard.Key.space: return "space"
            if key == keyboard.Key.enter: return "enter"
            if key == keyboard.Key.esc: return "escape"
            if key == keyboard.Key.tab: return "tab"
            if key == keyboard.Key.backspace: return "backspace"
            if key == keyboard.Key.delete: return "delete"
            if key == keyboard.Key.shift_l: return "left-shift"
            if key == keyboard.Key.shift_r: return "right-shift"
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r: return "ctrl"
            if key == keyboard.Key.alt_l or key == keyboard.Key.alt_r: return "alt"
            if key == keyboard.Key.up: return "arrow up"
            if key == keyboard.Key.down: return "arrow down"
            if key == keyboard.Key.left: return "arrow left"
            if key == keyboard.Key.right: return "arrow right"
            if key == keyboard.Key.page_up: return "page up"
            if key == keyboard.Key.page_down: return "page down"
            if key == keyboard.Key.home: return "home"
            if key == keyboard.Key.end: return "end"
            return key.name.lower()
        elif isinstance(key, keyboard.KeyCode):
            if key.char: return key.char.lower()
            return str(key).lower().replace("keycode.", "")
        elif isinstance(key, mouse.Button):
            return f"button.{str(key).split('.')[-1].lower()}"
        elif isinstance(key, str):
            return key.lower()
        return str(key).lower()

    def handle_input(self, key, pressed):
        key_str = self.normalize_input(key)

        if getattr(self, 'detect_mode', False) and getattr(self, 'detect_target_var', None):
            target = self.detect_target_var
            self.detect_mode = False
            self.detect_target_var = None
            if target:
                try:
                    self.root.after(0, lambda t=target, ks=key_str: t.set(ks))
                except:
                    pass
            return

        self.debug_var.set(f"Last detected: '{key_str}'   (pressed: {pressed})")

        # Input Remapper can deliver BTN_RIGHT through the global evdev backend
        # without Tk producing its traditional <Button-3> event. Accept both
        # forms, but only post the menu when the pointer is actually over G502V.
        if pressed and key_str == "button.right":
            self.maybe_show_context_menu_from_evdev()

        # Virtual outputs are still resolved through the active Input Remapper
        # profile for semantic macro/action confirmation. When both raw G502 proxy
        # interfaces are available, physical visualization is authoritative and
        # this path deliberately does not re-toggle the same button overlays.
        if hasattr(self, "auto_mapper"):
            actions = self.auto_mapper.resolve(key_str)
            resolution = "active-profile"
            if not actions:
                # Profile changes can race journal/autoload state. A catalog of
                # installed Input Remapper presets provides a safe fallback when
                # every matching profile agrees on the originating G502 control.
                actions = self.auto_mapper.resolve_catalog_consensus(key_str)
                resolution = "profile-consensus"
            if actions:
                self.handle_auto_actions(key_str, pressed, actions, resolution=resolution)
                return

        # Fallback/manual mapping path used by the original G502V implementation.
        for btn_name, combos in self.button_mappings.items():
            if btn_name not in self.button_images:
                continue
            for combo in combos:
                combo_clean = combo.lower().strip()
                if key_str == combo_clean or key_str == combo_clean.replace("button.", ""):
                    self.debug_var.set(f"LIGHTING: {btn_name}  (key: {key_str})")
                    self.toggle_overlay(btn_name, pressed)
                    return

    def on_auto_profile_change(self, profile_name):
        """Refresh unremapped click policy when the active preset switches."""
        try:
            if hasattr(self, "evdev_input"):
                self.evdev_input.set_native_click_passthrough(
                    self.auto_mapper.unmapped_native_click_codes()
                )
            self.root.after(0, lambda n=profile_name: self.debug_var.set(
                f"AUTO PROFILE: {n} — G502 actions and direct-click bridge ready"
            ))
        except Exception:
            pass

    def sync_input_remapper_profile(self, silent=False):
        ok = self.auto_mapper.sync_active_profile()
        if ok:
            msg = (f"AUTO PROFILE: {self.auto_mapper.profile_name} — "
                   f"{len(self.auto_mapper.actions)} macro/direct action leaves")
            self.debug_var.set(msg)
            if not silent:
                messagebox.showinfo("Input Remapper Auto Map", msg)
        else:
            msg = ("Could not detect/load the active Input Remapper profile.\n\n"
                   f"{self.auto_mapper.last_error}\n\n"
                   "Manual G502V mappings remain active. You can also choose a profile JSON manually.")
            self.debug_var.set("AUTO PROFILE: not detected — manual mappings active")
            if not silent:
                messagebox.showwarning("Input Remapper Auto Map", msg)
        return ok

    def choose_input_remapper_profile(self):
        initial = self.auto_mapper.preset_dir
        path = filedialog.askopenfilename(
            title="Choose Input Remapper profile JSON",
            initialdir=str(initial if initial.exists() else Path.home()),
            filetypes=[("Input Remapper JSON", "*.json"), ("All files", "*")],
        )
        if not path:
            return
        if self.auto_mapper.load_profile_path(path):
            self.debug_var.set(
                f"AUTO PROFILE: {self.auto_mapper.profile_name} — manually selected"
            )
        else:
            messagebox.showerror(
                "Profile load failed",
                f"Could not load that profile.\n\n{self.auto_mapper.last_error}",
            )

    def show_input_remapper_mapping_report(self):
        win = Toplevel(self.root)
        win.title("G502V — Input Remapper Auto Mapping Report")
        win.geometry("980x720")
        win.attributes("-topmost", True)
        text = scrolledtext.ScrolledText(win, wrap="word", font=("TkDefaultFont", 10))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", self.auto_mapper.report_text())
        text.configure(state="disabled")

    def show_input_backend_report(self):
        # Always show the evdev discovery report, even while the GUI is using
        # pynput fallback. Alpha.3 can therefore distinguish missing devices
        # from Linux permission/ACL problems.
        text = self.evdev_input.report_text()
        text += (
            "\n\nVisualizer authority:\n"
            "  Raw G502 proxy/hardware buttons are preferred and merged across sources.\n"
            "  Unmapped L1/R1 can be bridged from the generic Input Remapper mouse.\n"
            "  Active profiles determine which main clicks are truly unremapped.\n"
            "  Other macro/action mappings remain active.\n"
            "  Result: Desktop, Cyberpunk, Megabonk can share one G502V setup."
        )
        if getattr(self, "input_backend", "") != "evdev-global":
            text += (
                "\n\nCurrent GUI backend: pynput compatibility fallback\n"
                "Wayland cross-window/cross-monitor capture: NOT GUARANTEED\n"
                "Use setup_global_input_access.sh when the report lists virtual\n"
                "Input Remapper devices as NOT READABLE."
            )
        win = Toplevel(self.root)
        win.title("G502V — Global Input Backend Report")
        win.geometry("900x560")
        win.attributes("-topmost", True)
        box = scrolledtext.ScrolledText(win, wrap="word", font=("TkDefaultFont", 10))
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def handle_auto_actions(self, key_str, pressed, actions, resolution="active-profile"):
        # Multiple macro leaves can intentionally share an output (for example the
        # tap and hold forms of the same physical button). Collapse those to the
        # unique physical controls so the visual remains deterministic.
        visuals = []
        for action in actions:
            for visual in action.visuals:
                if visual in self.button_images and visual not in visuals:
                    visuals.append(visual)

        if not visuals:
            return

        # Build a compact stream/debug label. If several gestures map to the same
        # output from the same control, show all recognized gesture classes.
        sources = []
        gestures = []
        for action in actions:
            if action.visual_label not in sources:
                sources.append(action.visual_label)
            g = action.gesture.upper()
            if g not in gestures:
                gestures.append(g)
        source_text = " / ".join(sources)
        gesture_text = "/".join(gestures)
        tag = "ACTION CONFIRM" if resolution == "active-profile" else "PROFILE-CONSENSUS"
        self.debug_var.set(f"{tag}: {source_text} • {gesture_text} → {key_str}")

        now = time.monotonic()
        for visual in visuals:
            # If the raw G502 proxy is currently reporting this control as
            # physically down, do not let delayed/pulsed virtual output fight the
            # physical overlay. If the proxy does not expose this mapped control,
            # the profile-driven path remains the fallback exactly as before.
            if visual in self.physical_down:
                continue
            if pressed:
                generation = self.auto_release_generation.get(visual, 0) + 1
                self.auto_release_generation[visual] = generation
                self.auto_press_started[visual] = now
                self.toggle_overlay(visual, True)
            else:
                generation = self.auto_release_generation.get(visual, 0)
                started = self.auto_press_started.get(visual, now)
                elapsed_ms = max(0.0, (now - started) * 1000.0)
                delay = max(0, int(self.auto_min_flash_ms - elapsed_ms))

                def release(v=visual, g=generation):
                    if self.auto_release_generation.get(v, 0) == g and v not in self.physical_down:
                        self.toggle_overlay(v, False)
                        self.auto_press_started.pop(v, None)

                if delay:
                    self.root.after(delay, release)
                else:
                    release()

    def toggle_overlay(self, btn_name, show):
        data = self.button_images.get(btn_name)
        if not data:
            return
        if data["overlay_id"]:
            self.canvas.delete(data["overlay_id"])
            data["overlay_id"] = None
        if show and data.get("clicked"):
            data["overlay_id"] = self.canvas.create_image(
                data["x"], data["y"], anchor="center", image=data["clicked"]
            )

    def smart_detect_mouse(self):
        # This onboard-memory query is optional and independent of the live
        # Input Remapper telemetry backend. libratbag often exposes a codename
        # rather than the model name, so the global backend probes candidate info too.
        self.ratbag_status = "not checked"
        self.debug_var.set("Detecting optional G502 onboard-memory interface...")
        out, err, code = run_command("ratbagctl list")
        if code != 0 or not out:
            self.ratbag_status = f"ratbagctl unavailable/no devices: {err or 'no output'}"
            self.debug_var.set("Ready (onboard-memory query unavailable; telemetry can still work)")
            return
        candidates = []
        for line in out.splitlines():
            if ":" not in line:
                continue
            rid = line.split(":", 1)[0].strip()
            candidates.append((rid, line))
            if "g502" in line.lower():
                self.device_name = rid
                break
        if not self.device_name:
            for rid, line in candidates:
                info, _, rc = run_command(f'ratbagctl "{rid}" info')
                if rc == 0 and "g502" in info.lower():
                    self.device_name = rid
                    break
        if self.device_name:
            out2, _, _ = run_command(f'ratbagctl "{self.device_name}" profile active get')
            self.active_profile = out2.strip() if out2 else "0"
            self.ratbag_status = f"detected: {self.device_name}"
            self.debug_var.set(f"✅ {self.device_name} | Active Profile: {self.active_profile}")
        else:
            self.ratbag_status = "ratbagctl saw devices, but none identified as a G502"
            self.debug_var.set("Ready (G502 onboard-memory query unavailable; telemetry can still work)")

    def query_mouse_config(self):
        if not self.device_name:
            messagebox.showwarning(
                "Onboard query unavailable",
                "libratbag did not identify a G502 for the optional onboard-memory query.\n\n"
                f"{getattr(self, 'ratbag_status', 'No ratbag status')}\n\n"
                "This does NOT prevent Input Remapper auto-mapping or global evdev telemetry."
            )
            return
        win = Toplevel(self.root)
        win.title("G502 Onboard Memory - Full Smart Query")
        win.geometry("1000x720")
        win.attributes("-topmost", True)
        text = scrolledtext.ScrolledText(win, height=38, width=115, font=("TkDefaultFont", 10))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("end", f"Device: {self.device_name}\nActive Profile: {self.active_profile}\n\n")
        text.insert("end", "=== Standard Buttons ===\n")
        for name, num in [("L1", 13), ("R1", 14), ("G8", 8), ("G7", 7), ("G5", 5),
                          ("G4", 4), ("G9 (DPI)", 9), ("M3", 2), ("Trigger (G-Switch)", 6)]:
            cmd = f'ratbagctl "{self.device_name}" profile 0 button {num} action get'
            out, _, code = run_command(cmd)
            status = out if code == 0 else "N/A or special"
            text.insert("end", f"{name:22} → {status}\n")
        text.insert("end", "\n=== M3 Tilt Buttons (11 & 12) ===\n")
        for name, num in [("M3 (Tilt Left) - Button 11", 11), ("M3 (Tilt Right) - Button 12", 12)]:
            cmd = f'ratbagctl "{self.device_name}" profile 0 button {num} action get'
            out, _, code = run_command(cmd)
            status = out if code == 0 else "N/A or special"
            text.insert("end", f"{name:35} → {status}\n")
        text.insert("end", "\n=== Device Info ===\n")
        out, _, _ = run_command(f'ratbagctl "{self.device_name}" info')
        text.insert("end", out + "\n")
        text.config(state="disabled")

    def edit_mapped_keys(self):
        editor = Toplevel(self.root)
        editor.title("Key Map Editor")
        editor.geometry("1050x920")
        editor.attributes("-topmost", True)

        Label(editor, text="    Button   →    Combo 1    |    Combo 2   ",
              font=("TkDefaultFont", 11, "bold")).pack(pady=8)

        frame = tk.Frame(editor)
        frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.edit_vars1 = {}
        self.edit_vars2 = {}

        for btn_name, combos in self.button_mappings.items():
            row = tk.Frame(frame)
            row.pack(fill="x", pady=4)
            Label(row, text=btn_name, width=24, anchor="w").pack(side="left")
            var1 = StringVar(value=combos[0] if len(combos) > 0 else "")
            var2 = StringVar(value=combos[1] if len(combos) > 1 else "")
            self.edit_vars1[btn_name] = var1
            self.edit_vars2[btn_name] = var2
            tk.Entry(row, textvariable=var1, width=20).pack(side="left", padx=2)
            tk.Entry(row, textvariable=var2, width=20).pack(side="left", padx=2)
            Button(row, text="Default", width=6, command=lambda b=btn_name: self.set_default(b)).pack(side="left", padx=1)
            Button(row, text="Detect", width=6, command=lambda v1=var1, v2=var2, e=editor: self.start_detect_for_row(v1, v2, e)).pack(side="left", padx=1)
            Button(row, text="Show", width=5, command=lambda b=btn_name: self.show_button_temporary(b)).pack(side="left", padx=1)

        # DIRECTION INDICATOR SECTION
        yew_frame = tk.LabelFrame(editor, text="2D Movement Direction & Icon", font=("TkDefaultFont", 11, "bold"))
        yew_frame.pack(fill="x", padx=15, pady=10)

        row1 = tk.Frame(yew_frame)
        row1.pack(fill="x", pady=4)
        Label(row1, text="Icon:", width=8).pack(side="left")
        Button(row1, text="Hide/Show", width=10, command=lambda: self.toggle_yaw_element("icon")).pack(side="left", padx=8)

        row2 = tk.Frame(yew_frame)
        row2.pack(fill="x", pady=4)
        Label(row2, text="Loc:", width=8).pack(side="left")
        self.yaw_loc_var = StringVar(value=f"{self.yaw_center_x},{self.yaw_center_y}")
        tk.Entry(row2, textvariable=self.yaw_loc_var, width=14).pack(side="left", padx=3)
        Button(row2, text="◀", width=3, command=lambda: self.adjust_yaw("loc", -10, 0)).pack(side="left")
        Button(row2, text="▲", width=3, command=lambda: self.adjust_yaw("loc", 0, -10)).pack(side="left")
        Button(row2, text="▼", width=3, command=lambda: self.adjust_yaw("loc", 0, 10)).pack(side="left")
        Button(row2, text="▶", width=3, command=lambda: self.adjust_yaw("loc", 10, 0)).pack(side="left")

        row3 = tk.Frame(yew_frame)
        row3.pack(fill="x", pady=4)
        Label(row3, text="Ring:", width=8).pack(side="left")
        self.yaw_ring_var = StringVar(value=str(self.yaw_ring_radius))
        tk.Entry(row3, textvariable=self.yaw_ring_var, width=8).pack(side="left", padx=3)
        Button(row3, text="▲", width=3, command=lambda: self.adjust_yaw("ring", 5)).pack(side="left")
        Button(row3, text="▼", width=3, command=lambda: self.adjust_yaw("ring", -5)).pack(side="left")
        Button(row3, text="Hide/Show", width=9, command=lambda: self.toggle_yaw_element("ring")).pack(side="left", padx=8)

        row4 = tk.Frame(yew_frame)
        row4.pack(fill="x", pady=4)
        Label(row4, text="Dot:", width=8).pack(side="left")
        self.yaw_dot_var = StringVar(value=str(self.yaw_dot_size))
        tk.Entry(row4, textvariable=self.yaw_dot_var, width=8).pack(side="left", padx=3)
        Button(row4, text="▲", width=3, command=lambda: self.adjust_yaw("dot", 2)).pack(side="left")
        Button(row4, text="▼", width=3, command=lambda: self.adjust_yaw("dot", -2)).pack(side="left")
        Button(row4, text="Hide/Show", width=9, command=lambda: self.toggle_yaw_element("dot")).pack(side="left", padx=8)

        btn_frame = tk.Frame(editor)
        btn_frame.pack(pady=12)
        Button(btn_frame, text="Save & Close", command=lambda: self.save_mappings(editor)).pack(side="left", padx=15)
        Button(btn_frame, text="Cancel", command=editor.destroy).pack(side="left", padx=15)

    def adjust_yaw(self, element, delta_x=0, delta_y=0):
        if element == "loc":
            self.yaw_center_x += delta_x
            self.yaw_center_y += delta_y
            self.yaw_loc_var.set(f"{self.yaw_center_x},{self.yaw_center_y}")
            self.settings["yaw_center_x"] = [self.yaw_center_x]
            self.settings["yaw_center_y"] = [self.yaw_center_y]
        elif element == "ring":
            self.yaw_ring_radius = max(20, min(150, self.yaw_ring_radius + delta_x))
            self.yaw_ring_var.set(str(self.yaw_ring_radius))
            self.settings["yaw_ring_radius"] = [self.yaw_ring_radius]
        elif element == "dot":
            self.yaw_dot_size = max(4, min(30, self.yaw_dot_size + delta_x))
            self.yaw_dot_var.set(str(self.yaw_dot_size))
            self.settings["yaw_dot_size"] = [self.yaw_dot_size]

        self.save_settings()
        self.redraw_yaw_display()

    def toggle_yaw_element(self, element):
        if element == "icon":
            self.yaw_visible = not self.yaw_visible
            self.settings["yaw_visible"] = [self.yaw_visible]
        elif element == "ring":
            self.yaw_ring_visible = not self.yaw_ring_visible
            self.settings["yaw_ring_visible"] = [self.yaw_ring_visible]
        elif element == "dot":
            self.yaw_dot_visible = not self.yaw_dot_visible
            self.settings["yaw_dot_visible"] = [self.yaw_dot_visible]

        self.save_settings()
        self.update_yaw_visibility()

    def redraw_yaw_display(self):
        self.create_yaw_display()

    def start_detect_for_row(self, var1, var2, editor):
        target = var1 if not var1.get().strip() else var2
        self.detect_mode = True
        self.detect_target_var = target
        original_title = editor.title()
        editor.title("PRESS ANY KEY, CLICK, SCROLL OR TILT NOW...")
        def restore():
            if self.detect_mode:
                self.detect_mode = False
                self.detect_target_var = None
                editor.title(original_title)
        editor.after(9000, restore)

    def set_default(self, btn_name):
        defaults = {
            "L1": ["button.left"], "R1": ["button.right"], "G8": ["button.x1", "space"],
            "G7": ["button.x2", "enter"], "G5": ["g5", "w"], "G4": ["g4", "a"],
            "G9 (DPI)": ["button.middle"], "M3": ["m3", "s"],
            "Trigger (G-Switch)": ["shift", "gshift"],
            "ScrollUp": ["wheel.up"], "ScrollDown": ["wheel.down"],
            "M3 (Tilt Left)": ["button.x1", "tilt.left", "m3.tilt.left", "pushleft"],
            "M3 (Tilt Right)": ["button.x2", "tilt.right", "m3.tilt.right", "pushright"],
        }
        if btn_name in defaults:
            self.edit_vars1[btn_name].set(defaults[btn_name][0])
            if len(defaults[btn_name]) > 1:
                self.edit_vars2[btn_name].set(defaults[btn_name][1])

    def show_button_temporary(self, btn_name):
        data = self.button_images.get(btn_name)
        if not data or not data.get("clicked"):
            return
        overlay = self.canvas.create_image(data["x"], data["y"], anchor="center", image=data["clicked"])
        self.root.after(1400, lambda: self.canvas.delete(overlay))

    def save_mappings(self, window):
        for btn_name in self.button_mappings.keys():
            c1 = self.edit_vars1[btn_name].get().strip()
            c2 = self.edit_vars2[btn_name].get().strip()
            self.button_mappings[btn_name] = [c for c in [c1, c2] if c]
        self.save_mappings_to_file()
        messagebox.showinfo("Saved", "Mappings saved!")
        window.destroy()

    def save_mappings_to_file(self):
        path = Path(__file__).parent / "mappings.json"
        path.write_text(json.dumps(self.button_mappings, indent=2))

    def clear_visual(self):
        for data in self.button_images.values():
            if data["overlay_id"]:
                self.canvas.delete(data["overlay_id"])
                data["overlay_id"] = None

    def sim_all_pressed(self):
        self.clear_visual()
        for data in self.button_images.values():
            if data.get("clicked"):
                data["overlay_id"] = self.canvas.create_image(data["x"], data["y"], anchor="center", image=data["clicked"])

    def sim_rotate(self):
        def chase():
            for name, data in list(self.button_images.items()):
                self.clear_visual()
                if data.get("clicked"):
                    data["overlay_id"] = self.canvas.create_image(data["x"], data["y"], anchor="center", image=data["clicked"])
                time.sleep(2.4)
            self.clear_visual()
        threading.Thread(target=chase, daemon=True).start()

    def quit_app(self):
        self.clear_visual()
        if hasattr(self, "auto_mapper"): self.auto_mapper.stop()
        if hasattr(self, "evdev_input"): self.evdev_input.stop()
        if hasattr(self, "kb_listener"): self.kb_listener.stop()
        if hasattr(self, "mouse_listener"): self.mouse_listener.stop()
        if hasattr(self, "global_mouse_listener"): self.global_mouse_listener.stop()
        self.root.quit()
        self.root.destroy()

if __name__ == "__main__":
    app = G502Visualizer()
    app.root.mainloop()
