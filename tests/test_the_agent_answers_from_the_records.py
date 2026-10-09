"""The Agent answers its questions about a study from the study's records.

The start page offers three questions about the study open: what it found,
whether it ran long enough, and what would strengthen it most. With no AI
model set each was refused, though every part of each answer is recorded:
the report's line on what the run supports, each mean with its error or why
it gave none, the checks, how much more production the withheld means need
and what that takes here, and that a single run's error cannot show a state
the run never left. They are now said from the records, marked so, and
nothing is said that the records do not hold. With an AI model set, the
questions go to it as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.gui import records_answer
from fastmdxplora.gui.records_answer import MARK, TRAPPED, answer_from_the_records


def _findings(root: Path, name: str, found: dict) -> None:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "options": {}, "findings": {"mean": found}}), encoding="utf-8")


def _study(root: Path) -> Path:
    _findings(root, "rmsd", {"mean": 0.1123, "standard_error": 0.0021, "unit": "nm",
                             "effective_samples": 21.2, "discard": 62, "n_frames": 100})
    _findings(root, "rg", {"mean": 0.3281, "unit": "nm", "effective_samples": 4.0,
                           "not_a_measurement": "This run is not long against its own "
                                                "correlation time: taking half the frames "
                                                "away changes the estimate."})
    return root


SUPPORTED = "All 2 observables assessed had equilibrated and hold enough independent samples to average."
THIN = ("Of 2 observables assessed, 1 had equilibrated, 1 could not be judged from a run "
        "this length; see the report's Convergence section before using any average from "
        "this run.")


def test_each_mean_is_said_with_its_error_or_why_it_has_none(tmp_path):
    lines = records_answer._means(_study(tmp_path / "study"))
    # As the Analysis page gives them, in its order.
    assert lines == [
        "RMSD: 0.1123 ± 0.0021 nm, 21 independent samples, after the first 62 of 100 frames.",
        "Radius of gyration: 0.3281 nm, not determined; this run is not long against its "
        "own correlation time."]


def test_what_it_found(tmp_path, monkeypatch):
    monkeypatch.setattr(records_answer, "_supports", lambda base: THIN)
    said = answer_from_the_records(_study(tmp_path / "study"), "found")
    assert said.startswith(MARK)
    assert THIN in said
    assert "- RMSD: 0.1123 ± 0.0021 nm" in said and "- Radius of gyration: 0.3281 nm" in said


def test_long_enough_with_a_figure_and_the_command(tmp_path, monkeypatch):
    study = _study(tmp_path / "study")
    monkeypatch.setattr(records_answer, "_supports", lambda base: THIN)
    monkeypatch.setattr(records_answer, "_ask", lambda base: (
        "Radius of gyration withheld its mean for want of sampling: 2 ns more production "
        "should give it 10 independent samples.", "fastmdx explore --simulate-resume-from /s --simulate-extra-ns 2"))
    said = answer_from_the_records(study, "long_enough")
    assert "Not for all of its means. " + THIN in said
    assert "What they need, every mean withheld counted: Radius of gyration withheld its mean" in said
    assert "`fastmdx explore --simulate-resume-from /s --simulate-extra-ns 2`" in said
    assert said.endswith(TRAPPED)


def test_long_enough_by_its_records(tmp_path, monkeypatch):
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    said = answer_from_the_records(_study(tmp_path / "study"), "long_enough")
    assert "By its records, yes. " + SUPPORTED in said
    # Equilibrated and well sampled is not the same as not trapped.
    assert said.endswith(TRAPPED)


def test_not_long_enough_with_no_figure_says_why_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(records_answer, "_supports", lambda base: THIN)
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    said = answer_from_the_records(_study(tmp_path / "study"), "long_enough")
    assert "Not by its records. " + THIN in said
    assert records_answer.NO_FIGURE in said


def test_a_study_never_analysed_is_told_so_and_a_stopped_one_to_carry_on(tmp_path, monkeypatch):
    """A run stopped in production, never analysed, was told its analyses
    recorded no figure, to analyse it again, and to see a report it had
    not written."""
    import shutil

    from fastmdxplora.gui import telemetry

    study = _study(tmp_path / "study")
    shutil.rmtree(study / "analysis", ignore_errors=True)
    monkeypatch.setattr(records_answer, "_supports", lambda base: THIN)
    monkeypatch.setattr(telemetry, "status_as_it_stands", lambda base: {"status": "stopped"})
    said = answer_from_the_records(study, "long_enough")
    assert "it has not been analysed" in said and "What would fix it, on the Overview, says what to do first" in said
    assert records_answer.NO_FIGURE not in said and "Convergence" not in said
    assert said.endswith(TRAPPED)


def test_what_would_strengthen_it(tmp_path, monkeypatch):
    study = _study(tmp_path / "study")
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    said = answer_from_the_records(study, "strengthen")
    assert "adds precision but not a check" in said
    assert "Then replicas. " + TRAPPED in said
    monkeypatch.setattr(records_answer, "_ask", lambda base: ("rg needs 2 ns.", ""))
    said = answer_from_the_records(study, "strengthen")
    assert said.index("A longer run first") < said.index("Then replicas")


def test_a_study_of_several_runs_points_to_them(tmp_path):
    from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study

    root = _a_sweep_study(tmp_path)
    said = answer_from_the_records(root, "found")
    assert said.startswith(MARK) and "a study of 2 runs, 1 completed" in said


def test_nothing_to_say_is_nothing(tmp_path):
    assert answer_from_the_records(tmp_path / "missing", "found") is None
    assert answer_from_the_records(_study(tmp_path / "study"), "anything") is None


def test_it_answers_only_where_no_ai_model_can_be_asked(tmp_path, monkeypatch):
    from fastmdxplora.agent import models
    from fastmdxplora.gui.agent_panel import propose_endpoint

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    study = _study(tmp_path / "study")
    runtime = SimpleNamespace(snapshot=lambda: {"active_run": str(study)}, active_root=study)
    asked = {"request": "Summarise what this study found.", "records_question": "found"}
    answered = propose_endpoint(asked, runtime)
    assert answered["ok"] and answered["from_records"]
    assert answered["answer"].startswith(MARK)
    # Typed rather than asked from the start page, it is refused as before.
    refused = propose_endpoint({"request": "Summarise what this study found."}, runtime)
    assert refused == {"ok": False, "error": refused["error"], "code": "environment.model.unset"}


def test_the_start_page_question_is_answered_in_the_page(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent import models
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    study = _study(_write_study(tmp_path / "study"))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_selector("#agent-start-study:not([hidden])")
            note = page.locator("#agent-start-records").is_visible()
            # Sent as it is pressed.
            page.locator(".agent-starter[data-records='found']").click()
            answer = page.wait_for_selector(".agent-answer").inner_text()
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert note
    assert answer.startswith("From this study's records.")
    assert "RMSD: 0.1123 ± 0.0021 nm" in answer


@pytest.mark.parametrize("record, said", [
    ({"refused": "The windows do not overlap: window 3 and 4 share no samples."},
     "Its windows gave no free energy: the windows do not overlap."),
    ({"pmf": {}, "binding": {"delta_g_kjmol": -23.456, "delta_g_standard_error_kjmol": 1.23,
                             "reference": {"warnings": ["The pull went through the backbone."]}}},
     "Its windows give a binding free energy of -23.5 ± 1.2 kJ/mol (standard state, 1 M; "
     "the error a standard error). The pull went through the backbone."),
    ({"pmf": {}, "binding": {"refused": "The shell is not open: the cap holds the ligand."}},
     "Its windows were recombined into a free energy profile, and no binding free energy "
     "is given: the shell is not open."),
    ({"pmf": {}}, "Its windows were recombined into a free energy profile, on its Report page."),
])
def test_an_umbrella_study_says_what_its_windows_gave(tmp_path, record, said):
    (tmp_path / "pmf.json").write_text(json.dumps(record), encoding="utf-8")
    assert records_answer._free_energy(tmp_path) == said


def test_a_study_of_runs_says_its_free_energy_first(tmp_path):
    from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study

    root = _a_sweep_study(tmp_path)
    (root / "pmf.json").write_text(json.dumps({"pmf": {}}), encoding="utf-8")
    said = answer_from_the_records(root, "found")
    assert said.index("free energy profile") < said.index("a study of 2 runs")


def _withheld_with_a_figure(root: Path) -> Path:
    _findings(root, "rg", {"mean": 0.3281, "unit": "nm", "effective_samples": 4.0,
                           "not_a_measurement": "Too few independent samples: 4.",
                           "shortfall": {"more_ns": 2.0, "lower_bound": False}})
    return root


def test_from_real_records_with_the_command_that_extends_it():
    from tests.test_a_study_is_extended_in_place import _study as _extendable

    study = _withheld_with_a_figure(_extendable())
    said = answer_from_the_records(study, "long_enough")
    assert "What they need, every mean withheld counted: Radius of gyration withheld its mean for want of sampling: 2 ns" in said
    assert (f"`fastmdx explore --simulate-resume-from {study.resolve()} "
            "--simulate-extra-ns 2`") in said
    said = answer_from_the_records(study, "strengthen")
    assert "A longer run first" in said and "--simulate-extra-ns 2`" in said


def test_from_real_records_that_cannot_be_extended(tmp_path):
    study = _withheld_with_a_figure(tmp_path / "study")
    said = answer_from_the_records(study, "long_enough")
    assert "What they need, every mean withheld counted: Radius of gyration withheld its mean" in said
    assert "fastmdx explore" not in said


def test_a_study_with_no_records_yet(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    assert "No analysis has recorded a mean yet." in answer_from_the_records(study, "found")
    assert "Its records do not say yet" in answer_from_the_records(study, "long_enough")


def test_records_that_cannot_be_read_are_passed_over(tmp_path, monkeypatch):
    study = tmp_path / "study"
    (study / "analysis" / "broken").mkdir(parents=True)
    (study / "analysis" / "broken" / "options.json").write_text("{not json", encoding="utf-8")
    _findings(study, "qvalue", {"mean": "n/a"})
    _findings(study, "sasa", {"not_a_measurement": "The series is empty."})
    folder = study / "analysis" / "rmsd"
    folder.mkdir()
    (folder / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": "unrecorded", "other": 3}}), encoding="utf-8")
    # As the Analysis page says them: a mean recorded as no number has none.
    assert records_answer._means(study) == [
        "Q-value: no mean was recorded.",
        "Solvent accessible surface area: no mean; the series is empty."]

    def broken(base):
        raise ValueError("no convergence record")

    import fastmdxplora.report.document as document

    monkeypatch.setattr(document, "_what_the_run_supports", broken)
    assert records_answer._supports(study) == ""
    (tmp_path / "pmf.json").write_text("[]", encoding="utf-8")
    assert records_answer._free_energy(tmp_path) == ""


def test_a_run_of_a_study_points_to_the_comparison(tmp_path, monkeypatch):
    from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study

    root = _a_sweep_study(tmp_path)
    run = next(path for path in (root / "runs").iterdir() if path.is_dir())
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    said = answer_from_the_records(run, "strengthen")
    assert "This run is one of the study sweep" in said
    assert "Then replicas" not in said


def test_no_study_open_is_no_answer(tmp_path):
    runtime = SimpleNamespace(snapshot=lambda: {"active_run": None})
    assert records_answer.answered_from_the_records({"records_question": "found"},
                                                    runtime) is None


def test_the_thermodynamic_means_without_a_thermodynamics_analysis(tmp_path):
    """"Summarise what this study found" named no energy, temperature or
    density for a study with no thermodynamics analysis, while the Overview
    and the report gave each."""
    from tests.test_the_overview_leads_with_what_was_determined import _metrics, _status

    root = _study(tmp_path / "study")
    _metrics(root, production=2000)
    _status(root)
    lines = records_answer._means(root)
    temperature = next(line for line in lines if line.startswith("Temperature: "))
    assert " ± " in temperature and temperature.endswith("independent samples.")
    assert any(line.startswith("Potential energy: ") for line in lines)


def test_a_drifting_mean_is_not_sent_to_be_analysed_again(tmp_path, monkeypatch):
    """"Is this run long enough?" told a study analysed with this release to
    analyse it again with this release: a mean withheld as still drifting
    records no figure for how much longer."""
    monkeypatch.setattr(records_answer, "_supports", lambda base: THIN)
    monkeypatch.setattr(records_answer, "_ask", lambda base: None)
    study = _study(tmp_path / "study")
    _findings(study, "rg", {"mean": 0.33, "unit": "nm", "effective_samples": 30.0,
                            "not_a_measurement": "Still drifting after its equilibration: "
                                                 "the remedy is a longer run."})
    said = answer_from_the_records(study, "long_enough")
    assert records_answer.STILL_DRIFTING in said
    assert records_answer.NO_FIGURE not in said
