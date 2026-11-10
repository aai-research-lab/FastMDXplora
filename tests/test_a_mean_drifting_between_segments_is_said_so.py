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
    """The note's sentence for a run of segments all tested, its amount the
    X counted for their own errors and centres times the error of the
    segments' inverse-variance pooled mean, times their scatter about the
    line as the note says it, where that reads above 1.0; the error
    unrounded, said to two figures or to as many more as multiply out."""
    from fastmdxplora import statistics
    from fastmdxplora.statistics import summarise

    edges = [0, *joins, len(values)]
    pieces_ = [summarise(values[a:b], minimum_effective_samples=0.0)[0]
               for a, b in zip(edges, edges[1:])]
    means = np.array([p.mean for p in pieces_])
    w = 1 / np.array([p.standard_error for p in pieces_]) ** 2
    x = np.array([a + p.discard + (b - a - p.discard - 1) / 2
                  for (a, b), p in zip(zip(edges, edges[1:]), pieces_)])
    fit = np.polyfit(x, means, 1, w=np.sqrt(w))
    plain = means - np.polyval(np.polyfit(x, means, 1), x)
    # The larger of the weighted line's chi-square and the unweighted line's
    # scatter over the means' mean error variance, per degree of freedom.
    ratio = max(float(w @ (means - np.polyval(fit, x)) ** 2),
                float(plain @ plain) / float(np.mean(1 / w))) / (pieces - 2)
    error = 1 / np.sqrt(w.sum())
    # Found by name, so a version without it says the equal pieces' figure
    # and fails on the text rather than at import.
    layout = getattr(statistics, "drift_unseen_in_pooled_errors", lambda *_: UNSEEN[pieces])
    counted = layout(list(1 / np.sqrt(w)), list(x))
    scale = float(f"{np.sqrt(ratio):.1f}") if ratio > 1 else 1.0

    def figures(v, n):
        r = float(f"{v:.{n}g}")
        return f"{r:,.{max(0, n - 1 - int(np.floor(np.log10(r))))}f}"

    def two(v):
        return figures(v, 2)

    # From the error unrounded, the error said to as many figures as
    # multiply out to the figure as said.
    amount = counted * error * scale
    error_said = next(figures(error, n) for n in range(2, 18) if two(
        counted * scale * float(figures(error, n).replace(",", ""))) == two(amount))

    basis = f"{counted} times the error of their pooled mean, {error_said} kJ/mol"
    if scale > 1.0:
        basis += (f", times {scale:.1f} for their scatter about a straight line "
                  f"beyond that error, a scale estimated from only {pieces} means")
    return (f"With {pieces} segments, a steady drift smaller than about {two(amount)} kJ/mol "
            f"from the first segment's mean to the last's could go unseen ({basis})")


@pytest.mark.parametrize("pieces", [3, 4])
def test_three_or_four_segments_not_called_drifting_say_how_large_a_drift_could_go_unseen(
        tmp_path, pieces) -> None:
    # The rule holds its 5% false rate at three and four segments, so it
    # calls only a large drift there: a mean it does not call is said with
    # how large a steady drift that few could miss, on every reader of it.
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
    assert tile["determined"] and tile["unseen"] == said, (tile, said)
    # The same text on the Overview's card and the Analysis page.
    assert row["unseen"] == said and row["noted"] == said
    assert f"Potential energy: {said}." in _convergence_section(root)
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
    # One derivation: the figure a record works out for equal pieces is the
    # helper's count, which the docs give.
    from fastmdxplora import statistics
    from tests._short_segment_drift_check import unseen_in_pooled_errors

    for pieces in (3, 4):
        counted = unseen_in_pooled_errors([1] * pieces)["in_pooled_errors"]
        assert counted == UNSEEN[pieces]
        assert getattr(statistics, "DRIFT_UNSEEN_IN_POOLED_ERRORS", {}).get(pieces) == counted
        assert _layout_figure([1.0] * pieces, list(np.arange(pieces) + 0.5)) == counted


def _layout_figure(errors, centres):
    """The figure a record of these tested errors and centres says, in
    pooled errors, its scatter within its errors."""
    from fastmdxplora.statistics import _drift_unseen

    unseen = _drift_unseen({"segments": len(errors), "tested": len(errors), "left_out": "",
                            "called": False, "pooled_error": 1.0, "line_scatter": 0.5,
                            "lengths": [1] * len(errors), "errors": errors,
                            "centres": centres})
    return unseen["counted_in_pooled_errors"]


@pytest.mark.parametrize("lengths, counted", [
    ([1, 1, 1], 41), ([1, 1, 1, 1], 16), ([500, 500, 4000], 69), ([4000, 500, 500], 69),
    ([3000, 500, 3000], 71), ([500, 4000, 500], 48), ([500, 500, 500, 4000], 24),
    ([1000, 1000, 1000, 5000], 21)])
def test_the_figure_is_the_record_s_own_layout_s(lengths, counted) -> None:
    # The note gave 41 or 16 pooled errors whatever the layout: over pieces
    # of 500, 500 and 4,000 frames the rule calls four in five drifts only
    # of 69 (the docs' table, counted by the helper), so a drift as large as
    # the figure was called in about half the runs. Each error as one over
    # the root of its length, each mean at its centre in time.
    lengths = np.asarray(lengths, dtype=float)
    errors = list(np.sqrt(lengths.min() / lengths))
    assert _layout_figure(errors, list(np.cumsum(lengths) - lengths / 2)) == counted


def test_a_record_of_unequal_pieces_says_its_layout_s_figure_and_the_same_text_twice() -> None:
    # A stationary run in pieces of 500, 500 and 4,000 frames: its figure is
    # worked out for its own tested errors and centres in time when the
    # record is written, near the 69 counted for that layout, not 41.
    from fastmdxplora.statistics import drift_unseen_said

    rng = np.random.default_rng(7)
    values = 10.0 + _ar1(5000, 0.5, rng)
    first = _saved(mean_record(values, joins=[500, 1000]))
    unseen = first["drift_unseen"]
    assert 60 <= unseen["counted_in_pooled_errors"] <= 80, unseen
    said = drift_unseen_said(first, "nm")
    assert f"({unseen['counted_in_pooled_errors']} times the error of their pooled mean" in said
    assert "as unequal as these" not in said
    again = _saved(mean_record(values, joins=[500, 1000]))
    assert again == first and drift_unseen_said(again, "nm") == said


@pytest.mark.parametrize("question", ["long_enough", "strengthen"])
def test_the_records_answer_names_a_mean_drifting_between_segments(tmp_path, question) -> None:
    # Told apart by its record's code, not its words: the answer said no
    # figure was recorded and that analysing again records it, which it
    # does not for a drift between segments.
    from fastmdxplora.gui.records_answer import answer_from_the_records

    values, joins = _climbing(4)
    said = answer_from_the_records(_study(tmp_path, values, joins), question)
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run; no record says how much longer.") in said, said
    assert "Analysing it again with this release records it" not in said


def test_what_would_support_it_names_a_mean_drifting_between_segments(tmp_path) -> None:
    # It named only the means withheld for want of sampling; a drifting one
    # had its remedy only in its finding line above.
    from fastmdxplora.report.document import _convergence_section

    values, joins = _climbing(4)
    said = _convergence_section(_study(tmp_path, values, joins))
    support = next((line for line in said.splitlines()
                    if line.startswith("**What would support it.**")
                    or line.startswith("Potential energy is drifting")), "")
    assert ("Potential energy is drifting between the joined run's segments, so the "
            "remedy is a longer run; no record says how much longer.") in support, said


def test_the_report_s_first_paragraph_gives_an_extended_study_s_joined_length() -> None:
    # It read the first piece's record alone: "simulated for 100 ps" of 300.
    import json

    from fastmdxplora.report.document import _study_in_one_paragraph
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    root = _extended()
    record = root / "simulation" / "simulation_parameters.json"
    sim = json.loads(record.read_text(encoding="utf-8"))
    sim["parameters"]["duration_ns"] = 0.1
    record.write_text(json.dumps(sim), encoding="utf-8")
    said = _study_in_one_paragraph(root)
    assert "simulated for 300 ps in 3 pieces at 300 K" in said, said
    assert "for 100 ps" not in said


def _real(name: str):
    """A real joined run's series and joins (``tests/data/drift_short_real_runs.json``):
    a stationary tri-alanine study's temperature at 4 pieces of 20 ps, and
    a drifting one's (its thermostat at 300 K, friction 0.002/ps)
    temperature and RMSD at 3 pieces."""
    data = json.loads((Path(__file__).parent / "data" / "drift_short_real_runs.json")
                      .read_text(encoding="utf-8"))[name]
    return np.array(data["values"], dtype=float), data["joins"]


def test_a_determined_mean_whose_joined_summary_was_withheld_says_what_was_tested() -> None:
    # A real stationary run at 4 pieces: its first piece holds 15 independent
    # samples, too few to resolve its correlation, so the joined summary is
    # withheld; the drift test ran on the other 3 and did not call drift, and
    # the whole series' mean stands determined, 301.0 +/- 1.4 K. It said
    # nothing of what the test could miss.
    from fastmdxplora.statistics import withheld_reason

    values, joins = _real("stationary_4_pieces_temperature_K")
    record = _saved(mean_record(values, joins=joins))
    assert withheld_reason(record) is None
    said = _unseen_of(record, "K")
    assert said.startswith(
        "With 3 of its 4 segments tested (1 left out, numbered from zero: 0, its correlation "
        "not resolved), a steady drift smaller than about 35 K from the first segment's mean "
        "to the last's could go unseen (44 times the error of their pooled mean, 0.79 K)"), said


def test_a_determined_mean_with_too_few_segments_tested_says_drift_was_not_tested(
        tmp_path) -> None:
    # A real drifting run at 3 pieces: RMSD 0.0065, 0.0159, 0.0208 nm by
    # piece, its middle piece unresolved, so 2 could be tested; the mean
    # stood determined, 0.02087 +/- 0.00068 nm, with no word of drift.
    from fastmdxplora.gui.analysis_overview import overview_of

    values, joins = _real("drifting_3_pieces_rmsd_nm")
    record = _saved(mean_record(values, joins=joins))
    said = _unseen_of(record, "nm")
    assert said == ("Drift between its 3 segments was not tested: only 2 could be (1 left out, "
                    "numbered from zero: 1, its correlation not resolved); longer segments "
                    "would let it be"), said
    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": {**record, "unit": "nm"}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"results": {"rmsd": {"status": "ok"}}}), encoding="utf-8")
    row = next(q for r in overview_of(root)["rows"] for q in r["quantities"])
    assert row["determined"] and row["unseen"] == said


def test_the_figure_is_scaled_where_the_means_scatter_beyond_their_errors() -> None:
    # The real drifting run's temperature at 3 pieces, 177.79, 185.45 and
    # 186.61 K over errors of 0.6 to 0.7 K: their scatter about the line is
    # 3.4 times their errors (Q 11.9 on 1 degree of freedom), and there the
    # rule is decided by that scatter. Said "15 K" from errors that do not
    # hold; the rule calls four in five drifts only of about 55 K, and the
    # scale rests on 3 means. Its own errors and centres in time give 42
    # pooled errors.
    values, joins = _real("drifting_3_pieces_temperature_K")
    record = _saved(mean_record(values, joins=joins))
    said = _unseen_of(record, "K")
    assert said == ("With 3 segments, a steady drift smaller than about 55 K from the first "
                    "segment's mean to the last's could go unseen (42 times the error of their "
                    "pooled mean, 0.373 K, times 3.5 for their scatter about a straight line "
                    "beyond that error, a scale estimated from only 3 means)"), said


def _unseen_of(record, unit):
    """The note through the one reader every page uses, found by name so a
    version without it says nothing rather than failing to import."""
    from fastmdxplora import statistics

    reader = getattr(statistics, "drift_unseen_said", None)
    return reader(record, unit) if reader else ""


def _with_an_image_note(record: dict) -> dict:
    return {**record, "solute_image": {"frames_used": 1200, "frames_under_cutoff": 300,
                                       "cutoff_nm": 1.0, "counted_over": "frames"}}


def test_the_card_says_the_drift_note_whole_beside_an_image_note(tmp_path) -> None:
    # The card's note took two lines and cut the drift note before "could go
    # unseen", and an image note beside it would have hidden it whole.
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    values, joins = _climbing(3, step=0.0)
    root = _study(tmp_path, values, joins)
    options = root / "analysis" / "thermodynamics" / "options.json"
    document = json.loads(options.read_text(encoding="utf-8"))
    energy = document["findings"]["thermodynamics"]["potential_energy"]
    document["findings"]["thermodynamics"]["potential_energy"] = _with_an_image_note(energy)
    options.write_text(json.dumps(document), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    seen = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            for width in (1440, 390):
                for scheme in ("light", "dark"):
                    page = browser.new_page(viewport={"width": width, "height": 900},
                                            color_scheme=scheme)
                    page.set_default_timeout(60000)
                    page.goto(session.url + "#overview", wait_until="domcontentloaded")
                    page.wait_for_selector("#overview-tiles .overview-tile")
                    seen.append((width, scheme, page.evaluate("""() => {
                      const tile = [...document.querySelectorAll('#overview-tiles .overview-tile')]
                        .find((t) => t.textContent.includes('Potential energy'));
                      const note = tile && tile.querySelector('.tile-unseen');
                      const image = tile && tile.querySelector('.tile-noted');
                      const whole = (el) => !!el && el.offsetParent !== null
                        && el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1;
                      return {note: note ? note.innerText : '', whole: whole(note),
                              image: image ? image.innerText : ''};
                    }""")))
                    page.close()
            browser.close()
    finally:
        session.server.shutdown()
    for width, scheme, found in seen:
        assert found["whole"] and "could go unseen" in found["note"] and \
            " times the error of their pooled mean" in found["note"] and \
            found["note"].endswith(")."), (width, scheme, found)
        assert found["image"].startswith("may be biased"), (width, scheme, found)


def _rmsd_study(tmp_path: Path, record: dict) -> Path:
    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": {**record, "unit": "nm"}}}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"results": {"rmsd": {"status": "ok"}}}), encoding="utf-8")
    return root


def test_the_agent_s_context_its_chips_and_compare_carry_the_note(tmp_path) -> None:
    # The Agent's own context for the AI model, its citation chips and
    # Compare gave a mean of three segments as determined with the image
    # note alone.
    from fastmdxplora.gui.agent_panel import _results_summary
    from fastmdxplora.gui.citations import cited_findings
    from fastmdxplora.gui.runs_compared import _measure

    values, joins = _climbing(3, step=0.0)
    values = 0.2 + (values + 22300.0) / 2000.0
    record = {**_saved(mean_record(values, joins=joins)), "unit": "nm"}
    said = _unseen_of(record, "nm")
    assert "could go unseen" in said
    root = _rmsd_study(tmp_path, record)
    assert said in _results_summary(root)
    chips = cited_findings("The RMSD is near 0.2 nm.", root)
    assert chips and said in chips[0]["noted"], chips
    measure = _measure("rmsd", [{"run_id": "a"}], {"a": {"rmsd": {"mean": record}}},
                       False, {"a": "a"})
    assert measure["cells"][0].get("unseen") == said, measure["cells"][0]


def test_the_first_paragraph_does_not_give_the_first_piece_s_length_for_a_piece_linked_out(
        monkeypatch) -> None:
    # A piece outside the study's folder is not read; the paragraph gave the
    # first piece's length as the study's.
    from fastmdxplora.refusals import OutsideWorkspace
    from fastmdxplora.report.document import _study_in_one_paragraph
    from fastmdxplora.simulation import resume
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    root = _extended()
    record = root / "simulation" / "simulation_parameters.json"
    sim = json.loads(record.read_text(encoding="utf-8"))
    sim["parameters"]["duration_ns"] = 0.1
    record.write_text(json.dumps(sim), encoding="utf-8")

    def outside(_study):
        raise OutsideWorkspace("A piece of this study is outside the workspace.")

    monkeypatch.setattr(resume, "extended_production", outside)
    said = _study_in_one_paragraph(root)
    assert "for 100 ps" not in said, said
    assert "one of them outside the study's folder, so how long is not said here" in said


def _a_record(change: float, error: float, errors: list, scatter: float) -> dict:
    """A determined mean whose drift test ran on 3 equal pieces and did not
    call drift, as its record saves it."""
    return {"mean": 1.0, "standard_error": 0.1, "effective_samples": 400.0,
            "n_frames": 300, "rules": 5, "unit": "nm",
            "drift_unseen": {"segments": 3, "tested": 3, "left_out": "",
                             "counted_in_pooled_errors": 41, "scatter_about_line": scatter,
                             "in_pooled_errors": change / error, "pooled_error": error,
                             "change": change}}


def test_equal_pieces_whose_errors_differ_say_their_own_figure() -> None:
    # Three pieces of equal length, the middle one's error twice the others'
    # (its correlation time longer): the rule's 80% point is 61 pooled
    # errors, not 41, and the record judged them equal by their lengths. And
    # the scale is the scatter the rule decides on: an imprecise middle mean
    # off the line carries little weight in the weighted line's chi-square.
    from fastmdxplora.statistics import _drift_unseen, _line_scatter

    errors = np.array([1.0, 2.0, 1.0])
    means = np.array([0.0, 6.0, 1.0])
    centres = np.array([50.0, 150.0, 250.0])
    scatter = _line_scatter(means, 1 / errors**2, centres)
    # The unweighted line's residual variance over the means' mean error
    # variance, which the rule's own-scatter half judges the slope against,
    # and the weighted line's chi-square per degree of freedom, smaller.
    plain = means - np.polyval(np.polyfit(centres, means, 1), centres)
    weighted = means - np.polyval(np.polyfit(centres, means, 1, w=1 / errors), centres)
    assert scatter == pytest.approx(float(plain @ plain) / np.mean(errors**2))
    assert scatter > float(weighted @ (weighted / errors**2))
    unseen = _drift_unseen({"segments": 3, "tested": 3, "left_out": "", "called": False,
                            "pooled_error": 0.5, "line_scatter": scatter,
                            "lengths": [100, 100, 100], "errors": errors.tolist(),
                            "centres": centres.tolist()})
    assert unseen["counted_in_pooled_errors"] == 61


def test_a_scale_that_reads_1_point_0_is_not_said_and_one_said_says_its_means() -> None:
    from fastmdxplora.statistics import drift_unseen_said

    plain = drift_unseen_said(_a_record(41 * 1.04 * 0.01, 0.01, [1, 1, 1], 1.08), "nm")
    assert "times 1.0" not in plain and plain.endswith("(41 times the error of their pooled "
                                                       "mean, 0.010 nm)"), plain
    scaled = drift_unseen_said(_a_record(41 * 2.0 * 0.01, 0.01, [1, 1, 1], 4.0), "nm")
    assert scaled.endswith("times 2.0 for their scatter about a straight line beyond that "
                           "error, a scale estimated from only 3 means)"), scaled


def test_the_figure_multiplies_out_from_the_basis_it_gives() -> None:
    # Where the scale is not said (1.04 reads 1.0) it is not applied: "about
    # 0.43 nm (41 times the error of their pooled mean, 0.010 nm)" was said.
    # Where it is said, the figure is the count times the scale as said.
    from fastmdxplora.statistics import _drift_unseen, drift_unseen_said

    plain = drift_unseen_said(_a_record(41 * 1.04 * 0.01, 0.01, [1, 1, 1], 1.08), "nm")
    assert "about 0.41 nm" in plain, plain
    scaled = drift_unseen_said(_a_record(41 * 2.34 * 0.01, 0.01, [1, 1, 1], 2.34 ** 2), "nm")
    assert "about 0.94 nm" in scaled and "times 2.3 for" in scaled, scaled
    unseen = _drift_unseen({"segments": 3, "tested": 3, "left_out": "", "called": False,
                            "pooled_error": 0.01, "line_scatter": 1.04 ** 2,
                            "lengths": [1, 1, 1], "errors": [1.0, 1.0, 1.0],
                            "centres": [0.5, 1.5, 2.5]})
    assert unseen["change"] == pytest.approx(0.41), unseen


def test_the_figure_is_worked_out_from_the_error_as_said() -> None:
    # "about 55 K (42 times ... 0.37 K, times 3.5)": 42 x 0.37 x 3.5 is 54.4,
    # the error being 0.373 K. Over errors spread on a log scale, the
    # figure is the product of the basis as said, in every note.
    import re

    from fastmdxplora.statistics import _two_figures, drift_unseen_said

    for scale, scatter in ((1.0, 1.0), (3.5, 3.5 ** 2), (1.1, 1.1 ** 2)):
        for error in np.geomspace(0.01, 100.0, 97):
            change = 42 * scale * error
            record = _a_record(change, float(error), [1, 1, 1], scatter)
            record["drift_unseen"]["counted_in_pooled_errors"] = 42
            said = drift_unseen_said(record, "K")
            figure, basis = re.search(r"about ([\d,.]+) K .*pooled mean, ([\d,.]+) K", said).groups()
            expected = 42 * scale * float(basis.replace(",", ""))
            assert figure == _two_figures(expected), (error, said)


def test_the_figure_is_the_unrounded_error_s_and_the_error_is_said_as_it_multiplies_out() -> None:
    # The figure was the count times the scale times the error to two
    # figures, so it moved by up to 5% with the quantity's units: an error
    # of 1.0499 (said 1.0) gave a figure 4.8% below its layout's 80% point.
    # Now the figure is the unrounded error's, the same in any units, and the
    # error is said to two figures, or to three (more, rarely) where two
    # would not multiply out to it.
    import re

    from fastmdxplora.statistics import _two_figures, drift_unseen_said

    def _figures(value, n):
        rounded = float(f"{value:.{n}g}")
        return f"{rounded:,.{max(0, n - 1 - int(np.floor(np.log10(rounded))))}f}"

    def said(counted, scale, error, unit):
        record = _a_record(counted * scale * error, error, [1, 1, 1], scale ** 2)
        record["drift_unseen"]["counted_in_pooled_errors"] = counted
        text = drift_unseen_said(record, unit)
        return re.search(rf"about ([\d,.]+) {unit} .*pooled mean, ([\d,.]+) {unit}",
                         text).groups()

    assert said(42, 3.5, 0.374, "K") == ("55", "0.374")
    assert said(41, 1.0, 1.0499, "nm") == ("43", "1.05")
    assert said(41, 1.0, 0.010499, "nm") == ("0.43", "0.0105")
    longer = 0
    for counted in (16, 41, 42, 69, 135):
        for scale in (1.0, 1.1, 2.3, 3.5):
            # Off round values, so no product sits on a rounding tie.
            for error in np.geomspace(0.001, 1000.0, 211) * 1.0137:
                figure, basis = said(counted, scale, float(error), "K")
                assert figure == _two_figures(counted * scale * error), (counted, error)
                value = float(basis.replace(",", ""))
                assert _two_figures(counted * scale * value) == figure, (counted, error)
                two = _figures(error, 2)
                if _two_figures(counted * scale * float(two.replace(",", ""))) == figure:
                    assert basis == two, (counted, scale, error, basis)
                else:
                    longer += 1
                    assert basis in [_figures(error, n) for n in range(3, 18)], (
                        counted, scale, error, basis)
                # The same record in units a thousand times smaller says the
                # same digits.
                small, _ = said(counted, scale, float(error) / 1000.0, "K")
                assert float(small.replace(",", "")) * 1000.0 == pytest.approx(
                    float(figure.replace(",", ""))), (counted, error)
    assert longer, "no error needed more than two figures"


@pytest.mark.parametrize("lengths, counted", [([1, 1, 1], 41), ([1, 1, 1, 1], 16),
                                              ([500, 500, 4000], 69), ([200, 200, 8000], 135)])
def test_the_layout_s_figure_is_found_by_halving_not_size_by_size(
        monkeypatch, lengths, counted) -> None:
    # One size at a time from 1: a layout of 135 pooled errors read 128
    # sizes of 10,000 sets each, over a second for each record's mean.
    from fastmdxplora import statistics

    read = []
    called = statistics._drift_called

    def counting(*args, **kwargs):
        read.append(1)
        return called(*args, **kwargs)

    monkeypatch.setattr(statistics, "_drift_called", counting)
    statistics._unseen_for_layout.cache_clear()
    lengths = np.asarray(lengths, dtype=float)
    centres = np.cumsum(lengths) - lengths / 2
    try:
        assert statistics.drift_unseen_in_pooled_errors(
            list(np.sqrt(lengths.min() / lengths)), list(centres)) == counted
    finally:
        statistics._unseen_for_layout.cache_clear()
    assert len(read) <= 2 * int(np.log2(counted)) + 2, len(read)


def test_a_chip_s_note_has_the_unit_its_value_has(tmp_path) -> None:
    # An analysis that recorded no unit: the chip gave its value in nm and
    # the note bare.
    from fastmdxplora.gui.citations import cited_findings

    record = _a_record(41 * 0.01, 0.01, [1, 1, 1], 1.0)
    record.pop("unit")
    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "options.json").write_text(json.dumps(
        {"analysis": "rmsd", "findings": {"mean": record}}), encoding="utf-8")
    chips = cited_findings("The RMSD is near 1 nm.", root)
    assert chips and "about 0.41 nm from the first segment's mean" in chips[0]["noted"], chips


def test_the_first_paragraph_keeps_the_temperature_where_a_piece_is_linked_out(
        monkeypatch) -> None:
    from fastmdxplora.refusals import OutsideWorkspace
    from fastmdxplora.report.document import _study_in_one_paragraph
    from fastmdxplora.simulation import resume
    from tests.test_an_extended_study_is_said_at_its_length import _extended

    def outside(_study):
        raise OutsideWorkspace("A piece of this study is outside the workspace.")

    monkeypatch.setattr(resume, "extended_production", outside)
    said = _study_in_one_paragraph(_extended())
    assert "simulated at 300 K in pieces, one of them outside the study's folder" in said, said


def test_the_chart_caption_says_the_drift_note_as_a_sentence(tmp_path) -> None:
    # "production mean, 72 independent samples; With 3 of its 4 segments
    # tested ..." with no full stop.
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    values, joins = _climbing(3, step=0.0)
    root = _study(tmp_path, values, joins)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_function("() => { const n = document.querySelector("
                                   "'[data-chart-note=\"potential_energy\"]');"
                                   " return n && n.textContent.includes('production mean'); }")
            caption = page.evaluate("() => document.querySelector("
                                    "'[data-chart-note=\"potential_energy\"]').textContent")
            browser.close()
    finally:
        session.server.shutdown()
    assert "independent samples. With 3 segments, a steady drift" in caption, caption
    assert caption.endswith(").") and "; With" not in caption, caption


def test_the_records_answer_and_compare_join_the_drift_note_as_a_sentence(tmp_path) -> None:
    # "...after the first 102 of 1200 frames; With 3 segments, ...": a
    # capital after a semicolon, fixed on the chart caption alone.
    from fastmdxplora.gui.records_answer import answer_from_the_records
    from fastmdxplora.workspace_studies import mean_said

    values, joins = _climbing(3, step=0.0)
    said = answer_from_the_records(_study(tmp_path, values, joins), "found")
    assert ". With 3 segments, a steady drift" in said, said
    assert "; With" not in said, said
    note = "With 3 segments, a steady drift smaller than about 51 kJ/mol"
    assert mean_said({"mean": 1.0, "error": 0.1, "unit": "nm", "noted": note}) == \
        f"1.00 ± 0.10 nm. {note}"
    clause = "may be biased by the solute's image"
    assert mean_said({"mean": 1.0, "error": 0.1, "unit": "nm", "noted": clause}) == \
        f"1.00 ± 0.10 nm; {clause}"


@pytest.mark.parametrize("count", [3, 4])
def test_a_drift_refusal_gives_its_amounts_in_the_quantity_s_unit_to_two_figures(
        tmp_path, monkeypatch, count) -> None:
    # "changes by +13.92 across the run (first to last mean +14.79)", beside
    # a note giving "about 410 kJ/mol".
    from fastmdxplora.analysis import thermodynamics as module
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    pieces = []
    for i in range(count):
        folder = tmp_path / f"piece-{i}" / "simulation"
        folder.mkdir(parents=True)
        path = folder / "energy.csv"
        values = 300 + stationary + 2 * i
        path.write_text('#"Step","Time (ps)","Temperature (K)"\n' +
                        "".join(f"{j},{j},{value}\n" for j, value in enumerate(values)))
        pieces.append({"path": path, "last_step": None,
                       "steps_before": 4000 * i, "ps_before": 4000 * i})
    monkeypatch.setattr(module, "state_record_pieces", lambda _: pieces)
    analysis = module.Thermodynamics()
    monkeypatch.setattr(analysis, "_state_path", lambda: pieces[0]["path"])
    analysis.compute(None)
    said = analysis.findings["thermodynamics"]["temperature"]["not_a_measurement"]
    change = said.split("changes by ")[1].split(" across")[0]
    span = said.split("first to last mean ")[1].split(")")[0].split(",")[0]
    for amount in (change, span):
        assert amount.endswith(" K") and amount.startswith("+"), said
        number = amount[:-len(" K")]
        assert len(number.lstrip("+").replace(".", "").lstrip("0")) <= 2, said


def test_the_recorded_mean_s_note_is_joined_as_a_sentence_where_it_is_one():
    """The panel said "Recorded mean ... kJ/mol; With 3 segments, ...": a
    capital after a semicolon, fixed on the chart caption alone."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "analysis-page.js").read_text(encoding="utf-8")
    start = script.index("    // As the table above says it")
    end = script.index("    if (recorded.start_shared_with_replicas)", start)
    said = []
    for noted in ("With 3 segments, a steady drift smaller than about 51 kJ/mol",
                  "may be biased by the solute's image"):
        probe = ("var parts = []; var unit = ''; var eq = {};"
                 "function format(v) { return String(v); }"
                 "function firstSentence(v) { return v; }"
                 f"var recorded = {{mean: -22300, standard_error: 1.1, "
                 f"said: '-22,300 ± 1.1 kJ/mol', noted: {json.dumps(noted)}}};"
                 + script[start:end] + "\nconsole.log(parts[0]);")
        said.append(subprocess.run([node, "-e", probe], capture_output=True, text=True,
                                   check=True).stdout.strip())
    assert said == ["Recorded mean -22,300 ± 1.1 kJ/mol. With 3 segments, a steady drift "
                    "smaller than about 51 kJ/mol.",
                    "Recorded mean -22,300 ± 1.1 kJ/mol; may be biased by the solute's image."]


def _compared_studies(tmp_path: Path) -> tuple[Path, Path]:
    """Two real joined tri-alanine studies' records
    (``tests/data/drift_short_compared_studies.json``): a stationary one
    (friction 1/ps) in 3 pieces of 20, 20 and 100 ps, and a drifting one
    (friction 0.002/ps) in 4 of 100, 20, 20 and 20 ps. Neither mean carries
    an image note; their temperatures differ, resolved, and the second's
    carries a drift note, as each RMSD does."""
    data = json.loads((Path(__file__).parent / "data" / "drift_short_compared_studies.json")
                      .read_text(encoding="utf-8"))
    roots = []
    for name in ("stationary_3_pieces", "drifting_4_pieces"):
        root = tmp_path / "ws" / name
        for relative, text in data[name].items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        roots.append(root)
    return roots[0], roots[1]


def test_compare_of_two_studies_carries_the_drift_note_apart_from_the_image_note(
        tmp_path) -> None:
    # Compare of two studies took a drift note for an image note: it marked
    # the temperature with the image mark and said a resolved difference
    # "may be biased by the solute's own image" where no mean had one, and
    # gave no drift note beside the RMSD.
    from fastmdxplora.gui.workspace import studies_compared
    from fastmdxplora.workspace_studies import compared

    first, second = _compared_studies(tmp_path)
    found = studies_compared(first, second)
    rows = {row["analysis"]: row for row in found["measures"]}
    temperature = rows["temperature"]
    assert temperature["versus"]["resolved"], temperature
    assert temperature["second"]["noted"] is None, temperature["second"]
    assert temperature["second"]["unseen"].startswith("With 4 segments, a steady drift"), \
        temperature["second"]
    for side, figure in (("first", "about 0.030 nm"), ("second", "about 0.0042 nm")):
        assert rows["rmsd"][side]["noted"] is None, rows["rmsd"][side]
        assert figure in (rows["rmsd"][side].get("unseen") or ""), rows["rmsd"][side]
    said, ok = compared(first, second)
    assert ok
    line = next(x for x in said.splitlines() if x.lstrip().startswith("RMSD:"))
    assert line.count(". With 3 segments") + line.count(". With 4 segments") == 2, line
    assert "image" not in said, said


def test_the_compare_page_marks_the_drift_note_apart_and_says_no_image_bias(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    import urllib.parse

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    first, second = _compared_studies(tmp_path)
    session = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#compare/" + "|".join(
                urllib.parse.quote(str(p), safe="") for p in (first, second)),
                wait_until="domcontentloaded")
            page.wait_for_selector('.page[data-page="compare"][data-compared="yes"]')
            seen = page.evaluate("""() => {
              const lines = (s) => [...document.querySelectorAll(s)].map((n) => n.textContent);
              const cells = {};
              document.querySelectorAll('.compare-means-table tr[data-analysis]').forEach((r) => {
                cells[r.dataset.analysis] = [...r.children].slice(1, 3).map((c) => c.textContent);
              });
              return {noted: lines('.compare-noted'), unseen: lines('.compare-unseen'), cells};
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert seen["noted"] == [], seen["noted"]
    assert not any("†" in cell for row in seen["cells"].values() for cell in row), seen
    assert seen["cells"]["temperature"][1].endswith("‡"), seen["cells"]
    assert all(cell.endswith("‡") for cell in seen["cells"]["rmsd"]), seen["cells"]
    assert any(line.startswith("‡ ") and ", Temperature: With 4 segments" in line
               for line in seen["unseen"]), seen["unseen"]
    assert sum(", RMSD: With " in line for line in seen["unseen"]) == 2, seen["unseen"]


def test_one_mean_with_both_notes_on_compare_has_both_marks_and_the_bias_sentence_once(
        tmp_path) -> None:
    # A mean carrying an image note and a drift note: its cell read "image..
    # With" in its hover text, its second mark could wrap alone, and the
    # image-bias sentence came after the drift notes, as if said of them.
    pytest.importorskip("playwright.sync_api")
    import urllib.parse

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.workspace_studies import compared

    first, second = _compared_studies(tmp_path)
    options = second / "analysis" / "thermodynamics" / "options.json"
    data = json.loads(options.read_text(encoding="utf-8"))
    data["findings"]["thermodynamics"]["temperature"]["solute_image"] = {
        "frames_used": 539, "frames_under_cutoff": 54, "cutoff_nm": 1.0}
    options.write_text(json.dumps(data), encoding="utf-8")
    said, ok = compared(first, second)
    assert ok
    line = next(x for x in said.splitlines() if x.lstrip().startswith("Temperature:"))
    assert ("; may be biased: in 54 of 539 frames used (10%) the solute was within 1 nm of "
            "its own image. With 4 segments, a steady drift") in line, line
    session = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1250, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#compare/" + "|".join(
                urllib.parse.quote(str(p), safe="") for p in (first, second)),
                wait_until="domcontentloaded")
            page.wait_for_selector('.page[data-page="compare"][data-compared="yes"]')
            seen = page.evaluate("""() => {
              const row = document.querySelector(
                '.compare-means-table tr[data-analysis="temperature"]');
              const cell = row.children[2];
              const marks = cell.querySelector('.compare-marks');
              return {text: cell.textContent, title: cell.title,
                      marks: marks ? [marks.textContent, getComputedStyle(marks).whiteSpace]
                        : null,
                      lines: [...document.querySelectorAll('.compare-noted, .compare-unseen')]
                        .map((n) => [n.className.includes('compare-unseen') ? 'u' : 'n',
                                     n.textContent])};
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    # Both marks after the mean, with their spaces, where no line can wrap.
    assert seen["text"].endswith(" K † ‡") and seen["marks"] == [" † ‡", "nowrap"], seen
    assert ".." not in seen["title"] and "own image. With 4 segments" in seen["title"], seen
    kinds = [kind for kind, _ in seen["lines"]]
    noted = [text for kind, text in seen["lines"] if kind == "n"]
    # The † line, the bias sentence counting this mean once, then the ‡ lines.
    assert noted[0].startswith("† ") and ", Temperature: may be biased" in noted[0], noted
    assert noted[1].startswith("One resolved difference has a mean that may be biased by "
                               "the solute's own image"), noted
    assert len(noted) == 2 and kinds[:2] == ["n", "n"] and set(kinds[2:]) == {"u"}, kinds
    assert any(", Temperature: With 4 segments" in text for kind, text in seen["lines"]
               if kind == "u"), seen["lines"]
