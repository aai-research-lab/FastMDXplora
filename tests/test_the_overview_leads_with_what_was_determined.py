"""The Overview leads with what a study determined, then how its run went.

It opened on a health card that said "Completed" with "Ok" beside it, six
charts the full width of the page plotted against the sample index, seven
cards repeating the health card's numbers, a table of phases each marked
"Ok", and, last, what the analyses determined. It now opens on the study in
a line, then each mean the analyses recorded as a tile, then the run (the
molecule, its phases and how long each took), then its thermodynamics two a
row on the production's clock, equilibration before 0, each headed by its
mean over the production with the error the analyses would give it. A study
still running keeps its health and charts first.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.gui import overview_view
from fastmdxplora.gui.overview_view import MOST_TILES, overview_payload, thinned_metrics
from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest

HEADER = ("timestamp,stage,step,simulation_time_ns,potential_energy,kinetic_energy,"
          "total_energy,temperature,volume,density,speed,pressure,current_frame_count,"
          "progress_percent\n")


def _metrics(root: Path, production: int = 400, seed: int = 7) -> np.ndarray:
    """The live record as a run writes it: minimization, 0.001 ns each of
    NVT and NPT at 2 fs, then the production; returns its temperatures."""
    rng = np.random.default_rng(seed)
    lines = []
    step = 0

    def row(stage: str, temperature: float) -> str:
        t = step * 2e-6
        return (f"2026-10-05T12:00:00+00:00,{stage},{step},{t},{-45000 + rng.normal()},"
                f"7600,{-37400 + rng.normal()},{temperature},37.3,{1.02 + 0.001 * rng.normal()},"
                f"120,,,0\n")

    lines.append(row("minimization", 0.0))
    for stage in ("NVT equilibration", "NPT equilibration"):
        for _ in range(5):
            step += 100
            lines.append(row(stage, 250.0))
    temperatures = 300.0 + 1.5 * _ar1(production, 0.3, seed)
    for value in temperatures:
        step += 10
        lines.append(row("production", float(value)))
    folder = root / "simulation"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "live_metrics.csv").write_text(HEADER + "".join(lines), encoding="utf-8")
    return temperatures


def _status(root: Path, status: str = "completed", planned: bool = True) -> None:
    record = {"status": status, "stage": "production", "current_step": 5000,
              "total_steps": 5000, "target_temperature_K": 300.0}
    if planned:
        record.update(nvt_steps_planned=500, npt_steps_planned=500, timestep_fs=2)
    (root / "simulation" / "live_status.json").write_text(json.dumps(record), encoding="utf-8")


def _phases(root: Path) -> None:
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "analysis", "status": "ok", "started_at": "2026-10-05T12:10:00Z",
         "finished_at": "2026-10-05T12:11:30Z"},
        {"name": "setup", "status": "ok", "started_at": "2026-10-05T12:00:00Z",
         "finished_at": "2026-10-05T12:00:42Z"},
        {"name": "simulation", "status": "ok", "started_at": "2026-10-05T12:00:42Z",
         "finished_at": "2026-10-05T12:10:00Z"},
    ]}), encoding="utf-8")


@pytest.fixture
def study(tmp_path: Path) -> Path:
    root = tmp_path / "study"
    _manifest(root, {"rmsd": {"status": "ok"}, "rg": {"status": "ok"},
                     "rmsf": {"status": "ok"}, "sasa": {"status": "ok"}})
    _analysis(root, "rmsd", 0.30 + 0.002 * _ar1(4000, 0.5, 1))
    _analysis(root, "rg", 1.60 + 0.01 * _ar1(300, 0.95, 2, transient=3.0))
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    _analysis(root, "sasa", 91.0 + 0.3 * _ar1(4000, 0.5, 3), unit="nm²",
              others={"hydrophobic_sasa": 45.0 + 0.1 * _ar1(4000, 0.5, 4)})
    _metrics(root)
    _status(root)
    _phases(root)
    return root


class TestWhatWasDetermined:

    def test_each_mean_is_a_tile_said_as_the_analysis_page_says_it(self, study) -> None:
        from fastmdxplora.gui.analysis_overview import overview_of

        tiles = overview_payload(study)["tiles"]
        by = {(t["analysis"], t["label"]): t for t in tiles}
        rows = {row["analysis"]: row for row in overview_of(study)["rows"]}
        rmsd = by[("rmsd", rows["rmsd"]["quantities"][0]["label"])]
        assert rmsd["said"] == rows["rmsd"]["mean"]["said"]
        assert rmsd["determined"] is True
        assert rmsd["from_ns"] == rows["rmsd"]["mean"]["from_ns"]

    def test_a_withheld_mean_says_why(self, study) -> None:
        rg = next(t for t in overview_payload(study)["tiles"] if t["analysis"] == "rg")
        assert rg["determined"] is False
        assert rg["why"]

    def test_only_a_series_of_its_own_is_plotted_beside_a_mean(self, study) -> None:
        tiles = overview_payload(study)["tiles"]
        series = {(t["analysis"], t.get("series")) for t in tiles if t["kind"] == "mean"}
        assert ("rmsd", "rmsd") in series and ("rg", "rg") in series and ("sasa", "sasa") in series
        # Hydrophobic SASA is a mean of its own, not the SASA's series.
        hydrophobic = [t for t in tiles if t["label"] == "Hydrophobic SASA"]
        assert hydrophobic and hydrophobic[0]["series"] is None

    def test_rmsf_is_said_over_its_residues_without_an_error(self, study) -> None:
        rmsf = next(t for t in overview_payload(study)["tiles"] if t["analysis"] == "rmsf")
        assert rmsf["kind"] == "per_residue"
        assert rmsf["count"] == 3 and rmsf["each"] == "residue"
        assert rmsf["values"] == [0.05, 0.07, 0.06]
        assert rmsf["said"].endswith(" nm") and "±" not in rmsf["said"]

    def test_the_counts_say_how_many_were_determined(self, study) -> None:
        payload = overview_payload(study)
        means = [t for t in payload["tiles"] if t["kind"] == "mean"]
        assert payload["means"] == len(means)
        assert payload["determined"] == sum(t["determined"] for t in means)
        assert payload["analyses"] == 4

    def test_at_most_nine_are_shown_and_the_rest_counted(self, tmp_path, monkeypatch) -> None:
        import fastmdxplora.gui.analysis_overview as analysis_overview

        rows = [{"analysis": f"a{i}", "quantities": [
            {"key": "mean", "label": f"A{i}", "said": "1 nm", "value": 1.0, "determined": True}]}
            for i in range(MOST_TILES + 3)]
        monkeypatch.setattr(analysis_overview, "overview_of", lambda root: {"rows": rows})
        payload = overview_payload(tmp_path)
        assert len(payload["tiles"]) == MOST_TILES
        assert payload["more"] == 3


class TestTheProductionsClock:

    def test_production_begins_after_the_equilibration_planned(self, study) -> None:
        thermo = overview_payload(study)["thermodynamics"]
        assert thermo["production_start_ns"] == pytest.approx(0.002)
        assert thermo["target_temperature_K"] == 300.0

    def test_without_a_plan_it_begins_where_the_record_says(self, tmp_path) -> None:
        root = tmp_path / "study"
        _metrics(root)
        _status(root, planned=False)
        # The last time written before the first production sample.
        assert overview_payload(root)["thermodynamics"]["production_start_ns"] == pytest.approx(
            0.002)

    def test_a_mean_is_over_the_production_as_the_analyses_take_one(self, tmp_path) -> None:
        from fastmdxplora.statistics import mean_record, with_its_error

        root = tmp_path / "study"
        temperatures = _metrics(root, production=2000)
        _status(root)
        said = overview_payload(root)["thermodynamics"]["means"]["temperature"]
        record = mean_record(temperatures)
        assert said["mean"] == pytest.approx(record["mean"])
        assert said["determined"] is True
        assert said["said"] == with_its_error(record["mean"], record["standard_error"]) + " K"
        # The equilibration at 250 K is not in it.
        assert said["of"] == 2000

    def test_too_few_samples_are_not_given_an_error(self, tmp_path) -> None:
        root = tmp_path / "study"
        _metrics(root, production=8)
        _status(root)
        said = overview_payload(root)["thermodynamics"]["means"]["temperature"]
        assert said["determined"] is False and said["error"] is None
        assert said["why"]
        assert "±" not in said["said"]

    def test_a_large_value_is_grouped_not_an_exponent(self) -> None:
        assert overview_view._four_figures(-45472.31) == "-45,472"
        assert overview_view._four_figures(1.02617) == "1.026"


class TestOneMeanForOneQuantity:

    def test_the_thermodynamics_analysis_s_own_mean_is_given(self, study) -> None:
        folder = study / "analysis" / "thermodynamics"
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"findings": {"thermodynamics": {
            "samples": 5000, "ensemble": "constant pressure",
            "temperature": {"units": "K", "mean": 300.4, "standard_error": 0.12,
                            "effective_samples": 210.0, "discard": 40}}}}), encoding="utf-8")
        said = overview_payload(study)["thermodynamics"]["means"]["temperature"]
        assert said["mean"] == 300.4 and said["determined"] is True
        assert said["samples"] == 210.0 and said["of"] == 5000
        # The rest are still taken from the live record.
        assert overview_payload(study)["thermodynamics"]["means"]["potential_energy"]

    def test_a_density_with_its_volume_held_is_given_no_mean(self, tmp_path) -> None:
        root = tmp_path / "study"
        _metrics(root, production=2000)
        _status(root)
        # The live record's volume column is the same in every row here.
        said = overview_payload(root)["thermodynamics"]["means"]["density"]
        assert said["determined"] is False and said["error"] is None
        assert "volume did not change" in said["why"]


class TestADamagedRecord:

    def test_costs_its_part_of_the_page_not_the_page(self, study) -> None:
        (study / "simulation" / "live_metrics.csv").write_bytes(
            HEADER.encode() + b"2026-10-05,production,1,0.1,\x00\x00garbage\n")
        payload = overview_payload(study)
        assert payload["ok"] and payload["tiles"]
        assert payload["thermodynamics"]["means"]["temperature"] is None


class TestThePhases:

    def test_in_order_with_how_long_each_took(self, study) -> None:
        phases = overview_payload(study)["phases"]
        assert [p["name"] for p in phases] == ["setup", "simulation", "analysis"]
        assert [p["seconds"] for p in phases] == [42.0, 558.0, 90.0]


class TestReadOnlyWhenChanged:

    def test_the_same_payload_until_a_record_changes(self, study) -> None:
        first = overview_payload(study)
        assert overview_payload(study) is first
        _metrics(study, production=500, seed=9)
        assert overview_payload(study) is not first


class TestTheChartsSeeTheWholeRun:

    def test_thinning_keeps_both_ends_and_each_change_of_stage(self) -> None:
        rows = ([{"stage": "NVT", "i": i} for i in range(50)]
                + [{"stage": "NPT", "i": i} for i in range(50, 100)]
                + [{"stage": "production", "i": i} for i in range(100, 5000)])
        kept = thinned_metrics(rows, 600)
        index = [row["i"] for row in kept]
        assert index[0] == 0 and index[-1] == 4999
        assert {49, 50, 99, 100} <= set(index)
        assert len(kept) <= 604
        assert index == sorted(index)

    def test_a_short_record_is_left_as_it_is(self) -> None:
        rows = [{"stage": "production", "i": i} for i in range(10)]
        assert thinned_metrics(rows, 600) is rows

    def test_the_server_answers_both(self, study) -> None:
        from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

        assert "/api/overview" in GETS_ANSWERED_BEYOND_LOOPBACK
        _metrics(study, production=3000)
        session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
        try:
            overview = json.loads(urllib.request.urlopen(
                session.url + "/api/overview", timeout=60).read())
            metrics = json.loads(urllib.request.urlopen(
                session.url + "/api/metrics", timeout=60).read())
            fewer = json.loads(urllib.request.urlopen(
                session.url + "/api/metrics?most=100", timeout=60).read())["metrics"]
        finally:
            session.server.shutdown()
        assert overview["ok"] and overview["tiles"]
        rows = metrics["metrics"]
        # The whole run, thinned: the minimization first, not the newest 500.
        assert rows[0]["stage"] == "minimization"
        assert len(rows) <= 610
        # As many as the person's setting keeps.
        assert rows[0]["stage"] == fewer[0]["stage"] and 100 <= len(fewer) <= 110


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------

def _browser_study(root: Path, status: str) -> Path:
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(root)
    # Its production is the live record written below: the Overview averages
    # production's energy file where a run wrote one, and the study borrowed
    # here wrote a short one of its own.
    (root / "simulation" / "energy.csv").unlink(missing_ok=True)
    _manifest(root, {"rmsd": {"status": "ok"}, "rg": {"status": "ok"},
                     "rmsf": {"status": "ok"}})
    _analysis(root, "rmsd", 0.30 + 0.002 * _ar1(2000, 0.5, 1))
    _analysis(root, "rg", 1.60 + 0.01 * _ar1(300, 0.95, 2, transient=3.0))
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    _metrics(root)
    _status(root, status=status)
    _phases(root)
    return root


def _open(root: Path, check):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#overview-tiles .overview-tile")
            try:
                return check(page), errors
            finally:
                browser.close()
    finally:
        session.server.shutdown()


def _tops(page) -> dict:
    return page.evaluate("""() => Object.fromEntries(
        ['overview-verdict', 'overview-results', 'overview-health-card', 'overview-run',
         'overview-charts'].map((id) => {
            const box = document.getElementById(id).getBoundingClientRect();
            return [id, box.height ? box.top : null];
        }))""")


def test_an_ended_study_leads_with_what_it_determined(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    root = _browser_study(tmp_path / "study", "completed")

    def check(page):
        page.wait_for_function(
            "() => document.getElementById('overview-body').dataset.run === 'ended'")
        page.wait_for_selector("#overview-tiles .tile-plot svg path.spark-line")
        tops = _tops(page)
        tiles = page.eval_on_selector_all(
            "#overview-tiles .overview-tile", "(all) => all.map((t) => t.dataset.tileAnalysis)")
        phases = page.eval_on_selector_all(
            "#overview-phases .overview-phase", "(all) => all.map((p) => p.textContent)")
        header = page.text_content('[data-chart-value="temperature"]')
        page.click('#overview-tiles [data-tile-analysis="rg"]')
        page.wait_for_function("() => document.documentElement.dataset.page === 'analysis'")
        return tops, tiles, phases, header

    (tops, tiles, phases, header), errors = _open(root, check)
    assert not errors, errors
    assert tops["overview-verdict"] < tops["overview-results"] < tops["overview-run"]
    assert tops["overview-run"] < tops["overview-charts"]
    # A study that ended well says so in its line; no health card.
    assert tops["overview-health-card"] is None
    assert tiles[:2] == ["rmsd", "rg"] and "rmsf" in tiles
    assert phases == ["Setup42 s", "Simulation9m 18s", "Analysis1m 30s"]
    # The production's mean, with its error, rather than its last sample.
    assert "±" in header


def test_a_running_study_keeps_its_health_and_charts_first(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    root = _browser_study(tmp_path / "study", "running")

    def check(page):
        page.wait_for_function(
            "() => document.getElementById('overview-body').dataset.run === 'running'")
        return _tops(page)

    tops, errors = _open(root, check)
    assert not errors, errors
    assert tops["overview-health-card"] < tops["overview-charts"] < tops["overview-run"]
    assert tops["overview-charts"] < tops["overview-results"]


@pytest.mark.parametrize("ended", ["failed", "stopped"])
def test_a_study_that_stopped_short_leads_with_what_happened(tmp_path, ended) -> None:
    pytest.importorskip("playwright.sync_api")
    root = _browser_study(tmp_path / "study", ended)

    def check(page):
        page.wait_for_function(
            "() => document.getElementById('overview-body').dataset.lead === 'health'")
        return _tops(page)

    tops, errors = _open(root, check)
    assert not errors, errors
    assert tops["overview-health-card"] < tops["overview-charts"] < tops["overview-results"]


def test_one_time_is_pointed_at_on_every_plot(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    root = _browser_study(tmp_path / "study", "completed")

    def check(page):
        page.wait_for_selector("#overview-tiles .tile-plot svg path.spark-line")
        page.evaluate("""() => {
            window.__pointed = [];
            window.addEventListener('fmx:crosshair', (e) => window.__pointed.push(e.detail.t_ns));
        }""")
        canvas = page.locator('canvas[data-chart="temperature"]')
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2)
        page.wait_for_function("() => window.__pointed.some((t) => t != null)")
        pointed = page.evaluate("() => window.__pointed.filter((t) => t != null).pop()")
        crosses = page.eval_on_selector_all(
            "#overview-tiles .spark-cross", "(all) => all.map((c) => c.style.display)")
        page.mouse.move(5, 5)
        page.wait_for_function("() => window.__pointed[window.__pointed.length - 1] == null")
        return pointed, crosses

    (pointed, crosses), errors = _open(root, check)
    assert not errors, errors
    # A time on the production's clock, inside the production.
    assert 0 < pointed
    assert "" in crosses


def test_the_copy_is_not_in_the_fold_s_summary() -> None:
    """A button inside a summary is a control inside a control: a screen
    reader names the two as one, and a press on it opened the fold."""
    import re

    from fastmdxplora.gui import server

    page = (Path(server.__file__).parent / "templates" / "dashboard.html").read_text(
        encoding="utf-8")
    fold = re.search(r'<details class="card overview-methods".*?</details>', page, re.S).group(0)
    summary = fold[fold.index("<summary"):fold.index("</summary>")]
    assert "<button" not in summary
    assert 'id="overview-methods-copy"' in fold
