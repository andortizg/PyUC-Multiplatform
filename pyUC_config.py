#!/usr/bin/python3
"""
pyUC_config.py  —  Unified configuration loader.
Merges all core + UI + service settings into a single AppConfig dataclass.
Provides load_config() and save_config() as the only entry points.
"""

import configparser
import logging
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

UC_VERSION = "1.0 Beta"

_SYS = platform.system()

_PISTAR_BASE = "https://www.pistar.uk/downloads/"
_PISTAR_DEFAULTS: Dict[str, str] = {
    "DMR":  _PISTAR_BASE + "DMR_Hosts.txt",
    "P25":  _PISTAR_BASE + "P25_Hosts.txt",
    "YSF":  _PISTAR_BASE + "YSF_Hosts.txt",
    "NXDN": _PISTAR_BASE + "NXDN_Hosts.txt",
}

_USER_CSV_URL_DEFAULT  = "https://database.radioid.net/static/user.csv"
_USER_CSV_FILE_DEFAULT = "user.csv"

# Sections that are never treated as radio modes
_NON_MODE = frozenset({
    "DEFAULTS", "MACROS", "COLORS", "FAVORITES",
    "PISTAR_HOSTS", "PISTAR_URLS",
})


# ─────────────────────────────────────────────────────────────────────────────
# Color theme dataclass (loaded from [COLORS] section)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ColorTheme:
    """
    All UI colors, keyed to [COLORS] option names in the ini file.
    Falls back to the built-in dark defaults when an option is absent.
    """
    # Backgrounds
    bgColor:             str = "#1c1c1e"
    surfaceColor:        str = "#2c2c2e"
    surface2Color:       str = "#3a3a3c"
    buttonBgColor:       str = "#3a3a3c"
    # Mode selector buttons
    modeBtnBg:           str = "#3a3a3c"
    modeBtnFg:           str = "#ebebf5"
    modeBtnBorder:       str = "#48484a"
    modeBtnActiveBg:     str = "#0a84ff"
    modeBtnActiveFg:     str = "#ffffff"
    modeBtnActiveBorder: str = "#5ac8fa"
    # Text
    textPrimary:         str = "#f2f2f7"
    textSecondary:       str = "#aeaeb2"
    textMuted:           str = "#636366"
    # Accent / status
    accentColor:         str = "#0a84ff"
    accent2Color:        str = "#5ac8fa"
    warnColor:           str = "#ff9f0a"
    greenColor:          str = "#30d158"
    redColor:            str = "#ff453a"
    borderColor:         str = "#48484a"
    # PTT button
    pttIdleBg:           str = "#2c2c2e"
    pttIdleFg:           str = "#5ac8fa"
    pttActiveBg:         str = "#3d0000"
    pttActiveFg:         str = "#ff6961"
    # TG list
    tgSelectedBg:        str = "#0a3660"
    tgSelectedBorder:    str = "#0a84ff"
    tgHoverBg:           str = "#3a3a3c"
    # Tab bar
    tabActiveFg:         str = "#0a84ff"
    tabInactiveFg:       str = "#8e8e93"
    exitTabFg:           str = "#ff453a"
    # Widgets
    entryBgColor:        str = "#1c1c1e"
    meterBgColor:        str = "#1c1c1e"
    checkboxSelectColor: str = "#0a3660"
    # Connect / disconnect buttons
    connectBtnBg:        str = "#0a3660"
    connectBtnHover:     str = "#154a80"
    discBtnBg:           str = "#3d0000"
    discBtnFg:           str = "#ff6961"
    discBtnHover:        str = "#5a0000"
    # Shutdown button
    shutdownBtnBg:       str = "#3d0000"
    shutdownBtnFg:       str = "#ff9f9b"
    shutdownBtnBorder:   str = "#7a2020"
    shutdownBtnHover:    str = "#550000"
    # QRZ photo placeholder
    qrzPhotoBg:          str = "#2c2c2e"
    qrzPhotoFg:          str = "#636366"
    # About page
    aboutCreditsFg:      str = "#8e8e93"


# ─────────────────────────────────────────────────────────────────────────────
# Main config dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AppConfig:
    """
    Complete application configuration (core + UI + services).

    talk_groups:  dict  mode_name → [(display_name, dial_string), ...]
    macros:       dict  dial_string → display_name
    favorites:    dict  display_name → (mode, dial_string)
    pistar_hosts: dict  mode → url
    colors:       ColorTheme instance
    """
    # ── Identity ─────────────────────────────────────────────────────────────
    my_call:              str   = "N0CALL"
    my_locator:           str   = ""
    subscriber_id:        int   = 3112000
    repeater_id:          int   = 311200

    # ── Network ──────────────────────────────────────────────────────────────
    ip_address:           str   = "1.2.3.4"
    usrp_tx_port:         List[int] = field(default_factory=lambda: [50000])
    usrp_rx_port:         int   = 50000

    # ── Radio ─────────────────────────────────────────────────────────────────
    default_server:       str   = "DMR"
    slot:                 int   = 2
    asl_mode:             int   = 0

    # ── Audio ─────────────────────────────────────────────────────────────────
    in_index:             Optional[int] = None   # None = system default, -1 = disabled
    out_index:            Optional[int] = None
    mic_vol:              int   = 50
    spk_vol:              int   = 50
    vox_enable:           bool  = False
    vox_threshold:        int   = 200
    vox_delay:            int   = 50

    # ── AGC ───────────────────────────────────────────────────────────────────
    agc_enable:           bool  = False
    agc_target:           int   = 4000
    agc_max_gain:         float = 8.0
    agc_attack:           float = 0.1
    agc_release:          float = 0.02

    # ── UI / display ──────────────────────────────────────────────────────────
    window_width:         int   = 800
    window_height:        int   = 600
    screen_profile:       str   = "pc"    # 'pc' | 'rpi5' | 'rpi35'
    font_family:          str   = ""      # "" = auto per platform
    dpi_scale:            int   = 0
    spacebar_ptt:         bool  = True
    level_every_sample:   int   = 2
    nat_ping_timer:       int   = 0
    theme_mode:           str   = "dark"   # 'dark' | 'light'
    # log column widths in pixels: Call, Time, TG, Dur, Loss
    log_col_widths:       List[int] = field(default_factory=lambda: [80, 78, 145, 64, 72])
    # QRZ card own-data display
    show_own_data:        bool  = True    # show operator's own data at startup
    own_data_timeout:     int   = 30     # seconds after last RX to revert (0 = never)
    fullscreen:           bool  = False  # start in fullscreen / kiosk mode

    # ── GPIO (Raspberry Pi) ───────────────────────────────────────────────────
    gpio_ptt_pin:         int   = -1      # -1 = disabled
    gpio_ptt_active_low:  bool  = True

    # ── Online lookups ────────────────────────────────────────────────────────
    use_qrz:              bool  = True
    ham_user:             str   = ""
    ham_pass:             str   = ""
    user_csv_url:         str   = _USER_CSV_URL_DEFAULT
    user_csv_file:        str   = _USER_CSV_FILE_DEFAULT

    # ── Pi-Star ───────────────────────────────────────────────────────────────
    pistar_host:          str   = "pi-star.local"
    pistar_auto_update:   bool  = False
    pistar_hosts:         Dict[str, str] = field(default_factory=lambda: dict(_PISTAR_DEFAULTS))

    # ── Data tables ───────────────────────────────────────────────────────────
    talk_groups:  Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    macros:       Dict[str, str]                   = field(default_factory=dict)
    favorites:    Dict[str, Tuple[str, str]]       = field(default_factory=dict)
    colors:       ColorTheme                       = field(default_factory=ColorTheme)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _g(cp: configparser.ConfigParser, section: str, key: str, default, fn=str):
    """
    Safe configparser getter with type conversion.
    :param cp:      ConfigParser instance
    :param section: section name
    :param key:     option key
    :param default: fallback value when key is absent or equals 'Default'
    :param fn:      type conversion callable (int, float, str, …)
    :return: fn(raw_value) or default
    """
    try:
        raw = cp.get(section, key).split(None)[0]
        return default if raw.lower() == "default" else fn(raw)
    except Exception:
        return default


def _detect_profile(w: int, h: int, forced: str) -> str:
    """
    Resolves the layout profile from window dimensions or forced override.
    :param w:      window width from ini
    :param h:      window height from ini
    :param forced: screenProfile value ('' = auto-detect)
    :return: 'pc' | 'rpi5' | 'rpi35'
    """
    if forced in ("pc", "rpi5", "rpi35"):
        return forced
    return "pc" if (w >= 800 and h >= 580) else ("rpi5" if w >= 700 else "rpi35")


def _auto_font_family() -> str:
    """
    Returns the preferred system font for the current platform.
    :return: font family name string
    """
    if _SYS == "Windows": return "Segoe UI"
    if _SYS == "Darwin":  return "SF Pro Text"
    return "DejaVu Sans"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_config(path: str) -> AppConfig:
    """
    Parses a pyUC .ini file and returns a validated AppConfig.
    :param path: filesystem path to the .ini file
    :return: populated AppConfig instance
    :raises SystemExit: on missing / malformed file or unconfigured defaults
    """
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",))
    cp.optionxform = lambda o: o   # preserve key case
    try:
        cp.read(path)
        cfg = AppConfig()
        D = "DEFAULTS"

        # Identity
        cfg.my_call       = _g(cp, D, "myCall",       "N0CALL")
        cfg.my_locator    = _g(cp, D, "loc",           "").strip().upper()
        cfg.subscriber_id = _g(cp, D, "subscriberID",  3112000, int)
        cfg.repeater_id   = _g(cp, D, "repeaterID",    311200,  int)

        # Network
        cfg.ip_address   = _g(cp, D, "ipAddress",  "1.2.3.4")
        raw_tx = cp.get(D, "usrpTxPort") if cp.has_option(D, "usrpTxPort") else "50000"
        cfg.usrp_tx_port = [int(p.strip()) for p in raw_tx.split(",")]
        cfg.usrp_rx_port = _g(cp, D, "usrpRxPort", 50000, int)

        # Radio
        cfg.default_server = _g(cp, D, "defaultServer", "DMR")
        cfg.slot           = _g(cp, D, "slot",           2,   int)
        cfg.asl_mode       = _g(cp, D, "aslMode",        0,   int)

        # Audio
        cfg.in_index      = _g(cp, D, "in_index",  None, int)
        cfg.out_index     = _g(cp, D, "out_index", None, int)
        cfg.mic_vol       = _g(cp, D, "micVol",    50,   int)
        cfg.spk_vol       = _g(cp, D, "spkVol",    50,   int)
        cfg.vox_enable    = bool(_g(cp, D, "voxEnable",    0, int))
        cfg.vox_threshold = _g(cp, D, "voxThreshold", 200, int)
        cfg.vox_delay     = _g(cp, D, "voxDelay",      50, int)

        # AGC
        cfg.agc_enable   = bool(_g(cp, D, "agcEnable",  0,    int))
        cfg.agc_target   = _g(cp, D, "agcTarget",   4000, int)
        cfg.agc_max_gain = _g(cp, D, "agcMaxGain",  8.0,  float)
        cfg.agc_attack   = _g(cp, D, "agcAttack",   0.1,  float)
        cfg.agc_release  = _g(cp, D, "agcRelease",  0.02, float)

        # UI
        cfg.window_width    = _g(cp, D, "windowWidth",       800, int)
        cfg.window_height   = _g(cp, D, "windowHeight",      600, int)
        forced              = _g(cp, D, "screenProfile",     "").strip().lower()
        cfg.screen_profile  = _detect_profile(cfg.window_width, cfg.window_height, forced)
        ff                  = _g(cp, D, "fontFamily", "").strip()
        cfg.font_family     = ff if ff else _auto_font_family()
        cfg.dpi_scale       = _g(cp, D, "dpiScale",         0,   int)
        cfg.spacebar_ptt    = bool(_g(cp, D, "spacebarPtt", 1,   int))
        cfg.level_every_sample = _g(cp, D, "levelEverySample", 2, int)
        cfg.nat_ping_timer  = _g(cp, D, "pingTimer",         0,  int)
        cfg.theme_mode      = _g(cp, D, "themeMode",     "dark").strip().lower()
        try:
            raw = _g(cp, D, "logColWidths", "")
            cfg.log_col_widths = [int(x) for x in raw.split(",") if x.strip()] or [80, 78, 145, 64, 72]
        except Exception:
            cfg.log_col_widths = [80, 78, 145, 64, 72]
        cfg.show_own_data    = bool(_g(cp, D, "showOwnData",    1, int))
        cfg.own_data_timeout = _g(cp, D, "ownDataTimeout", 30, int)
        cfg.fullscreen       = bool(_g(cp, D, "fullscreen",     0, int))

        # GPIO
        cfg.gpio_ptt_pin      = _g(cp, D, "gpioPttPin",       -1, int)
        cfg.gpio_ptt_active_low = bool(_g(cp, D, "gpioPttActiveLow", 1, int))

        # Online lookups
        cfg.use_qrz       = bool(_g(cp, D, "useQRZ",      1, int))
        cfg.ham_user      = _g(cp, D, "hamUser",  "").strip()
        cfg.ham_pass      = _g(cp, D, "hamPass",  "").strip()
        cfg.user_csv_url  = _g(cp, D, "userCsvUrl",  _USER_CSV_URL_DEFAULT)
        cfg.user_csv_file = _g(cp, D, "userCsvFile", _USER_CSV_FILE_DEFAULT)

        # Pi-Star
        cfg.pistar_host        = _g(cp, D, "pistarHost",        "pi-star.local")
        cfg.pistar_auto_update = bool(_g(cp, D, "pistarAutoUpdate", 0, int))
        hosts = dict(_PISTAR_DEFAULTS)
        if cp.has_section("PISTAR_HOSTS"):
            for k, v in cp.items("PISTAR_HOSTS"):
                hosts[k.upper()] = v.strip().split()[0]
        cfg.pistar_hosts = hosts

        # Talk groups — every section that is not a reserved name
        for sect in cp.sections():
            u = sect.upper()
            if u not in _NON_MODE and not u.startswith("FAV_"):
                cfg.talk_groups[sect] = [
                    (name, val.strip().strip('"\'').split()[0])
                    for name, val in cp.items(sect)
                ]

        # Macros  dial_string → display_name
        if cp.has_section("MACROS"):
            for k, v in cp.items("MACROS"):
                cfg.macros[v.strip()] = k.strip()

        # Favorites: new [FAV_MODE] sections + legacy [FAVORITES]
        for sect in cp.sections():
            if sect.upper().startswith("FAV_"):
                mode = sect[4:].upper()
                for name, dial in cp.items(sect):
                    cfg.favorites[name.strip()] = (
                        mode, dial.strip().strip('"\'').split()[0])
        if cp.has_section("FAVORITES"):
            for n, v in cp.items("FAVORITES"):
                parts = v.split(",")
                if len(parts) == 2:
                    cfg.favorites[n.strip()] = (
                        parts[0].strip(), parts[1].strip().strip('"\''))

        # Colors — update only keys that exist in [COLORS]
        ct = ColorTheme()
        if cp.has_section("COLORS"):
            for k in ct.__dataclass_fields__:
                if cp.has_option("COLORS", k):
                    setattr(ct, k, cp.get("COLORS", k).split()[0])
        cfg.colors = ct

    except Exception as exc:
        logging.error("Config file error: %s", exc)
        sys.exit(f"Configuration file '{path}' is not valid: {exc}")

    # Sanity check: still-default critical values indicate the ini was never edited
    if (cfg.my_call == "N0CALL" or
            cfg.subscriber_id == 3112000 or
            cfg.ip_address == "1.2.3.4"):
        logging.error("Please edit %s: set myCall, subscriberID and ipAddress.", path)
        sys.exit(1)

    return cfg


def save_config(path: str, cfg: AppConfig) -> bool:
    """
    Persists mutable AppConfig fields back to the ini file.
    Preserves all existing sections and keys that are not explicitly updated.
    :param path: filesystem path to the ini file
    :param cfg:  current AppConfig instance
    :return: True on success, False on error
    """
    try:
        cp = configparser.ConfigParser(inline_comment_prefixes=(";",))
        cp.optionxform = lambda o: o
        cp.read(path)
        D = "DEFAULTS"
        if not cp.has_section(D):
            cp.add_section(D)

        fields = {
            "myCall":            cfg.my_call,
            "ipAddress":         cfg.ip_address,
            "usrpTxPort":        ",".join(str(p) for p in cfg.usrp_tx_port),
            "usrpRxPort":        str(cfg.usrp_rx_port),
            "subscriberID":      str(cfg.subscriber_id),
            "repeaterID":        str(cfg.repeater_id),
            "defaultServer":     cfg.default_server,
            "slot":              str(cfg.slot),
            "micVol":            str(cfg.mic_vol),
            "spkVol":            str(cfg.spk_vol),
            "voxEnable":         "1" if cfg.vox_enable else "0",
            "voxThreshold":      str(cfg.vox_threshold),
            "voxDelay":          str(cfg.vox_delay),
            "agcEnable":         "1" if cfg.agc_enable else "0",
            "gpioPttPin":        str(cfg.gpio_ptt_pin),
            "gpioPttActiveLow":  "1" if cfg.gpio_ptt_active_low else "0",
            "spacebarPtt":       "1" if cfg.spacebar_ptt else "0",
            "pistarHost":        cfg.pistar_host,
            "hamUser":           cfg.ham_user,
            "hamPass":           cfg.ham_pass,
            "themeMode":         cfg.theme_mode,
            "logColWidths":      ",".join(str(w) for w in cfg.log_col_widths),
            "showOwnData":       "1" if cfg.show_own_data else "0",
            "ownDataTimeout":    str(cfg.own_data_timeout),
            "fullscreen":        "1" if cfg.fullscreen else "0",
        }
        for k, v in fields.items():
            cp.set(D, k, v)

        with open(path, "w") as fh:
            cp.write(fh)
        logging.info("Settings saved to %s", path)
        return True

    except Exception as exc:
        logging.error("save_config: %s", exc)
        return False
