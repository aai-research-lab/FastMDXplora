"""The report says what the records hold, as they hold it at the end.

In a study of replicas each run's report was written before the study
averaged its runs again from the start they share, so seed 1's report said
0.07415 nm over all 100 frames while its record said 0.0799 nm over frames
38-99. The Convergence table worked each mean out again and gave SASA's
recorded 4.7592 as 4.7591. A mean its record says is not a measurement was
given an error, a record noted on its mean alone got no note, "Neither
lifts ..." followed reasons naming one remedy, and two numbers of the
checks table ("fewest: potential energy, 13", the mean temperature) were
not traced to the records that hold them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from fastmdxplora.statistics import mean_record, with_its_error


def _ar1(n: int, seed: int, phi: float = 0.95, sigma: float = 0.05) -> np.ndarray:
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma * math.sqrt(1.0 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal(0.0, sigma)])[0]


def _relaxing(n: int, seed: int) -> np.ndarray:
    return 1.0 + 0.15 * np.exp(-np.arange(n) / 300.0) + _ar1(n, seed)


# ---------------------------------------------------------------------------
# (a) Each run's report after the start its replicas share
# ---------------------------------------------------------------------------

def _replicas(root: Path) -> list[Path]:
    """Three replicas over a seed, analysed and reported, their relaxation
    one the shared start sees later than two of them do."""
    runs, members = [], []
    for k in range(3):
        run = root / "runs" / f"r{k}"
        where = run / "analysis" / "rmsd"
        where.mkdir(parents=True)
        series = _relaxing(2200, 70 + k)
        record = {**mean_record(series, frame_interval_ns=0.01), "unit": "nm"}
        record = {key: (str(v) if key == "not_a_measurement" else v)
                  for key, v in record.items()}
        (where / "options.json").write_text(json.dumps({"findings": {"mean": record}}),
                                            encoding="utf-8")
        np.savetxt(where / "rmsd.dat", series, fmt="%.17g", header="rmsd")
        (run / "analysis" / "analysis_manifest.json").write_text(json.dumps(
            {"plan": ["rmsd"], "n_frames": 2200, "results": {"rmsd": {"status": "ok"}}}),
            encoding="utf-8")
        (run / "manifest.json").write_text(json.dumps(
            {"system": "1L2Y", "phases": [{"name": "analysis", "status": "ok"}]}),
            encoding="utf-8")
        (run / "resolved_config.yml").write_text(yaml.safe_dump(
            {"systems": [{"system": "1L2Y"}], "include_phase": ["analysis", "report"],
             "report": {"slides": False, "pdf": False, "bundle": False}}), encoding="utf-8")
        runs.append({"run_id": f"r{k}", "status": "ok", "system": "1L2Y",
                     "output_dir_relative": f"runs/r{k}",
                     "sweep_values": {"simulation.random_seed": k}})
        members.append(run)
    (root / "batch_manifest.json").write_text(json.dumps(
        {"sweep": {"simulation.random_seed": [0, 1, 2]}, "runs": runs}), encoding="utf-8")
    from fastmdxplora.batch.explorer import BatchExplorer

    explorer = BatchExplorer.__new__(BatchExplorer)
    for run in members:
        explorer._report_again(run)
    return members


def _recorded(run: Path) -> dict:
    return json.loads((run / "analysis" / "rmsd" / "options.json").read_text(
        encoding="utf-8"))["findings"]["mean"]


def _said_in_its_report(run: Path) -> str:
    lines = (run / "report" / "report.md").read_text(encoding="utf-8").splitlines()
    (line,) = [line for line in lines if "Mean over the equilibrated part" in line]
    return line


def _as_recorded(record: dict) -> str:
    return (f"{with_its_error(record['mean'], record['standard_error'])} nm, from "
            f"{record['effective_samples']:.0f} independent samples after discarding "
            f"{record['discard']:,} frames")


def test_each_run_s_report_says_its_mean_from_the_start_the_replicas_share(tmp_path):
    from fastmdxplora.batch.explorer import compare_the_runs_again

    members = _replicas(tmp_path)
    before = [_recorded(run) for run in members]
    compare_the_runs_again(tmp_path)
    after = [_recorded(run) for run in members]
    moved = [run for run, old, new in zip(members, before, after)
             if new["discard"] != old["discard"]]
    assert moved, "the shared start moved no run's start: the test shows nothing"
    for run, record in zip(members, after):
        assert _as_recorded(record) in _said_in_its_report(run), run.name


def test_a_report_older_than_the_stopping_rule_s_shared_start_is_written_again(tmp_path):
    """The stopping rule shares the start after each round's reports: read
    again with nothing left to share, the study still writes each report
    from its records."""
    from fastmdxplora.batch.explorer import compare_the_runs_again
    from fastmdxplora.simulation.stopping import share_the_start

    members = _replicas(tmp_path)
    assert share_the_start(members, "rmsd") > 0
    compare_the_runs_again(tmp_path)
    for run in members:
        assert _as_recorded(_recorded(run)) in _said_in_its_report(run), run.name


def test_a_report_stamped_in_the_same_second_as_its_record_is_decided_by_content(tmp_path):
    """On a filesystem stamping whole seconds a record rewritten from the
    shared start in the second its report was written was not newer: the
    report still said the mean from the run's own start."""
    import os

    from fastmdxplora.batch.explorer import compare_the_runs_again
    from fastmdxplora.simulation.stopping import share_the_start

    members = _replicas(tmp_path)
    assert share_the_start(members, "rmsd") > 0
    for run in members:
        stamp = int((run / "report" / "report.md").stat().st_mtime)
        for path in (run / "report" / "report.md", run / "analysis" / "rmsd" / "options.json"):
            os.utime(path, (stamp, stamp))
    compare_the_runs_again(tmp_path)
    for run in members:
        assert _as_recorded(_recorded(run)) in _said_in_its_report(run), run.name


def test_compare_writes_the_replicas_reports_again(tmp_path):
    """`FastMDXplora.compare()` shares the replicas' start and left each
    report saying the mean from the run's own start."""
    pytest.importorskip("matplotlib")
    from fastmdxplora import FastMDXplora

    members = _replicas(tmp_path)
    before = [_recorded(run) for run in members]
    FastMDXplora.__new__(FastMDXplora).compare(output_dir=tmp_path)
    after = [_recorded(run) for run in members]
    assert any(new["discard"] != old["discard"] for old, new in zip(before, after))
    for run, record in zip(members, after):
        assert _as_recorded(record) in _said_in_its_report(run), run.name


def test_the_stopping_rule_writes_each_report_again_once_after_its_rounds(tmp_path, monkeypatch):
    """Each run's extension built the study's comparison again and wrote
    the other runs' reports again, mid-round: written once, after the
    rule's last round; a run analysed again on its own keeps its rewrite."""
    from fastmdxplora.batch import explorer as module
    from fastmdxplora.batch.explorer import BatchExplorer, compare_the_runs_again
    from fastmdxplora.simulation import resume
    from fastmdxplora.simulation.stopping import share_the_start

    members = _replicas(tmp_path)
    written = BatchExplorer.__new__(BatchExplorer)._report_again
    calls: list[str] = []

    def counted(run):
        calls.append(Path(run).name)
        written(run)

    def extended(run, *, more_ns, device_index=None):
        """A run's piece analysed and reported, then its study compared
        again, as `resume.extend_study` and a run's own explore do."""
        where = Path(run) / "analysis" / "rmsd"
        series = np.loadtxt(where / "rmsd.dat")
        longer = np.concatenate([series, 1.0 + _ar1(400, len(series))])
        np.savetxt(where / "rmsd.dat", longer, fmt="%.17g", header="rmsd")
        record = {**mean_record(longer, frame_interval_ns=0.01), "unit": "nm"}
        (where / "options.json").write_text(json.dumps({"findings": {"mean": {
            k: (str(v) if k == "not_a_measurement" else v) for k, v in record.items()}}}),
            encoding="utf-8")
        written(Path(run))
        compare_the_runs_again(tmp_path)
        return {"ok": True}

    monkeypatch.setattr(BatchExplorer, "_report_again", staticmethod(counted))
    monkeypatch.setattr(resume, "extend_study", extended)
    study = BatchExplorer.__new__(BatchExplorer)
    study.output_dir, study.mode, study.devices = tmp_path, "sequential", []
    study._raw = {}
    for _ in range(2):
        study._extend_runs(members, 0.004)
        share_the_start(members, "rmsd")
    assert calls == []
    study._maybe_aggregate_members()
    assert len(calls) == len(set(calls)) <= len(members), calls
    for run in members:
        assert _as_recorded(_recorded(run)) in _said_in_its_report(run), run.name
    # One run analysed again on its own, outside the rule's rounds.
    calls.clear()
    assert module._extending_in_rounds is False
    extended(members[0], more_ns=0.004)
    for run in members:
        assert _as_recorded(_recorded(run)) in _said_in_its_report(run), run.name


def test_a_report_written_again_records_no_phase_it_did_not_run(tmp_path):
    """A run given `setup_from` was recorded as set up again, skipped, at
    the time its report was written again; its report is marked written
    again, for the study's timeline."""
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (prepared / name).write_text("x", encoding="utf-8")
    members = _replicas(tmp_path / "study")
    run = members[0]
    config = yaml.safe_load((run / "resolved_config.yml").read_text(encoding="utf-8"))
    config["simulation"] = {"setup_from": str(prepared)}
    (run / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    setup = {"name": "setup", "status": "skipped", "started_at": "2026-10-18T06:00:00+00:00",
             "finished_at": "2026-10-18T06:00:00+00:00", "taken_from": str(prepared)}
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["phases"] = [setup] + manifest["phases"]
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    from fastmdxplora.batch.explorer import BatchExplorer

    BatchExplorer.__new__(BatchExplorer)._report_again(run)
    phases = {p["name"]: p for p in json.loads(
        (run / "manifest.json").read_text(encoding="utf-8"))["phases"]}
    assert phases["setup"] == setup
    assert phases["report"]["written_again"] is True


def test_the_last_round_comes_before_the_replicas_reports_written_again(tmp_path):
    """Every replica's report written again after the rounds was placed
    before the study's last round, read only for a study of one run."""
    from tests.test_the_study_has_a_timeline import _a_piece, _phase, _round, _stopping
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    from fastmdxplora.gui.study_timeline import timeline_of

    root = _a_study_of_runs(tmp_path, "simulation.random_seed", [1, 2])
    first, second = sorted((root / "runs").iterdir())
    for run, piece, analysed, again in (
            (first, ("08:00:00", "08:10:00"), ("08:10:00", "08:15:00"), "08:41:00"),
            (second, ("08:20:00", "08:30:00"), ("08:30:00", "08:35:00"), "08:42:00")):
        (run / "manifest.json").write_text(json.dumps({"phases": [
            _phase("simulation", "07:00:00", "07:30:00"), _phase("analysis", *analysed),
            {**_phase("report", again, again), "written_again": True}]}), encoding="utf-8")
        _a_piece(run, 1, 0.5, *piece)
    _stopping(root, [first.name, second.name],
              [_round("extend", 1.0, 0.5), _round("met", 1.5)], "met",
              "After 1.5 ns of production: rmsd 0.150 ± 0.001 nm.")
    said = [e["said"] for e in timeline_of(root)["events"]]
    assert said[:3] == ["Report written", "Report written",
                        "Round 2: determined as asked, so stopped"], said


def test_only_the_study_s_own_runs_have_their_reports_written_again(tmp_path):
    """The reports to write again were found under `runs/` by name: a run
    folder linked in from outside the study, or one its manifest does not
    list, was written into."""
    import shutil

    from fastmdxplora.batch.explorer import compare_the_runs_again
    from fastmdxplora.simulation.stopping import share_the_start

    study = tmp_path / "study"
    members = _replicas(study)
    outside = tmp_path / "outside"
    shutil.move(str(members[2]), str(outside))
    (study / "runs" / "r2").symlink_to(outside, target_is_directory=True)
    stray = study / "runs" / "stray"
    shutil.copytree(members[1], stray)
    assert share_the_start(members, "rmsd") > 0
    record = json.loads((stray / "analysis" / "rmsd" / "options.json").read_text(
        encoding="utf-8"))
    record["findings"]["mean"]["start_shared_with_replicas"] = 5
    (stray / "analysis" / "rmsd" / "options.json").write_text(json.dumps(record),
                                                             encoding="utf-8")
    reports = {run: (run / "report" / "report.md").read_text(encoding="utf-8")
               for run in (outside, stray)}
    compare_the_runs_again(study)
    for run, text in reports.items():
        assert (run / "report" / "report.md").read_text(encoding="utf-8") == text, run.name
    assert _as_recorded(_recorded(members[0])) in _said_in_its_report(members[0])


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
    assert "No record holds the frame count of Rg: the table counts it from the series." in section
    assert ("No record holds the mean, frames discarded, independent samples and error of Rg: "
            "the table works these out here from the series") in section


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


def test_the_convergence_sentence_names_no_record_where_none_is(tmp_path):
    """With no record at all it said the frame counts, means and errors
    were the analyses' records, then that no record held them."""
    from fastmdxplora.report.document import _convergence_section

    _analysed(tmp_path, "rg", 0.33 + 0.004 * np.random.default_rng(6).standard_normal(100),
              None)
    section = _convergence_section(tmp_path)
    assert "record, as written" not in section and "analyses' records" not in section
    assert "No record holds the frame count of Rg" in section


def test_the_thermodynamic_frame_count_is_its_record_s_and_traced_there(thermostatted):
    """The thermodynamics records how many rows it read (`samples`): the
    table said no record held the frame count, and the Report page traced
    it to the trajectory's frame count in `analysis_manifest.json`."""
    from fastmdxplora.report.document import _convergence_section

    options = thermostatted / "analysis" / "thermodynamics" / "options.json"
    options.write_text(json.dumps({"findings": {"thermodynamics": {**THERMO, "samples": 100}}}),
                       encoding="utf-8")
    (thermostatted / "analysis" / "analysis_manifest.json").write_text(
        json.dumps({"n_frames": 100}), encoding="utf-8")
    section = _convergence_section(thermostatted)
    assert "No record holds the frame count" not in section
    assert _row(section, "temperature")[1] == "100"
    (thermostatted / "report" / "report.md").write_text("# Report\n\n" + section,
                                                       encoding="utf-8")
    found = _traced(thermostatted, "100")
    assert found and {(n["record"]["file"], n["record"]["key"]) for n in found
                      if n.get("record")} == {("analysis/thermodynamics/options.json",
                                               "findings.thermodynamics.samples")}, found
    assert all(n["traced"] for n in found), found
