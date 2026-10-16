"""Claude subscription usage limits read from Claude Code's /usage output."""

import pytest
from fastapi.testclient import TestClient

from nestwork import usage

SAMPLE = """You are currently using your subscription to power your Claude Code usage

Current session: 13% used · resets Oct 12 at 2:30am (Europe/Berlin)
Current week (all models): 36% used · resets Oct 16 at 6pm (Europe/Berlin)
Current week (Sonnet only): 4% used

What's contributing to your limits usage?"""


def test_parse_keeps_claudes_own_numbers_and_wording():
    got = usage.parse(SAMPLE)
    assert [(l["key"], l["used_percent"], l["resets"]) for l in got] == [
        ("session", 13.0, "Oct 12 at 2:30am (Europe/Berlin)"),
        ("week", 36.0, "Oct 16 at 6pm (Europe/Berlin)"),
        ("week_sonnet_only", 4.0, None)]
    assert got[1]["label"] == "Current week (all models)"


@pytest.fixture()
def api(monkeypatch):
    usage._cache.update(at=0.0, data=None)
    calls = []

    async def fake_run():
        calls.append(1)
        return SAMPLE
    monkeypatch.setattr(usage, "_run", fake_run)
    from nestwork.main import app
    with TestClient(app) as c:
        c.calls = calls
        yield c


def test_endpoint_caches_and_refreshes(api):
    first = api.get("/api/claude/usage").json()
    assert first["subscription"] and first["limits"][0]["used_percent"] == 13 and "error" not in first
    api.get("/api/claude/usage")
    assert len(api.calls) == 1                     # reused for a minute
    api.get("/api/claude/usage?refresh=true")
    assert len(api.calls) == 2


def test_missing_claude_is_reported(monkeypatch):
    usage._cache.update(at=0.0, data=None)
    monkeypatch.setattr(usage.chat, "claude_bin", lambda: None)
    import anyio
    got = anyio.run(usage.read)
    assert got["limits"] == [] and "wasn't found" in got["error"]
