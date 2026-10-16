"""Paired devices (your phone) and who may use Nestwork.

Nestwork only listens on this computer (127.0.0.1). Requests made here, to localhost, need nothing. A phone
reaches it through Tailscale (`tailscale serve` passes its requests on to 127.0.0.1, with the phone's address
in X-Forwarded-For and a *.ts.net Host), and needs a device key: a random secret it got by pairing, kept in an
HttpOnly cookie (or sent as `Authorization: Bearer …`). Only a hash of each key is stored.

Pairing: Settings → 📱 Phone shows a one-time code (as a QR code and as text, valid 10 minutes). Scanning it
opens /pair on the phone, which trades the code for a device key. Because an app added to an iPhone's Home
Screen doesn't share Safari's cookies, pairing also hands out a one-time install code that the Home Screen
app trades for its own key on first launch (it's in the app's start URL). Revoking a device ends all its keys.
"""

import hashlib
import secrets
import time
from datetime import datetime

PAIR_TTL = 10 * 60
INSTALL_TTL = 60 * 60
COOKIE = "nw_device"
COOKIE_MAX_AGE = 400 * 24 * 3600
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L: easy to read and type
MAX_FAILS = 20                                        # failed code tries per 10 minutes before pairing pauses

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen  TEXT
);
CREATE TABLE IF NOT EXISTS device_tokens (
    token_hash TEXT PRIMARY KEY,
    device_id  INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pair_codes (
    code_hash  TEXT PRIMARY KEY,
    purpose    TEXT NOT NULL,                   -- pair | install
    device_id  INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
"""

_fails: list[float] = []


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().upper().replace("-", "").encode()).hexdigest()


def _thash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def new_pair_code(conn) -> dict:
    """A one-time pairing code, valid PAIR_TTL seconds. Older unused ones stop working."""
    conn.execute("DELETE FROM pair_codes WHERE purpose = 'pair' OR expires_at < ?", (time.time(),))
    code = _code()
    expires = time.time() + PAIR_TTL
    conn.execute("INSERT INTO pair_codes (code_hash, purpose, expires_at) VALUES (?, 'pair', ?)", (_hash(code), expires))
    return {"code": code, "expires_at": expires}


def _take(conn, code: str, purpose: str):
    global _fails
    now = time.time()
    _fails = [t for t in _fails if now - t < PAIR_TTL]
    if len(_fails) >= MAX_FAILS:
        raise PermissionError("Too many wrong codes. Wait 10 minutes and make a new code on your Mac.")
    row = conn.execute("SELECT * FROM pair_codes WHERE code_hash = ? AND purpose = ? AND expires_at > ?",
                       (_hash(code), purpose, now)).fetchone()
    if row is None:
        _fails.append(now)
        raise LookupError("That code isn't valid (codes work once, for 10 minutes). Make a new one on your Mac.")
    conn.execute("DELETE FROM pair_codes WHERE code_hash = ?", (row["code_hash"],))
    return row


def _issue(conn, device_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO device_tokens (token_hash, device_id, created_at) VALUES (?, ?, ?)",
                 (_thash(token), device_id, _now()))
    return token


def device_name(user_agent: str) -> str:
    ua = user_agent or ""
    for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"), ("Macintosh", "Mac"), ("Windows", "Windows PC")):
        if key in ua:
            return name
    return "Phone"


def pair(conn, code: str, user_agent: str = "") -> dict:
    """Trade a pairing code for a new device and its key. Also returns an install code for the Home Screen app."""
    _take(conn, code, "pair")
    cur = conn.execute("INSERT INTO devices (name, user_agent, created_at, last_seen) VALUES (?, ?, ?, ?)",
                       (device_name(user_agent), user_agent[:300], _now(), _now()))
    device_id = cur.lastrowid
    install = _code()
    conn.execute("INSERT INTO pair_codes (code_hash, purpose, device_id, expires_at) VALUES (?, 'install', ?, ?)",
                 (_hash(install), device_id, time.time() + INSTALL_TTL))
    return {"device_id": device_id, "token": _issue(conn, device_id), "install_code": install}


def redeem_install(conn, code: str) -> str:
    """The Home Screen app's first launch: a key of its own for the same device."""
    row = _take(conn, code, "install")
    return _issue(conn, row["device_id"])


def check(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute("SELECT d.* FROM device_tokens t JOIN devices d ON d.id = t.device_id WHERE t.token_hash = ?",
                       (_thash(token),)).fetchone()
    if row is None:
        return None
    if not row["last_seen"] or row["last_seen"][:16] != _now()[:16]:     # at most once a minute
        conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (_now(), row["id"]))
        conn.commit()
    return dict(row)


def listing(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name, created_at, last_seen FROM devices ORDER BY id")]


def rename(conn, device_id: int, name: str) -> None:
    conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name.strip()[:60] or "Phone", device_id))


def revoke(conn, device_id: int) -> None:
    conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))     # its keys and codes go with it
