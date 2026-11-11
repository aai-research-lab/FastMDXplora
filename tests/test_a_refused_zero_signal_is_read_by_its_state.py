"""EPERM to the zero signal is read by what the process is, not taken for
alive.

macOS leaves zombies out of the group it signals and says EPERM to
``killpg(group, 0)`` where no member is left that may be signalled: a group
whose members are all zombies (CI run 42759782805, where the GPU sampler's
test failed), or one of another user's processes. Linux answers success for
a zombie and for a group of them, and says EPERM for another user's
processes only. Both answer success for a single zombie. Read as going, the
sampler of a run that ended read its GPU for ever.

The tests that simulate EPERM need no simulation backend, so they run on
every leg, the floor and macOS among them; the real ones make EPERM on
Linux by reading as another user (``setpriv``), where that can be done.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _group_gone_within(group: int, seconds: float) -> bool:
    """Whether the process group is gone within ``seconds``, read as the GPU
    sampler reads a run's group (:func:`fastmdxplora.gpu_here._going`)."""
    from fastmdxplora.gpu_here import _going

    until = time.monotonic() + seconds
    while _going(group):
        if time.monotonic() > until:
            return False
        time.sleep(0.1)
    return True


# ---------------------------------------------------------------------------
# Simulated
# ---------------------------------------------------------------------------
def _signal_refused(monkeypatch, listed: str) -> None:
    """Every signal to a group says EPERM, and ``ps -A`` lists ``listed``."""
    from fastmdxplora import gpu_here

    def refused(*_):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refused)
    monkeypatch.setattr(gpu_here, "_answer", lambda script: listed)


class _Ended:
    pid = 4242

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return 0


def test_a_run_whose_group_is_zombies_refusing_the_signal_left_nothing(monkeypatch):
    """A failed round's cleanup read a group of zombies under EPERM as a
    worker left behind."""
    from tests.test_a_failed_round_is_said_failed import _ended

    _signal_refused(monkeypatch, " 4242 Z\n 4300 S\n")
    ran = _Ended()
    assert _ended(ran, 1, "the run") == ("", "")
    assert ran.left_behind is False


def test_a_run_whose_group_has_a_live_member_refusing_the_signal_left_it(monkeypatch):
    _signal_refused(monkeypatch, " 4242 Z\n 4242 S\n")
    assert _group_gone_within(4242, 0.3) is False


def _pid_refused(monkeypatch, state: str | None, *, refused: bool = True,
                 proc: Path | None = None) -> None:
    """The zero signal to a pid says EPERM (success where not ``refused``),
    and ``ps -p`` says ``state`` (``None``: ``ps`` cannot be run, and
    ``/proc`` is ``proc``, none where not given)."""
    from fastmdxplora import gpu_here
    from fastmdxplora.gui import exploration

    def signal(pid, number):
        assert number == 0
        if refused:
            raise PermissionError(1, "Operation not permitted")

    def ps(command, **_):
        assert command[:3] == ["ps", "-o", "stat="], command
        if state is None:
            raise FileNotFoundError("ps")
        return subprocess.CompletedProcess(command, 0, stdout=state, stderr="")

    monkeypatch.setattr(exploration, "_WINDOWS", False)
    monkeypatch.setattr(os, "kill", signal)
    monkeypatch.setattr(exploration.subprocess, "run", ps)
    monkeypatch.setattr(gpu_here, "_PROC", proc or Path("/nonexistent/proc"))


@pytest.mark.parametrize("state", ["Z\n", "Z+\n"])
def test_another_user_s_process_that_ps_says_ended_is_gone(monkeypatch, state):
    """Another user's zombie (Linux says EPERM for it) was read as alive: a
    colleague's run, or a pid reused."""
    from fastmdxplora.gui.exploration import _process_alive

    _pid_refused(monkeypatch, state)
    assert _process_alive(4242) is False


@pytest.mark.parametrize("state", ["S\n", "", None], ids=["S", "unlisted", "no ps"])
def test_another_user_s_process_alive_or_unread_is_alive(monkeypatch, state):
    """A live process of another user's, one ``ps`` does not list (``/proc``
    mounted ``hidepid=2``, where ``ps`` lists only this user's), and one
    neither ``ps`` nor ``/proc`` can read: EPERM proves a process there."""
    from fastmdxplora.gui.exploration import _process_alive

    _pid_refused(monkeypatch, state)
    assert _process_alive(4242) is True


# ---------------------------------------------------------------------------
# Real: read as another user, so the zero signal says EPERM on Linux too
# ---------------------------------------------------------------------------
_SETPRIV = shutil.which("setpriv")
_SRC = str(Path(__import__("fastmdxplora").__file__).resolve().parents[1])

_READ = """
import json, sys
from fastmdxplora.gpu_here import _going
from fastmdxplora.gui.exploration import _process_alive
pid = int(sys.argv[1])
try:
    import os
    os.kill(pid, 0)
    said = "ok"
except PermissionError:
    said = "EPERM"
except ProcessLookupError:
    said = "ESRCH"
print(json.dumps([said, _going(pid), _process_alive(pid)]))
"""


def _read_as_another_user(pid: int, *, path: str | None = None) -> tuple[str, bool, bool]:
    """The zero signal's answer, then ``_going`` and ``_process_alive`` of
    ``pid``, as user ``nobody`` reads them; ``path`` is that reader's PATH
    (one without ``ps`` or ``sh``, so ``/proc`` is read)."""
    import json

    env = {"PATH": path or os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": _SRC,
           "HOME": "/nonexistent"}
    said = subprocess.run([_SETPRIV, "--reuid=65534", "--regid=65534", "--clear-groups",
                           sys.executable, "-c", _READ, str(pid)],
                          capture_output=True, text=True, timeout=60, env=env)
    if said.returncode != 0:
        pytest.skip(f"cannot read as another user here: {said.stderr[-300:]}")
    answer, going, alive = json.loads(said.stdout.strip().splitlines()[-1])
    return answer, going, alive


def _a_zombie(started: subprocess.Popen) -> None:
    """Waits until ``started`` has ended and is not reaped."""
    deadline = time.monotonic() + 10
    while True:
        stat = Path(f"/proc/{started.pid}/stat").read_text()
        if stat.rpartition(")")[2].split()[0] == "Z":
            return
        assert time.monotonic() < deadline, stat
        time.sleep(0.05)


_real = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and _SETPRIV and hasattr(os, "geteuid")
         and os.geteuid() == 0 and Path("/proc/self/stat").exists()),
    reason="needs Linux, setpriv and root to read as another user")


@_real
@pytest.mark.parametrize("path", [None, "/nonexistent"], ids=["ps", "proc"])
def test_another_user_s_live_group_and_process_read_as_going(path):
    started = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert _read_as_another_user(started.pid, path=path) == ("EPERM", True, True)
    finally:
        started.kill()
        started.wait(timeout=10)


@_real
@pytest.mark.parametrize("path, alive", [(None, False), ("/nonexistent", True)],
                         ids=["ps", "proc"])
def test_another_user_s_group_of_a_zombie_reads_gone(path, alive):
    """A group whose one member has ended and is not reaped: EPERM, as
    macOS says for a group of this user's zombies. With no ``ps`` the group
    is read from ``/proc``; the one pid is taken for alive, EPERM being all
    there is to read of it."""
    started = subprocess.Popen(["sh", "-c", "exit 0"], start_new_session=True)
    try:
        _a_zombie(started)
        assert _read_as_another_user(started.pid, path=path) == ("EPERM", False, alive)
    finally:
        started.wait(timeout=10)
