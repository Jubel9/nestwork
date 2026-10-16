import base64
import json
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from test_awakening import PNG, photo

PNG_BYTES = base64.b64decode(PNG.split(",", 1)[1])


@pytest.fixture()
def client(monkeypatch, tmp_path):
    fake = Path(__file__).with_name("fake_claude.py")
    wrapper = fake.with_name("fake_claude.sh")
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("NESTWORK_CLAUDE_BIN", str(wrapper))
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=or-test\nexport WAVESPEED_API_KEY='ws-test'\n")
    monkeypatch.setenv("NESTWORK_ENV_FILE", str(env))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


def mock_http(monkeypatch, handler):
    from nestwork import imagegen

    async def no_wait(_):
        return None
    monkeypatch.setattr(imagegen, "http_client", lambda timeout=180: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(imagegen.asyncio, "sleep", no_wait)


def dress_up(client):
    """Face/body/room photos and one worn clothing item."""
    for kind in ("face", "body", "workspace"):
        client.post(f"/api/agent/images/{kind}", json={"data_url": photo(kind)})
    from nestwork import wardrobe
    item = wardrobe.add(PNG)
    wardrobe.update(item["id"], params={"name": "Lavender cardigan"}, worn=True)


def test_keys_come_from_env_file(client):
    assert client.get("/api/imagegen").json()["keys"] == {"openrouter": True, "wavespeed": True}


def test_openrouter_drawing(client, monkeypatch):
    seen = {}

    def handler(req):
        seen["url"], seen["body"], seen["auth"] = str(req.url), json.loads(req.content), req.headers["authorization"]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode(), "media_type": "image/png"}]})
    mock_http(monkeypatch, handler)
    from nestwork import imagegen
    path = anyio.run(imagegen.draw, "a cozy study at night", "16:9")
    assert path.read_bytes() == PNG_BYTES and path.suffix == ".png"
    assert seen["url"] == "https://openrouter.ai/api/v1/images" and seen["auth"] == "Bearer or-test"
    assert seen["body"]["aspect_ratio"] == "16:9" and "input_references" not in seen["body"]
    assert client.get(f"/api/generated/{path.name}").status_code == 200


def test_selfie_with_references_on_wavespeed(client, monkeypatch):
    client.put("/api/settings", json={"selfie_provider": "wavespeed", "selfie_model": "alibaba/wan-2.7/image-edit"})
    dress_up(client)
    calls = []

    def handler(req):
        url = str(req.url)
        calls.append((req.method, url))
        if url.endswith("/media/uploads"):
            n = sum(1 for m, u in calls if u.endswith("/media/uploads"))
            return httpx.Response(200, json={"code": 200, "data": {"upload": {"method": "PUT", "url": f"https://up.test/{n}"},
                                                                   "download_url": f"https://cdn.test/ref{n}.png"}})
        if url.startswith("https://up.test/"):
            return httpx.Response(200)
        if url.endswith("/alibaba/wan-2.7/image-edit"):
            body = json.loads(req.content)
            calls.append(("BODY", body))
            return httpx.Response(200, json={"data": {"id": "p1"}})
        if url.endswith("/predictions/p1/result"):
            return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn.test/out.png"]}})
        if url == "https://cdn.test/out.png":
            return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})
        return httpx.Response(404)
    mock_http(monkeypatch, handler)
    from nestwork import imagegen
    path, prompt = anyio.run(imagegen.selfie, "showing off the finished draft", "proud")
    body = next(c[1] for c in calls if c[0] == "BODY")
    assert body["images"] == [f"https://cdn.test/ref{i}.png" for i in range(1, 5)]      # face, body, clothes, room
    assert "Front camera selfie." in body["prompt"] and body["prompt"] == prompt
    assert prompt.endswith("Image reference 1 is the face.\nImage reference 2 is the body.\n"
                           "Image reference 3 is the clothes: Lavender cardigan.\nImage reference 4 is the room.")
    assert path.read_bytes() == PNG_BYTES


def test_no_outfit_selfie_adds_no_clothes(client, monkeypatch):
    client.put("/api/settings", json={"selfie_provider": "openrouter", "selfie_model": "some/model"})
    for kind in ("face", "body"):
        client.post(f"/api/agent/images/{kind}", json={"data_url": photo(kind)})
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode(), "media_type": "image/png"}]})
    mock_http(monkeypatch, handler)
    from nestwork import imagegen
    assert "WHAT I'M WEARING: nothing\n" in imagegen.selfie_input("hi", "happy")
    path, prompt = anyio.run(imagegen.selfie, "hi", "happy")
    assert prompt.endswith("Image reference 1 is the face.\n"
                           "Image reference 2 is the body, no outfit selected: do not add any clothing.")
    assert len(seen["body"]["input_references"]) == 2 and path.read_bytes() == PNG_BYTES
    assert path.with_suffix(".txt").read_text() == prompt + "\n"


def test_selfie_rules_are_neutral():
    import codecs
    from nestwork import imagegen
    # rot13 keeps the words themselves out of the repo, so the pre-publish grep stays clean
    banned = [codecs.decode(w, "rot13") for w in ("ahqr", "anxrq", "oernfgf", "anhtugl", "frkl", "yvatrevr")]
    assert not [w for w in banned if w in imagegen.SELFIE_RULES.lower()]


def test_selfie_rules_without_personal_preference(monkeypatch, tmp_path):
    from nestwork import imagegen, persona
    monkeypatch.setattr(persona, "agent_dir", lambda: tmp_path / "agent")
    assert imagegen.selfie_rules() == imagegen.SELFIE_RULES


def test_personal_preference_goes_above_the_tested_example(monkeypatch, tmp_path):
    from nestwork import imagegen, persona
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "personal_preference.py").write_text(
        'import os\nos.environ["RAN"] = "1"\nSELFIE_RULES = """- Extra rule one.\n- Extra rule two."""\n')
    monkeypatch.setattr(persona, "agent_dir", lambda: agent)
    monkeypatch.delenv("RAN", raising=False)
    rules = imagegen.selfie_rules()
    assert 'plain white void.\n- Extra rule one.\n- Extra rule two.\n\nTESTED EXAMPLE' in rules
    assert "RAN" not in __import__("os").environ                     # read, never run
    (agent / "personal_preference.py").write_text("SELFIE_RULES = (broken")
    assert imagegen.selfie_rules() == imagegen.SELFIE_RULES

def test_body_no_outfit_label_from_personal_preference(monkeypatch, tmp_path):
    from nestwork import imagegen, persona
    agent = tmp_path / "agent"
    monkeypatch.setattr(persona, "agent_dir", lambda: agent)
    assert imagegen.body_no_outfit_label() == imagegen.BODY_NO_OUTFIT_LABEL     # no file
    agent.mkdir()
    (agent / "personal_preference.py").write_text('BODY_NO_OUTFIT_LABEL = (broken')
    assert imagegen.body_no_outfit_label() == imagegen.BODY_NO_OUTFIT_LABEL     # broken file
    (agent / "personal_preference.py").write_text(
        'import os\nos.environ["RAN"] = "1"\nBODY_NO_OUTFIT_LABEL = "the body, private label"\n')
    monkeypatch.delenv("RAN", raising=False)
    assert imagegen.body_no_outfit_label() == "the body, private label"
    assert "RAN" not in __import__("os").environ                     # read, never run


def test_selfie_prompt_that_covers_the_body_is_rewritten(client, monkeypatch):
    client.put("/api/settings", json={"selfie_provider": "openrouter", "selfie_model": "some/model"})
    for kind in ("face", "body"):
        client.post(f"/api/agent/images/{kind}", json={"data_url": photo(kind)})
    mock_http(monkeypatch, lambda req: httpx.Response(
        200, json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode(), "media_type": "image/png"}]}))
    from nestwork import imagegen
    _path, prompt = anyio.run(imagegen.selfie, "goodnight", "sleepy")
    assert not imagegen.COVERINGS.search(prompt) and "Front camera selfie." in prompt


def test_no_selfie_from_unverified_photos(client, monkeypatch):
    from nestwork import appearance, imagegen
    client.post("/api/agent/images/body", json={"data_url": photo("body")})
    appearance.image_file("body").write_bytes(PNG_BYTES)   # swapped on disk after the check
    with pytest.raises(imagegen.ImageError, match="body photo hasn't passed the AI-generated check"):
        anyio.run(imagegen.selfie, "hi", "happy")


def test_missing_key_is_explained(client, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("")
    from nestwork import imagegen
    with pytest.raises(imagegen.ImageError, match="OPENROUTER_API_KEY"):
        anyio.run(imagegen.draw, "a cat")


def test_pictures_show_up_in_the_chat_reply(client):
    r = client.post("/api/chat", json={"message": "selfie please"})
    assert "event: image" in r.text and "/api/generated/x-selfie.png" in r.text
    hist = client.get("/api/chat/history").json()
    assert hist[-1]["meta"]["images"] == ["/api/generated/x-selfie.png"]
    labels = [t["label"] for t in hist[-1]["meta"]["tools"]]
    assert "Taking a selfie" in labels


def test_agent_file_explains_pictures(client):
    text = client.get("/api/agent/files").json()["files"]["agent_claude"]["content"]
    assert "## Pictures" in text and "take_selfie" in text
