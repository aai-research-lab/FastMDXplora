"""A run asked to stop does stop, whether or not what asked is still there.

An assistant's `stop_study` asks a run to stop, gives it time to reach its
next frame and write a checkpoint, and ends it if it has not. That last part
ran in a thread of the assistant's server, so an assistant closed in the
meantime took it along and a run that ignored the request went on. It runs
in a process of its own now (`fastmdxplora.stop_after`). The runs here are
sleepers that ignore the request, carrying a run's command on their command
line, as a run does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fastmdxplora.gui.exploration import _process_alive
from fastmdxplora.mcp import App, Workspace
from fastmdxplora.orchestrator import RUN_PROCESS_FILE
from fastmdxplora.stop_after import main, see_it_stops, watch
from tests._mcp_wire import Wire
from tests.test_an_assistant_reads_and_checks_studies import _study

#: Asked to stop, it goes on; its child, in its group, too.
STUBBORN = ("import signal, subprocess, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "if sys.argv[1] == 'parent':\n"
            "    child = subprocess.Popen([sys.executable, '-c', sys.argv[2], 'child', sys.argv[2]])\n"
            "    print(child.pid, flush=True)\n"
            "time.sleep(120)\n")

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX signals and groups")


@pytest.fixture
def study(tmp_path):
    return _study(tmp_path / "work" / "busy", duration=10, means={},
                  started="2026-09-02T10:00:00+00:00")


def _stubborn(study: Path) -> tuple[subprocess.Popen, int]:
    """A run that ignores a stop, with a worker in its own group."""
    process = subprocess.Popen([sys.executable, "-c", STUBBORN, "parent", STUBBORN,
                                str(study)], stdout=subprocess.PIPE, text=True,
                               start_new_session=True)
    worker = int(process.stdout.readline())
    (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": process.pid}))
    return process, worker


def _gone_within(pid: int, seconds: float, process=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return True
        if process is None and not _process_alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_a_run_that_ignores_a_stop_is_ended_with_its_group_and_says_so(study):
    process, worker = _stubborn(study)
    try:
        assert watch(process.pid, study, None, 0.5) is True
        assert _gone_within(process.pid, 5, process)
        assert _gone_within(worker, 5)
        assert "Ended: it had not stopped 0.5 s after it was asked to." in \
            (study / "exploration.log").read_text()
    finally:
        process.kill()
        process.wait()


def test_a_run_that_stops_in_time_is_left_alone(study):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)",
                                str(study)])
    try:
        assert watch(process.pid, study, None, 30) is False
        assert not (study / "exploration.log").exists()
    finally:
        process.wait()


def test_a_number_given_to_something_else_is_never_signalled(study):
    # Alive, but not this study's run: its command names something else.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert watch(other.pid, study, None, 0.2) is False
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_what_is_not_a_request_does_nothing():
    assert main([]) == 2
    assert main(["x", "f", "1", "null"]) == 2
    assert main(["0", "f", "1", "null"]) == 2


def test_the_watching_outlives_the_process_that_asked(study, monkeypatch):
    process, worker = _stubborn(study)
    try:
        # Started from a process that exits at once: the watching goes on.
        code = (f"from fastmdxplora.stop_after import see_it_stops; "
                f"see_it_stops({process.pid}, {str(study)!r}, None, 0.5)")
        subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
        assert _gone_within(process.pid, 30, process)
        assert _gone_within(worker, 10)
    finally:
        process.kill()
        process.wait()


def test_stop_study_leaves_the_watching_to_a_process_of_its_own(study, monkeypatch):
    monkeypatch.setenv("FASTMDX_STOP_GRACE_SECONDS", "0")
    started: list = []
    monkeypatch.setattr("fastmdxplora.stop_after.see_it_stops",
                        lambda *a: (started.append(a), see_it_stops(*a)))
    process, worker = _stubborn(study)
    wire = Wire(App(Workspace.at(study.parent)).server())
    try:
        said = wire.request("tools/call", {"name": "stop_study",
                                           "arguments": {"study": "busy"}})["result"]
        assert not said["isError"]
        assert said["content"][0]["text"].endswith(
            "One that has not stopped 10 s from now is ended, whether or not this "
            "assistant is still open.")
        assert started == [(process.pid, study.resolve(), None, 10.0)]
        # Nothing in this process waits on it.
        assert not [t for t in threading.enumerate() if t.name == "fastmdx-mcp-stop"]
        wire.close()
        assert _gone_within(process.pid, 40, process)
        assert _gone_within(worker, 10)
    finally:
        process.kill()
        process.wait()


def test_a_run_started_in_a_terminal_is_ended_without_its_group(study):
    """A run started by hand leads, at most, a group in the terminal's
    session (`fastmdx explore ... | tee log` does), and the rest of that
    group is the person's own: only the run is ended."""
    terminal = subprocess.Popen(
        [sys.executable, "-c",
         "import os, subprocess, sys, time\n"
         "run = subprocess.Popen([sys.executable, '-c', sys.argv[1], 'parent', sys.argv[1],"
         " sys.argv[2]], stdout=subprocess.PIPE, text=True,"
         " preexec_fn=lambda: os.setpgid(0, 0))\n"
         "print(run.pid, run.stdout.readline().strip(), flush=True)\n"
         "time.sleep(120)\n", STUBBORN, str(study)],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    run, piped_to = (int(n) for n in terminal.stdout.readline().split())
    try:
        assert os.getpgid(run) == run and os.getsid(run) == terminal.pid
        assert watch(run, study, None, 0.3) is True
        assert _gone_within(run, 5)
        assert _process_alive(piped_to) and terminal.poll() is None
    finally:
        os.kill(piped_to, 9)
        terminal.kill()
        terminal.wait()
