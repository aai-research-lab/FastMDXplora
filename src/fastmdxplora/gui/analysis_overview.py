"""What a study's analyses determined, read together, and how each series
converged.

The Analysis page showed each analysis as a figure with a caption under it,
so the question a reader opens the page with -- what did this study find,
and which of it holds -- needed every card read in turn, and a mean the run
was too short to determine looked, at a glance, like any other. This reads
what each analysis recorded beside its figure (`findings.mean` in its
`options.json`, or the reweighted mean a biased run recovered) and says it
as one table: the mean to the place its error allows, the frames it rests
on, and whether it was determined or why not. Nothing is computed again; a
number here is the number the report gives.

The second half is a series' convergence (:func:`convergence_payload`): the
same series the figure plots, read by
:func:`fastmdxplora.statistics.convergence_of`, the package's own estimator,
so the running mean, the block averages and the correlation shown are the
ones the recorded error rests on. Where the recorded mean started from a
point found elsewhere (replicas share their start) the view says so rather
than show a second start as if it were the one used.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastmdxplora.gui.report_dashboard import (
    ANALYSIS_SECTION_BY_FOLDER,
    SECTION_ANCHORS,
    SECTION_ORDER,
    SECTION_THEME,
    unit_of,
)
from fastmdxplora.gui.series import NAME, analysed_axis, series_column

#: The manifest's status words, as the page says them.
_STATUS_SAID = {"ok": "done", "completed": "done", "complete": "done",
                "failed": "failed", "error": "failed", "skipped": "skipped"}


def overview_of(root: str | Path) -> dict[str, Any]:
    """Every analysis of the study, in the page's order, with what it
    determined: ``{"ok", "rows", "biased", "complete"}``, ``complete`` once
    the analysis phase has recorded every analysis it ran.

    A row is ``analysis``, ``title``, ``theme``, ``anchor``, ``status``
    (done, failed, skipped), ``message`` (a failure's reason), ``kind``
    (``mean`` where it recorded a mean over its frames, else ``result``),
    ``mean`` (its main quantity's, or None) and ``quantities``: the main
    mean first, labelled by the analysis, then any other quantity it
    recorded a mean of over its frames (the helix fraction beside the
    secondary structure, the asphericity beside the moments of inertia),
    each ``key``, ``label`` and ``value``, ``error``, ``unit``, ``said``
    (the mean to the place its error allows, with its unit),
    ``determined``, ``why`` (where it is not), ``samples`` (independent
    samples, rounded), ``from_frame``, ``of_frames``, ``from_ns`` and
    ``reweighted``; and ``data``, its data files with their addresses.
    """
    root = Path(root)
    folder = root / "analysis"
    manifest = _json(folder / "analysis_manifest.json")
    results = manifest.get("results") if isinstance(manifest.get("results"), dict) else {}
    names = [str(name) for name in results]
    try:
        names += sorted(p.name for p in folder.iterdir()
                        if p.is_dir() and NAME.match(p.name) and p.name not in results
                        and (p / "options.json").is_file())
    except OSError:
        pass

    from fastmdxplora.report.reweighted import load_reweighted

    biased = load_reweighted(root)
    corrected = {item.get("analysis"): item
                 for item in ((biased or {}).get("quantities") or [])
                 if isinstance(item, dict)}

    # The manifest's results are written once every analysis has run
    # (analysis/orchestrator.py); until then a folder holds an analysis
    # still computing or one finished, told apart by what it recorded.
    complete = bool(results)
    rows = [_row(root, name, results.get(name), corrected, biased is not None, complete)
            for name in names]
    order = {title: index for index, title in enumerate(SECTION_ORDER)}
    rows.sort(key=lambda row: (order.get(row["title"], len(order)), row["analysis"]))
    return {"ok": True, "rows": rows, "biased": biased is not None, "complete": complete}


def _row(root: Path, name: str, result: Any, corrected: dict[Any, Any],
         biased: bool, complete: bool = True) -> dict[str, Any]:
    title = ANALYSIS_SECTION_BY_FOLDER.get(name) or name.replace("_", " ").capitalize()
    entry = result if isinstance(result, dict) else {}
    status = str(entry.get("status") or "done").lower()
    if not entry and not complete and not _has_its_figure(root, name):
        # Its options are written before it computes and its figure after.
        status = "running"
    row: dict[str, Any] = {
        "analysis": name,
        "title": title,
        "theme": SECTION_THEME.get(title, "Other"),
        "anchor": SECTION_ANCHORS.get(title, ""),
        "status": _STATUS_SAID.get(status, status),
        "message": _said(name, entry.get("message")) if status not in _STATUS_SAID
        or _STATUS_SAID[status] != "done" else "",
        "kind": "result",
        "mean": None,
        "quantities": [],
        "data": _data_files(root, name),
    }
    record = _json(root / "analysis" / name / "options.json")
    findings = record.get("findings") if isinstance(record.get("findings"), dict) else {}
    found = findings.get("mean") if isinstance(findings.get("mean"), dict) else None
    main = None
    if biased and name in corrected:
        main = _reweighted(name, corrected[name], found)
    elif found is not None:
        main = _recorded(root, name, found, biased)
    if main is not None:
        row["mean"] = main
        row["quantities"].append({"key": "mean", "label": title, **main})
    for key, other in findings.items():
        if key != "mean" and _is_a_mean_record(other):
            row["quantities"].append({"key": key, "label": _label(key),
                                      **_recorded(root, name, other, biased)})
    if row["quantities"]:
        row["kind"] = "mean"
    return row


#: Words a quantity's key spells in lower case that are said otherwise.
_SAID_AS = {"sasa": "SASA", "rmsd": "RMSD", "rmsf": "RMSF", "rg": "Rg", "pca": "PCA",
            "nm2": "", "nm": ""}


def _label(key: str) -> str:
    """A recorded quantity's key as a reader says it: hydrophobic_sasa is
    "Hydrophobic SASA", relative_shape_anisotropy "Relative shape anisotropy"."""
    words = [_SAID_AS.get(word, word) for word in key.split("_")]
    text = " ".join(word for word in words if word)
    return text[:1].upper() + text[1:]


#: What an analysis writes that a reader would open in another program.
_DATA_SUFFIXES = (".dat", ".csv", ".tsv")


def _data_files(root: Path, name: str) -> list[dict[str, str]]:
    """The analysis's data files, its own first, each with its address."""
    folder = root / "analysis" / name
    try:
        files = sorted((p for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in _DATA_SUFFIXES),
                       key=lambda p: (p.stem != name, p.name))
    except OSError:
        return []
    return [{"name": p.name, "href": f"/artifacts/analysis/{name}/{p.name}"}
            for p in files[:12]]


def _has_its_figure(root: Path, name: str) -> bool:
    folder = root / "analysis" / name
    return any((folder / f"{name}{suffix}").is_file() for suffix in (".png", ".svg"))


def _said(name: str, message: Any) -> str:
    """A failure's message without the analysis's name the manifest puts
    before it, which the row already shows."""
    text = str(message or "").strip()
    prefix = f"{name}: "
    return text[len(prefix):] if text.startswith(prefix) else text


def _is_a_mean_record(value: Any) -> bool:
    """What `statistics.mean_record` writes: a mean over a number of frames,
    or the reason it withheld one."""
    return (isinstance(value, dict) and "n_frames" in value
            and ("mean" in value or "not_a_measurement" in value))


def _recorded(root: Path, name: str, found: dict[str, Any], biased: bool) -> dict[str, Any]:
    from fastmdxplora.statistics import RESOLVED_SAMPLES, with_its_error

    unit = unit_of(name, found)
    value = _finite(found.get("mean"))
    error = _finite(found.get("standard_error"))
    samples = _finite(found.get("effective_samples"))
    n = _count(found.get("n_frames"))
    discard = _count(found.get("discard")) or 0
    why = found.get("not_a_measurement")
    why = str(why) if why else None
    if biased:
        why = ("The run was biased, so this is an average over the ensemble the "
               "bias flattened, not the equilibrium one; no reweighted value was "
               "recovered for it.")
    elif value is None and why is None:
        why = "No mean was recorded."
    elif error is None and why is None:
        if samples is not None and samples < RESOLVED_SAMPLES:
            why = (f"About {int(round(samples))} independent samples, fewer than the "
                   f"{RESOLVED_SAMPLES:g} an error bar needs.")
        else:
            why = "No error bar was recorded."
    determined = why is None and value is not None and error is not None
    suffix = f" {unit}" if unit else ""
    if value is None:
        said = "not determined"
    elif determined:
        said = with_its_error(value, error) + suffix
    else:
        said = f"{value:.4g}{suffix}"
    from_ns = None
    if n:
        _, x, label = analysed_axis(root, n)
        if label == "Time (ns)" and discard < len(x):
            from_ns = x[discard]
    return {
        "value": value, "error": error, "unit": unit, "said": said,
        "determined": determined, "why": why,
        "samples": int(round(samples)) if samples is not None else None,
        "from_frame": discard, "of_frames": n, "from_ns": from_ns,
        "reweighted": False,
    }


def _reweighted(name: str, item: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any]:
    from fastmdxplora.statistics import with_its_error

    unit = unit_of(name, found)
    value = _finite(item.get("reweighted_mean"))
    error = _finite(item.get("reweighted_standard_error"))
    samples = _finite(item.get("independent_samples"))
    suffix = f" {unit}" if unit else ""
    determined = value is not None and error is not None
    if value is None:
        said, why = "not determined", "The reweighting recovered no value."
    elif determined:
        said, why = with_its_error(value, error) + suffix, None
    else:
        said = f"{value:.4g}{suffix}"
        why = ("Reweighted from the biased run; too few independent samples "
               "behind the weights for an error bar.")
    return {
        "value": value, "error": error, "unit": unit, "said": said,
        "determined": determined, "why": why,
        "samples": int(round(samples)) if samples is not None else None,
        "from_frame": None, "of_frames": None, "from_ns": None, "reweighted": True,
    }


def convergence_payload(root: str | Path, analysis: str) -> dict[str, Any]:
    """How one analysis's series converged, or why it cannot be said.

    The series is the column the figure plots (:func:`series_column`), on
    the axis the analysis loaded it with; the result is
    :func:`fastmdxplora.statistics.convergence_of`'s, with ``label``,
    ``unit``, ``x_label`` and ``recorded``: the mean and error the analysis
    recorded, which are the ones the report gives, the reason it withheld
    the error where it did (an analysis can withhold for a reason the series
    cannot show, a chain reaching its own periodic image), and
    ``same_start`` saying whether it began where this view's does. The page
    states the record's mean and error, never the view's own.
    """
    from fastmdxplora.statistics import convergence_of

    if not NAME.match(analysis or ""):
        return {"ok": False, "reason": "not an analysis name"}
    root = Path(root)
    folder = root / "analysis" / analysis
    data = folder / f"{analysis}.dat"
    if not data.is_file():
        return {"ok": False, "reason": f"{analysis} wrote no data file"}
    record = _json(folder / "options.json")
    findings = record.get("findings") if isinstance(record.get("findings"), dict) else {}
    found = findings.get("mean") if isinstance(findings.get("mean"), dict) else None
    if found is None or _finite(found.get("mean")) is None:
        # A record holding only a reason (the moments of a molecule broken
        # across the box) has no series of its own: the last column of its
        # file was I3, which nothing recorded a mean of.
        return {"ok": False, "reason": f"{analysis} recorded no mean over its frames, "
                                       "so it has no series to converge"}
    if _columns(data) > 2:
        return {"ok": False, "reason": f"{analysis}'s data file holds several quantities "
                                       "a frame, and its mean is of none of them alone"}
    values = series_column(data)
    if not values:
        return {"ok": False, "reason": f"{analysis}'s data file holds no numbers"}
    frames, x, x_label = analysed_axis(root, len(values))
    timed = x_label == "Time (ns)"
    result = convergence_of(values, x if timed else None)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason") or "too short a series"}
    recorded_start = _count(found.get("discard"))
    start = (result.get("equilibration") or {}).get("discard_frames")
    result.update({
        "analysis": analysis,
        "label": ANALYSIS_SECTION_BY_FOLDER.get(analysis, analysis),
        "unit": unit_of(analysis, found),
        "x_label": x_label,
        "time_unit": "ns" if timed else None,
        "recorded": {
            "mean": _finite(found.get("mean")),
            "standard_error": _finite(found.get("standard_error")),
            "not_a_measurement": str(found["not_a_measurement"])
            if found.get("not_a_measurement") else None,
            "error_withheld_because": found.get("error_withheld_because") or None,
            "discard_frames": recorded_start,
            "same_start": recorded_start is None or recorded_start == start,
            "start_shared_with_replicas": bool(found.get("start_shared_with_replicas")),
        },
    })
    return result


def _columns(path: Path) -> int:
    """The most numbers on one data line of ``path``."""
    from fastmdxplora.gui.series import _rows

    return max((len(numbers) for _, numbers in _rows(path)), default=0)


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
