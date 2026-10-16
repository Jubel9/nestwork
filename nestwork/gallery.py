"""The agent's gallery and profile picture.

The gallery is every picture the agent generated (drawings and selfies, saved in data/agent/generated),
newest first, each with the chat message it was sent with when there is one. The profile picture is one
of those pictures, chosen by the user; without one, the face photo from the awakening is used. Choosing a
profile picture never replaces the face photo, which stays the reference for selfies.
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import agent, appearance, imagegen

AVATAR_KEY = "avatar_image"          # a generated file's name; "" = the face photo
NAME = re.compile(r"^(\d{8}-\d{6})-(image|selfie)\.\w+$")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _files() -> list[Path]:
    d = imagegen.generated_dir()
    if not d.exists():
        return []
    return sorted((f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                  key=lambda f: (f.stat().st_mtime, f.name), reverse=True)


def _sent(conn: sqlite3.Connection) -> dict[str, dict]:
    """Which chat message each picture was sent with (the first one that shows it)."""
    out = {}
    for r in conn.execute("SELECT id, text, meta, created_at FROM chat_messages WHERE role = 'assistant' "
                          "AND meta LIKE '%/api/generated/%' ORDER BY id"):
        try:
            urls = json.loads(r["meta"] or "{}").get("images") or []
        except json.JSONDecodeError:
            continue
        for url in urls:
            out.setdefault(Path(url).name, {"message_id": r["id"], "text": r["text"][:280], "sent_at": r["created_at"]})
    return out


def avatar_name(conn: sqlite3.Connection) -> str:
    name = agent.get_setting(conn, AVATAR_KEY)
    return name if name and (imagegen.generated_dir() / Path(name).name).is_file() else ""


def items(conn: sqlite3.Connection) -> list[dict]:
    sent, current = _sent(conn), avatar_name(conn)
    out = []
    for f in _files():
        m = NAME.match(f.name)
        created = (datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else datetime.fromtimestamp(f.stat().st_mtime))
        out.append({"name": f.name, "url": f"/api/generated/{f.name}", "kind": m.group(2) if m else "image",
                    "created_at": created.isoformat(timespec="seconds"), "avatar": f.name == current,
                    **({"message": sent[f.name]} if f.name in sent else {})})
    return out


def avatar_file(conn: sqlite3.Connection) -> Path | None:
    name = avatar_name(conn)
    return imagegen.generated_dir() / name if name else appearance.image_file("face")


def set_avatar(conn: sqlite3.Connection, name: str | None) -> str:
    """Use a gallery picture as the profile picture; None or "" goes back to the face photo."""
    name = Path(name).name if name else ""
    if name and not (imagegen.generated_dir() / name).is_file():
        raise ValueError("That picture isn't in the gallery.")
    agent.set_setting(conn, AVATAR_KEY, name)
    return name
