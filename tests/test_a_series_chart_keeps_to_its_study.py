"""A series chart shows its own study's numbers, or none.

A range chosen on a chart is the study's: forgotten with the charts when
another study is opened. A study opened since that has no such series shows
the card's figure, not the last study's numbers, and offers no chart.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mdtraj")

CHART = '.series-chart[data-analysis="rmsd"]'


def test_a_chart_s_range_is_forgotten_and_a_study_without_the_series_shows_its_figure(
        tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_graph_opens_exact_source_frame import _series, _study

    root = _study(tmp_path / "study")
    _series(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.locator(f"{CHART} svg").wait_for(state="visible")
            # 3 to 6 ps of 1 to 11, in the unit the form reads in.
            fields = page.locator(f"{CHART} form.series-range input")
            low, high = (float(fields.nth(0).get_attribute(end)) for end in ("min", "max"))
            fields.nth(0).fill(f"{low + (high - low) * 0.2:g}")
            fields.nth(1).fill(f"{low + (high - low) * 0.5:g}")
            page.locator(f"{CHART} form.series-range button[type=submit]").click()
            page.wait_for_function(f"() => document.querySelector('{CHART} svg')"
                                   ".getAttribute('aria-label').startsWith('RMSD, 4 points')")
            # Another study opened: the charts forget what was chosen on them.
            plotted = page.evaluate(f"""() => {{
              window.FastMDXSeries.forget();
              const chart = document.querySelector('{CHART}');
              chart.__seriesRender();
              return chart.querySelector('svg').getAttribute('aria-label');
            }}""")
            # And it has no such series.
            page.route("**/api/series?analysis=rmsd*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"ok": false, "reason": "no such series"}'))
            page.evaluate("() => window.dispatchEvent(new CustomEvent('dashboard:run-changed',"
                          " {detail: {first: false}}))")
            page.wait_for_function(f"() => document.querySelector('{CHART}').hidden")
            got = page.evaluate(f"""() => {{
              const chart = document.querySelector('{CHART}');
              const card = chart.closest('.analysis-card');
              const toggle = card && card.querySelector('[data-series-toggle]');
              return {{figure: !chart.nextElementSibling.hidden,
                       toggle: !toggle || toggle.hidden}};
            }}""")
            browser.close()
    finally:
        session.server.shutdown()
    assert plotted.startswith("RMSD, 11 points"), plotted
    assert got == {"figure": True, "toggle": True}, got
    assert errors == []


def test_a_null_series_value_leaves_a_gap_in_the_chart() -> None:
    pytest.importorskip("playwright.sync_api")
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    script = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"
              / "static" / "series-chart.js")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content('<div id="chart" style="width:440px"></div>')
        page.add_script_tag(path=str(script))
        path = page.evaluate("""() => {
          const host = document.getElementById('chart');
          window.FastMDXSeries.draw(host, {
            label: 'RMSD', unit: 'nm', kind: 'time', x_label: 'Frame',
            x: [0, 1, 2], y: [0.1, null, 0.3], frames: [0, 1, 2],
            mean: null, linked: false
          });
          return host.querySelector('.series-line').getAttribute('d');
        }""")
        browser.close()
    assert path.count("M") == 2 and "L" not in path


@pytest.mark.parametrize("values, expected", [
    ([0.1, None, 0.3, 0.4], "3"),
    ([None, 0.2, 0.3], "2"),
])
def test_keyboard_navigation_passes_missing_values(values, expected) -> None:
    pytest.importorskip("playwright.sync_api")
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    script = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"
              / "static" / "series-chart.js")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content('<div id="chart" style="width:440px"></div>')
        page.add_script_tag(path=str(script))
        page.evaluate("""values => {
          window.FastMDXSeries.draw(document.getElementById('chart'), {
            label: 'RMSD', unit: 'nm', kind: 'time', x_label: 'Frame',
            x: values.map((_, i) => i), y: values, frames: values.map((_, i) => i),
            mean: null, linked: false
          });
        }""", values)
        chart = page.locator("#chart svg")
        chart.focus()
        for _ in range(4):
            chart.press("ArrowRight")
        assert page.locator("#chart").get_attribute("data-at") == expected
        assert page.locator(".series-tip").is_visible()
        browser.close()


def test_an_all_missing_series_explains_why_there_is_no_chart() -> None:
    pytest.importorskip("playwright.sync_api")
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    script = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"
              / "static" / "series-chart.js")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content('<div id="chart"></div>')
        page.add_script_tag(path=str(script))
        page.evaluate("""() => window.FastMDXSeries.draw(document.getElementById('chart'), {
          label: 'RMSD', unit: 'nm', kind: 'time', x_label: 'Frame',
          x: [0, 1, 2], y: [null, null, null], frames: [0, 1, 2], mean: null
        })""")
        assert page.locator("#chart").inner_text() == "Every value is missing."
        assert page.locator("#chart svg").count() == 0
        browser.close()
