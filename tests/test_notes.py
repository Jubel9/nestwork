"""Notes & activity on a block: written by you or the agent, moves logged, focus sessions shown."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        c.pid = c.post("/api/blocks", json={"title": "Thesis", "template": "blank", "stages": ["To do", "Doing", "Done"]}).json()["block"]["id"]
        c.tid = c.post("/api/blocks", json={"title": "Chapter 1", "parent_id": c.pid}).json()["block"]["id"]
        yield c


def test_write_edit_delete_and_count(api):
    n = api.post(f"/api/blocks/{api.tid}/notes", json={"text": "Outline agreed with my professor"}).json()
    assert (n["kind"], n["author"]) == ("note", "you")
    assert api.patch(f"/api/notes/{n['id']}", json={"text": "Outline agreed"}).json()["edited_at"]
    cards = api.get(f"/api/blocks/{api.pid}").json()["cards"]
    assert cards[str(api.tid)]["notes_count"] == 1
    api.delete(f"/api/notes/{n['id']}")
    assert api.get(f"/api/blocks/{api.tid}/notes").json() == []
    assert api.post(f"/api/blocks/{api.tid}/notes", json={"text": "  "}).status_code == 422


def test_moves_and_focus_show_in_the_timeline(api):
    stages = api.get(f"/api/blocks/{api.pid}").json()["stages"]
    api.post(f"/api/blocks/{api.tid}/move", json={"status": stages[1]["key"]})
    s = api.post("/api/focus/start", json={"block_id": api.tid, "planned_min": 25}).json()
    api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True})
    items = api.get(f"/api/blocks/{api.tid}/notes").json()
    kinds = [i["kind"] for i in items]
    assert "event" in kinds and "focus" in kinds
    ev = next(i for i in items if i["kind"] == "event")
    assert ev["text"] == "Moved from “To do” to “Doing”" and ev["author"] == "you"
    # activity can't be edited, and a note count ignores activity
    assert api.patch(f"/api/notes/{ev['id']}", json={"text": "x"}).status_code == 422
    assert api.get(f"/api/blocks/{api.pid}").json()["cards"][str(api.tid)]["notes_count"] == 0


def test_agent_writes_and_reads_notes(api):
    from tests.test_mcp import call
    api.put("/api/settings", json={"agent_access": "full"})
    assert "added to “Chapter 1”" in call("add_note", block_id=api.tid, text="Drafted the intro, 900 words.")
    call("move", block_id=api.tid, phase="Doing")
    items = api.get(f"/api/blocks/{api.tid}/notes").json()
    assert {(i["kind"], i["author"]) for i in items} == {("note", "agent"), ("event", "agent")}
    out = call("get_block", block_id=api.tid)
    assert "Notes & activity" in out and "Drafted the intro" in out and "Moved from" in out
    api.put("/api/settings", json={"agent_access": "read_only"})
    assert "read only" in call("add_note", block_id=api.tid, text="nope")
