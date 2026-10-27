"""Request-local admission for existing record readers.

Ordinary callers retain pathlib's behavior. A public record aggregate can
enter ``confined_records`` so its readers and their helpers share one
boundary without caching a tree scan or changing another request's policy.
A record the GUI saves in the study goes through `save` (or `save_by_name`):
a new temporary file beside it, renamed into place, never through a link.
"""

from __future__ import annotations

import errno
import fnmatch
import os
import shutil
import stat as stat_module
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, TextIO

from fastmdxplora.refusals import OutsideWorkspace, RecordNotSaved


@dataclass
class ReadScope:
    root: Path
    admitted: Callable[[Path], bool]
    denied: set[str] = field(default_factory=set)
    descriptor: int | None = None
    #: Where each address led when first checked in this held read: a read
    #: asks the same folders again and again (a card, a hundred times).
    #: Kept for the read only; the bytes are still opened one part at a time
    #: from the pinned folder without following a link (`_open`), so an
    #: address retargeted since cannot lead a read out of the study.
    leads: dict[str, Path] = field(default_factory=dict)
    #: The records this held read did not save, each named in the folder it
    #: is held to, with why (`save`): a page says each was not saved.
    unsaved: dict[str, str] = field(default_factory=dict)

    def resolved(self, path: Path) -> Path:
        key = str(path)
        found = self.leads.get(key)
        if found is None:
            found = self.leads[key] = path.resolve()
        return found

    def checked(self, path: Path, *, remember: bool = True) -> Path:
        """Check both the requested address and its current destination."""
        path = path.absolute()
        relative: Path | None = None
        missing: FileNotFoundError | None = None
        try:
            relative = path.relative_to(self.root)
            resolved = self.resolved(path)
            resolved.relative_to(self.root)
            # One check where the address is where it leads (no link on
            # the way): the same answer, asked once.
            if self.admitted(path) and (resolved == path or self.admitted(resolved)):
                return resolved
            # Not there, where it would be is in the study: nothing is
            # refused, so it is not named as not read; the system's own
            # word that it is not there is given instead.
            try:
                os.lstat(path)
            except FileNotFoundError as exc:
                missing = exc
        except (OSError, RuntimeError, ValueError):
            pass
        if missing is not None:
            raise missing
        if remember:
            # A link out of the study is named by its own place in it; only
            # an address that is not in the study at all goes unnamed.
            named = relative is not None and ".." not in relative.parts
            self.denied.add(relative.as_posix() if named else "an unavailable record")
        raise OutsideWorkspace("The record could not be confined to the allowed study files.")


_scope: ContextVar[ReadScope | None] = ContextVar("record_read_scope", default=None)


def _root_descriptor(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    # A folder above the study need only be searchable (a shared cluster's
    # home folder is often 0711). Where the system has O_PATH, those are
    # opened without asking to read them; the study itself is opened to read.
    above = (os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW) if hasattr(os, "O_PATH") else flags
    parts = root.parts[1:]
    descriptor = os.open(root.anchor, above if parts else flags)
    try:
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            opened = os.open(part, flags if final else above, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = opened
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def confined_records(root: Path, admitted: Callable[[Path], bool]) -> Iterator[ReadScope]:
    """Apply an admission policy only to this call's record reads."""
    scope = ReadScope(Path(root).resolve(), admitted)
    # Open relative to a pinned directory on POSIX. Each resolved component
    # is opened without following links, so retargeting one after admission
    # cannot redirect an actual read outside the admitted tree.
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        scope.descriptor = _root_descriptor(scope.root)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)
        if scope.descriptor is not None:
            os.close(scope.descriptor)


def _open(scope: ReadScope, path: Path, *, directory: bool = False) -> int:
    resolved = scope.checked(path)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if scope.descriptor is not None:
        parts = resolved.relative_to(scope.root).parts
        descriptor = os.dup(scope.descriptor)
        try:
            for index, part in enumerate(parts):
                final = index == len(parts) - 1
                opened = os.open(part, flags | os.O_NOFOLLOW |
                                 (os.O_DIRECTORY if directory or not final else 0),
                                 dir_fd=descriptor)
                os.close(descriptor)
                descriptor = opened
            return descriptor
        except OSError as exc:
            os.close(descriptor)
            # Record a rejected link even when an inner legacy reader
            # catches the resulting OSError and supplies a fallback.
            scope.checked(path)
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                scope.denied.add(path.relative_to(scope.root).as_posix())
            raise
    descriptor = os.open(resolved, flags)
    try:
        # Windows does not support openat. Check the destination of the
        # opened handle itself before any bytes are read.
        import ctypes
        import msvcrt

        get_name = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        get_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong,
                             ctypes.c_ulong]
        get_name.restype = ctypes.c_ulong
        handle = msvcrt.get_osfhandle(descriptor)
        length = get_name(handle, None, 0, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        found = get_name(handle, buffer, len(buffer), 0)
        if not found or found >= len(buffer):
            scope.denied.add("an unavailable record")
            raise OutsideWorkspace("The opened record's location could not be verified.")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        scope.checked(Path(name))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_text(path: Path, *, encoding: str = "utf-8", errors: str | None = None,
              newline: str | None = None) -> TextIO:
    scope = _scope.get()
    if scope is None:
        return Path(path).open(encoding=encoding, errors=errors, newline=newline)
    descriptor = _open(scope, Path(path))
    if not stat_module.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        scope.denied.add("a non-regular record")
        raise OutsideWorkspace("Only regular study records may be read.")
    return os.fdopen(descriptor, encoding=encoding, errors=errors, newline=newline)


def read_bytes(path: Path) -> bytes:
    """A record's bytes (a figure), held as `open_text` holds a record's
    text."""
    scope = _scope.get()
    if scope is None:
        return Path(path).read_bytes()
    descriptor = _open(scope, Path(path))
    if not stat_module.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        scope.denied.add("a non-regular record")
        raise OutsideWorkspace("Only regular study records may be read.")
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


@contextmanager
def refusals_noted() -> Iterator[set[str]]:
    """The records this call's held read refuses within the block, each
    named as `refused` names it, those it had refused before among them:
    `refused` names each once, so a second refusal of one does not add to
    it. Empty outside a held read."""
    scope = _scope.get()
    if scope is None:
        yield set()
        return
    before = scope.denied
    noted: set[str] = set()
    scope.denied = noted
    try:
        yield noted
    finally:
        before |= noted
        scope.denied = before


def note_refused(names: frozenset[str] | set[str]) -> None:
    """Name again records this call's held read refused before, where a
    reader gives an answer it kept rather than read them again."""
    scope = _scope.get()
    if scope is not None:
        scope.denied.update(names)


def resolved(path: Path) -> Path:
    """Where ``path`` leads, as this call's held read found it (`ReadScope`),
    or now, outside a held read."""
    scope = _scope.get()
    return Path(path).resolve() if scope is None else scope.resolved(Path(path))


def refused() -> frozenset[str]:
    """The records this call's held read has refused so far, each named in
    the folder it is held to; none outside a held read."""
    scope = _scope.get()
    return frozenset(scope.denied) if scope is not None else frozenset()


def read_text(path: Path, *, encoding: str = "utf-8", errors: str | None = None) -> str:
    with open_text(path, encoding=encoding, errors=errors) as stream:
        return stream.read()


def open_binary(path: Path) -> BinaryIO:
    """A record's bytes as a stream, held as `open_text` holds its text: a
    topology counted line by line is not read whole into memory."""
    scope = _scope.get()
    if scope is None:
        return Path(path).open("rb")
    descriptor = _open(scope, Path(path))
    if not stat_module.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        scope.denied.add("a non-regular record")
        raise OutsideWorkspace("Only regular study records may be read.")
    return os.fdopen(descriptor, "rb")


# ---------------------------------------------------------------------------
# A record saved in the study
# ---------------------------------------------------------------------------

#: The process's file-creation mask (`_umask`): a record saved is given the
#: mode any file written here would have, not `mkstemp`'s owner-only mode
#: (a lab's shared study is read by the group).
_UMASK: int | None = None
_UMASK_READ = threading.Lock()
#: Why a record is not saved where it would be.
NOT_SAVED = ("Where the record would be saved is not one of the study's own, so it is "
             "not saved.")
#: Why a record could not be saved, said for a page, by the system's word.
_NO_ROOM = frozenset({errno.ENOSPC, errno.EFBIG, getattr(errno, "EDQUOT", errno.ENOSPC)})
#: Less room than this left where a writer failed is said as no room: MDTraj
#: writes DCD and XTC in C and reports a short write in its own words
#: (`TypeError`, "XTC write error").
_LITTLE_ROOM = 16 << 20


def _umask() -> int:
    """The process's file-creation mask, read once: from `/proc/self/status`
    where the system has it, which changes nothing; else by setting it and
    setting it back, once, under a lock."""
    global _UMASK
    if _UMASK is None:
        with _UMASK_READ:
            if _UMASK is None:
                _UMASK = _umask_read()
    return _UMASK


def _umask_read() -> int:
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def _named(scope: ReadScope, target: Path) -> str:
    """``target`` named in the folder ``scope`` is held to, as a refused read
    is named; "a record" where it is not in it."""
    try:
        return target.absolute().relative_to(scope.root).as_posix()
    except ValueError:
        try:
            return scope.resolved(target.parent).joinpath(target.name).relative_to(
                scope.root).as_posix()
        except (OSError, RuntimeError, ValueError):
            return "a record"


def _not_saved(target: Path, why: str) -> None:
    """Note, in this call's held read, that ``target`` was not saved and why."""
    scope = _scope.get()
    if scope is not None:
        scope.unsaved[_named(scope, target)] = why


def note_not_saved(target: Path, why: str) -> None:
    """Name ``target`` again as not saved, with why, where a reader gives
    what it said before rather than save it again."""
    _not_saved(Path(target), why)


def not_saved() -> dict[str, str]:
    """The records this call's held read did not save, each with why; none
    outside a held read."""
    scope = _scope.get()
    return dict(scope.unsaved) if scope is not None else {}


def _leads_out(path: Path) -> bool:
    """Whether a read held to the study would refuse ``path``, without
    naming it as not read: it is not read, it is not saved."""
    scope = _scope.get()
    try:
        scope.checked(path, remember=False)
    except FileNotFoundError:
        return False
    except OutsideWorkspace:
        return True
    return False


def _may_save(target: Path) -> None:
    """Refuse, in a read held to the study, where ``target`` or the folder
    it is saved in is a link out of the study: named as not saved, and
    never written over or through. Nothing outside a held read, where the
    temporary file and the rename still never follow a link at either
    name."""
    if _scope.get() is None:
        return
    if _leads_out(target.parent) or _leads_out(target):
        _not_saved(target, "it leads outside the study")
        raise OutsideWorkspace(NOT_SAVED)


def _failed(target: Path, exc: BaseException) -> str:
    """Why a save of ``target`` failed, noted (`_not_saved`): a full disk,
    MDTraj's own error on one included, is said as no room."""
    number = getattr(exc, "errno", None)
    if number in _NO_ROOM or (not isinstance(exc, OSError) and _little_room(target)):
        why = "no room"
    elif isinstance(exc, OSError):
        why = exc.strerror or str(exc) or type(exc).__name__
    else:
        why = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    _not_saved(target, why)
    return why


def _little_room(target: Path) -> bool:
    try:
        room = os.statvfs(target.parent)
    except (OSError, AttributeError):
        return False
    return room.f_bavail * room.f_frsize < _LITTLE_ROOM


def _temporary_beside(target: Path) -> tuple[int, str]:
    """A new file beside ``target``, by a name no other writer has, opened
    with ``O_CREAT | O_EXCL``: a link planted at any name is never
    followed (`tempfile.mkstemp`). Hidden, and ending ``.tmp``, so no
    listing of the study offers it."""
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.",
                                        suffix=".tmp")
    if hasattr(os, "fchmod"):
        try:
            os.fchmod(descriptor, 0o666 & ~_umask())
        except OSError:
            pass
    return descriptor, name


def _into_place(name: str, target: Path) -> None:
    """The temporary file ``name`` renamed to ``target``: a link there is
    replaced, never written through."""
    Path(name).replace(target)


def save(target: Path, content: bytes | str | Callable[[BinaryIO], None]) -> None:
    """``content`` saved as ``target`` at once: its bytes, its text in
    UTF-8, or what ``content(stream)`` writes to a binary stream (NumPy's
    `savez`). Written to a new temporary file beside it (`_temporary_beside`)
    and renamed into place; the temporary file is removed on any failure.
    In a read held to the study, refused with `OutsideWorkspace` where the
    record or its folder is a link out of it (`_may_save`); a record not
    saved, refused or failing, is named with why (`not_saved`)."""
    target = Path(target)
    _may_save(target)
    try:
        descriptor, name = _temporary_beside(target)
    except OSError as exc:
        _failed(target, exc)
        raise
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if callable(content):
                content(stream)
            else:
                stream.write(content.encode("utf-8") if isinstance(content, str) else content)
        _into_place(name, target)
    except Exception as exc:
        _removed(name)
        why = _failed(target, exc)
        if isinstance(exc, OSError):
            raise
        # A writer's own error is an `OSError` too, so a page says the
        # record was not saved rather than fail.
        raise RecordNotSaved(f"{target.name} could not be saved: {why}") from exc
    except BaseException:
        _removed(name)
        raise


def save_by_name(target: Path, write: Callable[[str], None]) -> None:
    """``target`` saved as `save` saves it, by a writer that is given a
    file's name, not a stream (MDTraj's `save_dcd` and `save_xtc`, which
    open the name again themselves, without ``O_EXCL``): the name is in a
    new folder of its own beside the record (`tempfile.mkdtemp`, made with
    ``O_EXCL`` and open to this user alone), so no other process can plant
    a link at it while the writer writes. The file is renamed into place
    and the folder removed, whatever happens."""
    target = Path(target)
    _may_save(target)
    try:
        folder = tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    except OSError as exc:
        _failed(target, exc)
        raise
    name = os.path.join(folder, target.name)
    try:
        write(name)
        _into_place(name, target)
    except Exception as exc:
        why = _failed(target, exc)
        if isinstance(exc, OSError):
            raise
        # MDTraj's own error on a short write (`TypeError`, "XTC write
        # error"): an `OSError`, as `save` gives.
        raise RecordNotSaved(f"{target.name} could not be saved: {why}") from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _removed(name: str) -> None:
    try:
        os.unlink(name)
    except OSError:
        pass


def open_to_append(path: Path, *, encoding: str = "utf-8", buffering: int = -1,
                   newline: str | None = None) -> TextIO:
    """A record of the study opened to add to its end (a run's log, its
    events), never through a link: the record is opened with
    ``O_NOFOLLOW``, in its folder opened without following a link at the
    folder's own name, so a link at either is refused with
    `OutsideWorkspace` and nothing is written where it leads. Made where
    it is not there yet, with the mode any file written here has."""
    path = Path(path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        if nofollow and os.open in os.supports_dir_fd:
            above = Path(path.parent.parent).resolve() / path.parent.name
            folder = os.open(above, os.O_RDONLY | os.O_DIRECTORY | nofollow)
            try:
                descriptor = os.open(path.name, flags, 0o666, dir_fd=folder)
            finally:
                os.close(folder)
        else:
            descriptor = os.open(path, flags, 0o666)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise OutsideWorkspace(f"{path.name} is a link, so nothing is written to it.") from exc
        raise
    return os.fdopen(descriptor, "a", encoding=encoding, buffering=buffering, newline=newline)


def can_read(path: Path) -> bool:
    """Whether a possible source is in scope, without reading or refusing it.

    This lets a reader distinguish an unknown owning-study relationship
    from a missing record. Actual reads still enforce admission afresh.
    """
    scope = _scope.get()
    if scope is None:
        return True
    try:
        scope.checked(Path(path), remember=False)
        return True
    except (OutsideWorkspace, FileNotFoundError):
        return False


def stat(path: Path) -> os.stat_result:
    scope = _scope.get()
    if scope is None:
        return Path(path).stat()
    if scope.descriptor is None:
        return scope.checked(Path(path)).stat()
    resolved = scope.checked(Path(path))
    if resolved == scope.root:
        return os.fstat(scope.descriptor)
    descriptor = _open(scope, resolved.parent, directory=True)
    try:
        result = os.stat(resolved.name, dir_fd=descriptor, follow_symlinks=False)
        if stat_module.S_ISLNK(result.st_mode):
            scope.denied.add(Path(path).relative_to(scope.root).as_posix())
            raise OutsideWorkspace("The record changed after its location was checked.")
        return result
    finally:
        os.close(descriptor)


def is_file(path: Path) -> bool:
    if _scope.get() is None:
        return Path(path).is_file()
    try:
        return stat_module.S_ISREG(stat(path).st_mode)
    except OSError:
        return False


def is_dir(path: Path) -> bool:
    if _scope.get() is None:
        return Path(path).is_dir()
    try:
        return stat_module.S_ISDIR(stat(path).st_mode)
    except OSError:
        return False


def exists(path: Path) -> bool:
    if _scope.get() is None:
        return Path(path).exists()
    try:
        stat(path)
        return True
    except OSError:
        return False


def iterdir(path: Path) -> Iterator[Path]:
    scope = _scope.get()
    if scope is None:
        yield from Path(path).iterdir()
        return
    path = Path(path)
    if scope.descriptor is not None:
        descriptor = _open(scope, path, directory=True)
        try:
            names = os.listdir(descriptor)
        finally:
            os.close(descriptor)
    else:
        names = os.listdir(scope.checked(path))
    for name in names:
        candidate = path / name
        # Discovery ignores private names. A reader requesting one
        # explicitly still records its refusal through checked().
        if scope.admitted(candidate):
            yield candidate


def glob(path: Path, pattern: str) -> Iterator[Path]:
    if _scope.get() is None:
        yield from Path(path).glob(pattern)
        return
    parts = pattern.split("/")
    candidates = [Path(path)]
    for index, part in enumerate(parts):
        found = []
        for parent in candidates:
            if not is_dir(parent):
                continue
            wildcard = any(char in part for char in "*?[")
            if wildcard:
                children = (child for child in iterdir(parent)
                            if fnmatch.fnmatchcase(child.name, part))
            else:
                children = iter([parent / part])
            if index == len(parts) - 1:
                found.extend(child for child in children if wildcard or exists(child))
            else:
                found.extend(child for child in children if is_dir(child))
        candidates = found
    yield from candidates
