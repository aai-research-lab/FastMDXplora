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
