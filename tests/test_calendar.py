"""The Calendar: fixed dates and planned items day by day, single-item changes, agent tools, undo."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        c.pid = c.post("/api/blocks", json={"title": "Scholarship"}).json()["block"]["id"]
        c.ids = [c.post("/api/blocks", json={"title": t, "parent_id": c.pid}).json()["block"]["id"]
                 for t in ("Essay", "Dentist", "Gym")]
        yield c


def day(n=0):
    return (date.today() + timedelta(days=n)).isoformat()


def test_fixed_dates_show_on_their_days(api):
    api.patch(f"/api/blocks/{api.ids[0]}", json={"due_date": day(2), "due_time": "17:00"})
    api.patch(f"/api/blocks/{api.ids[2]}", json={"due_date": day(0), "repeat": {"freq": "daily", "interval": 1}})
    api.post("/api/reminders", json={"text": "Call the bank", "remind_at": f"{day(1)}T18:00"})
    days = {d["date"]: d for d in api.get(f"/api/calendar?start={day(0)}&days=4").json()["days"]}
    assert [i["title"] for i in days[day(2)]["due"] if i["kind"] == "due"] == ["Essay"]
    assert days[day(2)]["due"][0]["time"] == "17:00"
    assert all(any(i["kind"] == "repeat" and i["title"] == "Gym" for i in days[day(n)]["due"]) for n in (1, 2, 3))
    assert days[day(1)]["reminders"][0]["title"] == "Call the bank" and days[day(1)]["reminders"][0]["time"] == "18:00"


def test_add_move_remove_and_undo(api):
    r = api.post("/api/calendar/items", json={"day": day(1), "start": "09:00", "minutes": 50, "block_id": api.ids[0]})
    assert r.status_code == 201
    item = r.json()["item"]
    assert item["title"] == "Essay" and item["kind"] == "todo" and item["by"] == "you" and item["id"]
    api.post("/api/calendar/items", json={"day": day(1), "start": "08:00", "title": "Gym class", "kind": "event"})
    plan = api.get(f"/api/calendar?start={day(1)}&days=1").json()["days"][0]["plan"]
    assert [i["start"] for i in plan] == ["08:00", "09:00"]

    moved = api.patch(f"/api/calendar/items/{day(1)}/{item['id']}", json={"day": day(3), "start": "14:30", "minutes": 25})
    assert moved.status_code == 200 and moved.json()["item"]["start"] == "14:30"
    days = {d["date"]: d for d in api.get(f"/api/calendar?start={day(1)}&days=3").json()["days"]}
    assert [i["title"] for i in days[day(1)]["plan"]] == ["Gym class"]
    assert [i["id"] for i in days[day(3)]["plan"]] == [item["id"]]

    api.post("/api/undo")                                                       # the move is one step
    days = {d["date"]: d for d in api.get(f"/api/calendar?start={day(1)}&days=3").json()["days"]}
    assert len(days[day(1)]["plan"]) == 2 and days[day(3)]["plan"] == []

    assert api.delete(f"/api/calendar/items/{day(1)}/{item['id']}").status_code == 200
    assert api.delete(f"/api/calendar/items/{day(1)}/nope").status_code == 404
    assert api.post("/api/calendar/items", json={"day": day(1), "start": "9am", "title": "x"}).status_code == 422


def test_today_on_the_calendar_is_the_huds_plan(api):
    api.post("/api/calendar/items", json={"day": day(0), "start": "10:00", "title": "Dentist visit", "kind": "event"})
    assert api.get("/api/plan").json()["plan"]["items"][0]["title"] == "Dentist visit"


def test_agent_tools(api):
    from nestwork.mcp_server import calendar, reschedule, schedule, set_day_plan, unschedule
    api.patch(f"/api/blocks/{api.ids[1]}", json={"due_date": day(1)})
    reply = schedule("tomorrow", "19:45", 30, block_id=api.ids[0])
    assert reply.startswith("Scheduled “Essay”") and "19:45–20:15" in reply
    text = calendar("today", 3)
    assert f"due: Dentist [id {api.ids[1]}]" in text and "19:45–20:15 · Essay" in text
    item_id = reply.split("(item ")[1].split(")")[0]
    assert "20:00" in reschedule("tomorrow", item_id, start="20:00")
    assert unschedule("tomorrow", item_id).startswith("Took “Essay” off")
    assert unschedule("tomorrow", item_id).startswith("Not done")
    assert "Not done" in schedule("someday", "10:00", title="x")
    assert set_day_plan([{"start": "09:00", "minutes": 30, "title": "Maths for ML", "kind": "event"}], "tomorrow").startswith(
        f"Saved the plan for {day(1)}")
    assert set_day_plan([{"start": "09:00", "title": "Git", "kind": "event"}]).startswith("Saved today's plan")
    plan = api.get(f"/api/calendar?start={day(1)}&days=1").json()["days"][0]["plan"]
    assert plan[0]["by"] != "you"                                                # shows who put it there


def test_past_days_keep_their_history(api):
    from nestwork import db, reminders
    api.post("/api/reminders", json={"text": "Call the bank", "remind_at": f"{day(0)}T00:01"})
    reminders.collect_due()                                                     # it goes out
    stages = api.get(f"/api/blocks/{api.ids[0]}").json()["stages"]
    api.post(f"/api/blocks/{api.ids[0]}/move", json={"status": stages[-1]["key"]})
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO focus_sessions (block_id, title, started_at, ended_at, planned_min, focus_min, status, rating) "
                     "VALUES (?, 'Essay', ?, ?, 25, 25, 'completed', 4)",
                     (api.ids[0], f"{day(-400)}T09:00:00", f"{day(-400)}T09:30:00"))
    conn.close()
    today = api.get(f"/api/calendar?start={day(0)}&days=1").json()["days"][0]
    assert today["reminders"][0]["sent"] is True and today["reminders"][0]["title"] == "Call the bank"
    assert [f["title"] for f in today["finished"]] == ["Essay"]
    long_ago = api.get(f"/api/calendar?start={day(-400)}&days=1").json()["days"][0]
    assert long_ago["focus"][0]["start"] == "09:00" and long_ago["focus"][0]["minutes"] == 30
    from nestwork.mcp_server import calendar
    assert "focused 25 min on Essay" in calendar(day(-400), 1) and "went out" in calendar("today", 1)


def test_archived_projects_keep_their_dates(api):
    api.patch(f"/api/blocks/{api.ids[0]}", json={"due_date": day(1)})
    api.post("/api/calendar/items", json={"day": day(1), "start": "10:00", "minutes": 30, "block_id": api.ids[1]})
    api.patch(f"/api/blocks/{api.pid}", json={"archived": True})
    assert api.pid not in [p["id"] for p in api.get("/api/projects").json()]
    d = api.get(f"/api/calendar?start={day(1)}&days=1").json()["days"][0]
    assert [(i["title"], i["archived"]) for i in d["due"]] == [("Essay", True)]
    assert d["plan"][0]["title"] == "Dentist" and not d["plan"][0].get("gone")
    from nestwork import calendar_view, db
    conn = db.connect()
    assert "Essay" in calendar_view.markdown(conn, date.fromisoformat(day(1)), 1) and "archived project" in calendar_view.markdown(conn, date.fromisoformat(day(1)), 1)
    conn.close()
