"""What AI agents may do, and how their changes are applied.

Agent access (a setting in the app):
  * "read_only": agents can look, every change is refused;
  * "ask":       changes are saved as proposals and only applied when you approve them in the app;
  * "full":      changes apply straight away.
Deleting is a separate switch, off by default.

Every change goes through the same functions as the web app, so rules, automatic moves and loop
protection apply exactly the same. A proposal is tried out first (and rolled back), so an agent
learns immediately when a change would be refused instead of filing one that can never work.
"""

import json
import sqlite3

from fastapi import HTTPException

from . import db, export, repeats

ACCESS_MODES = ("read_only", "ask", "full")
DEFAULTS = {"agent_access": "ask", "allow_delete": "0", "data_version": "0", "agent_tools": "broad", "agent_model": "",
            "agent_effort": "",
            "reminder_style": "quick", "chat_seen_id": "0", "awakened": "0",
            "image_provider": "openrouter", "image_model": "google/gemini-2.5-flash-image",
            "selfie_provider": "openrouter", "selfie_model": "google/gemini-2.5-flash-image",
            "selfie_prompt_model": "haiku", "avatar_image": "", "connectors": "[]", "mcp_seen": "{}",
            "keep_awake": "0", "chat_fresh_since": "0"}
TOOL_LEVELS = ("contained", "web", "broad")
# Claude Code's --effort levels; "" leaves it to Claude Code's default.
EFFORT_LEVELS = ("", "low", "medium", "high", "xhigh", "max")
WRITE_TOOLS = ("add_todo", "update", "move", "move_into", "set_rules", "duplicate", "delete", "install_skill",
               "create_project", "set_phases", "add_outline", "restore_from_trash", "edit_note", "delete_note")
# Tools that throw something away: they also need "Let agents delete" in the settings.
DELETE_TOOLS = ("delete", "delete_note")
# Changes outside the database (skills.py): checked without a dry run, applied without undo.
OUTSIDE_TOOLS = ("install_skill",)


class AgentError(Exception):
    """A change that was refused (by a rule, missing block, bad input or the access setting)."""


# --- settings & change tracking ---------------------------------------------
def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def settings(conn: sqlite3.Connection) -> dict:
    return {"agent_access": get_setting(conn, "agent_access"),
            "allow_delete": get_setting(conn, "allow_delete") == "1",
            "agent_tools": get_setting(conn, "agent_tools"),
            "agent_model": get_setting(conn, "agent_model"),
            "agent_effort": get_setting(conn, "agent_effort"),
            "reminder_style": get_setting(conn, "reminder_style"),
            "awakened": get_setting(conn, "awakened") == "1",
            **{k: get_setting(conn, k) for k in ("image_provider", "image_model", "selfie_provider", "selfie_model",
                                                 "selfie_prompt_model")}}


def after_change() -> None:
    """Bump the version open pages poll for, and rewrite the Markdown snapshot."""
    conn = db.connect()
    with conn:
        set_setting(conn, "data_version", str(int(get_setting(conn, "data_version")) + 1))
    conn.close()
    export.write_snapshot()


def version(conn: sqlite3.Connection) -> dict:
    from . import agentwork, chat, persona
    pending = conn.execute("SELECT COUNT(*) AS n FROM proposals WHERE status = 'pending'").fetchone()["n"]
    last = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM chat_messages WHERE role != 'event'").fetchone()["id"]
    unread = conn.execute("SELECT COUNT(*) AS n FROM chat_messages WHERE id > ? AND role = 'assistant'",
                          (int(get_setting(conn, "chat_seen_id")),)).fetchone()["n"]
    from . import undo
    nxt = undo.latest(conn)
    from . import review
    from . import timer
    return {"version": int(get_setting(conn, "data_version")), "pending": pending, "review_due": review.due(conn),
            "timer_rev": timer.rev(conn),
            "undo": {"label": nxt["label"], "source": nxt["source"]} if nxt else None,
            "files": persona.files_stamp(), "chat_running": chat.is_running(), "chat_doing": chat.doing(),
            "chat_last_id": last, "chat_unread": unread, "agent_work_running": len(agentwork._procs)}


# --- the write actions ---------------------------------------------------------
def _project(conn, block_id: int):
    from .main import load
    try:
        return load(conn, block_id)
    except HTTPException:
        raise AgentError(f"There is no block with id {block_id}. Use `find` to look it up.")


def phase_key(p, phase: str) -> str:
    """Accept a phase by its name (any case) or its key."""
    wanted = phase.strip().lower()
    for s in p.stage_list:
        if wanted in (s["key"].lower(), s["label"].lower()):
            return s["key"]
    names = ", ".join(f"“{s['label']}”" for s in p.stage_list)
    raise AgentError(f"“{phase}” isn't a phase of this project. Its phases are: {names}.")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException as e:
        raise AgentError(str(e.detail))


def _repeat_patch(repeat: str | None, due_date: str | None) -> dict:
    """The patch fields for a repeat written in words ("weekdays", "every 2 weeks", "none")."""
    from datetime import date
    if repeat is None:
        return {}
    try:
        rule = repeats.parse(repeat, date.fromisoformat(due_date) if due_date else None)
    except ValueError as e:
        raise AgentError(str(e))
    return {"repeat": rule} if rule else {"clear_repeat": True}


PRIORITIES = {"none": 0, "low": 1, "medium": 2, "normal": 2, "high": 3, "urgent": 3}


def _priority_patch(priority: str | None) -> dict:
    if priority is None:
        return {}
    key = str(priority).strip().lower()
    if key not in PRIORITIES:
        raise AgentError("Priority is none, low, medium or high.")
    return {"priority": PRIORITIES[key]}


def _estimate_patch(estimate_hours: float | None) -> dict:
    if estimate_hours is None:
        return {}
    if not 0 <= estimate_hours <= 1000:
        raise AgentError("An estimate goes from 0 (none) to 1000 hours.")
    return {"estimate_min": round(estimate_hours * 60)}


def _add_todo(conn, parent_id: int, title: str, description: str = "", due_date: str | None = None,
              phase: str | None = None, due_time: str | None = None, estimate_hours: float | None = None,
              repeat: str | None = None, priority: str | None = None, tags: list[str] | None = None) -> dict:
    from . import main
    p = _project(conn, parent_id)
    status = phase_key(p, phase) if phase else None
    extra = {**_estimate_patch(estimate_hours), **_repeat_patch(repeat, due_date), **_priority_patch(priority),
             **({"tags": tags} if tags is not None else {})}
    r = _call(main.create_block, main.BlockIn(title=title, parent_id=parent_id, status=status, notes=description), conn=conn)
    if due_date or extra:
        try:
            patch = main.BlockPatch(due_date=due_date, due_time=due_time, **extra)
        except ValueError as e:
            raise AgentError(f"Invalid value: {e}")
        r["block"] = _call(main.update_block, r["block"]["id"], patch, conn=conn)["block"]
    r["created_id"] = r["block"]["id"]
    return r


def _update(conn, block_id: int, title: str | None = None, description: str | None = None,
            due_date: str | None = None, clear_due_date: bool = False, due_time: str | None = None,
            daily_pomodoros: int | None = None, estimate_hours: float | None = None, repeat: str | None = None,
            priority: str | None = None, tags: list[str] | None = None, archived: bool | None = None,
            counter_done: int | None = None, counter_total: int | None = None, counter_add: int | None = None,
            counter_unit: str | None = None) -> dict:
    from . import main
    p = _project(conn, block_id)
    extra = {**{k: v for k, v in (("track_done", counter_done), ("track_total", counter_total),
                                  ("track_add", counter_add), ("track_unit", counter_unit)) if v is not None},
             **_estimate_patch(estimate_hours), **_repeat_patch(repeat, due_date or p.blocks[block_id]["due_date"]),
             **_priority_patch(priority), **({"tags": tags} if tags is not None else {}),
             **({"archived": archived} if archived is not None else {})}
    try:
        patch = main.BlockPatch(title=title, notes=description, due_date=due_date, due_time=due_time,
                                clear_due_date=clear_due_date, pomodoros=daily_pomodoros, **extra)
    except ValueError as e:
        raise AgentError(f"Invalid value: {e}")
    return _call(main.update_block, block_id, patch, conn=conn)


def _move(conn, block_id: int, phase: str, before_id: int | None = None) -> dict:
    from . import main
    p = _project(conn, block_id)
    if before_id is not None and (before_id not in p.blocks or before_id == p.root_id
                                  or p.blocks[before_id]["parent_id"] != p.blocks.get(block_id, {}).get("parent_id")):
        raise AgentError("before_id has to be another block in the same place (same parent) as the one you move.")
    return _call(main.move_block, block_id, main.MoveIn(status=phase_key(p, phase), before_id=before_id, by="agent"),
                 conn=conn)


def _set_rules(conn, block_id: int, rules: list[dict]) -> dict:
    from . import main
    p = _project(conn, block_id)
    body = main.RulesIn(rules=[main.RuleIn(
        depends_on_id=r["when_block_id"],
        required_stage=phase_key(p, r["reaches_phase"]) if r.get("reaches_phase") else None,
        target_stage=phase_key(p, r["move_to_phase"]) if r.get("move_to_phase") else None,
    ) for r in rules])
    return _call(main.set_rules, block_id, body, conn=conn)


def _duplicate(conn, block_id: int, new_title: str | None = None) -> dict:
    from . import main
    _project(conn, block_id)
    r = _call(main.duplicate_block, block_id, conn=conn)
    if new_title:
        r["block"] = _call(main.update_block, r["block"]["id"], main.BlockPatch(title=new_title), conn=conn)["block"]
    r["created_id"] = r["block"]["id"]
    return r


def _delete(conn, block_id: int) -> dict:
    from . import main
    _project(conn, block_id)
    return _call(main.delete_block, block_id, by="agent", conn=conn)


def _move_into(conn, block_id: int, new_parent_id: int) -> dict:
    from . import main
    _project(conn, block_id)
    return _call(main.move_into, block_id, main.ReparentIn(parent_id=new_parent_id), conn=conn)


def _create_project(conn, title: str, description: str = "", phases: list[str] | None = None,
                    template: str | None = None, outline: str = "") -> dict:
    from . import main
    from .templates import TEMPLATES
    if template and template not in TEMPLATES:
        raise AgentError(f"There's no template “{template}”. Templates: {', '.join(TEMPLATES)}.")
    if phases and template and template != "blank":
        raise AgentError("A template comes with its own phases: give either phases or a template, not both "
                         "(you can change the phases afterwards with `set_phases`).")
    if phases is not None and len(phases) > 10:
        raise AgentError("A project can have at most 10 phases.")
    try:
        body = main.BlockIn(title=title, notes=description or "", template=template, stages=phases)
    except ValueError as e:
        raise AgentError(f"Invalid value: {e}")
    r = _call(main.create_block, body, conn=conn)
    pid = r["block"]["id"]
    if outline.strip():
        r["notices"] = [*r["notices"], *_add_outline(conn, pid, outline)["notices"]]
    r["created_id"] = pid
    return r


def _set_phases(conn, project_id: int, phases: list) -> dict:
    """Phases in their new order: a name, or {"name", "was", "color", "role"}. A phase keeps its to-dos when it has
    the same name as before or names its old one in "was"; to-dos in a phase left out move to the nearest kept
    phase before it."""
    from . import main
    from .logic import PALETTE, ROLES
    p = _project(conn, project_id)
    old = {s["key"]: s for s in p.stage_list}
    out = []
    for ph in phases:
        ph = {"name": ph} if isinstance(ph, str) else dict(ph)
        name = str(ph.get("name") or "").strip()
        if not name:
            raise AgentError("Every phase needs a name.")
        was = ph.get("was")
        key = phase_key(p, was) if was else next((k for k, s in old.items() if s["label"].lower() == name.lower()), None)
        if key is None and not was:
            try:
                key = phase_key(p, name)   # also matches a phase's key
            except AgentError:
                pass
        role = ph.get("role", old[key]["role"] if key else None)
        role = None if role in (None, "", "none") else str(role).lower()
        if role is not None and role not in ROLES:
            raise AgentError("A phase's role is “active” (being worked on), “review” (waiting for a check) or none.")
        color = ph.get("color") or (old[key]["color"] if key else None)
        if color is not None and color not in PALETTE:
            raise AgentError(f"Colors are: {', '.join(PALETTE)}.")
        out.append(main.StageIn(key=key, label=name[:40], color=color, role=role))
    return _call(main.set_stages, project_id, main.StagesIn(stages=out), conn=conn)


def _add_outline(conn, parent_id: int, outline: str) -> dict:
    from . import main
    _project(conn, parent_id)
    try:
        body = main.OutlineIn(text=outline, parent_id=parent_id)
    except ValueError as e:
        raise AgentError(f"Invalid value: {e}")
    return _call(main.import_outline, body, conn=conn)


def _restore_from_trash(conn, trash_id: int) -> dict:
    from . import main
    r = _call(main.restore_from_trash, trash_id, conn=conn)
    return {**r, "created_id": r["block_id"]}


def _note(conn, note_id: int) -> dict:
    from . import notes
    n = notes.get(conn, note_id)
    if n is None:
        raise AgentError(f"There is no note #{note_id}. `get_block` lists a block's notes with their numbers.")
    if n["kind"] != "note":
        raise AgentError("That's an activity entry, not a note; those can't be changed.")
    return n


def _edit_note(conn, note_id: int, text: str) -> dict:
    from . import main
    _note(conn, note_id)
    try:
        body = main.NoteIn(text=text)
    except ValueError as e:
        raise AgentError(f"Invalid value: {e}")
    return {"note": _call(main.patch_note, note_id, body, conn=conn)}


def _delete_note(conn, note_id: int) -> dict:
    from . import main
    _note(conn, note_id)
    return _call(main.delete_note, note_id, conn=conn)


ACTIONS = {"add_todo": _add_todo, "update": _update, "move": _move, "move_into": _move_into,
           "set_rules": _set_rules, "duplicate": _duplicate, "delete": _delete, "create_project": _create_project,
           "set_phases": _set_phases, "add_outline": _add_outline, "restore_from_trash": _restore_from_trash,
           "edit_note": _edit_note, "delete_note": _delete_note}


def describe(conn, tool: str, args: dict) -> str:
    """One plain sentence for the proposals inbox, e.g. Move “Backstory” (My RPG › Cloud) to “In review”."""
    if tool == "install_skill":
        from . import skills
        return skills.describe(**args)
    def name(block_id):
        try:
            p = _project(conn, block_id)
        except AgentError:
            return f"block #{block_id}"
        where = " › ".join(p.path(block_id)[:-1])
        return f"“{p.title(block_id)}”" + (f" ({where})" if where else "")

    def phase(block_id, ph):
        try:
            p = _project(conn, block_id)
            return f"“{p.label(phase_key(p, ph))}”"
        except AgentError:
            return f"“{ph}”"

    a = args
    if tool == "add_todo":
        extra = (f", due {a['due_date']}" + (f" {a['due_time']}" if a.get("due_time") else "") if a.get("due_date") else "") \
            + (f", estimate {a['estimate_hours']}h" if a.get("estimate_hours") else "") \
            + (f", repeating {a['repeat']}" if a.get("repeat") else "") \
            + (f", {a['priority']} priority" if a.get("priority") else "") \
            + (f", tags {', '.join(a['tags'])}" if a.get("tags") else "") \
            + (" with a description" if a.get("description") else "")
        return f"Add “{a['title']}” inside {name(a['parent_id'])}" + (f" in {phase(a['parent_id'], a['phase'])}" if a.get("phase") else "") + extra
    if tool == "update":
        changes = [f"title to “{a['title']}”" if a.get("title") else None,
                   "the description" if a.get("description") is not None else None,
                   f"due date to {a['due_date']}" + (f" {a['due_time']}" if a.get("due_time") else "") if a.get("due_date") else None,
                   f"time to {a['due_time']}" if a.get("due_time") and not a.get("due_date") else None,
                   "remove the due date" if a.get("clear_due_date") else None,
                   (f"daily pomodoros to {a['daily_pomodoros']}" if a["daily_pomodoros"] else "no daily pomodoros")
                   if a.get("daily_pomodoros") is not None else None,
                   (f"estimate to {a['estimate_hours']}h" if a["estimate_hours"] else "remove the estimate")
                   if a.get("estimate_hours") is not None else None,
                   f"repeat to “{a['repeat']}”" if a.get("repeat") is not None else None,
                   f"priority to {a['priority']}" if a.get("priority") is not None else None,
                   ("tags to " + ", ".join(a["tags"]) if a["tags"] else "remove the tags") if a.get("tags") is not None else None,
                   ("archive it" if a["archived"] else "unarchive it") if a.get("archived") is not None else None,
                   ("remove its counter" if a["counter_total"] == 0 else f"counter total to {a['counter_total']}"
                    + (f" {a['counter_unit']}" if a.get("counter_unit") else "")) if a.get("counter_total") is not None else None,
                   f"counter to {a['counter_done']}" if a.get("counter_done") is not None else None,
                   f"counter {a['counter_add']:+d}" if a.get("counter_add") else None]
        return f"Change {name(a['block_id'])}: " + ", ".join(c for c in changes if c)
    if tool == "move":
        return f"Move {name(a['block_id'])} to {phase(a['block_id'], a['phase'])}" \
            + (f", just before {name(a['before_id'])}" if a.get("before_id") else "")
    if tool == "create_project":
        how = (f" from the “{a['template']}” template" if a.get("template") and a["template"] != "blank" else "") \
            + (" with phases " + " → ".join(f"“{x}”" for x in a["phases"]) if a.get("phases") else "") \
            + (f" and {_outline_count(a['outline'])} to-do(s)" if a.get("outline") else "") \
            + (" and a description" if a.get("description") else "")
        return f"Start a new project “{a['title']}”" + how
    if tool == "set_phases":
        names = [x if isinstance(x, str) else x.get("name", "?") for x in a["phases"]]
        return f"Change the phases of {name(a['project_id'])} to " + " → ".join(f"“{x}”" for x in names)
    if tool == "add_outline":
        return f"Add {_outline_count(a['outline'])} to-do(s) inside {name(a['parent_id'])} from an outline"
    if tool == "restore_from_trash":
        from . import trash
        try:
            t = trash.get(conn, a["trash_id"])
            return f"Restore “{t['title']}” from the trash" + (f" (into “{t['project_title']}”)" if t["project_id"] else "")
        except LookupError:
            return f"Restore trash item #{a['trash_id']}"
    if tool in ("edit_note", "delete_note"):
        from . import notes
        n = notes.get(conn, a["note_id"])
        where = name(n["block_id"]) if n else "a block"
        return (f"Edit note #{a['note_id']} on {where}" if tool == "edit_note"
                else f"Delete note #{a['note_id']} on {where}")
    if tool == "move_into":
        return f"Move {name(a['block_id'])} into {name(a['new_parent_id'])}"
    if tool == "set_rules":
        if not a["rules"]:
            return f"Remove all rules from {name(a['block_id'])}"
        parts = [f"move to {phase(a['block_id'], r.get('move_to_phase') or '') if r.get('move_to_phase') else 'the second phase'} "
                 f"when {name(r['when_block_id'])} reaches {phase(a['block_id'], r['reaches_phase']) if r.get('reaches_phase') else 'the last phase'}"
                 for r in a["rules"]]
        return f"Set rules for {name(a['block_id'])}: " + "; ".join(parts)
    if tool == "duplicate":
        return f"Duplicate {name(a['block_id'])}" + (f" as “{a['new_title']}”" if a.get("new_title") else "")
    if tool == "delete":
        return f"Delete {name(a['block_id'])} and everything inside it (to the trash)"
    return f"{tool} {json.dumps(a)}"


def _outline_count(text: str) -> int:
    from .transfer import parse_outline

    def count(items):
        return sum(1 + count(i["items"]) for i in items)
    try:
        return count(parse_outline(text)[1])
    except Exception:
        return 0


def apply(tool: str, args: dict) -> dict:
    """Run a change for real and save it."""
    from . import undo
    if tool == "install_skill":
        from . import skills
        try:
            return skills.install(**args)
        except (ValueError, OSError) as e:
            raise AgentError(str(e))
    conn = db.connect()
    try:
        undo.begin(conn, describe(conn, tool, args), source="agent")     # one ⌘Z undoes it
        result = ACTIONS[tool](conn, **args)
        undo.end(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    after_change()
    return result


def dry_run(tool: str, args: dict) -> str | None:
    """Try a change without saving it. Returns why it would be refused, or None if it would work."""
    if tool == "install_skill":
        from . import skills
        return skills.check(**args)
    conn = db.connect()
    try:
        ACTIONS[tool](conn, **args)
        return None
    except AgentError as e:
        return str(e)
    finally:
        conn.rollback()
        conn.close()


def request(tool: str, args: dict, agent: str = "AI agent") -> str:
    """Handle a change asked for by an agent according to the access setting. Returns a message for the agent."""
    args = {k: v for k, v in args.items() if v is not None}
    conn = db.connect()
    try:
        s = settings(conn)
        summary = describe(conn, tool, args)
    finally:
        conn.close()
    if s["agent_access"] == "read_only":
        return ("Not done: Nestwork's agent access is set to read only. "
                "The user can change this in Nestwork → Settings. Wanted: " + summary)
    if tool in DELETE_TOOLS and not s["allow_delete"]:
        return "Not done: deleting is turned off for agents in Nestwork → Settings. Wanted: " + summary
    problem = dry_run(tool, args)
    if problem:
        return f"Not done, Nestwork refused it: {problem}"
    if s["agent_access"] == "ask":
        conn = db.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO proposals (agent, tool, args, summary) VALUES (?, ?, ?, ?)",
                (agent, tool, json.dumps(args), summary))
        conn.close()
        after_change()
        return (f"Proposed (#{cur.lastrowid}): {summary}. It's waiting for the user's approval in Nestwork → Settings → "
                "Proposed changes, so it isn't applied yet. Tell the user it needs their approval.")
    try:
        result = apply(tool, args)
    except AgentError as e:
        if tool not in OUTSIDE_TOOLS:
            raise
        return f"Not done: {e}"
    notices = result.get("notices") or []
    if result.get("created_id"):
        notices = [f"Its id is {result['created_id']}.", *notices]
    if tool in ("create_project", "add_outline") and (args.get("outline") or tool == "add_outline"):
        notices.append("Use `whats_left` to see the new to-dos and their ids.")
    return "Done: " + summary + "." + ("\n" + "\n".join(notices) if notices else "")


# --- proposals ----------------------------------------------------------------
def list_proposals(conn, status: str | None = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM proposals" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT 100",
        (status,) if status else (),
    ).fetchall()
    return [{**dict(r), "args": json.loads(r["args"])} for r in rows]


def decide(proposal_id: int, approve: bool) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    finally:
        conn.close()
    if row is None or row["status"] != "pending":
        raise AgentError("That proposal was already handled or doesn't exist.")
    notices: list[str] = []
    if approve:
        result = apply(row["tool"], json.loads(row["args"]))   # raises AgentError if it no longer fits the rules
        notices = result.get("notices") or []
    conn = db.connect()
    with conn:
        conn.execute("UPDATE proposals SET status = ?, decided_at = datetime('now'), result = ? WHERE id = ?",
                     ("approved" if approve else "rejected", "\n".join(notices), proposal_id))
    conn.close()
    after_change()
    return {"notices": notices}

