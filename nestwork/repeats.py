"""Repeating to-dos: "every day", "every weekday", "every 2 weeks", "every month on the 15th"…

A rule is stored on the block as JSON: {"freq": "daily" | "weekly" | "monthly" | "yearly", "interval": n},
plus "weekdays" (0 = Monday … 6 = Sunday) for weekly rules and "monthday" (1-31) for monthly ones.

When a repeating block reaches its project's last phase it stays done for now, and Nestwork works out when
it comes back (`repeat_next`):
  * its next due date: the first occurrence after its due date (or after today when it's overdue, or
    when it has no due date);
  * the day it reopens: on that next date for daily rules, for rules on picked weekdays and for blocks
    without a due date (habits show up on their day); otherwise the day after the old due date, so a
    monthly report is back on the board early, due a month later.
On the reopen day `reopen_due()` puts the block, and everything inside it, back in the first phase with
the new due date. Moving it out of the last phase by hand before then cancels the pending reopen.
"""

import json
import re
from calendar import monthrange
from datetime import date, timedelta

FREQS = ("daily", "weekly", "monthly", "yearly")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_UNIT = {"daily": ("day", "days"), "weekly": ("week", "weeks"), "monthly": ("month", "months"), "yearly": ("year", "years")}


def normalize(rule: dict | None, anchor: date | None = None) -> dict | None:
    """A clean rule, or None for "doesn't repeat". Raises ValueError for nonsense."""
    if not rule:
        return None
    freq = rule.get("freq")
    if freq not in FREQS:
        raise ValueError("A repeat needs freq: daily, weekly, monthly or yearly.")
    interval = int(rule.get("interval") or 1)
    if not 1 <= interval <= 365:
        raise ValueError("Repeat every 1 to 365 days, weeks, months or years.")
    out = {"freq": freq, "interval": interval}
    if freq == "weekly" and rule.get("weekdays"):
        days = sorted({int(d) for d in rule["weekdays"]})
        if not all(0 <= d <= 6 for d in days):
            raise ValueError("Weekdays go from 0 (Monday) to 6 (Sunday).")
        out["weekdays"] = days
    if freq == "monthly":
        day = int(rule.get("monthday") or (anchor or date.today()).day)
        if not 1 <= day <= 31:
            raise ValueError("A day of the month goes from 1 to 31.")
        out["monthday"] = day
    return out


def load(raw: str | None) -> dict | None:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(rule: dict | None) -> str:
    if not rule:
        return ""
    n, freq = rule["interval"], rule["freq"]
    days = rule.get("weekdays")
    if freq == "weekly" and n == 1 and days == [0, 1, 2, 3, 4]:
        return "Every weekday"
    if freq == "weekly" and n == 1 and days == list(range(7)):
        return "Every day"
    one, many = _UNIT[freq]
    text = f"Every {one}" if n == 1 else f"Every {n} {many}"
    if days:
        text += " on " + ", ".join(DAY_NAMES[d] for d in days)
    if freq == "monthly":
        text += f" on the {_ordinal(rule['monthday'])}"
    return text


def parse(text: str | None, anchor: date | None = None) -> dict | None:
    """Plain words to a rule: "daily", "weekdays", "weekly", "every 2 weeks", "mon, thu", "monthly",
    "every 3 months", "yearly", "none". Raises ValueError when it can't tell."""
    t = (text or "").strip().lower()
    if t in ("", "none", "never", "no", "off", "once"):
        return None
    t = re.sub(r"^every\s+", "", t)
    simple = {"daily": ("daily", 1), "day": ("daily", 1), "weekly": ("weekly", 1), "week": ("weekly", 1),
              "biweekly": ("weekly", 2), "fortnightly": ("weekly", 2), "other week": ("weekly", 2),
              "monthly": ("monthly", 1), "month": ("monthly", 1), "yearly": ("yearly", 1), "year": ("yearly", 1),
              "annually": ("yearly", 1)}
    if t in simple:
        freq, n = simple[t]
        return normalize({"freq": freq, "interval": n}, anchor)
    if t in ("weekday", "weekdays", "workday", "workdays"):
        return normalize({"freq": "weekly", "weekdays": [0, 1, 2, 3, 4]})
    m = re.fullmatch(r"(\d+)\s*(day|week|month|year)s?", t)
    if m:
        freq = {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}[m.group(2)]
        return normalize({"freq": freq, "interval": int(m.group(1))}, anchor)
    names = re.findall(r"[a-z]+", t.replace("on ", " "))
    if names and all(n[:3] in [d.lower() for d in DAY_NAMES] for n in names):
        return normalize({"freq": "weekly", "weekdays": [[d.lower() for d in DAY_NAMES].index(n[:3]) for n in names]})
    raise ValueError(f"“{text}” isn't a repeat Nestwork knows. Try daily, weekdays, weekly, every 2 weeks, "
                     "mon,thu, monthly, every 3 months, yearly or none.")


def _add_months(d: date, months: int, day: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(day, monthrange(y, m)[1]))


def next_after(rule: dict, d: date) -> date:
    """The first occurrence strictly after `d`."""
    n, freq = rule["interval"], rule["freq"]
    if freq == "daily":
        return d + timedelta(days=n)
    if freq == "weekly":
        days = rule.get("weekdays")
        if not days:
            return d + timedelta(weeks=n)
        later = [x for x in days if x > d.weekday()]
        if later:
            return d + timedelta(days=later[0] - d.weekday())
        monday = d - timedelta(days=d.weekday())
        return monday + timedelta(weeks=n, days=days[0])
    if freq == "monthly":
        day = rule.get("monthday") or d.day
        same = _add_months(d, 0, day)
        return same if same > d else _add_months(d, n, day)
    try:
        return d.replace(year=d.year + n)
    except ValueError:      # 29 February
        return date(d.year + n, 2, 28)


def plan(rule: dict, due: str | None, today: date) -> dict:
    """When a repeating block done today comes back: {"reopen": date, "due": date or None}."""
    base = date.fromisoformat(due) if due else today
    nxt = next_after(rule, base)
    while nxt <= today:
        nxt = next_after(rule, nxt)
    on_the_day = not due or rule["freq"] == "daily" or bool(rule.get("weekdays"))
    reopen = nxt if on_the_day else min(nxt, max(base, today) + timedelta(days=1))
    return {"reopen": reopen.isoformat(), "due": nxt.isoformat() if due else None}


def card_fields(b: dict) -> dict:
    rule = load(b.get("repeat"))
    return {"repeat": rule, "repeat_text": describe(rule), "repeat_next": load(b.get("repeat_next"))}


# --- keeping repeat_next in step with the board ------------------------------------------
def schedule(conn, p, today: date | None = None) -> list[int]:
    """For every repeating block in the project: plan its return once it's done; forget it if reopened by hand.
    Returns the blocks that were just planned."""
    today = today or date.today()
    planned = []
    for x, b in p.blocks.items():
        if x == p.root_id or not b.get("repeat"):
            continue
        rule = load(b["repeat"])
        if p.is_done(x) and not b.get("repeat_next") and rule:
            nxt = json.dumps(plan(rule, b["due_date"], today))
            conn.execute("UPDATE blocks SET repeat_next = ? WHERE id = ?", (nxt, x))
            b["repeat_next"] = nxt
            planned.append(x)
        elif not p.is_done(x) and b.get("repeat_next"):
            conn.execute("UPDATE blocks SET repeat_next = NULL WHERE id = ?", (x,))
            b["repeat_next"] = None
    return planned


def reopen_due(conn, today: date | None = None) -> list[str]:
    """Bring back every repeating block whose reopen day has come. Returns notices (one per block)."""
    from . import notes
    from .logic import Project
    from .main import next_position, settle
    today = today or date.today()
    out = []
    for r in conn.execute("SELECT id, repeat_next FROM blocks WHERE repeat_next IS NOT NULL").fetchall():
        nxt = load(r["repeat_next"])
        if not nxt or nxt["reopen"] > today.isoformat():
            continue
        p = Project.for_block(conn, r["id"])
        if p is None:
            continue
        x, first = r["id"], p.stages[0]
        for i in [x, *p.descendants(x)]:
            conn.execute("UPDATE blocks SET status = ?, position = ?, updated_at = datetime('now') WHERE id = ?",
                         (first, next_position(conn, p.blocks[i]["parent_id"], first), i))
        conn.execute("UPDATE blocks SET repeat_next = NULL" + (", due_date = ?" if nxt.get("due") else "") + " WHERE id = ?",
                     (nxt["due"], x) if nxt.get("due") else (x,))
        when = f", due {nxt['due']}" if nxt.get("due") else ""
        notes.log_event(conn, x, f"🔁 Back for the next round{when} ({describe(load(p.blocks[x]['repeat'])).lower()})")
        out.append(f"🔁 “{p.title(x)}” is back in “{p.label(0)}”{when}.")
        settle(conn, x)
    return out
