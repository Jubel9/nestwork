import sys
from datetime import datetime, timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    fake = Path(__file__).with_name("fake_claude.py")
    wrapper = fake.with_name("fake_claude.sh")
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("NESTWORK_CLAUDE_BIN", str(wrapper))
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Thesis", "template": "blank", "stages": ["To do", "Done"]}).json()["block"]["id"]
        c.pid = pid
        yield c


def todo(client, title, due_date, due_time=None, **extra):
    bid = client.post("/api/blocks", json={"title": title, "parent_id": client.pid, "notes": "Bring the draft"}).json()["block"]["id"]
    client.patch(f"/api/blocks/{bid}", json={"due_date": due_date, **({"due_time": due_time} if due_time else {}), **extra})
    return bid


def collect(now):
    from nestwork import reminders
    return reminders.collect_due(now)


def test_appointment_reminders_go_out_once_each(client):
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    todo(client, "Meet Prof. Tan", tomorrow.isoformat(), "10:30")
    day_before_morning = datetime.combine(tomorrow - timedelta(days=1), datetime.min.time()).replace(hour=9, minute=5)
    due = collect(day_before_morning)
    assert [d["choice"] for d in due] == ["day_before"]
    assert collect(day_before_morning) == []                       # sent once
    on_the_day = datetime.combine(tomorrow, datetime.min.time())
    assert [d["choice"] for d in collect(on_the_day.replace(hour=8, minute=1))] == ["morning"]
    assert [d["choice"] for d in collect(on_the_day.replace(hour=9, minute=35))] == ["1h"]
    assert collect(on_the_day.replace(hour=11)) == []              # it's over: nothing more


def test_missed_moments_only_send_the_latest(client):
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    todo(client, "Meet Prof. Tan", tomorrow.isoformat(), "10:30")
    late = datetime.combine(tomorrow, datetime.min.time()).replace(hour=9, minute=45)   # server was off until now
    assert [d["choice"] for d in collect(late)] == ["1h"]
    assert collect(late + timedelta(minutes=1)) == []


def test_reminder_choices_and_done_items(client):
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    bid = todo(client, "Submit form", tomorrow.isoformat(), remind=[])
    day_before = datetime.combine(tomorrow - timedelta(days=1), datetime.min.time()).replace(hour=10)
    assert collect(day_before) == []                               # reminders turned off
    client.patch(f"/api/blocks/{bid}", json={"default_remind": True})
    done = client.get(f"/api/blocks/{client.pid}").json()["stages"][-1]["key"]
    client.post(f"/api/blocks/{bid}/move", json={"status": done})
    assert collect(day_before) == []                               # finished things aren't reminded


def test_quick_text_mentions_the_essentials(client):
    from nestwork import reminders
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    todo(client, "Meet Prof. Tan", tomorrow.isoformat(), "10:30")
    item = collect(datetime.combine(tomorrow - timedelta(days=1), datetime.min.time()).replace(hour=9, minute=5))[0]
    text = reminders.quick_text(item, datetime.combine(tomorrow - timedelta(days=1), datetime.min.time()).replace(hour=9))
    assert text.startswith("⏰ **Tomorrow at 10:30:** **Meet Prof. Tan** (Thesis)") and "Bring the draft" in text


def test_one_off_reminders_and_delivery_into_chat(client):
    when = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
    assert client.post("/api/reminders", json={"text": "Email the professor", "remind_at": when}).status_code == 201
    from nestwork import reminders
    due = reminders.collect_due()
    assert due[0]["text"] == "Email the professor"
    anyio.run(reminders.deliver, due[0], "quick")
    hist = client.get("/api/chat/history").json()
    assert hist[-1]["meta"]["reminder"] and "Email the professor" in hist[-1]["text"]
    v = client.get("/api/version").json()
    assert v["chat_unread"] == 1
    client.post("/api/chat/seen", json={"last_id": v["chat_last_id"]})
    assert client.get("/api/version").json()["chat_unread"] == 0


def test_agent_hears_about_reminders_the_user_saw(client):
    from nestwork import reminders
    reminders.save_reminder_message("⏰ Tomorrow at 10:30: Meet Prof. Tan", {"kind": "due", "block_id": None, "title": "x"})
    r = client.post("/api/chat", json={"message": "thanks, help me prepare"})
    assert "Reminders you showed the user" in r.text and "Meet Prof. Tan" in r.text   # fake claude echoes its prompt


def test_agent_written_reminders(client):
    from nestwork import reminders
    item = {"kind": "custom", "text": "Email the professor", "block_id": None}
    anyio.run(reminders.deliver, item, "agent")
    hist = client.get("/api/chat/history").json()
    assert hist[-1]["role"] == "assistant" and hist[-1]["meta"]["reminder"]
    assert "Automatic reminder" in hist[-1]["text"]          # the fake agent echoed the hidden prompt
    assert all(m["role"] != "event" for m in hist)           # the hidden prompt itself isn't shown


def test_upcoming_lists_both_kinds(client):
    in3 = (datetime.now() + timedelta(days=3)).date()
    todo(client, "Meet Prof. Tan", in3.isoformat(), "10:30")
    client.post("/api/reminders", json={"text": "Print slides", "remind_at": f"{in3}T08:00"})
    up = client.get("/api/reminders").json()["upcoming"]
    assert {u["kind"] for u in up} == {"due", "custom"} and up == sorted(up, key=lambda u: u["at"])


def test_agent_changes_and_deletes_reminders(client):
    from nestwork import mcp_server
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    rid = int(mcp_server.remind_me("Call the bank", f"{soon} 18:00").split("#")[1].split(" ")[0])
    assert mcp_server.change_reminder(rid, "Call the bank about the card", f"{soon} 18:30") == \
        f"Reminder #{rid} is now {soon} 18:30: Call the bank about the card"
    assert f"one-off #{rid}: Call the bank about the card" in mcp_server.upcoming_reminders()
    assert "Not done" in mcp_server.change_reminder(rid, at="half past six")
    assert mcp_server.delete_reminder(rid).startswith(f"Deleted reminder #{rid}")
    assert "Not done: There's no reminder" in mcp_server.delete_reminder(rid)
    assert "one-off" not in mcp_server.upcoming_reminders()

    bid = todo(client, "Submit form", soon, "10:00")
    assert "won't remind" in mcp_server.set_due_reminders(bid, [])
    assert all(u.get("block_id") != bid for u in client.get("/api/reminders").json()["upcoming"])
    assert "15 minutes before" in mcp_server.set_due_reminders(bid, ["15m"])
    assert "default" in mcp_server.set_due_reminders(bid, ["default"])
    assert "Not done" in mcp_server.set_due_reminders(bid, ["tomorrow"])
    assert "Not done: There is no block" in mcp_server.set_due_reminders(999999, [])
