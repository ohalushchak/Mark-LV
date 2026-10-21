"""
contacts — the user's own list of people, by voice: who is "Olya", what she
is called in Telegram, her number for Viber and calls.

Reading never changes anything; adding, changing and removing act at once and
"undo" reverses them (core/undo.py), like the other state-changing actions.
Numbers reach the model as their last four digits only (core/contacts.py),
so a reply never reads somebody's full number out into the room.
"""
from core import contacts as cb
from core.undo import push_undo


def _line(c: cb.Contact) -> str:
    parts = [c.name]
    if c.aliases:
        parts.append("also " + ", ".join(c.aliases))
    if c.phone:
        parts.append(f"phone {c.masked()}")
    if c.apps:
        parts.append("; ".join(f"in {k.title()}: {v}" for k, v in c.apps.items()))
    return " — ".join(parts)


def contacts(parameters: dict, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "find").strip().lower()
    name = str(p.get("name") or "").strip()
    try:
        if action == "list":
            cs = cb.load()
            if not cs:
                return ("The contact list is empty. People are added with action='add' "
                        f"(it lives in {cb.PATH.name}, on this computer only).")
            return f"{len(cs)} contact(s): " + "; ".join(c.name for c in cs[:40])
        if not name:
            return "Say whose contact it is (name)."
        if action == "find":
            f = cb.resolve(name)
            if f.contact is not None:
                return ("Found: " + _line(f.contact) + ". Nothing was sent and no call was "
                        "started.")
            if f.candidates:
                return f"Not sure who: {f.why}. Ask the user which one: {cb.describe(f.candidates)}."
            return f"Not in the contacts: {f.why}."
        if action == "add":
            phone = str(p.get("phone") or "").strip()
            if phone and cb.is_emergency(phone):
                return "That is an emergency number; it is not stored as a contact."
            aliases = [a.strip() for a in str(p.get("aliases") or "").split(",") if a.strip()]
            app, app_name = str(p.get("app") or "").strip(), str(p.get("app_name") or "").strip()
            if not (phone or aliases or (app and app_name)) and cb.resolve(name).contact is not None:
                return f"'{name}' is already in the contacts; say what to add (a phone, another name, the name in an app)."
            c, created, before = cb.add(name, phone, aliases, app, app_name)
            push_undo(f"{'add' if created else 'change'} contact {c.name}",
                      lambda b=before: (cb.restore(b), "The contact list is back as it was.")[1])
            if player is not None:
                try:
                    player.write_log(f"JARVIS: Contact {'added' if created else 'updated'}: {c.name}")
                except Exception:
                    pass
            return f"{'Added' if created else 'Updated'}: {_line(c)}. 'Undo' reverses it."
        if action == "rename":
            new = str(p.get("new_name") or "").strip()
            if not new:
                return "Say the new name (new_name)."
            f = cb.resolve(name)
            if f.contact is None:
                if f.candidates:
                    return f"Not renamed: {f.why}. Which one: {cb.describe(f.candidates)}?"
                return f"Not renamed: {f.why}."
            before = cb.rename(f.contact, new)
            push_undo(f"rename contact {f.contact.name} → {new}",
                      lambda b=before: (cb.restore(b), "The contact has its old name again.")[1])
            return f"Renamed {f.contact.name} to {new}. 'Undo' reverses it."
        if action == "remove":
            f = cb.resolve(name)
            if f.contact is None:
                if f.candidates:
                    return f"Not removed: {f.why}. Which one: {cb.describe(f.candidates)}?"
                return f"Not removed: {f.why}."
            before = cb.remove(f.contact)
            push_undo(f"remove contact {f.contact.name}",
                      lambda b=before: (cb.restore(b), "The contact is back.")[1])
            return f"Removed {f.contact.name} from the contacts. 'Undo' brings them back."
        return f"Unknown action '{action}': use find, list, add, rename or remove."
    except Exception as e:
        return f"Could not {action} the contact: {e}"


TOOL = {
    "name": "contacts",
    "description": (
        "The user's own contact list on this computer: who a name means, the name a person "
        "has in Telegram / Slack / Viber, their phone number (shown as the last 4 digits). "
        "action='find' — 'is Olya in my contacts', 'what is Taras called in Telegram'; "
        "'list'; 'add' — 'save Olya's number +380…', 'in Telegram she is Olya Petrenko' "
        "(name, phone, aliases comma-separated, app + app_name); 'rename' (name, new_name); "
        "'remove'. Changes take "
        "effect at once and the undo tool reverses them. Only looks up and stores — to write "
        "to someone use send_message, which finds the person here by itself."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "find (default) | list | add | rename | remove"},
            "new_name": {"type": "STRING", "description": "rename: the contact's new name"},
            "name": {"type": "STRING", "description": "The person, as the user calls them"},
            "phone": {"type": "STRING", "description": "add: the number with country code, e.g. +380671234567"},
            "aliases": {"type": "STRING", "description": "add: other names for them, comma-separated"},
            "app": {"type": "STRING", "description": "add: telegram | slack | viber | discord …"},
            "app_name": {"type": "STRING", "description": "add: their name exactly as that app shows it"},
        },
        "required": [],
    },
    "handler": contacts,
}
