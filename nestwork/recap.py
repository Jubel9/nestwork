"""What already happened on a day, gathered from what Nestwork recorded, so a new chat session can look it
up instead of asking the user: earlier chats, finished to-dos, focus sessions, notes, the plan and picks,
reminders and scheduled task runs. Also the end of the last conversations (both sides), which every new chat
reads first, and a search over past chats.

Nothing here is kept in the agent's context: it reads it with the `recent_chats`, `day_recap` and `search_chats` tools.
"""

from datetime import date, timedelta

from . import activities, plan

CHAT_CLIP = 160          # characters kept per chat message in a recap
CHAT_MAX = 30            # newest user messages kept per day (replies: search_chats)
RECENT_MAX = 20          # newest messages, both sides, in recent_chats
RECENT_CLIP = 400        # characters kept per message there


def parse_day(text: str | None) -> date:
    """"today", "yesterday", "N days ago" or YYYY-MM-DD."""
    t = (text or "today").strip().lower()
    if t in ("", "today"):
        return date.today()
    if t == "yesterday":
        return date.today() - timedelta(days=1)
    if t.endswith("days ago") and t.split()[0].isdigit():
        return date.today() - timedelta(days=int(t.split()[0]))
    try:
        return date.fromisoformat(t)
    except ValueError:
        raise ValueError(f"“{text}” isn't a day: use today, yesterday, “3 days ago” or YYYY-MM-DD.")


def _clip(text: str, n: int = CHAT_CLIP) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _hm(stamp: str) -> str:
    return (stamp or "")[11:16]


def chats(conn, day: str) -> list[dict]:
    """The day's newest messages from the user (local time), oldest first; replies, reminders and events left out."""
    rows = conn.execute(
        "SELECT * FROM (SELECT id, session_id, role, text, datetime(created_at, 'localtime') AS at FROM chat_messages "
        "WHERE role = 'user' AND date(created_at, 'localtime') = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
        (day, CHAT_MAX)).fetchall()
    return [dict(r) for r in rows]


def markdown(conn, d: date, agent_name: str = "agent") -> str:
    day = d.isoformat()
    label = "Today" if d == date.today() else d.strftime("%a %d %b %Y")
    out = [f"# {label} so far" if d == date.today() else f"# {label}"]

    done = conn.execute("SELECT title, project_title, done_at FROM completions WHERE date(done_at) = ? ORDER BY done_at",
                        (day,)).fetchall()
    if done:
        by_project: dict[str, list] = {}
        for r in done:
            by_project.setdefault(r["project_title"] or "No project", []).append(r)
        out += ["", f"## Finished ({len(done)})"]
        for proj, rs in by_project.items():
            out.append(f"- {proj} ({_hm(rs[0]['done_at'])}–{_hm(rs[-1]['done_at'])}): "
                       + _clip("; ".join(r["title"] for r in rs), 300))

    focus = conn.execute("SELECT title, project_title, started_at, focus_min, status, rating, note FROM focus_sessions "
                         "WHERE date(started_at) = ? ORDER BY started_at", (day,)).fetchall()
    if focus:
        total = sum(r["focus_min"] or 0 for r in focus)
        out += ["", f"## Focus ({round(total)} min)"]
        for r in focus:
            extra = [r["status"]] + ([f"felt {r['rating']}/5"] if r["rating"] else []) + ([f"note: {_clip(r['note'], 120)}"] if r["note"] else [])
            out.append(f"- {_hm(r['started_at'])} {r['title']}" + (f" ({r['project_title']})" if r["project_title"] else "")
                       + f", {round(r['focus_min'] or 0)} min, " + ", ".join(extra))

    acts = activities.for_day(conn, day)
    if acts:
        out += ["", "## Activity log (what the user did, logged from chat; change with change_activity)"] + [
            f"- {activities.line(a, day)}" for a in acts]

    picks = plan.get_picks(conn, day)
    if picks:
        lines = []
        for it in picks["items"]:
            if "text" in it:
                lines.append(f"- [{'x' if it.get('done') else ' '}] {it['text']}")
            else:
                r = conn.execute("SELECT title FROM blocks WHERE id = ?", (it["block_id"],)).fetchone()
                lines.append(f"- {r['title'] if r else '(deleted)'} [id {it['block_id']}]")
        out += ["", f"## Today's to-do list (by {picks['made_by']})" + (f": {picks['note']}" if picks["note"] else "")] + lines

    dp = plan.get(conn, day)
    if dp and dp["items"]:
        out += ["", f"## Day plan (by {dp['made_by']})"] + [
            f"- {it['start']} {it.get('title') or ''} ({it.get('minutes', 0)} min)" for it in dp["items"]]

    notes = conn.execute("SELECT n.text, n.author, n.created_at, b.title FROM block_notes n JOIN blocks b ON b.id = n.block_id "
                         "WHERE n.kind = 'note' AND date(n.created_at) = ? ORDER BY n.created_at", (day,)).fetchall()
    if notes:   # phase moves are left out: "Finished" covers what matters of them
        out += ["", "## Notes written on blocks"] + [
            f"- {_hm(r['created_at'])} {r['title']}: {_clip(r['text'], 120)} ({agent_name if r['author'] == 'agent' else 'user'})"
            for r in notes]

    rem = conn.execute("SELECT text, remind_at, status FROM reminders WHERE date(remind_at) = ? ORDER BY remind_at", (day,)).fetchall()
    if rem:
        out += ["", "## Reminders"] + [f"- {r['remind_at'][11:16]} {r['text']} ({r['status']})" for r in rem]

    try:
        runs = conn.execute("SELECT j.name, r.started_at, r.status, r.output FROM agent_job_runs r JOIN agent_jobs j ON j.id = r.job_id "
                            "WHERE date(r.started_at) = ? AND r.status NOT IN ('skipped', 'running') ORDER BY r.started_at",
                            (day,)).fetchall()
    except Exception:   # the scheduled tasks' tables don't exist until the app has started once
        runs = []
    if runs:
        out += ["", "## Scheduled tasks that ran (full text: scheduled_task_results)"] + [
            f"- {_hm(r['started_at'])} “{r['name']}” {r['status']}: {_clip(r['output'], 120)}" for r in runs]

    msgs = chats(conn, day)
    if msgs:
        sessions = len({m["session_id"] for m in msgs})
        out += ["", f"## What the user said in chat ({sessions} chat{'s' if sessions != 1 else ''}; newest {CHAT_MAX} at most, "
                    "clipped; search_chats for the full text and your replies)"]
        last = None
        for m in msgs:
            if m["session_id"] != last and last is not None:
                out.append("- — new chat —")
            last = m["session_id"]
            out.append(f"- {_hm(m['at'])} {_clip(m['text'])}")

    return "\n".join(out) if len(out) > 1 else out[0] + "\n\nNothing recorded for this day."


def recent_chats(conn, limit: int = RECENT_MAX) -> list[dict]:
    """The newest chat messages, the user's and the agent's, oldest first; reminders and events left out."""
    rows = conn.execute(
        "SELECT * FROM (SELECT id, session_id, role, text, datetime(created_at, 'localtime') AS at FROM chat_messages "
        "WHERE role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?) ORDER BY id",
        (max(1, min(limit, 60)),)).fetchall()
    return [dict(r) for r in rows]


def recent_markdown(conn, limit: int = RECENT_MAX) -> str:
    msgs = recent_chats(conn, limit)
    if not msgs:
        return "No earlier conversations yet."
    out = [f"# Your last conversations (newest {len(msgs)} messages, oldest first, clipped; search_chats for more)"]
    last = None
    for m in msgs:
        if m["session_id"] != last and last is not None:
            out.append("- — new chat —")
        last = m["session_id"]
        out.append(f"- {m['at'][:16]} {'User' if m['role'] == 'user' else 'You'}: {_clip(m['text'], RECENT_CLIP)}")
    return "\n".join(out)


def pointer(conn, d: date | None = None) -> str | None:
    """The note at the top of a new chat session's first message: read the last conversations first (and the day's
    history when there is some). None when there's nothing at all to look up."""
    day = (d or date.today()).isoformat()
    msgs = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE role IN ('user', 'assistant') "
                        "AND date(created_at, 'localtime') = ?", (day,)).fetchone()[0]
    focus = conn.execute("SELECT COUNT(*) FROM focus_sessions WHERE date(started_at) = ?", (day,)).fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM completions WHERE date(done_at) = ?", (day,)).fetchone()[0]
    logged = conn.execute("SELECT COUNT(*) FROM activities WHERE date(started_at) = ?", (day,)).fetchone()[0]
    going = activities.running(conn)
    parts = [f"{n} {what}" for n, what in ((msgs, "chat messages"), (focus, "focus sessions"), (done, "to-dos finished"),
                                           (logged, "activities logged")) if n]
    earlier = conn.execute("SELECT 1 FROM chat_messages WHERE role IN ('user', 'assistant') LIMIT 1").fetchone()
    if not parts and not going and not earlier:
        return None
    today = f" Today so far: {', '.join(parts)}." if parts else ""
    now = (" Still going: " + "; ".join(f"“{a['title']}” since {a['started_at'][11:16]} (#{a['id']})" for a in going) + ".") if going else ""
    steps = "call recent_chats to read how your last conversations went" if earlier else ""
    if parts or going:
        steps += (", then " if steps else "call ") + "day_recap for the rest of today"
    return (f"[This is a new chat, so you don't remember earlier ones.{today}{now} Standard first step, before you "
            f"reply: {steps}. Pick up from there the way you'd remember it; don't recite it back.]")


def search_chats(conn, query: str, days: int = 30, limit: int = 15) -> list[dict]:
    """Past chat messages containing every word of `query`, newest first."""
    words = [w for w in query.lower().split() if w][:6]
    if not words:
        raise ValueError("Give a word or two to search for.")
    since = (date.today() - timedelta(days=max(1, days))).isoformat()
    where = " AND ".join("lower(text) LIKE ?" for _ in words)
    rows = conn.execute(f"SELECT id, role, text, datetime(created_at, 'localtime') AS at FROM chat_messages "
                        f"WHERE role IN ('user', 'assistant') AND date(created_at, 'localtime') >= ? AND {where} "
                        f"ORDER BY id DESC LIMIT ?", (since, *[f"%{w}%" for w in words], max(1, min(limit, 50)))).fetchall()
    return [dict(r) for r in rows]


def snippet(text: str, query: str, width: int = 240) -> str:
    """The part of `text` around the first word of `query` it contains."""
    flat = " ".join((text or "").split())
    low = flat.lower()
    pos = min((p for w in query.lower().split() if (p := low.find(w)) >= 0), default=0)
    start = max(0, pos - width // 3)
    part = flat[start:start + width]
    return ("…" if start else "") + part + ("…" if start + width < len(flat) else "")
