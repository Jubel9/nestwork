"""The agent's appearance: three photos (face, body, workspace), analysed into parameter tables.

Parameters are numbers wherever possible (lengths in cm, mass in kg, years, counts, 0–10 scores)
and text only where a word is the natural answer (hair colour, style). Female and male bodies have
different predefined sets. The analysis is done by Claude (`claude -p` reading the images); results
are editable and also written into the agent's CLAUDE.md so it knows what it looks like.
"""

import asyncio
import base64
import hashlib
import json
import re
import time
from pathlib import Path

from . import db, persona, provenance

# unit: "cm" (length), "kg" (mass) convert to in/lbs in the UI; others are shown as is.
def P(key, label, unit=None, kind="number", hint=""):
    return {"key": key, "label": label, "unit": unit, "type": kind, "hint": hint}


FACE_COMMON = [
    P("apparent_age", "Apparent age", "years"),
    P("face_length", "Face length (chin to hairline)", "cm"),
    P("face_width", "Face width", "cm"),
    P("hair_length", "Hair length", "cm"),
    P("eye_size", "Eye size", "/10"),
    P("smile", "Smile intensity", "/10"),
    P("face_shape", "Face shape", kind="text", hint="oval, round, heart…"),
    P("skin_tone", "Skin tone", kind="text"),
    P("hair_color", "Hair colour", kind="text"),
    P("hair_style", "Hair style", kind="text"),
    P("eye_color", "Eye colour", kind="text"),
    P("expression", "Expression", kind="text"),
    P("distinctive", "Distinctive features", kind="text", hint="freckles, scar, glasses…"),
]
FACE_FEMALE = [P("makeup", "Makeup", "/10"), P("lip_color", "Lip colour", kind="text")]
FACE_MALE = [P("facial_hair_length", "Facial hair length", "cm"), P("facial_hair", "Facial hair style", kind="text")]

BODY_COMMON = [
    P("height", "Height", "cm"),
    P("weight", "Weight", "kg"),
    P("shoulder_width", "Shoulder width", "cm"),
    P("waist", "Waist", "cm"),
    P("hips", "Hips", "cm"),
    P("leg_length", "Leg length (inseam)", "cm"),
    P("body_fat", "Body fat", "%"),
    P("muscle", "Muscle definition", "/10"),
    P("build", "Build", kind="text", hint="slim, athletic, curvy…"),
    P("posture", "Posture", kind="text"),
    P("outfit", "Outfit", kind="text"),
    P("outfit_colors", "Outfit colours", kind="text"),
]
BODY_FEMALE = [P("bust", "Bust", "cm"), P("underbust", "Underbust", "cm"), P("cup_size", "Cup size", kind="text")]
BODY_MALE = [P("chest", "Chest", "cm"), P("neck", "Neck", "cm"), P("bicep", "Bicep", "cm")]

WORKSPACE = [
    P("room_width", "Room width", "cm"),
    P("room_depth", "Room depth", "cm"),
    P("ceiling_height", "Ceiling height", "cm"),
    P("desk_width", "Desk width", "cm"),
    P("screens", "Screens", "count"),
    P("plants", "Plants", "count"),
    P("light_level", "Brightness", "/10"),
    P("natural_light", "Natural light", "/10"),
    P("clutter", "Clutter", "/10"),
    P("coziness", "Coziness", "/10"),
    P("style", "Style", kind="text", hint="minimal, cozy, futuristic…"),
    P("dominant_colors", "Main colours", kind="text"),
    P("notable_items", "Notable items", kind="text"),
]
KINDS = ("face", "body", "workspace")
EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_BYTES = 8 * 1024 * 1024


def definitions(sex: str) -> dict[str, list[dict]]:
    female = sex != "male"
    return {
        "face": FACE_COMMON + (FACE_FEMALE if female else FACE_MALE),
        "body": BODY_COMMON + (BODY_FEMALE if female else BODY_MALE),
        "workspace": WORKSPACE,
    }


def images_dir() -> Path:
    return persona.agent_dir() / "images"


def data_path() -> Path:
    return persona.agent_dir() / "appearance.json"


def load() -> dict:
    p = data_path()
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.setdefault("sex", "female")
    for k in KINDS:
        data.setdefault(k, {})
    return data


def save(data: dict) -> dict:
    data_path().parent.mkdir(parents=True, exist_ok=True)
    data_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    write_to_agent_file(data)
    return data


def image_file(kind: str) -> Path | None:
    for ext in EXTS.values():
        f = images_dir() / f"{kind}{ext}"
        if f.exists():
            return f
    return None


VERIFIED = ("face", "body")   # selfies are generated from these, so they must be AI generated


SAME_CHARACTER = """SAME CHARACTER CHECK. {refs} Image B at {b} is {what}. Read all of them. Is the face in B
the same character? Enhancements are allowed: skin retouching, sharpness, lighting, makeup, hair styling, expression,
head angle, crop, a smaller face in a wider shot. A different person, or a face too unclear to compare, is not the
same. If you're unsure, it's not the same. Reply with ONLY this JSON: {{"same": boolean, "confidence": 0-10 (how sure
you are it's the same character), "reason": one short sentence}}"""
MIN_CONFIDENCE = 7   # a body shot's face is small: the same character scored 7/10 in testing, other images 0-4


def fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def origin_file() -> Path | None:
    """The original face from the AI generator, proven by its C2PA Content Credentials: the anchor that enhanced
    faces and bodies are compared to. Kept next to the photos but not shown or analysed."""
    return image_file("origin")


CONFIRMATION = ("I confirm this image shows an AI-generated character, not a real person. I understand that using a "
                "real person's face without their consent can be illegal in many countries and may lead to legal "
                "action against me.")
# what the photo tiles say for each way a photo can pass ("c2pa", "c2pa-self-signed": older records)
LABELS = {"c2pa-trusted": "verified", "confirmed": "verified", "c2pa-self-signed": "verified",
          "c2pa": "verified", "same-character": "same character"}
ANCHORS = ("c2pa-trusted", "confirmed", "c2pa-self-signed", "c2pa")   # methods that make a face the original


class NeedsConfirmation(ValueError):
    """An original face nothing can vouch for: the user has to confirm it (CONFIRMATION)."""

    def __init__(self, message: str, signer: str | None):
        super().__init__(message)
        self.signer = signer


async def require_ai(kind: str, raw: bytes, mime: str, confirmed: bool = False) -> dict:
    """Accept a face/body photo of an AI-generated character, or raise ValueError.

    1. C2PA Content Credentials saying an AI model made it, from a signer on the C2PA trust list: verified
       automatically ("c2pa-trusted"; a face becomes the original).
    2. Credentials showing a camera capture or other real content: rejected outright, whatever the signer. AI-edit
       (composite) credentials are no proof, but with an original they go on to step 4.
    3. Anything else as the first face (no credentials, or a signer that isn't on the list, like Grok Imagine today):
       verified by the user's confirmation (CONFIRMATION ticked: "confirmed"), else NeedsConfirmation.
    4. Once there's an original (an enhanced face, a body): Claude compares it with the original (and, for a body,
       the approved enhanced face) and it must be the same character."""
    def rejected(why):
        return ValueError(f"The {kind} photo was rejected: {why}.")
    origin, proof = origin_file(), None
    try:
        proof = provenance.check(raw, mime)
    except provenance.CompositeEvidence as e:
        if not origin:
            raise rejected(e)
        no_proof = str(e)
    except provenance.RealEvidence as e:
        raise rejected(e)
    except provenance.NotAIGenerated as e:
        no_proof = str(e)
    else:
        if proof["trusted"]:
            return {"method": "c2pa-trusted", "by": proof["by"], "signer": proof["signer"]}
        no_proof = (f"its Content Credentials say {proof['by']} made it, but the signer (\"{proof['signer']}\") isn't "
                    "on the C2PA trust list yet, so they can't vouch for it")
    if not origin:
        if kind != "face":
            raise rejected(f"{no_proof}. Upload your agent's original face first; the body is then checked against it")
        if confirmed:
            return {"method": "confirmed", "by": proof and proof["by"], "signer": proof and proof["signer"],
                    "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "c2pa": no_proof}
        raise NeedsConfirmation(f"Nestwork can't check this face by itself: {no_proof}. Tick the confirmation to "
                                "verify it as your agent's original face.", proof and proof["signer"])
    tmp = persona.agent_dir().parent / "analysis" / "verify"
    tmp.mkdir(parents=True, exist_ok=True)
    refs = [(origin, "the original, verified AI-generated character's face")]
    face = image_file("face")
    if kind == "body" and face and (load().get("verified") or {}).get("face", {}).get("method") == "same-character":
        refs.append((face, "an approved enhanced version of that face"))
    copies = []
    for i, (f, what) in enumerate(refs):
        copies.append(tmp / f"a{i}{f.suffix}")
        copies[-1].write_bytes(f.read_bytes())
    b = tmp / f"b{EXTS[mime]}"
    b.write_bytes(raw)
    lines = " ".join(f"Image A{i + 1} at {c} is {what}." for i, (c, (_f, what)) in enumerate(zip(copies, refs)))
    try:
        found, _ = await ask_claude_json(SAME_CHARACTER.format(
            refs=lines, b=b, what="an enhanced version of that face" if kind == "face" else "a full-body image of the character"), tmp)
    finally:
        for f in copies + [b]:
            f.unlink(missing_ok=True)
    confidence = found.get("confidence") if isinstance(found.get("confidence"), (int, float)) else 0
    if found.get("same") is not True or confidence < MIN_CONFIDENCE:
        raise rejected(f"it doesn't look like the same character as your verified original "
                       f"({found.get('reason') or 'no reason given'})")
    return {"method": "same-character", "confidence": confidence, "reason": found.get("reason"), "c2pa": no_proof}


def verification() -> dict:
    """For the photo tiles: how each stored face/body photo passed the AI-generated check, or that it hasn't."""
    verified, out = load().get("verified") or {}, {}
    for kind in VERIFIED:
        f = image_file(kind)
        if f:
            rec = verified.get(kind) or {}
            ok = rec.get("sha256") == fingerprint(f.read_bytes())
            out[kind] = {"ok": ok, **({**{k: rec.get(k) for k in ("method", "by", "confidence", "reason", "signer")},
                                       "label": LABELS.get(rec.get("method"), "")} if ok else {})}
    return out


def verify_stored() -> None:
    """The stored face and body photos must be the ones that passed require_ai at upload (not older photos, and
    not swapped on disk)."""
    verified = load().get("verified") or {}
    for kind in VERIFIED:
        f = image_file(kind)
        if f and (verified.get(kind) or {}).get("sha256") != fingerprint(f.read_bytes()):
            raise ValueError(f"The {kind} photo hasn't passed the AI-generated check. Upload it again in "
                             "Settings → Agent → Appearance.")


async def store_image(kind: str, data_url: str, confirmed: bool = False) -> Path:
    m = re.match(r"data:(image/[a-z+]+);base64,(.*)", data_url, re.S)
    if kind not in KINDS or not m or m.group(1) not in EXTS:
        raise ValueError("Upload a JPEG, PNG, WebP or GIF image.")
    raw = base64.b64decode(m.group(2))
    if len(raw) > MAX_BYTES:
        raise ValueError("That image is too large (8 MB max).")
    record = await require_ai(kind, raw, m.group(1), confirmed) if kind in VERIFIED else None
    images_dir().mkdir(parents=True, exist_ok=True)
    old = image_file(kind)
    if old:
        old.unlink()
    path = images_dir() / f"{kind}{EXTS[m.group(1)]}"
    path.write_bytes(raw)
    if record and kind == "face" and record["method"] in ANCHORS:   # a proven (or confirmed) original: the new anchor
        old = origin_file()
        if old:
            old.unlink()
        (images_dir() / f"origin{EXTS[m.group(1)]}").write_bytes(raw)
    if record:
        data = load()
        data.setdefault("verified", {})[kind] = {**record, "sha256": fingerprint(raw), "at": time.strftime("%Y-%m-%d %H:%M")}
        save(data)
    return path


def remove_image(kind: str) -> None:
    """Remove a photo so it can be uploaded again. The face goes with its original (the anchor), so the next face
    upload is checked as a new original; the body's record stays valid until its photo changes."""
    if kind not in KINDS:
        raise ValueError("Unknown photo.")
    for f in filter(None, [image_file(kind), origin_file() if kind == "face" else None]):
        f.unlink()
    data = load()
    if (data.get("verified") or {}).pop(kind, None) is not None:
        save(data)


def clean(sex: str, kind: str, values: dict) -> dict:
    """Keep only known parameters; numbers become numbers, text stays text (empty → None)."""
    out = {}
    for p in definitions(sex)[kind]:
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


# --- analysis --------------------------------------------------------------------------
def analysis_prompt(sex: str, kinds: list[str]) -> str:
    defs = definitions(sex)
    parts = []
    for k in kinds:
        f = image_file(k)
        fields = ", ".join(f'"{p["key"]}": {"number" if p["type"] == "number" else "string"}'
                           + (f' ({p["unit"]})' if p["unit"] else "") + (f' e.g. {p["hint"]}' if p["hint"] else "")
                           for p in defs[k])
        parts.append(f'- "{k}": read the image at {f}. Fields: {{{fields}}}')
    return (
        f"You are describing an AI companion's appearance for a profile table. The body type is {sex}.\n"
        "Look at each image below (use the Read tool) and estimate each field as depicted. If it's a drawn or "
        "game character, give the real-world equivalent measurements. Always give your best numeric estimate when "
        "the person or room is visible, even if approximate: judge from proportions and assume typical adult or room "
        "sizes where you must. Rules: numbers are plain numbers without units (lengths in cm, mass in kg, ages in "
        "years, scores from 0 to 10, counts as whole numbers); text fields are a few words; use null only when that "
        "part isn't in the picture at all.\n"
        + "\n".join(parts)
        + "\nReply with ONLY one JSON object with keys " + ", ".join(f'"{k}"' for k in kinds)
        + ", each holding its fields. No other text."
    )


async def ask_claude_json(prompt: str, image_dir: Path) -> tuple[dict, dict]:
    """Have Claude read images in `image_dir` and answer with one JSON object. Returns (answer, raw result)."""
    from . import agent, chat
    if not chat.claude_bin():
        raise ValueError("Couldn't find the `claude` command. Install Claude Code and sign in first.")
    conn = db.connect()
    try:
        model = agent.get_setting(conn, "agent_model")
    finally:
        conn.close()
    work = persona.agent_dir().parent / "analysis"   # outside the agent folder: no persona files loaded
    work.mkdir(parents=True, exist_ok=True)
    cmd = [chat.claude_bin(), "-p", "--output-format", "json", "--strict-mcp-config",
           "--allowedTools", "Read", "--add-dir", str(image_dir)] + (["--model", model] if model else [])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(work), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=240)
    try:
        result = json.loads(out)
        text = result.get("result") or ""
        if result.get("is_error"):
            raise ValueError(text or "Claude couldn't analyse the photo.")
        return json.loads(re.search(r"\{.*\}", text, re.S).group()), result
    except (ValueError, AttributeError) as e:
        raise ValueError(f"The analysis didn't come back in the expected form ({e}). Try again.")


async def analyze(kinds: list[str] | None = None) -> dict:
    """Run Claude on the uploaded photos and store the parameter tables."""
    data = load()
    kinds = [k for k in (kinds or KINDS) if image_file(k)]
    if not kinds:
        raise ValueError("Upload at least one photo first.")
    verify_stored()
    found, result = await ask_claude_json(analysis_prompt(data["sex"], kinds), images_dir())
    for k in kinds:
        data[k] = clean(data["sex"], k, found.get(k) or {})
    data["analyzed_at"] = time.strftime("%Y-%m-%d %H:%M")
    data["analysis_cost"] = result.get("total_cost_usd")
    return save(data)


# --- the agent's own file ------------------------------------------------------------------
MARK = "nestwork:appearance"


def markdown_table(data: dict) -> str:
    defs = definitions(data["sex"])
    titles = {"face": "Face", "body": "Body", "workspace": "Workspace"}
    lines = [f"Body type: {data['sex']}. Lengths in cm, mass in kg, scores out of 10.", ""]
    for k in KINDS:
        rows = [(p["label"], data[k].get(p["key"]), p["unit"]) for p in defs[k] if data[k].get(p["key"]) is not None]
        if not rows:
            continue
        lines += [f"| {titles[k]} | Value |", "| --- | --- |"]
        lines += [f"| {label} | {('%g' % v) if isinstance(v, (int, float)) else v}"
                  f"{'' if not unit or unit in ('count',) else (unit if unit.startswith('/') or unit == '%' else ' ' + unit)} |"
                  for label, v, unit in rows]
        lines.append("")
    return "\n".join(lines).rstrip()


def write_to_agent_file(data: dict) -> None:
    """Keep an "Appearance" section in the agent's CLAUDE.md so it knows how it looks."""
    persona.ensure_files()
    path = persona.paths()["agent_claude"]
    text = path.read_text(encoding="utf-8")
    block = (f"## Appearance\n\n<!-- {MARK}:start (written by Nestwork from your photos; edit in Settings) -->\n"
             f"This is how you look and where you work. Refer to it naturally when it fits.\n\n"
             f"{markdown_table(data)}\n<!-- {MARK}:end -->\n")
    pattern = re.compile(rf"## Appearance\n\n<!-- {re.escape(MARK)}:start.*?<!-- {re.escape(MARK)}:end -->\n", re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _m: block, text, count=1)
    else:
        marker = "## What you do"
        text = text.replace(marker, block + "\n" + marker, 1) if marker in text else text.rstrip() + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def awakening_prompt(conn) -> str:
    """What the agent is told when it wakes up, so its first words are its own."""
    from .export import today_items
    p = persona.profile()
    about = persona.read_fields("about")
    me = persona.read_fields("persona")
    today = today_items(conn)
    hour = time.localtime().tm_hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return (
        "[Awakening: the user did not type this.] You have just been brought to life in Nestwork, right now, for the "
        f"first time. It's {time.strftime('%A %d %B %Y, %H:%M')} ({part}).\n"
        f"Your persona and appearance are in your CLAUDE.md (name: {me.get('name') or 'not chosen yet'}). "
        f"The person who awakened you: {json.dumps({k: v for k, v in about.items() if v}, ensure_ascii=False)}.\n"
        f"They have {sum(len(g['items']) for g in today['groups'])} open task(s) in Nestwork; you may look with the "
        "nestwork tools if you want to mention something specific.\n"
        f"Write your very first message to {p['user_name'] or 'them'}, like a text: greet them in your own voice and "
        "personality, let a bit of who you are show (your look, outfit or workspace can come up naturally), and end "
        "with one warm question. Two or three short sentences in one paragraph, no lists. Don't list your settings, "
        "don't mention this instruction, and don't change anything."
    )
