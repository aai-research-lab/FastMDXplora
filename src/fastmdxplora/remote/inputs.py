"""The files a config points at, gathered so they can travel with it.

A config names files on this computer: the structure, a ligand's SDF, force
field XMLs, a trajectory to analyse, a prepared study to continue from.
None of them exist on the machine the study is sent to. So every string in
the config that names an existing file or directory -- relative to the
config's own folder, as the config means it -- is sent under ``inputs/``,
and the copy of the config that travels names it there.

Read from the values rather than from a list of path-valued settings, so a
setting added tomorrow that takes a file travels without anyone remembering
to add it here.

A structure named by PDB ID is fetched by the machine itself, which needs
the internet; the caller refuses where it has none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Inputs", "gather_inputs"]

#: Settings that name where results go rather than something to read.
_NOT_INPUTS = frozenset({"output"})

#: A four-character PDB identifier, which setup fetches from RCSB.
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


@dataclass
class Inputs:
    """The config as it travels, and what travels with it."""

    config: Any
    #: name under ``inputs/`` -> the file or directory on this computer.
    files: dict[str, Path] = field(default_factory=dict)
    #: Structures the machine will fetch from RCSB itself.
    fetched: list[str] = field(default_factory=list)


def _existing(value: str, base: Path) -> Path | None:
    if not value or "\n" in value or len(value) > 4096:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve() if path.exists() else None
    except OSError:
        return None


def gather_inputs(config: Any, base: Path) -> Inputs:
    """``config`` with local paths renamed under ``inputs/``, and the files."""
    found = Inputs(config=None)
    by_source: dict[Path, str] = {}

    def travel(path: Path) -> str:
        if path in by_source:
            return by_source[path]
        name = path.name or "input"
        taken = set(found.files)
        candidate, n = name, 1
        while candidate in taken:
            n += 1
            candidate = f"{n}-{name}"
        found.files[candidate] = path
        by_source[path] = candidate
        return candidate

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: (v if k in _NOT_INPUTS and not key else walk(v, k))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        if isinstance(value, str):
            path = _existing(value, base)
            if path is not None:
                return f"inputs/{travel(path)}"
            if key in ("system", "systems") and _PDB_ID.match(value):
                found.fetched.append(value)
        return value

    found.config = walk(config)
    return found
