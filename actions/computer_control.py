#computer_control.py
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import threading
import time
import random
from dataclasses import dataclass
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

if _PYAUTOGUI and platform.system() == "Windows":
    # pyautogui looks letters up with VkKeyScan in the keyboard layout active
    # when it was imported; under a Ukrainian layout "a" or "v" is not on the
    # keyboard and Ctrl+A / Ctrl+V go out as nothing. Letter and digit key
    # codes are the same in every layout, so pin them.
    try:
        from pyautogui import _pyautogui_win as _pw
        for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
            _pw.keyboardMapping[_c] = 0x41 + _i
        for _i, _c in enumerate("0123456789"):
            _pw.keyboardMapping[_c] = 0x30 + _i
    except Exception:
        pass

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _paste_text(text: str) -> None:
    """Put text in through the clipboard, then give the clipboard back."""
    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("command" if _get_os() == "mac" else "ctrl", "v")
    time.sleep(0.4)          # some apps read the clipboard after the key, not on it
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    if (platform.system() == "Windows" or not text.isascii()) and _PYPERCLIP:
        # Through the clipboard, whatever the text: pyautogui.typewrite
        # presses the KEYS of the US layout, so with a Ukrainian layout active
        # "John Smith" arrived as "Ощрт Іьшер" (seen live), and Cyrillic
        # was dropped altogether. Unicode SendInput avoids the layout but
        # Windows 11 Notepad dropped and repeated its characters (measured);
        # a paste lands whole, and a chat box takes its line breaks as line
        # breaks, not as Enter.
        _paste_text(text)
    else:
        pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if (len(text) > 20 or not text.isascii() or platform.system() == "Windows") and _PYPERCLIP:
        _paste_text(text)
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.dragTo(x2, y2, duration=duration, button="left")
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            return _focus_window_win(title)
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                return f"Focused window: {title}"
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"

def _find_window_win(title: str):
    """The window the user means: by app (open_app's aliases, the process),
    then by a title fragment. The one in front wins a tie, then the largest."""
    from actions._app_closer import match_windows
    from core import desktop_state
    wins = [w for w in desktop_state.windows() if not desktop_state.is_me(w.pid)]
    hits = match_windows(wins, title)
    if not hits:
        t = title.casefold().strip()
        hits = [w for w in wins if t and t in w.title.casefold()]
    if not hits:
        return None
    fg = desktop_state.foreground() or {}
    for w in hits:
        if w.hwnd == fg.get("hwnd"):
            return w

    def area(w):
        r = (desktop_state.window(w.hwnd) or {}).get("rect") or (0, 0, 0, 0)
        return (r[2] - r[0]) * (r[3] - r[1])
    return max(hits, key=area)


def _bring_forward(hwnd: int) -> None:
    """SetForegroundWindow, past Windows' rule that only the process which
    received the last input may change the foreground. The usual trick is a
    tap of Alt — and a lone Alt opens the menu bar: seen live in Notepad,
    where everything typed next went into the menu (and before the switch,
    the Alt lands in whatever window was in front). An injected zero-length
    mouse move satisfies the rule and does nothing anywhere."""
    import ctypes
    u = ctypes.windll.user32
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)                         # SW_RESTORE
    u.mouse_event(0x0001, 0, 0, 0, 0)                 # MOUSEEVENTF_MOVE, by (0, 0)
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)


def _focus_window_win(title: str) -> str:
    """Bring a window to the front. PowerShell's AppActivate matched only the
    start of a title, failed silently, and was reported as success."""
    w = _find_window_win(title)
    if w is None:
        return f"Could not find an open window matching '{title}'."
    _bring_forward(w.hwnd)
    time.sleep(0.25)
    return f"Focused window: {w.title}"


_FIND_IMG_MAX = (1600, 1000)   # what the vision model sees of the whole screen
_ZOOM_MIN = (640, 400)         # the native-resolution crop of the second look
_FIND_MAX_AREA = 0.25          # a "match" covering a quarter of the image is no match

_FIND_PROMPT = (
    "This is {what}. Find the user-interface element described as: \"{desc}\".\n"
    "Answer with JSON only: {{\"matches\": [{{\"box_2d\": [ymin, xmin, ymax, xmax], "
    "\"label\": \"the exact text shown on it, or a two-word name if it has none\"}}]}}, "
    "coordinates normalised to 0-1000 of this image. List EVERY element that fits the "
    "description, so an ambiguous one can be told apart. If it is not visible, answer "
    "{{\"matches\": []}}. Never guess."
)

# Words that say what kind of thing it is, not which one ("the Send button").
_ROLE_WORDS = {"the", "a", "an", "button", "menu", "link", "tab", "icon", "field", "item",
               "box", "checkbox", "option", "entry", "кнопку", "кнопка", "меню",
               "посилання", "вкладка", "вкладку", "поле"}


_KIND = {50000: "button", 50011: "menu item", 50005: "link", 50019: "tab", 50007: "list item",
         50002: "checkbox", 50013: "radio button", 50004: "text field", 50003: "combo box",
         50024: "tree item", 50031: "split button"}


@dataclass
class Found:
    x: int
    y: int
    label: str
    where: str          # which monitor, or "by its name"
    how: str            # "ui automation" | "vision"


def _core_name(desc: str) -> str:
    words = [w for w in re.split(r"\s+", desc.strip().strip("'\"«»").casefold())
             if w.strip("'\"«»") not in _ROLE_WORDS]
    return " ".join(w.strip("'\"«»") for w in words)


def _uia_find(desc: str):
    """The element by its accessible name, in the window in front or the
    taskbar: exact, instant, and immune to scaling — when the app exposes its
    controls (bfrpaulondev/Mark-BP's UIA-first route). (Found | None, why);
    None with no reason means "ask vision"."""
    from core import desktop_state as ds
    if platform.system() != "Windows":
        return None, ""
    full, core = desc.strip().strip("'\"«»").casefold(), _core_name(desc)
    if not core:
        return None, ""
    fg = ds.foreground() or {}
    pool = []
    for hwnd in (fg.get("hwnd"), ds.taskbar()):
        if hwnd and not ds.is_me((ds.window(hwnd) or {}).get("pid", 0)):
            pool += [c for c in ds.controls(hwnd) if c["name"].strip()]
    exact = [c for c in pool if c["name"].strip().casefold() in (full, core)]
    # A whole-word match only: "Пуск" (Start) was found inside "запускатися".
    word = re.compile(rf"(?<!\w){re.escape(core)}(?!\w)")
    hits = exact or ([c for c in pool if len(core) >= 3 and word.search(c["name"].casefold())])
    if not hits:
        return None, ""
    if len({c["rect"] for c in hits}) > 1:
        names = "; ".join(f"'{c['name'][:30]}' ({_KIND.get(c['type'], 'control')} at "
                          f"{(c['rect'][0] + c['rect'][2]) // 2}, {(c['rect'][1] + c['rect'][3]) // 2})"
                          for c in hits[:5])
        return None, (f"{len(hits)} controls are named like '{desc}': {names}. Click one by its "
                      "coordinates, or describe it with where it is (e.g. 'in the sidebar') "
                      "so it is looked for on screen")
    c = hits[0]
    if not c["enabled"]:
        return None, f"'{c['name']}' is there but disabled"
    l, t, r, b = c["rect"]
    return Found((l + r) // 2, (t + b) // 2, c["name"], "by its name", "ui automation"), ""


_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "at", "and", "or", "my", "this", "that"}


def _fits(desc: str, label: str) -> bool:
    """Does the label the vision model read fit the description? Most of the
    description's words must be there, compared by their first five letters
    ("updates" ~ "update"). Seen live: asked for "Check for updates button",
    vision returned 'Windows Update' — the button was not on that page — and
    it was clicked. A label-less match (an icon) is taken on trust."""
    words = [w[:5] for w in re.findall(r"\w+", _core_name(desc)) if w not in _STOP and len(w) > 1]
    have = {w[:5] for w in re.findall(r"\w+", (label or "").casefold())}
    if not words or not have:
        return True
    cyr = re.compile("[Ѐ-ӿ]")
    if bool(cyr.search(desc)) != bool(cyr.search(label)):
        return True                  # "Night light" vs "Нічне світло": cannot be compared
    hit = sum(1 for w in words if w in have)
    return hit * 3 >= len(words) * 2


def _ask_boxes(img, what: str, desc: str) -> list | None:
    """Gemini's boxes for `desc` in a PIL image: [(cx, cy, label)] in 0-1000,
    or None when no model answered."""
    from google.genai import types as gtypes
    from core import gemini
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    reply = gemini.as_json(
        [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
         _FIND_PROMPT.format(what=what, desc=desc.replace('"', "'"))],
        tier=gemini.VISION, timeout_ms=15_000, default=None)
    if not isinstance(reply, dict):
        return None
    out = []
    for m in reply.get("matches") or []:
        try:
            y0, x0, y1, x1 = (float(v) for v in m.get("box_2d"))
        except Exception:
            continue
        if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
            continue
        if (x1 - x0) * (y1 - y0) > _FIND_MAX_AREA * 1_000_000:
            continue
        out.append(((x0 + x1) / 2, (y0 + y1) / 2, str(m.get("label") or "").strip()))
    return out


def _vision_find(desc: str, monitor: str | None):
    """Two looks: the whole screen (scaled to 1600 px) for where, then a
    native-resolution crop around that spot for exactly where. Measured on a
    3440×1440 screen: one look put the Start button 38 px off — outside the
    45-px button — or on the wrong taskbar icon entirely; the crop corrects
    that, and a second look that cannot find it there refuses the click."""
    try:
        import PIL.Image
        from actions.screen_processor import capture_monitor
    except Exception as e:
        return None, f"screen search is unavailable ({e})"
    png, geom = capture_monitor(monitor)
    full = PIL.Image.open(io.BytesIO(png)).convert("RGB")
    W, H = full.size
    small = full.copy()
    small.thumbnail(_FIND_IMG_MAX, PIL.Image.LANCZOS)
    boxes = _ask_boxes(small, "a screenshot of one computer screen", desc)
    if boxes is None:
        return None, "the vision model did not answer"
    if not boxes:
        return None, f"'{desc}' is not visible on {geom['label']}"
    if len(boxes) > 1:
        exact = [b for b in boxes if b[2].casefold() == _core_name(desc)]
        if len(exact) != 1:
            names = ", ".join(f"'{b[2]}'" for b in boxes[:5])
            return None, (f"{len(boxes)} elements on {geom['label']} fit '{desc}': {names}. "
                          "Describe the one you mean more exactly")
        boxes = exact
    cx, cy, label = boxes[0]
    if not _fits(desc, label):
        return None, (f"the closest thing on {geom['label']} is '{label}', which is not '{desc}' — "
                      "it is probably not on screen")
    px, py = cx / 1000 * W, cy / 1000 * H

    # Second look, at native resolution around the first answer.
    zw, zh = min(W, max(_ZOOM_MIN[0], W // 5)), min(H, max(_ZOOM_MIN[1], H // 5))
    left = int(min(max(0, px - zw / 2), W - zw))
    top = int(min(max(0, py - zh / 2), H - zh))
    crop = full.crop((left, top, left + zw, top + zh))
    fine = _ask_boxes(crop, "a close-up crop of part of a computer screen", desc)
    if fine:
        nearest = min(fine, key=lambda b: (b[0] - 500) ** 2 + (b[1] - 500) ** 2)
        px = left + nearest[0] / 1000 * zw
        py = top + nearest[1] / 1000 * zh
        label = nearest[2] or label
        if not _fits(desc, label):
            # The close look's label counts too: seen live, "Recycle Bin icon"
            # came back as a checkbox labelled 'checkbox' and was clicked.
            return None, (f"a closer look found '{label}' there, which is not '{desc}' — "
                          "it is probably not on screen")
    elif fine is not None:
        return None, (f"I thought '{desc}' was near ({int(px)}, {int(py)}) on {geom['label']}, "
                      "but a closer look did not find it there")
    x = geom["left"] + round(px * geom["width"] / W)
    y = geom["top"] + round(py * geom["height"] / H)
    return Found(x, y, label or desc, f"on {geom['label']}", "vision"), ""


def _screen_find(description: str, monitor: str | None = None):
    """Find an element on screen; (Found, "") or (None, why not).

    Rewritten from a call that sent the primary monitor's full-size PNG to
    the Live model and parsed "x,y" out of its spoken reply: it never saw the
    other monitors, mixed image pixels with screen pixels, and a reply in
    words failed the regex. Now: by name through UI Automation first, then
    vision on the chosen screen with a second, closer look, mapped through
    that screen's rectangle; more than one match is refused instead of
    clicking the first (JishnuDevTech re-located and thresholded; rilzisheree
    refused unproven coordinates)."""
    desc = (description or "").strip()
    if not desc:
        return None, "no description of what to find"
    if not monitor:
        found, why = _uia_find(desc)
        if found is not None or why:
            return found, why
    return _vision_find(desc, monitor)


def _place_pointer(x: int, y: int) -> str:
    """Move the pointer and prove it arrived; "" or why not. A point outside
    every screen, or a pointer that lands elsewhere, means the image and the
    mouse disagree about coordinates (DPI scaling, a monitor that moved), and
    a click there would hit something else (rilzisheree's coordinate proof)."""
    from core import desktop_state as ds
    box = ds.virtual_desktop()
    if box and not (box[0] <= x < box[2] and box[1] <= y < box[3]):
        return f"({x}, {y}) is outside every screen"
    pyautogui.moveTo(x, y, duration=0.15)
    cur = ds.cursor()
    if cur is None:
        return ""
    if abs(cur[0] - x) > 2 or abs(cur[1] - y) > 2:
        return f"the pointer went to {cur} instead of ({x}, {y}) — screen scaling does not match"
    return ""


# ── Sending is asked first (core/send_gate.py) ──────────────────────────────
#
# Enter in a chat's message box sends a message from the user's account. Seen
# live: Enter in Slack's #qa box sent "John Smith" to the channel, and
# another Enter after a stray click opened and wrote to a different channel.

_CHAT_APPS = {"slack", "teams", "ms-teams", "msteams", "whatsapp", "whatsapp.root", "telegram",
              "discord", "signal", "viber", "skype", "element", "messenger"}
_COMPOSE_RE = re.compile(r"(message|повідомлен|сообщен|написати|напишіть|reply|"
                         r"відповісти|chat|чат|comment|коментар|compose)", re.IGNORECASE)
_SEND_KEYS = {"enter", "return", "ctrl+enter", "ctrl+return", "control+enter"}
# A call button dials as surely as Send sends (the user's rule, 2026-10-21).
_SEND_LABEL_RE = re.compile(r"^\s*(send|надіслати|відправити|отправить|submit|"
                            r"(start |voice |video |audio )?call|"
                            r"подзвонити|дзвінок|відеодзвінок|позвонить|звонок|видеозвонок)\b",
                            re.IGNORECASE)
_CALL_LABEL_RE = re.compile(r"(\bcall\b|дзвін|подзвон|звон)", re.IGNORECASE)


def _compose_target():
    """The chat message box that has the focus, when it holds text: a dict
    with the window, app, field and text — or None. A box whose text cannot
    be read counts as holding some (text None): better one question too many."""
    if platform.system() != "Windows":
        return None
    from core import desktop_state as ds
    fg, f = ds.foreground(), ds.focused_control()
    if not fg or not f or ds.is_me(fg.get("pid", 0)):
        return None
    editable = f.control_type in ("Edit", "Document")
    if not ((fg.get("exe") in _CHAT_APPS and editable)
            or (editable and _COMPOSE_RE.search(f.name or ""))):
        return None
    text = None if f.value is None else f.value.strip()
    if text == "":
        return None
    return {"hwnd": fg["hwnd"], "app": fg.get("exe") or "the app", "field": f.name or f.control_type,
            "text": text, "focus": f}


def _what(c: dict) -> str:
    t = c["text"]
    said = f"'{t[:80]}{'…' if len(t) > 80 else ''}'" if t else "the message"
    return f"{said} from the box '{c['field'][:50]}' in {c['app']}"


def _refocus(hwnd: int) -> None:
    _bring_forward(hwnd)
    time.sleep(0.35)


def send_guard(params: dict, keys: str, do, tool: str | None = None, to: str = "") -> str | None:
    """None: press the key now. Otherwise the reply instead — the question,
    or why a confirmed send is refused. `do` presses the key; `tool` is the
    call the model repeats with confirmed=true; `to` names the recipient."""
    if str(keys).lower().replace(" ", "") not in _SEND_KEYS:
        return None
    c = _compose_target()
    if c is None:
        return None
    from core import send_gate
    key = ("keys", c["hwnd"], c["field"], c["text"])
    if params.get("confirmed") and send_gate.waiting(key):
        return send_gate.approve(key)

    def from_button() -> str:
        from core import desktop_state as ds
        _refocus(c["hwnd"])
        f = ds.focused_control()
        if f is None or not f.same_control(c["focus"]):
            return "Not sent: that message box no longer has the focus."
        do()
        time.sleep(0.4)
        f = ds.focused_control()
        return ("Sent." if f is not None and f.value is not None and not f.value.strip()
                else "Enter was pressed; I could not see the box empty.")
    return send_gate.ask(_what(c) + to, key, from_button,
                         tool or f"computer_control (action {params.get('action')!s}, the same key)")


def _screen_click(params: dict) -> str:
    desc = str(params.get("description", "")).strip()
    found, why = _screen_find(desc, params.get("monitor"))
    if found is None:
        return f"Could not click: {why}."
    if _SEND_LABEL_RE.search(found.label) or _SEND_LABEL_RE.search(desc):
        from core import desktop_state as ds
        from core import send_gate
        fg = ds.foreground() or {}
        key = ("click", fg.get("hwnd"), found.label, found.x, found.y)
        if params.get("confirmed") and send_gate.waiting(key):
            refused = send_gate.approve(key)
            if refused:
                return refused
        else:
            def from_button(x=found.x, y=found.y, hwnd=fg.get("hwnd")) -> str:
                if hwnd:
                    _refocus(hwnd)
                _click(x, y)
                return f"Clicked '{found.label}'."
            app = fg.get("exe") or "the app"
            if _CALL_LABEL_RE.search(found.label) or _CALL_LABEL_RE.search(desc):
                return send_gate.ask(f"'{found.label}' in {app} — it starts a call", key, from_button,
                                     "computer_control (screen_click, the same description)",
                                     verb="press")
            return send_gate.ask(f"by clicking '{found.label}' in {app}", key, from_button,
                                 "computer_control (screen_click, the same description)")
    problem = _place_pointer(found.x, found.y)
    if problem:
        return f"Could not click '{found.label}': {problem}."
    click = {"action": "click", "x": found.x, "y": found.y}
    before = _snapshot(click)
    button = "right" if str(params.get("button", "")).lower() == "right" else "left"
    clicks = 2 if params.get("double") else 1
    _click(found.x, found.y, button, clicks)
    _tl.click = (click, before)
    return f"Clicked '{found.label}' at ({found.x}, {found.y}) {found.where}."


# Names the model used live for actions that exist under another name.
_ALIASES = {"key": "press", "keypress": "press", "press_key": "press", "key_press": "press",
            "type_text": "type", "write": "type", "find": "screen_find"}


def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      random_data   — generate fake form data
      user_data     — pull real data from memory
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    action = _ALIASES.get(action, action)

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in _CLICKS and _xy(params) is None:
            # Seen live: click {description: 'John Smith'} with no point
            # clicked wherever the mouse happened to be — Slack's search box —
            # and the Enter after it opened a different channel.
            if str(params.get("description", "")).strip():
                return _screen_click(dict(params, action="screen_click",
                                          button="right" if action == "right_click" else "left",
                                          double=action == "double_click"))
            return ("Could not click: give x and y in screen pixels, or a description "
                    "of what to click (screen_click).")

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            held = send_guard(params, "+".join(keys), lambda: _hotkey(*keys))
            return held or _hotkey(*keys)

        if action == "press":
            key = params.get("key", "enter")
            held = send_guard(params, key, lambda: _press(key))
            return held or _press(key)

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            found, why = _screen_find(params.get("description", ""), params.get("monitor"))
            if found is None:
                return f"Could not find it: {why}."
            return (f"Found '{found.label}' at ({found.x}, {found.y}) {found.where}; "
                    "those are screen coordinates for click.")

        if action == "screen_click":
            return _screen_click(params)

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Did it take effect? (core/verify.py) ─────────────────────────────────────
#
# Before the action: where the pointer will be, what has focus, and a small
# grey grid of the screen there — taken twice, half a second apart, so cells
# that change on their own (a blinking caret is ~530 ms on, ~530 ms off; a
# spinner, a video) are masked out. After: the same again, polled briefly.
# A click is VERIFIED when the window in front, the focused control or the
# screen around it changed; "nothing changed" is UNVERIFIED, not FAILED —
# clicking an already selected tab legitimately changes nothing. A pointer
# that is not where it was sent is FAILED. Typing is VERIFIED only by reading
# the focused field's text back (bfrpaulondev/Mark-BP's input postconditions;
# the noise mask answers JishnuDevTech's "any pixel change is success").

_CLICKS = {"click", "left_click", "double_click", "right_click"}
_tl = threading.local()  # screen_click's own click and "before", for _verify
_KEYS = {"hotkey", "press"}
_TYPING = {"type", "smart_type", "paste"}
_MIN_CELLS = 3           # changed grid cells that count as "something happened"
_NOISE_GAP_S = 0.55      # between the two "before" samples (see above)


def _xy(params: dict):
    try:
        x, y = params.get("x"), params.get("y")
        return (int(x), int(y)) if x is not None and y is not None else None
    except (TypeError, ValueError):
        return None


def _region_now(kind: str, point):
    from core import desktop_state as ds
    if kind == "window":
        fg = ds.foreground()
        return fg["rect"] if fg else None
    if kind == "under" and point:
        w = ds.window_at(*point)
        return w["rect"] if w else ds.around(*point, 600, 400)
    return ds.around(*point) if point else None


def _visual_before(region) -> dict:
    from core import desktop_state as ds
    a = ds.sample(region)
    time.sleep(_NOISE_GAP_S)
    b = ds.sample(region)
    return {"region": region, "sample": b, "mask": ds.noise(a, b)}


def _snapshot(params: dict):
    from core import desktop_state as ds
    action = str(params.get("action", "")).lower().strip()
    action = _ALIASES.get(action, action)
    _tl.click = None
    if action in _CLICKS and _xy(params) is None:
        return None                          # found and checked inside the handler
    if action in _CLICKS:
        pt = _xy(params)
        if pt and _PYAUTOGUI:
            # Hover first, so a button's hover highlight is part of "before"
            # and cannot pass for the click's effect.
            pyautogui.moveTo(*pt, duration=0.1)
        pt = pt or ds.cursor()
        return {"fg": ds.foreground(), "focus": ds.focused_control(), "point": pt,
                **_visual_before(_region_now("near", pt))}
    if action in _KEYS:
        return {"fg": ds.foreground(), "focus": ds.focused_control(),
                **_visual_before(_region_now("window", None))}
    if action == "scroll":
        return _visual_before(_region_now("under", ds.cursor()))
    if action in _TYPING or action == "clear_field":
        return {"focus": ds.focused_control()}
    return None


def _something_changed(before: dict, region):
    """What visibly changed since `before`, as a phrase; None if nothing."""
    from core import desktop_state as ds
    fg0, fg1 = before.get("fg"), ds.foreground()
    if fg0 and fg1 and fg1["hwnd"] != fg0["hwnd"]:
        return f"the window in front is now '{fg1['title'][:60]}'"
    if "focus" in before:
        f0, f1 = before.get("focus"), ds.focused_control()
        if f1 is not None and not f1.same_control(f0):
            label = f" '{f1.name[:40]}'" if f1.name else ""
            return f"keyboard focus moved to the {f1.control_type or 'control'}{label}"
    n = ds.changed_cells(before.get("sample"), ds.sample(region), before.get("mask"))
    if n >= _MIN_CELLS:
        return "the screen there changed"
    return None


def _norm(t: str) -> str:
    """Text as compared with a read-back: whitespace folded, case folded, and
    emoji left out — Slack keeps 😊 as ":blush:" in its message box, so a
    draft with emoji never matched and was reported as unreadable."""
    import unicodedata
    t = re.sub(r":[a-z0-9_+\-]+:", " ", str(t or ""))
    t = "".join(" " if unicodedata.category(ch) in ("So", "Sk", "Cs") or ch == "\ufe0f" else ch
                for ch in t)
    return " ".join(t.split()).casefold()


def _verify_typing(action: str, params: dict, before):
    from core import desktop_state as ds
    from core import verify as v
    text = str(params.get("text", ""))
    f0 = (before or {}).get("focus")

    def look():
        f = ds.focused_control()
        if f is None:
            return v.unverified("I could not read which control has the keyboard focus")
        if f.password:
            return v.unverified("it went into a password field, which cannot be read back")
        if f.value is None:
            what = f.control_type or "control"
            return v.unverified(f"the focused {what} does not expose its text to read back")
        if action == "clear_field":
            return (v.verified("the focused field is empty") if not f.value.strip()
                    else v.failed("the focused field still contains text"))
        if _norm(text) and _norm(text) in _norm(f.value):
            return v.verified(f"the focused {f.control_type or 'field'} now contains the text")
        if text.strip() and not _norm(text) and f0 is not None and f0.same_control(f):
            # Only emoji: apps store them as codes (":slightly_smiling_face:"),
            # so the change is the evidence there is.
            return (v.verified("the field changed (emoji are stored as codes, so only the change shows)")
                    if f0.value != f.value else v.failed("the focused field did not change"))
        if f0 is not None and f0.same_control(f) and f0.value == f.value:
            return v.failed("the focused field did not change")
        return v.unverified("the focused field changed, but its text does not match what was typed")
    return v.poll(look)


def _verify(params: dict, result, before):
    from core import desktop_state as ds
    from core import verify as v
    action = str(params.get("action", "")).lower().strip()

    action = _ALIASES.get(action, action)
    if action == "screen_click" or (action in _CLICKS and _xy(params) is None):
        # The target was found inside the handler, so its "before" was taken
        # there, right before the click, and comes back on this thread.
        made = getattr(_tl, "click", None)
        _tl.click = None
        if not made:
            return None
        params, before = made
        action = "click"

    if action in _TYPING or action == "clear_field":
        return _verify_typing(action, params, before)

    if action in ("move", "drag"):
        try:
            want = ((int(params.get("x", 0)), int(params.get("y", 0))) if action == "move"
                    else (int(params.get("x2", 0)), int(params.get("y2", 0))))
        except (TypeError, ValueError):
            return None
        cur = ds.cursor()
        if cur is None:
            return v.unverified("I could not read the pointer position")
        if abs(cur[0] - want[0]) <= 3 and abs(cur[1] - want[1]) <= 3:
            return v.verified(f"the pointer is at {cur}")
        return v.failed(f"the pointer is at {cur}, not at {want}")

    if action in _CLICKS and before:
        want = _xy(params)
        cur = ds.cursor()
        if want and cur and (abs(cur[0] - want[0]) > 3 or abs(cur[1] - want[1]) > 3):
            return v.failed(f"the pointer ended at {cur}, not at {want}")
        region = before.get("region")

        def look():
            what = _something_changed(before, region)
            return (v.verified(f"after the click {what}") if what
                    else v.unverified("nothing visible changed after the click"))
        return v.poll(look)

    if action in _KEYS and before:
        region = before.get("region")
        keys = str(params.get("key") or params.get("keys") or "").lower().replace(" ", "")
        f0 = before.get("focus")

        def look():
            if keys in ("enter", "return") and f0 is not None and (f0.value or "").strip():
                # A message box that held text and is empty after Enter: the
                # text was submitted, which "something changed" cannot tell.
                f1 = ds.focused_control()
                if f1 is not None and f1.same_control(f0) and f1.value is not None:
                    if not f1.value.strip():
                        return v.verified(f"the text in '{f0.name[:40] or f0.control_type}' "
                                          "was submitted; the field is empty now")
                    if f1.value == f0.value:
                        return v.failed("Enter left the text in the field — nothing was submitted")
            what = _something_changed(before, region)
            return (v.verified(what[0].upper() + what[1:]) if what
                    else v.unverified("nothing visible changed after the keys"))
        return v.poll(look)

    if action == "scroll" and before:
        def look():
            n = ds.changed_cells(before.get("sample"), ds.sample(before.get("region")),
                                 before.get("mask"))
            if n >= _MIN_CELLS:
                return v.verified("the content under the pointer moved")
            return v.unverified("nothing moved — it may already be at the end")
        return v.poll(look)

    if action == "focus_window":
        title = str(params.get("title", "")).strip()
        if platform.system() != "Windows":
            return v.unverified("I can only check the window in front on Windows")

        def look():
            fg = ds.foreground()
            if fg is None:
                return v.unverified("I could not read the window in front")
            want = _find_window_win(title)
            if (want is not None and want.hwnd == fg["hwnd"]) \
                    or title.casefold() in fg["title"].casefold():
                return v.verified(f"'{fg['title'][:60]}' is in front")
            return v.failed(f"'{fg['title'][:60]}' is still in front")
        return v.poll(look)

    if action == "screenshot":
        m = re.search(r"Screenshot saved: (.+)$", str(result or ""))
        if m and Path(m.group(1).strip()).is_file():
            return v.verified(f"the file {m.group(1).strip()} exists")
        return v.failed("no screenshot file was written")

    return None


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": ("Direct computer control: type, click, hotkeys, scroll, move the mouse, focus a window, "
                    "find and click things on screen. To click something you can see or the user names "
                    "('the Send button', 'Пуск'), use screen_click with a description — it finds the element "
                    "by name or by looking, on the right monitor, and clicks it. x/y are real screen pixels, "
                    "never positions in a screenshot you were shown. Every action is checked afterwards and "
                    "the result ends with [VERIFIED], [UNVERIFIED] or [FAILED]: report that, not the attempt. "
                    "Enter in a chat's message box, or clicking Send, sends a message from the user's "
                    "account: it is asked first ([CONFIRM_SEND]). To open someone's chat or write to "
                    "someone in a messenger, use send_message (it opens the right conversation, types a "
                    "draft and checks both), not keystrokes here. One action at a time, blind: for "
                    "anything that takes several steps on the screen — a cleanup, a setting inside an "
                    "app, a form — use computer_use, which looks at the screen between steps. Never "
                    "Ctrl+A a list of options you have not seen. "
                    "Settings and named shortcuts (volume, minimise, new tab) belong to computer_settings; "
                    "opening apps to open_app."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"
            },
            "text": {
                "type": "STRING",
                "description": "Text to type or paste"
            },
            "x": {
                "type": "INTEGER",
                "description": "X in screen pixels (not screenshot pixels)"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y in screen pixels (not screenshot pixels)"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter'"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down | left | right"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount (default: 3)"
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait"
            },
            "title": {
                "type": "STRING",
                "description": "Window title for focus_window"
            },
            "description": {
                "type": "STRING",
                "description": ("screen_find/screen_click: the element's visible text exactly as shown on "
                                "screen, in the screen's language, plus what it is — e.g. 'Send button', "
                                "'Пуск', 'Edit menu'")
            },
            "type": {
                "type": "STRING",
                "description": "Data type for random_data"
            },
            "field": {
                "type": "STRING",
                "description": "Field for user_data: name|email|city"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "monitor": {
                "type": "STRING",
                "description": ("screen_find/screen_click: which screen to look at — 'cursor' (default), "
                                "'primary', 'left', 'right', or a number counted from the left")
            },
            "double": {
                "type": "BOOLEAN",
                "description": "screen_click: double-click instead of a single click"
            },
            "button": {
                "type": "STRING",
                "description": "screen_click: left (default) or right"
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": ("Only on the follow-up call after a [CONFIRM_SEND] result, once the "
                                "user has said yes. It does not grant anything by itself.")
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": computer_control,
    "snapshot": _snapshot,
    "verify": _verify,
}
