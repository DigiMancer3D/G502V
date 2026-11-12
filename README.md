# 🐾 G502V,  Logitech G502 HERO Visualizer

**Show every click. Show every combo. Let your audience see how you play.**

![G502V logo](images/G502Vicon.png)

**G502V** is a lightweight, real-time mouse-input visualizer designed for **OBS Studio**, live gameplay, tutorials, accessibility demonstrations, and one-handed gaming. It displays a Logitech G502 HERO with glowing button overlays that react to clicks, held inputs, wheel movements, tilt actions, and layered G-Shift controls, even while another application has focus.

Built by [DigiMancer3D](https://github.com/DigiMancer3D) as part of the [soloPAW](https://github.com/DigiMancer3D/soloPAW) and [DigiMancer Streamer Tools](https://github.com/DigiMancer3D/DMST) ecosystem.

> **Current release: `v0.2.0-alpha.6`,  Native Click Bridge + SmartSense Multi-Profile Support.** Tested live on CachyOS/KDE Wayland with soloPAW Linux Desktop, Megabonk, and Cyberpunk 2077 profiles.

## ✨ Features

- **Live G502 button glows:** L1/R1, middle click, G-Shift/trigger, thumb and top buttons, scroll up/down, and wheel tilt left/right.
- **Native + remapped input detection:** observes physical G502 events, Input Remapper forwarding proxies, and virtual outputs where available.
- **Profile-aware SmartSense:** detects the active Input Remapper preset and interprets actions according to the current mapping without rewriting your mouse configuration.
- **Layered controls:** understands G-Shift combinations and common Input Remapper macros, including tap/hold, nested tap gestures, held-key outputs, and multi-key combinations.
- **Hold/release accuracy:** merges input sources so duplicate event paths don't prematurely turn off a still-held button.
- **360° 2D movement-direction display:** configurable ring/dot indicator based on mouse movement.
- **OBS-friendly visuals:** always-on-top window, customizable backgrounds, magenta/green/blue chroma-key presets, and saveable display options.
- **Global Linux input capture:** `evdev` backend continues tracking while a game or another monitor has focus; `pynput` provides a compatibility fallback.
- **Built-in diagnostics:** Auto Mapping Report, Global Input Backend Report, optional `ratbagctl` smart mouse query, and a command-line doctor.
- **Local-first:** no account, cloud service, or network connection required for live visualizing.

## 🖥️ Platform support

| Platform / display environment | Status |
| --- | --- |
| **CachyOS / Arch-family Linux, KDE Wayland** | ✅ Live-tested |
| **Linux with X11** | 🟡 Supported input paths; configuration-dependent |
| **Other Linux distributions or Wayland desktops** | 🟡 Expected where Python/Tk, `evdev`, device access, and Input Remapper are configured; not individually verified |
| **Windows / macOS** | ❌ Not currently supported nor verified |

**Wayland and X11 are Linux display environments, not separate operating systems.** G502V uses Linux input-event devices to avoid the usual Wayland limitation where window-level hooks cannot see gameplay inputs outside the visualizer. Its exact behavior depends on which G502 and Input Remapper event nodes are readable.

## 📥 Install on Linux

**Requirements:** Python 3, Tkinter/Tk, and a Logitech G502 HERO. Input Remapper is recommended for automatic preset recognition and full global-input telemetry; `ratbagctl`/libratbag is optional for onboard mouse configuration queries.

### 1. Get the source

```bash
git clone https://github.com/DigiMancer3D/G502V.git
cd G502V
```


### 2. Ensure Tkinter is installed

For CachyOS / Arch:

```bash
sudo pacman -S --needed python tk
```

For Ubuntu / Debian:

```bash
sudo apt install python3 python3-venv python3-tk
```

### 3. Set up and launch

```bash
bash ./setup_venv.sh
./run.sh
```

The setup creates a local `.venv` and installs `pynput` and `evdev`. The launcher uses that environment automatically. You can also launch through `python3 g502viz.py` when the dependencies are already installed in the selected Python environment.

> If `run.sh` reports a missing dependency, run `bash ./doctor.sh` for diagnostics. You normally do **not** need root privileges to start the application.

## 🎮 How to use G502V

1. Start G502V and the game or software you want to show.
2. Press, hold, scroll, or tilt the G502: the matching mouse control should illuminate.
3. Open **☰ MENU** or right-click the visualizer window to access settings.
4. Choose **Auto Sync Input Remapper Profile** for automatic profile detection, or **Choose Input Remapper Profile…** to select a preset manually.
5. Open **Auto Mapping Report** to see how keyboard/macro outputs map back to G502 controls.
6. Open **Global Input Backend Report** to inspect the detected physical/proxy/virtual device sources and native click status.
7. Use **Edit Keys & 2D Direction** and **Change Background Color** to adjust the overlay for your scene.

**Reset Settings** restores visual preferences; **Reset Mapping** restores custom mappings. Personal data is saved locally in `settings.crumbs` and `mappings.json`.

### OBS Studio quick setup

1. Launch G502V and select a **magenta**, **green**, or **blue** background that contrasts with the mouse artwork.
2. In OBS Studio, add a **Window Capture** of the G502V window (capture availability varies with compositor and OBS setup).
3. Add a **Color Key** filter matching your selected background.
4. Crop, resize, and position the visualizer in your stream layout.
5. Keep your game focused and confirm button glows remain responsive in the OBS preview.

G502V is a standalone desktop visualizer; OBS captures its window. It does not require a direct OBS plugin or API connection.

## 🐾 Tested soloPAW profiles

| Profile | Input style | What G502V visualizes |
| --- | --- | --- |
| **Megabonk v1.1** | Native L1/R1 plus direct mappings | Normal clicks, holds, wheel, tilt, and mapped actions |
| **Linux Desktop v1.5** | Input Remapper and G-Shift | Main buttons, modifiers, media/desktop mappings, and combinations |
| **Cyberpunk 2077 v2.9** | Layered G-Shift and gesture macros | Main buttons, tap/hold mappings, wheel tilt, and profile-aware actions |

These are **examples, not the only possible profiles**. G502V can read other compatible Input Remapper presets, but their macro coverage depends on the expressions and available input event sources.

## 🔍 Troubleshooting

| Problem | First thing to check |
| --- | --- |
| Clicks work inside G502V, but not during a game | Open **Global Input Backend Report** and verify the `evdev` backend and readable G502/Input Remapper sources |
| A native L1/R1 press is missing | Confirm the active preset in **Auto Mapping Report** and inspect native main-click passthrough in **Global Input Backend Report** |
| A profile switch is not detected | Use **Auto Sync Input Remapper Profile**, then inspect the Input Remapper service/preset status |
| Global device nodes are unreadable | Close games/streams, then run `bash ./setup_global_input_access.sh` **only if necessary** |
| `ratbagctl` is missing | Optional: install libratbag tools for the onboard mouse query; core live visualization does not depend on that query |
| UI or images are missing | Verify you downloaded the full repo or portable release and retained the `images/` directory |
| Environment/dependency issue | Run `bash ./doctor.sh` and `python3 -m unittest discover -s tests -v` |

**Input permissions:** The optional access helper requests confirmation and `sudo`, installs a narrowly scoped udev rule for G502/Input Remapper nodes, and **restarts Input Remapper**. Do not run it during a game or live stream. The normal launcher does not change profiles or seize devices exclusively.

## 🗂️ Important files

| Path | Purpose |
| --- | --- |
| `g502viz.py` | Application and UI |
| `evdev_global_input.py` | Linux global-input/native-click bridge |
| `input_remapper_auto.py` | SmartSense preset detection and macro interpretation |
| `motion_direction.py` | 2D motion-direction tracking |
| `images/` | G502 artwork and pressed-state overlays |
| `run.sh`, `setup_venv.sh` | Launch and local dependency setup |
| `doctor.sh`, `setup_global_input_access.sh` | Diagnostics and optional permission setup |
| `apply_update.sh`, `README_UPDATE.md` | In-place upgrade helper and release details |
| `tests/` | Automated input, parser, and motion tests |
| `settings.crumbs`, `mappings.json` | Personal preferences and mappings, generated/maintained locally |


## 🔗 More from DigiMancer3D

G502V is independently useful, but it also fits into a larger **mouse-first, OBS-friendly creator workflow**:

| Project | How it relates |
| --- | --- |
| [🐾 soloPAW](https://github.com/DigiMancer3D/soloPAW) | Mouse-first accessibility toolkit, G502 profiles, Profile Switcher, and Controller Map Studio; **the closest functional companion to G502V** |
| [🧰 DigiMancer Streamer Tools (DMST)](https://github.com/DigiMancer3D/DMST) | Parent hub and integration map for local streamer tools |
| [🎛️ Streamer Board & Console](https://github.com/DigiMancer3D/Streamer-Board-Console) | Launch/manage creator apps and reusable OBS-oriented pinboards |
| [🔀 DMS Live Switcher](https://github.com/DigiMancer3D/DMS-Live-Switcher) | Live show switching and timing for compatible pinboards and deck cards |
| [🃏 Deck Card Widget](https://github.com/DigiMancer3D/Deck-Card-Widget) | Text, PNG, QR, and sticker/card overlays for OBS scenes |
| [📚 DM-SBC-HB Resource Hub](https://github.com/DigiMancer3D/DM-SBC-HB) | Installation and compatibility guidance across streamer applications |
| [🥷 Bitninja Mocap Lite](https://github.com/DigiMancer3D/Bitninja-Mocap-Lite) | Optional local VTuber/VRM companion to capture alongside G502V in OBS |

**Integration note:** soloPAW and Input Remapper provide the profile-aware control data that G502V can visualize. The other tools are **complementary applications** that can share an OBS production workflow; a direct G502V-to-DMS/SBC/DCW automation protocol is *not* claimed here.

## 🧪 Project status & feedback

This is an **alpha release**. The three supplied soloPAW profiles passed live testing on CachyOS, but new devices, distributions, remapping styles, and permissions should be tested individually. 

## 📄 Attribution

Made by **[DigiMancer3D](https://github.com/DigiMancer3D)** for streamers, accessibility-minded gamers, and local-first creators.

----

**One mouse. More control. Visible to everyone.** 🐾
