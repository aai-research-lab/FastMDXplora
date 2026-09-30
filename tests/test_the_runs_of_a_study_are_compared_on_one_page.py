"""The runs of a study are compared on one page.

A study of several runs opened to an empty Analysis page: each run's figures
were inside the run, one at a time, and the comparison was a document written
once the last run finished. The page now shows the runs in one table, the
settings that differ between them beside the mean each recorded, a difference
marked only where it is more than twice the two runs' combined standard error
(the rule the written comparison uses for a trend), and one measure's series
from every finished run overlaid.

Replicas differ only by seed, so they are not compared run against run.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.runs_compared import RESOLVED_AT, run_folder, runs_compared


def _study(root: Path, runs: dict[str, dict], *, axis: str = "simulation.temperature_K",
           finished: tuple[str, ...] | None = None) -> Path:
    """``runs`` maps a run id to its swept value and its measures:
    ``{"value": 300, "rmsd": (mean, error, withheld)}``."""
    finished = tuple(runs) if finished is None else finished
    planned, results = [], []
    for run_id, spec in runs.items():
        planned.append({"run_id": run_id, "system": "s1", "sweep_values": {axis: spec["value"]}})
        if run_id not in finished:
            continue
        results.append({"run_id": run_id, "status": "ok"})
        folder = root / "runs" / run_id
        (folder / "analysis").mkdir(parents=True)
        (folder / "analysis" / "analysis_manifest.json").write_text(json.dumps(
            {"load_kwargs": {"stride": None, "first": None, "saving_interval_ps": 10.0}}),
            encoding="utf-8")
        for name, measured in spec.items():
            if name == "value":
                continue
            mean, error, withheld = measured
            record = {"mean": mean, "standard_error": error, "effective_samples": 40.0,
                      "discard": 0, "n_frames": 5, "unit": "nm"}
            if withheld:
                record["not_a_measurement"] = withheld
            here = folder / "analysis" / name
            here.mkdir()
            (here / "options.json").write_text(json.dumps(
                {"analysis": name, "findings": {"mean": record}}), encoding="utf-8")
            (here / f"{name}.dat").write_text(
                "".join(f"{mean + 0.001 * k:.5f}\n" for k in range(5)), encoding="utf-8")
    (root / "batch_manifest.json").write_text(json.dumps(
        {"planned": planned, "runs": results, "sweep": {axis: [s["value"] for s in runs.values()]}}),
        encoding="utf-8")
    return root


@pytest.fixture
def sweep(tmp_path) -> Path:
    return _study(tmp_path / "sweep", {
        "t300": {"value": 300, "rmsd": (0.160, 0.001, None), "rg": (1.20, 0.01, None)},
        # 0.004 against a combined error of 0.0014: resolved.
        "t310": {"value": 310, "rmsd": (0.164, 0.001, None), "rg": (1.21, 0.01, None)},
        # 0.002 against 0.0014: within twice the error.
        "t320": {"value": 320, "rmsd": (0.162, 0.001, None),
                 "rg": (1.50, float("nan"), "too short to measure")},
        "t330": {"value": 330},
    }, finished=("t300", "t310", "t320"))


class TestTheTable:
    def test_the_runs_and_what_differs_between_them(self, sweep):
        page = runs_compared(sweep)
        assert page["ok"] and page["completed"] == 3 and not page["replicas"]
        assert page["axes"] == [{"axis": "simulation.temperature_K", "label": "temperature_K"}]
        assert [(r["run_id"], r["label"], r["state"]) for r in page["runs"]] == [
            ("t300", "temperature_K 300", "completed"), ("t310", "temperature_K 310", "completed"),
            ("t320", "temperature_K 320", "completed"), ("t330", "temperature_K 330", "waiting")]
        assert [m["analysis"] for m in page["measures"]] == ["rmsd", "rg"]
        assert page["measures"][0]["unit"] == "nm"

    def test_only_a_resolved_difference_is_marked(self, sweep):
        rmsd = runs_compared(sweep)["measures"][0]
        assert rmsd["reference"] == "t300"
        cells = {c["run_id"]: c for c in rmsd["cells"]}
        assert "versus" not in cells["t300"]
        assert cells["t310"]["versus"]["resolved"] is True
        assert cells["t310"]["versus"]["difference"] == pytest.approx(0.004)
        assert cells["t310"]["versus"]["error"] == pytest.approx(0.001 * 2 ** 0.5)
        assert cells["t320"]["versus"]["resolved"] is False
        assert rmsd["said"] == (f"1 of 2 differ from temperature_K 300 by more than "
                                f"{RESOLVED_AT:g} times their combined error. 1 not yet recorded.")

    def test_a_mean_that_is_not_a_measurement_is_not_compared(self, sweep):
        rg = runs_compared(sweep)["measures"][1]
        cells = {c["run_id"]: c for c in rg["cells"]}
        assert cells["t320"]["withheld"] == "too short to measure"
        assert cells["t320"]["error"] is None and "versus" not in cells["t320"]
        assert cells["t310"]["versus"]["resolved"] is False
        assert "1 not determined, and not compared." in rg["said"]

    def test_a_run_not_finished_has_no_mean(self, sweep):
        rmsd = runs_compared(sweep)["measures"][0]
        assert rmsd["cells"][3] == {"run_id": "t330", "mean": None}

    def test_replicas_are_not_compared_run_against_run(self, tmp_path):
        page = runs_compared(_study(tmp_path / "replicas", {
            "seed1": {"value": 1, "rmsd": (0.16, 0.001, None)},
            "seed2": {"value": 2, "rmsd": (0.20, 0.001, None)}},
            axis="simulation.random_seed"))
        assert page["replicas"]
        rmsd = page["measures"][0]
        assert rmsd["reference"] is None
        assert all("versus" not in cell for cell in rmsd["cells"])

    def test_a_study_of_one_run_has_no_table(self, tmp_path):
        assert runs_compared(tmp_path) == {"ok": False, "reason": "not a study of several runs"}


class TestOneRunsSeries:
    def test_only_a_run_the_study_records(self, sweep):
        assert run_folder(sweep, "t310") == sweep / "runs" / "t310"
        assert run_folder(sweep, "../t310") is None
        assert run_folder(sweep, "nope") is None

    def test_it_is_served_by_id_and_answered_beyond_loopback(self, sweep):
        from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

        assert "/api/runs-compared" in GETS_ANSWERED_BEYOND_LOOPBACK
        session = start_dashboard_session(output=str(sweep), host="127.0.0.1", port=0)
        try:
            def get(path: str) -> dict:
                return json.loads(urllib.request.urlopen(session.url + path, timeout=10).read())

            one = get("/api/series?analysis=rmsd&run=t310")
            assert one["ok"] and one["y"][0] == pytest.approx(0.164)
            assert get("/api/series?analysis=rmsd&run=..%2Ft310") == {
                "ok": False, "reason": "no such run in this study"}
            assert get("/api/runs-compared")["completed"] == 3
        finally:
            session.server.shutdown()


def test_the_page_draws_them(sweep) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(sweep), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#runs-compared:not([hidden])")
            page.wait_for_selector('#runs-overlay-chart[data-drawn="3"]')
            head = page.locator("#runs-compared-table thead th").all_text_contents()
            varied = page.locator("#runs-compared-table thead th.is-varied").all_text_contents()
            marked = page.locator("#runs-compared-table .is-resolved").count()
            differs = page.locator('#runs-compared-table tr[data-run="t310"] .runs-compared-mark')
            differs_text = differs.text_content()
            differs_title = differs.get_attribute("title")
            withheld = page.locator('#runs-compared-table tr[data-run="t320"] .is-withheld')
            withheld_text = withheld.text_content()
            lines = page.locator("#runs-overlay-chart .runs-overlay-line").count()
            legend = page.locator("#runs-overlay-legend .runs-overlay-key").all_text_contents()
            empty = page.is_visible("#analysis-empty")
            page.select_option("#runs-overlay-measure", "rg")
            page.wait_for_selector('#runs-overlay-chart[data-analysis="rg"][data-drawn="3"]')
            browser.close()
    finally:
        session.server.shutdown()
    assert head == ["Run", "temperature_K", "RMSD (nm)", "Radius of gyration (nm)"]
    assert varied == ["temperature_K"]
    assert marked == 1
    assert differs_text == "differs"
    assert differs_title.startswith("Differs from temperature_K 300 by +0.0040 ± 0.0014 nm")
    assert withheld_text == "1.500 *"
    assert lines == 3
    assert legend == ["temperature_K 300", "temperature_K 310", "temperature_K 320"]
    assert not empty
    assert errors == []
