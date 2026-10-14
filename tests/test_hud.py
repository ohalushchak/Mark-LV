"""The HUD centrepieces: what they react to, that they paint, and the toggle
that cycles between them. Headless — Qt's offscreen platform, no window."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from core.orb_hud import OrbHud, build_mesh

_app = QApplication.instance() or QApplication([])

MAIN, ACC, BG = QColor("#00d4ff"), QColor("#ff6b00"), QColor("#00060a")


def _paint(obj, size=360, r=120):
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(BG)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    obj.paint(p, size / 2, size / 2, r, MAIN, ACC, BG)
    p.end()
    return img


def _lit(img):
    """Pixels that differ from the background, sampled on a grid."""
    bg = BG.rgb()
    return sum(img.pixel(x, y) != bg
               for x in range(0, img.width(), 4) for y in range(0, img.height(), 4))


# ── the orb ──────────────────────────────────────────────────────────────────

def test_geodesic_mesh():
    verts, edges = build_mesh(1)
    assert (len(verts), len(edges)) == (42, 120)
    assert all(abs(x * x + y * y + z * z - 1.0) < 1e-9 for x, y, z in verts)
    assert len(build_mesh(0)[1]) == 30


def test_spin_reads_the_state():
    rate = lambda **k: OrbHud.spin_rate(**{"speaking": False, "muted": False,
                                           "state": "LISTENING", "amp": 0.0, **k})
    assert rate(muted=True, state="THINKING") < rate() < rate(speaking=True) < rate(state="THINKING")


def test_speaking_comes_from_the_flag_not_the_state_text():
    orb = OrbHud()
    orb.step(0.016, 0.0, speaking=True, state="LISTENING")
    assert orb._live
    orb.step(0.016, 0.0, speaking=False, state="LISTENING")
    assert not orb._live


def test_voice_swells_the_orb_and_mute_silences_it():
    loud, muted = OrbHud(), OrbHud()
    for _ in range(60):
        loud.step(0.016, 0.8, speaking=True, state="SPEAKING")
        muted.step(0.016, 0.8, speaking=True, muted=True, state="SPEAKING")
    assert loud._pulse > 1.08
    assert muted._pulse == pytest.approx(1.0) and not muted._live


def test_the_orb_paints():
    orb = OrbHud()
    orb.step(0.016, 0.5, speaking=True, state="SPEAKING")
    assert _lit(_paint(orb)) > 500
    assert _lit(_paint(orb, r=4)) == 0            # too small to draw: skipped


# ── the faces ────────────────────────────────────────────────────────────────

def _settle(face, **state):
    for _ in range(200):
        face.step(0.05, 0.0, **state)
    return face


def test_the_face_smiles_at_rest_and_not_while_thinking():
    from core.avatar import HoloAvatar
    face = HoloAvatar()
    assert _settle(face, state="LISTENING")._smile > 0.5
    assert _settle(face, state="THINKING")._smile < 0.05
    assert _settle(face, state="SLEEPING")._lids < 0.4          # heavy lids


def test_the_anime_face_shares_the_animation_and_paints():
    from core.anime_avatar import AnimeAvatar
    from core.avatar import HoloAvatar
    face = AnimeAvatar()
    assert isinstance(face, HoloAvatar) and face.SPAN == AnimeAvatar.SPAN_ANIME
    face.step(0.016, 0.6, speaking=True, state="SPEAKING", v_open=0.8)
    assert _lit(_paint(face)) > 500


# ── the canvas and the toggle ────────────────────────────────────────────────

@pytest.fixture
def canvas():
    import ui
    c = ui.HudCanvas("", "J.A.R.V.I.S")
    c._tmr.stop()
    c.resize(420, 520)
    yield c
    c.deleteLater()


@pytest.mark.parametrize("style", ["face", "core", "orb", "anime"])
def test_every_style_steps_and_renders(canvas, style):
    from PyQt6.QtGui import QPixmap
    canvas.hud_style = style
    canvas.speaking, canvas.state = True, "SPEAKING"
    for _ in range(5):
        canvas._live_amp = 0.6
        canvas._step()
    pm = QPixmap(canvas.size())
    canvas.render(pm)
    assert _lit(pm.toImage()) > 500


def test_toggle_cycles_and_skips_what_failed_to_load(canvas, monkeypatch):
    import ui
    from memory import config_manager as cm
    saved = {"style": "face"}
    monkeypatch.setattr(cm, "get_hud_style", lambda: saved["style"])
    monkeypatch.setattr(cm, "save_hud_style", lambda s: saved.update(style=s))
    logs = []
    win = SimpleNamespace(hud=canvas, _HUD_STYLE_TEXT=ui.MainWindow._HUD_STYLE_TEXT,
                          _refresh_hud_btn=lambda: None,
                          _log=SimpleNamespace(append_log=logs.append))
    toggle = lambda: ui.MainWindow._toggle_hud_style(win)

    seen = []
    for _ in range(len(cm.HUD_STYLES)):
        toggle()
        seen.append(saved["style"])
    assert seen == list(cm.HUD_STYLES[1:]) + [cm.HUD_STYLES[0]]
    assert canvas.hud_style == saved["style"] and logs[-1].startswith("SYS: HUD switched to")

    canvas._orb = None                            # the orb module failed to load
    saved["style"] = "core"
    toggle()
    assert saved["style"] == "anime"


# ── theme and type ───────────────────────────────────────────────────────────

@pytest.fixture
def neon_after():
    """Theme functions change module-level colours; put neon back."""
    import ui
    yield ui
    ui.apply_ui_theme("neon")


def test_soft_theme_stays_soft_in_any_hue(neon_after):
    ui = neon_after
    from PyQt6.QtGui import QColor
    neon_sat = QColor(ui.C.PRI).hsvSaturationF()
    assert ui.apply_ui_theme("soft") and ui.C.BG == "#14181e"
    assert ui.C.ACC == "#d9955f"                       # status colours too
    ui.apply_ui_accent("#ff0040")                      # a loud accent on top
    pri = QColor(ui.C.PRI)
    assert abs(pri.hsvHueF() - QColor("#ff0040").hsvHueF()) < 0.02
    assert pri.hsvSaturationF() < neon_sat * 0.7       # still muted
    assert not ui.apply_ui_theme("nope")


def test_live_retheme_swaps_every_themed_colour_once(neon_after):
    ui = neon_after
    from PyQt6.QtWidgets import QLabel
    lbl = QLabel("x")
    lbl.setStyleSheet(f"color: {ui.C.PRI}; background: {ui.C.BG}; "
                      f"border: 1px solid {ui.C.GREEN}; outline: #00d4ff80;")
    old = ui.current_palette()
    ui.apply_ui_theme("soft")
    ui.retheme_all_widgets(old, ui.current_palette())
    ss = lbl.styleSheet()
    assert "#6fb4c8" in ss and "#14181e" in ss and "#6cc4a0" in ss
    assert "#00d4ff80" in ss                           # not a palette colour
    lbl.deleteLater()


def test_fonts_follow_the_text_size(monkeypatch):
    import ui
    monkeypatch.setattr(ui, "_UI_SCALE", 1.5)
    assert ui._font(8).pointSizeF() == pytest.approx(12.0)
    assert ui._font(8).family() == ui._MONO
    monkeypatch.setattr(ui, "_UI_SCALE", 1.0)
    assert ui._font(2).pointSizeF() == 5.0             # floor


def test_text_size_setting_snaps_to_a_step(monkeypatch):
    from memory import config_manager as cm
    for raw, want in ((1.2, 1.15), ("1.9", 1.5), ("junk", 1.0), (None, 1.0)):
        monkeypatch.setattr(cm, "load_api_keys", lambda raw=raw: {"ui_scale": raw})
        assert cm.get_ui_scale() == want
    monkeypatch.setattr(cm, "load_api_keys", lambda: {"ui_theme": "SOFT"})
    assert cm.get_ui_theme() == "soft"


def test_mix_hex_and_glass():
    import ui
    assert ui._mix_hex("#000000", "#ffffff", 0.5) == "#808080"
    assert ui._glass("#14181e", 245) == "rgba(20, 24, 30, 245)"


# ── captions and the desktop orb ─────────────────────────────────────────────

def test_captions_show_the_conversation_and_nothing_else():
    import ui
    cap = ui.ReplyCaption("Friday")
    cap.show_line("SYS: Wake word ready.")
    cap.show_line("ERR: something")
    assert cap.toPlainText() == ""
    cap.show_line("You: what time is it")
    assert cap.toPlainText() == "You  ·  what time is it"
    answer = "It is half past nine. " * 40                 # a long answer
    cap.show_line(f"Friday: {answer}")
    steps = 0
    while cap._tmr.isActive() and steps < 1000:
        cap._type_step()
        steps += 1
    assert steps <= ui.ReplyCaption._MAX_STEPS + 1         # bounded, not a crawl
    assert cap.toPlainText() == f"FRIDAY  ·  {answer.strip()}"
    cap.deleteLater()


def _owner(speaking=True, muted=False, minimised=False, active=True):
    hud = SimpleNamespace(speaking=speaking, muted=muted, state="SPEAKING",
                          _amp_disp=0.5)
    return SimpleNamespace(hud=hud, isMinimized=lambda: minimised,
                           isVisible=lambda: not minimised,
                           isActiveWindow=lambda: active)


@pytest.mark.parametrize("mode,owner,want", [
    ("speaking", _owner(active=True), False),               # HUD in front
    ("speaking", _owner(active=False), True),
    ("speaking", _owner(minimised=True, active=False), True),
    ("speaking", _owner(speaking=False, active=False), False),
    ("speaking", _owner(muted=True, active=False), False),
    ("always", _owner(speaking=False, active=False), True),
    ("always", _owner(muted=True, active=False), True),     # shows it is muted
    ("always", _owner(speaking=False, active=True), False),
    ("off", _owner(active=False), False),
])
def test_desktop_orb_follows_its_mode_and_only_when_the_hud_is_out_of_sight(
        mode, owner, want):
    import ui
    orb = ui.DesktopOrb(owner)
    orb.mode = mode
    assert orb.wanted() is want
    orb.deleteLater()


def test_desktop_orb_mode_setting(monkeypatch):
    from memory import config_manager as cm
    for raw, want in (("ALWAYS", "always"), ("off", "off"), ("junk", "speaking"),
                      (None, "speaking")):
        monkeypatch.setattr(cm, "load_api_keys", lambda raw=raw: {"desktop_orb_mode": raw})
        assert cm.get_desktop_orb_mode() == want
