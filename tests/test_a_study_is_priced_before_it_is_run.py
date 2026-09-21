"""An unattended study is priced between setup and the expensive part.

`--assisted` hands you a config and stops, because you are there to read
it. `--autonomous` does not, so something else has to stop it, and that is
a budget.

A budget needs a number, and the number does not exist when the agent
finishes writing. Cost scales with the *solvated* particle count, which
depends on box shape, padding and ion concentration — decisions setup
makes. A protein of 2,000 atoms is 60,000 solvated, and guessing from the
residue count would be inventing the water.

So the gate sits where the information first exists and before the cost is
incurred. Earlier and it would be guessing; later and there would be
nothing left to stop.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastmdxplora.agent import particles_after_setup, run_in_stages


class TestTheOrderOfOperations(unittest.TestCase):
    """With a stub for `explore`, so the sequencing is what is tested."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.calls: list[dict] = []

    def explore(self, *, config, output_dir):
        self.calls.append({"include_phase": config.get("include_phase"),
                           "exclude_phase": config.get("exclude_phase"),
                           "setup_from": (config.get("simulation") or {}
                                          ).get("setup_from")})
        # Stand in for what setup writes, so the count is findable.
        if config.get("include_phase") == ["setup"]:
            import json

            where = Path(output_dir) / "setup"
            where.mkdir(parents=True, exist_ok=True)
            (where / "setup_parameters.json").write_text(
                json.dumps({"n_atoms_solvated": 60_000}))

    def calibrated(self):
        from fastmdxplora.cost import calibrate

        calibrate(particles=30_000, steps=5_000, seconds=42.0,
                  platform_name="CUDA", precision="mixed")

    def config(self, steps=1_000_000):
        return {"systems": [{"id": "a", "system": "x.pdb"}],
                "simulation": {"production_steps": steps,
                               "nvt_steps": 0, "npt_steps": 0}}

    def test_setup_runs_first_and_alone(self):
        self.calibrated()
        run_in_stages(self.config(), self.root, budget_hours=1000,
                      platform_name="CUDA", explore=self.explore)
        self.assertEqual(self.calls[0]["include_phase"], ["setup"])

    def test_the_rest_excludes_setup_and_reuses_it(self):
        # Rerunning setup would solvate a second time and give a different
        # particle count from the one the study was priced on.
        self.calibrated()
        run_in_stages(self.config(), self.root, budget_hours=1000,
                      platform_name="CUDA", explore=self.explore)
        self.assertEqual(self.calls[1]["exclude_phase"], ["setup"])
        self.assertIn("setup", self.calls[1]["setup_from"])

    def test_setup_from_goes_in_the_simulation_block(self):
        # It is a simulation setting, not a top-level one. Put at the top
        # level the loader refuses it and names the right place, which is
        # how this was found.
        from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL

        self.assertIn("setup_from",
                      {f.name for f in PHASE_SCHEMAS["simulation"].fields})
        self.assertNotIn("setup_from", {f.name for f in TOP_LEVEL.fields})

    def test_a_study_over_budget_does_not_reach_the_simulation(self):
        self.calibrated()
        staged = run_in_stages(self.config(steps=10**9), self.root,
                               budget_hours=0.5, platform_name="CUDA",
                               explore=self.explore)
        self.assertTrue(staged.setup_done)
        self.assertFalse(staged.simulated)
        self.assertEqual(staged.stopped_at, "estimate")
        self.assertEqual(len(self.calls), 1, "the simulation was started")

    def test_the_refusal_names_the_number(self):
        # The answer to "too expensive" is usually a shorter run rather
        # than a bigger allowance, and a caller cannot choose without the
        # figure.
        self.calibrated()
        staged = run_in_stages(self.config(steps=10**9), self.root,
                               budget_hours=0.5, platform_name="CUDA",
                               explore=self.explore)
        self.assertEqual(staged.refusal.code, "environment.budget.exhausted")
        self.assertIn("estimate_hours", staged.refusal.details)
        self.assertEqual(staged.refusal.details["particles"], 60_000)

    def test_setup_output_is_kept_when_the_budget_refuses(self):
        # It cost minutes and it is worth having: a shorter study can reuse
        # it, and the particle count is the thing that made the refusal
        # possible.
        self.calibrated()
        run_in_stages(self.config(steps=10**9), self.root, budget_hours=0.5,
                      platform_name="CUDA", explore=self.explore)
        self.assertTrue(
            (self.root / "setup" / "setup_parameters.json").is_file())

    def test_a_study_within_budget_runs_both_stages(self):
        self.calibrated()
        staged = run_in_stages(self.config(steps=1000), self.root,
                               budget_hours=1000, platform_name="CUDA",
                               explore=self.explore)
        self.assertTrue(staged.simulated)
        self.assertEqual(staged.stopped_at, "")
        self.assertEqual(len(self.calls), 2)

    def test_no_budget_means_no_ceiling(self):
        # Right for a caller who is watching, wrong for one who is not --
        # which is why --autonomous refuses without one rather than
        # defaulting.
        self.calibrated()
        staged = run_in_stages(self.config(steps=10**9), self.root,
                               platform_name="CUDA", explore=self.explore)
        self.assertTrue(staged.simulated)


class TestWhenThereIsNoNumber(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_setup_that_records_no_count_stops_the_run(self):
        # Running on regardless would spend an unknown amount, which is the
        # one thing an unattended run must not do.
        staged = run_in_stages(
            {"systems": []}, self.root, budget_hours=10,
            explore=lambda **kw: None)
        self.assertTrue(staged.setup_done)
        self.assertIsNone(staged.particles)
        self.assertEqual(staged.refusal.code, "setup.structure.undetermined")

    def test_an_unmeasured_machine_stops_it_too(self):
        # No calibration means no ceiling, and no ceiling is what the
        # budget exists to provide.
        import json
        import os

        from fastmdxplora.cost import calibration_path

        before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(Path(tempfile.mkdtemp()))
        try:
            def explore(*, config, output_dir):
                if config.get("include_phase") == ["setup"]:
                    where = Path(output_dir) / "setup"
                    where.mkdir(parents=True, exist_ok=True)
                    (where / "setup_parameters.json").write_text(
                        json.dumps({"n_atoms_solvated": 60_000}))

            self.assertFalse(calibration_path().exists())
            staged = run_in_stages(
                {"systems": [], "simulation": {"production_steps": 1000}},
                self.root, budget_hours=10, explore=explore)
            self.assertFalse(staged.simulated)
            self.assertEqual(staged.refusal.code,
                             "environment.calibration.absent")
        finally:
            if before is None:
                os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
            else:
                os.environ["FASTMDXPLORA_CONFIG_DIR"] = before

    def test_a_failed_setup_is_recorded_as_such(self):
        def explode(**kw):
            raise ValueError("the structure could not be prepared")

        staged = run_in_stages({"systems": []}, self.root, budget_hours=10,
                               explore=explode)
        self.assertFalse(staged.setup_done)
        self.assertEqual(staged.stopped_at, "setup")
        self.assertIn("could not be prepared", staged.refusal.message)


class TestReadingTheCount(unittest.TestCase):

    def test_it_comes_from_setup_parameters_not_the_manifest(self):
        # The manifest was the first guess and it is not there: the
        # manifest records artifacts, status and timing, and the counts
        # live with the phase's own parameters.
        import json

        root = Path(tempfile.mkdtemp())
        (root / "setup").mkdir(parents=True)
        (root / "setup" / "setup_parameters.json").write_text(
            json.dumps({"n_atoms_solvated": 2349}))
        self.assertEqual(particles_after_setup(root), 2349)

    def test_a_missing_record_is_none_rather_than_a_guess(self):
        self.assertIsNone(particles_after_setup(Path(tempfile.mkdtemp())))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
