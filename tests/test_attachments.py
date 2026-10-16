"""Links and files on a block: add, open, remove, undo, trash, export, agent, orphan cleanup."""

import base64
import time

import pytest
from fastapi.testclient import TestClient

from nestwork import attachments, db


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Thesis"}).json()["block"]["id"]
        c.tid = c.post("/api/blocks", json={"title": "Read", "parent_id": pid}).json()["block"]["id"]
        c.pid = pid
        yield c


def test_links_and_files(api):
    t = api.tid
    link = api.post(f"/api/blocks/{t}/attachments", json={"url": "www.example.com/paper"}).json()
    assert link["url"] == "https://www.example.com/paper" and link["title"] == "example.com/paper"
    for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd"):
        assert api.post(f"/api/blocks/{t}/attachments", json={"url": bad}).status_code == 422
    assert api.post(f"/api/blocks/{t}/attachments", json={"url": "obsidian://open?vault=Uni"}).status_code == 201
    data = base64.b64encode(b"%PDF-1.4 hello").decode()
    f = api.post(f"/api/blocks/{t}/attachments", json={"name": "../notes v2.pdf", "data": data}).json()
    assert f["title"] == "notes v2.pdf" and f["mime"] == "application/pdf" and f["size"] == 14
    r = api.get(f["href"])
    assert r.content == b"%PDF-1.4 hello" and r.headers["content-disposition"].startswith("inline")
    assert api.get(f"/api/blocks/{api.pid}").json()["cards"][str(t)]["attachments_count"] == 3
    api.delete(f"/api/attachments/{f['id']}")
    assert len(api.get(f"/api/blocks/{t}/attachments").json()) == 2
    api.post("/api/undo")
    assert len(api.get(f"/api/blocks/{t}/attachments").json()) == 3                     # undo brings it back


def test_trash_export_agent_and_orphans(api):
    t = api.tid
    api.post(f"/api/blocks/{t}/attachments", json={"url": "https://a.org", "title": "Paper A"})
    f = api.post(f"/api/blocks/{t}/attachments", json={"name": "x.txt", "data": base64.b64encode(b"x").decode()}).json()
    trash_id = api.delete(f"/api/blocks/{t}").json()["trash_id"]
    api.post(f"/api/trash/{trash_id}/restore")
    assert {a["title"] for a in api.get(f"/api/blocks/{t}/attachments").json()} == {"Paper A", "x.txt"}
    data = api.get(f"/api/blocks/{api.pid}/export.json").json()
    new = api.post("/api/import", json={"data": data}).json()["projects"][0]
    copy = next(c for c in api.get(f"/api/blocks/{new}").json()["cards"].values() if c["title"] == "Read")
    assert [a["title"] for a in api.get(f"/api/blocks/{copy['id']}/attachments").json()] == ["Paper A"]

    from nestwork import agent
    from nestwork.mcp_server import add_link, get_block
    api.put("/api/settings", json={"agent_access": "read_only"})
    assert add_link(t, "https://b.org").startswith("Not done")
    api.put("/api/settings", json={"agent_access": "ask"})
    assert "added" in add_link(t, "https://b.org", "Paper B")
    assert "Paper B: https://b.org" in get_block(t)
    assert agent  # imported for the settings above

    api.delete(f"/api/attachments/{f['id']}")
    conn = db.connect()
    conn.execute("DELETE FROM undo_log")                                                 # nothing could bring it back
    later = time.time() + attachments.ORPHAN_GRACE + 10
    path = attachments.path_of(f)
    assert path.exists()
    assert attachments.clean_orphans(conn, later) == 1
    assert not path.exists()
    conn.close()


def test_files_live_in_their_projects_folder(api):
    data = base64.b64encode(b"hi").decode()
    f = api.post(f"/api/blocks/{api.tid}/attachments", json={"name": "a.txt", "data": data}).json()
    assert attachments.path_of(f) == attachments.folder() / "Thesis" / f["filename"]

    # A file from before project folders and a renamed project get tidied.
    conn = db.connect()
    legacy = api.post(f"/api/blocks/{api.tid}/attachments", json={"name": "old.txt", "data": data}).json()
    attachments.path_of(legacy).rename(attachments.folder() / legacy["filename"])
    api.patch(f"/api/blocks/{api.pid}", json={"title": "Thesis: final/v2"})
    other = api.post("/api/blocks", json={"title": "Thesis"}).json()["block"]["id"]   # same name as before
    with conn:
        assert attachments.tidy(conn) == 2
    home = attachments.folder() / "Thesis_ final_v2"
    assert attachments.path_of(f) == home / f["filename"] and attachments.path_of(legacy) == home / legacy["filename"]
    assert not (attachments.folder() / "Thesis").exists()                              # empty folder removed
    assert api.get(f["href"]).content == b"hi"

    api.patch(f"/api/blocks/{api.pid}", json={"title": "Thesis"})                      # two projects named the same
    with conn:
        attachments.tidy(conn)
    assert attachments.path_of(f) == attachments.folder() / f"Thesis ({api.pid})" / f["filename"]
    assert other
    conn.close()
