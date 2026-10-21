"""Calls start only after the user's yes, accepting asks too, declining and
hanging up do not; a call in progress is seen from who holds the microphone,
and a ringing one from its accept + decline controls (core/calls.py,
actions/call.py)."""
import sys
import time
import types

import pytest

from actions import call as ca, computer_control as cc
from core import activity, calls, confirm, contacts as cb, send_gate, verify


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(confirm, "_show_cb", lambda t, d: None)
    monkeypatch.setattr(confirm, "_hide_cb", lambda: None)
    monkeypatch.setattr(confirm, "_pending", None)
    monkeypatch.setattr(cb, "PATH", tmp_path / "contacts.json")
    monkeypatch.setattr(ca, "_OS", "Windows")
    monkeypatch.setattr(calls, "wait_for", lambda cond, s, step=0.5: bool(cond()))
    # No real audio-session COM in tests: next to the HUD's WMI thread it
    # crashed the test process.
    monkeypatch.setattr(calls, "capturing", lambda: set())
    clicks = []
    monkeypatch.setattr(ca, "_click", lambda rect: clicks.append(rect))
    send_gate.reset()
    activity.reset()
    yield clicks
    send_gate.reset()
    activity.reset()


def _user_answers():
    activity.set_speaking(True)
    activity.set_speaking(False)
    time.sleep(0.05)
    activity.note_user_input()


def _c(name, cls="", rect=(0, 0, 10, 10)):
    return calls.Control(name, cls, rect)


# ── what is going on ────────────────────────────────────────────────────────

def test_the_microphone_says_who_is_in_a_call(monkeypatch):
    """LastUsedTimeStop = 0 while an app holds the mic (Windows' privacy record)."""
    keys = {
        calls._MIC_KEY + r"\NonPackaged": {
            r"C:#Users#me#AppData#Local#Viber#Viber.exe": (100, 0),          # in use
            r"C:#Users#me#AppData#Roaming#Telegram Desktop#Telegram.exe": (100, 200),
            r"C:#Python#python.exe": (100, 0)},                              # not a call app
        calls._MIC_KEY: {"com.tinyspeck.slackdesktop_8yrtsj140pw4g": (5, 0),
                         "Microsoft.YourPhone_8wekyb3d8bbwe": (5, 9), "NonPackaged": None},
    }

    class K:
        def __init__(self, path, vals=None):
            self.path, self.vals = path, vals

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(root, sub):
        if isinstance(root, K):
            vals = keys[root.path][sub]
            if vals is None:
                raise OSError
            return K(root.path + "\\" + sub, vals)
        if sub not in keys:
            raise OSError
        return K(sub)

    def EnumKey(k, i):
        names = list(keys[k.path])
        if i >= len(names):
            raise OSError
        return names[i]

    def QueryValueEx(k, name):
        return ({"LastUsedTimeStart": k.vals[0], "LastUsedTimeStop": k.vals[1]}[name], 11)

    fake = types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=OpenKey, EnumKey=EnumKey,
                                 QueryValueEx=QueryValueEx)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert calls.mic_in_use() == {"viber", "slack"}


def test_a_ringing_call_offers_accept_and_decline():
    tg = calls.CallWindow("telegram", 1, "Olya Petrenko", [_c("Answer"), _c("Decline"), _c("Mute")])
    vb = calls.CallWindow("viber", 2, "", [_c("", "AcceptCallButton_QMLTYPE_77"),
                                           _c("", "DeclineCallButton_QMLTYPE_78")])
    chat = calls.CallWindow("telegram", 3, "Telegram", [_c("Call"), _c("Info")])
    rings = calls.ringing([tg, vb, chat])
    assert [(r.app, r.who) for r in rings] == [("telegram", "Olya Petrenko"), ("viber", "")]


def test_leave_channel_is_not_a_call():
    slack = calls.CallWindow("slack", 1, "Slack", [_c("Leave channel"), _c("Leave")])
    assert not slack.find(calls._END)
    huddle = calls.CallWindow("slack", 1, "Slack", [_c("Leave huddle")])
    assert huddle.find(calls._END)


def test_the_watch_reports_changes_once(monkeypatch):
    state = {"wins": [], "mic": set()}
    monkeypatch.setattr(calls, "windows", lambda: state["wins"])
    monkeypatch.setattr(calls, "mic_in_use", lambda: state["mic"])
    w = calls.Watch()
    assert w.check() == []
    state["wins"] = [calls.CallWindow("viber", 9, "", [_c("Accept"), _c("Decline")])]
    ev = w.check()
    assert [k for k, _ in ev] == ["ring"] and w.check() == []
    state["wins"], state["mic"] = [], {"viber"}
    assert w.check() == [("call", "viber")] and w.in_call()
    state["mic"] = set()
    assert w.check() == [("ended", "viber")] and not w.in_call()


# ── starting a call ─────────────────────────────────────────────────────────

def _messenger(monkeypatch, opened=True):
    from actions import send_message as sm
    from core import desktop_state as ds
    monkeypatch.setattr(sm, "open_conversation",
                        lambda app, r: (opened, "Остапчик's Viber chat is open" if opened else "no chat"))
    monkeypatch.setattr(ds, "windows", lambda: [types.SimpleNamespace(exe="viber", hwnd=5)])
    monkeypatch.setattr(cc, "_refocus", lambda h: None)
    dialed = []
    from actions import _messengers as mg
    monkeypatch.setattr(mg, "viber_open", lambda h, p, w, n=None: (True, "open"))
    monkeypatch.setattr(ca, "_viber_dial", lambda h, video: dialed.append(video) or "")
    return dialed


def test_a_call_opens_the_chat_and_waits_for_the_users_yes(monkeypatch):
    dialed = _messenger(monkeypatch)
    cb.save([cb.Contact("Остап", [], "+380671112233", {"viber": "Остапчик"})])
    monkeypatch.setattr(calls, "active", lambda wins=None: ["viber"])
    out = ca.call({"receiver": "Остап", "platform": "Viber"})
    assert out.startswith("[CONFIRM_SEND]") and "whether to call Остапчик on Viber" in out
    assert dialed == []
    assert ca.call({"receiver": "Остап", "platform": "Viber", "confirmed": True}).startswith("[NOT_SENT]")
    assert dialed == []
    _user_answers()
    out = ca.call({"receiver": "Остап", "platform": "Viber", "confirmed": True})
    assert dialed == [False] and "has started" in out and "not yet known whether they answer" in out
    assert ca._verify({}, out, None).state == verify.VERIFIED


def test_no_call_is_prepared_when_the_chat_did_not_open(monkeypatch):
    dialed = _messenger(monkeypatch, opened=False)
    out = ca.call({"receiver": "Остап", "platform": "Viber"})
    assert out.startswith("Could not open") and dialed == [] and send_gate.pending() is None


def test_the_hud_button_dials_too(monkeypatch):
    dialed = _messenger(monkeypatch)
    monkeypatch.setattr(calls, "active", lambda wins=None: [])
    ca.call({"receiver": "Остап", "platform": "Viber", "video": True})
    assert confirm._pending.run().startswith("I pressed video call") and dialed == [True]


def test_phone_calls_need_a_number_and_never_an_emergency_one(monkeypatch):
    monkeypatch.setattr(ca, "_phone_link_running", lambda: True)
    monkeypatch.setattr(calls, "app_windows", lambda: [])
    assert "no phone number" in ca.call({"receiver": "Оля", "platform": "phone"})
    assert "emergency" in ca.call({"receiver": "112", "platform": "phone"})
    cb.save([cb.Contact("Оля", [], "+380671234567")])
    dialed = []
    monkeypatch.setattr(ca, "_phone_dial", lambda n: dialed.append(n) or "")
    out = ca.call({"receiver": "Оля", "platform": "phone"})
    assert "whether to call Оля (…4567) through Phone Link" in out and dialed == []
    monkeypatch.setattr(ca, "_phone_link_running", lambda: False)
    assert "Phone Link is not running" in ca.call({"receiver": "Оля", "platform": "phone"})


def test_two_people_under_one_name_are_asked_about(monkeypatch):
    cb.save([cb.Contact("Тарас Коваль"), cb.Contact("Тарас Шевчук")])
    out = ca.call({"receiver": "Тарас", "platform": "Telegram"})
    assert "Ask the user which one" in out


# ── ringing, hanging up ─────────────────────────────────────────────────────

RING = calls.Ring("telegram", 4, "Olya", _c("Answer", rect=(1, 1, 3, 3)), _c("Decline", rect=(5, 5, 7, 7)))


def test_decline_is_at_once_and_accept_asks(monkeypatch, _clean):
    clicks = _clean
    state = {"rings": [RING]}
    monkeypatch.setattr(calls, "ringing", lambda wins=None: state["rings"])
    monkeypatch.setattr(calls, "active", lambda wins=None: ["telegram"])
    out = ca.call({"action": "accept"})
    assert out.startswith("[CONFIRM_SEND]") and "whether to accept the Telegram call from Olya" in out
    assert clicks == []
    _user_answers()
    assert ca.call({"action": "accept", "confirmed": True}).startswith("Accepted")
    assert clicks == [(1, 1, 3, 3)]
    clicks.clear()

    def gone(rect):
        clicks.append(rect)
        state["rings"] = []
    monkeypatch.setattr(ca, "_click", gone)
    state["rings"] = [RING]
    out = ca.call({"action": "decline"})
    assert out.startswith("Declined") and clicks == [(5, 5, 7, 7)]
    assert ca.call({"action": "decline"}).startswith("Nothing is ringing")


def test_hang_up_presses_the_end_control(monkeypatch, _clean):
    clicks = _clean
    state = {"active": ["telegram"]}
    monkeypatch.setattr(calls, "active", lambda wins=None: state["active"])
    win = calls.CallWindow("telegram", 4, "Olya", [_c("Hang up", rect=(2, 2, 4, 4))])

    def end_controls(app, wins=None):
        state["active"] = []
        return [(win, win.controls[0])]
    monkeypatch.setattr(calls, "end_controls", end_controls)
    assert ca.call({"action": "end"}) == "Hung up the Telegram call."
    assert clicks == [(2, 2, 4, 4)]
    assert ca.call({"action": "end"}).startswith("No call is going on")


def test_slack_hang_up_only_while_in_a_huddle(monkeypatch):
    """Ctrl+Shift+H also STARTS a huddle."""
    keys = []
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(
        hotkey=lambda *k: keys.append(k)))
    monkeypatch.setattr(calls, "active", lambda wins=None: [])
    ca.call({"action": "end"})
    assert keys == []


def test_a_call_button_click_is_asked_first():
    assert cc._SEND_LABEL_RE.search("Call") and cc._SEND_LABEL_RE.search("Video call")
    assert cc._SEND_LABEL_RE.search("Подзвонити") and not cc._SEND_LABEL_RE.search("Calendar")
    assert not cc._SEND_LABEL_RE.search("Accept cookies")


def test_telegram_dials_only_the_persons_open_chat(monkeypatch, _clean):
    """Seen live: the contact 'Остап' is 'Остапчик' in Telegram."""
    from actions import _messengers as mg
    from core import desktop_state as ds
    title = {"t": "‎Остапчик – (5)"}
    monkeypatch.setattr(ds, "window", lambda h: {"title": title["t"]})
    btn = types.SimpleNamespace()
    bar = types.SimpleNamespace(FindFirst=lambda scope, cond: btn)
    monkeypatch.setattr(mg, "_iuia", lambda: types.SimpleNamespace(
        ElementFromHandle=lambda h: None, CreatePropertyCondition=lambda *a: None))
    monkeypatch.setattr(mg, "_by_class", lambda root, cls: bar)
    monkeypatch.setattr(mg, "_rect", lambda el: (0, 0, 2, 2))
    monkeypatch.setattr(ca.time, "sleep", lambda s: None)
    # 'Call' opens Telegram's call window, which rings only after "Start Call".
    monkeypatch.setattr(calls, "windows", lambda: [calls.CallWindow("telegram", 8, "Остапчик", [
        _c("Start Call", rect=(5, 5, 9, 9)), _c("Start Video", rect=(10, 5, 14, 9)), _c("Cancel")])])
    assert ca._telegram_dial(7, "Остап", ["Остап", "Остапчик"]) == ""
    assert _clean[-1] == (5, 5, 9, 9)
    assert ca._telegram_dial(7, "Остап", ["Остапчик"], video=True) == "" and _clean[-1] == (10, 5, 14, 9)
    title["t"] = "‎Тарас – (5)"
    assert "no longer" in ca._telegram_dial(7, "Остап", ["Остап", "Остапчик"])


def test_viber_picks_the_free_call_and_never_viber_out(monkeypatch, _clean):
    """Seen live: Viber's call button opens a menu of a free call and Viber Out."""
    clicks = _clean
    from actions import _messengers as mg
    btn = types.SimpleNamespace(CurrentClassName="ViberCallButton_QMLTYPE_14", CurrentIsOffscreen=False)
    found = types.SimpleNamespace(Length=1, GetElement=lambda i: btn)
    monkeypatch.setattr(mg, "_iuia", lambda: types.SimpleNamespace(
        ElementFromHandle=lambda h: types.SimpleNamespace(FindAll=lambda s, c: found),
        CreateTrueCondition=lambda: None))
    monkeypatch.setattr(mg, "_rect", lambda el: (0, 0, 2, 2))
    monkeypatch.setattr(ca.time, "sleep", lambda s: None)
    monkeypatch.setattr(calls, "active", lambda wins=None: [])
    menu = [("Безплатний дзвінок Viber", (10, 10, 20, 20)), ("+380671112233 (Viber Out)", (10, 30, 20, 40))]
    monkeypatch.setattr(ca, "_viber_menu", lambda h: menu)
    assert ca._viber_dial(5, False) == "" and clicks == [(0, 0, 2, 2), (10, 10, 20, 20)]
    clicks.clear()
    keys = []
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(press=lambda k: keys.append(k)))
    monkeypatch.setattr(ca, "_viber_menu", lambda h: [menu[1]])
    assert "Viber Out is never used" in ca._viber_dial(5, False)
    assert clicks == [(0, 0, 2, 2)] and keys == ["escape"]


def _picture(w, h, circles):
    """An RGB picture on a dark background with filled circles (cx, cy, rgb)."""
    px = bytearray(bytes((40, 44, 52)) * (w * h))
    for cx, cy, rgb in circles:
        for y in range(h):
            for x in range(w):
                if (x - cx) ** 2 + (y - cy) ** 2 <= 22 ** 2:
                    px[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb)
    return bytes(px)


def test_viber_call_buttons_are_found_by_colour():
    """Viber's call window shows UI Automation nothing (seen live)."""
    ring = _picture(266, 170, [(102, 100, (240, 60, 60)), (162, 100, (60, 190, 90))])
    bs = calls.round_buttons(ring, 266, (1000, 500))
    assert [(b.name, b.center) for b in bs] == [("Accept", (1162, 600)), ("Decline", (1102, 600))]
    rings = calls.ringing([calls.CallWindow("viber", 1, "ViberPC", bs)])
    assert rings and rings[0].accept.name == "Accept" and rings[0].who == ""   # the window's name, not the caller
    talk = calls.round_buttons(_picture(266, 170, [(133, 100, (240, 60, 60))]), 266, (0, 0))
    assert [b.name for b in talk] == ["End call"]
    assert calls.active([calls.CallWindow("viber", 1, "ViberPC", talk)]) == ["viber"]
    assert calls.round_buttons(_picture(266, 170, []), 266, (0, 0)) == []


def test_viber_hang_up_is_the_white_disc_not_a_red_pill():
    """Seen live: in a Viber call the muted microphone and camera are RED pills,
    hang-up is the rightmost WHITE disc with a red handset."""
    w, h = 600, 80
    px = bytearray(bytes((30, 30, 34)) * (w * h))

    def fill(test, rgb):
        for y in range(h):
            for x in range(w):
                if test(x, y):
                    px[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb)
    fill(lambda x, y: 100 <= x <= 160 and 22 <= y <= 58, (230, 50, 60))          # mic pill
    fill(lambda x, y: 200 <= x <= 260 and 22 <= y <= 58, (230, 50, 60))          # camera pill
    fill(lambda x, y: (x - 520) ** 2 + (y - 40) ** 2 <= 18 ** 2, (255, 255, 255))  # hang-up disc
    fill(lambda x, y: 512 <= x <= 528 and 37 <= y <= 43, (220, 40, 50))          # its handset
    fill(lambda x, y: 20 <= x <= 80 and y in (30, 31), (255, 255, 255))          # a line of text
    c = calls.hangup_in_bar(bytes(px), w, (1000, 900))
    assert c is not None and c.name == "End call" and abs(c.center[0] - 1520) <= 2 and abs(c.center[1] - 940) <= 2
    assert calls.hangup_in_bar(bytes(bytearray(bytes((30, 30, 34)) * (w * h))), w, (0, 0)) is None


def test_a_confirmed_call_nobody_was_asked_about_asks_first(monkeypatch):
    dialed = _messenger(monkeypatch)
    out = ca.call({"receiver": "Остап", "platform": "Viber", "confirmed": True})
    assert out.startswith("[CONFIRM_SEND]") and dialed == []
