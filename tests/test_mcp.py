"""The MCP server: reading tools, and how changes follow the Agent access setting."""

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "test.db"))
    from nestwork.main import app
    with TestClient(app) as c:
        pid = c.post("/api/blocks", json={"title": "Game", "template": "game"}).json()["block"]["id"]
        c.pid = pid
        yield c


def call(tool: str, **args) -> str:
    from nestwork.mcp_server import server

    async def go():
        async with Client(server) as client:
            result = await client.call_tool(tool, args)
            return "\n".join(part.text for part in result.content)
    return anyio.run(go)


def block_id(api, title: str) -> int:
    def walk(n):
        if n["title"] == title:
            return n["id"]
        return next((r for c in n["children"] if (r := walk(c))), None)
    return walk(api.get(f"/api/blocks/{api.pid}").json()["tree"])


def stage(api, bid):
    return api.get(f"/api/blocks/{bid}").json()["block"]["status"]


def test_tools_are_listed():
    from nestwork.mcp_server import server

    async def go():
        async with Client(server) as client:
            return {t.name: t for t in (await client.list_tools()).tools}
    tools = anyio.run(go)
    assert {"today", "whats_left", "find", "get_block", "list_projects", "add_todo", "move", "delete"} <= set(tools)
    assert tools["whats_left"].annotations.read_only_hint is True
    assert tools["delete"].annotations.destructive_hint is True


def test_reading(api):
    assert "Game" in call("list_projects")
    left = call("whats_left", block_id=api.pid)
    assert "Backstory" in left and "3D model" in left and "waits until" in left
    assert f"[id {block_id(api, 'Backstory')}]" in call("find", query="cloud backstory")
    assert "Ready to start" in call("today")
    assert "Location: Game › Characters › Cloud" in call("get_block", block_id=block_id(api, "Backstory"))


def test_ask_first_creates_a_proposal_that_applies_on_approval(api):
    backstory = block_id(api, "Backstory")
    reply = call("move", block_id=backstory, phase="in review")      # phase by name, any case
    assert reply.startswith("Proposed")
    assert stage(api, backstory) == "backlog"                         # not applied yet
    proposals = api.get("/api/proposals").json()
    assert proposals[0]["summary"].startswith("Move “Backstory”") and api.get("/api/version").json()["pending"] == 1
    r = api.post(f"/api/proposals/{proposals[0]['id']}/approve")
    assert r.status_code == 200
    assert stage(api, backstory) == "review"
    assert stage(api, block_id(api, "2D sprites")) == "ready"         # rules ran as in the app


def test_rejecting_a_proposal(api):
    call("add_todo", parent_id=block_id(api, "Cloud"), title="Voice lines")
    pid = api.get("/api/proposals").json()[0]["id"]
    api.post(f"/api/proposals/{pid}/reject")
    assert block_id(api, "Voice lines") is None
    assert api.get("/api/proposals").json() == []


def test_refused_changes_are_not_proposed(api):
    reply = call("move", block_id=block_id(api, "3D model"), phase="In progress")
    assert reply.startswith("Not done, Nestwork refused it") and "2D sprites" in reply
    assert api.get("/api/proposals").json() == []


def test_read_only_and_full_access(api):
    api.put("/api/settings", json={"agent_access": "read_only"})
    assert "read only" in call("add_todo", parent_id=api.pid, title="X")
    api.put("/api/settings", json={"agent_access": "full"})
    reply = call("add_todo", parent_id=block_id(api, "Cloud"), title="Voice lines",
                 description="Record 20 lines", due_date="2030-01-01")
    assert reply.startswith("Done")
    vid = block_id(api, "Voice lines")
    card = api.get(f"/api/blocks/{vid}").json()["block"]
    assert card["notes"] == "Record 20 lines" and card["due_date"] == "2030-01-01"


def test_delete_needs_its_own_switch(api):
    api.put("/api/settings", json={"agent_access": "full"})
    audio = block_id(api, "Audio")
    assert "deleting is turned off" in call("delete", block_id=audio)
    api.put("/api/settings", json={"allow_delete": True})
    assert call("delete", block_id=audio).startswith("Done")
    assert block_id(api, "Audio") is None


def test_version_changes_on_every_change(api):
    v = api.get("/api/version").json()["version"]
    api.patch(f"/api/blocks/{api.pid}", json={"notes": "x"})
    assert api.get("/api/version").json()["version"] == v + 1


def test_agent_sets_daily_pomodoros(api):
    bid = block_id(api, "Backstory")
    assert "Daily pomodoros: no daily plan" in call("get_block", block_id=bid)   # new to-dos start without one
    reply = call("update", block_id=bid, daily_pomodoros=6)              # ask first (the default)
    assert "daily pomodoros to 6" in reply
    pid = api.get("/api/proposals").json()[0]["id"]
    api.post(f"/api/proposals/{pid}/approve")
    assert api.get(f"/api/blocks/{bid}").json()["block"]["pomodoros"] == 6
    api.put("/api/settings", json={"agent_access": "full"})
    assert call("update", block_id=bid, daily_pomodoros=0).startswith("Done")
    assert "no daily plan" in call("get_block", block_id=bid)
    assert "Not done" in call("update", block_id=bid, daily_pomodoros=99)


def test_memory_tools_edit_the_shared_memory(api):
    from nestwork import persona
    assert call("remember", text="Loves board games: Catan, Azul", heading="preferences").startswith("Saved to memory under “Preferences”")
    assert call("remember", text="Getting a bike") == "Saved to memory under “Notes”."
    assert "already in memory" in call("remember", text="- Loves board games: Catan, Azul")
    assert call("remember", text="Plays guitar", heading="Hobbies") == "Saved to memory under “Hobbies”."
    mem = persona.read_file("memory")["content"]
    assert "## Preferences\n\n- Loves board games: Catan, Azul\n" in mem and mem.rstrip().endswith("## Hobbies\n\n- Plays guitar")
    assert call("revise_memory", old="bike", new="Owns a bike") == "Rewrote “Getting a bike”."
    assert "Several lines match" in call("revise_memory", old="s")
    assert call("revise_memory", old="guitar") == "Removed “Plays guitar”."
    mem = persona.read_file("memory")["content"]
    assert "- Owns a bike" in mem and "guitar" not in mem


def test_set_about_me(api):
    from nestwork import persona
    assert call("set_about_me", name="Alex", timezone="Europe/Berlin").startswith("About me updated")
    assert "isn't a timezone" in call("set_about_me", timezone="Mars/Olympus")
    about = persona.read_fields("about")
    assert about["name"] == "Alex" and about["timezone"] == "Europe/Berlin"


def test_old_agent_instructions_switch_to_the_memory_tools(api):
    from nestwork import persona
    persona.ensure_files()
    path = persona.paths()["agent_claude"]
    old = path.read_text().split("## Memory")[0] + "## Memory\n\n- Edit memory.md yourself.\n\n## Extra\n\nkept\n"
    path.write_text(old)
    persona.ensure_files()
    text = path.read_text()
    assert "`remember`" in text and "Edit memory.md yourself" not in text and text.endswith("## Extra\n\nkept\n")


def test_agent_keeps_a_counter(api):
    bid = block_id(api, "Backstory")
    api.put("/api/settings", json={"agent_access": "full"})
    assert call("update", block_id=bid, counter_total=200, counter_unit="videos").startswith("Done")
    assert call("update", block_id=bid, counter_add=3).startswith("Done")
    assert "Counter: 3 of 200 videos (2%)" in call("get_block", block_id=bid)
    assert "3/200 videos" in call("find", query="Backstory")


def test_edit_description(api):
    from nestwork import agent, db
    conn = db.connect()
    with conn:
        agent.set_setting(conn, "agent_access", "full")
    conn.close()
    bid = block_id(api, "Backstory")
    api.patch(f"/api/blocks/{bid}", json={"notes": "Cloud grew up in Nibelheim.\n\nHe joined SOLDIER."})
    assert "Done" in call("edit_description", block_id=bid, old="Nibelheim", new="Nibelheim, a mountain village")
    assert "isn't in the description" in call("edit_description", block_id=bid, old="Midgar", new="x")
    call("edit_description", block_id=bid, new="Then he met Tifa.")
    notes = api.get(f"/api/blocks/{bid}").json()["block"]["notes"]
    assert notes == "Cloud grew up in Nibelheim, a mountain village.\n\nHe joined SOLDIER.\n\nThen he met Tifa."
    api.patch(f"/api/blocks/{bid}", json={"notes": "a a"})
    assert "2 times" in call("edit_description", block_id=bid, old="a", new="b")
    call("edit_description", block_id=bid, old="a", new="b", replace_all=True)
    assert api.get(f"/api/blocks/{bid}").json()["block"]["notes"] == "b b"


def test_export_block(api, tmp_path):
    bid = block_id(api, "Backstory")
    api.patch(f"/api/blocks/{bid}", json={"notes": "Cloud grew up in Nibelheim."})
    out = call("export_block", block_id=bid)
    saved = tmp_path / "exports" / "Backstory.md"
    assert str(saved) in out and saved.read_text() == "# Backstory\n\nCloud grew up in Nibelheim.\n"
    note = api.post(f"/api/blocks/{bid}/notes", json={"text": "DRAFT v1"}).json()
    call("export_block", block_id=bid, path=str(tmp_path / "all.txt"), include_notes=True)
    assert "## Note" in (tmp_path / "all.txt").read_text() and "DRAFT v1" in (tmp_path / "all.txt").read_text()
    call("export_block", block_id=bid, path="draft", note_id=note["id"])
    assert (tmp_path / "exports" / "draft.md").read_text() == "DRAFT v1\n"
    assert "no note #999" in call("export_block", block_id=bid, note_id=999)


def _id(reply: str) -> int:
    import re
    return int(re.search(r"Its id is (\d+)", reply).group(1))


def test_agent_builds_a_project_from_scratch(api):
    api.put("/api/settings", json={"agent_access": "full"})
    reply = call("create_project", title="Thesis", description="MSc thesis",
                 phases=["To do", "Writing", "With supervisor", "Done"],
                 outline="# Research\n- Read papers\n- [x] Pick topic\n# Writing\n- Draft\n  - Intro\n")
    assert reply.startswith("Done: Start a new project “Thesis” with phases"), reply
    pid = _id(reply)
    got = api.get(f"/api/blocks/{pid}").json()
    assert [s["label"] for s in got["stages"]] == ["To do", "Writing", "With supervisor", "Done"]
    assert [s["role"] for s in got["stages"]] == ["start", "active", "review", "done"]
    assert got["block"]["notes"] == "MSc thesis"
    titles = {c["title"]: c for c in got["cards"].values()}
    assert {"Research", "Read papers", "Pick topic", "Writing", "Draft", "Intro"} <= set(titles)
    assert titles["Pick topic"]["status"] == got["stages"][-1]["key"]

    # More to-dos from an outline, then a reorder within a phase.
    research = titles["Research"]["id"]
    assert call("add_outline", parent_id=research, outline="- Interview experts\n- Survey").startswith("Done: Add 2 to-do(s)")
    kids = api.get(f"/api/blocks/{research}").json()["children"]
    first_phase = [c for c in kids if c["status"] == got["stages"][0]["key"]]
    survey = next(c["id"] for c in kids if c["title"] == "Survey")
    assert call("move", block_id=survey, phase="To do", before_id=first_phase[0]["id"]).startswith("Done")
    kids = api.get(f"/api/blocks/{research}").json()["children"]
    assert [c["title"] for c in kids if c["status"] == got["stages"][0]["key"]][0] == "Survey"

    # Phases: rename one (keeping its to-dos), add one, drop one.
    draft = titles["Draft"]["id"]
    call("move", block_id=titles["Intro"]["id"], phase="With supervisor")
    reply = call("set_phases", project_id=pid, phases=[
        "To do", {"name": "Drafting", "was": "Writing"}, {"name": "Proofreading", "color": "butter"}, "Done"])
    assert reply.startswith("Done: Change the phases of “Thesis”"), reply
    stages = api.get(f"/api/blocks/{pid}").json()["stages"]
    assert [s["label"] for s in stages] == ["To do", "Drafting", "Proofreading", "Done"]
    assert stages[1]["key"] == got["stages"][1]["key"] and stages[2]["color"] == "butter"
    assert stage(api, titles["Intro"]["id"]) == stages[1]["key"]       # its phase was dropped → nearest earlier
    assert stage(api, draft) == stages[1]["key"]                       # follows its only to-do


def test_create_project_from_template_and_bad_input(api):
    api.put("/api/settings", json={"agent_access": "full"})
    pid = _id(call("create_project", title="Apply", template="university"))
    assert "Shortlist universities" in call("whats_left", block_id=pid)
    assert "isn't a phase" not in call("create_project", title="X", template="nope")
    assert "no template" in call("create_project", title="X", template="nope")
    assert "either phases or a template" in call("create_project", title="X", template="game", phases=["A", "B"])
    assert "at least two phases" in call("create_project", title="X", phases=["Only"])
    assert "isn't a phase" in call("set_phases", project_id=pid, phases=["A", {"name": "B", "was": "Nope"}])
    assert "role" in call("set_phases", project_id=pid, phases=["A", {"name": "B", "role": "boss"}, "C"])
    assert [p["title"] for p in api.get("/api/projects").json()].count("X") == 0


def test_create_project_asks_first(api):
    reply = call("create_project", title="Garden", phases=["Plan", "Plant", "Grow"])
    assert reply.startswith("Proposed") and "Garden" in reply
    assert "Garden" not in [p["title"] for p in api.get("/api/projects").json()]
    pid = api.get("/api/proposals").json()[0]["id"]
    assert api.post(f"/api/proposals/{pid}/approve").status_code == 200
    assert "Garden" in [p["title"] for p in api.get("/api/projects").json()]


def test_trash_and_notes(api):
    api.put("/api/settings", json={"agent_access": "full"})
    audio = block_id(api, "Audio")
    note = api.post(f"/api/blocks/{audio}/notes", json={"text": "Use chiptune"}).json()
    assert call("edit_note", note_id=note["id"], text="Use orchestral").startswith("Done")
    assert "Use orchestral" in call("get_block", block_id=audio)
    assert "deleting is turned off" in call("delete_note", note_id=note["id"])
    api.put("/api/settings", json={"allow_delete": True})
    assert call("delete_note", note_id=note["id"]).startswith("Done")
    assert "Use orchestral" not in call("get_block", block_id=audio)

    call("delete", block_id=audio)
    assert block_id(api, "Audio") is None
    listing = call("list_trash")
    tid = int(listing.split("#", 1)[1].split()[0])
    assert "Audio" in listing and "2 item(s) inside" in listing
    reply = call("restore_from_trash", trash_id=tid)
    assert reply.startswith("Done: Restore “Audio”") and f"Its id is {audio}" in reply
    assert block_id(api, "Main theme") is not None
    assert call("list_trash") == "The trash is empty."


def test_agent_archives_a_project(api):
    assert call("archive_project", project_id=api.pid).startswith("Proposed")     # ask first: the user approves
    api.put("/api/settings", json={"agent_access": "full"})
    assert call("archive_project", project_id=api.pid).startswith("Done: Change “Game”: archive it")
    assert api.get("/api/projects").json() == []
    assert "Archived" in call("list_projects")
    assert "Only a whole project" in call("archive_project", project_id=block_id_any(api))
    assert call("archive_project", project_id=api.pid, archived=False).startswith("Done")
    assert [p["id"] for p in api.get("/api/projects").json()] == [api.pid]


def block_id_any(api):
    return api.get(f"/api/blocks/{api.pid}").json()["children"][0]["id"]
