"""Estimates, time spent and the finish-date forecast."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from nestwork import db, forecast


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(c, parent, title, minutes=None):
    bid = c.post("/api/blocks", json={"title": title, "parent_id": parent}).json()["block"]["id"]
    if minutes:
        c.patch(f"/api/blocks/{bid}", json={"estimate_min": minutes})
    return bid


def log_focus(block_id, project_id, minutes, day):
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO focus_sessions (block_id, project_id, title, started_at, ended_at, planned_min, focus_min, status) "
                     "VALUES (?, ?, 'x', ?, ?, ?, ?, 'completed')",
                     (block_id, project_id, f"{day}T10:00:00", f"{day}T11:00:00", minutes, minutes))
    conn.close()


def test_forecast(api):
    pid = api.post("/api/blocks", json={"title": "Thesis", "stages": ["To do", "Doing", "Done"]}).json()["block"]["id"]
    done = api.get(f"/api/blocks/{pid}").json()["stages"][-1]["key"]
    read = add(api, pid, "Read", 120)
    write = add(api, pid, "Write", 240)
    edit = add(api, pid, "Edit")                     # no estimate: counted at the median (180)
    finished = add(api, pid, "Outline", 60)
    api.post(f"/api/blocks/{finished}/move", json={"status": done})
    api.put(f"/api/blocks/{write}/rules", json={"rules": [{"depends_on_id": read, "required_stage": done}]})
    api.put(f"/api/blocks/{edit}/rules", json={"rules": [{"depends_on_id": write, "required_stage": done}]})
    today = date.today()
    log_focus(read, pid, 150, today.isoformat())     # past its 2h estimate
    log_focus(None, pid, 270, (today - timedelta(days=3)).isoformat())

    f = api.get(f"/api/blocks/{pid}").json()["forecast"]
    assert f["typical_min"] == 120                   # median of 120, 240, 60
    assert (f["open"], f["estimated"], f["assumed"]) == (3, 2, 1)
    assert [o["title"] for o in f["over"]] == ["Read"]
    assert f["remaining_min"] == 30 + 240 + 120      # a quarter of Read's estimate, all of Write, Edit at the median
    assert f["pace_min_per_day"] == 30.0 and f["pace_from"] == "project"   # 420 min over 14 days
    assert f["finish"] == (today + timedelta(days=13 - 1)).isoformat()
    assert [c["title"] for c in f["chain"]] == ["Read", "Write", "Edit"]
    assert api.get(f"/api/blocks/{read}").json()["block"]["spent_min"] == 150

    api.patch(f"/api/blocks/{pid}", json={"due_date": (today + timedelta(days=5)).isoformat()})
    f = api.get("/api/projects").json()[0]["forecast"]
    assert f["late_days"] == 7
    assert "7 day(s) after its due date" in forecast.summary(f)


def test_no_estimates_no_forecast(api):
    pid = api.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
    add(api, pid, "A")
    f = api.get(f"/api/blocks/{pid}").json()["forecast"]
    assert f["remaining_min"] is None and f["finish"] is None
    assert "none of its 1 open" in forecast.summary(f)


def test_repeating_todos_are_left_out(api):
    pid = api.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
    add(api, pid, "Once", 60)
    gym = add(api, pid, "Gym", 90)
    api.patch(f"/api/blocks/{gym}", json={"repeat": {"freq": "daily"}})
    f = api.get(f"/api/blocks/{pid}").json()["forecast"]
    assert f["open"] == 1 and f["remaining_min"] == 60 and f["finish"] is None   # no focus logged yet
    assert "No finish date" in forecast.summary(f)


def test_mcp_forecast_and_estimate():
    import anyio
    from mcp.client.client import Client
    from nestwork.main import app
    from nestwork.mcp_server import server

    def call(tool, **args):
        async def go():
            async with Client(server) as client:
                return "\n".join(p.text for p in (await client.call_tool(tool, args)).content)
        return anyio.run(go)

    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "P"}).json()["block"]["id"]
        a = add(c, pid, "A")
        c.put("/api/settings", json={"agent_access": "full"})
        assert call("update", block_id=a, estimate_hours=2).startswith("Done")
        assert "estimate 2h" in call("get_block", block_id=pid)
        out = call("forecast", block_id=pid)
        assert "About 2h of work left" in out
        assert "Forecast:" in c.get("/api/snapshot.md").text


def test_chain_without_estimates_counts_todos(api):
    pid = api.post("/api/blocks", json={"title": "P", "stages": ["To do", "Done"]}).json()["block"]["id"]
    done = api.get(f"/api/blocks/{pid}").json()["stages"][-1]["key"]
    a, b, c, d = (add(api, pid, t) for t in "ABCD")
    api.put(f"/api/blocks/{b}/rules", json={"rules": [{"depends_on_id": a, "required_stage": done}]})
    api.put(f"/api/blocks/{c}/rules", json={"rules": [{"depends_on_id": b, "required_stage": done}]})
    f = api.get(f"/api/blocks/{pid}").json()["forecast"]
    assert [x["title"] for x in f["chain"]] == ["A", "B", "C"] and f["remaining_min"] is None
    assert "Longest chain: A → B → C (3 to-dos)" in forecast.summary(f)
