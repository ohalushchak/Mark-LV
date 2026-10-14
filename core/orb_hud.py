"""
The wireframe orb — a third HUD centrepiece beside the holographic head and
the reactor core.

Ported from the Kennycen/Mark-LV fork (its `core/orb_hud.py`), which drew the
layered "JARVIS orb" look from the web (wireframe icosahedron, glow, pulse
ring, frequency rings, particles) with QPainter alone — no WebGL, no new
dependency. Kept from it: the look, the step/paint contract the head uses, and
colours that come from the caller, so the hue wheel retints it for free.

Changed on the way in:

* **Speaking is passed in, not read from the state string.** `step()` takes
  the canvas's own `speaking` flag, as the head does, so one source decides
  whether JARVIS is talking; the fork searched the state text for "SPEAK".
* **One rotation per frame.** The fork recomputed cos/sin of the same two
  angles for every one of ~680 projected points, and projected the particles
  twice (once for the far half, once for the near half).
* **Particles are drawn in batches.** 320 `setBrush` + `drawEllipse` pairs a
  frame became a dozen `drawPoints` calls: round-capped points, bucketed by
  size and brightness.

Like the rest of the HUD it only ever paints opaque, pre-blended colours —
alpha compositing is the slow path in the software painter.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QRadialGradient


def _blend(bg: QColor, col: QColor, a: float) -> QColor:
    """`col` laid over `bg` at opacity `a` (0–1), as one opaque colour."""
    k = max(0.0, min(1.0, a))
    return QColor(
        int(bg.red()   + (col.red()   - bg.red())   * k),
        int(bg.green() + (col.green() - bg.green()) * k),
        int(bg.blue()  + (col.blue()  - bg.blue())  * k),
    )


_ICO_FACES = (
    (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
    (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
    (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
    (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
)


def _unit(x: float, y: float, z: float) -> tuple[float, float, float]:
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return x / n, y / n, z / n


def build_mesh(detail: int = 1) -> tuple[list[tuple[float, float, float]],
                                         list[tuple[int, int]]]:
    """A geodesic sphere: an icosahedron split `detail` times, on the unit
    sphere. Returns (vertices, unique edges). detail=1 gives 42 / 120."""
    t = (1.0 + math.sqrt(5.0)) * 0.5
    verts = [_unit(*v) for v in (
        (-1,  t,  0), ( 1,  t,  0), (-1, -t,  0), ( 1, -t,  0),
        ( 0, -1,  t), ( 0,  1,  t), ( 0, -1, -t), ( 0,  1, -t),
        ( t,  0, -1), ( t,  0,  1), (-t,  0, -1), (-t,  0,  1),
    )]
    faces = list(_ICO_FACES)
    for _ in range(max(0, detail)):
        mid: dict[tuple[int, int], int] = {}

        def midpoint(i: int, j: int) -> int:
            key = (i, j) if i < j else (j, i)
            if key not in mid:
                a, b = verts[i], verts[j]
                verts.append(_unit((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5,
                                   (a[2] + b[2]) * 0.5))
                mid[key] = len(verts) - 1
            return mid[key]

        split = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            split += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = split
    edges = {(min(u, v), max(u, v))
             for a, b, c in faces for u, v in ((a, b), (b, c), (c, a))}
    return verts, sorted(edges)


_N_PARTICLES = 320
_RING_POINTS = 96
_CAM_D = 3.6
# How far the frequency rings bulge with the voice. Kennycen's port already
# tamed the web original's 0.45; still wavy, never frantic.
_WAVE_AMOUNT = 0.18


class OrbHud:
    """Layered wireframe orb. One instance per HUD canvas.

    Same lifecycle as HoloAvatar:
        orb.step(dt, amp, speaking=..., muted=..., state=...)   # every tick
        orb.paint(painter, cx, cy, r, main, acc, bg)            # every frame
    """

    def __init__(self) -> None:
        self._verts, self._edges = build_mesh(detail=1)
        self._yaw = 0.0
        self._pitch = 0.18
        self._phase = 0.0
        self._pulse = 1.0
        self._amp = 0.0
        self._live = False
        self._muted = False
        rng = random.Random(42)
        # x, y, z, size, brightness, vx, vy, vz — a slow drift through a cube
        # that wraps at its faces.
        self._parts = [
            [rng.uniform(-1.8, 1.8), rng.uniform(-1.8, 1.8), rng.uniform(-1.8, 1.8),
             rng.uniform(0.4, 1.0), rng.uniform(0.15, 0.55),
             rng.uniform(-0.15, 0.15), rng.uniform(-0.12, 0.12), rng.uniform(-0.10, 0.10)]
            for _ in range(_N_PARTICLES)
        ]

    # ── animation ───────────────────────────────────────────────────────────

    @staticmethod
    def spin_rate(*, speaking: bool, muted: bool, state: str, amp: float) -> float:
        """Radians per second the orb turns at: a state you can read from
        across the room — near still when muted, fastest while thinking."""
        if muted:
            return 0.12
        if state in ("THINKING", "PROCESSING"):
            return 1.55
        if speaking:
            return 1.05
        if amp > 0.04:
            return 0.85
        return 0.55

    def step(self, dt: float, amp: float, *, speaking: bool = False,
             muted: bool = False, state: str = "IDLE") -> None:
        dt = max(0.0, min(0.10, float(dt)))
        amp = 0.0 if muted else max(0.0, min(1.0, float(amp)))
        self._amp = amp
        self._muted = muted
        self._live = (not muted) and (speaking or amp > 0.04
                                      or state in ("THINKING", "PROCESSING"))
        self._yaw += dt * self.spin_rate(speaking=speaking, muted=muted,
                                         state=state, amp=amp)
        self._pitch = 0.18 + 0.06 * math.sin(self._phase * 0.7)
        self._phase += dt * (1.0 + 0.8 * amp + (0.6 if speaking else 0.0))
        tgt = 1.0 + amp * (0.14 if speaking else 0.09)
        self._pulse += (tgt - self._pulse) * min(1.0, dt * 8.0)

        for q in self._parts:
            q[0] += q[5] * dt
            q[1] += q[6] * dt
            q[2] += q[7] * dt
            for i in range(3):
                if q[i] > 2.0:
                    q[i] = -2.0
                elif q[i] < -2.0:
                    q[i] = 2.0

    # ── drawing ─────────────────────────────────────────────────────────────

    def _projector(self, cx: float, cy: float, scale: float):
        """A projection for this frame: rotation by yaw then pitch, then a
        perspective divide. The trig is done once here, not per point."""
        cyw, syw = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)

        def project(x: float, y: float, z: float) -> tuple[float, float, float]:
            x1 = x * cyw - z * syw
            z1 = x * syw + z * cyw
            y2 = y * cp - z1 * sp
            z2 = y * sp + z1 * cp
            f = _CAM_D / (_CAM_D + z2 + 1e-6)
            return cx + x1 * scale * f, cy - y2 * scale * f, z2
        return project

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              main: QColor, acc: QColor, bg: QColor) -> None:
        if r < 8.0:
            return
        amp, live = self._amp, self._live

        # 1. Atmosphere — a centre-bright bloom in three layers.
        lift = 1.0 + 0.5 * amp + (0.15 if live else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0 in ((r * 1.15, 0.22), (r * 0.62, 0.28), (r * 0.30, 0.36)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, _blend(bg, main, min(0.9, a0 * lift)))
            g.setColorAt(0.55, _blend(bg, main, min(0.9, a0 * lift * 0.45)))
            g.setColorAt(1.00, bg)
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))

        far, near = self._project_particles(cx, cy, r)

        # 2. Particles behind the shell.
        self._paint_particles(p, far, main, bg, amp)

        # 3. Glow shell — accent at the heart, fading through the main colour.
        glow_r = r * 0.92 * self._pulse
        p.setPen(Qt.PenStyle.NoPen)
        gg = QRadialGradient(cx, cy, glow_r)
        gg.setColorAt(0.00, _blend(bg, acc, 0.18 + 0.25 * amp))
        gg.setColorAt(0.55, _blend(bg, main, 0.10 + 0.12 * amp))
        gg.setColorAt(1.00, bg)
        p.setBrush(QBrush(gg))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 4. The wireframe itself.
        self._paint_mesh(p, cx, cy, r * 0.78 * self._pulse, main, acc, bg, amp)

        # 5. Pulse rings.
        pr = r * 1.05 * self._pulse
        p.setPen(QPen(_blend(bg, acc if live else main, 0.28 + 0.45 * amp), 1.6))
        p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))
        p.setPen(QPen(_blend(bg, main, 0.12 + 0.2 * amp), 1.0))
        p.drawEllipse(QRectF(cx - pr * 1.04, cy - pr * 1.04, pr * 2.08, pr * 2.08))

        # 6. Frequency rings that ripple with the voice.
        self._paint_freq_rings(p, cx, cy, r, main, acc, bg, amp)

        # 7. Particles in front.
        self._paint_particles(p, near, acc, bg, amp)

    def _paint_mesh(self, p: QPainter, cx: float, cy: float, scale: float,
                    main: QColor, acc: QColor, bg: QColor, amp: float) -> None:
        project = self._projector(cx, cy, scale)
        proj = [project(*v) for v in self._verts]
        # Four depth bands, one pen and one drawLines call each: the far side
        # fades back and the near side carries the accent when there is voice.
        buckets: list[list[QLineF]] = [[], [], [], []]
        for i, j in self._edges:
            xi, yi, zi = proj[i]
            xj, yj, zj = proj[j]
            t = max(0.0, min(1.0, ((zi + zj) * 0.5 + 1.0) * 0.5))
            buckets[min(3, int(t * 4))].append(QLineF(xi, yi, xj, yj))
        alphas = (0.18, 0.32, 0.48, 0.72 + 0.2 * amp)
        widths = (0.8, 1.0, 1.15, 1.35)
        for bi, lines in enumerate(buckets):
            if lines:
                col = acc if bi == 3 and amp > 0.08 else main
                p.setPen(QPen(_blend(bg, col, alphas[bi]), widths[bi]))
                p.drawLines(lines)

    def _paint_freq_rings(self, p: QPainter, cx: float, cy: float, r: float,
                          main: QColor, acc: QColor, bg: QColor, amp: float) -> None:
        n = _RING_POINTS
        level = [
            max(0.0, min(1.0,
                amp * (0.55 + 0.45 * math.sin(self._phase * 2.1 + i * 0.37))
                + 0.04 * math.sin(self._phase * 0.9 + i * 0.2)))
            for i in range(n)
        ]
        # Each point averages three neighbours, so the ripple rolls rather
        # than jitters; each ring reads the level a third of the way round.
        smooth = [(level[i] + level[(i + 1) % n] + level[(i + 2) % n]) / 3.0
                  for i in range(n)]
        trig = [(math.cos(i / n * math.tau), math.sin(i / n * math.tau)) for i in range(n)]
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ring in range(3):
            base = r * (0.88 + ring * 0.14)
            off = ring * (n // 3)
            path = QPainterPath()
            for i, (c, s) in enumerate(trig):
                dyn = base * (1.0 + smooth[(i + off) % n] * _WAVE_AMOUNT)
                if i == 0:
                    path.moveTo(cx + c * dyn, cy + s * dyn)
                else:
                    path.lineTo(cx + c * dyn, cy + s * dyn)
            path.closeSubpath()
            col = acc if ring == 0 else main
            p.setPen(QPen(_blend(bg, col, 0.75 - ring * 0.18), 2.4 - ring * 0.45))
            p.drawPath(path)

    def _project_particles(self, cx: float, cy: float, r: float):
        """Project every particle once and split them at the orb's depth."""
        project = self._projector(cx, cy, r * 0.95)
        lim = (r * 1.6) ** 2
        far, near = [], []
        for x, y, z, sz, a0, *_ in self._parts:
            px, py, pz = project(x * 0.55, y * 0.55, z * 0.55)
            if (px - cx) ** 2 + (py - cy) ** 2 > lim:
                continue
            (far if pz < 0.05 else near).append((px, py, sz, a0))
        return far, near

    def _paint_particles(self, p: QPainter, pts, col: QColor, bg: QColor,
                         amp: float) -> None:
        """Round points, batched: three sizes × four brightness levels is at
        most twelve pen changes, against one per particle in the fork."""
        if not pts:
            return
        step = 1 if amp > 0.03 else 2          # half the cloud when idle
        batches: dict[tuple[int, int], list[QPointF]] = {}
        for px, py, sz, a0 in pts[::step]:
            a = a0 * (0.55 + 0.45 * amp)                  # 0.08 .. 0.55
            s = 1.0 + sz * (1.2 + amp)                    # 1.5 .. 3.2 px
            key = (min(2, int((s - 1.0) / 0.75)), min(3, int(a / 0.15)))
            batches.setdefault(key, []).append(QPointF(px, py))
        for (si, ai), group in batches.items():
            pen = QPen(_blend(bg, col, (ai + 0.5) * 0.15), 1.4 + si * 0.75)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPoints(group)
