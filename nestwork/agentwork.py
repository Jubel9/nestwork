"""Agent's Work: tasks the agent runs on a schedule by itself ("brief me every morning at 7").

A task is a prompt plus a schedule: a 5-field cron expression (``min hour day month weekday``, local time,
e.g. ``0 7 * * *``) or a one-off ``once YYYY-MM-DDTHH:MM``. The web server checks every 30 seconds (see
main.py); each run is a fresh headless Claude session with the Nestwork tools (not the chat's session, so the
chat stays free), its answer is saved as a run and, unless turned off, posted into the chat.

A run that was missed while Nestwork was off (more than GRACE late) is never run late: it's logged as skipped
with how many times it was missed, and the agent is told once, at the start of the user's next chat message, so
it can ask whether to run any of them now (see missed_note). Created from the Agent's Work tab or by the agent through the MCP ``schedule_task`` tool.

A task with nothing left to run (a one-off that's done, a schedule with no future date) moves to the archive by
itself, and so does a deleted one, with its run history: restore it from there, or delete it forever.
"""

import asyncio
import difflib
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta

from . import db, sounds

GRACE = timedelta(minutes=5)   # later than this, a run counts as missed (the server checks every 30 s)
MAX_MISSED = 999
RUN_TIMEOUT = 15 * 60
MAX_OUTPUT = 60_000
FMT = "%Y-%m-%dT%H:%M"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    schedule     TEXT NOT NULL,              -- cron "0 7 * * *" or "once 2026-10-14T08:00" (local time)
    enabled      INTEGER NOT NULL DEFAULT 1,
    deliver_chat INTEGER NOT NULL DEFAULT 1, -- also post each result into the chat
    next_run     TEXT,                       -- local "YYYY-MM-DDTHH:MM", NULL when nothing is coming
    run_now      INTEGER NOT NULL DEFAULT 0, -- asked to run once off-schedule (from the MCP server's process)
    notify       TEXT,                       -- its notification sound: NULL default, "none", or a notifications id
    created_by   TEXT NOT NULL DEFAULT 'you',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at  TEXT                        -- local "YYYY-MM-DDTHH:MM:SS" once it's in the archive (done or deleted)
);
CREATE TABLE IF NOT EXISTS agent_job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES agent_jobs(id) ON DELETE CASCADE,
    trigger     TEXT NOT NULL,               -- schedule | manual
    status      TEXT NOT NULL,               -- running | ok | failed | stopped | skipped
    started_at  TEXT NOT NULL,               -- local "YYYY-MM-DDTHH:MM:SS"
    finished_at TEXT,
    output      TEXT NOT NULL DEFAULT '',
    tools       TEXT,                        -- JSON list of tool labels used
    images      TEXT,                        -- JSON list of picture URLs it made (selfies, drawings)
    cost        REAL,
    message_id  INTEGER                      -- the chat message it was posted as
);
CREATE INDEX IF NOT EXISTS agent_job_runs_job ON agent_job_runs (job_id, id);
"""

# --- schedules ------------------------------------------------------------------------------
FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 7))   # 0 and 7 = Sunday
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
SHORTCUTS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
             "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}


def _field(text: str, name: str, lo: int, hi: int) -> set[int]:
    names = MONTHS if name == "month" else DAYS if name == "weekday" else {}
    out: set[int] = set()
    for part in text.lower().split(","):
        rng, _, step = part.partition("/")
        if step and not step.isdigit() or step == "0":
            raise ValueError(f"bad step in the {name} field: “{part}”")
        if rng == "*":
            a, b = lo, hi
        else:
            ends = [names.get(x, x) for x in rng.split("-")]
            try:
                ends = [int(x) for x in ends]
            except ValueError:
                raise ValueError(f"“{part}” isn't a valid {name}")
            if len(ends) > 2:
                raise ValueError(f"“{part}” isn't a valid {name}")
            a, b = ends[0], ends[-1] if len(ends) == 2 else (hi if step else ends[0])
        if not (lo <= a <= hi and lo <= b <= hi) or a > b:
            raise ValueError(f"the {name} “{part}” is out of range ({lo}–{hi})")
        out.update(range(a, b + 1, int(step or 1)))
    return {0 if x == 7 else x for x in out} if name == "weekday" else out


def parse(schedule: str) -> dict:
    """{'kind': 'once', 'at': datetime} or {'kind': 'cron', 'expr', 'minute', 'hour', …}. Raises ValueError."""
    s = " ".join((schedule or "").strip().split())
    if s.lower().startswith("once"):
        raw = s[4:].strip().replace(" ", "T")
        try:
            return {"kind": "once", "at": datetime.fromisoformat(raw).replace(second=0, microsecond=0, tzinfo=None)}
        except ValueError:
            raise ValueError(f"“{s[4:].strip()}” isn't a date and time like 2026-10-14 08:00")
    s = SHORTCUTS.get(s.lower(), s)
    parts = s.split(" ")
    if len(parts) != 5:
        raise ValueError("a schedule is 5 fields (minute hour day month weekday), e.g. “0 7 * * *”, "
                         "or “once 2026-10-14 08:00”")
    out = {"kind": "cron", "expr": s}
    for text, (name, lo, hi) in zip(parts, FIELDS):
        out[name] = _field(text, name, lo, hi)
    out["any_day"], out["any_weekday"] = parts[2] == "*", parts[4] == "*"
    return out


def normalize(schedule: str) -> str:
    p = parse(schedule)
    return f"once {p['at']:{FMT}}" if p["kind"] == "once" else p["expr"]


def _day_ok(p: dict, d: datetime) -> bool:
    if d.month not in p["month"]:
        return False
    dom, dow = d.day in p["day"], (d.weekday() + 1) % 7 in p["weekday"]
    if p["any_day"] or p["any_weekday"]:      # cron: when both are restricted, either one matches
        return dom and dow
    return dom or dow


def next_after(schedule: str, after: datetime) -> datetime | None:
    """The first run strictly after `after` (None for a one-off that's already past)."""
    p = parse(schedule) if isinstance(schedule, str) else schedule
    after = after.replace(second=0, microsecond=0)
    if p["kind"] == "once":
        return p["at"] if p["at"] > after else None
    day = after.replace(hour=0, minute=0)
    for _ in range(366 * 5):
        if _day_ok(p, day):
            for h in sorted(p["hour"]):
                for m in sorted(p["minute"]):
                    t = day.replace(hour=h, minute=m)
                    if t > after:
                        return t
        day += timedelta(days=1)
    return None


def upcoming_runs(schedule: str, n: int = 3, after: datetime | None = None) -> list[datetime]:
    out, t = [], after or datetime.now()
    for _ in range(n):
        t = next_after(schedule, t)
        if t is None:
            break
        out.append(t)
    return out


def describe(schedule: str) -> str:
    """In words: "Every day at 07:00", "Weekdays at 08:30", "Every 30 minutes", "Once on Tue 14 Oct at 08:00"."""
    try:
        p = parse(schedule)
    except ValueError:
        return schedule
    if p["kind"] == "once":
        return f"Once on {p['at']:%a %d %b %Y} at {p['at']:%H:%M}"
    mins, hours, parts = sorted(p["minute"]), sorted(p["hour"]), p["expr"].split(" ")
    if parts[2:] == ["*", "*", "*"] or parts[2:4] == ["*", "*"]:
        days = sorted(p["weekday"])
        when = ("Every day" if p["any_weekday"] or len(days) == 7 else "Weekdays" if days == [1, 2, 3, 4, 5]
                else "Weekends" if days == [0, 6]
                else "Every " + ", ".join(DAY_NAMES[d] for d in sorted(days, key=lambda d: (d + 6) % 7)))
        if len(hours) == 24 and len(mins) > 1 and parts[0].startswith("*/"):
            return f"Every {parts[0][2:]} minutes" + ("" if p["any_weekday"] else f", {when.lower()}")
        if len(hours) == 24 and len(mins) == 1:
            return ("Every hour" if p["any_weekday"] else f"{when}, every hour") + (f" at :{mins[0]:02d}" if mins[0] else "")
        if parts[1].startswith("*/") and len(mins) == 1:
            return f"Every {parts[1][2:]} hours" + (f" at :{mins[0]:02d}" if mins[0] else "") + \
                ("" if p["any_weekday"] else f", {when.lower()}")
        if len(hours) * len(mins) <= 4:
            times = ", ".join(f"{h:02d}:{m:02d}" for h in hours for m in mins)
            return f"{when} at {times}"
    if parts[3:] == ["*", "*"] and len(p["day"]) == 1 and len(hours) == 1 and len(mins) == 1:
        d = next(iter(p["day"]))
        suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"Every month on the {d}{suffix} at {hours[0]:02d}:{mins[0]:02d}"
    return f"Cron “{p['expr']}”"


# --- storing tasks ------------------------------------------------------------------------------
def install(conn) -> None:
    conn.executescript(SCHEMA)
    # Columns added after the table first shipped (a database made by an earlier build lacks them).
    db._add_columns(conn, ("run_now", "INTEGER NOT NULL DEFAULT 0"), ("notify", "TEXT"), ("archived_at", "TEXT"),
                   table="agent_jobs")
    db._add_columns(conn, ("images", "TEXT"), ("missed", "INTEGER"), ("told_at", "TEXT"), table="agent_job_runs")


def _row(conn, r) -> dict:
    j = dict(r)
    j["enabled"], j["deliver_chat"] = bool(j["enabled"]), bool(j["deliver_chat"])
    j["archived"] = j["archived_at"] is not None
    j["revision"] = revision(j)
    j["describe"] = describe(j["schedule"])
    j["notify_label"] = sounds.label(conn, j["notify"])
    j["running"] = j["id"] in _procs
    last = conn.execute("SELECT * FROM agent_job_runs WHERE job_id = ? AND status != 'running' ORDER BY id DESC LIMIT 1",
                        (j["id"],)).fetchone()
    j["last_run"] = _run(last) if last else None
    j["run_count"] = conn.execute("SELECT COUNT(*) FROM agent_job_runs WHERE job_id = ? AND status != 'skipped'",
                                  (j["id"],)).fetchone()[0]
    return j


def _run(r) -> dict:
    d = dict(r)
    d["tools"] = json.loads(d["tools"] or "[]")
    d["images"] = json.loads(d.get("images") or "[]")
    return d


def get(conn, job_id: int) -> dict:
    r = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
    if r is None:
        raise LookupError(f"There's no scheduled task #{job_id}.")
    return _row(conn, r)


def jobs(conn, archived: bool = False) -> list[dict]:
    """The active tasks, or (archived=True) the archive, most recently archived first."""
    rows = conn.execute("SELECT * FROM agent_jobs WHERE archived_at IS NOT NULL ORDER BY archived_at DESC, id DESC"
                        if archived else "SELECT * FROM agent_jobs WHERE archived_at IS NULL "
                        "ORDER BY enabled DESC, next_run IS NULL, next_run, id").fetchall()
    return [_row(conn, r) for r in rows]


def runs(conn, job_id: int | None = None, limit: int = 30) -> list[dict]:
    rows = conn.execute("SELECT r.*, j.name AS job_name FROM agent_job_runs r JOIN agent_jobs j ON j.id = r.job_id "
                        + ("WHERE r.job_id = ? " if job_id else "") + "ORDER BY r.id DESC LIMIT ?",
                        (job_id, limit) if job_id else (limit,)).fetchall()
    return [_run(r) for r in rows]


def _check(name: str, prompt: str) -> tuple[str, str]:
    name, prompt = (name or "").strip(), (prompt or "").strip()
    if not name:
        raise ValueError("Give the task a name.")
    if not prompt:
        raise ValueError("Say what the agent should do each time.")
    return name[:120], prompt[:8000]


def add(conn, name: str, prompt: str, schedule: str, created_by: str = "you", deliver_chat: bool = True,
        now: datetime | None = None, notify: str | None = None) -> int:
    """notify: the notification to play when it's done, by id or name (see sounds.resolve); empty = the default."""
    name, prompt = _check(name, prompt)
    schedule = normalize(schedule)
    notify = sounds.resolve(conn, notify)
    nxt = next_after(schedule, now or datetime.now())
    if nxt is None:
        raise ValueError("That time has already passed.")
    cur = conn.execute("INSERT INTO agent_jobs (name, prompt, schedule, deliver_chat, next_run, created_by, notify) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (name, prompt, schedule, int(deliver_chat), nxt.strftime(FMT), created_by, notify))
    return cur.lastrowid


def update(conn, job_id: int, now: datetime | None = None, **changes) -> dict:
    job = get(conn, job_id)
    name, prompt = _check(changes.get("name") or job["name"], changes.get("prompt") or job["prompt"])
    schedule = normalize(changes["schedule"]) if changes.get("schedule") else job["schedule"]
    enabled = job["enabled"] if changes.get("enabled") is None else bool(changes["enabled"])
    deliver = job["deliver_chat"] if changes.get("deliver_chat") is None else bool(changes["deliver_chat"])
    notify = job["notify"] if changes.get("notify") is None else sounds.resolve(conn, changes["notify"])   # "" = default
    nxt = next_after(schedule, now or datetime.now()) if enabled else None
    if enabled and nxt is None:
        raise ValueError("That time has already passed: pick a new one.")
    # Turning an archived task back on (e.g. a one-off given a new date) brings it out of the archive.
    archived_at = None if enabled else job["archived_at"]
    conn.execute("UPDATE agent_jobs SET name = ?, prompt = ?, schedule = ?, enabled = ?, deliver_chat = ?, next_run = ?, "
                 "notify = ?, archived_at = ? WHERE id = ?",
                 (name, prompt, schedule, int(enabled), int(deliver), nxt and nxt.strftime(FMT), notify, archived_at,
                  job_id))
    return get(conn, job_id)


def archive(conn, job_id: int) -> str:
    """Delete: the task stops and moves to the archive with its history (no-op if it's already there)."""
    name = get(conn, job_id)["name"]
    conn.execute("UPDATE agent_jobs SET enabled = 0, next_run = NULL, run_now = 0, archived_at = COALESCE(archived_at, ?) "
                 "WHERE id = ?", (_stamp(), job_id))
    return name


def restore(conn, job_id: int, now: datetime | None = None) -> dict:
    """Out of the archive: on again if it still has a run to come, otherwise paused (give it a new time to use it)."""
    job = get(conn, job_id)
    nxt = next_after(job["schedule"], now or datetime.now())
    conn.execute("UPDATE agent_jobs SET archived_at = NULL, enabled = ?, next_run = ? WHERE id = ?",
                 (int(nxt is not None), nxt and nxt.strftime(FMT), job_id))
    return get(conn, job_id)


def delete(conn, job_id: int) -> str:
    """Delete forever, with its run history (results posted in the chat stay there)."""
    name = get(conn, job_id)["name"]
    stop(job_id)
    conn.execute("DELETE FROM agent_jobs WHERE id = ?", (job_id,))
    return name


def archive_finished(conn, now: datetime | None = None, keep=()) -> None:
    """Tasks with nothing left to run (a one-off that's past, even paused) go to the archive. Paused repeating
    tasks stay: they can be resumed."""
    now = now or datetime.now()
    for r in conn.execute("SELECT id, schedule FROM agent_jobs WHERE archived_at IS NULL AND next_run IS NULL").fetchall():
        try:
            done = next_after(r["schedule"], now) is None
        except ValueError:
            done = False
        if done and r["id"] not in _procs and r["id"] not in keep:
            conn.execute("UPDATE agent_jobs SET enabled = 0, archived_at = ? WHERE id = ?", (_stamp(), r["id"]))


def claim_due(now: datetime | None = None) -> list[int]:
    """Tasks whose time has come: moves each one's next run on (so it fires once) and returns their ids
    (negative for one asked to run now, off its schedule). A run more than GRACE late is logged as skipped, with
    how many runs were missed, instead of running late."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    conn = db.connect()
    try:
        install(conn)
        due = []
        with conn:
            for r in conn.execute("SELECT * FROM agent_jobs WHERE enabled = 1 AND archived_at IS NULL AND next_run IS NOT NULL "
                                  "AND next_run <= ?",
                                  (now.strftime(FMT),)).fetchall():
                try:
                    nxt = next_after(r["schedule"], now)
                except ValueError:
                    nxt = None
                conn.execute("UPDATE agent_jobs SET next_run = ?, enabled = ? WHERE id = ?",
                             (nxt and nxt.strftime(FMT), int(nxt is not None), r["id"]))
                first = datetime.strptime(r["next_run"], FMT)
                if now - first > GRACE:
                    n, last = _missed(r["schedule"], first, now)
                    span = first.strftime("%Y-%m-%d %H:%M") + (f" to {last:%Y-%m-%d %H:%M}" if n > 1 else "")
                    conn.execute("INSERT INTO agent_job_runs (job_id, trigger, status, started_at, finished_at, output, missed) "
                                 "VALUES (?, 'schedule', 'skipped', ?, ?, ?, ?)",
                                 (r["id"], _stamp(), _stamp(),
                                  f"Missed {n} run{'s' if n > 1 else ''} ({span}): Nestwork wasn't running then. Not run "
                                  "late; your agent asks you about it on your next chat message.", n))
                elif r["id"] not in _procs:
                    due.append(r["id"])
            archive_finished(conn, now, keep=due)   # e.g. a one-off just skipped; one about to run waits for its end
            for r in conn.execute("SELECT id FROM agent_jobs WHERE run_now = 1").fetchall():
                conn.execute("UPDATE agent_jobs SET run_now = 0 WHERE id = ?", (r["id"],))
                if r["id"] not in _procs and r["id"] not in due:
                    due.append(-r["id"])     # negative: a manual run
        return due
    finally:
        conn.close()


def _missed(schedule: str, first: datetime, now: datetime) -> tuple[int, datetime]:
    """How many runs of a schedule fell from `first` (a missed run) up to `now`, and when the last one was."""
    n, last = 1, first
    try:
        while n < MAX_MISSED and (nxt := next_after(schedule, last)) and nxt <= now:
            n, last = n + 1, nxt
    except ValueError:
        pass
    return n, last


def missed_note(conn) -> str:
    """The missed runs the agent hasn't been told about yet, as a note for the start of the user's next message
    ("" when there are none). Each is told once: they're marked as told here."""
    # (skipped runs logged before counts were kept have missed = NULL: those are never announced)
    rows = conn.execute("SELECT j.name, SUM(r.missed) AS n FROM agent_job_runs r JOIN agent_jobs j ON j.id = r.job_id "
                        "WHERE r.status = 'skipped' AND r.missed IS NOT NULL AND r.told_at IS NULL "
                        "GROUP BY r.job_id ORDER BY MIN(r.id)").fetchall()
    if not rows:
        return ""
    conn.execute("UPDATE agent_job_runs SET told_at = ? WHERE status = 'skipped' AND missed IS NOT NULL "
                 "AND told_at IS NULL", (_stamp(),))
    lines = "\n".join(f"- {r['name']} ({r['n']} time(s))" for r in rows)
    return ("[Scheduled tasks past due: these didn't run while Nestwork was off, and weren't run late:\n" + lines +
            "\nDon't run them by yourself. If one still matters now, ask the user whether to run it "
            "(run_scheduled_task_now); otherwise just carry on. You're only told this once.]")


def recover() -> None:
    """At startup: runs left 'running' by a stopped server failed; enabled tasks get a next run."""
    conn = db.connect()
    with conn:
        install(conn)
        conn.execute("UPDATE agent_job_runs SET status = 'failed', finished_at = ?, "
                     "output = output || '\n\n(Nestwork stopped while this was running.)' WHERE status = 'running'", (_stamp(),))
        for r in conn.execute("SELECT * FROM agent_jobs WHERE enabled = 1 AND archived_at IS NULL AND next_run IS NULL").fetchall():
            nxt = next_after(r["schedule"], datetime.now())
            conn.execute("UPDATE agent_jobs SET next_run = ?, enabled = ? WHERE id = ?",
                         (nxt and nxt.strftime(FMT), int(nxt is not None), r["id"]))
        archive_finished(conn)
    conn.close()


# --- running ----------------------------------------------------------------------------------
_procs: dict[int, asyncio.subprocess.Process | None] = {}   # job id -> its Claude process while it runs
_tasks: set[asyncio.Task] = set()
_stopping: set[int] = set()


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def is_running(job_id: int) -> bool:
    return job_id in _procs


def start(job_id: int, trigger: str = "schedule") -> bool:
    """Run a task in the background (inside the server's event loop). False if it's already running."""
    if job_id in _procs:
        return False
    _procs[job_id] = None
    task = asyncio.get_running_loop().create_task(run(job_id, trigger))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def stop(job_id: int) -> bool:
    proc = _procs.get(job_id)
    if proc is None or proc.returncode is not None:
        return False
    _stopping.add(job_id)
    proc.terminate()
    return True


def job_prompt(job: dict, now_text: str) -> str:
    return (f"[{now_text}]\n"
            f"[Scheduled task “{job['name']}” from Agent's Work: the user did not type this. It runs automatically "
            f"({describe(job['schedule']).lower()}). Do the task below now, on your own, using your tools. Your final "
            "reply is the result the user will read (it's posted into their Nestwork chat), so write the finished thing "
            "itself, e.g. the brief, not a description of what you did. Don't ask questions back: nobody is waiting to "
            "answer. Standard first step: call recent_chats to see where your last conversations with the user left off; "
            "if the task needs to know what the user did today or yesterday, also check day_recap. Don't schedule new tasks unless the task says to. Pictures you make with take_selfie or draw_picture "
            "are attached to your result automatically.]\n\n" + job["prompt"])


async def run(job_id: int, trigger: str = "schedule") -> int | None:
    """One run of a task: a fresh headless Claude session. Returns the run id."""
    from . import agent, chat, connectors, persona
    _procs.setdefault(job_id, None)
    conn = db.connect()
    try:
        try:
            job = get(conn, job_id)
        except LookupError:
            return None
        with conn:
            run_id = conn.execute("INSERT INTO agent_job_runs (job_id, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
                                  (job_id, trigger, _stamp())).lastrowid
        settings, apps = agent.settings(conn), connectors.flags(conn)
    finally:
        conn.close()
    agent.after_change()

    texts: list[str] = []
    tools: list[str] = []
    images: list[str] = []
    result: dict = {}
    err, status = "", "failed"
    try:
        if not chat.claude_bin():
            raise RuntimeError("Couldn't find the `claude` command. Install Claude Code and sign in first.")
        persona.ensure_files()
        session = {"mode": "new", "session_id": str(uuid.uuid4())}
        cmd = chat.command(session, settings, persona.profile()["agent_name"], apps)
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(persona.agent_dir()), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024)
        _procs[job_id] = proc
        proc.stdin.write(job_prompt(job, chat.now_stamp()).encode())
        await proc.stdin.drain()
        proc.stdin.close()

        async def read():
            current = None
            async for raw in proc.stdout:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if e.get("type") == "stream_event":
                    ev = e["event"]
                    if ev.get("type") == "content_block_start" and ev["content_block"].get("type") == "text":
                        current = ""
                        texts.append("")
                    elif ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        if current is None:
                            texts.append("")
                            current = ""
                        texts[-1] += ev["delta"]["text"]
                    elif ev.get("type") == "content_block_stop":
                        current = None
                elif e.get("type") == "assistant":
                    for block in e["message"].get("content", []):
                        if block.get("type") == "tool_use":
                            tools.append(chat.tool_label(block["name"], block.get("input") or {}))
                elif e.get("type") == "user":
                    # A picture the agent made (take_selfie, draw_picture) comes back as a [nestwork-image:…] marker.
                    for block in (e.get("message") or {}).get("content") or []:
                        if block.get("type") != "tool_result":
                            continue
                        content = block.get("content")
                        text = content if isinstance(content, str) else " ".join(
                            c.get("text", "") for c in content or [] if isinstance(c, dict))
                        images.extend(re.findall(r"\[nestwork-image:(/api/generated/[^\]\s]+)\]", text))
                elif e.get("type") == "result":
                    result.update(e)
            await proc.wait()

        try:
            await asyncio.wait_for(read(), RUN_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            err = f"Stopped after {RUN_TIMEOUT // 60} minutes."
        err = err or (await proc.stderr.read()).decode(errors="replace").strip()
        if job_id in _stopping:
            status = "stopped"
        elif not result.get("is_error") and proc.returncode == 0:
            status = "ok"
    except Exception as e:
        err = str(e)
    finally:
        _stopping.discard(job_id)
        proc = _procs.pop(job_id, None)
        if proc is not None and proc.returncode is None:
            proc.kill()

    # The last text block is the answer; earlier ones are "Let me check…" narration between tool calls.
    blocks = [t.strip() for t in texts if t.strip()]
    output = blocks[-1] if blocks else ""
    if status != "ok":
        detail = (result.get("result") if result.get("is_error") else "") or (err.splitlines()[-1] if err else "")
        output = (output + "\n\n" if output else "") + ("Stopped." if status == "stopped" else
                                                         f"⚠️ {detail}" if detail else "⚠️ Something went wrong.")
    elif not output and not images:
        status, output = "failed", "⚠️ The agent finished without writing anything."
    output = output[:MAX_OUTPUT]

    conn = db.connect()
    try:
        with conn:
            message_id = None
            exists = conn.execute("SELECT deliver_chat, notify FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
            if exists and exists["deliver_chat"] and status in ("ok", "failed"):
                message_id = chat.save_message(conn, "", "assistant", output, {
                    "agent_job": job_id, "run_id": run_id, "title": job["name"], "failed": status != "ok",
                    "notify": exists["notify"], "images": images,
                    "tools": [{"name": t, "label": t} for t in dict.fromkeys(tools)], "cost": result.get("total_cost_usd"),
                    "duration_ms": result.get("duration_ms")})
            conn.execute("UPDATE agent_job_runs SET status = ?, finished_at = ?, output = ?, tools = ?, images = ?, "
                         "cost = ?, message_id = ? WHERE id = ?",
                         (status, _stamp(), output, json.dumps(list(dict.fromkeys(tools))), json.dumps(images),
                          result.get("total_cost_usd"), message_id, run_id))
            archive_finished(conn)          # a one-off that just ran is done
    finally:
        conn.close()
    agent.after_change()
    return run_id


def revision(j: dict) -> str:
    """A short fingerprint of what a task is (name, schedule, instructions). The agent passes back the one it read
    when it edits, so it can't rewrite a task it hasn't seen in full, or that changed since it looked."""
    return hashlib.sha1(json.dumps([j["name"], j["schedule"], j["prompt"]]).encode()).hexdigest()[:8]


def detail(j: dict) -> str:
    """Everything about one task, for the agent, with its instructions in full."""
    return (summary_line(j).split("\n")[0] + f" · made by {j['created_by']} · revision {j['revision']}\n"
            f"Instructions (in full):\n<<<\n{j['prompt']}\n>>>")


def changes(before: dict, after: dict) -> str:
    """What an edit changed, line by line, so the agent can see if it dropped something by mistake."""
    out = [f"{label}: “{before[k]}” → “{after[k]}”" for k, label in
           (("name", "Name"), ("schedule", "Schedule"), ("enabled", "On"), ("deliver_chat", "Posted to chat"),
            ("notify_label", "Sound")) if before[k] != after[k]]
    if before["prompt"] != after["prompt"]:
        old, new = before["prompt"].splitlines(), after["prompt"].splitlines()
        diff = [line for line in difflib.unified_diff(old, new, lineterm="", n=0)
                if line[:1] in "+-" and not line.startswith(("+++", "---"))]
        removed = sum(1 for d in diff if d.startswith("-"))
        out.append(f"Instructions: {removed} line{'' if removed == 1 else 's'} removed, "
                   f"{len(diff) - removed} added (- removed, + added):\n" + "\n".join(diff))
    return "\n".join(out) or "Nothing changed."


def summary_line(j: dict) -> str:
    """One line per task, for the agent."""
    state = ("running now" if j["running"] else f"next {j['next_run'].replace('T', ' ')}" if j["enabled"] and j["next_run"]
             else f"archived {j['archived_at'][:16].replace('T', ' ')}" if j["archived"]
             else "done (it was a one-off)" if j["schedule"].startswith("once") and j["run_count"] else "paused")
    last = j["last_run"]
    last_text = f"; last run {last['started_at'][:16].replace('T', ' ')} ({last['status']})" if last else ""
    does = re.sub(r"\s+", " ", j["prompt"])[:200]
    return (f"#{j['id']} “{j['name']}” · {describe(j['schedule'])} [{j['schedule']}] · {state}{last_text}"
            f"{'' if j['deliver_chat'] else ' · not posted to chat'} · sound: {j['notify_label']}\n   does: {does}")

