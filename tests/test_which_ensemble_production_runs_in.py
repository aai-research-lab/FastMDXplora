"""Two questions that shared one number.

`npt_steps` used to answer both "how long to equilibrate at constant
pressure" and "is there a barostat at all", because the runner gated the
barostat on `npt_steps > 0`. That made one combination impossible to ask
for:

    equilibrate at constant pressure, then produce at constant volume

which is the right way to run NVT production. The density has to be
learned from a barostat before the box is fixed at it — this package's own
explain text says so, and calls fixing it at whatever solvation produced
"the one option nobody intends". With one setting doing both jobs, the
option it recommends was unsayable.

It also made segmentation wrong. A resumed segment wants no equilibration
and the barostat the study runs under; zeroing `npt_steps` to get the
first silently removed the second, so a joined trajectory held NPT and NVT
stretches with nothing able to tell. Found on ubiquitin during a
rehearsal, by reading a warning that fired on segments 1 to 3 and not on
segment 0.

Nothing already written changes meaning: with `ensemble` absent the answer
is inferred exactly as the runner used to decide it.
"""

from __future__ import annotations

import unittest

from fastmdxplora.simulation.ensembles import (
    ENSEMBLES,
    NPT,
    NVT,
    describe_choice,
    resolve_ensemble,
)


class TestNothingAlreadyWrittenChangesMeaning(unittest.TestCase):
    """The inference must match what the runner used to do, exactly."""

    def test_a_positive_npt_stage_means_npt_production(self):
        self.assertEqual(resolve_ensemble({"npt_steps": 50_000}), NPT)

    def test_a_zero_npt_stage_means_nvt_production(self):
        self.assertEqual(resolve_ensemble({"npt_steps": 0}), NVT)

    def test_saying_nothing_means_npt(self):
        # The runner's default NPT stage is positive, so a config that
        # leaves the stages alone has always been a constant-pressure run.
        self.assertEqual(resolve_ensemble({}), NPT)
        self.assertEqual(resolve_ensemble(None), NPT)

    def test_a_duration_counts_the_same_as_a_step_count(self):
        self.assertEqual(
            resolve_ensemble({"npt_duration_ns": 0.1, "timestep_fs": 2}), NPT)
        self.assertEqual(resolve_ensemble({"npt_duration_ns": 0}), NVT)


class TestWhatCouldNotBeSaidBefore(unittest.TestCase):

    def test_equilibrate_at_pressure_then_produce_at_volume(self):
        # The combination the explain text recommends and the software
        # could not express.
        self.assertEqual(
            resolve_ensemble({"npt_steps": 50_000, "ensemble": "nvt"}), NVT)

    def test_no_equilibration_but_keep_the_barostat(self):
        # A resumed segment: the system settled during the first segment,
        # and production is still at constant pressure.
        self.assertEqual(
            resolve_ensemble({"npt_steps": 0, "ensemble": "npt"}), NPT)

    def test_a_stated_ensemble_wins_over_the_inference(self):
        for stated in ENSEMBLES:
            with self.subTest(ensemble=stated):
                self.assertEqual(
                    resolve_ensemble({"npt_steps": 50_000,
                                      "ensemble": stated}), stated)


class TestTheRecordSaysWhoDecided(unittest.TestCase):
    """A reader should tell a choice somebody made from one made for them."""

    def test_a_stated_choice_says_so(self):
        self.assertIn("as the config asks",
                      describe_choice({"ensemble": "nvt"}))

    def test_an_inferred_npt_says_what_it_was_inferred_from(self):
        described = describe_choice({"npt_steps": 50_000})
        self.assertIn("inferred", described)
        self.assertIn("50,000", described)

    def test_an_inferred_nvt_says_what_that_costs(self):
        # Inferred NVT is the case worth a sentence: the box keeps whatever
        # density solvation gave it, which is rarely what anybody wanted.
        described = describe_choice({"npt_steps": 0})
        self.assertIn("density solvation gave it", described)
        self.assertIn("ensemble: nvt", described)


class TestTheSchemaCarriesIt(unittest.TestCase):

    def test_the_setting_exists_with_both_choices(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = next(f for f in PHASE_SCHEMAS["simulation"].fields
                     if f.name == "ensemble")
        self.assertEqual(set(field.choices), set(ENSEMBLES))
        self.assertIsNone(field.default)

    def test_the_help_names_the_case_that_was_impossible(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = next(f for f in PHASE_SCHEMAS["simulation"].fields
                     if f.name == "ensemble")
        self.assertIn("constant volume", field.help)
        self.assertIn("nobody intends", field.help)

    def test_a_config_stating_it_validates(self):
        from fastmdxplora.config.loader import validate_config

        for ensemble in ENSEMBLES:
            with self.subTest(ensemble=ensemble):
                validate_config({
                    "systems": [{"id": "a", "system": "x.pdb"}],
                    "simulation": {"ensemble": ensemble, "npt_steps": 1000}})

    def test_an_ensemble_the_software_does_not_know_is_refused(self):
        from fastmdxplora.config.loader import ConfigError, validate_config

        with self.assertRaises(ConfigError):
            validate_config({
                "systems": [{"id": "a", "system": "x.pdb"}],
                "simulation": {"ensemble": "nve"}})


class TestASegmentStatesItRatherThanInfers(unittest.TestCase):

    def segments(self, simulation, count=3):
        from fastmdxplora.simulation.resume import plan_segments

        return plan_segments(
            {"systems": [{"id": "a", "system": "x.pdb"}],
             "simulation": simulation}, segments=count)

    def test_a_resumed_segment_carries_the_study_s_ensemble(self):
        for label, block, expected in (
                ("default", {"duration_ns": 4}, NPT),
                ("explicit npt", {"duration_ns": 4, "npt_steps": 50_000}, NPT),
                ("nvt", {"duration_ns": 4, "npt_steps": 0}, NVT),
                ("npt eq then nvt",
                 {"duration_ns": 4, "npt_steps": 50_000, "ensemble": "nvt"},
                 NVT)):
            with self.subTest(study=label):
                for piece in self.segments(block)[1:]:
                    self.assertEqual(
                        piece.config["simulation"]["ensemble"], expected)

    def test_and_does_not_equilibrate_again(self):
        # The point of zeroing the stages, which is now safe to do because
        # the ensemble is stated separately.
        for piece in self.segments({"duration_ns": 4})[1:]:
            self.assertEqual(piece.config["simulation"]["nvt_steps"], 0)
            self.assertEqual(piece.config["simulation"]["npt_steps"], 0)

    def test_the_first_segment_is_left_alone(self):
        first = self.segments({"duration_ns": 4})[0].config["simulation"]
        self.assertNotIn("ensemble", first)
        self.assertNotIn("nvt_steps", first)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheWarningSaysWhatHappened(unittest.TestCase):
    """The density warning must agree with the run it describes.

    It is gated on whether the density was ever equilibrated, and that used
    to be the same question as `npt_steps > 0`. Separating the ensemble
    broke the equivalence: a resumed segment with `ensemble: npt` and no
    equilibration gets its barostat, and the warning still said it had
    none.

    Caught on ubiquitin, in a rehearsal, by reading the segment 1 banner —
    the barostat was there and the warning said otherwise. A warning that
    says the opposite of what happened is worse than no warning, because
    somebody reads it, believes the run was at fixed volume, and discards
    or defends a result on that basis.

    What it means now: the density was never equilibrated, and nothing in
    this run will equilibrate it.
    """

    def warns(self, simulation) -> bool:
        from fastmdxplora.simulation.ensembles import resolve_ensemble

        # The runner's condition, asserted here rather than by running a
        # simulation: `not npt_steps > 0 and not npt production`.
        never_equilibrated = int(simulation.get("npt_steps") or 0) <= 0
        return never_equilibrated and resolve_ensemble(simulation) != "npt"

    def test_a_run_that_never_equilibrates_the_density_warns(self):
        self.assertTrue(self.warns({"npt_steps": 0}))

    def test_a_resumed_segment_does_not(self):
        # It has a barostat. The equilibration happened in segment 0.
        self.assertFalse(self.warns({"npt_steps": 0, "ensemble": "npt"}))

    def test_npt_equilibration_then_nvt_production_does_not(self):
        # The recommended workflow. The density was corrected by a barostat
        # before the box was fixed at it, which is the whole point, and
        # warning here would train people to ignore the warning.
        self.assertFalse(self.warns({"npt_steps": 50_000,
                                     "ensemble": "nvt"}))

    def test_an_ordinary_npt_run_does_not(self):
        self.assertFalse(self.warns({"npt_steps": 50_000}))

    def test_the_runner_asks_the_ensemble_and_not_only_the_steps(self):
        # The regression this exists to prevent: with no NPT equilibration,
        # a run whose production is at constant pressure has its density set
        # by the barostat and is not warned about; one at constant volume is.
        import tempfile
        from pathlib import Path

        import pytest

        pytest.importorskip("openmm")
        from fastmdxplora.simulation import runner
        from tests._the_phase import a_prepared_water_box

        warned = []
        real = runner._warn_density_was_never_equilibrated
        runner._warn_density_was_never_equilibrated = (
            lambda *a, **k: warned.append(True) or real(*a, **k))
        try:
            for ensemble, expected in (("npt", []), ("nvt", [True])):
                warned.clear()
                root = Path(tempfile.mkdtemp())
                runner.run_simulation(**a_prepared_water_box(root), output_dir=str(root / "out"),
                                      production_steps=10, nvt_steps=10, npt_steps=0,
                                      minimize=False, platform="CPU", ensemble=ensemble)
                self.assertEqual(warned, expected, ensemble)
        finally:
            runner._warn_density_was_never_equilibrated = real

class TestEveryPlaceThatAsksAsksTheSameWay(unittest.TestCase):
    """Three places asked whether there was a barostat, and by the end all
    three had to be corrected separately.

    The runner's barostat gate, the runner's density warning, and the
    advisory shown before a run starts. While `npt_steps > 0` answered both
    questions, asking it was right everywhere. Separating them left each
    one wrong until it was found, and they were found one rehearsal at a
    time: the gate first, then the warning, then this.

    The advisory was the worst of the three to find, because it lives in a
    different module and fires before the simulation phase, so fixing the
    runner did nothing to it and the output looked half-mended.
    """

    def advises(self, simulation) -> bool:
        from fastmdxplora.advisories import _a_density_never_equilibrated

        return bool(_a_density_never_equilibrated({}, simulation))

    def test_a_run_that_never_equilibrates_is_advised(self):
        self.assertTrue(self.advises({"npt_steps": 0}))

    def test_a_resumed_segment_is_not(self):
        self.assertFalse(self.advises({"npt_steps": 0, "ensemble": "npt"}))

    def test_npt_equilibration_then_nvt_is_not(self):
        self.assertFalse(
            self.advises({"npt_steps": 50_000, "ensemble": "nvt"}))

    def test_an_ordinary_run_is_not(self):
        self.assertFalse(self.advises({"npt_steps": 50_000}))
        self.assertFalse(self.advises({}))

    def test_the_advisory_and_the_warning_agree(self):
        # They describe the same fact to the same reader, before and during
        # the run. Disagreeing would be worse than either being wrong.
        from fastmdxplora.simulation.ensembles import resolve_ensemble

        for simulation in ({"npt_steps": 0},
                           {"npt_steps": 0, "ensemble": "npt"},
                           {"npt_steps": 50_000},
                           {"npt_steps": 50_000, "ensemble": "nvt"}):
            with self.subTest(simulation=simulation):
                never = int(simulation.get("npt_steps") or 0) <= 0
                warns = never and resolve_ensemble(simulation) != "npt"
                self.assertEqual(self.advises(simulation), warns)

    def test_no_other_module_infers_the_ensemble_from_the_stage(self):
        """The guard that would have found all three at once.

        Any `npt_steps` comparison outside the places that legitimately ask
        how long the stage runs is a place deciding the ensemble from the
        wrong question.
        """
        import re
        from pathlib import Path

        import fastmdxplora

        root = Path(fastmdxplora.__file__).parent
        allowed = {"ensembles.py", "resume.py", "schema.py", "runner.py",
                   "advisories.py"}
        offenders = []
        for path in root.rglob("*.py"):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if re.search(r'npt_steps.*(> 0|== 0|<= 0)', line):
                    offenders.append(f"{path.relative_to(root).as_posix()}: "
                                     f"{line.strip()[:60]}")
        self.assertEqual(offenders, [])
