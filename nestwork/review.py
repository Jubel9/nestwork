"""The weekly review: what got done, where the time went, what slipped, how each project's finish date
moved, what's blocked and what's coming. Saved reviews keep the numbers and your reflection, so the next
review can show what changed.

Completions are logged by `track()` (called from main.settle after every change): a to-do that reaches its
project's last phase gets done_at and a row in `completions`; reopening it the same day removes that row
(a mis-click), reopening it later (a repeat coming back) keeps it.
"""

import json
from datetime import date, datetime, timedelta

from . import export, focus, forecast

DAYS = 7


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def track(conn, p) -> None:
    """Log to-dos finished (or reopened) in this project since the last change."""
    now = _now()
    for x, b in p.blocks.items():
        if x == p.root_id or p.children[x]:
            continue
        if p.is_done(x) and not b.get("done_at"):
            conn.execute("UPDATE blocks SET done_at = ? WHERE id = ?", (now, x))
            conn.execute("INSERT INTO completions (block_id, title, project_id, project_title, done_at) VALUES (?, ?, ?, ?, ?)",
                         (x, b["title"], p.root_id, p.title(p.root_id), now))
            b["done_at"] = now
        elif not p.is_done(x) and b.get("done_at"):
            if b["done_at"][:10] == now[:10]:
                conn.execute("DELETE FROM completions WHERE block_id = ? AND done_at = ?", (x, b["done_at"]))
            conn.execute("UPDATE blocks SET done_at = NULL WHERE id = ?", (x,))
            b["done_at"] = None


def backfill(conn) -> None:
    """To-dos finished before completions were logged get a done_at (their last change) and no log row."""
    from .logic import Project
    for r in conn.execute("SELECT id FROM blocks WHERE parent_id IS NULL").fetchall():
        p = Project(conn, r["id"])
        for x, b in p.blocks.items():
            if x != p.root_id and not p.children[x] and p.is_done(x) and not b.get("done_at"):
                conn.execute("UPDATE blocks SET done_at = ? WHERE id = ?", (b["updated_at"].replace(" ", "T"), x))


def build(conn, today: date | None = None) -> dict:
    """This week's review (the last 7 days, today included)."""
    today = today or date.today()
    start = today - timedelta(days=DAYS - 1)
    done = [dict(r) for r in conn.execute(
        "SELECT * FROM completions WHERE substr(done_at, 1, 10) BETWEEN ? AND ? ORDER BY done_at",
        (start.isoformat(), today.isoformat()))]
    stats = focus.stats(conn, DAYS, today)
    last = conn.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
    before = json.loads(last["data"])["projects"] if last else {}

    items = [i for g in export.today_items(conn, today)["groups"] for i in g["items"]]
    week_end = (today + timedelta(days=DAYS)).isoformat()
    slipped = [i for i in items if i["due_date"] and start.isoformat() <= i["due_date"] < today.isoformat()]
    overdue_older = [i for i in items if i["due_date"] and i["due_date"] < start.isoformat()]
    coming = sorted((i for i in items if i["due_date"] and today.isoformat() <= i["due_date"] <= week_end),
                    key=lambda i: (i["due_date"], i.get("due_time") or ""))
    blocked = [i for i in items if i["locked"]]

    projects = {}
    for p in export.projects(conn):
        f = forecast.forecast(conn, p, p.root_id, today) if p.children[p.root_id] else None
        prev = before.get(str(p.root_id), {})
        projects[str(p.root_id)] = {
            "id": p.root_id, "title": p.title(p.root_id), "progress": round(p.progress(p.root_id), 4),
            "done_this_week": sum(1 for d in done if d["project_id"] == p.root_id),
            "finish": f and f["finish"], "remaining_min": f and f["remaining_min"], "due_date": p.blocks[p.root_id]["due_date"],
            "late_days": f and f["late_days"],
            "progress_before": prev.get("progress"), "finish_before": prev.get("finish"),
        }
    return {
        "start": start.isoformat(), "end": today.isoformat(),
        "done": done, "done_count": len(done),
        "focus": {"total_min": stats["total"]["focus_min"], "sessions": stats["total"]["sessions"],
                  "avg_rating": stats["total"]["avg_rating"], "avg_score": stats["total"]["avg_score"],
                  "active_days": stats["total"]["active_days"], "streak": stats["streak"],
                  "goal_min": stats["goal_min"], "by_project": stats["projects"], "by_day": stats["by_day"]},
        "slipped": slipped, "overdue_older": len(overdue_older), "coming": coming[:15], "blocked": blocked[:10],
        "blocked_count": len(blocked), "projects": projects,
        "last_review": {"id": last["id"], "created_at": last["created_at"], "note": last["note"]} if last else None,
    }


def save(conn, note: str, today: date | None = None) -> dict:
    data = build(conn, today)
    keep = {"done_count": data["done_count"], "focus_min": data["focus"]["total_min"],
            "projects": {k: {"title": v["title"], "progress": v["progress"], "finish": v["finish"]} for k, v in data["projects"].items()}}
    cur = conn.execute("INSERT INTO reviews (period_start, period_end, created_at, data, note) VALUES (?, ?, ?, ?, ?)",
                       (data["start"], data["end"], _now(), json.dumps(keep), note.strip()[:5000]))
    return {"id": cur.lastrowid}


def history(conn, limit: int = 20) -> list[dict]:
    return [{"id": r["id"], "period_start": r["period_start"], "period_end": r["period_end"], "created_at": r["created_at"],
             "note": r["note"], **{k: json.loads(r["data"]).get(k) for k in ("done_count", "focus_min")}}
            for r in conn.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,))]


def due(conn, today: date | None = None) -> bool:
    """Is a review due: something happened this week (a to-do finished or focus time) and none was saved in
    the last 6 days."""
    today = today or date.today()
    last = conn.execute("SELECT created_at FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
    if last and (today - date.fromisoformat(last["created_at"][:10])).days < 6:
        return False
    since = (today - timedelta(days=DAYS - 1)).isoformat()
    return bool(conn.execute("SELECT 1 FROM completions WHERE done_at >= ? UNION SELECT 1 FROM focus_sessions "
                             "WHERE started_at >= ? LIMIT 1", (since, since)).fetchone())


def markdown(conn, today: date | None = None) -> str:
    """The review as text, for the agent."""
    d = build(conn, today)
    fmt = forecast.fmt
    out = [f"# Weekly review {d['start']} → {d['end']}", ""]
    out += [f"## Done ({d['done_count']})"] + ([f"- {x['title']} ({x['project_title']}) · {x['done_at'][:10]}" for x in d["done"]] or ["- Nothing finished this week."])
    f = d["focus"]
    out += ["", "## Focus", f"- {fmt(f['total_min'])} in {f['sessions']} sessions over {f['active_days']} days; streak {f['streak']} days"
            + (f"; average productivity {f['avg_rating']}/5" if f["avg_rating"] else "")]
    out += [f"- {p['title']}: {fmt(p['focus_min'])}" for p in f["by_project"][:6]]
    out += ["", f"## Slipped ({len(d['slipped'])} due this week and still open; {d['overdue_older']} older overdue)"]
    out += [f"- {i['title']} ({i['project']}) · was due {i['due_date']} [id {i['id']}]" for i in d["slipped"]] or ["- Nothing slipped."]
    out += ["", "## Projects"]
    for p in d["projects"].values():
        line = f"- {p['title']}: {round(p['progress'] * 100)}% done, {p['done_this_week']} finished this week"
        if p["progress_before"] is not None:
            line += f" (was {round(p['progress_before'] * 100)}% at the last review)"
        if p["finish"]:
            line += f"; forecast done around {p['finish']}" + (f" (was {p['finish_before']})" if p["finish_before"] and p["finish_before"] != p["finish"] else "")
            if p["late_days"] and p["late_days"] > 0:
                line += f", {p['late_days']} days after its due date"
        out.append(line)
    out += ["", f"## Blocked ({d['blocked_count']})"] + ([f"- {i['title']}: waits until {' and '.join(i['waiting_for'])}" for i in d["blocked"]] or ["- Nothing blocked."])
    out += ["", "## Coming up (next 7 days)"] + ([f"- {i['due_date']}{' ' + i['due_time'] if i.get('due_time') else ''} · {i['title']} ({i['project']})" for i in d["coming"]] or ["- Nothing due."])
    if d["last_review"] and d["last_review"]["note"]:
        out += ["", f"## The user's reflection last time ({d['last_review']['created_at'][:10]})", d["last_review"]["note"]]
    return "\n".join(out)
