"""Telegram and Viber open exactly the person's conversation or nothing
(actions/_messengers.py), and send_message asks about people it cannot tell
apart instead of picking one."""
import pytest

from actions import _messengers as mg, send_message as sm
from core import contacts as cb, desktop_state as ds


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "PATH", tmp_path / "contacts.json")
    monkeypatch.setattr(mg, "time", type("T", (), {"sleep": staticmethod(lambda s: None),
                                                   "monotonic": staticmethod(_clock())}))
    keys = []
    monkeypatch.setattr(mg, "pyautogui", type("P", (), {
        "hotkey": staticmethod(lambda *k: keys.append("+".join(k))),
        "press": staticmethod(lambda k: keys.append(k)),
        "click": staticmethod(lambda x, y: keys.append(("click", x, y)))}))
    from actions import computer_control as cc
    monkeypatch.setattr(cc, "_paste_text", lambda t: keys.append(("paste", t)))
    yield keys


def _clock():
    t = [0.0]

    def now():
        t[0] += 0.5
        return t[0]
    return now


# ── Telegram ────────────────────────────────────────────────────────────────

def test_the_title_names_the_open_chat():
    assert mg.telegram_peer("‎Saved Messages – (53622)") == "Saved Messages"
    assert mg.telegram_peer("Olya Petrenko – Telegram") == "Olya Petrenko"
    assert mg.telegram_peer("Telegram (53622)") == ""
    assert mg.telegram_peer("Telegram") == ""


def test_a_person_wins_over_channels_of_the_same_name():
    res = [{"type": None, "name": "Olya", "rect": (0, 0, 10, 10)},
           {"type": "Channel", "name": "Olya", "rect": (0, 10, 10, 20)},
           {"type": "Bot", "name": "Olya", "rect": (0, 20, 10, 30)}]
    picked, _ = mg._tg_pick(res, "olya", sm._norm)
    assert picked["type"] is None
    two = [{"type": None, "name": "Olya", "rect": (0, 0, 1, 1)},
           {"type": None, "name": "Olya", "rect": (0, 1, 1, 2)}]
    picked, offered = mg._tg_pick(two, "olya", sm._norm)
    assert picked is None and len(offered) == 2
    picked, offered = mg._tg_pick([{"type": None, "name": "Olya Kovalenko", "rect": (0, 0, 1, 1)}],
                                  "olya", sm._norm)
    assert picked is None and offered == ["Olya Kovalenko"]   # a near name is offered, not opened


class _El:
    """A UIA element stand-in for the search box."""
    def __init__(self):
        self.focused = 0

    def SetFocus(self):
        self.focused += 1


def _telegram(monkeypatch, results, titles):
    box = _El()
    monkeypatch.setattr(mg, "_iuia", lambda: type("I", (), {"ElementFromHandle": staticmethod(lambda h: object())}))
    monkeypatch.setattr(mg, "_by_class", lambda root, cls, first=True: box)
    monkeypatch.setattr(mg, "telegram_results", lambda hwnd: results)
    monkeypatch.setattr(mg, "_focus_telegram_box", lambda hwnd: None)
    seq = iter(titles)
    last = {"t": titles[0]}

    def fg():
        last["t"] = next(seq, last["t"])
        return {"hwnd": 7, "exe": "telegram", "title": last["t"]}
    monkeypatch.setattr(ds, "foreground", fg)
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Write a message...", "", 1))
    return box


def test_telegram_opens_the_exact_person_and_checks_the_title(monkeypatch, _quiet):
    box = _telegram(monkeypatch, [{"type": None, "name": "Olya Petrenko", "rect": (0, 0, 100, 40)}],
                    ["Telegram (5)", "Telegram (5)", "‎Olya Petrenko – (5)"])
    ok, what = mg.telegram_open(7, "Olya Petrenko")
    assert ok and "Olya Petrenko" in what and box.focused == 1
    assert ("paste", "Olya Petrenko") in _quiet and ("click", 50, 20) in _quiet


def test_telegram_types_nothing_when_another_chat_opened(monkeypatch, _quiet):
    _telegram(monkeypatch, [{"type": None, "name": "Olya Petrenko", "rect": (0, 0, 100, 40)}],
              ["Telegram (5)", "Telegram (5)"] + ["‎Taras – (5)"] * 20)
    ok, what = mg.telegram_open(7, "Olya Petrenko")
    assert not ok and "'Taras'" in what


def test_telegram_clears_its_search_when_nobody_fits(monkeypatch, _quiet):
    _telegram(monkeypatch, [], ["Telegram (5)"])
    ok, what = mg.telegram_open(7, "Nobody Here")
    assert not ok and "found nobody" in what and "escape" in _quiet


# ── Viber ───────────────────────────────────────────────────────────────────

def test_viber_opens_by_number_and_needs_the_bubbles_to_change(monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda link: opened.append(link), raising=False)
    box = _El()
    states = iter([(["old chat"], box), (["old chat"], box), (["new chat"], box)])
    monkeypatch.setattr(mg, "_viber_chat", lambda hwnd: next(states))
    ok, what = mg.viber_open(9, "+380671234567", "Оля")
    assert ok and opened == ["viber://chat?number=%2B380671234567"] and "…4567" in what
    assert box.focused == 1


def test_viber_without_proof_types_nothing(monkeypatch):
    monkeypatch.setattr("os.startfile", lambda link: None, raising=False)
    monkeypatch.setattr(mg, "_viber_chat", lambda hwnd: (["same chat"], _El()))
    monkeypatch.setattr(mg, "_viber_header_says", lambda h, n, p: False)
    ok, what = mg.viber_open(9, "+380671234567", "Оля")
    assert not ok and "could not confirm" in what
    monkeypatch.setattr(mg, "_viber_chat", lambda hwnd: ([], None))
    ok, what = mg.viber_open(9, "+380671234567", "Оля")
    assert not ok and "no chat with a message box" in what


def test_a_viber_chat_already_open_is_known_by_its_bubbles(monkeypatch):
    """Seen live: the person's chat was already open, so the link changed nothing."""
    monkeypatch.setattr("os.startfile", lambda link: None, raising=False)
    monkeypatch.setattr(mg, "_viber_seen", {})
    looks = []
    monkeypatch.setattr(mg, "_viber_header_says", lambda h, n, p: looks.append(n) or True)
    monkeypatch.setattr(mg, "_viber_chat", lambda hwnd: (["their chat"], _El()))
    assert mg.viber_open(9, "+380671112233", "Остапчик", ["Остапчик", "Остап"])[0]
    assert looks == [["Остапчик", "Остап"]]            # one look the first time…
    assert mg.viber_open(9, "+380671112233", "Остапчик")[0]
    assert len(looks) == 1                            # …none once the chat is known
    monkeypatch.setattr(mg, "_viber_header_says", lambda h, n, p: False)
    monkeypatch.setattr(mg, "_viber_chat", lambda hwnd: (["another chat"], _El()))
    assert not mg.viber_open(9, "+380671112233", "Остапчик")[0]   # changed but unchanged by the link


# ── send_message with contacts ──────────────────────────────────────────────

def test_viber_needs_a_number_with_a_country_code(monkeypatch):
    monkeypatch.setattr(sm, "_OS", "Windows")
    called = []
    monkeypatch.setattr(sm, "_bring_app", lambda app: called.append(app) or "")
    cb.save([cb.Contact("Оля Петренко", ["Оля"], "0671234567")])
    ok, what = sm.open_conversation("Viber", "Оля")
    assert not ok and "country code" in what and called == []


def test_the_contacts_name_the_person_in_each_app(monkeypatch):
    monkeypatch.setattr(sm, "_OS", "Windows")
    monkeypatch.setattr(sm, "_bring_app", lambda app: "")
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 7, "exe": "telegram", "title": "Telegram"})
    asked = []
    monkeypatch.setattr(mg, "telegram_open", lambda hwnd, name: (asked.append(name), (True, "open"))[1])
    monkeypatch.setattr(mg, "viber_open", lambda hwnd, phone, name, names=None: (asked.append(phone), (True, "open"))[1])
    cb.save([cb.Contact("Оля Петренко", ["Оля"], "+380671234567", {"telegram": "Olya Petrenko"})])
    assert sm.open_conversation("Telegram", "Оля")[0] and asked == ["Olya Petrenko"]
    assert sm.open_conversation("Viber", "Оля")[0] and asked[-1] == "+380671234567"


def test_two_contacts_under_one_name_are_asked_about(monkeypatch):
    monkeypatch.setattr(sm, "_OS", "Windows")
    monkeypatch.setattr(sm, "_bring_app", lambda app: pytest.fail("nothing may be opened"))
    cb.save([cb.Contact("Тарас Коваль"), cb.Contact("Тарас Шевчук")])
    ok, what = sm.open_conversation("Telegram", "Тарас")
    assert not ok and "Ask the user which one" in what and "Тарас Шевчук" in what


def test_a_name_not_in_the_contacts_goes_to_the_app_as_said(monkeypatch):
    assert sm._who("Jane Doe", "slack") == ("Jane Doe", "", "")


def test_viber_messages_are_theirs_left_and_the_users_right(monkeypatch):
    """Measured live: received at x 446, sent ending at 1078 in a pane 418–1106."""
    class El:
        def __init__(self, cls, rect, value=""):
            self.CurrentClassName, self.rect, self.value, self.CurrentIsOffscreen = cls, rect, value, False
    stack = El("StackView_QMLTYPE_177", (418, 181, 1106, 1058))
    edits = [El("TextEditItem_QMLTYPE_1143_QML_1276", (961, 656, 1078, 675), "Привіт, як справи?"),
             El("TextEditItem_QMLTYPE_1143_QML_1276", (446, 727, 487, 746), "Добре"),
             El("TextEditItem_QMLTYPE_1143_QML_1212", (833, 66, 1074, 142), "a link above the pane"),
             El("QQuickTextEdit_QML_1554", (538, 1023, 1038, 1042), "")]
    coll = lambda xs: type("C", (), {"Length": len(xs), "GetElement": lambda self, i: xs[i]})()
    root = type("R", (), {"FindAll": lambda self, s, cond: coll([stack]) if cond == "pane" else coll(edits)})()
    monkeypatch.setattr(mg, "_iuia", lambda: type("I", (), {
        "ElementFromHandle": staticmethod(lambda h: root),
        "CreatePropertyCondition": staticmethod(lambda p, v: "pane" if v == 50033 else "edit")}))
    monkeypatch.setattr(mg, "_rect", lambda el: el.rect)
    monkeypatch.setattr(mg, "_value", lambda el: el.value)
    assert mg.viber_messages(1) == [("you", "Привіт, як справи?"), ("them", "Добре")]


def test_telegram_lines_say_who_wrote_last():
    from actions import read_messages as rm
    them = {"name": "Olya", "type": None, "cells": {"Message": "Добре", "Delivery": "Received", "Time": "20:01",
                                                   "Unread": "1 new message"}}
    mine = {"name": "Olya", "type": None, "cells": {"Message": "Іди вчи уроки", "Delivery": "Seen", "Time": "20:04"}}
    group = {"name": "Family", "type": "Group", "cells": {"Message": "Вечеря о 7", "Sender": "Mom",
                                                         "Delivery": "Received", "Muted": "Muted"}}
    assert "from Olya: «Добре»" in rm._tg_line(them) and "1 new message" in rm._tg_line(them)
    assert "from you (seen)" in rm._tg_line(mine)
    assert "(group)" in rm._tg_line(group) and "from Mom" in rm._tg_line(group) and "muted" in rm._tg_line(group)


def test_a_viber_chat_without_a_number_goes_by_name(monkeypatch):
    """A shop or a group has no number: Viber's search, then the header."""
    monkeypatch.setattr(sm, "_OS", "Windows")
    monkeypatch.setattr(sm, "_bring_app", lambda app: "")
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 7, "exe": "viber", "title": "Rakuten Viber"})
    went = []
    monkeypatch.setattr(mg, "viber_open_by_name", lambda h, label, names: (went.append(label), (True, "open"))[1])
    monkeypatch.setattr(mg, "viber_open", lambda *a: pytest.fail("no number to open by"))
    assert sm.open_conversation("Viber", "Dnipro-M") == (True, "open") and went == ["Dnipro-M"]


class _Box:
    def SetFocus(self):
        pass


def _viber_search(monkeypatch, states, header_yes):
    """Viber's search box, one unnamed result, and the chat states in turn."""
    class El:
        def __init__(self, cls, rect):
            self.CurrentClassName, self.rect, self.CurrentIsOffscreen = cls, rect, False

        def SetFocus(self):
            pass
    search = El("TextFieldItem_QMLTYPE_236", (198, 153, 370, 181))
    result = El("ListViewDelegateLoader_QMLTYPE_395", (118, 294, 418, 1237))
    coll = lambda xs: type("C", (), {"Length": len(xs), "GetElement": lambda self, i: xs[i]})()
    root = type("R", (), {"FindFirst": lambda self, s, c: search,
                          "FindAll": lambda self, s, c: coll([result])})()
    monkeypatch.setattr(mg, "_iuia", lambda: type("I", (), {
        "ElementFromHandle": staticmethod(lambda h: root),
        "CreatePropertyCondition": staticmethod(lambda p, v: None)}))
    monkeypatch.setattr(mg, "_rect", lambda el: el.rect)
    seq = iter(states)
    last = {"s": states[0]}
    monkeypatch.setattr(mg, "_viber_chat", lambda h: last.__setitem__("s", next(seq, last["s"])) or last["s"])
    looks = []
    monkeypatch.setattr(mg, "_viber_header_verdict", lambda h, n, p: looks.append(n) or ("yes" if header_yes else "no"))
    monkeypatch.setattr(mg, "_viber_seen", {})
    return looks


def test_viber_by_name_types_only_after_the_header_says_yes(monkeypatch, _quiet):
    looks = _viber_search(monkeypatch, [(["old"], _Box()), (["Dnipro news"], _Box())], header_yes=False)
    ok, what = mg.viber_open_by_name(7, "Dnipro-M", ["Dnipro-M"])
    assert not ok and "nothing was typed" in what and looks == [["Dnipro-M"]]
    assert ("click", 268, 324) in _quiet                      # the first result
    looks = _viber_search(monkeypatch, [(["old"], _Box()), (["Dnipro news"], _Box())], header_yes=True)
    ok, what = mg.viber_open_by_name(7, "Dnipro-M", ["Dnipro-M"])
    assert ok and "header checked" in what
    assert mg.viber_still_open(7, "Dnipro-M")                 # the same bubbles: no second look
    ok, what = mg.viber_open_by_name(7, "Dnipro-M", ["Dnipro-M"])
    assert ok and "already open" in what and len(looks) == 1
