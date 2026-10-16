"""Keep the Mac awake while it's plugged in, so your phone can reach Nestwork (Settings → 📱 Phone).

Uses macOS's own `caffeinate -s`: the system doesn't sleep while on power (the screen still can), and it
stops by itself when Nestwork stops (-w with this process id). Other systems: not available.
"""

import os
import shutil
import subprocess
import sys

_proc: subprocess.Popen | None = None


def available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("caffeinate"))


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def start() -> bool:
    global _proc
    if running() or not available():
        return running()
    _proc = subprocess.Popen(["caffeinate", "-s", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def stop() -> None:
    global _proc
    if running():
        _proc.terminate()
    _proc = None


def apply(on: bool) -> bool:
    if on:
        return start()
    stop()
    return False
