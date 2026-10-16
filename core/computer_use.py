"""
Computer Use — a multi-step task on the screen: look, plan, act, check.

For what no direct tool covers: a setting buried in an app's own menus, a
form, a sequence of clicks in a program the assistant has no API for. The
user says the goal; this loop works it out on the screen, in the background,
and the outcome is announced when it is done (core/announce.py).

ONE STEP
    1. Look: a screenshot of the target window (or one monitor), and the
       named controls UI Automation can see there, numbered.
    2. Plan: one vision call (core/gemini.py, VISION) returns up to three
       actions. A control is chosen by its number — exact, whatever the
       scaling — or by a point on the image, which a second, close look
       confirms before anything is clicked (computer_control._ask_boxes).
    3. Act: every action goes through computer_control's own checks
       (core/verify.py), and the verdict — VERIFIED / UNVERIFIED / FAILED with
       what was seen — is what the next plan reads.
    4. When the model says the goal is reached, a separate look answers
       yes / no / unknown; unknown is not success.

SAFETY
    * An action is risky by what it really touches — the UI Automation name
      of the control under the point, the message box Enter would send — not
      by how the model describes it (bfrpaulondev/Mark-BP checked only the
      model's own words: a "blue button" that is really Send passed). Risky
      means delete, pay, buy, send, submit, sign out, uninstall… The loop then
      pauses and asks; it goes on only after the user answered by voice
      (core/activity.py, the same gate as the agent hand-off and sending) or
      pressed CONFIRM on the HUD.
    * Typing into a password field is refused outright.
    * Moving the mouse while it works stops it — the user has taken over —
      and so do pyautogui's top-left-corner fail-safe and "stop" by voice.
    * Budgets: steps, model calls, steps without progress, wall time.

Mixed from bfrpaulondev/Mark-BP (window-scoped vision, batching only what
needs no new look, stale-screen check, bounded recovery, UIA first),
JishnuDevTech (look → act → verify, re-locate each time) and rilzisheree
(a strict yes/no/unknown verdict where unknown fails). Left out: their
provider router, cost telemetry and OpenAI planner — one Gemini ladder here.
"""
from __future__ import annotations

import io
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from core import activity, announce, confirm

MAX_STEPS = 20          # plan → act rounds
MAX_CALLS = 24          # vision calls in all (plans, close looks, final check)
MAX_IDLE = 4            # rounds in a row in which nothing verifiably happened
MAX_SECONDS = 8 * 60
ASK_TTL = 150.0         # how long a paused risky step waits for the user
MOVED_PX = 30           # the user's own mouse movement that stops the run
IMG_MAX = (1600, 1000)

_RISKY_RE = re.compile(
    r"(delete|remove|erase|uninstall|format|reset|wipe|discard|pay|purchase|buy|order|checkout|"
    r"subscribe|transfer|send|submit|post|publish|sign ?out|log ?out|password|"
    r"видал|вилуч|стерти|оплат|купи|придба|замов|переказ|надісл|відправ|опублік|вийти|пароль|"
    r"скину|форматув)", re.IGNORECASE)


@dataclass
class Step:
    n: int
    what: str            # "click [4] 'Display'"
    verdict: str         # "VERIFIED: focus moved to …"


@dataclass
class Run:
    goal: str
    window: str = ""
    monitor: str = ""
    started: float = field(default_factory=time.monotonic)
    state: str = "running"       # running | waiting | done | unverified | failed | stopped
    phase: str = "starting"      # what it is doing now, for the HUD
    steps: list = field(default_factory=list)
    calls: int = 0
    rounds: int = 0
    idle: int = 0
    result: str = ""
    question: str = ""           # while waiting: what is asked
    asked_at: float = 0.0

    @property
    def elapsed(self) -> str:
        s = int(time.monotonic() - self.started)
        return f"{s // 60}:{s % 60:02d}"


_lock = threading.Lock()
_run: Optional[Run] = None
_stop = threading.Event()
_resume = threading.Event()
_listeners: list[Callable[[], None]] = []


# ── public API (actions/computer_use.py) ────────────────────────────────────

def start(goal: str, window: str = "", monitor: str = "", player=None) -> str:
    global _run
    goal = (goal or "").strip()
    if not goal:
        return "No goal given. Call again with 'goal' saying exactly what should be done on screen."
    with _lock:
        if _run is not None and _run.state in ("running", "waiting"):
            return (f"[ALREADY_RUNNING] Computer use is already working on '{_run.goal[:80]}' "
                    f"({_run.elapsed}). Stop it first, or wait for it.")
        _run = Run(goal=goal, window=(window or "").strip(), monitor=(monitor or "").strip())
        r = _run
    _stop.clear()
    _resume.clear()
    threading.Thread(target=_work, args=(r, player), name="computer-use", daemon=True).start()
    _changed()
    return ("[CU_STARTED] Working on it on the screen, in the background. Tell the user in a "
            "few words that you are on it and that moving the mouse stops it. Do not wait: the "
            "outcome is announced when it is done.")


def stop(why: str = "stopped by the user") -> str:
    with _lock:
        r = _run
    if r is None or r.state not in ("running", "waiting"):
        return "Computer use is not running."
    r.result = why
    _stop.set()
    _resume.set()
    return "Stopping computer use now."


def proceed(player=None) -> str:
    """The user's answer to a paused risky step."""
    with _lock:
        r = _run
    if r is None or r.state != "waiting":
        return "Computer use is not waiting for anything."
    if time.monotonic() - r.asked_at > ASK_TTL:
        return "That question expired; computer use has stopped."
    if not activity.user_answered_since(r.asked_at):
        return ("[NOT_CONTINUED] You called continue before the user answered. End your turn "
                "and wait for their reply.")
    confirm.dismiss("computer_use")
    _resume.set()
    return "Continuing."


def status() -> str:
    with _lock:
        r = _run
    if r is None:
        return "Computer use has not run in this session."
    last = r.steps[-1] if r.steps else None
    tail = f" Last step: {last.what} — {last.verdict}." if last else ""
    if r.state == "waiting":
        return f"Computer use is paused, waiting for the user: {r.question}"
    if r.state == "running":
        return (f"Computer use is working on '{r.goal[:80]}': {r.phase}; {len(r.steps)} "
                f"action(s), {r.elapsed}.{tail}")
    return f"Computer use {r.state} '{r.goal[:80]}' after {r.elapsed}: {r.result}"


def state() -> dict:
    """For the HUD ring."""
    with _lock:
        r = _run
    if r is None:
        return {"state": ""}
    return {"state": r.state, "phase": r.phase, "goal": r.goal, "since": r.started}


def on_change(fn: Callable[[], None]) -> None:
    _listeners.append(fn)


def _changed() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


# ── looking ─────────────────────────────────────────────────────────────────

@dataclass
class View:
    jpeg: bytes
    image: object               # the full-resolution PIL image of the region
    left: int
    top: int
    width: int                  # region size in screen pixels
    height: int
    what: str                   # "the 'Settings' window" / "monitor 2 of 3 (…)"
    controls: list              # [{"id", "name", "type", "rect"}]
    sample: object = None       # a grey grid, to tell later whether the screen moved
    hwnd: int = 0               # the window that was in front when it was read


def _look(r: Run) -> View:
    import PIL.Image
    from actions import computer_control as cc
    from actions.screen_processor import capture_monitor
    from core import desktop_state as ds

    region, what, hwnd = None, "", 0
    fg = ds.foreground() or {}
    if r.window and _windows():
        w = cc._find_window_win(r.window)
        if w is not None:
            if fg.get("hwnd") != w.hwnd:
                cc._bring_forward(w.hwnd)
                time.sleep(0.4)
            info = ds.window(w.hwnd) or {}
            region = ds.clip(info.get("rect"))
            what, hwnd = f"the window '{w.title[:60]}'", w.hwnd
        elif _usable(fg):
            # Not found by name — Windows Settings is an ApplicationFrameHost
            # window titled "Налаштування" on this machine — so the window in
            # front, where what the steps opened appears. Seen live: the run
            # looked at the monitor under the mouse instead, never saw the
            # Settings it had opened, and gave up.
            region = ds.clip(fg.get("rect"))
            what, hwnd = f"the window in front, '{fg.get('title', '')[:60]}'", fg["hwnd"]
    if region is not None:
        import mss
        with mss.mss() as s:
            shot = s.grab({"left": region[0], "top": region[1],
                           "width": region[2] - region[0], "height": region[3] - region[1]})
        img = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
        left, top = region[0], region[1]
        width, height = region[2] - region[0], region[3] - region[1]
    else:
        # The screen the window in front is on, unless the user named one.
        which = r.monitor or None
        if not which and _usable(fg) and fg.get("rect"):
            l, t, rr, b = fg["rect"]
            which = ((l + rr) // 2, (t + b) // 2)
        png, geom = capture_monitor(which)
        img = PIL.Image.open(io.BytesIO(png)).convert("RGB")
        left, top, width, height = geom["left"], geom["top"], geom["width"], geom["height"]
        what = geom["label"]
        hwnd = (ds.foreground() or {}).get("hwnd", 0)

    controls = []
    if _windows() and hwnd and not ds.is_me((ds.window(hwnd) or {}).get("pid", 0)):
        for c in ds.controls(hwnd):
            l, t, rr, b = c["rect"]
            cx, cy = (l + rr) // 2, (t + b) // 2
            if c["name"].strip() and left <= cx < left + width and top <= cy < top + height:
                controls.append(c)
        controls = controls[:90]
        for i, c in enumerate(controls, 1):
            c["id"] = i
    small = img.copy()
    small.thumbnail(IMG_MAX, PIL.Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=85)
    return View(buf.getvalue(), img, left, top, width, height, what, controls,
                ds.sample((left, top, left + width, top + height)), hwnd)


def _usable(fg: dict) -> bool:
    """A window worth looking at: an app's, not the desktop, taskbar or this assistant."""
    from core import desktop_state as ds
    if not fg or not fg.get("hwnd") or not fg.get("title") or fg.get("minimized"):
        return False
    if ds.is_me(fg.get("pid", 0)) or fg.get("exe") in ("", "searchhost", "startmenuexperiencehost"):
        return False
    return fg.get("exe") != "explorer" or bool(fg.get("title"))


def _windows() -> bool:
    import platform
    return platform.system() == "Windows"


_KIND = {50000: "button", 50011: "menu item", 50005: "link", 50019: "tab", 50007: "list item",
         50002: "checkbox", 50013: "radio", 50004: "text field", 50003: "combo box",
         50024: "tree item", 50031: "split button"}


# ── planning ────────────────────────────────────────────────────────────────

_PLAN = """You operate a {os} computer to reach a goal, a few small actions at a time.

GOAL: {goal}

You see {what}. Coordinates are [y, x], 0-1000 of THIS image.
{controls}
Done so far, each with what was observed afterwards:
{history}

Reply with JSON only:
{{"status": "continue" | "done" | "impossible",
  "thought": "one short sentence: what you see and why the next actions",
  "actions": [ ... at most 3, in order ... ],
  "evidence": "when done: what on screen shows the goal is reached; when impossible: why"}}
Actions:
  {{"do": "click" | "double_click" | "right_click", "control": <number>}}   (preferred: exact)
  {{"do": "click", "point": [y, x], "target": "the visible text or name of what you click"}}
  {{"do": "type", "text": "..."}}       {{"do": "press", "key": "enter"}}
  {{"do": "hotkey", "keys": "ctrl+s"}}  {{"do": "scroll", "direction": "down", "amount": 5}}
  {{"do": "wait", "seconds": 2}}
  {{"do": "open", "app": "Settings"}}   opens an app, or a Windows Settings page directly
                                       ("ms-settings:nightlight", "ms-settings:storagesense",
                                       "ms-settings:windowsupdate") — better than the Start menu
Add "risky": true to anything that deletes, pays, buys, sends, submits, signs out or
changes an account — it is confirmed with the user first.
Rules:
- Chain only what needs no new look (click a text field, then type into it). After
  anything that changes what is shown, stop and look again.
- Check the observations: a FAILED or UNVERIFIED action did not visibly work — try
  another way instead of repeating it.
- Never type passwords. The window titled J.A.R.V.I.S is the assistant itself; never
  touch it.
- "done" only when the screen shows the goal reached. "impossible" when it cannot be
  reached from here (missing app, missing permission, the element is not there)."""


def _plan(r: Run, view: View) -> Optional[dict]:
    from google.genai import types as gtypes
    from core import gemini
    import platform
    controls = "Named controls (click these by number when one fits):\n" + "\n".join(
        f"  [{c['id']}] {_KIND.get(c['type'], 'control')} '{c['name'][:50]}'"
        + ("" if c.get("enabled", True) else " (disabled)") for c in view.controls) + "\n" \
        if view.controls else "No named controls are readable here; use points.\n"
    history = "\n".join(f"  {s.n}. {s.what} — {s.verdict}" for s in r.steps[-12:]) or "  nothing yet"
    prompt = _PLAN.format(os=platform.system() or "desktop", goal=r.goal, what=view.what,
                          controls=controls, history=history)
    r.calls += 1
    reply = gemini.as_json(
        [gtypes.Part.from_bytes(data=view.jpeg, mime_type="image/jpeg"), prompt],
        tier=gemini.VISION, timeout_ms=20_000, default=None)
    return reply if isinstance(reply, dict) else None


_CHECK = """GOAL: {goal}
This screenshot was taken after working on that goal. Is the goal reached, as far as
this screen shows? Answer JSON only: {{"verdict": "yes" | "no" | "unknown",
"evidence": "what on the screen shows it, in one sentence"}}. "unknown" when the
screen cannot show it either way. Do not guess."""


def _final_check(r: Run) -> tuple[str, str]:
    from google.genai import types as gtypes
    from core import gemini
    view = _look(r)
    r.calls += 1
    reply = gemini.as_json(
        [gtypes.Part.from_bytes(data=view.jpeg, mime_type="image/jpeg"), _CHECK.format(goal=r.goal)],
        tier=gemini.VISION, timeout_ms=20_000, default=None)
    if not isinstance(reply, dict):
        return "unknown", "the final look got no answer"
    v = str(reply.get("verdict", "unknown")).strip().lower()
    return (v if v in ("yes", "no", "unknown") else "unknown"), str(reply.get("evidence", ""))[:200]


# ── acting ──────────────────────────────────────────────────────────────────

class _Stop(Exception):
    pass


def _point(view: View, a: dict, r: Run) -> tuple[Optional[tuple[int, int]], str, str]:
    """((x, y) on screen, label, why not)."""
    from actions import computer_control as cc
    if a.get("control") is not None:
        try:
            cid = int(a["control"])
        except (TypeError, ValueError):
            return None, "", "the control number is not a number"
        c = next((c for c in view.controls if c.get("id") == cid), None)
        if c is None:
            return None, "", f"there is no control [{cid}]"
        l, t, rr, b = c["rect"]
        return ((l + rr) // 2, (t + b) // 2), c["name"], ""
    pt = a.get("point")
    try:
        py, px = float(pt[0]), float(pt[1])
    except Exception:
        return None, "", "no control and no point to click"
    if not (0 <= px <= 1000 and 0 <= py <= 1000):
        return None, "", "the point is outside the image"
    target = str(a.get("target") or "").strip()
    W, H = view.image.size
    ix, iy = px / 1000 * W, py / 1000 * H
    if target and r.calls < MAX_CALLS:
        # A close look at native resolution confirms the spot (stage 4).
        zw, zh = min(W, 640), min(H, 400)
        l = int(min(max(0, ix - zw / 2), W - zw))
        t = int(min(max(0, iy - zh / 2), H - zh))
        r.calls += 1
        fine = cc._ask_boxes(view.image.crop((l, t, l + zw, t + zh)),
                             "a close-up crop of part of a computer screen", target)
        if fine:
            best = min(fine, key=lambda b: (b[0] - 500) ** 2 + (b[1] - 500) ** 2)
            ix, iy = l + best[0] / 1000 * zw, t + best[1] / 1000 * zh
            target = best[2] or target
        elif fine is not None:
            return None, target, f"a closer look did not find '{target}' at that spot"
    x = view.left + round(ix * view.width / W)
    y = view.top + round(iy * view.height / H)
    return (x, y), target, ""


def _label_at(x: int, y: int) -> str:
    """The real name of what is under a point (UI Automation)."""
    if not _windows():
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        from pywinauto.uia_defines import IUIA
        el = IUIA().iuia.ElementFromPoint(wintypes.POINT(int(x), int(y)))
        return str(el.CurrentName or "")[:80] if el else ""
    except Exception:
        return ""


def _risk(a: dict, label: str) -> str:
    """Why this action needs the user's yes, or ""."""
    from actions import computer_control as cc
    do = a.get("do")
    if do in ("press", "hotkey"):
        keys = str(a.get("key") or a.get("keys") or "").lower().replace(" ", "")
        if keys in ("delete", "del", "shift+delete", "shift+del"):
            return "it presses Delete, which deletes what is selected"
        if keys in cc._SEND_KEYS:
            box = cc._compose_target()
            if box is not None:
                return f"pressing Enter would send {cc._what(box)}"
    if do in ("click", "double_click", "right_click") and label and _RISKY_RE.search(label):
        return f"it clicks '{label}'"
    if a.get("risky"):
        what = label or str(a.get("target") or a.get("text") or do)
        return f"the step is marked risky: {do} '{what[:60]}'"
    return ""


def _ask_user(r: Run, why: str, player=None) -> None:
    """Pause until the user says yes (voice or HUD), or stop."""
    r.state, r.question, r.asked_at = "waiting", f"may I go on? {why}", time.monotonic()
    r.phase = "waiting for your yes"
    _resume.clear()
    _changed()
    if not confirm.pending_title():
        confirm.request(key="computer_use", title="Computer use: go on?", detail=why[:200],
                        run=lambda: (_resume.set(), "Continuing.")[1])
    announce.say(
        f"[COMPUTER_USE_ASK] Computer use paused before a step that needs the user's yes: "
        f"{why}. Ask them in ONE short sentence whether to go on, then END YOUR TURN. If they "
        f"agree, call computer_use with action='continue'; if not, action='stop'. They can also "
        f"press CONFIRM on the HUD.", note="Computer use: waiting for your yes", key="cu-ask")
    if not _resume.wait(ASK_TTL) or _stop.is_set():
        confirm.dismiss("computer_use")
        raise _Stop(r.result or "the risky step was not confirmed")
    r.state, r.question = "running", ""
    _changed()


def _act(r: Run, view: View, a: dict, player=None) -> tuple[str, str, bool]:
    """Do one action through computer_control's checks: (what, verdict text,
    something verifiably happened)."""
    from actions import computer_control as cc
    from core import desktop_state as ds
    from core import verify as v
    do = str(a.get("do") or "").lower()
    if do == "wait":
        secs = max(0.0, min(float(a.get("seconds") or 1), 5.0))
        time.sleep(secs)
        return f"wait {secs:g}s", "done", False
    if do == "open":
        from actions import open_app as oa
        name = str(a.get("app") or a.get("name") or "").strip()
        if not name:
            return "open", "NOT DONE: no app named", False
        p = {"app_name": name}
        before = oa._snapshot(p)
        result = oa.open_app(p)
        v_ = oa._verify(p, result, before) if not result.lower().startswith(("could not", "failed"))             else None
        if v_ is None:
            return f"open {name}", f"FAILED: {result[:80]}", False
        time.sleep(0.8)                     # let the new window settle before the next look
        return f"open {name}", f"{v_.state.upper()}: {v_.detail}", v_.state == v.VERIFIED

    params: dict = {}
    label = ""
    if do in ("click", "double_click", "right_click"):
        pt, label, why = _point(view, a, r)
        if pt is None:
            return f"{do} '{label or a.get('target', '')}'", f"NOT DONE: {why}", False
        if _windows():
            label = _label_at(*pt) or label
        params = {"action": do, "x": pt[0], "y": pt[1]}
        what = f"{do} '{label[:50]}' at {pt}"
    elif do == "type":
        f = ds.focused_control()
        if f is not None and f.password:
            return "type into a password field", "NOT DONE: I never type passwords", False
        params = {"action": "type", "text": str(a.get("text", ""))}
        what = f"type '{params['text'][:50]}'"
    elif do in ("press", "hotkey"):
        keys = str(a.get("key") or a.get("keys") or "").strip()
        params = {"action": do, ("key" if do == "press" else "keys"): keys}
        what = f"{do} {keys}"
    elif do == "scroll":
        cx, cy = view.left + view.width // 2, view.top + view.height // 2
        if _windows():
            import pyautogui
            pyautogui.moveTo(cx, cy, duration=0.1)
        params = {"action": "scroll", "direction": str(a.get("direction") or "down"),
                  "amount": int(a.get("amount") or 5)}
        what = f"scroll {params['direction']}"
    else:
        return f"'{do}'", "NOT DONE: unknown action", False

    why = _risk(a, label)
    if why:
        _ask_user(r, why, player)
        # Answering — by voice or on the HUD — may have brought the HUD
        # forward; the keys must go back to the window that was read.
        if view.hwnd and (ds.foreground() or {}).get("hwnd") != view.hwnd:
            cc._bring_forward(view.hwnd)
            time.sleep(0.4)
    before = cc._snapshot(params)
    if do == "press":
        # The keys themselves, past computer_control's send gate: the risky
        # ones were just asked about here, the rest are not sends.
        result = cc._press(params["key"])
    elif do == "hotkey":
        result = cc._hotkey(*[k.strip() for k in params["keys"].split("+") if k.strip()])
    else:
        result = cc.computer_control(params)
    verdict = cc._verify(params, result, before)
    if verdict is None:
        failed = result.lower().startswith(("could not", "computer_control", "unknown"))
        return what, (f"FAILED: {result[:80]}" if failed else "done"), not failed
    return what, f"{verdict.state.upper()}: {verdict.detail}", verdict.state == v.VERIFIED


def _check_user(last_pos) -> None:
    """Stop when the user has taken the mouse."""
    from core import desktop_state as ds
    if _stop.is_set():
        raise _Stop(_run.result if _run and _run.result else "stopped")
    if last_pos is None:
        return
    cur = ds.cursor()
    if cur and (abs(cur[0] - last_pos[0]) > MOVED_PX or abs(cur[1] - last_pos[1]) > MOVED_PX):
        raise _Stop("you moved the mouse, so I stopped and left the rest to you")


def _screen_moved(view: View) -> bool:
    from core import desktop_state as ds
    if view.sample is None:
        return False
    now = ds.sample((view.left, view.top, view.left + view.width, view.top + view.height))
    # A tenth of the region: a caret or a clock is not "moved", a new page is.
    # Lower, and a window with a video in it was never acted on at all.
    n = ds.changed_cells(view.sample, now)
    return n >= 0 and n > view.sample.size * 0.12


# ── the loop ────────────────────────────────────────────────────────────────

def _work(r: Run, player) -> None:
    from core import desktop_state as ds
    last_pos = ds.cursor()
    outcome, evidence = "failed", ""
    try:
        while True:
            if _stop.is_set():
                raise _Stop(r.result or "stopped")
            if r.rounds >= MAX_STEPS or r.calls >= MAX_CALLS:
                raise _Stop("it ran out of steps before the goal was reached")
            if time.monotonic() - r.started > MAX_SECONDS:
                raise _Stop("it ran out of time")
            if r.idle >= MAX_IDLE:
                raise _Stop(f"nothing worked in the last {MAX_IDLE} rounds")
            _check_user(last_pos)
            r.phase = "looking"
            _changed()
            view = _look(r)
            r.rounds += 1
            _check_user(last_pos)
            r.phase = "planning"
            _changed()
            plan = _plan(r, view)
            _check_user(last_pos)
            if plan is None:
                r.idle += 1
                r.steps.append(Step(len(r.steps) + 1, "plan", "the vision model did not answer"))
                continue
            status = str(plan.get("status", "continue")).lower()
            if status == "impossible":
                outcome, evidence = "failed", str(plan.get("evidence") or plan.get("thought") or "")
                r.result = f"it cannot be done from here: {evidence[:200]}"
                break
            if status == "done":
                r.phase = "checking the result"
                _changed()
                verdict, ev = _final_check(r)
                if verdict == "yes":
                    outcome, evidence = "done", ev or str(plan.get("evidence") or "")
                    r.result = f"done — {evidence[:200]}"
                    break
                if verdict == "unknown":
                    outcome, evidence = "unverified", ev
                    r.result = (f"it believes it is done, but the screen does not confirm it: "
                                f"{ev[:160]}")
                    break
                r.steps.append(Step(len(r.steps) + 1, "claimed done",
                                    f"FAILED: the final look says not yet — {ev[:120]}"))
                r.idle += 1
                continue
            actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)][:3]
            if not actions:
                r.idle += 1
                r.steps.append(Step(len(r.steps) + 1, "plan", "no actions were proposed"))
                continue
            progressed = False
            for i, a in enumerate(actions):
                _check_user(last_pos)
                if i and str(actions[i - 1].get("do")) in ("click", "double_click", "right_click") \
                        and a.get("do") != "type":
                    break                      # only "click then type" chains without a look
                if i and a.get("do") in ("click", "double_click", "right_click"):
                    break
                if a.get("do") in ("click", "double_click", "right_click")                         and a.get("control") is None and _screen_moved(view):
                    r.steps.append(Step(len(r.steps) + 1, "look again",
                                        "the screen changed since it was read"))
                    break
                r.phase = f"step {len(r.steps) + 1}: {a.get('do')}"
                _changed()
                what, verdict, ok = _act(r, view, a, player)
                r.steps.append(Step(len(r.steps) + 1, what, verdict))
                print(f"[ComputerUse] {what} → {verdict}")
                progressed = progressed or ok
                last_pos = ds.cursor()       # also forgives a hand that answered on the HUD
                if verdict.startswith(("FAILED", "NOT DONE")):
                    break
                time.sleep(0.25)
            r.idle = 0 if progressed else r.idle + 1
    except _Stop as e:
        outcome = "stopped"
        r.result = str(e)
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            outcome, r.result = "stopped", "the mouse went to the screen corner (fail-safe)"
        else:
            import traceback
            traceback.print_exc()
            outcome, r.result = "failed", f"it crashed: {type(e).__name__}: {e}"
    r.state = outcome
    r.phase = outcome
    confirm.dismiss("computer_use")
    _changed()
    _announce(r, outcome)


def _announce(r: Run, outcome: str) -> None:
    said = {"done": "reached the goal", "unverified": "could not confirm the goal",
            "failed": "did NOT reach the goal", "stopped": "stopped"}[outcome]
    steps = "\n".join(f"{s.n}. {s.what} — {s.verdict}" for s in r.steps[-8:])
    announce.say(
        f"[COMPUTER_USE_RESULT] Computer use {said} for '{r.goal[:200]}' after {r.elapsed} "
        f"and {len(r.steps)} action(s): {r.result}\nLast actions:\n{steps}\n\nTell the user in "
        f"one or two short spoken sentences: the outcome first, as the result says it — "
        f"'done' only if it says done.",
        note=f"Computer use: {outcome} — {r.goal[:50]}", key="cu-result")


def reset() -> None:
    """Tests only."""
    global _run
    _stop.set()
    _resume.set()
    with _lock:
        _run = None
    _stop.clear()
    _resume.clear()
