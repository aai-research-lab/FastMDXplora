"""The runs of a study, side by side, with only resolved differences marked.

A study of several runs opened to an Analysis page with nothing on it: each
run's figures were inside the run, one run at a time, and the comparison was
a document written when the last one finished. This puts the runs in one
table, the settings that differ between them beside the means each recorded
(with its error, or marked where the analysis said it is not a measurement),
and marks a difference only where the code judges it resolved: more than
twice the two runs' combined standard error, the rule the written comparison
uses for a trend. Replicas, which differ only by seed or by the model of an
ensemble they start from, are not compared run
against run; their spread is set against the error each run estimated for
itself.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

__all__ = ["runs_compared", "run_folder", "mean_of_replicas"]

#: A difference is marked past this many combined standard errors, as the
#: written comparison marks a trend (`fastmdxplora.batch.compare`).
RESOLVED_AT = 2.0

#: The line a measure of replicas leads with, until their calibration is read.
REPLICAS_SAID = "Replicas: compared by their spread, not run against run."


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
    from fastmdxplora.gui.exploration import (axes_that_differ, runs_of_a_study, setting_said,
                                              value_said)

    base = Path(root)
    runs = runs_of_a_study(base)
    if runs is None:
        return {"ok": False, "reason": "not a study of several runs"}

    axes = axes_that_differ(runs)
    from fastmdxplora.batch.aggregate import SEED_AXES, how_replicas_differ

    replicas = bool(axes) and set(axes) <= SEED_AXES
    # With the runs a study still extends in rounds, as their pieces so far
    # say them: what the study judges, between its rounds.
    completed = [run for run in runs if run["state"] == "completed" or run.get("so_far")]
    # Replicas' records are rewritten from the start they share
    # (`aggregate_members`), as the Analysis page always has: done before
    # each run's mean is read, so a run shows one mean, not the one before
    # and, beside it in the runs together, the one after.
    summary = _the_replicas_together(base) if replicas else None
    found = {run["run_id"]: read_member_findings(Path(run["path"])) for run in completed}

    names: list[str] = []
    for findings in found.values():
        for name, record in findings.items():
            if isinstance(record.get("mean"), dict) and name not in names:
                names.append(name)
    names.sort(key=_order)

    labels = {run["run_id"]: run.get("label") or run["run_id"] for run in runs}
    measures = [_measure(name, runs, found, replicas, labels) for name in names]
    if summary is not None:
        _with_the_replica_spread(summary, measures, of=len(runs))
    return {
        "ok": True,
        "runs": [{"run_id": run["run_id"], "label": labels[run["run_id"]], "state": run["state"],
                  "fraction": run.get("fraction"), "so_far": bool(run.get("so_far")),
                  "values": {axis: (run.get("values") or {}).get(axis) for axis in axes},
                  # Under its column's head ("temperature (K)", "duration"):
                  # a length with its own unit, in ps under 1 ns.
                  "values_said": {axis: _cell_said(axis, (run.get("values") or {}).get(axis),
                                                   value_said) for axis in axes},
                  "path": run["path"], **_how_far(Path(run["path"]), run["state"])}
                 for run in runs],
        # Each setting by its name as a run's is said ("seed", "temperature (K)",
        # "duration").
        "axes": [{"axis": axis, "label": setting_said(axis)} for axis in axes],
        "replicas": replicas,
        # How they differ, as every page says it (`batch.aggregate`).
        "replicas_said": how_replicas_differ(axes, as_participle=True) if replicas else "",
        "completed": sum(1 for run in completed if run["state"] == "completed"),
        # Read as far as their pieces go, their study extending them in rounds.
        "so_far": sum(1 for run in completed if run["state"] != "completed"),
        "measures": measures,
        "resolved_at": RESOLVED_AT,
    }


def _order(name: str) -> tuple[int, str]:
    from fastmdxplora.gui.series import SERIES

    order = list(SERIES)
    return (order.index(name) if name in order else len(order), name)


def _measure(name: str, runs: list[dict[str, Any]], found: dict[str, dict[str, Any]],
             replicas: bool, labels: dict[str, str]) -> dict[str, Any]:
    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.image_note import note_of, said_short

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
            # Its image note beside it (`image_note`): a difference from a
            # run whose solute saw its own image may be that, not the setting.
            "noted": said_short(note_of(mean)) or None,
        })

    measured = [c for c in cells if c.get("mean") is not None and c.get("error") is not None]
    reference = measured[0]["run_id"] if measured and not replicas else None
    resolved = noted_resolved = 0
    if reference is not None:
        first = measured[0]
        for cell in measured[1:]:
            difference = cell["mean"] - first["mean"]
            error = math.hypot(cell["error"], first["error"])
            marked = error > 0 and abs(difference) > RESOLVED_AT * error
            resolved += marked
            noted_resolved += bool(marked and (cell.get("noted") or first.get("noted")))
            cell["versus"] = {"difference": difference, "error": error, "resolved": marked}
    return {
        "analysis": name,
        "label": _named(name),
        "unit": unit,
        "cells": cells,
        "reference": reference,
        "said": _said(cells, labels.get(reference or "", reference), resolved,
                      len(measured), replicas, noted_resolved=noted_resolved),
    }


def _named(name: str) -> str:
    """An analysis by its name on the pages: its series' label, else its
    Analysis page heading ("End-to-end distance", not "end to end")."""
    from fastmdxplora.gui.report_dashboard import ANALYSIS_THEMES
    from fastmdxplora.gui.series import SERIES

    if name in SERIES:
        return SERIES[name][0]
    headings = {key: words for _, keyed in ANALYSIS_THEMES for key, words in keyed}
    return headings.get(name) or name.replace("_", " ").capitalize()


def _said(cells: list[dict[str, Any]], reference: str | None, resolved: int,
          measured: int, replicas: bool, *, noted_resolved: int = 0) -> str:
    withheld = sum(1 for c in cells if c.get("withheld"))
    noted = sum(1 for c in cells if c.get("noted"))
    missing = sum(1 for c in cells if c.get("mean") is None)
    parts: list[str] = []
    if replicas:
        parts.append(REPLICAS_SAID)
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
    if noted_resolved:
        parts.append(f"Where either mean may be biased by the solute's own image ("
                     f"{noted_resolved} of these), the difference may be that bias and not "
                     "the setting.")
    if noted:
        parts.append(f"{noted} may be biased by the solute's own image, as said beside "
                     f"{'its' if noted == 1 else 'each'} value.")
    if withheld:
        parts.append(f"{withheld} not determined, and not compared.")
    if missing:
        parts.append(f"{missing} not yet recorded.")
    return " ".join(parts)


def _how_far(folder: Path, state: str) -> dict[str, Any]:
    """How far a run has got and how fast: its production's nanoseconds so
    far and planned, and its speed in ns/day, as the Overview of one run
    reads them (`simulated_time.simulated_times`,
    `overview_view._production_speed`). None where a run has not got that
    far."""
    from fastmdxplora.gui.overview_view import _production_speed
    from fastmdxplora.gui.simulated_time import simulated_times
    from fastmdxplora.gui.telemetry import status_as_it_stands

    none = {"production_ns": None, "production_planned_ns": None, "speed_ns_per_day": None}
    if state == "waiting" or not (folder / "simulation").is_dir():
        return none
    try:
        times = simulated_times(folder, status_as_it_stands(folder)) or {}
        speed = _production_speed(folder)
    except Exception:  # noqa: BLE001 - a row with what it has, never a wrong number
        return none
    return {"production_ns": _finite(times.get("production_ns")),
            "production_planned_ns": _finite(times.get("production_planned_ns")),
            "speed_ns_per_day": speed}


def _the_replicas_together(base: Path) -> dict[str, Any] | None:
    from fastmdxplora.batch.aggregate import aggregate_members

    try:
        summary = aggregate_members(base)
    except Exception:  # noqa: BLE001 - the table stands without it
        return None
    return summary if isinstance(summary, dict) else None


def _with_the_replica_spread(summary: dict[str, Any], measures: list[dict[str, Any]],
                             of: int | None = None) -> None:
    """The spread of the replicas' means against the error each run
    estimated for itself, as the written comparison says it; and the mean
    of their means with the error the spread gives it (its standard
    deviation over the square root of their number), as
    `batch.aggregate` reads the replicas that finished."""
    analyses = summary.get("analyses")
    if not isinstance(analyses, dict):
        return
    for measure in measures:
        entry = analyses.get(measure["analysis"])
        if isinstance(entry, dict) and entry.get("calibration"):
            # The calibration leads, in place of the line it says more
            # fully; what the cells say (their image notes, the means not
            # determined or not yet recorded) is kept after it.
            rest = measure["said"].removeprefix(REPLICAS_SAID).strip()
            measure["said"] = " ".join(part for part in (str(entry["calibration"]), rest) if part)
        if not isinstance(entry, dict):
            continue
        together = mean_of_replicas(entry, of=of)
        if together is not None:
            measure["ensemble"] = together


def mean_of_replicas(entry: dict[str, Any], *, of: int | None = None) -> dict[str, Any] | None:
    """One quantity of replicas, from its entry in their comparison
    (`batch.aggregate.aggregate_members`): the mean of their means and its
    error, the spread of the means (their standard deviation) over the
    square root of their number, with the spread and how many runs. Where
    the runs' own errors do not back the spread (aggregate's
    ``spread_not_an_error``), no error, and ``withheld`` says why. None
    where fewer than two means or no spread are recorded. The one
    derivation of it, which the runs' table, the written comparison and All
    studies all give. Given how many runs the study has (``of``), more than
    gave this mean (a run not read, not ended, or without it), ``of`` is
    kept beside ``runs``, for the mean to say it is of some of them. Its
    ``noted`` says how many of the replicas' means carry an image note
    (`image_note.replicas_said`), None where none does."""
    members = len(entry.get("members") or [])
    mean, spread = _finite(entry.get("mean_of_means")), _finite(entry.get("spread_of_means"))
    if mean is None or spread is None or members < 2:
        return None
    withheld = entry.get("spread_not_an_error") or None
    if withheld is None and entry.get("qualified_by_their_own_runs"):
        # A comparison written before the reason was: the runs it names.
        withheld = (f"{len(entry['qualified_by_their_own_runs'])} of the {members} replicas did "
                    "not determine their own mean, so their spread is not yet an error.")
    from fastmdxplora.image_note import replicas_said

    together = {"mean": mean, "error": None if withheld else spread / math.sqrt(members),
                "spread": spread, "runs": members, "withheld": withheld,
                # Replicas share the box: a bias of the solute's own image in
                # their means is in the mean of them, said beside it.
                "noted": replicas_said([row.get("solute_image") for row in entry["members"]
                                        if isinstance(row, dict)]) or None}
    if isinstance(of, int) and of > members:
        together["of"] = of
    return together


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _cell_said(axis: str, value: Any, value_said: Any) -> str:
    """A run's value in its setting's column: a length said with its unit
    (`exploration.value_said`), since its column names none; any other
    as recorded, under a head that names its unit."""
    if value is None:
        return ""
    return value_said(axis, value) if str(axis).endswith("_ns") else str(value)
