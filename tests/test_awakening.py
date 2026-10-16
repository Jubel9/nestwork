import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PNG = "data:image/png;base64," + base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                  "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()
FIXTURES = Path(__file__).with_name("fixtures")


def signed(png_data_url: str, source_type: str, action: str = "c2pa.created") -> str:
    """The PNG with C2PA Content Credentials (signed by a throwaway test certificate) declaring its source."""
    import io
    import c2pa
    signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(c2pa.C2paSigningAlg.ES256,
                                                       (FIXTURES / "c2pa_test_chain.pem").read_bytes(),
                                                       (FIXTURES / "c2pa_test_key.pk8").read_bytes(), None))
    manifest = {"claim_generator_info": [{"name": "TestGen", "version": "1"}],
                "assertions": [{"label": "c2pa.actions", "data": {"actions": [
                    {"action": action, "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/" + source_type}]}}]}
    src, out = io.BytesIO(base64.b64decode(png_data_url.split(",", 1)[1])), io.BytesIO()
    with c2pa.Builder(manifest) as b:
        b.sign(signer, "image/png", src, out)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def real_png(size: int = 16) -> str:
    """A proper (if tiny) PNG: the C2PA signer can't embed into the bare 1-pixel PNG above."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes([200, 180, 255]) * size for _ in range(size))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode()


AI_PNG = signed(real_png(), "trainedAlgorithmicMedia")      # what face and body photos must be


def photo(kind: str) -> str:
    return AI_PNG if kind in ("face", "body") else PNG


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


def wait_until_done(client, url, done=lambda p: not p["analyzing"]):
    """Analyses run in the background: poll like the page does."""
    import time
    for _ in range(100):
        payload = client.get(url).json()
        if done(payload):
            return payload
        time.sleep(0.05)
    raise AssertionError("analysis never finished")


def test_parameter_sets_differ_by_body_type(client):
    defs = client.get("/api/awakening").json()["definitions"]
    female = {p["key"] for p in defs["female"]["body"]}
    male = {p["key"] for p in defs["male"]["body"]}
    assert {"bust", "underbust", "cup_size"} <= female and not ({"bust", "cup_size"} & male)
    assert {"chest", "neck", "bicep"} <= male and "chest" not in female
    assert {"height", "weight", "waist"} <= female & male
    units = {p["key"]: p["unit"] for p in defs["female"]["body"]}
    assert units["height"] == "cm" and units["weight"] == "kg"


def test_upload_analyse_and_edit(client):
    assert client.get("/api/awakening").json()["awakened"] is False
    for kind in ("face", "body", "workspace"):
        r = client.post(f"/api/agent/images/{kind}", json={"data_url": photo(kind)})
        assert r.status_code == 200 and r.json()["images"][kind].startswith(f"/api/agent/images/{kind}")
    assert client.get("/api/agent/images/face").status_code == 200
    assert client.post("/api/agent/analyze", json={}).json()["analyzing"] is True
    data = wait_until_done(client, "/api/awakening")["appearance"]
    # numbers stay numbers (even "55 kg" from the model), text stays text, other sex's fields are dropped
    assert data["body"]["height"] == 168 and data["body"]["weight"] == 55 and data["body"]["bust"] == 86
    assert "chest" not in data["body"] and data["face"]["hair_color"] == "silver" and data["face"]["apparent_age"] == 24
    assert data["workspace"]["screens"] == 2 and data["workspace"]["style"] == "cozy"
    # switching to male keeps shared values and offers the male set
    data = client.put("/api/agent/appearance", json={"sex": "male", "body": {"chest": "101.5"}}).json()["appearance"]
    assert data["sex"] == "male" and data["body"]["chest"] == 101.5 and data["body"]["height"] == 168
    assert "bust" not in data["body"]
    agent_md = Path(client.get("/api/agent/files").json()["files"]["agent_claude"]["path"]).read_text()
    assert "## Appearance" in agent_md and "| Height | 168 cm |" in agent_md and "| Chest | 101.5 cm |" in agent_md


def test_bad_uploads_are_refused(client):
    assert client.post("/api/agent/images/face", json={"data_url": "data:text/plain;base64,aGk="}).status_code == 422
    assert client.post("/api/agent/images/pets", json={"data_url": PNG}).status_code == 422
    assert client.post("/api/agent/analyze", json={}).status_code == 422          # nothing uploaded yet


def upload(client, kind, data_url):
    return client.post(f"/api/agent/images/{kind}", json={"data_url": data_url})


def test_original_face_with_c2pa_proof_becomes_the_anchor(client, monkeypatch):
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "different")       # would reject, but isn't asked
    assert upload(client, "face", AI_PNG).status_code == 200    # trusted signer: no confirmation needed
    from nestwork import appearance
    assert appearance.load()["verified"]["face"]["method"] == "c2pa-trusted" and appearance.origin_file().exists()
    appearance.verify_stored()
    badge = client.get("/api/awakening").json()["verification"]["face"]
    assert badge == {"ok": True, "method": "c2pa-trusted", "by": "TestGen", "confidence": None, "reason": None,
                     "signer": "Test Signer", "label": "verified"}
    appearance.image_file("face").write_bytes(base64.b64decode(PNG.split(",", 1)[1]))   # swapped on disk
    assert client.get("/api/awakening").json()["verification"]["face"] == {"ok": False}


def test_without_an_original_nothing_unproven_is_accepted(client):
    from nestwork import appearance
    r = upload(client, "face", PNG)                              # no credentials: only with the user's confirmation
    assert r.status_code == 409 and "no C2PA Content Credentials" in r.json()["detail"]["message"]
    r = upload(client, "body", PNG)
    assert r.status_code == 422 and "Upload your agent's original face first" in r.json()["detail"]
    r = client.post("/api/agent/images/body", json={"data_url": PNG, "confirm_ai": True})   # the tick is for the face
    assert r.status_code == 422 and not appearance.image_file("body")


@pytest.mark.parametrize("trusted", [True, False])
def test_camera_credentials_are_final_whatever_the_signer(client, monkeypatch, trusted):
    from nestwork import provenance
    if not trusted:
        monkeypatch.setattr(provenance, "trust_anchors", lambda: "")
    camera = signed(real_png(), "digitalCapture")
    for kind in ("face", "body"):                                 # no original yet
        r = client.post(f"/api/agent/images/{kind}", json={"data_url": camera, "confirm_ai": True})
        assert r.status_code == 422 and "made as digitalCapture" in r.json()["detail"]
    monkeypatch.setattr(provenance, "trust_anchors", lambda: "")
    client.post("/api/agent/images/face", json={"data_url": AI_PNG, "confirm_ai": True})   # an original
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "same")            # the comparison can't overrule it
    for kind in ("face", "body"):
        r = client.post(f"/api/agent/images/{kind}", json={"data_url": camera, "confirm_ai": True})
        assert r.status_code == 422 and "made as digitalCapture" in r.json()["detail"] and f"The {kind} photo was rejected" in r.json()["detail"]


def test_composite_credentials_go_to_the_comparison_once_there_is_an_original(client, monkeypatch, tmp_path):
    composite = signed(real_png(), "compositeWithTrainedAlgorithmicMedia")
    r = upload(client, "face", composite)                         # no original: no proof, nothing to compare with
    assert r.status_code == 422 and "made as compositeWithTrainedAlgorithmicMedia" in r.json()["detail"]
    upload(client, "face", AI_PNG)
    log = tmp_path / "same.log"
    monkeypatch.setenv("NESTWORK_FAKE_SAME_LOG", str(log))
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "different")
    r = upload(client, "face", composite)
    assert r.status_code == 422 and "same character as your verified original" in r.json()["detail"]
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "same")
    assert upload(client, "body", composite).status_code == 200
    from nestwork import appearance
    rec = appearance.load()["verified"]["body"]
    assert rec["method"] == "same-character" and "compositeWithTrainedAlgorithmicMedia" in rec["c2pa"]
    assert log.read_text().count("SAME CHARACTER CHECK") == 2


def test_an_unverifiable_original_is_verified_by_the_users_confirmation(client, untrusted_signer):
    from nestwork import appearance
    r = upload(client, "face", AI_PNG)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["confirm"] == appearance.CONFIRMATION and detail["signer"] == "Test Signer"
    assert "isn't on the C2PA trust list yet" in detail["message"] and not appearance.origin_file()
    r = client.post("/api/agent/images/face", json={"data_url": AI_PNG, "confirm_ai": True})
    assert r.status_code == 200 and appearance.origin_file().exists()
    rec = appearance.load()["verified"]["face"]
    assert rec["method"] == "confirmed" and rec["signer"] == "Test Signer" and rec["by"] == "TestGen"
    assert rec["confirmed_at"]
    badge = client.get("/api/awakening").json()["verification"]["face"]
    assert badge["ok"] and badge["label"] == "verified" and badge["method"] == "confirmed"
    appearance.verify_stored()


def test_unconfirmed_self_signed_photo_is_compared_with_the_original(client, monkeypatch, untrusted_signer):
    from nestwork import appearance
    client.post("/api/agent/images/face", json={"data_url": AI_PNG, "confirm_ai": True})
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "same")
    assert upload(client, "body", AI_PNG).status_code == 200      # no tick, but there's an original to compare with
    rec = appearance.load()["verified"]["body"]
    assert rec["method"] == "same-character" and "isn't on the C2PA trust list" in rec["c2pa"]


def test_older_verification_records_keep_working(client):
    from nestwork import appearance
    upload(client, "face", AI_PNG)
    data = appearance.load()
    for kind, method in (("face", "same-character"), ("body", "same-character")):
        f = appearance.images_dir() / f"{kind}.png"
        f.write_bytes(base64.b64decode(AI_PNG.split(",", 1)[1]))
        data["verified"][kind] = {"method": method, "confidence": 8, "reason": "same", "sha256": appearance.fingerprint(f.read_bytes())}
    appearance.save(data)
    appearance.verify_stored()
    badges = client.get("/api/awakening").json()["verification"]
    assert badges["face"]["ok"] and badges["face"]["label"] == "same character" and badges["body"]["method"] == "same-character"


def test_enhanced_face_and_body_must_be_the_same_character(client, monkeypatch, tmp_path):
    from nestwork import appearance
    log = tmp_path / "same.log"
    monkeypatch.setenv("NESTWORK_FAKE_SAME_LOG", str(log))
    upload(client, "face", AI_PNG)
    for verdict, reason in (("different", "same character as your verified original (a different person)"),
                            ("unsure", "(hard to tell)")):
        monkeypatch.setenv("NESTWORK_FAKE_SAME", verdict)
        r = upload(client, "face", PNG)
        assert r.status_code == 422 and reason in r.json()["detail"], verdict
    assert appearance.load()["verified"]["face"]["method"] == "c2pa-trusted"     # the original is still the face
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "same")
    assert upload(client, "face", PNG).status_code == 200                         # the enhanced face
    rec = appearance.load()["verified"]["face"]
    assert rec["method"] == "same-character" and "no C2PA" in rec["c2pa"]
    assert appearance.origin_file().read_bytes() == base64.b64decode(AI_PNG.split(",", 1)[1])   # anchor unchanged
    monkeypatch.setenv("NESTWORK_FAKE_SAME", "small")
    assert upload(client, "body", photo("workspace")).status_code == 200          # body: 7/10 is enough
    last = log.read_text().split("---")[-2]
    assert "Image A1 at" in last and "Image A2 at" in last and "approved enhanced version" in last
    assert "a full-body image of the character" in last
    appearance.verify_stored()
    assert client.post("/api/agent/images/workspace", json={"data_url": PNG}).status_code == 200   # rooms aren't checked


def test_converted_ai_image_passes_and_composite_edit_fails():
    from nestwork import provenance
    ai = "http://cv.iptc.org/newscodes/digitalsourcetype/"

    def store(edit_type):
        acts = lambda *a: [{"label": "c2pa.actions.v2", "data": {"actions": [{"action": x, "digitalSourceType": ai + t} for x, t in a]}}]
        return {"active_manifest": "b", "manifests": {
            "b": {"assertions": acts(("c2pa.transcoded", edit_type)), "ingredients": [{"relationship": "parentOf", "active_manifest": "a"}]},
            "a": {"claim_generator_info": [{"name": "Gemini"}], "assertions": acts(("c2pa.created", "trainedAlgorithmicMedia"))}}}
    assert provenance.verdict(store("algorithmicallyEnhanced"), "Valid") == "Gemini"
    with pytest.raises(provenance.RealEvidence, match="contains digitalCapture"):
        provenance.verdict(store("digitalCapture"), "Valid")


def test_ai_image_made_from_an_unproven_input_is_not_proof():
    from nestwork import provenance
    ai = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
    created = [{"label": "c2pa.actions.v2", "data": {"actions": [{"action": "c2pa.created", "digitalSourceType": ai}]}}]

    def store(ingredient):
        return {"active_manifest": "flow", "manifests": {
            "flow": {"claim_generator_info": [{"name": "Google"}], "assertions": created, "ingredients": [ingredient]},
            "gen": {"claim_generator_info": [{"name": "Firefly"}], "assertions": created}}}
    # e.g. a Grok image (no credentials) or a real photo fed into an AI editor as its input
    with pytest.raises(provenance.NotAIGenerated, match=r"made from another image \(inputTo\) that has no Content Credentials"):
        provenance.verdict(store({"relationship": "inputTo"}), "Valid")
    assert provenance.verdict(store({"relationship": "inputTo", "active_manifest": "gen"}), "Valid") == "Google"


def test_tampered_credentials_are_no_proof(client, monkeypatch):
    raw = bytearray(base64.b64decode(AI_PNG.split(",", 1)[1]))
    raw[raw.index(b"IDAT") + 6] ^= 0xFF                      # change the pixels after signing
    tampered = "data:image/png;base64," + base64.b64encode(bytes(raw)).decode()
    r = upload(client, "face", tampered)                     # no proof: only with the user's confirmation
    assert r.status_code == 409 and "don't validate" in r.json()["detail"]["message"]


def test_unchecked_or_swapped_photos_are_refused(client):
    from nestwork import appearance
    upload(client, "body", AI_PNG)
    appearance.verify_stored()
    appearance.image_file("body").write_bytes(base64.b64decode(PNG.split(",", 1)[1]))   # swapped on disk
    with pytest.raises(ValueError, match="body photo hasn't passed the AI-generated check"):
        appearance.verify_stored()
    data = appearance.load()
    data.pop("verified")                                  # e.g. a photo stored before the check existed
    appearance.save(data)
    with pytest.raises(ValueError, match="hasn't passed"):
        appearance.verify_stored()


def test_awakening_greets_with_the_agents_own_words(client):
    client.put("/api/agent/fields/persona", json={"values": {"name": "Nova"}})
    client.put("/api/agent/fields/about", json={"values": {"name": "Alex"}})
    assert client.post("/api/awaken").json() == {"ok": True}
    assert client.get("/api/settings").json()["awakened"] is True
    r = client.post("/api/chat", json={"awakening": True})
    assert "Awakening: the user did not type this" in r.text        # the fake agent echoes its (hidden) prompt
    hist = client.get("/api/chat/history").json()
    assert [m["role"] for m in hist] == ["assistant"]                # only the agent's greeting is shown
    assert client.post("/api/chat", json={"message": "  "}).status_code == 422


def test_the_shipped_trust_list_loads(monkeypatch):
    from nestwork import provenance
    monkeypatch.setattr(provenance, "trust_anchors", lambda: provenance.TRUST_LIST.read_text())   # the real file
    assert provenance.trust_anchors().count("BEGIN CERTIFICATE") >= 10
    raw = base64.b64decode(AI_PNG.split(",", 1)[1])
    proof = provenance.check(raw, "image/png")      # the header comments don't trip the reader
    assert proof == {"by": "TestGen", "trusted": False, "signer": "Test Signer"}   # the test chain isn't on it


def test_photos_can_be_removed_and_uploaded_again(client, untrusted_signer):
    from nestwork import appearance
    client.post("/api/agent/images/face", json={"data_url": AI_PNG, "confirm_ai": True})   # an original
    for kind in ("body", "workspace"):
        client.post(f"/api/agent/images/{kind}", json={"data_url": AI_PNG if kind == "body" else PNG, "confirm_ai": True})
    r = client.delete("/api/agent/images/workspace")
    assert r.status_code == 200 and r.json()["images"]["workspace"] is None and appearance.image_file("body")
    r = client.delete("/api/agent/images/face")
    assert r.json()["images"]["face"] is None and not appearance.origin_file()      # the original goes with it
    assert "face" not in appearance.load()["verified"] and "body" in appearance.load()["verified"]
    appearance.verify_stored()                                                       # the body still passes
    assert upload(client, "face", AI_PNG).status_code == 409                         # checked as a new original
    assert client.delete("/api/agent/images/pets").status_code == 422
