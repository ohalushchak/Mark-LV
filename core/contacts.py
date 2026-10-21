"""
The people the user writes to and calls — and who exactly "Olya" is.

Why a list of our own: the messengers name people differently (Slack shows
"Olha P.", Telegram "Оля", Viber nothing at all to UI Automation — only the
phone number opens a Viber chat for certain), and the user names them a third
way by voice. A contact ties those together:

    {"contacts": [{"name": "Оля Петренко", "aliases": ["Оля", "Olya"],
                   "phone": "+380671234567",
                   "apps": {"telegram": "Olya Petrenko", "slack": "Olha P."}}]}

kept in config/contacts.json (git-ignored: it is personal data).

Matching follows Nathan123y's Contacts lookup — it REFUSES rather than
guesses: two people under one name is a question for the user, never the
first hit — with Kira's (Leoexxe13) rule for near matches: a fuzzy match wins
only when it is good enough AND clearly ahead of the runner-up; otherwise the
user gets a numbered list to pick from. Phone numbers are shown to the model
as their last four digits only (Nathan123y's masked handle), and emergency
numbers are never dialled or written to by the assistant.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from memory.config_manager import CONFIG_DIR

PATH = CONFIG_DIR / "contacts.json"

FUZZY_MIN = 0.78         # a near match below this is not offered as THE person
FUZZY_MARGIN = 0.08      # …and must lead the next one by this much

_lock = threading.Lock()

# Words a spoken name comes wrapped in: "my wife", "мою дружину", "the boss".
_FILLER = {"my", "the", "мій", "моя", "моє", "мою", "мого", "моєї", "моєму", "моїй",
           "мої", "моїм", "мой", "моя", "мою", "моего", "моей"}

# Numbers the assistant never calls or writes to: police, fire, ambulance,
# gas (Ukraine 101–104), and the international / other countries' ones.
_EMERGENCY = {"112", "911", "999", "000", "101", "102", "103", "104", "100", "110",
              "118", "119", "15", "17", "18"}


@dataclass
class Contact:
    name: str
    aliases: list[str] = field(default_factory=list)
    phone: str = ""
    apps: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.aliases:
            d["aliases"] = list(self.aliases)
        if self.phone:
            d["phone"] = self.phone
        if self.apps:
            d["apps"] = dict(self.apps)
        return d

    def in_app(self, app: str) -> str:
        """The name as `app` shows this person ('' app: the contact's name)."""
        return (self.apps.get(app) or "").strip() or self.name

    def masked(self) -> str:
        return masked(self.phone)


@dataclass
class Found:
    contact: Optional[Contact]      # the one person, or None
    candidates: list[Contact]       # several equally likely, or near misses
    why: str = ""                   # what to tell the model when contact is None
    ambiguous: bool = False         # candidates all fit: the user must pick one


# ── the file ────────────────────────────────────────────────────────────────

def load() -> list[Contact]:
    try:
        data = json.loads(Path(PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[Contacts] {PATH.name} is unreadable: {e}")
        return []
    out = []
    for row in data.get("contacts", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict) or not str(row.get("name", "")).strip():
            continue
        apps = row.get("apps") if isinstance(row.get("apps"), dict) else {}
        out.append(Contact(name=str(row["name"]).strip(),
                           aliases=[str(a).strip() for a in row.get("aliases", []) if str(a).strip()],
                           phone=normalize_phone(str(row.get("phone", ""))),
                           apps={str(k).lower(): str(v) for k, v in apps.items() if str(v).strip()}))
    return out


def save(contacts: list[Contact]) -> None:
    Path(PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(PATH).with_suffix(".tmp")
    tmp.write_text(json.dumps({"contacts": [c.as_dict() for c in contacts]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)


# ── phone numbers ───────────────────────────────────────────────────────────

def normalize_phone(text: str) -> str:
    """'+38 (067) 123-45-67' → '+380671234567'; '' when there are no digits.
    No country is guessed: a number without + stays as dialled."""
    t = str(text or "").strip()
    digits = re.sub(r"\D", "", t)
    if not digits:
        return ""
    if t.startswith("+"):
        return "+" + digits
    if digits.startswith("00") and len(digits) > 6:
        return "+" + digits[2:]
    return digits


def international(phone: str) -> str:
    """'+380671234567', or '' when the number has no country code."""
    p = normalize_phone(phone)
    return p if p.startswith("+") and len(p) >= 9 else ""


def masked(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return f"…{d[-4:]}" if len(d) >= 4 else ("…" if d else "")


def is_emergency(phone: str) -> bool:
    d = re.sub(r"\D", "", phone or "")
    return bool(d) and d in _EMERGENCY


def looks_like_phone(text: str) -> bool:
    t = str(text or "").strip()
    return bool(re.fullmatch(r"[+\d][\d\s().\-]{5,}", t)) and len(re.sub(r"\D", "", t)) >= 7


# ── who is meant ────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s'’-]", " ", str(text or "").casefold().replace("ё", "е"))
    words = [w for w in t.split() if w not in _FILLER]
    return " ".join(words)


def _score(query: str, c: Contact) -> float:
    names = [c.name, *c.aliases, *c.apps.values()]
    best = 0.0
    q_words = query.split()
    for n in names:
        n = _norm(n)
        if not n:
            continue
        if n == query:
            return 1.0
        words = n.split()
        if q_words and all(w in words for w in q_words):
            best = max(best, 0.9)                 # "Оля" of "Оля Петренко"
        best = max(best, difflib.SequenceMatcher(None, query, n).ratio() * 0.95)
        for w in words:                           # a misheard first or last name
            if len(q_words) == 1 and len(w) >= 3:
                best = max(best, difflib.SequenceMatcher(None, query, w).ratio() * 0.9)
    return best


def resolve(query: str, contacts: Optional[list[Contact]] = None) -> Found:
    """The one contact `query` means, or why not (several, or none)."""
    q = _norm(query)
    if not q:
        return Found(None, [], "no name was given")
    contacts = load() if contacts is None else contacts
    if looks_like_phone(query):
        p = normalize_phone(query)
        same = [c for c in contacts if c.phone and c.phone == p]
        if len(same) == 1:
            return Found(same[0], [])
        return Found(Contact(name=p, phone=p), [])
    scored = sorted(((_score(q, c), c) for c in contacts), key=lambda s: -s[0])
    exact = [c for s, c in scored if s >= 1.0]
    if len(exact) == 1:
        return Found(exact[0], [])
    if len(exact) > 1:
        return Found(None, exact, f"more than one contact is called '{query}'", True)
    whole = [c for s, c in scored if s >= 0.9]
    if len(whole) == 1:
        return Found(whole[0], [])
    if len(whole) > 1:
        return Found(None, whole, f"'{query}' fits more than one contact", True)
    if scored and scored[0][0] >= FUZZY_MIN and (
            len(scored) == 1 or scored[0][0] - scored[1][0] >= FUZZY_MARGIN):
        return Found(scored[0][1], [])
    near = [c for s, c in scored[:5] if s >= 0.55]
    if near:
        return Found(None, near, f"no contact is clearly '{query}'")
    return Found(None, [], f"there is no contact called '{query}'")


def describe(cs: list[Contact]) -> str:
    """'1) Оля Петренко (…4567); 2) Оля Коваль' — for the user to pick from."""
    return "; ".join(f"{i}) {c.name}{f' ({c.masked()})' if c.phone else ''}"
                     for i, c in enumerate(cs, 1))


# ── changes (act now, undo on request — core/undo.py) ───────────────────────

def add(name: str, phone: str = "", aliases: Optional[list[str]] = None,
        app: str = "", app_name: str = "") -> tuple[Contact, bool, list[Contact]]:
    """Add a contact or update the one with exactly this name.
    (contact, created?, the list as it was — for undo)."""
    with _lock:
        before = load()
        now = [Contact(c.name, list(c.aliases), c.phone, dict(c.apps)) for c in before]
        key = _norm(name)
        c = next((c for c in now if _norm(c.name) == key), None)
        created = c is None
        if c is None:
            c = Contact(name=name.strip())
            now.append(c)
        if phone:
            c.phone = normalize_phone(phone)
        for a in aliases or []:
            if a.strip() and _norm(a) not in {_norm(x) for x in c.aliases}:
                c.aliases.append(a.strip())
        if app and app_name:
            c.apps[app.lower()] = app_name.strip()
        save(now)
        return c, created, before


def rename(c: Contact, new_name: str) -> list[Contact]:
    """Give exactly this contact a new name; the list as it was, for undo."""
    with _lock:
        before = load()
        now = [Contact(x.name, list(x.aliases), x.phone, dict(x.apps)) for x in before]
        for x in now:
            if x.as_dict() == c.as_dict():
                x.name = new_name.strip()
        save(now)
        return before


def remove(c: Contact) -> list[Contact]:
    """Remove exactly this contact; the list as it was, for undo."""
    with _lock:
        before = load()
        save([x for x in before if x.as_dict() != c.as_dict()])
        return before


def restore(contacts: list[Contact]) -> None:
    with _lock:
        save(contacts)
