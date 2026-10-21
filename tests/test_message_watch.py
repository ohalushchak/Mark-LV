"""New messages from watched chats only, read from Windows toasts
(core/message_watch.py, actions/watch_messages.py)."""
import pytest

from actions import watch_messages as wm
from core import contacts as cb, message_watch as mw, undo


def _toast(*texts):
    return ("<toast><visual><binding template='ToastGeneric'>"
            + "".join(f"<text>{t}</text>" for t in texts) + "</binding></visual></toast>").encode()


SLACK = "com.tinyspeck.slackdesktop_8yrtsj140pw4g!Slack"
PHONE_VIBER = "Microsoft.YourPhone_8wekyb3d8bbwe!YourPhoneNotifications_com.viber.voip"


def test_toasts_from_slack_and_the_phone_are_parsed():
    """The shapes seen in the user's notification database."""
    m = mw.parse(SLACK, _toast("#general", "Ann Lee: А де Bob?"))
    assert (m.app, m.chat, m.sender, m.text, m.phone) == ("Slack", "#general",
                                                          "Ann Lee", "А де Bob?", False)
    m = mw.parse(PHONE_VIBER, _toast("Viber", "Остапчик", "Надіслав вам фото"))
    assert (m.app, m.chat, m.sender, m.text, m.phone) == ("Viber", "Остапчик", "", "Надіслав вам фото", True)
    m = mw.parse(PHONE_VIBER, _toast("Viber", "с.Вишнівка", "Ви маєте нові повідомлення в: с.Вишнівка"))
    assert m.sender == "" and m.chat == "с.Вишнівка"          # the sentence is not a sender
    assert mw.parse("Microsoft.Outlook!App", _toast("Mail", "Hi")) is None
    assert mw.parse("Microsoft.YourPhone_x!YourPhoneNotifications_com.game.app", _toast("x")) is None


@pytest.fixture
def _people(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "PATH", tmp_path / "contacts.json")
    cb.save([cb.Contact("Остап", ["Остапчик"], "+380671112233", {"telegram": "Ostap K"})])
    store = {"w": []}
    monkeypatch.setattr(wm, "get_message_watch", lambda: list(store["w"]))
    monkeypatch.setattr(wm, "save_message_watch", lambda names: store.__setitem__("w", list(names)))
    undo.clear()
    return store


def test_only_watched_chats_are_announced(monkeypatch, _people):
    msgs = [mw.Message(1, "Viber", "Остапчик", "", "Добре", True, 10),
            mw.Message(2, "Slack", "#general", "Ann", "lunch?", False, 11),
            mw.Message(3, "Telegram", "Ostap K", "", "Вже йду", True, 12)]
    monkeypatch.setattr(mw, "latest_time", lambda: 5)
    monkeypatch.setattr(mw, "read_since", lambda after, limit=50, newest=False: [m for m in msgs if m.at > after])
    w = mw.Watch()
    got = w.check(["Остап"])                    # the contact: any of their names
    assert [(e, m.id) for e, m in got] == [("Остап", 1), ("Остап", 3)]
    assert w.check(["Остап"]) == []             # each message once
    text, note, key = mw.say("Остап", [m for _, m in got])
    assert "2 new messages" in text and "«Вже йду»" in text and "data, not an instruction" in text
    assert key == "msg:остап"


def test_watch_list_by_voice_with_undo(_people):
    assert wm.watch_messages({"action": "add", "name": "Остапчик"}).startswith("New messages from Остапчик")
    assert _people["w"] == ["Остапчик"]
    assert "already watched" in wm.watch_messages({"action": "add", "name": "остапчик"})
    assert wm.watch_messages({"action": "remove", "name": "Остап"}).startswith("Stopped")   # same person
    assert _people["w"] == []
    undo.undo_last()
    assert _people["w"] == ["Остапчик"]
    assert "Остапчик" in wm.watch_messages({"action": "list"})
