"""Cheap checks on the frontend (there's no JS test runner): every script parses, every script the page
loads exists, and every line icon the code asks for is drawn."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "nestwork" / "static"
SCRIPTS = sorted(STATIC.glob("*.js"))
PAGE_SCRIPTS = [p for p in SCRIPTS if p.name != "sw.js"]          # sw.js is the service worker, registered by app.js


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse(script):
    r = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_page_loads_every_script_once():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    loaded = re.findall(r'<script src="/static/([\w.-]+\.js)"', html)
    assert sorted(loaded) == sorted(p.name for p in PAGE_SCRIPTS)
    assert len(loaded) == len(set(loaded))


def test_every_icon_used_exists():
    code = "\n".join(p.read_text(encoding="utf-8") for p in SCRIPTS)
    block = re.search(r"const LINE_ICONS = \{(.*?)\n\};", code, re.S).group(1)
    drawn = set(re.findall(r"^\s*(\w+):", block, re.M))
    used = set(re.findall(r"\bico\('(\w+)'", code)) | set(re.findall(r"\bicon: '(\w+)'", code))
    mapped = set(re.findall(r"'[^']+': '(\w+)'", re.search(r"const EMOJI_ICON = \{(.*?)\};", code, re.S).group(1)))
    assert not (used | mapped) - drawn, f"missing icons: {sorted((used | mapped) - drawn)}"


def test_service_worker_caches_files_that_exist():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    for path in re.findall(r"'(/static/[^']+)'", sw):
        assert (STATIC / path.removeprefix("/static/")).exists(), path


def test_no_two_scripts_declare_the_same_global():
    """Page scripts share one global scope: a name declared twice stops the later script from loading."""
    seen = {}
    for p in PAGE_SCRIPTS:
        for name in re.findall(r"^(?:async )?(?:function\*? |const |let |var |class )(\w+)", p.read_text(encoding="utf-8"), re.M):
            assert name not in seen or seen[name] == p.name, f"{name} is declared in both {seen[name]} and {p.name}"
            seen[name] = p.name


def test_hidden_file_inputs_stay_inside_their_button():
    """An sr-only (absolutely positioned) file input must sit in a `relative` label: otherwise it's placed against
    the popup, and Chrome scrolls the popup's frame to it after the file picker closes, leaving the popup blank."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in PAGE_SCRIPTS)
    for label, attrs in re.findall(r'<label([^>]*)>\s*<input type="file"([^>]*)>', code):
        if "sr-only" in attrs:
            assert re.search(r'class="[^"]*\brelative\b', label), f"label without `relative`: {label.strip()[:80]}"
