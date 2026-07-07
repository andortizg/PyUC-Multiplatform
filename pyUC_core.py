#!/usr/bin/python3
###################################################################################
# pyUC_core.py  –  USRP protocol + audio engine (no UI dependencies)
# Refactored from pyUC.py  Copyright (C) 2014-2020 N4IRR
# All UI events are delivered via registered callbacks fired from background threads.
# The UI layer MUST marshal them to its main thread (e.g. via a queue + after()).
###################################################################################

import socket
import struct
import threading
import queue
import logging
import math
import array

try:
    import numpy as _np          # opcional: acelera el soft clip si está instalado
except ImportError:
    _np = None

try:
    import audioop
except ImportError:          # Python 3.13+ — audioop removed from stdlib
    try:
        import audioop_lts as audioop   # pip install audioop-lts
    except ImportError:
        raise ImportError(
            "audioop not available. On Python 3.13+ install: pip install audioop-lts"
        ) from None
import pyaudio
import json
import hashlib
import configparser
import sys
from time   import time, sleep
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

UC_VERSION = "1.2.3"

# ---------------------------------------------------------------------------
# USRP packet types
# ---------------------------------------------------------------------------
USRP_TYPE_VOICE       = 0
USRP_TYPE_DTMF        = 1
USRP_TYPE_TEXT        = 2
USRP_TYPE_PING        = 3
USRP_TYPE_TLV         = 4
USRP_TYPE_VOICE_ADPCM = 5
USRP_TYPE_VOICE_ULAW  = 6

# ---------------------------------------------------------------------------
# TLV tags
# ---------------------------------------------------------------------------
TLV_TAG_BEGIN_TX  = 0
TLV_TAG_AMBE      = 1
TLV_TAG_END_TX    = 2
TLV_TAG_TG_TUNE   = 3
TLV_TAG_PLAY_AMBE = 4
TLV_TAG_REMOTE_CMD= 5
TLV_TAG_AMBE_49   = 6
TLV_TAG_AMBE_72   = 7
TLV_TAG_SET_INFO  = 8
TLV_TAG_IMBE      = 9
TLV_TAG_DSAMBE    = 10
TLV_TAG_FILE_XFER = 11

# Native pyaudio sample rate; downsampled to 8 kHz for USRP
SAMPLE_RATE = 48000

# Audio buffer size: número de paquetes a acumular antes de procesar
# 0 = procesar paquete a paquete (PC rápido)
# 2-4 = recomendado para Raspberry Pi
RX_BUFFER_PACKETS = 3

# Jitter buffer: paquetes de prearranque antes de empezar a reproducir (punto 2).
# Da margen contra jitter de red. 2 paquetes ≈ 40 ms extra de latencia.
RX_PREFILL_PACKETS = 2

# Máximo de bloques de silencio consecutivos a inyectar en underrun (punto 3)
# antes de asumir que la transmisión terminó. blk_size≈60ms → 5 ≈ 300 ms.
RX_MAX_SILENCE_BLOCKS = 5

# ---------------------------------------------------------------------------
# Soft clip parameters 
# ---------------------------------------------------------------------------
SOFT_CLIP_THRESHOLD = 25000    # absolute velue threshold (0-32767)
SOFT_CLIP_RATIO     = 0.25     # soft clipping factor:
                               #   0.0 = hard clip
                               #   0.25 = smooth (recommended)
                               #   0.5 = medium
                               #   1.0 = turn off soft clipping

# ---------------------------------------------------------------------------
# AGC RX — parámetros de suavizado
# ---------------------------------------------------------------------------
AGC_KNEE         = 0.5     # fracción del target donde empieza a comprimir
AGC_KNEE_RATIO   = 5.0     # ratio de compresión por encima de la rodilla
AGC_MAX_GAIN_STEP = 0.1   # cambio máximo de ganancia por iteración

# ---------------------------------------------------------------------------
# Configuration data class  (plain Python – no tkinter)
# ---------------------------------------------------------------------------
@dataclass
class CoreConfig:
    """
    All parameters loaded from the .ini file.
    talk_groups: dict  mode_name -> [(display_name, dial_string), ...]
    macros:      dict  dial_string -> display_name
    """
    my_call:            str  = "N0CALL"
    subscriber_id:      int  = 3112000
    repeater_id:        int  = 311200
    ip_address:         str  = "1.2.3.4"
    usrp_tx_port:       List[int] = field(default_factory=lambda: [12345])
    usrp_rx_port:       int  = 12345
    default_server:     str  = "DMR"
    slot:               int  = 2
    in_index:           Optional[int] = None   # None = default, -1 = disabled
    out_index:          Optional[int] = None
    mic_vol:            int  = 100   # 0–100, applied as software gain on TX audio
    spk_vol:            int  = 100   # 0–100, applied as software gain on RX audio
    vox_enable:         bool = False
    vox_threshold:      int  = 200
    vox_delay:          int  = 50
    asl_mode:           int  = 0
    use_qrz:            bool = True
    level_every_sample: int  = 2
    nat_ping_timer:     int  = 0
    talk_groups:        Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    macros:             Dict[str, str]                   = field(default_factory=dict)
    bg_color:           str  = "gray25"
    text_color:         str  = "white"


def _read_value(config, stanza, key, default, fn):
    """
    Safe ini reader with type conversion.
    :param config:  configparser instance
    :param stanza:  section name
    :param key:     option name
    :param default: value returned when absent or 'Default'
    :param fn:      conversion function (int, str, float …)
    :return: fn(raw_value) or default
    """
    try:
        raw = config.get(stanza, key).split(None)[0]
        return default if raw.lower() == "default" else fn(raw)
    except Exception:
        return default


def load_config(path: str) -> CoreConfig:
    """
    Parses a pyUC .ini file and returns a validated CoreConfig.
    :param path: filesystem path to the .ini file
    :return: CoreConfig instance
    :raises SystemExit: if the file is missing, malformed, or still has default values
    """
    parser = configparser.ConfigParser(inline_comment_prefixes=(';',))
    parser.optionxform = lambda o: o
    try:
        parser.read(path)
        cfg = CoreConfig()
        D = 'DEFAULTS'
        cfg.my_call         = parser.get(D, 'myCall').split(None)[0]
        cfg.subscriber_id   = int(parser.get(D, 'subscriberID').split(None)[0])
        cfg.repeater_id     = int(parser.get(D, 'repeaterID').split(None)[0])
        cfg.ip_address      = parser.get(D, 'ipAddress').split(None)[0]
        cfg.usrp_tx_port    = [int(p) for p in parser.get(D, 'usrpTxPort').split(',')]
        cfg.usrp_rx_port    = int(parser.get(D, 'usrpRxPort').split(None)[0])
        cfg.default_server  = parser.get(D, 'defaultServer').split(None)[0]
        cfg.slot            = int(parser.get(D, 'slot').split(None)[0])
        cfg.asl_mode        = int(parser.get(D, 'aslMode').split(None)[0])
        cfg.vox_enable      = bool(int(parser.get(D, 'voxEnable').split(None)[0]))
        cfg.vox_threshold   = int(parser.get(D, 'voxThreshold').split(None)[0])
        cfg.vox_delay       = int(parser.get(D, 'voxDelay').split(None)[0])
        cfg.in_index        = _read_value(parser, D, 'in_index',          None,    int)
        cfg.out_index       = _read_value(parser, D, 'out_index',         None,    int)
        cfg.mic_vol         = _read_value(parser, D, 'micVol',            100,     int)
        cfg.spk_vol         = _read_value(parser, D, 'spkVol',            100,     int)
        cfg.use_qrz         = bool(_read_value(parser, D, 'useQRZ',       1,       int))
        cfg.level_every_sample = _read_value(parser, D, 'levelEverySample', 2,     int)
        cfg.nat_ping_timer  = _read_value(parser, D, 'pingTimer',         0,       int)
        cfg.bg_color        = _read_value(parser, D, 'backgroundColor',  'gray25',str)
        cfg.text_color      = _read_value(parser, D, 'textColor',        'white', str)

        # Sections that are NOT radio modes (must never appear in talk_groups)
        _NON_MODE = {'DEFAULTS', 'MACROS', 'COLORS', 'FAVORITES',
                     'PISTAR_HOSTS', 'PISTAR_URLS'}
        for sect in parser.sections():
            if sect not in _NON_MODE and not sect.upper().startswith('FAV_'):
                cfg.talk_groups[sect] = list(parser.items(sect))

        if 'MACROS' in parser.sections():
            for k, v in parser.items('MACROS'):
                cfg.macros[v] = k          # dial_string -> display_name

    except Exception as exc:
        logging.error("Config file error: %s", exc)
        sys.exit(f"Configuration file '{path}' is not valid. Exiting.")

    if cfg.my_call == "N0CALL" or cfg.subscriber_id == 3112000 or cfg.ip_address == "1.2.3.4":
        logging.error("Please edit the .ini file: set myCall, subscriberID and ipAddress.")
        sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------
class USRPCore:
    """
    USRP protocol engine and audio I/O.
    No tkinter imports.  All UI notifications go through callbacks
    that are fired from background threads – the UI is responsible for
    marshalling them to its own event loop (e.g. via ipc_queue + after()).

    Registered events and their callback signatures:
      'registered'      ()
      'unregistered'    ()
      'rx_begin'        (call:str, tg:str, slot:str, mode:str, name:str)
      'rx_end'          (call:str, tg:str, loss:str, duration:float, start_time:float)
      'audio_level'     (level:int)           – fired ~25–50× per second while audio active
      'ptt_change'      (state:bool)
      'text_message'    (title:str, text:str)
      'mode_change'     (mode:str, last_tune:str)
      'photo_request'   (callsign:str, name:str)
      'macro_received'  (macros:dict)
      'tg_added'        (mode:str, tg_name:str, tg_value:str)
      'transmit_enable' (enabled:bool)
      'connected'       (tg_name:str)
      'disconnected'    ()
      'error'           (message:str)
      'ab_exiting'      (sleep_time:int)
    """

    # -----------------------------------------------------------------------
    def __init__(self, cfg: CoreConfig):
        """
        :param cfg: CoreConfig instance produced by load_config()
        """
        self.cfg = cfg

        # Runtime mutable state (safe to read/write from UI if needed)
        self.slot          = cfg.slot
        self.vox_enable    = cfg.vox_enable
        self.vox_threshold = cfg.vox_threshold
        self.vox_delay     = cfg.vox_delay
        self.talk_groups   = cfg.talk_groups   # {mode: [(name, dial), ...]}
        self.macros        = cfg.macros        # {dial: display}
        self._force_rx_reset = False
        
        # Private state
        self._udp          = None
        self._pyaudio      = None
        self._usrp_seq     = 0
        self._done         = False
        self._ptt          = False
        self._reg_state    = False
        self._tx_enable    = True    # False while remote station is transmitting
        self._tx_start     = 0.0
        self._current_mode = cfg.default_server
        self._current_tg   = ""
        self._rx_agc_gain  = 1.0    # AGC gain state for RX path
        self._tx_agc_gain  = 1.0    # AGC gain state for TX path
        self._no_quote     = {ord('"'): ''}
        self._file_md5     = None

        # Cola RX→reproducción (jitter buffer). maxsize=64 ≈ 1.28 s de audio.
        self._audio_q: queue.Queue = queue.Queue(maxsize=64)

        _all_events = [
            'registered', 'unregistered',
            'rx_begin', 'rx_end',
            'audio_level', 'ptt_change',
            'text_message', 'mode_change',
            'photo_request', 'macro_received', 'tg_added',
            'transmit_enable', 'connected', 'disconnected',
            'error', 'ab_exiting',
        ]
        self._callbacks: Dict[str, List[Callable]] = {e: [] for e in _all_events}

    # -----------------------------------------------------------------------
    # Callback API
    # -----------------------------------------------------------------------
    def on(self, event: str, cb: Callable):
        """
        Register a callback for event.
        :param event: one of the event names listed in the class docstring
        :param cb:    callable(*args) matching the event signature
        """
        if event in self._callbacks:
            self._callbacks[event].append(cb)
        else:
            logging.warning("USRPCore: unknown event '%s'", event)

    def _fire(self, event: str, *args):
        """
        Fire all callbacks for event.
        :param event: event name
        :param args:  forwarded to every registered callback
        """
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as exc:
                logging.warning("Callback error [%s]: %s", event, exc)

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------
    @property
    def ptt(self) -> bool:
        return self._ptt

    @property
    def reg_state(self) -> bool:
        return self._reg_state

    @property
    def current_mode(self) -> str:
        return self._current_mode

    @property
    def current_tg(self) -> str:
        return self._current_tg

    # -----------------------------------------------------------------------
    # Audio device enumeration
    # -----------------------------------------------------------------------
    def list_audio_devices(self, want_input: bool) -> List[str]:
        """
        Enumerates pyaudio devices.
        :param want_input: True → input devices; False → output devices
        :return: list of device name strings
        """
        devices = []
        p = pyaudio.PyAudio()
        try:
            n = p.get_host_api_info_by_index(0).get('deviceCount')
            for i in range(n):
                info    = p.get_device_info_by_host_api_device_index(0, i)
                is_in   = info.get('maxInputChannels') > 0
                if (want_input and is_in) or (not want_input and not is_in):
                    devices.append(info.get('name'))
        finally:
            p.terminate()
        return devices

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------
    def start(self):
        """
        Opens UDP socket, initialises pyaudio, starts background threads
        and registers with Analog Bridge.
        Call once after creating the instance.
        """
        self._suppress_alsa_errors()
        self._open_udp()
        self._pyaudio = pyaudio.PyAudio()

        threading.Thread(target=self._rx_thread,   daemon=True, name="usrp-rx").start()
        threading.Thread(target=self._play_thread, daemon=True, name="usrp-play").start()
        if self.cfg.in_index != -1:
            threading.Thread(target=self._tx_thread, daemon=True, name="usrp-tx").start()
        if self.cfg.nat_ping_timer > 0:
            threading.Thread(target=self._ping_thread, daemon=True, name="usrp-ping").start()

        self._fire('disconnected')
        self._do_register()

    def stop(self):
        """
        Signals background threads to exit and sends unregister to AB.
        Call on application shutdown.
        """
        self._done = True
        if self._reg_state:
            sleep(0.5)
            self.unregister_with_ab()

    # -----------------------------------------------------------------------
    # UDP helpers
    # -----------------------------------------------------------------------
    def _open_udp(self):
        """Opens and optionally binds the UDP socket."""
        self._usrp_seq = 0
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            logging.info("Windows: SO_REUSEPORT not supported, continuing.")
        if self.cfg.usrp_rx_port not in self.cfg.usrp_tx_port:
            self._udp.bind(('', self.cfg.usrp_rx_port))

    def _sendto(self, pkt: bytes):
        """
        Sends a raw USRP packet to every configured TX port.
        :param pkt: raw bytes of the complete USRP packet
        """
        for port in self.cfg.usrp_tx_port:
            self._udp.sendto(pkt, (self.cfg.ip_address, port))

    # -----------------------------------------------------------------------
    # USRP protocol
    # -----------------------------------------------------------------------
    def send_usrp_command(self, cmd: bytes, pkt_type: int):
        """
        Builds and sends a USRP command packet.
        :param cmd:      payload bytes appended after the 32-byte header
        :param pkt_type: one of USRP_TYPE_* constants
        """
        try:
            hdr = b'USRP' + struct.pack('>iiiiiii',
                self._usrp_seq, 0, 0, 0, pkt_type << 24, 0, 0)
            self._usrp_seq = (self._usrp_seq + 1) & 0xffff
            self._sendto(hdr + cmd)
        except Exception as exc:
            logging.error("send_usrp_command: %s", exc)
            self._fire('error', "Socket failure")

    def send_remote_ctrl(self, cmd: bytes):
        """
        Wraps cmd in a TLV_TAG_REMOTE_CMD envelope and sends as USRP_TYPE_TLV.
        :param cmd: raw command bytes
        """
        tlv = struct.pack("BB", TLV_TAG_REMOTE_CMD, len(cmd))[:2] + cmd
        self.send_usrp_command(tlv, USRP_TYPE_TLV)

    def send_remote_ctrl_ascii(self, cmd: str):
        """
        Sends an ASCII remote control command string.
        :param cmd: e.g. 'txTg=3100', 'txTs=2', 'ambeMode=DMR'
        """
        self.send_remote_ctrl(cmd.encode('ASCII'))

    def register_with_ab(self):
        """Sends REG:DVSWITCH to Analog Bridge."""
        self.send_usrp_command(b"REG:DVSWITCH", USRP_TYPE_TEXT)

    def unregister_with_ab(self):
        """Sends REG:UNREG to Analog Bridge."""
        self.send_usrp_command(b"REG:UNREG", USRP_TYPE_TEXT)

    def request_info(self):
        """Requests INFO JSON from Analog Bridge."""
        self.send_usrp_command(b"INFO:", USRP_TYPE_TEXT)

    def send_metadata(self):
        """Sends subscriber/callsign metadata to Analog Bridge (TLV_TAG_SET_INFO)."""
        dmr_id = self.cfg.subscriber_id
        call   = self.cfg.my_call.encode('ASCII') + b'\x00'
        tl_len = 3 + 4 + 3 + 1 + 1 + len(self.cfg.my_call) + 1
        hdr    = struct.pack("BBBBBBBBBBBBBB",
                    TLV_TAG_SET_INFO, tl_len,
                    (dmr_id >> 16) & 0xff, (dmr_id >> 8) & 0xff, dmr_id & 0xff,
                    0, 0, 0, 0, 0, 0, 0, 0, 0)[:14]
        self.send_usrp_command(hdr + call, USRP_TYPE_TEXT)

    def set_ambe_mode(self, mode: str):
        """
        Tells AB which AMBE codec mode to use.
        :param mode: 'DMR', 'DSTAR', 'YSF', 'NXDN', 'P25'
        """
        self.send_remote_ctrl_ascii("ambeMode=" + mode)

    def set_remote_ts(self, ts: int):
        """
        Sets the DMR time slot on AB.
        :param ts: 1 or 2
        """
        self.send_remote_ctrl_ascii("txTs=" + str(ts))

    def set_remote_tg(self, tg: str):
        """
        Tells AB to select a single talk group.
        :param tg: dial string (TG number, YSF address, or *macro)
        """
        self.send_remote_ctrl_ascii("tgs=" + tg)
        self.send_usrp_command(tg.encode('ASCII'), USRP_TYPE_DTMF)
        self._tx_enable = True

    def set_remote_tg_list(self, tg_list: List[str]):
        """
        Tells AB to monitor multiple talk groups simultaneously.
        :param tg_list: list of dial strings
        """
        self.send_remote_ctrl_ascii("tgs=" + ",".join(tg_list))
        self.send_remote_ctrl_ascii("txTg=0")
        self._tx_enable = True

    def set_mode(self, mode: str):
        """
        Sends a macro command to AB to switch protocol mode.
        :param mode: e.g. 'DMR', 'YSF', 'P25'
        """
        if not self._tx_enable:
            self._tx_enable = True
            self._fire('transmit_enable', True)
            self._fire('rx_end', '', '', '', 0.0, 0.0)
            self._fire('audio_level', 0)
        self._force_rx_reset = True   # ← nuevo
        self._current_mode = mode
        self.send_usrp_command(("*" + mode).encode('ASCII'), USRP_TYPE_DTMF)

    def connect(self, tg: str, tg_name: str):
        """
        Connects to a talk group or runs a macro.
        :param tg:      dial string (TG number / YSF address / *macro)
        :param tg_name: display name shown in the UI
        """
        if not self._reg_state:
            self._do_register()
        if not tg.startswith('*'):
            self.set_remote_ts(self.slot)
            self._fire('connected', tg_name)
        self.set_remote_tg(tg)
        self._current_tg = tg

    def disconnect_tg(self):
        """Disconnects by dialling the first entry (disconnect TG) for the current mode."""
        tgs = self.talk_groups.get(self._current_mode, [])
        if tgs:
            dis = tgs[0][1].translate(self._no_quote)
            self.set_remote_tg(dis)
        self._fire('disconnected')
    
    # -----------------------------------------------------------------------
    # SOFT CLIPPING
    # -----------------------------------------------------------------------
    @staticmethod
    def _soft_clip(data: bytes, threshold: int = SOFT_CLIP_THRESHOLD,
                   ratio: float = SOFT_CLIP_RATIO) -> bytes:
        """
        Soft clipping de audio PCM 16-bit mono.
        :param data:      bytes PCM s16le
        :param threshold: umbral absoluto (0-32767) donde empieza la compresión
        :param ratio:     factor de compresión por encima del umbral (1.0 = off)
        :return: bytes PCM s16le del mismo tamaño
        Optimización: salida inmediata (audioop.max, en C) si ningún pico supera
        el umbral — el caso habitual. Solo procesa bloques realmente altos, y con
        numpy vectorizado si está disponible.
        """
        if ratio >= 1.0:
            return data
        if audioop.max(data, 2) <= threshold:      # fast path (C): sin picos
            return data
        if _np is not None:
            s = _np.frombuffer(data, dtype=_np.int16).astype(_np.int32)
            hi = s >  threshold
            lo = s < -threshold
            s[hi] =  threshold + ((s[hi] - threshold) * ratio).astype(_np.int32)
            s[lo] = -threshold + ((s[lo] + threshold) * ratio).astype(_np.int32)
            return _np.clip(s, -32768, 32767).astype(_np.int16).tobytes()
        samples = array.array('h', data)
        for i, s in enumerate(samples):
            if s > threshold:
                samples[i] = int(threshold + (s - threshold) * ratio)
            elif s < -threshold:
                samples[i] = int(-threshold + (s + threshold) * ratio)
        return samples.tobytes()

    def _flush_audio_q(self):
        """
        Vacía la cola de audio pendiente y envía el sentinel None al hilo de
        reproducción para que descarte también su buffer parcial.
        Sin parámetros; no devuelve nada.
        """
        try:
            while True:
                self._audio_q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._audio_q.put_nowait(None)
        except queue.Full:
            pass

    def _open_out_stream(self):
        """
        Abre el stream de salida de PortAudio, intentando 8 kHz nativo primero
        (ALSA plughw remuestrea en C) y cayendo a SAMPLE_RATE (48 kHz) si el
        dispositivo lo rechaza.
        :return: (stream, rate:int, chunk:int)
                 stream  -> objeto PyAudio.Stream abierto para salida
                 rate    -> tasa de muestreo real del stream (8000 o SAMPLE_RATE)
                 chunk   -> frames por buffer base (160 @8k, 960 @48k)
        :raises Exception: si ningún modo consigue abrir el stream
        """
        want_8k = getattr(self.cfg, 'native_8k', True)
        attempts = [(8000, 160)] if want_8k else []
        attempts.append((SAMPLE_RATE, 960))
        last_exc = None
        for rate, chunk in attempts:
            try:
                stream = self._pyaudio.open(
                    format=pyaudio.paInt16, channels=1, rate=rate,
                    output=True, frames_per_buffer=chunk * 8,
                    output_device_index=self.cfg.out_index)
                logging.info("Output stream abierto a %d Hz", rate)
                return stream, rate, chunk
            except Exception as exc:
                last_exc = exc
                logging.warning("Output stream %d Hz falló: %s", rate, exc)
        raise last_exc

    def _open_in_stream(self):
        """
        Abre el stream de entrada (micro), intentando 8 kHz nativo primero y
        cayendo a SAMPLE_RATE si el dispositivo lo rechaza.
        :return: (stream, rate:int, chunk:int)
                 stream  -> objeto PyAudio.Stream abierto para entrada
                 rate    -> tasa de muestreo real (8000 o SAMPLE_RATE)
                 chunk   -> frames por lectura (160 @8k, 960 @48k)
        :raises Exception: si ningún modo consigue abrir el stream
        """
        want_8k = getattr(self.cfg, 'native_8k', True)
        attempts = [(8000, 160)] if want_8k else []
        attempts.append((SAMPLE_RATE, 960))
        last_exc = None
        for rate, chunk in attempts:
            try:
                stream = self._pyaudio.open(
                    format=pyaudio.paInt16, channels=1, rate=rate,
                    input=True, frames_per_buffer=chunk,
                    input_device_index=self.cfg.in_index)
                logging.info("Input stream abierto a %d Hz", rate)
                return stream, rate, chunk
            except Exception as exc:
                last_exc = exc
                logging.warning("Input stream %d Hz falló: %s", rate, exc)
        raise last_exc

    # -----------------------------------------------------------------------
    # RX DSP (a 8 kHz — 6× menos muestras que hacerlo a 48 kHz)
    # -----------------------------------------------------------------------
    def _process_rx_block(self, block8k: bytes, rx_state, out_rate: int):
        """
        Procesa un bloque de audio RX: volumen, AGC y soft clip a 8 kHz,
        y remuestrea a out_rate solo si el stream de salida no es de 8 kHz.
        :param block8k:  bytes PCM s16le mono a 8 kHz
        :param rx_state: estado de audioop.ratecv (None en el primer bloque)
        :param out_rate: tasa real del stream de salida (8000 → sin remuestreo)
        :return: (audio_out: bytes a out_rate, rx_state actualizado)
        """
        out = block8k
        spk_gain = max(0, min(self.cfg.spk_vol, 100)) / 100.0
        if spk_gain != 1.0:
            out = audioop.mul(out, 2, spk_gain)

        if self.cfg.rx_agc_enable:
            rms_rx = audioop.rms(out, 2) or 1
            max_rx = audioop.max(out, 2)
            if max_rx > 26000:
                peak_ratio  = 32767 / max_rx
                target_gain = self._rx_agc_gain * peak_ratio * 0.9
            else:
                if rms_rx > self.cfg.rx_agc_target * AGC_KNEE:
                    excess            = rms_rx - self.cfg.rx_agc_target * AGC_KNEE
                    compressed_excess = excess / AGC_KNEE_RATIO
                    target_rms        = self.cfg.rx_agc_target * AGC_KNEE + compressed_excess
                else:
                    target_rms = self.cfg.rx_agc_target
                target_gain = target_rms / rms_rx
            gain_diff = target_gain - self._rx_agc_gain
            if gain_diff > 0:
                gain_diff = min(gain_diff, AGC_MAX_GAIN_STEP)
            else:
                gain_diff = max(gain_diff, -AGC_MAX_GAIN_STEP * 2)
            self._rx_agc_gain += gain_diff
            self._rx_agc_gain = max(0.3, min(self._rx_agc_gain, self.cfg.rx_agc_max_gain))
            out = audioop.mul(out, 2, self._rx_agc_gain)

        out = self._soft_clip(out)

        if out_rate != 8000:
            out, rx_state = audioop.ratecv(out, 2, 1, 8000, out_rate, rx_state)
        return out, rx_state

    # -----------------------------------------------------------------------
    # Hilo de reproducción (jitter buffer real, desacoplado de la red)
    # -----------------------------------------------------------------------
    def _play_thread(self):
        """
        Consume paquetes de voz de self._audio_q, los procesa y los reproduce.
        Desacopla la recepción UDP (hilo RX) de la escritura bloqueante en
        PortAudio, eliminando los underruns por jitter de red o carga de CPU.
        Elementos de la cola:
          bytes  → payload de voz de 320 bytes (20 ms @ 8 kHz)
          None   → sentinel: nueva transmisión / reset → vaciar buffer parcial
        """
        try:
            out_stream, out_rate, _ = self._open_out_stream()
        except Exception as exc:
            logging.critical("Cannot open output audio stream: %s", exc)
            self._fire('error', "Output audio stream open error")
            return

        blk_size = 320 * max(1, RX_BUFFER_PACKETS)   # bytes por bloque de proceso
        prefill_bytes = blk_size + 320 * RX_PREFILL_PACKETS   # umbral de prearranque
        silence_in = b'\x00' * blk_size              # silencio a 8 kHz (pre-proceso)
        buf         = bytearray()
        rx_state    = None
        priming     = True    # True = acumulando prearranque, aún sin reproducir
        active      = False   # True = transmisión en curso (hay audio real fluyendo)
        silence_run = 0       # bloques de silencio consecutivos inyectados

        while not self._done:
            try:
                item = self._audio_q.get(timeout=0.1)
            except queue.Empty:
                # Underrun: si la transmisión sigue activa y no hay datos,
                # inyectar silencio para mantener PortAudio cebado (punto 3).
                if active and not priming:
                    if buf:
                        audio_out, rx_state = self._process_rx_block(bytes(buf), rx_state, out_rate)
                        out_stream.write(audio_out, len(audio_out) // 2)
                        buf.clear()
                    elif silence_run < RX_MAX_SILENCE_BLOCKS:
                        audio_out, rx_state = self._process_rx_block(silence_in, rx_state, out_rate)
                        out_stream.write(audio_out, len(audio_out) // 2)
                        silence_run += 1
                    else:
                        active = False   # asumir fin de transmisión sin sentinel
                continue

            if item is None:                  # reset (nueva transmisión / cambio de modo)
                buf.clear()
                rx_state    = None
                priming     = True
                active      = False
                silence_run = 0
                continue

            active      = True
            silence_run = 0
            buf.extend(item)

            # Prearranque: no reproducir hasta acumular el umbral (punto 2)
            if priming:
                if len(buf) < prefill_bytes:
                    continue
                priming = False

            while len(buf) >= blk_size:
                block = bytes(buf[:blk_size])
                del buf[:blk_size]            # in-place, sin copia del resto
                audio_out, rx_state = self._process_rx_block(block, rx_state, out_rate)
                out_stream.write(audio_out, len(audio_out) // 2)

        try:
            out_stream.stop_stream()
            out_stream.close()
        except Exception:
            pass
    
    # -----------------------------------------------------------------------
    # PTT
    # -----------------------------------------------------------------------
    def set_ptt(self, state: bool):
        """
        Sets PTT.  Ignores key-up→transmit if remote station is active.
        :param state: True = start transmitting, False = stop
        """
        if state and not self._tx_enable and not self._ptt:
            return
        self._ptt = state
        if state:
            self._tx_start = time()
        self._fire('ptt_change', state)

    def toggle_ptt(self):
        """Toggles PTT state."""
        self.set_ptt(not self._ptt)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------
    def _do_register(self):
        """Starts the AB registration sequence (or fakes it for ASL mode)."""
        if self.cfg.asl_mode != 0:
            self._reg_state = True
            self._fire('registered')
        else:
            self.register_with_ab()

    @staticmethod
    def _suppress_alsa_errors():
        """Silences noisy ALSA/libasound log output on Linux."""
        try:
            from ctypes import cdll, CFUNCTYPE, c_char_p, c_int
            handler = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
            cdll.LoadLibrary('libasound.so').snd_lib_error_set_handler(handler(lambda *a: None))
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Background threads
    # -----------------------------------------------------------------------
    def _ping_thread(self):
        """NAT keep-alive: sends PING every 20 s."""
        while not self._done:
            sleep(20.0)
            self.send_usrp_command(b"PING", USRP_TYPE_PING)

    # ---- RX ----------------------------------------------------------------
    def _rx_thread(self):
        """
        Receives USRP packets from Analog Bridge:
        - plays incoming audio through the output device
        - fires UI callbacks for all protocol events
        All tkinter interactions are the UI layer's responsibility.
        """
        USRP    = b'USRP'
        REG     = b'REG:'
        UNREG   = b'UNREG'
        OK      = b'OK'
        INFO    = b'INFO:'
        EXITING = b'EXITING'

        last_key = -1
        start_time = time()
        call = name = tg = rxslot = loss = ''
        last_seq = seq = 0
        _rx_seq_first  = -1
        _rx_seq_last   = -1
        _rx_pkt_rcv    = 0
        _rx_pkt_exp    = 0
    
        last_rx_time = time()
        self._udp.settimeout(0.5)

        while not self._done:
            try:
                raw, _ = self._udp.recvfrom(1024)
                last_rx_time = time()
            except socket.timeout:
                if last_key == 1 and time() - last_rx_time > 3.0:
                    logging.warning("RX timeout — forzando rx_end")
                    if _rx_seq_first >= 0 and _rx_seq_last > _rx_seq_first:
                        _rx_pkt_exp = _rx_seq_last - _rx_seq_first + 1
                        lost_pkts = max(0, _rx_pkt_exp - _rx_pkt_rcv)
                        loss = f'{lost_pkts / _rx_pkt_exp * 100:.1f}%' if _rx_pkt_exp > 0 else '0.0%'
                    duration = time() - start_time
                    if duration > 300:
                        duration = 0.0
                    self._fire('rx_end', call, tg, loss, duration, start_time)
                    self._fire('transmit_enable', True)
                    self._fire('audio_level', 0)
                    self._tx_enable = True
                    last_key = 0
                    call = name = tg = rxslot = loss = ''
                    self._flush_audio_q()
                    _rx_seq_first = -1
                continue
            except Exception:
                continue
    
            # Reset forzado por cambio de modo
            if self._force_rx_reset:
                self._force_rx_reset = False
                if last_key == 1:
                    if _rx_seq_first >= 0 and _rx_seq_last > _rx_seq_first:
                        _rx_pkt_exp = _rx_seq_last - _rx_seq_first + 1
                        lost_pkts = max(0, _rx_pkt_exp - _rx_pkt_rcv)
                        loss = f'{lost_pkts / _rx_pkt_exp * 100:.1f}%' if _rx_pkt_exp > 0 else '0.0%'
                    duration = time() - start_time
                    if duration > 300:
                        duration = 0.0
                    self._fire('rx_end', call, tg, loss, duration, start_time)
                    self._fire('transmit_enable', True)
                    self._fire('audio_level', 0)
                self._tx_enable = True
                last_key = -1
                call = name = tg = rxslot = loss = ''
                self._flush_audio_q()
                _rx_seq_first = -1
                continue
    
            if raw[:4] != USRP:
                continue
    
            seq,       = struct.unpack(">i", raw[4:8])
            keyup,     = struct.unpack(">i", raw[12:16])
            talkgroup, = struct.unpack(">i", raw[16:20])
            pkt_type,  = struct.unpack("i",  raw[20:24])
            payload    = raw[32:]
    
            # ---- Voice audio -------------------------------------------
            if pkt_type == USRP_TYPE_VOICE:
                if keyup != last_key:
                    if keyup:
                        start_time    = time()
                        self._flush_audio_q()
                        _rx_seq_first = -1
                        _rx_seq_last  = -1
                        _rx_pkt_rcv   = 0
                    else:
                        duration = time() - start_time
                        if duration > 300:
                            duration = 0.0
                        if _rx_seq_first >= 0 and _rx_seq_last > _rx_seq_first:
                            _rx_pkt_exp = _rx_seq_last - _rx_seq_first + 1
                            lost        = max(0, _rx_pkt_exp - _rx_pkt_rcv)
                            loss        = f'{lost / _rx_pkt_exp * 100:.1f}%'
                        else:
                            loss = '0.0%'
                        self._fire('rx_end', call, tg, loss,
                                   duration, start_time)
                        self._tx_enable = True
                        self._fire('transmit_enable', True)
                        self._fire('audio_level', 0)
                        _rx_seq_first = -1
                        loss          = ''
                    last_key = keyup

                if len(payload) == 320:
                    # Encolar para el hilo de reproducción (jitter buffer).
                    # Si la cola está llena (reproducción atascada) se descarta
                    # el paquete más antiguo para no acumular latencia.
                    try:
                        self._audio_q.put_nowait(payload)
                    except queue.Full:
                        try:
                            self._audio_q.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._audio_q.put_nowait(payload)
                        except queue.Full:
                            pass

                    # ── Audio level para la barra ───────────────────────
                    if (seq % self.cfg.level_every_sample) == 0:
                        rms = audioop.rms(payload, 2)
                        level = min(int(rms / 35), 100)
                        self._fire('audio_level', level)
    
                    # ── Packet loss tracking ──────────────────────────────
                    if keyup:
                        if _rx_seq_first == -1:
                            _rx_seq_first = seq
                            _rx_pkt_rcv   = 1
                        else:
                            _rx_pkt_rcv += 1
                        _rx_seq_last = seq

            # ---- Text / protocol messages ------------------------------
            elif pkt_type == USRP_TYPE_TEXT:
                if payload[:4] == REG:
                    if payload[4:6] == OK:
                        self._reg_state = True
                        self.send_metadata()
                        self.request_info()
                        self._fire('registered')
                    elif payload[4:9] == UNREG:
                        self._reg_state = False
                        self._fire('unregistered')
                    elif payload[4:11] == EXITING:
                        tmp   = payload[:payload.find(b'\x00')].decode('ASCII')
                        parts = tmp.split()
                        secs  = int(parts[2]) if len(parts) > 2 else 0
                        self._fire('ab_exiting', secs)
                        if secs > 0:
                            sleep(secs)
                            self.register_with_ab()
                    logging.info(payload[:payload.find(b'\x00')].decode('ASCII'))
    
                elif payload[:5] == INFO:
                    body = payload[5:payload.find(b'\x00')].decode('ASCII')
                    if body[:4] == "MSG:":
                        self._fire('text_message', "Text Message", body[4:])
                    elif body[:6] in ("MACRO:", "MENU:"):
                        prefix = 6 if body[:6] == "MACRO:" else 5
                        macs = {v.strip(): k
                                for item in body[prefix:].split('|')
                                for k, v in [item.split(',')]}
                        self.macros = macs
                        if body[:6] == "MACRO:":
                            self._fire('macro_received', macs)
                    else:
                        try:
                            obj  = json.loads(body)
                            mode = obj["tlv"]["ambe_mode"]
                            new_mode = "YSF" if mode[:3] == "YSF" else mode
                            last_tune = obj.get("last_tune", "")
                            logging.info(f"INFO mode_change: {new_mode}, last_tune={last_tune}")
                            if new_mode != self._current_mode and not self._tx_enable:
                                self._tx_enable = True
                                self._fire('transmit_enable', True)
                                self._fire('rx_end', '', '', '', 0.0, 0.0)
                                self._fire('audio_level', 0)
                            self._current_mode = new_mode
                            self._fire('mode_change', new_mode, last_tune)
                            if last_tune:
                                self._current_tg = last_tune
                                self.set_remote_tg(last_tune)
                        except Exception as exc:
                            logging.warning("INFO JSON parse error: %s", exc)
                else:
                    if payload[0] == TLV_TAG_SET_INFO:
                        rid = (payload[2] << 16) | (payload[3] << 8) | payload[4]
                        # Ignorar nuestro propio eco
                        if rid == self.cfg.subscriber_id:
                            if last_key == 1:
                                # Forzar fin inmediato de recepción propia
                                self._fire('rx_end', call, tg, '0.0%', 0.0, start_time)
                                self._tx_enable = True
                                self._fire('transmit_enable', True)
                                self._fire('audio_level', 0)
                                last_key = 0
                                call = name = tg = rxslot = loss = ''
                                self._flush_audio_q()
                                _rx_seq_first = -1
                            continue
    
                        if not self._tx_enable:
                            if _rx_seq_first >= 0 and _rx_seq_last > _rx_seq_first:
                                _rx_pkt_exp = _rx_seq_last - _rx_seq_first + 1
                                lost        = max(0, _rx_pkt_exp - _rx_pkt_rcv)
                                loss        = f'{lost / _rx_pkt_exp * 100:.1f}%'
                            duration = time() - start_time
                            if duration > 300:
                                duration = 0.0
                            self._fire('rx_end', call, tg, loss,
                                       duration, start_time)
                            _rx_seq_first = -1
                            loss          = ''
    
                        tg_num = (payload[9] << 16) | (payload[10] << 8) | payload[11]
                        rxslot = str(payload[12])
                        rxcc   = payload[13]
                        mode_s = "Private" if (rxcc & 0x80) else "Group"
                        name   = ""
    
                        if payload[14] == 0:
                            call = str(rid)
                        else:
                            try:
                                raw_call = payload[14:payload.find(b'\x00', 14)].decode('utf-8', errors='replace')
                            except Exception:
                                raw_call = payload[14:payload.find(b'\x00', 14)].decode('latin-1', errors='replace')
                            if raw_call.startswith('{'):
                                try:
                                    obj  = json.loads(raw_call)
                                    call = obj['call']
                                    name = obj.get('name', '').split()[0] if obj.get('name') else ''
                                except Exception:
                                    call = str(rid)
                                    name = ''
                            else:
                                call = raw_call
    
                        tg = str(tg_num)
                        mode_tgs = self.talk_groups.get(self._current_mode, [])
                        if self._current_mode in ('DSTAR', 'YSF'):
                            tg = self._current_tg
                        elif tg_num == self.cfg.subscriber_id:
                            tg = self.cfg.my_call
                        else:
                            for item in mode_tgs:
                                if item[1] == str(tg_num):
                                    tg = item[0]
                                    break
    
                        self._tx_enable = False
                        self._fire('transmit_enable', False)
                        self._fire('rx_begin', call, tg, rxslot, mode_s, name)
                        if not call.isdigit():
                            self._fire('photo_request', call, name)
    
                        if (rxcc & 0x80) and (rid > 10000):
                            priv_tg = str(rid) + '#'
                            if priv_tg != self._current_tg:
                                self.send_remote_ctrl_ascii("txTg=" + priv_tg)
                                label = call + " Private"
                                self.talk_groups[self._current_mode].append((label, priv_tg))
                                self._fire('tg_added', self._current_mode, label, priv_tg)
    
            # ---- Ping --------------------------------------------------
            elif pkt_type == USRP_TYPE_PING:
                if not self._tx_enable:
                    if (last_seq + 1) == seq:
                        logging.info("missed EOT")
                        if _rx_seq_first >= 0 and _rx_seq_last > _rx_seq_first:
                            _rx_pkt_exp = _rx_seq_last - _rx_seq_first + 1
                            lost        = max(0, _rx_pkt_exp - _rx_pkt_rcv)
                            loss        = f'{lost / _rx_pkt_exp * 100:.1f}%'
                        duration = time() - start_time
                        if duration > 300:
                            duration = 0.0
                        self._fire('rx_end', call, tg, loss,
                                   duration, start_time)
                        self._tx_enable = True
                        self._fire('transmit_enable', True)
                        _rx_seq_first = -1
                        loss          = ''
                    last_seq = seq
    
            # ---- TLV ---------------------------------------------------
            elif pkt_type == USRP_TYPE_TLV:
                if payload[0] == TLV_TAG_FILE_XFER:
                    self._handle_file_xfer(payload[2:], payload[1])

    # ---- TX ----------------------------------------------------------------
    def _tx_thread(self):
        """
        Reads audio from the microphone and transmits USRP voice packets
        when PTT is active.  Also handles VOX detection.
        """
        tx_state = None   # audioop resample state
        vox_decay = 0     # VOX hold-off counter (fixed uninitialized-var bug)

        try:
            in_stream, in_rate, CHUNK = self._open_in_stream()
        except Exception as exc:
            logging.critical("Cannot open input audio stream: %s", exc)
            self._fire('error', "Input audio stream open error")
            return

        last_ptt = self._ptt

        while not self._done:
            try:
                raw = in_stream.read(CHUNK, exception_on_overflow=False)

                # Reposo total (sin PTT, sin VOX, sin flanco pendiente):
                # drenar el micro sin remuestrear ni calcular nada.
                # Evita contención de GIL con el hilo de reproducción durante RX.
                if not self._ptt and not self.vox_enable and not last_ptt:
                    tx_state = None      # invalidar estado de ratecv
                    continue

                if in_rate != 8000:
                    audio, tx_state = audioop.ratecv(
                        raw, 2, 1, in_rate, 8000, tx_state)
                else:
                    audio = raw

                rms = audioop.rms(audio, 2)

                # ── Mic volume (software gain on TX audio) ───────────────
                mic_gain = max(0, min(self.cfg.mic_vol, 100)) / 100.0
                if mic_gain != 1.0:
                    audio = audioop.mul(audio, 2, mic_gain)
                    
                # ---- VOX -------------------------------------------
                if self.vox_enable:
                    if rms > self.vox_threshold:
                        vox_decay = self.vox_delay
                        if not self._ptt and self._tx_enable:
                            self._ptt = True
                            self._fire('ptt_change', True)
                    elif self._ptt:
                        vox_decay -= 1
                        if vox_decay <= 0:
                            self._ptt = False
                            self._fire('ptt_change', False)

                # ---- Transmit audio --------------------------------
                # On PTT edge send one extra packet (transition marker)
                if self._ptt != last_ptt:
                    pkt = b'USRP' + struct.pack('>iiiiiii',
                        self._usrp_seq, 0, int(self._ptt), 0,
                        USRP_TYPE_VOICE, 0, 0) + audio
                    self._sendto(pkt)
                    self._usrp_seq = (self._usrp_seq + 1) & 0xffff

                last_ptt = self._ptt

                if self._ptt:
                    pkt = b'USRP' + struct.pack('>iiiiiii',
                        self._usrp_seq, 0, 1, 0,
                        USRP_TYPE_VOICE, 0, 0) + audio
                    self._sendto(pkt)
                    self._usrp_seq = (self._usrp_seq + 1) & 0xffff
                    level = min(int(rms / 40), 100)
                    self._fire('audio_level', level)
            except Exception as exc:
                logging.warning("TX thread: %s", exc)

    # ---- File transfer (TLV) -------------------------------------------
    def _handle_file_xfer(self, value: bytes, length: int):
        """
        Handles TLV_TAG_FILE_XFER sub-commands.
        :param value:  TLV value field bytes
        :param length: TLV length field
        """
        FILE_NAME    = 0
        FILE_PAYLOAD = 1
        FILE_WRITE   = 2
        FILE_ERROR   = 4

        if value[0] == FILE_NAME:
            file_len  = int.from_bytes(value[1:5], 'big')
            zero      = value[5:].find(0)
            file_name = value[5:5 + zero].decode('ASCII')
            logging.info("File xfer name: %s  (%d bytes)", file_name, file_len)
            self._file_md5 = hashlib.md5()
        elif value[0] == FILE_PAYLOAD and self._file_md5:
            self._file_md5.update(value[1:length])
        elif value[0] == FILE_WRITE and self._file_md5:
            got  = self._file_md5.digest().hex().upper()
            want = value[1:33].decode('ASCII')
            status = "OK" if got == want else f"MISMATCH {got} vs {want}"
            logging.info("File xfer digest: %s", status)
        elif value[0] == FILE_ERROR:
            logging.error("File xfer error")
