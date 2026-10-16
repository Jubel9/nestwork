import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "test.db"))
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(client, parent, title, status=None):
    r = client.post("/api/blocks", json={"title": title, "parent_id": parent, "status": status})
    assert r.status_code == 201, r.text
    return r.json()["block"]["id"]


def move(client, bid, status):
    return client.post(f"/api/blocks/{bid}/move", json={"status": status})


def stage(client, bid):
    return client.get(f"/api/blocks/{bid}").json()["block"]["status"]


def set_rules(client, bid, *rules):
    """rules: (depends_on_id, required_stage, target_stage)"""
    return client.put(f"/api/blocks/{bid}/rules", json={"rules": [
        {"depends_on_id": d, "required_stage": req, "target_stage": tgt} for d, req, tgt in rules]})


def classic_project(client):
    """An empty project with the five default phases (Backlog … Complete & accepted)."""
    pid = client.post("/api/blocks", json={"title": "Game", "template": "work"}).json()["block"]["id"]
    for child in client.get(f"/api/blocks/{pid}").json()["children"]:
        client.delete(f"/api/blocks/{child['id']}")
    return pid


@pytest.fixture()
def cloud(client):
    game = classic_project(client)
    chars = add(client, game, "Characters")
    cloud = add(client, chars, "Cloud")
    ids = {t: add(client, cloud, t) for t in ("Backstory", "2D sprites", "3D model", "Relation", "Easter egg")}
    assert set_rules(client, ids["2D sprites"], (ids["Backstory"], "review", "ready")).status_code == 200
    assert set_rules(client, ids["3D model"], (ids["2D sprites"], "done", "ready")).status_code == 200
    assert set_rules(client, ids["Backstory"], (ids["2D sprites"], "done", "done"),
                     (ids["3D model"], "done", "done")).status_code == 200
    return dict(game=game, chars=chars, cloud=cloud, **ids)


def test_rule_gates_stage(client, cloud):
    r = move(client, cloud["2D sprites"], "ready")
    assert r.status_code == 409 and "Backstory" in r.json()["detail"]
    assert move(client, cloud["3D model"], "in_progress").status_code == 409


def test_rule_moves_block_automatically(client, cloud):
    r = move(client, cloud["Backstory"], "review")
    assert r.status_code == 200
    assert stage(client, cloud["2D sprites"]) == "ready"
    assert any("2D sprites" in n and "⚡" in n for n in r.json()["notices"])


def test_backstory_completes_when_art_completes(client, cloud):
    assert move(client, cloud["Backstory"], "done").status_code == 409  # art not done yet
    move(client, cloud["Backstory"], "review")
    move(client, cloud["2D sprites"], "done")
    assert stage(client, cloud["3D model"]) == "ready"
    move(client, cloud["3D model"], "done")
    assert stage(client, cloud["Backstory"]) == "done"


def test_parent_cannot_be_reviewed_early(client, cloud):
    r = move(client, cloud["cloud"], "review")
    assert r.status_code == 409 and "everything inside" in r.json()["detail"]
    assert move(client, cloud["cloud"], "done").status_code == 409


def test_parent_moves_to_review_when_everything_inside_is_done(client, cloud):
    for k in ("Backstory",):
        move(client, cloud[k], "review")
    for k in ("2D sprites", "3D model", "Relation", "Easter egg"):
        assert move(client, cloud[k], "done").status_code == 200, k
    assert stage(client, cloud["Backstory"]) == "done"
    assert stage(client, cloud["cloud"]) == "review"
    # Characters follows its slowest to-do: Cloud, which is in review.
    assert stage(client, cloud["chars"]) == "review"
    assert move(client, cloud["cloud"], "done").status_code == 200
    assert stage(client, cloud["chars"]) == "review"            # all done: waits for your OK


def test_auto_placed_block_cannot_be_dragged_back(client, cloud):
    move(client, cloud["Backstory"], "review")
    r = move(client, cloud["2D sprites"], "backlog")
    assert r.status_code == 409 and "automatically" in r.json()["detail"]


def test_parent_is_at_its_slowest_todo(client, cloud):
    move(client, cloud["Relation"], "in_progress")
    assert stage(client, cloud["cloud"]) == "backlog"            # the others haven't started
    for k in ("Backstory", "Easter egg"):
        move(client, cloud[k], "in_progress")
    move(client, cloud["Backstory"], "review")                    # 2D sprites becomes ready by its rule
    assert stage(client, cloud["2D sprites"]) == "ready"
    assert stage(client, cloud["cloud"]) == "backlog"            # 3D model is still waiting
    assert move(client, cloud["cloud"], "ready").status_code == 409


def test_reopening_child_reopens_parent(client, cloud):
    move(client, cloud["Backstory"], "review")
    for k in ("2D sprites", "3D model", "Relation", "Easter egg"):
        move(client, cloud[k], "done")
    move(client, cloud["cloud"], "done")
    r = move(client, cloud["Relation"], "in_progress")
    assert r.status_code == 200
    assert stage(client, cloud["cloud"]) == "in_progress"         # follows Relation back
    assert any("↩️" in n for n in r.json()["notices"])


def test_new_item_reopens_finished_parent(client, cloud):
    move(client, cloud["Backstory"], "review")
    for k in ("2D sprites", "3D model", "Relation", "Easter egg"):
        move(client, cloud[k], "done")
    assert stage(client, cloud["cloud"]) == "review"
    add(client, cloud["cloud"], "Voice lines")
    assert stage(client, cloud["cloud"]) == "backlog"            # its slowest to-do just started over


def test_lock_inherited_by_nested_items(client, cloud):
    sub = add(client, cloud["3D model"], "Rigging")
    data = client.get(f"/api/blocks/{sub}").json()["block"]
    assert data["locked"] and data["blocked_by"][0]["via_title"] == "3D model"
    assert move(client, sub, "in_progress").status_code == 409


def test_loops_refused(client, cloud):
    # 2D sprites can't start before Backstory is in review, so Backstory can't wait for 2D to start.
    assert set_rules(client, cloud["Backstory"], (cloud["2D sprites"], "ready", "review")).status_code == 409
    assert set_rules(client, cloud["Backstory"], (cloud["cloud"], "done", "ready")).status_code == 409
    assert set_rules(client, cloud["chars"], (cloud["Backstory"], "done", "ready")).status_code == 409


def test_progress_rolls_up(client, cloud):
    move(client, cloud["Relation"], "done")
    data = client.get(f"/api/blocks/{cloud['game']}").json()
    assert abs(data["block"]["progress"] - 1 / 5) < 0.01
    assert data["block"]["leaves_done"] == 1 and data["block"]["leaves_total"] == 5


def test_progress_follows_phases_and_counters(client):
    """A to-do counts by how far right it is (5 phases: 0, 25, 50, 75, 100%), or by its counter when it has one."""
    game = classic_project(client)
    study = add(client, game, "Study")
    videos, quiz = add(client, study, "Course videos"), add(client, study, "Quiz")
    prog = lambda b: client.get(f"/api/blocks/{b}").json()["block"]["progress"]
    move(client, quiz, "in_progress")
    assert prog(quiz) == 0.5 and prog(study) == 0.25
    # A counter: 50 of 200 videos = 25%, whatever its phase.
    r = client.patch(f"/api/blocks/{videos}", json={"track_total": 200, "track_done": 49, "track_unit": " course  videos "})
    assert r.json()["block"]["track"] == {"done": 49, "total": 200, "unit": "course videos"}
    client.patch(f"/api/blocks/{videos}", json={"track_add": 1})
    assert prog(videos) == 0.25 and prog(study) == 0.375 and prog(game) == 0.375
    assert client.patch(f"/api/blocks/{videos}", json={"track_add": 500}).json()["block"]["track"]["done"] == 200
    assert client.patch(f"/api/blocks/{quiz}", json={"track_add": 1}).status_code == 422       # no counter yet
    # It survives export/import and duplicating (a copy starts from 0), and 0 removes it.
    data = client.get(f"/api/blocks/{game}/export.json").json()
    new = client.post("/api/import", json={"data": data}).json()["projects"][0]
    copy = next(c for c in client.get(f"/api/blocks/{new}").json()["cards"].values() if c["title"] == "Course videos")
    assert copy["track"]["total"] == 200 and copy["track"]["done"] == 200
    dup = client.post(f"/api/blocks/{videos}/duplicate").json()["block"]
    assert dup["track"] == {"done": 0, "total": 200, "unit": "course videos"}
    assert client.patch(f"/api/blocks/{videos}", json={"track_total": 0}).json()["block"]["track"] is None
    assert prog(videos) == 0


def test_duplicate_remaps_internal_rules(client, cloud):
    r = client.post(f"/api/blocks/{cloud['cloud']}/duplicate")
    assert r.status_code == 201
    copy = r.json()["block"]["id"]
    kids = {c["title"]: c for c in client.get(f"/api/blocks/{copy}").json()["children"]}
    rule = kids["2D sprites"]["rules"][0]
    assert rule["depends_on_id"] == kids["Backstory"]["id"] and rule["required_stage"] == "review"


def test_templates_create(client):
    for t in client.get("/api/templates").json():
        r = client.post("/api/blocks", json={"title": t["name"], "template": t["key"]})
        assert r.status_code == 201


def stages_of(client, bid):
    return client.get(f"/api/blocks/{bid}").json()["stages"]


def test_blank_project_uses_own_phases(client):
    r = client.post("/api/blocks", json={"title": "Thesis", "template": "blank",
                                         "stages": ["Idea", "Writing", "Feedback", "Final"]})
    pid = r.json()["block"]["id"]
    stages = stages_of(client, pid)
    assert [s["label"] for s in stages] == ["Idea", "Writing", "Feedback", "Final"]
    assert [s["role"] for s in stages] == ["start", "active", "review", "done"]
    a = add(client, pid, "Chapter 1")
    assert stage(client, a) == stages[0]["key"]
    assert client.post("/api/blocks", json={"title": "x", "template": "blank", "stages": ["Only"]}).status_code == 422


def test_edit_phases_rename_add_remove(client, cloud):
    stages = stages_of(client, cloud["game"])
    keys = [s["key"] for s in stages]
    move(client, cloud["Relation"], "in_progress")
    # Rename "Backlog", remove "In progress", add "Polish" before "Complete".
    body = [{"key": keys[0], "label": "Ideas"}, {"key": keys[1], "label": "Ready"},
            {"key": keys[3], "label": "In review", "role": "review"},
            {"label": "Polish"}, {"key": keys[4], "label": "Shipped"}]
    r = client.put(f"/api/blocks/{cloud['game']}/stages", json={"stages": body})
    assert r.status_code == 200, r.text
    new = r.json()["stages"]
    assert [s["label"] for s in new] == ["Ideas", "Ready", "In review", "Polish", "Shipped"]
    assert new[3]["key"].startswith("s_")
    # The block that was "In progress" moved back to the nearest kept phase before it.
    assert stage(client, cloud["Relation"]) == keys[1]
    # A container can only reach the review phase once everything inside is complete.
    assert move(client, cloud["cloud"], keys[3]).status_code == 409


def test_phase_without_review_role_completes_containers(client):
    pid = client.post("/api/blocks", json={"title": "P", "template": "blank",
                                           "stages": ["To do", "Done"]}).json()["block"]["id"]
    done_key = stages_of(client, pid)[-1]["key"]
    group = add(client, pid, "Group")
    a, b = add(client, group, "A"), add(client, group, "B")
    move(client, a, done_key)
    assert stage(client, group) != done_key
    move(client, b, done_key)
    assert stage(client, group) == done_key


def test_phases_need_two(client, cloud):
    stages = stages_of(client, cloud["game"])
    r = client.put(f"/api/blocks/{cloud['game']}/stages", json={"stages": [{"key": stages[0]["key"], "label": "x"}]})
    assert r.status_code == 422


def test_delete_unlocks_dependents(client, cloud):
    client.delete(f"/api/blocks/{cloud['Backstory']}")
    assert stage(client, cloud["2D sprites"]) == "backlog"  # no rules left, so nothing pushes it
    assert move(client, cloud["2D sprites"], "ready").status_code == 200


def test_old_dependencies_table_is_migrated(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE blocks (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'backlog', position INTEGER NOT NULL DEFAULT 0,
            due_date TEXT, stage_labels TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE dependencies (block_id INTEGER, depends_on_id INTEGER);
        INSERT INTO blocks (id, parent_id, title) VALUES (1, NULL, 'P'), (2, 1, 'A'), (3, 1, 'B');
        INSERT INTO dependencies VALUES (3, 2);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setenv("NESTWORK_DB", str(path))
    from nestwork.main import app
    with TestClient(app) as c:
        rules = c.get("/api/blocks/3").json()["block"]["rules"]
        assert rules == [{"depends_on_id": 2, "title": "A", "required_stage": "done",
                          "target_stage": "ready", "met": False}]


def test_today_groups_and_snapshot(client, cloud, tmp_path):
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client.patch(f"/api/blocks/{cloud['Relation']}", json={"due_date": yesterday, "notes": "Who Cloud trusts."})
    move(client, cloud["Easter egg"], "in_progress")
    today = client.get("/api/today").json()
    groups = {g["key"]: [i["title"] for i in g["items"]] for g in today["groups"]}
    assert groups["overdue"] == ["Relation"]
    assert "Easter egg" in groups["in_progress"]
    assert "Backstory" in groups["ready"]
    assert {"2D sprites", "3D model"} <= set(groups["blocked"])
    assert today["focus"][0]["title"] == "Relation"
    # The snapshot file next to the database is rewritten after each change.
    md = (tmp_path / "test.md").read_text()
    assert "## Today" in md and "**Relation**" in md and "Who Cloud trusts." in md
    assert "🔒 waits until “Backstory” reaches “In review”" in md
    assert client.get("/api/snapshot.md").text.startswith("# Nestwork snapshot")


def test_blocks_follow_their_slowest_todo(client, cloud):
    """A bubble is in the phase of its slowest to-do, moves on its own, and can't run ahead."""
    move(client, cloud["Backstory"], "review")
    for k in ("Relation", "Easter egg", "2D sprites"):
        move(client, cloud[k], "in_progress")
    assert stage(client, cloud["cloud"]) == "backlog"                           # 3D model hasn't started
    assert move(client, cloud["cloud"], "in_progress").status_code == 409
    move(client, cloud["2D sprites"], "done")
    for k in ("Relation", "Easter egg", "3D model"):
        assert move(client, cloud[k], "review").status_code == 200, k
    assert stage(client, cloud["cloud"]) == "review"                           # all reached review
    assert stage(client, cloud["chars"]) == "review"
    move(client, cloud["Relation"], "in_progress")                             # one moves back
    assert stage(client, cloud["cloud"]) == "in_progress"
    assert stage(client, cloud["chars"]) == "in_progress"


def test_move_into_another_block(client, cloud):
    tifa = add(client, cloud["chars"], "Tifa")
    targets = {t["title"]: t for t in client.get(f"/api/blocks/{cloud['Relation']}/move-targets").json()}
    assert targets["Cloud"]["current"] and "Tifa" in targets and targets["Game"]["is_root"]
    assert "Relation" not in targets
    r = client.post(f"/api/blocks/{cloud['Relation']}/move-into", json={"parent_id": tifa})
    assert r.status_code == 200 and r.json()["block"]["parent_id"] == tifa
    # can't move a block inside itself or what it contains
    assert client.post(f"/api/blocks/{cloud['cloud']}/move-into", json={"parent_id": cloud["Backstory"]}).status_code == 409
    # 2D sprites waits for Backstory to start; putting Backstory inside 2D sprites would be a loop
    r = client.post(f"/api/blocks/{cloud['Backstory']}/move-into", json={"parent_id": cloud["2D sprites"]})
    assert r.status_code == 409 and "loop" in r.json()["detail"]
    assert client.get(f"/api/blocks/{cloud['Backstory']}").json()["block"]["parent_id"] == cloud["cloud"]


def test_move_into_and_phase_in_one_step(client, cloud):
    """Dropping a card on another block's row on the board: new parent and phase together, one undo step."""
    tifa = add(client, cloud["chars"], "Tifa")
    first = add(client, tifa, "Limit break", "in_progress")
    r = client.post(f"/api/blocks/{cloud['Relation']}/move-into",
                    json={"parent_id": tifa, "status": "in_progress", "before_id": first})
    assert r.status_code == 200
    kids = client.get(f"/api/blocks/{tifa}").json()["children"]
    assert [(k["title"], k["status"]) for k in kids] == [("Relation", "in_progress"), ("Limit break", "in_progress")]
    client.post("/api/undo")
    assert client.get(f"/api/blocks/{cloud['Relation']}").json()["block"]["parent_id"] == cloud["cloud"]
    assert stage(client, cloud["Relation"]) == "backlog"
    # Inside 2D sprites it can't start (2D sprites waits for Backstory): refused, and nothing moves.
    r = client.post(f"/api/blocks/{cloud['Easter egg']}/move-into",
                    json={"parent_id": cloud["2D sprites"], "status": "in_progress"})
    assert r.status_code == 409
    assert client.get(f"/api/blocks/{cloud['Easter egg']}").json()["block"]["parent_id"] == cloud["cloud"]


def test_old_databases_are_migrated_once(tmp_path, monkeypatch):
    """A first-release database (no phases column, plain dependencies) is brought up to date and versioned."""
    import sqlite3
    from nestwork import db
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE blocks (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'backlog', position INTEGER NOT NULL DEFAULT 0,
            due_date TEXT, stage_labels TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE dependencies (block_id INTEGER, depends_on_id INTEGER);
        INSERT INTO blocks (id, parent_id, title) VALUES (1, NULL, 'P'), (2, 1, 'A'), (3, 1, 'B');
        INSERT INTO dependencies VALUES (3, 2);""")
    old.commit()
    old.close()
    monkeypatch.setenv("NESTWORK_DB", str(path))
    db.init_db()
    db.init_db()                                     # a second start changes nothing
    conn = db.connect()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(blocks)")}
    assert {"stage_config", "pomodoros", "repeat", "tags", "archived_at"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT block_id, depends_on_id FROM rules").fetchall()[0][:] == (3, 2)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer Nestwork"):
        db.init_db()


def test_new_todo_inside_starts_in_its_blocks_phase(client):
    """Breaking a to-do down doesn't push it back to the start: its first to-dos start in its phase."""
    game = classic_project(client)
    docs = add(client, game, "Documents")
    diploma = add(client, docs, "Diploma", "ready")
    assert stage(client, add(client, game, "Top level")) == "backlog"                 # the project: from the start
    translate = add(client, diploma, "Translate")
    assert stage(client, translate) == "ready" and stage(client, diploma) == "ready"
    assert stage(client, add(client, diploma, "Prep", "backlog")) == "backlog"         # an explicit phase wins
    assert stage(client, diploma) == "backlog"
    sent = add(client, docs, "Sent", "review")
    assert stage(client, add(client, sent, "Follow up")) == "backlog"                 # waiting for review: from the start
