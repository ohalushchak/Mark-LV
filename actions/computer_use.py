"""
computer_use — let the assistant carry out a multi-step task on the screen.

The loop itself lives in core/computer_use.py: it looks at the screen, plans
a few actions with a vision model, does them through computer_control's
checked actions, and confirms the goal with a final look. This file is only
the tool the model calls to start, follow and stop it.
"""
from core import computer_use as cu


def computer_use(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "start").strip().lower()
    if action == "start":
        return cu.start(str(p.get("goal") or ""), str(p.get("window") or ""),
                        str(p.get("monitor") or ""), player=player)
    if action == "status":
        return cu.status()
    if action == "stop":
        return cu.stop()
    if action in ("continue", "proceed", "yes"):
        return cu.proceed(player=player)
    return f"Unknown action '{action}'. Use start, status, continue or stop."


TOOL = {
    "name": "computer_use",
    "description": (
        "Carries out a multi-step task ON THE SCREEN by looking and clicking, for what no direct "
        "tool covers: a setting inside an app's own menus, a form, a sequence of clicks in a "
        "program ('turn on night light in Settings', 'in Outlook, set my status to away'). Prefer "
        "the direct tools whenever one fits — open_app, computer_settings, send_message, "
        "browser_control, file_controller, computer_control for a single click. action='start' "
        "with the user's `goal` in their own words (and `window` when they name the app) runs in "
        "the background: say you are on it, do not wait; the outcome is announced. Moving the "
        "mouse stops it. When it pauses before a risky step it asks you to ask the user: call "
        "action='continue' only after they said yes, or action='stop'. 'status' says where it is."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "start (default) | status | continue | stop"},
            "goal": {"type": "STRING", "description": "start: what should be true when it is done"},
            "window": {"type": "STRING",
                       "description": "start: the app or window to work in, when known (e.g. 'Settings')"},
            "monitor": {"type": "STRING",
                        "description": "start, no window: 'cursor' (default), 'primary', 'left', 'right' or a number"},
        },
        "required": [],
    },
    "handler": computer_use,
}
