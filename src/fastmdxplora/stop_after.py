"""Making sure a run asked to stop does stop, in a process of its own.

A run asked to stop is given time to reach its next frame and write a
checkpoint there, so it can be carried on; one that has not stopped by then
is ended. An assistant's ``stop_study`` asked and then watched from a thread
of its server, so an assistant closed in the meantime took the watching with
it and a run that ignored the request went on. The watching is done here
instead, in a process started on its own, which outlives whatever asked:

    python -m fastmdxplora.stop_after PID FOLDER SECONDS ARGV_JSON

The run is identified again before it is ended, by its process and its
command line, so a process number the system has given to something else
since is never signalled. Where the run leads a session of its own, as a run
started from the GUI or by an assistant does, what is left of its group is
ended with it, as the GUI's Stop does: a parallel study's workers are not
left running a study nobody can see. A run started by hand in a terminal
belongs to the terminal's session, and its group may hold the person's own
commands (a ``tee`` it is piped to), so only the run itself is ended.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["see_it_stops", "watch"]

#: How often the run is looked at while it is given time.
LOOK_EVERY_S = 0.5


def see_it_stops(pid: int, folder: Path, argv: Any, grace_s: float) -> None:
    """Start the watcher: a process of its own, in a session of its own,
    holding none of this process's files."""
    command = [sys.executable, "-m", "fastmdxplora.stop_after", str(int(pid)),
               str(Path(folder)), repr(float(grace_s)), json.dumps(argv)]
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                               "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def watch(pid: int, folder: Path, argv: Any, grace_s: float) -> bool:
    """Wait for the run to stop; end it if it has not by ``grace_s``.
    True where it had to be ended."""
    from fastmdxplora.gui.exploration import (
        _end_what_is_left_of,
        _identify_run,
        _terminate_process,
    )

    deadline = time.monotonic() + max(0.0, grace_s)
    while time.monotonic() < deadline:
        if _identify_run(pid, folder, argv) is False:
            return False
        time.sleep(LOOK_EVERY_S)
    if _identify_run(pid, folder, argv) is not True:
        return False
    its_own_session = False
    if os.name != "nt":
        try:
            its_own_session = os.getsid(pid) == pid
        except OSError:
            pass
    _terminate_process(pid, force=True)
    if its_own_session:
        _end_what_is_left_of(pid)
    try:
        with (Path(folder) / "exploration.log").open("a", encoding="utf-8") as log:
            log.write(f"\n[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                      f"Ended: it had not stopped {grace_s:g} s after it was asked to.\n")
    except OSError:
        pass
    return True


def main(arguments: list[str] | None = None) -> int:
    given = sys.argv[1:] if arguments is None else arguments
    try:
        pid, folder, grace_s, argv = int(given[0]), Path(given[1]), float(given[2]), \
            json.loads(given[3])
    except (IndexError, ValueError):
        return 2
    if pid <= 0:
        return 2
    watch(pid, folder, argv, grace_s)
    return 0


if __name__ == "__main__":  # pragma: no cover - run as its own process
    sys.exit(main())
