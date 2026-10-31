"""How often the free-energy band from WHAM holds the true profile.

`simulation.umbrella.compute_pmf` puts a 95% band on each bin of a
potential of mean force: a moving-block bootstrap over each window's own
samples (`uncertainty.block_bootstrap`), the band the 2.5th and 97.5th
percentiles of the resampled curves. Nothing had checked that the band
holds the truth as often as it says. This does, where the truth is known:

- a double well along a coordinate, F(x) = H ((x - c)^2 / a^2 - 1)^2, with
  minima at c - a and c + a and a barrier of H between them;
- umbrella windows along it, each sampled exactly from its own biased
  distribution, exp(-(F + U) / kT), and correlated in time: an AR(1)
  Gaussian series mapped through its normal CDF onto the window's inverse
  CDF, so the marginal is exact and the correlation is set;
- WHAM over every set, with the bootstrap at the package's resamples and
  fixed seeds, and the true profile, integrated over each reported bin and
  set to zero at its lowest bin as the curve is, read against the band bin
  by bin.

Coverage is counted over every bin with a band, and separately over the
bins between the outermost window centres (``covered`` in the payload),
where every reading of the curve is made. Beside the band, value +/- 1.96
SE says whether a shortfall is in the band's percentiles or in its width;
in the reported gauge that figure also carries the curve's own offset
below. Each set's coverage is a sample too: bins within a set move
together, so the coverage carries a 95% interval from its spread over sets
(a t interval on the per-set coverages), not a binomial one over bins.

Two gauges are read. The reported one sets the curve to zero at its lowest
bin, and each resample to zero at that same bin, as `compute_pmf` does.
Noise puts a curve's lowest bin low, so this lifts the curve above the
truth everywhere away from its minimum (barriers read high); the band's
middle sits on the curve. The second gauge sets the curve, each resample
and the truth to zero at their mean over the bins between the window
centres, where no single bin's noise sets the zero; it is read from the
same resamples, so both gauges come from one bootstrap at the same seeds.
``--block-factor`` reruns the count with blocks of that many correlation
times (the package's is 2), to see how much of a short error is the block
length.

On the default sets (seeds 1000 to 1099, the command below), zeroed as
reported, the band held the truth in 86.4% of bins at 200 resamples (+/-
3.2 points at 95% over sets) and 87.0% at 400, against the 95% it states:
short by about 9 points. With each resample zeroed at its own lowest bin
instead, as before, it held 75.5% (76.2% at 400), and its middle sat 0.47
of its error above the curve. Between the centres the curve sat a median
0.81 of its error above the truth, and the band's middle +0.01 from the
curve. Value +/- 1.96 SE held the truth in 87.2% of the bins between the
centres, against the band's 86.2% (74.7% with the old zeroing): both carry
the curve's offset. Zeroed at the mean over the bins between the centres,
from the same resamples, the curve sat on the truth (+0.01 SE), the band's
middle on the curve (+0.01 SE), and the band held the truth in 89.5% of
the bins between the centres at 200 resamples and 90.0% at 400.

What is left is the error's size, read in the second gauge: the stated
error is about 13% smaller than the curve's spread from set to set (0.869
on these sets at 200 and 400 resamples). That reading moves with the seeds
too, from 0.82 on seeds 1000 to 1029 (the first 30 of these) to 0.91 on
seeds 52000 to 52039, but is below one on each. Blocks of four correlation
times, twice the package's (``--block-factor 4``, 200 resamples), raise it
to 0.916, and the coverage between the centres to 90.7%; on seeds 52000 to
52039 blocks of four gave 0.949 and blocks of eight 0.936, no further. The
same reading in the reported gauge (0.968 here) is not a reading of the
error alone: there the error and the spread both carry the noise of the
lowest bin, and it moves more with the seeds, from 0.86 on seeds 1000 to
1029 to 1.10 on seeds 52000 to 52039. The band is reported as computed,
not corrected.

    python -m fastmdxplora.validation.pmf_coverage --sets 100 --resamples 200 400 --out pmf_coverage.json
    python -m fastmdxplora.validation.pmf_coverage --sets 100 --resamples 200 --block-factor 4
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["Profile", "Design", "build_parser", "coverage", "main"]


@dataclass(frozen=True)
class Profile:
    """The known free energy: a double well, in kJ/mol along x in nm."""

    barrier_kjmol: float = 10.0
    centre: float = 1.5
    half_separation: float = 0.5
    low: float = 0.2
    high: float = 2.8

    def energy(self, x: np.ndarray) -> np.ndarray:
        u = (np.asarray(x, dtype=float) - self.centre) ** 2 / self.half_separation ** 2
        return self.barrier_kjmol * (u - 1.0) ** 2


@dataclass(frozen=True)
class Design:
    """The windows, how much each samples, and how correlated."""

    #: From one wall, 9.2 kJ/mol up, across both wells to the other. Placed
    #: further out, at 0.6 and 2.4 nm, the outermost bins sit 36 kJ/mol up a
    #: wall and hold a handful of counts: they scatter by 0.6 to 1.4 kJ/mol
    #: from seed to seed even with 100,000 independent samples a window,
    #: which would swamp the band's count there.
    windows: int = 12
    first: float = 0.8
    last: float = 2.2
    force_constant: float = 400.0
    samples: int = 2000
    #: AR(1) coefficient of the Gaussian series behind each window's
    #: samples: a statistical inefficiency of (1 + phi) / (1 - phi), 19 at 0.9.
    phi: float = 0.9
    temperature_K: float = 300.0
    bins: int = 60


GRID_POINTS = 40001


def _plan(design: Design):
    from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

    centres = np.linspace(design.first, design.last, design.windows)
    return UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c),
                             force_constant=design.force_constant)
                      for i, c in enumerate(centres)),
        # Neither a torsion nor a distance between centres: a plain line.
        collective_variable="position",
        equilibration_fraction=0.0, minimum_overlap=0.0, minimum_samples=10,
        cone=None)


def _kT(design: Design) -> float:
    from fastmdxplora.simulation.umbrella import KB_KJ

    return KB_KJ * design.temperature_K


def _window_samples(profile: Profile, design: Design, plan,
                    rng: np.random.Generator) -> dict[int, np.ndarray]:
    from scipy.signal import lfilter
    from scipy.special import ndtr

    kT = _kT(design)
    grid = np.linspace(profile.low, profile.high, GRID_POINTS)
    free = profile.energy(grid)
    sampled = {}
    for window in plan.windows:
        reduced = (free + 0.5 * window.force_constant
                   * (grid - window.centre) ** 2) / kT
        density = np.exp(-(reduced - reduced.min()))
        cdf = np.concatenate([[0.0], np.cumsum(
            0.5 * (density[1:] + density[:-1]) * np.diff(grid))])
        cdf /= cdf[-1]
        noise = rng.normal(0.0, math.sqrt(1.0 - design.phi ** 2), design.samples)
        series = lfilter([1.0], [1.0, -design.phi], noise,
                         zi=[design.phi * rng.normal()])[0]
        sampled[window.index] = np.interp(ndtr(series), cdf, grid)
    return sampled


def true_profile(profile: Profile, design: Design, coordinate: list[float]) -> np.ndarray:
    """The truth as the curve reports it: ``-kT ln`` of each bin's
    probability, zero at the lowest bin."""
    kT = _kT(design)
    centres = np.asarray(coordinate, dtype=float)
    half = 0.5 * (centres[1] - centres[0])
    values = []
    for c in centres:
        x = np.linspace(c - half, c + half, 401)
        weight = np.exp(-profile.energy(x) / kT)
        values.append(-kT * math.log(float(np.trapezoid(weight, x))))
    values = np.asarray(values)
    return values - values.min()


def _floats(values: Any) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _listed(values: np.ndarray) -> list[float | None]:
    return [None if not math.isfinite(v) else float(v) for v in values]


def _read(truth: np.ndarray, value: np.ndarray, boot: Any, part: slice) -> dict[str, Any]:
    """One gauge's reading of one set: the band, the error and the offsets."""
    low, high = np.asarray(boot.low)[part], np.asarray(boot.high)[part]
    error = np.asarray(boot.standard_error)[part]
    with np.errstate(divide="ignore", invalid="ignore"):
        band_offset = (0.5 * (low + high) - value) / error
        curve_offset = (value - truth) / error
    return {
        "inside": ((truth >= low) & (truth <= high)).tolist(),
        "inside_1_96_se": (np.abs(value - truth) <= 1.96 * error).tolist(),
        "has_band": (np.isfinite(low) & np.isfinite(high)).tolist(),
        "low": _listed(low),
        "high": _listed(high),
        "error": _listed(error),
        "deviation": _listed(value - truth),
        # Where the band's middle sits from the curve, and the curve from
        # the truth, in the band's own errors.
        "band_offset": _listed(band_offset),
        "curve_offset": _listed(curve_offset),
    }


def _one_set(profile: Profile, design: Design, seed: int,
             resamples: list[int]) -> dict[str, Any]:
    """One set, read in both gauges at each resample count.

    The bootstrap is `compute_pmf`'s own, taken here so its resampled curves
    can be read twice: the same windows in the same order, the same grid,
    the same seed and the same statistic, so the reported gauge's band is
    the one `compute_pmf` reports (a test holds them equal).
    """
    from fastmdxplora.simulation.umbrella import compute_pmf
    from fastmdxplora.uncertainty import block_bootstrap

    plan = _plan(design)
    samples = _window_samples(profile, design, plan, np.random.default_rng(seed))
    base = compute_pmf(samples, plan, temperature_K=design.temperature_K,
                       bins=design.bins, bootstrap_resamples=0)
    ordered = [w for w in plan.windows if w.index in samples]
    everything = np.concatenate([samples[w.index] for w in ordered])
    edges = np.linspace(float(everything.min()), float(everything.max()),
                        design.bins + 1)
    coordinate = np.asarray(base["pmf"]["coordinate"], dtype=float)
    between = (coordinate >= base["covered"][0]) & (coordinate <= base["covered"][1])
    n = coordinate.size
    # The curve's lowest bin, where `compute_pmf` sets each resample to zero.
    anchor = int(np.nanargmin(_floats(base["pmf"]["free_energy_kjmol"])))

    def curve(sampled: dict[int, np.ndarray]) -> np.ndarray:
        inner = compute_pmf(sampled, plan, temperature_K=design.temperature_K,
                            bins=design.bins, minimum_overlap=0.0,
                            bootstrap_resamples=0, _edges=edges, _resampling=True)
        values = _floats(inner["pmf"]["free_energy_kjmol"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            anchored = values - np.nanmean(values[between])
        return np.concatenate([values - values[anchor], anchored])

    truth = true_profile(profile, design, coordinate)
    truth_mean = truth - truth[between].mean()
    out: dict[str, Any] = {"seed": seed, "coordinate": coordinate.tolist(),
                           "between_centres": between.tolist()}
    for count in resamples:
        boot = block_bootstrap({w.index: samples[w.index] for w in ordered}, curve,
                               resamples=count, seed=seed)
        value = np.asarray(boot.value)
        out[str(count)] = {
            "lowest_bin": _read(truth, value[:n], boot, slice(0, n)),
            "mean_between_centres": _read(truth_mean, value[n:], boot, slice(n, 2 * n)),
            "block_length": int(boot.block_length),
            "correlation_resolved": bool(boot.resolved),
        }
    return out


def _gauge(sets: list[dict[str, Any]], key: str, gauge: str) -> dict[str, Any]:
    rows = [s[key][gauge] for s in sets]
    inside = np.array([r["inside"] for r in rows], dtype=bool)
    normal = np.array([r["inside_1_96_se"] for r in rows], dtype=bool)
    banded = np.array([r["has_band"] for r in rows], dtype=bool)
    between = np.array([s["between_centres"] for s in sets], dtype=bool) & banded

    def grid(name: str) -> np.ndarray:
        return np.array([_floats(r[name]) for r in rows], dtype=float)

    def share(mask: np.ndarray, hit: np.ndarray) -> float | None:
        total = int(mask.sum())
        return None if not total else float((hit & mask).sum()) / total

    deviation, error = grid("deviation"), grid("error")
    columns = between.any(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        # Bin by bin: the SD of the deviation over the sets is the curve's
        # real spread there, the mean stated error what the band claims.
        spread = np.nanstd(deviation, axis=0, ddof=1)
        stated = np.nanmean(error, axis=0)
        ratio = (float(np.nanmedian(stated[columns] / spread[columns]))
                 if columns.any() else None)

    def middle(name: str) -> float | None:
        # The median over every (set, bin) between the centres.
        values = grid(name)[between]
        return float(np.nanmedian(values)) if values.size else None

    return {
        "coverage_between_centres": share(between, inside),
        "coverage_1_96_se_between_centres": share(between, normal),
        # Over the bins between the centres: the stated error against the
        # observed spread of the curve over sets (one is honest).
        "stated_over_observed_spread": ratio,
        # Medians over every set's bins between the centres.
        "curve_offset_between_centres": middle("curve_offset"),
        "band_offset_between_centres": middle("band_offset"),
        "_inside": inside, "_banded": banded,
    }


def _counted(sets: list[dict[str, Any]], key: str) -> dict[str, Any]:
    from scipy.stats import t

    reported = _gauge(sets, key, "lowest_bin")
    anchored = _gauge(sets, key, "mean_between_centres")
    inside, banded = reported.pop("_inside"), reported.pop("_banded")
    anchored.pop("_inside"), anchored.pop("_banded")
    per_set = (inside & banded).sum(axis=1) / np.maximum(banded.sum(axis=1), 1)
    half = (float(t.ppf(0.975, len(sets) - 1) * per_set.std(ddof=1)
                  / math.sqrt(len(sets))) if len(sets) > 1 else None)
    total = int(banded.sum())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        per_bin = [None if not banded[:, j].any()
                   else float((inside[:, j] & banded[:, j]).sum() / banded[:, j].sum())
                   for j in range(inside.shape[1])]
    return {
        "bins_with_a_band": total,
        "coverage": float((inside & banded).sum()) / total if total else None,
        # The coverage's 95% interval from its spread over sets: bins within
        # a set move together, so the sets, not the bins, are the samples.
        "coverage_interval_half_width": half,
        "per_set_coverage_sd": float(per_set.std(ddof=1)) if len(sets) > 1 else None,
        **reported,
        "mean_gauge": anchored,
        "per_bin_coverage": per_bin,
        # Each set's grid runs from its own lowest to highest sample, as a
        # study's does, so bin j sits a little differently in each: its
        # coordinate here is the mean over the sets.
        "coordinate": np.mean([s["coordinate"] for s in sets], axis=0).tolist(),
        "block_lengths": sorted({int(s[key]["block_length"]) for s in sets}),
        "correlation_resolved_in_every_set": all(
            s[key]["correlation_resolved"] for s in sets),
    }


def coverage(sets: int = 100, resamples: tuple[int, ...] = (200,), *,
             profile: Profile | None = None, design: Design | None = None,
             first_seed: int = 1000, block_factor: float | None = None) -> dict[str, Any]:
    """Coverage of the band over ``sets`` synthetic umbrella sets, seeds
    ``first_seed`` onward, at each resample count on the same sets.

    ``block_factor`` sets the bootstrap's blocks to that many correlation
    times for this count only, and puts the package's back after.
    """
    from fastmdxplora import uncertainty

    profile = profile or Profile()
    design = design or Design()
    counts = [int(r) for r in resamples]
    started = time.perf_counter()
    previous = logging.root.manager.disable
    package_factor = uncertainty.BLOCKS_PER_CORRELATION_TIME
    logging.disable(logging.WARNING)
    try:
        if block_factor is not None:
            uncertainty.BLOCKS_PER_CORRELATION_TIME = block_factor
        done = [_one_set(profile, design, first_seed + k, counts) for k in range(sets)]
    finally:
        uncertainty.BLOCKS_PER_CORRELATION_TIME = package_factor
        logging.disable(previous)
    return {
        "profile": asdict(profile),
        "design": asdict(design),
        "sets": sets,
        "seeds": [first_seed, first_seed + sets - 1],
        "stated_level": 0.95,
        "block_factor": package_factor if block_factor is None else block_factor,
        "by_resamples": {str(c): _counted(done, str(c)) for c in counts},
        "seconds": round(time.perf_counter() - started, 1),
    }


def _said(result: dict[str, Any]) -> str:
    def pct(value: Any) -> str:
        return "n/a" if value is None else f"{100 * value:.1f}%"

    def g(value: Any, signed: bool = False) -> str:
        return "n/a" if value is None else (f"{value:+.3g}" if signed else f"{value:.3g}")

    lines = [f"{result['sets']} synthetic umbrella sets (seeds {result['seeds'][0]} "
             f"to {result['seeds'][1]}), a {result['profile']['barrier_kjmol']:g} kJ/mol "
             f"double well, {result['design']['windows']} windows of "
             f"{result['design']['samples']} correlated samples each, blocks of "
             f"{result['block_factor']:g} correlation times; the band's stated "
             "level is 95%.", "",
             "Zeroed at the lowest bin, as the curve is reported:", "",
             "| Resamples | Bins with a band | Coverage, all bins (95% over sets) "
             "| Between the centres | Value +/- 1.96 SE, between "
             "| Stated error over observed spread | Curve from the truth (SE) "
             "| Band's middle from the curve (SE) |",
             "|---|---|---|---|---|---|---|---|"]
    for count, row in result["by_resamples"].items():
        half = row["coverage_interval_half_width"]
        lines.append(
            f"| {count} | {row['bins_with_a_band']} | {pct(row['coverage'])} "
            f"(+/- {'n/a' if half is None else f'{100 * half:.1f}'}) | "
            f"{pct(row['coverage_between_centres'])} | "
            f"{pct(row['coverage_1_96_se_between_centres'])} | "
            f"{g(row['stated_over_observed_spread'])} | "
            f"{g(row['curve_offset_between_centres'], True)} | "
            f"{g(row['band_offset_between_centres'], True)} |")
    lines += ["", "Curve, resamples and truth zeroed at their mean over the bins "
              "between the window centres:", "",
              "| Resamples | Between the centres | Value +/- 1.96 SE, between "
              "| Stated error over observed spread | Curve from the truth (SE) "
              "| Band's middle from the curve (SE) |",
              "|---|---|---|---|---|---|"]
    for count, row in result["by_resamples"].items():
        mean = row["mean_gauge"]
        lines.append(
            f"| {count} | {pct(mean['coverage_between_centres'])} | "
            f"{pct(mean['coverage_1_96_se_between_centres'])} | "
            f"{g(mean['stated_over_observed_spread'])} | "
            f"{g(mean['curve_offset_between_centres'], True)} | "
            f"{g(mean['band_offset_between_centres'], True)} |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """The command's flags, which the docs name and a test reads."""
    parser = argparse.ArgumentParser(
        prog="python -m fastmdxplora.validation.pmf_coverage",
        description="Count how often WHAM's free-energy band holds a known profile.")
    parser.add_argument("--sets", type=int, default=100)
    parser.add_argument("--resamples", type=int, nargs="+", default=[200])
    parser.add_argument("--first-seed", type=int, default=1000)
    parser.add_argument("--block-factor", type=float, default=None,
                        help="Blocks of this many correlation times (the package's is 2).")
    parser.add_argument("--out", default=None, help="Also write the counts as JSON here.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sets < 2 or any(r < 2 for r in args.resamples) or (
            args.block_factor is not None and args.block_factor <= 0):
        print("At least two sets and two resamples, and blocks of a positive "
              "number of correlation times, are needed to count coverage.",
              file=sys.stderr)
        return 2
    result = coverage(args.sets, tuple(args.resamples), first_seed=args.first_seed,
                      block_factor=args.block_factor)
    print(_said(result))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"\nWritten to {args.out}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
