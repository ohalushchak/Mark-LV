from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QFontMetrics, QGuiApplication, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QSplitter,
    QGraphicsScene, QGraphicsView,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None

try:
    from core.orb_hud import OrbHud
except Exception:      # pragma: no cover — same rule
    OrbHud = None

try:
    from core.anime_avatar import AnimeAvatar
except Exception:      # pragma: no cover — same rule
    AnimeAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "MARK LV"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_STATUS_KEYS = ("ACC", "ACC2", "GREEN", "GREEN_D", "RED", "MUTED_C")
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]

# Base palettes the accent hue is then applied to (memory.config_manager
# .UI_THEMES). "soft" is the AcaneloL/Mark-LIV fork's palette: slate instead of
# near-black, desaturated cyan, warm status colours — easier on the eyes over
# a long session. Its RED and MUTED_C differ slightly (the fork had them equal)
# so a live switch back can tell them apart.
_THEMES: dict[str, dict[str, str]] = {
    "neon": {k: getattr(C, k) for k in _HUE_LINKED + _STATUS_KEYS},
    "soft": {
        "BG": "#14181e", "PANEL": "#1a1f27", "PANEL2": "#1d232c",
        "BORDER": "#2c3642", "BORDER_B": "#3d5566", "BORDER_A": "#34485a",
        "PRI": "#6fb4c8", "PRI_DIM": "#4a7f8f", "PRI_GHO": "#1c2a33",
        "TEXT": "#d5dde5", "TEXT_DIM": "#8793a0", "TEXT_MED": "#aab6c2",
        "WHITE": "#eef2f5", "DARK": "#12161b", "BAR_BG": "#222a33",
        "ACC": "#d9955f", "ACC2": "#d8b85c", "GREEN": "#6cc4a0",
        "GREEN_D": "#4f9d86", "RED": "#e0707a", "MUTED_C": "#e27886",
    },
}


def apply_ui_theme(name: str) -> bool:
    """Switch the base palette. The accent hue is re-applied on top by the
    caller (apply_ui_accent keeps each base colour's saturation and
    brightness, so a soft theme stays soft in any hue)."""
    base = _THEMES.get(name)
    if base is None:
        return False
    for k, v in base.items():
        setattr(C, k, v)
        if k in _PALETTE_DEFAULTS:
            _PALETTE_DEFAULTS[k] = v
    return True


def _mix_hex(a: str, b: str, t: float) -> str:
    """`b` laid over `a` at opacity t, as an opaque hex colour — for
    stylesheet tints that must follow the theme (live re-theming swaps hex
    strings, so a tint has to be a hex string too)."""
    qa, qb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return "#{:02x}{:02x}{:02x}".format(
        *(int(x + (y - x) * t + 0.5) for x, y in
          ((qa.red(), qb.red()), (qa.green(), qb.green()), (qa.blue(), qb.blue()))))


def _glass(hex_color: str, alpha: int) -> str:
    """A translucent overlay background in the theme's colour."""
    q = QColor(hex_color)
    return f"rgba({q.red()}, {q.green()}, {q.blue()}, {int(alpha)})"


# ── Type ─────────────────────────────────────────────────────────────────────
# One monospace face per OS (from the AcaneloL fork): Courier New was thin and
# blurry at the HUD's small sizes. Every explicit font goes through _font(), so
# the TEXT SIZE setting (madshadowtest fork) scales all of them at once. It is
# read once at import; widgets are sized when built, so a change needs a
# restart rather than a half-resized window.
_MONO = ("Consolas" if _OS == "Windows"
         else "Menlo" if _OS == "Darwin" else "DejaVu Sans Mono")

try:
    from memory.config_manager import get_ui_scale as _get_ui_scale
    _UI_SCALE = _get_ui_scale()
except Exception:
    _UI_SCALE = 1.0


def _font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(_MONO)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(max(5.0, float(size) * _UI_SCALE))
    f.setWeight(weight)
    return f


def _draw_fit(p: QPainter, rect: QRectF, flags, text: str) -> None:
    """drawText that shrinks the font until `text` fits the rect's width, so a
    larger TEXT SIZE never clips a fixed-width panel."""
    f = QFont(p.font())
    size = f.pointSizeF()
    while size > 5 and p.fontMetrics().horizontalAdvance(str(text)) > rect.width() - 6:
        size -= 0.5
        f.setPointSizeF(size)
        p.setFont(f)
    p.drawText(rect, flags, text)


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the themed colours currently on class C — the accent-
    linked ones and the status colours a theme switch also changes."""
    return {k: getattr(C, k) for k in _HUE_LINKED + _STATUS_KEYS}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    # One pass over each sheet: replacing colour by colour could turn a new
    # value back into an old one when two palettes share a hex.
    hexes = re.compile(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])")
    swap = lambda m: mapping.get(m.group(0).lower(), m.group(0))
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = hexes.sub(swap, ss)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # The anime face: HoloAvatar's animation with its own drawing, kept as
        # a separate object so each face keeps its own blink and gaze.
        self._anime = None
        if AnimeAvatar is not None:
            try:
                self._anime = AnimeAvatar()
            except Exception:
                self._anime = None

        # The wireframe orb, the third centrepiece. Same soft-fail rule.
        self._orb = None
        if OrbHud is not None:
            try:
                self._orb = OrbHud()
            except Exception:
                self._orb = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; every object is kept either way so switching back
        # is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _head(self):
        """The face the current style draws, or None for the core and orb."""
        if self.hud_style == "face":
            return self._avatar
        if self.hud_style == "anime":
            return self._anime
        return None

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            head = self._head()
            if head is not None:
                head.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        _hd = self._head()
        if _hd is not None:
            _hd.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        elif self._orb is not None and self.hud_style == "orb":
            self._orb.step(dt, amp, speaking=self.speaking,
                           muted=self.muted, state=self.state)
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def can_show(self, style: str) -> bool:
        """Whether this canvas can draw `style` — a centrepiece whose module
        failed to load is skipped by the toggle instead of showing as the
        reactor under another name."""
        if style == "face":
            return self._avatar is not None
        if style == "anime":
            return self._anime is not None
        if style == "orb":
            return self._orb is not None
        return style == "core"

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont(_MONO, fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        _hd = self._head()
        if _hd is not None:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (_hd.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - _hd.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            _hd.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # wireframe orb — rings reach ~1.2 r with a full voice, so the radius
        # leaves that much room inside the band.
        elif self._orb is not None and self.hud_style == "orb":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.40, _band_h / 2.5)
            _main, _acc = self._core_colours()
            self._orb.paint(p, cx, _band_t + _band_h / 2.0, _r, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # or the orb could not be built. The old face.png branch was
        # unreachable (no such file ships) and the bare orb it fell through to
        # is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(_font(11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class ReplyCaption(QTextEdit):
    """The latest exchange under the HUD, typed out like subtitles.

    From the Nathan123y/Jarvis-for-Nathan fork, which replaced the activity log
    with it. Here it is an addition: the log keeps the history, this keeps the
    one answer you want to read from across the room — larger type, three
    lines, the newest words always in view. It reads the same "You: …" and
    "<name>: …" lines the log gets, so nothing new is sent from main.py.
    """

    _LINES = 3
    _FONT_PT = 11
    _STEP_MS = 18
    # Long answers finish typing within this many steps (≈2.7 s) instead of
    # crawling for half a minute behind the voice.
    _MAX_STEPS = 150

    def __init__(self, assistant_name: str = "JARVIS", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFont(_font(self._FONT_PT))
        self.setFixedHeight(int(QFontMetrics(self.font()).lineSpacing() * self._LINES + 14))
        self._ai_lc = (assistant_name or "jarvis").lower()
        self._text = ""
        self._pos = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._type_step)
        self._restyle(C.TEXT)
        self.hide()

    def set_assistant_name(self, name: str) -> None:
        self._ai_lc = (name or "jarvis").lower()

    def _restyle(self, color: str) -> None:
        self.setStyleSheet(f"""
            QTextEdit {{ background: {C.BG}; color: {color};
                border: none; border-top: 1px solid {C.BORDER};
                padding: 4px 18px; }}""")

    def show_line(self, line: str) -> None:
        """Take one activity-log line; anything but the conversation is ignored."""
        speaker, sep, message = str(line).partition(":")
        message = message.strip()
        if not sep or not message:
            return
        who = speaker.strip().lower()
        if who == "you":
            self._tmr.stop()
            self._restyle(C.TEXT_DIM)
            self.setPlainText(f"You  ·  {message}")
        elif who in (self._ai_lc, "jarvis", "j.a.r.v.i.s"):
            self._restyle(C.TEXT)
            self._text = f"{speaker.strip().upper()}  ·  {message}"
            self._pos = 0
            self.clear()
            self._tmr.start(self._STEP_MS)
        else:
            return
        self._scroll_end()

    def _type_step(self) -> None:
        if self._pos >= len(self._text):
            self._tmr.stop()
            return
        step = max(1, -(-len(self._text) // self._MAX_STEPS))
        self._pos = min(len(self._text), self._pos + step)
        self.setPlainText(self._text[:self._pos])
        self._scroll_end()

    def _scroll_end(self) -> None:
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.setTextCursor(cur)


class DesktopOrb(QWidget):
    """A small orb at the top of the screen while JARVIS's own window is
    minimised or behind another app — so a spoken answer still has somewhere
    to look. `mode` decides when: "speaking" (only while it talks), "always"
    (listening, thinking and muted too) or "off".

    The idea is the rilzisheree/Assistant-V2 fork's `SpeakingOrbOverlay`: a
    frameless, always-on-top, click-through tool window with no parent (a child
    would be clipped to the HUD and hidden with it). Unlike the fork it draws
    its own `OrbHud` on a dark badge rather than re-running the HUD painter, and
    it appears on the screen under the mouse — where the user is working — not
    on the one the HUD happens to be on. It only steps while it is needed.
    """

    _SIZE = 96
    _TOP_MARGIN = 10

    def __init__(self, owner):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self._owner = owner
        self._orb = None
        if OrbHud is not None:
            try:
                self._orb = OrbHud()
            except Exception:
                self._orb = None
        self.mode = "speaking"
        self._fade = 0.0
        self._step_t = time.time()
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._tmr = QTimer(self)
        self._tmr.setInterval(33)
        self._tmr.timeout.connect(self._tick)
        self.hide()

    def wanted(self) -> bool:
        """The HUD is out of sight and the mode asks for the orb now."""
        try:
            hud = self._owner.hud
            if self._orb is None or self.mode == "off":
                return False
            card = getattr(self._owner, "_compact", None)
            if card is not None and card.isVisible():
                return False
            if self.mode == "speaking" and not (hud.speaking and not hud.muted):
                return False
            o = self._owner
            return o.isMinimized() or not o.isVisible() or not o.isActiveWindow()
        except Exception:
            return False

    def wake(self) -> None:
        """Called on every state change; the timer then follows the speech."""
        if self.wanted() and not self._tmr.isActive():
            self._step_t = time.time()
            self._tmr.start()

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = self._owner.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.left() + (area.width() - self.width()) // 2,
                  area.top() + self._TOP_MARGIN)

    def _tick(self) -> None:
        now = time.time()
        dt, self._step_t = now - self._step_t, now
        want = self.wanted()
        self._fade += ((1.0 if want else 0.0) - self._fade) * min(1.0, dt * 7.0)
        if want and not self.isVisible():
            self._place()
            self.show()
            self.raise_()
        if not want and self._fade < 0.02:
            self._fade = 0.0
            self.hide()
            self._tmr.stop()
            return
        hud = self._owner.hud
        self._orb.step(dt, hud._amp_disp, speaking=hud.speaking,
                       muted=hud.muted, state=hud.state)
        # Shown all day in "always" mode, so idle drops to 20 fps like the HUD.
        busy = (hud.speaking or hud._amp_disp > 0.02 or self._fade < 0.98
                or hud.state in ("THINKING", "PROCESSING"))
        self._tmr.setInterval(33 if busy else 50)
        self.update()

    def paintEvent(self, _):
        if self._fade <= 0.0 or self._orb is None:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        e = self._fade * self._fade * (3.0 - 2.0 * self._fade)     # smoothstep
        p.setOpacity(e)
        c = self._SIZE / 2.0
        bg = qcol(C.BG)
        # The orb blends onto an opaque background, so it gets one: a dark
        # badge, which also keeps it legible over a white document.
        p.setPen(QPen(qcol(C.BORDER_B), 1.0))
        p.setBrush(QBrush(bg))
        p.drawEllipse(QRectF(1.0, 1.0, self._SIZE - 2.0, self._SIZE - 2.0))
        main, acc = self._owner.hud._core_colours()
        # Rings reach ~1.37 r at full voice; keep them inside the badge.
        self._orb.paint(p, c, c, (c - 3.0) / 1.4, main, acc, bg)
        p.end()


class _CardCaption(ReplyCaption):
    """The card's copy of the captions: smaller type, its own background, and
    transparent to the mouse so the card can be dragged by its text."""

    _FONT_PT = 8.5

    def __init__(self, assistant_name: str, parent=None):
        super().__init__(assistant_name, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setPlaceholderText("Your last exchange with JARVIS shows here.")
        self.show()

    def _restyle(self, color: str) -> None:
        self.setStyleSheet(f"""
            QTextEdit {{ background: transparent; color: {color};
                border: none; padding: 0px 2px; }}""")


class _CardOrb(QWidget):
    """The orb in the card's corner — `OrbHud`, stepped by the card."""

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.orb = None
        if OrbHud is not None:
            try:
                self.orb = OrbHud()
            except Exception:
                self.orb = None
        self.colours = (qcol(C.PRI), qcol(C.PRI_DIM))

    def paintEvent(self, _):
        if self.orb is None:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.width() / 2.0
        # Rings reach ~1.37 r at full voice; keep them inside the widget.
        self.orb.paint(p, c, c, (c - 2.0) / 1.4, *self.colours, qcol(C.BG))
        p.end()


class CompactCard(QWidget):
    """Compact mode: a small card on top of other windows instead of the HUD.

    Mixed from three forks. omygod9138/Mark-L gave the pattern — a card with an
    orb, the state and the last reply, dragged anywhere and remembered, double
    click for the full HUD. maltahhan10-a11y/Mark-LII gave the handling — a
    hover strip of actions, a right-click menu, a drag that only starts after a
    few pixels so a click stays a click, and a position kept on screen. An
    action that needs CONFIRM brings the HUD back rather than putting the gate
    here: it is rare, and the HUD's banner is the one place it is answered.

    It draws nothing of its own: the orb is `OrbHud`, the text is the captions'
    logic, the actions call the main window's own handlers. Unlike omygod's card
    it stays on top (theirs sat under every other window), and unlike both forks
    it steps its animation only while it is visible.
    """

    _W = 300
    _ORB = 64
    _DRAG_PX = 4

    def __init__(self, owner):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(int(self._W * (1.0 + (_UI_SCALE - 1.0) * 0.5)))
        self._press = None          # (global pos, window pos) at mouse down
        self._dragging = False
        self._step_t = time.time()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        top = QHBoxLayout(); top.setSpacing(10)
        self._orb = _CardOrb(self._ORB)
        top.addWidget(self._orb)
        col = QVBoxLayout(); col.setSpacing(2)
        self._name = QLabel(owner._assistant_name.upper())
        self._name.setFont(_font(11, QFont.Weight.Bold))
        col.addWidget(self._name)
        self._state = QLabel("● LISTENING")
        self._state.setFont(_font(8.5, QFont.Weight.Bold))
        col.addWidget(self._state)
        self._agent = QLabel("")
        self._agent.setFont(_font(7.5, QFont.Weight.Bold))
        self._agent.hide()
        col.addWidget(self._agent)
        col.addStretch()
        top.addLayout(col, stretch=1)
        lay.addLayout(top)

        self.caption = _CardCaption(owner._assistant_name)
        lay.addWidget(self.caption)

        # Hover strip. Its row keeps its height when the buttons hide, so the
        # card does not jump under the mouse.
        self._strip = QWidget()
        self._strip.setFixedHeight(26)
        sl = QHBoxLayout(self._strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self._hud_btn = QPushButton("⤢  HUD")
        self._mic_btn = QPushButton("🎙  MIC")
        self._stop_btn = QPushButton("✋  STOP")
        for b, tip in ((self._hud_btn, "Back to the full HUD (or double-click the card)."),
                       (self._mic_btn, "Mute or unmute the microphone."),
                       (self._stop_btn, "Interrupt what JARVIS is saying.")):
            b.setFixedHeight(24)
            b.setFont(_font(7.5, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.hide()
            sl.addWidget(b)
        self._hud_btn.clicked.connect(owner.exit_compact)
        self._mic_btn.clicked.connect(owner._toggle_mute)
        self._stop_btn.clicked.connect(owner._do_interrupt)
        lay.addWidget(self._strip)

        sc = QShortcut(QKeySequence("F10"), self)
        sc.activated.connect(owner.exit_compact)

        self._tmr = QTimer(self)
        self._tmr.setInterval(33)
        self._tmr.timeout.connect(self._tick)
        self.restyle()
        self.hide()

    # ── look ────────────────────────────────────────────────────────────────

    def restyle(self) -> None:
        """Rebuild the colours — on creation and after a theme switch."""
        self._name.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self.caption._restyle(C.TEXT)
        btn = f"""
            QPushButton {{ background: {_mix_hex(C.BG, C.PRI, 0.06)}; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}"""
        for b in (self._hud_btn, self._mic_btn, self._stop_btn):
            b.setStyleSheet(btn)
        self.refresh_state()
        self.update()

    def set_name(self, name: str) -> None:
        self._name.setText((name or "JARVIS").upper())
        self.caption.set_assistant_name(name)

    def set_agent_line(self, text: str) -> None:
        """'● Claude Code working · 4 min' while a coding agent runs; hidden
        otherwise. The card is often all that is on screen during a long job."""
        if text:
            self._agent.setText(text)
            self._agent.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        self._agent.setVisible(bool(text))

    def refresh_state(self) -> None:
        hud = self._owner.hud
        if hud.muted:
            txt, col = "⊘  MUTED", C.MUTED_C
        elif hud.speaking:
            txt, col = "●  SPEAKING", C.ACC
        elif hud.state in ("THINKING", "PROCESSING"):
            txt, col = f"◈  {hud.state}", C.ACC2
        elif hud.state == "LISTENING":
            txt, col = "●  LISTENING", C.GREEN
        else:
            txt, col = f"○  {hud.state}", C.PRI_DIM
        self._state.setText(txt)
        self._state.setStyleSheet(f"color: {col}; background: transparent;")
        self._mic_btn.setText("🎙  UNMUTE" if hud.muted else "🎙  MUTE")

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcol(C.BORDER_B), 1.2))
        p.setBrush(QBrush(qcol(C.BG, 242)))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 10, 10)
        p.end()

    # ── showing ─────────────────────────────────────────────────────────────

    def showEvent(self, e):
        super().showEvent(e)
        self._step_t = time.time()
        self.refresh_state()
        self._tmr.start()

    def hideEvent(self, e):
        self._tmr.stop()
        super().hideEvent(e)

    def place(self) -> None:
        """Where it was left, kept on the screen it was left on — or the top
        right of the screen under the mouse when that screen is gone."""
        self.adjustSize()
        pos = None
        try:
            from memory.config_manager import get_compact_pos
            pos = get_compact_pos()
        except Exception:
            pass
        screen = None
        if pos is not None:
            screen = QGuiApplication.screenAt(QPoint(pos[0] + 20, pos[1] + 20))
        if screen is None:
            screen = (QGuiApplication.screenAt(QCursor.pos())
                      or QGuiApplication.primaryScreen())
            if screen is None:
                return
            a = screen.availableGeometry()
            pos = (a.right() - self.width() - 24, a.top() + 24)
        self.move(*self._clamp(pos[0], pos[1], screen))

    def _clamp(self, x: int, y: int, screen=None) -> tuple[int, int]:
        """Keep the whole card on one screen: the one its centre is over."""
        if screen is None:
            screen = (QGuiApplication.screenAt(QPoint(x + self.width() // 2,
                                                      y + self.height() // 2))
                      or self.screen() or QGuiApplication.primaryScreen())
        if screen is None:
            return x, y
        a = screen.availableGeometry()
        x = max(a.left(), min(x, a.right() - self.width() + 1))
        y = max(a.top(), min(y, a.bottom() - self.height() + 1))
        return x, y

    # ── animation ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        now = time.time()
        dt, self._step_t = now - self._step_t, now
        hud = self._owner.hud
        orb = self._orb.orb
        if orb is None:
            return
        orb.step(dt, hud._amp_disp, speaking=hud.speaking,
                 muted=hud.muted, state=hud.state)
        self._orb.colours = hud._core_colours()
        busy = (hud.speaking or hud._amp_disp > 0.02
                or hud.state in ("THINKING", "PROCESSING"))
        self._tmr.setInterval(33 if busy else 50)
        self._orb.update()

    # ── mouse ───────────────────────────────────────────────────────────────

    def enterEvent(self, e):
        for b in (self._hud_btn, self._mic_btn, self._stop_btn):
            b.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        for b in (self._hud_btn, self._mic_btn, self._stop_btn):
            b.hide()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.globalPosition().toPoint(), self.pos())
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        start, origin = self._press
        delta = e.globalPosition().toPoint() - start
        if not self._dragging and delta.manhattanLength() < self._DRAG_PX:
            return
        self._dragging = True
        self.move(origin + delta)

    def mouseReleaseEvent(self, e):
        if self._press is not None and self._dragging:
            x, y = self._clamp(self.x(), self.y())
            self.move(x, y)
            try:
                from memory.config_manager import save_compact_pos
                save_compact_pos(x, y)
            except Exception:
                pass
        self._press = None
        self._dragging = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._owner.exit_compact()

    def contextMenuEvent(self, e):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{ background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}""")
        hud = self._owner.hud
        m.addAction("⤢  Open HUD", self._owner.exit_compact)
        m.addAction("🎙  Unmute microphone" if hud.muted else "🎙  Mute microphone",
                    self._owner._toggle_mute)
        m.addAction("✋  Interrupt", self._owner._do_interrupt)
        m.addSeparator()
        m.addAction("⏻  Quit", self._owner.quit_app)
        m.exec(e.globalPos())


class JarvisTray:
    """The icon in the notification area: always a way back to the HUD, and the
    everyday switches without opening it.

    The design is the Nathan123y/Jarvis-for-Nathan fork's macOS menu-bar item —
    a status line, Open, Mute, Quit, an icon that turns when muted — built on
    Qt's `QSystemTrayIcon` instead of a separate signed Swift app, so it needs
    no new dependency and works on Windows, where this fork is used. It matters
    more here than there: the compact card has no taskbar button, so with the
    HUD hidden this icon and the card are the ways back.

    The window's ✕ still quits; the tray does not change that.
    """

    _ICON_PX = 32

    def __init__(self, owner):
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon
        self._owner = owner
        self._last = ""
        self._icon_key = None
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        self._status = self.menu.addAction("")
        self._status.setEnabled(False)
        self.menu.addSeparator()
        self._open = self.menu.addAction("⤢  Open HUD", owner.exit_compact)
        self._compact = self.menu.addAction("▭  Compact mode", self._toggle_compact)
        self._compact.setCheckable(True)
        self._prompt = self.menu.addAction("⌨  Quick prompt", owner.toggle_quick_prompt)
        self.menu.addSeparator()
        self._mute = self.menu.addAction("🎙  Mute microphone", owner._toggle_mute)
        self._mute.setCheckable(True)
        agent_menu = self.menu.addMenu("🤖  Coding agent")
        self._agent_usage = agent_menu.addAction("")
        self._agent_usage.setEnabled(False)
        agent_menu.addSeparator()
        self._agent_engines = {}
        for key, label in (("claude", "Claude Code"), ("codex", "Codex")):
            a = agent_menu.addAction(label, lambda k=key: owner._agent_panel.pick(k))
            a.setCheckable(True)
            self._agent_engines[key] = a
        orb_menu = self.menu.addMenu("🔵  Desktop orb")
        self._orb_modes = {}
        for mode, label in (("off", "Off"), ("speaking", "While speaking"),
                            ("always", "Always")):
            a = orb_menu.addAction(label, lambda m=mode: owner.set_desktop_orb_mode(m))
            a.setCheckable(True)
            self._orb_modes[mode] = a
        self.menu.addSeparator()
        self.menu.addAction("⏻  Quit", owner.quit_app)
        self.menu.aboutToShow.connect(self._sync_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.restyle()
        self.refresh()
        self.tray.show()

    # ── look ────────────────────────────────────────────────────────────────

    def restyle(self) -> None:
        if self.tray is None:
            return
        self.menu.setStyleSheet(f"""
            QMenu {{ background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; padding: 4px; }}
            QMenu::item {{ padding: 5px 22px 5px 10px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QMenu::item:disabled {{ color: {C.TEXT_DIM}; }}
            QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 4px 6px; }}""")
        self._icon_key = None
        self.refresh()

    def _state(self) -> tuple[str, str]:
        hud = self._owner.hud
        if hud.muted:
            return "Microphone muted", C.MUTED_C
        if hud.speaking:
            return "Speaking", C.ACC
        if hud.state in ("THINKING", "PROCESSING"):
            return "Thinking", C.ACC2
        if hud.state == "LISTENING":
            return "Listening", C.GREEN
        return hud.state.title(), C.PRI

    def _icon(self, colour: str):
        """A ring in the state colour around the theme's main colour — drawn,
        so it needs no image file and follows the theme."""
        from PyQt6.QtGui import QIcon
        n = self._ICON_PX
        pm = QPixmap(n, n)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.BG)))
        p.drawEllipse(QRectF(1, 1, n - 2, n - 2))
        p.setPen(QPen(qcol(colour), 4.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(4, 4, n - 8, n - 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.PRI)))
        p.drawEllipse(QRectF(n * 0.36, n * 0.36, n * 0.28, n * 0.28))
        p.end()
        return QIcon(pm)

    def refresh(self) -> None:
        """Icon and tooltip for the current state; cheap when nothing changed."""
        if self.tray is None:
            return
        text, colour = self._state()
        key = (colour, C.PRI, C.BG)
        if key != self._icon_key:
            self._icon_key = key
            self.tray.setIcon(self._icon(colour))
        name = self._owner._assistant_name.upper()
        tip = f"{name} — {text}"
        if self._last:
            tip += "\n" + self._last
        # Windows cuts tray tooltips at 127 characters.
        self.tray.setToolTip(tip if len(tip) <= 127 else tip[:124] + "…")

    def show_line(self, line: str) -> None:
        """The latest reply goes into the tooltip, so a glance answers it."""
        speaker, sep, message = str(line).partition(":")
        if sep and speaker.strip().lower() in (self._owner._assistant_name.lower(),
                                                "jarvis", "j.a.r.v.i.s"):
            self._last = " ".join(message.split())
            self.refresh()

    def hide(self) -> None:
        # An icon left behind by a process that exited stays in the tray until
        # the mouse passes over it.
        if self.tray is not None:
            self.tray.hide()

    # ── actions ─────────────────────────────────────────────────────────────

    def _sync_menu(self) -> None:
        o = self._owner
        text, _ = self._state()
        self._status.setText(f"{o._assistant_name.upper()} — {text}")
        self._mute.setChecked(o.hud.muted)
        self._compact.setChecked(o._compact.isVisible())
        mode = getattr(o._desk_orb, "mode", "speaking")
        for m, a in self._orb_modes.items():
            a.setChecked(m == mode)
        hk = getattr(o, "_quick_prompt_key_label", "")
        self._prompt.setText("⌨  Quick prompt" + (f"   [{hk}]" if hk else ""))
        try:
            from core import agents
            eng = agents.current()["engine"]
            for k, a in self._agent_engines.items():
                a.setChecked(k == eng)
            u = agents.usage(eng)
            bits = [f"{n} {u[k]['pct']:.0f}%" for k, n in (("five_hour", "5h"), ("week", "week"))
                    if u.get(k)]
            self._agent_usage.setText(f"{agents.LABEL[eng]}: "
                                      + (" · ".join(bits) if bits else "limits not known yet"))
        except Exception:
            pass

    def _toggle_compact(self) -> None:
        if self._owner._compact.isVisible():
            self._owner.exit_compact()
        else:
            self._owner.enter_compact()

    def _on_activated(self, reason) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            # A click on the icon means "show me JARVIS": the HUD, raised.
            self._owner.exit_compact()


class QuickPrompt(QWidget):
    """A one-line box in the middle of the screen: type, Enter, gone.

    From the omygod9138/Mark-L fork's Spotlight-style prompt. The text takes the
    same path as the HUD's own input box. Kept from the fork: the size, the
    place (centred, a little above the middle), Esc or clicking away to close.
    Changed: the hotkey is `core.hotkey.GlobalHotkey` (Windows' RegisterHotKey)
    instead of pynput — no new dependency, the key no longer also reaches the
    app in front, and it no longer fires twice when the HUD has focus.
    """

    _W = 580

    def __init__(self, owner):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self._owner = owner
        self._was_active = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(int(self._W * (1.0 + (_UI_SCALE - 1.0) * 0.5)))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        self._icon = QLabel("⌨")
        self._icon.setFont(_font(14))
        lay.addWidget(self._icon)
        self.edit = QLineEdit()
        self.edit.setFont(_font(12))
        self.edit.setFrame(False)
        self.edit.returnPressed.connect(self._submit)
        lay.addWidget(self.edit, stretch=1)
        self.restyle()
        self.hide()

    def restyle(self) -> None:
        name = self._owner._assistant_name
        self.edit.setPlaceholderText(f"Ask {name}…   Enter to send · Esc to close")
        self._icon.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self.edit.setStyleSheet(f"""
            QLineEdit {{ background: transparent; color: {C.TEXT}; border: none;
                selection-background-color: {C.PRI_DIM}; }}""")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcol(C.PRI_DIM), 1.4))
        p.setBrush(QBrush(qcol(C.BG, 246)))
        p.drawRoundedRect(QRectF(0.7, 0.7, self.width() - 1.4, self.height() - 1.4), 12, 12)
        p.end()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.popup()

    def popup(self) -> None:
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        self.adjustSize()
        if screen is not None:
            a = screen.availableGeometry()
            self.move(a.left() + (a.width() - self.width()) // 2,
                      a.top() + int(a.height() * 0.28))
        self._was_active = False
        self.show()
        self.raise_()
        self.activateWindow()
        if _OS == "Windows":
            # Qt's activateWindow only asks; the hotkey press made this
            # process the one allowed to take the foreground, so take it.
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass
        self.edit.setFocus()
        self.edit.selectAll()

    def _submit(self) -> None:
        text = self.edit.text().strip()
        self.edit.clear()
        self.hide()
        if text:
            self._owner.submit_text(text)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e):
        # Clicking anywhere else closes it — but only once it has really had
        # the focus, or a popup that Windows kept in the background would
        # vanish before anyone could type into it.
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and self.isVisible():
                self.hide()


class MetricBar(QWidget):

    _WARN, _HOT = 65, 85     # % at which the bar turns amber, then red

    def __init__(self, label: str, color: str = "PRI", parent=None):
        """`color` is a palette key ("PRI", "GREEN"…), looked up at paint time
        so a live theme switch reaches the bar; a hex string stays fixed."""
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value >= self._HOT:
            bar_col = qcol(C.RED)
        elif self._value >= self._WARN:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(getattr(C, self._color, self._color))

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(_font(7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(_font(9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class UsageBar(MetricBar):
    """One usage window of a coding agent: percent used and when it resets.
    Amber from 75 %, red from 90 % — the point where starting a long job is
    worth a second thought."""

    _WARN, _HOT = 75, 90

    def __init__(self, label: str, parent=None):
        super().__init__(label, "GREEN", parent)
        self.setFixedHeight(34)
        self.setMinimumWidth(60)


class AgentPanel(QWidget):
    """The coding agent at a glance: Claude Code or Codex, its model and effort,
    how much of its plan is left, and the job it is working on.

    Everything here mirrors core/agents.py, which owns the settings; the panel
    only reads its state and calls its setters. Anything that may block — the
    Codex app-server for models and limits, Claude's limit request — runs on a
    worker thread and comes back through a signal.
    """

    _changed_sig = pyqtSignal()          # core.agents changed (any thread)
    _models_sig = pyqtSignal()           # a model list finished loading
    _CODEX_POLL_MS = 5 * 60 * 1000       # Codex limits are free to read

    def __init__(self, owner):
        super().__init__()
        from core import agents
        self._agents = agents
        self._owner = owner
        self._models: dict[str, list] = {"claude": agents.models("claude")}
        self._loading: set[str] = set()
        self._refreshing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        top = QHBoxLayout()
        self._title = QLabel("▸ AGENT")
        self._title.setFont(_font(7, QFont.Weight.Bold))
        top.addWidget(self._title)
        top.addStretch()
        self._project = QLabel("")
        self._project.setFont(_font(7))
        self._project.setToolTip("The current project: where the agent, commands and git work.")
        top.addWidget(self._project)
        lay.addLayout(top)

        eng = QHBoxLayout()
        eng.setSpacing(6)
        self._eng_btns = {}
        for key, text in (("claude", "CLAUDE CODE"), ("codex", "CODEX")):
            b = QPushButton(text)
            b.setFixedHeight(24)
            b.setFont(_font(7.5, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.pick(k))
            eng.addWidget(b)
            self._eng_btns[key] = b
        lay.addLayout(eng)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        self._model_cb = QComboBox()
        self._effort_cb = QComboBox()
        for row, (cap, cb) in enumerate((("MODEL", self._model_cb),
                                         ("EFFORT", self._effort_cb))):
            lbl = QLabel(cap)
            lbl.setFont(_font(7, QFont.Weight.Bold))
            lbl.setObjectName("agentCap")
            grid.addWidget(lbl, row, 0)
            cb.setFont(_font(7.5))
            cb.setFixedHeight(22)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            grid.addWidget(cb, row, 1)
        grid.setColumnStretch(1, 1)
        self._model_cb.activated.connect(self._on_model)
        self._effort_cb.activated.connect(self._on_effort)
        lay.addLayout(grid)

        bars = QHBoxLayout()
        bars.setSpacing(6)
        self._bar_5h = UsageBar("5 HOURS")
        self._bar_week = UsageBar("WEEK")
        bars.addWidget(self._bar_5h)
        bars.addWidget(self._bar_week)
        lay.addLayout(bars)

        meta = QHBoxLayout()
        self._updated = QLabel("")
        self._updated.setFont(_font(6.5))
        meta.addWidget(self._updated, stretch=1)
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedSize(26, 18)
        self._refresh_btn.setFont(_font(8, QFont.Weight.Bold))
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh_usage)
        meta.addWidget(self._refresh_btn)
        lay.addLayout(meta)

        self._job_box = QWidget()
        self._job_box.setObjectName("agentJob")
        # A plain QWidget paints no stylesheet background without this.
        self._job_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        jl = QVBoxLayout(self._job_box)
        jl.setContentsMargins(6, 4, 6, 4)
        jl.setSpacing(2)
        self._job_lbl = QLabel("")
        self._job_lbl.setFont(_font(7.5, QFont.Weight.Bold))
        self._job_lbl.setWordWrap(True)
        jl.addWidget(self._job_lbl)
        jrow = QHBoxLayout()
        self._step_lbl = QLabel("")
        self._step_lbl.setFont(_font(7))
        jrow.addWidget(self._step_lbl, stretch=1)
        self._stop_btn = QPushButton("STOP")
        self._stop_btn.setFixedSize(52, 20)
        self._stop_btn.setFont(_font(7, QFont.Weight.Bold))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setToolTip("Stop the agent. What it already changed stays changed.")
        self._stop_btn.clicked.connect(self._stop)
        jrow.addWidget(self._stop_btn)
        jl.addLayout(jrow)
        self._job_box.hide()
        lay.addWidget(self._job_box)

        self._pending_refresh = False
        self._changed_sig.connect(self._schedule)
        self._models_sig.connect(self.refresh)
        agents.on_change(self._changed_sig.emit)

        self._tick_tmr = QTimer(self)
        self._tick_tmr.setInterval(2000)
        self._tick_tmr.timeout.connect(self._tick)
        self._tick_tmr.start()
        self._poll_tmr = QTimer(self)
        self._poll_tmr.setInterval(self._CODEX_POLL_MS)
        self._poll_tmr.timeout.connect(lambda: self._bg_usage("codex", quiet=True))
        self._poll_tmr.start()

        self.restyle()
        self._load_models("codex")
        QTimer.singleShot(4000, lambda: self._bg_usage("codex", quiet=True))

    # ── look ────────────────────────────────────────────────────────────────

    def restyle(self) -> None:
        self._title.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._project.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._updated.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._step_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        for lbl in self.findChildren(QLabel, "agentCap"):
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        combo = (f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
                 f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 1px 6px; }}"
                 f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
                 f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
                 f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}")
        self._model_cb.setStyleSheet(combo)
        self._effort_cb.setStyleSheet(combo)
        small = (f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                 f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
                 f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}")
        self._refresh_btn.setStyleSheet(small)
        self._stop_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.RED}; "
            f"border: 1px solid {C.RED}; border-radius: 3px; }}"
            f"QPushButton:hover {{ background: {_mix_hex(C.BG, C.RED, 0.18)}; }}")
        self._job_box.setStyleSheet(
            f"QWidget#agentJob {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 4px; }}")
        self.refresh()

    def _eng_style(self, on: bool) -> str:
        if on:
            return (f"QPushButton {{ background: {_mix_hex(C.BG, C.PRI, 0.16)}; color: {C.PRI}; "
                    f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        return (f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; "
                f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
                f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}")

    # ── state → widgets ─────────────────────────────────────────────────────

    def _schedule(self) -> None:
        # Agents report every step; coalesce bursts into one repaint.
        if not self._pending_refresh:
            self._pending_refresh = True
            QTimer.singleShot(150, self.refresh)

    def _model_list(self, engine: str) -> list:
        return self._models.get(engine) or []

    def _efforts(self, engine: str, model_id: str) -> list[str]:
        if engine == "claude":
            return list(self._agents.CLAUDE_EFFORTS)
        ms = self._model_list(engine)
        m = next((x for x in ms if x.id == model_id), None) if model_id else \
            next((x for x in ms if x.is_default), None)
        return list(m.efforts) if m else []

    def refresh(self) -> None:
        self._pending_refresh = False
        self._refreshing = True
        try:
            st = self._agents.state()
        except Exception as e:
            print(f"[AgentPanel] state failed: {e}")
            self._refreshing = False
            return
        engine = st["engine"]
        for key, b in self._eng_btns.items():
            b.setStyleSheet(self._eng_style(key == engine))
        label = self._agents.LABEL[engine]
        self._eng_btns["claude"].setToolTip(
            "Claude Code (claude -p) on your Claude plan. Click to use it for "
            "delegated coding tasks.")
        self._eng_btns["codex"].setToolTip(
            "Codex (codex exec) on your ChatGPT plan, in its workspace sandbox. "
            "Click to use it for delegated coding tasks.")

        cb = self._model_cb
        cb.blockSignals(True)
        cb.clear()
        default_name = ("Default (as set in Claude Code)" if engine == "claude"
                        else "Default (the account's ★ model)")
        ms = self._model_list(engine)
        if engine in self._loading and not ms:
            cb.addItem("loading models…", None)
            cb.setEnabled(False)
        else:
            cb.setEnabled(True)
            cb.addItem(default_name, "")
            for m in ms:
                cb.addItem(m.label + ("  ★" if m.is_default else ""), m.id)
            idx = cb.findData(st["model"])
            if idx < 0 and st["model"]:
                cb.addItem(f"{st['model']} (unavailable)", st["model"])
                idx = cb.count() - 1
            cb.setCurrentIndex(max(0, idx))
        cb.setToolTip("The model the agent runs on. ★ marks the CLI's own default.")
        cb.blockSignals(False)

        eb = self._effort_cb
        eb.blockSignals(True)
        eb.clear()
        eb.addItem("Default", "")
        costly = self._agents.COSTLY_EFFORTS
        for e in self._efforts(engine, st["model"]):
            eb.addItem(e + ("  ⚡" if e in costly else ""), e)
        idx = eb.findData(st["effort"])
        eb.setCurrentIndex(max(0, idx))
        eb.setToolTip("How hard the agent thinks. ⚡ levels use the plan up much faster.")
        eb.blockSignals(False)

        self._show_usage(engine, st["usage"].get(engine) or {})
        self._show_job(st.get("job"))
        self._refreshing = False
        try:
            self._owner._tray.refresh()
        except Exception:
            pass

    def _show_usage(self, engine: str, u: dict) -> None:
        clock = self._agents._clock
        for bar, key in ((self._bar_5h, "five_hour"), (self._bar_week, "week")):
            w = u.get(key)
            if w:
                reset = clock(int(w.get("resets_at") or 0))
                bar.set_value(float(w["pct"]), f"{w['pct']:.0f}%"
                              + (f" ↻{reset}" if reset else ""))
                bar.setToolTip(f"{w['pct']:.1f}% of this window used"
                               + (f"; it resets at {reset}." if reset else "."))
            else:
                bar.set_value(0, "--")
                bar.setToolTip("Not known yet — press ↻ to fetch it.")
        self._usage = u
        self._tick()
        tip = ("Refresh the limits. For Claude Code this sends a one-word request to "
               "haiku (a sliver of the plan); Codex reports them for free."
               if engine == "claude" else "Refresh Codex's limits (free).")
        self._refresh_btn.setToolTip(tip)

    def _show_job(self, j) -> None:
        if not j:
            self._job_box.hide()
            self._card_line("")
            return
        label = self._agents.LABEL[j["engine"]]
        mins = j["minutes"]
        elapsed = f"{mins * 60:.0f} s" if mins < 1.5 else f"{mins:.0f} min"
        if not j["done"]:
            self._job_lbl.setText(f"● {label}: {j['task'][:70]}")
            self._job_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
            self._step_lbl.setText(f"{elapsed} · {j['steps']} steps"
                                   + (f" · {j['last_step'][:40]}" if j["last_step"] else ""))
            self._stop_btn.show()
            self._card_line(f"● {label} working · {elapsed}")
        else:
            ok = j["ok"]
            self._job_lbl.setText(f"{'✓' if ok else '✗'} {label}: {j['task'][:70]}")
            self._job_lbl.setStyleSheet(
                f"color: {C.GREEN if ok else C.RED}; background: transparent;")
            self._step_lbl.setText(f"{'done' if ok else (j['killed'] or 'did not finish')}"
                                   f" · {elapsed} · {j['steps']} steps")
            self._stop_btn.hide()
            self._card_line("")
        self._job_box.show()

    def _card_line(self, text: str) -> None:
        card = getattr(self._owner, "_compact", None)
        if card is not None and hasattr(card, "set_agent_line"):
            card.set_agent_line(text)

    def _tick(self) -> None:
        try:
            from core import projects
            cur = projects.current()
            self._project.setText(cur.name if cur else "no project")
        except Exception:
            pass
        u = getattr(self, "_usage", {}) or {}
        upd = float(u.get("updated") or 0)
        if not upd:
            self._updated.setText("limits not fetched yet")
        else:
            age = int(time.time() - upd) // 60
            self._updated.setText("limits updated just now" if age < 1 else
                                  f"limits updated {age} min ago")
        j = self._agents.job()
        if j is not None and not j.done:
            self._show_job(self._agents.state().get("job"))

    # ── user actions ────────────────────────────────────────────────────────

    def pick(self, engine: str) -> None:
        self._owner._log_sig.emit("SYS: " + self._agents.set_engine(engine))
        if engine == "codex" and not self._model_list("codex"):
            self._load_models("codex")

    def _on_model(self, idx: int) -> None:
        if self._refreshing:
            return
        model = self._model_cb.itemData(idx)
        if model is None:
            return
        engine = self._agents.current()["engine"]
        self._bg(lambda: self._agents.set_model(model, engine))

    def _on_effort(self, idx: int) -> None:
        if self._refreshing:
            return
        effort = self._effort_cb.itemData(idx)
        if effort is None:
            return
        engine = self._agents.current()["engine"]
        self._bg(lambda: self._agents.set_effort(effort, engine))

    def _stop(self) -> None:
        self._owner._log_sig.emit("SYS: " + self._agents.cancel())

    def refresh_usage(self) -> None:
        self._bg_usage(self._agents.current()["engine"], quiet=False)

    # ── workers ─────────────────────────────────────────────────────────────

    def _bg(self, fn) -> None:
        def work():
            try:
                msg = fn()
                if msg:
                    self._owner._log_sig.emit(f"SYS: {msg}")
            except Exception as e:
                self._owner._log_sig.emit(f"ERR: agent settings — {e}")
        threading.Thread(target=work, daemon=True, name="agent-settings").start()

    def _bg_usage(self, engine: str, quiet: bool) -> None:
        if not quiet:
            self._updated.setText("fetching limits…")

        def work():
            try:
                msg = self._agents.refresh_usage(engine)
            except Exception as e:
                msg = f"could not fetch the limits: {e}"
            if not quiet:
                self._owner._log_sig.emit(f"SYS: {msg}")
            self._changed_sig.emit()
        threading.Thread(target=work, daemon=True, name="agent-usage").start()

    def _load_models(self, engine: str) -> None:
        if engine in self._loading:
            return
        self._loading.add(engine)

        def work():
            try:
                ms = self._agents.models(engine, refresh=True)
            except Exception:
                ms = []
            self._models[engine] = ms
            self._loading.discard(engine)
            self._models_sig.emit()
        threading.Thread(target=work, daemon=True, name="agent-models").start()


class RepairPanel(QWidget):
    """The assistant's own health and self-repair, under the AGENT panel.

    One line says whether any tool is failing (core/health.py); a second,
    shown only while something is happening, follows core/self_repair.py:
    the agent working, a change waiting for review, an applied change
    waiting for a restart. The buttons are the human half of the loop —
    CHECK runs the self-check, START answers the assistant's "shall I?" (the
    spoken answer can be lost: seen live, the model asked and then called run
    in the same breath, and a dropped connection took the conversation with
    it), REVIEW opens the diff (where APPLY lives), RESTART loads an applied
    change, STOP cancels a run. The model has no tool for APPLY."""

    _changed_sig = pyqtSignal()
    _checked_sig = pyqtSignal()          # a self-check finished (worker thread)

    def __init__(self, owner):
        super().__init__()
        from core import health, self_repair
        self._health = health
        self._repair = self_repair
        self._owner = owner
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        top = QHBoxLayout()
        self._title = QLabel("▸ SELF-REPAIR")
        self._title.setFont(_font(7, QFont.Weight.Bold))
        top.addWidget(self._title)
        top.addStretch()
        self._health_lbl = QLabel("")
        self._health_lbl.setFont(_font(7, QFont.Weight.Bold))
        top.addWidget(self._health_lbl)
        lay.addLayout(top)

        self._state_lbl = QLabel("")
        self._state_lbl.setFont(_font(7))
        self._state_lbl.setWordWrap(True)
        self._state_lbl.hide()
        lay.addWidget(self._state_lbl)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._btns = {}
        for key, text, tip in (
                ("check", "CHECK", "Check every tool, the packages, git, disk, key and audio."),
                ("start", "START", "Yes — let the agent make this change in a separate copy."),
                ("drop", "✕", "No — drop the change the assistant asked about."),
                ("review", "REVIEW", "Show the change waiting for review — apply or discard it there."),
                ("restart", "RESTART", "Restart JARVIS to load the applied change."),
                ("stop", "STOP", "Stop the self-repair. The running code is not touched.")):
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setFont(_font(7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self._click(k))
            row.addWidget(b)
            self._btns[key] = b
        lay.addLayout(row)

        self._pending = False
        self._changed_sig.connect(self._schedule)
        self._checked_sig.connect(lambda: self._btns["check"].setEnabled(True))
        health.on_change(self._changed_sig.emit)
        self_repair.on_change(self._changed_sig.emit)
        # A question expires after 3 minutes without any event saying so.
        self._tick_tmr = QTimer(self)
        self._tick_tmr.setInterval(5000)
        self._tick_tmr.timeout.connect(self.refresh)
        self._tick_tmr.start()
        self.restyle()

    # ── look ────────────────────────────────────────────────────────────────

    def restyle(self) -> None:
        self._title.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._state_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        small = (f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                 f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
                 f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}")
        for key, b in self._btns.items():
            if key == "stop":
                b.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.RED}; "
                                f"border: 1px solid {C.RED}; border-radius: 3px; }}"
                                f"QPushButton:hover {{ background: {_mix_hex(C.BG, C.RED, 0.18)}; }}")
            elif key in ("review", "start"):
                b.setStyleSheet(f"QPushButton {{ background: {_mix_hex(C.BG, C.ACC, 0.16)}; "
                                f"color: {C.ACC}; border: 1px solid {C.ACC}; border-radius: 3px; }}")
            else:
                b.setStyleSheet(small)
        self.refresh()

    # ── state → widgets ─────────────────────────────────────────────────────

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(150, self.refresh)

    def refresh(self) -> None:
        self._pending = False
        s = self._health.summary()
        if s["bad"]:
            text, col = f"● {len(s['bad'])} FAILING", C.RED
            tip = "Failing often: " + ", ".join(s["bad"])
        elif s["warn"]:
            text, col = f"● {len(s['warn'])} WARNING", C.ACC2
            tip = "Failed at least once recently: " + ", ".join(s["warn"])
        else:
            text, col = "● HEALTHY", C.GREEN
            tip = "No tool has failed this session."
        if s["errors"]:
            tip += f"\n{s['errors']} error(s) recorded on this code version."
        self._health_lbl.setText(text)
        self._health_lbl.setToolTip(tip)
        self._health_lbl.setStyleSheet(f"color: {col}; background: transparent;")

        st = self._repair.state()
        phase = st["state"]
        line = {
            "asked": f"? Waiting for your OK ({st.get('left', 0)} s) — say yes or press START",
            "preparing": f"⟳ Preparing — {st.get('step', '')}",
            "agent": f"⟳ {st.get('step', '')}",
            "testing": f"⟳ {st.get('step', '')}",
            "review": f"◆ Ready for review: {st['files'] if 'files' in st else 0} file(s), "
                      f"+{st.get('added', 0)} −{st.get('removed', 0)}",
            "applied": "✓ Applied — restart to load it",
            "failed": f"✗ Stopped: {st.get('problem', '')[:120]}",
            "discarded": "",
        }.get(phase, "")
        if line and phase not in ("idle", "discarded"):
            task = st.get("task", "")
            self._state_lbl.setText(f"{line}\n{task[:90]}" if task else line)
            self._state_lbl.show()
        else:
            self._state_lbl.hide()
        self._btns["start"].setVisible(phase == "asked")
        self._btns["drop"].setVisible(phase == "asked")
        self._btns["review"].setVisible(phase == "review")
        self._btns["restart"].setVisible(phase == "applied")
        self._btns["stop"].setVisible(phase in ("preparing", "agent", "testing"))

    # ── buttons ─────────────────────────────────────────────────────────────

    def _click(self, key: str) -> None:
        if key == "review":
            self._owner._show_review_overlay()
        elif key == "restart":
            self._bg(lambda: self._repair.restart(by_button=True) and "Restarting…")
        elif key == "stop":
            self._bg(self._repair.cancel)
        elif key == "start":
            self._bg(lambda: self._repair.run(player=None, by_button=True))
        elif key == "drop":
            self._bg(self._repair.drop_pending)
        elif key == "check":
            self._btns["check"].setEnabled(False)
            self._bg(self._check, done=self._checked_sig)

    def _check(self) -> str:
        from core import capabilities
        result = capabilities.run_probes()
        bad = [i for i in result["items"] if not i["ok"]]
        if not bad:
            return f"Self-check — all {len(result['items'])} parts fine."
        return (f"Self-check — {len(bad)} problem(s): "
                + "; ".join(f"{i['name']}: {i['detail']}" for i in bad[:5]))

    def _bg(self, fn, done=None) -> None:
        def work():
            try:
                msg = fn()
                if msg:
                    self._owner._log_sig.emit(f"SYS: {msg}")
            except Exception as e:
                self._owner._log_sig.emit(f"ERR: self-repair — {e}")
            finally:
                if done is not None:
                    done.emit()
        threading.Thread(target=work, daemon=True, name="self-repair-ui").start()


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(_font(9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        # Inside the dashed border, and spaced by the text size, so a larger
        # TEXT SIZE neither crosses the border nor stacks the two lines.
        line = 16 * _UI_SCALE
        y0 = cy + 8 - (line - 16) * 0.6
        p.setFont(_font(8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        _draw_fit(p, QRectF(12, y0, W - 24, line), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(_font(7))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        _draw_fit(p, QRectF(12, y0 + line * 0.95, W - 24, line * 0.9),
                  Qt.AlignmentFlag.AlignCenter,
                  "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(_font(20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(_font(8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        _draw_fit(p, QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(_font(8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        _draw_fit(p, QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(_font(7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        _draw_fit(p, QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(_font(6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        _draw_fit(p, QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(_font(9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: {_glass(C.BG, 242)};
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(_font(7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(_font(8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_font(font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(_font(10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(_font(9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(_font(10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(_font(fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(_font(10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(_font(10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(_font(8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(_font(7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(_font(10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(_font(9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(_font(9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(_font(12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(_font(8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(_font(9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(_font(8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(_font(7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(_font(11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(_font(10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(_font(8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(_font(9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(_font(9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class ReviewOverlay(_HudOverlay):
    """A change the assistant made to its own code, before it goes in.

    Everything needed to decide is on one panel: what was asked, which files
    changed (core files marked), the tests before and after, the agent's own
    account, and the diff itself. APPLY is a human click that goes straight
    to core/self_repair.apply(); the model has no tool that can do it. Robin's
    fork asked for approval without showing the diff — that is the mistake
    this panel exists to avoid."""

    decided = pyqtSignal(str)          # "apply" | "discard" | "close"
    _OW = 680

    def __init__(self, repair, diff: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ReviewOverlay {{
                background: {_glass(C.BG, 248)};
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(7)

        hdr = QLabel("◆  SELF-REPAIR — REVIEW")
        hdr.setFont(_font(11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(repair.task)
        ttl.setWordWrap(True)
        ttl.setFont(_font(10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        def esc(t):
            return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        rows = []
        for f in repair.files:
            mark = {"core": f" <span style='color:{C.ACC2}'>⚠ core code</span>",
                    "new test": f" <span style='color:{C.GREEN}'>new test</span>"}.get(
                f["flag"], f" <span style='color:{C.TEXT_DIM}'>{esc(f['flag'])}</span>"
                if f["flag"] else "")
            rows.append(f"{esc(f['path'])} <span style='color:{C.GREEN}'>+{f['added']}</span> "
                        f"<span style='color:{C.RED}'>−{f['removed']}</span>{mark}")
        b, a = repair.before or {}, repair.after or {}
        fixed = sorted(set(b.get("failed") or []) - set(a.get("failed") or []))
        tests = (f"Tests before: {b.get('passed', 0)} passed, {len(b.get('failed') or [])} failed"
                 f" &nbsp;→&nbsp; after: {a.get('passed', 0)} passed, "
                 f"{len(a.get('failed') or [])} failed, none newly failing"
                 + (f", {len(fixed)} fixed" if fixed else ""))
        info = QLabel("<br>".join(rows) + f"<br><span style='color:{C.TEXT_MED}'>{tests}</span>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setFont(_font(8))
        info.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(info)

        if repair.report:
            rep = QLabel(repair.report[:700])
            rep.setWordWrap(True)
            rep.setFont(_font(8))
            rep.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(rep)

        view = QTextEdit()
        view.setReadOnly(True)
        view.setFixedHeight(330)
        view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        view.setStyleSheet(f"QTextEdit {{ background: {C.DARK}; color: {C.TEXT}; "
                           f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")
        html = []
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                col = C.GREEN
            elif line.startswith("-") and not line.startswith("---"):
                col = C.RED
            elif line.startswith("@@"):
                col = C.PRI
            elif line.startswith(("diff ", "index ", "+++", "---", "new file", "rename ")):
                col = C.ACC2
            else:
                col = C.TEXT_MED
            html.append(f"<span style='color:{col}'>{esc(line) or '&nbsp;'}</span>")
        view.setHtml("<pre style='font-family: Consolas, monospace; font-size: 9pt; "
                     "margin: 0'>" + "<br>".join(html) + "</pre>")
        lay.addWidget(view)

        row = QHBoxLayout(); row.setSpacing(8)
        for key, text, col in (("apply", "▸  APPLY", C.ACC), ("discard", "DISCARD", C.RED),
                               ("close", "LATER", C.TEXT_MED)):
            btn = QPushButton(text)
            btn.setFixedHeight(32)
            btn.setFont(_font(9, QFont.Weight.Bold if key == "apply" else QFont.Weight.Normal))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {col};
                    border: 1px solid {col if key != "close" else C.BORDER}; border-radius: 3px; }}
                QPushButton:hover {{ background: {_mix_hex(C.BG, col, 0.18)}; }}
            """)
            btn.clicked.connect(lambda _=False, k=key: self.decided.emit(k))
            row.addWidget(btn)
            if key == "close":
                # Enter without reading must not apply anything.
                btn.setDefault(True)
                btn.setFocus()
        lay.addLayout(row)


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(_font(12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(_font(8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(_font(9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(_font(7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(_font(9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(_font(9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class WakeWordOverlay(_HudOverlay):
    """How the assistant hears its name: which engine listens, for what
    phrase, and how long it stays awake afterwards.

    The engines differ in what they can be asked to listen for, so the panel
    only shows the fields the chosen one uses — a model file for openWakeWord,
    a typed phrase for Sherpa and Vosk, a language for Vosk."""

    applied = pyqtSignal()          # emitted after Apply
    _status_sig = pyqtSignal()      # an install finished on a worker thread
    _OW = 480

    _OWW_CHOICES = [("Hey Jarvis", "hey_jarvis"), ("Alexa", "alexa"),
                    ("Hey Mycroft", "hey_mycroft"), ("Hey Rhasspy", "hey_rhasspy"),
                    ("Custom model file (.onnx)…", "")]
    _HINTS = {
        "openwakeword": "Lowest CPU and the fewest false wakes. A new phrase needs a "
                        "model trained with openWakeWord's notebook — pick its .onnx file.",
        "sherpa":       "Type any phrase — no training. The model hears English sounds, "
                        "so a Ukrainian name is spelled the way it sounds (Марко → MARKO). "
                        "Two words wake it more reliably than one.",
        "vosk":         "Understands Ukrainian words natively. Uses more CPU and decides at "
                        "the end of what you said. The phrase must be a word the model knows.",
    }

    def __init__(self, log=None, parent=None):
        super().__init__(parent)
        from core.wake_engines import engine_class
        from memory.config_manager import (
            WAKE_ENGINES, get_assistant_name, get_wake_settings,
        )
        self._log = log or (lambda _m: None)
        self._name = get_assistant_name()
        cfg = get_wake_settings()

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            WakeWordOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
            QCheckBox {{ color: {C.TEXT}; background: transparent; }}
            QCheckBox::indicator {{ width: 11px; height: 11px;
                border: 1px solid {C.BORDER_B}; background: {C.DARK}; }}
            QCheckBox::indicator:checked {{ background: {C.PRI_DIM}; }}
            QSpinBox {{ background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 4px; }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(5)

        hdr = QLabel("🎙  WAKE WORD")
        hdr.setFont(_font(12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _field_css = (
            f"background: {C.DARK}; color: {C.TEXT}; border: 1px solid {C.BORDER}; "
            f"border-radius: 3px; padding: 4px 8px;")
        _combo_css = (
            f"QComboBox {{ {_field_css} }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}")

        def _cap(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setFont(_font(8))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(lbl)
            return lbl

        def _combo(items, current) -> QComboBox:
            box = QComboBox()
            box.setFont(_font(9))
            box.setFixedHeight(28)
            box.setStyleSheet(_combo_css)
            for text, data in items:
                box.addItem(text, data)
            idx = box.findData(current)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            lay.addWidget(box)
            return box

        def _edit(text: str) -> QLineEdit:
            e = QLineEdit(text)
            e.setFont(_font(9))
            e.setFixedHeight(28)
            e.setStyleSheet(f"QLineEdit {{ {_field_css} }}")
            lay.addWidget(e)
            return e

        _cap("ENGINE — what listens for the wake phrase")
        self._engine = _combo([(engine_class(e).title, e) for e in WAKE_ENGINES],
                              cfg["engine"])

        self._oww_cap = _cap("PHRASE — pretrained model, or your own")
        oww = cfg["oww_model"]
        pre = {d for _t, d in self._OWW_CHOICES if d}
        self._oww = _combo(self._OWW_CHOICES, oww if oww in pre else "")
        self._oww_path = _edit("" if oww in pre else oww)
        self._oww_path.setPlaceholderText("path to a .onnx model")
        self._oww_browse = QPushButton("BROWSE…")
        self._oww_browse.setFixedHeight(24)
        self._oww_browse.setFont(_font(8))
        self._oww_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._oww_browse.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}")
        self._oww_browse.clicked.connect(self._browse_model)
        lay.addWidget(self._oww_browse)

        self._phrase_cap = _cap("PHRASE — commas separate alternatives")
        self._phrase = _edit(cfg["phrase"])
        self._spoken_cap = _cap("SOUNDS LIKE — optional, in English letters")
        self._spoken = _edit(cfg["spoken_as"])
        self._spoken.setPlaceholderText("automatic, e.g. HEY MARKO")
        self._lang_cap = _cap("LANGUAGE of the phrase")
        self._lang = _combo([("Українська", "uk"), ("English", "en")], cfg["vosk_lang"])

        self._sens_cap = _cap("")
        self._sens = QSlider(Qt.Orientation.Horizontal)
        self._sens.setRange(0, 100)
        self._sens.setValue(int(round(cfg["sensitivity"] * 100)))
        self._sens.valueChanged.connect(self._update_sens_caption)
        lay.addWidget(self._sens)

        row = QHBoxLayout(); row.setSpacing(6)
        self._follow = QCheckBox("Sleep again after each answer, in")
        self._follow.setFont(_font(8))
        self._follow.setChecked(cfg["follow_up"])
        row.addWidget(self._follow)
        self._follow_s = QSpinBox()
        self._follow_s.setRange(4, 60)
        self._follow_s.setSuffix(" s")
        self._follow_s.setValue(cfg["follow_up_seconds"])
        row.addWidget(self._follow_s)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout(); row.setSpacing(6)
        lbl = QLabel("Otherwise sleep after silence of")
        lbl.setFont(_font(8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        row.addWidget(lbl)
        self._timeout_s = QSpinBox()
        self._timeout_s.setRange(15, 3600)
        self._timeout_s.setSingleStep(15)
        self._timeout_s.setSuffix(" s")
        self._timeout_s.setValue(cfg["sleep_timeout_seconds"])
        row.addWidget(self._timeout_s)
        row.addStretch(1)
        lay.addLayout(row)

        self._chime = QCheckBox("Chime when it wakes")
        self._chime.setFont(_font(8))
        self._chime.setChecked(cfg["chime"])
        lay.addWidget(self._chime)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setFont(_font(7))
        self._hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(4)
        lay.addWidget(self._hint)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setFont(_font(8))
        lay.addWidget(self._status)
        self._dl_btn = QPushButton("⬇  DOWNLOAD THIS ENGINE")
        self._dl_btn.setFixedHeight(28)
        self._dl_btn.setFont(_font(8, QFont.Weight.Bold))
        self._dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dl_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.ACC2}; "
            f"border: 1px solid {C.ACC2}; border-radius: 3px; }}"
            f"QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}")
        self._dl_btn.clicked.connect(self._download)
        lay.addWidget(self._dl_btn)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(_font(9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)
        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(_font(9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

        self._installing = False
        self._status_sig.connect(self._refresh)
        for sig in (self._engine.currentIndexChanged, self._oww.currentIndexChanged,
                    self._lang.currentIndexChanged):
            sig.connect(self._refresh)
        self._oww_path.textChanged.connect(self._refresh)
        self._refresh()

    # ── Form state ───────────────────────────────────────────────────────────

    def values(self) -> dict:
        """The form as a wake_word config block."""
        oww = self._oww.currentData() or self._oww_path.text().strip() or "hey_jarvis"
        return {
            "engine": self._engine.currentData(),
            "phrase": self._phrase.text().strip(),
            "spoken_as": self._spoken.text().strip(),
            "sensitivity": self._sens.value() / 100.0,
            "oww_model": oww,
            "vosk_lang": self._lang.currentData(),
            "follow_up": self._follow.isChecked(),
            "follow_up_seconds": self._follow_s.value(),
            "sleep_timeout_seconds": self._timeout_s.value(),
            "chime": self._chime.isChecked(),
        }

    def _settings(self):
        from core.wake_engines import WakeSettings
        from memory.config_manager import WAKE_MODELS_DIR
        return WakeSettings.from_config(self.values(), self._name, WAKE_MODELS_DIR)

    def _update_sens_caption(self, v: int | None = None):
        v = self._sens.value() if v is None else v
        word = "strict" if v < 34 else ("eager" if v > 66 else "balanced")
        self._sens_cap.setText(f"SENSITIVITY — {v}% ({word}; higher wakes more easily)")

    def _refresh(self, *_a):
        from core.wake_engines import engine_class
        eng = self._engine.currentData()
        is_oww, is_vosk = eng == "openwakeword", eng == "vosk"
        custom = is_oww and not self._oww.currentData()
        for w in (self._oww_cap, self._oww):
            w.setVisible(is_oww)
        for w in (self._oww_path, self._oww_browse):
            w.setVisible(custom)
        for w in (self._phrase_cap, self._phrase):
            w.setVisible(not is_oww)
        for w in (self._spoken_cap, self._spoken):
            w.setVisible(eng == "sherpa")
        for w in (self._lang_cap, self._lang):
            w.setVisible(is_vosk)
        n = self._name.lower()
        self._phrase.setPlaceholderText(f"default: {n if is_vosk else 'hey ' + n}")
        self._hint.setText(self._HINTS.get(eng, ""))
        self._update_sens_caption()

        s = self._settings()
        ready = engine_class(eng).is_ready(s)
        if self._installing:
            self._status.setText("Downloading… progress is in the log.")
            self._status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        elif ready:
            self._status.setText("✓ Installed. You will say: " + engine_class(eng)(s).label)
            self._status.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        elif custom and not self._oww_path.text().strip():
            self._status.setText("Pick the model file.")
            self._status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        else:
            self._status.setText("Not installed yet — download it once (a few to ~80 MB).")
            self._status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._dl_btn.setVisible(not ready)
        self._dl_btn.setEnabled(not self._installing
                                and not (custom and not self._oww_path.text().strip()))
        self.adjustSize()

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "openWakeWord model", "", "ONNX model (*.onnx)")
        if path:
            self._oww_path.setText(path)

    def _download(self):
        if self._installing:
            return
        self._installing = True
        settings = self._settings()
        self._refresh()

        def work():
            try:
                from core.wake_word import install_and_download
                ok, msg = install_and_download(logger=self._log, settings=settings)
            except Exception as e:
                ok, msg = False, str(e)
            self._log(f"SYS: {'Wake word engine ready.' if ok else 'Wake word setup failed: ' + msg}")
            self._installing = False
            self._status_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-install").start()

    def _apply(self):
        from memory.config_manager import save_wake_settings
        save_wake_settings(self.values())
        self.hide()
        self.applied.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: {_glass(C.BG, 246)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(_font(12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(_font(7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(_font(9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(_font(8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(_font(7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(_font(8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(_font(9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: {_glass(C.PANEL, 248)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(_font(7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(_font(8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(_font(8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(_font(7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: {_glass(C.BG, 245)};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(_font(9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(_font(9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(_font(fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(_font(9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(_font(8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(_font(10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(_font(8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_font(fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(_font(8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(_font(28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(_font(8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(_font(8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(_font(8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(_font(8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(_font(7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(_font(54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _look_btns_sig   = pyqtSignal()          # a CONTROLS setting changed off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _review_sig     = pyqtSignal()           # self-repair: put the change up for review
    _quick_prompt_sig = pyqtSignal()         # global hotkey thread → toggle the prompt
    _notify_sig     = pyqtSignal(str)        # any thread → a tray notification
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _chime_sig      = pyqtSignal()           # woken by voice — play the system chime
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved theme, then the UI colour on top of it, BEFORE
        # panels/stylesheets are built
        try:
            from memory.config_manager import get_ui_theme
            apply_ui_theme(get_ui_theme())
        except Exception:
            pass
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        # Widgets without an explicit _font() follow the application font, so
        # it takes the TEXT SIZE multiplier too — once per process.
        _app = QApplication.instance()
        if _app is not None and _UI_SCALE != 1.0 and not _app.property("jarvis_scaled"):
            _f = _app.font()
            if _f.pointSizeF() > 0:
                _f.setPointSizeF(_f.pointSizeF() * _UI_SCALE)
                _app.setFont(_f)
            _app.setProperty("jarvis_scaled", True)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.on_barge_in       = None   # callable: (enable: bool, mode: str) -> str — voice interrupt
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self.on_wake_settings  = None   # callable: () -> None — wake settings saved
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(_font(8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(_font(8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did JARVIS close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(_font(9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(_font(8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over JARVIS is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # The HUD page: the canvas, with the latest exchange as captions below.
        self._caption = ReplyCaption(self._assistant_name)
        _hud_page = QWidget()
        _hud_v = QVBoxLayout(_hud_page)
        _hud_v.setContentsMargins(0, 0, 0, 0)
        _hud_v.setSpacing(0)
        _hud_v.addWidget(self.hud, stretch=1)
        _hud_v.addWidget(self._caption)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(_hud_page)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._log_sig.connect(self._on_caption_line)
        self._desk_orb = DesktopOrb(self)
        self._compact = CompactCard(self)
        self._log_sig.connect(self._compact.caption.show_line)
        self._quick_prompt = QuickPrompt(self)
        self._quick_prompt_sig.connect(self.toggle_quick_prompt)
        self._quick_hotkey = None
        self._quick_shortcuts = []
        self._quick_prompt_key_label = ""
        self._setup_quick_prompt_key(announce=False)
        self._tray = JarvisTray(self)
        self._notify_sig.connect(self._show_notification)
        self._log_sig.connect(self._tray.show_line)
        QApplication.instance().aboutToQuit.connect(self._tray.hide)
        try:
            from memory.config_manager import get_desktop_orb_mode
            self._desk_orb.mode = get_desktop_orb_mode()
        except Exception:
            pass
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._review_sig.connect(self._show_review_overlay)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._look_btns_sig.connect(self._refresh_look_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._chime_sig.connect(QApplication.beep)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)
        sc_comp = QShortcut(QKeySequence("F10"), self)
        sc_comp.activated.connect(self.enter_compact)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(_font(8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(APP_VERSION, C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(_font(11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Setup — things you set once")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched.
        #
        # One panel held twelve buttons: the ones you press once when you set
        # the assistant up sitting next to the ones you flick on and off every
        # day. Separating them by that — setup behind ⚙, everyday behind 🎛 —
        # is what makes each list short enough to read at a glance.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(26, 26)
        self._ctrl_btn.setFont(_font(11))
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addSpacing(4)
        lay.addWidget(self._ctrl_btn)

        self._compact_btn = QPushButton("▭")
        self._compact_btn.setFixedSize(26, 26)
        self._compact_btn.setFont(_font(11))
        self._compact_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_btn.setToolTip("Compact mode — a small card on top of "
                                     "other windows instead of the HUD [F10]")
        self._compact_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._compact_btn.clicked.connect(self.enter_compact)
        lay.addSpacing(4)
        lay.addWidget(self._compact_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(_font(17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(self._title_lbl)
        _sub_text = ("A Friendly Assistant"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(_font(7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(_font(14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(_font(7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(_font(7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", "PRI")
        self._bar_mem = MetricBar("MEM", "ACC2")
        self._bar_net = MetricBar("NET", "GREEN")
        self._bar_gpu = MetricBar("GPU", "ACC")
        self._bar_tmp = MetricBar("TMP", "MUTED_C")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(_font(8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(_font(8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(_font(8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\n" + APP_PROTOCOL,   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(_font(7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(_font(7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        self._agent_panel = AgentPanel(self)
        lay.addWidget(self._agent_panel)

        sep_r = QFrame(); sep_r.setFrameShape(QFrame.Shape.HLine)
        sep_r.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep_r)

        self._repair_panel = RepairPanel(self)
        lay.addWidget(self._repair_panel)

        sep_a = QFrame(); sep_a.setFrameShape(QFrame.Shape.HLine)
        sep_a.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep_a)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(_font(7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(_font(8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_interrupt_btn()
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        # The one control people reach for mid-sentence: a bigger target and
        # text (AcaneloL fork), tinted from the theme rather than fixed hex.
        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(40)
        self._mute_btn.setFont(_font(9.5, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ SETUP")
        hdr.setFont(_font(7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(_font(8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(_font(7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(_font(7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(_font(7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        wake_set_btn = QPushButton("🎙  WAKE WORD SETUP")
        wake_set_btn.setFixedHeight(26)
        wake_set_btn.setFont(_font(7))
        wake_set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        wake_set_btn.setStyleSheet(_BTN_STYLE_DIM)
        wake_set_btn.clicked.connect(self._open_wake_settings)
        lay.addWidget(wake_set_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(_font(7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(_font(7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(_font(7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(_font(7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        w.adjustSize()
        return w

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(_font(7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=26):
            button.setFixedHeight(height)
            button.setFont(_font(7))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._barge_btn = _row(QPushButton())
        self._barge_btn.clicked.connect(self._toggle_barge_in)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        self._theme_btn = _row(QPushButton())
        self._theme_btn.clicked.connect(self._toggle_ui_theme)
        self._scale_btn = _row(QPushButton())
        self._scale_btn.clicked.connect(self._cycle_ui_scale)
        self._caption_btn = _row(QPushButton())
        self._caption_btn.clicked.connect(self._toggle_captions)
        self._desk_orb_btn = _row(QPushButton())
        self._desk_orb_btn.clicked.connect(self._toggle_desktop_orb)
        self._quick_key_btn = _row(QPushButton())
        self._quick_key_btn.clicked.connect(self._cycle_quick_prompt_key)
        self._relay_btn = _row(QPushButton())
        self._relay_btn.clicked.connect(self._toggle_agent_relay)
        self._refresh_look_btns()

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, left: int):
        # Wider with a larger TEXT SIZE, or the longest labels clip.
        _W = int(220 * (1.0 + (_UI_SCALE - 1.0) * 0.45))
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        drawer.setGeometry(left, 54, _W, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, 12)

    def _position_ctrl_drawer(self):
        # Under its own header button rather than under the other one's.
        if hasattr(self, "_ctrl_drawer"):
            left = 12
            try:
                left = max(12, self._ctrl_btn.mapTo(self.centralWidget(),
                                                    QPoint(0, 0)).x() - 8)
            except Exception:
                pass
            self._place_drawer(self._ctrl_drawer, left)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(_font(9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(_font(11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(_font(9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(_font(8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(_font(7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(_font(7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(_font(8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:{_MONO};">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(_font(8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(_font(9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(_font(8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(_font(7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(_font(7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(_font(9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(_font(8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(_font(9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(_font(7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F10] Compact  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")

        if hasattr(self, "_barge_btn"):
            from memory.config_manager import (get_voice_barge_in_enabled,
                                               get_voice_barge_in_mode)
            barge = get_voice_barge_in_enabled()
            by_name = get_voice_barge_in_mode() == "name"
            self._barge_btn.setText(
                "🗣  INTERRUPT: OFF" if not barge
                else "🗣  INTERRUPT: BY NAME" if by_name
                else "🗣  INTERRUPT: BY VOICE")
            self._barge_btn.setStyleSheet(_on if barge else _off)
            self._barge_btn.setToolTip(
                "Talk over JARVIS and it pauses to listen; if no words are heard, "
                "it carries on. Click for name-only." if barge and not by_name
                else "Only the wake phrase stops JARVIS mid-answer — a television "
                     "or a door cannot. Click to turn off." if barge
                else "Let talking over JARVIS interrupt it. Click to cycle: by "
                     "voice, by name only, off.")


    # Label, tooltip and log wording per centrepiece, in toggle order
    # (memory.config_manager.HUD_STYLES).
    _HUD_STYLE_TEXT = {
        "face": ("🧑  HUD: ANIMATED FACE", "the animated face",
                 "An animated head that speaks your words and shows what "
                 "JARVIS is doing."),
        "core": ("◉  HUD: REACTOR CORE", "the reactor core",
                 "A reactor core that turns with the state and moves with "
                 "your voice."),
        "orb":  ("◈  HUD: WIREFRAME ORB", "the wireframe orb",
                 "A wireframe orb that spins faster while thinking and "
                 "ripples with your voice."),
        "anime": ("🌸  HUD: ANIME FACE", "the anime face",
                  "A drawn anime face with the same lip-sync, blinks and "
                  "expressions as the animated head."),
    }

    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        label, _, tip = self._HUD_STYLE_TEXT.get(get_hud_style(),
                                                 self._HUD_STYLE_TEXT["face"])
        # No style is "off", so every one reads as active — this is a choice
        # between things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText(label)
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(f"{tip} Tap to switch to the next style.")

    def _toggle_hud_style(self):
        """Step to the next centrepiece. Every object stays in memory, so the
        change is instant and switching back costs nothing."""
        from memory.config_manager import HUD_STYLES, get_hud_style, save_hud_style
        cur = get_hud_style()
        i = HUD_STYLES.index(cur) if cur in HUD_STYLES else 0
        want = cur
        for k in range(1, len(HUD_STYLES) + 1):
            cand = HUD_STYLES[(i + k) % len(HUD_STYLES)]
            try:
                ok = self.hud.can_show(cand)
            except Exception:
                ok = cand == "core"
            if ok:
                want = cand
                break
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(f"SYS: HUD switched to {self._HUD_STYLE_TEXT[want][1]}.")

    def _refresh_look_btns(self):
        from memory.config_manager import get_ui_scale, get_ui_theme
        style = self._hud_btn.styleSheet()
        soft = get_ui_theme() == "soft"
        self._theme_btn.setText("◐  THEME: SOFT" if soft else "◑  THEME: NEON")
        self._theme_btn.setStyleSheet(style)
        self._theme_btn.setToolTip(
            "Slate background, muted colours. Tap for the original neon look."
            if soft else
            "The original near-black neon look. Tap for a softer, low-glare one.")
        from memory.config_manager import get_captions_enabled, get_desktop_orb_mode
        off_style = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        on = get_captions_enabled()
        self._caption_btn.setText(f"{'▣' if on else '□'}  CAPTIONS: {'ON' if on else 'OFF'}")
        self._caption_btn.setStyleSheet(style if on else off_style)
        self._caption_btn.setToolTip(
            "The latest exchange, typed out under the HUD in large text. "
            f"Click to turn {'off' if on else 'on'}.")
        mode = get_desktop_orb_mode()
        label, tip = {
            "off":      ("□  DESKTOP ORB: OFF", "No orb outside this window."),
            "speaking": ("▣  DESKTOP ORB: SPEAKING",
                         "A small orb at the top of the screen while JARVIS "
                         "speaks and this window is minimised or behind "
                         "another app."),
            "always":   ("▣  DESKTOP ORB: ALWAYS",
                         "A small orb at the top of the screen whenever this "
                         "window is minimised or behind another app — "
                         "listening, thinking, speaking or muted."),
        }[mode]
        self._desk_orb_btn.setText(label)
        self._desk_orb_btn.setStyleSheet(off_style if mode == "off" else style)
        self._desk_orb_btn.setToolTip(f"{tip} Click to cycle: while speaking, "
                                      "always, off.")
        from memory.config_manager import get_quick_prompt_key
        key = get_quick_prompt_key()
        self._quick_key_btn.setText(f"{'□' if key == 'off' else '⌨'}  QUICK PROMPT: "
                                    f"{key.upper()}")
        self._quick_key_btn.setStyleSheet(off_style if key == "off" else style)
        self._quick_key_btn.setToolTip(
            "The key that opens a one-line prompt in the middle of the screen, "
            "from any app. Click to cycle: F9, Ctrl+Alt+J, off (F9 is also an "
            "IDE debugger key).")
        from memory.config_manager import get_agent_settings
        relay = get_agent_settings()["relay_enabled"]
        self._relay_btn.setText(f"{'🔔' if relay else '🔕'}  AGENT SESSIONS: "
                                f"{'ON' if relay else 'OFF'}")
        self._relay_btn.setStyleSheet(style if relay else off_style)
        self._relay_btn.setToolTip(
            "Announce your own Claude Code and Codex sessions: a turn that ran "
            "30 s or more has finished, or a session is waiting for your "
            "permission. Adds a hook to ~/.claude/settings.json and "
            "~/.codex/hooks.json (a backup is kept); turning it off removes it.")
        pct = int(round(get_ui_scale() * 100))
        self._scale_btn.setText(f"🔠  TEXT SIZE: {pct}%")
        self._scale_btn.setStyleSheet(style)
        self._scale_btn.setToolTip(
            "Tap to make all text larger. Takes effect after a restart"
            + (f" (now showing {int(round(_UI_SCALE * 100))}%)."
               if abs(_UI_SCALE - get_ui_scale()) > 1e-6 else "."))

    def _toggle_ui_theme(self):
        """Switch the base palette live; the chosen UI colour stays applied."""
        from memory.config_manager import get_ui_theme, save_ui_theme
        want = "neon" if get_ui_theme() == "soft" else "soft"
        save_ui_theme(want)
        old = current_palette()
        apply_ui_theme(want)
        accent = (_read_full_config().get("ui_color") or "").strip()
        if accent and accent.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(accent)
        retheme_all_widgets(old, current_palette())
        # Tints built by _mix_hex are not palette colours; rebuild them.
        self._style_mute_btn()
        self._style_interrupt_btn()
        self._caption._restyle(C.TEXT)
        self._compact.restyle()
        self._tray.restyle()
        self._quick_prompt.restyle()
        self._agent_panel.restyle()
        self._repair_panel.restyle()
        self._refresh_hud_btn()
        self._refresh_look_btns()
        self._log.append_log(f"SYS: Theme set to {want}.")

    def _toggle_captions(self):
        from memory.config_manager import get_captions_enabled, save_captions_enabled
        want = not get_captions_enabled()
        save_captions_enabled(want)
        if not want:
            self._caption.hide()
        elif self._caption.toPlainText():
            self._caption.show()
        self._refresh_look_btns()
        self._log.append_log(f"SYS: Captions {'on' if want else 'off'}.")

    def _toggle_desktop_orb(self):
        from memory.config_manager import DESKTOP_ORB_MODES, get_desktop_orb_mode
        cur = get_desktop_orb_mode()
        self.set_desktop_orb_mode(DESKTOP_ORB_MODES[(DESKTOP_ORB_MODES.index(cur) + 1)
                                                    % len(DESKTOP_ORB_MODES)])

    def set_desktop_orb_mode(self, want: str):
        from memory.config_manager import save_desktop_orb_mode
        save_desktop_orb_mode(want)
        self._desk_orb.mode = want
        self._desk_orb.wake()
        self._refresh_look_btns()
        self._log.append_log({"off": "SYS: Desktop orb off.",
                              "speaking": "SYS: Desktop orb shows while JARVIS speaks.",
                              "always": "SYS: Desktop orb always shows when this "
                                        "window is out of sight."}[want])

    def _toggle_agent_relay(self):
        from memory.config_manager import get_agent_settings
        want = not get_agent_settings()["relay_enabled"]

        def work():
            try:
                from core.agent_relay import install
                msg = install(want)
            except Exception as e:
                msg = f"could not change the agent hooks: {e}"
            self._log_sig.emit(f"SYS: {msg}")
            self._look_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="agent-relay").start()

    def _cycle_quick_prompt_key(self):
        from memory.config_manager import (QUICK_PROMPT_KEYS, get_quick_prompt_key,
                                           save_quick_prompt_key)
        cur = get_quick_prompt_key()
        want = QUICK_PROMPT_KEYS[(QUICK_PROMPT_KEYS.index(cur) + 1) % len(QUICK_PROMPT_KEYS)]
        save_quick_prompt_key(want)
        self._setup_quick_prompt_key()
        if want == "off":
            self._log_sig.emit("SYS: Quick prompt key off — it is still in the tray menu.")
        self._refresh_look_btns()

    def _cycle_ui_scale(self):
        from memory.config_manager import UI_SCALES, get_ui_scale, save_ui_scale
        cur = get_ui_scale()
        i = UI_SCALES.index(cur) if cur in UI_SCALES else 0
        save_ui_scale(UI_SCALES[(i + 1) % len(UI_SCALES)])
        self._refresh_look_btns()
        self._log.append_log(
            f"SYS: Text size {int(round(get_ui_scale() * 100))}% saved — "
            "restart JARVIS to apply it.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _toggle_barge_in(self):
        """Cycle: off → by voice → by name only → off."""
        from memory.config_manager import (
            get_voice_barge_in_enabled, get_voice_barge_in_mode,
            save_voice_barge_in_enabled, save_voice_barge_in_mode,
        )
        on, mode = get_voice_barge_in_enabled(), get_voice_barge_in_mode()
        if not on:
            on, mode = True, "voice"
        elif mode == "voice":
            mode = "name"
        else:
            on, mode = False, "voice"
        save_voice_barge_in_enabled(on)
        save_voice_barge_in_mode(mode)
        msg = ("Interrupt by voice off." if not on
               else f"Interrupt {'by name' if mode == 'name' else 'by voice'} on.")
        if self.on_barge_in:
            try:
                msg = self.on_barge_in(on, mode) or msg
            except Exception as e:
                self._log.append_log(f"ERR: Voice interrupt failed — {e}")
                save_voice_barge_in_enabled(False)
                msg = "Interrupt by voice off."
        self._refresh_talk_btns()
        self._log.append_log(f"SYS: {msg}")

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        name_changed = (name.strip() or "JARVIS") != self._assistant_name
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self._caption.set_assistant_name(self._assistant_name)
        self._compact.set_name(self._assistant_name)
        self._quick_prompt.restyle()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()
        # An unset wake phrase is derived from the name ("hey <name>").
        if name_changed and self.on_wake_settings:
            try:
                self.on_wake_settings()
            except Exception:
                pass

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _open_wake_settings(self):
        def log(m: str) -> None:
            self._log_sig.emit(m if m.startswith(("SYS:", "ERR:")) else f"SYS: {m}")
        ov = WakeWordOverlay(log=log, parent=self.centralWidget())
        ov.applied.connect(self._on_wake_settings_applied)
        self._centre_overlay(ov)
        self._wake_overlay = ov             # keep a reference so it isn't GC'd

    def _on_wake_settings_applied(self):
        self._log.append_log("SYS: Wake word settings saved.")
        if self.on_wake_settings:
            try:
                self.on_wake_settings()
            except Exception as e:
                self._log.append_log(f"ERR: Wake word — {e}")
        self._refresh_wake_btns()

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov
        # In compact mode the HUD is hidden, and a gate nobody can see would
        # leave the action hanging: bring the HUD back with it.
        if self._compact.isVisible():
            self.exit_compact()

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    # ── Self-repair review ───────────────────────────────────────────────────

    def _show_review_overlay(self):
        from core import self_repair
        r = self_repair.current()
        self._hide_review_overlay()
        if r is None or r.state != "review":
            self._log.append_log("SYS: No self-repair change is waiting for review.")
            return
        ov = ReviewOverlay(r, self_repair.diff_text(r), parent=self.centralWidget())
        ov.decided.connect(self._on_review_decided)
        self._centre_overlay(ov)
        self._review_overlay = ov
        if self._compact.isVisible():
            self.exit_compact()

    def _hide_review_overlay(self):
        ov = getattr(self, "_review_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._review_overlay = None

    def _on_review_decided(self, choice: str):
        self._hide_review_overlay()
        if choice == "close":
            return
        from core import self_repair
        fn = self_repair.apply if choice == "apply" else self_repair.discard
        self._log.append_log("SYS: Self-repair — applying…" if choice == "apply"
                             else "SYS: Self-repair — discarding…")

        def work():
            try:
                self._log_sig.emit(f"SYS: Self-repair — {fn()}")
            except Exception as e:
                self._log_sig.emit(f"ERR: Self-repair — {e}")
        threading.Thread(target=work, daemon=True, name="self-repair-apply").start()

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_interrupt_btn(self):
        """Tinted from the theme, so the soft palette does not keep a neon-era
        near-black red."""
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_mix_hex(C.BG, C.MUTED_C, 0.08)}; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 5px;
            }}
            QPushButton:hover {{
                background: {_mix_hex(C.BG, C.MUTED_C, 0.14)}; border: 1px solid {C.RED};
            }}
            QPushButton:pressed {{
                background: {_mix_hex(C.BG, C.MUTED_C, 0.22)};
            }}
        """)

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_mix_hex(C.BG, C.MUTED_C, 0.10)}; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 5px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_mix_hex(C.BG, C.GREEN, 0.07)}; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 5px;
                }}
                QPushButton:hover {{ background: {_mix_hex(C.BG, C.GREEN, 0.14)}; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self.submit_text(txt)

    def submit_text(self, txt: str):
        """Send typed text to the assistant — the HUD's box and the quick
        prompt both come through here."""
        self._log_sig.emit(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _show_notification(self, text: str):
        """A tray balloon — for news that cannot be spoken yet (asleep)."""
        tray = getattr(self._tray, "tray", None)
        if tray is not None and text:
            try:
                tray.showMessage(self._assistant_name, text[:200],
                                 tray.MessageIcon.Information, 6000)
            except Exception:
                pass

    def toggle_quick_prompt(self):
        self._quick_prompt.toggle()

    def _setup_quick_prompt_key(self, announce: bool = True):
        """(Re)bind the quick-prompt key: system-wide on Windows when the key
        is free, else as a shortcut inside the HUD and the card."""
        from core.hotkey import GlobalHotkey, chord_label, parse_chord, qt_sequence
        from memory.config_manager import get_quick_prompt_key
        if self._quick_hotkey is not None:
            self._quick_hotkey.stop()
            self._quick_hotkey = None
        for sc in self._quick_shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._quick_shortcuts = []
        key = get_quick_prompt_key()
        chord = parse_chord(key) if key != "off" else ()
        self._quick_prompt_key_label = chord_label(chord) if chord else ""
        self._quick_prompt_scope = "off"
        if not chord:
            return
        hk = GlobalHotkey(self._quick_prompt_sig.emit, chord)
        self._quick_prompt_scope = hk.start()
        if self._quick_prompt_scope == "global":
            # Windows delivers the key to us wherever the focus is — a Qt
            # shortcut as well would toggle the prompt twice.
            self._quick_hotkey = hk
        else:
            for w in (self, self._compact):
                sc = QShortcut(QKeySequence(qt_sequence(chord)), w)
                sc.activated.connect(self.toggle_quick_prompt)
                self._quick_shortcuts.append(sc)
            if _OS == "Windows":
                self._log_sig.emit(
                    f"SYS: {self._quick_prompt_key_label} is taken by another program — "
                    "the quick prompt opens with it only inside JARVIS. "
                    "Pick another key in CONTROLS.")
        if announce:
            where = ("anywhere" if self._quick_prompt_scope == "global"
                     else "inside JARVIS")
            self._log_sig.emit(f"SYS: Quick prompt: {self._quick_prompt_key_label} "
                               f"({where}).")

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        orb = getattr(self, "_desk_orb", None)
        if orb is not None:
            orb.wake()
        card = getattr(self, "_compact", None)
        if card is not None:
            card.refresh_state()
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.refresh()

    # ── compact mode ─────────────────────────────────────────────────────────

    def enter_compact(self):
        """Swap the HUD for the compact card. Remembered across restarts."""
        if not self._ready:
            return          # first-run setup lives in the HUD
        if getattr(self, "_confirm_overlay", None) is not None:
            # The gate is answered on the HUD; hiding it would strand it.
            self._log_sig.emit("SYS: Answer the confirmation first.")
            return
        try:
            from memory.config_manager import save_compact_mode
            save_compact_mode(True)
        except Exception:
            pass
        self._compact.place()
        self._compact.show()
        self._compact.raise_()
        # Hidden rather than minimised: the card is the way back, and a
        # taskbar button for a window nobody can see only invites confusion.
        self.hide()

    def exit_compact(self):
        """Back to the full HUD, where the card was."""
        try:
            from memory.config_manager import save_compact_mode
            save_compact_mode(False)
        except Exception:
            pass
        self._compact.hide()
        if self.isMinimized() or self.isFullScreen():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    # Printed when the event loop ends, so an exit is never a mystery again.
    quit_reason = "the event loop was stopped (no quit path recorded)"

    def closeEvent(self, event):
        # The ✕ still quits, as it always has — now on purpose rather than
        # through Qt's last-window rule, which is switched off (JarvisUI).
        super().closeEvent(event)
        if event.isAccepted():
            if self.quit_reason.startswith("the event loop"):
                self.quit_reason = "the window was closed"
            self._quit_now()

    def quit_app(self, reason: str = "Quit from the card or the tray"):
        """Quit from the card or the tray: the same exit as the window's ✕."""
        self.quit_reason = reason
        self.close()

    def _quit_now(self):
        self._compact.hide()
        self._quick_prompt.hide()
        self._tray.hide()
        if self._quick_hotkey is not None:
            self._quick_hotkey.stop()
        QApplication.quit()

    def changeEvent(self, event):
        # Minimised or sent behind another app mid-answer: the desktop orb
        # takes over from the HUD right away, not at the next state change.
        super().changeEvent(event)
        if event.type() in (event.Type.ActivationChange, event.Type.WindowStateChange):
            orb = getattr(self, "_desk_orb", None)
            if orb is not None:
                orb.wake()

    def _on_caption_line(self, text: str):
        try:
            from memory.config_manager import get_captions_enabled
            if not get_captions_enabled():
                return
        except Exception:
            pass
        self._caption.show_line(text)
        if not self._caption.isVisible() and self._caption.toPlainText():
            self._caption.show()

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        # Quitting is explicit — the window's ✕ (MainWindow.closeEvent), Quit
        # on the card or the tray, or the shutdown tool. Qt's default quits as
        # soon as the last *visible* ordinary window closes, and in compact
        # mode the HUD is hidden: then any passing top-level window that closed
        # ended the whole program, silently. A live session did exactly that.
        self._app.setQuitOnLastWindowClosed(False)
        self._app.aboutToQuit.connect(
            lambda: print(f"[UI] Quitting — {self._win.quit_reason}", flush=True))
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()
        try:
            from memory.config_manager import get_compact_mode
            if get_compact_mode():
                self._win.enter_compact()
        except Exception:
            pass

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    def show_review(self) -> None:
        """Thread-safe: put the self-repair change waiting for review on the HUD."""
        self._win._review_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def on_wake_settings(self):
        return self._win.on_wake_settings

    @on_wake_settings.setter
    def on_wake_settings(self, cb):
        self._win.on_wake_settings = cb

    def chime(self) -> None:
        """Thread-safe: the short system sound that says 'I'm listening'."""
        self._win._chime_sig.emit()

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            self._win.hud.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    @property
    def on_barge_in(self):
        return self._win.on_barge_in

    @on_barge_in.setter
    def on_barge_in(self, cb):
        self._win.on_barge_in = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def notify(self, text: str):
        """Thread-safe: a tray notification."""
        self._win._notify_sig.emit(str(text or ""))

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over JARVIS is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                       bool(muted), str(audio_source or ""))

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")