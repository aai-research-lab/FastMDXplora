"""Compare's Over time charts have local plot tools for the two studies."""

from __future__ import annotations

import json
import shutil
import urllib.parse
from pathlib import Path

import pytest


def test_two_studies_have_local_zoom_and_export_tools(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_frame_holds_the_study import _a_finished_study

    finished = Path(_a_finished_study())
    opened, first, second = (tmp_path / name for name in ("open", "alpha", "beta"))
    for root, offset in ((opened, 0.0), (first, 0.0), (second, 0.2)):
        shutil.copytree(finished, root)
        for analysis, base in (("rmsd", 0.1), ("rg", 1.0)):
            rows = "# frame value\n" + "".join(
                f"{frame} {base + offset + frame * 0.01:.4f}\n" for frame in range(20))
            (root / "analysis" / analysis / f"{analysis}.dat").write_text(rows, encoding="utf-8")
        manifest_path = root / "analysis" / "analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["load_kwargs"] = {"saving_interval_ps": 10.0}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    place = (urllib.parse.quote(str(first), safe="") + "|"
             + urllib.parse.quote(str(second), safe=""))
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
            chart = '.compare-series-block[data-analysis="rmsd"] .compare-series-chart'
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("SVG")')
            svg = Path(got.value.path()).read_text(encoding="utf-8")

            plot = page.locator(chart + " svg")
            plot.scroll_into_view_if_needed()
            box = plot.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.5, steps=6)
            page.mouse.up()
            page.wait_for_function("""() => document.querySelector(
                '.compare-series-block[data-analysis="rmsd"] .compare-series-chart'
            ).dataset.view === 'zoomed'""")
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
                page.click(chart + ' .plot-tool:text("CSV")')
            csv = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
            shown_x = [float(row.split(",")[1]) for row in csv[1:]]
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
    assert len(tools) == 2
    assert all(buttons == ["Show all", "SVG", "PNG", "CSV"] for buttons in tools)
    assert svg.startswith("<svg") and "xmlns" in svg
    assert zoomed["selected"] == "zoomed" and zoomed["showAll"] is True
    assert 0 < len(shown_x) < 40
    assert all(zoomed["view"][0] <= x <= zoomed["view"][1] for x in shown_x)
    assert all_again == {"view": None, "range": None}
