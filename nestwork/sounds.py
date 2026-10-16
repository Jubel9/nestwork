"""Notification sounds: presets (synthesized in the browser, see static/sounds.js), your own uploaded audio
files, and named notifications that pick one of them with a volume.

Six notifications always exist (their `key`): a message while you're looking at the chat, a message while
you're away (chat closed, another tab or app), a scheduled task finishing, a reminder, and the focus timer's
end of a focus session and end of a break. You add your own
("Wake up", "Sleep") and pick one per scheduled task in Agent's Work.

A sound is referenced as "preset:<key>", "file:<id>" or "none". Files live in data/agent/sounds/.

Every open Nestwork page (each tab, browser, address and your phone) hears new messages, so each asks
`claim()` before playing one: only the first page to ask for a message plays it. Pages you're looking at ask
right away and the others a moment later, so the sound comes from where you are.
"""

import base64
import binascii
import re
import secrets
import threading
from pathlib import Path

from . import persona

_claimed: set[int] = set()   # message ids whose sound a page already played (since the server started)
_claim_lock = threading.Lock()


def claim(message_id: int) -> bool:
    """True for the first page to ask for this message's sound, False for every one after it."""
    with _claim_lock:
        if message_id in _claimed:
            return False
        _claimed.add(message_id)
        if len(_claimed) > 500:
            _claimed.difference_update(sorted(_claimed)[:250])
        return True


PRESETS = {
    "pop": "Pop", "bubble": "Bubble", "chime": "Chime", "marimba": "Marimba", "bell": "Bell", "ping": "Ping",
    "glass": "Glass", "harp": "Harp", "koto": "Koto", "arcade": "Arcade", "soft": "Soft hum", "sunrise": "Sunrise",
    "alarm": "Alarm",
}
DEFAULTS = [   # key, name, sound, volume, help
    ("chat_open", "Message while chatting", "preset:pop", 0.5, "A new message while the chat is open in front of you."),
    ("chat_away", "Message while away", "preset:chime", 0.8, "A new message while the chat is closed or you're in another tab or app."),
    ("task", "Scheduled task done", "preset:marimba", 0.8, "A scheduled task from Agent's Work finished (unless the task picks another)."),
    ("reminder", "Reminder", "preset:bell", 0.9, "A reminder arrives in the chat."),
    ("focus_end", "Focus session over", "preset:harp", 0.7, "The focus timer ran out: time for a break."),
    ("break_end", "Break over", "preset:sunrise", 0.7, "The break ran out: back to work."),
]
HELP = {k: h for k, _, _, _, h in DEFAULTS}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".webm", ".flac"}
MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE,                  -- chat_open | chat_away | task | reminder | focus_end | break_end; NULL for your own
    name       TEXT NOT NULL,
    sound      TEXT NOT NULL,                -- preset:<key> | file:<id> | none
    volume     REAL NOT NULL DEFAULT 0.8,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sound_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    filename   TEXT NOT NULL,
    mime       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def install(conn) -> None:
    conn.executescript(SCHEMA)
    for key, name, sound, volume, _ in DEFAULTS:
        conn.execute("INSERT OR IGNORE INTO notifications (key, name, sound, volume) VALUES (?, ?, ?, ?)",
                     (key, name, sound, volume))


def sounds_dir() -> Path:
    return persona.agent_dir() / "sounds"


# --- reading ------------------------------------------------------------------------------
def files(conn) -> list[dict]:
    return [{**dict(r), "url": f"/api/sounds/files/{r['id']}"}
            for r in conn.execute("SELECT * FROM sound_files ORDER BY name COLLATE NOCASE, id")]


def notifications(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM notifications ORDER BY key IS NULL, id").fetchall()
    return [{**dict(r), "help": HELP.get(r["key"], "")} for r in rows]


def library(conn) -> dict:
    return {"presets": [{"key": k, "name": v} for k, v in PRESETS.items()], "files": files(conn),
            "notifications": notifications(conn)}


def get(conn, nid: int) -> dict:
    r = conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone()
    if r is None:
        raise LookupError("There's no such notification.")
    return dict(r)


def file_path(conn, fid: int) -> Path | None:
    r = conn.execute("SELECT filename FROM sound_files WHERE id = ?", (fid,)).fetchone()
    if r is None:
        return None
    path = sounds_dir() / r["filename"]
    return path if path.is_file() else None


def label(conn, notify: str | None) -> str:
    """How a scheduled task's notification choice reads: "default", "silent" or the notification's name."""
    if not notify:
        return "default"
    if notify == "none":
        return "silent"
    try:
        return get(conn, int(notify))["name"]
    except (LookupError, ValueError):
        return "default"


def resolve(conn, choice: str | None) -> str | None:
    """A scheduled task's notification from what the agent or the editor gave: None (the default), "none"
    (silent) or a notification id as text. Accepts an id or a name (any case). Raises ValueError."""
    c = (choice or "").strip()
    if c.lower() in ("", "default", "task"):
        return None
    if c.lower() in ("none", "silent", "off", "no sound", "mute"):
        return "none"
    rows = notifications(conn)
    for n in rows:
        if c == str(n["id"]) or c.lower() == n["name"].lower():
            return str(n["id"])
    raise ValueError(f"there's no notification called “{c}”. You have: "
                     + ", ".join(f"“{n['name']}”" for n in rows) + " (or “silent”).")


# --- changing -------------------------------------------------------------------------------
def _check_sound(conn, sound: str) -> str:
    sound = (sound or "").strip()
    if sound == "none":
        return sound
    kind, _, ref = sound.partition(":")
    if kind == "preset" and ref in PRESETS:
        return sound
    if kind == "file" and ref.isdigit() and conn.execute("SELECT 1 FROM sound_files WHERE id = ?", (int(ref),)).fetchone():
        return sound
    raise ValueError("Pick one of the sounds.")


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("Give it a name.")
    return name


def add_notification(conn, name: str, sound: str, volume: float = 0.8) -> int:
    name = _clean_name(name)
    if conn.execute("SELECT 1 FROM notifications WHERE lower(name) = lower(?)", (name,)).fetchone():
        raise ValueError(f"You already have a notification called “{name}”.")
    cur = conn.execute("INSERT INTO notifications (name, sound, volume) VALUES (?, ?, ?)",
                       (name, _check_sound(conn, sound), max(0.0, min(1.0, float(volume)))))
    return cur.lastrowid


def update_notification(conn, nid: int, name: str | None = None, sound: str | None = None,
                        volume: float | None = None) -> dict:
    n = get(conn, nid)
    new_name = _clean_name(name) if name is not None else n["name"]
    if new_name.lower() != n["name"].lower() and conn.execute(
            "SELECT 1 FROM notifications WHERE lower(name) = lower(?) AND id != ?", (new_name, nid)).fetchone():
        raise ValueError(f"You already have a notification called “{new_name}”.")
    conn.execute("UPDATE notifications SET name = ?, sound = ?, volume = ? WHERE id = ?",
                 (new_name, _check_sound(conn, sound) if sound is not None else n["sound"],
                  max(0.0, min(1.0, float(volume))) if volume is not None else n["volume"], nid))
    return get(conn, nid)


def delete_notification(conn, nid: int) -> str:
    n = get(conn, nid)
    if n["key"]:
        raise ValueError("The built-in notifications can't be deleted; set their sound to None to mute one.")
    conn.execute("UPDATE agent_jobs SET notify = NULL WHERE notify = ?", (str(nid),))   # back to the default
    conn.execute("DELETE FROM notifications WHERE id = ?", (nid,))
    return n["name"]


def save_file(conn, name: str, data_b64: str, mime: str = "") -> dict:
    clean = re.sub(r"[^\w.\- ]", "_", Path(name or "").name).strip(" .")[:80] or "sound"
    ext = Path(clean).suffix.lower()
    if ext not in AUDIO_EXTS and not (mime or "").startswith("audio/"):
        raise ValueError(f"“{clean}” isn't an audio file (use .mp3, .m4a, .wav or .ogg).")
    try:
        data = base64.b64decode(data_b64.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(f"“{clean}” couldn't be read.")
    if not data:
        raise ValueError(f"“{clean}” is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"“{clean}” is too big: sounds can be at most {MAX_BYTES // 1024 // 1024} MB.")
    if conn.execute("SELECT COUNT(*) FROM sound_files").fetchone()[0] >= MAX_FILES:
        raise ValueError(f"You can keep up to {MAX_FILES} sounds: delete one first.")
    filename = f"{secrets.token_hex(6)}_{clean}"
    sounds_dir().mkdir(parents=True, exist_ok=True)
    (sounds_dir() / filename).write_bytes(data)
    display = Path(clean).stem.replace("_", " ").strip() or "Sound"
    cur = conn.execute("INSERT INTO sound_files (name, filename, mime, size) VALUES (?, ?, ?, ?)",
                       (display, filename, (mime or "audio/mpeg")[:60], len(data)))
    return {**dict(conn.execute("SELECT * FROM sound_files WHERE id = ?", (cur.lastrowid,)).fetchone()),
            "url": f"/api/sounds/files/{cur.lastrowid}"}


def rename_file(conn, fid: int, name: str) -> None:
    conn.execute("UPDATE sound_files SET name = ? WHERE id = ?", (_clean_name(name), fid))


def delete_file(conn, fid: int) -> list[str]:
    """Remove an uploaded sound. Notifications that used it go back to their default sound (or Chime).
    Returns the names of the notifications that changed."""
    r = conn.execute("SELECT * FROM sound_files WHERE id = ?", (fid,)).fetchone()
    if r is None:
        raise LookupError("There's no such sound.")
    fallback = {k: s for k, _, s, _, _ in DEFAULTS}
    changed = []
    for n in conn.execute("SELECT * FROM notifications WHERE sound = ?", (f"file:{fid}",)).fetchall():
        conn.execute("UPDATE notifications SET sound = ? WHERE id = ?", (fallback.get(n["key"], "preset:chime"), n["id"]))
        changed.append(n["name"])
    conn.execute("DELETE FROM sound_files WHERE id = ?", (fid,))
    (sounds_dir() / r["filename"]).unlink(missing_ok=True)
    return changed
