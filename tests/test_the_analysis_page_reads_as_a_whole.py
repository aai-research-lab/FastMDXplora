"""The Analysis page reads as a whole: what the study determined, where each
analysis is, and how each series converged.

The page showed every analysis as a figure with a caption, so what a study
found, and which of it holds, needed every card read in turn. It now opens
with one table of what each analysis recorded (`analysis_overview.py`), an
index of the sections by what they study with a filter, and under any time
series its convergence: the running mean, the block averages, the
correlation and the distribution, from the estimator the recorded error
comes from. The fixtures are written by the producing code
(`statistics.mean_record`), as the analyses write them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.gui.analysis_overview import convergence_payload, overview_of
from fastmdxplora.statistics import mean_record, with_its_error

INTERVAL_PS = 10.0


def _ar1(n: int, phi: float, seed: int, transient: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal()
    return x + transient * np.exp(-np.arange(n) / 40.0)


def _manifest(root: Path, results: dict) -> None:
    folder = root / "analysis"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": INTERVAL_PS},
        "results": results,
    }), encoding="utf-8")


def _analysis(root: Path, name: str, series: np.ndarray | None, unit: str = "nm",
              others: dict | None = None, rows: str | None = None) -> dict:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    findings: dict = {}
    if series is not None:
        record = mean_record(series, frame_interval_ns=INTERVAL_PS / 1000.0)
        record["unit"] = unit
        findings["mean"] = record
        (folder / f"{name}.dat").write_text(
            f"# {name}\n" + "".join(f"{v:.8f}\n" for v in series), encoding="utf-8")
    elif rows is not None:
        (folder / f"{name}.dat").write_text(rows, encoding="utf-8")
    for key, values in (others or {}).items():
        record = mean_record(values, frame_interval_ns=INTERVAL_PS / 1000.0)
        record["unit"] = "nm²"
        findings[key] = record
    (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": findings}),
                                         encoding="utf-8")
    return findings


@pytest.fixture
def study(tmp_path: Path) -> Path:
    root = tmp_path / "study"
    long_and_resolved = 0.30 + 0.002 * _ar1(4000, 0.5, 1)
    short_and_correlated = 1.60 + 0.01 * _ar1(300, 0.95, 2, transient=3.0)
    _manifest(root, {
        "rmsd": {"status": "ok"}, "rg": {"status": "ok"}, "rmsf": {"status": "ok"},
        "sasa": {"status": "ok"},
        "order_parameters": {"status": "failed", "message": (
            "order_parameters: No backbone amide N--H pairs were found. Prepare the "
            "system with hydrogens.")},
    })
    _analysis(root, "rmsd", long_and_resolved)
    _analysis(root, "rg", short_and_correlated)
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    _analysis(root, "sasa", 91.0 + 0.3 * _ar1(4000, 0.5, 3), unit="nm²",
              others={"hydrophobic_sasa": 45.0 + 0.1 * _ar1(4000, 0.5, 4)})
    (root / "analysis" / "sasa" / "sasa_polar_split.csv").write_text("frame,x\n0,1\n")
    (root / "analysis" / "order_parameters").mkdir(parents=True)
    return root


def _rows(root: Path) -> dict:
    return {row["analysis"]: row for row in overview_of(root)["rows"]}


class TestWhatTheTableSays:

    def test_a_determined_mean_is_said_to_the_place_its_error_allows(self, study) -> None:
        row = _rows(study)["rmsd"]
        record = json.loads((study / "analysis/rmsd/options.json").read_text())["findings"]["mean"]
        mean = row["mean"]
        assert mean["determined"] is True and mean["why"] is None
        assert mean["said"] == with_its_error(record["mean"], record["standard_error"]) + " nm"
        assert mean["samples"] == int(round(record["effective_samples"]))
        assert (mean["from_frame"], mean["of_frames"]) == (record["discard"], 4000)
        # Frame k of the file was written at (k + 1) saving intervals.
        assert mean["from_ns"] == pytest.approx((record["discard"] + 1) * INTERVAL_PS / 1000.0)

    def test_a_withheld_mean_says_why(self, study) -> None:
        mean = _rows(study)["rg"]["mean"]
        record = json.loads((study / "analysis/rg/options.json").read_text())["findings"]["mean"]
        assert record.get("not_a_measurement"), "the fixture should be too short to determine"
        assert mean["determined"] is False
        assert mean["why"] == record["not_a_measurement"]

    def test_every_quantity_recorded_is_listed_by_its_name(self, study) -> None:
        quantities = _rows(study)["sasa"]["quantities"]
        assert [(q["key"], q["label"]) for q in quantities] == [
            ("mean", "Solvent Accessible Surface Area"), ("hydrophobic_sasa", "Hydrophobic SASA")]
        assert quantities[1]["unit"] == "nm²"

    def test_a_failure_says_its_reason_without_the_name_before_it(self, study) -> None:
        row = _rows(study)["order_parameters"]
        assert row["status"] == "failed"
        assert row["message"].startswith("No backbone amide N--H pairs were found.")

    def test_a_profile_is_a_result_with_no_mean(self, study) -> None:
        row = _rows(study)["rmsf"]
        assert (row["kind"], row["mean"], row["quantities"]) == ("result", None, [])

    def test_the_rows_are_in_the_page_s_order_and_name_their_theme(self, study) -> None:
        rows = overview_of(study)["rows"]
        assert [r["analysis"] for r in rows] == ["rmsd", "rg", "rmsf", "order_parameters", "sasa"]
        assert rows[0]["theme"] == "Structure and stability"
        assert rows[0]["anchor"] == "rmsd"

    def test_the_data_files_are_listed_its_own_first(self, study) -> None:
        names = [f["name"] for f in _rows(study)["sasa"]["data"]]
        assert names == ["sasa.dat", "sasa_polar_split.csv"]
        assert _rows(study)["sasa"]["data"][0]["href"] == "/artifacts/analysis/sasa/sasa.dat"

    def test_a_biased_run_gives_the_reweighted_mean(self, study) -> None:
        folder = study / "analysis" / "reweighted"
        folder.mkdir(parents=True)
        (folder / "reweighted_averages.json").write_text(json.dumps({"quantities": [
            {"analysis": "rmsd", "reweighted_mean": 0.31234, "reweighted_standard_error": 0.004,
             "independent_samples": 41.0}]}), encoding="utf-8")
        data = overview_of(study)
        rows = {row["analysis"]: row for row in data["rows"]}
        assert data["biased"] is True
        assert rows["rmsd"]["mean"]["said"] == with_its_error(0.31234, 0.004) + " nm"
        assert rows["rmsd"]["mean"]["reweighted"] is True
        # A series the reweighting did not recover is not given as equilibrium.
        assert rows["rg"]["mean"]["determined"] is False
        assert "biased" in rows["rg"]["mean"]["why"]


class TestWhileAnalysesArrive:

    def test_one_still_computing_is_said_to_be(self, tmp_path) -> None:
        """Before the analysis phase records its results, a folder holds an
        analysis finished or one still computing: its options are written
        before it computes, its figure after."""
        root = tmp_path / "running"
        (root / "analysis").mkdir(parents=True)
        (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
            {"load_kwargs": {"saving_interval_ps": INTERVAL_PS}}))
        _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(2000, 0.5, 1))
        (root / "analysis" / "rmsd" / "rmsd.png").write_bytes(b"\x89PNG")
        _analysis(root, "sasa", None)
        data = overview_of(root)
        rows = {row["analysis"]: row for row in data["rows"]}
        assert data["complete"] is False
        assert rows["rmsd"]["status"] == "done"
        assert rows["sasa"]["status"] == "running"

    def test_once_recorded_every_analysis_is_what_the_manifest_says(self, study) -> None:
        assert overview_of(study)["complete"] is True
        assert {row["status"] for row in overview_of(study)["rows"]} == {"done", "failed"}


class TestTheConvergence:

    def test_it_reads_the_series_as_the_record_was_made(self, study) -> None:
        data = convergence_payload(study, "rmsd")
        record = json.loads((study / "analysis/rmsd/options.json").read_text())["findings"]["mean"]
        assert data["ok"] is True
        assert data["equilibration"]["discard_frames"] == record["discard"]
        assert data["equilibration"]["mean"] == pytest.approx(record["mean"], rel=1e-9)
        assert data["equilibration"]["standard_error"] == pytest.approx(
            record["standard_error"], rel=1e-9)
        assert data["recorded"]["same_start"] is True
        assert (data["unit"], data["x_label"], data["time_unit"]) == ("nm", "Time (ns)", "ns")
        assert len(data["running_mean"]["mean"]) > 10
        assert data["blocking"]["block_length"][:3] == [1, 2, 4]

    def test_a_shared_start_is_said_rather_than_replaced(self, study) -> None:
        options = study / "analysis/rmsd/options.json"
        record = json.loads(options.read_text())
        record["findings"]["mean"]["discard"] = 1500
        record["findings"]["mean"]["start_shared_with_replicas"] = 1500
        options.write_text(json.dumps(record), encoding="utf-8")
        recorded = convergence_payload(study, "rmsd")["recorded"]
        assert recorded["same_start"] is False
        assert recorded["start_shared_with_replicas"] is True

    def test_an_error_the_analysis_withheld_stays_withheld(self, study) -> None:
        """end_to_end withholds the error of a chain reaching its own
        periodic image, which the series alone cannot show; the view gave
        it back as 3.421 +/- 0.0015 nm."""
        options = study / "analysis/rmsd/options.json"
        record = json.loads(options.read_text())
        mean = record["findings"]["mean"]
        mean["not_a_measurement"] = "The chain comes within 1.0 nm of its own periodic image."
        mean["error_withheld_because"] = "chain reaches its periodic image"
        mean["standard_error"] = float("nan")
        options.write_text(json.dumps(record), encoding="utf-8")
        recorded = convergence_payload(study, "rmsd")["recorded"]
        assert recorded["standard_error"] is None
        assert recorded["error_withheld_because"] == "chain reaches its periodic image"
        assert recorded["not_a_measurement"].startswith("The chain comes within")

    def test_a_record_with_no_mean_has_no_series(self, study) -> None:
        """The moments of a molecule broken across the box keep only a
        reason under `mean`; the last column of their file is I3."""
        folder = study / "analysis" / "moments_of_inertia"
        folder.mkdir(parents=True)
        (folder / "moments_of_inertia.dat").write_text(
            "".join(f"{60 + i % 3} {65 + i % 5} {70 + i % 7}\n" for i in range(200)))
        (folder / "options.json").write_text(json.dumps({"findings": {"mean": {
            "n_frames": 200, "not_a_measurement": "The molecule is broken across the box."}}}))
        assert convergence_payload(study, "moments_of_inertia")["ok"] is False
        record = json.loads((folder / "options.json").read_text())
        record["findings"]["mean"]["mean"] = 65.0
        (folder / "options.json").write_text(json.dumps(record))
        said = convergence_payload(study, "moments_of_inertia")
        assert said["ok"] is False and "several quantities" in said["reason"]

    def test_what_has_no_series_says_so(self, study) -> None:
        assert convergence_payload(study, "rmsf")["ok"] is False
        assert "no mean" in convergence_payload(study, "rmsf")["reason"]
        assert convergence_payload(study, "../etc")["reason"] == "not an analysis name"
        assert "no data file" in convergence_payload(study, "dimred")["reason"]


class TestTheRoutes:

    def test_they_answer_and_are_open_to_whoever_watches(self, study) -> None:
        import urllib.request

        from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

        assert {"/api/analysis-overview", "/api/convergence"} <= GETS_ANSWERED_BEYOND_LOOPBACK
        session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
        try:
            with urllib.request.urlopen(session.url.rstrip("/") + "/api/analysis-overview") as reply:
                rows = json.loads(reply.read())["rows"]
            assert rows[0]["analysis"] == "rmsd"
            with urllib.request.urlopen(session.url.rstrip("/") + "/api/convergence?analysis=rmsd") as reply:
                assert json.loads(reply.read())["ok"] is True
        finally:
            session.server.shutdown()


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path_factory.mktemp("whole") / "study"
    _manifest(root, {"rmsd": {"status": "ok"}, "rg": {"status": "ok"},
                     "order_parameters": {"status": "failed", "message": (
                         "order_parameters: No backbone amide N--H pairs were found. "
                         "Prepare the system with hydrogens.")}})
    _analysis(root, "rmsd", 0.30 + 0.002 * _ar1(2000, 0.5, 1))
    _analysis(root, "rg", 1.60 + 0.01 * _ar1(300, 0.95, 2, transient=3.0))
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    for name in ("rmsd", "rg"):
        _figure(root / "analysis" / name / f"{name}.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.errors = errors
        opened.goto(dashboard.url + "#analysis", wait_until="domcontentloaded")
        opened.wait_for_selector("#analysis-results:not([hidden])", timeout=60000)
        opened.wait_for_selector("#analysis-index:not([hidden])", timeout=60000)
        yield opened
        browser.close()


class TestThePage:

    def test_the_table_says_what_each_analysis_determined(self, page) -> None:
        body = page.locator("#analysis-results-body")
        rmsd = body.locator('tr[data-analysis="rmsd"]')
        assert rmsd.locator(".analysis-results-status").text_content() == "Determined"
        rg = body.locator('tr[data-analysis="rg"]')
        assert rg.get_attribute("data-status") == "undetermined"
        rg.locator("summary").click()
        assert "correlation time" in rg.locator(".analysis-results-why p").text_content()
        failed = body.locator('tr[data-analysis="order_parameters"]')
        assert failed.locator(".analysis-results-other").text_content() == (
            "No backbone amide N--H pairs were found.")
        themes = body.locator(".analysis-results-theme-row").all_text_contents()
        assert themes == ["Structure and stability", "Flexibility"]
        assert page.errors == []

    def test_a_name_in_the_table_goes_to_its_section(self, page) -> None:
        page.locator('#analysis-results-body tr[data-analysis="rg"] a').click()
        page.wait_for_function(
            "() => document.activeElement && document.activeElement.closest('#analysis-section-radius-of-gyration')",
            timeout=10000)

    def test_the_index_groups_by_theme_and_the_filter_finds_by_name(self, page) -> None:
        index = page.locator("#analysis-index")
        assert index.locator(".analysis-index-theme-name").all_text_contents() == [
            "Structure and stability"]
        page.fill("#analysis-filter", "gyration")
        assert page.locator("#analysis-section-rmsd").is_hidden()
        assert page.locator("#analysis-section-radius-of-gyration").is_visible()
        assert page.locator("#analysis-index-count").text_content() == "1 of 2 analyses match"
        page.fill("#analysis-filter", "nothing like it")
        assert page.locator("#analysis-filter-none").is_visible()
        page.fill("#analysis-filter", "")
        assert page.locator("#analysis-section-rmsd").is_visible()

    def test_a_series_shows_how_it_converged(self, page) -> None:
        button = page.locator('#analysis-sections [data-convergence="rmsd"]')
        button.click()
        panel = page.locator('#analysis-sections .convergence-panel[data-analysis="rmsd"]')
        page.wait_for_function(
            "() => document.querySelectorAll('.convergence-panel[data-analysis=\"rmsd\"] svg').length === 4",
            timeout=20000)
        assert button.get_attribute("aria-expanded") == "true"
        said = panel.locator(".convergence-said").text_content()
        assert said.startswith("Equilibrated from") or said.startswith("No equilibration period")
        assert "independent samples" in said
        assert panel.locator(".convergence-level").count() >= 2
        assert page.errors == []

    def test_a_card_links_its_data(self, page) -> None:
        link = page.locator('#analysis-sections .analysis-card[data-analysis="rmsd"] [data-data-file]')
        assert link.get_attribute("href") == "/artifacts/analysis/rmsd/rmsd.dat"

    def test_the_table_downloads_as_csv(self, page) -> None:
        with page.expect_download() as caught:
            page.locator("[data-results-download]").click()
        text = Path(caught.value.path()).read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0].startswith("Analysis,Quantity,Mean,Standard error,Unit")
        assert lines[1].startswith("RMSD,Mean,")
        assert len(lines) == 3
