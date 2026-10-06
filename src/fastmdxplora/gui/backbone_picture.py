"""A picture of a study's protein for its card on All studies, where the
study has plotted no figure yet.

The backbone as its Cα trace (a nucleic acid's by its phosphorus atoms),
read from the structure the Viewer would render first, seen down the
axis along which the molecule is thinnest so its widest face is shown,
coloured from the N terminus to the C terminus (viridis, its lightest
fifth left out to read on white) and shaded by depth, the nearer part
darker and wider. The trace is broken at a chain's end and wherever two
consecutive atoms are farther apart than a bond allows (a missing loop).
Written as SVG from the PDB's own lines, in milliseconds and with no
browser, and kept in memory by the file's size and time, never in the
study's folder.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

__all__ = ["backbone_svg", "structure_for_picture"]

WIDTH, HEIGHT, MARGIN = 480, 200, 14
#: Consecutive Cα atoms are 3.8 Å apart; phosphorus atoms about 6 to 7.
_GAP = {"CA": 4.3, "P": 8.0}
#: Above this many atoms the trace takes every k-th, so the picture stays
#: small to send.
MOST_POINTS = 1500
#: Files larger than this are not read for a card.
MOST_BYTES = 200 * 1024 * 1024
#: Viridis.
_STOPS = ((0.0, (0x44, 0x01, 0x54)), (0.2, (0x41, 0x44, 0x87)), (0.4, (0x2A, 0x78, 0x8E)),
          (0.6, (0x22, 0xA8, 0x84)), (0.8, (0x7A, 0xD1, 0x51)), (1.0, (0xFD, 0xE7, 0x25)))
#: Each scheme's part of viridis, its background and the colour of N and C:
#: on paper its lightest fifth is left out, to read on white; on the dark
#: scheme its darkest, to read on the page, which shows through.
SCHEMES = {"light": ((0.0, 0.8), "#ffffff", "#444444"),
           "dark": ((0.2, 1.0), "none", "#c8c8c8")}
#: Pictures kept in memory, the oldest let go first.
MOST_KEPT = 256
_KEPT: OrderedDict[tuple[str, int, int, str], str | None] = OrderedDict()
_LOCK = threading.Lock()


def structure_for_picture(folder: str | Path) -> Path | None:
    """The structure a card's picture is made from: the study's own, as the
    Viewer would find it, else that of the first of its runs with one."""
    from fastmdxplora.gui.protein_preview import find_structure

    base = Path(folder)
    found = find_structure(base)
    if found is not None and found.suffix.lower() == ".pdb":
        return found
    runs = base / "runs"
    for run in sorted(runs.iterdir()) if runs.is_dir() else []:
        found = find_structure(run) if run.is_dir() else None
        if found is not None and found.suffix.lower() == ".pdb":
            return found
    return None


def _pieces(path: Path) -> tuple[list[np.ndarray], str]:
    """The backbone's atoms as pieces of consecutive residues, in Å."""
    atoms: dict[str, list[tuple[str, str, tuple[float, float, float]]]] = {"CA": [], "P": []}
    seen: set[tuple[str, str, str]] = set()
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if line.startswith("ENDMDL"):
                break
            if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
                continue
            name = line[12:16]
            # " CA " is an alpha carbon; a calcium ion is "CA  ".
            kind = "CA" if name == " CA " else "P" if name.strip() == "P" else None
            if kind is None or line[16] not in " A":
                continue
            residue = (kind, line[21], line[22:27])
            if residue in seen:
                continue
            seen.add(residue)
            try:
                xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            except ValueError:
                continue
            atoms[kind].append((line[21], line[22:27], xyz))
    kind = "CA" if len(atoms["CA"]) >= 3 else "P"
    chosen = atoms[kind]
    pieces: list[list[tuple[float, float, float]]] = []
    for k, (chain, _, xyz) in enumerate(chosen):
        joined = k > 0 and chosen[k - 1][0] == chain and float(np.linalg.norm(
            np.subtract(xyz, chosen[k - 1][2]))) <= _GAP[kind]
        if joined:
            pieces[-1].append(xyz)
        else:
            pieces.append([xyz])
    return [np.array(piece) for piece in pieces], kind


def _colour(t: float, scheme: str = "light") -> str:
    low_end, high_end = SCHEMES[scheme][0]
    t = low_end + min(max(t, 0.0), 1.0) * (high_end - low_end)
    (a, low), (b, high) = next(pair for pair in zip(_STOPS, _STOPS[1:]) if t <= pair[1][0])
    f = (t - a) / (b - a)
    return "#%02x%02x%02x" % tuple(round(lo + f * (hi - lo)) for lo, hi in zip(low, high))


def _rendered(path: Path, scheme: str = "light") -> str | None:
    pieces, kind = _pieces(path)
    count = sum(len(piece) for piece in pieces)
    if count < 3:
        return None
    step = max(1, -(-count // MOST_POINTS))
    pieces = [piece[::step] if len(piece) > step else piece for piece in pieces]
    every = np.concatenate(pieces)
    centre = every.mean(axis=0)
    _, _, axes = np.linalg.svd(every - centre, full_matrices=False)
    across, up = axes[0], axes[1]
    # The N terminus on the left, and the depth axis right-handed, so the
    # nearer part is the one facing the reader.
    if (every[0] - centre) @ across > (every[-1] - centre) @ across:
        across = -across
    toward = np.cross(across, up)
    flat = [np.stack([(piece - centre) @ across, -((piece - centre) @ up),
                      (piece - centre) @ toward], axis=1) for piece in pieces]
    allflat = np.concatenate(flat)
    low, high = allflat.min(axis=0), allflat.max(axis=0)
    span = np.maximum(high - low, 1e-6)
    scale = min((WIDTH - 2 * MARGIN) / span[0], (HEIGHT - 2 * MARGIN) / span[1])
    offset = np.array([(WIDTH - scale * span[0]) / 2, (HEIGHT - scale * span[1]) / 2])
    width = 3.0 if count < 150 else 2.2 if count < 600 else 1.4
    segments = []
    done = 0
    total = max(len(allflat) - 1, 1)
    for piece in flat:
        xy = (piece[:, :2] - low[:2]) * scale + offset
        near = (piece[:, 2] - low[2]) / span[2]
        for k in range(len(piece) - 1):
            depth = float((near[k] + near[k + 1]) / 2)
            segments.append((depth, (*xy[k], *xy[k + 1]), (done + k + 0.5) / total))
        done += len(piece)
    segments.sort(key=lambda kept: kept[0])
    title = (f"The backbone of {count:,} residues ({'Cα' if kind == 'CA' else 'phosphorus'} "
             "trace), coloured from the N terminus (purple) to the C terminus "
             f"({'green' if scheme == 'light' else 'yellow'}), the nearer part "
             f"{'darker' if scheme == 'light' else 'brighter'}")
    _, background, ink = SCHEMES[scheme]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
           f'width="{WIDTH}" height="{HEIGHT}" role="img"><title>{escape(title)}</title>',
           f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{background}"/>',
           '<g fill="none" stroke-linecap="round">']
    for depth, (x1, y1, x2, y2), t in segments:
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="{_colour(t, scheme)}" stroke-opacity="{0.35 + 0.65 * depth:.2f}" '
                   f'stroke-width="{width * (0.7 + 0.5 * depth):.2f}"/>')
    out.append("</g>")
    first = (flat[0][0, :2] - low[:2]) * scale + offset
    last = (flat[-1][-1, :2] - low[:2]) * scale + offset
    for (x, y), label in ((first, "N"), (last, "C")):
        out.append(f'<text x="{min(max(x, 8), WIDTH - 8):.1f}" '
                   f'y="{min(max(y - 6, 11), HEIGHT - 3):.1f}" font-family="sans-serif" '
                   f'font-size="11" text-anchor="middle" fill="{ink}">{label}</text>')
    out.append("</svg>")
    return "".join(out)


def backbone_svg(folder: str | Path, scheme: str = "light") -> str | None:
    """The card's picture of the study's backbone as SVG, in the scheme
    given, or None where the study has no structure with a backbone to show."""
    scheme = scheme if scheme in SCHEMES else "light"
    path = structure_for_picture(folder)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > MOST_BYTES:
        return None
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, scheme)
    with _LOCK:
        if key in _KEPT:
            _KEPT.move_to_end(key)
            return _KEPT[key]
    try:
        picture = _rendered(path, scheme)
    except (OSError, ValueError, np.linalg.LinAlgError):
        picture = None
    with _LOCK:
        _KEPT[key] = picture
        while len(_KEPT) > MOST_KEPT:
            _KEPT.popitem(last=False)
    return picture
