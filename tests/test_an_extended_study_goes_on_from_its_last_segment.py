"""A study extended twice goes on from where it last stopped.

Each extension is the study's next segment, resumed from a checkpoint.
The checkpoint has to be the last segment's: the study's own is where its
first run stopped, and a second extension resumed from there runs the
first extension's span again -- at the same thread count it reproduces it
frame for frame -- and the join then holds that span twice with nothing to
say so. Which segment is last is decided by its number, the order the
join puts the pieces in, not by how the folders list or when they were
touched.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import yaml

from fastmdxplora.simulation.runner import write_checkpoint_sidecar
from tests.test_a_study_is_extended_in_place import _seal, _study

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False


def _checkpoint(folder: Path) -> Path:
    return (folder / "simulation" / "checkpoint.chk").resolve()


def _record(folder: Path, *, step: int, finished: bool,
            interval: int | None = None) -> None:
    """The sidecar a run leaves beside its checkpoint."""
    write_checkpoint_sidecar(folder / "simulation" / "checkpoint.chk", stage="production",
                             step=step, ensemble="npt", temperature_K=300.0,
                             timestep_fs=2.0, study=str(folder), finished=finished,
                             trajectory_interval_steps=interval)


def _segment(root: Path, index: int, *, done_steps: int = 50_000,
             duration: float = 0.1, finished: bool = True,
             interval: int | None = None) -> Path:
    """An extension as it is left inside the study: its own resolved
    config, resumed from the segment before it, with a sealed checkpoint."""
    folder = root / f"segment-{index:03d}"
    (folder / "simulation").mkdir(parents=True)
    before = root if index == 1 else root / f"segment-{index - 1:03d}"
    (folder / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "s1", "system": "1L2Y"}],
        "setup": {"solvent_padding_nm": 1.2, "ligand_resname": None},
        "simulation": {"duration_ns": duration, "nvt_steps": 0, "npt_steps": 0,
                       "timestep_fs": 2.0, "minimize": False, "ensemble": "npt",
                       "setup_from": str(root),
                       "resume_from": str(before / "simulation" / "checkpoint.chk")}}),
        encoding="utf-8")
    (folder / "simulation" / "production.dcd").write_bytes(b"frames")
    _seal(folder / "simulation")
    (folder / "simulation" / "checkpoint.chk").write_bytes(b"x")
    _record(folder, step=done_steps, finished=finished, interval=interval)
    return folder


class TestTheNextExtensionResumesFromTheLastSegment(unittest.TestCase):

    def test_a_second_extension_resumes_from_the_first(self):
        from fastmdxplora.simulation.resume import extension_of

        root = _study()
        first = _segment(root, 1)
        plan = extension_of(root, more_ns=0.1)
        self.assertTrue(plan.possible, plan.refusal)
        self.assertEqual(plan.config["simulation"]["resume_from"], str(_checkpoint(first)))
        self.assertEqual(Path(plan.config["output"]).name, "segment-002")
        # The prepared system is still the study's own; only the state moves on.
        self.assertEqual(plan.config["simulation"]["setup_from"], str(root.resolve()))

    def test_a_third_extension_resumes_from_the_second(self):
        from fastmdxplora.simulation.resume import extension_of

        root = _study()
        # Made out of order, and the first touched last, so neither the
        # listing nor the times would pick the second.
        second = _segment(root, 2)
        first = _segment(root, 1)
        later = second.stat().st_mtime + 60
        os.utime(first, (later, later))
        plan = extension_of(root, more_ns=0.1)
        self.assertEqual(plan.config["simulation"]["resume_from"], str(_checkpoint(second)))
        self.assertEqual(Path(plan.config["output"]).name, "segment-003")

    def test_what_is_done_is_counted_across_the_segments(self):
        # The first run was killed 0.3 ns into a 0.5 ns plan and the first
        # extension ran the remaining 0.2. Nothing of the plan remains, so
        # continuing it again is refused rather than resumed from where the
        # first run stopped, which would run those 0.2 ns a second time.
        from fastmdxplora.simulation.resume import extension_of

        root = _study(done_steps=150_000, finished=False)
        first = _segment(root, 1, done_steps=100_000, duration=0.2)
        plan = extension_of(root)
        self.assertFalse(plan.possible)
        self.assertIn("reached 0.500 ns", plan.refusal)
        # And a total counts all of it.
        plan = extension_of(root, total_ns=0.8)
        self.assertAlmostEqual(plan.config["simulation"]["duration_ns"], 0.3, places=6)
        self.assertEqual(plan.config["simulation"]["resume_from"], str(_checkpoint(first)))

    def test_the_pieces_are_in_the_order_they_ran(self):
        # By name, segment-1000 lists before segment-101.
        from fastmdxplora.analysis.joining import survey_segments
        from fastmdxplora.simulation.resume import last_segment

        root = _study()
        for index in (101, 1000):
            _segment(root, index)
        self.assertEqual([p.index for p in survey_segments(root)], [0, 101, 1000])
        self.assertEqual(last_segment(root).name, "segment-1000")


class TestAKilledSegmentIsStillCutBackToItsCheckpoint(unittest.TestCase):
    """Whichever segment was killed, its frames past its last checkpoint
    stay out of the join, and the resume accepts the checkpoint it left."""

    def extend(self, root: Path) -> tuple[dict, dict]:
        import fastmdxplora
        from fastmdxplora.analysis import joining
        from fastmdxplora.simulation.resume import extend_study

        with mock.patch.object(fastmdxplora, "FastMDXplora") as run, \
                mock.patch.object(joining, "join_segments",
                                  return_value={"segments": [0, 1, 2]}) as join:
            answer = extend_study(root, more_ns=0.1, analyse=False)
        self.assertTrue(answer["ok"], answer.get("error"))
        return run.call_args.kwargs["config_data"]["simulation"], join.call_args.kwargs

    def test_a_killed_last_segment_is_resumed_from_its_own_checkpoint(self):
        root = _study()
        killed = _segment(root, 1, done_steps=25_000, finished=False, interval=500)
        simulation, join = self.extend(root)
        self.assertEqual(simulation["resume_from"], str(_checkpoint(killed)))
        self.assertTrue(simulation["resume_unsealed"])
        self.assertEqual(join["keep_frames"], {1: 50})

    def test_a_killed_first_run_stays_trimmed_after_it_was_continued(self):
        root = _study(done_steps=150_000, finished=False)
        _record(root, step=150_000, finished=False, interval=500)
        first = _segment(root, 1, done_steps=100_000, duration=0.2, interval=500)
        simulation, join = self.extend(root)
        self.assertEqual(simulation["resume_from"], str(_checkpoint(first)))
        self.assertEqual(join["keep_frames"], {0: 300})


@unittest.skipUnless(HAS_BACKENDS, "OpenMM and MDTraj are needed")
class TestAStudyIsReallyExtendedTwice(unittest.TestCase):
    """A real run of a small water box, extended twice through the same
    call the command line makes, and the joined trajectory read back."""

    @classmethod
    def setUpClass(cls):
        from fastmdxplora.simulation.resume import extend_study
        from fastmdxplora.simulation.runner import run_simulation
        from tests._the_phase import a_prepared_water_box

        cls.root = Path(tempfile.mkdtemp()) / "study"
        cls.root.mkdir()
        box = a_prepared_water_box(cls.root)
        simulation = {"platform": "CPU", "production_steps": 100, "nvt_steps": 10,
                      "npt_steps": 0, "minimize": False, "timestep_fs": 2.0,
                      "trajectory_interval_steps": 50, "checkpoint_interval_steps": 50}
        run_simulation(**box, output_dir=str(cls.root / "simulation"), **simulation)
        (cls.root / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"id": "water", "system": str(cls.root / "seed.pdb")}],
            "simulation": simulation}), encoding="utf-8")
        # 100 more production steps each time.
        cls.answers = [extend_study(cls.root, more_ns=0.0002, analyse=False)
                       for _ in range(2)]

    def frames(self, trajectory: Path) -> np.ndarray:
        import mdtraj

        return mdtraj.load(str(trajectory),
                           top=str(self.root / "simulation" / "topology.pdb")).xyz

    def test_both_extensions_ran_as_the_next_segment(self):
        for answer, name in zip(self.answers, ("segment-001", "segment-002")):
            with self.subTest(segment=name):
                self.assertTrue(answer["ok"], answer.get("error"))
                self.assertEqual(Path(answer["segment"]).name, name)

    def test_the_second_continued_the_first(self):
        first, second = self.root / "segment-001", self.root / "segment-002"
        record = json.loads((second / "simulation" / "simulation_parameters.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(Path(record["continues"]["checkpoint"]).resolve(), _checkpoint(first))
        config = yaml.safe_load((self.root / "segment-002.yml").read_text(encoding="utf-8"))
        self.assertEqual(config["simulation"]["resume_from"], str(_checkpoint(first)))

    def test_every_segment_is_joined_once_in_order(self):
        record = json.loads((self.root / "joined" / "joined.json").read_text(encoding="utf-8"))
        self.assertEqual(record["segments"], [0, 1, 2])
        pieces = [self.frames(folder / "simulation" / "production.dcd")
                  for folder in (self.root, self.root / "segment-001",
                                 self.root / "segment-002")]
        joined = self.frames(self.root / "joined" / "production.dcd")
        self.assertEqual(record["frames"], sum(len(piece) for piece in pieces))
        # Equal to the float32 rounding of the join's unit conversion.
        np.testing.assert_allclose(joined, np.concatenate(pieces), atol=1e-5)
        # No span twice. Resumed from the same checkpoint, the second
        # extension reproduced the first to within 1e-4 nm; distinct frames
        # 0.1 ps apart differ by far more than 1e-3 nm somewhere.
        for later in range(1, len(joined)):
            for earlier in range(later):
                with self.subTest(frames=(earlier, later)):
                    self.assertGreater(np.abs(joined[later] - joined[earlier]).max(), 1e-3)

    def test_a_third_extension_would_go_on_from_the_second(self):
        from fastmdxplora.simulation.resume import extension_of

        plan = extension_of(self.root, more_ns=0.0002)
        self.assertTrue(plan.possible, plan.refusal)
        self.assertEqual(plan.config["simulation"]["resume_from"],
                         str(_checkpoint(self.root / "segment-002")))
        self.assertEqual(Path(plan.config["output"]).name, "segment-003")


if __name__ == "__main__":
    unittest.main()
