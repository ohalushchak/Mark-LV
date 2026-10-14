"""close_app closes the app it is named after, and says when it could not —
no Alt+F4 into whatever window happens to have focus."""

import os

import pytest

from actions import _app_closer as ac
from actions import computer_settings as cs


def _w(hwnd, exe, title="", pid=None, path=""):
    return ac.Win(hwnd=hwnd, pid=pid if pid is not None else hwnd + 1000,
                  exe=exe, title=title, path=path)


WINDOWS = [
    _w(1, "chrome", "News - Google Chrome"),
    _w(2, "chrome", "Mail - Google Chrome"),
    _w(3, "taskmgr", "Диспетчер завдань"),
    _w(4, "sublime_text", "notes.txt - Sublime Text"),
    _w(5, "notepad", "Untitled - Notepad"),
]


# ── matching ─────────────────────────────────────────────────────────────────

def test_alias_finds_task_manager_whatever_its_localised_title(monkeypatch):
    monkeypatch.setattr(ac, "_OS", "Windows")
    assert [w.hwnd for w in ac.match_windows(WINDOWS, "Task Manager")] == [3]


def test_process_name_and_all_its_windows():
    assert [w.hwnd for w in ac.match_windows(WINDOWS, "chrome")] == [1, 2]
    assert [w.hwnd for w in ac.match_windows(WINDOWS, "notepad.exe")] == [5]


def test_part_of_a_process_name():
    assert [w.hwnd for w in ac.match_windows(WINDOWS, "sublime")] == [4]


def test_window_title_when_no_process_matches():
    assert [w.hwnd for w in ac.match_windows(WINDOWS, "notes.txt")] == [4]


def test_nothing_plausible_matches_nothing():
    assert ac.match_windows(WINDOWS, "photoshop") == []
    assert ac.match_windows(WINDOWS, "") == []


def test_spoken_description():
    assert ac.describe(WINDOWS[:2]) == "2 Chrome windows"
    assert ac.describe([WINDOWS[4]]) == "Notepad"


def test_the_reply_uses_the_name_the_user_said(monkeypatch):
    """Seen live: 'Closed Olk.' for Outlook, 'Taskmgr runs…' for Task Manager."""
    olk = [_w(8, "olk", "Inbox - Outlook")]
    _fake_windows_os(monkeypatch, olk)
    assert ac._close_windows_os("Outlook")[0] == "Closed Outlook."
    assert ac.friendly("task manager") == "Task Manager"
    msg = ac._why_left([WINDOWS[2]], WINDOWS, is_admin=lambda: False,
                       elevated=lambda pid: True, name="Task Manager")
    assert msg.startswith("Task Manager runs with administrator rights")


# ── closing and checking ─────────────────────────────────────────────────────

def test_close_windows_reports_what_really_closed():
    asked, alive = [], {1: False, 2: True}
    out = ac.close_windows(WINDOWS[:2], alive=lambda h: alive[h],
                           close=asked.append, wait_s=0.05, sleep=lambda s: None)
    assert asked == [1, 2]
    assert [w.hwnd for w in out.closed] == [1] and [w.hwnd for w in out.left] == [2]


def test_an_elevated_app_is_reported_as_needing_admin_rights():
    msg = ac._why_left([WINDOWS[2]], WINDOWS, is_admin=lambda: False,
                       elevated=lambda pid: True)
    assert "administrator" in msg


def test_a_save_dialog_is_reported():
    dialog = _w(9, "notepad", "Notepad", pid=WINDOWS[4].pid)
    msg = ac._why_left([WINDOWS[4]], WINDOWS + [dialog], is_admin=lambda: False,
                       elevated=lambda pid: False)
    assert "save" in msg and "Notepad" in msg


def _fake_windows_os(monkeypatch, windows, alive=lambda h: False, fg=0):
    monkeypatch.setattr(ac, "_win_list", lambda: windows)
    monkeypatch.setattr(ac, "_win_foreground", lambda: fg)
    monkeypatch.setattr(ac, "_win_alive", alive)
    closed = []
    monkeypatch.setattr(ac, "_win_close", closed.append)
    monkeypatch.setattr(ac, "_WAIT_S", 0.05)
    return closed


def test_it_never_closes_itself(monkeypatch):
    me = [_w(7, "python", "JARVIS — MARK LV", pid=os.getpid())]
    closed = _fake_windows_os(monkeypatch, me, fg=7)
    msg, _ = ac._close_windows_os("")
    assert "That is me" in msg and closed == []
    msg, _ = ac._close_windows_os("jarvis")
    assert "That is me" in msg and closed == []


def test_without_a_name_the_app_in_front_is_closed(monkeypatch):
    closed = _fake_windows_os(monkeypatch, WINDOWS, fg=4)
    msg, wins = ac._close_windows_os("")
    assert closed == [4] and msg == "Closed Sublime Text."


def test_an_app_that_is_not_open(monkeypatch):
    _fake_windows_os(monkeypatch, WINDOWS)
    msg, wins = ac._close_windows_os("photoshop")
    assert "could not find" in msg and wins == []


# ── through computer_settings ────────────────────────────────────────────────

@pytest.fixture
def no_keys(monkeypatch):
    pressed = []
    monkeypatch.setattr(cs.pyautogui, "hotkey", lambda *k: pressed.append(k))
    return pressed


def test_close_app_passes_the_name_and_presses_nothing(monkeypatch, no_keys):
    seen = []
    monkeypatch.setattr(ac, "close_app", lambda name: (seen.append(name) or ("Closed Chrome.", [])))
    r = cs.computer_settings({"action": "close_app", "value": "chrome"})
    assert r == "Closed Chrome." and seen == ["chrome"] and no_keys == []


def test_a_closed_app_can_be_reopened_with_undo(monkeypatch, no_keys):
    from core import undo
    win = _w(1, "chrome", path=r"C:\Chrome\chrome.exe")
    monkeypatch.setattr(ac, "close_app", lambda name: ("Closed Chrome.", [win]))
    started = []
    monkeypatch.setattr(ac.subprocess, "Popen", lambda args, **k: started.append(args))
    cs.computer_settings({"action": "close_app", "value": "chrome"})
    assert undo.undo_last().endswith("Reopened Chrome.")
    assert started == [[r"C:\Chrome\chrome.exe"]]


@pytest.mark.parametrize("text,action,value", [
    ("close chrome", "close_app", "chrome"),
    ("quit the task manager", "close_app", "task manager"),
    ("закрий телеграм", "close_app", "телеграм"),
    ("close this", "close_window", None),
])
def test_descriptions_resolve_locally(text, action, value):
    d = cs._detect_action(text)
    assert d["action"] == action and d["value"] == value
