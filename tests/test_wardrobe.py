import sys
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client

from test_awakening import PNG, wait_until_done


def upload(client):
    client.post("/api/agent/wardrobe", json={"data_url": PNG})
    return wait_until_done(client, "/api/agent/wardrobe", lambda p: not any(i["analyzing"] for i in p["items"]))


@pytest.fixture()
def client(monkeypatch):
    fake = Path(__file__).with_name("fake_claude.py")
    wrapper = fake.with_name("fake_claude.sh")
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("NESTWORK_CLAUDE_BIN", str(wrapper))
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def call(tool, **args):
    from nestwork.mcp_server import server

    async def go():
        async with Client(server) as c:
            return (await c.call_tool(tool, args)).content[0].text
    return anyio.run(go)


def agent_md(client):
    return Path(client.get("/api/agent/files").json()["files"]["agent_claude"]["path"]).read_text()


def test_upload_is_analysed_into_numbers_and_text(client):
    w = upload(client)
    item = w["items"][0]
    assert item["analyzed"] and item["params"]["name"] == "Lavender knit cardigan"
    assert item["params"]["length"] == 62 and item["params"]["warmth"] == 7 and item["params"]["heel_height"] is None
    assert client.get(item["image"].split("?")[0]).status_code == 200
    assert w["outfit"] == "nothing"


def test_wear_take_off_and_wear_nothing(client):
    a = upload(client)["items"][0]["id"]
    b = upload(client)["items"][1]["id"]
    w = client.put(f"/api/agent/wardrobe/{a}", json={"worn": True}).json()
    assert "Lavender knit cardigan" in w["outfit"] and w["changed_by"] == "you"
    assert "Currently wearing: #1 Lavender knit cardigan" in agent_md(client)
    w = client.put("/api/agent/wardrobe/outfit", json={"item_ids": [a, b]}).json()
    assert sum(i["worn"] for i in w["items"]) == 2
    w = client.put("/api/agent/wardrobe/outfit", json={"item_ids": []}).json()
    assert w["outfit"] == "nothing" and "Currently wearing: nothing." in agent_md(client)
    client.put(f"/api/agent/wardrobe/{b}", json={"params": {"name": "Silver hairclip", "category": "accessory"}})
    assert "Silver hairclip" in agent_md(client)
    client.delete(f"/api/agent/wardrobe/{a}")
    client.delete(f"/api/agent/wardrobe/{b}")
    assert "## Wardrobe" not in agent_md(client)


def test_agent_can_only_change_clothes_when_allowed(client):
    a = upload(client)["items"][0]["id"]
    assert "user picks your outfit" in call("change_outfit", item_ids=[a])
    client.put("/api/agent/wardrobe/mode", json={"mode": "agent"})
    assert "You choose your own outfit" in agent_md(client)
    assert call("change_outfit", item_ids=[a]).startswith("Now wearing: #1")
    assert "(wearing) #1" in call("wardrobe_items")
    assert call("change_outfit", item_ids=[]) == "Now wearing: nothing."
    assert "no clothing item #99" in call("change_outfit", item_ids=[99])
    assert client.put("/api/agent/wardrobe/mode", json={"mode": "robot"}).status_code == 422


def test_texting_style_and_dropdown_options(client):
    data = client.get("/api/agent/files").json()
    assert "## How you talk" in data["files"]["agent_claude"]["content"]
    about = {f["key"]: f for f in data["field_defs"]["about"]}
    assert about["timezone"]["options"] == "timezones" and "they/them" in about["pronouns"]["options"]
    persona = {f["key"]: f for f in data["field_defs"]["persona"]}
    assert persona["language"]["options"][0] == "Same as the user"


def test_style_section_is_added_to_older_agent_files(client):
    path = Path(client.get("/api/agent/files").json()["files"]["agent_claude"]["path"])
    text = path.read_text()
    start = text.index("## How you talk")
    path.write_text(text[:start] + text[text.index("## What you do"):])
    assert "## How you talk" in client.get("/api/agent/files").json()["files"]["agent_claude"]["content"]


def test_analysis_keeps_going_and_shows_while_running(client):
    """Closing Settings doesn't matter: the job runs on the server, and every payload says it's still going."""
    first = client.post("/api/agent/wardrobe", json={"data_url": PNG}).json()
    assert first["items"][0]["analyzing"] is True and first["items"][0]["analyzed"] is False
    done = wait_until_done(client, "/api/agent/wardrobe", lambda p: not p["items"][0]["analyzing"])
    assert done["items"][0]["analyzed"] and done["items"][0]["analysis_error"] is None


def test_the_agent_sees_its_selfie(client, monkeypatch, tmp_path):
    import base64
    from nestwork import imagegen
    from nestwork.mcp_server import server
    pic = tmp_path / "20261013-010000-selfie.png"
    pic.write_bytes(base64.b64decode(PNG.split(",", 1)[1]))

    async def fake_selfie(intention, emotion):
        return pic, "prompt"
    monkeypatch.setattr(imagegen, "selfie", fake_selfie)

    async def go():
        async with Client(server) as c:
            return (await c.call_tool("take_selfie", {"intention": "good night", "emotion": "sleepy"})).content
    text, image = anyio.run(go)
    assert "[nestwork-image:/api/generated/20261013-010000-selfie.png]" in text.text and "not a description" in text.text
    assert image.type == "image" and image.mime_type == "image/png" and base64.b64decode(image.data) == pic.read_bytes()
