"""Whether the error one run states for its mean agrees with replicas.

A campaign of replicas, runs of one system differing only by a seed or by an
ensemble's model, determines a mean's error a second way: as the spread of
the replicas' means. Each run's own standard error should predict that
spread. This reads a finished campaign and sets the two side by side for
every analysis that recorded a mean over its frames, three ways:

- **as recorded**: the means and errors the runs wrote when they were
  analysed, by whatever release analysed them;
- **each run's own start**: computed again now from each run's series, by
  this release's estimator;
- **the shared start**: the same, from the start the replicas share
  (:func:`fastmdxplora.statistics.shared_start`), as the stopping rule and
  the campaign's comparison now take them.

What is compared is the observed between-replica variability, the standard
deviation of the replicas' means, against what each run's standard error
predicts for it. Each way gives:

- **the ratio**: the SD of the means over the mean stated error. Honest
  errors put it near one, not at one: below it at small R, where the
  sample SD runs low, and above it where the runs' errors differ, since
  their mean is below their root mean square; well above one, the errors
  were stated too small. The 2.5.7 preprint's ratio is the reciprocal of
  this one: stated error over observed SD;
- **the variance ratio**: the SD of the means squared over the mean of the
  runs' own errors squared, which honest errors put at one in expectation.
  Its 95% interval is the chi-square one on R - 1 degrees of freedom for R
  replicas, taking the stated errors as fixed, so it says only how
  uncertain the observed spread is: at R = 10 it runs from 0.47 to 3.3
  times the variance ratio, so a variance ratio of one half or two cannot
  be told from one. The ratio's
  interval uses the same chi-square factors, square-rooted; it is not the
  square root of the variance ratio's interval where the runs' errors
  differ, since the ratio divides by their mean and the variance ratio by
  the mean of their squares. The interval is exact for normal means with
  one common, exactly known error; with errors that differ from run to run,
  or are themselves estimates, the true interval is at least this wide;
- **the combined mean's standard error**: SD / sqrt(R), the error of the
  mean of the replicas' means, as the replicas determine it.

R counts the runs that determined their mean: a run whose record withheld
its mean (not a measurement) is left out of the spread, R and the combined
mean's error, and the report says how many were. The spread is then of the
runs that stayed. Runs withheld for too few independent samples can hold
the means furthest out, so leaving them out can make the spread look
smaller: with many withheld, a ratio near one is not agreement among all
the replicas.

A variance ratio whose interval lies above one is an error that was stated
too small. The replicas are not themselves anyone's error bar: the spread
is what one run's error should predict, and the combined mean's error is
the spread over sqrt(R).

Nothing in the campaign is written. Run it on a finished campaign of
replicas, where its runs' analyses wrote their series::

    python -m fastmdxplora.validation.replica_calibration <campaign folder> --out calibration.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["calibrate", "main"]

WAYS = ("recorded", "own_start", "shared_start")


def _members(root: Path) -> tuple[list[Path], str]:
    from fastmdxplora.batch.aggregate import _members_are_replicas, member_directory

    manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    runs = [r for r in manifest.get("runs", []) if r.get("status") == "ok"]
    replicas, why = _members_are_replicas(manifest)
    if not replicas:
        from fastmdxplora.refusals import StudyError

        raise StudyError(f"These members are not replicas: {why}.",
                         code="batch.members.not_replicas",
                         axes=sorted(manifest.get("sweep") or {}))
    return [member_directory(root, run) for run in runs], why


def _recorded(member: Path, name: str) -> dict[str, Any] | None:
    try:
        document = json.loads((member / "analysis" / name / "options.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = (document.get("findings") or {}).get("mean") if isinstance(document, dict) else None
    return found if isinstance(found, dict) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


#: Above this factor between the variance ratio's interval's ends, the
#: interval cannot tell an honest error from one stated several times wrong,
#: and the report says so. Ten replicas give 7.0; eight give 9.5; two, 5100.
WIDE_INTERVAL = 10.0


def _chi_square_interval(ratio: float, dof: int) -> tuple[float, float]:
    """The 95% interval on a variance ratio from ``dof + 1`` replicas.

    ``dof * s**2 / sigma**2`` is chi-square on ``dof`` degrees of freedom for
    normal means, so the true ratio lies between ``ratio * dof / chi2(0.975)``
    and ``ratio * dof / chi2(0.025)`` with 95% confidence.
    """
    from scipy.stats import chi2

    return (ratio * dof / float(chi2.ppf(0.975, dof)),
            ratio * dof / float(chi2.ppf(0.025, dof)))


def _compare(records: list[dict[str, Any]]) -> dict[str, Any]:
    # A run whose own record withheld its mean has not determined it, so its
    # mean is left out of the spread, of R and of the combined mean's error,
    # as its error is left out of the prediction.
    determined = [r for r in records if not r.get("not_a_measurement")]
    withheld = len(records) - len(determined)
    pairs = [(_finite(r.get("mean")), _finite(r.get("standard_error")))
             for r in determined]
    means = [m for m, _ in pairs if m is not None]
    errors = [e for m, e in pairs if m is not None and e is not None and e > 0]
    count = len(means)
    spread = float(np.std(means, ddof=1)) if count > 1 else None
    out: dict[str, Any] = {
        "runs": len(records),
        "withheld": withheld,
        "means": count,
        "mean_of_means": float(np.mean(means)) if means else None,
        "observed_spread": spread,
        "predicted_error": float(np.mean(errors)) if errors else None,
        "ratio": None,
        "ratio_interval": None,
        "variance_ratio": None,
        "variance_ratio_interval": None,
        "degrees_of_freedom": count - 1 if count > 1 else None,
        "combined_mean_standard_error": (
            spread / math.sqrt(count) if spread is not None else None),
        "said": [],
    }
    said = out["said"]
    if withheld:
        said.append(f"{withheld} of {len(records)} runs withheld their mean as not "
                    "a measurement; they are left out of the spread, R and the "
                    "combined mean's error.")
    if spread is None:
        said.append(f"{count} of {len(records)} runs determined a mean, so there "
                    "is no spread of means to compare.")
        return out
    if not errors:
        said.append("No run stated an error, so nothing predicts the spread.")
        return out
    if len(errors) < count:
        said.append(f"{count - len(errors)} of the {count} runs with a mean stated "
                    f"no error; the prediction is from the other {len(errors)}.")
    dof = count - 1
    out["ratio"] = spread / out["predicted_error"]
    low, high = _chi_square_interval(1.0, dof)
    # The same chi-square factors as the variance ratio's, on the SD scale.
    out["ratio_interval"] = [out["ratio"] * math.sqrt(low),
                             out["ratio"] * math.sqrt(high)]
    variance = spread ** 2 / float(np.mean(np.square(errors)))
    out["variance_ratio"] = variance
    out["variance_ratio_interval"] = [variance * low, variance * high]
    if high / low > WIDE_INTERVAL:
        said.append(f"With {count} means the variance ratio's interval spans a "
                    f"factor of {high / low:.3g}, so it cannot tell an honest "
                    "error from one stated several times wrong.")
    return out


def calibrate(campaign: str | Path) -> dict[str, Any]:
    """Every analysis with a series in each replica, compared three ways."""
    from fastmdxplora.simulation.stopping import _series_in
    from fastmdxplora.statistics import mean_record, shared_start

    root = Path(campaign)
    members, why = _members(root)
    names = sorted(set.intersection(*[
        {p.parent.name for p in m.glob("analysis/*/options.json")} for m in members]))
    analyses: dict[str, Any] = {}
    for name in names:
        recorded = [_recorded(m, name) for m in members]
        series = [_series_in(m / "analysis" / name / f"{name}.dat") for m in members]
        if any(r is None for r in recorded) or any(
                s is None or not s.size or r.get("n_frames") != s.size
                for r, s in zip(recorded, series)):
            continue
        at = shared_start(series)
        own = [mean_record(s) for s in series]
        shared = [mean_record(s, start_at_least=at) for s in series]
        analyses[name] = {
            "frames": [int(s.size) for s in series],
            "shared_start_frame": at,
            "recorded": _compare(recorded),
            "own_start": _compare(own),
            "shared_start": _compare(shared),
        }
    return {"campaign": str(root), "replicas": len(members), "why": why,
            "analyses": analyses}


def _said(result: dict[str, Any]) -> str:
    def g(value: Any) -> str:
        return "n/a" if value is None else f"{value:.4g}"

    def band(pair: Any) -> str:
        return "n/a" if pair is None else f"{pair[0]:.3g} to {pair[1]:.3g}"

    lines = [f"{result['replicas']} replicas: {result['why']}.", "",
             "Observed between-replica variability (the SD of the replicas' "
             "means) against what each run's stated error predicts for it. "
             "The variance ratio is that SD squared over the mean of the "
             "runs' errors squared; one is honest, and its 95% interval is "
             "chi-square on R - 1 degrees of freedom. The ratio is the SD over "
             "the mean stated error, near one where errors are honest and well "
             "above it where they were stated too small; its interval uses the "
             "square roots of the same chi-square factors, and the preprint's "
             "ratio is the reciprocal of this one. The combined mean's error "
             "is the SD over sqrt(R), R "
             "the runs that determined their mean; runs that withheld theirs "
             "are left out, so the spread is of the runs that stayed.", "",
             "| Analysis | Way | Withheld | Mean of means | SD of means "
             "| Mean stated error | Ratio (95% interval) | Variance ratio (95% interval) "
             "| Combined mean's error |",
             "|---|---|---|---|---|---|---|---|---|"]
    notes = []
    for name, entry in result["analyses"].items():
        for way, key in (("as recorded", "recorded"), ("own start", "own_start"),
                         (f"shared start (frame {entry['shared_start_frame']})",
                          "shared_start")):
            row = entry[key]
            ratio = g(row.get("ratio"))
            if row.get("ratio_interval") is not None:
                ratio += f" ({band(row['ratio_interval'])})"
            variance = g(row.get("variance_ratio"))
            if row.get("variance_ratio_interval") is not None:
                variance += f" ({band(row['variance_ratio_interval'])})"
            lines.append(f"| {name} | {way} | {row['withheld']} of {row['runs']} | "
                         f"{g(row['mean_of_means'])} | {g(row['observed_spread'])} | "
                         f"{g(row['predicted_error'])} | {ratio} | "
                         f"{variance} | "
                         f"{g(row.get('combined_mean_standard_error'))} |")
            notes += [f"- {name}, {way}: {note}" for note in row.get("said", [])]
    if not result["analyses"]:
        lines.append("| (none) | no analysis wrote a series of its frames in every replica "
                     "| | | | | | | |")
    if notes:
        lines += [""] + notes
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fastmdxplora.validation.replica_calibration",
        description="Set each run's stated error against the spread of replicas' means.")
    parser.add_argument("campaign", help="A finished campaign of replicas.")
    parser.add_argument("--out", default=None, help="Also write the comparison as JSON here.")
    args = parser.parse_args(argv)
    try:
        result = calibrate(args.campaign)
    except (OSError, ValueError) as error:
        print(f"Cannot compare: {error}", file=sys.stderr)
        return 2
    print(_said(result))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"\nWritten to {args.out}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
