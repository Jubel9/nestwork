"""The activity log: what the user did and when, logged by their agent from the chat when nothing else
tracked it (the focus timer wasn't running, it isn't a to-do, or they just forgot). Each entry has a start,
an end (none while it's still going), an optional to-do and a note. day_recap reads it, so a later chat
session knows what happened without asking.
"""

import re
from datetime import date, datetime, timedelta

TITLE_MAX = 200


def parse_time(text: str | None, now: datetime | None = None) -> str:
    """"now", "HH:MM" (today), "yesterday HH:MM" or "YYYY-MM-DD HH:MM" → local "YYYY-MM-DDTHH:MM"."""
    now = now or datetime.now()
    t = re.sub(r"^(\d{4}-\d\d-\d\d)t", r"\1 ", " ".join((text or "now").strip().lower().split()))
    if t in ("", "now"):
        return now.strftime("%Y-%m-%dT%H:%M")
    day = now.date()
    if t.startswith("yesterday"):
        day, t = day - timedelta(days=1), t[len("yesterday"):].strip()
    elif t.startswith("today"):
        t = t[len("today"):].strip()
    m = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2})\s+)?(\d{1,2})[:.](\d{2})", t)
    if not m:
        raise ValueError(f"“{text}” isn't a time like now, 19:30, yesterday 22:00 or 2026-10-14 09:00.")
    if m.group(1):
        day = date.fromisoformat(m.group(1))
    h, mi = int(m.group(2)), int(m.group(3))
    if h > 23 or mi > 59:
        raise ValueError(f"“{text}” isn't a time of day.")
    return f"{day.isoformat()}T{h:02d}:{mi:02d}"


def _check_block(conn, block_id: int | None) -> None:
    if block_id is not None and not conn.execute("SELECT 1 FROM blocks WHERE id = ?", (block_id,)).fetchone():
        raise ValueError(f"There's no block with id {block_id}.")


def _title(title: str) -> str:
    t = " ".join((title or "").split())
    if not t:
        raise ValueError("Say what the activity was.")
    return t[:TITLE_MAX]


def get(conn, aid: int) -> dict:
    r = conn.execute("SELECT a.*, b.title AS block_title FROM activities a LEFT JOIN blocks b ON b.id = a.block_id "
                     "WHERE a.id = ?", (aid,)).fetchone()
    if r is None:
        raise LookupError(f"There's no activity #{aid}.")
    return dict(r)


def add(conn, title: str, start: str = "now", end: str = "", block_id: int | None = None, note: str = "",
        by: str = "you") -> dict:
    s = parse_time(start)
    e = parse_time(end) if (end or "").strip() else None
    if e and e < s:
        raise ValueError("It can't end before it starts.")
    if s > parse_time("now"):
        raise ValueError("That hasn't started yet: log it when it happens, or set a reminder instead.")
    _check_block(conn, block_id)
    cur = conn.execute("INSERT INTO activities (title, block_id, started_at, ended_at, note, logged_by) VALUES (?, ?, ?, ?, ?, ?)",
                       (_title(title), block_id, s, e, " ".join((note or "").split()), by))
    return get(conn, cur.lastrowid)


def running(conn) -> list[dict]:
    rows = conn.execute("SELECT id FROM activities WHERE ended_at IS NULL ORDER BY started_at").fetchall()
    return [get(conn, r["id"]) for r in rows]


def finish(conn, aid: int | None = None, end: str = "now", note: str = "") -> dict:
    """End an activity that's still going; without an id, the only one still going."""
    if aid is None:
        open_ = running(conn)
        if not open_:
            raise LookupError("Nothing is still going.")
        if len(open_) > 1:
            raise LookupError("Several activities are still going: " + ", ".join(f"#{a['id']} {a['title']}" for a in open_)
                              + ". Say which.")
        aid = open_[0]["id"]
    a = get(conn, aid)
    e = parse_time(end)
    if e < a["started_at"]:
        raise ValueError("It can't end before it started.")
    extra = " ".join((note or "").split())
    conn.execute("UPDATE activities SET ended_at = ?, note = ? WHERE id = ?",
                 (e, (a["note"] + (" " if a["note"] and extra else "") + extra), aid))
    return get(conn, aid)


def change(conn, aid: int, title: str | None = None, start: str | None = None, end: str | None = None,
           block_id: int | None = None, note: str | None = None) -> dict:
    """end="" makes it still going again; block_id=0 unlinks the to-do."""
    a = get(conn, aid)
    s = parse_time(start) if start else a["started_at"]
    e = a["ended_at"] if end is None else (parse_time(end) if end.strip() else None)
    if e and e < s:
        raise ValueError("It can't end before it starts.")
    if block_id:
        _check_block(conn, block_id)
    conn.execute("UPDATE activities SET title = ?, started_at = ?, ended_at = ?, block_id = ?, note = ? WHERE id = ?",
                 (_title(title) if title else a["title"], s, e,
                  a["block_id"] if block_id is None else (block_id or None),
                  a["note"] if note is None else " ".join(note.split()), aid))
    return get(conn, aid)


def delete(conn, aid: int) -> dict:
    a = get(conn, aid)
    conn.execute("DELETE FROM activities WHERE id = ?", (aid,))
    return a


def for_day(conn, day: str) -> list[dict]:
    """Activities that started that day, or that were still going then (began earlier, ended that day or later)."""
    rows = conn.execute("SELECT id FROM activities WHERE date(started_at) = ? OR (date(started_at) < ? AND "
                        "(ended_at IS NULL OR date(ended_at) >= ?)) ORDER BY started_at", (day, day, day)).fetchall()
    return [get(conn, r["id"]) for r in rows]


def minutes(a: dict) -> int | None:
    if not a["ended_at"]:
        return None
    return round((datetime.fromisoformat(a["ended_at"]) - datetime.fromisoformat(a["started_at"])).total_seconds() / 60)


def line(a: dict, day: str | None = None) -> str:
    """"#3 19:00–20:30 (90 min) Passport photo trip [to-do: Visa application, id 79]: note (logged by Nova)"."""
    def hm(stamp: str) -> str:
        return stamp[11:16] if day and stamp[:10] == day else stamp[:16].replace("T", " ")
    span = f"{hm(a['started_at'])}–{hm(a['ended_at'])} ({minutes(a)} min)" if a["ended_at"] \
        else f"since {hm(a['started_at'])}, still going"
    return (f"#{a['id']} {span} {a['title']}"
            + (f" [to-do: {a['block_title']}, id {a['block_id']}]" if a.get("block_id") else "")
            + (f": {a['note']}" if a["note"] else "") + f" (logged by {a['logged_by']})")
