"""
memory/store.py — the SQLite file everything JARVIS remembers lives in.

WHY ONE SQLITE FILE
    Facts, session summaries, the notes index and the vectors that make them
    searchable by meaning sit in one database, `memory/memory.db`. A fact, its
    vector and its full-text entry are written in ONE transaction, and a delete
    trigger removes the vector with the row — so "forget X" can never leave a
    searchable copy behind in a second store. Dedicated vector databases
    (Chroma, LanceDB, Qdrant) were weighed and left out: at this scale (a few
    thousand vectors after years of use; the user's 83-note vault is ~1,800
    chunks) a brute-force numpy scan takes 1–3 ms, and each of them would be a
    second source of truth to keep in step, plus 100 MB+ of dependencies.
    The vector scan sits behind memory/search.py's VectorIndex, so a later
    switch (sqlite-vec, another engine) touches one class.

SCHEMA, AND WHERE EACH IDEA CAME FROM
    facts      one row per value ever held for a slot (category, key). A new
               value supersedes the old one instead of overwriting it, so the
               history survives and a wrong save can be reverted
               (TeddyDARKVADOR's slot supersede; bfrpaulondev's provenance —
               `source`, `evidence`). `status` is active | proposed |
               superseded. Only active facts reach the prompt; proposed ones are
               facts the model wrote that the user's own words did not back
               (reyesservice34-hue's propose/accept), waiting on the panel.
    sessions   one summary per conversation, kept for good.
    notes /    the indexed Obsidian vault: one row per file (mtime and size for
    chunks     incremental rescans, BlackStarG5's document index) and its
               heading-aware chunks (aonizai's documents/chunks).
    vectors    one embedding per (kind, ref), tagged with the model that made
               it and a hash of the text it was made from, so a model switch or
               an edited row is re-embedded instead of silently dropping out of
               search (the bug found in TeddyDARKVADOR's store).
    state      small named JSON values (the news monitors).
    *_fts      FTS5 indexes kept in step by triggers. `unicode61` folds
               Cyrillic case; `remove_diacritics 2` only touches Latin letters,
               so й/и and ї/і stay distinct. Every table has an INTEGER primary
               key: an external-content FTS index addresses rows by rowid, and
               the implicit rowid of a TEXT-keyed table may be renumbered by
               VACUUM.

THREADS
    One connection shared behind an RLock (`check_same_thread=False`): the
    asyncio loop, the Qt thread and the background indexer all write, and
    SQLite serialises writers anyway. Every statement is short; nothing slow
    (embedding, file reading) ever runs while the lock is held.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


DB_PATH = _base_dir() / "memory" / "memory.db"

_TOKENIZER = "unicode61 remove_diacritics 2"

# Each entry migrates the database from version i to i+1. Append only: a
# shipped migration is never edited, because databases out there already ran it.
_MIGRATIONS: list[str] = [
    f"""
    CREATE TABLE facts (
        id          INTEGER PRIMARY KEY,
        category    TEXT NOT NULL,
        key         TEXT NOT NULL,
        value       TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'active',
        source      TEXT NOT NULL DEFAULT 'model',
        evidence    TEXT NOT NULL DEFAULT '',
        created     TEXT NOT NULL,
        updated     TEXT NOT NULL,
        replaced_by INTEGER REFERENCES facts(id) ON DELETE SET NULL
    );
    CREATE UNIQUE INDEX facts_active   ON facts(category, key) WHERE status = 'active';
    CREATE UNIQUE INDEX facts_proposed ON facts(category, key) WHERE status = 'proposed';
    CREATE INDEX facts_slot ON facts(category, key);

    CREATE VIRTUAL TABLE facts_fts USING fts5(
        category, key, value, content='facts', content_rowid='id',
        tokenize='{_TOKENIZER}');
    CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
        INSERT INTO facts_fts(rowid, category, key, value)
        VALUES (new.id, new.category, new.key, new.value);
    END;
    CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
        VALUES ('delete', old.id, old.category, old.key, old.value);
        DELETE FROM vectors WHERE kind = 'fact' AND ref = old.id;
    END;
    CREATE TRIGGER facts_au AFTER UPDATE OF category, key, value ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, category, key, value)
        VALUES ('delete', old.id, old.category, old.key, old.value);
        INSERT INTO facts_fts(rowid, category, key, value)
        VALUES (new.id, new.category, new.key, new.value);
    END;

    CREATE TABLE sessions (
        id       INTEGER PRIMARY KEY,
        ended    TEXT NOT NULL,
        summary  TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT '',
        briefed  INTEGER NOT NULL DEFAULT 0
    );
    CREATE VIRTUAL TABLE sessions_fts USING fts5(
        summary, content='sessions', content_rowid='id', tokenize='{_TOKENIZER}');
    CREATE TRIGGER sessions_ai AFTER INSERT ON sessions BEGIN
        INSERT INTO sessions_fts(rowid, summary) VALUES (new.id, new.summary);
    END;
    CREATE TRIGGER sessions_ad AFTER DELETE ON sessions BEGIN
        INSERT INTO sessions_fts(sessions_fts, rowid, summary)
        VALUES ('delete', old.id, old.summary);
        DELETE FROM vectors WHERE kind = 'session' AND ref = old.id;
    END;

    CREATE TABLE notes (
        id       INTEGER PRIMARY KEY,
        path     TEXT NOT NULL UNIQUE,
        title    TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size     INTEGER NOT NULL
    );
    CREATE TABLE chunks (
        id      INTEGER PRIMARY KEY,
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        ord     INTEGER NOT NULL,
        heading TEXT NOT NULL DEFAULT '',
        text    TEXT NOT NULL
    );
    CREATE INDEX chunks_note ON chunks(note_id);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        heading, text, content='chunks', content_rowid='id', tokenize='{_TOKENIZER}');
    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, heading, text) VALUES (new.id, new.heading, new.text);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, heading, text)
        VALUES ('delete', old.id, old.heading, old.text);
        DELETE FROM vectors WHERE kind = 'chunk' AND ref = old.id;
    END;

    CREATE TABLE vectors (
        kind  TEXT NOT NULL,
        ref   INTEGER NOT NULL,
        model TEXT NOT NULL,
        hash  TEXT NOT NULL,
        vec   BLOB NOT NULL,
        PRIMARY KEY (kind, ref)
    );

    CREATE TABLE state (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]

SCHEMA_VERSION = len(_MIGRATIONS)


class Store:
    """A thin, thread-safe wrapper over one sqlite3 connection."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # isolation_level=None: transactions are opened explicitly in tx(), so
        # a read never leaves an implicit transaction holding the write lock.
        self._db = sqlite3.connect(str(path), check_same_thread=False,
                                   isolation_level=None, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            have = self._db.execute("PRAGMA user_version").fetchone()[0]
            for i in range(have, SCHEMA_VERSION):
                with self.tx():
                    for statement in _split(_MIGRATIONS[i]):
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {i + 1}")

    @contextmanager
    def tx(self):
        """One write transaction. Nested calls join the outer one."""
        with self._lock:
            if self._db.in_transaction:
                yield self._db
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def all(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def run(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.tx() as db:
            return db.execute(sql, args)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _split(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies whole.

    executescript() would do this for us, but it COMMITs any open transaction
    first — and a migration must run inside one, so a crash halfway leaves the
    previous version intact rather than half a schema."""
    out, buf, depth = [], [], 0
    for line in script.splitlines():
        s = line.strip()
        if not s:
            continue
        buf.append(line)
        up = s.upper()
        if up.startswith("CREATE TRIGGER"):
            depth += 1
        if depth and up == "END;":
            depth -= 1
        if not depth and s.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


_store: Store | None = None
_store_lock = threading.Lock()


def get() -> Store:
    """The process-wide store, opened (and migrated) on first use."""
    global _store
    with _store_lock:
        if _store is None:
            _store = Store(DB_PATH)
        return _store


def use(store: Store | None) -> None:
    """Tests: point every caller at another store (or reset to lazy open)."""
    global _store
    with _store_lock:
        if _store is not None and _store is not store:
            try:
                _store.close()
            except Exception:
                pass
        _store = store
