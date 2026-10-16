"""The trash: deleting a block keeps a copy of it for 30 days, so it can be put back.

`stash()` saves the block and everything inside it (with their rules, notes & activity, pending reminders
and which focus sessions were about them) as one JSON row before main.py deletes it for real. The rest of
Nestwork never sees what's in the trash. `restore()` puts it all back under its old ids (SQLite never reuses
them): inside its old parent, or at the top of its project if that parent is gone too. Rules that would
now make a loop are dropped, with a notice. Trash older than 30 days is purged by the maintenance loop.
"""

import json
from datetime import datetime, timedelta

KEEP_DAYS = 30


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(conn, sql: str, ids: list[int]) -> list[dict]:
    marks = ",".join("?" * len(ids))
    return [dict(r) for r in conn.execute(sql.format(ids=marks), ids * sql.count("{ids}"))]


def stash(conn, p, block_id: int, by: str = "you") -> int:
    """Copy a block and everything inside it into the trash (call before deleting it). Returns the trash id."""
    ids = [block_id, *p.descendants(block_id)]
    is_root = block_id == p.root_id
    payload = {
        "root_id": p.root_id,
        "blocks": [dict(p.blocks[i]) for i in ids],
        "rules": _rows(conn, "SELECT * FROM rules WHERE block_id IN ({ids}) OR depends_on_id IN ({ids})", ids),
        "notes": _rows(conn, "SELECT * FROM block_notes WHERE block_id IN ({ids})", ids),
        "reminders": _rows(conn, "SELECT * FROM reminders WHERE status = 'pending' AND block_id IN ({ids})", ids),
        "attachments": _rows(conn, "SELECT * FROM attachments WHERE block_id IN ({ids})", ids),
        "focus_blocks": [[r["id"], r["block_id"]] for r in
                         _rows(conn, "SELECT id, block_id FROM focus_sessions WHERE block_id IN ({ids})", ids)],
        "focus_projects": [r["id"] for r in conn.execute(
            "SELECT id FROM focus_sessions WHERE project_id = ?", (block_id,))] if is_root else [],
    }
    cur = conn.execute(
        "INSERT INTO trash (block_id, title, project_id, project_title, path, items, deleted_by, deleted_at, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (block_id, p.title(block_id), None if is_root else p.root_id, p.title(p.root_id),
         " › ".join(p.path(block_id)[:-1]), len(ids) - 1, by, _now(), json.dumps(payload)))
    return cur.lastrowid


def listing(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT id, block_id, title, project_id, project_title, path, items, deleted_by, deleted_at "
                          "FROM trash ORDER BY deleted_at DESC, id DESC"):
        d = dict(r)
        d["purge_on"] = (datetime.fromisoformat(r["deleted_at"]) + timedelta(days=KEEP_DAYS)).date().isoformat()
        d["is_project"] = r["project_id"] is None
        out.append(d)
    return out


def get(conn, trash_id: int) -> dict:
    row = conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
    if row is None:
        raise LookupError("That's no longer in the trash.")
    return {**dict(row), "payload": json.loads(row["payload"])}


def _exists(conn, block_id) -> bool:
    return block_id is not None and conn.execute("SELECT 1 FROM blocks WHERE id = ?", (block_id,)).fetchone() is not None


def restore(conn, trash_id: int) -> dict:
    """Put a trashed block back. Returns {"block_id", "project_id", "notices"}; the caller settles the project."""
    from .logic import Project
    from .main import next_position
    item = get(conn, trash_id)
    data = item["payload"]
    blocks = {b["id"]: b for b in data["blocks"]}
    top = blocks[item["block_id"]]
    if any(_exists(conn, i) for i in blocks):
        raise ValueError("Part of it is already back in Nestwork.")
    notices = []
    parent = top["parent_id"]
    if parent is not None and not _exists(conn, parent):
        if not _exists(conn, data["root_id"]):
            raise ValueError(f"Its project “{item['project_title']}” is gone too. Restore the project first.")
        parent = data["root_id"]
        notices.append(f"Its old place is gone, so “{top['title']}” is back at the top of “{item['project_title']}”.")

    # Parents before children (foreign keys are on).
    order, placed = [], set()
    while len(order) < len(blocks):
        for b in blocks.values():
            if b["id"] not in placed and (b["id"] == top["id"] or b["parent_id"] in placed):
                order.append(b)
                placed.add(b["id"])
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(blocks)")]
    for b in order:
        row = {k: v for k, v in b.items() if k in cols}
        if b["id"] == top["id"]:
            row["parent_id"] = parent
            row["position"] = next_position(conn, parent, row["status"])
        conn.execute(f"INSERT INTO blocks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    inside = set(blocks)
    internal, crossing = [], []
    for r in data["rules"]:
        if _exists(conn, r["block_id"]) and _exists(conn, r["depends_on_id"]):
            (internal if r["block_id"] in inside and r["depends_on_id"] in inside else crossing).append(r)
    insert = "INSERT OR IGNORE INTO rules (block_id, depends_on_id, required_stage, target_stage) VALUES (?, ?, ?, ?)"
    conn.executemany(insert, [(r["block_id"], r["depends_on_id"], r["required_stage"], r["target_stage"])
                              for r in internal + crossing])
    root = data["root_id"] if parent is not None else top["id"]
    if crossing and Project(conn, root).has_deadlock():
        # Something changed since it was deleted: rules across the boundary would now make a loop.
        for r in crossing:
            conn.execute("DELETE FROM rules WHERE block_id = ? AND depends_on_id = ? AND target_stage = ?",
                         (r["block_id"], r["depends_on_id"], r["target_stage"]))
        notices.append(f"Rules between “{top['title']}” and the rest of the project were left out: "
                       "with what changed since, they would make a loop.")
    if Project(conn, root).has_deadlock():
        raise ValueError("Putting it back would make its rules impossible to meet (a loop).")

    for n in data["notes"]:
        conn.execute("INSERT OR IGNORE INTO block_notes (id, block_id, kind, author, author_name, text, created_at, edited_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (n["id"], n["block_id"], n["kind"], n["author"], n["author_name"], n["text"], n["created_at"], n["edited_at"]))
    for r in data["reminders"]:
        conn.execute("INSERT OR IGNORE INTO reminders (id, block_id, text, remind_at, status, created_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (r["id"], r["block_id"], r["text"], r["remind_at"], r["status"], r["created_by"], r["created_at"]))
    for a in data.get("attachments", []):
        conn.execute("INSERT OR IGNORE INTO attachments (id, block_id, kind, title, url, filename, mime, size, added_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (a["id"], a["block_id"], a["kind"], a["title"], a["url"], a["filename"], a["mime"], a["size"],
                      a["added_by"], a["created_at"]))
    for sid, bid in data["focus_blocks"]:
        conn.execute("UPDATE focus_sessions SET block_id = ? WHERE id = ? AND block_id IS NULL", (bid, sid))
    for sid in data["focus_projects"]:
        conn.execute("UPDATE focus_sessions SET project_id = ? WHERE id = ? AND project_id IS NULL", (top["id"], sid))
    conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
    return {"block_id": top["id"], "project_id": root, "notices": notices}


def forget(conn, trash_id: int | None = None) -> int:
    """Delete from the trash for good (one item, or everything). Returns how many went."""
    if trash_id is None:
        return conn.execute("DELETE FROM trash").rowcount
    return conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,)).rowcount


def purge_old(conn, now: datetime | None = None) -> int:
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    return conn.execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,)).rowcount
