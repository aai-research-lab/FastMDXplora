"""The page is told when the study changes, rather than asking every few
seconds.

The dashboard asked for everything it draws every three seconds whether or
not anything had changed: seven requests a poll from each open tab, and
still up to three seconds behind the run. `GET /api/stream` sends one
server-sent event when the study's files or the run's state change, and the
page asks then; its poll slows to a fall-back while the stream is open.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from fastmdxplora.gui.stream import MOST_ENTRIES, changes, signature


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestTheStream:
    def test_one_event_at_once_then_one_per_change(self):
        clock, written = _Clock(), []
        looks = iter([1, 1, 2, 2, 3] + [3] * 10_000)
        why = changes(written.append, lambda: next(looks), closing=lambda: False,
                      clock=clock, sleep=clock.sleep, lifetime=10.0)
        assert written[0].startswith(b"retry: 3000\n\nevent: change")
        # One at once, then one for 1 to 2 and one for 2 to 3.
        assert b"".join(written).count(b"event: change") == 3
        assert why == "lifetime"

    def test_a_quiet_stream_says_it_is_still_there(self):
        clock, written = _Clock(), []
        changes(written.append, lambda: 1, closing=lambda: False,
                clock=clock, sleep=clock.sleep, lifetime=40.0)
        assert written.count(b": still here\n\n") == 2

    def test_it_ends_when_the_server_does(self):
        clock, written = _Clock(), []
        state = {"closing": False}

        def look():
            state["closing"] = clock.now >= 2.0
            return 1
        assert changes(written.append, look, closing=lambda: state["closing"],
                       clock=clock, sleep=clock.sleep) == "closing"

    def test_and_when_the_page_goes(self):
        def write(chunk):
            raise BrokenPipeError
        assert changes(write, lambda: 1, closing=lambda: False) == "gone"


class TestWhatIsLookedAt:
    def test_a_file_three_folders_down(self, tmp_path):
        record = tmp_path / "analysis" / "rmsd" / "options.json"
        record.parent.mkdir(parents=True)
        record.write_text("{}", encoding="utf-8")
        before = signature(tmp_path, {"status": "running"})
        record.write_text('{"findings": {}}', encoding="utf-8")
        assert signature(tmp_path, {"status": "running"}) != before

    def test_the_runs_state(self, tmp_path):
        assert signature(tmp_path, {"status": "running"}) != signature(
            tmp_path, {"status": "completed"})

    def test_no_study_is_not_walked(self):
        assert signature(None, {}) == ("", None, None, None, None)

    def test_a_huge_folder_is_bounded(self, tmp_path):
        from fastmdxplora.gui.stream import _entries

        many = tmp_path / "frames"
        many.mkdir()
        for i in range(MOST_ENTRIES + 50):
            (many / f"{i}.pdb").touch()
        assert len(_entries(tmp_path)) == MOST_ENTRIES


def _study(root: Path) -> Path:
    (root / "simulation").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"phases": []}), encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(
        json.dumps({"stage": "production", "current_step": 1}), encoding="utf-8")
    return root


def _events(sock_file, count, timeout):
    """The next ``count`` events off an open stream."""
    seen, until = [], time.monotonic() + timeout
    while len(seen) < count and time.monotonic() < until:
        line = sock_file.readline()
        if line.startswith(b"event: "):
            seen.append(line.strip().decode())
    return seen


def test_the_server_sends_a_change_and_lets_go_on_shutdown(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    stream = socket.create_connection(("127.0.0.1", session.port), timeout=10)
    try:
        stream.sendall(b"GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        reader = stream.makefile("rb")
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += reader.readline()
        assert b"200" in head.split(b"\r\n", 1)[0]
        assert b"text/event-stream" in head and b"X-Accel-Buffering: no" in head
        assert _events(reader, 1, 5) == ["event: change"]
        (root / "simulation" / "live_status.json").write_text(
            json.dumps({"stage": "production", "current_step": 2}), encoding="utf-8")
        assert _events(reader, 1, 5) == ["event: change"]
    finally:
        # With a stream still open, stopping takes a look's time, not the
        # stream's lifetime: `server_close` waits for its thread.
        began = time.monotonic()
        stopper = threading.Thread(target=session.stop)
        stopper.start()
        stopper.join(timeout=10)
        stream.close()
    assert not stopper.is_alive() and time.monotonic() - began < 5


def test_the_page_follows_a_change_without_waiting_for_its_poll(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_study_running_until_it_knows_is_drawn import _study as replicas

    class Patch:
        def setattr(self, name, value):
            import importlib
            module, attr = name.rsplit(".", 1)
            setattr(importlib.import_module(module), attr, value)

    import fastmdxplora.simulation.resume as resume

    kept = resume.production_done_ns
    try:
        mid = replicas(tmp_path / "study", Patch())["record"]
    finally:
        resume.production_done_ns = kept
    root = tmp_path / "study"
    finished = (root / "stopping.json").read_text(encoding="utf-8")
    (root / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1300, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => document.getElementById('stopping-outcome')"
                                   ".textContent === 'Still running'")
            page.wait_for_function("() => window.FastMDXDashboard.state.streaming === true")
            (root / "stopping.json").write_text(finished, encoding="utf-8")
            began = time.monotonic()
            # The poll is thirty seconds while the stream is open; well
            # inside that, only the stream can have said so.
            page.wait_for_function("() => document.getElementById('stopping-outcome')"
                                   ".textContent === 'Known as asked'", timeout=10000)
            took = time.monotonic() - began
            browser.close()
    finally:
        session.stop()
    assert took < 10 and errors == []
    shutil.rmtree(root, ignore_errors=True)
