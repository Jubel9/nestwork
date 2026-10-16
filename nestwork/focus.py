"""Focus sessions (the Pomodoro timer's log) and the productivity stats built from them.

Each focus session records what you worked on (a to-do, a whole project, or free text), when, how many
minutes you actually focused, whether it ran to the end, and optionally how productive it felt (1-5) and
a note. A session is either a Pomodoro (counts down a set length) or a stopwatch (counts up until you
press Finish; it can start without a label and be named when it ends). Times are local ("YYYY-MM-DDTHH:MM:SS"), like reminders. The agent reads all of it through the
`focus_stats` MCP tool and the snapshot, so it can answer "how productive was I this week?".
"""

from datetime import date, datetime, timedelta

from .logic import Project

GOAL_KEY = "focus_goal_min"
DEFAULT_GOAL = 120          # minutes of focus a day
MIN_COUNTED = 1.0           # a session stopped before one minute isn't kept
MODES = ("pomodoro", "stopwatch")
STOPWATCH_MAX = 16 * 60     # minutes: the longest a stopwatch session can be
STALE_STOPWATCH = 4 * 60    # a stopwatch left running when another session starts counts at most this
POMODORO_MIN = 25           # a stopwatch session counts one pomodoro (of the daily plan) per this many minutes
UNTITLED = "Untitled session"


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _stamp(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def goal(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (GOAL_KEY,)).fetchone()
    try:
        return max(5, int(row["value"])) if row else DEFAULT_GOAL
    except ValueError:
        return DEFAULT_GOAL


def set_goal(conn, minutes: int) -> int:
    minutes = max(5, min(16 * 60, int(minutes)))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (GOAL_KEY, str(minutes)))
    return minutes


# --- recording ----------------------------------------------------------------------------
def _resolve(conn, block_id: int | None, project_id: int | None, title: str) -> tuple:
    """(block_id, project_id, title, project_title) for what a session is about."""
    project_title = None
    if block_id is not None:
        p = Project.for_block(conn, block_id)
        if p is None:
            raise ValueError("That to-do no longer exists.")
        project_id, project_title = p.root_id, p.title(p.root_id)
        title = title.strip() or p.title(block_id)
        if block_id == p.root_id:
            block_id = None            # the whole project, not one to-do in it
    elif project_id is not None:
        p = Project.for_block(conn, project_id)
        if p is None:
            raise ValueError("That project no longer exists.")
        project_id, project_title = p.root_id, p.title(p.root_id)
        title = title.strip() or project_title
    return block_id, project_id, title.strip(), project_title


def start(conn, *, block_id: int | None, project_id: int | None, title: str, planned_min: float,
          mode: str = "pomodoro") -> dict:
    """Start a focus session. A session still marked running (e.g. the tab was closed) is closed first.
    A stopwatch may start without saying what it's for (it's named when it finishes)."""
    if mode not in MODES:
        raise ValueError("A focus session is a pomodoro or a stopwatch.")
    now = _now()
    for r in conn.execute("SELECT * FROM focus_sessions WHERE status = 'running'").fetchall():
        elapsed = (now - datetime.fromisoformat(r["started_at"])).total_seconds() / 60
        cap = STALE_STOPWATCH if r["mode"] == "stopwatch" else r["planned_min"]
        _close(conn, r["id"], min(elapsed, cap), completed=False, at=now)
    block_id, project_id, title, project_title = _resolve(conn, block_id, project_id, title)
    if not title:
        if mode != "stopwatch":
            raise ValueError("Say what you're working on first.")
        title = UNTITLED
    cur = conn.execute(
        "INSERT INTO focus_sessions (block_id, project_id, title, project_title, started_at, planned_min, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (block_id, project_id, title[:200], project_title, _stamp(now), 0 if mode == "stopwatch" else planned_min, mode))
    return get(conn, cur.lastrowid)


def retag(conn, sid: int, *, block_id: int | None, project_id: int | None, title: str) -> dict:
    """Say (or change) what a session was about: a to-do, a project, or a label of your own."""
    if get(conn, sid) is None:
        raise ValueError("That focus session no longer exists.")
    block_id, project_id, title, project_title = _resolve(conn, block_id, project_id, title)
    if not title:
        raise ValueError("Say what it was: pick something or type a name.")
    conn.execute("UPDATE focus_sessions SET block_id = ?, project_id = ?, title = ?, project_title = ? WHERE id = ?",
                 (block_id, project_id, title[:200], project_title, sid))
    return get(conn, sid)


def labels(conn, limit: int = 15) -> list[str]:
    """Your own session names (not to-dos or projects), most used recently first, for picking again."""
    rows = conn.execute(
        "SELECT title, COUNT(*) AS n, MAX(started_at) AS last FROM focus_sessions "
        "WHERE block_id IS NULL AND project_id IS NULL AND title != ? GROUP BY lower(title) "
        "ORDER BY last DESC, n DESC LIMIT ?", (UNTITLED, limit)).fetchall()
    return [r["title"] for r in rows]


def pomodoros_today(conn, day: date) -> dict[int, int]:
    """Pomodoros done today per to-do, for the daily plan: each finished pomodoro, plus one per
    POMODORO_MIN minutes of stopwatch time."""
    rows = conn.execute(
        "SELECT block_id, mode, COUNT(*) AS n, SUM(focus_min) AS mins FROM focus_sessions "
        "WHERE block_id IS NOT NULL AND substr(started_at, 1, 10) = ? AND "
        "((mode = 'pomodoro' AND status = 'completed') OR (mode = 'stopwatch' AND status != 'running')) "
        "GROUP BY block_id, mode", (day.isoformat(),)).fetchall()
    out: dict[int, int] = {}
    for r in rows:
        out[r["block_id"]] = out.get(r["block_id"], 0) + (r["n"] if r["mode"] == "pomodoro" else int((r["mins"] or 0) // POMODORO_MIN))
    return out


def _close(conn, sid: int, focus_min: float, completed: bool, at: datetime | None = None) -> bool:
    """Returns False when the session was too short to keep (it's removed)."""
    if not completed and focus_min < MIN_COUNTED:
        conn.execute("DELETE FROM focus_sessions WHERE id = ?", (sid,))
        return False
    conn.execute("UPDATE focus_sessions SET ended_at = ?, focus_min = ?, status = ? WHERE id = ?",
                 (_stamp(at or _now()), round(max(0.0, focus_min), 2), "completed" if completed else "stopped", sid))
    return True


def finish(conn, sid: int, focus_min: float, completed: bool) -> dict | None:
    row = conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (sid,)).fetchone()
    if row is None:
        raise ValueError("That focus session no longer exists.")
    focus_min = min(focus_min, STOPWATCH_MAX if row["mode"] == "stopwatch" else row["planned_min"] + 1)
    if row["status"] != "running":     # already closed (e.g. twice from two tabs): just update the minutes
        conn.execute("UPDATE focus_sessions SET focus_min = MAX(focus_min, ?) WHERE id = ?", (focus_min, sid))
        return get(conn, sid)
    return get(conn, sid) if _close(conn, sid, focus_min, completed) else None


def rate(conn, sid: int, rating: int | None = None, note: str | None = None) -> dict:
    if get(conn, sid) is None:
        raise ValueError("That focus session no longer exists.")
    if rating is not None:
        conn.execute("UPDATE focus_sessions SET rating = ? WHERE id = ?", (max(1, min(5, rating)) if rating else None, sid))
    if note is not None:
        conn.execute("UPDATE focus_sessions SET note = ? WHERE id = ?", (note.strip()[:1000], sid))
    return get(conn, sid)


def delete(conn, sid: int) -> None:
    conn.execute("DELETE FROM focus_sessions WHERE id = ?", (sid,))


def get(conn, sid: int) -> dict | None:
    r = conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (sid,)).fetchone()
    return dict(r) if r else None


def current(conn) -> dict | None:
    r = conn.execute("SELECT * FROM focus_sessions WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


# --- stats ----------------------------------------------------------------------------------
def _score(focus_min: float, goal_min: int, ratings: list[int], completed: int, closed: int) -> int | None:
    """0-100 for a day: 70% how much of the focus goal you reached, 30% how productive it felt
    (your average rating; the share of sessions you finished when you didn't rate any)."""
    if not closed:
        return None
    quality = (sum(ratings) / len(ratings) - 1) / 4 if ratings else completed / closed
    return round(100 * (0.7 * min(1.0, focus_min / goal_min) + 0.3 * quality))


def _project_key(r: dict) -> str:
    """Sessions group by project; a deleted project's sessions stay together under its old name."""
    if r["project_id"]:
        return str(r["project_id"])
    return f"t:{r['project_title']}" if r["project_title"] else "other"


def stats(conn, days: int = 7, today: date | None = None) -> dict:
    today = today or date.today()
    days = max(1, min(366, days))
    first = today - timedelta(days=days - 1)
    goal_min = goal(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM focus_sessions WHERE started_at >= ? ORDER BY started_at DESC",
        (first.isoformat(),)).fetchall()]
    closed = [r for r in rows if r["status"] != "running"]

    def day_summary(items):
        done = [r for r in items if r["status"] != "running"]
        mins = sum(r["focus_min"] for r in done)
        ratings = [r["rating"] for r in done if r["rating"]]
        completed = sum(r["status"] == "completed" for r in done)
        return {"focus_min": round(mins, 1), "sessions": len(done), "completed": completed,
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "score": _score(mins, goal_min, ratings, completed, len(done))}

    by_day = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        items = [r for r in closed if r["started_at"][:10] == d]
        per_project = {}
        for r in items:
            key = _project_key(r)
            per_project[key] = round(per_project.get(key, 0) + r["focus_min"], 1)
        by_day.append({"date": d, **day_summary(items), "by_project": per_project})

    projects = {}
    for r in closed:
        key = _project_key(r)
        p = projects.setdefault(key, {"key": key, "id": r["project_id"], "title": r["project_title"] or "No project",
                                      "focus_min": 0.0, "sessions": 0, "ratings": []})
        p["focus_min"] += r["focus_min"]
        p["sessions"] += 1
        if r["rating"]:
            p["ratings"].append(r["rating"])
    project_list = sorted(({**{k: v for k, v in p.items() if k != "ratings"}, "focus_min": round(p["focus_min"], 1),
                            "avg_rating": round(sum(p["ratings"]) / len(p["ratings"]), 2) if p["ratings"] else None}
                           for p in projects.values()), key=lambda p: -p["focus_min"])

    tasks = {}
    for r in closed:
        t = tasks.setdefault((r["block_id"], r["title"]), {"block_id": r["block_id"], "title": r["title"],
                                                          "project": r["project_title"], "focus_min": 0.0, "sessions": 0})
        t["focus_min"] += r["focus_min"]
        t["sessions"] += 1
    task_list = sorted(({**t, "focus_min": round(t["focus_min"], 1)} for t in tasks.values()), key=lambda t: -t["focus_min"])[:10]

    hours = [{"hour": h, "focus_min": 0.0, "ratings": []} for h in range(24)]
    for r in closed:
        start_dt = datetime.fromisoformat(r["started_at"])
        h = hours[start_dt.hour]
        h["focus_min"] += r["focus_min"]
        if r["rating"]:
            h["ratings"].append(r["rating"])
    hour_list = [{"hour": h["hour"], "focus_min": round(h["focus_min"], 1),
                  "avg_rating": round(sum(h["ratings"]) / len(h["ratings"]), 2) if h["ratings"] else None} for h in hours]
    busiest = max(hour_list, key=lambda h: h["focus_min"])

    # Streak: days in a row (ending today, or yesterday if nothing yet today) with a finished session.
    done_days = {r[0] for r in conn.execute(
        "SELECT DISTINCT substr(started_at, 1, 10) FROM focus_sessions WHERE status = 'completed'").fetchall()}
    streak, d = 0, today if today.isoformat() in done_days else today - timedelta(days=1)
    while d.isoformat() in done_days:
        streak += 1
        d -= timedelta(days=1)

    total = day_summary(rows)
    scored = [x["score"] for x in by_day if x["score"] is not None]
    return {
        "from": first.isoformat(), "to": today.isoformat(), "days": days, "goal_min": goal_min,
        "today": next(x for x in by_day if x["date"] == today.isoformat()),
        "total": {**total, "completion_rate": round(total["completed"] / total["sessions"], 2) if total["sessions"] else None,
                  "active_days": sum(1 for x in by_day if x["sessions"]),
                  "avg_score": round(sum(scored) / len(scored)) if scored else None},
        "streak": streak,
        "by_day": by_day,
        "projects": project_list,
        "tasks": task_list,
        "hours": hour_list,
        "best_hour": busiest["hour"] if busiest["focus_min"] else None,
        "sessions": rows[:200],
        "current": current(conn),
    }


# --- for the agent -----------------------------------------------------------------------------
def _fmt_min(m: float) -> str:
    m = round(m)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _session_line(r: dict) -> str:
    when = r["started_at"][:16].replace("T", " ")
    what = f"**{r['title']}**" + (f" ({r['project_title']})" if r["project_title"] and r["project_title"] != r["title"] else "")
    state = {"completed": "finished", "stopped": "stopped early", "running": "running now"}[r["status"]]
    bits = [f"{_fmt_min(r['focus_min'])} (stopwatch)" if r.get("mode") == "stopwatch"
            else f"{_fmt_min(r['focus_min'])} of {r['planned_min']:g} planned", state]
    if r["rating"]:
        bits.append(f"productivity {r['rating']}/5")
    line = f"- {when} · {what} · " + " · ".join(bits)
    if r["block_id"]:
        line += f" [id {r['block_id']}]"
    if r["note"]:
        line += f" — note: {r['note']}"
    return line


def current_line(conn, now: datetime | None = None) -> str | None:
    cur = current(conn)
    if not cur:
        return None
    since = datetime.fromisoformat(cur["started_at"])
    elapsed = ((now or _now()) - since).total_seconds() / 60
    where = f" ({cur['project_title']})" if cur["project_title"] and cur["project_title"] != cur["title"] else ""
    if cur["mode"] == "stopwatch":
        what = "a stopwatch session" + ("" if cur["title"] == UNTITLED else f" on **{cur['title']}**{where}")
        return f"Right now the user is in {what}, started {since:%H:%M} (counting up, no set length; may be paused)."
    return (f"Right now the user is in a focus session on **{cur['title']}**{where}, started {since:%H:%M} "
            f"({_fmt_min(min(elapsed, cur['planned_min']))} in of {cur['planned_min']:g} min planned).")


def markdown(conn, days: int = 7, sessions: bool = True) -> str:
    s = stats(conn, days)
    t, td = s["total"], s["today"]
    out = []
    if line := current_line(conn):
        out += [line, ""]
    out += [f"Daily focus goal: {_fmt_min(s['goal_min'])}. Streak: {s['streak']} day(s) in a row with a finished session.",
            "",
            f"**Today:** {_fmt_min(td['focus_min'])} focused in {td['sessions']} session(s), {td['completed']} finished"
            + (f", average productivity {td['avg_rating']}/5" if td["avg_rating"] else "")
            + (f", score {td['score']}/100" if td["score"] is not None else "") + ".",
            f"**Last {s['days']} days ({s['from']} to {s['to']}):** {_fmt_min(t['focus_min'])} in {t['sessions']} session(s) "
            f"on {t['active_days']} day(s)"
            + (f", {round(t['completion_rate'] * 100)}% finished" if t["completion_rate"] is not None else "")
            + (f", average productivity {t['avg_rating']}/5" if t["avg_rating"] else "")
            + (f", average daily score {t['avg_score']}/100" if t["avg_score"] is not None else "") + "."]
    if s["best_hour"] is not None:
        out.append(f"Focuses most around {s['best_hour']:02d}:00.")
    if s["projects"]:
        out += ["", "By project:"] + [f"- {p['title']}: {_fmt_min(p['focus_min'])} in {p['sessions']} session(s)"
                                      + (f", productivity {p['avg_rating']}/5" if p["avg_rating"] else "") for p in s["projects"]]
    out += ["", "Per day:"] + [f"- {d['date']}: {_fmt_min(d['focus_min'])}, {d['sessions']} session(s)"
                               + (f", score {d['score']}/100" if d["score"] is not None else "") for d in s["by_day"]]
    if sessions and s["sessions"]:
        out += ["", "Sessions (newest first):"] + [_session_line(r) for r in s["sessions"][:40]]
    out += ["", "_Score: 70% how much of the daily goal was reached, 30% how productive sessions felt "
                "(the average 1-5 rating, or the share finished when unrated)._"]
    return "\n".join(out)
