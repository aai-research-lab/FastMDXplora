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
        self.assertEqual(plan.config["include_phase"], ["simulation"])

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
        self.assertIn('whole["include_phase"] = ["analysis", "report"]', source)
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
        self.assertIn("Path(str(resume_from)).is_dir()", source)
        self.assertIn('total_ns=simulation.get("duration_ns")', source)
        self.assertIn('more_ns=simulation.get("extra_ns")', source)



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

    def test_the_staged_runner_uses_the_same_segment_names(self):
        # A campaign's segments and an extension's are the same
        # convention, so the same join reads both.
        import inspect

        from fastmdxplora.agent import run as staged

        self.assertIn('f"segment-{segment:03d}"',
                      inspect.getsource(staged.segment_directory))


class TestAKilledRunIsResumedFromItsLastCheckpoint(unittest.TestCase):
    """A killed run's trajectory holds frames written after its last
    checkpoint -- the frames a resume runs again. They are left out of
    the join, so the pieces meet at the checkpoint rather than
    overlapping, and the trim is recorded."""

    def test_the_frames_to_keep_are_counted_from_the_record(self):
        from fastmdxplora.simulation.resume import frames_before_checkpoint

        root = _study()
        (root / "resolved_config.yml").write_text(yaml.safe_dump({
            "simulation": {"trajectory_interval_steps": 125}}), encoding="utf-8")
        # A frame every 125 steps, a checkpoint at 250,000: 2,000 frames
        # precede it and anything after is what the resume runs again.
        self.assertEqual(frames_before_checkpoint(root), 2000)

    def test_a_guess_is_refused_rather_than_made(self):
        from fastmdxplora.simulation.resume import frames_before_checkpoint

        root = _study()
        (root / "resolved_config.yml").write_text(yaml.safe_dump({
            "simulation": {}}), encoding="utf-8")
        self.assertIsNone(frames_before_checkpoint(root))

    def test_the_join_leaves_the_overlap_out(self):
        from fastmdxplora.simulation.resume import extend_study

        root = _study()
        (root / "simulation" / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).unlink()
        (root / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"id": "s1", "system": "1L2Y"}],
            "simulation": {"duration_ns": 0.5, "nvt_steps": 50000,
                           "npt_steps": 50000, "timestep_fs": 2.0,
                           "trajectory_interval_steps": 125}}), encoding="utf-8")
        import inspect

        source = inspect.getsource(extend_study)
        self.assertIn("keep_frames[piece.index] = keep", source)
        self.assertIn("keep_frames=keep_frames or None", source)
        # And the resume is told it is loading an unsealed checkpoint
        # rather than inferring it.
        self.assertIn('["resume_unsealed"] = True', source)

    def test_the_join_records_what_it_left_out(self):
        import inspect

        from fastmdxplora.analysis import joining

        source = inspect.getsource(joining.join_segments)
        self.assertIn('"trimmed": {str(index): count', source)
        self.assertIn("if written_here >= allowed:", source)

    def test_where_it_cannot_be_counted_the_join_is_still_refused(self):
        from fastmdxplora.simulation.resume import extend_study

        root = _study()
        (root / "simulation" / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).unlink()
        (root / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"id": "s1", "system": "1L2Y"}],
            "simulation": {"duration_ns": 0.5, "nvt_steps": 50000,
                           "npt_steps": 50000, "timestep_fs": 2.0}}),
            encoding="utf-8")
        answer = extend_study(root, more_ns=0.1)
        self.assertFalse(answer["ok"])
        self.assertIn("cannot be worked out", answer["error"])
        self.assertFalse((root / "segment-001").exists())


class TestACheckpointAndItsSealAreOneFact(unittest.TestCase):
    """The seal says the file is whole -- that a kill did not tear the
    write -- and that is as true of a checkpoint at step 2,000 as of the
    last one. Sealing only at a clean finish made the marker say "this
    run finished" instead, so the checkpoint a killed run leaves, the one
    worth resuming from, looked damaged when it was intact."""

    def pair(self, payload: bytes = b"state-bytes"):
        import hashlib

        from fastmdxplora.simulation.runner import CHECKPOINT_DIGEST_SUFFIX

        folder = Path(tempfile.mkdtemp())
        checkpoint = folder / "checkpoint.chk"
        checkpoint.write_bytes(payload)
        seal = checkpoint.with_suffix(checkpoint.suffix + CHECKPOINT_DIGEST_SUFFIX)
        seal.write_text(f"{len(payload)} {hashlib.sha256(payload).hexdigest()}\n",
                        encoding="utf-8")
        return checkpoint, seal

    def test_every_checkpoint_is_sealed_as_it_is_written(self):
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner._attach_checkpoint_reporter)
        self.assertIn("new_seal.write_text(", source)
        self.assertIn("_os.replace(new_chk, chk_path)", source)
        self.assertIn("_os.replace(new_seal, seal_path)", source)

    def test_a_matching_pair_verifies(self):
        from fastmdxplora.simulation.runner import verify_checkpoint

        checkpoint, _ = self.pair()
        self.assertTrue(verify_checkpoint(checkpoint, require_seal=True))

    def test_caught_between_the_two_renames_it_still_verifies(self):
        # The checkpoint is renamed in before its seal, so the pair can be
        # caught a moment apart. The seal written alongside it is on disk
        # under its temporary name and verifies it exactly.
        from fastmdxplora.simulation.runner import verify_checkpoint

        checkpoint, seal = self.pair()
        seal.rename(seal.with_suffix(seal.suffix + ".new"))
        self.assertTrue(verify_checkpoint(checkpoint, require_seal=True))

    def test_a_torn_file_is_still_refused(self):
        from fastmdxplora.refusals import UnstableRun
        from fastmdxplora.simulation.runner import verify_checkpoint

        checkpoint, _ = self.pair()
        checkpoint.write_bytes(b"torn")
        with self.assertRaises(UnstableRun):
            verify_checkpoint(checkpoint, require_seal=True)

    def test_the_runner_accepts_an_unsealed_checkpoint_only_when_told(self):
        import inspect

        from fastmdxplora.simulation import pipeline, runner

        source = inspect.getsource(runner.run_simulation)
        self.assertIn("require_seal=not bool(resume_unsealed)", source)
        # And the key reaches the runner from the config rather than
        # being set somewhere in between.
        self.assertIn('resume_unsealed=bool(params.get("resume_unsealed"))',
                      inspect.getsource(pipeline))


class TestOneSettingOneFlag(unittest.TestCase):
    """Continuing a study is a property of the simulation phase, so it is
    asked for the way every simulation setting is: one setting, one flag
    generated from it, one GUI field. It was three top-level settings the
    CLI could not express and two subcommands the config could not."""

    def test_the_top_level_settings_are_gone(self):
        from fastmdxplora.config.schema import all_schemas

        top = {f.name for f in all_schemas()["(top-level)"].fields}
        for gone in ("continues", "duration_ns", "extra_ns"):
            self.assertNotIn(gone, top)

    def test_the_settings_live_in_the_simulation_block(self):
        from fastmdxplora.config.schema import all_schemas

        simulation = all_schemas()["simulation"]
        self.assertIsNotNone(simulation.get("extra_ns"))
        self.assertIsNotNone(simulation.get("resume_from"))

    def test_resume_from_says_what_a_study_does_and_what_a_file_does(self):
        from fastmdxplora.config.schema import all_schemas

        help_text = all_schemas()["simulation"].get("resume_from").help
        self.assertIn("STUDY DIRECTORY", help_text)
        self.assertIn("CHECKPOINT FILE", help_text)

    def test_the_flags_are_generated_from_the_schema(self):
        import subprocess
        import sys

        for flag in ("--resume-from", "--extra-ns"):
            out = subprocess.run([sys.executable, "-m", "fastmdxplora.cli.main",
                                  "simulate", "--help"],
                                 capture_output=True, text=True, timeout=120).stdout
            self.assertIn(flag, out)

    def test_the_subcommands_are_gone(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
                  / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('("resume", "Finish a study', source)
        self.assertNotIn('if args.command in ("extend", "resume")', source)

    def test_a_study_directory_routes_to_the_continuation(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
                  / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn('Path(str(resume_from)).is_dir()', source)
        self.assertIn('total_ns=simulation.get("duration_ns")', source)
        self.assertIn('more_ns=simulation.get("extra_ns")', source)

    def test_every_top_level_setting_is_in_a_group(self):
        # Three settings landed at the top level with no group and no flag,
        # and nothing checked. Now nothing can.
        from fastmdxplora.config.schema import SETTING_GROUPS, all_schemas

        top = {f.name for f in all_schemas()["(top-level)"].fields}
        grouped = {n for _t, _w, names in SETTING_GROUPS["(top-level)"] for n in names}
        self.assertEqual(top - grouped, set())

    def test_the_agent_is_given_the_simulation_form(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("extend it")
        self.assertIn("resume_from: ./fastmdxplora_1L2Y_study", prompt)
        self.assertIn("extra_ns: 0.1", prompt)
        self.assertNotIn("continues:", prompt)
