"""Your Claude subscription's usage limits (the 5-hour session and the weekly limit), for the HUD.

They're read with Claude Code's own `/usage` command run headless (`claude -p /usage`). That command runs
locally: no model call, nothing billed. It prints lines like
"Current session: 13% used · resets Oct 12 at 2:30am (Europe/Berlin)", which are parsed as they are.
Results are cached for a minute so the HUD can ask often.
"""

import asyncio
import json
import re
import tempfile
import time

from . import chat

TTL = 60                      # seconds a reading is reused
LINE = re.compile(r"^(Current [^:]+):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*·\s*resets\s+(.+))?$")
_cache: dict = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


def parse(text: str) -> list[dict]:
    """Every "Current …: N% used · resets …" line, in order. `label` is Claude's own wording."""
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if m:
            label, pct, resets = m.groups()
            key = "session" if label == "Current session" else "week" if label.startswith("Current week (all") \
                else re.sub(r"[^a-z0-9]+", "_", label.lower().removeprefix("current ")).strip("_")
            out.append({"key": key, "label": label, "used_percent": float(pct), "resets": resets})
    return out


async def _run() -> str:
    bin_ = chat.claude_bin()
    if not bin_:
        raise RuntimeError("Claude Code (the `claude` command) wasn't found.")
    proc = await asyncio.create_subprocess_exec(
        bin_, "-p", "/usage", "--output-format", "json", cwd=tempfile.gettempdir(),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Claude Code didn't answer within 30 seconds.")
    try:
        result = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        raise RuntimeError((err.decode().strip().splitlines() or ["Claude Code returned something unexpected."])[-1])
    if result.get("is_error"):
        raise RuntimeError(str(result.get("result") or "Claude Code reported an error."))
    return str(result.get("result") or "")


async def read(force: bool = False) -> dict:
    """{"limits": [...], "checked_at": epoch seconds, "subscription": bool} or {"error": "..."}."""
    async with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < TTL:
            return _cache["data"]
        try:
            text = await _run()
            limits = parse(text)
            data = {"limits": limits, "checked_at": time.time(), "subscription": "subscription" in text.lower()}
            if not limits:
                data["error"] = text.strip().splitlines()[0] if text.strip() else "No usage limits were reported."
        except (RuntimeError, OSError) as e:
            data = {"limits": [], "checked_at": time.time(), "error": str(e)}
        _cache.update(at=time.time(), data=data)
        return data
