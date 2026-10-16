"""Rules for nested blocks: phases, progress roll-up, rules, automatic moves and loop detection.

Phases
------
Every project has its own ordered list of phases (at least two). The first phase is where new
and waiting items sit; the last phase means "done". Two optional roles can be given to phases in
between:
  * "active": a block with items inside moves here when work starts inside it;
  * "review": a block with items inside can't reach this phase until everything inside is done,
    and moves here automatically once it is. Without a review phase, the same applies to the
    last phase (the block completes itself when everything inside is done).

Rules
-----
A rule reads "<block> moves to <target phase> when <other block> reaches <required phase>".
Every rule is both:
  * a gate: the block can't be moved to the target phase (or further) until the rule is met;
  * an automation: once all rules up to the target phase are met, the block moves there by itself.

A block also can't leave the first phase while one of its parents is still waiting on a rule for
leaving the first phase.
"""

import json
import secrets
import sqlite3

from . import repeats

PALETTE = ["lavender", "baby", "peach", "lilac", "mint", "butter", "blush"]
ROLES = ("active", "review")
DEFAULT_STAGES = [
    {"key": "backlog", "label": "Backlog", "color": "lavender", "role": None},
    {"key": "ready", "label": "Ready for work", "color": "baby", "role": None},
    {"key": "in_progress", "label": "In progress", "color": "peach", "role": "active"},
    {"key": "review", "label": "In review", "color": "lilac", "role": "review"},
    {"key": "done", "label": "Complete & accepted", "color": "mint", "role": None},
]
DEFAULT_KEYS = [s["key"] for s in DEFAULT_STAGES]
FIRST = DEFAULT_KEYS[0]
READY_I = 1


def new_stage_key() -> str:
    return "s_" + secrets.token_hex(4)


def default_color(i: int, n: int) -> str:
    if i == 0:
        return "lavender"
    if i == n - 1:
        return "mint"
    return ["baby", "peach", "lilac", "butter", "blush"][(i - 1) % 5]


def stages_from_labels(labels: list[str]) -> list[dict]:
    """Phases typed by the user, with sensible colors and roles for their count."""
    n = len(labels)
    out = [{"key": new_stage_key(), "label": l, "color": default_color(i, n), "role": None}
           for i, l in enumerate(labels)]
    if n >= 3:
        out[1]["role"] = "active"
    if n >= 4:
        out[n - 2]["role"] = "review"
    return out


def stages_with_labels(custom: dict | None) -> list[dict]:
    """The default five phases, optionally renamed (older projects and templates)."""
    return [{**s, "label": (custom or {}).get(s["key"]) or s["label"]} for s in DEFAULT_STAGES]


def root_of(conn: sqlite3.Connection, block_id: int) -> int | None:
    row = conn.execute(
        """
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM blocks WHERE id = ?
            UNION ALL
            SELECT b.id, b.parent_id FROM blocks b JOIN up ON b.id = up.parent_id
        )
        SELECT id FROM up WHERE parent_id IS NULL
        """,
        (block_id,),
    ).fetchone()
    return row["id"] if row else None


def quote_list(titles: list[str], limit: int = 3) -> str:
    shown = [f"“{t}”" for t in titles[:limit]]
    if len(titles) > limit:
        shown.append(f"{len(titles) - limit} more")
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def load_stages(root_row: dict) -> list[dict]:
    if root_row.get("stage_config"):
        return json.loads(root_row["stage_config"])
    raw = root_row.get("stage_labels")
    return stages_with_labels(json.loads(raw) if raw else None)



DEFAULT_POMODOROS = 4      # to-dos made before new ones started at 0 (their pomodoros is NULL)
NEW_POMODOROS = 0          # new to-dos: no daily plan until you give them one


def daily_pomodoros(b) -> int:
    """How many focus sessions a day a to-do is planned for (0 = no daily plan)."""
    v = b["pomodoros"] if "pomodoros" in b.keys() else None
    return DEFAULT_POMODOROS if v is None else v

def track(b) -> dict | None:
    """A to-do's optional counter: {"done", "total", "unit"}, or None."""
    total = b["track_total"] if "track_total" in b.keys() else None
    if not total:
        return None
    return {"done": min(b["track_done"] or 0, total), "total": total, "unit": b["track_unit"] or ""}


class Project:
    """An in-memory snapshot of one project tree (a root block and everything under it)."""

    def __init__(self, conn: sqlite3.Connection, root_id: int):
        self.root_id = root_id
        rows = conn.execute(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT ? UNION ALL SELECT b.id FROM blocks b JOIN sub ON b.parent_id = sub.id
            )
            SELECT blocks.* FROM blocks JOIN sub ON blocks.id = sub.id
            """,
            (root_id,),
        ).fetchall()
        self.blocks = {r["id"]: dict(r) for r in rows}
        self.children: dict[int, list[int]] = {i: [] for i in self.blocks}
        for b in sorted(self.blocks.values(), key=lambda b: (b["position"], b["id"])):
            if b["parent_id"] in self.children:
                self.children[b["parent_id"]].append(b["id"])
        self.set_stages(load_stages(self.blocks[root_id]))
        self.rules: dict[int, list[dict]] = {i: [] for i in self.blocks}
        for r in conn.execute("SELECT * FROM rules ORDER BY block_id, target_stage, depends_on_id").fetchall():
            if r["block_id"] in self.blocks and r["depends_on_id"] in self.blocks \
                    and r["target_stage"] in self.idx and r["required_stage"] in self.idx:
                self.rules[r["block_id"]].append({
                    "dep": r["depends_on_id"], "required": r["required_stage"], "target": r["target_stage"],
                })

    def set_stages(self, stages: list[dict]) -> None:
        self.stage_list = stages
        self.stages = [s["key"] for s in stages]
        self.idx = {k: i for i, k in enumerate(self.stages)}
        self.last = len(stages) - 1
        roles = {s.get("role"): i for i, s in enumerate(stages) if 0 < i < self.last and s.get("role")}
        self.active_i = roles.get("active")
        self.review_i = roles.get("review", self.last)

    @classmethod
    def for_block(cls, conn: sqlite3.Connection, block_id: int) -> "Project | None":
        root = root_of(conn, block_id)
        return cls(conn, root) if root is not None else None

    # --- structure -------------------------------------------------------
    def stages_out(self) -> list[dict]:
        return [{"key": s["key"], "label": s["label"], "color": s.get("color") or default_color(i, len(self.stages)),
                 "role": "start" if i == 0 else "done" if i == self.last else s.get("role")}
                for i, s in enumerate(self.stage_list)]

    def label(self, stage: str | int) -> str:
        i = stage if isinstance(stage, int) else self.idx[stage]
        return self.stage_list[i]["label"]

    def title(self, block_id: int) -> str:
        return self.blocks[block_id]["title"]

    def ancestors(self, block_id: int) -> list[int]:
        """Parents from the nearest up to the root."""
        out, cur = [], self.blocks[block_id]["parent_id"]
        while cur is not None and cur in self.blocks:
            out.append(cur)
            cur = self.blocks[cur]["parent_id"]
        return out

    def descendants(self, block_id: int) -> list[int]:
        out, stack = [], list(self.children[block_id])
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(self.children[cur])
        return out

    def family(self, block_id: int) -> set[int]:
        return set(self.ancestors(block_id)) | set(self.descendants(block_id)) | {block_id}

    def path(self, block_id: int) -> list[str]:
        ids = list(reversed(self.ancestors(block_id))) + [block_id]
        return [self.blocks[i]["title"] for i in ids]

    def stage(self, block_id: int) -> int:
        return self.idx.get(self.blocks[block_id]["status"], 0)

    def is_done(self, block_id: int) -> bool:
        return self.stage(block_id) == self.last

    # --- progress ---------------------------------------------------------
    def progress(self, block_id: int) -> float:
        """Share of the block that is finished, rolled up through every nesting level (the average of what's
        inside). A single to-do counts by its counter when it has one (37 of 200 videos = 18.5%), otherwise
        by how far right its phase is (with five phases: 0, 25, 50, 75, 100%)."""
        if self.is_done(block_id):
            return 1.0
        kids = self.children[block_id]
        if kids:
            return sum(self.progress(k) for k in kids) / len(kids)
        if t := track(self.blocks[block_id]):
            return min(t["done"] / t["total"], 1.0)
        return self.stage(block_id) / self.last

    def leaf_counts(self, block_id: int) -> tuple[int, int]:
        """(done, total) over the smallest tasks nested anywhere inside the block."""
        leaves = [d for d in self.descendants(block_id) if not self.children[d]]
        return sum(1 for d in leaves if self.is_done(d)), len(leaves)

    # --- rules ------------------------------------------------------------
    def unmet(self, block_id: int, stage_i: int) -> list[dict]:
        """Reasons the block can't be in phase `stage_i` (only the conditions attached to that phase)."""
        out = []
        for r in self.rules[block_id]:
            if self.idx[r["target"]] == stage_i and self.stage(r["dep"]) < self.idx[r["required"]]:
                out.append({"kind": "rule", "id": r["dep"], "title": self.title(r["dep"]),
                            "required": r["required"],
                            "text": f"“{self.title(r['dep'])}” reaches “{self.label(r['required'])}”"})
        if stage_i == READY_I:
            for anc in self.ancestors(block_id):
                if anc == self.root_id:
                    continue
                for r in self.rules[anc]:
                    if self.idx[r["target"]] == READY_I and self.stage(r["dep"]) < self.idx[r["required"]]:
                        out.append({"kind": "inherited", "id": r["dep"], "title": self.title(r["dep"]),
                                    "required": r["required"], "via_id": anc, "via_title": self.title(anc),
                                    "text": f"“{self.title(r['dep'])}” reaches “{self.label(r['required'])}” "
                                            f"(needed by “{self.title(anc)}”)"})
        # A block that holds to-dos is a bubble around them: it can't be ahead of its slowest one
        # (once everything inside is complete it may go all the way).
        kids = self.children[block_id]
        if kids and not all(self.is_done(c) for c in kids):
            behind = [c for c in kids if self.stage(c) < stage_i]
            if behind:
                out.append({"kind": "children", "ids": behind,
                            "text": f"everything inside reaches “{self.label(stage_i)}” ({len(behind)} left: "
                                    f"{quote_list([self.title(c) for c in behind])})"})
        return out

    def allowed_max(self, block_id: int) -> int:
        """The furthest phase the block may be in right now."""
        best = 0
        for s in range(1, self.last + 1):
            if self.unmet(block_id, s):
                break
            best = s
        return best

    def work_started_inside(self, block_id: int) -> bool:
        return self.active_i is not None and any(self.stage(c) >= self.active_i for c in self.children[block_id])

    def container_floor(self, block_id: int) -> int:
        """Where a block's to-dos put it: the phase of its slowest to-do. When everything inside is
        complete it waits in the review phase for acceptance (or completes itself without one)."""
        kids = self.children[block_id]
        if not kids:
            return 0
        if all(self.is_done(c) for c in kids):
            return self.review_i
        return min(self.stage(c) for c in kids)

    def container_cap(self, block_id: int) -> int:
        """The furthest phase its to-dos allow (ignoring the block's own rules)."""
        best = 0
        for s in range(1, self.last + 1):
            if any(u["kind"] == "children" for u in self.unmet(block_id, s)):
                break
            best = s
        return best

    def auto_min(self, block_id: int, allowed: int | None = None) -> int:
        """The phase the block's rules and to-dos push it to automatically (it can't be moved below this)."""
        allowed = self.allowed_max(block_id) if allowed is None else allowed
        targets = {self.idx[r["target"]] for r in self.rules[block_id]}
        best = max([t for t in targets if t <= allowed], default=0)
        floor = self.container_floor(block_id)
        return max(best, floor) if floor <= allowed else best

    def start_blockers(self, block_id: int) -> list[dict]:
        """What stops it from starting: its own and its parents' rules (not its to-dos still waiting)."""
        return [u for u in self.unmet(block_id, READY_I) if u["kind"] != "children"]

    def is_locked(self, block_id: int) -> bool:
        return block_id != self.root_id and bool(self.start_blockers(block_id))

    def push_reason(self, block_id: int, stage_i: int) -> str:
        kids = self.children[block_id]
        if kids and stage_i == self.container_floor(block_id):
            if all(self.is_done(c) for c in kids):
                return "everything inside is complete"
            return f"everything inside reached “{self.label(stage_i)}”"
        deps = [self.title(r["dep"]) for r in self.rules[block_id] if self.idx[r["target"]] == stage_i]
        return f"{quote_list(deps)} reached the needed phase" if deps else "its rules are met"

    def settle(self) -> list[dict]:
        """Apply every automatic move until nothing changes. Returns the changes made."""
        changes: dict[int, dict] = {}
        for _ in range(len(self.blocks) * len(self.stages) + 1):
            moved = False
            for x in self.blocks:
                if x == self.root_id:
                    continue
                cur = self.stage(x)
                new, reason = cur, None
                if self.children[x] and cur > self.container_cap(x):
                    new, reason = self.container_floor(x), "something inside it moved back"
                floor = self.auto_min(x)
                if floor > new:
                    new, reason = floor, self.push_reason(x, floor)
                if new != cur:
                    entry = changes.setdefault(x, {"id": x, "old": cur})
                    entry.update(new=new, reason=reason)
                    self.blocks[x]["status"] = self.stages[new]
                    moved = True
            if not moved:
                break
        return [c for c in changes.values() if c["old"] != c["new"]]

    def warnings(self) -> dict[int, list[str]]:
        """Blocks sitting further than their rules allow (e.g. after something they rely on was reopened)."""
        out = {}
        for x in self.blocks:
            reasons = self.warning_reasons(x)
            if reasons:
                out[x] = reasons
        return out

    def warning_reasons(self, block_id: int, allowed: int | None = None) -> list[str]:
        if block_id == self.root_id:
            return []
        allowed = self.allowed_max(block_id) if allowed is None else allowed
        return [u["text"] for s in range(allowed + 1, self.stage(block_id) + 1) for u in self.unmet(block_id, s)]

    def has_deadlock(self, override: dict[int, list[dict]] | None = None) -> bool:
        """Detect rules that could never all be met.

        Nodes: ("start", x) = x's own and its parents' start rules are met;
               ("may", x, s) = x may be in phase s.
          start(x) needs (dep, required) for x's start rules, and start(parent).
          may(x, 1) needs start(x); may(x, s) needs may(x, s-1) and (dep, required) for rules targeting s;
          a block with to-dos needs may(child, s) for every child.
        A cycle means some block could never get there.
        """
        rules = {**self.rules, **(override or {})}

        def prereqs(node):
            if node[0] == "start":
                x = node[1]
                out = [("may", r["dep"], self.idx[r["required"]]) for r in rules.get(x, []) if self.idx[r["target"]] == READY_I]
                parent = self.blocks[x]["parent_id"]
                if parent is not None and parent != self.root_id:
                    out.append(("start", parent))
                return out
            _, x, s = node
            out = [("may", x, s - 1)] if s > READY_I else [("start", x)]
            out += [("may", r["dep"], self.idx[r["required"]]) for r in rules.get(x, [])
                    if self.idx[r["target"]] == s and s > READY_I]
            out += [("may", c, s) for c in self.children[x]]
            return out

        WHITE, GREY, BLACK = 0, 1, 2
        color: dict = {}
        for start in [("may", x, s) for x in self.blocks for s in range(READY_I, self.last + 1)]:
            if color.get(start, WHITE) != WHITE:
                continue
            color[start] = GREY
            stack = [(start, iter(prereqs(start)))]
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    color[node] = BLACK
                    stack.pop()
                elif color.get(nxt, WHITE) == GREY:
                    return True
                elif color.get(nxt, WHITE) == WHITE:
                    color[nxt] = GREY
                    stack.append((nxt, iter(prereqs(nxt))))
        return False

    # --- serialization ----------------------------------------------------
    def automation(self, block_id: int, builtin: bool = True) -> list[str]:
        """Plain-language list of what moves this block automatically."""
        out = []
        groups: dict[tuple[str, str], list[str]] = {}
        for r in self.rules[block_id]:
            groups.setdefault((r["target"], r["required"]), []).append(self.title(r["dep"]))
        for (target, required), titles in sorted(groups.items(), key=lambda g: self.idx[g[0][0]]):
            verb = "reaches" if len(titles) == 1 else "reach"
            out.append(f"Moves to “{self.label(target)}” when {quote_list(titles, 5)} {verb} “{self.label(required)}”")
        if builtin and self.children[block_id]:
            out.append("Follows its slowest to-do: it's in the phase everything inside has reached")
            out.append(f"Waits in “{self.label(self.review_i)}” for your OK when everything inside is complete"
                       if self.review_i != self.last else
                       f"Moves to “{self.label(self.last)}” when everything inside is complete")
        return out

    def gate_info(self, block_id: int) -> dict:
        allowed, cur = self.allowed_max(block_id), self.stage(block_id)
        nxt = max(allowed, cur) + 1
        gate = None
        if nxt <= self.last:
            reasons = self.unmet(block_id, nxt)
            if reasons:
                gate = {"stage": self.stages[nxt], "reasons": [u["text"] for u in reasons]}
        blocked = self.start_blockers(block_id) if allowed == 0 else []
        warns = self.warning_reasons(block_id, allowed)
        return {
            "allowed_max": self.stages[allowed],
            "auto_min": self.stages[self.auto_min(block_id, allowed)],
            "gate": gate,
            "locked": block_id != self.root_id and bool(blocked),
            "blocked_by": blocked,
            "warning": bool(warns),
            "warning_reasons": warns,
        }

    def card(self, block_id: int) -> dict:
        b = self.blocks[block_id]
        kids = self.children[block_id]
        leaves_done, leaves_total = self.leaf_counts(block_id)
        return {
            "id": block_id,
            "parent_id": b["parent_id"],
            "title": b["title"],
            "notes": b["notes"],
            "status": self.stages[self.stage(block_id)],
            "done": self.is_done(block_id),
            "position": b["position"],
            "due_date": b["due_date"],
            "due_time": b.get("due_time"),
            "remind": json.loads(b["remind"]) if b.get("remind") else None,
            "pomodoros": daily_pomodoros(b),
            "track": track(b),
            "estimate_min": b.get("estimate_min"),
            "priority": b.get("priority") or 0,
            "tags": json.loads(b["tags"]) if b.get("tags") else [],
            "archived_at": b.get("archived_at"),
            **repeats.card_fields(b),
            "progress": round(self.progress(block_id), 4),
            "children_total": len(kids),
            "children_done": sum(1 for k in kids if self.is_done(k)),
            "leaves_total": leaves_total,
            "leaves_done": leaves_done,
            "rules": [
                {"depends_on_id": r["dep"], "title": self.title(r["dep"]), "required_stage": r["required"],
                 "target_stage": r["target"], "met": self.stage(r["dep"]) >= self.idx[r["required"]]}
                for r in self.rules[block_id]
            ],
            "automation": self.automation(block_id),
            **self.gate_info(block_id),
        }

    def tree(self, block_id: int) -> dict:
        b = self.blocks[block_id]
        info = self.gate_info(block_id)
        leave_first = self.stages[READY_I]
        return {
            "id": block_id,
            "title": b["title"],
            "status": self.stages[self.stage(block_id)],
            "done": self.is_done(block_id),
            "due_date": b["due_date"],
            "due_time": b.get("due_time"),
            "repeats": bool(b.get("repeat")),
            "priority": b.get("priority") or 0,
            "tags": json.loads(b["tags"]) if b.get("tags") else [],
            "progress": round(self.progress(block_id), 4),
            "locked": info["locked"],
            "blocked_by": [u["text"] for u in info["blocked_by"]],
            "gate": info["gate"],
            "warning": info["warning"],
            "allowed_max": info["allowed_max"],
            "auto_min": info["auto_min"],
            # Siblings this block waits on before starting, so the UI can draw it inside them as a chain.
            "after": sorted({r["dep"] for r in self.rules[block_id]
                             if r["target"] == leave_first and self.blocks[r["dep"]]["parent_id"] == b["parent_id"]}),
            "children": [self.tree(c) for c in self.children[block_id]],
        }
