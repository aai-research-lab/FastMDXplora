"""Reproduce P1's fixed synthetic checks and write their counts as JSON.

Run from an installed checkout:
    python tests/_short_segment_drift_check.py --output /tmp/p1.json
To count another checkout, put its src directory first on PYTHONPATH. The
seeds and cases are fixed here; the thresholds live in statistics.py. The
trajectory cases take about ten minutes on one core.
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
                mean_record=None if only else ramp_means_determined(),
                contiguous_ar1=trajectories(only=only))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="*", help="count only these trajectory cases")
    parser.add_argument("--by-index", action="store_true",
                        help="place each mean at its segment's index, not its centre in time")
    args = parser.parse_args()
    if args.by_index:
        by_index()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    counted = {**check(args.cases), "positions": "index" if args.by_index else "time"}
    args.output.write_text(json.dumps(counted, indent=2) + "\n", encoding="utf-8")
