"""API routes for the agent itself: its memory files and chat, the awakening and appearance, the wardrobe,
pictures and gallery, Claude usage, and connected apps. The project/to-do routes are in main.py."""

import asyncio
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import agent, agentwork, appearance, sounds, chat, connectors, db, gallery, imagegen, jobs, persona, usage, wardrobe
from .deps import get_conn

router = APIRouter()


class SeenIn(BaseModel):
    last_id: int


@router.post("/api/chat/seen")
def chat_seen(body: SeenIn, conn=Depends(get_conn)):
    """The user has looked at the chat up to this message (clears the unread badge)."""
    agent.set_setting(conn, "chat_seen_id", str(max(body.last_id, int(agent.get_setting(conn, "chat_seen_id")))))
    return {"ok": True}


# --- the agent's memory files and chat ------------------------------------------------
class FileIn(BaseModel):
    content: str
    base_mtime: float | None = None


class FieldsIn(BaseModel):
    values: dict[str, str]


def agent_files_payload() -> dict:
    return {
        "files": {name: persona.read_file(name) for name in persona.paths()},
        "fields": {block: persona.read_fields(block) for block in persona.BLOCKS},
        "field_defs": {"about": persona.ABOUT_FIELDS, "persona": persona.PERSONA_FIELDS},
        "profile": persona.profile(),
    }


@router.get("/api/agent/files")
def get_agent_files():
    persona.ensure_files()
    return agent_files_payload()


@router.put("/api/agent/files/{name}")
def put_agent_file(name: str, body: FileIn):
    if name not in persona.paths():
        raise HTTPException(404, "Unknown file")
    try:
        persona.write_file(name, body.content, body.base_mtime)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return agent_files_payload()


@router.put("/api/agent/fields/{block}")
def put_agent_fields(block: str, body: FieldsIn):
    if block not in persona.BLOCKS:
        raise HTTPException(404, "Unknown section")
    persona.write_fields(block, body.values)
    return agent_files_payload()


@router.get("/api/agent/profile")
def get_agent_profile():
    conn = db.connect()
    try:
        awakened = agent.get_setting(conn, "awakened") == "1"
        avatar = gallery.avatar_name(conn)
    finally:
        conn.close()
    return {**persona.profile(), "claude_found": bool(chat.claude_bin()), "awakened": awakened, "avatar": avatar}


class ChatFileIn(BaseModel):
    name: str = Field(max_length=300)
    mime: str = Field(default="", max_length=200)
    data: str                    # base64, or a data: URL


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=20000)
    new_chat: bool = False
    awakening: bool = False      # the agent's very first message, written by the agent itself
    files: list[ChatFileIn] = Field(default=[], max_length=chat.MAX_FILES)


@router.post("/api/chat")
async def post_chat(body: ChatIn):
    """One chat turn, streamed as server-sent events (session, delta, tool, done, error)."""
    if body.awakening:
        conn = db.connect()
        try:
            prompt = appearance.awakening_prompt(conn)
        finally:
            conn.close()
        stream = chat.run(prompt, force_new=True, hidden=True)
    elif not body.message.strip() and not body.files:
        raise HTTPException(422, "Write a message first.")
    else:
        try:
            files = [chat.save_upload(f.name, f.data, f.mime) for f in body.files]
        except ValueError as e:
            raise HTTPException(422, str(e))
        stream = chat.run(body.message.strip(), body.new_chat, files=files)
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- awakening & appearance ---------------------------------------------------------------
def awakening_payload(conn) -> dict:
    data = appearance.load()
    stamp = lambda f: int(f.stat().st_mtime) if f else None   # noqa: E731 - cache-buster for <img>
    return {
        "awakened": agent.get_setting(conn, "awakened") == "1",
        "appearance": data,
        "definitions": {sex: appearance.definitions(sex) for sex in ("female", "male")},
        "images": {k: (f"/api/agent/images/{k}?v={stamp(appearance.image_file(k))}" if appearance.image_file(k) else None)
                   for k in appearance.KINDS},
        "verification": appearance.verification(),
        "analyzing": jobs.running("appearance"),
        "analysis_error": jobs.errors.get("appearance"),
    }


@router.get("/api/awakening")
def get_awakening(conn=Depends(get_conn)):
    persona.ensure_files()
    return awakening_payload(conn)


class ImageIn(BaseModel):
    data_url: str = Field(max_length=12_000_000)
    confirm_ai: bool = False   # the user ticked appearance.CONFIRMATION (for an original face nothing else can vouch for)


@router.post("/api/agent/images/{kind}")
async def upload_image(kind: str, body: ImageIn):
    try:   # no database connection held during the AI-generated check (a vision check can take ~20 s)
        await appearance.store_image(kind, body.data_url, body.confirm_ai)
    except appearance.NeedsConfirmation as e:
        raise HTTPException(409, {"message": str(e), "confirm": appearance.CONFIRMATION, "signer": e.signer})
    except ValueError as e:
        raise HTTPException(422, str(e))
    agent.after_change()
    conn = db.connect()
    try:
        return awakening_payload(conn)
    finally:
        conn.close()


@router.delete("/api/agent/images/{kind}")
def delete_image(kind: str):
    try:
        appearance.remove_image(kind)
    except ValueError as e:
        raise HTTPException(422, str(e))
    agent.after_change()
    conn = db.connect()
    try:
        return awakening_payload(conn)
    finally:
        conn.close()


@router.get("/api/agent/images/{kind}")
def get_image(kind: str):
    f = appearance.image_file(kind) if kind in appearance.KINDS else None
    if not f:
        raise HTTPException(404, "No image")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


class AppearanceIn(BaseModel):
    sex: str | None = None
    face: dict | None = None
    body: dict | None = None
    workspace: dict | None = None


@router.put("/api/agent/appearance")
def put_appearance(body: AppearanceIn, conn=Depends(get_conn)):
    data = appearance.load()
    if body.sex is not None:
        if body.sex not in ("female", "male"):
            raise HTTPException(422, "Body type must be female or male.")
        data["sex"] = body.sex
    for k in appearance.KINDS:
        values = getattr(body, k)
        if values is not None:
            data[k] = appearance.clean(data["sex"], k, {**data[k], **values})
    appearance.save(data)
    return awakening_payload(conn)


class AnalyzeIn(BaseModel):
    kinds: list[str] | None = None


@router.post("/api/agent/analyze")
async def analyze_photos(body: AnalyzeIn):
    """Start analysing the photos in the background; poll /api/awakening for "analyzing" and the result."""
    if not any(appearance.image_file(k) for k in (body.kinds or appearance.KINDS)):
        raise HTTPException(422, "Upload at least one photo first.")
    if not chat.claude_bin():
        raise HTTPException(422, "Couldn't find the `claude` command. Install Claude Code and sign in first.")
    jobs.start("appearance", appearance.analyze(body.kinds))
    conn = db.connect()
    try:
        return awakening_payload(conn)
    finally:
        conn.close()


# --- wardrobe ---------------------------------------------------------------------------
class ClothingIn(BaseModel):
    data_url: str = Field(max_length=12_000_000)


class ClothingPatch(BaseModel):
    worn: bool | None = None
    params: dict | None = None


class OutfitIn(BaseModel):
    item_ids: list[int]


class ModeIn(BaseModel):
    mode: str


def _wardrobe_call(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    agent.after_change()
    return wardrobe.payload()


@router.get("/api/agent/wardrobe")
def get_wardrobe():
    return wardrobe.payload()


@router.post("/api/agent/wardrobe")
async def add_clothing(body: ClothingIn):
    """Upload a clothing photo; its analysis starts in the background straight away."""
    try:
        item = wardrobe.add(body.data_url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    jobs.start(f"clothing:{item['id']}", wardrobe.analyze(item["id"]))
    agent.after_change()
    return wardrobe.payload()


@router.post("/api/agent/wardrobe/{item_id}/analyze")
async def reanalyze_clothing(item_id: int):
    try:
        wardrobe.find(wardrobe.load(), item_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    jobs.start(f"clothing:{item_id}", wardrobe.analyze(item_id))
    return wardrobe.payload()


@router.put("/api/agent/wardrobe/outfit")
def put_outfit(body: OutfitIn):
    return _wardrobe_call(wardrobe.set_outfit, body.item_ids, by="you")


@router.put("/api/agent/wardrobe/mode")
def put_wardrobe_mode(body: ModeIn):
    return _wardrobe_call(wardrobe.set_mode, body.mode)


@router.put("/api/agent/wardrobe/{item_id}")
def put_clothing(item_id: int, body: ClothingPatch):
    return _wardrobe_call(wardrobe.update, item_id, params=body.params, worn=body.worn, by="you")


@router.delete("/api/agent/wardrobe/{item_id}")
def delete_clothing(item_id: int):
    jobs.cancel(f"clothing:{item_id}")
    return _wardrobe_call(wardrobe.remove, item_id)


@router.get("/api/agent/wardrobe/{item_id}/image")
def clothing_image(item_id: int):
    try:
        f = wardrobe.image_path(wardrobe.find(wardrobe.load(), item_id))
    except ValueError:
        f = None
    if not f or not f.exists():
        raise HTTPException(404, "No image")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


# --- pictures -----------------------------------------------------------------------------
@router.get("/api/imagegen")
def imagegen_info():
    """Which providers have a key in .env, and model suggestions for the dropdowns."""
    return {"keys": imagegen.keys_status(), "providers": imagegen.PROVIDERS, "key_names": imagegen.KEY_NAMES,
            "suggested": imagegen.SUGGESTED, "env_file": str(imagegen.env_path())}


@router.get("/api/imagegen/models")
async def imagegen_models(provider: str):
    """Live image-model list where the provider offers one (OpenRouter); otherwise the suggestions."""
    if provider == "openrouter" and imagegen.api_key("openrouter"):
        try:
            async with imagegen.http_client(20) as client:
                r = await client.get(f"{imagegen.OPENROUTER}/images/models",
                                     headers={"Authorization": f"Bearer {imagegen.api_key('openrouter')}"})
            ids = sorted({m.get("id") for m in r.json().get("data", []) if m.get("id")})
            if ids:
                return {"models": ids, "live": True}
        except Exception:
            pass
    return {"models": imagegen.SUGGESTED.get(provider, []), "live": False}


@router.get("/api/agent/gallery")
def agent_gallery(conn=Depends(get_conn)):
    """Every picture the agent generated, newest first, with the chat message it came with."""
    return {"items": gallery.items(conn), "avatar": gallery.avatar_name(conn)}


@router.get("/api/agent/avatar")
def agent_avatar(conn=Depends(get_conn)):
    """The agent's profile picture: the chosen gallery picture, or the face photo."""
    f = gallery.avatar_file(conn)
    if not f or not f.is_file():
        raise HTTPException(404, "No profile picture")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


class AvatarIn(BaseModel):
    name: str | None = None


@router.put("/api/agent/avatar")
def put_agent_avatar(body: AvatarIn, conn=Depends(get_conn)):
    try:
        name = gallery.set_avatar(conn, body.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"avatar": name}


@router.get("/api/generated/{name}")
def generated_image(name: str):
    f = imagegen.generated_dir() / Path(name).name
    if not f.exists():
        raise HTTPException(404, "No image")
    return FileResponse(f, headers={"Cache-Control": "max-age=31536000, immutable"})


@router.post("/api/awaken")
def awaken(conn=Depends(get_conn)):
    persona.ensure_files()
    appearance.write_to_agent_file(appearance.load())
    agent.set_setting(conn, "awakened", "1")
    return {"ok": True}


@router.post("/api/chat/stop")
def stop_chat():
    return {"stopped": chat.stop()}


@router.get("/api/chat/files")
def chat_files(conn=Depends(get_conn)):
    """Files you sent in the chat (the gallery's Files tab), newest first, with their category."""
    return {"items": chat.uploads(conn), "kinds": [{"key": k, "label": label} for k, label in chat.FILE_KINDS]}


@router.get("/api/chat/files/{fid}")
def chat_file(fid: str):
    """A file sent with a chat message (images and PDFs open in the browser)."""
    path = chat.upload_path(fid)
    if path is None:
        raise HTTPException(404, "That file is gone.")
    name = fid.split("_", 1)[1]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # Only kinds that can't run scripts open in the browser; anything else (HTML, SVG, …) downloads.
    inline = mime.startswith("image/") and mime != "image/svg+xml" or mime in ("application/pdf", "text/plain")
    return FileResponse(path, media_type=mime, filename=name, content_disposition_type="inline" if inline else "attachment")


@router.get("/api/chat/history")
def chat_history(limit: int = 200, before: int | None = None, after: int | None = None, conn=Depends(get_conn)):
    return chat.history(conn, max(1, min(limit, 200)), before, after)


@router.get("/api/claude/usage")
async def claude_usage(refresh: bool = False):
    """The Claude subscription's usage limits (5-hour session, week), from Claude Code's /usage."""
    return await usage.read(force=refresh)


# --- connected apps (claude.ai connectors the agent may use) ----------------------------
class ConnectorsIn(BaseModel):
    enabled: list[str]


@router.get("/api/connectors")
def get_connectors(conn=Depends(get_conn)):
    return connectors.payload(conn)


@router.post("/api/connectors/check")
async def check_connectors():
    try:
        return await connectors.probe()
    except (RuntimeError, asyncio.TimeoutError) as e:
        raise HTTPException(503, str(e) or "Claude Code took too long to answer.")


@router.post("/api/connectors/sign-in")
def connectors_sign_in():
    """Open Claude Code's app list in Terminal (macOS) so the user can sign in to a connector."""
    return {"opened": connectors.open_sign_in(), "command": connectors.SIGN_IN_COMMAND}


@router.put("/api/connectors")
def put_connectors(body: ConnectorsIn, conn=Depends(get_conn)):
    known = {s["name"] for s in connectors.seen(conn)["servers"]} | set(connectors.enabled(conn))
    wanted = [n for n in dict.fromkeys(body.enabled) if n in known]
    agent.set_setting(conn, "connectors", json.dumps(wanted))
    return connectors.payload(conn)


# --- Agent's Work: tasks the agent runs on a schedule (agentwork.py) ---------------------------
class AgentJobIn(BaseModel):
    name: str = Field(max_length=120)
    prompt: str = Field(max_length=8000)
    schedule: str = Field(max_length=200)
    deliver_chat: bool = True
    notify: str = Field("", max_length=80)        # "" default, "none" silent, or a notification id


class AgentJobPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    prompt: str | None = Field(None, max_length=8000)
    schedule: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    deliver_chat: bool | None = None
    notify: str | None = Field(None, max_length=80)


def _job_or_404(conn, job_id: int) -> dict:
    try:
        return agentwork.get(conn, job_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/api/agent-work")
def agent_work(conn=Depends(get_conn)):
    """The active tasks with their next and last run, the archive, and the latest runs of all of them."""
    return {"jobs": agentwork.jobs(conn), "archived": agentwork.jobs(conn, archived=True),
            "runs": agentwork.runs(conn, limit=40)}


@router.get("/api/agent-work/preview")
def agent_work_preview(schedule: str):
    """A schedule in words and its next runs, for the editor."""
    try:
        return {"schedule": agentwork.normalize(schedule), "describe": agentwork.describe(schedule),
                "next": [t.strftime(agentwork.FMT) for t in agentwork.upcoming_runs(schedule, 3)]}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/agent-work", status_code=201)
def add_agent_job(body: AgentJobIn, conn=Depends(get_conn)):
    try:
        job_id = agentwork.add(conn, body.name, body.prompt, body.schedule, "you", body.deliver_chat, notify=body.notify)
    except ValueError as e:
        raise HTTPException(400, str(e))
    conn.commit()
    return agentwork.get(conn, job_id)


@router.patch("/api/agent-work/{job_id}")
def patch_agent_job(job_id: int, body: AgentJobPatch, conn=Depends(get_conn)):
    _job_or_404(conn, job_id)
    try:
        job = agentwork.update(conn, job_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    conn.commit()
    return job


@router.delete("/api/agent-work/{job_id}")
def delete_agent_job(job_id: int, forever: bool = False, conn=Depends(get_conn)):
    """Moves the task to the archive; forever=true deletes it and its history for good."""
    _job_or_404(conn, job_id)
    name = agentwork.delete(conn, job_id) if forever else agentwork.archive(conn, job_id)
    conn.commit()
    return {"ok": True, "name": name, "archived": not forever}


@router.post("/api/agent-work/{job_id}/restore")
def restore_agent_job(job_id: int, conn=Depends(get_conn)):
    _job_or_404(conn, job_id)
    job = agentwork.restore(conn, job_id)
    conn.commit()
    return job


@router.get("/api/agent-work/{job_id}/runs")
def agent_job_runs(job_id: int, limit: int = 50, conn=Depends(get_conn)):
    _job_or_404(conn, job_id)
    return agentwork.runs(conn, job_id, min(limit, 200))


@router.post("/api/agent-work/{job_id}/run", status_code=202)
async def run_agent_job(job_id: int):
    """Run a task now, off its schedule (its schedule doesn't change)."""
    conn = db.connect()
    try:
        _job_or_404(conn, job_id)
    finally:
        conn.close()
    if not agentwork.start(job_id, "manual"):
        raise HTTPException(409, "It's already running.")
    return {"ok": True}


@router.post("/api/agent-work/{job_id}/stop")
def stop_agent_job(job_id: int):
    return {"ok": agentwork.stop(job_id)}


# --- notification sounds (sounds.py; played by static/sounds.js) ----------------------------
class SoundUploadIn(BaseModel):
    name: str = Field(max_length=200)
    data: str                                    # base64 (a data: URL is fine)
    mime: str = Field("", max_length=100)


class SoundRenameIn(BaseModel):
    name: str = Field(max_length=60)


class NotificationIn(BaseModel):
    name: str = Field(max_length=60)
    sound: str = Field(max_length=40)
    volume: float = Field(0.8, ge=0, le=1)


class NotificationPatch(BaseModel):
    name: str | None = Field(None, max_length=60)
    sound: str | None = Field(None, max_length=40)
    volume: float | None = Field(None, ge=0, le=1)


@router.get("/api/sounds")
def get_sounds(conn=Depends(get_conn)):
    """Preset sounds, your uploaded sounds and your notifications (the built-in ones first)."""
    return sounds.library(conn)


@router.post("/api/sounds/claim/{message_id}")
def claim_sound(message_id: int):
    """Whether this page should play the sound for a message: only the first page to ask does."""
    return {"play": sounds.claim(message_id)}


@router.post("/api/sounds/files", status_code=201)
def upload_sound(body: SoundUploadIn, conn=Depends(get_conn)):
    try:
        return sounds.save_file(conn, body.name, body.data, body.mime)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/sounds/files/{fid}")
def sound_file(fid: int, conn=Depends(get_conn)):
    path = sounds.file_path(conn, fid)
    if path is None:
        raise HTTPException(404, "No such sound")
    mime = conn.execute("SELECT mime FROM sound_files WHERE id = ?", (fid,)).fetchone()["mime"]
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "max-age=86400"})


@router.patch("/api/sounds/files/{fid}")
def rename_sound(fid: int, body: SoundRenameIn, conn=Depends(get_conn)):
    try:
        sounds.rename_file(conn, fid, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/api/sounds/files/{fid}")
def delete_sound(fid: int, conn=Depends(get_conn)):
    try:
        return {"ok": True, "changed": sounds.delete_file(conn, fid)}
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/api/notifications", status_code=201)
def add_notification(body: NotificationIn, conn=Depends(get_conn)):
    try:
        nid = sounds.add_notification(conn, body.name, body.sound, body.volume)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return sounds.get(conn, nid)


@router.patch("/api/notifications/{nid}")
def patch_notification(nid: int, body: NotificationPatch, conn=Depends(get_conn)):
    try:
        return sounds.update_notification(conn, nid, body.name, body.sound, body.volume)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/notifications/{nid}")
def delete_notification(nid: int, conn=Depends(get_conn)):
    try:
        return {"ok": True, "name": sounds.delete_notification(conn, nid)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
