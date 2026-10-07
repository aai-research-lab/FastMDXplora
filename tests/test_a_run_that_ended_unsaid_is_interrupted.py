"""A run that ended without saying so is interrupted, not running.

A run keeps a live record while it goes and writes its end into it. One
that ended without writing it (the machine restarted, or its job was ended
by a scheduler or by hand) left the record saying it was running, and the
GUI took it at its word: weeks later the active study read "Running", the
progress card gave it time left, and All studies and Recent said running
or, with no manifest written yet, not started.

Where the run's process record was written in this machine's boot and
process namespace, its process is asked: gone, or its number given to a
process begun since, the run has ended. A live process is never taken for
gone because its command line is not a run's, since a run started from a
script, a notebook or a pool's worker has such a command line. Where there
is no process here to ask, a day of silence decides, and the page says so
rather than that the run is certainly gone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fastmdxplora.gui import telemetry
from fastmdxplora.gui.telemetry import (
    SILENT_RUN_ENDED_AFTER_SECONDS, analyze_health, how_the_run_ended, run_ended_unsaid,
    status_as_it_stands)
from fastmdxplora.orchestrator import RUN_PROCESS_FILE, this_machine


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _a_run_said_it_was_going(root: Path, *, said="running", last: str | None = None,
                             step: int = 16000) -> Path:
    last = _ago(30) if last is None else last
    (root / "simulation").mkdir(parents=True, exist_ok=True)
    (root / "simulation" / telemetry.STATUS_FILE).write_text(json.dumps({
        "status": said, "stage": "production", "current_step": step,
        "total_planned_steps": 50000, "elapsed_wall_time_s": 3600.0,
        "last_update_timestamp": last}), encoding="utf-8")
    return root


def _a_gone_process() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(done.stdout)


@pytest.fixture
def a_script():
    """A live process whose command line is a script's, not `fastmdx`'s:
    a run started from Python, a notebook or a pool's worker."""
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield running.pid
    running.kill()
    running.wait()


def _its_process(root: Path, pid: int, *, started: str | None = None, **where) -> None:
    (root / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": pid, "argv": ["my_study.py"],
        "started_at": started or datetime.now(timezone.utc).isoformat(),
        **this_machine(), **where}), encoding="utf-8")


linux = pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")


class TestWhetherItEnded:

    def test_its_process_gone_here_it_ended_however_recently_it_wrote(self, tmp_path):
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, _a_gone_process())
        assert how_the_run_ended(root) == "process"

    def test_a_script_s_process_alive_is_not_taken_for_gone(self, tmp_path, a_script):
        recent = _a_run_said_it_was_going(tmp_path / "recent", last=_ago(5))
        _its_process(recent, a_script)
        assert not run_ended_unsaid(recent)
        # Alive but not recognisably a run: only a day's silence decides.
        quiet = _a_run_said_it_was_going(tmp_path / "quiet", last=_ago(30 * 86400))
        _its_process(quiet, a_script)
        assert how_the_run_ended(quiet) == "silence"

    def test_a_run_s_process_alive_goes_on_however_long_it_is_silent(self, tmp_path, a_script,
                                                                     monkeypatch):
        from fastmdxplora.gui import exploration

        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(30 * 86400))
        _its_process(root, a_script)
        monkeypatch.setattr(exploration, "_identify_run", lambda pid, where, argv: True)
        assert not run_ended_unsaid(root)

    @linux
    def test_its_number_given_to_a_process_begun_since_it_ended(self, tmp_path, a_script):
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, a_script, started=_ago(3600))
        assert how_the_run_ended(root) == "process"

    def test_this_host_restarted_since_it_ended(self, tmp_path, a_script):
        if not this_machine()["boot"]:
            pytest.skip("this system does not say which boot")
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, a_script, boot="an-earlier-boot")
        assert how_the_run_ended(root) == "process"

    @pytest.mark.parametrize("where", [{"host": "a-cluster-node"}, {"pidns": "pid:[1]"},
                                       {"none": True}])
    def test_with_no_process_here_to_ask_its_silence_decides(self, tmp_path, where):
        def study(name, last):
            root = _a_run_said_it_was_going(tmp_path / name, last=last)
            if "none" not in where:
                # A number that is no process here: in another host's or
                # another container's numbering it says nothing.
                _its_process(root, _a_gone_process(), **where)
            return root

        if "pidns" in where and not this_machine()["pidns"]:
            pytest.skip("this system does not say which process namespace")
        assert not run_ended_unsaid(study("recent", _ago(60)))
        assert not run_ended_unsaid(study("slow", _ago(SILENT_RUN_ENDED_AFTER_SECONDS - 60)))
        assert how_the_run_ended(study("weeks", _ago(21 * 86400))) == "silence"

    def test_never_for_the_run_reading_its_own_record(self, tmp_path):
        import os

        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(30 * 86400))
        _its_process(root, os.getpid())
        assert not run_ended_unsaid(root)

    @pytest.mark.parametrize("said", ["completed", "failed", "stopped", ""])
    def test_a_run_that_said_its_end_is_taken_at_its_word(self, tmp_path, said):
        root = _a_run_said_it_was_going(tmp_path / "s", said=said, last=_ago(30 * 86400))
        assert not run_ended_unsaid(root)
        assert status_as_it_stands(root)["status"] == said

    def test_every_planned_step_taken_it_finished(self, tmp_path):
        # The Python API's simulate() writes no end of its own.
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(30 * 86400), step=50000)
        shown = status_as_it_stands(root)
        assert (shown["status"], shown["recorded_status"]) == ("completed", "running")
        assert analyze_health(shown, [])["state"] == "ok"


def test_as_the_gui_shows_it_and_its_health(tmp_path):
    root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
    _its_process(root, _a_gone_process())
    shown = status_as_it_stands(root)
    assert (shown["status"], shown["recorded_status"], shown["stage"], shown["ended_by"]) == \
        ("interrupted", "running", "production", "process")
    health = analyze_health(shown, [])
    assert (health["state"], health["headline"]) == ("interrupted", "Interrupted")
    assert "the process that ran it is gone" in health["explanation"]
    # Known only by its silence, it is said so, and resuming is not urged.
    quiet = _a_run_said_it_was_going(tmp_path / "quiet", last=_ago(21 * 86400))
    health = analyze_health(status_as_it_stands(quiet), [])
    assert health["message"] == "The run has not written a word for over a day."
    assert "only once it is certainly not running" in health["explanation"]
    # What the run reads of its own record is as it wrote it.
    assert telemetry.read_status(root)["status"] == "running"


def test_the_studies_say_it(tmp_path):
    from fastmdxplora.gui.workspace import studies_in

    ended = _a_run_said_it_was_going(tmp_path / "ended", last=_ago(21 * 86400))
    going = _a_run_said_it_was_going(tmp_path / "going", last=_ago(30))
    for root in (ended, going):
        (root / "resolved_config.yml").write_text("systems: [{system: 1UBQ}]\n",
                                                  encoding="utf-8")
    found = {Path(s["path"]).name: s["state"] for s in studies_in(tmp_path)["studies"]}
    # With no manifest yet, these were "not started".
    assert found == {"ended": "interrupted", "going": "running"}


def test_the_page_is_told(tmp_path):
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _a_run_said_it_was_going(_write_study(tmp_path / "s"), last=_ago(21 * 86400))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(session.url.rstrip("/") + "/api/status", timeout=30) as r:
            payload = json.loads(r.read())
    finally:
        session.server.shutdown()
    assert payload["status"]["status"] == "interrupted"
    assert payload["health"]["state"] == "interrupted"


def test_the_page_says_it(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _a_run_said_it_was_going(_write_study(tmp_path / "s"), last=_ago(21 * 86400))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => document.body.classList.contains('state-ready') && "
                                   "document.getElementById('topbar-status-text')"
                                   ".textContent === 'Interrupted'")
            said = page.evaluate("""() => ({
                stage: document.getElementById('topbar-stage').textContent,
                dot: document.getElementById('topbar-status-dot').className,
                card: document.getElementById('sidebar-progress').getAttribute('data-run'),
                eta: document.getElementById('topbar-eta').textContent,
                pause: document.getElementById('pause-toggle').hidden,
                fix: getComputedStyle(document.getElementById('sidebar-fix')).display,
            })""")
            browser.close()
    finally:
        session.server.shutdown()
    assert said["stage"].startswith("Production, last update ")
    assert said["dot"] == "status-dot status-dot-waiting"
    # What would fix it is offered: `fastmdx resume` carries it on.
    assert (said["card"], said["eta"], said["pause"], said["fix"]) == \
        ("interrupted", "—", True, "block")
