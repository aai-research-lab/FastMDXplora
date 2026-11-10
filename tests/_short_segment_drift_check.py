"""Reproduce P1's fixed synthetic checks and write their counts as JSON.

Run from an installed checkout:
    python tests/_short_segment_drift_check.py --output /tmp/p1.json
To count another checkout, put its src directory first on PYTHONPATH. The
seeds and cases are fixed here; the thresholds live in statistics.py. The
trajectory cases take about ten minutes on one core. ``--sweep`` counts the
per-record sweep alone and writes its summary as
tests/data/drift_short_per_record_sweep.json holds it (about fifteen minutes
on one core).
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import platform

import numpy as np
import scipy
from scipy.signal import lfilter

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import _drift_test, mean_record, summarise_segments


def known_errors() -> list[dict]:
    """Segment means with known Gaussian errors, 10,000 sets a row."""
    rows = []
    for count in (3, 4):
        errors = {"rising": np.linspace(0.2, 0.8, count),
                  "equal": np.full(count, 0.2),
                  "short_restart": np.r_[0.2, 0.5, np.full(count - 2, 0.2)],
                  "precise_middle": np.r_[0.56, 0.2, np.full(count - 2, 0.56)]}
        shapes = {
            "stationary": ("rising", np.zeros(count), 0.0),
            "linear": ("rising", 2.0 * np.arange(count), 0.0),
            "jump": ("rising", np.r_[np.zeros(count - 1), 10.0], 0.0),
            "unordered": ("rising", np.array([0.0, 4.0, 0.0] if count == 3
                                             else [0.0, 4.0, -4.0, 0.0]), 0.0),
            # Offsets between segments three times the smallest error, in no
            # order: errors too small for the scatter.
            "offsets_equal": ("equal", np.zeros(count), 0.6),
            "offsets_short_restart": ("short_restart", np.zeros(count), 0.6),
            "offsets_precise_middle": ("precise_middle", np.zeros(count), 0.6),
        }
        for index, (name, (kind, offsets, scatter)) in enumerate(shapes.items()):
            seed = 740001 + count * 100 + index
            trials = 10000
            rng = np.random.default_rng(seed)
            sigma = errors[kind]
            means = rng.normal(size=(trials, count)) * sigma + offsets
            if scatter:
                means += rng.normal(size=(trials, count)) * scatter
            refused = sum(_drift_test(row, 1.0 / sigma**2)[2] is not None for row in means)
            rows.append(dict(case=name, segments=count, trials=trials, seed=seed,
                             errors=sigma.tolist(), means=offsets.tolist(),
                             offsets_sd=scatter, drift_refusals=refused,
                             rate=refused / trials))
    return rows


#: Segment lengths for the drift that could go unseen: equal pieces, and
#: the unequal layouts the trajectory rows use.
UNSEEN_LAYOUTS = {
    3: ([1, 1, 1], [3000, 500, 3000], [500, 4000, 500], [500, 500, 4000], [4000, 500, 500]),
    4: ([1, 1, 1, 1], [3000, 500, 3000, 3000], [500, 4000, 500, 500], [500, 500, 500, 4000],
        [1000, 1000, 1000, 5000]),
}


def unseen_in_pooled_errors(lengths, trials: int = 10000, seed: int = 760003,
                            called: float = 0.8, errors=None) -> dict:
    """The smallest steady drift, in whole pooled errors from the first
    segment's mean to the last's, that the rule calls in at least
    ``called`` of ``trials`` sets of means. Each segment's error goes as one
    over the root of its length, or is as ``errors`` gives; each mean sits at its centre in time and
    the drift is linear in time. The same random numbers at every size."""
    lengths = np.asarray(lengths, dtype=float)
    # Errors as given (equal pieces whose correlation times differ), else
    # each as one over the root of its length.
    sigma = (np.asarray(errors, dtype=float) / float(np.min(errors)) if errors is not None
             else np.sqrt(lengths.min() / lengths))
    precisions = 1.0 / sigma**2
    pooled = 1.0 / np.sqrt(precisions.sum())
    centres = np.cumsum(lengths) - lengths / 2.0
    shape = (centres - centres[0]) / (centres[-1] - centres[0])
    noise = np.random.default_rng(seed).normal(size=(trials, lengths.size)) * sigma
    size = 0
    while True:
        size += 1
        rows = noise + size * pooled * shape
        refused = sum(_drift_test(row, precisions, centres)[2] is not None for row in rows)
        if refused >= called * trials:
            return dict(segments=int(lengths.size), lengths=lengths.astype(int).tolist(),
                        errors=sigma.round(4).tolist(),
                        trials=trials, seed=seed, in_pooled_errors=size,
                        drift_refusals=refused)


def inflated(trials: int = 10000, seed: int = 760005) -> list[dict]:
    """Equal segments whose means scatter h times the variance their errors
    say (offsets the errors miss), h from 1 to 54: how often the rule calls
    a steady drift of the counted X pooled errors, and of X sqrt(h), first
    segment to last; and how often the figure a record says, X times the
    square root of its scatter about the line as the note says it
    (``statistics._scale_as_said``), is at least X sqrt(h)."""
    from fastmdxplora.statistics import (DRIFT_UNSEEN_IN_POOLED_ERRORS, _line_scatter,
                                         _scale_as_said)

    rows = []
    for count in (3, 4):
        counted = DRIFT_UNSEEN_IN_POOLED_ERRORS[count]
        precisions = np.ones(count)
        pooled = 1.0 / np.sqrt(count)
        centres = np.arange(count) + 0.5
        shape = (centres - centres[0]) / (centres[-1] - centres[0])
        for h in (1, 2, 4, 10, 54):
            noise = np.random.default_rng(seed).normal(size=(trials, count)) * np.sqrt(h)
            called = {}
            for name, size in (("at_x", counted), ("at_x_sqrt_h", counted * np.sqrt(h))):
                called[name] = sum(_drift_test(row, precisions, centres)[2] is not None
                                   for row in noise + size * pooled * shape)
            said = [counted * _scale_as_said(_line_scatter(row, precisions, centres))
                    for row in noise]
            rows.append(dict(segments=count, variance_ratio=h, trials=trials, seed=seed,
                             called_at_x=called["at_x"], called_at_x_sqrt_h=called["at_x_sqrt_h"],
                             said_at_least_x_sqrt_h=int(sum(x >= counted * np.sqrt(h) * (1 - 1e-12)
                                                            for x in said))))
    return rows


#: Equal pieces whose errors differ (their correlation times do).
UNSEEN_ERRORS = {3: ([1, 1.1, 1], [1, 1.25, 1], [1, 1.5, 1], [1, 2, 1], [1, 2.45, 1], [1, 3, 1],
                     [2, 1, 1], [1, 1, 2]),
                 4: ([1, 1.25, 1, 1], [1, 1.5, 1, 1], [1, 2, 1, 1], [2, 1, 1, 1])}


def unseen() -> list[dict]:
    return ([unseen_in_pooled_errors(layout) for count in (3, 4)
             for layout in UNSEEN_LAYOUTS[count]]
            + [unseen_in_pooled_errors([1] * len(errors), errors=errors)
               for count in (3, 4) for errors in UNSEEN_ERRORS[count]])


#: Per record: layouts, as segment lengths (each error one over the root of
#: its length) or as errors of equal pieces, and the variance ratios h their
#: means scatter at.
PER_RECORD = (
    (dict(errors=[1, 1, 1]), (1, 2, 4, 10, 54)),
    (dict(errors=[1, 1, 1, 1]), (1, 2, 4, 10, 54)),
    (dict(errors=[1, 2, 1]), (1, 4, 54)),
    (dict(errors=[1, 2.45, 1]), (1, 4, 54)),
    (dict(errors=[1, 3, 1]), (1, 4, 54)),
    (dict(errors=[1, 2, 1, 1]), (1, 4, 54)),
    (dict(lengths=[500, 500, 4000]), (1, 4, 54)),
    (dict(lengths=[4000, 500, 500]), (1, 4, 54)),
    (dict(lengths=[3000, 500, 3000]), (1, 4, 54)),
    (dict(lengths=[500, 4000, 500]), (1, 4, 54)),
    (dict(lengths=[100, 5000, 100]), (1, 4, 54)),
    (dict(lengths=[500, 500, 500, 4000]), (1, 4, 54)),
    (dict(lengths=[1000, 1000, 1000, 5000]), (1, 4, 54)),
    (dict(errors=[1, 1, 1, 2]), (1, 4, 54)),
    (dict(errors=[2, 1, 1, 2]), (1, 4, 54)),
    (dict(errors=[1, 1, 1, 4]), (1, 4, 54)),
    (dict(errors=[6, 1, 1, 1]), (1, 4, 54)),
    (dict(lengths=[1000, 1000, 1000, 250]), (1, 4, 54)),
    (dict(lengths=[4000, 4000, 4000, 250]), (1, 4, 54)),
)


def per_record_row(sigma, centres, h: float, trials: int = 10000,
                   seed: int = 760007) -> dict:
    """What a record's own figure (``statistics._drift_unseen``: its layout's
    80% point from its errors and centres, scaled by each set's own scatter
    as the note says it) claims for one layout at one h, counted the way a
    reader uses it: a steady drift as large as the figure beside the mean,
    put into that set, is it called (``called_at_own_figure``); and, for a
    fixed drift of 0.25 to 3 times the layout's true 80% point (its X times
    the root of h), how often it goes unseen while the figure says a drift
    that large would have been seen (``unseen_though_said_seen``, the
    largest over those sizes). The means scatter h times what their errors
    say. The rule in arrays (``statistics._drift_called``, the same verdict
    as ``_drift_test``), each set's scatter and figure the package's own."""
    from fastmdxplora.statistics import (_drift_called, _drift_figure, _drift_unseen,
                                         _line_scatter, _scale_as_said)

    sigma = np.asarray(sigma, dtype=float)
    centres = np.asarray(centres, dtype=float)
    precisions = 1.0 / sigma**2
    pooled = 1.0 / np.sqrt(precisions.sum())
    shape = (centres - centres[0]) / (centres[-1] - centres[0])
    tested = dict(segments=sigma.size, tested=sigma.size, left_out="", called=False,
                  pooled_error=pooled, errors=sigma.tolist(), centres=centres.tolist())
    counted = _drift_unseen({**tested, "line_scatter": 1.0})["counted_in_pooled_errors"]
    noise = np.random.default_rng(seed).normal(size=(trials, sigma.size)) * sigma * np.sqrt(h)
    figures = np.array([_drift_figure(counted, _scale_as_said(_line_scatter(
        row, precisions, centres)), pooled) for row in noise])
    own = int(_drift_called(noise + figures[:, None] * shape, precisions, centres).sum())
    worst = 0
    for times in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
        drift = times * counted * np.sqrt(h) * pooled
        missed = ~_drift_called(noise + drift * shape, precisions, centres)
        worst = max(worst, int((missed & (figures <= drift)).sum()))
    return dict(in_pooled_errors=int(counted), variance_ratio=h, trials=trials, seed=seed,
                called_at_own_figure=own, unseen_though_said_seen=worst)


def _layout(layout: dict) -> tuple:
    """A layout's errors and centres in time: given as segment lengths (each
    error one over the root of its length, the smallest 1), as errors of
    equal pieces, or as both."""
    lengths = np.asarray(layout.get("lengths") or [1] * len(layout["errors"]), dtype=float)
    sigma = (np.asarray(layout["errors"], dtype=float) if "errors" in layout
             else np.sqrt(lengths.min() / lengths))
    return sigma, np.cumsum(lengths) - lengths / 2.0


def per_record(trials: int = 10000, seed: int = 760007) -> list[dict]:
    """:func:`per_record_row` for every layout and h in ``PER_RECORD``."""
    rows = []
    for layout, ratios in PER_RECORD:
        sigma, centres = _layout(layout)
        for h in ratios:
            rows.append(dict(**layout, **per_record_row(sigma, centres, h, trials, seed)))
    return rows


#: The sweep of layouts per record: its seed, how many random layouts of
#: three and of four segments, the variance ratios, and the edge layouts
#: counted besides.
SWEEP_SEED = 760013
SWEEP_LAYOUTS = 150
SWEEP_RATIOS = (1, 4, 54)
SWEEP_EDGES = (
    dict(lengths=[100, 5000, 100]), dict(lengths=[50, 10000, 50]),
    dict(lengths=[50, 50, 10000]), dict(lengths=[10000, 50, 50]),
    dict(errors=[1, 0.2, 1]), dict(errors=[1, 0.1, 1]), dict(errors=[1, 10, 1]),
    dict(errors=[10, 1, 1]), dict(errors=[1, 1, 10]),
    dict(lengths=[10000, 10000, 10000, 50]), dict(lengths=[50, 10000, 10000, 50]),
    dict(lengths=[10000, 50, 50, 10000]), dict(lengths=[50, 50, 50, 10000]),
    dict(errors=[1, 1, 1, 30]), dict(errors=[30, 1, 1, 1]), dict(errors=[1, 1, 1, 100]),
    dict(errors=[1, 10, 10, 1]), dict(errors=[1, 0.1, 0.1, 1]),
    dict(errors=[1, 1, 1]), dict(errors=[1, 1, 1, 1]),
)


def sweep_layouts(seed: int = SWEEP_SEED, size: int = SWEEP_LAYOUTS) -> list[dict]:
    """``size`` random layouts of three segments and ``size`` of four, then
    the edge layouts. A third each: lengths spread over a factor of 200 (50
    to 10,000 frames, log-uniform; each error one over the root of its
    length); equal pieces whose errors are spread over a factor of 100
    (log-uniform); and both, the lengths so and each error besides
    multiplied by 1 to 10 (a correlation time up to 100 times another's)."""
    rng = np.random.default_rng(seed)
    layouts = []
    for count in (3, 4):
        for index in range(size):
            kind = index % 3
            lengths = np.round(10 ** rng.uniform(np.log10(50), 4, count)).astype(int)
            errors = 10 ** rng.uniform(0, 2, count)
            factors = 10 ** rng.uniform(0, 1, count)
            if kind == 0:
                layouts.append(dict(lengths=lengths.tolist()))
            elif kind == 1:
                layouts.append(dict(errors=[float(f"{e / errors.min():.3g}") for e in errors]))
            else:
                sigma = factors / np.sqrt(lengths)
                layouts.append(dict(lengths=lengths.tolist(),
                                    errors=[float(f"{e / sigma.min():.3g}") for e in sigma]))
    return layouts + [dict(layout) for layout in SWEEP_EDGES]


def sweep(seed: int = SWEEP_SEED, size: int = SWEEP_LAYOUTS, trials: int = 10000,
          only: "int | None" = None) -> list[dict]:
    """:func:`per_record_row` over :func:`sweep_layouts` at each of
    ``SWEEP_RATIOS``, each row numbered by its layout (``only`` counts that
    one layout alone)."""
    rows = []
    for number, layout in enumerate(sweep_layouts(seed, size)):
        if only is not None and number != only:
            continue
        sigma, centres = _layout(layout)
        for h in SWEEP_RATIOS:
            rows.append(dict(layout=number, **layout,
                             **per_record_row(sigma, centres, h, trials)))
    return rows


def sweep_summary(rows: list[dict], seed: int = SWEEP_SEED, size: int = SWEEP_LAYOUTS) -> dict:
    """The range the docs state from a sweep: at three and at four segments,
    the least called at its own figure and the most unseen though said
    seen, with the rows they are of, and every row."""
    trials, records = rows[0]["trials"], rows[0]["seed"]
    rows = [{k: v for k, v in r.items() if k not in ("trials", "seed")} for r in rows]
    summary = dict(seed=seed, layouts_each=size, edges=len(SWEEP_EDGES),
                   ratios=list(SWEEP_RATIOS), trials=trials, sets_seed=records, rows=rows)
    for count in (3, 4):
        mine = [r for r in rows if len(r.get("errors") or r["lengths"]) == count]
        summary[f"rows_{count}"] = len(mine)
        summary[f"least_called_{count}"] = min(mine, key=lambda r: r["called_at_own_figure"])
        summary[f"most_unseen_{count}"] = max(mine, key=lambda r: r["unseen_though_said_seen"])
    # Four segments whose first and last pieces are no less precise than the
    # inner ones.
    outer = [r for r in rows if len(r.get("errors") or r["lengths"]) == 4
             and max(_layout(r)[0][[0, -1]]) <= min(_layout(r)[0][1:-1])]
    summary["rows_4_outer_precise"] = len(outer)
    summary["least_called_4_outer_precise"] = min(outer, key=lambda r: r["called_at_own_figure"])
    return summary


def sweep_written(summary: dict) -> str:
    """The summary as ``tests/data/drift_short_per_record_sweep.json``
    holds it: one row a line."""
    head = {k: v for k, v in summary.items() if k != "rows"}
    rows = ",\n".join("  " + json.dumps(r, separators=(", ", ": ")) for r in summary["rows"])
    return json.dumps(head, indent=1)[:-2] + ',\n "rows": [\n' + rows + "\n ]\n}\n"


def ar1(rng, n, phi, r):
    """r independent stationary AR(1) series of unit variance, length n."""
    e = rng.normal(0.0, np.sqrt(1 - phi * phi), (r, n))
    zi = (phi * rng.normal(size=r))[:, None]
    return lfilter([1.0], [1.0, -phi], e, axis=1, zi=zi)[0]


def series(rng, r, lengths, phi=0.8, offsets=None, slow=0.0, slow_phi=0.999,
           ramp=0.0, curve=0.0):
    n = int(sum(lengths))
    x = ar1(rng, n, phi, r)
    if slow:
        x += slow * ar1(rng, n, slow_phi, r)
    if offsets is not None:
        x += np.repeat(np.asarray(offsets, float), lengths)[None, :]
    if ramp:
        x += ramp * np.linspace(0.0, 1.0, n)[None, :]
    if curve:
        x += curve * (1.0 - np.exp(-np.linspace(0.0, 1.0, n) / 0.3))[None, :]
    joins = list(np.cumsum(lengths)[:-1].astype(int))
    return x, joins


def verdict(x, joins):
    pooled, why = summarise_segments(x, joins)
    if pooled is not None:
        return "pooled_qualified" if pooled.qualification else "pooled"
    return refusal_of(why).code.replace("analysis.sampling.", "")


L = 1000
CASES = {
    # stationary, equal segments
    "stat_eq_3": dict(lengths=[L] * 3),
    "stat_eq_4": dict(lengths=[L] * 4),
    # stationary, a short restart between long segments (unequal errors)
    "stat_short_3": dict(lengths=[3000, 500, 3000]),
    "stat_short_4": dict(lengths=[3000, 500, 3000, 3000]),
    # stationary with a slow mode the segments' errors miss (errors too small)
    "stat_slow_eq_3": dict(lengths=[L] * 3, slow=0.5),
    "stat_slow_eq_4": dict(lengths=[L] * 4, slow=0.5),
    "stat_slow_short_3": dict(lengths=[3000, 500, 3000], slow=0.5),
    "stat_slow_short_4": dict(lengths=[3000, 500, 3000, 3000], slow=0.5),
    # unordered offsets between restarts (errors too small for the scatter)
    "unord_short_3": dict(lengths=[3000, 500, 3000], rand_offsets=0.3),
    "unord_short_4": dict(lengths=[3000, 500, 3000, 3000], rand_offsets=0.3),
    "unord_eq_3": dict(lengths=[L] * 3, rand_offsets=0.3),
    "unord_eq_4": dict(lengths=[L] * 4, rand_offsets=0.3),
    "unord_small_3": dict(lengths=[L] * 3, rand_offsets=0.15),
    "unord_small_4": dict(lengths=[L] * 4, rand_offsets=0.15),
    # stationary, a precise middle segment
    "stat_mid_3": dict(lengths=[500, 4000, 500]),
    "stat_mid_4": dict(lengths=[500, 4000, 500, 500]),
    # a 2-frame segment withheld (at the end and in the middle)
    "stat_w2_3": dict(lengths=[L, L, L, 2]),
    "stat_w2mid_3": dict(lengths=[L, 2, L, L]),
    "stat_w2_4": dict(lengths=[L, L, L, L, 2]),
    "ramp_w2_3": dict(lengths=[L, L, L, 2], offsets=[0, .4, .8, .8]),
    "ramp_w2mid_3": dict(lengths=[L, 2, L, L], offsets=[0, .4, .4, .8]),
    "ramp_w2_4": dict(lengths=[L, L, L, L, 2], offsets=[0, .4, .8, 1.2, 1.2]),
    # power: offsets rising by segment, and levelling off
    "ramp_eq_3": dict(lengths=[L] * 3, offsets=[0, .4, .8]),
    "ramp_eq_4": dict(lengths=[L] * 4, offsets=[0, .4, .8, 1.2]),
    "ramp_small_3": dict(lengths=[L] * 3, offsets=[0, .25, .5]),
    "ramp_small_4": dict(lengths=[L] * 4, offsets=[0, .25, .5, .75]),
    "ramp_short_3": dict(lengths=[3000, 500, 3000], offsets=[0, .4, .8]),
    "ramp_mid_3": dict(lengths=[500, 4000, 500], offsets=[0, .4, .8]),
    "level_4": dict(lengths=[L] * 4, offsets=[0, .6, .8, .85]),
    "level_3": dict(lengths=[L] * 3, offsets=[0, .6, .75]),
    # not one way
    "zigzag_4": dict(lengths=[L] * 4, offsets=[0, 1.0, .5, 1.5]),
    # k >= 5 unchanged
    "stat_eq_5": dict(lengths=[L] * 5),
    "ramp_eq_5": dict(lengths=[L] * 5, offsets=[0, .2, .4, .6, .8]),
    "unord_eq_5": dict(lengths=[L] * 5, rand_offsets=0.3),
    "stat_eq_6": dict(lengths=[L] * 6),
    "ramp_eq_6": dict(lengths=[L] * 6, offsets=[0, .1, .2, .3, .4, .5]),
    # unequal segments: stationary, and a ramp linear in time of 2.4 over the run
    "stat_uneq_3": dict(lengths=[500, 500, 4000]),
    "stat_uneq_4": dict(lengths=[500, 500, 500, 4000]),
    "ramp_time_uneq_3": dict(lengths=[500, 500, 4000], ramp=2.4),
    "ramp_time_uneq_4": dict(lengths=[500, 500, 500, 4000], ramp=2.4),
    # levelling off in time, 1.2 (1 - exp(-t / 0.3)) over the run: a long
    # last piece, a long first one, and equal pieces
    "level_time_long_last_4": dict(lengths=[1000, 1000, 1000, 5000], curve=1.2),
    "level_time_long_last_3": dict(lengths=[1000, 1000, 6000], curve=1.2),
    "level_time_long_first_3": dict(lengths=[6000, 1000, 1000], curve=1.2),
    "level_time_equal_3": dict(lengths=[2000, 2000, 2000], curve=1.2),
}


def by_index() -> None:
    """Count with each mean placed at its segment's index rather than its
    centre in time (as first built), for the trade between the two."""
    import fastmdxplora.statistics as statistics

    test = statistics._drift_test

    def at_index(means, precisions, positions=None, **kwargs):
        return test(means, precisions, None, **kwargs)

    statistics._drift_test = at_index


def trajectories(realisations: int = 1000, only: "list[str] | None" = None) -> list[dict]:
    """Contiguous AR(1) runs (phi 0.8 unless said) through summarise_segments:
    ``offsets`` per segment, ``rand_offsets`` the SD of random offsets per
    segment in no order, ``slow`` the size of a second AR(1) mode with phi
    0.999 (a correlation time of 1000 frames the segments' errors miss),
    ``ramp`` a rise linear in time over the run, ``curve`` one levelling
    off, ``curve (1 - exp(-t / 0.3))`` for t from 0 to 1. ``only`` names the cases
    to count (each keeps its own seed)."""
    rows = []
    for index, (name, spec) in enumerate(CASES.items()):
        if only and name not in only:
            continue
        spec = dict(spec)
        seed = 610000 + 1000 * index
        rand = spec.pop("rand_offsets", 0.0)
        rng = np.random.default_rng(seed)
        if rand:
            offsets = rng.normal(0.0, rand, (realisations, len(spec["lengths"])))
            x, joins = series(rng, realisations, **spec)
            x += np.repeat(offsets, spec["lengths"], axis=1)
        else:
            x, joins = series(rng, realisations, **spec)
        counts = Counter(verdict(row, joins) for row in x)
        rows.append(dict(case=name, spec=CASES[name], seed=seed,
                         realisations=realisations, counts=dict(sorted(counts.items()))))
    return rows


def ramp_means_determined(realisations: int = 1000) -> dict:
    """The 3-segment ramp of 0.4 a segment (``ramp_eq_3``, its seed and
    series) through ``mean_record`` with its joins, as an analysis records
    it: how many means are determined, and why the rest are not."""
    index = list(CASES).index("ramp_eq_3")
    rng = np.random.default_rng(610000 + 1000 * index)
    x, joins = series(rng, realisations, **CASES["ramp_eq_3"])
    counts = Counter("determined" if not record.get("not_a_measurement")
                     else record.get("withheld_code", "unclassified")
                     for record in (mean_record(row, joins=joins) for row in x))
    return dict(case="ramp_eq_3", realisations=realisations, counts=dict(sorted(counts.items())))


def check(only: "list[str] | None" = None) -> dict:
    return dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                known_errors=[] if only else known_errors(),
                unseen=[] if only else unseen(),
                inflated=[] if only else inflated(),
                per_record=[] if only else per_record(),
                mean_record=None if only else ramp_means_determined(),
                contiguous_ar1=trajectories(only=only))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="*", help="count only these trajectory cases")
    parser.add_argument("--sweep", action="store_true",
                        help="count the per-record sweep alone (about ten minutes) and write "
                             "its summary")
    parser.add_argument("--by-index", action="store_true",
                        help="place each mean at its segment's index, not its centre in time")
    args = parser.parse_args()
    if args.by_index:
        by_index()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.sweep:
        args.output.write_text(sweep_written(sweep_summary(sweep())), encoding="utf-8")
        raise SystemExit(0)
    counted = {**check(args.cases), "positions": "index" if args.by_index else "time"}
    args.output.write_text(json.dumps(counted, indent=2) + "\n", encoding="utf-8")
