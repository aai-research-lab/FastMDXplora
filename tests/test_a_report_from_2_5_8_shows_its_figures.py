"""A report written by 2.5.8 shows its figures on the Report page.

The report sits in report/ and, until 1085, linked each figure from the
study's root, `analysis/rmsd/rmsd.png`. 1085 corrected the link for reports
written since, but every study made with 2.5.8, the release on PyPI, still
holds the old one, and on the Report page each of its figures failed to load
(eleven of eleven on a tri-alanine study, 2026-10-02). A link that names
nothing from report/ but a file from the study's root is now read as the
second; the report's file is left as it was written.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

#: A one-pixel PNG, enough for a browser to load.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

REPORT = """# FastMDXplora Study: tri-ala

![rmsd: rmsd](analysis/rmsd/rmsd.png)

![Analysis summary](analysis_summary.png)

![rg: rg](../analysis/rg/rg.png)

See [the data](analysis/rmsd/rmsd.dat), [nothing](analysis/missing.png),
[the web](https://example.org/analysis/rmsd/rmsd.png) and [a heading](#summary).
"""


def _study(root: Path) -> Path:
    for name in ("rmsd", "rg"):
        folder = root / "analysis" / name
        folder.mkdir(parents=True)
        (folder / f"{name}.png").write_bytes(PIXEL)
        (folder / f"{name}.dat").write_text("0 0.1\n", encoding="utf-8")
    (root / "report").mkdir()
    (root / "report" / "analysis_summary.png").write_bytes(PIXEL)
    (root / "report" / "report.md").write_text(REPORT, encoding="utf-8")
    return root


def test_each_link_is_read_as_it_resolves(tmp_path):
    from fastmdxplora.gui.report_page import _links_from_the_report

    root = _study(tmp_path / "study")
    said = _links_from_the_report(REPORT, root / "report", root)
    assert "](../analysis/rmsd/rmsd.png)" in said
    assert "](../analysis/rmsd/rmsd.dat)" in said
    # Already right, beside the report, missing everywhere, or not a file.
    for kept in ("](analysis_summary.png)", "](../analysis/rg/rg.png)",
                 "](analysis/missing.png)", "](https://example.org/analysis/rmsd/rmsd.png)",
                 "](#summary)"):
        assert kept in said
    # The report on disk is as it was written.
    assert (root / "report" / "report.md").read_text(encoding="utf-8") == REPORT


def test_the_report_page_loads_every_figure(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    if report_payload(root)["rendered"] != "html":
        pytest.skip("the markdown library is not installed; the report is shown plain")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-document:not([hidden]) img")
            page.wait_for_function(
                "() => [...document.querySelectorAll('#report-document img')]"
                ".every(i => i.complete)")
            loaded = page.eval_on_selector_all(
                "#report-document img", "imgs => imgs.map(i => [i.alt, i.naturalWidth > 0])")
            browser.close()
    finally:
        session.server.shutdown()
    assert loaded == [["rmsd: rmsd", True], ["Analysis summary", True], ["rg: rg", True]]
