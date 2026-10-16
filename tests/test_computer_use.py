"""Stage 5: the computer-use loop (core/computer_use.py), with the screen,
the vision model and the mouse faked."""
import time

import pytest

from actions import computer_control as cc
from core import activity, announce, computer_use as cu, confirm, desktop_state as ds, verify


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    cu.reset()
    announce.reset()
    activity.reset()
    monkeypatch.setattr(confirm, "_show_cb", lambda t, d: None)
    monkeypatch.setattr(confirm, "_hide_cb", lambda: None)
    monkeypatch.setattr(confirm, "_pending", None)
    monkeypatch.setattr(ds, "cursor", lambda: (10, 10))
    yield
    cu.reset()


def _view(controls=()):
    return cu.View(b"jpg", None, 0, 0, 1000, 800, "the window 'Settings'",
                   [dict(c) for c in controls], None, 1)


def _script(monkeypatch, plans, final=("yes", "night light is on"), acts=None):
    plans = list(plans)
    done = []
    monkeypatch.setattr(cu, "_look", lambda r: _view([{"id": 1, "name": "Night light", "type": 50002,
                                                       "rect": (0, 0, 10, 10)}]))
    monkeypatch.setattr(cu, "_plan", lambda r, v: plans.pop(0) if plans else {"status": "continue",
                                                                                "actions": []})
    monkeypatch.setattr(cu, "_final_check", lambda r: final)

    def act(r, view, a, player=None):
        done.append(a)
        res = (acts or {}).get(a.get("do"), ("VERIFIED: it changed", True))
        return f"{a.get('do')}", res[0], res[1]
    monkeypatch.setattr(cu, "_act", act)
    return done


def _run(goal="turn on night light"):
    r = cu.Run(goal=goal)
    cu._run = r
    cu._work(r, None)
    return r


def test_a_goal_reached_and_confirmed_is_done(monkeypatch):
    done = _script(monkeypatch, [{"status": "continue", "actions": [{"do": "click", "control": 1}]},
                                 {"status": "done", "evidence": "the toggle is on"}])
    r = _run()
    assert r.state == "done" and "night light is on" in r.result
    assert [a["do"] for a in done] == ["click"]
    [item] = announce.take()
    assert "reached the goal" in item.text and "[COMPUTER_USE_RESULT]" in item.text


def test_done_that_the_final_look_cannot_confirm_is_unverified(monkeypatch):
    _script(monkeypatch, [{"status": "done"}], final=("unknown", "the panel is hidden"))
    r = _run()
    assert r.state == "unverified" and "does not confirm" in r.result
    assert "could not confirm the goal" in announce.take()[0].text


def test_done_that_the_final_look_denies_goes_on(monkeypatch):
    done = _script(monkeypatch, [{"status": "done"},
                                 {"status": "continue", "actions": [{"do": "click", "control": 1}]},
                                 {"status": "done"}],
                   final=("no", "still off"))
    monkeypatch.setattr(cu, "MAX_IDLE", 2)
    r = _run()
    assert r.state == "stopped" and done == [{"do": "click", "control": 1}]
    assert any("final look says not yet" in s.verdict for s in r.steps)


def test_impossible_is_reported_as_not_done(monkeypatch):
    _script(monkeypatch, [{"status": "impossible", "evidence": "there is no such setting"}])
    r = _run()
    assert r.state == "failed" and "no such setting" in r.result
    assert "did NOT reach the goal" in announce.take()[0].text


def test_rounds_without_progress_stop_it(monkeypatch):
    plans = [{"status": "continue", "actions": [{"do": "click", "control": 1}]}] * 10
    _script(monkeypatch, plans, acts={"click": ("UNVERIFIED: nothing changed", False)})
    monkeypatch.setattr(cu, "MAX_IDLE", 3)
    r = _run()
    assert r.state == "stopped" and "nothing worked" in r.result and len(r.steps) == 3


def test_only_click_then_type_is_chained_without_a_new_look(monkeypatch):
    done = _script(monkeypatch, [
        {"status": "continue", "actions": [{"do": "click", "control": 1}, {"do": "type", "text": "x"},
                                           {"do": "press", "key": "enter"}]},
        {"status": "continue", "actions": [{"do": "click", "control": 1}, {"do": "click", "control": 1}]},
        {"status": "done"}])
    _run()
    assert [a["do"] for a in done] == ["click", "type", "press", "click"]


def test_moving_the_mouse_stops_it(monkeypatch):
    pos = iter([(10, 10)] + [(400, 300)] * 20)
    monkeypatch.setattr(ds, "cursor", lambda: next(pos))
    _script(monkeypatch, [{"status": "continue", "actions": [{"do": "click", "control": 1}]}] * 5)
    r = _run()
    assert r.state == "stopped" and "moved the mouse" in r.result


def test_stop_by_voice(monkeypatch):
    _script(monkeypatch, [])
    r = cu.Run(goal="g")
    cu._run = r
    assert cu.stop() == "Stopping computer use now."
    cu._work(r, None)
    assert r.state == "stopped" and "stopped by the user" in r.result


def test_starting_twice_is_refused(monkeypatch):
    cu._run = cu.Run(goal="first")
    assert cu.start("second").startswith("[ALREADY_RUNNING]")
    assert cu.start("").startswith("No goal")


# ── risk and confirmation ───────────────────────────────────────────────────

def test_risk_is_read_from_what_is_really_there(monkeypatch):
    monkeypatch.setattr(cc, "_compose_target", lambda: None)
    assert cu._risk({"do": "click"}, "Delete account")
    assert cu._risk({"do": "click"}, "Видалити")
    assert not cu._risk({"do": "click"}, "Night light")
    assert cu._risk({"do": "click", "risky": True, "target": "Apply"}, "")
    monkeypatch.setattr(cc, "_compose_target",
                        lambda: {"hwnd": 1, "app": "slack", "field": "Message to qa", "text": "hi", "focus": None})
    assert "would send" in cu._risk({"do": "press", "key": "enter"}, "")
    assert not cu._risk({"do": "press", "key": "tab"}, "")


def test_a_risky_step_waits_for_the_users_yes(monkeypatch):
    r = cu.Run(goal="g")
    cu._run = r
    monkeypatch.setattr(cu, "ASK_TTL", 5.0)
    import threading
    out = {}

    def ask():
        try:
            cu._ask_user(r, "it clicks 'Delete'")
            out["ok"] = True
        except cu._Stop as e:
            out["stop"] = str(e)
    t = threading.Thread(target=ask)
    t.start()
    for _ in range(100):
        if r.state == "waiting":
            break
        time.sleep(0.01)
    assert r.state == "waiting" and "Delete" in cu.status()
    assert "[COMPUTER_USE_ASK]" in announce.take()[0].text
    assert cu.proceed().startswith("[NOT_CONTINUED]")        # the model cannot answer for the user
    activity.set_speaking(True)
    activity.set_speaking(False)
    time.sleep(0.05)
    activity.note_user_input()
    assert cu.proceed() == "Continuing."
    t.join(2)
    assert out == {"ok": True} and r.state == "running"


def test_a_risky_step_not_confirmed_stops(monkeypatch):
    r = cu.Run(goal="g")
    cu._run = r
    monkeypatch.setattr(cu, "ASK_TTL", 0.05)
    with pytest.raises(cu._Stop):
        cu._ask_user(r, "it clicks 'Pay'")


# ── acting ──────────────────────────────────────────────────────────────────

def test_points_come_from_controls_or_a_confirmed_close_look(monkeypatch):
    PIL = pytest.importorskip("PIL.Image")
    view = cu.View(b"", PIL.new("RGB", (2000, 1000)), 100, 50, 2000, 1000, "w",
                   [{"id": 3, "name": "Apply", "type": 50000, "rect": (200, 100, 300, 140)}])
    r = cu.Run(goal="g")
    assert cu._point(view, {"control": 3}, r) == ((250, 120), "Apply", "")
    assert cu._point(view, {"control": 9}, r)[2] == "there is no control [9]"
    monkeypatch.setattr(cc, "_ask_boxes", lambda img, what, d: [(500, 500, "OK")])
    pt, label, why = cu._point(view, {"point": [500, 500], "target": "OK"}, r)
    assert why == "" and label == "OK" and pt == (100 + 1000, 50 + 500)
    monkeypatch.setattr(cc, "_ask_boxes", lambda img, what, d: [])
    assert "closer look" in cu._point(view, {"point": [500, 500], "target": "OK"}, r)[2]


def test_passwords_are_never_typed(monkeypatch):
    monkeypatch.setattr(ds, "focused_control", lambda: ds.Focus("Edit", "Password", None, 1, True))
    typed = []
    monkeypatch.setattr(cc, "computer_control", lambda p: typed.append(p) or "Typed")
    what, verdict, ok = cu._act(cu.Run(goal="g"), _view(), {"do": "type", "text": "hunter2"})
    assert verdict.startswith("NOT DONE") and typed == [] and not ok


def test_an_action_goes_through_computer_controls_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(cu, "_windows", lambda: False)
    monkeypatch.setattr(cc, "_compose_target", lambda: None)
    monkeypatch.setattr(cc, "_snapshot", lambda p: calls.append(("snap", p["action"])) or {})
    monkeypatch.setattr(cc, "computer_control", lambda p: calls.append(("do", p["action"])) or "Clicked")
    monkeypatch.setattr(cc, "_verify", lambda p, res, b: verify.verified("the dialog opened"))
    view = _view([{"id": 1, "name": "Display", "type": 50000, "rect": (0, 0, 20, 20)}])
    what, verdict, ok = cu._act(cu.Run(goal="g"), view, {"do": "click", "control": 1})
    assert calls == [("snap", "click"), ("do", "click")]
    assert verdict == "VERIFIED: the dialog opened" and ok and "Display" in what


# ── second live test (2026-10-16) ───────────────────────────────────────────

def test_open_goes_through_open_apps_checks(monkeypatch):
    from actions import open_app as oa
    seen = []
    monkeypatch.setattr(oa, "_snapshot", lambda p: {"w": 1})
    monkeypatch.setattr(oa, "open_app", lambda p: seen.append(p["app_name"]) or "Opened ms-settings:nightlight.")
    monkeypatch.setattr(oa, "_verify", lambda p, r, b: verify.verified("'Налаштування' is in front"))
    what, verdict, ok = cu._act(cu.Run(goal="g"), _view(), {"do": "open", "app": "ms-settings:nightlight"})
    assert seen == ["ms-settings:nightlight"] and ok and verdict.startswith("VERIFIED")


def test_the_window_in_front_is_usable_unless_it_is_the_shell_or_me(monkeypatch):
    monkeypatch.setattr(ds, "is_me", lambda pid: pid == 99)
    assert cu._usable({"hwnd": 1, "title": "Налаштування", "exe": "applicationframehost", "pid": 5})
    assert not cu._usable({"hwnd": 1, "title": "J.A.R.V.I.S", "exe": "python", "pid": 99})
    assert not cu._usable({"hwnd": 1, "title": "Search", "exe": "searchhost", "pid": 5})
    assert not cu._usable({"hwnd": 1, "title": "", "exe": "explorer", "pid": 5})


def test_a_monitor_can_be_chosen_by_a_point():
    from actions.screen_processor import _choose_monitor
    mons = [{"left": -2560, "top": 0, "width": 6000, "height": 1440},
            {"left": -2560, "top": 0, "width": 2560, "height": 1440},
            {"left": 0, "top": 0, "width": 3440, "height": 1440, "is_primary": True}]
    assert _choose_monitor(mons, (-100, 500), (3000, 10))[0] == 1


def test_screen_click_refuses_a_label_that_is_not_what_was_asked():
    assert not cc._fits("Check for updates button", "Windows Update")
    assert cc._fits("Send button", "Send") and cc._fits("Пуск", "Кнопка «Пуск»")
    assert cc._fits("Night light", "Нічне світло")       # other alphabet: cannot tell, trusted
    assert cc._fits("settings gear icon", "")            # an icon has no text
