"""Danger zone: start over fresh, with a backup taken first so it can be undone.

`erase()` deletes only the parts you pick (projects, focus history, chat, the agent, settings, and,
only if asked, the shared memory every Claude session uses). Erasing the agent erases everything else in
Nestwork too (projects, focus, chat, settings), so you start over from the awakening. Before deleting anything it copies the
database, the agent folder, links' files, the agent's Claude Code transcripts and the shared memory files to
data/backups/<time>/; `restore()` puts a backup back. The transcripts matter because the chat resumes its latest
Claude Code session: without them a restored agent would still remember everything said after the backup. Both need the exact confirmation phrase, and there is no MCP tool for either.

`auto_backup()` (run by the server's maintenance loop) also saves a daily copy of the database and the
shared memory files (not the agent's photos, which rarely change and can be large), keeping the last
KEEP_AUTO of those. `prune_old()` (same loop) deletes every backup, of any kind, once it is MAX_AGE old.
"""

import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import db, persona

CONFIRM = "start over"            # typed by you in Settings → Danger zone
RESTORE_CONFIRM = "restore"
PARTS = ("projects", "focus", "chat", "agent", "settings", "shared")
WITH_AGENT = ("projects", "focus", "chat", "settings")   # erasing the agent erases all of Nestwork with it
KEEP_IN_AGENT = {"mcp.json"}      # rewritten by the chat on every run anyway
KEEP_AUTO = 14                    # daily automatic backups kept
AUTO_EVERY = timedelta(hours=24)
MAX_AGE = timedelta(days=30)      # any backup older than this is deleted


def backups_dir() -> Path:
    return Path(db.db_path()).resolve().parent / "backups"


def transcripts_dir() -> Path:
    """Where Claude Code keeps the agent's chat transcripts: <shared dir>/projects/ plus the agent folder's path with
    every character other than a letter or digit turned into "-" (how Claude Code names a project's folder)."""
    return persona.shared_dir() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(persona.agent_dir().resolve()))


def cut_transcript(path: Path, until: datetime) -> int:
    """Cut a Claude Code transcript (JSONL, appended in order) back to what it held at `until`: drop the first entry
    stamped later and everything after it. Returns how many lines went."""
    if not path.exists():
        return 0
    until = until.astimezone() if until.tzinfo is None else until
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        try:
            stamp = json.loads(line).get("timestamp")
            later = bool(stamp) and datetime.fromisoformat(stamp) > until
        except (ValueError, AttributeError, TypeError):
            continue
        if later:
            tmp = path.with_suffix(".cut")
            tmp.write_text("".join(lines[:i]), encoding="utf-8")
            shutil.copymode(path, tmp)        # Claude Code keeps transcripts private (0600)
            tmp.replace(path)
            return len(lines) - i
    return 0


def _count(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def summary(conn) -> dict:
    """What would be erased, so the page can say it in numbers."""
    a = persona.agent_dir()
    files = lambda d: sum(1 for f in d.iterdir() if f.is_file()) if d.exists() else 0   # noqa: E731
    profile = persona.profile()
    return {
        "projects": _count(conn, "SELECT COUNT(*) FROM blocks WHERE parent_id IS NULL"),
        "todos": _count(conn, "SELECT COUNT(*) FROM blocks WHERE parent_id IS NOT NULL"),
        "notes": _count(conn, "SELECT COUNT(*) FROM block_notes"),
        "reminders": _count(conn, "SELECT COUNT(*) FROM reminders WHERE status = 'pending'"),
        "trash": _count(conn, "SELECT COUNT(*) FROM trash"),
        "focus_sessions": _count(conn, "SELECT COUNT(*) FROM focus_sessions"),
        "chat_messages": _count(conn, "SELECT COUNT(*) FROM chat_messages"),
        "agent_name": profile["agent_name"],
        "user_name": profile["user_name"],
        "photos": files(a / "images"),
        "wardrobe": files(a / "wardrobe"),
        "pictures": files(a / "generated"),
        "shared_dir": str(persona.shared_dir()),
        "backups": list_backups(),
        "confirm": CONFIRM,
    }


def list_backups() -> list[dict]:
    out = []
    root = backups_dir()
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True):
            m = d / "manifest.json"
            if d.is_dir() and m.exists():
                try:
                    out.append({"name": d.name, **json.loads(m.read_text(encoding="utf-8"))})
                except ValueError:
                    continue
    return out


def backup(conn, reason: str, parts: list[str], agent_files: bool = True, auto: bool = False,
           now: datetime | None = None) -> Path:
    """Copy everything a reset could touch into data/backups/<time>/ (without the agent folder if not agent_files)."""
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    dest = backups_dir() / stamp
    n = 1
    while dest.exists():
        n += 1
        dest = backups_dir() / f"{stamp}_{n}"
    dest.mkdir(parents=True)
    target = sqlite3.connect(dest / "nestwork.db")
    with target:
        conn.backup(target)           # a consistent copy of the live database
    target.close()
    agent = persona.agent_dir()
    if agent_files and agent.exists():
        shutil.copytree(agent, dest / "agent")
    from . import attachments
    if agent_files and attachments.folder().exists():
        shutil.copytree(attachments.folder(), dest / "attachments")
    if agent_files and transcripts_dir().exists():
        shutil.copytree(transcripts_dir(), dest / "transcripts")
    shared = dest / "shared"
    shared.mkdir()
    for key in ("shared_claude", "memory"):
        f = persona.paths()[key]
        if f.exists():
            shutil.copy2(f, shared / f.name)
    s = summary(conn)
    (dest / "manifest.json").write_text(json.dumps({
        "created_at": now.isoformat(timespec="seconds"), "reason": reason, "parts": parts,
        "projects": s["projects"], "todos": s["todos"], "focus_sessions": s["focus_sessions"],
        "chat_messages": s["chat_messages"], "agent_name": s["agent_name"],
        "auto": auto, "agent_files": agent_files and agent.exists(), "transcripts": (dest / "transcripts").exists(),
        "size": sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()),
    }, indent=2), encoding="utf-8")
    return dest


def auto_backup(conn, now: datetime | None = None) -> Path | None:
    """The daily automatic backup: made when the last one is a day old (or there's none), and only when
    there is something to keep. Keeps the newest KEEP_AUTO automatic backups."""
    if os.environ.get("NESTWORK_AUTO_BACKUP", "1") == "0":
        return None
    now = now or datetime.now()
    autos = [b for b in list_backups() if b.get("auto")]
    if autos and now - datetime.fromisoformat(autos[0]["created_at"]) < AUTO_EVERY:
        return None
    if not _count(conn, "SELECT COUNT(*) FROM blocks") and not _count(conn, "SELECT COUNT(*) FROM focus_sessions"):
        return None
    dest = backup(conn, "daily automatic backup", ["projects", "focus", "chat", "settings", "shared"],
                  agent_files=False, auto=True, now=now)
    for old in [b for b in list_backups() if b.get("auto")][KEEP_AUTO:]:
        shutil.rmtree(backups_dir() / old["name"], ignore_errors=True)
    return dest


def prune_old(now: datetime | None = None) -> list[str]:
    """Delete every backup (automatic, by hand or before a reset) older than MAX_AGE. Returns their names."""
    now = now or datetime.now()
    gone = []
    for b in list_backups():
        try:
            old = now - datetime.fromisoformat(b["created_at"]) > MAX_AGE
        except (KeyError, TypeError, ValueError):
            continue
        if old:
            shutil.rmtree(backups_dir() / b["name"], ignore_errors=True)
            gone.append(b["name"])
    return gone


def _empty_agent_dir() -> None:
    agent = persona.agent_dir()
    if not agent.exists():
        return
    for f in agent.iterdir():
        if f.name in KEEP_IN_AGENT:
            continue
        shutil.rmtree(f) if f.is_dir() else f.unlink()


def _reset_ids(conn, *tables: str) -> None:
    """Start numbering from 1 again (sqlite_sequence only exists once a table has had rows)."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({','.join('?' * len(tables))})", tables)


def erase(conn, parts: list[str], confirm: str) -> dict:
    if confirm.strip().lower() != CONFIRM:
        raise ValueError(f"Type “{CONFIRM}” to confirm.")
    if "agent" in parts:   # a new agent starts from nothing: the awakening, no projects
        parts = [*parts, *WITH_AGENT]
    parts = [p for p in PARTS if p in parts]
    if not parts:
        raise ValueError("Pick at least one thing to erase.")
    from . import chat, jobs
    chat.stop()
    for key in list(jobs._tasks):
        jobs.cancel(key)
    dest = backup(conn, "before starting over", parts)

    if "projects" in parts:
        # Rules, notes and reminders about blocks go with them (ON DELETE CASCADE).
        conn.execute("DELETE FROM blocks")
        conn.execute("DELETE FROM reminders")
        conn.execute("DELETE FROM reminder_log")
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM trash")
        conn.execute("DELETE FROM completions")
        conn.execute("DELETE FROM reviews")
        _reset_ids(conn, "blocks", "reminders", "proposals", "block_notes", "trash", "attachments", "completions", "reviews")
        from . import attachments
        shutil.rmtree(attachments.folder(), ignore_errors=True)
    if "focus" in parts:
        conn.execute("DELETE FROM focus_sessions")
        _reset_ids(conn, "focus_sessions")
    if "chat" in parts:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_sessions")
        conn.execute("DELETE FROM settings WHERE key = 'chat_seen_id'")
        shutil.rmtree(chat.uploads_dir(), ignore_errors=True)
    if "settings" in parts:
        conn.execute("DELETE FROM settings WHERE key != 'data_version'")
    if "agent" in parts:
        _empty_agent_dir()
        conn.execute("DELETE FROM settings WHERE key = 'awakened'")
    if "shared" in parts:
        p = persona.paths()
        if p["memory"].exists():
            p["memory"].write_text(persona.MEMORY_TEMPLATE, encoding="utf-8")
        if p["shared_claude"].exists():
            persona.write_fields("about", {f["key"]: "" for f in persona.ABOUT_FIELDS})
    from . import undo
    undo.clear(conn)           # nothing from before a fresh start can be undone into it
    conn.commit()
    persona.ensure_files()     # a fresh agent CLAUDE.md (and memory files) to awaken
    return {"erased": parts, "backup": dest.name}


def restore(conn, name: str, confirm: str) -> dict:
    if confirm.strip().lower() != RESTORE_CONFIRM:
        raise ValueError(f"Type “{RESTORE_CONFIRM}” to confirm.")
    src = backups_dir() / name
    if "/" in name or ".." in name or not (src / "manifest.json").exists():
        raise LookupError("That backup doesn't exist.")
    from . import chat
    chat.stop()
    safety = backup(conn, f"before restoring {name}", list(PARTS))   # restoring can be undone too
    source = sqlite3.connect(src / "nestwork.db")
    with source:
        source.backup(conn)
    source.close()
    db.init_db()               # an older backup may predate newer tables
    from . import agent, undo
    undo.clear(conn)
    agent.set_setting(conn, "chat_fresh_since", str(time.time()))   # the next message starts a new chat session
    conn.commit()
    if (src / "agent").exists():
        _empty_agent_dir()
        shutil.copytree(src / "agent", persona.agent_dir(), dirs_exist_ok=True)
    from . import attachments
    if (src / "attachments").exists():
        shutil.rmtree(attachments.folder(), ignore_errors=True)
        shutil.copytree(src / "attachments", attachments.folder())
    for key in ("shared_claude", "memory"):
        f = src / "shared" / persona.paths()[key].name
        if f.exists():
            shutil.copy2(f, persona.paths()[key])
    if (src / "transcripts").exists():
        shutil.rmtree(transcripts_dir(), ignore_errors=True)
        shutil.copytree(src / "transcripts", transcripts_dir())
    elif transcripts_dir().exists():
        # An older backup without them: cut every transcript back to the backup's time, and remove sessions that
        # started after it. Nothing is lost: the safety backup above holds them as they were.
        made = datetime.fromisoformat(json.loads((src / "manifest.json").read_text(encoding="utf-8"))["created_at"])
        for f in transcripts_dir().glob("*.jsonl"):
            cut_transcript(f, made)
            if not f.read_text(encoding="utf-8").strip():
                f.unlink()
                shutil.rmtree(f.with_suffix(""), ignore_errors=True)   # its tool-results folder
    return {"restored": name, "backup": safety.name}
