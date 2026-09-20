"""The resume path, run rather than read.

The refusals around checkpoints were asserted by searching the source for
the strings that raise them, which shows the code is written and not that
it works. Two claims were resting on that and both are load-bearing:

  - that a run continued from a checkpoint is the run it continued, rather
    than something that merely starts from similar coordinates;
  - that a checkpoint from a different system is refused rather than
    quietly loaded, which would continue somebody's study from another
    system's state with nothing downstream looking wrong.

So these build a real ``System``, integrate it, write a real checkpoint
and load it back. Twenty argon atoms on the CPU platform: small enough to
run in a fraction of a second on any machine, real enough that OpenMM's
own validation is what answers.

Skipped where OpenMM is absent, because it is an optional backend and the
rest of the suite runs without it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from fastmdxplora.refusals import refusal_of

try:  # pragma: no cover - the skip is the point
    import openmm as mm
    import openmm.app as app
    from openmm import unit

    HAVE_OPENMM = True
except ImportError:  # pragma: no cover
    HAVE_OPENMM = False


def _a_small_system():
    """Twenty argon atoms. Real dynamics, no force field files."""
    system = mm.System()
    topology = app.Topology()
    chain = topology.addChain()
    residue = topology.addResidue("AR", chain)
    nonbonded = mm.NonbondedForce()
    nonbonded.setNonbondedMethod(mm.NonbondedForce.NoCutoff)
    argon = app.Element.getBySymbol("Ar")
    for index in range(20):
        system.addParticle(39.948 * unit.amu)
        topology.addAtom(f"AR{index}", argon, residue)
        nonbonded.addParticle(0.0, 0.34 * unit.nanometer,
                              0.996 * unit.kilojoule_per_mole)
    system.addForce(nonbonded)
    positions = [mm.Vec3(0.4 * (i % 5), 0.4 * ((i // 5) % 2), 0.4 * (i // 10))
                 for i in range(20)] * unit.nanometer
    return system, topology, positions


#: How far a resumed run may sit from a straight one after a thousand
#: steps, and the line the constant-pressure case has to cross.
#:
#: One number for both, because the pair is a contrast and two unrelated
#: tolerances would let it drift into meaninglessness. Measured on a
#: quiet machine with the thread count pinned: constant volume drifts
#: 8.3e-8 nm, constant pressure 0.92 nm. Seven orders of magnitude apart,
#: so anything between them draws the line and this sits a hundredfold
#: clear of the noise on one side and five orders clear of the signal on
#: the other.
#:
#: The 8.3e-8 is the checkpoint's own precision rather than chaos, and it
#: does not grow with load once the arithmetic is deterministic -- which
#: is what `_a_simulation` pins. Before that it was 1.2e-5 and rising
#: with whatever else the runner was doing.
_RESUME_DRIFT_NM = 1e-5


def _a_simulation(system, topology, positions):
    """One simulation, on one thread.

    The thread count is pinned because these tests compare two runs
    number for number. OpenMM's CPU platform splits force evaluation
    across as many threads as it finds cores, and the order a sum is
    accumulated in decides its last bit -- so two contexts built moments
    apart on a busy machine can take different thread counts and differ
    by an ulp before either has taken a step.

    An ulp would not matter if this were a measurement. It is dynamics:
    Langevin trajectories separate exponentially, so a difference in the
    sixteenth decimal reaches the fifth within a thousand steps. That is
    not a defect being caught, it is chaos being rediscovered, and it
    arrives as a test that passes on a quiet machine and fails on a
    loaded one.
    """
    integrator = mm.LangevinMiddleIntegrator(
        300 * unit.kelvin, 1 / unit.picosecond, 2 * unit.femtosecond)
    simulation = app.Simulation(topology, system, integrator,
                                mm.Platform.getPlatformByName("CPU"),
                                {"Threads": "1"})
    simulation.context.setPositions(positions)
    return simulation


@unittest.skipUnless(HAVE_OPENMM, "OpenMM is not installed")
class TestAResumedRunIsTheRunItContinued(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_resuming_reproduces_running_straight_through(self):
        # The claim the whole segmentation design rests on. If a resumed
        # run merely started from similar coordinates, ten segments would
        # not be one trajectory and nothing in the output would say so.
        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.minimizeEnergy(maxIterations=50)
        simulation.context.setVelocitiesToTemperature(300 * unit.kelvin, 1234)
        simulation.step(200)

        checkpoint = self.root / "checkpoint.chk"
        checkpoint.write_bytes(simulation.context.createCheckpoint())

        simulation.step(200)
        straight_through = simulation.context.getState(
            getPositions=True).getPositions(asNumpy=True)

        from fastmdxplora.simulation.runner import load_checkpoint

        system2, topology2, _ = _a_small_system()
        resumed = _a_simulation(system2, topology2, positions)
        load_checkpoint({}, resumed, checkpoint)
        resumed.step(200)
        after_resume = resumed.context.getState(
            getPositions=True).getPositions(asNumpy=True)

        np.testing.assert_allclose(
            straight_through.value_in_unit(unit.nanometer),
            after_resume.value_in_unit(unit.nanometer),
            atol=1e-6,
            err_msg="a resumed run diverged from the run it continued")

    def test_a_checkpoint_from_another_system_is_refused(self):
        # The hazard this refusal exists for: loading it quietly would
        # continue a study from another system's state, and nothing
        # downstream would look wrong.
        from fastmdxplora.refusals import UnstableRun
        from fastmdxplora.simulation.runner import load_checkpoint

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        checkpoint = self.root / "twenty.chk"
        checkpoint.write_bytes(simulation.context.createCheckpoint())

        smaller = mm.System()
        topology2 = app.Topology()
        chain = topology2.addChain()
        residue = topology2.addResidue("AR", chain)
        for index in range(5):
            smaller.addParticle(39.948 * unit.amu)
            topology2.addAtom(f"AR{index}",
                              app.Element.getBySymbol("Ar"), residue)
        other = _a_simulation(
            smaller, topology2,
            [mm.Vec3(0.4 * i, 0, 0) for i in range(5)] * unit.nanometer)

        with self.assertRaises(UnstableRun) as caught:
            load_checkpoint({}, other, checkpoint)
        refusal = refusal_of(caught.exception)
        self.assertEqual(refusal.code, "simulation.resume.checkpoint_rejected")
        # OpenMM's own reason travels with it. "Could not be loaded" alone
        # would send somebody looking at the disk; "wrong number of
        # particles" sends them to the system they built.
        self.assertIn("particles", refusal.message)

    def test_a_missing_checkpoint_names_the_phase_not_the_path(self):
        from fastmdxplora.refusals import MissingResultError
        from fastmdxplora.simulation.runner import load_checkpoint

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        with self.assertRaises(MissingResultError) as caught:
            load_checkpoint({}, simulation, self.root / "never-written.chk")
        refusal = refusal_of(caught.exception)
        self.assertEqual(refusal.code, "analysis.data.absent")
        self.assertIn("previous segment", refusal.message)

    def test_a_truncated_checkpoint_is_refused_rather_than_half_loaded(self):
        # OpenMM will not catch this. Measured: a checkpoint truncated to
        # half its length loads without complaint and gives the right
        # positions; truncated to a tenth it loads without complaint and
        # gives wrong ones. There is no length or checksum in the format,
        # so the only way to know a checkpoint is whole is to have written
        # down what whole meant.
        from fastmdxplora.refusals import UnstableRun
        from fastmdxplora.simulation.runner import (
            load_checkpoint, seal_checkpoint)

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        checkpoint = self.root / "truncated.chk"
        whole = simulation.context.createCheckpoint()
        checkpoint.write_bytes(whole)
        seal_checkpoint(checkpoint)
        checkpoint.write_bytes(whole[: len(whole) // 2])

        system2, topology2, _ = _a_small_system()
        other = _a_simulation(system2, topology2, positions)
        with self.assertRaises(UnstableRun) as caught:
            load_checkpoint({}, other, checkpoint)
        refusal = refusal_of(caught.exception)
        self.assertEqual(refusal.code,
                         "simulation.resume.checkpoint_truncated")
        self.assertEqual(refusal.details["expected"], len(whole))

    def test_openmm_really_does_load_a_truncated_one(self):
        # The premise the seal exists for, asserted rather than asserted
        # about. If a future OpenMM starts refusing these, the seal is no
        # longer load-bearing and this test is where that shows up.
        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        whole = simulation.context.createCheckpoint()

        system2, topology2, _ = _a_small_system()
        other = _a_simulation(system2, topology2, positions)
        other.context.loadCheckpoint(whole[: len(whole) // 2])

    def test_an_unsealed_checkpoint_is_refused_for_a_segment(self):
        # A segment's predecessor was written by this software and is
        # always sealed on a clean finish, so a missing seal means it was
        # killed mid-write.
        from fastmdxplora.refusals import MissingResultError
        from fastmdxplora.simulation.runner import load_checkpoint

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        checkpoint = self.root / "unsealed.chk"
        checkpoint.write_bytes(simulation.context.createCheckpoint())

        system2, topology2, _ = _a_small_system()
        other = _a_simulation(system2, topology2, positions)
        with self.assertRaises(MissingResultError) as caught:
            load_checkpoint({}, other, checkpoint, require_seal=True)
        self.assertEqual(refusal_of(caught.exception).code,
                         "simulation.resume.unsealed")

    def test_but_a_hand_made_checkpoint_is_not_refused_outside_a_segment(self):
        # Refusing it would be refusing a legitimate use over a convention
        # the person never agreed to.
        from fastmdxplora.simulation.runner import load_checkpoint

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        checkpoint = self.root / "by-hand.chk"
        checkpoint.write_bytes(simulation.context.createCheckpoint())

        system2, topology2, _ = _a_small_system()
        other = _a_simulation(system2, topology2, positions)
        load_checkpoint({}, other, checkpoint)

    def test_a_sealed_checkpoint_verifies(self):
        from fastmdxplora.simulation.runner import (
            seal_checkpoint, verify_checkpoint)

        system, topology, positions = _a_small_system()
        simulation = _a_simulation(system, topology, positions)
        simulation.step(10)
        checkpoint = self.root / "sealed.chk"
        checkpoint.write_bytes(simulation.context.createCheckpoint())
        seal_checkpoint(checkpoint)
        self.assertTrue(verify_checkpoint(checkpoint, require_seal=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def _a_periodic_system(n: int = 64, *, barostat: bool = False):
    """Argon in a periodic box, optionally at constant pressure."""
    system = mm.System()
    length = 2.0
    vectors = (mm.Vec3(length, 0, 0), mm.Vec3(0, length, 0),
               mm.Vec3(0, 0, length))
    system.setDefaultPeriodicBoxVectors(*[v * unit.nanometer for v in vectors])
    topology = app.Topology()
    topology.setUnitCellDimensions(
        mm.Vec3(length, length, length) * unit.nanometer)
    chain = topology.addChain()
    residue = topology.addResidue("AR", chain)
    nonbonded = mm.NonbondedForce()
    nonbonded.setNonbondedMethod(mm.NonbondedForce.CutoffPeriodic)
    nonbonded.setCutoffDistance(0.8 * unit.nanometer)
    argon = app.Element.getBySymbol("Ar")
    for index in range(n):
        system.addParticle(39.948 * unit.amu)
        topology.addAtom(f"AR{index}", argon, residue)
        nonbonded.addParticle(0.0, 0.34 * unit.nanometer,
                              0.996 * unit.kilojoule_per_mole)
    system.addForce(nonbonded)
    if barostat:
        system.addForce(
            mm.MonteCarloBarostat(1.0 * unit.bar, 300 * unit.kelvin, 5))
    grid = int(round(n ** (1 / 3))) + 1
    positions = [mm.Vec3(0.45 * (i % grid), 0.45 * ((i // grid) % grid),
                         0.45 * (i // grid ** 2)) for i in range(n)]
    return system, topology, positions * unit.nanometer


@unittest.skipUnless(HAVE_OPENMM, "OpenMM is not installed")
class TestWhatAJoinCostsUnderPressure(unittest.TestCase):
    """Measured, because the answer differs between NVT and NPT."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def _continue_and_resume(self, *, barostat):
        system, topology, positions = _a_periodic_system(barostat=barostat)
        first = _a_simulation(system, topology, positions)
        first.minimizeEnergy(maxIterations=50)
        first.context.setVelocitiesToTemperature(300 * unit.kelvin, 99)
        first.step(1000)

        checkpoint = self.root / f"{'npt' if barostat else 'nvt'}.chk"
        checkpoint.write_bytes(first.context.createCheckpoint())
        box_at_checkpoint = first.context.getState().getPeriodicBoxVectors(
            asNumpy=True).value_in_unit(unit.nanometer)

        first.step(1000)
        straight = first.context.getState(getPositions=True).getPositions(
            asNumpy=True).value_in_unit(unit.nanometer)

        system2, topology2, _ = _a_periodic_system(barostat=barostat)
        second = _a_simulation(system2, topology2, positions)
        second.context.loadCheckpoint(checkpoint.read_bytes())
        box_after_load = second.context.getState().getPeriodicBoxVectors(
            asNumpy=True).value_in_unit(unit.nanometer)
        second.step(1000)
        resumed = second.context.getState(getPositions=True).getPositions(
            asNumpy=True).value_in_unit(unit.nanometer)
        return box_at_checkpoint, box_after_load, straight, resumed

    def _state_at_checkpoint(self, *, barostat):
        """The first run's state at the moment it was checkpointed."""
        system, topology, positions = _a_periodic_system(barostat=barostat)
        first = _a_simulation(system, topology, positions)
        first.minimizeEnergy(maxIterations=50)
        first.context.setVelocitiesToTemperature(300 * unit.kelvin, 99)
        first.step(1000)
        state = first.context.getState(getPositions=True, getVelocities=True)
        return (
            state.getPositions(asNumpy=True).value_in_unit(unit.nanometer),
            state.getVelocities(asNumpy=True).value_in_unit(
                unit.nanometer / unit.picosecond),
            bytes(first.context.createCheckpoint()),
        )

    def test_the_box_comes_back_exactly_under_pressure(self):
        # The part that does work. Positions, velocities and box vectors
        # are all in the checkpoint, so the state a second segment starts
        # from is the state the first one ended at.
        at_checkpoint, after_load, _, _ = self._continue_and_resume(
            barostat=True)
        np.testing.assert_allclose(at_checkpoint, after_load, atol=1e-9)

    def test_the_state_a_resumed_run_starts_from_is_the_state_it_stopped_at(
        self
    ):
        """The claim segmentation actually rests on, asked before any
        dynamics can obscure it.

        Positions and velocities both, because positions alone would pass
        for a run that resumed from the right place at the wrong speed --
        which is a different trajectory and a different temperature, and
        nothing downstream would say so.

        Tight, and it can afford to be: this compares a checkpoint with
        what loading it produced, with no integration in between, so
        there is nothing here for chaos to amplify. The test below runs a
        thousand steps and cannot be this strict.
        """
        positions, velocities, checkpoint = self._state_at_checkpoint(
            barostat=False)

        system, topology, start = _a_periodic_system(barostat=False)
        second = _a_simulation(system, topology, start)
        second.context.loadCheckpoint(checkpoint)
        state = second.context.getState(getPositions=True, getVelocities=True)

        np.testing.assert_allclose(
            state.getPositions(asNumpy=True).value_in_unit(unit.nanometer),
            positions, atol=1e-9)
        np.testing.assert_allclose(
            state.getVelocities(asNumpy=True).value_in_unit(
                unit.nanometer / unit.picosecond),
            velocities, atol=1e-9)

    def test_constant_volume_stays_together_for_a_thousand_steps(self):
        """And the consequence: having started from the same state, the
        two runs track each other.

        Measured at 8.3e-8 nm, a hundredfold inside the bound, and the
        figure is the checkpoint's own precision rather than chaos: with
        the thread count pinned the arithmetic is deterministic, so it
        does not grow with load. Before it was pinned this drifted to
        1.2e-5 on a busy CI runner and failed a test that asserted 1e-5
        and called itself "resumes exactly" -- three numbers out of a
        hundred and ninety-two, which is chaos arriving on schedule
        rather than a defect.

        Exactness is the test above, which compares a checkpoint with
        what loading it produced and has nothing in between to amplify.
        This one says the two runs then track each other.
        """
        _, _, straight, resumed = self._continue_and_resume(barostat=False)
        np.testing.assert_allclose(straight, resumed, atol=_RESUME_DRIFT_NM)

    def test_constant_pressure_does_not_and_that_is_why_it_is_qualified(self):
        # The finding the qualification exists for. The barostat's adaptive
        # volume-move size is not in the checkpoint and is not a Context
        # parameter, so it restarts at its default and re-adapts after the
        # join. The state is right and the ensemble is right; the
        # trajectory is not the one an unsplit run would have produced.
        #
        # Against the same bound the constant-volume case is held to, so
        # the two read as the contrast they are. It is not a close thing:
        # 0.92 nm measured against 8.3e-8 for constant volume, five orders
        # past the line. An earlier version compared against a number that
        # sat below what an unpinned thread count could produce on its
        # own, which would have let this pass on rounding alone.
        #
        # If a future OpenMM starts carrying that state, this test fails
        # and the qualification should come off rather than be kept out of
        # habit.
        _, _, straight, resumed = self._continue_and_resume(barostat=True)
        self.assertFalse(
            np.allclose(straight, resumed, atol=_RESUME_DRIFT_NM),
            "constant pressure now resumes exactly; drop the qualification")

    def test_the_qualification_is_attached_to_constant_pressure_studies(self):
        from fastmdxplora.simulation.resume import segmentability

        under_pressure = segmentability(
            {"simulation": {"duration_ns": 10, "pressure_bar": 1.0}})
        self.assertTrue(under_pressure.allowed)
        self.assertIn("barostat", under_pressure.qualification)

        constant_volume = segmentability({"simulation": {"duration_ns": 10}})
        self.assertTrue(constant_volume.allowed)
        self.assertEqual(constant_volume.qualification, "")

    def test_it_is_a_qualification_rather_than_a_refusal(self):
        # Refusing would refuse constant pressure, which is most work
        # anybody does. Saying nothing would leave a volume artefact for
        # somebody to find.
        from fastmdxplora.simulation.resume import require_segmentable

        require_segmentable(
            {"simulation": {"duration_ns": 10, "pressure_bar": 1.0}},
            segments=10)


class TestACheckpointSaysWhatItIs(unittest.TestCase):
    """A checkpoint carries positions, velocities and the integrator's
    state, and nothing that says where in a run it was taken. Loading one
    from production into a run that then minimises and equilibrates it
    throws the velocities away and makes the continuation a new run from
    a snapshot rather than the same trajectory; nothing refused, because
    nothing knew. The sidecar is how the loader knows."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.d = Path(tempfile.mkdtemp())
        self.chk = self.d / "checkpoint.chk"
        self.chk.write_bytes(b"not a real checkpoint")

    def test_the_sidecar_records_the_stage_and_the_step(self):
        from fastmdxplora.simulation.runner import (read_checkpoint_sidecar,
                                                     write_checkpoint_sidecar)

        write_checkpoint_sidecar(self.chk, stage="production", step=321000, ensemble="npt",
                                 temperature_K=300.0, timestep_fs=2.0, study="/s")
        side = read_checkpoint_sidecar(self.chk)
        self.assertEqual(side["stage"], "production")
        self.assertEqual(side["step"], 321000)
        self.assertEqual(side["ensemble"], "npt")
        self.assertEqual(side["timestep_fs"], 2.0)
        self.assertTrue((self.d / "checkpoint.chk.json").is_file())

    def test_a_production_checkpoint_continues_only_into_production(self):
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.simulation.runner import check_continuation, write_checkpoint_sidecar

        write_checkpoint_sidecar(self.chk, stage="production", step=1000, ensemble="npt",
                                 temperature_K=300.0, timestep_fs=2.0)
        self.assertIsNotNone(check_continuation(
            self.chk, minimize=False, nvt_steps=0, npt_steps=0, timestep_fs=2.0))
        for what, kw in (("minimise", dict(minimize=True, nvt_steps=0, npt_steps=0)),
                         ("nvt", dict(minimize=False, nvt_steps=50000, npt_steps=0)),
                         ("npt", dict(minimize=False, nvt_steps=0, npt_steps=50000))):
            with self.subTest(what=what), self.assertRaises(StudyError) as caught:
                check_continuation(self.chk, timestep_fs=2.0, **kw)
            self.assertEqual(caught.exception.code, "simulation.resume.would_reequilibrate")
            self.assertIn("velocities", str(caught.exception))
            self.assertIn("nvt_steps: 0", str(caught.exception))

    def test_a_different_timestep_is_refused(self):
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.simulation.runner import check_continuation, write_checkpoint_sidecar

        write_checkpoint_sidecar(self.chk, stage="production", step=1000, ensemble="npt",
                                 temperature_K=300.0, timestep_fs=2.0)
        with self.assertRaises(StudyError) as caught:
            check_continuation(self.chk, minimize=False, nvt_steps=0, npt_steps=0, timestep_fs=4.0)
        self.assertEqual(caught.exception.code, "simulation.resume.timestep_differs")

    def test_a_checkpoint_with_no_sidecar_loads_with_a_warning(self):
        # A hand-made checkpoint, or one from before the sidecar existed:
        # nothing to check it against, and refusing would lock out every
        # checkpoint made before this.
        from fastmdxplora.simulation.runner import check_continuation

        from fastmdxplora.simulation.runner import logger

        with self.assertLogs(logger, level="WARNING") as logs:
            self.assertIsNone(check_continuation(
                self.chk, minimize=True, nvt_steps=1, npt_steps=1, timestep_fs=2.0))
        self.assertTrue(any("no sidecar" in line for line in logs.output))

    def test_the_fence_stands_before_the_load(self):
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner.run_simulation)
        self.assertLess(source.index("check_continuation(resume_from"),
                        source.index("load_checkpoint(omm, simulation, resume_from"))

    def test_both_writers_carry_the_sidecar(self):
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner.run_simulation)
        self.assertIn('sidecar={"stage": "production"', source)
        self.assertIn("write_checkpoint_sidecar(\n                checkpoint_path", source)
        reporter = inspect.getsource(runner._attach_checkpoint_reporter)
        self.assertIn("write_checkpoint_sidecar(", reporter)


class TestContinuingAStudyThatStopped(unittest.TestCase):
    """One new segment from a study that ran and stopped, with the
    arithmetic done from the record. The Agent used to write resume_from
    by hand, leaving minimisation and equilibration on, and ask the
    person for the equilibration lengths; the parent's resolved config has
    them, the checkpoint's sidecar has the step."""

    def study(self, *, step=321000, duration=0.5, nvt=50000, npt=50000, sidecar=True):
        import tempfile
        from pathlib import Path

        import yaml

        from fastmdxplora.simulation.runner import write_checkpoint_sidecar

        s = Path(tempfile.mkdtemp()) / "fastmdxplora_1UAO_study_1"
        (s / "simulation").mkdir(parents=True)
        (s / "resolved_config.yml").write_text(yaml.safe_dump({
            "systems": [{"system": "1UAO", "id": "chignolin"}],
            "simulation": {"duration_ns": duration, "nvt_steps": nvt, "npt_steps": npt,
                           "timestep_fs": 2.0},
            "agent": "assisted", "output": str(s)}), encoding="utf-8")
        chk = s / "simulation" / "checkpoint.chk"
        chk.write_bytes(b"x")
        if sidecar:
            write_checkpoint_sidecar(chk, stage="production", step=step, ensemble="npt",
                                     temperature_K=300.0, timestep_fs=2.0, study=str(s))
        return s

    def test_the_arithmetic_is_done_from_the_record(self):
        from fastmdxplora.simulation.resume import continuation_of

        c = continuation_of(self.study(), total_ns=0.5)
        self.assertTrue(c.possible)
        # 321,000 whole-run steps, 100,000 of them equilibration: 0.442 ns done.
        self.assertAlmostEqual(c.production_done_ns, 0.442, places=6)
        self.assertAlmostEqual(c.config["simulation"]["duration_ns"], 0.058, places=6)

    def test_the_config_is_a_true_continuation(self):
        from fastmdxplora.simulation.resume import continuation_of
        from fastmdxplora.simulation.runner import check_continuation

        s = self.study()
        sim = continuation_of(s).config["simulation"]
        self.assertFalse(sim["minimize"])
        self.assertEqual((sim["nvt_steps"], sim["npt_steps"]), (0, 0))
        # Resolved: on macOS /var is /private/var, and a config should
        # carry the canonical path.
        self.assertEqual(sim["setup_from"], str(s.resolve()))
        self.assertTrue(sim["resume_from"].endswith("checkpoint.chk"))
        self.assertEqual(sim["ensemble"], "npt")
        # And the fence it would face agrees.
        self.assertIsNotNone(check_continuation(
            s / "simulation" / "checkpoint.chk", minimize=sim["minimize"],
            nvt_steps=sim["nvt_steps"], npt_steps=sim["npt_steps"], timestep_fs=2.0))

    def test_more_and_total_and_the_remainder(self):
        from fastmdxplora.simulation.resume import continuation_of

        s = self.study()
        self.assertAlmostEqual(continuation_of(s, more_ns=1.0).config["simulation"]["duration_ns"], 1.0)
        self.assertAlmostEqual(continuation_of(s).config["simulation"]["duration_ns"], 0.058, places=6)
        self.assertIn("nothing remains", continuation_of(s, total_ns=0.4).refusal)

    def test_a_study_with_no_production_checkpoint_says_so(self):
        from fastmdxplora.simulation.resume import continuation_of

        s = self.study(sidecar=False)
        self.assertIn("does not say it was written during production", continuation_of(s).refusal)

    def test_the_new_study_records_its_parent(self):
        from fastmdxplora.simulation.pipeline import _continuation_of

        s = self.study()
        cont = _continuation_of({"resume_from": str(s / "simulation" / "checkpoint.chk")})
        self.assertEqual(cont["study"], str(s))
        self.assertEqual(cont["from_step"], 321000)
        self.assertTrue(cont["known_from_sidecar"])

    def test_the_agent_is_handed_the_continuation(self):
        from fastmdxplora.gui.agent_panel import _run_status

        s = self.study()

        class Runtime:
            active_root = s

            def snapshot(self):
                return {"active_run": str(s), "status": "idle"}

        status = _run_status(Runtime())
        self.assertIn("continuing this study: production done 0.442 ns", status)
        self.assertIn("resume_from:", status)
        self.assertIn("minimize: false", status)
        from fastmdxplora.agent.propose import prompt_for

        self.assertIn("never write `resume_from` from scratch", prompt_for("x"))
