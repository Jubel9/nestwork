"""Reaching Nestwork from your phone through Tailscale.

Tailscale puts your Mac and your phone on a private network only your devices can join. `tailscale serve`
then gives Nestwork an HTTPS address on it (https://<your-mac>.<tailnet>.ts.net) that passes requests on to
127.0.0.1, so Nestwork itself still only listens on this computer and nothing is exposed to the internet.
This module finds the Tailscale command, reads its status, and turns that address on or off. Who may use it is
decided per device (devices.py).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

CANDIDATES = ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"]


def cli() -> str | None:
    """The Tailscale command that actually works (a leftover link from an uninstalled app doesn't)."""
    for c in [shutil.which("tailscale"), *CANDIDATES]:
        if c and Path(c).exists():
            try:
                r = subprocess.run([c, "version"], capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r.returncode == 0 and r.stdout.strip():
                return c
    return None


def _run(*args, timeout=10) -> subprocess.CompletedProcess:
    return subprocess.run([cli(), *args], capture_output=True, text=True, timeout=timeout)


def status(port: int) -> dict:
    exe = cli()
    out = {"installed": bool(exe), "running": False, "dns_name": None, "https_ready": False, "serving": False, "url": None}
    if not exe:
        return out
    try:
        st = json.loads(_run("status", "--json").stdout or "{}")
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return out
    me = st.get("Self") or {}
    out["running"] = st.get("BackendState") == "Running"
    out["dns_name"] = (me.get("DNSName") or "").rstrip(".") or None
    out["https_ready"] = bool(st.get("CertDomains"))
    out["login_url"] = st.get("AuthURL") or None
    if out["running"] and out["dns_name"]:
        try:
            serve = json.loads(_run("serve", "status", "--json").stdout or "{}")
        except (ValueError, subprocess.TimeoutExpired, OSError):
            serve = {}
        handlers = ((serve.get("Web") or {}).get(f"{out['dns_name']}:443") or {}).get("Handlers") or {}
        proxy = (handlers.get("/") or {}).get("Proxy", "")
        out["serving"] = proxy.rstrip("/").endswith(f":{port}")
        out["url"] = f"https://{out['dns_name']}" if out["serving"] else None
    return out


def enable(port: int) -> dict:
    """Serve Nestwork on the tailnet over HTTPS. Tailscale may first ask to allow it in a browser: then the
    returned dict has a `consent_url` to open, and this can be tried again after."""
    if not cli():
        raise RuntimeError("Tailscale isn't installed on this Mac.")
    try:
        r = _run("serve", "--bg", str(port), timeout=25)
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        url = re.search(r"https://login\.tailscale\.com/\S+", text)
        return {"ok": False, "consent_url": url.group(0) if url else None,
                "message": "Tailscale is waiting for you to allow this in your browser."}
    text = r.stdout + r.stderr
    url = re.search(r"https://login\.tailscale\.com/\S+", text)
    if r.returncode != 0 or url:
        return {"ok": False, "consent_url": url.group(0) if url else None, "message": text.strip()[-400:]}
    return {"ok": True}


def disable() -> None:
    if cli():
        _run("serve", "--https=443", "off")
