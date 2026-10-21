"""
Writing to someone in a messenger — as a draft first, sent only when asked.

How the user wants it (2026-10-16): "Write Jane: hi, how are you?" opens the
conversation and types the text into its message box AND LEAVES IT THERE, so
it can be read and changed in the app. "Send it" sends what is in the box now
— after asking, answered by voice or on the HUD (core/send_gate.py).

What it replaced: a blind sequence per app — press Win, type the app's name,
Ctrl+F, type the contact, Enter, paste, Enter — that read nothing back and
answered "Message sent" every time. In Slack, Ctrl+F searches the open
channel, not people, so the text landed wherever the focus happened to be.

Now, on Windows:
  open   — focus (or start) the app, use its jump-to box (Slack and Discord:
           Ctrl+K), paste the name, Enter, and CHECK the conversation that
           opened is that person's: the window title or the message box names
           them ("Message to Jane Doe").
  draft  — open, then paste the text into the focused message box and read it
           back. Nothing is typed when the conversation could not be confirmed.
  send   — back to the window of the last draft; what is in the box NOW is
           what is asked about, and on the user's yes Enter is pressed and the
           box must come back empty.

Telegram and Viber open their conversations their own way (2026-10-21,
actions/_messengers.py): Telegram through its search box, its results read
as Type/Name cells and the chat confirmed by the window title; Viber, which
names nothing to UIA, by the person's phone number. Who the person is comes
from the user's contacts (core/contacts.py) when they list them.

Text goes in through the clipboard (computer_control._paste_text), so it does
not depend on the keyboard layout.
"""
import platform as _platform
import re
import threading
import time

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = _platform.system()

# Process names (without .exe) of the desktop messengers.
_APPS = {
    "slack": "slack", "discord": "discord", "telegram": "telegram", "teams": "ms-teams",
    "whatsapp": "whatsapp", "signal": "signal", "viber": "viber", "skype": "skype",
}
# The shortcut that opens each app's "jump to a conversation" box. Ctrl+F in
# Slack searches messages in the open channel — the old code used it.
_JUMP = {"slack": ("ctrl", "k"), "discord": ("ctrl", "k")}
_JUMP_DEFAULT = ("ctrl", "f")

_tl = threading.local()          # the handler's verdict, for _verify on this thread
_last_draft: dict | None = None  # {"hwnd", "app", "receiver", "field"}


def _app_key(platform: str) -> str:
    p = (platform or "").strip().lower()
    for k in _APPS:
        if k in p:
            return k
    return p.replace(" ", "")


def _norm(t: str) -> str:
    """Text as compared with a read-back: whitespace folded, case folded, and
    emoji left out — Slack keeps 😊 as ":blush:" in its message box, so a
    draft with emoji never matched and was reported as unreadable."""
    import unicodedata
    t = re.sub(r":[a-z0-9_+\-]+:", " ", str(t or ""))
    t = "".join(" " if unicodedata.category(ch) in ("So", "Sk", "Cs") or ch == "\ufe0f" else ch
                for ch in t)
    return " ".join(t.split()).casefold()


def _bare(name: str) -> str:
    """'#social' → 'social', '@Jane' → 'Jane': Slack names channels and
    people without the sign in titles and lists."""
    return str(name or "").strip().lstrip("#@").strip()


def _names_receiver(receiver: str) -> bool:
    """Does the window in front show that person's conversation?"""
    from core import desktop_state as ds
    words = _norm(_bare(receiver)).split()
    if not words:
        return False
    fg = ds.foreground() or {}
    f = ds.focused_control()
    # A focused MESSAGE BOX names the conversation exactly ("Message to Jane
    # Doe") and wins: Slack's window title lags a second behind, and
    # an old "Jane Doe (DM)" title passed while #social was open. Any
    # other focused control was too loose (a search result named like them).
    if f is not None and f.control_type in ("Edit", "Document") and (f.name or "").strip():
        text = f.name
    else:
        text = fg.get("title", "")
    t = _norm(text)
    return all(w in t for w in words)


def _box():
    """The focused message box: (Focus, editable?)."""
    from core import desktop_state as ds
    f = ds.focused_control()
    return f, bool(f and f.control_type in ("Edit", "Document"))


def _bring_app(app: str) -> str:
    """The app's window in front; "" or why not."""
    from actions import computer_control as cc
    from actions.open_app import open_app
    from core import desktop_state as ds
    exe = _APPS.get(app, app)
    if not any(w.exe == exe for w in ds.windows()):
        open_app({"app_name": app})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        wins = [w for w in ds.windows() if w.exe == exe]
        if wins:
            fg = ds.foreground() or {}
            if fg.get("hwnd") not in {w.hwnd for w in wins}:
                cc._bring_forward(max(wins, key=lambda w: len(w.title)).hwnd)
                time.sleep(0.5)
            if (ds.foreground() or {}).get("exe") == exe:
                return ""
        time.sleep(0.4)
    return f"{app.title()} did not come to the front"


def _who(receiver: str, app: str):
    """(name as the app shows them, phone, why not) — through the user's
    contacts (core/contacts.py) when they list this person. A name the
    contacts do not know goes to the app's own search as said; several
    people under one name is a question, never the first one."""
    from core import contacts as cb
    found = cb.resolve(receiver)
    if found.contact is not None:
        c = found.contact
        if c.phone and cb.is_emergency(c.phone):
            return "", "", "that is an emergency number; the assistant never writes to one"
        return c.in_app(app), c.phone, ""
    if found.ambiguous:
        return "", "", f"{found.why}. Ask the user which one: {cb.describe(found.candidates)}"
    return receiver, "", ""


def open_conversation(platform: str, receiver: str) -> tuple[bool, str]:
    """(confirmed?, what happened)."""
    from actions import computer_control as cc
    from core import desktop_state as ds
    app = _app_key(platform)
    if _OS != "Windows":
        return False, f"opening a conversation is only checked on Windows; nothing was done in {app}"
    name, phone, why = _who(receiver, app)
    if why:
        return False, why
    if app == "viber":
        # Viber shows UIA no names at all: the number opens the right chat for
        # certain (actions/_messengers.py). Without one (a shop, a group) the
        # chat is found by Viber's search and confirmed by its header.
        from core import contacts as cb
        if phone and not cb.international(phone):
            return False, (f"Viber chats are opened by phone number, and '{receiver}' has one "
                           "without a country code. Fix it: contacts action='add' with phone (+380…)")
    why = _bring_app(app)
    if why:
        return False, why
    hwnd = (ds.foreground() or {}).get("hwnd") or 0
    if app == "telegram":
        # The contact may know Telegram's name for them only under another
        # app (seen live: "Остапчик" saved for Viber was Telegram's name too).
        from actions import _messengers
        from core import contacts as cb
        c = cb.resolve(receiver).contact
        names = [name, receiver, *((c.aliases + list(c.apps.values())) if c else [])]
        ok, what = False, ""
        for n in dict.fromkeys(x.strip() for x in names if x and x.strip()):
            ok, what = _messengers.telegram_open(hwnd, n)
            if ok or "found nobody" not in what:
                break
        return ok, what
    if app == "viber":
        from actions import _messengers
        from core import contacts as cb
        c = cb.resolve(receiver).contact
        names = [name, *([c.name, *c.aliases] if c else [])]
        if not phone:
            return _messengers.viber_open_by_name(hwnd, name, names)
        return _messengers.viber_open(hwnd, phone, name, names)
    receiver = name
    if _names_receiver(receiver):
        return True, f"{receiver}'s conversation is already open"
    # Esc first: Slack's Ctrl+K toggles. Seen live — a search box left open by
    # the previous attempt was CLOSED by the next Ctrl+K, and "#new-channel"
    # was pasted into Jane's message box instead.
    pyautogui.press("escape")
    time.sleep(0.3)
    pyautogui.hotkey(*_JUMP.get(app, _JUMP_DEFAULT))
    time.sleep(0.7)
    f = ds.focused_control()
    if f is not None and f.control_type in ("Edit", "Document") and cc._COMPOSE_RE.search(f.name or ""):
        return False, (f"{app.title()}'s jump-to box did not open (the message box still has the "
                       "focus), so nothing was typed")
    cc._paste_text(_bare(receiver) if app in _JUMP else receiver)
    picked, offered = _pick_result(receiver)
    if picked is None:
        pyautogui.press("escape")
        if offered:
            return False, (f"{app.title()} offered {', '.join(repr(o) for o in offered[:6])} for "
                           f"'{receiver}', none of them exactly; nothing was opened. Say which one")
        return False, f"{app.title()} found nothing for '{receiver}'; nothing was opened"
    if picked:
        pyautogui.click(*picked)
    else:
        pyautogui.press("enter")          # the app does not list its results to UIA
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if _names_receiver(receiver):
            return True, f"{receiver}'s conversation is open"
    title = ((ds.foreground() or {}).get("title") or "")[:70]
    return False, (f"I searched for '{receiver}', but the conversation that opened ('{title}') "
                   f"does not show that name. Use the name exactly as it appears in {app.title()}")


def _pick_result(receiver: str, wait_s: float = 3.0):
    """The jump box's result for `receiver`, read from UI Automation once the
    app has listed it: ((x, y) to click, names offered). (None, names) when
    the list shows other names only; ((), []) when the app lists nothing to
    UIA, so Enter is all there is. Seen live: Enter 1.4 s after the paste,
    without looking, opened nothing while the right channel sat in the list."""
    from core import desktop_state as ds
    want = _norm(_bare(receiver))
    offered: list[str] = []
    listed = False
    deadline = time.monotonic() + wait_s
    while True:
        fg = ds.foreground() or {}
        found = [c for c in ds.controls(fg.get("hwnd") or 0)
                 if c["type"] in (50007, 50024) and c["name"].strip()]
        listed = listed or bool(found)
        items = [c for c in found if not c["name"].lower().startswith(("search for", "шукати"))]
        # "Jane Doe, Direct Message": the name is before the comma.
        exact = [c for c in items if _norm(_bare(c["name"].split(",")[0])) == want]
        if len(exact) == 1:
            l, t, r, b = exact[0]["rect"]
            return ((l + r) // 2, (t + b) // 2), []
        if len(exact) > 1:
            return None, [c["name"] for c in exact]
        offered = [c["name"][:40] for c in items if want.split()[0] in _norm(c["name"])] if want else []
        if time.monotonic() >= deadline:
            break
        time.sleep(0.3)
    if offered or listed:
        # The app lists its results and none is this name: Enter would only
        # open a search page (seen live).
        return None, offered
    return (), []


def _draft(platform: str, receiver: str, text: str) -> str:
    global _last_draft
    from actions import computer_control as cc
    from core import desktop_state as ds
    from core import verify as v
    ok, what = open_conversation(platform, receiver)
    if not ok:
        _tl.verdict = v.failed(what)
        return f"Could not open the conversation: {what}. Nothing was typed."
    f, editable = _box()
    if not editable:
        _tl.verdict = v.failed("the message box does not have the focus")
        return (f"{what}, but its message box does not have the keyboard focus, so nothing was "
                "typed.")
    replaced = ""
    if f.value and f.value.strip():
        replaced = f" It replaced an earlier unsent draft: '{f.value.strip()[:60]}'."
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.1)
    cc._paste_text(text)
    time.sleep(0.3)
    f2, _ = _box()
    fg = ds.foreground() or {}
    _last_draft = {"hwnd": fg.get("hwnd"), "app": _app_key(platform), "receiver": receiver,
                   "field": (f2.name if f2 else "") or "the message box"}
    if f2 is not None and f2.value is not None and _norm(text) and _norm(text) in _norm(f2.value):
        _tl.verdict = v.verified(f"the text is in '{_last_draft['field']}' and has NOT been sent")
    elif (f2 is not None and f2.value is not None and not _norm(text)
            and "".join(text.split()) in "".join(f2.value.split())):
        # Emoji only ("😡", seen live in Viber): _norm leaves nothing to compare,
        # so the raw characters are — Viber keeps them as typed.
        _tl.verdict = v.verified(f"the text is in '{_last_draft['field']}' and has NOT been sent")
    elif f2 is not None and f2.value is not None and _norm(text):
        _tl.verdict = v.unverified("the message box shows other text than what was typed")
    else:
        _tl.verdict = v.unverified("the message box does not let me read the text back")
    return (f"[DRAFT] {what}; the message is typed into '{_last_draft['field']}' and NOT sent."
            f"{replaced} Tell the user it is ready for them to read and change. Send it only "
            f"when they ask: send_message with action='send'.")


def _send(params: dict) -> str:
    from actions import computer_control as cc
    from core import desktop_state as ds
    from core import verify as v
    d = _last_draft
    if d and d.get("hwnd") and ds.window(d["hwnd"]):
        if (ds.foreground() or {}).get("hwnd") != d["hwnd"]:
            cc._refocus(d["hwnd"])
    c = cc._compose_target()
    if c is None:
        where = f" in {d['app'].title()}" if d else ""
        _tl.verdict = None
        return (f"Could not send: no message box with text has the focus{where}. Nothing was "
                "sent. Write the message first (send_message with action='draft').")
    to = f" to {d['receiver']}" if d and d.get("hwnd") == c["hwnd"] else ""

    def press() -> None:
        pyautogui.press("enter")

    held = cc.send_guard(dict(params, action="press"), "enter", press,
                         tool="send_message with action='send'", to=to)
    if held:
        _tl.verdict = None
        return held
    press()

    def look():
        f = ds.focused_control()
        if f is not None and f.same_control(c["focus"]) and f.value is not None:
            if not f.value.strip():
                if c["app"] == "viber" and c["text"]:
                    from actions import _messengers
                    if _messengers.viber_sent(c["hwnd"], c["text"]):
                        return v.verified("the box is empty and the message shows in the chat")
                return v.verified("the message box is empty — the message went")
            if c["text"] and _norm(f.value) == _norm(c["text"]):
                return v.failed("the text is still in the box — it was not sent")
        return v.unverified("I could not read the message box after Enter")
    _tl.verdict = v.poll(look)
    return f"Pressed Enter in '{c['field']}'{to}."


def send_message(parameters: dict, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    receiver = str(params.get("receiver", "")).strip()
    text = str(params.get("message_text", "")).strip()
    platform = str(params.get("platform", "")).strip() or "Slack"
    action = str(params.get("action", "")).strip().lower() or ("draft" if text else "open")
    _tl.verdict = None
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."
    if player:
        player.write_log(f"[msg] {action} {platform} {receiver}")
    # The length only: a console log is no place for somebody's message (Blobby132).
    print(f"[SendMessage] {action} {platform} → {receiver} ({len(text)} chars)")
    try:
        if action == "send":
            return _send(params)
        if not receiver:
            return "Please specify who the message is for."
        if action == "open":
            from core import verify as v
            ok, what = open_conversation(platform, receiver)
            _tl.verdict = v.verified(what) if ok else v.failed(what)
            return (f"{what}." if ok else f"Could not open the conversation: {what}.")
        if not text:
            return "Please specify the message text."
        return _draft(platform, receiver, text)
    except Exception as e:
        return f"Could not {action} the message: {e}"


def _verify(params: dict, result, before):
    v = getattr(_tl, "verdict", None)
    _tl.verdict = None
    return v


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": (
        "Writes to a person in a desktop messenger (Slack, Telegram, Viber, Discord, Teams…), "
        "in two steps the user controls. action='draft' (the default): opens the conversation "
        "with `receiver` and types `message_text` into its message box WITHOUT sending, so the "
        "user can read and change it. action='send' — only when the user says to send it: sends "
        "what is in the box now, after asking them ([CONFIRM_SEND]); once they say yes, call "
        "again with action='send' and confirmed=true. action='open': just opens that person's "
        "conversation ('find Jane in Slack', 'open my chat with…'). `receiver` is the person as "
        "the user named them, in the nominative ('Оля', 'Jane Doe') — their contacts (the "
        "contacts tool) translate it to the app's name; Viber needs the person's phone number "
        "in the contacts. When the result asks which person is meant, ask the user. Write "
        "`message_text` in the user's own language, exactly what they asked to say. Never say "
        "a message was sent unless the result says so."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "draft (default when there is text) | send | open"
            },
            "receiver": {
                "type": "STRING",
                "description": "The person, as the app names them"
            },
            "message_text": {
                "type": "STRING",
                "description": "draft: the message to type"
            },
            "platform": {
                "type": "STRING",
                "description": "Slack, Telegram, Viber, Discord, Teams… (default Slack)"
            },
            "confirmed": {
                "type": "BOOLEAN",
                "description": "send: only on the follow-up call after [CONFIRM_SEND], once the user said yes"
            }
        },
        "required": []
    },
    "handler": send_message,
    "verify": _verify,
}
