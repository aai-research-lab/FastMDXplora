"""The demo study: trypsin with benzamidine (3PTB), finished, to open first.

A first look at FastMDXplora needed a study run first: an hour on a GPU
before there was anything to look at. The demo is a finished study:
prepared, run for 10 ns on a GPU, analysed and reported, its production
kept as 100 frames without water. It is made from ``3ptb.yml`` by
``scripts/make_demo.py``, which records the release and the run that made
it in ``demo.json``.

It is not in the package: packaged, it is about 16 MB (11 MB compressed), on
every installation, for a study most people open once. It is fetched the
first time it is opened, from where ``3ptb.source.json`` says, checked
against the SHA-256 recorded there, and kept in the cache
(``~/.cache/fastmdxplora/demo``, or under ``FASTMDXPLORA_CACHE_DIR``). A
checkout that has packaged one beside this file (``3ptb/``) opens that.

It is never opened where it is kept: :func:`copy_demo` copies it into a
folder the person chooses, and the paths its records hold are made that
folder's, so the Viewer plays its frames and the analyses link to them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.refusals import CodedError

__all__ = ["DEMO_NAME", "CONFIG", "packaged", "record_of", "copy_demo", "DemoMissing",
           "source", "cached", "offered", "fetch_demo"]

#: The folder a copy of the demo is given.
DEMO_NAME = "3ptb-trypsin-benzamidine"
#: The study the demo is made from.
CONFIG = Path(__file__).with_name("3ptb.yml")
_PACKAGED = Path(__file__).with_name("3ptb")
#: Where the demo is fetched from, and what it must hash to.
_SOURCE = Path(__file__).with_name("3ptb.source.json")
#: What the demo's record is called, in the packaged folder and the copy.
RECORD = "demo.json"
#: The most a fetch reads: the demo is about 11 MB, and an answer much
#: larger is not it.
MOST_BYTES = 64 * 1024 * 1024
FETCH_TIMEOUT_S = 60


class DemoMissing(CodedError, RuntimeError):
    """The demo study cannot be had here: none published yet, or none
    fetched and none can be."""

    default_code = "environment.demo.absent"


def packaged() -> Path | None:
    """The demo study beside this file, where a checkout has packaged one,
    or None."""
    return _PACKAGED if (_PACKAGED / RECORD).is_file() else None


def source() -> dict[str, Any]:
    """Where the demo is fetched from (``url``, ``FASTMDXPLORA_DEMO_URL``
    for a mirror) and what it must hash to (``sha256``) and weigh
    (``bytes``); an empty URL or hash until one is published."""
    try:
        found = json.loads(_SOURCE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    found = found if isinstance(found, dict) else {}
    url = os.environ.get("FASTMDXPLORA_DEMO_URL") or str(found.get("url") or "")
    return {"url": url, "sha256": str(found.get("sha256") or "").lower(),
            "bytes": int(found.get("bytes") or 0)}


def _cache() -> Path:
    override = os.environ.get("FASTMDXPLORA_CACHE_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "fastmdxplora"
    return base / "demo"


def cached() -> Path | None:
    """The demo as fetched before, for the published one, or None."""
    digest = source()["sha256"]
    if not digest:
        return None
    folder = _cache() / digest[:16]
    return folder if (folder / RECORD).is_file() else None


def offered() -> bool:
    """Whether the demo can be opened here: packaged, fetched, or
    published to fetch."""
    published = source()
    return bool(packaged() or cached() or (published["url"] and published["sha256"]))


def fetch_demo(*, said: Callable[[str], None] | None = None) -> Path:
    """The published demo, fetched once, checked against its SHA-256 and
    kept in the cache; the folder it is in."""
    have = cached()
    if have is not None:
        return have
    published = source()
    if not (published["url"] and published["sha256"]):
        raise DemoMissing(
            "The demo study has not been published yet; until it is, start a study "
            "from the Agent or the Config Builder.")
    if said:
        megabytes = published["bytes"] / 1e6
        said(f"Fetching the demo study ({megabytes:.0f} MB) from {published['url']}"
             if megabytes else f"Fetching the demo study from {published['url']}")
    cache = _cache()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as work:
        archive = Path(work) / "demo.zip"
        digest = _download(published["url"], archive)
        if digest != published["sha256"]:
            raise DemoMissing(
                f"What {published['url']} sent is not the demo study this release "
                f"expects (SHA-256 {digest[:12]}..., not {published['sha256'][:12]}...); "
                "nothing was kept.", code="environment.demo.unverified")
        unpacked = Path(work) / "unpacked"
        _unpack(archive, unpacked)
        found = unpacked if (unpacked / RECORD).is_file() else next(
            (p for p in unpacked.iterdir() if (p / RECORD).is_file()), None)
        if found is None:
            raise DemoMissing("The archive fetched holds no demo study.",
                              code="environment.demo.unverified")
        target = cache / published["sha256"][:16]
        if target.exists():
            shutil.rmtree(target)
        # Moved whole into place, so a fetch cut short leaves nothing that
        # reads as the demo.
        found.rename(target)
    return target


def _download(url: str, to: Path) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"})
    hashed = hashlib.sha256()
    read = 0
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as answer, \
                to.open("wb") as out:
            while True:
                piece = answer.read(1 << 20)
                if not piece:
                    break
                read += len(piece)
                if read > MOST_BYTES:
                    raise DemoMissing(f"{url} sent more than the demo study weighs; "
                                      "nothing was kept.", code="environment.demo.unverified")
                hashed.update(piece)
                out.write(piece)
    except (urllib.error.URLError, OSError) as exc:
        raise DemoMissing(
            f"The demo study could not be fetched from {url}: {exc}. It is fetched once, "
            "the first time it is opened, and needs the internet then.",
            code="environment.service.unreachable", url=url) from exc
    return hashed.hexdigest()


def _unpack(archive: Path, into: Path) -> None:
    """Every member inside ``into``: none absolute, none climbing out."""
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    with zipfile.ZipFile(archive) as opened:
        for member in opened.infolist():
            target = (into / member.filename).resolve()
            if target != root and root not in target.parents:
                raise DemoMissing(f"The archive fetched names a path outside itself "
                                  f"({member.filename}); nothing was kept.",
                                  code="environment.demo.unverified")
        opened.extractall(into)


def record_of(folder: Path) -> dict[str, Any]:
    try:
        found = json.loads((Path(folder) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def copy_demo(into: str | Path, *, source: str | Path | None = None) -> Path:
    """The demo study copied into the folder ``into`` (made if need be), as
    ``3ptb-trypsin-benzamidine``, or with ``-2``, ``-3`` after it where one
    is there already; never over anything. Returns the copy.

    The paths the study's records hold to its own files are made the
    copy's, so its frames are the ones played and analysed."""
    origin = Path(source) if source is not None else (packaged() or fetch_demo())
    if not (origin / RECORD).is_file():
        raise DemoMissing(f"{origin} holds no demo study.")
    parent = Path(into).expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / DEMO_NAME
    n = 2
    while target.exists():
        target = parent / f"{DEMO_NAME}-{n}"
        n += 1
    shutil.copytree(origin, target)
    made_in = str(record_of(origin).get("made_in") or "")
    if made_in:
        _moved(target, made_in)
    return target


#: Records that keep where the demo was made, as it was: its own record.
_AS_MADE = {RECORD}


def _moved(study: Path, made_in: str) -> None:
    """Every path the study's JSON records name inside the folder it was
    made in, named inside the copy."""
    here = str(study.resolve())
    for path in sorted(study.rglob("*.json")):
        if path.relative_to(study).as_posix() in _AS_MADE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if made_in not in text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        path.write_text(json.dumps(_rewritten(data, made_in, here), indent=2), encoding="utf-8")


def _rewritten(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        if value == old or value.startswith(old.rstrip("/") + "/"):
            return new + value[len(old.rstrip("/")):]
        return value
    if isinstance(value, list):
        return [_rewritten(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rewritten(v, old, new) for k, v in value.items()}
    return value
