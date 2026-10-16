"""Connected apps: only Nestwork unless the user turns on claude.ai connectors, and then only those."""

from fastapi.testclient import TestClient

from nestwork import connectors, db
from nestwork.chat import command

SESSION = {"mode": "new", "session_id": "s1"}


def test_prefix():
    assert connectors.prefix("claude.ai Google Calendar") == "mcp__claude_ai_Google_Calendar"


def test_off_by_default_strict():
    from nestwork.main import app
    with TestClient(app):
        conn = db.connect()
        cmd = command(SESSION, {}, "Zoe", connectors.flags(conn))
        conn.close()
    assert "--strict-mcp-config" in cmd and "--disallowedTools" not in cmd


def test_turning_apps_on():
    from nestwork.main import app
    with TestClient(app) as c:
        conn = db.connect()
        with conn:
            connectors.remember(conn, [{"name": "claude.ai Gmail", "status": "connected"},
                                       {"name": "claude.ai Google Calendar", "status": "needs-auth"},
                                       {"name": "blender", "status": "connected"}, {"name": "nestwork", "status": "connected"},
                                       {"name": "claude-in-chrome", "status": "connected"}])
        conn.close()
        assert c.put("/api/connectors", json={"enabled": ["claude.ai Google Calendar", "made up"]}).json()["enabled"] == \
            ["claude.ai Google Calendar"]
        servers = {s["label"]: s for s in c.get("/api/connectors").json()["servers"]}
        assert servers["Google Calendar"]["enabled"] and not servers["Gmail"]["enabled"] and "nestwork" not in servers
        conn = db.connect()
        cmd = command(SESSION, {}, "Zoe", connectors.flags(conn))
        conn.close()
    assert "--strict-mcp-config" not in cmd
    assert "mcp__claude_ai_Google_Calendar" in cmd[cmd.index("--allowedTools") + 1].split(",")
    assert set(cmd[cmd.index("--disallowedTools") + 1].split(",")) == {"mcp__claude_ai_Gmail", "mcp__blender"}   # not Chrome: the broad tools turn it on
    assert "connected apps: Google Calendar" in cmd[cmd.index("--append-system-prompt") + 1]


def test_sign_in_opens_claude_codes_app_list(monkeypatch):
    import sys
    from nestwork import persona
    opened = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(connectors.subprocess, "Popen", lambda args: opened.append(args))
    monkeypatch.setattr("nestwork.chat.claude_bin", lambda: "/usr/local/bin/claude")
    assert connectors.open_sign_in() is True
    script = persona.agent_dir() / "sign-in-to-apps.command"
    assert opened == [["open", "-a", "Terminal", str(script)]]
    assert script.read_text().rstrip().endswith("/usr/local/bin/claude /mcp")
    monkeypatch.setattr(sys, "platform", "linux")
    assert connectors.open_sign_in() is False
