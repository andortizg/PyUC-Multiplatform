#!/usr/bin/python3
"""
pyUC_ui_base.py  —  Abstract UIAdapter contract.
Any UI implementation (customtkinter, web, headless, …) must subclass UIAdapter.
All methods documented here are called on the UI's main thread; the controller
ensures this via its IPC queue + scheduled pump.
"""

from abc import ABC, abstractmethod


class UIAdapter(ABC):
    """
    Contract between USRPApp (controller) and any UI implementation.

    Lifecycle:
      1. Instantiate a UIAdapter subclass (builds all widgets).
      2. Set ui.app = app  (back-reference to USRPApp).
      3. Call app.start()  (starts USRP core + services).
      4. Call ui.run()     (enters the UI event loop — blocks until closed).
      5. Call app.stop()   (shuts down threads after run() returns).
    """

    # ── Core protocol events ──────────────────────────────────────────────────

    @abstractmethod
    def show_registered(self):
        """
        Called when Analog Bridge confirms registration (REG:OK).
        Enables the PTT button (unless in_index == -1).
        """

    @abstractmethod
    def show_unregistered(self):
        """Called when Analog Bridge sends UNREG. Disables the PTT button."""

    @abstractmethod
    def show_rx_begin(self, call: str, tg: str, slot: str, mode: str, name: str):
        """
        Called when a remote station starts transmitting.
        :param call: callsign or DMR ID string
        :param tg:   talk group friendly name or number string
        :param slot: DMR time slot ('1' or '2')
        :param mode: call type ('Group' or 'Private')
        :param name: operator name from USRP metadata (may be empty)
        """

    @abstractmethod
    def show_rx_end(self, call: str, tg: str, loss: str, duration: float):
        """
        Called when the remote transmission ends.
        :param call:     callsign
        :param tg:       talk group name
        :param loss:     packet loss percentage string (e.g. '1.25%')
        :param duration: transmission duration in seconds
        """

    @abstractmethod
    def show_ptt(self, state: bool):
        """
        Called when PTT state changes (VOX, hardware GPIO, or spacebar).
        :param state: True = now transmitting, False = idle
        """

    @abstractmethod
    def show_mode(self, mode: str, last_tg: str):
        """
        Called when Analog Bridge confirms a mode change.
        :param mode:    new active mode string (e.g. 'DMR', 'YSF', 'P25')
        :param last_tg: dial string of the last connected TG (may be empty)
        """

    @abstractmethod
    def show_connected(self, tg_name: str):
        """
        Called immediately when the app connects to a talk group (before AB confirms).
        :param tg_name: friendly TG display name
        """

    @abstractmethod
    def show_disconnected(self):
        """Called when the app disconnects from a talk group."""

    @abstractmethod
    def show_photo(self, call: str, photo, name: str, grid: str, city: str):
        """
        Called when callsign metadata has been fully resolved.
        :param call:  callsign string
        :param photo: PIL.Image instance, or None
        :param name:  operator full name
        :param grid:  Maidenhead locator (e.g. 'IM76sp'), or ''
        :param city:  city / country string, or ''
        """

    @abstractmethod
    def show_status(self, text: str, color: str, temporary: bool = False):
        """
        Updates the connection status bar.
        :param text:      status text to display
        :param color:     CSS hex color string for the text
        :param temporary: True → revert to the previous status after ~3 s
        """

    @abstractmethod
    def show_toast(self, title: str, message: str):
        """
        Shows a brief non-blocking notification overlay.
        :param title:   short title (e.g. 'Error', 'Pi-Star')
        :param message: notification body text
        """

    @abstractmethod
    def show_tg_added(self, mode: str, tg_name: str, tg_value: str):
        """
        Called when a TG is dynamically added (e.g. incoming private call).
        :param mode:     radio mode ('DMR', etc.)
        :param tg_name:  display name for the new TG
        :param tg_value: dial string for the new TG
        """

    @abstractmethod
    def show_audio_level(self, level: int):
        """
        Called at audio rate (~25–50×/s) with the RX audio level.
        IMPORTANT: fired from the audio thread — must be extremely fast and
        must NOT touch any UI widget directly. Store to an attribute; update
        the widget in the pump.
        :param level: 0–100 integer RMS level
        """

    @abstractmethod
    def show_sysmon(self, cpu: float, ram: float, temp: str):
        """
        Called every ~5 s with system resource statistics.
        :param cpu:  CPU usage percentage (0–100)
        :param ram:  RAM usage percentage (0–100)
        :param temp: temperature string (e.g. '52°C') or '—'
        """

    @abstractmethod
    def show_transmit_enable(self, enabled: bool):
        """
        Enables or disables the PTT button.
        Disabled while a remote station is transmitting to prevent collision.
        :param enabled: True = PTT available
        """

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    def run(self):
        """
        Enters the UI event loop.
        This call blocks until the user closes the window.
        The pump that drains the app IPC queue must be scheduled here
        (e.g. via root.after(100, self._pump)).
        """
