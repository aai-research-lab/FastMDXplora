"""Joining a segmented run, and the three ways it should not.

Concatenation is four lines. The refusals are the module.

The one that matters most is a gap, because it is the failure that most
looks like success: segments two and four concatenate perfectly with
three missing, and the result is not a shorter trajectory. It is a
trajectory with a jump in the middle that every analysis reads straight
through. Equilibration detection finds a transient that is really a
discontinuity; a correlation time computed across it means nothing.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from fastmdxplora.analysis.joining import join_segments, survey_segments
from fastmdxplora.refusals import MissingResultError, StudyError, refusal_of

try:
    import mdtraj

    HAVE_MDTRAJ = True
except ImportError:  # pragma: no cover
    HAVE_MDTRAJ = False


@unittest.skipUnless(HAVE_MDTRAJ, "MDTraj is not installed")
class TestJoiningRefusesWhatItShould(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def segment(self, index, *, frames=10, finished=True, study="A",
                trajectory=True):
        directory = self.root / f"segment-{index:03d}" / "simulation"
        directory.mkdir(parents=True)
        topology = mdtraj.Topology()
        chain = topology.add_chain()
        residue = topology.add_residue("AR", chain)
        for atom in range(5):
            topology.add_atom(f"AR{atom}", mdtraj.element.argon, residue)
        xyz = np.random.default_rng(index).random((frames, 5, 3)).astype(np.float32)
        trace = mdtraj.Trajectory(xyz, topology)
        trace[0].save_pdb(str(directory / "topology.pdb"))
        if trajectory:
            trace.save_dcd(str(directory / "production.dcd"))
        if finished:
            (directory / "checkpoint.chk").write_bytes(b"x" * 10)
            (directory / "checkpoint.chk.sha256").write_text("10 abc\n")
        (directory / "resolved_config.yml").write_text(
            f"setup:\n  ph: 7.{0 if study == 'A' else 5}\n"
            f"systems:\n- id: a\n"
            f"simulation:\n  production_steps: {1000 * (index + 1)}\n"
            f"  minimize: false\n")

    def test_a_clean_run_joins(self):
        for index in range(3):
            self.segment(index)
        record = join_segments(self.root, self.root / "joined.dcd")
        self.assertEqual(record["frames"], 30)
        self.assertEqual(record["segments"], [0, 1, 2])
        self.assertFalse(record["ran_through"])

    def test_the_join_records_itself_beside_the_output(self):
        # A joined trajectory is derived, and a reader should see that
        # without opening the file or inferring it from a directory name.
        for index in range(2):
            self.segment(index)
        join_segments(self.root, self.root / "joined.dcd")
        self.assertTrue((self.root / "joined.dcd.join.json").is_file())

    def test_a_gap_refuses(self):
        for index in (0, 1, 3):
            self.segment(index)
        with self.assertRaises(StudyError) as caught:
            join_segments(self.root, self.root / "joined.dcd")
        self.assertIn("2", refusal_of(caught.exception).message)

    def test_a_run_that_does_not_start_at_zero_refuses(self):
        for index in (1, 2):
            self.segment(index)
        with self.assertRaises(StudyError):
            join_segments(self.root, self.root / "joined.dcd")

    def test_an_unfinished_segment_refuses(self):
        # No sealed checkpoint means the process died partway, and the
        # trajectory ends wherever it died with nothing in the file to
        # say so.
        for index in range(3):
            self.segment(index, finished=(index != 1))
        with self.assertRaises(MissingResultError) as caught:
            join_segments(self.root, self.root / "joined.dcd")
        self.assertEqual(refusal_of(caught.exception).code,
                         "simulation.resume.unsealed")

    def test_segments_from_two_studies_refuse(self):
        # Two runs of the same length under different settings leave
        # directories that look alike and concatenate without complaint.
        for index in range(3):
            self.segment(index, study=("A" if index < 2 else "B"))
        with self.assertRaises(StudyError) as caught:
            join_segments(self.root, self.root / "joined.dcd")
        self.assertIn("same study", refusal_of(caught.exception).message)

    def test_settings_that_vary_by_design_do_not_look_like_two_studies(self):
        # Production steps, minimize and resume_from differ between
        # segments on purpose. Hashing the resolved config whole would say
        # every segment came from a different study, which is backwards.
        for index in range(3):
            self.segment(index)
        pieces = survey_segments(self.root)
        self.assertEqual(len({p.config_digest for p in pieces}), 1)

    def test_a_finished_segment_with_no_frames_refuses(self):
        for index in range(3):
            self.segment(index, trajectory=(index != 2))
        with self.assertRaises(MissingResultError):
            join_segments(self.root, self.root / "joined.dcd")

    def test_nothing_to_join_says_so(self):
        with self.assertRaises(MissingResultError) as caught:
            join_segments(self.root, self.root / "joined.dcd")
        self.assertIn("single piece", refusal_of(caught.exception).message)

    def test_surveying_does_not_require_a_joinable_run(self):
        # Somebody coming back to a campaign that stopped overnight wants
        # to see the state before committing to producing a file from it.
        for index in (0, 2):
            self.segment(index, finished=(index == 0))
        pieces = survey_segments(self.root)
        self.assertEqual([p.index for p in pieces], [0, 2])
        self.assertTrue(pieces[0].usable)
        self.assertFalse(pieces[1].usable)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
