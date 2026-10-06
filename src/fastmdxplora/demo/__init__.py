"""The demo study: trypsin with benzamidine (3PTB), finished, to open first.

A first look at FastMDXplora needed a study run first: an hour on a GPU
before there was anything to look at. The demo is a finished study shipped
with the package (``3ptb/``): prepared, run for 10 ns on a GPU, analysed and
reported, its production kept as 100 frames without water. It is made from
``3ptb.yml`` by ``scripts/make_demo.py``, which records the release and the
run that made it in ``3ptb/demo.json``.

It is never opened where it is installed: :func:`copy_demo` copies it into a
folder the person chooses, and the paths its records hold are made that
folder's, so the Viewer plays its frames and the analyses link to them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import CodedError

__all__ = ["DEMO_NAME", "CONFIG", "packaged", "record_of", "copy_demo", "DemoMissing"]

#: The folder a copy of the demo is given.
DEMO_NAME = "3ptb-trypsin-benzamidine"
#: The study the demo is made from.
CONFIG = Path(__file__).with_name("3ptb.yml")
_PACKAGED = Path(__file__).with_name("3ptb")
#: What the demo's record is called, in the packaged folder and the copy.
RECORD = "demo.json"


class DemoMissing(CodedError, RuntimeError):
    """This installation carries no demo study."""

    default_code = "environment.demo.absent"


def packaged() -> Path | None:
    """The demo study as installed, or None where this installation has
    none (it is made on a GPU and added at a release)."""
    return _PACKAGED if (_PACKAGED / RECORD).is_file() else None


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
    origin = Path(source) if source is not None else packaged()
    if origin is None or not (origin / RECORD).is_file():
        raise DemoMissing(
            "This installation carries no demo study. It is added at a release; "
            "until then, start a study from the Agent or the Config page.")
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
