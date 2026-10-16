"""Notification sounds: the built-in notifications, your own, uploaded files, and per-task choices."""

import base64

import pytest
from fastapi.testclient import TestClient

from tests.test_agentwork import call

MP3 = "data:audio/mpeg;base64," + base64.b64encode(b"ID3\x03\x00fake mp3 bytes").decode()


@pytest.fixture()
def client():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def test_built_in_notifications(client):
    lib = client.get("/api/sounds").json()
    assert [n["key"] for n in lib["notifications"]] == ["chat_open", "chat_away", "task", "reminder", "focus_end", "break_end"]
    assert len({n["sound"] for n in lib["notifications"]}) == 6          # all different by default
    assert {"pop", "chime", "alarm", "sunrise"} <= {p["key"] for p in lib["presets"]}
    task = lib["notifications"][2]
    assert client.patch(f"/api/notifications/{task['id']}", json={"sound": "preset:alarm", "volume": 0.3}).json()["volume"] == 0.3
    assert client.patch(f"/api/notifications/{task['id']}", json={"sound": "preset:nope"}).status_code == 400
    assert client.delete(f"/api/notifications/{task['id']}").status_code == 400   # built-in ones stay


def test_upload_and_use_your_own_sound(client):
    f = client.post("/api/sounds/files", json={"name": "my alarm.mp3", "data": MP3, "mime": "audio/mpeg"}).json()
    assert f["name"] == "my alarm"
    assert client.get(f["url"]).content.startswith(b"ID3")
    assert client.post("/api/sounds/files", json={"name": "notes.txt", "data": MP3}).status_code == 400

    wake = client.post("/api/notifications", json={"name": "Wake up", "sound": f"file:{f['id']}"}).json()
    assert client.post("/api/notifications", json={"name": "wake UP", "sound": "preset:pop"}).status_code == 400
    away = next(n for n in client.get("/api/sounds").json()["notifications"] if n["key"] == "chat_away")
    client.patch(f"/api/notifications/{away['id']}", json={"sound": f"file:{f['id']}"})

    # Deleting the file sends everything that used it back to a default sound.
    r = client.delete(f"/api/sounds/files/{f['id']}").json()
    assert set(r["changed"]) == {"Wake up", "Message while away"}
    notes = {n["name"]: n["sound"] for n in client.get("/api/sounds").json()["notifications"]}
    assert notes["Wake up"] == "preset:chime" and notes["Message while away"] == "preset:chime"
    assert client.get(f["url"]).status_code == 404
    assert wake["id"]


def test_each_task_picks_its_notification(client):
    wake = client.post("/api/notifications", json={"name": "Wake up", "sound": "preset:alarm"}).json()
    job = client.post("/api/agent-work", json={"name": "Brief", "prompt": "Brief me", "schedule": "0 7 * * *",
                                               "notify": str(wake["id"])}).json()
    assert job["notify"] == str(wake["id"]) and job["notify_label"] == "Wake up"
    job = client.patch(f"/api/agent-work/{job['id']}", json={"notify": "none"}).json()
    assert job["notify_label"] == "silent"
    job = client.patch(f"/api/agent-work/{job['id']}", json={"notify": ""}).json()
    assert job["notify"] is None and job["notify_label"] == "default"
    assert client.post("/api/agent-work", json={"name": "x", "prompt": "y", "schedule": "0 7 * * *",
                                                "notify": "999"}).status_code == 400

    client.patch(f"/api/agent-work/{job['id']}", json={"notify": str(wake["id"])})
    client.delete(f"/api/notifications/{wake['id']}")                     # its tasks go back to the default
    assert client.get("/api/agent-work").json()["jobs"][0]["notify"] is None


def test_the_agent_picks_a_notification_by_name(client):
    client.post("/api/notifications", json={"name": "Sleep", "sound": "preset:soft"})
    out = call("schedule_task", name="Wind down", prompt="Tell me to sleep", schedule="0 23 * * *", notification="sleep")
    assert "Scheduled" in out
    assert "sound: Sleep" in call("list_scheduled_tasks")
    bad = call("schedule_task", name="x", prompt="y", schedule="0 7 * * *", notification="Party")
    assert "Not done" in bad and "“Sleep”" in bad
    job_id = client.get("/api/agent-work").json()["jobs"][0]["id"]
    assert "sound: silent" in call("update_scheduled_task", task_id=job_id, notification="silent")


def test_only_one_page_plays_a_message_sound(client):
    # Every open page (tabs, other browsers, the phone) asks; only the first one plays it.
    assert client.post("/api/sounds/claim/987654").json() == {"play": True}
    assert client.post("/api/sounds/claim/987654").json() == {"play": False}
    assert client.post("/api/sounds/claim/987655").json() == {"play": True}
