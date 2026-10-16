"""
What the desktop looks like right now — the observations verifiers compare.

Every reader here is cheap (a few Win32 calls, one small screen grab), returns
plain data, and returns None / [] instead of raising when it cannot look: on
macOS and Linux most of them simply have nothing to say yet, and a verifier
then answers UNVERIFIED instead of guessing (core/verify.py).

Coordinates are the ones pyautogui clicks in. pyautogui makes the process
DPI-aware when it is imported, and the Win32 calls and mss grabs below run in
the same process, so a window rectangle, a cursor position and a grab region
all share one space.

Screen samples are small grey grids kept in memory only — a verifier compares
two of them; nothing is saved or sent anywhere.
"""
from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from typing import Optional

_OS = platform.system()


# ── windows and processes ───────────────────────────────────────────────────

def windows() -> list:
    """Visible top-level app windows (actions/_app_closer.Win), Windows only."""
    if _OS != "Windows":
        return []
    try:
        from actions._app_closer import _win_list
        return _win_list()
    except Exception:
        return []


def window(hwnd: int) -> Optional[dict]:
    """One top-level window: title, process, rectangle, minimised/maximised."""
    if _OS != "Windows" or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        if not u.IsWindow(hwnd):
            return None
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, n + 1))
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        return {"hwnd": int(hwnd), "pid": int(pid.value), "exe": _exe(pid.value),
                "title": buf.value, "rect": (r.left, r.top, r.right, r.bottom),
                "minimized": bool(u.IsIconic(hwnd)), "maximized": bool(u.IsZoomed(hwnd)),
                "visible": bool(u.IsWindowVisible(hwnd))}
    except Exception:
        return None


def foreground() -> Optional[dict]:
    if _OS != "Windows":
        return None
    try:
        import ctypes
        return window(int(ctypes.windll.user32.GetForegroundWindow() or 0))
    except Exception:
        return None


def window_at(x: int, y: int) -> Optional[dict]:
    """The top-level window under a point."""
    if _OS != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        h = u.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        h = u.GetAncestor(h, 2) if h else 0                      # GA_ROOT
        return window(int(h or 0))
    except Exception:
        return None


_exe_cache: dict[int, str] = {}


def _exe(pid: int) -> str:
    if pid not in _exe_cache:
        try:
            import psutil
            n = psutil.Process(int(pid)).name().lower()
            _exe_cache[pid] = n[:-4] if n.endswith(".exe") else n
        except Exception:
            return ""
    return _exe_cache[pid]


def processes() -> dict[int, str]:
    """pid -> process name (lower case, no .exe), every OS."""
    try:
        import psutil
    except ImportError:
        return {}
    out = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            n = (p.info.get("name") or "").lower()
            out[int(p.info["pid"])] = n[:-4] if n.endswith(".exe") else n
        except Exception:
            continue
    return out


def is_me(pid: int) -> bool:
    return int(pid or 0) == os.getpid()


# ── pointer and keyboard focus ──────────────────────────────────────────────

def cursor() -> Optional[tuple[int, int]]:
    try:
        import pyautogui
        p = pyautogui.position()
        return int(p[0]), int(p[1])
    except Exception:
        return None


@dataclass
class Focus:
    control_type: str
    name: str
    value: Optional[str]      # None: the control does not expose its text
    pid: int
    password: bool = False

    def same_control(self, other: "Focus | None") -> bool:
        return (other is not None and self.control_type == other.control_type
                and self.name == other.name and self.pid == other.pid)


def focused_control() -> Optional[Focus]:
    """The UI Automation element that has keyboard focus, and its text when
    it exposes one (edit boxes, address bars, most web inputs). Asked of UIA
    directly — bfrpaulondev walked up to 500 descendants of the window asking
    each whether it had focus, which took seconds in a browser."""
    if _OS != "Windows":
        return None
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        el = IUIA().iuia.GetFocusedElement()
        if el is None:
            return None
        info = UIAElementInfo(el)
        password = False
        try:
            password = bool(el.CurrentIsPassword)
        except Exception:
            pass
        value = None
        if not password:
            try:
                from pywinauto.controls.uiawrapper import UIAWrapper
                v = UIAWrapper(info).iface_value.CurrentValue
                value = None if v is None else str(v)
            except Exception:
                value = None
        return Focus(str(info.control_type or ""), str(info.name or "")[:120], value,
                     int(info.process_id or 0), password)
    except Exception:
        return None


# ── screen samples ──────────────────────────────────────────────────────────

GRID = (48, 64)          # rows, columns of the grey grid a region is reduced to
CHANGE_LEVEL = 6         # a cell must move this many grey levels (0–255)


def virtual_desktop() -> Optional[tuple[int, int, int, int]]:
    try:
        import mss
        with mss.mss() as s:
            m = s.monitors[0]
        return (m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])
    except Exception:
        return None


def clip(rect, bounds=None) -> Optional[tuple[int, int, int, int]]:
    bounds = bounds or virtual_desktop()
    if not rect or not bounds:
        return None
    l, t, r, b = (int(v) for v in rect)
    l, t = max(l, bounds[0]), max(t, bounds[1])
    r, b = min(r, bounds[2]), min(b, bounds[3])
    return (l, t, r, b) if r - l >= 4 and b - t >= 4 else None


def sample(rect) -> Optional["object"]:
    """A region reduced to a GRID of mean grey levels (a numpy array), so a
    small change — a ticked box, a highlighted row — moves at least one cell,
    while the grid stays a few kilobytes."""
    box = clip(rect)
    if box is None:
        return None
    try:
        import mss
        import numpy as np
        l, t, r, b = box
        with mss.mss() as s:
            img = np.asarray(s.grab({"left": l, "top": t, "width": r - l, "height": b - t}),
                             dtype=np.uint8)
        g = (img[:, :, 0].astype(np.float32) * 0.114 + img[:, :, 1] * 0.587
             + img[:, :, 2] * 0.299)
        rows, cols = min(GRID[0], g.shape[0]), min(GRID[1], g.shape[1])
        ys = np.linspace(0, g.shape[0], rows + 1).astype(int)
        xs = np.linspace(0, g.shape[1], cols + 1).astype(int)
        # Block means through a summed-area table: exact, and fast on a
        # 3440×1440 window.
        sat = np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        area = np.outer(np.diff(ys), np.diff(xs)).astype(np.float32)
        tot = sat[ys[1:]][:, xs[1:]] - sat[ys[:-1]][:, xs[1:]] - sat[ys[1:]][:, xs[:-1]] \
            + sat[ys[:-1]][:, xs[:-1]]
        return tot / np.maximum(area, 1.0)
    except Exception:
        return None


def noise(a, b):
    """Cells that differ between two "before" samples — a blinking caret, a
    spinner, a video — and so prove nothing when they differ after."""
    try:
        import numpy as np
        if a is None or b is None or a.shape != b.shape:
            return None
        return np.abs(a - b) >= CHANGE_LEVEL
    except Exception:
        return None


def changed_cells(before, after, mask=None) -> int:
    """How many cells moved, ignoring the noisy ones. -1: cannot compare."""
    try:
        import numpy as np
        if before is None or after is None or before.shape != after.shape:
            return -1
        diff = np.abs(after - before) >= CHANGE_LEVEL
        if mask is not None and mask.shape == diff.shape:
            diff &= ~mask
        return int(diff.sum())
    except Exception:
        return -1


def around(x: int, y: int, w: int = 240, h: int = 160) -> tuple[int, int, int, int]:
    return (int(x) - w // 2, int(y) - h // 2, int(x) + w // 2, int(y) + h // 2)


def settle(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


# ── the user's own browser ──────────────────────────────────────────────────

BROWSERS = {"chrome", "msedge", "firefox", "opera", "brave", "vivaldi", "chromium",
            "yandex", "arc"}


def address_bar(hwnd: int) -> Optional[str]:
    """What a browser window's address bar shows, read through UI Automation.

    In Chrome, Edge and Firefox the address bar is the first Edit control in
    the window's tree, so FindFirst returns it in 8–30 ms (measured) without
    touching the page. bfrpaulondev/Mark-BP read it by pressing Ctrl+L, Ctrl+C
    in the window — stealing focus and the clipboard; this only looks."""
    if _OS != "Windows" or not hwnd:
        return None
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uiawrapper import UIAWrapper
        iuia = IUIA().iuia
        root = iuia.ElementFromHandle(int(hwnd))
        cond = iuia.CreatePropertyCondition(30003, 50004)    # ControlType == Edit
        el = root.FindFirst(4, cond)                          # TreeScope_Descendants
        if el is None:
            return None
        v = UIAWrapper(UIAElementInfo(el)).iface_value.CurrentValue
        return None if v is None else str(v)
    except Exception:
        return None


# ── named controls (UI Automation) ──────────────────────────────────────────

# Button, MenuItem, Hyperlink, TabItem, ListItem, CheckBox, RadioButton, Edit,
# ComboBox, TreeItem, SplitButton: what "click X" can mean.
_CLICKABLE_TYPES = (50000, 50011, 50005, 50019, 50007, 50002, 50013, 50004,
                    50003, 50024, 50031)


def controls(hwnd: int) -> list[dict]:
    """The on-screen, clickable controls of a window: name, UIA control type
    id, and rectangle. One FindAll call: 0.02–0.2 s for an ordinary window,
    about 1 s for a busy slicer UI (measured). Electron apps (Slack, ChatGPT,
    Claude) expose only their caption buttons, which is why vision stays
    behind this."""
    if _OS != "Windows" or not hwnd:
        return []
    try:
        from pywinauto.uia_defines import IUIA
        iuia = IUIA().iuia
        root = iuia.ElementFromHandle(int(hwnd))
        kinds = iuia.CreateOrConditionFromArray(
            [iuia.CreatePropertyCondition(30003, t) for t in _CLICKABLE_TYPES])
        visible = iuia.CreatePropertyCondition(30022, False)          # IsOffscreen
        found = root.FindAll(4, iuia.CreateAndCondition(kinds, visible))
        out = []
        for i in range(min(found.Length, 2000)):
            el = found.GetElement(i)
            try:
                r = el.CurrentBoundingRectangle
                if r.right - r.left < 2 or r.bottom - r.top < 2:
                    continue
                out.append({"name": str(el.CurrentName or ""), "type": int(el.CurrentControlType),
                            "rect": (r.left, r.top, r.right, r.bottom),
                            "enabled": bool(el.CurrentIsEnabled)})
            except Exception:
                continue
        return out
    except Exception:
        return []


def taskbar() -> int:
    if _OS != "Windows":
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None) or 0)
    except Exception:
        return 0
