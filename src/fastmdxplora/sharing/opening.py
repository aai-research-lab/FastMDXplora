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

import hashlib
import json
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastmdxplora.sharing import STUDY, ShareRefused
from fastmdxplora.sharing import crate as packing_list
from fastmdxplora.sharing.pack import never_shared

__all__ = ["MOST_BYTES", "open_shared", "unpacked_limit"]

#: The largest archive downloaded or opened unless more is allowed.
MOST_BYTES = 2 * 1024 ** 3
#: The most files an archive may hold.
MOST_FILES = 200_000
SHARED_FROM = "shared_from.json"
_AGENT = "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"


def unpacked_limit(most_bytes: int) -> int:
    """How much an archive may unpack to: compressed text unpacks to several
    times its size, and a crafted archive to thousands."""
    return 4 * most_bytes


def _download(url: str, to: Path, *, most: int, md5: str,
              said: Callable[[str], None] | None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    digest = hashlib.md5()  # noqa: S324 - Zenodo's own checksum, not a protection
    read = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as answer, to.open("wb") as out:
            for piece in iter(lambda: answer.read(1 << 20), b""):
                read += len(piece)
                if read > most:
                    raise ShareRefused(
                        f"{url} sent more than {most / 1e9:.1f} GB; nothing was kept. "
                        "Allow more with --open-most-gb.",
                        code="environment.share.too_large", url=url)
                digest.update(piece)
                out.write(piece)
    except (urllib.error.URLError, OSError) as exc:
        raise ShareRefused(f"The shared study could not be downloaded from {url}: {exc}.",
                           code="environment.service.unreachable", url=url) from exc
    if md5 and digest.hexdigest() != md5:
        raise ShareRefused(
            f"What {url} sent is not the file Zenodo recorded (MD5 {digest.hexdigest()}, "
            f"not {md5}); nothing was kept.", code="environment.share.unverified", url=url)
    if said:
        said(f"Downloaded {read / 1e6:.1f} MB.")


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
                f"The archive unpacks to more than {unpacked_limit(most_bytes) / 1e9:.1f} GB; "
                "nothing was opened.", code="environment.share.unsafe")
        members.append(info)
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:60].strip("-") or "shared-study"


def _placed(parent: Path, name: str) -> Path:
    target, n = parent / name, 2
    while target.exists():
        target, n = parent / f"{name}-{n}", n + 1
    return target


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
    record: dict[str, Any] = {}
    parent = Path(into).expanduser() if into else Path(workspace).expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fastmdx-opening-", dir=parent) as work:
        work_dir = Path(work)
        if given.is_file():
            archive = given
            if archive.stat().st_size > most_bytes:
                raise ShareRefused(
                    f"{archive} is over {most_bytes / 1e9:.1f} GB; allow more with "
                    "--open-most-gb.", code="environment.share.too_large")
        else:
            from fastmdxplora.sharing.zenodo import archive_of, record_of

            site, number = record_of(str(source))
            record = archive_of(site, number)
            if record["size"] and record["size"] > most_bytes:
                raise ShareRefused(
                    f"Zenodo's record {number} holds {record['name']} of "
                    f"{record['size'] / 1e9:.1f} GB, over {most_bytes / 1e9:.1f} GB; allow "
                    "more with --open-most-gb.", code="environment.share.too_large")
            if said:
                said(f"Downloading {record['name']} from Zenodo's record {record['id']}"
                     + (f" ({record['size'] / 1e6:.1f} MB)" if record["size"] else "") + "...")
            archive = work_dir / "archive.zip"
            _download(record["url"], archive, most=most_bytes, md5=record["md5"], said=said)
        try:
            opened = zipfile.ZipFile(archive)
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
        target = _placed(parent, _slug(str(root.get("name") or given.stem)))
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
        shutil.move(str(unpacked), str(target))
    if said:
        said(f"The shared study is in {target}.")
    return target
