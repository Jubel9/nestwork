"""Archive, priority and tags; JSON export/import; Markdown outline import."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(c, parent, title):
    return c.post("/api/blocks", json={"title": title, "parent_id": parent}).json()["block"]["id"]


def test_archive_hides_a_project_everywhere(api):
    pid = api.post("/api/blocks", json={"title": "Old"}).json()["block"]["id"]
    t = add(api, pid, "Leftover")
    api.patch(f"/api/blocks/{t}", json={"due_date": "2099-01-01"})
    assert api.patch(f"/api/blocks/{t}", json={"archived": True}).status_code == 422     # only projects
    api.patch(f"/api/blocks/{pid}", json={"archived": True})
    assert api.get("/api/projects").json() == []
    assert [p["title"] for p in api.get("/api/projects?archived=true").json()] == ["Old"]
    assert not any(i["id"] == t for g in api.get("/api/today").json()["groups"] for i in g["items"])
    assert api.get("/api/reminders?days=100000").json()["upcoming"] == []
    assert "## Archived projects" in api.get("/api/snapshot.md").text
    api.patch(f"/api/blocks/{pid}", json={"archived": False})
    assert len(api.get("/api/projects").json()) == 1


def test_priority_and_tags(api):
    pid = api.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
    low, high = add(api, pid, "Low one"), add(api, pid, "Urgent one")
    api.patch(f"/api/blocks/{high}", json={"priority": 3, "tags": ["#Uni", "uni", " writing  ", ""]})
    b = api.get(f"/api/blocks/{high}").json()["block"]
    assert b["priority"] == 3 and b["tags"] == ["Uni", "writing"]
    assert [i["title"] for i in api.get("/api/today").json()["focus"]][:2] == ["Urgent one", "Low one"]
    assert "high priority · #Uni #writing" in api.get("/api/snapshot.md").text
    assert api.patch(f"/api/blocks/{low}", json={"priority": 9}).status_code == 422


def test_export_then_import_is_a_faithful_copy(api):
    pid = api.post("/api/blocks", json={"title": "Game", "template": "game"}).json()["block"]["id"]
    tree = api.get(f"/api/blocks/{pid}").json()
    some = next(iter(tree["cards"]))
    api.patch(f"/api/blocks/{some}", json={"estimate_min": 90, "tags": ["art"], "priority": 2,
                                           "repeat": {"freq": "weekly"}})
    api.post(f"/api/blocks/{some}/notes", json={"text": "Remember the palette"})
    r = api.get(f"/api/blocks/{pid}/export.json")
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    new = api.post("/api/import", json={"data": data}).json()["projects"][0]
    a, b = api.get(f"/api/blocks/{pid}").json(), api.get(f"/api/blocks/{new}").json()
    assert new != pid and len(a["cards"]) == len(b["cards"]) and a["stages"] == b["stages"]
    shape = lambda d: sorted((c["title"], c["status"], len(c["rules"]), c["estimate_min"], tuple(c["tags"]), c["priority"],
                              c["repeat_text"], c["notes_count"]) for c in d["cards"].values())
    assert shape(a) == shape(b)
    # Rules in the copy point to the copy.
    copied = {c["id"] for c in b["cards"].values()}
    assert all(r["depends_on_id"] in copied for c in b["cards"].values() for r in c["rules"])
    assert len(api.get("/api/export.json").json()["projects"]) == 2
    assert api.post("/api/import", json={"data": {"nope": 1}}).status_code == 422


def test_outline_import(api):
    text = "# Thesis\n\n## Research\n- [x] Read papers\n  key ones only\n- [ ] Interviews\n  - Draft questions\n## Writing\n1. Intro\n"
    r = api.post("/api/import/outline", json={"text": text}).json()
    assert r["count"] == 6
    d = api.get(f"/api/blocks/{r['block_id']}").json()
    assert d["block"]["title"] == "Thesis" and [c["title"] for c in d["children"]] == ["Research", "Writing"]
    read = next(c for c in d["cards"].values() if c["title"] == "Read papers")
    assert read["done"] and read["notes"] == "key ones only"
    inside = api.post("/api/import/outline", json={"text": "- One\n- Two", "parent_id": r["block_id"]}).json()
    assert inside["block_id"] == r["block_id"] and inside["count"] == 2
    assert api.post("/api/import/outline", json={"text": "just words"}).status_code == 422
