"""The methods say how each mean and its error were determined, and why the
production is as long as it is.

They said how the system was prepared and simulated and nothing about the
analysis behind every number in the report: where averaging started, how the
correlation between frames entered the error, when no error was given, and,
for a study run until it knew, the rule that set its length. A reader
repeating the study could repeat the dynamics and not the numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from fastmdxplora.report.context import PhaseContext
from fastmdxplora.report.methods import methods_paragraphs
from fastmdxplora.statistics import mean_record


def _ar1(n: int, seed: int, phi: float = 0.95, sigma: float = 0.05) -> np.ndarray:
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, sigma * math.sqrt(1.0 - phi ** 2), n)
    return lfilter([1.0], [1.0, -phi], noise, zi=[phi * rng.normal(0.0, sigma)])[0]


def _record(series: np.ndarray, **extra) -> dict:
    record = {**mean_record(series), **extra}
    if "not_a_measurement" in record:
        record["not_a_measurement"] = str(record["not_a_measurement"])
    return record


def _relaxing(seed: int, n: int = 4000) -> np.ndarray:
    return 0.2 + 0.1 * np.exp(-np.arange(n) / 200.0) + _ar1(n, seed)


def _analysis(**kwargs) -> str:
    text = methods_paragraphs(Path("."), {}, {}, **kwargs)
    [paragraph] = [p for p in text.split("\n\n") if p.startswith("**Analysis.**")]
    return paragraph


def test_the_estimator_is_said_with_the_thresholds_it_applies():
    from fastmdxplora import statistics

    said = _analysis(means={"rmsd": _record(_relaxing(1), unit="nm"),
                            "rg": _record(1.1 + _ar1(4000, 2), unit="nm")})
    assert "Each per-frame quantity (`rg` and `rmsd`) was averaged" in said
    assert "Chodera, J. Chem. Theory Comput. 2016, 12, 1799" in said
    assert "Geyer's initial positive sequence (Stat. Sci. 1992, 7, 473)" in said
    assert "autocorrelation taken about the sample mean" in said
    # Read from the module that applies them.
    assert f"at least {100 * (1 - statistics.EQUILIBRATION_TOLERANCE):g}% of the most" in said
    assert f"at least {statistics.RESOLVED_SAMPLES:g} times their own statistical" in said
    assert f"least {statistics.MINIMUM_EFFECTIVE_SAMPLES:g} independent samples" in said
    assert "unless the discard at least doubled the independent samples" in said
    assert "frames analysed" in said


def test_one_quantity_is_said_as_one():
    said = _analysis(means={"rmsd": _record(_relaxing(3))})
    assert "The per-frame quantity `rmsd` was averaged" in said


def test_records_from_an_earlier_estimator_are_not_described_as_this_one():
    old = {k: v for k, v in _record(_relaxing(4)).items() if k != "degrees_of_freedom"}
    said = _analysis(means={"rmsd": old})
    assert "recorded by an earlier release" in said
    assert "Geyer" not in said


def test_what_was_discarded_is_said():
    flat = _record(0.2 + _ar1(4000, 5))
    for discard, expected in (
            ((0, 0), "No equilibration period was detected in the 4,000 frames analysed."),
            ((300, 300), "The first 300 of the 4,000 frames analysed were discarded as "
                         "equilibration in each."),
            ((100, 700), "The equilibration periods discarded ran from 100 to 700 of the "
                         "4,000 frames analysed.")):
        means = {name: {**flat, "discard": d} for name, d in zip(("a", "b"), discard)}
        assert expected in _analysis(means=means)


def test_a_start_shared_with_replicas_is_said():
    said = _analysis(means={"rmsd": _record(_relaxing(6), start_shared_with_replicas=600)})
    assert ("it was averaged from no earlier than frame 600, the start detected on the "
            "replicas' frame-by-frame average") in said


def test_a_mean_without_an_error_is_named():
    short = _record(0.2 + _ar1(150, 7))
    assert short.get("not_a_measurement")
    said = _analysis(means={"rmsd": _record(_relaxing(8)), "sasa": short})
    assert "No error is given for `sasa`; the report says why." in said


def _rule(runs: list[str], outcome: str = "met") -> dict:
    return {"targets": [{"analysis": "rmsd", "standard_error": 0.01, "relative_error": None}],
            "max_duration_ns": 100.0, "independent_starts": "not_required" if len(runs) == 1
            else "required", "runs": runs, "outcome": outcome,
            "rounds": [{"production_ns": 10.0}, {"production_ns": 25.0}]}


def test_a_single_run_until_it_knew_says_its_rule():
    said = _analysis(means={"rmsd": _record(_relaxing(9), unit="nm")}, stopping=_rule(["study"]))
    assert ("until rmsd to ±0.01 nm was determined, with at most 100 ns of production."
            in said)
    assert "Student's t at its own degrees of freedom" in said
    assert "(68.3% of the time)" in said
    assert "accepted only once it rested on 50 independent samples" in said
    assert "It stopped after 2 rounds, at 25 ns of production, with each determined" in said


def test_replicas_until_they_knew_say_theirs():
    said = _analysis(means={}, stopping=_rule(["a", "b", "c"], outcome="ceiling"))
    assert "determined in 3 replicas that agree within their errors" in said
    assert "with at most 100 ns of production per run" in said
    assert "Cochran's Q over its degrees of freedom at most 2" in said
    assert "It stopped at the ceiling after 2 rounds, at 25 ns of production per run" in said


def test_a_study_with_no_mean_and_no_rule_has_no_analysis_paragraph():
    assert "**Analysis.**" not in methods_paragraphs(Path("."), {}, {"parameters": {
        "production_steps": 1000, "timestep_fs": 2.0}})


def _study(root: Path, *, rule: dict | None = None) -> Path:
    where = root / "analysis" / "rmsd"
    where.mkdir(parents=True)
    (where / "options.json").write_text(json.dumps(
        {"findings": {"mean": _record(_relaxing(10), unit="nm")}}), encoding="utf-8")
    (root / "analysis" / "broken").mkdir()
    (root / "analysis" / "broken" / "options.json").write_text("[1, 2]", encoding="utf-8")
    if rule is not None:
        (root / "stopping.json").write_text(json.dumps(rule), encoding="utf-8")
    return root


def _methods(root: Path) -> str:
    from fastmdxplora.report.document import _methods_section

    return _methods_section(root, PhaseContext(simulation_present=True))


def test_the_report_reads_each_analysis_and_the_rule(tmp_path):
    text = _methods(_study(tmp_path, rule=_rule(["study"])))
    assert "**Analysis.** The per-frame quantity `rmsd` was averaged" in text
    assert "until rmsd to ±0.01 nm was determined" in text


def test_one_of_a_campaign_s_runs_reads_the_campaign_s_rule(tmp_path):
    (tmp_path / "stopping.json").write_text(json.dumps(_rule(["r0", "r1"])), encoding="utf-8")
    named = _study(tmp_path / "runs" / "r0")
    other = _study(tmp_path / "runs" / "elsewhere")
    assert "in 2 replicas that agree" in _methods(named)
    assert "replicas that agree" not in _methods(other)
