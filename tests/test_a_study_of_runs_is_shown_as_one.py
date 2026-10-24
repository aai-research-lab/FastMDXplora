"""A study of several runs, in the file browser and the dashboard.

A sweep writes one folder per run under runs/<id>/, each a complete study,
and a root that holds the plan. The dashboard treated the root as a study
with nothing in it: no stage, no events, and once switched into a run it
refused to switch back, because its rule for a study folder differed from
the browser's. Now one rule; the root lists its runs with where each stands;
a run names its study and the way back; and the browser tells a study of
runs, and a run inside one, apart from a study of one.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False


def _a_sweep_study(where: Path, temperatures=(300, 310)) -> Path:
    """A sweep over temperature: its first run completed, the rest running."""
    import sys

    sys.path.insert(0, "src")
    from fastmdxplora.batch.explorer import BatchExplorer
    from tests.test_the_frame_holds_the_study import _a_finished_study

    root = where / "sweep"
    study = BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                       "simulation": {"duration_ns": 1.0},
                                       "sweep": {"simulation.temperature_K": list(temperatures)}},
                          output_dir=str(root))
    root.mkdir(parents=True)
    (root / "runs").mkdir()
    study._write_study_config()
    study._write_batch_manifest()
    finished = Path(_a_finished_study())
    # `completed` is what the orchestrator records when a run ends.
    statuses = [{"stage": "completed", "current_step": 500, "total_planned_steps": 500}] + [
        {"stage": "Production", "current_step": 200, "total_planned_steps": 500}] * (len(temperatures) - 1)
    for spec, status in zip(study.run_specs, statuses):
        run = study._run_output_dir(spec)
        shutil.copytree(finished, run, dirs_exist_ok=True)
        (run / "simulation" / "live_status.json").write_text(json.dumps(status), encoding="utf-8")
        # What each run settled on, as an analysis records it: the record
        # `summarise` makes of a series, which is what the analyses write.
        for name, mean in (("rmsd", 0.15 + 0.01 * spec.sweep_values["simulation.temperature_K"] / 300),
                           ("rg", 1.2)):
            (run / "analysis" / name / "options.json").write_text(json.dumps({
                "analysis": name, "options": {},
                "findings": {"mean": _recorded(mean)}}), encoding="utf-8")
    return root


def _recorded(mean: float, frames: int = 400) -> dict:
    """A series about ``mean``, summarised by the analyses' own function."""
    import numpy as np

    from fastmdxplora.statistics import summarise

    series = mean + 0.002 * np.random.default_rng(0).standard_normal(frames)
    series = series - series.mean() + mean
    equilibrated, reason = summarise(series)
    record = dict(equilibrated.as_record()) if equilibrated is not None else {}
    if reason is not None:
        record["not_a_measurement"] = reason
    record["n_frames"] = frames
    return record


class TestTheBrowserTellsThemApart(unittest.TestCase):
    def test_a_root_and_its_runs(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.gui.browse import browse, is_study

        work = Path(tempfile.mkdtemp())
        root = _a_sweep_study(work)
        self.assertTrue(is_study(root))
        # A root written before the study-level resolved config existed has
        # only its batch manifest, and is a study by that alone.
        older = work / "older"
        older.mkdir()
        (older / "batch_manifest.json").write_text("{}", encoding="utf-8")
        self.assertTrue(is_study(older))
        studies = {e["name"]: e for e in browse(path=str(work))["entries"] if e.get("study")}
        self.assertEqual(studies["sweep"]["runs"], 2)
        self.assertEqual(studies["sweep"]["swept"], ["simulation.temperature_K"])
        runs = {e["name"]: e for e in browse(path=str(root / "runs"))["entries"] if e.get("study")}
        self.assertEqual(len(runs), 2)
        for entry in runs.values():
            self.assertEqual(entry["run_of"], "sweep")
        self.assertEqual(sorted(e["values"]["simulation.temperature_K"] for e in runs.values()),
                         [300, 310])

    def test_the_plan_is_recorded_before_any_run_starts(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.gui.exploration import runs_of_a_study

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        runs = runs_of_a_study(root)
        self.assertEqual([r["state"] for r in runs], ["completed", "running"])
        self.assertEqual(runs[1]["fraction"], 0.4)
        # A run not yet started is waiting, not absent.
        shutil.rmtree(Path(runs[1]["path"]))
        self.assertEqual(runs_of_a_study(root)[1]["state"], "waiting")


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestTheDashboardShowsTheRuns(unittest.TestCase):
    def test_the_root_lists_its_runs_and_a_run_names_its_study(self):
        """At the head of the page (place.js): the study, its runs in a menu
        with where each stands, and in a run, the study it belongs to."""
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        run = ".page[data-page=overview] .place-run"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(session.url + "#overview", wait_until="domcontentloaded")
                page.wait_for_selector(run, timeout=20000)
                self.assertEqual(page.text_content(run), "All runs")
                page.click(run)
                page.wait_for_selector("#place-menu")
                rows = page.eval_on_selector_all(
                    "#place-menu .place-menu-item",
                    "rs => rs.map(r => [r.querySelector('.place-menu-label').textContent,"
                    " r.querySelector('.place-menu-said').textContent])")
                self.assertEqual(rows, [["All runs", "1 of 2 completed"],
                                        ["temperature 300 K", "completed"],
                                        ["temperature 310 K", "40%"]])
                # Into the running run, and back by the study's name.
                page.click("#place-menu .place-menu-item:nth-child(3)")
                page.wait_for_function(
                    f"() => (document.querySelector('{run}') || {{}}).textContent"
                    " === 'temperature 310 K'", timeout=20000)
                self.assertEqual(page.text_content(".page[data-page=overview] .place-study"),
                                 "sweep")
                page.click(".page[data-page=overview] .place-study")
                page.wait_for_function(
                    f"() => (document.querySelector('{run}') || {{}}).textContent === 'All runs'",
                    timeout=20000)
                browser.close()
        finally:
            session.server.shutdown()


class TestTheRootReportsTheCollective(unittest.TestCase):
    def test_before_the_comparison_it_says_what_the_finished_runs_settled_on(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.gui.report_page import report_payload

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        page = report_payload(root)
        self.assertTrue(page["ok"])
        self.assertEqual((page["runs"], page["pending"]), (2, 1))
        self.assertIn("1 of 2 runs completed", page["html"])
        self.assertIn("1 still to run", page["html"])
        self.assertIn("RMSD mean (nm)", page["html"])
        # The finished run's mean, 300 K, with its error, to the place it allows.
        self.assertIn("0.16001 ± 0.00011", page["html"])
        self.assertNotIn("0.1603", page["html"])     # the running one's is not there
        self.assertIn("after equilibration, with its standard error", page["html"])
        self.assertNotIn("Not a measurement", page["html"])

    def test_a_mean_that_is_not_a_measurement_is_marked(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.gui.report_page import report_payload

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        finished = next(p for p in (root / "runs").iterdir()
                        if "completed" in (p / "simulation" / "live_status.json").read_text())
        (finished / "analysis" / "rg" / "options.json").write_text(json.dumps({
            "analysis": "rg", "options": {},
            "findings": {"mean": _recorded(1.2, frames=12)}}), encoding="utf-8")
        page = report_payload(root)
        self.assertIn("Not determined", page["html"])

    def test_once_every_run_has_finished_it_is_the_comparison(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.batch.compare import build_comparison_report
        from fastmdxplora.gui.report_page import report_payload

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
        manifest["runs"] = [dict(entry, status="ok", output_dir=str(root / "runs" / entry["run_id"]))
                            for entry in manifest["planned"]]
        (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for entry in manifest["planned"]:
            (root / "runs" / entry["run_id"] / "simulation" / "live_status.json").write_text(
                json.dumps({"stage": "completed", "current_step": 500, "total_planned_steps": 500}),
                encoding="utf-8")
        self.assertIsNotNone(build_comparison_report(root))
        page = report_payload(root)
        self.assertTrue(page["ok"])
        self.assertEqual(page["pending"], 0)
        self.assertEqual(page["figures_under"], "comparison")
        self.assertIn("overlay_rmsd.png", page["html"])
        self.assertIn("comparison_summary.csv", page["downloads"]["summary"])

    def test_with_no_run_finished_it_says_how_many_are_to_come(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from fastmdxplora.gui.report_page import report_payload

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        for run in (root / "runs").iterdir():
            (run / "simulation" / "live_status.json").write_text(
                json.dumps({"stage": "Production", "current_step": 10, "total_planned_steps": 500}),
                encoding="utf-8")
        page = report_payload(root)
        self.assertFalse(page["ok"])
        self.assertEqual(page["reason"], "none of 2 runs completed yet")
        self.assertEqual(page["pending"], 2)


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestTheComparisonRendersOnTheReportPage(unittest.TestCase):
    def test_its_figures_are_served_from_the_comparison_folder(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.batch.compare import build_comparison_report
        from fastmdxplora.gui.server import start_dashboard_session

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
        manifest["runs"] = [dict(entry, status="ok", output_dir=str(root / "runs" / entry["run_id"]))
                            for entry in manifest["planned"]]
        (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for entry in manifest["planned"]:
            (root / "runs" / entry["run_id"] / "simulation" / "live_status.json").write_text(
                json.dumps({"stage": "completed", "current_step": 500, "total_planned_steps": 500}),
                encoding="utf-8")
        build_comparison_report(root)
        # Without the markdown library (the pdf extra) the page shows the
        # report as the text it was written in, and there are no figures to
        # resolve; the plain form is checked in
        # test_the_report_is_read_where_the_study_ran. Here, the rendered one.
        from fastmdxplora.gui.report_page import report_payload

        if report_payload(root)["rendered"] != "html":
            self.skipTest("the markdown library is not installed; the report is shown plain")
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(session.url + "#report", wait_until="domcontentloaded")
                page.wait_for_selector("#report-document:not([hidden]) img", timeout=20000)
                images = page.eval_on_selector_all(
                    "#report-document img",
                    "imgs => imgs.map(i => [i.getAttribute('src'), i.naturalWidth > 0])")
                browser.close()
        finally:
            session.server.shutdown()
        self.assertTrue(images)
        for src, drawn in images:
            self.assertTrue(src.startswith("/artifacts/comparison/"), src)
            self.assertTrue(drawn, f"{src} did not load")


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestEveryRunIsInTheMenu(unittest.TestCase):
    def test_twelve_runs_are_listed_and_the_menu_scrolls_not_the_page(self):
        """The sidebar showed seven, then three more at a time, so a sweep
        did not push the rest of it away. The runs are a menu at the head of
        the page now: every one listed, in the order planned, the menu
        scrolling within the window."""
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        root = _a_sweep_study(Path(tempfile.mkdtemp()), temperatures=tuple(range(300, 420, 10)))
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 500})
                page.goto(session.url + "#overview", wait_until="domcontentloaded")
                page.wait_for_selector(".page[data-page=overview] .place-run", timeout=20000)
                page.click(".page[data-page=overview] .place-run")
                page.wait_for_selector("#place-menu")
                said = page.evaluate("""() => {
                    const menu = document.getElementById('place-menu');
                    const box = menu.getBoundingClientRect();
                    return {labels: [...menu.querySelectorAll('.place-menu-label')]
                                .map((l) => l.textContent),
                            inside: box.bottom <= innerHeight,
                            scrolls: menu.scrollHeight > menu.clientHeight}; }""")
                browser.close()
        finally:
            session.server.shutdown()
        self.assertEqual(said["labels"], ["All runs"] + [f"temperature {t} K"
                                                          for t in range(300, 420, 10)])
        self.assertTrue(said["inside"])
        self.assertTrue(said["scrolls"])


def test_a_study_of_several_runs_records_its_process_at_its_top(tmp_path, monkeypatch):
    """Found by the third review (10-07): only each run inside it recorded
    a process (a worker, in parallel), so a GUI opened on the study after a
    restart could not tell it was still going between two runs, or while
    its comparison was written."""
    import os

    from fastmdxplora.batch.explorer import BatchExplorer
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    root = tmp_path / "sweep"
    study = BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                       "simulation": {"duration_ns": 1.0},
                                       "sweep": {"simulation.temperature_K": [300, 310]}},
                          output_dir=str(root))
    seen = {}

    def runs(self, include, exclude):
        record = root / RUN_PROCESS_FILE
        seen["pid"] = json.loads(record.read_text(encoding="utf-8")).get("pid")
        return []

    def prepare(self, include, exclude):
        # An umbrella study prepares once, for hours, before any run.
        record = root / RUN_PROCESS_FILE
        seen["preparing"] = json.loads(record.read_text(encoding="utf-8")).get("pid")
        return None

    monkeypatch.setattr(BatchExplorer, "_maybe_prepare_once", prepare)
    monkeypatch.setattr(BatchExplorer, "_run_sequential", runs)
    monkeypatch.setattr(BatchExplorer, "_run_parallel", runs)
    monkeypatch.setattr(BatchExplorer, "_write_comparison", lambda self: None, raising=False)
    try:
        study.run()
    except Exception:  # noqa: BLE001 - what follows the runs is not this test's
        pass
    assert seen.get("pid") == seen.get("preparing") == os.getpid()
