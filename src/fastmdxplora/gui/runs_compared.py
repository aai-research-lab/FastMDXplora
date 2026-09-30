"""The runs of a study, side by side, with only resolved differences marked.

A study of several runs opened to an Analysis page with nothing on it: each
run's figures were inside the run, one run at a time, and the comparison was
a document written when the last one finished. This puts the runs in one
table, the settings that differ between them beside the means each recorded
(with its error, or marked where the analysis said it is not a measurement),
and marks a difference only where the code judges it resolved: more than
twice the two runs' combined standard error, the rule the written comparison
uses for a trend. Replicas, which differ only by seed, are not compared run
against run; their spread is set against the error each run estimated for
itself.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

__all__ = ["runs_compared", "run_folder"]

#: A difference is marked past this many combined standard errors, as the
#: written comparison marks a trend (`fastmdxplora.batch.compare`).
RESOLVED_AT = 2.0


def run_folder(root: Path | str, run_id: str) -> Path | None:
    """A run's folder, if ``run_id`` names one of this study's runs."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    for run in runs_of_a_study(Path(root)) or []:
        if run["run_id"] == run_id:
            return Path(root) / "runs" / run["run_id"]
    return None


def runs_compared(root: Path | str) -> dict[str, Any]:
    """The table the Analysis page draws for a study of several runs."""
    from fastmdxplora.batch.aggregate import read_member_findings
    from fastmdxplora.gui.exploration import runs_of_a_study

    base = Path(root)
    runs = runs_of_a_study(base)
    if runs is None:
        return {"ok": False, "reason": "not a study of several runs"}

    axes = _axes_that_differ(runs)
    from fastmdxplora.batch.aggregate import SEED_AXES

    replicas = bool(axes) and set(axes) <= SEED_AXES
    completed = [run for run in runs if run["state"] == "completed"]
    found = {run["run_id"]: read_member_findings(Path(run["path"])) for run in completed}

    names: list[str] = []
    for findings in found.values():
        for name, record in findings.items():
            if isinstance(record.get("mean"), dict) and name not in names:
                names.append(name)
    names.sort(key=_order)

    labels = {run["run_id"]: _label(run, axes) for run in runs}
    measures = [_measure(name, runs, found, replicas, labels) for name in names]
    if replicas:
        _with_the_replica_spread(base, measures)
    return {
        "ok": True,
        "runs": [{"run_id": run["run_id"], "label": labels[run["run_id"]], "state": run["state"],
                  "fraction": run.get("fraction"),
                  "values": {axis: (run.get("values") or {}).get(axis) for axis in axes}}
                 for run in runs],
        "axes": [{"axis": axis, "label": axis.split(".")[-1]} for axis in axes],
        "replicas": replicas,
        "completed": len(completed),
        "measures": measures,
        "resolved_at": RESOLVED_AT,
    }


def _axes_that_differ(runs: list[dict[str, Any]]) -> list[str]:
    every: list[str] = []
    for run in runs:
        for axis in run.get("values") or {}:
            if axis not in every:
                every.append(axis)
    return [axis for axis in every
            if len({json.dumps((run.get("values") or {}).get(axis), sort_keys=True, default=str)
                    for run in runs}) > 1]


def _label(run: dict[str, Any], axes: list[str]) -> str:
    """A run by what sets it apart: "temperature_K 310", or its id."""
    values = run.get("values") or {}
    said = [f"{axis.split('.')[-1]} {values.get(axis)}" for axis in axes if axis in values]
    return ", ".join(said) or str(run["run_id"])


def _order(name: str) -> tuple[int, str]:
    from fastmdxplora.gui.series import SERIES

    order = list(SERIES)
    return (order.index(name) if name in order else len(order), name)


def _measure(name: str, runs: list[dict[str, Any]], found: dict[str, dict[str, Any]],
             replicas: bool, labels: dict[str, str]) -> dict[str, Any]:
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.gui.series import SERIES

    cells: list[dict[str, Any]] = []
    unit = ""
    for run in runs:
        record = (found.get(run["run_id"]) or {}).get(name) or {}
        mean = record.get("mean") if isinstance(record.get("mean"), dict) else None
        if mean is None:
            cells.append({"run_id": run["run_id"], "mean": None})
            continue
        unit = unit or unit_of(name, mean)
        withheld = mean.get("not_a_measurement") or None
        error = _finite(mean.get("standard_error"))
        cells.append({
            "run_id": run["run_id"],
            "mean": _finite(mean.get("mean")),
            # An error the analysis did not stand behind is not one to
            # compare with.
            "error": None if withheld else error,
            "effective_samples": _finite(mean.get("effective_samples")),
            "withheld": str(withheld) if withheld else None,
        })

    measured = [c for c in cells if c.get("mean") is not None and c.get("error") is not None]
    reference = measured[0]["run_id"] if measured and not replicas else None
    resolved = 0
    if reference is not None:
        first = measured[0]
        for cell in measured[1:]:
            difference = cell["mean"] - first["mean"]
            error = math.hypot(cell["error"], first["error"])
            marked = error > 0 and abs(difference) > RESOLVED_AT * error
            resolved += marked
            cell["versus"] = {"difference": difference, "error": error, "resolved": marked}
    return {
        "analysis": name,
        "label": SERIES.get(name, (name.replace("_", " "), ""))[0],
        "unit": unit,
        "cells": cells,
        "reference": reference,
        "said": _said(cells, labels.get(reference or "", reference), resolved,
                      len(measured), replicas),
    }


def _said(cells: list[dict[str, Any]], reference: str | None, resolved: int,
          measured: int, replicas: bool) -> str:
    withheld = sum(1 for c in cells if c.get("withheld"))
    missing = sum(1 for c in cells if c.get("mean") is None)
    parts: list[str] = []
    if replicas:
        parts.append("Replicas: compared by their spread, not run against run.")
    elif reference is None:
        parts.append("No run has a mean with an error to compare against.")
    elif measured < 2:
        parts.append(f"Only {reference} has a mean with an error; nothing to compare it with.")
    elif resolved == 0:
        parts.append(f"No run differs from {reference} by more than {RESOLVED_AT:g} times "
                     "their combined error.")
    else:
        parts.append(f"{resolved} of {measured - 1} differ from {reference} by more than "
                     f"{RESOLVED_AT:g} times their combined error.")
    if withheld:
        parts.append(f"{withheld} not a measurement, and not compared.")
    if missing:
        parts.append(f"{missing} not yet recorded.")
    return " ".join(parts)


def _with_the_replica_spread(base: Path, measures: list[dict[str, Any]]) -> None:
    """The spread of the replicas' means against the error each run
    estimated for itself, as the written comparison says it."""
    from fastmdxplora.batch.aggregate import aggregate_members

    try:
        summary = aggregate_members(base)
    except Exception:  # noqa: BLE001 - the table stands without it
        return
    analyses = summary.get("analyses") if isinstance(summary, dict) else None
    if not isinstance(analyses, dict):
        return
    for measure in measures:
        entry = analyses.get(measure["analysis"])
        if isinstance(entry, dict) and entry.get("calibration"):
            measure["said"] = str(entry["calibration"])


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None
