# pyUC — EA7HQL Edition

> A multiplatform USRP client for DVSwitch / Analog_Bridge with full support for desktop PCs and Raspberry Pi touchscreens.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Amateur%20Radio%20Use-green)](#license)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows%20%7C%20macOS%20%7C%20Raspberry%20Pi-lightgrey)](#requirements)

---

## Overview

**pyUC EA7HQL Edition** is a refactored and extended version of the original [pyUC](https://github.com/DVSwitch/USRP_Client) USRP client by N4IRR / DVSwitch. It connects to a [DVSwitch](https://dvswitch.org) / Analog_Bridge server and provides a full PTT transceiver interface for DMR, P25, YSF, NXDN and D-STAR digital voice modes.

This edition cleanly separates the USRP protocol engine from the UI layer into independent modules, making it straightforward to build new interfaces for any platform or form factor.

### Key features

- **Decoupled architecture** — `pyUC_core.py` handles all USRP protocol, audio I/O and state; any UI registers callbacks and calls methods. Zero tkinter dependency in the core.
- **customtkinter UI** (`pyUC_ui_ctk.py`) with three built-in screen profiles: PC 800×600, Raspberry Pi 5" 800×480 and Raspberry Pi 3.5" 480×320. Profile auto-detected or forced in the ini file.
- **Dark / light theme** configurable via `themeMode` in `pyUC.ini`. Full colour palette overridable via the `[COLORS]` section — no code changes needed.
- **HamQTH callsign lookup** — retrieves Maidenhead grid locator, city and operator name for received callsigns. Calculates and displays distance from your own locator.
- **QRZ.com photo scraping** — operator photo displayed in the QRZ card on every received transmission.
- **Own-data display** — at startup the QRZ card shows your own callsign, photo, name and locator. After a configurable timeout it reverts from the last received station back to your own data.
- **Pi-Star reflector list download** — fetches live DMR, P25, YSF and NXDN host lists with one button press.
- **Settings sub-navigation** — settings screen is split into tabbed sub-pages (SERVER · IDENTITY · AUDIO · GPIO · PI-STAR), each fitting the RPi 5" screen without scrolling.
- **Favorites** — per-mode favourite talk groups defined in `[FAV_*]` ini sections; filter the TG list with the FAVS ★ button.
- **Software AGC** on the TX path — configurable target level, max gain, attack and release.
- **Software volume control** — mic and speaker volume sliders apply a PCM gain multiplier in the audio thread, fully cross-platform.
- **VOX** (voice-operated PTT) with threshold and hold-off delay.
- **GPIO PTT** — physical push-to-talk on any BCM GPIO pin. Bug-fixed: GPIO PTT now correctly drives the USRP TX path (not just the UI indicator).
- **Spacebar PTT** — toggle PTT with the spacebar when the application has focus.
- **Fullscreen / kiosk mode** — configurable via `fullscreen = 1`, Escape key to exit.
- **Raspberry Pi shutdown button** built into the tab bar.
- Call log with right-click lookup on QRZ, aprs.fi and Brandmeister. Column widths persisted in the ini file.

---

## What's new in v2.0

### Core — audio pipeline rewrite (`pyUC_core.py`)

The entire RX audio path has been redesigned to eliminate the micro-cuts that appeared during reception, particularly on Raspberry Pi 3B+.

**Decoupled RX/play threads.** Previously the USRP receive thread was also responsible for audio playback, so any network jitter or CPU spike stalled PortAudio directly. In v2.0 the receive thread only enqueues raw 8 kHz payloads into a thread-safe queue (the jitter buffer); a dedicated `_play_thread` drains it and writes to PortAudio independently.

**Jitter buffer with prefill.** `_play_thread` accumulates `RX_BUFFER_PACKETS + RX_PREFILL_PACKETS` packets (≈ 100 ms) before starting playback on each new transmission. This absorbs network jitter bursts without audible gaps.

**Silence injection on underrun.** If the queue empties mid-transmission, the play thread writes up to `RX_MAX_SILENCE_BLOCKS` silent frames to keep the PortAudio stream cebado. This prevents the click caused by stream re-arming after a momentary gap.

**All DSP at 8 kHz.** Speaker volume, RX AGC and soft clip now operate on the 8 kHz payload before resampling. This reduces the DSP workload by 6× compared to processing at 48 kHz. The final `audioop.ratecv` to 48 kHz only happens if the output device requires it (see below).

**Native 8 kHz streams.** The output and input streams now open at 8 kHz by default (`native8k = 1`), delegating resampling to ALSA `plughw` in C. The `audioop.ratecv` calls in both directions are eliminated entirely on hardware that supports 8 kHz (most USB audio adapters and the RPi built-in audio do). If a device rejects 8 kHz, the streams fall back automatically to 48 kHz and the Python resampling is restored.

**Optimised soft clip.** `_soft_clip` now returns the input unchanged (no allocation) if `audioop.max` shows no sample exceeds the threshold — the common case. When clipping is needed, it uses numpy vectorised operations if numpy is installed, or the existing sample loop otherwise.

**TX thread idle path.** When PTT is off and VOX is disabled, the TX thread drains the microphone input without resampling or computing RMS, reducing GIL contention against the play thread during reception.

**Minor fixes.** `settimeout` moved outside the receive loop (was being reset every packet). `del buf[:n]` replaces slice assignment to avoid copying the tail on every block.

### UI — numeric keypad (`pyUC_ui_ctk.py`)

A walkie-talkie style numeric keypad popup opens when the user clicks the TG entry field. This is designed for touchscreen operation on Raspberry Pi where typing on a physical keyboard is inconvenient.

- Layout: digits 1–9, `*`, `0`, `#`, backspace `⌫`, `ESC` and a full-width `CALL` button.
- Positioned automatically below the TG entry field, clamped to screen edges.
- Physical keyboard continues to work on the entry field at all times — the keypad is additive, not a replacement.
- Keyboard shortcuts: digit/`*`/`#` keys add to the display, `BackSpace` deletes, `Enter` dials, `Escape` closes.
- Can be enabled or disabled at runtime via **Settings → GPIO → Numeric keypad on TG click** without restarting.
- Controlled by `keypadEnable` in `pyUC.ini`.

### UI — private call checkbox (`pyUC_ui_ctk.py`)

The compact G/P dropdown for DMR private calls has been replaced by a **Priv** checkbox to the right of the TG entry. Unchecked = group call (default); checked = private call (appends `#` to the dial string).

### UI — VU meter optimisation (`pyUC_ui_ctk.py`)

The audio level meter no longer calls `canvas.delete('all')` and `create_rectangle` on every pump cycle (100 ms). Instead it creates the bar rectangle once and repositions it with `canvas.coords()`, caching the canvas height and the last painted fill width. The canvas is not touched at all if the level has not changed since the previous frame.

### Configuration additions (`pyUC_config.py`, `pyUC.ini`)

| Key | Default | Description |
|---|---|---|
| `native8k` | `1` | Open audio streams at 8 kHz; ALSA resamples in C. `0` = force 48 kHz (Python resampling) |
| `keypadEnable` | `1` | Show numeric keypad popup on TG entry click |

---

## Architecture

```
pyUC.ini
    │
    ├─► load_config()  ──► AppConfig  (pyUC_config.py)
    │                           │
    │                           ├── ColorTheme   (all UI colours)
    │                           └── talk_groups / favorites / macros

pyUC_core.py  ──  USRPCore
    │  UDP socket · pyaudio RX/TX · USRP protocol · VOX · AGC
    │  Software volume gain (mic_vol / spk_vol)
    │
    ├─► events fired on background threads
    │       registered | unregistered | rx_begin | rx_end | ptt_change
    │       transmit_enable | audio_level | connected | disconnected
    │       mode_change | tg_added | text_message | …
    │
    └─► commands (any thread, GIL-safe)
            connect() | disconnect_tg() | toggle_ptt() | set_ptt()
            set_mode() | set_remote_tg() | set_remote_ts() | …

pyUC_services.py  ──  background services
    │  UserDB · HamQTHSession · QRZPhotoFetcher
    │  PiStarUpdater · SysMonitor · GPIOPtt · CallsignWorker

pyUC_ui_base.py  ──  UIAdapter (abstract base)
    │  Defines the interface any UI must implement (16 methods)

pyUC_ui_ctk.py  ──  CtkUI(UIAdapter)
    │  customtkinter implementation
    │  Layout class: pixel budgets per screen profile
    │  All core events → ipc_queue → _pump() on main thread (100 ms)

pyUC_app.py  ──  USRPApp  (controller + entry point)
    │  Wires core ↔ services ↔ UI
    └─► main() — load_config → CtkUI → USRPApp → ui.run() → app.stop()
```

---

## File structure

```
pyUC_app.py             Entry point and application controller
pyUC_core.py            USRP protocol engine — no UI dependencies
pyUC_config.py          AppConfig dataclass, load_config(), save_config()
pyUC_services.py        HamQTH, QRZ, Pi-Star, GPIO, sysmon, callsign worker
pyUC_ui_base.py         UIAdapter abstract base class
pyUC_ui_ctk.py          customtkinter UI (PC + RPi 5" + RPi 3.5")
pyUC.ini                Configuration file
USRP_API_reference.md   Full API reference for building new UIs
README.md               This file
LAYOUT.md               Guide to changing sizes, positions and colours
```

---

## Requirements

### Python version

Python **3.8** through **3.12** — uses the built-in `audioop` module.
Python **3.13+** — `audioop` was removed from the stdlib; install the drop-in replacement:

```bash
pip install audioop-lts
```

### Python packages

| Package | Required | Purpose |
|---|---|---|
| `customtkinter` | **Yes** | UI framework |
| `pyaudio` | **Yes** | Audio capture and playback |
| `audioop-lts` | Python 3.13+ | Drop-in `audioop` replacement |
| `Pillow` | Recommended | QRZ operator photo display |
| `requests` | Recommended | QRZ photo scraping, Pi-Star download |
| `beautifulsoup4` | Recommended | QRZ photo URL parsing |
| `psutil` | Optional | CPU / RAM / temperature in topbar |
| `numpy` | Optional | Faster soft clip in RX DSP path |
| `RPi.GPIO` | Optional (RPi only) | Physical GPIO PTT button |

Install all at once:

```bash
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil numpy
```

### Raspberry Pi (Trixie / Python 3.13)

```bash
# System packages
sudo apt install portaudio19-dev python3-dev python3-tk python3-rpi.gpio

# Python packages in a venv (recommended on Trixie)
python3 -m venv ~/pyuc_env
source ~/pyuc_env/bin/activate
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil numpy audioop-lts
```

> **JACK / ALSA noise:** On Raspberry Pi the application pre-loads `libjack` and installs an ALSA null error handler before opening any audio device. The wall of "Unknown PCM / Cannot connect to JACK" messages is suppressed automatically. If the application still segfaults, the cleanest fix is to remove the unused JACK packages:
> ```bash
> sudo apt remove --purge libjack-jackd2-0 jackd2 && sudo apt autoremove
> ```

> **CPU governor:** For best audio performance on Raspberry Pi set the governor to `performance`:
> ```bash
> sudo cpufreq-set -g performance
> ```

---

## How to compile / install

pyUC is pure Python — no compilation step is needed. Setup consists of creating a virtual environment and installing the dependencies.

### Linux / Raspberry Pi

```bash
# 1. System-level build deps (needed by pyaudio → PortAudio)
sudo apt install portaudio19-dev python3-dev python3-tk python3-rpi.gpio

# 2. Create and activate virtual environment
python3 -m venv ~/pyuc_env
source ~/pyuc_env/bin/activate

# 3. Install Python dependencies
pip install --upgrade pip
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil numpy

# Python 3.13+ only (audioop removed from stdlib)
pip install audioop-lts

# 4. Run
python3 pyUC_app.py
```

### Windows / macOS

```bash
# 1. Create and activate virtual environment
python -m venv pyuc_env
source pyuc_env/bin/activate   # macOS / Linux
pyuc_env\Scripts\activate      # Windows

# 2. Install Python dependencies
pip install --upgrade pip
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil numpy

# Python 3.13+ only
pip install audioop-lts

# 3. Run
python pyUC_app.py
```

> **pyaudio on Windows:** if `pip install pyaudio` fails, download the matching `.whl` from [Unofficial Windows Binaries](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio) and install it with `pip install <file>.whl`.

> **pyaudio on macOS:** install PortAudio first via Homebrew: `brew install portaudio`, then `pip install pyaudio`.

---



1. Copy `pyUC.ini` and edit the `[DEFAULTS]` section at minimum:

```ini
[DEFAULTS]
myCall        = EA7HQL
subscriberID  = 2142001       ; your DMR/CCS7 ID
ipAddress     = 192.168.1.100 ; DVSwitch / Analog_Bridge host
screenProfile = rpi5          ; pc | rpi5 | rpi35
```

2. Run:

```bash
python3 pyUC_app.py
# or specify an ini file:
python3 pyUC_app.py /path/to/pyUC.ini
```

3. Select a mode (DMR / P25 / YSF / NXDN), choose a talk group and press **PTT — TRANSMIT** (or the spacebar, or the GPIO button).

---

## Configuration reference

All settings live in `pyUC.ini`.

### `[DEFAULTS]` — general settings

| Key | Default | Description |
|---|---|---|
| `myCall` | `N0CALL` | Your amateur radio callsign |
| `subscriberID` | `3112000` | DMR / CCS7 ID |
| `repeaterID` | `311200` | DMR peer / repeater ID |
| `ipAddress` | `1.2.3.4` | DVSwitch / Analog_Bridge host |
| `usrpTxPort` | `12345` | TX port (comma-separated for multi-server) |
| `usrpRxPort` | `12345` | Local UDP receive port |
| `defaultServer` | `DMR` | Startup mode (`DMR` / `P25` / `YSF` / `NXDN`) |
| `slot` | `2` | DMR time slot (1 or 2) |
| `in_index` | *(default)* | pyaudio input device index, or `-1` for RX-only |
| `out_index` | *(default)* | pyaudio output device index |
| `native8k` | `1` | Open audio streams at 8 kHz natively; `0` = force 48 kHz |
| `micVol` | `50` | Microphone software gain 0–100 (applied as PCM multiplier) |
| `spkVol` | `50` | Speaker software gain 0–100 |
| `screenProfile` | *(auto)* | `pc` \| `rpi5` \| `rpi35`; auto-detected from `windowWidth/Height` |
| `windowWidth` | `800` | Used for profile auto-detection |
| `windowHeight` | `600` | Used for profile auto-detection |
| `fullscreen` | `0` | `1` = start in fullscreen / kiosk mode (Escape key to exit) |
| `themeMode` | `dark` | `dark` \| `light` |
| `voxEnable` | `0` | `1` = enable VOX (voice-operated PTT) |
| `voxThreshold` | `200` | VOX trigger RMS level (0–32767) |
| `voxDelay` | `50` | VOX hold-off in audio chunks |
| `agcEnable` | `0` | `1` = enable software AGC on TX path |
| `agcTarget` | `4000` | AGC target RMS level |
| `agcMaxGain` | `8.0` | Maximum AGC gain multiplier |
| `agcAttack` | `0.1` | AGC attack coefficient (higher = faster) |
| `agcRelease` | `0.02` | AGC release coefficient (lower = slower) |
| `gpioPttPin` | `-1` | BCM GPIO pin for hardware PTT (`-1` = disabled) |
| `gpioPttActiveLow` | `1` | `1` = active low (internal pull-up) |
| `spacebarPtt` | `1` | `1` = spacebar toggles PTT when app has focus |
| `keypadEnable` | `1` | `1` = show numeric keypad popup on TG entry click |
| `useQRZ` | `1` | `1` = scrape operator photo from QRZ.com |
| `hamUser` | *(empty)* | HamQTH username for grid-locator and city lookup |
| `hamPass` | *(empty)* | HamQTH password |
| `loc` | *(empty)* | Your Maidenhead locator (e.g. `IM76sp`) for distance calculation |
| `showOwnData` | `1` | `1` = show your own QRZ data at startup |
| `ownDataTimeout` | `30` | Seconds after last RX before reverting to own data (`0` = never) |
| `logColWidths` | `80,78,145,64,72` | Call log column widths in pixels (saved automatically on "Save settings") |
| `pingTimer` | `0` | NAT keep-alive interval in seconds (`0` = disabled) |
| `levelEverySample` | `2` | Fire `audio_level` event every N received packets |

### `[COLORS]` — UI theme

Every colour can be overridden. Values are `#rrggbb` hex strings. See `LAYOUT.md` for the complete list with defaults and descriptions.

### Mode sections — `[DMR]`, `[P25]`, `[YSF]`, `[NXDN]`

Each entry is `display_name = dial_string`:

```ini
[DMR]
Disconnect  = 4000
Spain       = 21401
World Wide  = 91
Parrot      = 9990

[YSF]
Disconnect    = disconnect
Parrot        = "register.ysfreflector.de:42020"
EA C4FM       = 212.237.3.141:42000
```

### `[FAV_*]` — favourites per mode

```ini
[FAV_DMR]
Spain   = 21401
Europe  = 92

[FAV_YSF]
EA C4FM = 212.237.3.141:42000
```

Entries appear when the **FAVS ★** filter is active. Favourites are stored independently of the main TG list and survive Pi-Star host updates.

### `[PISTAR_HOSTS]` — Pi-Star download URLs

```ini
[PISTAR_HOSTS]
DMR  = https://www.pistar.uk/downloads/DMR_Hosts.txt
YSF  = https://www.pistar.uk/downloads/YSF_Hosts.txt
P25  = https://www.pistar.uk/downloads/P25_Hosts.txt
NXDN = https://www.pistar.uk/downloads/NXDN_Hosts.txt
```

### `[MACROS]`

```ini
[MACROS]
Kill Gateways = *666
TGIF          = *TGIF
BM            = *BM
```

---

## GPIO PTT wiring (Raspberry Pi)

Connect a normally-open momentary push button between the chosen BCM GPIO pin and GND. The internal pull-up resistor is enabled in software.

```
RPi GPIO 18 ──── [button] ──── GND
                (pull-up enabled via gpioPttActiveLow = 1)
```

```ini
gpioPttPin       = 18
gpioPttActiveLow = 1
```

Set `gpioPttPin = -1` to disable.

> **Note:** GPIO PTT drives both the UI indicator **and** the USRP TX path. When the button is pressed, audio is captured and transmitted to Analog_Bridge exactly as if the on-screen PTT button were pressed.

---

## Building a custom UI

`pyUC_core.py` is UI-agnostic. To build a new interface implement `UIAdapter` from `pyUC_ui_base.py` and wire it through `USRPApp`:

```python
from pyUC_config  import load_config
from pyUC_app     import USRPApp
from pyUC_ui_base import UIAdapter

class MyUI(UIAdapter):
    def show_rx_begin(self, call, tg, slot, mode, name): ...
    def show_rx_end(self, call, tg, loss, duration):     ...
    def show_ptt(self, state):                           ...
    def show_registered(self):                           ...
    def show_unregistered(self):                         ...
    def show_mode(self, mode, last_tg):                  ...
    def show_connected(self, tg_name):                   ...
    def show_disconnected(self):                         ...
    def show_photo(self, call, photo, name, grid, city): ...
    def show_status(self, text, color, temporary=False): ...
    def show_toast(self, title, message):                ...
    def show_tg_added(self, mode, tg_name, tg_value):   ...
    def show_audio_level(self, level):                   ...  # GIL-safe, no UI ops
    def show_sysmon(self, cpu, ram, temp):               ...
    def show_transmit_enable(self, enabled):             ...
    def run(self):                                       ...  # enter event loop

cfg = load_config('pyUC.ini')
ui  = MyUI(cfg)
app = USRPApp(cfg, ui)
ui.app = app
app.start('pyUC.ini')
ui.run()
app.stop()
```

See `USRP_API_reference.md` for the full API reference including packet formats, remote-control commands and AGC implementation details.

---

## Credits

| Role | Callsign | Name |
|---|---|---|
| Fork: UI redesign, multi-screen, HamQTH, AGC, GPIO PTT, Pi-Star, architecture | **EA7HQL** | Andrés Ortiz |
| v2.0: audio pipeline rewrite, jitter buffer, native 8 kHz streams, keypad UI | **EA7HQL** | Andrés Ortiz |
| Original pyUC author | **N4IRR** | Mike |
| Original DVSwitch co-author | **N4IRS** | Steve |
| Raspberry Pi UI inspiration | **DS5QDR** | Heonmin Lee |

---

## License

This software is for use on **amateur radio networks only**. It is provided for educational purposes. Use on commercial networks is strictly prohibited.

Permission to use, copy, modify and distribute this software is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.

**THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND.**

Original code copyright © 2014–2020 N4IRR / DVSwitch.
This fork copyright © 2026 EA7HQL — Andrés Ortiz.
