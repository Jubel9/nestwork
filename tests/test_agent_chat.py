import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    fake = Path(__file__).with_name("fake_claude.py")
    wrapper = Path(fake).with_name("fake_claude.sh")
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("NESTWORK_CLAUDE_BIN", str(wrapper))
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


# --- memory files & regex fields ------------------------------------------------------
def test_files_are_created_with_marked_fields(client, tmp_path):
    data = client.get("/api/agent/files").json()
    shared = Path(data["files"]["shared_claude"]["path"])
    assert shared == tmp_path / "shared" / "CLAUDE.md"
    assert "@memory.md" in shared.read_text() and data["files"]["memory"]["exists"]
    assert data["fields"]["persona"]["tone"] == "Warm and encouraging"


def test_fields_are_written_in_place(client):
    client.put("/api/agent/fields/persona", json={"values": {"name": "Nova", "aliases": "Nov,  N"}})
    client.put("/api/agent/fields/about", json={"values": {"name": "Alex"}})
    data = client.get("/api/agent/files").json()
    assert data["fields"]["persona"]["name"] == "Nova" and data["fields"]["persona"]["aliases"] == "Nov, N"
    assert "- **Name:** Nova" in data["files"]["agent_claude"]["content"]
    assert data["profile"] == {"agent_name": "Nova", "user_name": "Alex"}


def test_existing_shared_claude_md_is_kept(client, tmp_path):
    shared = tmp_path / "shared" / "CLAUDE.md"
    shared.parent.mkdir(parents=True)
    shared.write_text("# My own rules\nAlways use British spelling.\n")
    client.get("/api/agent/files")
    text = shared.read_text()
    assert text.startswith("# My own rules\nAlways use British spelling.") and "nestwork:about-me:start" in text


def test_hand_edits_by_the_agent_are_read_back(client, tmp_path):
    client.get("/api/agent/files")
    f = Path(client.get("/api/agent/files").json()["files"]["agent_claude"]["path"])
    f.write_text(f.read_text().replace("- **Name:**", "- **Name:** Echo", 1))
    assert client.get("/api/agent/files").json()["fields"]["persona"]["name"] == "Echo"


def test_editor_refuses_stale_saves(client):
    memory = client.get("/api/agent/files").json()["files"]["memory"]
    Path(memory["path"]).write_text("changed by the agent")
    time.sleep(0.01)
    r = client.put("/api/agent/files/memory", json={"content": "mine", "base_mtime": memory["mtime"] - 5})
    assert r.status_code == 409
    fresh = client.get("/api/agent/files").json()["files"]["memory"]
    r = client.put("/api/agent/files/memory", json={"content": "mine", "base_mtime": fresh["mtime"]})
    assert r.status_code == 200 and r.json()["files"]["memory"]["content"] == "mine"


# --- choosing a session ----------------------------------------------------------------
def make_session(conn, last_at, tokens=40_000):
    conn.execute("INSERT INTO chat_sessions (id, started_at, last_at, context_tokens, turns) "
                 "VALUES ('s1', ?, ?, ?, 1)", (last_at, last_at, tokens))


def decide(force_new=False, **kw):
    from nestwork import chat, db
    db.init_db()
    conn = db.connect()
    try:
        make_session(conn, **kw)
        return chat.choose_session(conn, force_new)
    finally:
        conn.close()


def test_continues_the_latest_session(client):
    # A small session is resumed however long ago it was; a big one while its cache is still warm.
    for gap, tokens in ((60, 40_000), (3 * 86400, 40_000), (1200, 559_000)):
        from nestwork import db
        db.init_db()
        conn = db.connect()
        conn.execute("DELETE FROM chat_sessions")
        conn.commit()
        conn.close()
        d = decide(last_at=time.time() - gap, tokens=tokens)
        assert d["mode"] == "resume" and d["session_id"] == "s1", (gap, tokens)


def test_a_big_idle_session_isnt_resumed(client):
    from nestwork import chat
    d = decide(last_at=time.time() - chat.CACHE_TTL - 60, tokens=559_000)
    assert d["mode"] == "new" and d["session_id"] != "s1" and "559k tokens" in d["reason"]


def test_after_a_restore_the_next_message_starts_fresh(client):
    from nestwork import agent, chat, db
    db.init_db()
    conn = db.connect()
    try:
        make_session(conn, last_at=time.time() - 60)              # a session from before the restore
        agent.set_setting(conn, "chat_fresh_since", str(time.time()))
        d = chat.choose_session(conn)
        assert d["mode"] == "new" and "restored" in d["reason"]
        chat.remember_session(conn, d["session_id"], 20_000)      # the first message after it
        again = chat.choose_session(conn)
        assert again["mode"] == "resume" and again["session_id"] == d["session_id"]   # then it carries on
    finally:
        conn.close()


def test_new_chat_starts_fresh(client):
    d = decide(force_new=True, last_at=time.time() - 60)
    assert d["mode"] == "new" and d["session_id"] != "s1"


# --- a chat turn -------------------------------------------------------------------------
def events(text):
    out = []
    for chunk in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in chunk.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_turn_streams_and_is_saved(client):
    r = client.post("/api/chat", json={"message": "what's left for the game?"})
    ev = events(r.text)
    assert ev[0][0] == "session" and ev[0][1]["mode"] == "new"
    assert [e for e, _ in ev].count("delta") == 2 and ("tool", {"name": "mcp__nestwork__whats_left", "label": "Looking at what's left"}) in ev
    done = ev[-1][1]
    assert done["text"].startswith("Let me check.\n\nYou said: [") and done["text"].endswith("]\nwhat's left for the game?")
    assert done["cache_read"] == 1000
    hist = client.get("/api/chat/history").json()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    # The next message continues the same Claude session.
    ev2 = events(client.post("/api/chat", json={"message": "and the levels?"}).text)
    assert ev2[0][1]["mode"] == "resume" and ev2[0][1]["session_id"] == ev[0][1]["session_id"]
    ev3 = events(client.post("/api/chat", json={"message": "hi", "new_chat": True}).text)
    assert ev3[0][1]["mode"] == "new" and ev3[0][1]["session_id"] != ev[0][1]["session_id"]


def test_history_pages_by_id(client):
    from nestwork import chat, db
    conn = db.connect()
    for i in range(7):
        chat.save_message(conn, "s", "user" if i % 2 == 0 else "assistant", f"m{i}", {})
    conn.commit()
    ids = lambda q: [m["text"] for m in client.get(f"/api/chat/history{q}").json()]
    assert ids("?limit=3") == ["m4", "m5", "m6"]
    first = client.get("/api/chat/history?limit=3").json()[0]["id"]
    assert ids(f"?limit=3&before={first}") == ["m1", "m2", "m3"]
    assert ids(f"?after={first}") == ["m5", "m6"]


def test_files_sent_with_a_message_reach_the_agent(client):
    import base64
    png = base64.b64encode(b"\x89PNG fake picture").decode()
    r = client.post("/api/chat", json={"message": "what's in these?", "files": [
        {"name": "shot.png", "mime": "image/png", "data": f"data:image/png;base64,{png}"},
        {"name": "../report.pdf", "mime": "application/pdf", "data": base64.b64encode(b"%PDF-1.4").decode()}]})
    done = events(r.text)[-1][1]
    assert "Open each with the Read tool" in done["text"] and done["text"].endswith("\n\nwhat's in these?")
    files = client.get("/api/chat/history").json()[0]["meta"]["files"]
    assert [f["name"] for f in files] == ["shot.png", "report.pdf"]       # no path tricks in stored names
    assert all(f"uploads/{f['id']}" in done["text"] for f in files)       # the agent is told where they are
    pic = client.get(f"/api/chat/files/{files[0]['id']}")
    assert pic.status_code == 200 and pic.content == b"\x89PNG fake picture" and pic.headers["content-disposition"].startswith("inline")


def test_files_alone_can_be_sent_and_unsafe_ones_download(client):
    import base64
    svg = base64.b64encode(b"<svg onload='alert(1)'/>").decode()
    r = client.post("/api/chat", json={"message": "", "files": [{"name": "x.svg", "data": svg}]})
    assert r.status_code == 200
    fid = client.get("/api/chat/history").json()[0]["meta"]["files"][0]["id"]
    assert client.get(f"/api/chat/files/{fid}").headers["content-disposition"].startswith("attachment")
    assert client.get("/api/chat/files/..%2F..%2Fnestwork.db").status_code == 404
    assert client.post("/api/chat", json={"message": "", "files": []}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "files": [{"name": "a", "data": "not base64!"}]}).status_code == 422


def test_files_tab_lists_uploads_by_category(client):
    import base64
    b64 = lambda b: base64.b64encode(b).decode()
    client.post("/api/chat", json={"message": "first", "files": [{"name": "a.png", "mime": "image/png", "data": b64(b"png")}]})
    client.post("/api/chat", json={"message": "second", "files": [
        {"name": "b.pdf", "mime": "application/pdf", "data": b64(b"%PDF")},
        {"name": "notes.docx", "data": b64(b"doc")}, {"name": "c.zip", "data": b64(b"zip")}]})
    r = client.get("/api/chat/files").json()
    assert [(f["name"], f["kind"]) for f in r["items"]] == [
        ("b.pdf", "pdf"), ("notes.docx", "document"), ("c.zip", "other"), ("a.png", "image")]   # newest message first
    assert r["items"][0]["message"]["text"] == "second" and r["items"][-1]["url"].startswith("/api/chat/files/")
    assert [k["key"] for k in r["kinds"]] == ["image", "pdf", "document", "other"]


def test_each_message_carries_the_time_it_was_sent(client):
    client.put("/api/agent/fields/about", json={"values": {"timezone": "Europe/Berlin"}})
    r = client.post("/api/chat", json={"message": "what time is it?"})
    done = events(r.text)[-1][1]
    assert done["text"].endswith("(Europe/Berlin)]\nwhat time is it?")   # the fake agent echoes its prompt
    hist = client.get("/api/chat/history").json()
    assert hist[0]["text"] == "what time is it?"                     # the stamp isn't shown in the chat


def test_effort_setting_reaches_the_cli(client):
    from nestwork import chat
    session = {"mode": "new", "session_id": "s9"}
    assert "--effort" not in chat.command(session, client.get("/api/settings").json(), "Nova")   # default: not passed
    assert client.put("/api/settings", json={"agent_effort": "turbo"}).status_code == 422
    s = client.put("/api/settings", json={"agent_effort": "high"}).json()
    cmd = chat.command(session, s, "Nova")
    assert cmd[cmd.index("--effort") + 1] == "high"
    client.put("/api/settings", json={"agent_effort": ""})


def test_tool_levels_allow_skills_and_chrome(client):
    from nestwork import chat
    session = {"mode": "new", "session_id": "s9"}
    allowed = lambda c: c[c.index("--allowedTools") + 1].split(",")
    broad = chat.command(session, {"agent_tools": "broad"}, "Nova")
    assert "Skill" in allowed(broad) and "mcp__claude-in-chrome" in allowed(broad) and "--chrome" in broad
    web = chat.command(session, {"agent_tools": "web"}, "Nova")
    assert "Skill" in allowed(web) and "--chrome" not in web
    assert "Skill" not in allowed(chat.command(session, {"agent_tools": "contained"}, "Nova"))
    assert chat.tool_label("mcp__claude-in-chrome__navigate", {}) == "Using your Chrome"
