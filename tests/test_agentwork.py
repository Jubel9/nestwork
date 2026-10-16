"""Agent's Work: schedules, the API, the scheduler, runs posting into the chat, and the MCP tools."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from nestwork import agentwork


@pytest.fixture()
def client(monkeypatch):
    fake = Path(__file__).with_name("fake_claude.py")
    wrapper = fake.with_name("fake_claude.sh")
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("NESTWORK_CLAUDE_BIN", str(wrapper))
    from nestwork import main
    from nestwork.main import app

    async def no_scheduler():          # the tests call claim_due with their own clock; the real one would race them
        pass
    monkeypatch.setattr(main, "agent_work_loop", no_scheduler)
    with TestClient(app) as c:
        yield c


MON = datetime(2026, 10, 12, 9, 30)   # a Monday


@pytest.mark.parametrize("schedule, words, first", [
    ("0 7 * * *", "Every day at 07:00", datetime(2026, 10, 13, 7, 0)),
    ("30 8 * * 1-5", "Weekdays at 08:30", datetime(2026, 10, 13, 8, 30)),
    ("0 18 * * 0", "Every Sunday at 18:00", datetime(2026, 10, 18, 18, 0)),
    ("0 9 * * 7", "Every Sunday at 09:00", datetime(2026, 10, 18, 9, 0)),
    ("0 9 * * mon,fri", "Every Monday, Friday at 09:00", datetime(2026, 10, 16, 9, 0)),
    ("*/30 * * * *", "Every 30 minutes", datetime(2026, 10, 12, 10, 0)),
    ("0 */2 * * *", "Every 2 hours", datetime(2026, 10, 12, 10, 0)),
    ("0 9 1 * *", "Every month on the 1st at 09:00", datetime(2026, 11, 1, 9, 0)),
    ("once 2026-10-14 08:00", "Once on Wed 14 Oct 2026 at 08:00", datetime(2026, 10, 14, 8, 0)),
])
def test_schedules(schedule, words, first):
    assert agentwork.describe(schedule) == words
    assert agentwork.next_after(schedule, MON) == first


def test_day_and_weekday_both_set_means_either():
    runs = agentwork.upcoming_runs("0 9 13 * 1", 3, MON)   # the 13th, or any Monday
    assert runs == [datetime(2026, 10, 13, 9), datetime(2026, 10, 19, 9), datetime(2026, 10, 26, 9)]


@pytest.mark.parametrize("bad", ["", "every morning", "61 * * * *", "* * * *", "*/0 * * * *", "once tomorrow"])
def test_bad_schedules_are_refused(bad):
    with pytest.raises(ValueError):
        agentwork.parse(bad)


def test_create_edit_pause_delete(client):
    r = client.post("/api/agent-work", json={"name": "Morning brief", "prompt": "Brief me", "schedule": "0 7 * * *"})
    assert r.status_code == 201
    job = r.json()
    assert job["describe"] == "Every day at 07:00" and job["enabled"] and job["next_run"].endswith("T07:00")
    assert client.post("/api/agent-work", json={"name": "x", "prompt": "y", "schedule": "nope"}).status_code == 400
    assert client.post("/api/agent-work", json={"name": "x", "prompt": "y",
                                                "schedule": "once 2020-01-01 08:00"}).status_code == 400

    job = client.patch(f"/api/agent-work/{job['id']}", json={"schedule": "30 8 * * 1-5"}).json()
    assert job["describe"] == "Weekdays at 08:30"
    job = client.patch(f"/api/agent-work/{job['id']}", json={"enabled": False}).json()
    assert not job["enabled"] and job["next_run"] is None
    job = client.patch(f"/api/agent-work/{job['id']}", json={"enabled": True}).json()
    assert job["next_run"].endswith("T08:30")

    preview = client.get("/api/agent-work/preview", params={"schedule": "0 7 * * *"}).json()
    assert preview["describe"] == "Every day at 07:00" and len(preview["next"]) == 3

    assert client.delete(f"/api/agent-work/{job['id']}").json()["archived"]       # deleting archives it
    data = client.get("/api/agent-work").json()
    assert data["jobs"] == [] and data["archived"][0]["id"] == job["id"] and not data["archived"][0]["enabled"]
    assert agentwork.claim_due(datetime.now() + timedelta(days=2)) == []            # archived: never runs

    job = client.post(f"/api/agent-work/{job['id']}/restore").json()
    assert not job["archived"] and job["enabled"] and job["next_run"].endswith("T08:30")

    assert client.delete(f"/api/agent-work/{job['id']}", params={"forever": True}).json()["name"] == "Morning brief"
    assert client.get("/api/agent-work").json() == {"jobs": [], "archived": [], "runs": []}
    assert client.delete(f"/api/agent-work/{job['id']}").status_code == 404


def test_a_finished_one_off_moves_to_the_archive(client):
    soon = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    once = client.post("/api/agent-work", json={"name": "Once", "prompt": "Do it", "schedule": f"once {soon}"}).json()
    daily = client.post("/api/agent-work", json={"name": "Daily", "prompt": "Do it", "schedule": "0 7 * * *"}).json()
    client.patch(f"/api/agent-work/{daily['id']}", json={"enabled": False})       # paused: stays in the list
    later = datetime.now() + timedelta(hours=1, minutes=1)
    assert agentwork.claim_due(later) == [once["id"]]
    assert {j["name"] for j in client.get("/api/agent-work").json()["jobs"]} == {"Daily", "Once"}   # waits for its run
    from nestwork import db
    conn = db.connect()
    with conn:   # the test claimed it an hour early: make its time really past
        conn.execute("UPDATE agent_jobs SET schedule = 'once 2020-01-01T08:00' WHERE id = ?", (once["id"],))
    conn.close()
    anyio.run(agentwork.run, once["id"], "schedule")
    data = client.get("/api/agent-work").json()
    assert [j["name"] for j in data["jobs"]] == ["Daily"]
    archived = data["archived"][0]
    assert archived["name"] == "Once" and archived["run_count"] == 1 and archived["last_run"]["status"] == "ok"

    restored = client.post(f"/api/agent-work/{once['id']}/restore").json()     # its time is past: back, paused
    assert not restored["archived"] and not restored["enabled"]
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 09:00")
    client.delete(f"/api/agent-work/{once['id']}")
    job = client.patch(f"/api/agent-work/{once['id']}", json={"schedule": f"once {tomorrow}", "enabled": True}).json()
    assert not job["archived"] and job["enabled"]                                  # a new date brings it back too


def test_finished_one_offs_from_before_the_archive_are_archived_at_startup(client):
    from nestwork import db
    conn = db.connect()
    with conn:
        job = agentwork.add(conn, "Old one-off", "x", "once 2026-10-14 08:00", now=MON)
        conn.execute("UPDATE agent_jobs SET enabled = 0, next_run = NULL, schedule = 'once 2020-01-01T08:00' WHERE id = ?",
                     (job,))
    conn.close()
    agentwork.recover()
    assert client.get("/api/agent-work").json()["archived"][0]["id"] == job


def test_scheduler_fires_once_and_catches_up_only_recent_runs(client):
    from nestwork import db
    conn = db.connect()
    with conn:
        daily = agentwork.add(conn, "Brief", "Brief me", "0 7 * * *", now=MON - timedelta(days=1))
        old = agentwork.add(conn, "Old", "Old", "0 7 * * *", now=MON - timedelta(days=1))
        conn.execute("UPDATE agent_jobs SET next_run = '2026-10-10T07:00' WHERE id = ?", (old,))
    conn.close()
    assert agentwork.claim_due(datetime(2026, 10, 12, 7, 0)) == [daily]       # due now
    assert agentwork.claim_due(datetime(2026, 10, 12, 7, 0)) == []            # once
    data = client.get("/api/agent-work").json()
    jobs = {j["name"]: j for j in data["jobs"]}
    assert jobs["Brief"]["next_run"] == "2026-10-13T07:00"
    assert jobs["Old"]["last_run"]["status"] == "skipped"                    # missed by days: not run late


def test_missed_runs_arent_run_late_and_are_counted(client):
    from nestwork import db
    conn = db.connect()
    with conn:
        night = agentwork.add(conn, "Night report", "Report", "30 23 * * *", now=MON - timedelta(days=3))
        morning = agentwork.add(conn, "Morning to-do curation", "Curate", "0 6 * * *", now=MON - timedelta(days=3))
        soon = agentwork.add(conn, "Brief", "Brief me", "0 7 * * *", now=MON - timedelta(days=3))
        conn.execute("UPDATE agent_jobs SET next_run = '2026-10-10T23:30' WHERE id = ?", (night,))
        conn.execute("UPDATE agent_jobs SET next_run = '2026-10-12T06:00' WHERE id = ?", (morning,))
        conn.execute("UPDATE agent_jobs SET next_run = '2026-10-12T07:00' WHERE id = ?", (soon,))
    conn.close()
    # Server started at 07:03 on the 12th: off for two nights and this morning's curation.
    assert agentwork.claim_due(datetime(2026, 10, 12, 7, 3)) == [soon]        # 3 min late: still runs
    jobs = {j["name"]: j for j in client.get("/api/agent-work").json()["jobs"]}
    assert jobs["Night report"]["last_run"]["status"] == "skipped" and "Missed 2 runs" in jobs["Night report"]["last_run"]["output"]
    assert jobs["Night report"]["next_run"] == "2026-10-12T23:30"
    assert "Missed 1 run " in jobs["Morning to-do curation"]["last_run"]["output"]


def test_the_agent_hears_about_missed_runs_once(client):
    from nestwork import db
    conn = db.connect()
    with conn:
        night = agentwork.add(conn, "Night report", "Report", "30 23 * * *", now=MON - timedelta(days=3))
        conn.execute("UPDATE agent_jobs SET next_run = '2026-10-10T23:30' WHERE id = ?", (night,))
        old = agentwork.add(conn, "Old", "Old", "0 6 * * *", now=MON)                 # next run still ahead
        conn.execute("INSERT INTO agent_job_runs (job_id, trigger, status, started_at, output) "    # logged by an older build
                     "VALUES (?, 'schedule', 'skipped', '2026-10-01T06:00', 'Skipped')", (old,))
    conn.close()
    agentwork.claim_due(datetime(2026, 10, 12, 7, 0))
    first = client.post("/api/chat", json={"message": "good morning"}).text    # the fake agent echoes its prompt
    assert "Scheduled tasks past due" in first and "- Night report (2 time(s))" in first and "Old" not in first
    assert "Don't run them by yourself" in first
    second = client.post("/api/chat", json={"message": "thanks"}).text
    assert "Scheduled tasks past due" not in second                           # told once


def test_a_run_posts_its_result_into_the_chat(client):
    job = client.post("/api/agent-work", json={"name": "Morning brief", "prompt": "Brief me on today",
                                               "schedule": "0 7 * * *"}).json()
    run_id = anyio.run(agentwork.run, job["id"], "manual")
    run = client.get(f"/api/agent-work/{job['id']}/runs").json()[0]
    assert run["id"] == run_id and run["status"] == "ok" and run["trigger"] == "manual"
    assert "Brief me on today" in run["output"] and "Scheduled task “Morning brief”" in run["output"]  # fake echoes
    assert "Let me check" not in run["output"]          # only the final answer, not the narration
    msg = client.get("/api/chat/history").json()[-1]
    assert msg["meta"]["agent_job"] == job["id"] and msg["meta"]["title"] == "Morning brief"
    assert client.get("/api/agent-work").json()["jobs"][0]["last_run"]["id"] == run_id
    # The next chat message tells the agent what it posted.
    r = client.post("/api/chat", json={"message": "thanks! more on the first point"})
    assert "Result of your scheduled task \\u201cMorning brief\\u201d" in r.text   # JSON-escaped quotes


def test_the_agent_can_read_earlier_results(client):
    job = client.post("/api/agent-work", json={"name": "Morning brief", "prompt": "Brief me on today",
                                               "schedule": "0 7 * * *"}).json()
    assert call("scheduled_task_results") == "No results yet."
    anyio.run(agentwork.run, job["id"], "schedule")
    anyio.run(agentwork.run, job["id"], "manual")
    out = call("scheduled_task_results", task_id=job["id"])
    assert out.count("(task #%d)" % job["id"]) == 2 and "Brief me on today" in out and "(run by hand)" in out
    assert call("scheduled_task_results", limit=1).count("---") == 0
    assert "no scheduled task #99" in call("scheduled_task_results", task_id=99)


def test_results_can_stay_out_of_the_chat(client):
    job = client.post("/api/agent-work", json={"name": "Quiet", "prompt": "Check", "schedule": "0 7 * * *",
                                               "deliver_chat": False}).json()
    anyio.run(agentwork.run, job["id"], "manual")
    assert client.get("/api/chat/history").json() == []
    assert client.get(f"/api/agent-work/{job['id']}/runs").json()[0]["status"] == "ok"


def call(tool: str, **args) -> str:
    from mcp.client.client import Client
    from nestwork.mcp_server import server

    async def go():
        async with Client(server) as c:
            result = await c.call_tool(tool, args)
            return "\n".join(part.text for part in result.content)
    return anyio.run(go)


def test_the_agent_schedules_its_own_tasks(client):
    out = call("schedule_task", name="Morning brief", prompt="Brief me", schedule="0 7 * * *")
    assert "every day at 07:00" in out and "Agent's Work" in out
    job = client.get("/api/agent-work").json()["jobs"][0]
    assert job["created_by"] != "you"
    assert "Not done" in call("schedule_task", name="x", prompt="y", schedule="every morning")
    assert "#%d “Morning brief”" % job["id"] in call("list_scheduled_tasks")

    refused = call("update_scheduled_task", task_id=job["id"], schedule="30 8 * * 1-5")   # hasn't read it
    assert refused.startswith("Not done: read the task") and "Brief me" in refused
    rev = job["revision"]
    assert f"revision {rev}" in refused and f"revision {rev}" in call("get_scheduled_task", task_id=job["id"])
    out = call("update_scheduled_task", task_id=job["id"], revision=rev, schedule="30 8 * * 1-5")
    assert "Weekdays at 08:30" in out and "Schedule: “0 7 * * *” → “30 8 * * 1-5”" in out
    stale = call("update_scheduled_task", task_id=job["id"], revision=rev, name="Brief")      # changed since
    assert stale.startswith("Not done: this task changed since you read it")
    assert "paused" in call("update_scheduled_task", task_id=job["id"], enabled=False)

    call("run_scheduled_task_now", task_id=job["id"])          # paused tasks can still be run by hand
    assert agentwork.claim_due() == [-job["id"]]
    assert not client.get("/api/agent-work").json()["jobs"][0]["enabled"]

    assert "archive" in call("delete_scheduled_task", task_id=job["id"])
    assert call("list_scheduled_tasks").startswith("No scheduled tasks yet.\n(1 more in the archive")
    assert "archived" in call("list_scheduled_tasks", archived=True)
    assert "next" in call("update_scheduled_task", task_id=job["id"], enabled=True)    # back from the archive
    assert "for good" in call("delete_scheduled_task", task_id=job["id"], forever=True)
    assert call("list_scheduled_tasks") == "No scheduled tasks yet."


def test_read_only_agents_cannot_schedule(client):
    client.put("/api/settings", json={"agent_access": "read_only"})
    assert "read only" in call("schedule_task", name="x", prompt="y", schedule="0 7 * * *")


def test_a_task_table_from_an_earlier_build_gets_its_new_columns(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "old.db"))
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE agent_jobs (id INTEGER PRIMARY KEY, name TEXT, prompt TEXT, schedule TEXT, "
                 "enabled INTEGER DEFAULT 1, deliver_chat INTEGER DEFAULT 1, next_run TEXT, created_by TEXT, created_at TEXT)")
    conn.commit()
    conn.close()
    from nestwork import db
    db.init_db()
    assert agentwork.claim_due() == []          # failed with "no such column: run_now" before


def test_a_selfie_from_a_task_comes_with_its_result(client):
    job = client.post("/api/agent-work", json={"name": "Nightly", "prompt": "Wrap up, selfie please",
                                               "schedule": "30 23 * * *"}).json()
    anyio.run(agentwork.run, job["id"], "schedule")
    run = client.get(f"/api/agent-work/{job['id']}/runs").json()[0]
    assert run["images"] == ["/api/generated/x-selfie.png"]
    msg = client.get("/api/chat/history").json()[-1]
    assert msg["meta"]["agent_job"] == job["id"] and msg["meta"]["images"] == ["/api/generated/x-selfie.png"]


def test_the_agent_sees_what_its_edit_removed(client):
    job = client.post("/api/agent-work", json={"name": "Brief", "schedule": "0 7 * * *",
                                               "prompt": "Check my deadlines.\nMention the scholarship.\nKeep it short."}).json()
    out = call("get_scheduled_task", task_id=job["id"])
    assert "Check my deadlines.\nMention the scholarship.\nKeep it short." in out        # in full, not cut short
    out = call("update_scheduled_task", task_id=job["id"], revision=job["revision"],
               prompt="Check my deadlines.\nKeep it short and warm.")
    assert "2 lines removed, 1 added" in out and "-Mention the scholarship." in out and "+Keep it short and warm." in out
    # Pausing or a new sound doesn't need the revision.
    assert "On: “True” → “False”" in call("update_scheduled_task", task_id=job["id"], enabled=False)
