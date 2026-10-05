"""The standalone dashboard is laid out as the GUI is.

`report/dashboard.html` had a layout of its own: a sidebar of initials,
figure cards to resize, "Top Metrics", "Recent Outputs" and "Quick
Actions", styled by a second stylesheet written to look like the GUI's. A
study read in the GUI and in the file its report phase wrote looked like two
programs. The page is now written in the GUI's markup, styled by the GUI's
own stylesheet inlined, its series plotted by the GUI's own chart script,
and its phases, files and report read by the functions the GUI reads them
with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import fastmdxplora.gui as gui_pkg
from fastmdxplora.gui.report_dashboard import _phase_rows, build_dashboard

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)
STATIC = Path(gui_pkg.__file__).with_name("static")


def _study(root: Path, *, manifest: dict | None = None) -> Path:
    rmsd = root / "analysis" / "rmsd"
    rmsd.mkdir(parents=True)
    (rmsd / "rmsd.png").write_bytes(PNG)
    (rmsd / "rmsd.dat").write_text("0 0.10\n1 0.20\n2 0.15\n3 0.18\n", encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"n_frames": 4, "n_atoms": 20, "results": {"rmsd": {"status": "ok"}}}),
        encoding="utf-8")
    if manifest is not None:
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _write(root: Path, **kwargs) -> str:
    (root / "report").mkdir(exist_ok=True)
    build_dashboard(orchestrator=SimpleNamespace(output_dir=root, system="1L2Y"),
                    output_dir=root / "report", title="A study", **kwargs)
    return (root / "report" / "dashboard.html").read_text(encoding="utf-8")


FINISHED = {"system": "1L2Y", "phases": [{"name": "analysis", "status": "ok"},
                                         {"name": "report", "status": "ok"}]}


def test_the_page_is_styled_by_the_gui_s_own_stylesheet(tmp_path: Path) -> None:
    html = _write(_study(tmp_path, manifest=FINISHED))

    for sheet in ("theme.css", "dashboard.css"):
        assert (STATIC / sheet).read_text(encoding="utf-8") in html
    for shell in ('class="app-shell"', '<aside class="sidebar"', 'class="sidebar-nav"',
                  'class="sidebar-stages"', 'class="page-shell"'):
        assert shell in html
    for page in ("overview", "analysis", "report", "files"):
        assert f'<a href="#{page}" class="nav-link' in html
    # What needs the server is not offered by a page without one.
    for live in ("viewer", "agent", "run", "studies"):
        assert f'data-view-link="{live}"' not in html


def test_a_series_is_plotted_by_the_gui_s_chart_script(tmp_path: Path) -> None:
    html = _write(_study(tmp_path, manifest=FINISHED))

    assert (STATIC / "series-chart.js").read_text(encoding="utf-8") in html
    assert '<div class="ac-frame" data-series="rmsd">' in html
    assert "data-series-toggle hidden>Show the figure</a>" in html
    held = re.search(r'<script type="application/json" id="fmx-series">(.*?)</script>',
                     html, re.S)
    assert held is not None
    series = json.loads(held.group(1))
    assert series["rmsd"]["ok"] is True
    assert series["rmsd"]["y"] == [0.10, 0.20, 0.15, 0.18]


def test_a_run_writing_its_report_is_in_its_report_phase() -> None:
    """The phase in progress was always taken to be the simulation, so a
    run writing its report read "Simulation: report", analysis not yet."""
    rows = {row.name: row for row in _phase_rows({}, {"status": "running", "stage": "report"})}

    assert rows["Report"].status == "running"
    assert rows["Analysis"].status == "ok"
    assert rows["Simulation"].status == "ok"


def test_the_page_written_during_a_run_reads_the_live_record(tmp_path: Path) -> None:
    """The manifest is written when the run ends, after this page: a first
    run's page said every phase was "Not run" beside cards saying which had
    finished, because the page read the phases without the live record."""
    root = _study(tmp_path)
    (root / "simulation").mkdir()
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "report", "platform": "CUDA",
        "stage_states": {"setup": "completed", "production": "completed",
                         "analysis": "completed", "report": "current"}}), encoding="utf-8")

    html = _write(root)

    assert '<tr><td>Setup</td><td><span class="stage-pill">ok</span>' in html
    assert '<tr><td>Report</td><td><span class="stage-pill">running</span>' in html
    assert '<li class="stage-step" data-stage="report" data-state="current">' in html
    assert '<span class="status-platform mono" title="Platform">CUDA</span>' in html
    # A run going on when the page was written: its progress card, at the
    # stage it was in.
    assert '<div class="sidebar-progress" data-run="running">' in html
    assert '<span class="progress-stage">Report</span>' in html


def test_only_the_stages_the_study_reaches_are_listed(tmp_path: Path) -> None:
    html = _write(_study(tmp_path, manifest=FINISHED))

    assert '<li class="stage-step" data-stage="nvt" data-state="waiting" hidden>' in html
    assert '<li class="stage-step" data-stage="analysis" data-state="completed">' in html


def test_what_was_not_produced_is_said_and_an_old_record_is_not(tmp_path: Path) -> None:
    root = _study(tmp_path, manifest=FINISHED)
    (root / "report").mkdir()
    (root / "report" / "report.md").write_text("# A study\n\nText.\n", encoding="utf-8")
    # Left by an earlier run; the report phase writes or removes it after
    # this page, so it may no longer be true.
    (root / "report" / "not_produced.json").write_text(json.dumps(
        [{"artifact": "report.pdf", "reason": "an earlier run's reason"}]), encoding="utf-8")

    assert "an earlier run" not in _write(root)
    html = _write(root, not_produced=[("report.pdf", "WeasyPrint is not installed")])
    assert "not produced · report.pdf</span>WeasyPrint is not installed" in html
    assert 'class="card report-document"' in html


def test_the_files_are_the_gui_s_files(tmp_path: Path) -> None:
    html = _write(_study(tmp_path, manifest=FINISHED), include_bundle_link=True)

    # Named by the GUI's own labels, this page and the bundle included
    # though written after the list is read.
    assert '<div class="file-title" title="report/dashboard.html">Standalone dashboard</div>' in html
    assert '<div class="file-title" title="report/project_bundle.zip">Everything, zipped</div>' in html
    assert '<details class="file-fold" data-fold="record">' in html
    assert '<a class="file-action" href="../analysis/rmsd/rmsd.png" target="_blank"' in html


def test_the_pages_open_from_the_sidebar_and_the_series_is_plotted(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _write(_study(tmp_path, manifest=FINISHED))
    page_url = (tmp_path / "report" / "dashboard.html").as_uri()
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.goto(page_url)
            assert page.locator('section[data-page="overview"]').is_visible()

            page.click('.sidebar-nav a[data-view-link="analysis"]')
            page.wait_for_selector(".series-chart svg", timeout=30_000)
            assert page.locator('section[data-page="overview"]').is_hidden()
            page.click("[data-series-toggle]")
            assert page.evaluate(
                "[document.querySelector('.ac-frame[data-series=rmsd]').hidden,"
                " document.querySelector('.series-chart').hidden]") == [False, True]
            assert page.evaluate("document.documentElement.dataset.page") == "analysis"

            page.click("#settings-open")
            page.click('.seg-btn[data-theme="paper"]')
            assert page.evaluate("document.documentElement.dataset.theme") == "paper"

            # An analysis's anchor opens the page it is on.
            page.goto(page_url + "#rmsd")
            assert page.evaluate("document.documentElement.dataset.page") == "analysis"
        finally:
            browser.close()
    assert problems == []


def test_the_sidebar_says_states_as_the_gui_says_them() -> None:
    from fastmdxplora.gui.report_dashboard import (
        _human_size,
        _normalise_stage,
        _phase_state,
        _stage_steps,
        _study_state,
    )

    assert [_normalise_stage(v) for v in ("NVT equilibration", "Loading structure", "odd")] \
        == ["nvt", "setup", "odd"]
    assert [_phase_state(v) for v in ("ok", "error", "skipped", "running", "")] \
        == ["completed", "failed", "skipped", "current", "waiting"]
    assert _study_state({"phases": [{"name": "setup", "status": "error"}]}, None) \
        == ("failed", "error")
    assert _study_state({"phases": [{"name": "setup", "status": "odd"}]}, None) \
        == ("recorded", "stale")
    assert _study_state({}, {"status": "failed"}) == ("failed", "error")
    assert _study_state({}, {"status": "completed"}) == ("completed", "completed")
    assert _study_state({}, {"status": "running"}) == ("running", "waiting")
    assert _study_state({}, None) == ("not run", "stale")
    assert [_human_size(v) for v in (512, 2048, 3 * 1024 ** 2, 5 * 1024 ** 3, None)] \
        == ["512 B", "2.0 KB", "3.00 MB", "5.00 GB", "\u2014"]
    # A stage the live record says is running is current, whatever else.
    steps = {s.stage: s.state for s in _stage_steps(
        {}, {"stage": "npt", "stage_states": {"npt": "running", "nvt": "failed"}}, None)}
    assert steps["npt"] == "current" and steps["nvt"] == "failed" and steps["setup"] == "completed"


def test_a_part_that_cannot_be_read_leaves_its_page_empty(tmp_path: Path, monkeypatch) -> None:
    """The report, the methods and a series are each read on their own: one
    that fails leaves its page as the GUI leaves it with nothing to show,
    never the page unwritten."""
    import fastmdxplora.gui.report_page as report_page
    import fastmdxplora.gui.series as series

    def broken(*args, **kwargs):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(report_page, "report_payload", broken)
    monkeypatch.setattr(report_page, "methods_payload", broken)
    monkeypatch.setattr(series, "series_payload", broken)
    root = _study(tmp_path, manifest=FINISHED)
    (root / "report").mkdir()
    (root / "report" / "report.md").write_text("# A study\n", encoding="utf-8")

    html = _write(root)

    assert "No report yet." in html
    assert 'id="overview-methods-card"' not in html
    assert 'data-series="rmsd"' not in html
    assert '<article class="analysis-card"' in html
