"""A study that failed raises, when the caller asks it to.

`explore()` records a failure on the phase that refused and returns it, so a
script that does not look at the results carries on as though the study had
succeeded. Decided 2026-09-24 for the next breaking release: a failure
raises. Until then it is opt-in, with nothing that works today changed:
`explore(check=True)` and `RunResult.raise_for_status()`.
"""

from __future__ import annotations

import pytest

from fastmdxplora import FastMDXplora, StudyFailed
from fastmdxplora.orchestrator import PhaseResult, RunResult
from fastmdxplora.refusals import CodedError, Refusal


def _failed(run_id: str = "s1", code: str = "setup.forcefield.incompatible") -> RunResult:
    refusal = Refusal(code=code, message="The force field cannot describe it.",
                      details={"forcefield": "amber14"}).as_dict()
    return RunResult(run_id=run_id, system="1UBQ", status="error", phases=[
        PhaseResult(name="setup", status="error", message="The force field cannot "
                    "describe it.", refusal=refusal)])


def _fine(run_id: str = "s2") -> RunResult:
    return RunResult(run_id=run_id, system="1UBQ", status="ok",
                     phases=[PhaseResult(name="setup", status="ok")])


class TestRaiseForStatus:

    def test_a_failed_run_raises_its_phases_refusal(self) -> None:
        with pytest.raises(StudyFailed) as raised:
            _failed().raise_for_status()
        assert raised.value.code == "setup.forcefield.incompatible"
        assert raised.value.refusal.details == {"forcefield": "amber14"}
        assert str(raised.value) == ("Run s1 failed in setup: The force field "
                                     "cannot describe it.")
        assert isinstance(raised.value, CodedError) and isinstance(raised.value, RuntimeError)

    def test_a_run_that_finished_or_did_not_start_is_quiet(self) -> None:
        assert _fine().raise_for_status() is None
        RunResult(run_id="s3", system="1UBQ", status="skipped").raise_for_status()

    def test_a_run_with_no_phase_says_what_it_recorded(self) -> None:
        run = RunResult(run_id="w", system="x", status="error",
                        message="StudyError: no system")
        with pytest.raises(StudyFailed, match="Run w failed: StudyError: no system"):
            run.raise_for_status()


class TestExploreCheck:

    @pytest.fixture
    def study(self, monkeypatch):
        """A study whose runs come back as ``outcome`` says."""
        outcome: list[RunResult] = []

        def explore_config(self, **_kwargs):
            return list(outcome)

        monkeypatch.setattr(FastMDXplora, "_explore_config", explore_config)
        return outcome, FastMDXplora(config_data={"systems": [{"system": "1UBQ"}]})

    def test_by_default_the_failure_is_returned(self, study) -> None:
        outcome, fmdx = study
        outcome.extend([_failed()])
        assert fmdx.explore()[0].status == "error"

    def test_asked_to_check_it_raises_with_every_failure(self, study) -> None:
        outcome, fmdx = study
        outcome.extend([_failed("w0"), _fine("w1"), _failed("w2", "simulation.run.stopped")])
        with pytest.raises(StudyFailed) as raised:
            fmdx.explore(check=True)
        assert raised.value.code == "setup.forcefield.incompatible"
        assert [run.run_id for run in raised.value.failed] == ["w0", "w2"]
        assert len(raised.value.results) == 3
        assert "(1 more failed: w2)" in str(raised.value)

    def test_asked_to_check_a_study_that_succeeded_returns_it(self, study) -> None:
        outcome, fmdx = study
        outcome.extend([_fine("w0"), _fine("w1")])
        assert [run.run_id for run in fmdx.explore(check=True)] == ["w0", "w1"]


def test_a_real_study_that_fails_raises_its_refusal(tmp_path) -> None:
    """Through the whole pipeline: the phase records the refusal, and the
    exception carries it."""
    study = FastMDXplora(system=str(tmp_path / "missing.pdb"),
                         output_dir=str(tmp_path / "out"))
    with pytest.raises(StudyFailed) as raised:
        study.explore(include=["setup"], check=True)
    assert raised.value.code == "environment.path.not_found"
    assert str(raised.value).startswith("Run s1 failed in setup: No structure at")
    assert raised.value.results[0].phase("setup").status == "error"
