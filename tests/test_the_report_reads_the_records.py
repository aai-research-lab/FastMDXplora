"""The report says what the records hold.

The Convergence table worked each mean out again and gave SASA's
recorded 4.7592 as 4.7591. A mean its record says is not a measurement was
given an error, a record noted on its mean alone got no note, "Neither
lifts ..." followed reasons naming one remedy, and two numbers of the
checks table ("fewest: potential energy, 13", the mean temperature) were
not traced to the records that hold them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.statistics import with_its_error


# ---------------------------------------------------------------------------
# (e) The Convergence table reads the recorded mean, error and verdict
# ---------------------------------------------------------------------------

#: SASA's record in the tri-alanine study: 4.7592 to the place its error
#: allows, and 4.7591 once saved as 4.75915 and rounded again.
SASA = {"discard": 60, "statistical_inefficiency": 1.6504978469285478,
        "effective_samples": 24.235111893321754, "mean": 4.759154880046845,
        "standard_error": 0.007547557718641827, "standard_deviation": 0.03715600028163848,
        "degrees_of_freedom": 13.333333333333334, "rules": 2, "n_frames": 100,
        "unit": "nm²"}


def _analysed(root: Path, name: str, series: np.ndarray, record: dict | None) -> None:
    where = root / "analysis" / name
    where.mkdir(parents=True, exist_ok=True)
    np.savetxt(where / f"{name}.dat", series, fmt="%.17g", header=name)
    if record is not None:
        (where / "options.json").write_text(json.dumps({"findings": {"mean": record}}),
                                            encoding="utf-8")


def _row(section: str, observable: str) -> list[str]:
    (line,) = [line for line in section.splitlines() if line.startswith(f"| {observable} |")]
    return [cell.strip() for cell in line.strip("|").split("|")]


def test_the_convergence_table_gives_the_recorded_mean_and_error(tmp_path):
    from fastmdxplora.report.document import _convergence_section

    series = 4.76 + 0.04 * np.random.default_rng(3).standard_normal(100)
    _analysed(tmp_path, "sasa", series, SASA)
    section = _convergence_section(tmp_path)
    row = _row(section, "SASA")
    assert (row[1], row[2], row[4], row[5]) == ("100", "60", "4.7592", "0.0075")
    assert with_its_error(SASA["mean"], SASA["standard_error"]) == "4.7592 ± 0.0075"
    assert row[3] == "24.2"


def test_an_error_recorded_otherwise_than_worked_out_is_the_recorded_one(tmp_path):
    """The error of a run with frames discarded is the whole run's scaled to
    the frames kept: not the spread over the root of the samples kept."""
    from fastmdxplora.report.document import _convergence_section

    record = {**SASA, "standard_error": 0.0123}
    _analysed(tmp_path, "sasa", 4.76 + 0.04 * np.random.default_rng(3).standard_normal(100),
              record)
    assert _row(_convergence_section(tmp_path), "SASA")[5] == "0.012"


def test_a_withheld_mean_keeps_its_record_s_verdict_and_what_no_record_holds_is_said(tmp_path):
    from fastmdxplora.report.document import _convergence_section

    withheld = {**SASA, "not_a_measurement": "9.0 independent samples. The remedy is a "
                                             "longer run."}
    rng = np.random.default_rng(4)
    _analysed(tmp_path, "sasa", 4.76 + 0.04 * rng.standard_normal(100), withheld)
    _analysed(tmp_path, "rg", 0.33 + 0.004 * rng.standard_normal(100), None)
    section = _convergence_section(tmp_path)
    assert _row(section, "SASA")[4:6] == ["4.759", "not determined"]
    assert ("No record holds the frame count, mean, frames discarded, independent samples "
            "and error of Rg: the table works these out here from the series") in section


# ---------------------------------------------------------------------------
# C13 L2, C13 L3 and C12 L2: the findings line and its notes
# ---------------------------------------------------------------------------

_IN_CONTACT = {"frames_used": 100, "frames_under_cutoff": 100, "fraction_under_cutoff": 1.0,
               "frames_under_close": 100, "nearest_nm": 0.3, "cutoff_nm": 0.9,
               "counted_over": "frames"}


def test_a_mean_its_record_says_is_not_a_measurement_is_given_no_error():
    from fastmdxplora.report.document import _findings_notes

    said = _findings_notes({"mean": {
        "mean": 12.661, "standard_error": 0.026, "effective_samples": 9.0, "unit": "nm²",
        "not_a_measurement": "9.0 independent samples. The remedy is a longer run."}})
    assert said[0] == ("Mean over the equilibrated part of the run: 12.66 nm², from "
                       "9 independent samples.")


def test_a_record_noted_on_its_mean_alone_gets_its_note():
    from fastmdxplora.report.document import _findings_notes

    note = {**_IN_CONTACT, "frames_under_cutoff": 40, "frames_under_close": 10,
            "fraction_under_cutoff": 0.4}
    said = _findings_notes({"mean": {"mean": 1.0, "standard_error": 0.01,
                                     "effective_samples": 30.0, "solute_image": note}})
    assert any(line.startswith("The solute came within the 0.9 nm cutoff of its own periodic "
                               "image in 40 of the 100 frames used") for line in said), said


@pytest.mark.parametrize("remedy, said", [
    ("the remedy is a longer run.", " A longer run does not lift the bias"),
    ("The remedy is a longer run, not more pooling.", " A longer run does not lift the bias"),
    ("The remedy is a longer run, or better, replicas.",
     " Neither a longer run nor replicas lift the bias"),
])
def test_what_does_not_lift_the_image_s_bias_is_named(remedy, said):
    from fastmdxplora.report.document import _findings_notes

    reason = "Still drifting after its equilibration; " + remedy
    notes = _findings_notes({"mean": {"mean": 1.04, "standard_error": None,
                                      "not_a_measurement": reason,
                                      "solute_image": dict(_IN_CONTACT)}})
    assert (reason + said + " of the solute's own image, in contact with it in every frame "
            "this mean uses: a larger box, in a new study, does.") in notes


# ---------------------------------------------------------------------------
# (f) The checks table's numbers traced to their records
# ---------------------------------------------------------------------------

THERMO = {
    "potential_energy": {"units": "kJ/mol", "discard": 2, "effective_samples": 12.8,
                         "mean": -19665.6, "standard_error": 40.0,
                         "standard_deviation": 139.6, "rules": 2},
    "temperature": {"units": "K", "discard": 1, "effective_samples": 30.4,
                    "mean": 298.04, "standard_error": 1.2, "standard_deviation": 6.6,
                    "rules": 2},
}


@pytest.fixture()
def thermostatted(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    rng = np.random.default_rng(5)
    rows = ["#\"Step\",\"Potential Energy (kJ/mole)\",\"Temperature (K)\""]
    rows += [f"{i},{-19665.6 + 140 * rng.standard_normal():.3f},"
             f"{298.0 + 6.6 * rng.standard_normal():.3f}" for i in range(100)]
    (root / "simulation" / "energy.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"duration_ns_actual": 0.2, "parameters": {"temperature_K": 300.0}}), encoding="utf-8")
    (root / "analysis" / "thermodynamics").mkdir(parents=True)
    (root / "analysis" / "thermodynamics" / "options.json").write_text(json.dumps(
        {"findings": {"thermodynamics": THERMO}}), encoding="utf-8")
    from fastmdxplora.report.document import _convergence_section

    (root / "report").mkdir()
    (root / "report" / "report.md").write_text("# Report\n\n" + _convergence_section(root),
                                               encoding="utf-8")
    return root


def _traced(root: Path, said: str) -> list[dict]:
    from fastmdxplora.gui.report_page import report_payload

    payload = report_payload(root)
    if payload.get("rendered") != "html":
        pytest.skip("the Markdown library is not installed")
    return [number for number in payload["numbers"] if number["said"] == said]


def test_the_fewest_independent_samples_are_traced_to_their_record(thermostatted):
    (number,) = _traced(thermostatted, "13")
    assert number["traced"], number
    assert number["record"]["key"] == "findings.thermodynamics.potential_energy.effective_samples"
    text = (thermostatted / "report" / "report.md").read_text(encoding="utf-8")
    assert "fewest: potential energy, 13 independent samples" in text


def test_the_mean_temperature_in_the_checks_is_traced_to_its_record(thermostatted):
    found = _traced(thermostatted, "298.0 K")
    assert found and all(number["traced"] for number in found), found
    assert {number["record"]["key"] for number in found} == {
        "findings.thermodynamics.temperature.mean"}
    text = (thermostatted / "report" / "report.md").read_text(encoding="utf-8")
    assert "mean temperature 298.0 K against a target of 300.0 K" in text


def test_a_mean_temperature_no_record_holds_is_said_so(thermostatted):
    """Worked out from the energy records where the thermodynamics recorded
    no mean: said so beside it, not left as if a record held it."""
    from fastmdxplora.report.document import _convergence_section

    options = thermostatted / "analysis" / "thermodynamics" / "options.json"
    options.write_text(json.dumps({"findings": {"thermodynamics": {
        "potential_energy": THERMO["potential_energy"]}}}), encoding="utf-8")
    assert ("K (worked out here; no record holds it) against a target of 300.0 K"
            in _convergence_section(thermostatted))
