"""`fastmdx resume` does not take a new process for the run it carries on.

A study stopped in one container is carried on in another, and a container
numbers its processes from one again. The old run's record names a process
number that, in the new container, can be the resume's own or its shell's,
and the resume refused the study as still running. The record now says
which machine and boot wrote it, and the resume never counts itself or
what started it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fastmdxplora.orchestrator import (
    RUN_PROCESS_FILE,
    _record_run_process,
    record_is_from_elsewhere,
    this_machine,
)
from fastmdxplora.simulation.resume import _still_running

pytestmark = pytest.mark.skipif(not Path("/proc/self/cmdline").exists(),
                                reason="reads other processes' command lines as Linux gives them")


def record(root: Path, pid: int, **extra) -> None:
    (root / RUN_PROCESS_FILE).write_text(json.dumps({
        "pid": pid, "argv": ["fastmdx", "explore", "--output", str(root)],
        "started_at": "2026-09-28T00:00:00+00:00", **extra}), encoding="utf-8")


@pytest.fixture()
def root(tmp_path):
    study = tmp_path / "run"
    study.mkdir()
    return study


def a_live_run(root: Path) -> subprocess.Popen:
    """A process whose command line is this study's run, still going."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             "fastmdx", "explore", "--output", str(root)])


def test_a_record_says_where_it_was_written(root) -> None:
    _record_run_process(root)
    written = json.loads((root / RUN_PROCESS_FILE).read_text())
    assert written["host"] == this_machine()["host"] and written["host"]
    assert written["pid"] == os.getpid()
    if Path("/proc/sys/kernel/random/boot_id").exists():
        assert written["boot"] and written["boot"] == this_machine()["boot"]


def test_a_run_still_going_here_is_still_seen(root) -> None:
    run = a_live_run(root)
    try:
        record(root, run.pid, **this_machine())
        assert _still_running(root)
    finally:
        run.kill()
        run.wait()


@pytest.mark.parametrize("key", ["host", "boot"])
def test_a_record_from_another_machine_or_boot_names_nothing_here(root, key) -> None:
    run = a_live_run(root)
    try:
        record(root, run.pid, **{**this_machine(), key: "another"})
        assert record_is_from_elsewhere(json.loads((root / RUN_PROCESS_FILE).read_text()))
        assert not _still_running(root), "its number belongs to another process here"
    finally:
        run.kill()
        run.wait()


def test_an_older_record_without_them_is_judged_as_before(root) -> None:
    run = a_live_run(root)
    try:
        record(root, run.pid)
        assert _still_running(root)
    finally:
        run.kill()
        run.wait()


CHECK = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path
    from fastmdxplora.simulation.resume import _still_running
    root, whose = Path(sys.argv[1]), sys.argv[2]
    pid = os.getpid() if whose == "own" else os.getppid()
    (root / ".fastmdxplora_run.json").write_text(json.dumps({
        "pid": pid, "argv": ["fastmdx", "resume", str(root)]}))
    print("running" if _still_running(root) else "stopped")
''')


@pytest.mark.parametrize("whose", ["own", "parent"])
def test_the_resume_and_its_shell_are_never_the_old_run(root, whose) -> None:
    # As in a new container: the old record's number is now the resume's
    # own, or the shell's that started it, and both command lines name the
    # study and the program.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    # Both command lines carry `fastmdx resume <study>`, as a real one's do.
    done = subprocess.run(
        ["sh", "-c", f'"{sys.executable}" -c "$0" "$1" {whose} fastmdx resume "$1"; true',
         CHECK, str(root), "fastmdx", "resume", str(root)],
        capture_output=True, text=True, env=env, timeout=60)
    assert done.stdout.strip() == "stopped", done.stderr


def test_without_a_boot_record_only_the_host_is_said(monkeypatch) -> None:
    import pathlib

    real = pathlib.Path.read_text

    def no_boot(self, *args, **kwargs):
        if str(self).endswith("boot_id"):
            raise OSError("not Linux")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", no_boot)
    assert this_machine()["boot"] == "" and this_machine()["host"]


def test_the_parents_are_read_up_the_chain(monkeypatch) -> None:
    """As /proc gives them: this process's parent 1000, whose parent 2000,
    whose parent is process 1, which is not counted."""
    import pathlib

    from fastmdxplora.simulation.resume import _this_process_and_its_parents

    stats = {"/proc/1000/stat": "1000 (sh) S 2000 1 1", "/proc/2000/stat": "2000 (a b) S 1 1 1"}
    real = pathlib.Path.read_text

    def proc(self, *args, **kwargs):
        if str(self) in stats:
            return stats[str(self)]
        if str(self).startswith("/proc/"):
            raise OSError("gone")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", proc)
    monkeypatch.setattr(os, "getppid", lambda: 1000)
    assert _this_process_and_its_parents() == {os.getpid(), 1000, 2000}
    monkeypatch.setattr(os, "getppid", lambda: 3000)  # no record of it: stops there
    assert _this_process_and_its_parents() == {os.getpid(), 3000}


def test_the_gui_does_not_adopt_a_run_from_another_machine(root, tmp_path) -> None:
    from fastmdxplora.gui.exploration import DashboardRuntime

    run = a_live_run(root)
    try:
        record(root, run.pid, **{**this_machine(), "host": "another"})
        runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path)
        assert runtime._adopt_if_running(root) is False
        assert runtime.process is None
    finally:
        run.kill()
        run.wait()
