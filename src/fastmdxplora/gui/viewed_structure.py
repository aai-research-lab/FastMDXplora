"""The structures the Viewer is sent, as it is sent them.

The structure (the system simulated, else the one the study started from)
is sent without its bulk solvent unless the solvent is asked for; the live
frame and the frames' topology as written. Atoms are named by their place
in these files, so whatever reads atoms for the Viewer (a typed selection,
a scene) reads these same bytes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastmdxplora import record_access as _records
from fastmdxplora.gui.protein_preview import find_structure, find_system

__all__ = ["display_structure_bytes", "structure_file", "viewer_structure"]


@lru_cache(maxsize=4)
def _display_structure_cached(path_string: str, _mtime_ns: int, _size: int) -> bytes:
    from fastmdxplora.gui.live_frames import dashboard_display_pdb

    # Through `record_access`, in the held read of whoever asked.
    target = Path(path_string)
    data = _records.read_bytes(target)
    filtered = dashboard_display_pdb(data.decode("utf-8", errors="ignore"))
    # An empty filter result means nothing matched the solute test -- an
    # unusual file rather than a solvent box. Send it as written rather than
    # sending nothing.
    return filtered.encode("utf-8") if filtered.strip() else data


def display_structure_bytes(target: Path) -> bytes:
    """The structure as the browser renders it: solute only.

    Cached per file version, because a solvated topology is read in full to
    filter it and the viewer asks on every page load.
    """
    try:
        stat = _records.stat(target)
        return _display_structure_cached(
            str(_records.resolved(target)), int(stat.st_mtime_ns), int(stat.st_size)
        )
    except OSError:
        return _records.read_bytes(target)


def structure_file(root: Path) -> Path | None:
    """The structure the Viewer is sent: the system simulated, else the
    structure the study started from. Through `record_access`: in a read
    held to the study, one linked out of it, or named outside it, is
    refused and named, and no other is sent in its place."""
    with _records.refusals_noted() as refused:
        target = find_system(root)
        if target is None and not refused:
            target = find_structure(root)
    return target if target is not None and not refused else None


def viewer_structure(root: Path, of: str, *, with_solvent: bool
                     ) -> tuple[bytes | None, tuple[str, int, int]]:
    """A structure the Viewer renders, as it was sent, and what names that
    version of it: ``of`` is "frames" (the frames' topology), "live" (the
    live frame) or the structure, with its solvent or without."""
    root = Path(root)
    if of == "frames":
        target: Path | None = root / "simulation" / "frames_topology.pdb"
    elif of == "live":
        target = root / "simulation" / "live_frame.pdb"
    else:
        target = structure_file(root)
    if target is None:
        return None, ("", 0, 0)
    try:
        # Through `record_access`: held to the study where the read is.
        stat = _records.stat(target)
        if of == "live":
            from fastmdxplora.gui.live_frames import live_frame_text

            text = live_frame_text(target.parent)
            if text is None:
                return None, ("", 0, 0)
            data = text.encode("utf-8")
        else:
            data = (_records.read_bytes(target) if of == "frames" or with_solvent
                    else display_structure_bytes(target))
    except OSError:
        return None, ("", 0, 0)
    key = (f"{_records.resolved(target)}|{of}|{with_solvent}", int(stat.st_mtime_ns),
           int(stat.st_size))
    return data, key
