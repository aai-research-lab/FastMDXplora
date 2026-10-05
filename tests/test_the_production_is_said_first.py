"""How long a study ran is said by its production, its equilibration beside.

The Overview's health card said "Simulation time 0.11 ns" for 100 ps of
production after 5 ps of NVT and 5 ps of NPT, and the card under it said
"0.1 ns": two lengths for one study, neither labelled. Production is what
the analyses average. And its Wall time card read 2h 57m for a run of 18
minutes analysed again that evening: it counted from the first phase's
start to the last phase's finish.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.gui.simulated_time import equilibration_said, say_length, simulated_times
from tests.test_live_status import a_watched_run  # noqa: F401 - a fixture


def _study(root: Path, *, actual: float | None = 0.1) -> Path:
    (root / "simulation").mkdir(parents=True)
    record = {"parameters": {"duration_ns": 0.1, "timestep_fs": 2.0},
              "resolved": {"nvt_steps": 2500, "npt_steps": 2500, "production_steps": 50000}}
    if actual is not None:
        record["duration_ns_actual"] = actual
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(record))
    return root


class TestSayingALength:

    @pytest.mark.parametrize("ns,said", [
        (0.1, "100 ps"), (0.11, "110 ps"), (0.0125, "12.5 ps"), (0.005, "5 ps"),
        (0.0, "0 ps"), (1.0, "1 ns"), (2.5, "2.5 ns"), (10.0, "10 ns"), (None, "—")])
    def test_picoseconds_under_a_nanosecond(self, ns, said) -> None:
        assert say_length(ns) == said


class TestTheTimes:

    def test_a_finished_run_says_its_production_and_how_it_was_equilibrated(self, tmp_path) -> None:
        root = _study(tmp_path)
        times = simulated_times(root, {"status": "completed", "stage": "report",
                                       "current_step": 55000, "timestep_fs": 2.0,
                                       "simulation_time_completed_ns": 0.11})
        assert times["production_ns"] == pytest.approx(0.1)
        assert times["equilibration_planned_ns"] == pytest.approx(0.01)
        assert not times["equilibrating"]
        assert equilibration_said(times) == "after 10 ps of equilibration (5 ps NVT, 5 ps NPT)"

    def test_while_it_equilibrates_production_has_not_begun(self, tmp_path) -> None:
        tmp_path.joinpath("simulation").mkdir()
        times = simulated_times(tmp_path, {
            "status": "running", "stage": "npt", "current_step": 3000, "timestep_fs": 2.0,
            "nvt_steps_planned": 2500, "npt_steps_planned": 2500,
            "production_steps_planned": 50000, "simulation_time_completed_ns": 0.006})
        assert times["equilibrating"] and times["stage"] == "npt"
        assert times["production_ns"] == 0.0
        assert times["equilibration_ns"] == pytest.approx(0.006)
        assert times["production_planned_ns"] == pytest.approx(0.1)

    def test_in_production_it_counts_from_where_production_began(self, tmp_path) -> None:
        tmp_path.joinpath("simulation").mkdir()
        times = simulated_times(tmp_path, {
            "status": "running", "stage": "production", "current_step": 30000,
            "timestep_fs": 2.0, "nvt_steps_planned": 2500, "npt_steps_planned": 2500,
            "production_steps_planned": 50000})
        assert times["production_ns"] == pytest.approx(0.05)
        assert not times["equilibrating"]

    def test_a_run_that_kept_no_plan_has_its_total_alone(self, tmp_path) -> None:
        tmp_path.joinpath("simulation").mkdir()
        times = simulated_times(tmp_path, {"status": "running", "stage": "production",
                                           "simulation_time_completed_ns": 0.012})
        assert times["production_ns"] is None and times["simulated_ns"] == 0.012
        assert equilibration_said(times) == ""

    def test_the_runner_records_each_stage_s_steps(self, a_watched_run) -> None:  # noqa: F811
        status = json.loads((a_watched_run.out / "live_status.json").read_text())
        assert (status["nvt_steps_planned"], status["npt_steps_planned"],
                status["production_steps_planned"]) == (
            a_watched_run.equilibration_steps, 0, a_watched_run.production_steps)


class TestTheCards:

    def _cards(self, root: Path, phases: list[dict]):
        from fastmdxplora.gui.report_dashboard import _summary_cards

        manifest = {"phases": phases}
        (root / "manifest.json").write_text(json.dumps(manifest))
        sim = json.loads((root / "simulation" / "simulation_parameters.json").read_text())
        return {card.label: card for card in _summary_cards(
            project_root=root, manifest=manifest, analysis_manifest={}, sim_manifest=sim)}

    def test_the_production_card_says_its_equilibration(self, tmp_path) -> None:
        root = _study(tmp_path / "study")
        cards = self._cards(root, [{"name": "simulation", "status": "ok"}])
        assert "Simulation time" not in cards
        assert (cards["Production"].value, cards["Production"].detail) == (
            "100 ps", "after 10 ps of equilibration (5 ps NVT, 5 ps NPT)")

    def test_the_wall_time_adds_the_phases_own_times(self, tmp_path) -> None:
        root = _study(tmp_path / "study")
        cards = self._cards(root, [
            {"name": "setup", "status": "ok", "started_at": "2026-10-05T16:41:00+00:00",
             "finished_at": "2026-10-05T16:41:04+00:00"},
            {"name": "simulation", "status": "ok", "started_at": "2026-10-05T16:41:04+00:00",
             "finished_at": "2026-10-05T16:59:01+00:00"},
            # Analysed again three hours later.
            {"name": "analysis", "status": "ok", "started_at": "2026-10-05T19:38:23+00:00",
             "finished_at": "2026-10-05T19:38:37+00:00"},
            {"name": "report", "status": "ok", "started_at": "2026-10-05T19:38:48+00:00",
             "finished_at": "2026-10-05T19:39:00+00:00"}])
        assert cards["Wall time"].value == "18m 27s"

    def test_a_run_that_stopped_says_how_far_it_got(self, tmp_path) -> None:
        """A run that raised wrote no production of its own, and the card
        gave the plan's 10 ns "as the run recorded it" for a run stopped at
        4 ns."""
        root = tmp_path / "study"
        (root / "simulation").mkdir(parents=True)
        (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
            "parameters": {"duration_ns": 10.0, "timestep_fs": 2.0},
            "duration_ns_actual": None, "resolved": {}}))
        (root / "simulation" / "live_status.json").write_text(json.dumps({
            "status": "failed", "stage": "production", "current_step": 2_005_000,
            "timestep_fs": 2.0, "nvt_steps_planned": 2500, "npt_steps_planned": 2500,
            "production_steps_planned": 5_000_000}))
        cards = self._cards(root, [{"name": "simulation", "status": "failed"}])
        assert (cards["Production"].value, cards["Production"].detail) == (
            "4 ns", "after 10 ps of equilibration (5 ps NVT, 5 ps NPT)")

    def test_a_run_that_recorded_nothing_says_its_plan_as_planned(self, tmp_path) -> None:
        root = tmp_path / "study"
        (root / "simulation").mkdir(parents=True)
        (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
            "parameters": {"duration_ns": 10.0}, "duration_ns_actual": None}))
        cards = self._cards(root, [{"name": "simulation", "status": "failed"}])
        assert (cards["Production"].value, cards["Production"].detail) == (
            "10 ns", "planned; the run recorded none")

    def test_the_overview_s_summary_says_production(self, tmp_path) -> None:
        from fastmdxplora.gui.server import _results_payload

        root = _study(tmp_path / "study")
        said = {row["label"]: row["value"] for row in _results_payload(root)["summary"]}
        assert said["Production"] == "100 ps" and "Simulation time" not in said


def test_the_status_route_gives_the_times(tmp_path) -> None:
    from urllib.request import urlopen

    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "report", "current_step": 55000,
        "timestep_fs": 2.0, "simulation_time_completed_ns": 0.11}))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with urlopen(session.url.rstrip("/") + "/api/status", timeout=30) as response:
            payload = json.loads(response.read())
    finally:
        session.server.shutdown()
    assert payload["times"]["production_ns"] == pytest.approx(0.1)
    assert payload["times"]["equilibration_planned_ns"] == pytest.approx(0.01)


@pytest.mark.parametrize("stage,step,said", [
    ("production", 30000, ("Production", "50 ps of 100 ps",
                           "after 10 ps of equilibration (5 ps NVT, 5 ps NPT)")),
    ("npt", 3000, ("Production", "0 of 100 ps", "equilibrating (NPT), 6 ps of 10 ps")),
])
def test_the_overview_says_it_while_the_run_goes(tmp_path, stage, step, said) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "study")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": stage, "current_step": step, "total_steps": 55000,
        "timestep_fs": 2.0, "nvt_steps_planned": 2500, "npt_steps_planned": 2500,
        "production_steps_planned": 50000, "simulation_time_completed_ns": step * 2e-6}))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.getElementById('live-simtime-cell').textContent !== '—'",
                timeout=60000)
            shown = page.evaluate("""() => ['live-simtime-label', 'live-simtime-cell',
                'live-simtime-note'].map(id => document.getElementById(id).textContent)""")
            browser.close()
    finally:
        session.server.shutdown()
    assert tuple(shown) == said
