"""A study runs until what it is for is determined, and no longer.

`simulation.stop_when` states the measures a study is for and how well each
must be known, and a ceiling. The study runs its first piece, reads the
analyses, and extends every run by what the numbers ask for until each
measure is determined as asked or the ceiling is reached. By default the runs
must be replicas, and must agree with each other within their own errors,
because one run trapped in one state equilibrates there and shrinks its error bar all
the same.

The judging and the loop are exercised here on recorded means, with the
extension replaced by one that writes the next round's means; a real
extension is `extend_study`, tested on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.simulation import stopping
from fastmdxplora.simulation.stopping import (
    StopTarget, check_stopping, judge, next_piece, run_until_known, stopping_section,
    targets_of)


def _mean(run: Path, analysis: str, mean: float, error: float | None, *,
          n: int = 1000, discard: int = 0, unit: str = "nm",
          withheld: str | None = None, more_ns: float | None = None) -> None:
    record: dict = {"mean": mean, "standard_error": error, "n_frames": n,
                    "discard": discard, "unit": unit}
    if withheld:
        record["not_a_measurement"] = withheld
        if more_ns is not None:
            record["shortfall"] = {"more_ns": more_ns, "more_frames": 10}
    where = run / "analysis" / analysis
    where.mkdir(parents=True, exist_ok=True)
    (where / "options.json").write_text(json.dumps(
        {"analysis": analysis, "findings": {"mean": record}}), encoding="utf-8")


def _study(**stop_when) -> dict:
    rule = {"measures": [{"analysis": "rmsd", "standard_error": 0.01}],
            "max_duration_ns": 20}
    rule.update(stop_when)
    return {"systems": [{"system": "1UAO"}],
            "simulation": {"duration_ns": 2, "stop_when": rule}}


PHASES = ["setup", "simulation", "analysis", "report"]


class TestTheRuleIsCheckedBeforeAnythingRuns:
    def test_a_study_without_one_is_untouched(self):
        assert check_stopping({"simulation": {"duration_ns": 2}}, replicas=False, runs=1,
                              phases=PHASES) is None

    def test_replicas_that_agree_are_the_default(self):
        with pytest.raises(StudyError) as raised:
            check_stopping(_study(), replicas=False, runs=1, phases=PHASES)
        found = refusal_of(raised.value)
        assert found.code == "simulation.stopping.no_replicas"
        assert "trapped" in str(raised.value) and "random_seed" in str(raised.value)

    def test_one_run_when_the_study_says_so(self):
        targets = check_stopping(_study(independent_starts="not_required"),
                                 replicas=False, runs=1, phases=PHASES)
        assert targets == [StopTarget("rmsd", standard_error=0.01)]

    def test_replicas_are_accepted(self):
        assert check_stopping(_study(), replicas=True, runs=3, phases=PHASES)

    def test_runs_that_are_not_replicas_are_not(self):
        with pytest.raises(StudyError) as raised:
            check_stopping(_study(), replicas=False, runs=3, phases=PHASES)
        assert refusal_of(raised.value).code == "simulation.stopping.no_replicas"

    def test_a_piece_of_a_study_does_not_apply_it(self):
        # An extension simulates only; an analysis rerun does not simulate.
        assert check_stopping(_study(), replicas=False, runs=1, phases=["simulation"]) is None
        assert check_stopping(_study(), replicas=False, runs=1,
                              phases=["analysis", "report"]) is None

    def test_the_ceiling_is_needed_and_above_the_first_piece(self):
        rule = _study()
        del rule["simulation"]["stop_when"]["max_duration_ns"]
        with pytest.raises(StudyError) as raised:
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)
        assert refusal_of(raised.value).code == "config.option.missing_companion"
        with pytest.raises(StudyError) as raised:
            check_stopping(_study(max_duration_ns=1), replicas=True, runs=3, phases=PHASES)
        found = refusal_of(raised.value)
        assert found.code == "config.option.out_of_range"
        assert found.details["minimum"] == 2

    def test_the_first_piece_is_read_as_the_runner_reads_it(self):
        rule = _study(max_duration_ns=0.5)
        rule["simulation"] = {"production_steps": 100_000, "timestep_fs": 4,
                              "stop_when": rule["simulation"]["stop_when"]}
        assert check_stopping(rule, replicas=True, runs=3, phases=PHASES)

    def test_a_study_that_cannot_be_split_is_refused(self):
        rule = _study()
        rule["simulation"]["metadynamics"] = {"collective_variable": "rg"}
        with pytest.raises(StudyError):
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)

    def test_an_analysis_the_study_does_not_run(self):
        rule = _study()
        rule["analysis"] = {"include": ["rg"]}
        with pytest.raises(StudyError) as raised:
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)
        found = refusal_of(raised.value)
        assert found.code == "config.option.missing_companion"
        assert found.details["requires"] == ["rmsd"]

    def test_a_value_is_not_a_rule(self):
        rule = _study()
        rule["simulation"]["stop_when"] = "rmsd"
        with pytest.raises(StudyError) as raised:
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)
        assert refusal_of(raised.value).details["found_type"] == "str"

    def test_a_study_with_no_production(self):
        rule = _study()
        rule["simulation"]["duration_ns"] = 0
        with pytest.raises(StudyError) as raised:
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)
        assert refusal_of(raised.value).details["context"] == "a study with no production"

    def test_umbrella_windows_are_not_judged_by_it(self):
        # A window may be split; its free energy has its own measure.
        rule = _study()
        rule["simulation"]["umbrella"] = {"collective_variable": "distance"}
        with pytest.raises(StudyError) as raised:
            check_stopping(rule, replicas=True, runs=3, phases=PHASES)
        assert refusal_of(raised.value).details["context"] == "an umbrella study"

    def test_independent_starts_is_one_of_two(self):
        with pytest.raises(StudyError) as raised:
            check_stopping(_study(independent_starts="sometimes"), replicas=True, runs=3,
                           phases=PHASES)
        assert refusal_of(raised.value).details["permitted"] == ["required", "not_required"]

    def test_analyses_named_as_a_string(self):
        rule = _study()
        rule["analysis"] = {"include": "rg, rmsd"}
        assert check_stopping(rule, replicas=True, runs=3, phases=PHASES)

    def test_a_rule_that_does_not_read_is_said_so_in_the_plan(self):
        from fastmdxplora.simulation.stopping import rule_said

        assert "refused before anything runs" in rule_said({"measures": []})

    @pytest.mark.parametrize("entry, code", [
        ({"analysis": "rmsd"}, "config.option.conflicting"),
        ({"analysis": "rmsd", "standard_error": 0.1, "relative_error": 0.1},
         "config.option.conflicting"),
        ({"analysis": "rmsd", "relative_error": 0}, "config.option.out_of_range"),
        ({"analysis": "rmsd", "standard_error": True}, "config.option.out_of_range"),
        ({"standard_error": 0.1}, "config.option.missing_companion"),
    ])
    def test_each_measure_says_one_precision(self, entry, code):
        with pytest.raises(StudyError) as raised:
            targets_of({"measures": [entry]})
        assert refusal_of(raised.value).code == code

    def test_no_measures(self):
        with pytest.raises(StudyError) as raised:
            targets_of({"max_duration_ns": 5})
        assert refusal_of(raised.value).code == "config.option.missing_companion"

    def test_it_is_a_setting_of_the_simulation(self):
        from fastmdxplora.config.loader import validate_config

        validate_config({**_study(), "sweep": {"simulation.random_seed": [1, 2, 3]}},
                        require_systems=True)

    def test_validation_refuses_what_the_run_would(self):
        """So `fastmdx check-config`, the GUI's check and the Agent's repair
        loop see the refusal, not only a run that has started."""
        from fastmdxplora.config.loader import ConfigError, validate_config

        with pytest.raises(ConfigError) as raised:
            validate_config(_study(), require_systems=True)
        assert refusal_of(raised.value).code == "simulation.stopping.no_replicas"
        # A fragment (a form mid-edit) is checked for its own shape only.
        validate_config({"simulation": _study()["simulation"]})
        with pytest.raises(ConfigError) as raised:
            validate_config({"simulation": {"stop_when": {"measures": [{"analysis": "rmsd"}]}}})
        assert refusal_of(raised.value).code == "config.option.conflicting"

    def test_a_measure_must_record_a_mean(self):
        with pytest.raises(StudyError) as raised:
            check_stopping(_study(measures=[{"analysis": "rmsf", "standard_error": 0.01}]),
                           replicas=True, runs=3, phases=PHASES)
        found = refusal_of(raised.value)
        assert found.code == "config.option.not_permitted"
        assert "rmsd" in found.details["permitted"] and "rmsf" not in found.details["permitted"]

    def test_replicas_are_read_as_the_batch_layer_expands_them(self):
        from fastmdxplora.simulation.stopping import replicas_of

        seeds = {"sweep": {"simulation.random_seed": [1, 2, 3]}}
        assert replicas_of({**_study(), **seeds}) == (True, 3)
        assert replicas_of({**_study(), "sweep": {"simulation.temperature_K": [300, 310]}}) == (
            False, 2)
        two = {"systems": [{"system": "1UAO"}, {"system": "1L2Y"}], **seeds}
        assert replicas_of(two) == (False, 6)
        assert replicas_of(_study()) == (False, 1)


class TestJudging:
    def test_one_run_within_what_was_asked(self, tmp_path):
        _mean(tmp_path, "rmsd", 0.2, 0.008)
        [verdict] = judge([tmp_path], [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert verdict.met and verdict.more_ns is None
        assert "0.2 ± 0.008 nm, within the ±0.01 asked" in verdict.said

    def test_one_run_short_of_it_asks_for_what_the_error_needs(self, tmp_path):
        # Twice the error allowed wants four times the kept frames: three
        # times as much again, of the kept share alone.
        _mean(tmp_path, "rmsd", 0.2, 0.02, n=1000, discard=200)
        [verdict] = judge([tmp_path], [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert not verdict.met
        assert verdict.more_ns == pytest.approx(2.0 * 0.8 * 3.0)

    def test_a_relative_target_is_of_the_mean(self, tmp_path):
        _mean(tmp_path, "sasa", 40.0, 1.5, unit="nm²")
        [verdict] = judge([tmp_path], [StopTarget("sasa", relative_error=0.05)], 2.0)
        assert verdict.met and verdict.allowed == pytest.approx(2.0)

    def test_a_mean_withheld_asks_for_its_own_shortfall(self, tmp_path):
        _mean(tmp_path, "rmsd", 0.2, None, withheld="too few samples", more_ns=7.5)
        [verdict] = judge([tmp_path], [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert not verdict.met and verdict.more_ns == 7.5
        assert "not yet determined" in verdict.said

    def test_a_mean_withheld_without_a_figure_asks_for_as_long_again(self, tmp_path):
        _mean(tmp_path, "rmsd", 0.2, None, withheld="not equilibrated")
        [verdict] = judge([tmp_path], [StopTarget("rmsd", standard_error=0.01)], 3.0)
        assert verdict.more_ns == 3.0

    def test_no_record_is_not_met(self, tmp_path):
        [verdict] = judge([tmp_path], [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert not verdict.met and "recorded no mean" in verdict.said

    def _replicas(self, tmp_path, means, errors):
        runs = []
        for index, (mean, error) in enumerate(zip(means, errors)):
            run = tmp_path / f"seed{index}"
            _mean(run, "rmsd", mean, error)
            runs.append(run)
        return runs

    def test_replicas_that_agree_are_pooled(self, tmp_path):
        runs = self._replicas(tmp_path, [0.200, 0.203, 0.198], [0.01, 0.01, 0.01])
        [verdict] = judge(runs, [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert verdict.agree and verdict.met
        assert verdict.value == pytest.approx(0.2003333, rel=1e-5)
        # Three runs of 0.01 each claim 0.01/sqrt(3) together; their spread
        # shows less, so the claim stands.
        assert verdict.error == pytest.approx(0.01 / 3 ** 0.5)
        assert [r["run"] for r in verdict.replicas] == ["seed0", "seed1", "seed2"]

    def test_the_spread_wins_where_it_is_larger(self, tmp_path):
        runs = self._replicas(tmp_path, [0.19, 0.20, 0.21], [0.008, 0.008, 0.008])
        [verdict] = judge(runs, [StopTarget("rmsd", standard_error=0.004)], 2.0)
        assert verdict.agree
        assert verdict.error == pytest.approx(0.01 / 3 ** 0.5)
        assert not verdict.met

    def test_replicas_each_precise_and_disagreeing_are_not_an_answer(self, tmp_path):
        # The trap: each run's error bar is small, and each has settled
        # somewhere different.
        runs = self._replicas(tmp_path, [0.15, 0.25, 0.20], [0.002, 0.002, 0.002])
        [verdict] = judge(runs, [StopTarget("rmsd", standard_error=0.01)], 2.0)
        assert verdict.agree is False and not verdict.met
        assert verdict.more_ns == 2.0
        assert "disagree" in verdict.said


class TestTheArithmetic:
    def test_every_frame_counts_where_none_were_recorded(self):
        assert stopping._kept_share({"mean": 1.0}) == 1.0
        assert stopping._kept_share({"n_frames": 100, "discard": 99}) == 0.05

    def test_nothing_more_where_the_error_is_already_small(self):
        assert stopping._more_for(0.01, 0.02, 5.0, 1.0) == 0.0

    def test_one_after_another_stops_at_the_first_that_fails(self, monkeypatch, tmp_path):
        tried = []

        def extend(run, *, more_ns):
            tried.append(run.name)
            return {"ok": run.name != "b"}
        monkeypatch.setattr("fastmdxplora.simulation.resume.extend_study", extend)
        answers = stopping.extend_one_after_another(
            [tmp_path / "a", tmp_path / "b", tmp_path / "c"], 1.0)
        assert tried == ["a", "b"] and [a["ok"] for a in answers] == [True, False]


class TestTheNextPiece:
    def test_bounded_per_round(self):
        asked = [stopping.Verdict("a", False, "", more_ns=100.0)]
        assert next_piece(asked, 2.0, 1000.0, None) == 6.0
        asked = [stopping.Verdict("a", False, "", more_ns=0.01)]
        assert next_piece(asked, 2.0, 1000.0, None) == 0.5

    def test_never_past_the_ceiling(self):
        asked = [stopping.Verdict("a", False, "", more_ns=5.0)]
        assert next_piece(asked, 2.0, 4.0, None) == 2.0

    def test_whole_frames(self):
        asked = [stopping.Verdict("a", False, "", more_ns=1.03)]
        assert next_piece(asked, 2.0, 100.0, 0.1) == pytest.approx(1.1)
        # Rounded down where rounding up would pass the ceiling.
        assert next_piece(asked, 2.0, 3.05, 0.1) == pytest.approx(1.0)

    def test_the_most_any_measure_asks(self):
        asked = [stopping.Verdict("a", False, "", more_ns=1.0),
                 stopping.Verdict("b", False, "", more_ns=3.0),
                 stopping.Verdict("c", True, "")]
        assert next_piece(asked, 2.0, 100.0, None) == 3.0


class _Replicas:
    """Runs whose means tighten as they are extended, as a real error does:
    the error falls as one over the root of the production."""

    def __init__(self, root: Path, means: list[float], error_at_2ns: float,
                 *, fail_at: int | None = None, stopped: bool = False):
        self.runs = [root / f"runs/seed{i}" for i in range(len(means))]
        self.means = means
        self.error_at_2ns = error_at_2ns
        self.production = {run: 2.0 for run in self.runs}
        self.calls: list[float] = []
        self.fail_at = fail_at
        self.stopped = stopped
        for run in self.runs:
            self._write(run)

    def _write(self, run: Path) -> None:
        index = self.runs.index(run)
        error = self.error_at_2ns * (2.0 / self.production[run]) ** 0.5
        _mean(run, "rmsd", self.means[index], error)

    def done(self, run) -> float:
        return self.production[Path(run)]

    def extend_all(self, runs: list[Path], more_ns: float) -> list[dict]:
        self.calls.append(more_ns)
        answers = []
        for run in runs:
            if self.fail_at is not None and len(self.calls) == self.fail_at:
                answers.append({"ok": False, "stage": "simulation", "error": "the card fell over",
                                "stopped": self.stopped})
                break
            self.production[run] += more_ns
            self._write(run)
            answers.append({"ok": True})
        return answers


@pytest.fixture
def replicas(tmp_path, monkeypatch):
    def make(means, error, **kw):
        made = _Replicas(tmp_path, means, error, **kw)
        monkeypatch.setattr("fastmdxplora.simulation.resume.production_done_ns", made.done)
        return made
    return make


RULE = {"measures": [{"analysis": "rmsd", "standard_error": 0.01}], "max_duration_ns": 20}


class TestTheLoop:
    def test_extends_until_known_and_records_each_round(self, tmp_path, replicas):
        made = replicas([0.20, 0.201, 0.199], 0.04)
        said = []
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=said.append)
        assert record["outcome"] == "met"
        # 0.04/sqrt(3) per replica at 2 ns wants 5.33 times the production:
        # capped at three times as much again in one round, then the rest.
        assert made.calls[0] == 6.0
        assert [r["decision"] for r in record["rounds"]][-1] == "met"
        assert all(r["decision"] == "extend" for r in record["rounds"][:-1])
        final = record["rounds"][-1]["verdicts"][0]
        assert final["error"] <= 0.01 and final["agree"] is True
        written = json.loads((tmp_path / "stopping.json").read_text(encoding="utf-8"))
        assert written == record
        assert "differ by seed" in record["said"]
        assert any("Extending every run" in line for line in said)

    def test_stops_at_the_ceiling_and_says_it_is_not_known(self, tmp_path, replicas):
        made = replicas([0.20, 0.201, 0.199], 0.5)
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "ceiling"
        assert record["rounds"][-1]["production_ns"] == 20
        assert "with what was asked not determined to the precision asked" in record["said"]
        assert sum(made.calls) == pytest.approx(18.0)

    def test_disagreeing_replicas_are_extended_not_accepted(self, tmp_path, replicas):
        made = replicas([0.10, 0.30, 0.20], 0.001)
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        # Each is precise from the start; they never agree, so the study
        # runs to its ceiling rather than stopping on the first round.
        assert record["outcome"] == "ceiling"
        assert all(v["agree"] is False for r in record["rounds"] for v in r["verdicts"])

    def test_earlier_rounds_and_how_many_ran_at_once_are_kept(self, tmp_path, replicas):
        made = replicas([0.20, 0.20, 0.20], 0.005)
        earlier = [{"production_ns": 1.0, "decision": "extend", "more_ns": 1.0}]
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None,
                                 at_once=3, earlier_rounds=earlier)
        assert record["rounds"][0] == earlier[0] and len(record["rounds"]) == 2
        assert record["at_once"] == 3

    def test_known_at_once_runs_nothing_more(self, tmp_path, replicas):
        made = replicas([0.20, 0.20, 0.20], 0.005)
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "met" and made.calls == []
        assert len(record["rounds"]) == 1

    def test_one_run_says_what_it_did_not_check(self, tmp_path, replicas):
        made = replicas([0.20], 0.005)
        rule = {**RULE, "independent_starts": "not_required"}
        record = run_until_known(made.runs, targets_of(rule), rule, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "met"
        assert "not checked against independent starts" in record["said"]

    def test_a_mean_never_recorded_stops_at_once(self, tmp_path, replicas):
        """More production does not give an analysis a mean it does not
        record: extending would spend the whole ceiling to learn nothing."""
        made = replicas([0.20, 0.201], 0.04)
        (made.runs[1] / "analysis" / "rmsd" / "options.json").unlink()
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "stopped" and made.calls == []
        assert "rmsd recorded no mean in seed1" in record["said"]
        assert record["rounds"][0]["verdicts"][0]["unrecorded"] is True

    def test_no_whole_frame_left_below_the_ceiling(self, tmp_path, replicas, monkeypatch):
        made = replicas([0.20, 0.201], 0.5)
        monkeypatch.setattr(stopping, "_frame_ns", lambda run: 1.0)
        record = run_until_known(made.runs, targets_of(RULE),
                                 {**RULE, "max_duration_ns": 2.5}, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "ceiling" and made.calls == []
        assert "leaves no whole frame to add" in record["said"]

    def test_a_bound_on_the_rounds(self, tmp_path, replicas, monkeypatch):
        made = replicas([0.20, 0.201], 5.0)
        monkeypatch.setattr(stopping, "MOST_ROUNDS", 2)
        said = []
        record = run_until_known(made.runs, targets_of(RULE),
                                 {**RULE, "max_duration_ns": 10_000}, record_in=tmp_path,
                                 extend_all=made.extend_all, say=said.append)
        assert record["outcome"] == "rounds" and len(made.calls) == 2
        assert said[-1] == record["said"]

    def test_a_failed_extension_stops_it(self, tmp_path, replicas):
        made = replicas([0.20, 0.201], 0.04, fail_at=1)
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "stopped"
        assert "seed0 failed at simulation: the card fell over" in record["said"]

    def test_asked_to_stop_says_how_to_go_on(self, tmp_path, replicas):
        made = replicas([0.20, 0.201], 0.04, fail_at=1, stopped=True)
        record = run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                                 extend_all=made.extend_all, say=lambda _: None)
        assert record["outcome"] == "stopped" and "Resuming the study" in record["said"]


class TestTheReport:
    def test_the_rounds_are_written_up(self, tmp_path, replicas):
        made = replicas([0.20, 0.201, 0.199], 0.04)
        run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                        extend_all=made.extend_all, say=lambda _: None)
        text = "\n".join(stopping_section(tmp_path))
        assert "## How long it ran, and why" in text
        assert "rmsd to ±0.01 nm" in text
        assert "| 1 | 2 | " in text and "extended by 6 ns" in text
        assert "stopped: determined as asked" in text
        assert "**Determined as asked.**" in text
        assert "is not in the error." in text

    def test_nothing_where_the_length_was_fixed(self, tmp_path):
        assert stopping_section(tmp_path) == []
        (tmp_path / "stopping.json").write_text("[]", encoding="utf-8")
        assert stopping_section(tmp_path) == []

    def test_replicas_that_disagree_are_marked(self, tmp_path, replicas):
        made = replicas([0.10, 0.30], 0.001)
        run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                        extend_all=made.extend_all, say=lambda _: None)
        text = "\n".join(stopping_section(tmp_path))
        assert ", replicas disagree |" in text and "**Not determined as asked.**" in text
        assert "is not in the error" not in text

    def test_the_study_report_carries_it(self, tmp_path, replicas):
        from fastmdxplora.report.document import _stopping_section

        made = replicas([0.20], 0.005)
        rule = {**RULE, "independent_starts": "not_required"}
        run_until_known(made.runs, targets_of(rule), rule, record_in=tmp_path,
                        extend_all=made.extend_all, say=lambda _: None)
        assert _stopping_section(tmp_path).startswith("## How long it ran, and why")

    def test_the_agent_is_given_it_from_a_replica(self, tmp_path, replicas):
        from fastmdxplora.gui.agent_panel import _stopping_summary

        made = replicas([0.20, 0.201, 0.199], 0.04)
        run_until_known(made.runs, targets_of(RULE), RULE, record_in=tmp_path,
                        extend_all=made.extend_all, say=lambda _: None)
        text = _stopping_summary(made.runs[1])
        assert text.startswith("how long the study ran, and why")
        assert "round 1, at 2.0 ns" in text and "-> extend by 6.0 ns" in text
        assert "outcome: After" in text


class TestThePlanAndThePrice:
    def test_the_plan_says_when_it_stops(self):
        from fastmdxplora.gui.plan import plan_of

        lines = {line["label"]: line["value"] for line in plan_of({
            "systems": [{"system": "1UAO"}], "sweep": {"simulation.random_seed": [1, 2, 3]},
            "simulation": {"duration_ns": 5, "stop_when": {
                "measures": [{"analysis": "rmsd", "standard_error": 0.01},
                             {"analysis": "sasa", "relative_error": 0.05}],
                "max_duration_ns": 50}}})}
        assert lines["Production"] == "5 ns first, 2 fs steps; then more, as the numbers ask"
        assert lines["Stops when"] == ("rmsd to ±0.01 nm and sasa to ±5% are determined and the "
                                       "replicas agree; or at 50 ns of production")

    def test_a_budget_prices_the_ceiling(self):
        from fastmdxplora.cost import total_steps

        fixed = total_steps({"duration_ns": 5})
        ruled = total_steps({"duration_ns": 5, "production_steps": 2_500_000,
                             "stop_when": {"max_duration_ns": 50}})
        assert ruled - fixed == 45 * 500_000


class TestTheStudyAppliesIt:
    """Through the batch layer, with each run's work replaced."""

    def _fake_run(self, means):
        from fastmdxplora.orchestrator import RunResult

        given = []

        def run(spec_dict, run_out, *args, **kwargs):
            given.append(spec_dict["options"])
            index = len(given) - 1
            _mean(Path(run_out), "rmsd", means[index], 0.002)
            return RunResult(run_id=spec_dict["run_id"], system=spec_dict["system"],
                             status="ok", output_dir=Path(run_out))
        return run, given

    def test_replicas_are_run_then_judged(self, tmp_path, monkeypatch):
        from fastmdxplora.batch.explorer import BatchExplorer

        fake, given = self._fake_run([0.20, 0.2005, 0.1995])
        monkeypatch.setattr("fastmdxplora.batch.explorer._execute_run", fake)
        monkeypatch.setattr("fastmdxplora.simulation.resume.production_done_ns",
                            lambda run: 2.0)
        config = {**_study(), "sweep": {"simulation.random_seed": [1, 2, 3]},
                  "report": {"comparison": False}}
        BatchExplorer(config_data=config, output_dir=tmp_path / "study").run()
        record = json.loads((tmp_path / "study" / "stopping.json").read_text(encoding="utf-8"))
        assert record["outcome"] == "met"
        assert record["runs"] == [f"s1__random-seed-{seed}" for seed in (1, 2, 3)]
        # Each run is handed the rule, and leaves it to the study: a run is
        # one piece, and a length.
        from fastmdxplora.batch.explorer import _without_the_study_rule

        assert all(options["simulation"]["stop_when"] for options in given)
        kept = _without_the_study_rule(given[0])
        assert "stop_when" not in kept["simulation"]
        assert kept["simulation"]["random_seed"] == 1
        assert given[0]["simulation"]["stop_when"], "the spec itself is left alone"

    def _explorer(self, tmp_path, **execution):
        from fastmdxplora.batch.explorer import BatchExplorer

        config = {**_study(), "sweep": {"simulation.random_seed": [1, 2]},
                  "execution": execution}
        return BatchExplorer(config_data=config, output_dir=tmp_path / "study")

    def test_one_after_another_on_the_first_card(self, tmp_path, monkeypatch):
        calls = []

        def extend(run, *, more_ns, device_index=None):
            calls.append((Path(run).name, more_ns, device_index))
            return {"ok": True}
        monkeypatch.setattr("fastmdxplora.simulation.resume.extend_study", extend)
        explorer = self._explorer(tmp_path, mode="sequential", devices=[1])
        answers = explorer._extend_runs([tmp_path / "a", tmp_path / "b"], 0.5)
        assert answers == [{"ok": True}, {"ok": True}]
        assert calls == [("a", 0.5, "1"), ("b", 0.5, "1")]

    def test_side_by_side_in_a_parallel_study(self, tmp_path):
        # Each in a worker of its own; neither run exists, so each worker
        # comes back with the planning refusal rather than a crash.
        explorer = self._explorer(tmp_path, mode="parallel", workers=2)
        answers = explorer._extend_runs([tmp_path / "a", tmp_path / "b"], 0.5)
        assert [a["ok"] for a in answers] == [False, False]
        assert all(a["stage"] == "planning" for a in answers)

    def test_one_after_another_stops_at_a_failure(self, tmp_path, monkeypatch):
        calls = []

        def extend(run, *, more_ns, device_index=None):
            calls.append(Path(run).name)
            return {"ok": False}
        monkeypatch.setattr("fastmdxplora.simulation.resume.extend_study", extend)
        explorer = self._explorer(tmp_path, mode="sequential")
        assert explorer._extend_runs([tmp_path / "a", tmp_path / "b"], 0.5) == [{"ok": False}]
        assert calls == ["a"]

    def test_a_worker_that_raises_is_an_answer(self, tmp_path, monkeypatch):
        from concurrent.futures import Future

        class Pool:
            def __init__(self, *args, **kwargs):
                pass

            def submit(self, *args):
                future = Future()
                future.set_exception(RuntimeError("the worker died"))
                return future

            def shutdown(self, **kwargs):
                pass
        monkeypatch.setattr("fastmdxplora.batch.explorer.ProcessPoolExecutor", Pool)
        explorer = self._explorer(tmp_path, mode="parallel", workers=2)
        answers = explorer._extend_runs([tmp_path / "a", tmp_path / "b"], 0.5)
        assert answers[0] == {"ok": False, "stage": "simulation",
                              "error": "RuntimeError: the worker died"}

    def _after(self, tmp_path, statuses, **extra):
        from fastmdxplora.orchestrator import RunResult

        explorer = self._explorer(tmp_path, mode="sequential")
        explorer.output_dir.mkdir(parents=True)
        explorer.results = [
            RunResult(run_id=f"r{i}", system="1UAO", status=status,
                      output_dir=tmp_path / f"r{i}", **extra)
            for i, status in enumerate(statuses)]
        return explorer

    def test_not_applied_when_too_few_replicas_finished(self, tmp_path, capsys):
        explorer = self._after(tmp_path, ["ok", "error"])
        explorer._run_until_known(targets_of(RULE))
        record = json.loads((explorer.output_dir / "stopping.json").read_text(encoding="utf-8"))
        assert record["outcome"] == "not_applied"
        assert "1 of 2 runs finished" in record["said"]

    def test_not_applied_to_a_study_asked_to_stop(self, tmp_path, capsys):
        from fastmdxplora.batch.explorer import STOPPED_ERROR_TYPE

        explorer = self._after(tmp_path, ["ok", "error"], error_type=STOPPED_ERROR_TYPE)
        explorer._run_until_known(targets_of(RULE))
        assert "Resuming the study applies it" in capsys.readouterr().out
        record = json.loads((explorer.output_dir / "stopping.json").read_text(encoding="utf-8"))
        assert record["outcome"] == "stopped" and record["rounds"] == []

    def test_the_rule_is_written_before_the_first_piece(self, tmp_path, monkeypatch):
        """A study running its first piece already says what it runs until,
        which is what the GUI shows while it waits."""
        from fastmdxplora.batch.explorer import BatchExplorer

        seen = {}

        def run(spec_dict, run_out, *args, **kwargs):
            seen.setdefault("record", json.loads(
                (tmp_path / "study" / "stopping.json").read_text(encoding="utf-8")))
            return self._fake_run([0.2, 0.2])[0](spec_dict, run_out)
        monkeypatch.setattr("fastmdxplora.batch.explorer._execute_run", run)
        monkeypatch.setattr("fastmdxplora.simulation.stopping.run_until_known",
                            lambda *a, **k: None)
        config = {**_study(), "sweep": {"simulation.random_seed": [1, 2]}}
        BatchExplorer(config_data=config, output_dir=tmp_path / "study").run()
        planned = seen["record"]
        assert planned["outcome"] == "running" and planned["rounds"] == []
        assert planned["runs"] == ["s1__random-seed-1", "s1__random-seed-2"]
        assert planned["targets"] == [{"analysis": "rmsd", "standard_error": 0.01,
                                       "relative_error": None}]

    def test_a_resumed_study_keeps_its_rounds(self, tmp_path):
        explorer = self._after(tmp_path, ["ok", "ok"])
        (explorer.output_dir / "stopping.json").write_text(json.dumps(
            {"rounds": [{"production_ns": 2.0, "decision": "extend"}]}), encoding="utf-8")
        assert explorer._earlier_rounds() == []
        explorer.resume = True
        assert explorer._earlier_rounds() == [{"production_ns": 2.0, "decision": "extend"}]
        explorer._write_planned_stopping(targets_of(RULE))
        kept = json.loads((explorer.output_dir / "stopping.json").read_text(encoding="utf-8"))
        assert kept["rounds"] == [{"production_ns": 2.0, "decision": "extend"}]

    def test_judged_on_the_runs_that_finished(self, tmp_path, monkeypatch, capsys):
        explorer = self._after(tmp_path, ["ok", "ok", "error"])
        seen = []
        monkeypatch.setattr("fastmdxplora.simulation.stopping.run_until_known",
                            lambda runs, *a, **k: seen.append([r.name for r in runs]))
        explorer._run_until_known(targets_of(RULE))
        assert seen == [["r0", "r1"]]
        assert "judges the 2 runs that finished" in capsys.readouterr().out

    def test_the_report_is_left_where_the_record_cannot_be_read(self, tmp_path, caplog):
        explorer = self._explorer(tmp_path, mode="sequential")
        explorer._report_again(tmp_path / "nowhere")
        assert "not written again" in caplog.text

    def test_refused_before_a_run_starts(self, tmp_path, monkeypatch):
        from fastmdxplora.batch.explorer import BatchExplorer
        from fastmdxplora.config.loader import ConfigError

        fake, given = self._fake_run([0.2])
        monkeypatch.setattr("fastmdxplora.batch.explorer._execute_run", fake)
        with pytest.raises(ConfigError) as raised:
            BatchExplorer(config_data=_study(), output_dir=tmp_path / "study").run()
        assert refusal_of(raised.value).code == "simulation.stopping.no_replicas"
        assert given == []

    def test_the_plan_refuses_it_too(self, tmp_path):
        from fastmdxplora.batch.explorer import BatchExplorer

        from fastmdxplora.config.loader import ConfigError

        with pytest.raises(ConfigError):
            BatchExplorer(config_data=_study(), output_dir=tmp_path / "study").dry_run()

    def test_a_call_that_drops_analysis_does_not_apply_it(self, tmp_path):
        """Validation reads the config's own phases; `explore()` can narrow
        them after, and the batch layer reads them again then."""
        from fastmdxplora.batch.explorer import BatchExplorer

        config = {**_study(), "sweep": {"simulation.random_seed": [1, 2]}}
        explorer = BatchExplorer(config_data=config, output_dir=tmp_path / "study")
        assert explorer._stopping_rule()
        explorer._raw["include_phase"] = ["setup", "simulation"]
        assert explorer._stopping_rule() is None

    def test_one_run_given_without_a_config_is_refused(self, tmp_path):
        from fastmdxplora import FastMDXplora

        with pytest.raises(StudyError) as raised:
            FastMDXplora(system="1UAO", output_dir=str(tmp_path / "x"),
                         options={"simulation": {"stop_when": RULE}})
        found = refusal_of(raised.value)
        assert found.code == "config.option.inapplicable"
        assert found.details["context"] == "a run given without a config"

    def test_a_piece_does_not_carry_the_rule(self, tmp_path):
        """The next segment is a length; left in, it would be judged as a
        study of one run and refused."""
        import yaml

        from fastmdxplora.simulation.resume import continuation_of

        study = tmp_path / "study"
        (study / "simulation").mkdir(parents=True)
        (study / "resolved_config.yml").write_text(yaml.safe_dump(
            {"simulation": {"duration_ns": 2, "stop_when": RULE}}), encoding="utf-8")
        (study / "simulation" / "checkpoint.chk").write_bytes(b"x")
        monkey = pytest.MonkeyPatch()
        monkey.setattr("fastmdxplora.simulation.runner.read_checkpoint_sidecar",
                       lambda _path: {"stage": "production", "step": 1_000_000})
        monkey.setattr("fastmdxplora.simulation.resume.trajectory_interval_of", lambda _s: None)
        try:
            plan = continuation_of(study, more_ns=1.0)
        finally:
            monkey.undo()
        assert plan.possible, plan.refusal
        assert "stop_when" not in plan.config["simulation"]


def test_a_real_study_is_run_until_its_ceiling(tmp_path):
    """A real study on tri-alanine: the first piece, a real extension, the
    join, the analyses again, and the report written once more with the
    record. Asked for more precision than its ceiling allows, it runs to
    the ceiling and says so."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    pytest.importorskip("mdtraj")
    import yaml

    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    pdb = tmp_path / "tri.pdb"
    pdb.write_text(TRI_ALANINE)
    config = {
        "systems": [{"id": "tri", "system": str(pdb)}],
        "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
        "simulation": {"platform": "CPU", "production_steps": 300, "nvt_steps": 100,
                       "npt_steps": 100, "timestep_fs": 2, "trajectory_interval_steps": 50,
                       "checkpoint_interval_steps": 100,
                       "stop_when": {"measures": [{"analysis": "rmsd", "standard_error": 1e-6}],
                                     "max_duration_ns": 0.0012,
                                     "independent_starts": "not_required"}},
        "analysis": {"include": ["rmsd"]},
        "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
    }
    study = tmp_path / "study"
    FastMDXplora(config_data=config, output_dir=str(study)).explore(check=True)
    record = json.loads((study / "stopping.json").read_text(encoding="utf-8"))
    assert record["outcome"] == "ceiling"
    # How much each round adds follows the dynamics; that production only
    # grows, and ends at the ceiling, does not. It went down once, when the
    # first piece was miscounted after the second extension.
    produced = [r["production_ns"] for r in record["rounds"]]
    assert produced[0] == 0.0006 and produced[-1] == 0.0012
    assert all(later > earlier for earlier, later in zip(produced, produced[1:]))
    assert (study / "segment-001").is_dir() and (study / "joined" / "production.dcd").is_file()
    report = (study / "report" / "report.md").read_text(encoding="utf-8")
    assert "## How long it ran, and why" in report
    assert f"| {len(produced)} | 0.0012 | " in report and "stopped at the ceiling" in report
    # Writing the report again leaves the record of what the study ran as
    # the last extension left it, not as one step that wrote a report.
    resolved = yaml.safe_load((study / "resolved_config.yml").read_text(encoding="utf-8"))
    assert resolved.get("include_phase") != ["report"]
    assert resolved["simulation"]["resume_from"].endswith("checkpoint.chk")
