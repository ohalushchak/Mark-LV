"""Compact mode: the card, its confirm gate, dragging and placement, and the
switch between it and the HUD. Headless — Qt's offscreen platform."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui                                             # noqa: E402
from memory import config_manager as cm               # noqa: E402


def _owner(**hud):
    calls = []
    h = SimpleNamespace(muted=False, speaking=False, state="LISTENING", _amp_disp=0.0,
                        _core_colours=lambda: (QColor("#00d4ff"), QColor("#00ff88")))
    h.__dict__.update(hud)
    o = SimpleNamespace(hud=h, _assistant_name="Jarvis", calls=calls,
                        exit_compact=lambda: calls.append("hud"),
                        _toggle_mute=lambda: calls.append("mute"),
                        _do_interrupt=lambda: calls.append("stop"),
                        quit_app=lambda: calls.append("quit"),
                        _on_confirm_answered=lambda ok: calls.append(("confirm", ok)))
    return o


@pytest.fixture
def card(monkeypatch):
    saved = {}
    monkeypatch.setattr(cm, "save_compact_pos", lambda x, y: saved.update(pos=(x, y)))
    monkeypatch.setattr(cm, "get_compact_pos", lambda: saved.get("pos"))
    c = ui.CompactCard(_owner())
    c.saved = saved
    yield c
    c.deleteLater()


def test_the_state_line_follows_the_hud(card):
    hud = card._owner.hud
    for change, text in (({}, "LISTENING"), ({"speaking": True}, "SPEAKING"),
                         ({"speaking": False, "state": "THINKING"}, "THINKING"),
                         ({"muted": True}, "MUTED")):
        hud.__dict__.update(change)
        card.refresh_state()
        assert text in card._state.text()
    assert card._mic_btn.text().endswith("UNMUTE")


def test_the_buttons_call_the_main_window(card):
    card._hud_btn.click(); card._mic_btn.click(); card._stop_btn.click()
    assert card._owner.calls == ["hud", "mute", "stop"]


def test_the_card_shows_the_conversation(card):
    card.caption.show_line("SYS: noise")
    card.caption.show_line("You: hello")
    assert card.caption.toPlainText() == "You  ·  hello"


def _mouse(card, kind, gx, gy, buttons=Qt.MouseButton.LeftButton):
    g = QPointF(gx, gy)
    ev = QMouseEvent(kind, g - QPointF(card.pos()), g, Qt.MouseButton.LeftButton
                     if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                     buttons, Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: card.mousePressEvent,
     QEvent.Type.MouseMove: card.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: card.mouseReleaseEvent}[kind](ev)


def test_a_small_wobble_is_a_click_and_a_real_drag_is_saved(card):
    card.move(100, 100)
    _mouse(card, QEvent.Type.MouseButtonPress, 150, 120)
    _mouse(card, QEvent.Type.MouseMove, 152, 121)        # 3 px: still a click
    assert card.pos() == QPoint(100, 100)
    _mouse(card, QEvent.Type.MouseMove, 190, 160)
    _mouse(card, QEvent.Type.MouseButtonRelease, 190, 160, Qt.MouseButton.NoButton)
    assert card.pos() == QPoint(140, 140) and card.saved["pos"] == (140, 140)


def test_the_card_stays_on_a_screen(card):
    a = QGuiApplication.primaryScreen().availableGeometry()
    card.adjustSize()
    x, y = card._clamp(a.right() + 5000, a.bottom() + 5000,
                       QGuiApplication.primaryScreen())
    assert x + card.width() - 1 <= a.right() and y + card.height() - 1 <= a.bottom()
    card.saved["pos"] = (-90000, -90000)                 # a monitor that is gone
    card.place()
    assert a.contains(card.pos())


def test_compact_settings(monkeypatch):
    for raw, want in (([10, -20], (10, -20)), (["3", "4"], (3, 4)), (None, None),
                      ("junk", None), ([1], None)):
        monkeypatch.setattr(cm, "load_api_keys", lambda raw=raw: {"compact_pos": raw})
        assert cm.get_compact_pos() == want
    monkeypatch.setattr(cm, "load_api_keys", lambda: {})
    assert cm.get_compact_mode() is False


def test_entering_compact_hides_the_hud_but_not_over_a_pending_gate(monkeypatch):
    monkeypatch.setattr(cm, "save_compact_mode", lambda v: None)
    shown, logs = [], []
    fake_card = SimpleNamespace(place=lambda: None, show=lambda: shown.append("card"),
                                raise_=lambda: None)
    win = SimpleNamespace(_ready=True, _compact=fake_card, _confirm_overlay=None,
                          hide=lambda: shown.append("hud hidden"),
                          _log_sig=SimpleNamespace(emit=logs.append))
    ui.MainWindow.enter_compact(win)
    assert shown == ["card", "hud hidden"]

    shown.clear()
    win._confirm_overlay = object()                     # a gate is waiting on the HUD
    ui.MainWindow.enter_compact(win)
    assert shown == [] and "confirmation" in logs[-1]

    win._confirm_overlay, win._ready = None, False      # first-run setup needs the HUD
    ui.MainWindow.enter_compact(win)
    assert shown == []


def test_a_confirm_gate_brings_the_hud_back(monkeypatch):
    monkeypatch.setattr(ui, "ConfirmBanner", lambda *a, **k: SimpleNamespace(
        answered=SimpleNamespace(connect=lambda f: None)))
    calls = []
    win = SimpleNamespace(_confirm_overlay=None, centralWidget=lambda: None,
                          _hide_confirm_banner=lambda: None,
                          _centre_overlay=lambda ov: None,
                          _on_confirm_answered=lambda ok: None,
                          _compact=SimpleNamespace(isVisible=lambda: True),
                          exit_compact=lambda: calls.append("hud"))
    ui.MainWindow._show_confirm_banner(win, "Shut down?", "")
    assert calls == ["hud"] and win._confirm_overlay is not None


def test_the_desktop_orb_steps_aside_for_the_card():
    owner = _owner(speaking=True)
    owner.isMinimized = lambda: True
    owner.isVisible = lambda: False
    owner.isActiveWindow = lambda: False
    orb = ui.DesktopOrb(owner)
    assert orb.wanted()
    owner._compact = SimpleNamespace(isVisible=lambda: True)
    assert not orb.wanted()
    orb.deleteLater()


# ── tray ─────────────────────────────────────────────────────────────────────

def test_tray_tooltip_carries_the_state_and_the_latest_reply():
    from PyQt6.QtWidgets import QSystemTrayIcon
    owner = _owner(speaking=True)
    owner._compact = SimpleNamespace(isVisible=lambda: False)
    owner._desk_orb = SimpleNamespace(mode="always")
    owner.toggle_quick_prompt = owner.enter_compact = owner.set_desktop_orb_mode = \
        lambda *a: None
    tray = ui.JarvisTray(owner)
    if tray.tray is None:
        assert not QSystemTrayIcon.isSystemTrayAvailable()
        tray.refresh(); tray.show_line("Jarvis: hi"); tray.hide()   # all no-ops
        pytest.skip("no system tray on this platform")
    assert tray.tray.toolTip() == "JARVIS — Speaking"
    tray.show_line("SYS: ignored")
    tray.show_line("Jarvis: " + "word " * 60)
    tip = tray.tray.toolTip()
    assert tip.startswith("JARVIS — Speaking\nword") and len(tip) <= 127
    owner.hud.muted = True
    tray.refresh(); tray._sync_menu()
    assert tray._mute.isChecked() and tray._orb_modes["always"].isChecked()
    assert "Microphone muted" in tray.tray.toolTip()
    tray.hide()


# ── quick prompt ─────────────────────────────────────────────────────────────

def test_quick_prompt_sends_trimmed_text_and_closes():
    sent = []
    owner = SimpleNamespace(_assistant_name="Jarvis", submit_text=sent.append)
    qp = ui.QuickPrompt(owner)
    qp.show()
    qp.edit.setText("   what's the time   ")
    qp._submit()
    assert sent == ["what's the time"] and not qp.isVisible() and qp.edit.text() == ""
    qp.show()
    qp.edit.setText("   ")
    qp._submit()
    assert sent == ["what's the time"]                  # nothing typed, nothing sent
    qp.toggle()
    assert qp.isVisible()
    qp.toggle()
    assert not qp.isVisible()
    qp.deleteLater()


def test_typed_text_goes_through_the_log_signal():
    logged = []
    win = SimpleNamespace(_log_sig=SimpleNamespace(emit=logged.append), on_text_command=None)
    ui.MainWindow.submit_text(win, "hello")
    assert logged == ["You: hello"]                     # so captions and card see it


def test_quick_prompt_key_setting(monkeypatch):
    for raw, want in (("f9", "F9"), ("ctrl+alt+j", "Ctrl+Alt+J"), ("OFF", "off"),
                      ("F1", "F9"), (None, "F9")):
        monkeypatch.setattr(cm, "load_api_keys", lambda raw=raw: {"quick_prompt_key": raw})
        assert cm.get_quick_prompt_key() == want


def test_chords_parse():
    from core.hotkey import chord_label, parse_chord
    assert parse_chord("Ctrl+Alt+J") == ("ctrl", "alt", "j")
    assert parse_chord("F9") == ("f9",)
    assert parse_chord("Hyper+J") == () and parse_chord("") == ()
    assert chord_label(("ctrl", "alt", "j")) == "Ctrl+Alt+J"


@pytest.mark.skipif(ui._OS != "Windows", reason="RegisterHotKey is Windows-only")
def test_a_global_hotkey_is_granted_released_and_refused_when_taken():
    from core.hotkey import GlobalHotkey
    # Any program on the machine may own a chord (Ctrl+Alt+K was taken on the
    # dev box between two runs), so use the first one that is free right now.
    first = second = None
    for chord in (("ctrl", "alt", "shift", "q"), ("ctrl", "alt", "f12"),
                  ("ctrl", "shift", "alt", "j"), ("ctrl", "alt", "q")):
        first = GlobalHotkey(lambda: None, chord)
        if first.start() == "global":
            break
        first.stop()
    else:
        pytest.skip("every candidate chord is owned by another program")
    second = GlobalHotkey(lambda: None, chord)
    try:
        assert second.start() == "window"                # another owner: refused
        first.stop()
        assert second.start() == "global"                # released on stop
    finally:
        first.stop(); second.stop()


def test_a_hotkey_off_windows_is_window_scoped(monkeypatch):
    import core.hotkey as hk
    monkeypatch.setattr(hk, "_OS", "Linux")
    assert hk.GlobalHotkey(lambda: None, ("f9",)).start() == "window"
