"""SQLite storage. Every task, project or group is a *block*; blocks nest via parent_id."""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "nestwork.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES blocks(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'backlog',
    position     INTEGER NOT NULL DEFAULT 0,
    due_date     TEXT,
    due_time     TEXT,                      -- optional "HH:MM" on the due date
    remind       TEXT,                      -- JSON list of reminder choices; NULL = the defaults
    pomodoros    INTEGER,                   -- daily focus sessions planned for it; 0 = none (new ones); NULL = 4 (older ones)
    estimate_min INTEGER,                   -- how long it should take, in minutes (NULL = not estimated)
    repeat       TEXT,                      -- JSON repeat rule, e.g. {"freq": "weekly", "interval": 1}; NULL = once
    repeat_next  TEXT,                      -- JSON {"reopen": date, "due": date|null} once a repeating block is done
    priority     INTEGER NOT NULL DEFAULT 0,  -- 0 none, 1 low, 2 medium, 3 high
    tags         TEXT,                      -- JSON list of short labels, e.g. ["writing", "uni"]
    archived_at  TEXT,                      -- projects only: when it was archived (hidden from lists and Today)
    done_at      TEXT,                      -- when it last reached the last phase (NULL while open)
    track_done   INTEGER,                   -- optional counter, e.g. 37 of 200 videos watched (NULL = no counter)
    track_total  INTEGER,
    track_unit   TEXT,                      -- what's being counted, e.g. "videos" ('' = nothing named)
    stage_labels TEXT,
    stage_config TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_blocks_parent ON blocks(parent_id);

-- "block_id moves to target_stage once depends_on_id has reached required_stage."
CREATE TABLE IF NOT EXISTS rules (
    block_id       INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    depends_on_id  INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    required_stage TEXT NOT NULL DEFAULT 'done',
    target_stage   TEXT NOT NULL DEFAULT 'ready',
    PRIMARY KEY (block_id, depends_on_id, target_stage)
);
CREATE INDEX IF NOT EXISTS idx_rules_dep ON rules(depends_on_id);

-- App-wide settings (agent access, delete switch, a change counter open pages poll).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The chat with the Nestwork agent: Claude sessions and their messages.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id             TEXT PRIMARY KEY,          -- the Claude Code session id
    started_at     REAL NOT NULL,
    last_at        REAL NOT NULL,
    keywords       TEXT NOT NULL DEFAULT '[]',
    titles         TEXT NOT NULL DEFAULT '[]',
    context_tokens INTEGER NOT NULL DEFAULT 0,
    turns          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    text       TEXT NOT NULL,
    meta       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One-off reminders ("remind me Friday at 9 to email my professor"), optionally about a block.
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id   INTEGER REFERENCES blocks(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    remind_at  TEXT NOT NULL,                -- local time, "YYYY-MM-DDTHH:MM"
    status     TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT NOT NULL DEFAULT 'you',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Which due-date reminders were already sent (so each goes out once).
CREATE TABLE IF NOT EXISTS reminder_log (
    key     TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Changes AI agents asked for while agent access is "ask first".
CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT NOT NULL,
    tool       TEXT NOT NULL,
    args       TEXT NOT NULL,
    summary    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    result     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);

-- Focus sessions from the Pomodoro timer: what you worked on, how long, and how productive it felt.
CREATE TABLE IF NOT EXISTS focus_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id      INTEGER REFERENCES blocks(id) ON DELETE SET NULL,   -- the to-do, if one was picked
    project_id    INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,                -- what you were doing (kept if the to-do is deleted)
    project_title TEXT,
    started_at    TEXT NOT NULL,                -- local time, "YYYY-MM-DDTHH:MM:SS"
    ended_at      TEXT,                         -- NULL while running
    planned_min   REAL NOT NULL,
    focus_min     REAL NOT NULL DEFAULT 0,      -- minutes actually focused (pauses don't count)
    status        TEXT NOT NULL DEFAULT 'running',   -- running | completed | stopped
    mode          TEXT NOT NULL DEFAULT 'pomodoro',  -- pomodoro (counts down) | stopwatch (counts up, no set length)
    rating        INTEGER,                      -- 1-5: how productive it felt
    note          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_focus_started ON focus_sessions(started_at);

-- Notes & activity on a block: notes by you or your agent, and entries Nestwork adds (moves).
CREATE TABLE IF NOT EXISTS block_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'note',     -- note | event
    author      TEXT NOT NULL DEFAULT 'you',      -- you | agent
    author_name TEXT,                             -- the agent's name, for its notes
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,                    -- local time, "YYYY-MM-DDTHH:MM:SS"
    edited_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_block ON block_notes(block_id, created_at);
-- Every time a to-do was finished (kept when it's reopened later, e.g. a repeat), for the weekly review.
CREATE TABLE IF NOT EXISTS completions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id      INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    project_id    INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
    project_title TEXT,
    done_at       TEXT NOT NULL                 -- local time, "YYYY-MM-DDTHH:MM:SS"
);
CREATE INDEX IF NOT EXISTS idx_completions_at ON completions(done_at);
-- Saved weekly reviews: the numbers at the time and your reflection.
CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    data         TEXT NOT NULL,                 -- JSON summary (per project: progress, finish date, done count…)
    note         TEXT NOT NULL DEFAULT ''
);
-- Plan my day: one plan per day (items in order with start times), made by you or your agent.
CREATE TABLE IF NOT EXISTS day_plans (
    day        TEXT PRIMARY KEY,                -- "YYYY-MM-DD"
    items      TEXT NOT NULL,                   -- JSON list of {start, minutes, title, block_id, kind}
    made_by    TEXT NOT NULL DEFAULT 'you',
    updated_at TEXT NOT NULL
);
-- Today's to-dos on the HUD as your agent picked them, in order; no row = the automatic list.
CREATE TABLE IF NOT EXISTS day_picks (
    day        TEXT PRIMARY KEY,                -- "YYYY-MM-DD"
    block_ids  TEXT NOT NULL,                   -- JSON list of the picked to-do ids, in order
    items      TEXT,                            -- JSON list in order: {"block_id": id} or {"text": …, "done": bool}
    note       TEXT NOT NULL DEFAULT '',        -- why these, shown on the HUD
    made_by    TEXT NOT NULL DEFAULT 'you',
    updated_at TEXT NOT NULL
);
-- Links and files on a block (files live in data/attachments/<project name>/).
CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id   INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                   -- link | file
    title      TEXT NOT NULL,
    url        TEXT,                            -- links
    filename   TEXT,                            -- files: the stored name (unique) in its project's folder
    mime       TEXT,
    size       INTEGER,
    added_by   TEXT NOT NULL DEFAULT 'you',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_block ON attachments(block_id);
-- What the user did, logged by their agent from the chat when it wasn't tracked otherwise (an errand, a call,
-- studying without the timer, something sudden), optionally about a to-do. No end time = still going.
CREATE TABLE IF NOT EXISTS activities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    block_id   INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,                   -- local time, "YYYY-MM-DDTHH:MM"
    ended_at   TEXT,                            -- NULL while it's still going
    note       TEXT NOT NULL DEFAULT '',
    logged_by  TEXT NOT NULL DEFAULT 'you',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(started_at);
-- Deleted blocks (with everything inside them, their rules, notes and reminders), restorable for 30 days.
CREATE TABLE IF NOT EXISTS trash (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id      INTEGER NOT NULL,             -- the block that was deleted (ids are never reused)
    title         TEXT NOT NULL,
    project_id    INTEGER,                      -- NULL when a whole project was deleted
    project_title TEXT,
    path          TEXT NOT NULL DEFAULT '',     -- where it was, e.g. "My RPG › Characters"
    items         INTEGER NOT NULL DEFAULT 0,   -- blocks nested inside it
    deleted_by    TEXT NOT NULL DEFAULT 'you',
    deleted_at    TEXT NOT NULL,                -- local time, "YYYY-MM-DDTHH:MM:SS"
    payload       TEXT NOT NULL                 -- JSON: blocks, rules, notes, reminders, focus links
);
"""


# Projects that aren't archived: what lists, Today, reminders and the snapshot work from.
ACTIVE_ROOTS = "SELECT id FROM blocks WHERE parent_id IS NULL AND archived_at IS NULL"


def db_path() -> str:
    return os.environ.get("NESTWORK_DB", str(DEFAULT_DB))


def connect() -> sqlite3.Connection:
    path = db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_columns(conn, *cols: tuple[str, str], table: str = "blocks") -> None:
    have = {c["name"] for c in conn.execute(f"PRAGMA table_info({table})")}
    for col, kind in cols:
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def _from_dependencies(conn) -> None:
    """The first release stored plain "waits for" links: they become "ready once X is done" rules."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'dependencies'").fetchone():
        conn.execute("INSERT OR IGNORE INTO rules (block_id, depends_on_id) SELECT block_id, depends_on_id FROM dependencies")
        conn.execute("DROP TABLE dependencies")


# Schema changes since the first release, oldest first. The database remembers how many it has had
# (PRAGMA user_version), so each runs once. Add new ones at the end; never edit or reorder old ones. Each is
# safe on a database that already has the change (version 0 databases predate the counter).
MIGRATIONS = [
    lambda c: _add_columns(c, ("stage_config", "TEXT"), ("due_time", "TEXT"), ("remind", "TEXT")),
    _from_dependencies,
    lambda c: _add_columns(c, ("pomodoros", "INTEGER")),
    lambda c: _add_columns(c, ("estimate_min", "INTEGER"), ("repeat", "TEXT"), ("repeat_next", "TEXT")),
    lambda c: _add_columns(c, ("priority", "INTEGER NOT NULL DEFAULT 0"), ("tags", "TEXT"), ("archived_at", "TEXT")),
    lambda c: _add_columns(c, ("mode", "TEXT NOT NULL DEFAULT 'pomodoro'"), table="focus_sessions"),
    lambda c: _add_columns(c, ("done_at", "TEXT")),
    lambda c: _add_columns(c, ("track_done", "INTEGER"), ("track_total", "INTEGER"), ("track_unit", "TEXT")),
    lambda c: _add_columns(c, ("items", "TEXT"), table="day_picks"),
]
SCHEMA_VERSION = len(MIGRATIONS)


def init_db() -> None:
    """Create missing tables, then run the migrations this database hasn't had yet."""
    conn = connect()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"{db_path()} was made by a newer Nestwork (schema {version}, this one knows "
                           f"{SCHEMA_VERSION}). Update Nestwork before opening it.")
    with conn:
        conn.executescript(SCHEMA)
        for migrate in MIGRATIONS[version:]:
            migrate(conn)
        if version != SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        from . import agentwork, devices, sounds, undo
        conn.executescript(devices.SCHEMA)
        agentwork.install(conn)
        sounds.install(conn)
        undo.install(conn)          # triggers follow the current columns
    conn.close()
