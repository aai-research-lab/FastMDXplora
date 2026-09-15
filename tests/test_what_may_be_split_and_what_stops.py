"""What may be split, and what happens to the rest when it should not run.

Two pieces. The first is a scientific gate: not every study may be
stopped and picked up, and the ones that may not are the ones where
splitting produces an answer that is wrong without looking wrong. The
second is the worker, whose only real decisions are about stopping.

The gate matters more than it looks. Splitting a metadynamics run does not
crash, does not warn, and does not produce a suspicious number. It
produces a free energy surface, smooth and plottable, with the wrong depth
-- because the second piece starts from zero bias in a well the first
piece already filled. Nothing downstream can tell. So the refusal has to
come before the first segment runs, and that is what is asserted here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastmdxplora.agent import Queue, work
from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.simulation.resume import (
    require_segmentable,
    resume_provenance,
    segmentability,
)


class TestWhatMayBeSplit(unittest.TestCase):

    def test_an_unbiased_run_may_be(self):
        verdict = segmentability({"simulation": {"duration_ns": 100}})
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.method, "unbiased")

    def test_an_umbrella_window_may_be(self):
        # Its restraint is a function of the collective variable and not of
        # time, so a window that stops at 4 ns and continues is doing what
        # it was doing before.
        verdict = segmentability({"simulation": {"umbrella": {"centres": [1]}}})
        self.assertTrue(verdict.allowed)

    def test_metadynamics_may_not(self):
        verdict = segmentability({"simulation": {"metadynamics": {"sigma": .1}}})
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "simulation.resume.bias_not_carried")
        self.assertIn("zero bias", verdict.reason)

    def test_a_steered_pull_may_not(self):
        verdict = segmentability({"simulation": {"steered": {"to": 3.0}}})
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code,
                         "simulation.resume.time_dependent_bias")

    def test_a_hand_written_plumed_script_may_not(self):
        # What state the script keeps is not something this software can
        # read, and guessing in the permissive direction is the expensive
        # way to be wrong.
        verdict = segmentability({"simulation": {"plumed": {"script": "p.dat"}}})
        self.assertFalse(verdict.allowed)

    def test_the_verdict_explains_itself_when_the_answer_is_yes(self):
        # A verdict that only explains itself when it says no teaches a
        # reader to skip it.
        self.assertTrue(segmentability({"simulation": {}}).reason)

    def test_one_piece_is_never_a_split(self):
        # A metadynamics study run through in one go is fine, and refusing
        # it would refuse the method rather than the splitting.
        config = {"simulation": {"metadynamics": {"sigma": 0.1}}}
        require_segmentable(config, segments=1)

    def test_asking_to_split_one_raises(self):
        config = {"simulation": {"metadynamics": {"sigma": 0.1}}}
        with self.assertRaises(StudyError) as caught:
            require_segmentable(config, segments=10)
        self.assertEqual(refusal_of(caught.exception).code,
                         "simulation.resume.bias_not_carried")


class TestTheJoinsAreRecorded(unittest.TestCase):
    """A trajectory assembled from pieces is not one that ran through."""

    def test_a_first_segment_has_no_join(self):
        record = resume_provenance(None, segment=0, of_segments=5,
                                   from_step=0)
        self.assertTrue(record["ran_through"])
        self.assertEqual(record["joins"], [])

    def test_joins_accumulate(self):
        first = resume_provenance(None, segment=1, of_segments=3,
                                  from_step=1000)
        second = resume_provenance(first, segment=2, of_segments=3,
                                   from_step=2000)
        self.assertEqual(len(second["joins"]), 2)
        self.assertFalse(second["ran_through"])
        self.assertEqual(second["joins"][0]["from_step"], 1000)

    def test_ran_through_is_stated_not_derived(self):
        # The thing an analysis wants to test is "did this run through",
        # and that should not require arithmetic on a list length.
        record = resume_provenance(None, segment=2, of_segments=3,
                                   from_step=2000)
        self.assertIn("ran_through", record)
        self.assertFalse(record["ran_through"])


class TestTheQueueConsultsTheGate(unittest.TestCase):

    def setUp(self):
        self.queue = Queue(Path(tempfile.mkdtemp()) / "queue.db")

    def tearDown(self):
        self.queue.close()

    def test_an_unbiased_study_may_be_submitted_in_segments(self):
        ids = self.queue.submit(
            "c", "simulate",
            {"config": {"simulation": {"duration_ns": 100}}},
            estimate_s=3600, segments=10)
        self.assertEqual(len(ids), 10)

    def test_a_metadynamics_study_may_not(self):
        # Refused at submission, before the first segment's hours are
        # spent. Discovering it at the join is discovering it too late.
        with self.assertRaises(StudyError):
            self.queue.submit(
                "c", "simulate",
                {"config": {"simulation": {"metadynamics": {"sigma": .1}}}},
                estimate_s=3600, segments=10)

    def test_but_it_may_be_submitted_whole(self):
        ids = self.queue.submit(
            "c", "simulate",
            {"config": {"simulation": {"metadynamics": {"sigma": .1}}}},
            estimate_s=3600)
        self.assertEqual(len(ids), 1)


class TestTheWorkerTakesWork(unittest.TestCase):

    def setUp(self):
        self.queue = Queue(Path(tempfile.mkdtemp()) / "queue.db")
        self.queue.set_budget("c", hours=40)

    def tearDown(self):
        self.queue.close()

    def test_it_runs_the_line_down(self):
        self.queue.submit("c", "simulate", {"system": "a"},
                          estimate_s=3600, segments=4)
        report = work(self.queue, lambda job: {"ok": True}, campaign="c")
        self.assertEqual(report.finished, 4)
        self.assertEqual(report.stopped_because, "the line is empty")

    def test_a_watcher_can_end_a_chain(self):
        # The whole value of segmenting on one card. The judgement is the
        # caller's, because what makes a run not worth continuing is a
        # question about the science.
        self.queue.submit("c", "simulate", {"system": "a"},
                          estimate_s=3600, segments=10)
        drift = iter([0.4, 0.8, 1.7] + [0.2] * 10)

        def watch(job, result):
            return ("the binder has left the epitope"
                    if result["rmsd"] > 1.5 else None)

        report = work(self.queue, lambda job: {"rmsd": next(drift)},
                      campaign="c", watch=watch)
        self.assertEqual(report.finished, 3)
        self.assertEqual(report.abandoned, 7)

    def test_a_refusal_does_not_stop_the_worker(self):
        # One study refusing says nothing about the next. A worker that
        # stopped on the first would turn a campaign of forty candidates
        # into however many came before the first awkward structure.
        self.queue.submit("c", "simulate", {"system": "a"})
        self.queue.submit("c", "simulate", {"system": "b"})
        self.queue.submit("c", "simulate", {"system": "c"})
        seen: list[str] = []

        def run(job):
            seen.append(job.payload["system"])
            if job.payload["system"] == "b":
                raise ValueError("this one blew up")
            return {"ok": True}

        report = work(self.queue, run, campaign="c")
        self.assertEqual(seen, ["a", "b", "c"])
        self.assertEqual(report.finished, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.refusals[0].message, "this one blew up")

    def test_a_refusal_abandons_what_waited_on_it(self):
        self.queue.submit("c", "simulate", {"system": "a"},
                          estimate_s=3600, segments=5)

        def run(job):
            raise ValueError("blew up in the first segment")

        work(self.queue, run, campaign="c")
        statuses = {j.status for j in self.queue.jobs("c")}
        self.assertEqual(statuses, {"failed", "abandoned"})

    def test_the_cap_holds(self):
        self.queue.submit("c", "simulate", {"system": "a"},
                          estimate_s=3600, segments=10)
        report = work(self.queue, lambda job: {}, campaign="c", max_jobs=3)
        self.assertEqual(report.ran, 3)
        self.assertIn("cap", report.stopped_because)

    def test_it_says_when_the_budget_was_the_problem(self):
        # The case a bare "nothing to do" gets most wrong. The line is
        # empty and the budget is full, and telling somebody the line is
        # empty sends them looking for a job they already submitted.
        self.queue.set_budget("small", hours=1)
        self.queue.submit("small", "simulate", {"system": "a"},
                          estimate_s=5 * 3600)
        report = work(self.queue, lambda job: {}, campaign="small")
        self.assertEqual(report.ran, 0)
        self.assertIn("budget", report.stopped_because)

    def test_an_interrupt_is_recorded_before_it_propagates(self):
        # A job left RUNNING would hold its estimate against the campaign's
        # allowance for good, so the record has to happen even on the way
        # out.
        self.queue.submit("c", "simulate", {"system": "a"}, estimate_s=3600)

        def run(job):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            work(self.queue, run, campaign="c")
        job = next(iter(self.queue.jobs("c")))
        self.assertEqual(job.status, "failed")

    def test_the_report_is_serialisable(self):
        import json
        self.queue.submit("c", "simulate", {"system": "a"})
        report = work(self.queue, lambda job: {"ok": True}, campaign="c")
        json.dumps(report.as_record())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestThePiecesAddUpToTheStudy(unittest.TestCase):
    """Three ways a split silently produces a different experiment."""

    def setUp(self):
        from fastmdxplora.simulation.resume import plan_segments
        self.plan = plan_segments
        self.config = {
            "systems": [{"id": "a", "system": "1UBQ"}],
            "setup": {"ph": 7.4},
            "simulation": {"duration_ns": 100, "timestep_fs": 4},
        }

    def test_the_steps_sum_to_the_whole_study(self):
        # Integer division leaves a remainder, and dropping it quietly
        # shortens the run: three segments of a million and one steps is
        # not three lots of 333,333.
        for segments in (1, 2, 3, 7, 10, 13):
            with self.subTest(segments=segments):
                pieces = self.plan(self.config, segments=segments)
                self.assertEqual(sum(p.steps for p in pieces),
                                 int(100 * 1e6 / 4))

    def test_only_the_first_segment_equilibrates(self):
        # A segment that re-equilibrated would throw away the production it
        # was meant to continue, and the joined trajectory would hold a
        # settling transient in the middle of a production run.
        pieces = self.plan(self.config, segments=4)
        self.assertNotIn("minimize", pieces[0].config["simulation"])
        for piece in pieces[1:]:
            self.assertIs(piece.config["simulation"]["minimize"], False)
            self.assertEqual(piece.config["simulation"]["nvt_steps"], 0)
            # Zero again, and safely so: the barostat no longer rides on
            # this number. `ensemble` carries it, which is what
            # TestASegmentRunsTheSameStudy checks.
            self.assertEqual(piece.config["simulation"]["npt_steps"], 0)

    def test_every_segment_after_the_first_names_its_predecessor(self):
        # Without this the pieces are not segments. They are ten
        # independent runs of a tenth the length, which is a different and
        # much worse experiment that no output would distinguish from the
        # intended one.
        pieces = self.plan(self.config, segments=3,
                           output_dir_for=lambda i: f"seg{i}")
        self.assertIsNone(pieces[0].resume_from)
        self.assertEqual(pieces[1].resume_from, "seg0/checkpoint.chk")
        self.assertEqual(pieces[2].resume_from, "seg1/checkpoint.chk")

    def test_a_duration_is_replaced_by_the_count_it_decided(self):
        # Leaving both would leave which one wins to the reader.
        pieces = self.plan(self.config, segments=3)
        for piece in pieces:
            self.assertNotIn("duration_ns", piece.config["simulation"])
            self.assertIn("production_steps", piece.config["simulation"])

    def test_one_segment_is_the_study_unchanged(self):
        [only] = self.plan(self.config, segments=1)
        self.assertIsNone(only.resume_from)
        self.assertEqual(only.steps, int(100 * 1e6 / 4))

    def test_a_study_that_may_not_be_split_is_refused_here_too(self):
        biased = dict(self.config)
        biased["simulation"] = {"metadynamics": {"sigma": 0.1}}
        with self.assertRaises(StudyError):
            self.plan(biased, segments=4)

    def test_the_segments_are_whole_configs(self):
        # A segment goes through validate_config like any other study. It
        # is an ordinary config that happens to start somewhere.
        from fastmdxplora.config.loader import validate_config
        for piece in self.plan(self.config, segments=3):
            validate_config(piece.config)


class TestASegmentedStudyRunsEndToEnd(unittest.TestCase):
    """Queue to orchestrator, with the bookkeeping neither side can hold."""

    def setUp(self):
        from fastmdxplora.agent import study_runner, submit_study
        self.root = Path(tempfile.mkdtemp())
        self.queue = Queue(self.root / "queue.db")
        self.submit, self.runner_for = submit_study, study_runner
        self.config = {
            "systems": [{"id": "a", "system": "1UBQ"}],
            "simulation": {"duration_ns": 40, "timestep_fs": 4},
        }
        self.seen: list[dict] = []

    def tearDown(self):
        self.queue.close()

    def explore(self, *, config, output_dir):
        self.seen.append(dict(config["simulation"]))
        return {"interface_rmsd_nm": 0.3}

    def run_it(self, segments=4, watch=None):
        self.submit(self.queue, "c", self.config, study="s", segments=segments)
        return work(self.queue,
                    self.runner_for(self.root / "runs", queue=self.queue,
                                    explore=self.explore),
                    campaign="c", watch=watch)

    def test_each_segment_runs_once_and_in_order(self):
        report = self.run_it(segments=4)
        self.assertEqual(report.finished, 4)
        self.assertEqual(len(self.seen), 4)

    def test_only_the_first_minimises(self):
        self.run_it(segments=4)
        self.assertNotIn("minimize", self.seen[0])
        self.assertTrue(all(s["minimize"] is False for s in self.seen[1:]))

    def test_each_segment_points_at_the_one_before(self):
        # Without this they are four independent runs of a quarter the
        # length, and no output would distinguish that from the run that
        # was asked for.
        self.run_it(segments=4)
        self.assertIsNone(self.seen[0].get("resume_from"))
        for index, block in enumerate(self.seen[1:], start=1):
            with self.subTest(segment=index):
                self.assertIn(f"segment-{index - 1:03d}", block["resume_from"])
                self.assertTrue(block["resume_from"].endswith("checkpoint.chk"))

    def test_the_resume_path_is_absolute(self):
        # The plan is written before anyone knows which directory the
        # campaign lands in, and a relative path resolved against the
        # working directory is a different file depending on where the
        # worker was started.
        self.run_it(segments=2)
        self.assertTrue(Path(self.seen[1]["resume_from"]).is_absolute())

    def test_the_joins_accumulate_across_segments(self):
        # A job's payload is fixed when it is submitted, so segment four
        # cannot be told at submission what segments one to three did.
        # Without reading the one before, a finished run says it was
        # joined once when it was joined three times.
        self.run_it(segments=4)
        done = list(self.queue.jobs("c", status="done"))
        provenance = done[-1].result["provenance"]
        self.assertEqual(len(provenance["joins"]), 3)
        self.assertFalse(provenance["ran_through"])

    def test_an_unsegmented_study_says_it_ran_through(self):
        self.run_it(segments=1)
        done = list(self.queue.jobs("c", status="done"))
        self.assertTrue(done[-1].result["provenance"]["ran_through"])

    def test_each_segment_writes_to_its_own_directory(self):
        # Appending into one would leave a crashed segment's half-written
        # trajectory in the middle of the run's own output, with no way to
        # tell which frames were good.
        self.run_it(segments=3)
        dirs = {j.result["output_dir"] for j in self.queue.jobs("c", status="done")}
        self.assertEqual(len(dirs), 3)

    def test_the_study_s_own_numbers_reach_the_watcher(self):
        got: list[dict] = []

        def watch(job, result):
            got.append(result)
            return None

        self.run_it(segments=2, watch=watch)
        self.assertEqual(got[0]["interface_rmsd_nm"], 0.3)

    def test_a_study_that_may_not_be_split_refuses_at_submission(self):
        self.config["simulation"] = {"metadynamics": {"sigma": 0.1}}
        with self.assertRaises(StudyError):
            self.submit(self.queue, "c", self.config, study="s", segments=4)


class TestASegmentRunsTheSameStudy(unittest.TestCase):
    """The ensemble must survive the split, and now it is stated.

    `npt_steps` used to answer two questions: how long to equilibrate, and
    whether a barostat existed at all. Zeroing it for a resumed segment
    therefore removed the barostat from production, so segment 0 ran NPT
    and the rest ran NVT — found on ubiquitin, by a warning that fired on
    segments 1 to 3 and not on segment 0.

    The first fix kept one token NPT step to re-establish the barostat,
    which worked and left the conflation in place. The two questions are
    separate settings now, so a segment can say `ensemble` outright and
    zero both stages: no equilibration, and the ensemble the study runs in.
    These assertions moved with it — they were written against the token
    step, which was the workaround rather than the answer.
    """

    def setUp(self):
        from fastmdxplora.simulation.resume import plan_segments

        self.plan = plan_segments
        self.base = {"systems": [{"id": "a", "system": "x.pdb"}]}

    def segments(self, simulation, count=3):
        return self.plan({**self.base, "simulation": simulation},
                         segments=count)

    def test_a_constant_pressure_study_keeps_its_barostat(self):
        for label, block in (
                ("unstated", {"duration_ns": 4}),
                ("explicit", {"duration_ns": 4, "npt_steps": 50_000}),
                ("by duration", {"duration_ns": 4, "npt_duration_ns": 1})):
            with self.subTest(study=label):
                for piece in self.segments(block)[1:]:
                    self.assertEqual(
                        piece.config["simulation"]["ensemble"], "npt",
                        "a resumed segment lost the barostat")

    def test_a_constant_volume_study_stays_at_constant_volume(self):
        # The other direction matters as much. Adding a barostat to a study
        # that deliberately had none would also be two ensembles in one
        # trajectory.
        for piece in self.segments({"duration_ns": 4, "npt_steps": 0})[1:]:
            self.assertEqual(piece.config["simulation"]["ensemble"], "nvt")

    def test_equilibration_happens_once_and_costs_nothing_after(self):
        # Both stages zero, because the ensemble no longer rides on them.
        # The token step the first fix needed is gone.
        pieces = self.segments({"duration_ns": 4, "npt_steps": 50_000})
        self.assertNotIn("nvt_steps", pieces[0].config["simulation"])
        for piece in pieces[1:]:
            self.assertEqual(piece.config["simulation"]["nvt_steps"], 0)
            self.assertEqual(piece.config["simulation"]["npt_steps"], 0)

    def test_an_npt_equilibration_then_nvt_study_splits_faithfully(self):
        # The combination that could not be expressed at all before.
        for piece in self.segments(
                {"duration_ns": 4, "npt_steps": 50_000,
                 "ensemble": "nvt"})[1:]:
            self.assertEqual(piece.config["simulation"]["ensemble"], "nvt")

    def test_the_production_steps_still_sum(self):
        pieces = self.segments({"production_steps": 1_000_000}, count=4)
        self.assertEqual(
            sum(p.config["simulation"]["production_steps"] for p in pieces),
            1_000_000)
