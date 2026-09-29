"""Tell an open page when the study it shows has changed.

The page asked every three seconds for everything it draws, whether or not
anything had changed: seven requests a poll, about 140 a minute from each
open tab, and still up to three seconds behind the run. `GET /api/stream`
is a server-sent event stream instead. The server looks at the study's
files (their times and sizes, three folders deep, twice a second) and
sends one `change` event when any of them, or the state of the run, is
different; the page then asks for what it draws, once. Between changes it sends a
comment every so often, so a proxy does not close a quiet connection, and
the page keeps a slow poll as a fall-back for a stream a network cuts.

Only that something changed is sent, never what: every answer the page
reads still comes through the routes it always used, each with its own
checks.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["changes", "signature"]

#: How often the files are looked at, in seconds.
LOOK_EVERY = 0.5

#: A comment on a quiet connection this often, so proxies keep it open.
KEEP_ALIVE_EVERY = 15.0

#: A stream ends after this long and the page opens another. A thread is
#: held for each open stream; this bounds how long one can outlive the tab
#: that opened it when the socket's close is never seen.
LIFETIME = 300.0

#: How deep under the study the files are looked at: the study's own
#: records (1), each phase's (2), and each analysis's (3). A replica's own
#: records are deeper and change with the study's manifest besides.
DEPTH = 3

#: The most entries looked at each time. A folder of many thousands would
#: make looking the work; past this the slow poll is what notices.
MOST_ENTRIES = 5000


def _entries(root: Path) -> list[tuple[str, int, int]]:
    """Each file and folder under ``root`` to :data:`DEPTH`, with its time
    and size, in a fixed order."""
    import os

    seen: list[tuple[str, int, int]] = []
    stack = [(str(root), 1)]
    while stack and len(seen) < MOST_ENTRIES:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as listing:
                found = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in found:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            seen.append((entry.path, info.st_mtime_ns, info.st_size))
            if depth < DEPTH and entry.is_dir(follow_symlinks=False):
                stack.append((entry.path, depth + 1))
            if len(seen) >= MOST_ENTRIES:
                break
    return seen


def signature(root: Path | None, state: dict[str, Any]) -> tuple:
    """What the page would see: the study, the run's state, and the time
    and size of each file and folder its answers are read from."""
    seen: list[Any] = [str(root) if root else "",
                       state.get("status"), state.get("active_run"),
                       state.get("returncode"), state.get("running_elsewhere")]
    if root is not None:
        seen.append(hash(tuple(_entries(Path(root)))))
    return tuple(seen)


def changes(write: Callable[[bytes], None], look: Callable[[], tuple], *,
            closing: Callable[[], bool], clock: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep,
            lifetime: float = LIFETIME) -> str:
    """Write server-sent events until the stream should end, and say why.

    ``look`` gives the current signature; ``closing`` says the server is
    shutting down. A write that fails means the page went away.
    """
    began = clock()
    try:
        # Looked at before the first event is sent: anything that changes
        # after the page has it is then a change, however quickly it comes.
        last = look()
        # A retry hint, and one event at once, so a page that reconnected
        # after missing a change asks again straight away.
        write(b"retry: 3000\n\nevent: change\ndata: {}\n\n")
        quiet_since = clock()
        while True:
            if closing():
                return "closing"
            if clock() - began >= lifetime:
                return "lifetime"
            sleep(LOOK_EVERY)
            now = look()
            if now != last:
                last = now
                write(b"event: change\ndata: {}\n\n")
                quiet_since = clock()
            elif clock() - quiet_since >= KEEP_ALIVE_EVERY:
                write(b": still here\n\n")
                quiet_since = clock()
    except (BrokenPipeError, ConnectionError, OSError):
        return "gone"
