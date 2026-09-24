"""The programs installed beside this Python, found without activating it.

AmberTools gives every ligand its AM1-BCC charges, and the OpenFF toolkit
finds it by looking for ``antechamber`` and ``sqm`` on ``PATH``, once, when
the toolkit is first imported. A conda environment puts them in its own
``bin`` directory, beside the interpreter, and only activating the
environment puts that directory on ``PATH``. Running the environment's
Python by its full path does not, and neither does a command sent over
``ssh`` (which reads no profile) or a scheduler job: AmberTools is then
installed and invisible, and a ligand fails after the system is solvated
with an error that names neither.

So the directory that holds the running interpreter's own programs is put
at the end of ``PATH`` when it is missing. Nothing outside this environment
is added, and anything already on ``PATH`` keeps its precedence.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["put_own_programs_on_path"]


def _own_bin() -> Path:
    """Where this environment keeps its programs."""
    return Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")


def put_own_programs_on_path(environ: dict | None = None) -> bool:
    """Append this environment's program directory to ``PATH`` if absent.

    Returns True when ``PATH`` was changed. ``environ`` defaults to
    ``os.environ``; tests pass their own mapping.
    """
    env = os.environ if environ is None else environ
    own = _own_bin()
    if not own.is_dir():
        return False
    entries = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    known = set()
    for entry in entries:
        try:
            known.add(Path(entry).resolve())
        except OSError:
            continue
    if own.resolve() in known:
        return False
    env["PATH"] = os.pathsep.join(entries + [str(own)])
    return True
