"""A comparison's figures say which runs, and which releases, are behind them.

Each figure an analysis plots has a chip on the Analysis and Report pages:
the release, the frames and options, and the command that plots it again
(1135, 1146). The overlays and trends of a study of several runs had none,
and they rest on more: one curve or one point per run, each run analysed by
whatever release analysed it. The comparison now records, as it plots them,
each figure's runs, where each is, the release that analysed it and, for a
trend, the mean and error plotted (`comparison/figures.json`), and the
Report page puts a chip under each figure that opens them.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study


def _compared(releases: tuple[str, str] = ("2.5.8", "2.5.8")) -> Path:
    from fastmdxplora.batch.compare import build_comparison_report

    root = _a_sweep_study(Path(tempfile.mkdtemp()))
    manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    manifest["runs"] = [dict(entry, status="ok", output_dir=str(root / "runs" / entry["run_id"]))
                        for entry in manifest["planned"]]
    (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for entry, release in zip(manifest["planned"], releases):
        run = root / "runs" / entry["run_id"]
        (run / "simulation" / "live_status.json").write_text(
            json.dumps({"stage": "completed", "current_step": 500, "total_planned_steps": 500}),
            encoding="utf-8")
        record = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        record["phases"] = [{"name": "analysis", "status": "ok",
                             "produced_by": {"version": release}}]
        (run / "manifest.json").write_text(json.dumps(record), encoding="utf-8")
    assert build_comparison_report(root) is not None
    return root


def test_each_figure_is_recorded_with_its_runs():
    from fastmdxplora import __version__

    root = _compared(("2.5.8", "2.5.9"))
    record = json.loads((root / "comparison" / "figures.json").read_text(encoding="utf-8"))
    assert record["version"] == __version__
    overlay, trend = record["figures"]["overlay_rmsd"], record["figures"]["trend_rmsd"]
    assert overlay["kind"] == "overlay" and overlay["analysis"] == "rmsd"
    assert [run["label"] for run in overlay["runs"]] == ["temperature_K=300", "temperature_K=310"]
    assert [run["analysed_by"] for run in overlay["runs"]] == ["2.5.8", "2.5.9"]
    assert all(run["path"].startswith("runs/") and run["frames"] > 0 for run in overlay["runs"])
    assert trend["against"] == "simulation.temperature_K"
    assert [run["x"] for run in trend["runs"]] == [300.0, 310.0]
    plotted = json.loads((root / "comparison" / "figures.json").read_text())["figures"]
    assert all(run["said"] and run["over"] == "after equilibration"
               for run in plotted["trend_rmsd"]["runs"])


def test_the_report_page_is_given_them():
    from fastmdxplora.gui.report_page import report_payload

    root = _compared()
    made = report_payload(root)["figure_provenance"]
    assert {"overlay_rmsd", "trend_rmsd", "overlay_rg", "trend_rg"} <= set(made)
    assert made["trend_rmsd"]["this_version"]


def test_a_comparison_plotted_before_it_was_recorded_has_no_chips():
    from fastmdxplora.gui.report_page import report_payload

    root = _compared()
    (root / "comparison" / "figures.json").unlink()
    assert report_payload(root)["figure_provenance"] == {}


def test_the_chip_under_a_trend_opens_its_runs():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.report_page import report_payload
    from fastmdxplora.gui.server import start_dashboard_session

    root = _compared(("2.5.8", "2.5.9"))
    if report_payload(root)["rendered"] != "html":
        pytest.skip("the markdown library is not installed; the report is shown plain")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-document .report-figure-made .figure-chip")
            chips = page.eval_on_selector_all(
                "#report-document .figure-chip", "cs => cs.map(c => c.dataset.provenance)")
            page.click('#report-document .figure-chip[data-provenance="trend_rmsd"]')
            panel = page.wait_for_selector(
                '.report-figure-made:has([data-provenance="trend_rmsd"]) .figure-provenance:not([hidden])')
            said = panel.text_content()
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert {"overlay_rmsd", "trend_rmsd"} <= set(chips)
    assert "Plotted from 2 runs" in said
    assert "temperature_K=300" in said and "temperature_K=310" in said
    assert "FastMDXplora 2.5.8" in said and "FastMDXplora 2.5.9" in said
    assert "more than one release" in said


def test_a_record_that_cannot_be_written_or_read_is_said_or_skipped(tmp_path, caplog):
    from fastmdxplora.batch.compare import _write_what_was_plotted
    from fastmdxplora.gui.report_page import _comparison_provenance

    (tmp_path / "figures.json").mkdir()
    with caplog.at_level("WARNING"):
        _write_what_was_plotted(tmp_path, {"overlay_rmsd": {"kind": "overlay"}})
    assert "Could not record what the comparison's figures were plotted from" in caplog.text
    assert _comparison_provenance(tmp_path) == {}
    (tmp_path / "figures.json").rmdir()
    (tmp_path / "figures.json").write_text(json.dumps({"figures": ["not", "figures"]}),
                                           encoding="utf-8")
    assert _comparison_provenance(tmp_path) == {}
