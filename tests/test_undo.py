"""⌘Z: every change to projects and to-dos is one undo step, by you or your agent."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(c, parent, title):
    return c.post("/api/blocks", json={"title": title, "parent_id": parent}).json()["block"]["id"]


def snapshot(c, pid):
    d = c.get(f"/api/blocks/{pid}").json()
    return sorted((x["id"], x["title"], x["status"], x["parent_id"], x["notes"], tuple(r["depends_on_id"] for r in x["rules"]))
                  for x in d["cards"].values())


def test_undo_each_kind_of_change(api):
    pid = api.post("/api/blocks", json={"title": "Game", "template": "game"}).json()["block"]["id"]
    stages = [s["key"] for s in api.get(f"/api/blocks/{pid}").json()["stages"]]
    cards = api.get(f"/api/blocks/{pid}").json()["cards"].values()
    a, b = [c["id"] for c in cards if not c["children_total"] and not c["locked"]][:2]
    before = snapshot(api, pid)
    changes = [
        lambda: api.patch(f"/api/blocks/{a}", json={"title": "Renamed", "notes": "x"}),
        lambda: api.post(f"/api/blocks/{b}/move", json={"status": stages[1]}),
        lambda: api.delete(f"/api/blocks/{a}"),
        lambda: add(api, pid, "New one"),
        lambda: api.post(f"/api/blocks/{b}/duplicate"),
    ]
    for change in changes:
        change()
        assert snapshot(api, pid) != before
        r = api.post("/api/undo").json()
        assert r["notices"][0].startswith("Undid:")
        assert snapshot(api, pid) == before, r["undone"]["label"]
    assert api.get("/api/trash").json()["items"] == []          # undoing a delete takes it out of the trash too


def test_undo_order_labels_and_agent_changes(api):
    from nestwork import agent
    pid = api.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
    t = add(api, pid, "Task")
    api.patch(f"/api/blocks/{t}", json={"title": "Task 2"})
    assert api.get("/api/undo").json()["next"]["label"] == "Edit “Task”"
    api.put("/api/settings", json={"agent_access": "full"})
    agent.request("update", {"block_id": t, "title": "By agent"})
    nxt = api.get("/api/undo").json()["next"]
    assert nxt["source"] == "agent" and "By agent" in nxt["label"]
    assert "(by your agent)" in api.post("/api/undo").json()["notices"][0]
    assert api.get(f"/api/blocks/{t}").json()["block"]["title"] == "Task 2"
    api.post("/api/undo")
    assert api.get(f"/api/blocks/{t}").json()["block"]["title"] == "Task"
    api.post("/api/undo")                                          # the add
    assert api.get(f"/api/blocks/{t}").status_code == 404
    api.post("/api/undo")                                          # the project itself
    assert api.post("/api/undo").status_code == 409


def test_a_new_change_drops_what_was_undone(api):
    pid = api.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
    t = add(api, pid, "A")
    api.patch(f"/api/blocks/{t}", json={"title": "B"})
    api.post("/api/undo")
    api.patch(f"/api/blocks/{t}", json={"notes": "n"})
    assert api.post("/api/undo").json()["undone"]["label"] == "Edit “A”"
    assert api.get("/api/undo").json()["next"]["label"] == "Add a to-do"
