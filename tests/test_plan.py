"""Plan my day: save, quick plan without AI, agent tools, undo."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from nestwork import db, plan


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Thesis"}).json()["block"]["id"]
        c.ids = [c.post("/api/blocks", json={"title": t, "parent_id": pid}).json()["block"]["id"] for t in ("Read", "Write", "Edit")]
        c.patch(f"/api/blocks/{c.ids[1]}", json={"priority": 3, "estimate_min": 90})
        yield c


def test_save_and_validate(api):
    r = api.put("/api/plan", json={"items": [{"start": "10:00", "minutes": 30, "title": "Call", "kind": "event"},
                                             {"start": "09:00", "minutes": 50, "block_id": api.ids[0]}]})
    items = r.json()["plan"]["items"]
    assert [i["start"] for i in items] == ["09:00", "10:00"] and items[0]["title"] == "Read" and items[0]["kind"] == "todo"
    assert api.put("/api/plan", json={"items": [{"start": "9am", "title": "x"}]}).status_code == 422
    assert api.put("/api/plan", json={"items": [{"start": "09:00", "block_id": 99999}]}).status_code == 422
    api.post(f"/api/blocks/{api.ids[0]}/move", json={"status": api.get(f"/api/blocks/{api.ids[0]}").json()["stages"][-1]["key"]})
    assert api.get("/api/plan").json()["plan"]["items"][0]["done"] is True
    api.delete("/api/plan")
    assert api.get("/api/plan").json()["plan"] is None
    api.post("/api/undo")                                                    # clearing can be undone
    assert api.get("/api/plan").json()["plan"] is not None


def test_quick_plan(api):
    conn = db.connect()
    with conn:
        p = plan.quick_plan(conn, datetime(2026, 10, 12, 9, 7))
    conn.close()
    todos = [i for i in p["items"] if i["kind"] == "todo"]
    assert p["items"][0]["start"] == "09:15" and todos[0]["title"] == "Write" and todos[0]["minutes"] == 90   # high priority first
    assert p["items"][1]["kind"] == "break" and p["items"][-1]["kind"] == "todo"
    assert sum(i["minutes"] for i in todos) >= 120                             # covers the daily focus goal


def test_agent_tools(api):
    from nestwork.mcp_server import get_day_plan, set_day_plan
    text = get_day_plan()
    assert f"Write [id {api.ids[1]}]" in text and "high priority" in text and "No calendar connected" in text
    assert "Not done" in set_day_plan([{"start": "nope"}])
    reply = set_day_plan([{"start": "09:00", "minutes": 60, "block_id": api.ids[1]}, {"start": "10:00", "minutes": 10, "kind": "break"}])
    assert reply.startswith("Saved today's plan (2 items")
    assert "## Today's plan" in get_day_plan()
    assert api.get("/api/undo").json()["next"]["source"] == "agent"


def test_picks_replace_the_automatic_todos(api):
    auto = api.get("/api/today").json()
    assert auto["picked"] is None and {i["id"] for i in auto["focus"]} == set(api.ids)   # default: automatic
    r = api.put("/api/today/picks", json={"block_ids": [api.ids[2], api.ids[0]], "note": "Edit first"})
    assert r.status_code == 200
    t = api.get("/api/today").json()
    assert [i["id"] for i in t["focus"]] == [api.ids[2], api.ids[0]] and t["picked"]["note"] == "Edit first"
    pid = api.get(f"/api/blocks/{api.ids[0]}").json()
    assert api.put("/api/today/picks", json={"block_ids": [99999]}).status_code == 422
    assert api.put("/api/today/picks", json={"block_ids": []}).status_code == 422
    api.post(f"/api/blocks/{api.ids[2]}/move", json={"status": pid["stages"][-1]["key"]})   # done ones drop out
    t = api.get("/api/today").json()
    assert [i["id"] for i in t["focus"]] == [api.ids[0]] and t["picked"]["count"] == 2
    api.post("/api/undo")
    api.delete("/api/today/picks")
    assert api.get("/api/today").json()["picked"] is None
    api.post("/api/undo")                                                    # going back to automatic can be undone
    assert api.get("/api/today").json()["picked"] is not None


def test_pick_todays_todos_tool(api):
    from nestwork.mcp_server import pick_todays_todos, today
    assert "automatic" in today()
    assert "Not done" in pick_todays_todos([123456])
    assert pick_todays_todos([api.ids[1]], "Deadline Friday").startswith("Today's list has 1 to-dos.")
    text = today()
    assert "## Picked for today" in text and "Deadline Friday" in text
    assert api.get("/api/undo").json()["next"]["source"] == "agent"
    assert pick_todays_todos([]) == "Back to the automatic to-dos on the HUD."
    assert api.get("/api/today").json()["picked"] is None


def test_todos_written_just_for_today(api):
    r = api.put("/api/today/picks", json={"items": ["Call the bank about the card", api.ids[1], "  call the BANK about the card "]})
    assert r.status_code == 200 and len(r.json()["picks"]["items"]) == 2          # repeated lines are skipped
    t = api.get("/api/today").json()
    assert t["picked"]["items"] == [{"index": 0, "text": "Call the bank about the card", "done": False}, {"block_id": api.ids[1]}]
    assert [i["id"] for i in t["focus"]] == [api.ids[1]]
    api.put("/api/today/picks", json={"items": ["Buy ink"], "append": True})
    assert [i.get("text") for i in api.get("/api/today").json()["picked"]["items"]] == ["Call the bank about the card", None, "Buy ink"]
    assert api.post("/api/today/picks/0/done", json={"done": True}).status_code == 200
    assert api.get("/api/today").json()["picked"]["items"][0]["done"] is True
    assert api.post("/api/today/picks/1/done", json={}).status_code == 404       # a project to-do: finish it with move
    api.post("/api/undo")                                                    # ticking off can be undone
    assert api.get("/api/today").json()["picked"]["items"][0]["done"] is False
    assert api.put("/api/today/picks", json={"items": ["   "]}).status_code == 422


def test_agent_writes_todays_list(api):
    from nestwork.mcp_server import pick_todays_todos, tick_todays_todo, today
    reply = pick_todays_todos(["Send the rent contract", api.ids[0]], "Language school deadline")
    assert reply.startswith("Today's list has 2 to-dos (1 written just for today)")
    text = today()
    assert "- [ ] Send the rent contract (just for today, #0)" in text and f"**Read** (Thesis) · To do [id {api.ids[0]}]" in text
    assert "Not done" in tick_todays_todo(1)
    assert tick_todays_todo(0) == "“Send the rent contract” is done."
    assert "- [x] Send the rent contract" in today()
    assert pick_todays_todos(["Water plants"], append=True).startswith("Today's list has 3")
