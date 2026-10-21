"""
Opening the right conversation in Telegram Desktop and Viber — what each app
actually lets UI Automation see, measured on the user's PC (2026-10-21).

borgelevisberg17's social_control gave the rules: one window of the app's own
process; a search result is taken only when its name is EXACTLY the person's,
and two such results are a question, never a pick; the conversation that
opened must prove it is theirs before a single character is typed. Their code
looked for the name in a header control — neither app has one:

  Telegram  The search box (Ui::InputField::Inner inside Dialogs::Widget) takes
            the focus from UIA. Every chat in the list and in the search
            results is a ListItem with DataItem cells whose values are the
            chat's Type ('Group', 'Channel', 'Bot' … none for a person) and
            Name. A search also lists public channels and bots of the same name
            ("Saved Messages" gave the user's own chat plus ten channels), so
            the Type matters. The open chat is named only in the WINDOW TITLE:
            "‎Saved Messages – (53622)" (a direction mark, the name, an
            unread count).
  Viber     Qt Quick with no accessible names at all: the list, the results
            and the chat header are blank to UIA. What it does expose: the text
            of the message bubbles and of the message box (values), and
            classes by role (ViberCallButton, SendToolbarButton,
            QQuickTextEdit for the box). So a Viber chat is opened by the
            person's PHONE NUMBER through viber://chat?number=, and "it opened"
            means the bubbles on screen changed to another conversation. When
            they did not change (that chat was already open, or the link did
            nothing) one look at the chat header by vision decides.
"""
from __future__ import annotations

import re
import time

import pyautogui

_MARKS = re.compile("[‎‏‪-‮⁦-⁩]")
_TG_PERSONLIKE = {None, "", "Group", "Saved Messages"}   # what a person writes to


def _iuia():
    from pywinauto.uia_defines import IUIA
    return IUIA().iuia


def _value(el) -> str | None:
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
        v = UIAWrapper(UIAElementInfo(el)).iface_value.CurrentValue
        return None if v is None else str(v)
    except Exception:
        return None


def _by_class(root, cls: str, first: bool = True):
    iuia = _iuia()
    cond = iuia.CreatePropertyCondition(30012, cls)                 # ClassName
    return root.FindFirst(4, cond) if first else root.FindAll(4, cond)


def _rect(el) -> tuple[int, int, int, int]:
    r = el.CurrentBoundingRectangle
    return r.left, r.top, r.right, r.bottom


# ── Telegram ────────────────────────────────────────────────────────────────

def telegram_peer(title: str) -> str:
    """The chat named in Telegram's window title; '' when none is open
    ("Telegram (53622)")."""
    t = _MARKS.sub("", title or "").strip()
    if " – " in t:
        return t.split(" – ")[0].strip()
    return "" if re.fullmatch(r"Telegram( \(\d+\))?", t) else t


def telegram_results(hwnd: int, limit: int = 30) -> list[dict]:
    """The chats Telegram lists now: [{type, name, rect}], on-screen only."""
    iuia = _iuia()
    try:
        root = iuia.ElementFromHandle(int(hwnd))
        pane = _by_class(root, "class Dialogs::Widget") or root
        items = pane.FindAll(4, iuia.CreatePropertyCondition(30003, 50007))   # ListItem
    except Exception:
        return []
    out = []
    for i in range(min(items.Length, 200)):
        el = items.GetElement(i)
        try:
            if el.CurrentIsOffscreen:
                continue
            cells = el.FindAll(2, iuia.CreatePropertyCondition(30003, 50029))  # DataItem
            d = {}
            for j in range(cells.Length):
                c = cells.GetElement(j)
                d[c.CurrentName] = _value(c)
            if d.get("Name"):
                # The user's own chat is Type 'Saved Messages', Name = the
                # user's first name; its title says "Saved Messages".
                name = "Saved Messages" if d.get("Type") == "Saved Messages" else d["Name"]
                out.append({"type": d.get("Type"), "name": _MARKS.sub("", name).strip(),
                            "rect": _rect(el), "cells": d})
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _tg_pick(results: list[dict], want: str, norm) -> tuple[dict | None, list[str]]:
    """(the one result, other names offered)."""
    exact = [r for r in results if norm(r["name"]) == want]
    if len(exact) > 1:
        # The user's contact and public channels/bots under the same name:
        # only a person or a group is someone to write to.
        people = [r for r in exact if r["type"] in _TG_PERSONLIKE]
        if len(people) == 1:
            return people[0], []
        return None, [f"{r['name']} ({r['type'] or 'person'})" for r in exact]
    if exact:
        return exact[0], []
    first = want.split()[0] if want else ""
    return None, [r["name"][:40] for r in results if first and first in norm(r["name"])][:6]


def _tg_clear_search(hwnd: int) -> None:
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyautogui.press("escape")


def telegram_open(hwnd: int, receiver: str) -> tuple[bool, str]:
    """Open `receiver`'s chat in the Telegram window `hwnd` (in front)."""
    from actions import computer_control as cc
    from actions.send_message import _norm
    from core import desktop_state as ds
    want = _norm(receiver)
    if _norm(telegram_peer((ds.foreground() or {}).get("title", ""))) == want:
        f = ds.focused_control()
        if f is None or f.control_type != "Edit" or "search" in (f.name or "").lower():
            _focus_telegram_box(hwnd)
        return True, f"{receiver}'s Telegram chat is already open"
    try:
        root = _iuia().ElementFromHandle(int(hwnd))
        pane = _by_class(root, "class Dialogs::Widget")
        box = _by_class(pane, "class Ui::InputField::Inner") if pane else None
        if box is None:
            return False, "Telegram's search box is not visible to UI Automation; nothing was opened"
        box.SetFocus()
    except Exception as e:
        return False, f"could not reach Telegram's search box ({e}); nothing was opened"
    time.sleep(0.25)
    pyautogui.hotkey("ctrl", "a")
    cc._paste_text(receiver)
    picked, offered = None, []
    deadline = time.monotonic() + 3.0
    while True:
        time.sleep(0.35)
        picked, offered = _tg_pick(telegram_results(hwnd), want, _norm)
        if picked is not None or time.monotonic() >= deadline:
            break
    if picked is None:
        _tg_clear_search(hwnd)
        if offered:
            return False, (f"Telegram offered {', '.join(repr(o) for o in offered)} for '{receiver}', "
                           "not exactly one person; nothing was opened. Say which one")
        return False, f"Telegram found nobody called '{receiver}'; nothing was opened"
    l, t, r, b = picked["rect"]
    pyautogui.click((l + r) // 2, (t + b) // 2)
    deadline = time.monotonic() + 4.0
    peer = ""
    while time.monotonic() < deadline:
        time.sleep(0.35)
        peer = telegram_peer((ds.foreground() or {}).get("title", ""))
        if _norm(peer) == _norm(picked["name"]):
            _focus_telegram_box(hwnd)
            return True, f"{picked['name']}'s Telegram chat is open"
    return False, (f"I picked '{picked['name']}', but Telegram's title names "
                   f"'{peer or 'no chat'}'; nothing was typed")


def _focus_telegram_box(hwnd: int) -> None:
    """The message box of the open chat (Ui::InputField::Inner in HistoryWidget)."""
    try:
        root = _iuia().ElementFromHandle(int(hwnd))
        hist = _by_class(root, "class HistoryWidget")
        box = _by_class(hist, "class Ui::InputField::Inner") if hist else None
        if box is not None:
            box.SetFocus()
            time.sleep(0.2)
    except Exception:
        pass


# ── Viber ───────────────────────────────────────────────────────────────────

def _viber_chat(hwnd: int):
    """(bubble texts on screen, the message box element or None)."""
    iuia = _iuia()
    try:
        root = iuia.ElementFromHandle(int(hwnd))
        edits = root.FindAll(4, iuia.CreatePropertyCondition(30003, 50004))   # Edit
    except Exception:
        return [], None
    bubbles, box = [], None
    for i in range(min(edits.Length, 400)):
        el = edits.GetElement(i)
        try:
            cls = el.CurrentClassName or ""
            if el.CurrentIsOffscreen:
                continue
            if cls.startswith("TextEditItem_QMLTYPE"):
                v = _value(el)
                if v:
                    bubbles.append(v)
            elif cls.startswith("QQuickTextEdit") and box is None:
                box = el
        except Exception:
            continue
    return bubbles, box


def viber_link(phone: str) -> str:
    return "viber://chat?number=%2B" + re.sub(r"\D", "", phone)


# phone → the bubbles of that chat when it was last confirmed open. Seen live:
# the person's chat was already open, so the link changed nothing on screen,
# and Esc does not close a Viber chat to make the change visible.
_viber_seen: dict[str, tuple] = {}


def viber_open(hwnd: int, phone: str, label: str, names: list[str] | None = None) -> tuple[bool, str]:
    """Open the Viber chat with `phone` (international) in window `hwnd`.
    `names`: what the chat header may call them (Viber's own name first)."""
    import os
    before, _ = _viber_chat(hwnd)
    try:
        os.startfile(viber_link(phone))
    except Exception as e:
        return False, f"Viber did not take the chat link ({e}); nothing was opened"
    from actions import computer_control as cc
    from core import desktop_state as ds
    after, box = before, None
    for attempt in range(2):
        deadline = time.monotonic() + (6.0 if attempt == 0 else 5.0)
        while time.monotonic() < deadline:
            time.sleep(0.5)
            # Seen live: the link went through Explorer and Viber stayed behind,
            # its message box reported off screen until it was brought forward.
            if (ds.foreground() or {}).get("hwnd") != hwnd:
                cc._bring_forward(hwnd)
            after, box = _viber_chat(hwnd)
            if box is not None and after != before:
                break
        if box is not None:
            break
        # Seen live twice: Viber just back from the tray showed no chat for
        # the first link; the same link a second time opened it.
        try:
            os.startfile(viber_link(phone))
        except Exception:
            break
    if box is None:
        return False, (f"Viber opened no chat with a message box for {label} — the number may not "
                       "be on Viber; nothing was typed")
    if after == before:
        known = _viber_seen.get(phone)
        if not (after and known == tuple(after)) and not _viber_header_says(hwnd, names or [label], phone):
            return False, (f"I could not confirm that the Viber chat on screen is {label}'s, so "
                           "nothing was typed. If Viber names them differently, save that name: "
                           "contacts action='add' with app='viber' and app_name")
    _viber_seen[phone] = tuple(after)
    try:
        box.SetFocus()
        time.sleep(0.2)
    except Exception:
        pass
    return True, f"{label}'s Viber chat is open (opened by their number {_masked(phone)})"


def _masked(phone: str) -> str:
    from core.contacts import masked
    return masked(phone)


def viber_open_by_name(hwnd: int, label: str, names: list[str]) -> tuple[bool, str]:
    """Open a Viber chat that has no phone number to go by — a shop, a group,
    a community — through Viber's own search, and confirm it by one look at
    the chat header. Viber's results are unnamed blocks (measured live: a
    'Dnipro-M' search listed eight ListViewDelegateLoader items with no names
    or values), so the first result is opened and the header must say yes;
    any other answer types nothing."""
    from actions import computer_control as cc
    key = "name:" + " ".join(label.casefold().split())
    before, box = _viber_chat(hwnd)
    if box is not None and before and _viber_seen.get(key) == tuple(before):
        try:
            box.SetFocus()
        except Exception:
            pass
        return True, f"the Viber chat '{label}' is already open"
    iuia = _iuia()
    try:
        root = iuia.ElementFromHandle(int(hwnd))
        search = root.FindFirst(4, iuia.CreatePropertyCondition(30003, 50004))   # the first Edit
        if search is None or not (search.CurrentClassName or "").startswith("TextFieldItem_QMLTYPE"):
            return False, "Viber's search box is not visible to UI Automation; nothing was opened"
        search.SetFocus()
    except Exception as e:
        return False, f"could not reach Viber's search box ({e}); nothing was opened"
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    cc._paste_text(label)
    first = None
    s_bottom = _rect(search)[3]
    deadline = time.monotonic() + 3.0
    while first is None and time.monotonic() < deadline:
        time.sleep(0.4)
        items = []
        found = root.FindAll(4, iuia.CreatePropertyCondition(30003, 50026))      # Group
        for i in range(min(found.Length, 600)):
            el = found.GetElement(i)
            try:
                if (el.CurrentClassName or "").startswith("ListViewDelegateLoader") and not el.CurrentIsOffscreen:
                    l, t, r, b = _rect(el)
                    if t > s_bottom + 20 and r - l > 100:
                        items.append((t, l, r))
            except Exception:
                continue
        if items:
            t, l, r = min(items)
            first = ((l + r) // 2, t + 30)
    if first is None:
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        pyautogui.press("escape")
        return False, f"Viber found nothing for '{label}'; nothing was opened"
    pyautogui.click(*first)
    after, box = before, None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        time.sleep(0.5)
        after, box = _viber_chat(hwnd)
        if box is not None and after != before:
            break
    if box is None:
        return False, f"Viber's first result for '{label}' opened no chat with a message box; nothing was typed"
    # Seen live: the first look came back without a yes and the same search a
    # second later was confirmed — the header was still being drawn, or the
    # vision model did not answer. One more look before giving up.
    verdict = _viber_header_verdict(hwnd, names or [label], "")
    if verdict == "unknown":
        time.sleep(1.0)
        verdict = _viber_header_verdict(hwnd, names or [label], "")
    if verdict != "yes":
        why = ("its header shows another name" if verdict == "no"
               else "I could not read its header")
        return False, (f"the Viber chat that opened may not be '{label}' ({why}), so nothing was "
                       "typed. Use the name exactly as Viber shows it")
    _viber_seen[key] = tuple(after)
    try:
        box.SetFocus()
        time.sleep(0.2)
    except Exception:
        pass
    return True, f"the Viber chat '{label}' is open (found by Viber's search, its header checked)"


def viber_still_open(hwnd: int, label: str) -> bool:
    """Is the chat confirmed by viber_open_by_name still the one on screen?"""
    before, box = _viber_chat(hwnd)
    key = "name:" + " ".join(label.casefold().split())
    return box is not None and bool(before) and _viber_seen.get(key) == tuple(before)


_HEADER_Q = """This is the top of a chat window in Viber. Is the chat header's name one
of: {names} — or a short or affectionate form of one of them{phone}? Answer JSON
only: {{"verdict": "yes" | "no" | "unknown"}}. "unknown" when no name is readable."""


def _viber_header_says(hwnd: int, names: list[str], phone: str) -> bool:
    """One look at the chat header — only when the bubbles could not tell."""
    return _viber_header_verdict(hwnd, names, phone) == "yes"


def _viber_header_verdict(hwnd: int, names: list[str], phone: str) -> str:
    """'yes' | 'no' | 'unknown' (no answer, nothing readable, or an error)."""
    try:
        import io
        import mss
        from PIL import Image
        from google.genai import types as gtypes
        from core import gemini
        root = _iuia().ElementFromHandle(int(hwnd))
        stack = None
        panes = root.FindAll(4, _iuia().CreatePropertyCondition(30003, 50033))  # Pane
        for i in range(panes.Length):
            el = panes.GetElement(i)
            if (el.CurrentClassName or "").startswith("StackView_QMLTYPE"):
                stack = el
                break
        if stack is None:
            return False
        l, t, r, _b = _rect(stack)
        with mss.mss() as sct:
            g = sct.grab({"left": l, "top": t, "width": r - l, "height": 80})
        img = Image.frombytes("RGB", g.size, g.rgb)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        digits = re.sub(r"\D", "", phone)
        reply = gemini.as_json(
            [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
             _HEADER_Q.format(names=", ".join(f'"{n}"' for n in dict.fromkeys(names)),
                              phone=f", or a phone number ending in {digits[-4:]}" if digits else "")],
            tier=gemini.VISION, timeout_ms=20_000, default=None)
        v = str(reply.get("verdict", "")).lower() if isinstance(reply, dict) else ""
        return v if v in ("yes", "no") else "unknown"
    except Exception:
        return "unknown"


def viber_messages(hwnd: int) -> list[tuple[str, str]]:
    """The messages of the open Viber chat that are on screen, oldest first:
    [("them" | "you", text)]. Viber names nothing, but its bubbles carry
    their text (values) and sit left (received) or right (sent) in the chat
    pane — measured live: received at x 446, sent ending at 1078 in a pane
    418–1106. Missed-call and call items are not text and are left out."""
    iuia = _iuia()
    try:
        root = iuia.ElementFromHandle(int(hwnd))
        panes = root.FindAll(4, iuia.CreatePropertyCondition(30003, 50033))       # Pane
        stack = next((panes.GetElement(i) for i in range(panes.Length)
                      if (panes.GetElement(i).CurrentClassName or "").startswith("StackView_QMLTYPE")),
                     None)
        if stack is None:
            return []
        sl, st, sr, sb = _rect(stack)
        edits = root.FindAll(4, iuia.CreatePropertyCondition(30003, 50004))     # Edit
    except Exception:
        return []
    mid = (sl + sr) // 2
    found = []
    for i in range(min(edits.Length, 400)):
        el = edits.GetElement(i)
        try:
            if el.CurrentIsOffscreen or not (el.CurrentClassName or "").startswith("TextEditItem_QMLTYPE"):
                continue
            l, t, r, b = _rect(el)
            if not (sl <= l and r <= sr and st <= t and b <= sb):
                continue                                  # outside the chat pane
            v = _value(el)
            if v and v.strip():
                found.append((t, "them" if (l + r) // 2 < mid else "you", _MARKS.sub("", v).strip()))
        except Exception:
            continue
    return [(who, text) for _t, who, text in sorted(found)]


def viber_sent(hwnd: int, text: str) -> bool:
    """The message shows as a bubble (borgelevisberg17: in the history AND the
    box empty — the box is checked by the caller)."""
    from actions.send_message import _norm
    bubbles, _ = _viber_chat(hwnd)
    return any(_norm(text) == _norm(b) for b in bubbles)
