"""Danger zone: erase only what was picked, only with the phrase, always with a backup to restore."""

import pytest
from fastapi.testclient import TestClient

from nestwork import persona


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Game", "template": "game"}).json()["block"]["id"]
        c.post(f"/api/blocks/{pid}/notes", json={"text": "A note"})
        s = c.post("/api/focus/start", json={"project_id": pid, "planned_min": 25}).json()
        c.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True})
        c.put("/api/settings", json={"agent_access": "full"})
        persona.ensure_files()
        persona.write_fields("persona", {"name": "Zoe"})
        persona.write_fields("about", {"name": "Alex"})
        yield c


def test_needs_the_phrase_and_a_choice(api):
    assert api.post("/api/reset", json={"parts": ["projects"], "confirm": "yes"}).status_code == 422
    assert api.post("/api/reset", json={"parts": [], "confirm": "start over"}).status_code == 422
    assert api.get("/api/reset").json()["projects"] == 1


def test_erase_picked_parts_only_then_restore(api):
    before = api.get("/api/reset").json()
    assert before["projects"] == 1 and before["notes"] == 1 and before["focus_sessions"] == 1 and before["agent_name"] == "Zoe"
    r = api.post("/api/reset", json={"parts": ["projects", "settings"], "confirm": "Start over"}).json()
    after = api.get("/api/reset").json()
    assert (after["projects"], after["todos"], after["notes"]) == (0, 0, 0)
    assert after["focus_sessions"] == 1 and after["agent_name"] == "Zoe"   # not picked: kept
    assert api.get("/api/settings").json()["agent_access"] == "ask"        # back to the default
    assert after["backups"][0]["name"] == r["backup"] and after["backups"][0]["projects"] == 1

    assert api.post("/api/reset/restore", json={"name": r["backup"], "confirm": "nope"}).status_code == 422
    api.post("/api/reset/restore", json={"name": r["backup"], "confirm": "restore"})
    back = api.get("/api/reset").json()
    assert back["projects"] == 1 and back["notes"] == 1 and back["agent_name"] == "Zoe"
    assert api.get("/api/settings").json()["agent_access"] == "full"
    assert len(back["backups"]) == 2                    # restoring took a backup of the empty state too


def test_shared_memory_only_when_asked(api):
    api.post("/api/reset", json={"parts": ["shared"], "confirm": "start over"})
    assert api.get("/api/reset").json()["user_name"] == ""
    assert api.post("/api/reset/restore", json={"name": "../x", "confirm": "restore"}).status_code == 404


def test_erasing_the_agent_erases_all_of_nestwork(api):
    r = api.post("/api/reset", json={"parts": ["agent"], "confirm": "start over"}).json()
    assert r["erased"] == ["projects", "focus", "chat", "agent", "settings"]      # never the shared memory
    after = api.get("/api/reset").json()
    assert (after["projects"], after["focus_sessions"], after["chat_messages"], after["agent_name"]) == (0, 0, 0, "")
    assert after["user_name"] == "Alex"
    settings = api.get("/api/settings").json()
    assert settings["awakened"] is False and settings["agent_access"] == "ask"


def _entry(text: str, hours: float) -> str:
    """A Claude Code transcript line stamped `hours` from now (UTC, as Claude Code writes it)."""
    import json
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({"type": "user", "timestamp": at, "message": {"content": text}}) + "\n"


def test_backups_carry_the_agents_transcripts(api):
    from nestwork import reset
    t = reset.transcripts_dir()
    assert t.parent == persona.shared_dir() / "projects" and "-agent" in t.name
    t.mkdir(parents=True)
    (t / "s1.jsonl").write_text(_entry("before the backup", -1))
    r = api.post("/api/reset", json={"parts": ["chat"], "confirm": "start over"}).json()
    assert (reset.backups_dir() / r["backup"] / "transcripts" / "s1.jsonl").exists()
    with (t / "s1.jsonl").open("a") as f:
        f.write(_entry("said after the backup", 0))
    (t / "s2.jsonl").write_text(_entry("a later chat", 0))
    api.post("/api/reset/restore", json={"name": r["backup"], "confirm": "restore"})
    text = (t / "s1.jsonl").read_text()
    assert "before the backup" in text and "said after the backup" not in text
    assert not (t / "s2.jsonl").exists()                       # the later conversation is gone
    safety = reset.list_backups()[0]["name"]                  # but kept in the backup taken before restoring
    assert "said after the backup" in (reset.backups_dir() / safety / "transcripts" / "s1.jsonl").read_text()


def test_an_older_backup_without_transcripts_cuts_them_back(api):
    import json
    import shutil
    from datetime import datetime, timedelta
    from nestwork import reset
    t = reset.transcripts_dir()
    t.mkdir(parents=True)
    r = api.post("/api/reset", json={"parts": ["chat"], "confirm": "start over"}).json()
    b = reset.backups_dir() / r["backup"]
    shutil.rmtree(b / "transcripts")                           # as backups made before transcripts were kept
    m = json.loads((b / "manifest.json").read_text())
    m["created_at"] = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    (b / "manifest.json").write_text(json.dumps(m))
    (t / "s1.jsonl").write_text(_entry("two hours ago", -2) + '{"type": "cost-state"}\n' + _entry("just now", 0))
    (t / "s1.jsonl").chmod(0o600)
    (t / "s2.jsonl").write_text(_entry("a chat after the backup", 0))
    (t / "s2").mkdir()
    api.post("/api/reset/restore", json={"name": r["backup"], "confirm": "restore"})
    text = (t / "s1.jsonl").read_text()
    assert "two hours ago" in text and "cost-state" in text and "just now" not in text
    assert (t / "s1.jsonl").stat().st_mode & 0o777 == 0o600
    assert not (t / "s2.jsonl").exists() and not (t / "s2").exists()
    safety = reset.backups_dir() / reset.list_backups()[0]["name"] / "transcripts"
    assert "just now" in (safety / "s1.jsonl").read_text() and (safety / "s2.jsonl").exists()


def test_restoring_marks_the_chat_to_start_fresh(api):
    from nestwork import agent, db
    r = api.post("/api/reset", json={"parts": ["chat"], "confirm": "start over"}).json()
    api.post("/api/reset/restore", json={"name": r["backup"], "confirm": "restore"})
    conn = db.connect()
    try:
        assert float(agent.get_setting(conn, "chat_fresh_since")) > 0
    finally:
        conn.close()
