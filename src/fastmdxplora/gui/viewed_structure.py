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

from fastmdxplora.gui.protein_preview import find_structure, find_system

__all__ = ["display_structure_bytes", "structure_file", "viewer_structure"]


@lru_cache(maxsize=4)
def _display_structure_cached(path_string: str, _mtime_ns: int, _size: int) -> bytes:
    from fastmdxplora.gui.live_frames import dashboard_display_pdb

    target = Path(path_string)
    text = target.read_text(encoding="utf-8", errors="ignore")
    filtered = dashboard_display_pdb(text)
    # An empty filter result means nothing matched the solute test -- an
    # unusual file rather than a solvent box. Send it as written rather than
    # sending nothing.
    return filtered.encode("utf-8") if filtered.strip() else target.read_bytes()


def display_structure_bytes(target: Path) -> bytes:
    """The structure as the browser renders it: solute only.

    Cached per file version, because a solvated topology is read in full to
    filter it and the viewer asks on every page load.
    """
    try:
        stat = target.stat()
        return _display_structure_cached(
            str(target.resolve()), int(stat.st_mtime_ns), int(stat.st_size)
        )
    except OSError:
        return target.read_bytes()


def structure_file(root: Path) -> Path | None:
    """The structure the Viewer is sent: the system simulated, else the
    structure the study started from."""
    target = find_system(root)
    if target is None or not target.is_file():
        target = find_structure(root)
    return target if target is not None and target.is_file() else None


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
        stat = target.stat()
        if of == "live":
            from fastmdxplora.gui.live_frames import live_frame_text

            text = live_frame_text(target.parent)
            if text is None:
                return None, ("", 0, 0)
            data = text.encode("utf-8")
        else:
            data = (target.read_bytes() if of == "frames" or with_solvent
                    else display_structure_bytes(target))
    except OSError:
        return None, ("", 0, 0)
    key = (f"{target.resolve()}|{of}|{with_solvent}", int(stat.st_mtime_ns), int(stat.st_size))
    return data, key
