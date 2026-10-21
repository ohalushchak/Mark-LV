"""
Calls in Telegram, Viber, Slack and through Phone Link: is one going on, is
something ringing, and the controls that answer, decline and hang up.

Two signals, both read locally, nothing sent anywhere:

  In a call   Windows records which app holds the microphone right now —
              HKCU\\…\\CapabilityAccessManager\\ConsentStore\\microphone, one key
              per app, LastUsedTimeStop = 0 while it is in use. A messenger
              that holds the mic is in a call (or recording a voice note; the
              assistant keeps quiet for that too). JAlmuscaty found the same
              fact through pycaw audio sessions; the registry needs no audio
              API and also covers Phone Link, whose calls run through the PC.
  Ringing     A window of one of these apps that offers BOTH an accept and a
              decline control. Named controls first (Telegram, Phone Link),
              classes for Viber, whose Qt Quick buttons have no names but say
              what they are in their class ("ViberCallButton_QMLTYPE_1460").

The control lists are guesses until seen live: an incoming call could not be
watched while this was written. `describe_window` prints what a call window
offers, so the first real one tells which names to add.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

APPS = ("telegram", "viber", "slack", "phone")
LABEL = {"telegram": "Telegram", "viber": "Viber", "slack": "Slack", "phone": "Phone Link"}

# process name (lower case, no .exe) → app
_EXE = {"telegram": "telegram", "viber": "viber", "slack": "slack",
        "phoneexperiencehost": "phone", "yourphone": "phone"}
# packaged app family (before the "_publisher" part, lower case) → app
_PACKAGED = {"com.tinyspeck.slackdesktop": "slack", "microsoft.yourphone": "phone"}

_ACCEPT = re.compile(r"^\s*(answer|accept|pick up|відповісти|прийняти|ответить|принять)\b|"
                     r"(accept|answer)", re.IGNORECASE)
_DECLINE = re.compile(r"^\s*(decline|reject|ignore|відхилити|отклонить|сбросить)\b|"
                      r"(decline|reject)", re.IGNORECASE)
# "Leave" alone is not enough: Slack's "Leave channel" is always on screen.
_END = re.compile(r"^\s*(end( the)? call|hang ?up|leave( the)? (huddle|call)|"
                  r"завершити( дзвінок)?|покласти( слухавку| трубку)?|завершить( звонок)?|"
                  r"положить трубку)\s*$|(endcall|hangup|leavecall|leavehuddle)", re.IGNORECASE)

_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


# ── one thread for the COM work ─────────────────────────────────────────────
# UI Automation and the audio-session API are COM. Done on whichever executor
# thread asked, their pointers sometimes ended up in garbage that the
# collector freed later on ANOTHER thread — an access violation that killed
# the process (reproduced: a minute of call checks beside the HUD's WMI
# thread). So all of it runs on this one thread, which collects its own
# garbage after every job.

_com_jobs: "queue.Queue" = None          # created with the thread
_com_thread: Optional[threading.Thread] = None
_com_lock = threading.Lock()


def _com_loop() -> None:
    import gc
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    while True:
        fn, args, box, done = _com_jobs.get()
        try:
            box["value"] = fn(*args)
        except BaseException as e:            # handed back to the caller
            box["error"] = e
        finally:
            box = None
            gc.collect()
            done.set()


def _com(fn, *args, timeout: float = 20.0):
    """Run fn(*args) on the COM thread and return its result."""
    global _com_jobs, _com_thread
    if threading.current_thread() is _com_thread:
        return fn(*args)
    import queue
    with _com_lock:
        if _com_thread is None or not _com_thread.is_alive():
            _com_jobs = queue.Queue()
            _com_thread = threading.Thread(target=_com_loop, daemon=True, name="calls-com")
            _com_thread.start()
    box: dict = {}
    done = threading.Event()
    _com_jobs.put((fn, args, box, done))
    if not done.wait(timeout):
        raise TimeoutError(f"{getattr(fn, '__name__', fn)} took over {timeout:.0f} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── who holds the microphone ────────────────────────────────────────────────

def mic_in_use() -> set[str]:
    """The apps (of APPS) holding the microphone right now. Windows only."""
    try:
        import winreg
    except ImportError:
        return set()
    out: set[str] = set()

    def in_use(key) -> bool:
        try:
            stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
            start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
            return int(stop) == 0 and int(start) > 0
        except OSError:
            return False

    def walk(path: str, name_of) -> None:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            app = name_of(sub)
            if not app:
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    if in_use(k):
                        out.add(app)
            except OSError:
                continue

    def nonpackaged(sub: str) -> str:
        exe = sub.replace("#", "\\").rsplit("\\", 1)[-1].lower()
        return _EXE.get(exe[:-4] if exe.endswith(".exe") else exe, "")

    def packaged(sub: str) -> str:
        return _PACKAGED.get(sub.split("_")[0].lower(), "")

    walk(_MIC_KEY + r"\NonPackaged", nonpackaged)
    walk(_MIC_KEY, packaged)
    return out | capturing()


def capturing() -> set[str]:
    """The apps (of APPS) with an ACTIVE audio capture session on any
    microphone. Seen live (2026-10-21): in a Viber call the privacy record
    above stayed old — Viber recorded from the webcam's microphone, not the
    default one — while its capture session on that device was active
    (JAlmuscaty read call state from audio sessions too)."""
    try:
        return _com(_capturing)
    except Exception:
        return set()


def _capturing() -> set[str]:
    try:
        import comtypes
        from comtypes import CLSCTX_ALL, CLSCTX_INPROC_SERVER
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator
        import psutil
    except Exception:
        return set()
    out: set[str] = set()
    devices = mgr = en = c = dev = None
    try:
        # Neither pycaw's AudioUtilities.GetAllDevices nor its README's
        # cast(dev.Activate(...), POINTER(X)): cast makes a second pointer
        # object without an AddRef, so the collector released the interface
        # twice and the process died (access violation, seen live in a call).
        # QueryInterface counts its reference.
        devices = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                            CLSCTX_INPROC_SERVER).EnumAudioEndpoints(1, 1)
        for d in range(devices.GetCount()):              # eCapture, DEVICE_STATE_ACTIVE
            try:
                dev = devices.Item(d)
                mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None
                                   ).QueryInterface(IAudioSessionManager2)
                en = mgr.GetSessionEnumerator()
                for i in range(en.GetCount()):
                    c = en.GetSession(i).QueryInterface(IAudioSessionControl2)
                    if c.GetState() != 1:                                     # AudioSessionStateActive
                        continue
                    pid = c.GetProcessId()
                    if not pid:
                        continue
                    n = psutil.Process(pid).name().lower()
                    app = _EXE.get(n[:-4] if n.endswith(".exe") else n)
                    if app:
                        out.add(app)
            except Exception:
                continue
    except Exception:
        pass
    finally:
        # Every COM pointer released here, on the thread that made it.
        devices = mgr = en = c = dev = None
    return out


# ── call windows and their controls ─────────────────────────────────────────

@dataclass
class Control:
    name: str
    cls: str
    rect: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.rect
        return (l + r) // 2, (t + b) // 2

    def says(self, rx: re.Pattern) -> bool:
        return bool(rx.search(self.name or "")) or bool(rx.search(_cls_words(self.cls)))


def _cls_words(cls: str) -> str:
    """'DeclineCallButton_QMLTYPE_12' → 'declinecallbutton' (classes have no spaces)."""
    return re.split(r"_QML|_QMLTYPE", cls or "")[0].lower()


@dataclass
class CallWindow:
    app: str
    hwnd: int
    title: str
    controls: list[Control] = field(default_factory=list)

    def find(self, rx: re.Pattern) -> list[Control]:
        return [c for c in self.controls if c.says(rx)]


def app_windows() -> list[tuple[str, int, str]]:
    """(app, hwnd, title) for every visible top-level window of the call
    apps — owned and untitled ones too: a call pops up as one of those."""
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
    except Exception:
        return []
    pids: dict[int, str] = {}
    for p in psutil.process_iter(["pid", "name"]):
        n = (p.info.get("name") or "").lower()
        app = _EXE.get(n[:-4] if n.endswith(".exe") else n)
        if app:
            pids[int(p.info["pid"])] = app
    if not pids:
        return []
    u = ctypes.windll.user32
    dwm = getattr(ctypes.windll, "dwmapi", None)
    out: list[tuple[str, int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lp):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            app = pids.get(int(pid.value))
            if not app:
                return True
            if dwm is not None:
                cloaked = ctypes.c_int(0)
                dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left < 60 or r.bottom - r.top < 40:
                return True
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(max(1, n + 1))
            u.GetWindowTextW(hwnd, buf, n + 1)
            out.append((app, int(hwnd), buf.value))
        except Exception:
            pass
        return True

    u.EnumWindows(each, 0)
    return out


def controls(hwnd: int) -> list[Control]:
    """Buttons and check boxes of a window, on screen: name, class, rectangle."""
    try:
        return _com(_controls, hwnd)
    except Exception:
        return []


def _controls(hwnd: int) -> list[Control]:
    try:
        from pywinauto.uia_defines import IUIA
        iuia = IUIA().iuia
        root = iuia.ElementFromHandle(int(hwnd))
        kinds = iuia.CreateOrConditionFromArray(
            [iuia.CreatePropertyCondition(30003, t) for t in (50000, 50002, 50031)])
        found = root.FindAll(4, kinds)
    except Exception:
        return []
    out = []
    for i in range(min(found.Length, 400)):
        el = found.GetElement(i)
        try:
            if el.CurrentIsOffscreen:
                continue
            r = el.CurrentBoundingRectangle
            if r.right - r.left < 4 or r.bottom - r.top < 4:
                continue
            out.append(Control(str(el.CurrentName or ""), str(el.CurrentClassName or ""),
                               (r.left, r.top, r.right, r.bottom)))
        except Exception:
            continue
    return out


def windows() -> list[CallWindow]:
    out = []
    for app, h, t in app_windows():
        cs = controls(h)
        if app == "viber" and t == "ViberPC" and not cs:
            cs = _viber_call_buttons(h)
        out.append(CallWindow(app, h, t, cs))
    return out


def _viber_call_buttons(hwnd: int) -> list[Control]:
    """Viber's call window ("ViberPC", a web view) shows UI Automation nothing
    at all (seen live, 2026-10-21): an incoming call is a red and a green round
    button at the bottom, a call in progress a red one. They are found by
    colour in a capture of that window only — kept in memory, sent nowhere —
    and named like the controls of the other apps."""
    try:
        import ctypes
        from ctypes import wintypes
        import mss
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        W, H = r.right - r.left, r.bottom - r.top
        if W < 100 or H < 150:
            return []
        top = int(H * 0.55)
        with mss.MSS() as sct:
            g = sct.grab({"left": r.left, "top": r.top + top, "width": W, "height": H - top})
        return round_buttons(g.rgb, g.width, (r.left, r.top + top))
    except Exception:
        return []


def round_buttons(px: bytes, width: int, origin: tuple[int, int]) -> list[Control]:
    """The red / green call buttons in an RGB capture whose top-left pixel is
    at `origin` on screen."""
    red, green = [], []
    for i in range(0, len(px) - 2, 3 * 2):               # every second pixel is plenty
        R, G, B = px[i], px[i + 1], px[i + 2]
        if R > 200 and G < 100 and B < 100:
            red.append(i // 3)
        elif G > 150 and R < 130 and B < 130:
            green.append(i // 3)

    def button(idx: list[int], name: str) -> list[Control]:
        if len(idx) < 300:                    # a button is ~1,500 px, ~750 of them sampled
            return []
        xs, ys = [k % width for k in idx], [k // width for k in idx]
        cx, cy = sum(xs) // len(xs) + origin[0], sum(ys) // len(ys) + origin[1]
        return [Control(name, "ViberPC", (cx - 20, cy - 20, cx + 20, cy + 20))]

    if len(green) >= 300:
        return button(green, "Accept") + button(red, "Decline")
    return button(red, "End call")


def describe_window(w: CallWindow) -> str:
    """What a call window offers, for tuning the lists above (no message text:
    buttons only)."""
    parts = [f"{c.name or '-'}<{_cls_words(c.cls) or c.cls[:24]}>" for c in w.controls[:30]]
    return f"{LABEL.get(w.app, w.app)} window '{w.title[:40]}': " + ", ".join(parts)


# ── what is going on ────────────────────────────────────────────────────────

@dataclass
class Ring:
    app: str
    hwnd: int
    who: str                       # '' when the window does not say
    accept: Control
    decline: Control


def _who(w: CallWindow) -> str:
    t = re.sub(r"\s*[-–|]\s*(Telegram|Viber|Rakuten Viber|Slack|Phone Link)\s*$", "", w.title or "").strip()
    return "" if t.lower() in ("", "telegram", "viber", "rakuten viber", "viberpc", "slack",
                               "phone link") else t


def ringing(wins: Optional[list[CallWindow]] = None) -> list[Ring]:
    out = []
    for w in windows() if wins is None else wins:
        acc, dec = w.find(_ACCEPT), w.find(_DECLINE)
        if len(acc) >= 1 and len(dec) >= 1:
            out.append(Ring(w.app, w.hwnd, _who(w), acc[0], dec[0]))
    return out


def end_controls(app: str, wins: Optional[list[CallWindow]] = None) -> list[tuple[CallWindow, Control]]:
    wins = windows() if wins is None else wins
    found = [(w, c) for w in wins if w.app == app for c in w.find(_END)]
    if not found and app == "viber":
        main = next((w for w in wins if w.app == "viber" and w.title != "ViberPC"), None)
        c = _viber_bar_hangup(main.hwnd) if main else None
        if c is not None:
            found = [(main, c)]
    return found


def _viber_bar_hangup(hwnd: int) -> Optional[Control]:
    try:
        return _com(_viber_bar_hangup_on_com, hwnd)
    except Exception:
        return None


def _viber_bar_hangup_on_com(hwnd: int) -> Optional[Control]:
    """A Viber call in progress plays inside the main window, a web view with
    nothing for UI Automation; under it sits a bar of round buttons (seen live,
    2026-10-21): microphone and camera as RED pills when off, …, and hang up
    as a WHITE circle with a red handset, rightmost. Red alone would hit the
    microphone toggle, so the white circle is what is looked for."""
    try:
        import ctypes
        from ctypes import wintypes
        import mss
        import pyautogui
        from pywinauto.uia_defines import IUIA
        iuia = IUIA().iuia
        doc = iuia.ElementFromHandle(int(hwnd)).FindFirst(4, iuia.CreatePropertyCondition(30003, 50030))
        if doc is None:
            return None
        d = doc.CurrentBoundingRectangle
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        if d.right - d.left < 200 or r.bottom - d.bottom < 30:
            return None
        pyautogui.moveTo((d.left + d.right) // 2, d.bottom - 40)       # the bar shows on hover
        time.sleep(0.6)
        top = d.bottom - 10
        with mss.MSS() as sct:
            g = sct.grab({"left": d.left, "top": top, "width": d.right - d.left,
                          "height": r.bottom - top})
        return hangup_in_bar(g.rgb, g.width, (d.left, top))
    except Exception:
        return None


def hangup_in_bar(px: bytes, width: int, origin: tuple[int, int]) -> Optional[Control]:
    """The rightmost solid white disc with red inside, in an RGB capture."""
    height = len(px) // 3 // width
    cols: dict[int, list[tuple[int, int]]] = {}
    for y in range(0, height, 2):
        row = y * width
        for x in range(0, width, 2):
            i = (row + x) * 3
            if px[i] > 230 and px[i + 1] > 230 and px[i + 2] > 230:
                cols.setdefault(x // 48, []).append((x, y))
    best = None
    for k in sorted(cols):
        pts = cols[k] + cols.get(k + 1, [])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        l, t, rr, b = min(xs), min(ys), max(xs), max(ys)
        w, h = rr - l + 2, b - t + 2
        if not (24 <= w <= 64 and 24 <= h <= 64):
            continue
        if len(pts) * 4 / (w * h) < 0.45:          # a filled disc, not a line of text
            continue
        red = 0
        for y in range(t, b, 2):
            for x in range(l, rr, 2):
                i = (y * width + x) * 3
                if px[i] > 180 and px[i + 1] < 90 and px[i + 2] < 90:
                    red += 1
        if red >= 8:
            cx, cy = (l + rr) // 2 + origin[0], (t + b) // 2 + origin[1]
            best = Control("End call", "ViberCallBar", (cx - 16, cy - 16, cx + 16, cy + 16))
    return best


def active(wins: Optional[list[CallWindow]] = None) -> list[str]:
    """Apps in a call now: they hold the mic, or show a hang-up control."""
    got = set(mic_in_use())
    for w in windows() if wins is None else wins:
        if w.find(_END) and not w.find(_ACCEPT):
            got.add(w.app)
    return [a for a in APPS if a in got]


# ── the watcher main.py polls ───────────────────────────────────────────────

class Watch:
    """check() → events since the last check: ('ring', Ring), ('call', app),
    ('ended', app). Called about once a second off the event loop."""

    def __init__(self) -> None:
        self._rings: dict[tuple[str, int], Ring] = {}
        self._calls: set[str] = set()
        self._lock = threading.Lock()
        self.described: set[int] = set()

    def check(self) -> list[tuple[str, object]]:
        wins = windows()
        rings = {(r.app, r.hwnd): r for r in ringing(wins)}
        calls = set(active(wins))
        events: list[tuple[str, object]] = []
        with self._lock:
            for k, r in rings.items():
                if k not in self._rings:
                    events.append(("ring", r))
            for a in calls - self._calls:
                events.append(("call", a))
            for a in self._calls - calls:
                events.append(("ended", a))
            self._rings, self._calls = rings, calls
        for k, r in rings.items():
            if r.hwnd not in self.described:
                self.described.add(r.hwnd)
                w = next((w for w in wins if w.hwnd == r.hwnd), None)
                if w is not None:
                    print(f"[Calls] {describe_window(w)}")
        return events

    def in_call(self) -> bool:
        with self._lock:
            return bool(self._calls)


def wait_for(cond, seconds: float, step: float = 0.5) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if cond():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)
