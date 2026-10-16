"""Pictures from the agent: drawings on request, and selfies.

Providers (chosen in Settings, separately for drawings and selfies):
* OpenRouter: POST /api/v1/images with `input_references` (base64 data URLs); returns base64.
* WaveSpeed: reference photos are uploaded first (POST /api/v3/media/uploads → PUT → download_url),
  then POST /api/v3/{model} with `images: [urls]`, then poll /api/v3/predictions/{id}/result.

API keys live in the project's .env file (OPENROUTER_API_KEY, WAVESPEED_API_KEY), read on each use
so editing .env needs no restart.

Selfies: the agent only writes its intention and emotion. Claude (the CLI) turns that, plus the
agent's personality, outfit, wardrobe, workspace and the time, into a short image prompt following
fixed rules. The face, body, worn clothes and workspace photos go along as reference images, and a
line per reference is appended in code.
"""

import asyncio
import base64
import json
import mimetypes
import os
import re
import time
from pathlib import Path

import httpx

from . import appearance, db, persona, wardrobe

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = {"openrouter": "OpenRouter", "wavespeed": "WaveSpeed"}
KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "wavespeed": "WAVESPEED_API_KEY"}
# Suggestions for the model dropdowns ("Other…" takes any model name the provider offers).
SUGGESTED = {
    "openrouter": ["google/gemini-2.5-flash-image", "google/gemini-3-pro-image-preview", "openai/gpt-image-1"],
    "wavespeed": ["alibaba/wan-2.7/image-edit", "alibaba/wan-2.7/image-edit-pro", "wavespeed-ai/qwen-image/edit-plus-lora",
                  "wavespeed-ai/flux-2-klein-base-9b/edit", "wavespeed-ai/z-image/turbo"],
}
OPENROUTER = "https://openrouter.ai/api/v1"
WAVESPEED = "https://api.wavespeed.ai/api/v3"

# Tests swap this for a mock transport.
def http_client(timeout: float = 180) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


class ImageError(Exception):
    pass


# --- keys ---------------------------------------------------------------------------------
def env_path() -> Path:
    return Path(os.environ.get("NESTWORK_ENV_FILE", ROOT / ".env"))


def api_key(provider: str) -> str | None:
    name = KEY_NAMES[provider]
    if os.environ.get(name):
        return os.environ[name]
    p = env_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"^\s*(?:export\s+)?{name}\s*=\s*(.*?)\s*$", line)
            if m:
                return m.group(1).strip().strip("'\"") or None
    return None


def keys_status() -> dict:
    return {p: bool(api_key(p)) for p in PROVIDERS}


# --- providers -----------------------------------------------------------------------------
def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


async def openrouter_generate(model: str, prompt: str, refs: list[Path], aspect: str) -> tuple[bytes, str]:
    key = api_key("openrouter")
    body = {"model": model, "prompt": prompt, "aspect_ratio": aspect, "n": 1}
    if refs:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(r)}} for r in refs]
    async with http_client() as client:
        r = await client.post(f"{OPENROUTER}/images", json=body, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise ImageError(f"OpenRouter said {r.status_code}: {_short(r.text)}")
    item = (r.json().get("data") or [{}])[0]
    if not item.get("b64_json"):
        raise ImageError("OpenRouter didn't return an image.")
    return base64.b64decode(item["b64_json"]), item.get("media_type") or "image/png"


async def wavespeed_upload(client: httpx.AsyncClient, key: str, path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    raw = path.read_bytes()
    r = await client.post(f"{WAVESPEED}/media/uploads", headers={"Authorization": f"Bearer {key}"},
                          json={"filename": path.name, "size": len(raw), "content_type": mime})
    ticket = r.json() if r.status_code == 200 else {}
    if ticket.get("code") != 200:
        raise ImageError(f"WaveSpeed upload failed: {_short(r.text)}")
    up = ticket["data"]["upload"]
    put = await client.put(up["url"], headers=up.get("headers") or {}, content=raw)
    if not 200 <= put.status_code < 300:
        raise ImageError(f"WaveSpeed upload failed ({put.status_code}).")
    return ticket["data"]["download_url"]


async def wavespeed_generate(model: str, prompt: str, refs: list[Path], aspect: str) -> tuple[bytes, str]:
    key = api_key("wavespeed")
    headers = {"Authorization": f"Bearer {key}"}
    async with http_client() as client:
        body = {"prompt": prompt}
        if refs:
            body["images"] = [await wavespeed_upload(client, key, p) for p in refs]
        r = await client.post(f"{WAVESPEED}/{model}", json=body, headers=headers)
        if r.status_code != 200:
            raise ImageError(f"WaveSpeed said {r.status_code}: {_short(r.text)}")
        pid = r.json()["data"]["id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            res = (await client.get(f"{WAVESPEED}/predictions/{pid}/result", headers=headers)).json().get("data", {})
            status = res.get("status")
            if status == "completed":
                out = (res.get("outputs") or [None])[0]
                if not out:
                    raise ImageError("WaveSpeed finished without an image.")
                img = await client.get(out)
                return img.content, img.headers.get("content-type", "image/png").split(";")[0]
            if status in ("failed", "cancelled", "timeout", "deleted"):
                raise ImageError(f"WaveSpeed {status}: {_short(res.get('error') or '')}")
    raise ImageError("WaveSpeed took too long.")


def _short(text: str) -> str:
    return re.sub(r"\s+", " ", str(text))[:300]


async def generate(kind: str, prompt: str, refs: list[Path], aspect: str) -> Path:
    """Generate with the provider/model set for `kind` ("image" or "selfie") and save the result."""
    from . import agent
    conn = db.connect()
    try:
        provider = agent.get_setting(conn, f"{kind}_provider")
        model = agent.get_setting(conn, f"{kind}_model")
    finally:
        conn.close()
    if provider not in PROVIDERS:
        raise ImageError("Choose an image provider in Settings → Agent → Pictures.")
    if not model:
        raise ImageError(f"Choose a {kind} model in Settings → Agent → Pictures.")
    if not api_key(provider):
        raise ImageError(f"Add {KEY_NAMES[provider]} to the .env file in the Nestwork folder.")
    fn = openrouter_generate if provider == "openrouter" else wavespeed_generate
    raw, mime = await fn(model, prompt, refs, aspect)
    out = generated_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}{mimetypes.guess_extension(mime) or '.png'}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return out


def generated_dir() -> Path:
    return persona.agent_dir() / "generated"


def marker(path: Path) -> str:
    """How a tool result tells the chat there's a picture to show (see chat.py)."""
    return f"[nestwork-image:/api/generated/{path.name}]"


# --- drawings ------------------------------------------------------------------------------------
async def draw(description: str, aspect: str = "1:1") -> Path:
    return await generate("image", description.strip(), [], aspect)


# --- selfies -------------------------------------------------------------------------------------
SELFIE_RULES = """The image model already has reference images for face/body/clothes/room. Never describe
those in words; only describe pose, hand actions, body actions, and expression.

STRUCTURE, one clause per part, in this order:
1. Pose + what the subject is interacting with (furniture indoors, nature outdoors). Avoid a generic selfie pose.
2. If any clothes are worn: "wearing this clothes" + once, only here: "resize the clothes to match the body reference, not the other way around." If no outfit is selected: skip this line completely and do not mention clothes.
3. The fixed phrase "Front camera selfie."
4. One hand (Left or Right) "stretching off screen", nothing else about that hand.
5. The other hand doing ONE concrete, specific action.
6. ONE expression phrase, 2-5 words (e.g. "shy smile", "mid-laugh"). Never stack descriptors (no mouth + eyes +
   eyebrows + chin all at once) and no mood backstory.

Hard rules:
- You assign BOTH hands yourself (steps 4 and 5). IGNORE any hand/arm/holding action in MY INTENTION; never carry it
  through, or the shot gets a third arm.
- Under 60 words total. This is the only length rule.
- No wall colours, furniture materials, fabric textures, hair detail or lighting: the reference images cover that.
- Never say "phone" or describe holding/using a device; only the fixed phrase "Front camera selfie."
- If only a top or only a bottom is worn, say so explicitly, so the model doesn't invent the missing piece.
- If an item is not visible in the picture, leave it out of clothes_to_show.
- "include_face": true unless the face is intentionally hidden. "include_room": false only for a plain white void.

TESTED EXAMPLE (match this register, don't quote it):
"Perched on the edge of the bed, one boot planted on the mattress, wearing this clothes, resize the clothes to match
the body reference, not the other way around. Right hand stretching off screen. Left hand tugging the boot strap
tight. Mouth caught mid-laugh."

Never write the "Image reference N is..." lines yourself; they are appended in code after your prompt.

Reply with ONLY this JSON: {"prompt": string, "include_face": boolean, "include_room": boolean,
"clothes_to_show": [wardrobe item ids that are visible]}"""


BODY_NO_OUTFIT_LABEL = "the body, no outfit selected: do not add any clothing"


def private_string(name: str) -> str:
    """The string constant `name` from data/agent/personal_preference.py (never committed), or "" when the file
    or the constant is missing or broken. Read with ast, never imported or run."""
    import ast
    path = persona.agent_dir() / "personal_preference.py"
    try:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return node.value.value.strip()
    except (OSError, SyntaxError, ValueError):
        pass
    return ""


def personal_preference() -> str:
    """Extra hard rules: the private file's SELFIE_RULES string, or "" when there is none."""
    return private_string("SELFIE_RULES")


def body_no_outfit_label() -> str:
    """The body reference label when no outfit is selected: the private file's BODY_NO_OUTFIT_LABEL, else the neutral one."""
    return private_string("BODY_NO_OUTFIT_LABEL") or BODY_NO_OUTFIT_LABEL


def selfie_rules() -> str:
    """SELFIE_RULES with the personal preferences added at the end of the hard rules, above the tested example."""
    extra = personal_preference()
    if not extra:
        return SELFIE_RULES
    head, sep, tail = SELFIE_RULES.partition("\n\nTESTED EXAMPLE")
    return f"{head}\n{extra}{sep}{tail}"


def anchor_identity() -> str:
    f = persona.read_fields("persona")
    parts = [f"name {f['name']}" if f.get("name") else "", f.get("personality", ""), f"tone: {f['tone']}" if f.get("tone") else ""]
    return "; ".join(p for p in parts if p) or "warm and friendly"


def room_description() -> str:
    """The workspace parameters as a short line (the room photo itself goes along as a reference)."""
    ws = appearance.load()["workspace"]
    parts = []
    for p in appearance.WORKSPACE:
        v = ws.get(p["key"])
        if v is None:
            continue
        if isinstance(v, (int, float)):
            parts.append(f"{p['label'].lower()} {v:g}{ {'cm': ' cm', '/10': '/10'}.get(p['unit'], '') }")
        else:
            parts.append(f"{p['label'].lower()}: {v}")
    return ", ".join(parts) or "their workspace (see the room reference)"


def selfie_input(intention: str, emotion: str) -> str:
    from .chat import now_stamp
    w = wardrobe.load()
    worn = [i for i in w["items"] if i["worn"]]
    return (f"MY INTENTION AND EMOTION: {intention}\n"
            f"MY PERSONALITY: {anchor_identity()}\n"
            f"WHAT I'M WEARING: {'; '.join(wardrobe.describe(i) for i in worn) or 'nothing'}\n"
            f"MY WARDROBE: {'; '.join(wardrobe.describe(i) for i in w['items']) or 'empty'}\n"
            f"WHERE I AM: {room_description()}\n"
            f"MY EMOTION: {emotion}\n"
            f"TIME: {now_stamp()}")


# Coverings the prompt writer tends to add despite the rules; they come out as broken anatomy.
COVERINGS = re.compile(r"\b(blankets?|sheets?|duvets?|quilts?|comforters?|towels?|robes?|bedsheets?|bedding)\b", re.I)


async def write_selfie_prompt(intention: str, emotion: str, note: str = "") -> dict:
    from . import agent, chat
    if not chat.claude_bin():
        raise ImageError("Couldn't find the `claude` command to write the selfie prompt.")
    conn = db.connect()
    try:
        model = agent.get_setting(conn, "selfie_prompt_model")
    finally:
        conn.close()
    work = persona.agent_dir().parent / "analysis"
    work.mkdir(parents=True, exist_ok=True)
    cmd = [chat.claude_bin(), "-p", "--output-format", "json", "--strict-mcp-config", "--allowedTools", "",
           "--append-system-prompt", selfie_rules()] + (["--model", model] if model else [])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(work), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate((selfie_input(intention, emotion) + note).encode()), timeout=120)
    try:
        text = json.loads(out).get("result") or ""
        found = json.loads(re.search(r"\{.*\}", text, re.S).group())
        assert isinstance(found.get("prompt"), str) and found["prompt"].strip()
    except Exception:
        raise ImageError("The selfie prompt didn't come back in the expected form. Try again.")
    return found


def references(plan: dict) -> tuple[list[Path], list[str]]:
    """Reference photos for the image model, and the line describing each one (appended to the prompt)."""
    w = wardrobe.load()
    worn = {i["id"]: i for i in w["items"] if i["worn"]}
    show = [i for i in plan.get("clothes_to_show") or [] if i in worn] or list(worn)
    refs, lines = [], []

    def add(path, what):
        if path and Path(path).exists():
            refs.append(Path(path))
            lines.append(f"Image reference {len(refs)} is {what}.")

    if plan.get("include_face", True):
        add(appearance.image_file("face"), "the face")
    # No outfit selected: say so in code, or the image model dresses the body in clothes of its own choosing.
    add(appearance.image_file("body"), "the body" if worn else body_no_outfit_label())
    for i in show:
        add(wardrobe.image_path(worn[i]), f"the clothes: {worn[i]['params'].get('name') or 'clothing'}")
    if plan.get("include_room", True):
        add(appearance.image_file("workspace"), "the room")
    return refs, lines


async def selfie(intention: str, emotion: str) -> tuple[Path, str]:
    if not (appearance.image_file("face") or appearance.image_file("body")):
        raise ImageError("There's no face or body photo yet. Add them in Settings → Agent → Appearance.")
    try:
        appearance.verify_stored()
    except ValueError as e:
        raise ImageError(f"No selfie. {e}")
    plan = await write_selfie_prompt(intention, emotion)
    for _ in range(2):   # rewrite if it covered the body anyway
        found = COVERINGS.findall(plan["prompt"])
        if not found:
            break
        plan = await write_selfie_prompt(intention, emotion, f"\nYOUR LAST PROMPT BROKE A HARD RULE by using "
                                         f"{', '.join(sorted(set(f.lower() for f in found)))}: nothing covers the body.")
    refs, lines = references(plan)
    prompt = plan["prompt"].strip() + ("\n" + "\n".join(lines) if lines else "")
    path = await generate("selfie", prompt, refs, "3:4")
    path.with_suffix(".txt").write_text(prompt + "\n", encoding="utf-8")   # the exact prompt, for checking later
    return path, prompt
