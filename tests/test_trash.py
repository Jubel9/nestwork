"""Deleting goes to the trash; restoring brings everything back. Daily automatic backups."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from nestwork import db, reset, trash


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(c, parent, title):
    return c.post("/api/blocks", json={"title": title, "parent_id": parent}).json()["block"]["id"]


def project(c):
    pid = c.post("/api/blocks", json={"title": "Game", "stages": ["To do", "Doing", "Done"]}).json()["block"]["id"]
    chars = add(c, pid, "Characters")
    cloud = add(c, chars, "Cloud")
    story, sprites = add(c, cloud, "Backstory"), add(c, cloud, "Sprites")
    music = add(c, pid, "Music")
    stages = c.get(f"/api/blocks/{pid}").json()["stages"]
    done = stages[-1]["key"]
    # Sprites wait for the backstory (inside), music waits for the sprites (a rule from outside).
    c.put(f"/api/blocks/{sprites}/rules", json={"rules": [{"depends_on_id": story, "required_stage": done}]})
    c.put(f"/api/blocks/{music}/rules", json={"rules": [{"depends_on_id": sprites, "required_stage": done}]})
    c.post(f"/api/blocks/{story}/notes", json={"text": "Born in a storm"})
    c.post("/api/reminders", json={"text": "Draft it", "remind_at": "2099-01-01T09:00", "block_id": story})
    s = c.post("/api/focus/start", json={"block_id": story, "planned_min": 25}).json()
    c.post(f"/api/focus/{s['id']}/finish", json={"focus_min": 25, "completed": True})
    return dict(pid=pid, chars=chars, cloud=cloud, story=story, sprites=sprites, music=music, done=done)


def test_delete_and_restore_everything(api):
    p = project(api)
    r = api.delete(f"/api/blocks/{p['chars']}").json()
    assert r["trash_id"]
    assert api.get(f"/api/blocks/{p['story']}").status_code == 404
    items = api.get("/api/trash").json()["items"]
    assert items[0]["title"] == "Characters" and items[0]["items"] == 3 and items[0]["path"] == "Game"

    back = api.post(f"/api/trash/{r['trash_id']}/restore").json()
    assert back["block_id"] == p["chars"]
    story = api.get(f"/api/blocks/{p['story']}").json()["block"]
    assert story["title"] == "Backstory" and story["notes_count"] == 1 and story["spent_min"] == 25
    assert api.get(f"/api/blocks/{p['music']}").json()["block"]["rules"][0]["depends_on_id"] == p["sprites"]
    assert api.get(f"/api/blocks/{p['sprites']}").json()["block"]["locked"]
    assert any(u["block_id"] == p["story"] for u in api.get("/api/reminders?days=100000").json()["upcoming"])
    assert api.get("/api/trash").json()["items"] == []


def test_restore_into_the_project_when_the_parent_is_gone(api):
    p = project(api)
    t1 = api.delete(f"/api/blocks/{p['story']}").json()["trash_id"]
    api.delete(f"/api/blocks/{p['chars']}")
    r = api.post(f"/api/trash/{t1}/restore").json()
    assert "top of “Game”" in " ".join(r["notices"])
    assert api.get(f"/api/blocks/{p['story']}").json()["block"]["parent_id"] == p["pid"]


def test_a_deleted_project_comes_back_whole(api):
    p = project(api)
    t = api.delete(f"/api/blocks/{p['pid']}").json()["trash_id"]
    assert api.get("/api/projects").json() == []
    assert api.get("/api/trash").json()["items"][0]["is_project"] is True
    api.post(f"/api/trash/{t}/restore")
    assert [x["title"] for x in api.get("/api/projects").json()] == ["Game"]
    assert api.get(f"/api/blocks/{p['music']}").json()["block"]["rules"]


def test_a_project_can_only_come_back_after_its_project(api):
    p = project(api)
    t = api.delete(f"/api/blocks/{p['story']}").json()["trash_id"]
    api.delete(f"/api/blocks/{p['pid']}")
    assert api.post(f"/api/trash/{t}/restore").status_code == 409


def test_rules_that_would_now_loop_are_left_out(api):
    p = project(api)
    t = api.delete(f"/api/blocks/{p['cloud']}").json()["trash_id"]
    # Meanwhile: Characters waits for Music. Bringing back Sprites → Music would close a loop.
    r = api.put(f"/api/blocks/{p['chars']}/rules", json={"rules": [{"depends_on_id": p["music"], "required_stage": p["done"]}]})
    assert r.status_code == 200
    back = api.post(f"/api/trash/{t}/restore").json()
    assert "would make a loop" in " ".join(back["notices"])
    assert api.get(f"/api/blocks/{p['music']}").json()["block"]["rules"] == []
    assert api.get(f"/api/blocks/{p['sprites']}").json()["block"]["rules"]     # the inside rule stays


def test_forget_and_purge(api):
    p = project(api)
    t = api.delete(f"/api/blocks/{p['music']}").json()["trash_id"]
    api.delete(f"/api/blocks/{p['story']}")
    assert api.delete(f"/api/trash/{t}").json()["deleted"] == 1
    conn = db.connect()
    with conn:
        assert trash.purge_old(conn) == 0
        assert trash.purge_old(conn, datetime.now() + timedelta(days=31)) == 1
    conn.close()
    assert api.get("/api/trash").json()["items"] == []
    assert api.post("/api/trash/999/restore").status_code == 404


def test_agent_deletes_go_to_the_trash(api):
    from nestwork import agent
    p = project(api)
    api.put("/api/settings", json={"agent_access": "full", "allow_delete": True})
    assert agent.request("delete", {"block_id": p["music"]}).startswith("Done")
    assert api.get("/api/trash").json()["items"][0]["deleted_by"] == "agent"


def test_daily_automatic_backup(api, monkeypatch):
    monkeypatch.setenv("NESTWORK_AUTO_BACKUP", "1")
    conn = db.connect()
    now = datetime(2026, 10, 1, 12)
    assert reset.auto_backup(conn, now) is None                 # nothing to keep yet
    project(api)
    first = reset.auto_backup(conn, now)
    assert first and not (first / "agent").exists()
    assert reset.auto_backup(conn, now + timedelta(hours=5)) is None       # one a day
    for day in range(1, reset.KEEP_AUTO + 3):
        reset.auto_backup(conn, now + timedelta(days=day))
    conn.close()
    autos = [b for b in reset.list_backups() if b["auto"]]
    assert len(autos) == reset.KEEP_AUTO and autos[0]["projects"] == 1
    made = api.post("/api/backups").json()["backup"]                        # by hand: not counted in KEEP_AUTO
    assert next(b for b in api.get("/api/backups").json()["backups"] if b["name"] == made)["auto"] is False


def test_backups_older_than_a_month_are_deleted(api):
    project(api)
    conn = db.connect()
    now = datetime(2026, 10, 1, 12)
    old = reset.backup(conn, "by hand", ["projects"], now=now - reset.MAX_AGE - timedelta(hours=1))
    young = reset.backup(conn, "before a reset", ["projects"], now=now - reset.MAX_AGE + timedelta(hours=1))
    auto = reset.backup(conn, "daily automatic backup", ["projects"], auto=True, now=now - timedelta(days=40))
    conn.close()
    assert sorted(reset.prune_old(now)) == sorted([old.name, auto.name])
    assert not old.exists() and not auto.exists() and young.exists()
    assert reset.prune_old(now) == []
    assert api.get("/api/backups").json()["max_age_days"] == 30
