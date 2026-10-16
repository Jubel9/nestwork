"""Claude Code skills the agent can install or update itself (MCP `install_skill`, `list_skills`).

Claude Code won't let a headless session write in ~/.claude, so the agent asks Nestwork to do it. A skill
comes from a GitHub repository (cloned with git, without its history); the folder holding its SKILL.md is
copied to ~/.claude/skills/<name>. An older copy is moved to ~/.claude/backups/skills/ first, never deleted.
Installing follows the Agent access setting like any other change (agent.request).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

GIT_BASE = os.environ.get("NESTWORK_SKILLS_GIT_BASE", "https://github.com")   # tests point this at local repos
SOURCE = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([\w.-]+)(?:/(.+?))?)?/?$")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RESERVED = {"synced"}
SOURCE_FILE = ".nestwork-source.json"


def skills_dir() -> Path:
    return Path(os.environ.get("NESTWORK_SKILLS_DIR") or Path.home() / ".claude" / "skills")


def backups_dir() -> Path:
    return skills_dir().parent / "backups" / "skills"


def parse_source(source: str, path: str = "") -> dict:
    """"owner/repo", a github.com URL, or a …/tree/<branch>/<folder> URL → {repo, branch, path}."""
    m = SOURCE.match(source.strip())
    if not m:
        raise ValueError("The source must be a GitHub repository: “owner/repo” or a https://github.com/… link.")
    owner, repo, branch, sub = m.groups()
    sub = (path or sub or "").strip("/")
    if ".." in Path(sub).parts:
        raise ValueError("The folder can't go outside the repository.")
    return {"repo": f"{owner}/{repo}", "branch": branch or "", "path": sub}


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    head = m.group(1) if m else ""
    get = lambda key: (re.search(rf"^\s*{key}:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", head, re.M) or [None, ""])[1]
    tools = re.search(r"^allowed-tools:[ \t]*(.*)((?:\n[ \t]+-.*)*)", head, re.M)
    allowed = []
    if tools:   # "allowed-tools: Read, Bash(git:*)" or a YAML list under it
        allowed = [t.strip(" \"'") for t in re.split(r",\s*|\s+(?![^(]*\))", tools.group(1).strip(" []"))
                   if t.strip(" \"'")]
        allowed += [l.strip()[1:].strip(" \"'") for l in tools.group(2).splitlines() if l.strip()]
    desc = get("description")
    if desc in ("|", ">", "|-", ">-"):
        block = re.search(r"^description:\s*[|>]-?\s*\n((?:[ \t]+.*\n?)+)", head, re.M)
        desc = " ".join(l.strip() for l in block.group(1).splitlines()) if block else ""
    return {"name": get("name"), "version": get("version"), "description": desc, "allowed_tools": allowed}


def info(folder: Path) -> dict:
    meta = _frontmatter(folder / "SKILL.md")
    source = ""
    try:
        source = json.loads((folder / SOURCE_FILE).read_text())["repo"]
    except (OSError, ValueError, KeyError):
        try:
            source = json.loads((folder / ".claude-plugin" / "plugin.json").read_text()).get("repository", "")
        except (OSError, ValueError):
            pass
    return {**meta, "name": meta["name"] or folder.name, "folder": folder.name, "source": source}


def installed() -> list[dict]:
    """The user's own skills (~/.claude/skills/*/SKILL.md), not the ones claude.ai syncs."""
    root = skills_dir()
    if not root.is_dir():
        return []
    return [info(d) for d in sorted(root.iterdir()) if d.name not in RESERVED and not d.name.startswith(".")
            and (d / "SKILL.md").is_file()]


def describe(source: str, path: str = "") -> str:
    try:
        s = parse_source(source, path)
    except ValueError:
        return f"Install the skill from “{source}”"
    return f"Install or update a Claude Code skill from GitHub {s['repo']}" + (f" (folder {s['path']})" if s["path"] else "")


def check(source: str, path: str = "") -> str | None:
    """Why it can't be installed, without downloading anything (None if it looks fine)."""
    try:
        parse_source(source, path)
    except ValueError as e:
        return str(e)
    if not shutil.which("git"):
        return "git isn't installed on this computer."
    return None


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise ValueError("Downloading the skill took too long.")
    if r.returncode:
        raise ValueError("git couldn't get it: " + ((r.stderr.strip().splitlines() or ["unknown error"])[-1]))
    return r.stdout.strip()


def install(source: str, path: str = "") -> dict:
    """Download the skill and put it in ~/.claude/skills. Returns {"notices": [...]}; ValueError if it can't."""
    s = parse_source(source, path)
    with tempfile.TemporaryDirectory(prefix="nestwork-skill-") as tmp:
        repo = Path(tmp) / "repo"
        _git("clone", "--depth", "1", *(["--branch", s["branch"]] if s["branch"] else []),
             f"{GIT_BASE}/{s['repo']}", str(repo))
        commit = _git("rev-parse", "--short", "HEAD", cwd=repo)
        if s["path"]:
            folder = repo / s["path"]
            if not (folder / "SKILL.md").is_file():
                raise ValueError(f"There's no SKILL.md in “{s['path']}” of {s['repo']}.")
        elif (repo / "SKILL.md").is_file():
            folder = repo
        else:
            found = sorted(p.parent.relative_to(repo).as_posix() for p in repo.rglob("SKILL.md") if ".git" not in p.parts)
            if len(found) != 1:
                raise ValueError(f"{s['repo']} has no SKILL.md." if not found else
                                 f"{s['repo']} has {len(found)} skills; say which folder (path): " + ", ".join(found[:30]))
            folder = repo / found[0]
        new = info(folder)
        name = (new["name"] if NAME.match(new["name"] or "") else folder.name).lower()
        if not NAME.match(name) or name in RESERVED:
            raise ValueError(f"“{name}” can't be used as a skill name.")
        dest = skills_dir() / name
        notices = []
        old = None
        if dest.exists() or dest.is_symlink():
            old = info(dest) if (dest / "SKILL.md").is_file() else None
            backups_dir().mkdir(parents=True, exist_ok=True)
            backup = backups_dir() / f"{name}-{(old or {}).get('version') or 'old'}-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.move(str(dest), str(backup))
            notices.append(f"The old copy is kept in {backup}.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, dest, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        (dest / SOURCE_FILE).write_text(json.dumps({"repo": s["repo"], "branch": s["branch"], "path": s["path"],
                                                     "commit": commit, "installed_at": time.strftime("%Y-%m-%d %H:%M")},
                                                    indent=2), encoding="utf-8")
    version = new["version"] or f"commit {commit}"
    head = (f"Updated “{name}” from {old['version'] or 'an older version'} to {version}" if old else
            f"Installed “{name}” {version}") + f" from {s['repo']}. It's available from the next message."
    if new["allowed_tools"]:
        notices.append("It lets itself use these tools without asking: " + ", ".join(new["allowed_tools"]) + ".")
    return {"notices": [head, *notices]}
