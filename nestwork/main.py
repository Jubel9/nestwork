"""Nestwork API (FastAPI) + static frontend."""

import asyncio
import json
import re
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import activities, agent, agentwork, attachments, awake, calendar_view, db, devices, export, focus, phone, plan, review, timer, forecast, imagegen, notes, reminders, repeats, reset, routes_agent, transfer, trash, undo
from .deps import get_conn
from .logic import FIRST, NEW_POMODOROS, PALETTE, ROLES, Project, default_color, new_stage_key, stages_from_labels, stages_with_labels
from .templates import TEMPLATES, summaries

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    # Bring existing projects in line with their rules (e.g. after an upgrade).
    conn = db.connect()
    with conn:
        review.backfill(conn)          # to-dos finished before completions were logged
        for r in conn.execute("SELECT id FROM blocks WHERE parent_id IS NULL").fetchall():
            settle(conn, r["id"])
        attachments.tidy(conn)             # files from before project folders move into them
    conn.close()
    export.write_snapshot()
    conn = db.connect()
    if agent.get_setting(conn, "keep_awake") == "1":
        awake.start()                  # keep the Mac awake on power so the phone can reach it
    conn.close()
    agentwork.recover()
    tasks = [asyncio.create_task(reminder_loop()), asyncio.create_task(maintenance_loop()),
             asyncio.create_task(agent_work_loop())]
    yield
    for task in tasks:
        task.cancel()
    awake.stop()


async def reminder_loop():
    """Every 30 seconds, send any reminders that are due into the chat."""
    while True:
        try:
            due = await run_in_threadpool(reminders.collect_due)
            if due:
                conn = db.connect()
                style = agent.get_setting(conn, "reminder_style")
                conn.close()
                for item in due:
                    await reminders.deliver(item, style)
        except Exception as e:   # never let one bad reminder stop the loop
            print(f"reminder loop: {e!r}", file=sys.stderr)
        await asyncio.sleep(30)


async def agent_work_loop():
    """Every 30 seconds, start the agent's scheduled tasks (Agent's Work) whose time has come."""
    while True:
        try:
            for job_id in await run_in_threadpool(agentwork.claim_due):
                agentwork.start(abs(job_id), "schedule" if job_id > 0 else "manual")
        except Exception as e:   # never let one bad task stop the loop
            print(f"agent work loop: {e!r}", file=sys.stderr)
        await asyncio.sleep(30)


def maintenance(now: datetime | None = None) -> bool:
    """Reopen repeating to-dos whose day has come; hourly-ish chores (daily backup, old trash) are cheap
    checks too. Returns True when something changed, so open pages refresh."""
    now = now or datetime.now()
    conn = db.connect()
    try:
        with conn:
            back = repeats.reopen_due(conn, now.date())
            purged = trash.purge_old(conn, now)
            attachments.clean_orphans(conn)
            attachments.tidy(conn)         # files follow renamed projects
        reset.auto_backup(conn, now)
        reset.prune_old(now)
    finally:
        conn.close()
    return bool(back or purged)


async def maintenance_loop():
    """Every minute: repeating to-dos coming back, the daily automatic backup, trash and backups older than 30 days."""
    while True:
        try:
            if await run_in_threadpool(maintenance):
                await run_in_threadpool(agent.after_change)
        except Exception as e:   # never let one failure stop the loop
            print(f"maintenance loop: {e!r}", file=sys.stderr)
        await asyncio.sleep(60)


app = FastAPI(title="Nestwork", lifespan=lifespan)


# --- who may use Nestwork (see devices.py) ------------------------------------------------
LOCAL_NAMES = {"127.0.0.1", "::1", "localhost"}
OPEN_PATHS = ("/pair", "/api/pair", "/manifest.webmanifest", "/static/vendor/", "/static/icons/")
LOCAL_ONLY = ("/api/phone", "/api/devices")


def _extra_local() -> set[str]:
    import os
    return {h.strip() for h in os.environ.get("NESTWORK_TRUSTED_LOCAL", "").split(",") if h.strip()}


def _hostname(request: Request) -> str:
    return (request.url.hostname or "").lower()


def is_local(request: Request) -> bool:
    """A request made on this computer to localhost (not one passed on by Tailscale from another device)."""
    names = LOCAL_NAMES | _extra_local()
    client = request.client.host if request.client else ""
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "tailscale-user-login", "forwarded"))
    return client in names and _hostname(request) in names and not forwarded


@app.middleware("http")
async def guard(request: Request, call_next):
    host = _hostname(request)
    if host not in LOCAL_NAMES | _extra_local() and not host.endswith(".ts.net"):
        return PlainTextResponse("Unknown host.", status_code=421)          # DNS-rebinding protection
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin:
        from urllib.parse import urlparse
        if urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    path = request.url.path
    if is_local(request):
        return await call_next(request)
    if path.startswith(LOCAL_ONLY):
        return JSONResponse({"detail": "Only from the Mac Nestwork runs on."}, status_code=403)
    if path.startswith(OPEN_PATHS):
        return await call_next(request)
    token = request.cookies.get(devices.COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    conn = db.connect()
    try:
        device = devices.check(conn, token)
        install = request.query_params.get("install")
        if device is None and install and path == "/":
            # A Home Screen app's first launch: trade its install code for a key of its own.
            try:
                new = devices.redeem_install(conn, install)
                conn.commit()
            except (LookupError, PermissionError):
                new = None
            if new:
                resp = RedirectResponse("/", status_code=303)
                _set_device_cookie(resp, new)
                return resp
    finally:
        conn.close()
    if device is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "This device isn't connected to Nestwork. Pair it in Settings → Phone on your Mac."},
                                status_code=401)
        return FileResponse(STATIC / "pair.html", headers={"Cache-Control": "no-cache"})
    request.state.device = device
    return await call_next(request)


def _set_device_cookie(resp, token: str) -> None:
    resp.set_cookie(devices.COOKIE, token, max_age=devices.COOKIE_MAX_AGE, httponly=True, secure=True, samesite="lax", path="/")


# --- request bodies ---------------------------------------------------------
class BlockIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    status: str | None = None
    notes: str = ""
    template: str | None = None
    stages: list[str] | None = None    # phase names for a blank project


class BlockPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    due_date: date | None = None
    due_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    clear_due_date: bool = False
    clear_due_time: bool = False
    remind: list[str] | None = None        # reminder choices; [] = no reminders
    default_remind: bool = False           # go back to the default reminders
    pomodoros: int | None = Field(default=None, ge=0, le=24)   # daily focus sessions; 0 = none
    estimate_min: int | None = Field(default=None, ge=0, le=100000)   # minutes; 0 = no estimate
    repeat: dict | None = None             # {"freq": "daily"|"weekly"|"monthly"|"yearly", "interval": n, ...}
    clear_repeat: bool = False             # stop repeating
    priority: int | None = Field(default=None, ge=0, le=3)    # 0 none, 1 low, 2 medium, 3 high
    tags: list[str] | None = None          # replaces the tags; [] removes them
    archived: bool | None = None           # projects only
    track_total: int | None = Field(default=None, ge=0, le=1_000_000)   # a counter on a to-do; 0 removes it
    track_done: int | None = Field(default=None, ge=0, le=1_000_000)
    track_add: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)   # +1 / -1 from the card
    track_unit: str | None = Field(default=None, max_length=24)


class MoveIn(BaseModel):
    status: str
    before_id: int | None = None
    by: str = "you"                  # "agent" when the agent moved it (for the activity log)


class RuleIn(BaseModel):
    depends_on_id: int
    required_stage: str | None = None   # defaults to the last phase
    target_stage: str | None = None     # defaults to the second phase


class RulesIn(BaseModel):
    rules: list[RuleIn]


class StageIn(BaseModel):
    key: str | None = None              # omit for a new phase
    label: str = Field(min_length=1, max_length=40)
    color: str | None = None
    role: str | None = None             # "active", "review" or none


class StagesIn(BaseModel):
    stages: list[StageIn]


# --- helpers ----------------------------------------------------------------
def load(conn: sqlite3.Connection, block_id: int) -> Project:
    project = Project.for_block(conn, block_id)
    if project is None:
        raise HTTPException(404, "Block not found")
    return project


def check_status(p: Project, status: str) -> None:
    if status not in p.idx:
        raise HTTPException(422, f"Unknown phase '{status}'")


def next_position(conn, parent_id, status) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM blocks WHERE parent_id IS ? AND status = ?",
        (parent_id, status),
    ).fetchone()
    return row["p"]


def insert_block(conn, parent_id, title, status=FIRST, **extra) -> int:
    cols = {"parent_id": parent_id, "title": title.strip(), "status": status,
            "position": next_position(conn, parent_id, status), "pomodoros": NEW_POMODOROS, **extra}
    cur = conn.execute(
        f"INSERT INTO blocks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(cols.values()),
    )
    return cur.lastrowid


def clean_tags(tags: list[str]) -> list[str]:
    """Short labels without a leading #, at most 10, no duplicates (ignoring case)."""
    out, seen = [], set()
    for t in tags:
        t = " ".join(str(t).strip().lstrip("#").split())[:24]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:10]


def touch(conn, block_id) -> None:
    conn.execute("UPDATE blocks SET updated_at = datetime('now') WHERE id = ?", (block_id,))


def settle(conn, any_id: int, before: Project | None = None, skip: int | None = None) -> list[str]:
    """Run automatic moves for the whole project, save them, and describe what happened."""
    after = load(conn, any_id)
    notices = []
    for ch in after.settle():
        x, new = ch["id"], after.stages[ch["new"]]
        conn.execute("UPDATE blocks SET status = ?, position = ?, updated_at = datetime('now') WHERE id = ?",
                     (new, next_position(conn, after.blocks[x]["parent_id"], new), x))
        icon = "⚡" if ch["new"] > ch["old"] else "↩️"
        notices.append(f"{icon} “{after.title(x)}” moved to “{after.label(new)}”: {ch['reason']}.")
    review.track(conn, after)          # completions, for the weekly review
    for x in repeats.schedule(conn, after):
        nxt = repeats.load(after.blocks[x]["repeat_next"])
        when = f", due {nxt['due']}" if nxt.get("due") else ""
        notices.append(f"🔁 “{after.title(x)}” repeats: it comes back on {nxt['reopen']}{when}.")
    if before is not None:
        old_warn = before.warnings()
        for x, reasons in after.warnings().items():
            if x not in old_warn and x != skip:
                notices.append(f"⚠️ “{after.title(x)}” is further along than its rules allow: waiting for {reasons[0]}.")
        changed = {c for c in after.blocks if c in before.blocks and after.stage(c) != before.stage(c)}
        for x in after.blocks:
            if x in before.blocks and x not in changed and before.is_locked(x) and not after.is_locked(x) \
                    and not any(a in before.blocks and before.is_locked(a) and not after.is_locked(a)
                                for a in after.ancestors(x)):
                notices.append(f"🔓 “{after.title(x)}” can start now.")
    touch(conn, after.root_id)
    return notices


def stage_error(p: Project, block_id: int, target: str) -> str | None:
    """Why a manual move of the block to `target` isn't allowed, or None if it is."""
    t, cur = p.idx[target], p.stage(block_id)
    allowed = p.allowed_max(block_id)
    name = p.title(block_id)
    if t > cur and t > allowed:
        first_blocked = next(s for s in range(allowed + 1, t + 1) if p.unmet(block_id, s))
        reasons = " and ".join(u["text"] for u in p.unmet(block_id, first_blocked))
        where = "start" if first_blocked == 1 else f"go to “{p.label(first_blocked)}”"
        return f"“{name}” can't {where} until {reasons}."
    floor = p.auto_min(block_id, allowed)
    if t < floor:
        return (f"“{name}” is kept in “{p.label(floor)}” automatically because {p.push_reason(block_id, floor)}. "
                f"To move it back, reopen what it relies on.")
    return None


def insert_rules(conn, block_id: int, rules: list[tuple[int, str, str]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO rules (block_id, depends_on_id, required_stage, target_stage) VALUES (?, ?, ?, ?)",
        [(block_id, dep, req, tgt) for dep, req, tgt in rules],
    )


def apply_template(conn, parent_id: int, items: list[dict]) -> None:
    ids = {}
    for item in items:
        ids[item["title"]] = insert_block(conn, parent_id, item["title"])
        apply_template(conn, ids[item["title"]], item.get("items", []))
    for item in items:
        rules = [(ids[dep], "done", "ready") for dep in item.get("after", [])]
        rules += [(ids[dep], req, tgt) for tgt, dep, req in item.get("rules", [])]
        insert_rules(conn, ids[item["title"]], rules)


# --- routes -----------------------------------------------------------------
@app.get("/api/templates")
def list_templates():
    return summaries()


class SettingsIn(BaseModel):
    reminder_style: str | None = None
    image_provider: str | None = None
    image_model: str | None = None
    selfie_provider: str | None = None
    selfie_model: str | None = None
    selfie_prompt_model: str | None = None
    agent_access: str | None = None
    allow_delete: bool | None = None
    agent_tools: str | None = None
    agent_model: str | None = None
    agent_effort: str | None = None


@app.get("/api/settings")
def get_settings(conn=Depends(get_conn)):
    root = Path(__file__).resolve().parent.parent
    return {**agent.settings(conn),
            # Shown in Settings so connecting Claude is copy-paste.
            "mcp_command": f"claude mcp add nestwork --env PYTHONPATH={root} -- {sys.executable} -m nestwork.mcp_server",
            "project_dir": str(root)}


@app.put("/api/settings")
def put_settings(body: SettingsIn, conn=Depends(get_conn)):
    if body.agent_access is not None:
        if body.agent_access not in agent.ACCESS_MODES:
            raise HTTPException(422, "Agent access must be read_only, ask or full.")
        agent.set_setting(conn, "agent_access", body.agent_access)
    if body.allow_delete is not None:
        agent.set_setting(conn, "allow_delete", "1" if body.allow_delete else "0")
    if body.agent_tools is not None:
        if body.agent_tools not in agent.TOOL_LEVELS:
            raise HTTPException(422, "Agent tools must be contained, web or broad.")
        agent.set_setting(conn, "agent_tools", body.agent_tools)
    if body.agent_model is not None:
        agent.set_setting(conn, "agent_model", body.agent_model.strip()[:60])
    if body.agent_effort is not None:
        if body.agent_effort not in agent.EFFORT_LEVELS:
            raise HTTPException(422, "Effort must be low, medium, high, xhigh or max (or empty for the default).")
        agent.set_setting(conn, "agent_effort", body.agent_effort)
    for key in ("image_provider", "selfie_provider"):
        value = getattr(body, key)
        if value is not None:
            if value not in imagegen.PROVIDERS:
                raise HTTPException(422, "The provider must be openrouter or wavespeed.")
            agent.set_setting(conn, key, value)
    for key in ("image_model", "selfie_model", "selfie_prompt_model"):
        value = getattr(body, key)
        if value is not None:
            agent.set_setting(conn, key, value.strip()[:120])
    if body.reminder_style is not None:
        if body.reminder_style not in ("quick", "agent"):
            raise HTTPException(422, "Reminder style must be quick or agent.")
        agent.set_setting(conn, "reminder_style", body.reminder_style)
    return agent.settings(conn)


# --- reminders ------------------------------------------------------------------------
class ReminderIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    remind_at: datetime
    block_id: int | None = None


@app.get("/api/reminders")
def get_reminders(days: int = 14, conn=Depends(get_conn)):
    """What will be reminded about soon, plus the reminder choices for to-dos."""
    return {"upcoming": reminders.upcoming(conn, days), "choices": reminders.CHOICES, "default": reminders.DEFAULT}


@app.post("/api/reminders", status_code=201)
def post_reminder(body: ReminderIn, conn=Depends(get_conn)):
    if body.block_id is not None:
        load(conn, body.block_id)
    rid = reminders.add(conn, body.text, body.remind_at.isoformat(), body.block_id)
    return {"id": rid}


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: int, conn=Depends(get_conn)):
    conn.execute("DELETE FROM reminders WHERE id = ? AND status = 'pending'", (reminder_id,))
    return {"ok": True}


class ActivityPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    start: str | None = None          # "YYYY-MM-DD HH:MM"
    end: str | None = None            # "" = still going
    note: str | None = Field(default=None, max_length=2000)


@app.patch("/api/activities/{activity_id}")
def patch_activity(activity_id: int, body: ActivityPatch, conn=Depends(get_conn)):
    """Correct an entry of the activity log (logged by the agent from the chat)."""
    try:
        return {"activity": activities.change(conn, activity_id, body.title, body.start, body.end, None, body.note)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, conn=Depends(get_conn)):
    try:
        return {"activity": activities.delete(conn, activity_id)}
    except LookupError as e:
        raise HTTPException(404, str(e))


# The agent's own routes (memory files, chat, appearance, wardrobe, pictures, connected apps).
app.include_router(routes_agent.router)


@app.get("/api/version")
def get_version(conn=Depends(get_conn)):
    """A counter that changes whenever anything changes (also from AI agents), for auto-refresh."""
    return agent.version(conn)


@app.get("/api/proposals")
def get_proposals(status: str | None = "pending", conn=Depends(get_conn)):
    return agent.list_proposals(conn, None if status == "all" else status)


@app.post("/api/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int):
    try:
        return agent.decide(proposal_id, True)
    except agent.AgentError as e:
        raise HTTPException(409, str(e))


@app.post("/api/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int):
    try:
        return agent.decide(proposal_id, False)
    except agent.AgentError as e:
        raise HTTPException(409, str(e))


# --- danger zone: start over (with a backup first) ---------------------------------------
class ResetIn(BaseModel):
    parts: list[str]
    confirm: str = ""


class RestoreIn(BaseModel):
    name: str
    confirm: str = ""


@app.get("/api/reset")
def reset_summary(conn=Depends(get_conn)):
    return reset.summary(conn)


@app.post("/api/reset")
def reset_erase(body: ResetIn, conn=Depends(get_conn)):
    try:
        return reset.erase(conn, body.parts, body.confirm)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/reset/restore")
def reset_restore(body: RestoreIn, conn=Depends(get_conn)):
    try:
        return reset.restore(conn, body.name, body.confirm)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- notes & activity on a block -------------------------------------------------------------
class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


@app.get("/api/blocks/{block_id}/notes")
def get_notes(block_id: int, conn=Depends(get_conn)):
    load(conn, block_id)
    return notes.timeline(conn, block_id)


@app.post("/api/blocks/{block_id}/notes", status_code=201)
def post_note(block_id: int, body: NoteIn, conn=Depends(get_conn)):
    load(conn, block_id)
    try:
        return notes.add(conn, block_id, body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.patch("/api/notes/{note_id}")
def patch_note(note_id: int, body: NoteIn, conn=Depends(get_conn)):
    try:
        return notes.edit(conn, note_id, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, conn=Depends(get_conn)):
    notes.delete(conn, note_id)
    return {"ok": True}


# --- focus sessions (the Pomodoro timer's log) -------------------------------------------
class FocusStartIn(BaseModel):
    block_id: int | None = None
    project_id: int | None = None
    title: str = Field(default="", max_length=200)
    planned_min: float = Field(default=0, ge=0, le=600)   # 0 for a stopwatch
    mode: str = "pomodoro"                                # pomodoro | stopwatch


class FocusFinishIn(BaseModel):
    focus_min: float = Field(ge=0, le=16 * 60)
    completed: bool
    # Optionally say what it was (a stopwatch started without a name): a to-do, a project or a label.
    block_id: int | None = None
    project_id: int | None = None
    title: str | None = Field(default=None, max_length=200)


class FocusRateIn(BaseModel):
    rating: int | None = Field(default=None, ge=0, le=5)    # 0 clears it
    note: str | None = Field(default=None, max_length=1000)


class FocusGoalIn(BaseModel):
    minutes: int = Field(ge=5, le=960)


def _focus_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(404 if "exist" in str(e) else 422, str(e))


@app.post("/api/focus/start", status_code=201)
def focus_start(body: FocusStartIn, conn=Depends(get_conn)):
    return _focus_call(focus.start, conn, block_id=body.block_id, project_id=body.project_id,
                       title=body.title, planned_min=body.planned_min, mode=body.mode)


@app.post("/api/focus/{sid}/finish")
def focus_finish(sid: int, body: FocusFinishIn, conn=Depends(get_conn)):
    if body.title is not None or body.block_id is not None or body.project_id is not None:
        _focus_call(focus.retag, conn, sid, block_id=body.block_id, project_id=body.project_id, title=body.title or "")
    return {"session": _focus_call(focus.finish, conn, sid, body.focus_min, body.completed)}


@app.get("/api/focus/labels")
def focus_labels(conn=Depends(get_conn)):
    """Your own session names, most recent first, to pick again."""
    return {"labels": focus.labels(conn)}


@app.patch("/api/focus/{sid}")
def focus_rate(sid: int, body: FocusRateIn, conn=Depends(get_conn)):
    return _focus_call(focus.rate, conn, sid, body.rating, body.note)


@app.delete("/api/focus/{sid}")
def focus_delete(sid: int, conn=Depends(get_conn)):
    focus.delete(conn, sid)
    return {"ok": True}


@app.get("/api/focus/stats")
def focus_stats(days: int = 7, conn=Depends(get_conn)):
    return focus.stats(conn, days)


@app.put("/api/focus/goal")
def focus_goal(body: FocusGoalIn, conn=Depends(get_conn)):
    return {"goal_min": focus.set_goal(conn, body.minutes)}


@app.get("/api/today")
def today(conn=Depends(get_conn)):
    """Every unfinished task across all projects, grouped by priority, plus a short suggested focus."""
    return export.today_items(conn)


@app.get("/api/snapshot.md", response_class=PlainTextResponse)
def snapshot(conn=Depends(get_conn)):
    """The same Markdown that is written to data/nestwork.md."""
    return PlainTextResponse(export.markdown(conn), media_type="text/markdown; charset=utf-8")


@app.get("/api/projects")
def list_projects(archived: bool = False, conn=Depends(get_conn)):
    """Every project that isn't archived (archived=true: only the archived ones)."""
    roots = conn.execute(
        "SELECT id FROM blocks WHERE parent_id IS NULL AND archived_at IS " + ("NOT NULL" if archived else "NULL")
        + " ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    out = []
    for r in roots:
        p = Project(conn, r["id"])
        card = p.card(r["id"])
        card["updated_at"] = p.blocks[r["id"]]["updated_at"]
        card["tree"] = p.tree(r["id"])
        card["stages"] = p.stages_out()
        card["forecast"] = forecast.forecast(conn, p, r["id"])
        card["ready_now"] = sum(
            1 for i in p.blocks
            if i != p.root_id and not p.children[i] and not p.is_done(i) and not p.is_locked(i)
        )
        out.append(card)
    return out


@app.post("/api/blocks", status_code=201)
def create_block(body: BlockIn, conn=Depends(get_conn)):
    notices = []
    if body.parent_id is None:
        tpl = TEMPLATES.get(body.template or "blank")
        if tpl is None:
            raise HTTPException(422, "Unknown template")
        if tpl["items"] or tpl["labels"]:
            stages = stages_with_labels(tpl["labels"])
        else:
            # A blank project uses the phases the user wrote (or a minimal To do / Doing / Done).
            names = [n.strip()[:40] for n in (body.stages or []) if n.strip()]
            if body.stages is not None and len(names) < 2:
                raise HTTPException(422, "Add at least two phases: where work starts and where it's done.")
            stages = stages_from_labels(names or ["To do", "Doing", "Done"])
        new_id = insert_block(conn, None, body.title, stages[0]["key"],
                              stage_config=json.dumps(stages), notes=body.notes)
        apply_template(conn, new_id, tpl["items"])
    else:
        before = load(conn, body.parent_id)
        status = body.status or default_child_stage(before, body.parent_id)
        check_status(before, status)
        new_id = insert_block(conn, body.parent_id, body.title, before.stages[0], notes=body.notes)
        if status != before.stages[0]:
            if err := stage_error(load(conn, new_id), new_id, status):
                if body.status:
                    raise HTTPException(409, err)
                status = before.stages[0]          # the parent's phase was only a default: start at the beginning
        if status != before.stages[0]:
            conn.execute("UPDATE blocks SET status = ?, position = ? WHERE id = ?",
                         (status, next_position(conn, body.parent_id, status), new_id))
        notices = settle(conn, new_id, before, skip=new_id)
    p = load(conn, new_id)
    return {"block": p.card(new_id), "notices": notices}


def default_child_stage(p: Project, parent_id: int) -> str:
    """Where a new to-do inside a block starts: in that block's phase, so adding a to-do list doesn't push
    the block back to the start (it follows its slowest to-do). Top-level to-dos, and blocks waiting for
    review or done, start at the beginning."""
    i = p.stage(parent_id)
    return p.stages[i] if parent_id != p.root_id and i < p.review_i else p.stages[0]


@app.get("/api/blocks/{block_id}")
def get_block(block_id: int, conn=Depends(get_conn)):
    p = load(conn, block_id)
    crumbs = [{"id": i, "title": p.blocks[i]["title"]} for i in reversed(p.ancestors(block_id))]
    below = p.descendants(block_id)
    n_notes = notes.counts(conn, [block_id, *below])
    spent = forecast.spent_minutes(conn, [block_id, *below])
    n_files = attachments.counts(conn, [block_id, *below])
    def card(i):
        return {**p.card(i), "notes_count": n_notes.get(i, 0), "spent_min": round(spent.get(i, 0)),
                "attachments_count": n_files.get(i, 0)}
    return {
        "block": card(block_id),
        "is_root": block_id == p.root_id,
        "root_id": p.root_id,
        "breadcrumb": crumbs,
        "stages": p.stages_out(),
        "children": [card(c) for c in p.children[block_id]],
        # Every block nested anywhere below, for the nested board (bands inside bands).
        "cards": {d: card(d) for d in below},
        "tree": p.tree(block_id),
        "forecast": forecast.forecast(conn, p, block_id) if p.children[block_id] else None,
        "today": date.today().isoformat(),
    }


@app.patch("/api/blocks/{block_id}")
def update_block(block_id: int, body: BlockPatch, conn=Depends(get_conn)):
    load(conn, block_id)
    fields = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.notes is not None:
        fields["notes"] = body.notes
    if body.due_date is not None:
        fields["due_date"] = body.due_date.isoformat()
    if body.due_time is not None:
        fields["due_time"] = body.due_time
    if body.clear_due_date:
        fields["due_date"] = None
        fields["due_time"] = None
    if body.clear_due_time:
        fields["due_time"] = None
    if body.remind is not None:
        fields["remind"] = json.dumps([c for c in body.remind if c in reminders.CHOICES])
    if body.default_remind:
        fields["remind"] = None
    if body.pomodoros is not None:
        fields["pomodoros"] = body.pomodoros
    if body.estimate_min is not None:
        fields["estimate_min"] = body.estimate_min or None
    if body.priority is not None:
        fields["priority"] = body.priority
    if body.tags is not None:
        fields["tags"] = json.dumps(clean_tags(body.tags)) if clean_tags(body.tags) else None
    if any(v is not None for v in (body.track_total, body.track_done, body.track_add, body.track_unit)):
        b = load(conn, block_id).blocks[block_id]
        total = b.get("track_total") if body.track_total is None else body.track_total
        if not total:
            if body.track_total is None:
                raise HTTPException(422, "Give the counter a total first (e.g. 200 videos).")
            fields.update(track_total=None, track_done=None, track_unit=None)
        else:
            done = b.get("track_done") or 0 if body.track_done is None else body.track_done
            done = max(0, min(done + (body.track_add or 0), total))
            fields.update(track_total=total, track_done=done)
            if body.track_unit is not None:
                fields["track_unit"] = " ".join(body.track_unit.split())
    if body.archived is not None:
        if load(conn, block_id).root_id != block_id:
            raise HTTPException(422, "Only a whole project can be archived.")
        fields["archived_at"] = datetime.now().isoformat(timespec="seconds") if body.archived else None
    if body.repeat is not None or body.clear_repeat:
        p = load(conn, block_id)
        if block_id == p.root_id:
            raise HTTPException(422, "A project itself can't repeat; make a to-do inside it repeat.")
        due = fields.get("due_date", p.blocks[block_id]["due_date"])
        try:
            rule = None if body.clear_repeat else repeats.normalize(body.repeat, date.fromisoformat(due) if due else None)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
        fields["repeat"] = json.dumps(rule) if rule else None
        fields["repeat_next"] = None      # planned again below from the new rule
    elif fields.get("due_date"):
        # A monthly repeat follows its due date's day of the month.
        rule = repeats.load(load(conn, block_id).blocks[block_id].get("repeat"))
        if rule and rule["freq"] == "monthly":
            fields["repeat"] = json.dumps({**rule, "monthday": date.fromisoformat(fields["due_date"]).day})
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE blocks SET {sets}, updated_at = datetime('now') WHERE id = ?",
                     (*fields.values(), block_id))
    notices = []
    if "repeat_next" in fields:
        notices = settle(conn, block_id, load(conn, block_id))
    return {"block": load(conn, block_id).card(block_id), "notices": notices}


@app.post("/api/blocks/{block_id}/move")
def move_block(block_id: int, body: MoveIn, conn=Depends(get_conn)):
    before = load(conn, block_id)
    check_status(before, body.status)
    if block_id == before.root_id:
        raise HTTPException(422, "A project itself can't be moved between phases.")
    b = before.blocks[block_id]
    if (err := stage_error(before, block_id, body.status)):
        raise HTTPException(409, err)

    # Re-order the target column with this block inserted before `before_id`.
    column = [c for c in before.children[b["parent_id"]]
              if before.blocks[c]["status"] == body.status and c != block_id]
    idx = column.index(body.before_id) if body.before_id in column else len(column)
    column.insert(idx, block_id)
    for pos, cid in enumerate(column):
        conn.execute("UPDATE blocks SET position = ? WHERE id = ?", (pos, cid))
    conn.execute("UPDATE blocks SET status = ?, updated_at = datetime('now') WHERE id = ?",
                 (body.status, block_id))
    if b["status"] != body.status:
        notes.log_event(conn, block_id, f"Moved from “{before.label(b['status'])}” to “{before.label(body.status)}”",
                        author="agent" if body.by == "agent" else "you")
    notices = settle(conn, block_id, before)
    return {"block": load(conn, block_id).card(block_id), "notices": notices}


class ReparentIn(BaseModel):
    parent_id: int
    status: str | None = None          # also put it in this phase (e.g. dropped on the board), all or nothing
    before_id: int | None = None


@app.get("/api/blocks/{block_id}/move-targets")
def move_targets(block_id: int, conn=Depends(get_conn)):
    """Blocks this one can be moved into: anything in the project except itself and what's inside it."""
    p = load(conn, block_id)
    if block_id == p.root_id:
        raise HTTPException(422, "A project can't be moved inside something else.")
    skip = set(p.descendants(block_id)) | {block_id}
    order = [p.root_id] + [i for i in _tree_order(p, p.root_id)]
    return [{"id": i, "title": p.title(i), "path": " › ".join(p.path(i)[:-1]), "depth": len(p.ancestors(i)),
             "is_root": i == p.root_id, "current": i == p.blocks[block_id]["parent_id"], "done": p.is_done(i)}
            for i in order if i not in skip]


def _tree_order(p: Project, block_id: int) -> list[int]:
    out = []
    for c in p.children[block_id]:
        out.append(c)
        out += _tree_order(p, c)
    return out


@app.post("/api/blocks/{block_id}/move-into")
def move_into(block_id: int, body: ReparentIn, conn=Depends(get_conn)):
    """Move a block (with everything inside it) into another block of the same project."""
    before = load(conn, block_id)
    if block_id == before.root_id:
        raise HTTPException(422, "A project can't be moved inside something else.")
    if body.parent_id not in before.blocks:
        raise HTTPException(422, "You can only move it within the same project.")
    if body.parent_id == block_id or body.parent_id in before.descendants(block_id):
        raise HTTPException(409, "A block can't be moved inside itself or inside something it contains.")
    if body.parent_id == before.blocks[block_id]["parent_id"]:
        if body.status:
            return move_block(block_id, MoveIn(status=body.status, before_id=body.before_id), conn=conn)
        return {"block": before.card(block_id), "notices": []}
    status = before.blocks[block_id]["status"]
    conn.execute("UPDATE blocks SET parent_id = ?, position = ?, updated_at = datetime('now') WHERE id = ?",
                 (body.parent_id, next_position(conn, body.parent_id, status), block_id))
    after = load(conn, block_id)
    if after.has_deadlock():
        # Not committed: get_conn only commits when the request succeeds.
        raise HTTPException(409, "Moving it there would make its rules impossible to meet (a loop).")
    notices = [f"Moved “{after.title(block_id)}” into “{after.title(body.parent_id)}”."]
    notices += settle(conn, block_id, before)
    if body.status:   # refused by its new rules → the whole request rolls back
        notices += move_block(block_id, MoveIn(status=body.status, before_id=body.before_id), conn=conn)["notices"]
    return {"block": load(conn, block_id).card(block_id), "notices": notices}


@app.get("/api/blocks/{block_id}/rule-options")
def rule_options(block_id: int, new_child: bool = False, conn=Depends(get_conn)):
    """Blocks this one can have rules about (anything in the project except its own family).

    With new_child, the options are for a to-do about to be added inside `block_id`.
    """
    p = load(conn, block_id)
    family = (set(p.ancestors(block_id)) | {block_id}) if new_child else p.family(block_id)
    parent = block_id if new_child else p.blocks[block_id]["parent_id"]
    out = [{
        "id": i,
        "title": p.title(i),
        "path": " › ".join(p.path(i)[1:-1]),
        "status": p.stages[p.stage(i)],
        "done": p.is_done(i),
        "sibling": p.blocks[i]["parent_id"] == parent,
    } for i in p.blocks if i != p.root_id and i not in family]
    # Siblings first (the usual case), then everything else grouped by location.
    out.sort(key=lambda o: (not o["sibling"], o["path"], p.blocks[o["id"]]["position"]))
    return out


@app.put("/api/blocks/{block_id}/rules")
def set_rules(block_id: int, body: RulesIn, conn=Depends(get_conn)):
    before = load(conn, block_id)
    if block_id == before.root_id:
        raise HTTPException(422, "A project itself doesn't have rules; add them to the blocks inside it.")
    family = before.family(block_id)
    wanted: dict[tuple[int, str], str] = {}
    for r in body.rules:
        r.target_stage = r.target_stage or before.stages[1]
        r.required_stage = r.required_stage or before.stages[-1]
        if r.target_stage not in before.stages[1:] or r.required_stage not in before.stages[1:]:
            raise HTTPException(422, "Rules need a phase after the first one.")
        if r.depends_on_id not in before.blocks or r.depends_on_id == before.root_id:
            raise HTTPException(422, "Rules can only point to blocks in the same project.")
        if r.depends_on_id in family:
            raise HTTPException(409, "A block can't have rules about itself, its parents or the items inside it.")
        wanted[(r.depends_on_id, r.target_stage)] = r.required_stage
    rules = [{"dep": d, "required": req, "target": tgt} for (d, tgt), req in wanted.items()]
    if before.has_deadlock({block_id: rules}):
        culprit = next((r for r in rules if before.has_deadlock({block_id: before.rules[block_id] + [r]})), None)
        detail = (f" (“{before.title(culprit['dep'])}” reaching “{before.label(culprit['required'])}” "
                  f"already depends on “{before.title(block_id)}”)") if culprit else ""
        raise HTTPException(409, f"Those rules create a loop where nothing could ever finish{detail}.")
    conn.execute("DELETE FROM rules WHERE block_id = ?", (block_id,))
    insert_rules(conn, block_id, [(r["dep"], r["required"], r["target"]) for r in rules])
    notices = settle(conn, block_id, before)
    return {"block": load(conn, block_id).card(block_id), "notices": notices}


@app.post("/api/blocks/{block_id}/duplicate", status_code=201)
def duplicate_block(block_id: int, conn=Depends(get_conn)):
    p = load(conn, block_id)
    if block_id == p.root_id:
        raise HTTPException(422, "Use a template to start a new project.")
    mapping: dict[int, int] = {}

    def copy(src: int, parent: int, title: str | None = None):
        b = p.blocks[src]
        mapping[src] = insert_block(conn, parent, title or b["title"], p.stages[0],
                                    notes=b["notes"], due_date=b["due_date"], due_time=b.get("due_time"),
                                    remind=b.get("remind"), pomodoros=b.get("pomodoros"),
                                    estimate_min=b.get("estimate_min"), repeat=b.get("repeat"),
                                    priority=b.get("priority") or 0, tags=b.get("tags"),
                                    track_total=b.get("track_total"), track_unit=b.get("track_unit"),
                                    track_done=0 if b.get("track_total") else None)
        for c in p.children[src]:
            copy(c, mapping[src])

    copy(block_id, p.blocks[block_id]["parent_id"], f"{p.blocks[block_id]['title']} (copy)")
    for src, new in mapping.items():
        # Rules inside the copied block point to the copies; rules pointing outside stay as they were.
        insert_rules(conn, new, [(mapping.get(r["dep"], r["dep"]), r["required"], r["target"]) for r in p.rules[src]])
    new_id = mapping[block_id]
    notices = settle(conn, new_id, p)
    return {"block": load(conn, new_id).card(new_id), "notices": notices}


@app.delete("/api/blocks/{block_id}")
def delete_block(block_id: int, by: str = "you", conn=Depends(get_conn)):
    """Delete a block and everything inside it. A copy goes to the trash first (restorable for 30 days)."""
    before = load(conn, block_id)
    parent = before.blocks[block_id]["parent_id"]
    trash_id = trash.stash(conn, before, block_id, by="agent" if by == "agent" else "you")
    conn.execute("DELETE FROM blocks WHERE id = ?", (block_id,))
    notices = []
    if parent is not None:
        notices = settle(conn, parent, before)
    return {"notices": notices, "trash_id": trash_id}


# --- export & import ---------------------------------------------------------------
def _download(data: dict, name: str) -> JSONResponse:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")[:60] or "nestwork"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{safe}.nestwork.json"'})


@app.get("/api/export.json")
def export_everything(conn=Depends(get_conn)):
    return _download(transfer.export_all(conn), f"nestwork-{date.today().isoformat()}")


@app.get("/api/blocks/{block_id}/export.json")
def export_block(block_id: int, conn=Depends(get_conn)):
    p = load(conn, block_id)
    return _download(transfer.export_block(conn, block_id), p.title(block_id))


class ImportIn(BaseModel):
    data: dict


class OutlineIn(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    parent_id: int | None = None
    title: str | None = Field(default=None, max_length=200)


@app.post("/api/import", status_code=201)
def import_json(body: ImportIn, conn=Depends(get_conn)):
    try:
        ids = transfer.import_data(conn, body.data)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e) or "That file couldn't be read as a Nestwork export.")
    for i in ids:
        settle(conn, i)
    names = [load(conn, i).title(i) for i in ids]
    return {"projects": ids, "notices": [f"Imported {len(ids)} project(s): " + ", ".join(f"“{n}”" for n in names) + "."]}


@app.post("/api/import/outline", status_code=201)
def import_outline(body: OutlineIn, conn=Depends(get_conn)):
    if body.parent_id is not None:
        load(conn, body.parent_id)
    try:
        bid, count = transfer.import_outline(conn, body.text, body.parent_id, body.title)
    except ValueError as e:
        raise HTTPException(422, str(e))
    notices = settle(conn, bid)
    return {"block_id": bid, "count": count, "notices": [f"Added {count} to-do(s).", *notices]}


# --- links & files on a block ------------------------------------------------------------
class AttachmentIn(BaseModel):
    url: str | None = Field(default=None, max_length=2000)      # a link…
    title: str = Field(default="", max_length=200)
    name: str | None = Field(default=None, max_length=200)      # …or a file: its name and base64 data
    data: str | None = None
    mime: str = Field(default="", max_length=100)


@app.get("/api/blocks/{block_id}/attachments")
def get_attachments(block_id: int, conn=Depends(get_conn)):
    load(conn, block_id)
    return attachments.listing(conn, block_id)


@app.post("/api/blocks/{block_id}/attachments", status_code=201)
def post_attachment(block_id: int, body: AttachmentIn, conn=Depends(get_conn)):
    load(conn, block_id)
    try:
        if body.data is not None:
            return attachments.add_file(conn, block_id, body.name or "file", body.data, body.mime)
        if body.url:
            return attachments.add_link(conn, block_id, body.url, body.title)
    except ValueError as e:
        raise HTTPException(422, str(e))
    raise HTTPException(422, "Give a link or a file.")


@app.get("/api/attachments/{aid}")
def open_attachment(aid: int, download: bool = False, conn=Depends(get_conn)):
    try:
        item = attachments.get(conn, aid)
    except LookupError as e:
        raise HTTPException(404, str(e))
    if item["kind"] != "file" or not attachments.path_of(item).exists():
        raise HTTPException(404, "That file is gone.")
    inline = not download and (item["mime"] or "").startswith(("image/", "application/pdf", "text/plain"))
    return FileResponse(attachments.path_of(item), media_type=item["mime"], filename=item["title"],
                        content_disposition_type="inline" if inline else "attachment")


@app.delete("/api/attachments/{aid}")
def delete_attachment(aid: int, conn=Depends(get_conn)):
    attachments.remove(conn, aid)
    return {"ok": True}


# --- phone access & pairing ---------------------------------------------------------------------
class PairIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _port(request: Request) -> int:
    return request.url.port or 8834


@app.get("/pair")
def pair_page():
    return FileResponse(STATIC / "pair.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/pair")
def pair_device(body: PairIn, request: Request):
    conn = db.connect()
    try:
        try:
            r = devices.pair(conn, body.code, request.headers.get("user-agent", ""))
        except PermissionError as e:
            raise HTTPException(429, str(e))
        except LookupError as e:
            raise HTTPException(400, str(e))
        conn.commit()
    finally:
        conn.close()
    resp = JSONResponse({"install_code": r["install_code"], "name": devices.device_name(request.headers.get("user-agent", ""))})
    _set_device_cookie(resp, r["token"])
    return resp


@app.get("/manifest.webmanifest")
def manifest(install: str | None = None):
    """The installable app's manifest. Right after pairing, the start URL carries a one-time install code so
    the Home Screen app (which doesn't share Safari's cookies) can connect itself on first launch."""
    start = "/"
    if install and re.fullmatch(r"[A-Z0-9-]{6,20}", install):
        start = f"/?install={install}"
    return JSONResponse({
        "name": "Nestwork", "short_name": "Nestwork", "start_url": start, "scope": "/", "display": "standalone",
        "background_color": "#0b0d1b", "theme_color": "#0b0d1b",
        "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


class AwakeIn(BaseModel):
    on: bool


@app.get("/api/phone")
def phone_status(request: Request, conn=Depends(get_conn)):
    return {"tailscale": phone.status(_port(request)), "devices": devices.listing(conn), "port": _port(request),
            "keep_awake": agent.get_setting(conn, "keep_awake") == "1", "keep_awake_available": awake.available(),
            "keep_awake_running": awake.running()}


@app.put("/api/phone/keep-awake")
def phone_keep_awake(body: AwakeIn, conn=Depends(get_conn)):
    agent.set_setting(conn, "keep_awake", "1" if body.on else "0")
    return {"keep_awake": body.on, "keep_awake_running": awake.apply(body.on)}


@app.post("/api/phone/enable")
def phone_enable(request: Request):
    try:
        r = phone.enable(_port(request))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {**r, "tailscale": phone.status(_port(request))}


@app.post("/api/phone/disable")
def phone_disable(request: Request):
    phone.disable()
    return {"tailscale": phone.status(_port(request))}


@app.post("/api/phone/pair-code", status_code=201)
def phone_pair_code(request: Request, conn=Depends(get_conn)):
    import segno
    st = phone.status(_port(request))
    if not st["serving"]:
        raise HTTPException(409, "Turn on phone access first (step 4).")
    c = devices.new_pair_code(conn)
    url = f"{st['url']}/pair#{c['code']}"
    svg = segno.make(url, error="m").svg_inline(scale=5, border=2, dark="#0b0d1b", light="#ffffff")
    return {**c, "url": url, "qr_svg": svg}


@app.patch("/api/devices/{device_id}")
def rename_device(device_id: int, body: DeviceIn, conn=Depends(get_conn)):
    devices.rename(conn, device_id, body.name)
    return {"devices": devices.listing(conn)}


@app.delete("/api/devices/{device_id}")
def revoke_device(device_id: int, conn=Depends(get_conn)):
    devices.revoke(conn, device_id)
    return {"devices": devices.listing(conn)}


# --- the focus timer (shared by every device) ---------------------------------------------
# Not through get_conn: saving the timer shouldn't count as a data change that redraws every open page.
class TimerIn(BaseModel):
    state: dict


class ClaimIn(BaseModel):
    rev: int


class TimerSettingsIn(BaseModel):
    settings: dict


def _timer_conn():
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@app.get("/api/timer")
def get_timer(conn=Depends(_timer_conn)):
    return timer.load(conn)


@app.put("/api/timer")
def put_timer(body: TimerIn, conn=Depends(_timer_conn)):
    return {"rev": timer.save(conn, body.state)}


@app.post("/api/timer/claim")
def claim_timer(body: ClaimIn, conn=Depends(_timer_conn)):
    new = timer.claim(conn, body.rev)
    if new is None:
        raise HTTPException(409, "Another device already handled it.")
    return {"rev": new}


@app.put("/api/timer/settings")
def put_timer_settings(body: TimerSettingsIn, conn=Depends(_timer_conn)):
    return {"settings": timer.save_settings(conn, body.settings)}


# --- plan my day ---------------------------------------------------------------------
class PlanIn(BaseModel):
    items: list[dict]
    day: date | None = None


@app.get("/api/plan")
def get_plan(day: date | None = None, conn=Depends(get_conn)):
    return {"plan": plan.get(conn, day.isoformat() if day else None), "candidates": plan.candidates(conn)[:20],
            "today": date.today().isoformat()}


@app.put("/api/plan")
def put_plan(body: PlanIn, conn=Depends(get_conn)):
    try:
        return {"plan": plan.save(conn, body.items, body.day.isoformat() if body.day else None)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/plan/quick", status_code=201)
def quick_plan(conn=Depends(get_conn)):
    try:
        return {"plan": plan.quick_plan(conn)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/plan")
def clear_plan(conn=Depends(get_conn)):
    plan.clear(conn)
    return {"ok": True}


# --- calendar (Workflow → Calendar) ------------------------------------------------------
@app.get("/api/calendar")
def get_calendar(start: date | None = None, days: int = 7, conn=Depends(get_conn)):
    return calendar_view.build(conn, start or date.today(), days)


class CalItemIn(BaseModel):
    day: date
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    minutes: int = Field(default=plan.DEFAULT_MIN, ge=1, le=720)
    title: str = Field(default="", max_length=200)
    block_id: int | None = None
    kind: str | None = None


class CalItemPatch(BaseModel):
    day: date | None = None            # move it to another day
    start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    minutes: int | None = Field(default=None, ge=1, le=720)
    title: str | None = Field(default=None, max_length=200)
    kind: str | None = None


def _cal_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/calendar/items", status_code=201)
def add_cal_item(body: CalItemIn, conn=Depends(get_conn)):
    item = body.model_dump(exclude={"day"})
    return {"item": _cal_call(plan.add_item, conn, body.day.isoformat(), item)}


@app.patch("/api/calendar/items/{day}/{item_id}")
def patch_cal_item(day: date, item_id: str, body: CalItemPatch, conn=Depends(get_conn)):
    changes = body.model_dump(exclude_none=True)
    if "day" in changes:
        changes["day"] = changes["day"].isoformat()
    return {"item": _cal_call(plan.update_item, conn, day.isoformat(), item_id, changes)}


@app.delete("/api/calendar/items/{day}/{item_id}")
def delete_cal_item(day: date, item_id: str, conn=Depends(get_conn)):
    return {"item": _cal_call(plan.remove_item, conn, day.isoformat(), item_id)}


class PicksIn(BaseModel):
    block_ids: list[int] = []
    items: list[int | str] = []        # to-do ids and lines of text, in order (instead of block_ids)
    note: str = Field(default="", max_length=300)
    append: bool = False


@app.put("/api/today/picks")
def put_picks(body: PicksIn, conn=Depends(get_conn)):
    try:
        return {"picks": plan.save_picks(conn, body.items or body.block_ids, body.note, append=body.append)}
    except ValueError as e:
        raise HTTPException(422, str(e))


class TickIn(BaseModel):
    done: bool = True


@app.post("/api/today/picks/{index}/done")
def tick_pick(index: int, body: TickIn, conn=Depends(get_conn)):
    """Tick off (or un-tick) one of today's to-dos written just for today."""
    try:
        return {"picks": plan.tick_pick(conn, index, body.done)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/today/picks")
def clear_picks(conn=Depends(get_conn)):
    """Back to the automatic to-dos on the HUD."""
    plan.clear_picks(conn)
    return {"ok": True}


# --- weekly review -------------------------------------------------------------------
class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=5000)


@app.get("/api/review")
def get_review(conn=Depends(get_conn)):
    return {**review.build(conn), "history": review.history(conn), "due": review.due(conn)}


@app.post("/api/review", status_code=201)
def save_review(body: ReviewIn, conn=Depends(get_conn)):
    return review.save(conn, body.note)


# --- undo ---------------------------------------------------------------------------
@app.get("/api/undo")
def get_undo(conn=Depends(get_conn)):
    """What ⌘Z would undo (or null)."""
    return {"next": undo.latest(conn)}


@app.post("/api/undo")
def post_undo(conn=Depends(get_conn)):
    try:
        group = undo.undo_last(conn)
    except LookupError as e:
        raise HTTPException(409, str(e))
    who = " (by your agent)" if group["source"] == "agent" else ""
    return {"undone": group, "next": undo.latest(conn), "notices": [f"Undid: {group['label']}{who}."]}


# --- the trash ---------------------------------------------------------------------
@app.get("/api/trash")
def get_trash(conn=Depends(get_conn)):
    return {"items": trash.listing(conn), "keep_days": trash.KEEP_DAYS}


@app.post("/api/trash/{trash_id}/restore")
def restore_from_trash(trash_id: int, conn=Depends(get_conn)):
    try:
        r = trash.restore(conn, trash_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    notices = [f"Restored “{load(conn, r['block_id']).title(r['block_id'])}”.", *r["notices"]]
    notices += settle(conn, r["block_id"])
    return {**r, "notices": notices}


@app.delete("/api/trash/{trash_id}")
def forget_trash_item(trash_id: int, conn=Depends(get_conn)):
    return {"deleted": trash.forget(conn, trash_id)}


@app.delete("/api/trash")
def empty_trash(conn=Depends(get_conn)):
    return {"deleted": trash.forget(conn)}


# --- backups -------------------------------------------------------------------------
@app.get("/api/backups")
def get_backups():
    return {"backups": reset.list_backups(), "keep_auto": reset.KEEP_AUTO, "max_age_days": reset.MAX_AGE.days}


@app.post("/api/backups", status_code=201)
def backup_now(conn=Depends(get_conn)):
    return {"backup": reset.backup(conn, "made by you", list(reset.PARTS)).name}


@app.put("/api/blocks/{block_id}/stages")
def set_stages(block_id: int, body: StagesIn, conn=Depends(get_conn)):
    """Rename, add, remove, reorder and recolor a project's phases.

    Blocks (and rules) in a removed phase move to the nearest phase before it that's kept.
    """
    before = load(conn, block_id)
    if not 2 <= len(body.stages) <= 10:
        raise HTTPException(422, "A project needs between 2 and 10 phases.")
    known = set(before.stages)
    new, seen_roles = [], set()
    for i, st in enumerate(body.stages):
        key = st.key if st.key in known else new_stage_key()
        role = st.role if st.role in ROLES and 0 < i < len(body.stages) - 1 and st.role not in seen_roles else None
        seen_roles.add(role)
        new.append({"key": key, "label": st.label.strip(), "role": role,
                    "color": st.color if st.color in PALETTE else None})
    if len({s["key"] for s in new}) != len(new):
        raise HTTPException(422, "Each phase can only appear once.")
    for i, st in enumerate(new):
        st["color"] = st["color"] or default_color(i, len(new))

    kept = {s["key"] for s in new}
    remap = {}
    for i, key in enumerate(before.stages):
        if key not in kept:
            earlier = [k for k in before.stages[:i] if k in kept]
            later = [k for k in before.stages[i + 1:] if k in kept]
            remap[key] = earlier[-1] if earlier else later[0]
    ids = [i for i in before.blocks if i != before.root_id]
    moved = 0
    for x in ids:
        status = before.blocks[x]["status"]
        target = remap.get(status, status if status in kept else new[0]["key"])
        if target != status:
            moved += 1
            conn.execute("UPDATE blocks SET status = ? WHERE id = ?", (target, x))
    for old, target in remap.items():
        conn.execute("UPDATE OR REPLACE rules SET target_stage = ? WHERE target_stage = ?", (target, old))
        conn.execute("UPDATE rules SET required_stage = ? WHERE required_stage = ?", (target, old))
    # Rules can't target the first phase.
    conn.execute(f"DELETE FROM rules WHERE target_stage = ? AND block_id IN ({','.join('?' * len(ids)) or 'NULL'})",
                 (new[0]["key"], *ids))
    conn.execute("UPDATE blocks SET stage_config = ? WHERE id = ?", (json.dumps(new), before.root_id))
    notices = [f"Moved {moved} item(s) out of removed phases."] if moved else []
    notices += settle(conn, block_id)   # phase indexes changed, so don't diff against `before`
    return {"stages": load(conn, block_id).stages_out(), "notices": notices}


# --- frontend ---------------------------------------------------------------
class FreshStaticFiles(StaticFiles):
    """Make browsers check for a newer app.js/chat.js on every load (cheap: unchanged files get a 304),
    so an update to Nestwork shows up on a normal reload instead of an old cached copy."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", FreshStaticFiles(directory=STATIC), name="static")


@app.get("/sw.js")
def service_worker():
    """The service worker lives at the root so it can serve the whole app (not just /static/)."""
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
