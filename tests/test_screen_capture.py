"""Which screen a screenshot is taken of, on a multi-monitor setup."""

from actions.screen_processor import _pick_monitor

# A real layout, as mss lists it: an AV-receiver output first, then a laptop
# panel left of the primary, then the primary ultrawide at the origin.
LAYOUT = [
    {"left": -2560, "top": -155, "width": 7280, "height": 1600},
    {"left": 3440, "top": 717, "width": 1280, "height": 720, "is_primary": False},
    {"left": -2560, "top": -155, "width": 2560, "height": 1600, "is_primary": False},
    {"left": 0, "top": 0, "width": 3440, "height": 1440, "is_primary": True},
]


def test_the_screen_under_the_cursor():
    assert _pick_monitor(LAYOUT, (2767, 682)) == (3, "under the cursor")
    assert _pick_monitor(LAYOUT, (-1000, 300)) == (2, "under the cursor")
    assert _pick_monitor(LAYOUT, (4000, 1000)) == (1, "under the cursor")


def test_no_cursor_means_the_primary_not_the_first_listed():
    assert _pick_monitor(LAYOUT, None) == (3, "primary")


def test_a_cursor_in_a_gap_between_screens_falls_back_to_primary():
    assert _pick_monitor(LAYOUT, (3500, 100)) == (3, "primary")


def test_older_mss_without_is_primary_uses_the_origin():
    old = [{k: v for k, v in m.items() if k != "is_primary"} for m in LAYOUT]
    assert _pick_monitor(old, None) == (3, "primary")


def test_single_screen():
    one = [{"left": 0, "top": 0, "width": 1920, "height": 1080},
           {"left": 0, "top": 0, "width": 1920, "height": 1080}]
    assert _pick_monitor(one, None)[0] == 1
    assert _pick_monitor(one[:1], None) == (0, "only screen")
