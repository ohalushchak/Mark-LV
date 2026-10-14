"""
core/anime_avatar.py — a hand-drawn anime face for the HUD.

Reuses every bit of HoloAvatar's animation (blink, lids, gaze, brows, smile,
visemes, sway) and only replaces the drawing: the same `step()` that moves the
holographic head moves this face, so lip sync and state expressions behave
identically. Pure QPainter vector art — no image files, no extra packages.

Ported from the madshadowtest/Mark-LV fork, where it was the default; here it
is the fourth HUD style and the holographic head stays the default. Hair, suit
and aura are mixed from the theme colours, so the hue wheel and the muted
colour still reach it; skin and line work are fixed, since a face tinted cyan
stops reading as a face.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPainterPath,
                         QPen, QRadialGradient)

from core.avatar import HoloAvatar, _blend, _c

_SKIN = QColor(255, 226, 210)
_SKIN_SHADE = QColor(240, 188, 172)
_LINE = QColor(92, 52, 52)
_MOUTH = QColor(150, 48, 62)
_TONGUE = QColor(236, 118, 128)
_BLUSH = QColor(255, 120, 140)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class AnimeAvatar(HoloAvatar):
    """Same lifecycle as HoloAvatar: step() once per tick, paint() per frame."""

    SPAN_ANIME = 2.3

    def __init__(self) -> None:
        super().__init__()
        self.SPAN = self.SPAN_ANIME

    # ── helpers ─────────────────────────────────────────────────────────────
    def _P(self, x: float, y: float) -> QPointF:
        return QPointF(self._ox + x * self._u, self._oy + y * self._u)

    def _path(self, pts, closed=True) -> QPainterPath:
        """Cubic path from (x, y) | ('c', c1x, c1y, c2x, c2y, x, y) entries."""
        path = QPainterPath(self._P(*pts[0]))
        for q in pts[1:]:
            if q[0] == "c":
                path.cubicTo(self._P(q[1], q[2]), self._P(q[3], q[4]), self._P(q[5], q[6]))
            else:
                path.lineTo(self._P(*q))
        if closed:
            path.closeSubpath()
        return path

    # ── drawing ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        if bg is None:
            bg = QColor(0, 0, 0)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        amp = self._glow

        # Aura, as on the hologram, so the face still belongs to the HUD.
        ar = r * 1.9
        grad = QRadialGradient(cx, cy, ar)
        grad.setColorAt(0.0, _c(primary, 40 + 60 * amp))
        grad.setColorAt(0.45, _c(primary, 18 + 30 * amp))
        grad.setColorAt(1.0, _c(primary, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - ar, cy - ar, ar * 2, ar * 2))

        # Head motion: the sway becomes a tilt and a small drift.
        self._u = r * 0.92
        self._ox = cx + self._yaw * r * 0.25
        self._oy = cy + 0.08 * r - self._pitch * r * 0.6
        tilt = math.degrees(self._yaw) * 0.18

        hair = _mix(_blend(bg, primary, 150), QColor(40, 50, 90), 0.45)
        hair_hi = _mix(hair, QColor(255, 255, 255), 0.35)
        hair_dk = _mix(hair, QColor(0, 0, 0), 0.40)

        self._bust(p, primary, accent, bg)
        p.translate(self._ox, self._oy)
        p.rotate(tilt)
        p.translate(-self._ox, -self._oy)
        self._back_hair(p, hair, hair_dk)
        self._face(p)
        self._blush(p)
        self._eyes(p, accent, amp)
        self._brows(p, hair_dk)
        self._nose_mouth(p)
        self._front_hair(p, hair, hair_hi, hair_dk, primary)
        p.restore()

    def _bust(self, p, primary, accent, bg):
        # Neck and a high-collared suit in the theme colour.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_SKIN_SHADE))
        p.drawPath(self._path([(-0.17, 0.70), (0.17, 0.70), (0.19, 1.22), (-0.19, 1.22)]))
        suit = QLinearGradient(self._P(0, 1.0), self._P(0, 1.45))
        suit.setColorAt(0.0, _blend(bg, primary, 120))
        suit.setColorAt(1.0, _blend(bg, primary, 40))
        p.setBrush(QBrush(suit))
        p.drawPath(self._path([(-0.20, 1.08), ("c", -0.45, 1.12, -0.85, 1.18, -1.05, 1.45),
                               (1.05, 1.45), ("c", 0.85, 1.18, 0.45, 1.12, 0.20, 1.08),
                               ("c", 0.12, 1.20, -0.12, 1.20, -0.20, 1.08)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(accent, 200), max(1.0, self._u * 0.012)))
        p.drawPath(self._path([(-0.20, 1.08), ("c", -0.10, 1.24, 0.10, 1.24, 0.20, 1.08)], False))
        p.setPen(QPen(_c(primary, 230), max(1.0, self._u * 0.008)))
        p.drawPath(self._path([(-0.62, 1.22), (-0.30, 1.30), (-0.22, 1.45)], False))
        p.drawPath(self._path([(0.62, 1.22), (0.30, 1.30), (0.22, 1.45)], False))

    def _back_hair(self, p, hair, hair_dk):
        g = QLinearGradient(self._P(0, -1.0), self._P(0, 1.2))
        g.setColorAt(0.0, hair)
        g.setColorAt(1.0, hair_dk)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(self._path([
            (0.0, -1.02),
            ("c", 0.62, -1.02, 0.98, -0.62, 0.95, -0.05),
            ("c", 0.94, 0.45, 0.98, 0.85, 0.88, 1.15),
            (0.55, 1.05), (0.62, 0.55),
            (-0.62, 0.55), (-0.55, 1.05), (-0.88, 1.15),
            ("c", -0.98, 0.85, -0.94, 0.45, -0.95, -0.05),
            ("c", -0.98, -0.62, -0.62, -1.02, 0.0, -1.02)]))

    def _face(self, p):
        face = self._path([
            (-0.70, -0.30),
            ("c", -0.72, 0.15, -0.60, 0.48, -0.36, 0.70),
            ("c", -0.20, 0.84, -0.08, 0.90, 0.0, 0.92),
            ("c", 0.08, 0.90, 0.20, 0.84, 0.36, 0.70),
            ("c", 0.60, 0.48, 0.72, 0.15, 0.70, -0.30),
            ("c", 0.66, -0.80, -0.66, -0.80, -0.70, -0.30)])
        g = QRadialGradient(self._P(-0.15, 0.0), self._u * 1.1)
        g.setColorAt(0.0, _SKIN)
        g.setColorAt(0.75, _SKIN)
        g.setColorAt(1.0, _SKIN_SHADE)
        p.setBrush(QBrush(g))
        p.setPen(QPen(_LINE, max(1.0, self._u * 0.010)))
        p.drawPath(face)

    def _blush(self, p):
        a = 70 + 90 * self._smile
        for sx in (-1, 1):
            c = self._P(sx * 0.42, 0.38)
            g = QRadialGradient(c, self._u * 0.16)
            g.setColorAt(0.0, _c(_BLUSH, a))
            g.setColorAt(1.0, _c(_BLUSH, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, self._u * 0.18, self._u * 0.10)

    def _eyes(self, p, accent, amp):
        open_ = max(0.0, min(1.0, (1.0 - self._blink) * self._lids))
        wide = 1.0 + 0.10 * max(0.0, self._brow)
        lw = max(1.2, self._u * 0.022)
        iris_c = _mix(QColor(70, 120, 235), accent, 0.30)
        for sx in (-1, 1):
            ex, ey = sx * 0.31, 0.10
            w, h = 0.185, 0.24 * wide

            def L(dx, dy, _ex=ex, _sx=sx):
                # Local eye coordinates; +dx always points to the outer corner.
                return (_ex + _sx * dx, ey + dy)

            def C(c1, c2, end):
                return ("c", *L(*c1), *L(*c2), *L(*end))

            if open_ < 0.30:
                # Closed: a soft arc, curved up when smiling (^ ^).
                k = 0.05 if self._smile > 0.3 else -0.03
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_LINE, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(self._path([L(-w, 0.04), C((-w * 0.4, 0.04 - 2 * k),
                                       (w * 0.4, 0.04 - 2 * k), (w, 0.04))], False))
                continue
            top = -h * open_
            bot = h * 0.85
            white = self._path([
                L(-w, 0.02),
                C((-w * 0.8, top), (w * 0.6, top - 0.02), (w * 1.02, top + 0.05)),
                C((w * 0.95, 0.10), (w * 0.6, bot), (0.0, bot)),
                C((-w * 0.6, bot), (-w * 0.95, 0.12), (-w, 0.02))])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255)))
            p.drawPath(white)

            p.save()
            p.setClipPath(white)
            ic = self._P(ex + self._gaze[0] * 0.05, ey + 0.06 + self._gaze[1] * 0.04)
            iw, ih = self._u * 0.118, self._u * 0.18
            g = QLinearGradient(QPointF(ic.x(), ic.y() - ih), QPointF(ic.x(), ic.y() + ih))
            g.setColorAt(0.0, _mix(iris_c, QColor(10, 10, 40), 0.65))
            g.setColorAt(0.55, iris_c)
            g.setColorAt(1.0, _mix(iris_c, QColor(255, 255, 255), 0.45 + 0.2 * amp))
            p.setBrush(QBrush(g))
            p.setPen(QPen(_mix(iris_c, QColor(0, 0, 0), 0.6), max(1.0, self._u * 0.008)))
            p.drawEllipse(ic, iw, ih)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_mix(iris_c, QColor(0, 0, 20), 0.8)))
            p.drawEllipse(ic, iw * 0.45, ih * 0.50)
            p.setBrush(QBrush(QColor(255, 255, 255, 240)))
            p.drawEllipse(QPointF(ic.x() - iw * 0.35, ic.y() - ih * 0.40), iw * 0.36, ih * 0.26)
            p.drawEllipse(QPointF(ic.x() + iw * 0.38, ic.y() + ih * 0.40), iw * 0.16, ih * 0.11)
            sh = QLinearGradient(self._P(0, ey + top), self._P(0, ey + top + 0.10))
            sh.setColorAt(0.0, QColor(60, 30, 60, 120))
            sh.setColorAt(1.0, QColor(60, 30, 60, 0))
            p.setBrush(QBrush(sh))
            p.drawPath(white)
            p.restore()

            # Upper lid: a heavy line with a lash flick at the outer corner.
            p.setBrush(QBrush(_LINE))
            p.setPen(QPen(_LINE, lw * 0.5))
            p.drawPath(self._path([
                L(-w * 1.05, 0.03),
                C((-w * 0.8, top - 0.01), (w * 0.6, top - 0.03), (w * 1.05, top + 0.04)),
                L(w * 1.30, top + 0.00),
                L(w * 1.08, top + 0.09),
                C((w * 0.6, top + 0.01), (-w * 0.7, top + 0.03), (-w * 0.95, 0.05))]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_c(_LINE, 170), lw * 0.45, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawPath(self._path([L(-w * 0.55, bot - 0.005),
                                   C((-w * 0.2, bot + 0.012), (w * 0.3, bot + 0.012),
                                     (w * 0.62, bot - 0.02))], False))

    def _brows(self, p, col):
        lift = -0.05 * self._brow
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, max(1.2, self._u * 0.020), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        for sx in (-1, 1):
            inner = 0.02 * max(0.0, -self._brow_bias) * 3  # concentration knits the brows
            p.drawPath(self._path([(sx * 0.18, -0.14 + lift + inner),
                                   ("c", sx * 0.28, -0.20 + lift, sx * 0.42, -0.21 + lift,
                                    sx * 0.52, -0.16 + lift)], False))

    def _nose_mouth(self, p):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(_LINE, 150), max(1.0, self._u * 0.012), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawPath(self._path([(0.015, 0.36), (-0.01, 0.40)], False))

        my = 0.60
        m = self._mouth
        w = 0.085 * (1.0 + 0.45 * self._wide) + 0.03 * self._smile
        curl = 0.035 * self._smile
        if m < 0.06:
            p.setPen(QPen(_LINE, max(1.2, self._u * 0.014), Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawPath(self._path([(-w, my - curl), ("c", -w * 0.4, my + curl * 0.8,
                                   w * 0.4, my + curl * 0.8, w, my - curl)], False))
            return
        h = 0.03 + 0.15 * m
        mouth = self._path([
            (-w, my - curl),
            ("c", -w * 0.5, my - 0.012, w * 0.5, my - 0.012, w, my - curl),
            ("c", w * 0.85, my + h * 0.9, w * 0.35, my + h, 0.0, my + h),
            ("c", -w * 0.35, my + h, -w * 0.85, my + h * 0.9, -w, my - curl)])
        p.setBrush(QBrush(_MOUTH))
        p.setPen(QPen(_LINE, max(1.0, self._u * 0.012)))
        p.drawPath(mouth)
        p.save()
        p.setClipPath(mouth)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawRect(QRectF(self._P(-w, my - 0.05), self._P(w, my + 0.012 + 0.02 * m)))
        p.setBrush(QBrush(_TONGUE))
        p.drawEllipse(self._P(0.0, my + h * 1.05), self._u * w * 0.75, self._u * h * 0.55)
        p.restore()

    def _front_hair(self, p, hair, hair_hi, hair_dk, primary):
        # Bangs: a row of pointed locks over the forehead plus two side locks.
        g = QLinearGradient(self._P(0, -1.0), self._P(0, 0.0))
        g.setColorAt(0.0, hair_hi)
        g.setColorAt(0.6, hair)
        g.setColorAt(1.0, hair_dk)
        p.setBrush(QBrush(g))
        p.setPen(QPen(hair_dk, max(1.0, self._u * 0.010)))
        bangs = [
            (-0.80, -0.20),
            ("c", -0.90, -0.80, -0.40, -1.05, 0.05, -1.02),
            ("c", 0.55, -1.02, 0.92, -0.75, 0.82, -0.18),
            (0.70, -0.30), (0.62, -0.02), (0.50, -0.30), (0.36, -0.10),
            (0.28, -0.36), (0.12, -0.12), (0.02, -0.40), (-0.12, -0.10),
            (-0.22, -0.38), (-0.36, -0.08), (-0.46, -0.34), (-0.60, 0.00),
            (-0.66, -0.28)]
        p.drawPath(self._path(bangs))
        for sx in (-1, 1):
            p.drawPath(self._path([
                (sx * 0.62, -0.40),
                ("c", sx * 0.80, -0.05, sx * 0.80, 0.40, sx * 0.72, 0.78),
                (sx * 0.66, 0.55),
                ("c", sx * 0.66, 0.20, sx * 0.62, -0.05, sx * 0.55, -0.25)]))
        # Shine band.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 70)))
        p.drawPath(self._path([(-0.50, -0.70), ("c", -0.20, -0.80, 0.25, -0.80, 0.52, -0.70),
                               ("c", 0.25, -0.74, -0.20, -0.74, -0.50, -0.64)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(primary, 160), max(1.0, self._u * 0.010)))
        p.drawPath(self._path([(-0.55, -0.86), ("c", -0.25, -1.00, 0.25, -1.00, 0.55, -0.86)], False))
