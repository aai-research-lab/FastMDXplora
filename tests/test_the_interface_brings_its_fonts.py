"""The interface brings its two typefaces, and keeps the fixed-width one for
numbers.

The stylesheet named Inter and JetBrains Mono first in its stacks and
shipped neither, so the page read in whatever the machine had: Helvetica
or Arial for the text, Menlo or Courier for numbers, and on a cluster's
login node something else again. And the fixed-width face set the Log's
prose, the labels over the Overview's figures and the settings menu's
words as well as the numbers, paths and code it is for.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "fastmdxplora" / "gui" / "static"
FONTS = ("inter-latin-wght-normal.woff2", "inter-greek-wght-normal.woff2",
         "jetbrains-mono-latin-wght-normal.woff2", "jetbrains-mono-greek-wght-normal.woff2")
LICENCES = ("OFL-Inter.txt", "OFL-JetBrainsMono.txt")


class TestTheFiles:

    def test_each_font_is_a_woff2_named_by_the_stylesheet(self) -> None:
        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        for name in FONTS:
            assert (STATIC / "fonts" / name).read_bytes()[:4] == b"wOF2", name
            assert f'url("/static/fonts/{name}?v=5.3.0")' in theme, name

    def test_their_licence_ships_and_is_declared(self) -> None:
        for name in LICENCES:
            assert "SIL Open Font License, Version 1.1" in (
                STATIC / "fonts" / name).read_text(encoding="utf-8")
        # Read as text: tomllib is Python 3.11's.
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'license = "MIT AND Apache-2.0 AND ISC AND 0BSD AND OFL-1.1"' in project
        recipe = (ROOT / "recipes" / "fastmdxplora" / "recipe.yaml").read_text(encoding="utf-8")
        assert "license: MIT AND Apache-2.0 AND ISC AND 0BSD AND OFL-1.1" in recipe
        for name in LICENCES:
            assert f'    "src/fastmdxplora/gui/static/fonts/{name}",' in project
            assert f"src/fastmdxplora/gui/static/fonts/{name}" in recipe


def test_they_are_served_as_fonts_and_kept(tmp_path) -> None:
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(session.url.rstrip("/")
                                    + "/static/fonts/inter-latin-wght-normal.woff2?v=5.3.0",
                                    timeout=30) as response:
            said = (response.headers["Content-Type"], response.headers["Cache-Control"],
                    response.read()[:4])
    finally:
        session.server.shutdown()
    assert said == ("font/woff2", "public, max-age=31536000, immutable", b"wOF2")


def test_the_standalone_page_carries_them(tmp_path) -> None:
    from fastmdxplora.gui.report_dashboard import _theme_tokens

    tokens = _theme_tokens()
    assert "/static/fonts/" not in tokens
    assert len(re.findall(r'url\("data:font/woff2;base64,d09GMg', tokens)) == len(FONTS)


def test_the_page_reads_in_them_and_numbers_alone_are_fixed_width(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => document.body.classList.contains('state-ready')")
            page.evaluate("() => document.fonts.ready")
            said = page.evaluate("""() => {
                const face = (el) => getComputedStyle(el).fontFamily.split(',')[0]
                    .replace(/["']/g, '').trim();
                const loaded = [...document.fonts].filter(f => f.status === 'loaded')
                    .map(f => f.family.replace(/["']/g, ''));
                return {
                    loaded: [...new Set(loaded)].sort(),
                    body: face(document.body),
                    label: face(document.querySelector('.overview-strip dt')),
                    number: face(document.getElementById('live-frames-cell')),
                    platform: face(document.getElementById('sidebar-platform')),
                };
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert said == {"loaded": ["Inter", "JetBrains Mono"], "body": "Inter",
                    "label": "Inter", "number": "JetBrains Mono", "platform": "Inter"}
