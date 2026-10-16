"""Stages 2–4 of verified computer use: each action's own check, with the
desktop faked — nothing here touches the real screen, mouse or apps."""
import io
from types import SimpleNamespace

import pytest

from actions import (browser_control as bc, computer_control as cc,
                     computer_settings as cs, file_controller as fc, open_app as oa)
from actions._app_closer import Win
from core import desktop_state as ds
from core import verify


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    """No waiting: one look per poll, no settle gaps."""
    real_poll = verify.poll
    monkeypatch.setattr(verify, "poll", lambda check, timeout=0, interval=0: real_poll(check, 0, 0))
    monkeypatch.setattr(cc, "_NOISE_GAP_S", 0)
    monkeypatch.setattr(ds, "sample", lambda rect: None)


def F(control_type="Edit", name="Search", value="", pid=7, password=False):
    return ds.Focus(control_type, name, value, pid, password)


# ── computer_control: typing ────────────────────────────────────────────────

def test_typing_is_verified_by_reading_the_field_back(monkeypatch):
    monkeypatch.setattr(ds, "focused_control", lambda: F(value="hello  World"))
    v = cc._verify({"action": "type", "text": "hello world"}, "Typed", {"focus": F(value="")})
    assert v.state == verify.VERIFIED


def test_typing_into_an_unchanged_field_failed(monkeypatch):
    monkeypatch.setattr(ds, "focused_control", lambda: F(value="old"))
    v = cc._verify({"action": "type", "text": "new"}, "Typed", {"focus": F(value="old")})
    assert v.state == verify.FAILED


def test_typing_where_nothing_can_be_read_is_unverified(monkeypatch):
    monkeypatch.setattr(ds, "focused_control", lambda: F(control_type="Document", value=None))
    assert cc._verify({"action": "type", "text": "x"}, "", {}).state == verify.UNVERIFIED
    monkeypatch.setattr(ds, "focused_control", lambda: F(password=True, value=None))
    v = cc._verify({"action": "type", "text": "x"}, "", {})
    assert v.state == verify.UNVERIFIED and "password" in v.detail


def test_clear_field_needs_an_empty_field(monkeypatch):
    monkeypatch.setattr(ds, "focused_control", lambda: F(value=""))
    assert cc._verify({"action": "clear_field"}, "", {}).state == verify.VERIFIED
    monkeypatch.setattr(ds, "focused_control", lambda: F(value="left"))
    assert cc._verify({"action": "clear_field"}, "", {}).state == verify.FAILED


def test_cyrillic_goes_through_the_clipboard(monkeypatch):
    # macOS / Linux: ASCII is still typed; Windows pastes everything
    # (tests/test_send_gate.py).
    typed, pasted, clip = [], [], {"v": "user's clipboard"}
    monkeypatch.setattr(cc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cc, "_get_os", lambda: "linux")
    monkeypatch.setattr(cc, "_PYPERCLIP", True)
    monkeypatch.setattr(cc, "pyperclip", SimpleNamespace(
        paste=lambda: clip["v"], copy=lambda t: clip.__setitem__("v", t)))
    monkeypatch.setattr(cc, "pyautogui", SimpleNamespace(
        typewrite=lambda t, interval=0: typed.append(t),
        hotkey=lambda *k: pasted.append(clip["v"])))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    cc._type("Привіт")
    cc._type("hello")
    assert pasted == ["Привіт"] and typed == ["hello"]
    assert clip["v"] == "user's clipboard"            # given back


# ── computer_control: pointer, clicks, windows ──────────────────────────────

def test_move_is_checked_against_the_pointer(monkeypatch):
    monkeypatch.setattr(ds, "cursor", lambda: (101, 199))
    assert cc._verify({"action": "move", "x": 100, "y": 200}, "", None).state == verify.VERIFIED
    monkeypatch.setattr(ds, "cursor", lambda: (0, 0))
    assert cc._verify({"action": "move", "x": 100, "y": 200}, "", None).state == verify.FAILED


def test_a_click_with_the_pointer_elsewhere_failed(monkeypatch):
    monkeypatch.setattr(ds, "cursor", lambda: (5, 5))
    v = cc._verify({"action": "click", "x": 400, "y": 300}, "", {"region": (0, 0, 9, 9)})
    assert v.state == verify.FAILED and "(5, 5)" in v.detail


def test_a_click_is_verified_by_what_changed(monkeypatch):
    monkeypatch.setattr(ds, "cursor", lambda: (400, 300))
    fg = {"hwnd": 1, "title": "Editor"}
    monkeypatch.setattr(ds, "foreground", lambda: fg)
    monkeypatch.setattr(ds, "focused_control", lambda: F(name="Search"))
    before = {"fg": fg, "focus": F(name="Search"), "region": (0, 0, 9, 9)}
    v = cc._verify({"action": "click", "x": 400, "y": 300}, "", before)
    assert v.state == verify.UNVERIFIED                   # nothing moved
    monkeypatch.setattr(ds, "focused_control", lambda: F(control_type="Button", name="Send"))
    v = cc._verify({"action": "click", "x": 400, "y": 300}, "", before)
    assert v.state == verify.VERIFIED and "Send" in v.detail
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 2, "title": "Save as"})
    v = cc._verify({"action": "click", "x": 400, "y": 300}, "", before)
    assert v.state == verify.VERIFIED and "Save as" in v.detail


def test_noise_cells_do_not_count_as_a_change():
    np = pytest.importorskip("numpy")
    a = np.zeros((4, 4), dtype=np.float32)
    b = a.copy()
    b[0, 0] = 200                                     # the caret blinked
    mask = ds.noise(a, b)
    after = a.copy()
    after[0, 0] = 200
    assert ds.changed_cells(b, after, mask) == 0
    after[2:4, 2:4] = 120                             # a real change
    assert ds.changed_cells(b, after, mask) == 4


def test_focus_window_checks_the_window_in_front(monkeypatch):
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cc, "_find_window_win", lambda t: Win(5, 9, "notepad", "notes.txt - Notepad"))
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 5, "title": "notes.txt - Notepad"})
    assert cc._verify({"action": "focus_window", "title": "notepad"}, "", None).state == verify.VERIFIED
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 6, "title": "Chrome"})
    v = cc._verify({"action": "focus_window", "title": "notepad"}, "", None)
    assert v.state == verify.FAILED and "Chrome" in v.detail


# ── computer_control: finding things on screen ──────────────────────────────

def test_uia_finds_by_whole_words_and_refuses_ambiguity(monkeypatch):
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 1})
    monkeypatch.setattr(ds, "taskbar", lambda: 2)
    monkeypatch.setattr(ds, "window", lambda h: {"pid": 99})
    monkeypatch.setattr(ds, "is_me", lambda pid: False)
    ctl = {1: [{"name": "Може запускатися", "type": 1, "rect": (0, 0, 10, 10), "enabled": True}],
           2: [{"name": "Кнопка «Пуск»", "type": 1, "rect": (1412, 1392, 1457, 1440), "enabled": True}]}
    monkeypatch.setattr(ds, "controls", lambda h: ctl[h])
    found, why = cc._uia_find("Пуск")
    assert (found.x, found.y, found.how) == (1434, 1416, "ui automation")
    ctl[1].append({"name": "Пуск", "type": 1, "rect": (50, 50, 60, 60), "enabled": True})
    found, why = cc._uia_find("Пуск")
    assert found.x == 55                                  # the exact name wins
    ctl[1].append({"name": "пуск", "type": 1, "rect": (70, 70, 80, 80), "enabled": True})
    found, why = cc._uia_find("Пуск")
    assert found is None and "2 controls" in why
    assert cc._uia_find("Settings") == (None, "")          # → vision


def _fake_screen(monkeypatch, replies):
    PIL = pytest.importorskip("PIL.Image")
    img = PIL.new("RGB", (2000, 1000), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    geom = {"left": -2000, "top": 100, "width": 2000, "height": 1000, "label": "monitor 1 of 2"}
    monkeypatch.setattr("actions.screen_processor.capture_monitor", lambda which=None: (buf.getvalue(), geom))
    asked = []

    def ask(img, what, desc):
        asked.append((img.size, what))
        return replies.pop(0)
    monkeypatch.setattr(cc, "_ask_boxes", ask)
    return asked


def test_vision_maps_the_close_look_back_to_the_screen(monkeypatch):
    asked = _fake_screen(monkeypatch, [[(500, 500, "Send")], [(500, 500, "Send")]])
    found, why = cc._vision_find("Send button", None)
    assert why == "" and found.how == "vision"
    assert (found.x, found.y) == (-1000, 600)              # centre of that screen
    assert asked[0][0][0] <= 1600 and "close-up" in asked[1][1]
    assert asked[1][0] == (640, 400)                       # native-resolution crop


def test_vision_refuses_when_the_close_look_disagrees(monkeypatch):
    _fake_screen(monkeypatch, [[(500, 500, "Send")], []])
    found, why = cc._vision_find("Send button", None)
    assert found is None and "closer look" in why


def test_vision_refuses_several_matches(monkeypatch):
    _fake_screen(monkeypatch, [[(100, 100, "OK"), (900, 900, "OK")]])
    found, why = cc._vision_find("OK button", None)
    assert found is None and "2 elements" in why


def test_boxes_are_parsed_and_screen_sized_ones_dropped(monkeypatch):
    PIL = pytest.importorskip("PIL.Image")
    from core import gemini
    monkeypatch.setattr(gemini, "as_json", lambda *a, **k: {"matches": [
        {"box_2d": [100, 200, 120, 260], "label": "Send"},
        {"box_2d": [0, 0, 1000, 1000], "label": "everything"},
        {"box_2d": [50, 50], "label": "broken"}]})
    boxes = cc._ask_boxes(PIL.new("RGB", (10, 10)), "x", "Send")
    assert boxes == [(230.0, 110.0, "Send")]


def test_a_pointer_that_lands_elsewhere_is_refused(monkeypatch):
    moved = []
    monkeypatch.setattr(cc, "pyautogui", SimpleNamespace(moveTo=lambda x, y, duration=0: moved.append((x, y))))
    monkeypatch.setattr(ds, "virtual_desktop", lambda: (0, 0, 1000, 800))
    assert "outside every screen" in cc._place_pointer(1500, 10) and moved == []
    monkeypatch.setattr(ds, "cursor", lambda: (300, 200))
    assert "scaling" in cc._place_pointer(600, 400)
    assert cc._place_pointer(300, 200) == ""


# ── open_app ────────────────────────────────────────────────────────────────

def _desk(monkeypatch, wins, fg=None, procs=None):
    monkeypatch.setattr(ds, "windows", lambda: wins)
    monkeypatch.setattr(ds, "foreground", lambda: fg)
    monkeypatch.setattr(ds, "processes", lambda: procs or {})
    monkeypatch.setattr(ds, "is_me", lambda pid: False)
    monkeypatch.setattr(oa, "_SYSTEM", "Windows")


def test_open_app_is_verified_by_a_new_window_of_that_app(monkeypatch):
    before = {"windows": {1}, "fg": 1, "pids": {10}}
    _desk(monkeypatch, [Win(1, 10, "explorer", "Files"), Win(2, 11, "notepad", "Untitled - Notepad")],
          fg={"hwnd": 2, "title": "Untitled - Notepad"})
    v = oa._verify({"app_name": "notepad"}, "Opened notepad.", before)
    assert v.state == verify.VERIFIED and "Untitled - Notepad" in v.detail


def test_open_app_store_window_counts_with_its_process(monkeypatch):
    before = {"windows": {1}, "fg": 1, "pids": {10}}
    _desk(monkeypatch, [Win(1, 10, "explorer", "Files"),
                        Win(3, 12, "applicationframehost", "Калькулятор")],
          procs={10: "explorer", 12: "applicationframehost", 13: "calculatorapp"})
    v = oa._verify({"app_name": "calculator"}, "", before)
    assert v.state == verify.VERIFIED and "Калькулятор" in v.detail


def test_open_app_with_nothing_new_failed(monkeypatch):
    before = {"windows": {1}, "fg": 1, "pids": {10}}
    _desk(monkeypatch, [Win(1, 10, "explorer", "Files")], fg={"hwnd": 1, "title": "Files"},
          procs={10: "explorer"})
    v = oa._verify({"app_name": "spotify"}, "", before)
    assert v.state == verify.FAILED


def test_open_app_ignores_lookalike_processes():
    # "task" alone matched taskhostw.exe, which Windows starts all the time.
    assert set(oa._proc_words("Task Manager")) == {"taskmgr", "taskmanager"}
    assert set(oa._proc_words("word")) == {"winword", "word"} or oa._SYSTEM != "Windows"


# ── computer_settings ───────────────────────────────────────────────────────

def test_settings_resolve_the_action_like_the_handler():
    assert cs._resolved({"description": "set volume to 30"}) == ("volume_set", 30)
    assert cs._resolved({"action": "Snap Left"})[0] == "snap_left"


def test_volume_is_read_back(monkeypatch):
    monkeypatch.setattr(cs, "volume_get", lambda: 30)
    v = cs._verify({"action": "volume_set", "value": 30}, "Volume set to 30%.", {"volume": 50})
    assert v.state == verify.VERIFIED
    v = cs._verify({"action": "volume_set", "value": 70}, "Volume set to 70%.", {"volume": 50})
    assert v.state == verify.FAILED and "30%" in v.detail
    v = cs._verify({"action": "volume_up"}, "Done.", {"volume": 30})
    assert v.state == verify.FAILED
    monkeypatch.setattr(cs, "volume_get", lambda: None)
    assert cs._verify({"action": "volume_up"}, "", {"volume": None}).state == verify.UNVERIFIED


def test_mute_means_muted_not_toggled(monkeypatch):
    state = {"m": True}
    monkeypatch.setattr(cs, "mute_get", lambda: state["m"])
    assert cs._verify({"action": "mute"}, "", {"muted": True}).state == verify.VERIFIED
    assert cs._verify({"action": "unmute"}, "", {"muted": True}).state == verify.FAILED
    calls = []
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr(cs, "_endpoint_volume", lambda: SimpleNamespace(
        SetMute=lambda on, ctx: calls.append(on)))
    cs.ACTION_MAP["mute"]()
    cs.ACTION_MAP["unmute"]()
    assert calls == [1, 0]


def test_new_pycaw_endpoint_is_used(monkeypatch):
    ev = SimpleNamespace(GetMasterVolumeLevelScalar=lambda: 0.4)
    fake = SimpleNamespace(AudioUtilities=SimpleNamespace(GetSpeakers=lambda: SimpleNamespace(EndpointVolume=ev)),
                           IAudioEndpointVolume=object)
    monkeypatch.setitem(__import__("sys").modules, "pycaw.pycaw", fake)
    monkeypatch.setattr(cs, "_OS", "Windows")
    assert cs.volume_get() == 40


def test_window_actions_read_the_window_state(monkeypatch):
    monkeypatch.setattr(cs, "_OS", "Windows")
    fg0 = {"hwnd": 4, "title": "Notes", "rect": (0, 0, 800, 600)}
    monkeypatch.setattr(ds, "window", lambda h: dict(fg0, minimized=False, maximized=False, visible=True))
    monkeypatch.setattr(ds, "foreground", lambda: fg0)
    assert cs._verify({"action": "minimize"}, "", {"fg": fg0}).state == verify.FAILED
    assert cs._verify({"action": "switch_window"}, "", {"fg": fg0}).state == verify.FAILED
    monkeypatch.setattr(ds, "window", lambda h: dict(fg0, minimized=True, maximized=False, visible=True))
    assert cs._verify({"action": "minimize"}, "", {"fg": fg0}).state == verify.VERIFIED


def test_a_pending_confirmation_is_not_checked():
    assert cs._verify({"action": "shutdown"}, "[CONFIRMATION_PENDING] press it", {}) is None


# ── file_controller ─────────────────────────────────────────────────────────

def test_file_changes_are_read_back_from_disk(tmp_path):
    p = {"action": "write", "path": str(tmp_path), "name": "a.txt", "content": "hi"}
    before = fc._snapshot(p)
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    assert fc._verify(p, "Written", before).state == verify.VERIFIED
    (tmp_path / "a.txt").write_text("other", encoding="utf-8")
    assert fc._verify(p, "Written", before).state == verify.FAILED

    p = {"action": "move", "path": str(tmp_path), "name": "a.txt", "destination": str(tmp_path / "sub")}
    (tmp_path / "sub").mkdir()
    before = fc._snapshot(p)
    assert fc._verify(p, "Moved", before).state == verify.FAILED
    (tmp_path / "a.txt").rename(tmp_path / "sub" / "a.txt")
    assert fc._verify(p, "Moved", before).state == verify.VERIFIED

    p = {"action": "delete", "path": str(tmp_path / "sub"), "name": "a.txt"}
    before = fc._snapshot(p)
    assert fc._verify(p, "Moved to Trash", before).state == verify.FAILED
    (tmp_path / "sub" / "a.txt").unlink()
    assert fc._verify(p, "Moved to Trash", before).state == verify.VERIFIED
    assert fc._verify({"action": "list"}, "x", None) is None


# ── browser_control ─────────────────────────────────────────────────────────

def test_native_navigation_is_verified_by_the_address_bar(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Windows")
    before = {"native": {7: "Old tab - Google Chrome"}, "fg": 9}
    monkeypatch.setattr(ds, "windows", lambda: [Win(7, 70, "chrome", "YouTube - Google Chrome")])
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 7})
    monkeypatch.setattr(ds, "address_bar", lambda h: "www.youtube.com/")
    v = bc._verify({"action": "go_to", "url": "youtube.com"}, "Opened", before)
    assert v.state == verify.VERIFIED and "youtube.com" in v.detail
    monkeypatch.setattr(ds, "address_bar", lambda h: "news.example.org/")
    v = bc._verify({"action": "go_to", "url": "youtube.com"}, "Opened", before)
    assert v.state == verify.UNVERIFIED


class _Sess:
    def __init__(self, after):
        self.after = after
        self._page = None

    def run(self, coro, timeout=60):
        coro.close()
        return self.after


def test_page_actions_compare_before_and_after(monkeypatch):
    b = {"url": "https://a.test/", "title": "A", "text_hash": 1, "scroll": 0, "pages": 1,
         "active": "BODY#", "value": None, "t0": 1.0}
    cases = [
        ("click", dict(b), verify.UNVERIFIED),
        ("click", dict(b, url="https://a.test/next"), verify.VERIFIED),
        ("click", dict(b, text_hash=2), verify.VERIFIED),
        ("reload", dict(b, t0=2.0), verify.VERIFIED),
        ("scroll", dict(b, scroll=500), verify.VERIFIED),
        ("close_tab", dict(b), verify.FAILED),
    ]
    for action, after, want in cases:
        bc._tl.before = (_Sess(after), dict(b))
        v = bc._verify({"action": action}, "done", {})
        assert v.state == want, (action, v)
    bc._tl.before = (_Sess(dict(b, value="hello there")), dict(b))
    assert bc._verify({"action": "type", "text": "hello"}, "", {}).state == verify.VERIFIED
    bc._tl.before = (_Sess(dict(b, url="https://other.test/")), dict(b))
    v = bc._verify({"action": "go_to", "url": "a.test"}, "", {})
    assert v.state == verify.FAILED


def test_hosts_and_targets():
    assert bc._host("https://www.YouTube.com/watch?v=1") == "youtube.com"
    assert bc._host("192.168.1.5:8080/x") == "192.168.1.5"
    assert bc._nav_target({"action": "search", "query": "a b"}).startswith("https://www.google.com/search?q=a+b")


# ── fixes from the first live test (2026-10-16) ─────────────────────────────

def test_a_click_without_a_point_never_clicks_where_the_mouse_is(monkeypatch):
    clicked, routed = [], []
    monkeypatch.setattr(cc, "_click", lambda *a: clicked.append(a))
    monkeypatch.setattr(cc, "_screen_click", lambda p: routed.append(p) or "Clicked 'John'.")
    out = cc.computer_control({"action": "click", "description": "John Smith"})
    assert out == "Clicked 'John'." and clicked == []
    assert routed[0]["action"] == "screen_click" and routed[0]["button"] == "left"
    assert cc.computer_control({"action": "click"}).startswith("Could not click")
    assert clicked == []


def test_the_key_alias_presses(monkeypatch):
    pressed = []
    monkeypatch.setattr(cc, "_compose_target", lambda: None)
    monkeypatch.setattr(cc, "_press", lambda k: pressed.append(k) or f"Pressed: {k}")
    assert cc.computer_control({"action": "key", "key": "enter"}) == "Pressed: enter"
    assert pressed == ["enter"]


def test_enter_that_empties_the_field_was_a_submit(monkeypatch):
    sent = F(name="Message to John", value="")
    monkeypatch.setattr(ds, "focused_control", lambda: sent)
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 1, "title": "Slack"})
    before = {"fg": {"hwnd": 1, "title": "Slack"}, "region": None,
              "focus": F(name="Message to John", value="Привіт")}
    v = cc._verify({"action": "press", "key": "enter"}, "", before)
    assert v.state == verify.VERIFIED and "submitted" in v.detail
    monkeypatch.setattr(ds, "focused_control", lambda: F(name="Message to John", value="Привіт"))
    assert cc._verify({"action": "press", "key": "enter"}, "", before).state == verify.FAILED


def test_ambiguous_names_come_back_with_where_they_are(monkeypatch):
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 1})
    monkeypatch.setattr(ds, "taskbar", lambda: 0)
    monkeypatch.setattr(ds, "window", lambda h: {"pid": 9})
    monkeypatch.setattr(ds, "is_me", lambda pid: False)
    monkeypatch.setattr(ds, "controls", lambda h: [
        {"name": "John Smith", "type": 50024, "rect": (0, 100, 200, 120), "enabled": True},
        {"name": "John Smith", "type": 50000, "rect": (800, 10, 900, 30), "enabled": True}])
    found, why = cc._uia_find("John Smith")
    assert found is None and "tree item at 100, 110" in why and "button at 850, 20" in why


def test_window_actions_bring_the_named_window_first(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr("actions.computer_control._focus_window_win",
                        lambda t: calls.append(("focus", t)) or f"Focused window: {t}")
    monkeypatch.setitem(cs.ACTION_MAP, "minimize", lambda: calls.append("minimize"))
    out = cs.computer_settings({"action": "minimize", "value": "Outlook"})
    assert calls == [("focus", "Outlook"), "minimize"] and out == "Done: minimize."
    calls.clear()
    monkeypatch.setattr("actions.computer_control._focus_window_win",
                        lambda t: "Could not find an open window matching 'Nope'.")
    assert cs.computer_settings({"action": "minimize", "value": "Nope"}).startswith("Could not")
    assert calls == []
    assert cs._window_target("volume_set", "30") == "" and cs._window_target("maximize", "5") == ""


def test_open_app_brings_a_running_app_forward(monkeypatch):
    focused = []
    monkeypatch.setattr(oa, "_SYSTEM", "Windows")
    monkeypatch.setattr(ds, "windows", lambda: [Win(3, 30, "claude", "Claude")])
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 9})
    monkeypatch.setattr(ds, "is_me", lambda pid: False)
    monkeypatch.setattr("actions.computer_control._focus_window_win", lambda t: focused.append(t))
    oa._to_front("Claude")
    assert focused == ["Claude"]
    monkeypatch.setattr(ds, "foreground", lambda: {"hwnd": 3})
    oa._to_front("Claude")
    assert focused == ["Claude"]


def test_open_app_keeps_a_settings_page_link():
    assert oa._normalize("ms-settings:nightlight") == "ms-settings:nightlight"
    assert oa._normalize("Chrome") != "Chrome" or oa._SYSTEM not in ("Windows", "Darwin", "Linux")


def test_emptying_the_recycle_bin_waits_for_the_hud(monkeypatch):
    from core import confirm
    shown = []
    monkeypatch.setattr(confirm, "_show_cb", lambda t, d: shown.append(t))
    monkeypatch.setattr(confirm, "_pending", None)
    monkeypatch.setattr(fc, "trash_info", lambda: (149, 1_128_326_141))
    emptied = []
    monkeypatch.setattr(fc, "_empty_trash_now", lambda: emptied.append(1) or "The Recycle Bin is empty now.")
    out = fc.file_controller({"action": "empty_trash"})
    assert out.startswith("[CONFIRMATION_PENDING]") and emptied == []
    assert "149 item(s)" in shown[0]
    assert confirm._pending.run() == "The Recycle Bin is empty now." and emptied == [1]
    monkeypatch.setattr(fc, "trash_info", lambda: (0, 0))
    monkeypatch.setattr(confirm, "_pending", None)
    assert "already empty" in fc.file_controller({"action": "empty_trash"})


def test_delete_keys_are_risky_in_computer_use(monkeypatch):
    from core import computer_use as cu
    monkeypatch.setattr(cc, "_compose_target", lambda: None)
    assert "Delete" in cu._risk({"do": "press", "key": "delete"}, "")
    assert cu._risk({"do": "hotkey", "keys": "shift+delete"}, "")
