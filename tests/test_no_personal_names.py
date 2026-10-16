"""The owner's name must not be hard-coded anywhere in the repo: names come from the persona and About me at run
time, and tests use neutral names (Alex for the user, Nova for the agent). The names to look for are read on this
computer, never written here: the About me name and nicknames in ~/.claude/CLAUDE.md, and git's user.name."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP = ("nestwork/static/vendor/", "nestwork/c2pa_trust_list.pem",
        "LICENSE", "README.md")   # these two may name the owner (copyright holder, author)


def owner_names() -> set[str]:
    from nestwork import persona
    words = []
    about = Path.home() / ".claude" / "CLAUDE.md"          # the real one, read only (the tests' shared dir is a temp)
    if about.exists():
        text = about.read_text(encoding="utf-8", errors="replace")
        for label in ("Name", "Also call me"):
            m = persona._line_re(label).search(text)
            if m:
                words += re.split(r"[\s,]+", m.group(1))
    try:
        words += subprocess.run(["git", "config", "user.name"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    except OSError:
        pass
    return {w for w in words if len(w) >= 3 and w.isalpha()}


def test_owner_name_is_not_in_tracked_files():
    names = owner_names()
    if not names:
        pytest.skip("no owner name on this computer (About me and git user.name are empty)")
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names))) + r")\b", re.I)
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    hits = []
    for name in filter(None, files.split("\0")):
        if name.startswith(SKIP) or not (ROOT / name).is_file():
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:                           # images, sounds, fonts
            continue
        hits += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
    assert not hits, f"the owner's name is hard-coded in: {', '.join(hits)}"
