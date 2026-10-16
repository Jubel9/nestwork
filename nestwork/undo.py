"""Undo for every change to projects and to-dos (⌘Z), by you or your agent.

SQLite triggers copy the old version of every row that changes in the tables below into `undo_log`, but
only while a change group is open (`undo_ctx` holds its id). A web request that changes blocks, rules,
notes, links or the trash opens a group (deps.get_conn), and so does every change an agent makes
(agent.apply). `undo_last()` puts the newest group's rows back exactly as they were, newest first, with
foreign-key checks deferred to the end, so a move, an edit, new rules, a delete (with what the trash
kept), an import or an approved agent change all come back as one step. Undo itself isn't recorded, and
only the last KEEP groups are kept. Repeats coming back, reminders, focus sessions and chat aren't undone.
"""

import json
from datetime import datetime

TABLES = ("blocks", "rules", "block_notes", "trash", "attachments", "completions", "day_plans", "day_picks", "activities")
KEEP = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS undo_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'you',       -- you | agent
    created_at TEXT NOT NULL,
    undone     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS undo_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    gid    INTEGER NOT NULL,
    tbl    TEXT NOT NULL,
    op     TEXT NOT NULL,                         -- INSERT | UPDATE | DELETE
    row_id INTEGER NOT NULL,                      -- the row's rowid
    old    TEXT                                   -- JSON of the row before (UPDATE, DELETE)
);
CREATE INDEX IF NOT EXISTS idx_undo_log_gid ON undo_log(gid);
CREATE TABLE IF NOT EXISTS undo_ctx (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    gid INTEGER
);
INSERT OR IGNORE INTO undo_ctx (id, gid) VALUES (1, NULL);
"""


def install(conn) -> None:
    """Create the tables and (re)create the triggers from each table's current columns."""
    conn.executescript(SCHEMA)
    for tbl in TABLES:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({tbl})")]
        if not cols:
            continue
        old = "json_object('_rowid', OLD.rowid, " + ", ".join(f"'{c}', OLD.\"{c}\"" for c in cols) + ")"
        gid = "(SELECT gid FROM undo_ctx WHERE id = 1)"
        when = f"WHEN {gid} IS NOT NULL"
        for op, row, data in (("INSERT", "NEW.rowid", "NULL"), ("UPDATE", "OLD.rowid", old), ("DELETE", "OLD.rowid", old)):
            name = f"undo_{tbl}_{op.lower()}"
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(f"CREATE TRIGGER {name} AFTER {op} ON {tbl} {when} BEGIN "
                         f"INSERT INTO undo_log (gid, tbl, op, row_id, old) VALUES ({gid}, '{tbl}', '{op}', {row}, {data}); END")


def begin(conn, label: str, source: str = "you") -> int:
    cur = conn.execute("INSERT INTO undo_groups (label, source, created_at) VALUES (?, ?, ?)",
                       (label[:200], source, datetime.now().isoformat(timespec="seconds")))
    conn.execute("UPDATE undo_ctx SET gid = ? WHERE id = 1", (cur.lastrowid,))
    return cur.lastrowid


def end(conn) -> None:
    """Close the open group; drop it if nothing was recorded; keep only the newest KEEP groups."""
    gid = conn.execute("SELECT gid FROM undo_ctx WHERE id = 1").fetchone()[0]
    if gid is None:
        return
    conn.execute("UPDATE undo_ctx SET gid = NULL WHERE id = 1")
    if not conn.execute("SELECT 1 FROM undo_log WHERE gid = ? LIMIT 1", (gid,)).fetchone():
        conn.execute("DELETE FROM undo_groups WHERE id = ?", (gid,))
        return
    # A new change after an undo: what was undone can't be redone, so it's no longer kept.
    conn.execute("DELETE FROM undo_log WHERE gid IN (SELECT id FROM undo_groups WHERE undone = 1)")
    conn.execute("DELETE FROM undo_groups WHERE undone = 1")
    old = [r[0] for r in conn.execute("SELECT id FROM undo_groups ORDER BY id DESC LIMIT -1 OFFSET ?", (KEEP,))]
    if old:
        marks = ",".join("?" * len(old))
        conn.execute(f"DELETE FROM undo_log WHERE gid IN ({marks})", old)
        conn.execute(f"DELETE FROM undo_groups WHERE id IN ({marks})", old)


def abort(conn) -> None:
    conn.execute("UPDATE undo_ctx SET gid = NULL WHERE id = 1")


def latest(conn) -> dict | None:
    r = conn.execute("SELECT * FROM undo_groups WHERE undone = 0 ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def undo_last(conn) -> dict:
    """Undo the newest change group. Returns it; raises LookupError when there's nothing to undo."""
    group = latest(conn)
    if group is None:
        raise LookupError("Nothing to undo.")
    conn.execute("UPDATE undo_ctx SET gid = NULL WHERE id = 1")
    conn.execute("PRAGMA defer_foreign_keys = ON")
    cols = {t: [c["name"] for c in conn.execute(f"PRAGMA table_info({t})")] for t in TABLES}
    for row in conn.execute("SELECT * FROM undo_log WHERE gid = ? ORDER BY id DESC", (group["id"],)).fetchall():
        tbl = row["tbl"]
        if row["op"] == "INSERT":
            conn.execute(f"DELETE FROM {tbl} WHERE rowid = ?", (row["row_id"],))
            continue
        data = {k: v for k, v in json.loads(row["old"]).items() if k in cols[tbl]}
        if row["op"] == "UPDATE":
            conn.execute(f"UPDATE {tbl} SET {', '.join(f'{k} = ?' for k in data)} WHERE rowid = ?",
                         (*data.values(), row["row_id"]))
        else:
            names = ["rowid", *data]
            conn.execute(f"INSERT OR REPLACE INTO {tbl} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                         (row["row_id"], *data.values()))
    conn.execute("UPDATE undo_groups SET undone = 1 WHERE id = ?", (group["id"],))
    return group


def clear(conn) -> None:
    conn.execute("DELETE FROM undo_log")
    conn.execute("DELETE FROM undo_groups")
    conn.execute("UPDATE undo_ctx SET gid = NULL WHERE id = 1")


# --- what a request did, in a few words -------------------------------------------------
def label_for(conn, method: str, path: str) -> str | None:
    """A label for a web request that may change projects, or None if it can't (no group is opened)."""
    parts = path.strip("/").split("/")          # e.g. api blocks 12 move
    if len(parts) < 2 or parts[0] != "api" or parts[1] not in ("blocks", "notes", "trash", "import", "attachments", "plan", "today", "calendar", "activities"):
        return None
    if parts[1] == "calendar":
        return {"POST": "Add to the calendar", "PATCH": "Move on the calendar", "DELETE": "Remove from the calendar"}.get(method)
    if parts[1] == "activities":
        return {"PATCH": "Change an activity", "DELETE": "Delete an activity"}.get(method)
    if parts[1] == "plan":
        return {"PUT": "Change the day's plan", "DELETE": "Clear today's plan", "POST": "Make a quick plan"}.get(method)
    if parts[1] == "today":
        return {"PUT": "Pick today's to-dos", "DELETE": "Back to the automatic to-dos",
                "POST": "Tick off a to-do for today"}.get(method)

    def title(i):
        try:
            r = conn.execute("SELECT title FROM blocks WHERE id = ?", (int(i),)).fetchone()
        except ValueError:
            return None
        return f"“{r['title']}”" if r else None

    if parts[1] == "blocks":
        if len(parts) == 2:
            return "Add a to-do" if method == "POST" else None
        name = title(parts[2]) or "a block"
        action = parts[3] if len(parts) > 3 else ""
        return {
            ("PATCH", ""): f"Edit {name}", ("DELETE", ""): f"Delete {name}", ("POST", "move"): f"Move {name}",
            ("POST", "move-into"): f"Move {name} into another block", ("PUT", "rules"): f"Change the rules of {name}",
            ("POST", "duplicate"): f"Duplicate {name}", ("PUT", "stages"): "Change the phases",
            ("POST", "notes"): f"Add a note to {name}", ("POST", "attachments"): f"Add to {name}",
        }.get((method, action), f"Change {name}")
    if parts[1] == "notes":
        return "Edit a note" if method == "PATCH" else "Delete a note"
    if parts[1] == "trash":
        return "Restore from the trash" if parts[-1] == "restore" else "Empty the trash"
    if parts[1] == "attachments":
        return "Remove a link or file"
    return "Import"
