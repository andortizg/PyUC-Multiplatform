#!/usr/bin/python3
###############################################################################
# pyUC_ui_ctk.py  —  customtkinter UI for pyUC (EA7HQL edition)
# Supports: PC 800×600 · Raspberry Pi 5" 800×480 · Raspberry Pi 3.5" 480×320
#
# Based on pyUC  Copyright (C) 2014-2020 N4IRR / DVSwitch
# UI redesign & refactor: Andrés Ortiz EA7HQL
###############################################################################

import customtkinter as ctk
from tkinter import *
from tkinter import messagebox, ttk
from time    import localtime, strftime
from pathlib import Path
import logging, webbrowser, math, subprocess
import platform as _platform
from time import localtime, strftime, time

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

_SYS = _platform.system()

from pyUC_config  import AppConfig, save_config, UC_VERSION
from pyUC_ui_base import UIAdapter
from pyUC_services import calc_distance_km

# ─────────────────────────────────────────────────────────────────────────────
# Layout — pixel budgets derived from screen profile
# ─────────────────────────────────────────────────────────────────────────────
class Layout:
    """
    Pixel budget for each screen profile.
    :param profile: 'pc' | 'rpi5' | 'rpi35'
    :param font_family_override: force a specific font family ('' = auto)
    """
    def __init__(self, profile: str, font_family_override: str = ''):
        self.profile = profile
        pc   = profile == 'pc'
        rpi5 = profile == 'rpi5'
        tiny = profile == 'rpi35'

        self.font_family = font_family_override or (
            'Segoe UI'    if _SYS == 'Windows' else
            'SF Pro Text' if _SYS == 'Darwin'  else
            'DejaVu Sans'
        )

        self.win_w = 800 if not tiny else 480
        self.win_h = 600 if pc else (480 if rpi5 else 320)

        self.topbar_h  = 32 if pc else (28 if rpi5 else 24)
        self.tabbar_h  = 36 if pc else (32 if rpi5 else 28)
        self.modebar_h = 44 if pc else (38 if rpi5 else 32)
        self.bottom_h  = 82 if pc else (72 if rpi5 else 60)

        self.body_h = (self.win_h - self.topbar_h - self.tabbar_h
                       - self.modebar_h - self.bottom_h)

        self.tg_col_w = 190 if pc else (168 if rpi5 else None)

        self.f     = 14 if pc else (12 if rpi5 else 10)
        self.f_sm  = 11 if pc else (10 if rpi5 else  9)
        self.f_lg  = 16 if pc else (14 if rpi5 else 12)
        self.f_ptt = 17 if pc else (15 if rpi5 else 12)
        self.f_cs  = 28 if pc else (22 if rpi5 else 16)

        self.qrz_w = 198 if pc else (148 if rpi5 else 80)
        self.qrz_h = 132 if pc else ( 98 if rpi5 else 54)

        self.log_rows = 4 if pc else (3 if rpi5 else 0)
        self.stacked  = tiny
        self.show_status = not tiny

        self.mode_pady = 8 if pc else (6 if rpi5 else 5)
        self.tab_pady  = 8 if pc else (6 if rpi5 else 5)
        self.tab_padx  = 14 if pc else (10 if rpi5 else 7)

        self.meter_h = 12 if pc else (10 if rpi5 else 8)

        self._reg_lbl = None
        
        # Timer 
        self._timer_running = False
        self._timer_start = 0.0
        self._timer_after_id = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _list_audio(input_only: bool) -> list:
    """
    Lists available audio devices via pyaudio.
    :param input_only: True = input devices, False = output devices
    :return: list of device name strings; ['Default'] on failure
    """
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        key = 'maxInputChannels' if input_only else 'maxOutputChannels'
        result = [
            pa.get_device_info_by_index(i)['name']
            for i in range(pa.get_device_count())
            if pa.get_device_info_by_index(i)[key] > 0
        ]
        pa.terminate()
        return result or ['Default']
    except Exception:
        return ['Default']


def _ctk_btn(parent, text, fg, text_col, hover, cmd, *,
             border_col=None, border_w=0, corner=4,
             font=None, width=0, height=28, state='normal'):
    """
    Convenience wrapper for CTkButton with explicit color control.
    :param parent:     parent widget
    :param text:       button label
    :param fg:         background color
    :param text_col:   text color
    :param hover:      hover background color
    :param cmd:        command callable
    :param border_col: border color (None = no border)
    :param border_w:   border width in pixels
    :param corner:     corner radius
    :param font:       (family, size) or (family, size, weight) tuple
    :param width:      explicit width (0 = auto)
    :param height:     explicit height in pixels
    :param state:      'normal' | 'disabled'
    :return: CTkButton instance
    """
    kw = dict(
        text=text,
        fg_color=fg,
        text_color=text_col,
        hover_color=hover,
        border_color=border_col or fg,
        border_width=border_w,
        corner_radius=corner,
        command=cmd,
        height=height,
        state=state,
    )
    if width:
        kw['width'] = width
    if font:
        kw['font'] = font
    return ctk.CTkButton(parent, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# CtkUI — UIAdapter implementation
# ─────────────────────────────────────────────────────────────────────────────
class CtkUI(UIAdapter):
    """
    customtkinter implementation of the UIAdapter contract.
    Builds all widgets in __init__; the 'app' back-reference is set
    by main() after construction but before start().
    """

    def __init__(self, cfg: AppConfig):
        """
        :param cfg: fully loaded AppConfig (talks groups, colors, layout)
        """
        self.cfg  = cfg
        self.T    = cfg.colors
        self.L    = Layout(cfg.screen_profile, cfg.font_family)
        self.app  = None   # set externally before run()

        # Internal state
        self._filt    = 'all'
        self._nq      = {ord('"'): ''}
        self._rx_level  = 0    # written from audio thread (GIL-safe)
        self._tx_level  = 0    # written from audio thread (GIL-safe)
        self._tx_active = False
        self._is_transmitting = False
        self._onair_after_id  = None
        self._actual_status   = ('Disconnected', self.T.redColor)
        self._status_timer    = None
        self._toast_win       = None
        self._own_data        = None   # (call, photo, name, grid, city) for own callsign
        self._own_data_timer  = None   # after() id for revert-to-own-data
        self._timer_running = False
        self._timer_start = 0.0
        self._timer_after_id = None

        
        # CTk root
        ctk.set_appearance_mode(cfg.theme_mode if cfg.theme_mode in ('dark', 'light') else 'dark')
        ctk.set_default_color_theme('dark-blue')

        self.root = ctk.CTk()
        self.root.title('pyUC')
        self.root.geometry(f'{self.L.win_w}x{self.L.win_h}')
        self.root.resizable(False, False)
        self.root.configure(fg_color=self.T.bgColor)
        self.root.protocol('WM_DELETE_WINDOW', self._close)
        if cfg.fullscreen:
            if _SYS == 'Windows':
                self.root.state('zoomed')
            else:
                self.root.attributes('-fullscreen', True)
            self.root.bind('<Escape>',
                           lambda e: self.root.attributes('-fullscreen', False))
        if _SYS == 'Darwin':
            self.root.createcommand('tk::mac::Quit', self._close)

        # tk vars
        self.v_mode     = StringVar(value=cfg.default_server)
        self.v_call     = StringVar(value='')
        self.v_name     = StringVar(value='')
        self.v_minfo    = StringVar(value='')
        self.v_sys      = StringVar(value='')
        self.v_tx_stats = StringVar(value='Loss: —')
        self.v_qth_info = StringVar(value='')
        self.v_loc_dist = StringVar(value='')
        self.v_slot     = IntVar(value=cfg.slot)
        self.v_vox_en   = IntVar(value=int(cfg.vox_enable))
        self.v_vox_th   = IntVar(value=cfg.vox_threshold)
        self.v_vox_dl   = IntVar(value=cfg.vox_delay)
        self.v_mic_vol  = IntVar(value=int(cfg.mic_vol))
        self.v_spk_vol  = IntVar(value=int(cfg.spk_vol))
        self.v_timer = StringVar(value='')


        self._tgconn_full_text = ''       # texto completo del TG conectado
        self._marquee_pos = 0             # posición actual de la marquesina
        self._marquee_id = None           # id del after() para cancelarlo
        self._tgconn_full = 'DISC'
        self._tgconn_offset = 0
        self._tgconn_scrolling = False
        self._tgconn_anim_id = None      
        self._tgconn_lbl = None
        

        # Traces → propagate to core when app is ready
        self.v_vox_en.trace('w', lambda *_: self.app and
            setattr(self.app.core, 'vox_enable',    bool(self.v_vox_en.get())))
        self.v_vox_th.trace('w', lambda *_: self.app and
            setattr(self.app.core, 'vox_threshold', self.v_vox_th.get()))
        self.v_vox_dl.trace('w', lambda *_: self.app and
            setattr(self.app.core, 'vox_delay',     self.v_vox_dl.get()))
        # _v_ts_str drives the TS dropdown ('TS1'/'TS2') → v_slot IntVar → core.slot
        self._v_ts_str = StringVar(value=f'TS{cfg.slot}')
        def _ts_trace(*_):
            val = 1 if self._v_ts_str.get() == 'TS1' else 2
            self.v_slot.set(val)
            if self.app:
                self.app.core.slot = val
        self._v_ts_str.trace('w', _ts_trace)
        self.v_mic_vol.trace('w', lambda *_: self.app and
            setattr(self.app.core.cfg, 'mic_vol',   self.v_mic_vol.get()))
        self.v_spk_vol.trace('w', lambda *_: self.app and
            setattr(self.app.core.cfg, 'spk_vol',   self.v_spk_vol.get()))

        # Widget refs (populated by _build)
        self.listbox = self.ptt_btn = self.log_tv  = None
        self.qrz_lbl = self._vu_cv = None
        self._mode_btns = self._tab_frms = self._tab_btns = None
        self._filt_btns = {}
        self._sv = {}
        self._status_v = self._tgconn_v = None
        self._status_lbl = self._tgconn_lbl = None
        self.on_air_lbl  = None
        self._v_manual_tg = None   # populated by _mk_right
        self._v_private   = None   # IntVar 1=private 0=group (DMR)
        self._tg_entry_w  = None   # CTkEntry widget ref para posicionar keypad
        self._kpad        = None   # Toplevel del keypad activo (o None)

        # HamQTH vars — used in settings
        self._v_ham_user      = StringVar(value=cfg.ham_user)
        self._v_ham_pass      = StringVar(value=cfg.ham_pass)
        self._pistar_status_v = StringVar(value='')
        self._hamqth_status_lbl = None   # populated by _mk_settings

        self._build()

        if cfg.spacebar_ptt:
            self.root.bind('<space>', lambda e: self._spc_ptt())

    # ══════════════════════════════════════════════════════════════════════════
    # Build
    # ══════════════════════════════════════════════════════════════════════════
    def _build(self):
        self._build_topbar()
        self._build_tabbar()
        rem_h = self.L.win_h - self.L.topbar_h - self.L.tabbar_h - 3
        self._content = Frame(self.root, bg=self.T.bgColor,
                              width=self.L.win_w, height=rem_h)
        self._content.pack(fill=BOTH, expand=True)
        self._content.pack_propagate(False)
        self._tab_frms = {
            'main':     self._mk_main(),
            'settings': self._mk_settings(),
            'about':    self._mk_about(),
        }
        self._show('main')

    # ── Topbar ────────────────────────────────────────────────────────────────
    def _build_topbar(self):
        L, T = self.L, self.T
        bar = Frame(self.root, bg=T.surfaceColor,
                    width=L.win_w, height=L.topbar_h)
        bar.pack(fill=X)
        bar.pack_propagate(False)

        Label(bar, text='pyUC', font=(None, 14, 'bold'),
              fg=T.accentColor, bg=T.surfaceColor).pack(side=LEFT, padx=10)
        Label(bar, text=f'{self.cfg.my_call}  ·  DMRID {self.cfg.subscriber_id}',
              font=(None, L.f_sm, 'bold'),
              fg=T.accent2Color, bg=T.surfaceColor).pack(side=LEFT, padx=8)
        
        
        self._clk = Label(bar, text='', font=(None, 12, 'bold'),
                          fg=T.textPrimary, bg=T.surfaceColor)
        self._clk.pack(side=RIGHT, padx=10)

        self._sys_lbl = Label(bar, textvariable=self.v_sys,
                              font=(None, L.f_sm),
                              fg=T.textMuted, bg=T.surfaceColor)
        self._sys_lbl.pack(side=RIGHT, padx=(0, 14))
        
        self._reg_lbl = Label(bar, text='Not registered',
                      font=(None, L.f_sm, 'bold'),
                      fg=T.redColor, bg=T.surfaceColor)
        self._reg_lbl.pack(side=LEFT, padx=(0, 22))


        Frame(self.root, bg=T.borderColor, height=1).pack(fill=X)
        self._tick()

    def _tick(self):
        """Updates clock label every second."""
        self._clk.configure(text=strftime('%H:%M:%S'))
        self.root.after(1000, self._tick)

    # ── Tab bar ───────────────────────────────────────────────────────────────
    def _build_tabbar(self):
        L, T = self.L, self.T
        bar = Frame(self.root, bg=T.surfaceColor,
                    width=L.win_w, height=L.tabbar_h)
        bar.pack(fill=X)
        bar.pack_propagate(False)
        self._tab_btns = {}

        # Tab buttons
        padx = L.tab_padx
        for key, lbl in [('main', 'MAIN'), ('settings', 'SETTINGS'),
                         ('about', 'ABOUT'), ('exit', '✕ EXIT'),
                         ('shutdown', '⏻ POWEROFF')]:
            fg = T.exitTabFg if key in ('exit', 'shutdown') else T.tabInactiveFg
            b  = _ctk_btn(bar, lbl, T.surfaceColor, fg, T.surfaceColor,
                          lambda k=key: self._tab_click(k),
                          font=(None, round(L.f_sm*1.3), 'bold'),
                          height=L.tabbar_h, corner=0)
            b.configure(text_color_disabled=T.tabInactiveFg)
            # Reduce internal CTk padding by overriding the underlying button
            b.pack(side=LEFT, fill=Y, padx=0)
            self._tab_btns[key] = b

        Frame(self.root, bg=T.borderColor, height=2).pack(fill=X)

    def _tab_click(self, key):
        if   key == 'exit':     self._ask_exit()
        elif key == 'shutdown': self._ask_shutdown()
        else:                   self._show(key)

    def _show(self, key):
        T = self.T
        for f in self._tab_frms.values():
            f.place_forget()
        rem = self.L.win_h - self.L.topbar_h - self.L.tabbar_h - 3
        self._tab_frms[key].place(x=0, y=0, width=self.L.win_w, height=rem)
        for k, b in self._tab_btns.items():
            if k in ('exit', 'shutdown'): continue
            b.configure(text_color=T.tabActiveFg if k == key else T.tabInactiveFg)
    def _start_timer(self):
        """Inicia el contador de tiempo."""
        self._timer_start = time()
        self._timer_running = True
        self._tick_timer()

    def _tick_timer(self):
        """Actualiza el contador cada segundo."""
        if not self._timer_running:
            return
        elapsed = int(time() - self._timer_start)
        mins = elapsed // 60
        secs = elapsed % 60
        self.v_timer.set(f'{mins:02d}:{secs:02d}')
        self._timer_after_id = self.root.after(1000, self._tick_timer)
    
    def _stop_timer(self):
        """Para el contador de tiempo."""
        self._timer_running = False
        if self._timer_after_id:
            self.root.after_cancel(self._timer_after_id)
            self._timer_after_id = None
        self.v_timer.set('')
    
    # ══════════════════════════════════════════════════════════════════════════
    # Main tab
    # ══════════════════════════════════════════════════════════════════════════
    def _mk_main(self):
        L, T = self.L, self.T
        frm = Frame(self._content, bg=T.bgColor,
                    width=L.win_w,
                    height=self.L.win_h - L.topbar_h - L.tabbar_h - 3)
        frm.pack_propagate(False)

        # Mode bar
        mb = Frame(frm, bg=T.bgColor, height=L.modebar_h)
        mb.pack(fill=X, padx=6, pady=(3, 2))
        mb.pack_propagate(False)
        self._mode_btns = {}
        for mode in sorted(self.cfg.talk_groups.keys()):
            b = _ctk_btn(mb, mode, T.modeBtnBg, T.modeBtnFg,
                         T.modeBtnActiveBg,
                         lambda m=mode: self._sel_mode(m),
                         border_col=T.modeBtnBorder, border_w=2,
                         font=(None, 18, 'bold'),
                         height=L.modebar_h - 8)
            b.pack(side=LEFT, fill=X, expand=True, padx=2)
            self._mode_btns[mode] = b
        self._upd_mode_btn(self.cfg.default_server)

        # Body
        body = Frame(frm, bg=T.bgColor, height=L.body_h)
        body.pack(fill=X)
        body.pack_propagate(False)

        if L.stacked:
            top_h = int(L.body_h * 0.46)
            bot_h = L.body_h - top_h - 1
            tp = Frame(body, bg=T.bgColor, height=top_h)
            tp.pack(fill=X); tp.pack_propagate(False)
            Frame(body, bg=T.borderColor, height=1).pack(fill=X)
            bp = Frame(body, bg=T.bgColor, height=bot_h)
            bp.pack(fill=X); bp.pack_propagate(False)
            self._mk_left(tp, compact=True)
            self._mk_right(bp)
        else:
            lf = Frame(body, bg=T.bgColor)
            lf.pack(side=LEFT, fill=BOTH, expand=True)
            lf.pack_propagate(False)
            Frame(body, bg=T.borderColor, width=1).pack(side=LEFT, fill=Y)
            rf = Frame(body, bg=T.bgColor, width=L.tg_col_w)
            rf.pack(side=LEFT, fill=Y)
            rf.pack_propagate(False)
            self._mk_left(lf)
            self._mk_right(rf)

        Frame(frm, bg=T.borderColor, height=1).pack(fill=X)
        bot = Frame(frm, bg=T.surfaceColor, height=L.bottom_h)
        bot.pack(fill=X)
        bot.pack_propagate(False)
        self._mk_bottom(bot)
        return frm

    # ── Left column ───────────────────────────────────────────────────────────
    def _mk_left(self, parent, compact=False):
        L, T = self.L, self.T
        pad = Frame(parent, bg=T.bgColor)
        pad.pack(fill=BOTH, expand=True, padx=8, pady=4)
        pad.pack_propagate(False)

        # QRZ card
        qc = Frame(pad, bg=T.surface2Color,
                   highlightbackground=T.borderColor, highlightthickness=1)
        qc.pack(fill=X, pady=(0, 4))
        ph_wrap = Frame(qc, bg=T.qrzPhotoBg, width=L.qrz_w, height=L.qrz_h)
        ph_wrap.pack(side=LEFT, padx=8, pady=6)
        ph_wrap.pack_propagate(False)
        self.qrz_lbl = Label(ph_wrap, text='QRZ\nphoto',
                             font=(None, L.f_sm),
                             bg=T.qrzPhotoBg, fg=T.qrzPhotoFg,
                             anchor=CENTER, cursor='hand2')
        self.qrz_lbl.place(relwidth=1, relheight=1)
        self.qrz_lbl.callsign = ''
        self.qrz_lbl.bind('<Button-1>', lambda e: self._qrz_click(e))

        inf = Frame(qc, bg=T.surface2Color)
        inf.pack(side=LEFT, fill=BOTH, expand=True, pady=8, padx=(0, 8))

        call_row = Frame(inf, bg=T.surface2Color)
        call_row.pack(fill=X, anchor=W)
        # ON AIR pinned to the right edge — packed first so it's never pushed out
        # Font scaled by profile: cs font -4 avoids overflow on rpi5 (148 px wide photo)
        _onair_fs = max(14, L.f_cs - 4)
        self.on_air_lbl = Label(call_row, text='● ON AIR',
                                font=(L.font_family, _onair_fs, 'bold'),
                                fg=T.surface2Color, bg=T.surface2Color)
        self.on_air_lbl.pack(side=RIGHT, padx=(4, 4), pady=(5, 0))
        Label(call_row, textvariable=self.v_call,
              font=(L.font_family, L.f_cs, 'bold'),
              fg=T.textPrimary, bg=T.surface2Color, anchor=W).pack(side=LEFT)
        
        Label(inf, textvariable=self.v_name,
              font=(L.font_family, 18),
              fg=T.accent2Color, bg=T.surface2Color, anchor=W).pack(fill=X)
        Label(inf, textvariable=self.v_qth_info,
              font=(L.font_family, 18),
              fg=T.textSecondary, bg=T.surface2Color, anchor=W).pack(fill=X)
        loc_row = Frame(inf, bg=T.surface2Color)
        loc_row.pack(fill=X)
        Label(loc_row, textvariable=self.v_loc_dist,
              font=(L.font_family, 18, 'bold'),
              fg=T.accentColor, bg=T.surface2Color, anchor=W).pack(side=LEFT, fill=X, expand=True)
        Label(loc_row, textvariable=self.v_timer,
              font=(L.font_family, 18, 'bold'),
              fg=T.greenColor, bg=T.surface2Color, anchor=E).pack(side=RIGHT, padx=(4, 0))
                
        # minfo row: DMR ID / mode info (left) + status (right)
        minfo_row = Frame(inf, bg=T.surface2Color)
        minfo_row.pack(fill=X)
        Label(minfo_row, textvariable=self.v_minfo,
              font=(None, L.f_sm),
              fg=T.textSecondary, bg=T.surface2Color, anchor=W).pack(side=LEFT)
        self._status_v   = StringVar(value='Disconnected')
        self._status_lbl = Label(minfo_row, textvariable=self._status_v,
                                 font=(None, L.f_sm, 'bold'),
                                 fg=T.redColor, bg=T.surface2Color, anchor=E)
        self._status_lbl.pack(side=RIGHT, padx=(4, 0))

        if compact:
            return

        # Log
        if L.log_rows > 0:
            self._style_tv()
            lf = Frame(pad, bg=T.bgColor)
            lf.pack(fill=BOTH, expand=True)
            lf.pack_propagate(False)
            self.log_tv = ttk.Treeview(lf, show='headings',
                                       height=L.log_rows, selectmode='browse')
            cols = ('Call', 'Time', 'TG', 'Dur', 'Loss')
            # Restore saved widths; fall back to defaults if count doesn't match
            saved = self.cfg.log_col_widths
            wids  = saved if len(saved) == len(cols) else [80, 78, 145, 64, 72]
            self.log_tv['columns'] = cols
            for c, w in zip(cols, wids):
                self.log_tv.heading(c, text=c)
                self.log_tv.column(c, width=w, anchor=W, stretch=False)
            vsb = Scrollbar(lf, orient=VERTICAL, command=self.log_tv.yview)
            self.log_tv.configure(yscrollcommand=vsb.set)
            self.log_tv.pack(side=LEFT, fill=BOTH, expand=True)
            vsb.pack(side=RIGHT, fill=Y)
            self._mk_log_menu()

    def _sc(self, parent, lbl, val, col):
        """
        Status card widget.
        :param parent: parent frame
        :param lbl:    card title string
        :param val:    initial value string
        :param col:    value text color
        :return: (StringVar, Label) tuple for external updates
        """
        T = self.T
        f = Frame(parent, bg=T.surface2Color,
                  highlightbackground=T.borderColor, highlightthickness=1)
        f.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
        Label(f, text=lbl, font=(None, 10),
              fg=T.textMuted, bg=T.surface2Color).pack(anchor=W, padx=6, pady=(4, 0))
        sv = StringVar(value=val)
        lb = Label(f, textvariable=sv, font=(None, self.L.f, 'bold'),
                   fg=col, bg=T.surface2Color)
        lb.pack(anchor=W, padx=6, pady=(0, 4))
        return sv, lb

    # ── Right column (TG list) ────────────────────────────────────────────────
    def _mk_right(self, parent):
        L, T = self.L, self.T
        col = Frame(parent, bg=T.bgColor)
        col.pack(fill=BOTH, expand=True, padx=5, pady=4)
        col.pack_propagate(False)

        if not L.stacked:
            Label(col, text='TALK GROUPS', font=(None, 9, 'bold'),
                  fg=T.textMuted, bg=T.bgColor).pack(anchor=W, pady=(0, 2))
            fr = Frame(col, bg=T.bgColor)
            fr.pack(fill=X, pady=(0, 3))
            sizes = {'all': 36, 'favs': 0, 'pistar': 60}
            for tag, txt in [('all', 'ALL'), ('favs', 'FAVS ★'), ('pistar', 'PI-STAR')]:
                w = sizes[tag]
                b = _ctk_btn(fr, txt, T.surface2Color, T.textPrimary,
                             T.tgSelectedBg,
                             lambda t=tag: self._set_filt(t),
                             border_col=T.borderColor, border_w=1,
                             font=(None, 9, 'bold'), height=24,
                             width=w if w else 0)
                if w:
                    b.pack(side=LEFT, padx=2)
                else:
                    b.pack(side=LEFT, fill=X, expand=True, padx=2)
                self._filt_btns[tag] = b
            _ctk_btn(fr, '⬇', '#102030', T.accent2Color, T.tgSelectedBg,
                     lambda: self.app and self.app.update_pistar(),
                     border_col=T.accent2Color, border_w=1,
                     font=(None, 9, 'bold'), height=24, width=28
                     ).pack(side=LEFT, padx=(4, 2))
            self._set_filt('all')

        # Listbox — slightly shorter to make room for the manual entry row
        lf = Frame(col, bg=T.bgColor)
        lf.pack(fill=BOTH, expand=True)
        self.listbox = Listbox(lf, font=(None, L.f),
                       bg=T.surface2Color, fg=T.textPrimary,
                       selectbackground=T.tgSelectedBg,
                       selectforeground=T.accentColor,
                       activestyle='none',
                       relief=FLAT, bd=0,
                       highlightbackground=T.borderColor,
                       highlightthickness=1,
                       height=6,
                       exportselection=False)
        sb = Scrollbar(lf, orient=VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)
        self.listbox.bind('<Double-Button-1>', lambda _: self._connect())
        self._fill_tg(self.cfg.default_server)

        # ── Manual TG entry + G/P dropdown ───────────────────────────────────
        me = Frame(col, bg=T.bgColor)
        me.pack(fill=X, pady=(3, 0))
        self._v_manual_tg = StringVar()
        self._tg_entry_w = ctk.CTkEntry(
                    me,
                    textvariable=self._v_manual_tg,
                    placeholder_text='TG / Reflector',
                    fg_color=T.entryBgColor,
                    text_color=T.textPrimary,
                    border_color=T.borderColor,
                    border_width=1,
                    font=(None, L.f_sm),
                    height=26,
                    width=100,
                )
        self._tg_entry_w.pack(side=LEFT, padx=(0, 1))
        self._tg_entry_w.bind(
            '<Button-1>',
            lambda e: self._open_keypad() if getattr(self.cfg, 'keypad_enable', True) else None,
            add='+',
        )
        # Private call checkbox — only meaningful in DMR, right-aligned
        self._v_private = IntVar(value=0)
        ctk.CTkCheckBox(me,
                        text='Priv',
                        variable=self._v_private,
                        font=(None, L.f_sm),
                        text_color=T.textSecondary,
                        fg_color=T.accentColor,
                        checkmark_color=T.bgColor,
                        border_color=T.borderColor,
                        hover_color=T.tgSelectedBg,
                        checkbox_width=18, checkbox_height=18,
                        ).pack(side=RIGHT)

        # ── CONNECT button ────────────────────────────────────────────────────
        br = Frame(col, bg=T.bgColor)
        br.pack(fill=X, pady=(4, 0))
        _ctk_btn(br, 'CONNECT', T.connectBtnBg, T.accentColor,
                 T.connectBtnHover, self._connect,
                 border_col=T.accentColor, border_w=2,
                 font=(None, L.f_sm, 'bold'), height=28
                 ).pack(fill=X)

        # ── Connected TG name + TS dropdown on the same line ─────────────────
        ts_row = Frame(col, bg=T.bgColor)
        ts_row.pack(fill=X, pady=(3, 0))
        self._tgconn_v = StringVar(value='DISC')
        ent = Entry(ts_row, textvariable=self._tgconn_v,
            font=(None, L.f_sm, 'bold'),
            fg=T.accent2Color, bg=T.surface2Color,
            readonlybackground=T.surface2Color,
            relief=FLAT, bd=0, width=10,
            state='readonly',
            highlightthickness=0)
        ent.pack(side=LEFT, padx=(0, 4))
        # TS1/TS2 dropdown — right-aligned
        ctk.CTkOptionMenu(ts_row,
                          variable=self._v_ts_str,
                          values=['TS1', 'TS2'],
                          fg_color=T.buttonBgColor,
                          text_color=T.textPrimary,
                          button_color=T.buttonBgColor,
                          button_hover_color=T.tgSelectedBg,
                          dropdown_fg_color=T.surfaceColor,
                          dropdown_text_color=T.textPrimary,
                          font=(None, L.f_sm, 'bold'),
                          dropdown_font=(None, L.f_sm),
                          width=70, height=26,
                          ).pack(side=RIGHT)

    # ── Bottom bar ────────────────────────────────────────────────────────────
    def _mk_bottom(self, parent):
        L, T = self.L, self.T
        inn = Frame(parent, bg=T.surfaceColor)
        inn.pack(fill=BOTH, expand=True, padx=8, pady=4)

        # Single VU meter: green during RX, red during TX
        mr = Frame(inn, bg=T.surfaceColor)
        mr.pack(fill=X, pady=(0, 3))
        Label(mr, text='Level', font=(None, 10, 'bold'),
              fg=T.textMuted, bg=T.surfaceColor).pack(side=LEFT, padx=(0, 4))
        self._vu_cv = Canvas(mr, height=L.meter_h, bg=T.meterBgColor,
                             bd=0, highlightbackground=T.borderColor,
                             highlightthickness=1)
        self._vu_cv.pack(side=LEFT, fill=X, expand=True)
        # Loss label to the right of the meter — updated via v_tx_stats
        Label(mr, textvariable=self.v_tx_stats,
              font=(None, L.f_sm, 'bold'),
              fg=T.warnColor, bg=T.surfaceColor,
              width=10, anchor=E).pack(side=LEFT, padx=(6, 0))

        # PTT button
        ptt_row = Frame(inn, bg=T.surfaceColor)
        ptt_row.pack(fill=X, pady=(0, 2))
        self.ptt_btn = _ctk_btn(ptt_row, 'PTT — TRANSMIT',
                                T.pttIdleBg, T.pttIdleFg, T.pttActiveBg,
                                lambda: self.app and self.app.toggle_ptt(),
                                border_col=T.pttIdleFg, border_w=2,
                                font=(None, L.f_ptt, 'bold'),
                                height=34, state='disabled')
        self.ptt_btn.pack(fill=X)


    # ══════════════════════════════════════════════════════════════════════════
    # Settings tab
    # ══════════════════════════════════════════════════════════════════════════
    def _mk_settings(self):
        T, L = self.T, self.L
        outer = Frame(self._content, bg=T.bgColor)
        inner = Frame(outer, bg=T.bgColor)
        inner.pack(fill=BOTH, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Settings tab — tabbed sub-navigation
    # ══════════════════════════════════════════════════════════════════════════
    def _mk_settings(self):
        T, L = self.T, self.L
        outer = Frame(self._content, bg=T.bgColor)

        sv = self._sv
        sv['ip']       = StringVar(value=self.cfg.ip_address)
        sv['tx_port']  = StringVar(value=str(self.cfg.usrp_tx_port[0]))
        sv['rx_port']  = StringVar(value=str(self.cfg.usrp_rx_port))
        sv['defmode']  = StringVar(value=self.cfg.default_server)
        sv['call']     = StringVar(value=self.cfg.my_call)
        sv['sub_id']   = StringVar(value=str(self.cfg.subscriber_id))
        sv['rep_id']   = StringVar(value=str(self.cfg.repeater_id))
        sv['rx_agc']   = IntVar(value=int(getattr(self.cfg, 'rx_agc_enable', False)))
        sv['gpio']     = StringVar(value=str(self.cfg.gpio_ptt_pin))
        sv['gpio_al']  = IntVar(value=int(self.cfg.gpio_ptt_active_low))
        sv['spc']      = IntVar(value=int(self.cfg.spacebar_ptt))
        sv['keypad']   = IntVar(value=int(getattr(self.cfg, 'keypad_enable', True)))
        sv['mic']      = self.v_mic_vol
        sv['spk']      = self.v_spk_vol
        sv['ham_user'] = self._v_ham_user
        sv['ham_pass'] = self._v_ham_pass

        in_d  = _list_audio(True)  or ['Default']
        out_d = _list_audio(False) or ['Default']
        sv['in_dev']  = StringVar(value=in_d[0])
        sv['out_dev'] = StringVar(value=out_d[0])

        # ── Font and size tokens ──────────────────────────────────────────────
        fs      = L.f          # base font — larger than before (was f_sm-1)
        fs_lbl  = L.f          # label font
        fs_ttl  = L.f + 1     # group title font
        fs_tab  = round(L.f_sm*1.3)       # sub-tab button font
        ctl_h   = 34           # CTkEntry / CTkOptionMenu height
        chk_h   = 26           # CTkCheckBox height (via pady)
        sld_h   = 22           # CTkSlider height
        sub_h   = 34           # sub-tab bar height
        foot_h  = 38           # footer height
        pad_y   = 6            # row padding
        pad_x   = 12           # label left padding
        col_min = 130          # minimum label column width

        # ── Sub-tab bar ───────────────────────────────────────────────────────
        sub_tabs = [
            ('server',   'SERVER'),
            ('identity', 'IDENTITY'),
            ('audio',    'AUDIO'),
            ('gpio',     'GPIO'),
            ('pistar',   'PI-STAR'),
        ]
        sub_bar = Frame(outer, bg=T.surfaceColor, height=sub_h)
        sub_bar.pack(fill=X)
        sub_bar.pack_propagate(False)
        self._set_btns = {}

        # Content area
        sub_content = Frame(outer, bg=T.bgColor)
        sub_content.pack(fill=BOTH, expand=True)
        self._set_frms = {}

        # ── Footer ────────────────────────────────────────────────────────────
        footer = Frame(outer, bg=T.bgColor,
                       highlightbackground=T.borderColor, highlightthickness=1)
        footer.pack(fill=X, side=BOTTOM)
        Label(footer, text='HamQTH:', font=(None, fs_lbl),
              fg=T.textMuted, bg=T.bgColor).pack(side=LEFT, padx=(10, 4), pady=5)
        self._hamqth_status_lbl = Label(footer, text='—',
                                        font=(None, fs_lbl, 'bold'),
                                        fg=T.textMuted, bg=T.bgColor)
        self._hamqth_status_lbl.pack(side=LEFT, pady=5)
        self.root.after(500, self._update_hamqth_status)
        _ctk_btn(footer, 'Cancel', T.buttonBgColor, T.textSecondary,
                 T.tgSelectedBg, lambda: self._show('main'),
                 border_col=T.borderColor, border_w=1,
                 font=(None, L.f, 'bold'), height=foot_h - 8
                 ).pack(side=RIGHT, padx=6, pady=4)
        _ctk_btn(footer, 'Save settings', T.connectBtnBg, T.accentColor,
                 T.connectBtnHover, self._save,
                 border_col=T.accentColor, border_w=2,
                 font=(None, L.f, 'bold'), height=foot_h - 8
                 ).pack(side=RIGHT, pady=4)

        # ── Helper builders ───────────────────────────────────────────────────
        def mk_grp(parent, title):
            """Titled group box."""
            g = Frame(parent, bg=T.surface2Color,
                      highlightbackground=T.borderColor, highlightthickness=1)
            g.pack(fill=X, padx=10, pady=(8, 0))
            Label(g, text=title, font=(None, fs_ttl, 'bold'),
                  fg=T.accentColor, bg=T.surface2Color,
                  padx=pad_x, pady=5).grid(row=0, column=0, columnspan=4, sticky=W)
            Frame(g, bg=T.borderColor, height=1).grid(
                row=1, column=0, columnspan=4, sticky=EW)
            g.columnconfigure(0, minsize=col_min)
            g.columnconfigure(1, weight=1, minsize=150)
            g.columnconfigure(2, minsize=col_min)
            g.columnconfigure(3, weight=1, minsize=150)
            return g

        def row(g, lbl, var, r, c=0, w=15, sel=False, opts=None):
            """Label + CTkEntry or CTkOptionMenu."""
            Label(g, text=lbl, font=(None, fs_lbl),
                  fg=T.textSecondary, bg=T.surface2Color,
                  padx=pad_x).grid(row=r, column=c, sticky=W, pady=pad_y)
            if sel:
                ctk.CTkOptionMenu(g, variable=var, values=list(opts or []),
                                  fg_color=T.buttonBgColor,
                                  text_color=T.textPrimary,
                                  button_color=T.buttonBgColor,
                                  button_hover_color=T.tgSelectedBg,
                                  dropdown_fg_color=T.surfaceColor,
                                  dropdown_text_color=T.textPrimary,
                                  font=(None, fs_lbl),
                                  dropdown_font=(None, fs_lbl),
                                  width=w * 9, height=ctl_h,
                                  ).grid(row=r, column=c+1, sticky=EW,
                                         padx=(0, 8), pady=pad_y)
            else:
                ctk.CTkEntry(g, textvariable=var,
                             fg_color=T.entryBgColor,
                             text_color=T.textPrimary,
                             border_color=T.borderColor,
                             border_width=1,
                             font=(None, fs_lbl),
                             height=ctl_h,
                             ).grid(row=r, column=c+1, sticky=EW,
                                    padx=(0, 8), pady=pad_y)

        def chk(g, lbl, var, r, c=0, span=4):
            """CTkCheckBox."""
            ctk.CTkCheckBox(g, text=lbl, variable=var,
                            font=(None, fs_lbl),
                            text_color=T.textPrimary,
                            fg_color=T.accentColor,
                            checkmark_color=T.bgColor,
                            border_color=T.borderColor,
                            hover_color=T.tgSelectedBg,
                            checkbox_width=22, checkbox_height=22,
                            ).grid(row=r, column=c, columnspan=span,
                                   sticky=W, padx=pad_x, pady=chk_h // 2)

        def vol_slider(g, lbl_text, var, r, c):
            """Label + CTkSlider + value, spanning two columns."""
            Label(g, text=lbl_text, font=(None, fs_lbl),
                  fg=T.textSecondary, bg=T.surface2Color,
                  padx=pad_x).grid(row=r, column=c, sticky=W, pady=pad_y)
            sf = Frame(g, bg=T.surface2Color)
            sf.grid(row=r, column=c+1, sticky=EW, padx=(0, 8), pady=pad_y)
            sf.columnconfigure(0, weight=1)
            ctk.CTkSlider(sf, variable=var, from_=0, to=100,
                          fg_color=T.borderColor,
                          progress_color=T.accentColor,
                          button_color=T.accent2Color,
                          button_hover_color=T.accentColor,
                          height=sld_h,
                          ).grid(row=0, column=0, sticky=EW)
            Label(sf, textvariable=var, font=(None, fs_lbl, 'bold'),
                  fg=T.accentColor, bg=T.surface2Color,
                  width=3, anchor=E).grid(row=0, column=1, padx=(6, 0))

        # ── Sub-page: SERVER ──────────────────────────────────────────────────
        pg = Frame(sub_content, bg=T.bgColor)
        g = mk_grp(pg, 'DVSwitch Server')
        row(g, 'IP / Hostname', sv['ip'],      2, 0)
        row(g, 'Default mode',  sv['defmode'], 2, 2, w=9, sel=True,
            opts=sorted(self.cfg.talk_groups.keys()))
        row(g, 'TX Port',       sv['tx_port'], 3, 0, w=7)
        row(g, 'RX Port',       sv['rx_port'], 3, 2, w=7)
        self._set_frms['server'] = pg

        # ── Sub-page: IDENTITY ────────────────────────────────────────────────
        pg = Frame(sub_content, bg=T.bgColor)
        g = mk_grp(pg, 'Identity')
        row(g, 'Callsign',    sv['call'],   2, 0, w=10)
        row(g, 'DMR/CCS7 ID', sv['sub_id'], 2, 2, w=10)
        row(g, 'Repeater ID', sv['rep_id'], 3, 0, w=10)
        self._set_frms['identity'] = pg

        # ── Sub-page: AUDIO ───────────────────────────────────────────────────
        pg = Frame(sub_content, bg=T.bgColor)
        g = mk_grp(pg, 'Audio')
        row(g, 'Input device',  sv['in_dev'],  2, 0, w=16, sel=True, opts=in_d)
        vol_slider(g, 'Mic volume', sv['mic'], 2, 2)
        row(g, 'Output device', sv['out_dev'], 3, 0, w=16, sel=True, opts=out_d)
        vol_slider(g, 'Spk volume', sv['spk'], 3, 2)
        chk(g, 'AGC RX', sv['rx_agc'],  4, c=0, span=2)
        chk(g, 'VOX',      self.v_vox_en, 5, c=0, span=2)
        row(g, 'VOX threshold', self.v_vox_th, 6, 0, w=6)
        row(g, 'VOX delay',     self.v_vox_dl, 6, 2, w=6)
        self._set_frms['audio'] = pg

        # ── Sub-page: GPIO ────────────────────────────────────────────────────
        pg = Frame(sub_content, bg=T.bgColor)
        g = mk_grp(pg, 'GPIO — Physical PTT')
        row(g, 'BCM pin  (−1 = off)', sv['gpio'],    2, 0, w=5)
        row(g, 'HamQTH user',         sv['ham_user'],2, 2, w=12)
        chk(g, 'Active low (pull-up resistor)', sv['gpio_al'], 3, c=0, span=2)
        row(g, 'HamQTH pass',          sv['ham_pass'],3, 2, w=12)
        chk(g, 'Spacebar toggles PTT', sv['spc'],     4, c=0, span=2)
        chk(g, 'Numeric keypad on TG click', sv['keypad'], 5, c=0, span=2)
        self._set_frms['gpio'] = pg

        # ── Sub-page: PI-STAR ─────────────────────────────────────────────────
        pg = Frame(sub_content, bg=T.bgColor)
        g = mk_grp(pg, 'Pi-Star — Reflector lists')

        def _do_update():
            self._pistar_status_v.set('Downloading…')
            if self.app:
                self.app.update_pistar()

        pr = Frame(g, bg=T.surface2Color)
        pr.grid(row=2, column=0, columnspan=4, sticky=EW, padx=pad_x, pady=12)
        _ctk_btn(pr, '⬇  Update ALL hosts  (DMR · YSF · P25 · NXDN)',
                 T.buttonBgColor, T.warnColor, T.tgSelectedBg,
                 _do_update, border_col=T.warnColor, border_w=1,
                 font=(None, fs_lbl, 'bold'), height=ctl_h).pack(side=LEFT)
        Label(pr, textvariable=self._pistar_status_v,
              font=(None, fs_lbl), fg=T.accent2Color,
              bg=T.surface2Color).pack(side=LEFT, padx=(14, 0))
        self._set_frms['pistar'] = pg

        # ── Sub-tab buttons ───────────────────────────────────────────────────
        def _show_set(key):
            for f in self._set_frms.values():
                f.place_forget()
            h = (self.L.win_h - self.L.topbar_h - self.L.tabbar_h
                 - sub_h - foot_h - 3)
            self._set_frms[key].place(x=0, y=0,
                                      width=self.L.win_w, height=max(h, 200))
            for k, b in self._set_btns.items():
                sel = (k == key)
                b.configure(
                    text_color=T.tabActiveFg   if sel else T.tabInactiveFg,
                    fg_color=T.surface2Color   if sel else T.surfaceColor)

        for key, lbl in sub_tabs:
            b = _ctk_btn(sub_bar, lbl, T.surfaceColor, T.tabInactiveFg,
                         T.surfaceColor,
                         lambda k=key: _show_set(k),
                         font=(None, fs_tab, 'bold'),
                         height=sub_h, corner=0)
            b.pack(side=LEFT, fill=Y, padx=0)
            self._set_btns[key] = b

        _show_set('server')
        return outer

    # ══════════════════════════════════════════════════════════════════════════
    # About tab
    # ══════════════════════════════════════════════════════════════════════════
    def _mk_about(self):
        L, T = self.L, self.T
        frm = Frame(self._content, bg=T.bgColor)
        ctr = Frame(frm, bg=T.bgColor)
        ctr.place(relx=0.5, rely=0.5, anchor=CENTER)
        Label(ctr, text='pyUC', font=(None, 32, 'bold'),
              fg=T.accentColor, bg=T.bgColor).pack()
        Label(ctr, text=f'Version {UC_VERSION} — EA7HQL Edition',
              font=(None, 12), fg=T.accent2Color, bg=T.bgColor).pack(pady=(0, 10))
        ac = Frame(ctr, bg=T.surface2Color,
                   highlightbackground=T.borderColor, highlightthickness=1)
        ac.pack(fill=X, padx=20, pady=(0, 8))
        Label(ac, text='Andrés Ortiz', font=(None, 16, 'bold'),
              fg=T.textPrimary, bg=T.surface2Color).pack(pady=(8, 0))
        Label(ac, text='EA7HQL', font=(None, 13, 'bold'),
              fg=T.accentColor, bg=T.surface2Color).pack()
        Label(ac, text='UI redesign · multi-screen · AGC · GPIO PTT · Pi-Star lists',
              font=(None, L.f_sm), fg=T.textSecondary,
              bg=T.surface2Color).pack(pady=(3, 8))
        Label(ctr,
              text=('Based on pyUC — USRP Client\n'
                    'Copyright © 2014–2020 N4IRR / DVSwitch\n'
                    'Mike N4IRR & Steve N4IRS\n\n'
                    'Inspired by USRP-for-Raspberrypi — DS5QDR, Heonmin Lee\n\n'
                    'Amateur radio use only · ABSOLUTELY NO WARRANTY'),
              font=(None, L.f_sm), fg=T.aboutCreditsFg, bg=T.bgColor,
              justify=CENTER, wraplength=460).pack(pady=(0, 8))
        lnk = Label(ctr, text='github.com/DVSwitch/USRP_Client',
                    font=(None, 11), fg=T.accent2Color, bg=T.bgColor,
                    cursor='hand2')
        lnk.pack()
        lnk.bind('<Button-1>',
                 lambda _: webbrowser.open_new('https://github.com/DVSwitch/USRP_Client'))
        return frm

    # ══════════════════════════════════════════════════════════════════════════
    # UIAdapter implementation
    # ══════════════════════════════════════════════════════════════════════════
    def show_registered(self):
        if self.ptt_btn:
            st = 'disabled' if (self.cfg.in_index == -1) else 'normal'
            self.ptt_btn.configure(state=st)
        if self._reg_lbl:
            self._reg_lbl.configure(text='● REG OK', fg=self.T.greenColor)

    def show_unregistered(self):
        if self.ptt_btn:
            self.ptt_btn.configure(state='disabled')
        if self._reg_lbl:
            self._reg_lbl.configure(text='✕ Not registered', fg=self.T.redColor)

    def show_rx_begin(self, call: str, tg: str, slot: str, mode: str, name: str):
        """
        Update QRZ card header and start ON AIR animation.
        :param call: callsign
        :param tg:   talk group name
        :param slot: DMR time slot
        :param mode: 'Group' or 'Private'
        :param name: operator name from metadata
        """
        self.v_call.set(call)
        self.v_name.set(name)
        current_mode = self.v_mode.get()
        if current_mode == 'DMR':
            self.v_minfo.set(f'DMR · TS{slot} · ID {call}')
        else:
            self.v_minfo.set(f'{current_mode} · TS{slot} · {mode}')
        self._set_on_air(True)
        self._start_timer()   # Timer

    def show_rx_end(self, call: str, tg: str, loss: str, duration: float):
        """
        Log entry, clear ON AIR, show real packet loss, and start own-data revert timer.
        :param call:     callsign
        :param tg:       talk group name
        :param loss:     packet loss percentage string e.g. '2.3%'
        :param duration: seconds
        """
        self._log_add(call, tg, duration, loss)
        self._set_on_air(False)
        self._stop_timer()    # Timer

        # Show loss in the QRZ stats label, revert to — after 8 s
        if loss and loss != '0.0%':
            self.v_tx_stats.set(f'Loss: {loss}')
            self.root.after(8000, lambda: self.v_tx_stats.set('Loss: —'))
        else:
            self.v_tx_stats.set('Loss: —')
            

        # Revert to own data after timeout
        if getattr(self.cfg, 'own_data_timeout', 30) > 0 and self._own_data:
            if self._own_data_timer:
                self.root.after_cancel(self._own_data_timer)
            self._own_data_timer = self.root.after(
                getattr(self.cfg, 'own_data_timeout', 30) * 1000, self._revert_to_own_data)

    def _revert_to_own_data(self):
        """Restores the QRZ card to the operator's own data after timeout."""
        self._own_data_timer = None
        if self._own_data:
            call, photo, name, grid, city = self._own_data
            self._display_qrz(call, photo, name, grid, city)

    def show_ptt(self, state: bool):
        """
        Update PTT button appearance and TX meter.
        :param state: True = transmitting
        """
        T = self.T
        self._tx_active = state
        if not self.ptt_btn:
            return
        if state:
            self.ptt_btn.configure(
                text='● TX ACTIVE — PTT ON',
                fg_color=T.pttActiveBg,
                text_color=T.pttActiveFg,
                border_color=T.pttActiveFg)
            self._start_timer()
            self.v_tx_stats.set('Loss: —')
            self._set_on_air(True)
        else:
            self.ptt_btn.configure(
                text='PTT — TRANSMIT',
                fg_color=T.pttIdleBg,
                text_color=T.pttIdleFg,
                border_color=T.pttIdleFg)
            self.v_tx_stats.set('Loss: —')
            self._tx_level = 0
            self._set_on_air(False)
            self._stop_timer()    # 

    def show_mode(self, mode: str, last_tg: str):
        """
        Update mode button highlight and TG list.
        :param mode:    new mode
        :param last_tg: dial string to pre-select
        """
        self._set_on_air(False)
        self._upd_mode_btn(mode.upper())
        self._fill_tg(mode.upper())
        if last_tg:
            self._sel_tg_val(last_tg)
            # Buscar nombre amigable del TG y mostrarlo
            tg_name = last_tg
            for name, dial in self.cfg.talk_groups.get(mode.upper(), []):
                if dial == last_tg:
                    tg_name = name
                    break
            self._tgconn_full = tg_name
            self._stop_scroll()
            if self._tgconn_v:
                self._tgconn_v.set(tg_name)
            self.root.after(500, self._start_scroll)
            
    def _marquee_tg(self):
        """Desplaza el texto del TG conectado en bucle."""
        if not self._tgconn_v:
            return
        full = self._tgconn_full_text
        # Si el texto es corto, lo mostramos estático
        if len(full) <= 12:
            self._tgconn_v.set(full)
            self._marquee_id = None
            return
        # Añadir separador al final del texto original para el bucle
        padded = full + '   ' + full
        visible = padded[self._marquee_pos:self._marquee_pos + 12]
        self._tgconn_v.set(visible)
        self._marquee_pos = (self._marquee_pos + 1) % (len(full) + 3)
        self._marquee_id = self.root.after(350, self._marquee_tg)
    
    def _start_scroll(self):
        """Inicia el scroll animado si el texto no cabe."""
        if not self._tgconn_lbl:
            return
        if self._tgconn_scrolling:
            return
        texto = self._tgconn_full
        lbl = self._tgconn_lbl
        lbl.update_idletasks()
        if lbl.winfo_width() > 1 and lbl.winfo_reqwidth() > lbl.winfo_width():
            self._tgconn_scrolling = True
            self._tgconn_offset = 0
            doble = '   '.join([texto] * 3)
            self._tgconn_anim_id = self.root.after(
                500, lambda: self._animate_scroll(doble))

    def _stop_scroll(self):
        """Detiene la animación de scroll."""
        self._tgconn_scrolling = False
        if self._tgconn_anim_id:
            self.root.after_cancel(self._tgconn_anim_id)
            self._tgconn_anim_id = None

    def _animate_scroll(self, texto_doble):
        """Desplaza el texto carácter a carácter."""
        if not self._tgconn_lbl:
            return
        if not self._tgconn_scrolling:
            return
        offset = self._tgconn_offset
        visible = texto_doble[offset:offset + 14]
        self._tgconn_v.set(visible)
        self._tgconn_offset = (offset + 1) % (len(texto_doble) // 3)
        self._tgconn_anim_id = self.root.after(
            250, lambda: self._animate_scroll(texto_doble))
        
    def show_connected(self, tg_name: str):
        """
        Update connected-TG status card and start marquee.
        :param tg_name: friendly name
        """
        self._tgconn_full = tg_name
        self._stop_scroll()
        if self._tgconn_v:
            self._tgconn_v.set(tg_name)
        self.root.after(500, self._start_scroll)

    def show_disconnected(self):
        """Clear connected-TG status card and stop marquee."""

        self._stop_scroll()
        self._tgconn_full = 'DISC'
        if self._tgconn_v:
            self._tgconn_v.set('DISC')
            

    def show_photo(self, call: str, photo, name: str, grid: str, city: str):
        """
        Update QRZ card with resolved callsign data.
        If it is the operator's own callsign, store it as own data.
        :param call:  callsign
        :param photo: PIL.Image or None
        :param name:  operator full name
        :param grid:  Maidenhead locator
        :param city:  city/country string
        """
        # For own callsign: use cfg.my_locator if HamQTH returned nothing
        if call.upper() == self.cfg.my_call.upper():
            if not grid and self.cfg.my_locator:
                grid = self.cfg.my_locator

        # Cancel any pending revert when a new station is received
        if call.upper() != self.cfg.my_call.upper():
            if self._own_data_timer:
                self.root.after_cancel(self._own_data_timer)
                self._own_data_timer = None

        self._display_qrz(call, photo, name, grid, city)

        # Store own data for later revert
        if call.upper() == self.cfg.my_call.upper():
            self._own_data = (call, photo, name, grid, city)

    def _display_qrz(self, call: str, photo, name: str, grid: str, city: str):
        """
        Renders callsign data into the QRZ card widgets.
        :param call:  callsign
        :param photo: PIL.Image or None
        :param name:  operator full name
        :param grid:  Maidenhead locator
        :param city:  city/country string
        """
        if not self.qrz_lbl:
            return
        if photo and PIL_OK:
            # Force exact frame size — thumbnail only shrinks, resize handles both
            photo_fit = photo.resize((self.L.qrz_w, self.L.qrz_h), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(photo_fit)
            self.qrz_lbl.configure(image=tk_img, text='')
            self.qrz_lbl.image = tk_img
        else:
            self.qrz_lbl.configure(image='', text='QRZ\nphoto')
            self.qrz_lbl.image = None
        self.qrz_lbl.callsign = call
        self.v_call.set(call)
        self.v_name.set(name)
        self.v_qth_info.set(city if city else '')

        final_grid = grid
        if not final_grid:
            import re
            m = re.search(r'\b([A-R]{2}\d{2}[A-X]{2})\b', name, re.IGNORECASE)
            if m:
                final_grid = m.group(1).upper()

        my_grid = self.cfg.my_locator
        if final_grid:
            # Don't show distance when displaying own data
            if call.upper() == self.cfg.my_call.upper():
                self.v_loc_dist.set(final_grid)
            else:
                d_str = final_grid
                if my_grid:
                    dist = calc_distance_km(my_grid, final_grid)
                    if dist:
                        d_str += f'   dst: {int(dist)} km'
                self.v_loc_dist.set(d_str)
        else:
            self.v_loc_dist.set('')

        # Show DMR ID in the info line when displaying own data
        if call.upper() == self.cfg.my_call.upper():
            self.v_minfo.set(f'DMR ID: {self.cfg.subscriber_id}')

    def show_status(self, text: str, color: str, temporary: bool = False):
        """
        Update the Status card.
        :param text:      status string
        :param color:     hex color for the text
        :param temporary: if True, reverts to previous status after 3 s
        """
        if not self._status_lbl:
            return
        if not temporary:
            self._actual_status = (text, color)
            if self._status_timer:
                return
        self._status_v.set(text)
        self._status_lbl.configure(fg=color)
        if self._status_timer:
            self.root.after_cancel(self._status_timer)
            self._status_timer = None
        if temporary:
            self._status_timer = self.root.after(3000, self._restore_status)

    def _restore_status(self):
        """Restores permanent status after temporary message expires."""
        self._status_timer = None
        if self._status_lbl:
            t, c = self._actual_status
            self._status_v.set(t)
            self._status_lbl.configure(fg=c)

    def show_toast(self, title: str, message: str):
        """
        Show a non-blocking overlay notification.
        Also updates the inline Pi-Star status label for update-related messages.
        :param title:   short title
        :param message: body text
        """
        if title in ('Pi-Star', 'Update', 'Database'):
            self._pistar_status_v.set(f'{title}: {message}')
        if self._toast_win:
            try: self._toast_win.destroy()
            except Exception: pass
        T = self.T
        w = Toplevel(self.root)
        w.overrideredirect(True)
        x = self.root.winfo_x() + self.L.win_w - 260
        y = self.root.winfo_y() + self.L.win_h - 60
        w.geometry(f'+{x}+{y}')
        w.configure(bg=T.surface2Color)
        Label(w, text=f'{title}: {message}',
              font=(None, self.L.f_sm),
              fg=T.textPrimary, bg=T.surface2Color,
              padx=10, pady=5).pack()
        self._toast_win = w
        w.after(3500, self._toast_fade)

    def _toast_fade(self):
        """Fades out the toast notification."""
        if not self._toast_win:
            return
        try:
            a = self._toast_win.attributes('-alpha')
            if a > 0.08:
                self._toast_win.attributes('-alpha', a - 0.08)
                self._toast_win.after(60, self._toast_fade)
            else:
                self._toast_win.destroy()
                self._toast_win = None
        except Exception:
            try:   self._toast_win.destroy()
            except Exception: pass
            self._toast_win = None

    def show_tg_added(self, mode: str, tg_name: str, tg_value: str):
        """
        Refresh TG list when a new entry is dynamically added.
        :param mode:     radio mode
        :param tg_name:  display name
        :param tg_value: dial string
        """
        self._fill_tg(self.v_mode.get())
        self._sel_tg_val(tg_value)

    def show_audio_level(self, level: int):
        """
        Store audio level (called from audio thread — no UI ops here).
        Routes to TX or RX meter based on PTT state.
        :param level: 0–100 RMS level
        """
        if self._tx_active:
            self._tx_level = min(level, 100)
        else:
            self._rx_level = min(level, 100)

    def show_sysmon(self, cpu: float, ram: float, temp: str):
        """
        Update system stats label in topbar.
        :param cpu:  CPU %
        :param ram:  RAM %
        :param temp: temperature string
        """
        self.v_sys.set(f'CPU {cpu:.0f}%  RAM {ram:.0f}%  {temp}')

    def show_transmit_enable(self, enabled: bool):
        """
        Enable or disable PTT button (inhibit during remote RX).
        :param enabled: True = PTT allowed
        """
        if self.ptt_btn:
            self.ptt_btn.configure(state='normal' if enabled else 'disabled')

    def run(self):
        """Start pump and enter CTk main loop."""
        self.root.after(100, self._pump)
        self.root.mainloop()

    # ══════════════════════════════════════════════════════════════════════════
    # Pump — called every 100 ms from main thread
    # ══════════════════════════════════════════════════════════════════════════
    def _pump(self):
        """Dispatches app IPC queue and updates meters."""
        try:
            if self.app:
                self.app.pump()
        except Exception as exc:
            logging.error("pump error: %s", exc, exc_info=True)

        # Single VU meter: red during TX (real mic level), green during RX
        if self._tx_active:
            self._meter(self._vu_cv, self._tx_level, self.T.redColor)
        else:
            self._meter(self._vu_cv, self._rx_level, self.T.greenColor)

        # HamQTH status label — update every ~2 s (every 20 pump cycles)
        self._pump_tick = getattr(self, '_pump_tick', 0) + 1
        if self._pump_tick % 20 == 0 and self._hamqth_status_lbl and self.app:
            self._update_hamqth_status()

        self.root.after(100, self._pump)

    def _update_hamqth_status(self):
        """Reads HamQTH session status and updates the footer label."""
        T   = self.T
        lbl = self._hamqth_status_lbl
        if not lbl or not self.app:
            return
        st = getattr(self.app, '_hamqth', None)
        if st is None:
            return
        status = st.status
        if status == 'connected':
            lbl.configure(text='✓ Connected', fg=T.greenColor)
        elif status == 'disabled':
            lbl.configure(text='— Disabled (no credentials)', fg=T.textMuted)
        else:
            lbl.configure(text='✗ Not authenticated', fg=T.redColor)

    # ══════════════════════════════════════════════════════════════════════════
    # ON AIR animation
    # ══════════════════════════════════════════════════════════════════════════
    def _set_on_air(self, state: bool):
        """Start or stop ON AIR label animation."""
        self._is_transmitting = state
        if state:
            # Cancel any previous animation before starting a new one
            if hasattr(self, '_onair_after_id') and self._onair_after_id:
                try: self.root.after_cancel(self._onair_after_id)
                except Exception: pass
            self._onair_after_id = None
            self._animate_onair(0.0)
        elif self.on_air_lbl:
            self._is_transmitting = False
            # Cancel any pending after() so animation stops immediately
            if self._onair_after_id:
                try: self.root.after_cancel(self._onair_after_id)
                except Exception: pass
                self._onair_after_id = None
            self.on_air_lbl.configure(fg=self.T.surface2Color)

    def _animate_onair(self, phase: float):
        """
        Recursive sine-wave red fade on the ON AIR label.
        :param phase: current animation phase in radians
        """
        if not self._is_transmitting or not self.on_air_lbl:
            self._onair_after_id = None
            return
        intensity = (math.sin(phase) + 1) / 2
        try:
            r_bg, g_bg, b_bg = [v // 256 for v in
                                 self.root.winfo_rgb(self.T.surface2Color)]
            r = int(r_bg + (255 - r_bg) * intensity)
            g = int(g_bg + (30  - g_bg) * intensity)
            b = int(b_bg + (30  - b_bg) * intensity)
            self.on_air_lbl.configure(fg=f'#{r:02x}{g:02x}{b:02x}')
        except Exception:
            pass
        self._onair_after_id = self.root.after(
            60, lambda: self._animate_onair(phase + 0.15))

    # ══════════════════════════════════════════════════════════════════════════
    # Meters
    # ══════════════════════════════════════════════════════════════════════════
    def _meter(self, cv, pct: float, color: str):
        """
        Dibuja una barra de nivel en un Canvas, reutilizando un único rectángulo
        persistente (coords) en lugar de borrar y recrear en cada ciclo.
        :param cv:    widget Canvas (o None)
        :param pct:   porcentaje de llenado 0–100
        :param color: color de la barra (hex)
        No devuelve nada.
        """
        if not cv:
            return
        w = getattr(cv, '_cached_w', 0)
        if w < 2:
            w = cv.winfo_width()
            if w > 1:
                cv._cached_w = w
            else:
                return
        h = getattr(cv, '_cached_h', 0)
        if h < 2:
            h = cv.winfo_height()
            if h > 1:
                cv._cached_h = h
            else:
                return

        fw = int(w * min(pct, 100) / 100)
        # Salir si nada cambió respecto al último frame pintado
        if getattr(cv, '_last_fw', -1) == fw and getattr(cv, '_last_col', None) == color:
            return
        cv._last_fw  = fw
        cv._last_col = color

        rect = getattr(cv, '_bar_id', None)
        if rect is None:
            cv._bar_id = cv.create_rectangle(0, 0, fw, h, fill=color, outline='')
        else:
            cv.itemconfigure(rect, fill=color)
            cv.coords(rect, 0, 0, fw, h)

    # ══════════════════════════════════════════════════════════════════════════
    # TG list helpers
    # ══════════════════════════════════════════════════════════════════════════
    def _fill_tg(self, mode: str):
        """Reload listbox with visible TGs for the given mode."""
        if not self.listbox:
            return
        self.listbox.delete(0, END)
        for name, _ in self._vis(mode):
            self.listbox.insert(END, name)
        if self.listbox.size():
            self.listbox.selection_set(0)

    def _vis(self, mode: str) -> list:
        """
        Returns visible (name, dial) pairs for the current filter.
        For 'favs': reads directly from cfg.favorites (independent of talk_groups
        contents, so it survives Pi-Star host updates).
        :param mode: radio mode key (matched case-insensitively)
        :return: list of (name, dial) tuples
        """
        if self._filt == 'favs':
            return [(n, d) for n, (m, d) in self.cfg.favorites.items()
                    if m.upper() == mode.upper()]
        # 'all' and 'pistar' both show the full talk_groups list
        return self.cfg.talk_groups.get(mode, [])

    def _cur_tg(self):
        """Returns (dial, name) of the currently selected listbox entry."""
        if not self.listbox:
            return '', ''
        sel = self.listbox.curselection()
        if not sel:
            return '', ''
        v = self._vis(self.v_mode.get())
        i = sel[0]
        return (v[i][1], v[i][0]) if i < len(v) else ('', '')

    def _sel_tg_val(self, val: str):
        """Highlight the listbox row matching dial string val."""
        vis = self._vis(self.v_mode.get())
        for i, (_, d) in enumerate(vis):
            if d.translate(self._nq) == val:
                self.listbox.selection_clear(0, END)
                self.listbox.selection_set(i)
                return

    def _set_filt(self, tag: str):
        """
        Switch TG list filter.
        :param tag: 'all' | 'favs' | 'pistar'
        """
        T = self.T
        self._filt = tag
        for t, b in self._filt_btns.items():
            sel = (t == tag)
            b.configure(
                fg_color=T.tgSelectedBg  if sel else T.surface2Color,
                text_color=T.accentColor if sel else T.textPrimary,
                border_color=T.accentColor if sel else T.borderColor)
        self._fill_tg(self.v_mode.get())

    # ══════════════════════════════════════════════════════════════════════════
    # Mode selection
    # ══════════════════════════════════════════════════════════════════════════
    def _sel_mode(self, mode: str):
        """Handle mode button click."""
        self.v_mode.set(mode)
        self._upd_mode_btn(mode)
        self._fill_tg(mode)
        if self.app:
            self.app.set_mode(mode)

    def _upd_mode_btn(self, active: str):
        """
        Highlight active mode button, reset others.
        :param active: mode key of the active button
        """
        T = self.T
        if not self._mode_btns:
            return
        for m, b in self._mode_btns.items():
            sel = (m == active)
            b.configure(
                fg_color=T.modeBtnActiveBg     if sel else T.modeBtnBg,
                text_color=T.modeBtnActiveFg   if sel else T.modeBtnFg,
                border_color=T.modeBtnActiveBorder if sel else T.modeBtnBorder)

    # ══════════════════════════════════════════════════════════════════════════
    # Connect / PTT
    # ══════════════════════════════════════════════════════════════════════════
    def _open_keypad(self):
        """
        Opens (or closes if already open) a walkie-talkie style numeric keypad
        as a Toplevel positioned below the TG entry widget.
        Writes digits to _v_manual_tg; CALL closes the popup and fires _connect().
        No params. No return value.
        """
        T, L = self.T, self.L

        # Toggle: second click on the entry closes the popup
        if self._kpad and self._kpad.winfo_exists():
            self._kpad.destroy()
            self._kpad = None
            return

        popup = Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=T.borderColor)   # 1-px border via bg
        self._kpad = popup

        inner = Frame(popup, bg=T.surfaceColor, padx=2, pady=2)
        inner.pack(fill=BOTH, expand=True, padx=1, pady=1)

        # ── Display ──────────────────────────────────────────────────────────
        disp_var = StringVar(value=self._v_manual_tg.get())
        disp = Entry(
            inner,
            textvariable=disp_var,
            font=(None, L.f + 4, 'bold'),
            fg=T.accentColor, bg=T.entryBgColor,
            relief=FLAT, bd=0,
            highlightthickness=1,
            highlightbackground=T.borderColor,
            highlightcolor=T.accentColor,
            justify=CENTER,
            width=10,
        )
        disp.pack(fill=X, padx=4, pady=(6, 2))
        disp.focus_set()

        # ── Button grid ──────────────────────────────────────────────────────
        grid_frm = Frame(inner, bg=T.surfaceColor)
        grid_frm.pack(fill=BOTH, expand=True, padx=4, pady=2)

        btn_font = (None, L.f + 3, 'bold')
        PAD = 3

        def _btn(parent, text, fg, bg, cmd, rowspan=1, colspan=1, r=0, c=0):
            """
            Creates and grids a Button.
            parent: container frame
            text: label
            fg: foreground color
            bg: background color
            cmd: command callback
            rowspan, colspan: grid span
            r, c: grid row and column
            """
            b = Button(
                parent, text=text, font=btn_font,
                fg=fg, bg=bg,
                activeforeground=T.accentColor,
                activebackground=T.surface2Color,
                relief=FLAT, cursor='hand2',
            )
            b.grid(row=r, column=c, rowspan=rowspan, columnspan=colspan,
                   padx=PAD, pady=PAD, sticky=NSEW, ipadx=8, ipady=6)
            b.configure(command=cmd)
            return b

        def press(ch):
            disp_var.set(disp_var.get() + ch)

        def backspace():
            disp_var.set(disp_var.get()[:-1])

        def clear():
            disp_var.set('')

        def call():
            self._v_manual_tg.set(disp_var.get())
            _close()
            self._connect()

        def _close():
            self._kpad = None
            popup.destroy()

        # Digits 1-9, *, 0, #
        keys = [('1','2','3'), ('4','5','6'), ('7','8','9'), ('*','0','#')]
        for ri, row in enumerate(keys):
            for ci, ch in enumerate(row):
                _btn(grid_frm, ch, T.textPrimary, T.buttonBgColor,
                     lambda c=ch: press(c), r=ri, c=ci)

        # ⌫ (2 cols) and CLR (1 col)
        _btn(grid_frm, '⌫', T.warnColor,  T.buttonBgColor, backspace, r=4, c=0, colspan=2)
        _btn(grid_frm, 'ESC', T.redColor, T.buttonBgColor, _close,    r=4, c=2)

        for col in range(3):
            grid_frm.columnconfigure(col, weight=1)
        for row in range(5):
            grid_frm.rowconfigure(row, weight=1)

        # ── CALL button ───────────────────────────────────────────────────────
        Button(
            inner, text='CALL', font=(None, L.f + 3, 'bold'),
            fg=T.bgColor, bg=T.accentColor,
            activeforeground=T.bgColor, activebackground=T.greenColor,
            relief=FLAT, cursor='hand2',
            command=call,
        ).pack(fill=X, padx=4, pady=(2, 6), ipady=6)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        popup.bind('<Return>',    lambda e: call())
        popup.bind('<Escape>',    lambda e: _close())
        popup.bind('<BackSpace>',  lambda e: backspace())
        for ch in '0123456789*#':
            popup.bind(ch, lambda e, c=ch: press(c))

        # ── Positioning: below the TG entry, clamped to screen ───────────────
        popup.update_idletasks()
        ew = self._tg_entry_w
        if ew and ew.winfo_exists():
            ex = ew.winfo_rootx()
            ey = ew.winfo_rooty() + ew.winfo_height() + 2
        else:
            ex = self.root.winfo_rootx() + 20
            ey = self.root.winfo_rooty() + 100

        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ex = min(ex, sw - pw - 4)
        ey = min(ey, sh - ph - 4)

        popup.geometry(f'{pw}x{ph}+{ex}+{ey}')
        popup.grab_set()

    def _connect(self):
        """
        Connect to a talk group.
        Priority: manual entry > listbox selection.
        In DMR, appends '#' for private calls (P).
        """
        if not self.app:
            return
        manual = self._v_manual_tg.get().strip() if self._v_manual_tg else ''
        if manual:
            dial = manual
            name = manual
            self._v_manual_tg.set('')
        else:
            dial, name = self._cur_tg()
    
        # Añadir sufijo '#' para llamadas privadas DMR
        if dial and not dial.startswith('*'):
            if (self._v_private and self._v_private.get()
                    and self.v_mode.get() == 'DMR'
                    and not dial.endswith('#')):
                dial = dial + '#'
                name = name + ' (P)'
    
        if dial:
            self.app.connect(dial, name)

    def _spc_ptt(self):
        """Spacebar PTT handler — only fires when PTT button is enabled."""
        if self.ptt_btn and self.ptt_btn.cget('state') == 'normal':
            if self.app:
                self.app.toggle_ptt()

    # ══════════════════════════════════════════════════════════════════════════
    # Log
    # ══════════════════════════════════════════════════════════════════════════
    def _log_add(self, call: str, tg: str, dur: float, loss: str):
        """
        Append a row to the RX log treeview.
        :param call: callsign
        :param tg:   talk group name
        :param dur:  duration in seconds
        :param loss: packet loss string
        """
        if not self.log_tv:
            return
        self.log_tv.insert('', END, values=(
            call.strip()[:9], strftime('%H:%M:%S', localtime()),
            tg[:18], f'{dur:.1f}s', loss or '0.00%'))
        self.root.after(200, lambda: self.log_tv.yview_moveto(1))

    def _style_tv(self):
        """Apply dark theme to ttk.Treeview."""
        T = self.T
        s = ttk.Style(self.root)
        try: s.theme_use('clam')
        except Exception: pass
        s.configure('Treeview', background=T.surface2Color,
                    fieldbackground=T.surface2Color,
                    foreground=T.textPrimary, rowheight=20)
        s.configure('Treeview.Heading', background=T.surfaceColor,
                    foreground=T.textMuted, font=(None, 10, 'bold'))
        s.map('Treeview',
              background=[('selected', T.tgSelectedBg)],
              foreground=[('selected', T.accentColor)])

    def _mk_log_menu(self):
        """Create right-click context menu for the log treeview."""
        T = self.T
        self._lmenu = Menu(self.root, tearoff=0,
                           bg=T.surfaceColor, fg=T.textPrimary)
        self._lmenu.add_command(label='QRZ',
            command=lambda: self._log_open('http://www.qrz.com/lookup/'))
        self._lmenu.add_command(label='aprs.fi',
            command=lambda: self._log_open('https://aprs.fi/#!call=a%2F'))
        self._lmenu.add_command(label='Brandmeister',
            command=lambda: self._log_open(
                'https://brandmeister.network/?page=profile&call='))
        self.log_tv.bind('<Button-2>', self._lm_pop)
        self.log_tv.bind('<Button-3>', self._lm_pop)

    def _lm_pop(self, ev):
        """Show log context menu on right-click."""
        iid = self.log_tv.identify_row(ev.y)
        if iid:
            self.log_tv.selection_set(iid)
            self._lmenu.post(ev.x_root, ev.y_root)

    def _log_open(self, base: str):
        """Open a web page for the selected log callsign."""
        sel = self.log_tv.selection()
        if sel:
            c = self.log_tv.item(sel[0])['values'][0].strip()
            if c and not c.isdigit():
                webbrowser.open_new_tab(base + c)

    # ══════════════════════════════════════════════════════════════════════════
    # QRZ photo click
    # ══════════════════════════════════════════════════════════════════════════
    def _qrz_click(self, ev):
        """Open QRZ page for the callsign shown on the photo widget."""
        cs = getattr(ev.widget, 'callsign', '')
        if cs:
            webbrowser.open_new_tab('http://www.qrz.com/lookup/' + cs)

    # ══════════════════════════════════════════════════════════════════════════
    # Dialogs
    # ══════════════════════════════════════════════════════════════════════════
    def _ask_exit(self):
        if messagebox.askyesno('Exit pyUC?',
                               'Unregister from DVSwitch and exit?',
                               parent=self.root):
            self._close()

    def _ask_shutdown(self):
        if messagebox.askyesno('Shut down Raspberry Pi?',
                               'Power off the system completely?',
                               icon='warning', parent=self.root):
            if self.app:
                self.app.stop()
            try:
                subprocess.run(['sudo', 'shutdown', '-h', 'now'], check=False)
            except Exception:
                pass
            self.root.destroy()

    def _close(self):
        self.root.destroy()

    # ══════════════════════════════════════════════════════════════════════════
    # Settings save
    # ══════════════════════════════════════════════════════════════════════════
    def _apply_theme_preview(self, mode: str):
        """
        Live partial theme preview via CTk appearance mode.
        Updates CTk widgets immediately; native tk widgets and inline frames
        require a restart for full effect.
        :param mode: 'dark' | 'light'
        """
        ctk.set_appearance_mode(mode)
        self.cfg.theme_mode = mode

    def _save(self):
        """Validate and persist settings via app.save_settings()."""
        sv = self._sv
        try:
            self.cfg.usrp_tx_port  = [int(sv['tx_port'].get())]
            self.cfg.usrp_rx_port  = int(sv['rx_port'].get())
            self.cfg.subscriber_id = int(sv['sub_id'].get())
            self.cfg.repeater_id   = int(sv['rep_id'].get())
        except ValueError:
            messagebox.showerror('Invalid', 'Ports and IDs must be integers.',
                                 parent=self.root)
            return
        self.cfg.ip_address          = sv['ip'].get().strip()
        self.cfg.my_call             = sv['call'].get().strip().upper()
        self.cfg.spacebar_ptt        = bool(sv['spc'].get())
        self.cfg.keypad_enable       = bool(sv['keypad'].get())
        self.cfg.gpio_ptt_pin        = int(sv['gpio'].get())
        self.cfg.gpio_ptt_active_low = bool(sv['gpio_al'].get())
        if hasattr(self.cfg, 'rx_agc_enable'):
            self.cfg.rx_agc_enable   = bool(sv['rx_agc'].get())
        self.cfg.mic_vol             = sv['mic'].get()
        self.cfg.spk_vol             = sv['spk'].get()
        self.cfg.ham_user            = self._v_ham_user.get().strip()
        self.cfg.ham_pass            = self._v_ham_pass.get().strip()

        # Capture current log column widths (user may have dragged them)
        if self.log_tv:
            cols = ('Call', 'Time', 'TG', 'Dur', 'Loss')
            try:
                self.cfg.log_col_widths = [
                    self.log_tv.column(c, 'width') for c in cols
                ]
            except Exception:
                pass

        if self.app:
            ini = getattr(self.app, '_ini_path', '')
            if ini:
                self.app.save_settings(ini)

        self.show_toast('Settings', 'Saved — reconnect to apply')
        self._show('main')
