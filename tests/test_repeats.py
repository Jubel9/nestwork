"""Repeating to-dos: the rule maths, and coming back after they're done."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from nestwork import db, repeats

D = date.fromisoformat


@pytest.mark.parametrize("rule, after, expected", [
    ({"freq": "daily", "interval": 1}, "2026-10-09", "2026-10-10"),
    ({"freq": "daily", "interval": 3}, "2026-10-09", "2026-10-12"),
    ({"freq": "weekly", "interval": 1, "weekdays": [0, 1, 2, 3, 4]}, "2026-10-09", "2026-10-12"),   # Fri → Mon
    ({"freq": "weekly", "interval": 1, "weekdays": [0, 3]}, "2026-10-12", "2026-10-15"),             # Mon → Thu
    ({"freq": "weekly", "interval": 2, "weekdays": [0]}, "2026-10-12", "2026-10-26"),
    ({"freq": "weekly", "interval": 2}, "2026-10-09", "2026-10-23"),
    ({"freq": "monthly", "interval": 1, "monthday": 31}, "2026-01-31", "2026-02-28"),
    ({"freq": "monthly", "interval": 1, "monthday": 31}, "2026-02-28", "2026-03-31"),
    ({"freq": "monthly", "interval": 3, "monthday": 15}, "2026-10-15", "2027-01-15"),
    ({"freq": "yearly", "interval": 1}, "2028-02-29", "2029-02-28"),
])
def test_next_after(rule, after, expected):
    assert repeats.next_after(rule, D(after)).isoformat() == expected


def test_parse_and_describe():
    assert repeats.describe(repeats.parse("weekdays")) == "Every weekday"
    assert repeats.describe(repeats.parse("every 2 weeks")) == "Every 2 weeks"
    assert repeats.describe(repeats.parse("Mon, Thu")) == "Every week on Mon, Thu"
    assert repeats.describe(repeats.parse("monthly", D("2026-10-03"))) == "Every month on the 3rd"
    assert repeats.parse("none") is None
    with pytest.raises(ValueError):
        repeats.parse("whenever")


def test_plan():
    daily, monthly = {"freq": "daily", "interval": 1}, {"freq": "monthly", "interval": 1, "monthday": 10}
    today = D("2026-10-10")
    # Done on its day: back tomorrow, due tomorrow.
    assert repeats.plan(daily, "2026-10-10", today) == {"reopen": "2026-10-11", "due": "2026-10-11"}
    # Overdue daily done late: the next one is after today, not a pile of missed days.
    assert repeats.plan(daily, "2026-10-07", today) == {"reopen": "2026-10-11", "due": "2026-10-11"}
    # A monthly report is back on the board tomorrow, due next month.
    assert repeats.plan(monthly, "2026-10-10", today) == {"reopen": "2026-10-11", "due": "2026-11-10"}
    # No due date: it comes back on the day, still without one.
    assert repeats.plan(daily, None, today) == {"reopen": "2026-10-11", "due": None}


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def test_a_repeating_todo_comes_back(api):
    pid = api.post("/api/blocks", json={"title": "Routines", "stages": ["To do", "Doing", "Done"]}).json()["block"]["id"]
    done = api.get(f"/api/blocks/{pid}").json()["stages"][-1]["key"]
    today = date.today()
    bid = api.post("/api/blocks", json={"title": "Flashcards", "parent_id": pid}).json()["block"]["id"]
    r = api.patch(f"/api/blocks/{bid}", json={"due_date": today.isoformat(), "repeat": {"freq": "daily"}})
    assert r.json()["block"]["repeat_text"] == "Every day"
    assert api.patch(f"/api/blocks/{bid}", json={"repeat": {"freq": "hourly"}}).status_code == 422
    assert api.patch(f"/api/blocks/{pid}", json={"repeat": {"freq": "daily"}}).status_code == 422

    moved = api.post(f"/api/blocks/{bid}/move", json={"status": done}).json()
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert any("comes back on " + tomorrow in n for n in moved["notices"])
    b = api.get(f"/api/blocks/{bid}").json()["block"]
    assert b["done"] and b["repeat_next"] == {"reopen": tomorrow, "due": tomorrow}
    assert not any(i["id"] == bid for g in api.get("/api/today").json()["groups"] for i in g["items"])

    conn = db.connect()
    with conn:
        assert repeats.reopen_due(conn, today) == []                         # not yet
        assert "back in “To do”" in repeats.reopen_due(conn, today + timedelta(days=1))[0]
    conn.close()
    b = api.get(f"/api/blocks/{bid}").json()["block"]
    assert not b["done"] and b["due_date"] == tomorrow and b["repeat_next"] is None
    assert "Back for the next round" in api.get(f"/api/blocks/{bid}/notes").json()[0]["text"]


def test_reopening_by_hand_cancels_and_a_bubble_comes_back_whole(api):
    pid = api.post("/api/blocks", json={"title": "Home", "stages": ["To do", "Done"]}).json()["block"]["id"]
    done = api.get(f"/api/blocks/{pid}").json()["stages"][-1]["key"]
    review = api.post("/api/blocks", json={"title": "Weekly review", "parent_id": pid}).json()["block"]["id"]
    steps = [api.post("/api/blocks", json={"title": t, "parent_id": review}).json()["block"]["id"] for t in ("Inbox", "Calendar")]
    api.patch(f"/api/blocks/{review}", json={"repeat": {"freq": "weekly"}})
    for s in steps:
        api.post(f"/api/blocks/{s}/move", json={"status": done})
    assert api.get(f"/api/blocks/{review}").json()["block"]["repeat_next"]   # completed itself, so it's planned
    api.post(f"/api/blocks/{steps[0]}/move", json={"status": "x"})            # unknown phase: refused, nothing changes
    first = api.get(f"/api/blocks/{pid}").json()["stages"][0]["key"]
    api.post(f"/api/blocks/{steps[0]}/move", json={"status": first})
    assert api.get(f"/api/blocks/{review}").json()["block"]["repeat_next"] is None

    api.post(f"/api/blocks/{steps[0]}/move", json={"status": done})
    conn = db.connect()
    with conn:
        repeats.reopen_due(conn, date.today() + timedelta(days=8))
    conn.close()
    assert all(not api.get(f"/api/blocks/{s}").json()["block"]["done"] for s in [review, *steps])


def test_agent_sets_a_repeat():
    from nestwork import agent
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Me"}).json()["block"]["id"]
        c.put("/api/settings", json={"agent_access": "full"})
        assert agent.request("add_todo", {"parent_id": pid, "title": "Gym", "repeat": "mon,thu", "estimate_hours": 1.5}).startswith("Done")
        gym = c.get(f"/api/blocks/{pid}").json()["children"][0]
        assert gym["repeat_text"] == "Every week on Mon, Thu" and gym["estimate_min"] == 90
        assert "Not done" in agent.request("update", {"block_id": gym["id"], "repeat": "sometimes"})
        assert agent.request("update", {"block_id": gym["id"], "repeat": "none"}).startswith("Done")
        assert c.get(f"/api/blocks/{gym['id']}").json()["block"]["repeat"] is None
