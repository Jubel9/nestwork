"""The agent's gallery (every generated picture) and its profile picture."""

import json
import os

from fastapi.testclient import TestClient

from nestwork import db, imagegen

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")


def make(name: str, mtime: int):
    d = imagegen.generated_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(PNG)
    os.utime(d / name, (mtime, mtime))


def test_gallery_lists_pictures_with_their_message_and_sets_the_avatar():
    from nestwork.main import app
    with TestClient(app) as c:
        make("20261010-090000-image.png", 1000)
        make("20261011-100000-selfie.png", 2000)
        conn = db.connect()
        conn.execute("INSERT INTO chat_messages (session_id, role, text, meta) VALUES ('s', 'assistant', 'Here I am!', ?)",
                     (json.dumps({"images": ["/api/generated/20261011-100000-selfie.png"]}),))
        conn.commit(); conn.close()

        g = c.get("/api/agent/gallery").json()
        assert [(i["name"], i["kind"]) for i in g["items"]] == [("20261011-100000-selfie.png", "selfie"),
                                                                 ("20261010-090000-image.png", "image")]
        assert g["items"][0]["message"]["text"] == "Here I am!" and "message" not in g["items"][1]
        assert g["avatar"] == "" and c.get("/api/agent/avatar").status_code == 404      # no face photo yet

        assert c.put("/api/agent/avatar", json={"name": "20261011-100000-selfie.png"}).json()["avatar"] == "20261011-100000-selfie.png"
        assert c.get("/api/agent/avatar").content == PNG
        assert c.get("/api/agent/profile").json()["avatar"] == "20261011-100000-selfie.png"
        assert c.get("/api/agent/gallery").json()["items"][0]["avatar"] is True
        assert c.put("/api/agent/avatar", json={"name": "../nope.png"}).status_code == 422
        assert c.put("/api/agent/avatar", json={"name": None}).json()["avatar"] == ""
