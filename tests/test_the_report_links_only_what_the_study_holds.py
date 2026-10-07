"""The Report page links only what the study holds.

The report names the standalone page in its first lines,
`_Dashboard: [dashboard.html](dashboard.html)_`. A study shared without that
page (the demo leaves it out) showed a link that went nowhere, the first
thing on the page. A link to a file the study does not hold is shown as its
words, and the line naming the standalone page is left out while the page
is not there.
"""

from __future__ import annotations

from pathlib import Path

REPORT = """# 3PTB

_Generated: 2026-10-07 (UTC)_<BR>
_Software: FastMDXplora_<BR>
_Dashboard: [dashboard.html](dashboard.html)_

## Results

The [RMSD data](../analysis/rmsd/rmsd.dat) and [the slides](slides.pptx).
""".replace("<BR>", "  ")  # the report's line breaks, two spaces each


def _study(root: Path, *, with_page: bool) -> Path:
    (root / "report").mkdir(parents=True)
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.dat").write_text("0 0.1\n", encoding="utf-8")
    (root / "report" / "report.md").write_text(REPORT, encoding="utf-8")
    if with_page:
        (root / "report" / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    return root


def test_a_study_without_its_standalone_page_does_not_link_it(tmp_path):
    from fastmdxplora.gui.report_page import _links_from_the_report

    root = _study(tmp_path / "s", with_page=False)
    said = _links_from_the_report(REPORT, root / "report", root)
    assert "dashboard.html" not in said
    assert "_Software: FastMDXplora_" in said
    # A file the study holds stays a link; one it does not is its words.
    assert "[RMSD data](../analysis/rmsd/rmsd.dat)" in said
    assert "and the slides." in said


def test_a_study_with_its_standalone_page_links_it(tmp_path):
    from fastmdxplora.gui.report_page import _links_from_the_report

    root = _study(tmp_path / "s", with_page=True)
    said = _links_from_the_report(REPORT, root / "report", root)
    assert "_Dashboard: [dashboard.html](dashboard.html)_" in said


def test_the_page_sent_has_no_dead_link(tmp_path):
    from fastmdxplora.gui.report_page import report_payload

    root = _study(tmp_path / "s", with_page=False)
    html = report_payload(root)["html"]
    assert "dashboard.html" not in html and "slides.pptx" not in html
    assert (root / "report" / "report.md").read_text(encoding="utf-8") == REPORT
