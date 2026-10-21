"""
recall_conversation — what was talked about in earlier conversations.

Every conversation leaves a short summary in the memory database when it ends
(main.py → memory_manager.save_session_summary). They are kept for good and
searched here by words and by meaning, in either language, so "що ми вчора
робили зі Slack?" finds a summary that says "налаштовували повідомлення в
Слеку". Only summaries are stored, never the full transcript — the user chose
that over keeping every turn.
"""

from memory import memory_manager


def recall_conversation(parameters: dict, player=None) -> str:
    query = str((parameters or {}).get("query") or "").strip()
    try:
        return memory_manager.search_sessions(query, limit=4)
    except Exception as e:
        return f"Could not read the saved conversations: {e}"


TOOL = {
    "name": "recall_conversation",
    "description": (
        "What the user and you talked about in EARLIER conversations (previous "
        "sessions, other days). Use for 'what did we discuss yesterday?', 'what "
        "were we doing with Slack last time?', 'did I tell you about the trip?', "
        "'нагадай, про що ми говорили'. query = the topic in any language; leave "
        "it empty for the most recent conversations. Searches saved summaries, "
        "not word-for-word transcripts. Not for facts about the user (name, "
        "family, preferences) — that is recall_memory; not for their notes — "
        "that is notes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING",
                      "description": "Topic to look for, in any language. Empty: the latest conversations."},
        },
    },
    "handler": recall_conversation,
}
