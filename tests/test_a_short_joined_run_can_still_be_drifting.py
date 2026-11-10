"""Three or four precise segment means can resolve a drift.

An order-permutation test cannot reach 0.05 with so few segments. Absolute
per-segment errors supply information that ordering alone cannot: a line
must have a significant slope and leave residuals consistent with those
errors. The slope must also stand against the means' own scatter about the
line, so offsets between segments larger than their errors (errors too
small, or a short restart between long segments) qualify the pooled error
rather than being mistaken for a steady trend.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from scipy.stats import chi2, linregress, norm

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import (
    _drift_test, _own_scatter_slope_p, drift_across_segments, summarise_segments)


def test_means_that_step_back_are_not_said_to_move_in_order():
    # Five segments: the permutation test calls this trend (4 of 120
    # orderings are as steep), but the third mean steps back, so "the
    # segment means move in order" would describe means the reader can see
    # do not. The verdict is the same as before; only what it says changed.
    means = np.array([0.0, 1.0, 0.8, 2.0, 3.0])
    _, p, why = _drift_test(means, np.full(5, 100.0))
    assert p == pytest.approx(4 / 120) and why is not None
    assert "move in order" not in str(why)
    assert "not one way at every step" in str(why)
    _, _, why = _drift_test(np.arange(5.0), np.full(5, 100.0))
    assert "move in order" in str(why) and "not one way" not in str(why)


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("direction", [-1, 1])
def test_a_precise_linear_drift_is_not_pooled(count, direction):
    means = direction * np.arange(count, dtype=float)
    scatter, p, why = _drift_test(means, np.full(count, 100.0))
    assert scatter > 1 and p < 0.05
    assert why is not None
    refusal = refusal_of(why)
    assert refusal.code == "analysis.sampling.drifting"
    assert refusal.details["drift_test"] == "slope_against_errors_and_scatter"
    assert "standard errors" in str(why) and "straight line" in str(why)
    assert "ordering this clean" not in str(why)
    # The same guard must keep its specificity: disagreement alone is not
    # a line, and an ordered difference smaller than the errors is not drift.
    patterns = ([[0.0, 10.0, 0.0]] if count == 3 else
                [[0.0, 10.0, 10.0, 0.0], [0.0, 0.0, 0.0, 10.0], [0.0, 0.0, 1.0, 10.0]])
    for pattern in patterns:
        scatter, _, why = _drift_test(np.array(pattern), np.full(count, 100.0))
        assert scatter > 2 and why is None
    scatter, _, why = _drift_test(np.linspace(0.0, 0.01, count), np.ones(count))
    assert scatter < 1 and why is None


@pytest.mark.parametrize("means,errors", [
    ([0.0, 0.8, 1.4], [0.5, 0.3, 0.4]),
    ([0.0, 0.4, 1.1, 1.7], [0.4, 0.3, 0.5, 0.6]),
])
def test_the_probability_matches_a_matrix_fit_using_absolute_errors(means, errors):
    means, errors = np.array(means), np.array(errors)
    design = np.column_stack((np.ones(means.size), np.arange(means.size)))
    a, b = design / errors[:, None], means / errors
    fitted, _, _, _ = np.linalg.lstsq(a, b, rcond=None)
    covariance = np.linalg.inv(a.T @ a)
    residual = (means - design @ fitted) / errors
    assert chi2.sf(float(residual @ residual), means.size - 2) > 0.05
    against_errors = 2 * norm.sf(abs(fitted[1]) / np.sqrt(covariance[1, 1]))
    # And against the means' own scatter: an unweighted fit's slope p-value,
    # Student's t on k - 2 degrees of freedom. The larger is the test's.
    against_scatter = linregress(np.arange(means.size), means).pvalue
    expected = max(against_errors, against_scatter)
    assert drift_across_segments(means, 1 / errors**2) == pytest.approx(expected)


def test_an_imprecise_middle_segment_does_not_make_two_ends_a_trend():
    # Two precise ends and a middle that says nothing are two means, and two
    # means always lie on a line: their difference is a trend or an offset
    # between segments, and nothing here tells which. A middle on the line
    # does say which.
    errors = np.array([0.1, 1000.0, 0.1])
    assert _drift_test(np.array([0.0, 1000.0, 2.0]), 1 / errors**2)[2] is None
    assert _drift_test(np.array([0.0, 1.0, 2.0]), 1 / errors**2)[2] is not None
    assert _drift_test(np.array([0.0, 1000.0, 2.0]), np.full(3, 100.0))[2] is None


@pytest.mark.parametrize("count", [3, 4])
def test_units_and_a_large_common_offset_do_not_change_the_verdict(count):
    means = np.arange(count, dtype=float)
    weights = np.linspace(0.5, 2.0, count)
    reference = drift_across_segments(means, weights)
    center = np.average(means, weights=weights)
    expected = 2 * norm.sf(np.sqrt(np.sum(weights * (means - center)**2)))
    assert reference == pytest.approx(expected)
    for scale in (1e-6, 1e6):
        assert drift_across_segments(means * scale, weights / scale**2) == \
            pytest.approx(reference)
    assert drift_across_segments(means + 1e12, weights) == pytest.approx(reference)


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, 0.0, -1.0])
def test_without_absolute_errors_the_ordering_test_keeps_its_limit(count, invalid):
    weights = np.full(count, 100.0)
    assert drift_across_segments(np.arange(count, dtype=float), weights) < 0.05
    weights[1] = invalid
    p = drift_across_segments(np.arange(count, dtype=float), weights)
    assert p >= 2 / math.factorial(count) - 1e-12


@pytest.mark.parametrize("count", [3, 4])
def test_a_non_finite_mean_is_not_given_a_significant_probability(count):
    means = np.arange(count, dtype=float)
    assert drift_across_segments(means, np.full(count, 100.0)) < 0.05
    means[1] = np.nan
    assert drift_across_segments(means, np.ones(count)) == 1.0


@pytest.mark.parametrize("count", [3, 4])
def test_an_extreme_normal_tail_is_not_reported_as_exactly_zero(count):
    p = drift_across_segments(np.arange(count, dtype=float), np.full(count, 1e6))
    assert 0 < p < 0.05


@pytest.mark.parametrize("count", [3, 4])
def test_the_joined_series_and_report_refuse_the_same_drift(count):
    from fastmdxplora.report.convergence import assess_series
    from tests.test_statistics import _segments

    # Reuse one stationary trace at each offset to make the segment means
    # exactly linear; independent traces are used in the rate check below.
    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([stationary + 2.0 * i for i in range(count)])
    joins = [stationary.size * i for i in range(1, count)]
    pooled, why = summarise_segments(values, joins)
    assert pooled is None
    assert refusal_of(why).code == "analysis.sampling.drifting"
    assessment = assess_series("rmsd", values, joins=joins)
    assert np.isnan(assessment.standard_error)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_the_pooled_record_says_which_test_it_used(count):
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.tile(stationary, count)
    pooled, why = summarise_segments(values, [stationary.size * i for i in range(1, count)])
    assert why is None
    assert pooled.as_record()["drift_test"] == (
        "slope_against_errors_and_scatter" if count < 5 else "permutation")


@pytest.mark.parametrize("offsets", [[0.0, 3.0, 0.0], [0.0, 3.0, -3.0, 0.0]])
def test_unordered_offsets_leave_a_qualification_about_stationarity(offsets):
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([stationary + offset for offset in offsets])
    pooled, why = summarise_segments(values, [stationary.size * i for i in range(1, len(offsets))])
    assert why is None and pooled is not None
    assert "does not establish stationarity" in pooled.qualification
    assert "lower bound" in pooled.qualification


@pytest.mark.parametrize("count", [3, 4])
def test_known_error_stationary_means_stay_under_the_nominal_rate(count):
    # A fixed, independently generated null sample. Each half of the test
    # holds 5% on its own, so both together hold at most that.
    rng = np.random.default_rng(9217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None
                  for row in rng.normal(size=(trials, count)) * errors)
    ceiling = 0.05 + 4 * np.sqrt(0.05 * 0.95 / trials)
    # Counted 13 and 20 of 2000: a guard that never rejects is not one.
    assert 0 < refused / trials <= ceiling


ERRORS = {"equal": [0.1, 0.1, 0.1, 0.1], "short_restart": [0.1, 0.25, 0.1, 0.1],
          "precise_middle": [0.28, 0.1, 0.28, 0.28]}


@pytest.mark.parametrize("count,kind,offsets", [
    (count, kind, offsets) for count in (3, 4) for kind in ERRORS for offsets in (0.3, 1.0)
    # Trusting the errors called these two 2.3% and 4.8%: no claim to test.
    if (count, offsets) != (4, 1.0) or kind == "precise_middle"])
def test_unordered_offsets_beyond_the_errors_are_not_called_a_trend(count, kind, offsets):
    # Segments whose means sit apart by more than their errors say (errors
    # too small, or a restart that landed elsewhere) in no order: 3 and 10
    # times the smallest error. Trusting the errors called 12.7% to 40.1% of
    # these drifting (counted on the same draws); the permutation test at
    # five segments holds 5% whatever the offsets.
    errors = ERRORS[kind]
    rng = np.random.default_rng(39217 + count + int(10 * offsets))
    errors = np.array(errors[:count])
    trials = 2000
    rows = (rng.normal(size=(trials, count)) * errors
            + rng.normal(size=(trials, count)) * offsets)
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None for row in rows)
    assert refused / trials <= 0.05 + 4 * np.sqrt(0.05 * 0.95 / trials)


@pytest.mark.parametrize("count,least", [(3, 0.25), (4, 0.88)])
def test_a_noisy_resolved_linear_drift_is_detected_as_often_as_counted(count, least):
    # Counted 645 of 2000 at three segments and 1852 at four: three means
    # leave one degree of freedom for their scatter about the line, so a
    # trend of 2.5 to 10 errors a segment is called only when the line is
    # close to straight. Four leave two.
    rng = np.random.default_rng(19217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    rows = rng.normal(size=(trials, count)) * errors + 2 * np.arange(count)
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None for row in rows)
    assert refused / trials >= least


@pytest.mark.parametrize("count", [3, 4])
def test_the_slope_uses_a_two_sided_five_percent_threshold(count):
    x = np.arange(count, dtype=float)
    boundary = norm.isf(0.025) / np.sqrt(np.sum((x - x.mean())**2))
    assert _drift_test(0.999 * boundary * x, np.ones(count))[2] is None
    assert _drift_test(1.001 * boundary * x, np.ones(count))[2] is not None


@pytest.mark.parametrize("count", [3, 4])
def test_the_line_fit_uses_k_minus_two_degrees_of_freedom(count):
    # This residual is orthogonal to both columns of the straight-line
    # design, so it changes the lack of fit without changing the slope.
    residual = np.array([1.0, -2.0, 1.0] if count == 3 else [1.0, -1.0, -1.0, 1.0])
    residual /= np.linalg.norm(residual)
    x = np.arange(count, dtype=float)
    for fits in (True, False):
        q = chi2.isf(0.05, count - 2) * (0.999 if fits else 1.001)
        # Steep enough that both halves call it: the fit decides only which
        # are read. Where the line does not fit the errors, the scatter's
        # p-value alone is returned (it was 1, and the drift pooled).
        means = 100 * x + np.sqrt(q) * residual
        p = drift_across_segments(means, np.ones(count))
        own = _own_scatter_slope_p(means, x)
        assert p == pytest.approx(own if not fits else max(own, norm.sf(100 * np.sqrt(
            np.sum((x - x.mean()) ** 2))) * 2))
        assert _drift_test(means, np.ones(count))[2] is not None


def test_a_large_drift_off_a_straight_line_is_called_on_its_own_scatter():
    # A real joined run's temperature after four pieces of 20 ps, climbing
    # toward its thermostat's 300 K: rising at every step, 14.8 K over errors
    # of 0.5 to 0.7 K, and off a straight line by far more than those errors
    # allow (Q 310 on 3 degrees of freedom). The line failing the errors'
    # chi-square returned p 1, and the mean was pooled.
    means = np.array([177.7929, 185.4486, 186.6115, 192.5817])
    errors = np.array([0.6704, 0.6769, 0.5985, 0.5167])
    centres = np.array([82.5, 179.5, 258.5, 367.0])
    _, p, why = _drift_test(means, 1 / errors**2, centres)
    assert p == pytest.approx(_own_scatter_slope_p(means, centres)) and p < 0.05
    assert why is not None
    assert refusal_of(why).code == "analysis.sampling.drifting"
    assert "stray from a straight line by more than their own standard errors" in str(why)


def test_a_drift_refusal_says_how_many_segments_were_tested_and_which_were_left_out():
    # Five pieces climbing, a fourth (numbered 3) too correlated to resolve
    # and a last of two frames: "move in order ... from first to last" was
    # said as though of all seven.
    from scipy.signal import lfilter

    rng = np.random.default_rng(5)

    def ar(n, phi):
        return lfilter([1.0], [1.0, -phi], rng.normal(0.0, np.sqrt(1 - phi * phi), n))

    pieces = [ar(1000, 0.8) + 0.4 * i for i in range(5)]
    pieces = pieces[:3] + [ar(60, 0.995) + 1.0] + pieces[3:] + [np.array([5.0, 5.0])]
    values = np.concatenate(pieces)
    joins = list(np.cumsum([piece.size for piece in pieces])[:-1])
    pooled, why = summarise_segments(values, joins)
    assert pooled is None and refusal_of(why).code == "analysis.sampling.drifting"
    assert ("The means of the 5 segments tested (2 left out, numbered from zero: "
            "3, its correlation not resolved; 6, too short for a mean of its own) "
            "move in order across the run") in str(why)


def test_segments_left_out_for_one_reason_are_said_together():
    # Six pieces with every one left out read "0, its correlation not
    # resolved; 1, its correlation not resolved; ..." over nine lines of a card.
    from scipy.signal import lfilter

    rng = np.random.default_rng(5)

    def ar(n, phi):
        return lfilter([1.0], [1.0, -phi], rng.normal(0.0, np.sqrt(1 - phi * phi), n))

    pieces = [ar(1000, 0.8) + 0.4 * i for i in range(5)]
    pieces = (pieces[:1] + [ar(60, 0.995) + 0.2] + pieces[1:3] + [ar(60, 0.995) + 1.0]
              + pieces[3:])
    values = np.concatenate(pieces)
    joins = list(np.cumsum([piece.size for piece in pieces])[:-1])
    _, why = summarise_segments(values, joins)
    assert ("The means of the 5 segments tested (2 left out, numbered from zero: 1 and 4, "
            "their correlation not resolved) move in order") in str(why), why


def _sweep_summary() -> dict:
    from pathlib import Path

    return json.loads((Path(__file__).parent / "data" / "drift_short_per_record_sweep.json")
                      .read_text(encoding="utf-8"))


def _about(share: float, down: bool) -> str:
    """A share of runs as the docs say it: to the whole percent below it
    for the least called, to a tenth above it for the most unseen."""
    if down:
        return f"{math.floor(share * 100):d}%"
    return f"{math.ceil(share * 1000) / 10:.1f}%"


def test_the_docs_state_the_per_record_range_from_the_sweep_and_table_its_examples():
    # The docs stated "91.6% to 100% of runs at three segments" from the
    # table's 58 rows as if a bound for every record: lengths 100, 5,000 and
    # 100, not in the table, are called in 90.6%, and the figure moved with
    # the quantity's units. Now the range is from a random sweep of
    # layouts, said as approximate, and the table gives examples.
    from pathlib import Path

    from tests._short_segment_drift_check import (PER_RECORD, SWEEP_EDGES, SWEEP_LAYOUTS,
                                                  SWEEP_RATIOS, SWEEP_SEED, per_record)

    text = (Path(__file__).parents[1] / "docs" / "short-segment-drift.md").read_text(
        encoding="utf-8")
    head = "| Layout | X | h | Called at its own figure | Unseen though said seen |"
    table = text[text.index(head):].split("\n\n")[0].splitlines()[2:]
    rows = {}
    for line in table:
        layout, x, h, called, unseen = (c.strip() for c in line.strip("|").split("|"))
        rows[(layout, int(h))] = (int(x), int(called.replace(",", "")), int(unseen.replace(",", "")))

    def label(layout):
        if "errors" in layout:
            return "Errors " + ", ".join(f"{e:g}" for e in layout["errors"])
        return "Lengths " + ", ".join(f"{n:,}" for n in layout["lengths"])

    assert len(rows) == sum(len(r) for _, r in PER_RECORD)
    assert {"lengths": [100, 5000, 100]} in [layout for layout, _ in PER_RECORD]
    # One row of the table recounted by the helper: 100, 5,000 and 100 at h = 54.
    import tests._short_segment_drift_check as helper

    saved = helper.PER_RECORD
    try:
        helper.PER_RECORD = ((dict(lengths=[100, 5000, 100]), (54,)),)
        (counted,) = per_record()
    finally:
        helper.PER_RECORD = saved
    assert rows[("Lengths 100, 5,000, 100", 54)] == (
        counted["in_pooled_errors"], counted["called_at_own_figure"],
        counted["unseen_though_said_seen"]), counted

    summary = _sweep_summary()
    assert (summary["seed"], summary["layouts_each"], summary["edges"], summary["ratios"]) == (
        SWEEP_SEED, SWEEP_LAYOUTS, len(SWEEP_EDGES), list(SWEEP_RATIOS))
    assert len(summary["rows"]) == (2 * SWEEP_LAYOUTS + len(SWEEP_EDGES)) * len(SWEEP_RATIOS)
    trials = summary["trials"]
    least = {n: summary[f"least_called_{n}"]["called_at_own_figure"] / trials for n in (3, 4)}
    most = {n: summary[f"most_unseen_{n}"]["unseen_though_said_seen"] / trials for n in (3, 4)}
    outer = summary["least_called_4_outer_precise"]["called_at_own_figure"] / trials
    said = " ".join(text[text.index("Over a wider sweep"):].split("\n\n")[0].split())
    limits = " ".join(text[text.index("## Limits and records"):].split())
    assert (f"seed {SWEEP_SEED}, {SWEEP_LAYOUTS} random layouts of three segments and "
            f"{SWEEP_LAYOUTS} of four") in said, said
    assert f"{len(SWEEP_EDGES)} edge layouts" in said, said
    called = (f"about {_about(least[3], True)} to 100% of runs at three segments and "
              f"{_about(least[4], True)} to 100% at four")
    unseen = (f"unseen though said seen in up to about {_about(most[3], False)} at three and "
              f"{_about(most[4], False)} at four")
    assert called in said and unseen in said, said
    assert f"about {_about(outer, True)} or more" in said, said
    assert called in limits and unseen in limits, limits
    # The table's examples lie inside the range the sweep gives.
    for (layout, _), (_, row_called, row_unseen) in rows.items():
        count = 3 if layout.count(",") == 2 else 4
        assert row_called / trials >= least[count] and row_unseen / trials <= most[count], layout
    assert "91.6%" not in text and "72.8%" not in text


def test_the_helper_reproduces_a_row_of_the_saved_sweep():
    # The sweep takes about fifteen minutes, so its summary is saved; the
    # helper recounts one layout of it here (100, 5,000 and 100, an edge
    # layout), and every row the summary names as least or most is one of
    # its rows.
    from tests._short_segment_drift_check import SWEEP_LAYOUTS, sweep, sweep_layouts

    summary = _sweep_summary()
    layouts = sweep_layouts()
    for row in summary["rows"]:
        layout = layouts[row["layout"]]
        assert all(row[k] == v for k, v in layout.items()), (row, layout)
    number = layouts.index(dict(lengths=[100, 5000, 100]))
    assert number == 2 * SWEEP_LAYOUTS
    again = [{k: v for k, v in r.items() if k not in ("trials", "seed")}
             for r in sweep(only=number)]
    assert again == [r for r in summary["rows"] if r["layout"] == number], again
    for key in ("least_called_3", "least_called_4", "most_unseen_3", "most_unseen_4",
                "least_called_4_outer_precise"):
        assert summary[key] in summary["rows"], key


def test_the_docs_say_what_unequal_lengths_with_equal_errors_need():
    # "Pieces of unequal length with equal errors need what equal pieces
    # need": 500, 500 and 4,000 frames at equal errors need 39, not 41.
    from pathlib import Path

    from fastmdxplora.statistics import drift_unseen_in_pooled_errors
    from tests._short_segment_drift_check import UNSEEN_LAYOUTS

    text = " ".join((Path(__file__).parents[1] / "docs" / "short-segment-drift.md")
                    .read_text(encoding="utf-8").split())
    need = {}
    for count in (3, 4):
        found = []
        for lengths in UNSEEN_LAYOUTS[count]:
            if len(set(lengths)) > 1:
                lengths = np.asarray(lengths, dtype=float)
                found.append(drift_unseen_in_pooled_errors(
                    [1.0] * count, list(np.cumsum(lengths) - lengths / 2)))
        need[count] = (min(found), max(found))
    assert need[3][1] == 41 and need[4][1] == 16, need
    assert (f"pieces of unequal length with equal errors need about what equal pieces "
            f"need ({need[3][0]} to {need[3][1]} at three, {need[4][0]} to {need[4][1]} "
            f"at four,") in text, need


def test_the_results_doc_says_of_unequal_lengths_what_the_drift_doc_says():
    # docs/results.md said the figure is "more where their errors differ"
    # straight after the equal pieces' 41 and 16, as if unequal lengths
    # needed more: with equal errors they need about that, 38 to 41 and 15
    # to 16, as the drift doc says.
    from pathlib import Path

    docs = Path(__file__).parents[1] / "docs"
    results = " ".join((docs / "results.md").read_text(encoding="utf-8").split())
    drift = " ".join((docs / "short-segment-drift.md").read_text(encoding="utf-8").split())
    assert "pieces of unequal length with equal errors need about what equal pieces need" in drift
    assert ("41 times the error of their pooled mean for three equal pieces, 16 for four, about "
            "that for pieces of unequal length with equal errors, more where their errors "
            "differ") in results, results[results.index("41 times"):][:300]
