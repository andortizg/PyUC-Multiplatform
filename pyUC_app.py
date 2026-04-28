#!/usr/bin/python3
"""
pyUC_app.py  —  Application controller + entry point.
Wires USRPCore ↔ services ↔ UI via a thread-safe IPC queue.
"""

# ── ALSA / JACK / PortAudio noise and crash prevention ────────────────────────
# Must run BEFORE any other import.
#
# Problem: PortAudio calls Pa_Initialize() on every pyaudio.PyAudio() creation.
# Each init probes the JACK backend; on RPi Trixie with jackd installed but not
# running, this can corrupt memory → segfault.  There are multiple PyAudio()
# calls in this project (pre-init, list_audio_devices, USRPCore.start, UI).
#
# Solution:
#   1. Env vars   — tell JACK not to start and PortAudio to use plughw.
#   2. ALSA null handler via libasound — silences all ALSA lib messages forever.
#   3. Preload libjack — stabilises the library before PortAudio touches it.
#   4. Monkey-patch pyaudio.PyAudio — wraps __init__ so EVERY instantiation
#      redirects fd 2 → /dev/null for the duration of Pa_Initialize(),
#      suppressing JACK stderr and preventing the stack corruption.
#
# If JACK is not needed on this system, the cleanest fix is:
#   sudo apt remove --purge libjack-jackd2-0 jackd2 && sudo apt autoremove
import os  as _os
import sys as _sys

if _sys.platform.startswith('linux'):

    # 1. Environment variables (must be set before libjack loads)
    _os.environ['JACK_NO_AUDIO_RESERVATION'] = '1'
    _os.environ['JACK_START_SERVER']         = '0'
    _os.environ['PA_ALSA_PLUGHW']            = '1'

    import ctypes as _ct

    # 2. ALSA null error handler — silences all libasound messages process-wide.
    #    Must keep a reference so the ctypes closure is not garbage-collected.
    _null_alsa_handler = None
    for _alsalib in ('libasound.so.2', 'libasound.so'):
        try:
            _libasound = _ct.CDLL(_alsalib)
            _ALSA_H = _ct.CFUNCTYPE(None, _ct.c_char_p, _ct.c_int,
                                    _ct.c_char_p, _ct.c_int, _ct.c_char_p)
            _null_alsa_handler = _ALSA_H(lambda *_: None)
            _libasound.snd_lib_error_set_handler(_null_alsa_handler)
            break
        except Exception:
            pass

    # 3. Preload libjack before PortAudio does
    for _jacklib in ('libjack.so.0', 'libjack.so', 'libjack.so.1'):
        try:
            _ct.CDLL(_jacklib)
            break
        except OSError:
            pass

    # 4. Monkey-patch pyaudio.PyAudio so every instantiation is fd-redirected.
    #    This covers: our probe, _list_audio(), list_audio_devices(), core.start().
    import pyaudio as _pyaudio_mod

    _OrigPyAudio = _pyaudio_mod.PyAudio

    class _SilentPyAudio(_OrigPyAudio):
        """PyAudio subclass that suppresses JACK/ALSA stderr during Pa_Initialize."""
        def __init__(self, *args, **kwargs):
            _fd2 = None
            try:
                _dn = _os.open('/dev/null', _os.O_WRONLY)
                _fd2 = _os.dup(2)
                _os.dup2(_dn, 2)
                _os.close(_dn)
            except Exception:
                pass
            try:
                super().__init__(*args, **kwargs)
            finally:
                if _fd2 is not None:
                    try:
                        _os.dup2(_fd2, 2)
                        _os.close(_fd2)
                    except Exception:
                        pass

    _pyaudio_mod.PyAudio = _SilentPyAudio

# ─────────────────────────────────────────────────────────────────────────────

import logging
import queue
import sys
import threading
from pathlib import Path

from pyUC_config   import AppConfig, load_config, save_config, UC_VERSION
from pyUC_core     import USRPCore
from pyUC_services import (
    UserDB, HamQTHSession, QRZPhotoFetcher,
    PiStarUpdater, SysMonitor, GPIOPtt, CallsignWorker,
)
from pyUC_ui_base  import UIAdapter

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)


class USRPApp:
    """
    Application controller.
    Owns the core, all services, and a reference to the UI adapter.
    The IPC queue bridges background threads to the UI main thread.
    """

    def __init__(self, cfg: AppConfig, ui: UIAdapter):
        """
        :param cfg: loaded AppConfig instance
        :param ui:  UIAdapter implementation
        """
        self.cfg  = cfg
        self.ui   = ui
        self._ipc: queue.Queue = queue.Queue()
        self._done = False

        # USRP core (duck-typed: AppConfig is CoreConfig-compatible)
        self.core = USRPCore(cfg)

        # Background services
        self._user_db   = UserDB()
        self._hamqth    = HamQTHSession(cfg.ham_user, cfg.ham_pass)
        from pyUC_ui_ctk import Layout as _Layout
        _lay = _Layout(cfg.screen_profile)
        self._qrz = QRZPhotoFetcher(_lay.qrz_w, _lay.qrz_h)
        self._pistar    = PiStarUpdater()
        self._sysmon    = SysMonitor()
        self._gpio      = GPIOPtt(cfg.gpio_ptt_pin, cfg.gpio_ptt_active_low)
        self._cs_worker = CallsignWorker(
            self._user_db, self._hamqth, self._qrz, cfg.use_qrz
        )

        self._ini_path  = ""
        self._register_core_callbacks()

    # ── Public API (called by the UI) ─────────────────────────────────────────

    def connect(self, dial: str, name: str):
        """
        Connects to a talk group.
        :param dial: dial string (TG number, YSF address, *macro)
        :param name: friendly display name for status bar
        """
        self._ipc.put(("conn", name))
        self.core.connect(dial, name)

    def disconnect(self):
        """Disconnects from the current talk group."""
        self.core.disconnect_tg()

    def set_ptt(self, state: bool):
        """
        Sets PTT state directly.
        :param state: True = start transmitting
        """
        self.core.set_ptt(state)

    def toggle_ptt(self):
        """Toggles PTT between TX and idle."""
        self.core.toggle_ptt()

    def set_mode(self, mode: str):
        """
        Switches protocol mode.
        :param mode: e.g. 'DMR', 'YSF', 'P25', 'NXDN', 'DSTAR'
        """
        self.core.set_mode(mode)
        threading.Timer(1.0, self.core.request_info).start()

    def update_pistar(self, modes=None):
        """
        Triggers a background refresh of Pi-Star host lists + user.csv.
        :param modes: list of mode keys to update, or None for all
        """
        self._ipc.put(("toast", "Update", "Downloading reflectors & user.csv…"))
        self._pistar.update(
            self.cfg.pistar_hosts,
            self.core.talk_groups,
            modes,
            on_done=lambda updated: self._ipc.put((
                "toast", "Pi-Star",
                "Updated: " + ", ".join(updated) if updated else "Nothing updated",
            )),
        )
        self._user_db.download(
            self.cfg.user_csv_url, self.cfg.user_csv_file,
            on_done=lambda ok: self._ipc.put((
                "toast", "Database",
                f"{self.cfg.user_csv_file} updated" if ok else "CSV update failed",
            )),
        )

    def save_settings(self, ini_path: str) -> bool:
        """
        Persists current AppConfig to the ini file.
        :param ini_path: path to pyUC.ini
        :return: True on success
        """
        return save_config(ini_path, self.cfg)

    # ── Pump (called from UI main thread every ~100 ms) ───────────────────────

    def pump(self):
        """
        Drains the IPC queue and dispatches events to the UI.
        Must be called periodically from the UI's main thread (via after()).
        """
        ui  = self.ui
        clr = self.cfg.colors
        try:
            while True:
                msg = self._ipc.get_nowait()
                k   = msg[0]

                if k == "reg":
                    ui.show_registered()
                    ui.show_status("Connected", clr.greenColor)
                    ui.show_status("REG OK", clr.accent2Color, temporary=True)

                elif k == "unreg":
                    ui.show_unregistered()
                    ui.show_status("Disconnected", clr.redColor)

                elif k == "rx_begin":
                    _, call, tg, slot, mode, name = msg
                    ui.show_rx_begin(call, tg, slot, mode, name)
                    self._cs_worker.lookup(call, name)

                elif k == "rx_end":
                    _, call, tg, loss, dur, *_ = msg   # core: (call, tg, loss, duration, start_time)
                    ui.show_rx_end(call, tg, loss, dur)

                elif k == "ptt_ui":
                    # ptt_change from core (e.g. VOX) — UI update only
                    ui.show_ptt(msg[1])

                elif k == "ptt":
                    # GPIO-originated PTT: drive the core AND update the UI
                    self.set_ptt(msg[1])
                    ui.show_ptt(msg[1])

                elif k == "conn":
                    ui.show_connected(msg[1])

                elif k == "disc":
                    ui.show_disconnected()
                    ui.show_status("Disconnected", clr.redColor)

                elif k == "mode_ch":
                    _, new_mode, last_tg = msg
                    ui.show_mode(new_mode, last_tg)
                    ui.show_status("Connected", clr.greenColor)

                elif k == "tg_add":
                    _, mode, tg_name, tg_val = msg
                    ui.show_tg_added(mode, tg_name, tg_val)

                elif k == "tx_en":
                    ui.show_transmit_enable(msg[1])

                elif k == "photo":
                    _, cs, photo, name, grid, city = msg
                    ui.show_photo(cs, photo, name, grid, city)

                elif k == "sys":
                    _, cpu, ram, temp = msg
                    ui.show_sysmon(cpu, ram, temp)

                elif k == "toast":
                    ui.show_toast(msg[1], msg[2])

                elif k == "status_msg":
                    ui.show_status(msg[1], clr.accent2Color, temporary=True)

                elif k == "error":
                    ui.show_toast("Error", msg[1])

                elif k == "ab_exit":
                    ui.show_toast("AB", f"Analog Bridge exiting in {msg[1]}s")

        except queue.Empty:
            pass

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, ini_path: str):
        """
        Starts all services and the USRP core.
        :param ini_path: path to pyUC.ini (stored for save_settings)
        """
        self._ini_path = ini_path
        csv = self.cfg.user_csv_file

        # user.csv — load existing or kick off a first-time download
        if Path(csv).exists():
            threading.Thread(
                target=self._user_db.load, args=(csv,),
                daemon=True, name="userdb_load",
            ).start()
        else:
            self._ipc.put(("toast", "Database", "Downloading user.csv for the first time…"))
            self._user_db.download(
                self.cfg.user_csv_url, csv,
                on_done=lambda ok: self._ipc.put((
                    "toast", "Database",
                    "user.csv ready" if ok else "user.csv download failed",
                )),
            )

        # Callsign resolution worker
        self._cs_worker.start(
            callback=lambda cs, photo, name, grid, city:
                self._ipc.put(("photo", cs, photo, name, grid, city))
        )

        # Own-data display: look up operator's own callsign immediately
        if getattr(self.cfg, 'show_own_data', True):
            self._cs_worker.lookup(self.cfg.my_call, '')

        # System monitor
        self._sysmon.start(
            callback=lambda cpu, ram, temp: self._ipc.put(("sys", cpu, ram, temp))
        )

        # GPIO PTT
        self._gpio.start(
            callback=lambda pressed: self._ipc.put(("ptt", pressed))
        )

        # USRP core (UDP sockets + audio threads + registers with AB)
        self.core.start()

    def stop(self):
        """Shuts down core and all services cleanly."""
        if self._done:
            return
        self._done = True
        self._sysmon.stop()
        self._cs_worker.stop()
        self._gpio.stop()
        self.core.stop()

    # ── Core event → IPC queue ────────────────────────────────────────────────

    def _register_core_callbacks(self):
        """Maps all USRPCore events to IPC queue tuples."""
        c = self.core
        q = self._ipc

        c.on("registered",      lambda:        q.put(("reg",)))
        c.on("unregistered",    lambda:        q.put(("unreg",)))
        c.on("rx_begin",        lambda *a:     q.put(("rx_begin",) + a))
        c.on("rx_end",          lambda *a:     q.put(("rx_end",)   + a))
        c.on("ptt_change",      lambda s:      q.put(("ptt_ui", s)))
        c.on("connected",       lambda n:      q.put(("conn", n)))
        c.on("disconnected",    lambda:        q.put(("disc",)))
        c.on("text_message",    lambda t, m:   q.put(("status_msg", m)))
        c.on("mode_change",     lambda m, tg:  q.put(("mode_ch", m, tg)))
        c.on("tg_added",        lambda *a:     q.put(("tg_add",)   + a))
        c.on("transmit_enable", lambda e:      q.put(("tx_en", e)))
        c.on("error",           lambda msg:    q.put(("error", msg)))
        c.on("ab_exiting",      lambda s:      q.put(("ab_exit", s)))

        # audio_level: bypass the queue for minimal meter latency.
        # show_audio_level() is explicitly designed to be called from a
        # background thread (it only writes a plain Python int — GIL-safe).
        c.on("audio_level", lambda lv: self.ui.show_audio_level(lv))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ini = (sys.argv[1] if len(sys.argv) > 1
           else str(Path(sys.argv[0]).parent / "pyUC.ini"))

    cfg = load_config(ini)

    # Import UI here so the rest of the codebase stays free of CTk
    from pyUC_ui_ctk import CtkUI

    ui  = CtkUI(cfg)
    app = USRPApp(cfg, ui)
    ui.app = app        # back-reference: UI can now call app.connect() etc.

    app.start(ini)
    ui.run()            # enters CTk mainloop — blocks until window closes
    app.stop()


if __name__ == "__main__":
    main()
