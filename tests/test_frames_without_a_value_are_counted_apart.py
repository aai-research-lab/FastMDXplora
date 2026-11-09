"""Frames without a value are counted apart from the frames a mean is over.

A mean's discard is a frame of the series as saved. Where frames have no
value, the record says so (`discard_of`), and how many after the discard
have none (`without_a_value`): the Analysis table said "239 of 300" for a
mean over the 236 of them with a value. A record an earlier version wrote,
whose discard counted only the frames with a value, is read as it was.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest


def _holed(n: int = 2000, lead: int = 200, inside: tuple[int, ...] = (1500, 1600, 1700)):
    values = 0.3 + 0.002 * _ar1(n, 0.5, 1, transient=3.0)
    values[:lead] = np.nan
    values[list(inside)] = np.nan
    return values


def test_a_record_says_what_its_discard_counts_and_what_has_no_value() -> None:
    from fastmdxplora.statistics import mean_record

    values = _holed()
    record = mean_record(values)
    assert record["discard_of"] == "saved frames"
    assert record["discard"] >= 200
    assert record["without_a_value"] == 3
    whole = mean_record(np.nan_to_num(values, nan=0.3))
    assert "discard_of" not in whole and "without_a_value" not in whole


def test_a_record_an_earlier_version_wrote_is_read_as_it_was() -> None:
    # Its discard counted the frames with a value, and the report judged
    # the frames after it among those.
    from fastmdxplora.report.convergence import assess_series
    from fastmdxplora.statistics import mean_record

    values = _holed()
    finite = values[np.isfinite(values)]
    earlier = mean_record(finite)
    earlier["n_frames"] = int(values.size)
    old = assess_series("rmsd", values, record=earlier)
    same = assess_series("rmsd", finite, record=mean_record(finite))
    assert old.discard == earlier["discard"]
    assert old.correlation_frames == pytest.approx(same.correlation_frames)
    assert old.drift_in_noise == pytest.approx(same.drift_in_noise)


def test_the_analysis_table_counts_only_the_frames_averaged(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path / "study"
    _manifest(root, {"rmsd": {"status": "ok"}})
    mean = _analysis(root, "rmsd", _holed())["mean"]
    quantity = next(row for row in overview_of(root)["rows"]
                    if row["analysis"] == "rmsd")["mean"]
    assert quantity["without_a_value"] == 3
    averaged = 2000 - int(mean["discard"]) - 3
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            cell = page.locator('#analysis-results-table tr[data-analysis="rmsd"] '
                                '.analysis-results-frames')
            cell.wait_for(timeout=60000)
            said = cell.text_content()
            browser.close()
    finally:
        session.server.shutdown()
    assert said.startswith(f"{averaged:,} of 2,000 (3 without a value)"), said
