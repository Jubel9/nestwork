"""Agent-friendly views of all projects: a prioritised "today" list and a Markdown snapshot.

The snapshot is rewritten after every change (see main.py) to a file next to the database,
e.g. data/nestwork.md, so any AI agent or note app can read the current state without the
server or any knowledge of the database.
"""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db, forecast, repeats
from .logic import Project, daily_pomodoros, track

DUE_SOON_DAYS = 3

# Order matters: an item is listed under the first group it fits.
GROUPS = [
    ("overdue", "Overdue"),
    ("due_soon", f"Due in the next {DUE_SOON_DAYS} days"),
    ("in_progress", "Keep going (already started)"),
    ("ready", "Ready to start"),
    ("in_review", "Waiting for review or feedback"),
    ("blocked", "Blocked (waiting on something else)"),
]


def snapshot_path() -> Path | None:
    if os.environ.get("NESTWORK_EXPORT"):
        return Path(os.environ["NESTWORK_EXPORT"])
    path = db.db_path()
    return None if path == ":memory:" else Path(path).with_suffix(".md")


def exports_dir() -> Path:
    """Where the agent's export_block saves files: data/exports/ next to the database."""
    path = db.db_path()
    return (Path.cwd() if path == ":memory:" else Path(path).resolve().parent) / "exports"


def projects(conn: sqlite3.Connection, archived: bool = False) -> list[Project]:
    """Every project that isn't archived (or, with archived=True, only the archived ones)."""
    where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
    rows = conn.execute(f"SELECT id FROM blocks WHERE parent_id IS NULL AND {where} ORDER BY updated_at DESC, id DESC")
    return [Project(conn, r["id"]) for r in rows.fetchall()]


def _group(p: Project, x: int, today: date) -> str:
    due = p.blocks[x]["due_date"]
    if due and due < today.isoformat():
        return "overdue"
    if due and due <= (today + timedelta(days=DUE_SOON_DAYS)).isoformat():
        return "due_soon"
    if p.is_locked(x):
        return "blocked"
    s = p.stage(x)
    if s == p.review_i and p.review_i != p.last:
        return "in_review"
    started_at = p.active_i if p.active_i is not None else 1
    return "in_progress" if s >= started_at else "ready"


def today_items(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Every unfinished smallest task across all projects, grouped by what to do about it."""
    today = today or date.today()
    groups = {key: [] for key, _ in GROUPS}
    # Focus sessions finished today per to-do, against its daily plan.
    from .focus import pomodoros_today
    done_today = pomodoros_today(conn, today)
    for p in projects(conn):
        for x in p.blocks:
            if x == p.root_id or p.children[x] or p.is_done(x):
                continue
            b = p.blocks[x]
            info = p.gate_info(x)
            groups[_group(p, x, today)].append({
                "id": x,
                "title": b["title"],
                "project": p.title(p.root_id),
                "project_id": p.root_id,
                "path": " › ".join(p.path(x)[1:-1]),
                "phase": p.label(p.stage(x)),
                "status": p.stages[p.stage(x)],
                "due_date": b["due_date"],
                "due_time": b.get("due_time"),
                "notes": b["notes"],
                "pomodoros": daily_pomodoros(b),
                "repeat_text": repeats.describe(repeats.load(b.get("repeat"))),
                "estimate_min": b.get("estimate_min"),
                "priority": b.get("priority") or 0,
                "tags": tags_of(b),
                "pomodoros_done": done_today.get(x, 0),
                "locked": info["locked"],
                "waiting_for": [u["text"] for u in info["blocked_by"]],
                "next": ({"phase": p.label(info["gate"]["stage"]), "when": info["gate"]["reasons"]}
                         if info["gate"] else None),
            })
    for key, items in groups.items():
        # Highest priority first, then earliest due, then by project and position in the tree.
        items.sort(key=lambda i: (-i["priority"], i["due_date"] or "9999", i["project"], i["path"], i["title"]))
    out = {
        "date": today.isoformat(),
        "groups": [{"key": k, "title": t, "items": groups[k]} for k, t in GROUPS],
        # Overdue and due-soon first; then started and ready ones, high priority before the rest.
        "focus": (groups["overdue"] + groups["due_soon"]
                  + sorted(groups["in_progress"] + groups["ready"], key=lambda i: -i["priority"]))[:5],
        "picked": None,
    }
    # Picked by your agent (or you) for today: those, in that order, replace the automatic focus. Finished or
    # deleted ones drop out; the pick count lets the HUD say "all done" rather than fall back.
    from .plan import get_picks
    # To-dos written just for today (outside every project) come in `picked.items`, in order with the rest:
    # {"block_id": id} for a project to-do still open, {"index": n, "text": …, "done": bool} for the others.
    if picks := get_picks(conn, today.isoformat()):
        by_id = {i["id"]: i for g in out["groups"] for i in g["items"]}
        out["focus"] = [by_id[i] for i in picks["block_ids"] if i in by_id]
        items = [{"block_id": it["block_id"]} if "block_id" in it else {"index": n, "text": it["text"], "done": it["done"]}
                 for n, it in enumerate(picks["items"]) if "block_id" not in it or it["block_id"] in by_id]
        out["picked"] = {"by": picks["made_by"], "note": picks["note"], "count": len(picks["items"]),
                         "items": items, "updated_at": picks["updated_at"]}
    return out


# --- Markdown ---------------------------------------------------------------
def picked_lines(today: dict, ids: bool = False) -> list[str]:
    """Today's picked to-dos in order: project to-dos as usual, the ones written just for today as checkboxes."""
    by_id = {i["id"]: i for i in today["focus"]}
    out = []
    for n, it in enumerate((today["picked"] or {}).get("items", [])):
        if "text" in it:
            out.append(f"- [{'x' if it['done'] else ' '}] {it['text']} (just for today" + (f", #{it['index']})" if ids else ")"))
        elif i := by_id.get(it["block_id"]):
            out.append(_item_line(i) + (f" [id {i['id']}]" if ids else ""))
    return out


PRIORITY = {1: "low", 2: "medium", 3: "high"}


def tags_of(b) -> list[str]:
    try:
        return json.loads(b.get("tags") or "[]")
    except ValueError:
        return []


def _due(d: str | None, t: str | None = None) -> str:
    return f" · due {d}" + (f" {t}" if t else "") if d else ""


def _item_line(i: dict) -> str:
    where = f"{i['project']}" + (f" › {i['path']}" if i["path"] else "")
    line = f"- **{i['title']}** ({where}) · {i['phase']}{_due(i['due_date'], i.get('due_time'))}"
    if i.get("repeat_text"):
        line += f" · 🔁 {i['repeat_text'].lower()}"
    if i.get("priority"):
        line += f" · {PRIORITY[i['priority']]} priority"
    if i.get("tags"):
        line += " · " + " ".join(f"#{t}" for t in i["tags"])
    if i.get("pomodoros"):
        line += f" · pomodoros {i['pomodoros_done']}/{i['pomodoros']} today"
    if i["waiting_for"]:
        line += f" · 🔒 waits until {' and '.join(i['waiting_for'])}"
    return line


def _quote(text: str, indent: str) -> list[str]:
    return [f"{indent}> {line}" if line.strip() else f"{indent}>" for line in text.strip().splitlines()]


def _tree_lines(p: Project, x: int, depth: int) -> list[str]:
    b = p.blocks[x]
    indent = "  " * depth
    info = p.gate_info(x)
    parts = [p.label(p.stage(x))]
    if p.children[x]:
        done, total = p.leaf_counts(x)
        parts.append(f"{round(p.progress(x) * 100)}% ({done}/{total} tasks)")
    if b["due_date"]:
        parts.append(f"due {b['due_date']}" + (f" {b['due_time']}" if b.get("due_time") else ""))
    if b.get("repeat"):
        parts.append("🔁 " + repeats.describe(repeats.load(b["repeat"])).lower())
    if (t := track(b)) and not p.children[x]:
        parts.append(f"{t['done']}/{t['total']}" + (f" {t['unit']}" if t["unit"] else ""))
    if b.get("estimate_min") and not p.children[x]:
        parts.append(f"estimate {forecast.fmt(b['estimate_min'])}")
    if b.get("priority"):
        parts.append(f"{PRIORITY[b['priority']]} priority")
    if tags_of(b):
        parts.append(" ".join(f"#{t}" for t in tags_of(b)))
    if info["locked"]:
        parts.append("🔒 waits until " + " and ".join(u["text"] for u in info["blocked_by"]))
    elif info["warning"]:
        parts.append("⚠️ further along than its rules allow")
    lines = [f"{indent}- [{'x' if p.is_done(x) else ' '}] **{b['title']}** · {' · '.join(parts)}"]
    if b["notes"].strip():
        lines += _quote(b["notes"], indent + "  ")
    for rule in p.automation(x, builtin=False):   # built-in rules are explained once at the end
        lines.append(f"{indent}  - ⚡ {rule}")
    for c in p.children[x]:
        lines += _tree_lines(p, c, depth + 1)
    return lines


def markdown(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now()
    today = today_items(conn, now.date())
    out = [
        "# Nestwork snapshot",
        "",
        f"_Updated {now:%Y-%m-%d %H:%M}. Written automatically by Nestwork after every change; "
        "edits to this file are overwritten. Make changes in the app._",
        "",
        "## Today",
        "",
    ]
    if today["picked"]:
        out += [f"### Picked for today (by {today['picked']['by']})", ""] + (picked_lines(today) or ["- All done."]) + [""]
    elif today["focus"]:
        out += ["### Suggested focus", ""] + [_item_line(i) for i in today["focus"]] + [""]
    for g in today["groups"]:
        if g["items"]:
            out += [f"### {g['title']} ({len(g['items'])})", ""] + [_item_line(i) for i in g["items"]] + [""]
    if not any(g["items"] for g in today["groups"]):
        out += ["Nothing open. 🎉", ""]

    from . import focus
    out += ["## Focus (Pomodoro sessions, last 7 days)", "", focus.markdown(conn, 7, sessions=False), ""]

    out += ["## Projects", ""]
    for p in projects(conn):
        root = p.blocks[p.root_id]
        done, total = p.leaf_counts(p.root_id)
        phases = []
        for i, s in enumerate(p.stage_list):
            role = " (🔍 review)" if i == p.review_i and i != p.last else " (⚙ work in progress)" if i == p.active_i else ""
            phases.append(s["label"] + role)
        out += [f"### {root['title']} · {round(p.progress(p.root_id) * 100)}% ({done}/{total} tasks)", ""]
        if root["notes"].strip():
            out += _quote(root["notes"], "") + [""]
        out += [f"Phases: {' → '.join(phases)}. The last phase means done.", ""]
        if p.children[p.root_id]:
            out += [f"Forecast: {forecast.summary(forecast.forecast(conn, p, p.root_id, now.date()))}", ""]
        if not p.children[p.root_id]:
            out += ["_No to-dos yet._", ""]
        for c in p.children[p.root_id]:
            out += _tree_lines(p, c, 0)
        out.append("")
    archived = projects(conn, archived=True)
    if archived:
        out += ["## Archived projects", "", ", ".join(f"{p.title(p.root_id)} [id {p.root_id}]" for p in archived), ""]
    out += [
        "## How to read this",
        "",
        "- Blocks nest: indented items are to-dos inside the block above them. Progress counts the smallest tasks.",
        "- 🔒 means the to-do can't start yet; ⚡ lines are rules that move a block automatically.",
        "- 🔁 means it repeats: once done, it comes back to the first phase with its next due date.",
        "- A block with to-dos inside moves to the ⚙ phase when work starts inside it, and can only reach "
        "the 🔍 phase (or the last phase, if there is no 🔍 phase) once everything inside is done.",
        "",
    ]
    return "\n".join(out)


def write_snapshot() -> Path | None:
    path = snapshot_path()
    if path is None:
        return None
    conn = db.connect()
    try:
        text = markdown(conn)
    finally:
        conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)   # atomic: readers never see a half-written file
    return path
