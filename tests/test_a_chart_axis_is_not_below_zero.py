"""A live chart's axis does not go below zero for a quantity that cannot.

The axis was the series with eight per cent added either side, so the speed
chart of every run, whose first sample is 0 ns/day, was labelled -0.381 at
the bottom: a negative speed, on the Overview of a finished study.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui" / "static" / "charts.js"


@pytest.fixture(scope="module")
def bounds():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        page.add_script_tag(path=str(SCRIPT))
        yield lambda values: page.evaluate(
            "(v) => window.FastMDXCharts.valueBounds(v)", values)
        browser.close()


def test_a_series_from_zero(bounds) -> None:
    got = bounds([0.0, 2.0, 4.75])
    assert got["minY"] == 0 and got["maxY"] > 4.75


def test_a_series_all_zero(bounds) -> None:
    got = bounds([0.0, 0.0])
    assert got["minY"] == 0 and got["maxY"] > 0


def test_a_negative_series_keeps_its_margin(bounds) -> None:
    got = bounds([-506551.0, -506000.0])
    assert got["minY"] < -506551.0 and got["maxY"] > -506000.0


def test_a_positive_series_away_from_zero_keeps_its_margin(bounds) -> None:
    got = bounds([295.0, 305.0])
    assert got["minY"] == pytest.approx(294.2) and got["maxY"] == pytest.approx(305.8)
