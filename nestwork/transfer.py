"""Moving projects in and out of Nestwork: JSON export/import and importing a Markdown outline.

JSON (`export_block`, `export_all`, `import_data`) keeps everything: phases, the nested to-dos with their
phase, description, due date, reminders, daily pomodoros, estimate, repeat, priority and tags, the rules
between them, the notes & activity log, and links (files stay on this computer). Items refer to each other by "ref" (their id when exported),
so an import gets fresh ids and its rules point to the imported copies. Importing always adds new projects;
it never overwrites what's there.

Outline (`parse_outline`, `import_outline`) turns Markdown such as an Obsidian note into to-dos:
  # Headings and list items ("-", "*", "+", "1.") nest by level and indentation;
  "- [x] item" is done (last phase), "- [ ] item" isn't; plain lines under an item become its description.
"""

import json
import re
from datetime import datetime

from . import attachments
from .logic import Project, daily_pomodoros, stages_from_labels, track

FORMAT = 1
FIELDS = ("notes", "due_date", "due_time", "remind", "pomodoros", "estimate_min", "repeat", "priority", "tags")


# --- export -----------------------------------------------------------------------
def _item(conn, p: Project, x: int) -> dict:
    b = p.blocks[x]
    out = {"ref": x, "title": b["title"], "status": p.stages[p.stage(x)]}
    for f in FIELDS:
        v = daily_pomodoros(b) if f == "pomodoros" else b.get(f)   # older to-dos: blank means 4
        if v not in (None, "", 0) or (f == "pomodoros" and v == 0):
            out[f] = json.loads(v) if f in ("remind", "repeat", "tags") and isinstance(v, str) else v
    if t := track(b):
        out["track"] = t
    if p.rules[x]:
        out["rules"] = [{"when": r["dep"], "reaches": r["required"], "move_to": r["target"]} for r in p.rules[x]]
    log = conn.execute("SELECT kind, author, author_name, text, created_at FROM block_notes WHERE block_id = ? "
                       "ORDER BY created_at", (x,)).fetchall()
    if log:
        out["log"] = [dict(r) for r in log]
    links = conn.execute("SELECT title, url FROM attachments WHERE block_id = ? AND kind = 'link' ORDER BY id", (x,)).fetchall()
    if links:
        out["links"] = [dict(r) for r in links]      # files stay on this computer; links travel
    if p.children[x]:
        out["items"] = [_item(conn, p, c) for c in p.children[x]]
    return out


def export_block(conn, block_id: int) -> dict:
    """One project (or any block, as a project of its own with its project's phases)."""
    p = Project.for_block(conn, block_id)
    if p is None:
        raise LookupError("Block not found")
    root = _item(conn, p, block_id)
    root.pop("status", None)
    root.pop("rules", None)          # a project has no rules of its own; a block exported alone loses its outside ones
    inside = {block_id, *p.descendants(block_id)}

    def keep_inside(item):           # rules to blocks outside what's exported can't come along
        if "rules" in item:
            item["rules"] = [r for r in item["rules"] if r["when"] in inside] or None
            if item["rules"] is None:
                del item["rules"]
        for c in item.get("items", []):
            keep_inside(c)
    keep_inside(root)
    return {"nestwork": FORMAT, "exported_at": datetime.now().isoformat(timespec="seconds"),
            "projects": [{**root, "stages": p.stage_list, "archived": bool(p.blocks[p.root_id].get("archived_at"))
                          and block_id == p.root_id}]}


def export_all(conn) -> dict:
    roots = [r["id"] for r in conn.execute("SELECT id FROM blocks WHERE parent_id IS NULL ORDER BY id")]
    return {"nestwork": FORMAT, "exported_at": datetime.now().isoformat(timespec="seconds"),
            "projects": [export_block(conn, r)["projects"][0] for r in roots]}


# --- import ------------------------------------------------------------------------
def _text(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def import_data(conn, data: dict) -> list[int]:
    """Add every project in an export as a new project. Returns the new project ids (not yet settled)."""
    from .main import clean_tags, insert_block, insert_rules
    from . import repeats
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ValueError("That isn't a Nestwork export (no “projects” list).")
    if int(data.get("nestwork") or 0) > FORMAT:
        raise ValueError("That export comes from a newer Nestwork. Update this one first.")
    new_ids = []
    for proj in data["projects"]:
        if not isinstance(proj, dict) or not _text(proj.get("title"), 200):
            raise ValueError("Every project needs a title.")
        stages = proj.get("stages")
        if not (isinstance(stages, list) and 2 <= len(stages) <= 10 and all(isinstance(s, dict) and s.get("key") for s in stages)):
            stages = stages_from_labels(["To do", "Doing", "Done"])
        stages = [{"key": str(s["key"])[:40], "label": _text(s.get("label") or s["key"], 40),
                   "color": s.get("color"), "role": s.get("role")} for s in stages]
        keys = [s["key"] for s in stages]
        refs: dict = {}
        pending_rules: list[tuple[int, list]] = []

        def extra(item):
            out = {}
            for f in FIELDS:
                v = item.get(f)
                if v is None:
                    continue
                if f == "notes":
                    out[f] = _text(v, 20000)
                elif f in ("due_date", "due_time"):
                    out[f] = _text(v, 10) or None
                elif f == "remind" and isinstance(v, list):
                    out[f] = json.dumps([str(c) for c in v])
                elif f == "repeat" and isinstance(v, dict):
                    try:
                        rule = repeats.normalize(v)
                    except (ValueError, TypeError):
                        rule = None
                    out[f] = json.dumps(rule) if rule else None
                elif f == "tags" and isinstance(v, list):
                    out[f] = json.dumps(clean_tags(v)) if clean_tags(v) else None
                elif f in ("pomodoros", "estimate_min", "priority") and isinstance(v, (int, float)):
                    out[f] = max(0, min(int(v), {"pomodoros": 24, "priority": 3}.get(f, 100000)))
            t = item.get("track")
            if isinstance(t, dict) and isinstance(t.get("total"), int) and 0 < t["total"] <= 1_000_000:
                done = t.get("done") if isinstance(t.get("done"), int) else 0
                out.update(track_total=t["total"], track_done=max(0, min(done, t["total"])),
                           track_unit=_text(t.get("unit"), 24))
            return out

        def add(item, parent):
            if not isinstance(item, dict) or not _text(item.get("title"), 200):
                raise ValueError("Every to-do needs a title.")
            status = item.get("status") if item.get("status") in keys else keys[0]
            bid = insert_block(conn, parent, _text(item["title"], 200), status, **extra(item))
            if "ref" in item:
                refs[item["ref"]] = bid
            for n in item.get("log") or []:
                if isinstance(n, dict) and n.get("text"):
                    conn.execute("INSERT INTO block_notes (block_id, kind, author, author_name, text, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 (bid, n.get("kind") if n.get("kind") in ("note", "event") else "note",
                                  n.get("author") if n.get("author") in ("you", "agent") else "you",
                                  n.get("author_name"), _text(n["text"], 10000),
                                  _text(n.get("created_at"), 19) or datetime.now().isoformat(timespec="seconds")))
            for link in item.get("links") or []:
                if isinstance(link, dict) and link.get("url"):
                    try:
                        attachments.add_link(conn, bid, str(link["url"]), str(link.get("title") or ""))
                    except ValueError:
                        pass
            if item.get("rules"):
                pending_rules.append((bid, item["rules"]))
            for c in item.get("items") or []:
                add(c, bid)
            return bid

        root_fields = {k: v for k, v in extra(proj).items() if k in ("notes", "due_date", "due_time", "priority", "tags")}
        root = insert_block(conn, None, _text(proj["title"], 200), keys[0], stage_config=json.dumps(stages),
                            archived_at=datetime.now().isoformat(timespec="seconds") if proj.get("archived") else None,
                            **root_fields)
        for item in proj.get("items") or []:
            add(item, root)
        for bid, rules in pending_rules:
            insert_rules(conn, bid, [(refs[r["when"]], r["reaches"], r["move_to"]) for r in rules
                                     if isinstance(r, dict) and r.get("when") in refs
                                     and r.get("reaches") in keys[1:] and r.get("move_to") in keys[1:]])
        if Project(conn, root).has_deadlock():
            conn.execute("DELETE FROM rules WHERE block_id IN (SELECT id FROM blocks WHERE id IN (%s))"
                         % ",".join(str(i) for i in refs.values() or [0]))
        new_ids.append(root)
    return new_ids


# --- Markdown outline ----------------------------------------------------------------
LIST = re.compile(r"^(?P<indent>[ \t]*)(?:[-*+]|\d+[.)])\s+(?:\[(?P<box>[ xX])\]\s+)?(?P<text>.+?)\s*$")
HEAD = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")


def parse_outline(text: str) -> tuple[str | None, list[dict]]:
    """(title from a lone top heading or None, nested items [{title, done, notes, items}])."""
    lines = [l for l in text.splitlines()]
    first_i = next((i for i, l in enumerate(lines) if l.strip()), 0)
    first = lines[first_i] if lines else ""
    heads = [len(m.group("hashes")) for l in lines if (m := HEAD.match(l))]
    title = None
    if HEAD.match(first) and heads.count(min(heads)) == 1 and len(HEAD.match(first).group("hashes")) == min(heads):
        title = HEAD.match(first).group("text")
    root: dict = {"items": [], "level": (-1, -1), "heading": True}
    stack = [root]
    heading_level = -1                # the level of the heading the list items sit under
    in_code = False
    for n, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip() or (title and n == first_i):
            continue
        m = HEAD.match(line)
        if m:
            heading_level = len(m.group("hashes"))
            item = {"title": m.group("text"), "done": False, "level": (heading_level, -1), "heading": True}
        elif (m := LIST.match(line)):
            indent = len(m.group("indent").replace("\t", "    ")) // 2
            item = {"title": m.group("text"), "done": (m.group("box") or " ").lower() == "x",
                    "level": (heading_level, indent), "heading": False}
        else:
            if stack[-1] is not root:
                stack[-1]["notes"].append(line.strip())
            continue
        item.update(notes=[], items=[])
        # Close everything at this level or deeper; a heading never sits inside a list item.
        while stack[-1] is not root and (stack[-1]["level"] >= item["level"] or (item["heading"] and not stack[-1]["heading"])):
            stack.pop()
        stack[-1]["items"].append(item)
        stack.append(item)

    def clean(items):
        return [{"title": i["title"][:200], "done": i["done"], "notes": "\n".join(i["notes"]), "items": clean(i["items"])}
                for i in items]
    return title, clean(root["items"])


def import_outline(conn, text: str, parent_id: int | None = None, title: str | None = None) -> tuple[int, int]:
    """Add an outline as a new project (parent_id None) or inside a block. Returns (block id, to-dos added)."""
    from .main import insert_block
    found, items = parse_outline(text)
    if not items:
        raise ValueError("Nothing to import: write a list (- item) or headings (# Part).")
    if parent_id is None:
        stages = stages_from_labels(["To do", "Doing", "Done"])
        target = insert_block(conn, None, (title or found or "Imported outline").strip()[:200], stages[0]["key"],
                              stage_config=json.dumps(stages))
    else:
        target = parent_id
    p = Project.for_block(conn, target)
    first, last = p.stages[0], p.stages[-1]
    count = 0

    def add(items, parent):
        nonlocal count
        for i in items:
            bid = insert_block(conn, parent, i["title"], last if i["done"] and not i["items"] else first, notes=i["notes"])
            count += 1
            add(i["items"], bid)
    add(items, target)
    return target, count
