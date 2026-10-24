"""Open a shared study, from its DOI, its Zenodo address or the file.

The archive is checked whole before anything in it is opened: downloaded
up to a limit and checked against the MD5 Zenodo recorded for it, unpacked
only inside a folder of its own (no absolute path, no `..`, no link, no
more than the limit), its packing list held to RO-Crate 1.2 and the study
profile, and every file in it of the size and SHA-256 the list gives, with
nothing in it the list does not name. Only then are the study's own paths
put back (`__FASTMDX_STUDY__` becomes the folder it is opened into), where
it came from recorded in `shared_from.json`, and the folder moved into
place whole. Nothing in an archive is run.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastmdxplora.sharing import STUDY, ShareRefused, said_size
from fastmdxplora.sharing import crate as packing_list
from fastmdxplora.sharing.pack import never_shared

__all__ = ["MOST_BYTES", "open_shared", "unpacked_limit"]

#: The largest archive downloaded or opened unless more is allowed.
MOST_BYTES = 2 * 1024 ** 3
#: The most files an archive may hold.
MOST_FILES = 200_000
SHARED_FROM = "shared_from.json"
_AGENT = "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"
#: How long a download waits for a byte more before it counts as stalled,
#: and is asked for again from where it stopped.
STALLED_S = 30
#: What a disk out of room says: full, or its owner's share of it used.
_NO_ROOM = frozenset({errno.ENOSPC, errno.EDQUOT})


def _gb(size: float) -> str:
    """A size as said to the person: in GB, or in MB below a tenth of one."""
    return f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{size / 1e6:.0f} MB"


def unpacked_limit(most_bytes: int) -> int:
    """How much an archive may unpack to: compressed text unpacks to several
    times its size, and a crafted archive to thousands."""
    return 4 * most_bytes


def _whole_size(answer: Any, start: int) -> int:
    """The whole file's size an answer gives: the total of its
    `Content-Range`, else where its part starts and its `Content-Length`;
    0 where it gives neither."""
    found = re.search(r"/(\d+)\s*$", str(answer.headers.get("Content-Range") or ""))
    if found:
        return int(found.group(1))
    length = str(answer.headers.get("Content-Length") or "")
    return start + int(length) if length.isdigit() else 0


def _range_start(answer: Any) -> int | None:
    """Where the bytes of a 206 answer start in the file, by its
    `Content-Range` (`bytes 1000-2999/3000`); None where it names none."""
    found = re.match(r"\s*bytes\s+(\d+)-", str(answer.headers.get("Content-Range") or ""))
    return int(found.group(1)) if found else None


def _no_room(where: Path, error: OSError, how_far: str = "") -> str:
    return (f"The shared study could not be written to {where}: the disk there has no room "
            f"for it ({error.strerror}){how_far}. Make room there, or open it into another "
            "folder.")


def _too_big(where: Path, error: OSError) -> str:
    return (f"The shared study could not be written to {where}: the disk there takes no file "
            f"this large ({error.strerror}). Open it into a folder on another disk.")


def _download(url: str, to: Path, *, most: int, md5: str,
              said: Callable[[str], None] | None, size: int = 0,
              room: Path | None = None) -> None:
    """``url`` written to ``to``, checked against ``md5`` as it lands. A
    download cut short goes on from where it stopped (an HTTP range), one
    Zenodo says to ask again for is asked again after a wait, each up to
    :data:`~fastmdxplora.sharing.zenodo.TRIES` times in a row without a byte
    more; a server that sends the whole file again is read from its start.
    A piece is counted only once it is written: one this computer could not
    write is said as the system says it, never asked for again."""
    from fastmdxplora.sharing import zenodo

    digest = hashlib.md5()  # noqa: S324 - Zenodo's own checksum, not a protection
    # A try counts as failed unless it reached further than any before:
    # a server that sends the file from its start each time, cut at the
    # same place, is asked no more than the tries.
    read, furthest, tenth, failures = 0, 0, 0, 0
    # This computer's disk, not the connection (full, say): no try again
    # would mend it.
    unwritten: OSError | None = None
    with to.open("wb") as out:
        while True:
            headers = {"User-Agent": _AGENT}
            if read:
                headers["Range"] = f"bytes={read}-"
            why = ""
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=STALLED_S) as answer:
                    # A gateway's page in place of the file: a try that failed.
                    page = str(answer.headers.get("Content-Type") or "").lower().startswith(
                        "text/")
                    start = _range_start(answer) if answer.status == 206 else 0
                    if not size and not page:
                        size = _whole_size(answer, start or 0)
                        if size > most:
                            raise ShareRefused(
                                f"{url} holds {_gb(size)}, over {_gb(most)}; "
                                "nothing was kept. Allow more with --open-most-gb.",
                                code="environment.share.too_large", url=url)
                    if start is None and answer.status == 206:
                        # A part, not saying of what: taken as following
                        # what is here only where that was asked for.
                        start = read
                    if page:
                        why = "it answered with a page of text, not the archive"
                    elif start != read and start:
                        # The file from somewhere else: no part of it is
                        # known to follow what is here.
                        why = f"it sent the file from byte {start}, not {read}"
                    else:
                        if start != read:
                            # The whole file again, not the rest of it.
                            if said:
                                said(f"{url} sent the whole file again, not the rest of "
                                     "it; reading it from its start.")
                            out.seek(0)
                            out.truncate()
                            digest, read, tenth = hashlib.md5(), 0, 0  # noqa: S324
                        # What has come, as it comes: a stall then loses none
                        # of what came before it.
                        reader = getattr(answer, "read1", answer.read)
                        for piece in iter(functools.partial(reader, 1 << 20), b""):
                            if read + len(piece) > most:
                                raise ShareRefused(
                                    f"{url} sent more than {_gb(most)}; nothing was "
                                    "kept. Allow more with --open-most-gb.",
                                    code="environment.share.too_large", url=url)
                            try:
                                out.write(piece)
                                out.flush()
                            except OSError as exc:
                                unwritten = exc
                                break
                            read += len(piece)
                            digest.update(piece)
                            if said and size >= 10 << 20 and read * 10 // size > tenth:
                                tenth = min(read * 10 // size, 9)
                                if tenth and read < size:
                                    said(f"Downloaded {read / 1e6:.1f} of "
                                         f"{size / 1e6:.1f} MB.")
                        if unwritten is not None or not size or read >= size:
                            break
                        # Ended before the file did: the connection was cut.
                        why = "the answer ended early"
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and size and read == size:
                    break
                whole = re.fullmatch(r"bytes \*/(\d+)",
                                     str((exc.headers or {}).get("Content-Range") or "").strip())
                if exc.code == 416 and read and whole and int(whole.group(1)) == read:
                    # The file is whole at what is here (the size recorded is
                    # not the file's): its MD5 says whether it is the one.
                    break
                if exc.code == 416 and read:
                    # A range it would not send: the whole file again.
                    out.seek(0)
                    out.truncate()
                    digest, read, tenth = hashlib.md5(), 0, 0  # noqa: S324
                    failures += 1
                    if failures < zenodo.TRIES:
                        _again(f"{url} would not send the rest of the file", failures, None,
                               said)
                        continue
                failures = 0 if read > furthest else failures + 1
                furthest = max(furthest, read)
                if exc.code in zenodo.AGAIN and failures < zenodo.TRIES:
                    _again(f"{url} answered HTTP {exc.code}", failures, exc.headers, said)
                    continue
                try:
                    said_back = exc.read().decode("utf-8", errors="replace")
                except (OSError, http.client.HTTPException):
                    said_back = ""
                # A 404 is Zenodo's own only in its own words; a gateway's page
                # is to be asked again.
                closed = exc.code in (401, 403) or (exc.code in (404, 410)
                                                    and zenodo._zenodo_s(said_back, exc.code))
                raise ShareRefused(
                    f"The shared study could not be downloaded from {url}: HTTP {exc.code}"
                    + (f", still after {failures} tries" if failures > 1 else "")
                    + _got(read, size) + ". Nothing was kept; "
                    + ("it is not open to be downloaded (restricted, or not published): ask "
                       "its owner for access." if closed else "open it again later."),
                    code="environment.service.unreachable", url=url) from exc
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                # A connection cut mid-file reads as an incomplete answer.
                why = str(exc)
            failures = 0 if read > furthest else failures + 1
            furthest = max(furthest, read)
            if failures >= zenodo.TRIES:
                raise ShareRefused(
                    f"The shared study could not be downloaded from {url}: {why}"
                    + (f", still after {failures} tries" if failures > 1 else "")
                    + _got(read, size) + ". Nothing was kept; open it again later.",
                    code="environment.service.unreachable", url=url)
            _again(f"The download from {url} stopped{_got(read, size, 'at')} ({why})",
                   failures, None, said)
    if unwritten is not None:
        if unwritten.errno in _NO_ROOM:
            raise ShareRefused(_no_room(room or to.parent, unwritten, _got(read, size)),
                               code="environment.share.too_large",
                               path=str(room or to.parent)) from unwritten
        if unwritten.errno == errno.EFBIG:
            raise ShareRefused(_too_big(room or to.parent, unwritten),
                               code="environment.share.too_large",
                               path=str(room or to.parent)) from unwritten
        raise unwritten
    if md5 and digest.hexdigest() != md5:
        raise ShareRefused(
            f"What {url} sent is not the file Zenodo recorded (MD5 {digest.hexdigest()}, "
            f"not {md5}); nothing was kept. Open it again: if it says so again, the file "
            "Zenodo serves is not the one it recorded.", code="environment.share.unverified",
            url=url)
    if said:
        said(f"Downloaded {said_size(read)}.")


def _got(read: int, size: int, how: str = "having") -> str:
    """How far a download got: ", with 30.0 of 111.5 MB downloaded" (in kB
    where the file is under 0.1 MB)."""
    if not read and how == "having":
        return ""
    most = size or read
    unit, scale = ("bytes", 1) if most < 1000 else \
        ("kB", 1e3) if most < 100_000 else ("MB", 1e6)
    shown = ".0f" if scale == 1 else ".1f"
    if read != size and size and f"{read / scale:{shown}}" == f"{size / scale:{shown}}":
        # Short of the whole by less than its unit says: in bytes.
        unit, scale, shown = "bytes", 1, ".0f"
    if scale == 1 and (size or read) == 1:
        unit = "byte"
    if size and read < {"kB": 1000, "MB": 100_000}.get(unit, 0):
        # Begun, by less than the whole's unit says: each in its own.
        got = f"{said_size(read)} of {size / scale:{shown}} {unit}"
    else:
        got = f"{read / scale:{shown}}{f' of {size / scale:{shown}}' if size else ''} {unit}"
    return f" {how} {got}" if how == "at" else f", with {got} downloaded"


def _again(what: str, failures: int, headers: Any,
           said: Callable[[str], None] | None) -> None:
    from fastmdxplora.sharing import zenodo

    wait = zenodo.wait_before(max(failures, 1), headers)
    if said:
        said(f"{what}; trying again in {wait:.0f} s.")
    zenodo._sleep(wait)


def _members(archive: zipfile.ZipFile, most_bytes: int) -> list[zipfile.ZipInfo]:
    """The archive's files, each safe to unpack; refused at the first that
    is not."""
    members, total = [], 0
    infos = archive.infolist()
    if len(infos) > MOST_FILES:
        raise ShareRefused(f"The archive holds {len(infos)} files, more than {MOST_FILES}.",
                           code="environment.share.unsafe")
    for info in infos:
        name = info.filename
        pure = PurePosixPath(name)
        kind = (info.external_attr >> 16) & 0o170000
        if (not pure.parts or pure.is_absolute() or ".." in pure.parts or "\\" in name
                or ":" in pure.parts[0]
                or kind in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO,
                            stat.S_IFSOCK)):
            raise ShareRefused(f"The archive names {name!r}, outside itself or not a file; "
                               "nothing was opened.", code="environment.share.unsafe")
        if info.is_dir():
            continue
        if name == packing_list.PREVIEW:
            # A page to read in a browser, listed nowhere and never opened.
            continue
        if name != packing_list.METADATA and never_shared(name):
            # What packing never writes: the Agent's conversations, a
            # process's record, a dot file, scratch or what was set aside.
            # Opened, the GUI would act on them as its own.
            raise ShareRefused(f"The archive holds {name!r}, which a shared study never "
                               "does; nothing was opened.", code="environment.share.unsafe")
        if info.flag_bits & 0x1:
            raise ShareRefused(f"The archive's {name} is encrypted; nothing was opened.",
                               code="environment.share.unsafe")
        total += info.file_size
        if total > unpacked_limit(most_bytes):
            raise ShareRefused(
                f"The archive unpacks to more than {_gb(unpacked_limit(most_bytes))}; "
                "nothing was opened.", code="environment.share.unsafe")
        members.append(info)
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


#: Letters no decomposition writes plainly, in their plain forms.
_LETTERS = str.maketrans({"\u00df": "ss", "\u00c6": "AE", "\u00e6": "ae", "\u00d8": "O",
                          "\u00f8": "o", "\u0152": "OE", "\u0153": "oe", "\u0141": "L",
                          "\u0142": "l", "\u0110": "D", "\u0111": "d", "\u00de": "Th",
                          "\u00fe": "th", "\u00d0": "D", "\u00f0": "d", "\u0131": "i",
                          "\u0126": "H", "\u0127": "h", "\u0166": "T", "\u0167": "t",
                          "\u014a": "NG", "\u014b": "ng", "\u0138": "k", "\u1e9e": "SS",
                          "\u018f": "E", "\u0259": "e", "\u0181": "B", "\u0253": "b",
                          "\u018a": "D", "\u0257": "d", "\u0198": "K", "\u0199": "k",
                          "\u0197": "I", "\u0268": "i", "\u01b5": "Z", "\u01b6": "z",
                          "\u01e4": "G", "\u01e5": "g", "\u023b": "C", "\u023c": "c",
                          "\u0246": "E", "\u0247": "e",
                          # Greek, as written in plain letters (a micro sign as u).
                          "\u03bc": "m", "\u039c": "M",
                          "\u03b1": "a", "\u03b2": "b", "\u03b3": "g", "\u03b4": "d",
                          "\u03b5": "e", "\u03b6": "z", "\u03b7": "e", "\u03b8": "th",
                          "\u03b9": "i", "\u03ba": "k", "\u03bb": "l", "\u03bd": "n",
                          "\u03be": "x", "\u03bf": "o", "\u03c0": "p", "\u03c1": "r",
                          "\u03c3": "s", "\u03c2": "s", "\u03c4": "t", "\u03c5": "y",
                          "\u03c6": "ph", "\u03c7": "ch", "\u03c8": "ps", "\u03c9": "o",
                          "\u0391": "A", "\u0392": "B", "\u0393": "G", "\u0394": "D",
                          "\u0395": "E", "\u0396": "Z", "\u0397": "E", "\u0398": "Th",
                          "\u0399": "I", "\u039a": "K", "\u039b": "L", "\u039d": "N",
                          "\u039e": "X", "\u039f": "O", "\u03a0": "P", "\u03a1": "R",
                          "\u03a3": "S", "\u03a4": "T", "\u03a5": "Y", "\u03a6": "Ph",
                          "\u03a7": "Ch", "\u03a8": "Ps", "\u03a9": "O", "\u2126": "O",
                          "\u0192": "f", "\u0186": "O", "\u0254": "o", "\u0190": "E",
                          "\u025b": "e", "\u01dd": "e", "\u018e": "E", "\u0292": "z",
                          "\u01b7": "Z", "\u0256": "d", "\u019d": "N", "\u0272": "n",
                          "\u0289": "u", "\ua733": "aa", "\ua732": "AA", "\u0191": "F",
                          "\u0189": "D", "\u0244": "U", "\u0283": "sh", "\u01a9": "Sh",
                          "\u0278": "ph"})


def _slug(text: str) -> str:
    # Letters kept in their plain forms: a ligature, a full-width letter or
    # an accented one is not dropped.
    # (Written plainly before and after the letters come apart: one comes
    # apart into another the table writes, as a slashed o with an accent.)
    # (A micro sign as u, before it reads as the Greek mu.)
    # (So too a unit's sign holding it, as a micrometre's.)
    text = re.sub("[\u3380-\u33df]", lambda unit: unicodedata.normalize(
        "NFKC", unit.group()).replace("\u03bc", "u"), text.replace("\u00b5", "u"))
    plain = unicodedata.normalize(
        "NFKD", unicodedata.normalize("NFKC", text).translate(_LETTERS)).translate(_LETTERS)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", plain.encode("ascii", "ignore").decode()) \
        .strip("-").lower()
    return slug[:60].strip("-") or "shared-study"


def _placed(parent: Path, name: str) -> Path:
    """A folder of ``name`` in ``parent`` none holds, made now (empty) so that
    another open at once takes the next name, not this one."""
    target, n = parent / name, 2
    while True:
        try:
            target.mkdir()
            return target
        except FileExistsError:
            target, n = parent / f"{name}-{n}", n + 1


def _swapped(value: Any, plain: str) -> Any:
    if isinstance(value, str):
        return value.replace(STUDY, plain)
    if isinstance(value, list):
        return [_swapped(v, plain) for v in value]
    if isinstance(value, dict):
        return {_swapped(k, plain): _swapped(v, plain) for k, v in value.items()}
    return value


def _in_yaml(text: str, plain: str) -> str:
    """A YAML file with the folder put back: as text where that reads as the
    same values (its layout kept), else written again from its values, since
    a folder's name can hold what YAML reads as syntax (`: `, ` #`)."""
    import yaml

    try:
        values = yaml.safe_load(text)
    except yaml.YAMLError:
        return text.replace(STUDY, plain)
    wanted = _swapped(values, plain)
    replaced = text.replace(STUDY, plain)
    try:
        if yaml.safe_load(replaced) == wanted:
            return replaced
    except yaml.YAMLError:
        pass
    return yaml.safe_dump(wanted, sort_keys=False, allow_unicode=True)


def _put_back(folder: Path, files: list[str], target: Path) -> list[str]:
    """`__FASTMDX_STUDY__` said as ``target`` in each of ``files``, each in
    its own syntax, its line endings as they were."""
    rewritten = []
    plain = str(target)
    for rel in files:
        path = folder / rel
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        if STUDY not in text:
            continue
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix == ".json":
            text = text.replace(STUDY, json.dumps(plain)[1:-1])
        elif suffix in (".yml", ".yaml"):
            text = _in_yaml(text, plain)
        else:
            text = text.replace(STUDY, plain)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        rewritten.append(rel)
    return rewritten


def _unpacked(opened: zipfile.ZipFile, into: Path, most_bytes: int,
              name: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The packing list and the files it names, unpacked into ``into`` and
    each found to be the file listed; refused at the first that is not."""
    members = _members(opened, most_bytes)
    names = {m.filename for m in members}
    if packing_list.METADATA not in names:
        raise ShareRefused(f"{name} has no {packing_list.METADATA} at its top, so it is not a "
                           "shared FastMDXplora study.", code="environment.share.not_a_study")
    try:
        data = json.loads(opened.read(packing_list.METADATA).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShareRefused(f"Its {packing_list.METADATA} is not JSON.",
                           code="environment.share.not_a_study") from exc
    listed = packing_list.check(data)
    files = names - {packing_list.METADATA}
    extra = sorted(files - set(listed))
    missing = sorted(set(listed) - files)
    if extra or missing:
        which = (f"{extra[0]} is in it and not listed" if extra
                 else f"{missing[0]} is listed and not in it")
        raise ShareRefused(f"The archive is not as its packing list says: {which}; nothing "
                           "was opened.", code="environment.share.unverified",
                           path=(extra or missing)[0])
    for member in members:
        opened.extract(member, into)
    for rel, expected in sorted(listed.items()):
        path = into / rel
        if not path.is_file() or path.stat().st_size != expected["size"] \
                or _sha256(path) != expected["sha256"]:
            raise ShareRefused(
                f"{rel} is not the file the packing list names (its size or SHA-256 differs); "
                "nothing was opened.", code="environment.share.unverified", path=rel)
    return data, listed


def open_shared(source: str, *, into: str | Path | None = None,
                workspace: str | Path = ".", most_bytes: int = MOST_BYTES,
                said: Callable[[str], None] | None = None) -> Path:
    """The study ``source`` names (a DOI, a Zenodo address or an archive
    here), checked and unpacked into ``into`` (else a folder of its own in
    ``workspace``); the folder it is in."""
    given = Path(str(source)).expanduser()
    parent = Path(into).expanduser() if into else Path(workspace).expanduser()
    made = [folder for folder in (parent, *parent.parents) if not folder.exists()]
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if exc.errno not in _NO_ROOM:
            raise
        # Deepest first, each as it was made: empty.
        for folder in made:
            try:
                folder.rmdir()
            except FileNotFoundError:
                continue
            except OSError:
                break
        raise ShareRefused(_no_room(parent, exc), code="environment.share.too_large",
                           path=str(parent)) from exc
    try:
        try:
            return _opened(source, given, parent, most_bytes, said)
        except OSError as exc:
            if exc.errno in _NO_ROOM:
                raise ShareRefused(_no_room(parent, exc), code="environment.share.too_large",
                                   path=str(parent)) from exc
            if exc.errno == errno.EFBIG:
                raise ShareRefused(_too_big(parent, exc), code="environment.share.too_large",
                                   path=str(parent)) from exc
            raise
    except BaseException:
        # Folders made only to open this into are not left behind, empty;
        # one holding anything (put there meanwhile) is kept.
        for folder in made:
            try:
                folder.rmdir()
            except OSError:
                break
        raise


#: What an open works in, beside the folder it opens into, named for this
#: computer and the process opening.
_WORKING = ".fastmdx-opening-"


#: The lock an open holds in its working folder while it runs.
_HELD = ".open.lock"

#: The working folders of this process's opens still running: kept, whatever
#: a lock says (on NFS a lock is the whole process's, so two opens in one
#: process cannot tell each other's apart by it).
_RUNNING: set[str] = set()


def _held(work: Path) -> Any:
    """The working folder ``work`` locked for this open while it runs (the
    lock a later open tests); None where the disk keeps no locks."""
    try:
        import fcntl
    except ImportError:
        return None
    # Locked under a name of its own, then named as the lock: a later open
    # never finds the lock file before it is held.
    taking = work / f"{_HELD}.{os.getpid()}"
    try:
        held = taking.open("w")
    except OSError:
        return None
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.replace(taking, work / _HELD)
    except OSError:
        held.close()
        return None
    return held


def _left_by_the_killed(parent: Path) -> None:
    """What an open on this computer left, killed part way, taken away: one
    whose lock no process holds (one running, in any process space, holds
    it). Another computer's, on a shared disk, is not this one's to judge,
    nor one where locks are not kept."""
    import socket

    try:
        import fcntl
    except ImportError:
        return
    for left in parent.glob(f"{_WORKING}{_host(socket.gethostname())}-*"):
        lock = left / _HELD
        if str(left.resolve()) in _RUNNING or not left.is_dir() or not lock.is_file():
            continue
        try:
            with lock.open("a") as tested:
                fcntl.flock(tested, fcntl.LOCK_EX | fcntl.LOCK_NB)
                shutil.rmtree(left, ignore_errors=True)
        except OSError:
            continue


@contextlib.contextmanager
def _running(work: Path) -> Any:
    """``work`` named among this process's opens still running while it is."""
    named = str(work.resolve())
    _RUNNING.add(named)
    try:
        yield work
    finally:
        _RUNNING.discard(named)


def _host(name: str) -> str:
    """This computer's name in a folder's, with a digest of it whole: two
    computers whose names differ only in their punctuation are told apart."""
    digest = hashlib.sha256(name.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"{re.sub(r'[^A-Za-z0-9.]+', '_', name) or 'host'}.{digest}"


def _opened(source: str, given: Path, parent: Path, most_bytes: int,
            said: Callable[[str], None] | None) -> Path:
    import socket

    record: dict[str, Any] = {}
    _left_by_the_killed(parent)
    with tempfile.TemporaryDirectory(prefix=f"{_WORKING}{_host(socket.gethostname())}-",
                                     dir=parent) as work, \
            contextlib.closing(_held(Path(work)) or io.StringIO()), _running(Path(work)):
        work_dir = Path(work)
        if given.is_file():
            archive = given
            if archive.stat().st_size > most_bytes:
                raise ShareRefused(
                    f"{archive} is over {_gb(most_bytes)}; allow more with "
                    "--open-most-gb.", code="environment.share.too_large")
        else:
            from fastmdxplora.sharing.zenodo import archive_of, record_of

            site, number = record_of(str(source))
            try:
                record = archive_of(site, number, said)
            except ShareRefused as exc:
                if not getattr(exc, "later", False):
                    # No such record, or no one study in it: no later mends it.
                    raise
                said_so = str(exc).rstrip()
                raise ShareRefused(
                    f"{said_so}{'' if said_so.endswith('.') else '.'} Nothing was kept; open "
                    "it again later.", code=exc.refusal.code,
                    url=getattr(exc.refusal, "details", {}).get("url", "")) from exc
            if record["size"] and record["size"] > most_bytes:
                raise ShareRefused(
                    f"Zenodo's record {number} holds {record['name']} of "
                    f"{_gb(record['size'])}, over {_gb(most_bytes)}; allow "
                    "more with --open-most-gb.", code="environment.share.too_large")
            if said:
                said(f"Downloading {record['name']} from Zenodo's record {record['id']}"
                     + (f" ({said_size(record['size'])})" if record["size"] else "") + "...")
            archive = work_dir / "archive.zip"
            _download(record["url"], archive, most=most_bytes, md5=record["md5"], said=said,
                      size=int(record["size"] or 0), room=parent)
        try:
            opened = zipfile.ZipFile(archive)
        except PermissionError as exc:
            raise ShareRefused(f"{archive} could not be read ({exc.strerror}); nothing was "
                               "opened. Make it readable and open it again.",
                               code="environment.share.not_a_study", path=str(archive)) from exc
        except (zipfile.BadZipFile, OSError) as exc:
            raise ShareRefused(f"{archive.name} is not a zip archive: {exc}.",
                               code="environment.share.not_a_study") from exc
        unpacked = work_dir / "study"
        try:
            data, listed = _unpacked(opened, unpacked, most_bytes, archive.name)
        except ShareRefused:
            raise
        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError,
                OSError, ValueError) as exc:
            if isinstance(exc, OSError) and (exc.errno in _NO_ROOM
                                             or exc.errno == errno.EFBIG):
                # The disk, not the archive: said as such where it is caught.
                raise
            # A member damaged, compressed in a way this Python cannot read,
            # or a file named where the archive also has a folder.
            raise ShareRefused(f"{archive.name} could not be unpacked: {exc}; nothing was "
                               "opened.", code="environment.share.unsafe") from exc
        finally:
            opened.close()
        if said:
            said(f"Checked {len(listed)} files against their SHA-256.")
        root = next((e for e in data["@graph"] if isinstance(e, dict) and e.get("@id") == "./"),
                    {})
        # Named for what it is, not for how every shared study's title begins.
        title = re.sub(r"(?i)^a fastmdxplora study of\s+", "",
                       str(root.get("name") or given.stem))
        target = _placed(parent, _slug(title))
        try:
            return _put_in(target, unpacked, root, listed, source, record, given, archive,
                           said)
        except BaseException:
            # The folder made for it, left empty: taken away again.
            with contextlib.suppress(OSError):
                target.rmdir()
            raise


def _put_in(target: Path, unpacked: Path, root: dict[str, Any], listed: Any, source: Any,
            record: dict[str, Any], given: Path, archive: Path,
            said: Callable[[str], None] | None) -> Path:
    """The study unpacked, its paths put back as ``target``'s and moved there."""
    held = root.get("fmx:placeholders")
    holding = next((p for p in (held if isinstance(held, list) else [])
                    if isinstance(p, dict) and p.get("fmx:placeholder") == STUDY), {})
    inside = holding.get("fmx:in")
    named = [str(i.get("@id")) for i in (inside if isinstance(inside, list) else [])
             if isinstance(i, dict)]
    rewritten = _put_back(unpacked, [n for n in named if n in listed], target.resolve())
    shared_from = {
        "source": str(source), "doi": record.get("doi") or str(root.get("identifier") or "")
        .removeprefix("https://doi.org/"),
        "record": record.get("id") or None, "version": record.get("version") or None,
        "archive": record.get("name") or given.name, "archive_sha256": _sha256(archive),
        "files_checked": len(listed), "rewritten": rewritten,
        "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (unpacked / SHARED_FROM).write_text(json.dumps(shared_from, indent=2) + "\n",
                                        encoding="utf-8")
    try:
        # Over the empty folder made for it, at once.
        os.replace(unpacked, target)
    except OSError:
        for inner in unpacked.iterdir():
            shutil.move(str(inner), str(target / inner.name))
        unpacked.rmdir()
    if said:
        said(f"The shared study is in {target}.")
    return target
