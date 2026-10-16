"""Notes & activity on a block: a running log next to its description.

Notes are written by you or by your agent (MCP `add_note`, only when you ask). Activity entries are
added by Nestwork itself when a block is moved to another phase (by you or the agent), and focus
sessions from the timer on that to-do show up in the same timeline. Times are local, like reminders.
"""

from datetime import datetime


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def add(conn, block_id: int, text: str, author: str = "you", author_name: str | None = None, kind: str = "note") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("A note can't be empty.")
    cur = conn.execute(
        "INSERT INTO block_notes (block_id, kind, author, author_name, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (block_id, kind, author, author_name, text[:10000], _now()))
    return get(conn, cur.lastrowid)


def log_event(conn, block_id: int, text: str, author: str = "you", author_name: str | None = None) -> None:
    add(conn, block_id, text, author, author_name, kind="event")


def get(conn, note_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM block_notes WHERE id = ?", (note_id,)).fetchone()
    return dict(r) if r else None


def edit(conn, note_id: int, text: str) -> dict:
    note = get(conn, note_id)
    if note is None:
        raise LookupError("That note no longer exists.")
    if note["kind"] != "note":
        raise ValueError("Activity entries can't be edited.")
    if not text.strip():
        raise ValueError("A note can't be empty.")
    conn.execute("UPDATE block_notes SET text = ?, edited_at = ? WHERE id = ?", (text.strip()[:10000], _now(), note_id))
    return get(conn, note_id)


def delete(conn, note_id: int) -> None:
    conn.execute("DELETE FROM block_notes WHERE id = ?", (note_id,))


def counts(conn, block_ids) -> dict[int, int]:
    """How many notes (not activity) each block has, for the badge on cards."""
    ids = list(block_ids)
    if not ids:
        return {}
    rows = conn.execute(f"SELECT block_id, COUNT(*) AS n FROM block_notes WHERE kind = 'note' AND block_id IN "
                        f"({','.join('?' * len(ids))}) GROUP BY block_id", ids).fetchall()
    return {r["block_id"]: r["n"] for r in rows}


def timeline(conn, block_id: int, limit: int = 200) -> list[dict]:
    """Notes, activity and focus sessions on this block, newest first."""
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM block_notes WHERE block_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (block_id, limit))]
    for s in conn.execute("SELECT * FROM focus_sessions WHERE block_id = ? AND status != 'running' "
                          "ORDER BY started_at DESC LIMIT ?", (block_id, limit)).fetchall():
        bits = [f"Focused {round(s['focus_min'])} min", "finished" if s["status"] == "completed" else "stopped early"]
        if s["rating"]:
            bits.append(f"productivity {s['rating']}/5")
        text = " · ".join(bits) + (f"\n{s['note']}" if s["note"] else "")
        items.append({"id": f"f{s['id']}", "block_id": block_id, "kind": "focus", "author": "you", "author_name": None,
                      "text": text, "created_at": s["started_at"], "edited_at": None})
    items.sort(key=lambda i: i["created_at"], reverse=True)
    return items[:limit]


def markdown(conn, block_id: int, limit: int = 20) -> list[str]:
    """Timeline lines for the agent (get_block)."""
    out = []
    for i in timeline(conn, block_id, limit):
        who = {"agent": i["author_name"] or "your agent", "you": "you"}.get(i["author"], i["author"])
        label = {"note": f"note by {who}", "event": f"activity ({who})", "focus": "focus session"}[i["kind"]]
        text = i["text"].strip().replace("\n", "\n  ")
        out.append(f"- {i['created_at'][:16].replace('T', ' ')} · {label}" + (f" [note id {i['id']}]" if i["kind"] == "note" else "") + f": {text}")
    return out
