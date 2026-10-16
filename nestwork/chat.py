"""Chat with the Nestwork agent: Claude Code run headless (`claude -p`) with the Nestwork MCP server.

Sessions
--------
Each message continues the latest Claude session (``--resume``). A fresh one (``--session-id``) is
started when you press "New chat", when there is no session yet, after a restore (the old session belongs to
another time), and when the last session is large and has been idle long enough for Claude's prompt cache to
expire: resuming it then re-reads the whole conversation at full price, which can use up a big part of the
usage limit in one message. A fresh chat starts small and is pointed at day_recap for what happened earlier.

Attachments
-----------
Files sent with a message (images, PDFs, text, …) are saved in data/agent/uploads/ and listed at the top of
the prompt; the agent opens them with its Read tool, which shows it images and PDFs as they are.
"""

import asyncio
import base64
import binascii
import json
import mimetypes
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re
import secrets
import shutil
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from . import agent, agentwork, connectors, db, persona, recap, reminders

CHROME = "mcp__claude-in-chrome"   # Claude in Chrome (the browser extension): only with the broad tools
# Skill: headless `claude -p` can't ask before running a skill, so without it every skill that lists its own
# allowed-tools (e.g. humanizer) fails with "Execute skill: …".
TOOLSETS = {
    "contained": ["mcp__nestwork", "Read", "Edit", "Write"],
    "web": ["mcp__nestwork", "Read", "Edit", "Write", "WebSearch", "WebFetch", "Skill"],
    "broad": ["mcp__nestwork", "Read", "Edit", "Write", "WebSearch", "WebFetch", "Skill",
              "Bash", "Glob", "Grep", "TodoWrite", "Task", "NotebookEdit", CHROME],
}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 10
UPLOAD_ID = re.compile(r"^[0-9a-f]{12}_[\w.\- ]{1,120}$")
_running: dict = {"proc": None, "doing": None}   # doing: what the turn is doing now, for the HUD


def now_stamp() -> str:
    """The time a message is sent, in the user's timezone (About you), else this computer's."""
    tz_name = (persona.read_fields("about").get("timezone") or "").strip() if persona.paths()["shared_claude"].exists() else ""
    try:
        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now().astimezone()
    except (ZoneInfoNotFoundError, ValueError):
        now = datetime.now().astimezone()
    zone = tz_name or now.tzname() or ""
    return f"{now:%a %d %b %Y, %H:%M}" + (f" ({zone})" if zone else "")


# --- choosing a session ----------------------------------------------------------
def latest_session(conn):
    row = conn.execute("SELECT * FROM chat_sessions ORDER BY last_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# A session this big (tokens of context) isn't resumed once its prompt cache has expired (CACHE_TTL after its
# last turn): the first message would pay for re-reading all of it.
FRESH_AFTER_TOKENS = 150_000
CACHE_TTL = 60 * 60                  # Claude Code's chat cache lasts an hour


def choose_session(conn, force_new: bool = False, now: float | None = None) -> dict:
    """Continue the latest session unless a new one is due (see the module docstring). Returns {mode, session_id,
    reason}."""
    last = latest_session(conn)
    now = time.time() if now is None else now
    reason = None
    if force_new:
        reason = "you started a new chat"
    elif last is None:
        reason = "first conversation"
    elif last["last_at"] < float(agent.get_setting(conn, "chat_fresh_since") or 0):
        reason = "a backup was restored, so the chat starts fresh"
    elif (last["context_tokens"] or 0) >= FRESH_AFTER_TOKENS and now - last["last_at"] >= CACHE_TTL:
        reason = (f"the last chat was long ({last['context_tokens'] // 1000}k tokens) and idle for over an hour, "
                  "so a fresh one is much cheaper than resuming it")
    if reason:
        return {"mode": "new", "session_id": str(uuid.uuid4()), "reason": reason}
    return {"mode": "resume", "session_id": last["id"], "reason": "continuing the current chat"}


def remember_session(conn, session_id: str, context_tokens: int | None) -> None:
    now = time.time()
    if conn.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)).fetchone():
        conn.execute("UPDATE chat_sessions SET last_at = ?, context_tokens = COALESCE(?, context_tokens), "
                     "turns = turns + 1 WHERE id = ?", (now, context_tokens, session_id))
    else:
        conn.execute("INSERT INTO chat_sessions (id, started_at, last_at, context_tokens, turns) VALUES (?, ?, ?, ?, 1)",
                     (session_id, now, now, context_tokens or 0))


# --- attachments -------------------------------------------------------------------
def uploads_dir() -> Path:
    return persona.agent_dir() / "uploads"


def save_upload(name: str, data_b64: str, mime: str = "") -> dict:
    """Store one file sent with a message. Returns {id, name, mime, size}."""
    try:
        data = base64.b64decode(data_b64.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(f"“{name}” couldn't be read.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"“{name}” is too big: files can be at most {MAX_FILE_BYTES // 1024 // 1024} MB.")
    clean = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip(" .")[:120] or "file"
    fid = f"{secrets.token_hex(6)}_{clean}"
    uploads_dir().mkdir(parents=True, exist_ok=True)
    (uploads_dir() / fid).write_bytes(data)
    return {"id": fid, "name": clean, "mime": (mime or mimetypes.guess_type(clean)[0] or "application/octet-stream")[:100],
            "size": len(data)}


def upload_path(fid: str) -> Path | None:
    """The stored file for an id, or None (unknown, gone, or not a valid id)."""
    if not UPLOAD_ID.match(fid):
        return None
    path = uploads_dir() / fid
    return path if path.is_file() else None


FILE_KINDS = (("image", "Images"), ("pdf", "PDFs"), ("document", "Documents"), ("other", "Other"))
DOCUMENT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".rtf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
                 ".ppt", ".pptx", ".odp", ".pages", ".numbers", ".key", ".epub", ".html", ".htm", ".xml", ".yaml",
                 ".yml", ".log"}


def file_kind(name: str, mime: str) -> str:
    """image, pdf, document or other: the categories of the Files tab."""
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        return "pdf"
    if mime.startswith("text/") or Path(name).suffix.lower() in DOCUMENT_EXTS:
        return "document"
    return "other"


def uploads(conn) -> list[dict]:
    """Every file the user sent in the chat that's still stored, newest first, with the message it came with."""
    out = []
    for r in conn.execute("SELECT id, text, meta FROM chat_messages WHERE role = 'user' AND meta LIKE '%\"files\"%' "
                          "ORDER BY id DESC"):
        try:
            files = json.loads(r["meta"] or "{}").get("files") or []
        except json.JSONDecodeError:
            continue
        for f in files:
            path = upload_path(f.get("id", ""))
            if path is None:
                continue
            out.append({**f, "kind": file_kind(f["name"], f.get("mime") or ""),
                        "url": f"/api/chat/files/{quote(f['id'])}",
                        "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                        "message": {"id": r["id"], "text": r["text"][:280]}})
    return out


def files_note(files: list[dict]) -> str:
    lines = [f"- {(uploads_dir() / f['id']).resolve()} ({f['name']}, {f['mime']}, {max(1, f['size'] // 1024)} KB)"
             for f in files]
    return ("[The user attached these files to this message. Open each with the Read tool before you answer "
            "(it shows you images and PDFs):\n" + "\n".join(lines) + "]")


# --- running Claude ------------------------------------------------------------------
def claude_bin() -> str | None:
    return os.environ.get("NESTWORK_CLAUDE_BIN") or shutil.which("claude") or \
        next((str(p) for p in [Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                               Path("/usr/local/bin/claude")] if p.exists()), None)


def write_mcp_config(agent_name: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    cfg = {"mcpServers": {"nestwork": {
        "command": sys.executable, "args": ["-m", "nestwork.mcp_server"],
        "env": {"PYTHONPATH": str(root), "NESTWORK_DB": str(Path(db.db_path()).resolve()),
                "NESTWORK_AGENT_NAME": agent_name or "Nestwork agent"}}}}
    path = persona.agent_dir() / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def command(session: dict, settings: dict, agent_name: str, apps: dict | None = None) -> list[str]:
    """apps: connectors.flags(), when the user turned on connected apps (Google Calendar, Gmail, …)."""
    apps = apps or {"strict": True, "allow": [], "disallow": [], "names": []}
    tools = TOOLSETS.get(settings.get("agent_tools") or "broad", TOOLSETS["broad"]) + apps["allow"]
    cmd = [claude_bin(), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--mcp-config", str(write_mcp_config(agent_name)), *(["--strict-mcp-config"] if apps["strict"] else []),
           "--permission-mode", "default", "--allowedTools", ",".join(tools),
           *(["--disallowedTools", ",".join(apps["disallow"])] if apps["disallow"] else []),
           "--add-dir", str(persona.shared_dir()), *(["--chrome"] if CHROME in tools else [])]
    cmd += ["--resume", session["session_id"]] if session["mode"] == "resume" else ["--session-id", session["session_id"]]
    if settings.get("agent_model"):
        cmd += ["--model", settings["agent_model"]]
    if settings.get("agent_effort"):
        cmd += ["--effort", settings["agent_effort"]]
    # Claude Code's own prompt leans towards formatted answers; this keeps chat replies human by default.
    cmd += ["--append-system-prompt", CHAT_STYLE + connectors.prompt_note(apps["names"])]
    return cmd


CHAT_STYLE = ("You are chatting with the user inside Nestwork's chat panel. Unless your persona in CLAUDE.md says "
              "otherwise, reply like a person texting: short and casual, one or two sentences, one paragraph, plain text "
              "with no Markdown at all (no **bold**, no lists, no headings). Only use more structure when you're "
              "reporting something that needs it. Each user message starts with [the date and time it was sent]: use it "
              "to know the time of day and how long it's been, but don't repeat it back. When the user wants something "
              "done on a schedule or regularly (\"brief me every morning\", \"check X every Friday\"), create it with "
              "Nestwork's schedule_task tool: it then shows in the Agent's Work tab and its results come into this chat; read earlier results with scheduled_task_results. "
              "For a plain one-off reminder use remind_me.")


TOOL_LABELS = {
    "today": "Checking what's on today", "whats_left": "Looking at what's left", "find": "Searching your blocks",
    "get_block": "Reading a block", "list_projects": "Looking at your projects", "add_todo": "Adding a to-do",
    "update": "Updating a block", "edit_description": "Editing a description", "export_block": "Saving a block to a file", "move": "Moving a block", "set_rules": "Setting rules",
    "duplicate": "Duplicating a block", "delete": "Deleting a block",
    "create_project": "Starting a project", "archive_project": "Archiving a project", "set_phases": "Changing a project's phases",
    "add_outline": "Adding to-dos from an outline", "list_trash": "Looking in the trash",
    "restore_from_trash": "Restoring from the trash", "edit_note": "Editing a note", "delete_note": "Deleting a note",
    "Read": "Reading a file", "Edit": "Editing a file", "Write": "Writing a file", "WebSearch": "Searching the web",
    "WebFetch": "Reading a web page", "Bash": "Running a command", "Glob": "Looking for files", "Grep": "Searching files",
    "TodoWrite": "Planning steps", "Task": "Delegating a sub-task", "ToolSearch": "Getting its tools ready", "Skill": "Using a skill",
    "list_skills": "Looking at its skills", "install_skill": "Installing a skill",
    "take_selfie": "Taking a selfie", "draw_picture": "Drawing a picture", "change_outfit": "Changing clothes",
    "wardrobe_items": "Looking in the wardrobe", "remind_me": "Setting a reminder",
    "change_reminder": "Changing a reminder", "delete_reminder": "Deleting a reminder",
    "set_due_reminders": "Changing reminders", "calendar": "Looking at the calendar",
    "schedule": "Adding to the calendar", "reschedule": "Moving on the calendar", "unschedule": "Removing from the calendar",
    "schedule_task": "Scheduling a task", "list_scheduled_tasks": "Looking at its scheduled tasks",
    "scheduled_task_results": "Reading its scheduled task results", "get_scheduled_task": "Reading a scheduled task",
    "update_scheduled_task": "Changing a scheduled task", "delete_scheduled_task": "Deleting a scheduled task",
    "run_scheduled_task_now": "Starting a scheduled task",
    "pick_todays_todos": "Writing today's to-do list", "tick_todays_todo": "Ticking off a to-do",
    "day_recap": "Checking what happened today", "log_activity": "Logging what you're doing",
    "finish_activity": "Logging that you finished", "change_activity": "Fixing the activity log", "search_chats": "Looking through past chats",
    "recent_chats": "Reading your last chats",
    "remember": "Updating memory", "revise_memory": "Updating memory", "set_about_me": "Updating what it knows about you",
}
APP_VERBS = {"Google_Calendar": "Using your calendar", "Gmail": "Checking your mail", "Google_Drive": "Looking in your Drive"}


def tool_label(name: str, tool_input: dict) -> str:
    if name.startswith("mcp__claude_ai_"):
        app = name.split("__")[1][len("claude_ai_"):]
        return APP_VERBS.get(app, f"Using {app.replace('_', ' ')}")
    if name.startswith(CHROME):
        return "Using your Chrome"
    short = name.split("__")[-1]
    label = TOOL_LABELS.get(short, short)
    path = tool_input.get("file_path") or ""
    if short == "Read" and "/uploads/" in path:
        return "Looking at your attachment"
    if short in ("Edit", "Write") and path.endswith("memory.md"):
        return "Updating memory"
    if short in ("Edit", "Write") and path.endswith("CLAUDE.md"):
        return "Updating its instructions"
    return label


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def history(conn, limit: int = 200, before: int | None = None, after: int | None = None) -> list[dict]:
    """The newest `limit` messages, oldest first; `before`/`after` page by message id."""
    rows = conn.execute("SELECT * FROM (SELECT * FROM chat_messages WHERE role != 'event' AND id < ? AND id > ? "
                        "ORDER BY id DESC LIMIT ?) ORDER BY id",
                        (before if before is not None else 2**62, after or 0, limit)).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in rows]


def save_message(conn, session_id: str, role: str, text: str, meta: dict) -> int:
    cur = conn.execute("INSERT INTO chat_messages (session_id, role, text, meta) VALUES (?, ?, ?, ?)",
                       (session_id, role, text, json.dumps(meta)))
    return cur.lastrowid


async def run(message: str, force_new: bool = False, hidden: bool = False, reminder: dict | None = None,
              files: list[dict] | None = None):
    """Stream one chat turn as server-sent events.

    hidden: the prompt isn't from the user (e.g. an automatic reminder), so it isn't shown in the chat.
    files: attachments already stored with save_upload().
    """
    files = files or []
    if _running["proc"] is not None:
        yield sse("error", {"message": "The agent is still answering. Wait or press Stop."})
        return
    if not claude_bin():
        yield sse("error", {"message": "Couldn't find the `claude` command. Install Claude Code and sign in first."})
        return
    persona.ensure_files()
    conn = db.connect()
    try:
        settings = agent.settings(conn)
        apps = connectors.flags(conn)
        session = choose_session(conn, force_new)
        # The time goes at the start of the message, not in the system prompt, so the cached prefix stays the same.
        prompt = message
        if not hidden:
            head = [f"[{now_stamp()}]"]
            # A fresh session doesn't know what happened earlier today: point it at day_recap (not the content).
            if session["mode"] == "new" and (note := recap.pointer(conn)):
                head.append(note)
            # Scheduled runs missed while Nestwork was off: told once, so the agent can ask about them.
            if missed := agentwork.missed_note(conn):
                head.append(missed)
            # Reminders shown since the user last wrote aren't in Claude's session: say what they were.
            seen = reminders.shown_since_last_message(conn)
            if seen:
                head.append("[Reminders you showed the user in the chat just before this message (and results of "
                            "your scheduled tasks):\n"
                            + "\n---\n".join(seen) + "]\n")
            if files:
                head.append(files_note(files) + "\n")
            prompt = "\n".join(head) + "\n" + message
        save_message(conn, session["session_id"], "event" if hidden else "user", message,
                     {"mode": session["mode"], "reason": session["reason"], **({"files": files} if files else {})})
        conn.commit()
    finally:
        conn.close()
    names = persona.profile()
    yield sse("session", session)

    texts: list[str] = []
    tools: list[dict] = []
    images: list[str] = []
    result: dict = {}
    attempt_session = session
    for attempt in range(2):
        proc = await asyncio.create_subprocess_exec(
            *command(attempt_session, settings, names["agent_name"], apps), cwd=str(persona.agent_dir()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024)
        _running["proc"] = proc
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            current_text = None
            async for raw in proc.stdout:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if e.get("type") == "system" and e.get("subtype") == "init" and not apps["strict"]:
                    # Which servers and connectors Claude Code has (and their sign-in status), for Settings.
                    c = db.connect()
                    with c:
                        connectors.remember(c, e.get("mcp_servers") or [])
                    c.close()
                elif e.get("type") == "stream_event":
                    ev = e["event"]
                    if ev.get("type") == "content_block_start" and ev["content_block"].get("type") == "text":
                        current_text = ""
                        texts.append("")
                    elif ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        if current_text is None:
                            texts.append("")
                            current_text = ""
                        _running["doing"] = "Writing a reply"
                        texts[-1] += ev["delta"]["text"]
                        yield sse("delta", {"text": ev["delta"]["text"]})
                    elif ev.get("type") == "content_block_stop":
                        current_text = None
                elif e.get("type") == "assistant":
                    for block in e["message"].get("content", []):
                        if block.get("type") == "tool_use":
                            t = {"name": block["name"], "label": tool_label(block["name"], block.get("input") or {})}
                            tools.append(t)
                            _running["doing"] = t["label"]
                            yield sse("tool", t)
                elif e.get("type") == "user":
                    # Tool results: a picture the agent made comes back as a [nestwork-image:…] marker.
                    for block in (e.get("message") or {}).get("content") or []:
                        if block.get("type") != "tool_result":
                            continue
                        content = block.get("content")
                        text = content if isinstance(content, str) else " ".join(
                            c.get("text", "") for c in content or [] if isinstance(c, dict))
                        for url in re.findall(r"\[nestwork-image:(/api/generated/[^\]\s]+)\]", text):
                            images.append(url)
                            yield sse("image", {"url": url})
                elif e.get("type") == "result":
                    result = e
            await proc.wait()
            err = (await proc.stderr.read()).decode(errors="replace")
        finally:
            if proc.returncode is None:
                proc.kill()
            _running["proc"] = _running["doing"] = None
        # A session Claude no longer has (e.g. its transcript was deleted): start a fresh one once.
        if attempt == 0 and attempt_session["mode"] == "resume" and not texts and \
                ("No conversation found" in err or "No conversation found" in str(result.get("result", ""))):
            attempt_session = {"mode": "new", "session_id": str(uuid.uuid4()),
                               "reason": "the previous session couldn't be resumed, so starting fresh"}
            yield sse("session", attempt_session)
            continue
        break

    stopped = proc.returncode not in (0, None) and not result
    reply = "\n\n".join(t.strip() for t in texts if t.strip())
    if not reply and result.get("is_error"):
        reply = f"⚠️ {result.get('result') or 'Something went wrong.'}"
    elif not reply and not stopped and err.strip():
        reply = f"⚠️ {err.strip().splitlines()[-1]}"
    usage = result.get("usage") or {}
    context = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    meta = {**({"reminder": True, "block_id": reminder.get("block_id"), "kind": reminder["kind"],
                "title": reminder.get("text") if reminder["kind"] == "custom" else reminder.get("title")}
               if reminder else {}),
            "tools": tools, "images": images, "stopped": stopped, "cost": result.get("total_cost_usd"),
            "cache_read": usage.get("cache_read_input_tokens"), "cache_write": usage.get("cache_creation_input_tokens"),
            "duration_ms": result.get("duration_ms")}
    failed = stopped or (not reply and not images) or reply.startswith("⚠️")
    if hidden and failed:   # e.g. an automatic reminder: the caller falls back to a plain one
        yield sse("done", {"text": reply, "failed": True, **meta})
        return
    conn = db.connect()
    try:
        msg_id = save_message(conn, attempt_session["session_id"], "assistant", reply or ("(stopped)" if stopped else ""), meta)
        remember_session(conn, attempt_session["session_id"], context or None)
        conn.commit()
    finally:
        conn.close()
    agent.after_change()
    yield sse("done", {"text": reply, "failed": failed, "id": msg_id, **meta})   # id: heard once, in this tab


def stop() -> bool:
    proc = _running["proc"]
    if proc is None or proc.returncode is not None:
        return False
    proc.terminate()
    return True


def is_running() -> bool:
    return _running["proc"] is not None


def doing() -> str | None:
    """What the running turn is doing right now (its current tool, or writing the reply), if anything."""
    return _running["doing"] if is_running() else None
