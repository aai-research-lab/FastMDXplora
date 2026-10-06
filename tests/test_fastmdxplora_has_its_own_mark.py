"""FastMDXplora has its own mark: the tab's icon and the mark over the folded sidebar.

Both were the AAi Research Lab's logo, a picture of the lab and not of the
software. The mark is a three-atom molecule going right with each atom's
trail behind it, made by scripts/make_mark.py from its atoms, bonds and
trails: in white on a black tile as the tab's icon, which reads on a light
tab bar and a dark one, and written inline in the accent over the folded
sidebar. The lab's logo stays the avatar at the foot of the sidebar
(test_the_lab_mark_and_the_pinned_sidebar.py).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from tests.test_a_hosted_gui_shows_its_service import _foot, _on_ones_own_machine

import fastmdxplora.gui as gui_pkg
from fastmdxplora.gui.server import LAB_LOGO, PRODUCT_MARK
from fastmdxplora.gui.sidebar_icons import ICONS, icon

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(gui_pkg.__file__).with_name("static")
MARK_FILE = STATIC / PRODUCT_MARK


def _the_script():
    """scripts/make_mark.py, where the mark is made."""
    found = importlib.util.spec_from_file_location("make_mark", ROOT / "scripts" / "make_mark.py")
    script = importlib.util.module_from_spec(found)
    found.loader.exec_module(script)
    return script


def test_what_ships_is_what_the_script_makes() -> None:
    script = _the_script()
    assert MARK_FILE.read_text(encoding="utf-8") == script.icon_file()
    assert ICONS["mark"] == script.shapes()
    assert script.main(["--check"]) == 0


def test_the_icon_is_white_on_a_black_tile_around_the_whole_mark() -> None:
    script = _the_script()
    icon = MARK_FILE.read_text(encoding="utf-8")
    assert 'fill="#000000"' in icon and 'stroke="#ffffff"' in icon
    assert "prefers-color-scheme" not in icon
    x, y, side = script.tile()
    half = script.ICON_STROKE / 2
    margin = side * script.TILE_MARGIN
    for (cx, cy), r in script.ATOMS.values():
        assert x + margin - 1e-9 <= cx - r - half and cx + r + half <= x + side - margin + 1e-9
        assert y + margin - 1e-9 <= cy - r - half and cy + r + half <= y + side - margin + 1e-9
    for _, start in script.TRAILS:
        assert x + margin - 1e-9 <= start - half


def test_the_mark_holds_together() -> None:
    script = _the_script()
    atoms = list(script.ATOMS.values())
    # No two atoms touch.
    for i, ((x1, y1), r1) in enumerate(atoms):
        for (x2, y2), r2 in atoms[i + 1:]:
            assert ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 > r1 + r2 + 1
    # Each trail runs left to right and stops short of its atom.
    for name, start in script.TRAILS:
        (x, _), r = script.ATOMS[name]
        assert 0 < start < x - r - script.TRAIL_GAP
    # Inside the sidebar's 24-unit grid, the stroke's half-width included.
    for (x, y), r in atoms:
        assert 1 <= x - r and x + r <= 23 and 1 <= y - r and y + r <= 23


def test_on_ones_own_machine_it_is_the_tab_and_the_strip_and_not_the_avatar(tmp_path) -> None:
    page = _on_ones_own_machine(tmp_path)
    head = page[:page.index("</head>")]
    assert f'<link rel="icon" type="image/svg+xml" href="/static/{PRODUCT_MARK}">' in head
    strip = page[page.index('id="sidebar-expand"'):page.index('<aside class="sidebar"')]
    assert icon("mark", "strip-mark") in strip
    foot = _foot(page)
    assert ICONS["mark"] not in foot
    assert f'<img class="sidebar-account-logo" src="/static/{LAB_LOGO}" alt="">' in foot


def test_the_icon_is_served_as_svg(tmp_path) -> None:
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(session.url.rstrip("/") + f"/static/{PRODUCT_MARK}",
                                    timeout=30) as response:
            said = response.headers["Content-Type"], response.read()
    finally:
        session.server.shutdown()
    assert said == ("image/svg+xml", MARK_FILE.read_bytes())


def test_the_standalone_dashboard_carries_it_as_its_tab(tmp_path) -> None:
    from fastmdxplora.gui.report_dashboard import build_dashboard

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    html = (tmp_path / "report" / "dashboard.html").read_text(encoding="utf-8")
    inline = "data:image/svg+xml;base64," + base64.b64encode(MARK_FILE.read_bytes()).decode("ascii")
    assert f'<link rel="icon" type="image/svg+xml" href="{inline}">' in html
