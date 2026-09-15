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


@unittest.skipUnless(HAVE_MDTRAJ, "MDTraj is not installed")
class TestTheRefusalSeesWhereARunPutsItsConfig(unittest.TestCase):
    """The two-studies check against the layout a run actually leaves.

    Every other test here hand-writes a config into the segment's
    simulation subdirectory. A run does not put it there: the orchestrator
    writes `resolved_config.yml` at the root of its output directory, and
    a segment's output directory is `segment-NNN/`. Looking only in
    `segment-NNN/simulation/` found nothing on every real run, every
    digest came back empty, and the refusal that stops two studies being
    concatenated -- the failure this module exists to prevent, and the one
    that most looks like success -- could not fire.

    It could not be caught by the fixtures above because they put the file
    where the reader was looking.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def segment(self, index, *, ph=7.0, frames=10):
        """A segment laid out the way a run leaves one.

        Complete enough to join, so that when the join is refused the
        refusal is the reason and not a missing file.
        """
        from fastmdxplora.config import write_resolved_config

        directory = self.root / f"segment-{index:03d}"
        simulation = directory / "simulation"
        simulation.mkdir(parents=True)
        topology = mdtraj.Topology()
        chain = topology.add_chain()
        residue = topology.add_residue("AR", chain)
        for atom in range(5):
            topology.add_atom(f"AR{atom}", mdtraj.element.argon, residue)
        xyz = np.random.default_rng(index).random(
            (frames, 5, 3)).astype(np.float32)
        trace = mdtraj.Trajectory(xyz, topology)
        trace[0].save_pdb(str(simulation / "topology.pdb"))
        trace.save_dcd(str(simulation / "production.dcd"))
        (simulation / "checkpoint.chk").write_bytes(b"x" * 10)
        (simulation / "checkpoint.chk.sha256").write_text("10 abc\n")
        options = {"setup": {"ph": ph},
                   "simulation": {"production_steps": 1000 * (index + 1)}}
        if index:
            options["simulation"].update(
                minimize=False, nvt_steps=0, npt_steps=0,
                resume_from=f"../segment-{index - 1:03d}/checkpoint.chk")
        write_resolved_config(
            {"system": "p.pdb", "output": str(directory), "options": options},
            directory)
        return directory

    def test_a_segment_is_recognised_as_a_study(self):
        self.segment(0)
        self.assertTrue(survey_segments(self.root)[0].config_digest)

    def test_segments_of_one_study_agree(self):
        """Steps, minimize, the NVT and NPT counts and resume_from all
        differ between segments by design, and a full resolved config
        names every one of them rather than leaving them out."""
        for index in range(3):
            self.segment(index)
        pieces = survey_segments(self.root)
        self.assertEqual(len({p.config_digest for p in pieces}), 1)

    def test_segments_of_one_study_join(self):
        """The other half of the claim: nothing here blocks a good join,
        so a refusal below is the digest and not a missing file."""
        for index in range(3):
            self.segment(index)
        record = join_segments(self.root, self.root / "joined.dcd")
        self.assertEqual(record["frames"], 30)

    def test_a_segment_from_another_study_refuses(self):
        for index in range(3):
            self.segment(index, ph=(7.0 if index < 2 else 6.0))
        with self.assertRaises(StudyError) as caught:
            join_segments(self.root, self.root / "joined.dcd")
        self.assertIn("same study", refusal_of(caught.exception).message)

    def test_a_config_in_the_simulation_directory_is_still_read(self):
        """A hand-assembled campaign tends to put it there."""
        directory = self.segment(0)
        (directory / "simulation" / "resolved_config.yml").write_text(
            (directory / "resolved_config.yml").read_text(encoding="utf-8"),
            encoding="utf-8")
        (directory / "resolved_config.yml").unlink()
        self.assertTrue(survey_segments(self.root)[0].config_digest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

    def test_the_record_says_where_the_joins_fall_in_frames(self):
        # The only thing the joined file cannot be asked for afterwards,
        # and the thing summarise_segments needs to split on. Without it
        # the join record is a list of segment numbers nothing can locate.
        for index in range(3):
            self.segment(index, frames=10)
        record = join_segments(self.root, self.root / "joined.dcd")
        self.assertEqual(record["joins"], [10, 20])

    def test_the_joins_can_be_handed_straight_to_the_statistics(self):
        import numpy as np

        from fastmdxplora.statistics import summarise_segments

        for index in range(4):
            self.segment(index, frames=400)
        record = join_segments(self.root, self.root / "joined.dcd")
        rng = np.random.default_rng(0)
        series = np.concatenate(
            [rng.normal(loc=i * 0.1, size=400) for i in range(4)])
        pooled, why = summarise_segments(series, record["joins"])
        self.assertIsNone(why)
        self.assertEqual(pooled.contributing, 4)


class TestJoiningOffersItself(unittest.TestCase):
    """The last step nobody remembers.

    A campaign leaves one directory per segment, which is right for crash
    safety, and then joining is a separate command that has to be run. So
    it is not, and six months later somebody analyses segment zero and
    calls it the run.
    """

    def setUp(self):
        from fastmdxplora.agent import Queue

        self.root = Path(tempfile.mkdtemp())
        self.queue = Queue(self.root / "queue.db")
        self.config = {
            "systems": [{"id": "a", "system": "1UBQ"}],
            "simulation": {"duration_ns": 40, "timestep_fs": 4},
        }

    def tearDown(self):
        self.queue.close()

    def run_campaign(self, **studies):
        from fastmdxplora.agent import study_runner, submit_study, work

        for study, segments in studies.items():
            submit_study(self.queue, "c", self.config, study=study,
                         segments=segments)
        return work(self.queue,
                    study_runner(self.root / "runs", queue=self.queue,
                                 explore=lambda **kw: {"ok": True}),
                    campaign="c")

    def test_the_worker_says_what_is_ready(self):
        report = self.run_campaign(segmented=4)
        self.assertEqual(report.ready_to_join, ["segmented"])

    def test_a_study_that_ran_whole_is_not_offered(self):
        # Its trajectory is already whole, and offering to join it would
        # teach a reader to ignore the offer.
        report = self.run_campaign(whole=1)
        self.assertEqual(report.ready_to_join, [])

    def test_a_campaign_still_running_offers_nothing(self):
        from fastmdxplora.agent import study_runner, submit_study, work

        submit_study(self.queue, "c", self.config, study="s", segments=4)
        report = work(self.queue,
                      study_runner(self.root / "runs", queue=self.queue,
                                   explore=lambda **kw: {"ok": True}),
                      campaign="c", max_jobs=2)
        self.assertEqual(report.ready_to_join, [])

    def test_an_abandoned_chain_is_not_offered(self):
        # Abandoned segments never ran, so the study is not finished and
        # joining it would produce a trajectory that stops early with
        # nothing saying so.
        from fastmdxplora.agent import study_runner, submit_study, work

        submit_study(self.queue, "c", self.config, study="s", segments=6)
        work(self.queue,
             study_runner(self.root / "runs", queue=self.queue,
                          explore=lambda **kw: {"ok": True}),
             campaign="c", max_jobs=2,
             watch=lambda job, result: "gone wrong" if job.segment == 1
             else None)
        from fastmdxplora.agent import finished_studies

        self.assertEqual(finished_studies(self.queue, "c"), [])

    def test_joining_a_campaign_skips_what_is_already_whole(self):
        from fastmdxplora.agent import join_finished

        self.run_campaign(segmented=3, whole=1)
        outcome = join_finished(self.queue, "c", self.root / "runs")
        self.assertEqual(outcome["already_whole"], ["whole"])

    def test_one_study_refusing_does_not_stop_the_others(self):
        # One study's problem says nothing about the next one's, and a
        # campaign of forty candidates should not become however many came
        # before the first awkward one.
        from fastmdxplora.agent import join_finished

        self.run_campaign(a=3, b=3)
        outcome = join_finished(self.queue, "c", self.root / "runs")
        # The stub wrote no trajectories, so both refuse -- and both are
        # reported, rather than the first ending the loop.
        self.assertEqual(sorted(outcome["refused"]), ["a", "b"])
        for record in outcome["refused"].values():
            self.assertIn("code", record)
