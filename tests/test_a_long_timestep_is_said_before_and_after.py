"""A timestep longer than its constraints allow is said before the run, and
the run that blew up on it is told what to try from the step it took.

A 10 fs timestep passed every check ("Checks pass", Run enabled) and failed
in NVT with NaN coordinates. The diagnosis then said "Halve the timestep:
--simulate-timestep-fs 1.0" whatever the run had taken; the fix card under
it said the software "suggests nothing" beneath text naming "the remedies
below"; and the list of what to try was set as one paragraph.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("settings, said", [
    ({"timestep_fs": 10.0}, "longer than the 2 fs"),
    ({"timestep_fs": 3.0}, "longer than the 2 fs"),
    ({"timestep_fs": 5.0, "hydrogen_mass_amu": 4.0}, "longer than the 4 fs"),
    ({"timestep_fs": 2.0, "constraints": "None"}, "longer than the 1 fs"),
])
def test_a_long_timestep_is_worth_knowing(settings, said):
    from fastmdxplora.advisories import advise

    [advice] = [a for a in advise({}, settings) if a.setting == "timestep_fs"]
    assert said in advice.summary


@pytest.mark.parametrize("settings", [
    {"timestep_fs": 2.0}, {}, {"timestep_fs": 4.0, "hydrogen_mass_amu": 4.0},
    {"timestep_fs": 10.0, "integrator": "variable_langevin"},
])
def test_a_usual_timestep_is_not(settings):
    from fastmdxplora.advisories import advise

    assert not [a for a in advise({}, settings) if a.setting == "timestep_fs"]


@pytest.mark.parametrize("taken, said", [
    (10.0, "--simulate-timestep-fs 2"), (4.0, "--simulate-timestep-fs 2"),
    (2.0, "--simulate-timestep-fs 1"), (None, "<half of it>")])
def test_the_step_to_try_follows_the_step_taken(taken, said):
    from fastmdxplora.simulation.diagnose import diagnose_failure
    from tests.test_diagnose import _blow_up, _system

    topology = _system({"ALA": 4, "HOH": 40})
    found = diagnose_failure(topology, _blow_up(topology, {"ALA", "HOH"}),
                             stage="NVT", timestep_fs=taken)
    tried = [line for line in found.advice if "timestep" in line.lower()]
    assert tried and said in tried[0], found.advice
    assert "1.0" not in tried[0] or taken == 2.0


def test_an_unstable_run_s_fix_points_at_its_diagnosis():
    from fastmdxplora.refusals import Refusal
    from fastmdxplora.remedies import remedy_for

    remedy = remedy_for(Refusal("simulation.run.unstable", "It blew up."))
    assert "suggests nothing" not in remedy.fix
    assert "in the run's health below" in remedy.fix


def test_what_to_try_is_set_as_a_list(tmp_path):
    pytest.importorskip("playwright.sync_api")
    import json

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    study = _study(tmp_path / "study", failed="simulation.run.unstable")
    error = ("UnstableRun: The simulation became unstable during NVT equilibration.\n"
             "24 of 3,344 atoms (0.7%) have non-finite coordinates.\n\n"
             "The affected atoms do not fall into a pattern.\n\n"
             "What to try:\n  - Shorten the timestep\n  - Minimize for longer\n"
             "  - Restrain the solute")
    (study / "simulation").mkdir(exist_ok=True)
    (study / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "failed", "stage": "nvt", "latest_error": error}), encoding="utf-8")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#health-explanation li")
            items = page.eval_on_selector_all("#health-explanation li",
                                              "(all) => all.map((li) => li.textContent)")
            paragraphs = page.eval_on_selector_all("#health-explanation p",
                                                   "(all) => all.length")
            browser.close()
    finally:
        session.server.shutdown()
    assert items == ["Shorten the timestep", "Minimize for longer", "Restrain the solute"]
    assert paragraphs >= 3


def test_a_config_file_checked_says_it_too():
    """A config with a 10 fs timestep, checked from New study's "A config I
    have", answered "Runs." and nothing else; the form said "Checks pass"
    above the advice."""
    from pathlib import Path

    from fastmdxplora.gui.config_builder import check_config

    said = check_config({"systems": [{"system": "1L2Y"}],
                         "simulation": {"timestep_fs": 10, "duration_ns": 0.01}})
    assert said["ok"] and any("10 fs" in line for line in said["worth_knowing"])
    assert check_config({"systems": [{"system": "1L2Y"}],
                         "simulation": {"duration_ns": 0.01}})["worth_knowing"] == []
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "run-builder.js").read_text(encoding="utf-8")
    assert 'setStatus("ok", checksPass());' in script
    assert "Worth knowing: ${knowing.join" in script


def test_the_note_after_a_failed_run_says_why_it_failed(tmp_path):
    """New study's note said "Manifest: .../manifest.json (exit code 1 ...)"
    for a run that became unstable in NVT: the log's last line."""
    import json

    from fastmdxplora.gui.exploration import DashboardRuntime

    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    (study / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "failed", "stage": "nvt",
        "latest_error": "UnstableRun: The simulation became unstable during NVT equilibration.\n"
                        "24 of 3,344 atoms have non-finite coordinates."}), encoding="utf-8")
    log = tmp_path / "exploration.log"
    log.write_text(f"Manifest: {study}/manifest.json\n", encoding="utf-8")
    runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path,
                               log_path=log, process_returncode=1)
    runtime.active_root = study
    said = runtime._process_failure_message()
    assert said.startswith("UnstableRun: The simulation became unstable during NVT")
    assert "Manifest:" not in said
