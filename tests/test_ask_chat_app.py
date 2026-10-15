"""ask_chat_app: clipboard first, then the app or its website; honest replies."""
from actions import ask_chat_app as a


def test_copies_then_opens_and_says_it_cannot_read_back(monkeypatch):
    calls = []
    monkeypatch.setattr(a, "_copy", lambda text: calls.append(("copy", text)) or True)
    monkeypatch.setattr(a, "_open", lambda app: calls.append(("open", app["label"])) or "app")
    out = a.ask_chat_app({"app": "claude", "question": "  Explain monads  "})
    assert calls == [("copy", "Explain monads"), ("open", "Claude")]
    assert "paste" in out and "cannot read" in out


def test_nothing_opens_when_the_clipboard_fails(monkeypatch):
    opened = []
    monkeypatch.setattr(a, "_copy", lambda text: False)
    monkeypatch.setattr(a, "_open", lambda app: opened.append(app) or "app")
    out = a.ask_chat_app({"question": "hi"})
    assert "did not open ChatGPT" in out and not opened


def test_unknown_app_and_empty_question(monkeypatch):
    monkeypatch.setattr(a, "_copy", lambda text: True)
    monkeypatch.setattr(a, "_open", lambda app: "web")
    assert "ChatGPT or Claude" in a.ask_chat_app({"app": "bard", "question": "x"})
    assert a.ask_chat_app({"app": "chatgpt"}).startswith("What should I ask")
    assert "website" in a.ask_chat_app({"app": "gpt", "question": "x"})


def test_store_apps_are_found_by_name(monkeypatch):
    monkeypatch.setattr(a, "_start_apps", {"chatgpt classic": "OpenAI.ChatGPT!ChatGPT",
                                           "claude": "Claude_x!Claude"})
    assert a._windows_app_id(a.APPS["chatgpt"]["names"]) == "OpenAI.ChatGPT!ChatGPT"
    assert a._windows_app_id(a.APPS["claude"]["names"]) == "Claude_x!Claude"


def test_coding_jobs_are_sent_to_the_agent_instead(monkeypatch):
    # Live test: "pass to Claude: add subtract to calc.py" landed here.
    opened = []
    monkeypatch.setattr(a, "_copy", lambda text: True)
    monkeypatch.setattr(a, "_open", lambda app: opened.append(app) or "app")
    for q in ("Додай у calc.py функцію subtract і тест до неї.",
              "запусти тести і скажи, що вийшло"):
        out = a.ask_chat_app({"app": "claude", "question": q})
        assert out.startswith("[WRONG_TOOL]") and "delegate_to_agent" in out
    assert not opened
    out = a.ask_chat_app({"app": "claude", "question": "explain this code",
                          "confirm_chat_app": True})
    assert "opened" in out and opened
    assert "opened" in a.ask_chat_app({"question": "як зварити борщ"})
