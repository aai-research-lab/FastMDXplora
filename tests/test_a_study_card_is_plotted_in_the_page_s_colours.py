"""A study's card on All studies is plotted in the page's colours.

Each card showed the first figure its study plotted, the analysis's own
picture, white on the dark scheme; a study with no figure showed its
backbone on white. A card now plots its first measure's series from its
numbers in the scheme's colours (the line, the part its mean is taken over,
the mean), and the backbone is rendered for the scheme: on the dark one
with no white behind it and viridis without its darkest fifth.
"""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlencode

import numpy as np
import pytest

from tests.test_a_study_without_a_figure_shows_its_backbone import _helix, _pdb
from tests.test_the_workspace_says_its_studies import _study


def _with_series(root, n=1000):
    study = _study(root, means={"rmsd": (0.1, 0.01)})
    folder = study / "analysis" / "rmsd"
    values = 0.1 + 0.01 * np.sin(np.arange(n) / 30.0)
    (folder / "rmsd.dat").write_text("# rmsd\n" + "".join(f"{v:.6f}\n" for v in values),
                                     encoding="utf-8")
    return study


def test_the_card_names_the_series_it_plots(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    assert card_of(_with_series(tmp_path / "with"))["series"] == "rmsd"
    # A figure without its numbers keeps its picture.
    assert card_of(_study(tmp_path / "without", means={"rmsd": (0.1, 0.01)}))["series"] is None


def test_the_series_is_thinned_to_what_a_card_shows(tmp_path):
    from fastmdxplora.gui.workspace import CARD_POINTS, thumbnail_series

    said = thumbnail_series(_with_series(tmp_path / "study", n=1000))
    assert said["ok"] and said["label"] == "RMSD" and said["unit"] == "nm"
    assert len(said["x"]) == len(said["y"]) <= CARD_POINTS
    assert said["y"][0] == pytest.approx(0.1)


def test_the_backbone_is_rendered_for_each_scheme(tmp_path):
    from fastmdxplora.gui.backbone_picture import backbone_svg

    study = _study(tmp_path / "unplotted")
    _pdb(study / "setup" / "prepared.pdb", _helix(20, "A", 1))
    light, dark = backbone_svg(study), backbone_svg(study, "dark")
    assert 'fill="#ffffff"' in light and 'fill="#ffffff"' not in dark
    assert 'fill="none"' in dark

    def colours(svg):
        return re.findall(r'stroke="(#[0-9a-f]{6})"', svg)

    # Purple first on paper; on the dark scheme the darkest fifth is left
    # out, and the C terminus is viridis's yellow end.
    assert colours(light)[0] != colours(dark)[0]
    assert "#fde725" in dark or max(colours(dark)) > max(colours(light))
    # Each kept apart: one is not the other's.
    assert backbone_svg(study, "dark") is dark and backbone_svg(study, "nonsense") == light


def test_the_route_sends_both(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _with_series(tmp_path / "plotted")
    bare = _study(tmp_path / "bare")
    _pdb(bare / "setup" / "prepared.pdb", _helix(12, "A", 1))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/") + "/api/study-thumbnail?"
    try:
        said = json.loads(urllib.request.urlopen(
            base + urlencode({"path": str(study), "series": "1"}), timeout=30).read())
        dark = urllib.request.urlopen(
            base + urlencode({"path": str(bare), "scheme": "dark"}), timeout=30).read()
    finally:
        session.server.shutdown()
    assert said["ok"] and said["analysis"] == "rmsd"
    assert b'fill="#ffffff"' not in dark


def test_the_card_plots_it_and_follows_the_scheme(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    _with_series(tmp_path / "plotted")
    bare = _study(tmp_path / "bare")
    _pdb(bare / "setup" / "prepared.pdb", _helix(12, "A", 1))
    session = start_dashboard_session(output=str(tmp_path / "plotted"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            plotted = '.study-card[data-path$="plotted"] .study-thumb'
            page.wait_for_selector(plotted + " svg.study-series path")
            label = page.text_content(plotted + " svg.study-series text")
            bare_img = '.study-card[data-path$="bare"] .study-thumb img'
            page.wait_for_selector(bare_img)
            line = page.get_attribute(plotted + " svg.study-series path", "stroke")
            src = page.get_attribute(bare_img, "src")
            page.evaluate("""() => {
                const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
                document.documentElement.dataset.theme = next;
                document.body.dataset.theme = next;
                document.dispatchEvent(new CustomEvent('fmx:theme'));
            }""")
            page.wait_for_function(
                f"(was) => document.querySelector('{plotted} svg.study-series path')"
                ".getAttribute('stroke') !== was", arg=line)
            again = page.get_attribute(bare_img, "src")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert label == "RMSD (nm)"
    assert "scheme=" in src and src != again
