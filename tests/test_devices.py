"""Who may use Nestwork: this computer freely; a phone (through Tailscale) only with a paired device key."""

import pytest
from fastapi.testclient import TestClient

PHONE = {"x-forwarded-for": "100.101.102.103", "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"}
TS = "https://mac.tail1234.ts.net"


@pytest.fixture()
def mac():
    from nestwork.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def phone():
    from nestwork.main import app
    with TestClient(app, base_url=TS, headers=PHONE) as c:
        yield c


def code_for(mac, monkeypatch):
    from nestwork import phone as ph
    monkeypatch.setattr(ph, "status", lambda port: {"serving": True, "url": TS})
    r = mac.post("/api/phone/pair-code").json()
    assert r["url"] == f"{TS}/pair#{r['code']}" and r["qr_svg"].startswith("<svg")
    return r["code"]


def test_a_phone_needs_pairing(mac, phone, monkeypatch):
    assert phone.get("/api/projects").status_code == 401
    assert "Pairing code" in phone.get("/").text                        # the connect page instead of the app
    assert phone.get("/api/phone").status_code == 403                   # managing devices: only on the Mac
    assert phone.post("/api/pair", json={"code": "WRONG-CODES"}).status_code == 400

    code = code_for(mac, monkeypatch)
    r = phone.post("/api/pair", json={"code": code})
    assert r.status_code == 200 and r.json()["name"] == "iPhone"
    assert "httponly" in r.headers["set-cookie"].lower() and "secure" in r.headers["set-cookie"].lower()
    assert phone.get("/api/projects").status_code == 200                 # the cookie is the key now
    assert phone.post("/api/pair", json={"code": code}).status_code == 400   # codes work once

    # The Home Screen app's first launch trades the install code for its own key.
    install = r.json()["install_code"]
    assert f"/?install={install}" in phone.get(f"/manifest.webmanifest?install={install}").text
    from nestwork.main import app
    with TestClient(app, base_url=TS, headers=PHONE) as app_jar:
        redirect = app_jar.get(f"/?install={install}", follow_redirects=False)
        assert redirect.status_code == 303 and app_jar.get("/api/projects").status_code == 200
        assert mac.get("/api/phone").json()["devices"][0]["name"] == "iPhone"

        dev = mac.get("/api/phone").json()["devices"][0]["id"]
        mac.patch(f"/api/devices/{dev}", json={"name": "Alex's iPhone"})
        mac.delete(f"/api/devices/{dev}")                                # revoking ends both keys
        assert phone.get("/api/projects").status_code == 401 and app_jar.get("/api/projects").status_code == 401


def test_bearer_key_hosts_and_cross_site(mac, monkeypatch):
    from nestwork.main import app
    code = code_for(mac, monkeypatch)
    with TestClient(app, base_url=TS, headers=PHONE) as c:
        token = c.post("/api/pair", json={"code": code}).cookies.get("nw_device")
    with TestClient(app, base_url=TS, headers={**PHONE, "authorization": f"Bearer {token}"}) as shortcut:
        assert shortcut.get("/api/today").status_code == 200              # e.g. an iOS Shortcut with the key
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/api/projects").status_code == 421                   # DNS-rebinding protection
    assert mac.post("/api/blocks", json={"title": "x"}, headers={"origin": "http://evil.example"}).status_code == 403
    assert mac.post("/api/blocks", json={"title": "x"}, headers={"origin": "http://testserver"}).status_code == 201


def test_too_many_wrong_codes(phone):
    from nestwork import devices
    devices._fails.clear()
    for _ in range(devices.MAX_FAILS):
        phone.post("/api/pair", json={"code": "AAAAA-AAAAA"})
    assert phone.post("/api/pair", json={"code": "AAAAA-AAAAA"}).status_code == 429
    devices._fails.clear()
