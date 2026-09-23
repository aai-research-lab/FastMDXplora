"""Reaching a machine: the user's own ``ssh``, and nothing in its place.

FastMDXplora does not carry an SSH implementation. It runs the ``ssh`` the
user already has, so whatever ``~/.ssh/config`` says -- a ``ProxyJump``
through a login node, a certificate, an agent, a ``Match`` block for one
site -- applies here exactly as it does in their terminal. A machine that
``ssh gpu-box`` reaches is a machine this reaches, and one it cannot reach
fails with the message ``ssh`` itself gives.

**One login per session.** Clusters that ask for a second factor ask at
every connection. A control master keeps the first connection open for a
few minutes and every later command rides on it, so one inspection is one
prompt rather than several. Windows' OpenSSH has no control master, so
there each command connects afresh.

**Never waiting on a prompt nobody can answer.** Where there is no terminal
-- a script, a test, a pipe -- ``BatchMode`` makes a connection that needs
a password fail at once instead of hanging.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = ["Answer", "Transport", "check_machine_name", "run_here"]

#: How a machine is named: an SSH alias or ``user@host``. Letters, digits and
#: ``_ . @ -``, and never a leading ``-``, which ``ssh`` would take as an
#: option. ``-oProxyCommand=...`` is a machine name only to someone who wants
#: a command run on this computer.
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$")

#: Seconds allowed to open a connection. Generous, because a second factor
#: is typed within it.
CONNECT_TIMEOUT_S = 30

#: How long a control master stays open after its last command.
CONTROL_PERSIST_S = 600

#: A Unix socket path is limited to about 104 bytes on macOS and 108 on
#: Linux, and ssh appends to the one it is given.
_SOCKET_PATH_LIMIT = 90


def check_machine_name(name: str) -> str:
    """The name, if it can be passed to ``ssh`` as a destination safely."""
    if not _NAME.match(name or ""):
        raise StudyError(
            f"{name!r} is not a machine name FastMDXplora will pass to ssh. "
            "Use the alias from your ~/.ssh/config, or user@host: letters, "
            "digits and . _ @ -, not starting with a dash.",
            code="remote.machine.unusable_name",
            given=name,
        )
    return name


@dataclass(frozen=True)
class Answer:
    """What a command on the machine printed, and how it ended."""

    stdout: str
    stderr: str
    returncode: int


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _socket_dir() -> Path | None:
    """Where control-master sockets go, made and checked, or ``None``.

    ``None`` means each command connects afresh: on Windows, where there is
    no control master; where no short enough path exists; and where the
    directory is not this user's alone, since a socket there could be
    replaced by someone else's.
    """
    if os.name == "nt":
        return None
    for base in (user_config_dir() / "sockets",
                 Path("/tmp") / f"fastmdx-{os.getuid()}"):
        # ssh expands %C to a 40-character hash.
        if len(str(base / ("x" * 40))) > _SOCKET_PATH_LIMIT:
            continue
        try:
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            status = base.lstat()
        except OSError:
            continue
        if (base.is_dir() and not base.is_symlink()
                and status.st_uid == os.getuid()
                and status.st_mode & 0o077 == 0):
            return base
    return None


class Transport:
    """Commands run on one machine through the user's ``ssh``.

    Parameters
    ----------
    name : str
        The machine: an alias from ``~/.ssh/config``, or ``user@host``.
    runner : callable, optional
        Stands in for :func:`subprocess.run`. Tests pass one; nothing else
        should.
    interactive : bool, optional
        Whether a person is there to answer a prompt. Defaults to whether
        standard input is a terminal.
    """

    def __init__(self, name: str, *, runner: Runner | None = None,
                 interactive: bool | None = None) -> None:
        self.name = check_machine_name(name)
        self._run = runner or subprocess.run
        self.interactive = (sys.stdin.isatty() if interactive is None
                            else interactive)

    def ssh_options(self, sockets: Path | None = None) -> list[str]:
        """The options every connection to this machine is made with."""
        options = ["-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}"]
        if not self.interactive:
            options += ["-o", "BatchMode=yes"]
        if sockets is not None:
            options += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={sockets / '%C'}",
                "-o", f"ControlPersist={CONTROL_PERSIST_S}",
            ]
        return options

    def ssh_command(self, remote: Sequence[str], *,
                    sockets: Path | None = None) -> list[str]:
        """The local command that runs ``remote`` on the machine."""
        # ssh joins its remaining arguments with spaces and hands them to the
        # remote shell, so they are quoted here once, for that shell.
        return ["ssh", *self.ssh_options(sockets), "--", self.name,
                shlex.join(list(remote))]

    def rsync_command(self, source: str, destination: str,
                      *extra: str) -> list[str]:
        """An rsync between this computer and the machine over the same ssh.

        ``source`` or ``destination`` names the machine's side with a
        leading ``:``, as in ``":/scratch/me/job/run/"``.
        """
        shell = shlex.join(["ssh", *self.ssh_options(_socket_dir())])
        def side(path: str) -> str:
            return f"{self.name}{path}" if path.startswith(":") else path
        return ["rsync", "-az", "-e", shell, *extra, side(source),
                side(destination)]

    def run(self, remote: Sequence[str], *, stdin: str | None = None,
            timeout: float = 120, show: bool = False) -> Answer:
        """Run ``remote`` on the machine and return what it printed.

        Refuses, rather than returning, when the machine could not be
        reached: ``ssh`` itself exits 255 for that, and a command that ran
        and failed returns its own code, which is the caller's to read.
        """
        command = self.ssh_command(remote, sockets=_socket_dir())
        # `show` lets a long command -- an install -- print as it goes rather
        # than leaving a person looking at nothing for ten minutes.
        capture = {} if show else {"capture_output": True}
        try:
            done = self._run(command, input=stdin, text=True, timeout=timeout,
                             check=False, **capture)
        except FileNotFoundError as exc:
            raise StudyError(
                "There is no ssh command on this computer, and FastMDXplora "
                "reaches other machines through it. Install OpenSSH's "
                "client.",
                code="environment.backend.missing",
                packages=["ssh"],
                install_command="an OpenSSH client from your system's "
                                "package manager",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise StudyError(
                f"{self.name} did not finish answering within {timeout:.0f} s.",
                code="environment.service.machine_unreachable",
                machine=self.name, reason="timed out",
            ) from exc
        if done.returncode == 255:
            said = (done.stderr or "").strip().splitlines()
            reason = said[-1] if said else "ssh exited without saying why"
            raise StudyError(
                f"Could not reach {self.name} over ssh: {reason}. Check that "
                f"`ssh {self.name}` works from this terminal.",
                code="environment.service.machine_unreachable",
                machine=self.name, reason=reason,
            )
        return Answer(stdout=done.stdout or "", stderr=done.stderr or "",
                      returncode=done.returncode)


def run_here(command: Sequence[str], *, runner: Runner | None = None,
             timeout: float = 3600, what: str = "") -> int:
    """Run a command on this computer, printing as it goes; its exit code.

    For the steps of a plan that happen here -- fetching a release image,
    copying it across -- and for rsync. A missing program is a refusal
    naming it, not a traceback.
    """
    try:
        done = (runner or subprocess.run)(list(command), text=True,
                                          timeout=timeout, check=False)
    except FileNotFoundError as exc:
        name = command[0]
        raise StudyError(
            f"There is no {name} command on this computer"
            + (f", and {what} needs it" if what else "") + ".",
            code="environment.backend.missing",
            packages=[name],
            install_command=f"{name} from your system's package manager",
        ) from exc
    return done.returncode
