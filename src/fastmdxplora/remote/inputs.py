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
a link in it, or in a folder one of its links leads to, points out of it or
back into itself, since the copy follows links. One
exception: a prepared study named by ``simulation.setup_from`` or
``simulation.resume_from`` may sit beside the folder, where a study usually
does (anywhere in the folder holding the study's folder), when it holds a
manifest FastMDXplora wrote; links in it are held to that study's folder.

Nothing in a place keys and credentials are kept (``.ssh``, ``.gnupg``,
``.aws`` and the like, and FastMDXplora's own settings) travels: a folder
that travels is refused if one is anywhere in it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

__all__ = ["Inputs", "gather_inputs", "link_out_of", "outside_said", "private_in",
           "size_of"]

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


def _walked(path: Path):
    """Each folder and file under ``path`` as a copy that follows links
    reaches it: the place it is reached at, and where it really is (``None``
    where a link leads nowhere, or back into a folder it is inside, which
    the copy would follow without end)."""
    within: dict[Path, tuple[Path, ...]] = {}
    unread: list[str] = []
    # A folder that cannot be listed cannot be checked: said as unresolved.
    for top, dirs, files in os.walk(path, followlinks=True,
                                    onerror=lambda e: unread.append(e.filename)):
        while unread:
            yield Path(unread.pop()), None
        here = Path(top)
        real = here.resolve()
        above = within.get(here.parent, ()) if here != path else ()
        if real in above:
            dirs[:] = []
            yield here, None
            continue
        within[here] = (*above, real)
        yield here, real
        for name in files:
            entry = here / name
            try:
                yield entry, entry.resolve(strict=True)
            except (OSError, RuntimeError):
                yield entry, None
        for name in list(dirs):
            entry = here / name
            if entry.is_symlink() and not entry.exists():
                dirs.remove(name)
                yield entry, None
    while unread:
        yield Path(unread.pop()), None


def link_out_of(path: Path, folder: Path) -> Path | None:
    """The first place in ``path`` a copy that follows links would be led
    out of ``folder`` by, if any.

    ``path`` is a file or a folder. Its links are followed as the copy
    follows them, through a folder a link leads to and the links in it in
    turn, and every file and folder reached is resolved: one outside
    ``folder``, one a link leads nowhere, or a folder reached twice (a
    link back into itself, which the copy would follow without end) is
    returned.
    """
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return path
    if not _inside(real, folder):
        return path
    if not path.is_dir():
        return None
    for entry, real in _walked(path):
        if real is None or not _inside(real, folder):
            return entry
    return None


#: Where keys and credentials are kept: never sent, whatever folder holds them.
_PRIVATE_PARTS = frozenset({".ssh", ".gnupg", ".aws", ".netrc", ".kube", ".docker",
                            ".pgpass", ".git-credentials", ".azure", ".password-store",
                            ".vault-token", ".pypirc", ".npmrc"})
#: The same, as a folder inside another.
_PRIVATE_PAIRS = frozenset({(".config", "gcloud"), (".config", "gh"),
                            (".config", "rclone"), ("library", "keychains")})


def _private(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    if _PRIVATE_PARTS & set(parts) or any(
            pair in _PRIVATE_PAIRS for pair in zip(parts, parts[1:])):
        return True
    # FastMDXplora's own settings: the machines, the AI model's key.
    from fastmdxplora.user_dir import user_config_dir

    try:
        return _inside(path, user_config_dir().resolve())
    except (OSError, RuntimeError):
        return False


def private_in(path: Path) -> Path | None:
    """The first place in ``path`` keys and credentials are kept, if any:
    ``path`` itself, or a file or folder a copy that follows links reaches
    in it, by where it is reached and where it really is."""
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        real = path
    if _private(path) or _private(real):
        return path
    if not path.is_dir():
        return None
    for entry, real in _walked(path):
        if _private(entry) or (real is not None and _private(real)):
            return entry
    return None


def _home_or_above(folder: Path) -> bool:
    """Whether ``folder`` is the top of the file system, the home folder or
    a folder holding it, also where the disk ignores the case of letters."""
    if folder == Path(folder.anchor):
        return True
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        return False
    for candidate in (home, *home.parents):
        if folder == candidate:
            return True
        try:
            if os.path.samefile(folder, candidate):
                return True
        except OSError:
            continue
    return False


def size_of(path: Path) -> int:
    """Bytes in a file, or in the files a copy that follows links sends of
    a folder."""
    try:
        if not path.is_dir():
            return path.stat().st_size
        return sum(real.stat().st_size for _, real in _walked(path)
                   if real is not None and real.is_file())
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
    if _home_or_above(folder):
        raise StudyError(
            f"The config is in {folder}, and a study's folder is what travels with "
            "it; that cannot be the top of the file system or your home folder. "
            "Put the config and its files in a folder of their own.",
            code="remote.input.outside", given=str(folder), where="",
            folder=str(folder))
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
            if held_to is not None and _home_or_above(held_to):
                held_to = None
        link = None if held_to is None else link_out_of(path, held_to)
        kept = None if held_to is None or link is not None else private_in(path)
        if kept is not None:
            raise StudyError(
                f"`{where}` names {given}, "
                + ("which is" if kept == path else f"and {kept} in it is")
                + " a place keys and credentials are kept, so it is not sent.",
                code="remote.input.outside", given=given, where=where,
                folder=str(held_to))
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
