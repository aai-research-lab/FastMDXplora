"""Chart hover backgrounds enclose their full text at desktop and narrow widths."""
from __future__ import annotations

import json

import pytest

from tests.test_an_analysis_is_drawn_from_its_numbers import _figure
from tests.test_every_figure_is_plotted_in_the_page_s_colours import _write as _other_figures
from tests.test_four_more_figures_are_plotted import _write as _four_figures

FIGURES = (
    "thermodynamics/thermodynamics", "moments_of_inertia/moments_of_inertia", "ss/ss",
    "cluster/cluster_kmeans", "cluster/cluster_hierarchical",
    "cluster/cluster_hierarchical_dendrogram", "dimred/dimred_pca",
)


@pytest.mark.parametrize("width", [1440, 480])
def test_requested_chart_tooltips_fit_long_text_and_stay_within_the_chart(tmp_path, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _four_figures(_other_figures(tmp_path / "study"))
    manifest_path = root / "analysis" / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["results"] = {name.split("/")[0]: {"status": "ok"} for name in FIGURES}
    manifest["resolved"] = {"trajectory": str(root / "simulation" / "production.dcd")}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    thermo = root / "analysis" / "thermodynamics"
    with (thermo / "thermodynamics.dat").open("a", encoding="utf-8") as output:
        output.write("density,1.02333,0.00046,304.0,0.2\n")
    options_path = thermo / "options.json"
    options = json.loads(options_path.read_text(encoding="utf-8"))
    options["findings"]["thermodynamics"]["density"] = {"mean": 1.02333, "units": "g/mL"}
    options_path.write_text(json.dumps(options), encoding="utf-8")
    for name in FIGURES:
        _figure(root / "analysis" / f"{name}.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(30000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_function(
                "(count) => document.querySelectorAll('.figure-chart svg').length === count",
                arg=len(FIGURES))
            page.evaluate("() => document.fonts.ready")
            for name in FIGURES:
                chart = page.locator(
                    f'.analysis-card:has([data-figure="analysis/{name}.png"]) .figure-chart')
                svg = chart.locator("svg")
                svg.scroll_into_view_if_needed()
                box = svg.bounding_box()
                if name == "dimred/dimred_pca":
                    point = svg.locator("circle").first.bounding_box()
                    x, y = point["x"] + point["width"] / 2, point["y"] + point["height"] / 2
                else:
                    x = box["x"] + box["width"] * 0.5
                    y = box["y"] + (63 if name.startswith("thermodynamics/")
                                    else box["height"] * 0.5)
                page.mouse.move(x, y)
                tip = chart.locator(".series-tip")
                expect(tip).to_be_visible()
                geometry = tip.evaluate("""(tip) => {
                    const box = tip.getBoundingClientRect();
                    const host = tip.parentElement.getBoundingClientRect();
                    const text = [];
                    const walker = document.createTreeWalker(tip, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const range = document.createRange();
                        range.selectNodeContents(walker.currentNode);
                        for (const rect of range.getClientRects()) {
                            text.push({left: rect.left, right: rect.right,
                                       top: rect.top, bottom: rect.bottom});
                        }
                    }
                    return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
                            width: box.width, hostLeft: host.left, hostRight: host.right,
                            scrollWidth: tip.scrollWidth, clientWidth: tip.clientWidth,
                            viewport: innerWidth, text};
                }""")
                assert geometry["scrollWidth"] <= geometry["clientWidth"] + 1, name
                assert geometry["left"] >= geometry["hostLeft"] - 1, name
                boundary = min(geometry["hostRight"], geometry["viewport"])
                assert geometry["right"] <= boundary + 1, name
                for rect in geometry["text"]:
                    assert rect["left"] >= geometry["left"] - 1, name
                    assert rect["right"] <= geometry["right"] + 1, name
                    assert rect["top"] >= geometry["top"] - 1, name
                    assert rect["bottom"] <= geometry["bottom"] + 1, name
                if name.startswith("thermodynamics/"):
                    assert "Density" in tip.inner_text()
                    assert geometry["width"] > 260
                else:
                    # Navigation hints share the same fitted background.
                    expect(tip.locator(".series-tip-hint")).to_be_visible()
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()


def test_a_hover_box_tells_one_frame_s_time_from_the_next(tmp_path):
    """Frames 2 fs apart: the times said to six decimals of a nanosecond, as
    the Viewer says them, and a matrix cell's row before its column."""
    pytest.importorskip("playwright.sync_api")
    np = pytest.importorskip("numpy")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.figure_data import figure_payload
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _series, _study

    root = _study(tmp_path / "study", frame_count=20)
    _series(root, rows=20)
    manifest_path = root / "analysis" / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["load_kwargs"]["saving_interval_ps"] = 0.002
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "analysis" / "cluster").mkdir()
    np.savez(root / "analysis" / "cluster" / "cluster_rmsd_matrix.npz",
             rmsd_nm=np.zeros((4, 4)), frames=np.arange(4),
             time_ns=(np.arange(4) + 1) * 0.000002)
    matrix = figure_payload(root, "analysis/cluster/cluster_rmsd_matrix.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            svg.wait_for(state="visible", timeout=30000)
            svg.focus()
            tip = page.locator('.series-chart[data-analysis="rmsd"] .series-tip')
            said = []
            for _ in range(2):
                page.keyboard.press("ArrowRight")
                said.append(tip.text_content())
            page.evaluate("""data => {
              const host = document.createElement('div');
              host.id = 'matrix-check';
              document.querySelector('.page[data-page="analysis"]').appendChild(host);
              window.FastMDXFigures.plot(host, data);
            }""", matrix)
            cell = page.locator("#matrix-check svg")
            cell.scroll_into_view_if_needed()
            box = cell.bounding_box()
            # The second column of the first row (rows run up from the bottom).
            page.mouse.move(box["x"] + 62 + (box["width"] - 62 - 70) * 0.375,
                            box["y"] + box["height"] - 50)
            matrix_said = page.locator("#matrix-check .series-tip").text_content()
            browser.close()
    finally:
        session.server.shutdown()
    assert "0.004 ps · frame 1" in said[0] and "0.006 ps · frame 2" in said[1], said
    # The row, then the column, each with its own time.
    assert "row frame 0 (0.002 ps); column frame 1 (0.004 ps)" in matrix_said, matrix_said
