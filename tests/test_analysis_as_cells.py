"""The Analysis page as cells, its plots each zoomed, moved and saved.

Each analysis was a heading over its figures; what it determined was in the
table at the top, what it was run with behind a chip, and Analyze again ran
every analysis. Each is now a cell: under its heading, its values as the
table says them, what it was run with (the frames, the atoms, its options)
and Analyze again with it alone ticked. Every plot zooms by a drag across
it, moves with Shift held, shows all again by a double click or its
button, and is saved as SVG, PNG or CSV of what it shows. A time range
dragged across a plot on the simulation's clock is every such plot's, said
at the top of the page and marked on the Viewer's timeline. A map
(secondary structure over time, a matrix, a landscape) is drawn as its
cells, not as a picture of them, and a run under a nanosecond is read in
picoseconds on every plot.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("cells") / "study", analyse=False)
    from fastmdxplora.analysis import AnalysisOrchestrator

    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(include=["rmsd", "rg", "ss"])
    return root


LOOK = """() => {
    const heads = Object.fromEntries([...document.querySelectorAll('.analysis-cell-head')]
        .map((h) => [h.dataset.analysis, h.textContent]));
    const chart = (sel) => document.querySelector(sel);
    const ss = chart('.figure-chart[data-figure-kind="states"]');
    return {
        heads,
        rg: (chart('.series-chart[data-analysis="rg"]') || {}).dataset?.view,
        rmsd: (chart('.series-chart[data-analysis="rmsd"]') || {}).dataset?.view,
        ss: ss ? ss.dataset.view : null,
        ssCells: ss ? ss.querySelector('svg').dataset.cells : null,
        ssImages: ss ? ss.querySelectorAll('svg image').length : null,
        ssWords: ss ? [...ss.querySelectorAll('svg text')].map((t) => t.textContent) : [],
        range: document.getElementById('analysis-cell-range')
            && !document.getElementById('analysis-cell-range').hidden
            ? document.getElementById('analysis-cell-range').textContent : '',
    };
}"""


def test_each_analysis_is_a_cell_whose_plots_zoom_share_a_range_and_save(study, tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"] .cell-values')
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] .plot-tools')
            page.wait_for_selector('.figure-chart[data-figure-kind="states"] .plot-tools')
            before = page.evaluate(LOOK)

            # Analyze again from the RMSD's cell: it alone ticked.
            page.click('.analysis-cell-head[data-analysis="rmsd"] .cell-again')
            page.wait_for_selector("#analysis-again-ask .again-choices")
            ticked = page.evaluate("""() => [...document.querySelectorAll(
                '#analysis-again-ask .again-choices input:checked')].map((i) => i.value)""")
            page.click("#analysis-again-ask .fix-cancel")

            # A range dragged across the RMSD: every plot on the clock shows it.
            svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
            svg.scroll_into_view_if_needed()
            box = svg.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + 100)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.75, box["y"] + 110, steps=6)
            page.mouse.up()
            page.wait_for_function("() => document.querySelector("
                                   "'.series-chart[data-analysis=\"rg\"]').dataset.view === 'range'")
            ranged = page.evaluate(LOOK)
            with page.expect_download() as got:
                page.click('.series-chart[data-analysis="rmsd"] .plot-tool:text("CSV")')
            csv = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
            with page.expect_download() as got:
                page.click('.figure-chart[data-figure-kind="states"] .plot-tool:text("SVG")')
            svg_file = (got.value.suggested_filename, Path(got.value.path()).read_text(encoding="utf-8"))
            with page.expect_download() as got:
                page.click('.series-chart[data-analysis="rg"] .plot-tool:text("PNG")')
            png = (got.value.suggested_filename, Path(got.value.path()).read_bytes()[:8])
            # A map of more runs of a colour than it draws as cells: an image of
            # them, and said so.
            many = page.evaluate("""() => {
                const host = document.createElement('div');
                host.style.width = '600px';
                document.querySelector('.page[data-page="analysis"]').appendChild(host);
                const frames = 400, residues = 300;
                const x = [...Array(frames).keys()].map((i) => 0.001 * (i + 1));
                window.FastMDXFigures.plot(host, {
                    ok: true, kind: 'states', title: 'Secondary structure', x: x,
                    frames: [...Array(frames).keys()], x_label: 'Time (ns)', y_label: 'Residue',
                    residues: [...Array(residues).keys()].map((i) => String(i + 1)),
                    states: x.map((_, i) => [...Array(residues).keys()]
                        .map((r) => ((i + r) % 2 ? 'H' : 'E')).join('')),
                    classes: [{code: 'H', label: 'Helix'}, {code: 'E', label: 'Strand'}],
                    linked: false});
                const image = host.querySelector('svg image');
                const said = [host.querySelector('svg').dataset.cells,
                    [...host.querySelectorAll('svg text')].map((t) => t.textContent),
                    image.querySelector('title').textContent, getComputedStyle(image).imageRendering];
                host.remove();
                return said;
            }""")
            # Show all, at the top of the page.
            page.click("#analysis-cell-range .plot-tool")
            page.wait_for_function("() => document.querySelector("
                                   "'.series-chart[data-analysis=\"rg\"]').dataset.view === ''")
            after = page.evaluate(LOOK)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    rmsd = before["heads"]["rmsd"]
    assert rmsd.startswith("Mean") and "6 frames" in rmsd and "Analyze again" in rmsd
    assert "Helix fraction" in before["heads"]["ss"]
    assert ticked == ["rmsd"]
    # A map drawn as its cells.
    assert before["ssCells"].isdigit() and int(before["ssCells"]) > 0 and before["ssImages"] == 0
    # This run kept no clock: its plots are on frame numbers.
    assert "Frame" in before["ssWords"]
    assert (before["rg"], before["ss"]) == ("", "")
    assert (ranged["rmsd"], ranged["rg"], ranged["ss"]) == ("range", "range", "range")
    assert ranged["range"].startswith("Every plot on the simulation's clock shows ")
    assert csv[0] == "frame,rmsd (nm)"
    shown = [float(line.split(",")[0]) for line in csv[1:]]
    assert 0 < len(shown) < 6, csv
    assert svg_file[0].endswith(".svg") and svg_file[1].startswith("<svg") and "xmlns" in svg_file[1]
    assert png[0].endswith(".png") and png[1] == b"\x89PNG\r\n\x1a\n"
    assert (after["rmsd"], after["rg"], after["ss"], after["range"]) == ("", "", "", "")
    # A run under a nanosecond (0.4 ns here) read in picoseconds.
    assert many[0] == "image" and "Time (ps)" in many[1] and "400" in many[1]
    assert many[2].startswith("Plotted as a picture of its 400 by 300 cells") and many[3] == "pixelated"


def _page(pw, session):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return browser, page, errors


PROJECTION = """() => {
    const host = document.createElement('div');
    host.id = 'a-projection';
    host.style.width = '600px';
    document.querySelector('.page[data-page="analysis"]').appendChild(host);
    const n = 100;
    window.FastMDXFigures.plot(host, {
        ok: true, kind: 'projection', title: 'Projection', axes: ['PC1', 'PC2'], unit: '',
        x: [...Array(n).keys()].map((i) => i % 10), y: [...Array(n).keys()].map((i) => Math.floor(i / 10)),
        t: [...Array(n).keys()], frames: [...Array(n).keys()], t_label: 'Frame', linked: false});
    host.scrollIntoView();
}"""


def _csv(page, selector):
    with page.expect_download() as got:
        page.click(selector + ' .plot-tool:text("CSV")')
    lines = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
    return lines[0], [[float(cell) for cell in line.split(",")[2:]] for line in lines[1:]]


def _drag(page, selector, a, b, shift=False):
    # In view first: a chart below its From/To range form can start below
    # the window, where the pointer reaches nothing.
    page.locator(selector + " svg").scroll_into_view_if_needed()
    box = page.locator(selector + " svg").bounding_box()
    if shift:
        page.keyboard.down("Shift")
    page.mouse.move(box["x"] + box["width"] * a[0], box["y"] + box["height"] * a[1])
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * b[0], box["y"] + box["height"] * b[1], steps=6)
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")


def test_a_map_zooms_on_both_axes_moves_and_shows_all_again(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"]')
            page.evaluate(PROJECTION)
            host = "#a-projection"
            head, every = _csv(page, host)
            _drag(page, host, (0.3, 0.3), (0.6, 0.6))
            page.wait_for_function("() => document.querySelector('#a-projection').dataset.view === 'zoomed'")
            _, zoomed = _csv(page, host)
            _drag(page, host, (0.6, 0.5), (0.3, 0.5), shift=True)
            page.wait_for_timeout(300)
            moved_view = page.evaluate("() => document.querySelector('#a-projection').dataset.view")
            _, moved = _csv(page, host)
            box = page.locator(host + " svg").bounding_box()
            page.mouse.dblclick(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.5)
            page.wait_for_function("() => document.querySelector('#a-projection').dataset.view === ''")
            _, again = _csv(page, host)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert head == "frame,frame_shown,PC1,PC2" and len(every) == 100 and len(again) == 100
    # Less of it on both axes: neither every x nor every y.
    assert 0 < len(zoomed) < 100
    assert len({x for x, _ in zoomed}) < 10 and len({y for _, y in zoomed}) < 10
    # Moved to the right of what was zoomed, and no higher or lower.
    assert moved_view == "zoomed"
    assert min(x for x, _ in moved) > min(x for x, _ in zoomed)
    assert {y for _, y in moved} == {y for _, y in zoomed}


def test_the_range_is_marked_on_the_viewer_s_timeline(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] .plot-tools')
            # The cells say their frame counts once the records are read,
            # which can move the chart under a drag begun before.
            page.wait_for_function("() => window.FastMDXAnalysisPage.overviewRead()")
            _drag(page, '.series-chart[data-analysis="rmsd"]', (0.4, 0.4), (0.8, 0.45))
            page.wait_for_selector("#analysis-cell-range:not([hidden])")
            page.click('[data-view-link="viewer"]')
            page.wait_for_selector("#timeline-ruler svg .timeline-range")
            marked = page.evaluate("""() => [document.getElementById('frame-series').dataset.range,
                parseFloat(getComputedStyle(document.getElementById('frame-series'))
                    .getPropertyValue('--range-width'))]""")
            page.evaluate("() => window.FastMDXPlotTools.clearRange()")
            page.wait_for_function("() => !document.querySelector('#timeline-ruler svg .timeline-range')")
            cleared = page.evaluate("() => document.getElementById('frame-series').dataset.range || ''")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert marked[0] == "on" and marked[1] > 1
    assert cleared == ""


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_beyond_loopback_no_cell_offers_analyze_again(study, public):
    """Analyze again POSTs, and a GUI served beyond this computer refuses
    it; each cell offered it there, only to be refused. The page offers it
    where it is answered."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    runtime = DashboardRuntime(workspace_root=study, exploration_root=study.parent,
                               active_root=study)
    with _serving(runtime, allow_control=not public) as url, sync_playwright() as pw:
        browser, page, errors = _page(pw, None)
        page.goto(url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"] .cell-values')
        page.wait_for_function("() => window.FastMDXDashboard && "
                               "window.FastMDXDashboard.state.appState.active_run")
        page.wait_for_timeout(500)
        offered = page.evaluate("""() => ({
            bound: document.body.dataset.beyondLoopback,
            cells: [...document.querySelectorAll('.cell-again')].filter((b) => !b.hidden).length,
            page: !document.getElementById('analysis-again').hidden,
        })""")
        browser.close()
    assert errors == []
    assert offered["bound"] == ("true" if public else "false")
    if public:
        assert offered == {"bound": "true", "cells": 0, "page": False}
    else:
        assert offered["cells"] >= 3 and offered["page"] is True


def test_a_plot_s_csv_names_the_units_of_its_columns(study):
    """A projection's CSV named its components without the nm its axes
    give, and a list of bars called its column "value", whatever the bars
    were (a share of frames, an error as a percent of a mean)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"]')
            page.evaluate("""() => {
                const add = (id, data) => {
                    const host = document.createElement('div');
                    host.id = id;
                    host.style.width = '600px';
                    document.querySelector('.page[data-page="analysis"]').appendChild(host);
                    window.FastMDXFigures.plot(host, data);
                };
                add('a-pca', {ok: true, kind: 'projection', title: 'Projection', axes: ['PC1', 'PC2'],
                    unit: 'nm', x: [0, 1, 2], y: [0, 1, 2], t: [0.1, 0.2, 0.3], frames: [0, 1, 2],
                    t_label: 'Time (ns)', linked: false});
                add('a-bars', {ok: true, kind: 'hbars', title: 'Protein-ligand interactions by type',
                    x_label: 'Fraction of frames present', x_low: 0, x_high: 1,
                    bars: [{label: 'HBOND A', value: 0.5, error: 0.1, said: ['HBOND A']}]});
                document.getElementById('a-pca').scrollIntoView();
            }""")
            pca = _csv(page, "#a-pca")[0]
            with page.expect_download() as got:
                page.click('#a-bars .plot-tool:text("CSV")')
            bars = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert pca == "frame,time_ns,PC1 (nm),PC2 (nm)"
    assert bars == ["quantity,Fraction of frames present,standard error", "HBOND A,0.5,0.1"]


def test_a_plot_is_zoomed_moved_and_shown_whole_by_the_keyboard(study):
    """Zoom and pan were done by the pointer alone: a figure could not take
    focus, and a series plot took only its cursor's keys. A big map drew
    every cell (40,000 rectangles, seconds to draw again); a projection of
    a run under a nanosecond said its time in ns; an SVG saved in Dark had
    light words on no ground; a range ending before the part averaged drew
    the error band with a negative width."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            logged: list[str] = []
            page.on("console", lambda m: logged.append(m.text) if m.type == "error" else None)
            page.add_init_script("localStorage.setItem('fmx.theme', 'dark');")
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.series-chart[data-analysis="rmsd"] .plot-tools')
            page.wait_for_selector('.figure-chart[data-figure-kind="states"] .plot-tools')
            view = "(s) => document.querySelector(s).dataset.view"
            rmsd = '.series-chart[data-analysis="rmsd"]'
            page.locator(rmsd + " svg").focus()
            page.keyboard.press("+")
            page.wait_for_function(view.replace("(s) =>", "() =>").replace("s)", f"'{rmsd}')")
                                   + " === 'range'")
            kept = page.evaluate(f"() => document.activeElement === document.querySelector('{rmsd} svg')")
            page.keyboard.press("0")
            page.wait_for_function(f"() => document.querySelector('{rmsd}').dataset.view === ''")
            states = page.evaluate("""() => { const s = document.querySelector(
                '.figure-chart[data-figure-kind="states"] svg');
                return [s.getAttribute('tabindex'), s.getAttribute('aria-label'),
                        window.FastMDXKeys.list().some((k) => k.name === 'plot-zoom')]; }""")
            page.evaluate(PROJECTION.replace("t: [...Array(n).keys()],",
                                             "t: [...Array(n).keys()].map((i) => 0.0001 * (i + 1)),")
                          .replace("t_label: 'Frame'", "t_label: 'Time (ns)'"))
            page.locator("#a-projection svg").focus()
            page.keyboard.press("+")
            page.wait_for_function("() => document.querySelector('#a-projection').dataset.view === 'zoomed'")
            _, zoomed = _csv(page, "#a-projection")
            page.locator("#a-projection svg").focus()
            page.keyboard.press("]")
            page.wait_for_timeout(200)
            _, moved = _csv(page, "#a-projection")
            page.locator("#a-projection svg").focus()
            page.keyboard.press("0")
            page.wait_for_function("() => document.querySelector('#a-projection').dataset.view === ''")
            words = page.evaluate("() => [...document.querySelectorAll('#a-projection svg text')]"
                                  ".map((t) => t.textContent)")
            with page.expect_download() as got:
                page.click('#a-projection .plot-tool:text("SVG")')
            saved = Path(got.value.path()).read_text(encoding="utf-8")
            drawn = page.evaluate("""() => {
                const host = document.createElement('div');
                host.style.width = '600px';
                document.querySelector('.page[data-page="analysis"]').appendChild(host);
                const n = 200, values = [...Array(n).keys()].map((i) =>
                    [...Array(n).keys()].map((j) => Math.abs(Math.sin(i * 7 + j * 13))));
                window.FastMDXFigures.plot(host, {ok: true, kind: 'matrix', title: 'RMSD between frames',
                    values: values, extent: [0, 1], axis_label: 'Frame', label: 'RMSD', unit: 'nm',
                    linked: false});
                const said = host.querySelector('svg').dataset.cells;
                host.remove();
                return said;
            }""")
            # A range that ends before the frames averaged.
            bands = page.evaluate("""() => {
                const host = document.createElement('div');
                host.style.width = '600px';
                document.querySelector('.page[data-page="analysis"]').appendChild(host);
                window.FastMDXPlotTools.setRange('Frame', 0, 3);
                window.FastMDXSeries.draw(host, {ok: true, kind: 'time', analysis: 'rg', unit: 'nm',
                    x: [...Array(20).keys()], frames: [...Array(20).keys()], x_label: 'Frame',
                    y: [...Array(20).keys()].map((i) => 1 + 0.01 * Math.sin(i)),
                    mean: {value: 1.0, error: 0.01, from_x: 10, discard: 10}});
                const said = [...host.querySelectorAll('.series-error')].map((r) => +r.getAttribute('width'));
                window.FastMDXPlotTools.clearRange();
                host.remove();
                return said;
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert kept is True
    assert states[0] == "0" and "Keys: + and - zoom" in states[1] and states[2] is True
    assert 0 < len(zoomed) < 100 and min(r[0] for r in moved) > min(r[0] for r in zoomed)
    assert "Time (ps)" in words and "Time (ns)" not in words
    assert '<rect x="0" y="0" width="100%" height="100%" fill="rgb(' in saved
    assert drawn == "image"
    assert all(width >= 0 for width in bands)
    assert not [line for line in logged if "negative value" in line], logged


@pytest.fixture(scope="module")
def full(tmp_path_factory) -> Path:
    """The helical study with its state log of 20 rows, analysed for RMSD,
    RMSF and its thermodynamics."""
    from tests.test_one_mean_has_one_verdict import ENERGY_HEADER
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("full") / "study", analyse=False)
    lines = [ENERGY_HEADER] + [
        f"{5 * k}%,{500 * (k + 1)},{1.0 * (k + 1)},{-45000 + 30 * (k % 3)},8500,"
        f"{-36500 + 30 * (k % 3)},{300 + (k % 4)},33.0,1.02,5" for k in range(20)]
    (root / "simulation" / "energy.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    from fastmdxplora.analysis import AnalysisOrchestrator

    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(
        include=["rmsd", "rmsf", "thermodynamics"])
    return root


def test_a_cell_follows_its_page_and_says_what_its_analysis_read(full):
    """Each cell's Analyze again copied the page's button before the page
    had set it from the run's state, so a run starting left the cells
    offered; the thermodynamics cell said the trajectory's frames beside a
    table saying the state log's rows it read; and a double click on a
    residue plot, to show all, opened the residue in the Viewer."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(full), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="thermodynamics"]')
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"] .cell-again')
            page.wait_for_selector('.series-chart[data-analysis="rmsf"] .plot-tools')
            # The cells are drawn again once the analyses' records are read.
            page.wait_for_function("() => window.FastMDXAnalysisPage.overviewRead()")
            thermo = page.text_content('.analysis-cell-head[data-analysis="thermodynamics"]')
            rmsf = '.series-chart[data-analysis="rmsf"]'
            page.locator(rmsf + " svg").scroll_into_view_if_needed()
            box = page.locator(rmsf + " svg").bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + 100)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 110, steps=6)
            page.mouse.up()
            page.wait_for_function(f"() => document.querySelector('{rmsf}').dataset.view === 'zoomed'")
            box = page.locator(rmsf + " svg").bounding_box()
            page.mouse.dblclick(box["x"] + box["width"] * 0.5, box["y"] + 100)
            page.wait_for_function(f"() => document.querySelector('{rmsf}').dataset.view === ''")
            page.wait_for_timeout(600)
            where = page.evaluate("() => document.documentElement.dataset.page")
            states = page.evaluate("""() => {
                const board = window.FastMDXDashboard, now = board.state.appState;
                const said = () => [document.getElementById('analysis-again').disabled,
                    [...document.querySelectorAll('.cell-again')].map((b) => b.disabled)];
                board.applyAppState({...now, process_running: true});
                const going = said();
                board.applyAppState({...now, process_running: false});
                return [going, said()];
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "20 frames" in thermo and "6 frames" not in thermo, thermo
    going, ended = states
    assert going[0] is True and all(going[1]) and going[1]
    assert ended[0] is False and not any(ended[1])
    assert where == "analysis"


def test_a_cell_says_no_frame_count_before_its_analysis_records_are_read(full):
    """A cell is drawn before the analyses' records come. It said the
    trajectory's frames meanwhile, so the thermodynamics cell read
    "6 frames" over a state log of 20 rows until the records came, and a
    machine slow to read them showed it (and a test read it). Until then it
    says no count; then the count its analysis read."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(full), host="127.0.0.1", port=0)
    head = '.analysis-cell-head[data-analysis="thermodynamics"]'
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            # The records held until the test lets them go.
            page.add_init_script("""
                const fetch = window.fetch;
                let release;
                const held = new Promise(done => { release = done; });
                window.__releaseOverview = () => release();
                window.fetch = (url, ...rest) =>
                    new URL(url, location.href).pathname === '/api/analysis-overview'
                        ? held.then(() => fetch(url, ...rest)) : fetch(url, ...rest);
            """)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector(head + " .cell-again")
            before = page.text_content(head)
            # Read through `?.`, so a page without the hook fails on the
            # count it says, not on the hook.
            read_before = page.evaluate("() => window.FastMDXAnalysisPage.overviewRead?.()")
            page.evaluate("() => window.__releaseOverview()")
            page.wait_for_function(
                "(head) => window.FastMDXAnalysisPage.overviewRead?.()"
                " ?? /20 frames/.test(document.querySelector(head).textContent)", arg=head)
            after = page.text_content(head)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "frames" not in before, before
    assert read_before is False
    assert "20 frames" in after and "6 frames" not in after, after


def test_a_study_of_runs_overlay_zooms_and_saves(tmp_path):
    """The overlay of a study's runs, the Analysis page's only plot for such
    a study, had no zoom, pan or save."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_runs_of_a_study_are_compared_on_one_page import _study

    sweep = _study(tmp_path / "sweep", {
        "t300": {"value": 300, "rmsd": (0.160, 0.001, None)},
        "t310": {"value": 310, "rmsd": (0.164, 0.001, None)}})
    session = start_dashboard_session(output=str(sweep), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('#runs-overlay-chart[data-drawn="2"] .plot-tools')
            head, every = (lambda h: (h[0], h[1]))(_csv_rows(page, "#runs-overlay-chart"))
            page.locator("#runs-overlay-chart svg").focus()
            page.keyboard.press("+")
            page.wait_for_function("() => document.getElementById('runs-overlay-chart')"
                                   ".dataset.view !== ''")
            _, some = _csv_rows(page, "#runs-overlay-chart")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert head == "run,time_ns,rmsd (nm)" and len(every) == 10
    assert 0 < len(some) < len(every)


def _csv_rows(page, selector):
    with page.expect_download() as got:
        page.click(selector + ' .plot-tool:text("CSV")')
    lines = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
    return lines[0], lines[1:]


def test_a_key_zooms_in_no_further_than_a_few_points(study):
    """Forty presses of + left no point in view and ticks all one number."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"]')
            page.evaluate(PROJECTION)
            for _ in range(40):
                page.locator("#a-projection svg").focus()
                page.keyboard.press("+")
            page.wait_for_timeout(300)
            _, projection = _csv(page, "#a-projection")
            rmsd = '.series-chart[data-analysis="rmsd"]'
            for _ in range(40):
                page.locator(rmsd + " svg").focus()
                page.keyboard.press("+")
            page.wait_for_timeout(300)
            with page.expect_download() as got:
                page.click(rmsd + ' .plot-tool:text("CSV")')
            series = Path(got.value.path()).read_text(encoding="utf-8").splitlines()[1:]
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # Four points' worth of 100 points on a 10 by 10 grid: some in view.
    assert len(projection) >= 1, projection
    assert len(series) >= 3, series


def test_a_key_or_a_drag_never_takes_a_plot_past_its_run(study):
    """- and ] (and Shift with a drag) had no bound: a few presses gave every
    plot, and the Viewer, a time range outside the run."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    rmsd = '.series-chart[data-analysis="rmsd"]'
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector(rmsd + " .plot-tools")

            def press(selector, key, times=1):
                for _ in range(times):
                    page.locator(selector + " svg").focus()
                    page.keyboard.press(key)
                page.wait_for_timeout(200)

            press(rmsd, "-")
            whole = page.evaluate(f"() => document.querySelector('{rmsd}').dataset.view")
            press(rmsd, "+", 2)
            press(rmsd, "]", 20)
            moved = page.evaluate("() => window.FastMDXPlotTools.range()")
            press(rmsd, "[", 20)
            back = page.evaluate("() => window.FastMDXPlotTools.range()")
            press(rmsd, "-", 8)
            out = page.evaluate(f"""() => [document.querySelector('{rmsd}').dataset.view,
                window.FastMDXPlotTools.range()]""")
            page.evaluate(PROJECTION)
            press("#a-projection", "+", 3)
            press("#a-projection", "ArrowRight", 20)
            press("#a-projection", "ArrowUp", 20)
            _, corner = _csv(page, "#a-projection")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # Frames 0 to 5 (the run kept no clock).
    assert whole == ""
    assert moved and moved["to"] <= 5 + 1e-9 and moved["from"] >= 0, moved
    assert back and back["from"] >= -1e-9, back
    assert out == ["", None]
    assert corner and max(r[0] for r in corner) == 9 and max(r[1] for r in corner) == 9


def test_the_backbone_order_parameters_are_a_plot_with_its_tools(study, tmp_path):
    """Of an analysed study's figures, the backbone order parameters were
    the one picture without zoom, SVG, PNG or CSV: its table is one value a
    residue, as RMSF's, and it is drawn as RMSF is."""
    pytest.importorskip("playwright.sync_api")
    import json
    import shutil

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    shutil.copytree(study, root)
    folder = root / "analysis" / "order_parameters"
    folder.mkdir()
    (folder / "order_parameters.dat").write_text(
        "# order_parameters: whitespace-delimited, no column header.\n"
        + "".join(f"{r:.8e} {0.6 + 0.01 * r:.8e}\n" for r in range(2, 12)), encoding="utf-8")
    (folder / "order_parameters.png").write_bytes((root / "analysis" / "rmsd" / "rmsd.png").read_bytes())
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "order_parameters", "findings": {"order_parameters": {"vectors": 10}}}),
        encoding="utf-8")
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text(encoding="utf-8"))
    manifest["plan"] = list(manifest.get("plan") or []) + ["order_parameters"]
    manifest.setdefault("results", {})["order_parameters"] = {
        "name": "order_parameters", "status": "ok", "output_dir": str(folder),
        "figure_path": str(folder / "order_parameters.png"),
        "data_path": str(folder / "order_parameters.dat"),
        "options_path": str(folder / "options.json")}
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    chart = '.series-chart[data-analysis="order_parameters"]'
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector(chart + " .plot-tools")
            page.locator(chart + " svg").scroll_into_view_if_needed()
            with page.expect_download() as got:
                page.click(chart + ' .plot-tool:text("CSV")')
            rows = Path(got.value.path()).read_text(encoding="utf-8").splitlines()
            focusable = page.evaluate(f"() => document.querySelector('{chart} svg').getAttribute('tabindex')")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert rows[0] == "residue,order_parameters" and len(rows) == 11 and rows[1].startswith("2,")
    assert focusable == "0"


def test_a_zoomed_map_has_its_scale_and_escape_puts_a_cell_s_question_away(study):
    """Zoomed by keys to its floor, the Ramachandran map kept its ticks every
    90 degrees and showed one, 0, on each axis; and the Analyze again
    question asked from a cell did not close on Escape."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector('.analysis-cell-head[data-analysis="rmsd"] .cell-again')
            page.evaluate("""() => {
                const host = document.createElement('div');
                host.id = 'a-rama';
                host.style.width = '600px';
                document.querySelector('.page[data-page="analysis"]').appendChild(host);
                const n = 72, edges = [...Array(n + 1).keys()].map((i) => -180 + 5 * i);
                const values = [...Array(n).keys()].map((j) => [...Array(n).keys()].map(
                    (i) => Math.log(1 + ((i * 7 + j * 3) % 11))));
                window.FastMDXFigures.plot(host, {ok: true, kind: 'density', title: 'Ramachandran',
                    values: values, edges_x: edges, edges_y: edges, ticks: [-180, -90, 0, 90, 180],
                    axes: ['φ (degrees)', 'ψ (degrees)'], label: 'log(1 + count)', unit: ''});
                host.scrollIntoView();
            }""")
            for _ in range(12):
                page.locator("#a-rama svg").focus()
                page.keyboard.press("+")
            page.wait_for_timeout(300)
            # The ψ axis's ticks (the colour bar's are not anchored at their end).
            words = page.evaluate("""() => [...document.querySelectorAll(
                '#a-rama svg text[text-anchor="end"]')].map((t) => t.textContent)""")
            cell = '.analysis-cell-head[data-analysis="rmsd"] .cell-again'
            page.click(cell)
            page.wait_for_selector("#analysis-again-ask .again-choices")
            page.keyboard.press("Escape")
            put = page.evaluate(f"""() => [document.getElementById('analysis-again-ask').hidden,
                document.activeElement === document.querySelector('{cell}')]""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert len(set(words)) >= 3, words
    assert put == [True, True]
