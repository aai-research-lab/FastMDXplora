"""Ctrl+C stops the GUI at once and says so, on a line of its own.

Stopped, the GUI printed nothing: the terminal had echoed ^C, and without a
newline after it the shell's next prompt began on the same line (zsh marks
that with a `%`).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_one_ctrl_c_stops_it_and_it_says_so(tmp_path) -> None:
    port = _free_port()
    env = {**os.environ, "FASTMDXPLORA_CONFIG_DIR": str(tmp_path / "settings")}
    gui = subprocess.Popen(
        [sys.executable, "-m", "fastmdxplora", "gui", "--output", str(tmp_path),
         "--no-browser", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read(1)
                break
            except OSError:
                time.sleep(0.25)
        else:
            raise AssertionError("the GUI did not start")
        asked = time.monotonic()
        gui.send_signal(signal.SIGINT)
        said, _ = gui.communicate(timeout=30)
        took = time.monotonic() - asked
    finally:
        if gui.poll() is None:
            gui.kill()
    assert gui.returncode == 0, said
    assert took < 10
    assert said.endswith("\nGUI stopped.\n"), said[-200:]
