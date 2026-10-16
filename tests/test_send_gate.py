"""Nothing is sent from the user's accounts until the user says so
(core/send_gate.py), and text arrives the same in every keyboard layout."""
import time

import pytest

from actions import computer_control as cc, computer_settings as cs, send_message as sm
from core import activity, confirm, send_gate, verify


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    shown, hidden = [], []
    monkeypatch.setattr(confirm, "_show_cb", lambda t, d: shown.append(t))
    monkeypatch.setattr(confirm, "_hide_cb", lambda: hidden.append(1))
    monkeypatch.setattr(confirm, "_pending", None)
    send_gate.reset()
    activity.reset()
    yield shown, hidden
    send_gate.reset()
    activity.reset()


def _user_answers():
    activity.set_speaking(True)
    activity.set_speaking(False)          # the assistant finished asking
    time.sleep(0.05)                      # Windows monotonic ticks are ~16 ms
    activity.note_user_input()            # …and the user spoke after it


# ── the gate ────────────────────────────────────────────────────────────────

def test_asking_sends_nothing_and_puts_a_button_up(_clean):
    shown, _ = _clean
    sent = []
    out = send_gate.ask("'hi' to John on Slack", ("k",), lambda: sent.append(1) or "Sent.", "send_message")
    assert out.startswith("[CONFIRM_SEND] Nothing was sent") and "END YOUR TURN" in out
    assert sent == [] and shown == ["Send 'hi' to John on Slack"]
    assert send_gate.pending() == "'hi' to John on Slack"


def test_the_model_cannot_confirm_for_the_user(_clean):
    _, hidden = _clean
    send_gate.ask("x", ("k",), lambda: "Sent.", "t")
    assert send_gate.approve(("k",)).startswith("[NOT_SENT] Nothing was sent: you called again before")
    _user_answers()
    assert send_gate.approve(("other",)).startswith("[NOT_SENT] That is not what the user was asked")
    assert send_gate.approve(("k",)) is None              # go ahead
    assert hidden                                          # the HUD button went away
    assert send_gate.approve(("k",)).startswith("[NOT_SENT] Nothing is waiting")


def test_an_old_question_expires(monkeypatch):
    send_gate.ask("x", ("k",), lambda: "Sent.", "t")
    _user_answers()
    monkeypatch.setattr(send_gate, "ASK_TTL", 0.0)
    assert send_gate.approve(("k",)).startswith("[NOT_SENT] Nothing is waiting")


def test_the_hud_button_sends_and_the_follow_up_says_so():
    sent = []
    send_gate.ask("x", ("k",), lambda: sent.append(1) or "Sent.", "t")
    p = confirm._pending
    assert p is not None
    assert p.run() == "Sent." and sent == [1]
    assert send_gate.approve(("k",)).startswith("[ALREADY_SENT]")


def test_waiting_results_get_no_verdict():
    for tag in ("[CONFIRM_SEND] x", "[NOT_SENT] x", "[ALREADY_SENT] x"):
        assert verify.check(lambda p, r, b: verify.failed("no"), {}, tag, None) is None


# ── where it applies ────────────────────────────────────────────────────────

BOX = {"hwnd": 5, "app": "slack", "field": "Message to qa", "text": "John Smith", "focus": None}


def test_enter_in_a_message_box_is_asked_first(monkeypatch):
    pressed = []
    monkeypatch.setattr(cc, "_compose_target", lambda: dict(BOX))
    monkeypatch.setattr(cc, "_press", lambda k: pressed.append(k) or f"Pressed: {k}")
    out = cc.computer_control({"action": "press", "key": "enter"})
    assert out.startswith("[CONFIRM_SEND]") and "'John Smith'" in out and "Message to qa" in out
    assert pressed == []
    out = cc.computer_control({"action": "press", "key": "enter", "confirmed": True})
    assert out.startswith("[NOT_SENT]") and pressed == []
    _user_answers()
    assert cc.computer_control({"action": "press", "key": "enter", "confirmed": True}) == "Pressed: enter"
    assert pressed == ["enter"]


def test_other_keys_and_other_fields_are_not_held(monkeypatch):
    pressed = []
    monkeypatch.setattr(cc, "_press", lambda k: pressed.append(k) or "ok")
    monkeypatch.setattr(cc, "_compose_target", lambda: dict(BOX))
    cc.computer_control({"action": "press", "key": "tab"})
    monkeypatch.setattr(cc, "_compose_target", lambda: None)
    cc.computer_control({"action": "press", "key": "enter"})
    assert pressed == ["tab", "enter"]


def test_what_counts_as_a_message_box(monkeypatch):
    from core import desktop_state as ds
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ds, "is_me", lambda pid: False)

    def case(exe, ctype, name, value):
        monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 1, "exe": exe, "pid": 2})
        monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus(ctype, name, value, 2))
        return cc._compose_target()
    assert case("slack", "Edit", "Message to qa", "hello")["text"] == "hello"
    assert case("slack", "Edit", "Message to qa", "") is None          # nothing to send
    assert case("slack", "Edit", "Message to qa", None)["text"] is None  # unreadable → asked
    assert case("chrome", "Edit", "Type a message", "hi") is not None   # WhatsApp Web
    assert case("chrome", "Edit", "Search Google or type a URL", "x") is None
    assert case("notepad", "Document", "Text editor", "x") is None
    assert case("slack", "Button", "Send now", None) is None


def _messenger(monkeypatch, value_after="Привіт, як справи?"):
    from core import desktop_state as ds
    typed, keys = [], []
    monkeypatch.setattr(sm, "_PYAUTOGUI", True)
    monkeypatch.setattr(sm, "pyautogui", type("P", (), {
        "press": staticmethod(lambda k: keys.append(k)),
        "hotkey": staticmethod(lambda *k: keys.append("+".join(k)))}), raising=False)
    monkeypatch.setattr(cc, "_paste_text", lambda t: typed.append(t))
    state = {"value": ""}
    monkeypatch.setattr(ds, "focused_control",
                        lambda: ds.Focus("Edit", "Message to Jane Doe", state["value"], 3))
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 4, "exe": "slack", "title": "Jane (DM)"})
    # sm.time is the time module itself: replace the name, not time.sleep.
    monkeypatch.setattr(sm, "time", type("T", (), {"sleep": staticmethod(lambda s: None),
                                                   "monotonic": staticmethod(time.monotonic)}))

    def pasted(t):
        typed.append(t)
        state["value"] = value_after
    monkeypatch.setattr(cc, "_paste_text", pasted)
    return typed, keys, state


def test_a_message_is_typed_as_a_draft_and_not_sent(monkeypatch):
    typed, keys, _ = _messenger(monkeypatch)
    monkeypatch.setattr(sm, "open_conversation", lambda p, r: (True, f"{r}'s conversation is open"))
    out = sm.send_message({"receiver": "Jane Doe", "message_text": "Привіт, як справи?",
                           "platform": "Slack"})
    assert out.startswith("[DRAFT]") and "NOT sent" in out
    assert typed == ["Привіт, як справи?"] and "enter" not in keys
    assert sm._verify({}, out, None).state == verify.VERIFIED


def test_nothing_is_typed_into_an_unconfirmed_conversation(monkeypatch):
    typed, keys, _ = _messenger(monkeypatch)
    monkeypatch.setattr(sm, "open_conversation", lambda p, r: (False, "a different chat opened"))
    out = sm.send_message({"receiver": "Jane", "message_text": "hi", "platform": "Slack"})
    assert out.startswith("Could not open") and typed == []


def test_send_asks_about_what_is_in_the_box_now(monkeypatch):
    typed, keys, state = _messenger(monkeypatch)
    state["value"] = "Привіт, як справи? (дописав сам)"     # the user edited the draft
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    from core import desktop_state as ds
    monkeypatch.setattr(ds, "is_me", lambda pid: False)
    out = sm.send_message({"action": "send"})
    assert out.startswith("[CONFIRM_SEND]") and "(дописав сам)" in out and keys == []
    assert sm.send_message({"action": "send", "confirmed": True}).startswith("[NOT_SENT]")
    _user_answers()
    monkeypatch.setattr(sm, "pyautogui", type("P", (), {
        "press": staticmethod(lambda k: (keys.append(k), state.__setitem__("value", "")))}), raising=False)
    out = sm.send_message({"action": "send", "confirmed": True})
    assert keys == ["enter"] and out.startswith("Pressed Enter"), out
    assert sm._verify({}, out, None).state == verify.VERIFIED


def test_the_conversation_must_name_the_person(monkeypatch):
    from core import desktop_state as ds
    monkeypatch.setattr(ds, "foreground", lambda: {"title": "social (Channel) - Slack"})
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("ListItem", "Jane Doe", None, 1))
    assert not sm._names_receiver("Jane Doe")          # a search result is not the chat
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Message to Jane Doe", "", 1))
    assert sm._names_receiver("Jane Doe")


def test_typing_with_enter_types_then_asks(monkeypatch):
    typed, entered = [], []
    monkeypatch.setattr(cs, "type_text", lambda t, press_enter_after=False: typed.append(t))
    monkeypatch.setattr(cs, "press_enter", lambda: entered.append(1))
    monkeypatch.setattr(cc, "_compose_target", lambda: dict(BOX))
    out = cs.computer_settings({"action": "type_text", "value": "John Smith", "press_enter": "true"})
    assert typed == ["John Smith"] and entered == [] and "[CONFIRM_SEND]" in out
    assert cs.computer_settings({"action": "enter"}).startswith("[CONFIRM_SEND]")
    assert entered == []


def test_a_send_button_click_is_asked_first(monkeypatch):
    from core import desktop_state as ds
    clicked = []
    monkeypatch.setattr(cc, "_screen_find", lambda d, m=None: (cc.Found(10, 20, "Send", "by its name", "ui automation"), ""))
    monkeypatch.setattr(cc, "_click", lambda *a: clicked.append(a))
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 3, "exe": "slack"})
    assert cc._screen_click({"description": "Send button"}).startswith("[CONFIRM_SEND]")
    assert clicked == []


# ── typing that does not depend on the layout ──────────────────────────────

def test_windows_typing_goes_through_the_clipboard(monkeypatch):
    pasted, typed = [], []
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cc, "_PYPERCLIP", True)
    monkeypatch.setattr(cc, "_paste_text", lambda t: pasted.append(t))
    monkeypatch.setattr(cc, "pyautogui", type("P", (), {"typewrite": staticmethod(lambda t, interval=0: typed.append(t))}))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    cc._type("John Smith")
    assert pasted == ["John Smith"] and typed == []


def test_letter_keys_are_pinned_to_layout_free_codes():
    pw = pytest.importorskip("pyautogui._pyautogui_win") if cc.platform.system() == "Windows" else None
    if pw is None:
        pytest.skip("Windows only")
    assert pw.keyboardMapping["a"] == 0x41 and pw.keyboardMapping["v"] == 0x56
    assert pw.keyboardMapping["0"] == 0x30


# ── jumping to a conversation (third live test, 2026-10-16) ─────────────────

def test_names_lose_their_signs_and_emoji_compare_equal():
    assert sm._bare("#social") == "social" and sm._bare("@Jane") == "Jane"
    assert sm._norm("Привіт 😊😊") == sm._norm("Привіт :blush::blush:") == "привіт"
    assert cc._norm("ok 👍️") == "ok"


def test_the_message_box_name_beats_a_stale_title(monkeypatch):
    from core import desktop_state as ds
    monkeypatch.setattr(ds, "foreground", lambda: {"title": "Jane Doe (DM) - Slack"})
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Message to social", "", 1))
    assert not sm._names_receiver("Jane Doe")
    assert sm._names_receiver("#social")


def _results(monkeypatch, names):
    from core import desktop_state as ds
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 1})
    monkeypatch.setattr(ds, "controls", lambda h: [
        {"name": n, "type": 50007, "rect": (0, 10 * i, 100, 10 * i + 10), "enabled": True}
        for i, n in enumerate(names)])


def test_the_listed_result_is_picked_by_its_name(monkeypatch):
    _results(monkeypatch, ["Search for: jane", "Jane Doe, Direct Message", "Jane's notes"])
    pick, offered = sm._pick_result("Jane Doe", wait_s=0)
    assert pick == (50, 15) and offered == []
    _results(monkeypatch, ["social", "Search for: social"])
    assert sm._pick_result("#social", wait_s=0)[0] == (50, 5)


def test_no_exact_result_opens_nothing(monkeypatch):
    _results(monkeypatch, ["Search for: zzz"])
    assert sm._pick_result("zzz", wait_s=0) == (None, [])        # listed, not found
    _results(monkeypatch, ["Jane Doe", "Jane Roe"])
    pick, offered = sm._pick_result("Jane", wait_s=0)
    assert pick is None and offered == ["Jane Doe", "Jane Roe"]
    _results(monkeypatch, [])
    assert sm._pick_result("Anyone", wait_s=0) == ((), [])        # the app lists nothing → Enter


def test_nothing_is_pasted_when_the_jump_box_did_not_open(monkeypatch):
    from core import desktop_state as ds
    pasted, keys = [], []
    monkeypatch.setattr(sm, "_OS", "Windows")
    monkeypatch.setattr(sm, "_bring_app", lambda app: "")
    monkeypatch.setattr(sm, "_names_receiver", lambda r: False)
    monkeypatch.setattr(sm, "pyautogui", type("P", (), {
        "press": staticmethod(lambda k: keys.append(k)),
        "hotkey": staticmethod(lambda *k: keys.append("+".join(k)))}), raising=False)
    monkeypatch.setattr(sm, "time", type("T", (), {"sleep": staticmethod(lambda s: None),
                                                   "monotonic": staticmethod(time.monotonic)}))
    monkeypatch.setattr(cc, "_paste_text", lambda t: pasted.append(t))
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Message to Jane Doe", "", 1))
    ok, why = sm.open_conversation("Slack", "#new-channel")
    assert not ok and "did not open" in why and pasted == []
    assert keys[:2] == ["escape", "ctrl+k"]                       # leftovers closed first


def test_emoji_only_typing_is_judged_by_the_change(monkeypatch):
    from core import desktop_state as ds
    real_poll = verify.poll
    monkeypatch.setattr(verify, "poll", lambda check, timeout=0, interval=0: real_poll(check, 0, 0))
    before = {"focus": ds.Focus("Edit", "Message to social", "Усім привіт.", 1)}
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Message to social",
                                                                "Усім привіт. :slightly_smiling_face:", 1))
    assert cc._verify({"action": "type", "text": " 🙂"}, "", before).state == verify.VERIFIED


def test_settings_enter_gets_the_submitted_check(monkeypatch):
    seen = []
    monkeypatch.setattr("actions.computer_control._verify", lambda p, r, b: seen.append(p) or None)
    cs._verify({"action": "enter"}, "Done: enter.", {"fg": None})
    assert seen == [{"action": "press", "key": "enter"}]
