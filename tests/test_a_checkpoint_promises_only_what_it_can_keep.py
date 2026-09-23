"""The checkpoint is read now, and only where reading it is sound.

This file used to hold the opposite line. `checkpoint.chk` was written and
nothing opened it, while three places -- the schema, the runner, the file
browser -- told people it could be loaded to resume a run. The test that
stood here asserted that no `loadCheckpoint` call existed anywhere, and
said in its own docstring:

    If this ever fails, resume has been implemented and the wording
    should say so again.

It has been, so this is that saying-so. What the old file recorded was not
"resume is hard" but exactly which studies it is wrong for, and that list
is now the specification rather than the reason for the absence:

  - a resumed metadynamics run starts again from zero bias inside a well
    it has already filled, and reports a free energy surface that is
    quietly wrong;
  - a steered pull places its restraint by absolute step number, so
    resuming mid-pull puts the anchor somewhere the protein is not.

Both refuse. Unbiased runs and umbrella windows resume, because neither
carries state a checkpoint does not hold. And a checkpoint that does not
belong to this system is refused rather than loaded, because OpenMM's own
rejection is the only check available and it must not be swallowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.simulation.resume import require_segmentable, segmentability

SOURCE = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"


class TestTheTwoStudiesItIsWrongFor:
    """The list the old file recorded, now enforced rather than explained."""

    @pytest.mark.parametrize("method,block,code", [
        ("metadynamics", {"metadynamics": {"sigma": 0.1}},
         "simulation.resume.bias_not_carried"),
        ("steered", {"steered": {"to": 3.0}},
         "simulation.resume.time_dependent_bias"),
        ("plumed", {"plumed": {"script": "p.dat"}},
         "simulation.resume.bias_not_carried"),
    ])
    def test_it_refuses(self, method, block, code):
        with pytest.raises(StudyError) as caught:
            require_segmentable({"simulation": block}, segments=10)
        assert refusal_of(caught.value).code == code

    @pytest.mark.parametrize("method,block", [
        ("unbiased", {"duration_ns": 100}),
        ("umbrella", {"umbrella": {"centres": [1.0, 1.5]}}),
    ])
    def test_it_allows(self, method, block):
        assert segmentability({"simulation": block}).allowed

    def test_the_reason_names_the_mechanism_not_just_the_method(self):
        # "Metadynamics cannot be split" is a rule somebody will work
        # around. "The second piece starts from zero bias in a well the
        # first already filled" is a reason they will not.
        reason = segmentability(
            {"simulation": {"metadynamics": {"sigma": 0.1}}}).reason
        assert "zero bias" in reason
        assert "already filled" in reason


class TestTheCheckpointIsCheckedRatherThanTrusted:

    def test_a_missing_checkpoint_refuses(self, tmp_path) -> None:
        # Asked to continue from a checkpoint that is not there, a run
        # refuses and says where it looked; it does not start over.
        from fastmdxplora.refusals import MissingResultError
        from fastmdxplora.simulation.runner import load_checkpoint

        simulation = _twenty_argon_atoms()[0]
        with pytest.raises(MissingResultError) as raised:
            load_checkpoint({}, simulation, tmp_path / "study" / "checkpoint.chk")
        assert f"No checkpoint at {tmp_path / 'study' / 'checkpoint.chk'}" in str(raised.value)

    def test_a_mismatched_checkpoint_refuses_rather_than_loading(self, tmp_path) -> None:
        # OpenMM raises when a checkpoint does not match the System and
        # Platform it is loaded into. That raise is the only check there
        # is -- there is no cheaper way to verify a checkpoint belongs to
        # this system -- so it must be turned into a refusal and never
        # swallowed. A checkpoint of twenty atoms, loaded into twenty-one.
        from fastmdxplora.simulation.runner import UnstableRun, load_checkpoint

        written, _ = _twenty_argon_atoms()
        checkpoint = tmp_path / "checkpoint.chk"
        with open(checkpoint, "wb") as handle:
            handle.write(written.context.createCheckpoint())
        other = _twenty_argon_atoms(n=21)[0]
        with pytest.raises(UnstableRun) as raised:
            load_checkpoint({}, other, checkpoint)
        assert raised.value.code == "simulation.resume.checkpoint_rejected"
        assert raised.value.__cause__ is not None

class TestTheWordingMatchesWhatIsTrue:
    """The point of the original file, kept: say only what is so."""

    def test_the_schema_says_which_studies_may_not_resume(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = next(f for f in PHASE_SCHEMAS["simulation"].fields
                     if f.name == "resume_from")
        assert "metadynamics" in field.help
        assert "refuses" in field.help

    def test_a_resumed_run_is_recorded_as_one(self):
        # A trajectory assembled from pieces is not the object that ran
        # through, and an analysis reading equilibration across the join is
        # reading across a discontinuity.
        from fastmdxplora.simulation.resume import resume_provenance

        joined = resume_provenance(None, segment=1, of_segments=2,
                                   from_step=1000)
        assert joined["ran_through"] is False
        assert joined["joins"]

        whole = resume_provenance(None, segment=0, of_segments=1,
                                  from_step=0)
        assert whole["ran_through"] is True


def _twenty_argon_atoms(n: int = 20):
    """A real simulation of `n` argon atoms on the CPU platform, and its
    System. Enough for OpenMM to write a checkpoint and to refuse one."""
    pytest.importorskip("openmm.app")
    import openmm
    from openmm import app, unit

    system = openmm.System()
    topology = app.Topology()
    residue = topology.addResidue("AR", topology.addChain())
    force = openmm.NonbondedForce()
    for _ in range(n):
        system.addParticle(39.948)
        force.addParticle(0.0, 0.34, 0.99)
        topology.addAtom("AR", app.Element.getBySymbol("Ar"), residue)
    system.addForce(force)
    positions = [openmm.Vec3((i % 5) * 0.4, (i // 5) * 0.4, 0.0) for i in range(n)]
    simulation = app.Simulation(topology, system,
                                openmm.VerletIntegrator(1 * unit.femtosecond),
                                openmm.Platform.getPlatformByName("CPU"))
    simulation.context.setPositions(positions * unit.nanometer)
    return simulation, system
