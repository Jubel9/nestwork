"""Focus sessions from the Pomodoro timer, the productivity stats, and what the agent sees."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from nestwork import db, focus


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        c.pid = c.post("/api/blocks", json={"title": "Thesis", "template": "blank", "stages": ["To do", "Doing", "Done"]}).json()["block"]["id"]
        c.tid = c.post("/api/blocks", json={"title": "Write chapter 1", "parent_id": c.pid}).json()["block"]["id"]
        yield c


def test_session_on_a_todo_records_project_and_minutes(api):
    s = api.post("/api/focus/start", json={"block_id": api.tid, "planned_min": 25}).json()
    assert (s["title"], s["project_id"], s["project_title"], s["status"]) == ("Write chapter 1", api.pid, "Thesis", "running")
    assert api.get("/api/focus/stats").json()["current"]["id"] == s["id"]
    done = api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True}).json()["session"]
    assert (done["status"], done["focus_min"]) == ("completed", 25)
    api.patch(f"/api/focus/{s['id']}", json={"rating": 4, "note": "Good flow"})
    st = api.get("/api/focus/stats").json()
    assert st["today"]["focus_min"] == 25 and st["today"]["sessions"] == 1 and st["today"]["avg_rating"] == 4
    assert st["projects"][0]["title"] == "Thesis" and st["streak"] == 1 and st["current"] is None
    # 70% of (25/120 of the goal) + 30% of (4-1)/4
    assert st["today"]["score"] == round(100 * (0.7 * 25 / 120 + 0.3 * 0.75))


def test_whole_project_and_free_text(api):
    p = api.post("/api/focus/start", json={"project_id": api.pid, "planned_min": 25}).json()
    assert (p["block_id"], p["title"]) == (None, "Thesis")
    api.post(f"/api/focus/{p['id']}/finish", json={"focus_min": 25, "completed": True})
    o = api.post("/api/focus/start", json={"title": "Emails", "planned_min": 15}).json()
    assert o["project_id"] is None
    api.post(f"/api/focus/{o['id']}/finish", json={"focus_min": 10, "completed": False})
    keys = {p["key"]: p["focus_min"] for p in api.get("/api/focus/stats").json()["projects"]}
    assert keys == {str(api.pid): 25, "other": 10}
    assert api.post("/api/focus/start", json={"planned_min": 25}).status_code == 422   # must say what


def test_stopped_under_a_minute_is_dropped_and_running_one_is_closed_on_start(api):
    s = api.post("/api/focus/start", json={"title": "Oops", "planned_min": 25}).json()
    assert api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 0.3, "completed": False}).json()["session"] is None
    first = api.post("/api/focus/start", json={"title": "Left open", "planned_min": 25}).json()
    api.post("/api/focus/start", json={"title": "Next", "planned_min": 25})
    conn = db.connect()
    status = conn.execute("SELECT status FROM focus_sessions WHERE id = ?", (first["id"],)).fetchone()
    conn.close()
    assert status is None or status["status"] == "stopped"   # closed (or dropped, being under a minute)


def test_deleted_todo_keeps_its_sessions(api):
    s = api.post("/api/focus/start", json={"block_id": api.tid, "planned_min": 25}).json()
    api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True})
    api.delete(f"/api/blocks/{api.tid}")
    log = api.get("/api/focus/stats").json()["sessions"]
    assert log[0]["title"] == "Write chapter 1" and log[0]["block_id"] is None


def test_streak_days_and_goal(api):
    conn = db.connect()
    today = date.today()
    for back in (1, 2, 4):     # yesterday, the day before, and one with a gap
        d = datetime.combine(today - timedelta(days=back), datetime.min.time()) + timedelta(hours=10)
        conn.execute("INSERT INTO focus_sessions (title, started_at, ended_at, planned_min, focus_min, status) "
                     "VALUES ('x', ?, ?, 25, 25, 'completed')", (d.isoformat(), (d + timedelta(minutes=25)).isoformat()))
    conn.commit()
    s = focus.stats(conn, 7)
    conn.close()
    assert s["streak"] == 2 and s["total"]["active_days"] == 3 and len(s["by_day"]) == 7
    assert s["hours"][10]["focus_min"] == 75 and s["best_hour"] == 10
    assert api.put("/api/focus/goal", json={"minutes": 90}).json()["goal_min"] == 90
    assert api.get("/api/focus/stats").json()["goal_min"] == 90


def test_agent_sees_focus(api):
    from tests.test_mcp import call
    s = api.post("/api/focus/start", json={"block_id": api.tid, "planned_min": 25}).json()
    assert "focus session on **Write chapter 1**" in call("today")
    api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True})
    api.patch(f"/api/focus/{s['id']}", json={"rating": 5, "note": "Nailed the intro"})
    out = call("focus_stats", days=7)
    assert "Write chapter 1" in out and "productivity 5/5" in out and "Nailed the intro" in out and "Thesis" in out
    assert "## Focus" in api.get("/api/snapshot.md").text


def test_daily_pomodoros_default_count_and_setting(api):
    item = lambda: next(i for g in api.get("/api/today").json()["groups"] for i in g["items"] if i["id"] == api.tid)
    assert (item()["pomodoros"], item()["pomodoros_done"]) == (0, 0)            # new to-dos: no daily plan
    api.patch(f"/api/blocks/{api.tid}", json={"pomodoros": 4})
    for completed in (True, True, False):                                       # a stopped one doesn't count
        s = api.post("/api/focus/start", json={"block_id": api.tid, "planned_min": 25}).json()
        api.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25 if completed else 5, "completed": completed})
    assert item()["pomodoros_done"] == 2
    assert api.patch(f"/api/blocks/{api.tid}", json={"pomodoros": 6}).json()["block"]["pomodoros"] == 6
    assert item()["pomodoros"] == 6
    api.patch(f"/api/blocks/{api.tid}", json={"pomodoros": 0})                  # no daily plan
    assert item()["pomodoros"] == 0
    assert api.patch(f"/api/blocks/{api.tid}", json={"pomodoros": -1}).status_code == 422


def test_stopwatch_sessions():
    """Count-up sessions: start without a name, name them at Finish, no length cap but 16 h; labels come back."""
    from datetime import date
    from fastapi.testclient import TestClient
    from nestwork import db, focus
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Thesis"}).json()["block"]["id"]
        todo = c.post("/api/blocks", json={"title": "Write", "parent_id": pid}).json()["block"]["id"]
        assert c.post("/api/focus/start", json={"planned_min": 25}).status_code == 422          # a pomodoro needs a name
        s = c.post("/api/focus/start", json={"mode": "stopwatch"}).json()
        assert s["title"] == focus.UNTITLED and s["planned_min"] == 0 and s["mode"] == "stopwatch"
        conn = db.connect()
        assert "stopwatch session" in focus.current_line(conn)
        conn.close()
        done = c.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 95, "completed": True, "title": "Emails"}).json()
        assert done["session"]["title"] == "Emails" and done["session"]["focus_min"] == 95     # not capped at a planned length
        s2 = c.post("/api/focus/start", json={"mode": "stopwatch", "block_id": todo}).json()
        c.post(f"/api/focus/{s2['id']}/finish", json={"focus_min": 60, "completed": True})
        s3 = c.post("/api/focus/start", json={"mode": "stopwatch"}).json()
        assert c.post(f"/api/focus/{s3['id']}/finish", json={"focus_min": 10, "completed": True, "title": " "}).status_code == 422
        c.post(f"/api/focus/{s3['id']}/finish", json={"focus_min": 10, "completed": True, "block_id": todo})
        assert c.get("/api/focus/labels").json()["labels"] == ["Emails"]
        conn = db.connect()
        assert focus.pomodoros_today(conn, date.today())[todo] == 2      # 60 min → 2, 10 min → 0
        assert "1 h 35 min (stopwatch)" in focus.markdown(conn, 7)
        conn.close()


def test_timer_is_shared_through_the_server():
    from fastapi.testclient import TestClient
    from nestwork.main import app
    with TestClient(app) as c:
        assert c.get("/api/timer").json() == {"state": None, "rev": 0, "settings": None}
        v0 = c.get("/api/version").json()["version"]
        rev = c.put("/api/timer", json={"state": {"mode": "focus", "endsAt": 123}}).json()["rev"]
        assert rev == 1 and c.get("/api/version").json()["timer_rev"] == 1
        assert c.get("/api/version").json()["version"] == v0          # not a data change: pages don't redraw
        # Two devices see the time run out: only the first claim wins.
        assert c.post("/api/timer/claim", json={"rev": 1}).json()["rev"] == 2
        assert c.post("/api/timer/claim", json={"rev": 1}).status_code == 409
        c.put("/api/timer/settings", json={"settings": {"focus": 50}})
        assert c.get("/api/timer").json()["settings"] == {"focus": 50}
