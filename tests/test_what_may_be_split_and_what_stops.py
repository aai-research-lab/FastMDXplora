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
