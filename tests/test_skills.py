"""Skills the agent installs or updates: from a git repository into ~/.claude/skills, keeping a backup."""

import subprocess

import pytest

from nestwork import skills

SKILL = """---
name: humanizer
version: {v}
description: |
  Make text sound human.
allowed-tools:
  - Read
  - Edit
---
# Humanizer
"""


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NESTWORK_SKILLS_DIR", str(tmp_path / "claude" / "skills"))
    monkeypatch.setattr(skills, "GIT_BASE", str(tmp_path / "github"))
    repo = tmp_path / "github" / "blader" / "humanizer"
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)
    git("config", "user.name", "t", cwd=repo)

    def release(version):
        (repo / "SKILL.md").write_text(SKILL.format(v=version))
        git("add", "-A", cwd=repo)
        git("commit", "-qm", version, cwd=repo)
    release("2.8.2")
    return tmp_path, release


def test_parse_source():
    assert skills.parse_source("blader/humanizer") == {"repo": "blader/humanizer", "branch": "", "path": ""}
    assert skills.parse_source("https://github.com/anthropics/skills/tree/main/skills/pdf") == \
        {"repo": "anthropics/skills", "branch": "main", "path": "skills/pdf"}
    for bad in ("https://evil.example.com/a/b", "rm -rf /", "a/b/tree/main/../../x"):
        with pytest.raises(ValueError):
            skills.parse_source(bad)


def test_install_then_update_keeps_a_backup(home):
    tmp, release = home
    first = skills.install("https://github.com/blader/humanizer")["notices"]
    assert first[0].startswith("Installed “humanizer” 2.8.2") and "Read, Edit" in first[-1]
    assert skills.installed()[0]["source"] == "blader/humanizer"
    release("3.1.0")
    notes = skills.install("blader/humanizer")["notices"]
    assert notes[0].startswith("Updated “humanizer” from 2.8.2 to 3.1.0")
    assert "version: 3.1.0" in (tmp / "claude" / "skills" / "humanizer" / "SKILL.md").read_text()
    backup, = (tmp / "claude" / "backups" / "skills").iterdir()
    assert backup.name.startswith("humanizer-2.8.2-") and "2.8.2" in (backup / "SKILL.md").read_text()
    assert not (tmp / "claude" / "skills" / "humanizer" / ".git").exists()


def test_install_follows_agent_access(home, monkeypatch, tmp_path):
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "test.db"))
    from fastapi.testclient import TestClient
    from nestwork import agent
    from nestwork.main import app
    with TestClient(app) as c:
        c.put("/api/settings", json={"agent_access": "ask"})
        out = agent.request("install_skill", {"source": "blader/humanizer"})
        assert out.startswith("Proposed") and not skills.installed()
        pid = c.get("/api/proposals").json()[0]["id"]
        assert c.post(f"/api/proposals/{pid}/approve").status_code == 200
        assert skills.installed()[0]["version"] == "2.8.2"
        c.put("/api/settings", json={"agent_access": "full"})
        assert "Not done" in agent.request("install_skill", {"source": "blader/nothing-here"})
        assert "must be a GitHub repository" in agent.request("install_skill", {"source": "http://x.y/z"})
