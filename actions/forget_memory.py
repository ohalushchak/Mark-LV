"""
forget_memory — "forget everything about X", by voice, with undo.

Finds the stored facts about a subject (by words, or by meaning with a stricter
bar than recall uses — memory_manager.find_about) and deletes them with their
history. Acting at once and registering the reversal follows the convention for
state-changing actions (core/undo.py): "undo" puts every deleted row back. The
MEMORY panel's ✕ is the precise way to delete one fact; this is the spoken one.

Two forks shaped it: TeddyDARKVADOR's forget_about(subject), and Giljacob67's
counter-example, which deleted every entry sharing any word with the request.
Here the matched facts are named in the result, so the user hears what went.
"""

from core.undo import push_undo
from memory import memory_manager


def forget_memory(parameters: dict, player=None) -> str:
    subject = str((parameters or {}).get("subject") or "").strip()
    if not subject:
        return "Say what to forget — a person, a topic or a fact."
    try:
        rows = memory_manager.find_about(subject)
        if not rows:
            return (f"Nothing stored matches '{subject}', so nothing was deleted. "
                    "If the user means one specific fact, recall_memory shows what is stored.")
        gone = memory_manager.forget_rows(rows)
        push_undo(f"forget {subject}", lambda g=gone: memory_manager.restore_rows(g))
        named = "; ".join(f"{r['category']}/{r['key'].replace('_', ' ')}: {r['value']}"
                          for r in rows[:8])
        if player is not None:
            try:
                player.write_log(f"JARVIS: Forgot {len(rows)} fact(s) about {subject}.")
            except Exception:
                pass
        return (f"Deleted {len(rows)} stored fact(s) about '{subject}' with their history: "
                f"{named}. 'Undo' brings them back.")
    except Exception as e:
        return f"Could not forget '{subject}': {e}"


TOOL = {
    "name": "forget_memory",
    "description": (
        "Delete what you have stored about a person, topic or fact when the user "
        "asks you to forget it: 'forget my sister's address', 'забудь усе про "
        "Тараса', 'don't remember that'. subject = what to forget, in any "
        "language. Deletes the matching facts with their history and says which; "
        "the undo tool reverses it. Not for files (file_controller) or saved "
        "conversations."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "subject": {"type": "STRING", "description": "Who or what to forget, in any language."},
        },
        "required": ["subject"],
    },
    "handler": forget_memory,
}
