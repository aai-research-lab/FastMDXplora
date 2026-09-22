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


def _a_sweep_study(where: Path) -> Path:
    """A two-run sweep over temperature, its runs finished and running."""
    import sys

    sys.path.insert(0, "src")
    from fastmdxplora.batch.explorer import BatchExplorer
    from tests.test_the_frame_holds_the_study import _a_finished_study

    root = where / "sweep"
    study = BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                       "simulation": {"duration_ns": 1.0},
                                       "sweep": {"simulation.temperature_K": [300, 310]}},
                          output_dir=str(root))
    root.mkdir(parents=True)
    (root / "runs").mkdir()
    study._write_study_config()
    study._write_batch_manifest()
    finished = Path(_a_finished_study())
    for spec, status in zip(study.run_specs, (
            {"stage": "finished", "current_step": 500, "total_planned_steps": 500},
            {"stage": "Production", "current_step": 200, "total_planned_steps": 500})):
        run = study._run_output_dir(spec)
        shutil.copytree(finished, run, dirs_exist_ok=True)
        (run / "simulation" / "live_status.json").write_text(json.dumps(status), encoding="utf-8")
        # What each run settled on, as an analysis records it.
        for name, mean in (("rmsd", 0.15 + 0.01 * spec.sweep_values["simulation.temperature_K"] / 300),
                           ("rg", 1.2)):
            (run / "analysis" / name / "options.json").write_text(json.dumps({
                "analysis": name, "options": {},
                "findings": {"mean": {"mean": mean, "uncertainty": 0.005, "unit": "nm"}}}),
                encoding="utf-8")
    return root


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
        self.assertEqual([r["state"] for r in runs], ["finished", "running"])
        self.assertEqual(runs[1]["fraction"], 0.4)
        # A run not yet started is waiting, not absent.
        shutil.rmtree(Path(runs[1]["path"]))
        self.assertEqual(runs_of_a_study(root)[1]["state"], "waiting")


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestTheDashboardShowsTheRuns(unittest.TestCase):
    def test_the_root_lists_its_runs_and_a_run_names_its_study(self):
        import sys
        import tempfile

        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        root = _a_sweep_study(Path(tempfile.mkdtemp()))
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(session.url, wait_until="domcontentloaded")
                page.wait_for_selector("#study-runs:not([hidden]) .study-run-row", timeout=20000)
                rows = page.eval_on_selector_all(
                    "#study-runs .study-run-row",
                    "rs => rs.map(r => [r.querySelector('.study-running-name').textContent,"
                    " r.dataset.state, r.querySelector('.study-running-pct').textContent])")
                self.assertEqual(rows, [["temperature_K = 300", "finished", "finished"],
                                        ["temperature_K = 310", "running", "40%"]])
                self.assertIn("1 of 2 finished", page.text_content("#study-runs-label"))
                self.assertTrue(page.eval_on_selector("#study-run-of", "e => e.hidden"))
                # Into the running run, and back.
                page.click("#study-runs .study-run-row:nth-child(2) button")
                page.wait_for_selector("#study-run-of:not([hidden])", timeout=20000)
                self.assertEqual(page.text_content("#study-run-of-name"), "sweep")
                self.assertEqual(page.text_content("#study-run-of-values"), "temperature_K = 310")
                self.assertTrue(page.eval_on_selector("#study-runs", "e => e.hidden"))
                page.click("#study-run-of-back")
                page.wait_for_selector("#study-runs:not([hidden]) .study-run-row", timeout=20000)
                self.assertTrue(page.eval_on_selector("#study-run-of", "e => e.hidden"))
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
        self.assertIn("1 of 2 runs finished", page["html"])
        self.assertIn("1 still to run", page["html"])
        self.assertIn("rmsd mean", page["html"])
        self.assertIn("0.16", page["html"])          # the finished run's mean, 300 K
        self.assertNotIn("0.1603", page["html"])     # the running one's is not there

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
                json.dumps({"stage": "finished", "current_step": 500, "total_planned_steps": 500}),
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
        self.assertEqual(page["reason"], "none of 2 runs finished yet")
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
                json.dumps({"stage": "finished", "current_step": 500, "total_planned_steps": 500}),
                encoding="utf-8")
        build_comparison_report(root)
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
