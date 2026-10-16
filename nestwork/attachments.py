"""Links and files on a block: a URL to a doc or a site, or a file (PDF, screenshot, slides…).

Files are stored in data/attachments/<project name>/ (next to the database), one folder per project, under a
random prefix plus a cleaned name. Rows keep only that unique stored name, so a file is found wherever it
sits; tidy() moves files into their project's current folder (e.g. after a rename) without touching the
rows, which keeps undo and the trash consistent. Removing one (or deleting its
block) only removes the row, so undo and the trash can bring it back; files nothing refers to any more (no
row, nothing in the trash) are cleaned up by the maintenance loop after a day.
"""

import base64
import binascii
import json
import mimetypes
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import db

MAX_BYTES = 25 * 1024 * 1024
SCHEMES = ("http", "https", "mailto", "obsidian")
ORPHAN_GRACE = 24 * 3600          # seconds before an unreferenced file is removed


def folder() -> Path:
    return Path(db.db_path()).resolve().parent / "attachments"


def _folder_name(title: str) -> str:
    """A project title made safe as a folder name (Finder- and Windows-friendly)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")[:80].strip(" .")
    return name or "Untitled project"


def project_folders(conn) -> dict[int, str]:
    """Folder name for every project; projects sharing a name get their id added so they never mix."""
    names = {r["id"]: _folder_name(r["title"]) for r in conn.execute("SELECT id, title FROM blocks WHERE parent_id IS NULL")}
    seen: dict[str, int] = {}
    for n in names.values():
        seen[n.lower()] = seen.get(n.lower(), 0) + 1
    return {i: f"{n} ({i})" if seen[n.lower()] > 1 else n for i, n in names.items()}


def _project_of(conn, block_id: int) -> int:
    return conn.execute("WITH RECURSIVE up(id, parent_id) AS (SELECT id, parent_id FROM blocks WHERE id = ? "
                        "UNION ALL SELECT b.id, b.parent_id FROM blocks b JOIN up ON b.id = up.parent_id) "
                        "SELECT id FROM up WHERE parent_id IS NULL", (block_id,)).fetchone()[0]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(r)
    d["href"] = d["url"] if d["kind"] == "link" else f"/api/attachments/{d['id']}"
    return d


def listing(conn, block_id: int) -> list[dict]:
    return [_row(r) for r in conn.execute("SELECT * FROM attachments WHERE block_id = ? ORDER BY id", (block_id,))]


def counts(conn, ids) -> dict[int, int]:
    ids = list(ids)
    if not ids:
        return {}
    rows = conn.execute(f"SELECT block_id, COUNT(*) FROM attachments WHERE block_id IN ({','.join('?' * len(ids))}) "
                        "GROUP BY block_id", ids).fetchall()
    return {r[0]: r[1] for r in rows}


def get(conn, aid: int) -> dict:
    r = conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone()
    if r is None:
        raise LookupError("That link or file is gone.")
    return _row(r)


def add_link(conn, block_id: int, url: str, title: str = "", by: str = "you") -> dict:
    url = url.strip()
    scheme = re.match(r"^([a-zA-Z][\w+.-]*):", url)
    if scheme and scheme.group(1).lower() not in SCHEMES and not re.match(r"^[\w.-]+:\d+", url):
        raise ValueError("That isn't a link Nestwork can open (http, https, mailto or obsidian).")
    if url and not scheme:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in SCHEMES or not (parsed.netloc or parsed.scheme in ("mailto", "obsidian")):
        raise ValueError("That isn't a link Nestwork can open (http, https, mailto or obsidian).")
    title = title.strip() or (parsed.netloc + parsed.path.rstrip("/")).removeprefix("www.") or url
    cur = conn.execute("INSERT INTO attachments (block_id, kind, title, url, added_by, created_at) VALUES (?, 'link', ?, ?, ?, ?)",
                       (block_id, title[:200], url[:2000], by, _now()))
    return get(conn, cur.lastrowid)


def add_file(conn, block_id: int, name: str, data_b64: str, mime: str = "", by: str = "you") -> dict:
    try:
        data = base64.b64decode(data_b64.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("That file couldn't be read.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"Files can be at most {MAX_BYTES // 1024 // 1024} MB.")
    clean = re.sub(r"[^\w.\- ]", "_", Path(name).name).strip(" .")[:120] or "file"
    stored = f"{secrets.token_hex(6)}_{clean}"
    home = folder() / project_folders(conn)[_project_of(conn, block_id)]
    home.mkdir(parents=True, exist_ok=True)
    (home / stored).write_bytes(data)
    mime = mime or mimetypes.guess_type(clean)[0] or "application/octet-stream"
    cur = conn.execute("INSERT INTO attachments (block_id, kind, title, filename, mime, size, added_by, created_at) "
                       "VALUES (?, 'file', ?, ?, ?, ?, ?, ?)", (block_id, clean, stored, mime[:100], len(data), by, _now()))
    return get(conn, cur.lastrowid)


def remove(conn, aid: int) -> None:
    conn.execute("DELETE FROM attachments WHERE id = ?", (aid,))


def path_of(item: dict) -> Path:
    """Where the file is: in some project's folder, or (stored before project folders) at the top."""
    name = Path(item["filename"]).name
    return next(folder().glob(f"*/{name}"), folder() / name)


def tidy(conn) -> int:
    """Put every attached file in its project's current folder. Returns how many files moved."""
    if not folder().exists():
        return 0
    names = project_folders(conn)
    rows = conn.execute("WITH RECURSIVE up(id, root) AS (SELECT id, id FROM blocks WHERE parent_id IS NULL "
                        "UNION ALL SELECT b.id, up.root FROM blocks b JOIN up ON b.parent_id = up.id) "
                        "SELECT a.filename, up.root FROM attachments a JOIN up ON up.id = a.block_id "
                        "WHERE a.filename IS NOT NULL").fetchall()
    moved = 0
    for filename, root in rows:
        src, dest = path_of({"filename": filename}), folder() / names[root] / Path(filename).name
        if src != dest and src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            moved += 1
    for d in folder().iterdir():          # folders of renamed or emptied projects
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return moved


def clean_orphans(conn, now: float | None = None) -> int:
    """Remove stored files no row and no trash item refers to, once they're a day old."""
    if not folder().exists():
        return 0
    used = {r[0] for r in conn.execute("SELECT filename FROM attachments WHERE filename IS NOT NULL")}
    for (payload,) in conn.execute("SELECT payload FROM trash"):
        used |= {a.get("filename") for a in json.loads(payload).get("attachments", [])}
    used |= {json.loads(r[0]).get("filename") for r in conn.execute(
        "SELECT old FROM undo_log WHERE tbl = 'attachments' AND old IS NOT NULL")}   # an undo could bring it back
    now = now or time.time()
    used = {Path(u).name for u in used if u}
    gone = 0
    for f in [*folder().iterdir(), *folder().glob("*/*")]:
        if f.is_file() and not f.name.startswith(".") and f.name not in used and now - f.stat().st_mtime > ORPHAN_GRACE:
            f.unlink()
            gone += 1
    return gone
