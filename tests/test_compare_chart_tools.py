"""Compare's Over time charts have local plot tools for the two studies."""

from __future__ import annotations

import csv
import json
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def _two_studies(tmp_path: Path) -> tuple[Path, str]:
    """A study to open and the Compare place of two others, alpha and beta."""
    from tests.test_the_frame_holds_the_study import _a_finished_study

    finished = Path(_a_finished_study())
    opened, first, second = (tmp_path / name for name in ("open", "alpha", "beta"))
    for root, offset in ((opened, 0.0), (first, 0.0), (second, 0.2)):
        shutil.copytree(finished, root)
        for metric, start in (("rmsd", 0.1), ("rg", 1.0)):
            rows = "# frame value\n" + "".join(
                f"{frame} {start + offset + frame * 0.01:.4f}\n" for frame in range(20))
            (root / "analysis" / metric / f"{metric}.dat").write_text(rows, encoding="utf-8")
        manifest_path = root / "analysis" / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["load_kwargs"] = {"saving_interval_ps": 10.0}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    place = (urllib.parse.quote(str(first), safe="") + "|"
             + urllib.parse.quote(str(second), safe=""))
    return opened, place



@pytest.mark.parametrize("analysis,base", [("rmsd", 0.1), ("rg", 1.0)])
def test_two_studies_have_local_zoom_and_export_tools(tmp_path, analysis, base):
    pytest.importorskip("playwright.sync_api")
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    opened, place = _two_studies(tmp_path)
    session = start_dashboard_session(output=str(opened), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#compare/" + place, wait_until="domcontentloaded")
            page.wait_for_selector('.compare-series-block[data-analysis="rmsd"] .plot-tools')
            page.wait_for_function(
                "() => [...document.querySelectorAll('.compare-series-block')].every("
                "(block) => block.querySelector('.plot-tools'))")
            tools = page.evaluate("""() => [...document.querySelectorAll('.compare-series-block')]
                .map((block) => [...block.querySelectorAll('.plot-tool')]
                    .map((button) => button.textContent))""")
            chart = f'.compare-series-block[data-analysis="{analysis}"] .compare-series-chart'
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("SVG")')
            exports = {"full_svg": got.value.suggested_filename}
            assert ET.parse(got.value.path()).getroot().tag == "{http://www.w3.org/2000/svg}svg"

            plot = page.locator(chart + " svg")
            plot.scroll_into_view_if_needed()
            box = plot.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.5, steps=6)
            page.mouse.up()
            page.wait_for_function(
                "(selector) => document.querySelector(selector).dataset.view === 'zoomed'",
                arg=chart)
            zoomed = page.evaluate("""(selector) => {
                const selected = document.querySelector(selector);
                return {
                    view: selected._view,
                    selected: selected.dataset.view,
                    other: [...document.querySelectorAll('.compare-series-chart')]
                        .filter((chart) => chart !== selected).map((chart) => chart.dataset.view),
                    range: window.FastMDXPlotTools.range(),
                    showAll: !selected.querySelector('.plot-tool').hidden,
                };
            }""", chart)
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("SVG")')
            exports["svg"] = got.value.suggested_filename
            svg = ET.parse(got.value.path()).getroot()
            assert svg.tag == "{http://www.w3.org/2000/svg}svg"
            lines = [
                element for element in svg.iter("{http://www.w3.org/2000/svg}path")
                if "runs-overlay-line" in element.get("class", "").split()
            ]
            assert len(lines) == 2 and all(line.get("d", "").startswith("M") for line in lines)
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("PNG")')
            exports["png"] = got.value.suggested_filename
            with Image.open(got.value.path()) as image:
                assert image.format == "PNG" and min(image.size) > 0
                image.verify()
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("CSV")')
            exports["csv"] = got.value.suggested_filename
            with Path(got.value.path()).open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            exports["header"] = rows[0]
            shown = [(row[0], float(row[1]), float(row[2])) for row in rows[1:]]
            expected = [
                (run, (frame + 1) * 0.01, round(base + offset + frame * 0.01, 4))
                for run, offset in (("alpha", 0.0), ("beta", 0.2))
                for frame in range(20)
                if zoomed["view"][0] <= (frame + 1) * 0.01 <= zoomed["view"][1]
            ]
            assert [row[0] for row in shown] == [row[0] for row in expected]
            for actual, wanted in zip(shown, expected, strict=True):
                assert actual[1:] == pytest.approx(wanted[1:])
            assert zoomed["range"] is None
            assert zoomed["other"] == [""]
            page.click(chart + ' .plot-tool:text("Show all")')
            page.wait_for_function(
                "(selector) => document.querySelector(selector).dataset.view === ''",
                arg=chart)
            all_again = page.evaluate("""(selector) => ({
                view: document.querySelector(selector)._view,
                range: window.FastMDXPlotTools.range(),
            })""", chart)
            browser.close()
    finally:
        session.server.shutdown()

    assert errors == []
    assert exports == {
        "full_svg": f"runs_{analysis}.svg",
        "svg": f"runs_{analysis}_range.svg",
        "png": f"runs_{analysis}_range.png",
        "csv": f"runs_{analysis}_range.csv",
        "header": ["run", "time_ns", f"{analysis} (nm)"],
    }
    assert len(tools) == 2
    assert all(buttons == ["Show all", "SVG", "PNG", "CSV"] for buttons in tools)
    assert zoomed["selected"] == "zoomed" and zoomed["showAll"] is True
    assert 0 < len(shown) < 40
    assert {row[0] for row in shown} == {"alpha", "beta"}
    assert all(zoomed["view"][0] <= row[1] <= zoomed["view"][1] for row in shown)
    assert all_again == {"view": None, "range": None}


#: What the page's CSS gives a chart's marks and words.
_LOOK = ("stroke", "stroke-width", "stroke-dasharray", "fill", "opacity",
         "font-size", "font-weight", "text-anchor", "dominant-baseline")


def test_an_exported_chart_looks_as_the_page_shows_it_on_a_dark_page(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    opened, place = _two_studies(tmp_path)
    look = "(svg, names) => [...svg.querySelectorAll('*')].filter((n) => !n.closest('defs'))" \
           ".map((n) => [n.getAttribute('class') || n.tagName," \
           " names.map((name) => getComputedStyle(n).getPropertyValue(name))])"
    session = start_dashboard_session(output=str(opened), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    accept_downloads=True, color_scheme="dark")
            page.set_default_timeout(60000)
            page.goto(session.url + "#compare/" + place, wait_until="domcontentloaded")
            chart = '.compare-series-block[data-analysis="rmsd"] .compare-series-chart'
            page.wait_for_selector(chart + " .plot-tools")
            shown = page.evaluate(f"(names) => ({look})(document.querySelector("
                                  f"'{chart} svg'), names)", list(_LOOK))
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("SVG")')
            exported = Path(got.value.path()).read_text(encoding="utf-8")
            alone = browser.new_page(color_scheme="dark")
            alone.set_content(exported)
            saved = alone.evaluate(f"(names) => ({look})(document.querySelector('svg'), names)",
                                   list(_LOOK))
            browser.close()
    finally:
        session.server.shutdown()

    saved = [mark for mark in saved if mark[0] != "plot-ground"]
    assert [mark[0] for mark in saved] == [mark[0] for mark in shown]
    assert saved == shown
    marks = dict(shown)
    stroke, width = marks["runs-overlay-axis"][0], marks["runs-overlay-axis"][1]
    assert stroke not in ("none", "rgb(0, 0, 0)") and width != "0px"
    assert marks["runs-overlay-grid"][0] not in ("none", "rgb(0, 0, 0)")
    assert marks["runs-overlay-tick"][3] not in ("none", "rgb(0, 0, 0)")
    assert marks["runs-overlay-label"][3] not in ("none", "rgb(0, 0, 0)")
