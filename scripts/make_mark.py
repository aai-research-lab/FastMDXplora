#!/usr/bin/env python
"""Make FastMDXplora's mark: a three-atom molecule going right, each atom
leaving its trail, as a trajectory is the path of each atom in time.

The mark is set down here as atoms (centre and radius), the bonds between
them and where each atom's trail begins. The bonds run from one atom's edge
to the other's, and each trail stops a little short of its atom, so moving
or resizing an atom is a change to one line here. It is set on the
24-unit grid of the sidebar's icons, stroked and never filled.

Usage
-----
    python scripts/make_mark.py                      # write the tab's icon
    python scripts/make_mark.py --check              # is what ships what is made here?
    python scripts/make_mark.py --preview mark.html  # the mark at each size, both schemes

Writing the icon gives ``src/fastmdxplora/gui/static/fastmdx-mark.svg``, the
mark in white on a black tile. The same shapes, stroked in the text's own
colour (the page sets it in the accent over the folded sidebar), is
``ICONS["mark"]`` in ``src/fastmdxplora/gui/sidebar_icons.py``; a change
here is copied there by hand (the script prints it), and ``--check``, run
by the tests, says when the two differ.
"""

from __future__ import annotations

import argparse
import base64
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_FILE = ROOT / "src" / "fastmdxplora" / "gui" / "static" / "fastmdx-mark.svg"

#: Each atom: its centre and radius, the leading one first and largest.
ATOMS = {
    "lead": ((18.0, 10.5), 2.8),
    "upper": ((12.5, 5.0), 2.0),
    "lower": ((11.0, 16.5), 2.0),
}
#: The bonds, each from the first atom's edge to the second's.
BONDS = (("upper", "lead"), ("lower", "lead"))
#: Where each atom's trail begins on the left, in the order they are written.
TRAILS = (("upper", 2.0), ("lower", 2.0), ("lead", 5.5))
#: The space left between the end of a trail and its atom's edge.
TRAIL_GAP = 2.4

#: The accents of the light and dark schemes (theme.css, --accent-text), in
#: in which the page shows the mark over the folded sidebar.
LIGHT_ACCENT = "#1b7590"
DARK_ACCENT = "#33a6c8"
#: The tab's icon: the mark in white on a black tile with rounded corners,
#: which reads on a light tab bar and a dark one alike. It is shown at
#: 16 px, a little heavier than the sidebar's 1.7.
ICON_INK = "#ffffff"
ICON_TILE = "#000000"
ICON_STROKE = 2
#: The space between the mark and the tile's edge, and the tile's corner,
#: as fractions of the tile.
TILE_MARGIN = 0.12
TILE_CORNER = 0.22


def _n(value: float) -> str:
    """A coordinate to two places, without trailing zeros."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def shapes() -> str:
    """The mark's circles and paths, as the inside of a 24-unit SVG."""
    circles = "".join(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}"/>'
                      for (x, y), r in ATOMS.values())
    bonds = []
    for one, other in BONDS:
        (x1, y1), r1 = ATOMS[one]
        (x2, y2), r2 = ATOMS[other]
        length = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        bonds.append(f"M{_n(x1 + ux * r1)} {_n(y1 + uy * r1)}"
                     f"L{_n(x2 - ux * r2)} {_n(y2 - uy * r2)}")
    trails = []
    for name, start in TRAILS:
        (x, y), r = ATOMS[name]
        trails.append(f"M{_n(start)} {_n(y)}h{_n(x - r - TRAIL_GAP - start)}")
    return circles + f'<path d="{"".join(bonds)}"/>' + f'<path d="{"".join(trails)}"/>'


def tile() -> tuple[float, float, float]:
    """The square the tab's icon is set in, as its corner and side in the
    mark's units: the mark's extent, stroke included, centred with
    `TILE_MARGIN` around it."""
    half = ICON_STROKE / 2
    left = min([start for _, start in TRAILS] + [x - r for (x, _), r in ATOMS.values()]) - half
    right = max(x + r for (x, _), r in ATOMS.values()) + half
    top = min(y - r for (_, y), r in ATOMS.values()) - half
    bottom = max(y + r for (_, y), r in ATOMS.values()) + half
    side = max(right - left, bottom - top) / (1 - 2 * TILE_MARGIN)
    return (left + right - side) / 2, (top + bottom - side) / 2, side


def icon_file() -> str:
    """The tab's icon: the mark in white on a black tile."""
    x, y, side = tile()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_n(x)} {_n(y)} {_n(side)} {_n(side)}">'
            f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(side)}" height="{_n(side)}" '
            f'rx="{_n(side * TILE_CORNER)}" fill="{ICON_TILE}"/>'
            f'<g fill="none" stroke="{ICON_INK}" stroke-width="{ICON_STROKE}" '
            f'stroke-linecap="round" stroke-linejoin="round">{shapes()}</g></svg>\n')


def preview() -> str:
    """A page of the mark at 16, 24, 48 and 96 px, in either scheme's text and
    in the accent, beside the product's name, and as the tab's icon."""
    sizes = "".join(f'<svg width="{s}" height="{s}" viewBox="0 0 24 24">{shapes()}</svg>'
                    for s in (16, 24, 48, 96))
    accent = f'<svg class="accent" width="48" height="48" viewBox="0 0 24 24">{shapes()}</svg>'
    named = (f'<span class="name"><svg class="accent" width="20" height="20" '
             f'viewBox="0 0 24 24">{shapes()}</svg>FastMDXplora</span>')
    # As the tab shows it: the icon file itself, at 16 and 32 px.
    encoded = base64.b64encode(icon_file().encode("utf-8")).decode("ascii")
    tab = "".join(f'<img src="data:image/svg+xml;base64,{encoded}" width="{s}" height="{s}" '
                  'alt="" title="the tab\'s icon">' for s in (16, 32))
    rows = "".join(f'<div class="row {scheme}">{sizes}{accent}{named}{tab}</div>'
                   for scheme in ("light", "dark"))
    return f"""<!doctype html><meta charset="utf-8"><title>FastMDXplora mark</title>
<style>
body{{font:14px system-ui;margin:24px;display:grid;gap:14px}}
.row{{display:flex;gap:28px;align-items:center;padding:12px 16px;border-radius:12px}}
.light{{background:#faf9f5;color:#141413;--accent:{LIGHT_ACCENT}}}
.dark{{background:#1c1c1d;color:#ececee;--accent:{DARK_ACCENT}}}
svg{{fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}}
.accent{{color:var(--accent)}}.name{{display:flex;gap:8px;align-items:center;font-weight:600}}
</style>
{rows}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the shipped icon or ICONS['mark'] differ from the shapes made here")
    parser.add_argument("--preview", metavar="FILE",
                        help="write a page showing the mark at each size, in both schemes")
    args = parser.parse_args(argv)

    if args.check:
        sys.path.insert(0, str(ROOT / "src"))
        from fastmdxplora.gui.sidebar_icons import ICONS

        stale = []
        if not ICON_FILE.is_file() or ICON_FILE.read_text(encoding="utf-8") != icon_file():
            stale.append(str(ICON_FILE.relative_to(ROOT)))
        if ICONS.get("mark") != shapes():
            stale.append('ICONS["mark"] in src/fastmdxplora/gui/sidebar_icons.py')
        for where in stale:
            print(f"differs from the shapes made here: {where}", file=sys.stderr)
        return 1 if stale else 0

    if args.preview:
        Path(args.preview).write_text(preview(), encoding="utf-8")
        print(f"wrote {args.preview}")
        return 0

    ICON_FILE.write_text(icon_file(), encoding="utf-8")
    print(f"wrote {ICON_FILE.relative_to(ROOT)}")
    print('ICONS["mark"] in src/fastmdxplora/gui/sidebar_icons.py is:')
    print(shapes())
    return 0


if __name__ == "__main__":
    sys.exit(main())
