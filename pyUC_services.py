#!/usr/bin/python3
"""
pyUC_services.py  —  Non-UI background services.
All classes are framework-independent and thread-safe.
No tkinter or customtkinter imports anywhere in this file.
"""

import csv
import logging
import math
import queue
import re
import threading
from pathlib import Path
from time import sleep
from typing import Callable, Dict, List, Optional, Tuple

# ── Optional dependencies — graceful degradation ─────────────────────────────
try:
    import requests
    from urllib.request import urlopen
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False
    logging.warning("requests not installed — QRZ and Pi-Star updates disabled")

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# Maidenhead locator utilities (pure math, no deps)
# ─────────────────────────────────────────────────────────────────────────────

def locator_from_string(text: str) -> Optional[str]:
    """
    Extracts a Maidenhead locator (e.g. IM76sp) from arbitrary text.
    :param text: text to search
    :return: uppercase 6-char locator, or None if not found
    """
    if not text:
        return None
    m = re.search(r"([A-R]{2}[0-9]{2}[a-z]{2})", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def locator_to_latlon(locator: str) -> Optional[Tuple[float, float]]:
    """
    Converts a Maidenhead grid locator to (lat, lon) decimal degrees.
    :param locator: 4- or 6-char Maidenhead string (e.g. 'IM76sp')
    :return: (latitude, longitude) tuple, or None on parse error
    """
    try:
        loc = locator.strip().upper()
        lon = (ord(loc[0]) - ord("A")) * 20 - 180
        lat = (ord(loc[1]) - ord("A")) * 10 - 90
        lon += (ord(loc[2]) - ord("0")) * 2
        lat += (ord(loc[3]) - ord("0")) * 1
        if len(loc) >= 6:
            lon += (ord(loc[4]) - ord("A")) * (2 / 24) + (1 / 24)
            lat += (ord(loc[5]) - ord("A")) * (1 / 24) + (0.5 / 24)
        return lat, lon
    except Exception:
        return None


def calc_distance_km(loc1: str, loc2: str) -> Optional[float]:
    """
    Computes great-circle distance between two Maidenhead locators.
    :param loc1: first locator (e.g. 'IM76sp')
    :param loc2: second locator
    :return: distance in km, or None if either locator is invalid
    """
    c1 = locator_to_latlon(loc1)
    c2 = locator_to_latlon(loc2)
    if not c1 or not c2:
        return None
    lat1, lon1 = map(math.radians, c1)
    lat2, lon2 = map(math.radians, c2)
    d = 2 * 6371 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) *
        math.sin((lon2 - lon1) / 2) ** 2
    ))
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Local RadioID.net user database
# ─────────────────────────────────────────────────────────────────────────────

class UserDB:
    """
    In-memory O(1) lookup index of RadioID.net user.csv.
    Expected columns: id, callsign, name, city, state, country, …
    Thread-safe for concurrent reads and writes.
    """

    def __init__(self):
        self._db:   Dict[str, dict] = {}
        self._lock  = threading.Lock()

    def load(self, csv_file: str) -> int:
        """
        Loads (or hot-reloads) the CSV file into memory.
        :param csv_file: path to user.csv
        :return: number of records loaded, 0 on error or missing file
        """
        if not Path(csv_file).exists():
            logging.warning("UserDB: %s not found", csv_file)
            return 0
        db: Dict[str, dict] = {}
        try:
            with open(csv_file, encoding="utf-8", errors="replace") as fh:
                for row in csv.reader(fh):
                    if len(row) > 5:
                        cs = row[1].strip().upper()
                        if cs:
                            db[cs] = {
                                "name": row[2].strip(),
                                "city": ", ".join(
                                    p for p in (row[3].strip(), row[5].strip()) if p
                                ),
                            }
            with self._lock:
                self._db = db
            logging.info("UserDB: loaded %d entries from %s", len(db), csv_file)
            return len(db)
        except Exception as exc:
            logging.error("UserDB.load: %s", exc)
            return 0

    def lookup(self, callsign: str) -> Optional[dict]:
        """
        Looks up a callsign (case-insensitive).
        :param callsign: amateur radio callsign
        :return: {'name': str, 'city': str} or None
        """
        with self._lock:
            return self._db.get(callsign.strip().upper())

    def download(self, url: str, csv_file: str,
                 on_done: Optional[Callable] = None) -> threading.Thread:
        """
        Downloads user.csv in a background thread using streaming chunks.
        :param url:      source URL
        :param csv_file: destination path
        :param on_done:  optional callback(success: bool) fired when done
        :return: the started Thread
        """
        def _work():
            if not REQUESTS_OK:
                if on_done: on_done(False)
                return
            try:
                logging.info("UserDB: downloading %s → %s", url, csv_file)
                r = requests.get(url, timeout=60, stream=True)
                r.raise_for_status()
                with open(csv_file, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
                self.load(csv_file)
                if on_done: on_done(True)
            except Exception as exc:
                logging.error("UserDB.download: %s", exc)
                if on_done: on_done(False)

        t = threading.Thread(target=_work, daemon=True, name="userdb_dl")
        t.start()
        return t


# ─────────────────────────────────────────────────────────────────────────────
# HamQTH locator lookup
# ─────────────────────────────────────────────────────────────────────────────

class HamQTHSession:
    """
    Reusable HamQTH XML API session for Maidenhead grid lookups.
    Handles login and automatic session re-authentication transparently.
    Thread-safe (single internal lock per session).
    """

    _BASE = "https://www.hamqth.com/xml.php"

    def __init__(self, username: str, password: str):
        """
        :param username: HamQTH login name
        :param password: HamQTH password (may be empty → lookups disabled)
        """
        self._user = username
        self._pass = password
        self._sid  = ""
        self._lock = threading.Lock()
        self._last_ok = False   # True after a successful lookup

    @property
    def status(self) -> str:
        """
        Returns the current authentication status string.
        :return: 'disabled' | 'connected' | 'error'
        """
        if not self._user or not self._pass:
            return 'disabled'
        return 'connected' if self._last_ok else 'error'

    def get_locator(self, callsign: str) -> str:
        """
        Returns the Maidenhead grid locator for a callsign, or '' on failure.
        :param callsign: amateur radio callsign
        :return: 6-char Maidenhead string or empty string
        """
        if not self._user or not self._pass or not REQUESTS_OK:
            return ""
        with self._lock:
            info = self._fetch(callsign)
            return info.get('grid', '')

    def get_info(self, callsign: str) -> dict:
        """
        Returns a dict with 'grid', 'city' and 'name' from HamQTH, or empty values.
        :param callsign: amateur radio callsign
        :return: {'grid': str, 'city': str, 'name': str}
        """
        if not self._user or not self._pass or not REQUESTS_OK:
            return {'grid': '', 'city': '', 'name': ''}
        with self._lock:
            return self._fetch(callsign)

    def _login(self) -> bool:
        """Authenticates and stores the session id. Returns True on success."""
        try:
            resp = urlopen(
                f"{self._BASE}?u={self._user}&p={self._pass}", timeout=5
            ).read().decode("utf-8")
            m = re.search(r"<session_id>(.*?)</session_id>", resp)
            if m:
                self._sid = m.group(1)
                return True
        except Exception as exc:
            logging.warning("HamQTH login: %s", exc)
        self._sid = ""
        return False

    def _fetch(self, callsign: str) -> dict:
        """
        Internal fetch; assumes lock is held.
        :return: dict with 'grid', 'city', 'name' (empty strings on failure)
        """
        empty = {'grid': '', 'city': '', 'name': ''}
        if not self._sid and not self._login():
            self._last_ok = False
            return empty
        try:
            url  = f"{self._BASE}?id={self._sid}&callsign={callsign}"
            resp = urlopen(url, timeout=5).read().decode("utf-8")

            # Expired session — retry once
            if "<e>" in resp and self._login():
                resp = urlopen(
                    f"{self._BASE}?id={self._sid}&callsign={callsign}", timeout=5
                ).read().decode("utf-8")

            def _tag(tag):
                m = re.search(rf"<{tag}>(.*?)</{tag}>", resp, re.IGNORECASE)
                return m.group(1).strip() if m else ''

            grid    = _tag('grid').upper()
            qth     = _tag('qth')       # city / location
            country = _tag('country')
            name    = _tag('name') or _tag('nick')

            city = ', '.join(filter(None, [qth, country]))

            if grid or city or name:
                self._last_ok = True
            elif self._sid:
                # Auth OK but callsign not found — still counts as connected
                self._last_ok = True

            return {'grid': grid, 'city': city, 'name': name}

        except Exception as exc:
            logging.warning("HamQTH fetch %s: %s", callsign, exc)
            self._sid = ""
            self._last_ok = False
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# QRZ.com photo fetcher
# ─────────────────────────────────────────────────────────────────────────────

class QRZPhotoFetcher:
    """
    Scrapes QRZ.com for operator photos and returns PIL.Image objects (resized).
    Callers must convert to a UI-specific image handle (e.g. ImageTk.PhotoImage).
    Results are cached in-memory for the session lifetime.
    Thread-safe.
    """

    def __init__(self, photo_w: int = 200, photo_h: int = 134):
        """
        :param photo_w: max image width in pixels
        :param photo_h: max image height in pixels
        """
        self._w     = photo_w
        self._h     = photo_h
        self._cache: Dict[str, object] = {}   # callsign → PIL.Image or None
        self._lock  = threading.Lock()

    def fetch(self, callsign: str) -> Optional[object]:
        """
        Returns a resized PIL.Image for callsign, or None.
        :param callsign: amateur radio callsign (case-insensitive)
        :return: PIL.Image instance, or None if unavailable
        """
        if not PIL_OK or not REQUESTS_OK or not BS4_OK:
            return None
        cs = callsign.strip().upper()
        with self._lock:
            if cs in self._cache:
                return self._cache[cs]
        photo = None
        try:
            page    = urlopen("https://qrz.com/lookup/" + cs, timeout=10).read()
            soup    = BeautifulSoup(page, "html.parser")
            img_tag = soup.find(id="mypic")
            if img_tag:
                raw = requests.get(img_tag["src"], stream=True, timeout=10)
                img = Image.open(raw.raw)
                img.thumbnail((self._w, self._h), Image.LANCZOS)
                photo = img
        except Exception as exc:
            logging.warning("QRZ photo %s: %s", cs, exc)
        with self._lock:
            self._cache[cs] = photo
        return photo

    def clear_cache(self):
        """Empties the in-memory photo cache."""
        with self._lock:
            self._cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Pi-Star reflector list updater
# ─────────────────────────────────────────────────────────────────────────────

class PiStarUpdater:
    """
    Downloads reflector host lists from pistar.uk into a talk_groups dict.
    Runs in a daemon thread; notifies caller via callback when done.
    """

    _YSF_MODES = frozenset({"YSF"})

    def update(self,
               host_urls: Dict[str, str],
               talk_groups: Dict[str, list],
               modes: Optional[List[str]] = None,
               on_done: Optional[Callable] = None) -> threading.Thread:
        """
        Downloads host files and updates talk_groups in-place.
        :param host_urls:   dict mode→url from [PISTAR_HOSTS]
        :param talk_groups: the talk_groups dict (modified in-place, thread-safe ref)
        :param modes:       list of mode keys to update, or None for all
        :param on_done:     optional callback(updated_modes: list)
        :return: started Thread
        """
        def _work():
            if not REQUESTS_OK:
                if on_done: on_done([])
                return
            updated: List[str] = []
            for mode, url in host_urls.items():
                if modes and mode not in modes:
                    continue
                try:
                    resp = requests.get(url, timeout=12)
                    resp.raise_for_status()
                    new: List[Tuple[str, str]] = []
                    for line in resp.text.splitlines():
                        line = line.strip()
                        if not line or line[0] in "#;":
                            continue
                        parts = line.split()
                        if len(parts) < 2:
                            continue
                        name = parts[0]
                        dial = (f"{parts[1]}:{parts[2]}"
                                if mode in self._YSF_MODES and len(parts) >= 3
                                else parts[1])
                        new.append((name, dial))
                    if new:
                        dis = talk_groups.get(mode, [("Disconnect", "0")])[:1]
                        talk_groups[mode] = dis + new
                        updated.append(mode)
                except Exception as exc:
                    logging.warning("PiStar %s: %s", mode, exc)
            if on_done:
                on_done(updated)

        t = threading.Thread(target=_work, daemon=True, name="pistar_dl")
        t.start()
        return t


# ─────────────────────────────────────────────────────────────────────────────
# System resource monitor
# ─────────────────────────────────────────────────────────────────────────────

class SysMonitor:
    """
    Polls CPU%, RAM% and CPU temperature every ~5 s.
    Fires a callback with the results; uses psutil when available.
    """

    def __init__(self, interval: float = 5.0):
        """
        :param interval: polling interval in seconds
        """
        self._interval = interval
        self._done     = False

    def start(self, callback: Callable) -> Optional[threading.Thread]:
        """
        Starts the monitor in a background daemon thread.
        :param callback: callable(cpu: float, ram: float, temp: str)
        :return: started Thread, or None if psutil is unavailable
        """
        if not PSUTIL_OK:
            return None
        self._done = False
        t = threading.Thread(target=self._run, args=(callback,),
                             daemon=True, name="sysmon")
        t.start()
        return t

    def stop(self):
        """Signals the monitor thread to exit."""
        self._done = True

    def _run(self, cb: Callable):
        while not self._done:
            try:
                cpu  = psutil.cpu_percent(interval=2)
                if self._done:
                    return
                ram  = psutil.virtual_memory().percent
                temp = self._read_temp()
                cb(cpu, ram, temp)
            except Exception:
                pass
            # Interruptible sleep: check _done every 100 ms
            for _ in range(int(self._interval / 0.1)):
                if self._done:
                    return
                sleep(0.1)

    @staticmethod
    def _read_temp() -> str:
        """
        Reads CPU temperature from /sys (RPi) or psutil sensors.
        :return: temperature string like '52°C' or '—'
        """
        try:
            raw = open("/sys/class/thermal/thermal_zone0/temp").read()
            return f"{int(raw.strip()) // 1000}°C"
        except Exception:
            pass
        try:
            t    = psutil.sensors_temperatures()
            vals = (t.get("coretemp") or t.get("cpu_thermal")
                    or t.get("acpitz") or next(iter(t.values()), None))
            if vals:
                return f"{vals[0].current:.0f}°C"
        except Exception:
            pass
        return "—"


# ─────────────────────────────────────────────────────────────────────────────
# GPIO PTT (Raspberry Pi only)
# ─────────────────────────────────────────────────────────────────────────────

class GPIOPtt:
    """
    Polls a BCM GPIO pin and fires a callback on edge transitions.
    Silently disabled on non-RPi platforms (ImportError on RPi.GPIO).
    """

    def __init__(self, pin: int, active_low: bool = True):
        """
        :param pin:        BCM pin number (-1 = disabled)
        :param active_low: True if PTT pulls line LOW when pressed (uses pull-up)
        """
        self._pin        = pin
        self._active_low = active_low
        self._done       = False

    def start(self, callback: Callable) -> Optional[threading.Thread]:
        """
        Starts the GPIO polling thread.
        :param callback: callable(pressed: bool)
        :return: started Thread, or None if pin < 0 or RPi.GPIO unavailable
        """
        if self._pin < 0:
            return None
        self._done = False
        t = threading.Thread(target=self._run, args=(callback,),
                             daemon=True, name="gpio_ptt")
        t.start()
        return t

    def stop(self):
        """Signals the GPIO thread to exit."""
        self._done = True

    def _run(self, cb: Callable):
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._pin, GPIO.IN,
                       pull_up_down=GPIO.PUD_UP if self._active_low else GPIO.PUD_DOWN)
            last = GPIO.input(self._pin)
            while not self._done:
                st = GPIO.input(self._pin)
                if st != last:
                    pressed = (st == GPIO.LOW) if self._active_low else (st == GPIO.HIGH)
                    cb(pressed)
                    last = st
                sleep(0.02)
            GPIO.cleanup(self._pin)
        except ImportError:
            logging.warning("RPi.GPIO not available — GPIO PTT disabled")
        except Exception as exc:
            logging.error("GPIOPtt: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Composite callsign resolution worker
# ─────────────────────────────────────────────────────────────────────────────

class CallsignWorker:
    """
    Background worker that resolves name/city (UserDB), Maidenhead grid
    (HamQTH) and QRZ photo for each callsign it receives.

    Deduplicates in-flight requests: if the same callsign is enqueued while
    already being resolved, the duplicate is silently dropped.

    Results are delivered via: callback(call, photo, name, grid, city)
    """

    def __init__(self,
                 user_db:  UserDB,
                 hamqth:   HamQTHSession,
                 qrz:      QRZPhotoFetcher,
                 use_qrz:  bool = True):
        """
        :param user_db:  UserDB instance
        :param hamqth:   HamQTHSession instance
        :param qrz:      QRZPhotoFetcher instance
        :param use_qrz:  False → skip QRZ photo lookup
        """
        self._db      = user_db
        self._hamqth  = hamqth
        self._qrz     = qrz
        self._use_qrz = use_qrz
        self._q:      queue.Queue = queue.Queue()
        self._inflight: set = set()
        self._ifl_lock = threading.Lock()
        self._done     = False

    def start(self, callback: Callable) -> threading.Thread:
        """
        Starts the worker thread.
        :param callback: callable(call, photo, name, grid, city)
        :return: started Thread
        """
        self._done = False
        t = threading.Thread(target=self._run, args=(callback,),
                             daemon=True, name="callsign_worker")
        t.start()
        return t

    def lookup(self, callsign: str, name_meta: str = ""):
        """
        Enqueues a lookup request. Duplicates are silently dropped.
        :param callsign:  callsign to resolve
        :param name_meta: name hint from USRP metadata
        """
        if not callsign:
            return
        cs = callsign.strip().upper()
        with self._ifl_lock:
            if cs in self._inflight:
                return
            self._inflight.add(cs)
        self._q.put((cs, name_meta))

    def stop(self):
        """Signals the worker thread to exit."""
        self._done = True
        self._q.put(None)   # unblock blocking get()

    def _run(self, cb: Callable):
        while not self._done:
            try:
                item = self._q.get(timeout=1.0)
                if item is None:
                    break
                cs, name_meta = item
                try:
                    db_data    = self._db.lookup(cs)
                    hq_info    = self._hamqth.get_info(cs)   # {grid, city, name}
                    # Name: prefer user.csv, fall back to HamQTH, then metadata
                    final_name = (db_data["name"] if db_data and db_data.get("name")
                                  else hq_info.get("name") or name_meta)
                    # City: prefer user.csv, fall back to HamQTH
                    final_city = (db_data["city"] if db_data and db_data.get("city")
                                  else hq_info.get("city", ""))
                    grid       = hq_info.get("grid", "")
                    photo      = self._qrz.fetch(cs) if self._use_qrz else None
                    cb(cs, photo, final_name, grid, final_city)
                except Exception as exc:
                    logging.warning("CallsignWorker(%s): %s", cs, exc)
                finally:
                    with self._ifl_lock:
                        self._inflight.discard(cs)
            except queue.Empty:
                pass
