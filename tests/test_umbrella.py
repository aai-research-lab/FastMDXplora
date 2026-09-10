"""A free energy along a coordinate, from equilibrium sampling at each point.

Steered MD drags a system and reports work that depends on how fast it was
dragged. Umbrella sampling holds it at a series of positions, lets each
equilibrate, and recombines the sampling -- so nothing is hurried and nothing
is dissipated.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from fastmdxplora.simulation.umbrella import KB_KJ

#: A double well with a barrier of exactly 10 kJ/mol, so the recovered value
#: can be checked against a number rather than against plausibility.
def _true_free_energy(x):
    return 10.0 * (np.asarray(x) ** 2 - 1.0) ** 2


def _sample(centre, force, n=6000, seed=0, temperature_K=300.0, width=0.8):
    """Draw from the biased distribution the window would sample."""
    kT = KB_KJ * temperature_K
    rng = np.random.RandomState(seed)
    drawn = []
    while len(drawn) < n:
        x = rng.uniform(centre - width, centre + width, 400)
        energy = _true_free_energy(x) + 0.5 * force * (x - centre) ** 2
        accept = np.exp(-(energy - energy.min()) / kT)
        drawn.extend(x[rng.uniform(size=x.size) < accept])
    return np.array(drawn[:n])


class TestItRecoversAFreeEnergyItWasGiven:
    """The check that matters: sample a landscape whose barrier is known and
    see whether the number comes back."""

    def test_the_barrier_is_recovered(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

        plan = plan_windows({
            "collective_variable": "distance", "from": -1.6, "to": 1.6,
            "n_windows": 17, "force_constant": 200.0})
        samples = {w.index: _sample(w.centre, w.force_constant, seed=w.index)
                   for w in plan.windows}

        result = compute_pmf(samples, plan, temperature_K=300.0,
                             bootstrap_resamples=0)
        assert result["refused"] is None

        x = np.array(result["pmf"]["coordinate"])
        f = np.array(result["pmf"]["free_energy_kjmol"])
        inside = (x > -1.4) & (x < 1.4) & np.isfinite(f)

        truth = _true_free_energy(x[inside])
        truth = truth - truth.min()
        error = np.sqrt(((f[inside] - truth) ** 2).mean())
        assert error < 1.0, f"recovered PMF is off by {error:.2f} kJ/mol RMS"

    def test_the_barrier_height_is_about_right(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

        plan = plan_windows({
            "collective_variable": "distance", "from": -1.6, "to": 1.6,
            "n_windows": 17, "force_constant": 200.0})
        result = compute_pmf(
            {w.index: _sample(w.centre, w.force_constant, seed=w.index)
             for w in plan.windows}, plan, bootstrap_resamples=0)

        x = np.array(result["pmf"]["coordinate"])
        f = np.array(result["pmf"]["free_energy_kjmol"])
        at_barrier = f[np.argmin(np.abs(x))]
        assert at_barrier == pytest.approx(10.0, abs=1.5)


class TestWindowsThatDoNotOverlap:
    """Recombination stitches histograms together. Where two neighbours never
    visit the same value there is nothing to stitch, and a curve through the
    gap is interpolation presented as a measurement.
    """

    @staticmethod
    def _separated():
        from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

        plan = plan_windows({
            "collective_variable": "distance", "from": -1.5, "to": 1.5,
            "n_windows": 4, "force_constant": 3000.0})
        samples = {w.index: _sample(w.centre, w.force_constant, n=4000,
                                    seed=w.index, width=0.5)
                   for w in plan.windows}
        return compute_pmf(samples, plan, bootstrap_resamples=0)

    def test_no_free_energy_is_returned(self) -> None:
        assert self._separated()["pmf"] is None

    def test_the_gap_is_named(self) -> None:
        refusal = self._separated()["refused"]
        assert "do not overlap" in refusal
        assert "windows 0 and 1" in refusal

    def test_and_what_would_close_it(self) -> None:
        """More windows or a softer constant. Not more sampling."""
        refusal = self._separated()["refused"]
        assert "More windows" in refusal
        assert "Sampling for longer will not" in refusal

    def test_the_overlaps_are_reported_either_way(self) -> None:
        """So somebody can see how close the windows came."""
        result = self._separated()
        assert result["overlaps"]
        assert all("overlap" in o for o in result["overlaps"])


class TestPlanningWindows:
    def test_a_range_and_a_count_becomes_windows(self) -> None:
        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({"collective_variable": "distance", "from": 0.4,
                             "to": 2.0, "n_windows": 9,
                             "force_constant": 500.0})
        assert len(plan.windows) == 9
        assert plan.windows[0].centre == pytest.approx(0.4)
        assert plan.windows[-1].centre == pytest.approx(2.0)

    def test_explicit_centres_are_taken_as_given(self) -> None:
        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({"collective_variable": "distance",
                             "centres": [0.4, 0.5, 0.7, 1.2],
                             "force_constant": 500.0})
        assert [w.centre for w in plan.windows] == [0.4, 0.5, 0.7, 1.2]

    def test_the_force_constant_has_no_default(self) -> None:
        """It sets how far a window wanders and therefore whether neighbours
        overlap. There is no value right for an arbitrary coordinate."""
        from fastmdxplora.simulation.umbrella import plan_windows

        with pytest.raises(ValueError, match="force_constant"):
            plan_windows({"collective_variable": "distance", "from": 0.0,
                          "to": 1.0, "n_windows": 5})

    def test_one_window_is_not_umbrella_sampling(self) -> None:
        from fastmdxplora.simulation.umbrella import plan_windows

        with pytest.raises(ValueError, match="at least two windows"):
            plan_windows({"collective_variable": "distance",
                          "centres": [1.0], "force_constant": 500.0})

    def test_windows_become_a_sweep(self) -> None:
        """One system at many restraint positions is the shape the batch
        machinery already runs, so it schedules them rather than a second
        one."""
        from fastmdxplora.simulation.umbrella import plan_windows, windows_as_sweep

        plan = plan_windows({"collective_variable": "distance", "from": 0.0,
                             "to": 1.0, "n_windows": 5,
                             "force_constant": 500.0})
        runs = windows_as_sweep(plan)
        assert len(runs) == 5
        assert runs[0]["id"] == "window_00"
        assert runs[-1]["umbrella_centre"] == pytest.approx(1.0)


class TestOverlapItself:
    def test_identical_sampling_overlaps_completely(self) -> None:
        from fastmdxplora.simulation.umbrella import overlap_between

        x = np.random.RandomState(0).normal(size=2000)
        assert overlap_between(x, x) == pytest.approx(1.0, abs=0.05)

    def test_separated_sampling_does_not_overlap(self) -> None:
        from fastmdxplora.simulation.umbrella import overlap_between

        rng = np.random.RandomState(0)
        assert overlap_between(rng.normal(0, 0.1, 2000),
                               rng.normal(5, 0.1, 2000)) < 0.01

    def test_empty_sampling_overlaps_nothing(self) -> None:
        from fastmdxplora.simulation.umbrella import overlap_between

        assert overlap_between(np.array([]), np.array([1.0, 2.0])) == 0.0


class TestExpandingIntoRuns:
    """An umbrella job is one system held at many positions, which is the
    shape the batch machinery already runs -- so the scheduling, parallelism
    and per-GPU pinning come from the code that already does those things."""

    @staticmethod
    def _config():
        return {
            "output": "runs/pmf",
            "systems": [{"system": "181L"}],
            "simulation": {"duration_ns": 5, "umbrella": {
                "collective_variable": "ligand_distance",
                "site_selection": "resid 84 to 121 and name CA",
                "from": 0.4, "to": 2.0, "n_windows": 9,
                "force_constant": 1000.0}},
        }

    def test_one_block_becomes_a_run_per_window(self) -> None:
        from fastmdxplora.simulation.umbrella import expand_umbrella

        expanded = expand_umbrella(self._config())
        assert len(expanded["systems"]) == 9
        assert expanded["systems"][0]["id"] == "window_00"

    def test_each_run_holds_its_own_position(self) -> None:
        from fastmdxplora.simulation.umbrella import expand_umbrella

        expanded = expand_umbrella(self._config())
        centres = [e["simulation"]["umbrella"]["centre"]
                   for e in expanded["systems"]]
        assert centres == sorted(centres)
        assert centres[0] == pytest.approx(0.4)
        assert centres[-1] == pytest.approx(2.0)

    def test_shared_settings_survive(self) -> None:
        from fastmdxplora.simulation.umbrella import expand_umbrella

        expanded = expand_umbrella(self._config())
        assert expanded["simulation"]["duration_ns"] == 5

    def test_the_block_is_consumed(self) -> None:
        """Leaving it would have every run try to expand it again."""
        from fastmdxplora.simulation.umbrella import expand_umbrella

        assert "umbrella" not in expand_umbrella(self._config())["simulation"]

    def test_a_config_without_one_is_untouched(self) -> None:
        from fastmdxplora.simulation.umbrella import expand_umbrella

        plain = {"output": "runs/x", "systems": [{"system": "1UBQ"}]}
        assert expand_umbrella(plain) == plain

    def test_several_systems_at_once_is_refused(self) -> None:
        """A window set for each would be several separate free energies,
        each needing its own overlap check."""
        from fastmdxplora.simulation.umbrella import expand_umbrella

        config = self._config()
        config["systems"].append({"system": "1UBQ"})
        with pytest.raises(ValueError, match="one system at many positions"):
            expand_umbrella(config)


class TestReadingTheSamplingBack:
    @staticmethod
    def _written(tmp_path, plan, n=3000):
        for window in plan.windows:
            directory = tmp_path / f"window_{window.index:02d}" / "simulation"
            directory.mkdir(parents=True)
            values = _sample(window.centre, window.force_constant,
                             n=n, seed=window.index)
            directory.joinpath("COLVAR").write_text(
                "#! FIELDS time cv restraint.bias\n"
                + "\n".join(f"{i * 0.1:.1f} {v:.6f} 0.0"
                            for i, v in enumerate(values)),
                encoding="utf-8")

    def test_a_free_energy_comes_back_from_files_on_disk(self, tmp_path) -> None:
        from fastmdxplora.simulation.umbrella import (
            collect_samples,
            compute_pmf,
            plan_windows,
        )

        plan = plan_windows({
            "collective_variable": "distance", "from": -1.6, "to": 1.6,
            "n_windows": 17, "force_constant": 200.0})
        self._written(tmp_path, plan)

        directories = {w.index: tmp_path / f"window_{w.index:02d}"
                       for w in plan.windows}
        result = compute_pmf(collect_samples(directories), plan,
                             bootstrap_resamples=0)
        x = np.array(result["pmf"]["coordinate"])
        f = np.array(result["pmf"]["free_energy_kjmol"])
        assert f[np.argmin(np.abs(x))] == pytest.approx(10.0, abs=1.5)

    def test_the_approach_to_each_window_is_discarded(self) -> None:
        """A window begins away from where it will settle, and counting the
        approach biases the histogram towards where the run started."""
        import inspect

        from fastmdxplora.simulation import umbrella

        source = inspect.getsource(umbrella.collect_samples)
        assert "equilibration_fraction" in source
        # Flattened, because the sentence wraps and a substring check
        # across a line break tests the layout rather than the meaning.
        prose = " ".join(inspect.getdoc(umbrella.collect_samples).split())
        assert "biases the histogram" in prose

    def test_a_missing_window_stops_it(self, tmp_path) -> None:
        """The windows either side of a missing one have nothing between them
        to stitch through."""
        from fastmdxplora.simulation.umbrella import collect_samples, plan_windows

        plan = plan_windows({
            "collective_variable": "distance", "from": -1.0, "to": 1.0,
            "n_windows": 5, "force_constant": 200.0})
        self._written(tmp_path, plan, n=500)
        import shutil
        shutil.rmtree(tmp_path / "window_02")

        directories = {w.index: tmp_path / f"window_{w.index:02d}"
                       for w in plan.windows}
        with pytest.raises(FileNotFoundError, match="window 2"):
            collect_samples(directories)


class TestOneWindowInTheRunner:
    def test_the_setting_is_declared(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["simulation"].get("umbrella")
        assert field is not None
        assert "overlap" in field.help

    def test_it_reaches_the_command_line(self) -> None:
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["simulate"]
        assert "umbrella" in {dest for _flag, dest, _kw in table}

    def test_the_pipeline_passes_it(self) -> None:
        import inspect

        from fastmdxplora.simulation import pipeline

        assert "umbrella=params.get" in inspect.getsource(pipeline)

    def test_only_one_way_of_moving_a_coordinate_at_a_time(self) -> None:
        """Steering, metadynamics and an umbrella restraint would add their
        forces."""
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner.run_simulation)
        assert "not more than one" in source or "not more than one" in source.replace("\n", " ")

    def test_a_window_needs_a_position(self) -> None:
        import inspect

        from fastmdxplora.simulation import runner

        assert "needs a `centre`" in inspect.getsource(runner.run_simulation)


class TestItIsActuallyReached:
    """The expansion was written, tested and called by nothing -- the second
    time in a row a piece of this feature worked and was unreachable.

    A config file is the one place every route into the software passes
    through, so expanding there means the command line, Python and the
    browser all get it without each remembering to ask.
    """

    def test_a_config_file_expands_into_windows(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file

        path = tmp_path / "pmf.yml"
        path.write_text(
            "output: runs/pmf\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  duration_ns: 5\n"
            "  umbrella:\n"
            "    collective_variable: ligand_distance\n"
            '    site_selection: "resid 84 to 121 and name CA"\n'
            "    from: 0.4\n"
            "    to: 2.0\n"
            "    n_windows: 9\n"
            "    force_constant: 1000\n",
            encoding="utf-8")

        config = load_config_file(path)
        assert len(config["systems"]) == 9
        assert config["systems"][0]["id"] == "window_00"
        assert config["simulation"]["duration_ns"] == 5

    def test_a_bad_umbrella_block_reads_as_a_config_error(self, tmp_path) -> None:
        """Rather than a ValueError from somewhere further in."""
        from fastmdxplora.config.loader import ConfigError, load_config_file

        path = tmp_path / "bad.yml"
        path.write_text(
            "output: runs/x\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: distance\n"
            "    from: 0.0\n"
            "    to: 1.0\n"
            "    n_windows: 5\n",
            encoding="utf-8")

        with pytest.raises(ConfigError, match="force_constant"):
            load_config_file(path)

    def test_every_public_piece_of_this_module_is_called(self) -> None:
        """Twice now something here worked and nothing reached it. Checked by
        searching the package rather than by remembering."""
        from pathlib import Path

        from fastmdxplora.simulation import umbrella

        package = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in package.rglob("*.py")
            if path.name != "umbrella.py")

        # Entry points a user's run should pass through.
        for name in ("expand_umbrella", "collect_samples", "compute_pmf"):
            assert name in umbrella.__all__, f"{name} is not public"
            assert name in sources, (
                f"{name} is exported and nothing in the package calls it"
            )


class TestNothingIsSmuggledThroughTheConfig:
    """The expansion stashed the plan under `_umbrella_plan`, and validation
    refused the unknown key -- correctly, since a config is what somebody
    wrote and not a place to hide state.

    Each window carries its own block, so the set is rebuilt from the runs.
    """

    @staticmethod
    def _written(tmp_path):
        path = tmp_path / "pmf.yml"
        path.write_text(
            "output: pmftest\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: ligand_distance\n"
            '    site_selection: "resid 84 to 121 and name CA"\n'
            "    from: 0.3\n"
            "    to: 1.5\n"
            "    n_windows: 7\n"
            "    force_constant: 1000\n",
            encoding="utf-8")
        return path

    def test_an_expanded_config_validates(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file, validate_config

        config = load_config_file(self._written(tmp_path))
        validate_config(config, require_systems=True)   # raises if not

    def test_no_private_key_is_added(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file

        config = load_config_file(self._written(tmp_path))
        assert not [k for k in config if k.startswith("_")], (
            "the config should hold nothing but what a user could have written"
        )

    def test_the_plan_is_rebuilt_from_the_windows(self, tmp_path) -> None:
        from fastmdxplora.config.loader import load_config_file
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        plan = plan_from_expanded(load_config_file(self._written(tmp_path)))
        assert plan is not None
        assert len(plan.windows) == 7
        assert plan.windows[0].centre == pytest.approx(0.3)
        assert plan.windows[-1].centre == pytest.approx(1.5)
        assert plan.collective_variable == "ligand_distance"

    def test_a_plain_study_rebuilds_nothing(self) -> None:
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        assert plan_from_expanded(
            {"systems": [{"system": "1UBQ"}, {"system": "181L"}]}) is None

    def test_the_explorer_rebuilds_rather_than_reads_a_stash(self) -> None:
        import inspect

        from fastmdxplora.batch import explorer

        source = inspect.getsource(explorer.BatchExplorer._maybe_build_pmf)
        assert "plan_from_expanded" in source
        assert "_umbrella_plan" not in source


class TestAWindowInheritsTheStudysSettings:
    """A per-system block replaces the top-level one rather than merging, so a
    block holding only the umbrella settings discarded the step counts. A run
    asking for five thousand production steps ran a million.
    """

    @staticmethod
    def _expanded():
        from fastmdxplora.simulation.umbrella import expand_umbrella

        return expand_umbrella({
            "output": "x",
            "systems": [{"system": "181L"}],
            "simulation": {
                "nvt_steps": 500, "npt_steps": 500, "production_steps": 5000,
                "timestep_fs": 1.0,
                "umbrella": {
                    "collective_variable": "ligand_distance",
                    "site_selection": "resid 84 to 121 and name CA",
                    "from": 0.3, "to": 1.5, "n_windows": 7,
                    "force_constant": 1000}},
        })

    def test_the_step_counts_reach_every_window(self) -> None:
        for entry in self._expanded()["systems"]:
            simulation = entry["simulation"]
            assert simulation["production_steps"] == 5000
            assert simulation["nvt_steps"] == 500
            assert simulation["timestep_fs"] == 1.0

    def test_and_each_still_holds_its_own_position(self) -> None:
        centres = [e["simulation"]["umbrella"]["centre"]
                   for e in self._expanded()["systems"]]
        assert len(set(centres)) == 7


class TestFindingTheLigand:
    """A ligand variable in an umbrella window refused for want of something
    the run knew: the runner never received the residue name, and neither did
    the metadynamics path -- it had simply never been run with one.
    """

    @staticmethod
    def _system(extra):
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        for index in range(10):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            for name in ("N", "CA", "C"):
                top.add_atom(name, md.element.carbon, residue)
        for name in extra:
            other = top.add_chain()
            residue = top.add_residue(name, other, resSeq=900)
            for index in range(6):
                top.add_atom(f"C{index}", md.element.carbon, residue)
        waters = top.add_chain()
        for index in range(3):
            residue = top.add_residue("HOH", waters, resSeq=1000 + index)
            top.add_atom("O", md.element.oxygen, residue)
        return top

    def test_one_candidate_is_the_ligand(self) -> None:
        from fastmdxplora.simulation.metadynamics import detect_ligand

        assert detect_ligand(self._system(["BNZ"])) == "BNZ"

    def test_no_candidate_is_not_a_ligand(self) -> None:
        from fastmdxplora.simulation.metadynamics import detect_ligand

        assert detect_ligand(self._system([])) is None

    def test_two_candidates_is_a_question_the_topology_cannot_answer(self) -> None:
        from fastmdxplora.simulation.metadynamics import detect_ligand

        assert detect_ligand(self._system(["BNZ", "LIG"])) is None

    def test_the_refusal_says_to_name_it(self) -> None:
        from fastmdxplora.simulation.metadynamics import plan_from_config

        with pytest.raises(ValueError, match="ligand_resname"):
            plan_from_config({"collective_variable": "ligand_distance",
                              "site_selection": "name CA", "sigma": 0.05,
                              "unbounded": True},
                             self._system(["BNZ", "LIG"]))

    def test_lipids_are_not_mistaken_for_a_ligand(self) -> None:
        from fastmdxplora.simulation.metadynamics import detect_ligand

        assert detect_ligand(self._system(["POP"])) is None


class TestAFailedWindowStopsTheStudy:
    """Umbrella windows are one measurement, not several systems.

    A campaign should carry on when one system fails -- the others are still
    results. A free energy cannot be computed at all if a window is missing,
    so continuing spends hours producing runs that get thrown away.
    """

    @staticmethod
    def _umbrella():
        from fastmdxplora.simulation.umbrella import expand_umbrella

        return expand_umbrella({
            "output": "/tmp/u", "systems": [{"system": "181L"}],
            "simulation": {"umbrella": {
                "collective_variable": "ligand_distance",
                "site_selection": "name CA", "from": 0.3, "to": 1.5,
                "n_windows": 5, "force_constant": 1000}}})

    def test_an_umbrella_study_stops_on_the_first_failure(self, tmp_path) -> None:
        from fastmdxplora.batch.explorer import BatchExplorer

        explorer = BatchExplorer(config_data=self._umbrella(),
                                 output_dir=str(tmp_path))
        assert explorer.continue_on_error is False

    def test_an_ordinary_campaign_carries_on(self, tmp_path) -> None:
        from fastmdxplora.batch.explorer import BatchExplorer

        explorer = BatchExplorer(
            config_data={"output": "x",
                         "systems": [{"system": "1UBQ"}, {"system": "181L"}]},
            output_dir=str(tmp_path))
        assert explorer.continue_on_error is True

    def test_it_can_still_be_asked_for_explicitly(self, tmp_path) -> None:
        """Somebody who wants the surviving windows anyway should get them."""
        from fastmdxplora.batch.explorer import BatchExplorer

        explorer = BatchExplorer(config_data=self._umbrella(),
                                 output_dir=str(tmp_path),
                                 continue_on_error=True)
        assert explorer.continue_on_error is True

    def test_the_message_says_why_the_rest_were_not_run(self) -> None:
        """The difference between a reader thinking something crashed and
        knowing the rest was not attempted on purpose."""
        from fastmdxplora.batch.explorer import _not_submitted

        umbrella = _not_submitted("window_02", umbrella=True)
        assert "a free energy needs every window" in umbrella
        assert "thrown away" in umbrella

        campaign = _not_submitted("run_02", umbrella=False)
        assert "continue_on_error=False" in campaign

    def test_the_expanded_config_is_still_json(self) -> None:
        """The window centres came out of numpy, and a numpy scalar
        serialises as an opaque object in YAML and not at all in JSON -- so a
        config written back out would not reload."""
        import json

        json.dumps(self._umbrella())
        for entry in self._umbrella()["systems"]:
            centre = entry["simulation"]["umbrella"]["centre"]
            assert type(centre) is float


class TestItLooksWhereTheRunsActuallyWent:
    """The first version guessed the path -- `<output>/window_00/simulation/
    COLVAR` -- and the runs go to `<output>/runs/window-00/simulation/COLVAR`:
    under a `runs` directory, with the identifier slugged.

    Two mistakes in one path, neither visible until a real study finished and
    the recombination found nothing. The caller knows where it put things.
    """

    @staticmethod
    def _explorer(tmp_path):
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "c.yml"
        config.write_text(
            "output: out\n"
            "include: [setup, simulation]\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: distance\n"
            '    selection_a: "name CA"\n'
            '    selection_b: "name CB"\n'
            "    from: -1.6\n"
            "    to: 1.6\n"
            "    n_windows: 17\n"
            "    force_constant: 200\n",
            encoding="utf-8")
        return BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

    def test_a_free_energy_comes_out_of_a_real_layout(self, tmp_path) -> None:
        import json

        explorer = self._explorer(tmp_path)
        for spec in explorer.run_specs:
            block = spec.options["simulation"]["umbrella"]
            simulation = explorer._run_output_dir(spec) / "simulation"
            simulation.mkdir(parents=True)
            values = _sample(block["centre"], block["force_constant"],
                             n=3000, seed=block["index"])
            simulation.joinpath("COLVAR").write_text(
                "#! FIELDS time cv bias\n"
                + "\n".join(f"{i * 0.1:.1f} {v:.6f} 0.0"
                            for i, v in enumerate(values)),
                encoding="utf-8")

        explorer._maybe_build_pmf()

        written = json.loads(
            (tmp_path / "out" / "pmf.json").read_text(encoding="utf-8"))
        assert written["refused"] is None
        x = np.array(written["pmf"]["coordinate"])
        f = np.array(written["pmf"]["free_energy_kjmol"])
        assert f[np.argmin(np.abs(x))] == pytest.approx(10.0, abs=1.5)

    def test_the_directories_come_from_the_explorer(self) -> None:
        """Not from reconstructing a path, which is what got it wrong."""
        import inspect

        from fastmdxplora.batch import explorer
        from fastmdxplora.simulation import umbrella

        assert "_run_output_dir(spec)" in inspect.getsource(
            explorer.BatchExplorer._maybe_build_pmf)
        # And the collector no longer builds one.
        # The body, not the docstring -- which explains the mistake and
        # therefore contains the very string this looks for.
        source = inspect.getsource(umbrella.collect_samples)
        body = source[source.index('"""', source.index('"""') + 3):]
        assert "window_" not in body, (
            "the collector should not know how a run directory is named"
        )

    def test_a_missing_window_names_the_path_it_looked_at(self, tmp_path) -> None:
        """So the next path mistake is one line of output rather than an
        empty result."""
        from fastmdxplora.simulation.umbrella import collect_samples

        with pytest.raises(FileNotFoundError, match="COLVAR"):
            collect_samples({0: tmp_path / "nowhere"})


class TestParallelRunsShareOneTerminal:
    """Three workers printing the wordmark at once produced a screen of
    interleaved fragments -- characters from three banners arriving mixed
    together, which is not three runs starting but one unreadable page.

    Each run writes its own log file. The terminal belongs to the study.
    """

    def test_a_worker_prints_no_banner(self) -> None:
        import io
        import os

        from fastmdxplora.utils.presenter import SessionPresenter

        previous = os.environ.get("FASTMDX_LOG_STYLE")
        os.environ["FASTMDX_LOG_STYLE"] = "plain"
        try:
            out = io.StringIO()
            SessionPresenter(stream=out).banner(
                System="181L", Output="x", Version="2.3.0")
            assert out.getvalue() == ""
        finally:
            if previous is None:
                os.environ.pop("FASTMDX_LOG_STYLE", None)
            else:
                os.environ["FASTMDX_LOG_STYLE"] = previous

    def test_an_ordinary_run_still_prints_one(self) -> None:
        import io
        import os

        from fastmdxplora.utils.presenter import SessionPresenter

        previous = os.environ.pop("FASTMDX_LOG_STYLE", None)
        try:
            out = io.StringIO()
            SessionPresenter(stream=out).banner(
                System="181L", Output="x", Version="2.3.0")
            assert "181L" in out.getvalue()
        finally:
            if previous is not None:
                os.environ["FASTMDX_LOG_STYLE"] = previous

    def test_the_worker_asks_for_it(self) -> None:
        import inspect

        from fastmdxplora.batch import explorer

        source = inspect.getsource(explorer._execute_run)
        assert "FASTMDX_LOG_STYLE" in source
        assert "share one terminal" in source

    @staticmethod
    def _guard():
        """The guard, lifted out of the worker and made runnable here."""
        import inspect
        import textwrap

        from fastmdxplora.batch import explorer

        source = inspect.getsource(explorer._execute_run)
        block = source[source.index("    class _QuietBanner:"):
                       source.index("    # Imported here")]
        namespace: dict = {}
        exec("import os as _os\nimport sys as _sys\n" + textwrap.dedent(block),
             namespace)
        return namespace["_QuietBanner"]

    def test_plumeds_own_output_goes_to_the_run_log(self) -> None:
        """PLUMED prints its banner from C++, straight to the file
        descriptor, so silencing ours left three copies of its own
        interleaved in the terminal. Python-level redirection does not reach
        a C write -- the descriptor has to move, which is the same lesson a
        Markdown renderer taught by printing its installation guide from a
        dynamic loader.

        Written through ``os.write``, which addresses the descriptor and not
        ``sys.stdout``, so it is the same path a C write takes and it is one
        every platform has.
        """
        import os
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "run", "run.log")
            with self._guard()(log):
                os.write(1, b"PLUMED: pretending to start\n")
            assert "PLUMED" in pathlib.Path(log).read_text(encoding="utf-8")

    def test_a_c_write_lands_there_too(self) -> None:
        """The descriptor is the thing shared with a C library, so a real one
        is used where the platform has a C runtime ``ctypes`` can reach.

        In a subprocess, and with ``PYTHONUNBUFFERED`` cleared, because that
        variable makes CPython set the C runtime's stdout unbuffered as well.
        With it set, every write lands immediately and this test passes
        whether or not the guard flushes -- which is to say it would pass for
        the wrong reason, and pass here while the leak it is about happened in
        CI.

        Nothing is flushed by the caller on purpose: what is still buffered
        when the descriptors go back is the guard's problem.
        """
        import os
        import subprocess
        import sys
        import textwrap

        import pytest

        from fastmdxplora.utils.native_output import _load_libc

        if _load_libc() is None:
            pytest.skip("no C runtime reachable through ctypes on this platform")

        script = textwrap.dedent("""
            import inspect, os, pathlib, sys, tempfile, textwrap
            from fastmdxplora.batch import explorer
            from fastmdxplora.utils.native_output import _load_libc

            source = inspect.getsource(explorer._execute_run)
            block = source[source.index("    class _QuietBanner:"):
                           source.index("    # Imported here")]
            namespace = {}
            exec("import os as _os\\nimport sys as _sys\\n"
                 + textwrap.dedent(block), namespace)

            directory = tempfile.mkdtemp()
            log = os.path.join(directory, "run", "run.log")
            with namespace["_QuietBanner"](log):
                _load_libc().puts(b"PLUMED: pretending to start")
            sys.stderr.write(pathlib.Path(log).read_text(encoding="utf-8"))
        """)

        environment = dict(os.environ)
        environment.pop("PYTHONUNBUFFERED", None)
        finished = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=environment, timeout=120)
        assert "PLUMED" in finished.stderr, (
            "the C runtime's buffer still held it when the descriptors went "
            f"back, so it reached the terminal instead: {finished.stderr!r}")

    def test_the_terminal_comes_back(self) -> None:
        """A worker that redirects and does not restore takes the study's
        terminal with it.

        Checked by what a later write does rather than by comparing
        descriptors, so it means the same thing on every platform.
        """
        import os
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "run", "run.log")
            with self._guard()(log):
                os.write(1, b"during\n")
            os.write(1, b"after\n")
            written = pathlib.Path(log).read_text(encoding="utf-8")
        assert "during" in written
        assert "after" not in written

    def test_and_puts_it_back(self) -> None:
        """The worker is called in-process by the tests as well as in a
        subprocess by a real run. Setting a global and not restoring it made
        seventeen unrelated tests fail."""
        import inspect

        from fastmdxplora.batch import explorer

        source = inspect.getsource(explorer._execute_run)
        assert "__exit__" in source
        # The call, whatever it now takes as an argument -- pinning the exact
        # text made this fail the moment the guard learned where to write.
        assert "with _QuietBanner(" in source


class TestHowMuchOverlapIsEnough:
    """Three per cent is enough to stitch and thin. On a real study, pairs at
    7.3% passed while a reader might reasonably want fifteen.

    It is a judgement about how much evidence a joint needs, so it belongs to
    whoever is making the claim rather than to a number in the source.
    """

    @staticmethod
    def _pair(threshold=None):
        from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

        spec = {"collective_variable": "distance", "centres": [0.0, 0.16],
                "force_constant": 1000}
        if threshold is not None:
            spec["minimum_overlap"] = threshold
        plan = plan_windows(spec)
        rng = np.random.RandomState(0)
        samples = {0: rng.normal(0.0, 0.10, 6000),
                   1: rng.normal(0.16, 0.10, 6000)}
        return compute_pmf(samples, plan, bootstrap_resamples=0)

    def test_the_same_data_passes_or_fails_by_the_threshold(self) -> None:
        assert self._pair(0.03)["refused"] is None
        assert self._pair(0.50)["refused"] is not None

    def test_the_default_is_the_permissive_one(self) -> None:
        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({"collective_variable": "distance",
                             "centres": [0.0, 1.0], "force_constant": 100})
        assert plan.minimum_overlap == 0.03

    def test_the_refusal_says_what_the_threshold_was(self) -> None:
        """So a reader can tell a genuine gap from a strict setting."""
        refusal = self._pair(0.50)["refused"]
        assert "50%" in refusal
        assert "minimum_overlap" in refusal

    def test_it_is_recorded_with_the_plan(self) -> None:
        assert self._pair(0.20)["plan"]["minimum_overlap"] == 0.20 \
            if "plan" in self._pair(0.20) else True

        from fastmdxplora.simulation.umbrella import plan_windows

        plan = plan_windows({"collective_variable": "distance",
                             "centres": [0.0, 1.0], "force_constant": 100,
                             "minimum_overlap": 0.2})
        assert plan.as_record()["minimum_overlap"] == 0.2

    def test_a_studys_threshold_survives_the_round_trip(self) -> None:
        """The recombination happens after the runs, from a rebuilt plan --
        so a threshold that did not survive expansion would silently revert
        to the default."""
        from fastmdxplora.simulation.umbrella import (
            expand_umbrella,
            plan_from_expanded,
        )

        expanded = expand_umbrella({
            "output": "x", "systems": [{"system": "181L"}],
            "simulation": {"umbrella": {
                "collective_variable": "distance",
                "selection_a": "name CA", "selection_b": "name CB",
                "from": 0.0, "to": 1.0, "n_windows": 5,
                "force_constant": 500, "minimum_overlap": 0.15}}})

        assert plan_from_expanded(expanded).minimum_overlap == 0.15


class TestOneSystemForEveryWindow:
    """Seven windows of one real study came out with 37,212, 37,254, 37,436
    and 37,445 atoms -- four different systems for one measurement.

    The windows are the same molecule held at different points along a
    coordinate, so they should be the same molecule. Solvation does not place
    water the same way twice, and water arranged differently between windows
    is noise in the free energy rather than physics. Preparing seven times
    was also seven times the work.
    """

    @staticmethod
    def _study(tmp_path, *, systems=("181L",), include="[setup, simulation]"):
        from fastmdxplora.batch.explorer import BatchExplorer

        listed = "\n".join(f"  - system: {s}" for s in systems)
        config = tmp_path / "study.yml"
        config.write_text(
            f"output: out\ninclude: {include}\nsystems:\n{listed}\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: radius_of_gyration\n"
            '    selection: "protein and name CA"\n'
            "    from: 0.4\n    to: 0.6\n    n_windows: 3\n"
            "    force_constant: 5000\n",
            encoding="utf-8")
        return BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

    @staticmethod
    def _pretend_it_is_prepared(directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("system.xml", "state.xml", "topology.pdb"):
            (directory / name).write_text("<x/>", encoding="utf-8")

    def test_a_study_prepares_once(self, tmp_path) -> None:
        study = self._study(tmp_path)
        shared = tmp_path / "out" / "shared_setup" / "setup"
        self._pretend_it_is_prepared(shared)

        assert study._maybe_prepare_once(["setup", "simulation"], None) == shared

    def test_and_every_window_simulates_from_it(self, tmp_path) -> None:
        study = self._study(tmp_path)
        shared = tmp_path / "out" / "shared_setup" / "setup"
        self._pretend_it_is_prepared(shared)
        study._maybe_prepare_once(["setup", "simulation"], None)

        pointed = {(spec.options.get("simulation") or {}).get("prepared_from")
                   for spec in study.run_specs}
        assert len(study.run_specs) == 3
        assert pointed == {str(shared)}

    def test_and_none_of_them_prepares_another(self, tmp_path) -> None:
        """The saving is the point, and a second preparation would undo the
        sharing as well.

        Asserted on what the windows are told to run, not on which list says
        so. The first version of this checked that setup had been added to
        `exclude` -- which it had, beside an `include` that also named it.
        Naming a phase in both is refused, so every window of a real study
        failed before doing anything, and the test was green throughout
        because it was pinning my choice of list rather than the outcome.
        """
        study = self._study(tmp_path)
        self._pretend_it_is_prepared(tmp_path / "out" / "shared_setup" / "setup")

        seen: dict = {}
        study._run_sequential = lambda include, exclude: seen.update(
            include=include, exclude=exclude) or []
        study.run()

        assert "setup" not in (seen["include"] or [])
        assert "simulation" in (seen["include"] or [])
        assert not (seen["include"] and seen["exclude"]), (
            "naming phases in both lists is refused, and the run fails before "
            "it starts")

    def test_a_study_that_excludes_instead_is_told_the_same_way(
        self, tmp_path
    ) -> None:
        """A config can say what not to run rather than what to run, and then
        `exclude` is the list that has to carry it."""
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "study.yml"
        config.write_text(
            "output: out\nexclude: [report]\nsystems:\n  - system: 181L\n"
            "simulation:\n  umbrella:\n    collective_variable: distance\n"
            "    centres: [0.0, 0.2]\n    force_constant: 1000\n",
            encoding="utf-8")
        study = BatchExplorer(config=config, output_dir=str(tmp_path / "out"))
        self._pretend_it_is_prepared(tmp_path / "out" / "shared_setup" / "setup")

        seen: dict = {}
        study._run_sequential = lambda include, exclude: seen.update(
            include=include, exclude=exclude) or []
        study.run()

        assert "setup" in seen["exclude"]
        assert not seen["include"]

    def test_preparing_is_not_shared_when_it_is_the_whole_study(
        self, tmp_path
    ) -> None:
        """Doing it once would leave every window with nothing to run."""
        study = self._study(tmp_path, include="[setup]")
        assert study._maybe_prepare_once(["setup"], None) is None

    def test_an_ordinary_campaign_prepares_per_system(self, tmp_path) -> None:
        """Different systems *are* different systems. Sharing one preparation
        between them would be wrong rather than efficient."""
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "campaign.yml"
        config.write_text(
            "output: out\nsystems:\n  - system: 1UBQ\n  - system: 181L\n",
            encoding="utf-8")
        campaign = BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

        assert campaign._maybe_prepare_once(None, None) is None
        for spec in campaign.run_specs:
            assert "prepared_from" not in (spec.options.get("simulation") or {})

    def test_several_molecules_never_reach_here(self, tmp_path) -> None:
        """Expansion refuses them: a window set for each would be several free
        energies. So sharing does not need a guard against it, and one that
        looked at the molecule would be a branch nothing could reach."""
        import pytest

        from fastmdxplora.config import ConfigError

        with pytest.raises(ConfigError, match="one system at many positions"):
            self._study(tmp_path, systems=("181L", "1UBQ"))

    def test_but_windows_prepared_differently_do_not_share(self, tmp_path) -> None:
        """A sweep can still ask for the windows to be prepared differently.
        Sharing would quietly ignore what it asked for, which is worse than
        preparing seven times."""
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "swept.yml"
        config.write_text(
            "output: out\ninclude: [setup, simulation]\n"
            "systems:\n  - system: 181L\n"
            "sweep:\n  setup.ph: [6.5, 7.4]\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: distance\n"
            "    centres: [0.0, 0.2]\n    force_constant: 1000\n",
            encoding="utf-8")
        study = BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

        assert len({spec.options["setup"]["ph"] for spec in study.run_specs}) == 2
        assert study._maybe_prepare_once(["setup", "simulation"], None) is None

    def test_nothing_is_prepared_when_nothing_was_going_to_be(self, tmp_path) -> None:
        """The windows are simulating from a system that already exists."""
        study = self._study(tmp_path, include="[simulation]")
        assert study._maybe_prepare_once(["simulation"], None) is None

    def test_a_preparation_that_fails_stops_the_study(self, tmp_path) -> None:
        """Every window would fail the same way, one after another, for hours.
        It is said once, before any of them starts.

        The failure is a path that cannot name a structure, so it fails for
        the same reason on every machine. Written first as a study of 181L
        that simply had nothing on disk, which failed only where the fetch
        did: on a machine with a network it downloaded the protein and
        solvated 36,957 atoms, and the test passed nothing but the sandbox's
        firewall.
        """
        import pytest

        study = self._study(tmp_path, systems=(str(tmp_path / "no-such.pdb"),))
        with pytest.raises(RuntimeError, match="nothing for the windows"):
            study._maybe_prepare_once(["setup", "simulation"], None)

    def test_an_existing_preparation_is_not_repeated(self, tmp_path) -> None:
        """A study rerun after a window failed should not solvate again --
        that would replace the system its finished windows already used."""
        import inspect

        from fastmdxplora.batch import explorer

        study = self._study(tmp_path)
        shared = tmp_path / "out" / "shared_setup" / "setup"
        self._pretend_it_is_prepared(shared)
        marker = shared / "system.xml"
        stamp = marker.stat().st_mtime_ns

        study._maybe_prepare_once(["setup", "simulation"], None)
        assert marker.stat().st_mtime_ns == stamp
        assert "_a_prepared_system_is_there" in inspect.getsource(
            explorer.BatchExplorer._maybe_prepare_once)

    def test_the_check_reads_the_first_element_not_the_tuple(self, tmp_path) -> None:
        """The simulation phase answers with a tuple of paths, and an empty
        answer is a tuple of Nones -- which is truthy."""
        from fastmdxplora.batch.explorer import _a_prepared_system_is_there

        assert _a_prepared_system_is_there(tmp_path) is False
        self._pretend_it_is_prepared(tmp_path / "setup")
        assert _a_prepared_system_is_there(tmp_path / "setup") is True


class TestAWindowThatIsNotWhereItWasHeld:
    """A window is a restraint at a position, and its histogram is supposed to
    sit around that position.

    On a real study of benzene leaving the T4 lysozyme cavity, four windows
    held at 0.3, 0.5, 0.7 and 0.9 sampled around 0.11, 0.16, 0.22 and 0.49 --
    they never left the bound state -- and two more held at 1.1 and 1.3 both
    settled at 1.19, on top of each other. The refusal reported the gap that
    left and advised a softer force constant, which is the remedy for windows
    that do not reach each other and the opposite of what a window that has
    already slipped needs.
    """

    @staticmethod
    def _plan(n=7, first=0.3, last=1.5):
        from fastmdxplora.simulation.umbrella import plan_windows

        return plan_windows({"collective_variable": "distance", "from": first,
                             "to": last, "n_windows": n,
                             "force_constant": 5000})

    @staticmethod
    def _sampled(plan, at, spread=0.05, n=400):
        rng = np.random.RandomState(0)
        return {w.index: rng.normal(at[w.index], spread, n)
                for w in plan.windows}

    def test_a_window_sitting_on_its_centre_has_not_drifted(self) -> None:
        from fastmdxplora.simulation.umbrella import windows_that_drifted

        plan = self._plan()
        on_target = {w.index: w.centre for w in plan.windows}
        assert windows_that_drifted(self._sampled(plan, on_target), plan) == []

    def test_one_that_slipped_onto_its_neighbour_is_named(self) -> None:
        from fastmdxplora.simulation.umbrella import windows_that_drifted

        plan = self._plan()
        where = {w.index: w.centre for w in plan.windows}
        where[5] = 1.19          # held at 1.3, sat on window 4's ground
        drifted = windows_that_drifted(self._sampled(plan, where), plan)

        assert [d["window"] for d in drifted] == [5]
        assert drifted[0]["centre"] == plan.windows[5].centre
        assert abs(drifted[0]["sampled_at"] - 1.19) < 0.02

    def test_the_measure_is_the_spacing_the_plan_intended(self) -> None:
        """Half the distance to the next window: further than that is sampling
        where another window was supposed to be. Not an absolute number, which
        would mean something different on every coordinate."""
        from fastmdxplora.simulation.umbrella import windows_that_drifted

        wide = self._plan(n=3, first=0.0, last=4.0)     # 2.0 apart
        where = {w.index: w.centre for w in wide.windows}
        where[1] += 0.6                                  # well inside half of 2.0
        assert windows_that_drifted(self._sampled(wide, where), wide) == []

        tight = self._plan(n=3, first=0.0, last=0.4)     # 0.2 apart
        where = {w.index: w.centre for w in tight.windows}
        where[1] += 0.6                                  # far outside half of 0.2
        assert [d["window"] for d in
                windows_that_drifted(self._sampled(tight, where, 0.01), tight)] == [1]

    def test_the_refusal_says_the_windows_moved_not_that_there_are_too_few(
        self
    ) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        where = {0: 0.114, 1: 0.156, 2: 0.219, 3: 0.485,
                 4: 1.190, 5: 1.197, 6: 1.500}
        result = compute_pmf(self._sampled(plan, where, 0.03), plan, bootstrap_resamples=0)

        assert result["pmf"] is None
        assert "did not sample where they were held" in result["refused"]
        assert "window 5 was held at 1.3" in result["refused"]

    def test_and_does_not_advise_the_remedy_that_makes_it_worse(self) -> None:
        """A softer force constant lets a window that has already slipped slip
        further."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        where = {0: 0.114, 1: 0.156, 2: 0.219, 3: 0.485,
                 4: 1.190, 5: 1.197, 6: 1.500}
        refused = compute_pmf(self._sampled(plan, where, 0.03), plan, bootstrap_resamples=0)["refused"]

        assert "larger `force_constant`" in refused
        assert "A softer one will make this worse" in refused
        assert "or a softer force constant so each wanders further" not in refused
        # And it names one, rather than leaving the reader to size it.
        assert "needs k" in refused
        assert "at spacing" in refused

    def test_a_genuine_gap_still_gets_the_advice_for_a_genuine_gap(self) -> None:
        """Windows on their centres that simply do not reach each other want
        more windows or a softer spring, and should still be told so."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan(n=3, first=0.0, last=4.0)
        on_target = {w.index: w.centre for w in plan.windows}
        refused = compute_pmf(self._sampled(plan, on_target, 0.05), plan, bootstrap_resamples=0)["refused"]

        assert "did not sample where they were held" not in refused
        assert "softer force constant so each wanders further" in refused

    def test_the_finding_is_recorded_not_only_described(self) -> None:
        """So a study can be read from its files rather than from a sentence."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        where = {w.index: w.centre for w in plan.windows}
        where[5] = 1.19
        result = compute_pmf(self._sampled(plan, where, 0.03), plan, bootstrap_resamples=0)

        assert [d["window"] for d in result["drifted"]] == [5]


class TestARunTooShortToJudge:
    """The study that found the drift recorded 41 values per window.

    An overlap is the area two histograms share, and a histogram from tens of
    points is mostly noise -- so a run short enough to be a smoke test
    produces overlaps, and gaps, that are arithmetic rather than evidence.
    Reporting a specific pair as sharing 0.0% is a precise claim resting on
    nothing, and a free energy stitched from those histograms would carry the
    noise as structure and look exactly like a result.
    """

    @staticmethod
    def _plan(**spec):
        from fastmdxplora.simulation.umbrella import plan_windows

        return plan_windows({"collective_variable": "distance", "from": 0.3,
                             "to": 1.5, "n_windows": 7,
                             "force_constant": 5000, **spec})

    @staticmethod
    def _sampled(plan, n, at=None):
        rng = np.random.RandomState(0)
        return {w.index: rng.normal((at or {}).get(w.index, w.centre), 0.05, n)
                for w in plan.windows}

    def test_a_thin_run_is_refused_before_a_gap_is_claimed(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        result = compute_pmf(self._sampled(plan, 41), plan, bootstrap_resamples=0)

        assert result["pmf"] is None
        assert "recorded fewer than 200 values" in result["refused"]
        assert "the thinnest has 41" in result["refused"]

    def test_and_the_overlaps_are_still_reported_as_observations(self) -> None:
        """Nothing is hidden -- what changes is what may be concluded."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        result = compute_pmf(self._sampled(plan, 41), plan, bootstrap_resamples=0)
        assert len(result["overlaps"]) == 6
        assert [t["window"] for t in result["thin"]] == list(range(7))

    def test_a_short_run_cannot_be_told_from_a_soft_restraint(self) -> None:
        """Both put a window away from its centre. Saying which would be a
        guess, so the refusal says it cannot tell and what to do first."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        where = {0: 0.114, 1: 0.156, 2: 0.219, 3: 0.485,
                 4: 1.190, 5: 1.197, 6: 1.500}
        refused = compute_pmf(self._sampled(plan, 41, where), plan, bootstrap_resamples=0)["refused"]

        assert "not at their centres" in refused
        assert "cannot be told apart" in refused
        assert "Sample for longer first" in refused
        # Not the confident diagnosis, which needs a run long enough to earn it.
        assert "larger `force_constant`" not in refused

    def test_a_long_enough_run_gets_the_confident_diagnosis(self) -> None:
        """With the histograms filled, a window off its centre is a statement
        about the restraint rather than about the clock."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan()
        where = {0: 0.114, 1: 0.156, 2: 0.219, 3: 0.485,
                 4: 1.190, 5: 1.197, 6: 1.500}
        refused = compute_pmf(self._sampled(plan, 4000, where), plan, bootstrap_resamples=0)["refused"]

        assert "larger `force_constant`" in refused
        assert "needs k" in refused
        assert "Sample for longer first" not in refused

    def test_the_threshold_belongs_to_the_study(self) -> None:
        """A smoke test that wants to reach the recombination can lower it,
        the way a study can raise the overlap it demands."""
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = self._plan(minimum_samples=20)
        assert plan.minimum_samples == 20
        result = compute_pmf(self._sampled(plan, 41), plan, bootstrap_resamples=0)
        assert "recorded fewer than" not in (result["refused"] or "")

    def test_it_survives_expansion_like_the_overlap_does(self) -> None:
        """The recombination happens after the runs, from a rebuilt plan, so a
        threshold that did not survive would silently revert to the default."""
        from fastmdxplora.simulation.umbrella import (
            expand_umbrella,
            plan_from_expanded,
        )

        expanded = expand_umbrella({
            "systems": [{"system": "181L"}],
            "simulation": {"umbrella": {
                "collective_variable": "distance", "from": 0.0, "to": 0.4,
                "n_windows": 3, "force_constant": 1000,
                "minimum_samples": 25}},
        })
        assert plan_from_expanded(expanded).minimum_samples == 25

    def test_the_record_carries_it(self) -> None:
        assert self._plan(minimum_samples=25).as_record()["minimum_samples"] == 25


class TestASettingThatDoesNotExist:
    """Nothing checked inside the umbrella block.

    `minimum_ovelap: 0.15` -- one letter -- was accepted, ignored, and the
    study stitched at the three per cent default while its author believed it
    had demanded fifteen. The run completes and produces a curve, and the
    guard that was supposed to stand behind that curve never existed.

    Every refusal this module makes can be switched off by a typo, so the typo
    is what has to be caught.
    """

    @staticmethod
    def _block(**extra):
        return {"collective_variable": "distance", "from": 0.0, "to": 0.4,
                "n_windows": 3, "force_constant": 1000, **extra}

    def test_a_misspelled_setting_is_refused_with_the_spelling(self) -> None:
        import pytest

        from fastmdxplora.config import ConfigError
        from fastmdxplora.simulation.umbrella import plan_windows

        with pytest.raises(ConfigError, match="minimum_overlap"):
            plan_windows(self._block(minimum_ovelap=0.15))

    def test_a_case_mismatch_too(self) -> None:
        """One of the commonest config typos, and short keys fall below
        difflib's ratio when only the case differs."""
        import pytest

        from fastmdxplora.config import ConfigError
        from fastmdxplora.simulation.umbrella import plan_windows

        with pytest.raises(ConfigError, match="force_constant"):
            plan_windows(self._block(Force_Constant=1000))

    def test_the_settings_that_exist_are_accepted(self) -> None:
        from fastmdxplora.simulation.umbrella import _accepted_keys, plan_windows

        # `equilibration_steps` used to be in this list. It was accepted,
        # validated, written into pmf.json and read by nothing, so a study
        # asking for it discarded only the default fraction while its record
        # said otherwise -- the same failure a typo produces, which this very
        # class exists to catch. It is refused by name now, pointing at
        # `equilibration_fraction`, which is what the discard is measured in.
        plan = plan_windows(self._block(
            minimum_overlap=0.15, minimum_samples=25,
            equilibration_fraction=0.25,
            selection="protein and name CA"))
        assert plan.minimum_overlap == 0.15
        assert plan.minimum_samples == 25
        assert plan.equilibration_fraction == 0.25
        assert {"minimum_overlap", "minimum_samples",
                "equilibration_fraction"} <= _accepted_keys()
        assert "equilibration_steps" not in _accepted_keys()

    def test_the_check_runs_when_a_config_is_only_validated(self) -> None:
        """Which is the route the browser's Check it button takes, and
        BatchExplorer given a config in memory -- neither expands the block,
        so neither reached the reader that does the checking."""
        import pytest

        from fastmdxplora.config import ConfigError, validate_config

        with pytest.raises(ConfigError, match="minimum_overlap"):
            validate_config(
                {"output": "o", "systems": [{"system": "181L"}],
                 "simulation": {"umbrella": self._block(minimum_ovelap=0.15)}},
                require_systems=True)

    def test_and_when_a_file_is_loaded(self, tmp_path) -> None:
        from fastmdxplora.config import ConfigError, load_config_file

        config = tmp_path / "study.yml"
        config.write_text(
            "output: o\nsystems:\n  - system: 181L\n"
            "simulation:\n  umbrella:\n    collective_variable: distance\n"
            "    from: 0.0\n    to: 0.4\n    n_windows: 3\n"
            "    force_constant: 1000\n    n_window: 7\n", encoding="utf-8")
        import pytest

        with pytest.raises(ConfigError, match="n_windows"):
            load_config_file(str(config))

    def test_the_expansion_still_reads_what_it_writes(self, tmp_path) -> None:
        """The block is rewritten onto each window with keys of its own, and a
        check that refused those would refuse every expanded study."""
        from fastmdxplora.simulation.umbrella import (
            expand_umbrella,
            plan_from_expanded,
        )

        expanded = expand_umbrella({
            "systems": [{"system": "181L"}],
            "simulation": {"umbrella": self._block(minimum_overlap=0.2)},
        })
        assert plan_from_expanded(expanded).minimum_overlap == 0.2

    def test_every_variable_can_be_named_the_way_it_is_documented(self) -> None:
        """The dangerous direction is refusing a real setting, and the first
        version of this check did: `site_selection` and the paired
        `selection_a`/`selection_b` belong to the shared collective-variable
        layer, not to umbrella, and a list written from umbrella's own reader
        did not have them. Three tests failed; a study would have been
        refused at the door.
        """
        from fastmdxplora.simulation.umbrella import check_umbrella_keys

        for real in ("selection", "site_selection", "bilayer_selection",
                     "axis_selection", "ligand_resname", "switch_distance_nm",
                     "selection_a", "selection_b"):
            check_umbrella_keys(self._block(**{real: "x"}))

    def test_the_variable_settings_come_from_the_layer_that_owns_them(
        self
    ) -> None:
        """Copied, they would go stale the next time a variable is added."""
        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLE_KEYS
        from fastmdxplora.simulation.umbrella import _accepted_keys

        assert COLLECTIVE_VARIABLE_KEYS <= _accepted_keys()

    def test_a_setting_added_without_being_declared_is_caught(self) -> None:
        """The list is declared beside the reader so the two cannot drift: a
        setting read but not listed would be refused the moment anyone used
        it, which is a loud failure in a test rather than a quiet one in a
        study."""
        import inspect

        from fastmdxplora.simulation import umbrella

        source = inspect.getsource(umbrella.plan_windows)
        read = set(re.findall(r'spec\.get\("([a-z_]+)"', source))
        read |= set(re.findall(r'spec\["([a-z_]+)"\]', source))
        read |= set(re.findall(r'"([a-z_]+)" in spec', source))
        assert read <= umbrella._accepted_keys(), read - umbrella._accepted_keys()


class TestAnUnsampledBinIsNotAMeasurement:
    """The floor that stops `log(0)` inside the WHAM iteration was carried
    into the output. A bin nobody sampled left as -kT*log(1e-300) -- 1724
    kJ/mol at 300 K, about seven hundred times RT -- sitting in the array
    between neighbours of eleven and thirteen, indistinguishable from a
    measurement. The tripeptide smoke run had six of them among sixty."""

    def _samples(self, gap: bool):
        """Five windows across a coordinate, with or without a hole."""
        import numpy as np

        rng = np.random.default_rng(0)
        centres = [0.6, 0.7, 0.8, 0.9, 1.0]
        samples = {}
        for index, centre in enumerate(centres):
            if gap and index == 2:
                # This window sampled somewhere else entirely, so the bins
                # around its centre stay empty.
                draw = rng.normal(0.62, 0.01, 400)
            else:
                draw = rng.normal(centre, 0.03, 400)
            samples[index] = np.asarray(draw)
        return centres, samples

    def _pmf(self, gap: bool):
        from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

        centres, samples = self._samples(gap)
        plan = plan_windows({
            "collective_variable": "distance",
            "selection_a": "resid 0 and name CA",
            "selection_b": "resid 2 and name CA",
            "centres": centres,
            "force_constant": 2000.0,
        })
        return compute_pmf(
            samples, plan, temperature_K=300.0, minimum_overlap=0.0,
            bootstrap_resamples=0)

    def test_a_sampled_bin_reports_a_number(self) -> None:
        result = self._pmf(gap=False)
        values = result["pmf"]["free_energy_kjmol"]
        assert any(v is not None for v in values)
        assert all(v is None or v < 500 for v in values), (
            "a free energy of hundreds of kJ/mol on a smooth coordinate is a "
            "clip leaking into the output, not a barrier"
        )

    def test_an_unsampled_bin_reports_nothing(self) -> None:
        """`null`, not a sentinel: the coordinate exists and the free energy
        there is unknown, which JSON says exactly."""
        result = self._pmf(gap=True)
        values = result["pmf"]["free_energy_kjmol"]
        assert None in values
        assert all(v is None or v < 500 for v in values)

    def test_the_count_is_reported(self) -> None:
        result = self._pmf(gap=True)
        assert result["unsampled_bins"] == sum(
            1 for v in result["pmf"]["free_energy_kjmol"] if v is None)

    def test_the_minimum_is_taken_over_what_was_sampled(self) -> None:
        result = self._pmf(gap=True)
        values = [v for v in result["pmf"]["free_energy_kjmol"] if v is not None]
        assert min(values) == pytest.approx(0.0, abs=1e-9)
