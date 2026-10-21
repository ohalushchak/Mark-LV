"""
call — voice and video calls in Telegram, Viber, Slack and to phone numbers
through Phone Link, by the user's rule (2026-10-21): the assistant opens the
person's conversation and stops; the call starts only after the user says yes
(core/send_gate.py, the same gate as sending a message). Accepting a ringing
call asks the same way; declining one and hanging up happen at once.

How each app dials, from what it shows UI Automation (actions/_messengers.py):
  Telegram    the 'Call' button in the open chat's top bar, then "Start Call"
              or "Start Video" in the call window it opens.
  Viber       the call buttons of the open chat, known by class
              (ViberCallButton / VideoCallButton) — Viber names nothing —
              then "Безплатний дзвінок Viber" in the menu the button opens;
              never Viber Out, which is paid.
  Slack       a huddle in the open conversation: Ctrl+Shift+H.
  Phone Link  a tel: link — Phone Link registers itself for tel: and calls
              through the user's Android phone.

"It started" means the app took the microphone or opened a call window with a
hang-up control (core/calls.py) — never that anyone answered.
"""
from __future__ import annotations

import platform as _platform
import re
import threading
import time

from core import calls

_OS = _platform.system()
_tl = threading.local()

_PLATFORM = {"telegram": "telegram", "tg": "telegram", "viber": "viber", "slack": "slack",
             "phone": "phone", "phone link": "phone", "телефон": "phone", "mobile": "phone",
             "sim": "phone", "gsm": "phone"}


def _app(platform: str, receiver: str) -> str:
    p = (platform or "").strip().lower()
    for k, v in _PLATFORM.items():
        if k in p:
            return v
    from core import contacts as cb
    return "phone" if not p and cb.looks_like_phone(receiver) else ""


def _verdict(v):
    _tl.verdict = v


# ── dialing, per app ────────────────────────────────────────────────────────

def _telegram_dial(hwnd: int, who: str, names: list[str], video: bool = False) -> str:
    """`names`: every name the person may have in Telegram — the chat open
    when the button is pressed must be under one of them."""
    from actions import _messengers as mg
    from core import desktop_state as ds
    from actions.send_message import _norm
    peer = _norm(mg.telegram_peer((ds.window(hwnd) or {}).get("title", "")))
    if not peer or peer not in {_norm(n) for n in names}:
        return f"{who}'s chat is no longer the one open in Telegram"
    iuia = mg._iuia()
    bar = mg._by_class(iuia.ElementFromHandle(int(hwnd)), "class HistoryView::TopBarWidget")
    btn = bar.FindFirst(4, iuia.CreatePropertyCondition(30005, "Call")) if bar else None
    if btn is None:
        return "this Telegram chat shows no Call button (a group, a channel or a bot)"
    _click(mg._rect(btn))
    # Seen live (2026-10-21): 'Call' opens the call window with its own
    # "Start Call" / "Start Video" / "Cancel"; nothing rings until one is pressed.
    want = "Start Video" if video else "Start Call"
    start = None
    deadline = time.monotonic() + 4.0
    while start is None and time.monotonic() < deadline:
        time.sleep(0.4)
        start = next((c for w in calls.windows() if w.app == "telegram"
                      for c in w.controls if c.name == want), None)
    if start is None:
        return f"Telegram's call window showed no '{want}' button"
    _click(start.rect)
    return ""


# Viber's call button opens a menu (seen live, 2026-10-21): "Безплатний дзвінок
# Viber" and "+380… (Viber Out)". Only the free call is ever chosen — Viber
# Out costs the user money.
_VIBER_FREE = re.compile(r"(free|безплатн|безкоштовн|бесплатн)", re.IGNORECASE)


def _viber_menu(hwnd: int) -> list[tuple[str, tuple]]:
    """(name, rect) of the menu items Viber shows now."""
    from actions import _messengers as mg
    iuia = mg._iuia()
    try:
        items = iuia.ElementFromHandle(int(hwnd)).FindAll(4, iuia.CreatePropertyCondition(30003, 50011))
    except Exception:
        return []
    out = []
    for i in range(items.Length):
        el = items.GetElement(i)
        try:
            if (el.CurrentClassName or "").startswith("MenuItem_QMLTYPE") and not el.CurrentIsOffscreen:
                out.append((str(el.CurrentName or ""), mg._rect(el)))
        except Exception:
            continue
    return out


def _viber_dial(hwnd: int, video: bool) -> str:
    import pyautogui
    from actions import _messengers as mg
    iuia = mg._iuia()
    want = "VideoCallButton_QMLTYPE" if video else "ViberCallButton_QMLTYPE"
    found = iuia.ElementFromHandle(int(hwnd)).FindAll(4, iuia.CreateTrueCondition())
    button = None
    for i in range(min(found.Length, 3000)):
        el = found.GetElement(i)
        try:
            if (el.CurrentClassName or "").startswith(want) and not el.CurrentIsOffscreen:
                button = mg._rect(el)
                break
        except Exception:
            continue
    if button is None:
        return "the open Viber chat shows no call button"
    # The first click right after the window came forward only showed the
    # button's tooltip once; one more click is safe, since it opens the menu.
    for _attempt in range(2):
        _click(button)
        menu = []
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline and not menu:
            time.sleep(0.3)
            menu = _viber_menu(hwnd)
            if not menu and "viber" in calls.active():
                return ""                        # this button called straight away
        if menu:
            free = [m for m in menu if _VIBER_FREE.search(m[0]) and "viber out" not in m[0].lower()]
            if len(free) != 1:
                pyautogui.press("escape")
                return ("Viber's call menu offered no free Viber call (only "
                        f"{', '.join(repr(n) for n, _ in menu)}); Viber Out is never used")
            _click(free[0][1])
            return ""
    return "Viber's call menu did not open"


def _slack_dial(hwnd: int) -> str:
    import pyautogui
    from actions import computer_control as cc
    cc._refocus(hwnd)
    pyautogui.hotkey("ctrl", "shift", "h")
    return ""


def _phone_dial(number: str) -> str:
    import os
    try:
        os.startfile("tel:" + number)
    except Exception as e:
        return f"Windows did not take the tel: link ({e}); is Phone Link set up?"
    return ""


def _click(rect) -> None:
    import pyautogui
    l, t, r, b = rect
    pyautogui.click((l + r) // 2, (t + b) // 2)


def _started(app: str) -> bool:
    return calls.wait_for(lambda: app in calls.active(), 8.0)


# ── the steps ───────────────────────────────────────────────────────────────

def _start(p: dict) -> str:
    from core import contacts as cb
    from core import desktop_state as ds
    from core import send_gate
    from core import verify as v
    receiver = str(p.get("receiver") or "").strip()
    video = str(p.get("video", "")).strip().lower() in ("true", "1", "yes")
    app = _app(str(p.get("platform") or ""), receiver)
    if not receiver:
        return "Say who to call."
    if not app:
        return "Say where to call them: Telegram, Viber, Slack, or their phone number (Phone Link)."
    if _OS != "Windows":
        return "Calls are built for Windows only so far; nothing was started."
    if video and app in ("slack", "phone"):
        video_note = (" Video cannot be started from here in "
                      f"{calls.LABEL[app]}; it starts as a voice call and the camera is switched on "
                      "in the call window.")
        video = False
    else:
        video_note = ""
    if cb.is_emergency(receiver):        # "112" is too short to look like a phone number
        return "That is an emergency number. The assistant never dials one — the user calls it themselves."
    found = cb.resolve(receiver)
    if found.ambiguous:
        return f"Not started: {found.why}. Ask the user which one: {cb.describe(found.candidates)}."
    c = found.contact
    if c is not None and c.phone and cb.is_emergency(c.phone):
        return "That is an emergency number. The assistant never dials one — the user calls it themselves."
    kind = "video call" if video else "call"
    what = ""                        # what opening the conversation said
    # confirmed=true counts only when the user was asked about this very call;
    # otherwise it is the first call and asks (send_gate.waiting).
    first_who = (c.name if c else receiver) if app == "phone" else (c.in_app(app) if c else receiver)
    confirmed = bool(p.get("confirmed")) and send_gate.waiting(("call", app, first_who.casefold(), video))
    # Step 1 (no confirmation): open the person's conversation, or for the
    # phone, know the number. Step 2 dials only after the user's yes.
    if app == "phone":
        number = c.phone if c is not None else ""
        if not number:
            return (f"There is no phone number for '{receiver}'. Add it with the contacts tool "
                    "(action='add', phone).")
        if cb.is_emergency(number):
            return "That is an emergency number. The assistant never dials one — the user calls it themselves."
        if not any(a == "phone" for a, _h, _t in calls.app_windows()) and not _phone_link_running():
            return "Phone Link is not running, so a phone call cannot be made. Open Phone Link and connect the phone first."
        who = c.name if c else number
        where = f"{who} ({cb.masked(number)}) through Phone Link"

        def dial() -> str:
            return _phone_dial(number)
    else:
        from actions import send_message as sm
        if not confirmed:
            ok, what = sm.open_conversation(app, receiver)
            if not ok:
                _verdict(v.failed(what))
                return f"Could not open the conversation, so no call was prepared: {what}."
        who = c.in_app(app) if c is not None else receiver
        where = f"{who} on {calls.LABEL[app]}"
        phone = c.phone if c is not None else ""
        names = [who, receiver, *([c.name, *c.aliases, *c.apps.values()] if c else [])]

        def dial(app=app, who=who, video=video) -> str:
            # Whatever happened since the question, the person's conversation
            # must be the one open when the button is pressed.
            wins = [w for w in ds.windows() if w.exe == sm._APPS.get(app, app)]
            if not wins:
                return f"{calls.LABEL[app]} is no longer open"
            hwnd = wins[0].hwnd
            from actions import computer_control as cc
            cc._refocus(hwnd)
            if app == "telegram":
                return _telegram_dial(hwnd, who, names, video)
            if app == "viber":
                from actions import _messengers as mg
                if not phone:
                    # Found by search and its header checked when asked; it
                    # must still be the chat on screen (no second vision look).
                    if not mg.viber_still_open(hwnd, who):
                        return f"the Viber chat '{who}' is no longer the one open"
                    return _viber_dial(hwnd, video)
                ok, why = mg.viber_open(hwnd, phone, who, names)
                return _viber_dial(hwnd, video) if ok else why
            if not sm._names_receiver(who):
                return f"{who}'s conversation is no longer the one open in Slack"
            return _slack_dial(hwnd)

    key = ("call", app, who.casefold(), video)

    def run() -> str:
        why = dial()
        if why:
            return f"No call was started: {why}."
        if _started(app):
            return f"The {kind} to {where} has started (ringing; not yet known whether they answer)."
        return f"I pressed {kind} for {where}, but could not see the call start."

    if confirmed:
        refused = send_gate.approve(key)
        if refused:
            return refused
        result = run()
        if result.startswith("The "):
            _verdict(v.verified(f"{calls.LABEL[app]} took the microphone or opened a call window"))
        elif result.startswith("No call"):
            _verdict(v.failed(result))
        else:
            _verdict(v.unverified("no call window and the microphone was not taken"))
        return result + video_note
    _verdict(None)
    return (send_gate.ask(f"{where} ({kind})", key, run, "call with action='start'", verb="call")
            + (f" {what}." if what else "") + video_note)


def _phone_link_running() -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == "phoneexperiencehost.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def _end(p: dict) -> str:
    from core import verify as v
    now = calls.active()
    if not now:
        _verdict(None)
        return "No call is going on that I can see, so nothing was hung up."
    app = now[0]
    if app == "slack":
        # Ctrl+Shift+H also STARTS a huddle: pressed only while Slack is in one.
        import pyautogui
        from core import desktop_state as ds
        wins = [w for w in ds.windows() if w.exe == "slack"]
        if wins:
            from actions import computer_control as cc
            cc._refocus(wins[0].hwnd)
        pyautogui.hotkey("ctrl", "shift", "h")
    else:
        ends = calls.end_controls(app)
        if not ends:
            _verdict(v.failed("no hang-up control is visible"))
            return (f"{calls.LABEL[app]} is in a call, but I see no hang-up button to press. "
                    "End it in the call window.")
        _click(ends[0][1].rect)
    if calls.wait_for(lambda: app not in calls.active(), 5.0):
        _verdict(v.verified(f"{calls.LABEL[app]} shows no call any more"))
        return f"Hung up the {calls.LABEL[app]} call."
    _verdict(v.unverified("the call still looks active"))
    return f"Pressed hang up in {calls.LABEL[app]}, but the call still looks active."


def _accept(p: dict) -> str:
    from core import send_gate
    from core import verify as v
    rings = calls.ringing()
    if not rings:
        _verdict(None)
        return "Nothing is ringing now."
    r = rings[0]
    who = r.who or "the caller"
    key = ("accept", r.app, r.hwnd)

    def run() -> str:
        now = next((x for x in calls.ringing() if x.hwnd == r.hwnd), None)
        if now is None:
            return "The call stopped ringing before it could be accepted."
        _click(now.accept.rect)
        if calls.wait_for(lambda: r.app in calls.active(), 5.0):
            return f"Accepted the {calls.LABEL[r.app]} call from {who}."
        return f"Pressed accept in {calls.LABEL[r.app]}, but could not see the call start."

    if p.get("confirmed") and send_gate.waiting(key):
        refused = send_gate.approve(key)
        if refused:
            return refused
        result = run()
        _verdict(v.verified("the app took the microphone") if result.startswith("Accepted")
                 else v.unverified(result))
        return result
    _verdict(None)
    return send_gate.ask(f"the {calls.LABEL[r.app]} call from {who}", key, run,
                         "call with action='accept'", verb="accept")


def _decline(p: dict) -> str:
    # No confirmation: the user's word is enough to turn a call away
    # (2026-10-21). Only a control the app names as decline is pressed.
    from core import verify as v
    rings = calls.ringing()
    if not rings:
        _verdict(None)
        return "Nothing is ringing now, so nothing was declined."
    r = rings[0]
    _click(r.decline.rect)
    if calls.wait_for(lambda: all(x.hwnd != r.hwnd for x in calls.ringing()), 4.0):
        _verdict(v.verified("the incoming-call window closed"))
        return f"Declined the {calls.LABEL[r.app]} call from {r.who or 'the caller'}."
    _verdict(v.unverified("the call window is still there"))
    return f"Pressed decline in {calls.LABEL[r.app]}, but it still seems to ring."


def _status(p: dict) -> str:
    _verdict(None)
    rings, now = calls.ringing(), calls.active()
    parts = [f"{calls.LABEL[r.app]} is ringing ({r.who or 'caller not shown'})" for r in rings]
    parts += [f"in a {calls.LABEL[a]} call" for a in now]
    return "; ".join(parts) + "." if parts else "No call and nothing ringing."


def call(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "start").strip().lower()
    _tl.verdict = None
    if player is not None:
        try:
            player.write_log(f"[call] {action} {p.get('platform', '')} {p.get('receiver', '')}".rstrip())
        except Exception:
            pass
    try:
        if action in ("start", "call", "dial"):
            return _start(p)
        if action in ("end", "hang_up", "hangup"):
            return _end(p)
        if action == "accept":
            return _accept(p)
        if action in ("decline", "reject"):
            return _decline(p)
        if action == "status":
            return _status(p)
        return f"Unknown action '{action}': use start, end, accept, decline or status."
    except Exception as e:
        return f"Could not {action} the call: {e}"


def _verify(params: dict, result, before):
    v = getattr(_tl, "verdict", None)
    _tl.verdict = None
    return v


TOOL = {
    "name": "call",
    "description": (
        "Voice and video calls: Telegram, Viber, Slack (a huddle) and phone numbers through "
        "Phone Link. action='start' (default) — 'call Olya in Viber', 'подзвони Тарасу в "
        "Telegram', 'video call…' (video=true): opens the person's conversation and asks the "
        "user first ([CONFIRM_SEND]); after their yes call again with confirmed=true. "
        "'accept' — answer the call that is ringing (also asks first). 'decline' — turn the "
        "ringing call away, at once. 'end' — hang up the call going on ('поклади трубку'). "
        "'status' — is a call on or ringing. `receiver` is the person as the user named them "
        "(their contacts give the app's name and the phone number). Never say a call started "
        "or was answered unless the result says so. Messages are send_message, not this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "start (default) | accept | decline | end | status"},
            "receiver": {"type": "STRING", "description": "start: who to call, as the user named them"},
            "platform": {"type": "STRING", "description": "start: telegram | viber | slack | phone"},
            "video": {"type": "BOOLEAN", "description": "start: a video call"},
            "confirmed": {"type": "BOOLEAN",
                          "description": "start/accept: only on the follow-up after [CONFIRM_SEND], once the user said yes"},
        },
        "required": [],
    },
    "handler": call,
    "verify": _verify,
}
