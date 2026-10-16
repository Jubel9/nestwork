"""Proactive reminders, delivered into the chat as messages from the agent.

Two sources:
* to-dos with a due date (and optional time). Each picks reminder moments ("the day before",
  "the morning of", "1 hour before", "15 minutes before"); NULL means the defaults.
* one-off reminders ("remind me Friday at 9 to email my professor"), set from the app or by the
  agent through the MCP `remind_me` tool.

The web server checks every 30 seconds (see main.py). Each reminder is sent once. If several
moments for the same to-do were missed (e.g. the server was off), only the latest is sent, and
nothing is sent for things already in the past.

Delivery is either "quick" (a message written by code, no AI call) or "agent" (Claude writes it in
the agent's voice, with what to prepare); "agent" falls back to "quick" if Claude is busy or fails.
"""

import json
from datetime import date, datetime, time, timedelta

from . import db
from .logic import Project

CHOICES = {
    "day_before": "The day before (9:00)",
    "morning": "The morning of (8:00)",
    "1h": "1 hour before",
    "15m": "15 minutes before",
}
DEFAULT = ["day_before", "morning", "1h"]


def choices_for(block: dict) -> list[str]:
    raw = block.get("remind")
    picked = json.loads(raw) if raw else DEFAULT
    return [c for c in picked if c in CHOICES and (block.get("due_time") or c in ("day_before", "morning"))]


def event_time(block: dict) -> datetime:
    d = date.fromisoformat(block["due_date"])
    if block.get("due_time"):
        h, m = map(int, block["due_time"].split(":"))
        return datetime.combine(d, time(h, m))
    return datetime.combine(d, time(23, 59))


def moment(block: dict, choice: str) -> datetime:
    d = date.fromisoformat(block["due_date"])
    if choice == "day_before":
        return datetime.combine(d - timedelta(days=1), time(9, 0))
    if choice == "morning":
        return datetime.combine(d, time(8, 0))
    return event_time(block) - timedelta(minutes=60 if choice == "1h" else 15)


def when_text(block: dict, choice: str | None, now: datetime) -> str:
    at = f" at {block['due_time']}" if block.get("due_time") else ""
    d = date.fromisoformat(block["due_date"])
    if choice == "1h":
        return f"In 1 hour ({block['due_time']})"
    if choice == "15m":
        return f"In 15 minutes ({block['due_time']})"
    if d == now.date():
        return f"Today{at}"
    if d == now.date() + timedelta(days=1):
        return f"Tomorrow{at}"
    return f"{d:%A %d %B}{at}"


def _upcoming_for_blocks(conn, now: datetime) -> list[dict]:
    """Due-date reminders that should go out now (and the keys of missed ones to skip)."""
    out = []
    rows = conn.execute(db.ACTIVE_ROOTS).fetchall()
    for r in rows:
        p = Project(conn, r["id"])
        for x, b in p.blocks.items():
            if x == p.root_id or not b["due_date"] or p.is_done(x):
                continue
            try:
                if event_time(b) <= now:
                    continue
                stamp = f"b{x}:{b['due_date']}T{b.get('due_time') or ''}"
                passed = [c for c in choices_for(b) if moment(b, c) <= now]
            except ValueError:
                continue
            fresh = [c for c in passed if not conn.execute("SELECT 1 FROM reminder_log WHERE key = ?",
                                                           (f"{stamp}:{c}",)).fetchone()]
            if not fresh:
                continue
            latest = max(fresh, key=lambda c: moment(b, c))
            out.append({"kind": "due", "block_id": x, "choice": latest,
                        "keys": [f"{stamp}:{c}" for c in fresh], **_block_context(p, x)})
    return out


def _block_context(p: Project, x: int) -> dict:
    b = p.blocks[x]
    info = p.gate_info(x)
    return {
        "title": b["title"], "project": p.title(p.root_id), "path": " › ".join(p.path(x)[1:-1]),
        "notes": b["notes"], "due_date": b["due_date"], "due_time": b.get("due_time"),
        "phase": p.label(p.stage(x)),
        "waiting_for": [u["text"] for u in info["blocked_by"]],
        "open_inside": sum(1 for d in p.descendants(x) if not p.children[d] and not p.is_done(d)),
    }


def collect_due(now: datetime | None = None) -> list[dict]:
    """Everything to remind about right now. Marks it as sent, so call `deliver` for each."""
    now = now or datetime.now()
    conn = db.connect()
    try:
        due = _upcoming_for_blocks(conn, now)
        for d in due:
            conn.executemany("INSERT OR IGNORE INTO reminder_log (key) VALUES (?)", [(k,) for k in d["keys"]])
        rows = conn.execute("SELECT * FROM reminders WHERE status = 'pending' AND remind_at <= ? ORDER BY remind_at",
                            (now.strftime("%Y-%m-%dT%H:%M"),)).fetchall()
        for r in rows:
            item = {"kind": "custom", "reminder_id": r["id"], "block_id": r["block_id"], "text": r["text"],
                    "created_by": r["created_by"]}
            if r["block_id"]:
                p = Project.for_block(conn, r["block_id"])
                if p:
                    item.update({k: v for k, v in _block_context(p, r["block_id"]).items() if k != "text"})
            due.append(item)
            conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (r["id"],))
        conn.commit()
        return due
    finally:
        conn.close()


def quick_text(item: dict, now: datetime | None = None) -> str:
    """The reminder as written by code (no AI call)."""
    now = now or datetime.now()
    where = item.get("project", "") + (f" › {item['path']}" if item.get("path") else "")
    lines = []
    if item["kind"] == "custom":
        lines.append(f"⏰ **Reminder:** {item['text']}")
        if item.get("title"):
            lines.append(f"About **{item['title']}** ({where}).")
    else:
        lines.append(f"⏰ **{when_text(item, item['choice'], now)}:** **{item['title']}** ({where}).")
    if item.get("notes", "").strip():
        first = item["notes"].strip().splitlines()[0]
        lines.append(f"> {first[:240]}")
    if item.get("waiting_for"):
        lines.append("Heads-up: it's still waiting until " + " and ".join(item["waiting_for"]) + ".")
    if item.get("open_inside"):
        lines.append(f"{item['open_inside']} task(s) inside it are still open.")
    return "\n\n".join(lines)


def agent_prompt(item: dict, user_name: str) -> str:
    now = datetime.now()
    details = json.dumps({k: item.get(k) for k in ("title", "project", "path", "notes", "due_date", "due_time",
                                                   "phase", "waiting_for", "open_inside", "text")
                          if item.get(k) not in (None, "", [], 0)}, ensure_ascii=False)
    when = when_text(item, item.get("choice"), now) if item.get("due_date") and item["kind"] == "due" else "now"
    return (f"[Automatic reminder: the user did not type this. It is {now:%A %d %B %Y, %H:%M}.]\n"
            f"Text {user_name or 'the user'} a short, friendly reminder in your usual voice, like a message from a friend "
            "(one or two sentences, no lists). Start with ⏰. "
            f"It's about: {details}. When: {when}.\n"
            "Mention anything they should prepare or check first; use the Nestwork tools to look at the block or "
            "related blocks if that helps. Don't change anything. Keep it to a few lines.")


def save_reminder_message(text: str, item: dict) -> int:
    conn = db.connect()
    try:
        meta = {"reminder": True, "block_id": item.get("block_id"), "kind": item["kind"],
                "title": item.get("text") if item["kind"] == "custom" else item.get("title")}
        cur = conn.execute("INSERT INTO chat_messages (session_id, role, text, meta) VALUES ('', 'assistant', ?, ?)",
                           (text, json.dumps(meta)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


async def deliver(item: dict, style: str) -> None:
    from . import agent, chat, persona
    if style == "agent" and not chat.is_running() and chat.claude_bin():
        try:
            failed = True
            async for event in chat.run(agent_prompt(item, persona.profile()["user_name"]), hidden=True,
                                        reminder=item):
                if event.startswith(b"event: done"):
                    failed = json.loads(event.decode().split("data: ", 1)[1])["failed"]
            if not failed:
                agent.after_change()
                return
        except Exception:
            pass
    save_reminder_message(quick_text(item), item)
    agent.after_change()


# --- one-off reminders -------------------------------------------------------------
def add(conn, text: str, remind_at: str, block_id: int | None = None, created_by: str = "you") -> int:
    when = datetime.fromisoformat(remind_at.replace(" ", "T"))
    cur = conn.execute("INSERT INTO reminders (block_id, text, remind_at, created_by) VALUES (?, ?, ?, ?)",
                       (block_id, text.strip(), when.strftime("%Y-%m-%dT%H:%M"), created_by))
    return cur.lastrowid


def _pending(conn, reminder_id: int):
    r = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    if not r:
        raise LookupError(f"There's no reminder #{reminder_id}. Use upcoming_reminders to see their numbers.")
    if r["status"] != "pending":
        raise LookupError(f"Reminder #{reminder_id} already went out.")
    return r


def edit(conn, reminder_id: int, text: str | None = None, remind_at: str | None = None) -> dict:
    """Change a one-off reminder that hasn't gone out yet: its words, its time, or both."""
    r = _pending(conn, reminder_id)
    new_text = (text or "").strip() or r["text"]
    when = datetime.fromisoformat(remind_at.replace(" ", "T")).strftime("%Y-%m-%dT%H:%M") if remind_at else r["remind_at"]
    conn.execute("UPDATE reminders SET text = ?, remind_at = ? WHERE id = ?", (new_text, when, reminder_id))
    return {"id": reminder_id, "text": new_text, "remind_at": when, "old_text": r["text"], "old_at": r["remind_at"]}


def delete(conn, reminder_id: int) -> dict:
    """Remove a one-off reminder that hasn't gone out yet."""
    r = _pending(conn, reminder_id)
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    return dict(r)


def upcoming(conn, days: int = 14, now: datetime | None = None) -> list[dict]:
    """What will be reminded about in the next `days`, soonest first."""
    now = now or datetime.now()
    horizon = now + timedelta(days=days)
    out = []
    for r in conn.execute(db.ACTIVE_ROOTS).fetchall():
        p = Project(conn, r["id"])
        for x, b in p.blocks.items():
            if x == p.root_id or not b["due_date"] or p.is_done(x):
                continue
            try:
                if not now < event_time(b) <= horizon + timedelta(days=1):
                    continue
                stamp = f"b{x}:{b['due_date']}T{b.get('due_time') or ''}"
                for c in choices_for(b):
                    at = moment(b, c)
                    sent = conn.execute("SELECT 1 FROM reminder_log WHERE key = ?", (f"{stamp}:{c}",)).fetchone()
                    if at > now and at <= horizon and not sent:
                        out.append({"at": at.strftime("%Y-%m-%dT%H:%M"), "kind": "due", "block_id": x,
                                    "title": b["title"], "project": p.title(p.root_id), "choice": CHOICES[c]})
            except ValueError:
                continue
    for r in conn.execute("SELECT r.*, b.title AS block_title FROM reminders r LEFT JOIN blocks b ON b.id = r.block_id "
                          "WHERE r.status = 'pending' AND r.remind_at <= ? ORDER BY r.remind_at",
                          (horizon.strftime("%Y-%m-%dT%H:%M"),)).fetchall():
        out.append({"at": r["remind_at"], "kind": "custom", "id": r["id"], "block_id": r["block_id"],
                    "title": r["block_title"], "text": r["text"], "created_by": r["created_by"]})
    return sorted(out, key=lambda o: o["at"])


def shown_since_last_message(conn) -> list[str]:
    """Reminders the user saw since they last wrote, so the agent knows what "thanks, help me" refers to."""
    last_user = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM chat_messages WHERE role = 'user'").fetchone()["id"]
    rows = conn.execute("SELECT text, json_extract(meta, '$.title') AS title, json_extract(meta, '$.agent_job') AS job "
                        "FROM chat_messages WHERE id > ? AND role = 'assistant' "
                        "AND (json_extract(meta, '$.reminder') = 1 OR json_extract(meta, '$.agent_job') IS NOT NULL) "
                        "ORDER BY id", (last_user,)).fetchall()
    return [f"(Result of your scheduled task “{r['title']}”)\n{r['text']}" if r["job"] else r["text"] for r in rows]
