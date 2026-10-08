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

**Only what is in the study's own folder travels.** A file the config names
outside the folder holding it is refused, not sent, whoever wrote the config:
a config an AI model wrote naming ``~/.ssh/id_ed25519`` would otherwise copy
the key to the machine. Links are resolved first, so a link in the folder
pointing out of it is outside too, and a folder that travels is refused if
any link in it points out of it, since the copy follows links. One
exception: a prepared study named by ``simulation.setup_from`` or
``simulation.resume_from`` may sit beside the folder, where a study usually
does (anywhere in the folder holding the study's folder), when it holds a
manifest FastMDXplora wrote; links in it are held to that study's folder.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

__all__ = ["Inputs", "gather_inputs", "link_out_of", "outside_said", "size_of"]

#: Settings that name where results go rather than something to read.
_NOT_INPUTS = frozenset({"output"})

#: A four-character PDB identifier, which setup fetches from RCSB.
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")

#: Settings naming a prepared study, which may sit beside the folder.
_STUDIES_NAMED = frozenset({"setup_from", "prepared_from", "resume_from"})


@dataclass
class Inputs:
    """The config as it travels, and what travels with it."""

    config: Any
    #: name under ``inputs/`` -> the file or directory on this computer.
    files: dict[str, Path] = field(default_factory=dict)
    #: Structures the machine will fetch from RCSB itself.
    fetched: list[str] = field(default_factory=list)
    #: name under ``inputs/`` -> the folder its links must stay inside.
    held_to: dict[str, Path] = field(default_factory=dict)


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


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def link_out_of(path: Path, folder: Path) -> Path | None:
    """The first link in ``path`` that leads out of ``folder``, if any.

    ``path`` is a file or a folder already resolved inside ``folder``. A
    folder is walked without following its links; each link is resolved,
    and one leading outside ``folder`` (or nowhere that resolves) is
    returned, since a copy that follows links would send what it names.
    """
    if path.is_symlink():
        # Resolved when gathered, so a link here now was made since.
        try:
            target = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return path
        if not _inside(target, folder):
            return path
    if not path.is_dir():
        return None
    for top, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            entry = Path(top) / name
            if not entry.is_symlink():
                continue
            try:
                target = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                return entry
            if not _inside(target, folder):
                return entry
    return None


def size_of(path: Path) -> int:
    """Bytes in a file, or in a folder's files, links not followed."""
    try:
        if not path.is_dir():
            return path.stat().st_size
        total = 0
        for top, _, files in os.walk(path, followlinks=False):
            for name in files:
                entry = Path(top) / name
                if not entry.is_symlink():
                    total += entry.stat().st_size
        return total
    except OSError:
        return 0


def _study_folder(path: Path) -> Path | None:
    """The study ``path`` is or is the setup folder of, by its manifest:
    one FastMDXplora wrote, which lists the phases it ran."""
    for folder in (path, path.parent) if path.name == "setup" else (path,):
        try:
            record = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("phases"), list):
            return folder
    return None


def outside_said(where: str, given: str, found: Path, folder: Path,
                 *, link: Path | None = None) -> str:
    """Why a file named outside the study's folder is not sent."""
    if link is not None:
        said = (f"`{where}` names {given}, and {link} in it is a link "
                f"leading out of {folder}, so it is not sent: what it points "
                "at would travel with the study. Copy the file in its place.")
    else:
        said = (f"`{where}` names {given}, which is {found}, outside the "
                f"study's folder {folder}, so it is not sent. Only files in "
                "the folder holding the config travel with it. Copy it into "
                "that folder and name it there"
                + (", or name a structure by its PDB identifier"
                   if where.rsplit(".", 1)[-1].split("[")[0] in ("system", "systems")
                   else "")
                + ".")
    return said


def gather_inputs(config: Any, base: Path) -> Inputs:
    """``config`` with local paths renamed under ``inputs/``, and the files.

    Refuses (``remote.input.outside``) a file outside ``base`` or a folder
    with a link leading out of it, except a prepared study named by
    ``setup_from`` or ``resume_from``, as the module says.
    """
    folder = base.resolve()
    found = Inputs(config=None)
    by_source: dict[Path, str] = {}

    def travel(path: Path, held_to: Path) -> str:
        if path in by_source:
            return by_source[path]
        name = path.name or "input"
        taken = set(found.files)
        candidate, n = name, 1
        while candidate in taken:
            n += 1
            candidate = f"{n}-{name}"
        found.files[candidate] = path
        found.held_to[candidate] = held_to
        by_source[path] = candidate
        return candidate

    def allowed(path: Path, key: str, where: str, given: str) -> Path:
        """The folder ``path``'s links are held to, or a refusal."""
        held_to = folder if _inside(path, folder) else None
        if (held_to is None and key in _STUDIES_NAMED and path.is_dir()
                and where.endswith(f"simulation.{key}")
                and folder.parent in path.parents):
            held_to = _study_folder(path)
        link = None if held_to is None else link_out_of(path, held_to)
        if held_to is None or link is not None:
            refused_in = held_to or folder
            raise StudyError(
                outside_said(where, given, path, refused_in, link=link),
                code="remote.input.outside", given=given, where=where,
                folder=str(refused_in))
        return held_to

    def walk(value: Any, key: str = "", where: str = "") -> Any:
        if isinstance(value, dict):
            return {k: (v if k in _NOT_INPUTS and not where
                        else walk(v, k, f"{where}.{k}" if where else str(k)))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item, key, f"{where}[{i}]") for i, item in enumerate(value)]
        if isinstance(value, str):
            path = _existing(value, folder)
            if path is not None:
                return f"inputs/{travel(path, allowed(path, key, where, value))}"
            if key in ("system", "systems") and _PDB_ID.match(value):
                found.fetched.append(value)
        return value

    found.config = walk(config)
    return found
