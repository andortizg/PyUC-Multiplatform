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
| `RPi.GPIO` | Optional (RPi only) | Physical GPIO PTT button |

Install all at once:

```bash
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil
```

### Raspberry Pi (Trixie / Python 3.13)

```bash
# System packages
sudo apt install portaudio19-dev python3-dev python3-tk python3-rpi.gpio

# Python packages in a venv (recommended on Trixie)
python3 -m venv ~/pyuc_env
source ~/pyuc_env/bin/activate
pip install customtkinter pyaudio Pillow requests beautifulsoup4 psutil audioop-lts
```

> **JACK / ALSA noise:** On Raspberry Pi the application pre-loads `libjack` and installs an ALSA null error handler before opening any audio device. The wall of "Unknown PCM / Cannot connect to JACK" messages is suppressed automatically. If the application still segfaults, the cleanest fix is to remove the unused JACK packages:
> ```bash
> sudo apt remove --purge libjack-jackd2-0 jackd2 && sudo apt autoremove
> ```

---

## Quick start

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
