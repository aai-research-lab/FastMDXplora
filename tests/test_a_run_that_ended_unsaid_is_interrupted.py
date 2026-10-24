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
def a_stand_in():
    """A live process whose command line names the program, as a run's
    does: any process given a run's number since."""
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                "fastmdx"])
    yield running.pid
    running.kill()
    running.wait()


@pytest.fixture
def a_script():
    """A live process whose command line is a script's, not `fastmdx`'s:
    a run started from Python, a notebook or a pool's worker."""
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield running.pid
    running.kill()
    running.wait()


def _its_process(root: Path, pid: int, *, started: str | None = None, argv=None,
                 **where) -> None:
    (root / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": pid, "argv": argv or ["my_study.py"],
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
        monkeypatch.setattr(exploration, "_what_the_process_is",
                            lambda pid, where, record, asking=False: "the run")
        assert not run_ended_unsaid(root)

    @linux
    def test_its_number_given_to_a_process_begun_since_it_ended(self, tmp_path, a_script):
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, a_script, started=_ago(3600))
        assert how_the_run_ended(root) == "process"

    @linux
    def test_its_number_given_to_a_run_like_process_begun_since_it_ended(self, tmp_path,
                                                                          a_stand_in):
        """A record with no arguments (the Python API's) took any live
        process with a `fastmdx` token for its run, before asking when that
        process began: a killed study read "Running"."""
        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, a_stand_in, started=_ago(3600))
        assert how_the_run_ended(root) == "process"
        # Begun when the record was written, it is the run.
        going = _a_run_said_it_was_going(tmp_path / "going", last=_ago(5))
        _its_process(going, a_stand_in)
        assert how_the_run_ended(going) is None

    @linux
    def test_its_number_given_to_the_process_asking_since_it_ended(self, tmp_path):
        """The GUI's own number in a record written weeks before it began (a
        container restarted, its record with no process namespace) read
        running."""
        import os

        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, os.getpid(), started=_ago(30 * 86400))
        assert how_the_run_ended(root) == "process"

    @linux
    def test_its_number_given_to_the_process_asking_under_a_record_with_arguments(self, tmp_path):
        """The same with the run's arguments recorded (a record written
        before its start ticks): the asking process's command line is not
        read, so its arguments cannot make it the run."""
        import os

        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(5))
        _its_process(root, os.getpid(), started=_ago(30 * 86400),
                     argv=[sys.argv[0], "run", str(root)])
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

    @pytest.mark.parametrize("arguments", [False, True])
    def test_never_for_the_run_reading_its_own_record(self, tmp_path, arguments):
        import os

        root = _a_run_said_it_was_going(tmp_path / "s", last=_ago(30 * 86400))
        if arguments:
            _its_process(root, os.getpid(), argv=[sys.argv[0], "run", str(root)])
        else:
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


#: Where the package is, for a process of its own to import it.
SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def a_live_run(tmp_path):
    """A live process that wrote its study's process record itself, as
    `fastmdx run <study>` does: its command line carries the arguments it
    recorded, and the record has its start."""
    root = _a_run_said_it_was_going(tmp_path / "w" / "live", last=_ago(5))
    code = (f"import sys, time; sys.path.insert(0, {SRC!r}); from pathlib import Path; "
            "from fastmdxplora.orchestrator import _record_run_process; "
            "_record_run_process(Path(sys.argv[3])); print('written', flush=True); "
            "time.sleep(120)")
    run = subprocess.Popen([sys.executable, "-c", code, "fastmdx", "run", str(root)],
                           stdout=subprocess.PIPE, text=True)
    try:
        assert run.stdout.readline().strip() == "written"
        yield root, run
    finally:
        run.kill()
        run.wait()


def _ticks(pid: int) -> int:
    """When the process began, in clock ticks since boot (`/proc`)."""
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def _its_record(root: Path) -> dict:
    return json.loads((root / RUN_PROCESS_FILE).read_text(encoding="utf-8"))


def _rewritten(root: Path, change) -> dict:
    record = _its_record(root)
    change(record)
    (root / RUN_PROCESS_FILE).write_text(json.dumps(record), encoding="utf-8")
    return record


@linux
class TestWhichProcessIsTheRun:
    """A process is the run where its number, boot and start in ticks since
    boot match its record; a record written before starts were is judged
    by its arguments first, and by when the process began only where it
    has none. Every reader asks the same test."""

    @pytest.mark.parametrize("written", ["with its start", "before starts were written"])
    def test_a_live_run_is_running_when_the_clock_steps(self, a_live_run, monkeypatch,
                                                        written):
        """The wall clock stepped 60 s forward (a laptop's VM resumed, NTP)
        moves every process's start read from `btime`: a live run that
        named its own study read "its process is gone"."""
        root, run = a_live_run
        if written == "with its start":
            assert _its_record(root).get("start_ticks") == _ticks(run.pid)
        else:
            _rewritten(root, lambda record: record.pop("start_ticks", None))
        assert how_the_run_ended(root) is None
        real = telemetry._process_started_at
        monkeypatch.setattr(telemetry, "_process_started_at",
                            lambda pid: None if real(pid) is None else real(pid) + 60)
        telemetry._ASKED.clear()
        assert how_the_run_ended(root) is None
        assert status_as_it_stands(root)["status"] == "running"

    def test_a_number_with_another_start_is_another_process(self, a_live_run, tmp_path):
        """Alive, carrying the recorded arguments, begun when the record was
        written: still not the run where its start is not the record's."""
        import os

        from fastmdxplora.gui.exploration import _identify_run

        root, run = a_live_run
        record = _rewritten(root, lambda record: record.update(start_ticks=_ticks(run.pid) - 1))
        assert _identify_run(run.pid, root, record) is False
        assert how_the_run_ended(root) == "process"
        # The process asking too.
        own = _a_run_said_it_was_going(tmp_path / "own", last=_ago(5))
        _its_process(own, os.getpid(), argv=[sys.argv[0], "run", str(own)],
                     start_ticks=_ticks(os.getpid()) - 1)
        assert how_the_run_ended(own) == "process"

    @pytest.mark.parametrize("record", ["no arguments, begun since", "another start"])
    def test_a_stand_in_is_neither_adopted_nor_going_nor_stopped(self, tmp_path, a_stand_in,
                                                                record):
        """A killed study's number given to a `fastmdx` process since was
        adopted as its run: All studies said running, and Stop killed the
        stand-in and wrote "stopped as asked" into the study's record."""
        from fastmdxplora.gui.exploration import DashboardRuntime, _process_alive
        from fastmdxplora.runs_here import going_in

        workspace = tmp_path / "w"
        root = _a_run_said_it_was_going(workspace / "s", last=_ago(5))
        if record == "another start":
            _its_process(root, a_stand_in, argv=["-c", "fastmdx"],
                         start_ticks=_ticks(a_stand_in) + 1)
        else:
            _its_process(root, a_stand_in, started=_ago(3600))
        runtime = DashboardRuntime(workspace_root=root, exploration_root=workspace,
                                   active_root=root)
        assert runtime.process is None
        assert going_in(workspace) == []
        assert runtime.stop()["stopped"] is False
        assert _process_alive(a_stand_in)
        assert telemetry.read_status(root)["status"] == "running"

    def test_stop_signals_nothing_once_the_number_is_another_s(self, a_live_run, monkeypatch):
        """Adopted while it was the run, its number given to another
        process before Stop was pressed: nothing is signalled, and why is
        said."""
        from fastmdxplora import orchestrator
        from fastmdxplora.gui.exploration import DashboardRuntime

        root, run = a_live_run
        runtime = DashboardRuntime(workspace_root=root, exploration_root=root.parent,
                                   active_root=root)
        assert runtime.process is not None and runtime.process.pid == run.pid
        monkeypatch.setattr(orchestrator, "process_start_ticks",
                            lambda pid: _ticks(pid) + (1 if pid == run.pid else 0),
                            raising=False)
        answer = runtime.stop()
        assert answer["stopped"] is False
        assert f"process {run.pid}, which the study's record names, is no longer its run" \
            in answer["detail"]
        assert run.poll() is None
        assert telemetry.read_status(root)["status"] == "running"
        assert answer["state"]["process_running"] is False


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
                card: document.getElementById('status-run').getAttribute('data-run'),
                eta: document.getElementById('topbar-eta').textContent,
                pause: document.getElementById('pause-toggle').hidden,
                fix: getComputedStyle(document.getElementById('status-fix')).display,
            })""")
            browser.close()
    finally:
        session.server.shutdown()
    assert said["stage"].startswith("Production, last update ")
    assert said["dot"] == "status-dot status-dot-waiting"
    # What would fix it is offered: `fastmdx resume` carries it on.
    assert (said["card"], said["eta"], said["pause"], said["fix"]) == \
        ("interrupted", "\u2014", True, "flex")
