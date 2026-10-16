"""Stage 1 of verified computer use: the verdict contract (core/verify.py),
how the action loader applies it, and the screen choice it relies on."""
import time

from core import health, verify
from core.action_loader import NO_RESULT, ActionRecord, ActionRegistry


def _registry(handler, snapshot=None, check=None, name="demo"):
    rec = ActionRecord(name=name, description="d", handler=handler, valid=True,
                       snapshot=snapshot, verify=check)
    return ActionRegistry({name: rec}, logger=lambda m: None)


# ── the contract ────────────────────────────────────────────────────────────

def test_tag_puts_the_verdict_last_with_what_to_say():
    assert verify.tag("Clicked.", None) == "Clicked."
    out = verify.tag("Opened Notepad.", verify.verified("a new Notepad window is open."))
    assert out == "Opened Notepad.\n[VERIFIED] a new Notepad window is open."
    out = verify.tag("Typed.", verify.unverified("the field does not expose its text"))
    assert out.startswith("Typed.\n[UNVERIFIED] the field does not expose its text.")
    assert "do not claim it happened" in out
    assert "do not claim success" in verify.tag("x", verify.failed("nothing changed"))


def test_poll_stops_at_the_first_verified_look():
    looks = iter([verify.unverified("not yet"), verify.verified("there it is"),
                  verify.failed("never reached")])
    v = verify.poll(lambda: next(looks), timeout=2, interval=0.01)
    assert v == verify.verified("there it is")


def test_poll_returns_the_last_look_when_time_runs_out():
    t = time.monotonic()
    v = verify.poll(lambda: verify.failed("still nothing"), timeout=0.1, interval=0.02)
    assert v.state == verify.FAILED and time.monotonic() - t < 1


def test_a_check_that_raises_is_unverified_not_an_error():
    def boom():
        raise OSError("no screen")
    assert verify.poll(boom, timeout=0).state == verify.UNVERIFIED
    assert verify.check(lambda p, r, b: 1 / 0, {}, "r", None).state == verify.UNVERIFIED
    assert verify.snapshot(lambda p: 1 / 0, {}) is None
    assert verify.check(lambda p, r, b: "not a verdict", {}, "r", None) is None


# ── the loader ──────────────────────────────────────────────────────────────

def test_the_loader_runs_snapshot_handler_and_verify_in_order():
    calls = []

    def snap(p):
        calls.append("snapshot")
        return {"before": 1}

    def handler(parameters):
        calls.append("handler")
        return "Did it."

    def check(p, result, before):
        calls.append(("verify", result, before))
        return verify.verified("seen")
    out = _registry(handler, snap, check).run("demo", {"a": 1})
    assert calls == ["snapshot", "handler", ("verify", "Did it.", {"before": 1})]
    assert out == "Did it.\n[VERIFIED] seen."
    assert health.status("demo") == "ok"


def test_a_failed_verdict_counts_as_a_failed_call():
    reg = _registry(lambda parameters: "Clicked.", None,
                    lambda p, r, b: verify.failed("the pointer is elsewhere"))
    out = reg.run("demo", {})
    assert "[FAILED] the pointer is elsewhere" in out
    assert health.status("demo") == "warn"
    assert health.recent()[0]["kind"] == "no_effect"


def test_unverified_is_not_a_failure():
    reg = _registry(lambda parameters: "Typed.", None,
                    lambda p, r, b: verify.unverified("no text to read back"))
    assert "[UNVERIFIED]" in reg.run("demo", {})
    assert health.status("demo") == "ok"


def test_a_handler_that_already_failed_is_not_verified():
    seen = []
    reg = _registry(lambda parameters: "Could not find the window.", None,
                    lambda p, r, b: seen.append(1) or verify.verified("x"))
    assert reg.run("demo", {}) == "Could not find the window."
    assert seen == [] and health.status("demo") == "warn"


def test_no_verdict_leaves_the_result_alone():
    reg = _registry(lambda parameters: None, None, lambda p, r, b: None)
    assert reg.run("demo", {}) == NO_RESULT


def test_the_codebase_failure_wordings_are_recognised():
    for text in ("computer_control 'click' failed: x", "Could not open: x",
                 "Couldn't reach it", "Unknown action: 'fly'"):
        assert health.looks_failed(text), text
    assert not health.looks_failed("Clicked 'Could not' button")


def test_actions_declare_their_checks():
    from actions import (browser_control, computer_control, computer_settings,
                         file_controller, open_app)
    for mod in (computer_control, computer_settings, open_app, file_controller, browser_control):
        assert callable(mod.TOOL["verify"]) and callable(mod.TOOL["snapshot"]), mod.__name__


# ── which screen ────────────────────────────────────────────────────────────

MONITORS = [
    {"left": -2560, "top": -155, "width": 7280, "height": 1600},
    {"left": 3440, "top": 717, "width": 1280, "height": 720, "is_primary": False},
    {"left": -2560, "top": -155, "width": 2560, "height": 1600, "is_primary": False},
    {"left": 0, "top": 0, "width": 3440, "height": 1440, "is_primary": True},
]


def test_monitors_are_chosen_the_way_people_number_them():
    from actions.screen_processor import _choose_monitor, describe_monitor
    assert _choose_monitor(MONITORS, "", (100, 100))[0] == 3            # under the cursor
    assert _choose_monitor(MONITORS, "primary", (3500, 800))[0] == 3
    assert _choose_monitor(MONITORS, "1", None)[0] == 2                 # leftmost
    assert _choose_monitor(MONITORS, "3", None)[0] == 1
    assert _choose_monitor(MONITORS, "left", None)[0] == 2
    assert _choose_monitor(MONITORS, "right", None)[0] == 1
    assert _choose_monitor(MONITORS, "9", (100, 100))[0] == 3           # nonsense → default
    assert describe_monitor(MONITORS, 3) == "monitor 2 of 3 (3440×1440, primary)"
    assert describe_monitor(MONITORS, 1) == "monitor 3 of 3 (1280×720)"


def test_vision_has_its_own_ladder_with_live_last():
    from core import gemini
    ladder = gemini._LADDERS[gemini.VISION]
    assert ladder[-1] == gemini.LIVE and gemini.LIVE not in ladder[:-1]
