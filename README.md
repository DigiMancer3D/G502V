# G502V - G502 Visualizer

**A lightweight, real-time G502 HERO mouse visualizer designed specifically for OBS streaming.**

This tool overlays your G502 mouse on screen and lights up the exact buttons, scroll wheel, and tilt actions as you press them! Perfect for game streams, tutorials, or showcasing your mouse setup.

---

## ✨ Features

- Real-time button, scroll, and **M3 tilt** visualization
- **Yew Icon** *(mouse direction indicator)* with customizable position, ring, and dot
- Full support for **G-Shift lighting** as overlayed button glows
- **OBS-ready**! Chroma key background support *(magenta, green, blue)*
- Global mouse tracking *(useable even if unseen on desktop)*
- Save & restore all settings *(position, colors, visibility)*
- *Smart mouse configuration reader* via `ratbagctl`

---

## 📥 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/DigiMancer3D/G502V.git
cd G502V
```

### 2. Create a Virtual Environment (Required)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Required Packages

```bash
pip install pynput
```

> **Note**: On some Linux distributions you may also need:
> ```bash
> sudo apt install python3-tk
> ```

### 4. Make the Script Executable (Optional but Recommended)

```bash
chmod +x g502viz.py
```

You can now run the program with:

```bash
./g502viz.py
```

Or simply:

```bash
python3 g502viz.py
```

---

## 🚀 How to Use

1. **Launch the program**
   ```bash
   python3 g502viz.py
   ```

2. **Right-click** anywhere on the window or click the **☰ MENU** button to access all options.

3. **Basic Controls**
   - Press any key or click/tilt/scroll your G502 → the corresponding button lights up
   - The **green dot** on the Yew Icon follows your mouse movement direction in real-time

4. **Edit Key Mappings**
   - Go to **Edit Mapped Keys**
   - Click **Detect** next to any button, then press the key or action you want to map
   - Supports some speciality mappings: arrows, shifts, ctrl, alt, and more

5. **Customize Appearance**
   - **Change Background Color** → Choose solid color or use OBS chroma key presets *(Magenta/Green/Blue)*
   - **Yew Icon section** → Move, resize ring/dot, hide/show elements *(click, click, click to move; no hold setup)*

6. **OBS Setup Tips**
   - Use **Magenta** or **Blue** background *(so Dectector Display stays visible)*
   - In OBS, add a **Color Key** filter and key out the background color 

---

## 📁 File Structure

| File                  | Purpose |
|-----------------------|---------|
| `g502viz.py`          | Main program |
| `settings.crumbs`     | Saves background color, Yew Icon position, visibility settings |
| `mappings.json`       | Stores your custom key-to-button mappings |
| `images/`             | All button images *(normal & clicked versions)* + `G502Vicon.png` *(icon image)* |

### `settings.crumbs` Format (JSON)

```json
{
  "background_color": ["#ff00ff"],
  "detector_label_visible": [true],
  "menu_button_visible": [true],
  "yaw_center_x": [120],
  "yaw_center_y": [430],
  "yaw_ring_radius": [55],
  "yaw_dot_size": [10],
  "yaw_visible": [true],
  ...
}
```

---

## 🔄 Reset Options

- **Reset Settings** → Restores all visual settings to default
- **Reset Mapping** → Restores key mappings to default

---

## 🛠️ Troubleshooting

| Problem                        | Solution |
|--------------------------------|----------|
| Black bar appears at bottom    | Already fixed in current version |
| Yaw icon appears in wrong place| Restart the program (it loads saved position on startup) |
| No lights when pressing keys   | Make sure `pynput` is installed and try running as normal user |
| Ratbagctl not found            | Install with `sudo apt install ratbagctl` (optional) |

---

## 📌 Why This Tool?

This project was created because most mouse overlays are generic. **G502V** is specifically built for the **Logitech G502 HERO** and includes support for:

- M3 wheel tilt (left/right)
- G-Shift lighting effects
- Full button glow visualization
- OBS chroma key workflow

---

## 📄 License

This project is open source. Feel free to use and modify it for your streams.

---

**Made for streamers, by a streamer.**

*Enjoy your streams! 🎮*
