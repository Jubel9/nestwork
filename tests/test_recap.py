"""recent_chats / day_recap / search_chats: a new chat can look up what already happened instead of asking."""

from datetime import date, datetime, timedelta

import pytest

from tests import test_mcp
from tests.test_mcp import block_id, call

api = test_mcp.api   # the shared fixture (assigned, not imported, so tests can take it as an argument)


def _db():
    from nestwork import db
    return db.connect()


def _chat(session: str, role: str, text: str, when: str = "now"):
    conn = _db()
    conn.execute("INSERT INTO chat_messages (session_id, role, text, meta, created_at) VALUES (?, ?, ?, '{}', datetime(?))",
                 (session, role, text, when))
    conn.commit()
    conn.close()


def test_recap_gathers_the_day(api):
    bid = block_id(api, "Backstory")
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn = _db()
    conn.execute("INSERT INTO completions (block_id, title, project_title, done_at) VALUES (?, 'Backstory', 'Game', ?)", (bid, now))
    conn.execute("INSERT INTO focus_sessions (title, project_title, started_at, ended_at, planned_min, focus_min, status, rating, note) "
                 "VALUES ('Backstory', 'Game', ?, ?, 25, 25, 'completed', 4, 'went well')", (now, now))
    conn.execute("INSERT INTO block_notes (block_id, text, created_at) VALUES (?, 'Passport photo done', ?)", (bid, now))
    conn.execute("INSERT INTO block_notes (block_id, kind, text, created_at) VALUES (?, 'event', 'Moved somewhere', ?)", (bid, now))
    conn.commit()
    conn.close()
    _chat("s1", "user", "I got the passport photo this morning")
    _chat("s1", "assistant", "Nice, only the translation left")
    _chat("s2", "user", "hi again")

    out = call("day_recap")
    assert "Today so far" in out
    assert "## Finished (1)" in out and "- Game (" in out and "Backstory" in out
    assert "## Focus (25 min)" in out and "felt 4/5" in out and "went well" in out
    assert "Passport photo done" in out and "Moved somewhere" not in out
    assert "I got the passport photo" in out and "Nice, only" not in out
    assert "2 chats" in out and "— new chat —" in out


def test_recap_other_days(api):
    assert "Nothing recorded" in call("day_recap", day="yesterday")
    assert "Not done" in call("day_recap", day="someday")
    _chat("old", "user", "talked about the language school", (date.today() - timedelta(days=2)).isoformat() + " 12:00:00")
    assert "language school" in call("day_recap", day="2 days ago")


def test_search_chats(api):
    _chat("a", "user", "My landlord needs the signed form for the rent contract")
    _chat("a", "assistant", "Got it")
    out = call("search_chats", query="rent form")
    assert "User:" in out and "rent contract" in out and "Got it" not in out
    assert "Nothing" in call("search_chats", query="zeppelin")
    assert "Not done" in call("search_chats", query="  ")


def test_pointer_only_when_there_is_history(api):
    from nestwork import recap
    conn = _db()
    assert recap.pointer(conn) is None
    conn.close()
    _chat("s1", "user", "morning")
    conn = _db()
    line = recap.pointer(conn)
    conn.close()
    assert "1 chat messages" in line and "recent_chats" in line and "day_recap" in line


def test_pointer_reads_last_chats_even_on_a_quiet_day(api):
    from nestwork import recap
    _chat("old", "user", "see you tomorrow", (date.today() - timedelta(days=2)).isoformat() + " 12:00:00")
    conn = _db()
    line = recap.pointer(conn)
    conn.close()
    assert "Standard first step" in line and "recent_chats" in line
    assert "day_recap" not in line and "Today so far" not in line


def test_recent_chats_shows_both_sides(api):
    assert "No earlier conversations" in call("recent_chats")
    _chat("a", "user", "I finally sent the letter", (date.today() - timedelta(days=1)).isoformat() + " 12:00:00")
    _chat("a", "assistant", "Proud of you, now rest", (date.today() - timedelta(days=1)).isoformat() + " 12:00:00")
    _chat("b", "user", "back again")
    out = call("recent_chats")
    assert out.index("User: I finally sent the letter") < out.index("You: Proud of you") < out.index("— new chat —") \
        < out.index("User: back again")
    only = call("recent_chats", limit=1)
    assert "back again" in only and "letter" not in only


def test_agent_file_gets_the_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "x.db"))
    from nestwork import persona
    p = persona.paths()
    p["agent_claude"].parent.mkdir(parents=True, exist_ok=True)
    p["agent_claude"].write_text("# Nestwork agent\n\n## Memory\n\n- `remember` it.\n", encoding="utf-8")
    persona.ensure_files()
    text = p["agent_claude"].read_text(encoding="utf-8")
    assert text.index("## Look it up, don't ask") < text.index("## Memory")
    persona.ensure_files()
    assert p["agent_claude"].read_text(encoding="utf-8").count("## Look it up") == 1


def test_older_recall_rules_get_the_standard_first_step(tmp_path, monkeypatch):
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "x.db"))
    from nestwork import persona
    p = persona.paths()
    p["agent_claude"].parent.mkdir(parents=True, exist_ok=True)
    p["agent_claude"].write_text("# Nestwork agent\n\n## Look it up, don't ask\n\n- Old rule.\n\n## Memory\n\n"
                                 "- `remember` it, until= a date.\n", encoding="utf-8")
    persona.ensure_files()
    text = p["agent_claude"].read_text(encoding="utf-8")
    assert "Old rule" not in text and "`recent_chats`" in text and text.count("## Look it up") == 1
    assert text.index("## Look it up") < text.index("## Memory")


# --- memory lines that expire -------------------------------------------------------
def test_memory_until(api):
    from nestwork import persona
    soon = (date.today() + timedelta(days=3)).isoformat()
    assert "until" in call("remember", text="Passport photo left", heading="Notes", until=soon)
    assert "Not saved" in call("remember", text="old news", until="2020-01-01")
    assert "Not saved" in call("remember", text="Passport photo left")          # same fact, with or without a date
    path = persona.paths()["memory"]
    assert f"- Passport photo left (until {soon})" in path.read_text()
    assert "stays for good" in call("revise_memory", old="passport", until="")
    assert "(until" not in path.read_text()
    call("revise_memory", old="passport", until=soon)
    call("revise_memory", old="passport", new="Only translation left")          # keeps its end date
    assert f"- Only translation left (until {soon})" in path.read_text()
    call("remember", text="Likes jazz", heading="Preferences")
    gone = persona.prune_memory(date.today() + timedelta(days=4))
    text = path.read_text()
    assert gone == [f"- Only translation left (until {soon})"] and "Only translation" not in text and "jazz" in text
    assert persona.prune_memory(date.today() + timedelta(days=4)) == []


# --- the activity log -----------------------------------------------------------------
def test_activity_log(api):
    from nestwork import activities
    bid = block_id(api, "Backstory")
    now = datetime(2026, 10, 13, 21, 0)
    assert activities.parse_time("19:05", now) == "2026-10-13T19:05"
    assert activities.parse_time("yesterday 23:10", now) == "2026-10-12T23:10"
    assert activities.parse_time("2026-10-14T09:00", now) == "2026-10-14T09:00"
    with pytest.raises(ValueError):
        activities.parse_time("after lunch", now)

    out = call("log_activity", title="Passport photo trip", start="00:00", note="with Sam")
    assert "Logged #1" in out and "still going" in out
    out = call("log_activity", title="Studied maths", start="00:00", end="00:01", block_id=bid)
    assert "(1 min)" in out and "to-do: Backstory" in out and "Still going too: #1" in out
    assert "Doing now (activity log): #1" in call("today")
    assert "can't end before" in call("log_activity", title="x", start="00:01", end="00:00")
    assert "hasn't started" in call("log_activity", title="x", start="2099-01-01 10:00")

    out = call("finish_activity", note="all photos done")
    assert "Finished #1" in out and "with Sam all photos done" in out
    assert "Nothing is still going" in call("finish_activity")

    recap_text = call("day_recap")
    assert "## Activity log" in recap_text and "Passport photo trip" in recap_text and "Studied maths" in recap_text
    assert "Now #2" in call("change_activity", activity_id=2, title="Studied maths for ML")
    assert "Deleted activity #2" in call("change_activity", activity_id=2, delete=True)
    assert "Studied maths" not in call("day_recap")

    # an agent's log can be undone with ⌘Z like its other changes
    call("log_activity", title="Oops")
    api.post("/api/undo")
    from nestwork import db
    conn = db.connect()
    assert not conn.execute("SELECT 1 FROM activities WHERE title = 'Oops'").fetchone()
    conn.close()


def test_pointer_mentions_what_is_still_going(api):
    from nestwork import recap
    call("log_activity", title="Walking to the post office", start="00:00")
    conn = _db()
    line = recap.pointer(conn)
    conn.close()
    assert "1 activities logged" in line and "Still going: “Walking to the post office”" in line


def test_activities_on_the_calendar(api):
    call("log_activity", title="Passport photo trip", start="00:00", end="00:30", note="with Sam")
    call("log_activity", title="Walking to the post office", start="00:05")
    day = api.get("/api/calendar", params={"days": 1}).json()["days"][0]
    acts = {a["title"]: a for a in day["activities"]}
    assert acts["Passport photo trip"]["real_minutes"] == 30 and not acts["Passport photo trip"]["running"]
    assert acts["Walking to the post office"]["running"] and acts["Walking to the post office"]["minutes"] >= 10
    assert "Passport photo trip (activity #1)" in call("calendar", days=1)

    aid = acts["Walking to the post office"]["id"]
    today = date.today().isoformat()
    r = api.patch(f"/api/activities/{aid}", json={"end": f"{today} 00:20", "note": "done"})
    assert r.status_code == 200 and r.json()["activity"]["ended_at"] == f"{today}T00:20"
    assert api.patch(f"/api/activities/{aid}", json={"end": f"{today} 00:01"}).status_code == 400
    assert api.delete(f"/api/activities/{aid}").status_code == 200
    assert api.delete(f"/api/activities/{aid}").status_code == 404
    api.post("/api/undo")                                   # deleting it from the calendar can be undone
    assert any(a["id"] == aid for a in api.get("/api/calendar", params={"days": 1}).json()["days"][0]["activities"])
