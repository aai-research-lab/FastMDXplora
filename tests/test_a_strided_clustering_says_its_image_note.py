"""A clustering of every k-th frame says its image note beside its stride.

The Analysis page's Conformations row gave a strided clustering's stride as
its finding, and the branch that shows a finding never showed the image
note under it: a run of more than 5,000 frames whose solute met its own
periodic image lost "may be biased" from the results table.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.image_note import said_short
from tests.test_the_image_note_reaches_every_page import N, _finding

STRIDE_SAID = ("Cluster populations were counted on every 3rd frame (`max_frames: 100`): "
               "each cluster's frames are of the 667 clustered, of 2,000 analysed, and its "
               "fraction is of those 667. `max_frames: null` clusters every frame.")


@pytest.fixture
def strided(tmp_path: Path) -> Path:
    """A study whose clustering took every 3rd frame, noted over the frames
    it clustered as the analysis notes them (`note_on_frames`)."""
    from fastmdxplora.analysis.image_check import note_on_frames
    from tests.test_the_analysis_page_reads_as_a_whole import _manifest

    root = tmp_path / "study"
    _manifest(root, {"image_check": {"status": "ok"}, "cluster": {"status": "ok"}})
    finding, series = _finding()
    folder = root / "analysis" / "image_check"
    folder.mkdir(parents=True)
    (folder / "image_check.dat").write_text("".join(f"{v}\n" for v in series))
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "image_check", "selection": "protein",
         "findings": {"solute_image": finding}}))
    folder = root / "analysis" / "cluster"
    folder.mkdir()
    note = note_on_frames(finding, series, np.arange(0, N, 3), analysis="cluster")
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "cluster", "selection": "name CA",
         "findings": {"frames": {"first_frame": 0, "stride": 3, "n_frames_analysed": N},
                      "populations": {"stride": 3, "said": STRIDE_SAID},
                      "solute_image": note}}))
    return root


def test_its_row_carries_both(strided):
    from fastmdxplora.gui.analysis_overview import overview_of

    row = {r["analysis"]: r for r in overview_of(strided)["rows"]}["cluster"]
    record = json.loads((strided / "analysis" / "cluster" / "options.json").read_text())
    assert row["finding"] == STRIDE_SAID.replace("`", "")
    assert row["noted"] == said_short(record["findings"]["solute_image"])


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def test_the_analysis_page_shows_both(browser, strided):
    from fastmdxplora.gui.server import start_dashboard_session

    record = json.loads((strided / "analysis" / "cluster" / "options.json").read_text())
    noted = said_short(record["findings"]["solute_image"])
    session = start_dashboard_session(output=str(strided), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector('#analysis-results-table tr[data-analysis="cluster"]')
        said = page.text_content('#analysis-results-table tr[data-analysis="cluster"]')
        image = page.query_selector(
            '#analysis-results-table tr[data-analysis="cluster"] .analysis-results-noted')
    finally:
        page.close()
        session.stop()
    assert not errors, errors
    assert STRIDE_SAID.replace("`", "") in said, said
    assert image is not None and noted in said, said
