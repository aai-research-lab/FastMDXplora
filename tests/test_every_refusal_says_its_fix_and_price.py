"""Every refusal says what would fix it, and what the fix costs here.

A study that stopped said why, and there it ended: the person, or the Agent
answering for them, had to work out what to change, the command that runs
the change, and how long that would take. The software knew all three. A
stopped run is carried on by `fastmdx resume`; windows that sampled too
little are run again, longer, with `--rerun-window`; a setting refused
against the schema has a complete set of values. The study measured its
own speed, so the price is arithmetic.

What may be said is the registry's to decide, as for every refusal: the
values of a setting where the schema holds the whole set, the setting alone
where the value is a judgement, and nothing but where the decision is
recorded where the software does not know the answer.

The fixtures are records as the pipeline writes them: manifests, a
checkpoint and its sidecar, `cost.json`, `pmf.json`.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import yaml

from fastmdxplora.remedies import Price, remedies_of, remedy_for
from fastmdxplora.simulation.runner import CHECKPOINT_DIGEST_SUFFIX, write_checkpoint_sidecar

STOPPED = {"code": "simulation.run.stopped", "retryable": True,
           "message": "The run was asked to stop (SIGTERM) and stopped at step 100,000. "
                      "`fastmdx resume` carries it on."}
PROTONATION = {"code": "setup.chemistry.protonation_undetermined",
               "message": "LIG carries a carboxylic acid whose pKa sits within one unit "
                          "of pH 7.4, so which state dominates is not determined. "
                          "State which you intend.",
               "details": {"resname": "LIG", "ph": 7.4}}


def _config(folder: Path, **simulation) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "s1", "system": "1L2Y"}],
        "simulation": {"duration_ns": 0.5, "nvt_steps": 50_000, "npt_steps": 50_000,
                       "timestep_fs": 2.0, **simulation}}), encoding="utf-8")


def _speed(folder: Path, *, seconds: float = 600.0) -> None:
    """100,000 steps of 2 fs in ``seconds``: 3,000 s a nanosecond at 600."""
    (folder / "simulation").mkdir(parents=True, exist_ok=True)
    (folder / "simulation" / "cost.json").write_text(json.dumps(
        {"steps": 100_000, "seconds": seconds, "timestep_fs": 2.0, "platform": "CUDA"}),
        encoding="utf-8")


def _stopped_at(folder: Path, step: int) -> None:
    """A run stopped in production, its sealed checkpoint on disk."""
    simulation = folder / "simulation"
    simulation.mkdir(parents=True, exist_ok=True)
    (simulation / "production.dcd").write_bytes(b"frames")
    (simulation / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).write_text("s", encoding="utf-8")
    checkpoint = simulation / "checkpoint.chk"
    checkpoint.write_bytes(b"x")
    write_checkpoint_sidecar(checkpoint, stage="production", step=step, ensemble="npt",
                             temperature_K=300.0, timestep_fs=2.0, study=str(folder),
                             finished=False)


def _manifest(folder: Path, phase: str, refusal: dict) -> None:
    order = ["setup", "simulation", "analysis"]
    phases = [{"name": name, "status": "ok"} for name in order[:order.index(phase)]]
    phases.append({"name": phase, "status": "error", "message": refusal["message"],
                   "refusal": refusal})
    (folder / "manifest.json").write_text(json.dumps({"phases": phases}), encoding="utf-8")


# ---------------------------------------------------------------------------
# A study of one
# ---------------------------------------------------------------------------
class TestAStudyOfOne:
    def test_a_stopped_run_is_carried_on_for_what_remains(self, tmp_path):
        root = tmp_path / "study"
        _config(root)
        _speed(root)
        _stopped_at(root, 100_000)
        _manifest(root, "simulation", STOPPED)
        (remedy,) = remedies_of(root)
        assert remedy.code == "simulation.run.stopped"
        assert remedy.command == f"fastmdx resume {shlex.quote(str(root.resolve()))}"
        # 0.2 of the 0.5 ns planned is written; the rest, with no
        # equilibration, at 3,000 s a nanosecond.
        assert remedy.price.production_ns == pytest.approx(0.3)
        assert remedy.price.equilibration_ns == 0
        assert remedy.price.seconds == pytest.approx(900.0)
        text = remedy.as_text()
        assert "0.3 ns of production" in text and "on CUDA, about 15 min" in text
        assert "kept" in remedy.fix

    def test_a_refusal_only_the_person_can_answer_names_where_and_not_what(self, tmp_path):
        root = tmp_path / "study"
        _config(root, duration_ns=10.0)
        _manifest(root, "setup", PROTONATION)
        (remedy,) = remedies_of(root)
        assert remedy.decision and remedy.permitted is None
        assert remedy.settings == ("setup.ligand", "setup.ph", "setup.heterogens")
        assert remedy.fix == (
            "The software does not know the answer here, so it suggests nothing; the "
            "choice is yours. A choice of this kind is recorded in `setup.ligand`, "
            "`setup.ph` or `setup.heterogens`.")
        assert remedy.why.startswith("LIG carries a carboxylic acid")
        # Run again from the top; nothing here ran, so no time is given.
        assert remedy.price.production_ns == 10.0
        assert remedy.price.equilibration_ns == pytest.approx(0.2)
        assert remedy.price.seconds is None
        assert "no speed has been measured here" in remedy.as_text()

    def test_a_finished_study_needs_nothing(self, tmp_path):
        root = tmp_path / "study"
        _config(root)
        (root / "manifest.json").write_text(json.dumps({"phases": [
            {"name": p, "status": "ok"} for p in ("setup", "simulation", "analysis")]}),
            encoding="utf-8")
        assert remedies_of(root) == []
        assert remedies_of(tmp_path / "nothing here") == []


class TestWhatTheRegistryLetsBeSaid:
    def test_the_values_where_the_schema_holds_them_all(self):
        remedy = remedy_for({"code": "config.option.not_permitted",
                             "message": "Unknown box shape 'sphere'.",
                             "details": {"option": "box_shape", "context": "setup",
                                         "permitted": ["cube", "dodecahedron", "octahedron"]}})
        assert remedy.settings == ("setup.box_shape",)
        assert remedy.fix == "Set `setup.box_shape` to one of: cube, dodecahedron, octahedron."
        assert remedy.price is None

    def test_the_setting_alone_where_the_value_is_a_judgement(self):
        remedy = remedy_for({"code": "setup.ligand.clash",
                             "message": "Ligand 'LIG' clashes with the protein: 3 pairs.",
                             "details": {"worst_distance_nm": 0.1, "threshold_nm": 0.15}})
        assert remedy.settings == ("setup.ligand", "setup.ligand_clash_threshold_nm",
                                   "setup.check_ligand_clashes")
        assert "The value is yours to choose" in remedy.fix
        assert "0.1" not in remedy.fix and not remedy.decision

    def test_the_exact_step_where_there_is_one(self):
        install = "conda install -c conda-forge pdbfixer"
        remedy = remedy_for({"code": "environment.backend.missing",
                             "message": "Setup needs pdbfixer, which is not installed.",
                             "details": {"packages": ["pdbfixer"], "install_command": install}})
        assert remedy.command == install

    def test_a_value_the_registry_withholds_is_not_given_even_if_recorded(self):
        # `permitted` in the details of a code that may not disclose it.
        remedy = remedy_for({"code": "setup.chemistry.charge_undetermined",
                             "message": "The net charge of LIG was not determined.",
                             "details": {"resname": "LIG", "permitted": [-1, 0, 1]}})
        assert remedy.permitted is None and remedy.decision
        assert "-1" not in remedy.as_text()

    def test_a_path_to_correct(self):
        remedy = remedy_for({"code": "simulation.resume.unsealed",
                             "message": "The segment did not finish.",
                             "details": {"path": "runs/s/segment-001"}})
        assert remedy.fix == "Correct the file it names, runs/s/segment-001, as the refusal says."

    def test_a_setting_named_in_a_context_that_is_not_a_block(self):
        remedy = remedy_for({"code": "config.option.wrong_type",
                             "message": "systems entry option 'id' is a number.",
                             "details": {"option": "id", "context": "systems entry 2"}})
        assert remedy.settings == ("id",)

    def test_a_service_that_did_not_answer_is_waited_for(self, tmp_path):
        root = tmp_path / "study"
        _config(root, production_steps=250_000)
        _speed(root)
        _manifest(root, "setup", {"code": "environment.service.unreachable",
                                  "message": "RCSB did not answer.", "retryable": True})
        (remedy,) = remedies_of(root)
        assert remedy.fix.startswith("Once the service answers again, carry it on.")
        # Nothing was simulated: the whole plan, 250,000 steps of 2 fs.
        assert remedy.price.production_ns == pytest.approx(0.5)
        assert remedy.price.equilibration_ns == pytest.approx(0.2)

    def test_a_mean_short_of_samples_is_extended_by_what_it_asked(self, tmp_path):
        root = tmp_path / "study"
        _config(root)
        _speed(root)
        folder = root / "analysis" / "rmsd"
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": "rmsd", "findings": {
            "mean": {"shortfall": {"more_ns": 1.23, "lower_bound": False}}}}), encoding="utf-8")
        _manifest(root, "analysis", {"code": "analysis.sampling.too_few_independent",
                                     "message": "Too few independent samples."})
        (remedy,) = remedies_of(root)
        assert remedy.config == {"simulation": {"resume_from": str(root.resolve()),
                                                "extra_ns": 1.3}}
        assert remedy.price.production_ns == 1.3
        assert "```yaml\nsimulation:" in remedy.as_text()
        assert "```yaml" not in remedy.as_text(with_config=False)

    def test_an_unregistered_code_is_read_as_unclassified(self):
        remedy = remedy_for({"code": "no.such.code", "message": "Something went wrong."})
        assert remedy.code == "unclassified" and remedy.why == "Something went wrong."

    def test_the_price_says_what_it_is(self):
        assert Price(production_ns=0.0).as_text() == "no simulation"
        assert Price(production_ns=4.0, equilibration_ns=3.0, runs=2, seconds=7200.0,
                     platform="CPU", lower_bound=True).as_text() == (
            "at least 4 ns of production and 3 ns of equilibration across 2 runs; at "
            "this study's own speed on CPU, about 2 h one after another")


# ---------------------------------------------------------------------------
# A study of several
# ---------------------------------------------------------------------------
def _batch(root: Path, runs: list[dict], planned: list[dict] | None = None,
           config: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "batch_manifest.json").write_text(json.dumps({
        "kind": "batch", "config": config, "runs": runs,
        "planned": planned or [{"run_id": r["run_id"], "options": {}} for r in runs]}),
        encoding="utf-8")
    _config(root)


def _run(root: Path, run_id: str, status: str, *, message: str = "",
         refusal: dict | None = None, error_type: str | None = None) -> dict:
    phases = []
    if refusal is not None:
        phases.append({"name": "simulation", "status": "error",
                       "message": refusal["message"], "refusal": refusal})
    return {"run_id": run_id, "status": status, "output_dir": str(root / "runs" / run_id),
            "output_dir_relative": f"runs/{run_id}", "phases": phases,
            "message": message, "error_type": error_type}


class TestAStudyOfSeveral:
    def test_stopped_runs_and_runs_not_started_are_one_command(self, tmp_path):
        root = tmp_path / "campaign"
        for name in ("seed0", "seed1", "seed2"):
            _config(root / "runs" / name)
        # Only the run that finished measured the speed.
        _speed(root / "runs" / "seed0")
        _stopped_at(root / "runs" / "seed1", 100_000)
        _batch(root, [
            _run(root, "seed0", "ok"),
            _run(root, "seed1", "error", refusal=STOPPED, error_type="Stopped"),
            _run(root, "seed2", "skipped",
                 message="Not started: the study was asked to stop."),
        ])
        (remedy,) = remedies_of(root)
        assert remedy.covers == ("seed1", "seed2")
        assert remedy.command == f"fastmdx resume {shlex.quote(str(root.resolve()))}"
        assert remedy.why == "1 run stopped where it can be carried on and 1 run did not start."
        # seed1's remaining 0.3 ns, and seed2's whole plan from the top.
        assert remedy.price.production_ns == pytest.approx(0.8)
        assert remedy.price.equilibration_ns == pytest.approx(0.2)
        assert remedy.price.runs == 2
        assert remedy.price.seconds == pytest.approx(3000.0 * (0.3 + 0.5 + 0.2))
        assert remedy.fix == ("Carry it on from where it stopped. The run that did not "
                              "start is run. Production already written is kept, and the "
                              "analyses run over the whole.")

    def test_a_failed_run_is_fixed_where_it_failed(self, tmp_path):
        root = tmp_path / "campaign"
        _config(root / "runs" / "lig2")
        _batch(root, [_run(root, "lig1", "ok"),
                      _run(root, "lig2", "error", refusal={**PROTONATION})])
        (remedy,) = remedies_of(root)
        assert remedy.where == "lig2" and remedy.decision


# ---------------------------------------------------------------------------
# Umbrella windows
# ---------------------------------------------------------------------------
def _windows(root: Path, n: int = 4, *, duration_ns: float = 1.0) -> list[dict]:
    planned = []
    for index in range(n):
        run_id = f"w{index:02d}"
        _config(root / "runs" / run_id, duration_ns=duration_ns)
        _speed(root / "runs" / run_id)
        planned.append({"run_id": run_id, "options": {"simulation": {"umbrella": {
            "index": index, "centre": 0.4 + 0.1 * index, "force_constant": 1000.0,
            "collective_variable": "distance"}}}})
    return planned


def _pmf(root: Path, **payload) -> None:
    (root / "pmf.json").write_text(json.dumps({"pmf": None, **payload}), encoding="utf-8")


class TestUmbrellaWindows:
    def test_thin_windows_are_run_again_longer_by_what_they_lacked(self, tmp_path):
        root = tmp_path / "umbrella"
        planned = _windows(root)
        config = tmp_path / "study.yml"
        config.write_text("systems: []\n", encoding="utf-8")
        _batch(root, [_run(root, p["run_id"], "ok") for p in planned], planned,
               config=str(config))
        _pmf(root, refused="2 of 4 windows recorded fewer than 200 values after "
                           "equilibration was discarded. More here.",
             thin=[{"window": 1, "samples": 50}, {"window": 3, "samples": 100}],
             plan={"minimum_samples": 200})
        (remedy,) = remedies_of(root)
        assert remedy.where == "windows 1 and 3" and remedy.covers == ("w01", "w03")
        # The thinner needs four times its nanosecond; the other then has more.
        assert remedy.command == (
            f"fastmdx explore -c {shlex.quote(str(config))} "
            f"--output {shlex.quote(str(root.resolve()))} "
            "--simulate-duration-ns 4 --rerun-window 1 3")
        assert remedy.settings == ("simulation.duration_ns",)
        assert remedy.price.production_ns == 8.0 and remedy.price.runs == 2
        assert remedy.price.seconds == pytest.approx(3000.0 * 2 * (4.0 + 0.2))
        assert remedy.fix == ("Run them again with 4 ns of production each, which should "
                              "record the 200 values a histogram needs, keeping every "
                              "other window.")

    def test_the_command_is_one_the_command_line_takes(self, tmp_path):
        from fastmdxplora.cli.main import _build_parser

        root = tmp_path / "umbrella"
        planned = _windows(root)
        _batch(root, [_run(root, p["run_id"], "ok") for p in planned], planned)
        _pmf(root, refused="1 of 4 windows recorded fewer than 200 values.",
             thin=[{"window": 2, "samples": 150}], plan={"minimum_samples": 200})
        (remedy,) = remedies_of(root)
        args = _build_parser().parse_args(shlex.split(remedy.command)[1:])
        assert args.rerun_windows == [2]
        assert args.config == str(root.resolve() / "resolved_config.yml")
        assert args.simulate__duration_ns == pytest.approx(1.4)

    def test_a_gap_is_answered_with_the_design_the_windows_measured(self, tmp_path):
        root = tmp_path / "umbrella"
        planned = _windows(root)
        _batch(root, [_run(root, p["run_id"], "ok") for p in planned], planned)
        _pmf(root, refused="Adjacent windows do not overlap, so no free energy can be "
                           "computed across the gap: windows 1 and 2 share 0.4%.",
             thin=[], next_study={"n_windows": 6, "covers": [0.4, 0.9],
                                  "centres": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                                  "force_constants": [1000, 1000, 2500, 2500, 1000, 1000],
                                  "worst_predicted_overlap": 0.12})
        (remedy,) = remedies_of(root)
        assert remedy.config == {"simulation": {"umbrella": {
            "centres": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
            "force_constant": [1000, 1000, 2500, 2500, 1000, 1000]}}}
        assert "6 windows from 0.4 to 0.9, worst overlap 0.12 predicted" in remedy.fix
        assert "longer would not close the gaps" in remedy.fix
        assert remedy.price.production_ns == 6.0 and remedy.price.runs == 6

    def test_windows_that_ran_otherwise_are_named_to_run_again(self, tmp_path):
        root = tmp_path / "umbrella"
        planned = _windows(root)
        # Window 2 ran with a softer spring than the config now gives it.
        for index in range(4):
            window = root / "runs" / f"w{index:02d}" / "simulation"
            (window / "umbrella_window.json").write_text(
                json.dumps({"centre": 0.4 + 0.1 * index,
                            "force_constant": 500.0 if index == 2 else 1000.0}),
                encoding="utf-8")
        _batch(root, [_run(root, p["run_id"], "ok") for p in planned], planned)
        _pmf(root, refused="The windows did not all run with the settings the config now "
                           "gives them: window 2 ran at 0.6 nm with 500 kJ/mol/nm^2.")
        (remedy,) = remedies_of(root)
        assert remedy.code == "config.option.conflicting" and remedy.where == "window 2"
        assert remedy.command.endswith("--rerun-window 2")
        assert "restore the settings they ran with, which costs nothing" in remedy.fix
        assert remedy.price.production_ns == 1.0

    def test_a_window_that_recorded_nothing_is_not_given_a_length(self, tmp_path):
        root = tmp_path / "umbrella"
        planned = _windows(root)
        _batch(root, [_run(root, p["run_id"], "ok") for p in planned], planned)
        _pmf(root, refused="1 of 4 windows recorded fewer than 200 values.",
             thin=[{"window": 0, "samples": 0}], plan={"minimum_samples": 200})
        (remedy,) = remedies_of(root)
        assert "cannot be read from a window that recorded nothing" in remedy.fix
        assert remedy.command.endswith("--rerun-window 0") and remedy.price is None

    def test_a_failed_window_runs_again_with_those_it_kept_from_starting(self, tmp_path):
        root = tmp_path / "umbrella"
        planned = _windows(root)
        unstable = {"code": "simulation.run.unstable",
                    "message": "The integration produced NaN positions at step 4,000."}
        _batch(root, [
            _run(root, "w00", "ok"),
            _run(root, "w01", "error", refusal=unstable),
            _run(root, "w02", "skipped", message="Not submitted: window 'w01' failed."),
            _run(root, "w03", "skipped", message="Not submitted: window 'w01' failed."),
        ], planned)
        _pmf(root, refused="These windows produced no sampling: window 1 (no COLVAR).")
        (remedy,) = remedies_of(root)
        assert remedy.where == "window 1" and remedy.decision
        assert remedy.command.endswith("--rerun-window 1 2 3")
        assert "2 windows of those never started" in remedy.fix
        assert remedy.price.runs == 3 and remedy.price.production_ns == 3.0


# ---------------------------------------------------------------------------
# Where it is said
# ---------------------------------------------------------------------------
class TestWhereItIsSaid:
    def test_the_agent_reads_it_from_a_run_of_the_campaign(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _remedies_summary

        root = tmp_path / "campaign"
        _config(root / "runs" / "seed1")
        _speed(root / "runs" / "seed1")
        _stopped_at(root / "runs" / "seed1", 100_000)
        _batch(root, [_run(root, "seed0", "ok"),
                      _run(root, "seed1", "error", refusal=STOPPED, error_type="Stopped")])
        said = _remedies_summary(root / "runs" / "seed1")
        assert said.startswith("what would fix it")
        assert f"fastmdx resume {shlex.quote(str(root.resolve()))}" in said
        assert "about 15 min" in said

    def test_the_agent_is_told_to_answer_from_it(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("why did it stop?")
        assert 'the "what would fix it" block' in prompt
        assert "never offer a value the block does not give" in prompt

    def test_the_console_says_it(self, tmp_path):
        from fastmdxplora.batch.explorer import _what_would_fix_it

        root = tmp_path / "study"
        _config(root)
        _speed(root)
        _stopped_at(root, 100_000)
        _manifest(root, "simulation", STOPPED)
        lines = _what_would_fix_it(root)
        assert lines[0] == "What would fix it:"
        assert lines[2].startswith("    Fix: Carry it on from where it stopped.")
        assert lines[3] == f"    Run: fastmdx resume {shlex.quote(str(root.resolve()))}"
        assert lines[4].startswith("    Costs 0.3 ns of production")

    def test_resume_refusing_gives_the_remedies_to_a_program(self, tmp_path, capsys):
        from fastmdxplora.cli.main import main

        root = tmp_path / "study"
        _config(root, duration_ns=10.0)
        _manifest(root, "setup", PROTONATION)
        assert main(["resume", str(root), "--json"]) == 1
        answer = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert answer["ok"] is False
        (remedy,) = answer["remedies"]
        assert remedy["code"] == "setup.chemistry.protonation_undetermined"
        assert remedy["decision"] is True and remedy["permitted"] is None
        # And to a person, under the reason it gives.
        assert main(["resume", str(root)]) == 1
        said = capsys.readouterr().err
        assert "What would fix it:" in said and "`setup.ligand`" in said
