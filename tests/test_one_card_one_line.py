"""One card, one line, and a number nobody talked their way past.

Two pieces tested together because they only make sense together: an
estimate with no queue to spend it in is trivia, and a queue with no
estimate cannot enforce a budget.

The assertions worth reading are the negative ones. A cost model that
guesses when it has not measured is worse than none, because a schedule
built on somebody else's GPU looks reasonable and is wrong by an order of
magnitude. A budget a caller can exceed is not a budget.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

try:  # pragma: no cover - the skip is the point
    import openmm  # noqa: F401

    HAS_OPENMM = True
except ImportError:  # pragma: no cover
    HAS_OPENMM = False

from fastmdxplora.agent import Queue
from fastmdxplora.cost import (
    Calibration,
    calibrate,
    estimate_seconds,
    estimate_study,
    load_calibration,
    total_steps,
)
from fastmdxplora.refusals import StudyError, refusal_of


class TestTheMachineIsMeasuredNotAssumed(unittest.TestCase):

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "calibration.json"

    def test_it_refuses_before_it_has_been_measured(self):
        # The whole point. A default constant would be a number from
        # somebody else's card, and the failure it produces is the quiet
        # kind: the schedule looks reasonable and is wrong by an order of
        # magnitude.
        with self.assertRaises(StudyError) as caught:
            estimate_seconds(particles=50_000, steps=1_000, path=self.path)
        self.assertEqual(refusal_of(caught.exception).code,
                         "environment.calibration.absent")

    def test_a_measurement_gives_a_constant_that_scales(self):
        calibrate(particles=30_000, steps=5_000, seconds=42.0,
                  platform_name="CUDA", precision="mixed", path=self.path)
        small = estimate_seconds(particles=30_000, steps=5_000,
                                 platform_name="CUDA", precision="mixed",
                                 path=self.path)
        self.assertAlmostEqual(small.seconds, 42.0, places=6)

        # Twice the particles and twice the steps is four times the work.
        big = estimate_seconds(particles=60_000, steps=10_000,
                               platform_name="CUDA", precision="mixed",
                               path=self.path)
        self.assertAlmostEqual(big.seconds, 42.0 * 4, places=6)

    def test_a_measurement_from_another_machine_is_refused(self):
        calibrate(particles=30_000, steps=5_000, seconds=42.0,
                  platform_name="CUDA", precision="mixed", path=self.path)
        with self.assertRaises(StudyError) as caught:
            estimate_seconds(particles=30_000, steps=5_000,
                             platform_name="CPU", precision="mixed",
                             path=self.path)
        self.assertEqual(refusal_of(caught.exception).code,
                         "environment.calibration.stale")

    def test_changing_precision_invalidates_it_too(self):
        # Double precision is several times slower than mixed on the same
        # card. Reusing the constant would understate the run badly, and
        # understating is the direction that costs somebody a weekend.
        calibrate(particles=30_000, steps=5_000, seconds=42.0,
                  platform_name="CUDA", precision="mixed", path=self.path)
        with self.assertRaises(StudyError):
            estimate_seconds(particles=30_000, steps=5_000,
                             platform_name="CUDA", precision="double",
                             path=self.path)

    def test_an_unknown_particle_count_is_refused(self):
        # Settled when the system is solvated, so an estimate before setup
        # has nothing to rest on. Inventing one from residue count would be
        # inventing the water, which is most of the atoms.
        with self.assertRaises(StudyError) as caught:
            estimate_seconds(particles=0, steps=1_000, path=self.path)
        self.assertEqual(refusal_of(caught.exception).code,
                         "setup.structure.undetermined")

    def test_a_run_of_no_duration_cannot_be_divided_out_of(self):
        with self.assertRaises(StudyError):
            calibrate(particles=30_000, steps=5_000, seconds=0.0,
                      path=self.path, save=False)

    def test_it_survives_a_round_trip_to_disk(self):
        made = calibrate(particles=30_000, steps=5_000, seconds=42.0,
                         platform_name="CUDA", path=self.path)
        read = load_calibration(self.path)
        self.assertEqual(read.seconds_per_particle_step,
                         made.seconds_per_particle_step)

    def test_a_record_from_another_version_is_treated_as_absent(self):
        # Repaired would be worse. A missing field guessed at puts an
        # unmeasured number into a planning decision, and a calibration is
        # cheap to retake.
        self.path.write_text('{"seconds_per_particle_step": 1e-7}',
                             encoding="utf-8")
        self.assertIsNone(load_calibration(self.path))


class TestTheStepCountIsTheWholeRun(unittest.TestCase):

    def test_equilibration_is_counted(self):
        # Counting production alone understates a short study badly: the
        # default equilibration is 750,000 steps, which is most of the work
        # in anything under a couple of nanoseconds.
        self.assertEqual(total_steps({"production_steps": 1_000}), 751_000)

    def test_a_duration_is_converted_at_the_study_s_own_timestep(self):
        # 10 ns at 4 fs is half the steps of 10 ns at 2 fs, and a study
        # using hydrogen mass repartitioning is the common case.
        at_four = total_steps({"duration_ns": 10, "timestep_fs": 4,
                               "nvt_steps": 0, "npt_steps": 0})
        at_two = total_steps({"duration_ns": 10, "timestep_fs": 2,
                              "nvt_steps": 0, "npt_steps": 0})
        self.assertEqual(at_four * 2, at_two)

    def test_an_explicit_step_count_wins_over_a_duration(self):
        steps = total_steps({"duration_ns": 100, "production_steps": 500,
                             "nvt_steps": 0, "npt_steps": 0})
        self.assertEqual(steps, 500)


class TestTheLineHoldsTheWork(unittest.TestCase):

    def setUp(self):
        self.queue = Queue(Path(tempfile.mkdtemp()) / "queue.db")
        self.queue.set_budget("campaign", hours=40)

    def tearDown(self):
        self.queue.close()

    def test_only_the_first_segment_is_ready(self):
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=3600, segments=5)
        statuses = [j.status for j in self.queue.jobs("campaign")]
        self.assertEqual(statuses, ["ready", "blocked", "blocked",
                                    "blocked", "blocked"])

    def test_finishing_one_releases_the_next(self):
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=3600, segments=3)
        first = self.queue.claim("campaign")
        self.queue.finish(first.id, seconds=1200)
        second = self.queue.claim("campaign")
        self.assertIsNotNone(second)
        self.assertEqual(second.segment, 1)

    def test_abandoning_drops_the_rest_of_the_chain(self):
        # The point of segmenting. A trajectory whose interface has already
        # come apart does not need the other eighty nanoseconds, and on one
        # card those hours are another candidate.
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=10 * 3600, segments=10)
        job = self.queue.claim("campaign")
        dropped = self.queue.abandon(job.id, "Interface RMSD past 1.5 nm.")
        self.assertEqual(dropped, 10)
        self.assertIsNone(self.queue.claim("campaign"))

    def test_an_abandoned_job_stops_holding_budget(self):
        # It was RUNNING when abandoned. Leaving it so would hold its
        # estimate against the allowance for good, and a campaign would
        # slowly lose budget to runs nobody is doing.
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=20 * 3600, segments=2)
        job = self.queue.claim("campaign")
        self.queue.abandon(job.id, "gone wrong")
        self.assertAlmostEqual(
            self.queue.budget("campaign").remaining_s, 40 * 3600, places=3)

    def test_a_failure_abandons_what_waited_on_it(self):
        # Abandoned, not failed. Those segments never ran and nothing is
        # wrong with them; marking them failed would put six failures in a
        # report where there was one.
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=3600, segments=4)
        job = self.queue.claim("campaign")
        self.queue.fail(job.id, ValueError("blew up"), seconds=60)
        statuses = {j.status for j in self.queue.jobs("campaign")}
        self.assertEqual(statuses, {"failed", "abandoned"})
        self.assertEqual(
            sum(1 for j in self.queue.jobs("campaign", status="failed")), 1)

    def test_a_refusal_survives_the_database(self):
        self.queue.submit("campaign", "simulate", {"system": "a"})
        job = self.queue.claim("campaign")
        self.queue.fail(job.id, ValueError("blew up"))
        read = self.queue.job(job.id)
        self.assertEqual(read.refusal.message, "blew up")
        self.assertEqual(read.refusal.code, "unclassified")

    def test_the_line_survives_the_process(self):
        self.queue.submit("campaign", "simulate", {"system": "a"})
        path = self.queue.path
        self.queue.close()
        with Queue(path) as reopened:
            self.assertEqual(len(list(reopened.jobs("campaign"))), 1)


class TestTheBudgetIsArithmetic(unittest.TestCase):
    """Not a number in a prompt that a model is asked to respect."""

    def setUp(self):
        self.queue = Queue(Path(tempfile.mkdtemp()) / "queue.db")

    def tearDown(self):
        self.queue.close()

    def test_a_job_beyond_the_allowance_does_not_start(self):
        self.queue.set_budget("campaign", hours=10)
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=20 * 3600)
        self.assertIsNone(self.queue.claim("campaign"))

    def test_and_the_queue_records_why(self):
        # Said on the job rather than only to the worker, so a caller
        # reading the queue tomorrow sees why the campaign stopped.
        self.queue.set_budget("campaign", hours=10)
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=20 * 3600)
        self.queue.claim("campaign")
        failed = list(self.queue.jobs("campaign", status="failed"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].refusal.code,
                         "environment.budget.exhausted")
        self.assertIn("remaining_hours", failed[0].refusal.details)

    def test_spending_accumulates_against_it(self):
        self.queue.set_budget("campaign", hours=10)
        self.queue.submit("campaign", "simulate", {"system": "a"},
                          estimate_s=3600, segments=4)
        for _ in range(2):
            job = self.queue.claim("campaign")
            self.queue.finish(job.id, seconds=2 * 3600)
        self.assertAlmostEqual(
            self.queue.budget("campaign").remaining_s, 6 * 3600, places=3)

    def test_a_running_job_holds_its_estimate(self):
        # Otherwise two jobs each fitting the remainder could both start,
        # and the allowance would be exceeded by whichever finished second.
        self.queue.set_budget("campaign", hours=10)
        self.queue.submit("campaign", "simulate", {"a": 1}, estimate_s=6 * 3600)
        self.queue.submit("campaign", "simulate", {"b": 2}, estimate_s=6 * 3600)
        self.assertIsNotNone(self.queue.claim("campaign"))
        self.assertIsNone(self.queue.claim("campaign"))

    def test_no_budget_means_no_ceiling(self):
        # A caller who has not set one is not silently given a default.
        # Guessing an allowance would stop somebody's overnight run for a
        # reason they never chose.
        self.queue.submit("campaign", "simulate", {"a": 1},
                          estimate_s=1_000 * 3600)
        self.assertIsNotNone(self.queue.claim("campaign"))

    def test_a_negative_allowance_is_refused(self):
        with self.assertRaises(StudyError):
            self.queue.set_budget("campaign", hours=0)


class TestTheTwoWorkTogether(unittest.TestCase):

    def test_an_estimate_can_be_submitted_as_a_budget_claim(self):
        path = Path(tempfile.mkdtemp())
        calibrate(particles=30_000, steps=5_000, seconds=42.0,
                  platform_name="CUDA", precision="mixed",
                  path=path / "cal.json")
        estimate = estimate_study(
            {"simulation": {"duration_ns": 10, "timestep_fs": 4,
                            "nvt_steps": 0, "npt_steps": 0}},
            particles=60_000, platform_name="CUDA", precision="mixed",
            path=path / "cal.json")

        with Queue(path / "queue.db") as queue:
            queue.set_budget("campaign", hours=estimate.hours / 2)
            queue.submit("campaign", "simulate", {"system": "a"},
                         estimate_s=estimate.seconds)
            # Half the budget it needs, so it does not start, and it says
            # so before spending anything rather than after.
            self.assertIsNone(queue.claim("campaign"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


@pytest.mark.skipif(not HAS_OPENMM, reason="OpenMM is not installed")
class TestTheMachineCanMeasureItself(unittest.TestCase):
    """calibrate() takes a measurement; this one makes it.

    The difference decides whether the cost model gets used at all. A
    caller who has to produce a timed run before they can get an estimate
    will not bother, every estimate will refuse, and the refusal will look
    like the software being difficult rather than honest.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "calibration.json"

    def test_it_produces_a_usable_constant(self):
        from fastmdxplora.cost import estimate_seconds, measure_this_machine

        # Small: the point is that nobody is discouraged from running it.
        calibration = measure_this_machine(particles=500, steps=200,
                                           path=self.path)
        self.assertGreater(calibration.seconds_per_particle_step, 0.0)
        self.assertGreater(calibration.seconds, 0.0)

        estimate = estimate_seconds(
            particles=500, steps=200,
            platform_name=calibration.machine["platform"],
            precision="mixed", path=self.path)
        self.assertAlmostEqual(estimate.seconds, calibration.seconds, places=6)

    def test_it_records_the_platform_it_actually_ran_on(self):
        # Not the one that was asked for. A caller that asked for CUDA on a
        # machine without it got CPU, and a constant labelled CUDA would
        # then be reused for a real CUDA run and understate it enormously.
        from fastmdxplora.cost import measure_this_machine

        calibration = measure_this_machine(particles=300, steps=100,
                                           path=self.path)
        self.assertIn(calibration.machine["platform"],
                      ("CPU", "Reference", "CUDA", "OpenCL", "HIP"))
        self.assertNotEqual(calibration.machine["platform"], "unknown")

    def test_it_warms_up_before_timing(self):
        # The first steps pay for kernel compilation and buffer allocation.
        # Charging them to the constant would overstate every estimate
        # afterwards, on a GPU by a great deal. Asserted by source rather
        # than by timing, because a timing assertion on shared CI hardware
        # is a flake waiting to happen.
        import inspect

        from fastmdxplora import cost

        source = inspect.getsource(cost.measure_this_machine)
        self.assertIn("Warm up before timing", source)
        warmup = source.index("simulation.step(max(100")
        timed = source.index("started = _time.perf_counter()")
        self.assertLess(warmup, timed)
