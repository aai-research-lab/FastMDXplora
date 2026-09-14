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

import inspect
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

    def test_a_missing_checkpoint_refuses(self):
        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner)
        assert "MissingResultError" in source
        assert "No checkpoint at" in source

    def test_a_mismatched_checkpoint_refuses_rather_than_loading(self):
        # OpenMM raises when a checkpoint does not match the System and
        # Platform it is loaded into. That raise is the only check there
        # is -- there is no cheaper way to verify a checkpoint belongs to
        # this system -- so it must be turned into a refusal and never
        # swallowed.
        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner)
        assert "simulation.resume.checkpoint_rejected" in source
        assert "loadCheckpoint" in source


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
