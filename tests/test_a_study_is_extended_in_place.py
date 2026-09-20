"""Extending a study leaves one study, not two.

A continuation is more of the same study: the same system, the same
water, the same velocities carried through a checkpoint. So it belongs
inside the study as its next segment, and the pieces are put together
and reanalysed by the software rather than by hand. What the join
refuses it still refuses; automatic does not mean unchecked.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from fastmdxplora.analysis.joining import _config_digest, survey_segments
from fastmdxplora.simulation.runner import (CHECKPOINT_DIGEST_SUFFIX,
                                             write_checkpoint_sidecar)


def _seal(directory: Path) -> None:
    (directory / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).write_text("s", encoding="utf-8")


def _study(*, done_steps: int = 250_000, duration: float = 0.5) -> Path:
    root = Path(tempfile.mkdtemp()) / "fastmdxplora_trpcage_study_x"
    (root / "simulation").mkdir(parents=True)
    (root / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "s1", "system": "1L2Y"}],
        "setup": {"solvent_padding_nm": 1.2, "ligand_resname": None},
        "simulation": {"duration_ns": duration, "nvt_steps": 50000,
                       "npt_steps": 50000, "timestep_fs": 2.0},
        "analysis": {"include": ["rmsd"]}}), encoding="utf-8")
    (root / "simulation" / "production.dcd").write_bytes(b"frames")
    _seal(root / "simulation")
    checkpoint = root / "simulation" / "checkpoint.chk"
    checkpoint.write_bytes(b"x")
    write_checkpoint_sidecar(checkpoint, stage="production", step=done_steps,
                             ensemble="npt", temperature_K=300.0,
                             timestep_fs=2.0, study=str(root))
    return root


class TestAWholeStudyIsItsOwnFirstSegment(unittest.TestCase):

    def test_it_is_segment_zero_and_nothing_is_moved(self):
        # The index a campaign's first segment has, so the join's rule
        # that segments start at zero holds for both.
        root = _study()
        pieces = survey_segments(root)
        self.assertEqual([p.index for p in pieces], [0])
        self.assertTrue(pieces[0].finished)
        self.assertTrue((root / "simulation" / "production.dcd").is_file())

    def test_a_campaign_that_numbers_itself_is_untouched(self):
        base = Path(tempfile.mkdtemp())
        for index in (0, 1):
            folder = base / f"segment-{index:03d}" / "simulation"
            folder.mkdir(parents=True)
            (folder / "production.dcd").write_bytes(b"x")
            _seal(folder)
        self.assertEqual([p.index for p in survey_segments(base)], [0, 1])

    def test_the_next_extension_is_one(self):
        from fastmdxplora.simulation.resume import next_segment_index

        self.assertEqual(next_segment_index(_study()), 1)


class TestTheExtensionIsPlannedInsideTheStudy(unittest.TestCase):

    def test_it_lands_in_the_study_and_simulates_only(self):
        from fastmdxplora.simulation.resume import extension_of

        root = _study()
        plan = extension_of(root, total_ns=0.6)
        self.assertTrue(plan.possible)
        self.assertEqual(Path(plan.config["output"]).name, "segment-001")
        self.assertEqual(Path(plan.config["output"]).parent, root)
        self.assertEqual(plan.config["include"], ["simulation"])

    def test_the_arithmetic_counts_what_the_study_has(self):
        from fastmdxplora.simulation.resume import extension_of, production_done_ns

        root = _study()
        self.assertAlmostEqual(production_done_ns(root), 0.5, places=6)
        plan = extension_of(root, total_ns=0.6)
        self.assertAlmostEqual(plan.config["simulation"]["duration_ns"], 0.1, places=6)
        self.assertAlmostEqual(extension_of(root, more_ns=0.2)
                               .config["simulation"]["duration_ns"], 0.2, places=6)


class TestAContinuationIsTheSameStudy(unittest.TestCase):
    """A segment that reuses another study's prepared system IS that
    study: the same solvated box, the same water placement, the same
    atoms, on disk. The digest said otherwise because resolving a config
    materialises defaults unevenly -- a ligand name appeared for a study
    with no ligand -- and the join refused to join a run to its own
    continuation."""

    def test_a_segment_inherits_its_parents_identity(self):
        root = _study()
        segment = root / "segment-001"
        (segment).mkdir()
        (segment / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"id": "s1", "system": "1L2Y"}],
            # Resolved differently, as a real one is: a default materialised
            # and the ensemble made explicit.
            "setup": {"solvent_padding_nm": 1.2, "ligand_resname": "LIG"},
            "simulation": {"duration_ns": 0.1, "nvt_steps": 0, "npt_steps": 0,
                           "timestep_fs": 2.0, "minimize": False,
                           "ensemble": "npt", "setup_from": str(root),
                           "prepared_from": str(root)}}), encoding="utf-8")
        self.assertEqual(_config_digest(segment), _config_digest(root))

    def test_a_different_study_is_still_different(self):
        a, b = _study(), _study()
        (b / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"id": "s1", "system": "1UBQ"}],
            "setup": {"solvent_padding_nm": 1.2},
            "simulation": {"duration_ns": 0.5, "timestep_fs": 2.0}}), encoding="utf-8")
        self.assertNotEqual(_config_digest(a), _config_digest(b))


class TestTheDriverDoesAllThree(unittest.TestCase):

    def test_it_plans_simulates_joins_and_reanalyses(self):
        import inspect

        from fastmdxplora.simulation.resume import extend_study

        source = inspect.getsource(extend_study)
        self.assertIn("join_segments(root", source)
        self.assertIn('whole["include"] = ["analysis", "report"]', source)
        # The join is against the trajectory's own topology, not the
        # solvated system's, and the study's report is replaced on purpose.
        self.assertIn("trajectory_topology.pdb", source)
        self.assertIn("explore(force=True)", source)

    def test_a_refusal_stops_the_step(self):
        # An unjoinable study is reported, not worked around.
        from fastmdxplora.simulation.resume import extend_study

        root = _study()
        (root / "resolved_config.yml").unlink()
        answer = extend_study(root, more_ns=0.1)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["stage"], "planning")

    def test_the_cli_offers_it(self):
        from pathlib import Path as P

        source = (P(__file__).resolve().parents[1] / "src" / "fastmdxplora"
                  / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"extend",', source)
        self.assertIn('group.add_argument("--to"', source)
        self.assertIn('group.add_argument("--more"', source)
        self.assertIn('if args.command == "extend":', source)


class TestResumeAndExtendAreOneMechanism(unittest.TestCase):
    """Two intentions, one path. Resume finishes what the study planned;
    extend runs past it. Both resume from the checkpoint, land inside the
    study as its next segment, and join."""

    def test_no_flag_runs_the_remainder_of_the_plan(self):
        from fastmdxplora.simulation.resume import extension_of

        # Planned 0.5 ns, 0.3 of it done.
        plan = extension_of(_study(done_steps=150_000))
        self.assertAlmostEqual(plan.config["simulation"]["duration_ns"], 0.2, places=6)

    def test_a_flag_runs_past_the_plan(self):
        from fastmdxplora.simulation.resume import extension_of

        plan = extension_of(_study(done_steps=250_000), total_ns=0.6)
        self.assertAlmostEqual(plan.config["simulation"]["duration_ns"], 0.1, places=6)

    def test_both_land_in_the_next_segment_of_the_same_study(self):
        from fastmdxplora.simulation.resume import extension_of

        root = _study(done_steps=150_000)
        for plan in (extension_of(root), extension_of(root, more_ns=0.1)):
            with self.subTest(plan=plan.config["simulation"]["duration_ns"]):
                self.assertEqual(Path(plan.config["output"]), root / "segment-001")
                self.assertFalse(plan.config["simulation"]["minimize"])
                self.assertEqual(plan.config["simulation"]["nvt_steps"], 0)

    def test_the_cli_makes_the_flags_optional(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
                  / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn("group = ex.add_mutually_exclusive_group()", source)
        self.assertNotIn("add_mutually_exclusive_group(required=True)", source)

    def test_the_staged_runner_uses_the_same_segment_names(self):
        # A campaign's segments and an extension's are the same
        # convention, so the same join reads both.
        import inspect

        from fastmdxplora.agent import run as staged

        self.assertIn('f"segment-{segment:03d}"',
                      inspect.getsource(staged.segment_directory))


class TestAKilledPieceIsRefusedBeforeAnythingRuns(unittest.TestCase):
    """A segment with no seal was killed, and its trajectory holds frames
    written after its last checkpoint -- the frames a resume would run
    again. Joining them would leave that overlap in the middle of the
    trajectory with nothing to mark it."""

    def test_it_is_refused_with_the_reason(self):
        from fastmdxplora.simulation.resume import extend_study

        root = _study()
        (root / "simulation" / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).unlink()
        answer = extend_study(root, more_ns=0.1)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["stage"], "planning")
        self.assertEqual(answer["unsealed"], [0])
        self.assertIn("frames written after its last checkpoint", answer["error"])

    def test_it_is_refused_before_a_segment_is_simulated(self):
        from fastmdxplora.simulation.resume import extend_study

        root = _study()
        (root / "simulation" / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).unlink()
        extend_study(root, more_ns=0.1)
        self.assertFalse((root / "segment-001").exists(),
                         "a refusal must not leave a half-run segment behind")
