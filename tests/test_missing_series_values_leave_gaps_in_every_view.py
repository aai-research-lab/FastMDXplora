"""Every series reader draws missing frames as gaps, without stretching the axis."""

import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("view, script, selector", [
    ("overview", "overview.js", ".spark-line"),
    ("timeline", "frame-series.js", ".frame-series-line"),
    ("compare", "runs-compared.js", ".runs-overlay-line"),
])
def test_missing_values_break_the_line_and_do_not_set_the_axis(view, script, selector):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    static = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui" / "static"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        paths = []
        for values in ([10, 11, 12, 13], [10, None, 12, 13], [None] * 4):
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
            page.set_content('''<div id="sidebar-output-folder">study</div>
                <div id="overview-results"><div id="overview-tiles"></div></div>
                <div id="frame-series"><div id="frame-series-chart"></div></div>
                <div id="compare"></div><div id="legend"></div>''')
            page.evaluate("""values => {
              window.testSeries = {
                ok: true, kind: 'time', label: 'RMSD', unit: 'nm', x_label: 'Frame',
                x: [0, 1, 2, 3], y: values, frames: [0, 1, 2, 3], linked: true
              };
              window.fetch = async url => ({json: async () => url === '/api/overview'
                ? {ok: true, tiles: [{analysis: 'rmsd', series: 'rmsd', label: 'RMSD'}]}
                : window.testSeries});
            }""", values)
            page.add_script_tag(path=str(static / script))
            page.evaluate("""async view => {
              if (view === 'overview') {
                await window.FastMDXOverview.load(true);
              } else if (view === 'timeline') {
                const state = window.FastMDXFrameSeries.state;
                state.playback = {frame_indices: [0, 1, 2, 3]};
                state.list = [{analysis: 'rmsd', label: 'RMSD', unit: 'nm'}];
                state.index = 1;
                window.FastMDXFrameSeries.add('rmsd');
                await state.tracks[0].asked;
              } else {
                window.FastMDXRunsCompared.drawOverlay(
                  document.getElementById('compare'), document.getElementById('legend'),
                  [{series: window.testSeries, r: {run: {run_id: 'a'}, index: 0}}],
                  {label: 'RMSD', unit: 'nm'}, 0);
              }
            }""", view)
            # The overview's series request is completed after its payload renders.
            page.wait_for_function("""selector => !window.testSeries.y.some(v => v !== null)
                || !!document.querySelector(selector)""", arg=selector)
            paths.append(page.locator(selector).get_attribute("d")
                         if page.locator(selector).count() else None)
            if view == "timeline" and values[0] is not None:
                dot = page.locator(".frame-series-dot")
                assert dot.get_attribute("visibility") == (
                    "hidden" if values[1] is None else "visible")
                assert page.locator(".frame-track-value").inner_text() == (
                    "—" if values[1] is None else "11.000")
            assert errors == []
            page.close()
        browser.close()
    whole, holed, missing = paths
    assert whole is not None and holed is not None
    assert holed.count("M") == 2 and holed.count("L") == 1, holed
    # Removing an interior value must not change the axis or the other points.
    def coords(path):
        return re.findall(r"[ML]([\d.-]+)[ ,]([\d.-]+)", path)

    assert coords(holed) == [coords(whole)[i] for i in (0, 2, 3)]
    assert missing is None
