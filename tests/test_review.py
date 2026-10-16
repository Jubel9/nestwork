"""Completions are logged as to-dos finish; the weekly review sums up the week and compares with the last one."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def add(c, parent, title):
    return c.post("/api/blocks", json={"title": title, "parent_id": parent}).json()["block"]["id"]


def test_completions_and_review(api):
    pid = api.post("/api/blocks", json={"title": "Thesis", "stages": ["To do", "Done"]}).json()["block"]["id"]
    first, done = [s["key"] for s in api.get(f"/api/blocks/{pid}").json()["stages"]]
    a, b, c = add(api, pid, "Read"), add(api, pid, "Write"), add(api, pid, "Late one")
    assert api.get("/api/review").json()["due"] is False             # nothing happened yet
    api.post(f"/api/blocks/{a}/move", json={"status": done})
    api.post(f"/api/blocks/{b}/move", json={"status": done})
    api.post(f"/api/blocks/{b}/move", json={"status": first})        # reopened the same day: a mis-click
    api.patch(f"/api/blocks/{c}", json={"due_date": (date.today() - timedelta(days=2)).isoformat()})
    r = api.get("/api/review").json()
    assert [d["title"] for d in r["done"]] == ["Read"] and r["due"] is True
    assert [i["title"] for i in r["slipped"]] == ["Late one"]
    proj = r["projects"][str(pid)]
    assert proj["done_this_week"] == 1 and proj["progress_before"] is None

    api.post("/api/review", json={"note": "Too much reading, not enough writing."})
    api.post(f"/api/blocks/{b}/move", json={"status": done})
    r = api.get("/api/review").json()
    assert r["due"] is False and r["history"][0]["note"].startswith("Too much")
    assert r["projects"][str(pid)]["progress_before"] == pytest.approx(1 / 3, abs=0.01)
    api.post("/api/undo")                                              # undoing "mark done" removes the completion
    assert [d["title"] for d in api.get("/api/review").json()["done"]] == ["Read"]

    from nestwork.mcp_server import weekly_review
    text = weekly_review()
    assert "## Done (1)" in text and "Late one" in text and "Too much reading" in text
