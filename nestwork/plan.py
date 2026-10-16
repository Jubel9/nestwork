"""Plan my day: today's to-dos (and calendar events) in order, with start times, shown on the HUD.

A plan is a list of items for one day: {"id": short id, "start": "HH:MM", "minutes": n, "title": …,
"block_id": id or None, "kind": "todo" | "event" | "break", "by": who put it there}. Any day can have one: the
Calendar (calendar.py) shows them next to due dates and reminders, and adds, moves or removes single items
(`add_item`, `update_item`, `remove_item`) without rewriting the rest of the day. Your agent makes one with you in the chat (MCP `get_day_plan` gives it the
candidates, `set_day_plan` saves what you agreed on; it can also read and write your Google Calendar when that
app is connected), or `quick_plan()` lines up the suggested to-dos from now without AI. You can clear it or
re-plan anytime. Plans are kept per day.

Picks are the HUD's Today → To-dos list, curated: by default that list is automatic (export.today_items'
`focus` plus what's due today); when your agent picks the to-dos for a day (MCP `pick_todays_todos`), the
HUD shows those, in its order, until you or it go back to the automatic list. A pick can also be a to-do
written just for that day, outside every project (a call, an errand), which you tick off on the HUD.
"""

import json
import math
import secrets
from datetime import date, datetime, timedelta

from . import export, forecast, focus

KINDS = ("todo", "event", "break")
DEFAULT_MIN = 25
BREAK_MIN = 5


def _today() -> str:
    return date.today().isoformat()


def get(conn, day: str | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM day_plans WHERE day = ?", (day or _today(),)).fetchone()
    if not row:
        return None
    return {"day": row["day"], "items": enrich(conn, json.loads(row["items"])), "made_by": row["made_by"],
            "updated_at": row["updated_at"]}


def enrich(conn, items: list[dict]) -> list[dict]:
    """Add each to-do's current title, done state and project (it may have changed since the plan was made)."""
    from .logic import Project
    out = []
    for it in items:
        it = dict(it)
        if it.get("block_id"):
            p = Project.for_block(conn, it["block_id"])
            if p is None:
                it.update(gone=True, done=False)
            else:
                it.update(title=p.title(it["block_id"]), done=p.is_done(it["block_id"]), project=p.title(p.root_id),
                          project_id=p.root_id)
        out.append(it)
    return out


def _clean(items: list, by: str = "you") -> list[dict]:
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("Each plan item is an object with start, minutes and title (or block_id).")
        start = str(it.get("start", "")).strip()
        try:
            datetime.strptime(start, "%H:%M")
        except ValueError:
            raise ValueError(f"“{start}” isn't a time like 09:30.")
        minutes = int(it.get("minutes") or DEFAULT_MIN)
        if not 1 <= minutes <= 12 * 60:
            raise ValueError("Each item takes between 1 minute and 12 hours.")
        kind = it.get("kind") or ("todo" if it.get("block_id") else "event")
        if kind not in KINDS:
            raise ValueError("kind is todo, event or break.")
        title = str(it.get("title") or "").strip()[:200]
        block_id = int(it["block_id"]) if it.get("block_id") else None
        if not title and not block_id and kind != "break":
            raise ValueError("An item needs a title or a block_id.")
        item_id = str(it.get("id") or "").strip()[:12] or secrets.token_hex(3)
        out.append({"id": item_id, "start": start, "minutes": minutes,
                    "title": title or ("Break" if kind == "break" else ""), "block_id": block_id, "kind": kind,
                    "by": str(it.get("by") or by)[:60]})
    return sorted(out, key=lambda i: i["start"])


def save(conn, items: list, day: str | None = None, by: str = "you") -> dict:
    from .logic import Project
    clean = _clean(items, by)
    for it in clean:
        if it["block_id"] and Project.for_block(conn, it["block_id"]) is None:
            raise ValueError(f"There is no to-do with id {it['block_id']}.")
    day = day or _today()
    date.fromisoformat(day)
    conn.execute("INSERT INTO day_plans (day, items, made_by, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(day) DO UPDATE SET items = excluded.items, made_by = excluded.made_by, updated_at = excluded.updated_at",
                 (day, json.dumps(clean), by, datetime.now().isoformat(timespec="seconds")))
    return get(conn, day)


def clear(conn, day: str | None = None) -> None:
    conn.execute("DELETE FROM day_plans WHERE day = ?", (day or _today(),))


# --- single items (the Calendar and the agent's schedule tools) ---------------------------
def _raw(conn, day: str) -> tuple[list[dict], str]:
    """A day's items as stored (ids given to older ones), and who made the plan."""
    row = conn.execute("SELECT items, made_by FROM day_plans WHERE day = ?", (day,)).fetchone()
    if not row:
        return [], "you"
    return [{**it, "id": it.get("id") or f"i{n}"} for n, it in enumerate(json.loads(row["items"]))], row["made_by"]


def _store(conn, day: str, items: list[dict], made_by: str) -> dict | None:
    if not items:
        clear(conn, day)
        return None
    return save(conn, items, day, made_by)


def add_item(conn, day: str, item: dict, by: str = "you") -> dict:
    """Put one thing on a day (a to-do, an event or a break). Returns the new item."""
    date.fromisoformat(day)
    items, made_by = _raw(conn, day)
    new = _clean([{**item, "id": None}], by)[0]
    _store(conn, day, items + [new], made_by if items else by)
    return next(i for i in get(conn, day)["items"] if i["id"] == new["id"])


def _find(items: list[dict], item_id: str) -> dict:
    for it in items:
        if it["id"] == item_id:
            return it
    raise LookupError(f"There's nothing with id “{item_id}” on that day.")


def update_item(conn, day: str, item_id: str, changes: dict, by: str = "you") -> dict:
    """Change one item's time, length, title or kind, or move it to another day (`changes["day"]`)."""
    items, made_by = _raw(conn, day)
    it = _find(items, item_id)
    to_day = str(changes.get("day") or day)
    date.fromisoformat(to_day)
    moved = {**it, **{k: v for k, v in changes.items() if k in ("start", "minutes", "title", "kind") and v is not None}}
    _clean([moved], by)                       # check before anything is written
    if to_day == day:
        _store(conn, day, [moved if i["id"] == item_id else i for i in items], made_by)
    else:
        _store(conn, day, [i for i in items if i["id"] != item_id], made_by)
        there, there_by = _raw(conn, to_day)
        _store(conn, to_day, there + [moved], there_by if there else by)
    return next(i for i in get(conn, to_day)["items"] if i["id"] == item_id)


def remove_item(conn, day: str, item_id: str) -> dict:
    items, made_by = _raw(conn, day)
    it = _find(items, item_id)
    gone = enrich(conn, [it])[0]
    _store(conn, day, [i for i in items if i["id"] != item_id], made_by)
    return gone


def candidates(conn) -> list[dict]:
    """What could go in today's plan: overdue, due soon, started and ready to-dos, with how long each takes."""
    today = export.today_items(conn)
    groups = {g["key"]: g["items"] for g in today["groups"]}
    picked = groups["overdue"] + groups["due_soon"] + groups["in_progress"] + groups["ready"]
    spent = forecast.spent_minutes(conn, [i["id"] for i in picked])
    out = []
    for i in picked:
        est = i.get("estimate_min")
        left = max(est - spent.get(i["id"], 0), est * 0.25) if est else None
        pomos_left = max(0, (i.get("pomodoros") or 0) - (i.get("pomodoros_done") or 0))
        minutes = round(left) if left else (pomos_left * DEFAULT_MIN if pomos_left else DEFAULT_MIN)
        out.append({**i, "suggested_min": max(DEFAULT_MIN, min(minutes, 4 * 60)), "estimate_left_min": round(left) if left else None,
                    "pomodoros_left": pomos_left})
    return out


def quick_plan(conn, now: datetime | None = None, by: str = "you") -> dict:
    """Without AI: from the next quarter hour, the suggested to-dos (high priority first) until today's focus
    goal is covered, each followed by a short break."""
    now = now or datetime.now()
    start = now.replace(second=0, microsecond=0) + timedelta(minutes=(15 - now.minute % 15) % 15 or 0)
    goal = focus.goal(conn)
    items, total, t = [], 0, start
    cands = sorted(candidates(conn), key=lambda i: (-i["priority"], i["due_date"] or "9999"))
    for c in cands:
        if total >= goal or t.date() != now.date():
            break
        m = min(c["suggested_min"], max(DEFAULT_MIN, goal - total))
        m = int(math.ceil(m / 5) * 5)
        items.append({"start": t.strftime("%H:%M"), "minutes": m, "block_id": c["id"], "title": c["title"], "kind": "todo"})
        total += m
        t += timedelta(minutes=m)
        items.append({"start": t.strftime("%H:%M"), "minutes": BREAK_MIN, "title": "Break", "kind": "break"})
        t += timedelta(minutes=BREAK_MIN)
    if items and items[-1]["kind"] == "break":
        items.pop()
    if not items:
        raise ValueError("Nothing to plan: no to-dos are ready or due.")
    return save(conn, items, now.date().isoformat(), by)


def markdown(conn, day: str | None = None) -> str:
    """For the agent: today's plan, or the candidates to make one."""
    from .chat import now_stamp
    from . import connectors
    p = get(conn, day)
    lines = [f"It's now {now_stamp()}."]
    apps = connectors.flags(conn)["names"]
    cal = [a for a in apps if "calendar" in a.lower()]
    lines.append(f"Connected calendar: {', '.join(cal)} (check free time and, if the user agrees, add the plan's blocks as events)."
                 if cal else "No calendar connected: ask the user about fixed appointments and when they want to stop.")
    if p:
        lines += ["", f"## Today's plan (made by {p['made_by']})"]
        for it in p["items"]:
            state = " ✓ done" if it.get("done") else " (gone)" if it.get("gone") else ""
            ref = f" [id {it['block_id']}]" if it.get("block_id") else f" ({it['kind']})"
            lines.append(f"- {it['start']} · {it['minutes']} min · {it['title']}{ref}{state}")
    cands = [f"- {c['title']} [id {c['id']}] · {c['project']} · suggested {c['suggested_min']} min"
             + (f" · {forecast.fmt(c['estimate_left_min'])} of estimate left" if c["estimate_left_min"] else "")
             + (f" · {c['pomodoros_left']} pomodoros left today" if c["pomodoros_left"] else "")
             + (f" · {export.PRIORITY[c['priority']]} priority" if c["priority"] else "")
             + (f" · due {c['due_date']}" + (f" {c['due_time']}" if c.get("due_time") else "") if c["due_date"] else "")
             for c in candidates(conn)[:20]]
    lines += ["", "## Candidates (overdue, due soon, started, ready), best first", *(cands or ["- Nothing ready or due."])]
    lines.append(f"\nDaily focus goal: {forecast.fmt(focus.goal(conn))}.")
    return "\n".join(lines)


# --- picks: today's to-dos on the HUD, curated ---------------------------------------
# A pick is a to-do from a project ({"block_id": id}) or one written just for the day ({"text": …, "done": bool}):
# a call, an errand, anything that doesn't belong in a project's workflow. Those are ticked off on the HUD.
MAX_PICKS = 15


def get_picks(conn, day: str | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM day_picks WHERE day = ?", (day or _today(),)).fetchone()
    if not row:
        return None
    ids = json.loads(row["block_ids"])
    items = json.loads(row["items"]) if row["items"] else [{"block_id": i} for i in ids]
    return {"day": row["day"], "block_ids": ids, "items": items, "note": row["note"], "made_by": row["made_by"],
            "updated_at": row["updated_at"]}


def _pick(conn, item) -> dict:
    """One pick from a to-do id or a line of text (or their dict forms)."""
    from .logic import Project
    if isinstance(item, dict):
        if item.get("block_id") is not None:
            item = item["block_id"]
        else:
            return _pick(conn, str(item.get("text") or "")) | {"done": bool(item.get("done"))}
    if isinstance(item, str) and not item.strip().lstrip("#").isdigit():
        text = " ".join(item.split())[:200]
        if not text:
            raise ValueError("A to-do written for today needs some text.")
        return {"text": text, "done": False}
    try:
        i = int(str(item).strip().lstrip("#"))
    except (TypeError, ValueError):
        raise ValueError(f"“{item}” isn't a to-do id or text.")
    p = Project.for_block(conn, i)
    if p is None or i == p.root_id:
        raise ValueError(f"There is no to-do with id {i}.")
    if p.children[i]:
        raise ValueError(f"“{p.title(i)}” has to-dos inside it: pick those instead.")
    if p.is_done(i):
        raise ValueError(f"“{p.title(i)}” is already done.")
    return {"block_id": i}


def _write_picks(conn, items: list[dict], note: str, day: str, by: str) -> dict:
    if not items:
        raise ValueError("Pick at least one to-do (or go back to the automatic list instead).")
    if len(items) > MAX_PICKS:
        raise ValueError(f"Pick at most {MAX_PICKS} to-dos for a day.")
    ids = [i["block_id"] for i in items if "block_id" in i]
    conn.execute("INSERT INTO day_picks (day, block_ids, items, note, made_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                 "ON CONFLICT(day) DO UPDATE SET block_ids = excluded.block_ids, items = excluded.items, "
                 "note = excluded.note, made_by = excluded.made_by, updated_at = excluded.updated_at",
                 (day, json.dumps(ids), json.dumps(items), str(note or "").strip()[:300], by,
                  datetime.now().isoformat(timespec="seconds")))
    return get_picks(conn, day)


def save_picks(conn, items: list, note: str = "", day: str | None = None, by: str = "you",
               append: bool = False) -> dict:
    """Replace (or with `append`, add to) the day's picks: to-do ids (unfinished, nothing inside them) and lines of
    text for to-dos that aren't in any project. Ids already picked and repeated lines are skipped."""
    day = day or _today()
    date.fromisoformat(day)
    old = get_picks(conn, day) if append else None
    out = list(old["items"]) if old else []
    for item in items:
        pick = _pick(conn, item)
        key = pick.get("block_id") or pick["text"].casefold()
        if any((o.get("block_id") or o.get("text", "").casefold()) == key for o in out):
            continue
        out.append(pick)
    return _write_picks(conn, out, note or (old["note"] if old else ""), day, by if not old else old["made_by"])


def tick_pick(conn, index: int, done: bool = True, day: str | None = None) -> dict:
    """Tick off (or un-tick) one of the day's to-dos written for today."""
    picks = get_picks(conn, day)
    if not picks or not 0 <= index < len(picks["items"]) or "text" not in picks["items"][index]:
        raise ValueError("That to-do isn't on today's list.")
    picks["items"][index]["done"] = bool(done)
    return _write_picks(conn, picks["items"], picks["note"], picks["day"], picks["made_by"])


def clear_picks(conn, day: str | None = None) -> bool:
    return conn.execute("DELETE FROM day_picks WHERE day = ?", (day or _today(),)).rowcount > 0
