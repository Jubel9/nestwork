"""The agent's wardrobe: clothes photos, analysed into parameters, and what it's wearing now.

Who picks the outfit is a setting: "user" (you choose in Settings) or "agent" (the agent may change
its own clothes through the MCP `change_outfit` tool, including no outfit at all). Either way you can
put items on or take them off in Settings. The current outfit is written into the agent's CLAUDE.md.
"""

import base64
import json
import re
import time
from pathlib import Path

from . import jobs, persona
from .appearance import EXTS, MAX_BYTES, P, ask_claude_json

PARAMS = [
    P("name", "Name", kind="text", hint="e.g. Lavender knit cardigan"),
    P("category", "Category", kind="text", hint="top, bottom, dress, outerwear, shoes, accessory…"),
    P("colors", "Colours", kind="text"),
    P("material", "Material", kind="text"),
    P("pattern", "Pattern", kind="text"),
    P("style", "Style", kind="text", hint="casual, elegant, sporty…"),
    P("length", "Garment length", "cm"),
    P("heel_height", "Heel height", "cm"),
    P("formality", "Formality", "/10"),
    P("warmth", "Warmth", "/10"),
    P("comfort", "Comfort", "/10"),
    P("season", "Season", kind="text"),
    P("occasion", "Occasion", kind="text"),
]
MODES = ("user", "agent")
MARK = "nestwork:wardrobe"


def folder() -> Path:
    return persona.agent_dir() / "wardrobe"


def data_path() -> Path:
    return persona.agent_dir() / "wardrobe.json"


def load() -> dict:
    p = data_path()
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.setdefault("mode", "user")
    data.setdefault("items", [])
    data.setdefault("next_id", 1)
    data.setdefault("changed_by", None)
    data.setdefault("changed_at", None)
    return data


def save(data: dict) -> dict:
    data_path().parent.mkdir(parents=True, exist_ok=True)
    data_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    write_to_agent_file(data)
    return data


def find(data: dict, item_id: int) -> dict:
    item = next((i for i in data["items"] if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"There's no clothing item #{item_id} in the wardrobe.")
    return item


def image_path(item: dict) -> Path:
    return folder() / item["file"]


def clean(values: dict) -> dict:
    out = {}
    for p in PARAMS:
        v = values.get(p["key"])
        if v in (None, "", "null"):
            out[p["key"]] = None
        elif p["type"] == "number":
            if isinstance(v, str):
                num = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", "."))
                v = float(num.group()) if num else None
            out[p["key"]] = None if v is None else round(float(v), 1)
        else:
            out[p["key"]] = str(v).strip()[:120]
    return out


def add(data_url: str) -> dict:
    """Store an uploaded clothing photo as a new (not yet analysed) item."""
    m = re.match(r"data:(image/[a-z+]+);base64,(.*)", data_url, re.S)
    if not m or m.group(1) not in EXTS:
        raise ValueError("Upload a JPEG, PNG, WebP or GIF image.")
    raw = base64.b64decode(m.group(2))
    if len(raw) > MAX_BYTES:
        raise ValueError("That image is too large (8 MB max).")
    data = load()
    item_id = data["next_id"]
    data["next_id"] += 1
    folder().mkdir(parents=True, exist_ok=True)
    name = f"{item_id}{EXTS[m.group(1)]}"
    (folder() / name).write_bytes(raw)
    item = {"id": item_id, "file": name, "worn": False, "analyzed": False, "added_at": time.strftime("%Y-%m-%d %H:%M"),
            "params": clean({})}
    data["items"].append(item)
    save(data)
    return item


async def analyze(item_id: int) -> dict:
    data = load()
    item = find(data, item_id)
    fields = ", ".join(f'"{p["key"]}": {"number" if p["type"] == "number" else "string"}'
                       + (f' ({p["unit"]})' if p["unit"] else "") + (f' e.g. {p["hint"]}' if p["hint"] else "")
                       for p in PARAMS)
    prompt = (
        f"Read the image at {image_path(item)} (use the Read tool). It shows a garment or accessory for an AI "
        "companion's wardrobe. Describe it with these fields: {" + fields + "}.\n"
        "Rules: give your best numeric estimate for numbers (lengths in cm measured on the garment, scores 0–10); "
        "heel_height only for shoes, otherwise null; `name` is a short, specific name; text fields are a few words. "
        "Reply with ONLY the JSON object."
    )
    found, result = await ask_claude_json(prompt, folder())
    data = load()   # reload: the user may have toggled something meanwhile
    item = find(data, item_id)
    item["params"] = clean(found)
    item["analyzed"] = True
    item["analysis_cost"] = result.get("total_cost_usd")
    save(data)
    return item


def update(item_id: int, params: dict | None = None, worn: bool | None = None, by: str = "you") -> dict:
    data = load()
    item = find(data, item_id)
    if params:
        item["params"] = clean({**item["params"], **params})
    if worn is not None and worn != item["worn"]:
        item["worn"] = worn
        data["changed_by"], data["changed_at"] = by, time.strftime("%Y-%m-%d %H:%M")
    return save(data)


def set_outfit(item_ids: list[int], by: str) -> dict:
    """Wear exactly these items ([] = nothing)."""
    data = load()
    for i in item_ids:
        find(data, i)
    for item in data["items"]:
        item["worn"] = item["id"] in item_ids
    data["changed_by"], data["changed_at"] = by, time.strftime("%Y-%m-%d %H:%M")
    return save(data)


def set_mode(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError("The outfit is picked by the user or the agent.")
    data = load()
    data["mode"] = mode
    return save(data)


def remove(item_id: int) -> dict:
    data = load()
    item = find(data, item_id)
    image_path(item).unlink(missing_ok=True)
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    return save(data)


def describe(item: dict) -> str:
    p = item["params"]
    bits = [p.get("colors"), p.get("material"), p.get("style")]
    nums = [f"formality {p['formality']:g}/10" if p.get("formality") is not None else None,
            f"warmth {p['warmth']:g}/10" if p.get("warmth") is not None else None]
    name = p.get("name") or f"item {item['id']}"
    return f"#{item['id']} {name} ({p.get('category') or 'clothing'}" + \
        "".join(f", {b}" for b in bits + nums if b) + ")"


def outfit_text(data: dict) -> str:
    worn = [i for i in data["items"] if i["worn"]]
    return "nothing" if not worn else "; ".join(describe(i) for i in worn)


def write_to_agent_file(data: dict) -> None:
    """Keep a "Wardrobe" section in the agent's CLAUDE.md: what it's wearing and what it could wear."""
    persona.ensure_files()
    path = persona.paths()["agent_claude"]
    text = path.read_text(encoding="utf-8")
    choose = ("You choose your own outfit. Change it with the nestwork `change_outfit` tool when you feel like it "
              "(e.g. for the time of day, the weather or the mood of the conversation); choosing no outfit is allowed. "
              "Mention a change naturally, don't make a fuss about it."
              if data["mode"] == "agent" else
              "The user picks your outfit in Settings; you can't change it, but you can say what you'd like to wear.")
    others = [describe(i) for i in data["items"] if not i["worn"]]
    block = (f"## Wardrobe\n\n<!-- {MARK}:start (written by Nestwork; change it in Settings → Agent → Wardrobe) -->\n"
             f"Currently wearing: {outfit_text(data)}.\n\n{choose}\n\n"
             + ("Also in your wardrobe:\n" + "\n".join(f"- {o}" for o in others) + "\n" if others else
                "Nothing else is in your wardrobe.\n")
             + f"<!-- {MARK}:end -->\n")
    pattern = re.compile(rf"## Wardrobe\n\n<!-- {re.escape(MARK)}:start.*?<!-- {re.escape(MARK)}:end -->\n", re.S)
    if not data["items"]:
        text = pattern.sub("", text).replace("\n\n\n", "\n\n")
    elif pattern.search(text):
        text = pattern.sub(lambda _m: block, text, count=1)
    else:
        marker = "## What you do"
        text = text.replace(marker, block + "\n" + marker, 1) if marker in text else text.rstrip() + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def payload() -> dict:
    data = load()
    return {
        "mode": data["mode"],
        "changed_by": data["changed_by"],
        "changed_at": data["changed_at"],
        "outfit": ", ".join(i["params"].get("name") or f"item {i['id']}" for i in data["items"] if i["worn"]) or "nothing",
        "definitions": PARAMS,
        "items": [{**i, "image": f"/api/agent/wardrobe/{i['id']}/image?v={int(image_path(i).stat().st_mtime)}"
                   if image_path(i).exists() else None,
                   "analyzing": jobs.running(f"clothing:{i['id']}"),
                   "analysis_error": jobs.errors.get(f"clothing:{i['id']}")} for i in data["items"]],
    }
