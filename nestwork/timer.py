"""The focus timer's state and settings, kept on the server so every device shows the same timer.

The browser still runs the clock (hud.js); it saves the timer here whenever it changes (start, pause, a new
task, a finished session…), and other devices pick it up through the revision number in /api/version.
When time runs out, every open device notices at once, so the first one to `claim()` the current revision
does the switch (logging the session, starting the break); the others just play the sound and reload.
"""

import json

STATE_KEY, REV_KEY, SETTINGS_KEY = "timer_state", "timer_rev", "timer_settings"


def _get(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _set(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def rev(conn) -> int:
    return int(_get(conn, REV_KEY, "0"))


def load(conn) -> dict:
    return {"state": json.loads(_get(conn, STATE_KEY, "null")), "rev": rev(conn),
            "settings": json.loads(_get(conn, SETTINGS_KEY, "null"))}


def save(conn, state: dict) -> int:
    _set(conn, STATE_KEY, json.dumps(state))
    new = rev(conn) + 1
    _set(conn, REV_KEY, str(new))
    return new


def claim(conn, seen_rev: int) -> int | None:
    """Take the turn to act on the current timer (e.g. its time ran out). Returns the new revision, or None
    when someone else already acted (the caller should reload instead)."""
    current = rev(conn)
    if seen_rev != current:
        return None
    _set(conn, REV_KEY, str(current + 1))
    return current + 1


def save_settings(conn, settings: dict) -> dict:
    _set(conn, SETTINGS_KEY, json.dumps(settings))
    return settings
