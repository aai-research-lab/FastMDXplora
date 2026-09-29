"""A study with a budget is priced on every run it makes.

The staged route runs setup alone, reads the particle count setup recorded,
prices the rest and runs it only if it fits. It read the count from
``setup/`` beside the study, which is where a study of one run keeps it. A
study of several keeps it elsewhere: each run's system under ``runs/<id>/``
and an umbrella study's one shared system under ``shared_setup/``. So every
umbrella study, replica sweep and campaign given a budget ran setup, found
no count, and refused; ``--autonomous``, which requires a budget, could run
none of them. An umbrella study asked only to prepare also prepared one
system per window rather than the one its windows share.

Here the batch layer is the real one, with the work of each run stood in
for, so the layout the pricing reads is the layout the batch layer writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.agent import run_in_stages
from fastmdxplora.agent.staged import _runs_of
from fastmdxplora.cost import Estimate, calibrate, estimate_runs, total_steps
from fastmdxplora.orchestrator import RunResult

PEPTIDE = (
    "ATOM      1  N   ALA A   1       1.201   0.847   0.000  1.00  0.00           N\n"
    "ATOM      2  CA  ALA A   1       2.643   0.847   0.000  1.00  0.00           C\n"
    "ATOM      3  C   ALA A   1       3.181   2.270   0.000  1.00  0.00           C\n"
    "ATOM      4  O   ALA A   1       2.420   3.240   0.000  1.00  0.00           O\n"
    "ATOM      5  N   ALA A   2       4.500   2.400   0.000  1.00  0.00           N\n"
    "ATOM      6  CA  ALA A   2       5.100   3.700   0.000  1.00  0.00           C\n"
    "ATOM      7  C   ALA A   2       6.600   3.600   0.000  1.00  0.00           C\n"
    "ATOM      8  O   ALA A   2       7.200   2.500   0.000  1.00  0.00           O\n"
    "ATOM      9  N   ALA A   3       7.200   4.700   0.000  1.00  0.00           N\n"
    "ATOM     10  CA  ALA A   3       8.650   4.800   0.000  1.00  0.00           C\n"
    "ATOM     11  C   ALA A   3       9.100   6.200   0.000  1.00  0.00           C\n"
    "ATOM     12  O   ALA A   3       8.400   7.200   0.000  1.00  0.00           O\n"
    "END\n"
)

#: Particles each preparation records, by run: replicas of one system differ
#: by where the water went.
COUNTS = {"pep__random-seed-1": 30_654, "pep__random-seed-2": 30_803, "shared": 36_075}

SIMULATION = {"nvt_steps": 1_000, "npt_steps": 2_000, "production_steps": 10_000}


class _Batch:
    """The work of each run, stood in for; which runs were given what."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, spec_dict, run_out, include, exclude, verbose, device_override,
                 quiet=True, force=False, resume=False):
        out = Path(run_out)
        simulation = (spec_dict.get("options") or {}).get("simulation") or {}
        phases = [p for p in ("setup", "simulation", "analysis", "report")
                  if (not include or p in include) and p not in (exclude or [])]
        self.calls.append({"run": spec_dict["run_id"], "out": out, "phases": phases,
                           "setup_from": simulation.get("setup_from")})
        if "setup" in phases:
            setup = out / "setup"
            setup.mkdir(parents=True, exist_ok=True)
            for name in ("system.xml", "state.xml"):
                (setup / name).write_text("<x/>", encoding="utf-8")
            (setup / "topology.pdb").write_text(PEPTIDE, encoding="utf-8")
            count = COUNTS.get(spec_dict["run_id"], COUNTS["shared"])
            (setup / "setup_parameters.json").write_text(
                json.dumps({"n_atoms_solvated": count}), encoding="utf-8")
        return RunResult(run_id=spec_dict["run_id"], system=spec_dict["system"],
                         status="ok", output_dir=out,
                         sweep_values=spec_dict.get("sweep_values") or {})


@pytest.fixture
def batch(monkeypatch, tmp_path):
    from fastmdxplora.batch import explorer

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    stand_in = _Batch()
    monkeypatch.setattr(explorer, "_execute_run", stand_in)
    (tmp_path / "peptide.pdb").write_text(PEPTIDE, encoding="utf-8")
    calibrate(particles=30_000, steps=5_000, seconds=42.0,
              platform_name="CUDA", precision="mixed")
    return stand_in


def _replicas(root: Path) -> dict:
    return {"systems": [{"id": "pep", "system": str(root / "peptide.pdb")}],
            "sweep": {"simulation.random_seed": [1, 2]},
            "simulation": dict(SIMULATION)}


def _umbrella(root: Path, **extra) -> dict:
    return {"systems": [{"id": "pep", "system": str(root / "peptide.pdb")}],
            "simulation": dict(SIMULATION, **extra, umbrella={
                "collective_variable": "radius_of_gyration",
                "selection": "protein and name CA",
                "from": 0.4, "to": 0.6, "n_windows": 3, "force_constant": 5000})}


def _k() -> float:
    return 42.0 / (30_000 * 5_000)


class TestTheRunsAStudyMakes:

    def test_one_run_is_the_study_itself(self, tmp_path) -> None:
        config = {"systems": [{"id": "a", "system": "x.pdb"}], "simulation": dict(SIMULATION)}
        runs, simulate_from = _runs_of(config, tmp_path)
        assert runs == [("the study", tmp_path, 13_000)]
        assert simulate_from == tmp_path / "setup"

    def test_replicas_are_each_their_own(self, tmp_path) -> None:
        runs, simulate_from = _runs_of(_replicas(tmp_path), tmp_path / "out")
        assert [(name, where.relative_to(tmp_path)) for name, where, _ in runs] == [
            (name, Path("out/runs") / name) for name in ("pep__random-seed-1", "pep__random-seed-2")]
        assert simulate_from is None, "each run simulates the system it prepared"

    def test_windows_share_one_system(self, tmp_path) -> None:
        runs, simulate_from = _runs_of(_umbrella(tmp_path), tmp_path / "out")
        assert len(runs) == 3
        assert {where for _, where, _ in runs} == {tmp_path / "out" / "shared_setup" / "setup"}
        assert simulate_from == tmp_path / "out" / "shared_setup" / "setup"
        assert all(steps == 13_000 for _, _, steps in runs), "each window equilibrates"

    def test_the_pull_that_seeds_them_is_a_run_too(self, tmp_path) -> None:
        runs, _ = _runs_of(_umbrella(tmp_path, steered={
            "collective_variable": "radius_of_gyration", "selection": "protein and name CA",
            "from": 0.4, "to": 0.6, "force_constant": 5000, "steps": 10_000}),
            tmp_path / "out")
        assert [name for name, _, _ in runs][-1] == "the pull that seeds the windows"
        assert len(runs) == 4

    def test_a_named_system_is_where_the_windows_start(self, tmp_path) -> None:
        named = tmp_path / "earlier" / "shared_setup" / "setup"
        named.mkdir(parents=True)
        for name in ("system.xml", "state.xml", "topology.pdb"):
            (named / name).write_text("x", encoding="utf-8")
        runs, simulate_from = _runs_of(
            _umbrella(tmp_path, setup_from=str(tmp_path / "earlier")), tmp_path / "out")
        assert {where for _, where, _ in runs} == {named}
        assert simulate_from is None, "the study already names it"

    def test_a_config_that_does_not_expand_is_one_run(self, tmp_path) -> None:
        """Setup refuses it, with the reason; pricing does not guess first."""
        runs, _ = _runs_of({"systems": [], "simulation": {"nope": 1}}, tmp_path)
        assert [name for name, _, _ in runs] == ["the study"]


class TestTheEstimateIsTheSum:

    def test_every_run_is_counted(self) -> None:
        from fastmdxplora.cost import Calibration

        measured = Calibration(seconds_per_particle_step=1e-9, particles=1, steps=1,
                               seconds=1.0, machine={}, measured_at="")
        whole = estimate_runs([(30_654, 13_000), (30_803, 13_000)], calibration=measured)
        assert whole.seconds == pytest.approx(1e-9 * 13_000 * (30_654 + 30_803))
        assert (whole.runs, whole.steps, whole.particles, whole.fewest_particles) == (
            2, 26_000, 30_803, 30_654)
        assert "for 2 runs, 26,000 steps in all, on 30,654 to 30,803 particles" in str(whole)
        assert whole.as_record()["runs"] == 2

    def test_runs_alike_say_each(self) -> None:
        from fastmdxplora.cost import Calibration

        measured = Calibration(seconds_per_particle_step=1e-9, particles=1, steps=1,
                               seconds=1.0, machine={}, measured_at="")
        assert "on 36,075 particles each" in str(
            estimate_runs([(36_075, 10)] * 3, calibration=measured))

    def test_nothing_to_price_is_said(self) -> None:
        from fastmdxplora.refusals import StudyError

        with pytest.raises(StudyError):
            estimate_runs([])

    def test_one_run_reads_as_it_did(self) -> None:
        from fastmdxplora.cost import Calibration

        measured = Calibration(seconds_per_particle_step=1e-9, particles=1, steps=1,
                               seconds=1.0, machine={}, measured_at="")
        one = estimate_runs([(100, 10)], calibration=measured)
        assert isinstance(one, Estimate) and "runs" not in str(one)


class TestAReplicaSweep:

    def test_it_is_priced_on_both_and_refused_over_budget(self, batch, tmp_path) -> None:
        staged = run_in_stages(_replicas(tmp_path), tmp_path / "out", budget_hours=1e-6,
                               platform_name="CUDA", precision="mixed")
        assert staged.refusal is not None
        assert staged.refusal.code == "environment.budget.exhausted"
        assert staged.runs == 2 and staged.particles == 30_803
        want = _k() * 13_000 * (30_654 + 30_803)
        assert staged.estimate_seconds == pytest.approx(want)
        assert staged.refusal.details["runs"] == 2
        assert [c["phases"] for c in batch.calls] == [["setup"], ["setup"]]

    def test_within_budget_each_simulates_what_it_prepared(self, batch, tmp_path) -> None:
        staged = run_in_stages(_replicas(tmp_path), tmp_path / "out", budget_hours=100,
                               platform_name="CUDA", precision="mixed")
        assert staged.refusal is None and staged.simulated
        assert str(staged) == "ran, 2 runs of up to 30,803 particles"
        assert staged.as_record()["runs"] == 2
        second = batch.calls[2:]
        assert [c["run"] for c in second] == ["pep__random-seed-1", "pep__random-seed-2"]
        assert all("setup" not in c["phases"] and c["setup_from"] is None for c in second)
        assert [c["out"] for c in second] == [c["out"] for c in batch.calls[:2]]

    def test_a_run_whose_setup_left_no_count_is_named(self, batch, tmp_path, monkeypatch) -> None:
        def without_one(*args, **kwargs):
            result = _Batch.__call__(batch, *args, **kwargs)
            if args[0]["run_id"] == "pep__random-seed-2":
                (Path(args[1]) / "setup" / "setup_parameters.json").unlink()
            return result

        from fastmdxplora.batch import explorer

        monkeypatch.setattr(explorer, "_execute_run", without_one)
        staged = run_in_stages(_replicas(tmp_path), tmp_path / "out", budget_hours=100,
                               platform_name="CUDA", precision="mixed")
        assert staged.refusal.code == "setup.structure.undetermined"
        assert "(pep__random-seed-2)" in staged.refusal.message


class TestAnUmbrellaStudy:

    def test_it_prepares_once_and_is_priced_on_every_window(self, batch, tmp_path) -> None:
        staged = run_in_stages(_umbrella(tmp_path), tmp_path / "out", budget_hours=1e-6,
                               platform_name="CUDA", precision="mixed")
        assert [c["out"].name for c in batch.calls] == ["shared_setup"], (
            "one preparation, for every window, and no window run")
        assert staged.refusal.code == "environment.budget.exhausted"
        assert staged.runs == 3
        assert staged.estimate_seconds == pytest.approx(_k() * 13_000 * 36_075 * 3)

    def test_within_budget_the_windows_simulate_the_shared_system(self, batch, tmp_path) -> None:
        staged = run_in_stages(_umbrella(tmp_path), tmp_path / "out", budget_hours=100,
                               platform_name="CUDA", precision="mixed")
        assert staged.simulated, staged.refusal
        windows = batch.calls[1:]
        shared = tmp_path / "out" / "shared_setup" / "setup"
        assert len(windows) == 3
        assert all("setup" not in c["phases"] for c in windows)
        assert {c["setup_from"] for c in windows} == {str(shared)}


class TestAStudyOnlyPreparing:

    def test_an_umbrella_study_prepares_the_one_system(self, batch, tmp_path) -> None:
        from fastmdxplora import FastMDXplora

        config = dict(_umbrella(tmp_path), include_phase=["setup"])
        results = FastMDXplora(config_data=config, output_dir=str(tmp_path / "out")).explore()
        assert [c["out"].name for c in batch.calls] == ["shared_setup"]
        assert [r.status for r in results] == ["skipped"] * 3
        assert "Prepared once" in results[0].message
        manifest = json.loads((tmp_path / "out" / "batch_manifest.json").read_text())
        assert manifest["n_runs"] == 3

    def test_with_a_named_system_it_prepares_nothing_and_pulls_nothing(
            self, batch, tmp_path, monkeypatch) -> None:
        from fastmdxplora.batch import explorer

        named = tmp_path / "earlier" / "setup"
        named.mkdir(parents=True)
        for name in ("system.xml", "state.xml"):
            (named / name).write_text("x", encoding="utf-8")
        (named / "topology.pdb").write_text(PEPTIDE, encoding="utf-8")
        seeded: list[Path] = []
        monkeypatch.setattr(explorer.BatchExplorer, "_give_each_window_its_start",
                            lambda self, prepared: seeded.append(prepared))
        study = explorer.BatchExplorer(
            config_data=_umbrella(tmp_path, setup_from=str(tmp_path / "earlier")),
            output_dir=str(tmp_path / "out"))
        assert study._maybe_prepare_once(["setup"], None) is None
        assert seeded == [], "seeding is simulation; only preparing was asked"
        assert study._maybe_prepare_once(["setup", "simulation"], None) is None
        assert seeded == [tmp_path / "earlier"]


def test_total_steps_is_what_each_run_integrates() -> None:
    assert total_steps(SIMULATION) == 13_000
