"""A mean withheld as drifting between a joined run's segments is said so.

The report called such a mean equilibrated ("yes", the check passed, no
word of drift) because it recognised a withheld mean as still moving by the
words "still drifting", which the joined refusal does not use; it now reads
the record's code. The test fits each segment mean against where it sits in
time, not its index, so a drift steady in time over segments of 500, 500 and
4,000 frames is a straight line and is called. And the Overview's card leads
with the verdict, its p and its remedy, while its lead names the mean.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.statistics import mean_record, summarise_segments


def _ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.normal(0.0, np.sqrt(1.0 - phi * phi), n)
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + noise[i]
    return x


def _climbing(pieces: int, length: int = 400, step: float = 0.4, seed: int = 3):
    """A joined run whose pieces each sit ``step`` frame SDs above the last,
    stationary within each: the shape of an energy climbing piece by piece
    under a weak thermostat (Reviewer 2's tri-alanine study, 100 rows a
    piece, -22,360, -22,321, -22,259, ... kJ/mol). Small against the
    frames' spread, so the thirds of the frames averaged do not move by
    twice it."""
    rng = np.random.default_rng(seed)
    values = -22300.0 + 20.0 * (_ar1(pieces * length, 0.5, rng)
                                + np.repeat(step * np.arange(pieces), length))
    joins = [length * i for i in range(1, pieces)]
    return values, joins


def _saved(record: dict) -> dict:
    """The record as an analysis saves it and a reader loads it."""
    return json.loads(json.dumps(record))


def _study(tmp_path: Path, values: np.ndarray, joins: list[int]) -> Path:
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    with (root / "simulation" / "energy.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Potential Energy (kJ/mole)", "Temperature (K)"])
        for value in values:
            writer.writerow([f"{value:.4f}", "300.0"])
    record = _saved(mean_record(values, joins=joins))
    folder = root / "analysis" / "thermodynamics"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({"analysis": "thermodynamics", "findings": {
        "thermodynamics": {"samples": int(values.size),
                           "potential_energy": {**record, "units": "kJ/mol"}}}}),
        encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": 0.2},
        "results": {"thermodynamics": {"status": "ok"}}}), encoding="utf-8")
    return root


@pytest.mark.parametrize("pieces", [4, 6])
def test_the_report_does_not_call_a_mean_drifting_between_segments_equilibrated(
        tmp_path, pieces) -> None:
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(pieces)
    record = _saved(mean_record(values, joins=joins))
    # The record is withheld as drifting between the segments (three or four
    # by the slope test, five or more by the order test).
    assert record["drift_test"] == ("slope_against_errors_and_scatter" if pieces < 5
                                    else "permutation")
    said = _convergence_section(_study(tmp_path, values, joins))
    row = next(line for line in said.splitlines() if line.startswith("| potential energy |"))
    assert row.rstrip().endswith("| no |"), row
    assert ("| Each observable equilibrates before it is averaged | **failed** | "
            "still moving: potential energy |") in said
    finding = next((line for line in said.splitlines()
                    if line.startswith("- Not equilibrated at the scale of the whole run: "
                                       "potential energy.")), "")
    assert "Drifting between its joined segments (p " in finding, said
    assert "the remedy is a longer run" in finding
    assert "Still moving in one direction" not in said


def test_a_record_saved_before_its_code_was_is_read_by_its_drift_test() -> None:
    from fastmdxplora.report.convergence import assess_run

    values, joins = _climbing(4)
    record = _saved(mean_record(values, joins=joins))
    record.pop("withheld_code", None)
    assessed = assess_run({"potential_energy": values},
                          records={"potential_energy": record})
    assert assessed["observables"]["potential_energy"]["equilibrated"] is False
    check = next(c for c in assessed["checks"] if c["check"] == "equilibrated")
    assert check["passed"] is False
    assert any(line.startswith("Not equilibrated at the scale of the whole run")
               for line in assessed["findings"])


def test_a_drift_steady_in_time_over_unequal_segments_is_called() -> None:
    # Segments of 500, 500 and 4,000 frames and a ramp linear in time: the
    # three means sit at the centres of what each averages, far off a
    # straight line in segment index, so that fit was refused and the drift
    # passed; in time they lie on one.
    lengths = [500, 500, 4000]
    joins = [500, 1000]
    n = sum(lengths)
    called = 0
    runs = 60
    for seed in range(runs):
        rng = np.random.default_rng(seed)
        x = _ar1(n, 0.8, rng) + 2.4 * np.linspace(0.0, 1.0, n)
        _, why = summarise_segments(x, joins)
        called += getattr(getattr(why, "refusal", None), "code", "") == "analysis.sampling.drifting"
    # Fitted against index: 0 of 60. Against time: 9 of 60 (counted; the
    # rest are mostly withheld for the long segment's own trend).
    assert called >= 6, called


def test_the_line_s_change_is_said_over_its_span_in_time() -> None:
    # Segment means on a line in time over unequal segments (0.2, 0.6 and
    # 2.4 frame SDs at the centres of 500, 500 and 4,000 frames): the line's
    # change is its slope over the span of the centres, 2.2, as the first to
    # last mean says, not its slope a segment times two.
    lengths = [500, 500, 4000]
    rng = np.random.default_rng(11)
    x = _ar1(sum(lengths), 0.8, rng)
    x += np.repeat(np.array([250.0, 750.0, 3000.0]) * 4.0 / 5000.0, lengths)
    _, why = summarise_segments(x, [500, 1000])
    assert why is not None and why.refusal.code == "analysis.sampling.drifting", why
    said = str(why)
    change = float(said.split("changes by ")[1].split(" ")[0])
    span = float(said.split("first to last mean ")[1].split(")")[0])
    assert change == pytest.approx(2.2, rel=0.1)
    assert change == pytest.approx(span, rel=0.05)


def test_the_refusal_leads_with_its_verdict_p_and_remedy() -> None:
    from fastmdxplora.gui.overview_view import _in_a_line

    values, joins = _climbing(4)
    record = _saved(mean_record(values, joins=joins))
    line = _in_a_line(record["not_a_measurement"])
    assert line.startswith("Drifting between its joined segments (p ")
    assert line.endswith("so the remedy is a longer run.")
    # Two lines of the card's width at 390 px hold it.
    assert len(line) <= 100, line


def test_the_overview_s_lead_names_the_mean_drifting(tmp_path) -> None:
    from fastmdxplora.gui.overview_view import overview_payload

    values, joins = _climbing(4)
    payload = overview_payload(_study(tmp_path, values, joins))
    tile = next(t for t in payload["tiles"] if t["label"] == "Potential energy")
    assert tile["determined"] is False
    assert tile["reason"].startswith("Drifting between its joined segments (p ")
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run") in payload["lead"]["said"], payload["lead"]


@pytest.mark.parametrize("scatter, count, plain", [
    (2.5, 3, True),   # Q 5.0 on 2 degrees of freedom: p 0.08
    (3.2, 3, False),  # Q 6.4: p 0.04
    (2.5, 4, True),   # Q 7.5 on 3: p 0.06
])
def test_the_stronger_qualification_is_said_only_beyond_chance(scatter, count, plain) -> None:
    # Equal segment errors and offsets in no order whose Cochran's Q is
    # ``scatter`` times count - 1: a ratio above 2 arises by chance in 1 of 7
    # stationary runs of three segments, so "This does not establish
    # stationarity" is said only where Q stands against its chi-square.
    from fastmdxplora.statistics import Equilibrated, heterogeneity_ratio

    shape = np.array([1.0, -1.0, 0.0, 0.0][:count]) if count == 3 else \
        np.array([1.0, -1.0, -1.0, 1.0])
    shape = shape - shape.mean()
    error = 0.01
    weights = np.full(count, 1.0 / error**2)
    shape *= np.sqrt(scatter / heterogeneity_ratio(shape, weights))
    assert heterogeneity_ratio(shape, weights) == pytest.approx(scatter)
    pieces = [Equilibrated(discard=0, inefficiency=1.0, effective_samples=400.0,
                           mean=float(m), standard_error=error, standard_deviation=0.2)
              for m in shape]
    from unittest import mock

    with mock.patch("fastmdxplora.statistics.summarise",
                    side_effect=[(p, None) for p in pieces]):
        pooled, why = summarise_segments(np.zeros(400 * count), [400 * i for i in range(1, count)])
    assert why is None and pooled is not None
    assert pooled.qualification.startswith(f"The segment means scatter {scatter:.1f} times")
    assert ("This does not establish stationarity" in pooled.qualification) is not plain


def test_two_segments_that_disagree_are_not_said_to_be_free_of_drift() -> None:
    # Two means have no order to test: 177.8 then 185.5 K was said "That is
    # not drift".
    rng = np.random.default_rng(7)
    values = np.concatenate([_ar1(2000, 0.5, rng), _ar1(2000, 0.5, rng) + 1.5])
    pooled, why = summarise_segments(values, [2000])
    assert why is None and pooled is not None and pooled.qualification
    assert "not drift" not in pooled.qualification
    assert "two segments cannot tell a drift from offsets between them" in pooled.qualification


#: The smallest steady drift, in pooled errors first segment to last, the
#: rule calls in 80% of runs of equal segments (counted by
#: tests/_short_segment_drift_check.py).
UNSEEN = {3: 41, 4: 16}


def _unseen_said(values, joins, pieces):
    pooled, _ = summarise_segments(values, joins)
    amount = float(f"{UNSEEN[pieces] * pooled.standard_error:.2g}")
    return (f"with {pieces} segments, a steady drift smaller than about {UNSEEN[pieces]} "
            f"times the pooled error ({amount:g} kJ/mol from the first segment's mean to "
            "the last's) could go unseen")


@pytest.mark.parametrize("pieces", [3, 4])
def test_three_or_four_segments_not_called_drifting_say_how_large_a_drift_could_go_unseen(
        tmp_path, pieces) -> None:
    # The rule holds its 5% false rate at three and four segments, so it
    # calls only a large drift there: a mean it pools is said with how
    # large a steady drift that few could miss, on every reader of it.
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import overview_payload
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(pieces, step=0.0)
    record = _saved(mean_record(values, joins=joins))
    assert not record.get("not_a_measurement")
    said = _unseen_said(values, joins, pieces)
    root = _study(tmp_path, values, joins)
    tile = next(t for t in overview_payload(root)["tiles"] if t["label"] == "Potential energy")
    row = next(q for r in overview_of(root)["rows"] for q in r["quantities"]
               if q.get("label") == "Potential energy")
    assert tile["determined"] and said in tile["noted"], tile
    # The same text on the Overview's card and the Analysis page.
    assert tile["noted"] == row["noted"]
    assert f"Potential energy: {said[:1].upper()}{said[1:]}." in _convergence_section(root)
    assert said in answer_from_the_records(root, "found")


@pytest.mark.parametrize("pieces, step", [(5, 0.0), (4, 0.4), (2, 0.0)])
def test_no_unseen_drift_is_said_at_five_where_drift_is_called_or_at_two(
        tmp_path, pieces, step) -> None:
    from fastmdxplora.gui.overview_view import overview_payload

    values, joins = _climbing(pieces, step=step)
    root = _study(tmp_path, values, joins)
    tile = next(t for t in overview_payload(root)["tiles"] if t["label"] == "Potential energy")
    assert "could go unseen" not in (tile["noted"] or "")
    assert "drift_unseen" not in _saved(mean_record(values, joins=joins))
    # The control: at four segments held level, it is said.
    values, joins = _climbing(4, step=0.0)
    assert "drift_unseen" in _saved(mean_record(values, joins=joins))


def test_the_unseen_drift_is_the_counted_one() -> None:
    # One derivation: the constant the note says is the helper's count.
    from fastmdxplora import statistics
    from tests._short_segment_drift_check import unseen_in_pooled_errors

    for pieces in (3, 4):
        counted = unseen_in_pooled_errors([1] * pieces)["in_pooled_errors"]
        assert counted == UNSEEN[pieces]
        assert getattr(statistics, "DRIFT_UNSEEN_IN_POOLED_ERRORS", {}).get(pieces) == counted
