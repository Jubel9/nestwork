"""The Calendar (Workflow → Calendar): every project's dates on one calendar, day by day.

Three kinds of things are on it:
  * fixed dates, which come from elsewhere: to-dos' due dates (with their time when they have one), the
    next rounds of repeating to-dos (shown ahead, before they exist), and reminders;
  * history, so past days keep what happened: reminders that went out (they're kept, marked sent), to-dos
    finished that day (completions), focus sessions from the timer and the activity log (what the user told
    their agent they did, activities.py). Nothing here is ever cleaned up, so
    the calendar goes back as far as Nestwork has been used;
  * the day's plan (plan.py): to-dos, events and breaks at a time, put there by you (click or drag on the
    Calendar) or by your agent (MCP `schedule`, `set_day_plan`). Those can be moved, stretched or removed.

`days()` builds the range the page asks for; `markdown()` is the agent's view (MCP `calendar`).
"""

from datetime import date, datetime, timedelta

from . import activities, plan, repeats
from .logic import Project

MAX_DAYS = 62
AHEAD = 40            # repeating to-dos: at most this many future rounds in a range


def _range(start: date, n: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _due_items(conn, first: str, last: str, today: str) -> dict[str, list[dict]]:
    """Due dates and the coming rounds of repeating to-dos, by day. Archived projects keep theirs on the
    calendar (marked archived): archiving hides a project from lists and reminders, not from its dates."""
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT id, archived_at FROM blocks WHERE parent_id IS NULL").fetchall():
        p = Project(conn, r["id"])
        for x, b in p.blocks.items():
            if x == p.root_id:
                continue
            done = p.is_done(x)
            base = {"block_id": x, "title": b["title"], "project": p.title(p.root_id), "project_id": p.root_id,
                    "priority": b.get("priority") or 0, "time": b.get("due_time"), "done": done,
                    "has_children": bool(p.children[x]), "path": " › ".join(p.path(x)[1:-1]),
                    "archived": bool(r["archived_at"])}
            due = b["due_date"]
            if due and first <= due <= last:
                out.setdefault(due, []).append({**base, "kind": "due", "date": due,
                                                "overdue": not done and due < today,
                                                "repeat_text": repeats.describe(repeats.load(b.get("repeat")))})
            rule = repeats.load(b.get("repeat"))
            nxt = repeats.load(b.get("repeat_next"))
            if not rule:
                continue
            # Where the next round starts from: its planned date once it's done for now, else after its due date.
            if done and nxt:
                cur = date.fromisoformat(nxt.get("due") or nxt["reopen"])
                if first <= cur.isoformat() <= last:
                    out.setdefault(cur.isoformat(), []).append({**base, "kind": "repeat", "date": cur.isoformat(),
                                                                "done": False, "repeat_text": repeats.describe(rule)})
            elif due:
                cur = date.fromisoformat(due)
            else:
                continue                       # a habit without a date: it's on Today, not on a day
            for _ in range(AHEAD):
                cur = repeats.next_after(rule, cur)
                if cur.isoformat() > last:
                    break
                if cur.isoformat() >= first:
                    out.setdefault(cur.isoformat(), []).append({**base, "kind": "repeat", "date": cur.isoformat(),
                                                                "done": False, "repeat_text": repeats.describe(rule)})
    return out


def _reminders(conn, first: str, last: str) -> dict[str, list[dict]]:
    """One-off reminders, the ones still to come and the ones that already went out."""
    out: dict[str, list[dict]] = {}
    rows = conn.execute("SELECT r.*, b.title AS block_title FROM reminders r LEFT JOIN blocks b ON b.id = r.block_id "
                        "WHERE substr(r.remind_at, 1, 10) BETWEEN ? AND ? ORDER BY r.remind_at",
                        (first, last)).fetchall()
    for r in rows:
        out.setdefault(r["remind_at"][:10], []).append({
            "kind": "reminder", "id": r["id"], "time": r["remind_at"][11:16], "title": r["text"],
            "block_id": r["block_id"], "block_title": r["block_title"], "by": r["created_by"],
            "sent": r["status"] != "pending"})
    return out


def _finished(conn, first: str, last: str) -> dict[str, list[dict]]:
    """To-dos finished each day, with the time (kept even after the to-do is deleted or reopened)."""
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM completions WHERE substr(done_at, 1, 10) BETWEEN ? AND ? ORDER BY done_at",
                          (first, last)).fetchall():
        out.setdefault(r["done_at"][:10], []).append({
            "kind": "finished", "id": r["id"], "time": r["done_at"][11:16], "title": r["title"],
            "block_id": r["block_id"], "project": r["project_title"] or ""})
    return out


def _focus(conn, first: str, last: str) -> dict[str, list[dict]]:
    """Focus sessions from the timer: when they ran (start to end, pauses included) and the minutes focused."""
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM focus_sessions WHERE ended_at IS NOT NULL AND focus_min > 0 "
                          "AND substr(started_at, 1, 10) BETWEEN ? AND ? ORDER BY started_at", (first, last)).fetchall():
        start = datetime.fromisoformat(r["started_at"])
        end = datetime.fromisoformat(r["ended_at"])
        span = max(5, round((min(end, start.replace(hour=23, minute=59)) - start).total_seconds() / 60))
        out.setdefault(r["started_at"][:10], []).append({
            "kind": "focus", "id": r["id"], "start": start.strftime("%H:%M"), "minutes": span,
            "focus_min": round(r["focus_min"]), "title": r["title"], "project": r["project_title"] or "",
            "block_id": r["block_id"], "rating": r["rating"], "note": r["note"], "mode": r["mode"],
            "status": r["status"]})
    return out


def _activities(conn, first: str, last: str, now: datetime | None = None) -> dict[str, list[dict]]:
    """The activity log by day. One still going runs until now (today) or the end of its day (an earlier day);
    one that crosses midnight is cut at the end of the day it started."""
    out: dict[str, list[dict]] = {}
    now = now or datetime.now()
    rows = conn.execute("SELECT id FROM activities WHERE substr(started_at, 1, 10) BETWEEN ? AND ? ORDER BY started_at",
                        (first, last)).fetchall()
    for r in rows:
        a = activities.get(conn, r["id"])
        start = datetime.fromisoformat(a["started_at"])
        day_end = start.replace(hour=23, minute=59)
        end = datetime.fromisoformat(a["ended_at"]) if a["ended_at"] else min(now, day_end)
        out.setdefault(a["started_at"][:10], []).append({
            "kind": "activity", "id": a["id"], "start": start.strftime("%H:%M"),
            "minutes": max(10, round((min(end, day_end) - start).total_seconds() / 60)),
            "real_minutes": activities.minutes(a), "end": a["ended_at"], "started_at": a["started_at"],
            "running": not a["ended_at"], "title": a["title"], "note": a["note"], "block_id": a["block_id"],
            "block_title": a["block_title"], "by": a["logged_by"]})
    return out


def days(conn, start: date, n: int = 7, today: date | None = None) -> list[dict]:
    """Each day from `start`: {"date", "plan": [items with times], "due": [...], "reminders": [...]}."""
    n = max(1, min(n, MAX_DAYS))
    today = (today or date.today()).isoformat()
    span = _range(start, n)
    due = _due_items(conn, span[0], span[-1], today)
    rem = _reminders(conn, span[0], span[-1])
    fin = _finished(conn, span[0], span[-1])
    foc = _focus(conn, span[0], span[-1])
    act = _activities(conn, span[0], span[-1])
    plans = {r["day"] for r in conn.execute("SELECT day FROM day_plans WHERE day BETWEEN ? AND ?",
                                                       (span[0], span[-1])).fetchall()}
    out = []
    for d in span:
        p = plan.get(conn, d) if d in plans else None
        out.append({
            "date": d,
            "plan": p["items"] if p else [],
            "made_by": p["made_by"] if p else None,
            "due": sorted(due.get(d, []), key=lambda i: (i["time"] or "99", -i["priority"], i["title"])),
            "reminders": rem.get(d, []),
            "finished": fin.get(d, []),
            "focus": foc.get(d, []),
            "activities": act.get(d, []),
        })
    return out


def build(conn, start: date, n: int = 7) -> dict:
    """What the Calendar page shows: the days, and the to-dos you could still put on one."""
    return {"today": date.today().isoformat(), "days": days(conn, start, n), "candidates": plan.candidates(conn)[:30]}


# --- the agent's view -----------------------------------------------------------------------
def _end(start: str, minutes: int) -> str:
    t = datetime.strptime(start, "%H:%M") + timedelta(minutes=minutes)
    return "24:00" if t.day > 1 and t.hour == 0 and t.minute == 0 else t.strftime("%H:%M")


def markdown(conn, start: date | None = None, n: int = 7) -> str:
    from .chat import now_stamp
    start = start or date.today()
    lines = [f"It's now {now_stamp()}. Calendar from {start.isoformat()} for {n} day{'s' if n != 1 else ''}.",
             "Fixed: due dates, repeating to-dos' next rounds and reminders (change a due date with `update`). "
             "Planned: what's scheduled at a time, by the user or you (`schedule`, `reschedule`, `unschedule`). "
             "Done: what actually happened (to-dos finished, focus sessions, the activity log), kept for every past day."]
    for d in days(conn, start, n):
        day = date.fromisoformat(d["date"])
        lines += ["", f"## {day.strftime('%a %d %b %Y')} ({d['date']})"]
        fixed = []
        for i in d["due"]:
            when = i["time"] or "any time"
            what = "🔁 next round of" if i["kind"] == "repeat" else "overdue:" if i.get("overdue") else "due:"
            state = " ✓ done" if i["done"] else ""
            fixed.append(f"- {when} · {what} {i['title']} [id {i['block_id']}] · {i['project']}"
                         f"{' (archived project)' if i.get('archived') else ''}{state}")
        for r in d["reminders"]:
            fixed.append(f"- {r['time']} · ⏰ reminder #{r['id']}: {r['title']}" + (" (went out)" if r["sent"] else ""))
        planned = []
        for it in d["plan"]:
            ref = f" [to-do id {it['block_id']}]" if it.get("block_id") else f" ({it['kind']})"
            state = " ✓ done" if it.get("done") else " (deleted to-do)" if it.get("gone") else ""
            who = f" · by {it['by']}" if it.get("by") and it["by"] != "you" else ""
            planned.append(f"- {it['start']}–{_end(it['start'], it['minutes'])} · {it['title']}{ref}{state}"
                           f" · item {it['id']}{who}")
        lines += ["Fixed:", *(fixed or ["- nothing"]), "Planned:", *(planned or ["- nothing yet"])]
        done = [f"- {f['start']}–{_end(f['start'], f['minutes'])} · focused {f['focus_min']} min on {f['title']}"
                + (f" · {f['project']}" if f["project"] and f["project"] != f["title"] else "")
                + (f" · rated {f['rating']}/5" if f["rating"] else "") for f in d["focus"]]
        done += [f"- {a['start']}–{a['end'][11:16] if a['end'] else 'still going'} · {a['title']} (activity #{a['id']})"
                 + (f" · {a['note']}" if a["note"] else "") for a in d["activities"]]
        done += [f"- {c['time']} · ✓ finished {c['title']}" + (f" · {c['project']}" if c["project"] else "")
                 for c in d["finished"]]
        if done:
            lines += ["Done:", *done]
    return "\n".join(lines)


def parse_day(text: str | None) -> date:
    """"today", "tomorrow", a weekday name ("friday": the next one) or YYYY-MM-DD."""
    t = (text or "today").strip().lower()
    today = date.today()
    if t in ("", "today"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(names):
        if t in (name, name[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7 or 7)
    try:
        return date.fromisoformat(t)
    except ValueError:
        raise ValueError(f"“{text}” isn't a day: use YYYY-MM-DD, today, tomorrow or a weekday name.")
