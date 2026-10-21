"""
memory/memory_manager.py — what JARVIS knows about the user, and how it is
found again.

The facts live in SQLite (memory/store.py) and are searched by words and by
meaning (memory/search.py). This module keeps the API the rest of the program
already used — load_memory / update_memory / format_memory_for_prompt /
search_memory / remember / forget / session summaries — so main.py, the
proactive engine and the HUD did not have to learn a new one.

What changed underneath, and why:

  * A new value for a slot (category, key) SUPERSEDES the old one instead of
    overwriting it. The history stays, so a wrong save can be reverted and the
    panel can show what a fact used to be.
  * A fact the model saves is checked against what the user actually said
    (memory/trust.py). Backed facts are active and reach the prompt; the rest
    are proposals, searchable but marked unconfirmed, until the user accepts
    them on the MEMORY panel.
  * Values are kept in the words the user said them in. The old store forced
    English values so that its word search could find them; search by meaning
    matches "сестра" to "sister" in either direction, and a name stays the way
    the user pronounces it.
  * Session summaries are kept for good (there used to be at most three) and
    are searchable — the recall_conversation tool reads them.
  * memory/long_term.json is migrated once, on first use, and kept as
    long_term.json.bak.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from memory import search as _search
from memory import store as _store
from memory.store import _base_dir

BASE_DIR    = _base_dir()
LEGACY_JSON = BASE_DIR / "memory" / "long_term.json"

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
MAX_VALUE_LENGTH = 380

# A runaway guard, not a feature limit: nothing normal reaches it; a bug saving
# in a loop does. History (superseded values) goes first.
MAX_FACT_ROWS = 5000

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect. Storage and prompt budget are
# separate concerns: the store keeps everything (MAX_FACT_ROWS above), and
#
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

_ready_lock = threading.Lock()
_ready_for: _store.Store | None = None


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _db() -> _store.Store:
    """The store, with the legacy JSON migrated into it on first use."""
    global _ready_for
    st = _store.get()
    if _ready_for is not st:
        with _ready_lock:
            if _ready_for is not st:
                _migrate_legacy(st)
                _ready_for = st
    return st


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


# ── Migration from long_term.json ─────────────────────────────────────────────

def _migrate_legacy(st: _store.Store, path: Path | None = None) -> int:
    """Copy memory/long_term.json into the store once, verify the copy, then
    rename the file to .bak. Returns how many facts were moved.

    The recipe is maseko-lucky-9's (back up first, a sentinel so it never runs
    twice, a round-trip check before the original is retired), in plain
    sqlite3. A file that does not parse is left where it is and reported."""
    path = path or LEGACY_JSON
    if st.one("SELECT 1 FROM state WHERE name = 'legacy_json'"):
        return 0
    if not path.exists():
        st.run("INSERT OR REPLACE INTO state(name, value) VALUES ('legacy_json', ?)",
               (json.dumps({"migrated": _today(), "facts": 0, "note": "no file"}),))
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except Exception as e:
        print(f"[Memory] ⚠️ {path.name} could not be read, left as it is: {e}")
        return 0

    facts: list[tuple[str, str, str, str]] = []
    for cat, items in data.items():
        if cat in ("sessions", "monitors") or not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            upd = (entry.get("updated") if isinstance(entry, dict) else "") or _today()
            facts.append((cat if cat in CATEGORIES else "notes", str(key), val, upd))
    sessions = [s for s in (data.get("sessions") or []) if isinstance(s, dict)
                and str(s.get("summary", "")).strip()]
    monitors = data.get("monitors") if isinstance(data.get("monitors"), dict) else None

    with st.tx() as db:
        for cat, key, val, upd in facts:
            db.execute("UPDATE facts SET status = 'superseded' WHERE category = ? "
                       "AND key = ? AND status = 'active'", (cat, key))
            db.execute("INSERT INTO facts(category, key, value, status, source, "
                       "created, updated) VALUES (?, ?, ?, 'active', 'migrated', ?, ?)",
                       (cat, key, val[:MAX_VALUE_LENGTH], upd, upd))
        for s in sessions:
            db.execute("INSERT INTO sessions(ended, summary, language) VALUES (?, ?, ?)",
                       (str(s.get("date") or _today()), str(s["summary"]),
                        str(s.get("language") or "")))
        if monitors is not None:
            db.execute("INSERT OR REPLACE INTO state(name, value) VALUES ('monitors', ?)",
                       (json.dumps(monitors, ensure_ascii=False),))
        moved = db.execute("SELECT COUNT(*) FROM facts WHERE source = 'migrated' "
                           "AND status = 'active'").fetchone()[0]
        if moved < len({(c, k) for c, k, _, _ in facts}):
            raise RuntimeError(f"memory migration copied {moved} of {len(facts)} facts")
        db.execute("INSERT OR REPLACE INTO state(name, value) VALUES ('legacy_json', ?)",
                   (json.dumps({"migrated": _today(), "facts": len(facts),
                                "sessions": len(sessions)}),))

    bak = path.with_name(path.name + ".bak")
    if bak.exists():
        bak = path.with_name(f"{path.name}.{datetime.now():%Y%m%d-%H%M%S}.bak")
    try:
        path.rename(bak)
    except OSError as e:
        print(f"[Memory] ⚠️ Migrated, but could not rename {path.name}: {e}")
    print(f"[Memory] 📦 Moved {len(facts)} facts and {len(sessions)} session "
          f"summaries from {path.name} into {st.path.name} (original kept as {bak.name})")
    _search.INDEXER.poke()
    return len(facts)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


def _clean_key(key: str) -> str:
    k = re.sub(r"[^\w]+", "_", str(key or "").strip().lower()).strip("_")
    return k[:60] or "note"


def _clean_category(category: str) -> str:
    c = str(category or "").strip().lower()
    return c if c in CATEGORIES else "notes"


def _truncate_value(val: str) -> str:
    val = " ".join(str(val or "").split())
    if len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


# ── Facts ─────────────────────────────────────────────────────────────────────

@dataclass
class Saved:
    """What save_fact did, enough to undo it."""
    id: int | None
    category: str
    key: str
    value: str
    status: str               # active | proposed | unchanged
    previous_id: int | None = None
    previous_value: str | None = None


def save_fact(category: str, key: str, value: str, *, source: str = "model",
              evidence: str = "", status: str = "active") -> Saved:
    """Store a value for (category, key).

    status='active' supersedes the slot's current value (kept as history) and
    drops a pending proposal for the slot; status='proposed' replaces any
    earlier proposal and leaves the active value alone."""
    category, key = _clean_category(category), _clean_key(key)
    value = _truncate_value(value)
    if not value:
        return Saved(None, category, key, value, "unchanged")
    now = _today()
    st = _db()
    with st.tx() as db:
        active = db.execute("SELECT id, value FROM facts WHERE category = ? AND key = ? "
                            "AND status = 'active'", (category, key)).fetchone()
        if active and active["value"] == value:
            db.execute("UPDATE facts SET updated = ? WHERE id = ?", (now, active["id"]))
            if status == "active" and source == "user":
                db.execute("UPDATE facts SET source = 'user', evidence = CASE WHEN "
                           "evidence = '' THEN ? ELSE evidence END WHERE id = ?",
                           (evidence, active["id"]))
            db.execute("DELETE FROM facts WHERE category = ? AND key = ? AND "
                       "status = 'proposed' AND value = ?", (category, key, value))
            return Saved(active["id"], category, key, value, "unchanged")

        db.execute("DELETE FROM facts WHERE category = ? AND key = ? AND status = 'proposed'",
                   (category, key))
        prev_id = prev_val = None
        if status == "active":
            if active:
                prev_id, prev_val = active["id"], active["value"]
                db.execute("UPDATE facts SET status = 'superseded' WHERE id = ?", (prev_id,))
                db.execute("DELETE FROM vectors WHERE kind = 'fact' AND ref = ?", (prev_id,))
        else:
            status = "proposed"
        cur = db.execute(
            "INSERT INTO facts(category, key, value, status, source, evidence, "
            "created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (category, key, value, status, source, evidence[:240], now, now))
        new_id = cur.lastrowid
        if prev_id is not None:
            db.execute("UPDATE facts SET replaced_by = ? WHERE id = ?", (new_id, prev_id))
    _trim_runaway()
    _search.INDEXER.poke()
    return Saved(new_id, category, key, value, status, prev_id, prev_val)


# The transcript of what the user just said can arrive AFTER the model's
# save_memory call: seen live (2026-10-21), "мого брата звати Юра" was saved as
# a suggestion while a long sentence about the family, already transcribed,
# passed. An unbacked save is therefore judged a second time this long after.
RECHECK_S = 3.0


def save_from_model(category: str, key: str, value: str,
                    utterances: list[str] | None = None,
                    on_final=None, recheck_s: float | None = None) -> Saved:
    """A save_memory call: check it against what the user said, then store it
    as active (backed) or proposed (not backed).

    A proposed save is checked again after RECHECK_S against the words heard by
    then, and confirmed if they back it. on_final(saved) is called once the
    outcome is settled — at once for a backed save, after the recheck
    otherwise — so the HUD only announces suggestions that really wait for the
    user."""
    from memory import trust
    from core import activity
    if utterances is None:
        utterances = activity.recent_user_text()
    category, key = _clean_category(category), _clean_key(key)
    status, evidence = trust.judge(category, key, _truncate_value(value), utterances)
    source = "observed" if (category, key) in trust._EXEMPT else (
        "user" if status == "active" else "model")
    saved = save_fact(category, key, value, source=source, evidence=evidence, status=status)
    if saved.status != "proposed":
        if on_final:
            on_final(saved)
        return saved

    def _recheck():
        final = saved
        try:
            row = _db().one("SELECT id FROM facts WHERE id = ? AND status = 'proposed'",
                            (saved.id,))
            heard = activity.recent_user_text()
            if row:
                st, ev = trust.judge(category, key, saved.value, heard)
                if st == "active":
                    final = save_fact(category, key, saved.value, source="user", evidence=ev)
                    final.status = "active"
                    print(f"[Memory] ✔ Confirmed by what the user said: {category}/{key}")
                else:
                    print(f"[Memory] ? Not backed by the last words heard "
                          f"({' | '.join(h[:60] for h in heard[-3:]) or 'nothing'}): "
                          f"{category}/{key} = {saved.value}")
        except Exception as e:
            print(f"[Memory] ⚠️ Recheck failed: {e}")
        if on_final:
            on_final(final)

    delay = RECHECK_S if recheck_s is None else recheck_s
    if delay <= 0:
        _recheck()
    else:
        t = threading.Timer(delay, _recheck)
        t.daemon = True
        t.start()
    return saved


def revert(saved: Saved) -> str:
    """Undo one save_fact: drop the new row and bring the previous value back."""
    if saved.id is None or saved.status == "unchanged":
        return "Nothing to undo."
    st = _db()
    with st.tx() as db:
        db.execute("DELETE FROM facts WHERE id = ?", (saved.id,))
        if saved.previous_id is not None:
            db.execute("UPDATE facts SET status = 'active', replaced_by = NULL "
                       "WHERE id = ? AND NOT EXISTS (SELECT 1 FROM facts WHERE "
                       "category = ? AND key = ? AND status = 'active')",
                       (saved.previous_id, saved.category, saved.key))
    _search.INDEXER.poke()
    return f"Restored {saved.category}/{saved.key}" + (
        f" to {saved.previous_value}" if saved.previous_value else "")


def accept(fact_id: int) -> str:
    """The user confirmed a proposed fact: it becomes the active value."""
    row = _db().one("SELECT category, key, value, evidence FROM facts "
                    "WHERE id = ? AND status = 'proposed'", (fact_id,))
    if not row:
        return "That suggestion is gone."
    save_fact(row["category"], row["key"], row["value"], source="user",
              evidence=row["evidence"] or "confirmed on the MEMORY panel")
    return f"Confirmed: {row['category']}/{row['key']} = {row['value']}"


def reject(fact_id: int) -> str:
    row = _db().one("SELECT category, key FROM facts WHERE id = ? AND status = 'proposed'",
                    (fact_id,))
    if not row:
        return "That suggestion is gone."
    _db().run("DELETE FROM facts WHERE id = ?", (fact_id,))
    return f"Dropped the suggestion {row['category']}/{row['key']}"


def history(category: str, key: str) -> list[dict]:
    """Every value the slot has held, newest first."""
    rows = _db().all("SELECT value, status, source, updated FROM facts WHERE "
                     "category = ? AND key = ? ORDER BY id DESC",
                     (_clean_category(category), _clean_key(key)))
    return [dict(r) for r in rows]


def _trim_runaway() -> None:
    st = _db()
    n = st.one("SELECT COUNT(*) FROM facts")[0]
    if n <= MAX_FACT_ROWS:
        return
    extra = n - MAX_FACT_ROWS
    with st.tx() as db:
        old = db.execute("SELECT id FROM facts WHERE status = 'superseded' "
                         "ORDER BY id LIMIT ?", (extra,)).fetchall()
        for r in old:
            db.execute("DELETE FROM facts WHERE id = ?", (r["id"],))
        extra -= len(old)
        dropped = []
        if extra > 0:
            for r in db.execute("SELECT id, category, key FROM facts ORDER BY "
                                "updated, id LIMIT ?", (extra,)).fetchall():
                db.execute("DELETE FROM facts WHERE id = ?", (r["id"],))
                dropped.append(f"{r['category']}/{r['key']}")
    if dropped:
        print(f"[Memory] 🗑️  Trimmed {len(dropped)} oldest facts")
        if _trim_notifier:
            try:
                _trim_notifier(
                    f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                    f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})")
            except Exception:
                pass


# ── The dict view the rest of the program reads ───────────────────────────────

def load_memory() -> dict:
    """Active facts as {category: {key: {"value", "updated"}}}."""
    out: dict = {c: {} for c in CATEGORIES}
    for r in _db().all("SELECT category, key, value, updated FROM facts "
                       "WHERE status = 'active' ORDER BY id"):
        out.setdefault(r["category"], {})[r["key"]] = {
            "value": r["value"], "updated": r["updated"]}
    return out


def update_memory(memory_update: dict) -> dict:
    """Store {category: {key: value | {"value": ...}}} as backed facts. Kept for
    callers that write directly (not the model, which goes through
    save_from_model)."""
    if isinstance(memory_update, dict):
        for cat, items in memory_update.items():
            if not isinstance(items, dict):
                continue
            for key, value in items.items():
                val = _entry_value(value)
                if val:
                    save_fact(cat, key, val, source="user")
    return load_memory()


def remember(key: str, value: str, category: str = "notes") -> str:
    s = save_fact(category, key, value, source="user")
    return f"Remembered: {s.category}/{s.key} = {s.value}"


def forget(key: str, category: str = "notes") -> str:
    """Delete a slot outright — its value, its history and any proposal. The
    MEMORY panel's ✕ means gone, not hidden."""
    cat, k = _clean_category(category), _clean_key(key)
    cur = _db().run("DELETE FROM facts WHERE category = ? AND key = ?", (cat, k))
    return f"Forgotten: {cat}/{k}" if cur.rowcount else f"Not found: {cat}/{k}"


forget_memory = forget


# "сестра" scored 0.41 against "relationships — sister: Олена…" and the next
# fact 0.21; unrelated subjects stayed under 0.2.
FORGET_FLOOR = 0.35


def find_about(subject: str, limit: int = 20) -> list[dict]:
    """Active and proposed facts about `subject`: matched by words, or by
    meaning with a stricter floor than recall uses — this list is about to be
    deleted, so a near miss must not be on it (Giljacob67's forget wiped every
    episode sharing any word with the query)."""
    st = _db()
    allowed = {r["id"] for r in st.all(
        "SELECT id FROM facts WHERE status IN ('active', 'proposed')")}
    if not allowed or not (subject or "").strip():
        return []
    hits = _search.hybrid("fact", "facts_fts", subject, k=limit, allowed=allowed,
                          floor=FORGET_FLOOR)
    ids = [h.ref for h in hits if h.lexical or (h.cosine or 0) >= FORGET_FLOOR]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = st.all(f"SELECT * FROM facts WHERE id IN ({marks})", ids)
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def forget_rows(rows: list[dict]) -> list[dict]:
    """Delete these facts with their whole history; returns every deleted row
    so restore_rows() can put them back."""
    st = _db()
    gone: list[dict] = []
    with st.tx() as db:
        for r in rows:
            for full in db.execute("SELECT * FROM facts WHERE category = ? AND key = ?",
                                   (r["category"], r["key"])).fetchall():
                gone.append(dict(full))
            db.execute("DELETE FROM facts WHERE category = ? AND key = ?",
                       (r["category"], r["key"]))
    return gone


def restore_rows(rows: list[dict]) -> str:
    st = _db()
    with st.tx() as db:
        for r in sorted(rows, key=lambda x: x["id"]):
            db.execute(
                "INSERT OR IGNORE INTO facts(id, category, key, value, status, source, "
                "evidence, created, updated, replaced_by) VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (r["id"], r["category"], r["key"], r["value"], r["status"],
                 r["source"], r["evidence"], r["created"], r["updated"]))
        for r in rows:
            if r.get("replaced_by"):
                db.execute("UPDATE facts SET replaced_by = ? WHERE id = ? AND EXISTS "
                           "(SELECT 1 FROM facts WHERE id = ?)",
                           (r["replaced_by"], r["id"], r["replaced_by"]))
    _search.INDEXER.poke()
    slots = sorted({f"{r['category']}/{r['key']}" for r in rows})
    return "Remembered again: " + ", ".join(slots)


# ── The prompt block ──────────────────────────────────────────────────────────

# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    It sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Only ACTIVE facts are in `memory` (load_memory): a proposal the user never
    confirmed is not something to tell the model as known."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

_UNCONFIRMED = (" [UNCONFIRMED — saved by you, never said by the user; "
                "ask before relying on it]")


def _semantic_note() -> str:
    if _search.semantic_ready():
        return ""
    return (" (Search by meaning is still loading, so only exact words were "
            "searched — a fact worded differently may still exist.)")


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`, by words and by meaning, in any
    language. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    st = _db()
    rows = st.all("SELECT id, category, key, value, status, updated FROM facts "
                  "WHERE status IN ('active', 'proposed')")
    if not rows:
        return "I have not stored anything about this person yet."
    by_id = {r["id"]: r for r in rows}

    if (query or "").strip():
        hits = _search.hybrid("fact", "facts_fts", query, k=max(1, limit),
                              allowed=set(by_id))
        picked = [by_id[h.ref] for h in hits]
        if not picked:
            return f"Nothing stored about '{query}'." + _semantic_note()
        head = f"Stored facts matching '{query}':"
        more = ""
    else:
        ordered = sorted(rows, key=lambda r: (r["status"] != "active", r["category"], r["key"]))
        picked = ordered[:max(1, limit) * 3]
        head = "Everything currently stored:"
        more = (f"\n(+{len(ordered) - len(picked)} more — search with a narrower keyword)"
                if len(ordered) > len(picked) else "")

    lines = [f"{r['category']}/{_pretty(r['key'])}: {r['value']} (since {r['updated']})"
             + (_UNCONFIRMED if r["status"] == "proposed" else "") for r in picked]
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what JARVIS knows, and when it learned it.
    Suggestions first (they wait for the user), then newest first."""
    st = _db()
    hist = {(r["category"], r["key"]): r["n"] for r in st.all(
        "SELECT category, key, COUNT(*) AS n FROM facts WHERE status = 'superseded' "
        "GROUP BY category, key")}
    rows = []
    for r in st.all("SELECT * FROM facts WHERE status IN ('active', 'proposed')"):
        rows.append({
            "id":       r["id"],
            "category": r["category"],
            "key":      r["key"],
            "value":    r["value"],
            "updated":  r["updated"],
            "status":   r["status"],
            "source":   r["source"],
            "evidence": r["evidence"],
            "history":  hist.get((r["category"], r["key"]), 0),
        })
    rows.sort(key=lambda r: (r["status"] != "proposed", r["updated"] or "0000-00-00", r["id"]))
    proposed = [r for r in rows if r["status"] == "proposed"]
    active = [r for r in rows if r["status"] != "proposed"]
    active.sort(key=lambda r: (r["updated"] or "0000-00-00", r["id"]), reverse=True)
    return proposed + active


def stats() -> dict:
    st = _db()
    def n(sql, args=()):
        return st.one(sql, args)[0]
    return {
        "facts":    n("SELECT COUNT(*) FROM facts WHERE status = 'active'"),
        "proposed": n("SELECT COUNT(*) FROM facts WHERE status = 'proposed'"),
        "sessions": n("SELECT COUNT(*) FROM sessions"),
    }


# ── Session memory ─────────────────────────────────────────────────────────────

_SECRET_RE = re.compile(
    r"(?i)\b(password|passcode|passphrase|pin|otp|one[- ]time code|token|api key|"
    r"secret key|пароль|пін|пін-код|код доступу|одноразовий код|токен|ключ api)"
    r"(\s*(?:is|was|:|=|—|-|це|був|була)?\s*)(\S+)")


def redact(text: str) -> str:
    """Hide secrets before a summary is stored for good.

    A word like "password" keeps its own text and loses the value after it.
    syamsulmuiin's journal had the right idea and a broken pattern: it turned
    "my password is hunter2" into "password [REDACTED] hunter2", hiding only
    "is". Keys, tokens, e-mail addresses and the home path go through the
    error journal's redaction as well."""
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text or "")
    try:
        from core import health
        out = health.redact(out)
    except Exception:
        pass
    return out


def save_session_summary(summary: str, language: str = "") -> None:
    """Store a conversation's summary. Kept for good and searchable."""
    summary = redact((summary or "").strip())
    if not summary:
        return
    _db().run("INSERT INTO sessions(ended, summary, language) VALUES (?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M"), summary[:900], language or ""))
    _search.INDEXER.poke()
    print(f"[Memory] 📝 Session saved: {summary[:60]}…")


def pop_last_session(max_age_days: int = 14) -> dict | None:
    """The newest summary the morning briefing has not mentioned yet, marked
    as mentioned so it is never repeated. Older unmentioned ones are marked
    too: a briefing should say what happened last time, not three weeks ago."""
    st = _db()
    with st.tx() as db:
        row = db.execute("SELECT id, ended, summary, language FROM sessions "
                         "WHERE briefed = 0 ORDER BY id DESC LIMIT 1").fetchone()
        db.execute("UPDATE sessions SET briefed = 1 WHERE briefed = 0")
    if not row:
        return None
    try:
        ended = datetime.strptime(row["ended"][:10], "%Y-%m-%d")
        if datetime.now() - ended > timedelta(days=max_age_days):
            return None
    except ValueError:
        pass
    return {"date": row["ended"][:10], "summary": row["summary"],
            "language": row["language"]}


def search_sessions(query: str, limit: int = 4) -> str:
    """Past conversations about `query` (empty: the most recent ones). Backs
    the recall_conversation tool."""
    st = _db()
    if not st.one("SELECT 1 FROM sessions LIMIT 1"):
        return "There are no saved conversations yet."
    if (query or "").strip():
        hits = _search.hybrid("session", "sessions_fts", query, k=max(1, limit))
        if not hits:
            return f"No saved conversation was about '{query}'." + _semantic_note()
        ids = [h.ref for h in hits]
        marks = ",".join("?" * len(ids))
        by_id = {r["id"]: r for r in st.all(
            f"SELECT id, ended, summary FROM sessions WHERE id IN ({marks})", ids)}
        rows = [by_id[i] for i in ids if i in by_id]
        head = f"Saved conversations about '{query}' (most relevant first):"
    else:
        rows = st.all("SELECT id, ended, summary FROM sessions ORDER BY id DESC LIMIT ?",
                      (max(1, limit),))
        head = "The most recent saved conversations:"
    return head + "\n" + "\n".join(f"- {r['ended']}: {r['summary']}" for r in rows)


# ── Named state (news monitors) ───────────────────────────────────────────────

def get_state(name: str, default=None):
    row = _db().one("SELECT value FROM state WHERE name = ?", (name,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def set_state(name: str, value) -> None:
    _db().run("INSERT OR REPLACE INTO state(name, value) VALUES (?, ?)",
              (name, json.dumps(value, ensure_ascii=False)))


# ── Startup ───────────────────────────────────────────────────────────────────

_started = False


def start_background() -> None:
    """Called once by main.py at startup, off the event loop: migrate the old
    JSON now rather than on the first spoken request, load the embedding model
    in the background, and start the indexer (vectors + the notes vault)."""
    global _started
    if _started:
        return
    _started = True
    from memory import embedder, notes_index
    try:
        from memory.config_manager import get_semantic_memory_enabled
        semantic = get_semantic_memory_enabled()
    except Exception:
        semantic = True
    _db()
    if semantic and embedder.available():
        if embedder.get() is None:
            embedder.use(embedder.Embedder())
        embedder.get().start(on_ready=_search.INDEXER.poke)
    elif semantic:
        print("[Memory] ⚠️ Search by meaning needs fastembed: python -m pip install fastembed")
    _search.INDEXER.add_task(notes_index.rescan)
    _search.INDEXER.start()
