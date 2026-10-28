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
