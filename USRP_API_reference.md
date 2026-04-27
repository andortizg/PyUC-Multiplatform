# pyUC Core — USRP API Reference

> Complete reference for `pyUC_core.py`, `pyUC_config.py`, `pyUC_services.py` and `pyUC_ui_base.py`.
> Intended for developers building a custom UI on top of the USRP engine without reading the source.

---

## Architecture overview

```
┌──────────────────────────────────────────────────────────┐
│                      Your UI                             │
│  Implements UIAdapter (pyUC_ui_base.py)                  │
│                                                          │
│  app = USRPApp(cfg, ui)                                  │
│  app.start('pyUC.ini')     ← starts all services        │
│  ui.run()                  ← enters event loop           │
│  app.stop()                ← clean shutdown              │
└───────────────────────────┬──────────────────────────────┘
                            │  IPC queue + 100 ms pump
┌───────────────────────────▼──────────────────────────────┐
│                     USRPApp                              │
│  Controller: wires core ↔ services ↔ UIAdapter          │
└───────┬───────────────────────────────────────┬──────────┘
        │                                       │
┌───────▼──────────┐                   ┌────────▼──────────┐
│   USRPCore       │                   │  pyUC_services    │
│  UDP · pyaudio   │                   │  HamQTH · QRZ     │
│  USRP protocol   │                   │  Pi-Star · GPIO   │
│  VOX · AGC       │                   │  SysMonitor       │
│  SW volume gain  │                   │  CallsignWorker   │
└──────────────────┘                   └───────────────────┘
        │ UDP (USRP protocol)
┌───────▼────────────┐
│   Analog_Bridge    │
│  (DVSwitch server) │
└────────────────────┘
```

All audio and network I/O runs on **background daemon threads**.
Callbacks are fired from those threads — the UI must marshal them to its own event loop
(IPC queue + `root.after()` in Tkinter, `Clock.schedule_once` in Kivy, etc.).

---

## Module: `pyUC_config`

### `AppConfig` (dataclass)

Produced by `load_config()`. Superset of `CoreConfig` — passed directly to `USRPCore`
(duck-typed compatible). All fields are read/write at runtime.

```python
@dataclass
class AppConfig:
    # ── Identity ──────────────────────────────────────────
    my_call:            str   = 'N0CALL'
    subscriber_id:      int   = 3112000
    repeater_id:        int   = 311200

    # ── Network ───────────────────────────────────────────
    ip_address:         str   = '1.2.3.4'
    usrp_tx_port:       List[int] = [12345]
    usrp_rx_port:       int   = 12345

    # ── Radio ─────────────────────────────────────────────
    default_server:     str   = 'DMR'
    slot:               int   = 2
    talk_groups:        Dict[str, List[Tuple[str,str]]]  # mode → [(name,dial),…]
    macros:             Dict[str, str]                   # dial → display_name
    favorites:          Dict[str, Tuple[str,str]]        # name → (mode, dial)

    # ── Audio ─────────────────────────────────────────────
    in_index:           Optional[int] = None  # None=default, -1=RX-only
    out_index:          Optional[int] = None
    mic_vol:            int   = 50            # 0–100 software gain on TX
    spk_vol:            int   = 50            # 0–100 software gain on RX
    vox_enable:         bool  = False
    vox_threshold:      int   = 200
    vox_delay:          int   = 50
    agc_enable:         bool  = False
    agc_target:         int   = 4000
    agc_max_gain:       float = 8.0
    agc_attack:         float = 0.1
    agc_release:        float = 0.02

    # ── UI / display ──────────────────────────────────────
    window_width:       int   = 800
    window_height:      int   = 600
    screen_profile:     str   = 'pc'    # 'pc' | 'rpi5' | 'rpi35'
    font_family:        str   = ''      # '' = auto per platform
    spacebar_ptt:       bool  = True
    fullscreen:         bool  = False
    theme_mode:         str   = 'dark'  # 'dark' | 'light'
    log_col_widths:     List[int] = [80, 78, 145, 64, 72]

    # ── GPIO ──────────────────────────────────────────────
    gpio_ptt_pin:       int   = -1      # -1 = disabled
    gpio_ptt_active_low: bool = True

    # ── Online lookups ────────────────────────────────────
    use_qrz:            bool  = True
    ham_user:           str   = ''      # HamQTH credentials
    ham_pass:           str   = ''
    my_locator:         str   = ''      # own Maidenhead locator for distance

    # ── Own data display ──────────────────────────────────
    show_own_data:      bool  = True
    own_data_timeout:   int   = 30      # 0 = stay on last-received station

    # ── Pi-Star ───────────────────────────────────────────
    pistar_hosts:       Dict[str, str]  # mode → URL
    colors:             ColorTheme      # full colour palette
```

---

### `load_config(path) → AppConfig`

Parses a `pyUC.ini` file and returns a fully-populated `AppConfig`.

```python
cfg = load_config('pyUC.ini')
```

**Raises** `SystemExit` if the file is missing or still contains placeholder values (`N0CALL`, `1.2.3.4`).

---

### `save_config(path, cfg) → bool`

Persists mutable settings back to the ini file, preserving all other sections and comments.

```python
save_config('pyUC.ini', cfg)
```

Returns `True` on success, `False` on I/O error.

---

## Module: `pyUC_core`

### Constants

#### USRP packet types

| Constant | Value | Description |
|---|---|---|
| `USRP_TYPE_VOICE`       | 0 | PCM audio (8 kHz, 16-bit mono, 320 bytes) |
| `USRP_TYPE_DTMF`        | 1 | DTMF / dialer string (ASCII) |
| `USRP_TYPE_TEXT`        | 2 | Control text (`REG:`, `INFO:`, …) |
| `USRP_TYPE_PING`        | 3 | NAT keep-alive |
| `USRP_TYPE_TLV`         | 4 | TLV-encoded command |
| `USRP_TYPE_VOICE_ADPCM` | 5 | ADPCM voice |
| `USRP_TYPE_VOICE_ULAW`  | 6 | µ-law voice |

#### TLV tags

| Constant | Value | Description |
|---|---|---|
| `TLV_TAG_BEGIN_TX`   | 0 | Start of transmission |
| `TLV_TAG_AMBE`       | 1 | AMBE vocoder frame |
| `TLV_TAG_END_TX`     | 2 | End of transmission |
| `TLV_TAG_TG_TUNE`    | 3 | Tune to talk group |
| `TLV_TAG_PLAY_AMBE`  | 4 | Play AMBE frame locally |
| `TLV_TAG_REMOTE_CMD` | 5 | ASCII remote-control command |
| `TLV_TAG_AMBE_49`    | 6 | AMBE 49-bit frame |
| `TLV_TAG_AMBE_72`    | 7 | AMBE 72-bit frame |
| `TLV_TAG_SET_INFO`   | 8 | Subscriber / station metadata |
| `TLV_TAG_IMBE`       | 9 | IMBE vocoder frame |
| `TLV_TAG_DSAMBE`     | 10 | DS-AMBE frame |
| `TLV_TAG_FILE_XFER`  | 11 | File transfer sub-protocol |

#### Audio

| Constant | Value | Description |
|---|---|---|
| `SAMPLE_RATE` | 48000 | Host sample rate; resampled to 8 kHz for USRP |

---

### `USRPCore`

#### Constructor

```python
core = USRPCore(cfg: AppConfig)
```

Creates the engine. Does **not** open sockets or start threads. Pass an `AppConfig` directly (it is duck-typed compatible with the internal `CoreConfig`).

---

#### Lifecycle

##### `core.start()`

Opens the UDP socket, initialises pyaudio, starts background threads (RX, TX, optional ping) and sends the first registration to Analog_Bridge.

##### `core.stop()`

Signals all threads to exit cleanly and sends `REG:UNREG` to AB if registered. Call before destroying the UI window.

---

#### Event system

##### `core.on(event, callback)`

Registers a callback for a named event. All callbacks fire from **background threads** — post to an IPC queue and handle in the main thread.

```python
core.on('rx_begin', lambda call, tg, slot, mode, name: ipc.put(('rx', call, tg)))
```

##### Event reference

| Event | Callback signature | Description |
|---|---|---|
| `'registered'`      | `()` | AB accepted the `REG:DVSWITCH` handshake |
| `'unregistered'`    | `()` | AB sent `REG:UNREG` (server shutting down) |
| `'rx_begin'`        | `(call:str, tg:str, slot:str, mode:str, name:str)` | Remote station started TX. `tg` = friendly name or raw number. `mode` = `'Group'` or `'Private'`. |
| `'rx_end'`          | `(call:str, tg:str, loss:str, duration:float, start_time:float)` | Remote station stopped. `duration` in seconds. |
| `'audio_level'`     | `(level:int)` | RMS level 0–100+. Fires 25–50× per second during RX and TX. |
| `'ptt_change'`      | `(active:bool)` | Local PTT state changed (VOX, spacebar, on-screen button). **GPIO PTT is handled separately by `GPIOPtt` and routed through `USRPApp`.** |
| `'transmit_enable'` | `(enabled:bool)` | `False` while a remote station is transmitting (half-duplex lockout). |
| `'connected'`       | `(tg_name:str)` | Successfully tuned to a TG after `connect()`. |
| `'disconnected'`    | `()` | Disconnected from current TG. |
| `'text_message'`    | `(title:str, text:str)` | Free-text message from AB (`INFO:` / `MSG:` prefix). |
| `'mode_change'`     | `(mode:str, last_tune:str)` | AB reports active protocol changed. |
| `'tg_added'`        | `(mode:str, tg_name:str, tg_value:str)` | Private-call TG auto-added to `talk_groups`. |
| `'macro_received'`  | `(macros:dict)` | AB pushed an ad-hoc macro menu `{dial:display}`. |
| `'error'`           | `(message:str)` | Non-fatal error (socket / audio stream). |
| `'ab_exiting'`      | `(sleep_time:int)` | AB is restarting; core re-registers automatically. |

---

#### Connection commands

##### `core.connect(tg, tg_name)`

Connects to a talk group or runs a macro. Fires `'connected'` on success.

```python
core.connect('3100',   'N. America')
core.connect('*TGIF',  'TGIF macro')
core.connect('americalink.hamfm.com:42000', 'America Link')   # YSF
core.connect('REF001CL', 'REF001C')                           # D-STAR
```

##### `core.disconnect_tg()`

Disconnects by dialling the first (Disconnect) entry for the current mode.

##### `core.set_mode(mode)`

Sends the `*MODE` macro to AB.

```python
core.set_mode('YSF')   # 'DMR' | 'P25' | 'YSF' | 'NXDN' | 'DSTAR'
```

##### `core.set_remote_tg(tg)`

Sets a single talk group on AB without the full `connect()` sequence.

##### `core.set_remote_tg_list(tg_list)`

Sets multiple simultaneous talk groups (monitoring mode).

```python
core.set_remote_tg_list(['3100', '3106', '3112'])
```

##### `core.set_remote_ts(ts)`

Sets the DMR time slot (1 or 2).

---

#### PTT

##### `core.toggle_ptt()`

Toggles PTT. Respects the half-duplex lockout — ignores key-up→transmit when a remote station is active.

##### `core.set_ptt(state: bool)`

Sets PTT to an explicit state.

```python
core.set_ptt(True)   # start transmitting
core.set_ptt(False)  # stop transmitting
```

> **GPIO PTT note:** GPIO events come from `GPIOPtt` in `pyUC_services`. `USRPApp` routes them by calling `core.set_ptt()` *and* `ui.show_ptt()`. Never route GPIO through `'ptt_change'` — that event is only for PTT changes the core itself initiates (VOX).

---

#### Metadata / configuration

##### `core.send_metadata()`

Sends subscriber callsign and DMR ID to AB (called automatically after registration).

##### `core.set_ambe_mode(mode: str)`

Tells AB which AMBE codec mode to use: `'DMR'` | `'DSTAR'` | `'YSF'` | `'NXDN'` | `'P25'`.

##### `core.request_info()`

Requests the INFO JSON from AB. Triggers a `'mode_change'` event on response.

---

#### Low-level protocol

##### `core.send_usrp_command(cmd: bytes, pkt_type: int)`

Builds and sends a raw USRP packet.

```python
core.send_usrp_command(b'REG:DVSWITCH', USRP_TYPE_TEXT)
```

##### `core.send_remote_ctrl_ascii(cmd: str)`

Wraps an ASCII string in `TLV_TAG_REMOTE_CMD` and sends it.

```python
core.send_remote_ctrl_ascii('txTs=2')
core.send_remote_ctrl_ascii('txTg=3100')
core.send_remote_ctrl_ascii('tgs=3100,3106,3112')   # multi-TG monitoring
```

---

#### Audio device enumeration

##### `core.list_audio_devices(want_input: bool) → List[str]`

```python
inputs  = core.list_audio_devices(True)
outputs = core.list_audio_devices(False)
```

---

#### Runtime-mutable properties

| Property | Type | Description |
|---|---|---|
| `core.ptt` | `bool` | Current PTT state |
| `core.reg_state` | `bool` | True if registered with AB |
| `core.current_mode` | `str` | Active protocol mode |
| `core.current_tg` | `str` | Active dial string |
| `core.cfg` | `AppConfig` | Configuration object (some fields live-writable) |
| `core.cfg.mic_vol` | `int` | TX software gain 0–100 (applied per audio chunk) |
| `core.cfg.spk_vol` | `int` | RX software gain 0–100 |
| `core.cfg.vox_enable` | `bool` | VOX enable (live) |
| `core.cfg.vox_threshold` | `int` | VOX threshold (live) |
| `core.cfg.vox_delay` | `int` | VOX delay (live) |
| `core.cfg.slot` | `int` | DMR time slot (live) |
| `core.talk_groups` | `dict` | `{mode: [(name,dial),…]}` — same object as `cfg.talk_groups` |

---

## Module: `pyUC_services`

### Utility functions

##### `locator_from_string(text) → Optional[str]`

Extracts the first Maidenhead locator (6-char) from a free-text string using regex.

##### `locator_to_latlon(locator) → Optional[Tuple[float,float]]`

Converts a Maidenhead locator to (latitude, longitude).

##### `calc_distance_km(loc1, loc2) → Optional[float]`

Calculates the great-circle distance in km between two Maidenhead locators.

---

### `UserDB`

In-memory callsign database loaded from a CSV file (user.csv format from RadioID.net).

```python
db = UserDB()
db.load('/path/to/user.csv')
entry = db.lookup('EA7HQL')   # → {'name': ..., 'city': ..., 'state': ..., 'country': ...} or None
db.download(url, dest_path, on_done=callback)  # async download
```

---

### `HamQTHSession`

Reusable HamQTH XML API session. Thread-safe.

```python
hq = HamQTHSession('EA7HQL', 'password')

info = hq.get_info('EA1ABC')
# → {'grid': 'IN70xx', 'city': 'Madrid, Spain', 'name': 'Juan García'}

grid = hq.get_locator('EA1ABC')   # → 'IN70xx' or ''

print(hq.status)   # → 'connected' | 'disabled' | 'error'
```

`status` becomes `'connected'` after the first successful lookup (even if the callsign has no grid).
`status` is `'disabled'` when no credentials are configured.

---

### `QRZPhotoFetcher`

Scrapes QRZ.com for operator photos. Results are cached in-memory. Thread-safe.

```python
fetcher = QRZPhotoFetcher(photo_w=200, photo_h=134)
img = fetcher.fetch('EA7HQL')   # → PIL.Image or None
```

---

### `PiStarUpdater`

Downloads reflector host lists from Pi-Star and merges them into `talk_groups`.

```python
updater = PiStarUpdater()
updater.update(
    host_urls   = cfg.pistar_hosts,    # {mode: url}
    talk_groups = cfg.talk_groups,     # modified in-place
    modes       = ['DMR', 'YSF'],
    on_done     = lambda: print('Done')
)
```

---

### `SysMonitor`

Periodic CPU, RAM and temperature monitor. Uses `psutil` and `/sys/class/thermal`.

```python
mon = SysMonitor(interval=5)
mon.start(callback=lambda cpu, ram, temp: ...)
mon.stop()
```

`callback` receives: `cpu` (float %, 1 decimal), `ram` (float %), `temp` (string like `"52.3°C"` or `"—"`).

---

### `GPIOPtt`

Physical PTT via a BCM GPIO pin. Silent no-op if `RPi.GPIO` is not available.

```python
gpio = GPIOPtt(pin=18, active_low=True)
gpio.start(callback=lambda pressed: ...)   # callback(True) = button pressed
gpio.stop()
```

> **Important:** the `callback` should call `app.set_ptt(pressed)` **and** `ui.show_ptt(pressed)`. Routing only to the UI will show the indicator but will not transmit audio.

---

### `CallsignWorker`

Background worker that resolves callsigns through user.csv → HamQTH → QRZ and fires a callback with the combined result.

```python
worker = CallsignWorker(user_db, hamqth_session, qrz_fetcher, use_qrz=True)
worker.start(callback=lambda call, photo, name, grid, city: ...)
worker.lookup('EA1ABC', name_meta='')   # duplicate requests silently dropped
worker.stop()
```

Resolution priority:
1. `user.csv` (name, city)
2. HamQTH XML API (grid, city, name — requires credentials)
3. Regex extraction from the `name` field (fallback grid from `TLV_TAG_SET_INFO`)

---

## Module: `pyUC_ui_base`

### `UIAdapter` (abstract base class)

All UI implementations must subclass `UIAdapter` and implement all 16 abstract methods.

```python
from pyUC_ui_base import UIAdapter

class MyUI(UIAdapter):
    ...
```

#### Method reference

| Method | Called when | Notes |
|---|---|---|
| `show_registered()` | AB accepted registration | Enable PTT button |
| `show_unregistered()` | AB disconnected | Disable PTT button |
| `show_rx_begin(call, tg, slot, mode, name)` | Remote TX starts | Update QRZ card, start ON AIR |
| `show_rx_end(call, tg, loss, duration)` | Remote TX ends | Log entry, stop ON AIR |
| `show_ptt(state: bool)` | PTT state changed | Update button appearance, TX meter |
| `show_mode(mode, last_tg)` | Mode changed | Update mode buttons, TG list |
| `show_connected(tg_name)` | TG connected | Update connected-TG display |
| `show_disconnected()` | TG disconnected | Clear connected-TG display |
| `show_photo(call, photo, name, grid, city)` | Callsign resolved | Update QRZ card |
| `show_status(text, color, temporary=False)` | Status changed | `temporary=True` reverts after 3 s |
| `show_toast(title, message)` | Notification | Non-blocking overlay |
| `show_tg_added(mode, tg_name, tg_value)` | New TG auto-added | Refresh TG list |
| `show_audio_level(level: int)` | Audio level update | **GIL-safe: only write an attribute, no widget ops** |
| `show_sysmon(cpu, ram, temp)` | System stats update | Update topbar stats label |
| `show_transmit_enable(enabled: bool)` | Half-duplex lock | Enable/disable PTT button |
| `run()` | After `app.start()` | Enter the UI event loop — must block |

---

## Module: `pyUC_app`

### `USRPApp`

Application controller. Wires `USRPCore` ↔ `pyUC_services` ↔ `UIAdapter`.

```python
app = USRPApp(cfg: AppConfig, ui: UIAdapter)
```

#### Key methods

| Method | Description |
|---|---|
| `app.start(ini_path)` | Starts all services, core and callsign worker. Triggers own-data lookup if `show_own_data=True`. |
| `app.stop()` | Clean shutdown of all services and core. |
| `app.pump()` | Drain the IPC queue. Call from the UI main thread every ~100 ms. |
| `app.connect(dial, name)` | Connect to a talk group. |
| `app.disconnect()` | Disconnect from current TG. |
| `app.set_ptt(state)` | Drive PTT on the core. Use this for GPIO PTT. |
| `app.toggle_ptt()` | Toggle PTT (on-screen button). |
| `app.set_mode(mode)` | Change radio mode. |
| `app.update_pistar()` | Trigger async Pi-Star host list download. |
| `app.save_settings(ini_path)` | Persist current `AppConfig` to the ini file. |

#### `main()`

Entry point. Accepts an optional path argument (`sys.argv[1]`).

```python
from pyUC_app import main
main()
```

---

## USRP packet format (binary reference)

Every packet starts with a fixed 32-byte header:

```
Bytes  0- 3  : 'USRP'  (ASCII eye-catcher)
Bytes  4- 7  : sequence number  (big-endian int32)
Bytes  8-11  : memory / flags   (big-endian int32)
Bytes 12-15  : keyup            (big-endian int32; 1=PTT on, 0=PTT off)
Bytes 16-19  : talk group       (big-endian int32)
Bytes 20-23  : packet type      (native int32; USRP_TYPE_* value << 24 for non-voice)
Bytes 24-27  : mpxid            (big-endian int32)
Bytes 28-31  : reserved         (big-endian int32)
Bytes 32+    : payload          (320 bytes PCM for voice; varies for other types)
```

**Voice audio:** 320 bytes — 160 samples × 2 bytes, 8 kHz / 16-bit / mono PCM. Software volume gain (`mic_vol` / `spk_vol`) is applied as a `audioop.mul()` multiplier before sending / after receiving.

**TLV_TAG_SET_INFO payload** (fired on each incoming RX start):

```
Byte  0    : TLV_TAG_SET_INFO (8)
Byte  1    : total TLV length
Bytes 2- 4 : source DMR ID      (24-bit big-endian)
Bytes 5- 8 : repeater/peer ID   (32-bit big-endian)
Bytes 9-11 : destination TG     (24-bit big-endian)
Byte  12   : time slot (1 or 2)
Byte  13   : call type flags    (bit 7 = private call)
Byte  14+  : NUL-terminated callsign (or JSON: {"call":"EA7HQL","name":"Andres","grid":"IM76sp"})
```

---

## Remote-control commands (Analog_Bridge)

Sent via `send_remote_ctrl_ascii()`:

| Command | Example | Description |
|---|---|---|
| `txTg=<tg>` | `txTg=3100` | Set transmit talk group |
| `txTs=<ts>` | `txTs=2` | Set DMR time slot |
| `tgs=<tg>[,…]` | `tgs=3100,3106` | Set monitored TG list |
| `txTg=0` | — | No TX TG (monitoring only) |
| `ambeMode=<m>` | `ambeMode=DMR` | Set AMBE codec mode |
| `ambeSize=<n>` | `ambeSize=72` | Set AMBE frame size in bits |
| `gateway_dmr_id=<id>` | — | Override gateway DMR ID |
| `gateway_call=<cs>` | — | Override gateway callsign |

Mode strings sent as `USRP_TYPE_DTMF`:

| String | Description |
|---|---|
| `*DMR` / `*P25` / `*YSF` / `*NXDN` / `*DSTAR` | Switch AB protocol mode |
| `*TGIF` / `*BM` | Connect to TGIF / Brandmeister |
| `*INFO` | Request INFO JSON |
| `*666` | Kill all gateway connections |

---

## Minimal UI skeleton

```python
import queue, threading
from pyUC_config  import load_config
from pyUC_core    import USRPCore
from pyUC_ui_base import UIAdapter

class MinimalUI(UIAdapter):
    def __init__(self, cfg):
        self.cfg = cfg
        self.app = None
        self._ipc = queue.Queue()

    # ── UIAdapter ──────────────────────────────────────────
    def show_registered(self):            print('Registered')
    def show_unregistered(self):          print('Unregistered')
    def show_rx_begin(self, *a):          print('RX start:', a[0])
    def show_rx_end(self, *a):            print('RX end:',   a[0])
    def show_ptt(self, state):            print('PTT', state)
    def show_mode(self, mode, last_tg):   print('Mode:', mode)
    def show_connected(self, tg):         print('Connected:', tg)
    def show_disconnected(self):          print('Disconnected')
    def show_photo(self, *a):             pass
    def show_status(self, t, c, **kw):    print('Status:', t)
    def show_toast(self, title, msg):     print(f'[{title}] {msg}')
    def show_tg_added(self, *a):          pass
    def show_audio_level(self, lv):       self._rx_level = lv   # GIL-safe
    def show_sysmon(self, *a):            pass
    def show_transmit_enable(self, en):   pass

    def run(self):
        """Simple CLI loop."""
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()
        while True:
            cmd = input('> ').strip()
            if cmd.startswith('c '):
                self.app.connect(cmd[2:], cmd[2:])
            elif cmd == 'ptt':
                self.app.toggle_ptt()
            elif cmd == 'q':
                break

    def _pump(self):
        import time
        while True:
            if self.app:
                self.app.pump()
            time.sleep(0.1)

# ── Bootstrap ──────────────────────────────────────────────
from pyUC_app import USRPApp

cfg = load_config('pyUC.ini')
ui  = MinimalUI(cfg)
app = USRPApp(cfg, ui)
ui.app = app
app.start('pyUC.ini')
ui.run()
app.stop()
```

---

## Thread safety notes

- `core.start()`, `core.stop()`, all `send_*`, `connect*` and `set_ptt()` are **safe to call from any thread** (CPython GIL + atomic socket sends).
- `core.cfg.mic_vol` and `core.cfg.spk_vol` are plain Python integers. Writing from the UI thread while the audio thread reads them is GIL-safe — no lock needed.
- `core.talk_groups` is a plain dict shared with `cfg.talk_groups`. `PiStarUpdater` modifies it in a background thread. If your UI reads the list while an update runs, wrap the read in a copy or use a lock.
- `show_audio_level()` fires 25–50 times per second. Assign `self._rx_level = level` only — never call widget methods here.
- Never call Tkinter / customtkinter widget methods from a background thread. Always post to an IPC queue and process in the main thread's timer callback.

---

## AGC implementation reference

Software AGC in `_tx_thread` in `USRPCore`:

```python
# __init__
self._agc_gain = 1.0

# _tx_thread, after reading audio:
rms = audioop.rms(audio, 2)

# Mic volume (applied first)
mic_gain = max(0, min(self.cfg.mic_vol, 100)) / 100.0
if mic_gain != 1.0:
    audio = audioop.mul(audio, 2, mic_gain)

# AGC (applied after mic gain)
if self.cfg.agc_enable:
    rms = audioop.rms(audio, 2) or 1
    error = self.cfg.agc_target / rms
    if error > 1:
        self._agc_gain += self.cfg.agc_attack  * (error - 1)
    else:
        self._agc_gain += self.cfg.agc_release * (error - 1)
    self._agc_gain = max(1.0, min(self._agc_gain, self.cfg.agc_max_gain))
    audio = audioop.mul(audio, 2, self._agc_gain)
```

AGC parameters (`AppConfig` / ini key):

| Field | ini key | Default | Description |
|---|---|---|---|
| `agc_enable` | `agcEnable` | `False` | Enable AGC |
| `agc_target` | `agcTarget` | `4000` | Target RMS (0–32767) |
| `agc_max_gain` | `agcMaxGain` | `8.0` | Maximum gain multiplier |
| `agc_attack` | `agcAttack` | `0.1` | Attack coefficient (higher = faster) |
| `agc_release` | `agcRelease` | `0.02` | Release coefficient (lower = slower) |

---

*Document version: 3.0 — 2026, EA7HQL*
