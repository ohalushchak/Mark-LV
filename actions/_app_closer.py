"""
Closing an application by name — and saying truthfully whether it closed.

`computer_settings` → `close_app` used to ignore the app's name entirely and
press Alt+F4 (Cmd+Q on macOS) in whatever window had focus, then report
"Done". Asked once to close Task Manager, it sent three Alt+F4s to the focused
window — which could have been the user's editor — while Task Manager stayed
open: it runs elevated, and Windows drops synthetic input sent to an elevated
window from a normal process.

What happens now:

* The target is found by name: the process (through open_app's alias table,
  so "task manager" means taskmgr.exe even on a Ukrainian Windows whose window
  title is "Диспетчер завдань"), or the window title.
* It is asked to close the way its own ✕ button would (WM_CLOSE / "quit" /
  `wmctrl -c`). Nothing is force-killed: that is how unsaved work is lost.
* The result is checked. If it is still there, the reply says why as far as
  it can be told — it needs administrator rights, or it is asking whether to
  save — instead of claiming success.
* This assistant never closes itself this way, nor the Windows shell.

Everything returns a short sentence for the model to speak. Only the
Windows window enumeration needs ctypes; the matching is plain Python, so it
can be tested anywhere.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass, field

_OS = platform.system()

# How long an app gets to close after being asked, before we look again.
_WAIT_S = 3.0

# Windows that belong to the shell itself, never an "app" to close.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


@dataclass
class Win:
    hwnd: int
    pid: int
    exe: str          # process name without ".exe", lower-case
    title: str
    path: str = ""    # full executable path when readable (for reopening)


@dataclass
class Outcome:
    closed: list[Win] = field(default_factory=list)
    left: list[Win] = field(default_factory=list)


# ── Matching (pure) ──────────────────────────────────────────────────────────

def _stem(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"\.(exe|app)$", "", n)
    return n


def _clean_query(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"^(the|app|application)\s+", "", q)
    q = re.sub(r"\s+(app|application|window|program)$", "", q)
    return q.strip()


def alias_exe(query: str) -> str:
    """open_app's name for an app on this OS: 'task manager' -> 'taskmgr'."""
    try:
        from actions.open_app import _APP_ALIASES
    except Exception:
        return ""
    entry = _APP_ALIASES.get(_clean_query(query))
    if not entry:
        return ""
    return _stem(entry.get(_OS, ""))


def match_windows(windows: list[Win], query: str) -> list[Win]:
    """Windows of the app the user named, best kind of match first:
    the alias' process, then a process named like the query, then a window
    title containing it. An empty list means nothing plausible."""
    q = _clean_query(query)
    if not q:
        return []
    alias = alias_exe(q)
    q_exe = _stem(q).replace(" ", "")
    for want in (alias, q_exe):
        if want:
            hit = [w for w in windows if w.exe == want]
            if hit:
                return hit
    if len(q_exe) >= 3:
        hit = [w for w in windows if q_exe in w.exe]
        if hit:
            return hit
    words = q.split()
    return [w for w in windows if all(x in w.title.lower() for x in words)]


def friendly(query: str) -> str:
    """The app as the user named it: 'task manager' -> 'Task Manager',
    'Outlook' stays 'Outlook'. Process names are a poor stand-in — the new
    Outlook runs as olk.exe, Task Manager as taskmgr.exe."""
    q = _clean_query(query)
    if not q:
        return ""
    raw = (query or "").strip()
    return q.title() if raw == raw.lower() else raw


def describe(wins: list[Win], name: str = "") -> str:
    """'Notepad', '3 Chrome windows' — for the spoken reply. `name` is how the
    user called it; without one, the process name is tidied up."""
    if not wins:
        return "nothing"
    nice = name
    if not nice:
        raw = wins[0].exe or wins[0].title
        nice = raw.replace("_", " ").title() if raw.islower() else raw
    return nice if len(wins) == 1 else f"{len(wins)} {nice} windows"


# ── Windows ──────────────────────────────────────────────────────────────────

def _win_list() -> list[Win]:
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    dwm = getattr(ctypes.windll, "dwmapi", None)
    out: list[Win] = []
    names: dict[int, tuple[str, str]] = {}

    def proc(pid: int) -> tuple[str, str]:
        if pid not in names:
            try:
                p = psutil.Process(pid)
                exe = _stem(p.name())
                try:
                    path = p.exe()
                except Exception:
                    path = ""
                names[pid] = (exe, path)
            except Exception:
                names[pid] = ("", "")
        return names[pid]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value in _SHELL_CLASSES:
                return True
            if dwm is not None:
                # Suspended Store apps keep invisible "cloaked" windows.
                cloaked = ctypes.c_int(0)
                dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                          ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe, path = proc(pid.value)
            out.append(Win(int(hwnd), int(pid.value), exe, buf.value, path))
        except Exception:
            pass
        return True

    user32.EnumWindows(each, 0)
    return out


def _win_foreground() -> int:
    import ctypes
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def _win_alive(hwnd: int) -> bool:
    import ctypes
    u = ctypes.windll.user32
    return bool(u.IsWindow(hwnd) and u.IsWindowVisible(hwnd))


def _win_close(hwnd: int) -> None:
    import ctypes
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE


def _win_is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _win_elevated(pid: int) -> bool | None:
    """True if the process runs elevated; None if Windows will not say —
    which for a normal process asking about an elevated one is itself the
    usual answer."""
    try:
        import ctypes
        from ctypes import wintypes
        k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
        h = k32.OpenProcess(0x1000, False, pid)               # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            tok = wintypes.HANDLE()
            if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):   # TOKEN_QUERY
                return None
            try:
                elev, size = wintypes.DWORD(), wintypes.DWORD()
                if not adv.GetTokenInformation(tok, 20, ctypes.byref(elev),   # TokenElevation
                                               ctypes.sizeof(elev), ctypes.byref(size)):
                    return None
                return bool(elev.value)
            finally:
                k32.CloseHandle(tok)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def close_windows(targets: list[Win], alive=None, close=None,
                  wait_s: float = _WAIT_S, sleep=time.sleep) -> Outcome:
    """Ask every target window to close, then see which are still there."""
    alive = alive or _win_alive
    close = close or _win_close
    for w in targets:
        try:
            close(w.hwnd)
        except Exception:
            pass
    deadline = time.monotonic() + wait_s
    left = list(targets)
    while left and time.monotonic() < deadline:
        sleep(0.15)
        left = [w for w in left if alive(w.hwnd)]
    return Outcome(closed=[w for w in targets if w not in left], left=left)


def _why_left(left: list[Win], windows_now: list[Win],
              is_admin=None, elevated=None, name: str = "") -> str:
    """Why the windows in `left` are still open, as far as can be told."""
    is_admin = _win_is_admin if is_admin is None else is_admin
    elevated = _win_elevated if elevated is None else elevated
    # An elevated window ignores a close request from a normal process
    # (UIPI). Measured on one machine: same-user elevated processes report
    # True; SYSTEM ones do not answer at all (None) — treated the same.
    if not is_admin() and any(elevated(w.pid) is not False for w in left):
        return (f"{describe(left, name)} runs with administrator rights, and Windows "
                "does not let me close it from a normal account. Close it "
                "yourself, or start me as administrator.")
    pids = {w.pid for w in left}
    extra = [w for w in windows_now if w.pid in pids and w.hwnd not in {x.hwnd for x in left}]
    if extra:
        return (f"{describe(left, name)} is still open and showing \"{extra[0].title}\" — "
                "probably asking whether to save. Answer it on screen.")
    return (f"{describe(left, name)} did not close — it may be asking whether to save "
            "changes. Check the screen.")


def _close_windows_os(query: str) -> tuple[str, list[Win]]:
    me = os.getpid()
    windows = _win_list()
    if query:
        targets = match_windows(windows, query)
        if not targets:
            return (f"I could not find an open window for '{query}'. "
                    "It may already be closed."), []
    else:
        fg = _win_foreground()
        targets = [w for w in windows if w.hwnd == fg]
        if not targets:
            return "Nothing is in front to close. Tell me which app.", []
    if all(w.pid == me for w in targets):
        return ("That is me. Say 'shut down' if you want me to close."), []
    targets = [w for w in targets if w.pid != me]

    name = friendly(query)
    result = close_windows(targets)
    if not result.left:
        return f"Closed {describe(result.closed, name)}.", result.closed
    msg = _why_left(result.left, _win_list(), name=name)
    if result.closed:
        msg = f"Closed {describe(result.closed, name)}, but " + msg[0].lower() + msg[1:]
    return msg, result.closed


# ── macOS ────────────────────────────────────────────────────────────────────

def _osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return (r.stdout or "").strip()


def _close_mac(query: str) -> tuple[str, list[Win]]:
    if query:
        try:
            from actions.open_app import _APP_ALIASES
            app = _APP_ALIASES.get(_clean_query(query), {}).get("Darwin", "") or query
        except Exception:
            app = query
    else:
        app = _osa('tell application "System Events" to name of first application '
                   'process whose frontmost is true')
        if not app:
            return "Nothing is in front to close. Tell me which app.", []
    if app.lower().startswith("python") or app.lower() in ("jarvis", "mark"):
        return "That is me. Say 'shut down' if you want me to close.", []
    app_q = app.replace('"', "")
    if _osa(f'application "{app_q}" is running') != "true":
        return f"{app} is not running.", []
    _osa(f'tell application "{app_q}" to quit')
    deadline = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if _osa(f'application "{app_q}" is running') != "true":
            return f"Closed {app}.", []
    return (f"{app} did not quit — it may be asking whether to save changes. "
            "Check the screen."), []


# ── Linux ────────────────────────────────────────────────────────────────────

def _close_linux(query: str) -> tuple[str, list[Win]]:
    import shutil
    if not shutil.which("wmctrl"):
        return ("Closing an app by name needs wmctrl on Linux "
                "(sudo apt install wmctrl)."), []
    if not query:
        return "Tell me which app to close.", []

    def titles() -> list[str]:
        r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
        return [ln.split(None, 3)[3] for ln in r.stdout.splitlines() if len(ln.split(None, 3)) == 4]

    words = _clean_query(query).split()
    hits = [t for t in titles() if all(w in t.lower() for w in words)]
    if not hits:
        return f"I could not find an open window for '{query}'.", []
    for t in hits:
        subprocess.run(["wmctrl", "-c", t], capture_output=True, timeout=5)
    deadline = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if not any(t in titles() for t in hits):
            return f"Closed {hits[0] if len(hits) == 1 else f'{len(hits)} windows'}.", []
    return (f"{hits[0]} did not close — it may be asking whether to save changes. "
            "Check the screen."), []


# ── Entry point ──────────────────────────────────────────────────────────────

def close_app(query: str | None) -> tuple[str, list[Win]]:
    """Close the named app (or the one in front when no name is given).
    Returns (sentence to speak, windows that did close)."""
    q = (query or "").strip()
    try:
        if _OS == "Windows":
            return _close_windows_os(q)
        if _OS == "Darwin":
            return _close_mac(q)
        return _close_linux(q)
    except Exception as e:
        return f"Could not close {q or 'the app'}: {e}", []


def reopen(wins: list[Win]) -> str:
    """Undo for close_app: start what was closed again. Documents and tabs it
    had open are not restored — only the application."""
    paths = sorted({w.path for w in wins if w.path})
    if not paths:
        return "I cannot reopen it — I do not know where it is installed."
    for p in paths:
        try:
            subprocess.Popen([p])
        except Exception as e:
            return f"Could not reopen {p}: {e}"
    return f"Reopened {describe(wins)}."
