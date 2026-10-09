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

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError

__all__ = ["Inputs", "fingerprint_of", "gather_inputs", "link_out_of", "outside_said",
           "private_in", "size_of"]

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
    try:
        # "~100 ns" is words, not a home folder: a home that cannot be found
        # makes it no path.
        path = Path(value).expanduser()
    except RuntimeError:
        return None
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


#: FastMDXplora's own settings: the AI model's key, the machines, the jobs.
_SETTINGS_KEPT = ("model.json", "calibration.json", "machines", "jobs", "sockets",
                  "gpu_memory", "gpu_memory_here.json")


def _settings_kept() -> tuple[str, ...]:
    """Where FastMDXplora keeps this person's settings, each resolved: never
    sent. The settings folder itself may hold a study; only these may not."""
    from fastmdxplora.user_dir import user_config_dir

    try:
        settings = user_config_dir().resolve()
    except (OSError, RuntimeError):
        return ()
    return tuple(str(settings / name) for name in _SETTINGS_KEPT)


def _private(path: Path, kept: tuple[str, ...] = ()) -> bool:
    parts = [part.casefold() for part in path.parts]
    if _PRIVATE_PARTS & set(parts) or any(
            pair in _PRIVATE_PAIRS for pair in zip(parts, parts[1:])):
        return True
    # By the path's text: a folder's parents are slow to walk 20,000 times.
    # Where the disk ignores the case of letters, so does this.
    text = _as_the_disk_reads(str(path))
    return any(text == place or text.startswith(place + os.sep)
               for place in map(_as_the_disk_reads, kept))


def _as_the_disk_reads(text: str) -> str:
    """``text`` as this platform's usual disk compares names: macOS and
    Windows ignore the case of letters."""
    return text.casefold() if sys.platform in ("darwin", "win32") else text


def private_in(path: Path) -> Path | None:
    """The first place in ``path`` keys and credentials are kept, if any:
    ``path`` itself, or a file or folder a copy that follows links reaches
    in it, by where it is reached and where it really is."""
    kept = _settings_kept()
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        real = path
    if _private(path, kept) or _private(real, kept):
        return path
    if not path.is_dir():
        return None
    for entry, real in _walked(path):
        if _private(entry, kept) or (real is not None and _private(real, kept)):
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


#: Files up to this size are read whole for a send's fingerprint, until
#: :data:`READ_IN_ALL_BYTES` have been read for one send; past either, a
#: file is known by its size, the file it is (device and inode) and when it
#: was last written and changed (``ctime``, which a copy that keeps times
#: does not put back).
READ_WHOLE_BYTES = 256 * 1024 * 1024
READ_IN_ALL_BYTES = 2 * 1024 * 1024 * 1024


def _file_print(real: Path, left: list[int]) -> tuple[int, str]:
    """A file's size, and what tells it from the same file changed;
    ``left`` is what may still be read for this send, and is spent."""
    # Opened without waiting, and what was opened is what is judged: a file
    # put in place of a pipe between a look and an open never holds the
    # reader, and a pipe or a device, which a copy does not send, is never
    # read.
    handle = os.open(real, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_BINARY", 0))
    with os.fdopen(handle, "rb") as opened:
        found = os.fstat(opened.fileno())
        if not stat.S_ISREG(found.st_mode):
            return 0, f"special:{stat.S_IFMT(found.st_mode)}"
        if found.st_size > READ_WHOLE_BYTES or found.st_size > left[0]:
            return found.st_size, (f"{found.st_size}:{found.st_dev}:{found.st_ino}:"
                                   f"{found.st_mtime_ns}:{found.st_ctime_ns}")
        left[0] -= found.st_size
        digest = hashlib.sha256()
        while chunk := opened.read(1 << 20):
            digest.update(chunk)
    return found.st_size, f"{found.st_size}:{digest.hexdigest()}"


def fingerprint_of(path: Path, left: list[int] | None = None) -> tuple[int, str]:
    """The bytes a copy that follows links sends of ``path`` (as
    :func:`size_of`), and a digest that changes when anything it sends
    does: a file's contents (or, past what is read whole, the file it is and
    when it was written and changed), and in a folder each file's place in
    it. ``left`` is what may still be read for one send, shared by its
    inputs (:data:`READ_IN_ALL_BYTES` where not given)."""
    left = [READ_IN_ALL_BYTES] if left is None else left
    digest = hashlib.sha256()
    total = 0
    if not path.is_dir():
        try:
            total, said = _file_print(path.resolve(strict=True), left)
        except (OSError, RuntimeError, ValueError):
            total, said = 0, "unread"
        digest.update(said.encode())
        return total, digest.hexdigest()
    lines = []
    try:
        # In one order whatever order the folder lists them in, so the
        # files read whole, before what may be read is spent, are the same
        # at the plan and at the send.
        walked = sorted(_walked(path), key=lambda entry: entry[0].as_posix())
    except (OSError, RuntimeError, ValueError):
        walked = [(path, None)]
    for here, real in walked:
        place = here.relative_to(path).as_posix() if here.is_relative_to(path) else str(here)
        said = "unread"
        if real is not None:
            try:
                if real.is_dir():
                    said = "folder"
                else:
                    size, said = _file_print(real, left)
                    total += size
            except (OSError, RuntimeError, ValueError):
                said = "unread"
        lines.append(f"{place}\0{said}\n")
    digest.update("".join(sorted(lines)).encode())
    return total, digest.hexdigest()


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
