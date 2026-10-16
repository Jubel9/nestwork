"""Estimates, time spent and a finish-date forecast for a project or any block with to-dos inside.

* Each smallest to-do can have an estimate (minutes). Time spent is the focus time logged on it with the
  Pomodoro timer.
* What's left of a to-do is its estimate minus the time spent on it. Once it has used up its estimate and
  still isn't done, a quarter of the estimate is assumed left, so an overrun never counts as finished.
* To-dos without an estimate count at the project's typical (median) estimate, and the forecast says how
  many were assumed. With no estimates at all there's no forecast.
* Repeating to-dos never finish, so they're left out.
* Pace is the focus time on this project per day over the last 14 days (or all your focus time, if none of
  it was on this project yet). The finish date is today plus what's left divided by that pace. It assumes
  you keep working the way you have been; it isn't a plan.
* The longest chain is the longest line of to-dos that wait for each other through rules, by time left
  (or by how many, without estimates): they can only be done one after another, so start them early.
"""

import math
from datetime import date, timedelta
from statistics import median

PACE_DAYS = 14
OVERRUN_LEFT = 0.25


def spent_minutes(conn, ids) -> dict[int, float]:
    ids = list(ids)
    if not ids:
        return {}
    rows = conn.execute(f"SELECT block_id, SUM(focus_min) FROM focus_sessions WHERE block_id IN ({','.join('?' * len(ids))}) "
                        "GROUP BY block_id", ids).fetchall()
    return {r[0]: r[1] or 0 for r in rows}


def pace(conn, project_id: int, today: date) -> tuple[float, str]:
    """(focus minutes a day, "project" or "all") over the last PACE_DAYS days."""
    since = (today - timedelta(days=PACE_DAYS - 1)).isoformat()
    proj = conn.execute("SELECT COALESCE(SUM(focus_min), 0) FROM focus_sessions WHERE project_id = ? AND substr(started_at, 1, 10) >= ?",
                        (project_id, since)).fetchone()[0]
    if proj:
        return proj / PACE_DAYS, "project"
    every = conn.execute("SELECT COALESCE(SUM(focus_min), 0) FROM focus_sessions WHERE substr(started_at, 1, 10) >= ?",
                         (since,)).fetchone()[0]
    return every / PACE_DAYS, "all"


def fmt(minutes: float | None) -> str:
    if minutes is None:
        return "?"
    m = int(round(minutes))
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h and m else f"{h}h" if h else f"{m}m"


def _leaves(p, block_id: int) -> list[int]:
    if not p.children[block_id]:
        return [] if block_id == p.root_id else [block_id]
    return [d for d in p.descendants(block_id) if not p.children[d]]


def _chain(p, leaves: list[int], left: dict[int, float]) -> tuple[list[int], float]:
    """The longest line of open to-dos that wait on each other through rules, weighted by time left."""
    open_set = set(leaves)
    leaves_under = {x: ([x] if not p.children[x] else [d for d in p.descendants(x) if not p.children[d]]) for x in p.blocks}

    def waits_on(x):
        out = set()
        for holder in [x, *p.ancestors(x)]:
            if holder == p.root_id:
                continue
            for r in p.rules[holder]:
                out |= {m for m in leaves_under[r["dep"]] if m in open_set and m != x}
        return out

    deps = {x: waits_on(x) for x in leaves}
    best: dict[int, tuple[float, list[int]]] = {}
    visiting = set()

    def longest(x):
        if x in best:
            return best[x]
        visiting.add(x)
        top = (0.0, [])
        for d in deps[x]:
            if d in visiting:          # rules can't loop, but never spin if they somehow do
                continue
            cand = longest(d)
            if (cand[0], len(cand[1])) > (top[0], len(top[1])):    # more time left, or more to-dos
                top = cand
        visiting.discard(x)
        best[x] = (top[0] + left.get(x, 0), top[1] + [x])
        return best[x]

    # Only real chains count: a single long to-do isn't a line of things waiting on each other.
    lines = [longest(x) for x in leaves]
    total, chain = max((t for t in lines if len(t[1]) > 1), default=(0.0, []), key=lambda t: (t[0], len(t[1])))
    return chain, total


def forecast(conn, p, block_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    all_leaves = [x for x in p.blocks if x != p.root_id and not p.children[x]]
    estimates = [p.blocks[x]["estimate_min"] for x in all_leaves if p.blocks[x].get("estimate_min")]
    typical = median(estimates) if estimates else None
    leaves = [x for x in _leaves(p, block_id) if not p.blocks[x].get("repeat")]
    spent = spent_minutes(conn, leaves)

    left: dict[int, float] = {}
    estimated = assumed = 0
    over = []
    total_est = 0.0
    for x in leaves:
        est = p.blocks[x].get("estimate_min")
        if est:
            total_est += est
        if p.is_done(x):
            continue
        if est:
            estimated += 1
            used = spent.get(x, 0)
            if used >= est:
                over.append(x)
                left[x] = est * OVERRUN_LEFT
            else:
                left[x] = est - used
        elif typical:
            assumed += 1
            left[x] = max(typical - spent.get(x, 0), typical * OVERRUN_LEFT)
    open_count = sum(1 for x in leaves if not p.is_done(x))
    remaining = sum(left.values()) if (typical or not open_count) else None

    per_day, pace_from = pace(conn, p.root_id, today)
    finish = None
    if remaining and per_day > 0:
        finish = (today + timedelta(days=max(1, math.ceil(remaining / per_day)) - 1)).isoformat()
    due = p.blocks[block_id].get("due_date")
    late_days = (date.fromisoformat(finish) - date.fromisoformat(due)).days if finish and due else None
    # The chain is weighed by time left; without estimates it's simply the most to-dos in a row.
    chain, chain_min = _chain(p, [x for x in leaves if not p.is_done(x)], left)

    return {
        "open": open_count,
        "estimated": estimated,
        "assumed": assumed,
        "typical_min": typical,
        "remaining_min": round(remaining) if remaining is not None else None,
        "estimate_min": round(total_est),
        "spent_min": round(sum(spent.values())),
        "over": [{"id": x, "title": p.title(x)} for x in over],
        "pace_min_per_day": round(per_day, 1),
        "pace_from": pace_from,
        "finish": finish,
        "due_date": due,
        "late_days": late_days,
        "chain": [{"id": x, "title": p.title(x), "left_min": round(left.get(x, 0))} for x in chain] if len(chain) > 1 else [],
        "chain_min": round(chain_min) if len(chain) > 1 else 0,
    }


def _chain_size(f: dict) -> str:
    return fmt(f["chain_min"]) if f["chain_min"] else f"{len(f['chain'])} to-dos"


def summary(f: dict) -> str:
    """One or two plain sentences, for the snapshot and the agent."""
    if not f["open"]:
        return "Nothing left to do."
    if f["remaining_min"] is None:
        text = f"No forecast yet: none of its {f['open']} open to-do(s) has an estimate."
        if f["chain"]:
            text += " Longest chain: " + " → ".join(c["title"] for c in f["chain"]) + f" ({_chain_size(f)})."
        return text
    text = f"About {fmt(f['remaining_min'])} of work left"
    bits = []
    if f["estimated"] < f["open"]:
        bits.append(f"{f['estimated']} of {f['open']} open to-dos estimated, the rest counted at {fmt(f['typical_min'])} each")
    if f["over"]:
        bits.append(f"{len(f['over'])} past their estimate")
    if bits:
        text += f" ({'; '.join(bits)})"
    if f["finish"]:
        where = "on this project" if f["pace_from"] == "project" else "in total (none yet on this project)"
        text += f". At your pace of {fmt(f['pace_min_per_day'])} a day {where}, done around {f['finish']}"
        if f["late_days"] is not None:
            text += (f", {f['late_days']} day(s) after its due date {f['due_date']}" if f["late_days"] > 0
                     else f", in time for its due date {f['due_date']}")
    else:
        text += ". No finish date: no focus time logged in the last 14 days"
    text += "."
    if f["chain"]:
        text += " Longest chain: " + " → ".join(c["title"] for c in f["chain"]) + f" ({_chain_size(f)})."
    return text
