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

The ratio is the observed spread of the means over the mean predicted
error. One is honest; with ten replicas the spread is itself uncertain by
about a quarter, so a ratio between one half and two cannot be told from
one. A ratio well above two is an error that was stated too small.

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


def _compare(records: list[dict[str, Any]]) -> dict[str, Any]:
    means = [_finite(r.get("mean")) for r in records]
    means = [m for m in means if m is not None]
    errors = [_finite(r.get("standard_error")) for r in records
              if not r.get("not_a_measurement")]
    errors = [e for e in errors if e is not None and e > 0]
    out: dict[str, Any] = {
        "runs": len(records),
        "withheld": sum(1 for r in records if r.get("not_a_measurement")),
        "mean_of_means": float(np.mean(means)) if means else None,
        "observed_spread": float(np.std(means, ddof=1)) if len(means) > 1 else None,
        "predicted_error": float(np.mean(errors)) if errors else None,
    }
    if out["observed_spread"] is not None and out["predicted_error"]:
        out["ratio"] = out["observed_spread"] / out["predicted_error"]
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
    lines = [f"{result['replicas']} replicas: {result['why']}.", "",
             "| Analysis | Way | Withheld | Mean of means | Spread of means "
             "| Mean stated error | Ratio |",
             "|---|---|---|---|---|---|---|"]
    for name, entry in result["analyses"].items():
        for way, key in (("as recorded", "recorded"), ("own start", "own_start"),
                         (f"shared start (frame {entry['shared_start_frame']})",
                          "shared_start")):
            row = entry[key]

            def g(value: Any) -> str:
                return "n/a" if value is None else f"{value:.4g}"

            lines.append(f"| {name} | {way} | {row['withheld']} of {row['runs']} | "
                         f"{g(row['mean_of_means'])} | {g(row['observed_spread'])} | "
                         f"{g(row['predicted_error'])} | {g(row.get('ratio'))} |")
    if not result["analyses"]:
        lines.append("| (none) | no analysis wrote a series of its frames in every replica "
                     "| | | | | |")
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
